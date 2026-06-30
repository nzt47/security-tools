# M3 里程碑任务规划 — 可见性指标全面达标

**里程碑**: M3 — Phase 2 可见性收敛（最终阶段）
**分支**: `phase2-visibility-convergence`
**规划日期**: 2026-06-30
**前置里程碑**: M2（structured_log 63.7% + exception 81.2% + track_event 51.7%）
**预估工时**: 70h

---

## 一、里程碑目标

### 指标收敛总览

| 指标 | M2 实际值 | M3 目标 | 差距 | 优先级 | 预估工时 |
|------|-----------|---------|------|--------|----------|
| `structured_log_coverage` | 63.7% | ≥ 70% | -6.3% | P1 | 8h |
| `exception_coverage` | 81.2% | ≥ 90% | -8.8% | P1 | 6h |
| `track_event_coverage` | 51.7% | ≥ 70% | -18.3% | P2 | 6h |
| `test_coverage` | 3.7% | ≥ 55% | -51.3% | P0 | 30h |
| `boundary_test_coverage` | 12.2% | ≥ 70% | -57.8% | P0 | 18h |
| `trace_coverage` | 91.8% | ≥ 70% | +21.8% | — | 0h（已超额） |
| **合计** | | | | | **68h** |

### 配置阈值变更（config.yaml）

```yaml
visibility_thresholds:
  runtime:
    structured_log_coverage: 70      # 55 → 70
    trace_coverage: 70              # 16 → 70（实测 91.8%，远超目标）
  verification:
    test_coverage: 55               # 0 → 55
    boundary_test_coverage: 70      # 12 → 70
    exception_coverage: 90          # 80 → 90
  business:
    track_event_coverage: 70        # 50 → 70
```

---

## 二、任务分解

### 任务 A：结构化日志补齐（SL-011~015，预估 8h）

**目标**: structured_log_coverage 63.7% → 70%（转换剩余 ~200 处 logger 调用）

**当前未转换最多的文件**:

| 任务ID | 文件 | 未转换数 | 优先级 | 备注 |
|--------|------|----------|--------|------|
| SL-011 | agent/web/search.py | 53 | P1 | 核心业务模块 |
| SL-012 | agent/tool_calling.py | 37 | P1 | 核心业务模块 |
| SL-013 | agent/digital_life.py | 28 | P1 | 核心业务模块 |
| SL-014 | agent/network/config_manager.py | 19 | P2 | 网络配置模块 |
| SL-015 | agent/scheduling.py | 16 | P2 | 调度模块 |
| — | agent/diff_tools.py | 15 | P2 | 工具模块 |
| — | agent/monitoring/prometheus.py | 13 | P2 | 监控模块 |
| — | agent/cognitive/failure_analysis.py | 12 | P2 | 认知模块 |
| — | agent/memory/long_term_memory.py | 12 | P2 | 记忆模块 |
| — | agent/utils/serialization.py | 12 | P2 | 工具模块 |
| — | agent/tools/__init__.py | 11 | P3 | 工具入口 |
| — | agent/log_system/introspection.py | 11 | P3 | 日志系统 |
| — | agent/llm_response_cache.py | 11 | P3 | 缓存模块 |
| — | 其他小文件（≤10 处） | ~60 | P3 | 分批转换 |

**实施步骤**:
1. 运行 `python scripts/find_unconverted_logs.py` 获取最新列表
2. 优先转换业务代码（跳过 test_*.py 文件）
3. 使用 `python scripts/convert_logger_to_json.py <file>` 批量转换
4. 转换后运行 `python scripts/check_metrics.py` 验证覆盖率
5. 目标：转换 200 处 → 覆盖率提升至 ~70%

**验收标准**:
- `structured_log_coverage ≥ 70%`
- 无 SyntaxError
- 现有测试无回归

---

### 任务 B：异常处理补齐（EH-001~005，预估 6h）

**目标**: exception_coverage 81.2% → 90%（为 ~30 个文件添加异常处理）

**当前无异常处理的非测试文件**（按行数排序）:

