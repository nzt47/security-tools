# 实施期独立复核记录（主审计维护）

> 更新：2026-09-25 19:0x｜复核者：主审计（不采信子代理自述，全部独立复跑）

## 复核方法

1. 开工前对全部 16 个目标文件做 **sha256 基线快照**（`_baseline_hashes.json`）
2. 每张卡交付后，**独立复跑它的验收命令**，并与它报告里的原始输出对拍
3. 对关键不变量做**端到端实测**（读真实数据库 / 真实注册表），不依赖单测
4. 发现失败或副作用 ⇒ 立即把**原始输出**回传该卡，要求按实现缺陷修（不许放宽断言绕过）

## 一、已独立验证通过

### D2 技能启停入审计链 —— 通过

| 复核项 | 我的实测 |
|---|---|
| `set_enabled` 落链 | action=`skill.registry.set_enabled`，subject=`skill:memory_summary` |
| `toggle` 落链 | action=`skill.registry.toggle`（独立 action 名） |
| 返回契约未变 | `{"ok":true,"id":...,"enabled":...,"track":"file_track"}` |
| 状态可还原 | 测后 `memory_summary` = enabled，与原值一致 |
| **无重复留痕** | 按 (subject, ts) 分组查重 ⇒ **NONE**；8 条记录一一对应 8 次调用 |
| 两条轨道都落链 | main=`skill:code-observability`、file_track=`skill:scripted-selftest` |
| 新测试 | 独立跑 `test_skill_registry_audit.py` → **9 passed** |
| 基线对照 | 修复前 `skill.registry.*` = **0 条**（我实测） |

### B1 工具数口径统一 —— 通过

| 复核项 | 我的实测 |
|---|---|
| 新测试 + 既有契约测试 | 独立跑 65 项 → **65 passed / 0 failed** |
| 既有 `test_tools_prompt_alignment.py` 零修改通过 | ✅（证明未破坏 `align_system_prompt_with_tools` 契约） |
| token 口径对拍 | 子代理实算 全量 91 条 = **18,311 token**，与我先前独立测得的 18,311 **逐位一致** |
| 上线口径 | 主线 26 条裁剪后 **6,757 token**；`reprchars=15,726` 与生产日志「约 15726 字符」逐字吻合 |
| 旧注释「13k」错因定位 | 13,416 = 字符÷3，对 18,311 低估 **29%** |
| 全仓 grep | `共 86 个` 在 `agent/*.py`、`plugins/*.py`、`memory/*.py`、`app_server.py`、`tests/**` → **0 命中** |

**实现要点（我读码确认）**：`tools_prompt_guard.render_tool_advert_line(tool_defs)` 的**数字与名字来自同一个入参**；
`resolve_dispatch_tool_defs()` 的三步与编排器下发链同源，末步刻意用 `get_tool_defs()` 本身；
`count_tool_defs_tokens()` 在 tiktoken 不可用时**返回 None 而不是退化成字符÷3**（正是审计点名的失真来源）。

**已知边界（子代理主动声明，我确认诚实）**：渲染发生在 `orchestrator.py:3322`，而下发集定稿在 `:3458`（晚约 136 行），
故「无激活主线 + 开启智能选择」这一条路径仍可能不等。生产默认有激活主线、不进入该分支 ⇒ 线上已一致。
已登记为待办 **B1-TODO-1**：把选择块上移并改传 `tool_defs=_tool_defs`（该入参已实现且有单测）。

### A1 启动除险 —— 代码与端到端均通过

| 复核项 | 我的实测 |
|---|---|
| 新增测试 | 独立跑 `test_startup_no_gap.py` → **15 passed**（首轮曾 4 failed，见第二节） |
| 启动时序 | `app_server.py:1859-1862` 确认 `cleanup_port_listeners` **已从启动早期移走** |
| 就绪门实现 | `_startup_preflight()` 用 Flask `test_client` 进程内自证 `/api/health`（不 bind，因端口仍被旧实例占着） |
| 不变量落地 | `guarded_startup`：①端口空闲 ⇒ 不跑探针、不杀任何进程 ②有旧实例且探针失败 ⇒ **一个进程都不杀** + 退出码 3（失败关闭） |
| 端到端 | 实测 `python app_server.py` 冷启动 → 5678 监听（PID 1140）、`/api/health` **HTTP 200**、内存 942 MB |

**设计评价**：探针异常一律视为「未就绪」（失败关闭）是对的；「端口空闲就不付探针代价」也是对的。
**残留窗口（已记录，非缺陷）**：探针 200 与实际 bind 成功之间仍有秒级窗口 —— 若新实例在此窗口内死掉，旧实例已被清理。
这个窗口被从「55-85 秒的整个启动期」压缩到「秒级」，是实质改善，但**不是零窗口**，报告与注释都应如实表述（不称『已消除』）。

### C1 索引巡检脚本 —— 通过

我独立跑 `scripts/verify_index_drift.py`，退出码 **1**，如实报出：

```
[T1] tool_index=90  YAML可见=90  内容漂移=0
[S1] cache.json=23  文件轨=23  缺失=0  多余=0  hash失效=0
[S2] 注册表并集=30（主轨=22 文件轨=23 交集=15）  索引可召回=23  缺口=7
[S3] 落盘向量库=8  注册表并集=30  未覆盖=22  覆盖率=26.7%  最新条目=2026-07-23 17:07:59（64.1 天前）
FAIL: S2 ... 7 项：code-observability, engineering-test-delivery, frontend-state-sync,
      global-core-principles, self-explanatory-ui, skill, testing-anti-patterns
FAIL: S3 ... 未覆盖 22 项
结论：FAIL（FAIL=2 WARN=2）
```

⇒ 【**2026-09-25 19:2x 复核撤回**】S3（向量未覆盖 22）是真话；但 **S2 已由 FAIL(7) 变成 PASS(0)，那是假绿** —— 见本文件末节。
注意 T1 用的是**内容比对**而非 mtime（审计已证明 mtime 判据全是假阳性）。

## 二、我抓到并要求返工的问题

### A1 首轮：4 个用例失败，其中一个是生产代码缺陷

我独立跑 test_startup_no_gap.py 得到 4 failed / 41 passed。关键一条：

    agent\server_port_guard.py:350: in cleanup_port_listeners
        "kinds": sorted({r["target_kind"] for r in records}),
    E   KeyError: target_kind

子进程收割路径产生的 record 没有 target_kind 键 => 真实启动路径只要发生一次子进程收割就会抛 KeyError，
而它正处在 app_server 启动流程中 —— 与本卡要消除的『零服务窗口』是同一条路径。
我已把原始输出回传，要求按实现缺陷修、不许放宽断言绕过。

**A1 的返工响应（我采信其结论，且它做得比要求更彻底）**：
- 它把我的栈里 `:350` 识别为**修复前行号**（修复后在 `:375`），并给出根因复现脚本：4 条失败里 **2 条是实现缺陷**
  （汇总日志对**混合记录**全量取 `target_kind`，而 `reap_child` 记录无此键）、**2 条是测试自己写错**
  （`serve_fn` 从不记 events 却断言末尾有 serve；`self_pid` 写成目标 PID 本身）—— 这个区分是对的，我确认。
- **断言强度未降低**（这是我明确要求的）。
- **超出要求的面修**：它指出旧版 `guarded_startup` 是裸调用，异常发生在 kill 之后、bind 之前 ⇒ 正是零服务窗口。
  于是把清理调用整段兜底，只记 `startup.port_cleanup.failed` 留痕并**继续尝试 bind**。这确实是我没要求但正确的加固。
- 复跑：**17 passed**（含真进程 Job Object 用例），加兜底后重跑 A→B 交接窗口 **0.32 s**，停服后无残留进程。

### C1 首轮：新测试超时挂死（**已修复**）

我独立跑 test_retrieval_silent_failures.py，pytest 超时中断：

    tests/unit/test_retrieval_silent_failures.py:310: in test_content_change_triggers_reencode
        adapter.ensure_indexed()
    agent/skills_mgmt/vector_adapter.py:741: in ensure_indexed
        int(total_in_repo * _CONTENT_HASH_FULL_REBUILD_DIRTY_RATIO))
    agent/skills_mgmt/vector_adapter.py:900: in _remove_skill_vector
        return updated
    +++ Timeout +++

我的初步定位（仅供 C1 参考，未最终确认）：vector_adapter.py:221 是 threading.Lock()（非可重入）。
ensure_indexed 在 :697 已持有该锁，而 upsert 在 :895-896 先调 _remove_skill_vector（:904 又取同一把锁）再调 ensure_indexed。
若期间有任何路径在已持锁时再取一次，就会死锁（表现为 future.result(timeout) 永不返回 => 超时）。
=> 与审计发现的 P1（2 秒超时被 with ThreadPoolExecutor 抵消）属于同一类无界耗时，必须当实现缺陷修。
我已要求 C1：① 定位 :741-:900 为何不返回 ② 若测试真去加载 BGE-m3 必须打桩 ③ 确认或否认 chromadb 重复 id 静默跳过那条外部证词。

**C1 的返工响应（根因与我的初步定位一致，处理更彻底）**：
- 真根因确认：`_lock` 是**普通 `threading.Lock()`（不可重入）**，`ensure_indexed` 在 `with self._lock` 内调用 `_remove_skill_vector()`，
  后者**又**取同一把锁 ⇒ **静默自死锁**（不抛异常、不打日志、永不返回）。
- **修法不是加超时，而是把 `_lock` 改为 `RLock`** ⇒ 该类缺陷**结构上不可能再现**（这是我更认可的修法）。
- 并加两道 watchdog 用例（join 20s / join 10s）使挂死变成**有界失败**；文件级 `timeout(60)`；
  新增 autouse tripwire 断言「重型后端初始化从未被调用」，且**自检验证该 tripwire 有效**（故意触发得到预期 AssertionError）。
- 复跑：**24 passed / 8.07s**（连跑 3 次 8.07/26.45/8.03 全绿）；我挂死的那条用例现在 **0.03s**；
  **全部用例只用 `_FakeModel`，无一条真加载 BGE-m3 或真编码**（满足我「必须打桩」的要求）。
- 它同时**确认**了 chromadb 重复 id 静默跳过这条外部证词（隔离实测 `2nd add raised: None`、内容原样未变），
  并证明 C1 已覆盖该场景 ⇒ **G1-B 不要重复实现**。

## 三、本轮累计产出

| 项 | 数量 |
|---|---|
| 修改的既有文件 | 14 |
| 新增文件 | 7（4 个测试 + 3 个脚本/看门狗） |
| 代码增量 | +2,090 / -223 |
| 已独立验证通过 | **5 张卡全部通过**（A1、B1、C1、D1、D2） |
| 待返工 | **0 项**（A1 的 4 失败与 C1 的挂死均已修复并复跑通过） |

## 第 1 批终态（2026-09-25 19:1x）

| 项 | 值 |
|---|---|
| 交付并独立复核通过的卡 | **5/5**（A1、B1、C1、D1、D2）+ 1 只读对账（G1-A） |
| 最终整体回归 | **98 passed / 0 failed**（5 文件）；更宽回归 152 passed / 0 failed（7 文件） |
| 工作区改动 | 14 个既有文件（+2,150/−228）、8 个新文件（5 测试 + 3 脚本/看门狗） |
| 提交状态 | **全部未提交**（按要求不 commit）；`git stash` 5 条与本次无关（TASK-06/S2-01/develop 期遗留） |
| 环境 | 无 Python 进程、5678 已释放、`data/` 工作区干净 |
| 报告 | 17 份（主报告 + Q1–Q8 + A1/B1/C1/D1/D2 + G1A + FINDINGS + VERIFICATION_LOG） |

**B1 的授权追加改动已复核**：`tests/unit/test_tools_prompt_alignment.py:50` 的陈旧夹具改为新口径，`git diff --numstat` = 1/1（断言逻辑一行未动）；
其判定依据我确认成立 —— 该常量被 17 处引用，但**无一处依赖具体文案或数字 86**（`grep "全部已启用|assert .*86"` = 0 命中）；
改后 65 passed。指定范围 grep `共 86 个` **rc=1（0 命中）**，全仓仅剩 2 类历史证据（未跟踪的探针 JSON + 一份历史分析文档）—— **不应删除**。

## 第 2 批派发前的必办事项

1. **`F1b` 必须最先完成**（`update_meta` 静默删字段/注释）——否则 G1 的描述改造会被技能启停逐次抹掉。
2. **`G1-B` 开工前先跑 G1-A 报告第 14 节的 4 条重新对账命令** —— 行号已因并发改动漂移
   （如 `registry.py` 153-193 → 205-245、`vector_adapter.py` 已 +612 行）。
3. **不要重复实现**：G1-A 与 C1 均已确认 chromadb 1.5.9 的「重复 id 静默跳过」已由 C1 修好；
   G1-B 只需做 V-verify / V-regress / V-guard 三件事。
4. **本次会话末尾大量子代理回报属重复播报**（同一份报告被送达两次），未产生新信息；
   为避免继续放大上下文开销与产出重复实现，**第 2 批只在明确顺序下派发**。

## A1 关键断言的独立实测（主审计亲测，非采信报告）

### 看门狗真的能拉起服务 —— 已用**磁盘留痕**证实

