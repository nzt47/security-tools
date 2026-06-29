#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成关键字参数冲突修复的变更清单报告

分析指定 git 提交中的代码变更，提取参数冲突修复点，生成结构化的
Markdown 报告，便于代码审查。

用法:
    python scripts/generate_fix_report.py
    python scripts/generate_fix_report.py --commits edabf6bc,44eaccf6,4ec3f00c
    python scripts/generate_fix_report.py --output docs/kwarg_fix_report.md
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ════════════════════════════════════════════════════════════
#  结构化日志（遵循可观测性硬约束）
# ════════════════════════════════════════════════════════════

def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


def _log(action: str, **payload: Any) -> None:
    """输出结构化 JSON 日志（trace_id, module_name, action, duration_ms）"""
    record = {
        "trace_id": _trace_id(),
        "module_name": "generate_fix_report",
        "action": action,
        "duration_ms": 0.0,
        **payload,
    }
    print(json.dumps(record, ensure_ascii=False, default=str), file=sys.stderr)


# ════════════════════════════════════════════════════════════
#  数据结构
# ════════════════════════════════════════════════════════════

@dataclass
class FixRecord:
    """单处修复记录"""
    commit: str          # commit hash (short)
    commit_msg: str      # commit message (first line)
    commit_date: str     # commit date
    file: str            # 文件路径
    lineno: int          # 变更行号
    function: str        # 涉及的函数名
    old_pattern: str     # 旧代码片段（**kwargs / **payload）
    new_pattern: str     # 新代码片段（**safe_kwargs / **safe_payload）
    filter_var: str      # 过滤变量名
    reserved_keys: List[str]  # 过滤的保留键
    risk_level: str      # 风险等级 HIGH/MEDIUM
    category: str        # 修复类别


@dataclass
class CommitInfo:
    """提交信息"""
    hash: str
    short_hash: str
    message: str
    date: str
    author: str


# ════════════════════════════════════════════════════════════
#  Git 操作
# ════════════════════════════════════════════════════════════

def git_cmd(*args: str) -> str:
    """执行 git 命令并返回输出"""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
        if result.returncode != 0:
            _log("git_cmd.error", args=args, error=result.stderr.strip())
            return ""
        return result.stdout
    except Exception as e:
        _log("git_cmd.exception", args=args, error=str(e))
        return ""


def get_commit_info(commit_hash: str) -> CommitInfo:
    """获取提交信息"""
    fmt = "%H%n%h%n%s%n%ci%n%an"
    output = git_cmd("show", "-s", f"--format={fmt}", commit_hash)
    lines = output.strip().split("\n")
    if len(lines) < 5:
        return CommitInfo(commit_hash, commit_hash[:8], "", "", "")
    return CommitInfo(
        hash=lines[0],
        short_hash=lines[1],
        message=lines[2],
        date=lines[3],
        author=lines[4],
    )


def get_commit_diff(commit_hash: str) -> str:
    """获取提交的 diff（仅新增/修改行）"""
    return git_cmd("diff", commit_hash + "~1", commit_hash, "--unified=3")


def get_changed_files(commit_hash: str) -> List[str]:
    """获取提交中修改的文件列表"""
    output = git_cmd("diff", "--name-only", commit_hash + "~1", commit_hash)
    return [f.strip() for f in output.split("\n") if f.strip()]


# ════════════════════════════════════════════════════════════
#  Diff 解析
# ════════════════════════════════════════════════════════════

# 匹配 **变量名 展开模式
SPREAD_PATTERN = re.compile(r"\*\*(\w+)")

# 匹配过滤变量赋值: safe_kwargs = {k: v for k, v in ... if k not in _RESERVED}
FILTER_ASSIGN_PATTERN = re.compile(
    r"(\w+)\s*=\s*\{k:\s*v\s+for\s+k,\s*v\s+in\s+(\w+)\.items\(\)\s+"
    r"if\s+k\s+not\s+in\s+(\w+)\}"
)

# 匹配保留键集合定义: _RESERVED = {...} 或 _http_reserved = {...}
RESERVED_SET_PATTERN = re.compile(
    r"(_\w+)\s*=\s*\{([^}]+)\}"
)

# 匹配函数定义
FUNC_DEF_PATTERN = re.compile(r"^\s*def\s+(\w+)\s*\(")


