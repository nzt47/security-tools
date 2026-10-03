# -*- coding: utf-8 -*-
"""鉴权闸门的 **fail-open 绕过**（2026-10-01 实测发现并修复的严重缺陷）。

## 缺陷现场（修复前实测，`CP_API_AUTH_MODE=enforce_all`）

| 请求 | 结果 |
|---|---|
| `GET /api/status`（无令牌） | 401 ✅ |
| `GET /api/status`（合法 `FLASK_API_TOKEN`） | 200 ✅ |
| `GET /api/status` + `Authorization: Bearer <含非 ASCII 的任意串>` | **200 + 完整响应体** ❌ |
| `GET /api/status` + `X-API-Token: <含非 ASCII 的任意串>` | **200 + 完整响应体** ❌ |

## 根因链（三个环节缺一不可，所以三处都要修）

1. `secrets.compare_digest(str, str)` 对**含非 ASCII 字符**的 str 抛
   `TypeError: comparing strings with non-ASCII characters is not supported`；
2. `authorize_token()` 把异常抛给了调用方；
3. 闸门 `_api_auth_gate()` 的 `except Exception: return None` 是 **fail-open**
   （docstring 自述「任何异常都放行，可用性优先」）⇒ 异常被吞，请求**直接放行**。

⇒ 只要在令牌头里塞一个非 ASCII 字节（`é` 即可），**整站 API 全部无认证可达**。
这不是「弱校验」而是**完整的远程鉴权绕过**，且它正好把「切 enforce_all」的效果抵消掉。

## 修复

1. 新增 `token_equal()`：按 **bytes** 比较，且自身**绝不抛异常**（异常一律视为不通过）；
2. `authorize_token()` 改用 `token_equal()`；
3. 闸门的 `except` 在 `enforce`/`enforce_all` 下改为**返回 401**（fail-closed）；
   `shadow`/`off` 仍放行 —— 那两种模式的语义本就是「只记不拦」。
"""
from __future__ import annotations

import pytest
from flask import Flask, jsonify

import agent.server_auth as sa
from agent.security.identity import TokenMap, set_token_map


NON_ASCII = "B\N{LATIN SMALL LETTER E WITH ACUTE}ar"


@pytest.fixture(autouse=True)
def _shared_token(monkeypatch):
    monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", True)
    monkeypatch.setenv("FLASK_API_TOKEN", "api-token-xyz")
    set_token_map(TokenMap(""))


def _ctx(headers):
    app = Flask("failopen_test")
    with app.test_request_context("/guarded", headers=headers):
        return sa.authorize_request()


class TestTokenEqual:
    """比较函数本身：对非 ASCII **不得抛异常**，且必须判为不相等。"""

    def test_非ASCII令牌判为不相等而不是抛异常(self):
        assert sa.token_equal(NON_ASCII, "api-token-xyz") is False

    def test_相等时返回真(self):
        assert sa.token_equal("api-token-xyz", "api-token-xyz") is True

    def test_空值一律不相等(self):
        assert sa.token_equal("", "api-token-xyz") is False
        assert sa.token_equal("api-token-xyz", "") is False
        assert sa.token_equal("", "") is False

    def test_两侧都是非ASCII且相等时也能正确判定(self):
        assert sa.token_equal(NON_ASCII, NON_ASCII) is True

    def test_绝不抛异常(self):
        """这是本缺陷的要害：抛出的异常会被 fail-open 的调用方变成放行。"""
        for bad in [NON_ASCII, "\ud800", "a" * 5000, "\x00", "令牌"]:
            sa.token_equal(bad, "api-token-xyz")
            sa.token_equal("api-token-xyz", bad)
            sa.token_equal(bad, bad)


class TestAuthorizeRequestRejectsNonAscii:
    """修复前这四条**全部返回 True**（= 绕过成功）。"""

    def test_非ASCII的_Authorization_必须拒绝(self):
        assert _ctx({"Authorization": "Bearer " + NON_ASCII})[0] is False

    def test_非ASCII的_X_API_Token_必须拒绝(self):
        assert _ctx({"X-API-Token": NON_ASCII})[0] is False

    def test_非ASCII加垃圾第二候选也必须拒绝(self):
        assert _ctx({"Authorization": "Bearer " + NON_ASCII,
                     "X-API-Token": "nope"})[0] is False

    def test_非ASCII的_Authorization_不妨碍合法_X_API_Token(self):
        """回退仍要正常：垃圾/非 ASCII 的第一候选不该拖垮合法第二候选。"""
        ok, _actor, source = _ctx({"Authorization": "Bearer " + NON_ASCII,
                                   "X-API-Token": "api-token-xyz"})
        assert (ok, source) == (True, sa.SRC_SHARED_TOKEN)


