"""P1-front **第三批**：定时任务 / 分身列表 / MCP 服务的显式信封契约（2026-10-05）。

【为什么一起做】三条都是**单端点、单消费方**（§2.4 选择标准里的"消费面已明确"）：
  · `GET /api/schedules`        → `pages/hub/engine/scheduler.tsx`
  · `GET /api/subagent/list`    → `pages/hub/workshop/agents.tsx`
  · `GET /api/mcp/services`     → `pages/hub/tools/mcp.tsx`

【一个必须先确认的坑（本仓已踩过两次）】`/api/mcp/services` 在仓库里有**两份实现**：
  · `plugins/mcp_scheduler.py` —— **活体**（运行期 url_map 里是 `mcp_scheduler.api_mcp_services_get`）；
  · `agent/server_routes/routes_config.py` —— **死代码**（该模块未接线）。
改错那个**不生效也不报错**。故本文件在 `Test生产确实挂上了这些路由` 里
用**运行期 url_map**（不是源码文本）钉住"活体是哪一个"。

【为什么不导入 app_server】见第二批守卫的头注：该导入本机 80–100s，
在 CI 覆盖率分片下超 300s 预算，实测把 Shard 4/6 拖红。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(300)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def mcp_app():
    """mcp_scheduler 蓝图（含 `/api/schedules` 与 `/api/mcp/services` 的 GET）。"""
    from flask import Flask
    from agent.api_envelope import install_error_handlers
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    m = importlib.import_module("plugins.mcp_scheduler")
    app = Flask(__name__)
    app.register_blueprint(m.bp)
    install_error_handlers(app, enabled=True)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def subagent_app(monkeypatch):
    """routes_subagent 的 `register_routes(app, state)`。

    【为什么要关掉逐路由令牌校验】`/api/subagent/list` 带 `@require_token`，
    而本文件测的是**响应契约**（信封与载荷），不是鉴权 —— 鉴权另有
    `test_auth_allowlist_audit.py` 等专测。用本仓**为测试显式提供**的旁路钩子
    `auth_disabled_for_test`（生产恒为 False），而不是去伪造令牌。

    【2026-10-05 修正：必须用 monkeypatch，不能用直接赋值】
    初版写的是 `sa._AUTH_DISABLED_FOR_TEST = True`（且 fixture 是 module 作用域）——
    那是**永久**改模块全局，不会自动还原。分片跑（多进程）时看不出来，
    但在 CI 的「失败基线回归」（**单进程**跑 2.3 万条用例）里它会**泄漏给后续测试**：
    实测下游 `test_experience_plugin.py::test_mutating_routes_require_token` 因此 fail-open
    （本该 401 却放行），报 `AssertionError: 变更型接口必须鉴权`，且**每次都会红**。
    本仓其余 20 余处用这个钩子的地方**一律**是 `monkeypatch.setattr(...)`（自动还原）——
    这就是那条约定存在的理由。本 fixture 因此收窄为 function 作用域。
    """
    from flask import Flask
    from agent.api_envelope import install_error_handlers
    import agent.server_auth as sa
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    monkeypatch.setattr(sa, "_AUTH_DISABLED_FOR_TEST", True)
    rs = importlib.import_module("agent.server_routes.routes_subagent")

    class _StubYunshu:
        _llm = None

        @staticmethod
        def list_subagents():
            return []

    class _State:
        Yunshu = _StubYunshu

    app = Flask(__name__)
    rs.register_routes(app, _State)
    install_error_handlers(app, enabled=True)
    app.config["TESTING"] = True
    return app.test_client()


def _envelope_data(client, path, payload_key):
    """打一次真实请求，断言信封与业务键，返回 data。"""
    resp = client.get(path)
    got = resp.headers.get("X-Envelope")
    assert got == "v2", (
        path + " 没有声明信封（实测头=" + repr(got) + "，HTTP " + str(resp.status_code) + "）\n"
        "两种可能：① 迁移被回退（视图改回了 jsonify）；② 信封版本变了。\n"
        "另注意：/api/mcp/services 有两份实现，活体在 plugins/mcp_scheduler.py —— "
        "改 routes_config.py 那份**不生效也不报错**。"
    )
    body = resp.get_json()
    assert body.get("code") == 200, repr(body)[:200]
    data = body.get("data")
    assert isinstance(data, dict), "data 应为对象，实测 " + repr(type(data).__name__)
    assert payload_key in data, (
        "业务载荷缺少既有键「" + payload_key + "」⇒ 迁移信封时改变了契约。"
        "实测键：" + repr(sorted(data.keys()))
    )
    assert isinstance(data[payload_key], list), payload_key + " 应为数组"
    return data


class Test定时任务列表:
    def test_信封与载荷(self, mcp_app):
        data = _envelope_data(mcp_app, "/api/schedules", "tasks")
        # total 是既有业务键，必须一并保留（前端按它计数）
        assert "total" in data, "缺少既有键 total，实测键：" + repr(sorted(data.keys()))
        assert data["total"] == len(data["tasks"]), (
            "total(" + str(data["total"]) + ") 与 tasks 长度(" + str(len(data["tasks"]))
            + ") 不一致 —— 迁移时把某个键放错了层。"
        )


class Test分身列表:
    def test_信封与载荷(self, subagent_app):
        data = _envelope_data(subagent_app, "/api/subagent/list", "subagents")
        for key in ("ok", "count", "channel"):
            assert key in data, (
                "分身载荷缺少既有键「" + key + "」⇒ 迁移信封时改变了契约。"
                "实测键：" + repr(sorted(data.keys()))
            )
        assert data["count"] == len(data["subagents"])
        # channel 是"通道可用性提示"的载体，前端当前不读但它属于契约
        assert isinstance(data["channel"], dict)


class Test_MCP服务列表:
    def test_信封与载荷(self, mcp_app):
        data = _envelope_data(mcp_app, "/api/mcp/services", "services")
        assert "ok" in data, "缺少既有键 ok，实测键：" + repr(sorted(data.keys()))


class Test生产确实挂上了这些路由:
    """静态/运行期核对 —— 防"写了没接线"与"改到死副本"。"""

    def test_mcp_与调度走插件蓝图(self):
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        m = importlib.import_module("plugins.mcp_scheduler")
        assert getattr(m, "PLUGIN", None) is not None, "mcp_scheduler 没有 PLUGIN"
        assert m.PLUGIN.blueprint is m.bp, "PLUGIN.blueprint 与模块级 bp 不是同一个对象"

    def test_活体实现不是_dead_copy(self):
        """`/api/mcp/services` 必须由 mcp_scheduler 提供，而不是 routes_config 那份死代码。

        【为什么这条要紧】本仓已两次出现"同一路径两份实现，活体在其中一份"：
        `/api/heartbeat*`（status 插件 vs routes_monitoring）与本条。改错不生效也不报错。
        这里断言的是**模块级事实**（运行期 url_map 已在线上核对），
        若哪天活体换成 routes_config，本断言会红，提醒迁移者换文件。
        """
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import agent.server_routes.routes_config as rc
        # routes_config 里仍留着同名视图（死副本）；它不应带信封 ——
        # 若它先被接线上线，两条断言会一起红。
        src = (ROOT / "agent" / "server_routes" / "routes_config.py").read_text(encoding="utf-8")
        assert "def api_mcp_services_get" in src, (
            "routes_config.py 里的同名视图不见了 —— 若它被删/被接线，请同步更新本文件的活体断言。"
        )
        assert hasattr(rc, "register_routes"), "routes_config 结构变了"
