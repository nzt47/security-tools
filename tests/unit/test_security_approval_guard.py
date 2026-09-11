"""TASK-S4-01 审批守卫与审批域接线单测

验收对应：
- auto(skill) / sub_agent 调 approve/reject → **拒绝 + 审计 + 告警**（越权三件套）；
- human 审批记录 `actor=登录会话`，且同时落 **S2-02 链式审计** 与 **S2-03 approval 事件**；
- **勿双写**：越权只产生一条 `policy.denied` 链记录（S2-03「一动作一记录」不变量）；
- 身份 + PII 叶子字段入链（裁定 A3/B），**原始 IP 不落盘**；
- `stage.promote` 属 human 专属；治理可回溯字段（undo_hint/补偿动作）可查；
- 向后兼容：不传 `actor_ctx` 的既有调用点行为不变。
"""

from __future__ import annotations

import json
import os

import pytest

from agent.observability import events as ev
from agent.security import alerts as alerts_mod
from agent.security import approval_guard as G
from agent.security import governance_bridge as GB
from agent.security.actor_matrix import (
    ACTOR_AUTO,
    ACTOR_HUMAN,
    ACTOR_SUB_AGENT,
    OP_APPROVE,
    OP_EXECUTE_CAPABILITY,
    OP_FORCE_STAGE,
    OP_VIEW_PANEL,
    PermissionRule,
)
from agent.skills_mgmt.approval import (
    ApprovalPermissionError,
    ApprovalFlow,
)


def _ctx(actor: str, actor_type: str = "", *, ip: str = "", scope: str = "",
         caps=()) -> G.ActorContext:
    return G.ActorContext(actor=actor, actor_type=actor_type,
                          identity_source="token_map", scope=scope,
                          actor_ip=ip, authorized_capabilities=frozenset(caps))


def _chain_actions(chain) -> list:
    return [e.action for e in chain.entries()]


def _chain_payloads(chain, action: str) -> list:
    return [e.payload for e in chain.entries() if e.action == action]


def _flat(payload) -> dict:
    """审计载荷拍平（顶层 + payload 嵌套层）"""
    out = {}
    if isinstance(payload, dict):
        out.update({k: v for k, v in payload.items() if not isinstance(v, dict)})
        nested = payload.get("payload")
        if isinstance(nested, dict):
            out.update(nested)
    return out


# ════════════════════════════════════════════════════════════════
#  操作映射与风险解析
# ════════════════════════════════════════════════════════════════


class TestOperationMapping:
    @pytest.mark.parametrize("action,expected", [
        ("approve", OP_APPROVE),
        ("approved", OP_APPROVE),
        ("merge", OP_APPROVE),
        ("mark_manual_executed", OP_APPROVE),
        ("reject", "approval.deny"),
        ("rejected", "approval.deny"),
    ])
    def test_transition_actions_map_regardless_of_object(self, action, expected):
        from agent.security.actor_matrix import normalize_operation
        assert G.operation_for("skill", action) == normalize_operation(expected)
        # 换 object_type 不能绕过审批行
        assert G.operation_for("whatever", action) == normalize_operation(expected)

    def test_submit_action_refines_by_object_type(self):
        assert G.operation_for("stage.promote", "promote") == OP_FORCE_STAGE
        assert G.operation_for("policy", "submit") == "governance.modify_policy"
        assert G.operation_for("skill", "params_submit") == "approval.submit"

    def test_extension_mapping_registered(self):
        G.register_object_operation("widget.v2", OP_FORCE_STAGE)
        assert G.operation_for("widget.v2", "submit") == OP_FORCE_STAGE
        G.reset_object_operations()
        assert G.operation_for("widget.v2", "submit") == "approval.submit"

    def test_prefix_match_for_namespaced_object_types(self):
        """`capability.foo` 一类带命名空间的对象类型按前缀落到对应治理行"""
        assert G.operation_for("capability.read_file", "submit") == OP_EXECUTE_CAPABILITY
        assert G.operation_for("stage.promote", "submit") == OP_FORCE_STAGE


