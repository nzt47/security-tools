# SET-REG · 开关注册表横切收口（17 条未登记 env）

- **卡号**：SET-REG（横切收口卡）
- **基线 HEAD**：`5c9ace10a4ca4bb96860db3a48debf9ddcf496bf`（工作区含 20+ 张卡未提交改动）
- **环境**：Python 3.12.0 系统解释器（未用 `venv/`），Windows
- **本卡文件范围**：`agent/settings/registry.py`（唯一代码改动）；本报告
- **未改**：`tests/unit/test_settings_registry.py`（一字未动 —— 没有断言过时，无需修改）
- **零 LLM 预算**：本卡全程未调用任何模型

---

## ① 改前 / 改后差分原始输出

### 改前（红）

命令：`python -m pytest tests/unit/test_settings_registry.py -q`

```
tests\unit\test_settings_registry.py ..............................F...F [ 62%]
================== FAILURES ===================
________ TestMechanicalZeroGap.test_zero_gap_between_scan_and_registry ________
tests\unit\test_settings_registry.py:177: in test_zero_gap_between_scan_and_registry
    assert gap.missing == [], f"代码读到但注册表未覆盖：{gap.missing}"
E   AssertionError: 代码读到但注册表未覆盖：['CP_HTTP_CONCURRENCY_GATE', 'CP_HTTP_MAX_CONCURRENT',
    'CP_HTTP_MAX_QUEUE', 'CP_HTTP_QUEUE_TIMEOUT', 'CP_ROUTE_EVENT_SINK_DIR', 'CP_ROUTE_EVENT_SINK_ENABLED',
    'CP_SKILL_DESC_FROM_FILE_TRACK', 'CP_TOOL_CONCURRENCY_GATE', 'CP_TOOL_LEVEL_BUCKET',
    'CP_TOOL_MAX_CONCURRENT', 'LLM_ADAPTER_CONNECT_TIMEOUT', 'LLM_ADAPTER_MAX_RETRIES',
    'LLM_ADAPTER_READ_TIMEOUT', 'SKILLS_INDEX_MAIN_TRACK', 'SKILLS_INDEX_MAIN_TRACK_PATH',
    'SKILL_VECTOR_FALLBACK_MIN_COVERAGE', 'YUNSHU_PROMPT_VOLATILE_TAIL']
E   assert ['CP_HTTP_CON...ENABLED', ...] == []
E     Left contains 17 more items, first extra item: 'CP_HTTP_CONCURRENCY_GATE'
___________ TestMechanicalZeroGap.test_extracted_scale_is_disclosed ___________
tests\unit\test_settings_registry.py:195: in test_extracted_scale_is_disclosed
    assert len(gap.registered_names) == len(gap.extracted_names)
E   AssertionError: assert 432 == 449
=========================== short test summary info ===========================
FAILED tests/unit/test_settings_registry.py::TestMechanicalZeroGap::test_zero_gap_between_scan_and_registry
FAILED tests/unit/test_settings_registry.py::TestMechanicalZeroGap::test_extracted_scale_is_disclosed
======================== 2 failed, 54 passed in 43.66s ========================
```

> 控制台捕获时中文被本机代码页打成乱码，以上按原始 UTF-8 字节还原；**缺口清单与 `432 == 449` 为逐字原文**。
> 主控给的 `2 failed, 54 passed / 缺口恰 17` 与本次独立复现**完全一致**。

### 改后（绿）

命令：`python -m pytest tests/unit/test_settings_registry.py -q`

```
tests\unit\test_settings_registry.py ................................... [ 62%]
.....................                                                    [100%]
========================= 所有测试通过！✓ =========================
通过: 56  失败: 0  跳过: 0
============================= 56 passed in 51.18s =============================
```

**四条硬判据的实测值**（独立探针 `probe.py`，非测试内部输出）：

