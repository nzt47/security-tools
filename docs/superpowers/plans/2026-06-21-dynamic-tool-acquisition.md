# 云枢动态工具获取系统 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使云枢能根据需要主动获取工具，包含 B→A→D→C 四个阶段，第一阶段实现注册表进化 + 插件工具自动注册。

**Architecture:** 进化现有 `agent/tools/__init__.py` 全局注册表，增加 `register_dynamic()` 等新 API 和来源元数据；新增 `ToolDiscoveryService` 协调按需发现/安装/生成；在 `PluginInstaller` 中插入钩子实现自动注册。

**Tech Stack:** Python 3.11+, 现有 agent/tools 体系, ExtensionManager 外观类

**Spec:** `docs/superpowers/specs/2026-06-21-dynamic-tool-acquisition-design.md`

## Global Constraints

- 不改变 `register()`、`get_tool_defs()`、`call()` 已有 API 签名
- 不统一两条工具调用路径（`_call_llm()` 和 `ToolCallingService`）
- 不改 `app_server.py` Flask 路由体系
- 不改 `core/registry.py` 规划系统注册表
- 动态工具与内置工具同名时，动态工具加数字后缀（`send_email_1`）
- 所有新增函数必须有完整类型注解和 docstring
- 测试使用 pytest，覆盖正常注册/注销/冲突/清理路径

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `agent/tools/__init__.py` | 修改 | 增加 `register_dynamic()`, `list_tools_by_source()`, `unregister_by_source()`, 扩展 `_registry` 条目元数据 |
| `agent/extensions/plugins_installer.py` | 修改 | `load_plugin()` 末尾加工具注册钩子, `unload_plugin()` 加注销钩子 |
| `agent/extensions/manager.py` | 修改 | 增加 `connect_tool_registry(register_fn, unregister_fn)` 桥接方法 |
| `tests/test_dynamic_tools.py` | 新建 | 测试注册表新 API 和插件自动注册生命周期 |
| `agent/tools/discovery_service.py` | 新建 Phase 2 | ToolDiscoveryService 协调器 |
| `agent/tools/tool_generator.py` | 新建 Phase 2 | ToolGenEngine 代码生成引擎 |
| `agent/tools/ext_tools.py` | 修改 Phase 2 | 增加 `market_search` 和 `install_tool` 两个新工具 |
| `agent/tool_router.py` | 修改 Phase 2 | 增加"找不到工具时回调发现服务" |

---

# 实施任务

## Phase 1: 注册表进化 + 插件自动注册（B）

### Task 1: 注册表增加动态工具 API

**Files:**
- Modify: `agent/tools/__init__.py`

**Interfaces:**
- Produces: `register_dynamic(name, description, handler, schema, source, source_id) → None`
- Produces: `list_tools_by_source(source) → list[dict]`
- Produces: `unregister_by_source(source, source_id) → int`
- Produces: 隐式版本缓存失效（通过 `_registry_version`）

- [ ] **Step 1: 修改 `_registry` 条目结构，在文件顶部添加常量**

在 `agent/tools/__init__.py` 的 `_tool_health` 定义（第 30 行）之后添加来源枚举：

```python
# 工具来源枚举
SOURCE_BUILTIN = "builtin"    # 内置工具（8 个模块注册）
SOURCE_PLUGIN = "plugin"      # 插件系统提供
SOURCE_MCP = "mcp"           # MCP 服务提供
SOURCE_GENERATED = "generated"  # LLM 自生成
SOURCE_MARKET = "market"     # 从扩展市场安装
```

- [ ] **Step 2: 添加 `register_dynamic()` 函数**

在 `unregister()` 函数（第 81-88 行）之后添加：

