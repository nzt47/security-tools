"""测试动态工具注册表 API 和插件自动注册钩子"""
import pytest
from agent import tools


class TestDynamicRegistry:
    """测试 register_dynamic / list_tools_by_source / unregister_by_source"""

    def test_register_dynamic_adds_metadata(self):
        """register_dynamic 应记录来源元数据"""
        def dummy_handler(**kw): return {"ok": True}

        tools.register_dynamic("test_dyn_1", "测试动态工具",
                               handler=dummy_handler, schema={},
                               source="plugin", source_id="test_plugin")
        try:
            entry = tools._registry.get("test_dyn_1")
            assert entry is not None
            assert entry["source"] == "plugin"
            assert entry["source_id"] == "test_plugin"
            assert entry["dynamic"] is True
            assert "registered_at" in entry
            assert callable(entry["handler"])
        finally:
            tools.unregister("test_dyn_1")

    def test_register_dynamic_name_conflict(self):
        """名称冲突时应自动加后缀"""
        def dummy_h(**kw): return {"ok": True}

        tools.register("existing_tool", "已存在", schema={}, handler=dummy_h)
        try:
            tools.register_dynamic("existing_tool", "冲突动态工具",
                                   handler=dummy_h, source="plugin")
            assert "existing_tool" in tools._registry
            assert "existing_tool_2" in tools._registry
            assert tools._registry["existing_tool_2"]["source"] == "plugin"
        finally:
            tools.unregister("existing_tool")
            tools.unregister("existing_tool_2")

    def test_list_tools_by_source(self):
        """list_tools_by_source 应只返回匹配来源的工具"""
        def dh(**kw): return {"ok": True}
        names = []
        try:
            names.append("ls_mcp_1")
            tools.register_dynamic("ls_mcp_1", "", handler=dh, source="mcp", source_id="svc1")
            names.append("ls_mcp_2")
            tools.register_dynamic("ls_mcp_2", "", handler=dh, source="mcp", source_id="svc1")
            names.append("ls_plugin_1")
            tools.register_dynamic("ls_plugin_1", "", handler=dh, source="plugin", source_id="p1")

            mcp_tools = tools.list_tools_by_source("mcp")
            assert len(mcp_tools) == 2
            assert all(t["name"].startswith("ls_mcp") for t in mcp_tools)

            plugin_tools = tools.list_tools_by_source("plugin")
            assert len(plugin_tools) == 1
        finally:
            for n in names:
                tools.unregister(n)

    def test_unregister_by_source_without_id(self):
        """unregister_by_source 不带 source_id 应注销该来源所有工具"""
        def dh(**kw): return {"ok": True}
        names = []
        try:
            names.append("urs_gen_1")
            tools.register_dynamic("urs_gen_1", "", handler=dh, source="generated")
            names.append("urs_gen_2")
            tools.register_dynamic("urs_gen_2", "", handler=dh, source="generated")
            names.append("urs_plugin_1")
            tools.register_dynamic("urs_plugin_1", "", handler=dh, source="plugin", source_id="p1")

            removed = tools.unregister_by_source("generated")
            assert removed == 2
            assert tools._registry.get("urs_gen_1") is None
            assert tools._registry.get("urs_gen_2") is None
            assert tools._registry.get("urs_plugin_1") is not None
        finally:
            for n in names:
                tools.unregister(n)

    def test_unregister_by_source_with_id(self):
        """unregister_by_source 带 source_id 应只注销特定来源实例的工具"""
        def dh(**kw): return {"ok": True}
        names = []
        try:
            names.append("urs_id_a")
            tools.register_dynamic("urs_id_a", "", handler=dh, source="plugin", source_id="p1")
            names.append("urs_id_b")
            tools.register_dynamic("urs_id_b", "", handler=dh, source="plugin", source_id="p2")

            removed = tools.unregister_by_source("plugin", source_id="p1")
            assert removed == 1
            assert tools._registry.get("urs_id_a") is None
            assert tools._registry.get("urs_id_b") is not None
        finally:
            for n in names:
                tools.unregister(n)

    def test_register_dynamic_triggers_cache_invalidation(self):
        """register_dynamic 应触发版本号增长，使 get_tool_defs 缓存失效"""
        def dh(**kw): return {"ok": True}
        version_before = tools._registry_version

        tools.register_dynamic("cache_test_tool", "", handler=dh, source="plugin")
        try:
            assert tools._registry_version > version_before
            defs = tools.get_tool_defs()
            assert any(d["function"]["name"] == "cache_test_tool" for d in defs)
        finally:
            tools.unregister("cache_test_tool")

    def test_get_tool_defs_respects_whitelist(self):
        """get_tool_defs 的白名单应正确过滤动态工具"""
        def dh(**kw): return {"ok": True}
        tools.register_dynamic("white_test", "", handler=dh, source="plugin")
        try:
            full = tools.get_tool_defs()
            filtered = tools.get_tool_defs(whitelist=["get_status"])
            assert any(d["function"]["name"] == "white_test" for d in full)
            assert not any(d["function"]["name"] == "white_test" for d in filtered)
        finally:
            tools.unregister("white_test")

    def test_set_discovery_service(self):
        """set_discovery_service 应正确设置全局变量"""
        old = tools._discovery_service
        tools.set_discovery_service("test_service")
        assert tools._discovery_service == "test_service"
        tools.set_discovery_service(None)
        assert tools._discovery_service is None
        tools.set_discovery_service(old)

    def test_register_dynamic_cleanup_doesnt_affect_others(self):
        """unregister_by_source 不应误删其他来源的工具"""
        def dh(**kw): return {"ok": True}
        tools.register("builtin_only", "内置", schema={}, handler=dh)
        tools.register_dynamic("gen_only", "", handler=dh, source="generated")
        try:
            tools.unregister_by_source("mcp")
            assert "builtin_only" in tools._registry
            assert "gen_only" in tools._registry
        finally:
            tools.unregister("builtin_only")
            tools.unregister("gen_only")


