# 下一迭代行动计划：边界治理 Phase 2 — 配置化 + 静态防护 + 覆盖率提升

> **基于文档**：[iteration_summary_boundary_overflow_fix.md](file:///c:/Users/Administrator/agent/docs/observability/iteration_summary_boundary_overflow_fix.md) 第七节"后续建议"
> **迭代周期**：1~2 周（预计 5 个工作日）
> **分支**：`phase2-visibility-convergence`（或新建 `boundary-governance-phase2`）
> **前置条件**：Phase 1 已完成（4 个溢出方法修复，47/47 场景覆盖）
> **文档版本**：v1.0.0
> **生成时间**：2026-07-02 01:45:00

---

## 一、迭代目标

| # | 目标 | 度量指标 | 对应总结文档建议 |
|---|------|----------|------------------|
| G1 | 时间窗口上限配置化 | 3 个模块的 `36500` 硬编码全部改为 `Config.get()` | 中期 7.2 第 1 条 |
| G2 | CI 静态防护建立 | `check_timedelta_overflow.py` 脚本接入 CI | 中期 7.2 第 3 条 |
| G3 | boundary_config 场景增强 | 所有 timedelta 模块声明 `overflow` 必需场景 | 中期 7.2 第 2 条 |
| G4 | 边界覆盖率阶段提升 | 24.4% → 30%（+5.6 个百分点） | 长期 7.3 第 1 条 |
| G5 | 提交原子化清理 | `test_p0_security_fix.py` 独立提交 | 短期 7.1 第 2 条 |
| G6 | 混沌测试启动 | `test_overflow_chaos.py` 集成到回归脚本 | 长期 7.3 第 2 条 |

---

## 二、任务清单

### Task 1: MAX_ANALYZE_DAYS 配置化（G1）

| 属性 | 值 |
|------|-----|
| **优先级** | P1 |
| **预估工时** | 0.5 天 |
| **依赖** | 无 |
| **涉及文件** | `agent/monitoring/observability_config.py`, `agent/data_analytics.py`, `agent/monitoring/replay_storage.py`, `agent/quality/defect_tracker.py` |

**具体步骤**：

1. 在 `observability_config.py` 的 `ValidationRule` 列表中新增：
   ```python
   ValidationRule(
       path="time_window.max_analyze_days",
       validator=_range_validator(1, 36500),
       default=36500,
       error_message="max_analyze_days 必须为 1~36500 之间的整数",
       description="时间窗口分析上限天数（100 年）",
   )
   ```

2. 在 `config.yaml` 示例文件中新增 `time_window` 段：
   ```yaml
   time_window:
     max_analyze_days: 36500  # 100 年上限
   ```

3. 修改 3 个模块，将硬编码 `36500` / `MAX_ANALYZE_DAYS` 改为：
   ```python
   from agent.monitoring.observability_config import get_config
   max_days = get_config("time_window.max_analyze_days", default=36500)
   ```

4. 更新现有测试中的 `MAX_ANALYZE_DAYS` 引用，改为从 Config mock 读取

**验收标准**：
- [ ] `grep -rn "36500" agent/` 输出中不包含硬编码（仅在 config 默认值中出现）
- [ ] 所有现有边界测试通过（23 + 122 = 145 个）
- [ ] 修改 Config 值后，3 个模块的上限行为同步变化

---

### Task 2: CI 静态分析规则 — timedelta 溢出自动检测（G2）

| 属性 | 值 |
|------|-----|
| **优先级** | P1 |
| **预估工时** | 1 天 |
| **依赖** | 无（与 Task 1 可并行） |
| **涉及文件** | `scripts/check_timedelta_overflow.py`（新增）, `.github/workflows/boundary-guard.yml`（新增） |

**具体步骤**：

1. 创建 `scripts/check_timedelta_overflow.py`：
   - 使用 Python `ast` 模块解析 `agent/` 下所有 `.py` 文件
   - 识别 `timedelta(days=<expr>)` 调用模式
   - 对 `<expr>` 进行 AST 分析：
     - 如果是 `Name` 节点（变量引用）→ 追溯赋值来源
     - 如果来源是函数参数 → 标记为 **高风险**（WARNING）
     - 如果来源是字面量常量 → 标记为 **无风险**（INFO）
   - 输出 JSON 报告 + 控制台摘要

2. 脚本输出格式：
   ```json
   {
     "trace_id": "...",
     "module_name": "timedelta_overflow_scanner",
     "action": "scan.complete",
     "total_calls": 16,
     "high_risk": 0,
     "no_risk": 16,
     "details": [
       {"file": "...", "line": 123, "pattern": "timedelta(days=days)", "risk": "high", "param_source": "arg"}
     ]
   }
   ```

3. 创建 `.github/workflows/boundary-guard.yml`：
   - 触发条件：PR 到 `phase2-*` 或 `master` 分支
   - 步骤：`python scripts/check_timedelta_overflow.py --fail-on-high-risk`
   - 高风险数 > 0 时 CI 失败

4. 在 `boundary_config.yaml` 的 `ci_policy` 中新增 `static_analysis: true`

**验收标准**：
- [ ] 脚本正确识别当前 16 处 timedelta 调用，0 个高风险（Phase 1 已修复）
- [ ] 故意引入 `timedelta(days=user_input)` 后，脚本检测到并报 WARNING
- [ ] CI workflow 文件语法正确，可被 GitHub Actions 解析
- [ ] 脚本执行耗时 < 5 秒

---

### Task 3: boundary_config.yaml 场景增强（G3）

| 属性 | 值 |
|------|-----|
| **优先级** | P2 |
| **预估工时** | 0.5 天 |
| **依赖** | Task 1（配置化后再增强场景要求） |
| **涉及文件** | `tests/boundary_config.yaml`, `scripts/check_boundary_coverage.py` |

**具体步骤**：

1. 在 `boundary_config.yaml` 中为以下模块的 `required_scenes` 新增 `overflow`：
   - `core` — 已有 empty/timeout/invalid/null，新增 overflow
   - `monitoring` — 已有 timeout/empty，新增 overflow
   - `observability` — 已有 empty/invalid，新增 overflow
   - `tools` — 已有 timeout/invalid/empty，新增 overflow
   - `data_analytics`（如未声明则新增模块条目）
   - `quality`（如未声明则新增模块条目）

2. 更新 `check_boundary_coverage.py` 扫描器：
   - 确保 `overflow` 关键词组已包含 `overflow/超长/超大/超出限制`（已有，确认即可）
   - 新增场景覆盖率统计中包含 `overflow` 维度

3. 为新增的 `overflow` 必需场景补充测试用例（如果现有测试未覆盖）：
   - `core`: 添加 `test_overflow_*` 测试
   - `monitoring`: 复用 `test_replay_storage.py` 中的溢出测试
   - `observability`: 添加边界覆盖率脚本自身的溢出测试
   - `tools`: 添加 `test_overflow_*` 测试（如 tool_timeout 溢出）
   - `data_analytics`: 已有 `TestOverflowFixBoundary`（7 个），满足要求
   - `quality`: 新增 `test_overflow_*` 测试

**验收标准**：
- [ ] `python scripts/check_boundary_coverage.py` 报告显示所有新增 `overflow` 场景已覆盖
- [ ] 场景覆盖率保持 100%（场景总数从 47 增加到 ~52）
- [ ] 无新的 `blocked_modules`

---

### Task 4: test_p0_security_fix.py 提交原子化清理（G5）

| 属性 | 值 |
|------|-----|
| **优先级** | P3 |
| **预估工时** | 0.5 天 |
| **依赖** | 无 |
| **涉及文件** | Git 历史操作（非代码修改） |

**具体步骤**：

1. 审查 `e174e276` 提交中 `test_p0_security_fix.py` 的 245 行内容：
   - 确认内容与溢出修复无关
   - 确认内容是独立的 P0 安全修复回归测试

2. 评估两种处理方案：
   - **方案 A（推荐）**：保持现状，在提交消息中补充说明。理由：`git rebase -i` 重写历史会影响已推送的远程分支，风险较高
   - **方案 B**：新建提交，在 `CHANGELOG.md` 中记录 `test_p0_security_fix.py` 的归属

3. 如果选择方案 A：
   - 在 `iteration_summary_boundary_overflow_fix.md` 的"残留风险"中已记录（P4）
   - 无需额外操作

4. 如果选择方案 B：
   - 创建 `docs/changelog/p0_security_attribution.md`，说明 `test_p0_security_fix.py` 的提交归属
   - 后续 P0 安全修复提交应与边界修复提交分离

**验收标准**：
- [ ] 决策记录在文档中（选择方案 A 或 B）
- [ ] 如选方案 B，changelog 文档已创建
- [ ] 团队成员清楚 `test_p0_security_fix.py` 的来源和归属

---

### Task 5: 边界覆盖率提升 — 24.4% → 30%（G4）

| 属性 | 值 |
|------|-----|
| **优先级** | P2 |
| **预估工时** | 2 天 |
| **依赖** | 无（与 Task 1~3 可并行） |
| **涉及文件** | `tests/boundary/test_*_boundary.py`（多个新增/增强） |

**具体步骤**：

1. **瓶颈分析**（0.5 天）：
   - 运行 `python scripts/check_boundary_coverage.py`，导出 JSON 报告
   - 按模块排序，找出边界测试数/总测试数比值最低的 10 个模块
   - 优先目标：`extensions`, `config`, `network`, `scheduling`, `search_aggregator`

2. **测试编写**（1.5 天）：
   - 为每个目标模块新增 `test_<module>_boundary.py`
   - 每个文件至少覆盖 `empty/invalid/timeout` 三个场景
   - 每个场景至少 3 个测试用例
   - 预计新增 5 个文件 × 9 个用例 = 45 个测试

3. **覆盖率验证**：
   - 运行 `python -m pytest tests/boundary/ -v`
   - 运行 `python scripts/check_boundary_coverage.py`
   - 确认覆盖率从 24.4% 提升至 ≥ 30%

**验收标准**：
- [ ] 新增 ≥ 45 个边界测试用例
- [ ] 边界覆盖率 ≥ 30%（边界测试数 ≥ 2079）
- [ ] 场景覆盖率保持 100%
- [ ] 所有新增测试通过

---

### Task 6: 混沌测试启动 — 溢出场景注入（G6）

| 属性 | 值 |
|------|-----|
| **优先级** | P3 |
| **预估工时** | 1 天 |
| **依赖** | Task 1（需要配置化后的上限值作为测试基准） |
| **涉及文件** | `tests/chaos/test_overflow_chaos.py`（新增）, `run_chaos_regression.ps1`（增强） |

**具体步骤**：

1. 创建 `tests/chaos/test_overflow_chaos.py`：
   ```python
   class TestOverflowChaos:
       """混沌测试：向所有已修复方法注入溢出参数"""

       @pytest.mark.chaos
       def test_chaos_data_analytics_overflow(self):
           """注入 days=999999 到 analyze_event_trends"""
           # 验证 ValueError 抛出，而非 OverflowError

       @pytest.mark.chaos
       def test_chaos_replay_storage_overflow(self):
           """注入 days=999999 到 cleanup_old_records"""

       @pytest.mark.chaos
       def test_chaos_defect_tracker_overflow(self):
           """注入 days=999999 到 calculate_escape_rate + get_escape_rate_trend"""

       @pytest.mark.chaos
       def test_chaos_random_mutation(self):
           """随机变异 days 参数（fuzzing 雏形）"""
           # 生成 -100 ~ 1000000 的随机值，验证不抛出 OverflowError
   ```

2. 增强 `run_chaos_regression.ps1`：
   - 新增 `tests/chaos/test_overflow_chaos.py` 到测试路径
   - 在回归报告中增加溢出测试通过率统计

3. 在 `tests/chaos/` 中添加 `conftest.py`（如不存在），配置 `chaos` marker

**验收标准**：
- [ ] 混沌测试文件包含 ≥ 4 个测试用例
- [ ] `run_chaos_regression.ps1` 执行通过，溢出测试 100% pass
- [ ] 混沌测试执行耗时 < 10 秒

---

## 三、任务依赖与排期

```
Day 1:  Task 1 (配置化)     ─┐
        Task 2 (静态分析)    ─┤── 并行
        Task 4 (提交清理)    ─┘

Day 2:  Task 3 (场景增强)    ← 依赖 Task 1
        Task 5 (覆盖率分析)  ── 独立启动

Day 3:  Task 5 (测试编写)    ── 继续
        Task 6 (混沌测试)    ← 依赖 Task 1

Day 4:  Task 5 (测试编写)    ── 继续
        集成测试 + 回归验证

Day 5:  全量回归测试
        文档更新
        PR 创建 + Code Review
```

**甘特图（文本版）**：

| 任务 | Day1 | Day2 | Day3 | Day4 | Day5 |
|------|------|------|------|------|------|
| Task 1 配置化 | ██ | | | | |
| Task 2 静态分析 | ██ | | | | |
| Task 3 场景增强 | | ██ | | | |
| Task 4 提交清理 | ██ | | | | |
| Task 5 覆盖率提升 | | ██ | ██ | ██ | |
| Task 6 混沌测试 | | | ██ | | |
| 集成验证 | | | | ██ | ██ |
| PR Review | | | | | ██ |

---

## 四、风险与对策

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| Task 1 配置化影响现有测试 mock | 中 | 中 | 在 Task 1 中预留 0.5 小时更新测试 mock |
| Task 2 AST 分析复杂度超预期 | 中 | 中 | 先实现简单正则匹配版本，后续迭代增强为 AST |
| Task 5 覆盖率提升工时不足 | 高 | 低 | 优先补充 P1 模块，P2 模块可延后 |
| 外部进程频繁推送导致分支冲突 | 高 | 中 | 每天开始前 `git fetch && git rebase` |
| CI 环境与本地环境差异 | 低 | 中 | 在 `.github/workflows/` 中固定 Python 版本和依赖 |

---

## 五、验收检查清单

迭代结束前，以下检查项必须全部通过：

- [ ] `grep -rn "36500" agent/` 仅在 config 默认值中出现（Task 1）
- [ ] `python scripts/check_timedelta_overflow.py` 报告 0 个高风险（Task 2）
- [ ] `python scripts/check_boundary_coverage.py` 场景覆盖率 100%，场景总数 ≥ 52（Task 3）
- [ ] `test_p0_security_fix.py` 归属决策已记录（Task 4）
- [ ] 边界覆盖率 ≥ 30%（Task 5）
- [ ] `run_chaos_regression.ps1` 全部通过，含溢出混沌测试（Task 6）
- [ ] 全量测试通过率 ≥ 95%
- [ ] PR 创建并通过 Code Review

---

## 六、附录：相关资源

### 6.1 前置文档

- [迭代技术总结](file:///c:/Users/Administrator/agent/docs/observability/iteration_summary_boundary_overflow_fix.md) — Phase 1 完整记录
- [边界覆盖完整报告](file:///c:/Users/Administrator/agent/docs/observability/boundary_coverage_full_report.md) — 当前覆盖率基线
- [boundary_config.yaml](file:///c:/Users/Administrator/agent/tests/boundary_config.yaml) — 场景声明配置

### 6.2 关键代码位置

- [ValidationRule 定义](file:///c:/Users/Administrator/agent/agent/monitoring/observability_config.py#L47) — Task 1 复用的验证架构
- [_range_validator](file:///c:/Users/Administrator/agent/agent/monitoring/observability_config.py#L65) — Task 1 使用的范围验证器
- [data_analytics.py MAX_ANALYZE_DAYS](file:///c:/Users/Administrator/agent/agent/data_analytics.py#L21) — Task 1 需要配置化的常量
- [check_boundary_coverage.py](file:///c:/Users/Administrator/agent/scripts/check_boundary_coverage.py) — Task 3 需要增强的扫描器
- [run_chaos_regression.ps1](file:///c:/Users/Administrator/agent/run_chaos_regression.ps1) — Task 6 需要增强的回归脚本

### 6.3 度量基线（Phase 1 结束时）

| 指标 | 当前值 | 目标值 |
|------|--------|--------|
| 总测试数 | 6930 | ≥ 7000 |
| 边界测试数 | 1644 | ≥ 2079 (30%) |
| 场景覆盖率 | 47/47 (100%) | ≥ 52/52 (100%) |
| 高风险 timedelta | 0 | 0 |
| 阻断模块数 | 0 | 0 |
