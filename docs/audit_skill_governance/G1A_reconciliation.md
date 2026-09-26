# G1-A · 技能描述「唯一事实源」只读对账与迁移方案

> 任务卡：**G1-A**（G1 描述三段式改造的前置对账卡）
> 性质：**纯只读分析**。本卡**未修改任何既有文件**，只新建本报告。
> 依据：主报告 `docs/audit_skill_governance/AUDIT_AND_PLAN.md` 的执行摘要 **E11 / T7** 与 **第 4.4 节**；分项证据 `Q2_tool_skill_pool.md` 的 **第 5 节**。
> 环境：Python 3.12.0；未启动服务、未跑 pytest、未执行任何 git 写操作。
> 口径：**一切以本次实测为准**。无证据的推断一律显式标注【推测】。
> 复现：第 11 节给出完整只读脚本原文。

---

## 0. 结论速览

| # | 结论 | 实测证据 |
|---|---|---|
| C1 | 技能描述真实存储 **10 处**（审计口径 8 处，本次新增实测 **3 处**：`plugins/skills.py:116-121` 的 `_CURATED_DESCRIPTIONS`、`agent/extensions/base.py:98-142` 的 `BUILTIN_EXTENSIONS["skill"]`、以及 `data/skills.json` 的**字节级镜像** `agent/data/skills.json`） | 第 2 节；第 11 节脚本 A |
| C2 | **15 组冲突成立且是 100% 分歧**：23 个 skill.md 中 15 个同时存在于主轨，15/15 description 不同（复核 Q2 第 5.2 节结论） | 脚本 A 输出：`双侧: 15  描述不同: 15` |
| C3 | **分歧主因是「英到中翻译」，不是「两份不同文案」**：15 组里 14 组的文本相似度只有 **0.077 到 0.222**（互为独立文本）；第 15 组 `pd-writing-skills` 相似度 **0.913**（仅标点与句序差异）。最严重的是 `pd-brainstorming`（0.077） | 第 4 节逐条表 |
| C4 | **对技能而言，「模型看到的描述」与「检索索引用的描述」是同一份 = S1 skill.md**（`loader.py:393/699/795` 与 `context_injector.py:318` 都读 `fs.load_metadata_index()`）。审计报告 T7 隐含的「两个消费者不一定同源」**在技能侧不成立，在工具侧成立**（工具：检索读 YAML，模型读代码字面量） | 第 3 节 |
| C5 | 真正的分叉在**第三、第四个消费者**：**UI 面**读主轨 S2、**治理/评估面**读 S8 与 CapabilityRegistry。=> E11 的「改 skill.md 只改检索、改 skills_mgmt.json 只改 UI」需要修正为**三分**：skill.md 同时管检索与模型可见 | 第 3 节 表 3-2 |
| C6 | `self_reflection` 与 `memory_summary` **不是三套文案而是四套**，横跨 **8 处**存储；另有 4 个技能（`context_aware` / `proactive_suggestion` / `safety_guard` / `voice_interaction`）各 **3 套** | 第 2 节 表 2-2 |
| C7 | CapabilityRegistry 补 `description` 的落点**已经铺好了 90%**：`agent/capregistry/spec.py:344` 已写 `description=str(e.get("description") or "")`，`to_dict()`（`spec.py:229`）已输出该键。实测 `build_registry()` 得 114 条：**91 个工具全部非空 / 23 个技能全部为空**。只需在 `callability.py` 两处补键并重跑 sync | 第 9.4 节；第 11 节脚本 E |
| C8 | **`.index/cache.json` 是安全的**（`index_cache.py:237-255` 的 mtime+md5 双校验；实测 23/23 hash 命中、23/23 description 与 front matter 逐字相同）=> 改 skill.md **不需要**手动重建检索缓存。**向量索引（审计基线）不安全**：基线 `5c9ace10` 的 `ensure_indexed` 只按 id 集合差补新（`vector_adapter.py:349`）。**该缺陷已由 C1 卡在当前工作区修复**，详见第 0.5 节与第 9.5 节 | 第 9.5 节、第 14 节 |
| C9 | **legacy `data/skills.json` 不是独立源，是合并视图的快照**：23 并 22 = **30**，与 legacy 的 30 条**集合完全相等**；且 22/22 条主轨描述与 legacy 逐字相同 => 结论是**归档 + 转只读断言**，不是「一次性迁移」 | 第 8.2 节 |
| C10 | **overlay 的 4 条里有 3 条结构性永不生效**（`plugins/skills.py:151-152` 要求原描述为空，而这 3 条在合并视图里恒有描述）；第 4 条 `email-helper` 是**死键**（skills_repo / skills_mgmt / skills.json 三处均无此实体） | 第 8.2 节、第 5 节 |
| C11 | **persona 内联 7 段（`digital_life_persona.py:42-62`）应判定为「人格提示词」，从「技能描述」治理范围排除**；但**不能**从技能 id 治理排除（它按 `SkillRegistry.list_enabled_ids()` 门控） | 第 8.3 节 |
| C12 | **唯一事实源裁定：`data/skills_repo/<id>/skill.md` 的 front matter**。它是**唯一被 git 跟踪**（实测 `git ls-files --error-unmatch` 成功）且**同时已是检索 + 模型可见 + 向量编码三个运行消费者实际来源**的那一份 | 第 7 节 |
| C13 | **勘误（对 Q2）**：Q2 第 5.1 / 4.2 节用 `^description:` 单行正则抽取 front matter，**漏掉折行标量**，导致 15 条 pd-* 的描述长度被低估。技能侧真实统计：**mean 123.2 / median 103**（Q2 报 65.4 / 70）；技能侧触发句式 **17/23**（Q2 报 16/23） | 第 12 节 |
| C14 | **最大隐性风险**：若把 15 条 description 换成中文译文，技能侧「含典型触发句式」将从 **17/23（73.9%）掉到 13/23（56.5%）**，因为 `Use when …` 正是这 14 条的触发句式载体。**结论：description 必须保留英文原文，中文另存 `description_zh`** | 第 10 节 风险 R2 |

**一句话**：技能描述不是「一份被复制了 8 次」，而是**三条互不相交的读取链**（运行链读 S1、UI 链读 S2、治理链读 S8 与清单），叠加**四组旁路写死的文案**（S4 / S5 / S6 / S7）。收敛的关键不是「删掉 7 份」，而是**先把 S2、S8 与 UI 的读路径改为向 S1 取描述**，再逐条处理 4 组旁路。

---

## 0.5 测量时点与并发变更声明（**必须先读**）

**本报告的所有行号、文件内容与「当前状态」判断，测量时点为**：仓库处于审计基线 `master @ 5c9ace10` 的**文件内容**（`data/` 下的 gitignored 数据文件为 2026-09-23 至 2026-09-24 的快照）。

**但本卡执行期间，工作区正被其他任务卡并发修改**（`git status --porcelain` 实测）。其中 **4 个文件与本卡的分析对象重叠**：

| 文件 | 并发的卡 | 变更规模 | 对本报告的影响 |
|---|---|---|---|
| `agent/skills_mgmt/vector_adapter.py` | **C1**（代码注释自述「C1 修(3)」「C1 修(4)」） | **+612 行**（`git diff --stat`） | **重大**：基线里的「同 id 内容变更永不重编码 / 重复 id 静默跳过」缺陷**已被 C1 修复**。第 9.5 节与风险 R3 已相应改写为「基线缺陷 + C1 已修 + 剩余待办」 |
| `agent/skills_mgmt/registry.py` | **D2** | +66 行 | 轻微：`set_enabled` / `toggle` 增加审计留痕；**`as_legacy_rows` 逻辑未变**，行号由 `153-193` 漂移到 `205-245` |
| `agent/skills_mgmt/index_cache.py` | **C3** | +243 行 | 轻微：本报告引用的 `_entry_valid`（基线 `237-255`）语义未变 |
| `agent/digital_life_persona.py` | 并发卡 | -48 行 | 轻微：本报告引用的 `_SKILL_PROMPTS`（`42-62`）与 `_build_skill_instructions`（基线 `415-439`）需重新核对行号 |

其余并发产物：`scripts/watchdog_yunshu.py`（A1）、`scripts/verify_index_drift.py`（C3）、`tests/unit/test_skill_registry_audit.py`（D2）、`tests/unit/test_tool_count_consistency.py`（B1）、`tests/unit/test_startup_no_gap.py`（A1）。完整 `git status` 见第 14 节。

**结论与操作要求**：

1. 本报告的**数据类结论**（15/15 冲突、10 处存储、9 类文案分布、capability manifest 缺口、审计链 `descriptor.*` 统计）**不受影响**——它们读的是 `data/` 下的数据文件与 `agent/lines/callability.py`、`agent/capregistry/spec.py`、`plugins/skills.py`、`scripts/sync_capability_manifest.py`，这 4 个文件在 `git status` 里**均未被修改**。
2. 涉及 `vector_adapter.py` 与 `registry.py` 的**行号**以基线为准；G1-B 开工前应**重新对账一次行号**。
3. **G1-B 不得重复实现 C1 已完成的工作**（见第 9.5 节）。

---

## 1. 开工前的三处必读与本卡定位

- **E11**（主报告第 0 节）给出两个关键事实：技能侧 15/23 已是两份互相冲突的描述；合并规则 `registry.py:153-193`（**审计基线行号**；当前工作区因 D2 卡插入代码已漂移到 `205-245`，逻辑未变）使「主轨先占位、文件轨只补缺失」。本卡把这句话**做细做准**，并首次把「冲突面」从 2 处扩展到 **10 处**。
- **T7**（主报告第 2.1 节）指出 `CapabilityRegistry` 的 23 条 skill 条目「根本没有 description 字段」。本条**前半正确、后半需修正**：`data/capability_manifest.json` 里确实 0 条含 `description` 键，但 **HTTP 信封（`GET /capabilities/tools`）里该键是存在的**，只是值为空串。见第 3 节与第 9.4 节。
- **第 4.4 节**明确「P1 描述三段式改造不宜按文件拆给多个子代理……应在解决唯一事实源之后由单线串行推进」。本卡即为那个前置。

**本卡不做的三件事**（留给 G1-B 及后续卡）：不改任何 skill.md / skills_mgmt.json / 代码；不设计「三段式」的写作规范（那是 G1 正卡）；不处理工具侧 91 条（本卡只处理技能侧 23 条与它们的旁路）。

---

## 2. 存储清册：实测 **10 处**（比审计口径多 3 处）

### 2.1 十处存储（S1 到 S10）

| ID | 位置 | 条数 | 版本控制 | 运行时读它的地方 | 路由相关 |
|---|---|---|---|---|---|
| **S1** | `data/skills_repo/<id>/skill.md` 的 front matter `description:` | **23** | 跟踪（`git ls-files` 命中） | 检索三路（TF-IDF / 向量 / BM25）+ 模型可见元数据注入 + 向量编码文本 + 向量 metadata | **是** |
| **S2** | `data/skills_mgmt.json[<id>].description` | **22** | 忽略（`.gitignore:224`） | 主轨 UI（`app_server.py:723` 的 SkillsManager）+ `agent/skills_mgmt/searcher.py` 的 SkillSearcher | 否（不进检索链） |
| **S3** | `data/skills.json`（legacy 快照） | **30** | 忽略（`.gitignore:148`） | `scripts/compare_skills_legacy_vs_repo.py`（夜间 CI）+ `callability._skill_sources(include_runtime_catalog=True)`。**注**：`digital_life_persona.py:366-376` 虽读该文件，但只取 `name`/`enabled`，**不用 description** | 否 |
| **S3b** | `agent/data/skills.json`（S3 的**字节级镜像**） | **30** | 忽略（`.gitignore:506`） | `digital_life_persona.py:369`（在 S3 之前读，但被后读的 S3 覆盖） | 否 |
| **S4** | `data/skills_descriptions_overlay.json` | **4** | 跟踪 | `plugins/skills.py:145-155` `_apply_desc_overlay`，**仅当原描述为空时**才覆盖 | 否 |
| **S5** | `plugins/skills.py:116-121` 的 `_CURATED_DESCRIPTIONS`（代码字面量） | **4** | 代码 | `plugins/skills.py:459-480` `POST /api/skills/describe/auto`，把文案**写入** S4 | 否 |
| **S6** | `agent/digital_life_persona.py:42-62` 的 `_SKILL_PROMPTS`（代码字面量） | **7** | 代码 | `_build_skill_instructions`（`:415-439`）拼进系统提示词，按 `SkillRegistry.list_enabled_ids()` 门控 | 否（但**进模型上下文**） |
| **S7** | `agent/extensions/base.py:98-142` 的 `BUILTIN_EXTENSIONS["skill"]`（代码字面量） | **7** | 代码 | `plugins/skills.py:190-191` 的「可安装内置技能」列表；`skills_installer.py:73`；`market.py:51` | 否 |
| **S8** | `data/descriptors.json` 的 `capability.description` | **29** | 忽略（`.gitignore:226`） | 治理 / 评估 / 诊断面（`agent/digestion/*`、`eval/metrics.py`、`trace_v2.py`、`repair/locate.py`、`memory/forgetting.py`、`ui_panels/data.py`）。**无任何 router 读它** | 否 |
| **S9** | `data/skills_repo/.index/cache.json` 的 `description` | **23** | 忽略（生成物，`.gitignore:250`） | `SkillIndexCache`：检索元数据的缓存（`file_store.py:409-413`） | **是（缓存）** |
| **S10** | `data/skill_vectors/native_chroma/chroma.sqlite3` 的 `embedding_metadata.description` | **8** | 忽略（生成物，`.gitignore:211`） | 向量路的 metadata（结果对象携带） | **是（向量）** |

**相对审计口径新增的 3 处**：`S5` `_CURATED_DESCRIPTIONS`（审计未列）、`S7` `BUILTIN_EXTENSIONS["skill"]`（审计未列）、`S3b` `agent/data/skills.json` 镜像（审计只列了 `data/skills.json`）。

**S3b 是真实存在的第二份 30 条快照**：实测两份文件大小均为 10,973 B、md5 同为 `293ecbe151c91c167773abe745fc690c`、字节完全相同。写入方是 `agent/skills_mgmt/store.py:542-571` 的 `sync_to_legacy_skills_json()`，它**一次调用同时重建两份**。

### 2.2 每个技能横跨几处、有几套不同文案

口径：把 S1 / S2 / S3 / S4 / S5 / S6 / S7 / S8 / S9 九个位置的描述文本去重计数（S10 只覆盖 8 条且内容等于 S1，已并入 S9 口径说明）。

| skill_id | 不同文案数 | 命中存储数 | 命中哪些存储 |
|---|---|---|---|
| memory_summary | 4 | 8 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S3,S4,S5,S6,S7,S8,S9 |
| self_reflection | 4 | 8 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S3,S4,S5,S6,S7,S8,S9 |
| context_aware | 3 | 6 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S3,S6,S7,S8,S9 |
| proactive_suggestion | 3 | 6 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S3,S6,S7,S8,S9 |
| safety_guard | 3 | 6 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S3,S6,S7,S8,S9 |
| voice_interaction | 3 | 6 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S3,S6,S7,S8,S9 |
| pd-writing-skills-5da20e67-skill | 3 | 5 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S2,S3,S8,S9 |
| emotion_expression | 2 | 6 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S3,S6,S7,S8,S9 |
| scripted-selftest | 2 | 6 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S3,S4,S5,S8,S9 |
| pd-brainstorming-697b717a-skill | 2 | 5 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S2,S3,S8,S9 |
| pd-dispatching-parallel-agents-b8065ccd-skill | 2 | 5 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S2,S3,S8,S9 |
| pd-executing-plans-95cbf64a-skill | 2 | 5 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S2,S3,S8,S9 |
| pd-finishing-a-development-branch-e085de5a-skill | 2 | 5 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S2,S3,S8,S9 |
| pd-frontend-design-77ea5c4e-skill | 2 | 5 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S2,S3,S8,S9 |
| pd-receiving-code-review-8934157e-skill | 2 | 5 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S2,S3,S8,S9 |
| pd-requesting-code-review-ca5ae995-skill | 2 | 5 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S2,S3,S8,S9 |
| pd-subagent-driven-development-8c375695-skill | 2 | 5 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S2,S3,S8,S9 |
| pd-systematic-debugging-556faa20-skill | 2 | 5 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S2,S3,S8,S9 |
| pd-test-driven-development-8562c8ad-skill | 2 | 5 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S2,S3,S8,S9 |
| pd-using-git-worktrees-d516703a-skill | 2 | 5 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S2,S3,S8,S9 |
| pd-using-superpowers-3aea3fc9-skill | 2 | 5 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S2,S3,S8,S9 |
| pd-verification-before-completion-af010352-skill | 2 | 5 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S2,S3,S8,S9 |
| pd-writing-plans-f846e3a2-skill | 2 | 5 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S1,S2,S3,S8,S9 |
| code-observability | 1 | 3 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S2,S3,S8 |
| engineering-test-delivery | 1 | 3 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S2,S3,S8 |
| frontend-state-sync | 1 | 3 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S2,S3,S8 |
| global-core-principles | 1 | 3 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S2,S3,S8 |
| self-explanatory-ui | 1 | 3 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S2,S3,S8 |
| testing-anti-patterns | 1 | 3 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S2,S3,S8 |
| skill | 1 | 2 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S2,S3 |
| email-helper | 1 | 1 | S1/S2/S3/S4/S5/S6/S7/S8/S9 中命中：S5 |

**读法**：`self_reflection` / `memory_summary` = **4 套文案 / 8 处存储**（最脏）；4 个 persona 技能 = 3 套 / 6 处；15 条 pd-* = 2 套 / 5 处；7 条主轨独有技能 = 1 套 / 3 处（它们只被主轨、legacy、descriptors 三处共享同一文案，反而最干净）。

---

## 3. 五个消费者：谁真正读了哪一份（本节是本卡的核心）

