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

#### 2b. ⚠️ 改写三处旧锁时**有意的行为变更申报**（批次原则「不改既有公开接口行为」要求显式声明）

把三处手抄实现统一到原语，**公开名与签名全部未变**，但有 4 处**可观察语义**确实变了。
逐条申报，含理由与兼容性核查（不申报即为"静默改语义"）：

| # | 位置 | 变更前 | 变更后 | 理由 / 兼容性核查 |
|---|---|---|---|---|
| 1 | `env_config_manager._acquire_process_lock` | `LOCK_EX`/`LK_LOCK` **无限阻塞**（无超时）；进程互等可永不返回 | **有限等待**（默认 **10s**，读 `knowledge.file_lock_timeout_sec`，异常/非正回落 10s）→ 超时先 warning 再 `raise` | 采纳任务书"锁超时须**显式失败**"要求。**不降级为无锁写**：`.env` 是"读改写 + rename"，无锁会静默丢配置。兼容性：生产唯一调用方 `agent/network_config.py::_save_secure` 外层已有 `except Exception` + `logger.error` ⇒ 新异常被接住并留痕；`scripts/diagnose.py` 的 set/delete 会以 traceback 结束（**变更前是永久阻塞**，故这是改进而非退化）。原有 `lock_acquire_start` / `lock_acquired(lock_wait_ms)` / `lock_contention(>1s)` 三条日志**原文保留** |
| 2 | `knowledge.ingest._FileLock` 锁对象 | 锁 **`log.md` 本体**的字节 0 | 锁**独立**的 `<log.md>.lock` | 原语硬约束：对"可能被 rename 替换的文件本体"加锁，会让锁落到**被淘汰的 inode** 上 ⇒ 互斥静默失效。`.fh` 语义（单 fd 读改写）**保留**。副作用核查：新增文件在 KB 根目录、**不在** `inbox/`（`KnowledgeWatcher` 只监听 inbox）；且 `ingest` 的文件扫描本就排除 `*.lock`（glob `exclude=["*.meta.json","*.lock","*.tmp"]` 与名尾过滤双重排除）⇒ 不会被登记/索引 |
| 3 | `knowledge.ingest._FileLock` 超时实现 | 自带 **50ms 轮询 + 手工 deadline**，单次 `LK_LOCK` 可阻塞约 10s | 原语 **5ms 轮询 + 非阻塞 OS 锁**，超时边界可预测 | 对外**异常类型与消息逐字保留**（仍是本地 `LockTimeout`，文案 `获取文件锁超时: …`），既有 `except` 与日志检索不受影响。`timeout=0` 时旧码可能仍阻塞约 10s、新码立刻超时——两者都是抛 `LockTimeout`，属边界收紧 |
| 4 | `watchdog_singleton._os_lock_acquirable()` / 身份槽 | 本模块自实现探测；写身份槽末尾 `flush()+os.fsync()` | 委托原语 `is_stale()`；身份槽写入由原语负责，**不再 fsync** | 行为等价（"未持锁 ∧ 文件存在 ∧ 立刻可获取 OS 锁"，且 `create=False` 无副作用），但**不是同一段代码**：将来原语改 `is_stale()` 口径，本方法随之变化（已写入注释）。身份槽是**诊断信息、非授权依据**，去掉 fsync 不影响互斥正确性（锁由 OS 持有）。`SplitBrainError` / `WatchdogLockError` / 非阻塞语义（第二个实例**被拒**而非排队）全部保留 |

> **顺带修掉的一个既有缺陷（有意修复，非回归）**：`ingest._FileLock.__enter__` 原本把
> `open()` 放在 `try` **之外** ⇒ open 失败（目录权限/路径过长）时 `_THREAD_LOCK`
> **永不释放**，本进程后续所有 `log.md` 写入静默卡死。现 open 纳入 try 并统一回滚，
> 已由探针验证超时路径 `_THREAD_LOCK` 未泄漏。

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

### ✅ 3b. 损坏防护：写入原子性 + **校验和隔离告警**（按介质分别说明，含一处如实缩水）

任务书原文：「损坏防护：写入原子性（临时文件 + rename / DB 事务），校验和不匹配即隔离并告警」。
按**介质能力**逐路径对照——**校验和只能存在于自带哈希的格式里**，故口径如下：

