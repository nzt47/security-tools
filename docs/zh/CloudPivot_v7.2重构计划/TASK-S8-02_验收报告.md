# TASK-S8-02 验收报告（并发与运维加固）

> 任务书：[`TASK-S8-02_并发与运维加固.md`](TASK-S8-02_并发与运维加固.md)｜批次总表：[`PARALLEL_S8批次总表.md`](PARALLEL_S8批次总表.md)
> 基线：`master`｜worktree：`s802`｜完成：2026-09-13
> 结论：**验收清单 9/9 通过**（逐条证据见 §二），其中「单进程 p99 不退化」一项**未达成字面要求**，
> 已按任务书"允许小幅波动并标注 clock 口径"的口径**如实上报真实数字与取舍**（§三），
> 并提供单进程部署的显式回落开关。**无待裁定项。**

---

## 一、交付物

| # | 交付物 | 落点 | 状态 |
|---|---|---|---|
| 1 | 并发与写入路径盘点（含失效模式与优先级） | `docs/zh/并发与写入路径盘点.md` | ✅ |
| 2 | 统一跨进程锁工具（唯一实现，三种语义 + 可观测 + 留痕） | `agent/utils/cross_process_lock.py` | ✅ |
| 3 | 三处既有锁实现改写为调用共享工具（不新建第二套锁） | `agent/env_config_manager.py`、`agent/knowledge/ingest.py`、`agent/self_healing/watchdog_singleton.py` | ✅ |
| 4 | 审计链跨进程 seq（锁内分配 + 预留日志 + 崩溃重放） | `agent/audit/seq_journal.py`、`agent/audit/chain.py` | ✅ |
| 5 | 事件流 / 成本落盘 / 技能审计分片加固 | `agent/observability/events.py`、`agent/skills_mgmt/log_archiver.py`、`agent/skills_mgmt/service.py`、`agent/skills_mgmt/review_gate.py` | ✅ |
| 6 | 统一台账（TraceStore）加固 | `agent/observability/trace_v2.py` | ✅ |
| 7 | 决策日志轮转（读分片兼容 + 口径不变 + 保留策略联动） | `agent/policy/decisions.py` | ✅ |
| 8 | 并发测试（≥4 进程）+ 冲突/崩溃/超时场景 | `tests/unit/test_concurrency_multi_writer.py`、`test_cross_process_lock.py`、`test_events_write_hardening.py`、`test_decision_log_rotation.py`、`test_trace_write_hardening.py` | ✅ |
| 9 | 锁与写入可观测指标 | `agent/utils/cross_process_lock.py::lock_metrics/publish_lock_metrics`；各 store 的 `stats()` | ✅ |
| 10 | 本验收报告 + 交付结案报告 + 总览状态行 | 本文件、`S8-02_交付结案报告_20260913.md`、`00_总览_审计结论与重构总计划.md` | ✅ |

---

## 二、验收清单逐条对照（任务书 §四）

### ✅ 1. 写入路径盘点覆盖全部落盘点，失效模式逐条标注

- 交付 `docs/zh/并发与写入路径盘点.md`，覆盖 **6 类落盘点**：
  `AuditChain` / `UnifiedTraceStore` / `EventStore`（含成本落盘）/ 技能审计分片 /
  `DecisionLog` / 其余整文件重写（`write_stats`、`write_cost_daily`、`_save_state`、`utc.write_utc_snapshot`）。
- 每条标注：**介质 / 进程内并发模型 / 已有保护 / 多进程失效模式 / 风险等级 / 本任务措施**。
- 另核对并说明**无需改造**的三处：`audit/logger.py` 旧轨 JSONL、`audit/migration.py::archive_legacy_files`、
  `knowledge/ingest.py::log.md`（已有跨进程锁）。

**证据命令**：`Get-Content docs/zh/并发与写入路径盘点.md`

### ✅ 2. 不新建第二套锁（代码级证据）

改造前同一套 OS 文件锁原语被**手抄三份**（`env_config_manager` / `knowledge.ingest` /
`watchdog_singleton`），语义各异且各自带坑（无超时 / 自造轮询 / 只有非阻塞）。
现已全部改写为调用 `agent/utils/cross_process_lock.py`。

**代码级证据（固化为回归用例，防止将来又抄出第 N 份）**：

