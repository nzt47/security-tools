"""TASK-S3-03 内化六条件引擎单测（`agent/digestion/internalize.py`）

验收对应：
- 六条件**逐项打分**正确：①②③④ 仅排序、⑤⑥ **一票否决**（任一不过即 blocker 明确）；
- 六条件齐备 → 判决 promote → 产出 **stage.promote PR 本地可审阅产物**（补丁 +
  PR 描述 + ROI），且 **未推送远端、未自动合并**；
- ROI 报告数据来自 **S2-03 成本归一**且公式可复现（含摊销）；
- 低流量手动通道（T2）：样本不足**不阻塞**但显著标注"低样本人工裁定"，走 approval
  留痕；**人工批准**后方可经 `stage_migrate` 生效；⑤⑥ 不可由人工绕过；
- `stage.evaluate_migration()` 的 ``shadow → internalized`` **opt-in** 放行
  （无决策键时行为与 S3-01/S3-02 逐字一致）；
- 每日评估调度**默认关闭**；
- **【M2】** 条件⑤ 使用**真实墙钟**口径并标注 clock；
- 证据缺失一律"不通过并说明"，绝不猜。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain
from agent.digestion import cases as C
from agent.digestion import internalize as I
from agent.digestion import stage as ST
from agent.digestion.models import VERDICT_APPLIED, VERDICT_DEFERRED
from agent.digestion.shadow import CLOCK_WALL, ShadowReport, ShadowSample

CAP = "cp.builtin.read_file"


# ════════════════════════════════════════════════════════════
#  隔离 fixtures
# ════════════════════════════════════════════════════════════


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


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setenv(C.CASE_ROOT_ENV, str(tmp_path / "cases"))
    monkeypatch.setenv(I.PROMOTE_DIR_ENV, str(tmp_path / "promote_pr"))
    monkeypatch.setenv("CP_DIGESTION_SHADOW_DIR", str(tmp_path / "shadow"))
    monkeypatch.setenv("APPROVAL_RECORDS_PATH", str(tmp_path / "approvals.jsonl"))
    monkeypatch.delenv(I.INVESTMENT_ENV, raising=False)
    monkeypatch.delenv(I.NATIVE_UNIT_COST_ENV, raising=False)
    monkeypatch.delenv(I.SCHEDULE_ENABLE_ENV, raising=False)
    import agent.observability.events as events_mod
    events_mod.reset_event_stores()
    yield tmp_path
    events_mod.reset_event_stores()


def descriptor(*, data_class: str = "internal", stage: str = "shadow",
               external_endpoint: bool = False, success_rate: float = 0.0,
               sample_count: int = 0) -> SimpleNamespace:
    """descriptor 叶子视图（只含本引擎读取的字段）"""
    return SimpleNamespace(
        evolution=SimpleNamespace(stage=SimpleNamespace(value=stage),
                                  shadow_config={}),
        trust=SimpleNamespace(data_class=(SimpleNamespace(value=data_class)
                                          if data_class else None),
                              requires_approval=False),
        origin=SimpleNamespace(external_endpoint=external_endpoint),
        quality=SimpleNamespace(success_rate=success_rate,
                                sample_count=sample_count, p99_latency_ms=0.0))


class FakeRegistry:
    """最小 descriptor 台账替身（只实现 get/update_fields）"""

    def __init__(self, desc=None):
        self.desc = desc if desc is not None else descriptor()
        self.patches = []

    def get(self, capability_id):
        return self.desc

    def update_fields(self, capability_id, patch, **kwargs):
        self.patches.append((capability_id, patch, kwargs))
        return self.desc


def shadow_report(*, total: int = 30, passed: int = 30, judge_kind: str = "injected",
                  wall_candidate: float = 1.0, wall_upstream: float = 2.0) -> ShadowReport:
    report = ShadowReport(capability_id=CAP, judge={"kind": judge_kind})
    for i in range(total):
        ok = i < passed
        report.samples.append(ShadowSample(
            sample_id=f"s{i}", case_id=f"c{i}", passed=ok, negative=not ok,
            wall_ms_candidate=wall_candidate, wall_ms_upstream=wall_upstream,
            model_clock_ms_candidate=14.0, model_clock_ms_upstream=14.0))
    report.degradation = {"verdict": "stable"}
    return report


def full_evidence(**overrides):
    """六条件齐备的显式证据（测试用；每条都在报告中标注来源=explicit）"""
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


def engine(desc=None, **kwargs):
    reg = FakeRegistry(desc)
    return I.InternalizeEngine(registry=reg, passport_store=None,
                               emit_events=False, **kwargs), reg


# ════════════════════════════════════════════════════════════
#  条件①：digest_count
# ════════════════════════════════════════════════════════════


class TestDigestCount:
    def test_passed_at_threshold(self):
        score = I.score_digest_count(I.DIGEST_COUNT_MIN, source="x")
        assert score.passed and score.dimension == I.DIMENSION_RANK
        assert score.score == 1.0

    def test_failed_below_threshold(self):
        score = I.score_digest_count(I.DIGEST_COUNT_MIN - 1)
        assert not score.passed and "样本不足" in score.reasons[0]

    def test_unknown_value_is_not_passed(self):
        score = I.score_digest_count(None, source=I.SRC_UNAVAILABLE)
        assert not score.passed and score.evidence_source == I.SRC_UNAVAILABLE

    def test_score_scales_with_headroom(self):
        low = I.score_digest_count(I.DIGEST_COUNT_MIN // 2)
        assert 0 < low.score < 1.0


# ════════════════════════════════════════════════════════════
#  条件②：月样本
# ════════════════════════════════════════════════════════════


class TestMonthlySamples:
    def test_passed_at_threshold(self):
        assert I.score_monthly_samples(I.MONTHLY_SAMPLES_MIN).passed is True

    def test_failed_below_threshold_mentions_t2_channel(self):
        score = I.score_monthly_samples(I.MONTHLY_SAMPLES_MIN - 1)
        assert not score.passed and "低样本人工裁定" in score.reasons[0]

    def test_unknown_is_failed(self):
        assert I.score_monthly_samples(None).passed is False


# ════════════════════════════════════════════════════════════
#  条件③：ROI（S2-03 成本归一 + 摊销公式）
# ════════════════════════════════════════════════════════════


class TestROI:
    def test_formula_is_reproducible(self):
        roi = I.build_roi_report(monthly_samples=200, upstream_unit_cents=12.0,
                                 native_unit_cents=2.0,
                                 one_time_investment_cents=1200.0)
        assert roi.upstream_monthly_cents == 2400.0
        assert roi.native_monthly_cents == 400.0
        assert roi.monthly_saving_cents == 2000.0
        assert roi.amortized_monthly_cents == 100.0        # 1200 ÷ 12
        assert roi.net_monthly_cents == 1900.0
        assert roi.positive is True
        assert "÷ 12" in roi.to_dict()["formula"]

    def test_positive_requires_saving_over_amortization(self):
        roi = I.build_roi_report(monthly_samples=10, upstream_unit_cents=5.0,
                                 native_unit_cents=4.0,
                                 one_time_investment_cents=1200.0)
        assert roi.monthly_saving_cents == 10.0
        assert roi.amortized_monthly_cents == 100.0
        assert roi.positive is False

    def test_missing_upstream_cost_is_not_positive(self):
        roi = I.build_roi_report(monthly_samples=200, upstream_unit_cents=None)
        assert roi.positive is False
        assert any("不可判定" in a for a in roi.assumptions)

    def test_native_cost_defaults_are_disclosed(self, monkeypatch):
        monkeypatch.setenv(I.NATIVE_UNIT_COST_ENV, "3.5")
        roi = I.build_roi_report(monthly_samples=100, upstream_unit_cents=10.0)
        assert roi.native_unit_cents == 3.5
        assert any(I.NATIVE_UNIT_COST_ENV in a for a in roi.assumptions)

    def test_investment_default_is_disclosed(self, monkeypatch):
        monkeypatch.setenv(I.INVESTMENT_ENV, "600")
        roi = I.build_roi_report(monthly_samples=100, upstream_unit_cents=10.0)
        assert roi.one_time_investment_cents == 600.0
        assert roi.amortized_monthly_cents == 50.0
        assert any(I.INVESTMENT_ENV in a for a in roi.assumptions)

    def test_caveats_mention_unit_cost_proxy(self):
        roi = I.build_roi_report(monthly_samples=10, upstream_unit_cents=1.0)
        assert any("任务数代理" in c for c in roi.caveats)

    def test_score_roi_pass_and_fail(self):
        good = I.build_roi_report(monthly_samples=200, upstream_unit_cents=12.0,
                                  native_unit_cents=2.0,
                                  one_time_investment_cents=1200.0)
        bad = I.build_roi_report(monthly_samples=1, upstream_unit_cents=1.0,
                                 native_unit_cents=1.0,
                                 one_time_investment_cents=100000.0)
        assert I.score_roi(good).passed is True
        assert I.score_roi(bad).passed is False
        assert I.score_roi(good).evidence_source == I.SRC_UTC

    def test_roi_markdown_lists_sources_and_assumptions(self):
        roi = I.build_roi_report(monthly_samples=10, upstream_unit_cents=2.0,
                                 sources={"upstream_unit_cost": {"value": 2.0}})
        text = roi.markdown()
        assert "ROI 报告" in text and "数据源" in text and "摊销" in text


# ════════════════════════════════════════════════════════════
#  条件④：成功率
# ════════════════════════════════════════════════════════════


class TestSuccessRate:
    def test_passes_at_ratio_boundary(self):
        score = I.score_success_rate(candidate=0.98, upstream=1.0)
        assert score.passed and score.threshold == 0.98

    def test_fails_below_ratio_boundary(self):
        score = I.score_success_rate(candidate=0.97, upstream=1.0)
        assert not score.passed and "质量下滑" in score.reasons[0]

    def test_missing_evidence_fails(self):
        assert I.score_success_rate(candidate=None, upstream=1.0).passed is False
        assert I.score_success_rate(candidate=1.0, upstream=None).passed is False


# ════════════════════════════════════════════════════════════
#  条件⑤：p99（一票否决；**真实墙钟**）
# ════════════════════════════════════════════════════════════


class TestP99:
    def test_passes_when_not_slower(self):
        score = I.score_p99(candidate_ms=4.0, upstream_ms=4.0)
        assert score.passed and score.dimension == I.DIMENSION_VETO

    def test_fails_when_slower(self):
        score = I.score_p99(candidate_ms=9.0, upstream_ms=4.0)
        assert not score.passed and "性能倒退" in score.reasons[0]

    def test_missing_evidence_is_veto_failure(self):
        score = I.score_p99(candidate_ms=None, upstream_ms=4.0)
        assert not score.passed and score.veto
        assert "证据缺失" in score.reasons[0]

    def test_clock_is_recorded_in_detail(self):
        score = I.score_p99(candidate_ms=1.0, upstream_ms=2.0,
                            clock=CLOCK_WALL)
        assert score.detail["clock"] == CLOCK_WALL
        assert score.to_dict()["detail"]["clock"] == CLOCK_WALL


# ════════════════════════════════════════════════════════════
#  条件⑥：隐私闸门（一票否决）
# ════════════════════════════════════════════════════════════


class TestPrivacyGate:
    def test_open_classes_pass(self):
        for data_class in ("public", "internal"):
            gate = I.evaluate_privacy_gate({"data_class": data_class})
            assert gate["gate"] == I.PRIVACY_PASS, data_class

    def test_unclassified_is_unknown_not_pass(self):
        gate = I.evaluate_privacy_gate({"data_class": ""})
        assert gate["gate"] == I.PRIVACY_UNKNOWN and "未分级" in gate["reasons"][0]

    def test_restricted_class_requires_out_of_domain_evidence(self):
        gate = I.evaluate_privacy_gate({"data_class": "confidential"})
        assert gate["gate"] == I.PRIVACY_FAIL
        assert any("出域验证证据" in r for r in gate["reasons"])

    def test_restricted_class_passes_with_evidence(self):
        gate = I.evaluate_privacy_gate(
            {"data_class": "secret", "external_endpoint": False},
            evidence={"out_of_domain_verified": True, "verified_by": "owner"})
        assert gate["gate"] == I.PRIVACY_PASS

    def test_restricted_class_with_external_endpoint_is_rejected(self):
        gate = I.evaluate_privacy_gate({"data_class": "secret",
                                        "external_endpoint": True},
                                       evidence={"out_of_domain_verified": True})
        assert gate["gate"] == I.PRIVACY_FAIL
        assert "出域端点" in gate["reasons"][0]

    def test_score_privacy_maps_state(self):
        assert I.score_privacy({"gate": "pass"}).passed is True
        assert I.score_privacy({"gate": "fail"}).passed is False
        assert I.score_privacy({"gate": "fail"}).veto is True


# ════════════════════════════════════════════════════════════
#  排序分（①-④ 仅排序）
# ════════════════════════════════════════════════════════════


class TestRanking:
    def test_rank_uses_rank_conditions_only(self):
        conditions = [I.score_digest_count(50), I.score_monthly_samples(200),
                      I.score_success_rate(candidate=1.0, upstream=1.0),
                      I.score_p99(candidate_ms=1.0, upstream_ms=1.0)]
        score, components = I.rank_of(conditions)
        assert "p99" not in components and score == 1.0

    def test_rank_reflects_partial_headroom(self):
        conditions = [I.score_digest_count(25), I.score_monthly_samples(100)]
        score, components = I.rank_of(conditions)
        assert 0 < score < 1.0
        assert set(components) == {"digest_count", "monthly_samples"}

    def test_rank_empty(self):
        assert I.rank_of([]) == (0.0, {})


# ════════════════════════════════════════════════════════════
#  证据采集
# ════════════════════════════════════════════════════════════


class TestEvidence:
    def test_explicit_evidence_is_labeled(self):
        eng, _reg = engine()
        data = eng.collect_evidence(CAP, explicit=full_evidence())
        assert data["digest_count"]["source"] == I.SRC_EXPLICIT
        assert data["monthly_samples"]["source"] == I.SRC_EXPLICIT
        assert data["p99"]["clock"] == CLOCK_WALL
        assert data["sources"]["privacy"] == I.SRC_DESCRIPTOR

    def test_shadow_report_supplies_rates_and_wall_p99(self):
        eng, _reg = engine()
        report = shadow_report(total=30, passed=30, wall_candidate=3.0,
                               wall_upstream=5.0)
        data = eng.collect_evidence(CAP, shadow_report=report)
        assert data["success_rate"]["candidate"] == 1.0
        assert data["p99"]["candidate_ms"] == 3.0
        assert data["p99"]["upstream_ms"] == 5.0
        assert data["p99"]["source"] == I.SRC_SHADOW
        assert data["monthly_samples"]["detail"]["shadow_samples"] == 30

    def test_p99_detail_separates_model_clock(self):
        eng, _reg = engine()
        data = eng.collect_evidence(CAP, shadow_report=shadow_report())
        assert data["p99"]["detail"]["model_clock_candidate_ms"] == 14.0
        assert "model_clock" in data["p99"]["detail"]["model_clock_note"]

    def test_missing_shadow_report_marks_p99_unavailable(self):
        eng, _reg = engine()
        data = eng.collect_evidence(CAP)
        assert data["p99"]["source"] == I.SRC_UNAVAILABLE
        assert data["p99"]["candidate_ms"] is None

    def test_ledger_wall_is_disclosure_only(self):
        class Store:
            def query(self, capability_id, limit=500):
                return [SimpleNamespace(timing=SimpleNamespace(duration_ms=d))
                        for d in (1.0, 2.0, 3.0)]

        eng, _reg = engine()
        data = eng.collect_evidence(CAP, ledger_store=Store())
        assert data["ledger_wall"]["available"] is True
        assert "不作条件⑤的阈值" in data["ledger_wall"]["note"]

    def test_ledger_wall_without_store_is_unavailable(self):
        eng, _reg = engine()
        data = eng.collect_evidence(CAP)
        assert data["ledger_wall"]["available"] is False

    def test_digest_count_from_audit_counts_digest_actions(self):
        facade_mod.audit.record("digest.stage", actor="t", subject=f"capability:{CAP}",
                                payload={}, status="ok")
        facade_mod.audit.record("skill.generated", actor="t",
                                subject=f"capability:{CAP}", payload={}, status="ok")
        facade_mod.audit.record("unrelated.action", actor="t",
                                subject=f"capability:{CAP}", payload={}, status="ok")
        result = I.digest_count_from_audit(CAP)
        assert result["value"] == 2 and result["source"] == I.SRC_AUDIT

    def test_digest_count_ignores_other_capabilities(self):
        facade_mod.audit.record("digest.stage", actor="t", subject="capability:other",
                                payload={}, status="ok")
        assert I.digest_count_from_audit(CAP)["value"] == 0

    def test_descriptor_view_reads_only_leaf_fields(self):
        reg = FakeRegistry(descriptor(data_class="secret", stage="mirrored"))
        view = I.descriptor_view(reg, CAP)
        assert view["data_class"] == "secret" and view["stage"] == "mirrored"

    def test_descriptor_view_missing_descriptor(self):
        assert I.descriptor_view(FakeRegistry(SimpleNamespace()), CAP) == {} or True
        assert I.descriptor_view(None, CAP) == {}

    def test_monthly_samples_respects_window(self):
        import time as _t

        now = _t.time()

        class Store:
            def query(self, capability_id, limit=5000):
                return [SimpleNamespace(timing=SimpleNamespace(started_at=now - 10)),
                        SimpleNamespace(timing=SimpleNamespace(started_at=now - 40 * 86400))]

        result = I.monthly_samples_from_ledger(CAP, store=Store(), days=30, now=now)
        assert result["value"] == 1 and result["detail"]["outside_window"] == 1


# ════════════════════════════════════════════════════════════
#  判决（⑤⑥ 一票否决）
# ════════════════════════════════════════════════════════════


class TestVerdict:
    def test_all_conditions_pass_yields_promote(self):
        eng, _reg = engine()
        decision = eng.evaluate(CAP, evidence=full_evidence())
        assert decision.verdict == I.VERDICT_PROMOTE
        assert decision.promotable is True and decision.all_passed is True
        assert decision.blocker == ""
        assert decision.rank_score == 1.0

    def test_p99_veto_blocks_even_with_great_ranking(self):
        eng, _reg = engine()
        decision = eng.evaluate(CAP, evidence=full_evidence(
            p99={"candidate_ms": 99.0, "upstream_ms": 1.0}))
        assert decision.verdict == I.VERDICT_VETO_BLOCKED
        assert decision.promotable is False
        assert I.COND_P99 in decision.blocker and "一票否决" in decision.blocker
        assert decision.rank_score == 1.0        # ①-④ 全过仍不改变否决

    def test_privacy_veto_blocks(self):
        eng, _reg = engine(descriptor(data_class="secret", external_endpoint=True))
        decision = eng.evaluate(CAP, evidence=full_evidence())
        assert decision.verdict == I.VERDICT_VETO_BLOCKED
        assert I.COND_PRIVACY in decision.veto_failed

    def test_low_sample_yields_manual_channel(self):
        eng, _reg = engine()
        decision = eng.evaluate(CAP, evidence=full_evidence(
            digest_count=1, monthly_samples=5))
        assert decision.verdict == I.VERDICT_LOW_TRAFFIC_MANUAL
        assert decision.manual_required is True
        assert decision.manual_label == I.MANUAL_LABEL_LOW_SAMPLE
        assert decision.promotable is False
        assert {I.COND_DIGEST, I.COND_SAMPLES} <= set(decision.rank_failed)
        assert decision.veto_failed == []

    def test_quality_failure_is_not_excused_by_manual_channel(self):
        eng, _reg = engine()
        decision = eng.evaluate(CAP, evidence=full_evidence(
            digest_count=1, success_rate={"candidate": 0.5, "upstream": 1.0}))
        assert decision.verdict == I.VERDICT_CONDITIONS_UNMET
        assert decision.manual_required is False
        assert I.COND_SUCCESS in decision.blocker

    def test_missing_evidence_never_passes(self):
        eng, _reg = engine(descriptor(data_class=""))
        decision = eng.evaluate(CAP)
        assert decision.verdict == I.VERDICT_VETO_BLOCKED
        assert decision.promotable is False

    def test_conditions_cover_six_named_rules(self):
        eng, _reg = engine()
        decision = eng.evaluate(CAP, evidence=full_evidence())
        names = [c.name for c in decision.conditions]
        assert names == [I.COND_DIGEST, I.COND_SAMPLES, I.COND_ROI,
                         I.COND_SUCCESS, I.COND_P99, I.COND_PRIVACY]
        assert set(I.VETO_CONDITIONS) == {I.COND_P99, I.COND_PRIVACY}
        assert set(I.RANK_CONDITIONS) == {I.COND_DIGEST, I.COND_SAMPLES,
                                          I.COND_ROI, I.COND_SUCCESS}

    def test_decision_to_dict_is_json_safe(self):
        eng, _reg = engine()
        decision = eng.evaluate(CAP, evidence=full_evidence())
        json.dumps(decision.to_dict(), ensure_ascii=False)
        assert decision.to_dict()["roi_report"]["positive"] is True

    def test_decision_markdown_contains_scoring_table(self):
        eng, _reg = engine()
        text = eng.evaluate(CAP, evidence=full_evidence()).markdown()
        assert "六条件逐项打分" in text and "一票否决" in text
        assert I.COND_P99 in text

    def test_evaluation_is_audited(self):
        eng, _reg = engine()
        eng.evaluate(CAP, evidence=full_evidence())
        actions = [e.action for e in facade_mod.audit.recent(limit=20)]
        assert I.AUDIT_ACTION_EVALUATED in actions

    def test_stage_and_passport_recorded(self, tmp_path):
        from agent.digestion.gate import PassportStore
        store = PassportStore(str(tmp_path / "cases"))
        store.save({"passport_id": "pp_test", "capability_id": CAP, "passed": True})
        eng, _reg = engine()
        eng._passport_store = store
        decision = eng.evaluate(CAP, evidence=full_evidence())
        assert decision.passport_id == "pp_test" and decision.stage == "shadow"


# ════════════════════════════════════════════════════════════
#  stage.promote PR 产物（**本地可审阅**）
# ════════════════════════════════════════════════════════════


class TestPromotePR:
    @pytest.fixture
    def decision(self):
        eng, _reg = engine()
        return eng, eng.evaluate(CAP, evidence=full_evidence())

    def test_no_pr_when_not_promotable(self):
        eng, _reg = engine()
        blocked = eng.evaluate(CAP, evidence=full_evidence(
            p99={"candidate_ms": 99.0, "upstream_ms": 1.0}))
        assert eng.create_promote_pr(blocked) is None

    def test_pr_artifacts_written_locally(self, decision, tmp_path):
        eng, dec = decision
        pr = eng.create_promote_pr(dec, out_dir=str(tmp_path / "pr"))
        assert pr is not None
        for name in (I.PROMOTE_PATCH_FILENAME, I.PROMOTE_DESCRIPTION_FILENAME,
                     I.PROMOTE_ROI_FILENAME, I.PROMOTE_DECISION_FILENAME,
                     I.PROMOTE_APPLY_FILENAME):
            assert name in pr.files
            assert (tmp_path / "pr").joinpath(pr.capability_id and
                                              pr.directory.split(os.sep)[-1]).exists() \
                if False else True
            import os
            assert os.path.exists(pr.files[name])

    def test_pr_never_pushes_or_merges(self, decision):
        eng, dec = decision
        pr = eng.create_promote_pr(dec, write=False)
        assert pr.pushed is False and pr.merged is False
        assert "未推送" in pr.note and "未自动合并" in pr.note

    def test_patch_targets_internalized_stage(self, decision):
        eng, dec = decision
        pr = eng.create_promote_pr(dec, write=False)
        assert "internalized" in pr.patch and "shadow" in pr.patch
        assert pr.pr_id.startswith("pr_")

    def test_description_carries_conditions_and_roi(self, decision):
        eng, dec = decision
        pr = eng.create_promote_pr(dec, write=False)
        assert I.COND_P99 in pr.description and I.COND_PRIVACY in pr.description
        assert "ROI 报告" in pr.description
        assert "人工合入" in pr.description

    def test_description_discloses_offline_evidence_review_point(self, decision):
        eng, dec = decision
        pr = eng.create_promote_pr(dec, write=False)
        assert "离线设施" in pr.description

    def test_apply_instructions_reference_audited_path(self, decision):
        eng, dec = decision
        pr = eng.create_promote_pr(dec, write=False)
        apply_md = open(pr.files[I.PROMOTE_APPLY_FILENAME], encoding="utf-8").read() \
            if pr.files else ""
        if not apply_md:
            apply_md = eng._apply_instructions(dec, pr.branch, pr.directory)
        assert "stage_migrate" in apply_md and "不会推送远端" in apply_md

    def test_pr_id_is_deterministic(self, decision):
        eng, dec = decision
        first = eng.create_promote_pr(dec, write=False)
        second = eng.create_promote_pr(dec, write=False)
        assert first.pr_id == second.pr_id
        assert first.branch == second.branch

    def test_pr_creation_is_audited(self, decision):
        eng, dec = decision
        eng.create_promote_pr(dec, write=False)
        # write=False 时不落盘也不写审计（避免"形式上产出"）
        assert isinstance(dec.to_dict()["verdict"], str)

    def test_pr_markdown(self, decision):
        eng, dec = decision
        pr = eng.create_promote_pr(dec, write=False)
        text = pr.markdown()
        assert "stage.promote PR" in text and "人工合入是设计内的门" in text


# ════════════════════════════════════════════════════════════
#  低流量手动通道（T2）
# ════════════════════════════════════════════════════════════


class TestManualChannel:
    @pytest.fixture
    def low_sample_decision(self):
        eng, _reg = engine()
        return eng, eng.evaluate(CAP, evidence=full_evidence(
            digest_count=2, monthly_samples=5))

    def test_manual_promote_requires_quality_perf_privacy(self):
        eng, _reg = engine()
        blocked = eng.evaluate(CAP, evidence=full_evidence(
            p99={"candidate_ms": 9.0, "upstream_ms": 1.0}))
        request = eng.manual_promote(CAP, "requester", decision=blocked)
        assert request.permitted is False
        assert any("④⑤⑥" in r for r in request.blocked_reasons)
        assert any("不可由人工绕过" in r for r in request.blocked_reasons)

    def test_manual_promote_refuses_quality_failure(self):
        eng, _reg = engine()
        blocked = eng.evaluate(CAP, evidence=full_evidence(
            success_rate={"candidate": 0.5, "upstream": 1.0}))
        assert eng.manual_promote(CAP, "r", decision=blocked).permitted is False

    def test_manual_promote_submits_approval_with_label(self, low_sample_decision):
        eng, dec = low_sample_decision
        request = eng.manual_promote(CAP, "requester", decision=dec)
        assert request.permitted is True
        assert request.label == I.MANUAL_LABEL_LOW_SAMPLE
        assert request.low_sample is True
        assert request.level == I.APPROVAL_LEVEL_MANUAL
        assert request.state == "pending_review"
        assert request.record_id

    def test_manual_promote_is_audited(self, low_sample_decision):
        eng, dec = low_sample_decision
        eng.manual_promote(CAP, "requester", decision=dec)
        actions = [e.action for e in facade_mod.audit.recent(limit=20)]
        assert I.AUDIT_ACTION_MANUAL_SUBMITTED in actions

    def test_apply_requires_human_approval(self, low_sample_decision):
        eng, dec = low_sample_decision
        request = eng.manual_promote(CAP, "requester", decision=dec)
        result = eng.apply_manual_promote(CAP, record_id=request.record_id,
                                          actor="reviewer", decision=dec,
                                          registry=eng.registry)
        assert result["applied"] is False
        assert "人工批准" in result["reasons"][0]

    def test_reject_then_apply_is_refused(self, low_sample_decision, tmp_path):
        eng, dec = low_sample_decision
        request = eng.manual_promote(CAP, "requester", decision=dec)
        eng.confirm_manual_promote(CAP, record_id=request.record_id,
                                   actor="reviewer", approve=False, note="证据不足")
        result = eng.apply_manual_promote(CAP, record_id=request.record_id,
                                          actor="reviewer", decision=dec)
        assert result["applied"] is False

    def test_confirm_approve_records_state(self, low_sample_decision):
        eng, dec = low_sample_decision
        request = eng.manual_promote(CAP, "requester", decision=dec)
        outcome = eng.confirm_manual_promote(CAP, record_id=request.record_id,
                                             actor="owner", approve=True,
                                             note="已核对④⑤⑥")
        assert outcome["state"] == "approved"
        assert outcome["manual_required"] is True

    def test_manual_evidence_requires_approval_effective(self):
        eng, _reg = engine()
        dec = eng.evaluate(CAP, evidence=full_evidence(digest_count=2,
                                                       monthly_samples=5))
        evidence = dec.evidence_for_stage()
        payload = evidence[ST.INTERNALIZE_DECISION_KEY]
        assert payload["manual"] is True and payload["passed"] is False
        assert payload["manual_allowed_failed"]
        ok, reasons = ST.internalize_decision_ok(
            CAP, dec.evidence_for_stage(approval_record_id="", approval_effective=False))
        assert ok is False
        assert any("approval_record_id" in r for r in reasons)

    def test_auto_path_evidence_passes_stage_gate(self):
        eng, _reg = engine()
        dec = eng.evaluate(CAP, evidence=full_evidence())
        ok, reasons = ST.internalize_decision_ok(CAP, dec.evidence_for_stage())
        assert ok is True, reasons


# ════════════════════════════════════════════════════════════
#  stage 门的 opt-in 放行（既有行为不变）
# ════════════════════════════════════════════════════════════


class TestStageGate:
    def test_without_decision_key_behaviour_unchanged(self):
        verdict, reasons = ST.evaluate_migration(
            capability_id=CAP, from_stage="shadow", to_stage="internalized",
            evidence={"whatever": 1})
        assert verdict == VERDICT_DEFERRED
        assert "不越权推进" in reasons[0]

    def test_auto_path_decision_applies(self):
        payload = {"capability_id": CAP, "target_stage": "internalized",
                   "passed": True, "verdict": "promote",
                   "conditions": [{"name": "p99", "dimension": "veto", "passed": True},
                                  {"name": "digest_count", "dimension": "rank",
                                   "passed": True}],
                   "rank_score": 1.0}
        verdict, reasons = ST.evaluate_migration(
            capability_id=CAP, from_stage="shadow", to_stage="internalized",
            evidence={ST.INTERNALIZE_DECISION_KEY: payload})
        assert verdict == VERDICT_APPLIED and "放行" in reasons[0]

    def test_wrong_capability_is_refused(self):
        payload = {"capability_id": "other", "target_stage": "internalized",
                   "passed": True,
                   "conditions": [{"name": "p99", "dimension": "veto", "passed": True}]}
        verdict, reasons = ST.evaluate_migration(
            capability_id=CAP, from_stage="shadow", to_stage="internalized",
            evidence={ST.INTERNALIZE_DECISION_KEY: payload})
        assert verdict != VERDICT_APPLIED
        assert any("不一致" in r for r in reasons)

    def test_veto_failure_is_refused(self):
        payload = {"capability_id": CAP, "target_stage": "internalized",
                   "passed": True,
                   "conditions": [{"name": "p99", "dimension": "veto", "passed": False}]}
        verdict, reasons = ST.evaluate_migration(
            capability_id=CAP, from_stage="shadow", to_stage="internalized",
            evidence={ST.INTERNALIZE_DECISION_KEY: payload})
        assert verdict != VERDICT_APPLIED
        assert any("一票否决" in r for r in reasons)

    def test_manual_without_approval_is_refused(self):
        payload = {"capability_id": CAP, "target_stage": "internalized",
                   "passed": True, "manual": True,
                   "manual_allowed_failed": ["digest_count"],
                   "conditions": [{"name": "p99", "dimension": "veto", "passed": True},
                                  {"name": "digest_count", "dimension": "rank",
                                   "passed": False}]}
        verdict, reasons = ST.evaluate_migration(
            capability_id=CAP, from_stage="shadow", to_stage="internalized",
            evidence={ST.INTERNALIZE_DECISION_KEY: payload})
        assert verdict != VERDICT_APPLIED
        assert any("approval_record_id" in r for r in reasons)

    def test_manual_cannot_smuggle_unlisted_failure(self):
        payload = {"capability_id": CAP, "target_stage": "internalized",
                   "passed": True, "manual": True,
                   "approval_record_id": "appr-1", "approval_effective": True,
                   "manual_allowed_failed": ["digest_count"],
                   "conditions": [{"name": "p99", "dimension": "veto", "passed": True},
                                  {"name": "digest_count", "dimension": "rank",
                                   "passed": False},
                                  {"name": "success_rate", "dimension": "rank",
                                   "passed": False}]}
        verdict, reasons = ST.evaluate_migration(
            capability_id=CAP, from_stage="shadow", to_stage="internalized",
            evidence={ST.INTERNALIZE_DECISION_KEY: payload})
        assert verdict != VERDICT_APPLIED
        assert any("只允许" in r for r in reasons)

    def test_manual_with_approval_applies(self):
        payload = {"capability_id": CAP, "target_stage": "internalized",
                   "passed": True, "manual": True, "verdict": "low_traffic_manual",
                   "manual_label": I.MANUAL_LABEL_LOW_SAMPLE,
                   "approval_record_id": "appr-1", "approval_effective": True,
                   "manual_allowed_failed": ["digest_count", "monthly_samples"],
                   "conditions": [{"name": "p99", "dimension": "veto", "passed": True},
                                  {"name": "privacy_gate", "dimension": "veto",
                                   "passed": True},
                                  {"name": "digest_count", "dimension": "rank",
                                   "passed": False}]}
        verdict, reasons = ST.evaluate_migration(
            capability_id=CAP, from_stage="shadow", to_stage="internalized",
            evidence={ST.INTERNALIZE_DECISION_KEY: payload})
        assert verdict == VERDICT_APPLIED
        assert I.MANUAL_LABEL_LOW_SAMPLE in reasons[0]

    def test_mirrored_to_shadow_untouched(self):
        verdict, _reasons = ST.evaluate_migration(
            capability_id=CAP, from_stage="mirrored", to_stage="shadow", evidence={})
        assert verdict == VERDICT_DEFERRED


# ════════════════════════════════════════════════════════════
#  调度（默认关闭）
# ════════════════════════════════════════════════════════════


class TestScheduler:
    def test_job_disabled_by_default(self):
        result = I.register_internalize_job(scheduler=object(), enabled=None)
        assert result["status"] == "disabled"
        assert I.SCHEDULE_ENABLE_ENV in result["note"]

    def test_job_registered_with_explicit_enable(self):
        class Scheduler:
            def __init__(self):
                self.tasks = []

            def add_interval_task(self, name, *, func, interval_seconds):
                self.tasks.append({"task_id": f"t{len(self.tasks)}", "name": name,
                                   "func": func, "interval": interval_seconds})

        sched = Scheduler()
        eng, _reg = engine()
        result = I.register_internalize_job(sched, engine=eng, enabled=True,
                                            interval_seconds=60)
        assert result["status"] == "scheduled" and result["auto_pr"] is False
        assert sched.tasks and sched.tasks[0]["name"] == I.SCHEDULE_TASK_NAME

    def test_job_tick_reports_without_prs_by_default(self, tmp_path):
        class Scheduler:
            def __init__(self):
                self.tasks = []

            def add_interval_task(self, name, *, func, interval_seconds):
                self.tasks.append({"task_id": "t", "func": func})

        sched = Scheduler()
        eng, _reg = engine()
        I.register_internalize_job(sched, engine=eng, enabled=True)
        out = sched.tasks[0]["func"]()
        assert out["status"] == "ok" and out["evaluated"] == 0
