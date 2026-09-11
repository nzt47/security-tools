"""TASK-S3-01 stage 迁移单测（三处联动 / 证据不足不静默推进 / L4 首次入轨）

联动三处：`descriptor.evolution.stage` + 链式审计 + `digest.stage` 事件。
审计链与事件目录均隔离到 tmp_path，绝不触碰真实台账。
"""

from __future__ import annotations

import json
import os

import pytest

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain
from agent.descriptors.bridge import register_bridge_view
from agent.descriptors.registry import DescriptorRegistry
from agent.digestion import stage as stage_mod
from agent.digestion.models import (
    MIN_SAME_KIND_TRACES,
    VERDICT_APPLIED,
    VERDICT_DEFERRED,
    VERDICT_ERROR,
    VERDICT_ILLEGAL_TRANSITION,
    VERDICT_INSUFFICIENT_EVIDENCE,
    VERDICT_NOT_FOUND,
)

pytestmark = pytest.mark.usefixtures("isolated_audit", "isolated_events")


# ════════════════════════════════════════════════════════════
#  fixtures
# ════════════════════════════════════════════════════════════


@pytest.fixture
def chain(tmp_path):
    c = AuditChain(str(tmp_path / "audit_chain.db"),
                   roots_path=str(tmp_path / "roots.jsonl"),
                   signing_key_path=str(tmp_path / "k.pem"), auto_seal=False)
    yield c
    c.close(timeout=2.0)


@pytest.fixture
def isolated_audit(chain):
    previous = facade_mod.audit.bind(chain)
    old_enabled = facade_mod.audit.enabled
    facade_mod.audit.enabled = True
    facade_mod.audit.reset_counters()
    yield facade_mod.audit
    facade_mod.audit.bind(previous)
    facade_mod.audit.enabled = old_enabled


