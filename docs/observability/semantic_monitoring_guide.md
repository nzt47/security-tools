# 意图层 Semantic 埋点监控 — Dashboard / 脚本 / 告警维护手册

> 适用对象：监控链路维护与值班团队
> 覆盖内容：Grafana Dashboard JSON 模板、日志注入脚本、告警规则 YAML 的用途、部署与排障
> 关联埋点规范：[intent_routing_logging.md](./intent_routing_logging.md)

---

## 一、数据链路总览

```
orchestrator.py (semantic 埋点, L1086-1108)
        │  输出原始 JSON 日志（module_name/action/metric_total/layer_counts/...）
        ▼
Promtail DaemonSet (deploy/k8s/promtail-daemonset.yaml)
        │  采集 hostPath /var/log/yunshu，pipeline template+output 规范化为：
        │  semantic[total=5][skill=...][score=...][instr_len=...][loaded=true][layers={...}]
        ▼
Loki (deploy/k8s/loki-standalone.yaml, svc: loki-gateway:3100)
        │
        ├── Grafana Dashboard  :  intent-layer-semantic（7 个面板）
        ├── Grafana 告警规则    :  semantic-metric-alerts（4 条，见 grafana-alerting.yaml）
        └── 稳定性自检脚本     :  periodic_log_injector.py（CronJob 每 5 分钟注入+验证）
```

关键文件清单：

| 文件 | 作用 |
|---|---|
| `deploy/monitoring/grafana/dashboards/intent_layer_semantic_dashboard.json` | Dashboard 模板（源文件） |
| `deploy/k8s/grafana.yaml` | Grafana 部署 + 数据源 + 看板 provisioning（内含 Dashboard JSON 副本） |
| `deploy/k8s/grafana-alerting.yaml` | 4 条告警规则 + webhook 通知策略 |
| `deploy/k8s/log-injector-cronjob.yaml` | 日志注入 CronJob + 脚本 ConfigMap |
| `scripts/send_semantic_logs.py` | 手动注入脚本（file/push 两模式） |
| `scripts/periodic_log_injector.py` | 定期注入 + LogQL 自检脚本（CronJob 使用） |
| `deploy/k8s/mock-webhook-pod.yaml` | 告警 webhook 接收验证器（mock-alert-webhook） |

---

## 二、Dashboard JSON 模板

### 2.1 基本信息

- 标题：`意图层 Semantic 埋点解析监控`，UID：`intent-layer-semantic`
- 数据源：Loki（uid 固定为 `loki`），刷新 `10s`
- 默认时间范围：`now-6h`

### 2.2 面板清单

| 面板 | 类型 | 查询要点 |
|---|---|---|
| 1 埋点触发速率 | timeseries | `rate({action="orchestrator.semantic.metric_total"}[$__rate_interval])` |
| 2 metric_total 分母值 | timeseries | `avg_over_time(unwrap total)`，正则 `semantic\[total=(?P<total>[0-9.]+)` |
| 3 layer_counts 各层计数 | timeseries（堆叠） | 7 个子查询 unwrap rule/semantic/llm/reject/template/llm_error/llm_low_confidence_fallback |
| 4 instruction_loaded 分布 | piechart | 按 label `instruction_loaded` 计数 |
| 5 top1_score 均值 | timeseries | `avg_over_time(unwrap score)`，阈值线 0.3（误召回判定） |
| 6 instruction_len 趋势 | timeseries | `max_over_time(unwrap instr_len)` |
| 7 低分 skill 异常日志 | logs | 正则提取 skill + score，`score < 0.3` 过滤 |

### 2.3 LogQL 正则约定（【不易】务必遵守）

1. **Loki `| regexp` 必须包含至少一个命名捕获组**（如 `(?P<total>\d+)`），否则报
   `at least one named capture must be supplied`（HTTP 400）。
2. **`| regexp`（带命名组）是 parser，不丢弃不匹配行**——行保留、字段为空。
   真正丢弃行需用 line filter **`|~ "pattern"`**。
