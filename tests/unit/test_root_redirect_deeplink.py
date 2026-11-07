# -*- coding: utf-8 -*-
"""启动重定向必须**保留深链的 search 段**（`?panel=…` / `&tool=…`）。

【症状】把 `http://127.0.0.1:5678/#/workbench?panel=tools&tool=web_search` 直接粘贴打开，
落地是 `/chat#/workbench` —— search 连 fragment 一起被抹掉；应用内点击导航不受影响。
实测（2026-11-07，playwright）三条深链全部复现：`panel=tools&tool=x`、`panel=governance/approvals`、
甚至不带 tool 的 `panel=tools`。

【根因】`app_server.index()` 当时返回 `redirect("/chat#/workbench")`。
`/` 这一跳是**服务端**跳转，而 fragment（`#…`）按定义不会发到服务器；
RFC 7231 §7.1.2 规定：Location 里**带** fragment 就用新的（覆盖旧的），
**不带** fragment 才沿用请求 URL 里那个。于是 Location 写死 `#/workbench` =
把用户粘贴的 `?panel=…` 无条件丢弃。

【判据】`GET /` 必须是 302，且 Location **只有路径、不含 '#'** ——
这样浏览器才会把原 fragment 带到 /chat 上，交给前端 HashRouter 处理。

⚠ 本模块 `import app_server`（80–100s），故用例给足超时预算；并沿用
`test_health_retrieval_endpoint.py` 的做法，**整表还原**工具注册表，避免污染同进程后续用例。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.timeout(900)


@pytest.fixture(scope="module")
def client():
    """真实入口 app 的 test_client（用完还原进程级工具注册表）"""
    from agent import tools as _tools

    saved = dict(getattr(_tools, "_registry", {}))
    import app_server  # noqa: PLC0415  （刻意延后：导入耗时 80–100s）

    app = app_server.app
    app.config.update(TESTING=True)
    try:
        with app.test_client() as c:
            yield c
    finally:
        reg = getattr(_tools, "_registry", None)
        if isinstance(reg, dict):
            reg.clear()
            reg.update(saved)


class Test启动重定向:
    def test_重定向到_chat_且不带_fragment(self, client):
        r = client.get("/")
        assert r.status_code == 302, "根路径必须是重定向（不再渲染 legacy 首页）"
        loc = r.headers.get("Location", "")
        assert loc.rstrip("/") == "/chat", f"Location 应为 /chat，实际 {loc!r}"
        assert "#" not in loc, (
            "Location 带 fragment 会**覆盖**用户粘贴的深链（RFC 7231 §7.1.2）："
            "粘贴 #/workbench?panel=tools 冷启动会丢 search 段。"
            "fragment 必须留给浏览器原样带过去 —— Location 只写 /chat。"
        )

    def test_不跟随重定向时也不会丢掉深链路径(self, client):
        """反向校验：Location 若是 `/chat#/workbench`，深链就必然被改写。"""
        loc = client.get("/").headers.get("Location", "")
        assert "workbench" not in loc, (
            "Location 里出现 workbench 说明又把默认面板写死了；"
            "默认面板应该由前端 HashRouter（空 hash 时 `#/` → `#/workbench`）决定"
        )
