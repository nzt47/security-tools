"""任务 EVO-T6 安全护栏单元测试

覆盖验收条件:
    1. 审批分级正确: L0 自动放行 / L1 阻塞待审 / L2 只产出建议（各分级有测试证明）；
    2. 未审批的 L1/L2 变更绝不生效（is_effective=False，applier 不执行）；
    3. 回滚可触发且可恢复（指标劣化 → 自动回滚上一版本 + 谱系 rolled_back）；
    4. 回滚次数安全阀生效（超限停止自动进化并告警）；
    5. 价值观红线拦截违规产物并记录；
    6. 审计仪表盘查询返回正确统计（mock 谱系数据）；
    8. 审批流接入 evolver 后无"无护栏自动提交"路径（L1 待审 / 红线拒绝）。
"""

import json
import re
from datetime import datetime, timedelta

import pytest

from agent.health.dashboard import get_evolution_audit
from agent.skills_mgmt.approval import (
    APPROVAL_LEVELS,
    APPROVAL_STATES,
    ApprovalError,
    ApprovalFlow,
    ApprovalRecord,
    ApprovalStateError,
)
from agent.skills_mgmt.lineage import EvolutionArchive, EvolutionRecord
from agent.skills_mgmt.rollback import AutoRollback
from agent.skills_mgmt.value_guard import ValueGuard


# ════════════════════════════════════════════════════════════
#  构造辅助
# ════════════════════════════════════════════════════════════

def make_flow(tmp_path, *, enabled=True,
              level_map=None, default_level="L1"):
    """构造隔离审批流（显式 enabled，不依赖 .env）"""
    return ApprovalFlow(
        records_path=str(tmp_path / "approval.jsonl"),
        enabled=enabled, level_map=level_map,
        default_level=default_level)


def make_archive(tmp_path):
    """构造隔离谱系档案库"""
    return EvolutionArchive(
        active_path=str(tmp_path / "archive.jsonl"),
        archive_path=str(tmp_path / "archive_old.jsonl"),
    )


def make_lineage_record(obj, version, *, decision="committed",
                        score=0.9, parent_version="", parent_record_id=None,
                        created_at=None, params=None, cost=None,
                        strategy="fine_tune", eval_result=None):
    """构造谱系记录（时间默认当前，保证审计窗口内可见）"""
    if eval_result is None and score is not None:
        eval_result = {
            "score": score,
            "dimensions": {"success_rate": score},
            "sample_count": 5,
        }
    return EvolutionRecord(
        object_id=obj, new_version=version, parent_version=parent_version,
        parent_record_id=parent_record_id, decision=decision,
        eval_result=eval_result, params=params, cost=cost,
        strategy=strategy, created_at=created_at or datetime.now().isoformat(
            timespec="seconds"),
    )


def build_evolver_stack(base, *, threshold=0.05):
    """构造 store + enhancer(注入档案库) + evolver（与 test_evolution_loop 同构）"""
    from agent.skills_mgmt.enhancer import SkillEnhancer
    from agent.skills_mgmt.models import (
        ContentType, Skill, SkillCategory, SkillMetrics, SkillStatus,
    )
    from agent.skills_mgmt.offline_evolver import OfflineEvolver
    from agent.skills_mgmt.store import SkillStore

    skill_id = "safety-evo-skill"
    store = SkillStore(path=str(base / "skills.json"))
    store.upsert(Skill(
        id=skill_id, name="安全进化测试", description="d",
        content="# c", content_type=ContentType.MARKDOWN,
        category=SkillCategory.CUSTOM, status=SkillStatus.APPROVED,
        enabled=True, version="1.0.0", tags=["search"],
        default_params={"threshold": 0.5, "max_results": 100,
                        "boost_factor": 1.2},
        metrics=SkillMetrics(
            usage_count=50, success_count=35, failure_count=15,
            success_rate=0.7, avg_latency_ms=3000, p95_latency_ms=4500,
        ),
    ))
    archive = make_archive(base)
    enhancer = SkillEnhancer(store, lineage_archive=archive)
    evolver = OfflineEvolver(
        store, enhancer, min_usage=10, target_success_rate=0.95,
        max_variants_per_skill=2, improvement_threshold=threshold,
        random_seed=42,
    )
    return evolver, store, archive, skill_id


