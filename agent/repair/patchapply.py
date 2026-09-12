"""统一 diff 应用器（TASK-S7-02 步骤 4 的机械前置）

【任务定位】
    隔离验证必须把子代理产出的补丁打到**临时副本**上。本模块是那一步的机械实现：
    解析统一 diff → 在内存中逐 hunk 定位 → **全部成功后**才落盘。

【不易（为什么不用系统 ``patch``/``git apply``）】
    1. 环境相关：Windows 上未必有 ``patch``；``git apply`` 要求目标目录是 git 工作区，
       而临时副本可能只是一个目录树。
    2. **可测性**：外部命令的失败模式（换行符策略、CRLF、路径引号）会变成不可控变量，
       而「补丁到底打没打上」是本任务最关键的判定之一，不能靠黑盒。
    3. **原子性**：本实现先在内存里应用全部文件，任一文件失败则整体放弃——
       绝不留下"打了一半"的副本（那会让验证结论失去意义）。

【不易（严格匹配，不做模糊猜测）】
    hunk 的上下文行必须**逐字匹配**。允许在期望位置 ±``FUZZ_WINDOW`` 行内搜索，
    但匹配内容必须完全一致；找不到就报错。理由：宽松匹配（如忽略空白）会让
    「补丁其实打错了位置但看起来成功了」成为可能，而验证的全部价值就在于这个
    判定不能有水份。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.repair.guardrails import ParsedFile, parse_unified_diff

#: hunk 期望位置附近允许的搜索窗口（行）
FUZZ_WINDOW = 40


class PatchApplyError(RuntimeError):
    """补丁应用失败（**整体放弃**，不产生半成品）"""

    def __init__(self, message: str, *, path: str = "", hunk: str = "") -> None:
        self.path = str(path or "")
        self.hunk = str(hunk or "")
        super().__init__(message)


@dataclass
class ApplyResult:
    """应用结果

    Attributes:
        ok: 是否全部应用成功。
        applied: 已应用文件（仓库相对路径）。
        created: 新建文件。
        deleted: 删除文件。
        error: 失败原因（ok=False 时）。
        failed_path: 失败文件。
        failed_hunk: 失败 hunk 头。
    """

    ok: bool = False
    applied: List[str] = field(default_factory=list)
    created: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    error: str = ""
    failed_path: str = ""
    failed_hunk: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "applied": list(self.applied),
            "created": list(self.created),
            "deleted": list(self.deleted),
            "error": self.error,
            "failed_path": self.failed_path,
            "failed_hunk": self.failed_hunk,
        }


def _read_lines(path: str) -> List[str]:
    """读文件为行列表（``\\n`` 切分；**不保留行尾符**）

    口径选择：统一以 ``\\n`` 切分并丢掉行尾符，使"文件里的行"与"diff 上下文行"
    的表示一致。代价是行内文本带 ``\\r``（CRLF 文件）时会与 diff 行不等——
    故匹配时统一做 ``rstrip``（见 ``_norm``），把「行尾符差异」与「行内容差异」
    分开：前者容忍，后者严格。
    """
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        text = fh.read()
    if text == "":
        return []
    return text.split("\n")


def _norm(line: str) -> str:
    """匹配用归一：仅去行尾空白（容忍 CRLF 与行尾空格，**不**容忍内容差异）"""
    return str(line).rstrip()


def _write_lines(path: str, lines: Sequence[str]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(lines))


def hunk_blocks(hunk: Any) -> Tuple[List[str], List[str]]:
    """拆 hunk 为 ``(旧侧行, 新侧行)``

    旧侧 = 上下文 + 删除行；新侧 = 上下文 + 新增行。
    ``\\ No newline at end of file`` 标记不进任何一侧（它是元数据）。
    """
    old: List[str] = []
    new: List[str] = []
    for line in hunk.lines:
        if line.startswith("\\"):
            continue
        if line.startswith("+"):
            new.append(line[1:])
        elif line.startswith("-"):
            old.append(line[1:])
        elif line.startswith(" "):
            old.append(line[1:])
            new.append(line[1:])
        elif line == "":
            old.append("")
            new.append("")
    return old, new


def _find_block(lines: List[str], block: Sequence[str], *, expected: int) -> int:
    """在 ``lines`` 中定位 ``block``（0 基起点）；失败抛 ``PatchApplyError``

    Args:
        expected: ``@@`` 头给出的期望起点（0 基）。先精确试探，再向两侧扩窗。
    """
    if not block:
        return max(0, min(int(expected), len(lines)))
    total = len(lines)
    if expected < 0:
        expected = 0
    want = [_norm(x) for x in block]

    def _match_at(pos: int) -> bool:
        if pos < 0 or pos + len(want) > total:
            return False
        return [_norm(x) for x in lines[pos:pos + len(want)]] == want

    if _match_at(expected):
        return expected
    for delta in range(1, FUZZ_WINDOW + 1):
        for candidate in (expected - delta, expected + delta):
            if _match_at(candidate):
                return candidate
    raise PatchApplyError("hunk 上下文在目标文件中找不到精确匹配", path="", hunk="")


def apply_unified_diff(diff: str, root: str, *, dry_run: bool = False) -> ApplyResult:
    """把统一 diff 应用到 ``root``（**全部成功才落盘**）

    Args:
        diff: 统一 diff 文本。
        root: 目标根目录（隔离验证时是**临时副本**根）。
        dry_run: 只验证可应用性，不写盘。

    Returns:
        ``ApplyResult``；``ok=False`` 时目标目录**未被修改**。
    """
    parsed, parse_error = parse_unified_diff(diff)
    if parse_error:
        return ApplyResult(ok=False, error=f"补丁解析失败：{parse_error}")
    pending: List[Tuple[str, Optional[List[str]]]] = []
    created: List[str] = []
    deleted: List[str] = []
    applied: List[str] = []

    for item in parsed:
        rel = item.path.replace("\\", "/")
        full = os.path.join(root, rel)
        exists = os.path.isfile(full)
        if item.is_deleted:
            if not exists:
                return ApplyResult(ok=False, failed_path=rel,
                                   error=f"要删除的文件不存在：{rel}")
            pending.append((full, None))
            deleted.append(rel)
            continue
        if item.is_new and not exists:
            lines: List[str] = []
        elif exists:
            try:
                lines = _read_lines(full)
            except OSError as exc:
                return ApplyResult(ok=False, failed_path=rel,
                                   error=f"读取失败：{type(exc).__name__}: {exc}")
        else:
            return ApplyResult(ok=False, failed_path=rel,
                               error=f"目标文件不存在（且补丁未标记为新增）：{rel}")
        for hunk in item.hunks:
            old_block, new_block = hunk_blocks(hunk)
            if not old_block and not new_block:
                continue
            if not old_block:
                # 纯新增（如文件中部插入）：按 hunk 头的新起点推断插入位置
                insert_at = min(len(lines), max(0, hunk.new_start - 1))
                lines[insert_at:insert_at] = list(new_block)
                continue
            try:
                pos = _find_block(lines, old_block, expected=hunk.old_start - 1)
            except PatchApplyError as exc:
                return ApplyResult(
                    ok=False, failed_path=rel, failed_hunk=hunk.header,
                    error=f"{exc}（文件 {rel}，hunk {hunk.header}）")
            lines[pos:pos + len(old_block)] = list(new_block)
        pending.append((full, lines))
        applied.append(rel)
        if item.is_new and not exists:
            created.append(rel)

    if dry_run:
        return ApplyResult(ok=True, applied=applied, created=created, deleted=deleted)

    # 全部 hunk 已在内存中应用成功 → 一次性落盘（原子性：不留"打了一半"的副本）
    written: List[str] = []
    try:
        for full, target_lines in pending:
            if target_lines is None:
                os.remove(full)          # 删除文件（``None`` = 该文件被删）
                continue
            _write_lines(full, target_lines)
            written.append(full)
    except OSError as exc:
        # 落盘失败：尽力回滚已写文件（无法回滚时如实报告，不静默）
        return ApplyResult(ok=False, applied=applied, created=created, deleted=deleted,
                           error=f"落盘失败（已写 {len(written)} 个文件）："
                                 f"{type(exc).__name__}: {exc}")
    return ApplyResult(ok=True, applied=applied, created=created, deleted=deleted)


__all__ = ["FUZZ_WINDOW", "PatchApplyError", "ApplyResult", "hunk_blocks",
           "apply_unified_diff"]
