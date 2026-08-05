# 仓库管理员简报: master commit 来源守卫 enforce 切换风险点

> **生成时间**: 2026-08-05
> **目标读者**: 仓库管理员(决定何时切 enforce 模式)
> **关联详细报告**: [guard_master_commit_origin_validation_report.md](./guard_master_commit_origin_validation_report.md)

---

## 一、当前状态(一句话)

PR #241 已合并, `guard-master-commit-origin.yml` workflow 在 **dry-run 模式**下正常运行, 修复了 pyyaml 缺失和 squash merge committer 误阻断两个问题。**暂未切 enforce**。

---

## 二、切 enforce 前必须知道的 1 个关键风险

### ⚠️ ORIGIN-04 在 CI 中降级, 实测无 PR 关联校验能力

- **现象**: workflow 日志显示 `GitHub API 不可用(所有 API 路径不可用), 跳过 ORIGIN-04`, 意味着"白名单 author 直接 push 到 master(不走 PR)"**无法被守卫阻断**
- **范围**: push 和 pull_request 事件均降级(不只 push 事件)
- **根因**: workflow step 未把 `GITHUB_TOKEN` 传给脚本 env, GitHub Actions 的 token 不会自动注入 → urllib 兜底拿不到 token → API 调用失败 → 降级

### 残余攻击面(enforce 后仍无法阻断的场景)

| 攻击场景 | enforce 模式下 |
|----------|----------------|
| 非白名单 author 伪造身份 push | ✅ 阻断(ORIGIN-01) |
| **白名单 author 直接 push(不走 PR)** | ❌ **放行(ORIGIN-04 降级)** |
| bot 修改非白名单路径 | ✅ 阻断(ORIGIN-02) |
| bot subject 缺 `[skip ci]` | ✅ 阻断(ORIGIN-03) |

**残余风险解读**: 攻击者需先获取白名单 author email 的本地 git 配置(等于攻破开发者机器或窃取 git 凭证), 才能绕过守卫。风险等级中等, 非零日漏洞。

---

## 三、切 enforce 前的 Checklist

### 【P0 必做】1 行 yaml 修复 ORIGIN-04 降级

在 `.github/workflows/guard-master-commit-origin.yml` 的"运行 commit 来源校验" step 添加:

```yaml
- name: 运行 commit 来源校验
  if: steps.precheck.outputs.skip != 'true'
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}  # 新增: 让 urllib 兜底能查关联 PR
  run: |
    python scripts/verify_commit_origin.py ...
```

无需新增 secret, 改 1 行即可让 ORIGIN-04 在 CI 中真正工作。

### 【建议】其他前置条件

- [ ] dry-run 模式观察 1-2 周, 确认无误报
- [ ] 确认 `publish_fix_to_docs.py` 等 bot 自动 push 路径全部用 `github-actions[bot]` 身份 + `[skip ci]` + 白名单路径
- [ ] 确认所有人工 push master 的 commit 都走 PR 流程
- [ ] 接受 ORIGIN-04 残余风险, 或实施 P1/P2 缓解方案

---

## 四、enforce 切换操作步骤

1. 先实施 P0 修复(单独发 PR 修改 workflow, 验证 ORIGIN-04 在 CI 不再降级)
2. 仓库 Settings → Secrets and variables → Actions → Variables
3. 新增/修改 Variable: `COMMIT_ORIGIN_GUARD_MODE = enforce`
4. 触发一次 master push 验证 workflow 仍 success
5. 观察 1-2 个周期, 确认无误报后可进入阶段 3(开启分支保护)

---

## 五、enforce 后出问题怎么办

### 误阻断合法 commit(开发者无法 push)

- **临时回退**: 仓库 Settings → Variables → 把 `COMMIT_ORIGIN_GUARD_MODE` 改回 `dry-run`(秒级生效, 下次 push 即生效)
- **彻底关闭**: 同样改 `dry-run` 即可, 不需要删除 workflow

### 误阻断 bot 自动 push(如 Pages 部署失败)

- 检查 bot commit 是否符合契约: `github-actions[bot]` 身份 + `[skip ci]` subject + 白名单路径
- 参考 PR #240 中 `publish_fix_to_docs.py` 的 bot 身份切换逻辑

---

## 六、三阶段上线进度

| 阶段 | 状态 | 风险等级 |
|------|------|----------|
| 阶段 1 (dry-run) | ✅ 已落地 | 低(仅告警) |
| 阶段 2 (enforce) | ⏳ 等待 P0 修复 + 观察 | 中(阻断 master push) |
| 阶段 3 (分支保护) | ⏳ 计划 | 低(GitHub 原生拦截) |

---

## 七、一句话决策建议

**当前不建议立即切 enforce**: 需先发 1 个小 PR 实施 P0 修复(workflow 加 1 行 `env: GITHUB_TOKEN`), 验证 ORIGIN-04 在 CI 不再降级, 再观察 1-2 周 dry-run 无误报, 然后切 enforce。