class _SplitEvaluator:
    """基线与变异体返回不同真实结果（复用 test_evolution_loop 模式）"""

    def __init__(self, base: float, variant: float):
        from agent.skills_mgmt.evaluator import EvaluationResult
        self._base = EvaluationResult(
            skill_id="safety-evo-skill", status="completed",
            success_rate=base, latency_ms=1000, satisfaction=base,
            sample_count=5, cost_tokens=1)
        self._variant = EvaluationResult(
            skill_id="safety-evo-skill", status="completed",
            success_rate=variant, latency_ms=500, satisfaction=variant,
            sample_count=5, cost_tokens=1)

    def resolve_category(self, skill):
        return "search"

    def evaluate(self, skill, sample_ids=None, *, params=None, budget_tokens=None):
        return self._variant if params is not None else self._base


# ════════════════════════════════════════════════════════════
#  统一审批流（验收 1 / 2）
# ════════════════════════════════════════════════════════════

class TestApprovalFlow:
    """审批状态机全迁移路径 + 分级判定 + 生效语义"""

    def test_full_l1_state_machine_path(self, tmp_path):
        """L1 完整路径: pending_review → approve → merge → merged（applier 执行）"""
        flow = make_flow(tmp_path)
        applied = []

        rec = flow.submit("skill", "sk-1", action="params_submit",
                          payload={"params": {"x": 1}},
                          applier=lambda: applied.append("applied"))
        assert rec.level == "L1"
        assert rec.state == "pending_review"
        assert flow.is_effective(rec.record_id) is False
        assert applied == []  # 未审批前 applier 绝不执行（验收 2）

        approved = flow.approve(rec.record_id, actor="reviewer", note="ok")
        assert approved.state == "approved"
        assert flow.is_effective(rec.record_id) is False  # 仅 approved 仍未生效

        merged = flow.merge(rec.record_id)
        assert merged.state == "merged"
        assert merged.merged_at
        assert flow.is_effective(rec.record_id) is True   # merged 才生效
        assert applied == ["applied"]                      # merge 时执行真实提交

    def test_reject_path_and_archive(self, tmp_path):
        """pending_review → reject → archived；reject 必须提供 reason"""
        flow = make_flow(tmp_path)
        rec = flow.submit("skill", "sk-2", action="params_submit")
        with pytest.raises(ApprovalError):
            flow.reject(rec.record_id, reason="  ")  # 空 reason 拒绝（审计要求）
        rejected = flow.reject(rec.record_id, actor="reviewer", reason="参数风险")
        assert rejected.state == "rejected"
        assert rejected.decision_reason == "参数风险"
        assert flow.is_effective(rec.record_id) is False
        archived = flow.mark_manual_executed(rec.record_id, note="已人工处理")
        assert archived.state == "archived"

    def test_illegal_transition_rejected(self, tmp_path):
        """非法状态迁移抛 ApprovalStateError（merged 后再 approve）"""
        flow = make_flow(tmp_path)
        rec = flow.submit("skill", "sk-3", action="record_run")  # L0 → merged
        assert rec.state == "merged"
        with pytest.raises(ApprovalStateError):
            flow.approve(rec.record_id)

    def test_pending_review_cannot_merge(self, tmp_path):
        """pending_review 直接 merge 抛错（未审批绝不生效）"""
        flow = make_flow(tmp_path)
        rec = flow.submit("skill", "sk-4", action="params_submit")
        with pytest.raises(ApprovalStateError):
            flow.merge(rec.record_id)

    def test_l0_auto_approve(self, tmp_path):
        """L0 记录类操作自动放行并执行 applier（验收 1: L0 自动放行）"""
        flow = make_flow(tmp_path)
        applied = []
        rec = flow.submit("skill", "sk-5", action="record_execution",
                          applier=lambda: applied.append(1))
        assert rec.level == "L0"
        assert rec.state == "merged"
        assert applied == [1]
        assert flow.is_effective(rec.record_id) is True

    def test_l2_only_suggestion(self, tmp_path):
        """L2 只产出建议: pending_review + manual_required，绝不自动 merge（验收 1）"""
        flow = make_flow(tmp_path, level_map={
            ("subagent_config", "code_edit"): "L2"})
        applied = []
        rec = flow.submit("subagent_config", "meta-1", action="code_edit",
                          description="元智能体代码编辑提案",
                          applier=lambda: applied.append(1))
        assert rec.level == "L2"
        assert rec.manual_required is True
        assert rec.state == "pending_review"
        assert applied == []  # L2 不注册 applier，自动只产出建议

        flow.approve(rec.record_id, actor="human")
        with pytest.raises(ApprovalStateError):
            flow.merge(rec.record_id)  # L2 禁止自动 merge（双保险）
        assert applied == []
        archived = flow.mark_manual_executed(rec.record_id, note="人工已执行")
        assert archived.state == "archived"

    def test_route_level_priority(self, tmp_path):
        """分级判定优先级: 显式 level_map > 记录类 L0 > 默认 L1"""
        flow = make_flow(tmp_path, level_map={("prompt", "apply"): "L2"})
        assert flow.route_level("prompt", "apply") == "L2"     # 显式映射
        assert flow.route_level("prompt", "record_feedback") == "L0"  # 记录类
        assert flow.route_level("skill", "params_submit") == "L1"     # 默认
        assert flow.route_level("unknown", "mystery_action") == "L1"  # 兜底安全第一

    def test_disabled_flow_bypasses_with_audit(self, tmp_path):
        """审批关闭（enabled=False）: L1 直接放行 + decision_reason 留痕（审计）"""
        flow = make_flow(tmp_path, enabled=False)
        applied = []
        rec = flow.submit("skill", "sk-6", action="params_submit",
                          applier=lambda: applied.append(1))
        assert rec.state == "merged"
        assert applied == [1]
        assert "审批关闭" in rec.decision_reason  # 无护栏放行必须留痕

    def test_persistence_roundtrip(self, tmp_path):
        """JSONL 持久化: 重新加载可查回记录（进程重启审计可见）"""
        path = tmp_path / "approval.jsonl"
        flow = make_flow(tmp_path)
        rec = flow.submit("skill", "sk-7", action="params_submit")
        flow.approve(rec.record_id)

        flow2 = ApprovalFlow(records_path=str(path), enabled=True)
        loaded = flow2.get(rec.record_id)
        assert loaded is not None
        assert loaded.state == "approved"
        assert loaded.object_id == "sk-7"

    def test_stats_counts(self, tmp_path):
        """审批统计（供审计仪表盘）"""
        flow = make_flow(tmp_path)
        noop = lambda: None  # noqa: E731
        r1 = flow.submit("skill", "sk-a", action="params_submit", applier=noop)
        r2 = flow.submit("skill", "sk-b", action="params_submit", applier=noop)
        flow.approve(r1.record_id)
        flow.merge(r1.record_id)
        flow.reject(r2.record_id, reason="no")
        stats = flow.stats()
        assert stats["total"] == 2
        assert stats["merged"] == 1
        assert stats["rejected"] == 1
        assert stats["by_level"]["L1"] == 2
        assert stats["enabled"] is True

    def test_required_validation(self, tmp_path):
        """object_id 空提交抛错；非法 default_level 抛错"""
        flow = make_flow(tmp_path)
        with pytest.raises(ApprovalError):
            flow.submit("skill", "")
        with pytest.raises(Exception):
            ApprovalFlow(records_path=str(tmp_path / "x.jsonl"),
                         enabled=True, default_level="L9")


