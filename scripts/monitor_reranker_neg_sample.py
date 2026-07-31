#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reranker 实时监控 + 切换建议（负样本比例近似）

【不易】仅监控不切换，输出告警 + 切换命令，人工执行
【变易】滑动窗口统计低分请求占比，超阈值告警
【简易】解析 observability JSON 日志，单脚本自包含

设计原理:
    生产环境 reranker 运行时无 ground truth，无法直接获取"负样本比例"。
    用两个近似信号综合判断:
        1. result_count == 0（min_score 过滤全部候选 → 强负样本信号）
        2. top_score < LOW_SCORE_THRESHOLD（top1 分数极低 → 弱负样本信号）
    滑动窗口统计近 N 个请求的负样本占比，超阈值输出告警。

用法:
    # 实时监控模式（tail -f 风格）
    python scripts/monitor_reranker_neg_sample.py --log reranker.log

    # 一次性报告模式（分析历史日志）
    python scripts/monitor_reranker_neg_sample.py --log reranker.log --report

    # 自定义窗口和阈值
    python scripts/monitor_reranker_neg_sample.py --log reranker.log --window 200 --warn 0.3 --critical 0.5

前置条件:
    - app_server 启动时重定向 stderr 到日志文件:
      python -m agent.app_server 2> reranker.log
    - 或通过日志收集系统（如 filebeat）采集 observability 日志
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ──────────────────────────────────────────────
# 配置（可通过命令行参数覆盖）
# ──────────────────────────────────────────────

DEFAULT_WINDOW_SIZE = 100          # 滑动窗口大小
DEFAULT_LOW_SCORE_THRESHOLD = 0.1  # top_score < 0.1 视为低分（近似负样本）
DEFAULT_WARN_THRESHOLD = 0.30      # 低分占比 > 30% → WARNING
DEFAULT_CRITICAL_THRESHOLD = 0.50  # 低分占比 > 50% → CRITICAL
DEFAULT_INT8_RECOMMEND_RATIO = 0.05  # 低分占比 < 5% → 建议切回 INT8
DEFAULT_P99_SLO_MS = 500           # P99 延迟 SLO（ms）

# 切换命令（人工执行）
SWITCH_TO_FP32_CMD_PS = (
    '$env:SKILL_RERANKER_ONNX_VARIANT="model.onnx"; '
    '# 然后重启服务让新配置生效'
)
SWITCH_TO_INT8_CMD_PS = (
    '$env:SKILL_RERANKER_ONNX_VARIANT="model_quantized.onnx"; '
    '# 然后重启服务让新配置生效'
)
SWITCH_TO_FP32_CMD_BASH = (
    'export SKILL_RERANKER_ONNX_VARIANT=model.onnx && '
    '# 然后重启服务让新配置生效'
)
SWITCH_TO_INT8_CMD_BASH = (
    'export SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx && '
    '# 然后重启服务让新配置生效'
)


# ──────────────────────────────────────────────
# 日志解析
# ──────────────────────────────────────────────

def parse_log_line(line: str) -> Optional[Dict[str, Any]]:
    """解析 observability JSON 日志行

    【简易】尝试 json.loads，失败返回 None（非 JSON 行跳过）
    """
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None


