"""TASK-03 学习有效性度量 — KPI 聚合与查询面测试

覆盖：50 次混合交互 7 项 KPI 自洽、token 复用率口径（命中 vs 无命中 >20pp）、
埋点异常零影响、get_snapshot 只读、/api/learning/metrics 200 + 7 KPI、
TASK-02 learning.eval.* 聚合、reset/disabled。
"""

import time

import os

import pytest

from agent.learning_metrics import LearningMetrics
from agent.learning_metrics_api import learning_metrics_bp
from flask import Flask
from unittest.mock import patch

# SKILLS_OFFLINE 模式下 conftest._skills_offline_mode 会把 get_metrics_collector
# patch 为 _DummyCollector（no-op），依赖真实计数聚合的测试无法验证，需 skip
# （与 test_monitoring_metrics.py 同模式）。
_skip_if_skills_offline = pytest.mark.skipif(
    os.environ.get("SKILLS_OFFLINE") == "1",
    reason="SKILLS_OFFLINE 模式下 get_metrics_collector 被 conftest patch 为 _DummyCollector（no-op，无法验证真实计数）",
)


class _BoomCollector:
    """埋点全部挂掉的模拟 collector：任何方法调用都抛异常"""

    def __getattr__(self, name):
        def _boom(*args, **kwargs):
            raise RuntimeError("collector exploded: %s" % name)
        return _boom


def _record_50_mixed(lm: LearningMetrics) -> None:
    """模拟 50 次混合交互（workflow/skill 命中、失败、反馈、沉淀、进化）"""
    for i in range(50):
        lm.record_interaction()
        if i % 2 == 0:
            lm.record_workflow_match(hit=True, saved_tokens=800)
            lm.record_semantic_query(hit=True, saved_tokens=200)
        else:
            lm.record_workflow_match(hit=False)
            lm.record_semantic_query(hit=False)
        lm.record_llm_tokens(3000 if i % 2 else 500)
        lm.record_task_result("qa", success=(i % 3 != 0))
        if i % 5 == 0:
            lm.record_feedback(5 if i % 2 else 3)
        if i % 10 == 0:
            lm.record_artifact("skill")
        lm.record_evolution_candidate(adopted=(i % 4 == 0))


def test_50_mixed_interactions_snapshot_self_consistent():
    """验收：50 次混合交互后 7 项 KPI 全部非空且数值自洽"""
    lm = LearningMetrics(enabled=True)
    _record_50_mixed(lm)
    snap = lm.get_snapshot()
    k = snap["kpis"]

    # token 复用率
    t = k["token_reuse_rate"]
    assert t["total_tokens"] > 0
    assert t["saved_tokens"] > 0
    assert t["rate"] == round(t["saved_tokens"] / t["total_tokens"], 4)
    # skill 命中率：25/50
    assert k["skill_hit_rate"]["queries"] == 50
    assert k["skill_hit_rate"]["hits"] == 25
    assert k["skill_hit_rate"]["rate"] == pytest.approx(0.5)
    # 工作流命中率：25/50
    assert k["workflow_hit_rate"]["interactions"] == 50
    assert k["workflow_hit_rate"]["hits"] == 25
    assert k["workflow_hit_rate"]["rate"] == pytest.approx(0.5)
    # 分类型失败率：qa 失败 i%3==0 → 17/50
    qa = k["failure_rate_by_task_type"]["qa"]
    assert qa["total"] == 50
    assert qa["failed"] == 17
    assert qa["rate"] == pytest.approx(17 / 50)
    # 反馈均分趋势：i%5==0 → 10 条
    assert k["feedback_rating_trend"]["count"] == 10
    assert k["feedback_rating_trend"]["by_day"], "近 7 日逐日趋势非空"
    # 沉淀增量：i%10==0 → 5 个 skill
    assert k["artifact_delta"]["skill"] == 5
    # 进化采纳率：i%4==0 → 13/50（0,4,...,48 共 13 个）
    ev = k["evolution_adoption_rate"]
    assert ev["candidates"] == 50
    assert ev["adopted"] == 13
    assert ev["rate"] == pytest.approx(0.26)
    # 近 7 日趋势与 7 项 KPI 键集合完整（任务7 增 task_type×complexity 双维度键）
    assert snap["trend_7d"], "trend_7d 非空"
    assert set(k.keys()) == {
        "token_reuse_rate", "skill_hit_rate", "workflow_hit_rate",
        "failure_rate_by_task_type", "feedback_rating_trend",
        "artifact_delta", "evolution_adoption_rate",
        "failure_rate_by_task_type_complexity",
    }