| 判据 | 改前 | 改后 |
|---|---|---|
| `gap.missing` | 17 条 | `[]` |
| `gap.extra`（反向：登记了没人读） | `[]` | `[]` |
| `len(registered_env_names())` | 432 | **449** |
| `len(extracted_names)` | 449 | 449（未变） |
| `len(R.all_specs())` | 482 | 499（+17） |

两条红用例（`test_zero_gap_between_scan_and_registry` / `test_extracted_scale_is_disclosed`）**同时转绿**；反向守卫 `test_registry_has_no_phantom_switches` 仍绿（`extra == []`）。

---

## ② 17 条取证表（默认值全部逐字读自代码，无一条猜测）

`owner` 口径 = **读取点所在模块**；`needs_restart` = 读取时机（构造时读一次 ⇒ True；每次调用都读 ⇒ False）。
判定"C1/C2/G1-B/F3-1 引入"的依据是**源码注释里的卡标签**（非文档推测），见最后一列。

| # | env 名 | 读取点（文件:行号） | 真实默认值 | 类型 | 语义 | 引入卡（源码内标签） | 登记为 |
|---|---|---|---|---|---|---|---|
| 1 | `CP_HTTP_CONCURRENCY_GATE` | `agent/rate_limiter.py:1275`（装配 `app_server.py:419`） | **True** | bool | HTTP 入口并发闸门总开关；置 0 ⇒ 不装 WSGI 中间件，回到无闸门现状 | C2（`agent/tools/__init__.py:22` 「【C2 背压】」；C2.md §6） | orchestration / A / True / needs_restart |
| 2 | `CP_HTTP_MAX_CONCURRENT` | `agent/rate_limiter.py:1279` | **8** | int | 同时**执行**的请求上限（waitress threads=16 的一半） | C2 | orchestration / A / 8 / needs_restart |
| 3 | `CP_HTTP_QUEUE_TIMEOUT` | `agent/rate_limiter.py:1280` | **20.0** | float | 闸门处最大等待秒数，超时 ⇒ 429 `SERVER_BUSY_TIMEOUT` | C2 | orchestration / A / 20.0 / needs_restart |
| 4 | `CP_HTTP_MAX_QUEUE` | `agent/rate_limiter.py:1281` | **0** | int | 等待队列上限；**0 = 不限**（只靠 queue_timeout 兜底） | C2 | orchestration / A / 0 / needs_restart |
| 5 | `CP_TOOL_CONCURRENCY_GATE` | `agent/rate_limiter.py:1305`（装配 `agent/tools/__init__.py:30`） | **True** | bool | 工具层并发闸门；置 0 ⇒ 只判速率，`max_concurrent` 回构造默认 100 | C2 | orchestration / A / True / needs_restart |
| 6 | `CP_TOOL_MAX_CONCURRENT` | `agent/rate_limiter.py:1307` | **16** | int | 同时**执行**的工具调用上限 | C2 | orchestration / A / 16 / needs_restart |
| 7 | `CP_TOOL_LEVEL_BUCKET` | `agent/rate_limiter.py:1309` | **True** | bool | L2/L3 确认级低容量桶；置 0 ⇒ 只留分类桶 | C2 | orchestration / A / True / needs_restart |
| 8 | `CP_ROUTE_EVENT_SINK_ENABLED` | `app_server.py:294-297`（装配 `app_server.py:2159`） | **True** | bool | 路由事件 jsonl sink 开关；0/false/no/off ⇒ 不装配且摘掉已装 handler | B2（`[B2-ROUTE-SINK-BEGIN]` 块；B2.md §6「后重启服务」） | observability / A / True / needs_restart |
| 9 | `CP_ROUTE_EVENT_SINK_DIR` | `app_server.py:346` | **None**（未设 / 空串 ⇒ 用 LokiClient 自带的 `<repo>/data/logs`） | str（path） | 落盘目录覆盖；路径项 | B2 | observability / **C** / None / path 校验 / needs_restart |
| 10 | `CP_SKILL_DESC_FROM_FILE_TRACK` | `agent/skills_mgmt/registry.py:44-47`（消费者 :246） | **True** | bool | `description` 是否**文件轨优先**；置 0 ⇒ 回退主轨文案（`description_zh` 仍读文件轨） | G1-B（`registry.py:35` 「【G1-B/M3 逃生开关】」） | skills / A / True / hot |
| 11 | `LLM_ADAPTER_CONNECT_TIMEOUT` | `agent/model_router/adapters.py:161`（常量 :57） | **5.0** | float | LLM 客户端**建连**超时（秒）；非法/非正值回退默认 | C1（`adapters.py:41` 「【C1 修(2)】」） | orchestration / A / 5.0 / hot |
| 12 | `LLM_ADAPTER_READ_TIMEOUT` | `agent/model_router/adapters.py:162`（常量 :58） | **45.0** | float | LLM 客户端**读**超时（秒，同时用作 write/pool）；旧行为吃 SDK 默认 600s | C1 修(2) | orchestration / A / 45.0 / hot |
| 13 | `LLM_ADAPTER_MAX_RETRIES` | `agent/model_router/adapters.py:170`（常量 :59） | **1** | int | LLM 客户端重试上限；可设 0 = 不重试 | C1 修(2) | orchestration / A / 1 / hot |
| 14 | `SKILLS_INDEX_MAIN_TRACK` | `agent/skills_mgmt/index_cache.py:98` | **True** | bool | 技能索引主轨（`data/skills_mgmt.json`）补位总开关；置 0 ⇒ 只服务文件轨 | C1（`index_cache.py:54` 「【C1 修(5)】」） | skills / A / True / needs_restart |
| 15 | `SKILLS_INDEX_MAIN_TRACK_PATH` | `agent/skills_mgmt/index_cache.py:115-119` | **None**（未设/纯空白 ⇒ 由代码算 `repo_path` 父目录 / `skills_mgmt.json`） | str（path） | 主轨文件路径显式覆盖；路径项 | C1 修(5) | skills / **C** / None / path 校验 / needs_restart |
| 16 | `SKILL_VECTOR_FALLBACK_MIN_COVERAGE` | `agent/skills_mgmt/vector_adapter.py:93-101`（常量 :88，消费者 :551） | **0.5** | float | 降级落盘向量库的覆盖率阈值；低于阈值必须显式标 degraded；取值夹到 [0,1] | C1（`vector_adapter.py:80` 「【C1 裁决2】」） | skills / A / 0.5 / `_range_validator(0,1)` / hot |
| 17 | `YUNSHU_PROMPT_VOLATILE_TAIL` | `agent/system_prompt_manager.py:79-82` | **True** | bool | 易变尾簇是否搬到请求最后一条消息（延长可缓存前缀）；0/false/no/off/disable ⇒ 旧顺序 | F3-1（`system_prompt_manager.py:71` 指向 `docs/…/F3-1.md`） | orchestration / A / True / hot |

