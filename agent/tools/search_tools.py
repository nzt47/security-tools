"""工具注册模块 — 内容检索（grep）与精准编辑（edit）

补齐既有工具链的两处缺口：

- ``grep``：按**内容**（正则）检索文件。既有 ``search_files`` 只按**文件名** glob，
  想知道"哪一行用了某个符号"只能把目录整个读一遍。
- ``edit``：按 ``old_string → new_string`` **局部替换**。既有唯一写路径 ``write_file``
  是整文件覆盖，改一行也要重写全文，既费 token 又容易丢内容。

【不易】返回结构与既有工具一致：成功 ``{"ok": True, ...}``、失败
        ``{"ok": False, "error": ...}``；``edit`` 的**读前置**（先 read_file 才能改）
        与**唯一性**（old_string 不唯一即拒绝）是成功率关键，不放宽。
【变易】忽略目录复用 ``file_monitor`` 的既有口径；二进制/超大文件自动跳过。
【简易】纯 ``re`` + ``os.walk``，不调 shell；不写审计、不建备份（由上层闸门负责）。
"""
import fnmatch
import logging
import os
import re

from agent import tools as _tools

logger = logging.getLogger(__name__)

#: 单个文件的检索上限（字节）：超过即跳过，避免误入日志/数据大文件把检索拖垮
_MAX_FILE_BYTES = 2 * 1024 * 1024
#: grep 结果条数的默认上限
_DEFAULT_MAX_RESULTS = 200
#: 二进制嗅探字节数（只看开头，不为判类型多读一遍文件）
_BINARY_SNIFF_BYTES = 8192
#: 报"不唯一"时最多回显的出现行号个数
_MAX_REPORTED_LINES = 5
#: file_monitor 不可用时的最小兜底忽略集合（正常路径下不复用这一份）
_FALLBACK_IGNORE_DIRS = frozenset({
    ".git", "venv", "__pycache__", "node_modules", ".mypy_cache",
    ".ruff_cache", ".pytest_cache", "htmlcov", "data",
})
#: 本工具在 file_monitor 清单之外额外剪枝的目录名（仓库根 data/ 是运行期大数据目录）
_EXTRA_IGNORE_DIRS = {"data"}

#: 已被本进程 read_file 成功读过的文件（规范化绝对路径）
_READ_FILES: set[str] = set()


# ════════════════════════════════════════════════════════════
#  读前置登记 — edit 要求"先读后改"
# ════════════════════════════════════════════════════════════

def _norm_path(path) -> str:
    """路径规范化：绝对路径 + 大小写归一（同一文件的不同写法必须等价）"""
    return os.path.normcase(os.path.abspath(str(path or "")))


def note_file_read(path) -> None:
    """登记"该文件已被本进程读取过"（由 ``read_file`` 读成功后调用）

    ``edit`` 的读前置校验依赖本登记：先看见原文再改，避免模型凭记忆盲改。
    纯旁路：登记失败绝不影响读取本身。
    """
    try:
        if path:
            _READ_FILES.add(_norm_path(path))
    except Exception:  # noqa: BLE001 登记失败不影响调用方
        pass


def has_been_read(path) -> bool:
    """该文件是否已被本进程读取过（``edit`` 的前置条件）"""
    try:
        return _norm_path(path) in _READ_FILES
    except Exception:  # noqa: BLE001 规范化失败一律视为未读
        return False


# ════════════════════════════════════════════════════════════
#  grep — 目录剪枝、过滤与文本读取
# ════════════════════════════════════════════════════════════

def _ignore_dir_names() -> set:
    """检索时剪枝的目录名集合（**复用 file_monitor 的既有清单**，不另立第二份）

    ``file_monitor.py``（仓库根的 Flask 监控服务）的 ``EXCLUDE_DIR_NAMES`` 是本仓
    既有的"扫描时剪枝"口径，这里直接复用，避免出现需要人工同步的两份清单。

    两点处理：
    - **惰性导入**：该模块导入期会执行 ``logging.basicConfig(force=True)``，会清空
      宿主进程根日志器既有 handler；导入后立即还原，保证工具调用不顺手改写日志配置。
    - 清单不可用（模块缺失等）时退回 ``_FALLBACK_IGNORE_DIRS``，行为不降级。
    """
    try:
        root_logger = logging.getLogger()
        saved_handlers = list(root_logger.handlers)
        import file_monitor  # noqa: PLC0415 惰性导入：规避其模块级日志副作用
        if list(root_logger.handlers) != saved_handlers:
            root_logger.handlers[:] = saved_handlers
        dirs = set(file_monitor.EXCLUDE_DIR_NAMES)
    except Exception:  # noqa: BLE001 清单不可用时退回兜底集合
        dirs = set(_FALLBACK_IGNORE_DIRS)
    return dirs | _EXTRA_IGNORE_DIRS