A1 报告里的 18:51 那次非 dry-run 重启**在其默认留痕文件里查不到**（`logs/watchdog_yunshu.jsonl` 当时只有 18:59 的 dry-run 三条），
原因查明：脚本是追加写（`open(path, "a")`），A1 那次用了 `--log` 自定义路径（可能落在 %TEMP%）。
**该文件被 .gitignore:32 忽略，不污染仓库**（已 `git check-ignore` 确认）。

⇒ 我**自己跑了一次非 dry-run 的 `--once`**，留痕落在默认文件里，证据完整：

```
{"ts":"2026-09-25T19:17:57+08:00","event":"health_check","healthy":false,
 "detail":"URLError: [WinError 10061] 目标计算机积极拒绝","consecutive_failures":1,
 "fail_threshold":3,"listening_pids":[],"old_pid":null,"new_pid":20404,
 "action":"restart","dry_run":false,"watchdog_pid":1516}
{"ts":"2026-09-25T19:18:47+08:00","event":"restart_result","reason":"cold_start_probe",
 "old_pid":null,"new_pid":20404,"ready":true,"waited_s":50.3,"dry_run":false}
{"ts":"2026-09-25T19:18:47+08:00","event":"watchdog_stop","reason":"once_mode",...}
```

并且我**独立确认了服务真的起来了**（不是只看脚本自述）：
- `Invoke-WebRequest /api/health` => **HTTP 200**
- `127.0.0.1:5678` LISTENING，OwningProcess = **20404**（与留痕里的 new_pid 一致）
- 冷启动耗时由留痕给出：**50.3 s**

### 停服后无遗留子进程 —— 已实测

```
taskkill /F /PID 20404  => SUCCESS
children of 20404 after stop => no orphans
5678 => free
any app_server process => none
```

⇒ **A1 的两条核心断言（看门狗可恢复、强杀不留孤儿）均由主审计独立复现，证据在默认留痕文件里可查。**
我在上一轮只验证了它的代码与单测；**本轮补上了运行时的端到端证据**，这是本卡最关键的交付价值所在。

### 一处方法学提醒

A1 报告中的「看门狗重启成功」当时**没有留下默认路径的证据**（用了自定义 --log）。
这不是缺陷，但说明：**凡是「我验证过了」的声明，留痕必须落在默认位置**，否则事后无法独立复核。
已作为派发规则的一部分（第 2 批起要求：验证性运行的留痕写默认路径，或明确贴出所用路径的内容）。

## C1 复核撤回：其 S2 门是**假绿**（我上一轮采信错误）

**我上一轮写的**：「C1 索引巡检脚本 —— 通过 …… 它真的能报出审计里发现的那两个缺口（7 与 22）」。
**这句现在只对一半**：S3（向量未覆盖 22）是真话；**S2 已从 FAIL(7) 变成 PASS(0)，而那是假绿。**

### 事实（我 grep 全仓确认，非推测）

- `main_track` 这个键**只在 `agent/skills_mgmt/index_cache.py` 内部出现**（29 处，全在同一文件）。
- `index_cache.py:237 get_main_track_metadata()` 的**生产调用方 = 0** ——
  全仓唯一引用是 C1 自己的测试 `tests/unit/test_retrieval_silent_failures.py:591`。
- 检索真正用的索引源：`vector_adapter.py:728` 与 `loader.py:551/685/741/785/1362` 全部读 `fs.load_metadata_index()`。
- 我独立实测：`load_metadata_index(refresh=True)` 返回 **23** 条；
  **7 个主轨独有技能（code-observability / engineering-test-delivery / frontend-state-sync /
  global-core-principles / self-explanatory-ui / skill / testing-anti-patterns）一个都不在里面**。

⇒ **结论：这 7 项在生产上仍然不可召回。** C1 的修(5) 让它们「被持久化进 cache.json 的 main_track 分区」，
但**没有任何代码把该分区并回检索路径**。

### 假绿的机制（值得单独记一笔）

`scripts/verify_index_drift.py:245`：

    recallable = set(skills) | set(main_track_index)

第 19 行注释写「元数据索引**可召回集合** = cache.json 的 skills ∪ main_track」。
这是**假设混淆**：`main_track` 是 cache.json 的**存储分区**，**不等于可召回**。
脚本断言的是**磁盘上的派生工件**，而不是**代码路径**。

⇒ 于是出现：S2 由 FAIL(7) 变 PASS(0)，**但生产行为零变化**。

### 为什么这条比原缺陷更重要

**原来的 FAIL 是真话（虽然难听），现在的 PASS 是假话。**
这正是本项目最核心的病灶 —— 「**声称值 vs 实测值**」—— 在**审计方自己交付的工具里**复现了一次。
如果没有这一步交叉核对（用生产入口而不是脚本自报），这个假绿会被写进 V1.1 并长期误导后续实施。

### 已下发的处置（C1 返工中）

1. S2 判据改为**「代码路径可达」**（调 `load_metadata_index()` 实测差集），当前应如实 FAIL(7)。
2. 报告里显式更正 S2 结论，**不得**保留 PASS(0)。
3. 补一条测试把「主轨独有技能当前不可召回」这个**事实**测出来（将来谁接上了，测试会红，逼人更新判据）。
4. 核实 `main_track` 是否为其引入、以及是否属**未接线**，标注清楚。

### 我给出的方法论教训（已写入派发规则）

> **巡检脚本的判据必须跑「生产入口」，不能断言「派生工件」。**
> 若一个门检查的是 cache/索引/清单这类**派生文件**，它必须同时证明
> 「该文件里的内容**真的会被生产代码读到**」，否则它测的是自己的假设。

这条与审计原有的发现同源但更进一步：
审计已指出 `sync_tool_index.py --check` **不比对索引**、`compare_skills_legacy_vs_repo.py` **SKIP 视为 ALL_MATCH**；
现在再加一条：**「断言派生工件」也是同一类盲区**。

## D1 终态与两处更正

### D1 的性能证据（比我此前的 20.9 ms 更精确）

D1 用只读连接 + `perf_counter` 取 7 次中位数，并给了**同进程同库对照**：

| 指标 | 修复前 | 修复后 | 倍数 |
|---|---|---|---|
| `facade.recent(50)` | **1239.55 ms** | **2.49 ms** | **498×** |
| `chain.entries()` 全表 + 切片（同进程对照） | — | 1680.67 ms | **674×** |
| `stats(verify=False)` | 1751.93 ms | 32.48 ms | 54× |
| `snapshot()` | 1725.73 ms | 26.01 ms | 66× |
| `count()` | 373.99 ms | 3.64 ms | 103× |
| `seq_range()` | 370.02 ms | 7.61 ms | 49× |
| `chain_head()` | 753.35 ms | 16.53 ms | 46× |

**我独立测得的生产形态是 20.9 ms**（含 writer + 预留日志合并），与 D1 自报的 19.96 ms 一致；
因此「<50 ms」目标**达成**，但**注意口径差异**：纯读 2.49 ms vs 生产形态 ~20 ms。
⇒ 后续若有人引用「2.5 ms」，应说明那是**不含写路径合并**的纯读口径。

### 更正 1：返回顺序 —— **我的任务卡写错了**

我在 C1/D1 任务卡里写的是「返回值语义不变（**最新的在前**）」。D1 实测指出**实际契约是 seq 升序、最新在末尾**，
并以既有用例 `test_audit_facade.py::test_recent_returns_tail_in_seq_order`（断言 `["act3","act4"]`）为证。

⇒ **D1 的选择是对的**：它按「不改动返回值语义」保留升序并固化为断言，**没有**因为我的错误表述而改契约。
这是正确的工程判断 —— 当任务卡与既有测试冲突时，应以既有契约为准并**显式指出冲突**（它做到了）。

### 更正 2：`.seqjournal` 回放导致我的两次基准行数不同

D1 指出：库实况已从 Q7 的 **71,160 行** → **72,289 行** → **72,297 行**（会话期间还在涨），
并解释了我此前观察到的「修复前后两次基准差 8 行」的原因。
这与我实测到的「启动时自动回放 .seqjournal 补齐 1,100 条」一致。
⇒ 引用审计链行数时**必须带时间戳**，否则数字会漂。

### D1 保留/轮转方案的关键实测（我采信其严谨性）

- dry-run：`rows_before = rows_after = 72297`、**sha256 逐字节不变**、`data\audit\archive` **未创建**。
- 正对照证明「只读」是**物理保证**而非自觉：在 `mode=ro` 连接上 `DELETE` → `OperationalError: attempt to write a readonly database`。
- **本仓日块与 seq 不同序**（2027-10-19 的 seq 928..2719 夹在 2026-09-14 的 915..2782 内）
  ⇒ 按 ts 切区间会**留空洞**；脚本因此自动挡死 ts 切法，只允许删 **seq 连续前缀**。
- 副本上全流程实测：归档 17 日 72,297 行、`--verify` 72,296 对相邻链接 **0 断裂**；
  删 seq ≤ 927（927 行）后按脚本打印的锚点重新锚定 → `verify_chain(start_seq=928, anchor_prev_hash=4cacf1a2…)` **ok=True checked=71370**。
- 冷归档约 **104.0 B/行**，比热库 757.8 B/行 **省 7.3×**。

⇒ 方案同时满足了两条硬约束：**hash 链连续性**（锚点重锚）与**不产生空洞**（只删前缀）。这是本卡最值得肯定的设计。

## A1-R1 复核：修订指纹核对通过（方法学上值得记录）

A1 在返工回复里**主动给出了 5 个文件的 sha256 前 16 位与行数**，要求「对齐同一版本再复核」。
我据此核对 —— **5/5 个 sha256 全部逐位一致**：

| 文件 | sha256 前16位 | 核对 |
|---|---|---|
| `agent/server_port_guard.py` | `893aed3971bf92a5` | OK |
| `app_server.py` | `9bf660dc0d15a2d9` | OK |
| `start_yunshu.bat` | `64e72948d04ba7a7` | OK |
| `scripts/watchdog_yunshu.py` | `5970ae8a08a73349` | OK |
| `tests/unit/test_startup_no_gap.py` | `0d9830abfeffe45d` | OK |

（行数显示 +1 的差异是我用 `b.count(b'\n') + 1` 数出的末尾空行，属**统计口径差**，不是内容差。）

### 为什么这条重要

上一轮有两件事同时发生：①我读到过 A1 的**中间态**（`KeyError` 那个栈是修复前行号）②另一张卡（D2）读到过别卡的中间态。
⇒ A1 这次**用指纹把自己钉在一个可复现的版本上**，是应对「并发改同仓」的正确做法。
**已提升为派发规则**：交付回复必须附**所有改动文件的 sha256 前 16 位**，复核方据此对齐版本。

### A1-R1 的实质结论（我独立复核）

| 断言 | 我的核对 |
|---|---|
| `app_server.py` **已无**对 `cleanup_port_listeners` 的真实调用 | ✅ 全文件只有 1 处命中，是**说明注释**（`:1859`） |
| 全仓真实调用点只剩 1 处 | ✅ 只有 `agent/server_port_guard.py:665`（`guarded_startup` 内） |
| `cleanup_port_listeners` 调用契约未变 | ✅ 位置参数只有 `port`，新增 3 个全为 keyword-only 带默认值；A1 还**真的执行了老调用形式** `cleanup_port_listeners(5678) -> []` |
| 新测试 | ✅ 独立跑 `test_startup_no_gap.py` + `test_server_port_guard.py` = **26 passed / 0 skipped**（`0 skipped` ⇒ 真进程 Job Object 用例真的跑了） |

### A1 的面修我确认为必要（不只是点修）

它指出旧版 `guarded_startup` 是 `result[...] = cleanup_port_listeners(...)` **无 try/except**，
异常发生在 **kill 之后、bind 之前** ⇒ 旧实例已死、进程带栈退出 = **正是本卡要消灭的零服务窗口**。
触发条件容易满足（`reap_children=True` 且收到一条后代记录，如历史实例留下的孤儿 worker）。
⇒ 它做了**两层修复**：点修（汇总日志按 `action` 过滤）+ 面修（清理调用整段兜底）。
并**显式保住三处既有契约**（原调用点注释、本模块 docstring、每个 kill 各自的 try/except）：
「**新链路不得比旧链路更严**」。清理抛异常现在只记 `action=startup.port_cleanup.failed` 留痕并**继续尝试 bind**，
端口没释放则以 `EXIT_SERVE_FAILED(4)` 退出交看门狗 —— 比半途崩溃更可观测。

这与我在本轮开头要求的「按实现缺陷修、不许放宽断言绕过」一致，且**超出了要求**。

## F1b 独立复核：**通过**（我自己的沙箱，非采信）

我按原缺陷的复现步骤在**仓库外临时目录**重跑（注入未知字段 + 注释 → 调一次 `update_meta` 改 enabled）：

| 检查项 | 修复前（主审计更早的沙箱实测） | 修复后（本次实测） |
|---|---|---|
| `unknown_custom_field: KEEP_ME` | **被删除** | **保留** |
| `# a hand-written comment` | **被删除** | **保留** |
| 末尾换行 | 丢失（`No newline at end of file`） | **保留** |
| front matter 行数 | 13 → 17（tags 被展开） | **13 → 13** |
| 变更行数 | 整份重写 | **1 行**（`enabled: true` → `enabled: false`） |
| 键丢失 / 值变化 | — | `keys lost: []`、`values changed (other than enabled): {}` |

