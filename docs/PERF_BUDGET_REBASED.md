# PERF_BUDGET_REBASED —— 云枢性能预算按实测重定（替代设计文档 §11.2 假设值）

> **任务**：TASK-S5-03（成本刹车与断食）步骤 4｜**依据**：v7.2 审计缺陷 **P8**（"性能预算按云枢实测重定"）
> 与 `docs/zh/CloudPivot_v7.2重构计划/RFC-宿主形态与范围.md`（§11.2 性能预算 → 参考（重定），
> 以云枢实测基线重定，不照搬 `<200MB / 1000 events/s` 假设值）
> **生成日期**：2026-09-11｜**基线 commit**：`master` / `15eae00d`
> **口径**：本文件中**只有"实测"栏的数字才可作为事实引用**；"未找到实测"栏的条目
> **一律不得当作已验证指标**（保持为假设，并给出复测方案）。

---

## 一、结论先行

1. **§11.2 的"内存 <200MB"假设被实测证伪**：加载重排模型（reranker）的可观测驻留内存
   实测 **1156.57 MB（onnx quantized）/ 1904.1 MB（PyTorch bge-reranker-v2-m3）**，
   是假设值的 **5.8× / 9.5×**。"<200MB"只能对**未加载重模型**的进程成立
   （RRF 降级路 65.4 MB、可观测性基准 18.35 MB）——因此该预算**必须按进程形态拆分**，
   否则它是一句会误导容量规划的话。
2. **"事件吞吐 ≤1000 events/s"（P7.2-22）实测达标**：本次实测 **1631.3 events/s**
   （1000 条批量，单条 613 µs），余量 1.63×。
3. **"路由 <100ms"实测大幅达标**：`ModelRouter.route()` **P99 0.0028 ms**（余量 >4 个数量级），
   该条目对当前实现而言是"没有约束力的预算"。
4. **RRF 5000 技能检索已接近容量边界**：实测 P99 **44.78 ms**（倒排索引 ON），
   距 50 ms 实时性线**仅余 10%**；关闭倒排索引则 **94.07 ms 超标**。
   这是**当前最需要关注的余量告急项**。
5. **三项仍无实测**（状态灯 / 首屏 / Watchdog）：本文件**不为其编造数字**，
   保持"未验证假设"并给出复测方案（§五）。
6. **【S6-01 补测，2026-09-12】状态灯与首屏已实测**（§八）：
   - **状态灯**（状态变更 → 首帧重绘）：真实浏览器 headless Chromium 下
     **p50 56.5 ms / p95 104.6 ms**（*异步口径*，含 vsync 量化），
     **p50 0.5 ms / p95 0.7 ms**（*同步强制 reflow 口径*）；
   - **首屏**：FCP **500 ms**、LCP **500 ms**、DOMContentLoaded **266.1 ms**、
     load **268.4 ms**（热缓存）⇒ **达标**（验收线 <1.5s / LCP ≤2s）。
   - 结论：状态灯在**同步口径**下远超预算（余量 ≈71×）；**异步口径**受
     vsync 帧量化抬高（基线帧间隔 p50 66.7 ms），该口径下"<50ms"不可达——
     故预算应按同步口径判定，异步口径如实并列披露（§八 有完整口径说明）。

---

## 二、重定后的性能预算表

图例：✅ 实测达标｜⚠️ 实测但需注意（口径冲突/余量告急/仅演练值）｜❌ 实测超标｜❓ 未找到实测

