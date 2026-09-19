# 覆盖率盲区补测计划 — `sensor/` `cognitive/` `core/`（2026-09-19）

> **任务**：把权威覆盖率实测暴露的 8,082 行盲区，变成一份**有优先级、有依据、可执行**的补测计划。
> **原则**：先判"活代码 vs 死代码"，**绝不给死代码写测试**。
> **状态**：本文件是**计划**。第 6 节记录了实际实施的 P0 第一批与其**实测**覆盖率变化。

---

## 0. 结论速览（先读这一段）

| 问题 | 结论 |
|---|---|
| 三个包里有死代码吗？ | **只有 34 行**（`core/local_llm.py`）。其余 8,150 行**全部活跃** |
| `core/` 是"整个包都死的"吗？ | **不是**。原表报"core 34 行 / 0%"是**汇总脚本的口径 bug**（见 §2.5）。真实 `core/` = 109 行，**64.2% 已覆盖**；`core/registry.py` 93.1% |
| `cognitive/` 为什么只有 11.56%？ | **主要是口径假象**：`cognitive/test_cognitive/` 的 **297 行测试代码躺在覆盖分母里，却被 `pytest.ini:52 --ignore` 排除从不执行**。剔除后 `cognitive/` 源码覆盖率是 **30.2%**，不是 11.56% |
| `sensor/` 21% 是"少数大模块未覆盖"还是"普遍浅覆盖"？ | **普遍浅覆盖**。Top 15 占 5,232 / 5,949 = **87.9%**，但 33 个文件里 **20 个 < 35%**，且 Top 15 **全部是活跃代码** |
| 能测吗？ | 分三层：**P0 完全可测（+717 行）**、**P1 需 mock IO（+900~1,300 行）**、**P2 受 Windows 平台限制（CI 上 0 收益）** |
| 建议动作 | **不要**为 `core/local_llm.py` 写测试（删除/标注）；**不要**追 `port_sensor`/`board_sensor` 等 WMI 模块的覆盖率（Linux CI 上永远为 0）；**先做 P0**，收益/成本比高一个数量级 |

> 🔴 **补测之外的重大发现（2026-09-19 追加，已修，见 §7.5）**
> 补测过程中发现一条**生产级静默失效**：`cognitive/translator.py::translate()` 只认 `dict`，
> 而真实读数是 `SensorReading` **对象** ⇒ 守卫把全部 651 条读数拦在入口，
> **100% 返回硬编码"传感器读数未识别"**，注入 LLM 的"身体状态"整段是噪声。
> 修后未识别率 **100% → 0.15%**，生产截断的 800 字符内由"全是噪声"变为 **20 行真实读数**。
> **本次补测的 93 条用例全部通过却没发现它** —— 因为夹具传 dict、生产传对象。
> 这一条比覆盖率数字重要得多，故置于此处。
>
> 已结案遗留项：**L-2**（`_fallback` 从未调用 + 类型不兼容）、**L-10**（`collect_all` 因裸 dict 全丢）。

---

## 1. 数据口径与来源（**引用前必读**）

### 1.1 本文所有数字的来源

| 项 | 值 |
|---|---|
| 数据文件 | `_ci_logs/coverage_auth/.coverage`（SQLite，2026-09-19 19:45，221,184 字节） |
| 采集命令 | `python scripts/run_authoritative_coverage.py --out _ci_logs/coverage_auth` |
| 采集口径 | `--cov=agent --cov=sensor --cov=memory --cov=planning --cov=persona --cov=core --cov=cognitive --cov=lifetrace --cov=utils`（9 包，逐块展开） |
| 测试集 | `tests/unit` 全量，轮转 10 块，`-p no:randomly`，`--timeout=300 --timeout-method=thread` |
| 宿主 | Windows（本机部署机） |
| 读取方式 | `coverage.CoverageData` + `coverage.parser.PythonParser`，**用绝对路径归包**（不用 XML 相对文件名） |
| 复现脚本 | `_gap_parse3.py`（本文档同目录之外的仓库根，临时脚本） |

**⚠️ 为什么不用 `coverage.xml` 归包**：见 §2.5 —— XML 的相对文件名在"多 `--cov=` 根"下会**跨包撞名**，导致按前缀归包静默丢文件。本文改用 `.coverage` 的绝对路径归包，是可复核的权威口径。

### 1.2 与任务书表格的差异（**逐项对清，不掩盖**）

| 包 | 任务书（有效行 / 覆盖率） | 本文实测（有效行 / 覆盖 / 覆盖率） | 差异与原因 |
|---|---|---|---|
| `sensor` | 7,555 / 21.26% | **7,579 / 1,630 / 21.51%** | +24 行 = `sensor/__init__.py` 被汇总表漏掉 |
| `cognitive` | 493 / 11.56% | **496 / 60 / 12.10%** | +3 行 = `cognitive/__init__.py` 被漏掉 |
| `core` | 34 / 0.00% | **109 / 70 / 64.22%** | **+72 行**：汇总表把 `core/registry.py`（72 行、93.1% 覆盖）整份丢了（原因见 §2.5） |
| 9 包合计 | 135,752 / 100,637 / **74.13%** | 135,872 / 100,629 / **74.06%** | Δ120 行来自 `omit` 与 `__init__.py` 归属差异，**不影响任何结论方向** |

⇒ **引用时必须写明口径**。任务书的密度（74.13%）成立；**但逐包分解表有三个包的归属是错的**，尤其 `core`。

### 1.3 关于"这 3 个包从未进入任何覆盖率分母"这一根因

**部分成立，但需要分成两个 workflow 说**：

| 入口 | 现状 | 证据 |
|---|---|---|
| `.github/workflows/ci.yml` 的 unit-tests 分片 | **已修好**，9 包全展开 | `ci.yml:464-472`（`--cov=agent` … `--cov=utils` 九行） |
| `.github/workflows/coverage-ci.yml` | **仍是 `--cov=agent` 单包**，分母退化成 1 包 | `coverage-ci.yml:64` |
| `.github/workflows/ci-cd.yml` | 仍是 `--cov=agent` 单包 | `ci-cd.yml:139` |
| **生成器**（第二真相源） | `--cov=__PACKAGE__` 单占位符，重新生成会把单包口径写回 | `scripts/generate_coverage_workflow.py:122` |

⇒ 根因**不是"CI 一直只写 `--cov=agent`"**（`ci.yml` 已改），而是：
**`coverage-ci.yml` / `ci-cd.yml` 仍是单包 + 生成器 `generate_coverage_workflow.py:122` 会持续把单包口径写回**。
按 D1（单一真相源），`generate_coverage_workflow.py` 应改为从 `pyproject.toml [tool.coverage.run].source` 读包列表——这与 `scripts/run_authoritative_coverage.py:86 read_declared_packages()` 已是同一模式，改造成本极低。**登记为遗留项 L-1**。

---

## 2. 三个包的实际性质（逐个查清，带 `文件:行号` 证据）

### 2.0 生产入口链（**这是全部存活判定的锚点，已实测**）

```
start_yunshu.bat:21   →  python app_server.py
app_server.py:539     →  from agent import DigitalLife
app_server.py:542     →  _Yunshu = DigitalLife(_cfg.merged)
agent/digital_life.py:380   class DigitalLife(Orchestrator, TaskDispatcher, LifecycleManager, ...)
agent/digital_life.py:168-170  from sensor import BodySensor / from cognitive import PromptInjector, PromptConfig
agent/digital_life.py:214      _safe_import('规划引擎', _import_planning, ...)   ← 立即执行
agent/digital_life.py:211      from planning import ...  → planning/__init__.py:8
planning/executor.py:23        from core.registry import SimpleRegistry
agent/orchestrator/lifecycle_manager.py:173  self._initialize_core_systems()
agent/orchestrator/lifecycle_manager.py:253  self.body = BodySensor(...)
sensor/body_sensor.py:90-95    SensorRegistry().discover(extra_kwargs=...)
sensor/registry.py:104-184     importlib.import_module(f"sensor.{mod_name}")  ← 动态导入全部 sensor 模块
```

**实测**（`_gap_discover.py`，本机 Windows，`YUNSHU_DISABLE_WINDOW_SENSOR` 未设）：

