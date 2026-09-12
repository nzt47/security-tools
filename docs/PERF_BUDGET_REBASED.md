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
7. **【S7-04 补测，2026-09-12】Watchdog 与熔断"达阈值 → 生效"已实测**（§九）：
   至此 §二 表中**再无 ❓ 项**（原有 4 项无实测已全部清零）。
   - **Watchdog 感知**：持锁超时口径 **p50 3000.9487 ms / p95 3002.3624 ms**（受控注入持锁 3 s，
     n=20）；主进程失联口径（本脚本自起的**桩进程**）**p50 6.0266 ms / p95 9.9044 ms**（n=20）
     ⇒ 两个口径均 **< 10 s**（余量 ≥3.3×）；**"恢复"侧仍无实测**（需真实服务重启，
     §九 已写明测法与环境要求，**不编造**）。
   - **熔断「达阈值 → 生效」**：阈值达成 → 状态 `OPEN` **p50 0.0015 ms / p95 0.0035 ms**；
     阈值达成 → **下一次 outbound 被实际阻断** **p50 0.0176 ms / p95 0.0418 ms**（n=20）
     ⇒ 远优于 **< 3 s**（余量 ≈7×10⁴），该条目对当前实现而言是"没有约束力的预算"。
   - 两项的**口径边界**（"释放点判定持锁超时"、"`is_stale()` 为显式调用而非自动发现"、
     "集群配置下发时延单机测不出"）在 §九 逐条写明 —— **引用数字必须连同边界一起引用**。

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
| 7b | 熔断（**达到阈值→生效**耗时） | <3s | **阈值达成 → `state=OPEN`：p50 0.0015 / p95 0.0035 ms**；**阈值达成 → 下一次 outbound 实际被阻断：p50 0.0176 / p95 0.0418 ms**（n=20，`wall_clock(perf_counter)`，受控抛错桩达阈值） | 本次实测：`scripts/measure_perf_budget.py`（`reports/s7_04/perf_budget_probe.json` `results.circuit-breaker`）；见 **§九** | ✅（余量 ≈7×10⁴） |
| 8 | 回滚 | <30s | **25 s（演练实测）** / "恢复时间 <30 秒" | `docs/emergency_plan.md:365`、`docs/deployment_confirmation.md:149` | ⚠️ 演练值，非基准压测 |
| 9 | Watchdog | <10s（硬性） | **感知**：① 持锁超时口径 **p50 3000.9487 / p95 3002.3624 ms**（受控注入持锁 3 s，n=20；纯判定开销 p95 1.3261 ms）；② 主进程失联口径 **p50 6.0266 / p95 9.9044 ms**（桩进程 terminate → `is_stale()` 判陈旧，n=20）<br>**恢复**：**未实测**（见 §九 测法与环境要求） | 本次实测：`scripts/measure_perf_budget.py`（`reports/s7_04/perf_budget_probe.json` `results.watchdog-*`）；配置阈值 `LOCK_WATCHDOG_HOLD_MS=2000` / `WAIT_MS=5000`（`docs/zh/P1B1_C2C3_实施计划_20260814.md:109-110`）；见 **§九** | ✅ 感知（余量 ≥3.3×）｜⚠️ 恢复未测 |
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
| Watchdog <10s | ✅ **已于 S7-04 补测完成**（2026-09-12）：`scripts/measure_perf_budget.py` 两个口径实测（持锁超时 p95 3002.36 ms；主进程失联 p95 9.90 ms）⇒ **感知**达标（余量 ≥3.3×），见 **§九**。**"恢复"侧仍未测**：需在可重启的环境里对真实服务注入"主进程失联 → 拉起"（本机不 kill 生产进程），环境要求见 §九 |
| 熔断"达到阈值 → 生效" | ✅ **已于 S7-04 补测完成**：`scripts/measure_perf_budget.py` 经**真实调用入口** `CircuitBreaker.call()` 连续注入失败达阈值，实测"阈值达成 → 下一次 outbound 被拒" p95 **0.0418 ms** ⇒ 达标（余量 ≈7×10⁴），见 **§九** |

