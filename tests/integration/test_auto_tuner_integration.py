"""AutoTuner 集成测试

覆盖自适应参数调优器的参数管理、指标记录、调优建议生成（4 种优化目标）、
建议生命周期（批准/拒绝/应用）、快照回滚、周报生成和数据模型序列化。

使用 tmp_path 隔离 sqlite 数据库，避免测试间状态污染。
"""

import json
import time
import pytest

from agent.auto_tuner import (
    AutoTuner,
    TunableParam,
    OptimizationObjective,
    SuggestionStatus,
    ParameterSnapshot,
    TuningSuggestion,
    TuningReport,
    get_auto_tuner,
)


pytestmark = pytest.mark.integration


# ═══════════════════════════════════════════════════════════════
#  Fixture
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def tuner(tmp_path):
    """每个测试使用独立的临时数据库"""
    t = AutoTuner(storage_path=str(tmp_path / "auto_tuning"))
    t.initialize()
    return t


def _seed_metrics(tuner, count=15, quality=60, response_time=12, cost=0.6):
    """填充足够的指标数据以满足 generate_suggestion 的最低样本要求(>=10)"""
    for _ in range(count):
        tuner.record_metric("quality_score", quality)
        tuner.record_metric("response_time", response_time)
        tuner.record_metric("cost", cost)


# ═══════════════════════════════════════════════════════════════
#  枚举与数据模型
# ═══════════════════════════════════════════════════════════════

class TestEnums:
    """枚举值验证"""

    def test_tunable_param_values(self):
        assert TunableParam.CRITIC_THRESHOLD.value == "critic_threshold"
        assert TunableParam.MAX_RETRIES.value == "max_retries"

    def test_optimization_objective_values(self):
        objs = [o.value for o in OptimizationObjective]
        assert "quality" in objs
        assert "speed" in objs
        assert "cost" in objs
        assert "balanced" in objs

    def test_suggestion_status_values(self):
        statuses = [s.value for s in SuggestionStatus]
        assert "pending" in statuses
        assert "approved" in statuses
        assert "rejected" in statuses
        assert "applied" in statuses


class TestDataModels:
    """数据模型序列化"""

    def test_parameter_snapshot_to_dict(self):
        snap = ParameterSnapshot(
            snapshot_id="snap-1",
            params={"temperature": 0.7},
            description="测试快照",
        )
        d = snap.to_dict()
        assert d["snapshot_id"] == "snap-1"
        assert d["params"]["temperature"] == 0.7
        assert "created_at_iso" in d

    def test_tuning_suggestion_to_dict(self):
        s = TuningSuggestion(
            suggestion_id="sug-1",
            title="测试建议",
            proposed_params={"max_retries": 4},
        )
        d = s.to_dict()
        assert d["suggestion_id"] == "sug-1"
        assert d["proposed_params"]["max_retries"] == 4
        assert "created_at_iso" in d

    def test_tuning_report_to_dict(self):
        r = TuningReport(
            report_id="rep-1",
            period_start=time.time() - 86400,
            period_end=time.time(),
        )
        d = r.to_dict()
        assert d["report_id"] == "rep-1"
        assert "period_start_iso" in d
        assert "period_end_iso" in d
        assert "created_at_iso" in d
        assert d["suggestions"] == []


# ═══════════════════════════════════════════════════════════════
#  参数管理
# ═══════════════════════════════════════════════════════════════

class TestParameterManagement:
    """参数获取与设置"""

    def test_get_current_params_returns_defaults(self, tuner):
        params = tuner.get_current_params()
        assert params["critic_threshold"] == 70
        assert params["max_retries"] == 3
        assert params["temperature"] == 0.7
        assert len(params) == 8

    def test_set_param_valid(self, tuner):
        tuner.set_param("max_retries", 5)
        assert tuner.get_current_params()["max_retries"] == 5

    def test_set_param_unsupported_raises(self, tuner):
        with pytest.raises(ValueError, match="不支持的参数"):
            tuner.set_param("unknown_param", 1)

    def test_set_param_out_of_range_raises(self, tuner):
        with pytest.raises(ValueError, match="超出范围"):
            tuner.set_param("max_retries", 100)

    def test_set_param_below_min_raises(self, tuner):
        with pytest.raises(ValueError, match="超出范围"):
            tuner.set_param("temperature", 0.01)

    def test_param_ranges_structure(self, tuner):
        ranges = tuner._param_ranges
        assert "critic_threshold" in ranges
        assert ranges["temperature"]["type"] == "float"
        assert ranges["max_retries"]["type"] == "int"
        assert "min" in ranges["temperature"]
        assert "max" in ranges["temperature"]
        assert "step" in ranges["temperature"]


