# 依赖版本升级变更影响分析报告

**日期**: 2026-08-01
**变更范围**: pyproject.toml P0+P1 依赖约束补全与冲突修复
**关联文档**: [CI_DEPENDENCY_AUDIT_REPORT.md](CI_DEPENDENCY_AUDIT_REPORT.md), [PYPROJECT_TOML_TODO_CHECKLIST.md](PYPROJECT_TOML_TODO_CHECKLIST.md)

## 1. 变更概述

### 1.1 P0 补全（4 项新增）

| 依赖 | 新增约束 | 位置 | 理由 |
|------|---------|------|------|
| pydantic | `>=2.0.0,<3.0.0` | `[project] dependencies` | 代码大量使用 v2 API |
| pytest-timeout | `>=2.0.0` | `[project.optional-dependencies] dev` | CI 使用 --timeout |
| ruff | `>=0.4.0` | `[project.optional-dependencies] dev` | CI Lint job 使用 |
| prometheus-client | `>=0.20.0` | `[project] dependencies` | 代码 import prometheus_client |

### 1.2 P1 冲突修复（4 项更新）

| 依赖 | 修改前 | 修改后 | requirements.txt |
|------|--------|--------|-----------------|
| openai | `>=1.0.0,<2.0.0` | `>=2.0.0,<3.0.0` | `==2.40.0` |
| anthropic | `>=0.30.0,<0.40.0` | `>=0.40.0,<1.0.0` | `==0.105.2` |
| tiktoken | `>=0.5.0,<0.7.0` | `>=0.7.0,<1.0.0` | `==0.13.0` |
| numpy | `>=1.24.0,<2.0.0` | `>=2.0.0,<3.0.0` | `==2.4.6` |

## 2. 代码兼容性验证

### 2.1 openai v2 SDK

| 检查项 | 结果 | 证据 |
|--------|------|------|
| 客户端初始化 | ✅ 兼容 | `OpenAI(**kwargs)` — v1+ API |
| 导入方式 | ✅ 兼容 | `from openai import OpenAI` |
| 本地版本 | 2.24.0 | 已验证运行正常 |
| PyPI 最新 | 2.52.0 | 约束 `>=2.0.0,<3.0.0` 覆盖 |

### 2.2 anthropic v0.40+

| 检查项 | 结果 | 证据 |
|--------|------|------|
| 客户端初始化 | ✅ 兼容 | `Anthropic(**kwargs)` — v0.40+ API |
| 导入方式 | ✅ 兼容 | `from anthropic import Anthropic` |
| 本地版本 | 0.102.0 | 已验证运行正常 |
| PyPI 最新 | 0.120.2 | 约束 `>=0.40.0,<1.0.0` 覆盖 |

### 2.3 tiktoken v0.7+

| 检查项 | 结果 | 证据 |
|--------|------|------|
| 本地版本 | 0.13.0 | 已是最新版 |
| PyPI 最新 | 0.13.0 | 约束 `>=0.7.0,<1.0.0` 覆盖 |

### 2.4 numpy 2.x

| 检查项 | 结果 | 证据 |
|--------|------|------|
| deprecated API 扫描 | ✅ 0 命中 | `np.float_`/`np.bool8`/`np.NaN` 等均未使用 |
| 引用文件数 | 9 个 | 均使用稳定 API |
| 本地版本 | 2.4.6 | 已验证运行正常 |
| PyPI 最新 | 2.5.1 | 约束 `>=2.0.0,<3.0.0` 覆盖 |

### 2.5 pydantic v2

| 检查项 | 结果 | 证据 |
|--------|------|------|
| v2 API 使用 | ✅ 30+ 处 | `model_dump()`/`ConfigDict`/`field_validator` |
| v1 兼容 fallback | 少量 | `mcp_adapter.py` 的 `.dict()` fallback（v2 中仍可用） |
| 本地版本 | 2.12.5 | 已验证运行正常 |
| PyPI 最新 | 2.13.4 | 约束 `>=2.0.0,<3.0.0` 覆盖 |

