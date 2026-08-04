# NetworkConfig 敏感数据脱敏最佳实践

> **审计日期**: 2026-07-29
> **审计范围**: `agent/network_config.py` 敏感字段脱敏覆盖
> **核心结论**: 8 个核心字段已实现脱敏（原 5 个 + 本次修复 3 个）；引入统一 `_mask_value` 工具函数；剩余 P3 审计日志为后续改进项

## 目录
1. [脱敏原则](#1-脱敏原则)
2. [现有覆盖清单（已实现）](#2-现有覆盖清单已实现)
3. [本次修复内容（3 个字段）](#3-本次修复内容3-个字段)
4. [统一 _mask_value 工具函数](#4-统一-_mask_value-工具函数)
5. [字段白名单/黑名单设计（建议）](#5-字段白名单黑名单设计建议)
6. [审计日志建议](#6-审计日志建议)
7. [改进路线图](#7-改进路线图)
8. [验证清单](#8-验证清单)

---

## 1. 脱敏原则

| 原则 | 说明 | 实现位置 |
|------|------|---------|
| 单一数据源 | 敏感数据仅存 `.env` 文件，通过 `EnvConfigManager` 管理 | `_save_secure` / `_load_secure` |
| 落盘前剥离 | `_save()` 写入 JSON 前必须移除所有敏感字段 | `_save()` |
| 输出时脱敏 | `get_all()` 返回给 UI/导出时必须脱敏显示 | `get_all()` |
| 不可逆 | 脱敏后值（如 `***1234`）不能反推原值 | `_mask_value()` |
| 一致性 | 同一字段在所有出口（get_all/export_config）脱敏方式相同 | `export_config` 调用 `get_all` |
| 内外分离 | `get_all()` 脱敏供 UI；`get_raw_config()` 返回原始值供内部模块 | `get_all` / `get_raw_config` |

---

## 2. 现有覆盖清单（已实现）

### 2.1 已脱敏字段矩阵

| 字段路径 | 落盘剥离位置 | 输出脱敏位置 | 脱敏方式 | env_var 映射 |
|----------|------|------|------|------|
| `llm.api_key` | `_save` | `get_all` | `tail4`（***+后4位）| `LLM_API_KEY` |
| `external_services.error_reporting.webhook_url` | `_save` | `get_all` | `full`（***）| `ERROR_REPORTING_WEBHOOK_URL` |
| `llm_instances[*].api_key` | `_save` | `get_all` | `tail4` | `LLM_{INSTANCE_ID}_API_KEY` |
| `search_instances[*].api_key` | `_save` | `get_all` | `tail4` | `SEARCH_{INST_ID}_API_KEY` |
| `search_api_keys`（旧版兼容） | `update` 不写 JSON | 不持久化 | 仅写 `.env` | `SEARCH_{ENGINE_NAME}_API_KEY` |
| `mcp.services[*].api_key` | `_save` | `get_all` | `tail4` | `MCP_{SERVICE_ID}_API_KEY` |
| `mcp.services[*].token` | `_save` | `get_all` | `tail4` | `MCP_{SERVICE_ID}_TOKEN` |
| `network.proxy_url` | 不剥离（需完整值）| `get_all` | `url_auth`（剥离 user:pass@）| N/A |
| `external_services.monitoring.endpoint` | 不剥离（需完整值）| `get_all` | `url_auth` | N/A |

### 2.2 关键代码路径验证

**`_save()` 剥离逻辑**（network_config.py `_save` 方法）：
- `save_data['llm'].pop('api_key', None)` ✅
- `save_data['external_services']['error_reporting'].pop('webhook_url', None)` ✅
- 循环剥离 `llm_instances[*].api_key` ✅
- 循环剥离 `search_instances[*].api_key` ✅
- 循环剥离 `mcp.services[*].api_key` 和 `[*].token` ✅（本次修复）

**`export_config()` 路径**：调用 `get_all()` → 已脱敏 ✅

**`import_config()` 路径**：接收 JSON → `_save(self._cache)` → 调用 `_save` 剥离已知字段 ✅

---

## 3. 本次修复内容（3 个字段）

### 3.1 P0 修复：MCP 服务凭证脱敏（高风险）

**风险**：`_update_mcp_config` 原未调用 `_save_secure`，`_save` 未剥离，MCP 凭证明文落盘到 `network_config.json`。

**修复方案**（同 `llm.api_key` 模式）：
1. `_DEFAULT_MCP_SERVICE` 新增 `"api_key": ""` 和 `"token": ""` 字段
2. `_update_mcp_config` 对每个 service 调用 `_save_secure(f'mcp_{service_id}_api_key', api_key)` 写入 .env
3. `_save` 循环 `save_data['mcp']['services']` 移除 `api_key`/`token`
4. `get_all` / `get_raw_config` 加载 MCP 凭证（从 .env）
5. `get_all` 用 `_mask_value(value, "tail4")` 脱敏显示
6. `_key_to_env_var` 新增 `mcp_{service_id}_api_key` → `MCP_{SERVICE_ID}_API_KEY` 映射

**验证测试**：`TestMcpCredentialsMasking`（3 个用例）

### 3.2 P2 修复：proxy_url 输出脱敏（中风险）

**风险**：`network.proxy_url` 可能含 `http://user:pass@host:port` 认证信息，明文显示。

**修复方案**：
- `get_all` 中：`_mask_value(proxy_url, "url_auth")` 剥离 user:pass@，保留 host:port
- `_save` 不剥离（proxy_url 需完整值供代理连接）
- `get_raw_config` 返回原始值（供代理模块使用）

**验证测试**：`TestProxyUrlMasking`（2 个用例）

### 3.3 P2 修复：monitoring.endpoint 输出脱敏（中风险）

**风险**：`external_services.monitoring.endpoint` 可能含认证信息，明文显示。

**修复方案**：同 proxy_url，`get_all` 用 `url_auth` 脱敏，`get_raw_config` 保留原始值。

**验证测试**：`TestMonitoringEndpointMasking`（2 个用例）

---

## 4. 统一 _mask_value 工具函数

### 4.1 设计

```python
def _mask_value(value: str, strategy: str = "tail4") -> str:
    """统一脱敏工具函数

    strategy:
        - "tail4": 保留后4位（***1234），适合 API Key
        - "full": 完全掩码（***），适合 webhook_url
        - "url_auth": 剥离 URL 认证（http://user:pass@host → http://host），适合 proxy_url
    """
```

### 4.2 应用位置

| 字段 | 策略 | 调用位置 |
|------|------|---------|
| `llm.api_key` | `tail4` | `get_all` |
| `webhook_url` | `full` | `get_all` |
| `llm_instances[*].api_key` | `tail4` | `get_all` |
| `search_instances[*].api_key` | `tail4` | `get_all` |
| `mcp.services[*].api_key` | `tail4` | `get_all` |
| `mcp.services[*].token` | `tail4` | `get_all` |
| `network.proxy_url` | `url_auth` | `get_all` |
| `monitoring.endpoint` | `url_auth` | `get_all` |

### 4.3 收益

- 消除 `get_all` 中 4 处重复的 `***+后4位` 逻辑
- 新增敏感字段只需调用 `_mask_value`，无需重复实现脱敏逻辑
- 三种策略覆盖 API Key / Webhook / URL 认证场景

---

## 5. 字段白名单/黑名单设计（建议）

> **状态**: 【建议改进】，未实现

### 5.1 设计原则

| 类型 | 含义 | 用途 |
|------|------|------|
| 敏感字段黑名单 | 必须从 JSON 剥离的字段 | `_save()` 调用 |
| 脱敏字段映射 | 输出时需脱敏的字段 + 策略 | `get_all()` 调用 |

### 5.2 建议数据结构

```python
_SENSITIVE_FIELDS = {
    # 字段路径 → (剥离?, 脱敏策略, env_var 前缀)
    "llm.api_key": (True, "tail4", "llm_api_key"),
    "external_services.error_reporting.webhook_url": (True, "full", "error_reporting_webhook"),
    "llm_instances[*].api_key": (True, "tail4", "llm_{instance_id}_api_key"),
    "search_instances[*].api_key": (True, "tail4", "search_{inst_id}_api_key"),
    "mcp.services[*].api_key": (True, "tail4", "mcp_{service_id}_api_key"),
    "mcp.services[*].token": (True, "tail4", "mcp_{service_id}_token"),
    "network.proxy_url": (False, "url_auth", None),  # 不剥离但脱敏显示
    "external_services.monitoring.endpoint": (False, "url_auth", None),
}
```

### 5.3 收益

- 新增敏感字段只需修改一处配置（`_SENSITIVE_FIELDS`），不再修改 `_save` 和 `get_all` 两处
- 审计时只需审查一张表

---

## 6. 审计日志建议

> **状态**: 【建议改进】，未实现

### 6.1 现状

`_add_change_log` 仅记录 `action/section/details`，不记录敏感字段访问。

### 6.2 建议设计

```python
def _log_sensitive_access(self, field: str, action: str, masked: bool = True):
    """记录敏感字段访问（用于安全审计）

    Args:
        field: 字段路径（如 "llm.api_key"）
        action: "read" / "write" / "export"
        masked: 是否脱敏后访问
    """
    logger.info(log_dict({
        'module_name': 'network_config',
        'action': f'sensitive.{action}',
        'field': field,
        'masked': masked,
    }))
```

### 6.3 调用点

- `get_all()` 调用 `_load_secure` 时记录 `read` + `masked=True`
- `get_raw_config()` 调用 `_load_secure` 时记录 `read` + `masked=False`（敏感操作）
- `_save_secure` 调用时记录 `write`
- `export_config` 调用时记录 `export`

---

## 7. 改进路线图

| 优先级 | 改进项 | 状态 | 影响文件 |
|------|------|------|------|
| P0 | MCP 服务凭证脱敏 + `_save_secure` 集成 | ✅ 已完成 | network_config.py |
| P2 | proxy_url 输出脱敏（url_auth 策略）| ✅ 已完成 | network_config.py |
| P2 | monitoring.endpoint 输出脱敏 | ✅ 已完成 | network_config.py |
| P1 | 引入 `_mask_value` 统一工具函数 | ✅ 已完成 | network_config.py |
| P1 | 引入 `_SENSITIVE_FIELDS` 配置表驱动 | 【建议】未实现 | network_config.py |
| P3 | 敏感字段访问审计日志 | 【建议】未实现 | network_config.py |

---

## 8. 验证清单

- [x] `network_config.json` 文件中不含任何 `api_key`/`token`/`webhook_url` 明文
- [x] `get_all()` 返回的所有敏感字段已脱敏
- [x] `export_config()` 输出已脱敏（调用 `get_all`）
- [x] `import_config()` 后 `_save` 不会把导入的敏感字段明文写入 JSON
- [x] MCP 服务凭证通过 `.env` 单一数据源管理
- [x] `_mask_value` 单元测试覆盖 tail4/full/url_auth 三种策略
- [x] `get_raw_config()` 返回原始值供内部模块使用代理/监控
- [x] 75 个 NetworkConfig 测试全通过（68 原有 + 7 新增）

### 测试命令

```bash
# 运行全部 NetworkConfig 测试
SKILLS_OFFLINE=1 python -m pytest tests/unit/test_network_config.py tests/unit/test_network_config_coverage.py -v

# 仅运行本次新增的脱敏回归测试
SKILLS_OFFLINE=1 python -m pytest tests/unit/test_network_config_coverage.py::TestMcpCredentialsMasking tests/unit/test_network_config_coverage.py::TestProxyUrlMasking tests/unit/test_network_config_coverage.py::TestMonitoringEndpointMasking -v
```