⇒ **F1b 达成设计目标**：白名单外的字段与注释不再被静默删除，`enabled` 单行变更不再重排整份文件。
实现方式（最小侵入行范围替换，`_find_fm_key` 处理块值续行）我读码确认合理；
其 `_meta_value_equal` 对异构比较异常按「不相等」处理（宁可多写一行，不静默跳过）也是正确的保守选择。

单测：`tests/unit/test_update_meta_no_data_loss.py` + `test_skill_update_audit.py` 合跑 **37 passed**。

## D4 独立复核：**通过**（含隐私断言）

我用**隔离的 `AUDIT_DB_PATH`** + 隔离 store/repo 重跑（与 D4 自己的方法一致）：

```
created: d4probe
update desc  -> True
update enabled -> False

records:
   seq=1  skill.assess.auto            skill:d4probe
   seq=2  skill.update                 skill:d4probe
   seq=3  skill.registry.set_enabled   skill:d4probe

PRIVACY marker anywhere => False
```

⇒ **两条通路都留痕**（一般字段走 `skill.update`、`enabled` 走 `skill.registry.set_enabled`），
且**传入的 description 原文没有进入审计链任何文本列**（隐私断言成立）。

【一次自我纠错】我第一次跑这个复核时得到 `0 条记录`，一度以为 D4 的实现没生效。
实际是我**错误地先调 `update` 再建技能**（`update` 内部 `self._require(skill_id)` 会因不存在而抛错），
造出只有 `skill.assess.auto` 的假象。**按正确顺序（先 `create_manual` 后 `update`）即 3 条齐全。**
⇒ 记下来：**「读到 0 条」的第三种成因是复核者自己的调用顺序错** ——
  前两种是「并发中间态」与「边界参数语义」（F4 已记）。凡遇 0 条，先自证调用前提。

## A2 的护栏已生效（但主清理尚未落地）

审计链新增 2 条（**我有责任逐一认领**，不能只写「新增若干条」）：

```
seq=72298  action=tool.confirm.exempt_changed  subject=setting:CP_TOOL_CONFIRM_LEVEL_EXEMPT  actor=tok_ffd912584295
seq=72299  action=approval.submit              subject=tool_call:skill.nope
```

- `72298` 是 **A2 实现的那道新护栏**（豁免名单变更入链）—— **它已经在工作**，这正是我在任务卡里超要求加的那一条。
- `72299` 是 A2 的 fail-closed 测试产物（对未知技能发起调用触发审批）。

**但主清理尚未落地**：我读了 `data/ui_settings.json`（740 B，mtime 仍是 `2026-09-22T22:55:42`），
`CP_TOOL_CONFIRM_LEVEL_EXEMPT` 的值**仍是 `fan_out,delegate`** —— 也就是说
**`fan_out` 这个 L2/risk=high 的豁免此刻仍然生效**。A2 还在运行中，此项待其交付后复核。

## A2 · S1（最高危项）**已落地并独立验证通过** —— 本次审计最优先的除险完成

### 三条互相印证的证据

1. **配置层**：`data/ui_settings.json` 的 `overrides` 里 **`CP_TOOL_CONFIRM_LEVEL_EXEMPT` 整个键已消失**
   （文件 740 B → **405 B**，mtime `2026-09-25 19:26:24`；旁边留有 `ui_settings.json.bak` = 原 740 B / 09-22 原样）。
   即 `fan_out` 与 `delegate` 的 L2 豁免**已解除**。
2. **审计链**：新增 `seq=72300 action=approval.submit subject=tool_call:fan_out`
   ⇒ **`fan_out` 现在走确认门并被挂审批单了**（修复前它在链上是 `decision=exempted`）。
3. **护栏已在工作**：`seq=72298 action=tool.confirm.exempt_changed subject=setting:CP_TOOL_CONFIRM_LEVEL_EXEMPT`
   —— 这正是我在卡里**超出审计建议额外要求**的那一条（豁免变更必须入链）。
   本次事故之所以要等审计才发现，根因就是豁免变更**无留痕**；这条护栏把该根因堵掉了。

### 为什么这条最重要

审计 §1.5 的 S1 是**当时唯一仍在生效**的高危项（L2 / effect=execute / risk=high 的能力被静默放行，
运行时已有 4 条 `decision=exempted`）。现在它变成「**每次调用都要过确认门 + 变更留痕**」。

### 仍待 A2 交付的部分（未完成，不要误记为已修）

- **S2 fail-closed**：尚待确认（链上有 `approval.submit subject=tool_call:skill.nope`，
  提示未知技能路径已被拦，但「`tool_gate` 导入失败 ⇒ 拒绝 L2/L3 而放行 L0/L1」这条**必须由单测证明**，我还没看到）。
- **S3 技能脚本执行过闸**：`executor.py` 无改动（`git status` 未见），**尚未修**。

⇒ 在 A2 交付完整证据前，**S2/S3 记为未修**，不因 S1 成功而放宽。

## A2 复核：**实现成立（11/11）**，但它的 S1 审计用例有**测试隔离缺陷**（2 条与顺序相关）

### 结论先讲

| 部分 | 状态 | 证据 |
|---|---|---|
| **S1 除险（豁免移除）** | ✅ **成立** | `ui_settings.json` 的 `CP_TOOL_CONFIRM_LEVEL_EXEMPT` 键已消失（740→405 B）；链上 `seq=72300 approval.submit subject=tool_call:fan_out` ⇒ `fan_out` 现走确认门 |
| **S1 护栏（豁免变更入链）** | ✅ **成立** | 链上 `seq=72298 action=tool.confirm.exempt_changed` |
| **S2 fail-closed** | ✅ **成立** | `pytest -k "S2 or S3"` ⇒ **11 passed**；实现读**同一真相源**（`agent.lines.load_tool_meta`）、判不出级别按 L3 |
| **S3 技能脚本过闸** | ✅ **成立** | 同上 11 passed 内，含「effect=execute 被拦且子进程没起」「闸门不可用时拒绝执行」 |
| **A2 自己的 2 条 S1 用例** | ❌ **与顺序相关地失败** | 全文件跑 24 passed / 2 failed；**单独跑那 2 条则通过** |

### 测试缺陷的机制（我已定位，但**第一版修法未解决**，如实登记）

1. `test_新增豁免写入审计链` 期望 `added == ["compress", "write_file"]`，实得 `['compress']`
   ⇒ 说明 `write_file` **已在基线里**（它本应只存在于上一个用例 `test_移除豁免也写入审计链` 的名单中）。
2. `test_L3_不可被豁免` 的对照断言 `_confirm_level_outcome(delegate) is None` 失败
   ⇒ `delegate` 被要求审批，说明该对照所处的**开关状态**与用例预期不同。

**我尝试的修法**：给缺 `monkeypatch.setitem(G._EXEMPT_WATCH, "raw", G._UNSET)` 的两条用例补上复位
（另外 3 条同族用例本来就有这一行，**这本身说明作者知道该状态需要复位**）。

**结果：未解决**（仍 24 passed / 2 failed）⇒ 说明**还有第二个跨用例状态源**我没找到；
我只确认了这一个，**不再继续猜**。

### 我为什么不继续深挖

- 这是**测试隔离问题**，不是产品缺陷：同一切片的 S2/S3 断言 11/11 全绿，且 S1 的三条独立证据（配置/链/护栏）都成立；
- 第一版的 `_EXEMPT_WATCH` 复位**在逻辑上仍然是对的**（模块级状态不随 `monkeypatch` 还原），保留它；
- 继续深挖需要在本进程里逐用例 dump 模块级状态，成本高于收益，且**不应挤占确认门本身的复核预算**。

### 建议的处置（留给下一批，已登记）

给 `tests/unit/conftest.py` 加一个 **autouse fixture**：每个用例前复位 `agent.tool_gate` 的
全部**模块级缓存**（至少 `_EXEMPT_WATCH`，以及元数据/描述符缓存），而不是让每个用例各自记得复位。
理由：**靠每个用例自觉复位模块级状态是不可靠的**——A2 自己的 5 条同族用例里有 3 条做了、2 条漏了，
这正说明该机制应该收敛到 fixture。这也是本次审计反复出现的同一主题：**机制存在，但没人保证它被用上**。

## F9 独立复核：**通过**，并已用 HEAD 对照证明测试非恒真

### 我做的对照实验（本批最有说服力的一条）

把 **HEAD 版**（`5c9ace10`，无 F9 修改）签出到临时 worktree，把 F9 的新测试原样拷进去运行：

```
=== confirm HEAD service.py has no guard ===
0                                  <= HEAD 版没有 _UNDO_MERGE_KEEP_FIELDS
=== run F9 test against HEAD (expect FAIL) ===
E   AssertionError: assert True is False
E   AssertionError: 操作员停用应恰好 1 条启停记录，实得 0
E   AssertionError: 撤销合并把治理状态 enabled 静默改回了合并前快照
FAILED ...test_disabled_dst_stays_disabled_while_content_rolls_back
```

⇒ **`assert dst.enabled is False` 在 HEAD 上确实是 `True is False`** —— 即旧实现真的把停用状态改回了启用。
这排除了「写了一条恒真的测试」这种最常见的形式主义，是本批我最看重的一条证据。

（验证用的临时 worktree 已 `git worktree remove` + `prune` 清掉，`git worktree list` 恢复为只剩主仓库。）

### 实现复核（我读码确认）

| 检查项 | 结果 |
|---|---|
| 治理字段集 | `service.py:1109-1110` `{"enabled", "is_sensitive", "isolation_strategy"}` |
| 情形一守卫位置正确 | `:1170-1173`：`for k, v in before.items(): if k in self._UNDO_MERGE_KEEP_FIELDS: continue; data[k] = v` —— 在赋值**之前**跳过 |
| 情形二**刻意不做**过滤 | `:1179-1185`：`except SkillNotFoundError:` 分支直接 `Skill.from_storage_dict(snap)`，无过滤 |
| 注释解释了**为什么两情形不同** | `:1166-1169` 与 `:1180-1182` 都写明了语义边界 |
| 新测试 | 独立跑 **8 passed**；既有治理回归 **28 passed** |

### 我为什么认可它**没有**采用「恢复+留痕」方案

F9 选择了「情形一不恢复治理状态」，而不是「恢复了但补一条 `skill.registry.set_enabled` 痕」。
它在报告 §3.3 给出的理由我认为是对的：

> 那会让审计链上出现一条**并非真实治理决策**的启停记录 —— 属于**误导性留痕**，
> 比「没有留痕」更糟（因为审计链的价值在于「记录的都是真事」）。

这与审计本报告多处强调的原则一致：**宁可少记，不可记假**。

## F10 · F9 顺带发现：情形二的 dst 重建分支在真实记录下**不可达**

F9 报告 §6 实测：`agent/skills_mgmt/service.py:1184` 的判据是
`isinstance(snap, dict) and snap.get("id") == dst_id`，而 `snap = rec.get("src_snapshot")`，
**`src_snapshot["id"]` 恒等于 `src_id`**，且 merge 禁止 `src_id == dst_id`
=> 该条件**永远为假**，dst 不会被重建。

实测：删掉 dst 后 undo，`restored == ["…src"]`，**dst 未被重建**。

**含义**：情形二实际只有「src 重建」一半在工作；「保留方 dst 也被删了」这一场景下，
**撤销合并无法恢复 dst**（静默少恢复一个技能，且不报错）。

**修法需要**：`merge_with_backup` 另写一份 `dst_snapshot`（当前只写 `src_snapshot` + `dst_before`）。
F9 未改（超出其允许范围，判断正确）。**建议单开一张卡**（记为 F10）。
注意：这条**不影响 F9 的修复正确性** —— 情形二的 dst 分支本来就走不到，
所以「不做治理过滤」这个决定今天没有可观测后果；但一旦有人修好 F10 让该分支可达，
**就必须回头确认它仍应恢复 `enabled`**（F9 的单测已用构造记录固化了这个口径）。

## 本轮交付复核（B2 / D3 / T-ISO + 契约冲突裁决）

### B2 路由日志落盘 —— 通过（含我做的 A/B 对照）

只读校验脚本：route_decision=2、intent_layer=2、[OK] exit=0；data/logs files=57 不变（无新文件、无轮转副本）。

我第一次验证开关=0 时只得到 chat=400（payload 字段名用错：应为 message 而非 question），
那样得到的「文件未变」是无信息的（根本没发生请求）。=> 我重做了对称 A/B 对照：

    === A: sink ENABLED（正对照）===
    sink_ON   ready=True chat=200  bytes 5802->7100  GREW=True
    === B: sink DISABLED（回滚证明）===
    sink_OFF  ready=True chat=200  bytes 7100->7100  GREW=False
    VERDICT: positive control grew = True | switch-off unchanged = True

=> 两次请求都真正成功（200），开关开时落盘、关时逐字节不变。B2 的开关与落盘都成立。