def extract_rerank_metric(log: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从日志提取 rerank.completed 指标

    【不易】仅处理 action=rerank.completed 的日志，其他跳过
    Returns:
        指标 dict 或 None（非 rerank 日志）
    """
    if log.get("action") != "rerank.completed":
        return None
    return {
        "top_score": float(log.get("top_score", 0.0)),
        "result_count": int(log.get("result_count", 0)),
        "candidate_count": int(log.get("candidate_count", 0)),
        "duration_ms": float(log.get("duration_ms", 0.0)),
        "score_stddev": float(log.get("score_stddev", 0.0)),
        "timestamp": log.get("trace_id", "")[:8],  # 简短 trace_id
    }


def is_negative_sample(
    metric: Dict[str, Any],
    low_score_threshold: float,
) -> bool:
    """判断是否为负样本（近似）

    【变易】双信号综合判断:
        1. result_count == 0（强信号：min_score 过滤全部候选）
        2. top_score < low_score_threshold（弱信号：top1 分数极低）
    任一满足即视为负样本

    【简易】返回 bool，无中间状态
    """
    # 强信号：候选池非空但结果为空（被 min_score 全部过滤）
    if metric["candidate_count"] > 0 and metric["result_count"] == 0:
        return True
    # 弱信号：top1 分数低于阈值
    if metric["top_score"] < low_score_threshold:
        return True
    return False


# ──────────────────────────────────────────────
# 滑动窗口统计
# ──────────────────────────────────────────────

class SlidingWindowStats:
    """滑动窗口统计器

    【简易】deque(maxlen=N) 自动淘汰旧数据，O(1) 更新
    """

    def __init__(self, window_size: int):
        self.window: deque = deque(maxlen=window_size)
        self.total_processed = 0
        self.total_negative = 0

    def add(self, metric: Dict[str, Any], is_negative: bool) -> None:
        """添加一个样本"""
        self.window.append({
            "metric": metric,
            "is_negative": is_negative,
        })
        self.total_processed += 1
        if is_negative:
            self.total_negative += 1

    def stats(self) -> Dict[str, Any]:
        """计算当前窗口统计"""
        if not self.window:
            return {
                "window_size": 0,
                "negative_count": 0,
                "negative_ratio": 0.0,
                "avg_duration_ms": 0.0,
                "p99_duration_ms": 0.0,
                "avg_stddev": 0.0,
            }

        negative_count = sum(1 for w in self.window if w["is_negative"])
        durations = [w["metric"]["duration_ms"] for w in self.window]
        stddevs = [w["metric"]["score_stddev"] for w in self.window]

        # P99 计算（简化：取排序后第 99 百分位）
        sorted_durations = sorted(durations)
        p99_idx = int(len(sorted_durations) * 0.99)
        p99 = sorted_durations[p99_idx] if sorted_durations else 0.0

        return {
            "window_size": len(self.window),
            "negative_count": negative_count,
            "negative_ratio": negative_count / len(self.window),
            "avg_duration_ms": sum(durations) / len(durations),
            "p99_duration_ms": p99,
            "avg_stddev": sum(stddevs) / len(stddevs),
        }


# ──────────────────────────────────────────────
# 告警决策
# ──────────────────────────────────────────────

def decide_alert(
    stats: Dict[str, Any],
    warn_threshold: float,
    critical_threshold: float,
    int8_recommend_ratio: float,
    p99_slo_ms: float,
    is_windows: bool,
) -> Dict[str, Any]:
    """根据统计结果决策告警级别

    【不易】仅输出建议，不执行切换
    Returns:
        {
            "level": "OK" | "WARNING" | "CRITICAL",
            "message": str,
            "switch_cmd": str | None,
        }
    """
    neg_ratio = stats["negative_ratio"]
    p99 = stats["p99_duration_ms"]

    # 切换命令（按平台）
    fp32_cmd = SWITCH_TO_FP32_CMD_PS if is_windows else SWITCH_TO_FP32_CMD_BASH
    int8_cmd = SWITCH_TO_INT8_CMD_PS if is_windows else SWITCH_TO_INT8_CMD_BASH

    # CRITICAL：负样本占比极高 或 P99 超 SLO
    if neg_ratio > critical_threshold:
        return {
            "level": "🔴 CRITICAL",
            "message": f"负样本占比 {neg_ratio:.1%} > {critical_threshold:.0%}，建议切换 FP32",
            "switch_cmd": fp32_cmd,
        }
    if p99 > p99_slo_ms and stats["window_size"] >= 20:
        return {
            "level": "🔴 CRITICAL",
            "message": f"P99 {p99:.0f}ms > SLO {p99_slo_ms}ms，检查并发或切换更轻量模型",
            "switch_cmd": None,
        }

    # WARNING：负样本占比偏高
    if neg_ratio > warn_threshold:
        return {
            "level": "🟡 WARNING",
            "message": f"负样本占比 {neg_ratio:.1%} > {warn_threshold:.0%}，关注流量质量",
            "switch_cmd": fp32_cmd,
        }

    # OK：负样本占比低，INT8 足够
    if neg_ratio < int8_recommend_ratio:
        return {
            "level": "🟢 OK",
            "message": f"负样本占比 {neg_ratio:.1%} < {int8_recommend_ratio:.0%}，INT8 模式足够",
            "switch_cmd": None,
        }

    # 默认 OK
    return {
        "level": "🟢 OK",
        "message": "INT8 模式运行正常",
        "switch_cmd": None,
    }


# ──────────────────────────────────────────────
# 输出格式化
# ──────────────────────────────────────────────

def format_alert(
    stats: Dict[str, Any],
    alert: Dict[str, Any],
    total_processed: int,
    total_negative: int,
) -> str:
    """格式化告警输出"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    lines = [
        f"[{timestamp}] {alert['level']} | {alert['message']}",
        f"  窗口: {stats['window_size']} | 负样本: {stats['negative_count']} ({stats['negative_ratio']:.1%})",
        f"  P99: {stats['p99_duration_ms']:.0f}ms | 平均: {stats['avg_duration_ms']:.0f}ms",
        f"  avg_stddev: {stats['avg_stddev']:.4f} | 累计: {total_processed} 请求, {total_negative} 负样本",
    ]
    if alert.get("switch_cmd"):
        lines.append(f"  切换命令（人工执行）: {alert['switch_cmd']}")
    return "\n".join(lines)


