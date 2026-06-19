"""
系统工具集 -- 沙盒、定时任务、浏览器、进程管理、剪贴板、工作区

我是云枢的"工具箱"——提供受控的系统级操作能力。
"""
import os
import subprocess
import tempfile
import json
import logging
import shutil
import time
import fnmatch
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  安全路径检查 — 防止云枢读写系统关键区域
# ════════════════════════════════════════════════════════════

# 禁止读取/写入的系统关键目录（Windows）
PROTECTED_SYSTEM_DIRS_WIN = [
    r"C:\Windows\System32",
    r"C:\Windows\SysWOW64",
    r"C:\Windows\SystemResources",
    r"C:\Windows\WinSxS",
    r"C:\Windows\Microsoft.NET",
    r"C:\Windows\Installer",
    r"C:\Windows\assembly",
    r"C:\Windows\Globalization",
    r"C:\Windows\security",
    r"C:\Windows\Registration",
    r"C:\Windows\servicing",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
    r"C:\Recovery",
    r"C:\$Recycle.Bin",
    r"C:\System Volume Information",
    r"C:\Boot",
    r"C:\Windows\Boot",
]

# 明确允许的 Windows 子目录（不拦截）
ALLOWED_WIN_SUBDIRS = [
    r"C:\Windows\Temp",
    r"C:\Windows\TEMP",
    r"C:\Windows\Help",
    r"C:\Windows\Fonts",
    r"C:\Windows\Media",
]

# 禁止读取/写入的系统关键目录（Unix/Linux/Mac）
PROTECTED_SYSTEM_DIRS_UNIX = [
    "/etc",
    "/usr/lib",
    "/usr/share",
    "/boot",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/sys",
    "/proc",
    "/dev",
    "/var/log",
    "/var/cache",
    "/System",
    "/Library",
    "/private",
]

# 禁止通过 write_file 创建/修改的文件扩展名（可执行文件等）
BLOCKED_WRITE_EXTENSIONS = {
    ".exe", ".dll", ".sys", ".bin", ".bat", ".cmd",
    ".ps1", ".psm1", ".psd1", ".vbs", ".vbe", ".js", ".jse",
    ".scr", ".pif", ".com", ".msi", ".msp", ".mst",
    ".reg", ".pyc", ".pyo",
    ".so", ".o", ".ko",
    ".app", ".dmg", ".pkg",
}

# 文件读取大小限制（默认 10MB）
DEFAULT_MAX_READ_SIZE = 10 * 1024 * 1024
# 文件写入大小限制（默认 50MB）
DEFAULT_MAX_WRITE_SIZE = 50 * 1024 * 1024


def is_protected_path(path: str) -> bool:
    """检查路径是否属于系统保护目录，禁止云枢直接访问"""
    try:
        abs_path = os.path.abspath(os.path.normpath(path))
    except Exception:
        return True

    # Windows 系统保护目录检测
    if os.name == "nt":
        # 先检查是否在允许列表中
        for allowed in ALLOWED_WIN_SUBDIRS:
            if abs_path.lower().startswith(allowed.lower() + os.sep) or abs_path.lower() == allowed.lower():
                return False
        # 再检查是否在保护列表中
        for protected in PROTECTED_SYSTEM_DIRS_WIN:
            if abs_path.lower().startswith(protected.lower() + os.sep) or abs_path.lower() == protected.lower():
                return True
    # Unix 系统保护目录检测
    for protected in PROTECTED_SYSTEM_DIRS_UNIX:
        if abs_path.startswith(protected + os.sep) or abs_path == protected:
            return True

    return False


def safe_resolve_path(path: str) -> str:
    """安全解析路径：规范化 + 保护目录检查

    将任意路径安全地解析为绝对路径，防止访问系统保护区域。

    Returns:
        规范化后的绝对路径

    Raises:
        ValueError: 路径非法或位于受保护的系统目录
    """
    logger.info(f"[safe_resolve_path] 开始解析路径: path={path}")
    try:
        abs_path = os.path.abspath(os.path.normpath(path))
        logger.info(f"[safe_resolve_path] 路径规范化成功: abs_path={abs_path}")
    except (ValueError, OSError) as e:
        logger.warning(f"[safe_resolve_path] 路径解析异常: path={path}, error={type(e).__name__}: {e}")
        raise ValueError(f"路径解析失败: {e}")

    if is_protected_path(abs_path):
        logger.warning(f"[safe_resolve_path] 路径被保护目录拦截: abs_path={abs_path}")
        raise ValueError(f"路径位于系统保护目录，拒绝访问: {abs_path}")
    
    logger.info(f"[safe_resolve_path] 路径解析完成，返回: {abs_path}")
    return abs_path


def is_binary_content(data: bytes) -> bool:
    """检测数据是否为二进制内容（判断前 8KB 中是否有 NULL 字节或过多非文本字符）"""
    chunk = data[:8192]
    if not chunk:
        return False
    # 如果包含 NULL 字节，必然是二进制
    if b'\x00' in chunk:
        return True
    # 检查非文本字符比例
    text_char_count = 0
    for byte in chunk:
        if 0x09 <= byte <= 0x0D or 0x20 <= byte <= 0x7E:
            text_char_count += 1
    return (text_char_count / len(chunk)) < 0.85


def is_executable_extension(path: str) -> bool:
    """检查文件扩展名是否为可执行/脚本类型"""
    ext = os.path.splitext(path)[1].lower()
    return ext in BLOCKED_WRITE_EXTENSIONS


# ════════════════════════════════════════════════════════════
#  通用文件操作 — 云枢读取/写入本地文件的能力
# ════════════════════════════════════════════════════════════

