# START-S11-11 —— 依赖约束与锁文件对齐（L1）

> **本文是自包含提示词**：新会话无任何前序上下文，所需事实、命令、证据与判据**全部写在本文内**。
> 直接整份复制到新会话即可执行。
>
> **来源**：S11-10 会话（架构环收口 + 存量红灯 CI 收口）遗留 **L1**，登记于
> `PARALLEL_S11批次_S10遗留待修项.md` §「S11-10 交付后遗留」。
> **性质**：**首先是决策任务，其次才是执行任务** —— 本任务**不允许**在"权威侧未定"前动手改依赖。

---

## 〇、任务与背景（一段话）

本仓 `pyproject.toml` 的 `dependencies`（声明）与 `requirements.txt`（pip-compile 生成的锁）**双向漂移**：
**9 条声明在锁里根本没有**，**13 条锁 pin 违反 pyproject 的约束**，其中 **8 条"锁 = 本机实际安装版本"**
⇒ 说明**实际运行环境是照锁/更新版本装的，而 pyproject 的约束明显滞后**（例：`chromadb` 约束 `<0.5.0`
却装 **1.5.9**；`torch` 约束 `<2.5.0` 却 2.12/2.13；`pypdf` 约束 `<6.0.0` 却 6.14.2）。
**为什么不能直接 `pip-compile` 重生成锁**：那只会把环境**降级到约束一侧**（chromadb 1.5.9 → **0.4.24**，
可能直接弄坏正在跑的服务环境），而且 **CI 根本不读锁文件**（CI 各 job 只做 `pip install -e .`）
⇒ 改锁**无法被 CI 验证**。因此本任务第一步是**逐包裁定"哪一侧是权威"**，第二步才动手。

---

## 一、工作区与隔离（先做）

```powershell
cd C:\Users\Administrator\agent
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
python scripts/dev/new_session_worktree.py create --id s1111 --base master
```

- **必须显式 `--base master`**（默认是 `develop`）；脚本会自动把 `.env` 以符号链接供给 worktree。
- 之后**所有命令都在 `C:\Users\Administrator\agent\.worktrees\s1111` 内跑**（每次 pwsh 都是新进程，先 `cd`）。
- **主工作区禁令**：✗ `git checkout`　✗ `git reset --hard`　✗ `git add -A`（只 add 具体文件）。
- 提交信息含中文/反引号时写进临时文件再 `git commit -F <文件> -- <具体路径>`。
- 证据一律落 worktree 内 `.tmp-s1111/`（`.gitignore` 的 `.tmp-*/` 已覆盖）。

---

## 二、先复现审计事实（只读，不改任何文件）

S11-10 已备好只读审计脚本（**可直接复用**：`.worktrees/s1110/.tmp-s1110/audit_r9f.py`，
拷到 `.tmp-s1111/` 即可）。它输出三节：

- **A) 声明了但锁里没有**（S11-10 实测 9 条）：`pyarrow`、`pandas`、`flask`、`waitress`、`pypdf`、
  `cssselect`、`pdfplumber`、`wmi`、`prometheus-flask-exporter`
  ⚠️ 其中 `pyarrow`/`pandas` 是 **S11-10 新加的**（为修 Shard 5 的原生栈守卫），其余 7 条是**早就缺的**。
- **B) 锁 pin 违反 pyproject 约束**（S11-10 实测 13 条），带"锁=本机 / 锁≠本机"标注：
  `psutil / torch / torchvision / torchaudio / chromadb / sentence-transformers / pyttsx3 /
  SpeechRecognition / pyperclip / selenium / lxml / watchdog / pynvml`。
- **C) 判读辅助**：13 条里 **8 条"锁=本机"** ⇒ 实际环境照的是锁。

**判据（务必先拿到这三节原始输出再往下走）**：把审计输出落 `.tmp-s1111/audit_before.txt`。

---

## 三、逐包裁定"权威侧"（本任务的**核心**，需 Owner 参与）

对 B 节每条，**必须给出可复现证据**再定权威侧，三选一：

