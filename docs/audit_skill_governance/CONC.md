# CONC —— 审计链并发写压测（回答 D3 留白的「并发写会不会丢记录」）

- 卡号：**CONC**（验证卡；**不改任何既有生产代码**）
- 补的留白来源：`docs/audit_skill_governance/VERIFICATION_LOG.md` 的
  「D3 上报的 `lock.degraded / seq_conflict` —— 我查证后判定：**良性降级，无记录丢失**」一节，
  其中显式登记：
  > 「我需要一张**并发压测卡**，而不是靠生产偶发观察 ——
  >   并发写审计链（≥4 进程 × 大记录数）下 seq 是否出现缺口 / `lock.degraded` 频率」
- 新建文件（**只新建本卡点名的 3 个文件，未改任何既有文件**）：

  | 文件 | sha256(前 16) | 行数（总 / 非空） | 字节 |
  |---|---|---|---|
  | `tests/unit/test_audit_chain_concurrency.py` | `9F98E36DED86E0E3` | 694 / 582 | 37657 |
  | `scripts/audit_concurrency_probe.py` | `13F4E949B53B6912` | 551 / 466 | 26590 |
  | `docs/audit_skill_governance/CONC.md`（本文件） | 见交付回复 | 455 / 358 | 29611+ |

- 测试文件放在 `tests/unit/` 而**不是** `tests/integration/` 的理由：
  被测对象是 `agent.audit.chain` **单模块写路径**的内部性质（seq 分配 / 预留日志收敛 /
  ring buffer），不跨服务、不依赖网络或外部组件契约；同目录已有同域的
  `tests/unit/test_concurrency_multi_writer.py`（S8-02 的多进程用例），
  `pytest.ini` 的 `pythonpath = tests/unit` 也让它可被 spawn 子进程按名导入。
  放 `tests/integration/` 会暗示"跨组件集成"，与事实不符。

---

## 0. 结论速览（本卡要回答的 5 个问题，逐条）

### 问题 1：并发写会不会丢记录？——**本次全部实测配置下：没有丢**

| 场景 | 配置 | 预期 | 实际 | seq 缺口 | 重复 | 总数守恒 |
|---|---|---|---|---|---|---|
| 稳态并发（全部写者先就位、再同时开始） | 2 / 4 / 8 进程 × 200 条，两种启动模式，共 6 轮 | 5600 | **5600** | **0** | **0** | 是 |
| 边写边加入（join） | 8 / 16 进程 × 500 条 | 12000 | **12000** | **0** | **0** | 是 |
| 同时启动 + 边写边加入 | 16 进程 × 500 条（startup） | 8000 | **8000** | **0** | **0** | 是 |

- 每个库里 seq 集合都**精确等于** `1..总数`（探针同时报 `seq范围=(1..N)`、缺口 0、重复 0）；
- 分配侧（子进程自报的 seq）也 **0 重号**；
- `verify_chain()` 在单测里逐轮通过（哈希链接 + seq 连续性）。

⇒ **「这次没丢」升级为「在 2~16 进程、最多 8000 条、含边写边加入的配置下实测没丢」。**
但**不能**升级为「永远不丢」——见问题 5 的边界与未测范围。

### 问题 2：`lock.degraded` 的频率与条件？出现时**是否伴随丢行**？——**不伴随丢行**

- **稳态并发（本卡主矩阵）下出现 0 次**：2/4/8 进程 × 200 条、6 轮，
  `seq_conflict = 0`、`其它降级 = 0`、`留痕 = 0`。
- **触发条件（实测）**：不是"并发度高"，而是
  > **「某进程开始写（或开始收敛）时，别的进程已有『已分配、已写进共享预留日志、
  >  但尚未提交 DB』的记录」**。

  这条条件在两种情况下必然成立：
  1. **新实例在别人正在写时启动** —— 新进程 `_load_state` 读到"预留日志链头 > DB 链头"，
     于是置 `_journal_needs_drain = True`（`agent/audit/chain.py:1421-1460`），
     去收敛**别人还在途的**记录；
  2. **大批量写入期间** —— 写者的后台线程按 ~0.5s 轮询批量提交，任何时刻都可能有一批
     "已进日志、未进 DB"的记录。