3. **正则匹配的是行内任意子串（部分匹配）**，需用强锚点或前置 `|~` 确保口径唯一。
4. **转义层级**：YAML/Python 源码中 `\\` → LogQL 字符串 `\` → 正则模式字面量。
   例：匹配字面 `[` 需写 `\\[`（LogQL 层）。

### 2.4 部署方式

```bash
# 方式一：整体部署 Grafana（含数据源、看板、告警）
kubectl apply -f deploy/k8s/grafana.yaml
kubectl apply -f deploy/k8s/grafana-alerting.yaml

# 方式二：仅更新 Dashboard JSON（修改源文件后手动同步到 grafana.yaml 的 ConfigMap）
kubectl create configmap grafana-dashboard-json -n monitoring \
  --from-file=intent_layer_semantic_dashboard.json=deploy/monitoring/grafana/dashboards/intent_layer_semantic_dashboard.json \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl rollout restart deploy/grafana -n monitoring
```

---

## 三、日志注入脚本

### 3.1 手动验证脚本 `scripts/send_semantic_logs.py`

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--mode` | `file` | `file` 写原始 JSON 由 Promtail 采集（推荐，全链路）；`push` 直推 Loki |
| `--log-dir` | `/var/log/yunshu/orchestrator` | file 模式输出目录 |
| `--loki-url` | `http://loki-gateway.monitoring.svc.cluster.local/...` | push 模式目标 |
| `--count` | `None`（全部 3 个用例） | 生成条数 |
| `--dry-run` | 关闭 | 仅打印不写入 |

```bash
# 本地 dry-run
python scripts/send_semantic_logs.py --dry-run

# 集群内 file 模式（10 条，由 Promtail 采集）
kubectl cp scripts/send_semantic_logs.py -n monitoring <pod>:/tmp/send_semantic_logs.py
kubectl exec -n monitoring <pod> -- python3 /tmp/send_semantic_logs.py --mode file --count 10
```

### 3.2 定期注入 + 自检脚本 `scripts/periodic_log_injector.py`

在注入基础上增加 **LogQL 正则稳定性自检**：注入后执行 3 条与告警/面板口径一致的查询
（metric_total / llm_error / layer_counts rule），任一失败则退出码 1。

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--mode` | `file` | 注入模式 |
| `--count` | `10` | 每次注入条数 |
| `--loki-url` | `http://loki-gateway:3100/...` | **短域名**（kind 集群完整 FQDN 解析会超时） |
| `--window` | `5` | 自检窗口分钟 |
| `--no-verify` | 关闭 | 跳过自检 |

```bash
# 本地经 port-forward 验证
python scripts/periodic_log_injector.py --mode push --count 5 --verify \
  --loki-url http://127.0.0.1:13100/loki/api/v1/query_range
```

### 3.3 CronJob 周期执行 `deploy/k8s/log-injector-cronjob.yaml`

- 调度：`*/5 * * * *`（每 5 分钟，间隔小于告警规则 A 的 5m 窗口，避免 no-data 误报）
- 镜像：`docker.io/library/skill-retrieval:local`（节点本地镜像，含 python3.11.15）
- 行为：写原始 JSON 到 hostPath `/var/log/yunshu/orchestrator` → Promtail 采集 →
  自检 3 条 LogQL → 失败退出码 1（CronJob 显示 Failed）

```bash
kubectl apply -f deploy/k8s/log-injector-cronjob.yaml

# 手动触发一次
kubectl create job --from=cronjob/log-injector log-injector-manual -n monitoring

# 查看执行结果
kubectl get cronjob, jobs -n monitoring | grep log-injector
kubectl logs -n monitoring -l job-name=log-injector-manual
```

> ⚠ 修改脚本后需同步 CronJob ConfigMap（`kubectl apply -f log-injector-cronjob.yaml`），
> 二者内容必须一致（ConfigMap 中脚本带"来源"注释）。

---

## 四、告警规则 `deploy/k8s/grafana-alerting.yaml`

数据源：Loki（uid `loki`），格式：Grafana Unified Alerting provisioning
（挂载到 `/etc/grafana/provisioning/alerting/`）。

| 规则 | severity | 触发条件 | 说明 |
|---|---|---|---|
| A semantic-metric-no-data | critical | 5m 速率 < 0.001 | 埋点链路中断（Promtail/Loki/应用） |
| B metric-total-abnormal-spike | warning | 当前 5m vs 历史 30m 相对偏差 > 50% | 分母异常波动 |
| C llm-error-threshold | warning | 5m 累计 llm_error > 3 | LLM 调用失败偏多 |
| D metric-total-denominator-drift | critical | abs(metric_total − Σ layer_counts) > 1 | 分母同步不变量被破坏 |

通知：默认 webhook → `http://mock-alert-webhook.monitoring:9093/api/v2/alerts`
（可替换为邮件/钉钉等接收器）。

### 4.1 误报修复记录（规则 D，2026-08-02）

- **现象**：规则 D 误报，Σ layer_counts（215）> metric_total（173），差 42。
- **根因**：Loki 中残留 Promtail 修复前的**原始 JSON 行**（message 字段含
  `semantic[total=25` 子串）。由于 `| regexp`（带命名组）**不丢弃不匹配行**，
  第二层 `"rule":` 正则会穿透到原始 JSON 行的 `layer_counts` 字段，层计数虚高。
- **修复**：所有 Loki 查询前置 line filter `|~ "semantic\\[total="`，先丢弃非规范化行，
  再进行 regexp 提取与 unwrap。
- **验证**：修复后 B=Σlayer_counts（131 = 28+44+35+9+9+3+3），不变量恢复。

---

## 五、排障指南

### 5.1 Loki 报错速查

| 报错 | 原因 | 处理 |
|---|---|---|
| `at least one named capture must be supplied` | `| regexp` 缺命名组 | 正则必须含 `(?P<name>...)` |
| `invalid char escape` | LogQL 字符串中转义层级错误 | 字面 `[` 写 `\\[`；`\s`/`\d` 写 `\\s`/`\\d` |
| `trailing backslash at end of expression` | 引号转义层级错误 | 匹配字面 `"` 写 `\\"`（LogQL 层为 `\"`） |
| 查询超时（`urlopen timed out`） | 使用了完整 FQDN | 同命名空间改用短域名 `loki-gateway:3100` |

### 5.2 数据链路排查

1. 注入是否生效：`kubectl get pods -n monitoring | grep log-injector`（Completed）
2. Promtail 是否采集：`kubectl logs -n monitoring -l app=promtail --tail=20`
3. Loki 是否有数据：port-forward 后查询
   `sum(count_over_time({action="orchestrator.semantic.metric_total"}[5m]))`
4. 告警是否调度：`kubectl logs deploy/grafana -n monitoring | grep -i "rule_uid"`

### 5.3 规则 D 复验（分母同步不变量）

```bash
# B（total 总和）与 C（各层总和）应相等
# C = rule+semantic+llm+reject+template+llm_error+llm_low_confidence_fallback
sum(sum_over_time({action="orchestrator.semantic.metric_total"} |~ "semantic\\[total=" | regexp "semantic\\[total=(?P<total>[0-9.]+)" | unwrap total [5m]))
```
两者一致即不变量成立；不一致时优先怀疑残留非规范化行（检查 `|~` 前置过滤）。

---

## 六、关键不变量（【不易】）

1. **分母同步**：`metric_total == Σ layer_counts`，ratio 总和恒 = 1.0。
2. **规范行格式唯一**：Loki 中只应有 `semantic[total=...]...` 规范化行，
   不得混入原始 JSON 行（原始 JSON 的 `layer_counts` 会干扰层计数正则）。
3. **regexp 语义**：`| regexp` 是 parser（保留行），`|~` 才是 line filter（丢弃行）。
