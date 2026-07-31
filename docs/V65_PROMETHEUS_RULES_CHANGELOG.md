# Prometheus 规则配置变更日志与回滚手册

> **变更编号**: CHG-2026-0729-PROM-RULES
> **变更类型**: 配置变更（prometheus.yml rule_files 引用补全）
> **风险等级**: 中（影响生产告警加载，但可秒级回滚）
> **关联文档**: [V65_ALERT_FALSE_POSITIVE_ANALYSIS.md](./V65_ALERT_FALSE_POSITIVE_ANALYSIS.md)、[reload_prometheus_rules.ps1](../scripts/reload_prometheus_rules.ps1)
> **关联脚本**: `scripts/reload_prometheus_rules.ps1 -FixRuleRefs`

---

## 1. 变更背景

### 1.1 问题发现

执行 `promtool check config` 时发现，`prometheus.yml` 的 `rule_files` **仅引用 `alert_rules.yml`**，未引用 `rules/` 子目录下的两个规则文件：

| 规则文件 | 规则数 | 是否被引用 | 影响 |
|----------|--------|-----------|------|
| `alert_rules.yml` | 8 | ✅ 已引用 | 正常加载 |
| `rules/reranker-alerts.yml` | 8 | ❌ 未引用 | **v6.5 ONNX 监控告警全部失效** |
| `rules/yunshu-v6-query-pattern-alerts.yml` | 14 | ❌ 未引用 | **查询模式告警全部失效** |

### 1.2 变更动机

- 上一轮已完成 `reranker-alerts.yml` 的误报防护修订（P1-2 P99 突增规则添加冷启动/低流量保护），但修订后的规则实际未被 Prometheus 加载
- 生产环境 ONNX 推理故障（P0）和延迟突增（P1）将无法触发告警，存在监控盲区
- 必须补全 `rule_files` 引用，使 22 条告警规则生效

---

## 2. 变更前基线

### 2.1 文件状态快照

| 文件 | Git 状态 | 说明 |
|------|---------|------|
| `monitoring/prometheus/prometheus.yml` | 已跟踪，工作区=HEAD | 待修改 |
| `monitoring/prometheus/rules/reranker-alerts.yml` | **未跟踪**（新增） | 不受 git checkout 影响 |
| `monitoring/prometheus/rules/yunshu-v6-query-pattern-alerts.yml` | 已跟踪 | 不受本次变更影响 |

### 2.2 prometheus.yml 变更前内容（第 15-16 行）

```yaml
rule_files:
  - 'alert_rules.yml'
```

### 2.3 Git 基线 commit

```
be80d89e feat: 云枢计划任务与心跳系统完整集成
```

回滚锚点：`git checkout be80d89e -- monitoring/prometheus/prometheus.yml`

---

## 3. 变更内容

### 3.1 变更 Diff

**文件**: `monitoring/prometheus/prometheus.yml`

```diff
 rule_files:
   - 'alert_rules.yml'
+  - 'rules/reranker-alerts.yml'
+  - 'rules/yunshu-v6-query-pattern-alerts.yml'
```

### 3.2 变更后预期内容

```yaml
rule_files:
  - 'alert_rules.yml'
  - 'rules/reranker-alerts.yml'
  - 'rules/yunshu-v6-query-pattern-alerts.yml'
```

### 3.3 变更影响范围

- **新增加载规则**: 22 条（8 reranker + 14 query-pattern）
- **累计加载规则**: 30 条（8 + 8 + 14）
- **告警分组**: P0(2) + P1(4) + P2(2) + query-pattern(14)
- **不影响项**: scrape_configs、alerting 配置不变

---

## 4. 执行步骤（含备份）

### 4.1 前置备份（必做）

```powershell
# 1. 备份 prometheus.yml（时间戳标记）
Copy-Item "monitoring\prometheus\prometheus.yml" `
          "monitoring\prometheus\prometheus.yml.bak.20260729"

# 2. 确认 git 基线干净（工作区无意外修改）
git status --short monitoring/prometheus/prometheus.yml
# 预期输出: 空（无修改）