| 判据 | 含义 | 需要的证据（**不得凭印象**） |
|---|---|---|
| **① 约束过期** | 代码/依赖已实际运行在新大版本上 ⇒ 应**上调 pyproject 约束** | 该包在**本机实际版本**下的相关测试/服务路径通过（指明命令与结果）；且被上调的范围不与其它依赖冲突（列出要求该包的依赖及其约束） |
| **② 锁过期** | 代码只能用旧大版本 ⇒ 应**重建环境到约束一侧** | 能指出代码/依赖确实依赖旧版 API 的证据（源码行级 or 依赖的硬约束）；并说明重建影响面（服务是否需停机、数据是否兼容，如 chromadb 向量库格式） |
| **③ 两者都不对** | 约束与实现均需调整 | 分别给出①与②的证据 |

**已知的两个高风险项（建议优先裁定）**：
- `chromadb`：约束 `<0.5.0`，实际/锁 **1.5.9**。chromadb 0.4 → 1.x 是**大版本跨越**，
  涉及向量库**持久化格式与 API**；`agent/` 内有真实依赖（向量存储/检索路径）。
  **必须先确认**：1.5.9 下现有数据与检索路径是否真的可用（跑相关套件 + 若有真实库则做只读探测）。
- `torch`：约束 `<2.5.0`，锁 2.12.0、本机 2.13.0+cpu。torch 只被 sentence-transformers 间接使用；
  证据应来自 `test_native_preimport` 相关链路与嵌入相关套件。

**产物**：`.tmp-s1111/authority_table.md`（每包一行：包 / 声明 / 锁 / 本机 / 权威侧 / 证据 / 风险）。

---

## 四、再动手（**只有权威侧全部裁定后**才执行）

按裁定结果分两类动作，**可分开提交**：

1. **上调/修订约束**（①类）：改 `pyproject.toml`，逐条写清理由（引上游 release/依赖硬约束 or 实测）。
2. **重建锁**：`pip-compile --output-file=requirements.txt pyproject.toml`。
   - ⚠️ 实测：该命令在本机需 **10~20 分钟**（全量重解析 torch/chromadb 树），**必须后台跑**。
   - ⚠️ 上一轮实测它会产出 **154 增 / 68 删**的大范围重解析；若权威侧已上调，diff 应显著缩小——
     **diff 大就说明还有约束没裁定完，停下来继续裁定，不要硬提**。
3. **环境对齐与验证**（②类或需要时）：`pip install -e .` 后跑受影响套件，
   并明确**是否需要重启 `127.0.0.1:5678`**（见 §六）。

---

## 五、验收（缺一不可，逐条给原始输出）

1. **审计零漂移**：改后重跑审计脚本 ⇒ A 节为 0 条、B 节为 0 条
   （即"声明全部在锁内且锁满足全部约束"）。
2. **约束可解析**：`python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"` 通过；
   并核**无解析冲突**（逐个列出要求该包的依赖及其约束，证明区间相交）。
3. **依赖矩阵门禁**：`python scripts/dev/check_light_loader_compat.py --json` → exit=0
   （该 job 会解析依赖矩阵，**必须复验**，它过去因依赖改动被抓过）。
4. **真实 CI**：push 后 `云枢系统测试流程`（`ci.yml`）**全部 job success**。
   判据与 S11-10 相同口径：**失败集与基线逐项相同**（基线 = S11-10 的 `02fec9c0`：21/21 全绿）。
5. **本地受影响套件**：至少 `tests/unit/test_native_preimport.py`、
   `tests/unit/test_env*.py`、`tests/unit/test_network_config*.py`、以及**向量检索相关**套件全绿。
   ⚠️ 本机跑全量 `tests/unit` **会挂起**（`test_resource_monitor.py` 触发 psutil 进程枚举挂起，
   `--timeout=600` 也拦不住）⇒ **全量回归认 CI**。
6. **产物漂移检查**：`git status` 干净（只应有你显式 add 的文件）。

---

## 六、环境事实（S11-10 实测，能省大量时间）

- 本机 `python` = **3.12.0**；CI = **3.12.14**（Linux）。`pip` 有网（PyPI 可查），
  `pip-compile` 已装（piptools **7.5.3**）。`github.com` 直连不通，但 **`gh` CLI 有网**。