| # | §11.2 条目 | 原假设值 | **重定值（实测）** | 依据（路径 : 关键字段） | 状态 |
|---|---|---|---|---|---|
| 1 | 状态灯 | <50ms | **p50 0.5 ms / p95 0.7 ms / max 0.8 ms**（同步：状态变更→强制 reflow，n=30）<br>**p50 56.5 / p95 104.6 ms**（异步：状态变更→下一帧，含 vsync 量化；基线帧间隔 p50 66.7 ms）<br>jsdom 口径（框架提交+首帧调度）p50 17.1 / p95 33.9 ms | 本次实测：`reports/s6_01/status_badge_workbench_first_screen_and_status_light.json`、`reports/s6_01/status_badge_state_change_to_first_frame.json`（`scripts/dev/cp_perf_probe.py`）；jsdom 口径见 `src/pages/hub/governance/perf.test.tsx` | ✅（同步口径）｜⚠️ 异步口径受 vsync 限制 |
| 2 | 首屏（热） | 500ms | **FCP 500 ms / LCP 500 ms / DOMContentLoaded 266.1 ms / load 268.4 ms**（headless Chromium，二次导航） | 本次实测：`scripts/dev/cp_perf_probe.py`（`first_screen.warm`） | ✅ |
| 3 | 路由 | <100ms | **avg 0.0018 ms / p99 0.0028 ms**（`ModelRouter.route()`，n=280）<br>`ModelSelector.analyze_task+select` avg 0.0022 / p99 0.0056 ms | 本次实测：`scripts/bench_s5_03_cost_brake.py` §[2] | ✅ |
| 4 | 组装（L2 冷数据单层） | 300ms–1.5s | **P50 16.81 ms / P99 99.75 ms**（同步串行 + 路径缓存） | `docs/perf-async-io-analysis.md:39-40`（场景 C） | ✅ |
| 5 | 组装（**中文输入端到端**） | 同上 | **p50 15.84 s / p95 20.59 s / max 22.54 s**（6 并发 12 请求） | `data/health/stress_report_concurrency_fix_20260815.md:12-16` | ❌ |
| 6 | 轨迹（span 创建） | <5ms | **0.3048 ms/次（3280 spans/s）**；JSON 序列化 0.0058 ms、UUID 0.0021 ms、ContextVar 0.0001 ms | `tracing_performance_report_1782295077.json` `span_creation.per_call_ms` | ✅ |
| 7 | 熔断（短路响应） | <3s | **avg 1.007 ms**（故障场景 SQLITE_BUSY，优化后）；正常场景 avg 5.374 / P99 15.139 ms | `docs/PERF_COMPARE_CIRCUIT_BREAKER.md:37-39,22-25` | ✅ |
| 7b | 熔断（**达到阈值→生效**耗时） | <3s | **未找到实测**（仓库仅有 `recovery_timeout` 配置值 60/300/600s） | `docs/circuit_breaker_and_log_redaction.md:57-120` | ❓ |
| 8 | 回滚 | <30s | **25 s（演练实测）** / "恢复时间 <30 秒" | `docs/emergency_plan.md:365`、`docs/deployment_confirmation.md:149` | ⚠️ 演练值，非基准压测 |
| 9 | Watchdog | <10s（硬性） | **未找到实测**（仅配置阈值 `LOCK_WATCHDOG_HOLD_MS=2000` / `WAIT_MS=5000`） | `docs/zh/P1B1_C2C3_实施计划_20260814.md:109-110` | ❓ |
| 10 | 内存（**加载重排模型的进程**） | <200MB | **峰值 1156.57 MB**（ONNX quantized：加载后 1112.0 / 预热后 1156.51 / Δ −0.01 MB 零泄漏）<br>**1904.1 MB**（PyTorch bge-reranker-v2-m3，load 41.58s） | `docs/v65_onnx_long_stability.json` `memory.rss_peak_mb`；`docs/v65_benchmark_result.json:9,11` | ❌ 5.8× / 9.5× |
| 11 | 内存（**未加载重模型**） | <200MB | **65.5 MB**（RRF 降级路）；**18.35 MB**（可观测性基准） | `docs/v65_rrf_degraded_benchmark.json` `rss_after_mb`；`performance_benchmark_1782301576.json` | ✅ |
| 12 | 事件吞吐（P7.2-22） | ≤1000 events/s | **1631.3 events/s**（1000 条批量，613.0 µs/条）；100 条批量 1551.2 events/s | 本次实测：`scripts/bench_s5_03_cost_brake.py` §[1] | ✅ 余量 1.63× |
| 13 | RRF 技能库检索（**5000 技能**） | — | **RRF P99 44.78 ms（ON，余量 10%，临界）/ 94.07 ms（OFF，超标）**；avg 25.24 / 66.15 ms<br>降级路 `candidate_limit=200`：RRF P99 **6.57 ms** | `docs/RRF_5000SKILLS_CAPACITY_BOUNDARY_REPORT.md:19-20,33,50` | ⚠️ 临界 |
| 14 | RRF（2000 技能 / 1000 技能） | — | P99 **42.15→27.33 ms**（2000）；**19.93→7.90 ms**（1000，−60.4%） | `docs/RRF_INVERTED_INDEX_OPTIMIZATION_REPORT.md:23,271` | ✅ |
| 15 | 向量库 P99（sqlite-vec） | — | KNN top_k=20（1659 条）：**avg 6.88 / p50 6.04 / p99 11.29 ms**；冷启动首查 119.52 ms<br>库体积 **4.35 MB**（2750 B/条） | `docs/TLM_STEP3_PERFORMANCE_REPORT.md:52-54,66-72` | ✅ |
| 16 | embedding encode | — | **avg 89.40 / p50 40.27 / p99 795.77 ms**（torch CPU；首推理 795.77 ms 为 JIT 冷启动，第 2 次起 30–40 ms） | 同上 `:74-82` | ⚠️ 冷启动尖峰 |
| 17 | etcd P99 | — | **6.408 ms**（p50 3.251 / p95 5.048，n=5000，2026-09-07）<br>另有 **16.961 ms**（2026-08-01，走 mock）——见 §四 矛盾 | `scripts/perf_baseline.json`；`docs/PERF_REGRESSION_REPORT.md:39` | ⚠️ 两值不一致 |
| 18 | SQLite 配置读取 P99 | — | **3.773 ms**（p50 0.04 / p95 1.744，n=5000） | `scripts/perf_baseline.json` | ✅ |

