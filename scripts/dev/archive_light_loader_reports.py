"""light_loader 报告月度归档：把 docs/reports 下当月的 light_loader_* 报告
自动汇总到月度文档（docs/reports/light_loader_monthly_summary_YYYYMM.md）。

【幂等】按报告文件名去重：已存在于月度文档的条目不会重复追加；月度文档
不存在时自动创建。可重复运行（含当月多次生成报告后补跑）。

用法（仓库根目录下）：
    $env:PYTHONIOENCODING="utf-8"
    python scripts/dev/archive_light_loader_reports.py [--month 2026-08]

归档条目从报告文件提取：标题（首个 # 行）与生成日期（文件名 YYYYMMDD）。
关键指标趋势为静态小节，首次创建时写入，脚本不覆盖已有内容。
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

# 使脚本可在任意 cwd 下运行：把仓库根加入 sys.path
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

_REPORTS_DIR = REPO_ROOT / "docs" / "reports"
_TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_DATE_RE = re.compile(r"(\d{4})(\d{2})\d{2}")


def _report_title(path: Path) -> str:
    m = _TITLE_RE.search(path.read_text(encoding="utf-8"))
    return m.group(1).strip() if m else path.stem


def _list_month_reports(month: str) -> list[Path]:
    """筛选文件名含 YYYYMM 的 light_loader_* 报告（排除月度汇总本身）。"""
    yyyymm = month.replace("-", "")
    return sorted(
        p for p in _REPORTS_DIR.glob("light_loader_*.md")
        if p.name != f"light_loader_monthly_summary_{yyyymm}.md"
        and yyyymm in p.name
    )


def _render_monthly(month: str, reports: list[Path]) -> str:
    lines = [
        f"# light_loader 月度汇总（{month}）",
        "",
        f"- 归档时间：{date.today().isoformat()}",
        f"- 生成方式：scripts/dev/archive_light_loader_reports.py（幂等，按文件名去重）",
        "",
        "## 本月报告清单",
        "",
        "| 报告 | 标题 |",
        "|---|---|",
    ]
    for p in reports:
        lines.append(f"| {p.name} | {_report_title(p)} |")
    lines += [
        "",
        "## 关键指标趋势（静态，随基准重跑更新）",
        "",
        "- 10000 卡串行耗时（最新实测）：1944.46ms（2026-08-11 线程数拐点扫描）",
        "- 最佳并行线程数：8（= 默认 min(8, 卡片数)，无更优拐点）",
        "- 并行收益：1.03~1.11x（页缓存命中 + GIL 约束，随规模趋缓）",
        "- 环境兼容：Python 3.12.0 / PyYAML 6.0.3 / libyaml C 扩展可用",
        "- 性能门禁阈值：基线 1944ms × 容差 1.5 ≈ 3000ms（见 docs/reports/light_loader_bench_threshold_guide_20260811.md）",
        "",
        "## 关联文档",
        "",
        "- [安装说明](light_loader_package_install_20260811.md)",
        "- [规模基准](light_loader_serial_parallel_bench_20260811.md)",
        "- [线程数拐点](light_loader_workers_scan_20260811.md)",
        "- [阈值配置指南](light_loader_bench_threshold_guide_20260811.md)",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="light_loader 报告月度归档（幂等）")
    parser.add_argument("--month", default=date.today().strftime("%Y-%m"),
                        help="归档月份 YYYY-MM（默认当月）")
    args = parser.parse_args()

    month = args.month
    yyyymm = month.replace("-", "")
    target = _REPORTS_DIR / f"light_loader_monthly_summary_{yyyymm}.md"

    reports = _list_month_reports(month)
    if not reports:
        print(f"本月（{month}）无 light_loader 报告可归档")
        return 0

    if target.exists():
        existing = target.read_text(encoding="utf-8")
        # 幂等：跳过已归档条目
        new = [p for p in reports if p.name not in existing]
        if not new:
            print(f"月度文档已存在且无新报告条目（幂等）: {target.name}")
            return 0
        # 追加新条目到「本月报告清单」表格末尾
        table_lines = existing.splitlines()
        insert_at = None
        for i, ln in enumerate(table_lines):
            if ln.startswith("| 报告 |"):
                insert_at = i + 2  # 表头下一行开始追加
                break
        rows = [f"| {p.name} | {_report_title(p)} |" for p in new]
        table_lines[insert_at:insert_at] = rows
        target.write_text("\n".join(table_lines) + "\n", encoding="utf-8")
        print(f"已追加 {len(new)} 条新报告到 {target.name}")
    else:
        target.write_text(_render_monthly(month, reports), encoding="utf-8")
        print(f"已创建月度归档: {target.name}（{len(reports)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
