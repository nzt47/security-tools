"""Plugin.schema 协议单测（T3.1，schema 驱动自解释 UI）。

覆盖：
- register_plugin 对非法 schema 抛 ValueError（开发期早失败）；
- 合法 schema（None / 空 dict / 顶层 type=object）通过注册；
- /api/plugins 的 manifest 输出约定：每个插件含 schema，未声明统一为空 dict；
- 真实插件样例（status/safety/skills）声明了合法 schema。
"""

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
#  非法 schema → ValueError（开发期早失败）
# ════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("bad_schema", [
    "object",                   # str
    ["type", "object"],         # list
    ("type", "object"),         # tuple
    42,                         # int
    True,                       # bool
])
def test_register_plugin_rejects_non_dict_schema(bad_schema):
    with pytest.raises(ValueError, match=r"plugin bad: invalid schema"):
        register_plugin(Plugin(name="bad", version="1.0.0", schema=bad_schema))


@pytest.mark.parametrize("bad_schema", [
    {"properties": {"x": {"type": "string"}}},  # 缺顶层 type
    {"type": "array"},                          # 顶层类型非 object
    {"type": "string", "enum": ["a", "b"]},     # 顶层类型非 object
])
def test_register_plugin_rejects_schema_without_top_level_object(bad_schema):
    with pytest.raises(ValueError, match=r"plugin bad: invalid schema"):
        register_plugin(Plugin(name="bad", version="1.0.0", schema=bad_schema))


# ════════════════════════════════════════════════════════════════
#  合法 schema → 通过注册
# ════════════════════════════════════════════════════════════════

def test_register_plugin_accepts_none_schema():
    p = register_plugin(Plugin(name="ok_none", version="1.0.0", schema=None))
    assert p.schema is None


def test_register_plugin_accepts_empty_dict_schema():
    # 空 dict 是默认占位（未声明配置），合法
    p = register_plugin(Plugin(name="ok_empty", version="1.0.0"))
    assert p.schema == {}


def test_register_plugin_accepts_object_schema():
    schema = {
        "type": "object",
        "title": "测试",
        "properties": {
            "mood": {"type": "string", "enum": ["calm", "playful"]},
            "level": {"type": "integer", "minimum": 1},
            "enabled": {"type": "boolean", "default": True},
        },
    }
    p = register_plugin(Plugin(name="ok_schema", version="1.0.0", schema=schema))
    assert p.schema["type"] == "object"


# ════════════════════════════════════════════════════════════════
#  /api/plugins（manifest）输出约定
# ════════════════════════════════════════════════════════════════

def test_manifest_output_has_schema_contract():
    register_plugin(Plugin(name="with_schema", version="1.0.0", schema={
        "type": "object",
        "properties": {"enabled": {"type": "boolean"}},
    }))
    register_plugin(Plugin(name="no_schema", version="1.0.0"))
    register_plugin(Plugin(name="none_schema", version="1.0.0", schema=None))

    entries = {p["name"]: p for p in manifest()["plugins"]}

    assert "schema" in entries["with_schema"]
    assert entries["with_schema"]["schema"]["type"] == "object"
    # 未声明 schema 统一输出为空 dict（None 亦归一化）
    assert entries["no_schema"]["schema"] == {}
    assert entries["none_schema"]["schema"] == {}
    for entry in manifest()["plugins"]:
        assert isinstance(entry["schema"], dict)


# ════════════════════════════════════════════════════════════════
#  client_slot 协议（T4.2：前端动态装载声明）
# ════════════════════════════════════════════════════════════════

def test_plugin_client_slot_defaults_none():
    p = register_plugin(Plugin(name="plain", version="1.0.0"))
    assert p.client_slot is None


def test_manifest_outputs_client_slot():
    register_plugin(Plugin(name="plain", version="1.0.0"))
    register_plugin(Plugin(
        name="dynamic",
        version="1.0.0",
        client_slot={"slotId": "panels", "module": "/plugins/demo-ui.js"},
    ))
    entries = {p["name"]: p for p in manifest()["plugins"]}
    assert entries["plain"]["client_slot"] is None
    assert entries["dynamic"]["client_slot"] == {
        "slotId": "panels",
        "module": "/plugins/demo-ui.js",
    }


def test_demo_plugin_declares_client_slot():
    """真实 demo 插件声明 client_slot（T4.2 全链路演示的 manifest 半边）。"""
    import plugins.demo_plugin  # noqa: F401  （模块级 PLUGIN 即注册对象）
    p = plugins.demo_plugin.PLUGIN
    assert p.name == "demo"
    assert p.client_slot == {"slotId": "panels", "module": "/plugins/demo-ui.js"}
    assert p.submit_url == "/api/demo/config"


# ════════════════════════════════════════════════════════════════
#  真实插件样例（T3.1 验收：status/safety/skills 含合法 schema）
# ════════════════════════════════════════════════════════════════

def test_real_plugins_declare_valid_schema():
    """status/safety/skills 声明合法 schema；其余插件保持空 dict。"""
    import plugins.status
    import plugins.safety
    import plugins.skills
    import plugins.memory
    import plugins.chat

    for mod in (plugins.status, plugins.safety, plugins.skills):
        schema = mod.PLUGIN.schema
        assert isinstance(schema, dict), mod.__name__
        assert schema["type"] == "object", mod.__name__
        assert schema.get("properties"), f"{mod.__name__} 至少声明一个属性"

    for mod in (plugins.memory, plugins.chat):
        assert mod.PLUGIN.schema == {}, mod.__name__