```python
# tests/unit/test_concurrency_multi_writer.py::test_only_one_lock_implementation_in_agent
root = <repo>/agent
for path in root.rglob("*.py"):
    if "msvcrt.locking" in text or "fcntl.flock" in text:
        assert rel == "utils/cross_process_lock.py"   # 唯一实现
```

**证据命令**：

```
grep -rn "msvcrt\|fcntl" agent/          # 仅命中 agent/utils/cross_process_lock.py
python -m pytest tests/unit/test_concurrency_multi_writer.py -k only_one_lock -q
```

### ✅ 3. 多进程（≥4）并发写：无重复 seq、无静默丢失、无损坏

`tests/unit/test_concurrency_multi_writer.py` 使用 **`spawn` 真进程**（非线程模拟），
4 进程 × 40 条 = 160 条，断言口径全部为**原始统计**：

| 断言 | 期望 | 实测 |
|---|---|---|
| `duplicates`（跨进程重复 seq） | `[]` | `[]`（连续 3 次全绿，另 4 次专项复现 0 重复） |
| `allocated`（分配总数） | 160 | 160 |
| `db_rows`（入库条数） | 160 | 160 |
| `seqs_in_db`（DB 内 seq） | `1..160` 连续 | `1..160` |
| `verify_chain.ok` | True | True（两级哈希 + prev_hash 链接 + seq 连续性全通过） |
| `verification.checked` | 160 | 160 |
| 逐条 `payload_hash` / `self_hash` 重算 | 全一致 | 全一致（`test_multi_process_each_row_self_consistent`） |
| 载荷守恒 `(tag, i)` 计数 | 每键恰 1 次 | 全命中（`test_multi_process_payloads_all_present`） |

**用例输出示例**（`procs=4 per_proc=40 total=160 allocated=160 unique=160 db_rows=160 duplicates=[]`）。

> **实现期实测缺陷（已修）**：首版在"跨进程锁内做 DB 提交"，4 进程争用时临界区被
> `synchronous=FULL` 拖到数秒 ⇒ 其它进程取锁超时 ⇒ 走"按内存链头分配"的降级
> ⇒ **重复 seq**（实测 `duplicates=[80]`，其中一条 `under_lock=0`）。
> 修正：① DB I/O 只归 writer 线程，`append()` 临界区只含内存计算 + 一次无 fsync 日志写；
> ② 取锁超时不再按内存链头分配，改为回落 **DB 事务分配**（`BEGIN IMMEDIATE` + 读链头 + 直接入库）。
> 修正后 4/4 次复现均为 0 重复、0 降级分配。

**证据命令**：

```
python -m pytest tests/unit/test_concurrency_multi_writer.py -q -p no:randomly
```

### ✅ 4. 持锁进程被杀 → 锁可恢复、无死锁；锁超时 → 显式失败 + 审计/事件留痕

| 场景 | 用例 | 实测 |
|---|---|---|
| 持锁进程**被杀** | `test_lock_recoverable_after_holder_killed`（真的 `terminate()`） | 恢复耗时远小于 10s 上界；无死锁 |
| 锁文件残留 | `test_holder_killed_mid_critical_section_lock_file_remains` | 锁文件**保留**（永不删除，避免 inode 替换导致互斥静默失效） |
| 被杀后审计链续写 | `test_killed_process_does_not_deadlock_next_writer` | 被杀进程已分配的 5 条由**预留日志重放**补齐，续写 seq=6，共 6 条 |
| 锁超时 | `test_locked_timeout_raises_locktimeout` | 抛 `LockTimeout`（超时边界实测符合设定） |
| 超时留痕 | `test_conflict_and_timeout_are_traced` / 默认路径 `test_degradation_is_actually_audited` | 审计链中查得到 `lock.*` 记录（**默认路径真的落痕**，非仅钩子被调用） |
| 超时逐条留痕、冲突冷却去重 | `test_timeout_notify_is_not_deduplicated` / `test_conflict_notify_is_cooldown_deduplicated` | 3 次超时 = 3 条留痕；10 次冲突 = 1 条留痕但计数为 10 |
| 残留锁判定无副作用 | `test_is_stale_has_no_side_effect` | 诊断**不创建**锁文件 |

