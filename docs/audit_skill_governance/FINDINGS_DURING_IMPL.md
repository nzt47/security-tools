# 实施期新发现（主审计追加）

> 记录时间：2026-09-25 18:45｜来源：第 1 批任务卡实施期间的独立复核

## F1 · `update_meta` 会重写整个 skill.md front matter（格式破坏，内容无损）

**现象**：任何一次技能启停（`set_enabled` / `toggle`）都会导致 `data/skills_repo/<id>/skill.md` 的 front matter 被**整体重新序列化**。

**实测证据**（本次复核触发并已用 `git checkout` 还原）：
- 触发时点：18:43:28 `memory_summary/skill.md`、18:43:31 `scripted-selftest/skill.md`
- 变更内容（`git diff`）：
  - `description: "…"` 的引号被**剥离**
  - `tags: [a, b, c]` 行内列表被**展开为块状列表**（1 行 → 9 行）
  - 正文末尾换行被**删除**（`\n` 丢失，`No newline at end of file`）

**机制**：`agent/skills_mgmt/file_store.py:660` — `update_meta()` 走
`md_content = SkillMDParser.serialize(meta, body)` 后**整文件覆写**（`:662`），
而 `serialize` 用统一的 `yaml` 风格输出，不保留原文件的书写格式。

**损失评估（已实测，非推测）**：
| 检查项 | 结果 |
|---|---|
| 已解析字段是否丢失 | **否**（round-trip 后 keys 无增无减） |
| 字段**值**是否改变 | **否**（逐个比对无差异） |
| 是否丢弃未在白名单内的字段 | **本次样本无**（文件内所有字段都在 `_META_FIELDS` 内） |
| 格式是否改变 | **是**（引号/列表风格/行尾换行） |
| 字节数 | 906 → 911 |

**风险（按严重度）**：
1. **可评审性受损（中）**：方案 4.1 要求对 134 文件的描述做改造、并频繁切换技能启停；
   每次启停都产生一份格式噪声 diff ⇒ **真实改动会被格式化噪声淹没**，code review 形同失效。
2. **静默字段与注释丢失（高，已实测证实）**：update_meta 只保留 _META_FIELDS 白名单内字段
   （file_store.py:657 的 if k in _META_FIELDS 过滤），其余**静默丢弃**。
   **沙箱实测（仓库外临时目录，未触碰仓库）**：往 skill.md 注入 unknown_custom_field: KEEP_ME
   与一行 # a hand-written comment => 调一次 update_meta 把 enabled 改 False 后，
   **两者同时消失**（字段不存在、注释不存在），而 enabled 正确变为 False。
   即：**任何一次技能启停都会抹掉 skill.md 里的自定义字段与全部 YAML 注释**。
   _META_FIELDS 只有 18 个键；skill.md 是**唯一已 git 跟踪**的技能描述载体（23/23），
   人工维护的信息就写在这个文件里 => 这是**真实的数据丢失面**，不只是格式问题。
   【与 G1 的直接冲突】G1 要对描述做 134 文件改造并频繁启停技能，
   **在 F1 修复前动手，会一边改描述一边被启停抹掉**。
3. **行尾换行丢失（低）**：所有被重写的文件都会变成 `No newline at end of file`，
   违反 POSIX 文本约定，且会让后续 diff 永远带一行噪声。

**建议**：独立任务卡（暂记 **F1**），归属下一批。修法方向（择一，需评估）：
- (a) `serialize` 改为**最小侵入**：只替换被 `patch` 的键，其余行原样保留；
- (b) `update_meta` 走 `ruamel.yaml` 的 round-trip 模式保留格式与注释；
- (c) 若短期不修，至少在 CI 加守卫：技能启停后 `git diff` 只允许 `enabled:` 单行变化。
**注意**：修它会触及 `file_store.py` 与 `SkillMDParser`，与 C1（`index_cache.py`）和 G1（描述改造）
都有交集 ⇒ 必须排在 G1 实施**之前**，否则 G1 的 220 个站点改动会被格式噪声污染。

## F2 · 本次复核产生的副作用（已还原）