def read_file(path: str, encoding: str = "utf-8", max_size_mb: int = 10,
              range: str = "") -> dict:
    """读取本地文件内容（安全受限）

    安全措施：
    - 路径遍历防护
    - 系统保护目录禁止访问
    - 文件大小上限控制
    - 二进制内容检测

    Args:
        path: 文件路径（绝对或相对当前工作目录）
        encoding: 文件编码，默认 utf-8，设置 None 以二进制模式读取
        max_size_mb: 最大读取大小（MB），默认 10MB
        range: 可选，行范围，如 "1-50" 读取第1到50行

    Returns:
        dict: {ok, content, path, size, encoding, binary, error}
    """
    logger.info(f"[read_file] 开始读取文件: path={path}, encoding={encoding}, max_size_mb={max_size_mb}")
    try:
        safe_path = safe_resolve_path(path)
        logger.info(f"[read_file] 路径安全解析成功: safe_path={safe_path}")
    except ValueError as e:
        logger.warning(f"[read_file] 路径解析失败: path={path}, error={e}")
        return {"ok": False, "error": str(e)}

    if not os.path.exists(safe_path):
        logger.warning(f"[read_file] 文件不存在: safe_path={safe_path}")
        return {"ok": False, "error": f"文件不存在: {path}"}
    if not os.path.isfile(safe_path):
        if os.path.isdir(safe_path):
            logger.warning(f"[read_file] 路径是目录而非文件: safe_path={safe_path}")
            return {"ok": False, "error": f"路径是目录而非文件: {path}，请使用 list_directory 工具列出目录内容"}
        logger.warning(f"[read_file] 路径不是文件: safe_path={safe_path}")
        return {"ok": False, "error": f"路径不是文件: {path}"}

    file_size = os.path.getsize(safe_path)
    logger.info(f"[read_file] 文件大小: {file_size} bytes")
    max_size = max_size_mb * 1024 * 1024
    if file_size > max_size:
        logger.warning(f"[read_file] 文件过大: file_size={file_size}, max_size={max_size}")
        return {
            "ok": False, "error": f"文件过大 ({file_size / 1024 / 1024:.1f}MB)，超过限制 {max_size_mb}MB",
            "path": path, "size": file_size,
        }

    try:
        with open(safe_path, "rb") as f:
            raw_data = f.read()
        logger.info(f"[read_file] 文件读取成功: raw_data_size={len(raw_data)}")
    except PermissionError as e:
        logger.warning(f"[read_file] 权限错误: safe_path={safe_path}, error={e}")
        return {"ok": False, "error": f"没有权限读取文件: {path}"}
    except OSError as e:
        logger.warning(f"[read_file] OS错误: safe_path={safe_path}, error={e}")
        return {"ok": False, "error": f"读取文件失败: {e}"}

    is_binary = is_binary_content(raw_data)
    logger.info(f"[read_file] 二进制检测结果: is_binary={is_binary}")

    if encoding is None or is_binary:
        # 二进制模式，返回 base64
        import base64
        logger.info(f"[read_file] 使用二进制模式返回 base64")
        return {
            "ok": True,
            "path": path,
            "abs_path": safe_path,
            "size": file_size,
            "encoding": "base64",
            "binary": True,
            "content": base64.b64encode(raw_data).decode("ascii"),
            "mime_hint": _guess_mime_type(safe_path),
        }

    # 文本模式，尝试解码
    logger.info(f"[read_file] 使用文本模式解码: encoding={encoding}")
    try:
        content = raw_data.decode(encoding)
        logger.info(f"[read_file] 解码成功: encoding={encoding}, content_length={len(content)}")
    except UnicodeDecodeError as e:
        logger.warning(f"[read_file] 解码失败，尝试降级: encoding={encoding}, error={e}")
        # 编码不对，尝试自动检测
        try:
            content = raw_data.decode("utf-8", errors="replace")
            encoding = "utf-8 (with replacements)"
            logger.info(f"[read_file] 降级解码成功: encoding={encoding}")
        except Exception as e2:
            logger.warning(f"[read_file] utf-8降级失败，使用latin-1: error={e2}")
            content = raw_data.decode("latin-1")
            encoding = "latin-1"

    logger.info(f"[read_file] 文件读取完成: ok=True, encoding={encoding}, binary=False")

    # 如果指定了行范围，截取对应行
    if range:
        try:
            parts = range.split("-")
            if len(parts) == 2:
                start = max(0, int(parts[0]) - 1)  # 转为 0-based
                end = int(parts[1])
                lines = content.splitlines(keepends=True)
                if start < len(lines):
                    selected = lines[start:end]
                    content = "".join(selected)
        except (ValueError, IndexError):
            pass

    return {
        "ok": True,
        "path": path,
        "abs_path": safe_path,
        "size": file_size,
        "encoding": encoding,
        "binary": False,
        "content": content,
        "lines": content.count("\n") + 1,
    }


