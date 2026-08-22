"""任务2 学习 KPI 数据源补齐与触发条件监控 — 单测

覆盖（对应评估标准）：
1. KPI#4 接线：record_task_result 按 task_type 聚合口径、judged_complexity 扩展键、
   orchestrator/feedback 生产调用方（源码审计 + kwargs 透传行为）
2. KPI#7 口径：候选基数不足 → 周级 insufficient_data，不进入"连续 4 周"统计；
   基数达标 + 低采纳率 → TC-1 命中
3. 持久化启用后模拟重启：周级窗口数据不丢、"连续 4 周"可累计
4. §5.2 五条触发条件逐条可计算（TC-1~TC-5 示例数据演示，无主观门槛）
5. 埋点零行为变化：知识检索观察埋点默认关（不产生任何计数）、开启后按观察模式计入
6. API 扩展端点：/api/learning/metrics/weekly、/trigger 200 + 结构正确
"""

import os
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.learning_metrics import LearningMetrics
from agent.learning_metrics_api import learning_metrics_bp
from flask import Flask

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _week_ts(weeks_ago: int, day_offset: int = 0) -> float:
    """N 周前 ISO 周（周一起始）偏移 day_offset 天的 12:00 时间戳（测试多周构造）"""
    today = date.today()
    iso = today.isocalendar()
    monday = date.fromisocalendar(iso[0], iso[1], 1)
    target = monday - timedelta(weeks=weeks_ago) + timedelta(days=day_offset)
    return datetime(target.year, target.month, target.day, 12).timestamp()


def _seed_week(lm: LearningMetrics, weeks_ago: int, *,
               interactions: int = 1,
               candidates: int = 0, adopted: int = 0,
               task_type: str = "", task_total: int = 0, task_failed: int = 0,
               feedback: float | None = None,
               tokens: int = 0, saved_tokens: int = 0,
               artifacts: int = 0) -> None:
    """在指定周写入一组事件（默认零事件周仅 1 次交互保证周桶存在）"""
    ts = _week_ts(weeks_ago)
    for _ in range(interactions):
        lm.record_interaction(ts=ts)
    for _ in range(candidates):
        lm.record_evolution_candidate(adopted=False, ts=ts)
    for _ in range(adopted):
        lm.record_evolution_candidate(adopted=True, ts=ts)
    for _ in range(task_total):
        lm.record_task_result(task_type or "llm", success=(_ >= task_failed),
                              ts=ts)
    if feedback is not None:
        lm.record_feedback(int(feedback), ts=ts)
    if tokens:
        lm.record_llm_tokens(tokens, ts=ts)
    if saved_tokens:
        lm.record_workflow_match(hit=True, saved_tokens=saved_tokens, ts=ts)
    for _ in range(artifacts):
        lm.record_artifact("skill", ts=ts)


# ════════════════════════════════════════════════════════════════
#  1. KPI#4 数据源：record_task_result 聚合口径 + orchestrator/feedback 接线
# ════════════════════════════════════════════════════════════════

def test_task_result_by_type_failure_rate():
    """验收：构造成功/失败任务 → 分类型失败率变化正确（task_type 计数）"""
    lm = LearningMetrics(enabled=True)
    for i in range(10):
        lm.record_task_result("qa", success=(i < 7))   # 7 成功 3 失败
    for _ in range(4):
        lm.record_task_result("llm", success=False)     # 4 失败
    lm.record_task_result("", True)                      # 空 → unknown
    snap = lm.get_snapshot()["kpis"]["failure_rate_by_task_type"]
    assert snap["qa"] == {"total": 10, "failed": 3,
                          "rate": pytest.approx(0.3)}
    assert snap["llm"] == {"total": 4, "failed": 4, "rate": 1.0}
    assert snap["unknown"] == {"total": 1, "failed": 0, "rate": 0.0}
    assert set(snap.keys()) == {"qa", "llm", "unknown"}


def test_task_result_complexity_extension_key():
    """验收：judged_complexity 扩展键独立聚合，不改 task_type 既有口径"""
    lm = LearningMetrics(enabled=True)
    lm.record_task_result("llm", False, judged_complexity="COMPLEX")
    lm.record_task_result("llm", True, judged_complexity="complex")  # 大小写归一
    lm.record_task_result("llm", True)  # 无复杂度 → 不进复杂度桶
    # task_type 口径不变
    t = lm.get_snapshot()["kpis"]["failure_rate_by_task_type"]["llm"]
    assert t["total"] == 3 and t["failed"] == 1
    # 复杂度扩展桶（周级统计可见；为任务7 复杂度维度预留）
    wk = lm.get_weekly_kpis(weeks=1)[0]
    cx = wk["complexity_failure_rate"]["COMPLEX"]
    assert cx["total"] == 2 and cx["failed"] == 1


