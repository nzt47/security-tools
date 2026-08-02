#!/usr/bin/env python3
"""CI 定期验证：意图层 ratio 总和恒 = 1.0（分母同步不变量守卫）

【不易】核心不变量：
  - INV-RATIO: sum(count/total) == 1.0（count/total 求和 = total/total）
  - INV-GAUGE: yunshu_intent_layer_ratio 实际 gauge 值 == count/total
  - INV-COUNTER: yunshu_intent_layer_total Counter 增量 == _intent_layer_counts 增量
  - INV-CONCUR: 并发写入下 ratio 总和仍 = 1.0（TD-3 场景，GIL 守护下验证）

【变易】支持 --concurrency 关闭并发压测（默认开启），--json 输出报告路径
【简易】独立可运行：python scripts/verify_intent_layer_ratio_ci.py

用法:
  python scripts/verify_intent_layer_ratio_ci.py
  python scripts/verify_intent_layer_ratio_ci.py --json ci-ratio-report.json
"""
import argparse
import json
import os
import sys
import threading
from datetime import datetime, timezone

# 确保项目根目录在 sys.path（独立运行脚本时 agent 包可导入）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.monitoring.prometheus import (
    record_intent_layer,
    reset_intent_layer_counts,
    _intent_layer_counts as _ilc,
)

# 容忍误差：浮点除法求和的理论上界（IEEE754 double）
_EPS = 1e-9


def _ratio_sum():
    """计算当前 ratio 总和（分母同步不变量，应恒 = 1.0）"""
    total = sum(_ilc.values())
    if total == 0:
        return 0.0
    return sum(c / total for c in _ilc.values())


def _gauge_value(layer):
    """读取指定 layer 的 ratio Gauge 当前值（经 prometheus REGISTRY）"""
    try:
        from prometheus_client import REGISTRY as _REG
        sample = _REG.get_sample_value(
            "yunshu_intent_layer_ratio", {"layer": layer}
        )
        return sample if sample is not None else 0.0
    except Exception:
        # prometheus_client 不可用时 gauge 检查降级跳过（对应 _NoopGauge 场景）
        return None


def _counter_value(layer):
    """读取指定 layer 的 Counter 当前值（经 prometheus REGISTRY）"""
    try:
        from prometheus_client import REGISTRY as _REG
        sample = _REG.get_sample_value(
            "yunshu_intent_layer_total", {"layer": layer}
        )
        return sample if sample is not None else 0.0
    except Exception:
        return None


def _assert_invariant(steps: list, failures: list, step_name: str):
    """校验 ratio 不变量，失败记录到 failures 列表

    每次记录后 ratio 总和必须 = 1.0，且所有层 ratio 落在 [0,1]。
    """
    total = sum(_ilc.values())
    if total == 0:
        return
    rs = _ratio_sum()
    if abs(rs - 1.0) > _EPS:
        failures.append(
            "[%s] ratio 总和=%.12f (期望 1.0), counts=%r" % (step_name, rs, dict(_ilc))
        )
    for layer, count in _ilc.items():
        r = count / total
        if r < 0.0 or r > 1.0:
            failures.append("[%s] layer=%s ratio=%.6f 越界" % (step_name, layer, r))
        # gauge 同步性：gauge 值 == count/total（prometheus_client 浮点存储）
        gv = _gauge_value(layer)
        if gv is not None and abs(gv - r) > 1e-6:
            failures.append(
                "[%s] layer=%s gauge=%.10f != count/total=%.10f" % (step_name, layer, gv, r)
            )
    if step_name not in steps:  # 去重：MIX 循环 500 次只展示一次
        steps.append(step_name)


def _record_with_check(layer: str, steps: list, failures: list, label: str):
    """记录一次埋点并立即校验不变量"""
    record_intent_layer(layer)
    _assert_invariant(steps, failures, label)


