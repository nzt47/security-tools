# T-ISO 交付报告：收敛测试的模块级状态复位（治本）

> 卡号：T-ISO｜实施者：修复实施子代理｜完成时间：2026-09-25 20:2x｜环境：Python 3.12.0 / pytest 9.1.1（pytest-randomly 4.1.0 **默认启用**，故每次运行顺序本就随机）
> 本报告所有「已验证」均附原始命令与原始输出；未实测的一律标注「未验证 + 原因」。

## 0. 结论摘要

| 项 | 结论 | 证据位置 |
|---|---|---|
| 全部跨用例状态源 | **找到 6 个真源 + 5 个候选被实证排除** | §2 |
| ① `agent.tool_gate` 模块级缓存 | 真源（`_EXEMPT_WATCH` / `_WARNED` / `_TOOL_META_CACHE` / `_DERIVED_CACHE` / `_STRICT_GATEWAY`）→ **已收敛到 autouse fixture** | §1.1、§2.1–§2.3 |
| ② 审计链「名单上一次记录的生效值」 | 真源，且是 `test_新增豁免写入审计链` 的**唯一**根因；**落盘、追加写、无删除 API ⇒ fixture 复位不了**，改为**用例自建基线** | §2.4、§3.1 |
| ③ `test_L3_不可被豁免` 的对照断言 | **断言本身写错**（把不在名单里的工具当成已豁免）；**单跑同样失败**，从来不是顺序问题 | §3.2 |
| 主审计「第一版修法未解决」的解释 | 复现并解释清楚：`_EXEMPT_WATCH` 复位**在逻辑上无害但不对症**；实测「只复位它」仍然 2 failed | §3.3 |
| 验收：单文件 | **26 passed** | §4.1 |
| 验收：倒序（S1 的五条） | **5 passed** | §4.2 |
| 验收：与相邻治理文件合跑 | 我的 26 条全绿；**1 条越界失败**（`test_tool_gate.py` 一条**既有**用例，被 A2 的 S2 改动打断，**不在本卡文件范围，未修**） | §4.3、§6 |
| 顺序无关性 | 3 次随机种子 + 文件序 + 倒序 全绿 | §4.4 |
| 生产代码 | **零改动**（`agent/` 下未动任何文件） | §5.2 |

## 1. 改了什么（本卡允许的三个文件）

| 文件 | 行数 | sha256 前 16 | 改动性质 |
|---|---|---|---|
| `tests/unit/conftest.py` | **881**（原 804，+77） | `93bd6abeb2ef6c10` | 新增 1 个 autouse fixture + 说明注释（未改动任何既有 fixture） |
| `tests/unit/test_confirm_gate_no_bypass.py` | **409**（原 387，+22） | `f64eb00b2a2638c6` | 删 5 处逐用例复位行；1 条用例改为**自建链基线**；1 条用例的**错误对照断言**被修正 |
| `docs/audit_skill_governance/T-ISO.md` | 本文件 | — | 报告 |

### 1.1 `tests/unit/conftest.py`：新增 `_tiso_reset_tool_gate_module_state`（autouse, function 级）

做的事（每个用例**前后各一次**，幂等）：

1. `agent.tool_gate._EXEMPT_WATCH["raw"] = _UNSET`（豁免名单的进程内对拍哨兵）；
2. `agent.tool_gate._reset_cache()`（= 清 `_DERIVED_CACHE` + `_WARNED` + 置 `_TOOL_META_CACHE = None`）；
3. `agent.tool_gate._STRICT_GATEWAY = None`（严格模式网关惰性单例；`_reset_cache()` **不**负责它）。

三条硬要求都落实了：

* **幂等**：用 `sys.modules.get("agent.tool_gate")` **查找而不 import**，模块未导入 / 导入被故意置 `None`（`_break_tool_gate`）时直接 no-op；三处复位各自 `try/except` 吞异常。
  为什么必须「不 import」：`agent.tool_gate` 在导入期会按当时的环境变量绑定审计路径，测试进程里主动 import 有副作用；且若它在 `sys.modules` 里是 `None`（导入被 halt），`import` 会抛错——而本 fixture 的 **teardown 可能早于 monkeypatch 的还原**，正好撞上这个状态。
