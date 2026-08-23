# master commit 来源守卫 — CI 流程演进总结报告(dry-run → enforce)

> **生成时间**: 2026-08-05 (UTC)
> **报告性质**: 从机制引入到 enforce 模式切换的最终总结(汇总验证报告、管理员简报、enforce 监控报告三份文档)
> **关联分支**: master

---

## 一、演进背景(问题定义)

2026-08-05 复盘发现 `scripts/publish_fix_to_docs.py` 可用本地 git 身份(nzt47)直接 commit + push 到 master,其 author email 与人工 commit 完全相同,**无法通过 author email 区分**。存在脚本静默修改 master 的越权风险。

**解决方案**: 引入 `verify_commit_origin.py` + `guard-master-commit-origin.yml` workflow,通过"**白名单 + GitHub 关联 PR 校验**"识别脚本直接 push 的 commit(无关联 PR → 阻断)。

**三阶段灰度上线设计**(【不易】不锁死 master push):

| 阶段 | 模式 | 行为 | 状态 |
|------|------|------|------|
| 1 | dry-run | 仅 `::warning::` 告警, exit 0 | ✅ 已完成(08-05 11:40 前 ~ 13:11) |
| 2 | enforce | 检测到问题 exit 1, 阻断 master push | ⚠️ 曾生效(08-05 13:11 切换) → **08-06 误伤回退 dry-run**(见里程碑 8/9) |
| 3 | 分支保护 | guard 勾选为 required status check | ⏳ 观察 1-2 周后可选 |

---

## 二、演进时间线(9 个里程碑)

| # | 里程碑 | 时间 (UTC) | PR | Commit | 内容 |
|---|--------|-----------|----|--------|------|
| 1 | 机制引入 | 08-05 11:40 前 | #240 | `ddc56c1a` | 守卫脚本 + workflow(dry-run 模式) + `publish_fix_to_docs.py` bot 身份修复 |
| 2 | 首次验证发现问题 | 08-05 11:40 | — | — | run 31002391217: pyyaml 不可用(降级默认配置) + ORIGIN-01 误阻断 squash merge committer |
| 3 | 生效修复 | 08-05 12:19 | #241 | `4f304fcb` | workflow 加 `pip install pyyaml` + ORIGIN-01 拆分校验(committer 放行 GitHub 平台邮箱); run 31005119462 (master push, pass) |
| 4 | 文档归档 | 08-05 12:20 | #243 | `e264d91c` | 验证报告 + 管理员简报(含 ORIGIN-04 降级根因追踪、攻击面量化) |
| 5 | P0 修复 ORIGIN-04 降级 | 08-05 12:55 | #247 | `9f57d52b` | workflow 显式注入 `GITHUB_TOKEN` + urllib 异常兜底(ssl.SSLError/TimeoutError/通用 Exception); run 31007480305 验证 `PRs=#247 \| method=gh API REST` |
| 6 | 通知联动补齐 | 08-05 13:03 | #249 | `d52dd1e3` | guard workflow 加入 ci-failure-notify 白名单 → 失败联动 GitHub Issue + 邮件 |
| 7 | **enforce 切换** | 08-05 13:11 | #252 | `5c084be2` | 创建 Variable `COMMIT_ORIGIN_GUARD_MODE=enforce`; 首次运行 run 31009087519: `mode=enforce, overall_status=pass, blocked=0`; squash merge commit `d52dd1e3` 正确关联 PR #249; 监控报告 + 演进记录归档 |
| 8 | **enforce 误伤人工 push** | 08-06 05:01 | — | `d55abd03` | 人工身份 commit(无关联 PR)直接 push master → ORIGIN-04 BLOCK → workflow failure → CI 失败通知噪音(邮件/钉钉); **不会回滚已推送 commit**(push 后事后校验) |
| 9 | **回退 dry-run** | 08-06 | — | — | `gh variable set COMMIT_ORIGIN_GUARD_MODE -b dry-run`; 恢复告警不阻断; 修复指南归档 `docs/troubleshooting/commit_origin_guard_fix_guide_20260806.md` |

---

## 三、关键运行数据

