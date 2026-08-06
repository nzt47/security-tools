# 发布流程维护操作手册

维护对象：`.github/workflows/release-auto.yml` 自动发布工作流及其配套脚本
（`scripts/update_changelog.py`、`scripts/create_gitee_release.ps1`）。

## 1. 发布流程架构

```
git tag vX.Y.Z + git push origin vX.Y.Z
        │
        ▼
┌─────────────┐
│ guard       │ 子包 tag 守卫：判定是否主项目发布
│ (skip=true) │ ← commit message 以 release(pypi) 开头 → 跳过，不创建 Release
└──────┬──────┘
       │ skip=false
       ▼
┌─────────────┐
│ auto-release│ ① 生成发布备注（update_changelog.py，vPREV..HEAD 分类）
│             │ ② 创建 GitHub Release（REST API，失败重试 3 次×10s；409/422 幂等冲突不重试）
│             │ ③ 同步 Gitee Release（create_gitee_release.ps1，失败重试 3 次×10s）
└──────┬──────┘
       │ 失败
       ▼
┌─────────────┐
│ alert-on-   │ 创建告警 Issue（标题「发布失败告警: {version}」）
│ failure     │ 附运行链接 + HTTP 状态码排查指引
└─────────────┘
```

触发方式：
- `git push origin vX.Y.Z`（push tag，自动）
- 手动：Actions → 自动发布 → Run workflow → 填 version

## 2. 子包 tag 守卫（guard job）

**目的**：仓库混有 l2-p99-monitor 子包版本 tag（v1.0.1、v1.1.x，PyPI 发布），
`push: tags: ['v*']` 会匹配它们，若不加守卫会误触发主项目自动发布。

**判定规则**（`guard` job → step `g`）：

```bash
MSG=$(git log -1 --format=%s "${GITHUB_REF_NAME}")
if echo "$MSG" | grep -qiE '^release\(pypi\)'; then skip=true; else skip=false; fi
```

- `skip=true` → auto-release 不执行（job 显示 Skipped，非失败，不告警）
- `skip=false` → 正常发布

**已验证用例**：
| tag | commit message | 判定 |
|---|---|---|
| v1.0.1-test | ci(release): ... | skip=false（放行）✓ |
| v1.0.1 | release(pypi): 升级 l2-p99-monitor 到 1.0.1 | skip=true（拦截）✓ |

**调整方式**：修改 `guard` job 的正则即可。注意：
- 正则必须匹配子包发布的 commit message 前缀（`release(pypi)`）
- **不要**用宽泛关键词（如 `pypi|l2-p99-monitor`）——会误伤主项目提交
  （曾因此误拦 v1.0.1-test，提交 `e104e391` 修复为精确前缀匹配）
- 子包发布约定若改变（如改用其他前缀），同步更新正则与下方注释

## 3. Gitee 同步重试机制

**位置**：`auto-release` job → step「同步 Gitee Release（失败自动重试 3 次，间隔 10s）」

**逻辑**：while 循环，最多 3 次，间隔 10s，每次输出 `=== Gitee 同步尝试 N/3 ===`；
耗尽后 `echo "::error::..."` + `exit 1` → job 失败 → 触发告警。

**参数调整**：`MAX_RETRY`（次数）、`sleep 10`（间隔秒数）。

**幂等性注意**：`create_gitee_release.ps1` 创建模式对已存在 Release 报
409/422（"该标签已经存在发行版"）。重试不改变 payload，因此：
- 首次失败（网络/限流 403）→ 重试有意义
- tag 已存在 Release → 重试无意义，需改调 `-Update` 参数（更新模式）
- 若要重试语义更强，可改为检测已有 release id 后自动 `-Update`

## 4. 失败告警机制

**位置**：`alert-on-failure` job（`needs: auto-release` + `if: failure()`）。

- 触发：auto-release 任何 step 失败（含 Gitee 重试耗尽）
- 动作：`gh issue create` 创建 Issue，标题「发布失败告警: {version} (自动发布)」
- 权限：workflow 级 `permissions: issues: write`；凭证用自动注入的 `GITHUB_TOKEN`
- 内容：版本 / 工作流 / 运行链接 / 触发方式 / 失败时间 / HTTP 状态码排查指引

**不触发场景**：
- auto-release 成功（正常发布）
- guard 拦截（skip，Skipped 非失败）
- GITEE_TOKEN secret 未配置（Gitee step 显示 Skipped，非失败）

