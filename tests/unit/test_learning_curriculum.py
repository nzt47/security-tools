"""任务7 课程难度自适应策略（agent/learning/curriculum.py）— 单测

覆盖（对应评估标准）：
1. 默认关闭零行为变化：LEARNING_CURRICULUM_ENABLED=false（默认）时 evaluate/
   get_routing_adjustment 返回零建议、不写审计——路由行为与现状一致（测试证明）；
2. 高复杂度档成功率基线达标（≥ 门槛）→ 允许提升路由概率（建议 +max_step）；
3. 低复杂度档失败率超门槛 → 封锁高复杂度档提升；
4. 高复杂度档成功率不达标 → 不提升；
5. 样本不足 → insufficient_data，不输出建议；
6. 审计：observe → decision=preview；active → decision=apply（JSONL 落盘）；
7. 输入形态：周行列表与快照 kpis dict 均支持。
"""

import json
import os
from pathlib import Path

import pytest

from agent.learning.curriculum import (
    CurriculumStrategy,
    get_curriculum_strategy,
    reset_curriculum_strategy,
)

_LEVELS = ("TRIVIAL", "SIMPLE", "NORMAL", "COMPLEX")
_ALL_ZERO = {lvl: 0.0 for lvl in _LEVELS}


def _kpis(cx_map: dict) -> dict:
    """构造 get_snapshot()["kpis"] 形态输入（failure_rate_by_task_type_complexity 节）"""
    return {"failure_rate_by_task_type_complexity": cx_map}


def _rows(*week_maps: dict) -> list:
    """构造 get_weekly_kpis() 周行形态输入（最近 N 周）"""
    return [{"failure_rate_by_task_type_complexity": m} for m in week_maps]


def _stable_cx(high_total=10, high_failed=1) -> dict:
    """稳定基线：低档 10% 失败、高档 90% 成功（默认高档 COMPLEX 达标）"""
    return {
        "llm": {
            "TRIVIAL": {"total": 10, "failed": 1},
            "SIMPLE": {"total": 10, "failed": 1},
            "COMPLEX": {"total": high_total, "failed": high_failed},
        },
    }


# ════════════════════════════════════════════════════════════
#  1. 默认关闭零行为变化
# ════════════════════════════════════════════════════════════

def test_default_disabled_zero_behavior(monkeypatch, tmp_path):
    """默认关：零建议、零审计、路由调整全 0（与现状路由行为一致）"""
    monkeypatch.delenv("LEARNING_CURRICULUM_ENABLED", raising=False)
    s = CurriculumStrategy()
    assert s.enabled is False
    advice = s.evaluate(_kpis(_stable_cx()))
    assert advice["enabled"] is False
    assert advice["adjustments"] == _ALL_ZERO
    assert advice["baselines"] == {}
    assert advice["audit"]["written"] is False
    assert s.get_routing_adjustment(_kpis(_stable_cx())) == _ALL_ZERO
    # 不写审计文件
    assert not (tmp_path / "curriculum_audit.jsonl").exists()


def test_default_disabled_with_strong_data_still_zero():
    """默认关时即便 KPI 数据支持提升也不产生任何调整（观察模式语义）"""
    s = CurriculumStrategy({"enabled": False})
    advice = s.evaluate(_kpis(_stable_cx(high_failed=0)))
    assert advice["adjustments"] == _ALL_ZERO


# ════════════════════════════════════════════════════════════
#  2. 高复杂度档成功率达标 → 提升
# ════════════════════════════════════════════════════════════

def test_promotion_when_high_complexity_success_above_baseline(tmp_path):
    """COMPLEX 成功率 90% ≥ 70% 且低档稳定 → COMPLEX 建议 +max_step"""
    s = CurriculumStrategy({"enabled": True, "audit_file": str(tmp_path / "a.jsonl")})
    advice = s.evaluate(_kpis(_stable_cx()))
    assert advice["gate"]["blocked"] is False
    assert advice["adjustments"]["COMPLEX"] == pytest.approx(0.1)
    assert advice["adjustments"]["TRIVIAL"] == 0.0
    assert advice["adjustments"]["SIMPLE"] == 0.0
    assert s.get_routing_adjustment(_kpis(_stable_cx()))["COMPLEX"] == pytest.approx(0.1)


def test_promotion_for_normal_too(tmp_path):
    """NORMAL 成功率达标同样允许提升（高档位 = NORMAL/COMPLEX）"""
    s = CurriculumStrategy({"enabled": True, "audit_file": str(tmp_path / "n.jsonl")})
    k = _kpis({
        "llm": {
            "TRIVIAL": {"total": 10, "failed": 1},
            "SIMPLE": {"total": 10, "failed": 1},
            "NORMAL": {"total": 10, "failed": 2},
        },
    })
    advice = s.evaluate(k)
    assert advice["adjustments"]["NORMAL"] == pytest.approx(0.1)


# ════════════════════════════════════════════════════════════
#  3. 低复杂度档失败率超门槛 → 封锁
# ════════════════════════════════════════════════════════════