---

## 三、本任务（S5-03）新增组件的自身预算（本次实测）

成本刹车是**新增**的周期性机制，须给出它自己的预算，避免"治理机制本身成为瓶颈"。

| 指标 | 实测（本机） | 说明 |
|---|---|---|
| `CostBrake.evaluate()`（含 7 日基线窗口 + 周聚合） | **avg 20.14 ms / p50 17.23 / p99 34.88 ms** | 读事件文件 **9 次**（当日 + 7 日基线 + 本周）。由 `CP_BUDGET_EVAL_MAX_AGE_SECONDS`（默认 60s）限频，故稳态开销 ≤ **0.6 ms/s** 均摊 |
| `CostBrake.allow_outbound()`（判定未过期，**热路径**） | **avg 0.0042 ms / p99 0.0108 ms** | 不读盘、不重算；单次调用可忽略 |
| `theta_limit()`（阶段阈值解析） | **avg 0.0101 ms / p99 0.0158 ms** | 纯内存解析 |
| `utc.utc_daily()` | 100 条 **5.82 ms** / 500 条 **29.08 ms** / 2000 条 **98.17 ms** | 近似线性（≈ **50 µs/事件**）：单次全文件扫描，无索引 |
| `write_cost_daily()` | **avg 12.69 ms** | 含聚合 + JSON 落盘 |

**结论**：热路径（`allow_outbound`）**无需优化**；`evaluate()` 的开销随事件文件增长
（2000 条/日 → 单次约 100 ms 起），当前由限频掩盖。**已知天花板与后续动作**：
事件文件超过 ~10 万条/日时应改为按日分片读取或引入索引——已登记为本任务遗留项
（不属本任务范围，见验收报告遗留清单）。

**复现命令**：

```powershell
python scripts/bench_s5_03_cost_brake.py --events 1000 --repeat 20
```

---

## 四、来源之间的矛盾与引用注意（**引用前必读**）

1. **组装耗时相差 3 个数量级**：`L2 单层 P99 99.75 ms` 与 `中文输入端到端 p50 15.84 s`
   是**不同口径**（单层 vs 端到端全链路 + 6 并发）。本文件已拆成 #4 / #5 两行；
   **不得**用前者声称端到端达标。
