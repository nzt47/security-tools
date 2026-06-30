# 网络配置管理系统使用指南

## 概述

网络配置管理系统用于集中管理云枢数字生命体所需的所有联网相关配置，包括 LLM 服务、MCP 服务、搜索引擎、网络基础设置等。

## 配置项说明

### 1. LLM 服务配置

#### 1.1 单实例配置（兼容旧版）

| 配置项 | 名称 | 数据类型 | 取值范围 | 默认值 | 说明 |
|--------|------|----------|----------|--------|------|
| `llm.enabled` | 启用状态 | boolean | true/false | true | 是否启用 LLM 服务 |
| `llm.provider` | 提供商 | string | openai/deepseek/anthropic | - | LLM 服务提供商 |
| `llm.api_key` | API Key | string | - | - | 访问密钥（加密存储） |
| `llm.model` | 模型名称 | string | - | - | 使用的模型名称 |
| `llm.api_endpoint` | API 端点 | string | URL 格式 | - | 自定义 API 端点 |
| `llm.timeout` | 请求超时 | integer | 1-300 | 30 | 请求超时时间（秒） |
| `llm.max_retries` | 最大重试 | integer | 0-10 | 3 | 失败重试次数 |

#### 1.2 LLM 多实例配置

每个 LLM 实例包含以下配置项：

| 配置项 | 名称 | 数据类型 | 取值范围 | 默认值 | 说明 |
|--------|------|----------|----------|--------|------|
| `id` | 实例ID | string | UUID | 自动生成 | 唯一标识 |
| `name` | 服务名称 | string | - | - | 必填，唯一标识 |
| `provider` | 提供商 | string | openai/deepseek/anthropic | - | LLM 服务提供商 |
| `api_key` | API Key | string | - | - | 访问密钥（加密存储） |
| `model` | 模型名称 | string | - | - | 使用的模型名称 |
| `api_endpoint` | API 端点 | string | URL 格式 | - | 自定义 API 端点 |
| `auth_method` | 认证方式 | string | api_key/token/oauth | api_key | 认证方式 |
| `max_concurrent_requests` | 最大并发 | integer | ≥1 | 5 | 最大并发请求数 |
| `timeout` | 请求超时 | integer | 1-300 | 30 | 请求超时时间（秒） |
| `max_retries` | 最大重试 | integer | 0-10 | 3 | 失败重试次数 |
| `description` | 描述 | string | - | - | 可选描述信息 |
| `enabled` | 启用状态 | boolean | true/false | true | 是否启用 |
| `is_default` | 默认实例 | boolean | true/false | false | 是否设为默认 |

### 2. MCP 服务配置

#### 2.1 全局配置

| 配置项 | 名称 | 数据类型 | 取值范围 | 默认值 | 说明 |
|--------|------|----------|----------|--------|------|
| `mcp.enabled` | 启用状态 | boolean | true/false | false | 是否启用 MCP 服务 |

#### 2.2 MCP 服务实例配置

每个 MCP 服务包含以下配置项：

| 配置项 | 名称 | 数据类型 | 取值范围 | 默认值 | 说明 |
|--------|------|----------|----------|--------|------|
| `id` | 服务ID | string | UUID | 自动生成 | 唯一标识 |
| `name` | 服务名称 | string | - | - | 必填，唯一标识 |
| `address` | 服务地址 | string | IP/域名 | - | 必填 |
| `port` | 通信端口 | integer | 1-65535 | 8080 | 必填 |
| `protocol` | 协议类型 | string | http/https | http | 通信协议 |
| `timeout` | 超时时间 | integer | 1-300 | 30 | 超时时间（秒） |
| `retry_strategy` | 重试策略 | string | fixed/exponential/none | fixed | 重试策略 |
| `max_retries` | 重试次数 | integer | 0-10 | 3 | 最大重试次数 |
| `security_methods` | 安全认证 | array | tls/token/certificate | [] | 安全认证方式 |
| `certificate_path` | 证书路径 | string | - | - | 证书文件路径 |
| `description` | 描述 | string | - | - | 可选描述信息 |
| `enabled` | 启用状态 | boolean | true/false | true | 是否启用 |

### 3. 网络基础设置