**扩展渠道**（Slack/DingTalk）：在 `alert-on-failure` job 增配 webhook secret
（如 `SLACK_WEBHOOK_URL`），加一步 curl POST 到 webhook；仓库有
`scripts/slack_notify.py` 可复用（需先配置对应 secret）。

## 5. 密钥配置

| secret | 用途 | 配置位置 |
|---|---|---|
| `GITEE_TOKEN` | Gitee Release 创建（projects 权限，40 位十六进制） | GitHub → Settings → Secrets and variables → Actions |
| `GITHUB_TOKEN` | GitHub Release / Issue（自动注入，无需配置） | — |

未配置 `GITEE_TOKEN`：Gitee 同步 step 安全跳过（`if: env.GITEE_TOKEN != ''`），
不会导致发布失败，也不触发告警。

## 6. 排障指引

**日志入口**：
1. Actions → 自动发布 → 本次运行
2. 关键 step 已启用 `set -x`；首步「打印发布环境信息」含 event/ref/run_url
3. Artifacts → release-notes（发布备注产物，核对内容）

**常见失败与排查**：
| 现象 | 状态码 | 处理 |
|---|---|---|
| token 无效 | Gitee 401 | 重新生成 token（勾选 projects）→ 更新 secret |
| 仓库/tag 不存在或无权限 | Gitee 404 | 确认 tag 已推送 gitee、token 有仓库权限 |
| tag 已存在 Release | Gitee 409/422 | 用 `-Update` 更新模式 |
| Gitee 同步 step 显示 Skipped | — | 未配置 GITEE_TOKEN，非故障 |
| GitHub Release 创建失败（5xx/网络） | 5xx | step 自动重试 3 次×10s，耗尽触发告警 |
| GitHub Release tag 已存在 | GitHub 409/422 | 幂等冲突不重试；改用 PATCH `/releases/{id}` 编辑接口 |
| Gitee 网络超时（连接挂起） | curl exit 28 / ps1 超时 | 走重试 3 次×10s（v1.0.6 模拟验证：超时×2 → 第 3 次成功） |
| Gitee 服务不可用 | 503 | 走重试 3 次×10s（v1.0.7 模拟验证：503×2 → 第 3 次成功） |
| GitHub API 网络层失败（超时/拒连） | curl exit 28/7，HTTP 000 | `\|\| CODE=500` 映射进重试，不再被 `set -e` 跳过（见下） |

### 6.1 网络超时与静默失败修复（2026-08-06）

**两类外部调用，两种失败形态**：

| 调用 | 失败形态 | 处理 |
|---|---|---|
| GitHub Release `curl` | ①网络层失败（超时 exit 28 / 拒连 exit 7，HTTP 000）②HTTP 5xx（503 等） | ①`\|\| CODE=500` 映射进重试；②HTTP 码直接进重试 |
| Gitee `pwsh`（Invoke-Gitee） | 网络超时 / HTTP 5xx / 4xx | `if pwsh ... else RC=$?` 捕获后进重试（if 豁免 `set -e`） |

**已修复的两个静默失败点**：

1. **GitHub curl 网络失败跳过重试**（release-auto.yml「创建 GitHub Release」step）：
   `CODE=$(curl ...)` 在 `set -e` 下，curl 网络层失败（退出码非零）会直接终止 step、
   跳过 while 重试循环——网络瞬时故障第一次就 abort 并误触发告警。修复：
   ```bash
   CODE=$(curl -s -o gh_resp.json -w '%{http_code}' --max-time 30 -X POST ...) || CODE=500
   ```
   - `|| CODE=500`：网络失败（HTTP 000）映射为 500 进入重试
   - `--max-time 30`：防 API 挂起拖满 step 超时（timeout-minutes: 10）
   - `[ -s gh_resp.json ]`：网络失败时响应文件可能不存在/为空，读取前容错

2. **Gitee 脚本无超时挂死**（scripts/create_gitee_release.ps1）：
   `Invoke-RestMethod` 默认无限等待，Gitee API 挂起时脚本静默拖满 10min 才失败。
   修复：`Invoke-Gitee` 增加 `TimeoutSec = 30` 默认值，超时即抛异常 → exit 1 →
   外层 else 捕获 → 进重试。