- **频率随并发度非线性上升**（实测）：

  | 配置 | 结果 |
  |---|---|
  | 8 进程 × 500 条，`--mode join --stagger 0.1`，重复 5 次 | 4 次 0 冲突（收尾 ~1s）；**1 次 13 次 `seq_conflict`（收尾 242.84s）** |
  | 16 进程 × 500 条，`--mode join --stagger 0.1` | **40 次 `seq_conflict`**（收尾 244.44s） |
  | 16 进程 × 500 条，`--mode startup` | **111 次 `seq_conflict` + 20341 次 `ring_buffer_overflow`**（收尾 243.69s） |

- **出现降级时是否丢行：否。** 上面三档"降级风暴"里，DB 内仍是 `1..N` 精确连续、
  0 重复、总数守恒、16 个子进程退出码全 0。

⇒ **降级是「安全地退回」，不是「悄悄丢」。** 这一条正是本卡要钉住的焦点结论。

### 问题 3：`UNIQUE constraint failed` 的具体来源 —— **不是 seq 分配竞争，是 DB 写入侧的"重复插入"**

（完整代码级分析见 §3；一句话结论：）

> 不是"两个进程分到了同一个 seq"，而是"**同一个 seq 被两个进程各插了一次**"：
> 进程 B 的预留日志收敛（`_drain_journal`）把进程 A **尚在途**的记录也插进了 DB，
> A 稍后按自己的队列再插一遍 ⇒ `IntegrityError` ⇒ `seq_conflict`。

支撑证据：
- 分配侧实测 **0 重号**（`分配重复=[]`，全轮次）⇒ 不是 `_resolve_head` / 跨进程锁路径的锅；
- 冲突只在"预留日志头 > DB 头"的窗口下出现（join / startup 模式），稳态矩阵 0 次；
- 与源码注释里 L51 段自述的机制一致（"在途 seq"表 `_inflight_seq` **是进程内的**）。

### 问题 4：性能（并发 N 下的 p50 / p95 与吞吐）

稳态矩阵（append 侧，单位 ms；吞吐按"批次并发窗口"口径）：

| 模式 | 进程数 | p50(ms) | p95(ms) | 吞吐(条/s) | 单轮 wall(s) |
|---|---|---|---|---|---|
| startup | 2 | 0.105 | 0.214 | 6919 | 0.40 |
| startup | 4 | 0.112 | 0.224 | 6715 | 0.49 |
| startup | 8 | 0.108 | 0.227 | 6361 | 0.74 |
| warm | 2 | 0.101 | 0.197 | 7163 | 1.24 |
| warm | 4 | 0.109 | 0.259 | 6209 | 3.40 |
| warm | 8 | 0.111 | 0.239 | 6340 | 7.59 |

- **append 延迟几乎不随并发度退化**（p50 0.10~0.11ms，p95 0.20~0.26ms）——
  与"锁内只做内存计算 + 一次日志写"的设计一致；
- **吞吐 ~6200–7200 条/s**（append 阶段）；
- ⚠️ **但收尾（`flush()` / `close()`）会退化**：一旦进入冲突-收敛循环，
  单轮 wall 从 ~1s 涨到 **240s+**（16 进程 × 500 条）。即 **append 快、恢复慢**；
- ⚠️ **`flush()` 屏障在并发冲突下会「假阴性」**：返回 `False`（"未确认落盘"），
  而记录其实已经**被别的进程**写进 DB。原因：冲突分支执行
  `_write_failed_count += len(records)`，而 `flush()` 的判据是
  `_commit_count - _write_failed_count >= target`。实测证据：`[startup 16×500] flush=False`
  但 `实际=8000/8000`、0 缺口。

### 问题 5：并发安全边界（可引用的一句话）+ 没测到的范围

**可引用的完整性结论：**

> 「在**全部写者先就位、再同时开始追加**的稳态场景下，实测 2/4/8 进程 × 200 条共 5600 条，
> `seq` 全部 `1..N` 连续、无重复、总数守恒，`lock.degraded = 0`；
> 把并发推高并允许**进程边写边加入**（最高 16 进程 × 500 条 = 8000 条，含 111 次
> `seq_conflict` 与 20341 次 ring buffer 溢出）后，`seq` **仍无缺口、总数仍守恒** ——
> 即审计链在本卡测到的全部档位下**没有丢过行**；代价是收尾耗时从秒级恶化到 240s+。」