```
discover() 注册成功 16 个：battery, behavior, board, chassis, cpu, disk, env, gpu,
                          hwfile, memory, network, peripheral, port, process, system, window
discover() 期间 import 了 30 个 sensor.* 模块（含 test_body_sensor、main）
```

> ⚠️ **方法论警告（对应 §0.2b 第 2 条）**：仅用 `from sensor.X` 的**绝对导入正则**做静态检索，
> 会把 `software_blueprint` / `file_blueprint` / `hardware_blueprint` / `event_monitor` /
> `registry` / `counter_reader` **误判为"完全死代码"**——因为它们是
> **包内相对导入**（`sensor/body_sensor.py:17-22,90`）或**函数级相对导入**
> （`sensor/gpu_sensor.py:62,246`、`sensor/memory_sensor.py:155,198`、
> `sensor/port_sensor.py:413,464`、`sensor/process_sensor.py:417`）或
> **运行期 `importlib` 动态导入**（`sensor/registry.py:144`）。
> 本文的判定已改为**三口径互证**：绝对导入 + 相对导入 + **实跑 discover()**。

### 2.1 `sensor/` — 33 个文件 / 7,579 行 / 21.51%

**它是什么**：云枢的**硬件与系统感知底座**。`BodySensor` 是聚合器，`SensorRegistry` 自发现
"类名以 Sensor 结尾 + 有 `collect()`"的模块并实例化，`BodySensor.collect_all()` 逐个调用
`collect()`（`sensor/body_sensor.py:504-517`）。共 27 处 `platform.system()` 分派。

**存活判定（逐文件）**

| 模块 | 判定 | 证据 |
|---|---|---|
| `cpu/battery/disk/memory/network/gpu/board/chassis/port/peripheral/environment/process/system/behavior/hardware_file/window_sensor` | **活跃**（生产自发现注册并 `collect()`） | 实跑 discover() 返回 16 个；`sensor/registry.py:144` 动态导入；`sensor/body_sensor.py:511` 统一采集 |
| `body_sensor.py` | **活跃** | `agent/orchestrator/lifecycle_manager.py:253` |
| `registry.py` | **活跃** | `sensor/body_sensor.py:90-92` |
| `hardware_blueprint.py` / `file_blueprint.py` / `software_blueprint.py` | **活跃** | `sensor/body_sensor.py:17-19` 导入，`:75-77` 构造 |
| `change_detector.py` | **活跃**（懒加载） | `sensor/body_sensor.py:20,169,520-527`；`lifecycle_manager.py:255` 默认 `enable_change_detection=True` |
| `event_monitor.py` | **活跃**（启动即建） | `sensor/body_sensor.py:22,207-213`；`lifecycle_manager.py:256` 默认 `enable_event_monitor=True` |
| `file_watcher.py` | **活跃**（条件） | `sensor/body_sensor.py:21,237-241`；`agent/knowledge/ingest.py:580`、`agent/knowledge/index.py:356`。**注**：`config.yaml` 无 `sensor:` 段 ⇒ `watch_dirs=None` ⇒ 经 `BodySensor` 这条路径不会创建 |
| `counter_reader.py` | **活跃**（函数级导入，全部落在平台分支内） | `sensor/gpu_sensor.py:62,246`、`memory_sensor.py:155,198`、`port_sensor.py:413,464`、`process_sensor.py:417`、`sensor/registry.py:134` 只是把它排除出自发现 |
| `novelty.py` | **活跃** | `agent/learning/novelty_hooks.py:30`、`agent/learning/behavior_drift.py:28` |
| `tags.py` | **活跃** | `agent/server_routes/routes_panorama.py:51`、`plugins/status.py:522` |
| `sensor_reading.py` | **活跃** | `agent/digital_life.py:169`、`agent/orchestrator/orchestrator.py:2894`、`sensor_server.py:102` |
| `ocr_sensor.py` | **活跃** | `lifetrace/enhanced_recorder.py:187,405`、`agent/digital_life.py:276` |
| `voice_sensor.py` | **活跃** | `agent/digital_life.py:79,267` |
| `main.py` | **未接线**（CLI 示例入口） | 全仓唯一引用是它自己的 docstring `sensor/main.py:5 "运行方式: python -m sensor.main"`；生产 `discover()` 只 import 不调用 `main()` |
| `test_body_sensor.py` | **包内测试，从未被 pytest 收集** | 见 §2.4 |
| — | **无完全死代码** | — |

> 🔴 **`sensor_server.py` 未接线**：它 `import cognitive.flask_adapter`（`sensor_server.py:753`），
> 但**没有任何启动脚本引用它**（启动路径只有 `start_yunshu.bat:21 → app_server.py`）。
> ⇒ `cognitive/flask_adapter.py`（32 行、0% 覆盖）**不是"漏测"，是未接线**。

### 2.2 `cognitive/` — 14 个文件 / 496 行 / 12.10%　（**其中 297 行是测试代码**）

**它是什么**：把传感器数值翻译成第一人称拟人化中文，注入系统提示词。纯业务逻辑，零硬件依赖。

| 模块 | 有效行 | 覆盖 | 判定 | 证据 |
|---|---:|---:|---|---|
| `config.py` | 40 | 19 (47.5%) | **活跃** | `lifecycle_manager.py:269` `PromptConfig(...)`（顶部硬导入，见 `:1308`） |
| `translator.py` | 60 | 10 (16.7%) | **活跃** | `cognitive/prompt_injector.py:5` |
| `templates.py` | 22 | 11 (50.0%) | **活跃** | `agent/server_routes/routes_panorama.py:112`、`plugins/status.py:584` |
| `prompt_injector.py` | 42 | 17 (40.5%) | **活跃** | `lifecycle_manager.py:270`、`agent/digital_life.py:170` |
| `flask_adapter.py` | 32 | 0 (0.0%) | **未接线** | 唯一引用 `sensor_server.py:753`，而 `sensor_server.py` 无启动脚本引用 |
| `__init__.py` | 3 | 3 | **活跃** | — |
| `test_cognitive/`（8 文件） | **297** | **0 (0.0%)** | **包内测试，被 `pytest.ini:52` 忽略** | 见 §2.4 |

**源码口径修正**：剔除 `test_cognitive/` 的 297 行后，`cognitive/` 源码 = **199 行 / 60 覆盖 = 30.2%**。
⇒ 任务书表格里的 **11.56% 有约 19 个百分点是分母假象**，不是"没测"。真实问题只剩"30.2% 偏低"。

### 2.3 `core/` — 3 个文件 / 109 行 / **64.22%**（**不是 0%**）

| 模块 | 有效行 | 覆盖 | 判定 | 证据 |
|---|---:|---:|---|---|
| `registry.py` | 72 | 67 (93.1%) | **活跃** | `planning/executor.py:23`；链路 `agent/digital_life.py:214 → planning/__init__.py:8 → planning/executor.py:23` |
| `local_llm.py` | 34 | 0 (0.0%) | 🔴 **完全死代码** | 在 **695 个生产文件 + 825 个测试文件**里 0 引用；`core/__init__.py:11-26` 只再导出 `registry`；`core/__init__.py:8` docstring 自述 **"当前保留模块：registry: 被 planning/executor.py 使用"** |
| `__init__.py` | 3 | 3 | **活跃** | — |

**`core/local_llm.py` 的性质**：`LocalLLM` 类 + 模块级单例 `local_llm = LocalLLM()`（`:49`），
打 `http://localhost:11434`（Ollama）/ `:8000`（vLLM），是"原则5 本地可用性"的**预留桩**。

⇒ **正确处置是"删除或标注未接线"，不是补测**。给它写测试只会把 34 行死代码永久固化进分母。

### 2.4 🔴 两个包把**测试代码放进了生产包**，且一个被忽略、一个被生产 import

| 文件 | 行数 | 有效语句 | 现状 |
|---|---:|---:|---|
| `cognitive/test_cognitive/`（8 文件） | 445 | **297** | `pytest.ini:52` 明写 `--ignore=cognitive/test_cognitive`；`testpaths = tests` ⇒ **永不收集**。**实测手跑：48 个用例，46 通过 / 2 失败**（见 §2.4b） |
| `sensor/test_body_sensor.py` | 298 | **195** | `testpaths = tests` ⇒ 永不收集。**但被生产 `SensorRegistry.discover()` import**（`registry.py:125-128` 只排除 `_` 开头，`test_*` 未被排除） |