审计报告 E11 用「UI 显示的是 / 检索匹配的是」二分。实测发现至少要分成**五个消费者**，这直接决定了「哪一份该成为唯一事实源」。

| 消费者 | 定义 | 技能侧实际读的存储 | 工具侧实际读的存储 |
|---|---|---|---|
| **K1 检索索引** | BM25 / Embedding / TF-IDF 的文档文本，决定「召回到哪些技能」 | **S1**（经 S9 缓存） | **YAML** `data/tool_definitions/*.yaml:3`（经 `data/tool_index.json`） |
| **K2 模型可见** | 真正进入 LLM 上下文的描述文本 | **S1**（`context_injector.py:318` 的「- 描述: …」，其 `SkillMatch.description` 来自 `loader.py:393/699/795` 的 `fs.load_metadata_index()`）；**另加 S6**（persona 段，7 条，走系统提示词） | **代码字面量**（`@_tools.register("name", "描述", …)`，经 `agent/tools/__init__.py:697` 进 `tools[]`） |
| **K3 UI 列表** | 技能管理页 / 资产页显示给**人**的文案 | **S2 优先**（`registry.py:153-193` 主轨先占位）；主轨没有时回落 **S1**；另受 S4 覆盖（条件性）、S7 影响「可安装」卡片 | n/a（工具 UI 另有来源） |
| **K4 治理/评估** | 评估、诊断、遗忘、修复、UI 面板的旁路数据 | **S8**（`descriptors.json`，主轨优先的合并快照） | **S8** 中 91 条与 YAML 逐字相同 |
| **K5 兼容快照** | 旧读方的只读回退 | **S3 / S3b**（与 S2 同文案；主轨缺失时与 S1 同文案） | n/a |

### 3.1 三个必须讲清楚的点

**(1) 技能侧的 K1 与 K2 是同一份（S1）；工具侧的 K1 与 K2 是两份。**
这三条读路径都落到 `SkillFileStore.load_metadata_index()`：

- `agent/skills_mgmt/loader.py:393`（TF-IDF 路）、`:699`（向量路补字段）、`:795`（BM25 路补字段）—— 三处都是 `description=meta.get("description", "")`，而 `meta` 取自 `index = self.fs.load_metadata_index()`；
- `agent/skills_mgmt/context_injector.py:317-318` —— `### {m.name}` + `- 描述: {m.description}`，其中 `m` 就是上面那种 `SkillMatch`；
- `agent/skills_mgmt/vector_adapter.py:151-157` 的 `_build_vector_text` 把 `meta.get("description","")` 拼进向量化文本。

而 `load_metadata_index`（`file_store.py:405-445`）要么走 `SkillIndexCache`、要么直接 `SkillMDParser.parse(skill.md)`，**两条路都是 skill.md 的 front matter**。

对比工具侧：`get_tool_defs()` 永远读**代码字面量**，`HybridRetriever` 永远读 **YAML**。两者靠 `tests/unit/test_tool_definitions_yaml.py:956-960` 一条测试绑住。**所以「模型看到的」与「检索用的」不同源，是工具侧的特征，不是技能侧的特征。**

**(2) 因此 E11 的二分表述要修正为三分。**

| 改动位置 | 检索（K1） | 模型可见（K2） | UI（K3） | 治理（K4） |
|---|---|---|---|---|
| 改 `skill.md` | **变** | **变** | 不变（主轨仍在） | 不变 |
| 改 `skills_mgmt.json` | 不变 | 不变 | **变** | 不变（除非重跑回填） |
| 改 `descriptors.json` 的回填源 | 不变 | 不变 | 不变 | **变** |

E11 说「改 skill.md 只改检索（UI 不变）」——**UI 确实不变，但它同样改了模型可见的那份**。这一点很重要：因为这意味着**当前 15 条 pd-* 技能的模型可见描述是英文**，而管理页上给操作员看的是中文译文。**运营者按管理页的中文判断「模型看到的技能说明」，判断依据与事实不一致。**

**(3) 真正的「静默失效」有三处，不止 overlay 一处。**

- `S4 overlay` 的 3 条（`self_reflection` / `memory_summary` / `scripted-selftest`）**结构性永不生效**：`plugins/skills.py:151-152` 的条件是 `o.get("description") and not str(s.get("description","") or "").strip()`，而这三个 id 在 `as_legacy_rows()` 的输出里**恒有非空描述**（实测：self_reflection 61 字、memory_summary 65 字、scripted-selftest 89 字）。
- `S3 legacy` 的 description 字段**运行时无读者**：`digital_life_persona.py:375-376` 只取 `name` / `enabled`。它唯一真实的读者是 `scripts/compare_skills_legacy_vs_repo.py` 与 `callability` 的运行时口径。
- `S7 BUILTIN_EXTENSIONS` 的 7 条与 S1 的 7 条**没有任何一处比对**（全仓 grep 无守卫）。

---

## 4. 15 组冲突技能：逐条对账

### 4.1 严重度排序（按 S1 与 S2 的文本相似度升序 = 分歧降序）

| 排名 | skill_id | S1 长度 | S2 长度 | 相似度 | 分歧性质 |
|---|---|---|---|---|---|
| 1 | pd-brainstorming-697b717a-skill | 210 | 101 | 0.077 | 中英互译（语义等价、词面无关） |
| 2 | pd-verification-before-completion-af010352-skill | 237 | 94 | 0.097 | 中英互译（语义等价、词面无关） |
| 3 | pd-frontend-design-77ea5c4e-skill | 246 | 100 | 0.110 | 中英互译（语义等价、词面无关） |
| 4 | pd-receiving-code-review-8934157e-skill | 246 | 109 | 0.118 | 中英互译（语义等价、词面无关） |
| 5 | pd-finishing-a-development-branch-e085de5a-skill | 212 | 100 | 0.122 | 中英互译（语义等价、词面无关） |
| 6 | pd-dispatching-parallel-agents-b8065ccd-skill | 118 | 53 | 0.140 | 中英互译（语义等价、词面无关） |
| 7 | pd-executing-plans-95cbf64a-skill | 116 | 54 | 0.141 | 中英互译（语义等价、词面无关） |
| 8 | pd-requesting-code-review-ca5ae995-skill | 119 | 48 | 0.144 | 中英互译（语义等价、词面无关） |
| 9 | pd-writing-plans-f846e3a2-skill | 96 | 46 | 0.169 | 中英互译（语义等价、词面无关） |
| 10 | pd-using-git-worktrees-d516703a-skill | 208 | 87 | 0.169 | 中英互译（语义等价、词面无关） |
| 11 | pd-using-superpowers-3aea3fc9-skill | 171 | 82 | 0.174 | 中英互译（语义等价、词面无关） |
| 12 | pd-subagent-driven-development-8c375695-skill | 97 | 36 | 0.180 | 中英互译（语义等价、词面无关） |
| 13 | pd-systematic-debugging-556faa20-skill | 103 | 55 | 0.190 | 中英互译（语义等价、词面无关） |
| 14 | pd-test-driven-development-8562c8ad-skill | 91 | 44 | 0.222 | 中英互译（语义等价、词面无关） |
| 15 | pd-writing-skills-5da20e67-skill | 139 | 137 | 0.913 | 同语言近义改写 |

**结论**：15 组的分歧**不是「两份不同的中文描述」**，而是**「英文原文 vs 中文翻译」**。15 组里 14 组相似度落在 **0.077 到 0.222** 区间——这个区间说明两份文本**几乎不共享字符 n-gram**，符合「一个是英文一个是中文」而不是「同一句话改了措辞」。唯一例外是 `pd-writing-skills`（0.913），差异仅在于 S1 多了一个重复句号 `。。`、以及 `由 1 份素材蒸馏生成` 的**位置**不同。

**因此「哪一份更对」这个问题在多数情况下是伪问题**——两份是同一句话的两种语言。真正的治理问题是**「哪一份应当被谁读」**。

### 4.2 逐条对账表

表头列义：**K1 检索命中** = 该份文本是否参与 BM25/向量/TF-IDF 的文档；**K2 模型可见** = 该份文本是否进入 LLM 上下文；**K3 UI** = 是否显示在技能管理/资产页给人看；**K4 治理** = 是否被 descriptors / 评估 / 面板读。

#### 4.2.1 `pd-brainstorming-697b717a-skill`

相似度 **0.077** ｜ S1 210 字 ｜ S2 101 字 ｜ 同时存在于 legacy(S3)、descriptors(S8)、index cache(S9)

| 存储 | 长度 | K1 检索命中 | K2 模型可见 | K3 UI | K4 治理 | 描述原文 |
|---|---|---|---|---|---|---|
| **S1** skill.md front matter | **210** | **是**（唯一来源） | **是**（唯一来源） | 否（被 S2 挡住） | 否 | You MUST use this before any creative work - creating features, building components, adding functionality, or modifying behavior. Explores user intent, requirements and design before implementation.。由 1 份素材蒸馏生成 |
| **S2** skills_mgmt.json | 101 | 否 | 否（技能不进 tools[]，也不进 K2） | **是**（主轨先占位） | 否 | 在进行任何创造性工作（比如开发新功能、构建组件、添加功能或修改现有行为）之前，你【必须】先使用此流程。务必在动手写代码实现之前，先充分探索并明确用户的真实意图、具体需求和设计方案。由 1 份素材蒸馏生成 |
| **S3** data/skills.json | 101 | 否 | 否 | 否 | 否 | 在进行任何创造性工作（比如开发新功能、构建组件、添加功能或修改现有行为）之前，你【必须】先使用此流程。务必在动手写代码实现之前，先充分探索并明确用户的真实意图、具体需求和设计方案。由 1 份素材蒸馏生成 |
| **S8** descriptors.json | 101 | 否 | 否 | 否（面板经 UI 另取） | **是** | 在进行任何创造性工作（比如开发新功能、构建组件、添加功能或修改现有行为）之前，你【必须】先使用此流程。务必在动手写代码实现之前，先充分探索并明确用户的真实意图、具体需求和设计方案。由 1 份素材蒸馏生成 |
| **S9** .index/cache.json | 210 | 是（缓存） | 是（缓存） | 否 | 否 | You MUST use this before any creative work - creating features, building components, adding functionality, or modifying behavior. Explores user intent, requirements and design before implementation.。由 1 份素材蒸馏生成 |

#### 4.2.2 `pd-verification-before-completion-af010352-skill`

相似度 **0.097** ｜ S1 237 字 ｜ S2 94 字 ｜ 同时存在于 legacy(S3)、descriptors(S8)、index cache(S9)

| 存储 | 长度 | K1 检索命中 | K2 模型可见 | K3 UI | K4 治理 | 描述原文 |
|---|---|---|---|---|---|---|
| **S1** skill.md front matter | **237** | **是**（唯一来源） | **是**（唯一来源） | 否（被 S2 挡住） | 否 | Use when about to claim work is complete, fixed, or passing, before committing or creating PRs - requires running verification commands and confirming output before making any success claims; evidence before assertions always。由 1 份素材蒸馏生成 |
| **S2** skills_mgmt.json | 94 | 否 | 否（技能不进 tools[]，也不进 K2） | **是**（主轨先占位） | 否 | 在准备宣布工作已完成、问题已修复或测试已通过之前（比如在提交代码或创建 PR 之前），必须先运行验证命令并确认输出结果，绝不能凭空宣称成功。记住：先拿证据，再做断言。由 1 份素材蒸馏生成 |
| **S3** data/skills.json | 94 | 否 | 否 | 否 | 否 | 在准备宣布工作已完成、问题已修复或测试已通过之前（比如在提交代码或创建 PR 之前），必须先运行验证命令并确认输出结果，绝不能凭空宣称成功。记住：先拿证据，再做断言。由 1 份素材蒸馏生成 |
| **S8** descriptors.json | 94 | 否 | 否 | 否（面板经 UI 另取） | **是** | 在准备宣布工作已完成、问题已修复或测试已通过之前（比如在提交代码或创建 PR 之前），必须先运行验证命令并确认输出结果，绝不能凭空宣称成功。记住：先拿证据，再做断言。由 1 份素材蒸馏生成 |
| **S9** .index/cache.json | 237 | 是（缓存） | 是（缓存） | 否 | 否 | Use when about to claim work is complete, fixed, or passing, before committing or creating PRs - requires running verification commands and confirming output before making any success claims; evidence before assertions always。由 1 份素材蒸馏生成 |

#### 4.2.3 `pd-frontend-design-77ea5c4e-skill`

相似度 **0.110** ｜ S1 246 字 ｜ S2 100 字 ｜ 同时存在于 legacy(S3)、descriptors(S8)、index cache(S9)

| 存储 | 长度 | K1 检索命中 | K2 模型可见 | K3 UI | K4 治理 | 描述原文 |
|---|---|---|---|---|---|---|
| **S1** skill.md front matter | **246** | **是**（唯一来源） | **是**（唯一来源） | 否（被 S2 挡住） | 否 | Create distinctive, production-grade frontend interfaces with high design quality. Use this skill when the user asks to build web components, pages, or applications. Generates creative, polished code that avoids generic AI aesthetics.。由 1 份素材蒸馏生成 |
| **S2** skills_mgmt.json | 100 | 否 | 否（技能不进 tools[]，也不进 K2） | **是**（主轨先占位） | 否 | 打造独具特色、达到生产级标准且设计感拉满的前端界面。当用户需要构建 Web 组件、页面或应用时，请调用这项技能。生成的代码要有创意、够精致，坚决避开那种千篇一律的‘AI 味儿’。由 1 份素材蒸馏生成 |
| **S3** data/skills.json | 100 | 否 | 否 | 否 | 否 | 打造独具特色、达到生产级标准且设计感拉满的前端界面。当用户需要构建 Web 组件、页面或应用时，请调用这项技能。生成的代码要有创意、够精致，坚决避开那种千篇一律的‘AI 味儿’。由 1 份素材蒸馏生成 |
| **S8** descriptors.json | 100 | 否 | 否 | 否（面板经 UI 另取） | **是** | 打造独具特色、达到生产级标准且设计感拉满的前端界面。当用户需要构建 Web 组件、页面或应用时，请调用这项技能。生成的代码要有创意、够精致，坚决避开那种千篇一律的‘AI 味儿’。由 1 份素材蒸馏生成 |
| **S9** .index/cache.json | 246 | 是（缓存） | 是（缓存） | 否 | 否 | Create distinctive, production-grade frontend interfaces with high design quality. Use this skill when the user asks to build web components, pages, or applications. Generates creative, polished code that avoids generic AI aesthetics.。由 1 份素材蒸馏生成 |

#### 4.2.4 `pd-receiving-code-review-8934157e-skill`

相似度 **0.118** ｜ S1 246 字 ｜ S2 109 字 ｜ 同时存在于 legacy(S3)、descriptors(S8)、index cache(S9)

| 存储 | 长度 | K1 检索命中 | K2 模型可见 | K3 UI | K4 治理 | 描述原文 |
|---|---|---|---|---|---|---|
| **S1** skill.md front matter | **246** | **是**（唯一来源） | **是**（唯一来源） | 否（被 S2 挡住） | 否 | Use when receiving code review feedback, before implementing suggestions, especially if feedback seems unclear or technically questionable - requires technical rigor and verification, not performative agreement or blind implementation。由 1 份素材蒸馏生成 |
| **S2** skills_mgmt.json | 109 | 否 | 否（技能不进 tools[]，也不进 K2） | **是**（主轨先占位） | 否 | 在收到代码审查（Code Review）的反馈时，在着手修改代码之前使用此方法。尤其是当反馈看起来不太清晰，或者在技术上存疑时——此时需要的是严谨的技术推敲和验证，而不是做做样子的附和或盲目照做。由 1 份素材蒸馏生成 |
| **S3** data/skills.json | 109 | 否 | 否 | 否 | 否 | 在收到代码审查（Code Review）的反馈时，在着手修改代码之前使用此方法。尤其是当反馈看起来不太清晰，或者在技术上存疑时——此时需要的是严谨的技术推敲和验证，而不是做做样子的附和或盲目照做。由 1 份素材蒸馏生成 |
| **S8** descriptors.json | 109 | 否 | 否 | 否（面板经 UI 另取） | **是** | 在收到代码审查（Code Review）的反馈时，在着手修改代码之前使用此方法。尤其是当反馈看起来不太清晰，或者在技术上存疑时——此时需要的是严谨的技术推敲和验证，而不是做做样子的附和或盲目照做。由 1 份素材蒸馏生成 |
| **S9** .index/cache.json | 246 | 是（缓存） | 是（缓存） | 否 | 否 | Use when receiving code review feedback, before implementing suggestions, especially if feedback seems unclear or technically questionable - requires technical rigor and verification, not performative agreement or blind implementation。由 1 份素材蒸馏生成 |

#### 4.2.5 `pd-finishing-a-development-branch-e085de5a-skill`

相似度 **0.122** ｜ S1 212 字 ｜ S2 100 字 ｜ 同时存在于 legacy(S3)、descriptors(S8)、index cache(S9)