class TestGateFailsClosed:
    """闸门自身异常时，enforce 模式必须**拒绝**而不是放行。"""

    def _gate_app(self, mode):
        """构造一个最小闸门：与 app_server._api_auth_gate 的异常分支**同判定**。

        【口径说明】本夹具只复刻"异常 ⇒ enforce 拒绝 / shadow 放行"这**一条判定**，
        且只断言状态码；响应体形状不在本文件职责内（2026-10-03 起真实闸门的 401
        已改走 RFC 9457 子集，形状断言见 tests/unit/test_api_envelope.py）。
        不要让本夹具跟着改形状 —— 那会把两个关注点重新搅在一起。
        """
        app = Flask("gate_failclosed")

        @app.route("/guarded")
        def guarded():
            return jsonify({"ok": True})

        @app.before_request
        def gate():
            try:
                raise RuntimeError("模拟闸门内部故障")
            except Exception:  # noqa: BLE001
                if mode in ("enforce", "enforce_all"):
                    return jsonify({"error": "未授权：鉴权闸门异常，已按拒绝处理"}), 401
                return None

        return app

    def test_enforce_下闸门异常必须拒绝(self):
        assert self._gate_app("enforce_all").test_client().get("/guarded").status_code == 401

    def test_shadow_下闸门异常仍然放行(self):
        """shadow/off 的语义本就是「只记不拦」，不得回归。"""
        assert self._gate_app("shadow").test_client().get("/guarded").status_code == 200


class TestSourceGuards:
    """源码级守卫：防止这三处再各自写回不安全的比较。"""

    def _src(self, rel):
        import os
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
            return fh.read()

    def test_闸门异常分支在_enforce_下返回_401(self):
        """异常分支必须**拒绝**，而不是 return None（那会退化成 fail-open 鉴权绕过）。

        【2026-10-03 修正本守卫的判据】原判据是"异常分支里必须出现
        `return jsonify`" —— 它把**构造响应所用的 helper 名**当成了判据。
        阶段 2 / R3 把 401 改走统一错误模型（`return _problem(401, ...)`）后，
        本守卫立刻变红：**它守的其实是"用哪个函数造响应"，而它想守的是"必须拒绝"**。
        这正是本仓反复记录的"把实现细节当契约"的形态。
        现改为按意图判定：异常分支必须 (a) 以 return 结束而非 return None，
        (b) 产出 401，(c) 不在 enforce 分支里放行。
        """
        # 【为什么必须用 AST 而不是字符串切分】首版改用文本切片后当场踩坑：
        #   "except Exception 之后" 一直切到了函数末尾，把 **shadow 分支那句合法的
        #   return None** 也算进了 enforce 分支，守卫随即误报。
        #   文本切分无法表达"这个 if 语句体"这种结构边界 —— 用 AST 才有边界。
        import ast
        src = self._src("app_server.py")
        tree = ast.parse(src)

        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_api_auth_gate"), None)
        assert fn is not None, "找不到 _api_auth_gate —— 锚点失效，请迁移本用例"

        handler = None
        for node in ast.walk(fn):
            if not isinstance(node, ast.Try):
                continue
            for h in node.handlers:
                # except Exception ...
                if isinstance(h.type, ast.Name) and h.type.id == "Exception":
                    handler = h
        assert handler is not None, "闸门异常分支缺失 —— fail-closed 保障被整体删掉了"

        seg = ast.get_source_segment(src, handler) or ""
        assert "鉴权闸门异常" in seg, "闸门异常分支必须显式拒绝（fail-closed）"

        enforce_if = None
        for node in ast.walk(handler):
            if isinstance(node, ast.If) and "enforce" in (ast.get_source_segment(src, node.test) or ""):
                enforce_if = node
        assert enforce_if is not None, (
            "异常分支必须区分 enforce 与 shadow —— 否则 shadow 的『只记不拦』语义会被误改"
        )
        body_src = [ast.get_source_segment(src, s) or "" for s in enforce_if.body]
        joined = "\n".join(body_src)
        assert "401" in joined, "enforce 分支必须产出 401"
        assert not any(s.strip() == "return None" for s in body_src), (
            "enforce 分支不得 return None（那是 fail-open ⇒ 鉴权绕过）"
        )

    def test_授权路径不再直接用_compare_digest_比较原始字符串(self):
        src = self._src("agent/server_auth.py")
        assert "def token_equal" in src, "token_equal 是唯一的按字节比较入口"
        block = src.split("def authorize_token", 1)[1]
        assert "compare_digest(presented, shared)" not in block, \
            "authorize_token 不得再对原始 str 调 compare_digest（非 ASCII 会抛）"