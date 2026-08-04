# Issue 模板：文档失效链接修复跟踪（2026-08-03）

> 用途：跟踪 pre-commit 链接预检（`precheck_docs.ps1`）发现的失效链接修复。
> 状态参考：master 工作区已修复 3 处（待提交）；develop 分支另有 4 处（阻塞提交，待修复）。
> 关联：commit a16fa4fb（master，4 缺陷修复）、commit 4db85572（develop，BOM 修复）提交时均被链接预检阻塞。

---

## 缺陷 #1：[MEDIUM] semantic_monitoring_runbook.md 锚点失效（master，已修复待提交）

**标签**：`docs` `broken-link` `anchor`

**现象**：
```
[BROKEN] docs/observability/semantic_monitoring_runbook.md
  原链接: [四、告警规则] (./semantic_monitoring_guide.md#四告警规则)
  原因: 目标文件不存在
```

**根因**：
- 目标文件 `semantic_monitoring_guide.md` 存在，但锚点 `#四告警规则` 与实际标题锚点不匹配
- 实际标题为 `## 四、告警规则 \`deploy/k8s/grafana-alerting.yaml\``，GitHub 生成的锚点含反引号内代码（`四告警规则deployk8sgrafana-alertingyaml`），链接检查器无法解析 `#四告警规则` 锚点

**修复建议**：
- [x] 已修复：移除失效锚点，改为纯文件链接 `[semantic_monitoring_guide.md] (./semantic_monitoring_guide.md)`（工作区已改，未提交）

**验收标准**：
- [ ] 提交后 pre-commit 链接预检通过（该链接不再出现在失效清单）

---

## 缺陷 #2：[LOW] incident_report_template.md 模板占位符被误判为失效链接（master，已修复待提交）

**标签**：`docs` `broken-link` `template`

**现象**：
```
[BROKEN] docs/wiki/incident_report_template.md（2 处）
  原链接: [{{文档名}}] ({{相对路径}})
  原因: 目标文件不存在
```

**根因**：
- 「关联文档」章节的模板占位符 `[{{文档名}}] ({{相对路径}})` 符合 Markdown 链接语法，链接检查器将其当作真实链接校验，`{{相对路径}}` 不存在 → 误判失效

**修复建议**：
- [x] 已修复：占位符改为反引号代码形式 `\`{{文档名}}\`：\`{{相对路径}}\`（示例：替换为真实文档链接），与同文件第 97 行占位符风格一致（工作区已改，未提交）

**验收标准**：
- [ ] 提交后 pre-commit 链接预检通过（占位符不再被误判）

---

## 缺陷 #3：[MEDIUM] develop 分支 4 处失效链接（阻塞 pre-commit，待修复）

**标签**：`docs` `broken-link` `develop`

**现象**（develop 分支 pre-commit 预检输出）：
```
[BROKEN] DEVELOPMENT_STANDARDS_K8S_SCRIPTS.md: ../scripts/mock_alert_webhook.py
[BROKEN] DEVELOPMENT_STANDARDS_K8S_SCRIPTS.md: HPA_PATROL_TEST_REPORT.md
[BROKEN] HPA_CHANGELOG.md: RESOURCE_COST_COMPARISON.md
[BROKEN] MIGRATION_PORT_FORWARD_TO_IN_CLUSTER.md: RESOURCE_COST_COMPARISON.md
[OK] 检查 642 个文件，522 个链接，4 个失效
```

**根因**：
- 4 处链接指向不存在的目标文件（或文件已移动/重命名），为 develop 分支既有问题，非 BOM 修复引入
- 阻塞阻塞模式提交（阈值 0），需先行修复

**子任务**：
- [ ] 定位 `../scripts/mock_alert_webhook.py` 是否已改名（如 `scripts/mock_alert_webhook.py`），修正相对路径
- [ ] 确认 `HPA_PATROL_TEST_REPORT.md`、`RESOURCE_COST_COMPARISON.md` 实际文件名/位置（可能已移入 reports/ 或改名），更新链接
- [ ] 运行 `scripts/dev/fix_broken_links.ps1 -DryRun` 复扫，确认 4 处清零
- [ ] 在 develop 分支提交修复（注意 develop 分支 pre-commit 锚点回归测试需 python 环境可用）

**验收标准**：
- [ ] develop 分支 pre-commit 链接预检通过（失效链接 0）
- [ ] 后续 develop push 不再被链接预检阻塞

---

## 使用方式

1. 缺陷 #1/#2 已在 master 工作区修复，随下一次 master 提交一并合入即可
2. 缺陷 #3 需在 develop 分支单独提交修复
3. 创建后在本文件「状态参考」登记 Issue 编号