# ──────────────────────────────────────────────
# 监控主循环
# ──────────────────────────────────────────────

def monitor_realtime(
    log_file: str,
    window_size: int,
    low_score_threshold: float,
    warn_threshold: float,
    critical_threshold: float,
    int8_recommend_ratio: float,
    p99_slo_ms: float,
    is_windows: bool,
) -> int:
    """实时监控模式（tail -f 风格）

    【不易】不修改日志文件，只读取
    【变易】文件增长时自动读取新行
    """
    log_path = Path(log_file)
    if not log_path.exists():
        print(f"❌ 日志文件不存在: {log_file}")
        print("   请先启动服务并重定向 stderr: python -m agent.app_server 2> reranker.log")
        return 2

    stats = SlidingWindowStats(window_size)

    print("=" * 72)
    print("Reranker 负样本比例实时监控")
    print("=" * 72)
    print(f"日志文件: {log_file}")
    print(f"窗口大小: {window_size}")
    print(f"低分阈值: top_score < {low_score_threshold}")
    print(f"告警阈值: WARN > {warn_threshold:.0%}, CRITICAL > {critical_threshold:.0%}")
    print(f"P99 SLO: {p99_slo_ms}ms")
    print(f"平台: {'Windows' if is_windows else 'Linux'}")
    print("=" * 72)
    print("等待 rerank.completed 日志...\n")

    # tail -f 风格：跳到文件末尾，循环读取新行
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        f.seek(0, 2)  # 跳到文件末尾
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.1)
                continue

            log = parse_log_line(line)
            if log is None:
                continue

            metric = extract_rerank_metric(log)
            if metric is None:
                continue

            is_neg = is_negative_sample(metric, low_score_threshold)
            stats.add(metric, is_neg)

            # 窗口填满后输出统计
            if stats.total_processed >= 10:  # 至少 10 个样本才输出
                window_stats = stats.stats()
                alert = decide_alert(
                    window_stats,
                    warn_threshold,
                    critical_threshold,
                    int8_recommend_ratio,
                    p99_slo_ms,
                    is_windows,
                )
                print(format_alert(
                    window_stats, alert,
                    stats.total_processed, stats.total_negative,
                ))
                print("-" * 72)