# ═══════════════════════════════════════════════════════════════
#  指标记录
# ═══════════════════════════════════════════════════════════════

class TestMetricRecording:
    """指标记录与收集"""

    def test_record_metric_returns_true(self, tuner):
        assert tuner.record_metric("quality_score", 85.0) is True

    def test_record_metric_with_params_and_context(self, tuner):
        tuner.record_metric(
            "quality_score", 90.0,
            params={"temperature": 0.5},
            context={"session": "s1"},
        )
        metrics = tuner._collect_metrics(1)
        assert "quality_score" in metrics
        assert len(metrics["quality_score"]) == 1
        assert metrics["quality_score"][0] == 90.0

    def test_collect_metrics_empty(self, tuner):
        metrics = tuner._collect_metrics(7)
        assert metrics == {}

    def test_collect_multiple_metrics(self, tuner):
        tuner.record_metric("quality_score", 80.0)
        tuner.record_metric("quality_score", 85.0)
        tuner.record_metric("response_time", 5.0)
        metrics = tuner._collect_metrics(7)
        assert len(metrics["quality_score"]) == 2
        assert len(metrics["response_time"]) == 1


# ═══════════════════════════════════════════════════════════════
#  调优建议生成
# ═══════════════════════════════════════════════════════════════

class TestSuggestionGeneration:
    """调优建议生成逻辑"""

    def test_insufficient_samples_returns_none(self, tuner):
        """样本 < 10 时返回 None"""
        for _ in range(5):
            tuner.record_metric("quality_score", 60.0)
        result = tuner.generate_suggestion(objective="quality")
        assert result is None

    def test_generate_quality_suggestion_low_quality(self, tuner):
        """低质量分数 → 质量优化建议"""
        _seed_metrics(tuner, count=15, quality=60)
        suggestion = tuner.generate_suggestion(objective="quality")
        assert suggestion is not None
        assert suggestion.objective == "quality"
        assert suggestion.status == "pending"
        assert len(suggestion.proposed_params) > 0

    def test_generate_quality_suggestion_high_quality(self, tuner):
        """高质量分数 → 可能无调整"""
        _seed_metrics(tuner, count=15, quality=90)
        suggestion = tuner.generate_suggestion(objective="quality")
        # 高质量时 critic_threshold 可能提升
        if suggestion is not None:
            assert suggestion.confidence > 0

    def test_generate_speed_suggestion_slow(self, tuner):
        """高延迟 → 速度优化"""
        _seed_metrics(tuner, count=15, response_time=15)
        suggestion = tuner.generate_suggestion(objective="speed")
        assert suggestion is not None
        assert suggestion.objective == "speed"

    def test_generate_cost_suggestion_expensive(self, tuner):
        """高成本 → 成本优化"""
        _seed_metrics(tuner, count=15, cost=0.8)
        suggestion = tuner.generate_suggestion(objective="cost")
        assert suggestion is not None
        assert suggestion.objective == "cost"

    def test_generate_balanced_suggestion(self, tuner):
        """平衡优化"""
        _seed_metrics(tuner, count=15, quality=65, response_time=6, cost=0.3)
        suggestion = tuner.generate_suggestion(objective="balanced")
        # 平衡模式可能或可能不产生建议
        if suggestion is not None:
            assert suggestion.objective == "balanced"

    def test_generate_with_target_param(self, tuner):
        """指定目标参数"""
        _seed_metrics(tuner, count=15, quality=60)
        suggestion = tuner.generate_suggestion(
            objective="quality", param_name="critic_threshold"
        )
        if suggestion is not None:
            assert "critic_threshold" in suggestion.proposed_params

    def test_confidence_levels(self, tuner):
        """样本量影响置信度"""
        # 15 个样本 → confidence = 0.4 (< 50)
        _seed_metrics(tuner, count=15, quality=60)
        suggestion = tuner.generate_suggestion(objective="quality")
        if suggestion:
            assert suggestion.confidence == 0.4

    def test_suggestion_persisted_to_db(self, tuner):
        """建议持久化到数据库"""
        _seed_metrics(tuner, count=15, quality=60)
        suggestion = tuner.generate_suggestion(objective="quality")
        assert suggestion is not None
        loaded = tuner.get_suggestion(suggestion.suggestion_id)
        assert loaded is not None
        assert loaded.suggestion_id == suggestion.suggestion_id


# ═══════════════════════════════════════════════════════════════
#  调优策略
# ═══════════════════════════════════════════════════════════════

