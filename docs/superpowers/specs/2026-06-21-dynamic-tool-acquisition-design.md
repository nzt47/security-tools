# 云枢动态工具获取系统设计

## 概述

使云枢能根据需要主动获取工具，打破启动时静态注册的局限。包含四个层次的能力（按优先级排序）：

- **A) 按需安装** — LLM 处理请求时发现缺少工具，自动搜索扩展市场并安装
- **B) 插件工具自动注册** — 插件安装后，其工具自动进入全局工具注册表
- **C) MCP 动态发现** — 自动发现局域网/公网上的 MCP 服务并注册其工具
- **D) 工具自主生成** — 找不到现成工具时，云枢自己写代码生成工具

设计取「方案 1：工具注册表进化为中心枢纽」路线，以最小侵入方式增强现有 `agent/tools/__init__.py` 注册表。

## 架构

```
┌─ 工具来源 ──────────────────────────────────────────────────────────┐
│                                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐ │
│  │ 现有内置  │  │ 插件系统  │  │ MCP 服务  │  │ 扩展市场 (社区索引)  │ │
│  │ 8 个模块  │  │ Plugin   │  │ TCP/SSE  │  │ GitHub index        │ │
│  └─────┬────┘  └────┬─────┘  └────┬─────┘  └──────────┬───────────┘ │
│        │            │             │                    │             │
│        ▼            ▼             ▼                    ▼             │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                  ToolDiscoveryService                        │   │
│  │   ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────┐ │   │
│  │   │ 检测引擎   │ │ 市场搜索   │ │ 安装调度   │ │ 代码生成 │ │   │
│  │   │(LLM+规则)  │ │(Market)   │ │(Install)   │ │(CodeGen) │ │   │
│  │   └────────────┘ └────────────┘ └────────────┘ └──────────┘ │   │
│  └──────────────────────────────┬────────────────────────────────┘   │
│                                 │ register_dynamic()                 │
└─────────────────────────────────┼────────────────────────────────────┘
                                  │
┌─ 工具注册表 ─────────────────────────────────────────────────────────┐
│  Global _registry (agent/tools/__init__.py)                          │
│  ┌──────────┬──────────┬──────────┬──────────┬──────────────────┐   │
│  │ register │register  │unregister│list_tools│   call()         │   │
│  │ (现有)   │_dynamic  │ (已有)   │ (已有)   │ (已有→按来源路由) │   │
│  │          │ (新增)   │          │          │                  │   │
│  └──────────┴──────────┴──────────┴──────────┴──────────────────┘   │
│  每条工具记录增加 source 元数据字段                                    │
└──────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼ get_tool_defs() (版本缓存，两边都能用)
┌─ LLM 调用层 ─────────────────────────────────────────────────────────┐
│  _call_llm() (V1)    +    ToolCallingService (V2)                    │
└──────────────────────────────────────────────────────────────────────┘
```

### 核心变化

**1. `_registry` 条目增加 `source` 字段**

每条工具记录从 `{name, description, handler, schema}` 扩展为：
```python
{
    "name": "tool_name",
    "description": "...",
    "handler": callable,
    "schema": {...},
    "source": "builtin" | "plugin" | "mcp" | "generated" | "market",
    "source_id": "plugin_id" | None,      # 来源标识符
    "dynamic": True | False,              # 是否动态注册
    "registered_at": timestamp,
}
```

**2. 新增 `register_dynamic()`**

```python
def register_dynamic(name, description, handler, schema=None,
                     source="dynamic", source_id=None):
    """注册动态获取的工具到全局注册表
    
    与 register() 的区别在于记录来源元数据，支持后续按来源批量管理。
    """
```

**3. 新增 `list_tools_by_source()`**

```python
def list_tools_by_source(source: str) -> list[dict]:
    """按来源列出工具"""
```

**4. 新增 `unregister_by_source()`**

```python
def unregister_by_source(source: str, source_id: str = None) -> int:
    """按来源注销工具组（插件卸载/MCP 断连时清理）"""
```

## 模块职责

### `agent/tools/__init__.py` — 注册表进化

**新增功能：**
- `register_dynamic()` — 注册动态获取的工具
- `list_tools_by_source()` — 按来源列出工具（用于管理界面）
- `unregister_by_source()` — 按来源批量注销（插件卸载时调用）
- `_registry` 条目增加 `source` 元数据（不破坏现有注册）

**不改变：**
- `register()` — 保持原样，内置工具仍用它注册
- `get_tool_defs()` — 缓存机制不变，版本号更新自动刷新
- `call()` — 执行逻辑不变
- 所有已有 API 签名

### `agent/tools/discovery_service.py` — 发现协调器（新增）