class TestRiskResolution:
    def test_explicit_risk_wins(self):
        assert G.resolve_risk("skill", "s", {"risk_level": "high"},
                              explicit="destructive") == "destructive"

    def test_payload_leaf_risk(self):
        assert G.resolve_risk("skill", "s", {"risk_level": "destructive"}) == "destructive"
        assert G.resolve_risk("skill", "s", {"risk": "High"}) == "high"

    def test_unknown_risk_is_not_destructive(self):
        assert G.resolve_risk("skill", "s", {}) == ""

    def test_injected_resolver_is_used(self):
        previous = G.set_risk_resolver(lambda ot, oi, pl: "destructive")
        try:
            assert G.resolve_risk("skill", "s", {}) == "destructive"
        finally:
            G.set_risk_resolver(previous)

    def test_resolver_failure_does_not_block(self):
        def boom(ot, oi, pl):
            raise RuntimeError("boom")

        previous = G.set_risk_resolver(boom)
        try:
            assert G.resolve_risk("skill", "s", {"risk": "high"}) == "high"
        finally:
            G.set_risk_resolver(previous)


# ════════════════════════════════════════════════════════════════
#  越权三件套：拒绝 + 审计 + 告警
# ════════════════════════════════════════════════════════════════


class TestViolationTriplet:
    def test_deny_writes_single_policy_denied_audit_record(
            self, s401_audit_chain, s401_events_dir):
        """**勿双写**：越权在链上只产生一条 `policy.denied`"""
        decision = G.authorize(operation=OP_APPROVE, actor_ctx=_ctx("auto:x", ACTOR_AUTO),
                              object_type="stage.promote", object_id="cap-1")
        assert decision.allowed is False
        actions = _chain_actions(s401_audit_chain)
        assert actions.count("policy.denied") == 1
        assert "approval.approved" not in actions
        # 事件侧同样只有一条
        envelopes = ev.iter_events(directory=str(s401_events_dir),
                                   types=[ev.EV_POLICY_DENIED])
        assert len(envelopes) == 1
        assert envelopes[0].actor == ACTOR_AUTO

    def test_deny_triggers_alert_with_structured_marker(self, caplog):
        with caplog.at_level("ERROR", logger="agent.security.alerts"):
            G.authorize(operation=OP_APPROVE, actor_ctx=_ctx("auto:x", ACTOR_AUTO))
        assert alerts_mod.ALERT_MARKER in caplog.text
        stats = alerts_mod.get_denial_stats()
        assert stats["total"] == 1
        assert stats["counts"]["actor_type:auto"] == 1

    def test_repeated_violations_each_leave_one_audit_record(
            self, s401_audit_chain, s401_events_dir):
        """同一执行体重复越权 ⇒ **每次**都留一条链上记录（不得被幂等吞掉）

        回归来源：首版实现的事件幂等键不含尝试序号，同操作/同对象的第 2 次越权
        在事件侧被判为重放，链上只剩第一条 —— 会让「同一来源多次越权」分析失真。
        """
        for _ in range(3):
            G.authorize(operation=OP_APPROVE,
                        actor_ctx=_ctx("auto:x", ACTOR_AUTO, ip="10.0.0.7"))
        assert _chain_actions(s401_audit_chain).count("policy.denied") == 3
        envelopes = ev.iter_events(directory=str(s401_events_dir),
                                   types=[ev.EV_POLICY_DENIED])
        assert len(envelopes) == 3
        assert len({e.payload["attempt_seq"] for e in envelopes}) == 3

    def test_denial_key_resists_cross_process_collision(self, s401_audit_chain,
                                                        s401_events_dir):
        """重启后 `seq` 从 1 重新开始，但幂等键含**进程命名空间** ⇒ 不撞键

        回归来源：真实 `data/events/` 上实测 —— 上一进程的
        `policy.denied:1:...` 会让新进程的第一条越权被判为重放而丢弃，审计静默缺一条。
        """
        key = alerts_mod.denial_event_key(1, "approval.approve", "", "", "")
        assert str(os.getpid()) in key
        assert key != "policy.denied:1:approval.approve:::"
        # 同进程同序号 ⇒ 稳定（可去重）；同进程不同序号 ⇒ 不同
        assert key == alerts_mod.denial_event_key(1, "approval.approve", "", "", "")
        assert key != alerts_mod.denial_event_key(2, "approval.approve", "", "", "")

    def test_alert_carries_pii_masked_not_raw(self, s401_ip_key):
        events = []
        alerts_mod.set_alert_sink(events.append)
        try:
            G.authorize(operation=OP_APPROVE,
                        actor_ctx=_ctx("auto:x", ACTOR_AUTO, ip="10.0.0.7"))
        finally:
            alerts_mod.set_alert_sink(None)
        assert len(events) == 1
        event = events[0]
        assert event["actor_ip_masked"] == "10.0.xxx.xxx"
        assert event["actor_ip_hash"]
        assert "10.0.0.7" not in json.dumps(event, ensure_ascii=False)

    def test_same_source_escalates_after_threshold(self, monkeypatch, s401_ip_key):
        monkeypatch.setenv("CP_SECURITY_ALERT_THRESHOLD", "2")
        monkeypatch.setenv("CP_SECURITY_ALERT_WINDOW_SECONDS", "600")
        events = []
        alerts_mod.set_alert_sink(events.append)
        try:
            for _ in range(2):
                G.authorize(operation=OP_APPROVE,
                            actor_ctx=_ctx("auto:x", ACTOR_AUTO, ip="10.0.0.7"))
        finally:
            alerts_mod.set_alert_sink(None)
        assert events[0]["escalated"] is False
        assert events[1]["escalated"] is True
        assert events[1]["level"] == alerts_mod.LEVEL_CRITICAL
        assert events[0]["source_key"] == events[1]["source_key"]

    def test_threshold_zero_disables_escalation(self, monkeypatch, s401_ip_key):
        monkeypatch.setenv("CP_SECURITY_ALERT_THRESHOLD", "0")
        events = []
        alerts_mod.set_alert_sink(events.append)
        try:
            for _ in range(5):
                G.authorize(operation=OP_APPROVE,
                            actor_ctx=_ctx("auto:x", ACTOR_AUTO, ip="10.0.0.7"))
        finally:
            alerts_mod.set_alert_sink(None)
        assert all(e["escalated"] is False for e in events)

    def test_sink_failure_does_not_block(self):
        def boom(event):
            raise RuntimeError("sink down")

        alerts_mod.set_alert_sink(boom)
        try:
            decision = G.authorize(operation=OP_APPROVE,
                                   actor_ctx=_ctx("auto:x", ACTOR_AUTO))
        finally:
            alerts_mod.set_alert_sink(None)
        assert decision.allowed is False

    def test_alerts_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("CP_SECURITY_ALERTS_ENABLED", "0")
        assert alerts_mod.alerts_enabled() is False
        decision = G.authorize(operation=OP_APPROVE,
                               actor_ctx=_ctx("auto:x", ACTOR_AUTO))
        assert decision.allowed is False
        assert alerts_mod.get_denial_stats()["total"] == 1

    @pytest.mark.parametrize("actor_type", [ACTOR_AUTO, ACTOR_SUB_AGENT])
    def test_non_human_view_panel_denied_and_audited(self, actor_type,
                                                     s401_audit_chain):
        decision = G.authorize(operation=OP_VIEW_PANEL,
                               actor_ctx=_ctx("x", actor_type))
        assert decision.allowed is False
        assert _chain_actions(s401_audit_chain).count("policy.denied") == 1