**可引用的可用性（性能）边界 —— 这条比完整性边界更紧、更有工程意义：**

> 「**≥8 个进程在别人正在写时加入**，实测开始出现 `seq_conflict` 降级；
> **16 进程**时进入降级风暴（40~111 次冲突 + 2 万次 ring buffer 溢出 + 240s 收尾）。
> 生产建议：同时写审计链的**实例数保持在个位数**，并避免『新实例上线』与『大批量写入』重叠。」

**没测到的范围（明确登记，不要当成已覆盖）：**
- **未测 32 进程及以上**；
- **未测长时浸泡**（例如 8 进程连续跑 30 分钟 / 反复启停实例的 soak）；
- **未测磁盘满 / 只读文件系统 / 预留日志不可写**（`SeqJournalError` 路径）；
- **未测进程被硬杀（kill -9）中途**——既有用例
  `tests/unit/test_concurrency_multi_writer.py` 覆盖，本卡**没有**重复验证；
- **未测网络文件系统（SMB/NFS）上的锁语义**（生产是同机磁盘）；
- **未测 `journal_enabled=False` 的降级配置下的并发**（那不是生产默认；那种配置下
  ring buffer 溢出就会**真的丢**，本卡未构造该用例）；
- **未在空闲机器上重测**：本机同时跑着 `python app_server.py` 与其它子代理的探针
  （12 逻辑核上有 3~4 个 python 进程），**延迟绝对值偏高**，趋势/完整性结论不受影响；
- **未做跨平台**（只有 Windows + `msvcrt` 锁；Linux `flock` 未测）。

---

## 1. 测量环境（版本钉住 + 外部写入者）

`agent/audit/chain.py` 在本卡执行期间**被其它子代理改过两次**（14:03:38Z、14:12:59Z），
故此处钉住本卡测量窗口内的实际版本：

| 文件 | sha256 | 说明 |
|---|---|---|
| `agent/audit/chain.py` | `5CD2FEDEBADA0080BB3D6969570B4AF77F431C5C695A5F059E97CE2596E0FB07`（178753 B，mtime 2026-09-25T14:12:59Z） | 本卡全部测量都在此版本之后 |
| `agent/utils/cross_process_lock.py` | `45D99C358630B6F5730903035FADBEDAE9F3E8DCC9AC483FA5071D5BBB5096DF` | 未变 |
| `agent/audit/seq_journal.py` | `45EAFC5438713AEA4C744312807B8B227A0269D8D7E5232044C2B8BBD981A079` | 未变 |
| `agent/audit/facade.py` | `19DEC32CD84ECBC13D5A68E58192B520AC422FAED3DDBA3D8B5915A68A6BA14B` | 与污染路径相关，见 §2 |

**外部写入者（重要，影响"只读断言"的判据选择）**：
本机常驻 `python app_server.py`（pid 7932，22:28 启动）会**持续正常写生产审计链与
`agent/data/approval_records.jsonl`**。实测后果：
- 生产库 sha256 在本会话内变过 3 次（`3E9CF2A4…` → `317CA05E…` → `AC732C9C…` → `EE3A46EC…`），
  其中至少一次 **size 不变而 mtime 变**（WAL 侧原地写）；
- 我第一版"文件级 (size, mtime) 只读断言"因此在 3 连跑里**红了 2 次**（假红）；
  `tests/conftest.py` 的 `_no_stray_approval_store` 守卫也因同一个服务写审批库报过 1 次 error；
  ⇒ 已改为**内容级 `action` 签名判据**（见 §2）。

环境：Python 3.12.0 / pytest 9.1.1 / SQLite 3.42.0 / Windows / 12 逻辑核。

---

## 2. 如何用**代码**保证"绝不压测生产库"（本卡铁律）

共 6 道闸，前 5 道在代码里，第 6 道是只读证据：

