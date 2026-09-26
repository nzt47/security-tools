# DYNGATE1 — 处置 detect_dynamic_loads.py 在 HEAD 上既有的 2 处 HIGH

- 分支: audit/skill-governance-v1.0, HEAD = 70aca896b93fc086bb74e4d56e29d8c411582501
- 环境: Windows, 系统解释器 python 3.12.0 (pytest 9.1.1), 未使用仓库里的空壳 venv/
- 本卡未执行任何改变 git 状态的命令; 只用了 git status / ls-files / check-ignore / cat-file / archive(均只读)
- 本卡只改了: scripts/detect_dynamic_loads.py、新增 tests/unit/test_dynamic_loads_high_exemption.py、本文件

## 0. 结论速览

| 项目 | 结论 |
|---|---|
| 2 处 HIGH 的性质 | **不是"路径可控导致任意文件加载"的真漏洞**; 是**设计内的有界加载被规则真阳性命中**(规则要求路径实参为常量, 此处是变量) |
| 退出码不一致 | **我复现不出**。两种模式共用同一行 `return 1 if report.high_risk else 0`(修好后 692 行), 实测改前改后、干净树/工作树/子根三种场景下**始终相等** |
| 处置 | 选 **(b)** 窄口径、带理由、可测试的审计豁免(键 = 文件 + 所在函数 + 动态加载函数名 + 命中配额), 附带"陈旧豁免"告警 |
| 干净检出等价测量 | 改前 HIGH=2 / MEDIUM=164 / LOW=33, 文本与 --json 退出码均为 **1**; 改后 HIGH=0 / MEDIUM=166 / LOW=33, 退出码均为 **0** |
| 守护测试 | 同一条测试文件: 对 HEAD 版扫描器 **8 failed / 6 passed**; 对修好的扫描器 **14 passed** |

## 1. 性质判定

### 1.1 调用链与路径来源(实测证据)

被点名的两行(干净检出实测):

`@
HIGH agent\tools\persistence.py 380 importlib.util.spec_from_file_location
HIGH agent\tools\persistence.py 383 importlib.util.module_from_spec
`@

代码(agent/tools/persistence.py:377-385):

`@python
def _import_module_from_path(path: str):
    """按文件路径导入（不要求父目录是包）"""
    mod_name = f"yunshu_custom_tool_{os.path.splitext(os.path.basename(path))[0]}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为 {path} 构造 import spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
`@

我实际核到的调用链(全部用只读检索/读码确认):

1. `_import_module_from_path` 在全仓**只有一个调用点**: persistence.py:410。
2. 该调用点位于 `load_dynamic_tools()`, 实参是局部变量 `path`;
   而 `path` 是 `for path in files:` 的循环变量, `files = _iter_custom_modules()`。
3. `_iter_custom_modules()` 的遍历目标是模块级常量
   `CUSTOM_TOOLS_DIR = os.path.join(_ROOT, "agent", "tools", "custom")`。
4. `load_dynamic_tools()` 的调用方是 `agent/orchestrator/lifecycle_manager.py:1014`, **不带任何参数**。
   ⇒ **没有任何用户输入 / 网络输入 / 配置输入能到达这个 path**。
5. 现状: `agent/tools/custom/` 下**没有任何 .py 文件**(只有 `__pycache__/_probe_tool.cpython-312.pyc`),
   即今天运行时这个循环加载 0 个模块。

被加载的**内容**确实是 LLM 生成代码(由 `tool_generator.generate_persistent` 落盘),
但它在本设计里的治理边界不在 importlib 这一行, 而在 persistence.py 模块文档 19-22 / 45-52 / 171-181 行写明的:
`register_all` + 保守治理声明 `plane=act / effect=execute / risk=critical`
⇒ `needs_approval` 为真(调用前需审批)。模块自己的注释也明确写着
"这道门**不能**替代什么 … 一个函数体里做危险操作的生成工具能通过这道门"。

### 1.2 判定

**既不是"假阳性"意义上的误报, 也不是"任意文件加载"漏洞, 而是: 规则真阳性 + 被扫代码是设计内的有界加载。**

