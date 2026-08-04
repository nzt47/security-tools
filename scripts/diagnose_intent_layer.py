#!/usr/bin/env python3
"""诊断 intent_layer 占比偏差：定位是 orchestrator 埋点漏掉还是流量分布问题

定位逻辑（三步判定）：
1. 查 Prometheus 各 layer Counter + ratio（当前指标）
2. 对比 orchestrator 日志埋点触发次数（metric_recorded / metric_failed）
3. 判定：
   - metric_failed > 0            → 埋点记录失败（prometheus/网络问题，指标丢失）
   - recorded 次数 ≠ Counter 增量  → 指标链路异常（Counter 与日志不一致）
   - recorded 比例 = Prometheus 比例 → 流量分布问题（真实流量偏离 35/55/10 标准）
   - 业务命中事件有但无 metric_recorded → 埋点漏掉（命中点未调用 _record_intent_layer）

用法:
    # 仅查 Prometheus 当前指标
    python scripts/diagnose_intent_layer.py

    # 对比 orchestrator 日志（定位埋点是否漏掉）
    python scripts/diagnose_intent_layer.py --log-file /path/to/orchestrator.log

    # 指定时间窗口的 Counter 增量（对比日志时段）
    python scripts/diagnose_intent_layer.py --log-file orchestrator.log --since 3600
"""

import argparse
import os
import re
import sys
import urllib.request
from collections import defaultdict

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 业务标准占比（用于判定流量分布是否偏离）
STANDARD_RATIO = {"rule": 0.35, "semantic": 0.55, "llm": 0.10}


def _prom_query(host: str, query: str):
    """查询 Prometheus instant，返回 {labels_dict: value} 列表"""
    url = "http://%s/api/v1/query?query=%s" % (host, urllib.parse.quote(query))
    with urllib.request.urlopen(url, timeout=5) as resp:
        import json
        data = json.loads(resp.read().decode("utf-8"))
    return [(r["metric"], float(r["value"][1]))
            for r in data.get("data", {}).get("result", [])]


def _parse_logs(log_file: str, since_sec: int):
    """解析 orchestrator 日志，统计 metric_recorded/failed 各 layer 次数

    【不易】日志格式：log_dict 输出含 'action' 和 'layer' 字段
    """
    recorded = defaultdict(int)
    failed = defaultdict(int)
    if not log_file or not os.path.exists(log_file):
        return recorded, failed, False
    import time
    cutoff = time.time() - since_sec if since_sec else 0
    with open(log_file, encoding="utf-8", errors="replace") as f:
        for line in f:
            # 简单时间过滤：行内有 epoch 时间戳则过滤（否则全量）
            if cutoff:
                ts_match = re.search(r"(\d{10})\b", line)
                if ts_match and int(ts_match.group(1)) < cutoff:
                    continue
            if "orchestrator.intent_layer.metric_recorded" in line:
                m = re.search(r"'layer':\s*'([^']*)'", line)
                if m:
                    recorded[m.group(1)] += 1
            elif "orchestrator.intent_layer.metric_failed" in line:
                m = re.search(r"'layer':\s*'([^']*)'", line)
                if m:
                    failed[m.group(1)] += 1
    return recorded, failed, True


