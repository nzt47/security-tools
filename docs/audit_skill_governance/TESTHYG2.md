# TESTHYG2 —— 「把真实工具泄漏进**进程级**注册表」的污染源：**找全 9 个，全部在源头修掉**

| 项 | 值 |
|---|---|
| 卡号 | TESTHYG2（测试卫生 / 独立工程卡） |
| 仓库 / 分支 | `C:\Users\Administrator\agent` / `audit/skill-governance-v1.0` |
| Python / pytest | 3.12.0（系统解释器 `python`；`venv/` 是空壳，全程未用） |
| 本卡改动的文件 | `tests/unit/test_search_tools.py`、`test_policy_integration.py`、`test_background_tasks_routes.py`、`test_legacy_memory_routes.py`、`test_graceful_shutdown_persist.py`、`test_health_retrieval_endpoint.py`、`test_server_routes_registration_inventory.py`、`test_yunshu_mcp_server.py`、`test_digital_life_comprehensive.py`（+ 本报告） |
| **未触碰** | `tests/unit/test_tool_count_consistency.py`（受害侧，**一字未改**）、`tests/unit/conftest.py`、`tests/conftest.py`、任何 `agent/` 生产代码、`agent/tools/__init__.py` 语义 |
| git 写操作 | **0 次**（只跑过 `git status` / `git diff`） |
| 出网 | **0 次** |
| 全量 `tests/unit` | **未跑**（遵守约束；最大的一次是 34 个文件的定向回归，见 §③） |

---

## 〇、结论先行

1. **污染源找全了：9 个**（TESTINFRA-2 登记的 4 个 + 它没提到的 5 个）。每一个**单独**与
   `tests/unit/test_tool_count_consistency.py` 合跑，都能**确定性地**打出那 2 条
   `TestAdvertEqualsDispatched` 红（实测 `2 failed`，逐文件见 §③）。
2. **9 个全部在源头修好**：每个泄漏点在 `finally` 里对进程级注册表做
   **整表快照 → 逐条还原 + 推进 `_registry_version`**（形状照抄受害侧
   `test_tool_count_consistency.py:171-174` 的 `isolated_tool_registry`）。改后同一命令
   **全绿**，且隔离探针实测**泄漏 = 0**。
3. **没有削弱任何守卫**：本卡 diff 里 **0 条** `assert` / `skip` / `xfail` / `pytest.mark` 改动
   （§④ 原始 `git diff` 取证）；受害侧文件根本没被改；未新增任何 skip/xfail。
4. **没做全局 autouse 隔离**（沿用 TESTINFRA-2 的裁定）。9 个修法里 2 个是**文件内**的
   autouse 夹具，其余 7 个是**逐站点** `finally` 还原 —— 都不碰 `tests/unit/conftest.py`。
5. 顺带更正了一个**我自己第一版探针的 bug**（会凭空造出 5 个"假污染源"），如实登记在 §①.3。

---

## ① 方法：怎么"找全"，以及判别口径

### 1.1 枚举规则（为什么这份清单可以声称"全"）

进程级注册表只有一处：`agent/tools/__init__.py:16 _registry`。能往里**加条目**的路径是有限集合，
我对整个 `tests/` 树做了穷举 grep：

| 入口 | 命令 |
|---|---|
| `register(` / `register_dynamic(` / `register_all(` | `grep -rn "register_all\(|_tools\.register\(|tools\.register\(" tests` |
| 直接改 `_registry` | `grep -rn "_registry" tests` |
| `import app_server`（整内建集登记，实测 91 个） | `grep -rn "import app_server" tests` |

得到的候选集 = `tests/unit` 下 34 个文件 + `tests/test_dynamic_tools.py`。
**没有第二种写入路径**（`agent/tools` 各模块只在 `register_all()` 里登记，`import` 本身不登记；
探针的 COLLECT 阶段实测增量恒为 0，反证了"导入期副作用登记"不存在）。

### 1.2 三件取证工具（全在仓库外，仓库内零新增文件）