1. **`assert_tmp_db_path()`（父进程 + 子进程各调用一次；纵深防御）**
   - ① **绝对禁止面**（无条件拒绝，哪怕它同时位于某个临时根下）：
     生产库本体 `<repo>/data/audit/audit_chain.db`、`<repo>/data/audit/**`、`<repo>/data/**`；
   - ② **必须落在某个临时根之下**（环境变量 `TEMP/TMP/TMPDIR` + `tempfile.gettempdir()`
     + 调用方显式传入的 `tmp_path`）；
   - **实测踩到的坑（已修）**：`tests/conftest.py::_safe_tmp_directory` 把
     `tempfile.tempdir` 重定向到**项目内** `<repo>/.pytest_tmp`，而 pytest 的
     `tmp_path_factory` 在本进程启动时**已经**按真实系统临时目录算好 basetemp ⇒ 两者不等
     （实测 `tmp_path = C:\Windows\Temp\pytest-of-AdminWT\...` 而
     `gettempdir() = <repo>\.pytest_tmp`）。只认后者会让**合法的 pytest 临时目录**
     被误判，3 条用例直接 error；
   - 子进程侧的临时根**由父进程显式传入**，不依赖环境变量继承。
2. **环境变量隔离**：夹具把 `AUDIT_DB_PATH` / `AUDIT_ROOTS_PATH` 指向 `tmp_path`。
   **这不是可选项**：降级留痕走 `notify_degraded` → `agent.audit.facade.record`，
   而门面口径是 `db_path or os.getenv("AUDIT_DB_PATH") or DEFAULT_DB_PATH`
   （`agent/audit/facade.py:198`）—— 不设它，一次锁降级就把留痕写进**生产审计链**。
   **探针脚本把这两行放在 `import agent.*` 之前**（模块最顶部），因为顺序错了就来不及。
3. **`set_notify_hook` 计数钩子**：子进程把默认留痕（写审计链 + 事件层）换成纯计数，
   既精确计数，又杜绝"留痕自己取锁写库"给被测对象加噪声。
4. **每轮一个独立库目录**（实测踩到的坑）：首版让重试共用同一个库，重跑时新进程接着
   已有链尾继续分配，DB 里于是同时有两轮的记录（实测读到 `实际DB行=400` 而预期 200），
   断言口径被自己污染。
5. **护栏自证用例** `test_guard_rejects_production_db_path`：对生产库路径、生产审计目录、
   仓库内非临时路径、`<repo>/data/` 之下**逐一断言必须抛 AssertionError**，
   并对 `tmp_path` 断言必须放行 ⇒ 护栏**可被回归**，删松了就红。
6. **只读证据（见 §4.4）**：探针每次运行前后打印 `sha256 + size + mtime_ns + rows + max_seq`；
   测试文件另做**只读签名查询** —— 生产库里 `action LIKE 'conc.%'` 的条数必须为 **0**
   （`conc.write` / `conc.probe` 是本卡独有的 action，外部服务不会写）。

---

## 3. 问题 3 的代码级根因分析

### 3.1 主路径是**正确**的（先说清楚哪里没问题）

`AuditChain.append()`（`agent/audit/chain.py:1742`）的 seq 分配：
取跨进程锁（`locked(timeout, on_timeout="degrade")`）→ 锁内 `_resolve_head()`
（`chain.py:1525`）三源取大 `max(内存链头, 预留日志末行, DB 最大行)` → 分配 →
写预留日志（flush）→ 释放锁 → 后台线程批量入库。**实测分配侧 0 重号**，与设计相符。

### 3.2 冲突发生在**入库侧**

`_drain_journal()`（`chain.py:2109`）的候选判据是：

> 「预留日志里的 seq」−「**已在 DB 里的** seq（`_seqs_in_db`）」−「**本进程**在途的 seq（`_inflight_seq`）」

问题出在第三项：`_inflight_seq` 是**进程内**集合（其注释自述"队列里那些尚未提交的记录"），
**看不到别的进程队列里那一批**。于是：

```text
进程 A: append → 写预留日志(seq 11..20) → 入队（尚未提交 DB）
进程 B: 启动/收敛，读共享预留日志 → 看到 11..20
        「不在 DB（db_max=10）」「不在**我 B** 的在途表」⇒ 判定为"滞留" ⇒ 插入 11..20  [OK]
进程 A: 后台线程提交自己的 11..20 ⇒ UNIQUE constraint failed: audit_chain.seq      [FAIL]
```

触发这个窗口的**充分条件**就是问题 2 里那条：
`_load_state`（`chain.py:1421-1460`）在"预留日志链头 > DB 链头"时置
`_journal_needs_drain = True` —— 而"新实例在别人正在写时启动"让它**必然**成立。

### 3.3 为什么最终**没有丢行**

