#!/usr/bin/env python3
"""将回归测试 CSV 数据转换为 Markdown 表格，并插入 PR 合并报告

【不易】契约:
  - CSV 首行必须是表头，首列缺失/空文件时报错而非静默
  - 单元格中的 `|` 会被转义为 `\\|`，防止破坏表格结构
  - 重复插入同一锚点时幂等替换（同标题块替换，不叠加）

用法:
  # 仅打印 Markdown 表格（stdout）
  python scripts/csv_to_md_table.py --csv docs/PR136_REGRESSION_TEST_DATA.csv

  # 转换并插入 PR 合并报告（在锚点前插入，幂等）
  python scripts/csv_to_md_table.py \
    --csv docs/PR136_REGRESSION_TEST_DATA.csv \
    --report docs/PR136_MERGE_REPORT.md

  # 自定义标题（默认取 CSV 文件名去扩展名）
  python scripts/csv_to_md_table.py --csv x.csv --report r.md --title "回归数据"
"""

import argparse
import csv
import re
import sys
from pathlib import Path


def csv_to_markdown(csv_path: Path) -> str:
    """CSV 转 Markdown 表格（表头 + 分隔行 + 数据行）"""
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
    except OSError as e:
        sys.exit(f"[错误] 无法读取 CSV: {e}")

    if not rows:
        sys.exit(f"[错误] CSV 为空: {csv_path}")
    if len(rows) < 2:
        sys.exit(f"[错误] CSV 缺少数据行（仅有表头）: {csv_path}")

    header = rows[0]
    lines = ["| " + " | ".join(h.strip() for h in header) + " |"]
    lines.append("|" + "|".join("---" for _ in header) + "|")
    for row in rows[1:]:
        cells = [c.strip().replace("|", "\\|") for c in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_section(title: str, table: str) -> str:
    """构造带标题的 Markdown 小节（标题级别固定为 H3，与报告结构一致）"""
    return f"### {title}\n\n{table}\n"


def upsert_into_report(report_path: Path, section: str, title: str) -> bool:
    """将小节插入报告；若已存在同标题小节则原位替换（幂等）"""
    try:
        content = report_path.read_text(encoding="utf-8")
    except OSError as e:
        sys.exit(f"[错误] 无法读取报告: {e}")

    heading = f"### {title}"
    # 幂等：同标题小节整体替换
    pattern = re.compile(rf"{re.escape(heading)}\n.*?(?=\n### |\n## |\Z)", re.S)
    if pattern.search(content):
        content = pattern.sub(section.rstrip("\n"), content)
    else:
        # 插入到 "## 四、" 之前（回归数据节末尾）；找不到则追加到文件末尾
        anchor = re.search(r"\n## ", content)
        content = section.rstrip("\n") + "\n\n" + content
    report_path.write_text(content, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="CSV 回归测试数据 → Markdown 表格并插入报告")
    parser.add_argument("--csv", required=True, type=Path, help="CSV 数据文件路径")
    parser.add_argument("--report", type=Path, help="PR 合并报告 Markdown 路径（可选，仅打印表格）")
    parser.add_argument("--title", help="插入报告的标题（默认取 CSV 文件名去扩展名）")
    args = parser.parse_args()

    if not args.csv.is_file():
        sys.exit(f"[错误] CSV 文件不存在: {args.csv}")

    title = args.title or args.csv.stem
    table = csv_to_markdown(args.csv)

    if args.report:
        section = build_section(title, table)
        upsert_into_report(args.report, section, title)
        print(f"[OK] 已插入 Markdown 表格到 {args.report}（标题: {title}）")
        print(table)
    else:
        print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