| 任务ID | 文件 | 行数 | 优先级 | 有 IO 操作 |
|--------|------|------|--------|-----------|
| EH-001 | agent/monitoring/metrics.py | 255 | P1 | 是（Prometheus） |
| EH-002 | agent/cognitive/loop.py | 253 | P1 | 否 |
| EH-003 | agent/skills_mgmt/exceptions.py | 157 | P1 | 否 |
| EH-004 | agent/memory/base.py | 127 | P1 | 是（文件 IO） |
| EH-005 | agent/p6/performance.py | 120 | P1 | 是（文件 IO） |
| — | agent/tools/pdf_tools.py | 101 | P2 | 是（文件 IO） |
| — | agent/orchestrator/response_builder.py | 82 | P2 | 否 |
| — | agent/orchestrator/message_handler.py | 82 | P2 | 否 |
| — | agent/performance_integration_guide.py | 109 | P3 | 否 |
| — | 其他小文件（≤80 行） | ~20 文件 | P3 | 分批处理 |

**实施步骤**:
1. 运行 `python scripts/find_no_exception.py` 获取最新列表
2. 排除 test_*.py 文件（测试不需要异常处理）
3. 优先处理有 IO 操作的文件（网络/文件/数据库）
4. 使用 `python scripts/add_exception_handling.py <file>` 添加 `_safe_call`
5. 或手动添加 try/except + 结构化日志
6. 验证：`exception_coverage ≥ 90%`

**模板**:
```python
try:
    result = operation()
except SpecificException as e:
    logger.error(json.dumps({
        "trace_id": _trace_id(),
        "module_name": "xxx",
        "action": "operation.failed",
        "error": f"{type(e).__name__}: {e}",
    }, ensure_ascii=False))
    raise  # 或返回降级值
```

**验收标准**:
- `exception_coverage ≥ 90%`
- 无 SyntaxError
- 现有测试无回归

---

### 任务 C：业务埋点补齐（TE-001~003，预估 6h）

**目标**: track_event_coverage 51.7% → 70%（为 14 个未埋点模块添加 trackEvent）

**当前未埋点模块**（14 个）:

| 任务ID | 模块 | 优先级 | 关键交互点 |
|--------|------|--------|-----------|
| TE-001 | agent/web/ | P1 | 搜索请求、结果返回 |
| TE-002 | agent/workflow_engine/ | P1 | 工作流执行、步骤完成 |
| TE-003 | agent/guardrails/ | P1 | 安全检查、拦截决策 |
| — | agent/health/ | P2 | 健康评估、报告生成 |
| — | agent/prompt_manager/ | P2 | 提示模板加载、部署 |
| — | agent/network/ | P2 | 网络配置、连接管理 |
| — | agent/human_in_the_loop/ | P2 | 人工确认、超时处理 |
| — | agent/observability/ | P2 | 指标收集、报告生成 |
| — | agent/audit/ | P3 | 审计日志记录 |
| — | agent/data/ | P3 | 数据处理 |
| — | agent/quality/ | P3 | 质量评估 |
| — | agent/utils/ | P3 | 工具函数 |
| — | agent/lazy_loader/ | P3 | 延迟加载 |
| — | agent/tests/ | P3 | 测试模块（低优先级） |

**实施步骤**:
1. 为每个未埋点模块创建 `observability.py`（参照 M2 模板）
2. 在关键用户交互点添加 `trackEvent()` 调用
3. 埋点命名遵循 `yunshu_<模块>_<动作>` 格式
4. 使用 `python scripts/add_track_event.py <module_dir>` 批量生成
5. 验证：`track_event_coverage ≥ 70%`

**埋点模板**:
```python
# agent/web/observability.py
from agent.monitoring.business_metrics import BusinessMetricsCollector

_metrics = BusinessMetricsCollector()

def trackEvent(event_name: str, payload: dict = None) -> None:
    """web 模块业务埋点"""
    try:
        _metrics.inc_counter(event_name, labels=payload or {})
    except Exception:
        pass  # 埋点失败不影响主流程

# 在 agent/web/search.py 中调用:
# trackEvent('yunshu_web_search', {'query_length': len(query), 'result_count': len(results)})
```

**验收标准**:
- `track_event_coverage ≥ 70%`
- 埋点命名规范检查通过
- 埋点不影响主流程性能（单次 <1ms）

---

### 任务 D：单元测试补齐（UT-001~010，预估 30h）

**目标**: test_coverage 3.7% → 55%（当前最大工作量）

**当前状态**:
- coverage.xml line-rate = 0.03676（3.7%）
- CI 中 full-project-tests 生成的 coverage.xml line-rate ≈ 19.7%
- 需要大幅补齐核心模块单元测试

