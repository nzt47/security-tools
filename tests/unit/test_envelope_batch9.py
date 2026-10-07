"""P1-front **第九批**：/api/context/config 与 /api/context/compress 的显式信封契约（2026-10-07）。

【本批端点（活体：plugins/memory.py；运行期 url_map 实测）】
  · POST /api/context/config   → memory.api_context_config
  · POST /api/context/compress → memory.api_context_compress
  同页 GET /api/context/status 已在第七批迁移。

【一个端点、两个消费方、两套客户端（第七批 §4.9 的同一形态）】
  · components/workbench/panels/ContextManagerBar.tsx → hubPost（自己发请求）→ 改 postEnvelope；
  · lib/contextMonitorApi.ts → lib/apiClient.request()（**不拆信封**、返回原始体）
    → 三个方法都用 unwrapEnvelopeBody 显式拆。
  只改前者的话，后者会在 {code,data,message} 上读 ok/freed_tokens 而**静默拿到 undefined**。

【既有断言预扫】（第四批 §4.4 那条机械化的审查面）
  tests/unit/test_context_limits_alignment.py::TestConfigPost 有 2 处对响应体做顶层键访问
  ⇒ 已同批改为读 ["data"]，并在第一条补上 X-Envelope: v2 断言（把契约钉在案发现场）。
  进程内对这两个**视图函数**的调用实测 0 处。

【本次不动（如实记录）】/api/context/compress 的失败分支仍是
  `jsonify({"ok": False, "error": ...}), 500` —— 错误路径按纪律本次不动，故本文件的
  AST 断言只要求「成功路径经 _ok」，不要求函数内无 jsonify。

【为什么不导入 app_server】见第四批守卫头注（本机 80–100s，CI 覆盖率分片下超 300s 预算）。
"""
from __future__ import annotations

import ast
import importlib
import logging
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(300)

ROOT = Path(__file__).resolve().parents[2]


class _CfgStub:
    def __init__(self):
        self.values = {}

    def set(self, value, section, key):
        self.values[key] = value

    def get(self, section, key, default=None):
        return self.values.get(key, default)


class _MemoryStub:
    @staticmethod
    def compress():
        return {"freed": 7, "current": 13}


class _YunshuStub:
    _memory = _MemoryStub()


@pytest.fixture
def batch9(monkeypatch):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    fake = types.ModuleType("app_server")
    fake._Yunshu = _YunshuStub()
    fake._cfg = _CfgStub()
    fake.logger = logging.getLogger("fake_app_server")
    fake.require_token = lambda f: f
    fake.log_request = lambda *a, **k: (lambda f: f)
    monkeypatch.setitem(sys.modules, "app_server", fake)

    from flask import Flask
    from agent.api_envelope import install_error_handlers

    mem = importlib.import_module("plugins.memory")
    monkeypatch.setattr(mem, "_context_limit_info",
                        lambda _y: {"limit_tokens": 1000, "limit_source": "test"})
    monkeypatch.setattr(mem, "_push_runtime_window", lambda v: True)

    app = Flask(__name__)
    app.register_blueprint(mem.bp)
    install_error_handlers(app, enabled=True)
    app.config["TESTING"] = True
    return app.test_client()


def _env_post(client, path, body="__none__"):
    resp = client.post(path) if body == "__none__" else client.post(path, json=body)
    got = resp.headers.get("X-Envelope")
    assert got == "v2", (
        path + " 没有声明信封（实测头=" + repr(got) + "，HTTP " + str(resp.status_code) + "）\n"
        "两种可能：① 迁移被回退（视图改回了 jsonify）；② 信封版本变了。"
    )
    b = resp.get_json()
    assert b.get("code") == 200, repr(b)[:200]
    return b.get("data")


class Test上下文写端点载荷:
    def test_config_业务键一字未动(self, batch9):
        data = _env_post(batch9, "/api/context/config", {"token_limit": 1234})
        for key in ("ok", "changed", "runtime_applied", "token_limit",
                    "token_limit_source", "send_limit_semantics"):
            assert key in data, (
                "data 缺少既有键「" + key + "」⇒ 迁移信封时改了契约。实测键：" + repr(sorted(data.keys()))
            )
        assert data["ok"] is True
        assert data["changed"] == ["token_limit"]

    def test_compress_成功路径业务键一字未动(self, batch9):
        data = _env_post(batch9, "/api/context/compress")
        assert data == {"ok": True, "freed_tokens": 7, "current_tokens": 13}, repr(data)[:200]