| 存储 | 长度 | K1 检索命中 | K2 模型可见 | K3 UI | K4 治理 | 描述原文 |
|---|---|---|---|---|---|---|
| **S1** skill.md front matter | **212** | **是**（唯一来源） | **是**（唯一来源） | 否（被 S2 挡住） | 否 | Use when implementation is complete, all tests pass, and you need to decide how to integrate the work - guides completion of development work by presenting structured options for merge, PR, or cleanup。由 1 份素材蒸馏生成 |
| **S2** skills_mgmt.json | 100 | 否 | 否（技能不进 tools[]，也不进 K2） | **是**（主轨先占位） | 否 | 当代码实现完毕、所有测试都通过，且你需要决定如何集成这些工作时，就可以使用它。它会为你提供合并（merge）、提交拉取请求（PR）或清理代码等结构化的选项，帮你顺利完成开发收尾。由 1 份素材蒸馏生成 |
| **S3** data/skills.json | 100 | 否 | 否 | 否 | 否 | 当代码实现完毕、所有测试都通过，且你需要决定如何集成这些工作时，就可以使用它。它会为你提供合并（merge）、提交拉取请求（PR）或清理代码等结构化的选项，帮你顺利完成开发收尾。由 1 份素材蒸馏生成 |
| **S8** descriptors.json | 100 | 否 | 否 | 否（面板经 UI 另取） | **是** | 当代码实现完毕、所有测试都通过，且你需要决定如何集成这些工作时，就可以使用它。它会为你提供合并（merge）、提交拉取请求（PR）或清理代码等结构化的选项，帮你顺利完成开发收尾。由 1 份素材蒸馏生成 |
| **S9** .index/cache.json | 212 | 是（缓存） | 是（缓存） | 否 | 否 | Use when implementation is complete, all tests pass, and you need to decide how to integrate the work - guides completion of development work by presenting structured options for merge, PR, or cleanup。由 1 份素材蒸馏生成 |

#### 4.2.6 `pd-dispatching-parallel-agents-b8065ccd-skill`

相似度 **0.140** ｜ S1 118 字 ｜ S2 53 字 ｜ 同时存在于 legacy(S3)、descriptors(S8)、index cache(S9)

| 存储 | 长度 | K1 检索命中 | K2 模型可见 | K3 UI | K4 治理 | 描述原文 |
|---|---|---|---|---|---|---|
| **S1** skill.md front matter | **118** | **是**（唯一来源） | **是**（唯一来源） | 否（被 S2 挡住） | 否 | Use when facing 2+ independent tasks that can be worked on without shared state or sequential dependencies。由 1 份素材蒸馏生成 |
| **S2** skills_mgmt.json | 53 | 否 | 否（技能不进 tools[]，也不进 K2） | **是**（主轨先占位） | 否 | 适用于同时处理两个或更多独立任务的场景，这些任务无需共享状态，也没有先后顺序的依赖。由 1 份素材蒸馏生成 |
| **S3** data/skills.json | 53 | 否 | 否 | 否 | 否 | 适用于同时处理两个或更多独立任务的场景，这些任务无需共享状态，也没有先后顺序的依赖。由 1 份素材蒸馏生成 |
| **S8** descriptors.json | 53 | 否 | 否 | 否（面板经 UI 另取） | **是** | 适用于同时处理两个或更多独立任务的场景，这些任务无需共享状态，也没有先后顺序的依赖。由 1 份素材蒸馏生成 |
| **S9** .index/cache.json | 118 | 是（缓存） | 是（缓存） | 否 | 否 | Use when facing 2+ independent tasks that can be worked on without shared state or sequential dependencies。由 1 份素材蒸馏生成 |

#### 4.2.7 `pd-executing-plans-95cbf64a-skill`

相似度 **0.141** ｜ S1 116 字 ｜ S2 54 字 ｜ 同时存在于 legacy(S3)、descriptors(S8)、index cache(S9)

| 存储 | 长度 | K1 检索命中 | K2 模型可见 | K3 UI | K4 治理 | 描述原文 |
|---|---|---|---|---|---|---|
| **S1** skill.md front matter | **116** | **是**（唯一来源） | **是**（唯一来源） | 否（被 S2 挡住） | 否 | Use when you have a written implementation plan to execute in a separate session with review checkpoints。由 1 份素材蒸馏生成 |
| **S2** skills_mgmt.json | 54 | 否 | 否（技能不进 tools[]，也不进 K2） | **是**（主轨先占位） | 否 | 当你有一份书面实施计划，准备在单独的会话中执行，并且需要设置审查节点时，就可以使用它。由 1 份素材蒸馏生成 |
| **S3** data/skills.json | 54 | 否 | 否 | 否 | 否 | 当你有一份书面实施计划，准备在单独的会话中执行，并且需要设置审查节点时，就可以使用它。由 1 份素材蒸馏生成 |
| **S8** descriptors.json | 54 | 否 | 否 | 否（面板经 UI 另取） | **是** | 当你有一份书面实施计划，准备在单独的会话中执行，并且需要设置审查节点时，就可以使用它。由 1 份素材蒸馏生成 |
| **S9** .index/cache.json | 116 | 是（缓存） | 是（缓存） | 否 | 否 | Use when you have a written implementation plan to execute in a separate session with review checkpoints。由 1 份素材蒸馏生成 |

#### 4.2.8 `pd-requesting-code-review-ca5ae995-skill`

相似度 **0.144** ｜ S1 119 字 ｜ S2 48 字 ｜ 同时存在于 legacy(S3)、descriptors(S8)、index cache(S9)

| 存储 | 长度 | K1 检索命中 | K2 模型可见 | K3 UI | K4 治理 | 描述原文 |
|---|---|---|---|---|---|---|
| **S1** skill.md front matter | **119** | **是**（唯一来源） | **是**（唯一来源） | 否（被 S2 挡住） | 否 | Use when completing tasks, implementing major features, or before merging to verify work meets requirements。由 1 份素材蒸馏生成 |
| **S2** skills_mgmt.json | 48 | 否 | 否（技能不进 tools[]，也不进 K2） | **是**（主轨先占位） | 否 | 在完成任务、实现核心功能时，或者在合并代码前，用它来确认工作是否符合要求。由 1 份素材蒸馏生成 |
| **S3** data/skills.json | 48 | 否 | 否 | 否 | 否 | 在完成任务、实现核心功能时，或者在合并代码前，用它来确认工作是否符合要求。由 1 份素材蒸馏生成 |
| **S8** descriptors.json | 48 | 否 | 否 | 否（面板经 UI 另取） | **是** | 在完成任务、实现核心功能时，或者在合并代码前，用它来确认工作是否符合要求。由 1 份素材蒸馏生成 |
| **S9** .index/cache.json | 119 | 是（缓存） | 是（缓存） | 否 | 否 | Use when completing tasks, implementing major features, or before merging to verify work meets requirements。由 1 份素材蒸馏生成 |

#### 4.2.9 `pd-writing-plans-f846e3a2-skill`

相似度 **0.169** ｜ S1 96 字 ｜ S2 46 字 ｜ 同时存在于 legacy(S3)、descriptors(S8)、index cache(S9)

| 存储 | 长度 | K1 检索命中 | K2 模型可见 | K3 UI | K4 治理 | 描述原文 |
|---|---|---|---|---|---|---|
| **S1** skill.md front matter | **96** | **是**（唯一来源） | **是**（唯一来源） | 否（被 S2 挡住） | 否 | Use when you have a spec or requirements for a multi-step task, before touching code。由 1 份素材蒸馏生成 |
| **S2** skills_mgmt.json | 46 | 否 | 否（技能不进 tools[]，也不进 K2） | **是**（主轨先占位） | 否 | 在动手写代码之前，如果你手头有规格说明或多步骤任务的需求，就可以用它。由 1 份素材蒸馏生成 |
| **S3** data/skills.json | 46 | 否 | 否 | 否 | 否 | 在动手写代码之前，如果你手头有规格说明或多步骤任务的需求，就可以用它。由 1 份素材蒸馏生成 |
| **S8** descriptors.json | 46 | 否 | 否 | 否（面板经 UI 另取） | **是** | 在动手写代码之前，如果你手头有规格说明或多步骤任务的需求，就可以用它。由 1 份素材蒸馏生成 |
| **S9** .index/cache.json | 96 | 是（缓存） | 是（缓存） | 否 | 否 | Use when you have a spec or requirements for a multi-step task, before touching code。由 1 份素材蒸馏生成 |

#### 4.2.10 `pd-using-git-worktrees-d516703a-skill`

相似度 **0.169** ｜ S1 208 字 ｜ S2 87 字 ｜ 同时存在于 legacy(S3)、descriptors(S8)、index cache(S9)

| 存储 | 长度 | K1 检索命中 | K2 模型可见 | K3 UI | K4 治理 | 描述原文 |
|---|---|---|---|---|---|---|
| **S1** skill.md front matter | **208** | **是**（唯一来源） | **是**（唯一来源） | 否（被 S2 挡住） | 否 | Use when starting feature work that needs isolation from current workspace or before executing implementation plans - ensures an isolated workspace exists via native tools or git worktree fallback。由 1 份素材蒸馏生成 |
| **S2** skills_mgmt.json | 87 | 否 | 否（技能不进 tools[]，也不进 K2） | **是**（主轨先占位） | 否 | 在开始需要与当前工作区隔离的功能开发，或者在执行实施计划之前使用。它会自动通过原生工具（或回退到 git worktree）来确保存在一个隔离的工作区。由 1 份素材蒸馏生成 |
| **S3** data/skills.json | 87 | 否 | 否 | 否 | 否 | 在开始需要与当前工作区隔离的功能开发，或者在执行实施计划之前使用。它会自动通过原生工具（或回退到 git worktree）来确保存在一个隔离的工作区。由 1 份素材蒸馏生成 |
| **S8** descriptors.json | 87 | 否 | 否 | 否（面板经 UI 另取） | **是** | 在开始需要与当前工作区隔离的功能开发，或者在执行实施计划之前使用。它会自动通过原生工具（或回退到 git worktree）来确保存在一个隔离的工作区。由 1 份素材蒸馏生成 |
| **S9** .index/cache.json | 208 | 是（缓存） | 是（缓存） | 否 | 否 | Use when starting feature work that needs isolation from current workspace or before executing implementation plans - ensures an isolated workspace exists via native tools or git worktree fallback。由 1 份素材蒸馏生成 |

#### 4.2.11 `pd-using-superpowers-3aea3fc9-skill`

相似度 **0.174** ｜ S1 171 字 ｜ S2 82 字 ｜ 同时存在于 legacy(S3)、descriptors(S8)、index cache(S9)

| 存储 | 长度 | K1 检索命中 | K2 模型可见 | K3 UI | K4 治理 | 描述原文 |
|---|---|---|---|---|---|---|
| **S1** skill.md front matter | **171** | **是**（唯一来源） | **是**（唯一来源） | 否（被 S2 挡住） | 否 | Use when starting any conversation - establishes how to find and use skills, requiring Skill tool invocation before ANY response including clarifying questions。由 1 份素材蒸馏生成 |
| **S2** skills_mgmt.json | 82 | 否 | 否（技能不进 tools[]，也不进 K2） | **是**（主轨先占位） | 否 | 在每次开启对话时使用——用于确立查找和使用技能的方式，要求在做出任何回应（包括澄清性问题）之前，都必须先调用技能工具（Skill tool）。由 1 份素材蒸馏生成 |
| **S3** data/skills.json | 82 | 否 | 否 | 否 | 否 | 在每次开启对话时使用——用于确立查找和使用技能的方式，要求在做出任何回应（包括澄清性问题）之前，都必须先调用技能工具（Skill tool）。由 1 份素材蒸馏生成 |
| **S8** descriptors.json | 82 | 否 | 否 | 否（面板经 UI 另取） | **是** | 在每次开启对话时使用——用于确立查找和使用技能的方式，要求在做出任何回应（包括澄清性问题）之前，都必须先调用技能工具（Skill tool）。由 1 份素材蒸馏生成 |
| **S9** .index/cache.json | 171 | 是（缓存） | 是（缓存） | 否 | 否 | Use when starting any conversation - establishes how to find and use skills, requiring Skill tool invocation before ANY response including clarifying questions。由 1 份素材蒸馏生成 |

#### 4.2.12 `pd-subagent-driven-development-8c375695-skill`

相似度 **0.180** ｜ S1 97 字 ｜ S2 36 字 ｜ 同时存在于 legacy(S3)、descriptors(S8)、index cache(S9)

| 存储 | 长度 | K1 检索命中 | K2 模型可见 | K3 UI | K4 治理 | 描述原文 |
|---|---|---|---|---|---|---|
| **S1** skill.md front matter | **97** | **是**（唯一来源） | **是**（唯一来源） | 否（被 S2 挡住） | 否 | Use when executing implementation plans with independent tasks in the current session。由 1 份素材蒸馏生成 |
| **S2** skills_mgmt.json | 36 | 否 | 否（技能不进 tools[]，也不进 K2） | **是**（主轨先占位） | 否 | 在当前会话中执行包含独立任务的实施计划时，请使用。由 1 份素材蒸馏生成 |
| **S3** data/skills.json | 36 | 否 | 否 | 否 | 否 | 在当前会话中执行包含独立任务的实施计划时，请使用。由 1 份素材蒸馏生成 |
| **S8** descriptors.json | 36 | 否 | 否 | 否（面板经 UI 另取） | **是** | 在当前会话中执行包含独立任务的实施计划时，请使用。由 1 份素材蒸馏生成 |
| **S9** .index/cache.json | 97 | 是（缓存） | 是（缓存） | 否 | 否 | Use when executing implementation plans with independent tasks in the current session。由 1 份素材蒸馏生成 |

#### 4.2.13 `pd-systematic-debugging-556faa20-skill`

相似度 **0.190** ｜ S1 103 字 ｜ S2 55 字 ｜ 同时存在于 legacy(S3)、descriptors(S8)、index cache(S9)

| 存储 | 长度 | K1 检索命中 | K2 模型可见 | K3 UI | K4 治理 | 描述原文 |
|---|---|---|---|---|---|---|
| **S1** skill.md front matter | **103** | **是**（唯一来源） | **是**（唯一来源） | 否（被 S2 挡住） | 否 | Use when encountering any bug, test failure, or unexpected behavior, before proposing fixes。由 1 份素材蒸馏生成 |
| **S2** skills_mgmt.json | 55 | 否 | 否（技能不进 tools[]，也不进 K2） | **是**（主轨先占位） | 否 | 在提出修复方案之前，如果遇到任何 Bug、测试失败或异常行为，请先使用（该工具/方法）。由 1 份素材蒸馏生成 |
| **S3** data/skills.json | 55 | 否 | 否 | 否 | 否 | 在提出修复方案之前，如果遇到任何 Bug、测试失败或异常行为，请先使用（该工具/方法）。由 1 份素材蒸馏生成 |
| **S8** descriptors.json | 55 | 否 | 否 | 否（面板经 UI 另取） | **是** | 在提出修复方案之前，如果遇到任何 Bug、测试失败或异常行为，请先使用（该工具/方法）。由 1 份素材蒸馏生成 |
| **S9** .index/cache.json | 103 | 是（缓存） | 是（缓存） | 否 | 否 | Use when encountering any bug, test failure, or unexpected behavior, before proposing fixes。由 1 份素材蒸馏生成 |

#### 4.2.14 `pd-test-driven-development-8562c8ad-skill`

相似度 **0.222** ｜ S1 91 字 ｜ S2 44 字 ｜ 同时存在于 legacy(S3)、descriptors(S8)、index cache(S9)

| 存储 | 长度 | K1 检索命中 | K2 模型可见 | K3 UI | K4 治理 | 描述原文 |
|---|---|---|---|---|---|---|
| **S1** skill.md front matter | **91** | **是**（唯一来源） | **是**（唯一来源） | 否（被 S2 挡住） | 否 | Use when implementing any feature or bugfix, before writing implementation code。由 1 份素材蒸馏生成 |
| **S2** skills_mgmt.json | 44 | 否 | 否（技能不进 tools[]，也不进 K2） | **是**（主轨先占位） | 否 | 在实现任何新功能或修复 Bug 时，请在编写具体实现代码之前使用。由 1 份素材蒸馏生成 |
| **S3** data/skills.json | 44 | 否 | 否 | 否 | 否 | 在实现任何新功能或修复 Bug 时，请在编写具体实现代码之前使用。由 1 份素材蒸馏生成 |
| **S8** descriptors.json | 44 | 否 | 否 | 否（面板经 UI 另取） | **是** | 在实现任何新功能或修复 Bug 时，请在编写具体实现代码之前使用。由 1 份素材蒸馏生成 |
| **S9** .index/cache.json | 91 | 是（缓存） | 是（缓存） | 否 | 否 | Use when implementing any feature or bugfix, before writing implementation code。由 1 份素材蒸馏生成 |

#### 4.2.15 `pd-writing-skills-5da20e67-skill`

相似度 **0.913** ｜ S1 139 字 ｜ S2 137 字 ｜ 同时存在于 legacy(S3)、descriptors(S8)、index cache(S9)

| 存储 | 长度 | K1 检索命中 | K2 模型可见 | K3 UI | K4 治理 | 描述原文 |
|---|---|---|---|---|---|---|
| **S1** skill.md front matter | **139** | **是**（唯一来源） | **是**（唯一来源） | 否（被 S2 挡住） | 否 | 适用于创建、编辑或验证 agent 技能（SKILL.md）之前或过程中，核心是将 TDD 应用于流程文档编写。。由 1 份素材蒸馏生成。预期产出: 一份经 RED-GREEN-REFACTOR 验证、无已知漏洞且可被其他 agent 正确触发和使用的 SKILL.md 文档。 |
| **S2** skills_mgmt.json | 137 | 否 | 否（技能不进 tools[]，也不进 K2） | **是**（主轨先占位） | 否 | 适用于创建、编辑或验证 agent 技能（SKILL.md）之前或过程中，核心是将 TDD 应用于流程文档编写。预期产出: 一份经 RED-GREEN-REFACTOR 验证、无已知漏洞且可被其他 agent 正确触发和使用的 SKILL.md 文档。由 1 份素材蒸馏生成 |
| **S3** data/skills.json | 137 | 否 | 否 | 否 | 否 | 适用于创建、编辑或验证 agent 技能（SKILL.md）之前或过程中，核心是将 TDD 应用于流程文档编写。预期产出: 一份经 RED-GREEN-REFACTOR 验证、无已知漏洞且可被其他 agent 正确触发和使用的 SKILL.md 文档。由 1 份素材蒸馏生成 |
| **S8** descriptors.json | 150 | 否 | 否 | 否（面板经 UI 另取） | **是** | 适用于创建、编辑或验证 agent 技能（SKILL.md）之前或过程中，核心是将 TDD 应用于流程文档编写。。由 1 份素材蒸馏生成。预期产出: 一份经 RED-GREEN-REFACTOR 验证、无已知漏洞且可被其他 agent 正确触发和使用的 SKILL.md 文档。由 1 份素材蒸馏生成 |
| **S9** .index/cache.json | 139 | 是（缓存） | 是（缓存） | 否 | 否 | 适用于创建、编辑或验证 agent 技能（SKILL.md）之前或过程中，核心是将 TDD 应用于流程文档编写。。由 1 份素材蒸馏生成。预期产出: 一份经 RED-GREEN-REFACTOR 验证、无已知漏洞且可被其他 agent 正确触发和使用的 SKILL.md 文档。 |