### 默认值的**独立**证据（不止"我读了那一行"）

| env 组 | 独立证据（另一条机械判据） |
|---|---|
| `CP_HTTP_*` | `tests/unit/test_backpressure.py:524` 断言 `(gate.max_concurrent, gate.queue_timeout, gate.max_queue) == (8, 20.0, 0)`；`:532-536` 断言非法值回退 `(8, 20.0)`；`:515-517` 断言 `=0` ⇒ 不装闸门 |
| `CP_TOOL_*` | `test_backpressure.py:549-554` 断言默认 `concurrency_gate is True / _level_buckets is True / max_concurrent == 16`；`:538-544` 断言关闸门后 `max_concurrent == 100` |
| `CP_ROUTE_EVENT_SINK_ENABLED` | `tests/unit/test_route_log_sink.py:201-207` 的解析表（`"0/false/no/off" ⇒ False`、`"1/true/yes/空串" ⇒ True`）与「未设即装配」用例 |
| `CP_SKILL_DESC_FROM_FILE_TRACK` | `tests/unit/test_skill_description_single_source.py:162-166`（=0 ⇒ 回退主轨；默认 = 文件轨优先） |
| `LLM_ADAPTER_*` | `tests/unit/test_retrieval_silent_failures.py:501-517` 断言 `max_retries == 1`、`timeout.connect == 5.0`、`timeout.read == 45.0` |
| `SKILLS_INDEX_MAIN_TRACK` | `test_retrieval_silent_failures.py:639-641`（=0 ⇒ 主轨补位关闭；未设 ⇒ 主轨只读技能可被索引） |
| `SKILL_VECTOR_FALLBACK_MIN_COVERAGE` | `test_retrieval_silent_failures.py:837-851`（非法值回退默认 0.5；可配到 0.95） |
| `YUNSHU_PROMPT_VOLATILE_TAIL` | `tests/unit/test_prompt_volatile_tail_order.py`（16 例：默认新顺序 + =0 必回旧顺序） |