# ════════════════════════════════════════════════════════════════
#  ApprovalFlow 接线：越权被拒
# ════════════════════════════════════════════════════════════════


class TestApprovalFlowEnforcement:
    def test_auto_approve_rejected_with_audit_and_alert(self, s401_flow,
                                                       s401_audit_chain,
                                                       s401_events_dir):
        record = s401_flow.submit("stage.promote", "cap-1", action="promote",
                                  actor="owner")
        with pytest.raises(ApprovalPermissionError) as exc:
            s401_flow.approve(record.record_id, actor="auto:evolver",
                              actor_ctx=_ctx("auto:evolver", ACTOR_AUTO))
        assert exc.value.decision is not None
        assert exc.value.decision.denied_by_matrix is True
        actions = _chain_actions(s401_audit_chain)
        assert actions.count("policy.denied") == 1
        # 状态未变更：仍 pending（拒绝不产生状态迁移）
        assert s401_flow.get(record.record_id).state == "pending_review"
        assert alerts_mod.get_denial_stats()["total"] == 1

    @pytest.mark.parametrize("action,actor_type", [
        ("approve", ACTOR_AUTO), ("approve", ACTOR_SUB_AGENT),
        ("reject", ACTOR_AUTO), ("reject", ACTOR_SUB_AGENT),
    ])
    def test_all_non_human_approval_paths_rejected(self, s401_flow, action,
                                                   actor_type):
        record = s401_flow.submit("skill", "s-1", action="params_submit")
        with pytest.raises(ApprovalPermissionError):
            if action == "approve":
                s401_flow.approve(record.record_id, actor="x",
                                  actor_ctx=_ctx("x", actor_type))
            else:
                s401_flow.reject(record.record_id, actor="x", reason="r",
                                 actor_ctx=_ctx("x", actor_type))

    def test_sub_agent_merge_and_archive_rejected(self, s401_flow):
        record = s401_flow.submit("skill", "s-2", action="params_submit")
        s401_flow.approve(record.record_id, actor="owner")
        with pytest.raises(ApprovalPermissionError):
            s401_flow.merge(record.record_id, actor="x",
                            actor_ctx=_ctx("x", ACTOR_SUB_AGENT))
        with pytest.raises(ApprovalPermissionError):
            s401_flow.mark_manual_executed(record.record_id, actor="x",
                                           actor_ctx=_ctx("x", ACTOR_SUB_AGENT))

    def test_auto_can_submit_skill_proposal(self, s401_flow):
        """§7.0 外扩展：auto 提交提案放行（"自动只产出建议"）"""
        record = s401_flow.submit("skill", "s-3", action="params_submit",
                                  actor="auto:evolver",
                                  actor_ctx=_ctx("auto:evolver", ACTOR_AUTO))
        assert record.state == "pending_review"
        assert record.actor_type == ACTOR_AUTO

    def test_auto_cannot_submit_stage_promote(self, s401_flow):
        with pytest.raises(ApprovalPermissionError):
            s401_flow.submit("stage.promote", "cap-9", action="promote",
                             actor="auto:evolver",
                             actor_ctx=_ctx("auto:evolver", ACTOR_AUTO))

    def test_sub_agent_cannot_submit_at_all(self, s401_flow):
        with pytest.raises(ApprovalPermissionError):
            s401_flow.submit("skill", "s-4", action="params_submit",
                             actor="sub_agent:1",
                             actor_ctx=_ctx("sub_agent:1", ACTOR_SUB_AGENT))

    def test_destructive_requires_second_factor(self, s401_flow):
        record = s401_flow.submit(
            "skill", "s-5", action="params_submit",
            payload={"risk_level": "destructive"})
        with pytest.raises(ApprovalPermissionError) as exc:
            s401_flow.approve(record.record_id, actor="owner")
        assert exc.value.decision.requires_second_factor is True
        ok = s401_flow.approve(record.record_id, actor="owner",
                               second_factor_ok=True)
        assert ok.state == "approved"

    def test_legacy_caller_without_actor_ctx_still_works(self, s401_flow):
        """向后兼容：既有调用点（不传 actor_ctx）行为不变"""
        applied = []
        record = s401_flow.submit("skill", "s-6", action="params_submit",
                                  actor="evolver", applier=lambda: applied.append(1))
        approved = s401_flow.approve(record.record_id, actor="reviewer",
                                     note="人工核对")
        assert approved.state == "approved"
        merged = s401_flow.merge(record.record_id, actor="reviewer")
        assert merged.state == "merged"
        assert applied == [1]

    def test_governance_matrix_row_extensible_via_register(self, s401_flow):
        from agent.security.actor_matrix import SCOPE_ALL, register_rule
        register_rule(OP_APPROVE, ACTOR_AUTO, PermissionRule(
            allowed=True, scope=SCOPE_ALL, desc="假想放行（扩展行验证）"),
            override=True)
        record = s401_flow.submit("skill", "s-7", action="params_submit")
        # 表加了行，判定函数零改动即生效
        assert s401_flow.approve(record.record_id, actor="auto:x",
                                 actor_ctx=_ctx("auto:x", ACTOR_AUTO)).state == "approved"


