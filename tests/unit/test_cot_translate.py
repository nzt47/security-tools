"""E3-4：CoT 翻译（开关/目标语言/缓存/上游失败/thinking 关闭）。"""
from __future__ import annotations

import pytest

from agent.context import cot_translate as C


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv(C.ENABLED_ENV, raising=False)
    C.reset_cache_for_tests()
    yield
    C.reset_cache_for_tests()


class _FakeLLM:
    def __init__(self, reply="译文：你好", boom=False):
        self.reply = reply
        self.boom = boom
        self.calls = []

    def chat(self, messages, **kwargs):
        if self.boom:
            raise RuntimeError("upstream down")
        self.calls.append({"messages": messages, **kwargs})
        return self.reply


def _on(monkeypatch):
    monkeypatch.setenv(C.ENABLED_ENV, "1")


class TestGate:
    def test_默认关(self):
        with pytest.raises(C.TranslateError) as exc:
            C.translate("hello")
        assert exc.value.code == "disabled"

    def test_目标语言白名单(self, monkeypatch):
        _on(monkeypatch)
        with pytest.raises(C.TranslateError) as exc:
            C.translate("hello", target="fr")
        assert exc.value.code == "bad_target"

    def test_空文本(self, monkeypatch):
        _on(monkeypatch)
        with pytest.raises(C.TranslateError) as exc:
            C.translate("   ")
        assert exc.value.code == "bad_text"


class TestTranslate:
    def test_成功_去标签_并缓存(self, monkeypatch):
        _on(monkeypatch)
        llm = _FakeLLM("译文：你好")
        first = C.translate("hello", llm=llm)
        assert first == {"text": "你好", "target": "zh", "cached": False}
        second = C.translate("hello", llm=llm)
        assert second["cached"] is True and len(llm.calls) == 1

    def test_关_thinking_且带系统提示(self, monkeypatch):
        _on(monkeypatch)
        llm = _FakeLLM("hi")
        C.translate("你好", target="en", llm=llm)
        call = llm.calls[0]
        assert call["extra_body"] == {"thinking": {"type": "disabled"}}
        assert "英文" in call["system_prompt"]
        assert call["messages"][0]["content"] == "你好"

    def test_输入截断(self, monkeypatch):
        _on(monkeypatch)
        monkeypatch.setattr(C, "MAX_INPUT_CHARS", 5)
        llm = _FakeLLM("ok")
        C.translate("abcdefghij", llm=llm)
        assert llm.calls[0]["messages"][0]["content"] == "abcde"

    def test_输出为空_失败(self, monkeypatch):
        _on(monkeypatch)
        with pytest.raises(C.TranslateError) as exc:
            C.translate("hello", llm=_FakeLLM("   "))
        assert exc.value.code == "translate_failed"

    def test_上游抛错_失败(self, monkeypatch):
        _on(monkeypatch)
        with pytest.raises(C.TranslateError) as exc:
            C.translate("hello", llm=_FakeLLM(boom=True))
        assert exc.value.code == "translate_failed" and exc.value.status == 502

    def test_没有_llm_配置(self, monkeypatch):
        _on(monkeypatch)

        def _none(timeout=0):
            return None

        from agent.context import session_title

        monkeypatch.setattr(session_title, "build_llm", _none)
        with pytest.raises(C.TranslateError) as exc:
            C.translate("hello")
        assert exc.value.code == "llm_unavailable" and exc.value.status == 503

    def test_缓存键稳定(self):
        assert C.cache_key("a", "zh") == C.cache_key("a", "zh")
        assert C.cache_key("a", "zh") != C.cache_key("a", "en")