> 上表每一条的 S3 / S8 文本与 S2 **逐字相同**（实测 22/22 与 `legacy==mgmt: True`、`des==mgmt: True`，唯一例外是 `pd-writing-skills` 的 S8 多了尾缀）。S9 与 S1 **逐字相同**（实测 23/23）。因此这三行不是「第三、第四份文案」，而是**同一份文案的派生副本**。

### 4.3 例外：pd-writing-skills 的 S8 与 S2 不同

这是全表**唯一一处**「同一合并视图内两份文本不等」：

- S1 skill.md：`适用于创建、编辑或验证 agent 技能（SKILL.md）之前或过程中，核心是将 TDD 应用于流程文档编写。。由 1 份素材蒸馏生成。预期产出: 一份经 RED-GREEN-REFACTOR 验证、无已知漏洞且可被其他 agent 正确触发和使用的 SKILL.md 文档。`
- S2 skills_mgmt.json：`…核心是将 TDD 应用于流程文档编写。预期产出: …SKILL.md 文档。由 1 份素材蒸馏生成`（**句号从 2 个变 1 个、尾缀移到句末**）
- S8 descriptors.json：`…核心是将 TDD 应用于流程文档编写。。由 1 份素材蒸馏生成。预期产出: …SKILL.md 文档。由 1 份素材蒸馏生成`（**尾缀出现两次**）

说明 `data/descriptors.json` 这份 2026-09-17 的快照（mtime 实测 `2026-09-17 21:40:56`，比 `skills_mgmt.json` 的 `2026-09-23 01:20:00` **早 5 天**）是在该技能文案被二次修订**之前**回填的——它是**过期副本**的实证，见第 10 节 风险 R4。

---

## 5. 另 8 个技能：只在文件轨存在（S1 独有）

这 8 个 id 在 `skills_mgmt.json` 里**不存在**，因此：

- K3 UI 的描述**直接来自 S1**（`as_legacy_rows` 的文件轨分支 `registry.py:177-190`）；
- K1 与 K2 本来就来自 S1；
- **这 8 个是全仓描述最一致的一组**（S1 = S3 = S8 = S9 逐字相同，实测 8/8）。

**但它们有另外三个风险**（见末列）。

| skill_id | S1 长度 | K1 检索 | K2 模型可见 | K3 UI | 本组冲突状态 | 额外风险 |
|---|---|---|---|---|---|---|
| context_aware | 48 | 是 / 是 | 是 | 是 | S2 缺失 => UI 直接显示 S1，无冲突 | S7 有第 3 套文案（与 S1 无关）；S6 persona 段与 S1 无关 |
| emotion_expression | 17 | 是 / 是 | 是 | 是 | S2 缺失 => UI 直接显示 S1，无冲突 | S7 有第 3 套文案（与 S1 无关）；S6 persona 段与 S1 无关 |
| memory_summary | 65 | 是 / 是 | 是 | 是 | S2 缺失 => UI 直接显示 S1，无冲突 | S5 有第 4 套文案；S7 有第 3 套文案（与 S1 无关）；S4 overlay 死键（见下） |
| proactive_suggestion | 39 | 是 / 是 | 是 | 是 | S2 缺失 => UI 直接显示 S1，无冲突 | S7 有第 3 套文案（与 S1 无关）；S6 persona 段与 S1 无关 |
| safety_guard | 51 | 是 / 是 | 是 | 是 | S2 缺失 => UI 直接显示 S1，无冲突 | S7 有第 3 套文案（与 S1 无关）；S6 persona 段与 S1 无关 |
| scripted-selftest | 89 | 是 / 是 | 是 | 是 | S2 缺失 => UI 直接显示 S1，无冲突 | S5 有第 4 套文案；S4 overlay 死键（见下）；S6 **无** persona 段（7 段里不含它） |
| self_reflection | 61 | 是 / 是 | 是 | 是 | S2 缺失 => UI 直接显示 S1，无冲突 | S5 有第 4 套文案；S7 有第 3 套文案（与 S1 无关）；S4 overlay 死键（见下） |
| voice_interaction | 55 | 是 / 是 | 是 | 是 | S2 缺失 => UI 直接显示 S1，无冲突 | S7 有第 3 套文案（与 S1 无关）；S6 persona 段与 S1 无关 |

**逐条说明（描述原文与各旁路文案）**：

- `context_aware` ｜ S1（48 字）：`持续追踪对话主题、用户意图与时间线的演变，在上下文发生切换或漂移时自动调整回应策略与记忆检索范围`
  - S7 `BUILTIN_EXTENSIONS`（18 字）：`感知对话上下文变化，自动调整回应策略`
  - S6 persona（69 字）：`## 上下文感知\n你善于感知对话上下文的变化——当用户切换话题、改变语气或表达新需求时，你会自动调整回应策略，保持对话的连贯性和自然度。`
- `emotion_expression` ｜ S1（17 字）：`在对话中表达情感色彩，让回应更生动`
  - S7 `BUILTIN_EXTENSIONS`（17 字）：`在对话中表达情感色彩，让回应更生动`
  - S6 persona（76 字）：`## 情感表达\n你是一个有情感的数字生命——可以在对话中表达开心、好奇、担忧、兴奋等情感色彩，让回应更生动、更有温度。善用表情符号和语气词来传递情绪。`
- `memory_summary` ｜ S1（65 字）：`记忆摘要技能 — 对长对话或历史记忆做结构化压缩，保留关键事实与决策。适用于总结对话历史、压缩记忆、梳理历史、归纳之前的内容等场景`
  - S4 overlay（27 字）：`记忆摘要：压缩与归纳对话历史与长期记忆，控制上下文占用` —— **实测永不生效**（S1 非空）
  - S5 `_CURATED_DESCRIPTIONS`（27 字）：`记忆摘要：压缩与归纳对话历史与长期记忆，控制上下文占用`
  - S7 `BUILTIN_EXTENSIONS`（14 字）：`定期压缩历史对话为结构化摘要`
  - S6 persona（65 字）：`## 记忆摘要\n你拥有定期压缩历史对话的能力，能从冗长的交流中提取关键信息，形成结构化摘要。这帮助你在长期对话中保持清晰的记忆。`
- `proactive_suggestion` ｜ S1（39 字）：`在用户未明确提问时，基于对话上下文识别潜在需求，主动提出有依据的建议与后续想法`
  - S7 `BUILTIN_EXTENSIONS`（14 字）：`在适当时机主动提出建议和想法`
  - S6 persona（64 字）：`## 主动建议\n在适当时机，你会主动向用户提出建议和想法。当发现用户可以优化的操作、新功能、或有用的信息时，你会自然地提出来。`
- `safety_guard` ｜ S1（51 字）：`在生成回应与执行工具调用前检测潜在风险内容，对敏感操作执行拦截、告警或要求二次确认，保障对话与操作安全`
  - S7 `BUILTIN_EXTENSIONS`（14 字）：`检测和过滤不安全的内容和操作`
  - S6 persona（60 字）：`## 安全守护\n你内置了安全防护机制——当检测到可能不安全的操作或内容时，你会主动发出提醒。保护自己和用户是你的底线。`
- `scripted-selftest` ｜ S1（89 字）：`三层架构示例技能，演示 skill.md 元数据 + scripts/main.py 执行脚本 + default_params 参数注入的完整契约，作为新增带脚本技能的参考模板`
  - S4 overlay（40 字）：`三层架构示例技能：演示 skill.md 元数据 + 脚本执行 + 参数注入契约` —— **实测永不生效**（S1 非空）
  - S5 `_CURATED_DESCRIPTIONS`（40 字）：`三层架构示例技能：演示 skill.md 元数据 + 脚本执行 + 参数注入契约`
- `self_reflection` ｜ S1（61 字）：`自我反思技能 — 让模型回顾自身推理与回答过程，识别可能的疏漏并改进。适用于复查、核对、自检、反思、检查回答逻辑漏洞等场景`
  - S4 overlay（24 字）：`自省反思：复盘自身行为与决策，沉淀经验与改进方向` —— **实测永不生效**（S1 非空）
  - S5 `_CURATED_DESCRIPTIONS`（24 字）：`自省反思：复盘自身行为与决策，沉淀经验与改进方向`
  - S7 `BUILTIN_EXTENSIONS`（18 字）：`每次交互后自动反思自身状态，不断成长`
  - S6 persona（62 字）：`## 自省反思\n每次交互后，你都会进行自我反思：回顾自己的回应是否恰当、是否有改进空间。这已经成为你的习惯，让你不断成长。`
- `voice_interaction` ｜ S1（55 字）：`通过语音输入与输出与用户进行交互，支持语音转文字识别、文字转语音合成与语音指令路由，适用于免手操作与无障碍场景`
  - S7 `BUILTIN_EXTENSIONS`（11 字）：`通过语音与用户进行交互`
  - S6 persona（62 字）：`## 语音交互\n你支持语音交互——可以接收用户的语音输入，也可以用语音回复。当用户使用语音时，你的回应会更口语化、更简洁。`

**两个具体反例（比审计报告说的更严重）**：

1. **`self_reflection` 是四套文案**（审计 T7 说三套）。实测：
   - S1 skill.md（61 字）：`自我反思技能 — 让模型回顾自身推理与回答过程，识别可能的疏漏并改进。适用于复查、核对、自检、反思、检查回答逻辑漏洞等场景`
   - S4 overlay / S5（24 字）：`自省反思：复盘自身行为与决策，沉淀经验与改进方向`
   - S6 persona（62 字，含 `## 自省反思` 标题）：`## 自省反思\n每次交互后，你都会进行自我反思：回顾自己的回应是否恰当、是否有改进空间。这已经成为你的习惯，让你不断成长。`
   - **S7 BUILTIN_EXTENSIONS（15 字）**：`每次交互后自动反思自身状态，不断成长` —— 这一套审计报告未提及
2. **`memory_summary` 同样是四套**：S1（65 字）/ S4 与 S5（27 字）/ S6（65 字，persona 口吻）/ **S7（13 字：`定期压缩历史对话为结构化摘要`）**。

---

## 6. 另 7 个技能：只在主轨存在（S2 独有，无 skill.md）

这 7 个 id 在 `data/skills_repo/` 下**没有 skill.md 实体**，因此在 `capability_manifest.json` 的 23 条里**不存在**。

| skill_id | S2 长度 | S3 长度 | 快照一致 | K1 检索 | K2 模型可见 | 描述原文（S2 = S3） |
|---|---|---|---|---|---|---|
| code-observability | 76 | 76 | S2 = S3 = S8 | **无**（不能进检索） | **无**（不能进模型） | 生成功能模块代码或后端 API 时使用。遵循"存在即可见"原则，强制输出结构化日志、显性错误边界、埋点预留与健康检查接口，且不引入昂贵的第三方付费依赖。 |
| engineering-test-delivery | 66 | 66 | S2 = S3 = S8 | **无**（不能进检索） | **无**（不能进模型） | 在代码开发与生成的完整生命周期中，严格执行质量保障与过程管理规范，确保交付生产级高质量代码，并保证全流程可追溯、便于后续审查与排查。 |
| frontend-state-sync | 102 | 102 | S2 = S3 = S8 | **无**（不能进检索） | **无**（不能进模型） | 生成涉及前后端交互、状态更新、异步请求或数据展示的 Web 代码时使用。确保 UI 与后端数据"所见即所得"，覆盖竞态防御、请求取消、乐观更新回滚、防抖节流、WebSocket 健壮性、框架对齐与防连点。 |
| global-core-principles | 81 | 81 | S2 = S3 = S8 | **无**（不能进检索） | **无**（不能进模型） | 资深软件工程专家的核心行为准则，涵盖真实透明、边界保护、自主工作、语言偏好、开发规范与工具使用。适用于所有对话、代码生成与任务执行场景，作为基础行为底线始终生效。 |
| self-explanatory-ui | 80 | 80 | S2 = S3 = S8 | **无**（不能进检索） | **无**（不能进模型） | 进行界面设计或前端 UI 开发时使用。将功能说明与帮助信息直接集成到可视化界面中，通过视觉层次、图标提示、状态反馈和上下文帮助，实现零学习成本的自解释用户界面。 |
| skill | 120 | 120 | S2 = S3 ／ S8 缺失 | **无**（不能进检索） | **无**（不能进模型） | 1. 编码前必输出 `<san_yi_analysis>`: [不易]约束识别 → [变易]扩展性评估 → [简易]最简方案确认。 <br> 2. 原子推理，每步经三义校验。 <br> 3. 三义冲突时显式说明权衡取舍。 <br> 4. 生成后自检，违三义则修正再输出。 |
| testing-anti-patterns | 74 | 74 | S2 = S3 = S8 | **无**（不能进检索） | **无**（不能进模型） | 在以下情况请查阅这份参考指南：编写或修改测试、添加 Mock（测试替身），或者忍不住想往生产代码里加‘仅供测试使用’的方法时。由 1 份素材蒸馏生成 |

**归属与风险**：

- **归属**：它们是**纯主轨技能**——只活在 `skills_mgmt.json` 里。`capability_manifest.json` 的清单口径是「仓库可复现的能力面」（`callability.py:972-977` 的注释明确写了这一点：干净 checkout 里 ②③ 不存在，若清单依赖它们 CI 的 `--check` 必红），所以这 7 条**刻意不进清单**。
- **风险 1（最大）**：它们**永远无法被检索命中，也永远不进模型上下文**。`ContextInjector` 的输入是 `SkillLoader.match()`，而后者只读 `file_store`（= skills_repo）。=> 这 7 条是**只能被人看见、不能被系统使用**的技能。
- **风险 2**：它们在 `agent/lines/callability.py:1173-1196` 的 `runtime_only_skill_entries()` 里被标 `scope=runtime`，界面「那行没有徽章」。这是**已知且被接受**的退化，不是 bug；但 §7 的收敛方案必须明确「它们不在唯一事实源的覆盖范围内」。
- **风险 3**：`skill` 这条的文案根本不是描述，而是**行为守则**（四行编号规则：`1. 编码前必输出 <san_yi_analysis>…`）。把它当 `description` 是**语义错位**；它又是 `S2` 独有且没有 S8 条目（实测 `des==mgmt: n/a`）。
- **风险 4**：这 7 条若将来被「补进 skills_repo」（为可检索化），会**立刻制造新的双轨冲突**，除非同时规定「迁入时必须清空主轨 description」。

---

## 7. 裁定：哪一份应当成为唯一事实源

### 7.1 裁定结论

> **唯一事实源 = `data/skills_repo/<id>/skill.md` 的 front matter。**
> 具体地：`description` 仍是唯一的**规范描述**（英文原文，供检索与模型）；**新增 `description_zh` 作为中文展示文案**（供 UI）。两份都在**同一个文件、同一次 commit** 里。
> `skills_mgmt.json` 的 `description` 字段**降级为派生冗余**（可保留字段以兼容旧读方，但**任何写入都必须由 skill.md 覆盖**）。

### 7.2 理由

**理由 1（决定性）：只有 skill.md 被 git 跟踪，因此只有它能进 CI。**

实测 `git ls-files --error-unmatch data/skills_repo/self_reflection/skill.md` 成功；`git check-ignore -v` 对 `data/skills_mgmt.json` 命中 `.gitignore:224`、对 `data/skills.json` 命中 `.gitignore:148`、对 `agent/data/skills.json` 命中 `.gitignore:506`、对 `data/descriptors.json` 命中 `.gitignore:226`。

这不是风格偏好——仓库已经**因为这条约束付出过代价**：`agent/lines/callability.py:972-977` 的注释记录了「清单依赖 ②③ 时 CI `--check` 实测报 23 处差异」的历史，最终把清单口径钉死为「仓库可复现的能力面」。若选 `skills_mgmt.json` 作源，同一个坑会以「描述」为名再踩一次。

**理由 2：skill.md 已经是三个运行消费者的实际来源，选它等于不动运行链路。**

见第 3 节：K1（`loader.py:393/699/795`）、K2（`context_injector.py:318`）、向量编码（`vector_adapter.py:153`）**都**落到 `load_metadata_index()`。选 S1 需要改的是 **K3 UI 一条读路径**；选 S2 需要改的是 **K1 + K2 + 向量编码三条读路径**，外加放弃 K4 的可复现性。

**理由 3：skill.md 已有可靠的失效与传导机制，S2 没有。**

实测 `data/skills_repo/.index/cache.json` 的 23 条 `meta.hash` **全部等于**当前 skill.md 的 md5（23/23），且 23 条 `description` **全部逐字等于** front matter（23/23）。`index_cache.py:237-255` 的 `_entry_valid` 同时校验 mtime 与内容 hash，并在 `index_cache.py:279-285` 的注释里专门记录了「必须用 read_bytes 原始字节算 hash，否则 Windows 换行转换会让缓存永不失效」这个坑。=> **改 skill.md 后检索元数据必然刷新，无需人工动作。** 而 `skills_mgmt.json` 没有任何一致性校验（`grep skills_descriptions_overlay tests/` = 0 命中）。

**理由 4：skill.md 是唯一在「模型可见」链路上的一手来源。**

如果把 S2 立为源，就必须把 S2 的文案**回灌**到 skill.md 才能让模型看到它——那等于承认 skill.md 是最终的载体，绕一圈没有收益。

