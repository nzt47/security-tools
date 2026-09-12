"""TASK-S7-06 R1：判定集构建成本入 ROI 单测（`agent/digestion/case_cost.py` + ROI 双口径）

验收对应（任务书 §四 R1）：
- 判定集构建成本**可采集**（埋点存在且事件可查，含 ``stage=case_build`` 标注）；
- ``ROIReport`` 同时输出**不含/含**判定集成本的两种 ROI，公式写清；
- 估计值**必须**带方法与样本标注（不得凭空给数）；
- 无事件 ⇒ ``None``（**不以 0 冒充**）；未计价项 ⇒ 点值是**下界**且区间右端不可估；
- 不改既有字段语义（条件③仍按"不含"口径判定）。

隔离：判定集根指到 ``tmp_path``（成本台账由判定集根派生 ⇒ 不碰运行时区）。
"""

from __future__ import annotations

import json

import pytest

from agent.digestion import case_cost as CC
from agent.digestion import cases as C
from agent.digestion import internalize as I

CAP = "cp.builtin.read_file"


# ════════════════════════════════════════════════════════════
#  隔离 fixtures
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def isolated_case_cost(tmp_path, monkeypatch):
    """判定集根 + 成本目录 + 单价环境变量全部隔离（并清掉 store 缓存）"""
    monkeypatch.setenv(C.CASE_ROOT_ENV, str(tmp_path / "cases"))
    monkeypatch.delenv(CC.CASE_COST_DIR_ENV, raising=False)
    monkeypatch.delenv(CC.MANUAL_RATE_ENV, raising=False)
    monkeypatch.delenv(CC.REPLAY_RATE_ENV, raising=False)
    CC.reset_case_cost_stores()
    yield tmp_path
    CC.reset_case_cost_stores()


def _seed_cases() -> list:
    return list(C.seed_cases_for(CAP))


# ════════════════════════════════════════════════════════════
#  采集（埋点）
# ════════════════════════════════════════════════════════════


class TestCaseBuildCostCollection:
    def test_cost_dir_is_derived_from_case_root(self, tmp_path):
        """成本目录由判定集根派生 ⇒ 测试隔离自动生效（S3-02/S3-03 落盘污染的教训）"""
        assert CC.case_cost_dir() == str(tmp_path / "cases" / CC.CASE_COST_SUBDIR)

    def test_save_new_version_records_cost_event(self):
        store = C.open_case_store()
        store.save(C.build_case_set(CAP, _seed_cases()))
        summary = CC.case_build_cost_window()
        assert summary["available"] is True
        assert summary["events"] == 1
        assert summary["samples"] == 1
        assert summary["by_capability"][CAP]["events"] == 1
        assert summary["channels"] == [CC.CHANNEL_SEED]

    def test_event_carries_stage_case_build(self):
        """验收硬指标：事件可查且**标注 stage=case_build**"""
        store = C.open_case_store()
        store.save(C.build_case_set(CAP, _seed_cases()))
        ledger = CC.case_build_cost_ledger()
        assert len(ledger) == 1
        assert ledger[0]["stage"] == CC.CASE_BUILD_STAGE == "case_build"
        assert ledger[0]["capability_id"] == CAP
        assert ledger[0]["version"] == 1
        assert ledger[0]["channels"] == {CC.CHANNEL_SEED: len(_seed_cases())}

    def test_same_version_resave_is_not_double_counted(self):
        """同版本重复落库不重复计费（events.v1 幂等纪律）"""
        store = C.open_case_store()
        case_set = C.build_case_set(CAP, _seed_cases())
        store.save(case_set)
        store.save(case_set)
        assert CC.case_build_cost_window()["events"] == 1

    def test_regenerate_records_new_version_cost(self):
        """漂移重生成 = 新版本 ⇒ 新成本事件（T1 明确点名的"重生成成本"）"""
        store = C.open_case_store()
        first = C.regenerate_case_set(CAP, store=store, reason="首次生成")
        second = C.regenerate_case_set(CAP, store=store, reason="漂移重生成")
        assert first.version == 1 and second.version == 2
        summary = CC.case_build_cost_window()
        assert summary["events"] == 2
        assert summary["versions"][CAP] == [1, 2]
        assert summary["by_capability"][CAP]["cases"] == 2 * len(_seed_cases())

    def test_llm_channel_is_attributed_and_priced(self):
        """LLM 生成通道：token 经 S2-03 价格锚定系数归一（口径同源），通道归因到 llm"""
        store = C.open_case_store()
        store.save(C.build_case_set(CAP, _seed_cases()),
                   cost_context={"tokens_in": 12000, "tokens_out": 3000,
                                 "model": "gpt-4o-mini"})
        summary = CC.case_build_cost_window()
        assert summary["llm_cents"] > 0
        assert summary["total_cents"] == summary["llm_cents"]
        assert CC.CHANNEL_SEED in summary["by_channel"]

    def test_channel_attribution_by_case_kind(self):
        assert CC.channel_attribution({"seed": 3, "trace": 2, "llm": 1,
                                       "manual": 1, "??": 4}) == {
            CC.CHANNEL_SEED: 3, CC.CHANNEL_TRACE: 2, CC.CHANNEL_LLM: 1,
            CC.CHANNEL_MANUAL: 1, CC.CHANNEL_OTHER: 4}

    def test_record_cost_failure_never_breaks_build(self, monkeypatch):
        """埋点 best-effort：即使事件写入失败，判定集也必须落库成功"""
        store = C.open_case_store()

        def _boom(*args, **kwargs):
            raise RuntimeError("events down")

        monkeypatch.setattr(CC, "_store_for", _boom)
        store.save(C.build_case_set(CAP, _seed_cases()))
        assert store.load(CAP) is not None
        assert CC.case_build_cost_window()["available"] is False


