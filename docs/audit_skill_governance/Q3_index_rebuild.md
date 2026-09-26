# Q3 · 索引重建：改注册表后 `.index` / `.vector_index` 是否自动重建？有无现存漂移？

> 审计编号：Q3　范围：技能索引 `data/skills_repo/.index/`、向量索引 `data/skill_vectors/`、工具索引 `data/tool_index.json`、注册表 `data/skills_mgmt.json` / `data/skills_repo/` / `data/tool_definitions/`
> 审计方式：**只读**。未修改仓库任何文件（本报告除外）、未启动服务、未跑 pytest、未 git commit。
> 所有数字来自本次实际读取/统计（Python 3.12.0 + PyYAML + sqlite3 只读 URI）。
> 无证据的推断一律显式标注【推测】。
> **副作用声明**：第 6 节巡检中的一次 GET /api/health/retrieval（对**已在运行**的 127.0.0.1:5678）可能触发了 `get_hybrid_retriever()` 的单例懒创建与 preheat（详见 §5.6 末注）。除此之外未对系统做任何写操作。

---

## 0. 结论速览

| # | 问题 | 结论 | 关键证据 |
|---|---|---|---|
| 1 | 索引的物理载体 | **3 类**：`data/skills_repo/.index/cache.json`（元数据索引）、`data/skill_vectors/*`（向量索引：chromadb + 自管 JSON）、`data/tool_index.json`（工具索引）。落盘文件共 **8 个**（§1.1） | 实测 |
| 2 | `.vector_index` 是什么 | **空壳**。目录里只有 `.gitkeep`（172 B），全仓库 **0 处代码引用**该路径；其注释声称「技能数 > upgrade_threshold(30) 后使用」，但该阈值只产出**报告**、不启用任何索引 | `data/skills_repo/.vector_index/.gitkeep:1-2`；全仓 grep `vector_index` 仅命中 `agent/skills_mgmt/vector_adapter.py:431` 的一个 metric 名 |
| 3 | 改注册表后是否自动重建 | **不会**。三条链路各有断裂（§2） | 见 §2.4 四处窗口期 |
| 4 | 工具索引：YAML 改了会重建吗 | **不会自动**。只能手跑 `python scripts/sync_tool_index.py`；`--check` 模式**只校验 YAML 语法、不比对索引新旧** | `scripts/sync_tool_index.py:356-358` |
| 5 | 工具索引现存漂移 | **内容 0 漂移**（90/90 字段一致），但 `generated_at`(2026-09-18 22:47:33) 落后于 **91/91** 个 YAML 的 mtime(2026-09-20) = 已致「陈旧标记」但内容未漂 | 实测 §3.2 |
| 6 | 技能元数据索引现存漂移 | **7 项结构性缺失**：注册表并集 **30**，`.index/cache.json` 只覆盖 **23**（=文件轨），主轨独有 7 项（`global-core-principles` 等）在 Layer-1 **结构上不可召回** | `agent/skills_mgmt/index_cache.py:298`（只扫 repo_path） |
| 7 | 技能向量索引现存漂移 | **严重**：落盘向量库只含 **8** 条，注册表并集 **30** ⇒ **未覆盖 22 项（73.3%）**；其中 **15 个 pd-*-skill 完全缺失**，索引冻结于 **2026-07-23 17:07:59** | 实测 `data/skill_vectors/native_chroma/chroma.sqlite3` |
| 8 | 已索引的 8 条内容是否也漂移 | **否**。8/8 条目用 `_build_vector_text` 逐字重建后与库内原文**完全一致**（仅 2 条差 1 个尾部换行）⇒ 是**覆盖缺口**而非内容陈旧 | 实测 §3.4 |
| 9 | worker 挂掉时是静默空召回吗 | **工具链：不是**（显式降级 BM25-only + `/api/health/retrieval` 可见）。**技能链：是近似静默**——`SkillVectorAdapter.search()` 超时/异常/后端缺失一律 `return []`（3 处），仅在 loader 侧降级到 TF-IDF | `agent/skills_mgmt/vector_adapter.py:707,748,751` |
| 10 | 隔离子进程是否覆盖技能 Embedding | **不覆盖**。子进程隔离只存在于工具链（MiniLM）；**技能链默认模型 BGE-m3 在主进程内** `SentenceTransformer(...)` 加载 | `agent/skills_mgmt/vector_adapter.py:257-260` |
| 11 | 「子进程探测 + TTL 缓存」是否生效 | **死代码**。`_ensure_st_checked()` **无任何调用点**；`data/.embedding_probe` 里 `available=false`（2026-07-23）对本系统**无影响** | 全仓 grep `_ensure_st_checked` 仅 1 处定义 + 1 处注释 |

---

## 1. 索引的物理载体

### 1.1 落盘载体（实测 8 个文件）

| # | 路径 | 大小(B) | 最后修改 | 条目数 | 覆盖对象 |
|---|---|---|---|---|---|
| 1 | `data/skills_repo/.index/cache.json` | 14,646 | 2026-09-21 20:55:14 | 23 skills + 23 meta | **仅文件轨** `skills_repo/*/skill.md` |
| 2 | `data/skills_repo/.vector_index/.gitkeep` | 172 | 2026-07-29 12:11:19 | 0 | 无（占位） |
| 3 | `data/skill_vectors/native_chroma/chroma.sqlite3` | 413,696 | 2026-07-27 06:30:26 | **8** embeddings | 8/23 文件轨技能 |
| 4 | `data/skill_vectors/native_chroma/878bb031-294d-423b-94d5-7dd1fd758459/data_level0.bin` | 167,600 | 2026-07-27 06:30:26 | HNSW 段 | 同上（384 维） |
| 5 | `…/header.bin`、`…/length.bin`、`…/link_lists.bin` | 100 / 400 / 0 | 2026-07-27 06:30:26 | — | 同上 |
| 6 | `data/skill_vectors/chroma.sqlite3` | 188,416 | 2026-07-28 13:54:11 | **0** embeddings | 空（VectorStore 路径） |
| 7 | `data/skill_vectors/skill_metadata.json` | 140,723 | 2026-07-29 00:18:06 | 80 = 8 技能 × 10 chunk | VectorStore 自管元数据 |
| 8 | `data/tool_index.json` | 39,741 | 2026-09-18 22:47:33 | 90 tools | `data/tool_definitions/*.yaml` |