* **不改既有 fixture 语义**：只碰 `agent.tool_gate` 的进程内缓存；不碰环境变量、不碰 `tests/conftest.py` 的会话基线（`CP_TOOL_GATE_APPROVAL_ENFORCE=0` 等）。
* **注释写明 Why + 引用失败用例名**：见该文件 :806-852。

## 2. 全部跨用例状态源（逐个给证据）

方法：写**仓库外**探针（`C:\Users\Administrator\tiso_probe\probe_plugin.py`，pytest 插件，用 `-p probe_plugin` 注入），在**同一进程**内逐用例 dump 候选状态，「只在变化时打印」。被观测的候选共 15 项。

```powershell
$env:PYTHONPATH="C:\Users\Administrator\tiso_probe"
python -m pytest tests/unit/test_confirm_gate_no_bypass.py tests/unit/test_tool_gate.py tests/unit/test_tool_approval.py -q -s -p no:randomly -p probe_plugin --tb=no
```

### 2.1 【纳入复位】`G._EXEMPT_WATCH`（模块级 dict，`agent/tool_gate.py:1107`）

原始 dump（**修复前**，文件顺序；`BEFORE` 行 = 该用例开始时）——同一份状态**跨用例活着**：

```
[TISO] BEFORE TestS1ExemptChangeIsAudited::test_新增豁免写入审计链    | env=None watch='delegate' chain_baseline='delegate' ...
[TISO] BEFORE TestS1ExemptChangeIsAudited::test_移除豁免也写入审计链   | env=None watch='delegate' chain_baseline='write_file,compress' ...
[TISO] BEFORE TestS1ExemptChangeIsAudited::test_L3_不可被豁免       | env=None watch='delegate' chain_baseline='write_file' ...
[TISO] BEFORE TestS3SkillExecGoesThroughGate::test_执行前调用闸门…   | env=None watch='delegate' ...
[TISO] AFTER  TestS3SkillExecGoesThroughGate::test_执行前调用闸门…   | env=None watch=''        chain_baseline='' ...
```

⇒ `watch` 依次留下 `'delegate'` / `''` / `'write_file,compress'` / `'shell_execute'` 并进入下一个用例。`monkeypatch` 只还原环境变量，**不还原它**（原作里 5 条同族用例中 3 条自己写了 `monkeypatch.setitem(G._EXEMPT_WATCH, "raw", G._UNSET)`、2 条漏了）。**纳入中央复位。**

### 2.2 【纳入复位】`G._WARNED`(:268) / `G._DERIVED_CACHE`(:266) / `G._TOOL_META_CACHE`(:255)

```
[TISO-CNG] …::test_L1_工具仍可被豁免            G.warned = 1
[TISO-CNG] …::test_未在名单里的工具照旧要确认     G.warned = 2
[TISO-CNG] …::test_L3_不可被豁免               G.warned = 3
[TISO-CNG] …::TestDeclaredCapabilityGrading::…  G.warned = 4
[TISO-CNG] …::test_总开关为0时_L2_照旧放行       G.tool_meta_cache = dict(91)  G.derived_cache = 2
```

`_WARNED` 0→1→2→3→4 单调累积（`_warn_once` 的去重表 ⇒ 后一个用例看不到本应出现的告警）；`_TOOL_META_CACHE` 在 `None` 与 `dict(91)` 之间反复。三者都在 `_reset_cache()`（:2178）的职责范围内，且**既有三个测试文件各自写过一遍**（`test_tool_gate.py:70`、`test_tool_gate_strict.py:77`、`test_scheduled_session_source.py:76`）——正是卡里说的「机制存在，但没人保证它被用上」。**纳入中央复位（走 `_reset_cache()`，不自己重写）。**

### 2.3 【纳入复位】`G._STRICT_GATEWAY`(:271)

`_reset_cache()` **不**清它（读码 :2178-2189 确认），而两个既有文件各自 `monkeypatch.setattr(G, "_STRICT_GATEWAY", None)` 复位（`test_tool_gate_strict.py:76/79`、`test_scheduled_session_source.py:75/79`）⇒ 它属于同一族「进程内惰性单例」。
**本轮探针里它全程恒为 `None`**（3 文件运行中从未被构造，`G.strict_gateway = None` 只打印过一次）：也就是说**本卡的两条失败与它无关**；纳入复位是「同族状态一次收口」，不是本轮失败的原因。