规则为什么保持 HIGH 是明确的、且被代码注释承认的: `_is_controlled_spec_load()` 只对
"路径实参是常量且指向仓库内已存在文件"降级; 此处实参是变量 `path`, 落到
`_logger.debug("controlled-check ... keep HIGH (path arg not const literal ...)")` 分支。
扫描器的风险等级定义(第 10-11 行)本身就把"在**生产代码**中加载外部脚本(无包路径)"定为 HIGH ——
按这个口径这两行**符合规则描述**, 只是规则的"生产代码中的路径加载"在**本**调用点不构成可利用风险。

### 1.3 顺带发现(不在本卡文件归属内, **未修复**, 仅静态阅读)

`agent/tools/tool_generator.py:218/223`:

`@python
target_dir = os.path.join(_CUSTOM_TOOLS_DIR, category)   # 218
file_path = os.path.join(target_dir, f"{name}.py")       # 223
`@

`name` 与 `category` 都**没有做标识符/路径净化**(grep 该文件无 `isidentifier` / `sanitize` / `re.sub` 之类校验),
而它们来自 LLM 生成流程(`agent/tools/ext_tools.py:370 → engine.generate_persistent(name, ..., code, ...)`)。
`generate_simple` 的前置校验只在 `namespace.get(name)` 找不到时回退到"第一个公开可调用对象",
因此一个形如 `../../x` 的名字**不会**被这一步拦住, 落盘路径可以逃出 `agent/tools/custom/`。

**这是静态阅读得出的推断, 我没有实际利用/复现**(硬约束禁止写生产数据目录, 我也没有在仓库内落任何触发文件)。
它属于 tool_generator.py, 不在本卡可改范围 ⇒ 交给上游卡评估。

## 2. 退出码不一致: 我复现不出, 且结构上不可能

交办说明称"纯文本模式退出码 0、--json 模式退出码 1"。**我复现不出这个差异**, 实测如下(原始输出):

干净检出(git archive HEAD 解到 %TEMP%, 即 CI 的等价环境), HEAD 版扫描器:

`@
CLEAN_TEXT_EXIT=1
CLEAN_JSON_EXIT=1
clean_before {'scanned_files': 2366, 'high_risk_count': 2, 'medium_risk_count': 164, 'low_risk_count': 33}
`@

本工作树默认根(含未跟踪目录)HEAD 版扫描器:

`@
TEXT_EXIT_PIPED=1
TEXT_EXIT_NULL=1
JSON_EXIT_PIPED=1
`@

原因(结构性, 不是猜测): `main()` 里 `--json` 只影响**序列化输出**, 两种模式共用同一行退出码(修好后在 692 行):

`@python
    if args.json:
        ...仅 print(json.dumps(...))...
    else:
        print_report(report)

    # 退出码: 有 HIGH 风险返回 1
    # [不易] 退出码只取决于 report.high_risk (= risk_level=="HIGH" 的条数),
    # 文本模式与 --json 模式**共用这一行**, 不存在两种裁决口径;
    # MEDIUM/LOW/INFO 以及被审计豁免降级后的条目都不影响退出码.
    return 1 if report.high_risk else 0
`@

`report.high_risk` 是同一个属性(第 78-79 行, 按 `f.risk_level == "HIGH"` 过滤),
`--json` 里导出的 `high_risk_count` 走的也是它 ⇒ 两边不可能给出不同裁决。

另外我专门证伪了"可能是 --json 把 MEDIUM 也算进退出码 / 统计口径不同":
测试 `test_medium_does_not_affect_exit_code_in_either_mode` 用一个只含 `__import__("os")`(MEDIUM)的
样例根目录跑真 CLI, 断言两种模式**都**退出 0。**这条测试在 HEAD 版扫描器上就是通过的**(见 §5 的 6 passed)
⇒ 原始代码里 MEDIUM 从来不参与退出码。

**真正的"同一份代码两种结论"发生在 workflow 层, 而不是扫描器**(这点我确认了):
`.github/workflows/skills-check.yml`

- `dynamic-load-gate`(127-133 行): 跑**文本模式**, `continue-on-error: false` ⇒ **阻断**;
- `nightly-full-scan`(186-191 行): 跑 `--json`, `continue-on-error: true` ⇒ **不阻断**
  (该处注释明说"HIGH 阻断职责专属 dynamic-load-gate")。

