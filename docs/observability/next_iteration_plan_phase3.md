# 边界治理 Phase 3 迭代行动计划 — P1 收尾 + 静态防护扩展 + 混沌测试

> **计划周期**：2026-07-03 ~ 2026-07-05（预计 2.5 个工作日）
> **分支**：`phase2-visibility-convergence`（或新建 `phase3-boundary-hardening`）
> **前置条件**：Phase 2 本轮已完成（Task 1~3 + max_retries 配置化）
> **文档版本**：v1.0.0
> **生成时间**：2026-07-02

---

## 一、迭代目标

### 1.1 核心目标

1. **P1 清单收尾**：完成 http_client.py 剩余 3 项配置化（timeout / connect_timeout / pool_size）
2. **静态防护扩展**：将 AST 静态分析从 timedelta 扩展到重试次数 + 超时值检测
3. **混沌测试注入**：针对配置化项注入异常值，验证降级行为
4. **P2 启动**：开始缓存容量与调度器常量的配置化

### 1.2 量化指标

| 指标 | Phase 2 结束 | Phase 3 目标 | 变化 |
|------|-------------|-------------|------|
| 配置化边界项 | 4 | **8**（+3 HTTP + 1 P2） | +4 |
| CI 静态分析规则 | 1（timedelta） | **3**（+重试 +超时） | +2 |
| 混沌测试场景 | 0 | **6** | +6 |
| 配置项总数 | 19 | **23** | +4 |
| P1 完成率 | 60%（6/10 含 max_retries） | **100%** | +40% |

---

## 二、任务分解

### Task 1: HTTP 超时与连接池配置化（P1 收尾）

**预计工时**：1.5h
**依赖**：无
**优先级**：P0

**改造范围**（http_client.py 3 个硬编码项）：

| # | 硬编码项 | 当前值 | 配置路径 | 范围 |
|---|----------|--------|----------|------|
| 1 | `DEFAULT_TIMEOUT` | 30 | `http.timeout_sec` | 1-300 |
| 2 | `DEFAULT_CONNECT_TIMEOUT` | 10 | `http.connect_timeout_sec` | 1-60 |
| 3 | `DEFAULT_POOL_SIZE` | 20 | `http.pool_size` | 1-100 |

**实施步骤**：

1. 在 `observability_config.py` 新增 3 个 ValidationRule：
   ```python
   ValidationRule(path="http.timeout_sec", validator=_range_validator(1, 300), default=30, ...)
   ValidationRule(path="http.connect_timeout_sec", validator=_range_validator(1, 60), default=10, ...)
   ValidationRule(path="http.pool_size", validator=_range_validator(1, 100), default=20, ...)
   ```
2. 新增 3 个便捷函数：`get_http_timeout()` / `get_http_connect_timeout()` / `get_http_pool_size()`
3. 修改 `http_client.py`：
   - `DEFAULT_TIMEOUT` / `DEFAULT_CONNECT_TIMEOUT` / `DEFAULT_POOL_SIZE` 保留为向后兼容别名
   - `_build_session()` 中 fallback 改为从 Config 读取
   - `request()` 方法中的 timeout 参数 fallback 也改为从 Config 读取
4. 编写验证脚本测试配置化与热加载
5. 运行 `test_http_client.py` 确认无回归

**验收标准**：
- [ ] 3 个配置项默认值正确（30/10/20）
- [ ] 修改 Config 值后 HttpClient 行为变化
- [ ] 显式传参不受默认值影响
- [ ] `test_http_client.py` 全部通过

---

### Task 2: 静态分析扩展 — 重试次数与超时值检测

**预计工时**：2h
**依赖**：无
**优先级**：P0

**目标**：在 `scripts/check_timedelta_overflow.py` 基础上，扩展检测重试次数和超时值的硬编码。

**实施步骤**：

1. 新建 `scripts/check_hardcoded_boundaries.py`（或扩展现有脚本）：
   - 检测 `max_retries = N` / `MAX_RETRIES = N` 硬编码（排除已配置化的模块）
   - 检测 `timeout = N` / `DEFAULT_TIMEOUT = N` 硬编码
   - 检测 `pool_size = N` / `max_workers = N` 硬编码
   - 风险分级：
     - `high`：硬编码且未从 Config 读取
     - `medium`：有 DEFAULT_* 常量但未配置化
     - `low`：已配置化或测试文件中的硬编码

2. 更新 `.github/workflows/boundary-guard.yml`：
   - 增加重试次数与超时值的扫描步骤
   - 设置基线策略（当前基线基于扫描结果确定）

3. 生成基线扫描报告，确定初始基线值

**验收标准**：
- [ ] 脚本能正确检测重试次数/超时值/容量限制 3 类硬编码
- [ ] 已配置化的模块（error_handler/reflection/http_client）不被标记为 high
- [ ] CI workflow 集成完成
- [ ] 基线值合理（不阻断现有代码）

---

### Task 3: 混沌测试 — 配置异常值注入