### 2.4 【真源，但**不能**由 fixture 复位】审计链上的「名单上一次被记录过的生效值」

`_record_exempt_change_if_needed()` 的 `old_value` 来自 `_chain_baseline_exempt()` —— 它读的是**审计链**上最近一条 `tool.confirm.exempt_changed` 的 `new_value`。链在 `tests/conftest.py:260` 只被隔离到**会话级**临时目录（`AUDIT_DB_PATH`），**不逐用例清**；链本身是追加写、**无删除 API**（删记录会打断 hash 链）。

```
[TISO-CNG] …test_移除豁免也写入审计链   G.chain_baseline = 'write_file,compress'/True   audit.records_exempt = 2
[TISO-CNG] …test_同值重复观测不重复记账  G.chain_baseline = 'write_file'/True             audit.records_exempt = 3
[TISO-CNG] …test_L3_不可被豁免          G.chain_baseline = 'shell_execute'/True         audit.records_exempt = 4
[TISO-CNG] …test_执行前调用闸门…         G.chain_baseline = ''/True                      audit.records_exempt = 5
```

⇒ 这是**落盘状态**，fixture 复位它是错的（会破坏 hash 链）也不可行。处置：**由用例自己先建立基线**（见 §3.1 的修法）。

### 2.5 【实证排除】审批收件箱 `approval_records.jsonl`

会话级隔离、**跨用例持久**（探针：`approval.records` 1→3→4→5→6→8 单调增长）。但它只承载「挂单」，本文件没有任何断言读它；两条失败与它无关（§3 的根因均已定位到别处）。**不纳入复位**（改它会动到 `tests/conftest.py` 的隔离契约，超出本卡范围）。

### 2.6 【实证排除】已用台账 `tool_approval_uses.jsonl`

探针全程 3 文件运行：`approval.uses = None`（**文件从未被创建**）⇒ 本轮无任何「审批被消费」残留。**排除。**

### 2.7 【实证排除】`agent.settings` 覆盖层单例（卡里点名的候选）

`tests/conftest.py:289-297` 已在**会话级**把 `CP_UI_SETTINGS_PATH` 指向临时目录并复位 store；探针实测整个 3 文件运行里：

```
[TISO-CNG] …::test_导入失败时_L3_工具被拒   resolver_effective = ''@default   ui_settings.exempt_key = no-file
```

（只打印过一次 = **全程未变**；覆盖层文件连文件都不存在 ⇒ `resolve()` 永远回落到默认空值。）**排除**（既有会话级隔离已覆盖它，另加逐用例复位会改变 `tests/conftest.py` 的既有语义）。

### 2.8 【实证排除】`agent.tools._registry` 与 `agent.lines` 元数据缓存

```
[TISO-CNG] …::test_导入失败时_L3_工具被拒   tools.registry = id=2456000679104 len=0   lines.meta_cache = 0
```

`tools.registry` 的 id/len **全程只打印一次**（= 无变化）：本文件用 `monkeypatch.setattr(tools, "_registry", reg)`，由 monkeypatch 自动还原。
`agent.lines.models._META_CACHE` 只在首次 0→1（**签名键控**：`(文件名, mtime_ns)` 变了才重读，见 `agent/lines/models.py:540-561`）⇒ 内容变即失效，不是泄漏源。
**两者都排除**（也不该为了「顺手」去复位 `_registry`：那会把真实注册表清空，反而制造新的假失败）。

### 2.9 状态源清单（总表）

| # | 状态 | 位置 | 实证 | 处置 |
|---|---|---|---|---|
| 1 | `_EXEMPT_WATCH["raw"]` | `tool_gate.py:1107` | 跨用例留下 'delegate' / '' / … | **fixture 复位** |
| 2 | `_WARNED` | `:268` | 0→1→2→3→4 | **fixture 复位**（经 `_reset_cache()`） |
| 3 | `_TOOL_META_CACHE` | `:255` | None ↔ dict(91) | **fixture 复位**（经 `_reset_cache()`） |
| 4 | `_DERIVED_CACHE` | `:266` | 0↔1↔2 | **fixture 复位**（经 `_reset_cache()`） |
| 5 | `_STRICT_GATEWAY` | `:271` | 本轮恒 None（未被构造）；但两处既有测试各自复位它 | **fixture 复位**（同族收口） |
| 6 | 审计链「链上已知生效值」 | 落盘 `audit_chain.db` | None→''→'delegate'→… | **不可复位** ⇒ 用例自建基线 |
| 7 | `approval_records.jsonl` | 会话级 tmp | 1→3→4→5→6→8 | 排除（无断言依赖；改它超范围） |
| 8 | `tool_approval_uses.jsonl` | 会话级 tmp | 全程 None（未创建） | 排除 |
| 9 | 覆盖层 store / `resolve()` | `CP_UI_SETTINGS_PATH` | 恒 `''@default`、`ui_settings.exempt_key=no-file` | 排除（既有隔离已覆盖） |
| 10 | `agent.tools._registry` | `agent/tools/__init__.py` | id/len 全程不变 | 排除（monkeypatch 还原；强行复位有害） |
| 11 | `agent.lines.models._META_CACHE` | `models.py:540` | 0→1（签名键控） | 排除（自动失效） |

