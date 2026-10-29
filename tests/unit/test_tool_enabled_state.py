"""工具「启用状态」的口径：UI 开关 > YAML `enabled`（2026-10-29，§11.9 收口）。

【现象（活体实测）】`data/tool_definitions/schedule_task.yaml` 是 `enabled: false`
（`agent.tools._hidden_tool_names()` 已把它从**模型可见集**剔除），但
`GET /api/tools/config` 与 `GET /api/tools/status-batch` 都报 `enabled: true`
⇒ 工具集页「状态」列显示「启用」、按钮显示「停用」，与真实生效状态**相反**。
根因：`plugins/skills.py::_get_tool_state` 只读 `data/tools_config.json`，对没出现在
开关文件里的工具一律 True。而「先停用观察、再物理删」这条流程必须能从界面判断谁被停了。

【判据】① 有 UI 开关时以开关为准（YAML 停用可被界面显式 re-enable）；
        ② 没有开关时回落声明层；
        ③ 两个端点共用同一个函数（静态断言，避免各自实现再次漂移）。
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(120)

ROOT = Path(__file__).resolve().parents[2]


def _skills():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module("plugins.skills")


class Test生效状态口径:
    def test_无UI开关时回落声明层停用(self):
        s = _skills()
        assert s._get_tool_state("schedule_task", declared=False) is False, (
            "YAML enabled:false 且没有 UI 开关 ⇒ 必须报停用，否则页面把停用显示成启用"
        )

    def test_无UI开关且声明启用时保持启用(self):
        s = _skills()
        assert s._get_tool_state("read_file", declared=True) is True

    def test_UI开关优先于声明层(self, monkeypatch):
        """界面显式 re-enable（true）要能盖过 YAML 的 false —— 两条路径都可回滚。"""
        s = _skills()
        monkeypatch.setattr(s, "_load_tool_states", lambda: {"tool_states": {"schedule_task": True}})
        assert s._get_tool_state("schedule_task", declared=False) is True

    def test_UI显式停用对声明启用的工具也生效(self, monkeypatch):
        s = _skills()
        monkeypatch.setattr(s, "_load_tool_states", lambda: {"tool_states": {"read_file": False}})
        assert s._get_tool_state("read_file", declared=True) is False

    def test_声明集合读不到时不误报停用(self, monkeypatch):
        s = _skills()
        tools_mod = importlib.import_module("agent.tools")

        def _boom():
            raise RuntimeError("声明读不到")

        monkeypatch.setattr(tools_mod, "declared_disabled_tool_names", _boom, raising=True)
        assert s._declared_disabled_tools() == frozenset(), (
            "读不到声明时必须退回「未停用」，不能把全部工具标成停用"
        )


@pytest.fixture
def client(monkeypatch):
    """最小 Flask app：只挂 skills 蓝图，替身 app_server 与工具注册表。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    fake_app_server = types.ModuleType("app_server")

    class _Perm:
        @staticmethod
        def get_permission_log():
            return []

    class _Yunshu:
        _permission = _Perm()

    class _SkillsMgr:
        @staticmethod
        def get_all():
            return []

    fake_app_server._Yunshu = _Yunshu
    fake_app_server._skills_mgr = _SkillsMgr()
    monkeypatch.setitem(sys.modules, "app_server", fake_app_server)

    tools_mod = importlib.import_module("agent.tools")
    monkeypatch.setattr(tools_mod, "list_tools", lambda: [
        {"name": "demo_disabled", "description": "声明停用的演示工具"},
        {"name": "demo_enabled", "description": "正常工具"},
    ])

    skills = _skills()
    monkeypatch.setattr(skills, "_declared_disabled_tools", lambda: frozenset({"demo_disabled"}))
    monkeypatch.setattr(skills, "_load_tool_states", lambda: {"tool_states": {}})

    from flask import Flask
    from agent.api_envelope import install_error_handlers

    app = Flask(__name__)
    app.register_blueprint(skills.bp)
    install_error_handlers(app, enabled=True)
    app.config["TESTING"] = True
    return app.test_client()


class Test端点:
    def test_tools_config_报出声明层停用(self, client):
        body = client.get("/api/tools/config").get_json()
        items = {i["name"]: i for i in body["data"]}
        assert items["demo_disabled"]["enabled"] is False, (
            "YAML 声明停用的工具在 /api/tools/config 仍报启用 ⇒ 页面与模型可见集相反"
        )
        assert items["demo_enabled"]["enabled"] is True

    def test_status_batch_与config同口径(self, client):
        body = client.get("/api/tools/status-batch").get_json()
        # 该端点历史上返回**裸数组**（未装信封）；两种形状都接受，只断言口径一致
        rows = {r["name"]: r for r in (body["data"] if isinstance(body, dict) else body)
                if r.get("type") == "tool"}
        assert rows["demo_disabled"]["enabled"] is False
        assert rows["demo_enabled"]["enabled"] is True

    def test_两个端点共用同一函数(self):
        src = (ROOT / "plugins" / "skills.py").read_text(encoding="utf-8")
        assert src.count("_get_tool_state(") >= 3, (
            "两个端点 + 定义处都应出现 _get_tool_state；若某处改回直读 tools_config.json，"
            "就会重新出现「页面显示启用、模型看不见」的分叉"
        )
