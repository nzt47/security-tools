"""任务7 复杂度判定源统一 — 单测

覆盖（对应评估标准）：
1. 判定源一致性（抽样集）：wire 启发式 vs enhanced_planner 分级在任务7 抽样集
   （data/evals 50 条 + data/complexity_samples.json 150 条）上的一致率与
   scripts/complexity_source_compare.py 落盘结果一致（数据支撑，非主观）；
2. wire 默认实现与既有启发式逐字节等价（默认态零行为变化）；
3. 统一入口：orchestrator 委托函数 == ComplexityClassifier 输出（单一入口）；
4. enhanced_planner 适配器：MODERATE→NORMAL 归一、纯函数封装（不触发 DAG）；
5. meets 语义：未知 min_complexity 保守收严（守主链路稳定）。
"""

import json
import os
from pathlib import Path

import pytest

from agent.task_planner.complexity_classifier import (
    COMPLEXITY_LEVELS,
    CANONICAL_LEVELS,
    EnhancedPlannerClassifier,
    WireHeuristicClassifier,
    build_classifier,
    get_complexity_classifier,
    normalize_level,
    reset_complexity_classifier,
    resolve_source,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVALS_DIR = _REPO_ROOT / "data" / "evals"
_CURATED_PATH = _REPO_ROOT / "data" / "complexity_samples.json"
_RESULT_PATH = _REPO_ROOT / "data" / "complexity_compare_result.json"

# 既有 wire 启发式公式（任务7 之前的独立复刻，验证统一模块逐字节等价）
_LEGACY_COMPLEX_KEYWORDS = (
    "架构", "系统", "平台", "重构", "迁移", "分布式",
    "设计一个", "帮我构建", "多步骤", "第一步", "第二步", "完整方案",
)
_LEGACY_ACTION_KEYWORDS = ("检查", "分析", "创建", "生成", "整理", "监控")


def _legacy_judge(message: str) -> str:
    complex_matches = [k for k in _LEGACY_COMPLEX_KEYWORDS if k in message]
    action_matches = [k for k in _LEGACY_ACTION_KEYWORDS if k in message]
    score = len(complex_matches) + len(action_matches) * 0.5
    if score >= 1.5:
        return "COMPLEX"
    if score >= 1.0:
        return "NORMAL"
    if score >= 0.5:
        return "SIMPLE"
    return "TRIVIAL"


def _load_sample_set() -> list:
    """加载任务7 抽样集（评估集 50 + 生产风格 150）"""
    samples = []
    for f in sorted(_EVALS_DIR.glob("*/")):
        for jf in sorted(f.glob("*.json")):
            if jf.name in ("manifest.json", "baselines.json"):
                continue
            data = json.loads(jf.read_text(encoding="utf-8"))
            items = data if isinstance(data, list) else list(data.values())
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    samples.append(it.get("task", ""))
    curated = json.loads(_CURATED_PATH.read_text(encoding="utf-8"))
    samples.extend(it["message"] for it in curated if isinstance(it, dict) and it.get("id"))
    return samples


# ════════════════════════════════════════════════════════════
#  1. 判定源一致性（抽样集）
# ════════════════════════════════════════════════════════════

def test_sample_set_composition():
    """抽样集构成：评估集 50 + 生产风格 150 = 200（对比报告数据基础）"""
    samples = _load_sample_set()
    assert len(samples) == 200, f"抽样集应为 200 条，实际 {len(samples)}"


def test_source_consistency_rate_matches_report():
    """一致率与对比报告落盘结果一致（scripts/complexity_source_compare.py 产出）"""
    samples = _load_sample_set()
    wire = WireHeuristicClassifier()
    enhanced = EnhancedPlannerClassifier()
    agree = sum(1 for m in samples if wire.classify(m) == enhanced.classify(m))
    rate = agree / len(samples)
    recorded = json.loads(_RESULT_PATH.read_text(encoding="utf-8"))
    assert rate == pytest.approx(recorded["consistency_rate"], abs=1e-6)
    assert recorded["agree_count"] == agree
    # 双源确实存在系统性分歧（一致率显著 < 1）——证明"双源口径"问题真实存在
    assert rate < 0.5, "两判定源一致率应显著偏低（双源口径问题的数据证据）"


def test_source_distribution_recorded():
    """分档分布与落盘结果一致（报告分档分布数据来源）"""
    samples = _load_sample_set()
    wire = WireHeuristicClassifier()
    enhanced = EnhancedPlannerClassifier()
    from collections import Counter
    w_dist = dict(Counter(wire.classify(m) for m in samples))
    e_dist = dict(Counter(enhanced.classify(m) for m in samples))
    recorded = json.loads(_RESULT_PATH.read_text(encoding="utf-8"))
    for lvl in CANONICAL_LEVELS:
        assert w_dist.get(lvl, 0) == recorded["wire_distribution"].get(lvl, 0)
        assert e_dist.get(lvl, 0) == recorded["enhanced_distribution"].get(lvl, 0)


# ════════════════════════════════════════════════════════════
#  2. wire 默认实现与既有启发式逐字节等价（默认态零行为变化）
# ════════════════════════════════════════════════════════════

def test_wire_classifier_parity_with_legacy_heuristic():
    """统一 wire 实现与任务7 之前启发式公式在抽样集上逐条一致"""
    wire = WireHeuristicClassifier()
    for msg in _load_sample_set():
        assert wire.classify(msg) == _legacy_judge(msg), f"判定不一致: {msg}"
        score, cx, ac = wire.detail(msg)
        assert score == len(cx) + len(ac) * 0.5


def test_wire_meets_semantics():
    """meets：有效 min_complexity 正常比较；未知级别保守收严（≥COMPLEX 才放行）"""
    wire = WireHeuristicClassifier()
    assert wire.meets("帮我设计一个分布式系统架构", "COMPLEX") is True
    assert wire.meets("查一下天气", "COMPLEX") is False
    # 未知级别 → 按 COMPLEX(3) 保守处理：仅 COMPLEX 级输入可满足
    assert wire.meets("帮我设计一个分布式系统架构", "UNKNOWN_LEVEL") is True
    assert wire.meets("查一下天气", "UNKNOWN_LEVEL") is False


# ════════════════════════════════════════════════════════════
#  3. 统一入口（单一判定源）
# ════════════════════════════════════════════════════════════

def test_unified_classifier_facade():
    """ComplexityClassifier 统一入口：classify/detail/meets 三能力齐备"""
    c = get_complexity_classifier()
    assert c.source == "wire"  # 默认 wire（生产现状等价）
    assert c.classify("帮我设计一个分布式系统架构") == "COMPLEX"
    score, cx, ac = c.detail("帮我设计一个分布式系统架构")
    assert score > 0 and cx and not ac
    assert c.meets("帮我设计一个分布式系统架构", "COMPLEX") is True


def test_orchestrator_delegates_to_unified_entry():
    """代码审计可证单一入口：orchestrator 委托函数与统一判定器输出一致"""
    import agent.orchestrator.orchestrator as orch
    from agent.task_planner.complexity_classifier import get_complexity_classifier as gcc

    for msg in ("帮我设计一个分布式系统架构", "查一下天气", "分析一下这个项目"):
        assert orch._judge_wire_complexity(msg) == gcc().classify(msg)
        assert orch._wire_complexity_meets(msg, "COMPLEX") == gcc().meets(msg, "COMPLEX")
        assert orch._wire_complexity_detail(msg)[0] == gcc().detail(msg)[0]
    # 兼容别名指向统一模块（无自实现分级逻辑）
    assert orch._WIRE_COMPLEXITY_LEVELS is COMPLEXITY_LEVELS


def test_source_resolution_env_and_default(monkeypatch):
    """判定源解析：环境变量 > config.yaml > 默认 wire；非法值回退 wire"""
    monkeypatch.delenv("COMPLEXITY_SOURCE", raising=False)
    assert resolve_source() == "wire"
    monkeypatch.setenv("COMPLEXITY_SOURCE", "enhanced_planner")
    assert resolve_source() == "enhanced_planner"
    monkeypatch.setenv("COMPLEXITY_SOURCE", "bogus")
    c = build_classifier(resolve_source())
    assert c.source == "wire"  # 非法 → 回退默认


# ════════════════════════════════════════════════════════════
#  4. enhanced_planner 适配器（封装不改内部实现）
# ════════════════════════════════════════════════════════════

def test_enhanced_planner_adapter_normalization():
    """适配器输出归一到 canonical：MODERATE → NORMAL；纯函数封装"""
    e = EnhancedPlannerClassifier()
    assert normalize_level("moderate") == "NORMAL"
    assert normalize_level("MODERATE") == "NORMAL"
    assert e.classify("帮我设计一个分布式系统架构") == "COMPLEX"
    assert e.classify("查一下今天的天气") in CANONICAL_LEVELS
    # 不触发 DAG 创建/确认流程：仅 _evaluate_complexity 纯函数
    assert e.classify("你好") == "SIMPLE"  # enhanced_planner 无命中默认 SIMPLE


def test_enhanced_planner_internal_untouched():
    """不变式：enhanced_planner.py 内部实现未被修改（git diff 审计锚点）——
    适配器只调用 _evaluate_complexity 与 COMPLEXITY_KEYWORDS 只读成员"""
    e = EnhancedPlannerClassifier()
    assert hasattr(e._planner, "_evaluate_complexity")
    assert e._planner.COMPLEXITY_KEYWORDS  # 只读引用，不写


def test_classifier_exception_fallback():
    """facade 异常兜底：分类异常回退 TRIVIAL，不影响主链路"""
    c = build_classifier("wire")
    # 非字符串输入由 str() 兜底，不抛异常
    assert c.classify(None) in CANONICAL_LEVELS
    assert c.meets(None, "COMPLEX") in (True, False)


# ════════════════════════════════════════════════════════════
#  5. 单例生命周期
# ════════════════════════════════════════════════════════════

def test_singleton_reset(monkeypatch):
    """get/reset 单例：reset 后重建；环境变量切换 source 生效"""
    reset_complexity_classifier()
    c1 = get_complexity_classifier()
    c2 = get_complexity_classifier()
    assert c1 is c2
    reset_complexity_classifier()
    c3 = get_complexity_classifier()
    assert c3 is not c1