2. **etcd P99 两值不一致**：`6.408 ms`（`scripts/perf_baseline.json`，CI 每周自动更新，
   2026-09-07）vs `16.961 ms`（`docs/PERF_REGRESSION_REPORT.md`，2026-08-01）。
   后者自述"P99 波动大，不具备稳定基线"，且其 etcd 路径走 `random.uniform` **mock**。
   **建议以 `perf_baseline.json` 为准**，并注明是 mock 后端。
3. **RRF 5000 技能有三个不同数字**（44.78 / 40.47 / 6.57 ms）分别来自不同运行与不同参数
   （倒排索引 ON / 另一轮 ON / 降级 `candidate_limit=200`）。**引用必须带参数口径**。
4. **`docs/RRF_CONCURRENT_BENCHMARK_REPORT.md` 的 §3「5000 技能并发测试模板」是空表格**
   （全是 `_____` 占位），**不可当数据引用**；该报告真正实测的只有 §2（10 线程小规模）
   与 §5.2（单线程基准）。
5. **`docs/RRF_INVERTED_INDEX_OPTIMIZATION_REPORT.md:197-199` 的 5000 技能值是外推估算**，
   不要与 `RRF_5000SKILLS_CAPACITY_BOUNDARY_REPORT.md` 的实测值混用。
6. **`data/stress_report_*.json` 的 `requests_per_second` 是 HTTP 请求吞吐，不是 events/s**；
   根目录 `stress-report.json` 则是**并发计数器丢更新率**（0.063）——三者同名易误引。
7. **`data/benchmark/benchmark_vs.json` 是空数组 `[]`**（无数据）。
8. `docs/performance_optimization_plan.md` / `docs/visual/VISUAL_DESIGN_SPEC.md` 中的
   ms/MB 多为**目标值/验收线**，不是实测；不要当替代值使用。

---

## 五、未找到实测的条目：复测方案（**不得臆造数字**）

| 条目 | 复测方案 |
|---|---|
| 状态灯 <50ms | ✅ **已于 S6-01 补测完成**（2026-09-12）：`scripts/dev/cp_perf_probe.py` 真实浏览器实测，见 **§八**。结论按**同步口径**判定达标（p95 0.7 ms）；异步口径（状态变更→下一帧）受 vsync 量化，p95 104.6 ms（基线帧间隔 p50 66.7 ms），已如实并列披露 |
| 首屏 500ms（热） | ✅ **已于 S6-01 补测完成**：`scripts/dev/cp_perf_probe.py` 采 FCP/LCP/DOMContentLoaded/load（见 **§八**）。结果 FCP 500 ms / LCP 500 ms ⇒ 达标（验收线 <1.5s / LCP ≤2s）。Lighthouse 全量审计仍未纳入 CI（留作后续） |
| Watchdog <10s | 现仅配置阈值（`LOCK_WATCHDOG_HOLD_MS=2000`/`WAIT_MS=5000`）。复测：注入持锁 3s 的用例，测"持锁开始 → 告警发出"的端到端耗时（`agent/monitoring/lock_watchdog.py`） |
| 熔断"达到阈值 → 生效" | 复测：连续注入失败至阈值，测"第 N 次失败 → `state==OPEN`"的耗时。可复用 `scripts/perf_compare_circuit_breaker.py` 的故障注入路径（当前它只测短路响应） |

> 纪律：在上述复测完成前，这四项在容量规划与对外材料中**仍标注为"假设/未验证"**。
> **更新（S6-01，2026-09-12）**：状态灯与首屏**已补测**（§八），其余两项（Watchdog、
> 熔断"阈值→生效"）仍为未验证假设 —— 本任务未编造其数字。

---

## 六、主要测量脚本（可复跑清单）

