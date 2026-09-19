# pre-commit 触发验证报告（TASK-03 §2.7 / §3 第 4 步第 0 项 / **E10**）

> **日期**：2026-09-18 · **HEAD（作业时）**：`82bbe6fd297963148d7a6874a94012a04897b184`
> **结论一句话**：`.pre-commit-config.yaml` 里 11 个 hook **确实从未随 `git commit` 触发**；
> 其中两个阻断级检查（敏感数据扫描、关键字冲突扫描）**已实测被本地 hook 拦下**（本任务已修）。

---

## 1. `git config core.hooksPath` 的实际值

```
$ git config --get core.hooksPath
hooks
```

**相对路径**，相对于仓库根解析 ⇒ git 实际执行的是 `<repo>/hooks/pre-commit`，
而**不是** `<repo>/.git/hooks/pre-commit`。

**直接后果**：`.git/hooks/` 下的三个自制 hook 全部**被遮蔽**（dead code）：

| 文件 | 大小 | 内容摘要 | 状态 |
|---|---|---|---|
| `.git/hooks/pre-commit` | 5821 B | `TLM-HOOK v1`（`scripts/dev/sync_precommit_hook.ps1` 生成）：调用 `scripts/dev/git_precommit_check.ps1` 做文档链接 + 锚点回归；要求环境变量 `TLM_HOOK_SOURCE_REPO`，缺失则 **exit 1** | ❌ 被 `hooksPath` 遮蔽，**从不执行** |
| `.git/hooks/pre-commit.legacy` | — | 历史版本 | ❌ 从不执行（`.legacy` 后缀本身也不是 git 的 hook 名） |
| `.git/hooks/pre-commit.bak.20260814_031552` / `.bak.20260911_180853` | — | 备份 | ❌ 从不执行 |

⇒ **现状是"两套 hook 互相遮蔽"**：`.git/hooks/pre-commit` 以为自己守着提交，
实际根本没被调用；`hooks/pre-commit` 又只做两件事（见 §2）。

---

## 2. `hooks/pre-commit` 到底做了什么（逐段摘要）

文件共 41 行（**修改前**），三段：

| 段 | 行 | 内容 | 是否阻断 |
|---|---|---|---|
| ① 环境探测 | 1–8 | 找 `python`/`python3`；找不到就打印提示并 **exit 0**（放行） | 否 |
| ② 运行噪音清理 | 9–16 | `python scripts/clean_runtime_noise.py`（还原统计字段漂移 / 换行差异） | 是（非零即中止） |
| ③ 策略 schema 门禁 | 18–40 | **仅当** `git diff --cached` 里有 `data/policies/*.json` 时，跑 `scripts/check_policy_change_gate.py --schema-only` | 是（非零即中止） |

**关键否定证据**：

```
$ grep -n "pre-commit run\|pre-commit install" hooks/pre-commit
（零命中）
```

⇒ `hooks/pre-commit` **从不调用 `pre-commit run`**。因此 `.pre-commit-config.yaml` 里
声明的 11 个 hook 全部处于"配了但不会跑"的状态：

| # | hook id | 级别 | 是否随 `git commit` 触发 |
|---|---|---|---|
| 1 | `kwarg-conflict-scan` | **阻断（HIGH）** | ❌ 否 |
| 2 | `kwarg-conflict-scan-medium` | 提醒（`pre-push`） | ❌ 否 |
| 3 | `tool-index-sync` | 阻断 | ❌ 否 |
| 4 | `scan-sensitive-data` | **阻断** | ❌ 否 |
| 5 | `knowledge-cli-verify`（32 断言） | 阻断 | ❌ 否 |
| 6 | `cli-parser-register-check`（AST） | 阻断 | ❌ 否 |
| 7 | `check-index-isolation` | 阻断 | ❌ 否 |
| 8 | `ps-script-analyzer` | 阻断 | ❌ 否 |
| 9 | `logging-disable-leak-scan` | 阻断 | ❌ 否 |
| 10 | `policy-schema-gate` | 阻断 | ⚠️ **部分**：逻辑被复制进了 `hooks/pre-commit` 第 ③ 段，所以**等效生效** |
| 11 | `docs-broken-links-diagnose` | 阻断 | ❌ 否（但 CI 有兜底 job） |

