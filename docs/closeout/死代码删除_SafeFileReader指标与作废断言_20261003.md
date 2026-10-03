# 死代码删除：SafeFileReader 5 个空指标 + 两个作废脚本的失效断言（2026-10-03）

> **派单**：业主已批准的两项删除 —— ①「3.改」去掉 `/metrics` 上 5 个恒为 0 的空名字；
> ②「4.删」删掉两个作废脚本里本来就跑不过的断言。
> **约束遵守**：全程 **未执行** `git add/commit/push/checkout/stash`；
> 未触碰 `agent/circuit_breaker.py`、`agent/monitoring/business_metrics.py`、`monitoring/**`、`CHANGELOG.md`；
> 未跑全量 pytest；未重启/停止正在运行的应用。

---

## 0. 一句话结论

- **任务 A 完成**：`yunshu_safe_file_reader_*` 5 个名字（含 `_bucket/_count/_sum/_created` 派生后缀）
  已从默认 `REGISTRY` / `/metrics` 上**彻底消失**（实测 0 行），
  两文件同改（`agent/monitoring/prometheus.py` + `agent/server_routes/routes_logging.py`），
  `python -c "import app_server"` 仍 `IMPORT_OK`，指定 4 组测试全绿（4 passed / 37 passed）。
- **任务 B 完成**：`scripts/deploy_automation.py` 删掉 3 类失效检查（+ 1 类被任务 A 连带作废的检查），
  头部改为诚实声明；`scripts/deployment_drill.py` **没有可执行检查项**（从来只有冻结记录），
  故只改头部 + 加运行时横幅，冻结正文一字未动；两脚本 `py_compile` 通过。

---

## 1. 任务 A：两文件同改（删了什么 / 为什么 / 怎么恢复）

### 1.1 `agent/server_routes/routes_logging.py`（去掉哪几个名字）

**先复核零调用点**（`git grep` 结果，5 个名字在文件内各自只出现在 import 块里）：

```
record_error: 1 hits                -> 52: record_error, record_encoding_fallback,
record_encoding_fallback: 1 hits    -> 52: record_error, record_encoding_fallback,
record_read_duration: 1 hits        -> 53: record_read_duration, set_loaded_history_count, set_invalid_ratio
set_loaded_history_count: 1 hits    -> 53: record_read_duration, set_loaded_history_count, set_invalid_ratio
set_invalid_ratio: 1 hits           -> 53: record_read_duration, set_loaded_history_count, set_invalid_ratio
```

**删掉的名字（只这 5 个，import 里其它名字保留）**：
`record_error`、`record_encoding_fallback`、`record_read_duration`、`set_loaded_history_count`、`set_invalid_ratio`。
**保留**：`PrometheusMetricsExporter`（该文件仍在用，见 `_create_prometheus_exporter()`）。

实际 diff：

```diff
-from agent.monitoring.prometheus import (
-    PrometheusMetricsExporter,
-    record_error, record_encoding_fallback,
-    record_read_duration, set_loaded_history_count, set_invalid_ratio
-)
+from agent.monitoring.prometheus import PrometheusMetricsExporter
+# 【2026-10-03 · 业主批准删除】原 import 括号里还导入了 5 个 SafeFileReader 指标发射函数
+#   （record_error / record_encoding_fallback / record_read_duration /
+#     set_loaded_history_count / set_invalid_ratio）：本文件内**零调用点**（纯未使用导入），
+#   却因此让 agent/monitoring/prometheus.py 里那 5 个恒为 0 的指标定义删不掉。
+#   本轮两文件同改：这 5 个名字已从本 import 移除，对应指标定义与函数也已从 prometheus.py 删除
+#   ⇒ /metrics 上不再出现 yunshu_safe_file_reader_*（含 _bucket/_count/_sum 等派生后缀）。
+#   恢复：git show <本次提交>^:agent/server_routes/routes_logging.py
+#   （必须与 prometheus.py 同一次恢复：只恢复一处会让另一边失衡 —— 只有定义没人 import ⇒ 空名字重现；
+#     只有 import 没有定义 ⇒ app_server 装配期 ImportError。）
```

### 1.2 `agent/monitoring/prometheus.py`