这是**有意设计并已注释**的分工, 不是缺陷; 但它确实是"看到 --json 有 HIGH 却没人被挡住"这一现象的来源,
排查时不要误当成退出码不一致。
(附带一提: 文本模式下中文报告在 GBK 控制台会显示为乱码 —— 我这边实测到 mojibake,
这很容易让人误读 HIGH 计数, 但**不影响退出码**。)

## 3. 处置: 选 (b) 窄口径审计豁免

### 3.1 为什么不是 (a) 改代码

保持行为地"消除"这个 importlib 调用有两条路, 都不成立:

1. 改成 `importlib.import_module`(扫描器眼里是 LOW): 目标目录 `agent/tools/custom/` **不是包**
   (没有 `__init__.py`, 且要递归子目录), 必须先把 `agent/tools` 塞进 `sys.path` —— 那是**新增一个全局副作用**
   (模块遮蔽/命名冲突面变大), 而且会把模块身份从 `yunshu_custom_tool_<name>` 改成包内名,
   影响 `sys.modules` 键与重载语义 ⇒ **不是"保持行为"**。
2. 换成 `runpy.run_path` / `exec(compile(...))`: 风险一模一样, 只是**从扫描器视野里消失** ——
   这是把告警藏起来而不是消除风险, 明确违反"不得削弱安全检查"。

结论: 这里没有"既保行为又不加载路径"的改法; 真正的边界在治理声明与审批, 不在这一行。

### 3.2 豁免的窄口径设计(scripts/detect_dynamic_loads.py)

新增 `AuditedDynamicLoadExemption` 与 `AUDITED_DYNAMIC_LOAD_EXEMPTIONS`(仅 2 条, 同属一个调用点):

`@python
AuditedDynamicLoadExemption(
    file="agent/tools/persistence.py",   # 必须是具体文件, 不是目录前缀
    qualname="_import_module_from_path", # 必须是具体函数, 不是整个文件
    pattern="spec_from_file_location",   # 必须是具体动态加载函数名
    max_matches=1,                       # 命中配额: 超出的那次保持 HIGH
    reason="受控目录遍历加载(设计内的有界加载, 非外部输入注入)…",
    evidence="tests/unit/test_dynamic_loads_high_exemption.py",
)
`@

行为: 命中 ⇒ **降级为 MEDIUM**(不是删除), 发现仍出现在文本报告与 `--json` 里,
并带新字段 `exempted_by`(文本报告多打一个 `[exempt: …]` 标记)。
未命中的豁免条目(在扫描范围内)会在 stderr 打 `stale exemption (registered but never matched in this scan)` 告警。

顺带修掉一个**实测踩到的坑**: 豁免路径必须以**仓库根 ROOT** 为基准计算。
第一版用扫描根(`self.root`)算, 导致 `--root agent` 时键变成 `tools/persistence.py`
而全仓扫描是 `agent/tools/persistence.py` ⇒ **同一份代码在两种扫描方式下结论不一致**(实测 HIGH 仍为 2)。
现已与 `_is_controlled_spec_load` 既有口径(它早就注明"基于 ROOT 而非 self.root")统一。

### 3.3 为什么这不算"放宽守卫"(五条防线, 均有测试)

1. **降级而非删除**: 两条发现仍在报告里(测试 `test_exempted_findings_are_still_reported_as_medium`)。
2. **三元组全等**: 换文件(`test_different_file_still_high`)、**同文件换函数**
   (`test_same_file_different_function_still_high`)都仍然 HIGH ⇒ 不是文件级/目录级放宽。
3. **命中配额**: 同文件同函数里再写一个 `spec_from_file_location`, 多出来的那个仍 HIGH
   (`test_extra_occurrence_in_same_function_stays_high`, 断言 HIGH 正好落在第 2 个调用点第 7 行)。
4. **规则本身未动**: 未登记文件里的常量路径(指向不存在文件)照旧 HIGH
   (`test_literal_missing_path_in_unregistered_file_still_high`); `_is_controlled_spec_load` 逻辑一字未改。