- `cache.json` 结构：`{cache_version:"1.0", skills:{id→front matter}, meta:{id→{mtime,hash}}}{BT}}；版本常量见 `agent/skills_mgmt/index_cache.py:46`；持久化白名单字段实测并集 = author/category/content_type/default_params/description/enabled/id/name/source/status/tags/version（12 个），与 `_META_FIELDS`（`agent/skills_mgmt/file_store.py:74-81`）一致。
- `skill_metadata.json` 是 `VectorStore` 的元数据文件：`memory/vector_store/vector_store.py:456` `self._storage_path = os.path.join(persist_dir, f"{collection_name}.json")`，其中 `collection_name="skill_metadata"`（`vector_adapter.py:44`）、`persist_dir="./data/skill_vectors"`（`vector_adapter.py:49`）。
- chroma 集合实测：`collections` 表 `name='skill_metadata', dimension=384`（= all-MiniLM-L6-v2 口径，与 `vector_adapter.py:271-303` 注释一致）。
- 原子写：`persist()` 先写 `cache.json.tmp` 再 `replace`（`index_cache.py:204-208`）；实测目录内**无**残留 `.tmp`。

### 1.2 只在内存、无物理载体的索引（**重启即丢**）

| 索引 | 载体 | 位置 |
|---|---|---|
| 技能向量（BGE-m3，**当前默认路**） | `(model, doc_ids, doc_vectors, doc_metas)` numpy 元组 | `agent/skills_mgmt/vector_adapter.py:118-120, 266, 400` |
| 工具 Embedding（MiniLM） | `self._embeddings` numpy + `self._doc_ids` | `agent/tool_router_hybrid.py:757` |
| 工具 BM25 倒排 | `BM25Index` dict | `agent/tool_router_hybrid.py:466-663` |
| 技能 BM25 | `BM25Okapi` + tokenized docs | `agent/skills_mgmt/bm25_searcher.py:154-159` |
| 技能 TF-IDF 倒排 | loader 内存 | `agent/skills_mgmt/loader.py:284, 551` |

> 即：**BGE-m3 路的向量索引没有任何落盘**。`tests/unit/test_vector_skill_searcher.py:294` 也承认「_st_backend 模式无磁盘持久化（numpy 数组在内存）」。

### 1.3 `.vector_index` 是死目录（与自身注释矛盾）

`.gitkeep` 原文：`# 预留目录：未来向量检索索引（如 ChromaDB / FAISS）持久化位置` / `# 当前为空，仅在技能数超过 upgrade_threshold(30) 启用向量检索后使用`。

实测反证：
- 全仓库（含 yaml/md/json/py）grep `vector_index` 只命中 1 处，且是 metric 名 `"yunshu_skill_vector_index_count"`（`agent/skills_mgmt/vector_adapter.py:431`），**没有任何代码用这个目录**。
- `upgrade_threshold` 的唯一用途是**只读建议**：`agent/skills_mgmt/lifecycle.py:275`；模块文档明确「仅报告，不改配置」（`lifecycle.py:8`）。
- 当前技能总数 30（注册表并集）已在阈值附近，但向量索引并未因此写入该目录。

---

## 2. 写入链路（谁写、何时写）

### 2.1 技能元数据索引 `data/skills_repo/.index/cache.json`

| 环节 | 位置 |
|---|---|
| 启动加载 | `SkillsMgmtService.__init__` → `SkillIndexCache(self.file_store)` + `load_on_startup()` — `agent/skills_mgmt/service.py:80-81`；实现 `index_cache.py:79-127` |
| 首次访问懒构建 | `get_all_metadata()`：缓存空 → `_rebuild_locked()` — `index_cache.py:156-164` |
| 增量校验（每次访问） | `_validate_all_locked()`（stat + md5），有变化才重解析 — `index_cache.py:309-345` |
| 失效判据 | **mtime + md5 双校验** — `index_cache.py:237-255` |
| 写盘触发 | 仅当 `changed=True` 时 `persist()` — `index_cache.py:162-163`；`rebuild()` 也 persist（`:182`） |
| 写入钩子（同进程内） | `file_store.create/update_meta/delete` → `_invalidate_index_cache` — `file_store.py:629 / 669 / 711` → `file_store.py:455-466` → `index_cache.py:166-174` |
| 扫描范围 | `Path(self.fs.repo_path).iterdir()`，**跳过** `_` / `.` 前缀 — `index_cache.py:298-307` |

**结论**：语义上是「**启动加载 + 首次访问全量/增量校验 + 变更时内存失效**」，**没有定时重建**，也**不感知技能目录之外的注册表**。

### 2.2 技能向量索引（BGE-m3，内存）

| 环节 | 位置 |
|---|---|
| 懒创建 + 注册写钩子 | `SkillLoader._get_vector_adapter()`：首次 `use_vector=True` 时才构造，并 `fs.register_write_hook(lambda sid, action: adapter.upsert(sid))` — `loader.py:611-638`（钩子注册 `:628-630`） |
| 增量策略 | `ensure_indexed()`：`new_ids = current_ids - self._indexed_skill_ids` — `vector_adapter.py:341-353`；**只补新增 id** |
| 单技能变更 | `upsert()` = 先 `_remove_skill_vector` 再 `ensure_indexed()` — `vector_adapter.py:441-482, 484-525` |
| 钩子挂载语义 | `register_write_hook` 是 **append**（`file_store.py:342`），由 `_notify_hooks` 逐个调用（`:344-354`），且**锁外**执行 |
| 索引源 | `self.fs.load_metadata_index()` — `vector_adapter.py:341`（**= 文件轨 23 条**） |

### 2.3 工具索引 `data/tool_index.json`

| 环节 | 位置 |
|---|---|
| 生成（**唯一入口，需手动跑**） | `scripts/sync_tool_index.py`：读 `data/tool_definitions/*.yaml` → `_build_index()` → 写 `data/tool_index.json` — `sync_tool_index.py:34-36, 249-279, 360-364` |
| 过滤规则 | `internal:true` / `llm_callable:false` / `callable_mode:manual` 不入索引 — `sync_tool_index.py:234-246`（实测排除 1 个：`process_distill_run`） |
| 进程内构建 | `HybridRetriever.__init__` → `_load_and_build_index()` → `rebuild()` — `tool_router_hybrid.py:1373, 1389-1421, 1423-1447`；**单例，进程内只构建一次** — `:1598-1619` |
| 热重载 / 文件监听 | **不存在**。对该文件 grep `mtime|watchdog|reload|getmtime` = **0 命中** |
| 启动时自动重生成 | **不存在**。`app_server.py` grep `tool_index` = **0 命中** |
| 定时重建 | **不存在**。`skills_mgmt` 下的调度器只有 `cleanup_scheduler.py:136`、`learning_scheduler.py:32`、`evolution_scheduler.py`，均不碰索引 |

### 2.4 「改了源但索引没变」的真实窗口期（4 处）