def parse_diff_for_fixes(diff: str, commit: CommitInfo) -> List[FixRecord]:
    """从 diff 中提取修复记录

    识别模式:
        +  _RESERVED = {"trace_id", "duration_ms", ...}
        +  safe_kwargs = {k: v for k, v in kwargs.items() if k not in _RESERVED}
        -  **kwargs
        +  **safe_kwargs
    """
    fixes: List[FixRecord] = []
    lines = diff.split("\n")

    current_file = ""
    current_func = "<module>"
    pending_reserved: Dict[str, List[str]] = {}  # 变量名 → 保留键列表
    pending_filters: Dict[str, Tuple[str, str]] = {}  # 过滤变量名 → (源变量, 保留键变量)

    i = 0
    while i < len(lines):
        line = lines[i]

        # 文件头
        if line.startswith("+++ b/"):
            current_file = line[6:]
            current_func = "<module>"
            pending_reserved.clear()
            pending_filters.clear()
            i += 1
            continue

        if line.startswith("--- a/"):
            i += 1
            continue

        # 函数定义（上下文行或新增行）
        func_match = FUNC_DEF_PATTERN.match(line.lstrip("+").lstrip(" "))
        if func_match:
            current_func = func_match.group(1)

        # 新增行: 保留键集合定义
        if line.startswith("+"):
            added = line[1:]
            # 匹配 _RESERVED = {"key1", "key2", ...}
            m = RESERVED_SET_PATTERN.search(added)
            if m:
                var_name = m.group(1)
                keys_str = m.group(2)
                keys = re.findall(r'"(\w+)"', keys_str)
                if keys:
                    pending_reserved[var_name] = keys
                    _log("parse.found_reserved",
                         file=current_file, var=var_name, keys=keys)

            # 匹配 safe_kwargs = {k: v for k, v in ... if k not in _RESERVED}
            m = FILTER_ASSIGN_PATTERN.search(added)
            if m:
                filter_var = m.group(1)
                source_var = m.group(2)
                reserved_var = m.group(3)
                pending_filters[filter_var] = (source_var, reserved_var)
                _log("parse.found_filter",
                     file=current_file, filter_var=filter_var,
                     source=source_var, reserved=reserved_var)

        # 匹配 -旧行/+新行 的 **展开 变更
        if (line.startswith("-") and not line.startswith("---")
                and i + 1 < len(lines)
                and lines[i + 1].startswith("+") and not lines[i + 1].startswith("+++")):
            old_line = line[1:]
            new_line = lines[i + 1][1:]

            old_spreads = SPREAD_PATTERN.findall(old_line)
            new_spreads = SPREAD_PATTERN.findall(new_line)

            # 旧行有 **kwargs/payload，新行有 **safe_kwargs/safe_payload
            for old_var in old_spreads:
                for new_var in new_spreads:
                    if (old_var != new_var
                            and new_var in pending_filters):
                        source_var, reserved_var = pending_filters[new_var]
                        reserved_keys = pending_reserved.get(reserved_var, [])

                        # 判断风险等级
                        risk = "HIGH" if len(reserved_keys) >= 2 else "MEDIUM"

                        # 判断修复类别
                        if "observability" in current_file:
                            category = "可观测性日志"
                        elif "adapter" in current_file:
                            category = "LLM 适配器"
                        elif "http_client" in current_file:
                            category = "HTTP 客户端"
                        elif "search" in current_file:
                            category = "搜索模块"
                        elif "summarizer" in current_file:
                            category = "子代理摘要"
                        elif "failure_collector" in current_file:
                            category = "失败收集器"
                        else:
                            category = "其他"

                        fixes.append(FixRecord(
                            commit=commit.short_hash,
                            commit_msg=commit.message,
                            commit_date=commit.date,
                            file=current_file,
                            lineno=i,
                            function=current_func,
                            old_pattern=f"**{old_var}",
                            new_pattern=f"**{new_var}",
                            filter_var=new_var,
                            reserved_keys=reserved_keys,
                            risk_level=risk,
                            category=category,
                        ))
                        _log("parse.found_fix",
                             file=current_file, func=current_func,
                             old=f"**{old_var}", new=f"**{new_var}",
                             keys=reserved_keys, risk=risk)
        i += 1

    return fixes


# ════════════════════════════════════════════════════════════
#  报告生成
# ════════════════════════════════════════════════════════════

