# 搜索实例校验逻辑统一重构 — 技术决策文档

**日期**: 2026-07-07
**状态**: 已实施并验证
**范围**: `validate_search_instance` 校验函数的声明式重构与重复代码消除

---

## 1. 背景

搜索实例配置校验逻辑（`validate_search_instance`）在代码库中存在两份独立实现，且行为不一致。
本次重构旨在：
1. 消除重复代码，统一校验入口
2. 引入声明式校验基础设施，便于后续扩展
3. 修复因副本缺失检查项导致的校验漏洞

---

## 2. 依赖分析

### 2.1 调用点梳理

重构前，代码库中存在 **两份** `validate_search_instance` 实现：

| 位置 | 函数名 | 调用点 | 检查项 |
|---|---|---|---|
| `agent/server_routes/routes_config.py:27` | `validate_search_instance` | `routes_config.py:482`（`/api/search/instances` POST） | name / engine_type 空 / 未知引擎 / custom+endpoint / timeout 范围 |
| `app_server.py:2455` | `_validate_search_instance` | `app_server.py:2489`（`/api/search/instances` POST） | name / engine_type 空 / custom+endpoint / timeout 范围 |

**关键发现**：`app_server.py` 的副本 **缺失"未知引擎类型"检查**，导致通过该端点提交的未知引擎类型（如 `engine_type: "foo"`）不会被拒绝，是一个校验漏洞。

### 2.2 配置加载链影响

`validate_search_instance` 仅在 API 请求处理时作为 **门禁校验** 调用，不参与配置加载/反序列化流程：
- 配置加载由 `NetworkConfigManager._load()` / `_save()` 处理，不经过此函数
- 默认实例由 `_DEFAULT_SEARCH_INSTANCE` 常量定义，不走校验
- 因此重构 **不影响** 配置加载逻辑

---

## 3. 重构决策

### 3.1 方案选择：声明式规则 + 条件检查（混合方案）

**采用方案**：将简单字段（`name`、`timeout`）抽取为声明式 `ValidationRule`，由共享函数 `validate_dict_against_rules` 统一处理；复杂条件逻辑（`engine_type` 枚举/自定义、`api_endpoint` 条件必填）保留在包装函数中。

**未将所有字段纳入声明式规则的原因**：
- `engine_type` 需要区分"空值"（→ "引擎类型不能为空"）和"未知值"（→ "未知的内置引擎类型: X"）两种不同错误消息，声明式规则的单一 `error_message` 无法表达
- `api_endpoint` 仅在 `engine_type == "custom"` 时必填，是跨字段条件依赖，不适合用独立规则表达

### 3.2 共享基础设施

新建 `agent/config_validation.py`，提供：

| 组件 | 用途 |
|---|---|
| `ValidationRule` 数据类 | 声明式规则定义（path / validator / error_message / required） |
| 验证器工厂 | `_range_validator` / `_non_empty_string_validator` / `_choice_validator` / `_bool_validator` / `_url_validator` / `_path_validator` |
| `validate_dict_against_rules` | 核心校验函数，遍历规则集返回错误列表 |
| `SEARCH_INSTANCE_VALIDATION_RULES` | 搜索实例字段规则集（name + timeout） |

### 3.3 重复代码消除

将 `app_server.py:2455` 的 `_validate_search_instance` 副本替换为导入：
```python
from agent.server_routes.routes_config import validate_search_instance as _validate_search_instance
```
两个 API 端点现在共享同一份校验逻辑，**修复了 `app_server.py` 缺失"未知引擎类型"检查的漏洞**。

### 3.4 行为差异

重构前后有一处行为优化：空 `engine_type` 的错误输出。

| 场景 | 重构前（routes_config） | 重构前（app_server） | 重构后（统一） |
|---|---|---|---|
| `engine_type=""` | "引擎类型不能为空" + "未知的内置引擎类型: " | "引擎类型不能为空" | "引擎类型不能为空" |

重构前 `routes_config` 用 `if`（非 `elif`），空 engine_type 会同时产生两条错误（第二条是冗余的空字符串"未知"错误）。重构后用 `elif`，仅产生一条语义准确的错误。现有测试仅断言 `"引擎类型不能为空" in errors`，兼容此变化。

---

## 4. 可观测性

在 `validate_search_instance` 中添加了校验耗时和错误详情日志：
```python
t0 = time.perf_counter()
errors = validate_dict_against_rules(instance, SEARCH_INSTANCE_VALIDATION_RULES)
elapsed_ms = (time.perf_counter() - t0) * 1000
...
logger.debug("搜索实例校验完成: 声明式校验耗时=%.3fms, 错误数=%d, 错误详情=%s", ...)
```
`validate_dict_against_rules` 内部也针对每条规则输出 DEBUG 日志（跳过/失败/通过），便于生产环境排查配置问题。

---

## 5. 变更文件清单

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `agent/config_validation.py` | 新建 | 共享声明式校验基础设施 |
| `agent/server_routes/routes_config.py` | 修改 | `validate_search_instance` 重构为混合方案 + 耗时日志 |
| `app_server.py` | 修改 | 消除重复函数，改为导入 |
| `tests/unit/test_search_instance_validation.py` | 新建 | 71 个边界测试（规则集 + 包装函数 + 常量） |

---

## 6. 验证结果

- **新增边界测试**: 71 个测试全部通过（含 9 个内置引擎参数化测试）
- **现有回归测试**: `test_routes_config_validation.py` 11 个测试全部通过
- **合计**: 82 个测试通过，0 失败
- **全量测试套件**: 见 Task #20 结果

---

## 7. 后续可扩展方向

- 将 LLM 实例校验（`validate_llm_instance`）和 MCP 服务校验也迁移到 `config_validation.py` 的声明式框架
- 为 `validate_dict_against_rules` 添加跨字段条件规则支持（如 `depends_on` 属性），进一步减少包装函数中的命令式代码
- 当前 `_range_validator` 接受数字字符串（如 `"30"`），比原代码更宽松；若需严格类型检查可增加 `strict_type` 参数
