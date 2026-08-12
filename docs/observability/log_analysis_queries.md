# EVO-T4 提示词优化日志分析查询（LogQL）

> 关联代码：`agent/cognitive/prompt_optimizer.py`（`[PromptOpt]` 日志）
> 关联配置：`deploy/k8s/promtail-evolution-log.yaml`（新建，见文末）
> 适用场景：按 `verdict` 统计建议产出率、监控 `no_samples` 异常、按技能类别分析

---

## 一、日志格式

`compare()` 输出的评估日志为**单行固定顺序 kv**（对齐现有 `module_name/action` 体系之外的文本流，
字段顺序固定便于 grok/regexp 提取）：

```text
[PromptOpt] 对比评估 prompt_id=prompt:search:xxx category=search orig_score=0.9997 orig_status=completed cand_score=0.9997 cand_status=completed improvement=0.0000 threshold=0.0300 verdict=no_improvement
[PromptOpt] 对比判定结果 prompt_id=prompt:search:xxx category=search status=proposed original=0.3 suggested=0.9997
```

`verdict` 三值枚举（日志分析的核心分组维度）：

| verdict | 含义 |
|---|---|
| `proposed` | 提升超阈值，产出建议版（待审批，不自动应用） |
| `no_improvement` | 提升未达阈值，不产出建议 |
| `no_samples` | 该类别无评估样本，降级不产伪建议 |

---

## 二、采集前提（新建 promtail job）

现有三个 promtail 配置（`promtail-structured-log.yaml` / `promtail-knowledge-log.yaml` /
`promtail-pipeline-verify.yaml`）均只解析各自 JSON 结构化日志流（`action` 过滤），
**`[PromptOpt]` 文本日志不在任何现有 pipeline 解析范围内**。

未来启用采集需**新建** `deploy/k8s/promtail-evolution-log.yaml`（独立 job，不污染现有流），核心片段：

```yaml
scrape_configs:
  - job_name: yunshu-evolution
    static_configs:
      - targets:
          - localhost
        labels:
          job: yunshu-evolution
          __path__: /var/log/yunshu/evolution/*.log   # ⚠ 按实际日志落盘目录调整
    pipeline_stages:
      # 1. 提取 verdict 为低基数 label（只做提取不改写）
      - regex:
          expression: "verdict=(?P<verdict>[a-z_]+)"
      - labels:
          verdict: verdict
      # 2. 规范化 message，便于 LogQL 直接展示
      - template:
          source: message
          template: 'prompt_opt[verdict={{ .verdict }}]'
      - output:
          source: message
```

> ⚠ 提示：`category`（如 search/chat）为低基数可照同样方式提取为 label；
> `prompt_id` 为高基数字段，**禁止设 label**（会撑爆 Loki 索引），查询时用 `| regexp` 解析。

---

## 三、LogQL 查询示例

### 1. 按 verdict 统计建议产出率（核心查询）

建议产出率 = `proposed` 事件速率 / 总评估事件速率：

```logql
sum(rate({job="yunshu-evolution", verdict="proposed"}[5m]))
/
sum(rate({job="yunshu-evolution"}[5m]))
```

Grafana 面板配置：Stat 面板，单位 `percentunit`（0.05 = 5% 产出率）。

### 2. 各 verdict 频率分布（堆叠柱状图）

```logql
sum by (verdict) (rate({job="yunshu-evolution"}[5m]))
```

### 3. 各 verdict 占比（比例堆叠，验证分母 = 1）

```logql
sum by (verdict) (rate({job="yunshu-evolution"}[5m]))
/ scalar(sum(rate({job="yunshu-evolution"}[5m])))
```

### 4. 按技能类别（category）分析产出率

需先在 promtail 侧将 `category` 提取为 label（见上文提示），或运行时解析：

```logql
sum by (category) (rate({job="yunshu-evolution"}
  | regexp "category=(?P<category>[a-z_]+) verdict=(?P<verdict>proposed)" [5m]))
```

### 5. 无样本异常监控（no_samples 突增告警）

`no_samples` 占比超 50% 即告警（多类别缺样本，提示评估覆盖不足）：

```logql
sum(rate({job="yunshu-evolution", verdict="no_samples"}[15m]))
/ sum(rate({job="yunshu-evolution"}[15m]))
> 0.5
```

### 6. 纯 LogQL 提取（promtail 未设 label 时的兜底写法）

```logql
sum by (verdict) (rate({job="yunshu-evolution"}
  | regexp "verdict=(?P<verdict>[a-z_]+)" [5m]))
```

---

## 四、启用采集的部署步骤

1. 新建 `deploy/k8s/promtail-evolution-log.yaml`（第二节片段，调整 `__path__` 为实际落盘目录）；
2. 创建 ConfigMap 并挂载到 promtail DaemonSet：
   ```bash
   kubectl create configmap promtail-evolution-log \
     --from-file=deploy/k8s/promtail-evolution-log.yaml -n monitoring
   ```
3. 参照 `promtail-pipeline-verify.yaml` 用 `promtail -dry-run` 验证解析；
4. Grafana 新增面板，套用第三节查询。

---

## 五、部署检查清单（启用采集前逐项确认）

- [ ] **路径**：`deploy/k8s/promtail-evolution-log.yaml` 的 `__path__` 已按实际日志落盘目录调整（默认 `/var/log/yunshu/evolution/*.log` 为 K8s 约定路径）
- [ ] **ConfigMap**：`kubectl create configmap promtail-evolution-log --from-file=...` 成功，key 为源文件名 `promtail-evolution-log.yaml`
- [ ] **DaemonSet 挂载**：promtail DaemonSet 已添加 volume/volumeMount，ConfigMap 映射到 `/etc/promtail/evolution-log.yaml`（参照 promtail-structured-log.yaml 的 items 写法）
- [ ] **dry-run 验证**：`promtail -config.file=/etc/promtail/evolution-log.yaml -dry-run` 消费一条真实 `[PromptOpt]` 日志，`verdict` / `category` label 提取正确（3 类判定 proposed / no_improvement / no_samples 各验一条）
- [ ] **混流检查**：日志文件若混入非 `[PromptOpt]` 行，确认 regex 不匹配行无 label 且不污染（可用 LogQL 示例 5 兜底过滤）
- [ ] **label 基数**：`verdict` / `category` 已设 label；`prompt_id`、`orig_score` 等高基数/数值字段确认未设 label
- [ ] **告警接入**：no_samples 突增告警查询已在 Grafana Alerting 或 Prometheus 配置（文档第三节示例 5）
- [ ] **面板验证**：建议产出率 Stat 面板（`sum(rate({job="yunshu-evolution", verdict="proposed"}[5m])) / sum(rate({job="yunshu-evolution"}[5m]))`）有数据且数值合理（0~1 之间）

---

## 六、相关文档

- 日志格式设计说明：EVO-T4 任务规格 `docs/zh/进化机制重构计划/04_上下文与知识进化闭环.md`
- 现有采集配置参照：`deploy/k8s/promtail-structured-log.yaml`、`deploy/k8s/promtail-knowledge-log.yaml`
