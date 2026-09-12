"""TASK-S8-04 judge 配置 / 凭证自检 / 结构化判定 单测

覆盖任务书 §四 验收清单的前六条：
- 配置项与凭证自检落地；三态可读（``available`` / ``no_credentials`` / ``disabled``）
- 真实 judge 经既有注入通道生效；**阈值 0.85 边界**（0.84 拒 / 0.85 过 / 0.86 过）
- **`judge_kind` 如实标注**（含具体回落原因）；无凭证时**不得冒充 llm**
- 解析失败按 ``E_UPSTREAM_FORMAT`` 语义处理（不猜、不静默）
- 日志/审计/事件中**无密钥明文**

隔离纪律（S3-02/S3-03 教训）：事件目录 / 灰度目录 / 判定存档一律落 ``tmp_path``，
本文件不触碰运行时区。
"""

from __future__ import annotations

import json

import pytest

from agent.digestion import judge_runtime as JR
from agent.digestion import shadow as SH
from agent.observability import events as events_mod

CAP = "cp.builtin.read_file"
SECRET = "sk-this-is-a-fake-secret-value-0123456789"


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """事件目录 + 灰度目录隔离；并清掉可能存在的凭证环境变量"""
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setenv(SH.SHADOW_DIR_ENV, str(tmp_path / "shadow"))
    for name in ("CP_DIGESTION_JUDGE_ENABLED", "CP_DIGESTION_JUDGE_PROVIDER",
                 "CP_DIGESTION_JUDGE_MODEL", "CP_DIGESTION_JUDGE_DAILY_BUDGET_CENTS",
                 "CP_DIGESTION_JUDGE_THRESHOLD", "CP_DIGESTION_JUDGE_FOLLOW_FASTING",
                 "CP_DIGESTION_JUDGE_SECRET_FILE", "CP_DIGESTION_JUDGE_DOTENV",
                 "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
                 "LLM_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    events_mod.reset_event_stores()
    yield tmp_path
    events_mod.reset_event_stores()


def structured(verdict: str, confidence: float, reason: str = "r") -> str:
    return json.dumps({"verdict": verdict, "confidence": confidence,
                       "reason": reason}, ensure_ascii=False)


# ════════════════════════════════════════════════════════════
#  1. 配置项（enabled 默认关闭；非法值回退默认）
# ════════════════════════════════════════════════════════════


class TestJudgeConfig:
    def test_default_is_disabled(self):
        config = JR.judge_config_from_env({})
        assert config.enabled is False
        assert config.threshold == pytest.approx(SH.JUDGE_THRESHOLD) == 0.85
        assert config.daily_budget_cents == pytest.approx(
            JR.DEFAULT_DAILY_BUDGET_CENTS)
        assert config.follow_fasting is True

    def test_env_enables_and_sets_provider_model(self):
        config = JR.judge_config_from_env({
            JR.JUDGE_ENABLE_ENV: "true",
            SH.JUDGE_PROVIDER_ENV: "openai",
            SH.JUDGE_MODEL_ENV: "gpt-4o-mini",
            JR.JUDGE_BUDGET_ENV: "25",
        })
        assert config.enabled is True
        assert (config.provider, config.model) == ("openai", "gpt-4o-mini")
        assert config.daily_budget_cents == pytest.approx(25.0)
        assert config.source["enabled"].startswith("env:")

    @pytest.mark.parametrize("bad", ["nonsense", "", "  "])
    def test_illegal_enabled_flag_falls_back_to_disabled(self, bad):
        config = JR.judge_config_from_env({JR.JUDGE_ENABLE_ENV: bad})
        assert config.enabled is False

    @pytest.mark.parametrize("bad", ["abc", "1.5", "-0.2", "0"])
    def test_illegal_threshold_falls_back_to_0_85(self, bad):
        config = JR.judge_config_from_env({JR.JUDGE_THRESHOLD_ENV: bad})
        assert config.threshold == pytest.approx(0.85)

    def test_threshold_one_is_accepted(self):
        config = JR.judge_config_from_env({JR.JUDGE_THRESHOLD_ENV: "1"})
        assert config.threshold == pytest.approx(1.0)

    def test_negative_budget_falls_back_to_default(self):
        config = JR.judge_config_from_env({JR.JUDGE_BUDGET_ENV: "-5"})
        assert config.daily_budget_cents == pytest.approx(
            JR.DEFAULT_DAILY_BUDGET_CENTS)

    def test_zero_budget_is_honored(self):
        """``0`` = 一分钱都不允许 —— 是**有效配置**，不得被当成"非法"回退"""
        config = JR.judge_config_from_env({JR.JUDGE_BUDGET_ENV: "0"})
        assert config.daily_budget_cents == pytest.approx(0.0)

    def test_follow_fasting_can_be_turned_off(self):
        config = JR.judge_config_from_env({JR.JUDGE_FOLLOW_FASTING_ENV: "false"})
        assert config.follow_fasting is False

    def test_config_payload_is_json_safe_and_has_no_secret(self):
        config = JR.judge_config_from_env({JR.JUDGE_ENABLE_ENV: "true"})
        text = json.dumps(config.to_dict(), ensure_ascii=False, default=str)
        assert text.startswith("{")
        assert SECRET not in text


# ════════════════════════════════════════════════════════════
#  2. 凭证解析（SecretStore → 环境变量 → .env；只出指纹）
# ════════════════════════════════════════════════════════════


class TestCredentialResolution:
    def test_env_source(self):
        res = JR.resolve_judge_credential(
            "openai", env={"OPENAI_API_KEY": SECRET},
            secret_provider=lambda name: None, dotenv_path="")
        assert res.present is True
        assert res.source == JR.CREDENTIAL_SOURCE_ENV
        assert res.name == "OPENAI_API_KEY"
        assert res.fingerprint.startswith("sha256:")
        assert res.secret == SECRET

    def test_dotenv_source_when_env_absent(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(f"# comment\nOPENAI_API_KEY={SECRET}\n", encoding="utf-8")
        res = JR.resolve_judge_credential(
            "openai", env={}, secret_provider=lambda name: None,
            dotenv_path=str(env_file))
        assert res.source == JR.CREDENTIAL_SOURCE_DOTENV
        assert res.secret == SECRET

    def test_secret_store_wins_over_env_and_dotenv(self, tmp_path):
        """**优先 SecretStore**：三处都有同一个键 ⇒ 必须取密钥存储那一个"""
        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_API_KEY=from-dotenv\n", encoding="utf-8")
        res = JR.resolve_judge_credential(
            "openai", env={"OPENAI_API_KEY": "from-env"},
            secret_provider=lambda name: SECRET if name == "OPENAI_API_KEY" else None,
            dotenv_path=str(env_file))
        assert res.source == JR.CREDENTIAL_SOURCE_SECRET_STORE
        assert res.secret == SECRET

    def test_env_wins_over_dotenv(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_API_KEY=from-dotenv\n", encoding="utf-8")
        res = JR.resolve_judge_credential(
            "openai", env={"OPENAI_API_KEY": "from-env"},
            secret_provider=lambda name: None, dotenv_path=str(env_file))
        assert res.source == JR.CREDENTIAL_SOURCE_ENV
        assert res.secret == "from-env"

    def test_generic_key_requires_matching_provider(self):
        """``LLM_API_KEY`` 只在 provider 与 ``LLM_PROVIDER`` 一致时才算命中"""
        res = JR.resolve_judge_credential(
            "openai", env={"LLM_API_KEY": SECRET, "LLM_PROVIDER": "deepseek"},
            secret_provider=lambda name: None, dotenv_path="")
        assert res.present is False
        assert "不匹配" in res.reason

    def test_generic_key_used_when_provider_matches(self):
        res = JR.resolve_judge_credential(
            "openai", env={"LLM_API_KEY": SECRET, "LLM_PROVIDER": "openai"},
            secret_provider=lambda name: None, dotenv_path="")
        assert res.present is True
        assert res.name == "LLM_API_KEY"

    def test_missing_credential_lists_what_was_tried(self):
        res = JR.resolve_judge_credential(
            "openai", env={}, secret_provider=lambda name: None, dotenv_path="")
        assert res.present is False
        assert res.source == JR.CREDENTIAL_SOURCE_NONE
        assert "OPENAI_API_KEY" in res.reason and "LLM_API_KEY" in res.reason

    def test_local_provider_needs_no_credential(self):
        res = JR.resolve_judge_credential(
            "ollama", env={}, secret_provider=lambda name: None, dotenv_path="")
        assert res.present is True
        assert res.source == "local_endpoint"

    def test_fingerprint_is_stable_and_irreversible(self):
        one = JR.resolve_judge_credential(
            "openai", env={"OPENAI_API_KEY": SECRET},
            secret_provider=lambda name: None, dotenv_path="")
        two = JR.resolve_judge_credential(
            "openai", env={"OPENAI_API_KEY": SECRET},
            secret_provider=lambda name: None, dotenv_path="")
        assert one.fingerprint == two.fingerprint
        assert SECRET not in one.fingerprint
        assert len(one.fingerprint) == len("sha256:") + 12

    def test_payload_and_repr_never_expose_the_secret(self):
        res = JR.resolve_judge_credential(
            "openai", env={"OPENAI_API_KEY": SECRET},
            secret_provider=lambda name: None, dotenv_path="")
        payload = json.dumps(res.to_dict(), ensure_ascii=False)
        assert SECRET not in payload
        assert payload.count("has_secret") == 1
        assert res.to_dict()["has_secret"] is True
        assert SECRET not in repr(res)
        assert "<redacted>" in repr(res)

    def test_secret_store_provider_reads_key_value_file(self, tmp_path):
        path = tmp_path / "secrets.env"
        path.write_text(f"OPENAI_API_KEY={SECRET}\n# c\n", encoding="utf-8")
        lookup = JR.secret_store_provider(str(path))
        assert lookup("OPENAI_API_KEY") == SECRET
        assert lookup("MISSING") is None

    def test_secret_store_provider_reads_json_file(self, tmp_path):
        path = tmp_path / "secrets.json"
        path.write_text(json.dumps({"OPENAI_API_KEY": SECRET}), encoding="utf-8")
        lookup = JR.secret_store_provider(str(path))
        assert lookup("OPENAI_API_KEY") == SECRET

    def test_secret_store_provider_missing_file_is_empty(self, tmp_path):
        lookup = JR.secret_store_provider(str(tmp_path / "nope.env"))
        assert lookup("OPENAI_API_KEY") is None

    def test_parse_env_file_handles_quotes_export_and_comments(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text(
            "# c\n\nexport A=1\nB='two'\nC=\"three\"\nBROKEN\n", encoding="utf-8")
        assert JR.parse_env_file(str(path)) == {"A": "1", "B": "two", "C": "three"}


# ════════════════════════════════════════════════════════════
#  3. 可用性三态（available / no_credentials / disabled）
# ════════════════════════════════════════════════════════════


class TestAvailabilityThreeStates:
    def test_disabled_by_default(self):
        check = JR.judge_self_check(env={}, log=False)
        assert check["state"] == JR.AVAILABILITY_DISABLED
        assert check["kind"] == "deterministic_local(disabled)"
        assert SH.is_llm_kind(check["kind"]) is False

    def test_enabled_without_provider_is_no_credentials(self):
        check = JR.judge_self_check(env={JR.JUDGE_ENABLE_ENV: "true"}, log=False)
        assert check["state"] == JR.AVAILABILITY_NO_CREDENTIALS
        assert SH.JUDGE_PROVIDER_ENV in check["reason"]
        assert check["kind"] == "deterministic_local(no_credentials)"

    def test_enabled_with_provider_but_no_credential(self, tmp_path):
        check = JR.judge_self_check(
            env={JR.JUDGE_ENABLE_ENV: "true", SH.JUDGE_PROVIDER_ENV: "openai",
                 SH.JUDGE_MODEL_ENV: "gpt-4o-mini"},
            secret_provider=lambda name: None,
            dotenv_path=str(tmp_path / "absent.env"), log=False)
        assert check["state"] == JR.AVAILABILITY_NO_CREDENTIALS
        assert check["credential"]["present"] is False
        assert SH.is_llm_kind(check["kind"]) is False

    def test_enabled_with_credential_is_available(self, tmp_path):
        check = JR.judge_self_check(
            env={JR.JUDGE_ENABLE_ENV: "true", SH.JUDGE_PROVIDER_ENV: "openai",
                 SH.JUDGE_MODEL_ENV: "gpt-4o-mini", "OPENAI_API_KEY": SECRET},
            secret_provider=lambda name: None,
            dotenv_path=str(tmp_path / "absent.env"), log=False)
        assert check["state"] == JR.AVAILABILITY_AVAILABLE
        assert check["kind"] == "llm:openai:gpt-4o-mini"
        assert SH.is_llm_kind(check["kind"]) is True
        assert check["credential"]["source"] == JR.CREDENTIAL_SOURCE_ENV

    def test_stub_channel_is_available_without_credentials(self):
        check = JR.judge_self_check(
            config=JR.JudgeConfig(enabled=True, provider="probe", model="fake"),
            env={}, invoke=lambda prompt: structured("equivalent", 0.9), log=False)
        assert check["state"] == JR.AVAILABILITY_AVAILABLE
        assert check["kind"] == "llm:probe:fake"
        assert check["credential"]["source"] == JR.CREDENTIAL_SOURCE_INJECTED

    def test_self_check_makes_no_model_call(self):
        calls = []

        def invoke(prompt):
            calls.append(prompt)
            return structured("equivalent", 0.9)

        JR.judge_self_check(
            config=JR.JudgeConfig(enabled=True, provider="probe", model="fake"),
            env={}, invoke=invoke, log=False)
        assert calls == []

    def test_self_check_payload_has_no_secret(self, tmp_path):
        check = JR.judge_self_check(
            config=JR.JudgeConfig(enabled=True, provider="openai",
                                  model="gpt-4o-mini"),
            env={"OPENAI_API_KEY": SECRET}, secret_provider=lambda name: None,
            dotenv_path=str(tmp_path / "absent.env"), log=False)
        text = json.dumps(check, ensure_ascii=False, default=str)
        assert SECRET not in text
        assert check["credential"]["fingerprint"]

    def test_self_check_fields_are_complete(self):
        check = JR.judge_self_check(env={}, log=False)
        for field in JR.SELF_CHECK_FIELDS:
            assert field in check, field

    def test_self_check_does_not_claim_verified_by_default(self):
        """**"有凭证" ≠ "已验证"**：默认不探针 ⇒ `verified=None`（不报假绿灯）"""
        check = JR.judge_self_check(
            config=JR.JudgeConfig(enabled=True, provider="probe", model="fake"),
            env={}, invoke=lambda prompt: structured("equivalent", 0.9), log=False)
        assert check["state"] == JR.AVAILABILITY_AVAILABLE
        assert check["verified"] is None
        assert check["configured"] is True
        assert "verify=False" in check["probe"]["reason"]

    def test_verify_probe_confirms_a_working_channel(self):
        calls = []

        def invoke(prompt):
            calls.append(prompt)
            return structured("equivalent", 0.9)

        check = JR.judge_self_check(
            config=JR.JudgeConfig(enabled=True, provider="probe", model="fake"),
            env={}, invoke=invoke, log=False, verify=True)
        assert calls, "verify=True 必须真的探一次"
        assert check["verified"] is True
        assert check["state"] == JR.AVAILABILITY_AVAILABLE

    def test_verify_probe_demotes_a_credential_that_does_not_work(self):
        """**凭证填了但无效**（过期/撤销）⇒ 探针失败 ⇒ 降级为 no_credentials

        这是"假绿灯"防线：只看"有没有填"会报 available，而真实调用其实 401。
        """
        def broken(prompt):
            raise RuntimeError("401 Authentication Fails")

        check = JR.judge_self_check(
            config=JR.JudgeConfig(enabled=True, provider="probe", model="fake"),
            env={}, invoke=broken, log=False, verify=True)
        assert check["configured"] is True          # 已填
        assert check["verified"] is False           # 但不可用
        assert check["state"] == JR.AVAILABILITY_NO_CREDENTIALS
        assert SH.is_llm_kind(check["kind"]) is False
        assert "真实调用失败" in check["reason"]
        assert SECRET not in json.dumps(check, ensure_ascii=False, default=str)


# ════════════════════════════════════════════════════════════
#  4. 结构化判定与 0.85 阈值边界（0.84 拒 / 0.85 过 / 0.86 过）
# ════════════════════════════════════════════════════════════


class TestStructuredVerdict:
    def test_judge_threshold_is_0_85(self):
        assert SH.JUDGE_THRESHOLD == 0.85

    @pytest.mark.parametrize("confidence,expected", [
        (0.84, "fail"), (0.85, "pass"), (0.86, "pass"),
        (0.0, "fail"), (1.0, "pass"),
    ])
    def test_confidence_threshold_boundaries(self, confidence, expected):
        judge = SH.LLMJudge(invoke=lambda prompt: structured("equivalent", confidence))
        result = judge.score_structured("a", "b")
        assert result["confidence"] == pytest.approx(confidence)
        assert result["verdict"] == expected
        assert result["format"] == "structured"

    def test_boundary_uses_exact_comparison_with_tolerance(self):
        """``>= 0.85`` 的边界：0.85 过、0.8499 拒（浮点用容差断言，不做等值猜测）"""
        just_below = SH.LLMJudge(
            invoke=lambda prompt: structured("equivalent", 0.8499))
        at_threshold = SH.LLMJudge(
            invoke=lambda prompt: structured("equivalent", 0.85))
        assert just_below.score_structured("a", "b")["verdict"] == "fail"
        assert at_threshold.score_structured("a", "b")["verdict"] == "pass"
        assert at_threshold.score_structured("a", "b")["confidence"] == pytest.approx(
            0.85, abs=1e-9)

    def test_model_verdict_word_is_disclosed_when_it_conflicts(self):
        """模型说 different 但给了高分 ⇒ **如实披露冲突**，不静默改写成通过

        §4.5 的软性门槛按 confidence ≥0.85 判定（本用例即 0.99 ⇒ pass），但模型自己的
        措辞另存 `model_verdict` 并置 `conflict=True` —— 是否采纳由人看得到，而不是被吞掉。
        """
        judge = SH.LLMJudge(invoke=lambda prompt: structured("different", 0.99))
        result = judge.score_structured("a", "b")
        assert result["confidence"] == pytest.approx(0.99)
        assert result["verdict"] == "pass"
        assert result["model_verdict"] == "fail"
        assert result["conflict"] is True
        assert judge.last_structured["conflict"] is True
        assert "不一致" in judge.last_structured["note"]

    @pytest.mark.parametrize("word,expected", [
        ("不等价", "fail"), ("不同", "fail"), ("不通过", "fail"),
        ("等价", "pass"), ("一致", "pass"),
        ("not equivalent", "fail"), ("equivalent", "pass"), ("different", "fail"),
    ])
    def test_negative_words_are_never_read_as_positive(self, word, expected):
        """**否定词不得被读成肯定**（"不等价" 含 "等价" ⇒ 必须先判否定）"""
        assert SH._normalize_verdict(word) == expected

    def test_chinese_verdict_words_are_normalized(self):
        judge = SH.LLMJudge(invoke=lambda prompt: json.dumps(
            {"verdict": "不等价", "confidence": 0.2, "reason": "步骤不同"},
            ensure_ascii=False))
        result = judge.score_structured("a", "b")
        assert result["verdict"] == "fail"
        assert result["model_verdict"] == "fail"
        assert "步骤不同" in result["reason"]

    def test_score_keeps_legacy_contract_and_adds_structured_fields(self):
        judge = SH.LLMJudge(invoke=lambda prompt: structured("equivalent", 0.91))
        result = judge.score("a", "b")
        assert result["score"] == pytest.approx(0.91)
        assert result["kind"] == SH.JUDGE_KIND_LLM
        assert result["verdict"] == "pass" and result["confidence"] == pytest.approx(0.91)

    def test_legacy_score_reply_is_accepted_and_labelled(self):
        judge = SH.LLMJudge(invoke=lambda prompt: '{"score": 0.9, "reason": "x"}')
        result = judge.score_structured("a", "b")
        assert result["verdict"] == "pass"
        assert result["confidence"] == pytest.approx(0.9)
        assert result["format"] == "legacy_score"

    def test_last_structured_is_recorded(self):
        judge = SH.LLMJudge(invoke=lambda prompt: structured("different", 0.2, "why"))
        judge.score_structured("a", "b")
        assert judge.last_structured["verdict"] == "fail"
        assert judge.last_structured["reason"] == "why"

    def test_text_without_any_number_is_a_format_error(self):
        judge = SH.LLMJudge(invoke=lambda prompt: "我觉得它们差不多")
        with pytest.raises(SH.JudgeFormatError) as err:
            judge.score_structured("a", "b")
        assert err.value.error_code == SH.JUDGE_REASON_FORMAT
        assert err.value.reason_code == SH.JUDGE_REASON_FORMAT
        assert judge.format_errors == 1

    def test_structured_without_numeric_confidence_is_a_format_error(self):
        """``confidence: "high"`` 不是数字 ⇒ 不猜（不得折算成 1.0）"""
        judge = SH.LLMJudge(invoke=lambda prompt: json.dumps(
            {"verdict": "equivalent", "confidence": "high"}))
        with pytest.raises(SH.JudgeFormatError):
            judge.score_structured("a", "b")

    def test_failure_reply_is_not_mistaken_for_a_high_score(self):
        """``{"success": false, "error": "429 rate limited"}`` **不得**被读成 1.0

        旧解析器会把 ``429`` 当相似度（>1 ⇒ /100 ⇒ 取 <1.0 上限 = 1.0），把通道
        故障伪装成"语义等价"。S8-04 起失败回复一律 `JudgeUnavailable`。
        """
        class Failing:
            def is_available(self):
                return True

            def generate(self, prompt, **kwargs):
                return {"success": False, "error": "429 rate limited"}

        judge = SH.LLMJudge(adapter=Failing())
        with pytest.raises(SH.JudgeUnavailable):
            judge.score_structured("a", "b")

    def test_format_error_code_matches_normalized_error_code(self):
        """与 §11.6.0 归一错误码**同字**（防漂移：字段值可被下游 grep）"""
        from agent.subagent.channel import E_UPSTREAM_FORMAT
        assert SH.JUDGE_REASON_FORMAT == E_UPSTREAM_FORMAT
        assert JR.JUDGE_REASON_FORMAT == E_UPSTREAM_FORMAT

    def test_parse_judge_verdict_returns_none_when_unparseable(self):
        assert SH.parse_judge_verdict("无法判断") is None
        assert SH.parse_judge_verdict("") is None
        assert SH.parse_judge_verdict(None) is None

    def test_layer_three_passes_at_0_85_and_fails_at_0_84(self):
        """层③ 端到端（`diff_judge`）：0.85 过 / 0.84 拒 —— 与 §4.5 逐字一致"""
        from agent.digestion.sandbox import Observation, diff_judge
        left = Observation(implementation="upstream", steps=["read", "write"])
        right = Observation(implementation="candidate", steps=["read", "write"])
        kind = SH.judge_kind_for("probe", "fake")
        pass_result = diff_judge(left, right, judge=lambda a, b: 0.85,
                                 judge_kind=kind)
        fail_result = diff_judge(left, right, judge=lambda a, b: 0.84,
                                 judge_kind=kind)
        assert pass_result.passed is True
        assert fail_result.passed is False
        assert pass_result.detail["judge_kind"] == "llm:probe:fake"
        assert pass_result.detail["threshold"] == pytest.approx(0.85)

    def test_judge_failure_in_layer_three_is_a_format_error_not_a_pass(self):
        """层③ 解析失败 ⇒ 层不通过且如实标注原因码（**不猜成通过**）"""
        from agent.digestion.sandbox import Observation, diff_judge

        def boom(reference, observed):
            raise SH.JudgeFormatError("E_UPSTREAM_FORMAT: 无法解析")

        result = diff_judge(
            Observation(implementation="u"), Observation(implementation="c"),
            judge=boom, judge_kind=SH.judge_fallback_kind(SH.JUDGE_REASON_FORMAT))
        assert result.passed is False
        assert result.detail["judge_kind"] == "deterministic_local(E_UPSTREAM_FORMAT)"


# ════════════════════════════════════════════════════════════
#  5. judge_kind 精确标注（诚信底线）
# ════════════════════════════════════════════════════════════


class TestJudgeKindHonesty:
    def test_real_judge_kind_carries_provider_and_model(self):
        assert SH.judge_kind_for("openai", "gpt-4o-mini") == "llm:openai:gpt-4o-mini"

    def test_missing_provider_or_model_is_labelled_default(self):
        assert SH.judge_kind_for("", "") == "llm:default:default"

    @pytest.mark.parametrize("code", [
        SH.JUDGE_REASON_DISABLED, SH.JUDGE_REASON_NO_CREDENTIALS,
        SH.JUDGE_REASON_BUDGET_EXCEEDED, SH.JUDGE_REASON_BUDGET_UNREADABLE,
        SH.JUDGE_REASON_FASTING, SH.JUDGE_REASON_LLM_UNAVAILABLE,
        SH.JUDGE_REASON_FORMAT,
    ])
    def test_every_reason_code_has_a_fallback_label_and_note(self, code):
        label = SH.judge_fallback_kind(code)
        assert label == f"deterministic_local({code})"
        assert SH.is_llm_kind(label) is False
        assert JR.JUDGE_REASON_NOTES[code]

    def test_llm_kind_family_covers_legacy_and_precise_labels(self):
        assert SH.is_llm_kind("llm_judge") is True
        assert SH.is_llm_kind("llm:openai:gpt-4o-mini") is True
        assert SH.is_llm_kind("deterministic_local") is False
        assert SH.is_llm_kind("deterministic_local(budget_exceeded)") is False

    def test_no_credentials_never_claims_llm(self):
        """**无凭证时不得冒充 llm**（本任务诚信底线）"""
        runtime = JR.build_judge_runtime(
            JR.JudgeConfig(enabled=True, provider="openai", model="gpt-4o-mini"),
            env={}, secret_provider=lambda name: None, dotenv_path="absent.env")
        assert runtime.availability.state == JR.AVAILABILITY_NO_CREDENTIALS
        assert runtime.is_llm is False
        assert runtime.kind == "deterministic_local(no_credentials)"

    def test_disabled_runtime_reports_disabled_kind(self):
        runtime = JR.build_judge_runtime(env={})
        assert runtime.kind == "deterministic_local(disabled)"
        assert runtime.is_llm is False
        assert runtime.budget.recorded == []


# ════════════════════════════════════════════════════════════
#  6. 无明文凭证（日志/审计/事件/报告）
# ════════════════════════════════════════════════════════════


class TestNoPlaintext:
    def test_runtime_payload_has_no_secret(self, tmp_path):
        runtime = JR.build_judge_runtime(
            JR.JudgeConfig(enabled=True, provider="openai", model="gpt-4o-mini",
                           secret_file=str(tmp_path / "s.env")),
            env={}, secret_provider=lambda name: SECRET if name == "OPENAI_API_KEY" else None,
            dotenv_path=str(tmp_path / "absent.env"))
        assert runtime.availability.state == JR.AVAILABILITY_AVAILABLE
        text = json.dumps(runtime.to_dict(), ensure_ascii=False, default=str)
        assert SECRET not in text
        assert runtime.to_dict()["availability"]["credential"]["fingerprint"]

    def test_resolved_judge_detail_has_no_secret(self, tmp_path):
        runtime = JR.build_judge_runtime(
            JR.JudgeConfig(enabled=True, provider="openai", model="gpt-4o-mini"),
            env={}, secret_provider=lambda name: SECRET if name == "OPENAI_API_KEY" else None,
            dotenv_path=str(tmp_path / "absent.env"))
        text = json.dumps(runtime.resolved.to_dict(), ensure_ascii=False, default=str)
        assert SECRET not in text
        assert runtime.resolved.detail["credential_fingerprint"].startswith("sha256:")

    def test_judge_object_keeps_secret_private(self, tmp_path):
        runtime = JR.build_judge_runtime(
            JR.JudgeConfig(enabled=True, provider="openai", model="gpt-4o-mini"),
            env={}, secret_provider=lambda name: SECRET if name == "OPENAI_API_KEY" else None,
            dotenv_path=str(tmp_path / "absent.env"))
        assert runtime.judge is not None
        public = json.dumps({k: v for k, v in vars(runtime.judge).items()
                             if not k.startswith("_")}, ensure_ascii=False, default=str)
        assert SECRET not in public
        assert runtime.judge._api_key == SECRET      # 私有：只用于交给适配器

    def test_adapter_receives_secret_but_env_is_untouched(self, tmp_path, monkeypatch):
        """凭证经 SecretStore 解析后**显式交给适配器**；进程环境不被写脏"""
        seen = {}

        class CaptureFactory:
            @staticmethod
            def create(provider, model, **kwargs):
                seen["provider"] = provider
                seen["model"] = model
                seen["api_key"] = kwargs.get("api_key")

                class Adapter:
                    def is_available(self):
                        return True

                    def generate(self, prompt, **kwargs):
                        return {"text": structured("equivalent", 0.9),
                                "usage": {"prompt_tokens": 11, "completion_tokens": 7}}

                return Adapter()

        monkeypatch.setattr("agent.model_router.adapters.ModelAdapterFactory",
                            CaptureFactory, raising=False)
        runtime = JR.build_judge_runtime(
            JR.JudgeConfig(enabled=True, provider="openai", model="gpt-4o-mini"),
            env={}, secret_provider=lambda name: SECRET if name == "OPENAI_API_KEY" else None,
            dotenv_path=str(tmp_path / "absent.env"))
        assert runtime.availability.state == JR.AVAILABILITY_AVAILABLE
        assert seen["api_key"] == SECRET
        assert "OPENAI_API_KEY" not in __import__("os").environ