def _expand_braces(pattern: str) -> list:
    """展开 glob 花括号：``*.{md,txt}`` → ``*.md`` / ``*.txt``（与 shell 习惯一致）"""
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    expanded = []
    for alt in match.group(1).split(","):
        expanded.extend(_expand_braces(pattern[:match.start()] + alt + pattern[match.end():]))
    return expanded


def _parse_includes(include) -> list:
    """解析 include 过滤项：先展开花括号再按逗号拆分（``*.{md,txt}``、``*.py,*.md`` 都支持）"""
    if not include:
        return []
    patterns: list = []
    for expanded in _expand_braces(str(include)):
        patterns.extend(p.strip() for p in expanded.split(",") if p.strip())
    return patterns


def _match_include(rel_path: str, name: str, patterns: list) -> bool:
    """文件是否命中 include 过滤（无过滤项时全命中）

    带路径分隔符的模式按相对路径匹配，否则按文件名匹配 —— 与 ``grep --include``
    一致，即 ``*.py`` 能命中任意深度的 .py 文件。
    """
    if not patterns:
        return True
    rel_posix = rel_path.replace(os.sep, "/")
    for pattern in patterns:
        normalized = pattern.replace("\\", "/")
        target = rel_posix if "/" in normalized else name
        if fnmatch.fnmatch(target, normalized):
            return True
    return False


def _looks_binary(raw: bytes) -> bool:
    """是否二进制：开头（8KB 内）含 NUL 字节即视为二进制

    这里**刻意不**复用 ``agent.tools.file_tools.is_binary_content``：它的
    "非文本字符占比 < 0.85" 判据会把中文为主的 UTF-8 文本判成二进制
    （中文字节均 >= 0x80），会让中文内容检索静默漏检。
    """
    return b"\x00" in raw[:_BINARY_SNIFF_BYTES]


def _read_search_text(full: str):
    """读取用于检索的文本；二进制/超大/读不动 → None（调用方跳过该文件）"""
    try:
        if os.path.getsize(full) > _MAX_FILE_BYTES:
            return None
        with open(full, "rb") as f:
            raw = f.read()
    except OSError:  # 权限/占用/竞态删除 → 跳过该文件，不整体失败
        return None
    if _looks_binary(raw):
        return None
    for encoding in ("utf-8-sig", "gbk"):  # utf-8-sig 顺带吃掉可能的 BOM
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    logger.debug("grep 跳过无法解码的文件: %s", full)
    return None


def grep_content(pattern, path=".", include=None, max_results=_DEFAULT_MAX_RESULTS,
                 case_sensitive=False, context_lines=0) -> dict:
    """内容检索实现体（由 ``grep`` 工具包装）

    逐行正则匹配；命中达到 ``max_results`` 立即停止并把 ``truncated`` 置 True。
    ``path`` 指向文件时只检索该文件；指向目录时递归检索并按忽略清单剪枝。

    Returns:
        ``{"ok": True, "matches": [{"path", "line", "text"}], "count", "truncated"}``；
        ``context_lines > 0`` 时每条命中额外带 ``"context"``（含命中行的上下文块，
        元素为 ``{"line", "text"}``）。非法正则/路径不存在返回 ``{"ok": False, "error"}``。
    """
    if not pattern:
        return {"ok": False, "error": "请提供搜索模式（pattern）"}
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return {"ok": False, "error": f"正则表达式无效: {e}"}

    root = str(path or ".")
    if not os.path.exists(root):
        return {"ok": False, "error": f"搜索路径不存在: {root}"}

    try:
        limit = int(max_results)
    except (TypeError, ValueError):
        limit = _DEFAULT_MAX_RESULTS
    if limit <= 0:
        limit = _DEFAULT_MAX_RESULTS
    try:
        ctx = max(0, int(context_lines))
    except (TypeError, ValueError):
        ctx = 0
    patterns = _parse_includes(include)

    matches: list[dict] = []
    truncated = False

    def _scan_file(full: str, rel: str) -> bool:
        """扫描单个文件；返回 False 表示已达上限、需停止整体检索"""
        nonlocal truncated
        text = _read_search_text(full)
        if text is None:
            return True  # 二进制/超大/读不动 → 跳过该文件，不整体失败
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            if not regex.search(line):
                continue
            entry = {"path": rel, "line": idx + 1, "text": line}
            if ctx:
                lo = max(0, idx - ctx)
                hi = min(len(lines), idx + ctx + 1)
                entry["context"] = [{"line": n + 1, "text": lines[n]} for n in range(lo, hi)]
            matches.append(entry)
            if len(matches) >= limit:
                truncated = True
                return False
        return True

    if os.path.isfile(root):
        name = os.path.basename(root)
        if _match_include(name, name, patterns):
            _scan_file(root, name)
    else:
        ignore_dirs = _ignore_dir_names()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in ignore_dirs)
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root)
                if not _match_include(rel, name, patterns):
                    continue
                if not _scan_file(full, rel):
                    break
            if truncated:
                break

    return {"ok": True, "matches": matches, "count": len(matches), "truncated": truncated}