**`sensor/test_body_sensor.py` 的判定，已用最小复现坐实**：

```
单独 `import sensor.test_body_sensor`     → statements=195  executed=66
权威全量运行（tests/unit 全量）           → valid=195  cover=66  miss=129
第一个未覆盖行 = 37（'r = SensorReading("test_sensor", 42.5, "%", "测试传感器")'）
```

⇒ **两行完全一致**，证明：该文件 66 个已覆盖语句**全部来自被 import**，
**12 个 `TestCase` 一个都没跑过**。它同时是
（a）分母污染（195 行放在生产包里）、（b）生产副作用（`discover()` 在生产启动时 import 一个 unittest 模块）。

### 2.4b 🔴 `pytest.ini` 的 `--ignore` 掩盖了 **2 个真实测试失败**

**实测命令**（用 `-o addopts=""` 临时清掉 `--ignore`，不改任何文件）：

```powershell
$env:PYTHONUTF8="1"
python -m pytest cognitive/test_cognitive -q --no-header -p no:cacheprovider -p no:randomly -o addopts="" --timeout=120
```

**实测结果**：`2 failed, 46 passed in 2.86s`

```
FAILED .../test_translator.py::TestTranslator::test_translate_unknown_sensor_fallback
    assert '测试传感器' in '传感器读数未识别'
FAILED .../test_translator.py::TestTranslator::test_translate_missing_sensor_name
    assert '42' in '传感器读数未识别'
```

**归因（已读源码）**：`cognitive/translator.py:88-93` 定义了 `_fallback()`（"无匹配规则时的通用描述"），
但 `translate()` 的 4 个兜底出口（`:23, :28, :32, :43, :59, :61`）**全部直接返回硬编码
`"传感器读数未识别"`，从不调用 `_fallback()`**。
⇒ `_fallback()` 是**死方法**（11 条语句），且**测试期望与实现不一致**。
这是被 `--ignore` 掩盖了不知多久的真实缺陷。**登记为遗留项 L-2**（修哪一边是产品决策，本任务不擅自改生产行为）。

### 2.5 🔴 汇总表"core 34 行 / 0%"是**归包 bug**（可复核）

`coverage.xml` 在"多 `--cov=` 源根"下，`filename` 是**相对各自源根**的路径。因此：

```
C:\...\agent\sensor\registry.py   →  filename="registry.py"     ← 相对 sensor 根
C:\...\agent\core\registry.py     →  filename="registry.py"     ← 相对 core  根   ⚠️ 撞名
C:\...\agent\sensor\__init__.py   →  filename="__init__.py"     ← 相对 sensor 根
C:\...\agent\core\__init__.py     →  filename="__init__.py"     ← 相对 core  根   ⚠️ 撞名
C:\...\agent\cognitive\__init__.py→  filename="__init__.py"     ← 相对 cognitive 根 ⚠️ 撞名
```

按 `filename` 建字典归包时 **后写覆盖先写**：
`registry.py` → 只留 `sensor/registry.py`，**`core/registry.py`（72 行、93.1%）被静默丢弃**；
`__init__.py` → 三个包的 `__init__.py`（24+3+3 行）只留一个。

**对账验证**：任务书 `core` = 34 行 = 恰好 `local_llm.py` 的 34 行（`109 - 3(__init__) - 72(registry) = 34`）✔
任务书 `sensor` = 7,555 = `7,579 - 24(__init__)` ✔　`cognitive` = 493 = `496 - 3(__init__)` ✔

⇒ **这不是覆盖率问题，是汇总脚本的口径 bug。** 本文所有逐包数字改用 `.coverage` 绝对路径归包。
**登记为遗留项 L-3**（`scripts/run_authoritative_coverage.py` 的汇总环节或产生该表的脚本应改为绝对路径归包）。

---

## 3. 未覆盖代码的性质分类（决定"该不该补测"）

### 3.1 三包合计

| 性质 | 行数 | 占比 | 该不该补测 |
|---|---:|---:|---|
| **业务逻辑 / 平台适配主体**（解析、阈值、开关、聚合、diff、格式化） | 3,859 | **60.1%** | ✅ **该测**（P0/P1） |
| **外部二进制 / IO 封装**（函数体内含 `subprocess`/PowerShell/`wmic`/`netsh`/`nvidia-smi`） | 1,606 | **25.0%** | ⚠️ 该测但**必须 mock 外部进程**，且多数只在 Windows 可达（→ P2） |
| **包内测试代码（从未运行）** | 426 | **6.6%** | ❌ **不是补测对象**：应移出生产包 / 解除 ignore（→ 处置动作） |
| **异常处理 / 防御分支**（`except` 体、`is None`/`isinstance` 防御） | 372 | **5.8%** | ⚠️ 低价值，可豁免并说明 |
| **平台分支（函数名含 windows/linux/macos/darwin）** | 520 | **8.1%** | ❌ Linux CI 上不可覆盖（→ P2 / 建议 skip 标注） |
| **完全死代码** | **34** | **0.5%** | ❌ **删除或标注**，绝不补测 |
| **已被 `# pragma: no cover` 豁免** | **0** | 0% | — |

> 说明：分类为**按行唯一归因、优先级判定**，故上表分类互斥但"平台分支"与"外部二进制/IO"有语义重叠
> （一个 Windows 分支里的 PowerShell 调用只计一次）。**三包 `# pragma: no cover` 实测为 0 处**——
> 这三个包**完全没有豁免标注**，所以"S 已有豁免"这一类在本题中不存在。

### 3.2 `sensor/` 逐文件性质分布（按未覆盖行数排序）

| 文件 | 未覆盖 | 有效行 | 覆盖率 | 业务/适配 | 外部二进制/IO | 异常防御 | 平台方法 | 死代码/包内测试 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| behavior_sensor.py | 539 | 645 | 16.4% | 385 | 111 | 43 | 39 | 0 |
| software_blueprint.py | 435 | 450 | 3.3% | 48 | 383 | 4 | 21 | 0 |
| port_sensor.py | 422 | 446 | 5.4% | 262 | 113 | 26 | 129 | 0 |
| event_monitor.py | 392 | 428 | 8.4% | 216 | 150 | 22 | 2 | 0 |
| system_sensor.py | 377 | 408 | 7.6% | 98 | 274 | 5 | 31 | 0 |
| board_sensor.py | 291 | 310 | 6.1% | 174 | 75 | 33 | 151 | 0 |
| hardware_blueprint.py | 280 | 292 | 4.1% | 272 | 0 | 8 | 0 | 0 |
| hardware_file_sensor.py | 273 | 349 | 21.8% | 193 | 61 | 4 | 32 | 0 |
| peripheral_sensor.py | 261 | 277 | 5.8% | 147 | 98 | 16 | 0 | 0 |
| counter_reader.py | 236 | 236 | 0.0% | 0 | **236** | 0 | 0 | 0 |
| network_sensor.py | 225 | 254 | 11.4% | 152 | 55 | 18 | 0 | 0 |
| cpu_sensor.py | 212 | 236 | 10.2% | 143 | 0 | 19 | 50 | 0 |
| gpu_sensor.py | 175 | 213 | 17.8% | 64 | 99 | 12 | 0 | 0 |
| environment_sensor.py | 170 | 208 | 18.3% | 102 | 51 | 8 | 0 | 0 |
| **body_sensor.py** | 169 | 311 | 45.7% | **140** | 9 | 18 | 0 | 0 |
| file_blueprint.py | 167 | 179 | 6.7% | 159 | 0 | 8 | 0 | 0 |
| process_sensor.py | 161 | 188 | 14.4% | 156 | 0 | 5 | 0 | 0 |
| memory_sensor.py | 158 | 173 | 8.7% | 108 | 38 | 10 | 0 | 0 |
| voice_sensor.py | 152 | 224 | 32.1% | 134 | 0 | 18 | 0 | 0 |
| chassis_sensor.py | 132 | 146 | 9.6% | 57 | 51 | 15 | 115 | 0 |
| **test_body_sensor.py** | 129 | 195 | 33.8% | 0 | 0 | 0 | 0 | **129** |
| ocr_sensor.py | 111 | 154 | 27.9% | 92 | 0 | 18 | 0 | 0 |
| change_detector.py | 99 | 348 | 71.6% | 54 | 38 | 7 | 0 | 0 |
| window_sensor.py | 87 | 133 | 34.6% | 73 | 0 | 11 | 0 | 0 |
| disk_sensor.py | 71 | 85 | 16.5% | 66 | 0 | 5 | 0 | 0 |
| file_watcher.py | 59 | 196 | 69.9% | 58 | 0 | 1 | 0 | 0 |
| main.py | 59 | 70 | 15.7% | 55 | 0 | 4 | 0 | 0 |
| battery_sensor.py | 47 | 59 | 20.3% | 42 | 0 | 5 | 0 | 0 |
| registry.py | 30 | 117 | 74.4% | 21 | 0 | 7 | 0 | 0 |
| novelty.py | 14 | 117 | 88.0% | 10 | 0 | 4 | 0 | 0 |
| sensor_reading.py | 12 | 56 | 78.6% | 12 | 0 | 0 | 0 | 0 |
| tags.py | 4 | 52 | 92.3% | 3 | 0 | 1 | 0 | 0 |
| **合计** | **5,949** | **7,579** | **21.5%** | 3,499 | 1,606 | 355 | 520 | 129 |

