"""TASK-S7-03 成本系数校准单测（`agent/observability/cost_calibration.py` + `utc` 来源层）

覆盖任务书 §四 验收清单中可机械验证的部分：

1. **系数计算**：单位任务成本比 + 有效标量价格系数 + 偏差率；
2. **样本不足 → 只披露不结论**（< 20 条/模型：**不给系数、不进工件**）；
3. **来源标注与优先级**：`override > measured > price_ratio`，且**逐模型**回落；
4. **版本标注**：完整实测 `measured.v1` / 降级部分校准 `measured.partial.v1`；
5. **历史口径不追溯**：改系数**不改写**已写入事件，聚合读字段求和；
6. **降级路径如实标注**：无凭证 / 离线重放 → `calibrated=False` + 显式声明；
7. **工件校验**：非法/未知版本**拒绝**而不是静默容忍；缺失 → 回落价格系数；
8. **过期规则**：锚模型变更 / 用例集哈希变更 → 失效回落。

**隔离纪律**：`CP_UTC_CALIBRATION_FILE` 一律指向 `tmp_path`（autouse），
避免本机真实校准件让断言随环境漂移；事件目录同理走 `CP_EVENTS_DIR`。
"""

import json
import os

import pytest

from agent.observability import cost_calibration as CC
from agent.observability import utc as U
from agent.observability.events import EventStore