| 删除点 | 内容 | 为什么 | 怎么恢复 |
|---|---|---|---|
| 5 个指标定义 | `yunshu_safe_file_reader_errors_total` / `_encoding_fallbacks_total` / `_read_duration_seconds` / `_loaded_history_count` / `_invalid_ratio` | SafeFileReader 在非测试代码里 **0 个调用方** ⇒ 这些名字在 `/metrics` 上恒为 0，是"空名字"不是证据；依赖它们的 9 条告警已于 2026-10-02 删除 | `git show <本次提交>^:agent/monitoring/prometheus.py`（与 `routes_logging.py` **同一次**恢复） |
| 5 个发射函数 | `record_error` / `record_encoding_fallback` / `record_read_duration` / `set_loaded_history_count` / `set_invalid_ratio` | 上一轮"不能删"的唯一原因是 `routes_logging.py:50-54` 仍 import 它们（未使用导入）；本轮两处同一次改掉，ImportError 风险消失 | 同上 |
| 只服务于它们的注释 | 原 612-622 行的"标签口径差异（1 标签 vs 3 标签 / 谁先注册谁说了算）"整段 | 它描述的是本文件这两个同名 Counter 与 `utils/file_reader.py` 的定义冲突；定义删了，注释即成死文 | 同上（随附于恢复段落） |
| 模块 docstring | 原第 5 行 `集成了 SafeFileReader 指标。` 改为"该说法已失效 + 指向下方说明块" | 不加这行，文件头会继续宣称集成了已删除的指标 | 同上 |
| 段落注释 | 原 542-559 的"**不能删** / 要删必须两文件同改（独立立项，本次不做）"改写为"**已于 2026-10-03 两文件同改删除（业主批准）**"块，含【删了什么】【为什么删】【保留了什么】【怎么恢复】四段 | 派单明确要求把上一轮那段注释改写成"已删除 + 恢复命令" | 同上 |

**明确保留（没有顺手删）**：
- `_NoopMetric` / `_NoopCounter` / `_NoopHistogram` / `_NoopGauge` 与 `_safe_counter` / `_safe_histogram` / `_safe_gauge` 三个工厂：
  它们**还被别的指标共用**（实测：`_safe_counter` 另有 9 处、`_safe_histogram` 另有 4 处、`_safe_gauge` 另有 4 处
  —— `skill_match_*` / `yunshu_intent_layer_*` / `context_assembler_*` / `route_depth*` / `cache_hit_ratio` /
  `zero_recall_total` / `tool_selected_total` / `llm_tokens_total` / `llm_cost_usd_total`）。
- 本文件**没有 `__all__`**（`git grep __all__` 命中 0），故不存在"同步清 `__all__`"这一步。

**文件规模变化**：`prometheus.py` 1310 → 1262 行。

### 1.3 `utils/file_reader.py`：**一字未动**（按要求）

它本体与**自己内联**的那套同名指标（`utils/file_reader.py:103/114/120/127/133`）保留；
`tests/unit/test_safe_file_reader_alerts.py` 直接 import 它的 `_metrics_errors` 等私有指标对象，继续全绿。
关键事实（写进注释块，防后来人误判）：**那套内联指标只在 `import utils.file_reader` 的进程里注册**，
而生产代码 0 处 import 它 ⇒ 服务进程的 `/metrics` 上不会出现；所以"删了 prometheus.py 却还能在别处看到这些名字"
只在 import 了 file_reader 的脚本/测试进程里成立，与线上 `/metrics` 无关。

---

## 2. 任务 A 验证（命令 + 输出）

### 2.1 `import app_server` 冒烟

命令：`python -c "import app_server; print('IMPORT_OK')"`

结果：`exit=0`，stdout 尾部 `IMPORT_OK`（装配日志正常，含 `[Routes] 诊断端点注册`）。
> 说明：`import app_server` 会跑完整初始化并打印大量日志，属预期。已确认它**没有**改动工作区文件
> （`data/system_prompt.txt` / `data/system_prompt_config.json` 的 mtime 仍是 12:49:26，早于本次会话）。

### 2.2 断言 `REGISTRY` 里已无 `yunshu_safe_file_reader_*`（含派生后缀）

命令（PowerShell here-string 管道进 python，避免引号地狱）：