⇒ **10/11 从未生效**，其中 **`scan-sensitive-data` 与 `kwarg-conflict-scan` 是纯防线型检查**
（不依赖 PR 上下文、本地就能判），它们"形同不存在"比"降级为告警"更严重。

---

## 3. ⭐ 实测："故意触发某个 hook 的提交，是否被拦"

### 3.1 方法说明（**为什么不是真的 `git commit`**）

本任务的硬约束禁止执行 `git commit`（提交由用户决定）。因此采用**等价且不产生提交**的方式：

```
git add <故意违规的文件>          # 造出与提交时完全相同的暂存状态
& "C:\Program Files\Git\bin\sh.exe" hooks/pre-commit   # 执行 git **实际会执行的那个** hook
```

覆盖率说明：这**完全覆盖**了"hook 被调用 + hook 判定 + 退出码"三段；唯一未覆盖的是
git 自身的"hook 返回非零 ⇒ 中止提交"这一步（git 的文档化行为，非疑问点）。
仓库里已有端到端用例 `tests/regression/test_precommit_hook_blocking.py::test_real_git_commit_blocked_by_hook`
（它当前在 `failures_baseline.txt` 中为 FAILED，原因见 §5）。

### 3.2 改动前的实测（证明 hook 确实会拦，但拦的是**另一个**检查）

暂存一个违反 schema 的策略文件，然后运行 hook：

```
$ git add -- data/policies/_task03_hookprobe.json
$ "C:\Program Files\Git\bin\sh.exe" hooks/pre-commit
[clean-runtime-noise] data/learned_workflows.json: 8 条统计漂移，已还原
[pre-commit] 检测到策略文件变更，运行 schema 门禁...
[FAIL] data/policies/_task03_hookprobe.json: 装载失败: 策略文件结构无法识别
[pre-commit] 策略 schema 门禁不通过（退出码 1），已中止提交
[pre-commit] 修正策略文件后重试；确需跳过请设 SKIP_POLICY_GATE=1 并在提交说明中交代
HOOK_EXIT=1
```

**判定**：hook **本身工作正常**（会拦、退出码 1、并给出逃生通道）；问题在于
**它只做策略 schema + 运行噪音清理两件事**，`.pre-commit-config.yaml` 的 11 个 hook 不参与。

### 3.3 改动**前**，敏感数据扫描会不会拦？—— 不会（它压根不跑）

`scan-sensitive-data` 不在 `hooks/pre-commit` 里，所以**无论暂存什么文件它都不运行**。
这正是"等于不存在"的实证。

### 3.4 改动**后**的实测（本任务新增的本地防线确实拦得住）

`hooks/pre-commit` 末尾新增一段（见 §4），再跑同一个实验：

**负例（暂存一个含假 API key 的文件）**：

```
$ git add -- task03_hook_probe_secret.txt
$ "C:\Program Files\Git\bin\sh.exe" hooks/pre-commit
[pre-commit] 关键字参数冲突扫描（HIGH 阻断）...
{"module_name":"scan_kwarg_conflicts","action":"scan_directory.done","files_scanned":597,"findings_count":102}
{"module_name":"scan_kwarg_conflicts","action":"scan.exit","high_count":0,"exit_code":0}
🚫 检测到敏感信息，提交已阻断！
   [API_KEY] L2 - OpenAI/DeepSeek style API key (sk-xxx...)
[pre-commit] 敏感信息扫描不通过（退出码 123），已中止提交
[pre-commit] 确需跳过请设 SKIP_PRECOMMIT_PARITY=1 并在提交说明中交代
HOOK_EXIT=123
```