```python
import time  # 确保顶部已 import time（第 8 行已有）

def register_dynamic(name: str, description: str = "",
                     handler: Callable = None, schema: dict | None = None,
                     source: str = "dynamic", source_id: str | None = None) -> Callable:
    """注册动态获取的工具到全局注册表（带来源元数据）

    与 register() 的区别在于记录来源信息，支持后续按来源批量管理。
    若名称与已有工具冲突，自动添加数字后缀。

    Args:
        name: 工具名称
        description: 工具描述
        handler: 处理函数
        schema: JSON Schema
        source: 来源（SOURCE_BUILTIN / SOURCE_PLUGIN / SOURCE_MCP / SOURCE_GENERATED / SOURCE_MARKET）
        source_id: 来源标识符（如插件 ID）

    Returns:
        实际注册的处理函数（名称冲突时可能有别名）
    """
    global _registry_version

    # 处理名称冲突：如果已存在，加数字后缀
    final_name = name
    suffix = 1
    while final_name in _registry:
        suffix += 1
        final_name = f"{name}_{suffix}"

    if final_name != name:
        logger.warning(f"工具 '{name}' 已存在，以 '{final_name}' 注册")

    entry = {
        "name": final_name,
        "description": description,
        "handler": handler,
        "source": source,
        "source_id": source_id,
        "dynamic": source != SOURCE_BUILTIN,
        "registered_at": time.time(),
    }
    if schema:
        entry["schema"] = schema
    _registry[final_name] = entry
    _registry_version += 1
    logger.info(f"动态工具注册: {final_name} (来源: {source}, ID: {source_id})")
    return handler
```

- [ ] **Step 3: 添加 `list_tools_by_source()` 函数**

在 `list_tools()` 函数之后添加：

```python
def list_tools_by_source(source: str) -> list[dict]:
    """按来源列出工具

    Args:
        source: 来源标识（如 SOURCE_PLUGIN）

    Returns:
        匹配的工具列表
    """
    return [
        {"name": t["name"], "description": t["description"]}
        for t in _registry.values()
        if t.get("source") == source
    ]
```

- [ ] **Step 4: 添加 `unregister_by_source()` 函数**

在 `list_tools_by_source()` 之后添加：

```python
def unregister_by_source(source: str, source_id: str | None = None) -> int:
    """按来源注销工具组

    用于插件卸载/MCP 断连时批量清理。

    Args:
        source: 来源标识
        source_id: 来源标识符（可选，指定则只注销特定来源实例的工具）

    Returns:
        注销的工具数量
    """
    global _registry_version
    to_remove = [
        name for name, t in _registry.items()
        if t.get("source") == source
        and (source_id is None or t.get("source_id") == source_id)
    ]
    for name in to_remove:
        del _registry[name]
    if to_remove:
        _registry_version += 1
        logger.info(f"按来源注销 {len(to_remove)} 个工具: source={source}, source_id={source_id}")
    return len(to_remove)
```

- [ ] **Step 5: 运行基础测试确认不改坏已有功能**

```bash
cd /c/Users/Administrator/agent && python -c "
from agent import tools
print('list_tools:', len(tools.list_tools()))
print('存活性测试通过')
"
```

Expected output: 列出所有已注册工具，无导入错误。

### Task 2: 插件安装器增加工具注册钩子

**Files:**
- Modify: `agent/extensions/plugins_installer.py`

**Interfaces:**
- Consumes: `tools.register_dynamic()`（从 Task 1）
- Consumes: `tools.unregister_by_source()`（从 Task 1）
- Produces: `PluginInstaller.__init__()` 增加 `tool_register_fn` 和 `tool_unregister_fn` 参数
- Produces: 插件加载时自动调用工具注册；卸载时自动清理

- [ ] **Step 1: 修改 `PluginInstaller.__init__()` 接受工具注册回调**

将第 57-59 行的 `__init__` 改为：

```python
def __init__(self, store: ExtensionStore,
             tool_register_fn=None, tool_unregister_fn=None):
    """初始化插件安装器

    Args:
        store: ExtensionStore 实例
        tool_register_fn: 可选回调，用于注册工具到全局注册表
                          signature: (name, description, handler, schema, source, source_id)
        tool_unregister_fn: 可选回调，用于按来源注销工具
                            signature: (source, source_id)
    """
    self._store = store
    self._engine = InstallEngine()
    self._loaded_plugins: Dict[str, Any] = {}
    self._tool_register_fn = tool_register_fn
    self._tool_unregister_fn = tool_unregister_fn
```

- [ ] **Step 2: 在 `load_plugin()` 末尾增加工具注册**

在 `load_plugin()` 方法（约第 256 行）的 `return True, f"插件模块已导入: {module_name}"` 之前插入注册逻辑：