我认可它的两处判断：①选同步写（p95 约 340us）而非异步队列，理由是「进程常被 taskkill /F，
异步队列会丢未刷盘事件」—— 而这正是本卡要消灭的「重启即丢」；以 <0.1% 请求耗时换不丢是对的。
②F1 验收要求的 tool_selected 事件全仓不存在，它用 tool_retrieval 作等价物并**主动标注为替代口径**，
不是偷偷换名。

### D3 审计治理巡检 —— 通过，且它指出了我任务卡里的一个证据漏洞

我独立跑 scripts/audit_governance_check.py => PASS（FAIL=0 WARN=2）exit=0：
G1 豁免名单为空；G2 全链 72310 条重算一致；G3 日根 9 天（通过 8）/缺根 8 天/历史失败 1；G4 非业务 0.0885%。

**它指出的漏洞（我采信并更正）**：

> git status --porcelain -- data/ 是弱证据 —— data/audit/（.gitignore:49）与
> data/ui_settings.json（.gitignore:173）都被 gitignore，改了也照样为空。

=> 它改用 sha256 + mtime 前后逐字相同作为真正的只读证据。
我在多张卡里都用 git status -- data/ 当「未污染」证据，**而它对被忽略的路径无效**。
凡涉及 data/audit、data/ui_settings.json 的只读断言，**必须用哈希**。已写入派发规则。

D3 的实质结论：豁免名单现为空且反事实探针证明门**不是假绿**（历史原值 fan_out,delegate => FAIL rc=1 并点名 L2/high）；
链完整 0 断链；8 天缺日根；2026-09-21 日根永久不符（叶子 245 ≠ 根记录 235）。
另实测 **get_daily_root() 取第一条 => 追加式重封对验签无效** => 补日根必须新开卡设计（它建议 D5）。

### T-ISO 测试隔离治本 —— 通过，但它纠正了我一处判断

A2 测试文件 **26 passed**（修复前 24/2）；我独立用倒序逐条指定跑 => 5 passed；agent/ 下零改动。

**它纠正了我**：我说 test_L3_不可被豁免「单独跑却通过」并据此推断顺序相关。
T-ISO 实测**单跑同样失败** => 我的前提不成立（我当时用 Select-String 过滤输出，
把 1 passed 那行误当成了它的结果，实际那是前一条用例的行）。

真实根因更准确：**该用例断言本身写错** —— 名单只设了 shell_execute，却断言 delegate（不在名单里）不被要求确认。
已改为「名单同时含 L3+L1 => L3 被忽略、L1 照旧豁免」，断言强度未降。

**它还发现了一个我完全没想到的状态源**：**审计链上「名单上一次记录的生效值」**。
该状态**落盘、追加写、无删除 API** => **fixture 复位不了**。
所以 test_新增豁免写入审计链 的根因是**链基线泄漏**（文件序得 removed==['delegate']、随机序得 ['shell_execute']），
与 _EXEMPT_WATCH 无关 —— 我第一轮补的那两行复位方向不对（无害但非解法）。它改用**自建链基线**，做法正确。

=> **教训（已写入派发规则）**：**跨用例状态源不只在内存里** —— **落盘的追加式状态（审计链）同样会泄漏**，
且无法用 monkeypatch 复位。凡断言「某次操作产生了几条记录」，都必须**自建基线**而非依赖「运行前有几条」。

## 契约冲突裁决：A2 的 fail-closed vs 既有 fail-open 用例

T-ISO 越界发现（未修，等我裁决）：tests/unit/test_tool_gate.py 的
test_闸门模块导入失败时_call_照常执行 **单跑即失败**，根因是 A2 的 fail-closed 与该老用例的 fail-open 期望冲突。
该文件不在 T-ISO 允许清单，它没动 —— **判断正确**。

**我的裁决：更新测试，因为它钉的是被明令废弃的旧契约。**

该用例断言 ok is True 且 probe_tool 计数 == 1（即「闸门导入失败 => 照常执行 handler」）。
而 probe_tool 是 registry.register 登记的、**无 YAML 元数据**的探针工具 => A2 规则下判不出级别 => 按 L3 => 拒绝。
**这不是 A2 改错了**：审计 S2 明确要求「闸门不可用时 L2/L3 一律拒绝」，而「无元数据 => 按最严」正是 fail-closed 的题中之义。

**我的处置**（我直接改，因为这是审计主线上的契约收紧，且 T-ISO 被明文禁止越界）：
改名为 test_闸门模块导入失败时未登记工具被拒_fail_closed，断言改为拒绝侧：
ok is False、blocked is True、**probe_tool 计数 == 0（被拒的工具不得执行 handler）**；
docstring 写明这是 A2/R3 的契约变更、附报告路径，并注明「放行面（L0/L1 仍放行）由 test_confirm_gate_no_bypass.py 覆盖」，
避免被误解为「整层关掉了」。

结果：test_tool_gate.py => **63 passed**；全套 16 个文件合跑 => **344 passed / 0 failed**。

## D3 上报的 `lock.degraded / seq_conflict` —— 我查证后判定：**良性降级，无记录丢失**

D3 在报告里如实上报：「本卡运行期间另有进程在写生产链（72300→72307），
seq=72302 是一条 `lock.degraded / seq_conflict: UNIQUE constraint failed`（pid 18800）」。
这条值得单独查证，因为它触及**审计链的完整性根基**。

### 我的独立查证（只读）

| 检查 | 结果 |
|---|---|
| **链是否有 seq 缺口** | **`seq gaps: 0`**；`min seq=1`、`max seq=72310`，**连续无洞** |
| `lock.degraded` 记录总数 | **195** 条 |
| 其中指向 `.pytest_tmp`（**测试产物**） | **146** 条 |
| 其中指向其它路径（`%TEMP%\audit_probe\*` 等） | **47** 条 |
| **指向生产锁 `data/audit/audit_chain.db.lock`** | **仅 2 条**（`seq=20235`、`seq=20236`，2026-09-21T16:52:38） |

### 结论

1. **没有记录被静默丢弃。** 链的 seq 从 1 到 72,310 **完全连续** ——
   如果并发写真的丢过行，`seq` 会出现空洞。锁定机制降级过，但它**保住了完整性**。
2. **绝大多数 `lock.degraded` 根本不是生产事件**：146/195 来自 pytest 的临时库，
   还有一批来自 `%TEMP%\audit_probe\*`（2026-09-21 的一次**并发探针实验**）。
   也就是说，这一条**在统计口径上更像测试噪声**，不是生产告警。
3. **真正触到生产锁的只有 2 条，且都发生在 2026-09-21 的那次探针期间** ——
   与 D3 观察到的 `seq=72302` 是**同一类现象**：多进程并发追加时的**瞬时争用**，
   而非持续性故障。

### 为什么这仍然值得记录（而不是「没问题就跳过」）

- 它暴露了**审计链的并发模型**：追加遇到 `UNIQUE constraint failed` 时会 `degrade` 而不是无限重试；
- 而**当前证据只能说「这次没丢」，不能说「永远不会丢」** ——
  我没有做并发压测（D3 也没做）。**这是一个待验证面，不是已关闭项**。
- 责任归属：D3 建议归「审计链写路径的卡」。我同意，但**优先级下调**：
  因为 `seq` 零缺口说明它在实测压力下是稳的，而当前证据不支持「紧急」。

⇒ **登记为待验证（非阻塞）**：
  「并发写审计链（≥4 进程 × 大记录数）下 seq 是否出现缺口 / `lock.degraded` 频率」——
  需要一张**并发压测卡**，而不是靠生产偶发观察。

### 顺带确认一个 D3 与 Q7 的口径差异

Q7 审计时链是 **71,160/72,289 条**；D3 跑时 **72,305~72,310 条**；我读时 **72,310 条**。
**引用审计链条数必须带时间戳**（我在 D1 复核时已记过这条，此处再次验证）。

## 第 4 轮收尾：跨卡回归实测（两轮大扫）

本次改动横跨 20 个既有文件，**跨卡破坏风险是主要隐患**。我做了两轮大范围回归：

### 扫一：治理/权限/技能域（12 文件）

```
python -m pytest tests/unit/test_tool_gate.py test_tool_gate_strict.py test_tool_gate_fallback.py \
  test_tool_approval.py test_confirm_level.py test_confirm_gate_no_bypass.py test_tool_exemptions.py \
  test_skill_registry.py test_skill_registry_audit.py test_skill_update_audit.py \
  test_undo_merge_governance_state.py test_update_meta_no_data_loss.py -q

结果：423 passed, 0 failed（26.70s）
```

### 扫二：检索/路由/审计域（12 文件）

```
python -m pytest tests/unit/test_skill_index_cache.py test_bm25_skill_searcher.py \
  test_skills_mgmt_lineage.py test_capregistry_core.py test_capregistry_callpaths_routes.py \
  test_prompt_cache_order.py test_adapters_comprehensive.py test_lazy_singleton_concurrency.py \
  test_route_log_sink.py test_audit_governance_check.py test_audit_read_path.py test_audit_facade.py -q

结果：425 passed, 7 skipped, 0 failed（23.65s）
```

7 条 skip 全部是**既有**的（6 条 fewshot 功能已移除、1 条需 `--runslow`），与本批改动无关。

### 结论

**两轮合计 848 passed / 0 failed**，覆盖了本批**所有被改动的模块**（tool_gate / tools / skills_mgmt / audit / adapters / vector_adapter / index_cache / persona / prompt_guard / app_server 相关）。
⇒ 目前**没有证据显示跨卡引入回归**。

### 一条附带核实（T-ISO 的越界观察）

T-ISO 报告称 `tests/unit/test_tool_exemptions.py` **疑似同一函数定义两次**（并发写者）。
我在它交付后核实：**该名字无重复定义，文件 15 passed**，且 `git status` 显示该文件**未被本批任何卡修改**。
⇒ 那是**观察时的瞬时状态**（T-ISO 自己也标注「疑似」并声明未触碰）。**已排除，无需处置。**

这再次印证 F4 的派发规则：**并发期间读到的文件状态可能是中间态**，
报告「疑似异常」时标注不确定性、且不动手，是正确的做法。

## F11 · 「工作流学习」子系统产出无意义数据（我实测发现，未被任何卡覆盖）

### 怎么发现的

C2 报告尾部提到「服务运行期副作用：`data/learned_workflows.json` 显示为 M」。
我查了这个 tracked 文件的 diff，发现它**不是**无害的运行时痕迹，而是暴露了一个子系统的问题。

### 实测证据（`data/learned_workflows.json`，8 条既有 + 我跑服务时新增的）

**证据 1：`task_signature` 全是「字符列表」，不是任务签名**

```
python-eb17ed25  sig='python|件|所|文|有|目|统|计|里|项'   input='统计项目里所有 Python 文件的行数并保存报告'
zip-d2968c59    sig='zip|仓|代|包|压|库|成|打|码|缩'       input='把代码仓库打包成 zip 压缩包'
json-30a189b6   sig='json|件|取|并|换|文|置|读|转|配'     input='读取 JSON 配置文件并转换为 YAML 格式'
wf-f19dc52c     sig='件|作|出|列|前|工|当|录|文|目'       input='帮我列出当前工作目录下的文件'
```

注意第 1 条：输入里的 `Python` 被切成 `python` 进了签名，剩下的 9 个「字」是
`件 所 文 有 目 统 计 里 项`（按某序排列的**单个汉字**）。
⇒ 这不是「任务签名」，而是**对输入做分词/排序后拼起来的字符清单**。
**两条语义不同的输入会得到相同的签名**（见证据 3），**而字符清单本身不携带语义**。

**证据 2：非用户输入会把工作流学习「钓」出来（可复现，出现两次）**

| 时间 | 触发 | 产生的条目 |
|---|---|---|
| 20:35:22 | 我跑 **B2 的 A/B 对照**（会话 `b2ab_sink_OFF`） | `ping-21ec4378`，`source_user_input: 'ping'`，1 步，`【准入未通过 STEPS_TOO_FEW】` |
| 20:5x | 我跑 **C2 的 24 并发压测**（会话 `sat_3`/`sat_4`） | `wf-d68743e6` / `wf-0ccbe42d`，`status: draft` |

我自己**没有向 `/api/chat` 发过 `ping`**（我用的是 `你好` 与 `请列出当前工作目录下的文件并逐个说明用途`）。
⇒ **有一个非用户路径在调用工作流学习**。我没能找到发送方（全仓 `"ping"` 只命中
`routes_logging.py:501`，而那是**直连 provider 的 POST、不过 orchestrator**，不可能是它）。
**如实登记为「未能定位触发源」**，不猜。

**证据 3：重复条目（同一任务 3 条）**

```
wf-f19dc52c  input='帮我列出当前工作目录下的文件'
wf-c7499f27  input='帮我列出当前工作目录下的文件'
wf-28f2775d  input='帮我列出当前工作目录下的文件'
```
⇒ 同一任务被学习了 **3 次**，且三条的 `task_signature` **完全相同**。
这意味着该子系统**既不按签名去重、又无法靠语义区分**。

### 我的处置

1. **两次把 `data/learned_workflows.json` 用 `git checkout --` 还原**（9→8 条、10→8 条），
   备份留在 `cleanup_backup_20260925/learned_workflows.with_junk.json.bak`。
2. **登记为待开卡（F11）**，不自己改学习逻辑 —— 改它需要产品判断（签名算法该用什么？去重口径？），
   且**不在本次审计任务的任何一张卡的范围内**。

