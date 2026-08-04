# Git Diff 变更报告 — Gitleaks CI 修复

> **变更范围**: `364174f0..HEAD`（2 个提交）
> **生成时间**: 2026-08-01
> **修复主题**: Gitleaks 密码扫描 CI 转绿 + compat_check 单测 + 复盘报告

---

## 一、变更概览

| 提交 | 类型 | 说明 |
|------|------|------|
| `a55e91f2` | fix(ci) | 修复 gitleaks 工作流 artifact 命名与 token 权限 + compat_check 单测 |
| `efd5478b` | docs(ci) | Gitleaks CI 修复复盘报告 |

## 二、文件统计

| 文件 | 变更 | 增/删 |
|------|------|-------|
| [hardcoded-password-scan.yml](file:///c:/Users/Administrator/agent/.github/workflows/hardcoded-password-scan.yml) | 修改 | +29 / -6 |
| [test_compat_check.py](file:///c:/Users/Administrator/agent/tests/unit/test_compat_check.py) | 新增 | +423 |
| [GITLEAKS_CI_FIX_RETROSPECTIVE_20260801.md](file:///c:/Users/Administrator/agent/docs/GITLEAKS_CI_FIX_RETROSPECTIVE_20260801.md) | 新增 | +148 |

**总计**: 3 文件变更，594 行新增，6 行删除

---

## 三、完整 Diff

### 3.1 `.github/workflows/hardcoded-password-scan.yml`

```diff
diff --git a/.github/workflows/hardcoded-password-scan.yml b/.github/workflows/hardcoded-password-scan.yml
index db6868a6..94be4fd1 100644
--- a/.github/workflows/hardcoded-password-scan.yml
+++ b/.github/workflows/hardcoded-password-scan.yml
@@ -77,12 +77,25 @@ jobs:
     name: Gitleaks 硬编码密码扫描
     runs-on: ubuntu-22.04
     timeout-minutes: 10
-    # 【修复 CHG-2026-0801】GITHUB_TOKEN 默认无 issues:write 权限，
-    # 导致 PR 评论步骤 HttpError: Resource not accessible by integration
+    # 【修复 CHG-2026-0801】GITHUB_TOKEN 默认无 issues/pull-requests:write 权限，
+    # 导致 PR 评论步骤 HttpError: Resource not accessible by integration。
+    # 同时补充 pull-requests: write，让 actions/github-script 可创建 PR 评论。
     permissions:
-      contents: read      # checkout 需要
-      issues: write       # PR 评论 (actions/github-script) 需要
+      contents: read            # checkout 需要
+      issues: write             # PR 评论 (actions/github-script createComment) 需要
+      pull-requests: write      # PR 评论 (createComment) 在 PR 上下文需要
     steps:
+      - name: 准备安全的 artifact 名称
+        id: artifact_name
+        # 【修复 CHG-2026-0801b】github.ref_name 在 PR 场景为 "<PR号>/merge"，
+        # 含 '/' 会导致 upload-artifact 抛 InvalidArtifactName（artifact 名禁含 '/' 等）。
+        # 统一将 '/' 替换为 '-'，输出到 GITHUB_OUTPUT 供后续步骤复用。
+        run: |
+          RAW="${{ github.ref_name }}"
+          SAFE="${RAW//\//-}"
+          echo "name=gitleaks-scan-report-${SAFE}" >> "$GITHUB_OUTPUT"
+          echo "已计算 artifact 名称: gitleaks-scan-report-${SAFE} (原始 ref_name: ${RAW})"
+
       - name: 检出代码（完整历史）
         uses: actions/checkout@v4
         with:
           fetch-depth: 0
@@ -156,18 +169,22 @@ jobs:
         if: always()
         uses: actions/upload-artifact@v4
         with:
-          name: gitleaks-scan-report-${{ github.ref_name }}
+          # 使用预处理步骤计算的安全名称，避免 ref_name 含 '/' 导致 InvalidArtifactName
+          name: ${{ steps.artifact_name.outputs.name }}
           path: scan-reports/gitleaks-report.json
           retention-days: 30
 
       - name: PR 评论（仅 PR 时）
         if: github.event_name == 'pull_request' && failure()
         uses: actions/github-script@v7
+        env:
+          # 透传安全的 artifact 名称给内联脚本，避免在模板字符串中再拼 ref_name
+          ARTIFACT_NAME: ${{ steps.artifact_name.outputs.name }}
         with:
           script: |
             github.rest.issues.createComment({
               issue_number: context.issue.number,
               owner: context.repo.owner,
               repo: context.repo.repo,
-              body: `## ❌ 硬编码密码扫描未通过\n\nGitleaks 检测到硬编码密码，请修复后重新提交。\n\n**修复指南**：\n1. 将密码改为 \`os.environ.get('VAR')\` 读取\n2. Docker Compose 使用 \`\${VAR:-default}\` 变量插值\n3. 密码放入 \`.env\`（已被 .gitignore 排除）\n4. 参考: commit 9d51c406 (P1 修复)\n\n**详细报告**：见 Artifact \`gitleaks-scan-report-${{ github.ref_name }}\`\n\n**规则配置**：\`.github/gitleaks-config.toml\``
+              body: `## ❌ 硬编码密码扫描未通过\n\nGitleaks 检测到硬编码密码，请修复后重新提交。\n\n**修复指南**：\n1. 将密码改为 \`os.environ.get('VAR')\` 读取\n2. Docker Compose 使用 \`\${VAR:-default}\` 变量插值\n3. 密码放入 \`.env\`（已被 .gitignore 排除）\n4. 参考: commit 9d51c406 (P1 修复)\n\n**详细报告**：见 Artifact \`${process.env.ARTIFACT_NAME}\`\n\n**规则配置**：\`.github/gitleaks-config.toml\``
             })
```

**变更要点**:
1. `permissions` 增加 `pull-requests: write`（PR 评论需要）
2. 新增"准备安全的 artifact 名称" step，`${RAW//\//-}` 清洗 `/`
3. artifact 上传改用预处理输出名称
4. PR 评论通过 `env: ARTIFACT_NAME` 透传安全名称

### 3.2 `tests/unit/test_compat_check.py`（新增，423 行）

详见 [test_compat_check.py](file:///c:/Users/Administrator/agent/tests/unit/test_compat_check.py)（21 个用例，覆盖 8 边界场景）

**测试场景清单**:

| # | 场景 | 断言 |
|---|------|------|
| 1 | 正常兼容（K8s 1.28 + API 可用） | ok=True，无 errors/warnings |
| 2 | 版本过低（1.18 < 1.19） | ok=False（hard error） |
| 3 | 版本偏低（1.20 < 1.22） | ok=True，仅告警 |
| 4 | metrics API NotFound | ok=False（hard error） |
| 5 | metrics API Forbidden | ok=True（降级告警） |
| 6 | kubectl 不可用 | ok=True（降级告警） |
| 7 | client/server skew > 2 | ok=True，告警 |
| 8 | 版本解析 + 旧版文本输出回退 | 多格式解析正确 |

**数据结构契约守护**: `CompatibilityCheckResult` 的 `ok`/`errors`/`warnings` 字段行为固定

### 3.3 `docs/GITLEAKS_CI_FIX_RETROSPECTIVE_20260801.md`（新增，148 行）

详见 [GITLEAKS_CI_FIX_RETROSPECTIVE_20260801.md](file:///c:/Users/Administrator/agent/docs/GITLEAKS_CI_FIX_RETROSPECTIVE_20260801.md)

---

## 四、验证记录

| 验证项 | 结果 |
|--------|------|
| gitleaks 扫描 CI（run 30704829068） | ✅ success |
| artifact 名称 | `gitleaks-scan-report-master`（合法） |
| 扫描结果 | ✅ 硬编码密码 0 处 |
| 单测执行 | ✅ 21 passed（1.23s） |
| Markdown 链接预检 | ✅ 0 失效 |
