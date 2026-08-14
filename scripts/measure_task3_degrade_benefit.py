#!/usr/bin/env python3
"""任务 3 降级收益测量：量化降级路径「挽回损失」

场景模拟：Critic 主路径耗时 50ms，故障注入后触发降级短路。
统计口径（[M6]）：
- degraded_calls_avoided：降级短路避免的主路径调用数
- degraded_fallbacks_used：实际用到 fallback 的次数
- saved_latency_ms：fallback 相对主路径的耗时差（累计挽回延迟）

用法: python scripts/measure_task3_degrade_benefit.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.graceful_degrade import (
    GracefulDegrade,
    DegradeConfig,
    DegradeModule,
)

MAIN_LATENCY_MS = 50.0   # 模拟主路径耗时
WARMUP_OK = 10           # 主路径成功次数（建立成功基线）
FAIL_COUNT = 40          # 故障注入次数（触发降级短路）


def main() -> None:
    # cache_ttl_seconds=0：禁用缓存，确保故障注入真实进入降级判定链路
    # （否则 warmup 成功后缓存命中会绕过降级路径）
    degrade = GracefulDegrade(DegradeConfig(max_retries=1, cache_ttl_seconds=0))

    def slow_main():
        time.sleep(MAIN_LATENCY_MS / 1000.0)
        return "ok"

    def fast_fallback():
        return "fallback"

    def failing_main():
        time.sleep(MAIN_LATENCY_MS / 1000.0)
        raise RuntimeError("critic service down")

    # 阶段 1：主路径成功（建立主延迟基线）
    for _ in range(WARMUP_OK):
        time.sleep(0.02)  # 规避 Windows 时钟 tick（~15.6ms）下 ttl=0 缓存的短暂有效窗口
        degrade.with_degrade(module=DegradeModule.CRITIC, func=slow_main)

    # 阶段 2：故障注入——主路径持续失败，错误率跨过阈值后降级短路
    degrade_count = 0
    for _ in range(FAIL_COUNT):
        time.sleep(0.02)
        degrade.with_degrade(
            module=DegradeModule.CRITIC,
            func=failing_main,
            fallback=fast_fallback,
        )
        degrade_count += 1

    m = degrade.get_metrics()
    entries = [e for e in m.degrade_history if e["module"] == "critic"]
    total_saved_ms = sum(e["saved_latency_ms"] for e in entries)
    avg_saved_ms = (
        total_saved_ms / len(entries) if entries else 0.0
    )

    print("=" * 60)
    print("任务 3 降级收益测量（模拟 Critic 主路径 50ms）")
    print("=" * 60)
    print(f"主路径成功基线        : {WARMUP_OK} 次")
    print(f"故障注入            : {FAIL_COUNT} 次")
    print(f"降级短路线路计数      : {m.degraded_calls_avoided} 次")
    print(f"实际使用 fallback    : {m.degraded_fallbacks_used} 次")
    print(f"degrade_history 条目 : {len(entries)} 条 (module=critic)")
    print(f"累计挽回延迟         : {total_saved_ms:.1f} ms")
    print(f"平均单次挽回延迟      : {avg_saved_ms:.2f} ms")
    print(f"等价说明             : 若未降级，{m.degraded_calls_avoided} 次主路径调用"
          f"将耗时约 {m.degraded_calls_avoided * MAIN_LATENCY_MS:.0f} ms")
    print("=" * 60)


if __name__ == "__main__":
    main()
