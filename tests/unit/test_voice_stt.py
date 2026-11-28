"""E3-4：STT 客户端（开关/体积/MIME/上游错误/文本截断）。"""
from __future__ import annotations

import json

import pytest

from agent.voice import stt


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv(stt.ENABLED_ENV, raising=False)
    monkeypatch.delenv(stt.BASE_URL_ENV, raising=False)
    monkeypatch.delenv(stt.KEY_ENV, raising=False)
    monkeypatch.setenv(stt.MODEL_ENV, "whisper-test")
    yield


def _on(monkeypatch):
    monkeypatch.setenv(stt.ENABLED_ENV, "1")
    monkeypatch.setenv(stt.BASE_URL_ENV, "https://stt.example/v1/")
    monkeypatch.setenv(stt.KEY_ENV, "sk-test")


class TestGate:
    def test_默认关(self):
        assert stt.enabled() is False
        with pytest.raises(stt.SttError) as exc:
            stt.transcribe(b"x", "audio/webm")
        assert exc.value.code == "disabled"

    def test_未配置端点或密钥(self, monkeypatch):
        monkeypatch.setenv(stt.ENABLED_ENV, "1")
        with pytest.raises(stt.SttError) as exc:
            stt.transcribe(b"x", "audio/webm")
        assert exc.value.code == "stt_unavailable"

    def test_configured_判据(self, monkeypatch):
        assert stt.configured() is False
        _on(monkeypatch)
        assert stt.configured() is True


class TestValidation:
    def test_空音频(self, monkeypatch):
        _on(monkeypatch)
        with pytest.raises(stt.SttError) as exc:
            stt.transcribe(b"", "audio/webm")
        assert exc.value.code == "empty_audio"

    def test_超体积(self, monkeypatch):
        _on(monkeypatch)
        monkeypatch.setattr(stt, "MAX_AUDIO_BYTES", 4)
        with pytest.raises(stt.SttError) as exc:
            stt.transcribe(b"12345", "audio/webm")
        assert exc.value.code == "too_large"

    @pytest.mark.parametrize("mime,ok", [
        ("audio/webm", True), ("audio/webm;codecs=opus", True), ("audio/wav", True),
        ("audio/ogg", True), ("video/mp4", False), ("text/plain", False), ("", False),
    ])
    def test_mime_白名单(self, mime, ok):
        assert stt.allowed_mime(mime) is ok

    def test_非音频拒绝(self, monkeypatch):
        _on(monkeypatch)
        with pytest.raises(stt.SttError) as exc:
            stt.transcribe(b"x", "application/octet-stream")
        assert exc.value.code == "bad_mime"


class TestUpstream:
    def test_成功_回文本与模型(self, monkeypatch):
        _on(monkeypatch)
        seen = {}

        def _post(url, headers, body, timeout):
            seen.update({"url": url, "headers": headers, "body": body})
            return 200, json.dumps({"text": "  你好世界  "}).encode()

        out = stt.transcribe(b"AUDIO", "audio/webm", transport=_post)
        assert out["text"] == "你好世界" and out["model"] == "whisper-test"
        assert seen["url"] == "https://stt.example/v1/audio/transcriptions"
        assert seen["headers"]["Authorization"] == "Bearer sk-test"
        assert b'name="model"' in seen["body"] and b"whisper-test" in seen["body"]
        assert b"AUDIO" in seen["body"] and seen["headers"]["Content-Type"].startswith("multipart/form-data")

    def test_上游_4xx_5xx(self, monkeypatch):
        _on(monkeypatch)
        with pytest.raises(stt.SttError) as exc:
            stt.transcribe(b"x", "audio/webm", transport=lambda *a, **k: (500, b"boom"))
        assert exc.value.code == "stt_failed" and exc.value.status == 502

    def test_超时(self, monkeypatch):
        _on(monkeypatch)

        def _boom(*a, **k):
            raise TimeoutError("slow")

        with pytest.raises(stt.SttError) as exc:
            stt.transcribe(b"x", "audio/webm", transport=_boom)
        assert exc.value.code == "stt_timeout" and exc.value.status == 504

    def test_网络错误(self, monkeypatch):
        _on(monkeypatch)

        def _boom(*a, **k):
            raise OSError("down")

        with pytest.raises(stt.SttError) as exc:
            stt.transcribe(b"x", "audio/webm", transport=_boom)
        assert exc.value.code == "stt_failed"

    def test_返回不是_json(self, monkeypatch):
        _on(monkeypatch)
        with pytest.raises(stt.SttError) as exc:
            stt.transcribe(b"x", "audio/webm", transport=lambda *a, **k: (200, b"<html>"))
        assert exc.value.code == "stt_failed"

    def test_返回空文本(self, monkeypatch):
        _on(monkeypatch)
        with pytest.raises(stt.SttError) as exc:
            stt.transcribe(b"x", "audio/webm",
                           transport=lambda *a, **k: (200, b'{"text":"   "}'))
        assert exc.value.code == "stt_failed"

    def test_文本截断(self, monkeypatch):
        _on(monkeypatch)
        monkeypatch.setattr(stt, "MAX_TEXT_CHARS", 3)
        out = stt.transcribe(b"x", "audio/webm",
                             transport=lambda *a, **k: (200, b'{"text":"abcdef"}'))
        assert out["text"] == "abc" and out["truncated"] is True
