# master commit 来源守卫 CI 流程生效验证报告

> **生成时间**: 2026-08-05
> **关联 PR**: [#240](https://github.com/nzt47/security-tools/pull/240) (机制引入) + [#241](https://github.com/nzt47/security-tools/pull/241) (生效修复)
> **状态**: ✅ 修复生效, dry-run 灰度运行中

---

## 一、背景

PR #240 引入 `guard-master-commit-origin.yml` workflow, 通过 `verify_commit_origin.py` 校验 push 到 master 的 commit 是否为人工发起(阻断脚本伪装直接 push)。

PR #240 合并后(commit `ddc56c1a`), 手动触发 `workflow_dispatch` (run 31002391217) 验证发现两个问题导致 dry-run 报告 BLOCK:

1. **CI 环境未安装 pyyaml**: `verify_commit_origin.py` 降级用内置默认配置, 导致 `commit_origin_whitelist.yaml` 的 bot `allowed_paths` 等配置无法生效
2. **GitHub squash merge committer 误阻断**: GitHub 平台 squash merge 时将 committer email 改写为 `noreply@github.com`, 不在白名单触发 `ORIGIN-01 BLOCK`

PR #241 修复这两个问题, 本报告验证修复在 master 合并后首次 push 场景下生效。

---

## 二、修复内容(PR #241)

### 修复 1: workflow 添加 `pip install pyyaml` 步骤

[`.github/workflows/guard-master-commit-origin.yml`](file:///C:/Users/Administrator/agent/.github/workflows/guard-master-commit-origin.yml#L52-L58)

在 `setup-python` 后新增"安装依赖"步骤, 显式安装 pyyaml, 避免降级到内置默认配置:

```yaml
- name: 安装依赖
  run: |
    python -m pip install --upgrade pip
    pip install pyyaml
```

### 修复 2: ORIGIN-01 拆分校验逻辑

[`scripts/verify_commit_origin.py`](file:///C:/Users/Administrator/agent/scripts/verify_commit_origin.py#L350-L381)

ORIGIN-01 拆分为两级校验:

- **author email 严格白名单** (【不易】防脚本用本地 git 身份伪造 author push 到 master)
- **committer email 放行 GitHub 平台邮箱**: committer 命中 `noreply@github.com` 或 `@noreply.github.com` 域时放行 (【变易】适配 GitHub squash merge 平台行为)
- **committer 既非白名单也非平台邮箱仍阻断** (防 author 伪造 + 真实 committer 蒙混)

### 修复 3: 注释同步

`scripts/commit_origin_whitelist.yaml` 与 `verify_commit_origin.py` docstring 同步更新 ORIGIN-01 校验项描述。

---

## 三、验证过程

### 3.1 本地自检(修复后 ORIGIN-01 逻辑)

4 个测试案例全部符合预期:

| 场景 | 期望 | 实际 | 结果 |
|------|------|------|------|
| author 白名单 + committer=`noreply@github.com` (squash merge) | 放行 | 放行 | ✅ |
| bot author 修改非白名单路径 | BLOCK | BLOCK (ORIGIN-02/03) | ✅ |
| 非白名单 author + 任意 committer | BLOCK | BLOCK (ORIGIN-01) | ✅ |
| 全伪造 (非白名单 author + 非 platform committer) | BLOCK | BLOCK (ORIGIN-01) | ✅ |

### 3.2 合并后首次 push 场景模拟(本地 dry-run)

对 PR #240 的 squash merge commit `ddc56c1a` 跑 dry-run:

```
author: nzt47 <13539371839@139.com>
committer: GitHub <noreply@github.com>
subject: feat(ci): 新增 master commit 来源守卫机制 + ... (#240)

[校验结果] 1 项
  [pass] ORIGIN-04 ddc56c1a: PR 关联校验跳过(API 不可用, 降级不阻断)
[总结] blocked=0/1
[PASS] squash merge commit 通过校验, ORIGIN-01 修复生效
```

### 3.3 PR #241 CI 验证(pull_request 事件)

PR #241 触发的 `commit-origin-guard` workflow (run 31004871537) 结果:

- ✅ `安装依赖`: `Successfully installed pyyaml-6.0.3`
- ✅ `运行 commit 来源校验`: 通过
- ✅ `overall_status=pass`, `total=1 blocked=0`

---

## 四、合并后首次 push 真实验证(run 31005119462)

PR #241 squash merge 到 master (commit `4f304fcb`) 触发 push 事件, `commit-origin-guard` workflow 自动运行:

**commit 元信息**:
- sha: `4f304fcb`
- author: `13539371839@139.com` (nzt47, 白名单)
- committer: `noreply@github.com` (GitHub 平台 squash merge)
- subject: `fix(ci): 修复 guard-master-commit-origin 两个生效问题 (#241)`

**workflow 运行结果** (run 31005119462, 2026-08-05 12:19:34 UTC):

| 步骤 | 结果 | 关键日志 |
|------|------|----------|
| 安装依赖 | ✅ success | `Successfully installed pyyaml-6.0.3` |
| 运行 commit 来源校验 | ✅ success | (无 ORIGIN-01 BLOCK) |
| 解析结果 | ✅ success | `overall_status=pass`, `total=1 blocked=0` |
| Job Summary | ✅ success | `✅ PASS ORIGIN-04` |
| Slack 通知 | ⏭️ skipped | (无阻断, 不通知) |

### 4.1 修复前后对比

| 项目 | 修复前 (run 31002391217) | 修复后 (run 31005119462) |
|------|--------------------------|--------------------------|
| pyyaml | ❌ `pyyaml 不可用, 使用内置默认配置` | ✅ `Successfully installed pyyaml-6.0.3` |
| 配置加载 | ❌ 降级到内置默认值 | ✅ 从 `commit_origin_whitelist.yaml` 加载 |
| ORIGIN-01 | ❌ `BLOCK committer=noreply@github.com` | ✅ 通过 (squash merge committer 放行) |
| overall_status | ❌ `fail` (blocked=1/1) | ✅ `pass` (blocked=0/1) |
| workflow conclusion | ⚠️ success (dry-run 不阻断) | ✅ success |

---

## 五、三阶段灰度上线状态

| 阶段 | 状态 | 说明 |
|------|------|------|
| 阶段 1 (当前) | ✅ 已落地 | `GUARD_MODE=dry-run`, 仅告警不阻断, 不勾选 required status check |
| 阶段 2 (待执行) | ⏳ 计划 | 观察 1-2 周后切 `GUARD_MODE=enforce`, 仍不勾选(push 后才跑, 事后告警) |
| 阶段 3 (长期) | ⏳ 计划 | 开启 master 分支保护 + 本 workflow + ci.yml 勾选 required status check |

**切换方式**: 仓库 Settings → Secrets and variables → Actions → Variables → 新增/修改 `COMMIT_ORIGIN_GUARD_MODE = enforce`

---

## 六、校验项清单(verify_commit_origin.py)

| ID | 校验内容 | 状态 |
|----|----------|------|
| ORIGIN-01 | author email 不在白名单 → BLOCK; committer 非白名单且非 GitHub 平台邮箱 → BLOCK | ✅ 已修复 |
| ORIGIN-02 | bot commit 修改非白名单路径 → BLOCK | ✅ 正常 |
| ORIGIN-03 | bot commit subject 缺 `[skip ci]` → BLOCK | ✅ 正常 |
| ORIGIN-04 | 人工身份 commit 无 GitHub 关联 PR(疑似脚本直接 push) → BLOCK | ⚠️ 见下 |
| ORIGIN-05 | subject 命中脚本特征黑名单(可选, 过渡期) → BLOCK | ✅ 正常(黑名单为空) |

---

## 七、已知限制与后续建议

### 7.1 ORIGIN-04 在 CI 中降级(实测: PR 与 push 事件均降级)

**现象**: master push 和 pull_request 两种事件触发的 workflow 中, ORIGIN-04 均降级为 `::warning::` 不阻断:
- run 31004871537 (PR #241, pull_request 事件): `GitHub API 不可用(所有 API 路径不可用(gh 缺失/token 缺失/网络故障)), 跳过 ORIGIN-04`
- run 31005119462 (master push 事件): 同样降级

**根因(经代码追踪定位)**:
1. **gh CLI 缺失**: ubuntu-latest runner 默认未安装 `gh` CLI → `query_associated_prs_gh` 返回 None
2. **GraphQL 兜底也走 gh CLI**: `query_associated_prs_graphql` 仍调 `gh api graphql` → 同样失败
3. **urllib 兜底缺 token**: `query_associated_prs_urllib` 调 `_get_github_token()` 读 `GH_TOKEN`/`GITHUB_TOKEN` env vars, 但 workflow step 未显式 `env: GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}`, GitHub Actions 的 `GITHUB_TOKEN` secret **不会自动注入** step 环境 → token=None → urllib 兜底失败

**降级范围**: 所有 CI 事件(push/PR)均触发降级, 不只是 push 事件。

**影响评估(攻击面量化)**:

| 攻击场景 | ORIGIN-04 降级时是否阻断 | 兜底层 |
|----------|--------------------------|--------|
| 非白名单 author 直接 push(任意 committer) | ✅ 阻断 | ORIGIN-01 |
| 白名单 author + 非白名单 committer + 非平台邮箱 | ✅ 阻断 | ORIGIN-01 |
| 白名单 author 直接 push(不走 PR, 无关联 PR) | ❌ **放行(风险)** | 无兜底 |
| bot 身份修改非白名单路径 | ✅ 阻断 | ORIGIN-02 |
| bot 身份 subject 缺 `[skip ci]` | ✅ 阻断 | ORIGIN-03 |

**残余风险**: 仅"白名单 author 直接 push 到 master(不走 PR 流程)"场景能绕过守卫。攻击者需先获取白名单 author email 的本地 git 配置(等于攻破开发者机器或窃取 git 凭证)。

**缓解方案(优先级排序)**:

1. **【P0 强烈建议 enforce 前实施】workflow 显式传 GITHUB_TOKEN**: 在"运行 commit 来源校验" step 添加 `env: GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}`, urllib 兜底即可工作。无需新增 secret, 改 1 行 yaml。
2. **【P1 可选】安装 gh CLI**: 在 workflow 添加 `apt-get install -y gh` 或用 `actions/setup-gh` 第三方 action, 使 gh CLI 路径可用(更稳但增加依赖)
3. **【P2 长期】阶段 3 开启 master 分支保护**: 强制所有 commit 走 PR 合并, 直接 push 被 GitHub 原生拦截, ORIGIN-04 降级与否不再重要

**当前实测状态**: PR #241 验证时未实施缓解方案 1, ORIGIN-04 处于降级状态。建议 enforce 前至少实施缓解方案 1。

### 7.2 阶段 2 enforce 切换前置条件

切 `GUARD_MODE=enforce` 前需确认:

- [ ] **【P0 必做】实施 7.1 缓解方案 1**: workflow "运行 commit 来源校验" step 添加 `env: GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}`, 否则 ORIGIN-04 在 enforce 模式下仍降级, 白名单 author 直接 push 的脚本无法阻断
- [ ] dry-run 模式观察 1-2 周, 无误报
- [ ] `publish_fix_to_docs.py` 等 bot 自动 push 路径全部走 `github-actions[bot]` 身份 + `[skip ci]` + 白名单路径
- [ ] 所有人工 push master 的 commit 都走 PR 流程(关联 PR 可查)
- [ ] 接受 ORIGIN-04 残余风险(7.1 攻击面表), 或实施缓解方案 2/3

### 7.3 阶段 3 分支保护配置

- [ ] master 分支开启 "Require a pull request before merging"
- [ ] 勾选 `commit-origin-guard` 为 required status check
- [ ] 勾选 `core-invariants-guard` 为 required status check
- [ ] 限制 push 权限(只允许 bot 通过 PR 合并)

---

## 八、相关资源

- 守卫脚本: [`scripts/verify_commit_origin.py`](file:///C:/Users/Administrator/agent/scripts/verify_commit_origin.py)
- 白名单配置: [`scripts/commit_origin_whitelist.yaml`](file:///C:/Users/Administrator/agent/scripts/commit_origin_whitelist.yaml)
- workflow: [`.github/workflows/guard-master-commit-origin.yml`](file:///C:/Users/Administrator/agent/.github/workflows/guard-master-commit-origin.yml)
- 通用报告生成器: [`scripts/report_generator.py`](file:///C:/Users/Administrator/agent/scripts/report_generator.py)
- PR #240: https://github.com/nzt47/security-tools/pull/240 (机制引入)
- PR #241: https://github.com/nzt47/security-tools/pull/241 (生效修复)
- 修复前 run: https://github.com/nzt47/security-tools/actions/runs/31002391217
- 修复后 run: https://github.com/nzt47/security-tools/actions/runs/31005119462

---

## 九、结论

✅ **PR #241 修复的两个问题(pyyaml 缺失 + squash merge committer 误阻断)在合并后首次 push 场景下完全生效**。

✅ **`guard-master-commit-origin.yml` workflow 在 dry-run 模式下正常运行, 不阻断 master push, 仅告警异常**。

✅ **三阶段灰度上线阶段 1 已完成, 可观察 1-2 周后切阶段 2(enforce)**。