```powershell
@'
import app_server  # 装配期会 import routes_logging
from prometheus_client import REGISTRY, generate_latest

text = generate_latest().decode("utf-8")
hits = [ln for ln in text.splitlines() if "yunshu_safe_file_reader" in ln]
print("=== /metrics(默认 REGISTRY) 里 yunshu_safe_file_reader* 行数:", len(hits))
for h in hits:
    print("   ", h)

names = sorted({n for n in REGISTRY._names_to_collectors if "safe_file_reader" in n})
print("=== REGISTRY._names_to_collectors 里含 safe_file_reader 的 key:", names)
assert not hits, "FAIL: /metrics 上仍有 yunshu_safe_file_reader_* 样本行"
assert not names, "FAIL: REGISTRY 里仍有 safe_file_reader 相关收集器"
print("REGISTRY_CLEAN_OK")
'@ | python -
```

输出（`exit=0`）：

```
=== /metrics(默认 REGISTRY) 里 yunshu_safe_file_reader* 行数: 0
=== REGISTRY._names_to_collectors 里含 safe_file_reader 的 key: []
REGISTRY_CLEAN_OK
```

> 判据写法：直接对 `generate_latest()` 全文做子串匹配，因此 `_total/_bucket/_count/_sum/_created` 等
> **派生后缀一并覆盖**（旧口径里那 13 个名字现在全部为 0 行）。
> 两个 `assert` 都未触发 —— 若仍有残留，脚本会以非 0 退出。

### 2.3 指定测试

```
python -m pytest tests/unit/test_safe_file_reader_alerts.py tests/unit/test_false_alarm_resistance.py tests/unit/test_prometheus_alert_trigger.py -q
```
```
collected 4 items
tests\unit\test_safe_file_reader_alerts.py ....                          [100%]
======================== 4 passed, 5 warnings in 3.95s ========================
```
> **没有因为"名字不存在"而挂**，因此**不需要**把任何断言改成"已删除"。
> 需要说清的一点：这 3 个文件里只有 `test_safe_file_reader_alerts.py` 有 pytest 可收集的 `test_*` 函数（4 条）；
> 另两个文件**只有 `scenario_*/main()`**，pytest 收集 0 条（"collected 4 items" 即 4+0+0）——
> 它们此前只是在被**当作脚本直接运行**时才会启停服务去读 `/metrics`。

```
python -m pytest tests/unit/test_business_metrics_tracking.py -q
```
```
collected 37 items
tests\unit\test_business_metrics_tracking.py ........................... [ 72%]
..........                                                               [100%]
======================== 37 passed, 1 warning in 2.96s ========================
```

**额外护栏**（因本轮动过这两个文件的**文档字符串**，顺带跑一次）：

```
python -m pytest tests/unit/test_business_metrics_tracking.py tests/unit/test_dashboard_metric_names.py -q
-> 39 passed, 1 warning in 57.30s
```

### 2.4 顺带修掉的"仍在断言这 5 个名字存在"的地方

| 文件 | 改法 | 为什么 |
|---|---|---|
| `scripts/verify_business_metrics_registration.py` | **删掉 A-4/A-5 两段**（import `utils.file_reader` → 读文件 → 断言 `yunshu_safe_file_reader_loaded_history_count` 在默认 REGISTRY 上有样本行），原位留注释说明 | 该断言在**本脚本进程内**永远为真（它自己 import 了 file_reader），却会被读成"线上 /metrics 有这个指标"——**恰好吃反**；删除后不再计入 PASS/FAIL |
| `scripts/export_test_data_csv.py` | 文件头加"⚠ 输出的是 2026-06-10 历史留档数据 + 5 指标/9 告警已删"；导出完成后再打印一条同样的提醒。**数据值一字未改** | 它是**数据导出器**（不是断言），值属历史留痕；按规则"能标注的就标注"，不动数值 |
| `scripts/verify_alert_trigger.py` | 头部加"判据已失效"横幅；`/metrics` 段的"告警条件未满足"后面补一行"该指标名已被删除 ⇒ 恒为 0 是预期结果，本条判定已失效" | 它原来把"查不到/为 0"读成"可能历史加载没生效"，会误导运维 |
| `scripts/full_alert_verify.py` | 同上（docstring + 运行横幅 + "可能原因"那行改成"指标已删除，0 是预期值"） | 派单说"至少包括 verify_alert_trigger.py"，这个同族脚本同一病症，顺手对齐 |
| `scripts/verify_skill_retrieval_metrics.py` | G 段标题由"agent/monitoring/prometheus.py:601 既有定义"改为"prometheus.py 那份定义已删除，下面测的是 utils/file_reader.py **自内联**的同名 Histogram（只在 import 它的进程里注册）" | 该处**行号引用已失效**，会把读者引到一个空位置 |
| `docs/OBSERVABILITY_OPERATION_MANUAL.md`（在用手册） | 3 条 SafeFileReader PromQL **删除**，换成"已于 2026-10-03 整组删除 + 现在查不到是预期结果 + 恢复命令指向 closeout" | 在用手册不该继续教人查已不存在的指标 |
| `docs/deploy_automation`/`deployment_drill` 见任务 B | — | — |
| `tests/unit/test_dashboard_metric_names.py` | docstring 里"恒为 0 的空名字"举例加【2026-10-03 更新】说明（那 5 个名字已删，只留在 file_reader 内联定义里） | 纯注释，避免该守卫文件的举例与事实脱节 |
| `tests/unit/test_false_alarm_resistance.py`、`tests/unit/test_prometheus_alert_trigger.py` | docstring 加"判据已失效 + pytest 收集 0 条"说明；**逻辑与断言一字未改** | 它们直接读 `/metrics` 判断这 5 个名字，虽然 pytest 不收集，但被当脚本跑时会得出反向结论 |