`_write_to_db_inner()`（`chain.py:1995`）的 `IntegrityError` 分支做了三件事：
`_note_degraded("seq_conflict: …")`、`_resync_seq()`、整批 `_buffer_failed()`
（ring buffer），并置 `_journal_needs_drain = True` 触发重放；
重放侧 `_drain_journal()` 会**先查 `_seqs_in_db` 再决定插不插**，重复的自然被跳过。
⇒ 冲突批的记录**本来就已在 DB**（被别的进程插入的那一份），所以"降级"不产生空洞。
这也是为什么 20341 次 ring buffer 溢出之下 `seq` 仍无缺口。

> ⚠️ 但这条安全网**依赖预留日志**：溢出被丢弃的是 **ring buffer 里的副本**，不是日志行，
> 所以没丢。**本卡没有构造"日志不可写/被压缩掉"的场景来证明这条推理**（见未验证项）。

### 3.4 最小复现（原文命令 + 原始输出）

```powershell
# 复现档（最可靠）：16 进程 × 500 条，错峰 0.1s，进程"边写边加入"
python scripts/audit_concurrency_probe.py --processes 16 --per-process 500 --stagger 0.1 --mode join
```

原始输出（节选，完整见 §4.3）：

```text
--- 运行 join / 16 进程 × 500 条 ...
    完成：实际=8000/8000 缺口=0 重复=0 seq冲突=40 耗时=244.44s
join  16    500     8000  8000  0     0     40       2         7     0.179    0.995    2257        244.44
    降级 ×40: seq_conflict: UNIQUE constraint failed: audit_chain.seq
    降级 ×2: close_with_pending
    留痕（节流后）: {'lock.degraded': 7}
```

更便宜的复现档（间歇性，5 次里 1 次命中）：

```powershell
python scripts/audit_concurrency_probe.py --processes 8 --per-process 500 --stagger 0.1 --mode join
```

```text
rep1 exit=0 unique_lines=0  ::  完成：实际=4000/4000 缺口=0 重复=0 seq冲突=0  耗时=0.99s
rep2 exit=0 unique_lines=0  ::  完成：实际=4000/4000 缺口=0 重复=0 seq冲突=0  耗时=0.99s
rep3 exit=0 unique_lines=0  ::  完成：实际=4000/4000 缺口=0 重复=0 seq冲突=0  耗时=1.14s
rep4 exit=0 unique_lines=14 ::  完成：实际=4000/4000 缺口=0 重复=0 seq冲突=13 耗时=242.84s
rep5 exit=0 unique_lines=0  ::  完成：实际=4000/4000 缺口=0 重复=0 seq冲突=0  耗时=1.13s
```

**与 D3 现场观察的关系（诚实口径）**：D3 在生产链看到 `seq=72302` 的一条
`lock.degraded / seq_conflict` —— 与本卡复现出的**是同一类现象**（入库侧重复插入），
但**不能**证明生产那次就是这个原因：生产链由 `app_server.py` 等常驻进程写，
本卡没有生产侧的进程启停时间线可比对。**登记为"同一机制、未取得生产侧证据"。**

### 3.5 可能的修复方向（**本卡不实现**，只登记）

1. **让"在途"跨进程可见**：把 `_inflight_seq` 的语义提升为跨进程（写进锁元数据槽 /
   预留日志的 owner 字段），收敛时跳过"活着的 owner 尚未提交"的行。
2. **按 owner 过滤收敛**：预留日志行里记 `owner_pid`（`cross_process_lock` 已有
   `_pid_alive`），收敛只回收**自己**或 **owner 已消失**的行；对活着的 owner 不代插。
3. **收紧 `_load_state` 的收敛触发**：把"日志头 > DB 头"细化为"日志头 > DB 头 **且**
   日志里存在 owner 已消失的行"。
4. **`INSERT OR IGNORE` + 显式留痕**：让重复插入幂等，冲突不再进 ring buffer、
   不再计入 `_write_failed_count`；但必须保留"重号必 ERROR 留痕"的断言，
   否则会把真实的重号一起吞掉（**有掩盖风险，优先级低于 1/2**）。
5. **修 `flush()` 屏障口径（与 1/2 独立，可单独做）**：冲突分支在扣减
   `_write_failed_count` 之前，用 `seqs_in_db` 复核"这批其实已在库"的情形，
   避免"记录已落盘却报未落盘"的**假阴性**（问题 4 的 ⚠️）。

