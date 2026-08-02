# 语义埋点监控告警 Runbook — 新人快速上手

> **用途**：收到告警或面板异常时，按本文步骤逐项排查。每步含「执行 → 看结果 → 定位」。
> **前置**：已按 [semantic_monitoring_guide.md](./semantic_monitoring_guide.md) 部署监控组件；
> 本地已配置 kubectl（kind 集群 `monitoring` 命名空间）。
> **速查**：告警规则一览见 [四、告警规则](./semantic_monitoring_guide.md#四告警规则)。

---

## 0. 收到告警后的第一步

> 先确认告警是否**真实故障**，还是测试数据/维护操作干扰。

```bash
# 1. 告警规则是否在正常调度（看 eval 日志）
kubectl logs deploy/grafana -n monitoring --since=5m | grep -iE "rule_uid|evaluat"

# 2. 是否刚执行过日志注入（CronJob 每 5 分钟注入，会制造正常波动）
kubectl get pods -n monitoring | grep log-injector

# 3. 当前 5 分钟内是否有真实业务日志
kubectl exec -n monitoring mock-alert-webhook -- curl -s localhost:9093/api/v2/alerts
```

- 若来自 CronJob 注入数据 → 看对应面板数据是否在预期范围，多数是预期波动，等 5m 观察即可。
- 若面板无数据 → 进入 **场景 A（采集中断）**。
- 若告警反复出现且数据异常 → 进入对应告警场景继续排查。

---

## 1. 场景 A：语义埋点采集中断（metric-total-no-data / 面板全空）

> 现象：metric_total / layer_counts 面板 5 分钟无新数据点。

```bash
# Step 1. 注入是否执行（CronJob 最近一次状态）
kubectl get cronjob,jobs -n monitoring | grep log-injector
# 期望：Completed。若 Failed → 查看 job 日志定位
kubectl logs -n monitoring -l job-name=<失败job名> --tail=20

# Step 2. Promtail 是否采集到日志
kubectl logs -n monitoring -l app=promtail --tail=20
# 期望：出现 /var/log/yunshu 相关 tail 日志。若无 → Promtail 挂载/部署问题

# Step 3. 日志文件是否存在
kubectl exec -n monitoring $(kubectl get pods -n monitoring -l app=promtail -o name | head -1) -- \
  ls -la /var/log/yunshu/orchestrator/ 2>/dev/null

# Step 4. Loki 是否可查（port-forward 后直接查）
kubectl port-forward svc/loki-gateway -n monitoring 13100:3100 &
curl -s "http://127.0.0.1:13100/loki/api/v1/query_range" \
  --data-urlencode 'query=count_over_time({action="orchestrator.semantic.metric_total"}[5m])' \
  --data-urlencode 'start=0' | python -c "import json,sys; print('rows:', json.load(sys.stdin).get('data',{}).get('result',[]))"
```

**判定与处置**：

| 检查点 | 结果 | 处置 |
|---|---|---|
| CronJob Failed | 脚本报错 | `kubectl logs` 看报错，检查 ConfigMap 脚本与最新代码是否一致 |
| Promtail 无日志 | 采集链路断 | 检查 promtail DaemonSet：`kubectl get ds -n monitoring promtail`，重启：`kubectl rollout restart ds/promtail -n monitoring` |
| Loki 查询空 | Loki 无数据 | 检查 Loki Pod：`kubectl get pods -n monitoring -l app=loki`，查看日志 |
| 全部正常但面板空 | 面板/数据源问题 | 见 **场景 E：看板数据源问题** |

---

## 2. 场景 B：metric_total 分母异常波动（metric-total-abnormal-spike）

> 现象：当前 5m 均值 vs 历史 30m 均值相对偏差 > 50%（warning）。

```bash
# Step 1. 查看当前与历史的值（复算告警表达式）
kubectl port-forward svc/loki-gateway -n monitoring 13100:3100 &
CUR=$(curl -s "http://127.0.0.1:13100/loki/api/v1/query_range" \
  --data-urlencode 'query=sum(avg_over_time({action="orchestrator.semantic.metric_total"} |~ "semantic\[total=" | regexp "semantic\[total=(?P<total>[0-9.]+)" | unwrap total [5m]))' \
  --data-urlencode 'start=0' | python -c "import json,sys; r=json.load(sys.stdin).get('data',{}).get('result',[]); print(r[0]['values'][-1][1] if r else 0)")
echo "当前5m total=$CUR"

# Step 2. 是否并发量激增（正常业务峰值也会触发）
kubectl get pods -n monitoring | grep log-injector   # 排除注入干扰
kubectl logs deploy/grafana -n monitoring --since=5m | grep -i spike

# Step 3. 是否有慢调用/超时（LLM 相关）
# 若 spike 伴随 llm_error 上升 → 走场景 C 排查 LLM 链路
```

**处置**：确认是业务峰值 → 调高阈值（grafana-alerting.yaml 规则 B 的 `params: [0.5]`）或忽略；
确认是埋点逻辑 bug → 检查 `_record_intent_layer` 调用点（见维护手册六、关键不变量）。

---

## 3. 场景 C：llm_error 数量超阈值（llm-error-threshold）

> 现象：5m 内 llm_error 层累计 > 3 次（warning）。表示 LLM 调用失败偏多。

```bash
# Step 1. 确认是真实 LLM 错误还是测试注入
kubectl get pods -n monitoring | grep log-injector
# log-injector 注入的用例含 llm_error:1（--count 3 时每次仅 1 个），
# 若 5m 内多次执行可能累计超阈值 → 属测试数据，非故障

# Step 2. 真实错误：查 orchestrator 日志中的 LLM 失败
kubectl logs -n <app-namespace> -l app=yunshu-orchestrator --since=5m | grep -iE "LLM 调用失败|orchestrator.process.fail|llm_error"

# Step 3. 定位失败原因（超时/限流/Key 失效）
# 失败类型通常在 error 字段，常见：
#   - 超时 → 检查 LLM 服务端延迟（可用 scripts/llm_slow_call_loadtest.py 复现慢调用）
#   - 401/403 → API Key 失效
#   - 429 → 限流
```

**处置**：临时调高阈值避免噪音（`params: [3]` 改大）→ 排查 LLM 服务；
恢复后调回，并确认 `llm_error / llm` 错误率面板回落。

---

## 4. 场景 D：分母同步不变量被破坏（metric-total-denominator-drift）

> 现象：`metric_total != Σ layer_counts`（偏差 > 1，critical）。这是**埋点计数 bug**，
> 不是链路故障。对应不变量：`sum(layer_counts.values()) == metric_total`。

```bash
# Step 1. 复算 B（total 总和）与 C（各层总和），二者应相等
kubectl port-forward svc/loki-gateway -n monitoring 13100:3100 &
B=$(curl -s "http://127.0.0.1:13100/loki/api/v1/query_range" --data-urlencode 'query=sum(sum_over_time({action="orchestrator.semantic.metric_total"} |~ "semantic\[total=" | regexp "semantic\[total=(?P<total>[0-9.]+)" | unwrap total [5m]))' --data-urlencode 'start=0' | python -c "import json,sys; r=json.load(sys.stdin).get('data',{}).get('result',[]); print(int(r[0]['values'][-1][1]) if r else 0)")
C=$(curl -s "http://127.0.0.1:13100/loki/api/v1/query_range" --data-urlencode 'query=sum(sum_over_time({action="orchestrator.semantic.metric_total"} |~ "semantic\[total=" | regexp "\"rule\":\\s*(?P<rule>\\d+)" | unwrap rule [5m]))+sum(sum_over_time({action="orchestrator.semantic.metric_total"} |~ "semantic\[total=" | regexp "\"semantic\":\\s*(?P<semantic>\\d+)" | unwrap semantic [5m]))' --data-urlencode 'start=0' | python -c "import json,sys; r=json.load(sys.stdin).get('data',{}).get('result',[]); print(int(r[0]['values'][-1][1]) if r else 0)")
echo "B(total)=$B  C(rule+semantic)=$C"

# Step 2. 若 B≠C → 优先怀疑残留非规范化行（原始 JSON 行穿透了 |~ 过滤）
# 查询是否仍有原始 JSON 行混入（message 字段含 semantic[total= 的 JSON 行）
curl -s "http://127.0.0.1:13100/loki/api/v1/query_range" \
  --data-urlencode 'query={action="orchestrator.semantic.metric_total"} |~ "semantic\[total=" | regexp "^(?P<body>.*)" | line_format "{{.__error__}}" ' \
  --data-urlencode 'start=0' | python -c "import json,sys; r=json.load(sys.stdin).get('data',{}).get('result',[]); print('rows:', sum(len(s['values']) for s in r))"
```

**判定与处置**：

| B vs C | 判定 | 处置 |
|---|---|---|
| 相等 | 不变量成立，误报 | 观察是否瞬时偏差（聚合窗口对齐问题），确认后关闭告警 |
| 不等 | 埋点逻辑 bug | 检查 orchestrator.py `_record_intent_layer` 各调用点（rule/template/semantic/llm/reject/llm_error），确认层字符串与 prometheus.py 标签字面一致 |

> ⚠ 关键知识：Loki 的 `| regexp`（带命名组）是 **parser，不丢弃行**；
> 丢弃行必须用 line filter `|~`。所有查询已前置 `|~ "semantic\\[total="`（见维护手册 2.3/4.1）。

---

## 5. 场景 E：看板数据源 / LogQL 报错

| 报错 | 原因 | 处理 |
|---|---|---|
| `at least one named capture must be supplied` | `| regexp` 缺命名组 | 正则必须含 `(?P<name>...)` |
| `invalid char escape` | LogQL 转义层级错误 | 字面 `[` 写 `\\[`；`\s`/`\d` 写 `\\s`/`\\d` |
| `trailing backslash at end of expression` | 引号转义层级错误 | 匹配字面 `"` 写 `\\"` |
| 查询超时（`urlopen timed out`） | 使用了完整 FQDN | 同命名空间改用短域名 `loki-gateway:3100` |

```bash
# 数据源 uid 固定为 loki，确认 Grafana 数据源配置
# ⚠ admin/admin 为 kind 测试环境默认凭据（见 deploy/k8s/grafana.yaml），生产环境请改用实际管理员账号
curl -s -u admin:admin http://127.0.0.1:13000/api/datasources | python -c "import json,sys; [print(d['name'], d['uid'], d['url']) for d in json.load(sys.stdin) if d['type']=='loki']"
```

---

## 6. 维护与例行验证

### 6.1 例行巡检（每日）

```bash
# 一键验证监控组件部署状态（不实际部署）
bash scripts/deploy_monitoring_stack.sh --verify-only

# 手动触发一次 CronJob 验证 LogQL 正则稳定性
kubectl create job --from=cronjob/log-injector log-injector-manual -n monitoring
kubectl wait --for=condition=Complete job/log-injector-manual -n monitoring --timeout=120s \
  && echo "自检通过" || echo "自检失败"
kubectl delete job log-injector-manual -n monitoring
```

### 6.2 组件更新流程

```bash
# 修改告警规则/看板后
kubectl apply -f deploy/k8s/grafana-alerting.yaml
kubectl rollout restart deploy/grafana -n monitoring
kubectl logs deploy/grafana -n monitoring --since=1m | grep -iE "alert|provision"  # 确认加载

# 修改注入脚本后（ConfigMap 与脚本内容必须一致）
kubectl apply -f deploy/k8s/log-injector-cronjob.yaml
```

### 6.3 LLM 慢调用专项压测（配合场景 C 排查）

```bash
# 内嵌 mock 服务制造 >2s 慢调用，验证可观测性
python scripts/llm_slow_call_loadtest.py --mock --delay-ms 2500 --vus 8 --duration 30
```

---

## 7. 关键参考

| 内容 | 位置 |
|---|---|
| 维护手册（完整版） | [semantic_monitoring_guide.md](./semantic_monitoring_guide.md) |
| 告警规则 YAML | [deploy/k8s/grafana-alerting.yaml](../../deploy/k8s/grafana-alerting.yaml) |
| 注入 CronJob | [deploy/k8s/log-injector-cronjob.yaml](../../deploy/k8s/log-injector-cronjob.yaml) |
| 一键部署脚本 | [scripts/deploy_monitoring_stack.sh](../../scripts/deploy_monitoring_stack.sh) |
| 慢调用压测脚本 | [scripts/llm_slow_call_loadtest.py](../../scripts/llm_slow_call_loadtest.py) |
| Dashboard 模板 | [intent_layer_semantic_dashboard.json](../../deploy/monitoring/grafana/dashboards/intent_layer_semantic_dashboard.json) |
