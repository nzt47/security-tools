"""v6.5 ONNX 模型版本热更新模拟 + Grafana 面板实时验证

模拟场景:
    1. 阶段 1（0-2min）: model_quantized.onnx 运行，P99 ~258ms（基线）
    2. 阶段 2（2-4min）: 热更新到 model_int8.onnx，P99 ~363ms（劣化 1.4x）
    3. 阶段 3（4-6min）: 回滚到 model_quantized.onnx，P99 恢复 ~258ms

验证目标:
    - Grafana 面板能实时反映 P99 延迟变化（30s 刷新周期）
    - 延迟趋势图能清晰展示版本切换前后的差异
    - P99 SLO 面板在阶段 2 不触发告警（363ms < 500ms SLO）

实现方式:
    【不易】使用 prometheus_client 推送指标到 pushgateway（或本地 exporter）
    【变易】模拟真实流量（2 req/s），每 30s 推送一次指标
    【简易】单脚本独立运行，不依赖真实 ONNX 推理

运行:
    python scripts/simulate_onnx_hot_update.py
    # 同时启动 Grafana，导入 reranker-dashboard.json 观察面板变化

前置条件:
    pip install prometheus-client
"""
from __future__ import annotations

import random
import time
import sys
import os
from datetime import datetime
from typing import List, Dict

try:
    from prometheus_client import (
        Histogram, Counter, Gauge,
        CollectorRegistry, push_to_gateway, start_http_server
    )
except ImportError:
    print("❌ 缺少 prometheus-client，请运行: pip install prometheus-client")
    sys.exit(1)


# ════════════════════════════════════════════════════════════
#  配置
# ════════════════════════════════════════════════════════════

# 模拟参数
SIM_DURATION_PER_PHASE = 120  # 每阶段 120s（2min）
PUSH_INTERVAL = 15            # 每 15s 推送一次指标
TRAFFIC_RATE = 2              # 2 req/s

# 三个阶段的模型配置（基于实测数据）
PHASES = [
    {
        "name": "阶段1: model_quantized.onnx（基线）",
        "variant": "model_quantized.onnx",
        "p50": 180,
        "p95": 230,
        "p99": 258,
        "color": "🟢",
    },
    {
        "name": "阶段2: model_int8.onnx（热更新后劣化）",
        "variant": "model_int8.onnx",
        "p50": 250,
        "p95": 320,
        "p99": 363,
        "color": "🟡",
    },
    {
        "name": "阶段3: 回滚 model_quantized.onnx（恢复）",
        "variant": "model_quantized.onnx",
        "p50": 180,
        "p95": 230,
        "p99": 258,
        "color": "🟢",
    },
]

# Pushgateway 配置（可选，默认用本地 HTTP exporter）
PUSHGATEWAY_URL = os.environ.get("PUSHGATEWAY_URL", "")
EXPORTER_PORT = 9101  # 本地 exporter 端口


# ════════════════════════════════════════════════════════════
#  Prometheus 指标定义（与 reranker.py emit_metric 一致）
# ════════════════════════════════════════════════════════════

def create_registry() -> CollectorRegistry:
    """创建独立的 registry，避免污染全局指标"""
    registry = CollectorRegistry()

    # 延迟直方图（与 reranker.py 的 bucket 对齐）
    duration_hist = Histogram(
        "yunshu_rerank_duration_ms",
        "Rerank duration in milliseconds",
        ["backend", "success"],
        buckets=[50, 100, 200, 300, 400, 500, 750, 1000, 2000, 5000],
        registry=registry,
    )

    # 加载计数器
    load_counter = Counter(
        "yunshu_reranker_load_total",
        "Reranker load count",
        ["backend", "status", "reason"],
        registry=registry,
    )

    # 加载耗时
    load_time = Gauge(
        "yunshu_reranker_load_time_seconds",
        "Reranker load time in seconds",
        ["backend"],
        registry=registry,
    )

    # 降级计数器
    fallback_counter = Counter(
        "yunshu_reranker_fallback_total",
        "Reranker fallback count",
        ["from", "to", "reason"],
        registry=registry,
    )

    # 成功计数器
    completed_counter = Counter(
        "yunshu_reranker_completed_total",
        "Reranker completed count",
        ["backend"],
        registry=registry,
    )

    # 推理失败计数器
    predict_failed_counter = Counter(
        "yunshu_reranker_predict_failed_total",
        "Reranker predict failed count",
        ["backend"],
        registry=registry,
    )

    # 当前 variant（自定义指标，用于面板标注版本切换）
    current_variant = Gauge(
        "yunshu_reranker_current_variant",
        "Current ONNX variant (1=quantized, 2=int8)",
        registry=registry,
    )

    return registry, {
        "duration": duration_hist,
        "load": load_counter,
        "load_time": load_time,
        "fallback": fallback_counter,
        "completed": completed_counter,
        "predict_failed": predict_failed_counter,
        "variant": current_variant,
    }


# ════════════════════════════════════════════════════════════
#  延迟采样生成器（模拟真实分布）
# ════════════════════════════════════════════════════════════

