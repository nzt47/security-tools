# TESTINFRA-2 —— 「仅全量复现」的 2 条顺序相关红：**已定位并修掉**；「行号当键」：**已改为不漂移的稳定键**

| 项 | 值 |
|---|---|
| 卡号 | TESTINFRA-2（测试基础设施） |
| 会话开始时的 HEAD | `5c9ace10`（分支 `master`） |
| 会话**期间**仓库被**别的执行者**提交了一次 | `f74dce16`（2026-09-27 00:03:43，"48 卡批次"）⇒ 当前 HEAD=`f74dce16`。**本卡全程未执行任何 git 写操作**（无 add / commit / checkout / stash / worktree） |
| 本卡改动文件 | `tests/unit/test_date_shift_blindspots_guard.py`、`tests/unit/test_tool_count_consistency.py`（+ 本报告） |
| 未触碰 | `agent/skills_mgmt/`、`agent/tool_router*`、`agent/audit/`、`agent/descriptors/`、`.github/workflows/`、`plugins/`、`yunshu-ui/`、`config.yaml`、`data/` 运行期文件、prompt 装配四件套 |
| 出网 | 0 次 |
| 全量 | **未跑**（遵守约束 4；见 §⑤ U1） |

---

## 〇、结论先行

**A（主）—— 定位到了，而且是"机制闭环 + 三个确定性最小复现"级别的定位。**

那 2 条不是"顺序玄学"，是**进程级工具注册表被别的测试文件污染** + **同一条被持久化的激活主线**共同作用：

1. 有测试文件把**真实工具登记进** `agent.tools` 的**进程级**注册表 `_registry` 并且**不还原**（实测三个文件分别留下 7 / 8 / 91 个）；
2. `data/agent_lines/_active.json` 里**持久化**着 `{"active": "engineering"}`（2026-09-20 起就在盘上）⇒ `line_whitelist(None)` 恒把下发集收窄成主线 `engineering` 的那一份；
3. ⇒ `resolve_dispatch_tool_defs(None)` 返回的是**那 7/8/26 个真实工具**，本文件自己登记的 `b1_alpha/b1_beta/b1_gamma` **被整个挤出下发集**
   ⇒ `TestAdvertEqualsDispatched` 的**两条前置条件**当场失配 ⇒ **恰好那 2 条红**。

**B（次）—— 已改；键不再含行号，但精度未降、门未削弱。**

旧键 `文件::函数@行号` 换成 `(文件, 检测器:作用域#同作用域出现序号@证据指纹)`。16 条存量登记**全部机械迁移、0 条失配**；
在**真实镜像树**上复刻 TESTHYG-1 那次"顶部插入 60 行"：**新口径 0 条未登记、键逐字不变**，而**旧口径当场报出 `tests/conftest.py::_no_stray_approval_store@201`**（§19.3 那条假红被端到端复刻）。
"同一函数里**新增**盲点仍会被检出"由 3 条新用例钉住（其中一条是"把旧口径装回去 ⇒ 必然红"的对照臂）。

---

## ① A 的证据链（文件 / 行 / 键 / 调用路径）

### 1.1 机制：两条腿

```text
腿① 进程级注册表被写脏（污染源，多个文件）
    tests/unit/test_search_tools.py:31          register_all(dl)        ← fixture 内
      └─ agent/tools/__init__.py:190  register(...)  ⇒ _registry[name] = entry（:236）
      └─ 该 fixture 的 finally（:36-40）**只**还原 grep / edit 两个名字
         ⇒ 其余 7 个留在 _registry：write_file / list_directory / get_file_info /
            search_files / compress / decompress / diff_files
    tests/unit/test_policy_integration.py:203   file_tools_reg.register_all(_DummyDl())  ⇒ 留 8 个
    tests/unit/test_background_tasks_routes.py:140  import app_server（真实入口）
      └─ 触发整套内建工具登记 ⇒ 留 91 个
    tests/unit/test_legacy_memory_routes.py:94  同形（import app_server，fixture 内）

腿② 激活主线把下发集收窄成 engineering 的那一份
    data/agent_lines/_active.json = {"active": "engineering"}   ← 盘上持久化（2026-09-20 起）
    agent/lines/registry.py:95   get_active()
    agent/lines/integration.py:77 line_whitelist(None)  ⇒ 装配主线工具集（非空 ⇒ 覆盖白名单）
    agent/tools_prompt_guard.py:405 resolve_dispatch_tool_defs(None)
      ⇒ agent/tools/__init__.py:810 get_tool_defs(whitelist=<主线那一份>)
    tests/unit/test_tool_count_consistency.py:257/267  ← 两条受害者
```