> 纪律：在上述复测完成前，这四项在容量规划与对外材料中**仍标注为"假设/未验证"**。
> **更新（S6-01，2026-09-12）**：状态灯与首屏**已补测**（§八），其余两项（Watchdog、
> 熔断"阈值→生效"）仍为未验证假设 —— 本任务未编造其数字。
> **更新（S7-04，2026-09-12）**：Watchdog 与熔断"阈值→生效"**已补测**（§九）。
> 本文件 §二 表中**已无 ❓ 项**。**唯一仍未实测的是 Watchdog 的"恢复"侧**
> （不是"忘了测"，而是需要可重启真实服务的环境）—— 它**如实保留为未验证**，
> 且 §九 给出了测法与环境要求，**未删除该指标、未以估算值填充**。

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
| `scripts/perf_compare_circuit_breaker.py` | 熔断开关前后的平均响应/P99（**短路响应**口径） | ✅ |
| `scripts/measure_perf_budget.py` | **§九 Watchdog（两个口径）+ 熔断「达阈值→生效」**；输出原始毫秒级采样 | ✅ 无需外部依赖（`--items all --repeat 20`） |
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

【仍然未验证的一项（S6-01 当时；已在 S7-04 补测，见 §九）】

- **Watchdog <10 s**：仓库仍只有配置阈值（`LOCK_WATCHDOG_HOLD_MS=2000` / `WAIT_MS=5000`），
  无端到端实测；
- **熔断"达到阈值 → 生效" <3 s**：仍只有短路响应实测（§二 #7）。

> ☝ **以上两项已于 S7-04 补测**（2026-09-12）：见 **§九**。本节原文保留（版本纪律：
> 禁止整版重写），供追溯"S6-01 时点的真实状态"。

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

## 九、S7-04 补测：Watchdog 与熔断「达阈值 → 生效」（2026-09-12，受控环境实测）
【谁测的】TASK-S7-04（验证与探活补全）—— 收官审计 #4 的四项性能缺口中，S6-01 已补
状态灯与首屏，本任务补剩余两项（§二 #7b、#9）。

【怎么测的】（**可复跑**；输出**原始毫秒级采样**，非估算、非引用历史）

```powershell
python scripts/measure_perf_budget.py --repeat 20 --out reports/s7_04/perf_budget_probe.json
# 原始采样：reports/s7_04/perf_budget_probe.json（含每个口径的 samples_ms 全量数组）
```

【实测环境（必须随数字一起引用）】

| 项 | 值 |
|---|---|
| 平台 | Windows-10-10.0.19045-SP0 |
| Python | 3.12.0 |
| CPU | 12 逻辑核 |
| **计时源** | `time.perf_counter()`（单调墙钟；`clock = wall_clock(monotonic perf_counter; 单次采样单位 ms)`） |
| 隔离 | 进程内受控环境：临时目录 + 桩实现；**不 kill 生产进程、不读写运行时台账** |
| 采样 | 每口径 **n=20** |

### 9.1 Watchdog（§二 #9，预算 <10 s）

`is_stale()` / 持锁超时是**两个不同口径**，本任务**分别测量、分别披露**，
**不合并成一个数字**：

| 口径 | 测量方法 | p50 | p95 | max | 与预算 10 s |
|---|---|---|---|---|---|
| **W-1 持锁超时感知** | 受控注入 `WatchedLock` 持锁 **3 s**（> `LOCK_WATCHDOG_HOLD_MS=2000`），测"**持锁开始 → 告警回调触发**" | **3000.9487 ms** | **3002.3624 ms** | 3002.3624 ms | ✅ 余量 ≥3.3× |
| W-1 的**纯判定开销** | "释放锁 → 告警回调触发"（把持锁时长与判定成本分开） | 0.3869 ms | 1.3261 ms | 1.3261 ms | — |
| **W-2 主进程失联感知** | 本脚本自起**桩进程**持有 `WatchdogSingleton` 锁 → `terminate(桩)` → 按 **100 ms 周期**探测 `is_stale()` 判陈旧，测"**失联 → 判陈旧**" | **6.0266 ms** | **9.9044 ms** | 9.9044 ms | ✅ 余量 ≈1009× |