**重试行为速查**（Gitee 同步 step，3 次×10s）：
- 超时（exit 28，v1.0.6 模拟）、503（v1.0.7 模拟）、401/404/403 → 均走 else 捕获 → 重试
- 409/422（幂等冲突）→ 重试无意义，耗尽后按失败处理并提示 `-Update`
- 3 次均失败 → `exit 1` → 自动触发 alert-on-failure 建告警 Issue

**安全测试方法**：用测试 tag（如 `v1.0.1-test`）跑全流程，验证后删除：
GitHub：`gh release delete vX-test --yes --cleanup-tag`
Gitee：API 删 Release（`DELETE /releases/{id}`）+
`git push gitee :refs/tags/vX-test`（Gitee API 无删 tag 接口，用 git 方式）
本地：`git tag -d vX-test`

## 7. 迁移到 GitLab CI 关键配置对照

| 能力 | GitHub Actions | GitLab CI（.gitlab-ci.yml） |
|---|---|---|
| tag 触发 | `on.push.tags: ['v*']` | `rules: - if: '$CI_COMMIT_TAG =~ /^v.*/'` |
| 手动触发 | `workflow_dispatch` + inputs | UI 的 Run pipeline + 变量 `version`（`$CI_PIPELINE_SOURCE == "web"`） |
| 全量 git 历史 | `actions/checkout@v6` + `fetch-depth: 0` | runner 默认全量 clone；浅克隆需 `GIT_DEPTH: 0` |
| job 依赖 | `needs` | `needs`（GitLab 14.2+） |
| 条件跳过 | `if: needs.x.outputs.skip != 'true'` | `rules` / `when` |
| 重试 | step 内手动 while 循环 | 内建 `retry: 2`（job 级，注意幂等） |
| 失败告警 | `if: failure()` + `gh issue create` | `when: on_failure` + GitLab API 建 Issue / webhook |
| 创建 GitHub Release | `gh` CLI 预装 + `GITHUB_TOKEN` 自动注入 | runner 无 gh：`apt-get install gh` + 配置 `GH_TOKEN`（PAT），或用 GitHub REST API curl |
| 创建 Gitee Release | `GITEE_TOKEN` secret | CI/CD Variables 配 `GITEE_TOKEN`（勾选 Masked） |
| 产物 | `actions/upload-artifact@v7` | `artifacts: paths: [notes.md], expire_in: 1 week` |
| 版本号来源 | `github.ref_name` | `$CI_COMMIT_TAG`（tag 触发）/ `$version`（手动） |
| 上一版本 | `git tag --sort=-creatordate \| sed -n '2p'` | 同（runner 内 git 逻辑不变） |

**迁移要点**：
1. 最核心差异是**凭据**：GitLab 无自动注入 token，`GH_TOKEN`（GitHub PAT）与
   `GITEE_TOKEN` 都要手动在 Settings → CI/CD → Variables 配置
2. 创建 GitHub Release 前需安装 gh CLI（或改用 curl + GitHub REST API）
3. 重试语义不同：GitLab `retry:` 重跑整个 job（副作用需幂等），step 内循环是「局部重试」
4. tag 守卫/发布备注/脚本逻辑（update_changelog.py、create_gitee_release.ps1）
   全部可原样复用，只需换 YAML 外壳
5. `GIT_DEPTH: 0` 保证 `git log vPREV..HEAD` 拿到完整提交（否则浅克隆截断）

## 8. 迁移回 GitHub Actions 关键配置对照（反向）

适用场景：从 `.gitlab-ci.yml` 迁回 `.github/workflows/release-auto.yml`。
GitHub 版工作流本来就存在且经过 v1.0.1-test 全流程验证，**无需从零重写**，
只需做语法适配层映射 + 回填 GitLab 版新增强的能力（GitHub Release 重试）。