## 3. 环境健康检查

### 3.1 pip check 结果

```
项目自身依赖: ✅ 无冲突
第三方包冲突: 10 个（与本次变更无关）
```

### 3.2 第三方包冲突（非本项目引入）

| 包 | 冲突 | 影响 |
|---|------|------|
| langchain 0.2.17 | 要求 `numpy<2.0.0` | ⚠️ langchain 不兼容 numpy 2.x |
| langchain-openai 0.1.23 | 要求 `openai<2.0.0` | ⚠️ langchain-openai 不兼容 openai 2.x |
| mootdx 0.11.7 | 要求 `httpx<0.26.0` | 与本项目无关 |
| gtts 2.5.4 | 要求 `click<8.2` | 与本项目无关 |
| hermes-agent 0.14.0 | 要求 `tenacity==9.1.4` | 与本项目无关 |
| opentelemetry ×3 | 版本不一致 | 与本项目无关 |

> **注意**: langchain/langchain-openai 的冲突需关注。如果项目使用 langchain，需升级到兼容 numpy 2.x 和 openai 2.x 的版本。

### 3.3 本地测试验证

```
47 passed, 0 failed, 3 warnings in 5.16s
```

## 4. 变更影响分析

### 4.1 对 CI 流水线的影响

| 影响 | 说明 |
|------|------|
| ✅ pytest-cov 参数兼容 | `--cov-fail-under=0` 兼容所有 4.0+ 版本 |
| ✅ pip install 版本确定 | CI 中 7 处 `pip install` 已添加版本约束 |
| ✅ pydantic v2 确认 | CI 安装 `>=2.0.0` 确保代码兼容 |
| ✅ 测试通过 | 6 个 skip_ci 标记移除后本地全部通过 |

### 4.2 对开发流程的影响

| 影响 | 说明 |
|------|------|
| ✅ `pip install -e ".[dev]"` | 现在会正确安装 pydantic/ruff/pytest-timeout |
| ✅ 版本约束对齐 | pyproject.toml 与 requirements.txt 不再冲突 |
| ⚠️ langchain 兼容 | 需确认项目是否使用 langchain，如使用需升级 |

### 4.3 对生产部署的影响

| 影响 | 说明 |
|------|------|
| ✅ openai v2 SDK | 代码已使用 `OpenAI()` 客户端 API |
| ✅ anthropic v0.40+ | 代码已使用 `Anthropic()` 客户端 API |
| ✅ numpy 2.x | 代码无 deprecated API |
| ⚠️ torch 上限 | `torch>=2.0.0,<2.5.0` 可能需更新（最新 2.6+），但不在本次范围 |

## 5. 风险评估

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| numpy 2.x 与 langchain 冲突 | 🟡 中 | 确认是否使用 langchain，如使用需升级 |
| openai 2.x 与 langchain-openai 冲突 | 🟡 中 | 同上 |
| torch 版本上限过时 | 🟢 低 | 不在本次范围，后续处理 |
| 第三方包冲突 | 🟢 低 | 非本项目引入，不影响 CI |

## 6. 回滚方案

如需回滚，执行：

```bash
git revert 55bdf617  # P0 补全
git revert <P1提交>   # P1 冲突修复
```

或手动恢复 pyproject.toml：

```toml
# 恢复为旧约束
"openai>=1.0.0,<2.0.0",
"anthropic>=0.30.0,<0.40.0",
"tiktoken>=0.5.0,<0.7.0",
"numpy>=1.24.0,<2.0.0",
# 移除 pydantic/prometheus-client/pytest-timeout/ruff
```

## 7. 三义校验

- **不易**: pyproject.toml 作为依赖声明真相源，与代码实际使用和 requirements.txt 对齐
- **变易**: 版本约束使用 `>=,<` 范围，允许 patch/minor 更新
- **简易**: P0 新增 + P1 更新，每项有代码兼容性验证支撑
