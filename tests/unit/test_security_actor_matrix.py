"""TASK-S4-01 Actor 权限矩阵单测（`agent/security/actor_matrix.py`）

验收对应：
- §7.0 矩阵**核心行全部可判定**（后端表驱动，前端不拥有额外权限）；
- 加行不改逻辑（`register_rule` 即扩展生效，判定函数零改动）；
- 越权路径（auto/sub_agent 审批、sub_agent 读审批、非 human 强制推进 stage）一律拒绝；
- **fail-closed**：未知操作 / 未知执行体类型 / 缺行 → 拒绝而非放行；
- 与 `agent.observability.events.ACTOR_*`、`agent.descriptors.models.RiskLevel` 口径一致。
"""

from __future__ import annotations

import pytest

from agent.security import actor_matrix as M


# ════════════════════════════════════════════════════════════
#  口径一致性（防漂移）
# ════════════════════════════════════════════════════════════


class TestVersionAlignment:
    """与既有口径逐值对账（不靠 import 维系，靠断言守护）"""

    def test_actor_types_match_events_constants(self):
        from agent.observability import events as ev
        assert M.ACTOR_HUMAN == ev.ACTOR_HUMAN
        assert M.ACTOR_AUTO == ev.ACTOR_AUTO
        assert M.ACTOR_SUB_AGENT == ev.ACTOR_SUB_AGENT

    def test_risk_order_matches_descriptor_risk_level(self):
        from agent.descriptors.models import RiskLevel
        expected = [m.value for m in RiskLevel]
        assert list(M.RISK_ORDER) == expected

    def test_matrix_doc_and_rows_cover_same_groups(self):
        assert set(M.MATRIX_DOC) == set(M.MATRIX_DOC_ROWS)

    def test_every_matrix_cell_registered(self):
        """§7.0 文档矩阵的每一格都必须在内置表中可查（核心行全覆盖）"""
        for group, cells in M.MATRIX_DOC.items():
            for operation in M.MATRIX_DOC_ROWS[group]:
                for actor_type in M.ACTOR_TYPES:
                    rule = M.rule_for(operation, actor_type)
                    assert rule is not None, f"缺行: {operation}/{actor_type}"
                    assert actor_type in cells


# ════════════════════════════════════════════════════════════
#  §7.0 核心行逐行判定
# ════════════════════════════════════════════════════════════


def _ctx(actor_type: str, **kwargs) -> M.PermissionContext:
    base = {"actor": f"{actor_type}-actor", "actor_type": actor_type}
    base.update(kwargs)
    return M.PermissionContext(**base)