**按规则"不动"的（历史留档 / 报告类）**：`docs/archive/`（无命中）、`docs/closeout/*`（含 `monitoring_evidence_20261002/live_metric_names.txt` 那份 186 名实测档案）、
`docs/audit_skill_governance/Q5_funnel_baseline.md`、`docs/deploy_checklist_safe_file_reader.md`、`docs/deployment_confirmation.md`、
`docs/final_test_report_safe_file_reader.md`、`docs/monitoring_dashboard.html`、`docs/ops_quick_manual.md`、
`docs/perf/可观测性实测.md`、`docs/risk_brief.md`、`docs/safe_file_reader_alert_rules.md`、
`monitoring/prometheus.yml`（注释里的恢复说明，属"另一个专项不可碰的 `monitoring/**`"）、`reports/*`。

---

## 3. 任务 B：两个作废脚本

### 3.1 `scripts/deploy_automation.py`

**删掉的检查/断言（4 类，含任务 A 连带作废的 1 类）**

| # | 原检查 | 位置 | 为什么删 |
|---|---|---|---|
| ① | `check_alerts()` 整个"阶段2 告警规则检查"：断言 `monitoring/alerts.yml` 里含 9 条 `SafeFileReader*` 规则名（`SafeFileReaderFileNotFound`/`FileTooLarge`/`EncodingFallback`/`AllEncodingsFailed`/`HighInvalidRatio`/`ConsecutiveParseFailures`/`HistoryLoadFailed`/`HistoryLoadEmpty`/`SlowRead`） | 原 162-184 + `main()` 两处调用 | 这 9 条随 `monitoring/alerts_safe_file_reader.yml` 于 2026-10-02 删除，`alerts.yml` 内**零命中** ⇒ 该阶段必然失败 |
| ② | 代码变更检查里"assert `app_server.py` 含 `_load_chat_history_from_file` + `SafeFileReader`" | 原 `check_code_changes()` 第 2 项 | 该函数**早已不存在**（历史读取改走 `agent/jsonl_history.py`）⇒ 本来就跑不过 |
| ③ | `check_metrics()` 整个"阶段5 监控指标检查"：断言 `agent/monitoring/prometheus.py` 里含 5 个 `yunshu_safe_file_reader_*` 名字 | 原 234-255 + `main()` 两处调用 | 正是任务 A 删掉的那 5 个名字 ⇒ 删完必然失败（属"仍在断言这 5 个名字存在"的地方，按派单规则改） |
| ④ | `backup_files()` 里已移除的 `utils/prometheus_exporter.py` 一项（上一轮已改，本轮只在头部说明中保留记录） | — | 该薄包装已作为零引用死代码删除 |

**保留未动**：`check_file_exists(utils/file_reader.py)`、`app_server.py 含 DEFAULT_REGISTRY`、
`file_reader.py 含 max_size_mb=10 / 编码降级链 / 'yunshu_safe_file_reader'`（**这条仍然为真**：file_reader 内联指标还在）、
`check_rollback_scripts()`、`check_backups()`、`run_functional_tests()`、`rollback_drill()`、`backup_files()`。
阶段编号同步重排（2/5 移除后：回滚脚本→阶段2、数据备份→阶段3、功能测试→阶段4、回滚演练→阶段5）。