原始采样（n=20，`results.watchdog-hold.runs[].hold_to_alert_ms`，`perf_counter` 毫秒）：

```
3002.3624 3000.5051 3000.8267 3000.9487 3000.7322 3000.8984 3001.2683 3001.3442
3000.9217 3000.5885 3001.9631 3000.7833 3001.3935 3001.0056 3000.7826 3001.0862
3001.0690 3000.5733 3000.4594 3001.1443
```

（W-2 `results.watchdog-liveness.runs[].detect_ms`：

```
7.0991 5.4658 5.8924 5.9914 6.8570 7.2152 9.9044 7.2077 7.5460 5.5449
9.3115 6.3904 8.1067 5.9592 4.5001 4.7379 4.4613 6.0266 5.2078 4.9063
```

）

【口径边界（**必须随数字一起引用**）】

1. **W-1 是"释放点"判定**：`WatchedLock.release()` 才调 `record_hold_timeout`。
   故 W-1 测的是"**已释放的超长持锁**"（≈ 持锁时长 + 判定开销）；
   **永不释放**的持锁**不会**触发该告警 —— 该场景由 W-2 的陈旧锁探测覆盖。
   这正是"两个口径并列、不合并"的原因：合成一个数会掩盖这个结构事实。
2. **W-2 是"显式调用"而非"自动发现"**：`WatchdogSingleton.is_stale()` 的文档明确写着
   "**显式调用，不自动回收**"。故端到端"感知时间" = **探测周期 + 单次判定耗时**；
   本表按 100 ms 周期披露（`--poll-interval-ms`），**不得**把它读成"看门狗自己发现得快"。
3. **W-2 的桩进程是受控对象**：被 `terminate()` 的是本脚本 `subprocess.Popen` 起的桩
   （命令行带 `--stub-holder`，pid 取自 `Popen.pid`），**不涉及任何生产进程**；
   失联前的对照断言 `holder_alive_before_kill=true` 证明该测量不是"恒真"。

【**仍然未实测：Watchdog 的"恢复"侧**（本任务不编造）】

- 缺口：§11.2 的硬性指标是"**主进程失联 → 感知 / 恢复**时间"，本任务实测的是**感知**；
  **"拉起/恢复"（L3 `kill → 重启`）需要真实服务进程**，在单机受控环境里无法安全注入。
- **测法与环境要求**（留给具备该环境的一方）：
  1. 在**可重启的隔离环境**（专用 VM/容器，非生产）部署一个由 `self_healing.levels`
     L3 管护的可执行服务；
  2. 注入"主进程失联"（向被测服务发 SIGKILL / Windows `taskkill`，**只在隔离环境**）；
  3. 以 `perf_counter` 记录 `t_kill` → `SelfHealer._restart_service` 返回 →
     健康探针 `l1_process` 恢复为 UP，取 **t_restore − t_kill**；
  4. 重复 n≥20 取 p50/p95/max，与本预算 10 s 比较；
  5. 回填本表并**同时**更新 §五 的完成状态。
- 纪律：在该测量完成前，"Watchdog <10s"的**恢复语义**仍标注为**未验证假设**。

### 9.2 熔断「达到阈值 → 生效」（§二 #7b，预算 <3 s）

**走真实调用入口** `CircuitBreaker.call()`（不是直接改状态），以进程内抛错桩连续失败
至阈值（`failure_threshold=0.3`，`min_calls=5`），再经同一入口发起下一次 outbound
并期望抛 `CircuitBreakerError`：

| 口径 | p50 | p95 | max | 与预算 3 s |
|---|---|---|---|---|
| **阈值达成 → `state == OPEN`**（状态跃迁） | **0.0015 ms** | **0.0035 ms** | 0.0035 ms | ✅ |
| **阈值达成 → 下一次 outbound 被实际阻断**（本项主口径） | **0.0176 ms** | **0.0418 ms** | 0.0418 ms | ✅ 余量 ≈7×10⁴ |
| 每次采样：失败次数至熔断 | 全部 = **5**（= `min_calls`） | — | — | 与阈值语义一致 |
| 每次采样：是否真的被阻断 | 全部 = **true**（20/20） | — | — | — |

