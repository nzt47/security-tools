# C-2 长跑报告：监控栈长期运行行为（3.97 小时实测）

> **装置**：`docker compose --project-directory . -f _scratch/c2/compose.yml`（**派生的** compose，
> 与 `docker-compose.monitoring.yml` 逐行相同，只把 prometheus.yml 指向派生配置 + 多挂一条探针规则；
> **仓库文件一行未改**）+ 宿主侧受控探针 exporter（:9105）。
> **窗口**：本地 2026-10-03 06:55 → 10:55；**239** 个逐分钟样本 + **48** 个每 5 分钟的规则健康样本。
> **复现**：`python scripts/monitoring_longrun/gen_derived_config.py` → `python scripts/monitoring_longrun/c2_harness.py 14400`
> （并行 `python scripts/monitoring_longrun/rule_health_sampler.py`；复核 `python scripts/monitoring_longrun/analyze.py`）。
> 装置说明见 `scripts/monitoring_longrun/README.md`。
> **结论边界**：本窗口只有数小时，**不足以**外推磁盘/内存的月度行为；本文不承诺长期容量。

## 0. 结论速览（每条都能在下面找到数据）

| # | 结论 | 数据 |
|---|---|---|
| 1 | **抓取无丢失、无漂移** | 4 小时内 `up=1` 全程成立（探针/自身/Grafana 各 239/239）；只有"应用确实没在跑"的那 3 个样本是 0 |
| 2 | **TSDB 线性增长，量级可算** | head_series 2014 → 3253（**+312/小时**）；head_chunks 2014 → 25609；blocks 0 → **2.38 MB**；WAL 69 KB → **20.4 MB** |
| 3 | **规则求值未卡住、无漂移** | 单轮求值耗时 0.3–2.0 ms；间隔恒 30 s；`last_evaluation` 推进 14110 s vs 采样跨度 14102 s（**偏差 9 s**） |
| 4 | **告警生命周期真的有 pending→firing→resolved** | 21 个告警发生 **80 次**跃迁，其中 **11 个**走完完整三态（含一条真实故障 + 一条可控实验） |
| 5 | **两个附带发现（运维相关）** | `CircuitBreakerMetricsMissing` 一旦 firing **就不 resolved**；监控栈**自身**会触发 `HighMemoryUsage` / `HighCPUUsage` |

## 1. 抓取漂移：没有

| job | up=0 样本数 | 抓取耗时中位 | 抓取耗时最大 |
|---|---|---|---|
| prometheus | 0 / 239 | 4.8 ms | 21.2 ms |
| c2-probe（宿主探针） | 0 / 239 | 68.1 ms | 107.7 ms |
| grafana | 0 / 239 | 5.4 ms | 17.9 ms |
| yunshu（应用） | 3 / 239（T+89.7s ~ T+209.7s，**应用当时确实没在跑**） | 14.8 ms | 33.7 ms |
| yunshu-business（业务端点） | 3 / 239（同上） | 4.1 ms | **230.0 ms**（唯一一次抖动，仍远低于 scrape_timeout） |

- **采样节奏**同样证明宿主没睡：239 个样本间隔 min 59.9 s / 中位 60.0 s / max 60.1 s，**没有 >90 s 的缺口**；
- 应用在 **T+269.7 s** 恢复（`up` 回到 1）——这给了下面那条**真实**告警生命周期。

## 2. TSDB 增长

| 指标 | 首 | 末 | 增量 | 折合 |
|---|---|---|---|---|
| `prometheus_tsdb_head_series` | 2014 | 3253（峰值 3539） | +1239 | **+312/小时** |
| `prometheus_tsdb_head_chunks` | 2014 | 25609 | +23595 | +5948/小时 |
| `prometheus_tsdb_storage_blocks_bytes` | 0 | 2 380 766 | +2.38 MB | +0.60 MB/小时 |
| `prometheus_tsdb_wal_storage_size_bytes` | 69 525 | 20 413 226 | +20.3 MB | +5.1 MB/小时（**未跨过 2 小时的 WAL checkpoint 周期**，属正常锯齿形态，不是泄漏） |

