#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HPA 巡检报告生成器

【不易】合并预热结果 + 巡检结果为单一 Markdown 报告，结论与 SLO 对齐
【变易】容忍部分输入缺失（预热失败/巡检超时），生成残缺报告而非崩溃
【简易】纯标准库，读取两个 JSON 输出 Markdown，零外部依赖

输入:
  --warmup  warmup_before_patrol.py 的输出 JSON
  --patrol  hpa_scale_patrol.py 的输出 JSON

输出:
  Markdown 报告（默认打印 stdout，--output 写入文件）

用法:
    # 巡检编排脚本执行后生成报告
    python scripts/generate_patrol_report.py \\
        --warmup /tmp/warmup-result.json \\
        --patrol /tmp/patrol-result.json \\
        --output /tmp/patrol-report.md

    # 仅打印到 stdout
    python scripts/generate_patrol_report.py \\
        --warmup /tmp/warmup-result.json \\
        --patrol /tmp/patrol-result.json

报告结构:
  一、总体结论（SLO 达标矩阵）
  二、预热效果分析（冷热启动延迟对比）
  三、HPA 扩容时效（摘要 + 时间线）
  四、错误信息（如有）
  五、改进建议
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional


# ════════════════════════════════════════════════════════════════════
#  JSON 加载（防御性）
# ════════════════════════════════════════════════════════════════════

def load_json(path: str) -> Optional[dict]:
    """加载 JSON 文件，失败返回 None

    【防御】巡检流程中预热/巡检可能失败未生成 JSON，报告应容忍缺失
    """
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"  [WARN] 文件不存在: {path}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"  [WARN] JSON 解析失败: {path} ({e})", file=sys.stderr)
        return None


# ════════════════════════════════════════════════════════════════════
#  格式化辅助
# ════════════════════════════════════════════════════════════════════

def fmt_num(val, suffix: str = "", precision: int = 1) -> str:
    """格式化数值，None → 'N/A'"""
    if val is None:
        return "N/A"
    try:
        return f"{float(val):.{precision}f}{suffix}"
    except (TypeError, ValueError):
        return str(val)


def fmt_pct(val, precision: int = 1) -> str:
    """格式化百分比"""
    if val is None:
        return "N/A"
    try:
        return f"{float(val):.{precision}f}%"
    except (TypeError, ValueError):
        return str(val)


def status_badge(ok: bool, ok_text: str = "✅ PASS", fail_text: str = "❌ FAIL") -> str:
    return ok_text if ok else fail_text


def fmt_latency(val, timed_out: bool = False, probe_duration=None) -> str:
    """格式化延迟值，超时时用 '>Ns(超时估算)' 表示下界"""
    if val is None:
        if timed_out and probe_duration:
            return f">{int(probe_duration)}s(超时估算)"
        return "N/A"
    return fmt_num(val, "s")


# ════════════════════════════════════════════════════════════════════
#  报告章节生成
# ════════════════════════════════════════════════════════════════════