## 3. 两条失败用例的真实根因

### 3.1 `test_新增豁免写入审计链`：**链上基线泄漏**（不是 `_EXEMPT_WATCH`）

修复前原始输出（文件顺序）：

```
tests\unit\test_confirm_gate_no_bypass.py:215: in test_新增豁免写入审计链
    assert payload.get("removed") == [], payload
E   AssertionError: {'actor': '', 'actor_from': 'unresolved', 'actor_source': 'default_system', 'added': ['compress', 'write_file'], ...}
E   assert ['delegate'] == []
```

随机顺序下同一条用例的实得值是 `assert ['shell_execute'] == []`（★ 同一断言、不同的多余项 ⇒ **典型的状态泄漏签名**，而非逻辑错误）。

**真实机制**（读码 + dump 双向印证）：`added/removed` 是拿「当前生效值」与**链上已知值**做**集合差**得来的（`tool_gate.py:1268-1272`），而链上已知值来自 `_chain_baseline_exempt()`；文件顺序下前序用例 `TestNormalPathNotOverBlocked::test_L1_工具仍可被豁免` 已把 `delegate` 记进链、随机顺序下 `test_L3_不可被豁免` 已把 `shell_execute` 记进链 ⇒ `removed` 必然非空。

**修法（不放宽断言）**：用例先**自己建立基线**——把名单切成空并消费一次，让「链上已知值」归零，再做原本的变更并断言：

```python
monkeypatch.setenv(G.CONFIRM_LEVEL_EXEMPT_ENV, "")
G._is_confirm_level_exempt(L2_TOOL)                     # 建立基线（= 空名单）
baseline, readable = G._chain_baseline_exempt()
assert readable and baseline in (None, ""), ()          # 前置条件：基线确实是空的
last_before = _last_seq(G.EXEMPT_CHANGE_ACTION)
new_value = L2_TOOL + "," + "compress"
...
assert payload.get("old_value") == "", payload          # ← 新增：把基线钉死
assert payload.get("new_value") == new_value, payload
assert payload.get("added") == ["compress", L2_TOOL], payload
assert payload.get("removed") == [], payload            # ← 原断言**原样保留**
```

⇒ 原断言（`added` 两个名字、`removed == []`、`levels` 两项）**一条都没放宽**，另**新增**了 `old_value` 断言（更强）。

### 3.2 `test_L3_不可被豁免`：**断言本身写错**（与执行顺序无关；任务卡的前提有误）

任务卡写「该用例单独跑却通过」。**实测不成立**：

```powershell
python -m pytest "tests/unit/test_confirm_gate_no_bypass.py::TestS1ExemptChangeIsAudited::test_L3_不可被豁免" -q -p no:randomly
# => 1 failed in 2.74s      （失败断言就是那条对照断言，实得 APPROVAL_REQUIRED）
```

**论证（为什么原断言必错）**：该用例自己把名单设成 `L3_TOOL` 一个名字（`shell_execute`），而豁免名单的语义是「**名单里**的工具免摘要确认」（`_exempt_tools()` 只读 `CP_TOOL_CONFIRM_LEVEL_EXEMPT` 的 env 值，`tool_gate.py:1305-1323`）——`delegate` 不在名单里 ⇒ `_confirm_level_outcome(delegate)` **理应**返回 `APPROVAL_REQUIRED`（这正是生产正确行为：L1 只是「摘要确认」，不是免确认）。原注释写「对照：同一次运行里 L1/L2 的豁免照旧生效」，但**那次运行里 L1 根本没有被豁免** ⇒ 断言与它自己设置的输入自相矛盾。

