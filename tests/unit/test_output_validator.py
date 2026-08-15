"""TASK-07 输出验证门控测试

覆盖验收：
- 规则层命中 4 类失败样例（空/超长/缺字段/PII）+ 格式不符；
- conservative_mode=true 只记录、用户响应零影响；
- 非保守 + enable_retry 走重试；重试仍失败返回原响应（降级保底）；
- LLM 层未配置时静默降级规则；默认路径（rule_based）零额外 LLM 调用；
- 验证器抛错主链路正常（降级验证）。
"""
import os
import sys
import pytest
from unittest.mock import MagicMock

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.verification.output_validator import (
    OutputValidator,
    ValidatorMode,
    Verdict,
    build_validator_from_config,
    reset_config_cache,
)


@pytest.fixture(autouse=True)
def _clean_state():
    reset_config_cache()
    yield


def _validator(**kwargs):
    """构造默认验证器（保守模式 + 规则层）"""
    defaults = dict(enabled=True, conservative_mode=True,
                    mode=ValidatorMode.RULE_BASED, max_output_length=100,
                    enable_retry=True, max_retries=1)
    defaults.update(kwargs)
    return OutputValidator(**defaults)


class TestRuleLayer:
    """规则层 4 类失败样例 + 通过样例"""

    def test_valid_response_ok(self):
        v = _validator()
        verdict = v.validate("这是一个正常的回答内容", "text_response")
        assert verdict.ok is True
        assert verdict.issues == []
        assert verdict.score == 100.0

    def test_empty_output_hit(self):
        v = _validator()
        for empty in (None, "", "   "):
            verdict = v.validate(empty, "text_response")
            assert verdict.ok is False
            assert "empty_output" in verdict.issues
            assert verdict.score == 0.0

    def test_too_long_output_hit(self):
        v = _validator(max_output_length=5)
        verdict = v.validate("这个回答实在是太长了", "text_response")
        assert verdict.ok is False
        assert "output_too_long" in verdict.issues

    def test_missing_required_field_hit(self):
        """summary_report 缺关键字段（结论/总结/摘要）"""
        v = _validator()
        verdict = v.validate("这份报告没有包含任何规定标记内容", "summary_report")
        assert verdict.ok is False
        assert "missing_required_field" in verdict.issues

    def test_summary_with_marker_ok(self):
        v = _validator()
        verdict = v.validate("结论：一切正常", "summary_report")
        assert verdict.ok is True

    def test_pii_leak_hit(self):
        """手机号未被遮盖 → PII 泄漏命中（复用 OutputGuard 规则）"""
        v = _validator()
        verdict = v.validate("请拨打 13812345678 联系我", "text_response")
        assert verdict.ok is False
        assert "pii_leak" in verdict.issues

    def test_unsupported_task_type_hit(self):
        """格式不符：task_type 不在 supported_types 声明内"""
        v = _validator(supported_types=("text_response",))
        verdict = v.validate("随便写点什么内容", "unknown_type")
        assert verdict.ok is False
        assert "unsupported_task_type" in verdict.issues


class TestConservativeMode:
    """保守模式：只记录，用户响应零影响"""

    def test_conservative_returns_original_response(self, monkeypatch):
        """失败样例在保守模式下仍返回原响应（零影响）"""
        v = _validator(conservative_mode=True)
        recorded = []
        monkeypatch.setattr(v, "_record", lambda verdict: recorded.append(verdict))
        original = "请拨打 13812345678 联系我"  # PII 泄漏样例
        final, verdict = v.check_and_act(original, "text_response")
        assert final == original
        assert verdict.ok is False
        assert len(recorded) == 1  # 已记录 verdict

    def test_conservative_records_metrics(self, monkeypatch):
        """conservative 模式记录 learning.eval.* 指标"""
        collector = MagicMock()
        monkeypatch.setattr("agent.verification.output_validator.get_metrics_collector",
                            lambda: collector)
        v = _validator(conservative_mode=True)
        v.check_and_act("", "text_response")  # 空输出 → 失败
        assert collector.increment_counter.call_count >= 2
        names = [c.args[0] for c in collector.increment_counter.call_args_list]
        assert "learning.eval.total" in names
        assert "learning.eval.failed" in names