放在 `%LOCALAPPDATA%\Temp\testhyg2\`，用 `-p <name>` 挂载：

| 探针 | 作用 |
|---|---|
| `t2h_probe.py` | 逐模块记录 `_registry` 在**模块边界**的增量（夹具 teardown 之后取样） |
| `t2h_reset2.py` | **每个模块开始前把注册表复位到会话初态** ⇒ 精确测"单文件泄漏"（否则前面的污染会把后面的增量吃掉，例如 `app_server` 已登记过就再也测不出第二个文件也泄漏） |
| `t2h_noiso.py` | 内存里摘掉受害侧 autouse 夹具 `isolated_tool_registry`（**不改仓库文件**）⇒ 复刻"TESTINFRA-2 加隔离之前"的判定；`T2H_NOISO_DROP_SELFCHECK=1` 时同时排除受害侧那条"隔离夹具存在性自证"用例（它按构造依赖隔离夹具，见 §①.4） |

### 1.3 ⚠ 我第一版探针的 bug（会凭空造出假污染源）——如实登记

第一版 `t2h_reset.py` 用的是**非 hookwrapper** 的 `pytest_runtest_teardown`，它**在夹具 teardown 之前**取样
⇒ 把"最后一个用例的夹具本来会还原掉"的条目算成了泄漏。实测后果：它多报了 5 个"污染源"
（`test_advert_equals_dispatch_smart.py +6`、`test_injection_end_to_end.py +1`、
`test_tool_approval_e2e.py +1`、`test_tool_gate.py +1`、`test_subagent_delegate_tool.py +1`）。

改 `hookwrapper=True` 后（`t2h_reset2.py`）这 5 个**全部变 CLEAN**，且它们与受害侧合跑**全绿**
（负对照，见 §③.3）。**本报告的清单以 hookwrapper 版为准。**

### 1.4 受害侧已被 TESTINFRA-2 隔离 ⇒ 必须摘掉隔离才看得到红（自我披露）

TESTINFRA-2 给受害文件加了 autouse 的 `isolated_tool_registry`，因此**污染源未修时**
`pytest <污染源> tests/unit/test_tool_count_consistency.py` **也是全绿**的（污染被受害者侧挡住）。
所以"改前红"必须用 `t2h_noiso` 把那条隔离在**内存里**摘掉——**仓库文件一个字节都没动**。
摘掉隔离后，受害侧那条自证用例（`test_隔离夹具挡住外来登记_…`）按构造不成立（它开头断言
`dict(_tools._registry) == {}`、末尾断言外来工具进了下发集），故一并 `--deselect` 掉；
**这一点在报告里明说，不藏在脚注里**。两处处置都只影响"取证口径"，不影响任何生产/守卫语义。

### 1.5 判别命令（§③ 所有数字都出自这条模板）

```powershell
$env:PYTHONPATH = "$env:LOCALAPPDATA\Temp\testhyg2"
$env:T2H_NOISO_DROP_SELFCHECK = "1"
$env:T2H_OUT = "$env:LOCALAPPDATA\Temp\testhyg2\x.txt"
python -m pytest <污染源文件> tests/unit/test_tool_count_consistency.py `
    -q -p no:randomly --timeout=60 -p t2h_noiso -p t2h_probe
```

---

## ② 污染源完整清单（9 个，逐个实测）

泄漏量 = "该文件**单独**跑完之后，进程级 `_registry` 比开跑前多出的条目数"（`t2h_reset2` 实测）。