原始采样（n=20，`results.circuit-breaker.runs[].outbound_block_latency_ms`）：

```
0.0418 0.0235 0.0203 0.0186 0.0188 0.0187 0.0170 0.0167 0.0168 0.0172
0.0178 0.0182 0.0171 0.0170 0.0169 0.0171 0.0250 0.0175 0.0174 0.0176
```

【口径边界（**必须随数字一起引用**）】

1. 本口径测的是熔断器**自身**的"阈值达成 → 阻断**下一次**调用"时延。**跨进程/跨节点
   的配置下发时延**（若部署侧另有开关下发通道）**不在此列**，需集群环境，**单机测不出**
   ⇒ 如实标注，**不并入本数字**。
2. "阈值达成"的时点定义为**使错误率跨过阈值的第 5 次失败调用返回处**（`perf_counter`），
   与"状态跃迁"是两个可分辨的语义，故**两行都列出**。
3. 结果与 §二 #7（短路响应 avg 1.007 ms，故障场景 SQLITE_BUSY）**不同口径**：
   #7 是"已熔断后的响应耗时"，#7b 是"从失败累积到生效的时延"。**不得互相替代引用**。

【本任务的**已知副产品**（如实登记，非本任务缺陷）】

- 测量过程中 `WatchdogSingleton._os_lock_acquirable()` 在清理路径上稳定报
  `锁文件解锁失败（句柄关闭时 OS 会释放）: [Errno 13] Permission denied`
  （Windows `msvcrt.locking` 的字节区间解锁失败）。该失败**不影响**判定结论
  （`_try_lock` 已返回 True，且句柄关闭即由 OS 释放锁），故**实测值有效**；
  但它是既有实现的一处噪声/隐患，**归属 S5-02/S4-04 侧**，本任务不改动其代码，
  仅在此登记（见 TASK-S7-04 验收报告 §遗留）。

【结论】

- §二 #7b、#9 **已补齐实测**；本文件 §二 表中**再无 ❓**；
- **唯一仍未实测的是 Watchdog 的"恢复"侧**（环境要求见 9.1），如实保留为未验证假设；
- 四项原 ❓ 全部按"填实测"或"写明测法与环境要求"处理，**未删除任何既有指标项**、
  **未以估算值填充**。

---

## 十、成本口径（**非**性能预算）：判定集构建成本单列披露（TASK-S7-06 R1）

本文件管的是**延迟/吞吐/内存**预算，不含金额口径。为避免把两类东西混读，这里明确一条边界：

1. **判定集构建成本**（生成用例的 LLM 调用、人工抽检工时、回放算力）**不属于本文件的性能预算**，
   它是**一次性投入**，口径与公式见
   [`zh/判定集构建成本入ROI口径说明.md`](zh/判定集构建成本入ROI口径说明.md)；
2. 该成本**不进** `utc.utc_window()` 的分子分母（成本事件写在独立目录
   `<判定集根>/_case_cost/`），故本文件与 UTC/成本刹车相关条目的数字**都不受影响**；
3. 单机形态下的设施与缺口边界见 [`zh/单机降级设施表.md`](zh/单机降级设施表.md)（R7）。
   **与本文件 §九 的衔接（2026-09-12 合并后对齐）**：S7-04 已补测 Watchdog 的**感知侧**
   （W-2「失联 → 判陈旧」p95 **9.9044 ms** ✅）与熔断「达阈值 → 生效」（**0.0418 ms** ✅）⇒ 本文件 §二 表内
   两项 ❓ 已填实测；**唯一仍未实测的是 Watchdog 的"恢复/接管"侧**（需可重启的隔离环境，§九 9.1 已给测法与
   环境要求）。因此残留 **R5 属"部分补测"**：感知侧已实测、恢复侧未实测 —— 表述以本文件 §九 与
   [`zh/单机降级设施表.md`](zh/单机降级设施表.md) §三 为准，两处不得互相替代引用。