⇒ **17 条中 15 条有"代码 + 单测"双重证据**；仅 **#9 / #15** 两条（`None` 型路径覆盖开关）**只有代码证据、无单测断言默认值**（见 ④）。

---

## ③ 改了哪些文件 / 几行

| 文件 | 改动 | 行号 |
|---|---|---|
| `agent/settings/registry.py` | **+108 行，-0 行**（`git diff --numstat` = `108  0`；单 hunk `@@ -2403,0 +2404,108 @@`，即整段都是本卡的） | 新增段 **2404–2511**（插在 `_REGISTRY_ROWS` 末尾、`CP_DIGESTION_JUDGE_DOTENV` 之后、闭合 `]` 之前） |
| `docs/audit_skill_governance/SETREG.md` | 新建（本报告） | — |

- 未改动 `tests/unit/test_settings_registry.py`（**没有断言过时**，无需修改）。
- 未触碰其它任何文件；17 条的读取点所在文件（`agent/rate_limiter.py`、`app_server.py`、`agent/model_router/adapters.py`、`agent/skills_mgmt/*`、`agent/system_prompt_manager.py`）**只读不改**。
- 沿用既有写法：`_a()/_c()` 助手 + 六分类 + A/B/C 三级 + `Validator("path")`/`_range_validator(...)`，**未新造任何助手或分类**。
- 风险分级口径：本批全是**限流 / 日志落盘 / 检索轨 / 提示词装配**的运行时开关，均不触碰安全边界（`C2.md §2.2` 原文："限流是性能判据不是安全判据"）⇒ 除两条路径项按 C 级只读脱敏外一律 **A 级，无 B 级**。

---

## ④ 我没搞清楚的条目（如实声明，不编）

**没有"完全没搞清楚"的条目** —— 17 条的名字、读取点、默认值、类型、语义、引入卡都有源码证据。但有两处**证据强度较弱**，逐条列出：

1. **#9 `CP_ROUTE_EVENT_SINK_DIR`**：默认值 `None` 由代码逐字可读
   （`app_server.py:346`：`(os.environ.get(_ROUTE_EVENT_SINK_DIR_ENV) or "").strip() or None`），
   但**没有任何单测断言"未设时是 None"**（`test_route_log_sink.py` 的用例都显式传 `log_dir`）。
   ⇒ 置信度：高（表达式无分支歧义），但**无第二来源**。登记为 `None`（"默认由代码逻辑决定"），
   **没有**编造 `<repo>/data/logs` 这个真实路径字符串 —— 该路径由 `LokiClient` 内部决定，
   写进注册表就等于把一个本表算不出的值当成默认值展示。

2. **#15 `SKILLS_INDEX_MAIN_TRACK_PATH`**：同上形态 —— 默认 `None`，代码 `index_cache.py:115-119`
   （`explicit = os.environ.get(...)`；空/空白 ⇒ 走 `Path(file_store.repo_path).resolve().parent / "skills_mgmt.json"`）。
   全仓**没有**测试设置过这个 env（只在 `index_cache.py:56` 的注释与 C1.md 里出现）。
   ⇒ 置信度：高，但同样**无单测第二来源**。