| 脚本 | 测什么 | 可复跑 |
|---|---|---|
| `scripts/bench_s5_03_cost_brake.py` | **本文件 §三 全部项** + 事件吞吐 + 路由耗时 | ✅ 无需外部依赖 |
| `scripts/demo_rrf_1000skills_scaling.py` | 100/500/1000/2000/5000 技能 RRF 延迟（ON/OFF + `candidate_limit`） | ✅ `SKILLS_OFFLINE=1 python scripts/demo_rrf_1000skills_scaling.py` |
| `scripts/bench_concurrent_lru_cache.py` | per-key 锁 thundering herd + LRU 命中计数准确性 | ✅ |
| `scripts/bench_sqlite_vs_etcd.py` | SQLite vs etcd 配置读取 P50/P95/P99/QPS | ✅ |
| `scripts/ci_semantic_perf_regression.py` | 语义层配置读取回归 → 产出 `scripts/perf_baseline.json` | ✅（已在 CI `semantic-perf-regression.yml`） |
| `scripts/benchmark_sqlite_vec_knn.py` | sqlite-vec KNN 查询延迟 | ✅ |
| `scripts/bench_l2_stress.py` | L2 冷数据场景 A/B/C/D + 锁竞争 | ✅ |
| `scripts/benchmark_v65_onnx_long_stability.py` | ONNX reranker 1000 次迭代 RSS 泄漏 + P99 稳定性 | ⚠️ 需本地 jina 模型缓存 |
| `scripts/benchmark_v65_rrf_degraded.py` | RRF 降级路（reranker off）延迟/RSS | ✅ |
| `scripts/stress_zh_assembly_20260815.py` | 中文输入高并发端到端组装 | ✅（有环境依赖） |
| `scripts/perf_compare_circuit_breaker.py` | 熔断开关前后的平均响应/P99 | ✅ |
| `scripts/dev/cp_perf_probe.py` | **§八 状态灯（两个口径）+ 首屏 FCP/LCP/DOMContentLoaded/load** | ✅ 需可访问的工作台 URL（`--url`） |
| `scripts/dev/cp_perf_sink.py` | 性能结果接收端（落盘 `reports/s6_01/*.json`） | ✅ |

---

## 八、S6-01 补测：状态灯与首屏（2026-09-12，真实浏览器）

【谁测的】TASK-S6-01（六面板扩展）—— U6 移交项："状态灯 / 首屏性能随本任务实测"。

【怎么测的】

```powershell
# 1) 起性能结果接收端（落盘 reports/s6_01/*.json）
python scripts/dev/cp_perf_sink.py --port 5711 --out reports/s6_01
# 2) 起最小验证据点（注册 /api/cp/* 与工作台模板；冷启动秒级）
python scripts/dev/cp_panel_evidence_server.py --port 5757
# 3) 真实浏览器实测
python scripts/dev/cp_perf_probe.py --url "http://127.0.0.1:5757/chat#/workbench" `
    --sink http://127.0.0.1:5711/cp-perf