def test_production_wiring_audit():
    """验收：git grep 审计——每项 KPI 至少一个生产调用方（源码级断言）"""
    orch = (_REPO_ROOT / "agent" / "orchestrator" / "orchestrator.py").read_text(
        encoding="utf-8")
    fb = (_REPO_ROOT / "agent" / "feedback.py").read_text(encoding="utf-8")
    ks = (_REPO_ROOT / "agent" / "knowledge" / "search.py").read_text(
        encoding="utf-8")

    # KPI#4：orchestrator 全部收尾路径（10 处）+ feedback 成功/失败路径
    for snippet in (
        'record_task_result", task_type="input_guard",',      # 输入护栏拦截
        'record_task_result", task_type="workflow",',         # 规则层命中
        'record_task_result", task_type="behavior_reject",',  # 行为/人格拒绝
        'record_task_result", task_type="template",',         # 模板层命中
        'record_task_result", task_type="workflow_learning",',  # 工作流学习命中
        'record_task_result", task_type="semantic",',         # 语义层命中
        'record_task_result", task_type="reject",',           # 软拒识
        'task_type="planning" if _wire_planning_used else "llm",',  # 主成功
        'judged_complexity=_wire_judged',                     # 复杂度扩展键（≥2 处）
    ):
        assert snippet in orch, f"orchestrator 缺 KPI#4 埋点: {snippet}"
    # llm 失败路径 + 低置信度路径（success=False）
    assert orch.count('record_task_result", task_type="llm",') >= 2
    # feedback 成功/失败
    assert 'record_task_result("feedback", success=True)' in fb
    assert 'record_task_result("feedback", success=False)' in fb
    # KPI#1 语义查询：knowledge/search 观察埋点 + orchestrator 语义层
    assert "record_semantic_query(hit=hit, saved_tokens=0)" in ks
    assert 'record_semantic_query", hit=False' in orch
    # KPI#1 token 计量：标准 _call_llm 与 _call_llm_v2 双路径
    assert orch.count('_emit_learning_metric("record_llm_tokens", tokens=_est_tokens)') >= 2


def test_orchestrator_emit_task_result_kwargs_passthrough(monkeypatch):
    """行为级：orchestrator 埋点助手把 task_type/success/judged_complexity 正确透传"""
    import agent.orchestrator.orchestrator as orch
    calls = []

    class _FakeLM:
        def record_task_result(self, task_type, success, judged_complexity=None):
            calls.append((task_type, success, judged_complexity))

    monkeypatch.setattr("agent.learning_metrics.get_learning_metrics",
                        lambda: _FakeLM())
    orch._emit_learning_metric("record_task_result", task_type="llm",
                               success=False, judged_complexity="COMPLEX")
    orch._emit_learning_metric("record_task_result", task_type="workflow",
                               success=True)
    assert calls == [("llm", False, "COMPLEX"), ("workflow", True, None)]


# ════════════════════════════════════════════════════════════════
#  2. KPI#7 口径：候选基数门槛 + insufficient_data + 连续 4 周
# ════════════════════════════════════════════════════════════════

def test_snapshot_evolution_extension_fields():
    """快照 evolution_adoption_rate 扩展字段（向后兼容）"""
    lm = LearningMetrics(enabled=True, min_candidates=5)
    ev = lm.get_snapshot()["kpis"]["evolution_adoption_rate"]
    assert ev["candidates"] == 0
    assert ev["insufficient_data"] is True      # 0 < 5
    assert ev["min_candidates"] == 5
    for _ in range(5):
        lm.record_evolution_candidate(adopted=False)
    ev = lm.get_snapshot()["kpis"]["evolution_adoption_rate"]
    assert ev["candidates"] == 5
    assert ev["insufficient_data"] is False


def test_kpi7_weekly_insufficient_data_marking():
    """周候选数 < N → 该周 insufficient_data=True；≥ N → False"""
    lm = LearningMetrics(enabled=True, min_candidates=5)
    _seed_week(lm, 1, candidates=3)    # 不足
    _seed_week(lm, 0, candidates=7)    # 达标
    rows = lm.get_weekly_kpis(weeks=2)
    by_week = {r["week"]: r["evolution"] for r in rows}
    assert len(by_week) == 2
    insufficient = [v["insufficient_data"] for v in by_week.values()]
    assert insufficient == [True, False]  # 旧周不足、新周达标（升序）