class TestRetryPath:
    """非保守 + enable_retry：重试一次；仍失败返回原响应（不阻断）"""

    def test_retry_once_then_ok(self):
        """首次失败 → retry_fn 重试成功 → 返回重试结果"""
        calls = {"n": 0}

        def retry_fn():
            calls["n"] += 1
            return "重试后的合格回答内容"

        v = _validator(conservative_mode=False, enable_retry=True, max_retries=1)
        final, verdict = v.check_and_act(
            "请拨打 13812345678 联系我", "text_response", retry_fn=retry_fn)
        assert calls["n"] == 1
        assert final == "重试后的合格回答内容"
        assert verdict.ok is True
        assert verdict.retried is True

    def test_retry_still_fails_returns_original(self):
        """重试仍失败 → 返回原响应（降级保底，不丢弃）"""
        def retry_fn():
            return ""  # 重试产出空输出，仍失败

        v = _validator(conservative_mode=False, enable_retry=True, max_retries=1)
        original = "请拨打 13812345678 联系我"
        final, verdict = v.check_and_act(original, "text_response", retry_fn=retry_fn)
        assert final == original
        assert verdict.ok is False
        assert "retry_still_failed" in verdict.issues
        assert verdict.retried is True

    def test_no_retry_fn_returns_original(self):
        """非保守但无重试来源：不丢弃响应"""
        v = _validator(conservative_mode=False, enable_retry=True)
        original = "请拨打 13812345678 联系我"
        final, verdict = v.check_and_act(original, "text_response")
        assert final == original
        assert verdict.ok is False


class TestLLMLayerReserved:
    """LLM-as-Judge 接口预留：默认规则层零 LLM 调用；llm_based 未配置静默降级"""

    def test_default_mode_rule_based_no_llm(self):
        """默认路径（rule_based）不触发任何 LLM 调用"""
        llm_client = MagicMock()
        v = _validator(mode=ValidatorMode.RULE_BASED, llm_client=llm_client,
                       llm_config={"enabled": True, "model": "gpt-4"})
        v.validate("正常回答", "text_response")
        llm_client.assert_not_called()

    def test_llm_based_unconfigured_falls_back_to_rules(self):
        """mode=llm_based 但 client/配置缺失 → 静默降级到规则层"""
        v = _validator(mode=ValidatorMode.LLM_BASED, llm_client=None, llm_config=None)
        verdict = v.validate("请拨打 13812345678 联系我", "text_response")
        # 静默降级：仍走规则层命中 PII
        assert "pii_leak" in verdict.issues
        assert verdict.mode == "llm_based"

    def test_llm_based_configured_reserved_interface(self):
        """mode=llm_based 且已配置：接口预留（本期不实现调用），规则层兜底"""
        v = _validator(mode=ValidatorMode.LLM_BASED,
                       llm_client=MagicMock(), llm_config={"enabled": True})
        verdict = v.validate("请拨打 13812345678 联系我", "text_response")
        assert "pii_leak" in verdict.issues


class TestDegradeSafety:
    """验证器异常 → 主链路正常（降级验证）"""

    def test_validator_exception_returns_original(self):
        """validate 抛错：check_and_act 降级返回原响应 + degraded 标记"""
        v = _validator()
        v.validate = MagicMock(side_effect=RuntimeError("boom"))
        original = "原回答"
        final, verdict = v.check_and_act(original, "text_response")
        assert final == original
        assert verdict.ok is True
        assert verdict.degraded is True

    def test_retry_fn_exception_breaks_gracefully(self):
        """retry_fn 抛错：跳出重试，返回原响应（不崩溃）"""
        def retry_fn():
            raise RuntimeError("llm 挂了")

        v = _validator(conservative_mode=False, enable_retry=True)
        original = "请拨打 13812345678 联系我"
        final, verdict = v.check_and_act(original, "text_response", retry_fn=retry_fn)
        assert final == original
        assert verdict.ok is False

    def test_score_penalties_accumulate(self):
        """多问题叠加扣分且下限为 0"""
        v = _validator(max_output_length=10)
        verdict = v.validate("请拨打 13812345678 联系我，这是很长很长的回答内容",
                             "text_response")
        assert verdict.ok is False
        assert verdict.score < 100.0


class TestConfigBuild:
    """build_validator_from_config：默认构建走保守模式 + 规则层"""

    def test_build_default_conservative_rule_based(self, monkeypatch):
        monkeypatch.setattr("agent.verification.output_validator.load_validator_config",
                            lambda: {"conservative_mode": True, "mode": "rule_based",
                                     "enabled": True})
        v = build_validator_from_config()
        assert v.conservative_mode is True
        assert v.mode == ValidatorMode.RULE_BASED