| 配置项 | 名称 | 数据类型 | 取值范围 | 默认值 | 说明 |
|--------|------|----------|----------|--------|------|
| `network.timeout` | 全局超时 | integer | 1-300 | 30 | 网络请求超时（秒） |
| `network.max_retries` | 最大重试 | integer | 0-10 | 3 | 失败重试次数 |
| `network.backoff_factor` | 重试间隔因子 | float | 0.1-5 | 0.5 | 指数退避因子 |
| `network.proxy_enabled` | 启用代理 | boolean | true/false | false | 是否使用代理 |
| `network.proxy_url` | 代理 URL | string | URL 格式 | - | 代理服务器地址 |

### 4. 搜索服务配置

| 配置项 | 名称 | 数据类型 | 取值范围 | 默认值 | 说明 |
|--------|------|----------|----------|--------|------|
| `search.enabled` | 启用状态 | boolean | true/false | true | 是否启用搜索 |
| `search.default_engine` | 默认引擎 | string | search_instances 中的实例 ID | - | 默认搜索引擎实例 ID |
| `search.max_results` | 最大结果 | integer | 1-50 | 10 | 搜索结果数量 |
| `search.timeout` | 超时时间 | integer | 1-120 | 30 | 搜索超时（秒） |
| `search.engine_priority` | 优先级 | array | 实例 ID 列表 | [] | 搜索引擎实例的优先级（UUID） |
| `search.engine_enabled` | 启用状态 | object | - | 全部启用 | 各引擎启用状态（旧版兼容） |

> **注意**：`engine_priority` 和 `default_engine` 使用 `search_instances` 中的实例 ID（UUID），而非引擎名称。`apply_search_instances()` 会在应用时自动将名称规范化为 UUID。

### 5. 搜索实例配置（search_instances）

搜索实例是新版搜索引擎管理方式，每个实例独立配置引擎类型、API 端点和认证信息。

#### 5.1 实例配置项

| 配置项 | 名称 | 数据类型 | 默认值 | 说明 |
|--------|------|----------|--------|------|
| `id` | 实例 ID | string (UUID) | 自动生成 | 唯一标识 |
| `name` | 实例名称 | string | - | 显示名称 |
| `engine_type` | 引擎类型 | string | custom | duckduckgo/sogou/so360/tavily/bing/google/brave/custom |
| `api_endpoint` | API 端点 | string | - | 自定义搜索 API 地址（custom 类型必填） |
| `http_method` | HTTP 方法 | string | GET | GET/POST |
| `query_param` | 查询参数名 | string | q | 搜索关键词的 URL 参数名 |
| `api_key` | API Key | string | - | 加密存储，不出现在配置文件中 |
| `auth_header` | 认证头模板 | string | Authorization: Bearer {key} | `{key}` 占位符会被实际 key 替换 |
| `results_path` | 结果路径 | string | data | JSON 响应中结果数组的路径 |
| `title_field` | 标题字段 | string | title | 结果项中标题的字段名 |
| `url_field` | URL 字段 | string | url | 结果项中链接的字段名 |
| `snippet_field` | 摘要字段 | string | snippet | 结果项中摘要的字段名 |
| `enabled` | 启用状态 | boolean | true | 是否启用该实例 |
| `is_default` | 默认实例 | boolean | false | 是否为默认搜索引擎 |
| `timeout` | 超时时间 | integer | 30 | 请求超时（秒） |
| `created_at` | 创建时间 | string | 自动生成 | ISO 格式时间戳 |
| `updated_at` | 更新时间 | string | 自动生成 | ISO 格式时间戳 |

#### 5.2 API Key 安全机制

- **加密存储**：`api_key` 通过 `SecureConfigManager` 使用 AES-GCM 加密存储在 `.secure_config.json` 中
- **自动剥离**：`_save()` 保存配置时自动移除 `search_instances` 中的 `api_key` 字段，明文不会写入 `network_config.json`
- **脱敏返回**：`get_all()` 返回的 `api_key` 显示为 `***` + 末尾 4 位（如 `***a1b2`）
- **解密读取**：`get_raw_config()` 返回解密后的真实 `api_key`，仅供内部使用（如构建 HTTP 请求头）
- **缓存防污染**：`get_raw_config()` 和 `get_all()` 均返回深拷贝，防止解密后的 `api_key` 残留在内存缓存中

### 6. Web 抓取服务

