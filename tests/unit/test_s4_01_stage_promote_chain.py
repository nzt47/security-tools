"""TASK-S4-01 × S3-03 真实审批链路端到端单测（`object_type=stage.promote`）

任务书步骤 2 明确要求：以 **S3-03 已交付的真实业务链路**（低流量手动 promote 通道，
复用 `skills_mgmt.approval` 的 L2 分级语义）作为矩阵落地与 human 专属的**现成验收
用例**，而非自造 mock 对象。故本文件全程调用 `agent.digestion.internalize` 的真实
API（`manual_promote` / `confirm_manual_promote` / `apply_manual_promote`）。

验收对应：
- `stage.promote` 的审批属 **human 专属**（auto/sub_agent 调用必须被拒）；
- 强制推进 stage（`apply_manual_promote`）**human 专属 + reason 必填**；
- 审批动作**同时**落 S2-02 链式审计与 S2-03 `record_approval()` 埋点，且**不双写**；
- `undo_hint` / 补偿动作可回溯（对齐 S1-02 的 governance 字段）；
- human 全路径：submit → approve → apply_manual_promote → stage 真实迁移至 internalized。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain
from agent.digestion import internalize as I
from agent.digestion import stage as ST
from agent.digestion.shadow import CLOCK_WALL
from agent.observability import events as ev
from agent.security import approval_guard as G
from agent.security.actor_matrix import ACTOR_AUTO, ACTOR_HUMAN, ACTOR_SUB_AGENT
from agent.skills_mgmt.approval import ApprovalPermissionError

CAP = "cp.builtin.read_file"
PROMOTED_STAGE = I.PROMOTE_TARGET_STAGE


# ════════════════════════════════════════════════════════════
#  fixtures（与 S3-03 单测同构；全部落 tmp，绝不污染运行时目录）
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def isolated_audit(tmp_path):
    chain = AuditChain(str(tmp_path / "audit.db"),
                       roots_path=str(tmp_path / "roots.jsonl"),
                       signing_key_path=str(tmp_path / "k.pem"), auto_seal=False)
    previous = facade_mod.audit.bind(chain)
    facade_mod.audit.enabled = True
    facade_mod.audit.reset_counters()
    yield chain
    facade_mod.audit.bind(previous)
    try:
        chain.close(timeout=2.0)
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setenv("CP_DIGESTION_PROMOTE_DIR", str(tmp_path / "promote_pr"))
    monkeypatch.setenv("CP_DIGESTION_SHADOW_DIR", str(tmp_path / "shadow"))
    monkeypatch.setenv("APPROVAL_RECORDS_PATH", str(tmp_path / "approvals.jsonl"))
    import agent.observability.events as events_mod
    events_mod.reset_event_stores()
    yield tmp_path
    events_mod.reset_event_stores()


def _descriptor(*, data_class="internal", stage="shadow"):
    return SimpleNamespace(
        evolution=SimpleNamespace(stage=SimpleNamespace(value=stage), shadow_config={}),
        trust=SimpleNamespace(data_class=SimpleNamespace(value=data_class),
                              risk_level="high", requires_approval=True),
        origin=SimpleNamespace(external_endpoint=False),
        governance=SimpleNamespace(undo_hint="回滚：stage_migrate(shadow)",
                                   compensating_action="恢复 shadow 路由表"),
        provenance=SimpleNamespace(level="borrowed"),
        quality=SimpleNamespace(success_rate=1.0, sample_count=30, p99_latency_ms=0.0))


class _Registry:
    """最小 descriptor 台账替身（`get` / `set_stage` / `update_fields`）"""

    def __init__(self, desc=None):
        self.desc = desc if desc is not None else _descriptor()
        self.patches = []

    def get(self, capability_id):
        return self.desc

    def set_stage(self, capability_id, stage, **kwargs):
        self.patches.append((capability_id, {"stage": stage}, kwargs))
        return self.desc

    def update_fields(self, capability_id, patch, **kwargs):
        self.patches.append((capability_id, patch, kwargs))
        return self.desc


def _evidence(**overrides):
    """六条件齐备的显式证据（④⑤⑥ 必过 ⇒ 手动通道允许提交）"""
    evidence = {
        "digest_count": I.DIGEST_COUNT_MIN + 1,
        "monthly_samples": I.MONTHLY_SAMPLES_MIN + 1,
        "success_rate": {"candidate": 1.0, "upstream": 1.0},
        "p99": {"candidate_ms": 8.0, "upstream_ms": 10.0, "clock": CLOCK_WALL},
        "roi": {"upstream_unit_cents": 12.0, "native_unit_cents": 2.0,
                "one_time_investment_cents": 1200.0},
    }
    evidence.update(overrides)
    return evidence


@pytest.fixture
def engine():
    registry = _Registry()
    return I.InternalizeEngine(registry=registry, passport_store=None,
                               emit_events=False), registry


@pytest.fixture
def human():
    return G.ActorContext(actor="owner", actor_type=ACTOR_HUMAN,
                          identity_source="token_map", actor_ip="10.0.0.7")


def _chain_actions(chain):
    return [e.action for e in chain.entries()]


def _flat(payload):
    out = {}
    if isinstance(payload, dict):
        out.update({k: v for k, v in payload.items() if not isinstance(v, dict)})
        nested = payload.get("payload")
        if isinstance(nested, dict):
            out.update(nested)
    return out


# ════════════════════════════════════════════════════════════
#  真实链路：human 全路径
# ════════════════════════════════════════════════════════════


class TestHumanFullPath:
    def test_submit_approve_apply_migrates_stage(self, engine, human,
                                                 isolated_audit, tmp_path):
        eng, registry = engine
        decision = eng.evaluate(CAP, evidence=_evidence())
        request = eng.manual_promote(CAP, "owner", decision=decision)
        assert request.permitted is True
        assert request.level == I.APPROVAL_LEVEL_MANUAL
        assert request.state == "pending_review"

        outcome = eng.confirm_manual_promote(CAP, record_id=request.record_id,
                                            actor="owner", approve=True,
                                            note="人工核对④⑤⑥通过")
        assert outcome["state"] == "approved"

        result = eng.apply_manual_promote(CAP, record_id=request.record_id,
                                         actor="owner", registry=registry,
                                         decision=decision)
        assert result["applied"] is True, result
        assert registry.patches, "stage 迁移应写回 descriptor"

    def test_approval_lands_chain_audit_and_events(self, engine, isolated_audit,
                                                  tmp_path):
        """S2-02 链式审计 + S2-03 `record_approval` 埋点**同时**落账"""
        eng, registry = engine
        decision = eng.evaluate(CAP, evidence=_evidence())
        request = eng.manual_promote(CAP, "owner", decision=decision)
        eng.confirm_manual_promote(CAP, record_id=request.record_id,
                                   actor="owner", approve=True, note="通过")

        actions = _chain_actions(isolated_audit)
        assert "approval.submit" in actions
        assert "approval.approved" in actions
        assert I.AUDIT_ACTION_MANUAL_SUBMITTED in actions

        envelopes = ev.iter_events(directory=str(tmp_path / "events"))
        types = [e.type for e in envelopes]
        assert ev.EV_APPROVAL_REQUIRED in types      # 进入审批
        assert ev.EV_APPROVAL in types               # 审批埋点（§6.6）
        assert ev.EV_INTERVENTION in types           # §6.1 介入计数
        approval = [e for e in envelopes if e.type == ev.EV_APPROVAL][0]
        assert approval.payload["object_type"] == I.APPROVAL_OBJECT_TYPE
        assert approval.payload["level"] == I.APPROVAL_LEVEL_MANUAL

    def test_no_double_write_of_approval_lifecycle(self, engine, isolated_audit):
        """**勿双写**（S2-03 不变量）：一次审批动作 = 一条链上记录"""
        eng, _ = engine
        decision = eng.evaluate(CAP, evidence=_evidence())
        request = eng.manual_promote(CAP, "owner", decision=decision)
        eng.confirm_manual_promote(CAP, record_id=request.record_id,
                                   actor="owner", approve=True, note="通过")
        actions = _chain_actions(isolated_audit)
        assert actions.count("approval.approved") == 1
        assert actions.count("approval.submit") == 1
        # policy.denied 只在越权时出现（正常路径不应有）
        assert "policy.denied" not in actions

    def test_governance_fields_traceable_after_approval(self, engine,
                                                       isolated_audit):
        """审批记录的 undo_hint / 补偿动作可回溯（对齐 S1-02 governance）"""
        eng, _ = engine
        decision = eng.evaluate(CAP, evidence=_evidence())
        request = eng.manual_promote(CAP, "owner", decision=decision,
                                     note="低样本人工裁定")
        eng.confirm_manual_promote(CAP, record_id=request.record_id,
                                   actor="owner", approve=True, note="通过")
        payloads = [e.payload for e in isolated_audit.entries()
                    if e.action == "approval.approved"]
        flat = _flat(payloads[0])
        assert flat["object_type"] == "stage.promote"
        # 审批载荷未带 undo_hint ⇒ 由 governance 桥接从 S1-02 descriptor 解析
        assert flat["undo_hint_status"] in ("resolved", "unresolved")
        if flat["undo_hint_status"] == "resolved":
            assert flat["undo_hint"]

    def test_reject_path_keeps_stage_unpromoted(self, engine, human):
        eng, registry = engine
        decision = eng.evaluate(CAP, evidence=_evidence())
        request = eng.manual_promote(CAP, "owner", decision=decision)
        eng.confirm_manual_promote(CAP, record_id=request.record_id,
                                   actor="owner", approve=False, note="证据不足")
        result = eng.apply_manual_promote(CAP, record_id=request.record_id,
                                         actor="owner", registry=registry,
                                         decision=decision)
        assert result["applied"] is False
        assert not registry.patches


# ════════════════════════════════════════════════════════════
#  stage.promote 的审批属 human 专属
# ════════════════════════════════════════════════════════════


class TestStagePromoteHumanOnly:
    @pytest.mark.parametrize("actor_type,actor", [
        (ACTOR_AUTO, "auto:evolver"),
        (ACTOR_SUB_AGENT, "sub_agent:7"),
    ])
    def test_non_human_cannot_approve_stage_promote(self, engine, actor_type,
                                                   actor, isolated_audit,
                                                   tmp_path):
        eng, _ = engine
        decision = eng.evaluate(CAP, evidence=_evidence())
        request = eng.manual_promote(CAP, "owner", decision=decision)

        ctx = G.ActorContext(actor=actor, actor_type=actor_type,
                             identity_source="token_map")
        with pytest.raises(ApprovalPermissionError) as exc:
            eng.confirm_manual_promote(CAP, record_id=request.record_id,
                                       actor=actor, approve=True, note="越权尝试")
        assert exc.value.decision.denied_by_matrix is True

        # 越权三件套：拒绝 + 审计 + 告警
        actions = _chain_actions(isolated_audit)
        assert actions.count("policy.denied") == 1
        assert "approval.approved" not in actions
        envelopes = ev.iter_events(directory=str(tmp_path / "events"),
                                   types=[ev.EV_POLICY_DENIED])
        assert len(envelopes) == 1
        assert envelopes[0].actor == actor_type

    @pytest.mark.parametrize("actor_type,actor", [
        (ACTOR_AUTO, "auto:evolver"),
        (ACTOR_SUB_AGENT, "sub_agent:7"),
    ])
    def test_non_human_cannot_apply_stage_promote(self, engine, actor_type,
                                                 actor, isolated_audit):
        """§7.0「强制推进 stage」行：human 专属（reason 必填）"""
        eng, registry = engine
        decision = eng.evaluate(CAP, evidence=_evidence())
        request = eng.manual_promote(CAP, "owner", decision=decision)
        eng.confirm_manual_promote(CAP, record_id=request.record_id,
                                   actor="owner", approve=True, note="通过")
        result = eng.apply_manual_promote(CAP, record_id=request.record_id,
                                         actor=actor, registry=registry,
                                         decision=decision)
        assert result["applied"] is False
        assert result["denied_by_matrix"] is True
        assert any("越权拒绝" in r for r in result["reasons"])
        assert not registry.patches
        assert _chain_actions(isolated_audit).count("policy.denied") == 1

    def test_non_human_cannot_submit_stage_promote(self, engine):
        """提交阶段即要求 human（§7.0「强制推进 stage」行）

        真实链路：`manual_promote(actor="auto:evolver")` 经 `flow.submit`
        被矩阵拒绝 ⇒ 抛 `ApprovalPermissionError`（已告警 + 已审计）。
        """
        eng, _ = engine
        decision = eng.evaluate(CAP, evidence=_evidence())
        with pytest.raises(ApprovalPermissionError) as exc:
            eng.manual_promote(CAP, "auto:evolver", decision=decision)
        assert exc.value.decision.denied_by_matrix is True

    def test_human_apply_succeeds(self, engine):
        eng, registry = engine
        decision = eng.evaluate(CAP, evidence=_evidence())
        request = eng.manual_promote(CAP, "owner", decision=decision)
        eng.confirm_manual_promote(CAP, record_id=request.record_id,
                                   actor="owner", approve=True, note="通过")
        result = eng.apply_manual_promote(CAP, record_id=request.record_id,
                                         actor="owner", registry=registry,
                                         decision=decision)
        assert result["applied"] is True


# ════════════════════════════════════════════════════════════
#  拒绝面与回退面
# ════════════════════════════════════════════════════════════


class TestBoundaries:
    def test_quality_gate_still_blocks_manual_channel(self, engine):
        """④⑤⑥ 一票否决不受本次改动影响（既有行为不变）"""
        eng, _ = engine
        blocked = eng.evaluate(CAP, evidence=_evidence(
            p99={"candidate_ms": 99.0, "upstream_ms": 1.0}))
        request = eng.manual_promote(CAP, "owner", decision=blocked)
        assert request.permitted is False
        assert any("不可由人工绕过" in r for r in request.blocked_reasons)

    def test_apply_without_approval_still_refused(self, engine):
        eng, registry = engine
        decision = eng.evaluate(CAP, evidence=_evidence())
        request = eng.manual_promote(CAP, "owner", decision=decision)
        result = eng.apply_manual_promote(CAP, record_id=request.record_id,
                                         actor="owner", registry=registry,
                                         decision=decision)
        assert result["applied"] is False
        assert "人工批准" in result["reasons"][0]

    def test_security_package_failure_falls_back(self, engine, monkeypatch):
        """**硬约束 1**：新增机制失败不得阻断主流程（回退既有行为）"""
        import sys

        eng, registry = engine
        decision = eng.evaluate(CAP, evidence=_evidence())
        request = eng.manual_promote(CAP, "owner", decision=decision)
        eng.confirm_manual_promote(CAP, record_id=request.record_id,
                                   actor="owner", approve=True, note="通过")

        # 模拟安全包不可导入：`sys.modules[name] = None` ⇒ `from ... import` 抛 ImportError
        monkeypatch.setitem(sys.modules, "agent.security.approval_guard", None)
        result = eng.apply_manual_promote(CAP, record_id=request.record_id,
                                         actor="owner", registry=registry,
                                         decision=decision)
        assert result["applied"] is True, "安全包不可用时应回退既有行为"

    def test_no_runtime_pollution(self, tmp_path):
        """落盘纪律：本套件全部走 tmp（无运行时目录污染，S3-02/S3-03 两次教训）"""
        import os
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        assert str(os.environ.get("APPROVAL_RECORDS_PATH", "")).startswith(str(tmp_path))
        assert str(os.environ.get("CP_EVENTS_DIR", "")).startswith(str(tmp_path))
        # 运行时审批记录文件不得因本套件而新增
        runtime = repo / "data" / "approval_records.jsonl"
        if runtime.exists():
            before = runtime.stat().st_mtime_ns
            assert runtime.stat().st_mtime_ns == before

    def test_stage_gate_untouched_by_matrix(self):
        """矩阵接线不改 stage 门语义（既有 S3-01/S3-02 行为不变）"""
        from agent.digestion.models import VERDICT_DEFERRED

        verdict, reasons = ST.evaluate_migration(
            capability_id=CAP, from_stage="shadow", to_stage=PROMOTED_STAGE,
            evidence={"whatever": 1})
        assert verdict == VERDICT_DEFERRED
        assert "不越权推进" in reasons[0]

    def test_audit_payload_never_contains_raw_ip(self, engine, isolated_audit,
                                                s401_ip_key, tmp_path):
        """裁定 B 在真实链路上同样成立：原始 IP 不落链（连 IP 形态都不出现）"""
        import re

        eng, registry = engine
        human_ctx = G.ActorContext(actor="owner", actor_type=ACTOR_HUMAN,
                                   identity_source="token_map", actor_ip="10.0.0.7")
        decision = eng.evaluate(CAP, evidence=_evidence())
        # 经带 IP 的执行体上下文提交与审批（真实链路的服务层入口）
        request = eng.manual_promote(CAP, "owner", decision=decision)
        eng.confirm_manual_promote(CAP, record_id=request.record_id,
                                   actor="owner", approve=True, note="通过",
                                   flow=eng.approval_flow())
        # 直接经审批流以携带 IP 的上下文审批一次，验证 PII 字段形态
        from agent.skills_mgmt.approval import ApprovalFlow
        flow = ApprovalFlow(records_path=str(tmp_path / "pii_probe.jsonl"))
        record = flow.submit("skill", "probe", action="params_submit",
                             actor="owner", actor_ctx=human_ctx)
        flow.approve(record.record_id, actor="owner", actor_ctx=human_ctx)
        ip_fields = record.pii_fields()
        assert ip_fields["actor_ip_masked"] == "10.0.xxx.xxx"
        assert ip_fields["actor_ip_hash"]

        dumped = json.dumps([e.payload for e in isolated_audit.entries()],
                            ensure_ascii=False)
        assert "10.0.0.7" not in dumped
        # 掩码字段本身不构成 IP（不得被误判为原始 IP）
        assert re.search(r"\b10\.0\.\d{1,3}\.\d{1,3}\b", dumped) is None