**优先测试的模块**（按业务重要性排序）:

| 任务ID | 模块 | 当前覆盖率 | 目标覆盖率 | 预估工时 |
|--------|------|-----------|-----------|----------|
| UT-001 | agent/orchestrator/ | ~0% | 70% | 4h |
| UT-002 | agent/tool_calling.py | ~0% | 70% | 3h |
| UT-003 | agent/memory/ | ~0% | 70% | 3h |
| UT-004 | agent/tools/ (file_tools, web/search) | ~0% | 65% | 4h |
| UT-005 | agent/cognitive/ | ~0% | 60% | 3h |
| UT-006 | agent/server_routes/ | ~0% | 60% | 3h |
| UT-007 | agent/skills_mgmt/ | ~80% | 85% | 1h |
| UT-008 | agent/workflow_learning/ | ~80% | 85% | 1h |
| UT-009 | agent/monitoring/ | ~0% | 60% | 4h |
| UT-010 | agent/model_router/ | ~0% | 65% | 2h |
| — | 其他模块（extensions/log_system/caching 等） | ~0% | 50% | 2h |

**实施步骤**:
1. 运行 `pytest --cov=agent --cov-report=xml` 获取当前覆盖率基线
2. 分析 coverage.xml 找出未覆盖的代码路径
3. 按优先级为每个模块编写单元测试
4. 每完成一个模块，运行测试验证覆盖率提升
5. 目标：总体 line-rate ≥ 55%

**测试编写规范**:
- 使用 pytest 框架
- 测试文件命名：`test_<module_name>.py`
- 测试类命名：`Test<ClassName>`
- 测试方法命名：`test_<action>_<condition>`
- 必须覆盖：正常流程 + 边界条件 + 异常路径
- 使用 mock 隔离外部依赖（网络/文件/数据库）

**验收标准**:
- `test_coverage ≥ 55%`（coverage.xml line-rate）
- 单元测试通过率 ≥ 95%
- 测试执行时间 ≤ 30 分钟
- 无 P0 级别缺陷

---

### 任务 E：边界测试补齐（BT-001~005，预估 18h）

**目标**: boundary_test_coverage 12.2% → 70%（新增 ~2700 个边界测试用例）

**当前状态**:
- 总测试数：3873
- 边界测试数：471
- 覆盖率：12.2%
- 目标：70%（需 ~2711 个边界测试）

**分批计划**:

| 批次 | 任务ID | 范围 | 新增用例数 | 累计覆盖率 | 预估工时 |
|------|--------|------|-----------|-----------|----------|
| 1 | BT-001 | 核心模块边界（orchestrator/tool_calling/memory） | 600 | ~25% | 4h |
| 2 | BT-002 | 工具模块边界（file_tools/web/search/tools） | 600 | ~38% | 4h |
| 3 | BT-003 | 路由模块边界（server_routes/） | 500 | ~48% | 3h |
| 4 | BT-004 | 认知/监控模块边界（cognitive/monitoring） | 500 | ~58% | 3h |
| 5 | BT-005 | 扩展/其他模块边界 | 511 | ~70% | 4h |

**边界测试分类**:

| 类型 | 说明 | 示例 |
|------|------|------|
| 空值边界 | None / 空字符串 / 空列表 | `test_search_empty_query()` |
| 极值边界 | 最大值 / 最小值 / 超限值 | `test_tool_timeout_zero()` |
| 类型边界 | 错误类型输入 | `test_process_string_input_as_int()` |
| 并发边界 | 多线程 / 竞态条件 | `test_concurrent_memory_write()` |
| 资源边界 | 内存不足 / 磁盘满 / 网络断开 | `test_file_read_disk_full()` |
| 权限边界 | 无权限 / 越权访问 | `test_api_call_without_auth()` |
| 编码边界 | UTF-8 / GBK / 特殊字符 | `test_filename_with_unicode()` |

**实施步骤**:
1. 运行 `python scripts/check_boundary_coverage.py --json-only` 获取当前状态
2. 分析各模块的边界测试缺口
3. 按批次编写边界测试用例
4. 每批次完成后运行验证
5. 目标：`boundary_test_coverage ≥ 70%`

**验收标准**:
- `boundary_test_coverage ≥ 70%`
- 边界测试通过率 ≥ 90%
- 覆盖核心模块的所有边界类型

---

## 三、执行顺序