| # | 场景 | 断在哪 | 窗口长度 | 证据 |
|---|---|---|---|---|
| **W1** | 通过注册表（主轨）改技能 —— 启停/描述：`SkillRegistry.set_enabled` → `svc.set_enabled` → `SkillEnhancer.set_enabled` → `store.upsert` | 只写 `data/skills_mgmt.json`，**不写 skill.md** ⇒ 不触发 `_notify_hooks` ⇒ 元数据索引与向量索引均不变 | **永久**（主轨独有技能结构上永远进不了 `.index` / 向量索引） | `registry.py:118-144`、`service.py:1981-1982`、`enhancer.py:679-692`、`service.py:58` |
| **W2** | 绕过 `SkillFileStore` 直接改 `skill.md`（git pull/merge、脚本批改、手工编辑） | 钩子不触发 ⇒ `upsert` 不调用；而 `ensure_indexed` 只按 **id 集合差**补新（`vector_adapter.py:349`）⇒ **同一 id 内容变了永不重编码** | 直到进程重启 | `git_sync.py:348-354`（git pull）、`:401-462`（checkout/merge/checkout -- 冲突解决）；`vector_adapter.py:342,349` |
| **W3** | 改 `data/tool_definitions/*.yaml` | 无钩子、无 watcher；必须手跑 `sync_tool_index.py`；**跑完还要重启服务**（单例内存索引不重载） | **两级无界** | `tool_router_hybrid.py:11-12` 自述需 `sync_tool_index.py`；`:1598-1619` 单例 |
| **W4** | 想靠 CI 拦住 W3 | `sync_tool_index.py --check` **只校验 YAML 合法性并直接 return 0**，根本不比对索引 | — | `sync_tool_index.py:356-358` |

**唯一的兜底守卫**（且各自只守一半）：

| 守卫 | 覆盖内容 | 不覆盖 |
|---|---|---|
| `tests/unit/test_tool_router_hybrid_real_index_l28.py:107-112` | YAML 派生工具集与生产索引的**名字集合相等**、总数 = 90 | description / version / category / parameter_names 的**内容**漂移 |
| `tests/unit/test_agent_lines.py:440-448` | `internal` 工具不得泄漏进索引 | 索引新鲜度 |
| `scripts/compare_skills_legacy_vs_repo.py`（nightly） | `data/skills.json` vs `skills_repo` 的 4 字段 + 差集；**legacy 缺失即整体 SKIP 并视为 ALL_MATCH** | 完全不涉及 `.index/cache.json`、`data/skill_vectors/`、主轨 JSON |
| — | — | **无任何守卫**：`.index/cache.json` 覆盖度、向量索引覆盖度、工具索引内容新鲜度 |

---

## 3. 实测漂移

### 3.1 解析方式（索引是二进制吗？）

| 载体 | 可解析性 | 解析方式 |
|---|---|---|
| `.index/cache.json` | ✅ 纯 JSON | `json.load` 直接读 `skills` / `meta` |
| `data/tool_index.json` | ✅ 纯 JSON | `json.load` → `tools[].name` |
| `data/skill_vectors/skill_metadata.json` | ✅ 纯 JSON | 列表，条目含 `id` / `metadata.skill_id` |
| `data/skill_vectors/*/chroma.sqlite3` | ⚠️ 非普通业务表，但**可离线只读解出条目集** | `sqlite3.connect("file:…?mode=ro", uri=True)`：`SELECT embedding_id FROM embeddings` 拿用户侧文档 ID（形如 `skill_<sid>`）；`SELECT id,key,string_value FROM embedding_metadata` 拿 `skill_id/name/description/enabled/version/chroma:document`；`collections.dimension` 拿维度。**无需启动 chromadb、无需 embedding 模型** |
| `*.bin`（HNSW 段） | ❌ 不可直接解析 | 不解析；条目集以 sqlite 为准 |

### 3.2 工具侧：**内容 0 漂移**

| 指标 | 实测值 |
|---|---|
| `data/tool_definitions/*.yaml` | **91** 个 |
| 其中被 `internal`/`llm_callable`/`callable_mode` 判隐 | **1**（`process_distill_run.yaml`，三者同时命中，属**设计内**排除） |
| YAML 可见集 | **90** |
| `data/tool_index.json` 条目 | **90** |
| 差集 | 缺失 0、多余 0 |
| **字段级内容漂移**（description / version / category / deprecated / parameter_names） | **0 项** |
| `tool_index.json.generated_at` | `2026-09-18T22:47:33`（与文件 mtime 一致） |
| 比索引更新的 YAML | **91 / 91**（最新 2026-09-20 17:13:29） |

**判定**：**「mtime 已漂、内容未漂」**。不能只用 mtime 判漂移（会 91/91 全红），必须比对内容；同时 `generated_at` 落后 2 天说明**最后一次 YAML 批量改动（2026-09-20 10:13–17:13）之后没人重跑过 `sync_tool_index.py`** —— 本次改动恰好没动那 5 个被索引字段，属**运气**。

### 3.3 技能注册表 vs 元数据索引

| 集合 | 数量 | 说明 |
|---|---|---|
| 主轨 `data/skills_mgmt.json` | **22** | mtime 2026-09-23 01:20:00 |
| 文件轨 `data/skills_repo/*/skill.md` | **23** | — |
| 交集 | **15** | — |
| **主轨独有** | **7** | `code-observability`、`engineering-test-delivery`、`frontend-state-sync`、`global-core-principles`、`self-explanatory-ui`、`skill`、`testing-anti-patterns` |
| 文件轨独有 | 8 | `context_aware`/`emotion_expression`/`memory_summary`/`proactive_suggestion`/`safety_guard`/`scripted-selftest`/`self_reflection`/`voice_interaction` |
| **注册表并集** | **30** | 与 legacy `data/skills.json` 的 30 行一致 |
| `.index/cache.json` 条目 | **23** | 缺失 0、多余 0、**hash 失效 0**（相对文件轨） |
| **覆盖缺口** | **7 / 30 = 23.3%** | 恰为上面 7 个主轨独有技能 |

**根因**：`SkillIndexCache._rebuild_locked` 只扫 `skills_repo`（`index_cache.py:298`）；而 `SkillRegistry.list_skill_ids()` 是「主轨 ∪ 文件轨」（`registry.py:90-103`）。创建技能的写路径 `SkillsMgmtService.create_manual` → `SkillCreator` → `self._store.upsert(skill)`（`creator.py:200`）**只写主轨**，不落 `skills_repo` ⇒ 新技能**天生不进 Layer-1 索引**。

