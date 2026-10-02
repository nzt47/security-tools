# 证据文件（2026-10-02 监控死指标清理）

配套报告：`docs/closeout/监控死规则与陈旧看板清理_20261002.md`（按《能力层重构交付报告》§10 接手）。
三个文件都是**现场采集的原始输出**，不是复述。

| 文件 | 是什么 | 怎么复现 |
|---|---|---|
| `live_metric_names.txt` | 本部署**真实存在**的 186 个指标名（去重） | `GET http://127.0.0.1:5678/metrics` 与 `GET http://127.0.0.1:5678/api/business/prometheus` 两份文本的**样本行 + # HELP/# TYPE 声明行**合并去重 |
| `dashboard_metric_scan_after.txt` | 修复后逐看板扫描结果（每个看板引用的 `yunshu_*` / `Yunshu_*` 名字里有几个不在这 186 个里） | 见报告 §6；修复前同一脚本给出的"不可解析"计数是 18 处 |
| `promtool_check_config.txt` | Prometheus 配置 + 14 个规则文件的校验输出（修正后仍是 `EXIT=0`） | `docker run --rm --entrypoint promtool` + 按 `docker-compose.monitoring.yml` 的 11 个挂载逐条复刻，最后一行带 `EXIT=0` |

【为什么要落盘】判据 `live_metric_names.txt` 原本只存在于被 gitignore 的 `_scratch/` 里，
而报告与看板守卫都以它为"什么叫真实指标名"的基准 —— 不落盘等于下次没人能复核。
