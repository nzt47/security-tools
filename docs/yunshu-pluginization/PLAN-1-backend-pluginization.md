# PLAN-1：后端插件化（阶段 1）

> 配套总览：`docs/yunshu-pluginization/README.md`
> 目标文件：`app_server.py`（213KB 单体）→ `plugins/` 目录 + 装配器。
> 约束：**路由路径、请求/响应格式、行为必须 100% 不变**；每完成一个域就跑回归。

---

## 1. 设计总览

```
app_server.py                     plugins/
┌──────────────────────┐          ┌─────────────────────────────┐
│ app = Flask(...)     │ ──────►  │ plugin_api.py   Plugin 协议  │
│ PluginRegistry.load()│ 注册      │ services.py    共享服务桥(可选) │
│ for p in PLUGINS:    │          │ chat.py         PLUGIN       │
│   app.register_...   │          │ memory.py       PLUGIN       │
│ /api/plugins         │          │ status.py       PLUGIN       │
│ 页面/静态/测试 路由保留 │          │ ...                         │
└──────────────────────┘          └─────────────────────────────┘
```

**两个核心角色：**

- **插件（Plugin）**：一个 Python 模块，定义 `PLUGIN = Plugin(...)`，内含一个 Flask `Blueprint`，承载一组内聚的路由。
- **装配器（Assembler）**：`app_server.py` 创建 `app` 后，加载全部插件、注册 blueprint、挂载 `/api/plugins` 元信息端点。

---

## 2. 插件协议（`plugins/plugin_api.py`）

```python
# plugins/plugin_api.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from flask import Blueprint

@dataclass
class Plugin:
    name: str                       # 插件唯一名，如 "chat"
    version: str                    # 语义化版本，如 "1.0.0"
    description: str = ""           # 一句话说明（自解释 UI 用）
    schema: Dict[str, Any] = field(default_factory=dict)  # JSON Schema（阶段 3 启用）
    blueprint: Optional[Blueprint] = None
    routes: List[str] = field(default_factory=list)  # 该插件暴露的路径清单

_REGISTRY: List[Plugin] = []

def register_plugin(plugin: Plugin) -> Plugin:
    """注册一个插件（幂等：同名直接返回已注册实例）。"""
    if not any(p.name == plugin.name for p in _REGISTRY):
        _REGISTRY.append(plugin)
    return plugin

def get_plugins() -> List[Plugin]:
    return list(_REGISTRY)

def manifest() -> Dict[str, Any]:
    """构建 /api/plugins 响应体。"""
    return {
        "plugins": [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "schema": p.schema,
                "routes": sorted(p.routes),
            }
            for p in _REGISTRY
        ],
        "host": {"python": __import__("sys").version.split()[0], "flask": __import__("flask").__version__},
    }
```

**插件模块模板（`plugins/<domain>.py`）：**

```python
# plugins/chat.py
from flask import Blueprint, request, jsonify
from .plugin_api import Plugin, register_plugin

bp = Blueprint("chat", __name__)   # 注意：不设 url_prefix，路由保持原路径

@bp.route("/api/chat", methods=["POST"])
def api_chat():
    # 共享依赖：函数内延迟 import，避免循环导入（见 §4）
    from app_server import _save_conversation_record, require_token
    ...

PLUGIN = register_plugin(Plugin(
    name="chat",
    version="1.0.0",
    description="对话、会话、历史记录",
    blueprint=bp,
    routes=["/api/chat", "/api/chat/stream", "/api/sessions", ...],
))
```

**关键点：**

- Blueprint **不设 `url_prefix`**，路由路径与原来完全一致，前端零改动。
- 插件模块顶层只 import `flask` 和 `plugin_api`；**绝不顶层 import `app_server`**（循环导入）。
- 共享依赖（`require_token`、`log_request`、`_save_conversation_record`、各类单例）在**视图函数内部延迟 import**；阶段 1 允许这样做，后续可收口到 `plugins/services.py`。

---

## 3. 装配器改造（`app_server.py`）

保持 `app = Flask(...)` 创建逻辑不变，在 blueprint 注册区改为：

```python
from plugins.plugin_api import get_plugins, manifest as plugin_manifest

# 原有 2 个 blueprint 保留
app.register_blueprint(health_bp)
app.register_blueprint(learning_metrics_bp)

# 注册全部插件 blueprint
for _p in get_plugins():
    if _p.blueprint is not None:
        app.register_blueprint(_p.blueprint)

# 插件元信息端点
@app.route("/api/plugins", methods=["GET"])
def api_plugins():
    return jsonify(plugin_manifest())
```