def test_kpi7_insufficient_data_blocks_4week_streak():
    """候选基数不足的周不进入"连续 4 周"统计（TC-1 标记 insufficient_data 而非命中）"""
    lm = LearningMetrics(enabled=True, min_candidates=5)
    # 3 周达标（10 候选 0 采纳 = 0%），1 周不足（2 候选）→ 窗口不可度量
    _seed_week(lm, 3, candidates=10, tokens=100, saved_tokens=50)
    _seed_week(lm, 2, candidates=10, tokens=100, saved_tokens=50)
    _seed_week(lm, 1, candidates=2, tokens=100, saved_tokens=50)  # 基数不足
    _seed_week(lm, 0, candidates=10, tokens=100, saved_tokens=50)
    trig = lm.evaluate_trigger_conditions(weeks=4)
    tc1 = trig["conditions"]["judge_intro"]
    assert tc1["hit"] is False
    assert tc1["status"] == "insufficient_data"


def test_kpi7_4week_streak_tc1_hit():
    """KPI#7 连续 4 周 <5%（候选基数达标）+ KPI#1 环比无提升 → TC-1 命中"""
    lm = LearningMetrics(enabled=True, min_candidates=5)
    for ago in (3, 2, 1, 0):
        # 每周 10 候选 0 采纳（0% < 5%）；前一周复用率 0.5、最新周 0.25 → 环比无提升
        saved = 100 if ago % 2 == 1 else 50
        _seed_week(lm, ago, candidates=10, tokens=200, saved_tokens=saved)
    trig = lm.evaluate_trigger_conditions(weeks=4)
    tc1 = trig["conditions"]["judge_intro"]
    assert tc1["status"] == "hit"
    assert tc1["hit"] is True
    assert tc1["detail"]["kpi1_wow_no_improvement"] is True


def test_kpi7_4week_streak_tc1_not_hit_when_rate_high():
    """任一周采纳率 ≥5% → TC-1 不命中"""
    lm = LearningMetrics(enabled=True, min_candidates=5)
    _seed_week(lm, 3, candidates=10, tokens=200, saved_tokens=100)
    _seed_week(lm, 2, candidates=10, tokens=200, saved_tokens=100)
    _seed_week(lm, 1, candidates=10, adopted=2, tokens=200, saved_tokens=100)  # 20%
    _seed_week(lm, 0, candidates=10, tokens=200, saved_tokens=100)
    trig = lm.evaluate_trigger_conditions(weeks=4)
    tc1 = trig["conditions"]["judge_intro"]
    assert tc1["status"] == "not_hit"
    assert tc1["hit"] is False


# ════════════════════════════════════════════════════════════════
#  3. §5.2 五条触发条件逐条可计算（示例数据演示）
# ════════════════════════════════════════════════════════════════

def test_tc2_solver_enhancement_hit():
    """KPI#3 工作流命中率连续 4 周 <10% → TC-2 命中"""
    lm = LearningMetrics(enabled=True)
    for ago in (3, 2, 1, 0):
        _seed_week(lm, ago, interactions=100, tokens=100)
        # 每周 100 次交互、3 次工作流命中（3% < 10%）
        for _ in range(3):
            lm.record_workflow_match(hit=True, saved_tokens=100,
                                     ts=_week_ts(ago))
    trig = lm.evaluate_trigger_conditions(weeks=4)
    tc2 = trig["conditions"]["solver_enhancement"]
    assert tc2["status"] == "hit"
    assert tc2["hit"] is True


def test_tc3_course_adaptation_hit():
    """KPI#4 任一分类型失败率连续 4 周 >30% 且 KPI#5 均分环比下降 → TC-3 命中"""
    lm = LearningMetrics(enabled=True)
    for ago in (3, 2, 1, 0):
        _seed_week(lm, ago, interactions=1,
                   task_type="llm", task_total=10, task_failed=5,  # 50% > 30%
                   feedback=(4 if ago % 2 == 1 else 3))            # 前 4 → 后 3 下降
    trig = lm.evaluate_trigger_conditions(weeks=4)
    tc3 = trig["conditions"]["course_adaptation"]
    assert tc3["status"] == "hit"
    assert tc3["hit"] is True
    assert tc3["detail"]["feedback_avg_declined"] is True