**附带实测（当前为 0）**：15 个交集技能的 `enabled` 在「主轨 vs 文件轨 front matter」**冲突 0 项**，三轨均无 `enabled=false`。⇒ W1 目前是**结构风险**而非已发生漂移。

### 3.4 技能向量索引 vs 注册表（**最严重**）

| 指标 | 实测值 |
|---|---|
| 落盘向量库路径 | `data/skill_vectors/native_chroma/chroma.sqlite3` |
| 集合名 / 维度 | `skill_metadata` / **384** |
| **索引内条目** | **8** |
| 条目 ID | `skill_context_aware`、`skill_emotion_expression`、`skill_memory_summary`、`skill_proactive_suggestion`、`skill_safety_guard`、`skill_scripted-selftest`、`skill_self_reflection`、`skill_voice_interaction` |
| 条目写入时间（`embeddings.created_at`） | **2026-07-23 17:07:59**（文件 mtime 2026-07-27 06:30:26） |
| 注册表并集 | **30** |
| **未覆盖** | **22**（7 主轨独有 + **15 个 pd-*-skill**） |
| 索引多余（不在注册表） | **0** |
| **覆盖率** | **8/30 = 26.7%** |
| 已索引条目内容是否陈旧 | **否**。按 `_build_vector_text`（`vector_adapter.py:142-173`：name + description + tags + category + body[:500]）逐字重建，**8/8 完全一致**（`emotion_expression`/`self_reflection` 仅差 1 个尾部换行） |
| `data/skill_vectors/chroma.sqlite3`（VectorStore 路） | 集合 `skill_metadata`，**0 条 embeddings**（空库） |
| `data/skill_vectors/skill_metadata.json` | **80** 条 = 8 技能 × 10 chunk（与那 8 条向量同代，2026-07-29） |

**差集明细**

| 类别 | 数量 | 明细 |
|---|---|---|
| 索引缺失·新建未补 | 15 | 全部 `pd-*-skill`（pd-brainstorming、pd-dispatching-parallel-agents、pd-executing-plans、pd-finishing-a-development-branch、pd-frontend-design、pd-receiving-code-review、pd-requesting-code-review、pd-subagent-driven-development、pd-systematic-debugging、pd-test-driven-development、pd-using-git-worktrees、pd-using-superpowers、pd-verification-before-completion、pd-writing-plans、pd-writing-skills）—— 其 `skill.md` mtime 均为 2026-09-06 |
| 索引缺失·主轨独有 | 7 | 见 §3.3 |
| 索引多余 | 0 | — |

**危害路径（★ 关键）**：`_ensure_vector_store`（`vector_adapter.py:175-233`）的优先级是 ① BGE-m3/ST → ② native_chroma → ③ VectorStore。一旦 ① 初始化失败（缺 torch、DLL 冲突、模型缓存被清），**会自动落到 ② 这份 2026-07-23 的旧库**；而 `loader._try_vector_match` 的快速退出条件只在 **两个后端都为 None** 时生效（`loader.py:666-669`）⇒ `_native_chroma` 非空就会**照常返回一份覆盖 8/23 的结果**，且 `fallback_used=False`、`retrieval_method="vector"`，日志上完全看不出「语义层只剩 8 个技能」。

---

## 4. mtime 对照：源文件是否比索引新

### 4.1 技能（文件轨 23 个 `skill.md`）

| 判定 | 数量 | 说明 |
|---|---|---|
| `.index/cache.json`(2026-09-21 20:55:14) 比源**新或相等** | 23 / 23 | 最新源为 `memory_summary` 2026-09-21 05:30:09 ⇒ **元数据索引不漂** |
| 向量库(2026-07-23 17:07:59) 比源**旧** | **16 / 23** | 15 个 `pd-*`(2026-09-06) + `memory_summary`(2026-09-21) |
| 其中**已索引**且源比索引新 | 1 | `memory_summary`（但内容实测一致，见 §3.4 ⇒ mtime 变动为**同内容重写**） |
| 其中**根本没索引** | 15 | 全部 `pd-*-skill` |

逐条（源 mtime / 是否在向量库中）：

| skill.md | mtime | 在向量库? | 源是否比向量库新 |
|---|---|---|---|
| context_aware | 2026-07-23 00:12:22 | ✅ | 否 |
| emotion_expression | 2026-07-23 00:12:22 | ✅ | 否 |
| memory_summary | 2026-09-21 05:30:09 | ✅ | **是**（内容一致） |
| pd-brainstorming-697b717a-skill | 2026-09-06 00:19:13 | ❌ | **是** |
| pd-dispatching-parallel-agents-b8065ccd-skill | 2026-09-06 00:19:13 | ❌ | **是** |
| pd-executing-plans-95cbf64a-skill | 2026-09-06 00:19:13 | ❌ | **是** |
| pd-finishing-a-development-branch-e085de5a-skill | 2026-09-06 00:19:13 | ❌ | **是** |
| pd-frontend-design-77ea5c4e-skill | 2026-09-06 00:19:13 | ❌ | **是** |
| pd-receiving-code-review-8934157e-skill | 2026-09-06 00:19:13 | ❌ | **是** |
| pd-requesting-code-review-ca5ae995-skill | 2026-09-06 00:19:13 | ❌ | **是** |
| pd-subagent-driven-development-8c375695-skill | 2026-09-06 00:19:13 | ❌ | **是** |
| pd-systematic-debugging-556faa20-skill | 2026-09-06 00:19:13 | ❌ | **是** |
| pd-test-driven-development-8562c8ad-skill | 2026-09-06 00:19:13 | ❌ | **是** |
| pd-using-git-worktrees-d516703a-skill | 2026-09-06 00:19:13 | ❌ | **是** |
| pd-using-superpowers-3aea3fc9-skill | 2026-09-06 00:19:13 | ❌ | **是** |
| pd-verification-before-completion-af010352-skill | 2026-09-06 00:19:13 | ❌ | **是** |
| pd-writing-plans-f846e3a2-skill | 2026-09-06 00:19:13 | ❌ | **是** |
| pd-writing-skills-5da20e67-skill | 2026-09-06 00:19:13 | ❌ | **是** |
| proactive_suggestion | 2026-07-23 00:12:22 | ✅ | 否 |
| safety_guard | 2026-07-23 00:12:22 | ✅ | 否 |
| scripted-selftest | 2026-07-23 00:12:22 | ✅ | 否 |
| self_reflection | 2026-07-23 00:12:22 | ✅ | 否 |
| voice_interaction | 2026-07-23 00:12:22 | ✅ | 否 |

### 4.2 工具（91 个 YAML）