| Run ID | 事件 | 阶段 | 结果 | 验证要点 |
|--------|------|------|------|----------|
| 31002391217 | workflow_dispatch | dry-run | ❌ 发现问题 | pyyaml 不可用(降级内置默认) + ORIGIN-01 误阻断 squash merge committer |
| 31004871537 | — | dry-run | ✅ | 观察期无异常 |
| 31005119462 | master push | dry-run | ✅ pass | PR #241 修复生效(pyyaml 正常 + committer 放行) |
| 31007480305 | PR push | dry-run | ✅ pass | **ORIGIN-04 真正校验**(`PRs=#247`, 不再降级) — P0 修复验证 |
| **31009087519** | workflow_dispatch | **enforce** | ✅ success | 首次 enforce 运行: `mode=enforce`, `total=1 blocked=0`, squash merge 关联 PR 验证通过 |

### 3.1 enforce 首次运行关键日志(run 31009087519)

```
GUARD_MODE: enforce                     ← 变量被 workflow 正确读取
GITHUB_TOKEN: ***                       ← token 注入 step 环境(P0 修复生效)
mode=enforce                            ← 脚本 enforce 模式运行
overall_status=pass                     ← 校验通过
total=1 blocked=0                       ← 无阻断项
配置文件: commit_origin_whitelist.yaml  ← pyyaml 加载正常(非内置默认)
```

校验报告(ORIGIN-04): `PRs=#249 | method=gh API REST` — **证实 squash merge commit 在 enforce 模式下不会误阻断**(此前唯一不确定项已消除)。

---

## 四、技术架构

### 4.1 校验项(verify_commit_origin.py)

| ID | 校验内容 | 失败动作 | 覆盖攻击面 |
|----|----------|----------|------------|
| ORIGIN-01 | author email 严格白名单; committer 白名单或 GitHub 平台邮箱(noreply.github.com)放行 | BLOCK | 脚本伪造 author 直推 master |
| ORIGIN-02 | bot commit 修改非白名单路径 | BLOCK | bot 越权改业务代码 |
| ORIGIN-03 | bot commit subject 缺 `[skip ci]` | BLOCK | bot 绕过 CI 检查 |
| ORIGIN-04 | 人工身份 commit 无 GitHub 关联 PR(疑似脚本直接 push) | BLOCK | 脚本用白名单身份绕过 |
| ORIGIN-05 | subject 命中脚本特征黑名单(可选, 过渡期) | BLOCK | 脚本特征识别 |

### 4.2 关联 PR 查询三级兜底

```
gh API REST(query_associated_prs_gh) → gh API GraphQL(query_associated_prs_graphql) → urllib REST(query_associated_prs_urllib)
```

- **降级语义**(【不易】不锁死 master push): 所有 API 路径不可用 → 返回 None → `::warning::` 降级, **即使 enforce 模式也不阻断**
- 本地无 GH_TOKEN/GITHUB_TOKEN → 跳过 PR 校验并 `::notice::` 提示(防本地锁死开发流程)
- GraphQL 必须用 40 位完整 SHA(短 SHA 报 GitObjectID 类型错误)

### 4.3 通知链路(enforce 阻断后)

```
非法 commit push master
    ↓
guard workflow (enforce) exit 1 → job 失败
    ├── GitHub 默认邮件 → commit 作者 / watcher(自动生效)
    ├── ci-failure-notify(guard 已在白名单)→ 创建 GitHub Issue(仅 master, 自动去重)
    └── Slack 步骤(failure() && SLACK_WEBHOOK_URL != '')→ scripts/slack_notify.py(待配置 webhook)
```

---

## 五、演进过程中的关键决策

1. **三阶段灰度上线**: dry-run(仅告警)→ enforce(阻断)→ 分支保护(required status check),【不易】不锁死 master push
2. **ORIGIN-01 拆分校验**: author email 严格白名单(防伪造)+ committer 放行 GitHub 平台邮箱(适配 squash merge 平台行为)
3. **ORIGIN-04 降级不阻断**: GitHub API 不可用时降级为 warning(守【不易】);P0 修复(注入 GITHUB_TOKEN)后 CI 中真正校验
4. **通知对齐**: guard workflow 加入 ci-failure-notify 白名单,与其余 workflow 失败通知行为一致
5. **P0 修复方式**: GitHub Actions 的 GITHUB_TOKEN secret 不会自动注入 step 环境,必须显式 `env: GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}`

