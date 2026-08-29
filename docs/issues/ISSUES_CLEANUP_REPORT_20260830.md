# GitHub Issues 全量清理报告（2026-08-30）

> 仓库：`nzt47/security-tools`（master）
> 处置时间：2026-08-30
> 处置范围：875 个 issue（869 open / 6 closed），其中 860 个自动生成噪音 Issue 已批量关闭

---

## 1. 摘要

| 项 | 值 |
|---|---|
| Issue 总数（不含 PR） | 875 |
| 处置前 open | 869 |
| 处置后 open | 9（全部为真实 issue） |
| 本次批量关闭 | 860 |
| 零评论 Issue | 868（占 99.2%） |

**结论**：869 个 open issue 中约 98.9% 是 CI 自动生成的噪音（P99 性能告警 + CI 失败通知），并非人工提交的工作项。本次已批量关闭 860 个噪音 Issue，保留 9 个真实 open issue 并逐条梳理（见 §5）；同时修复了两个 CI 工作流的去重根因，防止噪音再次堆积（见 §4）。

---

## 2. 处置前构成分析

| 类别 | 数量 | open | 已 closed | 标签 | 来源 |
|---|---|---|---|---|---|
| 🚨 L2 P99 性能告警 | 690 | 687 | 3 | `performance, alert, automated` | `.github/workflows/ci.yml`「创建 P99 告警 Issue」步骤 |
| CI 失败通知 | 173 | 173 | 0 | `ci-failure, auto-generated` | `.github/workflows/ci-failure-notify.yml`「创建 GitHub Issue」步骤 |
| 真实人工 issue | 12 | 9 | 3 | 混合（bug/security/next-iteration 等） | 人工创建 |
| **合计** | **875** | **869** | **6** | — | — |

### 2.1 噪音增长速率（根因严重程度）

| 噪音类别 | 出现天数 | 平均/天 | 峰值/天 | 时间段 |
|---|---|---|---|---|
| P99 告警 | 35 天 | 19.7 | 56 | 2026-07-01 ~ 08-29 |
| CI 失败 | 18 天 | 9.6 | 34 | 2026-07-01 ~ 08-29 |

P99 告警阈值仅为 **1ms**，CI runner 环境下 P99 极易波动超过该值，导致几乎每次 CI 运行都新建一个 Issue；CI 失败通知原去重键为 (workflow + commit)，同一 workflow 持续失败时每个失败 commit 都新建 Issue。二者叠加两个月即堆积 863 个噪音 Issue。

---

## 3. 处置动作

### 3.1 批量关闭（已完成）

按「标签含 `automated`」精确识别噪音 Issue，共关闭 **860 个**：

- P99 告警：687 个（另有 3 个此前已手动关闭）
- CI 失败：173 个

关闭仅变更 state，未删除任何 Issue、未添加评论，历史记录与链接（PR/CI run 引用）全部保留可回溯。

### 3.2 保留清单（9 个真实 open issue）

见 §5 逐条梳理。

---

## 4. 根因修复（本次随报告提交的代码改动）

### 4.1 `.github/workflows/ci.yml` — P99 告警改为「状态型去重」

改动位置：「创建 P99 告警 Issue」步骤（performance-tests job）。

- **原逻辑**：每次 CI 运行 `monitor_l2_p99.py` 退出码为 1（P99 > 1ms）即 `gh issue create`，无任何去重 → 687 个噪音。
- **新逻辑**：创建前先查询 open 且标题以「🚨 L2 P99 告警」开头的 Issue（`gh issue list --label performance` + jq 过滤）：
  - 已存在 → **仅向既有 Issue 追加本次告警评论**（含时间/P99/阈值/CI 运行链接），不新建；
  - 不存在 → 才新建告警 Issue。
- **生命周期契约**：同一时间最多 1 个 open P99 告警 Issue；P99 恢复后由人工关闭，下次告警才会再建。

### 4.2 `.github/workflows/ci-failure-notify.yml` — CI 失败改为「每 workflow 一个 open Issue」

改动位置：「创建 GitHub Issue」步骤（notify job，github-script）。

- **原逻辑**：去重键为 (workflow + commit)，`i.title.includes(workflowName) && i.title.includes(commit.substring(0,7))` → 同一 workflow 每个失败 commit 都新建 → 173 个噪音。
- **新逻辑**：去重键为 **workflow 名称**（open 的 `ci-failure` 标签 Issue 中 title 含 workflowName）：
  - 已存在 → **追加评论**（commit/触发者/运行链接），不新建；
  - 不存在 → 才新建。
- **生命周期契约**：每个持续失败的 workflow 最多 1 个 open Issue；修复后关闭该 Issue，下次同 workflow 失败才会再建。

> 说明：`develop-ci-stability-monitor.yml` 创建的阶段性报告 Issue（#169/#528）非告警型噪音，未改动该 workflow，其存量报告建议人工归档（见 §5）。

---

## 5. 真实 Issue 逐条梳理（12 个）

### 5.1 仍 open（9 个）