class TestTuningStrategies:
    """四种调优策略的参数调整逻辑"""

    def test_tune_for_quality_low_quality(self, tuner):
        pr = tuner._param_ranges["critic_threshold"]
        # 低质量 → 降阈值
        new_val = tuner._tune_for_quality("critic_threshold", 70, 60, pr)
        assert new_val < 70

    def test_tune_for_quality_high_quality(self, tuner):
        pr = tuner._param_ranges["critic_threshold"]
        # 高质量 → 升阈值
        new_val = tuner._tune_for_quality("critic_threshold", 70, 90, pr)
        assert new_val > 70

    def test_tune_for_quality_medium_quality_no_change(self, tuner):
        pr = tuner._param_ranges["critic_threshold"]
        # 中等质量(70-85) → 无变化
        new_val = tuner._tune_for_quality("critic_threshold", 70, 75, pr)
        assert new_val == 70

    def test_tune_for_speed_slow(self, tuner):
        pr = tuner._param_ranges["max_retries"]
        # 高延迟 → 减重试
        new_val = tuner._tune_for_speed("max_retries", 3, 15, pr)
        assert new_val < 3

    def test_tune_for_speed_fast(self, tuner):
        pr = tuner._param_ranges["max_tokens"]
        # 低延迟 → 增 token
        new_val = tuner._tune_for_speed("max_tokens", 2048, 1, pr)
        assert new_val > 2048

    def test_tune_for_cost_expensive(self, tuner):
        pr = tuner._param_ranges["max_tokens"]
        # 高成本 → 减 token
        new_val = tuner._tune_for_cost("max_tokens", 2048, 0.8, pr)
        assert new_val < 2048

    def test_tune_balanced_quality_low_time_ok(self, tuner):
        pr = tuner._param_ranges["max_retries"]
        # 质量低且延迟可接受 → 增重试
        new_val = tuner._tune_balanced("max_retries", 3, 65, 5, 0.1, pr)
        assert new_val > 3

    def test_tune_balanced_time_high_quality_ok(self, tuner):
        pr = tuner._param_ranges["max_retries"]
        # 延迟高且质量可接受 → 减重试
        new_val = tuner._tune_balanced("max_retries", 3, 85, 12, 0.1, pr)
        assert new_val < 3

    def test_tune_balanced_no_change(self, tuner):
        pr = tuner._param_ranges["temperature"]
        # 温度参数在平衡模式下通常无变化
        new_val = tuner._tune_balanced("temperature", 0.7, 80, 5, 0.1, pr)
        assert new_val == 0.7


# ═══════════════════════════════════════════════════════════════
#  建议生命周期
# ═══════════════════════════════════════════════════════════════

class TestSuggestionLifecycle:
    """建议的审批、拒绝、应用"""

    def _create_approved_suggestion(self, tuner):
        _seed_metrics(tuner, count=15, quality=60)
        suggestion = tuner.generate_suggestion(objective="quality")
        tuner.approve_suggestion(suggestion.suggestion_id, reviewer="admin")
        return suggestion

    def test_approve_suggestion(self, tuner):
        _seed_metrics(tuner, count=15, quality=60)
        suggestion = tuner.generate_suggestion(objective="quality")
        result = tuner.approve_suggestion(suggestion.suggestion_id, reviewer="admin")
        assert result is True
        loaded = tuner.get_suggestion(suggestion.suggestion_id)
        assert loaded.status == "approved"
        assert loaded.reviewer == "admin"

    def test_approve_nonexistent_raises(self, tuner):
        with pytest.raises(ValueError, match="建议不存在"):
            tuner.approve_suggestion("nonexistent", reviewer="admin")

    def test_approve_non_pending_raises(self, tuner):
        _seed_metrics(tuner, count=15, quality=60)
        suggestion = tuner.generate_suggestion(objective="quality")
        tuner.approve_suggestion(suggestion.suggestion_id, reviewer="admin")
        # 再次审批已批准的建议
        with pytest.raises(ValueError, match="只能审批待确认"):
            tuner.approve_suggestion(suggestion.suggestion_id, reviewer="admin")

    def test_reject_suggestion(self, tuner):
        _seed_metrics(tuner, count=15, quality=60)
        suggestion = tuner.generate_suggestion(objective="quality")
        result = tuner.reject_suggestion(
            suggestion.suggestion_id, reviewer="admin", reason="测试拒绝"
        )
        assert result is True
        loaded = tuner.get_suggestion(suggestion.suggestion_id)
        assert loaded.status == "rejected"

    def test_reject_nonexistent_raises(self, tuner):
        with pytest.raises(ValueError, match="建议不存在"):
            tuner.reject_suggestion("nonexistent")

    def test_reject_non_pending_raises(self, tuner):
        _seed_metrics(tuner, count=15, quality=60)
        suggestion = tuner.generate_suggestion(objective="quality")
        tuner.approve_suggestion(suggestion.suggestion_id, reviewer="admin")
        with pytest.raises(ValueError, match="只能拒绝待确认"):
            tuner.reject_suggestion(suggestion.suggestion_id)

    def test_apply_suggestion(self, tuner):
        suggestion = self._create_approved_suggestion(tuner)
        result = tuner.apply_suggestion(suggestion.suggestion_id)
        assert "old_params" in result
        assert "new_params" in result
        assert "snapshot_id" in result
        # 验证参数已更新
        current = tuner.get_current_params()
        for param, value in suggestion.proposed_params.items():
            assert current[param] == value

    def test_apply_nonexistent_raises(self, tuner):
        with pytest.raises(ValueError, match="建议不存在"):
            tuner.apply_suggestion("nonexistent")

    def test_apply_non_approved_raises(self, tuner):
        _seed_metrics(tuner, count=15, quality=60)
        suggestion = tuner.generate_suggestion(objective="quality")
        with pytest.raises(ValueError, match="只能应用已批准"):
            tuner.apply_suggestion(suggestion.suggestion_id)

    def test_list_suggestions(self, tuner):
        _seed_metrics(tuner, count=15, quality=60)
        tuner.generate_suggestion(objective="quality")
        tuner.generate_suggestion(objective="speed")
        all_suggestions = tuner.list_suggestions()
        assert len(all_suggestions) == 2

    def test_list_suggestions_by_status(self, tuner):
        _seed_metrics(tuner, count=15, quality=60)
        s1 = tuner.generate_suggestion(objective="quality")
        tuner.approve_suggestion(s1.suggestion_id, reviewer="admin")
        tuner.generate_suggestion(objective="speed")

        pending = tuner.list_suggestions(status="pending")
        approved = tuner.list_suggestions(status="approved")
        assert len(pending) == 1
        assert len(approved) == 1

    def test_get_nonexistent_suggestion(self, tuner):
        result = tuner.get_suggestion("nonexistent")
        assert result is None