5. **防腐 + 自证**: 陈旧豁免告警(`test_stale_exemption_is_warned`);
   以及 `test_guard_is_sensitive_to_the_exemption` —— 把豁免表清空, 同样输入**立刻回到 HIGH=2**,
   证明那两条绿不是"规则被删"换来的。
   另有 `test_exemption_anchor_call_chain_is_pinned`: 用 AST 钉死调用链
   (唯一调用方必须是 `load_dynamic_tools`、实参必须来自 `_iter_custom_modules()`、
   必须 `os.walk(CUSTOM_TOOLS_DIR)`、常量必须仍指 `agent/tools/custom`)。
   实测该断言的敏感性: 在 %TEMP% 副本上给被豁免函数加第二个调用方后,
   `mutant call sites = 2` ⇒ `assert len(calls) == 1` 必红。

**未改** `.github/workflows/skills-check.yml`(continue-on-error 仍是 false), **未删任何规则**。

## 4. 改前 / 改后实测计数

### 4.1 干净检出等价测量(权威口径, CI 就是这个输入)

用 `git archive --format=tar HEAD` 解出干净树, 对同一棵树分别放"HEAD 版扫描器"与"修好的扫描器"跑:

| 场景 | 扫描文件 | HIGH | MEDIUM | LOW | 文本退出码 | --json 退出码 |
|---|---|---|---|---|---|---|
| 改前(HEAD 扫描器) | 2366 | **2** | 164 | 33 | 1 | 1 |
| 改后(修好的扫描器) | 2366 | **0** | **166** | 33 | **0** | **0** |

原始输出:

`@
CLEAN_TEXT_EXIT=1        CLEAN_JSON_EXIT=1
clean_before {'scanned_files': 2366, 'high_risk_count': 2, 'medium_risk_count': 164, 'low_risk_count': 33}
 HIGH agent\tools\persistence.py 380 importlib.util.spec_from_file_location
 HIGH agent\tools\persistence.py 383 importlib.util.module_from_spec
CLEAN_TEXT_EXIT_AFTER=0  CLEAN_JSON_EXIT_AFTER=0
clean_after {'scanned_files': 2366, 'high_risk_count': 0, 'medium_risk_count': 166, 'low_risk_count': 33}
 EXEMPTED agent\tools\persistence.py 380 MEDIUM
 EXEMPTED agent\tools\persistence.py 383 MEDIUM
`@

**只有 2 条发现移位**: HIGH 2→0 且 MEDIUM 164→166(+2), LOW 与扫描文件数不变 ⇒ 豁免没有顺带影响第三个发现。

### 4.2 本工作树默认根(含未跟踪目录, 与 CI 无关)

| 场景 | 扫描文件 | HIGH | MEDIUM | LOW | 文本退出码 | --json 退出码 |
|---|---|---|---|---|---|---|
| 改前 | 11682 | 48 | 453 | 59 | 1 | 1 |
| 改后 | 11685(注) | **46** | 457 | 59 | 1 | 1 |

(注) 扫描文件数 11682→11685、MEDIUM 453→457 的变化包含**并发工作的其它卡**在同一工作树里的改动
(观察期间 `git status` 出现过 `agent/descriptors/registry.py` 等其它卡的改动), 所以本行**不是**干净测量;
权威口径请用 §4.1。

改后仍为 1 与本次修复无关, 而是**未跟踪、被 gitignore 的临时目录**:

`@
HIGH_by_topdir {'.tmp-merge': 2, '_ci_logs': 28, '_scratch': 4, '_t06_logs': 12}
clean_high 0 dirty_high 46
only_in_dirty(untracked junk): 46
`@

即 46 处 HIGH **全部**落在未跟踪目录里, 干净检出为 0 处。

### 4.3 --root agent 场景

| 场景 | 扫描文件 | HIGH | MEDIUM | LOW | 文本退出码 | --json 退出码 |
|---|---|---|---|---|---|---|
| 改前 | 622 | 2 | 25 | 17 | 1 | 1 |
| 改后 | 622 | **0** | 27 | 17 | **0** | **0** |