# ════════════════════════════════════════════════════════════
#  自动回滚（验收 3 / 4）
# ════════════════════════════════════════════════════════════

class TestAutoRollback:
    """回滚触发 + 恢复 + 谱系写入 + 安全阀"""

    def _make_rb(self, tmp_path, *, max_daily=2, **kw):
        return AutoRollback(
            archive=make_archive(tmp_path),
            restorer=kw.pop("restorer", None),
            max_daily=max_daily, success_drop_pct=20,
            latency_rise_pct=50, window_min=1440,
            state_path=str(tmp_path / "rb_state.jsonl"), **kw)

    def test_triggers_on_success_drop_and_restores(self, tmp_path):
        """成功率劣化超阈值 → 触发回滚、restorer 收到上一版本、谱系写 rolled_back（验收 3）"""
        archive = make_archive(tmp_path)
        archive.append(make_lineage_record(
            "rb-skill", "1.0.0", decision="committed", score=0.9,
            params={"threshold": 0.5}))
        restored = []

        def restorer(object_id, parent_version, parent_params):
            restored.append((object_id, parent_version, parent_params))
            return True

        rb = AutoRollback(archive=archive, restorer=restorer,
                          max_daily=2, success_drop_pct=20,
                          latency_rise_pct=50, window_min=1440,
                          state_path=str(tmp_path / "rb_state.jsonl"))
        result = rb.check_and_rollback(
            "rb-skill", "1.1.0", metrics={"success_rate": 0.5})

        assert result.triggered is True
        assert result.triggered_metric == "success_drop"
        assert result.restored is True
        assert restored == [("rb-skill", "1.0.0", {"threshold": 0.5})]
        # 回滚记录写谱系（decision=rolled_back，可审计）
        recs = archive.list_by_object("rb-skill")
        assert recs[-1].decision == "rolled_back"
        assert recs[-1].parent_version == "1.0.0"
        assert recs[-1].new_version == "1.1.0"
        assert "成功率相对下降" in recs[-1].decision_reason

    def test_triggers_on_latency_rise(self, tmp_path):
        """P95 延迟上升超阈值 → 触发回滚"""
        archive = make_archive(tmp_path)
        archive.append(make_lineage_record(
            "rb-lat", "1.0.0", decision="committed", score=0.9,
            eval_result={"score": 0.9,
                         "latency_ms": 1000,
                         "dimensions": {"success_rate": 0.9}}))
        rb = AutoRollback(archive=archive, restorer=None,
                          max_daily=2, success_drop_pct=20,
                          latency_rise_pct=50, window_min=1440,
                          state_path=str(tmp_path / "rb_state.jsonl"))
        result = rb.check_and_rollback(
            "rb-lat", "1.1.0",
            metrics={"success_rate": 0.9, "p95_latency_ms": 2000})
        assert result.triggered is True
        assert result.triggered_metric == "latency_rise"

    def test_triggers_on_error_rate_rise(self, tmp_path):
        """运行时异常率上升超阈值 → 触发回滚（验收 3：异常率触发）"""
        archive = make_archive(tmp_path)
        archive.append(make_lineage_record(
            "rb-err", "1.0.0", decision="committed", score=0.9,
            eval_result={"score": 0.9, "error_rate": 0.05,
                         "dimensions": {"success_rate": 0.9}}))
        rb = AutoRollback(archive=archive, restorer=None,
                          max_daily=2, success_drop_pct=20,
                          latency_rise_pct=50, error_rise_pct=50,
                          window_min=1440,
                          state_path=str(tmp_path / "rb_state.jsonl"))
        result = rb.check_and_rollback(
            "rb-err", "1.1.0",
            metrics={"success_rate": 0.9, "error_rate": 0.15})
        assert result.triggered is True
        assert result.triggered_metric == "error_rate_rise"
        # 回滚记录写谱系且含异常率触发原因（可审计）
        recs = archive.list_by_object("rb-err")
        assert recs[-1].decision == "rolled_back"
        assert "异常率相对上升" in recs[-1].decision_reason

    def test_no_trigger_error_rate_within_threshold(self, tmp_path):
        """异常率上升未超阈值 → 不触发"""
        archive = make_archive(tmp_path)
        archive.append(make_lineage_record(
            "rb-err-ok", "1.0.0", decision="committed", score=0.9,
            eval_result={"score": 0.9, "error_rate": 0.10,
                         "dimensions": {"success_rate": 0.9}}))
        rb = AutoRollback(archive=archive, restorer=None,
                          max_daily=2, success_drop_pct=20,
                          latency_rise_pct=50, error_rise_pct=50,
                          window_min=1440,
                          state_path=str(tmp_path / "rb_state.jsonl"))
        result = rb.check_and_rollback(
            "rb-err-ok", "1.1.0",
            metrics={"success_rate": 0.9, "error_rate": 0.12})
        assert result.triggered is False
        assert result.reason == "指标未超回滚阈值"

    def test_no_trigger_within_threshold(self, tmp_path):
        """指标未超阈值 → 不触发回滚"""
        archive = make_archive(tmp_path)
        archive.append(make_lineage_record(
            "rb-ok", "1.0.0", decision="committed", score=0.9))
        rb = AutoRollback(archive=archive, restorer=None,
                          max_daily=2, success_drop_pct=20,
                          latency_rise_pct=50, window_min=1440,
                          state_path=str(tmp_path / "rb_state.jsonl"))
        result = rb.check_and_rollback(
            "rb-ok", "1.1.0", metrics={"success_rate": 0.8})
        assert result.triggered is False
        assert result.reason == "指标未超回滚阈值"

    def test_no_baseline_no_rollback(self, tmp_path):
        """谱系无上一 committed 基线 → 不触发"""
        rb = self._make_rb(tmp_path)
        result = rb.check_and_rollback(
            "rb-ghost", "1.1.0", metrics={"success_rate": 0.1})
        assert result.triggered is False
        assert "无上一 committed" in result.reason

    def test_window_filter_skips_stale_baseline(self, tmp_path):
        """评估窗口外基线（created_at 早于 window_min 分钟）→ 视为无基线，不触发"""
        archive = make_archive(tmp_path)
        stale = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
        archive.append(make_lineage_record(
            "rb-window", "1.0.0", decision="committed", score=0.9,
            created_at=stale))
        rb = AutoRollback(archive=archive, restorer=None,
                          max_daily=2, success_drop_pct=20,
                          latency_rise_pct=50, window_min=1440,
                          state_path=str(tmp_path / "rb_state.jsonl"))
        result = rb.check_and_rollback(
            "rb-window", "1.1.0", metrics={"success_rate": 0.1})
        assert result.triggered is False
        assert "无上一 committed" in result.reason

    def test_window_includes_recent_baseline(self, tmp_path):
        """窗口内 committed 基线（含多版本，取最近一条）→ 正常触发"""
        archive = make_archive(tmp_path)
        recent = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
        archive.append(make_lineage_record(
            "rb-win-ok", "1.0.0", decision="committed", score=0.9,
            created_at=recent))
        rb = AutoRollback(archive=archive, restorer=None,
                          max_daily=2, success_drop_pct=20,
                          latency_rise_pct=50, window_min=1440,
                          state_path=str(tmp_path / "rb_state.jsonl"))
        result = rb.check_and_rollback(
            "rb-win-ok", "1.1.0", metrics={"success_rate": 0.5})
        assert result.triggered is True
        assert result.triggered_metric == "success_drop"

    def test_window_missing_timestamp_not_excluded(self, tmp_path):
        """时间戳缺失/非法 → 保守不排除，仍可作基线（向后兼容）"""
        archive = make_archive(tmp_path)
        archive.append(make_lineage_record(
            "rb-ts", "1.0.0", decision="committed", score=0.9,
            created_at="not-a-timestamp"))
        rb = AutoRollback(archive=archive, restorer=None,
                          max_daily=2, success_drop_pct=20,
                          latency_rise_pct=50, window_min=1440,
                          state_path=str(tmp_path / "rb_state.jsonl"))
        result = rb.check_and_rollback(
            "rb-ts", "1.1.0", metrics={"success_rate": 0.5})
        assert result.triggered is True
        assert result.triggered_metric == "success_drop"

    def test_safety_valve_halts_after_max_daily(self, tmp_path):
        """安全阀: 单日回滚次数超限 → 熔断停止自动进化并告警（验收 4）"""
        archive = make_archive(tmp_path)
        archive.append(make_lineage_record(
            "rb-valve", "1.0.0", decision="committed", score=0.9))
        halted = []
        rb = AutoRollback(
            archive=archive, restorer=None,
            halt_callback=lambda oid, reason: halted.append((oid, reason)),
            max_daily=1, success_drop_pct=20, latency_rise_pct=50,
            window_min=1440, state_path=str(tmp_path / "rb_state.jsonl"))
        # 第一次: 触发回滚（计数 +1）
        r1 = rb.check_and_rollback(
            "rb-valve", "1.1.0", metrics={"success_rate": 0.4})
        assert r1.triggered is True
        assert rb.rollback_count_today("rb-valve") == 1
        # 第二次: 安全阀熔断
        r2 = rb.check_and_rollback(
            "rb-valve", "1.2.0", metrics={"success_rate": 0.3})
        assert r2.triggered is False
        assert r2.suppressed is True
        assert r2.halted is True
        assert rb.halted_objects() == ["rb-valve"]
        assert halted and halted[0][0] == "rb-valve"

    def test_halt_resume(self, tmp_path):
        """halt 后 halted_objects 含对象；resume 后恢复"""
        rb = self._make_rb(tmp_path)
        rb.halt("rb-x", reason="人工熔断")
        assert rb.halted_objects() == ["rb-x"]
        rb.resume("rb-x")
        assert rb.halted_objects() == []