# ════════════════════════════════════════════════════════════════
#  human 审批的身份 / PII / 治理字段入链
# ════════════════════════════════════════════════════════════════


class TestHumanApprovalAudit:
    def test_identity_and_pii_fields_recorded(self, s401_flow, s401_audit_chain,
                                              s401_ip_key):
        ctx = _ctx("alice", ACTOR_HUMAN, ip="10.0.0.7", scope="team-1")
        record = s401_flow.submit("skill", "s-8", action="params_submit",
                                  actor="alice", actor_ctx=ctx)
        s401_flow.approve(record.record_id, actor="alice", actor_ctx=ctx,
                          note="核对通过")

        stored = s401_flow.get(record.record_id)
        assert stored.actor == "alice"
        assert stored.actor_type == ACTOR_HUMAN
        assert stored.identity_source == "token_map"
        assert stored.actor_ip_masked == "10.0.xxx.xxx"
        assert stored.actor_ip_hash
        assert stored.actor_scope == "team-1"

        payloads = _chain_payloads(s401_audit_chain, "approval.approved")
        assert payloads, "链上应有 approval.approved"
        flat = _flat(payloads[0])
        assert flat["identity_source"] == "token_map"
        assert flat["actor_type"] == ACTOR_HUMAN
        assert flat["actor_ip_masked"] == "10.0.xxx.xxx"
        assert flat["actor_ip_hash"]

    def test_raw_ip_never_persisted_in_jsonl(self, s401_flow, s401_ip_key):
        """**裁定 B 硬约束**：原始 IP 不落盘（审批记录 JSONL 亦不例外）"""
        ctx = _ctx("alice", ACTOR_HUMAN, ip="10.0.0.7")
        s401_flow.submit("skill", "s-9", action="params_submit",
                         actor="alice", actor_ctx=ctx)
        raw = s401_flow._records_path.read_text(encoding="utf-8")
        assert "10.0.0.7" not in raw
        assert "10.0.xxx.xxx" in raw

    def test_raw_ip_never_persisted_in_audit_chain(self, s401_flow,
                                                   s401_audit_chain, s401_ip_key):
        ctx = _ctx("alice", ACTOR_HUMAN, ip="10.0.0.7")
        record = s401_flow.submit("skill", "s-10", action="params_submit",
                                  actor="alice", actor_ctx=ctx)
        s401_flow.approve(record.record_id, actor="alice", actor_ctx=ctx)
        dumped = json.dumps([e.payload for e in s401_audit_chain.entries()],
                            ensure_ascii=False)
        assert "10.0.0.7" not in dumped

    def test_approval_emits_s2_03_events(self, s401_flow, s401_events_dir):
        """S2-03 口径：审批同时落 approval.required / approval / intervention"""
        ctx = _ctx("alice", ACTOR_HUMAN)
        record = s401_flow.submit("skill", "s-11", action="params_submit",
                                  actor="alice", actor_ctx=ctx)
        s401_flow.approve(record.record_id, actor="alice", actor_ctx=ctx,
                          note="条件：需补测试")
        envelopes = ev.iter_events(directory=str(s401_events_dir))
        types = [e.type for e in envelopes]
        assert ev.EV_APPROVAL_REQUIRED in types
        assert ev.EV_APPROVAL in types
        assert ev.EV_INTERVENTION in types
        approval_env = [e for e in envelopes if e.type == ev.EV_APPROVAL][0]
        assert approval_env.payload["kind"] == "conditional"   # note 非空 ⇒ 条件批准

    def test_governance_trace_fields_in_payload(self, s401_flow, s401_audit_chain):
        record = s401_flow.submit(
            "stage.promote", "cap-2", action="promote", actor="owner",
            payload={"undo_hint": "回滚 stage 至 shadow",
                     "compensating_action": "执行 stage_migrate(shadow)"})
        s401_flow.approve(record.record_id, actor="owner", note="人工裁定")
        flat = _flat(_chain_payloads(s401_audit_chain, "approval.approved")[0])
        assert flat["undo_hint"] == "回滚 stage 至 shadow"
        assert flat["compensating_action"] == "执行 stage_migrate(shadow)"
        assert flat["undo_hint_status"] == GB.STATUS_RESOLVED

    def test_governance_trace_unresolved_is_marked(self, s401_flow,
                                                   s401_audit_chain):
        record = s401_flow.submit("stage.promote", "cap-unknown-xyz",
                                  action="promote", actor="owner")
        s401_flow.approve(record.record_id, actor="owner")
        flat = _flat(_chain_payloads(s401_audit_chain, "approval.approved")[0])
        assert flat["undo_hint_status"] == GB.STATUS_UNRESOLVED

    def test_record_id_queryable_on_chain(self, s401_flow, s401_audit_chain):
        """`record_id` 必须**逐字可查**（关联键走 `technical` 通道）

        回归来源：经普通 payload 会被统一脱敏器的启发式掩码成
        `appr-202609********374219-...`，令「按 record_id 查链」失效。
        """
        record = s401_flow.submit("skill", "s-13", action="params_submit",
                                  actor="owner")
        s401_flow.approve(record.record_id, actor="owner")
        found = [e for e in s401_audit_chain.entries()
                 if (e.payload or {}).get("record_id") == record.record_id]
        assert len(found) == 2      # submit + approved，两条都能按 record_id 查到
        assert {e.action for e in found} == {"approval.submit", "approval.approved"}