def test_tc4_sandbox_replay_hit_and_unknown():
    """回放覆盖率 <50% 且 KPI#6 沉淀增量连续 4 周停滞 → TC-4 命中；
    回放统计未接入（replay_coverage=None）→ unknown（不触发）"""
    lm = LearningMetrics(enabled=True)
    for ago in (3, 2, 1, 0):
        _seed_week(lm, ago, interactions=10)  # 无任何沉淀 → 停滞
    trig = lm.evaluate_trigger_conditions(weeks=4, replay_coverage=0.4)
    tc4 = trig["conditions"]["sandbox_replay"]
    assert tc4["status"] == "hit"
    assert tc4["hit"] is True
    # 未接入回放统计 → unknown
    trig2 = lm.evaluate_trigger_conditions(weeks=4, replay_coverage=None)
    assert trig2["conditions"]["sandbox_replay"]["status"] == "unknown"


def test_tc5_l3_research_hit():
    """TC-1/TC-3/TC-4 全触发 + 外部前置全 True → TC-5 命中"""
    lm = LearningMetrics(enabled=True, min_candidates=5)
    for ago in (3, 2, 1, 0):
        _seed_week(lm, ago, interactions=10, candidates=10,
                   task_type="llm", task_total=10, task_failed=5,
                   feedback=(4 if ago % 2 == 1 else 3),
                   tokens=200, saved_tokens=(100 if ago % 2 == 1 else 50))
    trig = lm.evaluate_trigger_conditions(
        weeks=4, replay_coverage=0.4,
        audit_ok=True, g1_g5_ready=True, decision_approval=True)
    conds = trig["conditions"]
    assert conds["judge_intro"]["status"] == "hit"
    assert conds["course_adaptation"]["status"] == "hit"
    assert conds["sandbox_replay"]["status"] == "hit"
    assert conds["l3_research"]["status"] == "hit"
    assert conds["l3_research"]["hit"] is True


def test_tc5_l3_unknown_when_externals_missing():
    """外部前置缺失（审计/G1-G5/批准未提供）→ TC-5 unknown（不判命中）"""
    lm = LearningMetrics(enabled=True, min_candidates=5)
    for ago in (3, 2, 1, 0):
        _seed_week(lm, ago, interactions=10, candidates=10,
                   task_type="llm", task_total=10, task_failed=5,
                   feedback=(4 if ago % 2 == 1 else 3),
                   tokens=200, saved_tokens=(100 if ago % 2 == 1 else 50))
    trig = lm.evaluate_trigger_conditions(weeks=4, replay_coverage=0.4)
    tc5 = trig["conditions"]["l3_research"]
    assert tc5["hit"] is False
    assert tc5["status"] == "unknown"


# ════════════════════════════════════════════════════════════════
#  4. 持久化启用后模拟重启：周级窗口可累计
# ════════════════════════════════════════════════════════════════

def _mk_persistence(path, batch=200, retention=90) -> dict:
    return {
        "enabled": True,
        "path": str(path),
        "flush_batch_size": batch,
        "retention_days": retention,
    }


def test_persistence_restart_weekly_window_accumulates(tmp_path):
    """模拟重启：新实例加载 DB 后周级窗口数据不丢，"连续 4 周"判定可累计"""
    db = tmp_path / "lm_triggers.db"
    lm_a = LearningMetrics(persistence=_mk_persistence(db), min_candidates=5)
    for ago in (3, 2, 1, 0):
        _seed_week(lm_a, ago, interactions=10, candidates=10,
                   task_type="llm", task_total=10, task_failed=5)
    lm_a.flush()
    rows_a = lm_a.get_weekly_kpis(weeks=4)

    # 模拟进程重启：同一 DB 路径新实例
    lm_b = LearningMetrics(persistence=_mk_persistence(db), min_candidates=5)
    rows_b = lm_b.get_weekly_kpis(weeks=4)
    assert len(rows_b) == 4
    for a, b in zip(rows_a, rows_b):
        assert a["week"] == b["week"]
        assert a["evolution"] == b["evolution"]
        assert a["failure_rate_by_task_type"] == b["failure_rate_by_task_type"]
    # 重启后"连续 4 周"窗口可计算（TC-3 失败率 50% >30%，feedback 缺失 → 环比不可度量）
    trig_b = lm_b.evaluate_trigger_conditions(weeks=4, min_candidates=5)
    assert trig_b["conditions"]["course_adaptation"]["hit"] is False
    assert trig_b["weekly"][-1]["week"] == rows_b[-1]["week"]