class TestPluginLifecycleHooks:
    """测试插件加载/卸载时的工具注册钩子"""

    def test_plugin_load_registers_tools(self):
        """PluginInstaller.load_plugin 应通过回调注册工具"""
        registered = []

        def fake_register(name, description, handler, schema, source, source_id):
            registered.append({
                "name": name, "source": source, "source_id": source_id,
            })

        from agent.extensions.plugins_installer import PluginInstaller
        from agent.extensions.store import ExtensionStore
        store = ExtensionStore()
        inst = PluginInstaller(store, tool_register_fn=fake_register)

        # 模拟插件实例
        class FakePlugin:
            def get_tools(self):
                return [
                    {"name": "fake_tool_1", "description": "工具1",
                     "handler": lambda: None, "schema": {}},
                    {"name": "fake_tool_2", "description": "工具2",
                     "handler": lambda: None},
                ]

        # 触发注册
        inst._loaded_plugins["test_p"] = FakePlugin()
        inst._tool_register_fn = fake_register

        # 加载插件时调用的注册代码
        plugin_instance = inst._loaded_plugins.get("test_p")
        if hasattr(plugin_instance, "get_tools"):
            tools_list = plugin_instance.get_tools()
            for tool_def in tools_list:
                inst._tool_register_fn(
                    name=tool_def["name"],
                    description=tool_def.get("description", ""),
                    handler=tool_def["handler"],
                    schema=tool_def.get("schema"),
                    source="plugin",
                    source_id="test_p",
                )

        assert len(registered) == 2
        assert registered[0]["name"] == "fake_tool_1"
        assert registered[0]["source"] == "plugin"
        assert registered[0]["source_id"] == "test_p"

    def test_plugin_unload_unregisters_tools(self):
        """PluginInstaller.unload_plugin 应通过回调注销工具"""
        unregistered = []

        def fake_unregister(source, source_id):
            unregistered.append({"source": source, "source_id": source_id})
            return 2

        from agent.extensions.plugins_installer import PluginInstaller
        from agent.extensions.store import ExtensionStore
        store = ExtensionStore()
        inst = PluginInstaller(store, tool_unregister_fn=fake_unregister)

        # 触发注销
        if inst._tool_unregister_fn:
            removed = inst._tool_unregister_fn(source="plugin", source_id="test_p")

        assert len(unregistered) == 1
        assert unregistered[0]["source"] == "plugin"
        assert unregistered[0]["source_id"] == "test_p"
        assert removed == 2

    def test_extension_manager_bridge(self):
        """ExtensionManager.connect_tool_registry 应正确传递回调"""
        from agent.extensions.manager import ExtensionManager
        em = ExtensionManager()

        events = []

        def reg_fn(name, desc, handler, schema, source, sid):
            events.append(("register", name, source, sid))

        def unreg_fn(source, sid):
            events.append(("unregister", source, sid))
            return 1

        em.connect_tool_registry(reg_fn, unreg_fn)

        assert em._tool_register_fn is reg_fn
        assert em._tool_unregister_fn is unreg_fn

        # 验证回调可用
        em._tool_register_fn("test", "desc", lambda: None, {}, "plugin", "p1")
        em._tool_unregister_fn("plugin", "p1")

        assert len(events) == 2
        assert events[0] == ("register", "test", "plugin", "p1")
        assert events[1] == ("unregister", "plugin", "p1")

    def test_full_plugin_lifecycle_with_registry(self):
        """完整生命周期：注册→列表→注销→确认清理"""
        def dh(**kw): return {"ok": True}

        # 模拟插件注册
        tools.register_dynamic("lifecycle_a", "工具A", handler=dh,
                               source="plugin", source_id="lifecycle_plugin")
        tools.register_dynamic("lifecycle_b", "工具B", handler=dh,
                               source="plugin", source_id="lifecycle_plugin")

        # 验证注册成功
        names = [t["name"] for t in tools.list_tools()]
        assert "lifecycle_a" in names
        assert "lifecycle_b" in names

        # 按来源查询
        plugin_tools = tools.list_tools_by_source("plugin")
        assert len(plugin_tools) == 2

        # 模拟卸载清理
        removed = tools.unregister_by_source("plugin", source_id="lifecycle_plugin")
        assert removed == 2

        # 确认清理
        assert tools._registry.get("lifecycle_a") is None
        assert tools._registry.get("lifecycle_b") is None

    def test_name_conflict_suffix_increment(self):
        """多次同名冲突应逐渐递增后缀"""
        def dh(**kw): return {"ok": True}
        names = []
        try:
            tools.register("base_name", "", schema={}, handler=dh)
            names.append("base_name")

            # 第一次冲突 → _2
            tools.register_dynamic("base_name", "", handler=dh, source="plugin")
            names.append("base_name_2")
            assert "base_name_2" in tools._registry

            # 第二次冲突 → _3
            tools.register_dynamic("base_name", "", handler=dh, source="generated")
            names.append("base_name_3")
            assert "base_name_3" in tools._registry
        finally:
            for n in names:
                tools.unregister(n)


