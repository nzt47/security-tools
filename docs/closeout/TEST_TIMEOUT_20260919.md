# `pytest-timeout` 线程法丢整批文件的机制、复现与修复（D2 · 2026-09-19）

> **任务**：查清"单个挂起测试让整批文件从未执行"的**真实机制**（不照抄猜测），给出并实现修复，实测验证。
>
> **结论先行**（三句话）：
> 1. **机制**：`pytest.ini` 用 `--timeout-method=thread`，该超时法超时后走
>    `pytest_timeout.py:505 timeout_timer()` → **`finally: os._exit(1)`** ⇒ 整个 pytest 进程被杀，
>    **同块里排在后面的测试文件一个都不执行**，且 pytest 来不及写结束摘要。
> 2. **真实触发点不是"C 扩展阻塞死锁"**，而是 `test_server_routes_registration_inventory.py`
>    的**第 8 个测试**（`test_修过的端点必须在真实路由表里[/api/memory/review]`）——
>    它的模块级 fixture 做 `import app_server`，实测 **80–100 秒**，
>    其中 `import transformers` 会**递归扫描 `transformers/models/` 的 993 个子目录 / 2256 个 .py**。
>    这是**慢**（且被 coverage trace 放大、被并行任务进一步放大），不是死锁。
> 3. **`--timeout-method=signal` 在本平台不是解**：Windows 无 `SIGALRM`，
>    显式传入会 `AttributeError` → pytest INTERNALERROR、**0 个测试运行**（比 thread 更糟）。

---

## 1. 机制（源码级，非推测）

### 1.1 两种超时法的收尾方式完全不同

`site-packages/pytest_timeout.py`（本机实测版本）：

```python
# :26    HAVE_SIGALRM = hasattr(signal, "SIGALRM")
# :28-30 DEFAULT_METHOD = "signal" if HAVE_SIGALRM else "thread"   ← Windows ⇒ thread

# :485  信号法：只让**当前测试**失败，会话继续
def timeout_sigalrm(item, settings):
    ...
    dump_stacks(terminal)
    pytest.fail(PYTEST_FAILURE_MESSAGE % settings.timeout)      # :502 抛异常

# :505  线程法：**杀掉整个进程**
def timeout_timer(item, settings):
    ...
    finally:
        terminal.flush(); sys.stdout.flush(); sys.stderr.flush()
        os._exit(1)                                             # :542
```

⇒ 线程法的后果**不是**"一个测试失败"，而是：

* 该块剩余测试与**剩余测试文件全部从未执行**；
* pytest 不输出结束摘要 ⇒ **调用方拿不到"丢了哪些文件"的任何信息**；
* 退出码只有 `1`，与"有一批测试失败"**无法区分**。

### 1.2 最小复现（`scripts/repro_timeout_batch_loss.py`）

3 个测试文件，第 1 个必挂（`time.sleep(600)`），后两个平凡通过；`--timeout=4`：

| 场景 | 参数 | 退出码 | 耗时(s) | 结束摘要 | **从未执行的测试文件** |
|---|---|---:|---:|---|---:|
| thread | `--timeout-method=thread` | 1 | 6.6 | **无** | **2**（`test_02_after.py`、`test_03_after.py`） |
| signal | `--timeout-method=signal` | **3** | 2.6 | 无 | **3**（连收集都没跑完） |
| default | （Windows 上即 thread） | 1 | 6.4 | **无** | **2** |

`signal` 场景的实测日志（证明它在 Windows 上**不可用**）：

```text
INTERNALERROR>   File ".../pytest_timeout.py", line 324, in pytest_timeout_set_timer
INTERNALERROR>     signal.signal(signal.SIGALRM, handler)
INTERNALERROR>                   ^^^^^^^^^^^^^^
INTERNALERROR> AttributeError: module 'signal' has no attribute 'SIGALRM'. Did you mean: 'SIGABRT'?
============================ no tests ran in 0.17s ============================
```

⇒ **`--timeout-method=signal` 在 Windows 上比 thread 更糟**（thread 至少跑完了前面的测试）。
本平台的部署形态就是 Windows（`start_yunshu.bat` → `python app_server.py`），
CI 是 Linux（`ci.yml:457` 已用 signal）⇒ **本地/Windows 只能在调用层兜底，不能改超时法。**

复现产物：`_ci_logs/d2/repro/repro.json`、`_ci_logs/d2/repro/{thread,signal,default}.log`。