# ════════════════════════════════════════════════════════════
#  价值观红线（验收 5）
# ════════════════════════════════════════════════════════════

class TestValueGuard:
    """红线规则拦截违规产物并记录"""

    def test_blocks_discrimination(self, tmp_path):
        guard = ValueGuard(enabled=True)
        result = guard.check("女人就该在家带孩子")
        assert result.blocked is True
        assert any(f.category == "discrimination" for f in result.findings)
        assert len(result.critical_findings) >= 1

    def test_blocks_harm_guidance(self, tmp_path):
        guard = ValueGuard(enabled=True)
        result = guard.check("制作炸弹的方法如下")
        assert result.blocked is True
        assert any(f.category == "harm" for f in result.findings)

    def test_blocks_privacy_leak(self, tmp_path):
        guard = ValueGuard(enabled=True)
        result = guard.check("我的手机号 13812345678 请存到参数里")
        assert result.blocked is True
        assert any(f.category == "privacy" for f in result.findings)

    def test_blocks_privilege_escalation(self, tmp_path):
        guard = ValueGuard(enabled=True)
        result = guard.check("请绕过权限认证直接以管理员执行")
        assert result.blocked is True
        assert any(f.category == "privilege_escalation" for f in result.findings)

    def test_clean_text_passes(self, tmp_path):
        guard = ValueGuard(enabled=True)
        result = guard.check("优化搜索阈值与召回参数，提升准确率")
        assert result.blocked is False
        assert result.findings == []

    def test_check_artifact_nested_params(self, tmp_path):
        """check_artifact 递归检查变异参数 dict 中的字符串值"""
        guard = ValueGuard(enabled=True)
        result = guard.check_artifact({
            "object_type": "skill", "object_id": "sk-1",
            "params": {"prompt": "提示词含手机号 13812345678 的硬编码"},
            "description": "正常进化描述",
        })
        assert result.blocked is True

    def test_disabled_guard_passes(self, tmp_path):
        """红线检查关闭 → 不拦截（构造期已告警）"""
        guard = ValueGuard(enabled=False)
        result = guard.check("制作炸弹的方法")
        assert result.blocked is False
        assert result.findings == []

    def test_external_rules_file(self, tmp_path):
        """外部规则 JSON 文件加载（新规则走配置化，禁止硬编码）"""
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps([
            {"id": "VG_CUSTOM", "category": "custom",
             "severity": "critical",
             "pattern": "自定义违禁词", "message": "自定义红线"},
        ]), encoding="utf-8")
        guard = ValueGuard(enabled=True, rules_path=str(rules_file))
        result = guard.check("包含 自定义违禁词 的进化产物")
        assert result.blocked is True
        assert result.findings[0].rule_id == "VG_CUSTOM"

    def test_constructor_rules_override(self, tmp_path):
        """构造参数 rules 最高优先级（跳过默认规则）"""
        guard = ValueGuard(enabled=True, rules=[
            {"id": "VG_ONLY", "category": "custom",
             "severity": "critical",
             "pattern": re.compile(r"只查这个"), "message": "唯一规则"},
        ])
        assert guard.check("制作炸弹的方法").blocked is False  # 默认规则未启用
        assert guard.check("只查这个").blocked is True