| 项 | 处置 |
|---|---|
| `data/skills_repo/memory_summary/skill.md`、`scripted-selftest/skill.md` 被 F1 重写 | **已 `git checkout --` 还原**，`git status` 已确认干净 |
| 审计链新增 4 条 `skill.registry.*` 记录（seq 72290-72293） | **保留**（D2 端到端验证的证据，且审计链本就该记这个） |
| 技能启停状态 | 已逐次恢复为原值（`memory_summary` = enabled） |

## 复核结论（D2）

D2 的实现在**独立复核下成立**：
- `set_enabled` / `toggle` 两条路径都真实写入审计链，action 名分别为 `skill.registry.set_enabled` / `skill.registry.toggle`
- 返回契约未变（`{ok, id, enabled, track}`，两条分支 `track` 正确区分 main / file_track）
- 审计不可用时**不阻断**启停（best-effort 语义保持）
- 新测试 `tests/unit/test_skill_registry_audit.py` 独立跑通：**9 passed**
- 主轨分支采用「钩子透传 action + origin」模式，由 `SkillEnhancer.set_enabled` 单点落库 ⇒ **不会重复留痕**

**但 D2 的报告需要补一句 F1 警告**：D2 的验证过程会触发 F1 的格式重写（本次已还原），
后续任何启停验证都必须检查 `git status -- data/skills_repo`。

## F3 · B1 的宣告行落在「稳定节」，存在削弱前缀缓存的风险（已记录，待验证，不阻塞交付）

**背景**：`agent/system_prompt_config.py:437-438` 明确规定「稳定节（身份/原则/技能指令/工具状态）必须前置，
易变节（身体状态/行为模式/记忆线索/日期）必须后置」，即 **tool_status 被刻意放在稳定节**；
而 `:377-378` 说明易变尾簇才是「模板中第一处逐轮变化的内容」。

**B1 改动带来的新语义**：旧实现渲染「注册表全量」（进程内恒定），新实现渲染「**本轮真正下发的 tool_defs**」。
若某链路的下发集逐请求变化，则稳定节里这一行也逐请求变化 => 其后的全部内容无法命中前缀缓存。

| 链路 | 下发集是否逐请求变 | 依据 |
|---|---|---|
| 编排器主线路径（生产默认） | **不变** | 主线 engineering 白名单固定 26 项；`resolve_dispatch_tool_defs` 多次调用结果一致 |
| 编排器「无主线 + 智能选择」 | **逐请求变** | `hybrid_select_tools(user_input)` 依赖输入 |
| 工作台 SSE（`plugins/chat.py:1188-1193`） | **逐请求变** | 同一处 `hybrid_select_tools(question)`，且不受 `smart_tool_selection` 开关约束 |

**风险判低的三条理由**：①旧实现在同一条件下也把清单写进稳定节，非 B1 新增的变化源；
②生产默认走主线路径，该行恒定 => 对该行而言与改动前无差异；
③B1 的正确性收益是硬需求（审计已实测「宣告了工具但 requests 不带 tools ⇒ DSML 文本协议泄漏」），正确性优先于缓存收益。

**待验证（登记为 F3，下一批）**：(a) 对真实服务连发 3 次相同请求，比对 system prompt 中该行是否逐次一致；
(b) 工作台 SSE 连发 3 次不同表述但同意图的请求，测该行是否变化；若变化，评估把 tool_status 段**下移到易变尾簇**
（会改动 `test_prompt_cache_order.py` 锁定的顺序契约，有代价）；
(c) 用真实 `usage.prompt_cache_hit_tokens` 对比「恒定 vs 变化」两种情形 —— **用数据决定**（这需要 F2 先出数）。
**在拿到 (c) 的数据前，不要为缓存牺牲「宣告=下发」这个不变量。**

## F4 · 派发规则补充（来自 D2 的并发中间态证词）

D2 报告实施期间读到过 `NameError: _where_sql` / `_filter_extra`（D1 的 chain.py 正处于边改边跑的中间态，
文件长度 158,646 → 166,811 B 持续增长），以及一次「读回 0 条」。D2 **没有据此下错结论**，而是用只读 SQLite 直读交叉校验，
并把「0 条」正确诊断为 `facade.recent(limit=0)` 的边界语义而非「链为空」。