| # | 文件 : 行（改动前） | 夹具 / 站点 | 泄漏 | 泄漏的条目（探针逐字） |
|---|---|---|---|---|
| 1 | `tests/unit/test_search_tools.py:30-40` | fixture `registered`（`search_tools.register_all(dl)`，只还原 `grep`/`edit`） | **+7** | `compress, decompress, diff_files, get_file_info, list_directory, search_files, write_file` |
| 2 | `tests/unit/test_search_tools.py:384-394` | 用例 `test_read_file_registers_read_state`（`file_tools_reg.register_all`，只还原 `read_file`） | （含在上面 7 个里：`write_file/list_directory/get_file_info/search_files`） | 同上 |
| 3 | `tests/unit/test_policy_integration.py:194-204` | fixture `read_tool`（`file_tools_reg.register_all(_DummyDl())`，不还原） | **+8** | `compress, decompress, diff_files, get_file_info, list_directory, read_file, search_files, write_file` |
| 4 | `tests/unit/test_background_tasks_routes.py:138-146` | 用例 `test_端点已在真实入口注册`（函数体内 `import app_server`） | **+91** | 整套内建工具（`apply_patch … write_file`） |
| 5 | `tests/unit/test_legacy_memory_routes.py:78-98` | fixture `real_app`（module 作用域内 `import app_server`） | **+91** | 同上 |
| 6 | `tests/unit/test_graceful_shutdown_persist.py:25-30` | fixture `app_server_mod`（module 作用域内 `import app_server`） | **+91** | 同上 |
| 7 | `tests/unit/test_health_retrieval_endpoint.py:29-34` | fixture `flask_app`（module 作用域内 `import app_server`） | **+91** | 同上 |
| 8 | `tests/unit/test_server_routes_registration_inventory.py:150-154` | fixture `real_url_paths`（module 作用域内 `import app_server`） | **+91** | 同上 |
| 9 | `tests/unit/test_yunshu_mcp_server.py:60-63` + 用例体 `:378/:398/:406/:416/:423` | fixture `handler` + 5 处直接 `srv.YunshuMCPHandler()`（构造时 `_register_exposed_tools()`） | **+10** | `compress, decompress, diff_files, edit, get_file_info, grep, list_directory, read_file, search_files, write_file` |
| 10 | `tests/unit/test_digital_life_comprehensive.py`（用例体约 30 处 `DigitalLife(...)`，`:300/328/345/…/1438`） | 无夹具（对象构造发生在用例体里） | **+91** | 整套内建工具 |

> 表里 1/2 是**同一个文件的两个站点**，所以"文件数"是 **9 个**（列了 10 行）。

**与 TESTINFRA-2 登记的 U2 名单对照**：它登记的是 1（`test_search_tools.py:31`）、3、4、5 四个。
本卡**确认了这 4 个**（泄漏量 `7 / 8 / 91 / 91` 与它逐字一致——它写 `test_search_tools.py` 留 7 个，
我的 hookwrapper 版实测正好 7 个），并**新增 5 个**：6、7、8、9、10。

### 2.1 为什么这几个"多出来"的以前没被发现

* 6/7/8 与 4/5 同形（都是 `import app_server` 的导入副作用），只是**在同一个 pytest 进程里
  `app_server` 只会执行一次** ⇒ 一旦 4 先跑，6/7/8 的增量就被"吃掉"，看起来像没泄漏。
  必须**单文件跑**（或复位探针）才测得出——这正是 `t2h_reset2` 存在的理由。
* 9/10 泄漏的不是"夹具"，而是**用例体**里的对象构造（`YunshuMCPHandler()` / `DigitalLife()`），
  没有 teardown 可言 ⇒ 只看"夹具有没有 finally"是找不到它们的。

---

## ③ 改前红 / 改后绿（同一条命令，原始输出）

### 3.1 九个污染源：逐个两文件复现

命令模板见 §1.5。**改前**与**改后**只差本卡的源码（取证插件完全相同）。

| 污染源 | 泄漏（改前→改后） | 改前 | 改后 |
|---|---|---|---|
| `test_search_tools.py` | +7 → **0** | **2 failed, 54 passed** | **56 passed** |
| `test_policy_integration.py` | +8 → **0** | **2 failed, 61 passed** | **63 passed** |
| `test_digital_life_comprehensive.py` | +91 → **0** | **2 failed, 101 passed, 1 skipped** | **103 passed, 1 skipped** |
| `test_yunshu_mcp_server.py` | +10 → **0** | **2 failed, 51 passed** | **53 passed** |
| `test_background_tasks_routes.py` | +91 → **0** | **2 failed, 31 passed** | **33 passed** |
| `test_graceful_shutdown_persist.py` | +91 → **0** | **2 failed, 24 passed** | **26 passed** |
| `test_health_retrieval_endpoint.py` | +91 → **0** | **2 failed, 27 passed** | **29 passed** |
| `test_legacy_memory_routes.py` | +91 → **0** | **2 failed, 22 passed** | **24 passed** |
| `test_server_routes_registration_inventory.py` | +91 → **0** | **2 failed, 35 passed** | **37 passed** |