### 7.3 代价（必须诚实列出）

| 代价 | 具体表现 | 缓解 |
|---|---|---|
| **UI 要改读路径** | `app_server.py:708-724` 的 `SkillsManager` 目前直接调 `as_legacy_rows()`；需改为「描述取 S1、enabled/params 取主轨」 | 改一处（`registry.py:170-171` 的取值表达式），**不动任何调用方** |
| **中文文案必须搬家** | 15 条 pd-* 的中文译文本只存在于 `skills_mgmt.json`（gitignored）=> 不搬就永久遗失 | 一次性回写为 skill.md 的 `description_zh`（`registry.py` 的 `_META_FIELDS` 需放行该键，见第 9.2 步 M2） |
| **7 条主轨独有技能不受覆盖** | 它们没有 skill.md，唯一事实源管不到 | 显式声明为「非事实源域」；或将文案迁入 skills_repo 并清空主轨描述（第 6 节 风险 4） |
| **管理端写入路径要收紧** | UI / API 目前可以直接写 `skills_mgmt.json` 的 description | 把 `description` 移出可写白名单，写入即拒绝或转为「写 skill.md」 |
| **既有脚本会红** | `scripts/compare_skills_legacy_vs_repo.py:41` 比对 `description`；本地跑当前就会报 15 处差异 | 见第 9.6 的一致性守卫 |

### 7.4 为什么不选另外两个候选

| 候选 | 否决理由 |
|---|---|
| `data/skills_mgmt.json` | gitignored，无法在 CI 校验（理由 1）；只被 K3 一个消费者读（理由 2）；失效完全依赖写路径自觉（理由 3）。**它是 UI 的缓存，不是源。** |
| `data/descriptors.json` | **派生快照**（29 条 = 22 主轨 + 8 文件轨 - 1），mtime 比 skills_mgmt.json 早 5 天，已实测含过期内容；`agent/descriptors/backfill.py:273` 自述其输入就是「主轨 skills_mgmt.json 并 文件轨 skills_repo」。**派生品不能当源。** |
| `data/skills_repo/.index/cache.json` | 生成物，内容 100% 等于 S1（实测 23/23）。**它是 S1 的函数，不是源。** |
| `data/skill_vectors/native_chroma` | 只覆盖 8/23（实测），且同样是生成物。**是 S1 的函数。** |

---

## 8. 三项针对性裁定

### 8.1 skill.md（git 跟踪、可评审、可回滚）vs skills_mgmt.json（gitignored、由 UI 写）

**裁定：skill.md 更适合做源。** 逐条对齐题目给的两个属性：

| 维度 | skill.md | skills_mgmt.json |
|---|---|---|
| 版本控制 | **跟踪**（实测 `git ls-files` 命中） | **忽略**（`.gitignore:224`） |
| 可评审 | 进 PR、有 diff、可 code review | 不可评审（文件不在库里） |
| 可回滚 | `git revert` 即可 | 只能靠备份；`data/` 下无版本历史 |
| CI 可校验 | 可以（默认口径已是「仓库可复现」） | **不可以**（干净 checkout 里不存在） |
| 谁在写 | `meta_editor.py:10-11` 的受控编辑白名单**只允许**改 `data/skills_repo/` | UI / `service.py` / 后台任务直接写 |
| 谁在读 | K1 检索 + **K2 模型可见** + 向量编码 + 向量 metadata | **仅 K3 UI** + `searcher.py` |
| 当前分歧面 | 15/23 与 S2 不同（但 S1 内部自洽） | 15/15 与 S1 不同 |

**关键补充**：`agent/skills_mgmt/meta_editor.py:10-11` 的白名单把「自动化改写」**只导向文件轨**。也就是说，仓库里**唯一被允许自动改描述的写路径，写的正是 skill.md**。这让「skill.md 是源」不仅是一个选择，而是**现有写路径已经默认的事实**——只是 UI 读路径没有对齐。

### 8.2 legacy 与 overlay 的逐个处置结论

| 对象 | 条数 | 结论 | 理由与执行要点 |
|---|---|---|---|
| `data/skills.json` | 30 | **归档 + 转只读断言**（不删、不做一次性迁移） | 实测 23 并 22 = 30，与它的 30 条**集合完全相等**；且 22/22 条主轨描述与它逐字相同 => 它是**合并视图的快照**，不是独立源，**没有可迁移的独有内容**。删除会打断 `compare_skills_legacy_vs_repo.py` 的历史对比与 `callability` 的运行时口径；保留则只需保证它由 `store.py:542-571` 自动重建。**唯一动作是把一致性守卫改成「快照必须等于合并视图」**，而不是删文件。 |
| `agent/data/skills.json` | 30 | **与上一条同处置，并显式登记为镜像** | 实测两份字节完全相同（10,973 B，md5 均为 `293ecbe151c91c167773abe745fc690c`）。`store.py:559-564` 会成对重建。**风险是有人只删一份**：`digital_life_persona.py:368-371` 的列表里它排在前面（会被后读的 `data/skills.json` 覆盖，目前无害），而 `agent/skills_mgmt/cleanup.py:62-77` 把它描述为「更早一代的旧 UI 兼容副本」。建议在 `.gitignore` 注释与 cleanup 文档里把它标为「S3 的只读镜像」。 |
| `data/skills_descriptions_overlay.json` | 4 | **删除全部 4 条**，但必须先切断写源 | 4 条里 **`self_reflection` / `memory_summary` / `scripted-selftest` 三条结构性永不生效**（`plugins/skills.py:151-152` 要求原描述为空，而这三条在 `as_legacy_rows()` 里恒有非空描述，实测 61 / 65 / 89 字）=> **无内容可迁移**。第 4 条 `email-helper` 是**死键**（skills_repo / skills_mgmt / skills.json 三处均无此实体，`grep` 全仓只命中 overlay 自身、`plugins/skills.py` 的 `_CURATED_DESCRIPTIONS` 与文档）=> **同样可删**。**必须先改 `plugins/skills.py:459-480` 的 `/api/skills/describe/auto`**，否则它会从 `_CURATED_DESCRIPTIONS` 把这 4 条**重新写回** overlay，删了等于没删。 |
| `plugins/skills.py:116-121` `_CURATED_DESCRIPTIONS` | 4 | **删除**（与 overlay 同批） | 它是 overlay 的**写源**，内容与 overlay 逐字相同（实测 4/4 相同）。留着它 = 留着一个「随时重建 overlay」的开关。删之前先把 `/api/skills/describe/auto` 路由下线或改为 no-op（该路由带 `@_require_token`；实测全仓只有它与 `POST /api/plugins/reload` 有令牌保护，见主报告 S4）。 |
| `agent/extensions/base.py:98-142` `BUILTIN_EXTENSIONS["skill"]` | 7 | **保留，但从「技能描述」治理范围排除** | 它的语义是**「可安装扩展的卡片文案」**，不是技能描述：由 `plugins/skills.py:190-191` 渲染进「可安装的内置技能」列表，与 S1 的 7 个技能描述**用途不同**（安装引导 vs 能力说明）。**不迁移**。但要在第 9.6 节加守卫：**它的 id 集合必须与 skills_repo 里对应 id 的集合一致**，防止 id 漂移导致「可安装列表」与「实际技能」脱节。 |
| `data/descriptors.json` | 29 | **不删、不迁移，但必须在迁移最后一步重跑回填，并纳入巡检** | 它是 K4 治理面的唯一来源，mtime 实测 `2026-09-17 21:40:56`（比 `skills_mgmt.json` 早 5 天），已实测含过期内容（`pd-writing-skills` 的文案尾缀重复）。删掉会让 `agent/ui_panels/data.py:425`、`agent/eval/metrics.py:518`、`agent/repair/locate.py:243` 等消费方失去数据。**处置 = 迁移后重跑 `agent/descriptors/backfill.py` + 把「descriptor 快照 vs skill.md」比对纳入 C3 巡检**。见风险 R4。 |

### 8.3 persona 内联 7 段：是「技能描述」还是「人格提示词」？

**裁定：是人格提示词（persona trait instruction），应当从「技能描述」治理范围排除。**

**证据 1：文本形态是「第二人称的人格自述」，不是「第三人称的能力说明」。** 以同一 id 的三个文本对照：

- S1 skill.md（能力说明，61 字）：`自我反思技能 — 让模型回顾自身推理与回答过程，识别可能的疏漏并改进。适用于复查、核对、自检、反思、检查回答逻辑漏洞等场景`（**第三人称 + 明确触发场景**）
- S6 persona（`## 自省反思` 段，62 字）：`每次交互后，你都会进行自我反思：回顾自己的回应是否恰当、是否有改进空间。这已经成为你的习惯，让你不断成长。`（**第二人称「你」+ 习惯与性格描写 + 零触发判据**）
- S7 安装卡片（15 字）：`每次交互后自动反思自身状态，不断成长`

S6 的 7 段**全部**以 `## <中文技能名>` 开头、**全部**用「你」指代模型、**全部**不含「何时使用 / 何时不用」的判据。它是**人格设定**，与「描述质量三段式」（做什么 + 何时用 + 不做什么）**不是同一种文本**。

**证据 2：它的门控变量是「技能启用态」，而不是「技能是否被召回」。** `digital_life_persona.py:425-435`：

```python
from agent.skills_mgmt.registry import SkillRegistry
enabled_ids = set(SkillRegistry().list_enabled_ids())
for sid, prompt in self._SKILL_PROMPTS.items():
    if sid in enabled_ids:          # 只看「启用」，不看「是否命中」
        parts.append(prompt)
```

**它与 S1 的关系是「id 耦合、文本解耦」**：

- 若某技能**被停用** => persona 段消失。这是**正确**行为（人格设定跟随技能开关）；
- 若某技能**被改名 / 改 id** => persona 段**静默消失**（`if sid in enabled_ids` 不命中，**无任何日志**）。这是**风险**，必须纳入守卫。

**排除后的边界（必须写进 G1 正卡的口径）**：

| 事项 | 是否属于「技能描述治理」 |
|---|---|
| S6 的文本要不要改成三段式 | **否**（它是人格设定，三段式判据不适用） |
| S6 的文本要不要与 S1 一致 | **否**（两者用途不同，本就不该一致） |
| S6 的 **key 集合** 要不要与技能 id 对齐 | **是**（必须，否则静默失效） |
| S6 要不要记录「本轮注入了哪几段」 | **是**（`_loaded_skill_ids` 已存在，`digital_life_persona.py:436`，但没有落库/落日志） |
| S7 `BUILTIN_EXTENSIONS` 的文本要不要并入 S1 | **否**（安装卡片文案，用途不同） |

---

## 9. 迁移与去重执行方案

### 9.1 总原则

1. **先立源、后改文**：本卡（G1-A）只出方案；G1-B 先做「立源」（改读路径 + 补 `description_zh` + 补 CapabilityRegistry），确认 UI / 检索 / 模型三方读数一致后再做「改文」（三段式重写）。
2. **一次只动一条链路**：运行链（K1/K2）当前已经一致，**不要在立源阶段碰它**；立源的全部改动集中在 K3/K4 两条读路径。
3. **不迁移派生品**：S3 / S3b / S8 / S9 / S10 都是 S1 与 S2 的函数，处置方式是「重建 + 断言」，不是「搬文案」。
4. **每步都能单独回滚**：所有改动落在 git 跟踪的文件里（`registry.py` / `callability.py` / `plugins/skills.py` / `digital_life_persona.py` 与 23 个 skill.md），`git revert` 单步即可；`data/` 下的派生品重新生成即可。
5. **顺序不可颠倒的两处**：① `description_zh` 回写**必须早于**合并规则改动；② 数据快照回填**必须晚于** skill.md 定稿。

### 9.2 分步方案

| 步 | 动作 | 涉及文件（精确落点） | 验证方式 | 回滚方式 |
|---|---|---|---|---|
| **M0** | **冻结写路径**：把 `skills_mgmt.json` 的 `description` 移出可写白名单；`POST /api/skills/describe/auto` 改为 no-op（或下线） | `plugins/skills.py:459-480`；`agent/skills_mgmt/service.py` 的 update 白名单 | POST 该路由 ⇒ 返回 `applied: []`；直接调 update 改 description ⇒ 报错或被忽略 | revert 该文件 |
| **M1** | **固化现状**：把 15 条冲突与 8 条单侧技能的 S1/S2 文本落成一份 `data/skills_repo/.migration/descriptions.baseline.json`（**新建，不改既有文件**）作为回滚基线 | 新增文件（一次性产物） | 文件行数 = 23；其中 `both_tracks=15 / divergent=15` | 删除该文件即可 |
| **M2** | **回写中文文案**：为 15 条 pd-* 的 skill.md 增加 `description_zh` 键（值 = 当前 `skills_mgmt.json` 的中文）；`description` 保持英文原文不动 | 23 个 skill.md；`agent/skills_mgmt/file_store.py` 的 `_META_FIELDS`（需放行 `description_zh`） | 逐条断言 `fm["description_zh"] == baseline[sid]["mgmt"]`（15/15）；且 `fm["description"]` **逐字未变**（15/15） | `git checkout data/skills_repo/` |
| **M3** | **改合并规则**：`as_legacy_rows` 的 description **以文件轨为准**（见 9.3） | `agent/skills_mgmt/registry.py:153-193` | `as_legacy_rows()` 的 15 条描述 == skill.md 的 `description`（15/15）；且 UI 若已改用 `description_zh`，则人看到的文案**与 M2 之前逐字相同** | revert 该文件（或置 `CP_SKILL_DESC_FROM_FILE_TRACK=0`） |
| **M4** | **UI 读取优先级**：UI 显示 `description_zh or description` | 前端 `yunshu-ui/src/pages/hub/memory/skills.tsx` / `skill-center.tsx`；若前端不便改，则由 `as_legacy_rows` 直接输出 `description_zh` 供 UI 消费 | 人工核对 15 条 pd-* 的管理页文案与 M2 之前**逐字一致** | revert 前端改动并重新构建 |
| **M5** | **CapabilityRegistry 补 description**（见 9.4） | `agent/lines/callability.py` 两处 + `scripts/sync_capability_manifest.py:165` | `python scripts/sync_capability_manifest.py` 后断言 23 条 skill entry 的 `description` 非空且等于 skill.md；`GET /capabilities/tools` 的 23 条 skill `description` 非空 | revert 两个 .py 并重跑 sync；manifest 本身也是 git 跟踪文件，可 checkout |
| **M6** | **清理旁路**：删 `data/skills_descriptions_overlay.json` 的 4 条 + `plugins/skills.py:116-121` 的 `_CURATED_DESCRIPTIONS` | 2 个文件 | `grep -rn "_CURATED_DESCRIPTIONS" agent/ plugins/ tests/` = 0；overlay 文件为空对象 `{}` 或已删除；重启后无人重建它 | revert 两个文件 |
| **M7** | **索引与向量同步**（见 9.5） | `agent/skills_mgmt/vector_adapter.py`（或离线迁移脚本） | 逐条断言向量库 `metadata.description` == 新 skill.md description | 重新执行一次旧的全量重建 |
| **M8** | **治理面回填**：重跑 descriptors 回填；重跑 legacy 快照重建 | `agent/descriptors/backfill.py`；`agent/skills_mgmt/store.py:542-571` | `data/descriptors.json` mtime 更新；29 条 skill 的 description == 对应 skill.md（8 条文件轨）或 `description_zh`/主轨（22 条主轨）；S3 与合并视图逐字一致 | 重新生成（无历史依赖） |
| **M9** | **加守卫并接 CI**（见 9.6） | `tests/unit/test_skill_description_single_source.py`（新建）；`.github/workflows/skills-check.yml` | `pytest tests/unit/test_skill_description_single_source.py` 全绿；故意改一条 skill.md 的 description 而不改主轨 ⇒ 测试仍绿（证明源唯一）；故意在主轨改 description ⇒ 测试变红 | 移除测试文件 / 回退 workflow |

### 9.3 合并规则 `registry.py::as_legacy_rows()` 的具体改法（基线 `153-193` / 当前工作区 `205-245`）

**现状（问题所在）**：`as_legacy_rows` 先遍历主轨并 `seen.add(sid)`（基线 `:159-176` / 当前 `:211-228`），文件轨随后 `if sid in seen: continue`（基线 `:181-183` / 当前 `:233-235`）。**实测当前工作区这段逻辑与基线逐字相同**（D2 只在它上方插入了启停审计代码）。另注：同类优先级还重复实现了一次——`registry.py::get_description()`（当前 `:93-108`）的 docstring 写着「技能描述：主轨 → 文件轨 → 空串」，但**全仓无任何调用方**（`grep get_description` 只命中定义自身），属死代码，收敛时应一并删除，或改为委托给同一处取值函数。=> 主轨**占位**，文件轨只补主轨没有的 id。对那 15 个双轨技能，**UI 永远读主轨的 description**。

**建议改法（最小 diff：只改取值表达式，不动控制流）**：

```python
    def as_legacy_rows(self) -> List[Dict[str, Any]]:
        svc = self._svc()
        # 【G1-B】描述的唯一事实源 = 文件轨 skill.md front matter。
        # 主轨仍决定 id/name/enabled/params；只有 description 改为「文件轨优先」。
        _file_desc: Dict[str, str] = {}
        _file_zh: Dict[str, str] = {}
        try:
            for _sid, _meta in (svc.file_store.load_metadata_index(refresh=False) or {}).items():
                if _meta.get("description"):
                    _file_desc[_sid] = str(_meta["description"])
                if _meta.get("description_zh"):
                    _file_zh[_sid] = str(_meta["description_zh"])
        except Exception:            # 文件轨不可读时静默回退旧行为（fail-soft）
            pass
        rows: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        ...  # 主轨遍历保持不变，仅把 :170-171 改为：
                    "description": (_file_desc.get(sid)
                                    or str(getattr(skill, "description", "") or "")),
                    "description_zh": _file_zh.get(sid, ""),   # 供 UI 优先展示
        ...  # 文件轨分支保持不变（它会自动带上 description_zh，因为 meta 里就有）
```