---

## 六、当前状态(通知渠道矩阵)

| 渠道 | 状态 | 说明 |
|------|------|------|
| GitHub Issue | ✅ 生效 | ci-failure-notify 白名单已含 guard workflow |
| GitHub 默认邮件 | ✅ 自动 | commit 作者 / watcher |
| Actions UI 失败标记 | ✅ 自动 | enforce 阻断 → job 失败 → commit 状态可见 |
| Slack | ⏳ 待配置 | 需 `SLACK_WEBHOOK_URL` secret;guard workflow Slack 步骤已就绪,脚本 dry-run 已验证 |
| 钉钉 | ⏳ 待配置 | 需 `DINGTALK_WEBHOOK` secret;ci-failure-notify 钉钉渠道已就绪 |

**回退方案**(如误阻断): `gh variable set COMMIT_ORIGIN_GUARD_MODE --body "dry-run"`(下次运行即生效); 或 `gh variable delete COMMIT_ORIGIN_GUARD_MODE`(回落到默认 dry-run)。

---

## 七、后续观察项(非阻塞)

- [ ] **enforce 重新评估**(08-06 误伤回退后): 人工身份直推 master 需规范化——
  - 方案 A: 人工 push 前先 `gh pr create`(附关联 PR)再走 PR 合入
  - 方案 B: enforce 前把人工常用身份加入 ORIGIN-04 豁免白名单(需评估风险)
  - 方案 C: 维持 dry-run + 手动巡检 `::warning::` 告警
- [ ] 观察 1-2 周 dry-run 告警频率, 确认误伤根因(人工直推 vs 脚本伪装)后再决定是否重开 enforce
- [ ] 配置 `SLACK_WEBHOOK_URL`(用户已记录, 待提供 webhook 后配置并本地真实发送验证)
- [ ] 配置 `DINGTALK_WEBHOOK`(可选)
- [ ] 阶段 3(可选): 开启 master 分支保护, 勾选 guard + ci.yml 为 required status check
- [ ] 验证 ci-failure-notify 联动在真实失败场景工作(可用 `workflow_dispatch` + `simulate_failure` 模拟)

---

## 八、相关文档索引

| 文档 | 用途 |
|------|------|
| `docs/observability/guard_master_commit_origin_validation_report.md` | 详细验证报告(攻击面量化、ORIGIN-04 根因追踪、P0/P1/P2 缓解) |
| `docs/observability/guard_master_commit_origin_admin_brief.md` | 面向仓库管理员的 enforce 切换风险简报 |
| `docs/observability/guard_master_commit_origin_enforce_monitoring_report.md` | enforce 切换后首次运行监控报告 |
| `docs/observability/ci_workflow_changes_commit_record.md` | 第十章: CI 流程演进记录 |
| `.trae/documents/guard-master-commit-origin-plan.md` | 三阶段灰度上线计划 |
| `.github/workflows/guard-master-commit-origin.yml` | 守卫 workflow(enforce 生效) |
| `scripts/verify_commit_origin.py` | 校验脚本(ORIGIN-01~05) |
| `scripts/slack_notify.py` | Slack 通知脚本(dry-run 已验证) |

---

## 九、结论

✅ **master commit 来源守卫已完成完整演进(引入 → dry-run 验证 → enforce 切换 → 误伤回退)**:
- 08-05: 6 个 PR 全部合并,enforce 首次运行(run 31009087519)验证通过,合法 commit(含 squash merge)正常放行
- 08-06: enforce 误伤人工身份直推 commit(`d55abd03`, ORIGIN-04 BLOCK)→ 按预案回退 dry-run(告警不阻断),修复指南归档
- 当前状态: **dry-run 观察期**,GitHub Issue + 邮件通知链路正常,Slack 待配置 webhook
- 回退机制验证有效(「不锁死 master push」的【不易】设计经受住了真实误伤场景)

*本报告由 master commit 来源守卫演进任务生成,数据来源为各里程碑 run 日志与已归档报告。*