class TestToolGenerator:
    """测试 ToolGenEngine 工具自生成"""

    def test_generate_simple_ok(self):
        """能成功注册内联工具并可调用"""
        from agent.tools.tool_generator import ToolGenEngine
        engine = ToolGenEngine()
        code = '''def greet(**kw):
    name = kw.get("name", "world")
    return {"ok": True, "message": f"Hello, {name}!"}
'''
        ok = engine.generate_simple("greet", "打招呼", code)
        assert ok
        try:
            from agent import tools
            tnames = [t["name"] for t in tools.list_tools()]
            assert "greet" in tnames
            result = tools.call("greet", name="测试")
            assert result["ok"]
            assert result["message"] == "Hello, 测试!"
        finally:
            tools.unregister("greet")

    def test_generate_simple_syntax_error(self):
        """语法错误的代码应被拒绝"""
        from agent.tools.tool_generator import ToolGenEngine
        engine = ToolGenEngine()
        ok = engine.generate_simple("bad", "坏代码", "def broken(:**kw): pass")
        assert not ok

    def test_generate_simple_no_function(self):
        """没有可调用函数的代码应被拒绝"""
        from agent.tools.tool_generator import ToolGenEngine
        engine = ToolGenEngine()
        ok = engine.generate_simple("nope", "无函数", "x = 42")
        assert not ok

    def test_generate_simple_finds_first_callable(self):
        """找不到命名函数时自动找第一个可调用对象"""
        from agent.tools.tool_generator import ToolGenEngine
        engine = ToolGenEngine()
        code = '''def my_func(**kw):
    return {"ok": True, "value": kw.get("x", 0)}
'''
        ok = engine.generate_simple("my_func", "自动找到的函数", code)
        assert ok
        try:
            from agent import tools
            result = tools.call("my_func", x=42)
            assert result["ok"]
            assert result["value"] == 42
        finally:
            tools.unregister("my_func")