---

## 4. 验收命令与**原始输出**

### 4.1 `python -m pytest tests/unit/test_audit_chain_concurrency.py -q` —— 连跑 3 次

```text
=== pytest RUN 1 exit=0 wall=7.97s :: ============================== 4 passed in 3.57s ==============================
=== pytest RUN 2 exit=0 wall=7.57s :: ============================== 4 passed in 2.82s ==============================
=== pytest RUN 3 exit=0 wall=6.95s :: ============================== 4 passed in 2.55s ==============================
```

（此前另有一组同口径 3 连跑：`4 passed in 2.54s / 2.61s / 2.68s`，exit 均为 0，
`UNIQUE constraint` 打印行数 3 次均为 0。⇒ 两轮共 6 次全绿，**不 flaky**。）

**为不 flaky 做的处理（逐条）**：
1. **就绪栅栏**（最关键）：全部子进程先构造完链并报"就绪"，父进程才放发令枪 ⇒
   任何进程开始 append 时预留日志都是空的。**不这样做的实测代价**：后构造的进程在
   `_load_state` 撞上"日志头 > DB 头"（别人的在途记录）⇒ 触发 §3 的重复插入 ⇒
   单次用例从 2.4s 涨到 43s，3 连跑红 2 次。那个场景由探针 `--mode join` 专门覆盖，
   本用例钉的是**稳态并发追加**契约，两者**刻意分开**，CI 才不会变成随机红。
2. **负载完全确定**：进程数/条数是常量，不用随机数 ⇒ 不需要固定 seed。
3. **结果通道用独占文件**（一进程一文件），父进程只做"读文件 + `join(timeout)`"，
   不用 `Queue`/pipe，不受管道缓冲或沙箱 `stdio: pipe` 限制影响。
4. **全链路显式超时**（`_CHILD_DEADLINE_S=120` / `_FLUSH_TIMEOUT_S=20` /
   `_GATE_TIMEOUT_S=60`），且都**远小于** pytest 的 `--timeout`：
   `pytest.ini` 用 `--timeout-method=thread`，超时会 `os._exit(1)` **杀掉整个 pytest 进程**。
   （实测踩到的边界：子进程收尾会调用**两次**带超时的屏障 `flush` + `close`，
   所以上界必须 > `2×_FLUSH_TIMEOUT_S`，否则把"收尾慢"误判成"挂死"，实测退出码 -15。）
5. **重试口径克制**：只在"子进程异常 / 退出码非 0 / 未退出 / 未全部就绪"这类**瞬态**失败上
   重跑一次，且**每次重跑用独立的新库**；**发现 seq 缺口或重复立即失败，绝不重试**。
6. **不拿 `flush()` 返回值当瞬态失败**：实测并发冲突下它是**假阴性**（见问题 4），
   拿它重试只会白等；真正判据是库内行数 / seq 连续性 / 无重号。

### 4.2 `python scripts/audit_concurrency_probe.py --processes 2 4 8 --per-process 200`

```text
模式     进程  每进程  预期  实际  缺口  重复  seq冲突  其它降级  留痕  p50(ms)  p95(ms)  吞吐(条/s)  耗时(s)
-------  ----  ------  ----  ----  ----  ----  -------  --------  ----  -------  -------  ----------  -------
startup  2     200     400   400   0     0     0        0         0     0.105    0.214    6919        0.40
startup  4     200     800   800   0     0     0        0         0     0.112    0.224    6715        0.49
startup  8     200     1600  1600  0     0     0        0         0     0.108    0.227    6361        0.74
warm     2     200     400   400   0     0     0        0         0     0.101    0.197    7163        1.24
warm     4     200     800   800   0     0     0        0         0     0.109    0.259    6209        3.40
warm     8     200     1600  1600  0     0     0        0         0     0.111    0.239    6340        7.59
```