### 为什么这条重要

- 它**修正了我审计里的一处表述**：我在 §4.4 曾把工作流学习层描述为「文档未列」，
  而实测显示它**真的在跑并真的在写数据**（虽然写的是垃圾）。
- 它是**又一个「机制存在但没人验证输出」**的实例 —— 与本次审计的主线完全同构：
  系统有一层会自动学习、自动落盘、还进了 git 的机制，
  **而没有任何测试断言它的输出是否有意义**。
- 它同时说明**跑一次服务就会污染一个 tracked 数据文件** ——
  任何人在这个仓库里 `git add -A` 都会把测试期间的脏数据提交进去。

### 建议的最小动作（未实施）

1. 先查清**触发源**（证据 2）：是什么在非用户路径上调用了工作流学习？
   （线索：会话 id 分别是 `b2ab_sink_OFF`、`sat_3`/`sat_4` —— 即由**我的 HTTP 请求**创建，
    但输入内容却不是我的请求内容；这提示「学习用的是**上一次**会话的内容」，值得优先验证。）
2. 给 `task_signature` 定一个**真签名口径**，并加去重；否则该层永远只产垃圾。
3. 把 `data/learned_workflows.json` 移出 git 跟踪（运行时产物不应 tracked），或加写入守卫。

## 第 5 轮：G1-B0 对账 + S0 前置落地 + B1-T2 复核

### G1-B0 —— 通过（只读对账，82 KB 报告）

| 项 | 结果 |
|---|---|
| **数据面零漂移** | 10 处存储条数**一个没变**；15/23 冲突逐条 `SequenceMatcher.ratio` **15 个数值与 G1-A 表格逐个相同**（0.077…0.913）；触发句式 **17/23→13/23 完全复现** |
| **行号面部分漂移** | `vector_adapter.py` 一族（+26/+67/+42）；`app_server.py` 的 `SkillsManager` **708→992**；`registry.py:205-245` **完全命中**；persona 两落点 **0 漂移** |
| **唯一源裁定** | 仍成立，且**证据更强**：23/23 skill.md 全部 `git ls-files --error-unmatch` 通过（G1-A 只抽查 1 个） |
| **F1b 效果复核** | `update_meta` **23/23「除目标键外逐字节不变」** + 幂等 23/23；15 个缺末尾换行**全是 `pd-*`**，各补 **1 个 CRLF 且只补一次** |

**它给出 3 处对我/对 G1-A 的勘误**（我采信）：
1. M4 前端落点：`skill-center.tsx` 里 `description` **grep 零命中**；真正渲染的是 **`skills.tsx:249`**。
2. M0 **漏了一条 overlay 写路径** `POST /api/skills/describe`（`plugins/skills.py:443-456`，可写任意 id）
   ⇒ **写路径是 3 条不是 1 条**。冻结写路径时必须一并处理，否则 G1 改成什么样都会被旁路覆盖。
3. G1-A 自身 §0 与 §9.4 对 `callability.py` 的**站点数自相矛盾**，实测正确是 **3 处**。

### S0 前置（我亲自落地并验证）

G1-B0 指出 G1-B **唯一的阻塞项**：`description_zh` 不在 `file_store._META_FIELDS`（`:75-82`）
⇒ `update_meta` 写它被**静默忽略**、`parse()` 也看不见 ⇒ **「回填中文」这一步在数据层落不了地**。

**我补了这一行**（并在注释里写明为什么必须在这里、与 F1b 的关系、以及为什么 `description` 不改成中文），
然后在 `%TEMP%` 副本上实测四件事：

```
1) update_meta 写入 description_zh   -> file changed: True，字节里出现该键
2) parse() 看得见                     -> parsed keys has description_zh: True，value='中文展示文案 ABC'
3) load_metadata_index() 带得出来     -> description_zh: '中文展示文案 ABC'
4) 再启停一次（enabled=False）后仍在  -> survived a toggle: True，值未变   <= 关键：F1b 保护生效
```

⇒ **G1-B 的阻塞项已清除**，且第 4 条证明「写了不会被下次启停抹掉」（这正是 F1b 与 S0 的组合效果）。

【一处如实登记的未决】我那个「只改该键时其余字节是否不变」的**自制判据返回 False**；
但我用的是自己写的 strip 辅助函数（对块标量可能切错），**不足以判定**。
⇒ 该点**以 F1b 的独立实测为准**（它用与实现无关的判据测出 23/23 通过），
   我这条自制判据**不作为证据**，也不据此下结论。

回归：`test_update_meta_no_data_loss` + `test_skill_registry*` + `test_skill_update_audit` ⇒ **56 passed / 0 failed**。

### B1-T2 —— 通过（含「非恒绿」自证）

| 复核项 | 我的实测 |
|---|---|
| 新测试 | **7 passed** |
| 它点名的 4 个回归文件 | **78 passed / 6 skipped**（skip 为该文件既有 Few-shot 移除） |
| **定稿点与渲染点的先后** | 我 grep 确认：V1 定稿 `:3359` → 渲染 `:3386/3388`；V2 定稿 `:4099` → 渲染 `:4107/4122` ⇒ **两处都是「先定稿后渲染」** |
| 是否有逻辑丢失/重复 | 6 个 hunk 头与我 grep 到的站点**一一对应**；两处删除各留了指路注释 |

**它的「非恒绿」自证值得肯定**：把渲染口径临时切回修复前语义后，新单测**立刻变红**
（`AssertionError: 宣告 6 个、下发 2 个`）—— 这与我在 F9 用的 HEAD 对照是同一类方法。

**一处它主动声明的越界（我接受）**：改法 A 在文本上必然包含**原位删除**（V1 `:3500-3503`、V2 `:4165-4167`），
因此触碰了卡内区域清单之外的两点。它的理由是「不删会让 V2 把已定稿的 `tools_whitelist` 覆盖回收窄前白名单」——
⇒ **这是为避免真实回归所必需的删除，且已披露**，我接受，并记录为「边界清单可以更精确」的教训。

## B1-T2 的「残留 2 条」我查证后：**两条都已被守卫覆盖**（比它的自述更安全）

B1-T2 在报告 §5/§6 主动登记了两条「既有残留（未修）」：

> ① V2 末轮 `tool_calling.py` 会丢 tools 而 V2 未接 `align_system_prompt_with_tools`；
> ② V2 在 `allow_tools=True` 且 `_tool_calling_service=None` 时出网不带 tools 而提示词仍按默认口径宣传。

这两条都属 **DSML 泄漏那一类**（提示词宣传工具 + 请求不带 tools），所以我逐条追了**实际调用图**：

### 残留 ① —— **已被覆盖**

`agent/tool_calling.py:353` 确实会丢 tools：
```
need_tools = tool_defs if round_idx < self._max_rounds else None
```
但 `:657-679` 的 `_call_llm_with_tools` 是**该循环的唯一出网口**，且它在出网前调用守卫：
```
673:  from agent.tools_prompt_guard import align_system_prompt_with_tools
674:  system_prompt, messages = align_system_prompt_with_tools(
675:      system_prompt, bool(tool_defs), site="tool_calling._call_llm_with_tools", ...)
```
⇒ `tool_defs` 变空时 `bool(tool_defs)` 为假 ⇒ **提示词被就地中和**。
V2 出网点在 `orchestrator.py:4180/4189`，**都走 `chat_with_steps`** ⇒ **必经此口**。

### 残留 ② —— **也被覆盖**

`orchestrator.py:4204-4210` 确实在 `(_tool_calling_service is None or not allow_tools)` 时走：
```
4205:  response = self._run_llm_bounded(lambda: self._llm.chat(
4206:      messages=messages, system_prompt=system_prompt, ...))
```
**不带 tools**。但我追了 `self._llm.chat` 的调用图：

| 层 | 文件:行 | 守卫？ |
|---|---|---|
| `chat()` | `memory/llm_service.py:330` | — |
| `_chat_with_retry` | 由 `:91-96` 的 `with_retry(...)(self._do_chat)` **包装** | — |
| **`_do_chat()`** | `memory/llm_service.py:278` | ✅ **有守卫** |

```
282: 【DSML 根因防线】本方法**按定义永远不下发 `tools`**（没有该形参），
283: 因此传入的 `system_prompt` 里任何"你有工具"的宣传都是**虚假宣传**。
289:  try:
290:      from agent.tools_prompt_guard import align_system_prompt_with_tools
291:      system_prompt, _ = align_system_prompt_with_tools(
292:          system_prompt, False, site="llm_service._do_chat")
```
⇒ `tools_exposed=False` **硬编码** ⇒ 该路径**永远**中和宣传。**已被覆盖。**

## 我为什么认为 B1-T2 的「保守登记」是对的

它把这两条记为「未修、超出本卡范围」——**从它的视角是合理的**：
守卫在**若干跳之外**（`with_retry` 包装 + 动态属性绑定），**静态读该文件看不出来**。
⇒ 这恰恰是本项目的一个结构性问题：**不变量分散在调用图里，而没有任何单一地方声明它**。

**建议（已登记，未实施）**：像 `tools_prompt_guard` 那样，把「哪些出网口已挂守卫」做成**一张可断言的清单**
（例如一个测试遍历所有 `chat/completions` 调用点，断言其上游存在守卫），
这样下一个读代码的人（或代理）**不必靠追调用图**才能确认安全性。

**这条不影响 B1-T2 的交付结论**：它的修改（宣告=下发）已独立复核通过，
而我这次查证的结果是**它比我预想的更安全**，不是更危险。

## 第 6 轮：F11 / D5 / F10 / B3 复核 + 两处交接我亲自执行

### F11 —— 通过，且它**纠正了我的 F11 触发源假设**

**它查清了触发源，结论是「没有幽灵调用方」**（我原先说「未能定位」）。证据链完整：
- `data/sessions/b2ab_sink_OFF/messages.jsonl` 共 **194 行**，97 条 user 消息中 **96 条内容就是 `ping`**；
- 其中**唯一一次带工具调用**的 assistant 行与条目 `created_at` 相差 **1.1 s**，方向符合代码顺序；
- 代码侧 `orchestrator.py:1623` 是 `learn_from_interaction` 的**唯一生产调用方**。
=> **发送方是 B2 探针客户端**（以 `ping` 作最小载荷）。**我采信，并撤回我的「未能定位」。**

**它的真签名修复我实测确认有效**：

| 检查 | 实测 |
|---|---|
| 新测试 | **20 passed** |
| 旧口径 vs 新口径（同一输入） | 旧 `python|件|所|文|有|目|统|计|里|项` → 新 `python|保存|存报|并保|所有|报告|数并|文件|目里|统计|行数|计项|里所|项目` |
| **去重真的生效** | 实测条目 `bm25-20d5ab81` 的 `observed_count: 2`、`source_sessions: ['b3-cache-A1','b3-cache-A2']` ⇒ **跨会话合并成 1 条** |

**但它对子系统「不能产出可用工作流」的判断我实测确认成立**：

我读了两条 2026-09-25 22:1x 新增（**在 F11 修复之后产生**）的条目：

| id | trigger_patterns | status | 说明 |
|---|---|---|---|
| `bm25-20d5ab81` | `['bm25']` | draft | 含拉丁词 `BM25` ⇒ **恰好**拿到触发词 |
| `bm25-6b0cda26` | **`[]`** | draft | 语义几乎相同，但写法不同 ⇒ **零触发词** |

两条的准入拒绝原因都含 `STEPS_TOO_FEW`，第二条**还多一条 `NO_DISCRIMINATIVE_TRIGGER`**。

⇒ **F11 的修复解决了「签名是字符清单」与「无去重」，但没有、也未声称解决「中文拿不到触发词」。**
⇒ 这坐实了它的结论：**该子系统当前无法产出被消费的工作流**；两条新条目的 `status` 都是 `draft`，
   而 `draft` 按它查证**不进匹配候选池**。

**F11 的硬伤 3 交接：我已执行**（见下节）。

### 我执行了 F11 无法执行的交接（`git rm --cached`）

F11 正确指出：**给已跟踪文件加 `.gitignore` 是无效的**，它实测：
`git check-ignore -v data/learned_workflows.json` → **rc=1（未被忽略）**、`git ls-files -v` → **`H`（正常跟踪中）**。
（我独立复核：**确认 rc=1 且状态为 H**。）

然后它明确把这一步**交接给上级**（因为铁律 2 禁止它做 git 写操作）——**判断正确**。
我执行了：

```
git rm --cached data/learned_workflows.json
  -> rm 'data/learned_workflows.json'
复核：git ls-files           => 空（不再跟踪）
      git status --porcelain => 'D  data/learned_workflows.json'（staged 删除）
      git check-ignore -v    => .gitignore:507 命中，rc=0（**现在生效了**）
      文件仍在磁盘：19904 bytes（未删除数据）
```

**回滚方式**：`git reset -- data/learned_workflows.json`（撤销 staged 删除）——
因为**未 commit**，一次 reset 即可完全恢复原状。

### D5 —— 通过（它把「追加式重封无效」的定性**说得比 D3 更准**）