### 3.3 `cognitive/` 与 `core/`

| 文件 | 未覆盖 | 性质 | 该不该补测 |
|---|---:|---|---|
| `cognitive/test_cognitive/*`（8 文件） | 297 | **包内测试，从未运行** | ❌ 移出生产包 / 解除 ignore |
| `cognitive/translator.py` | 50 | 业务逻辑（阈值区间翻译） | ✅ **P0**（且含死方法 `_fallback` 缺陷 L-2） |
| `cognitive/flask_adapter.py` | 32 | **未接线**（`sensor_server.py` 无启动入口） | ❌ 不测；接线或删除 |
| `cognitive/prompt_injector.py` | 25 | 业务逻辑（告警/建议编排） | ✅ **P0** |
| `cognitive/config.py` | 21 | 业务逻辑 + YAML 加载 | ✅ **P0** |
| `cognitive/templates.py` | 11 | 业务逻辑（模板渲染） | ✅ **P0** |
| `core/local_llm.py` | 34 | 🔴 **完全死代码** | ❌ **删除/标注** |
| `core/registry.py` | 5 | 异常防御 | ⚠️ 可豁免 |

---

## 4. `sensor/` 21% 里未覆盖的**是什么** — Top 15（含存活判定）

**回答"少数大模块 vs 普遍浅覆盖"**：**两者都是，但主要是普遍浅覆盖**。
33 个文件里 **20 个覆盖率 < 35%**；Top 15 合计 5,232 / 5,949 = **87.9%**。
有 3 个"巨坑"（`behavior_sensor` 539、`software_blueprint` 435、`port_sensor` 422 = 1,396 行 = 23.5%），
但砍掉它们仍剩 4,553 行分散在 30 个文件里。

> ✅ **Top 15 全部是活跃代码，无一死代码。** 这条否定了"先删死代码再补测"的乐观预期。

| # | 模块 | 未覆盖/有效 | 覆盖率 | 存活判定（证据） | 未覆盖主体性质 | 可测性 |
|---:|---|---:|---:|---|---|---|
| 1 | `behavior_sensor.py` | 539 / 645 | 16.4% | **活跃** `agent/learning/behavior_drift.py:27` + 自发现注册 `behavior` | 385 业务（磁盘/CPU 调度/内存行为解析）+ 111 外部进程 | ⚠️ 需 mock psutil+PowerShell+wmic |
| 2 | `software_blueprint.py` | 435 / 450 | 3.3% | **活跃** `sensor/body_sensor.py:19` 导入、`:77` 构造 | **383 外部二进制**（注册表/netsh/wmic 扫描） | ❌ 平台受限（Windows 注册表/CLI） |
| 3 | `port_sensor.py` | 422 / 446 | 5.4% | **活跃** 自发现注册 `port` | 262 业务（USB/COM/LPT/PS2/PCIe 解析）+ 129 平台方法 | ❌ 需真实设备拓扑 + WMI |
| 4 | `event_monitor.py` | 392 / 428 | 8.4% | **活跃** `sensor/body_sensor.py:22,207-213`、`lifecycle_manager.py:256` | 216 业务（事件循环/快照 diff）+ 150 外部 | ⚠️ 线程循环，需伪时钟 |
| 5 | `system_sensor.py` | 377 / 408 | 7.6% | **活跃** 自发现注册 `system` | 274 外部（PowerShell/WMI）+ 98 业务 | ❌ 平台受限 |
| 6 | `board_sensor.py` | 291 / 310 | 6.1% | **活跃** 自发现注册 `board` | 174 业务（主板解析）+ 151 平台/`_collect_windows` | ❌ WMI，Windows-only |
| 7 | `hardware_blueprint.py` | 280 / 292 | 4.1% | **活跃** `sensor/body_sensor.py:17` 导入、`:75` 构造 | **272 业务**（无 IO，纯构建蓝图 dict） | ✅ **可测**（需注入源 dict） |
| 8 | `hardware_file_sensor.py` | 273 / 349 | 21.8% | **活跃** 自发现注册 `hwfile` | 193 业务（驱动目录/INF 分类）+ 61 外部 | ⚠️ 需 tmp_path + mock `_which` |
| 9 | `peripheral_sensor.py` | 261 / 277 | 5.8% | **活跃** 自发现注册 `peripheral` | 147 业务（显示器/打印机/音频端点解析）+ 98 外部 | ❌ WMI |
| 10 | `counter_reader.py` | 236 / 236 | **0.0%** | **活跃** `gpu_sensor.py:62,246`、`memory_sensor.py:155,198`、`port_sensor.py:413,464`、`process_sensor.py:417` | **236 全部外部二进制**（PowerShell / nvidia-smi / COM） | ❌ 平台受限；**非死代码** |
| 11 | `network_sensor.py` | 225 / 254 | 11.4% | **活跃** 自发现注册 `network` | 152 业务（接口/IP 解析）+ 55 外部（netsh） | ⚠️ 需 mock `psutil.net_*` + netsh |
| 12 | `cpu_sensor.py` | 212 / 236 | 10.2% | **活跃** 自发现注册 `cpu` | 143 业务（频率/温度/缓存/perf counter）+ 50 平台 | ⚠️ 部分可测（psutil mock） |
| 13 | `gpu_sensor.py` | 175 / 213 | 17.8% | **活跃** 自发现注册 `gpu` | 64 业务 + 99 外部（pynvml/GPUtil/nvidia-smi） | ❌ 需 NVIDIA GPU |
| 14 | `environment_sensor.py` | 170 / 208 | 18.3% | **活跃** 自发现注册 `env` | 102 业务（运行时/环境变量/pip 反射）+ 51 外部 | ⚠️ 部分可测 |
| 15 | `body_sensor.py` | 169 / 311 | 45.7% | **活跃** `lifecycle_manager.py:253` | **140 业务（纯 Python 聚合/开关/过滤，零硬件）** + 18 异常防御 | ✅ **最易测**（fake sensor 注入） |

---

## 5. `sensor/` 敏感性与平台限制评估（**决定补测可行性的关键一节**）

### 5.1 依赖性质实测

`sensor/` 共 **27 处 `platform.system()` / `sys.platform` / `os.name` 分派**，覆盖 21 个文件。
`pyproject.toml` 的 Windows-only marker（实测行号）：

```
pyproject.toml:135  "pywin32>=305; sys_platform == 'win32'"
pyproject.toml:136  "pypiwin32>=223; sys_platform == 'win32'"
pyproject.toml:137  "comtypes>=1.4.0; sys_platform == 'win32'"
pyproject.toml:138  "wmi>=1.5.0,<1.6.0; sys_platform == 'win32'"
pyproject.toml:110  "GPUtil>=1.4.0,<1.5.0"
pyproject.toml:111  "pynvml>=13.0.1,<14.0.0"
pyproject.toml:109  "watchdog>=6.0.0,<7.0.0"
pyproject.toml:68   "opencv-python-headless>=4.8.0,<5.0.0"
pyproject.toml:32   "psutil>=7.2.2,<8.0.0"
```