**键**（本卡的"哪个键"）= `agent/tools/__init__.py:16` 的进程级 `_registry` 的**条目名**。
实测被污染时的下发集**逐字**（取自断言报文）：

| 污染源 | 残留工具数 | 下发集实际内容（断言报文逐字） | 报错 |
|---|---|---|---|
| `test_search_tools.py` | 7 | `write_file, list_directory, get_file_info, search_files, compress, decompress, diff_files` | `assert 7 == 3` |
| `test_policy_integration.py` | 8 | 同上 + `read_file` | `assert 8 == 3` |
| `test_background_tasks_routes.py` | 91 | 26 个主线工具（`search_memory, remember, read_file, …, run_lint`） | `assert 26 == 3` |

★ `26` 这个数**恰好**等于 `AUDIT_AND_PLAN.md` E6/B1 表里记的"实际注入的 tools[]（主线 engineering 白名单）= 26" —— 两条腿咬合的直接指纹。

### 1.2 三个**确定性**最小复现（两文件、`-p no:randomly`）

> 取证方式：临时把本卡新加的隔离夹具置 `autouse=False`（= 改动前的行为），跑完**立即还原**（残留自证见 §⑦）。
> 命令形如 `python -m pytest <污染源> tests/unit/test_tool_count_consistency.py -p no:randomly -q --no-header`

```text
### 1) search_tools 前缀
    assert tool_names_of(defs) == registered_tools, (...)
E   AssertionError: 前置条件：注册的测试工具必须真的出现在下发集里（实际 ['write_file', 'list_directory',
    'get_file_info', 'search_files', 'compress', 'decompress', 'diff_files']）
    assert registry_wide == len(registered_tools)
E   AssertionError: assert 7 == 3
FAILED ...::TestAdvertEqualsDispatched::test_真实注册表上宣告数与下发集长度一致
FAILED ...::TestAdvertEqualsDispatched::test_宣告数随白名单收窄_不等于注册表全量
======================== 2 failed, 54 passed in 3.29s ========================

### 2) policy_integration 前缀                → 2 failed, 61 passed（assert 8 == 3）
### 3) background_tasks_routes 前缀           → 2 failed, 31 passed（assert 26 == 3）
```

**"恰好这 2 条、且只在这 2 条"**与 v3/v4 全量里的形态**逐字同形**（`2 failed`，同类，其余 19 条全绿）。
探针（`os.environ` 式的脏写钩子，本卡打在**注册表**上）在三个组合里都记到同一次跃变：

```text
T2PROBE  n 0 -> 91 at tests/unit/test_tool_count_consistency.py ...
         names=['apply_patch','arch_diagram','browser_close',...]  active=engineering
```

### 1.3 为什么审计挑的两个"显而易见的组合"复现不出来

因为它们**恰好不脏**（实测，改后）：`test_server_routes_registration_inventory.py` → **38 passed**；
`test_env_hot_reload.py` → **62 passed**（审计登记的是 37 / 61）。
⇒ **污染源不在那两个文件里**，而在**有序文件集里排在目标之前、且会 `register_all`/`import app_server` 的那些**文件里 —— 这正是"折半二分"要找的东西，只是**每一个成员单独就足以复现**，所以最小复现是两文件而不是全量。

### 1.4 机制闭环（注入实验，独立于"找污染源"）

向注册表注入 30 个工具后再跑**单文件**：