def test_token_reuse_rate_hit_vs_miss_gap():
    """验收：有 workflow/skill 命中会话的复用率显著高于无命中会话（>20pp）"""
    lm_hit = LearningMetrics(enabled=True)
    lm_miss = LearningMetrics(enabled=True)
    for _ in range(10):
        # 命中会话：一半消耗 + 一半节省
        lm_hit.record_llm_tokens(1000)
        lm_hit.record_token_reuse(1000)
        # 无命中会话：全消耗
        lm_miss.record_llm_tokens(2000)
    r_hit = lm_hit.get_snapshot()["kpis"]["token_reuse_rate"]["rate"]
    r_miss = lm_miss.get_snapshot()["kpis"]["token_reuse_rate"]["rate"]
    assert r_hit - r_miss > 0.20
    assert r_miss == 0.0


def test_instrumentation_failure_no_effect_on_main_chain():
    """验收：埋点全部挂掉（mock 异常）时主链路零影响"""
    lm = LearningMetrics(collector=_BoomCollector(), enabled=True)
    # 全部埋点调用不抛异常
    lm.record_interaction()
    lm.record_workflow_match(hit=True, saved_tokens=100)
    lm.record_semantic_query(hit=True, saved_tokens=50)
    lm.record_llm_tokens(100)
    lm.record_task_result("qa", False)
    lm.record_feedback(4)
    lm.record_artifact("skill")
    lm.record_evolution_candidate(True)
    # 本地聚合不受 collector 异常影响
    snap = lm.get_snapshot()
    assert snap["kpis"]["token_reuse_rate"]["total_tokens"] == 250
    assert snap["kpis"]["skill_hit_rate"]["hits"] == 1


def test_emit_learning_metric_safe_when_singleton_breaks(monkeypatch):
    """orchestrator 安全包装：单例获取抛异常时零影响"""
    import agent.orchestrator.orchestrator as orch_mod

    def _boom():
        raise RuntimeError("singleton down")

    monkeypatch.setattr("agent.learning_metrics.get_learning_metrics", _boom)
    orch_mod._emit_learning_metric("record_interaction")  # 不抛异常
    orch_mod._get_learning_saved_estimate()  # 估算兜底不抛异常


def test_get_snapshot_readonly():
    """只读聚合：重复调用不改变状态、不产生写操作"""
    lm = LearningMetrics(enabled=True)
    _record_50_mixed(lm)
    before = lm.get_snapshot()
    lm.get_snapshot()
    after = lm.get_snapshot()
    assert before["kpis"] == after["kpis"]
    assert before["trend_7d"] == after["trend_7d"]


def test_metrics_api_returns_200_with_7_kpis():
    """验收：mock 请求 /api/learning/metrics 返回 200 且含 7 项 KPI"""
    lm = LearningMetrics(enabled=True)
    _record_50_mixed(lm)
    app = Flask(__name__)
    app.register_blueprint(learning_metrics_bp)
    with patch("agent.learning_metrics_api.get_learning_metrics", return_value=lm):
        resp = app.test_client().get("/api/learning/metrics")
        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data["kpis"].keys()) == {
            "token_reuse_rate", "skill_hit_rate", "workflow_hit_rate",
            "failure_rate_by_task_type", "feedback_rating_trend",
            "artifact_delta", "evolution_adoption_rate",
            "failure_rate_by_task_type_complexity",
        }


def test_metrics_api_500_when_snapshot_fails(monkeypatch):
    """查询面异常 → 500 明确失败，不影响主链路"""
    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr("agent.learning_metrics_api.get_learning_metrics", _boom)
    app = Flask(__name__)
    app.register_blueprint(learning_metrics_bp)
    resp = app.test_client().get("/api/learning/metrics")
    assert resp.status_code == 500
    assert resp.get_json()["error"] == "learning_metrics_unavailable"


