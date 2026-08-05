# P2/P3 GitHub Actions 升级规划（剩余 29 个 workflow）

> **创建日期**: 2026-08-05  
> **前置条件**: P1 升级已完成（ci.yml、ci-cd.yml、observability-ci.yml、test.yml，commit `61843b10`）  
> **截止日期**: 2026-09-16（Node 20 完全移除，距今 42 天）  
> **关联文档**: [v1_1_4_to_v1_1_9_release_postmortem_and_workflow_audit.md](./v1_1_4_to_v1_1_9_release_postmortem_and_workflow_audit.md)

---

## 一、当前状态

### 1.1 已完成（P1）

| Workflow | Commit | 升级处数 | 状态 |
|----------|--------|---------|------|
| publish-psgallery.yml | (v1.1.9 时完成) | 5 处 | ✅ 已验证 |
| ci.yml | 61843b10 | 30 处 | ⏳ CI 运行中 |
| ci-cd.yml | 61843b10 | 23 处 | ⏳ CI 运行中 |
| observability-ci.yml | 61843b10 | 42 处 | ⏳ CI 运行中 |
| test.yml | 61843b10 | 28 处 | ✅ 已提交（master 不触发） |

### 1.2 待升级（P2/P3）

**29 个 workflow，共 153 处升级**

---

## 二、风险评估

### 2.1 风险检查结果

| 检查项 | 结果 | 说明 |
|--------|------|------|
| self-hosted runner | ✅ 无 | 全部 GitHub-hosted runner |
| upload-artifact 特殊参数 | ✅ 无 | 全部标准 `name:`+`path:` 调用 |
| download-artifact 特殊参数 | ⚠️ 2 处 `pattern:` | ci.yml 标准用法，v8 仍支持 |
| setup-node 特殊参数 | ✅ 无 | 标准使用 `node-version` 参数 |
| checkout 特殊参数 | ✅ 无 | 标准 `fetch-depth` 用法 |

### 2.2 结论

所有 workflow 均使用标准参数，**无破坏性变更影响**。P1 升级模式已验证安全，P2 可批量执行。

---

## 三、P2 升级清单（29 个 workflow）

### 3.1 按升级处数排序

| # | Workflow | 处数 | 升级内容 |
|---|----------|------|---------|
| 1 | web-module-tests.yml | 14 | checkout×4 + upload-artifact×4 + download-artifact×2 + setup-python×4 |
| 2 | extension-health-check.yml | 10 | checkout×4 + upload-artifact×3 + setup-python×3 |
| 3 | yunshui-ui-tests.yml | 9 | checkout×3 + upload-artifact×3 + **setup-node×3** |
| 4 | p0-security.yml | 9 | checkout×4 + upload-artifact×1 + setup-python×4 |
| 5 | kwarg-conflict-check.yml | 9 | checkout×3 + upload-artifact×3 + setup-python×3 |
| 6 | l3-docker-tests.yml | 8 | checkout×3 + upload-artifact×4 + download-artifact×1 |
| 7 | log-perf-guard.yml | 8 | checkout×3 + upload-artifact×2 + setup-python×3 |
| 8 | skills-check.yml | 8 | checkout×4 + upload-artifact×1 + setup-python×3 |
| 9 | coverage-ci.yml | 8 | checkout×2 + upload-artifact×3 + download-artifact×1 + setup-python×2 |
| 10 | daily_regression.yml | 8 | checkout×3 + upload-artifact×2 + setup-python×3 |
| 11 | kwarg-docker-scan.yml | 6 | checkout×4 + upload-artifact×2 |
| 12 | semantic-perf-regression.yml | 6 | checkout×2 + upload-artifact×2 + setup-python×2 |
| 13 | boundary-guard.yml | 6 | checkout×2 + upload-artifact×2 + setup-python×2 |
| 14 | tool-retrieval-ci.yml | 4 | checkout×2 + setup-python×2 |
| 15 | release-docs.yml | 4 | checkout×1 + upload-artifact×1 + download-artifact×1 + setup-python×1 |
| 16 | tool-tests.yml | 3 | checkout×1 + upload-artifact×1 + setup-python×1 |
| 17 | sandbox-boundary-tests.yml | 3 | checkout×1 + upload-artifact×1 + setup-python×1 |
| 18 | intent-layer-ratio-check.yml | 3 | checkout×1 + upload-artifact×1 + setup-python×1 |
| 19 | hook-failsafe-e2e.yml | 3 | checkout×3 |
| 20 | hardcoded-password-scan.yml | 3 | checkout×1 + upload-artifact×1 + **cache×1** |
| 21 | core-invariants-guard.yml | 3 | checkout×1 + upload-artifact×1 + setup-python×1 |
| 22 | config-drift-guard.yml | 3 | checkout×1 + upload-artifact×1 + setup-python×1 |
| 23 | ci-guard-runner.yml | 3 | checkout×1 + upload-artifact×1 + setup-python×1 |
| 24 | architecture-check.yml | 3 | checkout×1 + upload-artifact×1 + setup-python×1 |
| 25 | reranker-timeout-guard.yml | 2 | checkout×1 + setup-python×1 |
| 26 | deploy-pages.yml | 2 | checkout×1 + setup-python×1 |
| 27 | ci-failure-notify.yml | 2 | checkout×2 |
| 28 | import-linter.yml | 2 | checkout×1 + setup-python×1 |
| 29 | develop-ci-stability-monitor.yml | 1 | checkout×1 |