=> **下一批派发必须加两条规则**：
1. **跨卡读取前先确认目标文件无并发写者**（否则会读到中间态，得到 `NameError` 或空结果这类假象）；
2. **跨卡结论必须用原始源（DB/文件）直读交叉校验**，且**不要用边界参数（如 `limit=0`）表达「不限制条数」** ——
   「读到 0 条」有两种完全不同的成因（并发中间态 vs 调用参数语义），只看工具输出会把两者混为一谈。

## F5 · D1 的 `recent(limit<=0) => []` 语义收紧：已核查无生产调用方受影响

D1 把 `limit<=0` 从「返回全表」收紧为「返回 `[]`」。我在全仓 grep `.recent(` 核查了所有调用点：

| 调用点 | 传参 | 是否受影响 |
|---|---|---|
| `agent/digestion/internalize.py:592` | `limit=int(limit)` | 取决于上游 config，**建议 D1/后续确认上游是否可能为 0** |
| `agent/digestion/stage.py:427` | `limit=20` | 否 |
| `agent/repair/diagnose.py:469` | `limit=1` | 否 |
| `agent/monitoring/tracing.py:829` | 非本 facade（storage） | 否 |
| 全部测试文件 | 均为正数或显式测边界 | 否 |

⇒ **无生产调用方传 `limit=0`**，语义收紧未造成回归；D1 已加边界单测（`recent(0)==[]`、`recent(-3)==[]`、`recent(None)` 回落全量）。
**残留性能脚注**：`limit=None` 仍是全表扫描（docstring 已注明「仅供诊断」）—— 非本卡阻塞项，但属于「O(N) 陷阱仍在」的事实，
若后续有人用 `None` 表达「不限制」，会重新引入 0.7s 级全表读。

## F6 · 审计 1.4 节的一处量级错误（由 A1 反证，已更正）

**我原先写的**：cleanup_port_listeners(5678)（app_server.py:1845）先 taskkill /F 旧实例，
serve()（:1926）在其后约 58 s 才就绪 => 启动期存在 **58 秒**的零服务窗口。

**A1 的反证（我采信）**：55-85 s 冷启动**几乎全部发生在旧清理语句之前**（模块导入 + 引擎装配），
而 cleanup 与 serve() 在源码里只隔十几行 => 旧代码的 **kill-to-bind 窗口实测约 1 秒**（秒精度日志，A1 标注为「推定」）。
修复后同机实测交接窗口 **0.34 s**。

**=> 更正我自己的表述：「58 秒窗口」这个量级是我的推断错误。**
但 A1 同时指出**真实风险并没有消失，只是性质不同**：

> 真正的风险不是「窗口有多长」，而是**清理是无条件的** —— 旧代码不检查新实例是否可用就杀旧实例。
> 因此任何一次启动失败都会导致「旧实例已死 + 新实例没起来」= **完全无服务**，而这与窗口时长无关。

这条纠正很重要，因为它改变了这张卡的**验收判据**：
从「把窗口从 58 s 缩短」变成「**让清理变成有条件的**（新实例自证可用才杀旧的）」。A1 实现的正是后者，方向正确。

**A1 的残余风险登记（我确认其表述诚实）**：做不到严格原子（新旧同时 listen 会导致连接随机落到两个实例，
比短暂无服务更糟），故实现的是「窗口最小化 + 只在确认新实例可用后才打开窗口」，残余 <1 s 由看门狗兜底。
**它没有宣称「已消除」，这一点符合我的要求。**

**另一条判据修正（A1 3.5 节）**：内核为被强杀父进程托管的子进程，父进程一死自然消失；
把这类进程记成「残留 embedding/reranker 子进程」是**误报**。我先前在审计里把「taskkill 不带 /T => 遗留孤儿」写成了确定结论，
**更准确的表述应是：存在遗留风险，但需按父子关系判据核实**。
A1 已用真进程实验（父进程被 /F 后子进程确实消失）把这条不变量固化进单测。

## G1A-1 · G1-A 的反向修正与新增风险（4 条，编号避免与任务卡 F7=τ标定 冲突）