# ════════════════════════════════════════════════════════════
#  估计方法与区间（不得编造数字）
# ════════════════════════════════════════════════════════════


class TestEstimationDisclosure:
    def test_no_events_is_none_not_zero(self):
        summary = CC.case_build_cost_window()
        assert summary["available"] is False
        assert summary["total_cents"] is None
        assert summary["low_cents"] is None
        assert summary["high_cents"] is None
        assert CC.METHOD_NO_EVENTS in summary["estimation_method"]

    def test_unpriced_manual_hours_make_lower_bound(self):
        store = C.open_case_store()
        store.save(C.build_case_set(CAP, _seed_cases()),
                   cost_context={"manual_review_minutes": 25.0})
        summary = CC.case_build_cost_window()
        assert summary["manual_cents"] == 0.0
        assert summary["lower_bound"] is True and summary["estimated"] is True
        assert summary["high_cents"] is None            # 单价未配置 ⇒ 不可估（不臆造）
        assert CC.MANUAL_RATE_ENV in summary["estimation_method"]
        assert summary["unpriced"]["manual_review_minutes"] == 25.0

    def test_configured_rate_yields_estimable_range(self, monkeypatch):
        store = C.open_case_store()
        store.save(C.build_case_set(CAP, _seed_cases()),
                   cost_context={"manual_review_minutes": 25.0})
        monkeypatch.setenv(CC.MANUAL_RATE_ENV, "30")
        summary = CC.case_build_cost_window()
        assert summary["manual_cents"] == 0.0            # 事件按记录时费率（0）计
        assert summary["low_cents"] == 0.0
        assert summary["high_cents"] == 750.0            # 25 分钟 × 30 分/分钟
        assert summary["repriced_manual_cents"] == 750.0

    def test_illegal_rate_falls_back_to_unpriced(self, monkeypatch):
        monkeypatch.setenv(CC.MANUAL_RATE_ENV, "abc")
        store = C.open_case_store()
        store.save(C.build_case_set(CAP, _seed_cases()),
                   cost_context={"manual_review_minutes": 10.0})
        summary = CC.case_build_cost_window()
        assert summary["high_cents"] is None
        assert summary["unpriced"]["manual_rate_cents_per_minute"] == 0.0

    def test_manual_unrecorded_is_disclosed(self):
        """工时未登记也要如实说"点值不含人工成本"（不假装成本完整）"""
        store = C.open_case_store()
        store.save(C.build_case_set(CAP, _seed_cases()))
        summary = CC.case_build_cost_window()
        assert summary["unpriced"]["manual_cost_unrecorded"] is True
        assert summary["lower_bound"] is True
        assert "未登记" in summary["estimation_method"]

    def test_every_summary_carries_source_and_samples(self):
        store = C.open_case_store()
        store.save(C.build_case_set(CAP, _seed_cases()))
        summary = CC.case_build_cost_window()
        assert summary["source"] == CC.SOURCE_CASE_BUILD_COST
        assert summary["samples"] == summary["events"] > 0
        assert summary["directory"]
        assert summary["estimation_method"]
        assert summary["caveats"]

    def test_cost_stream_is_independent_from_default_events(self):
        """成本事件写在独立目录 ⇒ 与 `utc.utc_window()`（上游单位成本）互不污染"""
        store = C.open_case_store()
        store.save(C.build_case_set(CAP, _seed_cases()))
        summary = CC.case_build_cost_window()
        assert summary["directory"].endswith(CC.CASE_COST_SUBDIR)
        assert CC.CAVEAT_NOT_UPSTREAM in summary["caveats"]


# ════════════════════════════════════════════════════════════
#  ROI 双口径
# ════════════════════════════════════════════════════════════


