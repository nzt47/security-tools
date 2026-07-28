"""CI 健康度看板自动更新脚本 [CI-L2]

用途：
- 解析 GitHub Actions junit.xml 测试结果
- 在 docs/dashboards/ci_health_dashboard.md 的「合入趋势记录」表格中
  追加一行本次 CI 的通过率/耗时/失败数等指标
- 容错：junit.xml 不存在或表格找不到时安全跳过，不阻塞 CI

设计原则（三义）：
- 【不易】不修改人工补填的历史行，仅在占位行 (YYYY-MM-DD) 前插入新行
- 【变易】支持环境变量覆盖参数（CI_SHA / CI_DATE / CI_BRANCH）
- 【简易】单文件零依赖（仅标准库），失败时 exit 0 不阻塞流水线

运行：
    python scripts/update_ci_health_dashboard.py
    python scripts/update_ci_health_dashboard.py --junit test-results/junit.xml \\
        --dashboard docs/dashboards/ci_health_dashboard.md \\
        --sha abc1234 --date 2026-07-29 --note "fix(xxx): 简要说明"

CI 环境变量（GitHub Actions 自动注入）：
    GITHUB_SHA             - 完整 commit sha
    GITHUB_REF             - 分支引用 (refs/heads/main)
    GITHUB_RUN_ID          - run ID（用于备注链接）
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


# ──────────────────────────────────────────────────────────────────────────
# 解析 junit.xml
# ──────────────────────────────────────────────────────────────────────────

def parse_junit(junit_path: Path) -> dict | None:
    """解析 junit.xml，返回指标 dict。失败返回 None。

    【不易】junit.xml schema 为 pytest --junitxml 标准输出，
           根元素 <testsuites> 或 <testsuite>，字段：tests/failures/errors/skipped/time
    """
    if not junit_path.exists():
        print(f"[dashboard] junit.xml 不存在: {junit_path}", file=sys.stderr)
        return None
    try:
        tree = ET.parse(junit_path)
    except ET.ParseError as e:
        print(f"[dashboard] junit.xml 解析失败: {e}", file=sys.stderr)
        return None

    root = tree.getroot()
    # pytest 新版根是 <testsuites>，旧版直接是 <testsuite>
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        # 退化：聚合所有 testsuite
        suites = root.findall("testsuite") if root.tag == "testsuites" else []
        if not suites:
            print("[dashboard] 未找到 testsuite 元素", file=sys.stderr)
            return None
        total = sum(int(s.get("tests", 0)) for s in suites)
        failed = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
        skipped = sum(int(s.get("skipped", 0)) for s in suites)
        time = sum(float(s.get("time", 0)) for s in suites)
    else:
        total = int(suite.get("tests", 0))
        failed = int(suite.get("failures", 0)) + int(suite.get("errors", 0))
        skipped = int(suite.get("skipped", 0))
        time = float(suite.get("time", 0))

    passed = total - failed - skipped
    pass_rate = (passed / total * 100) if total > 0 else 0.0

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "time": time,
        "pass_rate": pass_rate,
    }


# ──────────────────────────────────────────────────────────────────────────
# 追加趋势行
# ──────────────────────────────────────────────────────────────────────────

# 占位行匹配：模板中的示例行，新行插入其前
PLACEHOLDER_PATTERN = re.compile(
    r"^\| YYYY-MM-DD \| `<sha7>` \| — \| — \| — \| — \| — \| — \| — \| 模板占位行.*\|\s*$",
    re.MULTILINE,
)


def build_trend_row(date: str, sha7: str, metrics: dict, note: str,
                    coverage: str | None = None, mypy_blocked: str = "0") -> str:
    """构建 Markdown 表格行。

    【简易】字段顺序与看板「合入趋势记录」表头一致：
           日期 | Commit | 通过率 | 失败 | 跳过 | 耗时 | 覆盖率 | mypy阻塞 | Trend | 备注
    """
    pass_rate_str = f"{metrics['pass_rate']:.1f}% ({metrics['passed']}/{metrics['total']})"
    duration_str = f"{metrics['time']:.2f}"
    cov_str = coverage if coverage else "—"
    # Trend 判定：失败=0 且通过率 100% 视为 ↑（改善/持平基线），否则 →
    trend = "↑" if metrics["failed"] == 0 else "↓"
    note_escaped = note.replace("|", "\\|") if note else ""
    return (
        f"| {date} | `{sha7}` | {pass_rate_str} | {metrics['failed']} | "
        f"{metrics['skipped']} | {duration_str} | {cov_str} | {mypy_blocked} | "
        f"{trend} | {note_escaped} |"
    )


def insert_trend_row(dashboard_path: Path, new_row: str) -> bool:
    """在占位行前插入新趋势行。返回是否成功写入。

    【不易】不修改占位行之外的内容；找不到占位行时追加到表格末尾。
    """
    if not dashboard_path.exists():
        print(f"[dashboard] 看板文件不存在: {dashboard_path}", file=sys.stderr)
        return False

    content = dashboard_path.read_text(encoding="utf-8")
    match = PLACEHOLDER_PATTERN.search(content)
    if match:
        # 在占位行前插入新行
        new_content = content[:match.start()] + new_row + "\n" + content[match.start():]
    else:
        # 退化：找到趋势表最后一个数据行后追加
        # 表格以空行结束，找"| YYYY-MM-DD" 后第一个空行
        print("[dashboard] 未找到占位行，跳过追加（请人工确认表格结构）", file=sys.stderr)
        return False

    dashboard_path.write_text(new_content, encoding="utf-8")
    return True


# ──────────────────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="CI 健康度看板自动更新")
    parser.add_argument("--junit", default="test-results/junit.xml",
                        help="junit.xml 路径 (默认: test-results/junit.xml)")
    parser.add_argument("--dashboard",
                        default="docs/dashboards/ci_health_dashboard.md",
                        help="看板 Markdown 路径")
    parser.add_argument("--sha", default=None,
                        help="commit sha (默认: 读 GITHUB_SHA 取前 7 位)")
    parser.add_argument("--date", default=None,
                        help="日期 YYYY-MM-DD (默认: UTC 今天)")
    parser.add_argument("--note", default="",
                        help="趋势行备注（如 commit message 摘要）")
    parser.add_argument("--coverage", default=None,
                        help="覆盖率 (如 '72%')，可选")
    parser.add_argument("--mypy-blocked", default="0",
                        help="mypy 阻塞模块数 (默认 0)")
    args = parser.parse_args()

    # 解析 sha
    sha = args.sha or os.getenv("GITHUB_SHA", "")
    sha7 = sha[:7] if sha else "unknown"

    # 解析日期
    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 解析 junit
    junit_path = Path(args.junit)
    metrics = parse_junit(junit_path)
    if metrics is None:
        print("[dashboard] 无 junit 数据，跳过更新（不阻塞 CI）")
        return 0  # 【变易】CI 容错：无数据时 exit 0

    # 构建并插入趋势行
    new_row = build_trend_row(
        date=date,
        sha7=sha7,
        metrics=metrics,
        note=args.note,
        coverage=args.coverage,
        mypy_blocked=args.mypy_blocked,
    )
    print(f"[dashboard] 准备插入趋势行: {new_row}")

    dashboard_path = Path(args.dashboard)
    success = insert_trend_row(dashboard_path, new_row)
    if success:
        print(f"[dashboard] 已更新看板: {dashboard_path}")
    else:
        print("[dashboard] 未更新看板（见 stderr 原因）")
    return 0  # 始终 exit 0，不阻塞 CI


if __name__ == "__main__":
    sys.exit(main())
