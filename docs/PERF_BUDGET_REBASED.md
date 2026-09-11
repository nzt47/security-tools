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

---

## 二、重定后的性能预算表

图例：✅ 实测达标｜⚠️ 实测但需注意（口径冲突/余量告急/仅演练值）｜❌ 实测超标｜❓ 未找到实测

| # | §11.2 条目 | 原假设值 | **重定值（实测）** | 依据（路径 : 关键字段） | 状态 |
|---|---|---|---|---|---|
| 1 | 状态灯 | <50ms | **未找到实测**（无前端渲染耗时测量） | — | ❓ |
| 2 | 首屏（热） | 500ms | **未找到实测**（仓库仅有验收线：`首屏加载 <1.5s` / `LCP ≤2s`，非实测） | `docs/VISUAL_DESIGN_SPEC.md:1355`、`docs/superpowers/design/P2_生产环境性能基线建议.md:165` | ❓ |
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
| 状态灯 <50ms | 前端（`StatusBadge` 五态，见 `TASK-S6-01_六面板扩展.md`）落地后，用浏览器 Performance API 测"状态变更 → 首帧重绘"；S6-01 交付时补测并回填本文件 |
| 首屏 500ms（热） | 用 Lighthouse / Web Vitals 采 LCP/FCP（当前仓库只有验收线 `<1.5s`、`LCP ≤2s`）；建议在 S6-01 面板扩展时纳入视觉回归 CI |
| Watchdog <10s | 现仅配置阈值（`LOCK_WATCHDOG_HOLD_MS=2000`/`WAIT_MS=5000`）。复测：注入持锁 3s 的用例，测"持锁开始 → 告警发出"的端到端耗时（`agent/monitoring/lock_watchdog.py`） |
| 熔断"达到阈值 → 生效" | 复测：连续注入失败至阈值，测"第 N 次失败 → `state==OPEN`"的耗时。可复用 `scripts/perf_compare_circuit_breaker.py` 的故障注入路径（当前它只测短路响应） |

> 纪律：在上述复测完成前，这四项在容量规划与对外材料中**仍标注为"假设/未验证"**。

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

---

## 七、§13.3 登记

本文件**替代**设计文档 `§11.2 性能预算`的假设值口径（P8 修正）。设计文档 §11.2 原文
保留（版本纪律：禁止整版重写），但在按 §11.2 做容量规划或对外承诺时，
**以本文件为准**；两者冲突时以本文件的实测值为准，实测缺失项以"假设/未验证"标注。

关联：`agent/monitoring/cost_brake.py`（S5-03 成本刹车，§三 为其自身预算）、
`docs/zh/CloudPivot_v7.2重构计划/TASK-S5-03_验收报告.md`（验收证据）。
