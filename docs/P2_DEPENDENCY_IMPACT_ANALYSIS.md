# P2 依赖补全变更影响分析报告

**日期**: 2026-08-01
**变更范围**: pyproject.toml P2 补全 3 项缺失依赖约束
**关联文档**: [DEPENDENCY_UPGRADE_IMPACT_ANALYSIS.md](DEPENDENCY_UPGRADE_IMPACT_ANALYSIS.md)

## 1. 变更内容

| 依赖 | 新增约束 | 位置 | 代码引用 |
|------|---------|------|---------|
| httpx | `>=0.27.0,<1.0.0` | `[project] dependencies` | 1 文件（`agent/monitoring/trace_http_client.py`） |
| requests | `>=2.31.0,<3.0.0` | `[project] dependencies` | 13 文件 |
| scipy | `>=1.13.0,<2.0.0` | `[project] dependencies` | 0 文件直接引用（间接依赖） |

## 2. 版本兼容性验证

| 依赖 | pyproject.toml | requirements.txt | 本地安装 | PyPI 最新 | 兼容 |
|------|---------------|-----------------|---------|----------|------|
| httpx | `>=0.27.0,<1.0.0` | `==0.28.1` | 0.28.1 | 0.28.1 | ✅ |
| requests | `>=2.31.0,<3.0.0` | `==2.34.2` | 2.33.0 | 2.34.2 | ✅ |
| scipy | `>=1.13.0,<2.0.0` | `==1.17.1` | 1.18.0 | 1.18.0 | ✅ |

## 3. 代码使用分析

### 3.1 httpx
- **直接引用**: `agent/monitoring/trace_http_client.py:32` — `import httpx`
- **间接依赖**: openai/anthropic 的 HTTP 客户端
- **风险评估**: 🟢 低 — httpx 0.28 API 稳定

### 3.2 requests
- **直接引用**: 13 个文件
- **风险评估**: 🟢 低 — requests 2.x API 长期稳定

### 3.3 scipy
- **直接引用**: 0 个文件（agent/ 目录中无 `import scipy`）
- **间接依赖**: sentence-transformers / chromadb 的底层依赖
- **风险评估**: 🟢 低 — 仅作为间接依赖，版本约束防止意外升级

## 4. 环境健康检查

### pip check 结果

```
项目自身依赖: ✅ 无冲突
第三方包冲突: 10 个（与 P2 变更无关，与 P1 报告一致）
```

P2 新增的 3 个依赖未引入新的冲突。

## 5. 影响评估

| 影响维度 | 评估 | 说明 |
|---------|------|------|
| CI 流水线 | ✅ 无影响 | requirements.txt 已锁定精确版本 |
| 开发流程 | ✅ 改善 | `pip install -e .` 现在会安装 httpx/requests/scipy |
| 生产部署 | ✅ 无影响 | 版本约束与 requirements.txt 一致 |
| 向后兼容 | ✅ 兼容 | 仅新增约束，不修改已有约束 |

## 6. P0+P1+P2 完成状态

| 优先级 | 数量 | 完成 | 内容 |
|--------|------|------|------|
| P0 | 4 | ✅ | pydantic/pytest-timeout/ruff/prometheus-client |
| P1 | 4 | ✅ | openai/anthropic/tiktoken/numpy 版本对齐 |
| P2 | 3 | ✅ | httpx/requests/scipy 补全 |
| **合计** | **11** | **11** | **全部完成** |

## 7. 三义校验

- **不易**: pyproject.toml 声明所有项目依赖，确保 `pip install -e .` 完整安装
- **变易**: 版本约束使用 `>=,<` 范围，与 requirements.txt 精确锁定互补
- **简易**: 3 项直接添加，无风险
