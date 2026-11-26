# -*- coding: utf-8 -*-
"""E0-2④ token-meter：实测装配量驱动压缩（2026-11-26）

为什么要有这一片：压缩触发与 /api/context/status 此前只量「本会话最近 200 条正文」——
同源，但那是**记忆池的代理指标**，不等于真正装进请求的上下文（系统提示 + 工具 schema +
历史 + 摘要，实测主线光固定开销就上万 token）。DSH 的 token-meter 用实测量，本片补这半个。

本文件钉住四件事：
  1. record/usage 是**唯一读数入口**（触发与界面都只调它），无样本返回 None 不伪造 0；
  2. 触发过阈值才触发；压缩后按**水位**（再长 growth 倍）才允许再压（口径 §7）；
  3. 开关 CP_TOKEN_METER 关掉即回到只按记忆窗口口径（读数仍在）；
  4. /api/context/status 如实披露实测块与最近触发口径，且**不改**既有 percentage 语义。
"""
from __future__ import annotations

import sys
import types

import pytest

from memory import token_meter
from memory.memory_manager import MemoryManager


@pytest.fixture(autouse=True)
def _clean_meter():
    """用例间清空读数（模块级状态；autouse 才能覆盖类内方法）"""
    token_meter.reset()
    yield
    token_meter.reset()


class _Counter:
    """len() 计数：数字可手算，不受 tiktoken 版本影响"""

    def count(self, text):
        return len(text or "")

    def count_messages(self, msgs):
        return sum(len((m or {}).get("content", "") or "") for m in msgs or [])


class _CapturingSummarizer:
    def __init__(self):
        self.seen = []

    def compress(self, messages, strategy="default"):
        self.seen.append(list(messages))
        return "摘要"

    def merge_summaries(self, old_summary, new_summary):
        return new_summary or old_summary

    def should_compress(self, total_tokens, token_limit, threshold=0.8):
        return total_tokens >= int(token_limit * threshold)


@pytest.fixture
def manager(tmp_path):
    mgr = MemoryManager(config={
        "data_dir": str(tmp_path / "memory_data"),
        "token_limit": 1000,
        "compress_threshold": 0.8,
        "llm": {"provider": "openai", "api_key": "sk-test-key-valid-12345", "model": "gpt-4"},
        "async_compress": {"enabled": False},
    })
    mgr._token_counter = _Counter()
    return mgr


class TestReadings:
    def test_无样本返回None不伪造0(self):
        assert token_meter.usage("nobody") is None
        assert token_meter.usage("") is None

    def test_记录后可读出占用率(self):
        snap = token_meter.record("s1", prompt_tokens=40000, window_tokens=131072,
                                  source="chat", overhead_tokens=10000, history_tokens=30000)
        assert snap["prompt_tokens"] == 40000
        assert snap["samples"] == 1
        u = token_meter.usage("s1")
        assert u["window_tokens"] == 131072
        assert u["percentage"] == round(40000 / 131072 * 100, 1)
        assert u["source"] == "chat"
        assert u["overhead_tokens"] == 10000 and u["history_tokens"] == 30000

    def test_分母优先用编排窗口(self):
        token_meter.record("s1", prompt_tokens=100, window_tokens=1000)
        assert token_meter.usage("s1")["percentage"] == 10.0
        # 编排窗口与样本窗口不同时，以编排窗口为准（与 /api/context/status.token_limit 同源）
        assert token_meter.usage("s1", limit_tokens=500)["percentage"] == 20.0

    def test_非法参数不污染既有样本(self):
        token_meter.record("s1", prompt_tokens=100, window_tokens=1000)
        assert token_meter.record("s1", prompt_tokens=-1) is None
        assert token_meter.record("s1", prompt_tokens="nope") is None
        assert token_meter.usage("s1")["prompt_tokens"] == 100


class TestShouldCompress:
    def test_未到阈值不触发(self):
        token_meter.record("s1", prompt_tokens=700, window_tokens=1000)
        assert token_meter.should_compress("s1", limit_tokens=1000, threshold=0.8) is None

    def test_过阈值触发并给出可复核明细(self):
        token_meter.record("s1", prompt_tokens=820, window_tokens=1000)
        got = token_meter.should_compress("s1", limit_tokens=1000, threshold=0.8)
        assert got and got["reason"] == "token_meter"
        assert got["prompt_tokens"] == 820 and got["limit_tokens"] == 1000
        assert got["percentage"] == 82.0

    def test_压缩后水位没长够不重复触发(self):
        token_meter.record("s1", prompt_tokens=900, window_tokens=1000)
        assert token_meter.should_compress("s1", limit_tokens=1000) is not None
        assert token_meter.note_compaction("s1") == 900
        # 同量再来一次：压缩只写摘要、不删消息 ⇒ 量值不会掉，必须靠水位挡住
        token_meter.record("s1", prompt_tokens=900, window_tokens=1000)
        assert token_meter.should_compress("s1", limit_tokens=1000) is None
        # 长够 10%（990）才允许再压
        token_meter.record("s1", prompt_tokens=990, window_tokens=1000)
        assert token_meter.should_compress("s1", limit_tokens=1000) is not None

    def test_开关关掉只停触发不停读数(self, monkeypatch):
        token_meter.record("s1", prompt_tokens=900, window_tokens=1000)
        monkeypatch.setenv(token_meter.ENV_TOKEN_METER, "0")
        assert token_meter.enabled() is False
        assert token_meter.should_compress("s1", limit_tokens=1000) is None
        assert token_meter.usage("s1")["prompt_tokens"] == 900, "读数不该被开关掐掉"


