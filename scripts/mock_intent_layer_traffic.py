#!/usr/bin/env python3
"""模拟意图识别四层流量并验证 /metrics 指标输出（任务5 验证脚本）

启动 prometheus_client HTTP server，按 35%/55%/10% 标准分布触发
record_intent_layer，拉取 /metrics 自动校验：
  - 四层（rule/semantic/llm/reject）Counter 均有计数且 > 0
  - ratio 之和 ≈ 1.0
  - 主层占比接近 35%/55%/10% 标准

用法:
    python scripts/mock_intent_layer_traffic.py
    python scripts/mock_intent_layer_traffic.py --duration 15 --port 8001

【不易】独立脚本，不污染主链路；复用 agent.monitoring.prometheus 模块级指标
【简易】自包含 sys.path 处理，从项目根目录或 scripts/ 均可运行
"""

import argparse
import os
import sys
import threading
import time
import urllib.request
from collections import defaultdict

# [简易] 自包含 sys.path 处理：允许从 scripts/ 目录直接运行
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from prometheus_client import start_http_server  # noqa: E402

from agent.monitoring.prometheus import (  # noqa: E402
    record_intent_layer,
    reset_intent_layer_counts,
)


# 流量分布：模拟 35%/55%/10% 标准 + 少量 template/reject 验证全覆盖
# 【变易】分布可调整；保持 rule/semantic/llm 主比例接近标准
TRAFFIC_DISTRIBUTION = {
    "rule": 35,
    "semantic": 55,
    "llm": 10,
    "template": 2,   # 少量，验证 template 层埋点
    "reject": 1,     # 少量，验证 reject 层埋点
}


def _generate_traffic(stop_event: threading.Event, distribution: dict,
                      interval: float = 0.05):
    """按 distribution 比例循环生成流量"""
    layers = []
    for layer, count in distribution.items():
        layers.extend([layer] * count)
    total_per_round = len(layers)
    rounds = 0
    while not stop_event.is_set():
        for layer in layers:
            if stop_event.is_set():
                break
            record_intent_layer(layer)
        rounds += 1
        time.sleep(interval)
    return rounds, rounds * total_per_round


def _fetch_metrics(port: int) -> str:
    """拉取 /metrics 文本"""
    with urllib.request.urlopen(
        "http://localhost:%d/metrics" % port, timeout=5
    ) as resp:
        return resp.read().decode("utf-8")


def _parse_intent_metrics(metrics_text: str) -> dict:
    """解析 yunshu_intent_layer_* 指标"""
    counters = defaultdict(float)
    ratios = defaultdict(float)
    for line in metrics_text.splitlines():
        if line.startswith("yunshu_intent_layer_total{"):
            # yunshu_intent_layer_total{layer="rule"} 35.0
            label = line.split('layer="')[1].split('"')[0]
            counters[label] += float(line.split()[-1])
        elif line.startswith("yunshu_intent_layer_ratio{"):
            label = line.split('layer="')[1].split('"')[0]
            ratios[label] = float(line.split()[-1])
    return {"counters": dict(counters), "ratios": dict(ratios)}


def _verify(metrics: dict, expected_layers: set) -> dict:
    """校验指标符合预期，返回 {passed, checks[]}"""
    result = {"passed": True, "checks": []}
    actual_layers = set(metrics["counters"].keys())

    # 检查1: 所有预期层均有计数
    missing = expected_layers - actual_layers
    if missing:
        result["passed"] = False
        result["checks"].append("FAIL 缺失层计数: %s" % sorted(missing))
    else:
        result["checks"].append("OK 所有预期层均有计数: %s" % sorted(actual_layers))

    # 检查2: Counter 均大于 0
    zero_layers = [l for l, v in metrics["counters"].items() if v <= 0]
    if zero_layers:
        result["passed"] = False
        result["checks"].append("FAIL 计数为 0 的层: %s" % zero_layers)
    else:
        result["checks"].append("OK 所有层 Counter > 0")

    # 检查3: ratio 之和接近 1.0
    if metrics["ratios"]:
        ratio_sum = sum(metrics["ratios"].values())
        if abs(ratio_sum - 1.0) < 0.05:
            result["checks"].append("OK ratio 之和 = %.4f ~= 1.0" % ratio_sum)
        else:
            result["passed"] = False
            result["checks"].append(
                "FAIL ratio 之和 = %.4f 偏离 1.0" % ratio_sum
            )

    # 检查4: 主层占比接近 35%/55%/10% 标准
    for layer, expected in [("rule", 0.35), ("semantic", 0.55), ("llm", 0.10)]:
        actual = metrics["ratios"].get(layer)
        if actual is None:
            result["checks"].append("SKIP %s ratio 缺失" % layer)
        elif abs(actual - expected) < 0.05:
            result["checks"].append(
                "OK %s ratio=%.4f ~= 预期 %.2f" % (layer, actual, expected)
            )
        else:
            result["passed"] = False
            result["checks"].append(
                "FAIL %s ratio=%.4f 偏离预期 %.2f" % (layer, actual, expected)
            )

    return result