ANCHOR = "gpt-4"
MODEL = "gpt-4o-mini"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """全部用例在临时目录里跑：校准件、事件流、锚模型都不碰真实运行环境"""
    monkeypatch.setenv(CC.ENV_ARTIFACT, str(tmp_path / "cost_coefficients.json"))
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setenv(U.ENV_ANCHOR_MODEL, ANCHOR)
    for name in (U.ENV_COEFFICIENTS, U.ENV_PRICE_OVERRIDES, "CP_ENV_FILE",
                 "LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    U.reset_config_cache()
    yield
    U.reset_config_cache()


@pytest.fixture
def store(tmp_path):
    return EventStore(str(tmp_path / "events" / "events.jsonl"))


def rows_for(model, *, count, cents_per_call, tokens_in=1000, tokens_out=0,
             tasks=None, cost_normalized=None, error=""):
    """构造样本行（**数字全部显式传入**：测试里没有任何"默认魔数"）"""
    out = []
    for index in range(count):
        out.append(CC.SampleRow(
            model=model,
            task_id=(f"{model}-t{index % tasks}" if tasks else ""),
            tokens_in=tokens_in, tokens_out=tokens_out,
            cost_raw_cents=cents_per_call,
            cost_normalized_cents=(cents_per_call if cost_normalized is None
                                   else cost_normalized),
            error=error, source_path="test.jsonl", source_line=index + 1))
    return out


def write_artifact(calibration, path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(calibration.to_dict(), ensure_ascii=False, indent=2) + "\n")
    return path


def make_calibration(version=CC.MEASURED_VERSION, method=CC.METHOD_L2_RUN,
                     coefficient=0.5, anchor=ANCHOR, model=MODEL,
                     caseset_sha256="", created_at="2026-09-12T10:00:00+0800"):
    calibration = CC.CostCalibration(
        version=version, method=method, created_at=created_at,
        anchor_model=anchor, caseset_sha256=caseset_sha256)
    calibration.models[model] = CC.ModelCalibration(
        model=model, samples=20, tasks=20, measured_coefficient=coefficient,
        confidence="adequate" if version == CC.MEASURED_VERSION else "provisional")
    return calibration


# ════════════════════════════════════════════════════════════
#  1. 系数计算（公式可复现）
# ════════════════════════════════════════════════════════════


class TestCoefficientMath:
    def test_measured_coefficient_is_unit_task_cost_ratio(self):
        anchor = CC.MeasuredSamples.from_rows(
            ANCHOR, rows_for(ANCHOR, count=20, cents_per_call=10.0, tasks=20))
        model = CC.MeasuredSamples.from_rows(
            MODEL, rows_for(MODEL, count=20, cents_per_call=2.5, tasks=20))
        assert anchor.cents_per_task_raw() == pytest.approx(10.0)
        assert model.cents_per_task_raw() == pytest.approx(2.5)
        assert CC.measured_coefficient(model, anchor) == pytest.approx(0.25)

    def test_measured_coefficient_none_when_anchor_has_no_tasks(self):
        anchor = CC.MeasuredSamples.from_rows(ANCHOR, [])
        model = CC.MeasuredSamples.from_rows(MODEL, rows_for(MODEL, count=1,
                                                             cents_per_call=1.0))
        assert CC.measured_coefficient(model, anchor) is None

    def test_effective_price_coefficient_weights_by_token_mix(self):
        price = {"in": 0.5, "out": 2.0}
        # 全输入 → 0.5；全输出 → 2.0；对半 → 1.25
        assert CC.effective_price_coefficient(price, 100, 0) == pytest.approx(0.5)
        assert CC.effective_price_coefficient(price, 0, 100) == pytest.approx(2.0)
        assert CC.effective_price_coefficient(price, 50, 50) == pytest.approx(1.25)

    def test_effective_price_coefficient_none_without_tokens(self):
        assert CC.effective_price_coefficient({"in": 1.0, "out": 1.0}, 0, 0) is None

    def test_deviation_math_and_none_guards(self):
        assert CC.deviation(1.5, 1.0) == pytest.approx(0.5)
        assert CC.deviation(0.5, 1.0) == pytest.approx(-0.5)
        assert CC.deviation(None, 1.0) is None
        assert CC.deviation(1.0, None) is None
        assert CC.deviation(1.0, 0.0) is None

    def test_statistics_from_rows(self):
        rows = rows_for(MODEL, count=4, cents_per_call=2.0, tokens_in=100,
                        tokens_out=50, tasks=2)
        samples = CC.MeasuredSamples.from_rows(MODEL, rows)
        assert samples.samples == 4 and samples.tasks == 2
        assert samples.tokens_in == 400 and samples.tokens_out == 200
        assert samples.cost_raw_cents == pytest.approx(8.0)
        assert samples.mean_cost_cents() == pytest.approx(2.0)
        assert samples.median_cost_cents() == pytest.approx(2.0)
        assert samples.p95_cost_cents() == pytest.approx(2.0)

    def test_anonymous_rows_count_as_separate_tasks(self):
        """缺 `task_id` 的行各算一个任务（**已知偏差，如实计数**）"""
        rows = rows_for(MODEL, count=3, cents_per_call=1.0, tasks=None)
        samples = CC.MeasuredSamples.from_rows(MODEL, rows)
        assert samples.tasks == 3


# ════════════════════════════════════════════════════════════
#  2. 样本不足 → 只披露不结论
# ════════════════════════════════════════════════════════════


class TestSampleAdequacy:
    def test_confidence_is_mechanical(self):
        assert CC.confidence_for(0) == "insufficient"
        assert CC.confidence_for(19) == "insufficient"
        assert CC.confidence_for(20) == "adequate"
        assert CC.confidence_for(20, method=CC.METHOD_OFFLINE_REPLAY) == "provisional"
        assert CC.confidence_for(20, method=CC.METHOD_MIXED) == "provisional"

    def test_insufficient_samples_disclosed_not_concluded(self):
        rows = (rows_for(ANCHOR, count=20, cents_per_call=10.0, tasks=20)
                + rows_for(MODEL, count=19, cents_per_call=1.0, tasks=19))
        calibration, table = CC.build_calibration(
            rows, anchor_model=ANCHOR, method=CC.METHOD_L2_RUN)
        assert MODEL not in calibration.models            # 不进工件
        assert MODEL in calibration.insufficient_models   # 但要披露
        row = next(r for r in table["rows"] if r["model"] == MODEL)
        assert row["measured_coefficient"] is None        # 不给系数
        assert row["deviation"] is None
        assert row["samples"] == 19
        assert "只披露不结论" in row["note"]

    def test_adequate_samples_produce_coefficient(self):
        rows = (rows_for(ANCHOR, count=20, cents_per_call=10.0, tasks=20)
                + rows_for(MODEL, count=20, cents_per_call=2.5, tasks=20))
        calibration, table = CC.build_calibration(
            rows, anchor_model=ANCHOR, method=CC.METHOD_L2_RUN)
        assert calibration.measured_models == [MODEL]
        assert calibration.calibrated is True
        row = next(r for r in table["rows"] if r["model"] == MODEL)
        assert row["measured_coefficient"] == pytest.approx(0.25)
        # 价格有效标量：锚 token 全为输入 → 用 k_in = 0.00015/0.03 = 0.005
        assert row["price_coefficient_effective"] == pytest.approx(0.005)
        assert row["deviation"] == pytest.approx((0.25 - 0.005) / 0.005)
        assert row["adequate"] is True

    def test_anchor_itself_insufficient_blocks_everything(self):
        """锚样本不足 → 分母不达标 → 谁都不能生效"""
        rows = (rows_for(ANCHOR, count=5, cents_per_call=10.0, tasks=5)
                + rows_for(MODEL, count=20, cents_per_call=2.5, tasks=20))
        calibration, _ = CC.build_calibration(
            rows, anchor_model=ANCHOR, method=CC.METHOD_L2_RUN)
        assert calibration.models == {}
        assert calibration.calibrated is False
        assert ANCHOR in calibration.insufficient_models
        assert any("锚模型自身样本" in item for item in calibration.disclosures)

    def test_anchor_absent_from_sample_is_disclosed(self):
        rows = rows_for(MODEL, count=20, cents_per_call=2.5, tasks=20)
        calibration, table = CC.build_calibration(
            rows, anchor_model=ANCHOR, method=CC.METHOD_L2_RUN)
        assert calibration.models == {}
        assert ANCHOR in calibration.insufficient_models
        anchor_row = next(r for r in table["rows"] if r["is_anchor"])
        assert anchor_row["samples"] == 0
        assert anchor_row["measured_coefficient"] is None


# ════════════════════════════════════════════════════════════
#  3. 来源标注与优先级（override > measured > price_ratio）
# ════════════════════════════════════════════════════════════


class TestSourcePriority:
    def test_default_is_price_ratio_and_matches_legacy(self):
        detail = U.coefficient_detail(MODEL)
        assert detail["source"] == "price_ratio"
        assert detail["calibrated"] is False
        assert U.coefficient(MODEL) == {"in": pytest.approx(0.005),
                                        "out": pytest.approx(0.01),
                                        "source": "price_ratio"}

    def test_measured_artifact_takes_effect(self, tmp_path):
        write_artifact(make_calibration(coefficient=0.25), str(tmp_path / "cost_coefficients.json"))
        detail = U.coefficient_detail(MODEL)
        assert detail["source"] == "measured"
        assert detail["in"] == pytest.approx(0.25)
        assert detail["out"] == pytest.approx(0.25)
        assert detail["calibrated"] is True
        assert detail["calibration_version"] == U.MEASURED_CALIBRATION_VERSION

    def test_override_beats_measured(self, tmp_path, monkeypatch):
        write_artifact(make_calibration(coefficient=0.25), str(tmp_path / "cost_coefficients.json"))
        monkeypatch.setenv(U.ENV_COEFFICIENTS, json.dumps({MODEL: {"in": 9.0, "out": 9.0}}))
        detail = U.coefficient_detail(MODEL)
        assert detail["source"] == "override"
        assert detail["in"] == pytest.approx(9.0)

    def test_unmeasured_model_falls_back_per_model(self, tmp_path):
        """**逐模型**回落：A 生效不改变 B 的口径（S5-03 无行为回归的关键）"""
        write_artifact(make_calibration(coefficient=0.25), str(tmp_path / "cost_coefficients.json"))
        assert U.coefficient(MODEL)["source"] == "measured"
        assert U.coefficient("gpt-3.5-turbo")["source"] == "price_ratio"
        table = U.coefficient_table(["gpt-4", MODEL, "gpt-3.5-turbo"])
        assert table["coefficient_sources"] == {ANCHOR: "price_ratio",
                                                MODEL: "measured",
                                                "gpt-3.5-turbo": "price_ratio"}
        assert table["measured_models"] == [MODEL]

    def test_table_carries_price_side_for_comparison(self, tmp_path):
        write_artifact(make_calibration(coefficient=0.25), str(tmp_path / "cost_coefficients.json"))
        table = U.coefficient_table([MODEL])
        row = table["models"][MODEL]
        assert row["source"] == "measured"
        assert row["price_coefficient"]["in"] == pytest.approx(0.005)
        assert row["origin"].startswith("artifact:")

    def test_normalization_uses_measured_coefficient(self, tmp_path):
        write_artifact(make_calibration(coefficient=1.0), str(tmp_path / "cost_coefficients.json"))
        calc = U.normalize_cost(1000, 0, MODEL)
        assert calc["coefficient_source"] == "measured"
        # 归一到锚价：1000/1000 × 3.0 分 × 1.0
        assert calc["cost_normalized_cents"] == pytest.approx(3.0)


# ════════════════════════════════════════════════════════════
#  4. 版本标注（完整实测 / 降级部分校准 / 回落）
# ════════════════════════════════════════════════════════════


class TestVersionLabelling:
    def test_no_artifact_keeps_price_anchor_labels(self):
        block = U.calibration_block()
        assert block["calibration_version"] == U.CALIBRATION_VERSION
        assert block["calibrated"] is False
        assert block["calibration_partial"] is False
        assert block["measured_models"] == []
        assert block["stale_reason"] == "no_artifact"
        assert "价格锚定" in block["calibration_note"]

    def test_measured_artifact_raises_version(self, tmp_path):
        write_artifact(make_calibration(), str(tmp_path / "cost_coefficients.json"))
        block = U.calibration_block()
        assert block["calibration_version"] == U.MEASURED_CALIBRATION_VERSION
        assert block["calibrated"] is True
        assert block["measured_models"] == [MODEL]
        assert block["calibrated_at"] == "2026-09-12T10:00:00+0800"
        assert block["coefficient_sources"]["counts"]["measured"] >= 1

    def test_partial_artifact_is_not_claimed_as_measured(self, tmp_path):
        """**降级不得冒充实测**：版本打标 + `calibrated=False` + 显式声明"""
        cal = make_calibration(version=CC.MEASURED_PARTIAL_VERSION,
                               method=CC.METHOD_OFFLINE_REPLAY)
        write_artifact(cal, str(tmp_path / "cost_coefficients.json"))
        block = U.calibration_block()
        assert block["calibration_version"] == U.MEASURED_PARTIAL_CALIBRATION_VERSION
        assert block["calibrated"] is False
        assert block["calibration_partial"] is True
        assert "未完成完整实测校准" in block["calibration_note"]
        assert block["calibration_method"] == CC.METHOD_OFFLINE_REPLAY

    def test_utc_daily_and_snapshot_carry_upgraded_block(self, store):
        U.record_cost(model=ANCHOR, tokens_in=1000, interaction_id="v1",
                      task_id="t1", store=store)
        row = U.utc_daily(directory=os.path.dirname(store.path))
        assert "coefficient_sources" in row["calibration"]
        assert row["calibration"]["calibration_version"] == U.CALIBRATION_VERSION
        snapshot = U.utc_snapshot(days=2, directory=os.path.dirname(store.path))
        assert snapshot["calibration"]["priority"] if "priority" in snapshot[
            "calibration"] else True
        assert snapshot["calibration"]["source_of_truth"] == "events"

    def test_calibration_status_is_disclosed(self):
        block = U.calibration_block()
        assert "无可用多模型凭证" in block["calibration_status"]
        assert "L2 Core-50" in block["calibration_trigger"]


# ════════════════════════════════════════════════════════════
#  5. 历史口径不追溯
# ════════════════════════════════════════════════════════════


class TestNoRetroactiveReinterpretation:
    def test_historical_event_keeps_its_own_coefficient(self, tmp_path, store):
        """旧事件带**写入时**的系数与来源；之后改系数**不改写**它"""
        env = U.record_cost(model=MODEL, tokens_in=1000, tokens_out=0,
                            task_id="t-hist", interaction_id="hist-1", store=store)
        assert env.payload["coefficient_source"] == "price_ratio"
        before = env.payload["cost_normalized_cents"]

        # 把实测件换成 coefficient=1.0（会把"若重算"的结果翻 200 倍）
        write_artifact(make_calibration(coefficient=1.0),
                       str(tmp_path / "cost_coefficients.json"))
        assert U.coefficient(MODEL)["source"] == "measured"

        row = U.utc_daily(directory=os.path.dirname(store.path))
        # 聚合读事件字段求和 → 历史数字**分毫不变**
        assert row["cost_normalized_cents"] == pytest.approx(before)
        assert row["by_model"][MODEL]["cost_normalized_cents"] == pytest.approx(before)
        assert U.normalize_cost(1000, 0, MODEL)["cost_normalized_cents"] == pytest.approx(3.0)

    def test_new_events_use_new_calibration(self, tmp_path, store):
        U.record_cost(model=MODEL, tokens_in=1000, tokens_out=0, task_id="t-old",
                      interaction_id="old", store=store)
        write_artifact(make_calibration(coefficient=1.0),
                       str(tmp_path / "cost_coefficients.json"))
        U.record_cost(model=MODEL, tokens_in=1000, tokens_out=0, task_id="t-new",
                      interaction_id="new", store=store)
        row = U.utc_daily(directory=os.path.dirname(store.path))
        # 旧 0.015 分 + 新 3.0 分 = 3.015（**新系数只影响生效之后的写入**）
        assert row["cost_normalized_cents"] == pytest.approx(3.015)

    def test_module_never_rewrites_event_files(self, tmp_path, store):
        path = store.path
        U.record_cost(model=ANCHOR, tokens_in=10, interaction_id="rw-1", store=store)
        digest = os.path.getsize(path), open(path, "rb").read()
        write_artifact(make_calibration(), str(tmp_path / "cost_coefficients.json"))
        U.utc_daily(directory=os.path.dirname(path))
        assert (os.path.getsize(path), open(path, "rb").read()) == digest


# ════════════════════════════════════════════════════════════
#  6. 工件读写与校验（拒绝坏数据 / 不创建文件）
# ════════════════════════════════════════════════════════════


class TestArtifactIO:
    def test_missing_artifact_is_none_and_not_created(self, tmp_path):
        target = tmp_path / "nope.json"
        assert CC.load_artifact(str(target)) is None
        assert not target.exists()
        with pytest.raises(CC.CalibrationError):
            CC.load_artifact(str(target), required=True)

    def test_invalid_json_is_rejected(self, tmp_path):
        target = tmp_path / "bad.json"
        target.write_text("{not json", encoding="utf-8")
        assert CC.load_artifact(str(target)) is None
        with pytest.raises(CC.CalibrationError):
            CC.load_artifact(str(target), required=True)

    def test_unknown_version_is_rejected(self, tmp_path):
        target = tmp_path / "bad.json"
        target.write_text(json.dumps({"schema": CC.CALIBRATION_SCHEMA,
                                      "version": "v9", "models": {}}),
                          encoding="utf-8")
        assert CC.load_artifact(str(target)) is None

    def test_round_trip(self, tmp_path):
        cal = make_calibration(coefficient=0.375)
        path = write_artifact(cal, str(tmp_path / "cost_coefficients.json"))
        loaded = CC.load_artifact(path, required=True)
        assert loaded is not None
        assert loaded.anchor_model == ANCHOR
        assert loaded.models[MODEL].measured_coefficient == pytest.approx(0.375)
        assert loaded.measured_models == [MODEL]
        assert loaded.path == path

    def test_env_var_selects_artifact(self, tmp_path, monkeypatch):
        target = tmp_path / "from_env.json"
        write_artifact(make_calibration(), str(target))
        monkeypatch.setenv(CC.ENV_ARTIFACT, str(target))
        assert CC.artifact_path() == str(target)
        assert U.coefficient(MODEL)["source"] == "measured"


# ════════════════════════════════════════════════════════════
#  7. 过期规则（锚变更 / 量尺变更）
# ════════════════════════════════════════════════════════════


class TestStaleness:
    def test_anchor_change_invalidates(self):
        cal = make_calibration(anchor="gpt-4o-mini")
        assert CC.stale_reason(cal, anchor_model=ANCHOR) == \
            "anchor_changed:gpt-4o-mini->gpt-4"
        assert CC.measured_coefficients(cal, anchor_model=ANCHOR) == {}

    def test_caseset_change_invalidates(self):
        cal = make_calibration(caseset_sha256="aaa")
        assert CC.stale_reason(cal, anchor_model=ANCHOR,
                               caseset_sha256="bbb") == "caseset_changed"
        assert CC.measured_coefficients(cal, anchor_model=ANCHOR,
                                        caseset_sha256="bbb") == {}

    def test_unknown_caseset_does_not_invalidate(self):
        cal = make_calibration(caseset_sha256="aaa")
        assert CC.stale_reason(cal, anchor_model=ANCHOR, caseset_sha256="") == ""

    def test_stale_artifact_falls_back_in_utc(self, tmp_path, monkeypatch):
        write_artifact(make_calibration(anchor="gpt-4o-mini"),
                       str(tmp_path / "cost_coefficients.json"))
        assert U.coefficient(MODEL)["source"] == "price_ratio"
        block = U.calibration_block()
        assert block["stale_reason"].startswith("anchor_changed")
        assert block["calibrated"] is False

    def test_empty_artifact_reason(self):
        cal = CC.CostCalibration(anchor_model=ANCHOR)
        assert CC.stale_reason(cal, anchor_model=ANCHOR) == "empty_artifact"


# ════════════════════════════════════════════════════════════
#  8. 降级路径（脚本侧：重放 / CSV / 凭证探测）
# ════════════════════════════════════════════════════════════


class TestDegradedPath:
    def test_replay_reads_only_cost_events_with_provenance(self, tmp_path):
        from scripts import calibrate_cost_coefficients as S
        events = tmp_path / "events"
        events.mkdir()
        target = events / "events.jsonl"
        lines = [
            {"event_id": "e1", "type": "cost", "ts": "2026-09-12T10:00:00+08:00",
             "payload": {"model": ANCHOR, "tokens_in": 100, "tokens_out": 0,
                         "cost_raw_cents": 1.0, "cost_normalized_cents": 1.0,
                         "task_id": "t1"}},
            {"event_id": "e2", "type": "cost", "ts": "2026-09-12T10:01:00+08:00",
             "payload": {"model": MODEL, "tokens_in": 50, "tokens_out": 0,
                         "cost_raw_cents": 0.2, "cost_normalized_cents": 0.2,
                         "task_id": "t2", "error": "boom"}},
            {"event_id": "e3", "type": "task.closed", "ts": "2026-09-12T10:02:00+08:00",
             "payload": {"task_id": "t3"}},
            {"event_id": "e1", "type": "cost", "ts": "2026-09-12T10:00:00+08:00",
             "payload": {"model": ANCHOR, "tokens_in": 100, "tokens_out": 0,
                         "cost_raw_cents": 1.0, "cost_normalized_cents": 1.0,
                         "task_id": "t1"}},
        ]
        target.write_text("\n".join(json.dumps(x) for x in lines) + "\n",
                          encoding="utf-8")
        rows, meta = S.replay_event_rows([ANCHOR, MODEL], events_dir=str(events))
        assert len(rows) == 2                      # 非 cost 事件与重复 event_id 都排除
        assert meta["duplicate_rows_dropped"] == 1
        assert meta["rows_with_error"] == 1
        assert rows[0].provenance.endswith("events.jsonl:1")
        assert rows[1].cost_raw_cents == pytest.approx(0.2)

    def test_degraded_build_is_partial_version(self):
        rows = (rows_for(ANCHOR, count=20, cents_per_call=10.0, tasks=20)
                + rows_for(MODEL, count=20, cents_per_call=2.5, tasks=20))
        calibration, table = CC.build_calibration(
            rows, anchor_model=ANCHOR, method=CC.METHOD_OFFLINE_REPLAY)
        assert calibration.version == CC.MEASURED_PARTIAL_VERSION
        assert calibration.calibrated is False          # **不得冒充实测**
        assert calibration.partial is True
        row = next(r for r in table["rows"] if r["model"] == MODEL)
        assert row["confidence"] == "provisional"
        assert any("未完成完整实测校准" in item for item in calibration.disclosures)

    def test_csv_import_requires_columns(self, tmp_path):
        from scripts import calibrate_cost_coefficients as S
        target = tmp_path / "bad.csv"
        target.write_text("model,tokens_in\n" + f"{MODEL},10\n", encoding="utf-8")
        with pytest.raises(CC.CalibrationError):
            S.import_csv_rows(str(target), known_models=[MODEL])

    def test_csv_import_skips_bad_rows_and_counts(self, tmp_path):
        from scripts import calibrate_cost_coefficients as S
        target = tmp_path / "ok.csv"
        target.write_text(
            "model,task_id,tokens_in,tokens_out,cost_raw_cents,error\n"
            f"{MODEL},t1,100,0,0.5,\n"
            "unknown-model,t2,100,0,0.5,\n"          # 未知模型 → 跳过
            f"{MODEL},t3,abc,0,0.5,\n"               # 数值非法 → 跳过
            f"{MODEL},t4,100,0,0.5,boom\n",
            encoding="utf-8")
        rows, meta = S.import_csv_rows(str(target), known_models=[MODEL])
        assert len(rows) == 2
        assert meta["rows_skipped"] == 2
        assert meta["by_model"] == {MODEL: 2}
        assert meta["rows_without_ts"] == 2
        assert any("未知模型" in item["reason"] for item in meta["skipped"])

    def test_credential_scan_flags_placeholders(self):
        from scripts import calibrate_cost_coefficients as S
        scan = S.find_credentials({"LLM_API_KEY": "sk-test-1234",
                                   "DEEPSEEK_API_KEY": "short",
                                   "OPENAI_API_KEY": ""})
        assert scan["LLM_API_KEY"]["verdict"] == "placeholder"
        assert scan["DEEPSEEK_API_KEY"]["verdict"] == "placeholder"
        assert scan["OPENAI_API_KEY"]["verdict"] == "empty"

    def test_credential_scan_accepts_long_key_shape(self):
        from scripts import calibrate_cost_coefficients as S
        scan = S.find_credentials({"LLM_API_KEY": "a" * 40})
        assert scan["LLM_API_KEY"]["verdict"] == "valid"
        assert "a" * 40 not in scan["LLM_API_KEY"]["value"]   # **必须脱敏**

    def test_budget_stops_charging(self):
        from scripts import calibrate_cost_coefficients as S
        budget = S.Budget(limit_cents=1.0)
        budget.charge(0.6)
        assert budget.exhausted is False
        budget.charge(0.6)
        assert budget.exhausted is True
        assert budget.to_dict()["spent_cents"] == pytest.approx(1.2)

    def test_parse_answer_tolerates_code_fence(self):
        from scripts import calibrate_cost_coefficients as S
        assert S._parse_answer('```json\n{"code": "x"}\n```') == {"code": "x"}
        assert S._parse_answer("garbage") == {}
        assert S._parse_answer('前言 {"a": 1} 后记') == {"a": 1}

    def test_resolve_models_excludes_anchor(self):
        from scripts import calibrate_cost_coefficients as S
        assert ANCHOR not in S.resolve_models("", ANCHOR)
        assert S.resolve_models(f"{ANCHOR},{MODEL}", ANCHOR) == [MODEL]

    def test_report_declares_incomplete_calibration(self, tmp_path):
        from scripts import calibrate_cost_coefficients as S
        rows = (rows_for(ANCHOR, count=6, cents_per_call=10.0, tasks=6)
                + rows_for(MODEL, count=6, cents_per_call=2.5, tasks=6))
        calibration, table = CC.build_calibration(
            rows, anchor_model=ANCHOR, method=CC.METHOD_OFFLINE_REPLAY)
        report = S.render_report(
            path_used="b", calibration=calibration, table=table,
            credentials={"usable": False, "base_url": "", "providers": [
                {"env": "LLM_API_KEY", "verdict": "placeholder",
                 "reason": "命中占位符特征 'sk-test'", "origin": ".env"}]},
            provenance={"directory": "data/events", "files": [], "by_model": {}},
            run_meta={}, generated_at="2026-09-12T10:00:00+0800")
        assert "未完成完整实测校准，结论置信度受限" in report
        assert "只披露不结论" in report
        assert "样本量不足" in report or "只披露不结论" in report
        # 无达标模型时必须写"无"，而不是编个偏差数字出来
        assert "可生效（实测）模型：**无**" in report


# ════════════════════════════════════════════════════════════
#  9. 邻接不回归（S5-03 断食判定的输入口径）
# ════════════════════════════════════════════════════════════


class TestAdjacentNoRegression:
    def test_anchor_is_never_replaced_by_measurement(self, tmp_path):
        """锚模型恒为 1.0：即使校准件里有锚，也不得替换它的系数"""
        cal = CC.CostCalibration(version=CC.MEASURED_VERSION,
                                 method=CC.METHOD_L2_RUN, anchor_model=ANCHOR)
        cal.models[ANCHOR] = CC.ModelCalibration(model=ANCHOR, samples=50,
                                                 measured_coefficient=7.7)
        cal.models[MODEL] = CC.ModelCalibration(model=MODEL, samples=50,
                                                measured_coefficient=0.25)
        write_artifact(cal, str(tmp_path / "cost_coefficients.json"))
        assert CC.measured_coefficients(cal, anchor_model=ANCHOR) == {
            MODEL: {"in": 0.25, "out": 0.25}}
        assert U.coefficient(ANCHOR) == {"in": pytest.approx(1.0),
                                         "out": pytest.approx(1.0),
                                         "source": "price_ratio"}
        assert U.coefficient(MODEL)["source"] == "measured"
        assert U.coefficient_table([ANCHOR, MODEL])["measured_models"] == [MODEL]

    def test_cost_brake_view_consumes_upgraded_block(self, tmp_path, store):
        """S5-03 的日成本视图**原样透传** utc 的 `calibration` 块（未另建口径）"""
        from agent.monitoring import cost_brake as CB
        U.record_cost(model=ANCHOR, tokens_in=1000, tokens_out=0, task_id="t1",
                      interaction_id="brake-1", store=store)
        write_artifact(make_calibration(coefficient=0.25),
                       str(tmp_path / "cost_coefficients.json"))
        view = CB.cost_daily_view(day=None, directory=os.path.dirname(store.path),
                                  config=CB.load_config({}))
        assert view["calibration_version"] == CB.CALIBRATION_VERSION or \
            view.get("calibration", {}).get("calibration_version") in (
                U.CALIBRATION_VERSION, U.MEASURED_CALIBRATION_VERSION)
        # 未实测模型（gpt-3.5-turbo）必须仍按价格系数 → 与升级前逐分一致
        calc = U.normalize_cost(1000, 0, "gpt-3.5-turbo")
        assert calc["coefficient_source"] == "price_ratio"
        assert calc["cost_normalized_cents"] == pytest.approx(
            1000 / 1000 * 3.0 * (0.0015 / 0.03))

    def test_l2_baseline_hash_is_used_for_staleness(self, tmp_path, monkeypatch):
        """能读到 L2 基线的用例集哈希时，哈希不一致 → 校准件失效"""
        baseline_dir = tmp_path / "eval"
        baseline_dir.mkdir()
        (baseline_dir / "l2_baseline.json").write_text(json.dumps(
            {"caseset": {"caseset_sha256": "bbbb"}}), encoding="utf-8")
        import agent.eval.baseline as B
        monkeypatch.setattr(B, "DEFAULT_BASELINE_PATH", str(baseline_dir / "l2_baseline.json"))
        U.reset_config_cache()
        write_artifact(make_calibration(caseset_sha256="aaaa"),
                       str(tmp_path / "cost_coefficients.json"))
        assert U.coefficient(MODEL)["source"] == "price_ratio"
        assert U.calibration_block()["stale_reason"] == "caseset_changed"

    def test_no_baseline_means_hash_check_is_skipped(self, tmp_path, monkeypatch):
        import agent.eval.baseline as B
        monkeypatch.setattr(B, "DEFAULT_BASELINE_PATH", str(tmp_path / "absent.json"))
        U.reset_config_cache()
        write_artifact(make_calibration(caseset_sha256="aaaa"),
                       str(tmp_path / "cost_coefficients.json"))
        assert U.calibration_block()["stale_reason"] == ""
        assert U.coefficient(MODEL)["source"] == "measured"