```text
python -m pytest tests/unit/test_tool_count_consistency.py -p no:randomly -q -p t2inject
→ 2 failed, 19 passed      （= 与全量里那 2 条同一个集合）
E   AssertionError: assert 33 == 3
```

⇒ **"多出外来工具"这一条**就是充分原因，不需要别的假设（不需要网络、不需要计时、不需要 .env）。

### 1.5 我没能确定的一件事（如实登记）

**究竟是哪个文件在 v3/v4 的那一次全量里当的污染源，无法从留存的产物里回放**，因为：

* 全量入口 `scripts/run_full_pytest.py:239` 用 `-p no:randomly`（固定顺序）**且分块并行**（`chunks/workers` 运行时给），
  而 v4 那次**只留下了应用日志**（`test_reports/logs/test_20260926_200233.log`，24 MB，是 logger 输出、不是 pytest stdout），
  **没有任何一份 pytest stdout / nodeid 顺序**被存下来（我按 `22677 passed` 等标记全仓搜过，0 命中）；
* 因此"目标文件所在分块里、排在它前面的那个污染源"不可回放。
* **但这不影响结论**：污染源是一个**族**（§1.1 的三个成员各自单独即可复现），且在**固定顺序 + 分块**下，只要分块把任一成员与目标放在同块且排在其前，就必然复现 —— 与"v2 过、v3/v4 红"相容（分块归属随文件数与 worker 数变化）。

---

## ② A 的修法 +「改前红 / 改后绿」原始对照

### 2.1 修法：**受害侧加"注册表隔离"夹具**（断言一条都没改）

`tests/unit/test_tool_count_consistency.py:141-186` 新增 `@pytest.fixture(autouse=True) isolated_tool_registry`：

* 用例开始：把 `_registry` 的**快照**收好，然后**清空**（推进 `_registry_version` 让各级缓存失效）；
* 用例结束：把快照**逐条原样放回**（含 `source`/`schema`/`handler`/`source_id`）⇒ 别的文件看到的状态与用例前**完全一致**；
* **不用** `tools.clear()`（它会连 `_tool_health` 一起清）；只换 `_registry` 的内容，作用面最小。

**为什么不是"放宽断言"**：两条断言的语义是"宣告值 == 真实下发值"，它们在污染下**依然成立**；失配的是**前置条件**（"下发集 == 本文件登记的 3 个工具"）。
所以正确的修法是**隔离**（让前置条件成立），不是改判据。断言**逐字未动**（`git diff` 里那两条 `assert` 一行未改）。

**非空转自证（新增用例 `test_隔离夹具挡住外来登记_否则前置条件必失配`，`:224`）**：
在本用例内**故意**登记一个外来工具，断言它**真的会**进入下发集（`tool_names_of(leaked) == ["foreign_leaked_tool"]`）——
即"外来登记确实会打红那两条"，从而证明隔离夹具是**有牙齿的**（不是"加了夹具、问题自己好了"）。用例结束由夹具把它还原。

### 2.2 改前红 / 改后绿（同一命令、同一文件，只差那个夹具开关）

| 组合（`-p no:randomly`） | 改前 | 改后 |
|---|---|---|
| `+ test_search_tools.py` | **2 failed, 54 passed** | **57 passed** |
| `+ test_policy_integration.py` | **2 failed, 61 passed** | **64 passed** |
| `+ test_background_tasks_routes.py` | **2 failed, 31 passed** | **34 passed** |
| 单跑 | 21 passed | **22 passed**（+1 = 非空转自证用例） |
| `+ test_env_hot_reload.py` | 61 passed（审计登记） | **62 passed** |
| `+ test_server_routes_registration_inventory.py` | 37 passed（审计登记） | **38 passed** |
| 三个共用注册表的邻居一起跑 | — | **73 passed** |

---

## ③ B 的改动 +「不削弱」的证明 + 非空转自证

### 3.1 新的键格式（`tests/unit/test_date_shift_blindspots_guard.py`）