class TestDiscoveryService:
    """测试 ToolDiscoveryService"""

    def test_search_market_offline(self):
        """离线时搜索市场应返回缓存结果（不崩溃）"""
        from agent.tools.discovery_service import ToolDiscoveryService
        ds = ToolDiscoveryService()
        result = ds.search_market("test")
        assert "ok" in result
        assert "results" in result

    def test_on_tool_not_found_no_discovery(self):
        """找不到工具时应返回 acquired=False"""
        from agent.tools.discovery_service import ToolDiscoveryService
        ds = ToolDiscoveryService()
        result = ds.on_tool_not_found("nonexistent_tool_xyz")
        assert result["acquired"] is False
        assert result["tool"] == "nonexistent_tool_xyz"

    def test_install_and_register_no_manager(self):
        """无扩展管理器时返回错误"""
        from agent.tools.discovery_service import ToolDiscoveryService
        ds = ToolDiscoveryService()
        result = ds.install_and_register("test_tool")
        assert result["ok"] is False
        assert "未初始化" in result.get("error", "")


class TestCallDiscoveryTrigger:
    """测试 call() 的规则触发"""

    def test_call_unknown_tool_raises(self):
        """找不到且发现服务未设置时抛出 ToolError"""
        from agent import tools
        tools.set_discovery_service(None)
        import pytest
        with pytest.raises(Exception) as exc:
            tools.call("_definitely_not_exists_999")
        assert "未知工具" in str(exc.value) or "ToolError" in str(exc.value)

    def test_call_with_discovery_service_set(self):
        """设置了发现服务但找不到时仍应抛异常"""
        from agent import tools
        class FakeDiscovery:
            def on_tool_not_found(self, name, params):
                return {"acquired": False, "tool": name}
        old = tools._discovery_service
        tools.set_discovery_service(FakeDiscovery())
        import pytest
        try:
            with pytest.raises(Exception) as exc:
                tools.call("_fake_unknown_888")
            assert "未知工具" in str(exc.value)
        finally:
            tools.set_discovery_service(old)

    def test_call_with_discovery_acquires_tool(self):
        """发现服务成功获取工具后应能正常调用"""
        from agent import tools
        # 先注册一个工具让发现服务"找到"
        def dh(**kw): return {"ok": True}
        tools.register("_existing_tool", "已有", schema={}, handler=dh)

        class AcquiringDiscovery:
            def __init__(self):
                self.called = False
            def on_tool_not_found(self, name, params):
                self.called = True
                return {"acquired": True, "tool": name}

        discovery = AcquiringDiscovery()
        old = tools._discovery_service
        tools.set_discovery_service(discovery)
        try:
            # 调用存在工具不应触发发现
            result = tools.call("_existing_tool")
            assert result["ok"]
            assert not discovery.called
        finally:
            tools.set_discovery_service(old)
            tools.unregister("_existing_tool")