| 能力 | GitLab CI（.gitlab-ci.yml） | GitHub Actions（release-auto.yml） |
|---|---|---|
| tag 触发 | `rules: - if: '$CI_COMMIT_TAG =~ /^v.*/'` | `on.push.tags: ['v*']` |
| 手动触发 | `$CI_PIPELINE_SOURCE == "web"` + 变量 `version` | `workflow_dispatch` + `inputs.version` |
| 全量 git 历史 | `GIT_DEPTH: 0` | `actions/checkout@v6` + `fetch-depth: 0` |
| 守卫传参 | `artifacts.reports.dotenv: guard.env` + `$SKIP` | `outputs.skip` + `$GITHUB_OUTPUT` |
| 条件跳过 | `rules: - if: '$SKIP == "true"' → when: never` | `if: needs.guard.outputs.skip != 'true'` |
| 版本号 | `$CI_COMMIT_TAG` / `$version` | `github.ref_name` / `github.event.inputs.version` |
| 仓库路径 | `$CI_PROJECT_PATH` | `github.repository` |
| 触发方式 | `$CI_PIPELINE_SOURCE` | `github.event_name` |
| Pipeline 链接 | `$CI_PIPELINE_URL` | `server_url/repository/actions/runs/{run_id}` |
| 创建 GitHub Release | curl + GitHub REST API（需 `GH_TOKEN` PAT） | 同左（curl + REST API），token 用自动注入 `GITHUB_TOKEN` |
| 创建 Gitee Release | 独立 job（`needs: [auto-release]`，powershell 镜像） | auto-release 内 step（`if: env.GITEE_TOKEN != ''`） |
| 失败告警 | `when: on_failure` + `GITLAB_TOKEN` 建 GitLab Issue | `if: failure()` + `gh issue create`（GITHUB_TOKEN 零依赖） |
| 产物 | `artifacts.paths: [notes.md]` | 同 job 内共享工作区；跨 job 用 upload/download-artifact |
| Runner | 逐 job `image:`（ubuntu / powershell 镜像） | `runs-on: ubuntu-latest`（git/python/pwsh 预装） |

**迁移要点**：
1. **凭据**（最关键）：`GH_TOKEN`（GitLab Variable）删除 → 改用内置 `GITHUB_TOKEN`
   （自动注入，配合 workflow 级 `permissions: contents: write`）；`GITLAB_TOKEN` 删除
   （告警改走 GitHub Issue）；`GITEE_TOKEN` 从 CI/CD Variables 挪到 GitHub Secrets
2. gitee-sync 从独立 job 收敛回 auto-release 内 step，notes.md 同 job 共享，
   无需 `artifacts` 跨 job 传递（GitLab 版独立 job 是语法限制所致，非业务需要）
3. GitLab 版新增的「GitHub Release 3 次×10s 重试 + 409/422 幂等冲突不重试」逻辑
   需回填到「创建 GitHub Release」step（`gh release create` 无内建重试，改用 curl + REST API）
4. 守卫正则（`^release\(pypi\)`）/ 发布备注 / 脚本逻辑（update_changelog.py、
   create_gitee_release.ps1）全部原样复用，只需换 YAML 外壳

## 9. bash 退出码捕获与调试经验

本节为 2026-08-06 在发布流程中发现的 bash 退出码陷阱的完整记录，
适用于本仓库所有含 bash `run:` 块的 workflow 排查。

### 9.1 陷阱机制

`if cmd; then ...; fi` 中 cmd 失败且无 `else` 时，**if 构造整体返回 0**，
其后的 `$?` 恒为 0（真实失败被吞）。

根因：if 构造的退出码 = 最后一个执行分支的最后一条命令的退出码；
条件为假且无 else 时，bash 约定返回 0。

### 9.2 三种调用形式实测（2026-08-06）

```bash
# FORM_A：直接捕获（安全）
cmd
FORM_A_RC=$?            # = cmd 退出码（1）

# FORM_B：if 构造后捕获（陷阱！）
if cmd; then :; fi
FORM_B_RC=$?            # = 0（真实失败被吞！）

# FORM_C：&& 短路后捕获（安全）
cmd && { echo SUCC; }
FORM_C_RC=$?            # cmd 失败时 = cmd 退出码（1）
```

实测结果：`FORM_A_RC=1`、`FORM_B_RC=0`、`FORM_C_RC=1`。

### 9.3 正确写法

```bash
# 写法一：else 分支内捕获（release-auto.yml Gitee step）
if pwsh -NoProfile -File scripts/create_gitee_release.ps1 ...; then
  echo "成功"
  exit 0
else
  RC=$?                 # $? 必须在 else 内，这里才等于失败命令的退出码
fi

# 写法二：&& 短路（.gitlab-ci.yml gitee-sync）
pwsh ... && { echo "成功"; break; }
RC=$?                   # 失败时 = pwsh 退出码

# 写法三：if 多分支时在每个命令后立即捕获（log-perf-guard.yml 修复后）
EXIT_CODE=0
if cond; then
  python scan.py
  EXIT_CODE=$?
else
  python scan.py --diff
  EXIT_CODE=$?
fi
echo "exit_code=$EXIT_CODE" >> $GITHUB_OUTPUT  # 不能用 $?：分支内 echo scan_mode 已覆盖
```