def test_gate_blocks_when_low_complexity_unstable(tmp_path):
    """TRIVIAL 失败率 50% > 30% → 封锁所有高复杂度档提升"""
    s = CurriculumStrategy({"enabled": True, "audit_file": str(tmp_path / "g.jsonl")})
    k = _kpis({
        "llm": {
            "TRIVIAL": {"total": 10, "failed": 5},
            "SIMPLE": {"total": 10, "failed": 1},
            "COMPLEX": {"total": 10, "failed": 1},
        },
    })
    advice = s.evaluate(k)
    assert advice["gate"]["blocked"] is True
    assert advice["adjustments"] == _ALL_ZERO
    assert "封锁" in advice["recommendations"][0]


def test_gate_simple_unstable_also_blocks(tmp_path):
    """SIMPLE 档失败率超门槛同样触发封锁"""
    s = CurriculumStrategy({"enabled": True, "audit_file": str(tmp_path / "s.jsonl")})
    k = _kpis({
        "llm": {
            "TRIVIAL": {"total": 10, "failed": 1},
            "SIMPLE": {"total": 10, "failed": 4},
            "COMPLEX": {"total": 10, "failed": 1},
        },
    })
    advice = s.evaluate(k)
    assert advice["gate"]["blocked"] is True
    assert advice["adjustments"]["COMPLEX"] == 0.0


# ════════════════════════════════════════════════════════════
#  4. 高复杂度档成功率不达标 → 不提升
# ════════════════════════════════════════════════════════════

def test_no_promotion_when_success_below_baseline(tmp_path):
    """COMPLEX 成功率 60% < 70% → 不提升（成功率门槛生效）"""
    s = CurriculumStrategy({"enabled": True, "audit_file": str(tmp_path / "b.jsonl")})
    k = _kpis(_stable_cx(high_total=10, high_failed=4))
    advice = s.evaluate(k)
    assert advice["adjustments"]["COMPLEX"] == 0.0
    assert "不提升" in advice["recommendations"][-1]


# ════════════════════════════════════════════════════════════
#  5. 样本不足 → insufficient_data
# ════════════════════════════════════════════════════════════

def test_insufficient_samples_no_advice(tmp_path):
    """COMPLEX 样本 2 < min_samples 5 → insufficient_data，不输出建议"""
    s = CurriculumStrategy({"enabled": True, "audit_file": str(tmp_path / "i.jsonl")})
    k = _kpis({
        "llm": {
            "TRIVIAL": {"total": 10, "failed": 1},
            "SIMPLE": {"total": 10, "failed": 1},
            "COMPLEX": {"total": 2, "failed": 0},
        },
    })
    advice = s.evaluate(k)
    assert advice["baselines"]["COMPLEX"]["insufficient_data"] is True
    assert advice["adjustments"]["COMPLEX"] == 0.0


# ════════════════════════════════════════════════════════════
#  6. 审计
# ════════════════════════════════════════════════════════════

def test_audit_written_observe_preview(tmp_path):
    """observe 模式：审计 decision=preview，JSONL 落盘"""
    audit = tmp_path / "obs.jsonl"
    s = CurriculumStrategy({"enabled": True, "mode": "observe",
                            "audit_file": str(audit)})
    advice = s.evaluate(_kpis(_stable_cx()))
    assert advice["audit"]["written"] is True
    assert advice["audit"]["decision"] == "preview"
    lines = audit.read_text(encoding="utf-8").strip().splitlines()
    rec = json.loads(lines[0])
    assert rec["decision"] == "preview"
    assert rec["adjustments"]["COMPLEX"] == pytest.approx(0.1)


def test_audit_written_active_apply(tmp_path):
    """active 模式：审计 decision=apply"""
    audit = tmp_path / "act.jsonl"
    s = CurriculumStrategy({"enabled": True, "mode": "active",
                            "audit_file": str(audit)})
    advice = s.evaluate(_kpis(_stable_cx()))
    assert advice["audit"]["decision"] == "apply"
    rec = json.loads(audit.read_text(encoding="utf-8").strip().splitlines()[0])
    assert rec["decision"] == "apply"


def test_audit_not_written_when_disabled(tmp_path):
    """默认关：evaluate 不写审计（零副作用）"""
    audit = tmp_path / "off.jsonl"
    s = CurriculumStrategy({"enabled": False, "audit_file": str(audit)})
    advice = s.evaluate(_kpis(_stable_cx()))
    assert advice["audit"]["written"] is False
    assert not audit.exists()


# ════════════════════════════════════════════════════════════
#  7. 输入形态与单例
# ════════════════════════════════════════════════════════════

def test_weekly_rows_input(tmp_path):
    """周行列表输入：跨周聚合（最近 window_weeks 周）"""
    s = CurriculumStrategy({"enabled": True, "audit_file": str(tmp_path / "w.jsonl")})
    rows = _rows(
        {"llm": {"TRIVIAL": {"total": 10, "failed": 1},
                 "SIMPLE": {"total": 10, "failed": 1},
                 "COMPLEX": {"total": 10, "failed": 2}}},
        {"llm": {"COMPLEX": {"total": 10, "failed": 0}}},
    )
    advice = s.evaluate(rows)
    # COMPLEX 跨 2 周聚合：20 次 2 失败 → 90% 达标
    assert advice["baselines"]["COMPLEX"]["total"] == 20
    assert advice["adjustments"]["COMPLEX"] == pytest.approx(0.1)


def test_singleton_default_disabled():
    """全局单例默认关闭（config.yaml learning.curriculum.enabled=false 生效）"""
    reset_curriculum_strategy()
    try:
        s = get_curriculum_strategy()
        assert s.enabled is False
    finally:
        reset_curriculum_strategy()