**预计工时**：2h
**依赖**：Task 1 完成
**优先级**：P1

**目标**：针对配置化项注入异常值，验证系统降级行为。

**实施步骤**：

1. 新建 `tests/chaos/test_config_boundary_chaos.py`：
   - 测试 `retry.default_max_retries = 0`：验证不重试直接失败
   - 测试 `retry.default_max_retries = 20`：验证最大重试不超时
   - 测试 `http.max_retries = 0`：验证 HTTP 不重试
   - 测试 `http.timeout_sec = 1`：验证短超时触发超时异常
   - 测试 `cognitive.reflection_max_retries = 1`：验证反思仅重试一次
   - 测试 `time_window.max_analyze_days = 1`：验证短时间窗口正常工作

2. 测试配置恢复：每个测试后恢复默认值，避免影响其他测试

3. 使用 `@pytest.fixture` 管理配置生命周期

**验收标准**：
- [ ] 6 个混沌测试场景全部通过
- [ ] 异常配置值不会导致系统崩溃（优雅降级）
- [ ] 配置恢复后行为正常
- [ ] 测试隔离性良好（无副作用）

---

### Task 4: P2 启动 — 缓存容量配置化

**预计工时**：2h
**依赖**：无
**优先级**：P1

**改造范围**（3 个缓存模块）：

| # | 文件 | 硬编码项 | 当前值 | 配置路径 |
|---|------|----------|--------|----------|
| 1 | `multi_level_cache.py` | `l1_max_size` | 1000 | `cache.l1_max_size` |
| 2 | `tracing_cache.py` | `max_size` (×2) | 4096/2048 | `tracing.context_cache_size` / `tracing.span_cache_size` |
| 3 | `short_term_memory.py` | `max_size` | 100 | `memory.stm_max_size` |

**实施步骤**：

1. 在 `observability_config.py` 新增 4 个 ValidationRule
2. 新增 4 个便捷函数
3. 修改 3 个模块从 Config 读取
4. 运行相关测试确认无回归

**验收标准**：
- [ ] 4 个配置项默认值正确
- [ ] 修改 Config 值后缓存容量变化
- [ ] 相关模块测试全部通过

---

### Task 5: P2 续 — 调度器与监控常量配置化

**预计工时**：2.5h
**依赖**：无
**优先级**：P2

**改造范围**：

| # | 文件 | 硬编码项 | 当前值 | 配置路径 |
|---|------|----------|--------|----------|
| 1 | `task_scheduler.py` | `DEFAULT_CHECK_INTERVAL` | 10 | `scheduler.check_interval_sec` |
| 2 | `task_scheduler.py` | `MAX_HISTORY_LINES` | 1000 | `scheduler.max_history_lines` |
| 3 | `task_scheduler.py` | `MAX_HEARTBEAT_HISTORY` | 1440 | `scheduler.max_heartbeat_history` |
| 4 | `llm_monitor.py` | `MAX_RECORDS` | 500 | `llm_monitor.max_records` |
| 5 | `loki.py` | `timeout` (×2) | 10/30 | `loki.timeout_sec` |
| 6 | `alert_notifier.py` | `timeout` | 30 | `alert.timeout_sec` |

**实施步骤**：参照 Task 1 模式

**验收标准**：
- [ ] 6 个配置项默认值正确
- [ ] 相关模块测试全部通过

---

### Task 6: 提交清理与文档更新

**预计工时**：0.5h
**依赖**：Task 1~5 完成
**优先级**：P0

**实施步骤**：

1. 按 Task 分组原子性提交（每个 Task 一个提交）
2. 更新 `hardcoded_boundary_scan_report.md` 标记已完成项
3. 更新 `iteration_summary_boundary_governance_phase2.md` 补充 Phase 3 完成项
4. 推送到远程
5. 创建 PR 合并到 master

**验收标准**：
- [ ] 每个 Task 有独立提交
- [ ] 提交消息符合 `feat(boundary):` / `feat(ci):` / `test(chaos):` 规范
- [ ] 文档更新完整
- [ ] PR 创建成功

---

## 三、风险与缓解

### 3.1 配置化改造风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 配置读取性能开销 | 高频调用路径性能下降 | 便捷函数已有 try/except 兜底，单次耗时 < 0.1ms |
| 配置热加载竞态 | 并发读写导致状态不一致 | ObservabilityConfig 使用 RLock 保护所有读写 |
| 向后兼容性破坏 | 现有代码引用常量失败 | 保留所有 DEFAULT_* / MAX_* 常量作为别名 |

### 3.2 静态分析误报风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 已配置化模块被误报 | CI 噪音 | 扫描脚本维护白名单，已配置化模块标记为 low |
| 测试文件硬编码被误报 | CI 阻断 | 扫描脚本默认排除 tests/ 目录 |
| 基线值设置不当 | CI 频繁阻断或失效 | 初始基线基于当前扫描结果，后续定期更新 |