# ════════════════════════════════════════════════════════════════
#  治理桥接：风险 / 可回溯 / 污染标记（注入 registry 桩，不依赖真实资产）
# ════════════════════════════════════════════════════════════════


class _StubDescriptor:
    def __init__(self, risk="high", undo="", comp="", external=False,
                 provenance="borrowed"):
        self.trust = type("T", (), {"risk_level": risk})()
        self.governance = type("G", (), {"undo_hint": undo,
                                        "compensating_action": comp})()
        self.origin = type("O", (), {"external_endpoint": external})()
        self.provenance = type("P", (), {"level": provenance})()


class _StubRegistry:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, capability_id):
        return self._mapping.get(capability_id)


class TestGovernanceBridge:
    def test_descriptor_risk_used(self):
        GB.set_registry(_StubRegistry({"cap-a": _StubDescriptor(risk="destructive")}))
        assert GB.descriptor_risk("capability", "cap-a") == "destructive"
        assert GB.descriptor_risk("capability", "cap-b") == ""

    def test_descriptor_risk_not_applicable_for_other_types(self):
        GB.set_registry(_StubRegistry({"cap-a": _StubDescriptor(risk="destructive")}))
        assert GB.descriptor_risk("memory", "cap-a") == ""

    def test_governance_fields_from_descriptor(self):
        GB.set_registry(_StubRegistry({"cap-a": _StubDescriptor(
            undo="撤销：删除生成文件", comp="恢复备份")}))
        fields = GB.governance_trace_fields("capability", "cap-a")
        assert fields["undo_hint"] == "撤销：删除生成文件"
        assert fields["undo_hint_status"] == GB.STATUS_RESOLVED

    def test_governance_fields_missing_marked(self):
        GB.set_registry(_StubRegistry({"cap-a": _StubDescriptor()}))
        fields = GB.governance_trace_fields("capability", "cap-a")
        assert fields["undo_hint_status"] == GB.STATUS_MISSING

    def test_registry_failure_degrades(self):
        class Boom:
            def get(self, capability_id):
                raise RuntimeError("registry down")

        GB.set_registry(Boom())
        assert GB.descriptor_risk("capability", "cap-a") == ""
        assert GB.governance_trace_fields(
            "capability", "cap-a")["undo_hint_status"] == GB.STATUS_UNRESOLVED

    def test_taint_from_external_endpoint(self):
        GB.set_registry(_StubRegistry({"cap-a": _StubDescriptor(external=True)}))
        assert GB.taint_flags("capability", "cap-a")["taint"] is True

    def test_taint_from_payload_marker(self):
        assert GB.taint_flags("skill", "s", {"taint": True})["taint"] is True
        assert GB.taint_flags("skill", "s", {"provenance_level": "borrowed"})["taint"] is True

    def test_no_taint_for_clean_payload(self):
        GB.set_registry(_StubRegistry({"cap-a": _StubDescriptor(
            external=False, provenance="native")}))
        assert GB.taint_flags("capability", "cap-a", {})["taint"] is False