- **`requirements.txt` 是 pip-compile 生成物**（头部注明
  `pip-compile --output-file=requirements.txt pyproject.toml`）；**CI 不读它**（只 `pip install -e .`）
  ⇒ 改锁**无法用 CI 验证**，这是本任务最需要小心的点。
- **CI 触发口径**：`architecture-check.yml` 只在 `paths: ['agent/**']` 变化时触发；
  `ci.yml` 每次 push 都跑（约 15~25 分钟，21 个 job）。
- **push 前必须先 `git fetch origin && git merge origin/master`**：门禁转绿后
  `architecture-check` 的"提交依赖图文档"步骤会**自动向 origin push** 一个
  `docs(architecture): 自动更新模块依赖图 [skip ci]` 提交，否则 non-fast-forward 被拒。
- **改到 `agent/**` 需要重启 5678**。口径：停进程 → 起 `app_server.py` → `GET /api/health` 验活。
  ⚠️ **S11-10 踩过的坑（务必照抄正确姿势）**：用 `Start-Process` 从**会话 shell** 起的进程
  **会在该次命令结束时被连带回收**（现象：health 连接被拒、日志无 traceback、末条是正常 heartbeat）。
  正确姿势是用 **WMI 脱离式启动**：

  ```powershell
  $py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
  $cmd = 'cmd.exe /c cd /d C:\Users\Administrator\agent && "' + $py + '" app_server.py >> logs\_s1111_stdout.log 2>> logs\_s1111_stderr.log'
  Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$cmd}
  # 复验：父链应为 python <- cmd <- WmiPrvSE.exe（不挂在会话 shell 下），且 60s 后再测一次 health
  ```

  另：`logs/`（`.gitignore:32`）与 `*.db`（`:170`）均已被忽略，测试写审计日志不会造成产物漂移。

---

## 七、违规项（**逐条都是红线**）

- ✗ **不要在权威侧未裁定时就 `pip-compile` 覆盖锁文件** —— 会把环境降级（chromadb → 0.4.24）。
- ✗ 不要用 `--upgrade` 让 pip-compile 自由升级（会引入一大批与本任务无关的版本变更）。
- ✗ 不要为了让门禁/检查变绿而放宽任何断言、跳过任何检查、伪造产物。
- ✗ 不要在**主工作区**做 `git checkout` / `git reset --hard` / `git add -A`。
- ✗ 不要只改 `requirements*.txt` 就宣称修好 —— **CI 不读它**，必须同时保证 `pyproject.toml` 正确。
- ✓ 数字要么实测、要么标"推算"；"数字变化"与"口径变化"分开讲。

---

## 八、回报格式

- **权威侧裁定表**（每包：声明/锁/本机/裁定/证据/风险），以及**未裁定项**（如实列出）。
- **改动文件清单** + 每个文件的改动理由（含上游依据或实测命令）。
- **验收逐条**：命令 + 原始输出（审计 A/B=0、light_loader 兼容、真实 CI 结论、受影响套件结果）。
- **锁文件 diff 的规模**与"是否仍有未裁定项"的判断依据。
- **是否需要重启 5678**：明确结论 + 依据（是否触及 `agent/**` 运行时字节）。
- **双远端 SHA**：`git rev-parse HEAD` / `origin/master` / `gitee/master` 三点一致。
- **文档**：回填 `PARALLEL_S11批次_S10遗留待修项.md` 的 L1 行（闭环或如实降级），
  并按需在 `docs/zh/CloudPivot_v7.2重构计划/` 出交付结案报告。

---

## 九、任务边界

- **只做 L1**（依赖约束与锁文件对齐）。**不要**顺手改其它遗留：
  - L2（环检测是否豁免动态边）属架构策略，需 Owner 另行裁定；
  - L3（本机装 flake8/ruff）低优先、与门禁无关；
  - L4（`import agent.monitoring` 裸包导入变重）是有意权衡，仅在启动耗时成为痛点时再评估。
- **S11-10 已闭环的事不要重做**：架构门禁真绿（`total=0/active=0/exempted=0`、豁免已清空）、
  `ci.yml` 21/21 全绿、`SystemRoot` 夹具跨平台化、CI 专用 mock 已删除、
  原生栈四件套（numpy/pyarrow/pandas/scikit-learn）已显式声明。
