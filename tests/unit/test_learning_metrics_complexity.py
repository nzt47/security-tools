"""任务7 KPI#4 复杂度维度（task_type × complexity 双维度）— 单测

覆盖（对应评估标准）：
1. get_snapshot() failure_rate_by_task_type_complexity 双维度聚合（嵌套结构）；
2. get_weekly_kpis() 周行双维度聚合（事件 key '::' 拆回嵌套）；
3. 向后兼容：task_type 既有口径、单维度复杂度桶、既有 kpis 键不变；
4. 持久化重启恢复：双维度跨重启可累计（与周级滚动统计同源同形）；
5. 空复杂度键不进双维度桶（judged_complexity=None 零影响）。
"""

import pytest

from agent.learning_metrics import LearningMetrics


def _mk_persistence(path, batch=200, retention=90) -> dict:
    return {
        "enabled": True,
        "path": str(path),
        "flush_batch_size": batch,
        "retention_days": retention,
    }


# ════════════════════════════════════════════════════════════
#  1. 快照双维度聚合
# ════════════════════════════════════════════════════════════

def test_snapshot_task_type_complexity_dimension():
    """快照 failure_rate_by_task_type_complexity：task_type → complexity → 统计"""
    lm = LearningMetrics(enabled=True)
    lm.record_task_result("llm", False, judged_complexity="COMPLEX")
    lm.record_task_result("llm", True, judged_complexity="COMPLEX")
    lm.record_task_result("llm", True, judged_complexity="TRIVIAL")
    lm.record_task_result("planning", False, judged_complexity="NORMAL")
    lm.record_task_result("planning", False, judged_complexity="NORMAL")
    lm.record_task_result("planning", True, judged_complexity="NORMAL")
    lm.record_task_result("qa", True)  # 无复杂度键 → 不进双维度

    cx2 = lm.get_snapshot()["kpis"]["failure_rate_by_task_type_complexity"]
    assert cx2["llm"]["COMPLEX"] == {"total": 2, "failed": 1, "rate": 0.5}
    assert cx2["llm"]["TRIVIAL"] == {"total": 1, "failed": 0, "rate": 0.0}
    assert cx2["planning"]["NORMAL"] == {
        "total": 3, "failed": 2, "rate": 0.6667}  # 快照 rate 四舍五入 4 位
    assert "qa" not in cx2  # 无复杂度键不进双维度


def test_snapshot_complexity_normalization():
    """复杂度键大小写归一（complex → COMPLEX）；MODERATE 别名归一 NORMAL"""
    lm = LearningMetrics(enabled=True)
    lm.record_task_result("llm", True, judged_complexity="complex")
    lm.record_task_result("llm", False, judged_complexity="moderate")
    cx2 = lm.get_snapshot()["kpis"]["failure_rate_by_task_type_complexity"]
    assert cx2["llm"]["COMPLEX"] == {"total": 1, "failed": 0, "rate": 0.0}
    assert cx2["llm"]["NORMAL"] == {"total": 1, "failed": 1, "rate": 1.0}


# ════════════════════════════════════════════════════════════
#  2. 周级双维度聚合
# ════════════════════════════════════════════════════════════

def test_weekly_task_type_complexity_dimension():
    """周行 failure_rate_by_task_type_complexity 与快照口径一致"""
    lm = LearningMetrics(enabled=True)
    lm.record_task_result("llm", False, judged_complexity="COMPLEX")
    lm.record_task_result("llm", True, judged_complexity="COMPLEX")
    lm.record_task_result("planning", False, judged_complexity="NORMAL")
    row = lm.get_weekly_kpis(weeks=1)[0]
    cx2 = row["failure_rate_by_task_type_complexity"]
    assert cx2["llm"]["COMPLEX"] == {"total": 2, "failed": 1, "rate": 0.5}
    assert cx2["planning"]["NORMAL"] == {"total": 1, "failed": 1, "rate": 1.0}
    # 周行同时保留单维度复杂度桶（任务2 口径不变）
    assert row["complexity_failure_rate"]["COMPLEX"] == {"total": 2, "failed": 1, "rate": 0.5}


# ════════════════════════════════════════════════════════════
#  3. 向后兼容
# ════════════════════════════════════════════════════════════

def test_backward_compat_task_type_and_complexity_buckets():
    """双维度新增不影响 task_type 口径与单维度复杂度桶"""
    lm = LearningMetrics(enabled=True)
    lm.record_task_result("llm", False, judged_complexity="COMPLEX")
    lm.record_task_result("llm", True)
    snap = lm.get_snapshot()["kpis"]
    # task_type 口径不变（含无复杂度键条目）
    assert snap["failure_rate_by_task_type"]["llm"] == {
        "total": 2, "failed": 1, "rate": 0.5}
    # 单维度复杂度桶不变
    assert snap["failure_rate_by_task_type_complexity"]["llm"]["COMPLEX"] == {
        "total": 1, "failed": 1, "rate": 1.0}
    # 既有 kpis 键全部保留（新增键为纯增量）
    required = {
        "token_reuse_rate", "skill_hit_rate", "workflow_hit_rate",
        "failure_rate_by_task_type", "feedback_rating_trend",
        "artifact_delta", "evolution_adoption_rate",
    }
    assert required.issubset(set(snap.keys()))


def test_judged_complexity_none_zero_impact():
    """judged_complexity=None（生产多数埋点）零影响：不进双维度桶"""
    lm = LearningMetrics(enabled=True)
    for _ in range(5):
        lm.record_task_result("llm", success=True)
    cx2 = lm.get_snapshot()["kpis"]["failure_rate_by_task_type_complexity"]
    assert cx2 == {}
    assert lm.get_snapshot()["kpis"]["failure_rate_by_task_type"]["llm"]["total"] == 5


# ════════════════════════════════════════════════════════════
#  4. 持久化重启恢复
# ════════════════════════════════════════════════════════════

def test_persistence_restart_double_dimension(tmp_path):
    """模拟重启：双维度数据跨重启可累计（与周级滚动统计同源同形）"""
    db = tmp_path / "lm_cx.db"
    lm_a = LearningMetrics(persistence=_mk_persistence(db))
    lm_a.record_task_result("llm", False, judged_complexity="COMPLEX")
    lm_a.record_task_result("planning", True, judged_complexity="NORMAL")
    lm_a.flush()
    rows_a = lm_a.get_weekly_kpis(weeks=1)[0]

    lm_b = LearningMetrics(persistence=_mk_persistence(db))
    rows_b = lm_b.get_weekly_kpis(weeks=1)[0]
    assert rows_b["failure_rate_by_task_type_complexity"] == \
        rows_a["failure_rate_by_task_type_complexity"]
    cx2 = lm_b.get_snapshot()["kpis"]["failure_rate_by_task_type_complexity"]
    assert cx2["llm"]["COMPLEX"] == {"total": 1, "failed": 1, "rate": 1.0}
    assert cx2["planning"]["NORMAL"] == {"total": 1, "failed": 0, "rate": 0.0}