### 5.2 平台限制清单（**受限制而"根本测不了"的部分，如实列出，不假装能测**）

| # | 限制 | 受影响模块（未覆盖行） | 本机（Windows） | CI（`ubuntu-22.04`） | 处置建议 |
|---|---|---|---|---|---|
| **PL-1** | **`wmi`（WMI COM）** — `sys_platform == 'win32'` | `board_sensor._collect_windows`、`chassis_sensor._collect_windows`、`port_sensor._collect_usb_windows/_com_ports/_lpt_ports/_ps2_ports`、`peripheral_sensor._collect_monitors/_printers/_storage_smart`、`memory_sensor._collect_memory_modules/_collect_memory_config`、`network_sensor._collect_adapter_info`、`behavior_sensor._get_windows_services`、`file_blueprint`、`hardware_blueprint` | 可测（需 mock WMI 返回） | ❌ **不可达** | 不追覆盖率；用 `@pytest.mark.skipif(sys.platform != "win32")` 标注 |
| **PL-2** | **`pywin32`（`win32gui`/`win32api`/`win32process`/`pythoncom`）** | `window_sensor.py`（87 miss，`win32gui` 取前台窗口）、`ocr_sensor.py` 截屏路径、`behavior_sensor._get_foreground_window/_is_workstation_locked`、`body_sensor.py:57-62` `pythoncom.CoInitialize()` | 可测 | ❌ **import 即失败**（`sensor/registry.py:145` 用 `except ImportError` 吞掉） | 同上；`window_sensor` 已有 `HAS_WIN32` 开关（`plugins/safety.py:78`） |
| **PL-3** | **`comtypes`（音频 COM）** | `counter_reader.get_audio_info/get_audio_service_status`（约 100 行） | 可测 | ❌ 不可达 | 同上 |
| **PL-4** | **外部 CLI：`powershell` / `wmic` / `netsh` / `Get-CimInstance` / `reg.exe`** | `counter_reader`（全 236 行）、`system_sensor`（274）、`software_blueprint`（383）、`environment_sensor._check_powershell/_query_service_status`、`behavior_sensor._get_disk_queue_depth/_get_idle_time/_get_scheduled_tasks`、`hardware_file_sensor._get_file_version_windows`、`network_sensor._collect_wifi_info`、`event_monitor._snapshot_devices_original/_optimized` | 部分可测（本机有 powershell）；**结果随机器/负载变化** | ❌ `wmic` 在 GitHub runner 上**已被移除**；`powershell` 需 `pwsh` | 若测，必须 monkeypatch `subprocess.run`，只能验证"解析逻辑"不能验证"取值" |
| **PL-5** | **NVIDIA GPU 驱动（`pynvml`/`GPUtil`/`nvidia-smi`）** | `gpu_sensor.py`（175）、`counter_reader.get_nvidia_smi/_safe_nvidia_val` | ⚠️ 依本机有无 N 卡 | ❌ **runner 无 GPU** | 只测"无 GPU 时的降级路径"（那是唯一在 CI 上真实执行的路径） |
| **PL-6** | **`tesseract` 二进制 + 屏幕/窗口捕获** | `ocr_sensor.py`（111 miss 中的 `capture_screen`/`recognize_text`/`capture_window`） | 可测（需装 tesseract） | ❌ 无显示、无二进制 | 只测 `_clean_text`（纯逻辑）与 config 读写 |
| **PL-7** | **音频设备 / 麦克风 / TTS** | `voice_sensor.py`（152 miss 中的 `TTSEngine`/`STTEngine`） | 依本机声卡 | ❌ 无音频设备 | 只测 `VoiceResult` 纯数据逻辑 |
| **PL-8** | **真实硬件拓扑**（USB/COM/PCIe/显示器/打印机/主板/机箱/电池） | `port_sensor`、`peripheral_sensor`、`board_sensor`、`chassis_sensor`、`battery_sensor`、`disk_sensor` | 值随机器变化 ⇒ **断言只能结构化（键/类型），不能断值** | 拓扑完全不同 | 断言必须写成"结构不变式"，否则本机绿、CI 红（或反之） |
| **PL-9** | **无电池机器** | `battery_sensor._collect_battery_info`（47 miss）；`body_sensor.collect_quick:571 psutil.sensors_battery()` 返回 `None` | 本机可能无电池 ⇒ 该分支**物理不可达** | 同 | 用 monkeypatch 伪造 `sensors_battery()` 返回值才可测 |
| **PL-10** | **文件系统事件（`watchdog`）** | `file_watcher.py`（59 miss 的 `start/stop/on_created/on_modified/on_deleted/on_moved`） | 可测但**依赖真实 FS 事件 ⇒ 慢且 flaky** | 可测 | `EventBuffer`/`PatternFilter` 纯逻辑直接测；事件回调用直接调用替代等待 |
| **PL-11** | **线程 / 轮询 / `sleep` 副作用** | `event_monitor._run_event_loop/_run_fallback_polling/start_health_check`、`voice_sensor`、`window_sensor`、`file_watcher.start` | 需停线程，易泄漏 | 同 | 测"启动/停止接口与状态位"，不测循环体；或注入伪时钟 |
| **PL-12** | **`# pragma: no cover` 豁免** | 三包实测 **0 处** | — | — | 无既有豁免可依赖 |

### 5.3 一句话结论

> `sensor/` 的 5,949 行未覆盖里，**约 2,100~2,400 行是"Windows/WMI/PowerShell/GPU/音频/硬件拓扑"强绑定的**，
> 在 `ubuntu-22.04` 上**永远不可能进入已执行状态**。
> ⇒ **在本机把覆盖率刷上去、却不给这些路径加平台标注，等于制造一个 CI 永远达不到的假指标。**
> 正确做法：**这些行如实标注"平台不可覆盖"并排除出目标达成口径**，只在可跨平台的部分补测。

---

## 6. 可执行的补测计划（按 价值/成本比 排序）

### 6.0 目标与计算基线

| 项 | 值 |
|---|---|
| 当前 9 包合计 | **74.06%**（100,629 / 135,872，本文口径）／ **74.13%**（100,637 / 135,752，任务书口径） |
| 三包未覆盖 | 6,424 行 |
| **其中"可跨平台补测"上限** | 约 **3,900 行**（= 业务/适配 3,859 − 平台受限部分 + 包内测试 426） |
| **其中"平台不可覆盖"** | 约 **2,100~2,400 行**（PL-1/2/3/4/5/6/7/8） |
| **其中"应删除/标注"** | **34 行**（`core/local_llm.py`） |

> **提升区间为估算，标注依据**：按"预计覆盖行数 / 135,752"折算百分点，未计入新测试自身引入的语句。

### 6.1 P0 — 立即做，零平台限制，纯逻辑（**预计 +717 行 ⇒ 74.13% → 74.66%，区间 74.4~74.7%**）