| # | 标题 | 创建 | 标签 | 结论 / 建议动作 |
|---|---|---|---|---|
| 6 | bug(observability): 可见性趋势报告 Mock 测试 query_range 验证失败 (exit code 3) | 07-01 | `bug` | **待处理**：PR #5 暴露的预存在问题，observability-ci.yml `visibility-trend-mock-test` job 退出码 3，Mock 服务启动但 query_range 验证失败。建议复现（workflow_dispatch 手动触发 mock_test_enable=true）后修复或确认当前是否仍失败。 |
| 78 | [security] .secure_config.json 本地敏感文件安全管理改进 + scan 脚本 gitignore 误报修复 | 08-01 | — | **待处理（安全，建议优先）**：`scripts/scan_sensitive_data.py` 全量扫描未跳过 gitignore 文件，对 `.secure_config.json` 产生噪音告警。建议①扫描跳过 gitignore 文件；②收紧本地文件权限（文件已确认未进 git 历史）。 |
| 169 | Develop CI 稳定性监控报告 (1/3) | 08-04 | `ci-stability-monitor, auto-generated` | **归档**：自动生成的阶段性快照报告，内容为 commit c63caba 的 CI 状态快照（稳定）。无后续行动项，可关闭归档。 |
| 232 | flaky: test_positive_not_matched[case_031] 间歇性失败（PR #227 发现） | 08-05 | `bug` | **待处理**：`tests/unit/test_negative_intent.py::TestPositiveSamplesNotMatched::test_positive_not_matched[case_031]` 间歇性失败，同一 commit 下 ci.yml 3 个 Python 版本全过、observability-ci Shard 1 偶发失败。建议按 issue 内复现步骤定位分片时序/环境依赖后修复或加稳定标记。 |
| 528 | Develop CI 稳定性监控 - 最终报告 (3/3) | 08-08 | `ci-stability-monitor, auto-generated` | **归档**：监控已结束的最终报告（commit 232eba4 有 1 个失败：hardcoded-password-scan）。无后续行动项，可关闭归档。 |
| 678 | [B1] 锁优化：optimized_storage.py:363 缩短持锁临界区 | 08-14 | `performance, next-iteration` | **待排期**：锁竞争热点（累计 29.82ms），验收为持锁累计降 ≥50%。属 next-iteration 规划项，随迭代排期执行。 |
| 679 | [B3] 测试套件提速：全量 <30min（integration 段分块） | 08-14 | `performance, next-iteration` | **待排期**：全量 56.6min → <30min 目标，integration 段分块/并行。属 next-iteration 规划项。 |
| 680 | [C1] 灰度发布部署执行（放量/演练/监控） | 08-14 | `enhancement, next-iteration` | **待排期（需生产权限）**：systemd 灰度放量 + 回滚演练 + 7 天监控，前置为生产环境权限与变更审批。 |
| 681 | [C2] 锁看门狗生产接入（挂载规则+告警验证） | 08-14 | `alert, next-iteration` | **待排期（需部署环境）**：Prometheus 挂载 lock_watchdog_alerts.yml + 告警触发验证，代码/测试/规则已就绪。 |

### 5.2 已 closed（3 个，核对无误）

| # | 标题 | 状态说明 |
|---|---|---|
| 11 | bug(test): 26 个单元测试失败（缓存隔离缺深拷贝 + 网络配置加密环境变量未注入） | 已修复关闭，正常 |
| 375 | 架构规则校验：知识引擎循环依赖已修复，待远程 CI 验证 | 已关闭，正常 |
| 376 | gitee 镜像同步：master 落后 13 提交 + v1.0.0 tag 待同步 | 已关闭，正常 |

---

## 6. 后续建议（可选）

1. **P99 阈值校准**：1ms 阈值对 CI runner 过紧（平均 19.7 告警/天）。建议将阈值放宽到可区分真实回归的水平（如 2-5ms），或将告警改为仅对比同环境历史基线，减少误报。
2. **告警生命周期自动化**：可在 ci-failure-notify.yml 的 `docker-scan-recover-notify` 模式基础上扩展——当某 workflow 恢复成功后自动关闭其对应 open 的 `ci-failure` Issue（当前仍需人工关闭）。
3. **告警渠道分级**：P99 瞬时波动建议仅走钉钉/日志渠道，GitHub Issue 保留给「持续超阈值（如连续 N 次）」或「超阈值倍数 ≥ 2x」的场景。
4. **报告类 Issue 归档**：#169/#528 属自动生成的监控报告，可手动关闭；如需长期保留，建议后续改为发布到 Pages/Wiki 而非 Issue。

---

## 7. 附：涉及文件

| 文件 | 变更 |
|---|---|
| `.github/workflows/ci.yml` | P99 告警 Issue 状态型去重（+21 行） |
| `.github/workflows/ci-failure-notify.yml` | CI 失败 Issue 每 workflow 去重 + 评论追加 |
| `docs/issues/ISSUES_CLEANUP_REPORT_20260830.md` | 本报告 |
