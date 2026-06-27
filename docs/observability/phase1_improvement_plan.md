# 阶段 1 未达标指标改进计划

> 生成时间：2026-06-28
> 当前阶段：阶段 1（部分实施）
> 文档用途：针对 4 项未达标指标制定阶段性改进措施，为阶段 2 收敛提供执行路线

## 一、现状概览

| 指标 | 实测值 | 阶段1阈值 | 阶段1目标 | 差距 | 阶段2目标 | 最终目标 |
|------|--------|----------|----------|------|----------|---------|
| structured_log_coverage | 26.5% | 26 | 50% | -23.5% | 70% | 80% |
| trace_coverage | 16.7% | 16 | 50% | -33.3% | 60% | 70% |
| test_coverage | 0.6% | 0 | 55% | -54.4% | 65% | 70% |
| boundary_test_coverage | 12.2% | 12 | 70% | -57.8% | 80% | 90% |

> exception_coverage（71.6%）已达标阶段 1 目标 70%，本计划不涉及

## 二、各指标根因分析与改进措施

### 2.1 structured_log_coverage（26.5% → 50%）

**根因**：项目中有 2552 条日志调用，仅 680 条为 JSON 结构化格式（含 trace_id/module_name/action/duration_ms）。大量 logger.info/warning/error 调用使用传统字符串拼接，不符合可观测性强制约束。

**改进措施**：
1. 批量转换 agent/ 目录下 288 个 .py 文件中的 logger 调用为 json.dumps 结构化输出
2. 优先转换核心模块：orchestrator、workflow_engine、tool_calling、server_routes
3. 建立结构化日志模板，统一字段规范（trace_id、module_name、action、duration_ms）

**里程碑**：
- M1（+15%）：转换核心模块 4 个，覆盖率提升至 40%
- M2（+10%）：转换监控模块 6 个，覆盖率提升至 50%（阶段 1 目标）
- M3（+20%）：转换剩余模块，覆盖率提升至 70%（阶段 2 目标）

**预估工作量**：需转换约 600 条 logger 调用，每条平均 2 分钟，合计约 20 工时

### 2.2 trace_coverage（16.7% → 50%）

**根因**：216 个路由中仅 36 个使用了 @trace_route 装饰器或 TraceContext，覆盖率不足五分之一。

**改进措施**：
1. 在 agent/server_routes/ 目录下批量添加 @trace_route 装饰器
2. 优先覆盖核心业务路由：工具调用、任务分发、记忆存储、模型路由
3. 确保 @trace_route 装饰器注入 trace_id 并传递至下游调用

**里程碑**：
- M1（+20%）：为核心业务路由（约 43 个）添加装饰器，覆盖率提升至 35%
- M2（+15%）：为辅助路由（约 32 个）添加装饰器，覆盖率提升至 50%（阶段 1 目标）
- M3（+10%）：补齐剩余路由，覆盖率提升至 60%（阶段 2 目标）

**预估工作量**：需为 72 个路由添加装饰器，每个约 5 分钟，合计约 6 工时

### 2.3 test_coverage（0.6% → 55%）

**根因**：coverage.xml 的 line-rate=0.005513（0.6%），降级逻辑已移除，真实覆盖率极低。CI 中 full-project-tests job 生成的 coverage.xml line-rate≈19.7%，仍远低于目标。

**改进措施**：
1. 修复 CI 中 full-project-tests job，确保 coverage.xml 正确生成且 line-rate 真实反映测试覆盖
2. 为 agent/ 核心模块补充单元测试：orchestrator、workflow_engine、tool_calling、circuit_breaker、rate_limiter
3. 提升 CI 中 coverage.xml 的 line-rate 至 55% 以上
4. 本地运行 `pytest --cov=agent --cov=scripts --cov-report=xml` 验证覆盖率