| # | 目标文件 | 当前覆盖率 | 预计提到 | 需测用例 | 需 mock / 夹具 | 平台限制 |
|---|---|---:|---:|---:|---|---|
| **P0-1** | `cognitive/config.py` + `translator.py` + `templates.py` + `prompt_injector.py` | 60/199 = **30.2%** | **~90%**（+120 行） | **~40** | 无（纯 dict/YAML，`tmp_path` 造配置） | ❌ 无 ｜ ✅ **已实施 → 实测 81.9%（+103），见 §7** |
| **P0-2** | `sensor/body_sensor.py`（开关/标签/过滤/分发） | 142/311 = **45.7%** | **~90%**（+140 行） | **~30** | 自造 `_FakeSensor`（有 `collect()` 的普通对象）；用 `BodySensor.__new__` 绕开 `discover()` 的硬件初始化 | ❌ 无 ｜ ✅ **已实施 → 实测 89.4%（+136），见 §7** |
| **P0-3** | `sensor/registry.py`（自发现判定逻辑） | 87/117 = **74.4%** | **~97%**（+27 行） | **~14** | monkeypatch `os.listdir` + `importlib.import_module`；伪模块对象 | ❌ 无 |
| **P0-4** | `sensor/change_detector.py` 的 8 个 `_diff_*` + `_sanitize_reg_value` | 249/348 = **71.6%** | **~95%**（+80 行） | **~22** | 纯 snapshot dict 夹具（新增/删除/改值/空） | ❌ 无 |
| **P0-5** | `sensor/sensor_reading.py` + `tags.py` + `novelty.py` | 206/285 = **72.3%** | **~95%**（+50 行） | **~18** | 无 | ❌ 无 |
| **P0-6** | **处置（非补测）**：把 `cognitive/test_cognitive/`（297 行）移入 `tests/`；把 `sensor/test_body_sensor.py`（195 行）移出生产包 | 0% | — | — | 需改 `pytest.ini`（**本任务禁改 ⇒ 遗留项 L-4**） | ❌ 无 |
| **P0-7** | **删除/标注** `core/local_llm.py`（34 行死代码） | 0% | — | **0**（不写测试） | — | ❌ 无 |

**P0 收益拆解（估算）**：`+120 + 140 + 27 + 80 + 50 = 417` 行
；若 P0-6 落地（297 行测试被真实执行）再 **+297** ⇒ **合计 +714 行**
⇒ `74.13% → (100,637+714)/135,752 = 74.66%`（**区间 74.4%~74.7%**）。

> **已实施部分的实测对账（P0-1 + P0-2）**：估算 +120/+140 = **+260**；
> **实测 +103/+136 = +239**（`sensor/tags.py` 顺带 +1 ⇒ 总净增 **+240**）。
> 估算偏高 8%，原因：`translator.py` 的 `_fallback()`（11 行）与
> `flask_adapter.py`（32 行）**不是"漏测"而是未接线/未调用**，测不到也不该测。
> ⇒ 未实施的 P0-3/P0-4/P0-5 估算（+157）**按同样比例下修后约 +145**。
> 完整实测见 §7.2。

### 6.2 P1 — 活跃但需 IO mock（**预计 +900~1,300 行 ⇒ → 75.3%~76.1%**）

| # | 目标文件 | 未覆盖 | 需 mock | 平台限制 |
|---|---|---:|---|---|
| P1-1 | `sensor/file_watcher.py`（`EventBuffer`/`PatternFilter` 纯逻辑 + `FileWatcher` 状态机） | 59 | `tmp_path`；事件回调直接调用，不等真实 FS 事件 | ❌ 无 |
| P1-2 | `sensor/disk_sensor.py` + `battery_sensor.py` + `memory_sensor.py` 的 psutil 路径 | 71+47+158 | monkeypatch `psutil.disk_partitions/disk_io_counters/sensors_battery/virtual_memory/swap_memory` | ❌ 无（PL-9 需伪造电池） |
| P1-3 | `sensor/process_sensor.py`（进程快照/Top-N 解析） | 161 | monkeypatch `psutil.process_iter` 返回伪 `Process` 对象 | ❌ 无 |
| P1-4 | `sensor/environment_sensor.py` 的 `_collect_runtime/_collect_environment/_collect_modules/_collect_api_availability` | ~100 | monkeypatch `sys.modules` / `os.environ` / `shutil.which` | ⚠️ 部分 |
| P1-5 | `sensor/cpu_sensor.py` 的 `_collect_usage/_frequency/_times/_stats/_cache` | ~140 | monkeypatch `psutil.cpu_*` | ⚠️ WMI 分支除外 |
| P1-6 | `sensor/network_sensor.py` 的 `_collect_interfaces/_collect_ip_config/_collect_io/_collect_connections/_collect_hostname` | ~150 | monkeypatch `psutil.net_if_addrs/net_if_stats/net_io_counters/net_connections` | ⚠️ `netsh` 分支除外 |
| P1-7 | `sensor/hardware_blueprint.py` 的 `_build_blueprint`（**272 行纯业务、零 IO**） | 280 | 注入伪"源读数 dict 列表" | ❌ 无 |
| P1-8 | `sensor/hardware_file_sensor.py` 的目录/INF 分类 | ~130 | monkeypatch `_which`/`os.walk`，`tmp_path` 造假驱动目录 | ⚠️ 部分 |
| P1-9 | `sensor/body_sensor.py::collect_quick` | ~20 | monkeypatch `psutil` | ❌ 无 |
| P1-10 | `sensor/ocr_sensor.py` 的 `_clean_text` + config 读写；`voice_sensor.py` 的 `VoiceResult` | ~60 | `tmp_path` | ❌ 无（PL-6/7 部分） |

⇒ P1 合计约 **+900~1,300 行**（保守用 900）⇒ `74.66% + 900/135752 = 75.32%`。

### 6.3 P2 — 平台受限：**建议不追覆盖率**（本机可测 ≈ +2,400 行，CI 上 0 收益）

| # | 目标 | 未覆盖 | 为什么不做 |
|---|---|---:|---|
| P2-1 | `software_blueprint.py`（383 外部二进制行） | 435 | Windows 注册表/netsh/wmic 扫描；CI 不可达 |
| P2-2 | `port_sensor.py` | 422 | USB/COM/LPT/PS2/PCIe 真实拓扑 + WMI；断值必 flaky |
| P2-3 | `event_monitor.py` | 392 | 线程事件循环 + wmic/udev；需伪时钟 |
| P2-4 | `system_sensor.py` | 377 | PowerShell/WMI |
| P2-5 | `behavior_sensor.py` | 539 | psutil + PowerShell + wmic；41 个方法 |
| P2-6 | `board_sensor.py` / `chassis_sensor.py` | 291 + 132 | `_collect_windows` 占 52% / 87% |
| P2-7 | `peripheral_sensor.py` | 261 | WMI |
| P2-8 | `counter_reader.py` | 236 | PowerShell + nvidia-smi + COM；**在 CI 上 236 行恒为 0** |
| P2-9 | `gpu_sensor.py` | 175 | 无 GPU；只测降级路径 |
| P2-10 | `window_sensor.py`（87）、`ocr_sensor` 截屏、`voice_sensor` 音频（~200） | ~290 | `win32gui` / 显示 / 声卡 |

**P2 的正确处置（替代"补测"）**：
1. 给这些路径加**显式平台标注**（`@pytest.mark.skipif(sys.platform != "win32", reason="PL-1 WMI")`），
   或对纯 Windows 分支加 `# pragma: no cover  # Windows-only（PL-1）`；
2. 在 `docs/closeout/COVERAGE_SCOPE_20260919.md` 的达成口径里**声明一个"平台不可覆盖行"下界**；
3. **不要**为了让本机数字好看而给它们写"只调一次不验行为"的测试。

### 6.4 提升合计（**估算，标注依据**）

| 阶段 | 累计覆盖行 | 9 包覆盖率（估） | 说明 |
|---|---:|---:|---|
| 现状 | 100,637 | **74.13%** | 任务书口径 |
| **P0** | 101,351 | **74.66%** | +714（含 P0-6 的 297） |
| **P0 + P1** | 102,251 | **75.32%** | +900（P1 保守下界） |
| P0 + P1 + P2（仅本机可测） | 104,651 | **77.09%** | +2,400，**CI 上 P2 归零 ⇒ CI 实际仍 ≈75.3%** |

⇒ **诚实结论**：把这三个包"补到 80%"在本仓的当前 CI 拓扑下**做不到**，因为约 **17.5% 的未覆盖行
（2,100~2,400 / 6,424）是平台不可达**。**可跨平台的真实上限约为 75.3%~76.1%**（P0+P1）。

---

## 7. 实施的 P0 第一批（**实测，不是估算**）

### 7.1 交付物

| 文件 | 用例数 | 覆盖目标 | 平台限制 |
|---|---:|---|---|
| `tests/unit/test_cognitive_engine.py` | **68** | `cognitive/{config,translator,templates,prompt_injector}.py` | ❌ 无（纯逻辑） |
| `tests/unit/test_sensor_body_switch.py` | **62** | `sensor/body_sensor.py`（聚合/开关/标签/分发）+ `sensor/tags.py` | ❌ 无（`psutil` 已 monkeypatch） |
| **合计** | **130** | | **全部通过**：`130 passed in 3.23s` |