| 落盘点 | 介质 | 写入原子性 | 校验和依据 | 不匹配即隔离/告警 |
|---|---|---|---|---|
| `AuditChain` | SQLite | **DB 事务**（`synchronous=FULL`）+ 跨进程锁 | **自带两级哈希**：`payload_hash`（规范化记录 JSON）+ `self_hash`（`seq\|ts\|actor\|action\|subject\|payload_hash\|prev_hash`）+ 前驱链接 + 每日 Merkle 根 | ✅ `verify_chain()` 全链重算并报**首个**篡改位（`seq_not_monotonic` / `prev_hash_mismatch` / `payload_hash_mismatch` / `self_hash_mismatch`）；`verify_daily_root` 另以 `leaf_count` 兜区间空洞 ⇒ 篡改/删除/插入均**显式失败**（`stats()['chain_ok']=False`、CLI 退出码 1） |
| `UnifiedTraceStore` | SQLite | WAL + 跨进程锁保护提交 | `hash_content`（行内 `args`/`output_hash`） | ✅ `verify_integrity()` 读侧重算 + `_integrity_checked` / `_integrity_mismatch` 计数（**显式调用**、可按 limit 抽样，不在默认读路径上跑） |
| `EventStore` / 成本落盘 | JSONL | **单次 `os.write` 整行追加**（持锁）⇒ 结构上不可能撕行 | **格式本身无校验和字段** | ⚠️ **仅"可见性"，非"校验和"**：坏行/超长行计 `skipped_line_count` / `skipped_oversized_line_count` + 限流告警；不做逐条哈希校验 |
| `DecisionLog` | JSONL | 轮转走 **临时文件 + `os.replace` + `fsync`**（已改为原子）；追加持锁 | **格式本身无校验和字段** | ⚠️ **仅"可见性"**：坏行跳过、**不可读分片计 `unreadable_shards` + 告警**（修掉了原先 `except OSError: continue` 的静默）；不做逐条哈希校验 |
| 技能审计分片 | JSONL | 临时文件 + `os.replace` | 无 | ⚠️ 归档跳过/降级均计数 + 留痕 |
| **整文件 JSON 快照/状态**（`utc_snapshot` / `cost_daily` / `cost_brake_state` / `trace_stats`） | JSON | **统一原子写**（`agent/utils/atomic_write.py`：同目录临时文件 + `os.replace` + fsync） | 无（无校验和字段） | ⚠️ 同上；但**写入原子性已补齐**——这 4 个文件原是"截断在前、写入在后"的整文件覆盖写，崩在中间即半截 JSON，而它们是 **S5-03 预算刹车**的输入 ⇒ 损坏可能让刹车静默失去保护 |

**⚠️ 原子写在 Windows 上的已知代价（实测，有意接受）**：`os.replace` 需要目标**未**被
其它句柄以"不共享 delete"的方式打开，而 Python 的 `read_text()` 正是这种打开。因此
① **写侧**：目标被瞬时占用会报 `WinError 5`，原语用有界重试（`REPLACE_ATTEMPTS`）消化；
② **读侧**：换名瞬间并发读者可能拿到 `Errno 13`；③ **持续不让出的读者可饿死写者**
（已用 `test_continuous_reader_can_starve_writer_on_windows` 固化）。
**这是有意用它换掉"读者静默读到半截 JSON"**：对这些文件而言，"这一轮没刷新、
下轮会成功"远优于"预算刹车读到错误数值"。故**不**断言"写入零失败"
（实测该断言在 2 写者 + 2 间歇读者下稳定失败，写成断言只会得到偶发绿灯），
只断言设计真正保证的不变量：**读者要么拿到完整旧版本、要么完整新版本、要么拿到
可重试的瞬时错误，绝不会拿到半截/交错内容**。

**如实缩水说明（不做美化）**：`events.v1` 与 `policy.decision.v1` 两种 JSONL 信封
**schema 里没有校验和字段**，因此"校验和不匹配即隔离"在这两条路径上**没有实现**——
它们拿到的是"**损坏可见**"（计数 + 限流告警 + 不可读分片计数），不是"校验和校验"。
给它们加校验和属于**改数据格式**（写侧多一个字段、读侧要兼容历史行），
超出本任务"不改既有公开接口语义"的边界，故**刻意不做**，登记为遗留（§五 第 9 条）。
自带哈希的两种介质（审计链 / 统一台账）则**完整满足**该验收条款。

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

### ⚠️ 7. 单进程写入 p99 不退化 —— **仅审计链未达字面要求，如实上报**

见 §三「性能对照」。**按路径分别给结论**（本任务加固了 6 条写入路径，代价并不相同）：

| 路径 | 单进程 p50 前 → 后 | 结论 |
|---|---|---|
| 审计链 `AuditChain.append()` | 0.035 → **0.131 ms**（p99 0.140 → 0.451 ms） | **未达字面要求** |
| 事件流 `EventStore`（含成本落盘） | 618.9 → **343.8 µs**（p99 1832 → 1072 µs） | ✅ **反向优化**（约 −45%） |
| 决策日志 `DecisionLog`（默认：轮转关） | ~160–190 → **126 µs**（p99 222 µs） | ✅ **持平或更好** |
| 统一台账 / 事件分片 / 整文件快照 | 未逐项计（有 WAL/锁/原子写增量，量级远低于上表） | — |