**修法（把对照修成正确形态，而不是删掉它）**：名单里**同时**写 L3 与 L1 —— L3 被忽略、L1 照旧豁免，这才恰好证明「L3 规则生效」而不是「整层被关掉」：

```python
monkeypatch.setenv(G.CONFIRM_LEVEL_EXEMPT_ENV, L3_TOOL + "," + L1_TOOL)
out = G._confirm_level_outcome(L3_TOOL, {"command": "echo hi"})
assert out is not None and out.get("blocked") is True, out              # 原断言不变
assert "confirm_level=L3" in str(out.get("reason") or ""), out          # 原断言不变
assert G._confirm_level_outcome(L1_TOOL, dict(DELEGATE_ARGS)) is None   # 对照：修好后真的成立
```

（顺带修掉误因：该用例里那条 `monkeypatch.setitem(G._EXEMPT_WATCH, ...)` 与失败**无关**——见 §3.3。）

### 3.3 主审计「第一版修法未解决」的解释（含一处机制更正）

实测复现：给逐用例补上 `_EXEMPT_WATCH` 复位（= 探针里的 `--tiso-reset=watch`，甚至 `--tiso-reset=all`）：

```
===== --tiso-reset=watch =====   FAILED …test_新增豁免写入审计链 / FAILED …test_L3_不可被豁免   2 failed, 24 passed
===== --tiso-reset=all   =====   FAILED …test_新增豁免写入审计链 / FAILED …test_L3_不可被豁免   2 failed, 24 passed
```

原因：
* `test_新增豁免写入审计链` 的基线来自**链**（§3.1），复位 `_EXEMPT_WATCH` 改变不了链；
* `test_L3_不可被豁免` 的失败是**断言写错**（§3.2），与任何状态都无关。

**机制更正**：原注释称「若不复位，本用例把 raw 留在 `L3_TOOL`，下一个用例就会因『raw 与上一次相同』而跳过记账」。这条**不成立**：下一个用例把 env 换成 `'write_file,compress'`，与留在缓存里的 `'shell_execute'` **不同** ⇒ `_record_exempt_change_if_needed` 的早退分支（`raw == env_value`）不会被命中。`_EXEMPT_WATCH` 的复位**在逻辑上仍然是对的**（模块级状态确实跨用例活着，§2.1），但它**不是**这两条失败的成因；本卡按「治本」把它与同族缓存一起收进 fixture。

## 4. 验收命令与原始输出

### 4.1 单文件（卡里第 1 条，必须 26 passed）

```powershell
PS C:\Users\Administrator\agent> python -m pytest tests/unit/test_confirm_gate_no_bypass.py -q
collected 26 items
tests\unit\test_confirm_gate_no_bypass.py ..........................     [100%]
================================== 所有测试通过！✓ ===================================
通过: 26  失败: 0  跳过: 0
============================= 26 passed in 6.49s ==============================
```

（修复前同一命令：`2 failed, 24 passed in 3.10s`。）

### 4.2 顺序无关性#1：`TestS1ExemptChangeIsAudited` 五条**倒序**单独跑

```powershell
PS> python -m pytest "…::TestS1ExemptChangeIsAudited::test_L3_豁免不落_exempted_决策" "…::TestS1ExemptChangeIsAudited::test_L3_不可被豁免" "…::TestS1ExemptChangeIsAudited::test_同值重复观测不重复记账" "…::TestS1ExemptChangeIsAudited::test_移除豁免也写入审计链" "…::TestS1ExemptChangeIsAudited::test_新增豁免写入审计链" -q -p no:randomly
通过: 5  失败: 0  跳过: 0
============================= 5 passed in 2.23s ==============================
```

### 4.3 与相邻治理文件合跑（卡里第 3 条）

```powershell
PS> python -m pytest tests/unit/test_confirm_gate_no_bypass.py tests/unit/test_tool_gate.py tests/unit/test_tool_approval.py -q
通过: 135  失败: 1  跳过: 0
FAILED tests/unit/test_tool_gate.py::TestToolsCallIsEnforcementPoint::test_闸门模块导入失败时_call_照常执行
================= 1 failed, 135 passed, 3 warnings in 15.02s ==================
```

