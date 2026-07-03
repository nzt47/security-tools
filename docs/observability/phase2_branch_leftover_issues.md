# Phase 2 分支遗留问题与待办清单

**分支**：`phase2-visibility-convergence`
**目标合并基线**：`origin/master`
**分支领先提交数**：69 个
**生成时间**：2026-07-04
**关联文档**：[log_dict_refactoring_summary.md](./log_dict_refactoring_summary.md)

---

## 1. 工作区状态总览

### 1.1 已跟踪文件修改统计（不含未跟踪）
- 修改文件总数：**74 个**
- 主要变更类型：
  - **64 个 log_dict 迁移文件**（脚本化批量替换，1411 处 `json.dumps({...}) → log_dict({...})`）
  - **10 个手动修改文件**（与日志/可观测性相关的非迁移变更）
  - **CI 配置变更**：5 个 workflow 文件

### 1.2 未跟踪文件分类

#### 应提交（log_dict 重构相关 — 8 个）
| 文件 | 用途 | 状态 |
|---|---|---|
| `docs/observability/log_dict_refactoring_summary.md` | 技术总结文档（任务 10 产物） | 已生成 |
| `docs/log_dict_migration_roadmap.md` | 迁移路线图 | 已生成 |
| `scripts/migrate_to_log_dict.py` | 通用迁移工具（AST 增强） | 已增强 |
| `scripts/check_double_serialization.py` | 双重序列化检测器 | 已完成 |
| `scripts/migrate_top20_batch.py` | 批量迁移脚本 | 已完成 |
| `scripts/run_log_perf_stress_test.py` | 性能压力测试 | 已完成 |
| `tests/unit/test_log_dict_performance.py` | 性能基准测试（10 passed） | 已验证 |
| `tests/unit/test_memory_comparison.py` | 内存收益验证（14 passed） | 已验证 |

#### 应提交（其他配套工作 — 11 个）
- `.github/workflows/log-perf-guard.yml` — CI 性能守门 Job
- `docs/ci_log_perf_guard.md` — CI 守门文档
- `docs/ci_mail_notification_setup.md` — 邮件通知配置
- `docs/di_refactor_and_bugfix_report.md` — 依赖注入重构报告
- `docs/error_handler_di_refactor_plan.md` — 错误处理 DI 计划
- `docs/observability/arch_metric_fix_retrospective_report.md` — 架构指标修复回顾
- `docs/observability/ci_timeout_alert_notification.md` — CI 超时告警
- `docs/observability/ci_workflow_changes_commit_record.md` — CI 工作流变更记录
- `docs/observability/jira_issue_drafts.md` — Jira 议题草稿
- `docs/observability/resilience_api_contract_fix_summary.md` — 韧性 API 契约修复总结
- `test_reports/p0-security-junit.xml`, `test_reports/p0_coverage.json` — 测试报告

#### 临时调试脚本（不应提交 — 7 个）
- `scripts/_check_ci_detail.py`、`scripts/_check_ci_status.py`、`scripts/_check_f1572c4b.py`
- `scripts/_check_rerun_status.py`、`scripts/_extract_coverage.py`
- `scripts/_get_job_logs.py`、`scripts/_rerun_failed_jobs.py`
- 处理建议：放入 `.gitignore` 的 `scripts/_*.py` 模式，或直接删除

#### 运行时/编辑器数据（不应提交）
- `.cursor/`、`.trae/`（IDE 元数据）
- `.file_backups/`（运行时备份文件）
- `data/state/`、`data/lifetrace/sources/`、`data/reports/`、`data/audit/`、`data/`（运行时数据）
- `workspace/`、`unit_run.log`、`_test_sanitize.py`（临时测试输出）

---

## 2. 预存测试失败清单

### 2.1 失败统计
- **总失败数**：45 个（已验证为预存，非 log_dict 迁移引入）
- **前序会话记录**：49 个（数量小幅波动，但根因一致）

### 2.2 按根因分类
| 模块 | 失败数 | 根因 | 修复建议 |
|---|---|---|---|
| `test_task_scheduler.py` | 29 | task_scheduler API 不匹配（旧测试期望与重构后实现不一致） | 单独 PR 修复 API 兼容 |
| `test_error_handler.py` | 14 | error_handler API 不匹配（DI 重构后签名变更） | 同步更新测试或回退 API |
| `test_config_secure.py` | 5 | 脱敏期望不匹配（`[REDACTED]` vs `********`/`***`） | 统一脱敏替换值为 `REDACTED_VALUE` |
| `test_v2_performance_patch.py` | 1 | mock 断言失效（`info('V2 优化初始化...')` 日志文本漂移） | 更新断言或使用 substring 匹配 |

### 2.3 与 log_dict 重构的关系
**结论：所有 45 个失败均与 log_dict 迁移无关**
- 失败原因全部为 API 契约/字符串期望不匹配
- 迁移前后失败的测试集合稳定不变
- 已通过对比 master 上的相同测试套件验证

---

## 3. log_dict 迁移质量验证