def write_file(path: str, content: str, encoding: str = "utf-8") -> dict:
    """写入本地文件（安全受限）

    安全措施：
    - 路径遍历防护
    - 系统保护目录禁止写入
    - 禁止写入可执行文件类型
    - 文件大小上限控制
    - 覆盖前自动备份

    Args:
        path: 文件路径（绝对或相对当前工作目录）
        content: 文件内容（字符串）
        encoding: 文件编码，默认 utf-8

    Returns:
        dict: {ok, path, size, backup, error}
    """
    logger.info(f"[write_file] 开始写入文件: path={path}, encoding={encoding}, content_length={len(content) if content else 0}")
    try:
        safe_path = safe_resolve_path(path)
        logger.info(f"[write_file] 路径安全解析成功: safe_path={safe_path}")
    except ValueError as e:
        logger.warning(f"[write_file] 路径解析失败: path={path}, error={e}")
        return {"ok": False, "error": str(e)}

    # 禁止写入可执行文件类型
    if is_executable_extension(safe_path):
        ext = os.path.splitext(safe_path)[1]
        logger.warning(f"[write_file] 禁止写入可执行文件类型: ext={ext}")
        return {"ok": False, "error": f"禁止写入可执行/脚本文件类型 ({ext})"}

    # 检查内容大小
    content_bytes = content.encode(encoding) if isinstance(content, str) else content
    logger.info(f"[write_file] 内容大小: {len(content_bytes)} bytes")
    if len(content_bytes) > DEFAULT_MAX_WRITE_SIZE:
        logger.warning(f"[write_file] 内容过大: size={len(content_bytes)}, max_size={DEFAULT_MAX_WRITE_SIZE}")
        return {
            "ok": False,
            "error": f"内容过大 ({len(content_bytes) / 1024 / 1024:.1f}MB)，超过限制 {DEFAULT_MAX_WRITE_SIZE // (1024 * 1024)}MB",
        }

    # 覆盖前备份
    backup_path = None
    if os.path.exists(safe_path):
        logger.info(f"[write_file] 文件已存在，准备备份: safe_path={safe_path}")
        try:
            backup_dir = os.path.join(os.path.dirname(__file__), "..", ".file_backups")
            os.makedirs(backup_dir, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            fname = os.path.basename(safe_path)
            backup_path = os.path.join(backup_dir, f"{fname}.{timestamp}.bak")
            shutil.copy2(safe_path, backup_path)
            logger.info(f"[write_file] 备份成功: backup_path={backup_path}")
        except Exception as e:
            logger.warning(f"[write_file] 备份失败（继续写入）: error={e}")

    # 创建目录
    dir_path = os.path.dirname(safe_path)
    logger.info(f"[write_file] 检查目录: dir_path={dir_path}")
    try:
        os.makedirs(dir_path, exist_ok=True)
        logger.info(f"[write_file] 目录创建/确认成功")
    except OSError as e:
        logger.warning(f"[write_file] 创建目录失败: dir_path={dir_path}, error={e}")
        return {"ok": False, "error": f"无法创建目录: {e}"}

    # 写入文件
    logger.info(f"[write_file] 开始写入文件内容")
    try:
        with open(safe_path, "w", encoding=encoding) as f:
            f.write(content)
        logger.info(f"[write_file] 文件写入成功: safe_path={safe_path}")
    except PermissionError as e:
        logger.warning(f"[write_file] 权限错误: safe_path={safe_path}, error={e}")
        return {"ok": False, "error": f"没有权限写入文件: {path}"}
    except OSError as e:
        logger.warning(f"[write_file] OS错误: safe_path={safe_path}, error={e}")
        return {"ok": False, "error": f"写入文件失败: {e}"}

    result = {
        "ok": True,
        "path": path,
        "abs_path": safe_path,
        "size": len(content_bytes),
    }
    if backup_path:
        result["backup"] = backup_path

    logger.info(f"[write_file] 写入完成: ok=True, size={len(content_bytes)}, backup={backup_path}")
    return result


def list_directory(path: str = ".", show_hidden: bool = False, max_items: int = 500) -> dict:
    """列出目录内容

    安全措施：
    - 路径遍历防护
    - 系统保护目录禁止访问

    Args:
        path: 目录路径，默认为当前工作目录
        show_hidden: 是否显示隐藏文件/目录
        max_items: 最大返回条目数

    Returns:
        dict: {ok, path, items: [{name, type, size, modified, ...}], total, error}
    """
    logger.info(f"[list_directory] 开始列出目录: path={path}, show_hidden={show_hidden}, max_items={max_items}")
    try:
        safe_path = safe_resolve_path(path)
        logger.info(f"[list_directory] 路径安全解析成功: safe_path={safe_path}")
    except ValueError as e:
        logger.warning(f"[list_directory] 路径解析失败: path={path}, error={e}")
        return {"ok": False, "error": str(e)}

    if not os.path.exists(safe_path):
        logger.warning(f"[list_directory] 路径不存在: safe_path={safe_path}")
        return {"ok": False, "error": f"路径不存在: {path}"}
    if not os.path.isdir(safe_path):
        # 如果是文件，返回文件信息
        logger.info(f"[list_directory] 路径是文件而非目录: safe_path={safe_path}")
        return {
            "ok": True,
            "path": path,
            "type": "file",
            "name": os.path.basename(safe_path),
            "total": 1,
        }

    items = []
    logger.info(f"[list_directory] 开始遍历目录内容")
    try:
        for name in os.listdir(safe_path):
            if not show_hidden and name.startswith("."):
                continue
            if len(items) >= max_items:
                logger.info(f"[list_directory] 达到最大条目数限制: max_items={max_items}")
                break
            item_path = os.path.join(safe_path, name)
            try:
                info = _get_single_file_info(item_path)
                info["name"] = name
                items.append(info)
                logger.debug(f"[list_directory] 获取文件信息成功: name={name}, type={info.get('type')}")
            except OSError as e:
                logger.warning(f"[list_directory] 获取文件信息失败: name={name}, error={e}")
                items.append({"name": name, "type": "unknown"})
    except PermissionError as e:
        logger.warning(f"[list_directory] 权限错误: safe_path={safe_path}, error={e}")
        return {"ok": False, "error": f"没有权限列出目录: {path}"}
    except OSError as e:
        logger.warning(f"[list_directory] OS错误: safe_path={safe_path}, error={e}")
        return {"ok": False, "error": f"列出目录失败: {e}"}

    # 排序：目录优先，然后按名称
    items.sort(key=lambda x: (0 if x.get("type") == "dir" else 1, x.get("name", "")))
    logger.info(f"[list_directory] 目录列出完成: total_items={len(items)}")

    return {
        "ok": True,
        "path": path,
        "abs_path": safe_path,
        "type": "dir",
        "items": items,
        "total": len(items),
    }


def get_file_info(path: str) -> dict:
    """获取文件或目录的详细信息

    Args:
        path: 文件/目录路径

    Returns:
        dict: {ok, path, type, size, modified, created, ...}
    """
    try:
        safe_path = safe_resolve_path(path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    if not os.path.exists(safe_path):
        return {"ok": False, "error": f"路径不存在: {path}"}

    info = _get_single_file_info(safe_path)
    info["ok"] = True
    info["path"] = path
    info["abs_path"] = safe_path
    return info


def search_files(pattern: str, root_path: str = ".", max_results: int = 200, ignore_case: bool = True) -> dict:
    """搜索匹配模式的文件

    Args:
        pattern: 文件名模式（支持 glob 通配符，如 *.py, **/*.md）
        root_path: 搜索根目录，默认为当前工作目录
        max_results: 最大返回结果数
        ignore_case: 是否忽略大小写

    Returns:
        dict: {ok, pattern, root, results: [{path, name, type, size}], total, error}
    """
    logger.info(f"[search_files] 开始搜索文件: pattern={pattern}, root_path={root_path}, max_results={max_results}, ignore_case={ignore_case}")
    try:
        safe_root = safe_resolve_path(root_path)
        logger.info(f"[search_files] 路径安全解析成功: safe_root={safe_root}")
    except ValueError as e:
        logger.warning(f"[search_files] 路径解析失败: root_path={root_path}, error={e}")
        return {"ok": False, "error": str(e)}

    if not os.path.exists(safe_root):
        logger.warning(f"[search_files] 搜索根目录不存在: safe_root={safe_root}")
        return {"ok": False, "error": f"搜索根目录不存在: {root_path}"}
    if not os.path.isdir(safe_root):
        logger.warning(f"[search_files] 搜索根目录不是目录: safe_root={safe_root}")
        return {"ok": False, "error": f"搜索根目录不是目录: {root_path}"}

    results = []
    walked = 0
    max_walk = 50000  # 防止遍历过多文件
    logger.info(f"[search_files] 开始遍历目录树")

    try:
        for root, dirs, files in os.walk(safe_root):
            # 跳过保护目录
            dirs[:] = [d for d in dirs if not is_protected_path(os.path.join(root, d))]
            logger.debug(f"[search_files] 当前目录: root={root}, dirs_count={len(dirs)}, files_count={len(files)}")

            for fname in files:
                walked += 1
                if walked > max_walk:
                    logger.warning(f"[search_files] 遍历文件数达到上限: max_walk={max_walk}")
                    break
                if len(results) >= max_results:
                    logger.info(f"[search_files] 结果数达到上限: max_results={max_results}")
                    break

                # 模式匹配
                matched = False
                if ignore_case:
                    matched = fnmatch.fnmatch(fname.lower(), pattern.lower())
                else:
                    matched = fnmatch.fnmatch(fname, pattern)
                
                logger.debug(f"[search_files] 模式匹配: fname={fname}, matched={matched}")

                if matched:
                    full_path = os.path.join(root, fname)
                    try:
                        stat = os.stat(full_path)
                        results.append({
                            "path": os.path.relpath(full_path, safe_root),
                            "abs_path": full_path,
                            "name": fname,
                            "size": stat.st_size,
                            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                        })
                        logger.debug(f"[search_files] 添加匹配结果: fname={fname}, size={stat.st_size}")
                    except OSError as e:
                        logger.warning(f"[search_files] 获取文件stat失败: full_path={full_path}, error={e}")

            if walked > max_walk:
                break
    except PermissionError as e:
        logger.warning(f"[search_files] 权限错误（继续返回已有结果）: error={e}")
        pass  # 部分目录无权限，继续返回已有结果
    except OSError as e:
        logger.warning(f"[search_files] OS错误: safe_root={safe_root}, error={e}")
        return {"ok": False, "error": f"搜索文件失败: {e}"}

    logger.info(f"[search_files] 搜索完成: total_results={len(results)}, walked={walked}, truncated={len(results) >= max_results or walked >= max_walk}")
    return {
        "ok": True,
        "pattern": pattern,
        "root": root_path,
        "abs_root": safe_root,
        "results": results,
        "total": len(results),
        "truncated": len(results) >= max_results or walked >= max_walk,
    }


def _get_single_file_info(path: str) -> dict:
    """获取单个文件/目录的元信息"""
    logger.info(f"[_get_single_file_info] 开始获取文件信息: path={path}")
    try:
        stat = os.stat(path)
        logger.info(f"[_get_single_file_info] stat获取成功: size={stat.st_size}, mode={oct(stat.st_mode)}")
    except OSError as e:
        logger.warning(f"[_get_single_file_info] stat获取失败: path={path}, error={e}")
        raise
    
    is_dir = os.path.isdir(path)
    is_link = os.path.islink(path)
    logger.info(f"[_get_single_file_info] 文件属性: is_dir={is_dir}, is_link={is_link}")

    info = {
        "type": "dir" if is_dir else "file",
        "size": stat.st_size,
        "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
        "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_ctime)),
        "permissions": oct(stat.st_mode)[-3:],
        "is_link": is_link,
    }

    if not is_dir:
        info["extension"] = os.path.splitext(path)[1].lower()
        logger.debug(f"[_get_single_file_info] 文件扩展名: extension={info['extension']}")

    if is_link:
        logger.info(f"[_get_single_file_info] 文件是符号链接，尝试读取目标")
        try:
            info["link_target"] = os.readlink(path)
            logger.info(f"[_get_single_file_info] 符号链接目标: link_target={info['link_target']}")
        except OSError as e:
            logger.warning(f"[_get_single_file_info] 读取符号链接目标失败: path={path}, error={e}")
            pass

    logger.info(f"[_get_single_file_info] 文件信息获取完成: type={info['type']}, size={info['size']}")
    return info


def _guess_mime_type(path: str) -> str:
    """根据扩展名猜测 MIME 类型"""
    ext = os.path.splitext(path)[1].lower()
    mime_map = {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".html": "text/html",
        ".htm": "text/html",
        ".css": "text/css",
        ".js": "application/javascript",
        ".json": "application/json",
        ".xml": "application/xml",
        ".yaml": "text/yaml",
        ".yml": "text/yaml",
        ".toml": "application/toml",
        ".ini": "text/plain",
        ".cfg": "text/plain",
        ".conf": "text/plain",
        ".csv": "text/csv",
        ".py": "text/x-python",
        ".java": "text/x-java",
        ".c": "text/x-c",
        ".cpp": "text/x-c++",
        ".h": "text/x-c-header",
        ".sh": "text/x-shellscript",
        ".bat": "text/x-bat",
        ".ps1": "text/x-powershell",
        ".sql": "text/x-sql",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".ico": "image/vnd.microsoft.icon",
        ".pdf": "application/pdf",
        ".zip": "application/zip",
        ".gz": "application/gzip",
        ".tar": "application/x-tar",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".mp4": "video/mp4",
    }
    return mime_map.get(ext, "application/octet-stream")


# ════════════════════════════════════════════════════════════
#  工作区管理
# ════════════════════════════════════════════════════════════

WORKSPACE_DIR = os.path.join(os.path.dirname(__file__), "..", "workspace")


def init_workspace():
    """初始化受保护的工作区目录"""
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    # 创建 .gitkeep
    gitkeep = os.path.join(WORKSPACE_DIR, ".gitkeep")
    if not os.path.exists(gitkeep):
        with open(gitkeep, "w") as f:
            f.write("# 云枢受保护工作区\n")
    # 创建 readme
    readme = os.path.join(WORKSPACE_DIR, "README.txt")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write("云枢受保护工作区\n此目录内的文件操作受安全策略约束。\n")
    logger.info(f"工作区已初始化: {WORKSPACE_DIR}")
    return WORKSPACE_DIR


def list_workspace(path=""):
    """列出工作区内容"""
    full_path = os.path.normpath(os.path.join(WORKSPACE_DIR, path))
    if not full_path.startswith(os.path.normpath(WORKSPACE_DIR)):
        raise ValueError("路径超出工作区范围")
    if not os.path.exists(full_path):
        return {"path": path, "items": [], "error": "路径不存在"}
    if os.path.isfile(full_path):
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(5000)
        return {"path": path, "type": "file", "size": os.path.getsize(full_path), "content": content}
    items = []
    for name in os.listdir(full_path):
        item_path = os.path.join(full_path, name)
        items.append({
            "name": name,
            "type": "dir" if os.path.isdir(item_path) else "file",
            "size": os.path.getsize(item_path) if os.path.isfile(item_path) else 0,
        })
    return {"path": path, "type": "dir", "items": sorted(items, key=lambda x: (x["type"], x["name"]))}


def write_workspace(path, content):
    """写入工作区文件"""
    full_path = os.path.normpath(os.path.join(WORKSPACE_DIR, path))
    if not full_path.startswith(os.path.normpath(WORKSPACE_DIR)):
        raise ValueError("路径超出工作区范围")
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    return {"ok": True, "path": path, "size": len(content)}


def delete_workspace(path):
    """删除工作区文件/目录"""
    full_path = os.path.normpath(os.path.join(WORKSPACE_DIR, path))
    if not full_path.startswith(os.path.normpath(WORKSPACE_DIR)):
        raise ValueError("路径超出工作区范围")
    if path in ("", ".", "/"):
        raise ValueError("不能删除工作区根目录")
    if os.path.isdir(full_path):
        shutil.rmtree(full_path)
    else:
        os.remove(full_path)
    return {"ok": True, "path": path}


# ════════════════════════════════════════════════════════════
#  Python 沙盒
# ════════════════════════════════════════════════════════════

# 沙盒拒绝的模式（类型属性遍历逃逸检测）
_SANDBOX_BLOCKED_PATTERNS = [
    ".__class__", ".__bases__", ".__mro__", ".__subclasses__",
    ".__globals__", ".__code__", ".__dict__", ".__builtins__",
    ".__init__", ".__getattribute__", ".__getitem__",
    "getattr(", "hasattr(", "eval(", "exec(", "compile(",
    "__import__(", "import ", "open(", "__builtins",
    "globals()", "locals()", "vars(", "type(",
]

# 沙盒允许的安全内置函数（去除了异常类和可反射的类型）
_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "chr": chr,
    "dict": dict, "enumerate": enumerate, "filter": filter, "float": float,
    "int": int, "len": len, "list": list,
    "map": map, "max": max, "min": min, "ord": ord, "range": range,
    "reversed": reversed, "round": round, "set": set, "slice": slice,
    "sorted": sorted, "str": str, "sum": sum, "tuple": tuple, "zip": zip,
    "True": True, "False": False, "None": None,
}


def run_sandbox(code, timeout_sec=5):
    """在受限的 Python 沙盒中执行代码

    安全措施：
    - 仅暴露纯函数内置（无异常类、无反射函数）
    - 在独立线程中执行，带超时
    - 预检查已知逃逸模式
    - 捕获 stdout/stderr 输出
    """
    import sys
    import threading
    import io

    result = {"stdout": "", "stderr": "", "error": None, "timed_out": False}

    # 预检查：阻止已知的沙箱逃逸模式
    for pattern in _SANDBOX_BLOCKED_PATTERNS:
        if pattern in code:
            result["error"] = f"代码包含被禁止的模式: {pattern}"
            return result

    # 创建受限的全局命名空间
    safe_globals = {"__builtins__": _SAFE_BUILTINS}

    # 捕获输出
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    exc = [None]

    def _run():
        try:
            exec(code, safe_globals)
        except Exception as e:
            # 不暴露异常类型（防止类遍历攻击）
            exc[0] = str(type(e).__name__) + ": " + str(e)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout_sec)

    result["stdout"] = sys.stdout.getvalue()[:10000]
    result["stderr"] = sys.stderr.getvalue()[:5000]
    result["timed_out"] = thread.is_alive()
    if exc[0]:
        result["error"] = exc[0]
    if result["timed_out"]:
        result["error"] = f"执行超时 ({timeout_sec}秒)"

    sys.stdout = old_stdout
    sys.stderr = old_stderr
    return result