### 9.4 调试经验

1. **分层排查**：先用最小脚本验证跨语言退出码传播正常（`pwsh -File` 调用
   PowerShell 脚本，`$?` 可正确拿到 `exit 1`）→ 再直接验证脚本自身退出码
   （PowerShell `$LASTEXITCODE`）→ 最后用三种形式对比定位吞码点。
2. **排查清单**：凡 `$?` 出现在 `if/fi`、`while/done`、`&&/||` 之后，都要检查
   「真正关心其成败的命令」与捕获之间是否夹了必然成功的命令（echo、printf、
   赋值等）——任何成功命令都会覆盖 `$?`。
3. **set -e 注意**：GitHub Actions 的 `run:` 默认不开启 `set -e`，命令失败后脚本
   继续执行；若想在失败时立即退出，需显式 `set -e`（release-auto.yml 已启用
   `set -euo pipefail`）。两者结合时：`set -e` 下失败即退出、捕获逻辑仅对
   `set +e`/`||`/if 条件等场景生效。
4. **模拟验证**：本地可写临时 bash 脚本 + `pwsh -File` 直接验证退出码传播，
   不碰真实网络；验证后清理临时文件。

### 9.5 全仓库排查结果（2026-08-06）

全量扫描 `.github/workflows/*.yml` 与 `.gitlab-ci.yml` 中 21 处 `$?` 捕获点：

| 文件 | 位置 | 结论 |
|---|---|---|
| release-auto.yml | 178/239 | ✅ 已修复（else 内捕获，提交 1094a1c6） |
| .gitlab-ci.yml | 165 | ✅ 安全（`&&` 短路形式，见 §9.3 写法二） |
| ci-cd.yml | 177/470/481 | ✅ 安全（命令后直接捕获，无 if 包裹） |
| ci.yml | 290/689 | ✅ 安全（命令后直接捕获） |
| **log-perf-guard.yml** | **169** | ❌→✅ **本次修复**（fi 后 `$?` 被分支内 `echo scan_mode` 覆盖恒 0，改为分支内立即捕获） |
| reranker-timeout-guard.yml | 52 | ✅ 安全（命令后直接捕获） |
| semantic-perf-regression.yml | 60 | ✅ 安全（命令后直接捕获） |
| observability-ci.yml | 668/1110/1635/1670/1680 | ✅ 安全（直接捕获；1110 为 `EXIT_CODE=$?` + `exit $EXIT_CODE` 显式传播） |
| architecture-check.yml | 92 | ✅ 安全 |
| import-linter.yml | 70 | ✅ 安全 |
| kwarg-docker-scan.yml | 257 | ✅ 安全 |
| hardcoded-password-scan.yml | 138 | ✅ 安全 |
| config-drift-guard.yml | 67 | ✅ 安全（`cmd \|\| EXIT_CODE=$?` 标准写法） |

**本次修复详情（log-perf-guard.yml:169）**：
- 原缺陷：`if/elif/else` 三分支内最后一条命令均为 `echo "scan_mode=..."`（必然成功），
  `fi` 之后的 `echo "exit_code=$?"` 恒为 0 → python 扫描失败被吞 → 下游 PR 评论永远
  显示「✅ 无新增违规」。
- 修复：初始化 `EXIT_CODE=0`，每个 python 调用后立即 `EXIT_CODE=$?`，`fi` 后写
  `echo "exit_code=$EXIT_CODE"`。行为不变（step 仍以 echo 成功结束，提示而非阻断），
  仅让退出码真实传播。

## 10. 常见问题（FAQ）

### Q1. bash 命令退出码恒为 0，真实失败被吞？

这是 §9 记录的 **bash if 构造吞退出码陷阱**：`if cmd; then ...; fi` 中 cmd 失败且无
`else` 时，if 构造整体返回 0。快速自查：

