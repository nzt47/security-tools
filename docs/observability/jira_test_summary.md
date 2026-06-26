# [YUNSHU-TEST] 单元测试覆盖率与通过率报告

**报告日期：** 2026-06-26
**测试环境：** Python 3.12.0 / Windows 10 / pytest 9.0.3
**关联文档：** [unit_test_report.md](file:///c:/Users/Administrator/agent/docs/observability/unit_test_report.md)

---

## 执行摘要

| 指标 | 数值 | 阈值 | 状态 |
|------|------|------|------|
| 测试通过率 | 97.38%（3014/3095） | ≥ 95% | PASS |
| 总代码覆盖率 | 31.93% | ≥ 40% | FAIL |
| 分支覆盖率 | 23.62% | — | — |
| 新增模块覆盖率 | 82.54% | ≥ 80% | PASS |
| 测试耗时 | 340.32 秒（5 分 40 秒） | ≤ 30 分钟 | PASS |
| 失败用例 | 72 失败 + 3 错误 | — | 需修复 |

> **更新（2026-06-27）：** `test_error_handler.py`（16 失败 + 3 错误）和 `test_search.py`（3 失败）已全部修复，验证通过 **348 passed, 0 failed**。剩余失败降至 53 个。

---

## 失败用例分布

| 测试文件 | 失败数 | 根因分类 | 优先级 | 状态 |
|---------|--------|---------|--------|------|
| test_task_scheduler.py | 23 | 时间断言不稳定 / Windows 路径兼容 | P1 | 待修复 |
| ~~test_error_handler.py~~ | ~~16+3~~ | 参数签名变更 / mock 路径 / 浮点精度 | P1 | **已修复** |
| test_verification.py | 5 | Critic 降级逻辑变更 | P0 | 待修复 |
| test_config_secure.py | 4 | 敏感字段过滤策略变更 | P1 | 待修复 |
| test_p6_snapshot_advanced.py | 4 | P6 快照字段结构变更 | P1 | 待修复 |
| test_text_tools.py | 3 | humanizer-zh 检测规则调整 | P2 | 待修复 |
| ~~test_search.py~~ | ~~3~~ | SearchEngine 配置结构变更 | P0 | **已修复** |
| 其他 8 个文件 | 8 | 各类断言失败 | P2 | 待修复 |

---

## 覆盖率分布（按模块）

| 模块 | 文件数 | 覆盖率 | 评估 |
|------|--------|--------|------|
| persona | 3 | 91.66% | 优秀 |
| planning | 11 | 72.85% | 良好 |
| core | 2 | 62.64% | 中等 |
| utils | 3 | 45.36% | 中等 |
| agent | 227 | 36.72% | 偏低 |
| cognitive | 6 | 21.00% | 低 |
| memory | 7 | 19.02% | 低 |
| lifetrace | 4 | 16.63% | 低 |
| sensor | 30 | 9.18% | 极低 |

**覆盖率分布：** 148 个文件覆盖率 < 20%（占总数 56.5%），是拉低整体覆盖率的主要原因。

---

## 风险评估

### 高风险（P0）
- **Critic 降级逻辑与测试期望不一致**：Critic 服务不可用时降级返回 `passed=True`，但测试期望 `passed=False`，可能掩盖真实评估失败
- ~~SearchEngine 方法重命名测试未更新~~ **已修复**

### 中风险（P1）
- **任务调度器 23 个测试失败**：时间相关断言不稳定 + Windows 路径兼容性，调度功能可能存在回归
- **整体覆盖率 31.93% 未达 40% 阈值**：大量代码无测试保护
- ~~错误处理器 16 个测试失败~~ **已修复**

### 低风险（P2）
- sensor 模块覆盖率 9.18%，几乎无测试
- 148 个文件覆盖率 0-20%
- 29 个测试文件因外部依赖被跳过（chromadb / 网络 / 浏览器 / LLM）

---

## 已修复内容（2026-06-27）

### test_error_handler.py — 10 个失败已修复

| 失败类别 | 数量 | 修复方式 |
|---------|------|---------|
| 浮点精度（jitter 随机抖动） | 3 | 添加 `jitter_factor=0.0` 禁用抖动 |
| mock 路径错误 | 3 | `agent.error_handler` → `agent.monitoring.metrics`（延迟导入） |
| custom_condition 签名不匹配 | 1 | 移除多余的 `attempt` 参数 |
| 断路器状态断言过时 | 1 | 改为 `assert cb._can_half_open() is True` |
| 自定义重试条件字符串匹配 | 1 | `"no retry"` → `"skip this"`（避免子串匹配） |
| SearchEngine 默认配置断言 | 1+ | 配合 test_search.py 修复 |

### test_search.py — 3 个失败已修复

| 失败用例 | 修复方式 |
|---------|---------|
| test_init_default_config | `assert _default_engine == ""`（新版默认空字符串） |
| test_init_with_api_keys | 增加 `engine.update_config(config)` 调用 |
| test_engine_selection | `assert _default_engine == ""` |

---

## 行动计划

| 优先级 | 任务 | 负责人 | 预估工时 |
|--------|------|--------|---------|
| P0 | 修复 test_verification.py（Critic 降级逻辑） | 后端 | 2 人日 |
| P1 | 修复 test_task_scheduler.py（23 个时间/路径兼容性失败） | 后端 | 3 人日 |
| P1 | 修复 test_config_secure.py（敏感字段过滤策略） | 后端 | 1 人日 |
| P1 | 修复 test_p6_snapshot_advanced.py（快照字段结构） | 后端 | 1 人日 |
| P2 | sensor 模块测试补全（242 个用例，详见 [sensor_test_plan.md](file:///c:/Users/Administrator/agent/docs/observability/sensor_test_plan.md)） | 后端 | 22 人日 |
| P2 | 将整体覆盖率从 31.93% 提升至 40%（阶段目标：40% → 55% → 70%） | 全员 | 持续 |

---

## 附件

- [完整测试报告](file:///c:/Users/Administrator/agent/docs/observability/unit_test_report.md)
- [sensor 模块测试补全计划](file:///c:/Users/Administrator/agent/docs/observability/sensor_test_plan.md)
- [pytest 完整输出日志](file:///c:/Users/Administrator/agent/coverage_report/full_test_output.txt)
- [coverage.json 覆盖率数据](file:///c:/Users/Administrator/agent/coverage_report/coverage.json)