def _own_returns(fn: ast.FunctionDef):
    out = []
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Return):
            out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _ret_name(ret: ast.Return):
    v = ret.value
    # 错误路径写作 `return jsonify(...), 500` ⇒ 返回的是元组，取第一个元素再判
    if isinstance(v, ast.Tuple) and v.elts:
        v = v.elts[0]
    if isinstance(v, ast.Call) and isinstance(v.func, ast.Name):
        return v.func.id
    return None


class Test结构断言:
    def test_config_视图return全经_ok(self):
        src = (ROOT / "plugins" / "memory.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        fn = fns.get("api_context_config")
        assert fn is not None, "api_context_config 不见了（活体换文件？请同步更新本守卫）"
        rets = _own_returns(fn)
        assert rets and all(_ret_name(r) == "_ok" for r in rets), (
            "api_context_config 的 return 未全部经 _ok"
        )
        seg = ast.get_source_segment(src, fn) or ""
        assert "jsonify(" not in seg, "api_context_config 里仍出现 jsonify"

    def test_compress_成功路径经_ok错误路径仍jsonify(self):
        src = (ROOT / "plugins" / "memory.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        fn = fns.get("api_context_compress")
        assert fn is not None, "api_context_compress 不见了"
        names = [_ret_name(r) for r in _own_returns(fn)]
        assert "_ok" in names, "成功路径未改走 _ok"
        assert "jsonify" in names, (
            "错误路径的 jsonify 不见了 —— 本批按纪律**不动**错误路径；"
            "若确实要迁，请连同错误模型一起改并更新本断言"
        )
        seg = ast.get_source_segment(src, fn) or ""
        assert "return _ok({" in seg and 'return jsonify({"ok": False' in seg, (
            "成功/失败两条返回的形态与预期不符"
        )

    def test_结构断言对合成样例成立(self):
        demo = ast.parse("def f():\n    try:\n        return _ok(1)\n    except Exception:\n        return jsonify(2), 500\n")
        fn = [n for n in ast.walk(demo) if isinstance(n, ast.FunctionDef) and n.name == "f"][0]
        names = [_ret_name(r) for r in _own_returns(fn)]
        assert set(names) == {"_ok", "jsonify"}, (
            "本判据应同时认两种 return（成功 _ok / 失败 jsonify），实测 " + repr(names)
        )


class Test前端解析:
    def _code(self, rel):
        from scripts.audit.contract_diff import strip_comments
        return strip_comments((ROOT / rel).read_text(encoding="utf-8"))

    def test_面板两处改用postEnvelope(self):
        import re
        code = self._code("yunshu-ui/src/components/workbench/panels/ContextManagerBar.tsx")
        for const in ("CONTEXT_CONFIG", "CONTEXT_COMPRESS"):
            assert re.search(r"postEnvelope[^\n]*" + const, code), (
                "「" + const + "」不在 postEnvelope 调用里 ⇒ 迁移被回退或只改了一半"
            )
        assert "hubPost(" not in code, "面板仍用 hubPost ⇒ 后端已发信封、前端还在读裸体"

    def test_apiClient链路也拆信封(self):
        code = self._code("yunshu-ui/src/lib/contextMonitorApi.ts")
        # 用「调用形态」计数（unwrapEnvelopeBody<...>），避免把 import 行也算进去
        assert code.count("unwrapEnvelopeBody<") >= 3, (
            "contextMonitorApi 的三个方法都应经 unwrapEnvelopeBody（status 第七批已做，config/compress 本批）"
        )
        for const in ("CONTEXT_CONFIG", "CONTEXT_COMPRESS"):
            assert const in code

    def test_剥注释口径对合成样例成立(self):
        from scripts.audit.contract_diff import strip_comments
        assert "hubPost(" not in strip_comments("// hubPost(x)\nconst y = 1")
        assert "hubPost(" in strip_comments("hubPost(x)")