```text
旧： (相对路径, "相对路径::函数@行号")                         ← 行号硬钉 ⇒ 插入行即假红
新： (相对路径, "<检测器>:<作用域无行号>#<同(检测器,文件,作用域)出现序号>@<证据指纹>")
     例：("tests/conftest.py", "fs_clock_vs_today:_no_stray_approval_store#1@baf5d810")
         ("tests/unit/test_task_scheduler.py", "mtime_from_time_time:test_cleanup_old_logs_exception#1@aa58fcaa")
```

* **去掉行号** ⇒ 在上方插入任意行都不漂移；
* **保留 检测器 + 作用域 + 出现序号** ⇒ 精度不降（同一函数里第二处同形盲点仍是**另一个键**）；
* **加上证据指纹**（说明文本数字归一后的 sha1 前 8 位）⇒ **同一函数里新增盲点必然换键**（见 3.3①）；
* 关键实现：`_LINENO_TAIL` / `evidence_digest` / `hit_key` / `keyed_hits`（:661-710）；
  `_unexpected` 改为**按键**判定（:846）；`_fail` 直接打印**稳定键**（可直接粘贴）；
  新增 `--keys` 口径（打印可直接粘贴的登记条目）与 `--scan` 的键化输出（:1236-1250）。

**16 条存量登记全部机械迁移**（迁移助手逐条比对：`MATCHED 16 / 16`、`UNMATCHED_OLD:` 空）；
理由文本**逐字保留**（含 2026-09-20 那条裁定）。行号只在 `loc` 里保留给人读（`tests/conftest.py::_no_stray_approval_store@141` 仍在报文里）。

### 3.2 端到端「改前红 / 改后绿」（真实树 + 真实插入，跑在**镜像**上）

为了**不动**别的卡正在跑的工作区（`tests/conftest.py` 是全局文件，改它会影响它们的行号敏感守卫），
把 `tests/` 整树拷到仓库外（`…\Temp\testinfra2\mirror`，18 MB / 1031 文件，排除 `temp`/`__pycache__`），
在其中`tests/conftest.py` **顶部真实插入 60 行**（1444 → 1504 行，复刻 TESTHYG-1 那次插入），然后：

```text
MIRROR conftest.py: 1444 -> 1504 lines
NEW-KEY 口径：插入 60 行后未登记合计 = 0（期望 0）          ← 不再假红
KEY-DIFF（仓库 vs 插入后镜像）= []（期望 []）                 ← 键**逐字相同**
LEGACY-KEY 口径：同一插入下未登记合计 = 1（期望 > 0 = 复刻假红）
   ✗ [fs_clock_vs_today] tests/conftest.py::_no_stray_approval_store@201   ← §19.3 那条假红的端到端复刻
```

（LEGACY 臂 = 把旧口径 `(相对路径, loc 字面量)` 原样装回去 + 用"插入前"的旧表判定 —— 就是当时那次假红的判定链。）

### 3.3 「同一函数里出现**新的**盲点仍会被检出」——三条钉子（不许整条函数被豁免）

| 用例 | 构造 | 断言的性质 |
|---|---|---|
| `test_stable_key_does_not_drift_when_unrelated_lines_are_inserted` | 合成源 + 顶部插 60 行 | 键**不变**；且**非空转**：`loc` 确实漂了 60 行、旧键口径确实失配 |
| `test_same_function_new_blindspot_is_still_detected` | 同函数内**追加**一处盲点（`os.utime` + 比 `date.today()`） | **必须红**：位置部分（文件+函数+序号）**相同**，只有**证据**变了 ⇒ 键必须不同；先断言"已登记的旧形状是绿的"，再断言"新形状报未登记" |
| `test_same_function_new_detector_kind_is_still_detected` | 同函数内追加**另一类**盲点（`time.time()` 派生值写进 mtime） | **必须红**：跨检测器**不撞键**（键里含检测器名），且同函数 fs 侧证据也变了 ⇒ 那条也必须重新登记 |
| `test_real_conftest_registration_carries_no_line_number` | **真实** `tests/conftest.py` 内容 + 内存里插 60 行 | 真实那条登记**在表里**、键里**没有** `@\d+$`、插入前后键**逐字相同**；旧键口径确实失配 |
| `test_legacy_line_number_keys_would_have_gone_red` | **把旧口径装回去**跑真实判定链 | 同一插入**立刻报红**（对照臂：改前红可复算） |