# ════════════════════════════════════════════════════════════════
#  能力执行/记忆写入的判定经 guard 入口（sub_agent 授权子集）
# ════════════════════════════════════════════════════════════════


class TestGuardedOperations:
    def test_sub_agent_execute_uses_authorized_subset(self):
        ctx = _ctx("sub_agent:7", ACTOR_SUB_AGENT, caps=["cap-allowed"])
        ok = G.authorize(operation=OP_EXECUTE_CAPABILITY, actor_ctx=ctx,
                         object_id="cap-allowed")
        denied = G.authorize(operation=OP_EXECUTE_CAPABILITY, actor_ctx=ctx,
                             object_id="cap-forbidden")
        assert ok.allowed is True
        assert denied.allowed is False
        assert "授权子集" in denied.reason

    def test_report_false_skips_alert(self):
        decision = G.authorize(operation=OP_APPROVE,
                               actor_ctx=_ctx("auto:x", ACTOR_AUTO), report=False)
        assert decision.allowed is False
        assert alerts_mod.get_denial_stats()["total"] == 0


# ════════════════════════════════════════════════════════════════
#  ApprovalFlow 与 actor_ctx 的默认身份来源（诚实标注）
# ════════════════════════════════════════════════════════════════


class TestActorContextDefaults:
    def test_context_from_request_marks_degraded(self):
        ctx = G.context_from_request(remote_addr="10.0.0.9")
        assert ctx.actor == "ui:10.0.0.9"
        assert ctx.identity_source == "remote_addr"
        assert ctx.degraded is True
        assert ctx.resolved_type() == ACTOR_HUMAN

    def test_context_from_request_uses_token_map(self, monkeypatch):
        from agent.security.identity import TokenMap, set_token_map, reset_identity
        reset_identity()
        set_token_map(TokenMap("tokA:alice:team-1"))
        ctx = G.context_from_request(headers={"Authorization": "Bearer tokA"})
        assert ctx.actor == "alice"
        assert ctx.identity_source == "token_map"
        assert ctx.scope == "team-1"
        assert ctx.degraded is False

    def test_audit_fields_include_pii_only_when_ip_present(self, s401_ip_key):
        with_ip = _ctx("alice", ACTOR_HUMAN, ip="10.0.0.7").audit_fields()
        assert with_ip["actor_ip_masked"] == "10.0.xxx.xxx"
        without_ip = _ctx("alice", ACTOR_HUMAN).audit_fields()
        assert "actor_ip_masked" not in without_ip

    def test_legacy_context_source_is_marked(self, s401_flow):
        """不传 actor_ctx 时来源如实标注为 legacy_actor_param（不冒充 token_map）"""
        record = s401_flow.submit("skill", "s-12", action="params_submit")
        decision = G.authorize_approval_action(
            action="approve", object_type="skill", object_id="s-12",
            actor="reviewer", report=False)
        assert decision.allowed is True
        assert record.identity_source == ""      # 未提供上下文 ⇒ 不改写记录
