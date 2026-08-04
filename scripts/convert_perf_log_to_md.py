"""性能对比日志转 Markdown 转换脚本 [TLM-L3]

用途：
- 将 simulate_l2_async_switch.py --log-file 生成的纯文本性能对比日志转为 Markdown 表格
- 方便直接粘贴到技术文档、PR 描述、Wiki 中

【不易】原始数据不变（P50/P99/Max 数值精确，不四舍五入）
【变易】支持多种输出：stdout / 文件 / 同时输出
【简易】单文件脚本，正则提取，无外部依赖

运行：
    python scripts/convert_perf_log_to_md.py --input test_reports/l2_switch_perf_comparison.log
    python scripts/convert_perf_log_to_md.py --input test_reports/l2_switch_perf_comparison.log --output test_reports/l2_switch_perf_comparison.md
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def parse_perf_log(content: str) -> dict:
    """解析性能对比日志纯文本

    Returns: {
        "comparison": [{"metric": "P50", "sync": 16.81, "async": 370.64, "change": "变慢 22.0 倍", "verdict": "❌ 恶化"}],
        "decision": "❌ 不建议切换：...",
        "recommendation": "建议：保持当前同步串行方案...",
        "root_cause": "路径缓存已消除瓶颈...",
        "overview": [{"scenario": "A", "p50": 98.63, "p99": 98.83, "pmax": 98.83}],
    }
    """
    result = {
        "comparison": [],
        "decision": "",
        "recommendation": "",
        "root_cause": "",
        "overview": [],
    }

    # 解析对比表（P50/P99/Max 行）
    # 格式：  P50      16.81                370.64               变慢 22.0 倍       ❌ 恶化
    comp_pattern = re.compile(
        r"^\s*(P50|P99|Max)\s+([\d.]+)\s+([\d.]+)\s+(变慢|变快)\s+([\d.]+)\s+倍\s+(❌\s*恶化|✅\s*改善|⚠️\s*持平|N/A)"
    )
    for line in content.split("\n"):
        m = comp_pattern.match(line)
        if m:
            result["comparison"].append({
                "metric": m.group(1),
                "sync": float(m.group(2)),
                "async": float(m.group(3)),
                "change": f"{m.group(4)} {m.group(5)} 倍",
                "verdict": m.group(6).replace("  ", " "),
            })

    # 解析决策建议
    decision_match = re.search(r"(❌\s*不建议切换[^\n]*|✅\s*可考虑切换[^\n]*|⚠️\s*性能持平[^\n]*)", content)
    if decision_match:
        result["decision"] = decision_match.group(1).strip()

    rec_match = re.search(r"建议：([^\n]+)", content)
    if rec_match:
        result["recommendation"] = rec_match.group(1).strip()

    root_match = re.search(r"根因参考：([^\n]+)", content)
    if root_match:
        result["root_cause"] = root_match.group(1).strip()

    # 解析全场景概览
    # 格式：  A        98.63           98.83           98.83
    overview_pattern = re.compile(r"^\s*([A-Z])\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$")
    in_overview = False
    for line in content.split("\n"):
        if "全部场景概览" in line:
            in_overview = True
            continue
        if in_overview:
            m = overview_pattern.match(line)
            if m:
                result["overview"].append({
                    "scenario": m.group(1),
                    "p50": float(m.group(2)),
                    "p99": float(m.group(3)),
                    "pmax": float(m.group(4)),
                })

    return result


def to_markdown(data: dict, source_file: str = "") -> str:
    """转换为 Markdown 格式

    Returns: Markdown 文本
    """
    lines: list[str] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines.append("# L2 性能对比：同步串行 vs 异步 IO")
    lines.append("")
    lines.append(f"**生成时间**: {now}  ")
    lines.append(f"**数据来源**: `{source_file}`  ")
    lines.append(f"**压测配置**: 300 条冷数据 × 30 子目录 × 20 并发")
    lines.append("")

    # 决策建议（置顶，便于快速查看）
    if data["decision"]:
        lines.append("## 决策建议")
        lines.append("")
        lines.append(f"> {data['decision']}")
        lines.append(">")
        if data["recommendation"]:
            lines.append(f"> {data['recommendation']}")
        if data["root_cause"]:
            lines.append(">")
            lines.append(f"> **根因**: {data['root_cause']}")
        lines.append("")

    # 对比表
    if data["comparison"]:
        lines.append("## 场景 C（同步串行）vs 场景 E（异步 IO）")
        lines.append("")
        lines.append("| 指标 | 同步串行 (C) | 异步 IO (E) | 变化 | 结论 |")
        lines.append("|------|--------------|-------------|------|------|")
        for row in data["comparison"]:
            lines.append(
                f"| {row['metric']} | {row['sync']:.2f}ms | {row['async']:.2f}ms | {row['change']} | {row['verdict']} |"
            )
        lines.append("")

    # 全场景概览
    if data["overview"]:
        lines.append("## 全部场景概览")
        lines.append("")
        lines.append("| 场景 | 描述 | P50 (ms) | P99 (ms) | Max (ms) |")
        lines.append("|------|------|----------|----------|----------|")
        scenario_desc = {
            "A": "冷启动（路径缓存空）",
            "B": "热启动（路径缓存满）",
            "C": "高并发同步串行",
            "D": "大 fragment 限量读取",
            "E": "高并发异步 IO",
        }
        for row in data["overview"]:
            desc = scenario_desc.get(row["scenario"], "")
            lines.append(
                f"| {row['scenario']} | {desc} | {row['p50']:.2f} | {row['p99']:.2f} | {row['pmax']:.2f} |"
            )
        lines.append("")

    # 决策门槛说明
    lines.append("## 决策门槛")
    lines.append("")
    lines.append("| P50 比值 (async/sync) | 结论 | 动作 |")
    lines.append("|----------------------|------|------|")
    lines.append("| > 1.0（变慢） | ❌ 不建议切换 | 保持同步方案，执行回滚 |")
    lines.append("| < 1.0（改善） | ✅ 可考虑切换 | 按检查清单执行完整流程 |")
    lines.append("| = 1.0（持平） | ⚠️ 需综合判断 | 结合 P99/Max 与业务场景 |")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*本报告由 `scripts/convert_perf_log_to_md.py` 自动生成，可直接粘贴到技术文档。*")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="性能对比日志转 Markdown")
    parser.add_argument("--input", type=str, required=True, help="输入日志文件路径")
    parser.add_argument("--output", type=str, default="", help="输出 Markdown 文件路径（默认 stdout）")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = ROOT / args.input
    if not input_path.exists():
        print(f"[error] 输入文件不存在: {input_path}", file=sys.stderr)
        return 1

    content = input_path.read_text(encoding="utf-8", errors="replace")
    data = parse_perf_log(content)
    markdown = to_markdown(data, source_file=str(args.input))

    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = ROOT / args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
        print(f"[✓] Markdown 已生成: {output_path}")
    else:
        print(markdown)

    return 0


if __name__ == "__main__":
    sys.exit(main())