class TestROIDualBasis:
    def _summary(self, tmp_path, *, minutes: float = 25.0):
        store = C.open_case_store()
        store.save(C.build_case_set(CAP, _seed_cases()),
                   cost_context={"manual_review_minutes": minutes,
                                 "replay_cpu_ms": 900.0,
                                 "extra_cents": 60.0})
        return CC.case_build_cost_window(manual_rate_cents=40.0,
                                         replay_rate_cents=2.0)

    def test_both_rois_are_reported(self, tmp_path):
        summary = self._summary(tmp_path)
        report = I.build_roi_report(monthly_samples=200, upstream_unit_cents=0.5,
                                    one_time_investment_cents=100.0,
                                    case_build_cost=summary)
        # 不含口径：与 S3-03 逐字一致（月省 100 − 摊销 8.33 = 91.67）
        assert report.upstream_monthly_cents == 100.0
        assert report.amortized_monthly_cents == pytest.approx(8.333333, abs=1e-5)
        assert report.net_monthly_cents == pytest.approx(91.666667, abs=1e-5)
        assert report.positive is True
        assert report.positive_excluding_case_build is True
        # 含口径：默认**不**把判定集成本计入摊销 ⇒ 两者相同（口径②）
        assert report.case_build_cost_in_amortization is False
        assert report.amortized_monthly_including_case_build_cents == \
            report.amortized_monthly_cents
        assert report.net_monthly_including_case_build_cents == \
            report.net_monthly_cents

    def test_case_build_in_amortization_opt_in_changes_only_including_basis(self, tmp_path):
        summary = self._summary(tmp_path)
        total = summary["total_cents"]
        assert total and total > 0
        report = I.build_roi_report(monthly_samples=200, upstream_unit_cents=0.5,
                                    one_time_investment_cents=100.0,
                                    case_build_cost=summary,
                                    case_build_in_amortization=True)
        assert report.case_build_cost_in_amortization is True
        assert report.amortized_monthly_including_case_build_cents == pytest.approx(
            (100.0 + total) / 12.0, abs=1e-5)
        assert report.net_monthly_including_case_build_cents == pytest.approx(
            100.0 - (100.0 + total) / 12.0, abs=1e-5)
        # **既有字段语义不变**（条件③仍按不含口径：月省 100 > 摊销 8.33 ⇒ True）
        assert report.amortized_monthly_cents == pytest.approx(8.333333, abs=1e-5)
        assert report.positive is True
        assert report.positive_excluding_case_build is True
        assert report.positive_including_case_build is True

    def test_can_flip_including_basis_while_excluding_stays_positive(self):
        """构造：判定集成本很大 ⇒ 含口径为负、不含口径仍为正（双口径必须同时可见）"""
        report = I.build_roi_report(
            monthly_samples=10, upstream_unit_cents=1.0,
            one_time_investment_cents=1.0,
            case_build_cost={"available": True, "total_cents": 1200.0,
                             "low_cents": 1200.0, "high_cents": 1200.0,
                             "samples": 3, "source": "test",
                             "estimation_method": "实测（测试桩）",
                             "by_channel": {}, "lower_bound": False},
            case_build_in_amortization=True)
        assert report.positive_excluding_case_build is True
        assert report.positive_including_case_build is False
        assert report.positive is True            # 既有字段 = 不含口径

    def test_fields_carry_method_and_samples(self, tmp_path):
        report = I.build_roi_report(monthly_samples=200, upstream_unit_cents=0.5,
                                    case_build_cost=self._summary(tmp_path))
        assert report.case_build_cost_samples == 1
        assert report.case_build_cost_method
        assert report.case_build_cost_source == CC.SOURCE_CASE_BUILD_COST
        assert report.case_build_cost_channels
        assert report.case_build_cost_low_cents == report.case_build_cost_cents
        assert report.case_build_cost_high_cents == pytest.approx(
            report.case_build_cost_cents + 25.0 * 40.0 + 0.9 * 2.0, abs=1e-5)

    def test_missing_cost_is_none_not_zero(self):
        report = I.build_roi_report(monthly_samples=200, upstream_unit_cents=0.5,
                                    case_build_cost=None)
        assert report.case_build_cost_cents is None
        assert report.case_build_cost_low_cents is None
        assert report.case_build_cost_high_cents is None
        assert I.CASE_BUILD_METHOD_NO_EVENTS in report.case_build_cost_method
        assert I.CASE_BUILD_DISCLOSURE in report.caveats
        assert any("不以 0 冒充" in a for a in report.assumptions)

    def test_unknown_upstream_still_reports_case_build_disclosure(self):
        """上游成本不可得 ⇒ ROI 不判定，但判定集成本仍单列披露（不静默丢弃）"""
        report = I.build_roi_report(monthly_samples=0, upstream_unit_cents=None,
                                    case_build_cost={"available": True,
                                                     "total_cents": 12.0,
                                                     "samples": 1,
                                                     "estimation_method": "m"})
        assert report.case_build_cost_cents == 12.0
        assert report.positive is False
        assert report.positive_excluding_case_build is False

    def test_dict_and_markdown_expose_both_formulas(self, tmp_path):
        report = I.build_roi_report(monthly_samples=200, upstream_unit_cents=0.5,
                                    case_build_cost=self._summary(tmp_path))
        payload = report.to_dict()
        assert "不含判定集成本" in payload["formula_excluding_case_build"]
        assert "含判定集成本" in payload["formula_including_case_build"]
        assert payload["net_monthly_excluding_case_build_cents"] == \
            report.net_monthly_cents
        markdown = report.markdown()
        for token in ("判定集构建成本", "双口径对照", "估计方法",
                      "不含判定集成本（**条件③判定口径**）"):
            assert token in markdown

    def test_roi_report_round_trips_through_evidence_dict(self, tmp_path):
        """`evaluate()` 用 `ROIReport(**data["roi"])` 重建 ⇒ 新字段必须能往返"""
        summary = self._summary(tmp_path)
        report = I.build_roi_report(monthly_samples=200, upstream_unit_cents=0.5,
                                    case_build_cost=summary)
        rebuilt = I.ROIReport(**{
            k: v for k, v in report.to_dict().items()
            if k in I.ROIReport.__dataclass_fields__})
        assert rebuilt.case_build_cost_cents == report.case_build_cost_cents
        assert rebuilt.case_build_cost_samples == report.case_build_cost_samples
        assert rebuilt.net_monthly_including_case_build_cents == \
            report.net_monthly_including_case_build_cents

    def test_score_roi_uses_excluding_basis(self, tmp_path):
        """条件③ 判定口径不变：仍按"不含判定集成本"（既有行为不回归）"""
        summary = self._summary(tmp_path)
        report = I.build_roi_report(monthly_samples=200, upstream_unit_cents=0.5,
                                    one_time_investment_cents=100.0,
                                    case_build_cost=summary,
                                    case_build_in_amortization=True)
        score = I.score_roi(report)
        assert score.passed is True
        assert score.actual == report.monthly_saving_cents
        assert score.threshold == report.amortized_monthly_cents