- 尚未迁移的路由**原样保留**在 `app_server.py`（不删、不动）。
- `plugins/__init__.py` 提供显式清单，保证加载顺序确定：

```python
# plugins/__init__.py
from . import chat, memory, status, skills, admin, safety, mcp_scheduler, system_tools  # 逐步添加
__all__ = ["chat", "memory", "status", "skills", "admin", "safety", "mcp_scheduler", "system_tools"]
```

---

## 4. 共享依赖策略（重要）

**问题**：`app_server.py` 的路由函数大量依赖模块级全局（`require_token`、`log_request`、`_save_conversation_record`、`app` 上的配置、各类单例）。

**策略（阶段 1 务实版）**：

1. **优先搬「自治度高的域」**：依赖越少的路由先搬，如 `status`（health/sensors/heartbeat）、`memory`（读写单例）。
2. **共享装饰器**：`require_token` / `log_request` 在阶段 1 保留在 `app_server.py`，插件视图函数内 `from app_server import require_token`（函数内 import，调用时才执行，无循环导入问题）。
3. **不搬共享代码**：阶段 1 只搬**路由 + 其私有 helper**；公共 helper（`_save_conversation_record` 等）留在原文件。
4. **可选演进（不在阶段 1 强制）**：新建 `plugins/services.py`，用懒加载桥把高频共享依赖收口，风格与 `agent/utils/singleton_manager.py` 一致；后续任务可逐步迁入。

> ⚠️ 循环导入红线：任何插件模块的**顶层**不得出现 `import app_server` 或 `from app_server import ...`。发现即算失败。

---

## 5. 路由域拆分清单（来自 2026-08 实测的 177 条路由）

| 插件名 | 文件 | 路由域 |
|---|---|---|
| chat | `plugins/chat.py` | `/api/chat`、`/api/chat/stream`、`/api/voice/*`、`/api/news`、`/api/sessions*`、`/api/history*`、`/api/clear` |
| memory | `plugins/memory.py` | `/api/context/*`、`/api/memory*`、`/api/vector/search`、`/api/memory/windows/*` |
| status | `plugins/status.py` | `/api/health`、`/api/sensors`、`/api/status`、`/api/mode`、`/api/planning/toggle`、`/api/cognitive/status`、`/api/heartbeat*`、`/api/panorama`、`/api/personality*` |
| skills | `plugins/skills.py` | `/api/skills*`、`/api/extensions*`、`/api/tools/*` |
| admin | `plugins/admin.py` | `/api/config`、`/api/config/logs`、`/api/auth/token-check`、`/api/audit/logs`、`/api/network-config*`、`/api/apply-network-config`、`/api/system-prompt*`、`/api/llm/instances*`、`/api/search/instances*`、`/api/search-performance/*` |
| safety | `plugins/safety.py` | `/api/safety/*`、`/api/permission/*`、`/api/privacy/info`、`/api/window/consent` |
| mcp_scheduler | `plugins/mcp_scheduler.py` | `/api/mcp/*`、`/api/scheduler/*`、`/api/schedules*`、`/api/tasks*` |
| system_tools | `plugins/system_tools.py` | `/api/workspace*`、`/api/filesystem/*`、`/api/sandbox/run`、`/api/browser/*`、`/api/process/*`、`/api/clipboard`、`/api/web/*` |
| （保留在 app_server） | — | 页面路由 `/`、`/chat`、`/legacy`、`/static/*`、`/mascot-test`、`/network-test`、`/search-status`、`/network-config-debug`、`/replay-viewer`；测试路由 `/api/test/*` |

---

## 6. 回归策略

1. 每搬完一个域，**立即**跑：
   - `python -m pytest tests/ -x -q`（仓库现存 12714 项用例；若全量太慢，先跑与 app/API 相关的子集，收尾任务 T1.10 跑全量）
   - 启动服务冒烟：`/api/health`、`/api/sensors`、`/api/chat`、`/api/plugins`
2. 每个任务一个 git 提交；破坏行为时**回滚该提交**再重试。
3. 路由路径清单核对：迁移前后 `Select-String -Path app_server.py -Pattern '@app\.route|@bp\.route'` 比对路径集合一致。

---

## 7. 完成标准（阶段 1 结束）

- [ ] `plugins/` 目录存在，8 个域插件全部注册，`app_server.py` 不再包含这些路由
- [ ] `/api/plugins` 返回完整 manifest（名称/版本/描述/routes 全对）
- [ ] 全部路由路径与迁移前一致（页面/静态/测试路由除外，它们保留在原文件）
- [ ] 全量 pytest 通过；前端构建与运行不受影响
- [ ] `app_server.py` 行数显著下降（从 213KB 降到约 60KB 以内）