**删掉的函数原位留了什么**：两处都留了"【2026-10-03 删除】原 check_xxx 断言了什么 / 为什么必然失败 / 恢复：`git show <本次提交>^:scripts/deploy_automation.py`"注释块。

**头部改成什么**（原文）：

```python
"""SafeFileReader 自动化部署脚本（**已作废的留档**）

本脚本为 SafeFileReader 时代留档，检查项已随该功能下线移除（2026-10-03），
**不要再据此判断部署状态**。

仍在的阶段（编号已随删除重排）：
1. 代码变更验证
2. 回滚脚本验证
3. 数据备份验证
4. 功能测试
5. 回滚演练

用法: ...
⚠ 【2026-10-03 已作废】本脚本是 SafeFileReader 上线专用的演练/自动化脚本，**不要再据此判断部署状态**：
  ① 原"告警规则检查"（check_alerts）**已整段删除**：……
  ② 原"代码变更检查"里"app_server.py 含 _load_chat_history_from_file + SafeFileReader"一条**已删除**：……
  ③ 原"监控指标检查"（check_metrics）**已整段删除**：……
  ④ 【2026-10-03】它备份的 utils/prometheus_exporter.py……另外"功能测试"阶段引用的
     tests/unit/test_safe_file_reader.py **本仓已不存在** ⇒ 该阶段仍会失败（属留档，未动）。
  保留仅为历史记录。原因见 docs/closeout/监控死规则与陈旧看板清理_20261002.md §3（A-1）与
  docs/closeout/死代码删除_SafeFileReader指标与作废断言_20261003.md。
"""
```

**实跑证据**（`--check-only`，不停服务、不改文件）：

```powershell
$env:PYTHONIOENCODING='utf-8'; python scripts/deploy_automation.py --check-only
```
```
[SUCCESS] ✅ Prometheus 指标注册修复 / ✅ 文件大小限制（10MB） / ✅ 编码降级链 / ✅ Prometheus 指标上报
[SUCCESS] 阶段2: 回滚脚本检查 -> ✅ Shell/PowerShell 回滚脚本 + monitoring 参数 全通过
[WARNING] 阶段3: 数据备份检查 -> 四个 *.bak_* 均未找到（❌ 失败）
部署检查结果汇总：代码变更检查 ✅ 通过 / 回滚脚本检查 ✅ 通过 / 数据备份检查 ❌ 失败
总计: 2/3 通过
```
> 删改后脚本**能完整跑到底**（无 ImportError/NameError），原来两个必然失败的阶段（告警规则、监控指标）已消失；
> 剩下"数据备份检查"失败是真实状态（工作区里没有任何 `.bak_*` 备份文件），不是删改引入的。
> ⚠ 环境提示（**与本次改动无关**）：不加 `PYTHONIOENCODING=utf-8` 时，该脚本会在打印 `✅`（U+2705）时抛
> `UnicodeEncodeError: 'gbk' codec can't encode character '\u2705'` —— 这是 Windows GBK 控制台的老问题，
> 未在本轮改动范围内处理。

### 3.2 `scripts/deployment_drill.py`

**结论：本脚本没有任何可执行的检查项可删。** 它 imports 只有 `os/sys/json/datetime`，
函数只有 `generate_drill_record()`（把一份**硬编码的 2026-06-10 演练记录**写成 `reports/deployment_drill_*.{json,md}`）
与 `generate_md_report()`（渲染 Markdown）。它对已删除的规则文件/告警名**没有断言**，
那些名字（`SafeFileReaderFileNotFound` 等）出现在**冻结的记录文本**里 —— 按派单"冻结的演练记录文本不要删"，
**原文照留、一字未改**。

因此按"自我描述必须诚实"的要求，只做了两件事：

1. **文件头重写**为（要点）：