```text
[startup 2×200] seq范围=(1..400)  分配重复=[] 退出码=[0, 0] 未退出=0 flush=True 批次并发窗口=0.06s 延迟样本=400
[startup 4×200] seq范围=(1..800)  分配重复=[] 退出码=[0, 0, 0, 0] 未退出=0 flush=True 批次并发窗口=0.12s 延迟样本=800
[startup 8×200] seq范围=(1..1600) 分配重复=[] 退出码=[0]*8 未退出=0 flush=True 批次并发窗口=0.25s 延迟样本=1600
[warm 2×200]    seq范围=(1..400)  分配重复=[] 退出码=[0, 0] 未退出=0 flush=True 批次并发窗口=0.06s 延迟样本=400
[warm 4×200]    seq范围=(1..800)  分配重复=[] 退出码=[0, 0, 0, 0] 未退出=0 flush=True 批次并发窗口=0.13s 延迟样本=800
[warm 8×200]    seq范围=(1..1600) 分配重复=[] 退出码=[0]*8 未退出=0 flush=True 批次并发窗口=0.25s 延迟样本=1600

 总轮次=6 合计预期=5600 合计实际=5600
 seq 缺口总数=0  重复 seq 总数=0  缺失（预期-实际）=0  seq_conflict 降级总数=0
 ⇒ 本轮矩阵内**未发现丢行**：seq 全部连续、无重复、总数守恒。
```

### 4.3 高并发档原始输出（问题 2 / 问题 3 的证据来源）

```text
模式     进程  每进程  预期  实际  缺口  重复  seq冲突  其它降级  留痕  p50(ms)  p95(ms)  吞吐(条/s)  耗时(s)
-------  ----  ------  ----  ----  ----  ----  -------  --------  ----  -------  -------  ----------  -------
join     8     500     4000  4000  0     0     0        0         0     0.149    0.280    4380        1.17
join     16    500     8000  8000  0     0     40       2         7     0.179    0.995    2257        244.44
startup  16    500     8000  8000  0     0     111      20344     15    0.100    0.753    3913        243.69

[startup 16×500] seq范围=(1..8000) 分配重复=[] 退出码=[0]*16 未退出=0 flush=False 批次并发窗口=2.04s
    降级 ×20341: ring_buffer_overflow
    降级 ×111:  seq_conflict: UNIQUE constraint failed: audit_chain.seq
    降级 ×3:    close_with_pending
    留痕（节流后）: {'lock.degraded': 15}
```

（`ring_buffer_overflow` 的完整 reason 文本形如
`ring_buffer_overflow（容量 2000，已丢弃最旧一条；丢弃累计 N）`，
表格为可读性截断到方法名。`flush=False` 的含义见问题 4 的假阴性说明。
`ring_buffer_overflow` 计的是**重试风暴的次数**，不是"丢了 N 条"——同轮 `实际=8000/8000`。）

### 4.4 只读断言：生产库 `sha256 + mtime` 前后对照

对照窗口 = **3 次 pytest 连跑 + 一次完整探针矩阵（6 轮 5600 条）**：

```text
[BEFORE] len=55107584 mtime_utc=2026-09-25T14:31:53.1489769Z sha256=EE3A46ECC5343CECB67FC8030D5976F3F9549C4194C920C13507C56F30EA6B7F
=== pytest RUN 1 exit=0 wall=7.97s :: 4 passed in 3.57s
=== pytest RUN 2 exit=0 wall=7.57s :: 4 passed in 2.82s
=== pytest RUN 3 exit=0 wall=6.95s :: 4 passed in 2.55s
=== probe exit=0
[AFTER]  len=55107584 mtime_utc=2026-09-25T14:31:53.1489769Z sha256=EE3A46ECC5343CECB67FC8030D5976F3F9549C4194C920C13507C56F30EA6B7F
```

**逐字节相同**（len / mtime_utc / sha256 三项全等）。

探针自己也打印同一对照（另一次运行）：

```text
  before: sha256=ee3a46ecc5343cec size=55107584 mtime_ns=1790346713148976900 rows=72625 max_seq=72625
  after : sha256=ee3a46ecc5343cec size=55107584 mtime_ns=1790346713148976900 rows=72625 max_seq=72625
  结论  : 未变化（探针全程只写了临时目录）
```

**内容级佐证（不受外部写入干扰的判据）**：

```text
rows= 72625  maxseq= 72625  conc_rows= 0  lockdeg= 195
```

- `conc_rows = 0` ⇒ 生产审计链里**没有本卡写过的任何一条记录**
  （`action LIKE 'conc.%'`，本卡独有命名空间）；
- `lockdeg = 195` ⇒ 与 D3 复核时记录的 195 条 `lock.degraded` **一致**，
  即本卡运行期间**没有新增**生产侧 `lock.degraded`。