- **91 / 91** 个 YAML 的 mtime（2026-09-20 10:13:38 ~ 17:13:29）**晚于** `data/tool_index.json`（2026-09-18 22:47:33）⇒ 按 mtime 判据**全部「已漂移」**；
- 但字段级内容漂移 = **0**（§3.2）⇒ 这是**假阳性**，说明巡检**不能只用 mtime**。

### 4.3 其余索引文件时间线

| 文件 | mtime | 与最新源的关系 |
|---|---|---|
| `.index/cache.json` | 2026-09-21 20:55:14 | 落后最新源(memory_summary 09-21 05:30)但**已包含**该变更 |
| `native_chroma/chroma.sqlite3` | 2026-07-27 06:30:26 | 落后 **56 天**（条目创建于 2026-07-23） |
| `data/skill_vectors/chroma.sqlite3` | 2026-07-28 13:54:11 | 落后 55 天，且 0 条 |
| `skill_metadata.json` | 2026-07-29 00:18:06 | 落后 54 天 |
| `tool_index.json` | 2026-09-18 22:47:33 | 落后最新 YAML **2 天**（内容未漂） |
| `.vector_index/.gitkeep` | 2026-07-29 12:11:19 | 死目录 |

---

## 5. Embedding 模型加载方式与 worker 挂掉时的**实际**降级行为

### 5.1 有两条互不相干的 Embedding 链，隔离性完全不同

| | 工具链（hybrid 检索） | 技能链（SkillLoader Layer-1） |
|---|---|---|
| 模型 | `paraphrase-multilingual-MiniLM-L12-v2`（384 维） — `tool_router_hybrid.py:57` | `BAAI/bge-m3`（1024 维） — `vector_adapter.py:48, 53` |
| **进程模型** | **隔离子进程**（`python -c` + JSON Lines） — `tool_router_hybrid.py:991-998`、worker 脚本 `:672-718` | **主进程内直接加载** — `vector_adapter.py:257-260` `SentenceTransformer(self.model_name, device="cpu")` |
| 落盘 | 无（内存 numpy） | 无（内存 numpy）；仅降级路写 chromadb |
| 崩溃可观测 | `worker_health()` — `:913-943` | 仅 `health()`（无 worker 概念） — `vector_adapter.py:1053-1069` |
| 健康出口接线 | `GET /api/health/retrieval` — `app_server.py:1171-1201` | **无** |

> 代码自己承认技能链无隔离：`loader.py:1394` 注释「守 project_memory：**Embedding 无隔离会崩溃**」。
> 实测模型缓存体积：`~/.cache/huggingface/hub/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2` = **479,729,050 B（≈457.5 MiB，与注释「约 470MB」吻合）**；`models--BAAI--bge-m3` = **4,564,396,159 B（≈4.25 GiB）** ⇒ 技能链默认模型体量是工具链的 **9.5 倍**，且**跑在主进程**。

### 5.2 工具链 MiniLM worker：位置 / 协议 / 退避 / 探针

| 要件 | 位置 |
|---|---|
| 启动：`subprocess.Popen([sys.executable, "-c", _WORKER_SCRIPT_EMBEDDING, model_name], stdin/stdout/stderr=PIPE, text=True)` | `tool_router_hybrid.py:990-998` |
| worker 脚本（JSON Lines：`{"type":"encode","texts":[…]}{BT}} → `{"type":"embeddings","data":<base64 np.tobytes>,"shape":[…]}{BT}}；另含 `ready` / `init_failed` / `error` / `exit`） | `tool_router_hybrid.py:672-718`（`ready` `:685`；encode 分支 `:700-712`） |
| 读超时（Windows 用 daemon 线程 + `join(timeout)`） | `tool_router_hybrid.py:219-242` |
| ready 超时 **30s** / encode 超时 **30s** / 探测超时 **60s** | `:121, 126, 116` |
| 退避重启：`_WORKER_MAX_RESTARTS=3`、退避 5s→10s→20s（上限 300s）、后台线程重试 | `:135-137, 807-846, 848-880, 882-911` |
| 不可用态唯一入口（property setter + 上升沿计数） | `:784-806` |
| 健康探针（只读出口） | `worker_health()` `:913-943`（字段：mode / init_failed / worker_alive / available / failure_total / restart_attempts / max_restart_attempts / restarting / retry_exhausted / next_restart_in_sec / last_failure） |
| HTTP 接线 | `app_server.py:1171-1201`；降级口径 `:1193-1200` |
| 可用性判据 | `available = not _init_failed and _proc alive and _embeddings is not None and len(_doc_ids)>0` — `:945-952` |
| 预热 | `HybridRetriever.__init__` 起 daemon 线程 `self._embedding.preheat` — `:1375-1387`；被 `AGENT_HYBRID_EMBEDDING in (0,false,no,off)` 关闭（`.env:200` 实测 =1） |

### 5.3 worker 挂掉时的**实际**行为（工具链）——**不是静默空召回**

| 步骤 | 实际代码行为 | 位置 |
|---|---|---|
| ① worker 崩 / encode 失败 | `self._init_failed = True`（12 处置位点统一收敛到 setter） | `:789-806` |
| ② 置位时**显式声明后果** | `logger.warning({action:'embedding.worker.unusable', degrade_to:'bm25_only', …})` | `:836-846` |
| ③ 本次请求 | `EmbeddingIndex.search()` 顶层 `if not self._ensure_worker(): return []`；`_ensure_worker` 首行 `if self._init_failed: self._maybe_restart_in_background(); return False` ⇒ **立即降级、不阻塞请求路径** | `:1182-1183`、`:978-982` |
| ④ encode 回包失败/超时 | 也在 `search()` 内 `return []`，并 `_cleanup_proc()` 回收（防响应错位） | `:1202-1204`、`:1095-1102` |
| ⑤ 融合层 | `final = bm25_score`（**不是 0 分、不是空结果**）：`if not self._embedding.available or not embed_norm: final = bm25_score` | `:1560-1563` |
| ⑥ 上层可见性 | `HybridRetriever.degraded = self._tools_loaded and not self._embedding.available`；`embedding_health()["retriever_degraded"]` | `:1577-1591` |
| ⑦ 最终对调用方 | `hybrid_select_tools(...)` 仍返回**纯 BM25 候选**（非 None）；只有整体失败才 None → 调用方回退 `get_tools_for_input` | `:1632-1660+`；调用点 `orchestrator.py:3452, 4103`、`task_dispatcher.py:50` |