def main():
    parser = argparse.ArgumentParser(
        description="诊断 intent_layer 占比偏差：埋点漏掉 vs 流量分布问题"
    )
    parser.add_argument("--prometheus", default="localhost:9090",
                        help="Prometheus 地址（默认 localhost:9090）")
    parser.add_argument("--log-file", default=None,
                        help="orchestrator 日志文件路径，用于对比埋点触发次数")
    parser.add_argument("--since", type=int, default=0,
                        help="仅统计近 N 秒的日志（默认全量）")
    args = parser.parse_args()

    print("=" * 64)
    print("intent_layer 占比偏差诊断")
    print("=" * 64)

    # ---- 1. Prometheus 当前指标 ----
    print("\n[1] Prometheus 当前指标")
    try:
        counters = _prom_query(args.prometheus, "yunshu_intent_layer_total")
        ratios = _prom_query(args.prometheus, "yunshu_intent_layer_ratio")
    except Exception as e:
        print("  FAIL: 无法查询 Prometheus: %s" % e)
        return 1

    counter_map = {m.get("layer", "?"): v for m, v in counters}
    ratio_map = {m.get("layer", "?"): v for m, v in ratios}
    total = sum(counter_map.values())

    print("  Counter (yunshu_intent_layer_total):")
    for layer in sorted(counter_map):
        print("    %-10s = %.0f" % (layer, counter_map[layer]))
    print("  Ratio (yunshu_intent_layer_ratio):")
    for layer in sorted(ratio_map):
        std = STANDARD_RATIO.get(layer)
        std_str = " (标准 %s)" % std if std else ""
        print("    %-10s = %.4f%s" % (layer, ratio_map[layer], std_str))

    # ---- 2. 对比 orchestrator 日志（如提供） ----
    recorded, failed, log_ok = _parse_logs(args.log_file, args.since)
    print("\n[2] orchestrator 日志埋点触发统计")
    if not log_ok:
        print("  SKIP: 未提供 --log-file 或文件不存在")
        print("  提示: 如需定位埋点是否漏掉，传入 orchestrator 日志路径")
    else:
        rec_total = sum(recorded.values())
        fail_total = sum(failed.values())
        print("  metric_recorded (成功):")
        for layer in sorted(set(list(recorded.keys()) + list(counter_map.keys()))):
            print("    %-10s = %d" % (layer, recorded.get(layer, 0)))
        print("  metric_failed (失败): %d" % fail_total)
        for layer in sorted(failed):
            print("    %-10s = %d" % (layer, failed[layer]))

    # ---- 3. 判定结论 ----
    print("\n[3] 诊断结论")
    print("-" * 64)
    issues = []

    # 判定A: 埋点记录失败
    if log_ok and sum(failed.values()) > 0:
        issues.append("FAIL: 存在埋点记录失败 (metric_failed=%d)，"
                      "prometheus 记录异常导致指标丢失" % sum(failed.values()))

    # 判定B: recorded 次数 vs Counter 增量
    if log_ok and recorded:
        # 注：Counter 是累计值，日志次数是该时段增量；仅当 since 过滤对齐时可比
        if args.since:
            print("  [对比] 近 %ds 日志 recorded vs Counter 增量:" % args.since)
            for layer in sorted(set(list(recorded.keys()) + list(counter_map.keys()))):
                rec = recorded.get(layer, 0)
                print("    %-10s recorded=%d" % (layer, rec))

    # 判定C: 流量分布是否偏离标准
    if ratio_map and total > 0:
        print("  [流量分布] 各层占比 vs 业务标准:")
        deviation = False
        for layer, std in STANDARD_RATIO.items():
            actual = ratio_map.get(layer)
            if actual is None:
                continue
            diff = actual - std
            flag = "OK" if abs(diff) < 0.05 else "DEVIATE"
            if abs(diff) >= 0.05:
                deviation = True
            print("    %-10s 实际=%.2f%% 标准=%.0f%% 偏差=%+.2f%% [%s]"
                  % (layer, actual * 100, std * 100, diff * 100, flag))
        if deviation:
            issues.append("DEVIATE: 流量分布偏离 35/55/10 标准（埋点正常，"
                          "真实流量分布如此）→ 需排查业务流量来源，非埋点问题")

    # 判定D: 埋点漏掉（需日志对比业务命中事件）
    if log_ok and recorded:
        zero_layers = [l for l in STANDARD_RATIO if l not in recorded and l not in counter_map]
        if zero_layers:
            issues.append("FAIL: 业务标准层 %s 在日志和指标中均无记录，"
                          "可能命中点未埋点" % zero_layers)

    if not issues:
        print("  OK: 指标链路正常，流量分布符合业务标准")
    else:
        for issue in issues:
            print("  " + issue)

    print("-" * 64)
    print("\n[排查指引]")
    print("  1. 若 metric_failed>0 → 埋点记录失败：查 prometheus_client 可用性/网络")
    print("  2. 若某层 recorded=0 但有业务命中日志 → 埋点漏掉：检查 orchestrator.py")
    print("     5 处 _record_intent_layer 调用点是否覆盖该层")
    print("  3. 若 recorded 比例 = Prometheus 比例 → 流量分布问题：分析真实请求")
    print("     的 layer 命中分布（非埋点问题）")
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