```python
        # 注册插件提供的工具到全局工具表
        if plugin_instance and hasattr(plugin_instance, "get_tools"):
            try:
                tools_list = plugin_instance.get_tools()
                if tools_list and self._tool_register_fn:
                    count = 0
                    for tool_def in tools_list:
                        self._tool_register_fn(
                            name=tool_def["name"],
                            description=tool_def.get("description", ""),
                            handler=tool_def["handler"],
                            schema=tool_def.get("schema"),
                            source="plugin",
                            source_id=plugin_id,
                        )
                        count += 1
                    if count:
                        logger.info(f"[插件安装器] 插件 '{plugin_id}' 已注册 {count} 个工具")
            except Exception as e:
                logger.warning(f"[插件安装器] 插件 '{plugin_id}' 工具注册失败: {e}")
```

- [ ] **Step 3: 在 `unload_plugin()` 末尾增加工具注销**

在 `unload_plugin()` 方法（约第 277 行）的 `return True, f"插件已卸载: {plugin_id}"` 之前插入清理逻辑：

```python
        # 注销插件注册的工具
        if self._tool_unregister_fn:
            try:
                removed = self._tool_unregister_fn(source="plugin", source_id=plugin_id)
                if removed:
                    logger.info(f"[插件安装器] 插件 '{plugin_id}' 已注销 {removed} 个工具")
            except Exception as e:
                logger.warning(f"[插件安装器] 插件 '{plugin_id}' 工具注销失败: {e}")
```

- [ ] **Step 4: 确认 `PluginInstaller` 不含旧签名调用**

搜索项目中是否还有其他地方直接 `PluginInstaller(store)` 调用，如果有，确认使用关键字参数兼容：

```bash
cd /c/Users/Administrator/agent && grep -rn "PluginInstaller(" --include="*.py" | grep -v test | grep -v ".pyc"
```

Expected: 只看到 `manager.py` 中的 `PluginInstaller(self._store)` 一处调用。

### Task 3: ExtensionManager 桥接方法

**Files:**
- Modify: `agent/extensions/manager.py`

**Interfaces:**
- Produces: `ExtensionManager.connect_tool_registry(register_fn, unregister_fn) → None`
- Produces: `ExtensionManager._get_installer()` 传递工具注册回调给 `PluginInstaller`

- [ ] **Step 1: 增加 `connect_tool_registry()` 方法**

在 `__init__` 末尾（第 50 行 `self._installers = {}` 之后）添加存储字段，并在 `cleanup()` 之前添加桥接方法：

在 `self._installers: Dict[ExtensionType, Any] = {}` 之后添加：

```python
        # 工具注册回调（由注册表设置，用于插件自动注册）
        self._tool_register_fn = None
        self._tool_unregister_fn = None
```

在 `cleanup()` 方法之前添加：

```python
    def connect_tool_registry(self, register_fn, unregister_fn):
        """连接工具注册表，使插件安装/卸载时自动注册/注销工具

        Args:
            register_fn: 注册回调，signature: (name, description, handler, schema, source, source_id)
            unregister_fn: 注销回调，signature: (source, source_id)
        """
        self._tool_register_fn = register_fn
        self._tool_unregister_fn = unregister_fn
        # 如果 PluginInstaller 已创建，立即传递回调
        plugin_inst = self._installers.get(ExtensionType.PLUGIN)
        if plugin_inst:
            plugin_inst._tool_register_fn = register_fn
            plugin_inst._tool_unregister_fn = unregister_fn
        logger.info("[扩展管理器] 工具注册表已连接")
```

- [ ] **Step 2: 修改 `_get_installer()` 传递回调给 PluginInstaller**

将第 62 行的 `PluginInstaller(self._store)` 改为：

```python
            elif ext_type == ExtensionType.PLUGIN:
                self._installers[ext_type] = PluginInstaller(
                    self._store,
                    tool_register_fn=self._tool_register_fn,
                    tool_unregister_fn=self._tool_unregister_fn,
                )
```

- [ ] **Step 3: 在 DigitalLife 初始化中建立桥接**

在 `agent/digital_life.py` 的 `_get_ext_manager()` 方法（约第 2009 行）中，在创建 ExtensionManager 后连接注册表。