# 3. 记录当前 commit hash（回滚锚点）
git rev-parse HEAD
# 记录输出: be80d89e...（或当前 HEAD）
```

### 4.2 执行变更

**方式 A：使用自动化脚本（推荐）**

```powershell
# 一键完成: 配置校验 → 追加引用 → 重新校验 → reload
.\scripts\reload_prometheus_rules.ps1 -FixRuleRefs
```

**方式 B：手动编辑**

```powershell
# 1. 编辑 prometheus.yml，在 rule_files 下追加两行
#    - 'rules/reranker-alerts.yml'
#    - 'rules/yunshu-v6-query-pattern-alerts.yml'

# 2. 校验配置
.\scripts\reload_prometheus_rules.ps1 -DryRun

# 3. 执行 reload
.\scripts\reload_prometheus_rules.ps1
```

### 4.3 验证步骤

```powershell
# 1. 确认规则已加载（应返回 30 条规则）
curl http://localhost:9090/api/v1/rules | python -m json.tool | findstr "name"

# 2. 确认 P0 告警规则存在
curl "http://localhost:9090/api/v1/rules?type=alert"

# 3. 检查 Prometheus 日志无加载错误
docker logs Yunshu-prometheus --tail 20 | findstr "error\|FAIL"

# 4. 确认告警状态页面可访问
# 浏览器打开: http://localhost:9090/alerts
```

---

## 5. 回滚操作手册

### 场景 A：reload 失败 / Prometheus 无法启动

**症状**: reload 后 Prometheus 容器异常退出或 9090 端口不可达

```powershell
# 1. 立即回滚 prometheus.yml
git checkout HEAD -- monitoring/prometheus/prometheus.yml
# 或使用备份
Copy-Item "monitoring\prometheus\prometheus.yml.bak.20260729" `
          "monitoring\prometheus\prometheus.yml" -Force

# 2. 校验回滚后配置
.\scripts\reload_prometheus_rules.ps1 -DryRun

# 3. 重启 Prometheus 容器
docker restart Yunshu-prometheus

# 4. 验证恢复
Start-Sleep 10
curl http://localhost:9090/-/healthy
# 预期: Prometheus is Healthy.
```

### 场景 B：规则加载后告警风暴（误报）

**症状**: 新增规则触发大量误报告警，需紧急禁用但保留 Prometheus 运行

```powershell
# 方案 1: 回滚 prometheus.yml + reload（推荐，秒级生效）
git checkout HEAD -- monitoring/prometheus/prometheus.yml
curl -X POST http://localhost:9090/-/reload
# 验证: 新增的 22 条规则从 /alerts 消失

# 方案 2: 仅临时静音告警（保留规则，不回滚配置）
# 通过 Alertmanager API 静音（如有配置 Alertmanager）
curl -X POST http://localhost:9093/api/v2/silences `
     -H "Content-Type: application/json" `
     -d '{"matchers":[{"name":"alertname","value":"RerankerP99LatencySpike","isRegex":false}],"startsAt":"2026-07-29T00:00:00Z","endsAt":"2026-07-30T00:00:00Z","createdBy":"ops","comment":"紧急静音-误报排查"}'
```

### 场景 C：手动编辑失误（YAML 语法错误）

**症状**: promtool check 失败，YAML 缩进/引号错误

```powershell
# 1. 对比备份差异，定位错误
Compare-Object (Get-Content monitoring\prometheus\prometheus.yml.bak.20260729) `
               (Get-Content monitoring\prometheus\prometheus.yml)

# 2. 直接恢复备份
Copy-Item "monitoring\prometheus\prometheus.yml.bak.20260729" `
          "monitoring\prometheus\prometheus.yml" -Force

# 3. 重新校验
.\scripts\reload_prometheus_rules.ps1 -DryRun
```

### 场景 D：规则文件本身有缺陷需回滚规则内容

**症状**: `reranker-alerts.yml` 内某条规则表达式错误，需回滚到修订前版本

```powershell
# 注: reranker-alerts.yml 是未跟踪文件，无 git 历史
# 回滚方式: 从上一轮会话的修订记录恢复

# 1. 查看误报分析文档中的修订前表达式
#    docs/V65_ALERT_FALSE_POSITIVE_ANALYSIS.md → "修订前" 列

# 2. 手动恢复特定规则（以 P1-2 为例）
#    将 reranker-alerts.yml 中 P1-2 的 expr 恢复为:
#      histogram_quantile(0.99, sum(rate(yunshu_rerank_duration_ms_bucket[5m])) by (le)) >
#      2 * histogram_quantile(0.99, sum(rate(yunshu_rerank_duration_ms_bucket[5m] offset 1h)) by (le))

# 3. 校验 + reload
.\scripts\reload_prometheus_rules.ps1
```