**为什么这不是"削弱"**：旧键在"同函数新增盲点"上其实**抓不到**（`detect_fs_clock_vs_today`/`detect_mtime_from_time_time` **每函数只出一条命中**，位置相同则键相同 ⇒ 整条函数被登记豁免）。
新键把**证据**并入键 ⇒ 这一格被**补上**了（是**加强**，不是放宽）。
代价我如实登记：**检测器报文措辞**变化、或**同函数更靠前处**插入同形盲点，会让序号/指纹变化 ⇒ 报一次红要求重新登记（**保守误报**，不是漏报）。

### 3.3b 真实文件级的"同函数新增盲点"证明（最强版本，跑在镜像树）

§3.3 是**合成源码**上的钉子；这一条是**真实文件**上的：在镜像树的
`tests/conftest.py` 里，**在已登记的那个函数 `_no_stray_approval_store` 内部**插入 1 行新盲点
（`os.utime(__file__, (1_600_000_000, 1_600_000_000))` —— 属 FS 时钟侧），再另加一个含新盲点的**全新文件**：

```text
① 已在已登记函数 _no_stray_approval_store 内插入 1 行新盲点（os.utime）
② 已新增文件 tests/unit/test_zz_testinfra2_synth.py（1 处新盲点）
   ✗ [fs_clock_vs_today] tests/conftest.py::_no_stray_approval_store@141
      键=('tests/conftest.py', 'fs_clock_vs_today:_no_stray_approval_store#1@30d8d14c')   ← 原为 …@baf5d810
      why=文件系统时钟=['st_mtime_ns', 'utime']；Python 今天侧=['now']
   ✗ [fs_clock_vs_today] tests/unit/test_zz_testinfra2_synth.py::test_synth_new_blindspot@2
      键=('tests/unit/test_zz_testinfra2_synth.py', 'fs_clock_vs_today:test_synth_new_blindspot#1@122d20bb')
      why=文件系统时钟=['st_mtime']；Python 今天侧=['datetime.now']
★ 未登记合计 = 2（期望 2：已登记函数内的新盲点 + 全新文件）
```

⇒ 三件事同时成立：**①** 同一函数（位置串仍是 `@141`）里的新盲点**被检出**（键因证据变化而改变）；
**②** 全新文件的盲点**被检出**；**③** 除此之外**一条误报都没有**（合计恰好 2）。

### 3.4 非空转自证（汇总）

* 旧口径对照臂**真的红**（3.3 最后一行 + 3.2 LEGACY 臂）；
* 漂移用例里 `loc` **真的**漂了 60 行（否则用例自证"没测到东西"）；
* 扫描面下界用例（`@pytest.mark.slow`）与全部检测器正/反向自检**原样保留、全绿**；
* `--scan` 实跑：`未裁定合计: 0`，退出码 **0**；`--keys` 实跑：`未登记条目: 0`。

---

## ④ 回归结果（定向，全部实测）

| 项 | 结果 |
|---|---|
| `tests/unit/test_date_shift_blindspots_guard.py` | **24 passed, 1 skipped**（改前 19 passed / 1 skipped；+5 条新用例；slow 那条按既有机制 skip） |
| `tests/unit/test_tool_count_consistency.py` 单跑 | **22 passed**（改前 21） |
| 邻居三件套（`test_tools_prompt_alignment` + `test_advert_equals_dispatch_smart` + 目标） | **73 passed** |
| 三个污染源 + 目标（`-p no:randomly`） | **57 / 64 / 34 passed**（改前 2 failed ×3） |
| 审计登记的另两个组合 | `test_env_hot_reload` **62 passed**；`test_server_routes_registration_inventory` **38 passed** |
| 守卫 CLI | `--scan` → `未裁定合计: 0`（exit 0）；`--keys` → `未登记条目: 0` |
| `scripts/audit_governance_check.py` | **PASS（FAIL=0 / WARN=2）** —— 与 §19.5 终态一致 |
| 分块/全量 | **未跑**（见 ⑤ U1） |

