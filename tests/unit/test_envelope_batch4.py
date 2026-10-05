"""P1-front **第四批**：工具配置列表 / 人格配置的显式信封契约（2026-10-05）。

【本批取这两个端点的理由（第四批选择标准）】
  · GET /api/tools/config —— **裸数组**（实测 91 项），前端原来只能靠
    pickList(r, 'tools') 在候选键里「找数组」来猜，改完收益最大；单端点、单消费方。
  · GET /api/personality —— 裸对象，单端点、单消费方。
  两者消费方各只有一个文件：pages/hub/tools/toolset.tsx、pages/hub/personality.tsx。

【动手前已按纪律核对的两件事（本仓各踩过一次）】
  ① **活体实现是哪一份**：运行期 url_map 实测
       /api/tools/config   → skills.api_tools_config      （plugins/skills.py）
       /api/personality    → status.api_personality_get   （plugins/status.py）
     同路径在 agent/server_routes/routes_personality.py 还有一份**死副本**
     （该模块在 test_server_routes_registration_inventory.py::KNOWN_UNREGISTERED 里，
     原因写明是「人格端点由 plugins/status.py 提供」）——改错那份不生效也不报错。
  ② **既有断言预扫**：全仓（tests/ + yunshu-ui）对这两个端点的响应体做顶层键访问的地方
     实测 **0 处**，故本批不需要同步改动既有断言（第三批的教训：漏了这一步会被 CI 打红两次）。

【为什么不导入 app_server】见第二批守卫的头注：该导入本机 80–100s，在 CI 覆盖率分片下
超 300s 预算，实测把 Shard 4/6 拖红。这里的做法与本仓既有技法一致：
用**最小 Flask app + 替身**把「视图自己的行为」测出来（app_server 与 agent.tools 都是
函数内延迟 import，故可以在 sys.modules / 模块属性上放替身），接线则用静态断言。
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(300)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def batch4_client(monkeypatch):
    """最小 Flask app：挂 skills 与 status 两个蓝图，并给两个延迟 import 放替身。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    # 替身 1：app_server（api_tools_config 里 from app_server import _Yunshu 取权限日志）
    fake_app_server = types.ModuleType("app_server")

    class _Perm:
        @staticmethod
        def get_permission_log():
            return []

    class _Yunshu:
        _permission = _Perm()

    fake_app_server._Yunshu = _Yunshu
    monkeypatch.setitem(sys.modules, "app_server", fake_app_server)

    # 替身 2：工具注册表（真实注册表由 app_server 装配，这里不想要那条 80–100s 的导入）
    tools_mod = importlib.import_module("agent.tools")
    monkeypatch.setattr(
        tools_mod, "list_tools",
        lambda: [{"name": "demo_tool", "description": "演示工具"}],
    )

    from flask import Flask
    from agent.api_envelope import install_error_handlers

    skills = importlib.import_module("plugins.skills")
    status = importlib.import_module("plugins.status")
    app = Flask(__name__)
    app.register_blueprint(skills.bp)
    app.register_blueprint(status.bp)
    install_error_handlers(app, enabled=True)
    app.config["TESTING"] = True
    return app.test_client()


def _envelope_data(client, path):
    """打一次真实请求，断言信封头与业务码，返回 data。"""
    resp = client.get(path)
    got = resp.headers.get("X-Envelope")
    assert got == "v2", (
        path + " 没有声明信封（实测头=" + repr(got) + "，HTTP " + str(resp.status_code) + "）\n"
        "两种可能：① 迁移被回退（视图改回了 jsonify）；② 信封版本变了。\n"
        "另注意：/api/personality 在 agent/server_routes/routes_personality.py 有死副本，"
        "活体在 plugins/status.py —— 改那份死代码不生效也不报错。"
    )
    body = resp.get_json()
    assert body.get("code") == 200, repr(body)[:200]
    return body.get("data")


class Test工具配置列表:
    def test_信封与载荷形状(self, batch4_client):
        """GET /api/tools/config：data 必须是**数组本身**，元素键与迁移前一致。"""
        data = _envelope_data(batch4_client, "/api/tools/config")
        assert isinstance(data, list), (
            "data 应为数组（本端点原本就是裸数组，迁移只是把它放进信封）——实测 "
            + repr(type(data).__name__)
            + "。若这里变成对象，说明顺手加了一层业务键，而前端已按数组解析。实测=" + repr(data)[:200]
        )
        assert len(data) == 1, "替身只给了一个工具，实测 data=" + repr(data)[:200]
        item = data[0]
        for key in ("name", "description", "enabled", "call_count", "last_used"):
            assert key in item, (
                "工具项缺少既有键「" + key + "」⇒ 迁移信封时改变了契约。实测键："
                + repr(sorted(item.keys()))
            )
        assert item["name"] == "demo_tool"
        assert isinstance(item["enabled"], bool)


class Test人格配置:
    def test_信封与载荷形状(self, batch4_client):
        """GET /api/personality：data 必须是那个裸对象，四个既有键一个不少。"""
        data = _envelope_data(batch4_client, "/api/personality")
        assert isinstance(data, dict), "data 应为对象，实测 " + repr(type(data).__name__)
        for key in ("current_profile", "custom_params", "dimensions", "profiles"):
            assert key in data, (
                "人格载荷缺少既有键「" + key + "」⇒ 迁移信封时改变了契约。实测键："
                + repr(sorted(data.keys()))
            )


class Test生产接线与死副本:
    """静态核对 —— 防「写了没接线」与「改到死副本」。"""

    def test_两个插件都经_PLUGIN_注册(self):
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        for name in ("plugins.skills", "plugins.status"):
            m = importlib.import_module(name)
            assert getattr(m, "PLUGIN", None) is not None, name + " 没有 PLUGIN"
            assert m.PLUGIN.blueprint is m.bp, (
                name + " 的 PLUGIN.blueprint 与模块级 bp 不是同一个对象 ⇒ 迁移可能落在没被注册的蓝图上"
            )

    def test_活体实现所在文件(self):
        """把「活体在哪一份文件」钉死（运行期 url_map 已在线上核对过）。"""
        skills_src = (ROOT / "plugins" / "skills.py").read_text(encoding="utf-8")
        status_src = (ROOT / "plugins" / "status.py").read_text(encoding="utf-8")
        assert '@bp.route("/api/tools/config", methods=["GET"])' in skills_src, (
            "plugins/skills.py 里的 /api/tools/config 路由不见了 —— "
            "若活体换成别的文件，请同步更新本断言与前端解析。"
        )
        assert '@bp.route("/api/personality", methods=["GET"])' in status_src, (
            "plugins/status.py 里的 /api/personality 路由不见了 —— 同上。"
        )

    def test_死副本仍在且未接线(self):
        """agent/server_routes/routes_personality.py 仍是死副本。

        【为什么单钉这一条】它和活体**同名同路径**：若哪天有人把它接线，
        /api/personality 的活体就换了实现（本批的信封迁移随之失效，且不报错）。
        本断言只看「死副本还在、且还是旧形态」这个事实；真要接线，请同时改本文件与迁移。
        """
        dead = ROOT / "agent" / "server_routes" / "routes_personality.py"
        src = dead.read_text(encoding="utf-8")
        assert "def api_personality_get" in src, (
            "死副本里的同名视图不见了 —— 若它被删/被接线，请同步更新本文件的活体断言。"
        )
        assert "return jsonify(personality_mgr.get())" in src, (
            "死副本的返回形态变了 —— 本批只迁了活体（plugins/status.py）；"
            "若死副本也被迁了信封，说明它可能已被接线，请复核活体到底是谁。"
        )