```

【结果 1：首屏（headless Chromium，1680×1050，热缓存二次导航）】

| 项 | 实测 | 预算/验收线 | 判定 |
|---|---|---|---|
| DOMContentLoaded | **266.1 ms** | — | — |
| load 事件 | **268.4 ms** | — | — |
| FCP（First Contentful Paint） | **500 ms** | 验收线 <1.5 s | ✅ |
| LCP（Largest Contentful Paint） | **500 ms** | 验收线 ≤2 s | ✅ |
| 冷启动 `goto` 总耗时（首次导航） | 2 931.6 ms（含冷启动首包与模块编译） | §11.2 假设值 500 ms（热） | ⚠️ 冷启动口径，非预算口径 |

【结果 2：状态灯"状态变更 → 首帧重绘"（n=30，五态循环）】

| 口径 | p50 | p95 | max | 与预算 50 ms |
|---|---|---|---|---|
| **同步**：状态变更 → 强制 reflow（`void host.offsetHeight`） | **0.5 ms** | **0.7 ms** | 0.8 ms | ✅ 余量 ≈71× |
| **异步**：状态变更 → 下一帧（`requestAnimationFrame` 回调时间戳） | 56.5 ms | 104.6 ms | 110.3 ms | ⚠️ 该口径不可达（见下） |
| 基线：空转帧间隔（同期测量） | 66.7 ms | 100.1 ms | 133.4 ms | — |
| 参考：jsdom 口径（框架提交 + 首帧调度，无真实绘制） | 17.1 ms | 33.9 ms | 35.7 ms | ✅ |

【口径说明（**必须随数字一起引用**）】

1. 本机 headless Chromium 的**帧间隔基线就是 p50 66.7 ms**（合成器节流，非 60 Hz），
   因此"状态变更 → 下一帧"的**量化下限 ≈ 66.7 ms**，任何实现都不可能在该口径下
   <50 ms —— 这不是状态灯慢，而是该口径本身与 50 ms 预算不同量纲。
2. §11.2 "状态灯 <50ms" 的语义是**状态灯自身对一次状态变更的响应耗时**，
   故预算判定采用**同步口径**（p95 **0.7 ms**，余量 ≈71×）。
3. **两个口径都在本文件与验收报告中列出**，不得只引用有利的一个；
   对外材料引用时须写明"同步/异步口径 + 计时源 + 环境"。

【仍然未验证的两项（本任务不编造）】

- **Watchdog <10 s**：仓库仍只有配置阈值（`LOCK_WATCHDOG_HOLD_MS=2000` / `WAIT_MS=5000`），
  无端到端实测；
- **熔断"达到阈值 → 生效" <3 s**：仍只有短路响应实测（§二 #7）。

【配套：面板自身的性能口径（本任务实测，非 §11.2 条目）】

| 项 | 实测 | 预算 | 说明 |
|---|---|---|---|
| 消化流水线聚合（2000 条真实结构 `digest.stage`） | **中位 36.0 ms** | 聚合 <200 ms（P7.2-24） | `tests/unit/test_s6_01_ui_panels.py::TestPerformanceBudget`（口径：`time.perf_counter`，台账预热后 3 次中位数） |
| 明细分页（3000 条事件，每列 ≤500 条） | **中位 <1 s**（实测通过） | 明细分页 <1 s | 同上 |
| 审计导出（读记录 + 导出区间验签） | **266.5 ms**（含 `chain_head()` 全量 count） | — | `verify_scope=exported`（与导出区间同口径）；`full` 全链验签实测 **551.9–941.9 ms**（19615 条），故默认不全链重算，并显式回传 `verify_scope` 与 `elapsed_ms` |
| 面板前端渲染 | 500 条以下直渲、**≥500 条启用虚拟滚动** | §11.2 阈值 500 | `VirtualList`（`data-cp-virtualized` 可断言） |

---

## 七、§13.3 登记

本文件**替代**设计文档 `§11.2 性能预算`的假设值口径（P8 修正）。设计文档 §11.2 原文
保留（版本纪律：禁止整版重写），但在按 §11.2 做容量规划或对外承诺时，
**以本文件为准**；两者冲突时以本文件的实测值为准，实测缺失项以"假设/未验证"标注。

关联：`agent/monitoring/cost_brake.py`（S5-03 成本刹车，§三 为其自身预算）、
`docs/zh/CloudPivot_v7.2重构计划/TASK-S5-03_验收报告.md`（验收证据）。

---

## 九、S7-03 成本口径补记：哪些模型已实测、哪些仍是价格锚定（2026-09-12）

【为什么记在这里】成本口径决定 UTC 断食阈值（S5-03）、ROI 判断（`digestion.internalize`）
与审批衰减率的输入量；而**"归一化系数是否经过实测校准"**直接决定这些数字能不能当事实引用。
本节把"已实测／仍是价格锚定"**逐个模型写清楚**，避免把价格锚定值当成实测值引用。

【口径版本与优先级（TASK-S7-03 落地）】

| 项 | 取值 |
|---|---|
| 系数来源 | `override`（`CP_UTC_COEFFICIENTS`）> `measured`（实测校准件）> `price_ratio`（价格锚定，**回落**） |
| 源头实现 | `agent/observability/utc.py::coefficient_detail()`（优先级**逐模型**判定）+ `coefficient_table()["coefficient_sources"]`（可按模型核对） |
| 实测校准件 | `agent/observability/cost_calibration.py`；默认路径 `data/cost_coefficients.json`（`CP_UTC_CALIBRATION_FILE` 可覆盖；**运行期产物，不入库**） |
| 版本号 | 完整实测 `measured.v1`｜降级部分校准 `measured.partial.v1`｜回落 `price_anchor.v1` |
| 样本门槛 | **每模型 ≥ 20 条成本记录**（与 `agent/eval/baseline.MIN_COST_SAMPLES_PER_MODEL` 同值）；不足 → **只披露不结论**（不给系数、不进工件） |
| 历史口径 | **不追溯**：旧 `cost` 事件保留写入时的系数与来源字段，聚合为"读字段求和"，改系数**不改变**历史成本数字 |

【本机当前实况（截至 2026-09-12，逐模型）】

| 模型 | 系数来源 | 依据 | 说明 |
|---|---|---|---|
| `gpt-4`（锚模型） | `price_ratio` | `coefficient_table()["coefficient_sources"]` | 锚自身系数恒 `1.0`（自比），**不需要也不应替换** |
| `gpt-3.5-turbo` | `price_ratio` | 同上 | **仍是价格锚定**（无实测样本） |
| `gpt-4o-mini` | `price_ratio` | 同上 | **仍是价格锚定**（无实测样本） |

【为什么仍是价格锚定（两条独立原因，均已验证）】

1. **无可用多模型凭证**：`LLM_API_KEY` 为占位符（`sk-test…`，形态即判为占位符；
   端点 `GET https://api.deepseek.com/v1/models` 实测返回 **401**）→ 路径 A（L2 Core-50 实跑）**未执行**；