**预算**：单文件跑 7–15 s，两文件组合 8–50 s；**没有跑过一次全量**（47–62 min 那种）。最大的两次是
`server_routes_registration_inventory`（50 s）与 `background_tasks_routes`（28 s）。

---

## ⑤ 未验证项与残留风险

**U1（最重要）全量未跑。** 约束 4 明令"不要跑全量 tests/unit"，本卡据此没有跑 22677 条。
⇒ "**改后全量里那 2 条不再红**"目前是**强推断**（3 个确定性最小组合逐字复现 + 注入实验闭环 + 修复后同组合全绿），**不是 v5 全量实测**。需要一张卡（或主审计）在收尾全量里确认。

**U2 污染源未修，只修了受害侧。** `tests/unit/test_search_tools.py:30-31`、`tests/unit/test_policy_integration.py:203`、`tests/unit/test_background_tasks_routes.py:140`、`tests/unit/test_legacy_memory_routes.py:94` 仍会把真实工具留在进程级注册表里（它们的文件不在本卡文件范围）。
⇒ **别的**测试文件若有同类"前置条件=注册表只有我登记的"，仍可能被同一机制打红（本卡未普查，样本是 33 个候选文件）。
建议后续卡（可复制的修法）：把这类 `register_all` 调用一律用"**快照-还原**"（本卡 `isolated_tool_registry` 的形状，10 行）包住；
**不建议**做成 `tests/unit/conftest.py` 的全局 autouse 隔离 —— 它会打掉 `module` 级夹具登记的工具，影响面 = 全量且本卡无法验证。

**U3 污染源清单是"至少这些"，不是"恰好这些"。** 我没有做 753 文件的完整二分（理由：每个成员单独即可复现 ⇒ 二分对**修法**没有增量价值；且预算应留给"机制闭环"而不是枚举）。
如果主审计要**完整名单**，给定一条命令即可枚举（`pytest <file> <目标> -p no:randomly`，单文件 ~7–15 s）。

**U4 污染源未被定位到"v4 那一次的具体那个文件"**（原因见 §1.5：没有留存 pytest stdout / nodeid 顺序）。

**U5 隔离夹具依赖 `agent.tools` 的内部结构**（`_registry` / `_registry_version`）。已在夹具 docstring 里写明：若内部结构改名，本夹具要跟着改（用例会当场报错，不会静默退化）。

**U6 B 的"证据指纹"是保守设计**：检测器报文措辞变化 ⇒ 报红要求重新登记（**宁可误报，不可漏报**）。已写进代码注释与 §3.3。

**U7 "同函数更靠前处插入同形盲点"会让序号位移** ⇒ 报红一次（保守误报）。这是序号方案的已知代价，已写进 §3.3。

**U8 `tests/conftest.py` 的全量影响未实测**：本卡**没有**在真实 `tests/conftest.py` 上做插入实验（只在镜像树与内存里做），
理由是当时另有两张卡在跑 pytest，改这个全局文件会污染它们的行号敏感守卫 —— **这是有意的规避，不是遗漏**。

**U9 会话期间仓库被别的执行者提交**（`f74dce16`，00:03:43）：本卡的三处改动（两个测试文件 + 本报告）**在提交之后**，因此 `git diff` 相对 `f74dce16` 只含本卡改动（已核）。

---

## ⑥ 回滚

```powershell
# ① 两个测试文件：f74dce16 里的内容就是"本卡改动前"的状态（提交时间 00:03:43 早于本卡首次编辑）
#    且这两个文件在提交后**只有本卡的改动**（已用 git diff 核对）⇒ 整文件 checkout 对**这两个文件**是安全的
git checkout -- tests/unit/test_date_shift_blindspots_guard.py tests/unit/test_tool_count_consistency.py

# ② 报告
Remove-Item docs/audit_skill_governance/TESTINFRA2.md
```