def generate_markdown_report(fixes: List[FixRecord],
                             commits: List[CommitInfo]) -> str:
    """生成 Markdown 格式的变更清单报告"""
    lines = []

    # 标题
    lines.append("# 关键字参数冲突修复 — 变更清单报告")
    lines.append("")
    lines.append(f"> **生成时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> **修复总数**: {len(fixes)} 处")
    lines.append(f"> **涉及提交**: {len(commits)} 个")
    lines.append(f"> **涉及文件**: {len(set(f.file for f in fixes))} 个")
    lines.append("")

    # 提交概览
    lines.append("## 1. 提交概览")
    lines.append("")
    lines.append("| Commit | 日期 | 提交信息 |")
    lines.append("|--------|------|----------|")
    for c in commits:
        lines.append(f"| `{c.short_hash}` | {c.date[:10]} | {c.message} |")
    lines.append("")

    # 修复统计
    lines.append("## 2. 修复统计")
    lines.append("")

    # 按风险等级
    by_risk: Dict[str, List[FixRecord]] = {"HIGH": [], "MEDIUM": []}
    for f in fixes:
        by_risk.setdefault(f.risk_level, []).append(f)
    lines.append("### 按风险等级")
    lines.append("")
    lines.append("| 风险等级 | 数量 | 说明 |")
    lines.append("|----------|------|------|")
    lines.append(f"| 🔴 HIGH | {len(by_risk.get('HIGH', []))} | "
                 f"显式 kwarg 与函数参数同名，**kwargs 展开可能冲突 |")
    lines.append(f"| 🟡 MEDIUM | {len(by_risk.get('MEDIUM', []))} | "
                 f"外部函数签名已知，**kwargs 转发可能冲突 |")
    lines.append("")

    # 按修复类别
    by_category: Dict[str, List[FixRecord]] = {}
    for f in fixes:
        by_category.setdefault(f.category, []).append(f)
    lines.append("### 按修复类别")
    lines.append("")
    lines.append("| 类别 | 数量 | 典型文件 |")
    lines.append("|------|------|----------|")
    for cat, items in sorted(by_category.items(), key=lambda x: -len(x[1])):
        sample_file = items[0].file.split("/")[-1]
        lines.append(f"| {cat} | {len(items)} | `{sample_file}` |")
    lines.append("")

    # 按保留键频率
    key_freq: Dict[str, int] = {}
    for f in fixes:
        for k in f.reserved_keys:
            key_freq[k] = key_freq.get(k, 0) + 1
    lines.append("### 保留键出现频率（Top 10）")
    lines.append("")
    lines.append("| 保留键 | 出现次数 |")
    lines.append("|--------|----------|")
    for k, cnt in sorted(key_freq.items(), key=lambda x: -x[1])[:10]:
        lines.append(f"| `{k}` | {cnt} |")
    lines.append("")

    # 详细变更清单
    lines.append("## 3. 详细变更清单")
    lines.append("")

    # 按文件分组
    by_file: Dict[str, List[FixRecord]] = {}
    for f in fixes:
        by_file.setdefault(f.file, []).append(f)

    for filepath in sorted(by_file.keys()):
        items = by_file[filepath]
        lines.append(f"### `{filepath}` ({len(items)} 处)")
        lines.append("")
        lines.append("| 行 | 函数 | 旧模式 | 新模式 | 过滤变量 | 保留键 | 风险 | Commit |")
        lines.append("|----|------|--------|--------|----------|--------|------|--------|")
        for f in items:
            keys_str = ", ".join(f"`{k}`" for k in f.reserved_keys[:5])
            if len(f.reserved_keys) > 5:
                keys_str += f" (+{len(f.reserved_keys) - 5})"
            risk_icon = "🔴" if f.risk_level == "HIGH" else "🟡"
            lines.append(
                f"| {f.lineno} | `{f.function}` | "
                f"`{f.old_pattern}` | `{f.new_pattern}` | "
                f"`{f.filter_var}` | {keys_str} | "
                f"{risk_icon} {f.risk_level} | `{f.commit}` |"
            )
        lines.append("")

    # 修复模式说明
    lines.append("## 4. 修复模式说明")
    lines.append("")
    lines.append("### 统一修复模板")
    lines.append("")
    lines.append("```python")
    lines.append("# 1. 定义保留键集合（与显式参数同名）")
    lines.append('_RESERVED = {"trace_id", "duration_ms", "level", "action", "module_name"}')
    lines.append("")
    lines.append("# 2. 过滤 **kwargs 中的保留键")
    lines.append("safe_kwargs = {k: v for k, v in kwargs.items() if k not in _RESERVED}")
    lines.append("")
    lines.append("# 3. 使用过滤后的变量展开")
    lines.append("func(explicit_kwarg=value, **safe_kwargs)")
    lines.append("```")
    lines.append("")
    lines.append("### 扫描器识别规则")
    lines.append("")
    lines.append("扫描器 `scripts/scan_kwarg_conflicts.py` 通过以下规则识别已过滤变量，"
                 "避免误报：")
    lines.append("")
    lines.append("- 变量名含 `safe_`/`filtered_`/`clean_` 前缀 → 识别为已过滤")
    lines.append("- 变量名含 `_safe`/`_filtered`/`_clean` 后缀 → 识别为已过滤")
    lines.append("- 字典推导式含 `if k not in _RESERVED` 条件 → 识别为已过滤")
    lines.append("")

    # 审查建议
    lines.append("## 5. 审查建议")
    lines.append("")
    lines.append("1. **重点审查 HIGH 风险项**: 确认保留键集合是否完整覆盖了"
                 "目标函数的所有显式参数名")
    lines.append("2. **检查过滤变量命名**: 确保使用 `safe_` 前缀或 `_safe` 后缀，"
                 "以便扫描器识别")
    lines.append("3. **验证测试覆盖**: 运行 `pytest tests/unit/test_observability_track_event.py` "
                 "确认无回归")
    lines.append("4. **CI 集成**: 提交前运行 "
                 "`python scripts/scan_kwarg_conflicts.py --min-risk HIGH`，"
                 "HIGH 风险会阻断提交")
    lines.append("")

    # 附录
    lines.append("## 6. 附录")
    lines.append("")
    lines.append("### 扫描命令")
    lines.append("")
    lines.append("```bash")
    lines.append("# 扫描高风险（CI 拦截用）")
    lines.append("python scripts/scan_kwarg_conflicts.py --path agent/ --min-risk HIGH")
    lines.append("")
    lines.append("# 扫描中风险（代码审查用）")
    lines.append("python scripts/scan_kwarg_conflicts.py --path agent/ --min-risk MEDIUM")
    lines.append("")
    lines.append("# 生成 JSON 报告")
    lines.append("python scripts/scan_kwarg_conflicts.py --format json --output report.json")
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
#  主入口
# ════════════════════════════════════════════════════════════