**结论（工具链）**：**不是**静默空召回，而是「显式 BM25-only 降级 + 结构化日志 + HTTP 只读探针」三件套齐备的降级。
**但有一个真实盲区**：若 worker **存活**却 `_embeddings is None`（ready 后 pending 编码失败），`available` 恒为 False，而 `_ensure_worker` 在 `self._proc.poll() is None` 时**直接 return True**（`:987-988`，早于 `_encode_pending_locked`）⇒ **pending 编码不会重试**，该进程余生 `available=False` 且 `init_failed=False`（`mode` 仍报 `"hybrid"`）。本次**实测到过这个状态**（见 §5.6）。

### 5.4 技能链 BGE-m3：**静默空召回链**（★ 重大隐患）

| 触发条件 | 代码行为 | 位置 |
|---|---|---|
| 负样本启发式命中 | `return []` | `vector_adapter.py:695-697` |
| 后端全失败（`_vector_store is None`） | `logger.warning(...); return []` | `:704-707` |
| 查询超时（**默认 2.0s**） | `search.timeout_fallback … fallback:'empty_list'` → `return []` | `:716-717, 746-748` |
| 任意异常 | `search.exception_fallback … fallback:'empty_list'` → `return []` | `:749-751` |
| `_st_backend` 存在但 `doc_ids` 为空 | `if not doc_ids: return []`（**无日志**） | `:855-856` |
| query 编码失败 | `return []` | `:861-863` |
| numpy 计算异常 | `logger.warning + return []` | `:875-877` |
| chroma query 异常 | `logger.warning + return []` | `:937-939` |
| 索引构建失败 | `ensure_indexed` 各后端 `try/except` 仅 `logger.warning`，**返回 0**（不抛） | `:317-319, 401-402, 414-415, 422-423` |

**上层如何接住**：

| 调用点 | 行为 |
|---|---|
| `loader._try_vector_match` | `if not results: return None`（`:681-682`）⇒ 外层 TF-IDF 单路；**有** `vector_search.results result_count=0` 日志 |
| `loader._try_rrf_match` | 向量为空 → 继续 tfidf(+bm25) 融合；三路全空才 `return None`（`:1457-1458`）；**`retrieval_method` 仍报 "rrf"、`fallback_used=False`**，从返回值**看不出向量腿是空的**（仅 `rrf.paths_before_fuse` 日志里 `vector_candidate_count=0`） |

**结论（技能链）**：**是「静默空召回」**——召回为空与「真的没有语义匹配」在**返回对象上不可区分**，只在 INFO 日志里留痕；而且 `vector_adapter.health()` 只有一个布尔 `vector_available`（`:1053-1069`），**没有**任何等价于工具链 `worker_health()` 的降级计数/原因出口，**也没有 HTTP 探针**。

### 5.5 探针与「隔离子进程」在技能链上的错位

- 题面中的「MiniLM ~470MB / 隔离子进程 / JSON Lines / 退避重启」**全部只属于工具链**（§5.2），**技能链一条都没有**。
- 技能链的对应物是「`_try_init_sentence_transformers` 失败 → `_try_init_native_chroma` → `VectorStore`」三级 fallback（`vector_adapter.py:175-233`），**没有退避、没有重启、没有失败计数**；一旦选中某档就**永久粘住**（`self._vector_store` 一旦赋值即缓存，`:187-188`）。

### 5.6 子进程探测 + TTL 是**死代码**（实测）

- `_ensure_st_checked()` 定义于 `tool_router_hybrid.py:379-426`；`_run_embedding_probe`(`:337-376`)、`_read_probe_cache`(`:284-324`)、`_write_probe_cache`(`:327-334`) 均**只被它调用**。
- 全仓库 grep `_ensure_st_checked`：**仅 1 处定义 + 1 处注释**（`tool_router_hybrid.py:1378`：`# 注意:tool_router_hybrid._ensure_st_checked 无调用点,此处为实际 env gate`）。生产实际生效的 gate 只有 `AGENT_HYBRID_EMBEDDING`（`:1379-1380`）。
- `data/.embedding_probe` 实测内容 = `{"available": false, "probed_at": 1784737215.8083909}{BT}} ⇒ probed_at = **2026-07-23 00:20:15**，TTL=7 天（`:153`）**早已过期**；但因无调用点，这个 false 对运行时**零影响**。

**实测运行时状态**（对**已在运行**的 127.0.0.1:5678 做只读 GET `/api/health/retrieval`）：

| 时刻 | status | mode | worker_alive | available | failure_total | attempts | retry_exhausted | retriever_degraded |
|---|---|---|---|---|---|---|---|---|
| t=0s（首次 GET） | degraded | hybrid | False | False | 0 | 0 | False | True |
| t=6s | degraded | hybrid | True | **False** | 0 | 0 | False | True |
| t=12s | **ok** | hybrid | True | **True** | 0 | 0 | False | False |
| t=18/24/30s | ok | hybrid | True | True | 0 | 0 | False | False |

观察三点：
1. t=0：`init_failed=false / failure_total=0` 但 `worker_alive=false` ⇒ 单例存在却**从未预热**，或**本次 GET 触发了单例懒创建**（`get_hybrid_retriever` `:1602-1619` ⇒ `HybridRetriever.__init__` ⇒ preheat 线程）。**本审计的探针本身可能就是这个触发器，故该行 degraded 不能证明生产常态降级**【推测】。
2. t=6：worker 已活但 `available=false`（MiniLM 正在加载 / pending 未编码完），而 `mode` 仍报 `"hybrid"` ⇒ **mode 字段会在此窗口撒谎**（`mode` 只看 `_init_failed`，`:932`）；实际降级可见性靠 `retriever_degraded`。
3. t=12 起完全恢复 ⇒ **本机 MiniLM worker 冷启动 ≈ ≤12s**，在 30s ready 超时内（与 `tool_router_hybrid.py:120` 的「30s 足够」吻合）。

---

## 6. 夜间一致性巡检可执行方案

### 6.1 检查项与阈值

| ID | 检查 | 阈值 / 判定 | 本次实测 | 现状 |
|---|---|---|---|---|
| **T1** | `tool_index.json` 的 90 条 vs YAML 可见集：**集合 + 字段内容**（description/version/category/parameter_names） | 集合必须相等；**内容漂移必须 = 0** | 90 = 90，漂移 **0** | ✅ PASS |
| **T1-w** | `generated_at` vs 最新 YAML mtime | 仅 **WARN**（内容一致时允许陈旧） | 落后 2 天 | ⚠️ WARN |
| **S1** | `.index/cache.json` 与文件轨：集合相等 + 每项 md5 == md5(skill.md) | 缺失/多余/失效必须 = 0 | 0/0/0 | ✅ PASS |
| **S2** | 注册表并集(主轨 ∪ 文件轨) − 文件轨 | **必须 = 0**（否则 Layer-1 结构性漏召） | **7** | ❌ FAIL |
| **S3** | 落盘向量索引条目集 vs 注册表并集 | 未覆盖必须 = 0；多余必须 = 0；覆盖率 < 100% 记 WARN | 8 vs 30，**未覆盖 22**，覆盖率 **26.7%** | ❌ FAIL |
| **H1** | `GET /api/health/retrieval` | `status` 必须 = `ok`；`retry_exhausted` 必须 = false | 预热后 = ok | ✅（注意探针副作用） |
| **S4**（建议新增） | 技能链降级可见性：`SkillVectorAdapter.health()` 的 `indexed_count` | 建议 ≥ 文件轨技能数；**当前无出口** | 无出口 | ❌ 缺能力 |

阈值口径：**T1/S1/S2/S3 任一 FAIL 即视为「索引不可信」**，退出码 1；T1-w 与 S3 覆盖率仅 WARN，不阻断。

### 6.2 巡检脚本（只读，已在本次审计中实跑通过）

> 下列脚本**未落盘**（本次任务约定只读，仅允许写报告文件）。建议维护者保存为 `scripts/audit_index_consistency.py`，或直接用 §6.3 的 stdin 方式运行。

```python
# -*- coding: utf-8 -*-
"""索引一致性巡检（只读）。退出码 0=PASS / 1=FAIL。不写任何文件。"""
import json, os, re, sqlite3, sys, hashlib
try:
    import yaml