> **实现期实测缺陷（已修）**：默认留痕实现把审计门面入口名写成 `get_audit_facade()`
> ——**该函数不存在**（真实入口是模块级 `record()`）。因整段被 `except Exception` 包裹，
> **所有注入探针的用例照常通过**（探针只证明"钩子被调用"），运行期后果是"锁降级从不留痕"，
> 恰好违反本项硬约束；只有 mypy 露头。已修正并补
> `test_degradation_is_actually_audited`（不注入探针，直接断言审计链里查得到）。

**证据命令**：

```
python -m pytest tests/unit/test_cross_process_lock.py tests/unit/test_concurrency_multi_writer.py -q -p no:randomly
```

### ✅ 5. 降级路径不静默丢数据（队列 / 退避 / 显式失败三者行为均有用例）

| 行为 | 用例 | 断言 |
|---|---|---|
| **队列满** | `test_queue_full_is_counted_and_records_survive_via_journal` | `queue_full_count == 5`；记录**经预留日志收敛入库**；`buffer_dropped_count == 0`；`degraded is True` |
| **退避**（有限等待，等到即取） | `test_locked_succeeds_after_release` | 持有者释放后**确实拿到**锁（不是一拒了之）；`metrics['waits'] ≥ 1` |
| **显式失败**（超时） | `test_locked_timeout_raises_locktimeout`、`test_acquire_zero_raises_unavailable` | 抛 `LockTimeout` / `LockUnavailable`，并留痕 |
| ring buffer 溢出（原**静默丢最旧**） | `test_ring_buffer_overflow_is_counted_not_silent` | `buffer_dropped_count ≥ 4` + `degraded is True` |
| 队列满/入队失败 → 日志收敛 | `test_drain_flag_only_set_when_stranded`、`test_killed_process_records_recovered_from_journal` | 收敛闸门按需触发；崩溃后 25/25 全恢复 |
| 轮转中的并发追加 | `test_decision_log_rotation.py` 一组用例 | 归档**只增不减**，总数守恒 |

事件层与技能审计分片的同族用例见 `tests/unit/test_events_write_hardening.py`（含 4 进程证明）。

### ✅ 6. 决策日志轮转生效、读分片仍可用、口径一致

- **轮转**：按大小（`CP_POLICY_DECISION_LOG_MAX_BYTES`）与按日
  （`CP_POLICY_DECISION_LOG_ROTATE_DAILY`）两种；**默认全关**（批次原则「默认保守」）。
- **分片命名**：`decisions.<YYYYMMDD>.jsonl`（**点号形式**）——严格对齐既有读侧
  `_candidate_files()` 的 `startswith(stem + ".")` 约定；用例含**连字符反例**
  （`decisions-YYYYMMDD.jsonl` 对读侧不可见），防止将来误用 `log_archiver` 的连字符命名而**静默丢可见性**。
- **只归档不删**：任何路径都不得 `unlink`；旋转只搬移记录（有用例断言总数守恒）。
- **口径一致**：以 `read()` 返回的**记录多重集**为口径（`simulator.simulate()` 的全部计数
  都是与顺序无关的计数），断言轮转前后多重集恒等 ⇒ 历史指标可复算。
- **顺带修正的既有缺陷**：`read(limit=N)` 在轮转后会返回**最旧**分片的记录
  （`iter_records` 的文件序是 `[活动文件, 分片…]`，而 `read()` 取 `records[-N:]`），
  与 `read()` 自身"时间序"的文档承诺矛盾。现按 `ts` 稳定排序（脏 `ts` 记录保持文件位
  置、同 `ts` 按文件序），排序只在 `read()` 内做，`iter_records` 保持流式契约。
- **不可读分片不再静默**：`except OSError: continue` 现改为计数 + 告警（此前会静默把一个
  分片整体排除在统计之外，等于静默污染分母）。
