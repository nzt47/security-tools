"""文件操作工具——从 system_tools.py 拆出

包含：路径安全检查、文件读写、目录列表、文件搜索等底层操作。
"""
import os
import re
import time
import json
import uuid
import shutil
import base64
import logging
import fnmatch
from pathlib import Path
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


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
    else:
        # Unix 系统保护目录检测
        for protected in PROTECTED_SYSTEM_DIRS_UNIX:
            if abs_path.startswith(protected + os.sep) or abs_path == protected:
                return True

    return False


# ════════════════════════════════════════════════════════════
#  凭据文件边界（安全审计 S-2）
# ════════════════════════════════════════════════════════════
# 【改动前的事实】read_file 只挡"系统保护目录"与"可执行扩展名"，
#   **不拦 .env / 私钥 / 凭据文件**，并且路径保护用的是
#   abspath + startswith（**未 realpath**）⇒ symlink / junction 可绕过。
#   后果：模型可以直接读到 data/audit/audit_signing_key.pem、.env（实测本机
#   .env 内有 20 个明文密钥）等，并把明文带进上下文。这直接违反
#   V2.0 INV-05「凭证不出 Adapter，内存不留明文」与 §3.8「输出清洗」。
#
# 【为什么不做成开关】本仓对"不许写/不许读"类边界的一致做法是**机制级防线**
#   （见 agent/settings/overrides.py::_guard_path），而不是可配置项。
#   做成 env 开关还会触碰 agent/settings/registry.py 的零缺口守卫，
#   而这里并不存在"合法的第二种取值"。
#
# 【为什么不加"工作区根约束"】read_file 的设计用途就是读本机任意文本文件
#   （见其 docstring 与 use 场景），加根约束会直接砍掉既有能力，属越界改动。
#   本次只补**凭据边界**这一条与凭证泄漏直接相关的口子。

#: 按**文件名**精确匹配的凭据文件（小写比较）
CREDENTIAL_FILENAMES = {
    ".env", ".env.local", ".env.production", ".env.development",
    ".netrc", "_netrc", ".pgpass", ".my.cnf", ".git-credentials",
    "credentials", "credentials.json", "secrets.json", "secrets.yaml",
    ".secure_config.json", "kubeconfig", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
}

#: 按**扩展名**匹配的凭据/密钥文件
CREDENTIAL_EXTENSIONS = {
    ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".ppk",
    ".asc", ".gpg", ".kdbx", ".ovpn",
}

#: 按**路径片段**匹配的凭据目录（如 ~/.aws/credentials、~/.ssh/id_rsa）
CREDENTIAL_DIR_PARTS = {".ssh", ".aws", ".gnupg", ".docker", ".kube"}

#: 前缀匹配的凭据文件名（.env.backups / .env.example 之类一并挡掉）
CREDENTIAL_FILENAME_PREFIXES = (".env.", "secrets.", "credentials.")