except ImportError:
    yaml = None

def _find_root():
    cands = [os.getcwd()]
    try:
        cands.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    except NameError:
        pass
    for c in cands:
        if os.path.isdir(os.path.join(c, "data", "tool_definitions")) and \
           os.path.isdir(os.path.join(c, "agent", "skills_mgmt")):
            return c
    raise SystemExit("找不到仓库根（需在仓库根运行，或放在 scripts/ 下）")

ROOT = _find_root()
REPO = os.path.join(ROOT, "data", "skills_repo")
fails, warns = [], []

def check_tool_index():
    dd = os.path.join(ROOT, "data", "tool_definitions")
    idx = json.load(open(os.path.join(ROOT, "data", "tool_index.json"), encoding="utf-8"))
    imap = {t["name"]: t for t in idx["tools"]}
    drift, visible = [], 0
    for f in sorted(os.listdir(dd)):
        if not f.endswith((".yaml", ".yml")):
            continue
        d = yaml.safe_load(open(os.path.join(dd, f), encoding="utf-8")) or {}
        if d.get("internal") is True or d.get("llm_callable") is False \
           or str(d.get("callable_mode") or "").strip().lower() == "manual":
            continue
        visible += 1
        t = imap.get(d.get("name"))
        props = ((d.get("schema") or {}).get("properties") or {})
        if t is None:
            drift.append((d.get("name"), "ABSENT")); continue
        for k, v in (("description", d.get("description")), ("version", d.get("version")),
                     ("category", d.get("category"))):
            if t.get(k) != v:
                drift.append((d.get("name"), k))
        if list(t.get("parameter_names") or []) != list(props.keys()):
            drift.append((d.get("name"), "parameter_names"))
    print("[T1] tool_index=%d YAML可见=%d 内容漂移=%d" % (len(imap), visible, len(drift)))
    if len(imap) != visible:
        fails.append("T1 条目数不等 (%d vs %d)" % (len(imap), visible))
    if drift:
        fails.append("T1 工具索引内容漂移 %d 项: %s" % (len(drift), drift[:10]))
    newest = max((os.path.getmtime(os.path.join(dd, f)) for f in os.listdir(dd)
                  if f.endswith((".yaml", ".yml"))), default=0)
    if newest > os.path.getmtime(os.path.join(ROOT, "data", "tool_index.json")):
        warns.append("T1 tool_index.json 落后最新 YAML mtime（内容一致可接受）")

def file_track_ids():
    return sorted(e for e in os.listdir(REPO)
                  if os.path.isdir(os.path.join(REPO, e)) and not e.startswith((".", "_"))
                  and os.path.isfile(os.path.join(REPO, e, "skill.md")))

def main_track_ids():
    p = os.path.join(ROOT, "data", "skills_mgmt.json")
    if not os.path.exists(p):
        return []
    d = json.load(open(p, encoding="utf-8"))
    return sorted((v.get("id") or k) for k, v in d.items())

def check_meta_index():
    p = os.path.join(REPO, ".index", "cache.json")
    if not os.path.exists(p):
        fails.append("S1 .index/cache.json 不存在"); return set()
    d = json.load(open(p, encoding="utf-8"))
    sk, meta = d.get("skills", {}), d.get("meta", {})
    ft = set(file_track_ids())
    miss, extra = sorted(ft - set(sk)), sorted(set(sk) - ft)
    stale = [sid for sid in sorted(ft & set(sk))
             if hashlib.md5(open(os.path.join(REPO, sid, "skill.md"), "rb").read()).hexdigest()
             != (meta.get(sid) or {}).get("hash")]
    print("[S1] cache.json=%d file-track=%d 缺失=%d 多余=%d hash失效=%d"
          % (len(sk), len(ft), len(miss), len(extra), len(stale)))
    if miss: fails.append("S1 元数据索引缺失: %s" % miss)
    if extra: fails.append("S1 元数据索引多余: %s" % extra)
    if stale: fails.append("S1 元数据索引 hash 失效: %s" % stale)
    return ft

def check_registry_vs_index(ft):
    mt_ = set(main_track_ids())
    gap = sorted(mt_ - ft)
    print("[S2] 注册表并集=%d (主轨=%d 文件轨=%d 交集=%d) 主轨独有=%d"
          % (len(mt_ | ft), len(mt_), len(ft), len(mt_ & ft), len(gap)))
    if gap:
        fails.append("S2 注册表有而文件轨索引无（Layer-1 不可召回）: %d 项 %s" % (len(gap), gap))

def check_vector_index(ft):
    db = os.path.join(ROOT, "data", "skill_vectors", "native_chroma", "chroma.sqlite3")
    if not os.path.exists(db):
        print("[S3] 无 native_chroma 落盘向量库"); return
    con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
    ids = [r[0] for r in con.execute("select embedding_id from embeddings").fetchall()]
    con.close()
    reg = set(ft) | set(main_track_ids())
    indexed = set(i[len("skill_"):] for i in ids if i.startswith("skill_"))
    print("[S3] 落盘向量索引=%d 注册表并集=%d 未覆盖=%d 多余=%d"
          % (len(indexed), len(reg), len(reg - indexed), len(indexed - reg)))
    if indexed - reg:
        fails.append("S3 向量索引含注册表外条目: %s" % sorted(indexed - reg))
    if reg - indexed:
        fails.append("S3 向量索引未覆盖 %d 项（示例 %s）"
                     % (len(reg - indexed), sorted(reg - indexed)[:8]))
        warns.append("S3 覆盖率 %.1f%%；BGE-m3 不可用时会回落到这份旧索引"
                     % (100.0 * len(indexed) / max(len(reg), 1)))