同一条命令、显式 deselect 那**一条越界失败**（见 §6）后：

```powershell
PS> … --deselect "tests/unit/test_tool_gate.py::TestToolsCallIsEnforcementPoint::test_闸门模块导入失败时_call_照常执行"
通过: 135  失败: 0  跳过: 0
=============== 135 passed, 1 deselected, 3 warnings in 14.83s ================
```

⇒ **本卡文件在这个合跑里 0 失败**；唯一失败是一条**既有**用例、根因是 A2 的 S2 改动，且它**单跑同样失败**（§6）。

### 4.4 顺序无关性#2/#3：pytest-randomly 两遍（环境自带，默认启用）

```powershell
PS> python -m pytest tests/unit/test_confirm_gate_no_bypass.py -q --randomly-seed=1                   => 26 passed in 2.44s
PS> python -m pytest tests/unit/test_confirm_gate_no_bypass.py -q --randomly-seed=999983              => 26 passed in 2.47s
PS> python -m pytest tests/unit/test_confirm_gate_no_bypass.py -q                                    => 26 passed in 2.40s（每次自带新种子）
PS> python -m pytest tests/unit/test_confirm_gate_no_bypass.py -q -p no:randomly                      => 26 passed in 2.39s（文件定义序）
PS> python -m pytest tests/unit/test_confirm_gate_no_bypass.py tests/unit/test_tool_approval.py -q    => 73 passed in 12.62s
```

### 4.5 「复位真的发生了吗」——复位后状态的逐用例实测（新增机制的自证）

用探针显式依赖 conftest 的 fixture，在**每个用例开始时**读状态：

```powershell
$env:PYTHONPATH="C:\Users\Administrator\tiso_probe"
python -m pytest tests/unit/test_confirm_gate_no_bypass.py -q -s -p no:randomly -p probe_plugin --tiso-reset=after
```

```
[TISO-AFTER-RESET] TestS1ExemptChangeIsAudited::test_新增豁免写入审计链      | watch=<_UNSET> warned=0 meta=None gateway=None
[TISO-AFTER-RESET] TestS1ExemptChangeIsAudited::test_移除豁免也写入审计链     | watch=<_UNSET> warned=0 meta=None gateway=None
[TISO-AFTER-RESET] TestS1ExemptChangeIsAudited::test_同值重复观测不重复记账    | watch=<_UNSET> warned=0 meta=None gateway=None
[TISO-AFTER-RESET] TestS1ExemptChangeIsAudited::test_L3_不可被豁免           | watch=<_UNSET> warned=0 meta=None gateway=None
[TISO-AFTER-RESET] TestS1ExemptChangeIsAudited::test_L3_豁免不落_exempted_决策 | watch=<_UNSET> warned=0 meta=None gateway=None
…（26 条全部同形）…
============================= 26 passed in 3.21s ==============================
```

（对比 §2.1 修复前的同样 dump：`watch='delegate'` / `warned=1..4`。）

### 4.6 附加安全检查：新 autouse fixture 对**其它**依赖同族状态的文件的回归

本 fixture 定义在 `tests/unit/conftest.py` ⇒ 它对**整个 unit 套件**生效。故额外跑了四个**最依赖这些模块级状态**的既有文件（它们各自都写过复位代码，是「新 fixture 会不会与既有复位打架」的最敏感样本）：

```powershell
PS> python -m pytest tests/unit/test_tool_gate_strict.py tests/unit/test_scheduled_session_source.py tests/unit/test_confirm_level.py tests/unit/test_tool_gate_fallback.py -q -p no:randomly
通过: 245  失败: 0  跳过: 0
============================= 245 passed in 6.91s =============================
```

（这不是全量回归 —— 全量被铁律 #3 禁止；它只覆盖「会被本 fixture 直接影响」的四个文件。）

### 4.7 复位代价（同一进程内实测）

```
META_COUNT 91
RELOAD_MS [0.34, 0.28, 0.27, 0.27, 0.27]     # _reset_cache() 之后重建元数据缓存的一次性代价
RESET_CACHE_200_MS 0.1                        # 复位本身 200 次 = 0.1 ms
```

⇒ 每个用例多付 ≤0.35 ms（且只在真的用到闸门元数据时）。

## 5. 改动细节与自我约束

### 5.1 `test_confirm_gate_no_bypass.py` 逐条

