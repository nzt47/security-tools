# Gitleaks 密码扫描 CI 失败修复复盘报告

> **事件编号**: INC-20260801-GITLEAKS-CI
> **发生时间**: 2026-08-01 14:56 (UTC) 修复验证通过
> **影响范围**: 硬编码密码扫描 CI 工作流（全分支触发）
> **严重等级**: P2（CI 阻塞，密码扫描无法作为 PR 合并防线）
> **报告状态**: ✅ 已修复并验证转绿

---

## 一、执行摘要（Executive Summary）

硬编码密码扫描 CI 在 PR（`feat/intent-layer-metrics-fix` #80）场景下持续失败，导致密码扫描防线失效。根因有二：

1. **artifact 命名含非法字符**：上传扫描报告时直接使用 `github.ref_name` 作为 artifact 名称，而 PR 场景的 ref_name 为 `<PR号>/merge`（含 `/`），触发 `upload-artifact` 抛 `InvalidArtifactName`。
2. **GITHUB_TOKEN 权限不足**：工作流 `permissions` 仅声明 `issues: write`，PR 评论步骤（`actions/github-script`）在 PR 上下文需要 `pull-requests: write` 权限，报 `HttpError: Resource not accessible by integration`。

修复后，master 分支推送触发的新一轮密码扫描已 **转绿（success）**。

---

## 二、故障现象

### 2.1 CI 失败日志（修复前，run 30697380695）

```
##[error]The artifact name is not valid: gitleaks-scan-report-80/merge. Contains the following character:  Forward slash /
RequestError [HttpError]: Resource not accessible by integration
##[error]Unhandled error: HttpError: Resource not accessible by integration
```

### 2.2 失败链路

```
PR 触发 gitleaks 扫描
  ├─ 步骤"上传扫描报告"失败 → artifact 名 gitleaks-scan-report-80/merge 含 '/'
  │     （GitHub Actions 规则：artifact 名称禁止含 '/' ':' 等字符）
  └─ 步骤"PR 评论"失败 → GITHUB_TOKEN 缺 pull-requests: write 权限
        （actions/github-script 调用 issues.createComment 被拒）
```

---

## 三、根因分析

### 3.1 根因 1：`github.ref_name` 在 PR 场景含 `/`

| 触发事件 | `github.ref_name` 值 | 含 `/` | 后果 |
|----------|---------------------|--------|------|
| push 到分支 | `feat/intent-layer-metrics-fix` | ✅ | artifact 名不合法 |
| pull_request | `80/merge` | ✅ | artifact 名不合法 |
| push 到 master | `master` | ❌ | 恰好通过（掩盖问题） |

**本质**：`ref_name` 携带的是 git 引用名（`refs/pull/80/merge` 的末段），天然含 `/`，不能直接用作 artifact 名称。

### 3.2 根因 2：`permissions` 声明不完整

- `issues: write` 允许写 Issue，但 **PR 评论在 GitHub API 中属于 `pull_request` 资源**，需要 `pull-requests: write`。
- 之前的提交 `c934136f` 只补了 `issues: write`，未覆盖 PR 评论场景。

### 3.3 为何本地未发现

工作流在 `pull_request` 事件下才暴露两个问题；而 push 到 master 的 run（ref_name 无 `/`）恰好通过，掩盖了 artifact 命名缺陷。属于典型的**环境依赖缺陷**（只在特定触发事件暴露）。

---

## 四、修复方案

### 4.1 修改文件

- [`.github/workflows/hardcoded-password-scan.yml`](file:///c:/Users/Administrator/agent/.github/workflows/hardcoded-password-scan.yml)

### 4.2 修复内容（三处）

| # | 修复点 | 修改前 | 修改后 |
|---|--------|--------|--------|
| 1 | `permissions` | `contents: read` + `issues: write` | 增加 `pull-requests: write` |
| 2 | artifact 名称 | `gitleaks-scan-report-${{ github.ref_name }}` | 新增预处理 step，`${RAW//\//-}` 将 `/` 替换为 `-`，输出到 `GITHUB_OUTPUT` |
| 3 | PR 评论模板 | 内联拼接 `${{ github.ref_name }}` | 通过 `env: ARTIFACT_NAME` 透传安全名称，脚本内用 `process.env.ARTIFACT_NAME` |

### 4.3 关键代码

```yaml
# 预处理 step：计算安全的 artifact 名称（ref_name 含 '/' → '-'）
- name: 准备安全的 artifact 名称
  id: artifact_name
  run: |
    RAW="${{ github.ref_name }}"
    SAFE="${RAW//\//-}"
    echo "name=gitleaks-scan-report-${SAFE}" >> "$GITHUB_OUTPUT"
```

```yaml
# 上传步骤改用预处理输出
- name: 上传扫描报告
  uses: actions/upload-artifact@v4
  with:
    name: ${{ steps.artifact_name.outputs.name }}
```

### 4.4 配套工作

- 新增单测 [`tests/unit/test_compat_check.py`](file:///c:/Users/Administrator/agent/tests/unit/test_compat_check.py)（21 用例，覆盖 8 边界场景），守护共享兼容性模块，与本次 CI 修复一并提交。

---

## 五、修复验证

### 5.1 验证结果（run 30704829068）

| 项目 | 修复前 | 修复后 |
|------|--------|--------|
| run 结论 | failure | ✅ **success** |
| artifact 名称 | `gitleaks-scan-report-80/merge`（非法） | `gitleaks-scan-report-master`（合法） |
| PR 评论权限 | `HttpError: Resource not accessible` | 权限已具备 |
| 扫描结果 | —（中途失败） | ✅ 未发现硬编码密码（0 处） |

### 5.2 关键日志证据

```
已计算 artifact 名称: gitleaks-scan-report-master (原始 ref_name: master)
✅ 未发现硬编码密码
✅ 扫描通过
上传扫描报告: name=gitleaks-scan-report-master
```

---

## 六、经验教训（Lessons Learned）

### 6.1 守护不变量

1. **artifact 名称永不直接使用 `github.ref_name`** —— 需先做字符清洗（`/` → `-`），或在命名时只用 `github.sha`、`github.run_id` 等安全标识。
2. **PR 评论必须声明 `pull-requests: write`** —— `issues: write` 不覆盖 PR 评论资源。
3. **CI 配置改动后需在 PR 场景验证** —— push 到 master 的成功会掩盖 PR 专属缺陷。

### 6.2 回归防线

- 工作流本身由 `paths` 过滤，修改 `.github/workflows/*.yml` 时会自动触发全分支密码扫描，形成自愈回路。

---

## 七、附录

- 修复提交：`a55e91f2`（`fix(ci): 修复 gitleaks 工作流 artifact 命名与 token 权限 + compat_check 单测`）
- 验证 run：[30704829068](https://github.com/nzt47/security-tools/actions/runs/30704829068)
- 失败 run：[30697380695](https://github.com/nzt47/security-tools/actions/runs/30697380695)
- 相关规则文档：`.github/gitleaks-config.toml`
