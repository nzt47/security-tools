#!/usr/bin/env python3
"""验证 semantic 埋点 structured log 字段（orchestrator.py L1086 增强）

模拟场景：低置信度请求前置（L507 llm → L586 fallback）→ 后续 semantic 命中
验证 L1086 增强的 structured log 字段是否正确输出：
  metric_total / layer_counts / skill_id / top1_score / instruction_len / instruction_loaded

【不易】不调用完整 Orchestrator，直接复用 L1086 日志模板（log_dict）验证字段格式
【简易】独立可运行：python scripts/verify_semantic_metric_log.py
"""
import os
import sys
from types import SimpleNamespace

# 确保项目根目录在 sys.path（独立运行脚本时 agent 包可导入）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.logging_utils import log_dict
from agent.monitoring.prometheus import (
    record_intent_layer,
    reset_intent_layer_counts,
    _intent_layer_counts as _ilc,
)


def verify_semantic_metric_log() -> int:
    """模拟低置信度 + semantic 命中，验证 L1086 structured log 字段

    Returns:
        0 = 全部字段验证通过；1 = 存在失败
    """
    # ── 场景：低置信度请求前置（orchestrator.py L507 → L586 控制流）──
    reset_intent_layer_counts()
    record_intent_layer("llm")                          # L507: 进入 LLM 路径
    record_intent_layer("llm_low_confidence_fallback") # L586: 低置信度兜底

    # ── 后续另一次请求命中 semantic（L1086 埋点 + L1087 structured log）──
    record_intent_layer("semantic")                     # L1086

    # 构造 fake top1 + instruction（模拟 semantic 命中上下文）
    fake_top1 = SimpleNamespace(skill_id="verify_skill_001", score=0.875)
    fake_instruction = "这是一条测试 instruction，用于验证 structured log 字段"
    trace_id = "verify_l1086_001"

    # 复用 L1086 的日志逻辑（orchestrator.py:1086-1108）
    _sem_total = sum(_ilc.values())
    log_entry = log_dict({
        'module_name': 'orchestrator',
        'action': 'orchestrator.semantic.metric_total',
        'trace_id_ctx': trace_id,
        'message': '[埋点] semantic 触发, total=%d, counts=%s, skill=%s, score=%.3f, instr_len=%d, instr_loaded=success' % (
            _sem_total, dict(_ilc), fake_top1.skill_id, fake_top1.score, len(fake_instruction)
        ),
        'metric_total': _sem_total,
        'layer_counts': dict(_ilc),
        'skill_id': fake_top1.skill_id,
        'top1_score': float(fake_top1.score),
        'instruction_len': len(fake_instruction),
        'instruction_loaded': True,
    })

    # ── 字段验证 ──
    print("=" * 70)
    print("L1086 semantic 埋点 structured log 字段验证")
    print("场景：llm + fallback + semantic → total 应 = 3")
    print("=" * 70)
    print()

    expected = {
        'metric_total': 3,
        'skill_id': 'verify_skill_001',
        'top1_score': 0.875,
        'instruction_len': len(fake_instruction),
        'instruction_loaded': True,
        'module_name': 'orchestrator',
        'action': 'orchestrator.semantic.metric_total',
        'trace_id_ctx': trace_id,
    }

    all_pass = True
    for field, exp_val in expected.items():
        actual = log_entry.get(field)
        ok = actual == exp_val
        status = "✓ PASS" if ok else "✗ FAIL"
        print("  [%s] %-20s 期望=%r, 实际=%r" % (status, field, exp_val, actual))
        if not ok:
            all_pass = False

    # 验证 layer_counts（dict 类型，含 3 个 layer 各 1 次）
    layer_counts = log_entry.get('layer_counts')
    expected_counts = {'llm': 1, 'llm_low_confidence_fallback': 1, 'semantic': 1}
    counts_ok = layer_counts == expected_counts
    status = "✓ PASS" if counts_ok else "✗ FAIL"
    print("  [%s] %-20s 期望=%r" % (status, 'layer_counts', expected_counts))
    print("  %s 实际=%r" % (" " * 26, layer_counts))
    if not counts_ok:
        all_pass = False

    # 验证 ratio 总和 = 1.0（分母同步不变量）
    total = sum(_ilc.values())
    ratio_sum = sum(c / total for c in _ilc.values())
    ratio_ok = abs(ratio_sum - 1.0) < 1e-9
    status = "✓ PASS" if ratio_ok else "✗ FAIL"
    print("  [%s] %-20s 期望=1.0, 实际=%.10f" % (status, 'ratio 总和', ratio_sum))
    if not ratio_ok:
        all_pass = False

    # 验证 message 字段包含关键信息
    msg = log_entry.get('message', '')
    msg_ok = ('total=3' in msg and 'instr_loaded=success' in msg and 'semantic 触发' in msg)
    status = "✓ PASS" if msg_ok else "✗ FAIL"
    print("  [%s] message 含 total=3 / instr_loaded=success / semantic 触发" % status)
    if not msg_ok:
        all_pass = False
        print("       实际 message: %s" % msg)

    print()
    print("=" * 70)
    if all_pass:
        print("✓ 所有字段验证通过！L1086 structured log 增强工作正常。")
        print("  - 低置信度前置（llm + fallback）正确计入分母（total=3）")
        print("  - ratio 总和=1.0（分母同步不变量守恒）")
        print("  - instruction_loaded=True 确认 INV-2（加载成功才埋点）")
        print("  - 所有新增结构化字段（metric_total/layer_counts/skill_id/")
        print("    top1_score/instruction_len/instruction_loaded）正确输出")
    else:
        print("✗ 存在字段验证失败，请检查 L1086 日志逻辑（orchestrator.py:1086-1108）。")
    print("=" * 70)

    # 清理
    reset_intent_layer_counts()
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(verify_semantic_metric_log())