> ⚠️ 本卡的硬约束"禁止整文件 `git checkout`"针对的是**那 48 张卡的未提交改动**；上面的 checkout **只列了本卡碰过的两个文件**，且已核对它们的 diff 只含本卡改动。
> 不想动工作区的话，等价做法是 `git diff -- <两个文件> > 本卡.patch` 后手工反向应用。

**未回滚也不会有残留**：探针、镜像、原始日志全部在仓库外的临时目录（见 §⑦）。

---

## ⑦ 残留物自证

**仓库内**（`git status --porcelain` 全量，本卡会话结束时）：

```text
 M .github/workflows/skill-description-single-source.yml     ← 别的卡（CI-1）
 M .github/workflows/tool-retrieval-ci.yml                   ← 别的卡（CI-1）
 M agent/skills_mgmt/loader.py                               ← 别的卡（GATE-1）
 M docs/audit_skill_governance/AUDIT_AND_PLAN.md             ← 别的卡/主审计
 M tests/unit/test_date_shift_blindspots_guard.py            ← 本卡 ✔
 M tests/unit/test_skill_description_single_source.py        ← 别的卡
 M tests/unit/test_tool_count_consistency.py                 ← 本卡 ✔
 ?? .github/workflows/settings-registry-gap-guard.yml        ← 别的卡
 ?? docs/audit_skill_governance/GATE1.md                     ← 别的卡（GATE-1）
 ?? tests/unit/test_gate1_single_vector_quality_gate.py      ← 别的卡（GATE-1）
```

⇒ 本卡**只**改了 2 个已跟踪文件 + 新增 1 个报告文件（`docs/audit_skill_governance/TESTINFRA2.md`）。
**未触碰** `agent/`、`.github/`、`plugins/`、`config.yaml`、`data/`（`data/agent_lines/_active.json` 只读不改，运行前后 `size/mtime_ns` 未变，见 `audit_governance_check` 的自检块）。

**仓库外（探针与证据，均在 `C:\Users\Administrator\AppData\Local\Temp\testinfra2\`）**：

```text
t2probe.py          只读探针插件（注册表/激活主线的状态跃变，含文件归属）
t2inject.py         注入实验插件（向注册表注 30 个工具 ⇒ 复现那 2 条）
sweep1.ps1/.log     语义候选前缀扫描（16 个文件）
sweep2.ps1/.log     第二/三批候选扫描（17 个文件）
migrate_keys.py     16 条 ALLOWLIST 键的机械迁移助手
mirror_drift.py     镜像树端到端漂移验证（改前红/改后绿，真实插入 60 行）
mirror/             tests/ 的镜像副本（含插入 60 行后的 conftest.py）—— 仓库外
mirror_samefn.py    真实文件级"同函数新增盲点仍被检出"验证（§3.3b）
mirror2/            第二个镜像副本（含"已登记函数内新增盲点"+1 个合成新文件）—— 仓库外
before_raw.txt      "改前红"原始报文
b2.txt / b2b.txt    审计登记组合的原始输出
compare_after.log   改后绿对照
```

**其它卫生项**：

| 项 | 值 |
|---|---|
| `git stash` | **0** |
| `git worktree` | **只有主工作区** |
| 本卡执行的 git 写操作 | **0**（只有 `rev-parse/status/diff/log/reflog/check-ignore` 等只读） |
| 常驻服务 / 监听端口 | **0**（未起任何服务） |
| 被 taskkill 的 python 进程 | **0**（另两张卡的 pytest 全程未干预） |
| 后台作业 | 3 个（`pwsh-92/97/98`）**全部 completed**，无遗留 |
| pytest 产生的 `test_reports/logs/*.log`、`.pytest_tmp/*` | 由 pytest 既有机制产生，且被 `.gitignore:179` / `:40` 忽略（`git status` 里不出现） |