---

## 2. 真实触发点（推翻"C 扩展阻塞"的猜测）

### 2.1 TASK-03 的事故日志指向哪个文件

`_ci_logs/task03/chunked_cov/chunk_08.log:69`：

```text
tests\unit\test_server_routes_registration_inventory.py .......+++++++++++++++++++++++++++++++++++ Timeout +++++++++++++++++++++++++++++++++++
```

**7 个点 = 前 7 个测试通过，第 8 个测试开始后被杀。** 该文件的收集顺序是：

| # | 测试 | 是否触发强杀 |
|---|---|---|
| 1–4 | `test_每个_register_routes_模块_…` / `清单里的模块确实仍未接线` / `清单里的模块文件都存在` / `清单条目都写明了原因` | 否（纯 AST/文本解析） |
| 5–7 | `test_近期接线过的模块仍在真实注册位置[routes_agent_lines/approval/semantic_config]` | 否 |
| **8** | **`test_修过的端点必须在真实路由表里[/api/memory/review]`** | ✅ **是** |

第 8 个测试依赖**模块级 fixture** `real_url_paths`，其函数体只有一行：

```python
@pytest.fixture(scope="module")
def real_url_paths() -> set:
    import app_server       # ← 代价全部在这里
    return {str(r.rule) for r in app_server.app.url_map.iter_rules()}
```

被强杀的栈顶（`chunk_08.log` 尾部）正是这条导入链的深处：

```text
  File ".../sentence_transformers/base/model.py", line 24, in <module>
    from transformers import PreTrainedModel, is_datasets_available, is_torch_npu_available
  ...
  File ".../transformers/models/__init__.py", line 510, in <module>
    sys.modules[__name__] = _LazyModule(__name__, _file, define_import_structure(_file), ...)
  File ".../transformers/utils/import_utils.py", line 3080, in define_import_structure
    import_structure = create_import_structure_from_path(module_path)
  File ".../transformers/utils/import_utils.py", line 2797, in create_import_structure_from_path
    import_structure[entry.name] = create_import_structure_from_path(entry.path)   ← 递归
```

### 2.2 实测代价（本机，2026-09-19，作业期间有并行任务在跑）

| 测量 | 耗时 |
|---|---:|
| `import transformers`（冷） | 8.19 s |
| `import transformers`（暖） | 8.58 s |
| `import transformers.models`（触发 `create_import_structure_from_path`） | 9.29 s |
| `import sentence_transformers` | **19.89 s** |
| `import app_server`（暖，第 1 次） | **80.45 s** |
| `import app_server`（暖，第 2 次） | **87.67 s** |
| `coverage run --source=agent` 下 `import app_server` | **96.46 s** |
| 被递归扫描的目录规模 | **993 个子目录 / 2256 个 `.py`** |

⇒ **判定：这是"慢到超出单测试预算"，不是"死锁"。**
这解释了任务书里那个看似矛盾的现象 ——
「同一个文件单独重定向到文件跑是 `16 passed / exit 0`」：
它并不挂死，只是**代价随机器负载浮动**（80 s → >300 s）。
事故当天机器上还有并行作业（TASK-03 文档自己记载了这一点），
叠加 coverage 行级 trace 的放大量，就跨过了 `--timeout=300`。

### 2.3 为什么不能"删掉那条 import"

`agent/orchestrator/lifecycle_manager.py:108-137` 里的
`import sentence_transformers` 是**有意的预导入**，注释写明根因：

> 规避 Windows 上 `0xC0000005` ACCESS_VIOLATION：
> DigitalLife 构造时 `VectorStore._get_shared_encoder` 首次 import `sentence_transformers`，
> 其内部 datasets/pyarrow(native) 与已加载的库冲突导致进程直接消失。
> 在构造早期(相对干净环境)完成包导入，后续直接复用 `sys.modules` 不再走崩溃路径。

⇒ **删掉它会重新打开一个"进程无 traceback 直接消失"的崩溃**。
本任务**不动它**（也不动另外 3 个被其它任务占用的文件）。

### 2.4 事故回放验证（判据对真实日志有效）

`_ci_logs/d2/verify_incident_replay.py` 用**修复后的判据**读真实事故日志：