**1/3 两行的 `2 failed, 54 passed` 与 `2 failed, 61 passed` 与 TESTINFRA-2 §1.2 记录的逐字一致**
（本卡第一次独立复现出这两个数）。改后的 `passed` 比改前多 2 = 原来红的那 2 条转绿。

原始输出片段（改前，`test_search_tools.py`）：

```text
T2H-NOISO stripped=18 dropped_selfcheck=1
T2H LEAK test_search_tools.py +7 -0 added=['compress','decompress','diff_files','get_file_info',
        'list_directory','search_files','write_file'] removed=[]
FAILED tests/unit/test_tool_count_consistency.py::TestAdvertEqualsDispatched::test_真实注册表上宣告数与下发集长度一致
FAILED tests/unit/test_tool_count_consistency.py::TestAdvertEqualsDispatched::test_宣告数随白名单收窄_不等于注册表全量
======================== 2 failed, 54 passed in 4.94s =========================
```

原始输出片段（改后，同一条命令）：

```text
T2H CLEAN test_search_tools.py n=0
T2H CLEAN test_tool_count_consistency.py n=0
============================= 56 passed in 4.72s ==============================
```

`test_background_tasks_routes.py` 的改前（`assert 26 == 3` 那一族）：

```text
T2H LEAK test_background_tasks_routes.py +91 -0 added=['apply_patch','arch_diagram',…,'write_file']
T2H CLEAN test_tool_count_consistency.py n=91
================== 2 failed, 31 passed, 3 warnings in 35.06s ==================
```

### 3.2 全量清单探针：改前 4 处 LEAK → 改后 27/27 CLEAN

```powershell
# 复位探针：27 个"非 app_server 导入型"候选文件跑一遍（每个模块前复位注册表）
python -m pytest @f1 -q -p no:randomly --timeout=60 -p t2h_reset2
```

| | 改前 | 改后 |
|---|---|---|
| LEAK 行 | **4**（`test_digital_life_comprehensive +91`、`test_policy_integration +8`、`test_search_tools +7`、`test_yunshu_mcp_server +10`） | **0** |
| CLEAN 行 | 23 | **27** |
| 该轮总计数 | `1138 passed, 1 skipped` | `1138 passed, 1 skipped`（**未少一条用例**） |

### 3.3 负对照：不泄漏的文件 → 全绿（证明"红"不是取证插件造的）

这 5 个在第一版探针里被我误报过，hookwrapper 版实测 CLEAN；把它们与受害侧合跑（**同样摘掉隔离**）：

```text
test_injection_end_to_end.py        → 48 passed
test_tool_approval_e2e.py           → 38 passed
test_tool_gate.py                   → 84 passed
test_advert_equals_dispatch_smart.py→ 28 passed
test_subagent_delegate_tool.py      → 78 passed
```

⇒ **"2 failed"只在真有泄漏时出现**，不是插件的产物。

### 3.4 卡片指定的四文件组合（改后实测）

```powershell
python -m pytest tests/unit/test_search_tools.py tests/unit/test_tool_count_consistency.py `
    tests/unit/test_policy_integration.py tests/unit/test_background_tasks_routes.py `
    -q -p no:randomly --timeout=60
```

```text
====================== 111 passed, 3 warnings in 35.12s =======================
```

> ⚠️ 这一条命令**对污染不敏感**（受害侧 autouse 隔离会挡住污染，改前跑它也是绿的）——所以它只能
> 当"没打坏东西"的回归证据，判别力来自 §3.1 的 `noiso` 臂。本报告不把它当"改后变绿"的证据。

### 3.5 34 文件定向回归（唯一一次较大的运行，**不是全量**）