（退出码 123 是 `xargs` 转发"子命令非零退出"的约定值，非 0 即阻断。）

**正例（当前工作区，无违规暂存文件）**：

```
$ "C:\Program Files\Git\bin\sh.exe" hooks/pre-commit
[pre-commit] 关键字参数冲突扫描（HIGH 阻断）...
HOOK_EXIT=0
```

**E10 满足**：`core.hooksPath` 实际值 + `hooks/pre-commit` 内容摘要 +
"故意触发是否被拦"的实测（**改动前后各一次**）全部给出，并附处置。

**附带发现（值得单独记一笔）**：`scan_sensitive_data.py` 会**自命中**。
`agent/policy/taint.py:84` 的 `SECRET_VALUE_PATTERNS` 里写着"匹配 OpenSSH 私钥头"的
正则字面量 —— 那是**识别私钥的正则**，不是私钥，但会被本扫描器当成 `[PRIVATE_KEY]`。
更妙的是：在扫描器自己的注释里写下那段字面量后，**它连自己也报**
（实测 `scripts/scan_sensitive_data.py:111` 被自己命中）。
⇒ 该自命中此前从未暴露，正因为这个扫描**从未运行过**。处置见 §4.2。

---

## 4. 处置（TASK-03 给了两个选项：修 hook 路径 / 下沉 CI）

**两个都做了，但主次分明。**

### 4.1 本地：把两个防线型检查**内联**进 `hooks/pre-commit`（主）

**为什么不改 `core.hooksPath` 去启用框架 hook**：改 `hooksPath` 会同时改变运维对
`.git/hooks/` 的既有预期（§1 里有三个自制 hook 依赖该目录，其中 TLM 链接预检
目前已因遮蔽而失效）。**内联只增加检查，不改变既有行为**，风险显著更低。

新增段落（`hooks/pre-commit` 末尾，`exit 0` 之前）：

```sh
if [ "${SKIP_PRECOMMIT_PARITY:-0}" = "1" ]; then
  echo "[pre-commit] SKIP_PRECOMMIT_PARITY=1：按显式声明跳过...（须在提交说明中交代）" >&2
else
  "$PY" scripts/scan_kwarg_conflicts.py --path agent/ --min-risk HIGH
  git diff --cached -z --name-only --diff-filter=ACMR 2>/dev/null | xargs -0 -r "$PY" scripts/scan_sensitive_data.py
fi
```

设计要点（每条都有理由，写进了文件注释）：

| 点 | 理由 |
|---|---|
| 用 `-z` + `xargs -0 -r` 传文件名 | 仓库里有中文路径；`-r` 保证"无暂存文件时不调用"（否则会退化成全仓扫描） |
| 敏感扫描只扫**暂存**文件 | 与 `.pre-commit-config.yaml` 的 `pass_filenames: true` 语义一致 —— 只判"这次要进历史的东西" |
| 提供 `SKIP_PRECOMMIT_PARITY=1` | 沿用本文件既有的 `SKIP_POLICY_GATE=1` 约定；强制"在提交说明中交代" |
| 不用反斜杠续行 | 遵循文件顶部既有警告（CRLF 会吃掉续行）；已实测 **CRLF count = 0**、`sh -n` 退出 0 |

**实测耗时**（本机）：`kwarg-conflict-scan` **~13s**（全量扫 `agent/`，597 文件）、
`scan-sensitive-data` **~5s**（仅暂存文件）。合计每次提交约 **+18s**，可接受。

### 4.2 同时修掉扫描器的自命中（否则新本地防线会误拦）

`scripts/scan_sensitive_data.py` 的 `WHITELIST_PATHS` 增加 `agent/policy/taint.py`，
并写明理由与**残余风险**：按路径豁免会让该文件内的真实泄漏不被扫到；
更细的做法是"仅豁免位于 `re.compile(...)` 实参内的匹配"，需要改扫描器逻辑 ——
登记为债务（`REPO_HYGIENE_20260918.md` §6）。
同一手法在仓库里已有先例（`gitleaks-config.toml` 因含 PEM 测试样例而被豁免）。

