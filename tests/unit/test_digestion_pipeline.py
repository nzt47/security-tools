"""TASK-S3-01 消化流水线端到端单测（`DigestionService.pipeline`）

验收对应：
- 真实/模拟轨迹 ≥20 条同类可产出候选模式；
- 端到端产物为 **draft 态** SKILL.md；
- stage 迁移三处联动；证据不足不静默推进；
- 入口能力键归一（L1：历史工具名落账行不丢）。

台账/registry/事件/审计全部隔离到 tmp_path，不触碰运行时真实数据。
"""

from __future__ import annotations

import json

import pytest

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain
from agent.digestion import DigestionService
from agent.digestion import capability as cap_mod
from agent.digestion.models import (
    MIN_SAME_KIND_TRACES,
    SameTaskKey,
    TraceSet,
)
from agent.observability.trace_v2 import TraceFacade

from tests.unit.digestion_util import build_ledger, descriptor_registry

CAP = "cp.builtin.read_file"
TRAILING = ("cp.builtin.shell_execute", "cp.builtin.write_file")


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


@pytest.fixture(autouse=True)
def isolated_audit(chain):
    previous = facade_mod.audit.bind(chain)
    old_enabled = facade_mod.audit.enabled
    facade_mod.audit.enabled = True
    facade_mod.audit.reset_counters()
    yield facade_mod.audit
    facade_mod.audit.bind(previous)
    facade_mod.audit.enabled = old_enabled


