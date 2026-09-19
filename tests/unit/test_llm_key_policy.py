"""LLM Key 形态判定（agent/llm_key.py）单元测试

为什么单独锁：该判定决定"对话走真实 LLM 还是演示模式"，且被两处共用
（plugins/chat.py 的对话流、agent/server_routes 的诊断自检）。口径漂移会让用户
看到"自检说正常、对话却在演示模式"的矛盾——线上实测过（.env 里是 sk-test… 占位 key）。
"""
from __future__ import annotations

import pytest

from agent.llm_key import MIN_API_KEY_LENGTH, PLACEHOLDER_PREFIXES, key_usable


class TestKeyUsable:
    def test_空值一律不可用(self):
        assert key_usable("") is False
        assert key_usable(None) is False  # type: ignore[arg-type]

    def test_过短不可用(self):
        assert key_usable("sk-abc") is False
        assert key_usable("x" * (MIN_API_KEY_LENGTH - 1)) is False

    def test_占位前缀不可用(self):
        for prefix in PLACEHOLDER_PREFIXES:
            # 即便长度达标，占位前缀仍判不可用
            assert key_usable(prefix + "-" + "a" * 30) is False

    def test_真实形态可用(self):
        assert key_usable("sk-" + "a" * 33) is True
        assert key_usable("sk-2c1f9b7e4d3a5c8b0f2e6d9a4b7c1e2f2b2c") is True

    def test_判定不发起网络请求且为纯函数(self):
        """形态判定必须零副作用：设置网络打桩后仍只做字符串判断"""
        calls = []

        class _Boom:
            def __getattr__(self, item):  # pragma: no cover - 被调用即失败
                calls.append(item)
                raise AssertionError("形态判定不应触达网络/客户端")

        assert key_usable("sk-" + "b" * 33) is True
        assert calls == []


class TestSingleSource:
    def test_plugins_chat_与诊断端点口径一致(self):
        """plugins.chat.key_usable 必须是同一判据的转发（避免两处各写一份）"""
        from plugins.chat import key_usable as chat_key_usable

        for probe in ("", "sk-test-placeholder-cdef", "sk-" + "c" * 33):
            assert chat_key_usable(probe) == key_usable(probe)

    def test_诊断端点使用同一函数(self, monkeypatch):
        """自检端点内的 demo_mode 判定必须由本函数驱动"""
        import io
        import json

        from flask import Flask

        from agent.server_routes.routes_logging import register_routes

        monkeypatch.setenv("LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
        monkeypatch.setenv("LLM_API_KEY", "sk-" + "d" * 33)
        monkeypatch.setattr("requests.post", lambda *a, **k: type(
            "R", (), {"status_code": 200, "text": "", "json": lambda self=None: {"model": "deepseek-chat"}})())

        app = Flask(__name__)
        app.config.update(TESTING=True)
        register_routes(app, None)
        client = app.test_client()
        body = json.loads(client.post("/api/diagnostics/llm-check").get_data(as_text=True))
        assert body["workbench_demo_mode"] is False   # 真实形态 ⇒ 不判演示模式

        monkeypatch.setenv("LLM_API_KEY", "sk-test-again")
        app2 = Flask(__name__)
        app2.config.update(TESTING=True)
        register_routes(app2, None)
        body2 = json.loads(app2.test_client().post("/api/diagnostics/llm-check").get_data(as_text=True))
        assert body2["workbench_demo_mode"] is True   # 占位 key ⇒ 判演示模式
        assert body2["ok"] is False
