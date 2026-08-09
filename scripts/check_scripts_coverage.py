#!/usr/bin/env python3
"""scripts/ 层覆盖率门禁：读取 coverage_scripts.xml，低于红线时失败/告警。

【不易】红线 = 脚本层质量底线。S2 起步 50%（当前 6.9%，需 3-5 倍提升，务实起步），
        缺口 ≥ warn-gap（默认 5pp）时阻断部署。
【变易】--warn-gap 让「低于红线但差距小」先告警不阻断，避免刚起步就频繁失败；
        阈值/缺口均可命令行覆盖，供 S3 渐进上调（50% → 60% → 70%）复用。
【简易】单文件单函数，xml 解析与 observability_quality_gate.py 同范式（line-rate 转百分比）。

用法（由 observability-ci 调用）：
    python scripts/check_scripts_coverage.py --xml coverage_scripts.xml
    python scripts/check_scripts_coverage.py --xml coverage_scripts.xml --fail-under 60 --warn-gap 3
"""
import argparse
import sys
import xml.etree.ElementTree as ET


def main() -> int:
    ap = argparse.ArgumentParser(description="scripts/ 层覆盖率门禁")
    ap.add_argument("--xml", required=True, help="coverage xml 文件路径（--cov-report=xml:coverage_scripts.xml）")
    ap.add_argument("--fail-under", type=float, default=50.0, help="红线阈值（默认 50%%）")
    ap.add_argument("--warn-gap", type=float, default=5.0, help="缺口≥该值才失败，否则仅告警（默认 5pp）")
    args = ap.parse_args()

    try:
        root = ET.parse(args.xml).getroot()
        rate = float(root.attrib["line-rate"]) * 100
    except (ET.ParseError, KeyError, FileNotFoundError) as exc:
        print(f"::error::无法解析覆盖率报告 {args.xml}: {exc}")
        return 1

    print(f"scripts 层覆盖率: {rate:.2f}% (红线 {args.fail_under:.0f}%, 告警缺口 {args.warn_gap:.0f}pp)")
    if rate < args.fail_under - args.warn_gap:
        print(f"::error::scripts 覆盖率缺口 {args.fail_under - rate:.1f}pp ≥ {args.warn_gap:.0f}pp，阻断部署")
        return 1
    if rate < args.fail_under:
        print(f"::warning::scripts 覆盖率低于红线 {args.fail_under:.0f}%（缺口 {args.fail_under - rate:.1f}pp），请优先补测")
        return 0
    print(f"✅ scripts 覆盖率达标（≥ {args.fail_under:.0f}%）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
