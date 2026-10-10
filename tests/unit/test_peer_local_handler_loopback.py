"""镜内真推理 handler × 真实 LocalLLM 适配器的 HTTP 回环 E2E（不装 Ollama）

【为什么有这份守卫（不这样会怎样）】
    `test_subagent_peer_local_handler.py` 用的是**替身 LLM**，只证明 handler 的映射/失败语义，
    不证明"handler → 真实适配器 → 真实 HTTP → 解析"这条链真的通。
    本文件起一个 **Ollama 形状的回环服务**（`GET /api/tags` + `POST /api/generate`），
    用**真实** `LocalLLMAdapter(LocalLLM(...))` 驱动 `peer_local_handler.run`，把系统边界内的
    真实代码路径（aiohttp POST + JSON 解析 + run_sync 同步桥）纳入可复算证据。

【它证明什么、不证明什么（如实）】
    · 证明：镜内 handler 在"本地服务可达且有响应"时产出真实记录；服务 500 / 不可达时 fail-closed。
    · 不证明：模型质量与真实 Ollama（本环境无 Ollama、无模型）—— 那是目标环境的 E2E。

不 import app_server；只监听 127.0.0.1 的临时端口。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent.subagent import peer_local_handler as h
from agent.subagent.local_inference import LocalLLMAdapter
from core.local_llm import LocalLLM


class _OllamaStub(BaseHTTPRequestHandler):
    """最小 Ollama 形状：GET /api/tags 探活；POST /api/generate 回 JSON response"""

    response_text = '{"status": "done", "summary": "loopback"}'
    fail = False

    def log_message(self, *args):  # 静音（避免测试输出噪音）
        return

    def _send(self, code, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send(200, {"models": []})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if _OllamaStub.fail:
            self._send(500, {"error": "boom"})
            return
        self._send(200, {"response": _OllamaStub.response_text})


@pytest.fixture()
def ollama_stub():
    _OllamaStub.fail = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        _OllamaStub.fail = False


def _adapter(api_base):
    return LocalLLMAdapter(LocalLLM(engine="ollama", model="stub-model",
                                   api_base=api_base), model="stub-model")


class TestLoopback:
    def test_真实适配器经http产出记录(self, ollama_stub, monkeypatch):
        monkeypatch.setattr(h, "_resolve_llm", lambda: _adapter(ollama_stub))
        rec = h.run({"goal": "演示目标"})
        assert rec["status"] == "done"
        assert rec["summary"] == "loopback"
        assert rec["channel_meta"] == {"llm_used": True, "provider": "local",
                                       "model": "stub-model"}

    def test_服务500时fail_closed(self, ollama_stub, monkeypatch):
        _OllamaStub.fail = True
        monkeypatch.setattr(h, "_resolve_llm", lambda: _adapter(ollama_stub))
        with pytest.raises(RuntimeError):
            h.run({"goal": "g"})

    def test_服务不可达时fail_closed(self, monkeypatch):
        # 127.0.0.1:1 基本不可能有监听 ⇒ 探活失败 ⇒ generate 返回 None ⇒ handler 抛错
        monkeypatch.setattr(h, "_resolve_llm", lambda: _adapter("http://127.0.0.1:1"))
        with pytest.raises(RuntimeError):
            h.run({"goal": "g"})

