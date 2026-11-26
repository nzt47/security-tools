# -*- coding: utf-8 -*-
"""E3-1 侧栏终端（PTY）—— 界面侧路由（2026-11-27）

模型侧（agent/pty_session.py + terminal 工具）已交付；本文件只钉**界面侧入口**的口径：

  ① 开关默认关时：GET 列表 200 + enabled=false；POST 开会话 200 + code=disabled
     —— 界面要能显示「未启用」，而不是 500/400（hubPost 只在非 2xx 抛错）；
  ② 受限档 fail-closed：code=blocked，且**不 spawn**任何进程；
  ③ 会话不存在：input/output/signal/close 全回 200 + code=no_session（幂等语义）；
  ④ 缺 id 才是 400（参数错误与域内失败要能区分）；
  ⑤ 输入默认补 \\r（PTY 的 Enter），newline=false 时不补；
  ⑥ 输出 offset/limit 收敛后再透传（非法值不炸路由）；
  ⑦ **响应体不回 error 原文**：只回稳定 code，中文由界面自己出。
"""
from __future__ import annotations

import sys
import types

import pytest
from flask import Flask


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("CP_TERMINAL_ENABLED", raising=False)
    monkeypatch.delenv("CP_PERMISSION_PRESET", raising=False)
    yield


def _reset_view_cache() -> None:
    """清掉 system_tools 各路由 `_view` 的包装缓存。

    【为什么必须清】本文件用**假 app_server**（require_token=直通）注册蓝图；`_view` 会把首次
    请求时包好的装饰器缓存在闭包里。不清的话，同进程后续测试（随机顺序）里真 app 的
    `/api/terminal/*` 会用到这个"免鉴权"包装 —— 变成难以复现的红。teardown 统一清空，
    下一次真请求会重新从当时的 app_server 取装饰器。
    """
    import plugins.system_tools as st

    for name in dir(st):
        if not name.startswith("api_"):
            continue
        fn = getattr(st, name)
        for cell in getattr(fn, "__closure__", None) or ():
            try:
                contents = cell.cell_contents
            except ValueError:  # 空 cell
                continue
            if isinstance(contents, dict) and "wrapped" in contents:
                contents["wrapped"] = None


@pytest.fixture()
def client(monkeypatch):
    """装配桩 app_server（require_token / log_request 直通）+ 注册 system_tools 蓝图。"""
    fake = types.ModuleType("app_server")
    fake.require_token = lambda f: f
    fake.log_request = lambda *a, **k: (lambda f: f)
    fake.logger = types.SimpleNamespace(info=lambda *a, **k: None,
                                       warning=lambda *a, **k: None,
                                       error=lambda *a, **k: None,
                                       debug=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "app_server", fake)

    import plugins.system_tools as st

    app = Flask(__name__)
    app.register_blueprint(st.bp)
    app.config.update(TESTING=True)
    yield app.test_client()
    from agent import pty_session
    pty_session.close_all()
    _reset_view_cache()


class TestDisabledByDefault:
    def test_列表回200且enabled为false(self, client):
        body = client.get("/api/terminal/sessions").get_json()
        assert body["ok"] is True and body["enabled"] is False
        assert body["sessions"] == [] and body["backend"] in ("winpty", "pty")

    def test_开会话回code_disabled而不是报错(self, client):
        resp = client.post("/api/terminal/sessions", json={})
        assert resp.status_code == 200, resp.status_code
        body = resp.get_json()
        assert body["ok"] is False and body["code"] == "disabled"
        # 响应体不回原文（开关提示由界面按 code 出）
        assert "error" not in body


class TestConfinedBlocked:
    def test_受限档回blocked且不spawn(self, client, monkeypatch):
        monkeypatch.setenv("CP_TERMINAL_ENABLED", "1")
        import agent.sandbox as sandbox
        monkeypatch.setattr(sandbox, "is_confined", lambda *a, **k: True)
        from agent import pty_session

        body = client.post("/api/terminal/sessions", json={"command": "cmd"}).get_json()
        assert body["ok"] is False and body["code"] == "blocked"
        assert pty_session.list_sessions()["count"] == 0, "受限档一律不得 spawn"


class TestParameterErrors:
    @pytest.mark.parametrize("path", ["/api/terminal/input", "/api/terminal/signal", "/api/terminal/close"])
    def test_缺id_400(self, client, path):
        resp = client.post(path, json={})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "missing_id"

    def test_输出缺id_400(self, client):
        resp = client.get("/api/terminal/output")
        assert resp.status_code == 400 and resp.get_json()["code"] == "missing_id"


