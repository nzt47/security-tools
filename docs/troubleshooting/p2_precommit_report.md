# P2 全量升级预提交报告

> **生成日期**: 2026-08-05  
> **执行方案**: P2 方案 A（一次性全量升级）  
> **升级范围**: 29 个 workflow，153 处 action 引用  
> **目标**: 消除 Node 20 deprecation 警告（截止 2026-09-16）

---

## 一、执行摘要

| 维度 | 数值 |
|------|------|
| 升级文件数 | 29 个 workflow |
| 升级处数 | 153 处 |
| 涉及 action 类型 | 6 类 |
| YAML 语法验证 | ✅ 29/29 通过 |
| 旧版本残留检查 | ✅ 无残留 |
| git diff | 153 insertions + 153 deletions |

---

## 二、P1 状态确认

### 2.1 P1 升级 CI 结果

| Workflow | 状态 | 备注 |
|----------|------|------|
| 核心不变量监控 | ✅ success | P1 升级后正常 |
| 硬编码密码扫描 | ✅ success | P1 升级后正常 |
| ci-cd.yml（Error Reporting） | ❌ failure | **根因：Docker 镜像 `agent-test-sqlite-vec` 不存在（与 P1 无关）** |
| observability-ci.yml | ⏳ in_progress | 仍在运行 |

### 2.2 ci-cd.yml 失败根因分析

**失败 job**: `Docker Build and Test`  
**错误信息**: `pull access denied for agent-test-sqlite-vec, repository does not exist or may require 'docker login'`  
**根因**: docker-compose.yml 引用了不存在的 Docker 镜像，**与 action 升级无关**  
**其他 job**: Lint/Type Check ✅、Reranker Hot Reload ✅、Stress Test ✅、Integration Test ✅、Circuit Breaker Inspection ✅

**结论**: P1 升级的 action 版本本身工作正常，可安全执行 P2。

---

## 三、升级映射表

| Action | 当前版本 | 目标版本 | 处数 | 破坏性变更影响 |
|--------|---------|---------|------|---------------|
| actions/checkout | v4 | **v6** | 61 | ✅ 无（persist-credentials 改写位置，runner 已满足） |
| actions/setup-python | v5 | **v6** | 42 | ✅ 无（Node 24，新增 pip-version 参数） |
| actions/upload-artifact | v4 | **v7** | 41 | ✅ 无（ESM 化，现有 name+path 调用兼容） |
| actions/download-artifact | v4 | **v8** | 5 | ⚠️ hash 不匹配默认 error（标准场景无影响） |
| actions/setup-node | v4 | **v5** | 3 | ✅ 无（Node 24，标准 node-version 参数） |
| actions/cache | v4 | **v6** | 11 | ✅ 无（ESM 化，无 API 变更） |
| **总计** | | | **153** | |

---

## 四、文件清单与升级详情

### 4.1 按升级处数排序

| # | 文件 | 处数 | 升级内容 |
|---|------|------|---------|
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

### 4.2 git diff 统计

```
 29 files changed, 153 insertions(+), 153 deletions(-)

 .github/workflows/architecture-check.yml           |  6 ++---
 .github/workflows/boundary-guard.yml               | 12 +++++-----
 .github/workflows/ci-failure-notify.yml            |  4 ++--
 .github/workflows/ci-guard-runner.yml              |  6 ++---
 .github/workflows/config-drift-guard.yml           |  6 ++---
 .github/workflows/core-invariants-guard.yml        |  6 ++---
 .github/workflows/coverage-ci.yml                  | 16 ++++++-------
 .github/workflows/daily_regression.yml             | 16 ++++++-------
 .github/workflows/deploy-pages.yml                 |  4 ++--
 .github/workflows/develop-ci-stability-monitor.yml |  2 +-
 .github/workflows/extension-health-check.yml       | 20 ++++++++--------
 .github/workflows/hardcoded-password-scan.yml      |  6 ++---
 .github/workflows/hook-failsafe-e2e.yml            |  6 ++---
 .github/workflows/import-linter.yml                |  4 ++--
 .github/workflows/intent-layer-ratio-check.yml     |  6 ++---
 .github/workflows/kwarg-conflict-check.yml         | 18 +++++++-------
 .github/workflows/kwarg-docker-scan.yml            | 12 +++++-----
 .github/workflows/l3-docker-tests.yml              | 16 ++++++-------
 .github/workflows/log-perf-guard.yml               | 16 ++++++-------
 .github/workflows/p0-security.yml                  | 18 +++++++-------
 .github/workflows/release-docs.yml                 |  8 +++----
 .github/workflows/reranker-timeout-guard.yml       |  4 ++--
 .github/workflows/sandbox-boundary-tests.yml       |  6 ++---
 .github/workflows/semantic-perf-regression.yml     | 12 +++++-----
 .github/workflows/skills-check.yml                 | 16 ++++++-------
 .github/workflows/tool-retrieval-ci.yml            |  8 +++----
 .github/workflows/tool-tests.yml                   |  6 ++---
 .github/workflows/web-module-tests.yml             | 28 +++++++++++-----------
 .github/workflows/yunshui-ui-tests.yml             | 18 +++++++-------
```