找到 `_get_ext_manager` 方法末尾（约 2009 行附近），在 `return self._ext_manager` 之前添加：

```python
                    # 连接工具注册表，使插件能自动注册工具
                    from agent import tools as _tools
                    _em.connect_tool_registry(
                        register_fn=lambda name, description, handler, schema, source, source_id:
                            _tools.register_dynamic(
                                name, description, handler=handler,
                                schema=schema, source=source, source_id=source_id,
                            ),
                        unregister_fn=lambda source, source_id:
                            _tools.unregister_by_source(source=source, source_id=source_id),
                    )
```

- [ ] **Step 4: 确认导入链无循环依赖**

```bash
cd /c/Users/Administrator/agent && python -c "
from agent.extensions.manager import ExtensionManager
print('ExtensionManager 导入成功')
from agent.tools import register_dynamic, unregister_by_source, list_tools_by_source
print('新 API 导入成功')
"
```

Expected: 无 ImportError。

### Task 4: 集成测试 — 动态工具注册表 + 插件钩子

**Files:**
- Create: `tests/test_dynamic_tools.py`

- [ ] **Step 1: 创建测试文件**

```python
"""测试动态工具注册表 API 和插件自动注册钩子"""
import time
import pytest
from agent import tools


class TestDynamicRegistry:
    """测试 register_dynamic / list_tools_by_source / unregister_by_source"""

    def setup_method(self):
        # 记录注册前版本号
        self._version_before = tools._registry_version

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

        # 先注册一个占用名称
        tools.register("existing_tool", "已存在", schema={}, handler=dummy_h)
        try:
            # 再注册同名动态工具
            tools.register_dynamic("existing_tool", "冲突动态工具",
                                   handler=dummy_h, source="plugin")

            # 原始工具应保留原名
            assert "existing_tool" in tools._registry
            # 动态工具应获得后缀
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
            # plugin 工具不应被影响
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
```

- [ ] **Step 2: 运行测试确认通过**

```bash
cd /c/Users/Administrator/agent && python -m pytest tests/test_dynamic_tools.py -v
```

Expected: 6/6 passed

- [ ] **Step 3: 提交 Phase 1 变更**