3. **一处不确定的判定口径（不是证据不足，是分级口径问题）**：`CP_HTTP_CONCURRENCY_GATE` /
   `CP_TOOL_CONCURRENCY_GATE` 我定为 **A 级**（依据 `C2.md §2.2` 的"限流是性能判据不是安全判据"
   与本表 B 级定义）。若主控认为"关闭闸门 = 关闭即降低防护"应归 B 级（B 级会要求二次认证 + 双人确认），
   **只需把这两行的 `_a` 改成 `_b`**（两行改动），语义与默认值不受影响 —— 我保留 A 的理由已写进源码注释段（`registry.py:2415-2418`）。

---

## ⑤ 未验证项与残留风险

### 未验证

1. **未跑全量 pytest**（铁律 3）。本卡实跑的定向回归见 §回归证据；其它卡域的测试未回归。
2. **未在真实 UI 上目视确认**（`yunshu-ui/` 不在本卡范围，也未启服务、未占用 5678 端口）。
   登记的 category/risk 会改变开关中心的分类计数与可编辑性（A 级可编辑、C 级只读），
   只做了 `to_public_dict()` 级的结构核验（分类/风险/type/validator/effect 均合法），未看渲染结果。
3. **`.env` 载入路径未验证**：即"把 `CP_HTTP_MAX_CONCURRENT=12` 写进 `.env` 后是否真的进 `os.environ`"
   —— 与 `B2.md §7.1` 登记的同一未验证项（`.env` 含真实凭据，本卡不改、不读其值）。
4. **`needs_restart` 未做端到端实测**：判定依据是"读取时机"的源码审计（构造/启动时读一次 vs 每次调用读）。
   "改了 env 不重启确实无效"这一点**未实测**。
5. **未验证这两类开关在其它卡正在改的文件里的最终形态**：读取点行号取证于当前工作区（含 20+ 张卡未提交改动）。
   若某张卡后续**删掉**其中一条读取点，反向守卫 `test_registry_has_no_phantom_switches` 会立刻变红
   （这正是它的用途），但**行号会漂移** —— 本报告的 `文件:行号` 只在当前工作区快照下有效。

### 残留风险 / 交回主控

1. **扫描器盲区（本卡未修，不在文件范围）**：`CP_HTTP_GATE_EXEMPT` 是**真实的**同族读取点
   （`agent/rate_limiter.py:1282`，`_env_csv("CP_HTTP_GATE_EXEMPT", DEFAULT_HTTP_GATE_EXEMPT)`），
   但 `scripts/scan_settings.py` **没有提取它**（实测：只扫该文件时 `managed_names` 里
   `CP_HTTP_GATE_EXEMPT` 不存在，而同文件的 `_env_flag/_env_int/_env_float` 三个都被提取）。
   因为它既不在 `missing` 也不在 `extra`，**登记它反而会让反向守卫变红**，故本卡**按纪律不登记**。
   ⇒ 这是**提取器对 `_env_csv` 形态的覆盖盲区**（同类：豁免路径前缀因此永远不会出现在开关中心）。
   要根治须改 `scripts/scan_settings.py` 的 helper 识别表 —— 不在本卡文件范围。

2. **一个跨卡测试冲突（已定位、非本卡引入、未修）**：
   `tests/unit/test_route_log_sink.py::test_switch_off_writes_nothing` 现在**红**，原因是
   `tests/unit/conftest.py:907-931` 的 autouse 夹具 `_iso_assessment_events_to_tmp` **在每个单测 setup 时**
   无条件执行 `(tmp_path / "data").mkdir(parents=True, exist_ok=True)`，而 B2 那条用例断言
   `list(tmp_path.iterdir()) == []` —— 该断言在 unit conftest 下**不可满足**。
   证据：① 单测**隔离运行**（`-p no:randomly`，只跑这一条）仍红，报错项恒为 `<tmp_path>/data`；
   ② 该用例整段逻辑是从 `app_server.py` 抽源码 exec，**从不 import `agent.settings.registry`**（`test_route_log_sink.py` 全文无 "settings" 字样）；
   ③ 本卡的改动是纯数据表，`git diff` 只落在 `agent/settings/registry.py` 一个文件。
   ⇒ 与 SET-REG 无关；`tests/unit/conftest.py` 与 `tests/unit/test_route_log_sink.py` **都不在本卡文件范围**，交回主控/B2 卡处置。