class TestMatrixCoreRows:
    """§7.0 六组核心行"""

    # ── 查看轨迹/记忆/面板：human ✅ / auto 仅自身 scope / sub_agent ❌ ──

    @pytest.mark.parametrize("op", [M.OP_VIEW_TRACE, M.OP_VIEW_MEMORY, M.OP_VIEW_PANEL])
    def test_view_human_allowed(self, op):
        assert M.decide(op, _ctx(M.ACTOR_HUMAN)).allowed is True

    @pytest.mark.parametrize("op", [M.OP_VIEW_TRACE, M.OP_VIEW_MEMORY, M.OP_VIEW_PANEL])
    def test_view_auto_only_own_scope(self, op):
        ctx = _ctx(M.ACTOR_AUTO, scope="task-1")
        assert M.decide(op, ctx, target_scope="task-1").allowed is True
        assert M.decide(op, ctx, target_scope="task-2").allowed is False
        # 未声明目标 scope ⇒ 无法证明「自身 scope」⇒ 拒绝
        assert M.decide(op, ctx).allowed is False

    @pytest.mark.parametrize("op", [M.OP_VIEW_TRACE, M.OP_VIEW_MEMORY, M.OP_VIEW_PANEL])
    def test_view_sub_agent_denied(self, op):
        decision = M.decide(op, _ctx(M.ACTOR_SUB_AGENT))
        assert decision.allowed is False
        assert decision.denied_by_matrix is True

    # ── 审批 Approve/Deny：仅 human ──

    @pytest.mark.parametrize("op", [M.OP_APPROVE, M.OP_DENY])
    def test_approval_human_only(self, op):
        assert M.decide(op, _ctx(M.ACTOR_HUMAN)).allowed is True
        for actor_type in (M.ACTOR_AUTO, M.ACTOR_SUB_AGENT):
            decision = M.decide(op, _ctx(actor_type))
            assert decision.allowed is False
            assert decision.denied_by_matrix is True
            assert decision.alert_on_deny is True

    def test_approve_alias_and_reject_alias(self):
        assert M.normalize_operation("approve") == M.OP_APPROVE
        assert M.normalize_operation("reject") == M.OP_DENY
        assert M.decide("reject", _ctx(M.ACTOR_AUTO)).allowed is False

    # ── 切换熔炉/修改策略：human ✅（二次认证） ──

    @pytest.mark.parametrize("op", [M.OP_SWITCH_FORGE, M.OP_MODIFY_POLICY])
    def test_policy_change_requires_second_factor(self, op):
        ctx = _ctx(M.ACTOR_HUMAN)
        blocked = M.decide(op, ctx)
        assert blocked.allowed is False
        assert blocked.requires_second_factor is True
        assert M.decide(op, ctx, second_factor_ok=True).allowed is True
        for actor_type in (M.ACTOR_AUTO, M.ACTOR_SUB_AGENT):
            assert M.decide(op, _ctx(actor_type), second_factor_ok=True).allowed is False

    # ── 强制推进 stage / 摘除来源：human ✅（reason 必填） ──

    @pytest.mark.parametrize("op", [M.OP_FORCE_STAGE, M.OP_REMOVE_SOURCE])
    def test_force_stage_requires_reason(self, op):
        ctx = _ctx(M.ACTOR_HUMAN)
        no_reason = M.decide(op, ctx)
        assert no_reason.allowed is False
        assert no_reason.requires_reason is True
        assert M.decide(op, ctx, reason="人工裁定：证据齐备").allowed is True
        for actor_type in (M.ACTOR_AUTO, M.ACTOR_SUB_AGENT):
            assert M.decide(op, _ctx(actor_type), reason="x").allowed is False

    def test_stage_promote_object_alias_maps_to_force_stage(self):
        """S3-03 的 `stage.promote` 对象经别名落到「强制推进」行"""
        assert M.normalize_operation("stage.promote") == M.OP_FORCE_STAGE

    # ── 执行 capability：human ✅ / auto scope 内 / sub_agent 授权子集 ──

    def test_execute_capability_human(self):
        assert M.decide(M.OP_EXECUTE_CAPABILITY, _ctx(M.ACTOR_HUMAN)).allowed is True

    def test_execute_capability_auto_in_scope(self):
        ctx = _ctx(M.ACTOR_AUTO, scope="task-1")
        assert M.decide(M.OP_EXECUTE_CAPABILITY, ctx, target_scope="task-1").allowed is True
        assert M.decide(M.OP_EXECUTE_CAPABILITY, ctx, target_scope="task-9").allowed is False

    def test_execute_capability_sub_agent_authorized_subset(self):
        ctx = _ctx(M.ACTOR_SUB_AGENT, authorized_capabilities=frozenset({"cap.a"}))
        assert M.decide(M.OP_EXECUTE_CAPABILITY, ctx, object_id="cap.a").allowed is True
        outside = M.decide(M.OP_EXECUTE_CAPABILITY, ctx, object_id="cap.b")
        assert outside.allowed is False
        assert "授权子集" in outside.reason
        # 未声明 capability ⇒ 拒绝（不得默认放行）
        assert M.decide(M.OP_EXECUTE_CAPABILITY, ctx).allowed is False

    def test_execute_capability_sub_agent_empty_grant_denies(self):
        ctx = _ctx(M.ACTOR_SUB_AGENT)
        assert M.decide(M.OP_EXECUTE_CAPABILITY, ctx, object_id="cap.a").allowed is False

    # ── 写入记忆：human ✅ / auto 仅工作记忆 / sub_agent ❌ ──

    def test_write_memory_auto_working_only(self):
        ctx = _ctx(M.ACTOR_AUTO)
        assert M.decide(M.OP_WRITE_MEMORY, ctx, memory_layer="working").allowed is True
        for layer in ("long_term", "episodic", "semantic", ""):
            decision = M.decide(M.OP_WRITE_MEMORY, ctx, memory_layer=layer)
            assert decision.allowed is False, layer
        assert M.decide(M.OP_WRITE_MEMORY, _ctx(M.ACTOR_HUMAN)).allowed is True
        assert M.decide(M.OP_WRITE_MEMORY, _ctx(M.ACTOR_SUB_AGENT)).allowed is False

    # ── §7.0 外扩展行：提交审批提案 ──

    def test_submit_proposal_auto_allowed_sub_agent_denied(self):
        assert M.decide(M.OP_SUBMIT_APPROVAL, _ctx(M.ACTOR_HUMAN)).allowed is True
        assert M.decide(M.OP_SUBMIT_APPROVAL, _ctx(M.ACTOR_AUTO)).allowed is True
        assert M.decide(M.OP_SUBMIT_APPROVAL, _ctx(M.ACTOR_SUB_AGENT)).allowed is False


# ════════════════════════════════════════════════════════════
#  fail-closed 与前置条件
# ════════════════════════════════════════════════════════════


