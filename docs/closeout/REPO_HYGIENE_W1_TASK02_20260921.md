# 云枢重构 W1 · TASK-02 仓库与磁盘卫生 —— 变更与证据台账

> 生成时间：2026-09-21
> 基线锚点：任务书给定 `bf698931`。**执行期间实测 HEAD 已前移**为
> `a21f2ee9`（`fix(gates): 消除两个门禁在 GBK 控制台下的"假红"`），
> 即本任务落在一个**已前进一格**的基线上；`bf698931` 是 `a21f2ee9` 的父提交
> （`git log --oneline -3`）。
> 所有移动均为**归档（移动）**，无任何删除。

---

## 1. 目标 1：根目录一次性产物归档到 `_scratch/`

| 指标 | 移动前 | 移动后 |
|---|---|---|
| 根目录磁盘文件 | **304** | **98** |
| 受版本控制 | 96 | 89 |
| 根目录 `.py` | **40** | **10** |
| 根目录 `.txt` | **177** | **7** |
| 根目录 `.log` | 5 | 0 |

归档 **205** 个文件、约 **35.9 MB**（`37,661,130` 字节），其中
`.py` 30 个、`.txt` 170 个、`.log` 5 个；6 个**已跟踪**文件用 `git mv`
（其余未跟踪/已被忽略，用 `Move-Item`）。逐条清单：
`_scratch/_moved_from_root.log`（205 行，`GITMV|` / `MOVE|` 前缀）。

迁移的 6 个已跟踪文件（`git mv`）：
`demo_full_stack.py`、`demo_production_deployment.py`、`demo_prometheus_export.py`、
`gen_mock_data.py`、`generate_guard_json_example.py`、`run_evolution_demo.py`。

保留的 10 个根目录 `.py`：`app_server.py`、`config.py`、`feature.py`、
`file_monitor.py`、`gunicorn_config.py`、`health_check.py`、`main.py`、
`run_tests.py`、`sensor_server.py`、`setup.py`。

保留的 7 个根目录 `.txt`：`requirements.txt`、`requirements-dev.txt`、
`requirements-test.txt`、`requirements-monitor.txt`、`auto_save_service.txt`、
`failures_baseline.txt`、`CI_VALIDATION_REPORT.txt`（后三个虽"像产物"，但
`.gitignore:533-537` 已明确其**仍被引用**，按该结论保留）。

### 移动前的引用检查（判据：无 `.py/.yaml/.toml/.ini/.cfg` 引用其模块名/文件名）

* **唯一真实 import**：`verify_budget_break.py:48` `from run_evolution_demo import (...)`
  —— 两者**一并**迁入 `_scratch/`，依赖关系保持在同一目录内，未断裂。
* `run_evolution_demo.py` 无其它 importer：`scripts/dev/verify_staged_eval.py:39` 明文
  "本脚本自包含：不 import run_evolution_demo"。
* `demo_full_stack.py`：`tests/unit/test_full_stack_demo.py:3` 只在 **docstring** 提及，
  无 `import`（该测试自带实现）⇒ 迁移后 13 项全绿。
* `generate_guard_json_example.py` / `demo_llm_guard.py` / `demo_guard_trace.py`：
  `tests/unit/conftest.py:616,669-689,874` 只引用 **`docs/guard_result_example.json` 等夹具产物**，
  且夹具缺失时 `pytest.skip`，不依赖脚本本体。
* 170 个调试 `.txt` / 5 个 `.log`：`.py` 中**零**引用。唯二命中是子串假阳性——
  `tests/unit/test_s7_06_mechanical_signals.py:248`（`test_single_run_is_not_applicable` 函数名）
  与 `scripts/inspect_stash_origins.py`（`inspect_stash_origins` 模块名）。
* 5 个 `.log` 均已停写（最新 `_ci_docs_job.log` = 2026-09-09 01:40；执行日 2026-09-21）。

---

## 2. 目标 2：修 `quality_gate_report.json` 的写入点

### 定位（文件:行号）

写入链**三段**，缺一不可：

1. `tests/unit/test_scripts_quality_gate.py:402-406` ——
   `test_require_e2e_flag_parsing` 组装的 `sys.argv` **只给 `--results-dir`，不给 `--output`**。
2. `scripts/observability_quality_gate.py:450` —— `--output` 的 argparse 默认值是
   **裸相对路径** `"quality_gate_report.json"`。
3. `scripts/observability_quality_gate.py:393` —— `open(self.output_file, 'w')`
   按 **CWD** 解析 ⇒ pytest 在仓库根运行时写到仓库根。

**实测铁证**（修复前仓库根那份报告的内容）：

```json
"results_dir": "C:\\Windows\\Temp\\pytest-of-AdminWT\\pytest-3977\\test_require_e2e_flag_parsing0",
"overall_status": "inconclusive", "passed_checks": 0, "failed_checks": 0, "skipped_checks": 6
```

