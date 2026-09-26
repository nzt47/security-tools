# -*- coding: utf-8 -*-
"""F3-2：流式链路的 usage 可观测性（**不发起任何真实网络调用**）

对照卡 F3 实测发现的盲区：工作台 SSE（plugins/chat.py 的流式链路）**测不到**
prompt_cache_hit_tokens —— 主线（非流式）能算出 3.72%/96.48%，工作台那一列只能写 n/a。

本文件锁定修复后的四条不变式（全部用假 client 打桩，零出网）：

1. 流式请求**显式**带 stream_options={"include_usage": True}（可被上游拒绝时降级重试）；
2. 最后一个 chunk 上的 usage（choices 非空 **或** 为空数组）都能落进
   **与主线同一条** LLM 监控记录（同一个 LLMMonitor 环形缓冲区 / 同一套字段）；
3. 服务端**不报** usage 时安全降级：不抛异常、不丢内容、记录照落（usage_available=False）；
4. 客户端中断（关页面 ⇒ 生成器被关闭）时记录仍然落库，且不把中断变成异常。

另锁一条回归：非流式路径的 create 记录行为不变（仍然立即落库且带 usage）。
详见 docs/audit_skill_governance/F3-2.md。
"""
from __future__ import annotations

import types

import pytest

import agent.llm_monitor as lm
from memory.llm_service import LLMService


# ── 假 openai 运行时（形状与 openai>=1.x 的 chunk 对齐） ──────────────

def _delta(content=None, tool_calls=None, reasoning_content=None):
    return types.SimpleNamespace(content=content, tool_calls=tool_calls,
                                 reasoning_content=reasoning_content)


def _chunk(content=None, finish=None, usage=None, empty_choices=False):
    choices = [] if empty_choices else [
        types.SimpleNamespace(delta=_delta(content), finish_reason=finish, index=0)]
    return types.SimpleNamespace(choices=choices, usage=usage)


def _usage(prompt=1000, completion=7, hit=640, miss=360):
    return types.SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion,
        total_tokens=prompt + completion,
        prompt_tokens_details=types.SimpleNamespace(cached_tokens=hit),
        completion_tokens_details=types.SimpleNamespace(reasoning_tokens=0),
        prompt_cache_hit_tokens=hit, prompt_cache_miss_tokens=miss,
    )


class _FakeStream:
    """最小 Stream 替身：可迭代、可 close、可被中断"""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._i = 0
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        if self._i >= len(self._chunks):
            raise StopIteration
        c = self._chunks[self._i]
        self._i += 1
        return c

    def close(self):
        self.closed = True


class _FakeCreate:
    def __init__(self, stream=None, response=None):
        self.stream = stream
        self.response = response
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.stream if kwargs.get("stream") else self.response


def _service(create: _FakeCreate) -> LLMService:
    """构造 LLMService 并**预置**假 client

    预置的目的：让 agent.llm_monitor 的 _get_client 补丁去包**这个假 client**，
    从而真正走一遍生产代码里的 _wrapped_create（而不是绕过它）。
    """
    svc = LLMService(provider="deepseek", api_key="sk-" + "x" * 40,
                     model="deepseek-flash", base_url="http://127.0.0.1:1/v1")
    svc._client = types.SimpleNamespace(chat=types.SimpleNamespace(
        completions=types.SimpleNamespace(create=create)))
    return svc


@pytest.fixture
def monitor():
    """装上与 app_server.py 完全一致的监控钩子，用例结束卸载"""
    lm.reset_llm_monitor()
    lm.install_hooks()
    mon = lm.get_monitor()
    mon.clear()
    yield mon
    lm.reset_llm_monitor()


def _only_record(mon) -> dict:
    records, total = mon.get_records(limit=10)
    assert total >= 1, "监控里没有记录 —— 流式链路没有落库"
    return records[0]