```
阶段 1（P1，预估 20h）          阶段 2（P0，预估 48h）           最终验证
┌─────────────────────┐      ┌─────────────────────┐      ┌─────────────────────┐
│ SL-011~015          │      │ UT-001~010          │      │ 全量指标验证        │
│ 结构化日志 63.7→70% │      │ 单元测试 3.7→55%    │      │ visibility_report   │
├─────────────────────┤      ├─────────────────────┤      │ 所有指标达标        │
│ EH-001~005          │      │ BT-001~005          │      │ 测试无回归          │
│ 异常处理 81.2→90%   │ ──→  │ 边界测试 12.2→70%   │ ──→  │ Git 提交推送        │
├─────────────────────┤      ├─────────────────────┤      └─────────────────────┘
│ TE-001~003          │      │ 最终验证             │
│ 业务埋点 51.7→70%   │      │ 全量指标达标         │
└─────────────────────┘      └─────────────────────┘
```

### 阶段 1：可见性补齐（预估 20h）

1. **SL-011~015**: 结构化日志转换（8h）
2. **EH-001~005**: 异常处理补齐（6h）
3. **TE-001~003**: 业务埋点补齐（6h）

### 阶段 2：测试覆盖补齐（预估 48h）

4. **UT-001~010**: 单元测试补齐（30h）
5. **BT-001~005**: 边界测试补齐（18h）

### 阶段 3：最终验证（预估 2h）

6. 运行全量验证
7. 更新 config.yaml 阈值
8. Git 提交推送

---

## 四、风险评估

| 风险项 | 影响 | 概率 | 缓解措施 |
|--------|------|------|---------|
| test_coverage 工作量巨大（30h） | 阶段 2 可能延期 | 高 | 分批执行，每批 5h，可并行 |
| 边界测试 2700 用例工作量 | 阶段 2 可能延期 | 高 | 优先核心模块，批次 1+2 完成即可达 ~38% |
| CI 环境 coverage.xml 差异 | 本地与 CI line-rate 不一致 | 中 | 统一 CI 配置，确保 coverage.xml 正确生成 |
| 日志转换引入格式错误 | 核心逻辑日志丢失 | 中 | 每个模块转换后单独运行测试验证 |
| 埋点影响主流程性能 | 系统响应变慢 | 低 | 埋点使用 try/except 包裹，单次 <1ms |
| 测试 mock 不充分导致误报 | 测试通过但实际有 bug | 中 | 使用 integration test 补充验证 |

---

## 五、验收标准

### 指标达标

| 指标 | 目标 | 验证方法 |
|------|------|----------|
| `structured_log_coverage` | ≥ 70% | `python scripts/visibility_report.py` |
| `trace_coverage` | ≥ 70% | 同上（已超额 91.8%） |
| `test_coverage` | ≥ 55% | coverage.xml line-rate |
| `boundary_test_coverage` | ≥ 70% | `python scripts/check_boundary_coverage.py` |
| `exception_coverage` | ≥ 90% | `python scripts/visibility_report.py` |
| `track_event_coverage` | ≥ 70% | 同上 |
| `overall_status` | pass | 同上 |
| `violations_count` | 0 | 同上 |

### 质量标准

- 单元测试通过率 ≥ 95%
- 集成测试通过率 ≥ 90%
- P0 测试 100% 通过
- 测试执行时间 ≤ 30 分钟
- 新增代码覆盖率 ≥ 80%
- 安全扫描高危漏洞 = 0
- 无功能缺陷、无性能瓶颈

### 交付物

- 所有代码变更已提交并推送到 `phase2-visibility-convergence` 分支
- 提交消息：`feat(observability): M3 里程碑 全量可见性指标达标`
- 更新 config.yaml 阈值至 M3 目标
- 生成 M3 Release Notes 文档
- visibility_report.json 最终快照

---

## 六、参考资源

- 执行计划：`docs/observability/phase2_execution_plan.md`
- M2 Release Notes：`docs/observability/M2_release_notes.md`
- M2 里程碑报告：`docs/observability/M2_milestone_report.md`
- 指标采集脚本：`scripts/visibility_report.py`
- 边界测试检查：`scripts/check_boundary_coverage.py`
- 转换工具：`scripts/convert_logger_to_json.py` / `scripts/add_exception_handling.py` / `scripts/add_track_event.py`
- 可观测性约束：`project_memory.md`（埋点命名规范、结构化日志规范）