`results_dir` 的临时目录名 `test_require_e2e_flag_parsing0` 与用例名逐字对应，
**指向唯一写入点**，非推断。

### 修改

* `scripts/observability_quality_gate.py`：新增 `DEFAULT_OUTPUT_FILENAME` 与
  `default_output_path()`（返回 `tempfile.gettempdir()/quality_gate_report.json`）；
  第 37 行 `output_file or "quality_gate_report.json"` → `output_file or default_output_path()`；
  第 450 行 argparse `default="quality_gate_report.json"` → `default=None`。
  **显式传 `--output` 的行为完全不变**（CI 契约保持）。
* `tests/unit/test_scripts_quality_gate.py:402-406`：补上 `--output str(tmp_path/"out.json")`。
* 新增回归类 `TestNoRepoRootPollution`（3 例）：① 默认路径必须在系统临时目录；
  ② 不传 `output_file` 的 checker 不指向仓库根；③ 省略 `--output` 跑完 `main()` 后
  仓库根**零新增、零改写**，且报告确实写到了兜底位置（防"什么都没写"的假绿）。
* 存量那份根目录 `quality_gate_report.json` → `_scratch/quality_gate_report.json`（未删）。

### 验证（D3）

`python -m pytest tests/unit/test_scripts_quality_gate.py -q -p no:randomly`
→ **34 passed**（原 31 + 新增 3），耗时 3.38s。
跑完后 `Test-Path quality_gate_report.json` = `False` ⇒ 污染已止。

---

## 3. 目标 3：`.gitignore` 收口（逐行）

| # | 变更 | 行号（改后） |
|---|---|---|
| 1 | 新增 `_scratch/` | 570 |
| 2 | 新增 `.env.corrupt_backup_*` | 574 |
| 3 | 新增 `env_archive/` | 577 |
| 4 | **移除** `/start_yunshu.bat`，替换为说明注释 | 436-442 |
| 5 | `security-tools/` 注释补复核结论（**规则保留**） | 317-326 |

`git check-ignore -v` 实测：`_scratch/x.txt` → `.gitignore:570`；
`.env.corrupt_backup_20260810` → `:574`；`env_archive/x.txt` → `:577`；
`security-tools/x` → `:326`（仍忽略）；`start_yunshu.bat` → **TRACKABLE**（已解除忽略）。

### `/start_yunshu.bat` 反向副作用 —— 处置结论

* **症状**：规则把一键启动入口对整个仓库隐藏 ⇒ 新克隆者拿到仓库却**没有启动脚本**。
* **根因**：忽略的理由（原注释）是脚本内含机器绝对路径，属"**不可移植**"而非"不该入库"。
* **落地**：把脚本内硬编码的 `C:\Users\Administrator\agent` 改为 `%~dp0`（脚本自身目录），
  子进程工作目录改用 `start /D "%ROOT%"`（避开 `cmd /k` 嵌套引号坑）。
  该写法**已实测**：探针 `_scratch/_bat_probe/probe.bat` 用含空格的 `sub dir` 验证，
  子进程 CWD = `...\_bat_probe\sub dir`（正确）。
  ⇒ 移除忽略规则使其纳管，并留注释禁止加回。

### `security-tools/` —— 处置结论：**保留忽略**

* **复核事实**：本机 `security-tools/` **没有** `.git`（只有
  `.git.legacy-20260808_181649` 这个改名后的旧 git 目录），也**不是** submodule
  （`Test-Path .gitmodules` = `False`）⇒ 它是 **25,813 个文件的普通目录**。
* **结论**：一旦取消忽略，这 2.5 万文件会整体涌入本仓库，故**必须继续忽略**。
* **获取方式**：`README.md:478-480` 已写明（独立仓库 `nzt47/security-tools`，
  `git ls-remote https://github.com/nzt47/security-tools.git` 可验证可达）⇒ 无需改 README。
* 未触碰其内容（符合任务红线）。

---

## 4. 目标 4：五个大目录的保留策略（**仅清单，未删除任何文件**）