def main():
    parser = argparse.ArgumentParser(
        description="模拟意图识别四层流量并验证 /metrics 指标输出"
    )
    parser.add_argument("--duration", type=int, default=8,
                        help="流量生成时长（秒，默认 8）")
    parser.add_argument("--port", type=int, default=8001,
                        help="metrics HTTP 端口（默认 8001，避开生产 8000）")
    parser.add_argument("--interval", type=float, default=0.05,
                        help="每轮流量间隔秒数（默认 0.05）")
    parser.add_argument("--no-reset", action="store_true",
                        help="不重置模块级 ratio 计数（叠加历史值）")
    parser.add_argument("--distribution", type=str, default=None,
                        help="自定义流量分布，格式 layer:count,... "
                             "例 rule:35,semantic:55,llm:10,template:2,reject:1 "
                             "默认 35/55/10 + 少量 template/reject；用于占比偏差二次验证")
    args = parser.parse_args()

    # 【变易】解析 --distribution 覆盖默认分布，便于占比偏差二次验证
    distribution = TRAFFIC_DISTRIBUTION
    if args.distribution:
        distribution = {}
        for item in args.distribution.split(","):
            layer, _, count = item.partition(":")
            layer = layer.strip()
            count = count.strip()
            if not layer or not count:
                print("[mock] 错误: --distribution 格式无效，期望 layer:count")
                return 1
            distribution[layer] = int(count)
        if not distribution:
            print("[mock] 错误: --distribution 解析为空")
            return 1

    if not args.no_reset:
        reset_intent_layer_counts()
        print("[mock] 已重置模块级 ratio 计数视图")

    # 启动 prometheus HTTP server
    start_http_server(args.port)
    print("[mock] Prometheus metrics server started on :%d" % args.port)
    print("[mock] 流量分布: %s" % distribution)
    print("[mock] 开始生成 %ds 流量..." % args.duration)

    stop_event = threading.Event()
    traffic_thread = threading.Thread(
        target=_generate_traffic, args=(stop_event, distribution, args.interval)
    )
    traffic_thread.start()
    traffic_thread.join(timeout=args.duration + 2)
    stop_event.set()
    traffic_thread.join(timeout=2)

    print("[mock] 流量生成完成，等待 0.5s 指标刷新...")
    time.sleep(0.5)

    # 拉取并解析
    metrics_text = _fetch_metrics(args.port)
    metrics = _parse_intent_metrics(metrics_text)
    # 【变易】按实际分布的 count>0 层检查，避免 reject:0 等场景误报"缺失层"
    expected_layers = set(l for l, c in distribution.items() if c > 0)
    verify = _verify(metrics, expected_layers)

    # 打印报告
    print()
    print("=" * 64)
    print("指标验证结果")
    print("=" * 64)
    print("Counter 计数 (yunshu_intent_layer_total):")
    for layer in sorted(metrics["counters"]):
        print('  yunshu_intent_layer_total{layer="%s"} = %.0f'
              % (layer, metrics["counters"][layer]))
    print()
    print("Ratio 占比 (yunshu_intent_layer_ratio):")
    for layer in sorted(metrics["ratios"]):
        print('  yunshu_intent_layer_ratio{layer="%s"} = %.4f'
              % (layer, metrics["ratios"][layer]))
    print()
    print("检查项:")
    for check in verify["checks"]:
        print("  %s" % check)
    print("=" * 64)
    print("[PASS] 验证通过" if verify["passed"] else "[FAIL] 验证失败")
    print("=" * 64)

    # 打印 /metrics 原始片段（intent_layer 相关）
    print()
    print("/metrics 原始输出（intent_layer 相关，含 HELP/TYPE）:")
    for line in metrics_text.splitlines():
        if "yunshu_intent_layer" in line:
            print("  %s" % line)

    return 0 if verify["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