同一批候选文件（含全部 app_server 导入型）合跑，**改前改后各一次**：

| | 结果 |
|---|---|
| 改前 | `1275 passed, 11 skipped`（`-q -p no:randomly --timeout=60`，137–177 s） |
| 改后 | **`1275 passed, 11 skipped`**（144.50 s） |

⇒ 改动没有让任何既有用例变红或变跳（11 skipped 全部是既有的 `--runslow` 车道）。

受害文件单跑：`22 passed`（21 + TESTINFRA-2 加的自证用例）。

---

## ④ 修法与「为什么没有削弱守卫」

### 4.1 修法形状（9 个文件统一，照抄受害侧 `:171-174`）

```python
saved = dict(_tools._registry)          # ① 改动前整表快照
try:
    <原来会登记真实工具的动作>
    yield / return …
finally:
    _tools._registry.clear()            # ② 逐条放回（含 source / schema / handler / source_id）
    _tools._registry.update(saved)
    _tools._registry_version += 1       # ③ 推进版本让 list_tools / get_tool_defs 等缓存失效
```

* 统一**不用** `tools.clear()`（它会连 `_tool_health` 一起清）。
* 7 个站点是**逐站点 `finally`**；`test_yunshu_mcp_server.py` 与
  `test_digital_life_comprehensive.py` 的泄漏点在**用例体**里（对象构造），
  故用**文件内**的 autouse 夹具包（不是 `tests/unit/conftest.py` 的全局 autouse）。
* 受害侧的断言、`tests/unit/conftest.py`、`agent/tools/__init__.py` **一字未动**。

### 4.2 「没削弱」的取证

* **diff 里 0 条断言/skip 改动**（原样输出）：

```powershell
git diff -U0 -- <本卡 9 个文件> | Select-String -Pattern '^[+-]' | Select-String -Pattern 'assert|skip|xfail|pytest.mark'
# → （无输出）
git diff --stat -- <本卡 9 个文件>
# 9 files changed, 208 insertions(+), 30 deletions(-)
```

  即：**一条 `assert` 都没动，没有新增 `skip`/`xfail`/`mark`**；30 行删除全部是旧的
  "只还原一两个名字"的代码与其文档。
* **受害文件没被改**：`git status --porcelain` 里不出现
  `tests/unit/test_tool_count_consistency.py`。
* **负对照**（§3.3）证明"红"不是取证手法造出来的；**改后泄漏 = 0**（§3.1/3.2 探针）。
* **用例数不减**：27 文件复位探针轮 `1138 passed, 1 skipped` 改前改后完全一致；
  34 文件回归轮 `1275 passed, 11 skipped` 改前改后完全一致。

### 4.3 过程中真踩到的一个坑（已修，登记在此）

`test_yunshu_mcp_server.py` 我先写成**逐用例**还原，结果**打红了它自己的**
`TestExposedToolsEnvOverride::test_override_can_narrow_to_one_tool`。根因链（实测）：

1. 该用例要求 `get_file_info` 已是"已注册"工具——`mcp_services/yunshu_mcp_server.py:209-212`
   的 `_register_exposed_tools()` **只在 `"read_file" not in known` 时才**整体登记文件工具五件套；
2. 逐用例还原后残留恰好只有 `read_file`（来自同文件 `:296` 的
   `monkeypatch.setitem(agent_tools._registry, "read_file", …)`）⇒ 五件套不再登记 ⇒
   构造即抛 `ToolConfigError: 含未注册的工具名: ['get_file_info']`；
3. **夹具 teardown 顺序**：autouse 夹具在 fixture 闭包里排最后 ⇒ **最先**被 teardown，
   `monkeypatch` 的 undo 在它之后跑，于是把 `read_file` 又写回注册表。

修法：该文件改用 **`scope="module"`** 的 autouse 夹具（模块内所有函数级夹具收尾**之后**才还原）——
既真正清干净（实测泄漏 0），又不改变本文件内部原有的"先注册后暴露"可见性。
改后：`test_yunshu_mcp_server.py + victim` = **53 passed**（改前 2 failed, 51 passed）。