```bash
if pwsh -NoProfile -File script.ps1; then echo SUCC; else RC=$?; fi  # ✅ RC 在 else 内
pwsh ... && { echo SUCC; }                                            # ✅ && 短路，失败时 $? 正确
if cmd; then echo A; fi; RC=$?                                        # ❌ 若 if 内最后命令是 echo，$? 恒 0
```

排查清单：凡 `$?` 出现在 `if/fi`、`while/done`、`&&/||` 之后，检查「真正关心成败的
命令」与捕获之间是否夹了必然成功的命令（echo/printf/赋值）。详见 §9。

### Q2. 本地模拟发布流程时 python/pwsh/curl 报 not found 或路径 FileNotFound？

**根因**：Windows 上 `bash` 命令实际是 WSL 的 Linux bash，与 Windows 网络栈、路径体系隔离。

| 现象 | 原因 | 解法 |
|---|---|---|
| `curl: (7) Failed to connect to 127.0.0.1` | Linux curl 走 WSL 网络栈，连不上 Windows 宿主上的 mock | 用 Windows curl：`/mnt/c/Windows/System32/curl.exe` |
| `python: command not found` / `pwsh: command not found` | WSL 里无 Linux 版 python/pwsh | 用 `python.exe` / `pwsh.exe`（WSL interop 调 Windows 程序） |
| `FileNotFoundError: '\\mnt\\c\\...'` | Windows 程序（python/curl/pwsh）解析不了 WSL 路径 | 传参前 `wslpath -w` 转成 `C:\...` 风格：`WIN_SIM_DIR=$(wslpath -w "$SIM_DIR")` |

**成功模板**（2026-08-06 v1.0.5 模拟验证）：
```bash
PY="python.exe"; PS="pwsh.exe"
CURL="/mnt/c/Windows/System32/curl.exe"        # 走 Windows 网络栈访问 mock
WIN_SIM_DIR=$(wslpath -w "$SIM_DIR")            # Windows 程序收参用
# bash 侧读文件用 WSL 路径（$SIM_DIR/...），Windows 程序收参用 WIN 路径（$WIN_SIM_DIR\...）
```
注意：以上只影响**本地模拟**；真实 GitHub Actions runner 是 Ubuntu，bash/pwsh/python
同一 Linux 环境、路径一致，不存在该问题。

### Q3. 网络超时（curl timeout）时不走重试、直接失败？

**隐患**（2026-08-06 修复）：创建 GitHub Release 的 `CODE=$(curl ...)` 在 curl 网络层
失败（超时/连接拒绝，退出码非零）时，会触发 `set -e` 终止整个 step，**跳过 while 重试
循环**——网络瞬时故障本应重试 3 次却第一次就 abort 并误触发告警。

**修复**（release-auto.yml「创建 GitHub Release」step）：
```bash
CODE=$(curl -s -o gh_resp.json -w '%{http_code}' --max-time 30 -X POST ...) || CODE=500
```
- `|| CODE=500`：把网络层失败（curl 退出码非零，`-w` 输出 000）映射为 HTTP 500 进入重试
- `--max-time 30`：防 GitHub API 挂起拖满 step 超时（timeout-minutes: 10）
- 响应体容错：网络失败时 `gh_resp.json` 可能不存在/为空，读前用 `[ -s gh_resp.json ]` 判断

**Gitee 侧对应防御**：`create_gitee_release.ps1` 的 `Invoke-Gitee` 增加
`TimeoutSec = 30` 默认值——否则 Gitee API 挂起时脚本无限等待，外层重试 step 静默拖满
10min 才失败。注意 Gitee step 用 `if pwsh ...; then else RC=$?; fi` 结构，pwsh 失败能
正确进 else 捕获（§9 写法一），不触发 set -e，重试循环本身是安全的。

### Q4. 发布失败但日志看不出原因？

按顺序排查：
1. 打开运行页 → 失败 step 的日志（关键 step 已启用 `set -x` 与时间戳 `[HH:MM:SS]`）
2. 看「响应体」输出：`==> 响应体: (无——网络层失败...)` 表示网络问题（对应 Q3）；
   有 JSON 则看 `message` 字段（401 token / 409·422 幂等冲突 / 5xx 服务端）
3. Gitee step 的 `create_gitee_release.ps1` 自带 HTTP 状态码分类输出（401/404/403/409/422）
4. 从 Artifacts 下载 release-notes 核对发布备注是否正常生成（定位在生成备注 step 还是发布 step）
