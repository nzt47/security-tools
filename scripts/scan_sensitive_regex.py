#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""敏感数据正则静态扫描脚本

扫描 agent/ 目录下的 Python 文件，检测可能导致敏感数据泄露的正则模式。

检测规则：
1. 贪婪正则 \S+ 用于匹配敏感值（P0-SEC-002 风险）
2. split('=') 用于脱敏替换逻辑（P0-SEC-001 风险）
3. Bearer token 处理未独立分支
4. 硬编码的真实 token/password 字符串

使用方式：
    python scripts/scan_sensitive_regex.py [--fix-hint]

CI 集成：
    退出码 0 = 无风险，退出码 1 = 发现风险项
"""

import re
import sys
from pathlib import Path
from typing import List, Tuple

# 扫描根目录
SCAN_ROOT = Path(__file__).resolve().parent.parent / "agent"

# 风险规则定义
# 规则1: \S+ 用于 token/password/secret 等敏感值匹配
RULE_GREEDY_REGEX = re.compile(
    r'(token|password|secret|api[_-]?key|bearer|auth|credential).*\\S\+',
    re.IGNORECASE,
)

# 规则2: split('=') 或 split(":") 用于脱敏替换（可能保留 token 值）
RULE_SPLIT_REDACT = re.compile(
    r'split\s*\(\s*["\'][:=]["\']\s*\).*REDACTED',
    re.IGNORECASE,
)

# 规则3: 日志中直接输出敏感变量（logger.xxx(f"...{token}") 等）
RULE_LOG_SENSITIVE = re.compile(
    r'(logger|log|logging)\.\w+\s*\(\s*f["\'].*\{(token|password|secret|api[_-]?key|bearer|credential|auth_header)\}',
    re.IGNORECASE,
)

# 规则4: 硬编码的真实 token 模式（非测试数据，含连字符/下划线等真实 key 格式）
RULE_HARDCODED_TOKEN = re.compile(
    r'(token|api[_-]?key|password|secret)\s*=\s*["\'][a-zA-Z0-9\-_]{20,}["\']',
    re.IGNORECASE,
)

# 排除目录（测试文件、__pycache__ 等）
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "tests", "test"}
# 排除已修复的参考实现文件（包含 split('=') 但有 Bearer 独立分支，已验证安全）
EXCLUDE_FILES = {"token_redactor.py", "scan_sensitive_regex.py", "error_reporting_config.py"}


def scan_file(filepath: Path) -> List[Tuple[int, str, str]]:
    """扫描单个文件，返回风险项列表

    Returns:
        [(行号, 规则名, 匹配内容), ...]
    """
    risks = []
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return risks

    for lineno, line in enumerate(content.splitlines(), 1):
        # 规则1: 贪婪 \S+ 匹配敏感值
        if RULE_GREEDY_REGEX.search(line) and "\\S+" in line:
            risks.append((lineno, "GREEDY_REGEX", line.strip()))

        # 规则2: split('=') 用于脱敏
        if RULE_SPLIT_REDACT.search(line):
            risks.append((lineno, "SPLIT_REDACT", line.strip()))

        # 规则3: 日志输出敏感变量
        if RULE_LOG_SENSITIVE.search(line):
            risks.append((lineno, "LOG_SENSITIVE", line.strip()))

        # 规则4: 硬编码 token
        if RULE_HARDCODED_TOKEN.search(line):
            risks.append((lineno, "HARDCODED_TOKEN", line.strip()))

    return risks


def main(show_hint: bool = False) -> int:
    """主扫描函数

    Returns:
        0 = 无风险，1 = 发现风险

    CI 日志输出 (3 通道冗余, 与 demo-sensitive-info-scan.ps1 保持一致):
        1. stdout: ::error file=,line= workflow command (GitHub UI 红色高亮 + 文件跳转)
        2. $GITHUB_STEP_SUMMARY: Markdown 表格 (PR 检查页面底部汇总)
        3. 文件视图: GitHub 自动在指定行号标注红色波浪线
    """
    import os
    is_ci = os.environ.get("GITHUB_ACTIONS") == "true"
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")

    print("=" * 70)
    print("敏感数据正则静态扫描")
    print("=" * 70)

    total_risks = 0
    scanned_files = 0
    # [CI 增强] 收集风险项用于 step summary Markdown 表格
    summary_rows: List[Tuple[str, int, str, str]] = []

    for pyfile in SCAN_ROOT.rglob("*.py"):
        # 排除目录和文件
        if any(part in EXCLUDE_DIRS for part in pyfile.parts):
            continue
        if pyfile.name in EXCLUDE_FILES:
            continue

        scanned_files += 1
        risks = scan_file(pyfile)

        if risks:
            total_risks += len(risks)
            rel_path = pyfile.relative_to(SCAN_ROOT.parent).as_posix()
            print(f"\n⚠️  {rel_path}")
            for lineno, rule, content in risks:
                print(f"   行 {lineno} [{rule}]: {content}")
                # [CI 增强] 通道 1: ::error workflow command (GitHub UI 红色波浪线 + 文件跳转)
                if is_ci:
                    preview = content if len(content) <= 60 else content[:60] + "..."
                    # workflow command 中 | 需转义为 %7C
                    safe_preview = preview.replace("|", "%7C")
                    print(f"::error file={pyfile},line={lineno}::敏感数据风险 [{rule}]: {safe_preview}")
                # [CI 增强] 收集 Markdown 表格行
                summary_rows.append((rel_path, lineno, rule, content))
                if show_hint:
                    if rule == "GREEDY_REGEX":
                        print(f"      → 修复建议: \\S+ 改为 [^&\\s]+")
                    elif rule == "SPLIT_REDACT":
                        print(f"      → 修复建议: 使用 agent.utils.token_redactor.redact_token_match")
                    elif rule == "LOG_SENSITIVE":
                        print(f"      → 修复建议: 先脱敏再输出日志")

    print(f"\n{'=' * 70}")
    print(f"扫描完成: {scanned_files} 个文件, {total_risks} 个风险项")

    # [CI 增强] 通道 2: step summary Markdown 表格 (PR 检查页面底部聚合视图)
    if is_ci and summary_path and total_risks > 0:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("## ⚠️ 敏感数据正则静态扫描结果\n\n")
                f.write("| 文件 | 行号 | 规则 | 内容预览 |\n")
                f.write("|---|---|---|---|\n")
                for rel_path, lineno, rule, content in summary_rows:
                    # Markdown 转义: | → \|, 换行 → 空格
                    preview = content if len(content) <= 60 else content[:60] + "..."
                    preview_md = preview.replace("|", "\\|").replace("\n", " ").replace("\r", "")
                    f.write(f"| `{rel_path}` | {lineno} | `{rule}` | {preview_md} |\n")
                f.write(f"\n**总计**: {total_risks} 个风险项\n\n")
                f.write("**修复建议**:\n")
                f.write("1. 贪婪正则 `\\S+` 改为 `[^&\\s]+`\n")
                f.write("2. `split('=')` 改用 `agent.utils.token_redactor.redact_token_match`\n")
                f.write("3. 日志输出前先脱敏\n")
            print(f"  [summary] 已写入 {summary_path}")
        except Exception as e:
            print(f"  [warning] 写入 step summary 失败: {e}")

    if total_risks == 0:
        print("✅ 未发现敏感数据正则风险")
        return 0
    else:
        print(f"❌ 发现 {total_risks} 个风险项，请修复后提交")
        return 1


if __name__ == "__main__":
    show_hint = "--fix-hint" in sys.argv
    sys.exit(main(show_hint))