协调 A/C/D 三个主动获取场景，提供统一接口。

```python
class ToolDiscoveryService:
    """工具发现服务 — 按需获取工具的协调器"""
    
    def __init__(self, digital_life=None, extension_manager=None, market=None):
        self._dl = digital_life
        self._ext_mgr = extension_manager
        self._market = market  # ExtensionMarket 实例
        self._gen_engine = ToolGenEngine()  # 代码生成引擎
    
    # ── A: 按需安装 ──
    def detect_and_acquire(self, user_input: str, tool_name: str = None,
                           need_description: str = None) -> dict:
        """检测缺口并获取工具
        
        1. 根据用户输入或缺失的工具名/描述搜索扩展市场
        2. 找到匹配则自动安装
        3. 安装成功后注册到全局工具表
        4. 返回获取结果供调用方重试原请求
        """
    
    # ── C: MCP 发现 ──
    def scan_mcp_services(self, network_range: str = None) -> list[dict]:
        """扫描局域网发现 MCP 服务并注册工具"""
    
    # ── D: 工具自生成 ──
    def generate_tool(self, name: str, description: str,
                      code: str, schema: dict) -> bool:
        """注册 LLM 生成的工具代码"""
    
    # ── 钩子 ──
    def on_tool_not_found(self, tool_name: str, params: dict) -> dict:
        """工具未找到时的回调 — 触发 A → D 链"""
        # 1. 先搜市场 (A)
        # 2. 搜不到则尝试 LLM 自生成 (D)
        # 3. 都不行则返回原始错误
```

### `agent/tools/tool_generator.py` — 工具生成引擎（新增）

```python
class ToolGenEngine:
    """工具代码生成引擎 — D 能力的核心"""
    
    def generate_from_llm(self, requirement: str) -> dict | None:
        """根据 LLM 提供的需求描述，生成工具代码并注册
        
        1. LLM 描述需要的函数签名、参数、行为
        2. 生成 Python 实现代码
        3. 编译验证语法
        4. 提取 schema 并注册
        Returns: {"name": str, "code": str} 或 None
        """
    
    def generate_simple(self, name: str, description: str,
                        code: str, schema: dict) -> bool:
        """注册一个简单的内联工具（不落盘）
        
        用于临时工具，不会持久化到文件系统。
        """
    
    def generate_persistent(self, name: str, description: str,
                            code: str, schema: dict,
                            category: str = "custom") -> bool:
        """注册一个持久化工具（保存到 tools/custom/ 目录）
        
        生成完整的 .py 文件（含 register_all 函数），
        保存到 agent/tools/custom/，下次启动自动加载。
        """
```

### 插件自动注册钩子（B）

在 `agent/extensions/plugins_installer.py` 中改动：

```python
# 在 PluginInstaller.load_plugin() 末尾新增
def load_plugin(self, plugin_id: str) -> Tuple[bool, str]:
    # ... 现有代码 ...
    if plugin_instance and hasattr(plugin_instance, "get_tools"):
        tools_list = plugin_instance.get_tools()
        if tools_list and self._tool_registry_callback:
            for tool_def in tools_list:
                self._tool_registry_callback(
                    name=tool_def["name"],
                    description=tool_def.get("description", ""),
                    handler=tool_def["handler"],
                    schema=tool_def.get("schema"),
                    source="plugin",
                    source_id=plugin_id,
                )
```

插件卸载时对应清理：

```python
def unload_plugin(self, plugin_id: str):
    # ... 现有代码 ...
    if self._tool_unregister_callback:
        self._tool_unregister_callback(source="plugin", source_id=plugin_id)
```

`ExtensionManager` 增加 `connect_tool_registry(register_fn, unregister_fn)` 方法建立桥接。

### 扩展工具（A 能力）

在 `agent/tools/ext_tools.py` 新增两个工具供 LLM 调用：

```python
@_tools.register("market_search", "搜索扩展市场寻找可用工具", schema={
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "搜索关键词，如'发送邮件'、'日期计算'"},
        "category": {"type": "string", "description": "过滤类别: tool/skill/mcp/plugin"},
    },
    "required": ["query"],
})
def _market_search(**kw):
    """搜索扩展市场，返回匹配的工具/扩展列表"""
    return discovery.search_market(kw["query"], kw.get("category"))

@_tools.register("install_tool", "从扩展市场安装工具", schema={
    "type": "object",
    "properties": {
        "tool_id": {"type": "string", "description": "工具ID"},
        "source": {"type": "string", "description": "安装来源"},
    },
    "required": ["tool_id"],
})
def _install_tool(**kw):
    """安装工具并自动注册到工具表"""
    ok, msg = discovery.install_and_register(kw["tool_id"], kw.get("source"))
    return {"ok": ok, "message": msg}
```