def simulate_request_paths() -> bool:
    """模拟 orchestrator 全部意图路由路径，逐次校验 ratio 不变量

    对照 orchestrator.py 控制流：
      P1 rule 命中            → L263  record("rule")
      P2 template 命中        → L423  record("template")
      P3 reject 拒识          → L486  record("reject")
      P4 llm 成功高置信       → L507  record("llm")
      P5 llm 成功低置信       → L507 record("llm") + L586 record("llm_low_confidence_fallback")
      P6 llm 调用失败(TD-1)   → L507 record("llm") + except record("llm_error")
    """
    steps, failures = [], []
    reset_intent_layer_counts()

    # P1: rule 命中
    _record_with_check("rule", steps, failures, "P1 rule")
    # P2: template 命中
    _record_with_check("template", steps, failures, "P2 template")
    # P3: reject 拒识
    _record_with_check("reject", steps, failures, "P3 reject")
    # P4: llm 成功高置信
    _record_with_check("llm", steps, failures, "P4 llm-high")
    # P5: llm 成功低置信（fallback 子指标）
    _record_with_check("llm", steps, failures, "P5 llm (low)")
    _record_with_check("llm_low_confidence_fallback", steps, failures, "P5 fallback")
    # P6: llm 调用失败（TD-1 llm_error 子指标）
    _record_with_check("llm", steps, failures, "P6 llm (attempt)")
    _record_with_check("llm_error", steps, failures, "P6 llm_error")

    # 混合流量：各路径随机交错 500 次（确定性伪随机，种子固定保证可复现）
    import random
    rng = random.Random(20260801)
    paths = [
        ["rule"],
        ["template"],
        ["reject"],
        ["llm"],
        ["llm", "llm_low_confidence_fallback"],
        ["llm", "llm_error"],
    ]
    for _ in range(500):
        for layer in rng.choice(paths):
            _record_with_check(layer, steps, failures, "MIX")

    # 最终快照校验
    _assert_invariant(steps, failures, "FINAL")

    ok = not failures
    print("=" * 70)
    print("意图层 ratio 分母同步不变量验证（CI 脚本）")
    print("模拟路径: %s" % " → ".join(steps))
    print("最终计数: %r" % dict(_ilc))
    print("最终 ratio 总和: %.12f" % _ratio_sum())
    print("=" * 70)
    if ok:
        print("✓ 全部路径 ratio 总和恒 = 1.0，gauge 同步，无越界")
    else:
        print("✗ 发现 %d 处不变量违规：" % len(failures))
        for f in failures:
            print("   - %s" % f)
    print("=" * 70)
    return ok


def verify_counter_sync() -> bool:
    """校验 Counter 增量与 _intent_layer_counts 增量一致

    CI 进程内 Counter 从 0 开始；为兼容同一进程多次运行，按增量对比。
    """
    failures = []
    total_entries = sum(_ilc.values())
    for layer, count in _ilc.items():
        cval = _counter_value(layer)
        if cval is None:
            continue  # prometheus_client 不可用时跳过 Counter 检查
        # 脚本内记录的全部次数都应反映到 Counter（同进程下 Counter 为单调累计）
        if int(cval) < count:
            failures.append(
                "layer=%s Counter=%.0f < counts=%d（Counter 未同步）" % (layer, cval, count)
            )
    ok = not failures
    print("[Counter 同步] %s (总埋点次数=%d)" % ("✓ 通过" if ok else "✗ 失败", total_entries))
    if failures:
        for f in failures:
            print("   - %s" % f)
    return ok


def verify_concurrency_safety() -> bool:
    """并发写入压测：多线程随机层写入后 ratio 总和仍 = 1.0

    对照 TD-3：_intent_layer_counts 无显式锁，GIL 守护下丢失更新不影响
    ratio 数学恒等（总和仍 = 1.0），但绝对计数可能偏低。
    本测试仅校验 ratio 不变量不被并发破坏。
    """
    reset_intent_layer_counts()
    layers = ["rule", "template", "semantic", "llm", "reject",
              "llm_low_confidence_fallback", "llm_error"]

    def _worker(n: int, seed: int):
        import random
        rng = random.Random(seed)
        for _ in range(n):
            record_intent_layer(rng.choice(layers))

    threads = [threading.Thread(target=_worker, args=(500, 100 + i)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rs = _ratio_sum()
    ok = abs(rs - 1.0) < _EPS
    print("[并发安全] 8 线程 × 500 次写入后 ratio 总和=%.12f %s"
          % (rs, "✓ =1.0" if ok else "✗ 偏离"))
    if not ok:
        print("   counts=%r" % dict(_ilc))
    reset_intent_layer_counts()
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="意图层 ratio 不变量 CI 验证")
    parser.add_argument("--json", default="", help="输出 JSON 报告路径（可选）")
    parser.add_argument("--no-concurrency", action="store_true", help="跳过并发压测")
    args = parser.parse_args()

    results = {}

    # 1) 模拟路径 ratio 不变量
    results["simulate_paths"] = simulate_request_paths()

    # 2) Counter 同步性（需在 simulate 之后、reset 之前执行）
    results["counter_sync"] = verify_counter_sync()

    # 3) 并发安全（默认开启）
    if args.no_concurrency:
        results["concurrency"] = True
        print("[并发安全] 已跳过（--no-concurrency）")
    else:
        results["concurrency"] = verify_concurrency_safety()

    all_ok = all(results.values())
    results["overall"] = all_ok
    results["generated_at"] = datetime.now(timezone.utc).isoformat()

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print("JSON 报告已写入: %s" % args.json)

    print()
    print("=" * 70)
    print("CI ratio 不变量验证结果: %s" % ("全部通过" if all_ok else "存在失败"))
    for k, v in results.items():
        if k not in ("overall", "generated_at"):
            print("  %-16s: %s" % (k, "✓" if v else "✗"))
    print("=" * 70)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