修完之后：**全仓 tracked 扫描通过**（`python scripts/scan_sensitive_data.py` → `✅ 未检测到敏感信息`，exit 0，18.2s）。

### 4.3 CI：把两个检查下沉为**非 `|| true`** 的阻断步骤（兜底）

`.github/workflows/ci.yml` 的 `code-quality` job 新增两步：

```yaml
      - name: 敏感数据扫描（pre-commit 阻断级检查下沉 · TASK-03 E10）
        run: python scripts/scan_sensitive_data.py
      - name: 关键字参数冲突扫描 HIGH（pre-commit 阻断级检查下沉 · TASK-03 E10）
        run: python scripts/scan_kwarg_conflicts.py --path agent/ --min-risk HIGH
```

**为什么本地 + CI 都要**：本地 hook 只能防"本机 + 正常提交"，
`git commit --no-verify`、其它 git 客户端、直接 push 都能绕过。CI 是唯一的兜底。

---

## 5. 与 `tests/regression/test_precommit_hook_blocking.py` 的关系（**重要**）

`failures_baseline.txt:20` 里有一条：

```
FAILED tests/regression/test_precommit_hook_blocking.py::test_real_git_commit_blocked_by_hook - AssertionError: No .pre-commit-config.yaml file was found
```

**这条失败恰好说明仓库自己知道这个缺口**：该用例试图在临时仓库里跑真实的
`git commit` 并断言被 hook 拦下，但因为临时仓库里没有 `.pre-commit-config.yaml`
（它只在主仓根目录），`pre-commit` 框架直接报 "No .pre-commit-config.yaml file was found"。

**本次不做的事**：不修这条用例。理由：(1) 它属于 `tests/regression/`，
不在本任务的验证入口（`tests/unit`）内；(2) 修它需要决定"到底以框架 hook 为准
还是以自定义 hook 为准"，那是一个**架构口径决定**，不属于 TASK-03 的"地基加固"范围；
(3) 本任务已经把**实际行为**测清楚并写进本文档，比修一条用例更有价值。

**建议**（登记为债务）：TASK-04 起明确"本地提交入口到底是 `hooks/pre-commit` 还是框架 hook"，
然后要么把该用例改成针对 `hooks/pre-commit` 的断言，要么统一到框架 hook（`pre-commit install`）。

---

## 6. 复现命令（任何人可重跑本节全部证据）

```powershell
cd C:\Users\Administrator\agent

# 1) hooksPath 实际值
git config --get core.hooksPath

# 2) hooks/pre-commit 是否调用框架
Select-String -Path hooks\pre-commit -Pattern "pre-commit run"

# 3) 本地 hook 正例（当前工作区应通过）
& "C:\Program Files\Git\bin\sh.exe" hooks/pre-commit; $LASTEXITCODE   # 期望 0

# 4) 本地 hook 负例（造一个含假 key 的暂存文件；演示后务必清理）
#    注意：必须 git add 才能被 scan_sensitive_data.py 看到（它只扫 tracked 文件）
"OPENAI_STYLE_KEY = sk-QZ7xm4Rt9Lp2Wn6Bv1Kd8Hs3" | Set-Content task03_hook_probe_secret.txt -Encoding utf8
git add -- task03_hook_probe_secret.txt
& "C:\Program Files\Git\bin\sh.exe" hooks/pre-commit; $LASTEXITCODE   # 期望非 0
git restore --staged task03_hook_probe_secret.txt; Remove-Item task03_hook_probe_secret.txt

# 5) CI 兜底两步（全仓口径）
python scripts/scan_sensitive_data.py
python scripts/scan_kwarg_conflicts.py --path agent/ --min-risk HIGH
```
