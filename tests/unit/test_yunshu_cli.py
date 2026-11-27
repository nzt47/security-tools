"""E4-5 headless CLI（scripts/yunshu_cli.py）契约测试。

判据：只走既有 REST；令牌只从环境/.env 读且绝不出现在输出里；
ask 的 body 带 message/session_id；HTTP 错误只回服务端 error 字段（截断）。
"""
from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import urllib.error

import pytest

_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "yunshu_cli.py"


def _load():
    spec = importlib.util.spec_from_file_location("yunshu_cli_under_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cli():
    return _load()


class TestTokenAndBase:
    def test_环境变量优先(self, cli, monkeypatch):
        monkeypatch.setenv("YUNSHU_API_TOKEN", "tok-from-env")
        assert cli.token_from_env() == "tok-from-env"

    def test_回落_env文件(self, cli, monkeypatch, tmp_path):
        monkeypatch.delenv("YUNSHU_API_TOKEN", raising=False)
        (tmp_path / ".env").write_text('FLASK_API_TOKEN="tok-from-dotenv"\n', encoding="utf-8")
        monkeypatch.setattr(cli, "_repo_root", lambda: str(tmp_path))
        assert cli.token_from_env() == "tok-from-dotenv"

    def test_都没有回空串(self, cli, monkeypatch, tmp_path):
        monkeypatch.delenv("YUNSHU_API_TOKEN", raising=False)
        monkeypatch.setattr(cli, "_repo_root", lambda: str(tmp_path))
        assert cli.token_from_env() == ""

    def test_base_url_环境覆盖且去尾斜杠(self, cli, monkeypatch):
        monkeypatch.setenv("YUNSHU_BASE_URL", "http://example.test:9999/")
        assert cli._base_url() == "http://example.test:9999"
        monkeypatch.delenv("YUNSHU_BASE_URL")
        assert cli._base_url() == cli.DEFAULT_BASE_URL


class TestRequest:
    def test_带令牌与_json(self, cli, monkeypatch):
        seen = {}

        class _Resp:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self):
                return b'{"ok": true}'

        def _fake(req, timeout=None):
            seen["url"] = req.full_url
            seen["auth"] = req.get_header("Authorization")
            seen["method"] = req.get_method()
            seen["body"] = req.data
            return _Resp()

        monkeypatch.setattr(cli.urllib.request, "urlopen", _fake)
        data = cli.request_json("POST", "/api/chat", {"message": "hi"},
                                token="secret-token", timeout=5)
        assert data == {"ok": True}
        assert seen["url"].endswith("/api/chat")
        assert seen["auth"] == "Bearer secret-token"
        assert seen["method"] == "POST"
        assert json.loads(seen["body"].decode("utf-8")) == {"message": "hi"}

    def test_http错误只回服务端error且截断(self, cli, monkeypatch):
        body = json.dumps({"ok": False, "error": "x" * 999}).encode("utf-8")

        def _fake(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 400, "Bad Request", {}, io.BytesIO(body))

        monkeypatch.setattr(cli.urllib.request, "urlopen", _fake)
        with pytest.raises(RuntimeError) as info:
            cli.request_json("GET", "/api/x")
        message = str(info.value)
        assert "HTTP 400" in message
        assert len(message) < cli._MAX_ERROR_CHARS + 20
        assert "secret" not in message

    def test_连接失败回稳定短语(self, cli, monkeypatch):
        def _fake(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(cli.urllib.request, "urlopen", _fake)
        with pytest.raises(RuntimeError) as info:
            cli.request_json("GET", "/api/health")
        assert "请求失败" in str(info.value)
        assert "refused" not in str(info.value)


class TestCommands:
    def test_ask_带_session(self, cli, monkeypatch, capsys):
        seen = {}

        def _fake(method, path, payload=None, token="", timeout=30.0):
            seen.update(method=method, path=path, payload=payload)
            return {"Yunshu": "回答正文"}

        monkeypatch.setattr(cli, "request_json", _fake)
        code = cli.main(["ask", "问题", "--session", "sess-1"])
        assert code == 0
        assert seen["method"] == "POST" and seen["path"] == "/api/chat"
        assert seen["payload"] == {"message": "问题", "session_id": "sess-1"}
        assert "回答正文" in capsys.readouterr().out

    def test_ask_空问题回用法错误(self, cli):
        assert cli.main(["ask", "   "]) == 2

    def test_ask_json_输出完整结构(self, cli, monkeypatch, capsys):
        monkeypatch.setattr(cli, "request_json",
                            lambda *a, **k: {"Yunshu": "x", "steps": [{"title": "t"}]})
        assert cli.main(["ask", "q", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["steps"] == [{"title": "t"}]

    def test_version_打印三处(self, cli, monkeypatch, capsys):
        monkeypatch.setattr(cli, "request_json", lambda *a, **k: {
            "data": {"backend": {"commit": "abc"},
                     "frontend": {"build_commit": "abc", "bundle": "index-x.js"}},
        })
        assert cli.main(["version"]) == 0
        out = capsys.readouterr().out
        assert "backend.commit: abc" in out
        assert "frontend.build_commit: abc" in out
        assert "bundle: index-x.js" in out

    def test_health_传感器数组(self, cli, monkeypatch, capsys):
        monkeypatch.setattr(cli, "request_json", lambda *a, **k: [
            {"severity": "normal"}, {"severity": "warning"},
        ])
        assert cli.main(["health"]) == 1
        assert "异常 1 项" in capsys.readouterr().out

    def test_health_全部正常回_0(self, cli, monkeypatch, capsys):
        monkeypatch.setattr(cli, "request_json", lambda *a, **k: [{"severity": "normal"}])
        assert cli.main(["health"]) == 0
        assert "异常 0 项" in capsys.readouterr().out

    def test_health_失败回_1(self, cli, monkeypatch, capsys):
        def _boom(*a, **k):
            raise RuntimeError("HTTP 401")

        monkeypatch.setattr(cli, "request_json", _boom)
        assert cli.main(["health"]) == 1
        assert "失败" in capsys.readouterr().err
