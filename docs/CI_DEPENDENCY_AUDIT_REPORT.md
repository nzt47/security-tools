# CI 依赖版本兼容性审计报告

**日期**: 2026-08-01
**触发事件**: CI Integration Test 因 `--no-cov-fail-under` 参数不被识别而失败（exit code 4）

## 1. 问题背景

CI 流水线中 `pip install` 命令未指定版本约束，导致不同环境安装不同版本的依赖包，引发兼容性问题：

- **直接原因**: `--no-cov-fail-under` 参数在 pytest-cov <4.1 不支持，但 pyproject.toml 约束 `>=4.0.0` 允许安装 4.0.x
- **根本原因**: CI 中 7 处 `pip install` 命令未指定版本约束，依赖解析不确定

## 2. 修复内容

### 2.1 提交链

| 提交 | 修复内容 |
|------|---------|
| `deffc832` | `--no-cov-fail-under` → `--cov-fail-under=0`（兼容 pytest-cov 4.0+） |
| `231d9824` | `pip install pydantic` → `pip install "pydantic>=2.0.0"`（2 处） |
| `cdf9026f` | pytest/ruff/black 版本约束（5 处） |

### 2.2 修改明细

| 行号 | 修改前 | 修改后 | 约束来源 |
|------|--------|--------|---------|
| L47 | `pip install ruff` | `pip install "ruff>=0.4.0"` | 合理下限 |
| L52 | `pip install black` | `pip install "black>=23.0.0"` | pyproject.toml |
| L125 | `pip install pytest pytest-timeout pytest-asyncio` | `pip install "pytest>=7.0.0" "pytest-timeout>=2.0.0" "pytest-asyncio>=0.21.0"` | pyproject.toml |
| L130 | `pip install pytest-cov -q` | `pip install "pytest-cov>=4.0.0" -q` | pyproject.toml |
| L171 | `pip install pydantic` | `pip install "pydantic>=2.0.0"` | 代码使用 v2 API |
| L318 | `pip install pytest pytest-timeout pytest-asyncio -q` | `pip install "pytest>=7.0.0" "pytest-timeout>=2.0.0" "pytest-asyncio>=0.21.0" -q` | pyproject.toml |
| L464 | `pip install pydantic` | `pip install "pydantic>=2.0.0"` | 代码使用 v2 API |

## 3. pydantic 版本确认

### 3.1 代码 API 使用分析

| API 版本 | 使用情况 | 关键 API |
|---------|---------|---------|
| **v2**（主要） | 30+ 处 | `model_dump()`, `ConfigDict(use_enum_values=True)`, `field_validator` |
| v1（兼容 fallback） | 少量 | `mcp_adapter.py` 的 `.dict()` fallback（v2 中仍可用） |

### 3.2 v2 API 使用位置

- `agent/skills_mgmt/models.py`: `model_config = ConfigDict(...)`, `model_dump()`, `field_validator`
- `agent/workflow_learning/models.py`: `model_config = ConfigDict(...)`, `model_dump()`
- `agent/server_routes/routes_skills_mgmt.py`: `model_dump()` 15+ 处
- `agent/server_routes/routes_workflow_learning.py`: `model_dump()` 7+ 处
- `agent/skills_mgmt/service.py`, `bm25_searcher.py`, `enhancer.py`, `reviewer.py`: `model_dump()`

**结论**: 代码需要 pydantic v2，CI 约束 `>=2.0.0` 正确。

## 4. 本地兼容性验证

### 4.1 本地版本

| 依赖 | 本地版本 | CI 约束 | 兼容 |
|------|---------|---------|------|
| pydantic | 2.12.5 | `>=2.0.0` | ✅ |
| pytest | 9.0.3 | `>=7.0.0` | ✅ |
| pytest-cov | 7.1.0 | `>=4.0.0` | ✅ |
| pytest-asyncio | 1.4.0 | `>=0.21.0` | ✅ |
| pytest-timeout | (已安装) | `>=2.0.0` | ✅ |

### 4.2 测试结果

```
47 passed, 3 warnings in 5.16s
```

- pydantic v2 API（`model_dump`, `ConfigDict`, `field_validator`）全部正常
- pytest 9.0.3 + pytest-cov 7.1.0 + `--cov-fail-under=0` 参数兼容
- 6 个移除 skip_ci 标记的测试全部通过

## 5. CI pip install 命令审计

### 5.1 审计结果

| 类别 | 数量 | 说明 |
|------|------|------|
| ✅ 已约束 | 7 处 | ruff, black, pytest×2, pytest-cov, pydantic×2 |
| ✅ requirements.txt | 5 处 | 已有 `==` 精确版本锁定 |
| ✅ 容错 | 8 处 | prometheus_client（`|| true` 不影响流水线） |
| ❌ 未约束 | 0 处 | 全部修复 |

### 5.2 requirements.txt 版本锁定

| 依赖 | requirements.txt | pyproject.toml |
|------|-----------------|---------------|
| pydantic | `==2.13.4` | — |
| openai | `==2.40.0` | `>=1.0.0,<2.0.0` ⚠️ |
| anthropic | `==0.105.2` | `>=0.30.0,<0.40.0` ⚠️ |
| httpx | `==0.28.1` | — |
| numpy | `==2.4.6` | `>=1.24.0,<2.0.0` ⚠️ |

> ⚠️ 注意: requirements.txt 中 openai/anthropic/numpy 的精确版本超出了 pyproject.toml 的上限约束，建议后续对齐。

## 6. 版本约束对齐表

| 依赖 | CI 约束 | pyproject.toml | requirements.txt | 对齐 |
|------|---------|---------------|-----------------|------|
| pytest | `>=7.0.0` | `>=7.0.0` | — | ✅ |
| pytest-timeout | `>=2.0.0` | — | — | ✅ |
| pytest-asyncio | `>=0.21.0` | `>=0.21.0` | — | ✅ |
| pytest-cov | `>=4.0.0` | `>=4.0.0` | — | ✅ |
| pydantic | `>=2.0.0` | — | `==2.13.4` | ✅ |
| ruff | `>=0.4.0` | — | — | ✅ |
| black | `>=23.0.0` | `>=23.0.0` | — | ✅ |

## 7. 三义校验

- **不易**: CI 依赖版本约束与 pyproject.toml 对齐，确保构建可复现
- **变易**: 版本下限允许 patch/minor 更新，兼容未来修复
- **简易**: 7 处 `pip install` 统一添加版本约束，无额外抽象

## 8. 后续建议

1. **pyproject.toml 补全**: 添加 `pydantic>=2.0.0`、`pytest-timeout>=2.0.0`、`ruff>=0.4.0` 约束
2. **requirements.txt 对齐**: openai/anthropic/numpy 的精确版本超出 pyproject.toml 上限，需对齐
3. **CI 缓存策略**: 考虑添加 pip 缓存 key 包含版本约束，避免缓存旧版本