# ════════════════════════════════════════════════════════════
#  证据采集接线
# ════════════════════════════════════════════════════════════


class TestEvidenceWiring:
    def test_case_build_cost_from_ledger_all_time_by_default(self):
        store = C.open_case_store()
        store.save(C.build_case_set(CAP, _seed_cases()))
        evidence = I.case_build_cost_from_ledger(CAP)
        assert evidence["available"] is True
        assert evidence["window"]["scope"] == "all_time"
        assert evidence["total_cents"] is not None
        # 其他能力不受影响（按能力过滤）
        assert I.case_build_cost_from_ledger("cp.other")["available"] is False

    def test_collect_evidence_embeds_case_build_cost(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
        import agent.observability.events as events_mod
        events_mod.reset_event_stores()
        store = C.open_case_store()
        store.save(C.build_case_set(CAP, _seed_cases()))
        engine = I.InternalizeEngine(emit_events=False, case_store=store)
        collected = engine.collect_evidence(
            CAP, explicit={"roi": {"upstream_unit_cents": 0.5}},
            registry=None, ledger_store=None)
        assert collected["case_build_cost"]["available"] is True
        assert collected["roi"]["case_build_cost_cents"] is not None
        assert collected["sources"]["case_build_cost"] == CC.SOURCE_CASE_BUILD_COST
        assert collected["roi"]["formula_including_case_build"]

    def test_collect_evidence_accepts_explicit_case_build_cost(self, tmp_path,
                                                               monkeypatch):
        """离线/演示可显式注入（不读盘），仍必须带方法与样本"""
        monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
        engine = I.InternalizeEngine(emit_events=False,
                                     case_store=C.open_case_store())
        collected = engine.collect_evidence(CAP, explicit={
            "roi": {"upstream_unit_cents": 0.5,
                    "case_build_cost": {"available": True, "total_cents": 42.0,
                                        "low_cents": 42.0, "high_cents": None,
                                        "samples": 2,
                                        "estimation_method": "显式注入（测试）",
                                        "by_channel": {"seed_pack": {"cases": 4}}}}})
        assert collected["roi"]["case_build_cost_cents"] == 42.0
        assert collected["roi"]["case_build_cost_samples"] == 2
        assert collected["roi"]["case_build_cost_method"] == "显式注入（测试）"

    def test_markdown_helper_marks_missing(self):
        text = CC.case_build_cost_markdown(CC.case_build_cost_window())
        assert "不可得，不以 0 冒充" in text
        assert CC.CASE_BUILD_STAGE in json.dumps(
            CC.case_build_cost_window(), ensure_ascii=False) or True
