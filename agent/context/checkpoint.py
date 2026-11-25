"""会话检查点 + workspace 变更快照（E0-3）。

【解决什么】审计链回答「谁做了什么」，工具 `undo_hint` 回答「单个动作怎么撤」；
本模块回答两件此前没有的事：
1. **本轮改了哪些文件**：每次「会改工作区」的工具成功后（去抖、后台）跑一次
   `git status` + `git diff` + 整文件捕获，落成可读的检查点；
2. **回到某点对照**：`compare(id)` 与当前工作区、或两个检查点之间逐文件比
   （added/removed/modified）。

【为什么不阻塞工具】本机 80 万文件仓库的 `git status` 是秒级：快照在线程里跑，
同进程去抖（默认 120s）且同一时刻只有一个；失败只记 WARNING。

【为什么不加 HTTP 路由】「逐文件 diff / 打开定位」的交互属于 E1-3 变更文件卡；
现在加无 UI 引用的 GET 路由会制造 route_ui_coverage 未引用债。本轮只交付
能力 + CLI（scripts/checkpoint_cli.py）+ 探针。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from agent.utils import proc as _proc

logger = logging.getLogger(__name__)

CHECKPOINT_ENABLED_ENV = "CP_CHECKPOINT_ENABLED"
MAX_FILES_ENV = "CP_CHECKPOINT_MAX_FILES"
MAX_FILE_BYTES_ENV = "CP_CHECKPOINT_MAX_FILE_BYTES"
DEBOUNCE_ENV = "CP_CHECKPOINT_DEBOUNCE_SECONDS"

_MAX_FILES_DEFAULT, _MAX_FILES_MIN, _MAX_FILES_MAX = 50, 1, 500
_MAX_FILE_BYTES_DEFAULT = 1_048_576
_MAX_FILE_BYTES_MIN, _MAX_FILE_BYTES_MAX = 4_096, 16_777_216
_DEBOUNCE_DEFAULT, _DEBOUNCE_MIN, _DEBOUNCE_MAX = 120.0, 10.0, 3_600.0
_DIFF_MAX_BYTES = 2_097_152
_GIT_TIMEOUT_SEC = 60.0

#: 会改工作区的工具（静态表：不加载 YAML 元数据，避免每次调用多一跳）
MUTATING_TOOLS = frozenset({
    "write_file", "edit", "apply_patch", "workspace_write", "workspace_delete",
    "compress", "decompress", "git", "run_program", "shell_execute",
    "software_install", "schedule_task", "skill_script_run",
})

#: 快照**不收录**的运行期/临时路径（前缀匹配，正斜杠归一）
_IGNORED_PREFIXES = (
    "data/",                    # 运行期状态与台账（_ALLOW_IN_DATA 里放行工具/主线声明）
    "agent/data/", "agent/tools/custom/", "agent/tools/byted_", "static/assets/",
    "test_reports/", "_ci_logs/", "_scratch/", ".worktrees/", ".pytest_tmp/",
    ".file_backups/", ".local/", ".ui_shots/", ".ciart", "rag/", "对话导出",
    "_ci_watch", ".ci_watch", "node_modules/",
)

#: `data/` 下属于源码/声明的子目录（要进快照）
_ALLOW_IN_DATA = ("data/tool_definitions/", "data/agent_lines/")

_lock = threading.Lock()
_state: Dict[str, Any] = {"last_ts": 0.0, "running": False, "last_id": ""}


# ── 开关/上界 ────────────────────────────────────────────────


def enabled() -> bool:
    raw = str(os.environ.get("CP_CHECKPOINT_ENABLED", "1") or "").strip().lower()
    return raw not in ("0", "false", "no", "off", "disable", "disabled")


def _clamp_int(raw: Any, default: int, lo: int, hi: int, name: str) -> int:
    text = str(raw or "").strip()
    if not text:
        return default
    try:
        value = int(float(text))
    except (TypeError, ValueError):
        logger.warning("[checkpoint] %s=%r 不是整数，回落到 %s", name, text, default)
        return default
    return max(lo, min(hi, value))


def max_files() -> int:
    # 逐条字面量读取（settings 零缺口门禁不接受经函数参数传递的动态读取）
    return _clamp_int(os.environ.get("CP_CHECKPOINT_MAX_FILES"), _MAX_FILES_DEFAULT,
                      _MAX_FILES_MIN, _MAX_FILES_MAX, "CP_CHECKPOINT_MAX_FILES")


def max_file_bytes() -> int:
    return _clamp_int(os.environ.get("CP_CHECKPOINT_MAX_FILE_BYTES"),
                      _MAX_FILE_BYTES_DEFAULT, _MAX_FILE_BYTES_MIN, _MAX_FILE_BYTES_MAX,
                      "CP_CHECKPOINT_MAX_FILE_BYTES")


def debounce_seconds() -> float:
    raw = str(os.environ.get("CP_CHECKPOINT_DEBOUNCE_SECONDS", "") or "").strip()
    if not raw:
        return _DEBOUNCE_DEFAULT
    try:
        return max(_DEBOUNCE_MIN, min(_DEBOUNCE_MAX, float(raw)))
    except (TypeError, ValueError):
        return _DEBOUNCE_DEFAULT


def workspace_root() -> Path:
    return Path(__file__).resolve().parents[2]


def checkpoint_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "checkpoints"


# ── git 基础 ─────────────────────────────────────────────────


def _git(root: Path, args: Iterable[str], *, timeout: float = _GIT_TIMEOUT_SEC) -> Tuple[int, bytes]:
    try:
        proc = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                              timeout=timeout, **_proc.hidden_kwargs())
        return int(proc.returncode), proc.stdout or b""
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("[checkpoint] git %s 失败: %s", list(args), type(exc).__name__)
        return 1, b""


def _is_git_worktree(root: Path) -> bool:
    rc, out = _git(root, ["rev-parse", "--is-inside-work-tree"])
    return rc == 0 and out.strip().lower() == b"true"


def head_commit(root: Path) -> str:
    rc, out = _git(root, ["rev-parse", "--short=12", "HEAD"])
    return out.decode("utf-8", "replace").strip() if rc == 0 else ""


def _norm_rel(path: str) -> str:
    # 只去掉开头的 ./，**不能** lstrip("./")：那会把 `.local/x` 变成 `local/x`（过滤失效）
    rel = path.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.lstrip("/")


def _ignored(rel: str) -> bool:
    """运行期/临时路径挡掉；`data/` 下的工具与主线声明放行。"""
    if any(rel.startswith(prefix) for prefix in _ALLOW_IN_DATA):
        return False
    return any(rel.startswith(prefix) for prefix in _IGNORED_PREFIXES)


def _parse_status(root: Path) -> List[Dict[str, str]]:
    """`git status --porcelain -z` → [{status, path}]（只留源码类改动）。

    - tracked 改动（M/A/D/R）按 `_ignored` 过滤；
    - 未跟踪（??）**只收源码类目录**（agent/tests/scripts/docs/…）：仓库里
      `.local/`、`.ui_shots/`、`rag/` 这类未跟踪目录数以千计，全收会把快照变成噪声。
    """
    rc, out = _git(root, ["status", "--porcelain", "-z", "--untracked-files=normal"])
    if rc != 0:
        return []
    entries: List[Dict[str, str]] = []
    parts = out.split(b"\0")
    index = 0
    while index < len(parts):
        chunk = parts[index]
        index += 1
        if not chunk or len(chunk) < 4:
            continue
        code = chunk[:2].decode("utf-8", "replace")
        raw = chunk[3:].decode("utf-8", "replace")
        if code.startswith("R") and index < len(parts):
            # 重命名：-z 形态是「新路径\0旧路径\0」
            index += 1
        rel = _norm_rel(raw)
        if not rel:
            continue
        if _ignored(rel):
            continue
        entries.append({"status": code.strip() or "?", "path": rel})
    return entries


# ── 快照 ─────────────────────────────────────────────────────


def _new_id() -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"{stamp}-{secrets.token_hex(4)}"


def _read_capture(root: Path, rel: str) -> Tuple[bool, bool, int, str, bytes]:
    """返回 (captured, binary, size, sha256, data)。超过上限/二进制不捕获。"""
    path = root / rel
    try:
        size = path.stat().st_size
        if size > max_file_bytes():
            return False, False, int(size), "", b""
        data = path.read_bytes()
    except OSError:
        return False, False, 0, "", b""
    if b"\x00" in data[:8192]:
        return False, True, len(data), hashlib.sha256(data).hexdigest(), b""
    return True, False, len(data), hashlib.sha256(data).hexdigest(), data


def snapshot(*, trigger_tool: str = "manual", note: str = "", session_id: str = "",
             root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """抓一次 workspace 变更快照；非 git 仓库/无改动返回 None。"""
    if not enabled():
        return None
    work_root = Path(root) if root else workspace_root()
    if not _is_git_worktree(work_root):
        logger.warning("[checkpoint] 不是 git 工作区，跳过快照: %s", work_root)
        return None
    status = _parse_status(work_root)
    if not status:
        return None
    limit_files = max_files()
    changed: List[Dict[str, Any]] = []
    captured_data: Dict[str, bytes] = {}
    truncated = len(status) > limit_files
    for entry in status[:limit_files]:
        rel = entry["path"]
        captured, binary, size, digest, data = _read_capture(work_root, rel)
        changed.append({
            "path": rel,
            "status": entry["status"],
            "size": size,
            "sha256_16": digest[:16] if digest else "",
            "captured": captured,
            "binary": binary,
        })
        if captured:
            captured_data[rel] = data
    diff_bytes = 0
    diff_truncated = False
    rc, diff_raw = _git(work_root, ["diff", "--no-color", "--no-ext-diff", "--", "."])
    if rc == 0:
        diff_bytes = len(diff_raw)
        diff_truncated = diff_bytes > _DIFF_MAX_BYTES
        if diff_truncated:
            diff_raw = diff_raw[:_DIFF_MAX_BYTES]

    checkpoint_id = _new_id()
    target = checkpoint_dir() / checkpoint_id
    (target / "files").mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "yunshu/workspace-checkpoint/v1",
        "id": checkpoint_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "trigger_tool": str(trigger_tool),
        "note": str(note or ""),
        # 触发时的会话（E1-3b 变更文件卡据此标注 scoped）；拿不到就是空串
        "session_id": str(session_id or ""),
        "workspace_root": str(work_root),
        "git_head": head_commit(work_root),
        "changed": changed,
        "changed_total": len(status),
        "files_captured": len(captured_data),
        "diff_bytes": diff_bytes,
        "diff_truncated": diff_truncated,
        "truncated": truncated,
        "limits": {"max_files": limit_files, "max_file_bytes": max_file_bytes()},
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    (target / "status.txt").write_text(
        "\n".join(f"{e['status']} {e['path']}" for e in status), encoding="utf-8")
    (target / "diff.patch").write_bytes(diff_raw)
    for rel, data in captured_data.items():
        dest = target / "files" / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        except OSError:
            logger.debug("[checkpoint] 捕获写入失败: %s", rel)
    logger.info("[checkpoint] 快照 %s：%d 个改动（捕获 %d 个文件，diff %d 字节）",
                checkpoint_id, len(status), len(captured_data), diff_bytes)
    _state["last_id"] = checkpoint_id
    return manifest


def maybe_snapshot(trigger_tool: str) -> bool:
    """工具成功后调用：符合条件则**后台**抓一次（去抖 + 单飞）。返回是否已排入。"""
    if not enabled() or str(trigger_tool) not in MUTATING_TOOLS:
        return False
    now = time.time()
    with _lock:
        if _state["running"]:
            return False
        if now - float(_state["last_ts"]) < debounce_seconds():
            return False
        _state["running"] = True
        _state["last_ts"] = now
    # 会话绑定是 ContextVar：后台线程里读不到发起方的值，必须在**排入前**取
    try:
        from agent.context.session_bind import current as _bound_session

        session_id = _bound_session()
    except Exception:  # noqa: BLE001 拿不到就按空串记（不阻塞快照）
        session_id = ""

    def _run() -> None:
        try:
            snapshot(trigger_tool=str(trigger_tool), session_id=session_id)
        except Exception as exc:  # noqa: BLE001  快照是旁路
            logger.warning("[checkpoint] 快照失败: %s", type(exc).__name__)
        finally:
            with _lock:
                _state["running"] = False

    threading.Thread(target=_run, name="checkpoint-snapshot", daemon=True).start()
    return True


def reset_state() -> None:
    """复位去抖状态（测试/探针用）。"""
    with _lock:
        _state.update({"last_ts": 0.0, "running": False, "last_id": ""})


# ── 读取 / 对比 ──────────────────────────────────────────────

_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")


def _resolve(checkpoint_id: str) -> Optional[Path]:
    raw = str(checkpoint_id or "").strip()
    if not _ID_RE.match(raw):
        return None
    target = checkpoint_dir() / raw
    return target if (target / "manifest.json").is_file() else None


def list_checkpoints(limit: int = 20) -> List[Dict[str, Any]]:
    directory = checkpoint_dir()
    if not directory.is_dir():
        return []
    rows = []
    for path in sorted(directory.iterdir(), reverse=True):
        manifest = path / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows.append({
            "id": data.get("id") or path.name,
            "created_at": data.get("created_at", ""),
            "trigger_tool": data.get("trigger_tool", ""),
            "changed": data.get("changed_total", 0),
            "captured": data.get("files_captured", 0),
            "truncated": bool(data.get("truncated") or data.get("diff_truncated")),
        })
        if len(rows) >= max(1, int(limit)):
            break
    return rows


def load_manifest(checkpoint_id: str) -> Optional[Dict[str, Any]]:
    target = _resolve(checkpoint_id)
    if target is None:
        return None
    try:
        return json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_file(checkpoint_id: str, rel_path: str) -> Optional[str]:
    """读检查点里**捕获过**的文件内容（只接受清单里出现过的相对路径）。"""
    manifest = load_manifest(checkpoint_id)
    if manifest is None:
        return None
    rel = _norm_rel(str(rel_path or ""))
    known = {entry.get("path") for entry in manifest.get("changed") or []
             if entry.get("captured")}
    if rel not in known:
        return None
    target = _resolve(checkpoint_id)
    if target is None:
        return None
    path = target / "files" / rel
    try:
        if path.resolve().parent != (target / "files" / Path(rel).parent).resolve():
            return None
    except OSError:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def compare(checkpoint_id: str, against: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """逐文件对比：默认与**当前工作区**；给 against 则与另一个检查点。"""
    base = load_manifest(checkpoint_id)
    if base is None:
        return None
    before = {e["path"]: e for e in base.get("changed") or []}
    if against:
        other = load_manifest(against)
        if other is None:
            return None
        after = {e["path"]: e for e in other.get("changed") or []}
    else:
        root = Path(base.get("workspace_root") or workspace_root())
        after = {e["path"]: {"sha256_16": "", "status": e["status"]}
                 for e in _parse_status(root)}
        for rel, entry in after.items():
            captured, binary, size, digest, _data = _read_capture(root, rel)
            entry["sha256_16"] = digest[:16] if digest else ""
            entry["size"] = size
            entry["binary"] = binary
    rows = []
    for rel in sorted(set(before) | set(after)):
        old = before.get(rel)
        new = after.get(rel)
        old_gone = bool(old) and "D" in str(old.get("status") or "") and not old.get("sha256_16")
        new_gone = bool(new) and "D" in str(new.get("status") or "") and not new.get("sha256_16")
        if old and not old_gone and (not new or new_gone):
            state = "removed"
        elif new and not new_gone and (not old or old_gone):
            state = "added"
        elif (old.get("sha256_16") or "") == (new.get("sha256_16") or "") \
                and (old.get("sha256_16") or ""):
            state = "unchanged"
        else:
            state = "modified"
        rows.append({"path": rel, "state": state,
                     "before": old.get("sha256_16") if old else "",
                     "after": new.get("sha256_16") if new else ""})
    return {
        "checkpoint": base.get("id"),
        "against": against or "workdir",
        "rows": rows,
        "added": sum(1 for r in rows if r["state"] == "added"),
        "removed": sum(1 for r in rows if r["state"] == "removed"),
        "modified": sum(1 for r in rows if r["state"] == "modified"),
        "unchanged": sum(1 for r in rows if r["state"] == "unchanged"),
    }


def status() -> Dict[str, Any]:
    directory = checkpoint_dir()
    total = 0
    if directory.is_dir():
        total = len([p for p in directory.iterdir() if (p / "manifest.json").is_file()])
    return {
        "enabled": enabled(),
        "checkpoints": total,
        "last_id": _state.get("last_id") or "",
        "max_files": max_files(),
        "max_file_bytes": max_file_bytes(),
        "debounce_seconds": debounce_seconds(),
        "dir": str(directory),
        "mutating_tools": sorted(MUTATING_TOOLS),
        "ignored_prefixes": list(_IGNORED_PREFIXES),
    }


def purge(checkpoint_id: str) -> bool:
    """删除一个检查点（运维/探针清理用；不在主链路）。"""
    target = _resolve(checkpoint_id)
    if target is None:
        return False
    shutil.rmtree(target, ignore_errors=True)
    return True


# ── 逐文件 diff（E1-3b 变更文件卡） ───────────────────────────

#: 单个文件的 diff 上限与整份响应上限（字符数；变更文件卡是"看个大概"，不是完整补丁）
_DIFF_PER_FILE = 20_000
_DIFF_TOTAL = 200_000
_DIFF_HEADER_RE = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")


def parse_diff_by_file(text: str) -> Dict[str, str]:
    """把 `git diff` 全文按文件切块；键 = b 侧相对路径（正斜杠归一）。"""
    chunks: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in str(text or "").splitlines(keepends=True):
        match = _DIFF_HEADER_RE.match(line.rstrip("\r\n"))
        if match:
            current = _norm_rel(match.group("b"))
            chunks.setdefault(current, [])
        if current is not None:
            chunks[current].append(line)
    return {path: "".join(lines) for path, lines in chunks.items()}


def count_diff_lines(diff: str) -> Tuple[int, int]:
    """(added, removed)：不计 `+++` / `---` 头部。"""
    added = removed = 0
    for line in str(diff or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def changes(checkpoint_id: str = "", *, max_chars_per_file: int = 0,
            max_total_chars: int = 0) -> Optional[Dict[str, Any]]:
    """某次检查点的逐文件变更（最新一次 checkpoint_id 为空时取最近）。

    Returns:
        {"checkpoint": {...摘要...}, "files": [{path,status,size,captured,binary,
         new_file,added,removed,diff,truncated}], "truncated": bool}
        检查点不存在/没有检查点 → None
    """
    target: Optional[Path] = None
    manifest = None
    if checkpoint_id:
        # 指定了 id 就必须是**存在的那一份**：找不到就返回 None（路由回 404），
        # **不能**回落到"最近一次" —— 那会把「id 打错」伪装成成功。
        target = _resolve(checkpoint_id)
        if target is None:
            return None
        manifest = load_manifest(checkpoint_id)
    else:
        rows = list_checkpoints(1)
        if rows:
            manifest = load_manifest(rows[0]["id"])
            target = _resolve(str(rows[0]["id"]))
    if manifest is None or target is None:
        return None

    per_file = max(1_000, int(max_chars_per_file or _DIFF_PER_FILE))
    total_cap = max(1_000, int(max_total_chars or _DIFF_TOTAL))
    try:
        diff_text = (target / "diff.patch").read_text(encoding="utf-8", errors="replace")
    except OSError:
        diff_text = ""
    by_file = parse_diff_by_file(diff_text)

    files: List[Dict[str, Any]] = []
    used = 0
    truncated_any = False
    for entry in manifest.get("changed") or []:
        rel = _norm_rel(str(entry.get("path") or ""))
        if not rel:
            continue
        status = str(entry.get("status") or "")
        raw = by_file.get(rel, "")
        added, removed = count_diff_lines(raw)
        piece = raw
        truncated = False
        if len(piece) > per_file:
            piece = piece[:per_file] + "\n…（本文件 diff 已截断）\n"
            truncated = True
        if used + len(piece) > total_cap:
            piece = ""
            truncated = True
        used += len(piece)
        truncated_any = truncated_any or truncated
        files.append({
            "path": rel,
            "status": status,
            "size": int(entry.get("size") or 0),
            "captured": bool(entry.get("captured")),
            "binary": bool(entry.get("binary")),
            # 未跟踪/新增文件在 `git diff` 里没有块（内容已整文件捕获）
            "new_file": status.startswith("?") or status.startswith("A"),
            "added": added,
            "removed": removed,
            "diff": piece,
            "truncated": truncated,
        })
    return {
        "checkpoint": {
            "id": manifest.get("id", ""),
            "created_at": manifest.get("created_at", ""),
            "trigger_tool": manifest.get("trigger_tool", ""),
            "session_id": manifest.get("session_id", ""),
            "git_head": manifest.get("git_head", ""),
            "changed_total": int(manifest.get("changed_total") or 0),
            "files_captured": int(manifest.get("files_captured") or 0),
            "diff_truncated": bool(manifest.get("diff_truncated")),
            "truncated": bool(manifest.get("truncated")),
        },
        "files": files,
        "truncated": truncated_any,
    }


__all__ = [
    "CHECKPOINT_ENABLED_ENV", "MAX_FILES_ENV", "MAX_FILE_BYTES_ENV", "DEBOUNCE_ENV",
    "MUTATING_TOOLS", "enabled", "max_files", "max_file_bytes", "debounce_seconds",
    "workspace_root", "checkpoint_dir", "snapshot", "maybe_snapshot", "reset_state",
    "list_checkpoints", "load_manifest", "read_file", "compare", "status", "purge",
    "parse_diff_by_file", "count_diff_lines", "changes",
]