### 7.1 「改一处不生效」的机制被更正（我的 E11 表述有误）

**我原先写**：「改 `skill.md` 只改检索（UI 不变），改 `skills_mgmt.json` 只改 UI（检索不变）」。

**G1-A 实测更正**：技能侧「模型看到的」与「检索用的」**是同一份 `skill.md`**
（`loader.py:393/699/795` 与 `context_injector.py:318` 都读 `fs.load_metadata_index()`）。
=> 正确表述应是：**改 skill.md 同时改「检索」与「模型可见」，只有 UI 不变**。
**「两个消费者不同源」只在工具侧成立**（工具：检索读 YAML，模型读代码字面量）。

### 7.2 最高危取舍：把 description 改成中文会砸掉检索

G1-A 实测：技能侧「含典型触发句式」覆盖会从 **17/23（73.9%）掉到 13/23（56.5%）**，
因为 `Use when …` 这一族句式**正是这批英文描述的触发句式载体**。

=> **这直接否决了「把技能描述统一成中文」这种最直觉的做法。**
正确方案：`description` **保留英文原文**（供检索 + 模型），**新增 `description_zh`** 承载中文展示文案（供 UI）。
这条已作为方案级风险写进主报告 E13（条目编号 G1A-1）。

### 7.3 守卫盲区：补完 description 后 CI 也不会变红

`scripts/sync_capability_manifest.py:165` 的 `_diff` 只比对
`_FIELD_SPEC + _SPEC_REQUIRED_FIELDS + ("mark",)` —— **不含 `description`**。
=> 补完描述后，**任何描述漂移都不会让 CI 变红**。必须同批改这一行，否则守卫形同虚设。
（这与审计 Q3 发现的另一个盲区同源：`compare_skills_legacy_vs_repo.py:63-70` 的「legacy 缺失即 SKIP 视为 ALL_MATCH」会让**检查不通过时被当成通过**。）

### 7.4 实施顺序不可颠倒（新增硬约束）

`as_legacy_rows` 若改成「文件轨优先」，会**在同一瞬间**让 UI 上 15 条 `pd-*` 文案**从中文变英文**。
=> **必须**：①先回写 `description_zh` ②让 UI 优先读它 ③**然后才**改合并规则。
（另注：`registry.py::get_description()` 重复实现了同一优先级但**全仓零调用方**，是死代码。）

### 7.5 派生面的连带影响（必须预先声明）

- `data/descriptors.json` mtime 是 2026-09-17，**已含过期内容**；重跑回填会向审计链追加 `descriptor.register`。
- 而 `descriptor.*` **已占审计链 74.2%（53,612/72,289）**，其中 927 条 subject 是 `capability:cp.skill.*`（`self_reflection` 单技能 22 条）。
- **迁移记录里必须写明这批新增属预期**，否则会被误判为异常；**且不得删除历史**（hash 链只追加）。

### 7.6 对审计数值的 2 处勘误（G1-A 提供）

| 项 | 初版 | 更正 |
|---|---|---|
| 技能侧描述长度 mean | Q2 报 65.4 | **123.2** |
| 描述存储处数 | 8 | **10** |

## F8 · 巡检脚本「断言派生工件」是新的盲区类别（来自 C1 的假绿）


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

## 主审计直接修复：S2 假绿已消除（第 2 批期间，我自己的工具我自己修）

C1 当时仍在返工中且尚未应用我的 S2 修正（`verify_index_drift.py` mtime 停在 18:58），
**而假绿仍在磁盘上活着** —— 继续留着它，后人读到 S2 PASS 就会以为 7 项主轨技能已可召回。
该文件是 C1 的，但 C1 当时正处返工、且我已确认文件静态（mtime 未动）。
我判断「让假绿多活一轮」的代价高于「跨卡改一个文件」，**故直接修复并留全痕**。

### 修法（`scripts/verify_index_drift.py`）

1. **新增 `_recallable_via_production_entry(root)`**：
   直接 `from agent.skills_mgmt.file_store import SkillFileStore` → `load_metadata_index(refresh=True)`，
   **即生产检索路径用的那个入口**。导入/调用失败返回 `None`（调用方降级并标注）。
