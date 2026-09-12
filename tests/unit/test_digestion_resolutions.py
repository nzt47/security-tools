"""TASK-S8-05 步骤 4（D4）裁定留痕台账单测

验收对应（任务书 §四）：

- 【D4】`ResolutionStore` 可写可查；裁定记录入审计（``resolution.record``）；
- 【D4】needs 重算**跳过已裁定项**，并在报告中**列出已裁定项与依据**（不得静默消失）；
- 【D4】登记 6 条后 needs_review = 0；未裁定项**仍照报**（不放宽规则）。

隔离纪律：台账**显式传 tmp 路径** + 注入时钟；审计绑 tmp 链，不写运行时区。
"""

from __future__ import annotations

import json

import pytest

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain
from agent.descriptors import backfill as bf
from agent.digestion import resolutions as R

CLOCK = 1_800_000_000.0


@pytest.fixture
def chain(tmp_path):
    c = AuditChain(str(tmp_path / "audit.db"),
                   roots_path=str(tmp_path / "roots.jsonl"),
                   signing_key_path=str(tmp_path / "k.pem"), auto_seal=False)
    yield c
    c.close(timeout=2.0)


@pytest.fixture(autouse=True)
def isolated_audit(chain):
    previous = facade_mod.audit.bind(chain)
    facade_mod.audit.enabled = True
    facade_mod.audit.reset_counters()
    yield facade_mod.audit
    facade_mod.audit.bind(previous)


@pytest.fixture
def store(tmp_path):
    return R.ResolutionStore(path=str(tmp_path / "resolutions.jsonl"),
                             clock=lambda: CLOCK)


def _rec(**kwargs) -> R.ResolutionRecord:
    payload = {"scope": R.SCOPE_DESCRIPTOR, "rule": "DC-2",
               "asset_id": "engineering-test-delivery",
               "reason": "通用测试流程规范，不含敏感数据；原 confidential 系自动推断误伤",
               "decided_by": "Owner", "decided_at": CLOCK}
    payload.update(kwargs)
    return R.ResolutionRecord(**payload)


# ════════════════════════════════════════════════════════════
#  1. 记录与校验
# ════════════════════════════════════════════════════════════


class TestResolutionRecord:
    def test_valid_record_passes(self):
        assert _rec().validate() == []

    @pytest.mark.parametrize("patch,needle", [
        ({"scope": "nope"}, "scope"),
        ({"verdict": "maybe"}, "verdict"),
        ({"rule": ""}, "rule"),
        ({"rule": "X" * 100}, "过长"),
        ({"decided_by": ""}, "decided_by"),
    ])
    def test_invalid_records_are_rejected(self, patch, needle):
        reasons = _rec(**patch).validate()
        assert reasons and any(needle in r for r in reasons)

    def test_case_scope_requires_case_id(self):
        assert any("case_id" in r for r in
                   _rec(scope=R.SCOPE_CASE, asset_id="").validate())

    def test_descriptor_scope_requires_subject(self):
        assert any("asset_id" in r for r in
                   _rec(asset_id="", capability_id="").validate())

    def test_require_valid_raises(self):
        with pytest.raises(R.ResolutionError):
            _rec(rule="").require_valid()

    def test_basis_is_human_readable(self):
        text = _rec(written_value="internal").basis()
        assert "Owner" in text and "DC-2" in text
        assert "internal" in text and "误伤" in text

    def test_revoked_basis_is_marked(self):
        assert _rec(revoked=True).basis().startswith("[已撤销]")

    def test_round_trip(self):
        rec = _rec(written_value="internal", evidence=["证据 A", "证据 B"])
        assert R.ResolutionRecord.from_dict(rec.to_dict()).to_dict() == \
            rec.to_dict()

    def test_applies_only_when_accepted_and_not_revoked(self):
        assert _rec().applies is True
        assert _rec(verdict=R.VERDICT_DEFERRED).applies is False
        assert _rec(verdict=R.VERDICT_REJECTED).applies is False
        assert _rec(revoked=True).applies is False


# ════════════════════════════════════════════════════════════
#  2. 台账读写
# ════════════════════════════════════════════════════════════