def check_health_endpoint():
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:5678/api/health/retrieval", timeout=5) as r:
            b = json.loads(r.read().decode("utf-8"))
        print("[H1] status=%s mode=%s worker_alive=%s available=%s retry_exhausted=%s"
              % (b.get("status"), b.get("mode"), b.get("worker_alive"),
                 b.get("available"), b.get("retry_exhausted")))
        if b.get("degraded"):
            fails.append("H1 检索已降级: %s" % b.get("degrade_to"))
    except Exception as e:
        warns.append("H1 健康端点不可读（服务未启动？）: %s" % type(e).__name__)

if __name__ == "__main__":
    check_tool_index()
    ft = check_meta_index()
    check_registry_vs_index(ft)
    check_vector_index(ft)
    check_health_endpoint()
    print("\n=== 结论 ===")
    for f in fails:
        print("FAIL:", f)
    for w in warns:
        print("WARN:", w)
    if not fails:
        print("PASS")
    sys.exit(1 if fails else 0)
```

**本次实跑输出（照录）**：

```text
[T1] tool_index 条目=90  YAML 可见=90  内容漂移=0
[S1] cache.json 条目=23  file-track=23  缺失=0 多余=0 hash失效=0
[S2] 注册表并集=30 (主轨=22 文件轨=23 交集=15)  主轨独有=7
[S3] 落盘向量索引条目=8  注册表并集=30  未覆盖=22  索引多余=0
[H1] /api/health/retrieval status=ok mode=hybrid worker_alive=True retry_exhausted=False
FAIL: S2 注册表有而文件轨索引无（Layer-1 结构上不可召回）: 7 项 [...]
FAIL: S3 向量索引未覆盖 22 项（示例 [...]）
WARN: T1 tool_index.json 落后于最新 YAML mtime（内容一致时可接受；建议重跑 sync_tool_index.py）
WARN: S3 覆盖率 26.7%；BGE-m3 加载失败时会回落到这份旧索引
```

### 6.3 运行命令（已实测可跑）

```powershell
# 方式 1：落盘后运行（推荐，需维护者先保存 §6.2 内容）
cd C:\Users\Administrator\agent
python scripts/audit_index_consistency.py ; $LASTEXITCODE

# 方式 2：不落盘，stdin 执行（本次审计实测通过）
cd C:\Users\Administrator\agent
$py = @'
<粘贴 §6.2 脚本正文>
'@
$py | python -
```

### 6.4 与现有 nightly job 的差异（**不要重复造轮子，但要补盲区**）

现有 nightly：`.github/workflows/skills-check.yml` 的 `nightly-full-scan`，其「一致性验证」步骤跑 `scripts/compare_skills_legacy_vs_repo.py` + `scripts/verify_migrated_skills.py`（本地复现脚本 `scripts/simulate-nightly-scan.ps1:24-28`）。

| 盲区 | 说明 |
|---|---|
| b1 | `compare_skills_legacy_vs_repo.py` 比的是 **legacy skills.json ↔ skills_repo**，且 legacy 缺失即 **SKIP 并视为 ALL_MATCH**（`compare_skills_legacy_vs_repo.py:63-70`）⇒ CI 环境下**永远 SKIP** |
| b2 | 该脚本**完全不知道** `.index/cache.json` 与 `data/skill_vectors/` 的存在 |
| b3 | 不校验工具索引的内容新鲜度（唯一守卫 `test_tool_router_hybrid_real_index_l28.py:109` 只比**名字集合**） |
| b4 | 无任何 `SkillVectorAdapter` 覆盖度检查（因为它**没有健康出口**，见 §7-A） |

本地等价实跑 `compare_skills_legacy_vs_repo` 的比对口径（`data/skills.json` 30 行 ↔ 文件轨 23 行，字段 id/name/enabled/description）结果：**only_legacy=7、only_repo=0、字段差异=15**（15 项中 14 项是 `pd-*-skill` 的 description 中英文不一致，1 项为 `pd-writing-skills` 尾部截断差异）⇒ **这份 legacy 检查本身当前就会报红**。

---

## 7. 局限与建议（按优先级）

| # | 建议 | 依据 |
|---|---|---|
| **A** | 给技能链补一个等价于 `worker_health()` 的只读出口 + HTTP 探针（`SkillVectorAdapter.health()` 目前只有 `vector_available/indexed_count/engine/model_name`） | `vector_adapter.py:1053-1069`、§5.4 |
| **B** | 把写钩子注册从「首次 use_vector 才注册」改为 `SkillsMgmtService.__init__` 即注册，并让 `ensure_indexed` 用 **content hash** 而非 id 集合判增量（消除 W2） | `loader.py:619-631`、`vector_adapter.py:349` |
| **C** | `SkillIndexCache` 增加主轨来源，或明确「Layer-1 只服务文件轨」并在 `create_manual` 时同步落 `skills_repo`（消除 7 项结构性缺口） | §3.3、`creator.py:200` |
| **D** | `sync_tool_index.py --check` 增加「与现有索引内容比对」，并作为 CI 必过关卡 | `sync_tool_index.py:356-358` |
| **E** | `ensure_indexed` 已有 dimension mismatch 重建分支（`vector_adapter.py:325-339`），但**覆盖缺口不触发重建**；建议加覆盖度自检：`len(_indexed_skill_ids) != len(current_ids)` 时告警 | `vector_adapter.py:349-353` |

### 未验证 / 明确标注

- 【推测】t=0 的 `degraded` 读数是否反映生产常态，无法判定——首次 `GET /api/health/retrieval` 本身可能触发了 `HybridRetriever` 单例懒创建与 preheat（§5.6 注 1）。
- 【推测】`native_chroma` 那份旧库为何停在 2026-07-23：时间与 `_DEFAULT_MODEL` 从 MiniLM 切 BGE-m3（`vector_adapter.py:45-48` 注释）吻合，切换后 ST 路接管，chromadb 路不再被写入。
- 未真加载 `sentence_transformers`/BGE-m3（避免拉起 4.25 GiB 模型），故「技能链当前实际后端」仅由**缓存文件与代码路径**推断，未经运行时确认。
- 未跑 pytest、未启动服务；`data/*.jsonl` 等运行期事件日志未纳入本次比对。