**测试运行命令（可复现）**：

```powershell
$env:PYTHONUTF8="1"; $env:PYTHONIOENCODING="utf-8"
python -m pytest tests/unit/test_sensor_body_switch.py tests/unit/test_cognitive_engine.py `
  -q --no-header -p no:cacheprovider -p no:randomly --timeout=120
```

### 7.2 实测覆盖率变化

**测量方法**（为什么这样算即可靠）：
新增测试**只增加**已覆盖行、**不减少任何一行**（不修改任何生产文件）⇒

```
净增 = |新测试覆盖的行集合  \  权威基线已覆盖的行集合|
```

基线 = `_ci_logs/coverage_auth/.coverage`（2026-09-19 19:45，`tests/unit` 全量 9 包）；
增量 = 仅运行上面两个新测试文件、同样按 9 包 `--cov=` 采集；
两边都用**同一个** `coverage.parser.PythonParser` 语句集过滤。
脚本：`_ci_logs/coverage_gap_20260919/_gap_after_delta.py`。

| 口径 | 基线 | 含新增测试 | 净增 |
|---|---:|---:|---:|
| **9 包合计** | 100,629 / 135,872 = **74.0616%** | 100,869 / 135,872 = **74.2383%** | **+240 行 / +0.177 个百分点** |
| `sensor` | 1,630 / 7,579 = 21.51% | **1,767 / 7,579 = 23.31%** | +137 行 / +1.81pp |
| `cognitive` | 60 / 496 = 12.10% | **163 / 496 = 32.86%** | +103 行 / +20.76pp |
| `memory` / `planning` / `persona` / `core` / `lifetrace` / `utils` / `agent` | — | 不变 | 0 |

**逐文件净增明细**

| 文件 | 净增行 | 基线覆盖 | 有效行 | 基线→实测 |
|---|---:|---:|---:|---|
| `sensor/body_sensor.py` | **+136** | 142 | 311 | 45.7% → **89.4%** |
| `cognitive/translator.py` | **+46** | 10 | 60 | 16.7% → **93.3%** |
| `cognitive/prompt_injector.py` | **+25** | 17 | 42 | 40.5% → **100.0%** |
| `cognitive/config.py` | **+21** | 19 | 40 | 47.5% → **100.0%** |
| `cognitive/templates.py` | **+11** | 11 | 22 | 50.0% → **100.0%** |
| `sensor/tags.py` | +1 | 48 | 52 | 92.3% → 94.2% |

> **`cognitive/` 源码口径**：剔除包内 `test_cognitive/` 的 297 行后
> （199 行源码），实测 **163 / 199 = 81.9%**（基线 60/199 = 30.2%）。
> 包级 32.86% 之所以仍低，是因为那 297 行测试代码还在分母里且从不执行 —— 这正是 **L-4** 要处置的。

### 7.3 🔴 实施过程中发现的第 3 个真实缺陷（新增遗留项 L-10）

写 `test_sensor_body_switch.py` 时，我原本按"设计意图"断言
"传感器返回非 `SensorReading` 时该结果被忽略"，**测试直接失败**——真实行为是抛 `AttributeError`：

```
sensor\body_sensor.py:530: in collect_all      self._apply_tags(results)
sensor\body_sensor.py:442: in _apply_tags      if not r.tags:
E   AttributeError: 'str' object has no attribute 'tags'
```

**归因**：`collect_all` 的 `try/except`（`body_sensor.py:510-517`）**只包住 `sensor.collect()`**，
而 `self._apply_tags(results)`（`:530`）在 `try` 之外；`_apply_tags` 自己的 `try`（`:443-446`）
只包住 `tags_mod.get_tags(...)`，**不包住 `r.tags` 属性读取**（`:442`）。
⇒ 一个返回裸值（如 `return self._summary_str`）的传感器会让**所有其它传感器的采集结果一起丢失**，
与"单传感器失败隔离"的设计意图相悖。

**处理**（遵循"不写假测试、不擅自改生产行为"）：测试**锁定当前行为**并写明原因，
使修复此 bug 时该断言会**显式失败**而不是被静默忽略。已登记为 **L-10**。

### 7.4 未实施的部分（如实标注）

| 项 | 状态 | 原因 |
|---|---|---|
| P0-3 `sensor/registry.py` | **未实施** | 预算耗尽（本次已交付 P0-1、P0-2 两个模块） |
| P0-4 `sensor/change_detector.py` `_diff_*` | **未实施** | 同上 |
| P0-5 `sensor_reading.py` + `tags.py` + `novelty.py` 补测 | **部分**（`tags.py` 顺带 +1） | 同上 |
| P0-6 处置包内测试（426 行） | **未实施** | **硬约束禁改 `pytest.ini`** ⇒ 必须移交（L-4） |
| P0-7 删除/标注 `core/local_llm.py` | **未实施** | 删除生产文件超出"补测计划"授权范围 ⇒ 移交（L-11） |
| P1 / P2 全部 | **未实施** | 本任务明确"仅做 P0 里价值最高的 1–2 个模块" |

### 7.5 L-2 结案：`translate()` 噪声的真实根因**不是**"缺规则"（**本节推翻上文归因**）

> **本节记录一次典型的"归因错一层"事故，并给出实测推翻过程。**
> 上文 §2.4b / §7.3 把噪声归因于"`translate()` 6 个兜底出口返回硬编码串、`_fallback()` 从未被调用"。
> 该描述**现象正确、成因不完整**。真正起决定作用的是**类型不兼容**。

**根因（实测，非推测）**

`translate()` 首行是 `if not isinstance(reading, dict): return "传感器读数未识别"`，
而 `BodySensor.collect_all()` 返回的是 **`SensorReading` 对象列表**：

```
BodySensor().collect_all()        -> 652 条，类型分布 {'SensorReading': 652}
translate_all(同 652 条)          -> 652 条全部为"传感器读数未识别"（100%）
首条实际字段：sensor_name='behavior_disk_total_read'
              value=68150101  unit='次'  description='磁盘总读取次数'   ← 字段完好，只是装在对象上
