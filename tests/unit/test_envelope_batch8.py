"""P1-front **第八批**：人格配置三个**写**端点的显式信封契约（2026-10-07）。

【本批端点（活体：plugins/status.py；运行期 url_map 实测）】
  · POST /api/personality/params   → status.api_personality_params
  · POST /api/personality/profile  → status.api_personality_profile
  · POST /api/personality/reset    → status.api_personality_reset
  三者消费方各只有一个文件：pages/hub/personality.tsx（同页 GET 已在第四批迁移）。

【动手前已按纪律核对的两件事】
  ① 活体是谁：运行期 url_map 实测三条都在 plugins/status.py；
     agent/server_routes/routes_personality.py 里那份同名同路径实现**未接线**（死副本）。
  ② 消费方预扫（**按客户端种类**）：全仓（agent/plugins/app_server/scripts）对这三个
     **视图函数**的进程内调用实测 0 处；tests/ 对响应体做顶层键访问 0 处；前端只有
     personality.tsx 一处走 hubPost；lib/apiClient 无消费方 ⇒ 本批不需同步改既有断言。

【一条**迁移顺手修掉**的真实契约不一致（响应侧）】后端一向返回 `params` / `profile`，
  而前端读的是 `custom_params` / `current_profile`（与同文件 GET 的键名混淆）⇒ 点预设人格后
  本地参数一直不刷新（拿到 undefined，不报错）。本批按后端既有契约修前端读取键。

【本次**不动**（如实记录）】apply_profile 对未知 profile（如前端「自定义」按钮发的 "custom"）
  以 **HTTP 200 + {ok:false,error}** 返回；前端不判 ok，仍提示「已应用人格」。这是**错误路径**，
  按纪律本次不动，另记。

【为什么不导入 app_server】见第四批守卫头注（本机 80–100s，CI 覆盖率分片下超 300s 预算）。
  这里用最小 Flask app + 替身：status.py 的 _log_request/_require_token 由 plugins/plugin_api
  惰性解析 app_server 的 log_request/require_token，故替身里放两个恒等实现即可。
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


class _FakeMgr:
    """_personality_mgr 的替身：只回可断言的固定形状，不碰 data/personality.json。"""

    @staticmethod
    def get():
        return {"current_profile": "custom", "custom_params": {"tone": 0.6},
                "dimensions": [], "profiles": {}}

    @staticmethod
    def update_params(params):
        return {"ok": True, "params": dict(params)}

    @staticmethod
    def apply_profile(key):
        return {"ok": True, "profile": key, "params": {"tone": 0.5}}

    @staticmethod
    def reset():
        return {"ok": True, "profile": "gentle_helper", "params": {"tone": 0.6}}


@pytest.fixture
def batch8(monkeypatch):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    fake_app_server = types.ModuleType("app_server")
    # plugin_api 惰性取这两个装饰器；本用例只关心响应契约，故都是恒等实现
    fake_app_server.require_token = lambda f: f
    fake_app_server.log_request = lambda *a, **k: (lambda f: f)
    fake_app_server.logger = logging.getLogger("fake_app_server")
    monkeypatch.setitem(sys.modules, "app_server", fake_app_server)

    from flask import Flask
    from agent.api_envelope import install_error_handlers

    status = importlib.import_module("plugins.status")
    monkeypatch.setattr(status, "_personality_mgr", _FakeMgr())

    app = Flask(__name__)
    app.register_blueprint(status.bp)
    install_error_handlers(app, enabled=True)
    app.config["TESTING"] = True
    return app.test_client()


def _env_post(client, path, body="__none__"):
    """打一次真实 POST，断言信封头与业务码，返回 data。"""
    resp = client.post(path) if body == "__none__" else client.post(path, json=body)
    got = resp.headers.get("X-Envelope")
    assert got == "v2", (
        path + " 没有声明信封（实测头=" + repr(got) + "，HTTP " + str(resp.status_code) + "）\n"
        "两种可能：① 迁移被回退（视图改回了 jsonify）；② 信封版本变了。\n"
        "另注意：/api/personality* 在 agent/server_routes/routes_personality.py 有死副本，"
        "活体在 plugins/status.py —— 改那份死代码不生效也不报错。"
    )
    body = resp.get_json()
    assert body.get("code") == 200, repr(body)[:200]
    return body.get("data")


class Test人格写端点的载荷形状:
    def test_params_载荷业务键一字未动(self, batch8):
        data = _env_post(batch8, "/api/personality/params", {"params": {"tone": 0.7}})
        assert data == {"ok": True, "params": {"tone": 0.7}}, (
            "data 必须仍是 {ok, params}（旧裸体）——实测 " + repr(data)[:200]
        )

    def test_profile_载荷业务键一字未动(self, batch8):
        data = _env_post(batch8, "/api/personality/profile", {"profile": "gentle_helper"})
        assert data.get("ok") is True
        assert data.get("profile") == "gentle_helper", "缺 profile 键：" + repr(sorted(data.keys()))
        assert data.get("params") == {"tone": 0.5}, "缺 params 键：" + repr(sorted(data.keys()))

    def test_reset_空_body_也可用(self, batch8):
        data = _env_post(batch8, "/api/personality/reset")
        assert data.get("ok") is True and "params" in data, repr(data)[:200]


def _own_returns(fn: ast.FunctionDef):
    """只取**该函数自己**的 return，跳过内层闭包 / lambda（第五批守卫踩过的坑）。"""
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


def _calls_ok(ret: ast.Return) -> bool:
    v = ret.value
    return isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "_ok"


class Test结构断言:
    """行为测不到的地方用 AST 钉结构（第五批教训：AST 也要选对节点范围）。"""

    def test_三个视图的return都经_ok且函数内无jsonify(self):
        src = (ROOT / "plugins" / "status.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for name in ("api_personality_params", "api_personality_profile", "api_personality_reset"):
            fn = fns.get(name)
            assert fn is not None, name + " 不见了（活体换文件？请同步更新本守卫）"
            rets = _own_returns(fn)
            assert rets, name + " 没有 return"
            assert all(_calls_ok(r) for r in rets), (
                name + " 的 return 未全部经 _ok（）—— 可能只改了 docstring 没改 return"
            )
            seg = ast.get_source_segment(src, fn) or ""
            assert "jsonify(" not in seg, (
                name + " 里仍出现 jsonify —— 迁移不完整（行为用例可能覆盖不到这条分支）"
            )

    def test_结构断言对合成样例成立(self):
        """可证伪：把「内层闭包的 return」当成本函数的 return 必须被本判据排除。"""
        demo = ast.parse("def f():\n    def g():\n        return jsonify(1)\n    return _ok(2)\n")
        fn = [n for n in ast.walk(demo)
              if isinstance(n, ast.FunctionDef) and n.name == "f"][0]
        rets = _own_returns(fn)
        assert len(rets) == 1 and _calls_ok(rets[0]), (
            "本文件的 _own_returns 把内层闭包也算进来了 —— 这正是第五批让断言在正确代码上变红的坑"
        )


class Test前端解析:
    def _code(self):
        from scripts.audit.contract_diff import strip_comments
        src = (ROOT / "yunshu-ui" / "src" / "pages" / "hub" / "personality.tsx").read_text(encoding="utf-8")
        return strip_comments(src)

    def test_三处POST都改用postEnvelope(self):
        import re
        code = self._code()
        # 允许 postEnvelope<泛型>(...) 的写法，故不要求紧跟左括号
        for const in ("PERSONALITY_PROFILE", "PERSONALITY_PARAMS", "PERSONALITY_RESET"):
            assert re.search(r"postEnvelope[^\n]*" + const, code), (
                "「" + const + "」不在 postEnvelope 调用里 ⇒ 迁移被回退或只改了一半"
            )
        assert "hubPost(" not in code, "本页仍用 hubPost ⇒ 后端已发信封、前端还在读裸体"

    def test_读的是后端既有键params_profile而不是自定义键(self):
        code = self._code()
        assert "rr.custom_params" not in code, "仍在读后端不存在的 custom_params（静默 undefined）"
        assert "d.params" in code and "d.profile" in code, (
            "未按后端既有契约读 params / profile ⇒ 点预设人格后本地参数不刷新"
        )

    def test_剥注释口径对合成样例成立(self):
        from scripts.audit.contract_diff import strip_comments
        assert "hubPost(" not in strip_comments("// hubPost(x)\nconst y = 1")
        assert "hubPost(" in strip_comments("hubPost(x)")


class Test生产接线与死副本:
    def test_status_经PLUGIN注册(self):
        status = importlib.import_module("plugins.status")
        assert getattr(status, "PLUGIN", None) is not None, "plugins.status 没有 PLUGIN"
        assert status.PLUGIN.blueprint is status.bp, "PLUGIN.blueprint 与模块级 bp 不是同一个对象"

    def test_死副本仍在且仍是旧形态(self):
        dead = (ROOT / "agent" / "server_routes" / "routes_personality.py").read_text(encoding="utf-8")
        assert "def api_personality_reset" in dead, "死副本不见了 —— 复核活体是否换文件"
        assert "return jsonify(result)" in dead, (
            "死副本的返回形态变了 —— 本批只迁了活体 plugins/status.py；"
            "若死副本也被迁了信封，说明它可能已被接线，请复核活体到底是谁"
        )