def generate_latency_sample(phase: Dict) -> float:
    """根据阶段的 P50/P95/P99 生成单次延迟采样

    使用对数正态分布模拟真实推理延迟分布:
    - 大部分请求集中在 P50 附近
    - 少量请求在 P95-P99 尾部
    """
    p50 = phase["p50"]
    p99 = phase["p99"]

    # 对数正态分布参数
    import math
    mu = math.log(p50)
    sigma = (math.log(p99) - mu) / 2.326  # 2.326 = P99 对应的 z-score

    # 生成样本
    sample = random.lognormvariate(mu, sigma)
    # 截断到合理范围 [50, 5000]
    return max(50, min(5000, sample))


# ════════════════════════════════════════════════════════════
#  模拟主循环
# ════════════════════════════════════════════════════════════

def run_simulation():
    """执行三阶段热更新模拟"""
    registry, metrics = create_registry()

    # 启动本地 HTTP exporter
    start_http_server(EXPORTER_PORT, registry=registry)
    print(f"✅ Prometheus exporter 已启动: http://localhost:{EXPORTER_PORT}/metrics")
    print(f"   Grafana 数据源配置: Prometheus URL = http://localhost:{EXPORTER_PORT}")
    print()

    print("=" * 70)
    print("  v6.5 ONNX 模型版本热更新模拟")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  总时长: {len(PHASES) * SIM_DURATION_PER_PHASE}s "
          f"({len(PHASES) * SIM_DURATION_PER_PHASE // 60}min)")
    print(f"  流量: {TRAFFIC_RATE} req/s")
    print("=" * 70)

    for phase_idx, phase in enumerate(PHASES):
        print(f"\n{'═' * 70}")
        print(f"  {phase['color']} {phase['name']}")
        print(f"  variant: {phase['variant']}")
        print(f"  预期 P50/P95/P99: {phase['p50']}/{phase['p95']}/{phase['p99']}ms")
        print(f"{'═' * 70}")

        # 模拟模型加载
        variant_id = 1 if "quantized" in phase["variant"] else 2
        metrics["variant"].set(variant_id)
        metrics["load"].labels(backend="onnx", status="success", reason="").inc()
        metrics["load_time"].labels(backend="onnx").set(1.12)

        # 阶段内持续推送指标
        phase_start = time.time()
        sample_count = 0

        while time.time() - phase_start < SIM_DURATION_PER_PHASE:
            # 生成 TRAFFIC_RATE * PUSH_INTERVAL 次请求
            for _ in range(TRAFFIC_RATE * PUSH_INTERVAL):
                latency = generate_latency_sample(phase)
                metrics["duration"].labels(
                    backend="onnx", success="true"
                ).observe(latency)
                metrics["completed"].labels(backend="onnx").inc()
                sample_count += 1

            # 推送到 pushgateway（可选）
            if PUSHGATEWAY_URL:
                try:
                    push_to_gateway(
                        PUSHGATEWAY_URL,
                        job="reranker_hot_update_sim",
                        registry=registry,
                    )
                except Exception as e:
                    print(f"  ⚠️ push_to_gateway 失败: {e}")

            # 打印进度
            elapsed = int(time.time() - phase_start)
            remaining = SIM_DURATION_PER_PHASE - elapsed
            print(f"  [{elapsed:3d}s/{SIM_DURATION_PER_PHASE}s] "
                  f"已生成 {sample_count} 次请求, "
                  f"剩余 {remaining}s")

            time.sleep(PUSH_INTERVAL)

        # 阶段切换提示
        if phase_idx < len(PHASES) - 1:
            next_phase = PHASES[phase_idx + 1]
            print(f"\n  🔄 热更新切换: {phase['variant']} → {next_phase['variant']}")
            print(f"     预期 P99 变化: {phase['p99']}ms → {next_phase['p99']}ms")

    # ── 汇总 ──
    print(f"\n{'=' * 70}")
    print("  模拟完成")
    print(f"{'=' * 70}")
    print(f"  总请求数: {sum(1 for _ in range(0))}")  # placeholder
    print()
    print("  📊 Grafana 面板验证清单:")
    print("  1. 打开 http://localhost:3000（Grafana）")
    print("  2. 导入 monitoring/grafana/dashboards/reranker-dashboard.json")
    print("  3. 数据源选择 Prometheus (http://localhost:9101)")
    print("  4. 观察以下面板变化:")
    print("     ✅ P99 延迟面板: 阶段1~258ms → 阶段2~363ms → 阶段3~258ms")
    print("     ✅ 延迟趋势图: 可见明显的阶梯式变化")
    print("     ✅ P99 SLO 面板: 全程绿色（< 500ms SLO）")
    print("     ✅ QPS 面板: 稳定 2 req/s")
    print()
    print("  ⚠️ 注意: exporter 仍在运行，按 Ctrl+C 退出")

    # 保持 exporter 运行，让用户观察 Grafana
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n✅ 模拟已停止")


# ════════════════════════════════════════════════════════════
#  主函数
# ════════════════════════════════════════════════════════════

def main():
    print("v6.5 ONNX 模型版本热更新模拟脚本")
    print()

    # 检查是否已启动 Grafana
    print("前置条件检查:")
    print(f"  ✅ prometheus_client 已安装")
    print(f"  {'✅' if PUSHGATEWAY_URL else '⚠️'} Pushgateway: "
          f"{PUSHGATEWAY_URL or '未配置（使用本地 exporter）'}")
    print()

    # 设置随机种子（可复现）
    random.seed(42)

    run_simulation()


if __name__ == "__main__":
    main()