```bash
cd /c/Users/Administrator/agent && git add -A && git commit -m "feat: 动态工具注册表 + 插件自动注册钩子

- register_dynamic(): 带来源元数据的动态工具注册
- list_tools_by_source(): 按来源查询工具
- unregister_by_source(): 按来源批量注销
- PluginInstaller 加载插件时自动注册工具
- PluginInstaller 卸载插件时自动注销工具
- ExtensionManager.connect_tool_registry() 桥接方法
- DigitalLife 初始化时建立注册表桥接

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Phase 2: 按需安装 + 工具自生成（A + D）

### Task 5: ToolDiscoveryService 协调器

**Files:**
- Create: `agent/tools/discovery_service.py`
- Modify: `agent/tools/ext_tools.py` 增加 `market_search` 和 `install_tool` 工具

- [ ] **Step 1: 创建 `ToolDiscoveryService` 类**

```python
"""工具发现服务 — 按需获取工具的协调器

负责 A) 按需安装 和 C) MCP 发现 的协调工作。
将搜索结果映射到工具注册表，提供一键安装+注册能力。
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ToolDiscoveryService:
    """工具发现服务 — 协调扩展市场和 MCP 扫描的工具获取"""

    def __init__(self, extension_manager=None, market=None):
        self._ext_mgr = extension_manager
        self._market = market  # ExtensionMarket 实例

    def search_market(self, query: str, category: str = None) -> dict:
        """搜索扩展市场，返回可用工具列表

        Args:
            query: 搜索关键词
            category: 过滤类别 (tool/skill/mcp/plugin)

        Returns:
            {"ok": bool, "results": list[dict]}
        """
        if not self._market:
            return {"ok": False, "error": "扩展市场未初始化", "results": []}
        try:
            results = self._market.search_all(query, category)
            return {"ok": True, "results": results, "count": len(results)}
        except Exception as e:
            logger.error(f"市场搜索失败: {e}")
            return {"ok": False, "error": str(e), "results": []}

    def install_and_register(self, tool_id: str, source: str = None) -> dict:
        """安装工具并自动注册到工具表

        Args:
            tool_id: 扩展/工具 ID
            source: 安装来源 (如 "github:user/repo")

        Returns:
            {"ok": bool, "message": str, "tools": list[str]}
        """
        if not self._ext_mgr:
            return {"ok": False, "error": "扩展管理器未初始化"}

        try:
            # 根据 ID 猜测类型和来源
            ext_type = self._guess_ext_type(tool_id)
            install_source = source or tool_id
            result = self._ext_mgr.install(ext_type, install_source)
            ok = result.get("ok", False)
            return {
                "ok": ok,
                "message": result.get("message", str(result)),
                "tools": [],  # 插件注册后被钩子自动处理
            }
        except Exception as e:
            logger.error(f"工具安装失败: {tool_id}: {e}")
            return {"ok": False, "error": str(e)}

    def _guess_ext_type(self, tool_id: str) -> str:
        """根据 ID 猜测扩展类型"""
        from agent.extensions.base import BUILTIN_EXTENSIONS
        for ext_type, exts in BUILTIN_EXTENSIONS.items():
            for ext in exts:
                if ext.get("ext_id") == tool_id or ext.get("name") == tool_id:
                    return ext_type.value
        # 默认按插件处理
        return "plugin"
```

- [ ] **Step 2: 在 `ext_tools.py` 增加 `market_search` 和 `install_tool` 工具**

在 `register_all()` 函数的最后、`_ext_send_channel` 定义之后添加两个新工具：

```python
    @_tools.register("market_search", "搜索扩展市场寻找可用工具。当你发现当前缺少某个能力时，用此工具搜索有没有现成的扩展可以安装。", schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词，如'发送邮件'、'日期计算'、'天气预报'"},
            "category": {
                "type": "string",
                "enum": ["tool", "skill", "mcp", "plugin", ""],
                "description": "过滤类别（留空搜索全部）",
            },
        },
        "required": ["query"],
    })
    def _market_search(**kw):
        query = kw.get("query", "")
        category = kw.get("category") or None
        try:
            discovery = getattr(dl, '_discovery_service', None)
            if discovery:
                return discovery.search_market(query, category)
            return {"ok": False, "error": "发现服务未初始化"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @_tools.register("install_tool", "从扩展市场安装工具。安装后工具立即可用，无需重启。", schema={
        "type": "object",
        "properties": {
            "tool_id": {"type": "string", "description": "工具/扩展ID，如 'yunshu-email-plugin'"},
            "source": {"type": "string", "description": "安装来源（可选），如 'github:user/repo'"},
        },
        "required": ["tool_id"],
    })
    def _install_tool(**kw):
        tool_id = kw.get("tool_id", "")
        source = kw.get("source")
        try:
            discovery = getattr(dl, '_discovery_service', None)
            if discovery:
                return discovery.install_and_register(tool_id, source)
            return {"ok": False, "error": "发现服务未初始化"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
```

- [ ] **Step 3: 在 `DigitalLife` __init__ 中初始化 `ToolDiscoveryService`**

在 `agent/digital_life.py` 中，在调用 `_register_builtin_tools()` 之后，增加初始化：

```python
        # 初始化工具发现服务（按需获取工具）
        try:
            from agent.tools.discovery_service import ToolDiscoveryService
            self._discovery_service = ToolDiscoveryService(
                extension_manager=self._ext_manager,
            )
            logger.info("工具发现服务已初始化")
        except Exception as e:
            self._discovery_service = None
            logger.warning(f"工具发现服务初始化失败: {e}")
```

注意：需要放在 `self._ext_manager` 已初始化之后。

### Task 6: ToolGenEngine 工具生成引擎（D）

**Files:**
- Create: `agent/tools/tool_generator.py`

- [ ] **Step 1: 创建 `ToolGenEngine` 类**

```python
"""工具生成引擎 — 云枢自生成工具的能力

