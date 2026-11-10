"""压缩（summarize）调用的 usage 可观测性（2026-11-10，条目 summarize-usage-observability）。

缺陷：`LLMService._do_summarize` 对外返回**字符串**，llm_monitor 的 summarize 钩子把 str
交给 `create_from_api_call` ⇒ `extract_usage(str)` 恒判不可用 ⇒ `source=summarize` 的
prompt/completion/cache hit 全为 0，压缩的 token 与命中既不在「LLM 缓存命中」卡，也不进成本。

修法：`_do_summarize` 把真响应对象暂存到 `_last_summarize_response`，钩子经
`summarize_response_for_record()` 取真对象去读 usage（异常时暂存为 None，不串用上一次）。
"""
from __future__ import annotations

from memory.llm_service import LLMService
from agent import llm_monitor as lm


class _Msg:
    content = "summary text"


class _Choice:
    message = _Msg()


class _Resp:
    choices = [_Choice()]
    usage = object()  # 真实客户端里是 prompt/completion/cache 明细


class _Client:
    class chat:
        class completions:
            @staticmethod
            def create(**_kwargs):
                return _Resp()


def _service(monkeypatch):
    svc = LLMService.__new__(LLMService)
    svc.model = "deepseek-flash"
    svc.provider = "deepseek"
    svc._last_summarize_response = None
    monkeypatch.setattr(svc, "_is_openai_compat", lambda: True, raising=False)
    monkeypatch.setattr(svc, "_get_client", lambda: _Client(), raising=False)
    return svc


def test_do_summarize_stashes_real_response(monkeypatch):
    svc = _service(monkeypatch)
    out = svc._do_summarize([{"role": "user", "content": "hi"}])
    assert out == "summary text"
    assert isinstance(svc._last_summarize_response, _Resp), "真响应对象必须留给监控读 usage"


def test_do_summarize_clears_stash_before_call(monkeypatch):
    svc = _service(monkeypatch)
    svc._last_summarize_response = object()  # 上一次的残留
    monkeypatch.setattr(svc, "_get_client", lambda: (_ for _ in ()).throw(RuntimeError("boom")), raising=False)
    try:
        svc._do_summarize([{"role": "user", "content": "hi"}])
    except Exception:
        pass
    assert svc._last_summarize_response is None, "失败时不能把上一次的响应当成这一次的 usage"


def test_response_for_record_prefers_real_object():
    class _Svc:
        pass

    real = object()
    assert lm.summarize_response_for_record(_Svc(), real) is real


def test_response_for_record_falls_back_to_stash_for_str():
    class _Svc:
        _last_summarize_response = "stashed"

    assert lm.summarize_response_for_record(_Svc(), "summary text") == "stashed"
    assert lm.summarize_response_for_record(_Svc(), None) == "stashed"


def test_response_for_record_returns_none_when_nothing():
    class _Svc:
        pass

    assert lm.summarize_response_for_record(_Svc(), "summary text") is None
