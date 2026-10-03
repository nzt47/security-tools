# 监控栈长跑验证装置（C-2）

> 交付报告 §10 的 **C-2**（「只在一次性启动下验证过」）就是靠这套装置回答的。
> 结论与完整数据见 `docs/closeout/监控清理_evidence_20261002/c2_longrun_report.md`
> （2026-10-03 实测 3.97 小时：239 个逐分钟样本 + 48 个规则健康样本）。

## 它验证什么

| 问号 | 怎么答 |
|---|---|
| 抓取会不会漂移 / 丢 | 逐分钟记每个 job 的 `up` / `scrape_duration_seconds` / `count_over_time(up[5m])`，并核对采样间隔（宿主休眠会造成缺口） |
| TSDB 涨多快 | `head_series` / `head_chunks` / `storage_blocks_bytes` / WAL 大小的首末差与折合速率 |
| 告警真的会走完 pending→firing→resolved 吗 | 每轮快照 `/api/v1/alerts` 的 (alertname → state)，把状态跃迁记成事件流；**另有宿主侧受控探针**把这件事做成可重复实验 |
| 规则求值会不会卡 | `rule_health_sampler.py` 每 5 分钟记规则组求值耗时 / 间隔 / `last_evaluation` 推进 / `health != ok` |

## 怎么跑（**不改仓库任何监控配置**）

```bash
python scripts/monitoring_longrun/gen_derived_config.py     # 生成 _scratch/c2/ 下的派生 compose 与 prometheus.yml
python scripts/monitoring_longrun/c2_harness.py 14400       # 4 小时；每 60s 采样；跑完自动 down -v
python scripts/monitoring_longrun/rule_health_sampler.py    # 另开一个终端并行跑
python scripts/monitoring_longrun/analyze.py                # 跑完复核原始样本（自动汇总容易算错，见下）
```

- 派生 compose 与 `docker-compose.monitoring.yml` **逐行相同**，只把 `prometheus.yml` 指向派生配置
  （多一条探针规则 + 一个探针 scrape job）；用 `--project-directory` 指向仓库根，相对路径才不会错位。
- 探针 exporter 由 harness 进程自己在宿主 `:9105` 提供，Prometheus 容器按 `host.docker.internal` 抓它；
  到点把 gauge 从 0 拨到 1（默认 T+5min）/ 拨回 0（T+25min），配合规则的 `for: 2m` 就能看到完整三态。
- 原始样本落在 `_scratch/c2/`（该目录被 gitignore）；**报告**由 harness 收尾时写进
  `docs/closeout/监控清理_evidence_20261002/`。

## 两个坑（都实际踩过）

1. **自动汇总很容易把 `{job: value}` 当标量** ⇒ TSDB 增量会全算成 +0（第一版报告就这样错了，
   已在 `analyze.py` 里改成先取标量再算差；报告里也留了更正记录）。
3. **同一个查询会返回多条序列**：`prometheus_tsdb_head_samples_appended_total` 在 v2.51.0 上带
   `type` 标签（`float` 真实值 / `histogram` 恒 0），一次查询两条序列的 `job` 都是
   `prometheus` ⇒ 用 job 做字典键会**静默互相覆盖**（第一版就这样把 239/239 个样本记成了 0，
   还被当成"指标有问题"写进报告）。`query()` 现已改为"同键则用其余标签区分 + 记一条告警"，
   采样这类指标请直接写 `sum(...)`。

2. **告警「消失」= resolved**：`/api/v1/alerts` 只返回**当前**活跃的告警；若不显式处理
   「上一轮有、这一轮没了」，就会漏掉所有 resolved 跃迁（第一版报告就漏了探针那条 resolved）。