### 4.4 未跟踪目录的独立核实(对交办说明的一点更正)

`@
_t06_logs -> []        (git ls-files 无输出 = 未跟踪)
_ci_logs  -> []
_scratch  -> []
.tmp-merge-> []
git check-ignore -v _t06_logs → .gitignore:594:_t06_logs/	_t06_logs
`@

⇒ **证实**: `_t06_logs/` 未跟踪且被 gitignore, 与干净检出无关。
**更正**: 我实测 `_t06_logs/` 下是 **12 处 HIGH(6 文件 × 2)** ——
`cost_compare.py` / `probe11.py` / `probe2.py` / `probe7.py` / `probe8.py` / `sensitivity.py`,
而不是交办说明里的 8 处。该目录未跟踪、内容可变, 两个数字可能各自反映不同时刻, 但结论不变。

## 5. 改前红 / 改后绿(同一条测试文件)

守护测试: `tests/unit/test_dynamic_loads_high_exemption.py`(14 条, 全部本卡新增)

改后(当前扫描器):

`@
$ python -m pytest tests/unit/test_dynamic_loads_high_exemption.py -q -p no:randomly --timeout=60
通过: 14   失败: 0   跳过: 0
======================= 14 passed, 1 warning in 19.63s ========================
`@

改前(用 byte-exact 的 HEAD 版扫描器, 不改动仓库任何文件):

`@
$ python -c "...git cat-file blob HEAD:scripts/detect_dynamic_loads.py → %TEMP%\dyngate1_head\detect_dynamic_loads.py"
head_sha da124e3fcd7f   work_sha f7b16cf1e2dd
$ $env:DYNGATE1_SCANNER_PATH="$env:TEMP\dyngate1_head\detect_dynamic_loads.py"
$ python -m pytest tests/unit/test_dynamic_loads_high_exemption.py -q -p no:randomly --timeout=60
E   AssertionError: persistence.py 仍有 HIGH: [(380, 'importlib.util.spec_from_file_location'), (383, 'importlib.util.module_from_spec')]
E   AssertionError: 应有 2 条 MEDIUM(降级后), 实际 0
E   AssertionError: agent/ 仍有 HIGH: [('tools\\persistence.py', 380), ('tools\\persistence.py', 383)]
E   AttributeError: module 'dyn_scan_under_test' ... has no attribute 'AUDITED_DYNAMIC_LOAD_EXEMPTIONS'
E   AssertionError: 超配额的调用点必须保持 HIGH, 实际 [(5,...), (6,...), (7,...)]
E   AssertionError: 未命中的豁免条目应产生 stale exemption 告警
E   AssertionError: 文本模式应退出 0 (无 HIGH)
8 failed, 6 passed in 20.88s
`@

**这 6 条在"改前"就通过, 恰好证明文本/--json 退出码在原始代码里也是一致的**
(其中 `test_high_makes_both_modes_exit_one` 与 `test_medium_does_not_affect_exit_code_in_either_mode`
都是退出码口径测试)。

## 6. 可复现命令清单

