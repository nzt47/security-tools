# master commit 来源守卫 enforce 模式切换监控报告

> **生成时间**: 2026-08-05 13:15 (UTC)
> **切换操作**: 创建仓库 Variable `COMMIT_ORIGIN_GUARD_MODE=enforce`
> **切换时间**: 2026-08-05T13:11:09Z
> **首次运行**: run 31009087519 (workflow_dispatch, master)

---

## 一、切换前准备(已全部完成)

| 前置条件 | 状态 | 依据 |
|----------|------|------|
| P0 修复(workflow 注入 GITHUB_TOKEN) | ✅ 已合并 | PR #247, commit `9f57d52b` |
| ORIGIN-04 在 CI 真正校验(不再降级) | ✅ 已验证 | PR #247 日志 `PRs=#247 \| method=gh API REST` |
| dry-run 观察期无误报 | ✅ 已确认 | run 31002391217 / 31005119462 / 31004871537 |
| 通知联动补齐(guard 加入 ci-failure-notify 白名单) | ✅ 已合并 | PR #249, commit `d52dd1e3` |
| GitHub 平台 committer(squash merge)放行 | ✅ 已验证 | PR #241 修复 ORIGIN-01, 本地 4 案例自检通过 |

---

## 二、enforce 切换操作

```bash
gh variable set COMMIT_ORIGIN_GUARD_MODE --body "enforce"
```

验证变量已生效:

```
COMMIT_ORIGIN_GUARD_MODE   enforce   2026-08-05T13:11:09Z
```

切换方式: 仓库 Settings → Secrets and variables → Actions → Variables → `COMMIT_ORIGIN_GUARD_MODE = enforce`(等效)。

---

## 三、首次运行结果(run 31009087519)

### 3.1 运行概况

| 项目 | 值 |
|------|-----|
| Run ID | 31009087519 |
| 触发事件 | workflow_dispatch (master) |
| 结论 | ✅ success |
| 耗时 | ~12s |

### 3.2 关键日志证据

| 日志行 | 含义 |
|--------|------|
| `GUARD_MODE: enforce` | 变量被 workflow 正确读取 |
| `GITHUB_TOKEN: ***` | token 已注入 step 环境(P0 修复生效) |
| `mode=enforce` | 脚本以 enforce 模式运行 |
| `overall_status=pass` | 校验通过 |
| `total=1 blocked=0` | 无阻断项 |
| `配置文件: commit_origin_whitelist.yaml` | pyyaml 加载正常(非内置默认) |

### 3.3 校验报告(artifact: commit_origin_report.json)

```json
{
  "tool": "verify_commit_origin",
  "status": "pass",
  "meta": {
    "mode": "enforce",
    "shas": ["HEAD"],
    "repo": "nzt47/security-tools",
    "config_source": "配置文件: .../commit_origin_whitelist.yaml"
  },
  "total": 1,
  "blocked": 0,
  "items": [
    {
      "id": "ORIGIN-04",
      "path": "d52dd1e3",
      "desc": "人工身份 commit 有关联 PR",
      "status": "pass",
      "detail": "PRs=#249 | method=gh API REST"
    }
  ]
}
```

**关键验证点**: master 上最近 squash merge commit `d52dd1e3` 被 GitHub 正确关联到 PR #249 → ORIGIN-04 通过。**这证实了 squash merge commit 在 enforce 模式下不会误阻断**(此前唯一不确定项已消除)。

### 3.4 校验项明细

| ID | 状态 | 说明 |
|----|------|------|
| ORIGIN-01 | pass | author=`13539371839@139.com`(白名单), committer=`noreply@github.com`(GitHub 平台放行) |
| ORIGIN-04 | pass | `PRs=#249 \| method=gh API REST`, 人工身份 commit 有关联 PR |

---

## 四、通知渠道配置检查结果(切换后现状)

| 渠道 | 状态 | 说明 |
|------|------|------|
| GitHub Issue | ✅ 生效 | PR #249 已把 guard workflow 加入 ci-failure-notify 白名单, 失败时自动创建 Issue(仅 master 分支, 自动去重) |
| GitHub 默认邮件 | ✅ 自动 | 失败时 GitHub 向 commit 作者/watcher 发送邮件 |
| Actions UI 失败标记 | ✅ 自动 | enforce 模式下阻断 → job 失败 → commit 状态可见 |
| Slack | ⏳ 待配置 | 需 `SLACK_WEBHOOK_URL` secret(当前未配置), 配置后 guard workflow 自带 Slack 步骤自动生效 |
| 钉钉 | ⏳ 待配置 | 需 `DINGTALK_WEBHOOK` secret(当前未配置), 配置后 ci-failure-notify 钉钉渠道自动生效 |

### 4.1 最小可用通知链路(当前)

```
非法 commit push master
    ↓
guard workflow (enforce) exit 1 → job 失败
    ├── GitHub 默认邮件 → commit 作者 / watcher
    └── ci-failure-notify (workflow_run: guard 在白名单)
         └── 创建 GitHub Issue "CI 失败: master commit 来源守卫(...) @ <sha>"
```

### 4.2 建议补充(非阻塞)

如需实时通知(Slack/钉钉), 需管理员提供对应 webhook URL, 配置后立即生效, 无需改 workflow 代码。

---

## 五、enforce 模式行为确认

| 行为 | 状态 |
|------|------|
| 合法 commit(白名单 author + 关联 PR)→ 放行 | ✅ 验证 (run 31009087519) |
| squash merge commit → 关联 PR → 放行 | ✅ 验证 |
| bot commit(白名单路径 + [skip ci])→ 放行 | ✅ 逻辑确认(ORIGIN-02/03) |
| 非法 commit → exit 1 → job 失败 → 阻断 | ✅ 逻辑确认(enforce 模式) |
| GitHub API 不可用 → 降级 warning 不阻断 | ✅ 逻辑确认(【不易】不锁死 master push) |

---

## 六、回退方案(如误阻断)

| 操作 | 生效时间 |
|------|----------|
| 改回 dry-run: `gh variable set COMMIT_ORIGIN_GUARD_MODE --body "dry-run"` | 下次 workflow 运行即生效 |
| 彻底关闭: 删除变量 `gh variable delete COMMIT_ORIGIN_GUARD_MODE` | 回落到 workflow 默认值 dry-run |

---

## 七、结论与后续建议

✅ **enforce 模式已开启且首次运行通过**, master 上合法 commit(含 squash merge)正常放行。

**后续观察项**:
- [ ] 观察 1-2 周, 确认无误报/漏报
- [ ] 确认 ci-failure-notify 联动通知在真实失败场景下工作(可用 `workflow_dispatch` + `simulate_failure` 模拟验证)
- [ ] 阶段 3(可选): 开启 master 分支保护, 勾选 guard 为 required status check

**相关链接**:
- enforce 首次运行: https://github.com/nzt47/security-tools/actions/runs/31009087519
- PR #247 (P0 修复): https://github.com/nzt47/security-tools/pull/247
- PR #249 (通知联动): https://github.com/nzt47/security-tools/pull/249
- 验证报告: docs/observability/guard_master_commit_origin_validation_report.md