def gen_summary(warmup: Optional[dict], patrol: Optional[dict]) -> str:
    """一、总体结论"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    patrol_id = (patrol or {}).get("patrol_id", "N/A")
    namespace = (warmup or {}).get("namespace", (patrol or {}).get("namespace", "N/A"))
    hpa_name = (warmup or {}).get("hpa_name", "N/A")

    # SLO 判定
    slo_threshold = (patrol or {}).get("slo_threshold_sec", 60)
    scale_time = (patrol or {}).get("scale_time_sec")
    patrol_ok = (patrol or {}).get("success", False) if patrol else False
    slo_ok = patrol_ok and (scale_time is not None and scale_time <= slo_threshold)

    warmup_ok = (warmup or {}).get("success", False) if warmup else False
    improvement_pct = (warmup or {}).get("latency_improvement_pct")
    # 预热有效判定: 改善率 > 0
    warmup_effective = (improvement_pct is not None and improvement_pct > 0) if warmup else False

    lines = [
        "# HPA 巡检报告",
        "",
        f"> **巡检 ID**: `{patrol_id}`  ",
        f"> **生成时间**: {now}  ",
        f"> **命名空间**: `{namespace}`  ",
        f"> **HPA**: `{hpa_name}`",
        "",
        "## 一、总体结论",
        "",
        "| 指标 | 实测值 | SLO 阈值 | 状态 |",
        "|------|--------|---------|------|",
        f"| HPA 扩容时效 | {fmt_num(scale_time, 's')} | ≤{slo_threshold}s | "
        f"{status_badge(slo_ok)} |",
        f"| 预热效果 | 改善 {fmt_pct(improvement_pct)} | >0% | "
        f"{status_badge(warmup_effective, '✅ 有效', '⚠️ 无改善' if warmup else '⬜ 未执行')} |",
        "",
    ]

    # 总体判定
    overall = slo_ok and (warmup_ok if warmup else True)
    lines.append(f"**总体判定**: {status_badge(overall, '✅ 全部达标', '❌ 存在未达标项')}")
    lines.append("")
    return "\n".join(lines)


def gen_warmup_section(warmup: Optional[dict]) -> str:
    """二、预热效果分析"""
    lines = ["## 二、预热效果分析", ""]
    if not warmup:
        lines.extend([
            "> ⬜ 预热结果不可用（warmup JSON 缺失或解析失败）",
            "",
            "可能原因: 预热脚本未执行 / 预热失败未生成 JSON / 兼容性校验阻断",
            "",
        ])
        return "\n".join(lines)

    resolution = warmup.get("metrics_resolution_sec")
    warmup_id = warmup.get("warmup_id", "N/A")

    lines.extend([
        f"**预热 ID**: `{warmup_id}`  ",
        f"**metrics-server 采集间隔**: {fmt_num(resolution, 's', 0) if resolution else 'N/A'}  ",
        f"**预热 Pod**: `{warmup.get('pod_name', 'N/A')}`",
        "",
        "### 2.1 指标延迟对比（冷启动 vs 热启动）",
        "",
        "| 阶段 | 延迟 | CPU 基线 | CPU 峰值 | CPU 变化 | 请求数 | 超时 |",
        "|------|------|---------|---------|---------|--------|------|",
    ])

    latency_before = warmup.get("latency_before", {}) or {}
    latency_after = warmup.get("latency_after", {}) or {}
    probe_duration = (latency_before.get("sample_count") and 20) or 20  # 默认探测时长

    for label, lat in [("预热前（冷启动）", latency_before), ("预热后（热启动）", latency_after)]:
        lines.append(
            f"| {label} | {fmt_latency(lat.get('latency_sec'), lat.get('timed_out'), probe_duration)} "
            f"| {fmt_pct(lat.get('cpu_baseline'), 0)} "
            f"| {fmt_pct(lat.get('cpu_peak'), 0)} "
            f"| {fmt_pct(lat.get('cpu_delta'), 0)} "
            f"| {lat.get('request_count', 'N/A')} "
            f"| {'是' if lat.get('timed_out') else '否'} |"
        )

    imp_sec = warmup.get("latency_improvement_sec")
    imp_pct = warmup.get("latency_improvement_pct")
    lines.extend([
        "",
        f"**延迟改善**: {fmt_num(imp_sec, 's')}（{fmt_pct(imp_pct)}）",
        "",
        "### 2.2 预热执行结果",
        "",
    ])

    wr = warmup.get("warmup_result", {}) or {}
    lines.extend([
        f"- **状态**: {status_badge(wr.get('success', False))}",
        f"- **CPU 变化**: {fmt_pct(wr.get('cpu_before'), 0)} → {fmt_pct(wr.get('cpu_after'), 0)} "
        f"（Δ={fmt_pct(wr.get('cpu_delta'), 0)}）",
        f"- **预热耗时**: {fmt_num(wr.get('elapsed_sec'), 's')}",
        f"- **说明**: {wr.get('message', 'N/A')}",
        "",
    ])
    return "\n".join(lines)


def gen_patrol_section(patrol: Optional[dict]) -> str:
    """三、HPA 扩容时效"""
    lines = ["## 三、HPA 扩容时效", ""]
    if not patrol:
        lines.extend([
            "> ⬜ 巡检结果不可用（patrol JSON 缺失或解析失败）",
            "",
            "可能原因: 巡检脚本未执行 / 巡检超时未生成 JSON / 兼容性校验阻断",
            "",
        ])
        return "\n".join(lines)

    slo = patrol.get("slo_threshold_sec", 60)
    scale_time = patrol.get("scale_time_sec")
    success = patrol.get("success", False)
    slo_ok = success and scale_time is not None and scale_time <= slo

    lines.extend([
        "### 3.1 摘要",
        "",
        "| 项 | 值 |",
        "|----|----|",
        f"| 起始副本 | {patrol.get('start_replicas', 'N/A')} |",
        f"| 峰值副本 | {patrol.get('peak_replicas', 'N/A')} |",
        f"| 目标副本 | {patrol.get('target_replicas', 'N/A')} |",
        f"| 扩容耗时 | {fmt_num(scale_time, 's')} |",
        f"| SLO 阈值 | ≤{slo}s |",
        f"| SLO 达标 | {status_badge(slo_ok)} |",
        "",
    ])

    timeline = patrol.get("timeline", []) or []
    if timeline:
        lines.extend([
            "### 3.2 扩容时间线",
            "",
            "| 时间戳 | 经过(s) | 副本 | 就绪 | CPU(%) | 备注 |",
            "|--------|---------|------|------|--------|------|",
        ])
        # 采样点过多时只显示关键节点（首/末 + 副本变化点）
        show = timeline
        if len(timeline) > 30:
            show = [timeline[0]] + timeline[1::max(1, len(timeline) // 28)] + [timeline[-1]]
            show = list({t["elapsed_sec"]: t for t in show}.values())  # 去重保序
        for e in show:
            lines.append(
                f"| {e.get('t', '')} | {fmt_num(e.get('elapsed_sec'), '', 2)} "
                f"| {e.get('replicas', 'N/A')} | {e.get('ready', 'N/A')} "
                f"| {fmt_pct(e.get('cpu'), 0)} | {e.get('note', '')} |"
            )
        lines.append("")
        lines.append(f"_共 {len(timeline)} 个采样点（显示 {len(show)} 个）_")
        lines.append("")

    return "\n".join(lines)


def gen_errors_section(warmup: Optional[dict], patrol: Optional[dict]) -> str:
    """四、错误信息"""
    lines = ["## 四、错误信息", ""]
    has_error = False

    if patrol and patrol.get("error_message"):
        has_error = True
        lines.extend([
            "### 4.1 巡检错误",
            "",
            "```",
            patrol["error_message"],
            "```",
            "",
        ])

    if warmup and not warmup.get("success", False):
        has_error = True
        wr = warmup.get("warmup_result", {}) or {}
        lines.extend([
            "### 4.2 预热异常",
            "",
            f"- 预热状态: 失败",
            f"- 说明: {wr.get('message', 'N/A')}",
            "",
        ])

    if not has_error:
        lines.extend(["> ✅ 无错误信息", ""])

    return "\n".join(lines)


def gen_suggestions(warmup: Optional[dict], patrol: Optional[dict]) -> str:
    """五、改进建议（基于结果自动生成）"""
    lines = ["## 五、改进建议", ""]
    suggestions = []

    # SLO 未达标建议
    if patrol:
        slo = patrol.get("slo_threshold_sec", 60)
        scale_time = patrol.get("scale_time_sec")
        if scale_time is not None and scale_time > slo:
            suggestions.append(
                f"扩容耗时 {scale_time:.1f}s 超过 SLO {slo}s。排查方向: "
                f"① 检查 metrics-server 采集间隔（建议 ≤30s）；"
                f"② 检查 HPA `--horizontal-pod-autoscaler-sync-period`（默认 15s）；"
                f"③ 检查 Pod 镜像预热/就绪探针延迟。"
            )

    # 预热无效建议
    if warmup:
        imp_pct = warmup.get("latency_improvement_pct")
        if imp_pct is not None and imp_pct <= 0:
            suggestions.append(
                "预热未改善指标延迟。建议: ① 增大 `--warmup-vu`；"
                f"② 延长 `--warmup-duration`（需 ≥ 采集间隔 {warmup.get('metrics_resolution_sec', 'N/A')}s）；"
                "③ 确认 metrics-server 已就绪。"
            )
        wr = warmup.get("warmup_result", {}) or {}
        cpu_delta = wr.get("cpu_delta")
        if cpu_delta is not None and cpu_delta == 0:
            suggestions.append(
                "预热期间 CPU 未变化，流量可能未到达服务。"
                "建议检查 `--service-name` / `--probe-endpoint` 配置。"
            )

    # 超时建议
    if warmup:
        for label, key in [("预热前", "latency_before"), ("预热后", "latency_after")]:
            lat = warmup.get(key, {}) or {}
            if lat.get("timed_out"):
                suggestions.append(
                    f"{label}延迟探测超时（{lat.get('sample_count', 0)} 个采样内 CPU 未变化 ≥ 阈值）。"
                    "建议: ① 增大 `--cpu-change-threshold`；② 延长 `--probe-duration`。"
                )

    if not suggestions:
        lines.append("> ✅ 各项指标正常，无需特别改进。")
    else:
        for i, s in enumerate(suggestions, 1):
            lines.append(f"{i}. {s}")
    lines.append("")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════════════

def generate_report(warmup: Optional[dict], patrol: Optional[dict]) -> str:
    """生成完整 Markdown 报告"""
    sections = [
        gen_summary(warmup, patrol),
        gen_warmup_section(warmup),
        gen_patrol_section(patrol),
        gen_errors_section(warmup, patrol),
        gen_suggestions(warmup, patrol),
    ]
    return "\n".join(sections)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="HPA 巡检报告生成器（合并预热 + 巡检结果为 Markdown）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--warmup", required=True,
                        help="warmup_before_patrol.py 输出 JSON 路径")
    parser.add_argument("--patrol", required=True,
                        help="hpa_scale_patrol.py 输出 JSON 路径")
    parser.add_argument("--output", default=None,
                        help="报告输出 Markdown 文件路径（不指定则打印 stdout）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    warmup = load_json(args.warmup)
    patrol = load_json(args.patrol)

    # 【防御】两个输入都缺失时仍生成报告（仅说明不可用），不崩溃
    if warmup is None and patrol is None:
        print("  [ERROR] 预热与巡检结果均不可用，无法生成有效报告", file=sys.stderr)
        # 仍生成一个最小报告
    elif warmup is None:
        print(f"  [WARN] 预热结果不可用，将生成仅含巡检的残缺报告", file=sys.stderr)
    elif patrol is None:
        print(f"  [WARN] 巡检结果不可用，将生成仅含预热的残缺报告", file=sys.stderr)

    report = generate_report(warmup, patrol)

    if args.output:
        output_dir = os.path.dirname(args.output)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"  [OK] 报告已写入 {args.output}", file=sys.stderr)
    else:
        print(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