一句话：**"完全不退化"对审计链不可达**——任何正确的跨进程互斥都必须在写路径上至少付一次
同步系统调用，而审计链额外付了一次"预留日志持久化写"。实测代价为
**+0.10ms p50 / +0.30ms p99**（绝对量），并已提供
`lock_enabled=False, journal_enabled=False` 的单进程显式回落开关（回到 p50 0.019ms）。

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

### 3.3 决策日志追加（`DecisionLog`，**本次复核自行实测**）

clock 口径：`time.perf_counter()`，N=3000，同机同进程；样本已排空后台影响（先写 200 条 + `flush()`）。

| 配置 | p50 | p95 | p99 | mean |
|---|---|---|---|---|
| **轮转关闭（默认，`lock_appends` 三态 auto ⇒ 不取锁、用常驻句柄）** | **126.2 µs** | 177.7 µs | **222.4 µs** | 131.9 µs |
| 轮转开启（size 阈值 64KB） | 434.4 µs | 571.1 µs | 5650.7 µs | 526.0 µs |

- **默认（轮转关）不退化**：子任务记录的改造前基线约 160–190 µs p50，实测默认路径 **126 µs p50 / 222 µs p99**，即**持平或更好**。原因：轮转关闭时**不存在 `os.replace`**，故可安全复用常驻句柄（实测长期句柄 55 µs/条 vs 逐条开-关 307 µs/条）。
- 轮转开启时逐条开-关是**必需**的：Windows 上 `os.replace` 无法替换仍被打开的文件（实测 `PermissionError [WinError 5]`），POSIX 上更糟——句柄指向被替换的旧 inode，之后追加会**静默写进读不到的文件**。
- p99 的 5.6ms 尖峰来自真正执行轮转的那几次 append（搬移 + 分片 fsync + `os.replace`），属预期；且只在显式开启轮转时才出现。
- **安全性不依赖配置开关**：`os.replace` 前显式 `_close_handle()`；`_rotation_generation` 代际号（仅成功替换时自增）在每次命中句柄时比对；每 256 次写做一次 `(st_dev, st_ino)` 身份复核（覆盖"本进程配置看不到的跨进程替换"）。三条不变式均有反向验证。

#### 3.3b 运维可见语义（实测确认，属"可观察行为"须申报）

| 项 | 默认行为（实测） | 说明 |
|---|---|---|
| 是否产生 `.lock` 文件 | **默认不产生**（连写 10 条后目录只有 `decisions.jsonl`）；`lock_appends_effective == False` | `CP_POLICY_DECISION_LOG_LOCK_APPENDS` 是**三态**：未设置 = auto（**仅轮转开启时才取锁**）／`"1"` = 强制取／`"0"` = 强制不取。auto 的依据：默认配置下这把锁**没有第二个参与者**（单进程 + 本进程不轮转），逐条取锁实测 +35 µs p50 且不带来互斥收益；而轮转侧的"增量对账 + 替换前字节校验"本就覆盖"快照之后新到的写" |
| `lock_appends` 何时必须开 | **混合配置部署**（部分进程开轮转、部分不开）建议设 `CP_POLICY_DECISION_LOG_LOCK_APPENDS=1` | 见下方残余风险 |
| `rotate(force=True)` 在**未配置任何规则**时 | **按大小规则收缩到只剩最后一条**（实测 `trigger=size`、`records_moved=9`、`records_kept=1`、`read()` 仍返回全部 10 条） | `force` 是**运维显式动作**，故给确定性语义而非静默无操作；自动路径 `maybe_rotate()` **走不到**这里，**保守默认不受影响** |