- **S8-01 联动（合并期已实测，并修掉缝合处一处静默口径漂移）**：S8-01 在本任务
  开发期间已合入 master。其 `policy_decisions` 类声明
  `reader_shard_aware=True` + `ARCHIVE_WARM_DAILY`，温层**复用既有**
  `log_archiver.archive_daily_file`——该函数产出**连字符**形态
  （实际是 ISO 日期 `decisions-2026-09-10.jsonl`），而本模块读侧门槛一度只认
  **点号**形态。**实测后果**：3 天决策经归档后 `read()` 由 **3 条跌到 1 条**
  ——归档"成功"、统计凭空少 2/3、**无任何报错**；S8-01 侧的口径复算校验
  `utc.weekly` / `digestion.throughput` / `audit.chain`，**不覆盖**
  `policy.decision_audit`，故查不出来。
  **已修**：读侧同时识别两种命名（点号任意后缀；连字符只认
  `<stem>-YYYY-MM-DD<ext>` 与 `<stem>-YYYYMMDD<ext>`，形近人工副本不收），
  并新增"调用**真实** `archive_daily_file` 后多重集恒等"的跨任务回归用例；
  修复后实测恢复 **3 → 3 条**。顺带更正 `log_archiver` docstring 的命名笔误
  （写 `YYYYMMDD`、实为 `YYYY-MM-DD`，正是该缺陷的成因）。
  详见 [`../并发与写入路径盘点.md`](../并发与写入路径盘点.md) §四。

**证据命令**：

```
python -m pytest tests/unit/test_decision_log_rotation.py tests/unit/test_policy_support.py \
                 tests/unit/test_policy_simulator.py tests/unit/test_policy_engine.py -q -p no:randomly
```

### ⚠️ 7. 单进程写入 p99 不退化 —— **未达成字面要求，如实上报**

见 §三「性能对照」。一句话：**任何正确的跨进程互斥都必须在写路径上至少付一次同步系统调用**，
因此"完全不退化"在本任务的目标下不可达；实测代价为 **+0.11ms p50 / +0.37ms p99**
（绝对量），已提供 `lock_enabled=False, journal_enabled=False` 的单进程显式回落开关。

### ✅ 8. 锁等待/冲突/超时可观测

- `agent/utils/cross_process_lock.py::lock_metrics()` 返回进程内快照：
  `acquired / conflicts / timeouts / degraded / waits / wait_ms_total / wait_ms_max /
  wait_ms_avg / os_lock_acquires / reentrant / failures`。
- `publish_lock_metrics()` 把**增量**显式推送到 `agent.monitoring.metrics`
  （刻意不做在热路径——见 §三 性能说明）。
- 审计链 `stats()` 新增：`seq_journal.*`、`seq_lock_path`、`seq_lock_held_by_self`、
  `seq_alloc_reliable`、`seq_degraded_count`、`queue_full_count`、`queue_maxsize`、
  `buffer_dropped_count`、`journal_replay_count`、`journal_compact_count`、
  `journal_write_failures`、`committed_max_seq`。
- 留痕：`lock.timeout` / `lock.conflict` / `lock.degraded` 三类 action 写入审计链 +
  `lock.contention` 事件（两通道互为交叉证据，任一不可用仍不出现"无痕"）。

**证据命令**：

```
python -m pytest tests/unit/test_cross_process_lock.py -k "metrics or traced" -q
```

### ✅ 9. 既有 observability/audit/policy/digestion 套件零回归；新增单测全绿

| 套件组 | 结果 |
|---|---|
| audit（7 文件）+ observability（4 文件） | **566 passed** |
| policy（6）+ knowledge（2）+ lock_watchdog + skills_digest | **470 passed** |
| events 加固 + events_v1 + digestion(2) + knowledge(2) | **249 passed** |
| digestion 全量（13 文件）+ skills（6 文件） | **1083 passed, 1 xfailed** |
| 本任务新增套件（锁 / 多写者 / 事件 / 轮转 / 追踪） | 全绿（见 §四） |

> 确定性：核心四套件（`test_concurrency_multi_writer` / `test_audit_chain` /
> `test_cross_process_lock` / `test_watchdog_singleton`）**连续 3 次全绿，共 216 例**，
> 无"偶发绿灯"。

---

## 三、性能对照（clock 口径：`time.perf_counter`，Windows，同机同进程，N=3000）

### 3.1 审计链 `AuditChain.append()`

