# -*- coding: utf-8 -*-
"""L26 回归：规则层置信度出口 —— 默认逐位不变 + 声明/推导可携带信息。

缺陷事实：agent/workflow_engine/engine.py 的 try_match 原先把
WorkflowResult.confidence **硬编码为 1.0**（改前 engine.py:57），该层读数恒为常数、
不携带信息 ⇒ 无法参与校准/拒识（TASK-10 基线把该层记为 confidence_kind="hardcoded_1.0"）。

加固后的契约（本文件所断言）：
  ① 未声明 confidence 的规则 ⇒ 读数仍是 1.0（与加固前**逐位一致**）；
  ② 规则显式声明 confidence（0.0~1.0）⇒ 原样生效；
  ③ 提供 confidence_from_priority 作为"按命中强度保守推导"的可选 fallback，
     但**不默认启用**（否则 tests/unit/test_workflow_engine_comprehensive.py:462
     的 "priority=50 命中 ⇒ confidence == 1.0" 会变红）；
  ④ 任何情况下判定结果（matched / rule_name / intent / output）不受影响。
"""

import pytest

from agent.workflow_engine.builtin_rules import register_builtin_rules
from agent.workflow_engine.engine import (
    DEFAULT_RULE_CONFIDENCE, MIN_DERIVED_CONFIDENCE, WorkflowEngine,
    confidence_from_priority, resolve_rule_confidence,
)
from agent.workflow_engine.registry import Rule


def _engine_with_rule(match_fn=None, execute_fn=None, priority=50):
    engine = WorkflowEngine()
    rule = Rule("test_rule", "测试规则",
                match_fn or (lambda t: "hit" in t),
                execute_fn or (lambda t: "echo:" + str(t)),
                priority=priority)
    engine.registry.register(rule)
    return engine, rule


def test_undeclared_rule_keeps_legacy_default():
    """未声明 confidence ⇒ 仍是 1.0（既有读数与判定都不变）"""
    engine, rule = _engine_with_rule()
    result = engine.try_match("hit me")
    assert result.matched is True
    assert result.rule_name == "test_rule" and result.intent == "test_rule"
    assert result.output == "echo:hit me"
    assert result.confidence == 1.0
    assert result.confidence == DEFAULT_RULE_CONFIDENCE
    assert resolve_rule_confidence(rule) == 1.0


def test_declared_confidence_flows_through_without_touching_judgement():
    """规则声明 confidence ⇒ 生效；判定字段一字不变"""
    engine, rule = _engine_with_rule()
    rule.confidence = 0.82          # Rule 是普通 dataclass，可直接声明/赋值

    result = engine.try_match("hit me")

    assert result.confidence == pytest.approx(0.82)
    assert result.matched is True
    assert (result.rule_name, result.intent, result.output) == (
        "test_rule", "test_rule", "echo:hit me")


@pytest.mark.parametrize("bad", ["auto", 1.5, -0.1, None, True, float("nan")])
def test_invalid_declared_confidence_falls_back_to_default(bad):
    """非法/越界声明 ⇒ 退回默认 1.0（绝不抛异常、绝不产生越界读数）"""
    engine, rule = _engine_with_rule()
    rule.confidence = bad
    assert resolve_rule_confidence(rule) == DEFAULT_RULE_CONFIDENCE
    assert engine.try_match("hit") .confidence == DEFAULT_RULE_CONFIDENCE


def test_priority_derivation_is_opt_in():
    """confidence_from_priority 保守且**默认不启用**（fallback 未传 ⇒ 1.0）"""
    engine, rule = _engine_with_rule(priority=50)
    assert resolve_rule_confidence(rule) == 1.0
    assert resolve_rule_confidence(rule,
                                   fallback=confidence_from_priority) == 0.5
    assert engine.try_match("hit").confidence == 1.0   # 未启用推导


@pytest.mark.parametrize("priority,expected", [
    (100, 1.0), (90, 0.9), (70, 0.7), (50, 0.5), (10, 0.1),
    (0, MIN_DERIVED_CONFIDENCE), (-5, MIN_DERIVED_CONFIDENCE),
    (200, 1.0),
])
def test_priority_derivation_is_bounded(priority, expected):
    """推导值恒在 [MIN_DERIVED_CONFIDENCE, 1.0]：不越界、不给出 0"""
    rule = Rule("r", "", lambda t: True, lambda t: "", priority=priority)
    assert confidence_from_priority(rule) == pytest.approx(expected)


def test_priority_derivation_survives_missing_priority():
    """规则对象没有 priority ⇒ 退回默认（不臆造读数）"""
    class _NoPriority:
        pass
    assert confidence_from_priority(_NoPriority()) == DEFAULT_RULE_CONFIDENCE


def test_broken_fallback_never_breaks_matching():
    """fallback 抛异常 ⇒ 退回默认值，匹配结果不受影响"""
    engine, rule = _engine_with_rule()

    def _boom(_rule):
        raise RuntimeError("校准钩子故障")

    assert resolve_rule_confidence(rule, fallback=_boom) == DEFAULT_RULE_CONFIDENCE
    assert engine.try_match("hit").confidence == 1.0


#: 覆盖 8 条内置规则 + 1 条未命中（与 L26 前后对拍脚本同一组输入）
_SAMPLES = [
    ("check_time", "现在几点"), ("check_date", "今天几号"),
    ("check_health", "你还好吗"), ("simple_calc", "1 + 2"),
    ("greeting", "早上好"), ("farewell", "再见"),
    ("thanks", "谢谢"), ("confirmation", "好的"),
    (None, "这是一段未知的话xyz123"),
]


def test_builtin_rules_judgement_unchanged_confidence_can_carry_info():
    """前后对拍：8 条内置规则判定结果不变；开推导后仅 confidence 变化"""
    engine = WorkflowEngine()
    register_builtin_rules(engine.registry)
    by_name = {r.name: r for r in engine.registry.get_enabled()}

    changed, judgements = [], []
    for expect, text in _SAMPLES:
        result = engine.try_match(text)
        judgements.append((expect, bool(result.matched), result.rule_name,
                           result.output))
        if not result.matched:
            continue
        derived = resolve_rule_confidence(by_name[result.rule_name],
                                          fallback=confidence_from_priority)
        assert result.confidence == 1.0          # ① 默认读数逐位不变
        if derived != result.confidence:
            changed.append((result.rule_name, result.confidence, derived))

    assert judgements == [
        ("check_time", True, "check_time", judgements[0][3]),
        ("check_date", True, "check_date", judgements[1][3]),
        ("check_health", True, "check_health", judgements[2][3]),
        ("simple_calc", True, "simple_calc", judgements[3][3]),
        ("greeting", True, "greeting", judgements[4][3]),
        ("farewell", True, "farewell", judgements[5][3]),
        ("thanks", True, "thanks", judgements[6][3]),
        ("confirmation", True, "confirmation", judgements[7][3]),
        (None, False, "", ""),
    ]
    # ② 开推导后置信度不再恒为常数（confirmation priority=50 ⇒ 0.5）
    assert ("confirmation", 1.0, 0.5) in changed
    assert ("check_time", 1.0, 1.0) not in changed


def test_no_rule_hit_still_unmatched():
    """无命中路径不变：matched=False（不看 confidence）"""
    engine = WorkflowEngine()
    result = engine.try_match("测试输入")
    assert result.matched is False and result.rule_name == ""