---

## 6. 回滚验证清单

执行回滚后，逐项确认：

| # | 验证项 | 命令 | 预期结果 |
|---|--------|------|----------|
| 1 | Prometheus 健康 | `curl http://localhost:9090/-/healthy` | `Prometheus is Healthy.` |
| 2 | 配置加载成功 | `curl http://localhost:9090/api/v1/status/config` | `status: success` |
| 3 | 规则数量 | `curl http://localhost:9090/api/v1/rules` | 回滚后 8 条（仅 alert_rules.yml）|
| 4 | 容器状态 | `docker ps --filter name=Yunshu-prometheus` | `Up` |
| 5 | 日志无错误 | `docker logs Yunshu-prometheus --tail 20` | 无 `error`/`FAIL` |

---

## 7. 影响评估

### 7.1 变更收益

| 指标 | 变更前 | 变更后 |
|------|--------|--------|
| 生效告警规则数 | 8 | 30 |
| ONNX P0 告警覆盖 | ❌ 盲区 | ✅ 全覆盖 |
| P99 延迟突增告警 | ❌ 盲区 | ✅ 含误报防护 |
| 查询模式异常告警 | ❌ 盲区 | ✅ 全覆盖 |

### 7.2 回滚耗时

| 回滚场景 | 预计耗时 | 数据影响 |
|----------|---------|----------|
| 场景 A（reload 失败） | < 30s | 无（TSDB 持久卷不受影响）|
| 场景 B（告警风暴） | < 10s | 无（仅规则卸载）|
| 场景 C（YAML 错误） | < 15s | 无 |
| 场景 D（规则缺陷） | < 2min | 无（手动恢复单条规则）|

### 7.3 风险点

- **低风险**: 变更仅追加 `rule_files` 引用，不修改已有规则和抓取配置
- **低风险**: 回滚通过 `git checkout` 秒级完成，TSDB 数据卷不受影响
- **中风险**: 新加载的 22 条规则首次生效时，可能因历史指标已存在而立即触发告警（建议在低峰期执行）

---

## 8. 变更记录

| 时间 | 操作人 | 动作 | 备注 |
|------|--------|------|------|
| 2026-07-29 | 待执行 | 创建变更计划 | 本文档 |
| _待填写_ | _待填写_ | 执行备份 | `prometheus.yml.bak.20260729` |
| _待填写_ | _待填写_ | 执行变更 | `-FixRuleRefs` |
| _待填写_ | _待填写_ | 验证完成 | 规则数 30 |
| _待填写_ | _待填写_ | 提交 git | commit message 见下 |

### Git Commit Message 模板

```
fix(monitoring): 补全 prometheus.yml rule_files 引用

reranker-alerts.yml 和 yunshu-v6-query-pattern-alerts.yml 未被
prometheus.yml 引用，导致 v6.5 ONNX 监控告警和查询模式告警
共 22 条规则失效。追加引用使全部 30 条规则生效。

变更: rule_files +2 引用
影响: 告警规则 8 → 30
回滚: git checkout HEAD~1 -- monitoring/prometheus/prometheus.yml
```

---

## 9. 附录：关键命令速查

```powershell
# === 变更前 ===
git checkout HEAD -- monitoring/prometheus/prometheus.yml   # 回滚到基线
Copy-Item prometheus.yml prometheus.yml.bak.20260729         # 备份

# === 变更执行 ===
.\scripts\reload_prometheus_rules.ps1 -FixRuleRefs           # 自动修复+reload
.\scripts\reload_prometheus_rules.ps1 -DryRun                # 仅校验

# === 验证 ===
curl http://localhost:9090/-/healthy                          # 健康检查
curl http://localhost:9090/api/v1/rules                       # 规则列表
docker logs Yunshu-prometheus --tail 20                       # 日志检查

# === 回滚 ===
git checkout HEAD -- monitoring/prometheus/prometheus.yml     # 秒级回滚
curl -X POST http://localhost:9090/-/reload                   # 热加载回滚
docker restart Yunshu-prometheus                              # 重启回滚
```