class TestDomainErrors:
    @pytest.mark.parametrize("path", ["/api/terminal/input", "/api/terminal/signal", "/api/terminal/close"])
    def test_会话不存在回no_session(self, client, path):
        resp = client.post(path, json={"id": "t-doesnotexist", "text": "x"})
        assert resp.status_code == 200
        assert resp.get_json()["code"] == "no_session"

    def test_输出会话不存在回no_session(self, client):
        body = client.get("/api/terminal/output?id=t-doesnotexist").get_json()
        assert body["ok"] is False and body["code"] == "no_session"

    def test_error原文不进响应体(self, client, monkeypatch):
        from agent import pty_session
        monkeypatch.setattr(pty_session, "send",
                            lambda sid, text: {"ok": False, "code": "write_failed",
                                               "error": "SECRET-LEAK-原文"})
        resp = client.post("/api/terminal/input", json={"id": "t-1", "text": "x"})
        assert resp.status_code == 200
        assert "SECRET-LEAK" not in resp.get_data(as_text=True)
        assert resp.get_json()["code"] == "write_failed"


class TestInputAndOutput:
    def test_输入默认补回车(self, client, monkeypatch):
        from agent import pty_session
        seen = {}

        def _fake_send(sid, text):
            seen["text"] = text
            return {"ok": True, "id": sid}

        monkeypatch.setattr(pty_session, "send", _fake_send)
        client.post("/api/terminal/input", json={"id": "t-1", "text": "dir"})
        assert seen["text"] == "dir\r"
        client.post("/api/terminal/input", json={"id": "t-1", "text": "dir", "newline": False})
        assert seen["text"] == "dir"

    def test_输出参数收敛后透传(self, client, monkeypatch):
        from agent import pty_session
        seen = {}
        monkeypatch.setattr(pty_session, "read",
                            lambda sid, offset=None, limit=None: seen.update(
                                {"sid": sid, "offset": offset, "limit": limit}) or {"ok": True, "chunk": ""})
        client.get("/api/terminal/output?id=t-1&offset=abc&limit=999999")
        assert seen == {"sid": "t-1", "offset": 0, "limit": 60000}
        seen.clear()
        client.get("/api/terminal/output?id=t-1")
        assert seen["offset"] is None, "缺省 offset = 从当前窗口起点读"
        assert seen["limit"] == 20000

    def test_开会话参数收敛(self, client, monkeypatch):
        from agent import pty_session
        seen = {}
        monkeypatch.setattr(pty_session, "open_session",
                            lambda **kw: seen.update(kw) or {"ok": True, "id": "t-1"})
        body = client.post("/api/terminal/sessions",
                           json={"command": "cmd", "rows": 9999, "cols": 1}).get_json()
        assert body["ok"] is True
        assert seen["rows"] == 200 and seen["cols"] == 20 and seen["command"] == "cmd"



# ── 真 PTY 全链路（HTTP 层）：路由 → pty_session → 子进程 → 输出 → 关闭 ──
# 上面的用例都 monkeypatch 了 pty_session；这一条**不 mock**，用真终端跑一遍，
# 防止「路由参数都对、但接上真会话就不通」。无 PTY 后端（Windows 缺 winpypty）时 skip。

def _pty_backend() -> str:
    if sys.platform == "win32":
        try:
            import winpty  # noqa: F401
            return "winpty"
        except Exception:  # noqa: BLE001
            return ""
    try:
        import pty  # noqa: F401
        return "pty"
    except Exception:  # noqa: BLE001
        return ""


BACKEND = _pty_backend()
needs_pty = pytest.mark.skipif(not BACKEND, reason="本机没有可用的 PTY 后端（Windows 需 pywinpty）")


def _read_until(client, sid, needle, timeout=15.0):
    import time as _time

    deadline = _time.time() + timeout
    collected, offset = "", 0
    while _time.time() < deadline:
        body = client.get("/api/terminal/output?id=%s&offset=%s" % (sid, offset)).get_json()
        assert body.get("ok"), body
        chunk = body.get("chunk") or ""
        collected += chunk
        nxt = body.get("next_offset")
        offset = nxt if nxt is not None else int(body.get("offset", 0)) + len(chunk)
        if needle in collected:
            return collected
        _time.sleep(0.1)
    return collected


@needs_pty
class TestRealPtyThroughRoutes:
    def test_开_发_读_关全链路(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("CP_TERMINAL_ENABLED", "1")
        script = tmp_path / "echo_lines.py"
        script.write_text(
            "import sys\n"
            "print('READY', flush=True)\n"
            "for line in sys.stdin:\n"
            "    print('ECHO:' + line.strip(), flush=True)\n",
            encoding="utf-8")
        resp = client.post("/api/terminal/sessions", json={
            "command": '"%s" "%s"' % (sys.executable, script),
            "cwd": str(tmp_path),
        })
        body = resp.get_json()
        assert body.get("ok") is True and body.get("id"), body
        sid = body["id"]
        assert "READY" in _read_until(client, sid, "READY")
        assert client.post("/api/terminal/input",
                           json={"id": sid, "text": "hello"}).get_json()["ok"] is True
        assert "ECHO:hello" in _read_until(client, sid, "ECHO:hello")
        assert client.post("/api/terminal/close", json={"id": sid}).get_json()["ok"] is True
        # 关掉之后：再读/再关都回 no_session（200，不是 500）
        assert client.get("/api/terminal/output?id=%s" % sid).get_json()["code"] == "no_session"
        assert client.post("/api/terminal/close", json={"id": sid}).get_json()["code"] == "no_session"