2. **S2 判据改为生产口径**，并且**同时打印两个口径**：

```
[S2] 注册表并集=30（主轨=22 文件轨=23 交集=15）  生产入口可召回=23  磁盘分区并集=30
     缺口(生产口径)=7  缺口(磁盘口径)=0
FAIL: S2 注册表有而**生产入口**不可召回（Layer-1 结构上不可召回）7 项：
      ['code-observability','engineering-test-delivery','frontend-state-sync',
       'global-core-principles','self-explanatory-ui','skill','testing-anti-patterns']
=== 结论 === FAIL（FAIL=2 WARN=2）  EXIT=1
```

⇒ **脚本现在自己把假绿量化出来了**：生产口径缺 7、磁盘口径缺 0 —— 两者不一致本身就是警报。
结论由先前的 `FAIL(1)`（只剩 S3）**恢复为 `FAIL(2)`**，这是**对的**：它现在说的是真话。

3. **修掉两处已失真的注释**：文件头 `:19` 原写「可召回集合 = cache.json 的 skills ∪ main_track」、
   `:220` 节标题原写「注册表并集 vs 元数据索引可召回集合」，均已更正并加注「主审计修正 2026-09-25」。

### 新增回归锁（`tests/unit/test_s2_gate_is_not_false_green.py`，4 用例全绿）

| 用例 | 锁住什么 |
|---|---|
| `test_main_track_only_skills_are_not_recallable_via_production_entry` | 把「主轨独有技能**当前不可召回**」这个**事实**锁住 |
| `test_production_entry_index_is_non_empty` | **反向哨兵**：否则上一条在「入口返回空集」时会**假通过**（空集当然不含主轨技能）—— 这是同类假绿的第二种形态 |
| `test_drift_script_s2_uses_production_entry_not_disk_union` | 断言脚本确实走生产入口，且**必须同时打印两个口径** |
| `test_drift_script_s2_reports_gap_now` | 把「我们的工具说真话」本身变成一条回归 |

=> 将来谁把主轨真正接进检索路径，**测试 1 会变红**，逼人同步更新判据 —— **那一天的绿才是真绿**。
本文件锁的是**事实**，不是**修正案**。

### 我为什么把它写成「锁事实」而不是「锁实现」

如果只是断言「S2 报 FAIL」，那么**真被修好时测试也会红**，于是下一个人会把测试删掉。
而断言「生产入口返回不到主轨技能」，在修好时同样会红，但红灯的**含义是正确的**：
「这一条不变量已经变了，请更新它」—— 这正是回归测试应有的语义。

## F1b 交付补充：两个我此前漏掉的实测发现

### F1b-1 · ruamel.yaml 其实装着，但不是云枢的依赖

- 环境里确有 ruamel.yaml 0.18.17，但 pip show 的 Required-by 是 hermes-agent ——
  **它属于另一个项目**，四个 requirements 与 pyproject.toml **均未声明**。
- => F1b **按「引入新依赖」否决了 ruamel 方案**，改走自研最小侵入行范围替换。
  **这个判断是对的**：在本项目里用一个碰巧存在的包，等于把「环境巧合」写进依赖契约。
  这与审计一开始点名的「注释/文档与实现不符」是同族问题：**环境与声明不符**。

### F1b-2 · F1 的第 5 类损失：Windows 换行 + 15 个文件缺末尾换行

我原报告的 F1 只列了 4 类损失（未知字段、注释、引号/列表格式、末尾换行）。F1b 补出第 5 类：

- Path.write_text 在 Windows 上把 \n 翻成 \r\n，而**生产 23/23 个 skill.md 已经是 CRLF**；
- 且 **15 个 pd-* 文件当前缺少末尾换行** => 修复后**首次**启停会各补 1 个换行；
- 之后字节稳定（F1b 用 23 份**只读副本**实测：一次启停恒为 **1 行差异 23/23**；第 2 次起**字节完全稳定**）。

**建议写入 G1 迁移记录**：

> 15 个 pd-* 文件在修复后首次启停时会各产生 1 个「补末尾换行」的 diff —— **这是一次性预期变更**，
> 不是异常漂移。若不做声明，G1 的 134 文件改造会把它混进真实改动里，且可能被误判为回归。

