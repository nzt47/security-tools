"""LLM 连通性自检端点测试（/api/diagnostics/llm-check）

为什么有这个端点：让用户为了"key 是否有效 / 模型名是否被识别"去命令行手搓
curl/Invoke-WebRequest，既麻烦又要经手密钥。自检在服务端跑同一条判据，结构化返回：
  - 配置摘要（**密钥只回掩码**，绝不回原文）；
  - 工作台判定（与 plugins/chat.py::key_usable 同源 ⇒ 不会与"演示模式"矛盾）；
  - 最小真实调用结果（HTTP 状态/耗时/上游原文）+ 修复建议（hints）。
"""
from __future__ import annotations

import pytest
from flask import Flask

from agent.server_routes.routes_logging import _llm_check_hints, _mask_secret, register_routes


class FakeResp:
    def __init__(self, status_code: int, text: str = "", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture
def client(monkeypatch):
    """构造 Flask app + 注入 LLM 环境变量；requests.post 由各用例 monkeypatch"""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-" + "a" * 30)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app = Flask(__name__)
    app.config.update(TESTING=True)
    register_routes(app, None)
    return app.test_client()


def _fake_post(monkeypatch, resp=None, exc=None):
    calls = []

    def _post(url, **kw):
        calls.append({"url": url, **kw})
        if exc:
            raise exc
        return resp

    monkeypatch.setattr("requests.post", _post)
    return calls


class TestMasking:
    def test_掩码只留前5后4(self):
        assert _mask_secret("sk-abcdefghijklmnop") == "sk-ab***mnop"
        assert _mask_secret("") == ""
        assert _mask_secret("short") == "sh***"

    def test_自检响应不含密钥原文(self, client, monkeypatch):
        _fake_post(monkeypatch, FakeResp(200, payload={"model": "deepseek-chat", "usage": {"total_tokens": 5}}))
        raw = client.post("/api/diagnostics/llm-check").get_data(as_text=True)
        assert "a" * 30 not in raw  # 密钥原文不得出现
        body = client.post("/api/diagnostics/llm-check").get_json()
        assert body["config"]["api_key_masked"].startswith("sk-a")
        assert body["config"]["api_key_length"] == 33


class TestHappyPath:
    def test_连通_ok_且不是演示模式(self, client, monkeypatch):
        calls = _fake_post(monkeypatch, FakeResp(200, payload={
            "model": "deepseek-chat", "usage": {"total_tokens": 7}}))
        body = client.post("/api/diagnostics/llm-check").get_json()
        assert body["ok"] is True
        assert body["workbench_demo_mode"] is False
        assert body["probe"]["http_status"] == 200
        assert body["probe"]["model"] == "deepseek-chat"
        assert body["probe"]["endpoint"] == "https://api.deepseek.com/v1/chat/completions"
        # 探测请求用最小 token 预算，避免"自检"本身花钱
        assert calls[0]["json"]["max_tokens"] == 5
        assert calls[0]["headers"]["Authorization"].startswith("Bearer sk-")


class TestFailures:
    def test_401_给出_key_无效结论与建议(self, client, monkeypatch):
        _fake_post(monkeypatch, FakeResp(401, '{"error":{"message":"Authentication Fails, Your api key: ****cdef is invalid"}}'))
        body = client.post("/api/diagnostics/llm-check").get_json()
        assert body["ok"] is False
        assert body["probe"]["http_status"] == 401
        assert "Authentication Fails" in body["probe"]["raw_message"]
        hints = " ".join(body["probe"]["hints"])
        assert "API Key 无效" in hints and "sk-test" in hints

    def test_模型名不被识别_提示官方模型名(self, client, monkeypatch):
        _fake_post(monkeypatch, FakeResp(400, '{"error":{"message":"Model Not Exist"}}'))
        body = client.post("/api/diagnostics/llm-check").get_json()
        hints = " ".join(body["probe"]["hints"])
        assert "模型名不被该服务识别" in hints
        assert "deepseek-chat" in hints

    def test_404_提示_base_url_需带_v1(self, client, monkeypatch):
        _fake_post(monkeypatch, FakeResp(404, "Not Found"))
        hints = " ".join(client.post("/api/diagnostics/llm-check").get_json()["probe"]["hints"])
        assert "/v1" in hints

    def test_网络异常_按未能建立请求处理(self, client, monkeypatch):
        _fake_post(monkeypatch, exc=ConnectionError("DNS 解析失败"))
        body = client.post("/api/diagnostics/llm-check").get_json()
        assert body["ok"] is False
        assert "DNS" in body["probe"]["error"]
        assert any("网络" in h or "DNS" in h for h in body["probe"]["hints"])

    def test_未配置_base_url或key时直接给结论_不发请求(self, client, monkeypatch):
        calls = _fake_post(monkeypatch, FakeResp(200))
        monkeypatch.setenv("LLM_BASE_URL", "")
        body = client.post("/api/diagnostics/llm-check").get_json()
        assert body["probe"]["ok"] is False
        assert "LLM_BASE_URL" in body["probe"]["error"]
        assert calls == []


class TestWorkbenchVerdict:
    def test_占位_key_判为演示模式_且整体不ok(self, client, monkeypatch):
        """sk-test… 是线上实际踩过的占位 key：必须判"会进演示模式"，且不因 200 就报 ok"""
        monkeypatch.setenv("LLM_API_KEY", "sk-test-placeholder-cdef")
        _fake_post(monkeypatch, FakeResp(200, payload={"model": "deepseek-chat"}))
        body = client.post("/api/diagnostics/llm-check").get_json()
        assert body["workbench_demo_mode"] is True
        assert "演示模式" in body["workbench_note"]
        # 探测本身（假设上游意外接受）也不能让总体判定为可用
        assert body["ok"] is False


class TestHintsPure:
    def test_unknown_status_无建议(self):
        assert _llm_check_hints(418, "", {"model": "x"}) == []

    def test_5xx_归因上游(self):
        assert any("上游 5xx" in h for h in _llm_check_hints(503, "", {"model": "x"}))