`@powershell
cd C:\Users\Administrator\agent
$env:PYTHONUTF8="1"; $env:PYTHONIOENCODING="utf-8"

# 1) 干净检出等价测量(改前/改后)
$clean = "$env:TEMP\dyngate1_clean"; Remove-Item -Recurse -Force $clean -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $clean | Out-Null
git archive --format=tar HEAD -o "$env:TEMP\dyngate1_head.tar"; tar -xf "$env:TEMP\dyngate1_head.tar" -C $clean
python "$clean\scripts\detect_dynamic_loads.py"        ; "HEAD text exit=$LASTEXITCODE"   # 1
python "$clean\scripts\detect_dynamic_loads.py" --json ; "HEAD json exit=$LASTEXITCODE"   # 1
Copy-Item .\scripts\detect_dynamic_loads.py "$clean\scripts\" -Force
python "$clean\scripts\detect_dynamic_loads.py"        ; "FIXED text exit=$LASTEXITCODE"  # 0
python "$clean\scripts\detect_dynamic_loads.py" --json ; "FIXED json exit=$LASTEXITCODE"  # 0

# 2) 本工作树(注意含未跟踪目录, 与 CI 无关)
python scripts/detect_dynamic_loads.py          ; "exit=$LASTEXITCODE"
python scripts/detect_dynamic_loads.py --json   ; "exit=$LASTEXITCODE"
python scripts/detect_dynamic_loads.py --root agent --json

# 3) 改前红 / 改后绿
python -m pytest tests/unit/test_dynamic_loads_high_exemption.py -q -p no:randomly --timeout=60   # 14 passed
$head = "$env:TEMP\dyngate1_head"; New-Item -ItemType Directory -Force -Path $head | Out-Null
python -c "import subprocess,pathlib;d=subprocess.run(['git','cat-file','blob','HEAD:scripts/detect_dynamic_loads.py'],capture_output=True).stdout;pathlib.Path(r'$head\detect_dynamic_loads.py').write_bytes(d)"
$env:DYNGATE1_SCANNER_PATH="$head\detect_dynamic_loads.py"
python -m pytest tests/unit/test_dynamic_loads_high_exemption.py -q -p no:randomly --timeout=60   # 8 failed, 6 passed

# 4) 未跟踪目录核实
git ls-files -- _t06_logs _ci_logs _scratch .tmp-merge      # 全空 = 未跟踪
git check-ignore -v _t06_logs                               # .gitignore:594:_t06_logs/
`@

## 7. 我没能确认的部分

1. **"文本模式退出码 0 / --json 退出码 1" 我复现不出**。四种场景(干净树改前/改后、工作树改前/改后、子根扫描)
   全部显示两模式**相等**, 结构性原因见 §2。若你那次观测有具体命令与原始输出请给出 ——
   我目前的结论是"该现象在本 HEAD + 本环境下不存在"。
   (唯一能让文本模式返回 0 的情形是 `report.high_risk` 为空, 那就与"HIGH=2"自相矛盾。)
2. **tool_generator 的路径逃逸只是静态推断, 未实测利用**(§1.3), 也没有在仓库内造过任何触发文件。
3. **豁免的残留缺口(诚实说明)**: 豁免锚定在"函数"上。若将来有人给 `_import_module_from_path`
   **新增一个调用方并传入外部路径**, 该新入口不会被扫描器报警(报警点在函数体内)。
   我用 `test_exemption_anchor_call_chain_is_pinned` 把"当前只有一个调用方、且实参来自固定目录遍历"钉成了断言
   (新增调用方 ⇒ 测试红), 但这要求有人跑测试; **扫描器本身检测不到这种跨函数的新入口**。
   彻底堵住需要跨过程数据流分析, 本卡没做。
4. **未跑全量 tests/unit**(交办说明已声明要几十分钟, 本卡不在前台跑)。本卡改动只碰
   `scripts/detect_dynamic_loads.py`(全仓无任何生产/测试代码 import 它, 只有 `.github/workflows/skills-check.yml` 调用它),
   受影响的测试面就是新增的这一条文件。
5. **`security-tools/scripts/detect_dynamic_loads.py` 我没有改**(不在本卡归属内)。它是另一份**内容不同**的副本
   (11603 字节 vs 根部 22820 字节, sha 不同), 且**没有任何 workflow 引用它**;
   若它将来被当作门禁使用, 那 2 处 HIGH 仍会被报出来。
6. "`agent/tools/custom/` 无 .py 文件"是在本工作树观察的, 不排除历史上生产环境曾有生成工具落盘
   (审计链数据 `data/audit/**` 我没有读, 也不该读)。

## 8. 变更文件

- `scripts/detect_dynamic_loads.py`(+191 / -10): 新增审计豁免注册表、函数级 qualname 追踪、配额约束、
  `exempted_by` 字段、陈旧豁免告警、ROOT 基准的豁免路径解析、退出码口径注释。
- `tests/unit/test_dynamic_loads_high_exemption.py`(新增, 14 条; 对修好的扫描器全绿, 对 HEAD 版 8 红)。
- `docs/audit_skill_governance/DYNGATE1.md`(本文件)。
- **未改**: `agent/tools/persistence.py`、`.github/workflows/skills-check.yml`、其它任何文件。