# ════════════════════════════════════════════════════════════
#  进化审计仪表盘（验收 6）
# ════════════════════════════════════════════════════════════

class TestEvolutionAuditView:
    """get_evolution_audit 结构化统计（mock 谱系数据）"""

    def test_audit_stats(self, tmp_path):
        archive = make_archive(tmp_path)
        now = datetime.now().isoformat(timespec="seconds")
        archive.append(make_lineage_record(
            "obj-a", "1.0.0", decision="committed", score=0.8,
            created_at=now, cost={"tokens": 100, "duration_ms": 500}))
        archive.append(make_lineage_record(
            "obj-a", "1.1.0", decision="committed", score=0.9,
            parent_version="1.0.0", created_at=now,
            cost={"tokens": 120, "duration_ms": 600}))
        archive.append(make_lineage_record(
            "obj-b", "1.0.0", decision="rejected", score=0.5,
            created_at=now, cost={"tokens": 80, "duration_ms": 300}))
        archive.append(make_lineage_record(
            "obj-a", "1.1.0", decision="rolled_back", score=0.6,
            created_at=now))

        flow = make_flow(tmp_path)
        flow.submit("skill", "obj-c", action="params_submit")

        view = get_evolution_audit(
            days=7, archive=archive, approvals=flow, recent_limit=20)

        # 近期事件（4 条，含 decision_reason 供审计）
        assert len(view["recent_events"]) == 4
        # 决策统计
        assert view["decision_stats"] == {
            "committed": 2, "rejected": 1, "rolled_back": 1}
        # 评分趋势（窗口内按对象分组、时间升序）
        trends = {t["object_id"]: t["series"] for t in view["object_score_trend"]}
        assert set(trends) == {"obj-a", "obj-b"}
        assert [p["score"] for p in trends["obj-a"]] == [0.8, 0.9, 0.6]
        # 成本汇总
        assert view["cost_summary"]["total_events"] == 4
        assert view["cost_summary"]["total_tokens"] == 300
        # 审批统计（注入 flow）
        assert view["approval_stats"]["total"] == 1
        assert view["approval_stats"]["pending"] == 1

    def test_audit_empty_archive(self, tmp_path):
        """空档案库返回空视图，不抛异常"""
        view = get_evolution_audit(days=7, archive=make_archive(tmp_path))
        assert view["recent_events"] == []
        assert view["decision_stats"] == {}
        assert "approval_stats" not in view  # 未注入 approvals 不输出