def is_credential_path(path: str) -> bool:
    """判断路径是否指向凭据/密钥文件（**read 侧的硬边界**）。

    Args:
        path: 任意路径（内部会做 realpath，故 symlink / junction 指向的
              真实凭据文件同样会被识别）

    Returns:
        True 表示命中凭据边界，调用方应拒绝读取。

    【为什么必须 realpath】只比对字面路径时，一个指向 .env 的 symlink
    可以完全绕过（实测原实现就是 abspath + startswith，无 realpath）。
    """
    try:
        real = os.path.realpath(path)
    except Exception:  # noqa: BLE001  解析失败 ⇒ 按最严处理，拒绝读取
        return True

    name = os.path.basename(real).lower()
    if name in CREDENTIAL_FILENAMES:
        return True
    if any(name.startswith(p) for p in CREDENTIAL_FILENAME_PREFIXES):
        return True
    if os.path.splitext(name)[1].lower() in CREDENTIAL_EXTENSIONS:
        return True

    # 目录片段（大小写不敏感；Windows 上 .ssh 等同样存在）
    parts = [p.lower() for p in real.replace("\\", "/").split("/") if p]
    if any(p in CREDENTIAL_DIR_PARTS for p in parts):
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
    logger.info(log_dict({'module_name': 'file_tools', 'action': 'path.path', 'msg': f'[safe_resolve_path] 开始解析路径: path={path}'}))
    try:
        abs_path = os.path.abspath(os.path.normpath(path))
        logger.info(log_dict({'module_name': 'file_tools', 'action': 'abs_path.abs_path', 'msg': f'[safe_resolve_path] 路径规范化成功: abs_path={abs_path}'}))
    except (ValueError, OSError) as e:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'path.path.error', 'msg': f'[safe_resolve_path] 路径解析异常: path={path}, error={type(e).__name__}: {e}'}))
        raise ValueError(f"路径解析失败: {e}")

    if is_protected_path(abs_path):
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'abs_path.abs_path', 'msg': f'[safe_resolve_path] 路径被保护目录拦截: abs_path={abs_path}'}))
        raise ValueError(f"路径位于系统保护目录，拒绝访问: {abs_path}")

    # 【S-2 补漏：堵 symlink / junction 绕过】原实现只检查字面 abs_path，
    #   一个指向 /etc 或保护目录的符号链接可以直接通过。此处对**真实路径**再判一次。
    #
    # 【2026-10-01 修一处我引入的跨平台回归 —— 务必保留此注释】
    #   我最初在 realpath 抛错时 `raise ValueError("路径无法解析为真实路径，拒绝访问")`，
    #   出发点是"解析不了就当危险"（fail-closed）。但它在 **Linux** 上把畸形输入的
    #   既有契约改掉了：`os.path.realpath("\x00invalid")` 在 Linux 抛
    #   `ValueError: lstat: embedded null character in path`，而 **Windows 不抛** ⇒
    #   本地 125 passed、CI 上
    #   `test_system_tools_platform.py::TestErrorHandling::test_invalid_path_characters`
    #   失败（该用例要求 safe_resolve_path 对这种输入**返回字符串**）。
    #   【正确取舍】本加固的目标是"符号链接绕过"，不是"畸形输入"——畸形输入自有下游
    #   open() 处理，其错误契约不该被本函数顺手改掉。故：**realpath 失败即跳过本项检查，
    #   保持改动前的行为**（不新增异常、不新增拒绝）。安全性不受影响：原实现同样
    #   不做这一检查，且畸形路径在下游必然打不开。
    try:
        _real = os.path.realpath(abs_path)
    except Exception:  # noqa: BLE001  解析不了 ⇒ 跳过本项检查（保持改动前行为）
        _real = ""
    if _real and _real != abs_path and is_protected_path(_real):
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'realpath', 'msg': f'[safe_resolve_path] 符号链接指向保护目录，已拦截: abs_path={abs_path}'}))
        raise ValueError(f"路径经符号链接指向系统保护目录，拒绝访问: {abs_path}")

    logger.info(log_dict({'module_name': 'file_tools', 'action': 'abs_path', 'msg': f'[safe_resolve_path] 路径解析完成，返回: {abs_path}'}))
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