# 默认分析的提交（关键字参数冲突修复的三次提交）
DEFAULT_COMMITS = ["edabf6bc", "44eaccf6", "4ec3f00c"]


def main():
    parser = argparse.ArgumentParser(
        description="生成关键字参数冲突修复的变更清单报告",
    )
    parser.add_argument(
        "--commits", default=",".join(DEFAULT_COMMITS),
        help="分析的 commit hash 列表（逗号分隔）",
    )
    parser.add_argument(
        "--output", default=None,
        help="输出文件路径（默认: 控制台）",
    )
    parser.add_argument(
        "--format", choices=["markdown", "json"], default="markdown",
        help="输出格式（默认: markdown）",
    )
    args = parser.parse_args()

    commit_hashes = [c.strip() for c in args.commits.split(",") if c.strip()]
    _log("report.start", commits=commit_hashes, format=args.format)

    all_fixes: List[FixRecord] = []
    commit_infos: List[CommitInfo] = []

    for ch in commit_hashes:
        info = get_commit_info(ch)
        commit_infos.append(info)
        _log("report.analyzing_commit",
             commit=info.short_hash, msg=info.message)

        diff = get_commit_diff(ch)
        if not diff:
            _log("report.no_diff", commit=ch)
            continue

        fixes = parse_diff_for_fixes(diff, info)
        all_fixes.extend(fixes)
        _log("report.commit_fixes",
             commit=info.short_hash, fix_count=len(fixes))

    # 生成报告
    if args.format == "json":
        report = json.dumps({
            "scan_time": time.strftime('%Y-%m-%d %H:%M:%S'),
            "total_fixes": len(all_fixes),
            "commits": [
                {"hash": c.short_hash, "date": c.date, "message": c.message}
                for c in commit_infos
            ],
            "fixes": [
                {
                    "commit": f.commit,
                    "file": f.file,
                    "lineno": f.lineno,
                    "function": f.function,
                    "old_pattern": f.old_pattern,
                    "new_pattern": f.new_pattern,
                    "filter_var": f.filter_var,
                    "reserved_keys": f.reserved_keys,
                    "risk_level": f.risk_level,
                    "category": f.category,
                }
                for f in all_fixes
            ],
        }, ensure_ascii=False, indent=2)
    else:
        report = generate_markdown_report(all_fixes, commit_infos)

    # 输出
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(report, encoding="utf-8")
        _log("report.output", file=args.output, size=len(report))
        print(f"报告已写入: {args.output}")
    else:
        print(report)

    _log("report.done",
         total_fixes=len(all_fixes),
         files=len(set(f.file for f in all_fixes)))


if __name__ == "__main__":
    main()