### 3.3 混沌测试风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 测试间配置污染 | 后续测试异常 | 使用 fixture 确保每个测试后恢复默认值 |
| 异常配置导致系统崩溃 | 测试环境不稳定 | 配置系统有范围校验，超出范围自动修复到默认值 |

---

## 四、时间安排

| 日期 | 任务 | 预计工时 | 产出 |
|------|------|----------|------|
| Day 1 上午 | Task 1: HTTP 配置化 | 1.5h | 3 个配置项 + http_client 改造 |
| Day 1 下午 | Task 2: 静态分析扩展 | 2h | check_hardcoded_boundaries.py + CI 更新 |
| Day 1 晚 | Task 3: 混沌测试 | 2h | 6 个混沌测试场景 |
| Day 2 上午 | Task 4: 缓存容量配置化 | 2h | 4 个配置项 + 3 模块改造 |
| Day 2 下午 | Task 5: 调度器与监控配置化 | 2.5h | 6 个配置项 + 4 模块改造 |
| Day 2 晚 | Task 6: 提交清理与文档 | 0.5h | 6 次提交 + PR |
| **合计** | — | **10.5h** | — |

---

## 五、验收清单

### 5.1 功能验收

- [x] P1 清单 100% 完成（10/10 项配置化）✅ Task 1 已提交 3a6b8b08
- [x] P2 缓存容量 4 项配置化 ✅ Task 4 已提交 41f069ef
- [x] P2 调度器常量 5 项配置化 ✅ Task 5 已提交 41f069ef（注：实际改造 task_scheduler.py 5 项，llm_monitor/loki/alert_notifier 列入第三批后续迭代）
- [ ] CI 静态分析覆盖 3 类模式（timedelta + 重试 + 超时）⏳ Task 2 进行中
- [x] 混沌测试场景通过 ✅ Task 3 已提交 65a625cc，17/17 passed

### 5.2 质量验收

- [ ] 所有相关单元测试通过（无回归）
- [ ] 配置化功能验证脚本通过
- [ ] 静态分析脚本基线合理
- [ ] 代码审查无 P0/P1 问题

### 5.3 文档验收

- [ ] 技术总结文档更新
- [ ] 硬编码扫描报告标记完成项
- [ ] 配置项速查表更新
- [ ] PR 描述完整

---

## 六、附录：配置项速查表（Phase 3 实际完成状态）

| 配置路径 | 默认值 | 范围 | 用途 | Phase | 状态 |
|----------|--------|------|------|-------|------|
| `time_window.max_analyze_days` | 36500 | 1-36500 | timedelta 上限 | 2 | ✅ |
| `retry.default_max_retries` | 3 | 0-20 | RetryPolicy 默认重试 | 2 | ✅ |
| `cognitive.reflection_max_retries` | 3 | 1-10 | 反思引擎重试 | 2 | ✅ |
| `http.max_retries` | 3 | 0-10 | HTTP 重试 | 2 | ✅ |
| `http.timeout_sec` | 30 | 1-300 | HTTP 超时 | 3 Task 1 | ✅ |
| `http.connect_timeout_sec` | 10 | 1-60 | HTTP 连接超时 | 3 Task 1 | ✅ |
| `http.pool_size` | 20 | 1-100 | HTTP 连接池 | 3 Task 1 | ✅ |
| `cache.l1_max_size` | 1000 | 100-10000 | L1 内存缓存容量 | 3 Task 4 | ✅ |
| `tracing_cache.context_max_size` | 4096 | 256-16384 | 追踪上下文缓存 | 3 Task 4 | ✅ |
| `tracing_cache.span_max_size` | 2048 | 128-8192 | Span 数据缓存 | 3 Task 4 | ✅ |
| `tracing_cache.span_pool_size` | 500 | 50-2000 | Span 对象池 | 3 Task 4 | ✅ |
| `scheduler.check_interval_sec` | 10 | 1-300 | 调度器 tick 检查间隔 | 3 Task 5 | ✅ |
| `scheduler.command_timeout_sec` | 300 | 10-3600 | 系统命令执行超时 | 3 Task 5 | ✅ |
| `scheduler.max_history_lines` | 1000 | 100-10000 | 执行历史最大行数 | 3 Task 5 | ✅ |
| `scheduler.heartbeat_interval_sec` | 60 | 10-600 | 心跳检测间隔 | 3 Task 5 | ✅ |
| `scheduler.max_heartbeat_history` | 1440 | 144-14400 | 心跳历史保留条数 | 3 Task 5 | ✅ |
| `llm_monitor.max_records` | 500 | — | LLM 监控环形缓冲 | 后续 | ⏳ |
| `loki.timeout_sec` | 10 | — | Loki 推送超时 | 后续 | ⏳ |
| `alert.timeout_sec` | 30 | — | 告警通知超时 | 后续 | ⏳ |

**Phase 3 实际完成 16/19 项配置化**（Task 1: 3 项 + Task 4: 4 项 + Task 5: 5 项 + 既有 4 项），剩余 3 项列入后续迭代。