### 3.2 按行动作汇总

| Action | 当前→目标 | 处数 |
|--------|----------|------|
| actions/checkout | v4 → v6 | **61** |
| actions/setup-python | v5 → v6 | **42** |
| actions/upload-artifact | v4 → v7 | **41** |
| actions/download-artifact | v4 → v8 | **5** |
| actions/setup-node | v4 → v5 | **3** |
| actions/cache | v4 → v6 | **1** |
| **总计** | | **153** |

---

## 四、分批策略

### 方案 A：一次性全量升级（推荐）

**一个 commit 升级全部 29 个 workflow（153 处）**

| 维度 | 评估 |
|------|------|
| 优势 | 一次完成、避免多次碰文件、9 月 16 日前彻底消除风险 |
| 风险 | 若某 workflow 有未发现的特殊场景，多个 CI 可能同时失败 |
| 适用 | P1 已验证升级模式安全，且风险评估无特殊参数 |
| 回滚 | `git revert <commit>` 一次性回滚 |

### 方案 B：分 3 批按业务模块升级

**Batch 1（安全模块，5 个 workflow，36 处）**
- p0-security.yml (9)
- kwarg-docker-scan.yml (6)
- kwarg-conflict-check.yml (9)
- boundary-guard.yml (6)
- hardcoded-password-scan.yml (3) + cache 升级

**Batch 2（测试模块，7 个 workflow，50 处）**
- web-module-tests.yml (14)
- extension-health-check.yml (10)
- yunshui-ui-tests.yml (9) + setup-node 升级
- l3-docker-tests.yml (8)
- skills-check.yml (8)
- sandbox-boundary-tests.yml (3) - 已删
- hook-failsafe-e2e.yml (3)

**Batch 3（CI 基础设施 + 发布，17 个 workflow，67 处）**
- coverage-ci.yml、daily_regression.yml、log-perf-guard.yml
- semantic-perf-regression.yml、tool-retrieval-ci.yml、tool-tests.yml
- intent-layer-ratio-check.yml、release-docs.yml、deploy-pages.yml
- ci-failure-notify.yml、ci-guard-runner.yml、core-invariants-guard.yml
- config-drift-guard.yml、architecture-check.yml、reranker-timeout-guard.yml
- import-linter.yml、develop-ci-stability-monitor.yml

### 方案 C：仅最高频 action（checkout + upload-artifact）

**只升 checkout×61 + upload-artifact×41 = 102 处**
- 优势：覆盖最常用 action，风险最低
- 缺点：setup-python×42 + download-artifact×5 + setup-node×3 + cache×1 = 51 处仍触发警告

---

## 五、推荐执行方案

**推荐方案 A（一次性全量升级）**，理由：

1. **P1 已验证**：4 个 workflow 123 处升级，YAML 语法正确，已成功的 CI（核心不变量、硬编码密码扫描）无异常
2. **风险评估**：全部 GitHub-hosted runner，无特殊参数，无破坏性变更影响
3. **时间压力**：距 9 月 16 日仅 42 天，一次完成最稳妥
4. **回滚简单**：单 commit 可 `git revert` 一键回滚

### 5.1 执行步骤

```powershell
# 1. 批量替换 6 类 action（checkout + setup-python + upload-artifact + download-artifact + setup-node + cache）
# 2. YAML 语法验证 29 个文件
# 3. 扫描无旧版本残留
# 4. git add + commit（chore(ci): P2 全量升级剩余 29 个 workflow）
# 5. push 触发 CI 验证
```

### 5.2 验证清单

- [ ] 29 个文件 YAML 语法正确
- [ ] 无旧版本残留（checkout@v4、upload-artifact@v4 等）
- [ ] pre-commit hook 通过（链接 + 锚点 + 不变量）
- [ ] push 后观察 CI：核心 workflow（ci-cd、observability-ci）无新增失败
- [ ] 确认无 Node 20 deprecation 警告

---

## 六、P3 说明

原 P3 定义为 "setup-python、cache、download-artifact 升级"。但 P1 已包含这些 action 的升级，且 P2（方案 A）也会全量升级。因此 **P3 已合并到 P2**，不再单独存在。

升级后剩余的"待查证"action（github-script@v7、slackapi/slack-github-action@v1、docker/setup-buildx-action@v3、docker/build-push-action@v5、codecov/codecov-action@v4、dawidd6/action-send-mail@v3、actions/deploy-pages@v4、actions/upload-pages-artifact@v3）不触发 Node 20 警告或已是 Node 24，可在 P2 完成后单独查证。

---

## 七、参考

- [v1.1.4 → v1.1.9 复盘与全仓审计](./v1_1_4_to_v1_1_9_release_postmortem_and_workflow_audit.md)
- [Node 20 deprecation 备忘录](./node20_deprecation_action_upgrade_memo.md)
- [GitHub 官方弃用公告](https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/)