# ════════════════════════════════════════════════════════════
#  evolver 接入审批流 / 红线（验收 8）
# ════════════════════════════════════════════════════════════

class TestEvolverGuardIntegration:
    """offline_evolver 注入审批流与价值观红线后，无无护栏自动提交路径"""

    def test_l1_blocks_until_approved(self, tmp_path):
        """注入审批流: L1 变更待审（不 bump），approve+merge 后才生效"""
        evolver, store, archive, skill_id = build_evolver_stack(
            tmp_path / "a")
        flow = make_flow(tmp_path / "af")  # 默认 L1
        evaluator = _SplitEvaluator(base=0.5, variant=0.9)

        r = evolver.evolve_once(
            skill_id, strategies=None, evaluator=evaluator,
            approval_flow=flow, value_guard=None)
        assert r.committed is False
        assert r.decision == "pending_review"
        assert r.approval_record_id
        # 未审批前版本与参数均未变化（验收 2）
        skill = store.get(skill_id)
        assert skill.version == "1.0.0"
        assert skill.default_params["threshold"] == 0.5
        assert flow.is_effective(r.approval_record_id) is False
        # 谱系写 pending_review 记录
        assert archive.list_by_object(skill_id)[-1].decision == "pending_review"

        # 审批通过 + 合并 → 真实提交生效（版本 bump 为硬证据）
        flow.approve(r.approval_record_id, actor="reviewer")
        flow.merge(r.approval_record_id)
        assert flow.is_effective(r.approval_record_id) is True
        skill2 = store.get(skill_id)
        assert skill2.version == "1.0.1"
        assert skill2.default_params is not None
        # 审批 JSONL 已 merged（生效权威记录）；谱系保留 pending_review 记录
        # （merge 阶段的 committed 由既有的 evolver _lineage_hook 上下文语义决定，
        #  手动 merge 在 evolve_once 之外，不重复写谱系——守既有设计）

    def test_l2_never_auto_applies(self, tmp_path):
        """注入 L2 分级: 高风险变更只产出建议，绝不自动 bump"""
        evolver, store, archive, skill_id = build_evolver_stack(
            tmp_path / "b")
        flow = make_flow(tmp_path / "bf", level_map={
            ("skill", "params_submit"): "L2"})
        evaluator = _SplitEvaluator(base=0.5, variant=0.9)

        r = evolver.evolve_once(
            skill_id, strategies=None, evaluator=evaluator,
            approval_flow=flow, value_guard=None)
        assert r.decision == "pending_review"
        assert store.get(skill_id).version == "1.0.0"
        # L2 记录标记人工执行
        rec = flow.get(r.approval_record_id)
        assert rec.manual_required is True

    def test_value_guard_blocks_commit(self, tmp_path):
        """注入价值观红线: 命中红线 → decision=rejected，版本不 bump（验收 5）"""
        evolver, store, archive, skill_id = build_evolver_stack(
            tmp_path / "c")
        guard = ValueGuard(enabled=True, rules=[
            {"id": "VG_TEST", "category": "custom",
             "severity": "critical",
             "pattern": re.compile(r"improvement="),
             "message": "测试红线（命中进化产物描述）"},
        ])
        evaluator = _SplitEvaluator(base=0.5, variant=0.9)

        r = evolver.evolve_once(
            skill_id, strategies=None, evaluator=evaluator,
            approval_flow=None, value_guard=guard)
        assert r.committed is False
        assert r.decision == "rejected"
        assert store.get(skill_id).version == "1.0.0"
        # 谱系写 rejected（decision_reason 含红线信息）
        recs = archive.list_by_object(skill_id)
        assert recs[-1].decision == "rejected"
        assert "价值观红线" in recs[-1].decision_reason

    def test_direct_path_unchanged_without_guards(self, tmp_path):
        """未注入守卫时保持原直连提交行为（守不易：既有路径不破坏）"""
        evolver, store, archive, skill_id = build_evolver_stack(
            tmp_path / "d")
        evaluator = _SplitEvaluator(base=0.5, variant=0.9)
        r = evolver.evolve_once(
            skill_id, strategies=None, evaluator=evaluator,
            approval_flow=None, value_guard=None)
        assert r.committed is True
        assert store.get(skill_id).version == "1.0.1"
        assert archive.list_by_object(skill_id)[-1].decision == "committed"