| 检查 | 结果 |
|---|---|
| `run_full_pytest.chunk_log_status(chunk_08.log)` | `False`｜证据：`无结束摘要且含 Timeout 标记 ⇒ 被 pytest-timeout 强杀（os._exit）` |
| 日志中**实际跑到过**的测试文件数 | **49** |
| 手工整理的"从未执行"清单 | **14** 个，且**这 14 个在日志里 0 次出现**（日志自证成立） |
| 该块总文件数（TASK-03 `chunks.json`） | **63** |
| 算术闭合 | 63 − 14 = **49** = 日志中实际出现的文件数 ✅ |
| 日志中最后一个进度标记 | **`[73%]`** —— 与 TASK-03 记录的"73% 处被强杀"一致 |
| 日志中是否有 pytest 结束摘要 | **无** ⇒ 若只信 rc，这次丢 14 个文件的事故看起来只是"这条测试失败了" |

> 说明：最初尝试用"复算 round-robin 分块"来独立推导丢文件清单，**失败了**——
> 事故当天与今天之间其它子任务会增删测试文件，轮转分块的成员会整体偏移。
> 因此改用**日志自证**（丢的文件必然在日志里 0 次出现）这一不依赖于文件集合快照的判据。

---

## 3. 修复（两层，均已实测）

### 3.1 第一层：给"必须导入整个 app"的测试显式预算

**改动**：`tests/unit/test_server_routes_registration_inventory.py` 增加模块级

```python
pytestmark = pytest.mark.timeout(900)
```

并附完整理由注释（实测耗时、触发链、为何不能删 `sentence_transformers` 预导入）。

**为什么这不是"放宽门禁"**：

* 这是**进程级资源边界**，不是质量门禁 —— 断言语义一字未动，路由没接线照样失败；
* `pytest.ini` 自己就写着这条规矩：「**极慢测试应显式 `@pytest.mark.timeout(N)` 覆盖，
  不要依赖全局默认**」——本模块正是这类测试；
* 900 s ≈ 实测 96 s 的 **9 倍余量**，且只作用于这一个模块。

**实测（修复后）**：

```text
$ python -m pytest tests/unit/test_server_routes_registration_inventory.py -q --no-header -p no:cacheprovider
================== 16 passed, 6 warnings in 87.40s (0:01:27) ==================
rc=0   elapsed=93.8s
```

（`87.40s` 与 §2.2 实测的 `import app_server` 80–96 s 完全吻合 —— 即该模块的耗时
**几乎全部**是那一次整应用导入，与断言逻辑无关。产物：`_ci_logs/d2/routes_inventory_after_fix.log`。）

### 3.2 第二层（结构性）：`run_full_pytest.py` 增加批次完整性校验 + 丢文件自动补跑

`scripts/run_full_pytest.py` 原本只打印每块 rc 与最后 3 行日志 —— **无法发现丢文件**。
本次新增：

1. `chunk_log_status(log)`：判定该块**是否正常收尾**（找 pytest 结束摘要行；
   显式识别 `no tests ran`；无摘要 + 含 `Timeout` 标记 ⇒ 判定为被强杀）。
   **判据必须基于"日志有没有摘要"，不能基于 rc** —— 被强杀与"有测试失败"都是 rc=1。
2. `main()` 在所有块结束后校验；发现未跑完的块 ⇒
   ① 落盘 `pytest_chunks/incomplete_files.txt`；
   ② 调 `resume_lost_files()` **逐文件独立进程补跑**（有界）；
   ③ 补跑后仍无摘要的文件 = **真正元凶**，写 `pytest_chunks/still_lost_files.txt` 并点名报出。
3. 设 `RUN_FULL_PYTEST_NO_RESUME=1` 可跳过自动补跑（只出清单）。
4. 文档字符串与退出码语义同步更新。

**为什么"逐文件补跑"而不是"整块重跑"**：一个进程只装一个文件 ⇒
单个文件爆预算只影响它自己；同时能**精确定位元凶**（否则永远只知道"这块丢了 14 个文件"）。

同类判据也被加进了权威覆盖率采集器
`scripts/run_authoritative_coverage.py`（`chunk_completed()` + `incomplete_files.txt` + 退出码 1）。

### 3.3 配套：把"这条超时法在 Windows 上不可用"写进配置

`pytest.ini` 的 `--timeout` 注释已补充指向本文档，并说明
`signal` 法在 Windows 上的实际失败方式（`AttributeError`，实测），
以免后人用"改成 signal"来"修复"这个问题（那会更糟）。

