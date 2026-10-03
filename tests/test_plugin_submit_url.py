"""Plugin.submit_url 协议 + /api/status/config 端点单测（T3.3）。

覆盖：
- Plugin 新增可选字段 submit_url（默认 ""），manifest 输出该字段；
- 真实 status 插件声明 submit_url="/api/status/config"；
- /api/status/config 闭环：GET 读当前生效值、POST 应用字段（真实子系统 + 持久化），
  再 GET 可见生效（Schema 驱动「改参 → 提交 → 生效」的后端半边）。
"""

import sys
import types

import pytest

from plugins.plugin_api import Plugin, register_plugin, manifest


@pytest.fixture(autouse=True)
def _isolated_registry():
    """每个用例前后保存/恢复插件注册表，避免用例间及真实插件注册互相污染。"""
    from plugins import plugin_api as api
    saved = list(api._REGISTRY)
    api._REGISTRY.clear()
    yield
    api._REGISTRY[:] = saved


# ════════════════════════════════════════════════════════════════
#  submit_url 协议：默认 "" + manifest 输出
# ════════════════════════════════════════════════════════════════

def test_plugin_submit_url_defaults_empty():
    p = register_plugin(Plugin(name="plain", version="1.0.0"))
    assert p.submit_url == ""


def test_manifest_outputs_submit_url():
    register_plugin(Plugin(name="a", version="1.0.0"))
    register_plugin(Plugin(name="b", version="1.0.0", submit_url="/api/b/config"))
    entries = {p["name"]: p for p in manifest()["plugins"]}
    assert entries["a"]["submit_url"] == ""
    assert entries["b"]["submit_url"] == "/api/b/config"


def test_status_plugin_declares_submit_url():
    """真实 status 插件声明 submit_url 指向 /api/status/config（schema 驱动闭环的端点）。

    【2026-10-03 改】原断言是 `"/api/status/config" in p.routes` —— 读的是 Plugin 上那份
    **手写** routes 清单。阶段 4 / R4 已删除该字段（routes 改为从 app.url_map 派生），
    故这里改为断言**派生结果**：把 status 蓝图挂到一个真实 Flask app 上，再问
    plugin_routes() 要答案。这比原来那条更强 —— 它验的是"路由真的注册了"，
    而不是"有人在清单里写了一行"。
    """
    from flask import Flask
    import plugins.status  # noqa: F401  （触发真实插件注册，模块级 PLUGIN 即注册对象）
    from plugins.plugin_api import plugin_routes

    p = plugins.status.PLUGIN
    assert p.name == "status"
    assert p.submit_url == "/api/status/config"

    app = Flask("submit_url_derivation")
    app.register_blueprint(p.blueprint)
    derived = plugin_routes(p, app)
    assert "/api/status/config" in derived, (
        "派生结果里没有 /api/status/config —— 说明该路由没注册到 blueprint，"
        "或派生逻辑失效。derived=" + repr(derived[:8])
    )
    # 派生结果只含本蓝图的路由（不得把别人的规则算进来）
    assert all(r.startswith("/api/") or r.startswith("/logs/") for r in derived), derived


# ════════════════════════════════════════════════════════════════
#  /api/status/config 端点（最小 Flask app + 桩子系统）
# ════════════════════════════════════════════════════════════════

class _FakePersonality:
    """行为等价的 _personality_mgr 桩：get / apply_profile / update_params。"""

    def __init__(self):
        self.profile = "gentle_helper"
        self.params = {"tone": 0.6, "emotion": 0.7}

    def get(self):
        return {
            "current_profile": self.profile,
            "custom_params": dict(self.params),
            "profiles": {},
            "dimensions": [],
        }

    def apply_profile(self, key: str):
        if key not in ("gentle_helper", "professional", "humorous", "custom"):
            return {"ok": False, "error": f"未知人格方案: {key}"}
        self.profile = key
        return {"ok": True, "profile": key, "params": dict(self.params)}

    def update_params(self, params: dict):
        self.params.update(params)
        return {"ok": True, "params": dict(self.params)}

    def reset(self):
        return self.apply_profile("gentle_helper")


@pytest.fixture
def status_client(tmp_path, monkeypatch):
    """最小 Flask app（仅 status 蓝图）+ 桩化的 _Yunshu/_personality_mgr/_status_config。

    - app_server 以 stub 模块注入 sys.modules：_require_token 延迟读取的
      _API_TOKEN_ENABLED=False（跳过令牌校验），视图内 _Yunshu 指向桩；
    - _status_config 指向 tmp_path 下的全新实例，不污染真实 data/status_config.json。
    """
    from flask import Flask
    from plugins import status as status_mod
    from plugins.status import StatusConfigManager

    stub_app_server = types.SimpleNamespace(
        _API_TOKEN_ENABLED=False,
        _API_TOKEN="",
        _Yunshu=types.SimpleNamespace(_planning_enabled=True),
    )
    monkeypatch.setitem(sys.modules, "app_server", stub_app_server)
    monkeypatch.setattr(status_mod, "_personality_mgr", _FakePersonality())
    monkeypatch.setattr(
        status_mod,
        "_status_config",
        StatusConfigManager(path=str(tmp_path / "status_config.json")),
    )

    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(status_mod.bp)
    return app.test_client()


def test_status_config_get_returns_current_values(status_client):
    resp = status_client.get("/api/status/config")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["refresh_interval"] == 5
    assert body["sensor_categories"] == ["硬件感知", "网络感知", "进程与行为", "文件感知", "系统与环境"]
    assert body["planning_enabled"] is True
    assert body["personality_profile"] == "gentle_helper"
    assert body["personality_tone"] == 0.6


def test_status_config_post_applies_and_persists(status_client):
    resp = status_client.post("/api/status/config", json={
        "planning_enabled": False,
        "personality_profile": "humorous",
        "personality_tone": 0.9,
        "refresh_interval": 10,
        "sensor_categories": ["硬件感知", "网络感知"],
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["applied"]["planning_enabled"] is False
    assert body["applied"]["personality_profile"] == "humorous"
    assert body["applied"]["personality_tone"] == 0.9
    assert body["persisted"]["refresh_interval"] == 10
    assert body["persisted"]["sensor_categories"] == ["硬件感知", "网络感知"]

    # 闭环：GET 读到提交后的生效值
    cur = status_client.get("/api/status/config").get_json()
    assert cur["planning_enabled"] is False
    assert cur["personality_profile"] == "humorous"
    assert cur["personality_tone"] == 0.9
    assert cur["refresh_interval"] == 10
    assert cur["sensor_categories"] == ["硬件感知", "网络感知"]


def test_status_config_post_rejects_bad_input(status_client):
    # 未知人格方案 → 400
    r = status_client.post("/api/status/config", json={"personality_profile": "nope"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False

    # 非法 tone → 400
    r = status_client.post("/api/status/config", json={"personality_tone": "abc"})
    assert r.status_code == 400

    # 非法 sensor_categories → 400
    r = status_client.post("/api/status/config", json={"sensor_categories": "硬件感知"})
    assert r.status_code == 400

    # 空提交 → 400
    r = status_client.post("/api/status/config", json={})
    assert r.status_code == 400