| 目录 | 实测占用 / 文件数 | 内容性质 | 最后修改 | 是否仍被引用 | 建议 |
|---|---|---|---|---|---|
| `test_reports/` | 9.62 GB / 11,634 | **9.61 GB 集中在 `test_reports/logs/`**：`test_YYYYMMDD_HHMMSS.log` 测试运行日志（11,559 项） | **2026-09-21 19:04（本次 pytest 刚写入）** | **是，活跃写入中** | ⛔ **不可删**。仅可按"保留 N 天"轮转归档旧日志（建议 >30 天，2026-08-21 之前） |
| `.worktrees/` | 5.48 GB / 243,000 | 并行会话隔离工作树 | 2026-09-20（task09） | **是**：`git worktree list` 注册了 **8** 个（e2e-verify、p0-loadtoolmeta、s1003、s1107、s1108、s1109、s1110、task09） | ⛔ **不可删**。仅 `s601`(16 MB)/`s703`(2.5 MB) **未注册**，可在确认后走 `git worktree prune`（**非本任务动作**） |
| `.tmp-s1109/` | 1.73 GB / 91,897 | S11-09 会话产物：`base-9efbbd6e`/`scan-before`/`scan-after`/`scan-after2` 各 ~414 MB 目录树 + `base-9efbbd6e.zip` 65 MB + `gitleaks` 10 MB | 2026-09-16 | 否（会话已结案，`.gitignore` 已有 `.tmp-*/`） | ✅ **可归档/可删**（建议先整体打包归档到仓库外，再删） |
| `backup/` | 1.25 GB / 66,514 | `untracked_backup_*` 快照（2026-08-07 三份各 ~398 MB）+ `untracked_archive/` 13 MB + `parallel_session_tmp_20260806/` 1.5 MB + `logs/` | 2026-08-16 | 部分被 `.gitignore` 显式豁免（`!backup/logs/`） | ⚠️ **部分可归档**：三份 2026-08-07 `untracked_backup_*` 共 ~1.2 GB 可归档；`logs/` 保留 |
| `.pytest_tmp/` | 1.10 GB / 102,652 | `conftest._safe_tmp_directory` 的跨平台临时目录兜底重定向目标（大量微型 `cp_s704_test_stub_*`） | 2026-09-20 | **是，测试运行期自动重建** | ⛔ **不可删目录本身**（删了会重建）；可清空**旧子项**作临时回收，属运行期缓存 |

> 五个目录合计约 **19.2 GB**。其中 `test_reports/` 与 `.worktrees/`、`.pytest_tmp/`
> 三项（约 16.2 GB）**有活跃引用，不建议删**；真正可回收的是
> `.tmp-s1109/`（1.73 GB）+ `backup/untracked_backup_2026080*`（~1.2 GB）≈ **2.9 GB**。

---

## 5. 目标 5：根目录整洁度检查脚本

新增 `scripts/check_root_hygiene.py`（**只读**，stdlib only）：

```bash
python scripts/check_root_hygiene.py            # 人类可读
python scripts/check_root_hygiene.py --json     # CI 消费
```

退出码 `0`=达标 / `1`=有阈值突破 / `2`=环境错误。默认阈值
`--max-py 10 --max-txt 10 --max-oneshot 0`（对齐 W1 验收目标）。
一次性判定**保守**：仅"下划线前缀 或 `.log` 后缀"。

实测：`EXIT=0`（py=10、txt=7、log=0、oneshot=0）；
`--json --max-py 5` ⇒ `EXIT=1` 且 `violations` 列出该条。

**刻意不接入** `.pre-commit-config.yaml` / `.github/workflows`（任务书要求"可选接入，不要强接"，
且两者分属他人文件所有权）。

---

## 6. 验证汇总

| 验证 | 命令 | 结果 |
|---|---|---|
| 门禁单测（D3） | `python -m pytest tests/unit/test_scripts_quality_gate.py -q -p no:randomly` | **34 passed**（3.38s） |
| 迁移影响面回归 | `python -m pytest tests/unit/test_full_stack_demo.py tests/unit/test_skill_output_guard.py -q -p no:randomly` | **61 passed**（3.15s） |
| 污染是否止住 | 跑完上列用例后 `Test-Path quality_gate_report.json` | `False` ✅ |
| `.gitignore` 生效 | `git check-ignore -v` × 7 项 | 见 §3 ✅ |
| 整洁度脚本 | `python scripts/check_root_hygiene.py` | `EXIT=0` ✅ |

未跑全量回归（D10 要求 W1 不跑）。

---

## 7. 跨任务请求（**未动手**，请对应任务处理）

1. **TASK-06（`.github/workflows/**`）**：`.github/workflows/observability-ci.yml:1412`
   调用 `scripts/observability_quality_gate.py`。请确认该调用**是否显式传 `--output`**；
   若省略，则报告将落到 CI 的 CWD（本次改动后落系统临时目录，不再污染工作区），
   但若 CI 后续要 `actions/upload-artifact` 该报告，需显式给路径。
2. **TASK-06**：`scripts/check_root_hygiene.py` 可按需接入 CI（**未强接**，见 §5）。
3. **主会话**：本任务只 `git add` 了自有新文件；`.gitignore`、`scripts/observability_quality_gate.py`、
   `tests/unit/test_scripts_quality_gate.py` 为**已跟踪文件的修改**，按 D1 留给主会话统一提交。
4. **基线锚点漂移**：任务书给 `bf698931`，实测 HEAD = `a21f2ee9`（父即 `bf698931`）。
   对拍《06-基线台账.md》时请以 `a21f2ee9` 为准，或说明这一格差异。
5. **`_scratch/` 的最终归宿**：现为 `.gitignore` 忽略的归档区。若团队希望它**入库**
   （保留可追溯证据），需由主会话决定并 `git add -f`；本任务默认不入库。
