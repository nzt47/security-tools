# 依赖版本兼容性修复发布文档

**版本**: v2.0.0-dependency-fix
**日期**: 2026-08-01
**状态**: ✅ 全部完成并推送
**范围**: CI 依赖版本兼容性修复 + pyproject.toml 约束补全（P0-P2）

---

## 一、发布概述

CI Integration Test 因 `--no-cov-fail-under` 参数不被旧版 pytest-cov 识别而失败（exit code 4），暴露出 CI 中 7 处 `pip install` 命令未指定版本约束的系统性问题。本次发布修复了所有依赖版本兼容性问题，补全 pyproject.toml 中 11 项缺失/过时的约束。

## 二、变更概览

### CI 配置修复（3 个提交）

| 提交 | 修复内容 | 根因 |
|------|---------|------|
| `deffc832` | `--no-cov-fail-under` → `--cov-fail-under=0` | pytest-cov <4.1 不支持该参数 |
| `231d9824` | `pip install pydantic` → `pip install "pydantic>=2.0.0"` | 代码使用 v2 API |
| `cdf9026f` | pytest/ruff/black 版本约束（5 处） | CI 未指定版本 |

### pyproject.toml 补全（3 个提交）

| 提交 | 优先级 | 内容 |
|------|--------|------|
| `55bdf617` | P0 | 补全 4 项缺失约束 |
| `fd716d21` | P1 | 对齐 4 项版本冲突 |
| `1b6bdf6d` | P2 | 补全 3 项缺失约束 |

## 三、P0-P2 变更明细

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

## 四、测试修复

- 修复 `test_chat_increment_interaction_count`: 禁用模板/语义层短路返回
- 修复 `test_tool_calling_chat_flow`: 禁用模板/语义层 + 输入改长
- 修复 `test_memory_logging`: 禁用模板/语义层 + 输入改长
- 移除 6 个测试的 `@pytest.mark.skip_ci` 标记

## 五、兼容性验证

| 依赖 | 验证方法 | 结果 |
|------|---------|------|
| pydantic v2 | API 扫描（model_dump/ConfigDict/field_validator） | ✅ 30+ 处 |
| openai v2 | API 扫描（OpenAI() 客户端） | ✅ 兼容 |
| anthropic v0.40+ | API 扫描（Anthropic() 客户端） | ✅ 兼容 |
| numpy 2.x | deprecated API 扫描（np.float_等 20+ 项） | ✅ 0 命中 |
| pytest 9.0.3 | 本地 47 测试通过 | ✅ 兼容 |

## 六、最终环境健康状态

### pyproject.toml 统计

| 指标 | 修改前 | 修改后 |
|------|--------|--------|
| 核心依赖 | 38 | 44 (+6) |
| 开发依赖 | 13 | 15 (+2) |
| 版本冲突 | 4 | 0 ✅ |
| 缺失约束 | 7 | 0 ✅ |

### pip check 结果

```
项目自身依赖: ✅ 0 冲突
第三方包冲突: 10 个（非本项目引入）
```

### CI pip install 审计

| 类别 | 数量 |
|------|------|
| ✅ 已约束 | 7 处 |
| ✅ requirements.txt | 5 处 |
| ✅ 容错（|| true） | 8 处 |
| ❌ 未约束 | 0 处 |

### 本地测试

```
47 passed, 0 failed, 3 warnings in 5.16s
```

## 七、已知问题

- langchain 0.2.17 与 numpy 2.x / openai 2.x 不兼容（非本次变更引入）
- 需升级 langchain 到兼容版本（后续处理）

## 八、Commit 列表

| Commit | 描述 |
|--------|------|
| `deffc832` | fix(ci): --no-cov-fail-under -> --cov-fail-under=0 |
| `231d9824` | fix(ci): pin pydantic>=2.0.0 |
| `cdf9026f` | fix(ci): pin pytest/ruff/black version constraints |
| `55bdf617` | fix(deps): P0 补全 pyproject.toml 缺失约束 |
| `fd716d21` | fix(deps): P1 对齐版本冲突 + 变更影响分析 |
| `1b6bdf6d` | fix(deps): P2 补全 httpx/requests/scipy 约束 |
| `926b2555` | docs: 依赖修复完整总结报告 + Release Notes |

## 九、回滚方案

```bash
git revert 926b2555 1b6bdf6d fd716d21 55bdf617 cdf9026f 231d9824 deffc832
```

## 十、三义校验

- **不易**: pyproject.toml 作为依赖声明真相源，与代码实际使用和 requirements.txt 完全对齐
- **变易**: 版本约束使用 `>=,<` 范围，允许 patch/minor 更新，CI 使用精确版本锁定
- **简易**: 11 项变更逐项验证，每项有代码扫描或测试支撑