| 位置 | 改动 |
|---|---|
| `test_新增豁免写入审计链` | 自建空名单基线 + 新增 `old_value` 断言；其余断言原样 |
| `test_移除豁免也写入审计链` | **仅删**除 1 行逐用例复位（新 fixture 覆盖） |
| `test_同值重复观测不重复记账` | **仅删**除 1 行逐用例复位 |
| `test_L3_不可被豁免` | 删除 1 行逐用例复位 + 那条机制不成立的旧注释；名单改为 L3+L1；对照断言修正（§3.2） |
| `test_L3_豁免不落_exempted_决策` | **仅删**除 1 行逐用例复位 |

共删除 5 行逐用例复位（全部被新 fixture 覆盖），未改动任何 fixture（`enforce_on` / `stub_registry` 语义原样）。

### 5.2 「不越界」的证据

```
$ git status --porcelain agent tests docs/audit_skill_governance
 M agent/audit/chain.py  M agent/audit/facade.py  M agent/digital_life_persona.py
 M agent/model_router/adapters.py  M agent/rate_limiter.py  M agent/server_port_guard.py
 M agent/skills_mgmt/enhancer.py  M agent/skills_mgmt/executor.py  M agent/skills_mgmt/file_store.py
 M agent/skills_mgmt/index_cache.py  M agent/skills_mgmt/loader.py  M agent/skills_mgmt/registry.py
 M agent/skills_mgmt/service.py  M agent/skills_mgmt/vector_adapter.py
 M agent/tool_gate.py                       ← 别的卡（如 A2）改的；**本卡未触碰**
 M agent/tools/__init__.py                  ← A2 的改动；**本卡未触碰**
 M agent/tools_prompt_guard.py              ← 别的卡；本卡未触碰
 M tests/unit/conftest.py                   ← 本卡（唯一改动的既有测试文件）
 M tests/unit/test_tools_prompt_alignment.py← 别的卡；本卡未触碰
?? tests/unit/test_confirm_gate_no_bypass.py ← A2 建的文件，本卡按卡修改
```

**本卡实际写入的文件只有 3 个**：`tests/unit/conftest.py`、`tests/unit/test_confirm_gate_no_bypass.py`、`docs/audit_skill_governance/T-ISO.md`（外加仓库外的探针，见 §8）。
上面 `agent/` 下的一长串 M 是**其它卡在本轮并行改动**的既有工作区状态（`git status` 是工作区全量视图，不代表本卡所为；`agent/tool_gate.py` 我只读不写）。
未 commit / push / checkout / stash / 建分支；未跑全量 pytest。

### 5.3 关于并发写者

`tests/unit/test_tool_exemptions.py` 读到**同一函数被定义两次**（:25-30，前一个只有 docstring）——看起来正被别的卡并发编辑，故本卡**未运行也未触碰**它（涉及「覆盖层单例」的候选因此改用**探针实测**排除，见 §2.7，而不是靠跑该文件）。

## 6. 越界发现（**未修，等人裁决**）：A2 的 S2 改动打断了 `test_tool_gate.py` 的一条既有用例

**现象**：`tests/unit/test_tool_gate.py::TestToolsCallIsEnforcementPoint::test_闸门模块导入失败时_call_照常执行` 失败（`assert False is True`）。

**它不是本卡造成的**，三条独立证据：

1. **单跑即失败**（无任何跨用例状态可依赖）：

```powershell
PS> python -m pytest "tests/unit/test_tool_gate.py::TestToolsCallIsEnforcementPoint::test_闸门模块导入失败时_call_照常执行" -q -p no:randomly
FAILED tests/unit/test_tool_gate.py::TestToolsCallIsEnforcementPoint::test_闸门模块导入失败时_call_照常执行
============================== 1 failed in 2.12s ==============================
```

2. **脱离 pytest 也复现**（只有「注册表 + 会话基线 + sys.modules」三个输入，本卡 fixture 根本不参与）：

```python
import os, sys
os.environ['CP_TOOL_GATE_APPROVAL_ENFORCE'] = '0'   # tests/conftest.py 的会话基线
from agent import tools as registry
registry.register('probe_gate_tool', 'T-ISO 探针', handler=lambda **kw: {'ok': True})
sys.modules['agent.tool_gate'] = None
print(registry.call('probe_gate_tool', a=1))
# DECISION: {'ok': False, 'blocked': True, 'error_code': 'PERMISSION_DENIED',
#            'error_code_detail': 'tool_gate_import_failed', 'confirm_level': 'L3', 'degraded': True}
```