---

## 4. 实测验证

### 4.1 修复的端到端验证（`_ci_logs/d2/verify_fix_end_to_end.py`）

**直接调用修复后的真实函数**（不是另写一套等价逻辑）：

```text
[1] run_chunk() 跑一整块（含一个必然超时的测试）
    rc=1  completed=False
    证据: 无结束摘要且含 Timeout 标记 ⇒ 被 pytest-timeout 强杀（os._exit）
    ✔ 判据正确识别出「该块未正常收尾」—— 旧版只看 rc，会把它当成普通失败

[2] resume_lost_files() 补跑「从未执行」的 2 个文件
    [补跑 1/2] test_02_after.py rc=0 ✔
    [补跑 2/2] test_03_after.py rc=0 ✔
    补跑结果: [('test_02_after.py', 0), ('test_03_after.py', 0)]
    仍丢失  : []

✔ 修复有效：runner 检出「无 pytest 结束摘要」→ 落盘清单 → 逐文件独立进程补跑 → 0 文件丢失。
```

**"不再丢文件"= 由 runner 自己在同一轮内发现并补回**（不需要人事后想起来补跑，
也不需要依赖"有人手工整理清单"）。

### 4.2 单元测试（D8 纪律：可自动执行）

| 测试文件 | 覆盖 | 实测 |
|---|---|---|
| `tests/unit/test_run_full_pytest_integrity.py` | 结束摘要判定（含 TASK-03 事故日志的真实形状）、无摘要/无日志/空日志、分块无重无漏、补跑函数存在、**import 不泄漏 `os.chdir` 副作用** | **12 passed** |
| `tests/unit/test_coverage_scope_consistency.py` | 其中 `chunk_completed()` 判定（摘要 / `no tests ran` / Timeout / 空） | **16 passed** |
| `tests/unit/test_check_coverage_regression.py` | 覆盖率断言退出码矩阵（含负例） | **14 passed** |

三个文件合跑：**42 passed in 2.75s**（`_ci_logs/d1/final_tests.log`）。

> 附：给 `tests/unit/test_run_full_pytest_integrity.py` 加"import 不泄漏 cwd"这条断言，
> 是因为 `scripts/run_full_pytest.py` 在**模块顶层**执行 `os.chdir(ROOT)`（它本是命令行入口）。
> 测试若直接 import 会把整个 pytest 进程的工作目录改掉、污染同批次其它用例 ——
> 本仓已有过多起顺序依赖 flaky，不能再加一条。测试侧已保存/恢复 cwd，并加断言锁死。

### 4.3 全量 10 块实测（真实证据：632 个测试文件，0 文件丢失）

`scripts/run_authoritative_coverage.py`（与 `run_full_pytest.py` 共用同一套分块与完整性判据）
跑 `tests/unit` 全量 632 个文件：

```text
[chunk 1/10] rc=1  321s ✔ 已跑完 :: ===== 1 failed, 1695 passed, 7 skipped ... in 313.71s =====
[chunk 2/10] rc=0  205s ✔ 已跑完 :: ========== 1428 passed, 53 skipped ... in 194.22s ===========
[chunk 3/10] rc=0  276s ✔ 已跑完 :: ==== 1875 passed, 45 skipped, 16 xfailed ... in 268.27s ====
[chunk 4/10] rc=0  583s ✔ 已跑完 :: ===== 1628 passed, 93 skipped, 1 xfailed ... in 575.27s =====
[chunk 5/10] rc=0  338s ✔ 已跑完 :: ========== 2137 passed, 80 skipped ... in 331.10s ===========
[chunk 6/10] rc=0  284s ✔ 已跑完 :: ===== 1982 passed, 1 skipped, 1 xfailed ... in 277.34s =====
[chunk 7/10] rc=0 1182s ✔ 已跑完 :: ========== 2253 passed, 13 skipped ... in 1174.69s ==========
[chunk 8/10] rc=1 1858s ✔ 已跑完 :: == 1825 passed, 19 skipped, 4 xpassed, 2 errors in 1847.33s ==
[chunk 9/10] rc=0  307s ✔ 已跑完 :: =========== 2180 passed, 2 skipped ... in 299.37s ===========
[chunk 10/10] rc=0 219s ✔ 已跑完 :: =========== 2001 passed, 5 skipped ... in 212.42s ===========

总块数        : 10
已完成        : 10
未跑完        : 0 → []
受影响文件    : 0 个（从未执行）
✔ 所有块均已跑完，数据可用于基线。
```