---

## ⑤ 我【没能确认】的部分（如实登记）

1. **没有跑全量 `tests/unit`**（约束明令）。"全量里那 2 条不会再被这 9 个污染源打红"是
   **9/9 确定性最小复现 + 改后同命令全绿 + 34 文件定向回归一致**支撑的强结论，**不是 v5 全量实测**。
2. **`--runslow` 车道未跑**：`test_skills_classifier.py`（class 级夹具 `import app_server`）与
   `test_tool_callability.py`（module 级夹具 `real_app` 导 `app_server`）的 app_server 导入都在
   **slow 用例**里，默认车道直接 skip ⇒ 默认车道实测 `CLEAN n=0`（不泄漏）。
   打开 `--runslow` 后它们**大概率会**像 4–8 那样泄 91 个，**我没有实测**（代价过大），故**未修**。
3. **`data/audit/knowledge_audit.jsonl` 的写入归属**：见 §⑦——我确实**间接**追加了记录，
   无法逐条判定哪几条是我的、哪几条是并发卡片（同一 pytest 临时目录前缀）。
4. **`audit_chain.db` 附属文件 mtime 变化的原因归属**：`-shm/-lock/-seqjournal` 的 mtime 在我
   的运行窗口内变化（`audit_chain.db` 本身的 size/mtime 未变 ⇒ 没有提交事务）。
   是"打开/检查点"还是别的卡片所致，**未确认**。
5. **受害者侧隔离夹具与被修后的污染源是否"双保险"冗余**未做实验（例如"撤掉受害侧隔离 + 9 个
   泄漏源全修"下全量是否恒绿）——只在 34 文件定向集上验证过。

---

## ⑥ 覆盖面：还有谁"动注册表"但**我没修**，以及为什么

枚举规则同 §1.1（覆盖整个 `tests/` 树，不只 `tests/unit`）。

| 对象 | 实测 | 为什么没修 |
|---|---|---|
| `tests/test_dynamic_tools.py`（4 处 `tools.register`） | **CLEAN n=0**（单跑 `32 passed`） | 不在 `tests/unit`（文件归属外），且它每一处都在 `finally` 里 `unregister`，实测不泄漏 |
| `tests/unit/test_tool_callability.py`（`:497/507/515/523/527/538` 六处 `T.clear()`） | **破坏型**：合跑时 `-81`（把上家登记的内建工具整表清掉） | 不属于"把真实工具**泄漏进**"的那一族（方向相反），且是**用例体**里的清空；本卡未改（见下） |
| `tests/unit/test_fan_out.py:208-212`（autouse `_isolate_registration` 无条件 `unregister("fan_out")`） | 单跑 CLEAN；合跑 `-1`（删掉上家登记的 `fan_out`） | 同上（破坏型）。它的 `register_all` 只登记 `fan_out` 一个，泄漏面小 |
| `tests/unit/test_knowledge_workflow.py`（`finally: unregister_knowledge_tools()`）、`test_process_distill.py`、`test_subagent_delegate_tool.py` | 单跑 CLEAN；合跑分别 `-6 / -2 / -1` | 同上（**破坏型**：它们注销的是上家（`app_server`）登记的同名工具）。**注**：一旦 4–8 修好，上家不再登记它们，这三处的 `unregister` 就退回"只删自己登记的"，净效果归零 |
| `tests/unit/test_skills_classifier.py`、`test_tool_callability.py` 的 app_server 导入 | 默认车道 CLEAN（slow 被 skip） | §⑤.2：`--runslow` 车道未实测，**不写没实测的结论**、也不盲改 |
| 其余 `tests/boundary`、`tests/integration`、`tests/performance` 里的 `.clear()` | 全部是**别的**注册表（workflow 引擎 `registry`、插件 `api._REGISTRY`、各类缓存） | 与 `agent/tools/_registry` 无关（grep 逐个核对过） |

**建议后续卡**（本卡不做，超出归属与预算）：把 `test_tool_callability.py` 的六处 `T.clear()`
与 `test_fan_out.py` 的无条件 `unregister` 一并改成"快照-还原"形状；那属于**破坏型**污染，
与本卡的**泄漏型**是同一类缺陷的两个方向。