```python
"""SafeFileReader 上线流程演练脚本（**已作废的留档**）

本脚本为 SafeFileReader 时代留档，检查项已随该功能下线移除（2026-10-03），
**不要再据此判断部署状态**。

【本脚本现在还剩什么】
  它**没有任何可执行的检查/断言**（从来就不检查仓库状态），只把 2026-06-10 那次上线演练的记录
  **硬编码打印/落盘**为 reports/deployment_drill_*.{json,md}。……
【记录里哪些内容当前已不存在（只说明，不改正文）】
  · monitoring/alerts_safe_file_reader.yml 及其 9 条 SafeFileReader* 告警：已于 2026-10-02 删除；
  · utils/prometheus_exporter.py（薄包装）：已于 2026-10-03 作为零引用死代码删除；
  · 5 个 yunshu_safe_file_reader_* 指标：已于 2026-10-03 两文件同改删除；
  · app_server.py 里的 _load_chat_history_from_file：早已不存在。
  ⇒ 运行本脚本会生成一份"12/12 通过、100% 成功"的报告，那是 2026-06-10 的状态快照，**不是**今天的部署结论。
"""
```

2. **运行时横幅**（`generate_drill_record()` 开头，只加 print，不动记录内容）：

```
======================================================================
⚠ 【已作废 · 2026-10-03】本脚本为 SafeFileReader 时代留档，检查项已随该功能下线移除。
   下面输出的全部内容是 2026-06-10 的**冻结演练记录**（历史留痕）：
   其中的告警规则文件 / 9 条 SafeFileReader* 告警 / 5 个 yunshu_safe_file_reader_* 指标
   当前均已不存在 ⇒ **不要再据此判断部署状态**。
======================================================================
```

**实跑证据（在 `%TEMP%` 的副本里跑，避免往仓库 `reports/` 塞新文件）**：

```powershell
$tmp = Join-Path $env:TEMP 'dsh_drill_check'   # 复制脚本到临时目录后执行
python (Join-Path $tmp 'scripts/deployment_drill.py')
```
输出前 6 行即上面那段横幅，随后正常生成 `deployment_drill_20261003_130821.json/.md`；临时目录已删除，仓库零污染。

### 3.3 任务 B 验证

```powershell
python -m py_compile scripts/deploy_automation.py scripts/deployment_drill.py
-> COMPILE_EXIT=0

git grep -n 'alerts_safe_file_reader\|_load_chat_history_from_file\|SafeFileReaderFileNotFound' scripts/
```
结果（**符合预期**：0 条"活断言"，只剩注释与冻结记录）：

```
scripts/deploy_automation.py:22  ① 原"告警规则检查"（check_alerts）**已整段删除**：…… alerts_safe_file_reader.yml   ← 删除说明注释
scripts/deploy_automation.py:25  ② 原"代码变更检查"里"…… _load_chat_history_from_file + SafeFileReader"一条**已删除**  ← 删除说明注释
scripts/deploy_automation.py:138 # 【2026-10-03 删除】原此处断言 app_server.py 里含 "_load_chat_history_from_file"……  ← 删除说明注释
scripts/deploy_automation.py:170 #   monitoring/alerts_safe_file_reader.yml 于 2026-10-02 一起删除，alerts.yml 内零命中      ← 删除说明注释
scripts/deployment_drill.py:14   · monitoring/alerts_safe_file_reader.yml 及其 9 条 SafeFileReader* 告警：已于 2026-10-02 删除  ← 头部说明
scripts/deployment_drill.py:18   · app_server.py 里的 _load_chat_history_from_file：早已不存在……                            ← 头部说明
scripts/deployment_drill.py:107  {"规则名称": "SafeFileReaderFileNotFound", ...}                                          ← 冻结的演练记录正文（按要求保留）
scripts/export_test_data_csv.py:14  · 其中的 SafeFileReader* 告警名…… 已于 2026-10-02 删除                                ← 头部说明
scripts/export_test_data_csv.py:253 "告警名称": "SafeFileReaderFileNotFound"                                              ← 冻结的历史测试数据（保留）
scripts/verify_alert_trigger.py:14  · 9 条告警：随 monitoring/alerts_safe_file_reader.yml 删除                            ← 新增说明
```

---

## 4. 改动文件清单（本轮我改的 13 个；未 git add）

```
agent/monitoring/prometheus.py                    | 122 ++++++-------------
agent/server_routes/routes_logging.py             |  15 ++-
docs/OBSERVABILITY_OPERATION_MANUAL.md            |  20 ++--
scripts/deploy_automation.py                      | 116 +++++----------
scripts/deployment_drill.py                       |  35 +++++--
scripts/export_test_data_csv.py                   |  14 +++
scripts/full_alert_verify.py                      |  15 ++-
scripts/verify_alert_trigger.py                   |   9 +-
scripts/verify_business_metrics_registration.py   |  38 ++------
scripts/verify_skill_retrieval_metrics.py         |   4 +-
tests/unit/test_dashboard_metric_names.py         |   6 +-
tests/unit/test_false_alarm_resistance.py         |  10 ++
tests/unit/test_prometheus_alert_trigger.py       |   9 ++
```