@pytest.fixture(autouse=True)
def isolated_events(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    import agent.observability.events as events_mod
    events_mod.reset_event_stores()
    yield str(tmp_path / "events")
    events_mod.reset_event_stores()


@pytest.fixture(autouse=True)
def clean_facade():
    from agent.observability.trace_v2 import _trace_context_var
    _trace_context_var.set(None)
    yield
    _trace_context_var.set(None)


@pytest.fixture
def facade(tmp_path):
    f = TraceFacade(str(tmp_path / "trace.db"))
    TraceFacade._instance = f
    yield f
    f.flush()
    f._store.stop(timeout=2.0)
    TraceFacade._instance = None


@pytest.fixture
def reg(tmp_path):
    return descriptor_registry(tmp_path, ["read_file", "shell_execute",
                                          "write_file"])


@pytest.fixture
def svc(facade, reg, tmp_path):
    return DigestionService(store=facade._store, registry=reg,
                            draft_dir=str(tmp_path / "drafts"))


def _events(event_type: str, directory: str):
    import agent.observability.events as events_mod
    return [e for e in events_mod.iter_events(directory=directory)
            if e.type == event_type]


# ════════════════════════════════════════════════════════════
#  1. 端到端：轨迹 → 清洗 → 挖掘 → 草稿
# ════════════════════════════════════════════════════════════


class TestPipelineEndToEnd:
    def test_twenty_plus_same_kind_yields_pattern(self, facade, svc):
        """验收：≥20 条同类轨迹可产出候选模式"""
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        assert report.eligible is True
        assert len(report.candidates) == 1
        pattern = report.candidates[0]
        assert pattern.support >= MIN_SAME_KIND_TRACES
        assert pattern.lcs_length >= 2
        assert [s.label for s in pattern.steps] == [
            "read_file", "shell_execute", "write_file"]

    def test_trajectory_and_cleanup_metrics(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        assert report.trajectories_total == 24
        assert report.cleanup["merged_steps"] == 24     # 重试重复被合并
        assert report.cleanup["dropped_steps"] >= 24    # 探索前缀被剔除
        assert "explore_prefix" in report.cleanup["noise_flags"]
        assert "duplicate_step_merged" in report.cleanup["noise_flags"]

    def test_draft_is_generated_and_persisted(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        assert len(report.drafts) == 1
        draft = report.drafts[0]
        assert draft.status == "draft"
        assert draft.path
        with open(draft.path, encoding="utf-8") as fh:
            text = fh.read()
        assert "status: draft" in text
        assert "enabled: false" in text
        assert "自动挖掘草稿" in text

    def test_draft_not_written_when_disabled(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False,
                              persist_draft=False)
        assert report.drafts and report.drafts[0].path == ""

    def test_skill_generated_event_emitted(self, facade, svc, isolated_events):
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        events = _events("skill.generated", isolated_events)
        assert len(events) == 1
        assert events[0].payload["status"] == "draft"
        assert events[0].payload["track"] == "digestion"
        assert events[0].payload["skill_id"] == report.drafts[0].skill_id
        assert events[0].event_id in report.events

    def test_report_is_json_serializable(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        text = json.dumps(report.as_dict(), ensure_ascii=False)
        assert "pattern_id" in text
        assert "stage_recommendation" in text

    def test_deterministic_across_runs(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=0)
        facade.flush()
        first = svc.pipeline(capability_id=CAP, migrate=False)
        second = svc.pipeline(capability_id=CAP, migrate=False)
        assert first.candidates[0].pattern_id == second.candidates[0].pattern_id
        assert first.drafts[0].skill_id == second.drafts[0].skill_id
        assert [s.label for s in first.candidates[0].steps] == \
            [s.label for s in second.candidates[0].steps]


class TestThresholdAndEligibility:
    def test_below_threshold_not_eligible(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=5, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        assert report.eligible is False
        assert "门槛" in report.reason

    def test_shallow_skeleton_not_eligible(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=22, fail_every=0,
                     trailing=())
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        assert report.eligible is False
        assert "单步骨架" in report.reason
        # 但草稿仍然产出（由 front matter 标注"未达升格门槛"，判断权留人工）
        assert report.candidates and report.drafts

    def test_empty_ledger(self, svc):
        report = svc.pipeline(capability_id=CAP, migrate=False)
        assert report.eligible is False
        assert report.trajectories_total == 0

    def test_threshold_is_configurable(self, facade, reg, tmp_path):
        build_ledger(facade, capability_id=CAP, tasks=6, fail_every=0)
        facade.flush()
        svc = DigestionService(store=facade._store, registry=reg,
                               threshold=3, draft_dir=str(tmp_path / "d"))
        report = svc.pipeline(capability_id=CAP, migrate=False)
        assert report.eligible is True
        assert report.threshold == 3


class TestNegativeSamplesAndBranches:
    def test_failure_trajectories_kept_as_negative(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=7)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        assert report.negative_total == 3
        assert any(item["negative"] > 0 for item in report.trace_sets)
        assert "negative_sample" in report.cleanup["noise_flags"]

    def test_negative_not_in_backbone_sample(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=7)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        pattern = report.candidates[0]
        assert pattern.negative_samples == 3
        assert pattern.sample_size == pattern.support + pattern.negative_samples

    def test_branches_extracted_from_success_vs_failure(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=28, fail_every=7)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        assert report.candidates[0].branches
        assert any("write_file" in b.condition for b in
                   report.candidates[0].branches)

    def test_no_branches_when_all_succeed(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=22, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        assert report.candidates[0].branches == []


class TestParameterSlots:
    def test_varying_values_become_named_slots(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        placeholders = {s.placeholder for s in report.candidates[0].slots}
        assert "${cmd}" in placeholders

    def test_constant_values_stay_literal(self, facade, svc):
        """``encoding`` 跨轨迹恒定 ⇒ 不进参数槽"""
        build_ledger(facade, capability_id=CAP, tasks=22, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        names = {s.name for s in report.candidates[0].slots}
        assert "encoding" not in names

    def test_slots_attached_to_pattern_steps(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=22, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        pattern = report.candidates[0]
        attached = {k: v for step in pattern.steps for k, v in step.params.items()}
        assert attached.get("cmd") == "${cmd}"

    def test_path_values_are_shape_masked(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=22, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        dump = json.dumps(report.as_dict(), ensure_ascii=False)
        assert "C:/repo/proj0" not in dump


# ════════════════════════════════════════════════════════════
#  2. 同类判定键与分组
# ════════════════════════════════════════════════════════════


class TestIntentGrouping:
    def test_same_kind_trajectories_share_one_key(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        assert len(report.trace_sets) == 1
        assert report.trace_sets[0]["size"] == 24

    def test_key_components(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=22, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        key = report.cleanup["primary_key"]
        capability_id, intent_key, outcome = key.split("|")
        assert capability_id == CAP
        assert intent_key.startswith("shape:")
        assert outcome == "success"

    def test_same_task_key_excludes_task_identity(self):
        key = SameTaskKey(CAP, "shape:path", "success")
        assert "task" not in key.as_str()

    def test_different_capabilities_do_not_merge(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=22, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id="cp.builtin.write_file",
                              migrate=False)
        assert report.capability_id == "cp.builtin.write_file"
        assert report.trajectories_total == 22

    def test_intent_override_creates_distinct_bucket(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=22, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, intent="修复失败测试",
                              migrate=False)
        assert report.cleanup["primary_key"].split("|")[1] == \
            "修+复+失+测+试+败"


# ════════════════════════════════════════════════════════════
#  3. L1：入口能力键归一（历史工具名行）
# ════════════════════════════════════════════════════════════


class TestCapabilityKeyNormalization:
    def test_merge_keys_includes_legacy_tool_name(self, facade, reg):
        keys = cap_mod.merge_keys(CAP, registry=reg)
        assert keys[0] == CAP
        assert "read_file" in keys

    def test_legacy_rows_are_collected(self, facade, reg):
        """改写前落账的历史行（工具名）不得被入口丢掉"""
        facade.start(task_id="legacy1", workspace_id="ws_l")
        facade.record("read_file", args={"path": "/a.py"}, output={"ok": True})
        facade.finish()
        facade.flush()
        rows, meta = cap_mod.collect_rows(facade._store, CAP, registry=reg)
        assert meta["legacy_matched"] == 1
        assert meta["matched"] == 1
        assert any(str(r.capability_id) == "read_file" for r in rows)

    def test_normalize_trace_capability(self, facade, reg):
        facade.start(task_id="t", workspace_id="ws")
        trace = facade.record("read_file", args={"path": "/a.py"},
                              output={"ok": True})
        facade.finish()
        assert cap_mod.normalize_trace_capability(trace, registry=reg) == CAP

    def test_collect_expands_whole_task_chain(self, facade, reg):
        facade.start(task_id="t1", workspace_id="ws")
        facade.record(CAP, args={"path": "/a.py"}, output={"ok": True})
        facade.record("shell_execute", args={"cmd": "x"}, output={"ok": True})
        facade.finish()
        facade.flush()
        rows, meta = cap_mod.collect_rows(facade._store, CAP, registry=reg)
        assert meta["matched"] == 1
        assert meta["trajectory_rows"] == 3      # 2 能力行 + 1 任务级收口行
        assert any(not str(r.capability_id) for r in rows)

    def test_step_order_follows_ledger_order(self, facade, reg):
        facade.start(task_id="t1", workspace_id="ws")
        for i, label in enumerate(["shell_execute", CAP, "write_file"]):
            facade.record(label, args={"k": f"v{i}"}, output={"ok": True},
                          started_at=100.0 + i)
        facade.finish()
        facade.flush()
        rows, _ = cap_mod.collect_rows(facade._store, CAP, registry=reg)
        caps = [str(r.capability_id) for r in rows if str(r.capability_id)]
        assert caps == ["shell_execute", CAP, "write_file"]

    def test_collect_limit_applies_to_trajectories(self, facade, reg):
        for i in range(5):
            facade.start(task_id=f"t{i}", workspace_id="ws")
            facade.record(CAP, args={"path": f"/{i}.py"}, output={"ok": True},
                          started_at=100.0 + i)
            facade.finish()
        facade.flush()
        _, meta = cap_mod.collect_rows(facade._store, CAP, registry=reg, limit=2)
        assert meta["trajectories"] == 2
        assert meta["truncated"] is True

    def test_registry_unavailable_is_advisory(self, facade):
        class _Boom:
            def aliases_of(self, _cid):
                raise RuntimeError("down")

            def get(self, _cid):
                raise RuntimeError("down")

        keys = cap_mod.merge_keys(CAP, registry=_Boom())
        assert keys[0] == CAP and "read_file" in keys

    def test_resolve_falls_back_to_raw_name(self):
        out = cap_mod.resolve("read_file")
        assert out["capability_id"].startswith("cp.")

    def test_store_failure_returns_empty(self, reg):
        class _Boom:
            def query(self):
                raise RuntimeError("db down")

        rows, meta = cap_mod.collect_rows(_Boom(), CAP, registry=reg)
        assert rows == [] and meta["matched"] == 0

    def test_merge_keys_empty_input(self, reg):
        assert cap_mod.merge_keys("", registry=reg) == []

    def test_merge_keys_non_cp_id_has_no_tail(self, reg):
        keys = cap_mod.merge_keys("plain_tool_name", registry=reg)
        assert keys == ["plain_tool_name"]

    def test_merge_keys_includes_registered_name(self, tmp_path):
        from agent.descriptors.bridge import descriptor_from_builtin_tool
        from tests.unit.digestion_util import descriptor_registry

        reg = descriptor_registry(tmp_path, [])
        reg.register(descriptor_from_builtin_tool("read_file", "读文件",
                                                 source_id="tools"))
        keys = cap_mod.merge_keys("cp.tools.read_file", registry=reg)
        assert keys[0] == "cp.tools.read_file"
        assert "read_file" in keys

    def test_resolve_helper_falls_back_on_import_error(self, monkeypatch):
        import agent.descriptors.bridge as bridge_mod
        monkeypatch.delattr(bridge_mod, "resolve_capability_id")
        out = cap_mod.resolve("read_file")
        assert out["resolved_by"] == "advisory_fallback"
        assert out["capability_id"] == "read_file"

    def test_normalize_trace_capability_empty(self):
        class _T:
            capability_id = ""

        assert cap_mod.normalize_trace_capability(_T()) == ""

    def test_collect_rows_honest_when_no_match(self, facade, reg):
        facade.start(task_id="t", workspace_id="ws")
        facade.record("shell_execute", args={"cmd": "x"}, output={"ok": True})
        facade.finish()
        facade.flush()
        rows, meta = cap_mod.collect_rows(facade._store, CAP, registry=reg)
        assert rows == []
        assert meta["matched"] == 0
        assert meta["total_in_ledger"] >= 2

    def test_group_rows_by_task_preserves_order(self):
        class _R:
            def __init__(self, task_id, label):
                self.task_id = task_id
                self.capability_id = label

        rows = [_R("b", "x"), _R("a", "y"), _R("b", "z")]
        groups = cap_mod.group_rows_by_task(rows)
        assert list(groups) == ["b", "a"]
        assert [r.capability_id for r in groups["b"]] == ["x", "z"]


# ════════════════════════════════════════════════════════════
#  4. 显式 trace_set 入口 与 stage 迁移驱动
# ════════════════════════════════════════════════════════════


class TestExplicitTraceSetAndMigration:
    def test_trace_set_input_skips_collection(self, facade, svc):
        from agent.digestion.models import Trajectory, TrajectoryStep

        key = SameTaskKey(CAP, "shape:path", "success")
        trajectories = []
        for i in range(22):
            trajectories.append(Trajectory(
                trajectory_id=f"t{i}", task_id=f"t{i}", key=key,
                steps=[TrajectoryStep(seq=1, label="read_file",
                                      capability_id=CAP,
                                      params={"path": "${path}"}),
                       TrajectoryStep(seq=2, label="write_file",
                                      capability_id="cp.builtin.write_file",
                                      params={"path": "${path}"})]))
        report = svc.pipeline(trace_set=TraceSet(key=key,
                                                trajectories=trajectories),
                              migrate=False)
        assert report.capability_id == CAP
        assert report.eligible is True
        assert report.rows_total == 0

    def test_pipeline_first_entry_migration(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=True)
        assert report.stage_migration is not None
        assert report.stage_migration.applied is True
        assert report.stage_migration.to_stage == "borrowed"
        assert report.stage_migration.trace_policy
        assert "#read=UnifiedTraceStore.list_by_capability" in \
            report.stage_migration.trace_policy

    def test_pipeline_borrowed_to_mirrored_when_eligible(self, facade, svc, reg):
        reg.set_stage(CAP, "borrowed", trace_policy="trace:orig")
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=True)
        assert report.stage_recommendation["to"] == "mirrored"
        assert report.stage_migration.to_stage == "mirrored"
        assert report.stage_migration.applied is True
        assert reg.get(CAP).evolution.stage.value == "mirrored"

    def test_pipeline_does_not_advance_without_evidence(self, facade, svc, reg):
        """证据不足：**显式尝试后被拒**，台账不变，拒绝入链（不静默）"""
        reg.set_stage(CAP, "borrowed", trace_policy="trace:orig")
        # 22 条同类但骨架仅 1 步 ⇒ 有模式、证据不足
        build_ledger(facade, capability_id=CAP, tasks=22, fail_every=0,
                     trailing=())
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=True)
        assert report.stage_migration is not None
        assert report.stage_migration.applied is False
        assert report.stage_migration.verdict == "insufficient_evidence"
        assert report.stage_migration.audit_action == "digest.stage.refused"
        assert reg.get(CAP).evolution.stage.value == "borrowed"
        assert reg.get(CAP).evolution.trace_policy == "trace:orig"

    def test_below_threshold_makes_no_migration_attempt(self, facade, svc, reg):
        """连模式都没有 ⇒ 不发起迁移（缺口只在建议里披露，不产生事件噪声）"""
        reg.set_stage(CAP, "borrowed", trace_policy="trace:orig")
        build_ledger(facade, capability_id=CAP, tasks=5, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=True)
        assert report.stage_migration is None
        assert report.candidates == []
        assert report.stage_recommendation["eligible"] is False
        assert report.stage_recommendation["shortfall"]["threshold"] == \
            MIN_SAME_KIND_TRACES
        assert reg.get(CAP).evolution.stage.value == "borrowed"

    def test_migrate_false_only_recommends(self, facade, svc, reg):
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=False)
        assert report.stage_migration is None
        assert report.stage_recommendation["to"] == "borrowed"
        assert reg.get(CAP).evolution.stage is None

    def test_explicit_to_stage_honoured(self, facade, svc, reg):
        """显式目标态仍受迁移门约束：下游边 ⇒ deferred，台账不变"""
        reg.set_stage(CAP, "mirrored")
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=True, to_stage="shadow")
        assert report.stage_migration.verdict == "deferred_to_downstream"
        assert reg.get(CAP).evolution.stage.value == "mirrored"

    def test_explicit_illegal_target_rejected(self, facade, svc, reg):
        reg.set_stage(CAP, "borrowed", trace_policy="t")
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=True, to_stage="native")
        assert report.stage_migration.verdict == "illegal_transition"
        assert reg.get(CAP).evolution.stage.value == "borrowed"

    def test_stage_migration_event_in_report_events(self, facade, svc,
                                                    isolated_events):
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=0)
        facade.flush()
        report = svc.pipeline(capability_id=CAP, migrate=True)
        assert report.stage_migration.event_id in report.events
        assert len(_events("digest.stage", isolated_events)) == 1


class TestIngestUnstaged:
    def test_dry_run(self, facade, svc, reg):
        result = svc.ingest_unstaged(execute=False)
        assert result["empty_stage"] == 3
        assert result["ingested"] == []

    def test_execute(self, facade, svc, reg):
        result = svc.ingest_unstaged(execute=True)
        assert len(result["ingested"]) == 3
        assert result["warnings_after"] == 0
        assert all(d.evolution.stage.value == "borrowed" for d in reg.list())


class TestServiceWiring:
    def test_default_store_and_registry_are_lazy(self):
        svc = DigestionService(store=None, registry=None)
        assert svc._store is None and svc._registry is None

    def test_collect_uses_injected_store(self, facade, reg):
        build_ledger(facade, capability_id=CAP, tasks=3, fail_every=0)
        facade.flush()
        svc = DigestionService(store=facade._store, registry=reg)
        rows, meta = svc.collect(CAP)
        assert meta["trajectories"] == 3
        assert rows

    def test_emit_events_disabled(self, facade, reg, tmp_path, isolated_events):
        build_ledger(facade, capability_id=CAP, tasks=24, fail_every=0)
        facade.flush()
        svc = DigestionService(store=facade._store, registry=reg,
                               emit_events=False,
                               draft_dir=str(tmp_path / "d"))
        report = svc.pipeline(capability_id=CAP, migrate=True)
        assert report.events == []
        assert _events("digest.stage", isolated_events) == []
        assert _events("skill.generated", isolated_events) == []

    def test_build_trajectories_reports_stats(self, facade, svc):
        build_ledger(facade, capability_id=CAP, tasks=3, fail_every=0)
        facade.flush()
        rows, _ = svc.collect(CAP)
        trajectories, meta = svc.build_trajectories(rows, capability_id=CAP)
        assert meta["trajectories"] == 3
        assert meta["intent_keys"] == ["shape:encoding+path"]
        assert all(t.step_count == 3 for t in trajectories)