---

## ⑦ 数据卫生与残留

`data/agent_lines/_active.json`（本卡只读不改，它是机制腿②的输入）：

| 文件 | 会话开始 | 会话结束 | 判定 |
|---|---|---|---|
| `data/agent_lines/_active.json` | 25 B / 2026-09-19 16:44:58Z | **同左（一字未动）** | ✅ |
| `data/skills_mgmt.json` | 188467 B / 2026-09-25 23:46:05Z | **同左** | ✅ |
| `data/audit/audit_chain.db` | 55177216 B / 2026-09-26 07:22:34Z | **同左** | ✅（无提交事务） |
| `data/audit/daily_roots.jsonl` | 9895 B / 2026-09-26 02:07:00Z | **同左** | ✅ |
| `data/audit/knowledge_audit.jsonl` | 50048 B | 53112 B（+7 条） | ⚠️ **见下** |
| `data/audit/audit_chain.db-shm/-lock/-seqjournal` | — | mtime 变（size 未变/微增） | ⚠️ 未确认归属（§⑤.4） |

**`knowledge_audit.jsonl` 的 +7 条（实测逐条）**：本卡为了"找全污染源"跑了多轮 27/34 文件回归集，
其中包含 `tests/unit/test_knowledge_workflow.py`；它的 `kb_lint` 用例走
`agent.tools.kb_lint → agent/knowledge/audit_entry.py::run_knowledge_audit_entry`，
**该实现无论成败都往 `data/audit/knowledge_audit.jsonl` 追加一条结构化记录**（这是该测试**既有**的行为，
不是本卡引入的）。落在本卡窗口（本地 00:52–01:31）的 7 条：

```text
2026-09-27T00:52:42  pytest-5921      2026-09-27T01:18:16  pytest-6077
2026-09-27T00:56:56  pytest-5943      2026-09-27T01:26:10  pytest-6112
2026-09-27T01:06:44  pytest-5991      2026-09-27T01:28:41  pytest-6115
                                      2026-09-27T01:31:01  pytest-6122
```

⇒ **我确实间接写了这份审计日志**（追加型、不影响 `audit_chain.db` 哈希链）。我**没有**去删/截断它
（那会是第二次未经授权的写，且会破坏并发卡片写入的连续性），只在此处如实登记。

**仓库内残留**：`git status` 里属于本卡的只有上面 9 个 `M`；另有其它并发卡片的改动/新增
（`agent/descriptors/*`、`scripts/*`、`data/eval/*`、`docs/audit_skill_governance/{DYNGATE1,LEDGER2,MINSCORE1}.md` 等），
**均非本卡所为**。本卡未新建任何仓库内文件（除本报告）。

**仓库外**（`%LOCALAPPDATA%\Temp\testhyg2\`）：`t2h_probe.py`、`t2h_reset2.py`、`t2h_noiso.py`、
`t2h_state2.py` 与各轮原始输出（`reset2.txt`、`c2.log`、`d1.txt`、`d2.log`、`d3.log`、`e1.txt`、`e.log` 等）。

---

## ⑧ 回滚

```powershell
# 本卡只改了 9 个测试文件（+ 新增本报告）；仓库里这 9 个文件的 diff 只含本卡改动（已核对）
git diff --stat -- tests/unit/test_search_tools.py tests/unit/test_policy_integration.py `
    tests/unit/test_background_tasks_routes.py tests/unit/test_legacy_memory_routes.py `
    tests/unit/test_graceful_shutdown_persist.py tests/unit/test_health_retrieval_endpoint.py `
    tests/unit/test_server_routes_registration_inventory.py tests/unit/test_yunshu_mcp_server.py `
    tests/unit/test_digital_life_comprehensive.py

# 回滚方式（**需要时由主审计执行**；本卡全程未执行任何 git 写操作）：
git checkout -- <上面 9 个文件>
Remove-Item docs/audit_skill_governance/TESTHYG2.md
```