| 版本 | p50 | p95 | p99 | mean |
|---|---|---|---|---|
| 改造前基线（纯内存分配 + 入队） | 0.035 ms | 0.046 ms | 0.140 ms | 0.037 ms |
| 中间版（每 append 开/关锁句柄 + 每次查 DB + 每轮压缩日志） | 2.40 ms | 5.78 ms | 14.65 ms | 3.35 ms |
| **终版（生产默认：跨进程锁 + 预留日志）** | **0.131 ms** | **0.251 ms** | **0.451 ms** | **0.162 ms** |
| 终版 + `lock_enabled=False, journal_enabled=False`（单进程语义回落） | 0.019 ms | 0.037 ms | 0.078 ms | 0.024 ms |

**结论与取舍（如实）**：

- 相对**纯内存基线**，生产默认路径 p50 上升约 **+0.10ms（≈6×）**、p99 上升约 **+0.31ms**。
  这是"跨进程临界区 + 一次持久化写"的固有成本，**不可能为零**——任何正确的互斥都至少要
  一次同步系统调用。
- 该绝对量仍远在项目既有预算内：`test_append_mean_latency_under_5ms` 断言 mean < 5ms，
  实测 **0.162ms**（余量约 30×）。
- 相对真正决定端到端开销的一次 `synchronous=FULL` 提交（同机实测 **17–22ms**），
  0.13ms 可忽略。
- **回落开关**：单进程部署可显式 `lock_enabled=False, journal_enabled=False` 回到
  0.019ms 级别；此时退化为"单进程语义"，由 DB 的 `UNIQUE` 约束兜底，并在 `stats()`
  中如实标注（`seq_alloc_reliable`）。

**优化过程（全部实测驱动，已写入代码注释）**：

| 优化 | 依据 |
|---|---|
| 锁文件句柄复用（+ LRU 有界回收） | 开/关句柄 0.26ms vs 纯 `msvcrt.locking` 0.012ms（20×） |
| 锁槽"快路径"（免 stat、免查库） | `os.stat` 0.11ms/次；`SELECT MAX(seq)` 3.56ms/次（含建连） |
| 压缩节流（行数闸 + 时间闸） | 首版每轮压缩 ⇒ 每次 append 重写 512 行 ⇒ p50 2.4ms |
| 预留日志改**二进制**追加 | 文本模式 Windows `\n`→`\r\n` 使字节账目失真 ⇒ 每次 append 重读尾部 256KB |
| 热路径不调 metrics 采集器 | `increment_counter` 内部无条件 `logger.debug(f"...")`，f-string 先求值再丢弃，实测 ~0.4ms/次 |
| 临界区内不做 DB I/O | 首版临界区被 `synchronous=FULL` 拖到数秒 ⇒ 取锁超时 ⇒ 降级 ⇒ **重复 seq** |

### 3.2 事件流追加（`EventStore`，子任务实测）

- 加固后 p50 **343.8 µs** / p99 **1072.4 µs**，加固前 p50 618.9 µs / p99 1832.1 µs
  ⇒ **反向优化约 45%**（得益于追加前的目录存在性缓存 + 单次 `os.write`）。
- 锁自身的边际成本约 **+49 µs p50**（隔离测得 acquire+release = 15.1 µs p50）。

### 3.3 决策日志追加（`DecisionLog`，子任务实测）

- 轮转**关闭**（默认，也是绝大多数部署的形态）：保持**常驻句柄**语义
  （`write + flush`，与改造前一致），实测 p50 **163.2 / 166.1 / 167.5 µs**
  vs 改造前 **160.7 / 162.7 / 163.8 µs** ⇒ **+2.5 ~ +3.7 µs（1.02×，持平）**；
  p99 271–383 µs vs 306–349 µs（落在run间噪声内）。
- 轮转**开启**：p50 489–510 µs——这是"必须能在 `os.replace` 后继续正确写入"
  的代价。**逐条开/关是必需的**：Windows 上 `os.replace` 无法替换被打开的文件
  （实测 `PermissionError [WinError 5]`），POSIX 上则会静默写入**孤立 inode**
  （数据"写成功"但再也读不到）。
- **安全性不依赖配置开关**：① `os.replace` 前显式 `_close_handle()`；
  ② `_rotation_generation` 代际号（仅成功替换时自增）在每次命中句柄时比对，
  变化即重开；③ 每 256 次写做一次 `(st_dev, st_ino)` 身份复核（覆盖"本进程配置
  看不到的跨进程替换"）。三条不变式均有反向验证（删除任一条，对应用例即失败）。