def _msg(session_id, role="user", size=10):
    m = {"role": role, "content": "x" * size}
    if session_id:
        m["session_id"] = session_id
    return m


class TestManagerIntegration:
    def test_记忆窗口没到阈值但实测已满照样触发(self, manager):
        """补的正是代理指标的盲区：正文只有几百 token，真正装进去的已 900/1000"""
        manager._summarizer = _CapturingSummarizer()
        for _ in range(30):
            manager._message_window.append(_msg("A", size=10))
        assert manager._should_compress_now(manager._recent_messages("A"), "A") is False
        token_meter.record("A", prompt_tokens=900, window_tokens=1000, source="chat")
        assert manager._should_compress_now(manager._recent_messages("A"), "A") is True
        assert manager.last_compression_trigger()["source"] == "token_meter"

    def test_压完记水位_立刻再判不会重复压(self, manager):
        manager._summarizer = _CapturingSummarizer()
        for _ in range(3):
            manager._message_window.append(_msg("A", size=10))
        token_meter.record("A", prompt_tokens=900, window_tokens=1000, source="chat")
        assert manager._maybe_request_compress("A") is True
        assert manager._execute_compression("A") is True
        assert token_meter.usage("A")["watermark_tokens"] == 900
        assert manager._should_compress_now(manager._recent_messages("A"), "A") is False

    def test_记忆窗口触发时如实标注来源(self, manager):
        manager._summarizer = _CapturingSummarizer()
        manager._message_window.append(_msg("A", size=900))
        assert manager._should_compress_now(manager._recent_messages("A"), "A") is True
        assert manager.last_compression_trigger()["source"] == "memory_window"

    def test_开关关掉退回旧口径(self, manager, monkeypatch):
        manager._summarizer = _CapturingSummarizer()
        for _ in range(3):
            manager._message_window.append(_msg("A", size=10))
        token_meter.record("A", prompt_tokens=900, window_tokens=1000)
        monkeypatch.setenv(token_meter.ENV_TOKEN_METER, "false")
        assert manager._should_compress_now(manager._recent_messages("A"), "A") is False


class _StatusCounter:
    def count(self, text):
        return len(text or "")


class _StatusSessionMgr:
    def get_messages(self, session_id, limit=50):
        return [{"role": "user", "content": "abcd" * 25}]


class _StatusMemory:
    def __init__(self):
        self._trigger = {}

    @property
    def compress_rounds(self):
        return 0

    def last_compression_trigger(self):
        return dict(self._trigger)


class _StatusYunshu:
    def __init__(self):
        self._memory_token_limit = 131072
        self._memory_token_limit_source = "stub"
        self._memory = _StatusMemory()

    def context_limit_info(self):
        return {"limit_tokens": self._memory_token_limit,
                "limit_source": self._memory_token_limit_source, "available": True}


class _StatusCfg:
    def get(self, section, key, default=None):
        return default

    def set(self, value, section, key):
        return None


@pytest.fixture()
def status_client(monkeypatch):
    from flask import Flask

    from plugins.memory import bp

    yunshu = _StatusYunshu()
    fake = types.ModuleType("app_server")
    fake._Yunshu = yunshu
    fake._session_mgr = _StatusSessionMgr()
    fake._get_current_session_id = lambda: "sess-meter"
    fake._get_token_counter = lambda: _StatusCounter()
    fake._cfg = _StatusCfg()
    fake.logger = types.SimpleNamespace(info=lambda *a, **k: None,
                                       warning=lambda *a, **k: None,
                                       error=lambda *a, **k: None,
                                       debug=lambda *a, **k: None)
    # plugins.memory 的 _view 装饰器在**请求期**才 import 这两个（与既有面板用例同形）
    fake.require_token = lambda f: f
    fake.log_request = lambda *a, **k: (lambda f: f)
    monkeypatch.setitem(sys.modules, "app_server", fake)
    app = Flask(__name__)
    app.register_blueprint(bp)
    app.config.update(TESTING=True)
    return app.test_client(), yunshu


class TestStatusRoute:
    def test_无样本时如实null(self, status_client):
        client, _ = status_client
        body = client.get("/api/context/status").get_json()["data"]
        assert body["metered"] is None
        assert body["metered_percentage"] is None
        assert body["compression_trigger"] is None
        assert "实测" in body["metered_note"]
        # 既有口径**不变**（不把两个数混成一个）
        assert body["percentage_semantics"] == "compress_trigger_window"

    def test_有样本时披露实测块且不改旧语义(self, status_client):
        client, yunshu = status_client
        token_meter.record("sess-meter", prompt_tokens=26214, window_tokens=131072,
                           source="chat")
        yunshu._memory._trigger = {"source": "token_meter", "prompt_tokens": 26214,
                                   "limit_tokens": 131072, "percentage": 20.0}
        body = client.get("/api/context/status").get_json()["data"]
        assert body["metered"]["prompt_tokens"] == 26214
        assert body["metered"]["source"] == "chat"
        assert body["metered_percentage"] == 20.0
        assert body["metered"]["limit_tokens"] == 131072, "分母取编排窗口（同一口径）"
        assert body["compression_trigger"]["source"] == "token_meter"
        assert body["percentage_semantics"] == "compress_trigger_window"