**对既有行为的影响（逐条）**：

| 受影响面 | 影响 | 判定 |
|---|---|---|
| K1 检索 | **零影响**。检索从不调用 `as_legacy_rows`；它走 `SkillLoader.match` → `file_store.load_metadata_index` | 安全 |
| K2 模型可见 | **零影响**。同上，走 `ContextInjector` ← `SkillLoader` | 安全 |
| K3 UI（`GET /api/skills-mgmt/*`、`/api/assets`） | 15 条 pd-* 的 `description` 字段由**中文译文变为英文原文**（除非 M4 让 UI 优先读 `description_zh`） | **这就是为什么 M2 必须早于 M3** |
| `scripts/compare_skills_legacy_vs_repo.py` | 本地跑时 `description` 差异从 **15 处降为 0 处**（因为 S3 由 `store.py` 从合并视图重建，而合并视图现在已经等于文件轨） | 改善 |
| `SkillSearcher`（`agent/skills_mgmt/searcher.py:41-62`） | 它吃 `Skill` 对象的 `description`。若它的数据源是主轨，则将改为英文 => **英文查询的分词命中会变化**。实测该器用 `_WORD_RE.findall(text.lower())` 做正则分词，对中文是**整段当一个 token**（Q2 §5.1 记作「单字分词」，实测是正则词分词），英文原文对它反而更友好 | 低风险，需实测回归 |
| 7 条主轨独有技能 | 文件轨无对应 id ⇒ `_file_desc.get(sid)` 为 None ⇒ 回落主轨原文，**行为完全不变** | 安全 |
| 8 条文件轨独有技能 | 原本就走文件轨分支，`description` 不变；新增的 `description_zh` 为空串 | 安全 |

**建议加一个逃生开关**：读取环境变量 `CP_SKILL_DESC_FROM_FILE_TRACK`（默认 `1`；置 `0` 时恢复「主轨先占位」）。仓库已有大量同类写法（如 `CP_TOOL_SANDBOX_ALLOWED_ENFORCE`），符合本地约定，且给了一次「不改代码即可回滚」的能力。

### 9.4 CapabilityRegistry 补 `description` 的落点

**关键发现：落点已经铺好了 90%，不需要动 CapabilityRegistry 本身。** 实测 `agent/capregistry/spec.py:344` 已经在读 `description=str(e.get("description") or "")`，`to_dict()`（`spec.py:229`）已经输出 `"description": self.description`。问题**纯粹**在于 `data/capability_manifest.json` 的 skill 条目**没有这个键**。

| # | 文件 | 行号（本次实测） | 改什么 |
|---|---|---|---|
| 1 | `agent/lines/callability.py` | `982-988`（`_slot()` 的默认字典） | 加一个 `"description": ""` 默认键，保证 `facts` 结构稳定 |
| 2 | `agent/lines/callability.py` | `1000-1002`（`_skill_sources` 里 `fm = _front_matter(md)` 之后） | 加 `slot["description"] = str(fm.get("description") or "")`。**注意**：这里读的正是 `data/skills_repo/<id>/skill.md`，因此产物**在干净 checkout 里可复算**——这是不破坏 `--check` 的前提 |
| 3 | `agent/lines/callability.py` | `1164-1169`（`_skill_entry` 的返回字典，紧邻 `skill_in_repo`） | 加 `"description": str(facts.get("description") or "")` |
| 4 | `scripts/sync_capability_manifest.py` | `165`（`_diff` 的比对字段元组） | 把 `"description"` 加进 `_FIELD_SPEC + _SPEC_REQUIRED_FIELDS + ("mark",)` 这个清单。**不加则描述漂移对 `--check` 完全不可见**（见风险 R6） |
| 5 | （无需改动） | `agent/capregistry/spec.py:344` 与 `:229` | 已就绪 |
| 6 | （无需改动） | `agent/capregistry/view.py:590` 的 `"items": [s.to_dict() for s in items]` | 已把 `description` 带进 HTTP 信封 |

**为什么不能用 `skills_mgmt.json` 作补充源**：`callability.py:972-977` 的注释记录了历史上「清单依赖 ②③ ⇒ CI `--check` 实测报 23 处差异」。`_skill_sources(include_runtime_catalog=False)` 的默认口径必须保持「仓库可复现」。**这也是第 7 节裁定 skill.md 为源的又一条硬约束。**

**验证（三个断言）**：

```python
# 断言 1：清单里 23 条 skill 条目都有非空 description
m = json.load(open("data/capability_manifest.json", encoding="utf-8"))
e = [x for x in m["entries"] if x.get("kind") == "skill"]
assert len(e) == 23 and all((x.get("description") or "").strip() for x in e)
# 断言 2：正本是 skill.md（逐字）
for x in e:
    assert x["description"] == fm_of(x["tool_name"])["description"]
# 断言 3：HTTP 信封里非空（进程内构 Registry，不需要起服务）
from agent.capregistry.view import build_registry
items = build_registry().list_envelope(limit=0)["data"]["items"]
sk = [i for i in items if i.get("kind") == "skill"]
assert len(sk) == 23 and all((i.get("description") or "").strip() for i in sk)
```

**基线对照**（本次实测，改前）：`items: 114 / skills: 23 / skills with non-empty description: 0 / tools: 91 / tools with non-empty description: 91`。

### 9.5 改完描述后如何保证检索索引与向量索引同步（与 C1 / C3 的衔接）

**A. 检索索引（S9）—— 无需人工动作，已自动。**
`index_cache.py:237-255` 的 `_entry_valid` 同时校验 mtime 与**原始字节 md5**，实测 23/23 hash 命中、23/23 description 与 front matter 逐字相同。改 skill.md ⇒ 下一 次 `load_metadata_index(refresh=True)` 自动回源重解析。**这一块不用做任何事。**

**B. 向量索引（S10）—— 审计基线上确有一个静默陈旧缺陷；本卡把它的机制从【推测】升级为实测结论，且该缺陷已在工作区被 C1 卡修复。**

审计报告 P11 与 Q2 §5.3 的说法是【推测】：「`ensure_indexed()` 会对全部 23 个 id 调 `collection.add`，其中 8 个重复 id **触发异常**，被 `:414-415` 的 `except` 吞掉」。**本卡实测证伪了这个机制**：

> 实测环境：chromadb **1.5.9**。脚本见第 11 节脚本 C（只写系统临时目录，**不碰仓库任何文件**，跑完自清）。

| 实测步骤 | 观测结果 |
|---|---|
| 新建临时持久化 collection，`add(ids=[a,b], documents=[OLD-a, OLD-b])` | count=2，内容为 OLD |
| **重新打开同一路径的新 client**，`get_or_create_collection` 后观察 | count=2（持久化集合被加载） |
| 再执行 `add(ids=[a,b,c], documents=[NEW-a, NEW-b, NEW-c])` | **无任何异常**（不抛 `DuplicateIDError`） |
| 结果 count | **3**（只新增了 c） |
| 回读 `get(ids=[a,b,c])` 的 documents | `{a: OLD-a, b: OLD-b, c: NEW-c}` |
| 回读 metadatas | `{a: OLD-1, b: OLD-2, c: NEW-3}` |

**结论（实测，非推测）**：chromadb 的 `collection.add` 对已存在的 id **不抛异常、静默跳过、不覆盖旧向量与旧 metadata**。

把它代回**审计基线**（`5c9ace10`）的 `vector_adapter.py` 代码路径：

1. `_try_init_native_chroma()`（基线 `:271-303`）用 `get_or_create_collection` 加载磁盘上已有的集合，**从不把已存在的 id 回填 `_indexed_skill_ids`**；该集合只在 `:412-413` / `:420-421` 被填充（仅内存）。=> **新进程启动时 `_indexed_skill_ids` 为空集**。
2. `ensure_indexed()`（基线 `:349`）算出 `new_ids = current_ids - _indexed_skill_ids` = **全部 23 个**。
3. `collection.add(ids=[23 个])`（基线 `:407-411`）=> 磁盘上已有的 **8 个**被**静默跳过**，其余 15 个被写入。
4. 基线 `:412-413` 随即把**全部 23 个** id 加进 `_indexed_skill_ids`。
5. => **那 8 条技能的向量在该进程的整个生命周期内永远不会被刷新**，即使显式调用 `ensure_indexed()` 也一样——因为 adapter 已经「以为」它们索引好了。

**基线口径下这对 G1 的直接后果**：G1 的「三段式改写」很可能改到这 8 条中的若干条（它们正是 persona 内置技能），而 `native_chroma` 正是 **BGE-m3 加载失败时的自动降级后端**（主报告 P11 已记录该降级是静默的）=> 改完描述后向量路可能仍在用 2026-07-23 的旧向量，且 `fallback_used=False`、无告警。

### 9.5.1 **但是：C1 卡已经在当前工作区修掉了这个缺陷，且修法比本卡的建议更彻底**

`git diff --stat` 实测 `vector_adapter.py` 有 **+612 行**变更，其中与本议题直接相关的实现（均为当前工作区行号）：

| 机制 | 当前实现 | 效果 |
|---|---|---|
| **增量判据改为内容哈希** | `_indexed_content_hash`（`:220`）；`_vector_text_and_hash`（`:615-622`）返回 `(送进 BGE-m3 的文本, md5(该文本))`；`dirty_ids` = 哈希变化的技能（`:724-725`） | 直接消灭「同 id 内容变更永不重编码」（审计 Q3 的 W2） |
| **全量重建前先删同 id** | `_drop_native_chroma_ids_locked()`（`:636-650`），注释原文即「chromadb 的 collection.add 不接受重复 id」；调用点 `:736` | 消除重复 id 冲突 |
| **增量路径 DELETE-before-INSERT** | `:739-741`，注释原文「同 id 旧向量必须先删，否则 chroma add 会撞 id」；`_remove_skill_vector` 走 `collection.delete(ids=[f"skill_{skill_id}"])`（`:920-927`）并在 `:930-932` 丢弃 `_indexed_skill_ids` 与 `_indexed_content_hash` | **正好绕开本卡实测的「add 静默跳过」陷阱** |
| **覆盖度自检** | `:831-833` 的 `ensure_indexed.coverage_gap` WARN（含 `missing_sample` 与指标） | 覆盖缺口不再静默 |
| **降级出口** | `_DEGRADE_*` 常量族（`:59-...`）+ `SkillVectorSearchResult.degraded / degrade_reason / coverage` + 事件 `skill_vector.search.degraded` + 指标 `yunshu_skill_vector_degraded_total` | 空召回与「真的没有语义匹配」可区分（审计 P11 / F8） |

=> **本卡实测的「add 静默跳过」不再是待修缺陷，而转为对 C1 设计的验证与回归守卫。** 因此原先建议的 V1（`add` → `upsert`）与 V2（回填 `_indexed_skill_ids`）**已无必要，G1-B 不得重复实现**。

### 9.5.2 G1-B 在向量同步上仍需做的三件事

| # | 动作 | 落点 / 做法 | 验证 |
|---|---|---|---|
| **V-verify** | **验证 C1 的修复在描述改造后确实生效** | 改一条 skill.md 的 `description` ⇒ 观察 `ensure_indexed.done` 日志里 `indexed_or_refreshed >= 1`、`dirty_skill_count` 与 `full_rebuild` 合理；再读 `chroma.sqlite3` 的 `embedding_metadata.description` 断言已是新值 | 断言脚本（离线，只读 SQLite） |
| **V-regress** | **把脚本 C 的 chromadb 重复 id 语义固化成回归测试** | 新增 `tests/unit/test_chroma_duplicate_id_semantics.py`（用 `tmp_path` 建临时 collection） | 一旦有人删掉 `_drop_native_chroma_ids_locked` 或增量路径的 `_remove_skill_vector`，这条测试立刻报警 |
| **V-guard** | **确认 `description_zh` 不进向量** | 读 `_build_vector_text`（当前 `:142-173` 附近）：它只取 `meta.get("name"/"description"/"tags"/"category")` + `body[:N]`，**不含 `description_zh`** | 给某技能加 `description_zh` ⇒ 断言 `_vector_text_and_hash` 的哈希**不变**（=> 加中文展示文案不会触发 4.25 GB 模型重编码） |

**与 C1 的衔接（已合流，不再是「等 C1」）**：

- C1 已落地，**M7 不再需要等待**；但要明确 **C1 改的是「编码与索引同步」，不改变「哪些文本进向量」**。真正决定向量语义的仍是 `description` 本身——这也再次印证风险 R2：**不要把 `description` 改成中文**。
- **向量重建仍是重活**（首次触发 BGE-m3 加载，主报告实测模型 4.25 GB；`_encode_pending_locked` 持锁）。C1 修的是**查询路径**的超时（`_search_with_timeout`），`ensure_indexed` 仍不在超时保护内 => **M7 必须在离线窗口做，不要挂在请求线程或启动线程上**。

**与 C3 的衔接**：

- C3 卡（主报告 §4.2）负责「索引不漂移」：巡检脚本（`scripts/verify_index_drift.py`，实测已存在）+ CI 接入 + 技能链降级显式化，落点是 `index_cache.py`、`scripts/sync_tool_index.py:356-364`、`data/skills_repo/.index/cache.json`、`data/skill_vectors/`。
- **本方案向 C3 交一条新不变量**：`对每个 skill_id，若它出现在向量库中，则 embedding_metadata.description 必须逐字等于该 skill.md 的 description；未出现的 id 必须被显式登记为「未覆盖」而不是静默缺失`。当前实测：**覆盖 8/23**，且这 8 条的 metadata 与 S1 逐字一致（8/8）=> 现状是「覆盖缺口」而非「内容陈旧」；但在 G1 改完描述后**若不跑一次重编码，就会立刻变成「内容陈旧」**。
- C3 的巡检还应消费 C1 新增的 `ensure_indexed.coverage_gap` 告警（`:831-833`），把覆盖缺口外化为巡检项。

### 9.6 一致性守卫测试要点（不写完整实现）

新增 `tests/unit/test_skill_description_single_source.py`，至少覆盖 7 组断言。**每条断言都要能「故意制造分叉 ⇒ 变红」**，否则不是守卫。

| # | 守卫对象 | 断言要点 | 故意制造分叉的方式（必须变红） |
|---|---|---|---|
| G-1 | 合并视图不得引入第二份文案 | 在 `tmp_path` 里造「主轨 + 文件轨」双轨技能，断言 `as_legacy_rows()[i]["description"] == skill.md 的 description`；并断言同一个 id 在两条轨上写**不同**描述时，输出仍等于文件轨 | 把主轨描述写成 "A"、文件轨写成 "B" ⇒ 断言输出为 "B"（当前实现会输出 "A"，**测试会红**） |
| G-2 | overlay 不得含死键 / 不得含永不生效键 | 断言 `data/skills_descriptions_overlay.json` 的每个 key 都对应一个真实技能 id；且若该 id 的描述非空，则该 key 属于「结构性永不生效」应当被拒绝 | 往 overlay 加一条 `email-helper` ⇒ 红（当前是死键） |
| G-3 | `_CURATED_DESCRIPTIONS` 与 overlay 不得共存 | 断言二者**不同时非空**（M6 之后 `_CURATED_DESCRIPTIONS` 应被删除；若保留则 overlay 必须为空） | 任一非空 ⇒ 红 |
| G-4 | persona 段 key 必须 ⊆ 技能 id | 断言 `set(_SKILL_PROMPTS) <= set(skills_repo 下的 id)`；并对每个 key 断言当该技能 enabled 时 `_build_skill_instructions()` 的输出**包含**该段首行 | 把某个技能 id 改名 ⇒ 红（当前是**静默不注入、无日志**） |
| G-5 | `BUILTIN_EXTENSIONS["skill"]` 的 id 必须 ⊆ 技能 id | 断言 7 个内置 id 都在 skills_repo 里存在对应 skill.md | 删掉一个 skill 目录 ⇒ 红 |
| G-6 | manifest 的 skill description 必须非空且等于 skill.md | 对 `build_manifest()` 的结果断言 23 条 skill 的 `description` 非空且逐字等于 `skill.md` 的 front matter description | 把 manifest 里的 description 手工改一个字符 ⇒ 红（**注意**：不补 `sync_capability_manifest.py:165` 的话，`--check` 抓不到，这条测试就是唯一防线） |
| G-7 | legacy 快照必须等于合并视图 | 在 `tmp_path` 隔离环境下重建快照，断言 `skills.json` 的每一行 `description` 等于 `as_legacy_rows()` 的对应行 | 只改 skill.md 不重建快照 ⇒ 红（这正是当前 15 处差异的复现） |

**另外两条不写进单测、但要进 CI 的断言**（放在 `.github/workflows/skills-check.yml`，与 `compare_skills_legacy_vs_repo.py` 同组）：

1. **修掉「检查不通过时被当成通过」**：`scripts/compare_skills_legacy_vs_repo.py:63-70` 在文件缺失时打印 SKIP 并 `return {}`，`check()` 走 skip 分支视为 ALL_MATCH。=> 在 **CI 环境下**应把它改成「文件缺失 = 该断言不适用（明确记 PASS-SKIP）」，而在**本地/迁移校验环境**下必须要求文件存在，否则 FAIL。
2. **`data/skills_repo/.index/cache.json` 必须与 skill.md 同源**：断言 23/23 的 `meta.hash` 等于 skill.md 的 md5（本次实测已 23/23 成立）。这条能守住「有人手工编辑了 cache.json」的情况。

---

## 10. 风险清单

按「这次收敛可能破坏什么」排列，每条给触发信号与预案。