# ════════════════════════════════════════════════════════════
#  edit — 编码/换行保真读写与出现次数统计
# ════════════════════════════════════════════════════════════

def _detect_encoding(raw: bytes) -> str:
    """按 BOM 与可解码性判定编码：写回沿用同一编码 → **BOM 不增不减**"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"  # 读时去 BOM，写回时自动补回
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return "utf-16"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "gbk"


def _read_source(path: str):
    """读取原文件 → ``(内容, 编码)``

    以二进制读入、按判定出的编码解码，替换后仍以同一编码二进制写回，因此
    **BOM 与 CRLF/LF 逐字节保真**（本仓有 BOM/换行门禁：不得新增或删除 BOM，
    也不得把 CRLF 改成 LF）。
    """
    with open(path, "rb") as f:
        raw = f.read()
    encoding = _detect_encoding(raw)
    return raw.decode(encoding), encoding


def _write_source(path: str, content: str, encoding: str) -> int:
    """以原编码二进制写回，返回写入字节数（BOM/换行随内容原样落盘）"""
    data = content.encode(encoding)
    with open(path, "wb") as f:
        f.write(data)
    return len(data)


def _occurrence_offsets(content: str, needle: str) -> list:
    """needle 在 content 中的全部起始偏移（左到右，不重叠）"""
    offsets: list = []
    start = 0
    while True:
        idx = content.find(needle, start)
        if idx < 0:
            return offsets
        offsets.append(idx)
        start = idx + len(needle)


def _line_of(content: str, offset: int) -> int:
    """字符偏移 → 1-based 行号"""
    return content.count("\n", 0, offset) + 1


def edit_content(path, old_string, new_string, replace_all=False, dl=None) -> dict:
    """精准编辑实现体（由 ``edit`` 工具包装）

    校验顺序：文件存在 → old/new 合法性 → **读前置** → 权限 → 内容安全 → 出现次数。
    文件是否存在提前判，是为了让报错指向真实原因（而不是先弹"请先读取"）。

    Args:
        dl: DigitalLife 实例（取其 ``_permission`` 做权限/内容安全校验）；
            为 None 时不校验（仅供内部与测试直调）。

    Returns:
        成功 ``{"ok": True, "path", "replaced", "bytes_written"}``；
        失败 ``{"ok": False, "error", ...}``，权限/读前置拒绝额外带 ``"blocked": True``。
    """
    if not path:
        return {"ok": False, "error": "请提供文件路径（path）"}
    if not os.path.exists(path):
        return {"ok": False, "error": f"文件不存在: {path}"}
    if not os.path.isfile(path):
        return {"ok": False, "error": f"路径不是文件: {path}"}
    if old_string == new_string:
        return {"ok": False, "error": "old_string 与 new_string 相同，无需修改"}
    if not old_string:
        return {"ok": False, "error": "old_string 不能为空"}

    # 读前置校验：必须先 read_file 看见原文，再改（防止凭记忆盲改）
    if not has_been_read(path):
        return {"ok": False, "error": "编辑前必须先读取该文件（请先调用 read_file）", "blocked": True}

    # 安全检查：通过 PermissionSystem 校验（与 write_file 同模式）
    if dl is not None:
        permission = getattr(dl, "_permission", None)
        if permission:
            perm = permission.check_action(f"edit_file:{path}", f"编辑文件 {path}")
            if not perm.allowed:
                return {"ok": False, "error": f"权限系统拒绝: {perm.reason}", "blocked": True}
            try:
                check = permission.check_text(new_string)
                if check.get("level") == "critical":
                    return {
                        "ok": False,
                        "error": f"内容安全检查未通过: {[m.get('description', '') for m in check.get('matches', [])]}",
                        "blocked": True,
                    }
            except Exception:
                pass

    try:
        content, encoding = _read_source(path)
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"读取文件失败: {e}"}

    offsets = _occurrence_offsets(content, old_string)
    if not offsets:
        return {"ok": False, "error": "未找到待替换内容（old_string 在文件中不存在）", "occurrences": 0}
    if len(offsets) > 1 and not replace_all:
        return {
            "ok": False,
            "error": f"old_string 在文件中出现 {len(offsets)} 次，不唯一。"
                     "请扩大上下文使其唯一，或设置 replace_all=true",
            "occurrences": len(offsets),
            "lines": [_line_of(content, off) for off in offsets[:_MAX_REPORTED_LINES]],
        }

    replaced = len(offsets) if replace_all else 1
    if replace_all:
        new_content = content.replace(old_string, new_string)
    else:
        new_content = content.replace(old_string, new_string, 1)
    try:
        written = _write_source(path, new_content, encoding)
    except (OSError, UnicodeEncodeError) as e:
        return {"ok": False, "error": f"写入文件失败: {e}"}
    logger.info("edit 已写回: %s（替换 %d 处，%d 字节）", path, replaced, written)
    return {"ok": True, "path": path, "replaced": replaced, "bytes_written": written}


# ════════════════════════════════════════════════════════════
#  工具注册
# ════════════════════════════════════════════════════════════

def register_all(dl):
    """注册内容检索与精准编辑工具

    Args:
        dl: DigitalLife 实例（用于访问 self 属性与权限系统）
    """

    @_tools.register("grep", "按内容（正则表达式）检索文件，返回命中的相对路径、1-based 行号与该行原文。"
                             "自动跳过 .git/data/node_modules 等大目录、二进制文件与超过 2MB 的文件。"
                             "Search file contents by regular expression, search in files, grep", schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式（必填），如 \\bdef\\s+compose\\b"},
            "path": {"type": "string", "description": "搜索根路径，默认当前工作目录"},
            "include": {"type": "string", "description": "文件名 glob 过滤，如 *.py 或 *.{md,txt}"},
            "max_results": {"type": "integer", "description": "结果条数上限，默认 200"},
            "case_sensitive": {"type": "boolean", "description": "是否大小写敏感，默认 false"},
            "context_lines": {"type": "integer", "description": "每命中行附加上下文的行数，默认 0"},
        },
        "required": ["pattern"],
    })
    def _grep(**kwargs):
        """内容检索入口（参数见 schema）"""
        return grep_content(
            pattern=kwargs.get("pattern", ""),
            path=kwargs.get("path") or ".",
            include=kwargs.get("include") or "",
            max_results=kwargs.get("max_results", _DEFAULT_MAX_RESULTS),
            case_sensitive=bool(kwargs.get("case_sensitive", False)),
            context_lines=kwargs.get("context_lines", 0),
        )

    @_tools.register("edit", "对文件做精准局部替换（old_string → new_string），只改指定片段、不重写全文；"
                             "old_string 必须在文件中唯一（除非 replace_all=true）。必须先调用 read_file 读过该文件。"
                             "Edit a file by replacing an exact string, replace text in a file", schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（必填）"},
            "old_string": {"type": "string", "description": "待替换的原文（必填），需在文件中唯一"},
            "new_string": {"type": "string", "description": "替换后的内容（必填）"},
            "replace_all": {"type": "boolean", "description": "是否替换全部匹配，默认 false（要求 old_string 唯一）"},
        },
        "required": ["path", "old_string", "new_string"],
    })
    def _edit(**kwargs):
        """精准编辑入口（参数见 schema）"""
        return edit_content(
            path=kwargs.get("path") or "",
            old_string=kwargs.get("old_string") or "",
            new_string=kwargs.get("new_string") or "",
            replace_all=bool(kwargs.get("replace_all", False)),
            dl=dl,
        )