3. **根因在 A2 未提交的生产改动里**：`agent/tools/__init__.py` 的新增函数（`git diff` 确认是**新增**）：

```
+GATE_UNAVAILABLE_EVENT = "tool_gate_import_failed"
+def _confirm_level_without_gate(name: str) -> str:      # 判不出级别 ⇒ L3
+def _gate_unavailable_outcome(...)                      # L2/L3/判不出 ⇒ 拒绝
```

而 `agent/tools/__init__.py:390-412` 对**没有 YAML 条目**的工具返回 `L3`（探针工具 `probe_gate_tool` 正是这一类）⇒ 降级路径**必然拒绝**它。该用例（`tests/unit/test_tool_gate.py:659-668`，**文件未被修改**、最后提交于 `61f53a96 2026-09-20 TASK-06`）断言的却是**改动前**的契约（「闸门模块导入失败时 call 照常执行」）——它与 A2 自己的新用例 `TestS2GateImportFailureIsFailClosed::test_导入失败时未登记工具按最严拒绝` **直接冲突**（同一输入，两个相反期望）。

**为什么本卡不修**：`tests/unit/test_tool_gate.py` **不在本卡允许修改的文件清单里**（清单只有 `tests/unit/conftest.py`、`tests/unit/test_confirm_gate_no_bypass.py`、本报告），且这是「S2 契约要有意收紧到什么程度」的**契约决策**，属 A2 的范围。

**建议处置（给主审计 / A2）**：二选一——
* 若确认 S2 的新契约（判不出级别 = L3 拒绝）是最终契约 ⇒ 该老用例应改为用**真实 L0 工具**（如 `read_file`）验证「降级时 L0 照常执行」的回归意图，另加一条「已注册但无 YAML ⇒ L3 拒绝」；
* 若认为「注册表里存在但无 YAML 的探针工具」应放行 ⇒ 那是**生产代码**要改（`_confirm_level_without_gate` 的口径），本卡不碰。

## 7. 未验证项（如实声明）

| 项 | 状态 | 原因 |
|---|---|---|
| 全量 pytest 回归 | **未验证** | 任务铁律 #3 明确禁止（只跑本卡 targeted 测试） |
| 与 `tests/unit/test_tool_exemptions.py` 合跑 | **未验证** | 该文件疑似被并发写者编辑中（同一函数定义两次），避免读到中间态；其涉及的「覆盖层单例」候选已用探针实测排除（§2.7） |
| `pytest-xdist` 并行（`-n`）下的表现 | **未验证** | 卡里未要求，且并行下「同进程状态」的假设不同，需另开卡 |
| 第 5 项状态源 `_STRICT_GATEWAY` 的**必要性** | **未验证为「必需」** | 探针实测本轮它恒为 `None`（未被构造）⇒ 无法证明「不复位它会失败」；纳入的理由是**同族收口 + 两个既有文件各自复位过它**（§2.3），不是本轮失败的成因 |
| 审计链「链上基线」的彻底隔离 | **未实现（有意）** | 链是追加写、无删除 API，删记录会打断 hash 链；正确处置是「用例自建基线」（已做）。若将来要「每用例一条干净链」，需要 `agent.audit` 侧提供换链能力（跨卡改动） |
| `test_tool_gate.py` 那条越界失败 | **未修（越界）** | 见 §6，等主审计 / A2 裁决 |

## 8. 复现材料（仓库外，不入库）

* 探针插件：`C:\Users\Administrator\tiso_probe\probe_plugin.py`（约 150 行；用法见文件头 docstring）
* 三文件运行的完整 dump：`C:\Users\Administrator\tiso_probe\out_3files.txt`
* 用法：

```powershell
$env:PYTHONPATH="C:\Users\Administrator\tiso_probe"
python -m pytest tests/unit/test_confirm_gate_no_bypass.py -q -s -p no:randomly -p probe_plugin --tiso-reset=none   # 变化即打印
python -m pytest tests/unit/test_confirm_gate_no_bypass.py -q -s -p no:randomly -p probe_plugin --tiso-reset=after  # 复位后状态
```