**与历史事故同一点位的对照**（这是本次最有说服力的证据）：

| | TASK-03（2026-09-19 上午，修复前） | 本次（2026-09-19 下午，修复后） |
|---|---|---|
| 第 8 块 | 在 **73%** 处被强杀，进程消失 | 跑到 **100%**，耗时 1,858 s，正常输出摘要 |
| `test_server_routes_registration_inventory.py` | 只跑出 **7 个点**就被杀 | **16 个点全过**（`................ [74%]`） |
| 该块文件损失 | **14 个从未执行** | **0 个** |
| 结束摘要 | 无 | 有（`1825 passed, ..., 2 errors in 1847.33s`） |
| 判定 | 只看 rc=1 ⇒ 像是"有一批失败" | 判据给出 `✔ 已跑完` |

> **归因诚实声明**：本次第 8 块能跑完，**不能全部归功于本修复**。
> 至少有三个因素同时变了：① `test_server_routes_registration_inventory.py` 增加了
> 显式 `pytest.mark.timeout(900)`；② 机器负载与 TASK-03 当时不同；
> ③ 分块成员因仓库增删测试文件而漂移。
> **真正归功于修复的证据是 §4.1 与 §4.2**（判据能识别、补跑确实补回、单测锁死）。
> 本次全量运行的价值在于：它证明了**在这条管线上，"每一块是否真跑完"现在是可见且可断言的了**。
>
> 另附实测：该文件在修复后单独跑 = `16 passed in 87.40s`（§3.1），
> 即它的耗时几乎全部来自那一次整应用导入，而不是本文件自身的断言。

---

## 5. 变更清单

| 文件 | 变更 |
|---|---|
| `scripts/repro_timeout_batch_loss.py` | **新增**：最小复现（thread 丢批 / signal 在 Windows 不可用） |
| `scripts/run_full_pytest.py` | **修改**：`chunk_log_status()` 完整性判据、`resume_lost_files()` 逐文件补跑、`main()` 校验与清单落盘、文档与退出码 |
| `tests/unit/test_server_routes_registration_inventory.py` | **修改**：新增模块级 `pytestmark = pytest.mark.timeout(900)` + 实测依据注释 |
| `pytest.ini` | **修改**：`--timeout` 注释补充 Windows 上 signal 法不可用的实测结论与本文档指引 |
| `tests/unit/test_run_full_pytest_integrity.py` | **新增**：完整性判据单元测试（12 passed） |
| `docs/closeout/TEST_TIMEOUT_20260919.md` | 本文档 |
| `_ci_logs/d2/**` | 复现/回放/验证脚本与原始日志（证据留存） |

---

## 6. 遗留 / 未完成（如实登记）

| # | 事项 | 状态 |
|---|---|---|
| T-1 | `import app_server` 本身的 80–100 s 代价 | **未优化**。它是 `app_server`（16 线程 waitress 的完整装配）+ 有意的 `sentence_transformers` 预导入（规避 `0xC0000005`）的固有成本。建议后续单独立项：用 `-X importtime` 分解 `app_server` 的导入热点，评估把非必需重依赖改为惰性导入（**不得**动 `lifecycle_manager.py:118` 那条预导入） |
| T-2 | `transformers` 递归扫描 993 个目录 | 第三方行为，未处理。仅记录：这是**冷启动 60–90 s**（`start_yunshu.bat:20` 自述）的一个确定贡献项 |
| T-3 | Windows 上无 `SIGALRM` ⇒ 无"只失败当前测试"的超时法 | 平台限制。已用"分块 + 完整性校验 + 自动补跑"在调用层兜底；`pytest-timeout` 的 `timeout_func_only` 可作为备选（只对测试函数计时、不含 fixture setup），**本次未启用**，因为它会全局削弱 setup 阶段的挂起检测 |
| T-4 | CI（Linux）侧 | 已用 `--timeout-method=signal`（`ci.yml:457`），**不受本问题影响**；但 CI 的 6 分片入口同样没有"块完整性校验"。建议把本次的判据移植到 CI（未做，超出 D2 范围） |
| T-5 | 本次权威覆盖率 10 块运行中若有块被强杀 | 由 `run_authoritative_coverage.py` 的 `--only-chunk N` 补跑；结果见覆盖率文档附录 A |