**里程碑**：
- M1（+20%）：为核心模块补充单元测试，CI line-rate 提升至 20%
- M2（+15%）：为监控模块补充测试，CI line-rate 提升至 35%
- M3（+20%）：补齐剩余模块测试，CI line-rate 提升至 55%（阶段 1 目标）
- M4（+10%）：持续补齐，CI line-rate 提升至 65%（阶段 2 目标）

**预估工作量**：需新增约 200 个测试用例，每个约 30 分钟，合计约 100 工时

### 2.4 boundary_test_coverage（12.2% → 70%）

**根因**：3827 个测试中仅 466 个为边界测试（12.2%）。边界测试指针对极端输入、异常场景、空值、溢出等边界条件的测试用例。

**改进措施**：
1. 分批为 32 个模块补充边界测试用例
2. 优先覆盖核心模块：orchestrator、workflow_engine、tool_calling、memory、model_router
3. 建立边界测试模板：空输入、超长字符串、非法类型、并发冲突、资源耗尽
4. 每个模块至少补充 20 个边界测试用例

**里程碑**：
- M1（+18%）：为核心 5 个模块补充边界测试（约 100 个），覆盖率提升至 30%
- M2（+20%）：为监控 6 个模块补充边界测试（约 120 个），覆盖率提升至 50%
- M3（+20%）：为剩余 21 个模块补充边界测试（约 420 个），覆盖率提升至 70%（阶段 1 目标）
- M4（+10%）：持续补齐，覆盖率提升至 80%（阶段 2 目标）

**预估工作量**：需新增约 7300 个边界测试用例，每个约 10 分钟，合计约 1200 工时（分批执行）

## 三、阶段 2 收敛路线图

```
阶段 1（当前）           阶段 2（计划）            最终目标
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│ structured_log│      │ structured_log│      │ structured_log│
│   26.5%       │ ──→  │   70%        │ ──→  │   80%        │
├──────────────┤      ├──────────────┤      ├──────────────┤
│ trace_cover   │      │ trace_cover   │      │ trace_cover   │
│   16.7%       │ ──→  │   60%        │ ──→  │   70%        │
├──────────────┤      ├──────────────┤      ├──────────────┤
│ test_cover    │      │ test_cover    │      │ test_cover    │
│   0.6%        │ ──→  │   65%        │ ──→  │   70%        │
├──────────────┤      ├──────────────┤      ├──────────────┤
│ boundary      │      │ boundary      │      │ boundary      │
│   12.2%       │ ──→  │   80%        │ ──→  │   90%        │
├──────────────┤      ├──────────────┤      ├──────────────┤
│ exception     │      │ exception     │      │ exception     │
│   71.6% ✅    │ ──→  │   80%        │ ──→  │   90%        │
└──────────────┘      └──────────────┘      └──────────────┘
```

## 四、风险评估

| 风险项 | 影响 | 概率 | 缓解措施 |
|--------|------|------|---------|
| boundary_test 补齐工作量过大（1200 工时） | 阶段 1 目标 70% 难以短期达成 | 高 | 分批执行，优先核心模块，阶段 1 可调整为 30% |
| test_coverage CI 生成环境差异 | 本地与 CI line-rate 不一致 | 中 | 统一 CI 环境配置，确保 full-project-tests 正确生成 |
| structured_log 批量转换引入 bug | 核心逻辑日志丢失或格式错误 | 中 | 转换后逐模块验证，保留原日志作为 fallback |
| trace_coverage 装饰器循环依赖 | @trace_route 导入导致循环引用 | 低 | 使用延迟导入，参照现有 36 个路由的实现模式 |

## 五、验证方式

每个里程碑完成后，执行以下验证：

```bash
# 运行可见性报告，确认所有指标在新阈值下通过
python scripts/visibility_report.py --config config.yaml --output docs/observability/visibility_report.md

# 确认退出码为 0（所有指标达标）
echo $?

# 运行测试套件确保无回归
python -m pytest tests/unit/test_visibility_report.py tests/integration/test_visibility_report.py -q
```