D3 说「`get_daily_root()` 取第一条 ⇒ 追加式重封无效」；D5 准确定性为：
**写侧只会追加，读侧读第一条** ⇒ 这是**读侧取用语义**缺陷，不是写侧问题。
它在**同一份生产数据副本**上做双向实测：修前 `force=True` 重封后 `verify` 仍 `False`；修后 `ok=True`。

**它否决了另外两条路线并给了理由**（这两条我都认同）：
- 路线 (b) 插行 → **会破坏外层根链**；
- 原地改写 → **等于改写合规证据**（触碰审计底线）。

**兼容性论证到位**：9 条有效根里只有 09-14 有两条且**可验签字段逐字相同** ⇒ 既有行为零变化。
**它不藏安全权衡**：追加者可取代既有根 ⇒ 明确写了代价与两条护栏（拒绝重封「篡改类」失败；被取代记录永不删除且 `seal=1` 可取回）。

**只读证据**：`daily_roots.jsonl` 的 size/mtime_ns/sha256 从 D3 基线到交卡**逐字未变**。
**它主动澄清** `audit_chain.db` 有变化但**归属明确**：D5 命令窗口内 `rows +0 / head 相同 / sha256 相同`，
增量来自**在跑的服务**（`ui.*` 记录，链头 72305→72592）—— 这种「把不是你造成的差异也说清楚」的做法正是我要的。

### F10 —— 通过，且它精准指出自己**打穿了一条 F9 断言**

F10 实测：修前 `restored == ['…src']`、`dst_rebuilt: false`；修后 `restored == ['…src','…dst']`、`dst_rebuilt: true`。
新测试 **14 passed**。

**它同时报告**：回归时 `test_undo_merge_governance_state.py` **1 failed** ——
失败的正是 F9 文件里那条**把 F10 缺陷写死**的断言（`assert ['f9-c2-src','f9-c2-dst'] == ['f9-c2-src']`）。
**该文件不在 F10 的允许清单内，它没动，而是写进报告待转交 —— 判断正确。**

**我复核并修正了它**：我读了那条用例，其 docstring 与注释**明确把「dst 重建分支不可达」记为「现状」**
（原文：`# 现状记录：dst 侧的重建分支判据是 snap["id"] == dst_id … ⇒ 真实合并产生的记录走不到该分支`）。
F10 已修掉该缺陷 ⇒ **这条断言与注释都已过时**。

我把它改为断言**新契约**：`restored == ['f9-c2-src','f9-c2-dst']`，并补两条实质断言——
dst 的**正文回到合并前**（`CONTENT_DST`）、**`enabled` 来自快照**（True）。
注释里写明：**这不是放宽断言，是契约变更**，因为「静默少恢复一个技能且不报错」本身就是被修掉的缺陷。

结果：`test_undo_merge_governance_state` + `test_undo_merge_dst_snapshot` + `test_skill_update_audit` + `test_skill_registry` ⇒ **41 passed**。

### B3 —— 测试通过；但它更正了**我审计里的一处描述**，且**指出一个更危险的变体**

B3 的测试 **32 passed**。它**没有越界**：3 个打点缺陷所在的文件都不在它的清单内，
于是它交付「计数函数 + 可直接套用补丁」并实测更正了其中一条 —— **这正是我要的处理方式**。

**它对我的更正（我采信）**：审计说「`tool_retrieval` 只带 `query_hash`、**无 `trace_id`**」。
实际是：**它有 `trace_id` 字段**，但该字段由 `log_dict()`（`agent/logging_utils.py:166-167` → `uuid4`）
**每个事件现场随机生成** ⇒ **同一次请求内两条事件 trace_id 互不相同**、与 `route_decision` 关联 **0/2 命中**。

⇒ **这比「缺字段」更危险**：缺字段会让人立刻发现关联做不了；
   而**有一个看起来对的字段却永远 JOIN 出 0 行**，会让人以为是「没有数据」而不是「键是错的」。
   已登记为待接线项（`routing_observability.current_trace_id()` 已备好且有单测）。

## ISO-EVENTS 部分落地（**未闭环**，如实登记）

### 我做了什么

F10 交卡时报告：跑既有回归时 `data/skills_assessment_events.jsonl` **被写入**，新增行 `skill_id` 全是 `d4-*`/`f9-*`
（即 D4/F9 的**未隔离单测**写的）。我复核时该文件已达 **1115 行**并在继续增长。

F10 在自己测试里**手工**把 `log_archiver.__file__` 重定向到 tmp（**方向对**），但那是「每个用例自觉隔离」；
T-ISO 已经证明过**靠自觉不可靠**（`tool_gate` 的 5 条同族用例 3 条写了复位、2 条漏了）。
⇒ 我在 `tests/unit/conftest.py` 加了一个 autouse fixture `_iso_assessment_events_to_tmp`，把它收敛到一处机制。

### 实测结果：**fixture 未生效**（我自己的判据）

| 测试文件 | 结果 |
|---|---|
| `test_undo_merge_dst_snapshot.py` | **clean**（它自带 F10 的手工重定向） |
| `test_confirm_gate_no_bypass.py` | **clean** |
| `test_skill_registry.py` | **WROTE**（每次 +186 B / +1 行） |
| `test_undo_merge_governance_state.py` | **WROTE**（+760 B） |
| `test_skill_update_audit.py` | **WROTE**（+192 B） |

确定性实测：连跑 3 次 `test_skill_registry.py`，**每次都写 186 B**（非随机）。
写入记录形如 `{"kind":"auto","skill_id":"main-1","verdict":"ok",...}`。

**我试过的两步修法都没成功**：
1. 改 `la.repo_data_dir` —— 单独验证**有效**（`active_events_file()` 确实返回 tmp 路径），但实跑仍写 ⇒ 说明调用链上有**包装层**重新绑定；
2. 升级为直接替换唯一出口 `la.active_events_file` —— **实跑仍写**。

**我没有继续深挖的原因（如实说明）**：
- 这是**数据卫生**问题，不是正确性问题：该文件被 `.gitignore:458` 忽略 ⇒
  **测试记录永远不会进仓库**，也不会被任何人当作生产数据消费（它是面板轮询源，重启即重算）；
- 我已在这个点上消耗了过多轮次；继续深挖需要逐步 dump 子进程内的模块绑定，**成本高于收益**；
- 更重要的：`test_undo_merge_dst_snapshot.py`（F10 手工重定向的那个）**是 clean 的** ⇒
  **证明「显式隔离」这条路是可用的**，只是「autouse 收敛」这一层我没做通。

### 处置

1. **fixture 保留**（它无害、且对已导入 `log_archiver` 的用例有效）——但**在注释里标注为「部分生效，未闭环」**，
   不要让后人误以为测试已完全隔离；
2. **登记为待办**（低优先级）：`ISO-EVENTS-2 · 把评估事件隔离真正做通`；
   建议下一个人从「为什么 `active_events_file` 替换后仍写」入手，第一手工具是**在子进程内 dump `log_archiver` 的函数 id**
   （而不是像我一样先猜调用链）。
3. **不回滚那个 fixture**：它比没有好，且不影响任何测试结果（58 passed / 41 passed 均已复跑）。

### 一条给后续者的提示（我这次踩的坑）

我一开始推断「`active_events_file` 内部读模块全局 `repo_data_dir`，故改全局即可」——
**这个推断在单进程里被证实有效，但在 pytest 实跑中不成立**。
⇒ 教训：**「单进程验证有效」不等于「在 pytest 里有效」**，
  因为 pytest 会加载更多模块、可能有更早的绑定与包装层。
  与 F4 记录的「读到 0 条时先自证调用前提」同族：**先测量，再推断**。

## 更正我自己的 T4（缺陷①）：`planning`/`llm` 双重计数 **不存在**

我在主报告 T4 行里写：

> ①`planning` 与 `llm` 对同一请求**双重计数**（`orchestrator.py:1312` 无条件先执行、`:1516` 旧规划路再计一次），
> 破坏 `prometheus.py:756` 注释声称的「ratio 总和 = 1.0」

**B3 报告未实测复现（只给读码结论）**，我便自己去读了三处控制流。**结果是：我的说法不成立。**

### 实测（读码，逐处控制流）

`_record_intent_layer()` 共 11 个调用点（我 grep 全列）：`rule:751` / `template:971` / `workflow_learning:1080` /
`reject:1179` / `planning:1290` / `llm:1312` / `llm_error:1345` / `llm_low_confidence_fallback:1457` /
`planning:1516` / `semantic:2550` / `semantic_failed:2607`。

**两个 `planning` 点是互斥的，且都有显式守卫**：

| 站点 | 守卫 | 原文 |
|---|---|---|
| `:1290`（wire 规划） | `:1278 if _wire_cfg["enabled"] and self._planner and _wire_meets:` | 且成功后 **`:1292 _planning_mode = False  # 抑制旧"第四步半"段，避免双规划`** |
| `:1516`（旧第四步半） | `:1506 if planning_mode and self._planner:` | 而 `planning_mode = _planning_mode`（`:1481`）⇒ wire 成功时已被置 False ⇒ **不进入** |

**`llm` 点同样有守卫**：`:1311 if not _wire_planning_used:` ——
wire 规划成功 ⇒ `_wire_planning_used=True` ⇒ **不记 `llm`**。

### 结论

⇒ **每条请求恰好落一层**（`planning` 或 `llm` 或更早的 rule/template/…），
**「ratio 总和 = 1.0」这条不变量成立，代码里没有双重计数。**

**我在 T4 里把两件事说错了**：① 我说 `:1312`「无条件先执行」—— 它其实有 `:1311` 守卫；
② 我说「`:1516` 旧规划路再计一次」—— 它被 `:1292` 抑制。

### 我为什么当时会写错（诚实复盘）

我读到 `:1312` 与 `:1516` 两处调用**就下了结论**，**没有往上读它们的守卫行**。
这与本次审计反复强调的教训**同族**：**「看到调用点」≠「看到调用条件」**。
（我在 B1-T2 复核里用的正是正确做法：追调用图，而不是看单个站点。）

### 这条更正的实际影响

- **减少一项待修缺陷**（B3 把它列为「缺陷①需另开卡」，**现在可以撤销**）；
- 但它**不改变 P0 的结论**：六指标仍然 0/6（B3 已建好），`tool_retrieval` 的 trace_id 关联**仍然真坏**
  （B3 实测 0/2 命中，这一点我采信），零召回 6 处静默 return **仍然真存在**；
- ⇒ **T4 行的 ②③ 两条成立，① 条作废。**

## F11-B 复核：**通过（我用 API 无关探针做了 HEAD 对照，证明行为真变）**

### 我的对照实验（比它自己的 A/B 更独立）

它用「把 `trigger_tokens` 还原成改前实现」做 A/B。我没有采信这个自述，而是**用同一份探针在 HEAD 与当前两个签出上各跑一次**：

```
probe: LearningRecord(session_id='probe-s1', user_input='统计当前工作目录下有多少个 py 文件', tool_calls=[2 步])
       -> WorkflowLearner().learn(rec)  ->  打印 trigger_patterns / status

===== HEAD (5c9ace10) =====
trigger_patterns: []
status          : WorkflowStatus.DRAFT

===== CURRENT =====
trigger_patterns: ['统计', '计当', '当前', '前工', '工作']
status          : WorkflowStatus.ACTIVE
```

⇒ **同一输入、同一入口，行为确实从「零触发词 + draft」变为「2 字滑窗触发词 + active」。**
这排除了「测试与实现同源所以恒绿」的可能（探针**不依赖**它新加的 `TRIGGER_TOKENS_MAX` 等常量，
只调两边都有的 `WorkflowLearner.learn`）。

**顺带证实了它的口径纪律**：`MIN_TRIGGER_CHARS` **仍为 2**，5 个触发词**每个都是 2 字符**
⇒ **改的是切分单位，不是门槛**（它的表述准确）。

### 它请我裁决的「第 3 处越界断言」——**我批准**

卡片只授权改 `:286`/`:289`，但它实测**第 3 处同源断言**也被打穿：
`test_workflow_learning_admission.py:276`（`test_one_step_interaction_becomes_draft`）
原断言「单步中文条目的拒绝原因必须含 `NO_DISCRIMINATIVE_TRIGGER`」。

**我的判断：批准，因为它做对了三件事**（我读了改动后的 273-283 行）：
1. **保留了该用例的真正意图**：`status == DRAFT`、`"准入未通过" in description`、
   `CODE_STEPS_TOO_FEW in description` **三条都留着** ⇒ 「单步必须落草稿」仍被钉住；
2. **没有放宽政策**：它把 `in NO_DISCRIMINATIVE_TRIGGER` 改成 `not in`，
   这是**新口径下唯一正确的表述**（中文不再是无区分度触发词）；
3. **补了正向断言** `assert wf.trigger_patterns`（中文现在应拿到触发词），**强度是增加而非降低**；
4. 注释里写明「该行不在卡片点名范围，属同一口径变更导致的第 3 处过时断言」，并指向报告 §3 的实测证据；
   且**给了我一行回退方式**。

⇒ 若它机械遵守「只改两行」，该文件会**恒定 1 failed**，而那是把**过时断言**当成了护栏。
   **正确做法是把过时断言更新到新契约，而不是让主套件长期带红。**

### ⚠️ F11-B 的遗留发现（我确认为真，值得单独立项）

它给出了「近似复述是否命中」的判定标准与实测：

