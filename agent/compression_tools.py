"""
文件压缩/解压工具集 -- 云枢压缩和解压文件的能力

我是云枢的"双手"之一——提供安全的文件压缩和解压操作。
使用 Python 标准库（zipfile, tarfile），无需额外依赖。

安全措施：
- 路径遍历防护（safe_resolve_path）
- Zip Slip 攻击防护（解压时检测 ../ 和绝对路径）
- 保护目录禁止操作
- 大文件分块流式处理（避免内存爆炸）
"""

import os
import shutil
import zipfile
import tarfile
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 分块大小：64KB
_CHUNK_SIZE = 64 * 1024


def compress(
    source_path: str,
    output_path: str = "",
    format: str = "zip",
    progress_callback: callable = None,
) -> dict:
    """压缩文件或目录为 zip 或 tar.gz 格式

    使用分块流式处理，不将整个文件加载到内存。
    支持进度回调，为后续异步框架预留接口。

    Args:
        source_path: 源文件或目录路径
        output_path: 输出文件路径（不指定则自动生成：同目录 + .zip/.tar.gz）
        format: 压缩格式，支持 "zip" 或 "tar.gz"
        progress_callback: 进度回调函数，签名为 (current: int, total: int, filename: str)

    Returns:
        dict: {ok, output_path, compressed_size, file_count, format, error}
    """
    from agent.system_tools import safe_resolve_path

    # ── 1. 参数校验 ──
    fmt = format.lower().strip()
    if fmt not in ("zip", "tar.gz", "tgz"):
        return {"ok": False, "error": f"不支持的压缩格式: {fmt}，仅支持 zip 和 tar.gz"}

    # 兼容 tgz 缩写
    if fmt == "tgz":
        fmt = "tar.gz"

    # ── 2. 源路径安全检查 ──
    try:
        safe_src = safe_resolve_path(source_path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    if not os.path.exists(safe_src):
        return {"ok": False, "error": f"源路径不存在: {source_path}"}

    is_dir = os.path.isdir(safe_src)
    src_name = os.path.basename(safe_src.rstrip(os.sep).rstrip("/"))

    # ── 3. 输出路径处理 ──
    if not output_path:
        # 自动生成：同级目录下的 源名.zip 或 源名.tar.gz
        ext = ".tar.gz" if fmt == "tar.gz" else ".zip"
        parent_dir = os.path.dirname(safe_src) if not is_dir else os.path.dirname(safe_src)
        output_path = os.path.join(parent_dir or ".", src_name + ext)
    else:
        try:
            output_path = safe_resolve_path(output_path)
        except ValueError as e:
            return {"ok": False, "error": f"输出路径不安全: {e}"}

    # 确保输出目录存在
    out_dir = os.path.dirname(output_path) or "."
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": f"无法创建输出目录: {e}"}

    # ── 4. 收集文件列表（用于进度追踪） ──
    file_list = []
    if is_dir:
        for root, dirs, files in os.walk(safe_src):
            for fname in files:
                full_path = os.path.join(root, fname)
                arcname = os.path.relpath(full_path, os.path.dirname(safe_src))
                file_list.append((full_path, arcname))
    else:
        arcname = os.path.basename(safe_src)
        file_list.append((safe_src, arcname))

    total_files = len(file_list)
    if total_files == 0:
        return {"ok": False, "error": "没有可压缩的文件（源目录为空）"}

    logger.info("[compress] 开始压缩: src=%s, fmt=%s, files=%d, output=%s",
                safe_src, fmt, total_files, output_path)

    # ── 5. 执行压缩 ──
    try:
        if fmt == "zip":
            _compress_zip(safe_src, file_list, output_path, progress_callback, total_files, is_dir)
        else:
            _compress_tar_gz(safe_src, file_list, output_path, progress_callback, total_files, is_dir)
    except Exception as e:
        # 压缩失败时清理可能生成的不完整文件
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass
        logger.error("[compress] 压缩失败: %s", e)
        return {"ok": False, "error": f"压缩失败: {e}"}

    compressed_size = os.path.getsize(output_path)
    logger.info("[compress] 压缩完成: output=%s, size=%d, files=%d",
                output_path, compressed_size, total_files)

    return {
        "ok": True,
        "output_path": output_path,
        "compressed_size": compressed_size,
        "file_count": total_files,
        "format": fmt,
    }


def _compress_zip(
    base_dir: str,
    file_list: list,
    output_path: str,
    progress_callback: callable,
    total_files: int,
    is_dir: bool,
):
    """使用 zipfile 模块压缩（分块流式处理）"""
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx, (full_path, arcname) in enumerate(file_list):
            current = idx + 1
            if progress_callback:
                try:
                    progress_callback(current, total_files, arcname)
                except Exception:
                    pass  # 回调异常不影响主流程

            # 分块添加大文件（>100MB 使用 chunked write）
            file_size = os.path.getsize(full_path)
            if file_size > 100 * 1024 * 1024:
                _add_large_file_to_zip(zf, full_path, arcname)
            else:
                zf.write(full_path, arcname)


def _add_large_file_to_zip(zf: zipfile.ZipFile, file_path: str, arcname: str):
    """分块添加大文件到 zip，避免一次性加载到内存"""
    # 获取文件信息用于设置压缩头部
    stat = os.stat(file_path)
    zinfo = zipfile.ZipInfo.from_file(file_path, arcname)

    with zf.open(zinfo, "w") as dest, open(file_path, "rb") as src:
        while True:
            chunk = src.read(_CHUNK_SIZE)
            if not chunk:
                break
            dest.write(chunk)


def _compress_tar_gz(
    base_dir: str,
    file_list: list,
    output_path: str,
    progress_callback: callable,
    total_files: int,
    is_dir: bool,
):
    """使用 tarfile 模块压缩为 tar.gz"""
    with tarfile.open(output_path, "w:gz") as tf:
        for idx, (full_path, arcname) in enumerate(file_list):
            current = idx + 1
            if progress_callback:
                try:
                    progress_callback(current, total_files, arcname)
                except Exception:
                    pass

            tf.add(full_path, arcname)


def decompress(
    file_path: str,
    output_dir: str = "",
    progress_callback: callable = None,
) -> dict:
    """解压压缩文件（zip 或 tar.gz）

    安全措施：
    - Zip Slip 攻击防护（检测成员路径中的 ../ 和绝对路径）
    - 目标路径通过 safe_resolve_path 验证
    - 禁止解压到系统保护目录

    Args:
        file_path: 压缩文件路径
        output_dir: 输出目录（不指定则解压到压缩文件所在目录）
        progress_callback: 进度回调函数，签名为 (current: int, total: int, filename: str)

    Returns:
        dict: {ok, output_dir, file_count, extracted_size, format, error}
    """
    from agent.system_tools import safe_resolve_path

    # ── 1. 源文件安全检查 ──
    try:
        safe_file = safe_resolve_path(file_path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    if not os.path.exists(safe_file):
        return {"ok": False, "error": f"文件不存在: {file_path}"}
    if not os.path.isfile(safe_file):
        return {"ok": False, "error": f"路径不是文件: {file_path}"}

    # ── 2. 检测格式 ──
    file_lower = safe_file.lower()
    if file_lower.endswith(".zip"):
        archive_format = "zip"
    elif file_lower.endswith((".tar.gz", ".tgz")):
        archive_format = "tar.gz"
    elif file_lower.endswith(".tar"):
        archive_format = "tar"
    else:
        # 尝试通过 magic bytes 检测
        try:
            with open(safe_file, "rb") as f:
                magic = f.read(4)
            if magic[:4] == b"PK\x03\x04":
                archive_format = "zip"
            elif magic[:2] == b"\x1f\x8b":
                archive_format = "tar.gz"
            else:
                return {"ok": False, "error": f"无法识别压缩格式: {file_path}，仅支持 zip/tar.gz"}
        except Exception:
            return {"ok": False, "error": f"无法读取文件以检测格式: {file_path}"}

    # ── 3. 输出目录处理 ──
    if not output_dir:
        # 默认解压到压缩文件所在目录下的同名目录
        base_name = os.path.splitext(os.path.basename(safe_file))[0]
        # 处理 .tar.gz 双扩展名
        if base_name.endswith(".tar"):
            base_name = base_name[:-4]
        output_dir = os.path.join(os.path.dirname(safe_file), base_name)
    else:
        try:
            output_dir = safe_resolve_path(output_dir)
        except ValueError as e:
            return {"ok": False, "error": f"输出目录不安全: {e}"}

    # 确保输出目录存在
    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": f"无法创建输出目录: {e}"}

    logger.info("[decompress] 开始解压: file=%s, fmt=%s, output=%s",
                safe_file, archive_format, output_dir)

    # ── 4. 执行解压 ──
    try:
        if archive_format == "zip":
            file_count, extracted_size = _safe_extract_zip(safe_file, output_dir, progress_callback)
        else:
            file_count, extracted_size = _safe_extract_tar(safe_file, output_dir, progress_callback)
    except zipfile.BadZipFile as e:
        return {"ok": False, "error": f"ZIP 文件损坏: {e}"}
    except tarfile.TarError as e:
        return {"ok": False, "error": f"TAR 文件损坏: {e}"}
    except Exception as e:
        logger.error("[decompress] 解压失败: %s", e)
        return {"ok": False, "error": f"解压失败: {e}"}

    logger.info("[decompress] 解压完成: output=%s, files=%d, size=%d",
                output_dir, file_count, extracted_size)

    return {
        "ok": True,
        "output_dir": output_dir,
        "file_count": file_count,
        "extracted_size": extracted_size,
        "format": archive_format,
    }


def _safe_extract_zip(file_path: str, output_dir: str, progress_callback: callable = None) -> tuple:
    """安全解压 zip，防止 Zip Slip 攻击

    Zip Slip 防护：检查每个成员的提取路径，
    确保不包含 ../ 且不是绝对路径。

    Returns:
        tuple: (file_count, total_extracted_size)
    """
    with zipfile.ZipFile(file_path, "r") as zf:
        members = zf.infolist()

        # 过滤目录条目，只处理文件
        file_members = [m for m in members if not m.is_dir()]
        total_files = len(file_members)

        if total_files == 0:
            return 0, 0

        extracted_size = 0

        for idx, member in enumerate(file_members):
            current = idx + 1

            # ── Zip Slip 防护 ──
            member_path = os.path.normpath(member.filename)
            if member_path.startswith("..") or os.path.isabs(member_path):
                logger.warning("[decompress] Zip Slip 攻击检测: 跳过 %s", member.filename)
                continue

            target_path = os.path.join(output_dir, member_path)
            # 二次确认：确保解析后的路径仍在 output_dir 内
            target_real = os.path.realpath(target_path)
            output_real = os.path.realpath(output_dir)
            if not target_real.startswith(output_real + os.sep) and target_real != output_real:
                logger.warning("[decompress] 路径逃逸检测: 跳过 %s -> %s", member.filename, target_real)
                continue

            if progress_callback:
                try:
                    progress_callback(current, total_files, member.filename)
                except Exception:
                    pass

            # 确保目标目录存在
            os.makedirs(os.path.dirname(target_path), exist_ok=True)

            # 分块解压大文件（>100MB）
            if member.file_size > 100 * 1024 * 1024:
                _extract_large_zip_member(zf, member, target_path)
            else:
                with zf.open(member) as src, open(target_path, "wb") as dst:
                    shutil.copyfileobj(src, dst, _CHUNK_SIZE)

            extracted_size += os.path.getsize(target_path)

        return total_files, extracted_size


def _extract_large_zip_member(zf: zipfile.ZipFile, member: zipfile.ZipInfo, target_path: str):
    """分块解压大型 zip 成员文件"""
    with zf.open(member) as src, open(target_path, "wb") as dst:
        while True:
            chunk = src.read(_CHUNK_SIZE)
            if not chunk:
                break
            dst.write(chunk)


def _safe_extract_tar(file_path: str, output_dir: str, progress_callback: callable = None) -> tuple:
    """安全解压 tar/tar.gz，防止路径遍历攻击

    检查每个成员的提取路径，防止 Zip Slip 类攻击。

    Returns:
        tuple: (file_count, total_extracted_size)
    """
    with tarfile.open(file_path, "r:*") as tf:
        members = tf.getmembers()

        # 过滤目录条目
        file_members = [m for m in members if m.isfile()]
        total_files = len(file_members)

        if total_files == 0:
            return 0, 0

        extracted_size = 0

        for idx, member in enumerate(file_members):
            current = idx + 1

            # ── 路径遍历防护 ──
            member_path = os.path.normpath(member.name)
            if member_path.startswith("..") or os.path.isabs(member_path):
                logger.warning("[decompress] 路径遍历攻击检测: 跳过 %s", member.name)
                continue

            target_path = os.path.join(output_dir, member_path)
            # 二次确认
            target_real = os.path.realpath(target_path)
            output_real = os.path.realpath(output_dir)
            if not target_real.startswith(output_real + os.sep) and target_real != output_real:
                logger.warning("[decompress] 路径逃逸检测: 跳过 %s -> %s", member.name, target_real)
                continue

            if progress_callback:
                try:
                    progress_callback(current, total_files, member.name)
                except Exception:
                    pass

            # 确保目标目录存在
            os.makedirs(os.path.dirname(target_path), exist_ok=True)

            # 分块解压大文件（>100MB）
            if member.size > 100 * 1024 * 1024:
                with tf.extractfile(member) as src, open(target_path, "wb") as dst:
                    while True:
                        chunk = src.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        dst.write(chunk)
            else:
                tf.extract(member, output_dir, set_attrs=False)

            extracted_size += member.size

        return total_files, extracted_size