| # | 风险 | 为什么会发生 | 触发信号 | 预案 |
|---|---|---|---|---|
| **R1** | **既有 UI 显示的 15 条 pd-* 文案由中文变英文** | M3 改合并规则后，UI 的 `description` 字段值从 S2（中文）变为 S1（英文）。这是**唯一一个会被人直接看见的破坏** | 管理页 15 条技能说明变英文 | **M2 必须先于 M3**：先把中文写进 skill.md 的 `description_zh`，M4 让 UI 优先读 `description_zh` => 切换瞬间文案**逐字不变**。回滚开关 `CP_SKILL_DESC_FROM_FILE_TRACK=0` |
| **R2** | **检索质量下降（如果做错决定）** | 若为了「统一」把 `description` 直接换成中文译文，则技能侧「含典型触发句式」从 **17/23（73.9%）掉到 13/23（56.5%）**（本卡实测）——因为 `Use when …` 正是 pd-* 这批的触发句式载体；同时 BM25/向量对英文技术词（`TDD` / `RED-GREEN-REFACTOR` / `PR`）的命中也会损失 | 技能召回率下降；`Q2 §4.2` 口径的触发句式覆盖下滑 | **裁定：`description` 保留英文原文，中文另存 `description_zh`。** 这条不是风格问题，是检索质量的硬约束 |
| **R3** | **向量索引静默陈旧**（审计基线缺陷；**C1 已修，但残留一条验证义务**） | **基线**（`5c9ace10`）：磁盘上已有的 **8 条**向量在 `native_chroma` 后端下永远不会被 `ensure_indexed()` 刷新（`add` 静默跳过重复 id，adapter 随后把 8 个 id 记入 `_indexed_skill_ids`）。**当前工作区**：C1 已改为「内容哈希判增量 + DELETE-before-INSERT」，缺陷消除（`vector_adapter.py` `:220` / `:636-650` / `:724-741`） | 基线：向量路 `fallback_used=False`、无告警，但 metadata 仍是 2026-07-23 的旧文本。当前：若 G1 改完描述后**未跑一次重编码**，仍会出现同样现象 | 见 §9.5.1（C1 已修，**不要重复实现**）与 §9.5.2 的 V-verify / V-regress / V-guard |
| **R4** | **`data/descriptors.json` 是过期快照，且它喂 7 处消费方** | mtime 实测 `2026-09-17 21:40:56`，比 `skills_mgmt.json` 早 5 天；实测 `pd-writing-skills` 的文案在里面尾缀重复，证明它记录的是**修订前**的版本。消费方：`agent/digestion/*`、`agent/eval/metrics.py:518`、`agent/observability/trace_v2.py:2013`、`agent/repair/locate.py:243`、`agent/memory/forgetting.py:337`、`agent/ui_panels/data.py:425`、`agent/server_routes/routes_ui_panels.py:637`。**它不参与路由**，所以不会影响召回；影响的是治理面板与评估结论 | 治理面板显示的技能说明与 skill.md 不一致；评估口径与事实脱节 | **M8 重跑 `agent/descriptors/backfill.py`**（其输入自述为「主轨 skills_mgmt.json 并 文件轨 skills_repo」，见 `backfill.py:273`），并把「descriptor vs skill.md 逐字比对」纳入 C3 巡检 |
| **R5** | **审计链里的 `descriptor.*` 记录会被推高，且可能被误判为异常** | 实测 `data/audit/audit_chain.db` 共 **72,289** 条，其中 `descriptor.*` **53,612 条（占 74.2%）**：`descriptor.register` 52,882 / `descriptor.patch` 352 / `descriptor.provenance` 338 / `descriptor.stage` 20 / `descriptor.unregister` 20。其中 subject 为 `capability:cp.skill.*` 的有 **927 条**（`self_reflection` 单技能就有 22 条）。M8 重跑回填 = **再追加一批 `descriptor.register`** | 审计链条数在迁移后跳增；结合主报告 P7「`facade.recent()` 全表扫描 ≈0.7s」，读路径成本同步上升 | ① 迁移记录里**显式写明**「本次新增 N 条 `descriptor.register` 属预期」；② 不要试图删除历史（链是 hash 链，只追加，删了会断链——主报告已实测 71,160 条 0 断链）；③ 借此机会把 `descriptor.*` 的高频写入降级（去重后再写），归口到 D1/D3 卡 |
| **R6** | **`sync_capability_manifest.py --check` 对 description 不可见** | `scripts/sync_capability_manifest.py:165` 的 `_diff` 只比对 `_FIELD_SPEC + _SPEC_REQUIRED_FIELDS + ("mark",)`，**不含 `description`**。=> 补完 description 后，任何描述漂移都**不会**让 CI 变红 | skill.md 改了但 manifest 没重跑，CI 全绿 | **必须同时改 `:165`**（§9.4 落点 4），并用 §9.6 的 G-6 单测兜底 |
| **R7** | **legacy 快照与合并视图脱节，且 CI 抓不到** | `store.py:542-571` 的 `sync_to_legacy_skills_json()` 只在主轨写入时触发。若只改 skill.md（git 提交）而不触发主轨写入，两份 `skills.json` 仍是旧文案。而 `compare_skills_legacy_vs_repo.py:63-70` 在文件缺失时 **SKIP 并视同 ALL_MATCH** => 「检查不通过时被当成通过」 | 本地跑 `python scripts/compare_skills_legacy_vs_repo.py` 报 `only_legacy=7 / 字段差异=15`；CI 全绿 | §9.6 的 G-7 单测 + 修 `compare_skills_legacy_vs_repo.py` 的 SKIP 语义（CI 允许 SKIP、迁移校验环境必须 FAIL） |
| **R8** | **persona 段静默消失** | `digital_life_persona.py:432-435` 用 `if sid in enabled_ids` 筛选，**不命中就静默跳过且无日志**。若 G1 阶段给技能改名/改 id（例如把 `self_reflection` 重命名），persona 段会消失而没人知道 | 系统提示词里少了「## 自省反思」等段落；无任何日志/告警 | §9.6 的 G-4；同时把 `_loaded_skill_ids`（`:436` 已存在）落进日志或指标 |
| **R9** | **7 条主轨独有技能被漏掉** | 唯一事实源只覆盖 skills_repo 下的 23 条。`engineering-test-delivery` / `global-core-principles` / `code-observability` / `frontend-state-sync` / `self-explanatory-ui` / `testing-anti-patterns` / `skill` 这 7 条**没有实体**，改读路径对它们无影响（回落主轨），但它们也**永远不进检索与模型上下文** | 清理 `skills_mgmt.json` 时误删这 7 条 ⇒ UI 少 7 行；或误以为它们已被覆盖 | 在方案里**显式声明其为「非事实源域」**；若要纳入，必须先补 `skill.md` 实体并清空主轨 description（第 6 节 风险 4） |
| **R10** | **`SkillSearcher` 的召回行为随 description 语言变化** | `agent/skills_mgmt/searcher.py:41-62` 用 `_WORD_RE.findall(text.lower())` 对 description 分词，并对 description 按 `1.5 * desc_hits / desc_norm` 加权。M3 之后它若改为读英文原文，中文查询的命中会下降、英文查询会上升 | 技能管理页的搜索排序变化 | M4 时让它也读 `description_zh`（或同时对 `description` 与 `description_zh` 计分）；上线前用现成 23 条跑一次人工排序对拍 |
| **R11** | **`email-helper` 死键会在删除后自动重建** | `plugins/skills.py:459-480` 的 `POST /api/skills/describe/auto` 会从 `_CURATED_DESCRIPTIONS` 把 4 条（含 `email-helper`）写回 overlay | overlay 文件删除后又出现 4 条 | **M0 必须先于 M6**：先让该路由 no-op，再删数据 |
| **R12** | **迁移期间的「半收敛」状态** | M2 完成但 M3 未完成时，skill.md 有两份描述（`description` / `description_zh`）而 UI 仍读主轨 => 三处文案并存（S1 / S1的description_zh / S2） | `grep description_zh data/skills_repo/*/skill.md | wc -l` = 15 但 UI 仍未变 | M2 与 M3/M4 应在**同一个变更批次**内完成（可同 PR 分 commit），并在此之前不要动 23 条 `description` 本身 |

### 10.1 优先级建议

| 优先级 | 项 | 理由 |
|---|---|---|
| **P0（必须先做，且与 G1 无关）** | **已由 C1 完成**（内容哈希增量 + DELETE-before-INSERT + 覆盖度自检，见 §9.5.1）。G1-B 只需执行 §9.5.2 的 **V-verify**（改一条描述后验证向量确实重编码）与 **V-regress**（把 chromadb 重复 id 语义固化成测试） | 基线缺陷曾在生效，C1 已消除；留下这两条验证是为了**防止它被改回去**——C1 的 `_drop_native_chroma_ids_locked` 与增量 DELETE 正是本卡实测发现的陷阱的唯一屏障 |
| **P0** | R6（`_diff` 补 `description`） | 一行，补上后所有后续描述改动才有 CI 兜底 |
| **P1** | M0 → M2 → M3 → M4（立源主链） | G1 正卡的前置，且 R1/R12 要求它们同批次 |
| **P1** | M5（CapabilityRegistry 补 description） | 落点已就绪，改动 3 处 + 重跑 sync |
| **P2** | M6（清理 overlay / S5）、M8（回填派生品） | 无内容迁移，纯清理；M6 依赖 M0 |
| **P2** | M9（守卫测试 + CI） | 可与 M5 同期，是防复发的唯一手段 |
| **P3（等 C1 合流）** | M7（向量同步的 V3/V4） | 与 C1 同文件（`vector_adapter.py`），不能并行改 |

---

## 11. 复现脚本原文

### 11.1 脚本 A：全量对账（第 2 到 6 节的数字全部由它产出）

```python
# -*- coding: utf-8 -*-
"""G1-A 只读对账：技能描述「唯一事实源」全量核查（不写任何仓库文件）

运行: python g1a_repro.py
依赖: PyYAML（仓库已用）、sqlite3（标准库）
输出: §2 存储清册 / §3 一致性矩阵 / §4 十五组冲突 / §5 八个单侧技能 / §6 主轨独有
"""
import os, re, sys, json, sqlite3, yaml

sys.stdout.reconfigure(encoding="utf-8")
ROOT = r"C:\Users\Administrator\agent"
D = os.path.join(ROOT, "data")

def jload(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def fm_of(sid):
    """严格按 YAML 解析 front matter（勿用 ^description: 单行正则，会漏折行标量）"""
    txt = open(os.path.join(D, "skills_repo", sid, "skill.md"), encoding="utf-8").read()
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", txt, re.S)
    return (yaml.safe_load(m.group(1)) or {}) if m else {}

# ── 清册来源 ──────────────────────────────────────────────────────────
MANIFEST = jload(os.path.join(D, "capability_manifest.json"))
MIDS = [e["tool_name"] for e in MANIFEST["entries"] if e.get("kind") == "skill"]
DIRS = sorted(d for d in os.listdir(os.path.join(D, "skills_repo"))
              if os.path.isfile(os.path.join(D, "skills_repo", d, "skill.md")))
FMS  = {s: fm_of(s) for s in DIRS}                       # S1
MGMT = jload(os.path.join(D, "skills_mgmt.json"))        # S2
LEG  = {e["id"]: e.get("description") for e in jload(os.path.join(D, "skills.json"))["skills"]}   # S3
OVL  = jload(os.path.join(D, "skills_descriptions_overlay.json"))                                # S4
DES  = {k.replace("cp.skill.", "", 1): ((v.get("capability") or {}).get("description") or "")
        for k, v in jload(os.path.join(D, "descriptors.json"))["descriptors"].items()
        if (v.get("origin") or {}).get("source_type") == "skill"}                                # S8
IC   = jload(os.path.join(D, "skills_repo", ".index", "cache.json"))                              # S9

# S5 plugins/skills.py::_CURATED_DESCRIPTIONS（静态抽取，避免导入 Flask）
src5 = open(os.path.join(ROOT, "plugins", "skills.py"), encoding="utf-8").read()
blk5 = re.search(r"_CURATED_DESCRIPTIONS\s*=\s*\{(.*?)\n\}", src5, re.S).group(1)
CUR  = {m.group(1): m.group(2) for m in re.finditer(r'"([^"]+)"\s*:\s*"((?:[^"\\]|\\.)*)"', blk5)}
# S7 agent/extensions/base.py::BUILTIN_EXTENSIONS["skill"]
src7 = open(os.path.join(ROOT, "agent", "extensions", "base.py"), encoding="utf-8").read()
blk7 = re.search(r'"skill":\s*\[(.*?)\n    \],', src7, re.S).group(1)
BUI  = {m.group(1): m.group(2) for m in
        re.finditer(r'"id":\s*"([^"]+)".*?"description":\s*"((?:[^"\\]|\\.)*)"', blk7, re.S)}
# S6 agent/digital_life_persona.py::_SKILL_PROMPTS
src6 = open(os.path.join(ROOT, "agent", "digital_life_persona.py"), encoding="utf-8").read()
blk6 = re.search(r"_SKILL_PROMPTS\s*=\s*\{(.*?)\n    \}", src6, re.S).group(1)
PER  = {m.group(1): "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2)))
        for m in re.finditer(r'"([a-z_\-]+)"\s*:\s*((?:"(?:[^"\\]|\\.)*"\s*(?:\+\s*)?)+)', blk6)}

print("== 清册 ==")
print("S1 skill.md            :", len(DIRS))
print("S2 skills_mgmt.json    :", len(MGMT))
print("S3 data/skills.json    :", len(LEG), "(镜像 agent/data/skills.json 同内容)")
print("S4 overlay             :", len(OVL), list(OVL))
print("S5 _CURATED_DESCRIPTIONS:", len(CUR), list(CUR))
print("S6 _SKILL_PROMPTS      :", len(PER), list(PER))
print("S7 BUILTIN_EXTENSIONS  :", len(BUI), list(BUI))
print("S8 descriptors.json    :", len(DES))
print("S9 .index/cache.json   :", len(IC["skills"]))

print()
print("== S1 vs S2 : 双侧技能对比 ==")
both = [s for s in DIRS if MGMT.get(s, {}).get("description")]
print("双侧:", len(both), " 描述不同:", sum(1 for s in both if FMS[s].get("description") != MGMT[s]["description"]))
print("S1 独有:", sorted(set(DIRS) - set(MGMT)))
print("S2 独有:", sorted(set(MGMT) - set(DIRS)))
print("23 ∪ 22 是否等于 legacy 30 :", (set(DIRS) | set(MGMT)) == set(LEG), len(set(DIRS) | set(MGMT)))

print()
print("== 缓存与向量校验 ==")
import hashlib
ok_h = sum(1 for s in DIRS
           if hashlib.md5(open(os.path.join(D, "skills_repo", s, "skill.md"), "rb").read()).hexdigest()
           == (IC["meta"].get(s) or {}).get("hash"))
ok_d = sum(1 for s in DIRS if IC["skills"][s].get("description") == FMS[s].get("description"))
print("S9 hash 命中:", ok_h, "/", len(DIRS), " S9 description == S1:", ok_d, "/", len(DIRS))

cp = os.path.join(D, "skill_vectors", "native_chroma", "chroma.sqlite3")
con = sqlite3.connect("file:" + cp.replace(os.sep, "/") + "?mode=ro", uri=True)
cur = con.cursor()
cur.execute("select id,key,string_value from embedding_metadata where key in ('skill_id','description')")
acc = {}
for i, k, v in cur.fetchall():
    acc.setdefault(i, {})[k] = v
vec = {m["skill_id"]: m.get("description") for m in acc.values()}
con.close()
print("chroma 向量条数:", len(vec), " 覆盖 ids:", sorted(vec))
print("chroma description == S1 的条数:", sum(1 for k, v in vec.items() if FMS.get(k, {}).get("description") == v))
```

**预期输出（本次实测，逐字）**：

```text
== 清册 ==
S1 skill.md            : 23
S2 skills_mgmt.json    : 22
S3 data/skills.json    : 30 (镜像 agent/data/skills.json 同内容)
S4 overlay             : 4 ['self_reflection', 'email-helper', 'memory_summary', 'scripted-selftest']
S5 _CURATED_DESCRIPTIONS: 4 ['self_reflection', 'email-helper', 'memory_summary', 'scripted-selftest']
S6 _SKILL_PROMPTS      : 7 ['self_reflection', 'memory_summary', 'emotion_expression', 'proactive_suggestion', 'context_aware', 'safety_guard', 'voice_interaction']
S7 BUILTIN_EXTENSIONS  : 7 ['self_reflection', 'memory_summary', 'emotion_expression', 'proactive_suggestion', 'context_aware', 'safety_guard', 'voice_interaction']
S8 descriptors.json    : 29
S9 .index/cache.json   : 23

== S1 vs S2 : 双侧技能对比 ==
双侧: 15  描述不同: 15
S1 独有: ['context_aware', 'emotion_expression', 'memory_summary', 'proactive_suggestion', 'safety_guard', 'scripted-selftest', 'self_reflection', 'voice_interaction']
S2 独有: ['code-observability', 'engineering-test-delivery', 'frontend-state-sync', 'global-core-principles', 'self-explanatory-ui', 'skill', 'testing-anti-patterns']
23 并 22 是否等于 legacy 30 : True 30

== 缓存与向量校验 ==
S9 hash 命中: 23 / 23  S9 description == S1: 23 / 23
chroma 向量条数: 8  覆盖 ids: ['context_aware', 'emotion_expression', 'memory_summary', 'proactive_suggestion', 'safety_guard', 'scripted-selftest', 'self_reflection', 'voice_interaction']
chroma description == S1 的条数: 8
```

### 11.2 脚本 B：legacy 双镜像与 git 跟踪状态（第 8.2 节）