class TestResolutionStore:
    def test_record_and_lookup(self, store):
        store.record(_rec(written_value="internal"))
        assert store.is_resolved(R.SCOPE_DESCRIPTOR,
                                 rule="DC-2",
                                 asset_id="engineering-test-delivery") is True
        assert store.lookup(R.SCOPE_DESCRIPTOR, rule="DC-2",
                            asset_id="engineering-test-delivery").written_value == \
            "internal"

    def test_unknown_triple_is_not_resolved(self, store):
        store.record(_rec())
        assert store.is_resolved(R.SCOPE_DESCRIPTOR, rule="DC-3",
                                 asset_id="engineering-test-delivery") is False
        assert store.is_resolved(R.SCOPE_DESCRIPTOR, rule="DC-2",
                                 asset_id="someone-else") is False

    def test_jsonl_is_append_only_and_last_wins(self, store):
        store.record(_rec(verdict=R.VERDICT_DEFERRED))
        store.record(_rec(written_value="internal"))
        assert len(store.rows()) == 2                 # 两条都在（历史不删）
        assert store.is_resolved(R.SCOPE_DESCRIPTOR, rule="DC-2",
                                 asset_id="engineering-test-delivery") is True

    def test_deferred_is_recorded_but_not_resolved(self, store):
        """『待补证据』不是裁定完成 —— 如实标注，规则**仍要报**"""
        store.record(_rec(verdict=R.VERDICT_DEFERRED))
        assert store.is_resolved(R.SCOPE_DESCRIPTOR, rule="DC-2",
                                 asset_id="engineering-test-delivery") is False
        assert store.summary()["deferred_or_rejected"] == 1

    def test_revoke_returns_to_unresolved(self, store):
        store.record(_rec())
        store.revoke(scope=R.SCOPE_DESCRIPTOR, rule="DC-2",
                     asset_id="engineering-test-delivery", revoked_by="Owner",
                     reason="复核发现新证据", now=CLOCK + 10)
        assert store.is_resolved(R.SCOPE_DESCRIPTOR, rule="DC-2",
                                 asset_id="engineering-test-delivery") is False
        assert store.summary()["revoked"] == 1
        assert len(store.rows()) == 2                 # 撤销是**追加**，不是删除

    def test_basis_for_and_by_subject(self, store):
        store.record(_rec())
        assert "Owner" in store.basis_for(R.SCOPE_DESCRIPTOR, rule="DC-2",
                                          asset_id="engineering-test-delivery")
        assert store.basis_for(R.SCOPE_DESCRIPTOR, rule="DC-9",
                               asset_id="engineering-test-delivery") == ""
        assert [r.rule for r in
                store.by_subject("engineering-test-delivery")] == ["DC-2"]

    def test_summary_counts(self, store):
        store.record(_rec())
        store.record(_rec(rule="PRV-5", asset_id="code-observability",
                          written_value="unknown"))
        summary = store.summary()
        assert summary["active"] == 2
        assert summary["by_scope"] == {R.SCOPE_DESCRIPTOR: 2}
        assert summary["by_rule"] == {"DC-2": 1, "PRV-5": 1}

    def test_corrupt_lines_are_skipped(self, store, tmp_path):
        store.record(_rec())
        path = tmp_path / "resolutions.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("{ not json\n")
        assert len(store.records()) == 1

    def test_missing_file_reads_empty(self, store):
        assert store.rows() == [] and store.summary()["active"] == 0

    def test_invalid_record_never_lands(self, store, tmp_path):
        with pytest.raises(R.ResolutionError):
            store.record(_rec(rule=""))
        assert not (tmp_path / "resolutions.jsonl").exists()


# ════════════════════════════════════════════════════════════
#  3. 审计联动（resolution.record）
# ════════════════════════════════════════════════════════════


class TestAuditLinkage:
    def test_record_writes_audit(self, store):
        store.record(_rec(written_value="internal"))
        actions = [e.action for e in facade_mod.audit.recent(limit=20)]
        assert R.AUDIT_ACTION_RESOLUTION in actions

    def test_audit_payload_carries_rule_and_reason(self, store):
        store.record(_rec(written_value="internal"))
        entries = [e for e in facade_mod.audit.recent(limit=20)
                   if e.action == R.AUDIT_ACTION_RESOLUTION]
        assert entries, "裁定必须入审计"
        payload = json.dumps(entries[-1].payload, ensure_ascii=False, default=str)
        assert "DC-2" in payload and "Owner" in payload

    def test_audit_can_be_disabled_explicitly(self, store):
        store.record(_rec(), audit=False)
        actions = [e.action for e in facade_mod.audit.recent(limit=20)]
        assert R.AUDIT_ACTION_RESOLUTION not in actions

    def test_revoke_is_audited_as_revoked(self, store):
        store.record(_rec(), audit=False)
        store.revoke(scope=R.SCOPE_DESCRIPTOR, rule="DC-2",
                     asset_id="engineering-test-delivery", revoked_by="Owner",
                     reason="新证据", now=CLOCK + 5)
        entries = [e for e in facade_mod.audit.recent(limit=20)
                   if e.action == R.AUDIT_ACTION_RESOLUTION]
        assert entries, "撤销也必须入审计"
        # AuditEntry.payload 是信封（{payload: {...}, status: ...}），取内层正文
        body = entries[-1].payload.get("payload", entries[-1].payload)
        assert body.get("revoked") is True


# ════════════════════════════════════════════════════════════
#  4. needs 跳过（**只跳过，不放宽**）
# ════════════════════════════════════════════════════════════


def _need(asset_id: str, scope: str, rule: str) -> dict:
    return {"asset_id": asset_id, "capability_id": f"cp.skill.{asset_id}",
            "scope": scope, "rule": rule, "reason": "自动规则命中",
            "disposal": "人工复核"}


class TestNeedsSkip:
    def test_no_store_is_byte_identical(self):
        needs = {"needs_review": [_need("a", "data_class", "DC-2")],
                 "needs_undo_hint": []}
        remaining, resolved = R.apply_resolutions(needs["needs_review"], None)
        assert remaining == needs["needs_review"] and resolved == []

    def test_resolved_item_moves_out_with_basis(self, store):
        store.record(_rec(asset_id="a", written_value="internal"))
        remaining, resolved = R.apply_resolutions(
            [_need("a", "data_class", "DC-2"), _need("b", "data_class", "DC-2")],
            store)
        assert [r["asset_id"] for r in remaining] == ["b"]
        assert len(resolved) == 1
        assert "Owner" in resolved[0]["basis"]
        assert resolved[0]["resolution"]["rule"] == "DC-2"

    def test_unresolved_rules_still_reported(self, store):
        """反面纪律：登记了 DC-2 不等于 PRV-5 也不用报了"""
        store.record(_rec(asset_id="a"))
        remaining, _ = R.apply_resolutions(
            [_need("a", "provenance", "PRV-5")], store)
        assert [r["rule"] for r in remaining] == ["PRV-5"]

    def test_deferred_decision_does_not_silence_rule(self, store):
        store.record(_rec(asset_id="a", verdict=R.VERDICT_DEFERRED))
        remaining, resolved = R.apply_resolutions([_need("a", "data_class",
                                                         "DC-2")], store)
        assert len(remaining) == 1 and resolved == []

    def test_enrich_needs_shape(self, store):
        store.record(_rec(asset_id="a", written_value="internal"))
        out = R.enrich_needs({"needs_review": [_need("a", "data_class", "DC-2")],
                              "needs_undo_hint": []}, store)
        assert out["needs_review"] == []
        assert len(out["needs_review_resolved"]) == 1
        assert out["resolution_skipped"] == 1
        assert out["resolution_summary"]["active"] == 1
        assert "不放宽" in out["resolution_note"]

    def test_enrich_without_store_keeps_lists(self):
        needs = {"needs_review": [_need("a", "data_class", "DC-2")],
                 "needs_undo_hint": []}
        out = R.enrich_needs(needs, None)
        assert out["needs_review"] == needs["needs_review"]
        assert out["needs_review_resolved"] == []
        assert out["resolution_skipped"] == 0


# ════════════════════════════════════════════════════════════
#  5. 与 backfill 的接入（含"未接入 ⇒ 零行为变化"）
# ════════════════════════════════════════════════════════════


class TestBackfillIntegration:
    def test_collect_needs_without_store_is_unchanged(self):
        plan = [{"asset_id": "a", "capability_id": "cp.skill.a",
                 "flags": {"needs_review": [
                     {"kind": "NEEDS_REVIEW", "scope": "data_class",
                      "rule": "DC-2", "reason": "r"}],
                     "needs_undo_hint": False},
                 "governance": {"rationale": ""}}]
        needs = bf.collect_needs(plan)
        assert len(needs["needs_review"]) == 1
        assert needs["needs_review_resolved"] == []
        assert needs["resolution_skipped"] == 0

    def test_collect_needs_with_store_skips_resolved(self, store):
        store.record(_rec(asset_id="a", written_value="internal"))
        plan = [{"asset_id": "a", "capability_id": "cp.skill.a",
                 "flags": {"needs_review": [
                     {"kind": "NEEDS_REVIEW", "scope": "data_class",
                      "rule": "DC-2", "reason": "r"}],
                     "needs_undo_hint": False},
                 "governance": {"rationale": ""}}]
        needs = bf.collect_needs(plan, resolutions=store)
        assert needs["needs_review"] == []
        assert len(needs["needs_review_resolved"]) == 1

    def test_needs_markdown_shows_basis(self, store):
        store.record(_rec(asset_id="a", written_value="internal"))
        plan = [{"asset_id": "a", "capability_id": "cp.skill.a",
                 "flags": {"needs_review": [
                     {"kind": "NEEDS_REVIEW", "scope": "data_class",
                      "rule": "DC-2", "reason": "r"}],
                     "needs_undo_hint": False},
                 "governance": {"rationale": ""}}]
        md = bf.needs_markdown(bf.collect_needs(plan, resolutions=store))
        assert "已裁定项及其依据" in md
        assert "Owner" in md and "DC-2" in md

    def test_broken_store_degrades_to_unfiltered(self):
        """台账故障**不得让清单消失**（照旧产出，只是没过滤）"""
        class _Broken:
            def lookup(self, *a, **k):
                raise RuntimeError("台账损坏")

        plan = [{"asset_id": "a", "capability_id": "cp.skill.a",
                 "flags": {"needs_review": [
                     {"kind": "NEEDS_REVIEW", "scope": "data_class",
                      "rule": "DC-2", "reason": "r"}],
                     "needs_undo_hint": False},
                 "governance": {"rationale": ""}}]
        needs = bf.collect_needs(plan, resolutions=_Broken())
        assert len(needs["needs_review"]) == 1


# ════════════════════════════════════════════════════════════
#  6. 复核表接入
# ════════════════════════════════════════════════════════════


class TestResolutionSheet:
    def test_sheet_lists_decisions_and_evidence(self, store):
        store.record(_rec(case_id="", asset_id="a",
                          evidence=["docs/zh/裁定记录.md §3.2"]))
        sheet = R.resolution_sheet(store)
        assert "已裁定项及其依据" in sheet
        assert "docs/zh/裁定记录.md §3.2" in sheet
        assert "resolution.record" in sheet

    def test_sheet_empty_without_store(self, store):
        assert R.resolution_sheet(None) == ""

    def test_resolution_basis_for_case(self, store):
        store.record(R.ResolutionRecord(
            scope=R.SCOPE_CASE, rule="M5", case_id="case-000",
            reason="行为等价", decided_by="Owner", decided_at=CLOCK))
        assert "行为等价" in R.resolution_basis(store, case_id="case-000")
        assert R.resolution_basis(store, case_id="case-999") == ""

    def test_digest_is_deterministic(self):
        records = [_rec(), _rec(rule="PRV-5", asset_id="b")]
        assert R.resolution_digest(records) == R.resolution_digest(records)
        assert R.resolution_digest(records).startswith("res-")
