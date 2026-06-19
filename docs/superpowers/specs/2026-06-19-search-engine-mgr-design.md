# 搜索引擎实例管理 — 动态添加/删除/配置

## 概述

用户可通过 UI 动态添加、删除、配置搜索引擎，无需改代码。支持两类引擎：

- **内置引擎**：tavily、firecrawl、bing、google、brave、duckduckgo、baidu、sogou、so360（已有 handler 不变）
- **自定义引擎**：用户通过模板字段配置任意 HTTP JSON API 的搜索引擎

## 数据模型

```python
_DEFAULT_SEARCH_INSTANCE = {
    "id": str(uuid.uuid4()),
    "name": "",                    # 显示名称
    "engine_type": "custom",       # "custom" 或内置引擎名 (tavily/firecrawl/...)
    # ---- 请求（仅 custom 使用） ----
    "api_endpoint": "",            # API URL，{query} 替换为搜索词
    "http_method": "GET",          # GET / POST
    "query_param": "q",            # GET 查询参数名
    # ---- 认证（仅 custom 使用） ----
    "api_key": "",                 # 加密存储
    "auth_header": "Authorization: Bearer {key}",  # 模板，{key} 替换为 api_key
    # ---- 结果解析（仅 custom 使用） ----
    "results_path": "data",        # JSON 键路径到结果数组，如 "data.items"
    "title_field": "title",        # 结果项中的标题字段名
    "url_field": "url",            # 结果项中的 URL 字段名
    "snippet_field": "snippet",    # 结果项中的摘要字段名
    # ---- 通用 ----
    "enabled": True,
    "is_default": False,           # 是否默认搜索引擎
    "timeout": 30,
    "created_at": "",
    "updated_at": "",
}
```

存储在 `network_config.json` 的 `search_instances` 数组中，API Key 通过 `search_{instance_id}_api_key` 加密存入 `.secure_config.json`。

## 后端架构

### CRUD API

| 方法 | 路由 | 说明 |
|------|------|------|
| `GET` | `/api/search/instances` | 列出所有实例（API Key 脱敏） |
| `POST` | `/api/search/instances` | 新增实例，加密保存 Key |
| `PUT` | `/api/search/instances/<id>` | 编辑实例 |
| `DELETE` | `/api/search/instances/<id>` | 删除实例，移除注册 |
| `POST` | `/api/search/instances/<id>/default` | 设为默认引擎 |
| `POST` | `/api/search/instances/<id>/test` | 发起测试搜索，返回结果 |

### 自定义引擎通用 Handler

在 `SearchEngine` 中新增 `_search_custom(instance, query, num_results)` 方法：

1. 替换 URL：`api_endpoint.replace("{query}", quote_plus(query))`
2. 构建认证头：`auth_header.replace("{key}", api_key)` → 解析为 `{header_name}: {header_value}`
3. 发起 HTTP 请求（GET/POST）
4. 解析 JSON 响应，沿 `results_path` 取结果数组
5. 对每个结果项，用 `title_field` / `url_field` / `snippet_field` 提取字段
6. 返回统一格式 `{"ok": True, "results": [...], "engine": "实例名"}`

### 注册时机

- 服务启动时 `apply_to_app()`：遍历 `search_instances` 中所有启用的实例，调用 `register_engine(实例id, _search_custom, needs_key=True)`
- 添加/编辑/删除实例后：重新注册

### 兼容性

- 现有 `search_api_keys`、`engine_priority`、`engine_enabled` 继续保留
- 内置引擎的 `_register_builtin_engines()` 不变
- 自定义引擎的注册名称用实例 ID，避免名称冲突

## 前端 UI

### 列表视图

在网络配置页新增"搜索引擎管理"区块，位于 LLM 实例管理下方：

- 每行显示：名称、引擎类型、端点/状态
- 操作：测试、编辑、删除、设为默认
- 默认引擎显示"默认"标签

### 添加/编辑模态框

**基本设置页签：**
- 名称（必填）
- 引擎类型（下拉：custom / tavily / firecrawl / bing / google / brave / duckduckgo / baidu / sogou / so360）
- API 端点 URL（仅 custom 显示，必填）
- API Key（加密存储，仅 custom 及需 Key 的内置引擎显示）
- 认证头模板（仅 custom，默认 `Authorization: Bearer {key}`）
- HTTP 方法（仅 custom，GET/POST）
- 超时

**API 解析设置页签**（仅 custom 显示）：
- 查询参数名（默认 `q`）
- 结果 JSON 路径（默认 `data`）
- 标题字段（默认 `title`）
- URL 字段（默认 `url`）
- 摘要字段（默认 `snippet`）

### 测试功能

点击"测试"按钮，调用 `POST /api/search/instances/<id>/test` API，后端用该实例配置搜索一次测试关键词（如"test"），返回前 2 条结果预览。

## 实施步骤

1. `agent/network_config.py`：添加 `search_instances` 默认值 + 加密存储 + 加载逻辑
2. `agent/network_config.py`：添加 `apply_search_instances()` 注册/注销方法
3. `agent/server_routes/routes_config.py`：添加 CRUD + 测试 API 路由
4. `agent/web/search.py`：添加 `_search_custom()` 通用 handler
5. `templates/index.html`：添加搜索实例模态框 + 列表
6. `static/js/network-config.js`：添加 CRUD 前端逻辑 + 测试