@pytest.fixture
def isolated_events(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    import agent.observability.events as events_mod
    events_mod.reset_event_stores()
    yield str(tmp_path / "events")
    events_mod.reset_event_stores()


@pytest.fixture
def reg(tmp_path):
    r = DescriptorRegistry(path=str(tmp_path / "d.json"), autosave=False)
    register_bridge_view(r, "builtin", [
        {"name": "read_file", "description": "读文件"},
        {"name": "write_file", "description": "写文件"}])
    return r


def _events(event_type: str, directory: str):
    import agent.observability.events as events_mod
    return [e for e in events_mod.iter_events(directory=directory)
            if e.type == event_type]


def _chain_actions(chain, action: str):
    chain.flush()
    return [e for e in chain.entries() if e.action == action]


def _find_key(node, key):
    """在嵌套载荷里递归找键（审计载荷经门面包装，层级随写入方而定）"""
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for value in node.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_key(value, key)
            if found is not None:
                return found
    return None


def _mirrored_evidence(**over) -> dict:
    evidence = {
        "trace_count": MIN_SAME_KIND_TRACES,
        "pattern_extracted": True,
        "pattern_steps": 3,
        "side_effect_profile": {"files_written_count": 2,
                                "files_written_shape": ["${path}"]},
    }
    evidence.update(over)
    return evidence


# ════════════════════════════════════════════════════════════
#  1. 迁移门（纯函数）
# ════════════════════════════════════════════════════════════


class TestEvaluateMigration:
    def test_borrowed_to_mirrored_pass(self):
        verdict, reasons = stage_mod.evaluate_migration(
            capability_id="cp.a.b", from_stage="borrowed", to_stage="mirrored",
            evidence=_mirrored_evidence())
        assert verdict == VERDICT_APPLIED and reasons == []

    def test_insufficient_trace_count(self):
        verdict, reasons = stage_mod.evaluate_migration(
            capability_id="cp.a.b", from_stage="borrowed", to_stage="mirrored",
            evidence=_mirrored_evidence(trace_count=19))
        assert verdict == VERDICT_INSUFFICIENT_EVIDENCE
        assert any("19 条 < 门槛 20 条" in r for r in reasons)

    def test_missing_pattern(self):
        verdict, reasons = stage_mod.evaluate_migration(
            capability_id="cp.a.b", from_stage="borrowed", to_stage="mirrored",
            evidence=_mirrored_evidence(pattern_extracted=False))
        assert verdict == VERDICT_INSUFFICIENT_EVIDENCE
        assert any("未提取出候选模式" in r for r in reasons)

    def test_shallow_pattern(self):
        verdict, reasons = stage_mod.evaluate_migration(
            capability_id="cp.a.b", from_stage="borrowed", to_stage="mirrored",
            evidence=_mirrored_evidence(pattern_steps=1))
        assert verdict == VERDICT_INSUFFICIENT_EVIDENCE
        assert any("单步骨架" in r for r in reasons)

    def test_missing_side_effect_profile(self):
        verdict, reasons = stage_mod.evaluate_migration(
            capability_id="cp.a.b", from_stage="borrowed", to_stage="mirrored",
            evidence=_mirrored_evidence(side_effect_profile=None))
        assert verdict == VERDICT_INSUFFICIENT_EVIDENCE
        assert any("副作用画像" in r for r in reasons)

    def test_incomplete_side_effect_profile(self):
        verdict, reasons = stage_mod.evaluate_migration(
            capability_id="cp.a.b", from_stage="borrowed", to_stage="mirrored",
            evidence=_mirrored_evidence(side_effect_profile={"a": 1}))
        assert verdict == VERDICT_INSUFFICIENT_EVIDENCE

    def test_multiple_gaps_all_reported(self):
        verdict, reasons = stage_mod.evaluate_migration(
            capability_id="cp.a.b", from_stage="borrowed", to_stage="mirrored",
            evidence={"trace_count": 0, "pattern_extracted": False})
        assert verdict == VERDICT_INSUFFICIENT_EVIDENCE
        assert len(reasons) >= 3

    def test_first_entry_requires_evidence(self):
        verdict, _ = stage_mod.evaluate_migration(
            capability_id="cp.a.b", from_stage=None, to_stage="borrowed",
            evidence={"first_entry": True, "trace_policy": "trace:x"})
        assert verdict == VERDICT_APPLIED

    def test_first_entry_without_policy_rejected(self):
        verdict, reasons = stage_mod.evaluate_migration(
            capability_id="cp.a.b", from_stage=None, to_stage="borrowed",
            evidence={"first_entry": True})
        assert verdict == VERDICT_INSUFFICIENT_EVIDENCE
        assert any("trace_policy" in r for r in reasons)

    def test_downstream_edge_deferred(self):
        for from_stage, to_stage in stage_mod.DOWNSTREAM_EDGES:
            verdict, reasons = stage_mod.evaluate_migration(
                capability_id="cp.a.b", from_stage=from_stage,
                to_stage=to_stage, evidence=_mirrored_evidence())
            assert verdict == VERDICT_DEFERRED
            assert "S3-02" in reasons[0] or "S3-03" in reasons[0]

    def test_illegal_edge(self):
        verdict, reasons = stage_mod.evaluate_migration(
            capability_id="cp.a.b", from_stage="borrowed", to_stage="native",
            evidence=_mirrored_evidence())
        assert verdict == VERDICT_ILLEGAL_TRANSITION

    def test_unknown_stage_value(self):
        verdict, reasons = stage_mod.evaluate_migration(
            capability_id="cp.a.b", from_stage="borrowed", to_stage="nonsense",
            evidence={})
        assert verdict == VERDICT_ILLEGAL_TRANSITION
        assert any("七态" in r for r in reasons)

    def test_empty_capability_id(self):
        verdict, _ = stage_mod.evaluate_migration(
            capability_id="", from_stage=None, to_stage="borrowed", evidence={})
        assert verdict == VERDICT_NOT_FOUND

    def test_pure_function_has_no_side_effects(self, reg):
        before = reg.get("cp.builtin.read_file").evolution.stage
        stage_mod.evaluate_migration(capability_id="cp.builtin.read_file",
                                     from_stage="borrowed", to_stage="mirrored",
                                     evidence=_mirrored_evidence())
        assert reg.get("cp.builtin.read_file").evolution.stage == before


class TestSevenStates:
    def test_seven_states_complete(self):
        assert len(stage_mod.SEVEN_STATES) == 7

    def test_driven_edges_are_owned_by_this_task(self):
        assert (None, "borrowed") in stage_mod.DRIVEN_EDGES
        assert ("borrowed", "mirrored") in stage_mod.DRIVEN_EDGES

    def test_main_chain_is_prefix_of_seven(self):
        assert stage_mod.SEVEN_STATES[:len(stage_mod.MAIN_CHAIN)] == \
            stage_mod.MAIN_CHAIN


class TestDigestRunId:
    def test_deterministic(self):
        a = stage_mod.digest_run_id(capability_id="c", from_stage="borrowed",
                                    to_stage="mirrored", evidence={"x": 1})
        b = stage_mod.digest_run_id(capability_id="c", from_stage="borrowed",
                                    to_stage="mirrored", evidence={"x": 1})
        assert a == b and a.startswith("dg_")

    def test_changes_with_evidence(self):
        a = stage_mod.digest_run_id(capability_id="c", from_stage="borrowed",
                                    to_stage="mirrored", evidence={"x": 1})
        b = stage_mod.digest_run_id(capability_id="c", from_stage="borrowed",
                                    to_stage="mirrored", evidence={"x": 2})
        assert a != b

    def test_scalar_view_truncates(self):
        view = stage_mod._scalar_view({"s": "x" * 500})
        assert len(view["s"]) == 200


# ════════════════════════════════════════════════════════════
#  2. 三处联动
# ════════════════════════════════════════════════════════════


class TestThreeWayLinkage:
    def test_descriptor_audit_event_all_written(self, reg, chain, isolated_events):
        mig = stage_mod.stage_migrate(
            "cp.builtin.read_file", "borrowed",
            {"first_entry": True, "trace_policy": "trace:x:ledger=y"},
            registry=reg)
        assert mig.applied is True and mig.verdict == VERDICT_APPLIED
        # 落点 1：descriptor
        assert reg.get("cp.builtin.read_file").evolution.stage.value == "borrowed"
        # 落点 2：链式审计（registry 独占写入）
        entries = _chain_actions(chain, "descriptor.stage")
        assert len(entries) == 1
        assert entries[0].subject == "capability:cp.builtin.read_file"
        assert _find_key(entries[0].payload, "to") == "borrowed"
        assert _find_key(entries[0].payload, "from") is None
        # 落点 3：digest.stage 事件
        events = _events("digest.stage", isolated_events)
        assert len(events) == 1
        assert events[0].payload["capability_id"] == "cp.builtin.read_file"
        assert events[0].payload["applied"] is True

    def test_event_carries_run_id_and_verdict(self, reg, isolated_events):
        mig = stage_mod.stage_migrate(
            "cp.builtin.read_file", "borrowed",
            {"first_entry": True, "trace_policy": "trace:x"}, registry=reg)
        [event] = _events("digest.stage", isolated_events)
        assert event.payload["digest_run_id"] == mig.digest_run_id
        assert event.payload["verdict"] == VERDICT_APPLIED

    def test_no_duplicate_audit_records(self, reg, chain):
        """一动作一记录：不得既有 descriptor.stage 又有 digest.stage 链上重复"""
        stage_mod.stage_migrate(
            "cp.builtin.read_file", "borrowed",
            {"first_entry": True, "trace_policy": "trace:x"}, registry=reg)
        chain.flush()
        actions = [e.action for e in chain.entries()]
        assert actions.count("descriptor.stage") == 1
        assert "digest.stage" not in actions

    def test_migration_fields_populated(self, reg):
        mig = stage_mod.stage_migrate(
            "cp.builtin.read_file", "borrowed",
            {"first_entry": True, "trace_policy": "trace:x"}, registry=reg)
        assert mig.audit_action == "descriptor.stage"
        assert mig.audit_seq > 0 and mig.audit_hash
        assert mig.trace_policy == "trace:x"
        assert mig.ok is True

    def test_event_replay_is_idempotent(self, reg, isolated_events):
        evidence = {"first_entry": True, "trace_policy": "trace:x"}
        stage_mod.stage_migrate("cp.builtin.read_file", "borrowed", evidence,
                                registry=reg)
        reg.set_stage("cp.builtin.read_file", None, actor="test")
        stage_mod.stage_migrate("cp.builtin.read_file", "borrowed", evidence,
                                registry=reg)
        assert len(_events("digest.stage", isolated_events)) == 1

    def test_emit_event_can_be_disabled(self, reg, isolated_events):
        stage_mod.stage_migrate("cp.builtin.read_file", "borrowed",
                                {"first_entry": True, "trace_policy": "t"},
                                registry=reg, emit_event=False)
        assert _events("digest.stage", isolated_events) == []

    def test_three_rails_share_one_correlation_key(self, reg, chain,
                                                   isolated_events):
        """三处联动**可互查**：链上 reason 与事件载荷共享同一 `digest_run_id`

        两条轨道独立写入，若无共享关联键就只能靠 (subject, detail.to) 隐式 join。
        """
        mig = stage_mod.stage_migrate(
            "cp.builtin.read_file", "borrowed",
            {"first_entry": True, "trace_policy": "trace:x"}, registry=reg)
        assert mig.audit_reason and f"run={mig.digest_run_id}" in mig.audit_reason
        entries = _chain_actions(chain, "descriptor.stage")
        assert len(entries) == 1
        blob = json.dumps(entries[0].payload, ensure_ascii=False)
        assert mig.digest_run_id in blob, "链上记录未携带 digest_run_id"
        [event] = _events("digest.stage", isolated_events)
        assert event.payload["digest_run_id"] == mig.digest_run_id
        # 反向：由链上记录的 subject 可定位能力，与事件载荷的 capability_id 一致
        assert entries[0].subject == f"capability:{event.payload['capability_id']}"

    def test_caller_reason_also_carries_run_id(self, reg, chain):
        """自定义 reason（如首次入轨的 rationale）同样被追加 run id

        此前仅"流水线默认 reason"含 run id，首次入轨路径传自定义 reason 时两轨
        无法互查 —— 该缺口已由实现期实测确认并在此固化。
        """
        mig = stage_mod.stage_migrate(
            "cp.builtin.read_file", "borrowed",
            {"first_entry": True, "trace_policy": "trace:x"}, registry=reg,
            reason="TASK-S3-01 首次入轨：stage 为空 → borrowed")
        assert mig.audit_reason.startswith("TASK-S3-01 首次入轨")
        assert f"run={mig.digest_run_id}" in mig.audit_reason
        [entry] = _chain_actions(chain, "descriptor.stage")
        assert mig.digest_run_id in json.dumps(entry.payload, ensure_ascii=False)

    def test_run_id_not_duplicated_when_already_present(self, reg):
        """调用方 reason 已含**同一** run id 时不重复追加（幂等拼接）"""
        evidence = {"first_entry": True, "trace_policy": "trace:x"}
        run_id = stage_mod.digest_run_id(
            capability_id="cp.builtin.read_file", from_stage=None,
            to_stage="borrowed", evidence=dict(evidence, actor="digestion_pipeline"))
        mig = stage_mod.stage_migrate(
            "cp.builtin.read_file", "borrowed", evidence, registry=reg,
            reason=f"自定义（run={run_id}）")
        assert mig.audit_reason.count("（run=") == 1
        assert mig.digest_run_id == run_id

    def test_correlation_key_survives_chain_redaction(self, reg, chain):
        """关联键在链载荷**脱敏后原样保留**（否则互查会静默失效）"""
        mig = stage_mod.stage_migrate(
            "cp.builtin.read_file", "borrowed",
            {"first_entry": True, "trace_policy": "trace:x"}, registry=reg)
        [entry] = _chain_actions(chain, "descriptor.stage")
        reason = _find_key(entry.payload, "reason") or ""
        assert mig.digest_run_id in reason
        assert "*" not in mig.digest_run_id


# ════════════════════════════════════════════════════════════
#  3. 不静默推进
# ════════════════════════════════════════════════════════════


class TestRefusalIsNeverSilent:
    def test_insufficient_evidence_keeps_stage(self, reg, chain, isolated_events):
        reg.set_stage("cp.builtin.read_file", "borrowed",
                      trace_policy="trace:orig")
        mig = stage_mod.stage_migrate("cp.builtin.read_file", "mirrored",
                                      _mirrored_evidence(trace_count=3),
                                      registry=reg)
        assert mig.applied is False
        assert mig.verdict == VERDICT_INSUFFICIENT_EVIDENCE
        assert reg.get("cp.builtin.read_file").evolution.stage.value == "borrowed"
        assert reg.get("cp.builtin.read_file").evolution.trace_policy == "trace:orig"

    def test_refusal_recorded_in_chain(self, reg, chain):
        """台账未变 ⇒ 链上本无记录 ⇒ 必须补 `digest.stage.refused`"""
        mig = stage_mod.stage_migrate("cp.builtin.read_file", "mirrored",
                                      _mirrored_evidence(trace_count=1),
                                      registry=reg)
        assert mig.audit_action == "digest.stage.refused"
        entries = _chain_actions(chain, "digest.stage.refused")
        assert len(entries) == 1
        assert entries[0].payload.get("status") == "refused"

    def test_refusal_event_carries_reasons(self, reg, isolated_events):
        stage_mod.stage_migrate("cp.builtin.read_file", "mirrored",
                                _mirrored_evidence(trace_count=1), registry=reg)
        [event] = _events("digest.stage", isolated_events)
        assert event.payload["applied"] is False
        assert event.payload["reasons"]

    def test_illegal_transition_not_applied(self, reg):
        mig = stage_mod.stage_migrate("cp.builtin.read_file", "native",
                                      _mirrored_evidence(), registry=reg)
        assert mig.applied is False
        assert mig.verdict == VERDICT_ILLEGAL_TRANSITION
        assert not reg.get("cp.builtin.read_file").evolution.stage

    def test_deferred_edge_not_applied(self, reg):
        reg.set_stage("cp.builtin.read_file", "mirrored")
        mig = stage_mod.stage_migrate("cp.builtin.read_file", "shadow",
                                      _mirrored_evidence(), registry=reg)
        assert mig.applied is False
        assert mig.verdict == VERDICT_DEFERRED
        assert reg.get("cp.builtin.read_file").evolution.stage.value == "mirrored"

    def test_unknown_capability(self, reg, isolated_events):
        mig = stage_mod.stage_migrate("cp.nope.x", "borrowed",
                                      {"first_entry": True, "trace_policy": "t"},
                                      registry=reg)
        assert mig.verdict == VERDICT_NOT_FOUND
        assert mig.applied is False

    def test_registry_failure_is_converged_not_raised(self, isolated_events):
        class _Boom:
            def get(self, _cid):
                raise RuntimeError("ledger down")

        mig = stage_mod.stage_migrate("cp.x.y", "borrowed", {}, registry=_Boom())
        assert mig.verdict == VERDICT_ERROR
        assert "ledger down" in mig.reasons[0]
        # 台账读失败也不静默：发事件留痕
        [event] = _events("digest.stage", isolated_events)
        assert event.payload["verdict"] == VERDICT_ERROR

    def test_set_stage_failure_converged(self, reg):
        class _Reg:
            def get(self, cid):
                return reg.get(cid)

            def set_stage(self, *a, **k):
                raise RuntimeError("write denied")

        mig = stage_mod.stage_migrate(
            "cp.builtin.read_file", "borrowed",
            {"first_entry": True, "trace_policy": "t"}, registry=_Reg())
        assert mig.verdict == VERDICT_ERROR
        assert mig.applied is False


# ════════════════════════════════════════════════════════════
#  4. L4 首次入轨
# ════════════════════════════════════════════════════════════


class TestFirstEntryStage:
    def test_unstaged_asset_enters_at_borrowed(self, reg):
        stage, policy, rationale = stage_mod.first_entry_stage(
            reg.get("cp.builtin.read_file"))
        assert stage == "borrowed"
        assert "#read=UnifiedTraceStore.list_by_capability" in policy
        assert "首次入轨" in rationale

    def test_policy_is_real_ledger_reference_not_placeholder(self, reg):
        _, policy, _ = stage_mod.first_entry_stage(reg.get("cp.builtin.read_file"))
        assert "pending" not in policy
        assert "ledger=unified_traces@agent/data/tool_trace.db" in policy
        assert "capability_id=cp.builtin.read_file" in policy

    def test_native_not_auto_granted(self, reg):
        _, _, rationale = stage_mod.first_entry_stage(reg.get("cp.builtin.read_file"))
        assert "不置 native" in rationale


class TestBackfillStages:
    def test_dry_run_touches_nothing(self, reg):
        result = stage_mod.backfill_stages(reg, execute=False)
        assert result["executed"] is False
        assert result["empty_stage"] == 2
        assert len(result["plan"]) == 2
        assert result["ingested"] == []
        assert not reg.get("cp.builtin.read_file").evolution.stage

    def test_execute_ingests_all(self, reg):
        result = stage_mod.backfill_stages(reg, execute=True)
        assert result["executed"] is True
        assert len(result["ingested"]) == 2
        assert result["failed"] == []
        assert result["warnings_after"] == 0
        for desc in reg.list():
            assert desc.evolution.stage.value == "borrowed"
            assert desc.evolution.trace_policy

    def test_by_source_type_breakdown(self, reg):
        result = stage_mod.backfill_stages(reg, execute=False)
        assert result["by_source_type"] == {"builtin": 2}

    def test_idempotent_second_run(self, reg):
        stage_mod.backfill_stages(reg, execute=True)
        again = stage_mod.backfill_stages(reg, execute=True)
        assert again["empty_stage"] == 0
        assert again["plan"] == []

    def test_limit_batches(self, reg):
        result = stage_mod.backfill_stages(reg, execute=False, limit=1)
        assert result["empty_stage"] == 1

    def test_plan_carries_rationale_and_policy(self, reg):
        result = stage_mod.backfill_stages(reg, execute=False)
        item = result["plan"][0]
        assert item["from"] is None and item["to"] == "borrowed"
        assert item["rationale"] and item["trace_policy"]

    def test_empty_registry(self, tmp_path):
        empty = DescriptorRegistry(path=str(tmp_path / "empty.json"),
                                   autosave=False)
        result = stage_mod.backfill_stages(empty, execute=False)
        assert result["empty_stage"] == 0 and result["plan"] == []


class TestRecommendedStage:
    def test_untracked_asset_recommends_borrowed(self):
        rec = stage_mod.recommended_stage({"capability_id": "c",
                                           "current_stage": None})
        assert rec["to"] == "borrowed" and rec["eligible"] is True

    def test_borrowed_eligible_recommends_mirrored(self):
        rec = stage_mod.recommended_stage({
            "capability_id": "c", "current_stage": "borrowed",
            **_mirrored_evidence()})
        assert rec["to"] == "mirrored" and rec["eligible"] is True

    def test_shortfall_disclosed(self):
        rec = stage_mod.recommended_stage({
            "capability_id": "c", "current_stage": "borrowed",
            **_mirrored_evidence(trace_count=2)})
        assert rec["to"] == "borrowed" and rec["eligible"] is False
        assert rec["shortfall"]["threshold"] == MIN_SAME_KIND_TRACES