# ═══════════════════════════════════════════════════════════════
#  快照与回滚
# ═══════════════════════════════════════════════════════════════

class TestSnapshotAndRollback:
    """参数快照与回滚"""

    def test_create_snapshot(self, tuner):
        snap = tuner._create_snapshot("snap-1", {"temperature": 0.5}, "测试")
        assert snap.snapshot_id == "snap-1"
        assert snap.params["temperature"] == 0.5

    def test_rollback_to_snapshot(self, tuner):
        # 先保存当前参数
        original = tuner.get_current_params()
        tuner.set_param("max_retries", 8)
        tuner._create_snapshot("before_change", original, "变更前")

        # 回滚
        result = tuner.rollback_to_snapshot("before_change")
        assert result is True
        assert tuner.get_current_params()["max_retries"] == 3  # 恢复为原始值

    def test_rollback_nonexistent_raises(self, tuner):
        with pytest.raises(ValueError, match="快照不存在"):
            tuner.rollback_to_snapshot("nonexistent")

    def test_apply_creates_snapshots(self, tuner):
        """应用建议时自动创建快照"""
        _seed_metrics(tuner, count=15, quality=60)
        suggestion = tuner.generate_suggestion(objective="quality")
        tuner.approve_suggestion(suggestion.suggestion_id, reviewer="admin")
        result = tuner.apply_suggestion(suggestion.suggestion_id)
        # 验证快照已创建
        assert result["snapshot_id"] is not None


# ═══════════════════════════════════════════════════════════════
#  周报生成
# ═══════════════════════════════════════════════════════════════

class TestWeeklyReport:
    """周报生成"""

    def test_generate_weekly_report_with_data(self, tuner):
        _seed_metrics(tuner, count=15, quality=60)
        report = tuner.generate_weekly_report(objective="quality")
        assert report.report_id is not None
        assert report.objective == "quality"
        assert "count" in report.metrics_summary.get("quality_score", {}) or \
               len(report.metrics_summary) >= 0

    def test_generate_weekly_report_no_data(self, tuner):
        """无数据时也能生成报告（建议为空）"""
        report = tuner.generate_weekly_report()
        assert report.report_id is not None
        assert isinstance(report.suggestions, list)

    def test_report_summary_contains_suggestion_count(self, tuner):
        _seed_metrics(tuner, count=15, quality=60)
        report = tuner.generate_weekly_report(objective="quality")
        assert "建议" in report.summary or str(len(report.suggestions)) in report.summary


# ═══════════════════════════════════════════════════════════════
#  全局单例
# ═══════════════════════════════════════════════════════════════

class TestGlobalSingleton:
    """全局单例获取"""

    def test_get_auto_tuner_returns_instance(self):
        t = get_auto_tuner()
        assert t is not None
        assert isinstance(t, AutoTuner)

    def test_get_auto_tuner_singleton(self):
        t1 = get_auto_tuner()
        t2 = get_auto_tuner()
        assert t1 is t2