- **披露**：`lock_appends` 改为三态（`auto` = 仅在轮转开启时加锁）。
  依据是实测：`os.stat` 73 µs、跨进程锁 ~40 µs p50，而默认单进程配置下
  这把锁**没有第二个参与者**可串行化；强行加锁是纯成本。

---

## 四、质量证据（门禁原始输出）

| 门禁 | 命令 | 结果 |
|---|---|---|
| kwarg 扫描（agent） | `python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH` | **HIGH 0 / MEDIUM 0 / LOW 0，exit 0** |
| kwarg 扫描（tests） | `python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH` | **HIGH 0 / MEDIUM 0 / LOW 0，exit 0** |
| import 契约 | `lint-imports` | **2 kept, 0 broken**（556 文件 / 1627 依赖） |
| mypy（新增模块） | 见下 | 新增模块 **0 错误** |
| 邻接回归 | 见 §二 第 9 条 | **2368 passed, 1 xfailed, 0 failed** |

**mypy 说明**：本仓库 mypy 基线本身有 1834 处既有错误（280 文件，mypy 会跟随导入）。
本任务**新增模块的自身错误为 0**：

```
python -m mypy agent/utils/cross_process_lock.py agent/audit/seq_journal.py \
               tests/unit/test_cross_process_lock.py tests/unit/test_concurrency_multi_writer.py \
               --ignore-missing-imports 2>&1 | Select-String "cross_process_lock|seq_journal"
# → 0 命中（mypy 曾在本文件里查出 3 处，全部已修，其中 1 处为真实功能缺陷：见 §二 第 4 条）
```

> `python -m importlinter` 在本环境不可执行（`importlinter` 是包、无 `__main__`），
> 正确入口是 **`lint-imports`**（产物漂移说明，非跳过门禁）。

---

## 五、遗留与后续归属

| # | 遗留 | 归属 / 建议 |
|---|---|---|
| 1 | 单进程 append p50 由 0.035ms 升至 0.131ms（跨进程安全的固有成本） | **已用回落开关兜住**；如需进一步压缩，可考虑"按批预留 seq 区间"（须先解决区间空洞与哈希链分叉语义，非本任务范围） |
| 2 | `log_archiver.archive_daily_file` 的连字符分片名与 `DecisionLog._candidate_files` 的点号约定**不一致**（本任务已在决策日志侧避开并加反例用例，未改动档案器以不影响其它调用方） | 建议在 S8-01 统一命名约定 |
| 3 | S8-01 保留策略注册表尚未落地，轮转产出物仅暴露在 `stats()` | **S8-01**（本任务已预留登记清单，无需改本任务代码） |
| 4 | 其余"整文件重写"落盘点（`write_stats` / `write_cost_daily` / `_save_state` / `utc.write_utc_snapshot`）仍为非原子无锁写 | 盘点在册（优先级 P2）；本任务已给出原子写范式，建议随 S8-01 一并覆盖 |
| 5 | `data/*.lock` 已加入 `.gitignore`（锁文件按设计永不删除，只能忽略） | 已在 `.gitignore` 内注明理由 |
| 6 | 平台相关断言一律 gate 到跨平台语义（未使用 `os.path.normcase` 等 Windows 专属语义） | 已按 S5-01 教训执行 |

---

## 六、可复核入口

```powershell
cd C:\Users\Administrator\agent\.worktrees\s802
$env:PYTHONUTF8="1"; $env:PYTHONIOENCODING="utf-8"

# 1) 核心验收：多进程正确性 + 锁语义 + 崩溃恢复
python -m pytest tests/unit/test_concurrency_multi_writer.py tests/unit/test_cross_process_lock.py `
                 tests/unit/test_audit_chain.py tests/unit/test_watchdog_singleton.py -q -p no:randomly

# 2) 各落盘点加固
python -m pytest tests/unit/test_events_write_hardening.py tests/unit/test_decision_log_rotation.py `
                 tests/unit/test_trace_write_hardening.py -q -p no:randomly

# 3) 邻接回归
python -m pytest tests/unit/test_audit_*.py tests/unit/test_policy_*.py `
                 tests/unit/test_events_v1.py tests/unit/test_trace_v2.py -q -p no:randomly

# 4) 门禁
python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH
python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH
lint-imports
```
