# 依赖修复完整总结报告

**日期**: 2026-08-01
**范围**: CI 依赖版本兼容性修复 + pyproject.toml 约束补全（P0-P2）
**状态**: ✅ 全部完成并推送

## 1. 问题起源

CI Integration Test 因 `--no-cov-fail-under` 参数不被旧版 pytest-cov 识别而失败（exit code 4），暴露出 CI 中 7 处 `pip install` 命令未指定版本约束的系统性问题。

## 2. 变更概览

### 2.1 CI 配置修复（3 个提交）

| 提交 | 修复内容 | 根因 |
|------|---------|------|
| `deffc832` | `--no-cov-fail-under` → `--cov-fail-under=0` | pytest-cov <4.1 不支持该参数 |
| `231d9824` | `pip install pydantic` → `pip install "pydantic>=2.0.0"` | 代码使用 v2 API |
| `cdf9026f` | pytest/ruff/black 版本约束（5 处） | CI 未指定版本 |

### 2.2 pyproject.toml 补全（3 个提交）

| 提交 | 优先级 | 内容 |
|------|--------|------|
| `55bdf617` | P0 | 补全 4 项缺失约束（pydantic/pytest-timeout/ruff/prometheus-client） |
| `fd716d21` | P1 | 对齐 4 项版本冲突（openai/anthropic/tiktoken/numpy） |
| `1b6bdf6d` | P2 | 补全 3 项缺失约束（httpx/requests/scipy） |

## 3. P0-P2 变更明细

### P0: 补全缺失约束（4 项）

| 依赖 | 约束 | 位置 | 理由 |
|------|------|------|------|
| pydantic | `>=2.0.0,<3.0.0` | dependencies | 代码 30+ 处使用 v2 API |
| pytest-timeout | `>=2.0.0` | dev | CI 使用 --timeout |
| ruff | `>=0.4.0` | dev | CI Lint job 使用 |
| prometheus-client | `>=0.20.0` | dependencies | 代码 import prometheus_client |

### P1: 对齐版本冲突（4 项）

| 依赖 | 修改前 | 修改后 | requirements.txt | 代码兼容性 |
|------|--------|--------|-----------------|-----------|
| openai | `>=1.0.0,<2.0.0` | `>=2.0.0,<3.0.0` | `==2.40.0` | ✅ `OpenAI()` v2 SDK |
| anthropic | `>=0.30.0,<0.40.0` | `>=0.40.0,<1.0.0` | `==0.105.2` | ✅ `Anthropic()` v0.40+ |
| tiktoken | `>=0.5.0,<0.7.0` | `>=0.7.0,<1.0.0` | `==0.13.0` | ✅ 已是最新 |
| numpy | `>=1.24.0,<2.0.0` | `>=2.0.0,<3.0.0` | `==2.4.6` | ✅ 0 个 deprecated API |

### P2: 补全其他缺失（3 项）

| 依赖 | 约束 | 代码引用 | requirements.txt |
|------|------|---------|-----------------|
| httpx | `>=0.27.0,<1.0.0` | 1 文件 | `==0.28.1` |
| requests | `>=2.31.0,<3.0.0` | 13 文件 | `==2.34.2` |
| scipy | `>=1.13.0,<2.0.0` | 0 文件（间接） | `==1.17.1` |

## 4. 最终环境健康状态

### 4.1 pyproject.toml 统计

| 指标 | 修改前 | 修改后 |
|------|--------|--------|
| 核心依赖 | 38 | 44 (+6) |
| 开发依赖 | 13 | 15 (+2) |
| 版本冲突 | 4 | 0 ✅ |
| 缺失约束 | 7 | 0 ✅ |

### 4.2 pip check 结果

```
项目自身依赖: ✅ 0 冲突
第三方包冲突: 10 个（非本项目引入）
```

**第三方包冲突清单**（均非本次变更引入）：

| 包 | 冲突 | 处理建议 |
|---|------|---------|
| langchain 0.2.17 | `numpy<2.0.0` | 升级 langchain |
| langchain-openai 0.1.23 | `openai<2.0.0` | 升级 langchain-openai |
| mootdx 0.11.7 | `httpx<0.26.0` | 与本项目无关 |
| gtts 2.5.4 | `click<8.2` | 与本项目无关 |
| hermes-agent 0.14.0 | `tenacity==9.1.4` | 与本项目无关 |
| opentelemetry ×3 | 版本不一致 | 与本项目无关 |

### 4.3 本地测试验证

```
47 passed, 0 failed, 3 warnings in 5.16s
```

### 4.4 CI pip install 审计

| 类别 | 数量 |
|------|------|
| ✅ 已约束 | 7 处 |
| ✅ requirements.txt | 5 处 |
| ✅ 容错（|| true） | 8 处 |
| ❌ 未约束 | 0 处 |

## 5. 代码兼容性验证总结

| 依赖 | 验证方法 | 结果 |
|------|---------|------|
| pydantic v2 | API 扫描（model_dump/ConfigDict/field_validator） | ✅ 30+ 处 |
| openai v2 | API 扫描（OpenAI() 客户端） | ✅ 兼容 |
| anthropic v0.40+ | API 扫描（Anthropic() 客户端） | ✅ 兼容 |
| numpy 2.x | deprecated API 扫描（np.float_等 20+ 项） | ✅ 0 命中 |
| pytest 9.0.3 | 本地 47 测试通过 | ✅ 兼容 |

## 6. 生成文档清单

| 文档 | 路径 | 内容 |
|------|------|------|
| CI 依赖审计报告 | `docs/CI_DEPENDENCY_AUDIT_REPORT.md` | CI pip install 审计 |
| pyproject.toml 待办清单 | `docs/PYPROJECT_TOML_TODO_CHECKLIST.md` | P0-P2 待办项 |
| 依赖升级影响分析 | `docs/DEPENDENCY_UPGRADE_IMPACT_ANALYSIS.md` | P0+P1 影响分析 |
| P2 影响分析 | `docs/P2_DEPENDENCY_IMPACT_ANALYSIS.md` | P2 影响分析 |
| 本总结报告 | `docs/DEPENDENCY_FIX_SUMMARY_REPORT.md` | P0-P2 完整总结 |

## 7. 三义校验

- **不易**: pyproject.toml 作为依赖声明真相源，与代码实际使用和 requirements.txt 完全对齐
- **变易**: 版本约束使用 `>=,<` 范围，允许 patch/minor 更新，CI 使用精确版本锁定
- **简易**: 11 项变更逐项验证，每项有代码扫描或测试支撑