def generate_report(
    log_file: str,
    window_size: int,
    low_score_threshold: float,
    warn_threshold: float,
    critical_threshold: float,
    int8_recommend_ratio: float,
    p99_slo_ms: float,
    is_windows: bool,
) -> int:
    """一次性报告模式（分析历史日志）"""
    log_path = Path(log_file)
    if not log_path.exists():
        print(f"❌ 日志文件不存在: {log_file}")
        return 2

    stats = SlidingWindowStats(window_size)
    all_metrics: List[Dict[str, Any]] = []

    print("=" * 72)
    print("Reranker 负样本比例分析报告")
    print("=" * 72)
    print(f"日志文件: {log_file}")
    print(f"低分阈值: top_score < {low_score_threshold}")
    print("=" * 72)

    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            log = parse_log_line(line)
            if log is None:
                continue
            metric = extract_rerank_metric(log)
            if metric is None:
                continue

            is_neg = is_negative_sample(metric, low_score_threshold)
            stats.add(metric, is_neg)
            all_metrics.append({**metric, "is_negative": is_neg})

    if not all_metrics:
        print("\n❌ 未找到 rerank.completed 日志")
        return 1

    # 整体统计
    total = len(all_metrics)
    total_neg = sum(1 for m in all_metrics if m["is_negative"])
    durations = [m["duration_ms"] for m in all_metrics]
    stddevs = [m["score_stddev"] for m in all_metrics]

    sorted_durations = sorted(durations)
    p50 = sorted_durations[int(len(sorted_durations) * 0.5)]
    p99 = sorted_durations[int(len(sorted_durations) * 0.99)]

    print(f"\n📊 整体统计")
    print(f"  总请求数: {total}")
    print(f"  负样本数: {total_neg} ({total_neg/total:.1%})")
    print(f"  延迟 P50: {p50:.0f}ms")
    print(f"  延迟 P99: {p99:.0f}ms (SLO: {p99_slo_ms}ms {'✅' if p99 <= p99_slo_ms else '❌'})")
    print(f"  平均 stddev: {sum(stddevs)/len(stddevs):.4f}")

    # 最近窗口统计
    window_stats = stats.stats()
    print(f"\n📊 最近 {window_size} 请求（滑动窗口）")
    print(f"  窗口大小: {window_stats['window_size']}")
    print(f"  负样本占比: {window_stats['negative_ratio']:.1%}")

    alert = decide_alert(
        window_stats,
        warn_threshold,
        critical_threshold,
        int8_recommend_ratio,
        p99_slo_ms,
        is_windows,
    )
    print(f"\n🎯 建议")
    print(f"  级别: {alert['level']}")
    print(f"  信息: {alert['message']}")
    if alert.get("switch_cmd"):
        print(f"  切换命令（人工执行）:")
        print(f"    {alert['switch_cmd']}")

    # 时间分布（按 10% 分段统计）
    print(f"\n📈 负样本时间分布")
    segment_size = max(total // 10, 1)
    for i in range(0, total, segment_size):
        segment = all_metrics[i:i+segment_size]
        seg_neg = sum(1 for m in segment if m["is_negative"])
        seg_ratio = seg_neg / len(segment) if segment else 0
        bar = "█" * int(seg_ratio * 20)
        print(f"  [{i:4d}-{i+len(segment):4d}] {seg_ratio:5.1%} {bar}")

    return 0


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reranker 负样本比例监控 + 切换建议",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 实时监控
  python scripts/monitor_reranker_neg_sample.py --log reranker.log

  # 一次性报告
  python scripts/monitor_reranker_neg_sample.py --log reranker.log --report

  # 自定义阈值
  python scripts/monitor_reranker_neg_sample.py --log reranker.log --window 200 --warn 0.3 --critical 0.5
""",
    )
    parser.add_argument(
        "--log", required=True,
        help="reranker 日志文件路径（stderr 重定向文件）",
    )
    parser.add_argument(
        "--report", action="store_true",
        help="一次性报告模式（分析历史日志后退出）",
    )
    parser.add_argument(
        "--window", type=int, default=DEFAULT_WINDOW_SIZE,
        help=f"滑动窗口大小（默认 {DEFAULT_WINDOW_SIZE}）",
    )
    parser.add_argument(
        "--low-score", type=float, default=DEFAULT_LOW_SCORE_THRESHOLD,
        help=f"低分阈值 top_score < 此值视为负样本（默认 {DEFAULT_LOW_SCORE_THRESHOLD}）",
    )
    parser.add_argument(
        "--warn", type=float, default=DEFAULT_WARN_THRESHOLD,
        help=f"WARNING 阈值：负样本占比超过此值（默认 {DEFAULT_WARN_THRESHOLD:.0%}）",
    )
    parser.add_argument(
        "--critical", type=float, default=DEFAULT_CRITICAL_THRESHOLD,
        help=f"CRITICAL 阈值：负样本占比超过此值（默认 {DEFAULT_CRITICAL_THRESHOLD:.0%}）",
    )
    parser.add_argument(
        "--int8-recommend", type=float, default=DEFAULT_INT8_RECOMMEND_RATIO,
        help=f"INT8 推荐阈值：负样本占比低于此值时 INT8 足够（默认 {DEFAULT_INT8_RECOMMEND_RATIO:.0%}）",
    )
    parser.add_argument(
        "--p99-slo", type=float, default=DEFAULT_P99_SLO_MS,
        help=f"P99 延迟 SLO（ms，默认 {DEFAULT_P99_SLO_MS}）",
    )
    args = parser.parse_args()

    is_windows = os.name == "nt"

    if args.report:
        return generate_report(
            args.log, args.window, args.low_score,
            args.warn, args.critical, args.int8_recommend,
            args.p99_slo, is_windows,
        )
    else:
        return monitor_realtime(
            args.log, args.window, args.low_score,
            args.warn, args.critical, args.int8_recommend,
            args.p99_slo, is_windows,
        )


if __name__ == "__main__":
    sys.exit(main())