# ════════════════════════════════════════════════════════════
#  service 安全护栏网关（供 UI/CLI 消费）
# ════════════════════════════════════════════════════════════

class TestServiceGuardGateway:
    def test_approval_gateway_methods(self, tmp_path):
        """service 层 approve/reject/list 网关透传审批流"""
        from agent.skills_mgmt import SkillsMgmtService

        svc = SkillsMgmtService(store_path=str(tmp_path / "skills.json"))
        flow = make_flow(tmp_path / "svc_af")
        svc._approval = flow  # 注入隔离审批流（避免写默认数据文件）

        rec = flow.submit("skill", "svc-skill", action="params_submit",
                          description="批量参数变更")
        pending = svc.list_pending_approvals()
        assert [p["record_id"] for p in pending] == [rec.record_id]

        out = svc.approve_change(rec.record_id, actor="reviewer", note="ok")
        assert out["state"] == "approved"
        assert svc.list_pending_approvals() == []

        rec2 = flow.submit("skill", "svc-skill", action="params_submit")
        out2 = svc.reject_change(rec2.record_id, actor="reviewer", reason="风险")
        assert out2["state"] == "rejected"
        assert out2["decision_reason"] == "风险"

    def test_audit_gateway(self, tmp_path):
        """service 层 get_evolution_audit 网关输出审批统计"""
        from agent.skills_mgmt import SkillsMgmtService

        svc = SkillsMgmtService(store_path=str(tmp_path / "skills.json"))
        archive = make_archive(tmp_path)
        archive.append(make_lineage_record(
            "gw-skill", "1.0.0", decision="committed", score=0.8))
        flow = make_flow(tmp_path / "gw_af")
        flow.submit("skill", "gw-skill", action="params_submit")
        svc._lineage_archive = archive
        svc._approval = flow

        view = svc.get_evolution_audit(days=7)
        assert view["decision_stats"]["committed"] == 1
        assert view["approval_stats"]["total"] == 1

    def test_auto_rollback_gateway(self, tmp_path):
        """service 层 auto_rollback 懒加载单例 + run_auto_rollback 网关"""
        from agent.skills_mgmt import SkillsMgmtService

        svc = SkillsMgmtService(store_path=str(tmp_path / "skills.json"))
        assert svc.auto_rollback is not None  # 懒加载可构造（默认 restorer 就绪）
        # 无基线时安全返回（不抛异常）
        out = svc.run_auto_rollback(
            "ghost-skill", "1.0.0", metrics={"success_rate": 0.1})
        assert out["triggered"] is False
        assert "无上一 committed" in out["reason"]