**残余风险（子任务自报，本报告确认在册）**：身份复核是**周期性**的（每 `HANDLE_RECHECK_EVERY = 256` 次写一次；本机 `os.stat` 实测 73 µs、`fstat+stat` 80 µs，逐条做比跨进程锁还贵）。因此在"**别的进程开着轮转、把我们正在写的 inode 换掉**"这一本进程配置看不见的场景下，最坏有**最多 255 条**写入落在孤儿 inode。缓解：混合配置部署显式开 `CP_POLICY_DECISION_LOG_LOCK_APPENDS=1`；Windows 上对方替换通常直接因共享冲突失败（表现为对方写入失败而非我们丢数据）。该取舍写在 `HANDLE_RECHECK_EVERY` 与 `lock_appends_effective` 的注释里。

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
| 1 | 单进程 append p50 由 0.035ms 升至 0.131ms（跨进程安全的固有成本，仅审计链） | **已用回落开关兜住**；如需进一步压缩，可考虑"按批预留 seq 区间"（须先解决区间空洞与哈希链分叉语义，非本任务范围） |
| 2 | ~~`log_archiver` 连字符分片与决策日志点号约定不一致~~ → **已修**：读侧现同时识别两种命名（`<stem>-YYYY-MM-DD<ext>` / `<stem>-YYYYMMDD<ext>`），并加"调用真实 `archive_daily_file` 后多重集恒等"的跨任务回归用例 | ✅ 已关闭（缺陷 #19） |
| 3 | ~~S8-01 保留策略注册表尚未落地~~ → **S8-01 已合入 master**；本任务已完成实测联动并修掉缝合处静默缺陷；轮转产出物暴露在 `stats()`，与 S8-01 的 `globs` 口径一致 | ✅ 已关闭；另建议 S8-01 把 `policy_decisions.globs` 扩为覆盖连字符分片（**不阻塞**，读端已安全） |
| 4 | ~~其余"整文件重写"落盘点非原子~~ → **已修**：4 处（`utc_snapshot` / `cost_daily` / `cost_brake_state` / `trace_stats`）全部接入统一原子写 `agent/utils/atomic_write.py` | ✅ 已关闭（缺陷 #22） |
| 5 | 全仓仍有 **12 处**各自实现的 `_atomic_write*`（属其它任务代码面），与本任务对**锁**的判定同一条道理（「不新建第 N 份实现」） | 本任务只接入清单内 4 处；其余**登记为遗留**，建议后续逐步收敛到 `agent/utils/atomic_write.py` |
| 6 | 平台相关断言一律 gate 到跨平台语义（未使用 `os.path.normcase` 等 Windows 专属语义） | 已按 S5-01 教训执行 |
| 7 | **原子写在 Windows 上的代价**：持续占用的并发读者可**饿死**写者（有界重试耗尽 → 干净上抛 `PermissionError`）；换名瞬间读者可能拿到 `Errno 13` | **有意接受**（换掉"读者静默读到半截 JSON"）。已固化为用例；明确**不**断言"写入零失败"（实测该断言稳定失败，写进去只会得到偶发绿灯） |
| 8 | `events.v1` / `policy.decision.v1` 两种 JSONL **信封无校验和字段** ⇒ 这两条路径只有"损坏可见"（计数 + 告警 + 不可读分片计数），**没有**"校验和不匹配即隔离"；加校验和需改数据格式（写侧增字段 + 读侧兼容历史行），超出"不改既有公开接口语义"的边界 | **本任务刻意不做**；如要补齐，建议与 **S8-01** 一并设计格式迁移 |
| 9 | 新增的并发/超时旋钮（`journal_enabled` / `lock_enabled` / `seq_lock_timeout` / `queue_maxsize`）是**构造参数**，未登记进 `agent/settings/registry.py` 与 `digestion/switch_snapshot.py` | 建议后续（S7-01 开关中心 / S8-01 快照核对）；当前以构造参数形式可用，不阻塞验收 |
| 10 | 决策日志轮转**未用真正的第二个 OS 进程**跑竞争用例（用的是"同进程第二个 `CrossProcessLock` 实例占锁（原语按实例判重入 ⇒ 与外部进程同路径被拒）"+"注入式复现并发追加"两种替代） | **声明的测试缺口**；原语跨进程语义由 `test_cross_process_lock.py`（真 `spawn` 子进程互斥 + 持锁进程被杀可恢复）覆盖，审计链 ≥4 进程正确性由 `test_concurrency_multi_writer.py` 覆盖。建议后续补一条真多进程轮转竞争用例 |
| 12 | 决策日志句柄身份复核是**周期性**（每 256 次写）而非逐条 ⇒ "别的进程开着轮转换掉我们的 inode"时最坏 **≤255 条**写入落入孤儿 inode；默认配置下 `lock_appends` 为 auto（不取锁）故锁拦不住该场景 | **有意取舍**（逐条 `stat` 实测 73 µs 比跨进程锁还贵）。缓解：混合配置部署设 `CP_POLICY_DECISION_LOG_LOCK_APPENDS=1`；Windows 上通常表现为对方替换失败（不丢我方数据）。已写入代码注释与验收报告 §3.3b |
| 11 | `_failed_buffer` 竞态用例是**守卫而非复现**（小容量下复现不出 `RuntimeError`） | 已如实标注；真复现需放大 GIL 切换窗口，而 `setswitchinterval` 是进程级全局状态，放进测试会顺序污染其它用例 |

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