@_skip_if_skills_offline
def test_eval_stats_aggregates_task02_counters():
    """聚合 TASK-02 learning.eval.* 计数器（评估失败率数据源）"""
    from agent.monitoring.metrics import get_metrics_collector
    collector = get_metrics_collector()
    collector.increment_counter("learning.eval.total", value=10)
    collector.increment_counter("learning.eval.passed", value=7)
    collector.increment_counter("learning.eval.failed", value=3)
    lm = LearningMetrics(enabled=True)
    ev = lm.get_snapshot()["evaluation"]
    assert ev["total"] == 10
    assert ev["passed"] == 7
    assert ev["failed"] == 3
    assert ev["failure_rate"] == pytest.approx(0.3)


def test_reset_clears_state():
    lm = LearningMetrics(enabled=True)
    _record_50_mixed(lm)
    lm.reset()
    k = lm.get_snapshot()["kpis"]
    assert k["token_reuse_rate"]["total_tokens"] == 0
    assert k["skill_hit_rate"]["queries"] == 0
    assert k["workflow_hit_rate"]["interactions"] == 0
    assert k["failure_rate_by_task_type"] == {}
    assert k["feedback_rating_trend"]["count"] == 0
    assert k["evolution_adoption_rate"]["candidates"] == 0


def test_disabled_records_nothing():
    lm = LearningMetrics(enabled=False)
    lm.record_interaction()
    lm.record_workflow_match(hit=True, saved_tokens=100)
    k = lm.get_snapshot()["kpis"]
    assert k["workflow_hit_rate"]["interactions"] == 0
    assert k["workflow_hit_rate"]["hits"] == 0
    assert k["token_reuse_rate"]["saved_tokens"] == 0


def test_feedback_rating_window_current_vs_previous():
    """验收：反馈均分按 days 窗口滑动——previous_avg 取自前窗、current_avg 取自当前窗"""
    lm = LearningMetrics(enabled=True)
    now = time.time()
    # 前窗（8~14 天前，仍在 32 天保留窗口内）：4 分 ×5
    for i in range(5):
        lm.record_feedback(4, ts=now - (8 + i) * 86400)
    # 当前窗（7 天内）：5 分 ×5
    for i in range(5):
        lm.record_feedback(5, ts=now - i * 86400)
    t = lm.get_snapshot(days=7)["kpis"]["feedback_rating_trend"]
    assert t["window_days"] == 7
    assert t["count"] == 5          # 仅当前窗计数
    assert t["current_avg"] == 5.0
    assert t["previous_avg"] == 4.0


def test_artifact_delta_all_types_recorded():
    """验收：沉淀增量固定 6 类（skill/workflow/experience/knowledge_card/reflection/other）逐类计数"""
    lm = LearningMetrics(enabled=True)
    for t in ("skill", "workflow", "experience", "knowledge_card", "reflection"):
        lm.record_artifact(t)
    lm.record_artifact("unknown_type")  # 未知类型归入 other
    d = lm.get_snapshot()["kpis"]["artifact_delta"]
    assert d["skill"] == 1
    assert d["workflow"] == 1
    assert d["experience"] == 1
    assert d["knowledge_card"] == 1
    assert d["reflection"] == 1
    assert d["other"] == 1
    assert set(d.keys()) == {"skill", "workflow", "experience", "knowledge_card", "reflection", "other"}


def test_workflow_hit_rate_denominator_is_interactions():
    """口径：workflow 命中率分母是交互总数（record_interaction），而非 workflow 查询数"""
    lm = LearningMetrics(enabled=True)
    lm.record_interaction()
    lm.record_interaction()
    lm.record_workflow_match(hit=True, saved_tokens=100)  # 1 次查询 1 次命中
    lm.record_workflow_match(hit=False)                   # 1 次查询 0 次命中
    w = lm.get_snapshot()["kpis"]["workflow_hit_rate"]
    assert w["interactions"] == 2
    assert w["hits"] == 1
    assert w["rate"] == pytest.approx(0.5)