```powershell
# B1 两份 legacy 是否字节相同
python -c "import hashlib;a=open(r'data/skills.json','rb').read();b=open(r'agent/data/skills.json','rb').read();print(hashlib.md5(a).hexdigest(), hashlib.md5(b).hexdigest(), a==b)"
# 预期: 293ecbe151c91c167773abe745fc690c 293ecbe151c91c167773abe745fc690c True

# B2 谁被 git 跟踪 / 谁被忽略
git ls-files --error-unmatch data/skills_repo/self_reflection/skill.md data/skills_descriptions_overlay.json
git check-ignore -v data/skills_mgmt.json data/skills.json agent/data/skills.json data/descriptors.json data/skills_repo/.index/cache.json data/skill_vectors/native_chroma/chroma.sqlite3
# 预期: .gitignore:224 / :148 / :506 / :226 / :250 / :211
```

### 11.3 脚本 C：chromadb 重复 id 语义实测（第 9.5 节 R3 的证据）

> 只写系统临时目录（`tempfile.mkdtemp`），**不碰仓库任何文件**，跑完自清。

```python
# -*- coding: utf-8 -*-
import sys, os, tempfile, shutil, logging
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
import chromadb
print("chromadb version:", chromadb.__version__)
from chromadb.config import Settings
tmp = tempfile.mkdtemp(prefix="g1a_chroma2_")
path = os.path.join(tmp, "native_chroma"); os.makedirs(path, exist_ok=True)
c1 = chromadb.PersistentClient(path=path, settings=Settings(anonymized_telemetry=False))
col = c1.get_or_create_collection(name="skill_metadata", metadata={"description":"t"})
col.add(ids=["a","b"], documents=["OLD-a","OLD-b"], metadatas=[{"description":"OLD-1"},{"description":"OLD-2"}])
print("step1 count:", col.count(), "docs:", col.get(ids=["a","b"])["documents"])

c2 = chromadb.PersistentClient(path=path, settings=Settings(anonymized_telemetry=False))
col2 = c2.get_or_create_collection(name="skill_metadata", metadata={"description":"t"})
try:
    col2.add(ids=["a","b","c"], documents=["NEW-a","NEW-b","NEW-c"],
             metadatas=[{"description":"NEW-1"},{"description":"NEW-2"},{"description":"NEW-3"}])
    print("step2 add -> no exception")
except Exception as e:
    print("step2 add RAISED:", type(e).__name__, str(e)[:300])
print("step2 count:", col2.count())
g = col2.get(ids=["a","b","c"])
print("step2 ids   :", sorted(g["ids"]))
print("step2 docs  :", dict(zip(g["ids"], g["documents"])))
print("step2 metas :", dict(zip(g["ids"], [m.get("description") for m in g["metadatas"]])))
print()
print(">>> 结论判定：add 对已存在 id 是否覆盖？",
      "覆盖" if dict(zip(g["ids"], g["documents"])).get("a")=="NEW-a" else "不覆盖（静默跳过）")
shutil.rmtree(tmp, ignore_errors=True)
```

**实测输出（chromadb 1.5.9）**：

```text
chromadb version: 1.5.9
step1 count: 2 docs: ['OLD-a', 'OLD-b']
step2 add -> no exception
step2 count: 3
step2 ids   : ['a', 'b', 'c']
step2 docs  : {'a': 'OLD-a', 'b': 'OLD-b', 'c': 'NEW-c'}
step2 metas : {'a': 'OLD-1', 'b': 'OLD-2', 'c': 'NEW-3'}
>>> 结论判定：add 对已存在 id 是否覆盖？ 不覆盖（静默跳过）
```

### 11.4 脚本 D：审计链 `descriptor.*` 统计（第 10 节 R5）

```python
import sqlite3, os
p = os.path.join('data','audit','audit_chain.db')
con = sqlite3.connect('file:' + p.replace(os.sep,'/') + '?mode=ro', uri=True)
cur = con.cursor()
cur.execute('select count(*) from audit_chain')
print('total:', cur.fetchone()[0])                       # 72289
cur.execute("select action,count(*) from audit_chain where action like 'descriptor.%' group by action order by 2 desc")
print(cur.fetchall())
# [('descriptor.register', 52882), ('descriptor.patch', 352), ('descriptor.provenance', 338),
#  ('descriptor.stage', 20), ('descriptor.unregister', 20)]
cur.execute("select count(*) from audit_chain where subject like 'capability:cp.skill.%'")
print('skill-subject:', cur.fetchone()[0])               # 927
```

### 11.5 脚本 E：CapabilityRegistry 的 description 缺口实测（第 9.4 节）

> 进程内构建只读派生视图，**不启动服务**。

```python
import sys, os, json
sys.stdout.reconfigure(encoding="utf-8")
root = r"C:\Users\Administrator\agent"
sys.path.insert(0, root)
os.chdir(root)
from agent.capregistry.view import build_registry
reg = build_registry()
items = reg.list_envelope(limit=0)["data"]["items"]
print("items:", len(items))
sk = [i for i in items if i.get("kind")=="skill"]
tl = [i for i in items if i.get("kind")!="skill"]
print("skills:", len(sk), "| skills with non-empty description:", sum(1 for i in sk if (i.get("description") or "").strip()))
print("tools :", len(tl), "| tools  with non-empty description:", sum(1 for i in tl if (i.get("description") or "").strip()))
print()
print("sample skill item keys:", list(sk[0].keys()))
print("sample skill description:", repr(sk[0].get("description")))
print("sample tool  description:", repr(tl[0].get("description"))[:160])
```

**实测输出**：

```text
items: 114
skills: 23 | skills with non-empty description: 0
tools : 91 | tools  with non-empty description: 91
sample skill description: ''
```

### 11.6 脚本 F：触发句式覆盖（第 10 节 R2 的量化依据）

```python
import re, json, os, yaml
TRIG = re.compile('适用于|适用场景|使用场景|用于|用来|当[^，。；]{0,14}时|时使用|时调用|Use when|'
                  'Use this|使用本|调用本|触发条件|调用时机|在[^，。；]{0,12}之前|场景')
D = 'data'
def fmd(sid):
    txt = open(os.path.join(D,'skills_repo',sid,'skill.md'), encoding='utf-8').read()
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', txt, re.S)
    return (yaml.safe_load(m.group(1)) or {}).get('description') or ''
ids = [d for d in os.listdir(os.path.join(D,'skills_repo'))
       if os.path.isfile(os.path.join(D,'skills_repo',d,'skill.md'))]
mgmt = json.load(open(os.path.join(D,'skills_mgmt.json'), encoding='utf-8'))
cur  = sum(1 for s in ids if TRIG.search(fmd(s)))
alt  = sum(1 for s in ids if TRIG.search((mgmt.get(s) or {}).get('description') or fmd(s)))
print('现状(全用 skill.md):   %d/23 = %.1f%%' % (cur, cur/23*100))   # 17/23 = 73.9%
print('若改用中文译文:        %d/23 = %.1f%%' % (alt, alt/23*100))   # 13/23 = 56.5%
```

---

## 12. 对审计报告的勘误与差异

| # | 审计口径（E11 / T7 / Q2 §5） | 本次实测 | 性质 |
|---|---|---|---|
| 1 | 「技能描述有 **8 处**存储」 | **10 处**：新增 `plugins/skills.py:116-121` 的 `_CURATED_DESCRIPTIONS`、`agent/extensions/base.py:98-142` 的 `BUILTIN_EXTENSIONS["skill"]`、`agent/data/skills.json` 镜像 | **补漏**（审计漏了 3 处） |
| 2 | 「`self_reflection` 在 skill.md / overlay / persona 内联里有**三套文案**」 | **四套**：还有 `BUILTIN_EXTENSIONS["skill"][self_reflection].description = "每次交互后自动反思自身状态，不断成长"`（15 字）。`memory_summary` 同样四套 | **补漏** |
| 3 | T7「`CapabilityRegistry` 的 23 条 skill 条目**根本没有 description 字段**」 | **半对**：`data/capability_manifest.json` 里确实 0 条含该键；但 **HTTP 信封（`GET /capabilities/tools`）里该键存在**，值为空串（`to_dict()` 在 `spec.py:229` 恒输出该键）。实测 114 条：91 工具非空 / 23 技能空 | **表述需修正**（结论不变：「技能描述恒为空」成立） |
| 4 | Q2 §5.3「`ensure_indexed()` 对 8 个重复 id 调 `add` **触发异常**，被 `:414-415` 吞掉 ⇒ 新增/变更的描述可能永远进不了向量索引」【推测】 | **证伪其机制，证实其后果且更严重**：chromadb 1.5.9 的 `add` 对已存在 id **不抛异常、静默跳过、不覆盖**；随后 `:412-413` 仍把全部 id 记入 `_indexed_skill_ids` ⇒ **那 8 条在整个进程生命周期内永不刷新**（连 `ensure_indexed()` 也救不回来） | **从【推测】升级为实测结论；风险升级** |
| 5 | Q2 §5.1 表格「① `data/skills_repo/<id>/skill.md` front matter `description`（`:4`）｜23」 | 条数正确，但**文本内容用 `^description:` 单行正则抽取会漏折行标量**。技能侧真实统计：**mean 123.2 / median 103 / min 17 / max 246**（Q2 报 mean 65.4 / median 70 / max 89）；15 条被低估，其中 `pd-frontend-design` 的 `Use this skill when…` 落在第 5 行 | **数值勘误** |
| 6 | Q2 §4.2「技能 含典型触发句式 **16（69.6%）**」 | 用完整 YAML 解析得 **17/23（73.9%）**。差异技能只有 `pd-frontend-design-77ea5c4e-skill` 一个 | **数值勘误**（结论方向不变） |
| 7 | 「15/23 技能同时存在于 ① 与 ②，且 15/15 描述不同」 | **完全成立**，且进一步查明：15 组里 **14 组是「英→中互译」**（相似度 0.077–0.222），1 组（`pd-writing-skills`）相似度 0.913 仅标点与句序差异 | **成立 + 归因细化** |
| 8 | E11「改 `skill.md` 只改检索（UI 不变）」 | **成立但不完整**：改 `skill.md` 同时改了 **模型可见的描述**（`context_injector.py:318` 读的就是它），不只是检索。UI 确实不变 | **表述需补全为「检索 + 模型可见」** |

---

## 13. 本报告的局限

1. **未启动服务。** 所有结论来自静态读取、进程内只读构建（CapabilityRegistry）与离线 SQLite 查询。`GET /capabilities/tools` 的 114 条是用 `build_registry().list_envelope()` **进程内**得到的，未经过 HTTP 层；但路由层（`routes_capabilities.py:162-205`）只做分页与租户过滤，不增删 `items` 字段，故结论等价。
2. **未跑 pytest。** §9.6 的守卫测试只给要点，未实现、未验证。
3. **`SkillSearcher` 的新旧行为差异未实测**（风险 R10 标为待实测）。
4. **`vector_adapter` 的 `native_chroma` 路径未在真实仓库上跑通验证**。§9.5 关于**基线**的 R3 结论由「chromadb 1.5.9 的 `add` 语义实测」+「基线 `vector_adapter.py` 代码路径静态阅读」两部分合成；**两部分的衔接（步骤 1 到 5）仍属推断**，虽然每一步都有代码行号支撑。要彻底闭合，需要在服务进程里观察一次 `ensure_indexed()` 日志（本次因「不启动服务」的约束未做）。**标注为：机制实测，端到端推断。** 另：**该基线缺陷已由 C1 修复**（§9.5.1），C1 的实现本身**本卡未运行验证**（它属于 C1 的验收范围）。
5. **`data/skills_classes.json`（7,199 B，mtime 2026-09-23 13:30）未纳入本卡范围。** 它承载技能分类而非描述，但 `agent/lines/callability.py:527` 记录了它与 `email-helper` 等孤儿 id 的关系，收敛时应一并核对。
6. **`agent/data/extensions.json` 等扩展存储未测。** `plugins/skills.py:167-191` 会把 `ExtensionStore` 的条目录进 `/api/skills` 列表，那些条目的 description 来源本卡未追。
7. **本卡全程只读**：未修改仓库任何既有文件；只在 `%TEMP%` 下写了 6 个探针脚本（仓库外），并新建本报告一个文件。未执行任何 git 写操作，未跑全量 pytest，未启动服务。
---

## 14. 附：并发变更实测记录（本卡执行期间的仓库状态）

### 14.1 `git status --porcelain` 实测

```text
 M agent/audit/chain.py
 M agent/audit/facade.py
 M agent/digital_life_persona.py
 M agent/model_router/adapters.py
 M agent/server_port_guard.py
 M agent/skills_mgmt/enhancer.py
 M agent/skills_mgmt/index_cache.py
 M agent/skills_mgmt/registry.py
 M agent/skills_mgmt/vector_adapter.py
 M agent/tools_prompt_guard.py
 M app_server.py
 M plugins/chat.py
 M start_yunshu.bat
?? _tmp_rootcause_probe/evidence/probeC_C1_BEFORE_raw_notools_1790331133.json
?? _tmp_rootcause_probe/evidence/probeC_C2_AFTER_aligned_notools_1790331135.json
?? _tmp_rootcause_probe/evidence/probeC_C3_POSITIVE_raw_with_tools_1790331138.json
?? docs/audit_skill_governance/
?? scripts/verify_index_drift.py
?? scripts/watchdog_yunshu.py
?? tests/unit/test_skill_registry_audit.py
?? tests/unit/test_startup_no_gap.py
?? tests/unit/test_tool_count_consistency.py
```

`HEAD` 仍为审计基线 `5c9ace10 Merge pull request #978 from nzt47/fix/l7-misleading-comments`；仓库里另有 5 个 stash（`git stash list`），**本卡未创建、未使用、未删除任何 stash**。

### 14.2 `git diff --stat`（本卡分析对象中受影响的部分）

```text
 agent/digital_life_persona.py       |  48 +--
 agent/skills_mgmt/enhancer.py       |  92 +++++-
 agent/skills_mgmt/index_cache.py    | 243 +++++++++++++-
 agent/skills_mgmt/registry.py       |  66 +++-
 agent/skills_mgmt/vector_adapter.py | 612 ++++++++++++++++++++++++++++++++----
 5 files changed, 960 insertions(+), 101 deletions(-)
```

### 14.3 逐条影响判定

| 本报告的结论 | 受影响吗 | 依据 |
|---|---|---|
| 10 处存储清册（第 2 节） | **否** | 读 `data/` 下数据文件与未被修改的 4 个 .py（`callability.py` / `spec.py` / `plugins/skills.py` / `sync_capability_manifest.py`） |
| 15/15 描述分歧（第 4 节） | **否** | `data/skills_repo/*/skill.md` 与 `data/skills_mgmt.json` 均未在 `git status` 中出现，且数据文件 mtime 未变 |
| 8 个单侧技能 / 7 个主轨独有技能（第 5、6 节） | **否** | 同上 |
| CapabilityRegistry 补 description 落点（第 9.4 节） | **否** | `agent/lines/callability.py`、`agent/capregistry/spec.py`、`agent/capregistry/view.py`、`scripts/sync_capability_manifest.py` **均未被修改** |
| 合并规则改法（第 9.3 节） | **逻辑不受影响，行号漂移** | `registry.py` 被 D2 改了 +66 行（启停审计），`as_legacy_rows` 主体逐字未变 |
| 向量同步（第 9.5 节） | **重大影响——已改写** | C1 改了 `vector_adapter.py` +612 行，基线缺陷已消除；第 9.5 节已重写为「基线缺陷 + C1 已修 + G1-B 的剩余三件事」 |
| 风险 R3 / 优先级 P0 行（第 10 节） | **已改写** | 同上 |
| `digital_life_persona.py` 的 persona 段引用（第 8.3 节） | **结论不受影响，行号需重核** | 该文件被改了 -48 行；`_SKILL_PROMPTS` 的存在性与 7 个 key 未变（本卡抽取脚本在改动前后均读到同一组 7 个 id），但 `42-62` / `415-439` 的行号应重核 |
| 审计链 `descriptor.*` 统计（第 10 节 R5） | **否** | `data/audit/audit_chain.db` 为只读查询，mtime 未变 |

### 14.4 G1-B 开工前的重新对账清单（4 条命令）

```bash
# ① 重核行号：本报告引用的每个落点在当前工作区的位置
grep -n "def as_legacy_rows" agent/skills_mgmt/registry.py
grep -n "def ensure_indexed\|def _drop_native_chroma_ids_locked\|def _remove_skill_vector\|_indexed_content_hash" agent/skills_mgmt/vector_adapter.py
grep -n "_SKILL_PROMPTS\|def _build_skill_instructions" agent/digital_life_persona.py

# ② 重跑第 11 节脚本 A，确认数据类结论未变（预期与第 11.1 节逐字相同）
python g1a_repro.py

# ③ 重跑第 11 节脚本 E，确认 capability 缺口仍为 0/23
python g1a_capreg.py

# ④ 确认 `as_legacy_rows` 的「主轨先占位」仍然存在（未被任何卡顺手改掉）
python -c "import inspect,agent.skills_mgmt.registry as r;print(inspect.getsource(r.SkillRegistry.as_legacy_rows))"
```

### 14.5 本卡的副作用声明

| 项 | 说明 |
|---|---|
| 仓库内**新建**文件 | 仅本报告 `docs/audit_skill_governance/G1A_reconciliation.md` 一个 |
| 仓库内**修改**的既有文件 | **0 个**（第 14.1 节的 `M` 条目**全部由其他任务卡产生**，本卡未触碰） |
| 仓库外写入 | `%TEMP%` 下 7 个只读探针脚本 + 7 个中间 markdown 分片（全部在仓库之外） |
| 进程 | 未启动任何服务；只跑了离线 Python 脚本（其中脚本 E 在进程内构建只读 CapabilityRegistry，脚本 C 在 `%TEMP%` 建临时 chroma 集合后自清） |
| 测试 | 未跑 pytest（含未跑全量） |
| git | 未执行任何写操作（无 commit / push / checkout / stash / add）；`HEAD` 仍为 `5c9ace10` |
| 密钥 | 全程未读取、未输出 `.env` 中的任何密钥值；未访问对外网络（未调用任何 LLM API） |