| 变体 | 相似度 | 命中 |
|---|---|---|
| 原样 / 语序调换 / 拆句重排 | ≈**0.854** | ✅ |
| 加虚词「请帮我…」 | 0.0025 | ❌ |
| 换同义词「总计…里有几个…」 | 0.002 | ❌ |
| 短问法 | 0.0012 | ❌ |
| **无关句** | **0.0001** | **❌（防误召 OK）** |

⇒ **判定标准是「查询是否引入了索引文本里没有的字符」，与语序无关。**
根因在 matcher 的 IDF 平滑下界：**单文档时未知字 idf=0.693 vs 已知字 0.001（差 693 倍）**；
索引变 2 文档后同一改写升到 **0.4466** ⇒ 由 ❌ 变 ✅。

**为什么这条重要**：它意味着**召回质量依赖「索引里有多少文档」** ——
**同一个改写，在 1 条工作流时漏召、在 2 条时命中**。
这不是触发词口径的问题（F11-B 明确指出与它无关），而是 **matcher 层的既有性质**，
且**在工作流数量增长时会改变行为** ⇒ 是潜在的不稳定源。已登记为待开卡。

### 验收（我独立复跑）

| 项 | 结果 |
|---|---|
| F11-B 新测试 | **21 passed** |
| 它点名的 4 文件 | **91 passed**（与改前基线同为 91 ⇒ 无用例消失） |
| 探针 HEAD 对照 | **行为真变**（上表） |

---

## ISO-EVENTS-2 · **已关闭**（主审计自己修，2026-09-26）

> 上一条 ISO-EVENTS 我记为「**未闭环**」。这一轮我把它做通了，并且**推翻了上次自己写下的归因**。

### 1. 先测量（上次我就是栽在「先推断」上）

| 时机 | 文件 sha256 | 行数 | 字节 |
|---|---|---|---|
| 跑 `test_skill_registry.py` **之前** | `83AFD1A171E72EEF…` | 1155 | 221437 |
| 跑完之后 | `CC5596DD93600BE3…` | **1156** | 221623（**+186**） |
| 再跑 `test_undo_merge_governance_state.py + test_skill_update_audit.py` | `E0E7DB1F5147AF05…` | **1157** | 221844（**+221**） |

泄漏行原文（不是我猜的，是文件里读出来的）：

```json
{"ts": "2026-09-26T00:48:38", "kind": "auto", "skill_id": "main-1", "verdict": "ok", "summary": "自动评审-评估：扩展评估未发现阻断项，可执行正式审核后发布"}
{"ts": "2026-09-26T00:48:45", "kind": "merge-undo", "skill_id": "f9-c2c-dst", "verdict": "ok", "summary": "撤销合并 f9-c2c-crafted：恢复 f9-c2c-dst（内容快照回滚，不回滚已有技能的治理状态）"}
```

**关键指纹**：`test_skill_registry.py` 跑了 **10 个用例**，却只多 **1 行**。
⇒ 不是「每个用例都漏」，而是「**只有一条漏**」。这个指纹直接指向「首次逃逸」。

### 2. 单进程探针：机制本身是**对的**

我在仓库外写探针（`%TEMP%\iso_probe.py`），**显式 import** 后替换，然后真实调用 `SkillsMgmtService.create_manual()`：

```text
la.__file__ = C:\Users\Administrator\agent\agent\skills_mgmt\log_archiver.py
patched active_events_file -> C:\Windows\TEMP\iso_probe_armaj1iy\data\skills_assessment_events.jsonl
sys.modules 里 agent.skills_mgmt.log_archiver 只有 1 个，id=2470296447520，替换后 aef_is_orig=False
tmp events exists: True bytes: 187
PROD sha before=E0E7DB1F5147AF05 after=E0E7DB1F5147AF05 changed=False
```

⇒ **替换 `active_events_file` 这条路完全有效**（tmp 落了 187 B，生产文件 sha 未变）。
**我上次写的归因「调用链上有包装层重新绑定了原函数」是错的** —— 全仓库只有 **一个** `log_archiver` 模块对象，
且 `service.py:811` 是**函数内 import**（`from .log_archiver import active_events_file`），
每次调用都会重新读模块属性，**能**拿到 patch。

### 3. 真正的根因（一行）

`tests/unit/conftest.py` 的 fixture 用 **`sys.modules.get("agent.skills_mgmt.log_archiver")`** 查找模块。
**第一个用到它的用例**在 fixture setup 时，该模块**还没被导入** ⇒ 拿到 `None` ⇒ fixture 静默什么都不做
⇒ **恰好这一条写入逃逸**。之后的用例模块已在 `sys.modules` 里，patch 生效，所以不再漏。
这与「+1 行/文件、而不是 +1 行/用例」的指纹**完全吻合**。

我当初避免 `import` 的理由是「避免导入期副作用」——**这个顾虑对这个模块不成立**：
它只是路径解析器（`repo_data_dir` / `active_events_file`），且被测代码本来就会导入它。

### 4. 修法

`tests/unit/conftest.py`：把两处 `sys.modules.get(...)` 改成**显式 `importlib.import_module(...)`**（log_archiver 与 observability.events 各一处），并在注释里写明根因与指纹。**只改查找方式，不改任何业务语义。**

### 5. 修后实测（改前/改后差分，这是本条的验收证据）

| 跑的命令 | 用例 | 行数 | `skill_id="main-1"` | `skill_id="f9-c2c-dst"` |
|---|---|---|---|---|
| 基线（未跑） | — | 1157 | 65 | 14 |
| `test_skill_registry.py` | 10 passed | **1157** | **65** | **14** |
| `test_undo_merge_governance_state.py + test_skill_update_audit.py` | 17 passed | **1157** | **65** | **14** |
| `test_undo_merge_dst_snapshot.py + test_confirm_gate_no_bypass.py`（原本就 clean 的两条） | 40 passed | **1157** | **65** | **14** |

⇒ **泄漏归零，且原本 clean 的两条没有被我改坏**。合计 **67 passed**。

### 6. 诚实的边界

- 我**只**证明了这 4 个文件 + 相关 5 个文件不再泄漏，**没有**跑完整 `tests/unit` 全量；
  原因是此刻 **G1-B 正在并发改 `data/skills_repo/` 并跑它自己的测试**，
  我此刻跑全量会与它互相污染（读到中间态），结论不可信。
  ⇒ **全量隔离复测排到 G1-B / B3-W 落地之后**，这条**仍然记账，不算已经全量证明**。
- 该文件被 `.gitignore:458` 忽略，所以这始终是**数据卫生**问题、不是正确性问题 —— 我把它做完是因为
  「测试不该写生产文件」这条本身值得钉住，**不是因为它的危害等级高**。

---

## 第 8 轮：我用**同一会话背靠背 A/B** 证实了 F3-1 的核心主张（这是本次审计最强的单条证据）

> **为什么必须我自己做**：F3-1 只测到了「改后」，改前那一半是**借用 F3 的跨会话数字**（它自己在报告里如实说明
> 「旧顺序的同用例 A/B 未由本卡实跑：端口 5678 被别卡反复抢占」）。跨会话比较无法排除
> 「两个相位处在不同缓存/负载状态」这个混杂 —— 而 F3-1 自己就吃过这个亏（它的 ident 组被 2.5 小时前的陈旧条目污染）。
> ⇒ 我把端口空出来的窗口接住，**同一份探针（`scripts/probe_prefix_cache_interaction.py`）、同一组 `vary` 用例、
> 同一会话、背靠背两相位**，用 `YUNSHU_PROMPT_VOLATILE_TAIL` 开关切换，服务每次重启。

### 原始结果

| 相位 | 间隔 min/mean/max | prompt 均值 | `sys_chars` | `sys_sha16` 去重 | **命中率 Σ** |
|---|---|---|---|---|---|
| **OFF**（`YUNSHU_PROMPT_VOLATILE_TAIL=0`，旧顺序） | 11.6 / 11.7 / 11.8 s | 8477 | **2675 / 3189 / 3191** | **3 种** | **6.04%** |
| **ON**（`=1`，易变尾簇搬到请求尾部） | 11.0 / 11.1 / 11.1 s | 8520 | **1668 / 1668 / 1668** | **1 种** | **87.64%** |

逐请求原始数（hit/miss）：
- OFF：#1 `512/7384`、#2 `512/8148`、#3 `512/8363`（每次都只有 512 命中）
- ON ：#1 `7168/748`、#2 `7552/1129`、#3 `7680/1283`

两相位 `distinct_advert_lines` 都是 **1 种**、`distinct_tool_counts` 都是 `[26]`、**宣告行偏移恒为 689**。

### 结论

1. **6.04% → 87.64%，14.5×。** 同一会话、同一探针、间隔与 prompt 长度同量级 ⇒ **混杂被排除**。
2. **机制自证**（不只是统计）：OFF 下 system prompt **逐请求不同**（3 种 sha16），ON 下 **3 次逐字相同**（1 种，1668 字符）。
   这不是相关性，是**因果**：稳定前缀必然可缓存。
3. **F3-1 的跨会话数字（5.039% → 87.62% / 86.57%）被我独立证实** —— 两套测量的量级完全一致。
4. **反向确认了 F3 的裁定 A**：宣告行偏移在两相位都是 689、去重后都是 1 种
   ⇒ **真正决定命中率的是它后面那块易变内容，不是它自己。**
   F3 当时说「不要为这行牺牲宣告=下发不变量」是对的；F3-1 进一步证明真正的杠杆在别处。
5. **顺带修正一条我方结论的量级**：本报告 §1.4 / F3 条目里「跨请求损失面 ≤640 token（≤6.8%）」应更新为
   **实测 6.04% 命中率 ⇒ 每轮约 7900/8500 token 未命中**；尾部改造后降到 **12.4%** 未命中。

### 我这次没有验证的（不要当成已验）

- `ident` 组我只跑在 F3-1 的报告里读过，**我未重测**；ON 下 ident 反而略低这件事，
  我**采信它的归因**（旧顺序命中的是 2.5 小时前的陈旧条目），但**那是它的解释、不是我的实测**。
- 记忆线索从「system 段内」改成「末尾 system 消息」是**唯一语义面变化**，**未做人评、未做自动评测**。
- 我只跑了 `vary` 一组，未跑 `workbench`（SSE）组。
- 出网预算：本次 A/B **6 次真实请求**（OFF 3 + ON 3）。
---

## 第 10 轮：我抓到 E1-F1-A 的**主修法是死分支**（已发回返工）—— 本轮最重要的一次复核

### 我怎么发现的
我没有采信它的代码注释（它写着「已在 registry 登记为 A 级」「缓存优先 ⇒ 23.2 s 就绪」），
而是**按生产入口自己跑**：`get_hybrid_retriever()._embedding._ensure_worker()`。

### 原始输出（我的实测）
```text
action=embedding.worker.ready_timeout, timeout_sec=120.0,
       model='paraphrase-multilingual-MiniLM-L12-v2'
_ensure_worker() -> False   elapsed 120.02s
worker_health mode = bm25_only | init_failed = True | worker_alive = False | available = False
```
⇒ **向量腿仍然起不来**，只是把「30 s 的失败」变成了「120 s 的失败」。

### 根因（两条对照，各 0.00 s 出结果）
```text
snapshot_download("paraphrase-multilingual-MiniLM-L12-v2", local_files_only=True)
    -> FAILED in 0.00s -> LocalEntryNotFoundError
snapshot_download("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", local_files_only=True)
    -> OK     in 0.00s -> ...\models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2\snapshots\e8f8c211...
```
**`snapshot_download` 不会像 `SentenceTransformer` 那样替你补 `sentence-transformers/` 命名空间。**
而 `tool_router_hybrid.py:59` 的 `_DEFAULT_MODEL` 是**裸名** ⇒ 它的 `local_files_only=True` **每次必抛**
⇒ `except` 每次都走到在线回退 ⇒ **那个「缓存优先」分支从来没被执行过 = 死分支**。
HF 缓存目录**确实存在**（`models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2`），**只是名字对不上**。

### 我同时抓到第二处不实（**已于同日被返工修好 —— 此条现已过时，保留原文以示过程**）
`AGENT_HYBRID_WORKER_READY_TIMEOUT` 在 `agent/settings/registry.py` 里 **grep 0 命中**，
而它的代码注释写着「已在 … 登记为 A 级」。（我这边的零缺口护栏是绿的 ⇒ 它确实没进注册表。）

**【2026-09-26 09:3x 更新】此条已被修复，不再是未决项**：返工后我复查 `agent/settings/registry.py`，
`AGENT_HYBRID_WORKER_READY_TIMEOUT` 已在 **`:2547`**（`_a` 级、默认 120.0、`CAT_SKILLS`），
且 F11-C-2 追加的 `WORKFLOW_LEARNING_GATE_ON_EVIDENCE` 在 **`:2562`**；
`test_settings_registry.py` 实测 **56 passed 全绿**。
⇒ **我的指控在提出时是真的，但已经被修好；后续读者不要把它当成未决缺陷。**
（**我把它改写成「已修复」而不是删掉** —— 记录「我先发现、卡后修复」这个过程本身有价值，
而删掉会让后来的读者以为从没出过这个问题。）

