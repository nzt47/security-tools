"""范围护栏：统一 diff 解析 + 只读区/文件数/行数硬闸（TASK-S7-02 步骤 3）

【任务定位】
    任务书 §三 步骤 3 的「范围护栏（硬闸，违反即丢弃本次产出）」：
      - 单次改动文件数 ≤ N（默认 3）、单文件改行数 ≤ M（默认 120）；
      - 命中**只读区黑名单**即拒；
      - 不得修改测试断言以"骗过"测试——若改测试，必须在 PR 描述中**显式标注**。

【不易（为什么护栏必须是"硬闸"而不是"警告"）】
    自修复的失效模式不是"改坏了"（那会被三关验证拦住），而是"**改宽了**"：把 20 个
    文件一起改、把只读区顺手重构、把断言行号挪一挪。这些改动**能通过**目标用例与
    L0 锚——因为标尺本身没坏，坏的是"这次改动到底动了多大范围"这件事不再可信。
    故护栏必须在**派工之后、验证之前**就把这类产出整包丢弃。

【不易（解析失败 = 不放行）】
    无法解析的 diff 一律 ``ok=False``（fail-closed）。
    「解析不了所以按空补丁处理」会让护栏在最需要它的时候失效。

【变易】
    文件数/行数阈值全部取自 ``policy``（唯一权威）；本模块不持有任何默认阈值常量。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.repair.models import PatchFileStat, PatchGuardReport
from agent.repair.policy import (
    RepairPolicy,
    is_readonly_path,
    is_test_path,
    normalize_relpath,
    readonly_reason,
)

#: ``diff --git a/x b/y`` 头
_DIFF_GIT_RE = re.compile(r"^diff --git\s+(?P<a>\S+)\s+(?P<b>\S+)\s*$")
#: ``+++ b/path`` / ``--- a/path`` 头
_PLUS_RE = re.compile(r"^\+\+\+\s+(?P<path>.+?)\s*$")
_MINUS_RE = re.compile(r"^---\s+(?P<path>.+?)\s*$")
#: hunk 头 ``@@ -a,b +c,d @@``
_HUNK_RE = re.compile(r"^@@\s+-(?P<old_start>\d+)(?:,(?P<old_len>\d+))?\s+"
                      r"\+(?P<new_start>\d+)(?:,(?P<new_len>\d+))?\s+@@")

#: 平台相关路径前缀（解析时报文可能带引号与制表符）
_QUOTE_STRIP = "\"'"


def _clean_path(raw: str) -> str:
    """从 ``---``/``+++`` 头里取路径（去掉引号与制表符尾巴）

    ``/dev/null``（新增/删除文件的一侧）归一为**空串**：它表示"这一侧不存在"，
    而不是一个叫 ``dev/null`` 的文件——若当成路径，删除操作会去找 ``<root>/dev/null``。
    """
    text = str(raw or "").strip().strip(_QUOTE_STRIP)
    if "\t" in text:
        text = text.split("\t", 1)[0]
    if normalize_relpath(text) == "dev/null":
        return ""
    return normalize_relpath(text)


class Hunk:
    """一个 hunk（供测试与统计使用）"""

    def __init__(self, header: str) -> None:
        self.header = header
        match = _HUNK_RE.match(header)
        self.old_start = int(match.group("old_start")) if match else 0
        self.old_len = int(match.group("old_len") or 1) if match else 0
        self.new_start = int(match.group("new_start")) if match else 0
        self.new_len = int(match.group("new_len") or 1) if match else 0
        self.lines: List[str] = []

    @property
    def added(self) -> int:
        return sum(1 for ln in self.lines if ln.startswith("+"))

    @property
    def removed(self) -> int:
        return sum(1 for ln in self.lines if ln.startswith("-"))


class ParsedFile:
    """一个文件条目（一个 ``diff --git`` 段）"""

    def __init__(self, path: str) -> None:
        self.path = path
        self.old_path = ""
        self.added = 0
        self.removed = 0
        self.is_new = False
        self.is_deleted = False
        self.hunks: List[Hunk] = []


def parse_unified_diff(diff: str) -> Tuple[List[ParsedFile], str]:
    """解析统一 diff → ``([ParsedFile], 解析错误)``

    容错口径：
      - 缺 ``diff --git`` 头但存在 ``---`` / ``+++`` 对时，按 ``+++`` 的路径建条目；
      - 空 diff → 返回 ``([], "空补丁")``（调用方据此判「无产出」）；
      - 只要有一个 hunk 落在没有路径的条目上，即返回解析错误（fail-closed）。
    """
    text = str(diff or "")
    if not text.strip():
        return [], "空补丁（子代理未产出 diff）"
    files: List[ParsedFile] = []
    current: Optional[ParsedFile] = None
    pending_old: str = ""
    in_hunk = False

    for line in text.splitlines():
        git_match = _DIFF_GIT_RE.match(line)
        if git_match:
            # a/b 两侧在正常改动里是同一路径；取 b 侧（若有）否则 a 侧。
            # 新建/删除文件的头部仍是两段路径，故这里不做 /dev/null 分支
            # （``/dev/null`` 只出现在 ``---``/``+++`` 行上）。
            path = _clean_path(git_match.group("b")) or _clean_path(git_match.group("a"))
            current = ParsedFile(path)
            files.append(current)
            pending_old = ""
            in_hunk = False
            continue

        if line.startswith("new file mode"):
            if current is not None:
                current.is_new = True
            continue
        if line.startswith("deleted file mode"):
            if current is not None:
                current.is_deleted = True
            continue

        minus_match = _MINUS_RE.match(line)
        if minus_match and not in_hunk:
            # ``--- /dev/null`` → 归一为空串（该侧不存在）
            pending_old = _clean_path(minus_match.group("path"))
            continue
        plus_match = _PLUS_RE.match(line)
        if plus_match and not in_hunk:
            new = _clean_path(plus_match.group("path"))
            if current is None:
                current = ParsedFile(new or pending_old)
                files.append(current)
            if not new:
                # ``+++ /dev/null`` → 该文件被删除
                current.is_deleted = True
            else:
                current.path = new
            current.old_path = pending_old
            continue

        hunk_match = _HUNK_RE.match(line)
        if hunk_match:
            in_hunk = True
            if current is None:
                return files, f"hunk 出现在任何文件头之前：{line[:80]}"
            current.hunks.append(Hunk(line))
            continue

        if in_hunk and current is not None and current.hunks:
            hunk = current.hunks[-1]
            if line.startswith("\\"):
                # `\ No newline at end of file` 标记：属 hunk 内容，**不改变**计数与状态
                hunk.lines.append(line)
            elif line.startswith("+"):
                hunk.lines.append(line)
                current.added += 1
            elif line.startswith("-"):
                hunk.lines.append(line)
                current.removed += 1
            elif line.startswith(" "):
                hunk.lines.append(line)
            elif line.strip() == "":
                # diff 里 hunk 内的上下文空行常缺前导空格
                hunk.lines.append(" ")
            else:
                in_hunk = False
            continue

    if not files:
        return [], "未解析出任何文件条目（可能不是统一 diff）"
    for item in files:
        if not item.path:
            return files, "存在无路径的文件条目"
    return files, ""


def guard_patch(diff: str, *, policy: Optional[RepairPolicy] = None) -> PatchGuardReport:
    """对补丁执行范围护栏判定（步骤 3 硬闸）

    Args:
        diff: 统一 diff 文本。
        policy: 生效策略（文件数/行数阈值）。

    Returns:
        ``PatchGuardReport``（``ok=True`` 才允许进入隔离验证）。
    """
    policy = policy or RepairPolicy()
    report = PatchGuardReport(policy=policy.to_dict())
    parsed, error = parse_unified_diff(diff)
    if error:
        report.parse_error = error
        report.violations.append(f"补丁解析失败：{error}")
        report.ok = False
        return report

    stats: List[PatchFileStat] = []
    for item in parsed:
        rel = item.path
        stat = PatchFileStat(
            path=rel, added=int(item.added), removed=int(item.removed),
            is_new=bool(item.is_new), is_deleted=bool(item.is_deleted),
            is_test=is_test_path(rel), readonly_hit=is_readonly_path(rel))
        stats.append(stat)
        if stat.readonly_hit:
            report.readonly_hits[rel] = readonly_reason(rel) or "命中只读区"
        if stat.is_test and stat.changed_lines > 0:
            if rel not in report.test_assertions_touched:
                report.test_assertions_touched.append(rel)

    report.files = stats
    report.changed_files = len(stats)
    report.max_file_changed_lines = max((s.changed_lines for s in stats), default=0)

    if report.readonly_hits:
        for rel, reason in report.readonly_hits.items():
            report.violations.append(f"命中只读区：{rel}（{reason}）")
    if report.changed_files > int(policy.max_changed_files):
        report.violations.append(
            f"改动文件数超限：{report.changed_files} > {policy.max_changed_files}")
    for stat in stats:
        if stat.changed_lines > int(policy.max_lines_per_file):
            report.violations.append(
                f"单文件改动行数超限：{stat.path} = {stat.changed_lines} "
                f"> {policy.max_lines_per_file}")

    report.ok = not report.violations
    return report


def guard_report_markdown(report: PatchGuardReport) -> str:
    """护栏判定的人读视图（进体检/PR 描述）"""
    lines = [
        f"- 结论：**{'通过' if report.ok else '拒绝'}**",
        f"- 改动文件数：{report.changed_files}"
        f"（上限 {report.policy.get('max_changed_files')}）",
        f"- 单文件最大改动行数：{report.max_file_changed_lines}"
        f"（上限 {report.policy.get('max_lines_per_file')}）",
    ]
    if report.files:
        lines += ["", "| 文件 | 增 | 删 | 测试 | 只读区 |", "|---|---|---|---|---|"]
        for stat in report.files:
            lines.append(
                f"| `{stat.path}` | {stat.added} | {stat.removed} | "
                f"{'是' if stat.is_test else '否'} | "
                f"{'命中' if stat.readonly_hit else '否'} |")
    if report.test_assertions_touched:
        lines += ["", f"- ⚠ 本次**修改了测试文件**（{len(report.test_assertions_touched)} 个）："
                      f"{', '.join(f'`{p}`' for p in report.test_assertions_touched)}",
                  "  → 已按纪律在 PR 描述中显式标注，供人工重点审。"]
    if report.violations:
        lines += ["", "**违规**："] + [f"- {v}" for v in report.violations]
    if report.parse_error:
        lines += ["", f"- 解析错误：{report.parse_error}"]
    return "\n".join(lines) + "\n"


__all__ = [
    "Hunk", "ParsedFile", "parse_unified_diff", "guard_patch", "guard_report_markdown",
]