## 数据流

### A) 按需安装流程

```
用户: "给我发一封邮件"
              │
              ▼
LLM 检查工具列表 → 没有 send_email 工具
              │
         ┌─────┴─────┐
         │  检测引擎   │ ← LLM 自主判断 + 规则兜底
         └─────┬─────┘
               │ market_search("发送邮件")
               ▼
         ┌──────────┐
         │ 扩展市场   │ → 找到 yunshu-email-plugin
         └─────┬────┘
               │ install + register_dynamic("send_email", ...)
               ▼
         ┌──────────┐
         │ 重试请求   │ → LLM 调用 send_email → 成功
         └──────────┘
```

### B) 插件自动注册流程

```
用户: "安装 yunshu-email-plugin"
              │
              ▼
        PluginInstaller.install_plugin("github:user/email-plugin")
              │
              ▼
        PluginInstaller.load_plugin("yunshu-email-plugin")
              │
              ▼  新增钩子
        plugin.get_tools() → [{"name": "send_email", ...}]
              │
              ▼
        tools.register_dynamic("send_email", source="plugin", source_id="yunshu-email-plugin")
              │
              ▼
        _registry_version++ → 缓存失效 → 下一轮 LLM 调用自动可见
```

### C) MCP 动态发现流程

```
ToolDiscoveryService.scan_mcp_services()
              │
              ▼
        扫描局域网/配置的端点
              │
              ▼
        连接 MCP 服务 → 获取 tool_defs
              │
              ▼
        tools.register_dynamic("mcp_tool_name", source="mcp", source_id="service_id")
              │
              ▼
        _registry_version++ → 工具立即可用
```

### D) 工具自生成流程

```
发现: "帮我算一下斐波那契数列"
     │
     ▼
LLM 分析: 没有 fibonacci 工具，尝试自生成
     │
     ▼
LLM 生成 Python 代码 + schema
     │
     ▼
ToolGenEngine.generate_from_llm(code)
     ├── compile() 验证语法
     ├── 构建 handler 包装函数
     └── register_dynamic("fibonacci", source="generated")
     │
     ▼
_register_version++ → 下一次 LLM 调用立即可用
```

## 阶段划分

### Phase 1 — B + Registry 进化（基础）

- [ ] `agent/tools/__init__.py` 增加 `register_dynamic()`、`list_tools_by_source()`、`unregister_by_source()`
- [ ] `_registry` 条目增加 `source`、`source_id`、`dynamic`、`registered_at` 字段
- [ ] `PluginInstaller.load_plugin()` 末尾增加工具注册钩子
- [ ] `PluginInstaller.unload_plugin()` 末尾增加工具注销钩子
- [ ] `ExtensionManager` 增加 `connect_tool_registry()` 桥接方法
- [ ] 单元测试：自动注册/注销生命周期

### Phase 2 — A + D（按需安装和自生成）

- [ ] 新建 `agent/tools/discovery_service.py`（ToolDiscoveryService）
- [ ] 新建 `agent/tools/tool_generator.py`（ToolGenEngine）
- [ ] 注册 `market_search` 和 `install_tool` 两个新工具
- [ ] `detect_and_acquire()` 流程：检测→搜索→安装→注册→重试
- [ ] ToolGenEngine：简单函数注册（不落盘）+ 持久化保存
- [ ] LLM 触发检测：在 tool_router.py 增加"找不到工具时回调发现服务"
- [ ] 规则触发：在 `call()` 的 `ToolError("未知工具")` 处切入发现流程
- [ ] 智能分级安装策略：轻量自动/重量确认

### Phase 3 — C（MCP 动态发现）

- [ ] MCP 服务发现（mDNS/配置范围扫描）
- [ ] 连接 MCP 服务，提取 tool_defs
- [ ] 注册到全局工具表
- [ ] 断连时自动清理

## 不做的（本次范围外）

- 不统一两条工具调用路径（`_call_llm()` 和 `ToolCallingService`）
- 不改 `app_server.py` 的 Flask 路由体系
- 不改 `core/registry.py` 的规划系统注册表

## 风险和缓解

| 风险 | 缓解 |
|------|------|
| 动态工具与内置工具同名冲突 | `register_dynamic()` 检测重名并编号/加后缀 |
| 插件卸载时工具未清理 | `unregister_by_source()` 批量清理 |
| 自生成的工具代码有安全隐患 | 生成后调用 `compile()` 语法检查；限制 import 白名单 |
| MCP 服务不稳定导致工具调用超时 | 增加工具级超时；MCP 源标记可降级 |