# ════════════════════════════════════════════════════════════════
#  5. 埋点零行为变化：知识检索观察埋点默认关
# ════════════════════════════════════════════════════════════════

def test_knowledge_search_observe_gating(monkeypatch):
    """观察埋点默认关（零计数）；开启后按观察模式计入（saved_tokens=0）"""
    import agent.knowledge.search as ks
    called = []

    class _FakeLM:
        def record_semantic_query(self, hit, saved_tokens=0):
            called.append((hit, saved_tokens))

    monkeypatch.setattr("agent.learning_metrics.get_learning_metrics",
                        lambda: _FakeLM())
    # 默认关：不调用（零行为变化）
    monkeypatch.setattr(ks, "_OBSERVE_KNOWLEDGE_SEARCH", False)
    ks._emit_knowledge_semantic_metric(hit=True)
    assert called == []
    # 开启：调用且 saved_tokens=0（不改 token 复用率口径）
    monkeypatch.setattr(ks, "_OBSERVE_KNOWLEDGE_SEARCH", True)
    ks._emit_knowledge_semantic_metric(hit=False)
    ks._emit_knowledge_semantic_metric(hit=True)
    assert called == [(False, 0), (True, 0)]


# ════════════════════════════════════════════════════════════════
#  6. API 扩展端点
# ════════════════════════════════════════════════════════════════

def _seed_api_data(lm: LearningMetrics) -> None:
    for ago in (3, 2, 1, 0):
        _seed_week(lm, ago, interactions=5, candidates=10,
                   task_type="qa", task_total=10, task_failed=2,
                   feedback=4, tokens=200, saved_tokens=100)


def test_api_weekly_endpoint():
    lm = LearningMetrics(enabled=True)
    _seed_api_data(lm)
    app = Flask(__name__)
    app.register_blueprint(learning_metrics_bp)
    with patch("agent.learning_metrics_api.get_learning_metrics",
               return_value=lm):
        resp = app.test_client().get("/api/learning/metrics/weekly?weeks=4")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 4
        row = data["weekly"][-1]
        assert row["week"] and row["start"] and row["end"]
        assert row["start"] < row["end"]
        for key in ("week", "start", "end", "interactions",
                    "token_reuse_rate", "skill_hit_rate", "workflow_hit_rate",
                    "failure_rate_by_task_type", "complexity_failure_rate",
                    "feedback", "artifact_delta", "evolution"):
            assert key in row, f"周行缺字段: {key}"


def test_api_trigger_endpoint():
    lm = LearningMetrics(enabled=True, min_candidates=5)
    _seed_api_data(lm)
    app = Flask(__name__)
    app.register_blueprint(learning_metrics_bp)
    with patch("agent.learning_metrics_api.get_learning_metrics",
               return_value=lm):
        resp = app.test_client().get(
            "/api/learning/metrics/trigger?weeks=4&replay_coverage=0.4"
            "&audit_ok=1&g1_g5_ready=1&decision_approval=1")
        assert resp.status_code == 200
        data = resp.get_json()
        conds = data["conditions"]
        assert set(conds.keys()) == {
            "judge_intro", "solver_enhancement", "course_adaptation",
            "sandbox_replay", "l3_research",
        }
        for cid, entry in conds.items():
            assert entry["status"] in (
                "hit", "not_hit", "insufficient_data", "unknown")
            assert isinstance(entry["hit"], bool)


def test_api_legacy_metrics_endpoint_unchanged():
    """既有 /api/learning/metrics 结构不变（7 项 KPI + 扩展字段向后兼容）"""
    lm = LearningMetrics(enabled=True)
    _seed_api_data(lm)
    app = Flask(__name__)
    app.register_blueprint(learning_metrics_bp)
    with patch("agent.learning_metrics_api.get_learning_metrics",
               return_value=lm):
        resp = app.test_client().get("/api/learning/metrics")
        assert resp.status_code == 200
        k = resp.get_json()["kpis"]
        assert set(k.keys()) == {
            "token_reuse_rate", "skill_hit_rate", "workflow_hit_rate",
            "failure_rate_by_task_type", "feedback_rating_trend",
            "artifact_delta", "evolution_adoption_rate",
        }
        ev = k["evolution_adoption_rate"]
        assert ev["candidates"] == 40
        assert ev["insufficient_data"] is False  # 40 ≥ 5