| 配置项 | 名称 | 数据类型 | 取值范围 | 默认值 | 说明 |
|--------|------|----------|----------|--------|------|
| `web_scraping.enabled` | 启用状态 | boolean | true/false | true | 是否启用抓取 |
| `web_scraping.respect_robots_txt` | 遵守 robots.txt | boolean | true/false | true | 是否遵守规则 |
| `web_scraping.delay_between_requests` | 请求间隔 | float | 0-10 | 1.0 | 请求间隔（秒） |

### 7. 浏览器自动化

| 配置项 | 名称 | 数据类型 | 取值范围 | 默认值 | 说明 |
|--------|------|----------|----------|--------|------|
| `browser.enabled` | 启用状态 | boolean | true/false | false | 是否启用 |
| `browser.headless` | 无头模式 | boolean | true/false | true | 无界面运行 |
| `browser.timeout` | 超时时间 | integer | 1-120 | 30 | 超时（秒） |

### 8. 数据同步

| 配置项 | 名称 | 数据类型 | 取值范围 | 默认值 | 说明 |
|--------|------|----------|----------|--------|------|
| `sync.enabled` | 启用状态 | boolean | true/false | true | 是否启用同步 |
| `sync.interval_minutes` | 同步间隔 | integer | 5-1440 | 60 | 同步频率（分钟） |
| `sync.auto_sync_on_start` | 启动同步 | boolean | true/false | true | 启动时自动同步 |

### 9. 外部服务

#### 9.1 错误报告

| 配置项 | 名称 | 数据类型 | 说明 |
|--------|------|----------|------|
| `external_services.error_reporting.enabled` | 启用状态 | boolean | 是否启用 |
| `external_services.error_reporting.webhook_url` | Webhook URL | string | 加密存储 |

#### 9.2 监控端点

| 配置项 | 名称 | 数据类型 | 说明 |
|--------|------|----------|------|
| `external_services.monitoring.enabled` | 启用状态 | boolean | 是否启用 |
| `external_services.monitoring.endpoint` | 端点 URL | string | 监控服务地址 |

## 操作指南

### 1. 添加 LLM 实例

1. 进入「网络配置」页面
2. 找到「LLM 多实例管理」区域
3. 点击「+ 添加实例」按钮
4. 填写表单：
   - **服务名称**（必填）：输入唯一的实例名称
   - **提供商**：选择 OpenAI/DeepSeek/Anthropic
   - **API Key**：输入服务商提供的密钥
   - **模型名称**：输入模型标识
   - **API 端点**：留空使用默认端点
   - **认证方式**：选择 API Key/Token/OAuth
   - **最大并发请求数**：设置并发限制
   - **超时时间**：设置请求超时
   - **最大重试次数**：设置失败重试
   - **描述信息**：可选说明
5. 点击「保存」按钮

### 2. 编辑 LLM 实例

1. 在「LLM 多实例管理」区域找到目标实例
2. 点击实例卡片上的「✏️」编辑按钮
3. 修改需要变更的配置项
4. 点击「保存」按钮

### 3. 删除 LLM 实例

1. 在「LLM 多实例管理」区域找到目标实例
2. 点击实例卡片上的「🗑」删除按钮
3. 确认删除操作

### 4. 设置默认 LLM 实例

1. 在「LLM 多实例管理」区域找到目标实例
2. 点击「设为默认」按钮
3. 该实例将被标记为默认实例

### 5. 启用/禁用 LLM 实例

1. 在「LLM 多实例管理」区域找到目标实例
2. 点击实例卡片上的开关按钮
3. 切换启用/禁用状态

### 6. 添加 MCP 服务

1. 进入「网络配置」页面
2. 找到「MCP 服务配置」区域
3. 点击「+ 添加服务」按钮
4. 填写表单：
   - **服务名称**（必填）：输入唯一的服务名称
   - **MCP 服务地址**（必填）：输入 IP 地址或域名
   - **通信端口**（必填）：输入端口号（1-65535）
   - **协议类型**：选择 HTTP/HTTPS
   - **超时时间**：设置超时（秒）
   - **重试策略**：选择固定间隔/指数退避/无重试
   - **最大重试次数**：设置重试次数
   - **安全认证方式**：选择 TLS/Token/证书
   - **证书路径**：填写证书文件路径
   - **描述信息**：可选说明
5. 点击「保存」按钮

### 7. 导出配置

1. 进入「网络配置」页面
2. 点击底部的「📤 导出」按钮
3. 浏览器将自动下载配置文件（脱敏）

### 8. 导入配置