3. **C 级路径项永不投影默认值**（设计如此）：`_public_default()` 对 `risk == RISK_C` 返回 `None`，
   故 #9/#15 的 `default=None` 在 UI 上**无论如何都显示为空** ——即"逐字一致"这两条在展示层不可见，
   但登记值仍必须为真（`test_registered_defaults_are_not_contradicted_by_code` 一类的口径纪律）。

---

## ⑥ 回归证据（本卡实跑，原始输出摘要）

| 命令 | 结果 |
|---|---|
| `python -m pytest tests/unit/test_settings_registry.py -q`（改前） | **2 failed, 54 passed** in 43.66s |
| `python -m pytest tests/unit/test_settings_registry.py -q`（改后） | **56 passed** in 51.18s |
| `python -m pytest tests/unit/test_settings_routes.py tests/unit/test_settings_service.py tests/unit/test_settings_resolver.py tests/unit/test_settings_c_default_regression.py tests/unit/test_observability_config.py -q` | **185 passed** in 11.39s（输出里的 `loki.push.exception` 是本机无 Loki 服务的既有噪声，非失败） |
| `python -m pytest tests/unit/test_retrieval_silent_failures.py::TestLLMClientTimeout ::TestIndexCacheMainTrack ::TestFallbackCoverageThreshold -q` | **14 passed** in 3.53s（直接断言 #11/#12/#13/#14/#16 的默认值） |
| `python -m pytest tests/unit/test_backpressure.py tests/unit/test_prompt_volatile_tail_order.py tests/unit/test_skill_description_single_source.py tests/unit/test_route_log_sink.py -q` | **99 passed, 1 failed**；唯一失败 = 上文残余风险 2（跨卡，非本卡引入）；再以 `--deselect` 排除该条后 **99 passed** |

---

## ⑦ 回滚指令

本卡不允许 `git commit/add`，也**不得**用 `git checkout <file>`（该文件夹着别的卡的风险）。
回滚只需撤销 `agent/settings/registry.py` 里那**一个 hunk**（已实测 `git diff` 该文件**只有本卡这一个 hunk**：`108 added, 0 deleted`）：

```powershell
# 主方案（精确反向打补丁，只动本卡那 108 行；已用 --check 验证可应用，退出码 0）
cd C:\Users\Administrator\agent
git diff -R -- agent/settings/registry.py | git apply --check -    # 干跑校验
git diff -R -- agent/settings/registry.py | git apply              # 真正回滚
```

```powershell
# 备份方案（手工）：删除 agent/settings/registry.py 的 2404-2511 行
#   —— 即 "# ── SET-REG：" 注释起到 `_a("YUNSHU_PROMPT_VOLATILE_TAIL"...` 条目止，
#      闭合 `]` 保留。删除后 registered_env_names() 应回到 432，守卫恢复为 2 failed。
```

回滚本报告：`Remove-Item docs\audit_skill_governance\SETREG.md`。

**回滚后自检**：`python -m pytest tests/unit/test_settings_registry.py -q` ⇒ 应重新出现
`2 failed, 54 passed` 与 `432 == 449`（即回到本卡开工时的状态）。

---

## ⑧ 残留物声明

- 探针脚本全部写在**仓库外**：`C:\Users\Administrator\AppData\Local\Temp\setreg\`（`probe.py` / `probe_scan.py` / `before.txt` / `after.txt` / `regression_*.txt`），**未在仓库内留任何探针**。
- 未使用、未改动 `venv/`；未 kill 任何 python 进程；未触碰 5678 端口或 `app_server.py` 前台实验。
- 仓库内新增文件仅 `docs/audit_skill_governance/SETREG.md`（deliverable）。