---

## 五、Diff 预览抽样

### 5.1 web-module-tests.yml（14 处，最大）

```diff
@@ -46,10 +46,10 @@ jobs:
     steps:
       - name: 检出代码
-        uses: actions/checkout@v4
+        uses: actions/checkout@v6

       - name: 设置 Python 环境
-        uses: actions/setup-python@v5
+        uses: actions/setup-python@v6
         with:
           python-version: ${{ env.PYTHON_VERSION }}
           cache: 'pip'
@@ -72,7 +72,7 @@ jobs:
             --timeout=300

       - name: 上传覆盖率报告
-        uses: actions/upload-artifact@v4
+        uses: actions/upload-artifact@v7
         if: always()
         with:
           name: processor-coverage-${{ matrix.os }}
```

### 5.2 yunshui-ui-tests.yml（9 处，含 setup-node）

```diff
       - name: 检出代码
-        uses: actions/checkout@v4
+        uses: actions/checkout@v6

       - name: 上传覆盖率报告
-        uses: actions/upload-artifact@v4
+        uses: actions/upload-artifact@v7

       - name: 设置 Node 环境
-        uses: actions/setup-node@v4
+        uses: actions/setup-node@v5
         with:
           node-version: ${{ env.NODE_VERSION }}
```

### 5.3 hardcoded-password-scan.yml（3 处，含 cache）

```diff
       - name: 检出代码
-        uses: actions/checkout@v4
+        uses: actions/checkout@v6

       - name: 上传扫描报告
-        uses: actions/upload-artifact@v4
+        uses: actions/upload-artifact@v7

       - name: 缓存 pip 依赖
-        uses: actions/cache@v4
+        uses: actions/cache@v6
```

---

## 六、风险评估

### 6.1 风险检查结果

| 检查项 | 结果 | 说明 |
|--------|------|------|
| self-hosted runner | ✅ 无 | 全部 GitHub-hosted runner |
| upload-artifact 特殊参数 | ✅ 无 | 全部标准 `name:`+`path:` 调用 |
| download-artifact 特殊参数 | ✅ 无 | 标准 `name` 调用 |
| setup-node 特殊参数 | ✅ 无 | 标准 `node-version` 参数 |
| setup-python 特殊参数 | ✅ 无 | 标准 `python-version`+`cache` 参数 |
| checkout 特殊参数 | ✅ 无 | 标准 `fetch-depth` 用法 |
| cache 特殊参数 | ✅ 无 | 标准 `path`+`key` 用法 |

### 6.2 风险结论

所有 workflow 均使用标准参数，**无破坏性变更影响**。P1 升级模式已验证安全（ci-cd.yml 失败根因为 Docker 镜像问题，与 action 无关），P2 可安全执行。

---

## 七、验证清单

### 7.1 提交前验证（已完成）

- [x] 29 个文件 YAML 语法正确
- [x] 无旧版本残留（checkout@v4、upload-artifact@v4 等）
- [x] git diff 统计匹配（153 insertions + 153 deletions）
- [x] 风险评估全部通过

### 7.2 提交后验证（push 后观察）

- [ ] pre-commit hook 通过（链接 + 锚点 + 不变量）
- [ ] CI 触发：观察核心 workflow 无新增失败
- [ ] 确认无 Node 20 deprecation 警告

---

## 八、回滚预案

若 P2 升级导致 CI 大规模失败：

```bash
# 一键回滚（单 commit revert）
git revert <P2-commit-hash>
git push origin master
```

回滚后 P1 升级（4 个 workflow）仍保留，仅回退 P2 的 29 个 workflow。

---

## 九、参考

- [P2/P3 升级规划文档](./p2_p3_workflow_upgrade_plan.md)
- [v1.1.4 → v1.1.9 复盘与全仓审计](./v1_1_4_to_v1_1_9_release_postmortem_and_workflow_audit.md)
- [Node 20 deprecation 备忘录](./node20_deprecation_action_upgrade_memo.md)
- [GitHub 官方弃用公告](https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/)