```

⇒ **该守卫把所有真实读数拦在函数入口，规则查找与 thresholds 遍历从未执行**。
"规则覆盖率低"只是**第二层**原因（真实 645 个不同 `sensor_name`，默认仅 5 条规则），
只在类型修好之后才会显现。两层必须同时修，这也解释了为何单修任一层都无效。

**为什么补测没有发现它（这才是本事故最该记的部分）**

`tests/unit/test_cognitive_engine.py`（68 用例）与 `tests/test_cognitive_boundary.py`（25 用例）
**全部通过**，因为两者的夹具**一律传 dict**，而生产路径传**对象** ——
典型的**"测试夹具冒充生产"**。当时据此判定"修复已生效"，用真实数据复测才暴露。

⇒ 纪律补充：**任何"修复已生效"的结论，都必须用 `BodySensor().collect_all()` 的真实产物复测一次，
不能只看单测。** 已新增 `tests/unit/test_translator_object_readings.py` 专门覆盖对象路径，
并用"对象与等价 dict 必须同结果"作为不变量断言。

**修复内容**（提交 `400b76f4`）

| 文件 | 改动 |
|---|---|
| `cognitive/translator.py` | 新增 `_coerce()`：dict 原样返回、带 `__dict__` 的对象提取**最小字段集**（只搬真实存在的键，不注入默认值）、其它类型返回 `None` |
| `cognitive/translator.py` | 无规则命中改走 `_fallback()`；NaN 显式判掉（原 `except TypeError` 兜不住 NaN） |
| `cognitive/translator.py` | `_fallback()` 不再重复描述已含的值（`_already_carries`，6 类真实形态）；`_clean_unit()` 不把**类型名**当单位 |
| `sensor/body_sensor.py` | 新增模块级 `_normalize_reading()` 在**唯一收集点**归一化；`_apply_tags()` 兼容 dict/对象且单条失败只影响自己（对应 **L-10，一并结案**） |
| `tests/unit/test_translator_object_readings.py` | 新增 36 用例，覆盖此前零覆盖的对象路径 |

**实测效果（口径：真实生产路径）**

生产接线为 `agent/digital_life_persona.py::_build_body_status`：
`[r.to_dict() for r in readings]` → `inj.inject(...)` → **`len > 800` 时截断**。
故必须按"截断后"口径评估，否则会高估影响面：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| `translate_all` 未识别占比 | **100%**（652/652） | **0.15%**（1/651） |
| 截断的 800 字符内 | **全是"传感器读数未识别"** | **20 行真实读数、0 噪声** |
| 值/单位重复渲染 | 有（如 `'用户态 CPU: 11.0%: 11.0%'`） | **0 条** |
| 类型名冒充单位 | 3 条（`'…: Truebool'`） | **0 条** |

**顺带修正的其他错误归因（同一事故的连带）**

- 上文若出现"99.8% 因为无匹配规则"的表述，**应读作**"656 条曾被判未识别，其**直接原因**是类型守卫"。
- `_fallback()` 的 `value` 默认值是 `""`，而 `translate()` 取 `0`，二者**不一致但不可达**
  （`translate()` 总会先补 0）。已由 `test_missing_value_is_normalized_to_zero_only_for_rule_lookup` 钉死，
  避免日后"顺手统一默认值"时无从判断。

---

## 8. 遗留项（移交，本任务不擅自修）

| ID | 遗留项 | 证据 | 建议归属 |
|---|---|---|---|
| **L-1** | `scripts/generate_coverage_workflow.py:122` 硬编码 `--cov=__PACKAGE__` 单包占位符；`coverage-ci.yml:64` / `ci-cd.yml:139` 仍是单包口径 ⇒ 覆盖率口径存在**第二真相源**，重新生成会退化 | `pyproject.toml` 已声明 9 包 | 与 `read_declared_packages()`（`run_authoritative_coverage.py:86`）对齐 |
| **L-2** | ✅ **已结案（2026-09-19，提交 `400b76f4`）** —— 原描述：`cognitive/translator.py:88-93` 的 `_fallback()` **从未被调用**（`translate()` 6 个兜底出口全返回硬编码 `"传感器读数未识别"`）；`pytest.ini:52` 的 `--ignore` 掩盖了 2 个相关测试失败（实测 2 failed / 46 passed）。**真实根因是类型不兼容**（对象撞 `isinstance(dict)` 守卫），见 **§7.5** | §2.4b、**§7.5** | ✅ 已修实现（非修断言） |
| **L-3** | 覆盖率汇总表按 `coverage.xml` 相对 `filename` 归包，在多 `--cov=` 下**跨包撞名**，静默丢弃 `core/registry.py`(72 行) 与两个 `__init__.py` | §2.5 | 改为按 `.coverage` 绝对路径归包 |
| **L-4** | `pytest.ini:52 --ignore=cognitive/test_cognitive` + `testpaths = tests` ⇒ 426 行测试代码（`cognitive/test_cognitive/` 297 + `sensor/test_body_sensor.py` 129 未执行语句）留在分母里且从不执行 | §2.4 | **本任务硬约束禁改 `pytest.ini`** ⇒ 必须移交 |
| **L-5** | `sensor/registry.py:125-128` 的自发现未排除 `test_*.py` ⇒ **生产启动时会 import 一个 unittest 模块**（实测 `discover()` 期间 import 了 `sensor.test_body_sensor`） | §2.4、`_gap_discover.json` | 一行修复：把 `test_body_sensor` 加入排除名单，或改判据为 `not fname.startswith(("_", "test_"))` |
| **L-6** | `sensor_server.py`（含 `cognitive/flask_adapter.py`）**无任何启动入口**，属未接线资产 | `start_yunshu.bat:21` 是唯一启动链 | 接线或删除 |
| **L-7** | `sensor/main.py`（70 行）未接线（唯一引用是自身 docstring） | §2.1 | 接线或删除 |
| **L-8** | 三包 **`# pragma: no cover` 为 0 处** ⇒ 平台不可覆盖行没有豁免标注，会持续压制分母 | §5.2 PL-12 | 配合 §6.3 处置建议 1 |
| **L-9** | `tests/unit/test_digital_life_comprehensive.py` 等**全部 patch 掉 `BodySensor`** ⇒ 感知层在测试里是"全 mock 真空"，任何真实回归都无法被捕获 | `tests/unit/test_digital_life_comprehensive.py:291,316,...`（实测 20+ 处） | 至少补 1 个 BodySensor 真构造的冒烟测试。**注**：本次 P0-2 已用"`__new__` + 手工 `_registry`"覆盖聚合语义，但**仍未**覆盖 `__init__` → `discover()` 的真构造路径 |
| **L-10** | ✅ **已结案（2026-09-19，提交 `400b76f4`）** —— 原描述：`sensor/body_sensor.py:530` 的 `_apply_tags(results)` 在 `try` 之外、`:442` 的 `r.tags` 读取未包 try ⇒ 任一传感器返回非 `SensorReading` 时整次 `collect_all()` 抛 `AttributeError`、其余结果全部丢失。处置：新增 `_normalize_reading()` 在唯一收集点归一化 + `_apply_tags()` 兼容 dict/对象且单条失败只影响自己（详见 §7.5） | §7.3、**§7.5** | ✅ 已修 |
| **L-11** | `core/local_llm.py`（34 行、0% 覆盖、0 引用）是**完全死代码**，但一直留在覆盖率分母里 | §2.3 | 删除，或加显式"未接线"注释并从分母排除。**本任务无删除生产文件的授权 ⇒ 移交** |
| **L-12** | `build/lib/sensor/test_body_sensor.py` 与 `.tmp-s1109/*/sensor/test_body_sensor.py` 存在 `sensor/test_body_sensor.py` 的**陈旧副本** | 本次全盘检索实测 | 构建残留，建议清理（不属本次范围） |

---

## 附：证据文件清单（可复现）

> 全部证据已移入 **gitignored** 的 `_ci_logs/coverage_gap_20260919/`（`.gitignore:172 _ci_logs/`），
> 以免污染工作区。**下列脚本均以"从仓库根 `C:\Users\Administrator\agent` 运行"为前提**
> （脚本内用的是相对路径）；若要复跑，先 `cd` 到仓库根。
> 运行环境：`$env:PYTHONUTF8="1"`，解释器 `Python 3.12`。

| 文件（`_ci_logs/coverage_gap_20260919/` 下） | 内容 |
|---|---|
| `_ci_logs/coverage_auth/.coverage` | 权威覆盖率原始数据（**输入**，不在本目录） |
| `_gap_parse3.py` / `_gap_cov_raw.json` | 绝对路径归包的逐文件 valid/cover/miss/miss_lines |
| `_gap_survival2.py` / `_gap_survival2.log` / `_gap_survival.json` | 静态调用方普查（绝对导入；**含已知假阴性**，见 §2.0 警告） |
| `_gap_discover.py` / `_gap_discover.log` / `_gap_discover.json` | **实跑** `SensorRegistry.discover()`：16 个注册结果 + 30 个被 import 模块 + 33 个模块 import 探测 |
| `_gap_struct.py` / `_gap_struct.log` | 逐模块类/方法/IO 特征 |
| `_gap_nature.py` / `_gap_nature.json` | 未覆盖行的性质归因 |
| `_gap_final.py` / `_gap_final.log` / `_gap_final_verdict.json` | 修正后的存活判定 + 平台方法行归因 |
| `_gap_tbs3.py` / `_gap_tbs3.log` | `sensor/test_body_sensor.py` 66 行"仅来自 import"的最小复现 |
| `_gap_cog.log` | `cognitive/test_cognitive` 手跑结果（**46 passed / 2 failed**） |
| `_gap_after.coverage` + `_gap_after_delta.py` / `.log` / `.json` | §7.2 的**实测增量**（基线 vs 含新增测试） |
| `_gap_t1.log` / `_gap_t2.log` / `_gap_t12.log` | 新增测试的运行日志（130 passed） |
| `_gap_parse*.log` / `_gap_xml*.log` | §2.5 归包 bug 的对账过程（含 `coverage.xml` 的 `<package>` 名称清单） |