1. 进入「网络配置」页面
2. 点击底部的「📥 导入」按钮
3. 选择 JSON 格式的配置文件
4. 配置将被导入并覆盖现有配置

### 9. 保存并即时生效

1. 修改配置后点击「⚡ 应用并即时生效」按钮
2. 系统将：
   - 保存配置到文件
   - 立即应用配置到运行中的实例
   - 配置 LLM 连接（如果提供了新的 API Key）

## 配置示例

### LLM 实例配置示例

```json
{
  "llm_instances": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "name": "OpenAI Primary",
      "provider": "openai",
      "model": "gpt-4",
      "api_endpoint": "",
      "auth_method": "api_key",
      "max_concurrent_requests": 10,
      "timeout": 60,
      "max_retries": 3,
      "description": "主用 OpenAI 实例",
      "enabled": true,
      "is_default": true
    },
    {
      "id": "550e8400-e29b-41d4-a716-446655440001",
      "name": "DeepSeek Backup",
      "provider": "deepseek",
      "model": "deepseek-chat",
      "api_endpoint": "",
      "auth_method": "api_key",
      "max_concurrent_requests": 5,
      "timeout": 30,
      "max_retries": 3,
      "description": "备用 DeepSeek 实例",
      "enabled": true,
      "is_default": false
    }
  ]
}
```

### MCP 服务配置示例

```json
{
  "mcp": {
    "enabled": true,
    "services": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440002",
        "name": "Local MCP",
        "address": "localhost",
        "port": 8080,
        "protocol": "http",
        "timeout": 30,
        "retry_strategy": "fixed",
        "max_retries": 3,
        "security_methods": ["token"],
        "certificate_path": "",
        "description": "本地 MCP 服务",
        "enabled": true
      }
    ]
  }
}
```

## 安全注意事项

1. **API Key 加密存储**：所有敏感信息（API Key、Token、Webhook URL）均通过 SecureConfigManager 加密存储
2. **配置导出脱敏**：导出配置时，敏感信息会被脱敏处理（显示为 `***` + 末尾4位）
3. **API 令牌保护**：设置 `FLASK_API_TOKEN` 环境变量后，所有修改操作需携带令牌
4. **输入验证**：所有配置项均有严格的输入验证，防止非法输入

## 配置变更日志

系统自动记录所有配置变更，包括：
- 修改时间
- 操作类型（添加/更新/删除/重置/导入）
- 修改模块
- 修改详情

可通过 API `/api/config/logs` 查看最近的配置变更记录。

## API 端点列表

### LLM 实例管理

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/llm/instances` | 获取所有实例 |
| GET | `/api/llm/instances/{id}` | 获取单个实例 |
| POST | `/api/llm/instances` | 添加实例 |
| PUT | `/api/llm/instances/{id}` | 更新实例 |
| DELETE | `/api/llm/instances/{id}` | 删除实例 |
| POST | `/api/llm/instances/{id}/default` | 设置默认实例 |

### MCP 服务管理

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/mcp/services` | 获取所有服务 |
| GET | `/api/mcp/services/{id}` | 获取单个服务 |
| POST | `/api/mcp/services` | 添加服务 |
| PUT | `/api/mcp/services/{id}` | 更新服务 |
| DELETE | `/api/mcp/services/{id}` | 删除服务 |
| POST | `/api/mcp/enable` | 启用/禁用 MCP |

### 搜索实例管理

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/search/instances` | 获取所有搜索实例 |
| POST | `/api/search/instances` | 新增搜索实例 |
| PUT | `/api/search/instances/{id}` | 更新搜索实例 |
| DELETE | `/api/search/instances/{id}` | 删除搜索实例（自动清理 engine_priority 残留 id） |
| POST | `/api/search/instances/{id}/default` | 设置默认搜索引擎（同步 default_engine 字段） |

> **注意**：删除实例时后端会自动从 `engine_priority` 中移除该实例 ID，并在该实例为默认引擎时清空 `default_engine`，防止残留无效引用。

### 配置管理

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/network-config` | 获取配置 |
| POST | `/api/network-config` | 更新配置 |
| POST | `/api/network-config/reset` | 重置配置 |
| GET | `/api/network-config/export` | 导出配置 |
| POST | `/api/network-config/import` | 导入配置 |
| POST | `/api/apply-network-config` | 应用配置 |
| GET | `/api/config/logs` | 获取变更日志 |