> 工作区里另有**不属于本轮**的改动，我**没有碰**：`agent/monitoring/business_metrics.py`、`app_server.py`、
> `tests/unit/test_circuit_breaker_state_publication.py`（未跟踪）—— 属另一个专项的工作线，
> 它们是在本会话进行中出现的（会话开始时 `git status` 只有 `data/system_prompt*.{txt,json}`）。

---

## 5. 我没把握 / 没动的（如实列出）

1. **`scripts/deploy_automation.py` 的"功能测试"阶段仍然必然失败**：它引用的 `tests/unit/test_safe_file_reader.py`
   在本仓**不存在**（实测 `Test-Path` = False）。它**不在派单的三类**里，故按"保留脚本其余部分"未动，
   只在头部 ④ 里写明"该阶段仍会失败（属留档）"。若你希望一并删掉，我可以再改一次。
2. **`check_code_changes()` 里那条 `utils/file_reader.py 含 'yunshu_safe_file_reader'` 我保留了**：
   它是**字面前缀**断言且**当前仍为真**（file_reader 内联指标还在），不属于"跑不过的断言"。
   如果你认为"SafeFileReader 时代的一切检查都该删"，这条也可以去掉 —— 请给一句确认。
3. **两个遗留测试文件（`test_false_alarm_resistance.py` / `test_prometheus_alert_trigger.py`）的判据逻辑我没改**：
   pytest 对它们收集 0 条（只 import 模块），派单要求的 pytest 命令**全绿、没有因名字不存在而挂**，
   所以我按"最小改动"只补了 docstring 说明。**若把它们当脚本手动跑**（会启停服务），
   `scenario_2/3` 仍会打印"失败"——那是过期判据，不是线上问题。
4. **`docs/closeout/`、`docs/perf/`、`reports/`、`docs/audit_skill_governance/` 等历史文档里的旧名字一律未改**（按派单的留档规则）；
   其中 `docs/closeout/监控清理_evidence_20261002/live_metric_names.txt` 记录的仍是**当时**的 186 个名字，含那 5 个 ——
   这是**证据档案**，刻意保持原样。
5. **`monitoring/prometheus.yml:52,58`** 里还有关于 `yunshu_safe_file_reader_*` 的注释（恢复说明），
   按硬性约束"不要碰 `monitoring/**`"**未动**；若另一个专项结束后需要，我可以在下一轮把那两句改成"指标已删除"。
6. **`test_business_metrics_tracking.py` 的 37 passed 是在另一专项正改 `business_metrics.py` 期间跑出来的**
   （当时该文件已是 M 状态）。本轮我没有碰它；如需绝对干净的证据，建议在那条工作线收口后复跑一次。
7. **未做**：全量 pytest、`/metrics` 的线上实抓（应用在跑，未重启）、`git add/commit`。
   因此"线上 `/metrics` 已无这些名字"的证据是**进程内默认 REGISTRY 复现**（第 2.2 节），
   要让运行中的服务生效需重启该应用 —— 这一步留给业主安排。

---

## 6. 恢复手册（一页速查）

| 想恢复什么 | 命令 |
|---|---|
| prometheus.py 的 5 指标定义 + 5 发射函数 | `git show <本次提交>^:agent/monitoring/prometheus.py` |
| routes_logging.py 的那 5 个 import 名 | `git show <本次提交>^:agent/server_routes/routes_logging.py` |
| deploy_automation.py 的 check_alerts / check_metrics | `git show <本次提交>^:scripts/deploy_automation.py` |
| 9 条 SafeFileReader 告警规则文件 | `git show <A-1 删除提交>^:monitoring/alerts_safe_file_reader.yml` + `rule_files` 项 + 两份 compose 挂载 |
| **注意** | 指标定义与 `routes_logging` 的 import **必须同一次恢复**：只恢复前者 ⇒ 空名字重现；只恢复后者 ⇒ `app_server` 装配期 `ImportError` |
| **更重要的前提** | 恢复这些只有先**真接线**（让非测试代码真的调用 SafeFileReader）才有意义，否则只是把恒为 0 的空名字挂回去 |