> **诚实说明**：生产库在本会话内**确实变过**（72602 → 72625 行、sha256 变 3 次），
> 那是本机常驻 `python app_server.py` 的正常写入，**不是**本卡造成的 ——
> 判据见上两条（`conc_rows=0` + §2 的路径护栏）。这也正是我把测试里的"文件级只读断言"
> 改成"`action` 签名判据"的原因（文件级判据会把外部写入打成假红，实测 3 连跑红 2 次）。

---

## 5. 发现清单（本卡新增）

| # | 严重度 | 发现 | 证据 | 结论 |
|---|---|---|---|---|
| F1 | **中** | 预留日志收敛会**代插别的活进程在途的记录** ⇒ `UNIQUE constraint failed` ⇒ 降级 | §3.4 最小复现 | 实测**不丢行**；但降级风暴会把收尾耗时打到 240s+ |
| F2 | **中** | `flush()` / `close()` **持久化屏障假阴性**：冲突把 `_write_failed_count` 扣成"未落盘"，而记录其实已在 DB | `[startup 16×500] flush=False` 但 `8000/8000`、0 缺口 | 调用方会白等满超时（单进程最坏 2× 超时） |
| F3 | 低（正向） | 稳态并发下 append 延迟与吞吐**几乎不随并发度退化** | §4.2 矩阵（p50 0.10~0.11ms，6200~7200 条/s） | 写路径设计达标 |
| F4 | 低 | `ring_buffer_overflow` 是**重试风暴的度量**，不是"丢了 N 条" | 16×500 下 20341 次溢出而总数守恒 | 报告口径上不要把它读成丢行数 |
| F5 | 观察 | 常驻 `app_server.py` 写入时，长耗时用例会触发 `tests/conftest.py` 的 `_no_stray_approval_store` 守卫 | §1 外部写入者 | 环境噪声，非本卡产物缺陷 |

---

## 6. 未验证项（显式登记，不当作已覆盖）

1. **未验证"永远不丢"** —— 本卡只覆盖问题 5 列出的档位；**没有找到丢行边界**，
   也没有证明不存在丢行边界。
2. **未验证修复方案**（§3.5 的 1~5 全是方向，**本卡不实现**，属验证卡边界）。
3. **未验证"ring buffer 溢出时记录一定还在预留日志/DB 里"的一般性** —— §3.3 是代码推理，
   本卡未构造"预留日志同时不可用/被压缩"的用例来证伪。
4. **未验证跨平台**（Linux `flock` / macOS）。
5. **未验证 `_allocate_via_db` 兜底路径在"DB 头落后于预留日志头"时是否会分到重复 seq** ——
   `_allocate_via_db`（`chain.py:1850`）只读 **DB 链头**、不读预留日志，
   **代码上看起来存在重号风险**，但本卡**没有把它复现出来**（需要把 `seq_lock_timeout`
   压到能稳定触发锁超时，本卡未做）。**登记为可疑点，不是结论。**
6. **未验证延迟绝对值**（本机有 3~4 个 python 进程并发运行，含 `app_server.py`
   与其它子代理的探针）—— 只保证趋势与完整性结论。

---

## 附录 A：本卡如何"只写临时目录"（横向索引）

- **单测**：`conc_env` 夹具 → `assert_tmp_db_path(db, allowed_roots=_temp_roots([tmp_path]))`
  （父进程）+ 子进程 `assert_tmp_db_path(job["db"], allowed_roots=job["temp_roots"])`；
  外加 `AUDIT_DB_PATH` / `AUDIT_ROOTS_PATH` 指向 `tmp_path`，与 `set_notify_hook` 计数钩子。
- **探针**：模块最顶部 `mkdtemp` 后**先**写 `AUDIT_DB_PATH` / `AUDIT_ROOTS_PATH`
  **再** import `agent.*`；每轮再 `assert_tmp_db_path` 一次；每轮独立子目录。
- **回归**：`test_guard_rejects_production_db_path` 让护栏本身可被测试。

## 附录 B：本卡修改过的既有文件

**无。** 本卡只新建上述 3 个文件（铁律第 1 条）。
`agent/audit/chain.py` 等既有文件虽然在本卡时间窗内被改动过（见 §1），
但那是**其它子代理**的并行改动，与本卡无关；本卡的全部测量结论都钉在 §1 的版本哈希上。
