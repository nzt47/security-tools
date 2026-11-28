"""E3-4 路由：/api/voice/transcribe 与 /api/context/translate（默认关）。"""
from __future__ import annotations

import io
import json

import pytest
from flask import Flask

from agent.context import cot_translate as C
from agent.server_routes.routes_voice import register_routes
from agent.voice import stt


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv(stt.ENABLED_ENV, raising=False)
    monkeypatch.delenv(C.ENABLED_ENV, raising=False)
    for key in (stt.BASE_URL_ENV, stt.KEY_ENV):
        monkeypatch.delenv(key, raising=False)
    C.reset_cache_for_tests()
    app = Flask(__name__)
    app.config.update(TESTING=True)
    register_routes(app)
    return app.test_client()


def _post_audio(client, data=b"AUDIO", mime="audio/webm", name="a.webm"):
    return client.post("/api/voice/transcribe",
                       data={"audio": (io.BytesIO(data), name, mime)},
                       content_type="multipart/form-data")


class TestSttRoute:
    def test_默认关_200_disabled(self, client):
        body = _post_audio(client).get_json()
        assert body["ok"] is False and body["code"] == "disabled"

    def test_缺音频_400(self, client, monkeypatch):
        monkeypatch.setenv(stt.ENABLED_ENV, "1")
        r = client.post("/api/voice/transcribe", data={}, content_type="multipart/form-data")
        assert r.status_code == 400 and r.get_json()["code"] == "missing_audio"

    def test_未配置_503(self, client, monkeypatch):
        monkeypatch.setenv(stt.ENABLED_ENV, "1")
        r = _post_audio(client)
        assert r.status_code == 503 and r.get_json()["code"] == "stt_unavailable"

    def test_非音频_415(self, client, monkeypatch):
        monkeypatch.setenv(stt.ENABLED_ENV, "1")
        r = _post_audio(client, mime="application/octet-stream")
        assert r.status_code == 415 and r.get_json()["code"] == "bad_mime"

    def test_超体积_413(self, client, monkeypatch):
        monkeypatch.setenv(stt.ENABLED_ENV, "1")
        monkeypatch.setattr(stt, "MAX_AUDIO_BYTES", 4)
        r = _post_audio(client, data=b"1234567890")
        assert r.status_code == 413 and r.get_json()["code"] == "too_large"

    def test_成功_回文本(self, client, monkeypatch):
        monkeypatch.setenv(stt.ENABLED_ENV, "1")
        monkeypatch.setenv(stt.BASE_URL_ENV, "https://stt.example/v1")
        monkeypatch.setenv(stt.KEY_ENV, "sk-x")
        monkeypatch.setattr(stt, "_http_post",
                            lambda *a, **k: (200, b'{"text":"\xe4\xbd\xa0\xe5\xa5\xbd"}'))
        body = _post_audio(client).get_json()
        assert body["ok"] is True and body["text"] == "你好"

    def test_上游错误_502_不回原文(self, client, monkeypatch):
        monkeypatch.setenv(stt.ENABLED_ENV, "1")
        monkeypatch.setenv(stt.BASE_URL_ENV, "https://stt.example/v1")
        monkeypatch.setenv(stt.KEY_ENV, "sk-x")
        monkeypatch.setattr(stt, "_http_post", lambda *a, **k: (500, b"SECRET-BOOM"))
        r = _post_audio(client)
        assert r.status_code == 502
        assert "SECRET-BOOM" not in json.dumps(r.get_json(), ensure_ascii=False)


class TestTranslateRoute:
    def test_默认关_200_disabled(self, client):
        body = client.post("/api/context/translate", json={"text": "hi"}).get_json()
        assert body["ok"] is False and body["code"] == "disabled"

    def test_成功(self, client, monkeypatch):
        monkeypatch.setenv(C.ENABLED_ENV, "1")

        class _LLM:
            def chat(self, messages, **kwargs):
                return "你好"

        from agent.context import session_title

        monkeypatch.setattr(session_title, "build_llm", lambda timeout=0: _LLM())
        body = client.post("/api/context/translate",
                           json={"text": "hi", "target": "zh"}).get_json()
        assert body["ok"] is True and body["text"] == "你好" and body["cached"] is False

    def test_目标语言非法_400(self, client, monkeypatch):
        monkeypatch.setenv(C.ENABLED_ENV, "1")
        r = client.post("/api/context/translate", json={"text": "hi", "target": "fr"})
        assert r.status_code == 400 and r.get_json()["code"] == "bad_target"

    def test_异常_500_固定文案(self, client, monkeypatch):
        monkeypatch.setenv(C.ENABLED_ENV, "1")

        def _boom(*a, **k):
            raise RuntimeError("SECRET-BOOM")

        monkeypatch.setattr(C, "translate", _boom)
        r = client.post("/api/context/translate", json={"text": "hi"})
        assert r.status_code == 500
        assert "SECRET-BOOM" not in json.dumps(r.get_json(), ensure_ascii=False)