class TestFailClosed:
    def test_unknown_operation_denied(self):
        decision = M.decide("nope.not.registered", _ctx(M.ACTOR_HUMAN))
        assert decision.allowed is False
        assert decision.matrix_hit is False
        assert "fail-closed" in decision.reason

    def test_unknown_actor_type_denied(self):
        decision = M.decide(M.OP_APPROVE, _ctx("robot"))
        assert decision.allowed is False
        assert decision.matrix_hit is False

    def test_submit_mode_skips_preconditions_but_not_matrix(self):
        """提交阶段跳过 reason / 二次认证，但**不跳过**矩阵格判定"""
        ctx = _ctx(M.ACTOR_HUMAN)
        assert M.decide(M.OP_FORCE_STAGE, ctx, enforce_preconditions=False).allowed is True
        assert M.decide(M.OP_FORCE_STAGE, ctx).allowed is False
        assert M.decide(M.OP_FORCE_STAGE, _ctx(M.ACTOR_AUTO),
                        enforce_preconditions=False).allowed is False

    def test_destructive_risk_forces_second_factor(self):
        ctx = _ctx(M.ACTOR_HUMAN)
        assert M.decide(M.OP_APPROVE, ctx, risk="destructive").allowed is False
        assert M.decide(M.OP_APPROVE, ctx, risk="destructive",
                        second_factor_ok=True).allowed is True

    def test_non_destructive_risk_needs_no_second_factor(self):
        assert M.decide(M.OP_APPROVE, _ctx(M.ACTOR_HUMAN), risk="high").allowed is True

    def test_decision_is_json_serializable(self):
        import json
        payload = M.decide(M.OP_APPROVE, _ctx(M.ACTOR_AUTO)).to_dict()
        assert json.loads(json.dumps(payload))["allowed"] is False


# ════════════════════════════════════════════════════════════
#  表驱动扩展（加行不改逻辑）
# ════════════════════════════════════════════════════════════


class TestTableDrivenExtension:
    def test_register_rule_takes_effect_without_logic_change(self):
        rule = M.PermissionRule(allowed=True, scope=M.SCOPE_OWN, desc="扩展行")
        M.register_rule("view.custom_panel", M.ACTOR_AUTO, rule)
        ctx = _ctx(M.ACTOR_AUTO, scope="s1")
        assert M.decide("view.custom_panel", ctx, target_scope="s1").allowed is True
        assert M.decide("view.custom_panel", ctx, target_scope="s2").allowed is False

    def test_register_rule_rejects_duplicate_without_override(self):
        with pytest.raises(ValueError):
            M.register_rule(M.OP_APPROVE, M.ACTOR_HUMAN,
                            M.PermissionRule(allowed=False))

    def test_register_rule_override_restores_by_reset(self):
        M.register_rule(M.OP_APPROVE, M.ACTOR_HUMAN,
                        M.PermissionRule(allowed=False, desc="临时收紧"), override=True)
        assert M.decide(M.OP_APPROVE, _ctx(M.ACTOR_HUMAN)).allowed is False
        M.reset_rules()
        assert M.decide(M.OP_APPROVE, _ctx(M.ACTOR_HUMAN)).allowed is True

    def test_matrix_rows_export(self):
        rows = M.matrix_rows()
        assert len(rows) == len(M.OPERATIONS) * len(M.ACTOR_TYPES)
        assert all({"operation", "actor_type", "allowed"} <= set(r) for r in rows)


# ════════════════════════════════════════════════════════════
#  actor 类型推断（向后兼容既有调用方）
# ════════════════════════════════════════════════════════════


class TestActorTypeInference:
    @pytest.mark.parametrize("name,expected", [
        ("reviewer", M.ACTOR_HUMAN),
        ("owner", M.ACTOR_HUMAN),
        ("alice", M.ACTOR_HUMAN),
        ("system", M.ACTOR_HUMAN),
        ("auto:evolver", M.ACTOR_AUTO),
        ("skill:curator", M.ACTOR_AUTO),
        ("sub_agent:42", M.ACTOR_SUB_AGENT),
        ("subagent:42", M.ACTOR_SUB_AGENT),
    ])
    def test_infer(self, name, expected):
        assert M.infer_actor_type(name) == expected

    def test_infer_empty_falls_back_to_default(self):
        assert M.infer_actor_type("") == M.ACTOR_HUMAN
        assert M.infer_actor_type("", default=M.ACTOR_SUB_AGENT) == M.ACTOR_SUB_AGENT

    @pytest.mark.parametrize("alias,expected", [
        ("auto(skill)", M.ACTOR_AUTO),
        ("skill", M.ACTOR_AUTO),
        ("subagent", M.ACTOR_SUB_AGENT),
        ("HUMAN", M.ACTOR_HUMAN),
    ])
    def test_normalize_alias(self, alias, expected):
        assert M.normalize_actor_type(alias) == expected

    def test_normalize_unknown_without_default_raises(self):
        with pytest.raises(ValueError):
            M.normalize_actor_type("robot")

    def test_normalize_unknown_with_default(self):
        assert M.normalize_actor_type("robot", default="weird") == "weird"

    def test_risk_helpers(self):
        assert M.risk_rank("destructive") == 3
        assert M.risk_rank("unknown") == -1
        assert M.is_destructive("DESTRUCTIVE") is True
        assert M.is_destructive("high") is False