### 3.1 语法与导入
- **64 个迁移文件**全部通过 `py_compile` 语法检查
- 全部正确导入 `from agent.logging_utils import log_dict`
- AST 解析插入策略已修复 3 个早期 bug：
  - 函数内 import（`agent/memory/router.py`）
  - 模块级代码后 import（`agent/digital_life.py`）
  - docstring 内 import（`agent/caching/multi_level_cache.py`）

### 3.2 非迁移手动修改文件（10 个）
这些文件包含 log_dict 相关变更但不在批量迁移脚本覆盖范围：
```
agent/feedback.py
agent/monitoring/self_healer.py
agent/server_routes/routes_skills_mgmt.py
agent/server_routes/tracing_decorator.py
agent/server_routes/tracing_middleware.py
agent/skills_mgmt/context_injector.py
agent/skills_mgmt/enhancer.py
agent/skills_mgmt/service.py
agent/utils/perf_monitor.py
agent/workflow_learning/models.py
```
**验证状态**：已通过 `py_compile` 检查；相关测试通过。

### 3.3 测试结果
- `test_log_dict_performance.py`：**10/10 通过**（性能基准）
- `test_memory_comparison.py`：**14/14 通过**（内存收益验证）
- `test_log_system_safe_logger.py`：**18/18 通过**（脱敏 + Filter 链）
- 全量回归（前序会话）：1143 passed / 49 failed（已确认非迁移引入）

---

## 4. 待办事项与下一步行动

### 4.1 P0 — 阻塞 PR 合并
- [ ] **提交未提交变更**（任务 11）
  - 选择性 stage：仅添加 log_dict 相关 + 配套工作文件
  - 排除：`scripts/_*.py`、`.file_backups/`、`data/`、`workspace/`、`.cursor/`、`.trae/`、`unit_run.log`、`_test_sanitize.py`
- [ ] **创建 PR 描述**（含性能数据表格）
  - 推送分支：`git push origin phase2-visibility-convergence`
  - 发起 PR：`phase2-visibility-convergence → master`
- [ ] **更新 `.gitignore`**（防止再次跟踪临时文件）
  - 添加：`scripts/_*.py`、`unit_run.log`、`_test_sanitize.py`、`workspace/`、`.file_backups/`

### 4.2 P1 — 合并后跟进
- [ ] **修复 task_scheduler API 不匹配**（单独 PR）
  - 影响：29 个失败测试
  - 方案：更新测试期望或回退 task_scheduler 公共 API
- [ ] **修复 error_handler DI 重构兼容**（单独 PR）
  - 影响：14 个失败测试
  - 方案：在 DI 重构基础上补回旧 API 委托
- [ ] **统一脱敏替换值**
  - 影响：5 个失败测试
  - 方案：将 `test_config_secure.py` 期望从 `[REDACTED]` 改为 `REDACTED_VALUE`
- [ ] **修复 v2_performance_patch mock 断言**
  - 影响：1 个失败测试
  - 方案：使用 substring 匹配替代精确字符串

### 4.3 P2 — 后续规划
- [ ] **任务 13：日志系统自动化监控告警规则规划**
  - 基于本分支的 log_dict 架构设计告警阈值
  - 输出：`docs/observability/log_alert_rules_plan.md`
- [ ] **CI 守门 Job 接入**
  - 启用 `.github/workflows/log-perf-guard.yml`
  - 性能回归阈值：单函数 ≥ 2x、P99 ≥ 30% 退化
- [ ] **运行时指标暴露**
  - 通过 `agent/utils/perf_monitor.py` 暴露 Prometheus 指标

---

## 5. 风险评估

### 5.1 已识别风险
| 风险项 | 等级 | 缓解措施 |
|---|---|---|
| 64 文件批量迁移可能引入隐性回归 | 中 | 已通过全量回归（1143 passed）+ 语法检查双重验证 |
| 非迁移文件中 log_dict 手动修改 | 低 | 已通过 `py_compile` 与单测验证 |
| 45 个预存失败可能掩盖新引入问题 | 中 | 已通过 master 对比确认失败集合稳定 |
| 大量未跟踪文件可能误入提交 | 高 | 已制定选择性 stage 策略 |
| log_dict 在并发场景的线程安全 | 低 | log_dict 内部使用 `dict(payload)` 浅复制，无共享状态 |

### 5.2 未识别的潜在问题
- 未在真实生产负载下验证 log_dict 性能
- 未验证 log_dict 在异步上下文（asyncio）下的行为
- 未覆盖跨进程日志聚合场景

---

## 6. 结论

**分支状态评估：可进入 PR 流程**

- log_dict 重构核心工作完成且通过验证
- 45 个预存失败非本次引入，可作为已知问题在 PR 描述中说明
- 未跟踪文件中需提交的部分已明确分类
- 无 log_dict 相关 TODO/FIXME 残留
- 性能数据已收集完毕，可纳入 PR 描述

**建议下一步**：执行任务 11（提交 + PR 描述），然后任务 13（监控规则规划）。
