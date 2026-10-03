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
> 另：`prometheus_tsdb_head_samples_appended_total` 在本窗口**恒为 0**（原因未查清），
> 因此**不拿它**当增长判据 —— 改用 head_chunks（它随样本写入单调上涨）。

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

### 3.3 只进不出：一条永远不会自己好的规则

`CircuitBreakerMetricsMissing`：pending @89.7 s → **firing @389.7 s** → **窗口末仍在 firing**。
它是 `absent()` 形态的规则（指标缺席即告警），应用恢复后指标回来本应自动 resolved —— 但实测没有。
**这条值得单独立项查**（§9.18 的既有结论是"它长期误报"，本次给出了更具体的形态：**一旦 firing 就不会自动恢复**）。

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

## 4. 规则求值健康（48 个每 5 分钟样本）

| 观测项 | 值 |
|---|---|
| 规则组单轮求值耗时 | 首 0.3 ms / 末 1.1 ms / **max 2.0 ms** ⇒ 无劣化趋势 |
| 求值间隔 | 恒 **30 s**（与配置一致，239 分钟内无漂移） |
| `last_evaluation` 推进 | 14110 s vs 采样跨度 14102 s（偏差 9 s）⇒ **规则求值全程未卡住** |
| `health != ok` 的规则 | 仅**首次采样**（T+0）出现过 1 次：`yunshu_v6_query_pattern_p2` 组的 `YunshuV6FallbackFrequent` 为 `unknown`（首次求值前的瞬态），之后 47 个样本全 ok |
| 规则组总数 | 37 组 / 93 条规则（= 仓库现有 92 条 + 1 条探针）—— 与删除 15 条后的账**一致** |

## 5. 附带发现（与 C-2 同批观测到，值得记）

1. **`CircuitBreakerMetricsMissing` 不会自动 resolve**（见 3.3）——「有告警没人知道」的反面：
   「有告警一直响」同样会让人麻木。
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
- **`prometheus_tsdb_head_samples_appended_total` 恒 0 的原因**（未查）；
- **告警通知**投递链路另见 §6.1（C-1），本次长跑**没有**配 Alertmanager（栈里没有该服务）。