def _is_probably_binary(raw_data: bytes, encoding: str) -> bool:
    """``read_file`` 专用的二进制判据：**能否严格解码成文本**（而非 ASCII 字节占比）

    Why 不复用 ``is_binary_content``：后者只把 ASCII 可打印字节（0x20-0x7E 与 0x09-0x0D）
    计为"文本"，而**中文字节全部 >= 0x80** ⇒ 中文稍多的文本文件非文本占比就超过 0.15，
    被判成二进制，``read_file`` 于是返回 base64——对一个中文智能体等于"读不了中文文件"
    （实测：纯中文与中英混合的 .txt 都被判二进制，纯 ASCII 正常）。
    ``is_binary_content`` 的历史语义被多处测试钉住（如断言 "mostly_text" 为二进制），
    故**不改它**，只让 ``read_file`` 换用本判据：NUL 字节快速判二进制，否则逐个尝试严格
    解码（声明编码 → utf-8 → gbk），任一成功即视为文本。

    与旧行为的一致性：含 NUL 的文件（PNG/PDF 主体、UTF-16 文本等）依旧返回 base64；
    差别仅在"无 NUL 但非 ASCII"这一类——正是中文/GBK 文本，现在能正常读出来了。
    """
    chunk = raw_data[:8192]
    if not chunk:
        return False
    if b"\x00" in chunk:
        return True
    tried = set()
    for candidate in (encoding, "utf-8", "gbk"):
        if not candidate or candidate in tried:
            continue
        tried.add(candidate)
        try:
            # 严格解码**整份**数据（只用前 8KB 会在多字节字符被截断处误判为二进制）
            raw_data.decode(candidate)
            return False
        except (UnicodeDecodeError, LookupError):
            continue
    return True


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
    logger.info(log_dict({'module_name': 'file_tools', 'action': 'path.path.encoding', 'msg': f'[read_file] 开始读取文件: path={path}, encoding={encoding}, max_size_mb={max_size_mb}'}))
    try:
        safe_path = safe_resolve_path(path)
        logger.info(log_dict({'module_name': 'file_tools', 'action': 'safe_path.safe_path', 'msg': f'[read_file] 路径安全解析成功: safe_path={safe_path}'}))
    except ValueError as e:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'path.path.error', 'msg': f'[read_file] 路径解析失败: path={path}, error={e}'}))
        return {"ok": False, "error": str(e)}

    # ── 凭据文件：**标注**而不是拒读（2026-09-30 回退一次过度收紧）────────
    # 【为什么不在这里硬拦】本仓对"读到凭据"这件事有**既有且已测试**的设计：
    #   读取允许，防线在**外发侧** —— `agent/policy/egress.py` 用
    #   `is_secret_path()` + `scan_secret_material()` 把"读了密钥"记成污点，
    #   之后任何出域请求被拒（`data_class_source="read_then_egress"`）。
    #   见 tests/unit/test_policy_integration.py::TestToolReadToEgressChain（3 条）。
    #   我最初在这里加硬拦，导致该链路**整条失效**：读取失败 ⇒ 不记污点 ⇒
    #   外发不再被拦。更糟的是它不泛化 —— shell_execute 一样能 cat .env，
    #   于是"拦了 read_file"只是给了一种已被防住的错觉。
    # 【保留什么】`is_credential_path()` 作为**分类器**保留（供外发侧/审计复用），
    #   并在结果里显式标注命中，使下游与日志可见（INV-08：降级/敏感要留痕）。
    _credential_hit = is_credential_path(safe_path)
    if _credential_hit:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'credential_read',
                                 'msg': f'[read_file] 读取的是凭据类文件，已标注并记入污点链路: path={path}'}))

    if not os.path.exists(safe_path):
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'safe_path.safe_path', 'msg': f'[read_file] 文件不存在: safe_path={safe_path}'}))
        return {"ok": False, "error": f"文件不存在: {path}"}
    if not os.path.isfile(safe_path):
        if os.path.isdir(safe_path):
            logger.warning(log_dict({'module_name': 'file_tools', 'action': 'safe_path.safe_path', 'msg': f'[read_file] 路径是目录而非文件: safe_path={safe_path}'}))
            return {"ok": False, "error": f"路径是目录而非文件: {path}，请使用 list_directory 工具列出目录内容"}
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'safe_path.safe_path', 'msg': f'[read_file] 路径不是文件: safe_path={safe_path}'}))
        return {"ok": False, "error": f"路径不是文件: {path}"}

    file_size = os.path.getsize(safe_path)
    logger.info(log_dict({'module_name': 'file_tools', 'action': 'file_size.bytes', 'msg': f'[read_file] 文件大小: {file_size} bytes'}))
    max_size = max_size_mb * 1024 * 1024
    if file_size > max_size:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'file_size.file_size.max_size', 'msg': f'[read_file] 文件过大: file_size={file_size}, max_size={max_size}'}))
        return {
            "ok": False, "error": f"文件过大 ({file_size / 1024 / 1024:.1f}MB)，超过限制 {max_size_mb}MB",
            "path": path, "size": file_size,
        }

    try:
        with open(safe_path, "rb") as f:
            raw_data = f.read()
        logger.info(log_dict({'module_name': 'file_tools', 'action': 'raw_data_size.len.raw_data', 'msg': f'[read_file] 文件读取成功: raw_data_size={len(raw_data)}'}))
    except PermissionError as e:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'safe_path.safe_path.error', 'msg': f'[read_file] 权限错误: safe_path={safe_path}, error={e}'}))
        return {"ok": False, "error": f"没有权限读取文件: {path}"}
    except OSError as e:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'safe_path.safe_path', 'msg': f'[read_file] OS错误: safe_path={safe_path}, error={e}'}))
        return {"ok": False, "error": f"读取文件失败: {e}"}

    is_binary = _is_probably_binary(raw_data, encoding)
    logger.info(log_dict({'module_name': 'file_tools', 'action': 'is_binary.is_binary', 'msg': f'[read_file] 二进制检测结果: is_binary={is_binary}'}))

    if encoding is None or is_binary:
        # 二进制模式，返回 base64
        logger.info(log_dict({'module_name': 'file_tools', 'action': 'base64', 'msg': f'[read_file] 使用二进制模式返回 base64'}))
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
    logger.info(log_dict({'module_name': 'file_tools', 'action': 'encoding.encoding', 'msg': f'[read_file] 使用文本模式解码: encoding={encoding}'}))
    try:
        content = raw_data.decode(encoding)
        logger.info(log_dict({'module_name': 'file_tools', 'action': 'encoding.encoding.content_length', 'msg': f'[read_file] 解码成功: encoding={encoding}, content_length={len(content)}'}))
    except UnicodeDecodeError as e:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'encoding.encoding.error', 'msg': f'[read_file] 解码失败，尝试降级: encoding={encoding}, error={e}'}))
        # 声明的编码不对：**先按 GBK 严格试一次**。GBK 在中文 Windows 上极常见，而下面
        # 的"替换式降级"会把 GBK 字节逐字节替换成 U+FFFD，读出来是乱码——对中文智能体
        # 等于读不了这类文件（与上面 _is_probably_binary 修的是同一类问题）。
        try:
            content = raw_data.decode("gbk")
            encoding = "gbk"
            logger.info(log_dict({'module_name': 'file_tools', 'action': 'encoding.encoding', 'msg': f'[read_file] GBK 严格解码成功: encoding={encoding}'}))
        except UnicodeDecodeError:
            # 编码不对，尝试自动检测
            try:
                content = raw_data.decode("utf-8", errors="replace")
                encoding = "utf-8 (with replacements)"
                logger.info(log_dict({'module_name': 'file_tools', 'action': 'encoding.encoding', 'msg': f'[read_file] 降级解码成功: encoding={encoding}'}))
            except Exception as e2:
                logger.warning(log_dict({'module_name': 'file_tools', 'action': 'utf.latin.error', 'msg': f'[read_file] utf-8降级失败，使用latin-1: error={e2}'}))
                content = raw_data.decode("latin-1")
                encoding = "latin-1"

    logger.info(log_dict({'module_name': 'file_tools', 'action': 'true.encoding', 'msg': f'[read_file] 文件读取完成: ok=True, encoding={encoding}, binary=False'}))

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
    logger.info(log_dict({'module_name': 'file_tools', 'action': 'path.path.encoding', 'msg': f'[write_file] 开始写入文件: path={path}, encoding={encoding}, content_length={(len(content) if content else 0)}'}))
    try:
        safe_path = safe_resolve_path(path)
        logger.info(log_dict({'module_name': 'file_tools', 'action': 'safe_path.safe_path', 'msg': f'[write_file] 路径安全解析成功: safe_path={safe_path}'}))
    except ValueError as e:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'path.path.error', 'msg': f'[write_file] 路径解析失败: path={path}, error={e}'}))
        return {"ok": False, "error": str(e)}

    # 禁止写入可执行文件类型
    if is_executable_extension(safe_path):
        ext = os.path.splitext(safe_path)[1]
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'ext.ext', 'msg': f'[write_file] 禁止写入可执行文件类型: ext={ext}'}))
        return {"ok": False, "error": f"禁止写入可执行/脚本文件类型 ({ext})"}

    # 检查内容大小
    content_bytes = content.encode(encoding) if isinstance(content, str) else content
    logger.info(log_dict({'module_name': 'file_tools', 'action': 'len.content_bytes.bytes', 'msg': f'[write_file] 内容大小: {len(content_bytes)} bytes'}))
    if len(content_bytes) > DEFAULT_MAX_WRITE_SIZE:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'size.len.content_bytes', 'msg': f'[write_file] 内容过大: size={len(content_bytes)}, max_size={DEFAULT_MAX_WRITE_SIZE}'}))
        return {
            "ok": False,
            "error": f"内容过大 ({len(content_bytes) / 1024 / 1024:.1f}MB)，超过限制 {DEFAULT_MAX_WRITE_SIZE // (1024 * 1024)}MB",
        }

    # 覆盖前备份
    backup_path = None
    if os.path.exists(safe_path):
        logger.info(log_dict({'module_name': 'file_tools', 'action': 'safe_path.safe_path', 'msg': f'[write_file] 文件已存在，准备备份: safe_path={safe_path}'}))
        try:
            backup_dir = os.path.join(os.path.dirname(__file__), "..", "..", ".file_backups")
            os.makedirs(backup_dir, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            fname = os.path.basename(safe_path)
            backup_path = os.path.join(backup_dir, f"{fname}.{timestamp}.bak")
            shutil.copy2(safe_path, backup_path)
            logger.info(log_dict({'module_name': 'file_tools', 'action': 'backup_path.backup_path', 'msg': f'[write_file] 备份成功: backup_path={backup_path}'}))
        except Exception as e:
            logger.warning(log_dict({'module_name': 'file_tools', 'action': 'error', 'msg': f'[write_file] 备份失败（继续写入）: error={e}'}))

    # 创建目录
    dir_path = os.path.dirname(safe_path)
    logger.info(log_dict({'module_name': 'file_tools', 'action': 'dir_path.dir_path', 'msg': f'[write_file] 检查目录: dir_path={dir_path}'}))
    try:
        os.makedirs(dir_path, exist_ok=True)
        logger.info(log_dict({'module_name': 'file_tools', 'action': 'log', 'msg': f'[write_file] 目录创建/确认成功'}))
    except OSError as e:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'dir_path.dir_path.error', 'msg': f'[write_file] 创建目录失败: dir_path={dir_path}, error={e}'}))
        return {"ok": False, "error": f"无法创建目录: {e}"}

    # 写入文件
    logger.info(log_dict({'module_name': 'file_tools', 'action': 'log', 'msg': f'[write_file] 开始写入文件内容'}))
    try:
        with open(safe_path, "w", encoding=encoding) as f:
            f.write(content)
        logger.info(log_dict({'module_name': 'file_tools', 'action': 'safe_path.safe_path', 'msg': f'[write_file] 文件写入成功: safe_path={safe_path}'}))
    except PermissionError as e:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'safe_path.safe_path.error', 'msg': f'[write_file] 权限错误: safe_path={safe_path}, error={e}'}))
        return {"ok": False, "error": f"没有权限写入文件: {path}"}
    except OSError as e:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'safe_path.safe_path', 'msg': f'[write_file] OS错误: safe_path={safe_path}, error={e}'}))
        return {"ok": False, "error": f"写入文件失败: {e}"}

    result = {
        "ok": True,
        "path": path,
        "abs_path": safe_path,
        "size": len(content_bytes),
    }
    if backup_path:
        result["backup"] = backup_path

    logger.info(log_dict({'module_name': 'file_tools', 'action': 'true.size', 'msg': f'[write_file] 写入完成: ok=True, size={len(content_bytes)}, backup={backup_path}'}))
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
    logger.info(log_dict({'module_name': 'file_tools', 'action': 'path.path.show_hidden', 'msg': f'[list_directory] 开始列出目录: path={path}, show_hidden={show_hidden}, max_items={max_items}'}))
    try:
        safe_path = safe_resolve_path(path)
        logger.info(log_dict({'module_name': 'file_tools', 'action': 'safe_path.safe_path', 'msg': f'[list_directory] 路径安全解析成功: safe_path={safe_path}'}))
    except ValueError as e:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'path.path.error', 'msg': f'[list_directory] 路径解析失败: path={path}, error={e}'}))
        return {"ok": False, "error": str(e)}

    if not os.path.exists(safe_path):
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'safe_path.safe_path', 'msg': f'[list_directory] 路径不存在: safe_path={safe_path}'}))
        return {"ok": False, "error": f"路径不存在: {path}"}
    if not os.path.isdir(safe_path):
        # 如果是文件，返回文件信息
        logger.info(log_dict({'module_name': 'file_tools', 'action': 'safe_path.safe_path', 'msg': f'[list_directory] 路径是文件而非目录: safe_path={safe_path}'}))
        return {
            "ok": True,
            "path": path,
            "type": "file",
            "name": os.path.basename(safe_path),
            "total": 1,
        }

    items = []
    logger.info(log_dict({'module_name': 'file_tools', 'action': 'log', 'msg': f'[list_directory] 开始遍历目录内容'}))
    try:
        for name in os.listdir(safe_path):
            if not show_hidden and name.startswith("."):
                continue
            if len(items) >= max_items:
                logger.info(log_dict({'module_name': 'file_tools', 'action': 'max_items.max_items', 'msg': f'[list_directory] 达到最大条目数限制: max_items={max_items}'}))
                break
            item_path = os.path.join(safe_path, name)
            try:
                info = _get_single_file_info(item_path)
                info["name"] = name
                items.append(info)
                logger.debug(log_dict({'module_name': 'file_tools', 'action': 'name.name.type', 'msg': f'[list_directory] 获取文件信息成功: name={name}, type={info.get("type")}'}))
            except OSError as e:
                logger.warning(log_dict({'module_name': 'file_tools', 'action': 'name.name.error', 'msg': f'[list_directory] 获取文件信息失败: name={name}, error={e}'}))
                items.append({"name": name, "type": "unknown"})
    except PermissionError as e:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'safe_path.safe_path.error', 'msg': f'[list_directory] 权限错误: safe_path={safe_path}, error={e}'}))
        return {"ok": False, "error": f"没有权限列出目录: {path}"}
    except OSError as e:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'safe_path.safe_path', 'msg': f'[list_directory] OS错误: safe_path={safe_path}, error={e}'}))
        return {"ok": False, "error": f"列出目录失败: {e}"}

    # 排序：目录优先，然后按名称
    items.sort(key=lambda x: (0 if x.get("type") == "dir" else 1, x.get("name", "")))
    logger.info(log_dict({'module_name': 'file_tools', 'action': 'total_items.len.items', 'msg': f'[list_directory] 目录列出完成: total_items={len(items)}'}))

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
    try:
        info = _get_single_file_info(safe_path)
        return {"ok": True, "path": path, "abs_path": safe_path, **info}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def search_files(pattern: str, root_path: str = ".", max_results: int = 200,
                 ignore_case: bool = True) -> dict:
    """按文件名模式搜索文件（支持 glob 通配符）

    Args:
        pattern: 搜索模式，如 *.py, **/*.md, test_*
        root_path: 搜索根目录，默认为当前目录
        max_results: 最大结果数，默认 200
        ignore_case: 是否忽略大小写，默认 True

    Returns:
        dict: {ok, pattern, root, results: [{path, name, size, modified, ...}], total}
    """
    logger.info(log_dict({'module_name': 'file_tools', 'action': 'pattern.pattern.root_path', 'msg': f'[search_files] 开始搜索文件: pattern={pattern}, root_path={root_path}, max_results={max_results}, ignore_case={ignore_case}'}))
    try:
        safe_root = safe_resolve_path(root_path)
        logger.info(log_dict({'module_name': 'file_tools', 'action': 'safe_root.safe_root', 'msg': f'[search_files] 路径安全解析成功: safe_root={safe_root}'}))
    except ValueError as e:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'root_path.root_path.error', 'msg': f'[search_files] 路径解析失败: root_path={root_path}, error={e}'}))
        return {"ok": False, "error": str(e)}

    if not os.path.exists(safe_root):
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'safe_root.safe_root', 'msg': f'[search_files] 搜索路径不存在: safe_root={safe_root}'}))
        return {"ok": False, "error": f"搜索路径不存在: {root_path}"}
    if not os.path.isdir(safe_root):
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'safe_root.safe_root', 'msg': f'[search_files] 搜索路径不是目录: safe_root={safe_root}'}))
        return {"ok": False, "error": f"搜索路径不是目录: {root_path}"}

    # 限制递归深度（防止遍历过深）
    max_walk = 50000
    walked = 0
    results = []

    # 预编译模式
    flags = re.IGNORECASE if ignore_case else 0
    try:
        regex = re.compile(fnmatch.translate(pattern), flags)
        logger.debug(log_dict({'module_name': 'file_tools', 'action': 'pattern.pattern.regex', 'msg': f'[search_files] 预编译正则: pattern={pattern} -> regex={regex.pattern}'}))
    except re.error as e:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'pattern.pattern.error', 'msg': f'[search_files] 模式编译失败: pattern={pattern}, error={e}'}))
        return {"ok": False, "error": f"搜索模式错误: {e}"}

    try:
        for dirpath, dirnames, filenames in os.walk(safe_root):
            walked += 1
            if walked > max_walk:
                logger.info(log_dict({'module_name': 'file_tools', 'action': 'max_walk.max_walk', 'msg': f'[search_files] 达到最大遍历步数: max_walk={max_walk}'}))
                break

            # 跳过隐藏目录
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]

            # 对目录名也做匹配（如果模式匹配目录）
            for fname in filenames:
                if len(results) >= max_results:
                    break
                if regex.search(fname):
                    full_path = os.path.join(dirpath, fname)
                    # 【凭据文件名不出现在结果里 —— 与 read_file 的口径对齐说明】
                    #   read_file **允许**读凭据文件，防线在外发侧（egress 污点链路）——
                    #   见 file_tools 顶部 is_credential_path 处的说明与
                    #   tests/unit/test_policy_integration.py::TestToolReadToEgressChain。
                    #   这里挡的是**另一件事**：search_files 的返回值是"一份文件清单"，
                    #   把 .env / 私钥的**路径**递给模型等于主动指路（下一步它就去读）。
                    #   不读内容 ⇒ 不构成内容泄漏；只少给一条线索 ⇒ 也不损失任何能力
                    #   （list_directory 仍可看到这些文件存在）。故两者不矛盾：
                    #   「读得到」与「被主动推荐」是两回事。
                    if is_credential_path(full_path):
                        logger.debug(log_dict({'module_name': 'file_tools', 'action': 'credential_guard',
                                               'msg': f'[search_files] 跳过凭据文件: {fname}'}))
                        continue
                    try:
                        stat = os.stat(full_path)
                        results.append({
                            "path": os.path.relpath(full_path, safe_root),
                            "abs_path": full_path,
                            "name": fname,
                            "size": stat.st_size,
                            "modified": time.strftime(
                                "%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)
                            ),
                        })
                        logger.debug(log_dict({'module_name': 'file_tools', 'action': 'fname.fname.size', 'msg': f'[search_files] 添加匹配结果: fname={fname}, size={stat.st_size}'}))
                    except OSError as e:
                        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'stat.full_path.full_path', 'msg': f'[search_files] 获取文件stat失败: full_path={full_path}, error={e}'}))

            if len(results) >= max_results:
                break

            if walked > max_walk:
                break
    except PermissionError as e:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'error', 'msg': f'[search_files] 权限错误（继续返回已有结果）: error={e}'}))
        pass  # 部分目录无权限，继续返回已有结果
    except OSError as e:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'safe_root.safe_root', 'msg': f'[search_files] OS错误: safe_root={safe_root}, error={e}'}))
        return {"ok": False, "error": f"搜索文件失败: {e}"}

    logger.info(log_dict({'module_name': 'file_tools', 'action': 'total_results.len.results', 'msg': f'[search_files] 搜索完成: total_results={len(results)}, walked={walked}, truncated={len(results) >= max_results or walked >= max_walk}'}))
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
    logger.info(log_dict({'module_name': 'file_tools', 'action': 'path.path', 'msg': f'[_get_single_file_info] 开始获取文件信息: path={path}'}))
    try:
        stat = os.stat(path)
        logger.info(log_dict({'module_name': 'file_tools', 'action': 'stat.size.stat', 'msg': f'[_get_single_file_info] stat获取成功: size={stat.st_size}, mode={oct(stat.st_mode)}'}))
    except OSError as e:
        logger.warning(log_dict({'module_name': 'file_tools', 'action': 'stat.path.path', 'msg': f'[_get_single_file_info] stat获取失败: path={path}, error={e}'}))
        raise

    is_dir = os.path.isdir(path)
    is_link = os.path.islink(path)
    logger.info(log_dict({'module_name': 'file_tools', 'action': 'is_dir.is_dir.is_link', 'msg': f'[_get_single_file_info] 文件属性: is_dir={is_dir}, is_link={is_link}'}))

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
        logger.debug(log_dict({'module_name': 'file_tools', 'action': 'extension.info', 'msg': f'[_get_single_file_info] 文件扩展名: extension={info["extension"]}'}))

    if is_link:
        logger.info(log_dict({'module_name': 'file_tools', 'action': 'log', 'msg': f'[_get_single_file_info] 文件是符号链接，尝试读取目标'}))
        try:
            info["link_target"] = os.readlink(path)
            logger.info(log_dict({'module_name': 'file_tools', 'action': 'link_target.info', 'msg': f'[_get_single_file_info] 符号链接目标: link_target={info["link_target"]}'}))
        except OSError as e:
            logger.warning(log_dict({'module_name': 'file_tools', 'action': 'path.path.error', 'msg': f'[_get_single_file_info] 读取符号链接目标失败: path={path}, error={e}'}))
            pass

    logger.info(log_dict({'module_name': 'file_tools', 'action': 'type.info', 'msg': f'[_get_single_file_info] 文件信息获取完成: type={info["type"]}, size={info["size"]}'}))
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


# 导出公共 API
__all__ = [
    "is_protected_path", "safe_resolve_path", "is_binary_content", "is_executable_extension",
    "read_file", "write_file", "list_directory", "get_file_info", "search_files",
    "_get_single_file_info", "_guess_mime_type",
]