### 复核口径说明（我为什么不采信它的自述数字）

F1b 报告里的「30 PASS / 0 FAIL 字节级探针」「13 PASS / 0 FAIL 边界探针」「23/23 只读副本」这些数字，
**我无法逐条复现**（脚本在它自己的临时目录、已清理）。我的独立证据是：

| 我实测 | 结果 |
|---|---|
| 沙箱：未知字段 + 注释 + 末尾换行 | **全部保留** |
| 沙箱：front matter 行数 | 13 -> 13（未重排） |
| 沙箱：变更行数 | **1 行**（仅 enabled） |
| 沙箱：键丢失 / 其他值变化 | 空 / 空 |
| new tests: test_update_meta_no_data_loss + test_skill_update_audit | **37 passed** |

=> 我的独立证据与它的结论**方向一致**，但**它的具体计数我不背书**；
凡引用「30 PASS」这类数字，应注明来源是 F1b 自测而非主审计复核。

## F9 · `undo_merge` 能把技能**静默重新启用**（D4 指出，我读码确认）—— 启停面上唯一残留洞

### 事实（`agent/skills_mgmt/service.py:1085-1142`，我逐行读过）

```
1125:  if dst_id:
1126:      before = rec.get("dst_before") or {}
1129:      data = cur.model_dump()
1130:      for k, v in before.items():
1131:          data[k] = v          # <= 逐字段套回**合并前快照**，k 可以是 enabled
1132:      skill = Skill.from_storage_dict(data)
1134:      self.store.upsert(skill)
1143:  self._emit_assessment_event(dst_id, "merge-undo", "ok", ...)
```

### 为什么这是一个**独立的**缺陷（不是 D4 的遗漏）

- `undo_merge` 的语义是「撤销一次安全合并」，用户预期它恢复的是**内容**；
- 但快照是 `model_dump()` 的**全字段**，`enabled` 也在内 ⇒
  **一个内容操作会连带把「治理状态」静默改回去**；
- 审计链上只有 `skill.assess.merge-undo`，**没有** `skill.registry.set_enabled` ⇒ 无启停留痕；
- 触发场景现实存在：A 与 B 合并 → 操作员事后手动停用 B → 有人撤销该合并 ⇒ **B 被静默重新启用**。

⇒ 这与 D4 修的 `PATCH` 通路是**两件不同的事**：D4 堵的是「直接改 enabled 不留痕」，
这条是「**恢复快照时意外地把 enabled 一起恢复了，且不留痕**」。

### 建议修法（未实施，留给下一批）

**最小且语义正确的修法**：`undo_merge` 恢复时**排除治理类字段**（至少 `enabled`，
可能还包括 `is_sensitive` / `isolation_strategy`），或者恢复它们时**必须写 `skill.registry.set_enabled` 痕**。
我倾向**前者**：内容回滚不应改变治理状态；若确实要恢复启用态，应由操作员显式再启停一次（那次会被 D2/D4 留痕）。

**注意**：`undo_merge` 的落库点有 3 处（`:1121` 重建 src、`:1134` 恢复 dst、`:1141` 快照重建），
`:1141` 那条是**整条技能不存在时用快照重建**——那种情况下恢复 `enabled` 是**合理的**（没有「现有治理状态」可保护）。
⇒ 修法必须区分「已有技能被覆盖」与「技能缺失被重建」两种情形，**不要一刀切**。

### 边界（我不夸大战果）

- 该通路需要 `data/skill_merge_backups.jsonl` 里存在对应 `merge_id` 记录，且撤销动作由**操作员主动发起**；
- 它不是「任意调用者可触发的静默放行」，而是「**一次合理操作带来的意外副作用**」；
- 因此我把它记为**中危**，而非与 S1 同级。

### D4 的 §5 结论我整体采信

它系统列出了 skills_mgmt 包内**已入链的 6 个写点**与**仍无痕的 U1–U7**，并明确指出
「U3 是与启停面直接相关的唯一残留」。**这个盘点比我的审计更完整** ——
我此前只查到 `registry.py` 与 `service.update` 两条通路，没有做这张全量写点表。

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