# ════════════════════════════════════════════════════════════
#  定时任务管理 (Windows Task Scheduler)
# ════════════════════════════════════════════════════════════

SCHEDULED_TASKS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "scheduled_tasks.json")


def _load_tasks():
    try:
        with open(SCHEDULED_TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"tasks": []}


def _save_tasks(data):
    os.makedirs(os.path.dirname(SCHEDULED_TASKS_FILE), exist_ok=True)
    with open(SCHEDULED_TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_scheduled_tasks():
    """列出所有已注册的定时任务"""
    return _load_tasks()


def create_scheduled_task(name, command, interval_sec=60, enabled=True):
    """创建受控的定时任务（仅限白名单命令）"""
    # 白名单检查
    allowed = ["python", "echo", "dir", "type", "curl", "ping"]
    cmd_lower = command.lower()
    if not any(cmd_lower.startswith(a) for a in allowed):
        return {"ok": False, "error": f"命令不在白名单中。允许的命令: {', '.join(allowed)}"}

    data = _load_tasks()
    task = {
        "id": str(int(time.time() * 1000)),
        "name": name,
        "command": command,
        "interval_sec": interval_sec,
        "enabled": enabled,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "last_run": None,
        "run_count": 0,
    }
    data["tasks"].append(task)
    _save_tasks(data)
    # 同步注册到运行中的调度器
    try:
        from agent.task_scheduler import get_scheduler
        scheduler = get_scheduler()
        if scheduler.running:
            scheduler.add_command_task(name, command, interval_sec, task_id, enabled)
    except Exception:
        pass
    return {"ok": True, "task": task}


def delete_scheduled_task(task_id):
    """删除定时任务"""
    data = _load_tasks()
    before = len(data["tasks"])
    data["tasks"] = [t for t in data["tasks"] if t["id"] != task_id]
    _save_tasks(data)
    # 同步移除
    try:
        from agent.task_scheduler import get_scheduler
        scheduler = get_scheduler()
        if scheduler.running:
            scheduler.remove_task(task_id)
    except Exception:
        pass
    return {"ok": True, "deleted": before > len(data["tasks"])}


def toggle_scheduled_task(task_id, enabled):
    """启用/禁用定时任务"""
    data = _load_tasks()
    for t in data["tasks"]:
        if t["id"] == task_id:
            t["enabled"] = enabled
            _save_tasks(data)
            # 同步状态
            try:
                from agent.task_scheduler import get_scheduler
                scheduler = get_scheduler()
                if scheduler.running:
                    scheduler.set_task_enabled(task_id, enabled)
            except Exception:
                pass
            return {"ok": True}
    return {"ok": False, "error": "任务不存在"}


# ════════════════════════════════════════════════════════════
#  无头浏览器控制
# ════════════════════════════════════════════════════════════

_browser_instance = None

# 浏览器配置参数（可注入）
_browser_config = {
    "headless": True,
    "no_sandbox": True,
    "disable_dev_shm": True,
    "disable_gpu": True,
    "disable_extensions": True,
    "disable_file_system": True,
    "remote_debugging_port": 0,
    "page_load_timeout": 15,
}


def set_browser_config(**kwargs):
    """设置浏览器配置参数"""
    global _browser_config
    _browser_config.update(kwargs)


def get_browser(webdriver_module=None):
    """获取或创建无头浏览器实例（懒加载）
    
    Args:
        webdriver_module: 可选的 webdriver 模块，用于测试时注入 Mock 对象
        
    Returns:
        浏览器实例或 None
    """
    global _browser_instance
    if _browser_instance is None:
        try:
            # 如果传入了 webdriver 模块，则使用它（用于测试）
            if webdriver_module is not None:
                wd = webdriver_module
            else:
                from selenium import webdriver
                wd = webdriver
            
            from selenium.webdriver.chrome.options import Options
            opts = Options()
            opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--disable-gpu")
            opts.add_argument("--disable-extensions")
            opts.add_argument("--disable-file-system")
            opts.add_argument("--remote-debugging-port=0")
            _browser_instance = wd.Chrome(options=opts)
            logger.debug(f"Chrome浏览器实例创建成功，对象ID: {id(_browser_instance)}")

            # 关键修复: 后续配置失败时必须清理 _browser_instance，
            # 避免下次调用 get_browser 时返回部分初始化的实例。
            try:
                page_load_timeout = _browser_config.get("page_load_timeout", 15)
                _browser_instance.set_page_load_timeout(page_load_timeout)
                logger.info(f"页面加载超时时间设置为 {page_load_timeout} 秒")
            except Exception as timeout_e:
                logger.warning(f"设置页面加载超时失败: {timeout_e}")
                _cleanup_browser_instance()
                return None

            try:
                window_handles = _browser_instance.window_handles
                logger.debug(f"浏览器窗口句柄: {window_handles}")
            except Exception as handle_e:
                logger.debug(f"获取窗口句柄失败: {handle_e}")

            logger.info("无头浏览器已成功启动")
        except ImportError:
            logger.warning("selenium 未安装，浏览器功能不可用")
            return None
        except Exception as e:
            logger.warning(f"无头浏览器启动失败: {e}")
            # 任何启动失败均清理 _browser_instance，防止状态泄漏
            _cleanup_browser_instance()
            return None
    return _browser_instance


def _cleanup_browser_instance():
    """清理浏览器实例：尝试 quit, 然后将全局变量重置为 None。

    用于 get_browser 部分初始化失败时释放资源，避免下次调用返回
    已损坏/部分初始化的实例。
    """
    global _browser_instance
    if _browser_instance is not None:
        try:
            _browser_instance.quit()
        except Exception:
            # quit 失败不应阻止清理流程
            pass
        _browser_instance = None


def browser_navigate(url):
    """导航到指定 URL（仅允许 http/https）"""
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "仅允许 http/https 协议"}
    # 禁止内网地址
    blocked = ["localhost", "127.0.0.1", "0.0.0.0", "::1", "192.168.", "10.", "172.16."]
    for b in blocked:
        if b in url.lower():
            return {"ok": False, "error": f"禁止访问内网地址"}

    browser = get_browser()
    if not browser:
        return {"ok": False, "error": "浏览器不可用（需要安装 selenium）"}
    try:
        browser.get(url)
        title = browser.title
        text = browser.find_element("tag name", "body").text[:5000]
        return {"ok": True, "title": title, "url": browser.current_url, "text": text}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def browser_screenshot():
    """截取当前页面截图（返回 base64）"""
    import base64
    browser = get_browser()
    if not browser:
        return {"ok": False, "error": "浏览器不可用"}
    try:
        screenshot = browser.get_screenshot_as_base64()
        return {"ok": True, "screenshot_base64": screenshot[:500000]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def browser_close():
    """关闭浏览器"""
    global _browser_instance
    if _browser_instance:
        try:
            _browser_instance.quit()
        except Exception:
            pass
        _browser_instance = None


# ════════════════════════════════════════════════════════════
#  Shell 执行 — 云枢执行 shell 命令的能力
# ════════════════════════════════════════════════════════════

# Shell 类型与执行命令的映射
_SHELL_COMMANDS = {
    "bash": ["bash", "-c"],
    "cmd": ["cmd", "/c"],
    "powershell": ["powershell", "-Command"],
}

# Unix 风格特征（检测到这些则倾向使用 bash）
_UNIX_SHELL_PATTERNS = [
    r"\$\(.*\)",      # $() 命令替换
    r"grep\s+",       # grep
    r"ls\s+-[lahr]",  # ls -l/a/h/r
    r"ps\s+\-?(aux|ef)", # ps aux/ef/-ef
    r"chmod\s+",      # chmod
    r"chown\s+",      # chown
    r"rm\s+-[rf]",    # rm -r/-f
    r"mv\s+",         # mv
    r"cp\s+",         # cp
    r"cat\s+",        # cat
    r"less\s+",       # less
    r"tail\s+",       # tail
    r"head\s+",       # head
    r"which\s+",      # which
    r"whoami",        # whoami
    r"pwd",           # pwd
]

# PowerShell cmdlet 特征（检测到这些则使用 powershell）
_PS_CMDLET_PATTERNS = [
    r"(Get|Set|Write|Read|Invoke|Remove|New|Add|Select|Where|ForEach)-",
    r"\$Env:",         # PowerShell 环境变量
    r"\$_\s*\.",      # PowerShell 管道变量
    r"\$\w+\s*=",     # PowerShell 变量赋值
    r"\bWrite-(Host|Output|Error|Warning)",
    r"\bGet-(Process|Service|ChildItem|Content|Date|Item)",
    r"\bSet-(ExecutionPolicy|Location|Content)",
    r"\bRemove-Item",
]


def _detect_shell(command: str) -> str:
    """根据命令内容智能检测适合的 shell 类型

    Args:
        command: 要执行的命令字符串

    Returns:
        str: "bash", "cmd" 或 "powershell"
    """
    # 先检测 PowerShell cmdlet（特征最明显）
    for pattern in _PS_CMDLET_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return "powershell"

    # 再检测 Unix 风格特征
    for pattern in _UNIX_SHELL_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return "bash"

    # Windows 环境下的 cmd 常见命令
    if os.name == "nt":
        cmd_only_patterns = [
            r"\bdir\s+",
            r"\btype\s+",
            r"\bfind\s+",
        ]
        for pattern in cmd_only_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return "cmd"

    # 默认使用 bash（云枢运行在 Git Bash 环境）
    return "bash"


def _truncate_output(text: str, max_bytes: int = 102400) -> str:
    """截断过长输出，防止爆内存

    Args:
        text: 原始输出文本
        max_bytes: 最大字节数，默认 100KB

    Returns:
        str: 截断后的文本（可能附加 truncated 标注）
    """
    if not text:
        return text
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
    return truncated + f"\n...（输出已截断，共 {len(encoded)} 字节）"


def execute_shell(command: str, shell: str = "auto", cwd: str = None, timeout: int = 30) -> dict:
    """在 shell 中执行命令并返回结果

    注意：此函数本身不进行命令安全检查（如危险命令过滤）。
    调用方（如 digital_life.py 中的工具注册层）应负责使用 SafetyGuard
    和 PermissionSystem 执行安全扫描。

    Args:
        command: 要执行的命令字符串
        shell: "auto" / "bash" / "cmd" / "powershell"
        cwd: 工作目录，默认使用当前目录
        timeout: 超时秒数，会被限制在 1-120 范围内，默认 30

    Returns:
        dict: {ok: bool, stdout: str, stderr: str, exit_code: int, shell: str, cwd: str}
    """
    if not command or not command.strip():
        return {"ok": False, "error": "命令不能为空", "exit_code": -1}

    # 1. 确定 shell 类型
    shell = _detect_shell(command) if shell == "auto" else shell.lower()
    if shell not in _SHELL_COMMANDS:
        return {"ok": False, "error": f"不支持的 shell 类型: {shell}，可选: auto/bash/cmd/powershell", "exit_code": -1}

    # 2. 构建执行命令
    shell_cmd = _SHELL_COMMANDS[shell]
    cmd = shell_cmd + [command]

    # 3. 确定工作目录
    work_dir = cwd or os.getcwd()

    # 4. 限制超时
    timeout = max(1, min(timeout, 120))

    # 5. 执行
    try:
        proc = subprocess.run(
            cmd,
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        stdout = _truncate_output(proc.stdout.decode("utf-8", errors="replace"))
        stderr = _truncate_output(proc.stderr.decode("utf-8", errors="replace"))

        return {
            "ok": proc.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
            "shell": shell,
            "cwd": work_dir,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"命令执行超时（{timeout}秒）",
            "exit_code": -1,
            "shell": shell,
            "cwd": work_dir,
        }
    except FileNotFoundError as e:
        return {
            "ok": False,
            "error": f"找不到 shell 程序: {e}",
            "exit_code": -1,
            "shell": shell,
            "cwd": work_dir,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"执行失败: {e}",
            "exit_code": -1,
            "shell": shell,
            "cwd": work_dir,
        }


# ════════════════════════════════════════════════════════════
#  进程管理（白名单制）
# ════════════════════════════════════════════════════════════

# 内置默认白名单（不可删除）
_DEFAULT_WHITELIST = [
    "notepad.exe", "calc.exe", "mspaint.exe", "write.exe",
    "python.exe", "python3.exe", "pip.exe",
    "node.exe", "npm.cmd", "npx.cmd",
    "git.exe", "curl.exe", "wget.exe",
    "explorer.exe", "cmd.exe",
]
PROCESS_WHITELIST = _DEFAULT_WHITELIST  # 向后兼容

_WHITELIST_CONFIG_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'process_whitelist_custom.json')


def _load_custom_whitelist() -> list[str]:
    """加载用户自定义白名单条目"""
    try:
        with open(_WHITELIST_CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get("custom", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_custom_whitelist(entries: list[str]):
    """保存用户自定义白名单条目"""
    os.makedirs(os.path.dirname(_WHITELIST_CONFIG_FILE), exist_ok=True)
    with open(_WHITELIST_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump({"custom": entries}, f, ensure_ascii=False, indent=2)


def get_process_whitelist() -> list[str]:
    """获取完整白名单（默认 + 自定义）"""
    return _DEFAULT_WHITELIST + _load_custom_whitelist()


def add_whitelist_entry(program: str) -> dict:
    """添加自定义白名单条目"""
    program = program.strip().lower()
    if not program:
        return {"ok": False, "error": "程序名不能为空"}
    if program in _DEFAULT_WHITELIST:
        return {"ok": False, "error": f"「{program}」已在默认白名单中"}
    custom = _load_custom_whitelist()
    if program in custom:
        return {"ok": False, "error": f"「{program}」已存在"}
    custom.append(program)
    _save_custom_whitelist(custom)
    logger.info(f"白名单新增: {program}")
    return {"ok": True, "program": program}


def remove_whitelist_entry(program: str) -> dict:
    """移除自定义白名单条目"""
    program = program.strip().lower()
    if not program:
        return {"ok": False, "error": "程序名不能为空"}
    if program in _DEFAULT_WHITELIST:
        return {"ok": False, "error": f"「{program}」是默认条目，不能删除"}
    custom = _load_custom_whitelist()
    if program not in custom:
        return {"ok": False, "error": f"「{program}」不在自定义白名单中"}
    custom.remove(program)
    _save_custom_whitelist(custom)
    logger.info(f"白名单移除: {program}")
    return {"ok": True, "program": program}


def get_whitelist_detail() -> dict:
    """获取白名单详情（区分默认和自定义）"""
    return {
        "default": _DEFAULT_WHITELIST,
        "custom": _load_custom_whitelist(),
        "all": get_process_whitelist(),
    }


def start_process(program, args=None, cwd=None):
    """启动白名单程序"""
    prog_lower = program.lower()
    allowed = False
    wl = get_process_whitelist()
    for w in wl:
        if prog_lower == w or prog_lower.endswith("\\" + w):
            allowed = True
            break
    if not allowed:
        return {"ok": False, "error": f"程序不在白名单中。允许: {', '.join(wl)}"}

    try:
        cmd = [program] + (args if args else [])
        proc = subprocess.Popen(
            cmd, cwd=cwd or WORKSPACE_DIR,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return {"ok": True, "pid": proc.pid, "program": program}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_processes():
    """列出运行中的白名单进程"""
    import psutil
    result = []
    wl = get_process_whitelist()
    for proc in psutil.process_iter(["pid", "name", "create_time", "status"]):
        try:
            info = proc.info
            name = (info["name"] or "").lower()
            if any(name == w.lower() for w in wl):
                result.append({
                    "pid": info["pid"],
                    "name": info["name"],
                    "status": info["status"],
                })
        except Exception:
            pass
    return result


def stop_process(pid):
    """终止指定进程（仅限白名单程序）"""
    import psutil
    try:
        proc = psutil.Process(pid)
        name = (proc.name() or "").lower()
        wl = get_process_whitelist()
        if not any(name == w.lower() for w in wl):
            return {"ok": False, "error": f"进程 {name} 不在白名单中，拒绝终止"}
        proc.terminate()
        return {"ok": True, "pid": pid, "name": proc.name()}
    except psutil.NoSuchProcess:
        return {"ok": False, "error": "进程不存在"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ════════════════════════════════════════════════════════════
#  剪贴板接口
# ════════════════════════════════════════════════════════════

def get_clipboard():
    """读取剪贴板内容"""
    try:
        import pyperclip
        content = pyperclip.paste()
        return {"ok": True, "content": content[:10000]}
    except ImportError:
        try:
            import subprocess
            result = subprocess.run(
                ["powershell", "-Command", "Get-Clipboard"],
                capture_output=True, text=True, timeout=3,
            )
            return {"ok": True, "content": result.stdout[:10000]}
        except Exception as e:
            return {"ok": False, "error": f"剪贴板读取失败: {e}"}


def set_clipboard(text):
    """写入剪贴板（需要确认）"""
    if len(text) > 50000:
        return {"ok": False, "error": "内容过长（最大 50000 字符）"}
    try:
        import pyperclip
        pyperclip.copy(text)
        return {"ok": True}
    except ImportError:
        try:
            import subprocess
            subprocess.run(
                ["powershell", "-Command", f"Set-Clipboard -Value '{text[:5000]}'"],
                capture_output=True, timeout=3,
            )
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"剪贴板写入失败: {e}"}


# ════════════════════════════════════════════════════════════
#  天气查询 — 使用 wttr.in 服务，无需 API Key
# ════════════════════════════════════════════════════════════

def get_weather(city: str = "", format: str = "text") -> dict:
    """查询天气信息

    使用 wttr.in 服务，无需 API Key。

    Args:
        city: 城市名称，如 "Beijing"、"Shanghai"、"Tokyo"，留空则自动查询当前 IP 所在地天气
        format: 返回格式
            - "text": 简洁文本格式（如 "Beijing: ☀️ +25°C"）
            - "json": 完整 JSON 数据格式
            - "full": 完整文本预报格式

    Returns:
        dict: {ok, data, format, city, error}
    """
    import urllib.request
    import urllib.error
    import urllib.parse

    if not city:
        city = ""

    # 根据 format 选择 URL
    if format == "json":
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1" if city else "https://wttr.in?format=j1"
    elif format == "full":
        url = f"https://wttr.in/{urllib.parse.quote(city)}?lang=zh" if city else "https://wttr.in?lang=zh"
    else:
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=3&lang=zh" if city else "https://wttr.in?format=3&lang=zh"

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "curl/7.68.0",
        })
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read()

        if format == "json":
            data = json.loads(raw.decode("utf-8"))
        else:
            data = raw.decode("utf-8").strip()

        return {
            "ok": True,
            "data": data,
            "format": format,
            "city": city or "auto",
        }
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP 错误: {e.code} {e.reason}", "city": city}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"网络连接失败: {e.reason}", "city": city}
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"JSON 解析失败: {e}", "city": city}
    except Exception as e:
        return {"ok": False, "error": f"未知错误: {e}", "city": city}


def expand_context_from_memory(digital_life, query, max_items=5):
    """从记忆库中查找更多与当前话题相关的上下文信息"""
    try:
        if hasattr(digital_life, '_vector_memory') and digital_life._vector_memory:
            results = digital_life._vector_memory.search(query, top_k=max_items)
            context_items = []
            for item in results:
                if hasattr(item, 'content'):
                    context_items.append({
                        'content': item.content,
                        'score': getattr(item, 'score', 0)
                    })
                elif isinstance(item, dict) and 'content' in item:
                    context_items.append({
                        'content': item['content'],
                        'score': item.get('score', 0)
                    })
            return {
                "ok": True,
                "query": query,
                "count": len(context_items),
                "items": context_items
            }
        else:
            return {
                "ok": False,
                "error": "向量记忆系统未启用",
                "query": query
            }
    except Exception as e:
        logger.error(f"expand_context_from_memory 错误: {e}")
        return {
            "ok": False,
            "error": str(e),
            "query": query
        }