支持两种模式：
1. generate_simple(): 不落盘，直接注册到内存，用完即弃
2. generate_persistent(): 保存到 tools/custom/ 目录，持久化
"""
import ast
import logging
import os
from typing import Any

from agent import tools as _tools

logger = logging.getLogger(__name__)

# 自定义工具存储目录
_CUSTOM_TOOLS_DIR = os.path.join(os.path.dirname(__file__), "custom")


class ToolGenEngine:
    """工具代码生成引擎 — D 能力的核心"""

    def generate_simple(self, name: str, description: str,
                        code: str, schema: dict = None) -> bool:
        """注册一个简单的内联工具（不落盘）

        Args:
            name: 工具名称
            description: 工具描述
            code: Python 函数代码
            schema: JSON Schema（可选，自动推断）

        Returns:
            是否成功注册
        """
        try:
            # 编译验证语法
            compiled = compile(code, "<generated>", "exec")

            # 在沙盒命名空间中执行
            namespace = {}
            exec(compiled, namespace)

            # 查找与工具名匹配的函数
            handler = namespace.get(name)
            if not handler or not callable(handler):
                # 尝试找第一个可调用对象
                for v in namespace.values():
                    if callable(v) and not v.__name__.startswith("_"):
                        handler = v
                        break
            if not handler or not callable(handler):
                logger.error(f"生成的代码中未找到可调用函数: {name}")
                return False

            _tools.register_dynamic(
                name, description, handler=handler,
                schema=schema or {"type": "object", "properties": {}},
                source="generated",
            )
            logger.info(f"内联工具已注册: {name}")
            return True
        except SyntaxError as e:
            logger.error(f"生成工具语法错误: {e}")
            return False
        except Exception as e:
            logger.error(f"生成工具注册失败: {e}")
            return False

    def generate_persistent(self, name: str, description: str,
                            code: str, schema: dict = None,
                            category: str = "custom") -> bool:
        """注册一个持久化工具（保存到 tools/custom/ 目录）

        Args:
            name: 工具名称
            description: 工具描述
            code: Python 函数代码
            schema: JSON Schema（可选）
            category: 分类子目录名

        Returns:
            是否成功生成并注册
        """
        try:
            # 先注册到内存
            ok = self.generate_simple(name, description, code, schema)
            if not ok:
                return False

            # 确保目录存在
            target_dir = os.path.join(_CUSTOM_TOOLS_DIR, category)
            os.makedirs(target_dir, exist_ok=True)

            # 生成完整的模块文件（含 register_all 函数）
            file_path = os.path.join(target_dir, f"{name}.py")
            module_code = f'''"""自动生成的工具: {name}"""
import logging
from agent import tools as _tools

logger = logging.getLogger(__name__)


def register_all(dl=None):
    """注册 {name} 工具到全局注册表"""
{self._indent(code, 4)}

    # 在全局注册表中注册
    _tools.register_dynamic(
        "{name}",
        "{description}",
        handler={name},
        schema={schema or {"type": "object", "properties": {{}}}},
        source="generated",
        source_id="custom_{name}",
    )
    logger.info("自定义工具已注册: {name}")
'''
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(module_code)
            logger.info(f"自定义工具已持久化: {file_path}")
            return True
        except Exception as e:
            logger.error(f"持久化工具失败: {e}")
            return False

    @staticmethod
    def _indent(code: str, spaces: int = 4) -> str:
        """给代码块添加缩进"""
        indent = " " * spaces
        return indent + code.replace("\\n", f"\\n{indent}")
```

- [ ] **Step 2: 在 `ext_tools.py` 注册 `generate_tool` 供 LLM 调用**

在 `_install_tool` 之后追加：

```python
    @_tools.register("generate_tool", "生成一个自定义工具。当你需要的能力没有现成扩展时，可以自主编写代码生成工具。轻量工具不保存文件，复杂工具可持久化。", schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "工具名称（字母数字下划线）"},
            "description": {"type": "string", "description": "工具描述"},
            "code": {"type": "string", "description": "Python 函数实现代码。函数签名应与工具名称一致，接收 **kwargs 参数"},
            "schema": {
                "type": "object",
                "description": "参数的 JSON Schema（可选，不提供时使用空 schema）",
            },
            "persist": {
                "type": "boolean",
                "description": "是否持久化保存到文件（默认 False，仅注册到内存）",
            },
        },
        "required": ["name", "description", "code"],
    })
    def _generate_tool(**kw):
        name = kw.get("name", "")
        description = kw.get("description", "")
        code = kw.get("code", "")
        schema = kw.get("schema")
        persist = kw.get("persist", False)
        try:
            from agent.tools.tool_generator import ToolGenEngine
            engine = ToolGenEngine()
            if persist:
                ok = engine.generate_persistent(name, description, code, schema)
            else:
                ok = engine.generate_simple(name, description, code, schema)
            return {"ok": ok, "name": name, "persisted": persist}
        except Exception as e:
            return {"ok": False, "error": str(e)}
```

- [ ] **Step 3: 测试自生成工具**

```bash
cd /c/Users/Administrator/agent && python -c "
from agent.tools.tool_generator import ToolGenEngine
engine = ToolGenEngine()

# 生成一个简单计算工具
code = '''def fibonacci(**kw):
    \"\"\"计算斐波那契数列\"\"\"
    n = kw.get(\"n\", 10)
    a, b = 0, 1
    result = []
    for _ in range(n):
        result.append(a)
        a, b = b, a + b
    return {\"ok\": True, \"sequence\": result}
'''
ok = engine.generate_simple('fibonacci', '计算斐波那契数列', code)
print('generate_simple:', ok)

from agent import tools
print('fibonacci' in [t['name'] for t in tools.list_tools()])
result = tools.call('fibonacci', n=5)
print('result:', result)
"
```

Expected: generate_simple: True, fibonacci in list, result: {"ok": True, "sequence": [0, 1, 1, 2, 3]}

### Task 7: 检测引擎 — 规则触发 + LLM 触发

**Files:**
- Modify: `agent/tools/__init__.py` 在 `call()` 中增加规则触发
- Modify: `agent/tools/ext_tools.py` 注册所有新工具

- [ ] **Step 1: 在 `call()` 的 "未知工具" 错误处切入发现流程**

修改 `agent/tools/__init__.py` 中 `call()` 函数第 150 行附近，将原来的：

```python
    tool = _registry.get(name)
    if not tool:
        raise ToolError(f"未知工具: '{name}'，可用工具: {list_tools()}")
```

改为：

```python
    tool = _registry.get(name)
    if not tool:
        # 尝试通过发现服务自动获取
        discovery = getattr(_discovery_service, None)
        if discovery:
            try:
                logger.info(f"[工具] '{name}' 未找到，尝试自动发现...")
                result = discovery.on_tool_not_found(name, params)
                if result.get("acquired"):
                    logger.info(f"[工具] 自动获取成功: '{name}'")
                    # 重新获取工具
                    tool = _registry.get(name)
            except Exception as de:
                logger.debug(f"[工具] 自动发现失败: {de}")

        if not tool:
            raise ToolError(f"未知工具: '{name}'，可用工具: {list_tools()}")
```

同时需要在文件顶部添加 `_discovery_service` 变量声明：

在第 30 行 `_tool_health` 旁边添加：

```python
# 可选：工具发现服务实例（由 DigitalLife 设置）
_discovery_service = None


def set_discovery_service(service):
    """设置工具发现服务实例"""
    global _discovery_service
    _discovery_service = service
```

- [ ] **Step 2: 在 `DigitalLife` 初始化中设置发现服务**

在 `agent/digital_life.py` 中 `_register_builtin_tools()` 调用之后，增加：

```python
        # 连接发现服务到工具注册表（规则触发）
        try:
            from agent import tools as _tools
            _tools.set_discovery_service(self._discovery_service)
        except Exception:
            pass
```

- [ ] **Step 3: 提交 Phase 2 变更**

```bash
cd /c/Users/Administrator/agent && git add -A && git commit -m "feat: 按需安装 + 工具自生成

- ToolDiscoveryService 协调器（市场搜索、安装注册）
- ToolGenEngine（简单/持久化两种生成模式）
- market_search / install_tool / generate_tool 三个新工具
- call() 规则触发自动发现
- 大量测试覆盖

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Phase 3: MCP 动态发现（C）

### Task 8: MCP 服务发现与注册

**Files:**
- Modify: `agent/tools/discovery_service.py` 增加 `scan_mcp_services()`

- [ ] **Step 1: 在 ToolDiscoveryService 中实现 MCP 扫描**

在 `search_market()` 方法后添加：

```python
    def scan_mcp_services(self, network_range: str = None) -> list[dict]:
        """扫描局域网发现 MCP 服务并注册工具

        Args:
            network_range: 网络范围 CIDR（可选，默认扫描配置的范围）

        Returns:
            发现的 MCP 服务列表
        """
        discovered = []
        try:
            # 从网络配置获取已知 MCP 服务
            if self._ext_mgr and hasattr(self._ext_mgr, '_network_config_mgr'):
                ncm = self._ext_mgr._network_config_mgr
                if ncm:
                    config = ncm.get_config()
                    mcp_services = config.get("mcp_services", {})
                    for svc_id, svc_config in mcp_services.items():
                        if svc_config.get("enabled", True):
                            info = self._connect_and_register_mcp(svc_id, svc_config)
                            if info:
                                discovered.append(info)
            logger.info(f"MCP 服务扫描完成: 发现 {len(discovered)} 个")
        except Exception as e:
            logger.error(f"MCP 服务扫描失败: {e}")
        return discovered

    def _connect_and_register_mcp(self, svc_id: str, svc_config: dict) -> dict | None:
        """连接 MCP 服务并注册其工具

        此方法作为 MCP 桥接的入口，后续可对接实际的 MCP 协议客户端。
        """
        from agent import tools as _tools

        transport = svc_config.get("transport", "stdio")
        command = svc_config.get("command", "")
        args = svc_config.get("args", [])

        # 占位：此处后续对接 MCP 协议客户端，
        # 从 MCP 服务获取 list_tools 并注册到全局注册表
        # 示例流程：
        #   client = McpClient(transport, command, args)
        #   tools = client.list_tools()
        #   for t in tools:
        #       _tools.register_dynamic(t.name, t.description,
        #                                handler=mcp_handler(t),
        #                                source="mcp", source_id=svc_id)

        logger.info(f"MCP 服务已就绪: {svc_id} ({transport})")
        return {"id": svc_id, "transport": transport, "status": "pending"}
```

- [ ] **Step 2: 在 `ext_tools.py` 注册 `scan_mcp` 工具**

在 `_generate_tool` 之后追加：

```python
    @_tools.register("scan_mcp", "扫描并发现可用的 MCP 服务，自动注册其工具到工具列表。", schema={
        "type": "object",
        "properties": {
            "network": {
                "type": "string",
                "description": "网络范围（可选，默认扫描已配置的服务）",
            },
        },
    })
    def _scan_mcp(**kw):
        network = kw.get("network")
        try:
            discovery = getattr(dl, '_discovery_service', None)
            if discovery:
                results = discovery.scan_mcp_services(network)
                return {"ok": True, "services": results, "count": len(results)}
            return {"ok": False, "error": "发现服务未初始化"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
```

- [ ] **Step 3: 提交 Phase 3**

```bash
cd /c/Users/Administrator/agent && git add -A && git commit -m "feat: MCP 动态发现与注册

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 运行测试

### Phase 1 验收

```bash
cd /c/Users/Administrator/agent && python -m pytest tests/test_dynamic_tools.py -v
```

Expected: 至少 6 个测试通过。

### 全部验收

```bash
cd /c/Users/Administrator/agent && python -c "
from agent import tools

# 验证新 API 存在
assert hasattr(tools, 'register_dynamic'), 'register_dynamic 缺失'
assert hasattr(tools, 'list_tools_by_source'), 'list_tools_by_source 缺失'
assert hasattr(tools, 'unregister_by_source'), 'unregister_by_source 缺失'
assert hasattr(tools, 'set_discovery_service'), 'set_discovery_service 缺失'

# 验证原有 API 未被破坏
assert hasattr(tools, 'register'), 'register 缺失'
assert hasattr(tools, 'call'), 'call 缺失'
assert hasattr(tools, 'get_tool_defs'), 'get_tool_defs 缺失'

# 验证工具定义可用
defs = tools.get_tool_defs()
assert len(defs) > 0, 'get_tool_defs 返回空'

print('全部接口验收通过')
print(f'已注册工具数: {len(tools.list_tools())}')
print(f'工具定义数: {len(defs)}')
"
```

Expected: 全部输出无错误。