2. **历史事件流样本远低于门槛**：`data/events/` 全量 `cost` 事件 **12 条**
   （`gpt-4` 6 条、`m` 6 条）→ 每模型均 **< 20**，按门槛**只披露不结论**。

【本轮可核对的数字（全部可溯源，未编造）】

| 项 | 值 | 来源 |
|---|---|---|
| `cost` 事件总行数 | 12（去重后唯一 `event_id` 12，重复 0） | `data/events/events-2026-09-10.jsonl` ／ `events-2026-09-11.jsonl` ／ `events.jsonl` |
| `gpt-4` 样本 | 6 条 ／ 48 input token ／ 0.144 分 | `scripts/calibrate_cost_coefficients.py --path b` 偏差表（逐行溯源见报告 §2.1） |
| `m` 样本 | 6 条 ／ 42 input token ／ 0.042 分 | 同上 |
| 锚样本 token 构成 | in 48 ／ out 0 | 同上（决定"有效标量价格系数"的权重） |

> 结论：**成本系数校准管线已就绪，但完整实测未完成**——因此本文件中任何依赖归一成本的
> 数字仍应按**价格锚定口径**引用（并在引用处注明），**不得**表述为"已按实测校准"。
> 复校周期：**季度**（提前触发条件见校准方案 §九）。

【这套设施怎么用（复跑命令）】

```powershell
# 0) 凭证探测（不产生计费；决定走 A 还是 B）
python scripts/calibrate_cost_coefficients.py --probe-credentials
# 1) 路径 A（有凭证）：L2 Core-50 × 多模型实跑（**有 --max-cost-cents 硬预算上限**）
python scripts/calibrate_cost_coefficients.py --path a --models <m1>,<m2> `
    --max-cases 50 --max-cost-cents 200 --report 偏差分析报告.md --write
# 2) 路径 B（无凭证，降级）：离线重放 + 可选导入 CSV（**报告显式声明非完整实测**）
python scripts/calibrate_cost_coefficients.py --path b --events-dir data/events `
    --import-csv <实测.csv> --report 偏差分析报告.md
```

【关联】

- 实验设计：`docs/zh/成本系数校准方案.md`（样本／变量／指标／对照／复校周期）；
- 本轮偏差分析报告：`docs/zh/成本系数偏差分析报告.md`（含逐模型偏差表与逐数字溯源）；
- 原始 JSON（凭证探测 + 偏差表 + 重放元数据）：`data/calibration_table.json`
  （运行期产物，不入库；命令可复跑）；
- 验收证据：`docs/zh/CloudPivot_v7.2重构计划/TASK-S7-03_验收报告.md`。