class TestStreamUsageLandsInTheSameMonitorRecord:
    def test_流式请求显式带_include_usage(self, monitor):
        create = _FakeCreate(stream=_FakeStream([_chunk(content="x", finish="stop")]))
        svc = _service(create)
        "".join(svc.chat_stream([{"role": "user", "content": "hi"}], system_prompt="sys"))
        assert create.calls, "没有发出请求"
        assert create.calls[0].get("stream_options") == {"include_usage": True}

    def test_末块usage_choices非空时落库且命中率可读(self, monitor):
        """本端点（api.deepseek.com/v1）实测形状：usage 挂在 finish_reason 那一块"""
        create = _FakeCreate(stream=_FakeStream([
            _chunk(content="收"), _chunk(content="到", finish="stop", usage=_usage())]))
        svc = _service(create)
        text = "".join(svc.chat_stream([{"role": "user", "content": "hi"}],
                                       system_prompt="sys"))
        rec = _only_record(monitor)
        assert text == "收到"                      # 内容不受计量改造影响
        assert rec["usage_available"] is True
        assert rec["usage_prompt_tokens"] == 1000
        assert rec["prompt_cache_hit_tokens"] == 640
        assert rec["prompt_cache_miss_tokens"] == 360
        assert rec["cache_reported"] is True
        assert abs(rec["call_cache_hit_ratio"] - 0.64) < 1e-9
        assert rec["source"] == "tool_calling"     # 与整改前同一条表、同一个 source

    def test_尾块choices为空数组时仍取到usage且不丢正文(self, monitor):
        """OpenAI 规范形状：include_usage 时补一个 choices=[] 的尾块"""
        create = _FakeCreate(stream=_FakeStream([
            _chunk(content="A"), _chunk(content="B", finish="stop"),
            _chunk(empty_choices=True, usage=_usage(prompt=500, completion=3, hit=100, miss=400))]))
        svc = _service(create)
        text = "".join(svc.chat_stream([{"role": "user", "content": "hi"}],
                                       system_prompt="sys"))
        rec = _only_record(monitor)
        assert text == "AB"
        assert rec["usage_available"] is True
        assert rec["prompt_cache_hit_tokens"] == 100
        assert rec["prompt_cache_miss_tokens"] == 400

    def test_服务端不报usage时安全降级(self, monitor):
        create = _FakeCreate(stream=_FakeStream([
            _chunk(content="好"), _chunk(content="的", finish="stop")]))
        svc = _service(create)
        text = "".join(svc.chat_stream([{"role": "user", "content": "hi"}],
                                       system_prompt="sys"))
        rec = _only_record(monitor)
        assert text == "好的"                      # 内容一片不丢
        assert rec["usage_available"] is False     # 不猜、不编
        assert rec["prompt_cache_hit_tokens"] == 0
        assert rec["error"] == ""                  # 降级不是错误

    def test_客户端中断仍落记录且不抛异常(self, monitor):
        stream = _FakeStream([_chunk(content="部分"), _chunk(content="内容"),
                              _chunk(content="", finish="stop", usage=_usage())])
        create = _FakeCreate(stream=stream)
        svc = _service(create)
        gen = svc.chat_stream([{"role": "user", "content": "hi"}], system_prompt="sys")
        first = next(gen)
        gen.close()                                # ← 等价于用户关页面
        assert first == "部分"
        rec = _only_record(monitor)
        # 中断时上游不会补 usage ⇒ 记为"未上报"，但记录必须还在（不是丢数据）
        assert rec["usage_available"] is False
        assert rec["error"] == ""
        assert stream.closed is True               # 底层连接被主动放掉

    def test_非流式路径行为不变(self, monitor):
        response = types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(
                content="完整回复", reasoning_content=None, tool_calls=None))],
            usage=_usage(prompt=800, completion=5, hit=700, miss=100))
        create = _FakeCreate(response=response)
        svc = _service(create)
        out = svc.chat([{"role": "user", "content": "hi"}], system_prompt="sys")
        records, total = monitor.get_records(limit=10)
        assert out == "完整回复"
        # _do_chat 的 "chat" 记录 + client.create 层的 "tool_calling" 记录（既有行为）
        sources = {r["source"] for r in records}
        assert "chat" in sources and "tool_calling" in sources
        tc = next(r for r in records if r["source"] == "tool_calling")
        assert tc["usage_available"] is True
        assert tc["prompt_cache_hit_tokens"] == 700