### 我为什么把「23.2 s」这条也打回去
它的**上级卡 E1** 曾报「直跑 worker 源码 55.6 s 就 ready」，**E1-F1 用同样方式跑 600 s 拿不到 ready**，
判定 E1 那个数是「**同一条重尾分布里抽到的偶发样本**」—— 这个方法论是**对**的。
但按上面这个死分支，**E1-F1-A 自己的「23.2 s 就绪」也解释不通**。
所以我在返工指令里明确要求它：**修好后能否稳定复现快速就绪；复现不了就自己写「原 23.2 s 系偶发样本」。**
**用「反证别人抽样」立论的卡，自己不能被同一个坑绊倒而沉默。**

### 我确认**保留**它的两处好设计（返工指令里明确「不要回退」）
1. **优先级 4 的孤儿修法**：它抓住了「**子进程唯一的死亡信号是 stdin EOF，而原实现把 stdin 读取放在模型加载之后**」——
   于是加载前起一个 stdin 抽水线程，EOF 即 `os._exit(0)`；父进程侧再补 `weakref.WeakSet` + `atexit`。**这是对的。**
2. **优先级 3**：超时 30→120，带非法值回退告警，且**只在预热线程生效、不在请求路径上**（拉长不增加任何一次查询时延）。

### 已发回返工（`send_message`，不是新开卡）
要求它：① 用**能命中缓存的 repo id**（裸名先试 `sentence-transformers/<name>`）重做缓存解析，
**走生产入口、不抬任何常量**证明 `mode=hybrid`；② 诚实复核 23.2 s；③ 补注册 + **交报告**（它到现在**没交** `E1-F1-A.md`）。

---

## F3-2（流式 usage 可观测性）—— 复核通过

| 我核对的 | 结果 |
|---|---|
| `memory/llm_service.py:442` | `create_kwargs["stream_options"] = {"include_usage": True}` ✓ |
| 上游不认该参数时的降级 | `:451-457` 命中 `_so_markers` ⇒ `pop` 后重试（**不硬失败**）✓ |
| `agent/llm_monitor.py:104` | `cache_reported: bool = False`，语义正确：**只有 API 明确给出缓存字段才为 True** ✓ |
| 新增测试 | `tests/unit/test_f32_stream_usage_observability.py` → **我亲跑 6 passed** ✓ |

**它最值得肯定的一点**：它**区分了「未上报」与「上报了但 0 命中」**（`cache_reported`）。
工作台提示词只有 75 字符，本就无可缓存前缀 ⇒ `hit=0` 是**真值不是缺失**。
**不做这个区分，就会把一个「真 0%」误读成「测不到」** —— 这正是本审计反复追的假绿同族问题。

**端到端证据（它跑的）**：真实 `POST /api/chat/stream` ⇒ 经**既有读路径** `GET /api/llm-monitor/records` 读到
`prompt=6310 hit=5632 miss=678 ratio=89.26%`；改前同端点只能读到 `0/0/0`。

**它的 `duration_ms` 语义变化它主动声明了**（从「create() 调用耗时 ≈290 ms」变成「整流耗时 ≈954 ms」）——
与非流式口径对齐更真实，但**这是行为变化**，下游若依赖旧口径需知悉。**主动声明行为变化是加分项。**

**它还主动披露了一处副作用**：它为端到端验证启动 `app_server.py` 时，**既有的 `server_port_guard` 逻辑
杀掉了当时占用 5678 的上一实例（另一张卡的 PID 8948）** —— 这是 **A1 的护栏在按设计工作**，但它如实上报了。
**这也解释了 E1-F1-A 为何中途失去端口。**

**未验证项我采信**（它列了 9 条）：`include_usage` 只在 `api.deepseek.com` + `deepseek-flash` 上实测；
多轮工具循环、真实「关页面」中断、其它 OpenAI 兼容网关均**未实测**；「不认该参数的降级分支」**从未真实触发过**。

### 环境

两相位各自 `Stop-Process` 服务；`listeners_after_auditOFF=0`、`listeners_after_auditON=0`、`final_listeners=0`。
脚本与原始输出在仓库外 `C:\\Users\\Administrator\\AppData\\Local\\Temp\\f3ab\\`。

---

## 第 9 轮：我亲自修好了 G1-B 留下的唯一红灯（D4 的 6 条过时用例）

**G1-B 交卡时**：`tests/unit/test_skill_update_audit.py` **6 failed / 3 passed**（原始错误「期望恰好 1 条 `skill.update`，实得 0」）。
它**没有代修**，而是标注「需你拍板」上报 —— 判断正确。

**我的裁定：G1-B 是对的，D4 的用例过时。** 理由见 §8.6。**修法不是放宽断言，是换见证字段 + 加锁**：
`description` → `tags`（逐条保留原意图），**新增** `test_description_is_frozen_by_M0` 钉住新契约，
**加强**隐私断言（被冻结字段的 SECRET 值同样不得泄漏、不得落库）。

**结果 `6 failed / 3 passed` → `10 passed`。**

### 非空转自证（变异测试，我做的）

| 步骤 | 结果 |
|---|---|
| 把 `"description"` **加回** `service.py` 的 `allowed` 白名单 | **2 failed**（`test_description_is_frozen_by_M0`、`test_secret_values_never_enter_chain`）、8 passed |
| 逐字还原白名单行 | **10 passed**；`Select-String 'allowed = {'` 显示 `:1699` 原文复原 |

⇒ **这两条断言真的在守 M0**，而不是「我改完就绿」。**禁止整文件 checkout** 的约束也守住了（只做了两次定向 edit）。


---

## 第 7 轮：G1-B **实施中途**的独立核验（我只验它已完成的部分，不下终态结论）

> **时点**：2026-09-26 00:5x，**G1-B 仍在实施中**。按「不支持并发中间态当结论」的派发规则，
> 我只把**已经稳定落盘且可复算**的部分记下来，并明确标注「这不是终态验收」。
> **口径**：我不读它的报告、不采信它的自述数字，**直接从工作区 + HEAD 差分 + 生产解析器算**。

### 7.1 M2（中文文案回写）—— **核心断言 15/15 通过**

**方法**：`git diff --name-only -- data/skills_repo` 取改动文件；对每个文件用**生产解析器**
`agent.skills_mgmt.file_store.SkillMDParser.parse()` 解析**工作区版本**与 `git show HEAD:<path>` **HEAD 版本**；
再把 `description_zh` 与 `data/skills_mgmt.json[<id>]["description"]`（主轨中文）做**逐字相等**比较。

| 断言 | 结果 |
|---|---|
| 改动的 skill.md 文件数 | **15**（与 G1-B0 §4.1「15 个」一致） |
| `description_zh` **逐字等于**主轨中文（15 条的长度也逐条相同） | **15/15 通过**，`BAD=0` |
| `description`（英文）**逐字节未变** | **15/15 通过**（解析相等 **且** 该行根本没出现在 diff 里） |

**逐条实测**（`zh_len == ref_len`）：101/101、53/53、54/54、100/100、100/100、109/109、48/48、36/36、
55/55、44/44、87/87、82/82、94/94、46/46、137/137。

### 7.2 我查了一个**声明里没写**的形态，结论是「无问题但值得记」

diff 里出现了 **YAML 折行**：长中文值被写成

```yaml
description_zh: 在进行任何创造性工作（……）之前，你【必须】先使用此流程。……由
  1 份素材蒸馏生成
```

⇒ 这是 YAML 的**多行 plain scalar 折叠**。我担心的风险是「折叠会把换行变成空格，导致解析值与源串不等」，
**实测不成立**：生产解析器读回来的值与主轨中文**逐字相等**（见 7.1 的 15/15）。
这也解释了 numstat 为什么是 8 个文件 `3/1`、7 个文件 `2/1`（长值多一行折行），**不是** D-2 之外的额外改动。

### 7.3 一次性 diff 的核对（对照 G1-B0 §4.4 的提前声明）

| 声明 | 实测 | 判定 |
|---|---|---|
| **D-1** 15 个 `pd-*` 各补 1 个末尾 CRLF | 每个文件都有 `- xxx` → `+ xxx`（`\ No newline at end of file` 消失） | **符合** |
| **D-2** 15 个各新增 1 行 `description_zh` | 15 行 | **符合** |
| **D-7** 不应出现「非 `pd-*` 文件的 description 变化」 | 改动文件**全部**是 `pd-*`（15/15） | **符合** |
| 未声明项 | YAML 折行（见 7.2） | **形态偏差，语义无影响**；建议 G1-B 在报告里补声明 |

### 7.4 顺带测得（供 M3 参考，**不是** M3 的验收）

`SkillFileStore.load_metadata_index()` 返回的字典**已经带上 `description_zh`**
（`any("description_zh" in v for v in idx.values()) == True`），且索引仍是 **23 条**。
⇒ M3 要改的是 `registry.as_legacy_rows()` 里那个**显式键字典**（G1-B0 §4.1 已点明），
与 `load_metadata_index` 这一层无关 —— 这条能帮 G1-B 少改错一处。

### 7.5 明确**没有**验证的（不要当成已验）

- M0（写路径冻结）、M1（baseline）、M3（合并规则）、M4（UI）、M5（capability）、M6（overlay 清理）、
  M7（向量）、M8（回填）、M9（守卫测试）—— **一条都没验**，因为 G1-B 还没交卡；
- 我**没有**跑它的任何测试；
- 我**没有**验证 `description_zh` 在「技能启停后仍然存在」（那是 F1b 的功劳，需要单独跑一次启停）。

⇒ 本节只能证明 **M2 的数据形态是对的**，**不能**证明 G1-B 完成。


---

## CONC · 审计链并发压测复核：**通过**（主审计独立复跑）

> 卡片：`CONC`。复核方法：**不采信它的自述数字**，我自己跑它给的脚本 + 自己读原始库。

### 我独立跑的两件事

| # | 我执行的命令 | 结果 |
|---|---|---|
| 1 | `python -m pytest tests/unit/test_audit_chain_concurrency.py -q` | **4 passed** |
| 2 | `python scripts/audit_concurrency_probe.py --processes 2 4 8 --per-process 200` | **6 轮，5600/5600 条入库**，`seq` 缺口 **0**、重复 **0**，`seq_conflict` 66 |

**生产库未被污染**：跑前/跑后 `data/audit/audit_chain.db` 的 sha256 均为 `ee3a46ecc5343cec…`（不变）。

### 它的三条书面回答我逐条采信（附我的判定）

1. **没有丢行** —— 在我测到的量级（最高 16×500 = 8000/8000）内成立；
2. **`lock.degraded` 稳态为 0，且不伴随丢行** —— 我判定为**良性降级**（降级路径是安全兜底，不是故障）；
3. **`UNIQUE constraint failed` 的根因是「写入端重复插入」**，具体是 `_drain_journal()` 的 `_inflight_seq` 是**每进程**的，
   看不见**其它进程**已 journal 但未提交的行 ⇒ 会重放别人的在途行。**这是真缺陷，不是测试问题。**

### 性能口径（它给的是分布，不是单点）

append **p50 0.101–0.112 ms / p95 0.197–0.259 ms / 6209–7163 rows/s**。

### 我为什么判定「通过」而不是「完美」

它**自己**给了边界声明与未测范围（我没要求它写，它主动写了），并登记了 2 条非阻塞风险：
**F1** `_drain_journal` 会插入其它活进程的在途行；**F2** `flush()` 屏障可能假阴性。两条都给了最小复现。
⇒ **结论采信 + 风险留档**，不把「没测到的」说成「没有」。

---

## F3 · 前缀缓存交互裁决：**裁定 A（保持现状）**（主审计复核）

> F3 = B1 的「宣告行」落在稳定节，**若下发集逐请求变**会削弱前缀缓存。这是 B1 交付时登记的待验证项。

### 我做的三项实测（都是真实请求，不是推演）

**(a) 主线连发 3 次相同请求** ⇒ 宣告行**逐字节相同**（偏移恒为 char 689；尾部长度 2504 / 2590 / 2633）。
prompt 的 sha16 **确实**逐次不同，但**差异从 char 1773 / 1859 才开始** —— 那是**记忆线索的尾巴**，**不是宣告行**。

**(b) 工作台 SSE 路径的 prompt**（`plugins/chat.py:1146`）**根本不含 `【工具】`**：
`:1184-1189` 会**先走主线装配**（`主线:engineering(26)`），
`hybrid_select_tools` 只有在**没有活跃主线**时才可达 —— 而那时 `tools=` 本身也会变。
⇒ **F3 假设的「该行逐请求变」在本部署里不成立**。

**(c) A/B 对照**：A（相同请求）命中 **5.735%**，B（同意图换措辞）**5.039%**，区间可比（11–14 s vs 11 s）
⇒ **0.7pp 的差不可归因于该行**。低命中率的真因是 **char 1698–1773 的易变尾巴**让 8700–9400 token 落空。

### 裁定

**A —— 保持现状。** B 方案实测收益 **0**，代价是**改 4 个生产渲染点 + 4 份已登记契约**。
⇒ **不值得为一处零收益的改动牺牲「宣告 = 下发」这条不变量。**

### 诚实的边界

- 混杂因素与**未测项**已在 `F3.md` 中逐条列出；
- 我**超支了出站预算**：实际 **22 次真实调用**，估的是 6–12 次。这条记在我头上。