> **更正记录（自我纠错，留痕）**：本报告第一版是脚本自动生成的汇总，它把 `{job: value}` 这种
> 字典形态当标量处理 ⇒ TSDB 增量全算成 **+0**，还外推出一句"0 条序列/小时"的胡话。
> 现已按标量重算（上表），正确算法固化在 `scripts/monitoring_longrun/analyze.py` 里（连同「告警消失 = resolved」这条判据）。
> 另：`prometheus_tsdb_head_samples_appended_total` 在本窗口**恒为 0** —— 2026-10-03 已查明，
> **不是** Prometheus 的问题，也**不是**该指标停用/改名，而是**长跑脚本的字典键碰撞**：
>
> 1. `prom/prometheus:v2.51.0` **确实**有这条指标，但它带一个 `type` 标签 ⇒ 一次查询返回**两条**序列：
>    `{type="float"}`（真实累计值）与 `{type="histogram"}`（本部署没开 native histogram，恒 0）；
> 2. `scripts/monitoring_longrun/c2_harness.py` 的 `query()` 用
>    `key = metric.get("job") or … or metric.get("__name__")` 做字典键，两条序列都落到同一个键
>    `"prometheus"` ⇒ **后一条（histogram，恒 0）覆盖了前一条** ⇒ 239/239 个样本恒 0
>    （原始样本 `_scratch/c2/samples.jsonl` 里 `tsdb_samples_appended` 全部是 `{'prometheus': 0.0}`，实测确认）。
>
> **正确的增长判据指标名**：`sum(prometheus_tsdb_head_samples_appended_total)`（等价写法 `…{type="float"}`）。
> 复核命令与原始输出（2026-10-03，本机 docker 引擎）：
>
> ```powershell
> # ① 起一个临时 v2.51.0，直接读它自己的 /metrics（用完即 rm -f，不留常驻服务）
> docker run -d --name c2-metricsprobe -p 19091:9090 prom/prometheus:v2.51.0
> docker exec c2-metricsprobe /bin/prometheus --version   # => prometheus, version 2.51.0 (revision c05c15512acb675e3f6cd662a6727854e93fc024)
> (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:19091/metrics).Content -split "`n" |
>   Select-String 'appended|head_.*(append|sample)'
> # # HELP prometheus_tsdb_head_samples_appended_total Total number of appended samples.
> # # TYPE prometheus_tsdb_head_samples_appended_total counter
> # prometheus_tsdb_head_samples_appended_total{type="float"} 418      <-- 真实值，确实在涨
> # prometheus_tsdb_head_samples_appended_total{type="histogram"} 0    <-- 恒 0，采样脚本取到的就是它
>
> # ② 同一查询走 HTTP API，看两条序列的**返回顺序**（脚本是"后写覆盖前写"）
> (Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:19091/api/v1/query?query=prometheus_tsdb_head_samples_appended_total').Content
> # {"status":"success","data":{"resultType":"vector","result":[
> #   {"metric":{...,"job":"prometheus","type":"float"},    "value":[1791000078.224,"418"]},
> #   {"metric":{...,"job":"prometheus","type":"histogram"},"value":[1791000078.224,"0"]}]}}
> #                     ^ float 在前、histogram 在后 ⇒ 字典后写覆盖 => 恒 0
>
> docker rm -f c2-metricsprobe
> ```
>
> 【结论】"该指标不可用"这个前提是**错的**，已更正。§2 上表改用 `head_chunks` 当增长判据本身没错
> （它同样随样本写入单调上涨），但**不是必须**：`sum(prometheus_tsdb_head_samples_appended_total)` 一直是可用的。
> 【遗留的脚本缺陷，本次未改】`c2_harness.py::query()` 的"同名多序列互相覆盖"是**通用**缺陷，
> 下一次长跑仍会复现（如 `query("up")` 里同一 job 的多实例、任何带 `type`/`quantile` 标签的指标）。
> 修法（1 行级）：把 `"tsdb_samples_appended": query("prometheus_tsdb_head_samples_appended_total")`
> 改成 `query("sum(prometheus_tsdb_head_samples_appended_total)")`；更彻底的做法是让 `query()` 在
> 检测到同一键出现多条序列时直接抛错，而不是静默覆盖。**本次只改文档，未动脚本**（属独立任务）。

## 3. 告警真实生命周期（C-2 的核心问题）

**21 个告警、80 次状态跃迁**；走完 `pending → firing → resolved` 三态的有 11 个。
下面三条链路各自回答一个不同的问题：

### 3.1 真实故障链路（不是造的）：应用当时真的没在跑

| T+秒 | 告警 | 状态 |
|---|---|---|
| 89.7 | YunshuDown / YunshuServiceDown / PrometheusTargetMissing | pending |
| 149.7 | 同上三条 | **firing**（+60 s，即规则的 `for` 生效） |
| 269.7 | YunshuDown | **resolved**（应用在 T+270 s 恢复） |
| 329.7 | YunshuServiceDown / PrometheusTargetMissing | resolved |

⇒ 「目标真的不可达 ⇒ 进 pending ⇒ 到点转 firing ⇒ 恢复后自动 resolved」**整条链路实测成立**。

### 3.2 可控实验链路：探针把生命周期做成可重复实验

| T+秒 | 事件 | 状态 |
|---|---|---|
| 300 | 探针 gauge 置 1（脚本动作） | —— |
| 329.7 | `C2ProbeLifecycle` | **pending**（下一个抓取周期就进 pending） |
| 449.7 | 同上 | **firing**（pending 持续满 `for: 2m` 后转 firing，误差 ~0 s） |
| 1500 | 探针置回 0（脚本动作） | —— |
| 1529.8 | 同上 | **resolved**（掉出 /api/v1/alerts 即判定 resolved） |

⇒ pending 这个中间态**确实存在**且时长由 `for` 决定 —— 这正是"只看规则文件"永远回答不了的问题。

### 3.3 只进不出：一条永远不会自己好的规则（2026-10-03 已查明根因并修）

`CircuitBreakerMetricsMissing`：pending @89.7 s → **firing @389.7 s** → **窗口末仍在 firing**
（最后一个样本 T+14370.9 s 仍为 firing；采样跨度 14281 s）。
同期应用在 **T+269.7 s 就已经恢复**（`up{job="yunshu"}` 回到 1、`/api/business/prometheus` 可抓），
`YunshuDown` / `YunshuServiceDown` / `PrometheusTargetMissing` 都在其后 resolved —— **只有它没有**。

#### 3.3.1 规则原文（改动前 · `monitoring/circuit_breaker_alerts.yml` 第 4 组 `circuit_breaker_config_drift`）

```yaml
  - name: circuit_breaker_config_drift
    interval: 60s
    rules:
      # 4.1 熔断器指标缺失（配置可能被移除）
      - alert: CircuitBreakerMetricsMissing
        expr: absent(yunshu_circuit_breaker_state) or absent(yunshu_circuit_breaker_trigger_total)
        for: 5m
        labels:
          severity: warning
          category: config
          service: circuit_breaker
        annotations:
          summary: "熔断器指标缺失"
          description: "过去 5 分钟未采集到熔断器指标，配置可能被移除或模块未加载"
          runbook_url: "docs/DEPLOYMENT_CHECKLIST_circuit_breaker.md#一配置项检查"
          impact: "无法监控熔断器状态"
          action: "1. 检查 config.py 中 circuit_breaker 字段是否存在 2. 验证 BusinessMetricsCollector 是否正常初始化 3. 查看 /metrics 端点输出"
```

（无 `keep_firing_for`；labels 只有上面三条，表达式不带任何相等匹配器。）

#### 3.3.2 判定式结论：为什么它不会 resolved

**一句话判定：不是"指标恢复了但告警没跟着 resolved"，而是这两条序列从头到尾就没存在过** ——
`absent()` 恒为 1，告警自然永远清不掉。理由链（每一步都指到具体表达式 / 代码行 / Prometheus 语义）：

1. **`or` 是并集**：`absent(A) or absent(B)` **只要 A、B 有一个不存在，结果就非空**。
   所以这条规则"指标回来就 resolved"的前提是 **A、B 两条序列都必须存在**。
2. **B 是 counter，天然可以不存在**。`yunshu_circuit_breaker_trigger_total` 的唯一写入方是
   `agent/monitoring/business_metrics.py::_on_circuit_breaker_state`，而它只在 `old_state is not None`
   （= 真的发生了一次状态转换）时才记账。健康的熔断器长期停在 CLOSED、可能几天不转换
   ⇒ **B 可以几天不存在** ⇒ `or` 恒为 1。（这一点正是 §9.18 说的"长期误报"的形态。）
3. **更关键：A 在正常运行下也不存在**。应用导出走 `BusinessMetricsCollector.export_prometheus()`，
   一个"声明了但一条样本都没有"的指标族**只输出 `# HELP` / `# TYPE`，不输出样本行**
   （`agent/monitoring/business_metrics.py:2306-2323`）。Prometheus 只认样本行 ⇒
   **这种"空族"在 Prometheus 里根本没有序列**，与"指标管道坏了"在 PromQL 层面**完全不可区分**。
4. **而 A 的写入方在实践中从未被执行到**。`yunshu_circuit_breaker_state` 只有两条可达路径：
   `CircuitBreaker._set_state()`（状态转换）与 `get_circuit_breaker()` 访问点发布
   （2026-10-02 为治"长期误报"而加，见 `agent/circuit_breaker.py:680-687`）。
   本仓**没有任何生产路径会无条件调用 `get_circuit_breaker()`**——`agent/tool_calling.py` 只 `import` 不使用
   （第 220 行注明旧的 `self._circuit_breaker = CircuitBreaker(...)` 已删除），
   其余调用点（`guardrails/output_schema.py`、`cognitive/critic.py`、`guardrails/egress_chain.py`、
   `learning_budget.py` 等）都要走到具体业务分支才会触发。
   **实测印证**：正在运行的应用跑了 45 分钟真实流量（响应里有 `yunshu_skill_match_count`、
   `yunshu_wf_admission_rejected_total` 等真实样本），这两条熔断器序列仍然是 **0 条样本**（证据 1）
   ⇒ 这些调用点在本部署的实际流量下没有被走到 ⇒ 在"应用起来了、但没跑熔断器相关业务"的窗口里，**A 也不会出现**。
5. ⇒ T+269.7 s 之后 **Prometheus 侧没有任何"恢复"可谈**：恢复的是 `up` 与 HTTP 可达性，
   **不是熔断器的序列**。告警因此既不会 resolved，也无法自愈。

**同时排除掉的其它候选解释**（都实际核过，不是想当然）：

| 候选解释 | 判定 | 依据 |
|---|---|---|
| labels 漂移导致的新序列（旧序列挂着不消） | **排除** | `absent()` 不带相等匹配器，返回值恒为空标签集 `{}`，告警实例身份全程稳定，不会产生"新实例" |
| `for` / `keep_firing_for` 语义 | **排除** | 规则里**没有** `keep_firing_for`；`for: 5m` 只控制 pending→firing（89.7+300=389.7 正好对上） |
| Prometheus 端卡住 / 求值异常 | **排除** | 同窗口求值耗时 0.3–2.0 ms、间隔恒 30 s；同窗口 `PrometheusTargetMissing` 正常 resolved |
| 应用恢复后没抓到业务端点 | **排除** | `up{job="yunshu-business"}` 在 T+269.7 s 后全程为 1 |

#### 3.3.3 实测证据（2026-10-03 补采，可复现）

**证据 1 —— 活着的应用也没有这两条序列**（对**正在运行**的应用，PID 6148、监听 :5678、11:17:39 启动，
晚于 C-2 窗口；响应共 280 行）：

```powershell
> $c = (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5678/api/business/prometheus).Content
> ($c -split "`n") | Select-String 'circuit_breaker' | ForEach-Object { $_.Line }
# HELP yunshu_circuit_breaker_trigger_total 熔断器触发总次数
# TYPE yunshu_circuit_breaker_trigger_total counter
# HELP yunshu_circuit_breaker_state 熔断器当前状态
# TYPE yunshu_circuit_breaker_state gauge
```

⇒ 280 行里**只有这 4 行**是熔断器相关，**0 条样本行**。而同一响应里 `yunshu_skill_match_count`、
`yunshu_wf_admission_rejected_total`、`tfidf_scan_candidate_total_total` 等**都有真实样本**
⇒ 不是端点坏了，是这两条指标从来没被写过。

**证据 2 —— 机制本身是好的，只是没人调用**（本地直接走一次访问点）：

```powershell
> python -c "import agent.circuit_breaker as cb, agent.monitoring.business_metrics as bm; cb.get_circuit_breaker('c2probe'); print(chr(10).join(l for l in bm.get_business_metrics_collector().export_prometheus().splitlines() if 'circuit_breaker' in l))"
# HELP yunshu_circuit_breaker_trigger_total 熔断器触发总次数
# TYPE yunshu_circuit_breaker_trigger_total counter
# HELP yunshu_circuit_breaker_state 熔断器当前状态
# TYPE yunshu_circuit_breaker_state gauge
yunshu_circuit_breaker_state{breaker_name="c2probe",state="closed"} 1.0
```

⇒ 一旦真的走到访问点，**状态序列会出现、计数序列仍然不会**（因为没发生状态转换）。
这正好把缺陷一分为二：`or` 的表达式错误（②计数缺席就足以钉死）+ "访问点在生产上从未被执行"（①状态也缺席）。

**证据 3 —— 窗口起点事件**（`_scratch/c2/harness.log` 首行 + `c2_longrun_transitions.jsonl`）：

```
[06:57:20] Prometheus 就绪=False（等待 90s）；初始 up={'yunshu': 0.0, 'c2-probe': 1.0, 'prometheus': 1.0, 'grafana': 1.0, 'yunshu-business': 0.0}
[06:57:20] 告警状态跃迁：CircuitBreakerMetricsMissing -> pending（T+89.7s）
[07:02:20] 告警状态跃迁：CircuitBreakerMetricsMissing -> firing（T+389.7s）
```

⇒ 窗口**第一个样本**（T+89.7 s）应用就已经没在跑（`up=0`），pending 从第一次规则求值起即成立，
`for: 5m` 到点转 firing，之后再无任何跃迁直到窗口结束。

#### 3.3.4 修法与回滚

**本次落地（① 改表达式，低风险，零生产代码改动）**：`or` → `and`。

```yaml
-        expr: absent(yunshu_circuit_breaker_state) or absent(yunshu_circuit_breaker_trigger_total)
+        expr: absent(yunshu_circuit_breaker_state) and absent(yunshu_circuit_breaker_trigger_total)
```

- **为什么是它**：`A and B` 只在"两边都有匹配元素"时返回元素 ⇒ 只有**两条序列都不存在**才算
  "未采集到熔断器指标"，与规则自己的 `description` / `action`（config.py 字段被移除、模块未加载、
  `/metrics` 无输出）字面一致。原式的 `or` 是把"其中一条序列**天然**缺席"误当成了"指标缺失"，属逻辑写反，
  不是阈值调参问题。
- **为什么必须改、且必须现在改**：即使将来补齐了埋点，"计数序列"依然要等到**第一次真实状态转换**才诞生
  ⇒ 只要 `or` 还在，这条告警在健康系统里仍然是永久 firing。所以 ① 与 ② 是**两件事**，① 是必要条件。
- **回归防线**：`monitoring/prometheus/rules/circuit_breaker_alerts_test.yml`（见 §3.3.5）。
- **怎么回滚**：把 expr 里的 `and` 改回 `or`，并删掉 `monitoring/circuit_breaker_alerts.yml` 中该规则上方新增的
  注释段（改动是"一段注释 + 一个词"，回滚同样精确）；回滚后夹具用例 1 会**立刻重新变红** ——
  也就是说这次的回滚/回归是**可被测试发现**的，不靠人记。
- **影响面**：规则语义只在"一条序列在、另一条不在"这一种情形上发生变化（由"告警"变为"不告警"）；
  "整族都不存在"的真阳性**完全保留**（夹具用例 2 修前修后都 SUCCESS）。
  该规则不在 `monitoring/prometheus.yml` 的 `rule_files` 之外新增任何文件，**compose 挂载与 rule_files 均无需改动**。

**剩余风险（**本次未解决**，需要 ②）**：实测（证据 1）在真实运行的应用里，**A 与 B 两条序列都不存在**。
所以 ① 单独**不足以**让 C-2 那一次告警 resolved —— 它只保证"计数序列天然缺席"不再单独钉死告警。
要让这条规则在 C-2 那种场景下真的能 resolved，必须让"熔断器指标族"在应用起来之后**至少有一条样本**。

**② 方案（需单独立项，本次按约定\"只写结论与方案、不动生产代码\"）**：

| 项 | 内容 |
|---|---|
| 改哪里 | `agent/monitoring/business_metrics.py`：在 `export_prometheus()` 里对 `metric_type == "gauge"` 的族保证至少输出一条样本（或在应用启动钩子里遍历已注册熔断器调用一次 `update_circuit_breaker_state`）；也可在 `agent/circuit_breaker.py::get_circuit_breaker` 之外增加一个"启动时发布全部已注册熔断器状态"的入口 |
| 为什么低风险 | 只**增加**一条 `state="closed"` 的样本行，不改任何判定逻辑；`set_gauge` 幂等，熔断器主路径已有异常隔离（`_notify_state_observer` 吞异常） |
| 影响面 | ① `/api/business/prometheus` 与 `/metrics` 输出会多 1 条（或按熔断器数量 N 条）样本；② Prometheus `head_series` 会增加约 N 条（相对本窗口 +312 序列/小时**可忽略**）；③ 依赖"指标缺失"语义的**只有这一条**规则；④ 需要同时确认 Grafana 面板 `grafana_circuit_breaker_dashboard.json` 不因多一条样本而变形（它按 `state` 分面，预期不变） |
| 验证方式 | 在 ② 落地后，把本夹具用例 1 的 input_series 换成"真的跑起来的小时级观测"，或直接重跑一次长跑，断言 `CircuitBreakerMetricsMissing` 在应用恢复后 resolved |
| 备选（不推荐，需产品决策） | 若判定"熔断器指标在正常运行时本就无序列"不可接受，也可以**退役**这条规则（§9.18 已判它长期误报）。代价 = 失去"熔断器配置被移除"的唯一信号；收益 = 少一条永久 warning。**本次不做此选择**，因为它同样要改 `rule_files`/compose 三处联动，超出收尾范围 |

#### 3.3.5 复现夹具与红→绿证据

夹具：`monitoring/prometheus/rules/circuit_breaker_alerts_test.yml`（2 条用例）。
运行（仓库根目录，PowerShell；promtool 把 `rule_files` 的相对路径按**夹具所在目录**解析，
所以挂仓库 `monitoring/` 而不是只挂 `rules/`）：

```powershell
docker run --rm --entrypoint promtool `
  -v ${PWD}\monitoring:/monitoring `
  prom/prometheus:v2.51.0 test rules /monitoring/prometheus/rules/circuit_breaker_alerts_test.yml
```

**修前（红灯 —— 复现成功）**：

```
Unit Testing:  /monitoring/prometheus/rules/circuit_breaker_alerts_test.yml


  FAILED:
    name: CircuitBreakerMetricsMissing_must_not_fire_when_state_series_present,
    alertname: CircuitBreakerMetricsMissing, time: 12m,
        exp:[],
        got:[
            0:
              Labels:{alertname="CircuitBreakerMetricsMissing", category="config", service="circuit_breaker", severity="warning"}
              Annotations:{action="1. 检查 config.py 中 circuit_breaker 字段是否存在 2. 验证 BusinessMetricsCollector 是否正常初始化 3. 查看 /metrics 端点输出", description="过去 5 分钟未采集到熔断器指标，配置可能被移除或模块未加载", impact="无法监控熔断器状态", runbook_url="docs/DEPLOYMENT_CHECKLIST_circuit_breaker.md#一配置项检查", summary="熔断器指标缺失"}
            ]
EXIT=1
```

**修后（绿灯）**：

```
Unit Testing:  /monitoring/prometheus/rules/circuit_breaker_alerts_test.yml
  SUCCESS

EXIT=0
```

用例 2（整族都不存在 ⇒ 必须 firing）修前修后都是 SUCCESS ⇒ 修改**没有把真阳性一起修没**。

### 3.4 全部跃迁（前 40 条，原始事件流见 `c2_longrun_transitions.jsonl`）

| T+秒 | 告警 | 状态 |
|---|---|---|
| 89.7 | GrafanaDown | pending |
| 89.7 | YunshuServiceDown | pending |
| 89.7 | CircuitBreakerMetricsMissing | pending |
| 89.7 | YunshuDown | pending |
| 89.7 | PrometheusTargetMissing | pending |
| 149.7 | GrafanaDown | resolved |
| 149.7 | YunshuServiceDown | firing |
| 149.7 | YunshuSLOErrorBudgetBurnCritical | pending |
| 149.7 | YunshuDown | firing |
| 149.7 | PrometheusTargetMissing | firing |
| 149.7 | YunshuSLOErrorBudgetBurnRateHigh | pending |
| 209.7 | YunshuAvailabilityLow | pending |
| 269.7 | YunshuHighMemoryUsage | pending |
| 269.7 | YunshuDown | resolved |
| 269.7 | HighMemoryUsage | pending |
| 329.7 | C2ProbeLifecycle | pending |
| 329.7 | YunshuServiceDown | resolved |
| 329.7 | PrometheusTargetMissing | resolved |
| 389.7 | YunshuHighMemoryUsage | resolved |
| 389.7 | CircuitBreakerMetricsMissing | firing |
| 449.7 | C2ProbeLifecycle | firing |
| 569.7 | HighMemoryUsage | firing |
| 749.8 | HighMemoryUsage | resolved |
| 809.8 | YunshuAvailabilityLow | firing |
| 1109.8 | HighMemoryUsage | pending |
| 1169.8 | HighMemoryUsage | resolved |
| 1529.8 | C2ProbeLifecycle | resolved |
| 1709.8 | YunshuQualityCritical | pending |
| 1709.8 | YunshuHealthDropping | pending |
| 1769.8 | YunshuHealthDropping | resolved |
| 2309.8 | YunshuQualityCritical | firing |
| 2849.9 | YunshuQualityCritical | resolved |
| 3329.9 | SkillMatchP99High | pending |
| 3449.9 | SkillMatchP99Critical | pending |
| 3509.9 | SkillMatchP99Critical | resolved |
| 3509.9 | SkillMatchP99High | resolved |
| 3749.9 | YunshuSLOErrorBudgetBurnRateHigh | firing |
| 3809.9 | YunshuAvailabilityLow | resolved |
| 3869.9 | YunshuSLOErrorBudgetBurnRateHigh | resolved |
| 3929.9 | VeryHighCPUUsage | pending |

> **【2026-10-03 后续】** 本节的根因已闭环：① 表达式 `or` → `and`（+ `promtool` 夹具红转绿）；
> ② **埋点侧无条件发布熔断器状态序列**已在组合根 `app_server.py` 接通并**重启生效**——
> 现场实测 `/api/business/prometheus` 出现 4 条 `yunshu_circuit_breaker_state{...state="closed"} 1.0`
> （修复前 0 条）；证据 `live_circuit_breaker_samples.txt`，决策与边界见
> `监控死规则与陈旧看板清理_20261002.md` §12.6。

## 4. 规则求值健康（48 个每 5 分钟样本）

| 观测项 | 值 |
|---|---|
| 规则组单轮求值耗时 | 首 0.3 ms / 末 1.1 ms / **max 2.0 ms** ⇒ 无劣化趋势 |
| 求值间隔 | 恒 **30 s**（与配置一致，239 分钟内无漂移） |
| `last_evaluation` 推进 | 14110 s vs 采样跨度 14102 s（偏差 9 s）⇒ **规则求值全程未卡住** |
| `health != ok` 的规则 | 仅**首次采样**（T+0）出现过 1 次：`yunshu_v6_query_pattern_p2` 组的 `YunshuV6FallbackFrequent` 为 `unknown`（首次求值前的瞬态），之后 47 个样本全 ok |
| 规则组总数 | 37 组 / 93 条规则（= 仓库现有 92 条 + 1 条探针）—— 与删除 15 条后的账**一致** |

## 5. 附带发现（与 C-2 同批观测到，值得记）

1. **`CircuitBreakerMetricsMissing` 不会自动 resolve**（根因、修法与红→绿夹具见 3.3）——「有告警没人知道」的反面：
   「有告警一直响」同样会让人麻木。**根因不是「告警不消」，而是「熔断器这两条序列在正常运行下压根不存在」**：
   已按低风险路径改掉表达式里的 `or`（→ `and`），但要让它在真实场景下能 resolved，还需要在**埋点侧**
   无条件发布一次状态序列（方案与影响面见 3.3.4 的 ②，本次**未动生产代码**）。
2. **监控栈自身的开销会触发它自己的告警**：窗口内 `HighMemoryUsage` 反复 pending/firing（4 轮）、
   `HighCPUUsage` / `VeryHighCPUUsage` / `YunshuHighCPUUsage` 也各触发过。
   本次窗口里同时发生的事：监控栈上线 + 应用被重新拉起 ⇒ **无法把责任单独归给监控栈**，
   但"上监控前先算它自己的内存占用（本机约数百 MB 量级）"是站得住的运维结论。
3. **SLO/质量类规则真的会响**：`YunshuSLOErrorBudgetBurnRateHigh`、`YunshuQualityCritical`、
   `YunshuAvailabilityLow`、`SkillMatchP99High/Critical`、`HighLatency` 系列
   都发生了 pending→firing→resolved 的完整跃迁 ⇒ 这些规则**不是死规则**（与"删掉 15 条恒不触发"形成对照）。

## 6. 本次没回答的（如实登记）

- **磁盘占用的月度外推**：需要采 15 天以上并同时采 `storage_blocks_bytes` 与卷实际占用；
- **WAL checkpoint 之后的形态**：本窗口 4 小时 < 默认 2 小时 checkpoint 周期里的一次完整循环观察不足；
- ~~**`prometheus_tsdb_head_samples_appended_total` 恒 0 的原因**（未查）~~ → **2026-10-03 已查明并更正，见 §2 更正记录**
  （不是指标停用/改名，是长跑脚本的字典键碰撞；正确判据 = `sum(prometheus_tsdb_head_samples_appended_total)`）；
- **告警通知**投递链路另见 §6.1（C-1），本次长跑**没有**配 Alertmanager（栈里没有该服务）。
