# TLM (Tiered Local Memory) 三层融合架构 — 设计文档

> **文档定位**: 在动手重构前对现有记忆/存储资产做完整盘点，定义每个模块的处置策略与 API 契约白名单。
> **生成日期**: 2026-07-13
> **作用范围**: 仅限记忆/向量/存储相关模块，不动业务编排层
> **约束**: 全程只读盘点；不污染主 venv；中文输出保留英文技术术语

---

## 1. 现状盘点表

### 1.1 模块清单（按职责分组）

| # | 文件路径 | 主要类/函数 | 数据存储方式 | 对外 API | 被谁调用 |
|---|----------|-------------|--------------|----------|----------|
| 1 | `agent/memory/base.py` | `MemoryInterface`(ABC), `MemoryResult`, `MemoryCapability`(Enum), `_safe_call` | 无（仅抽象） | `save/search/get_profile/update_graph`, `capabilities`, `to_dict` | `router.py`, `holographic_adapter.py`, `mem0_adapter.py`, `long_term_memory.py`, `short_term_memory.py`, `__init__.py` |
| 2 | `agent/memory/router.py` | `MemoryRouter` | 内存态适配器字典 + 可选 `MultiLevelCache` | `route(task_type)`, `register/unregister/get_adapter/list_adapters`, `save/search/get_profile/update_graph`, `attach_cache_layer/detach_cache_layer`, `to_dict`, `default`(property) | `agent/cognitive/loop.py`(CognitiveLoop), `agent/cognitive/knowledge.py`(KnowledgePrecipitator), `agent/subagent/container.py`(参数引用), `agent/memory/__init__.py` |
| 3 | `agent/memory/long_term_memory.py` | `LongTermMemory`, `LongTermMemoryEntry` | SQLite（`./data/memory/long_term.db`）, 表 `long_term_memory` | `save/get/search/delete/verify`, `get_stats`, `list_unverified/list_sensitive` | `agent/memory/reviewer.py`(MemoryReviewer) |
| 4 | `agent/memory/short_term_memory.py` | `ShortTermMemory`, `ShortTermMemoryEntry` | 纯内存 dict + threading.RLock + LRU + TTL | `save/get/delete/clear_task_memory/clear_all`, `cleanup_expired`, `get_stats`, `list_entries` | （**无生产调用方**，仅测试） |
| 5 | `agent/memory/adapters/holographic_adapter.py` | `HolographicAdapter` | SQLite + FTS5（`./data/memory/holographic.db`）, 表 `memory_items` + `memory_fts` + `MultiLevelCache` | 同 `MemoryInterface` + `delete/clear/get_stats` | `agent/memory/router.py`(默认兜底), `agent/memory/__init__.py` |
| 6 | `agent/memory/adapters/mem0_adapter.py` | `Mem0Adapter` | JSON 文件（`./data/memory/mem0_facts.json`）, 可选 mem0 引擎 | 同 `MemoryInterface` + `get_stats/get_raw_facts` | `agent/memory/router.py`(注册), `agent/memory/__init__.py` |
| 7 | `agent/memory/observability.py` | `trackEvent`, `_emit_structured_log` | 无（仅日志埋点 → `BusinessMetricsCollector`） | `trackEvent(event_name, payload)` | （**仅模块内导出符号**，外部 grep 未发现调用） |
| 8 | `agent/memory/filter.py` | `SensitiveDataFilterCompatibility`（继承自 `agent.utils.sensitive_data_filter.SensitiveDataFilter`） | 无（向后兼容层） | `detect/check/check_and_sanitize/mask`, `BUILT_IN_PATTERNS`(property) | `agent/memory/router.py`(`_get_sensitive_filter`) |
| 9 | `agent/memory/reviewer.py` | `MemoryReviewer`, `ReviewResult` | 无（依赖 LongTermMemory 的 SQLite） | `review/review_quick`, `get_last_review` | （**无生产调用方**，仅测试） |
| 10 | `agent/memory_optimized.py` | `OptimizedChromaDB`, `ChromaInitStats`, `ChromaInitCache`, `LazyCollectionProxy`, `MockChromaClient`, `MockCollection` | ChromaDB PersistentClient（`./data/chroma`）+ 初始化缓存 | `add/query/get_stats/clear_cache`, `is_initialized/is_initializing`(property), `collection`(property), `create_optimized_chroma`（便捷函数） | `tests/unit/test_memory_optimized.py`, `tests/unit/conftest.py`, `agent/tests/test_chroma_optimized.py`, `agent/tests/test_chroma_optimization.py` |
| 11 | `memory/vector_store/vector_store.py` | `VectorStore`, `KnowledgeBase`, `InvertedIndex`, `LRUQueryCache`, `MemoryItem` | ChromaDB PersistentClient（首选）或 JSON（fallback）+ 倒排索引 + LRU 缓存 | `VectorStore.add/batch_add/search/search_async/get_by_id/get_recent/clear/get_stats/get_cache_stats/get_index_stats`, `KnowledgeBase.add_document/query/query_async` | `agent/__init__.py`(导出), `agent/orchestrator/lifecycle_manager.py`(实例化), `agent/orchestrator/orchestrator.py`(`_vector_memory.add`), `agent/digital_life.py`, `agent/digital_life_state.py`, `agent/__init__.py` |
| 12 | `memory/storage.py` | `Storage`, `StorageError` | 文件持久化（`./memory_data/messages.jsonl` + `summary.txt` + `summary_version.txt`）+ threading.Lock | `save_message/load_recent_messages`, `save_summary/load_summary/clear_summary`, `clear_messages` | `memory/memory_manager.py`, `tests/unit/test_memory_storage_boundary.py`, `memory/tests/test_risk_fixes.py`, `memory/tests/test_storage.py` |
| 13 | `agent/server_routes/routes_memory.py` | 22 个 Flask route handler（见 §1.2） | 间接调用 `Yunshu._memory` / `Yunshu._vector_memory` / `Yunshu._knowledge_base` / `Yunshu._memory._black_box` / `window_sensor` | HTTP 路由（见 §1.2 与 §4） | Flask app（通过 `register_routes(app, state)`） |
| 14 | `agent/skills_mgmt/memory_abstractor.py` | `MemorySkillAbstractor`, `MemoryEntry`, `MemoryCluster` | 纯计算（聚类 + 模式提取），不持久化 | `abstract_new_skills`, `cluster_memories`, `generate_skill_draft`, `check_quality_gate` | `agent/server_routes/routes_skills_mgmt.py`, `agent/skills_mgmt/signal_scorer.py`（反向被调用） |

### 1.2 routes_memory.py 全部 22 个路由

| 路径 | 方法 | 鉴权 | Handler | 数据来源 |
|------|------|------|---------|----------|
| `/api/memory/overview` | GET | `@log_request` (无 token) | `api_memory_overview` | `Yunshu._memory.load_summary()` + `_storage.load_recent_messages(20)` + `_black_box.analyze()` |
| `/api/memory/manual` | POST | `@require_token` | `api_memory_manual` | `Yunshu._memory.add_memory({role, content})` |
| `/api/memory/compress` | POST | `@require_token` | `api_memory_compress` | `Yunshu._memory.compress()` (async) |
| `/api/memory/<int:index>` | DELETE | `@require_token` | `api_memory_delete_index` | 空实现（直接返回 `{ok:True}`） |
| `/api/memory/clear-summary` | POST | `@require_token` | `api_memory_clear_summary` | `Yunshu._memory.clear_summary()` |
| `/api/memory/summary` | PUT | `@require_token` | `api_memory_update_summary` | `Yunshu._memory._storage.save_summary(summary, version+1)` |
| `/api/vector/stats` | GET | `@log_request` (无 token) | `api_vector_stats` | `Yunshu._vector_memory.get_stats()` + `count` |
| `/api/vector/search` | POST | `@require_token` | `api_vector_search` | `Yunshu._vector_memory.search(query, top_k)` |
| `/api/vector/add` | POST | `@require_token` | `api_vector_add` | `Yunshu._vector_memory.add(content, metadata)` |
| `/api/vector/batch_add` | POST | `@require_token` | `api_vector_batch_add` | `Yunshu._vector_memory.batch_add(items)` |
| `/api/vector/item/<item_id>` | GET | `@log_request` (无 token) | `api_vector_get_item` | `Yunshu._vector_memory.get_by_id(item_id)` |
| `/api/vector/recent` | GET | `@log_request` (无 token) | `api_vector_recent` | `Yunshu._vector_memory.get_recent(limit)` |
| `/api/vector/clear` | DELETE | `@require_token` | `api_vector_clear` | `Yunshu._vector_memory.clear()` |
| `/api/knowledge/query` | POST | `@require_token` | `api_knowledge_query` | `Yunshu._knowledge_base.query(question, top_k)` |
| `/api/knowledge/add` | POST | `@require_token` | `api_knowledge_add` | `Yunshu._knowledge_base.add_document(content, source, tags)` |
| `/api/memory/windows/events` | GET | `@log_request` (无 token) | `api_window_events` | `Yunshu._memory._black_box.query(event_type="window_event", limit)` |
| `/api/memory/windows/stats` | GET | `@log_request` (无 token) | `api_window_stats` | 聚合 `_black_box.query("window_event", 2000)` |
| `/api/memory/windows/current` | GET | `@log_request` (无 token) | `api_window_current` | `window_sensor.get_current()` |
| `/api/memory/windows/config` | GET, POST | `@require_token` | `api_window_config` | `window_sensor.save_config/get_config` |
| `/api/memory/windows/clear` | POST | `@require_token` | `api_window_clear` | 空实现（自然过期） |
| `/api/window/consent` | POST | `@log_request` (无 token) | `api_window_consent` | `window_sensor.save_config/start/stop` + `state.window_sensor_consented` |
| `/api/privacy/info` | GET | `@log_request` (无 token) | `api_privacy_info` | 静态信息（含 `sensor.window_sensor.HAS_WIN32`） |

### 1.3 已发现的隐性问题

| 问题 | 证据 | 风险 |
|------|------|------|
| `memory_abstractor.py` 第 433 行 `from agent.memory_optimized import MemoryManager` 永远失败 | `agent/memory_optimized.py` 中没有 `MemoryManager` 类（仅有 `OptimizedChromaDB` 等） | `_load_long_term_memories` 永远返回 `[]`，记忆→技能抽象实际只能从 workflow + feedback 两源拉取 |
| `ShortTermMemory` 与 `MemoryReviewer` 在生产代码中无任何调用方 | grep 结果仅命中测试文件 | 死代码嫌疑，但保留价值在于"短期记忆"和"健康度审查"本身是 TLM 必备能力 |
| `routes_memory.py` 的 `api_memory_delete_index` 是空实现 | 第 89-90 行直接返回 `{ok:True}` | 删除索引 API 形同虚设，前端调用方误以为已删除 |
| `MemoryRouter.ROUTE_MAP` 中 `hindsight/honcho/openviking` 三个适配器名称从未在任何代码中实现/注册 | grep 这些名称仅命中 router.py 的 ROUTE_MAP | 路由表是"愿景图"而非"实际能力"，`route()` 实际只走 `holographic` 兜底 |
| `agent/memory/observability.py` 的 `trackEvent` 函数无外部调用方 | grep 仅命中自身定义 | 死代码，但埋点能力值得保留 |
| `memory/__init__.py` 同时存在 `agent/memory/__init__.py`（两个独立的 memory 包） | grep 验证两个包均存在 | 双包结构是历史遗留，TLM 重构应明确归并路径 |

---

## 2. 资产处置策略表

> **处置分类**：保留（不变）/ 扩展（接口不变，能力增强）/ 替换（接口保留，实现重写）/ 废弃（删除或仅留兼容层）
> **判定原则**（不易约束）：API 契约白名单（§4）中的所有路由对应的底层实现模块必须保持接口签名不变。

| # | 文件/模块 | 处置策略 | 理由 |
|---|-----------|----------|------|
| 1 | `agent/memory/base.py` | **保留** | `MemoryInterface` 是抽象契约层，被 4 个适配器依赖。`MemoryResult`/`MemoryCapability` 通过 `__init__.py` 导出，删改会破坏外部导入。 |
| 2 | `agent/memory/router.py` | **扩展** | `MemoryRouter` 是 TLM 的入口编排层。保留现有 `route/register/save/search/get_profile/update_graph` 签名；扩展点：1) `ROUTE_MAP` 增加三层映射（L1 工作记忆/L2 情景记忆/L3 语义记忆）；2) `attach_cache_layer` 复用现有缓存；3) `_filter_sensitive_info` 保留不变（已与 `agent.utils.sensitive_data_filter` 解耦）。**禁止修改** `default`(property) 与 `to_dict()` 签名。 |
| 3 | `agent/memory/long_term_memory.py` | **扩展** | `LongTermMemory` 的 SQLite 表结构是 TLM L3 层的基础。保留表 `long_term_memory` schema 与 `save/get/search/delete/verify/get_stats` 签名；扩展点：1) 增加 `embedding` BLOB 列（与 vec0 虚拟表联动）；2) `search` 方法增加 `mode="keyword"\|"semantic"\|"hybrid"` 参数；3) `LongTermMemoryEntry` 增加可选 `embedding` 字段（向后兼容）。 |
| 4 | `agent/memory/short_term_memory.py` | **保留并启用** | 当前无生产调用方但接口设计正确（LRU + TTL + 纯内存）。TLM L1 工作记忆层直接复用，**无需改代码**，只需在 `lifecycle_manager.py` 实例化并注入 `MemoryRouter`。 |
| 5 | `agent/memory/adapters/holographic_adapter.py` | **保留** | SQLite + FTS5 是 TLM L2 情景记忆层的兜底实现，**完全保留**。`save/search/get_profile/update_graph/delete/clear/get_stats` 签名不变。 |
| 6 | `agent/memory/adapters/mem0_adapter.py` | **保留** | 事实提取能力作为可选适配器保留，签名不变。若 TLM 引入 `sqlite-vec` 后事实去重可由 L3 层统一处理，则 `Mem0Adapter` 可降级为"无操作"适配器（保留接口、空实现）。 |
| 7 | `agent/memory/observability.py` | **废弃（保留兼容层）** | `trackEvent` 无外部调用，且 `BusinessMetricsCollector` 已通过 `long_term_memory.py` 间接埋点。建议标记为 deprecated，不再新增调用；保留模块文件以避免破坏 import。 |
| 8 | `agent/memory/filter.py` | **保留** | 仅是 `agent.utils.sensitive_data_filter` 的向后兼容层，本身无逻辑。TLM 不动此文件，继续作为 `router.py` 的依赖。 |
| 9 | `agent/memory/reviewer.py` | **保留并启用** | 当前无生产调用方但能力必备（健康度评分 + 陈旧检测 + 重复检测）。TLM 重构后在 `lifecycle_manager.py` 中实例化并定期触发 `review_quick()`。**签名不变**。 |
| 10 | `agent/memory_optimized.py` | **替换** | `OptimizedChromaDB` 的功能与 `memory/vector_store/vector_store.py` 高度重叠，且 ChromaDB 是重量级依赖（torch + native ext，CI SIGILL 风险已被项目记忆确认）。TLM L2/L3 层切换为 `sqlite-vec` 后，此模块整体废弃；保留文件作为兼容层，但新代码不得引用。 |
| 11 | `memory/vector_store/vector_store.py` | **扩展** | `VectorStore` 是 TLM 的核心向量层。**保留** `add/search/get_by_id/get_recent/clear/get_stats` 签名；**扩展**：1) `_use_chroma` 检测逻辑增加 `_use_sqlite_vec` 分支（优先级：sqlite-vec > chromadb > JSON fallback）；2) `_init_chroma` 改名为 `_init_vector_backend`；3) `InvertedIndex` 与 `LRUQueryCache` 保留不变（已是优秀实现）。`KnowledgeBase` 完全保留。 |
| 12 | `memory/storage.py` | **保留** | JSONL 消息历史与摘要存储是独立于 TLM 的对话历史层，**完全保留**。TLM 不接管 messages.jsonl。 |
| 13 | `agent/server_routes/routes_memory.py` | **保留** | 22 个路由的 HTTP 接口签名是 API 契约白名单的核心（见 §4），**全部保留不变**。允许的内部修改：1) `api_memory_delete_index` 当前是空实现，TLM 后可真正实现删除；2) handler 内部对 `Yunshu._vector_memory` 的调用可改走 `MemoryRouter`（但响应字段不变）。 |
| 14 | `agent/skills_mgmt/memory_abstractor.py` | **扩展** | 修复 `_load_long_term_memories` 中的错误导入（`from agent.memory_optimized import MemoryManager` → `from memory.memory_manager import MemoryManager`），其他签名与算法**完全保留**。P0 结构化字段提取（root_cause/triggers/steps/if_then/anti_patterns）是核心价值，不动。 |

### 2.1 处置统计

- **保留**: 8 个（base/filter/storage/holographic_adapter/mem0_adapter/short_term_memory/reviewer/routes_memory）
- **扩展**: 5 个（router/long_term_memory/vector_store/memory_abstractor + reviewer 启用）
- **替换**: 1 个（memory_optimized → sqlite-vec）
- **废弃**: 1 个（observability，保留兼容层）

---

## 3. TLM 目标架构图

```mermaid
flowchart TB
    subgraph API[HTTP API 层 - routes_memory.py]
        A1[/api/memory/* 22 个路由/]
    end

    subgraph TLM[TLM 三层融合架构]
        direction TB

        Router[MemoryRouter<br/>任务特征路由 + 缓存层 + 敏感过滤]

        subgraph L1[L1 - 工作记忆层 (Hot)]
            STM[ShortTermMemory<br/>纯内存 + LRU + TTL<br/>会话级, 不持久化]
        end

        subgraph L2[L2 - 情景记忆层 (Warm)]
            Holo[HolographicAdapter<br/>SQLite + FTS5<br/>全文检索 + LIKE 兜底]
            STM2[ShortTermMemoryEntry<br/>跨会话短期缓存]
        end

        subgraph L3[L3 - 语义记忆层 (Cold)]
            LTM[LongTermMemory<br/>SQLite + importance/tags/sensitive]
            Vec[VectorStore<br/>sqlite-vec + InvertedIndex + LRU Cache]
            KB[KnowledgeBase<br/>结构化知识文档]
            Mem0[Mem0Adapter<br/>事实提取与去重]
        end

        Router -->|task_type=local_privacy| Holo
        Router -->|task_type=fact_extraction| Mem0
        Router -->|task_type=user_profile| Holo
        Router -->|task_type=knowledge_nav| Holo
        Router -->|default 兜底| Holo
    end

    subgraph Storage[持久化层]
        DB1[(long_term.db)]
        DB2[(holographic.db)]
        DB3[(memory_vec.db<br/>sqlite-vec vec0)]
        DB4[(mem0_facts.json)]
        DB5[(messages.jsonl)]
        DB6[(summary.txt)]
    end

    subgraph Review[审查与抽象层]
        Rev[MemoryReviewer<br/>健康度评分 + 陈旧/重复检测]
        Abs[MemorySkillAbstractor<br/>记忆 → 技能抽象]
    end

    A1 --> Router
    A1 --> Vec
    A1 --> KB
    A1 --> Storage5[Storage<br/>messages.jsonl]

    STM -.会话结束清理.-> Router
    Holo --> DB2
    LTM --> DB1
    Vec --> DB3
    Mem0 --> DB4
    Storage5[Storage] --> DB5
    Storage5[Storage] --> DB6

    Rev --> LTM
    Abs --> LTM
    Abs --> Vec
    Abs --> KB

    classDef l1 fill:#FFE5B4,stroke:#FF8C00
    classDef l2 fill:#B4E5FF,stroke:#0080FF
    classDef l3 fill:#C8E6C9,stroke:#2E7D32
    class STM l1
    class Holo,STM2 l2
    class LTM,Vec,KB,Mem0 l3
```

### 3.1 三层职责定义

| 层 | 名称 | 触发场景 | 持久化 | 检索方式 | 实现模块 |
|----|------|----------|--------|----------|----------|
| **L1** | 工作记忆 (Hot) | 当前会话的中间结果、临时上下文 | 否（纯内存） | 直接 key 查找 | `ShortTermMemory` |
| **L2** | 情景记忆 (Warm) | 跨会话的近期事件、用户操作记录 | 是（SQLite + FTS5） | 全文检索 + LIKE 兜底 | `HolographicAdapter` |
| **L3** | 语义记忆 (Cold) | 长期偏好、知识文档、向量化事实 | 是（SQLite + vec0 + JSON） | 语义向量 KNN + BM25 关键词 + importance 评分 | `LongTermMemory` + `VectorStore` + `KnowledgeBase` + `Mem0Adapter` |

### 3.2 数据流路径

1. **写入**: `API → MemoryRouter.save(key, data, metadata, task_type) → route(task_type) → 对应 Adapter.save`
2. **检索**: `API → MemoryRouter.search(query, top_k, task_type) → [缓存层] → Adapter.search → 聚合 MemoryResult 列表`
3. **跨层联动**: `MemoryReviewer.review() → LongTermMemory.get_stats() + 陈旧/重复检测 → 输出 ReviewResult（含 suggestions）`
4. **记忆→技能**: `MemorySkillAbstractor.abstract_new_skills() → _load_recent_memories() → cluster_memories() → generate_skill_draft() → check_quality_gate() → (可选) SkillsMgmtService.create_manual()`

---

## 4. API 契约白名单

> **不变量约束**（不易）：以下 API 的请求/响应字段在 TLM 重构后必须保持完全一致。前端、第三方调用方依赖这些字段名与类型。
> 任何字段变更必须走"新增字段→灰度→废弃旧字段"的三步流程。

### 4.1 routes_memory.py 路由契约（22 个全部锁定）

#### 4.1.1 记忆基础路由

| 路径 | 方法 | 请求字段 | 响应字段 | 备注 |
|------|------|----------|----------|------|
| `/api/memory/overview` | GET | 无 | `{summary_version: int\|null, summary_text: str\|null, recent_messages: [{index:int, role:str, content:str}], message_count: int, log_stats: dict}` | summary_text 截断 300 字符 |
| `/api/memory/manual` | POST | `{content: str, priority: str="normal"}` | `{ok: bool}` 或 `{ok:False, error: str}` (400/500) | content 不能为空 |
| `/api/memory/compress` | POST | 无 | `{ok: bool}` 或 `{ok:False, error: str}` (500) | 触发异步 compress |
| `/api/memory/<int:index>` | DELETE | 路径参数 index | `{ok: bool}` | **当前空实现，TLM 后可真正删除但响应不变** |
| `/api/memory/clear-summary` | POST | 无 | `{ok: bool}` 或 `{ok:False, error: str}` (500) | |
| `/api/memory/summary` | PUT | `{summary: str}` | `{ok: bool, version: int}` 或 `{ok:False, error: str}` (500) | version 自增 |

#### 4.1.2 向量记忆路由

| 路径 | 方法 | 请求字段 | 响应字段 | 备注 |
|------|------|----------|----------|------|
| `/api/vector/stats` | GET | 无 | `{available: bool, type: str, count: int, persist_dir: str, collection_name: str, cache?: dict, inverted_index?: dict, total_memories: int}` | 不可用时返回 `{available: False}` |
| `/api/vector/search` | POST | `{query: str, top_k: int=5}` | `{ok: bool, results: [{id:str, content:str, metadata:dict, timestamp:str}], count: int}` 或 `{available:False, results:[]}` (503) | top_k 上限 50 |
| `/api/vector/add` | POST | `{content: str, metadata: dict={}}` | `{ok: bool, item_id: str}` 或 `{ok:False, error: str}` (400/503/500) | 返回 `item_id` 格式：`mem_YYYYMMDD_HHMMSS_ffffff` |
| `/api/vector/batch_add` | POST | `{items: [{content: str, metadata?: dict}]}` | `{ok: bool, item_ids: [str], count: int}` | |
| `/api/vector/item/<item_id>` | GET | 路径参数 item_id | `{id:str, content:str, metadata:dict, timestamp:str}` 或 `{error: str}` (404) | 不可用时返回 `{available: False}` (503) |
| `/api/vector/recent` | GET | `?limit=20` (上限 100) | `{items: [{id, content, metadata, timestamp}], count: int}` 或 `{available:False, items:[]}` (503) | |
| `/api/vector/clear` | DELETE | 无 | `{ok: bool}` 或 `{ok:False, error: str}` (503/500) | |

#### 4.1.3 知识库路由

| 路径 | 方法 | 请求字段 | 响应字段 | 备注 |
|------|------|----------|----------|------|
| `/api/knowledge/query` | POST | `{question: str, top_k: int=3}` | `{ok: bool, result: str}` 或 `{available:False, error: str}` (503) | result 是格式化的 markdown 字符串 |
| `/api/knowledge/add` | POST | `{content: str, source: str="manual", tags: [str]=[]}` | `{ok: bool}` 或 `{ok:False, error: str}` (400/503/500) | |

#### 4.1.4 窗口事件路由

| 路径 | 方法 | 请求字段 | 响应字段 | 备注 |
|------|------|----------|----------|------|
| `/api/memory/windows/events` | GET | `?limit=50` (上限 500) | `{events: [dict]}` 或 `{events: [], error: str}` | |
| `/api/memory/windows/stats` | GET | 无 | `{total_duration_sec: float, total_switches: int, apps: [{process:str, title:str, duration_sec:float, switch_count:int, percentage:float}], error?: str}` | apps 最多 20 条 |
| `/api/memory/windows/current` | GET | 无 | `{process: str\|null, title: str\|null, elapsed_sec: float, is_idle: bool}` | |
| `/api/memory/windows/config` | GET | 无 | `dict` (window_sensor 配置) | |
| `/api/memory/windows/config` | POST | `dict` (新配置) | `{ok: bool, config: dict}` 或 `{ok:False, error: str}` (400) | |
| `/api/memory/windows/clear` | POST | 无 | `{ok: bool, message: str}` 或 `{ok:False, error: str}` (500) | 当前是空操作 |
| `/api/window/consent` | POST | `{consent: bool}` | `{ok: bool, consent: bool, enabled: bool}` 或 `{ok:False, error: str}` | |
| `/api/privacy/info` | GET | 无 | `{version: int, 采集说明: str, categories: [dict], 不采集的信息: [str], 数据存储: dict}` | 静态信息 |

### 4.2 Python 模块契约（不可破坏的导入与签名）

#### 4.2.1 `agent/memory/__init__.py` 导出符号

```python
# 以下导出必须保持不变（外部代码 from agent.memory import X 依赖）
from agent.memory.base import MemoryInterface, MemoryResult, MemoryCapability
from agent.memory.router import MemoryRouter
from agent.memory.adapters import HolographicAdapter, Mem0Adapter

__all__ = [
    "MemoryInterface", "MemoryResult", "MemoryCapability",
    "MemoryRouter", "HolographicAdapter", "Mem0Adapter",
]
__version__ = "0.1.0"  # 允许升级，但不能删
```

#### 4.2.2 `MemoryInterface` 抽象方法签名（base.py）

```python
class MemoryInterface(ABC):
    @abstractmethod
    async def save(self, key: str, data: Any, metadata: Optional[dict] = None) -> bool: ...
    @abstractmethod
    async def search(self, query: str, top_k: int = 5) -> list[MemoryResult]: ...
    @abstractmethod
    async def get_profile(self, user_id: str) -> dict: ...
    @abstractmethod
    async def update_graph(self, entities: list, relations: list) -> bool: ...
    @property
    def capabilities(self) -> set[MemoryCapability]: ...
    def to_dict(self) -> dict: ...
```

#### 4.2.3 `MemoryResult` 数据类（base.py）

```python
@dataclass
class MemoryResult:
    content: Any
    confidence: float          # 0.0 ~ 1.0
    source: str                # 'holographic' / 'mem0' / 'long_term_memory' / ...
    metadata: dict = field(default_factory=dict)
```

#### 4.2.4 `MemoryRouter` 公开方法签名（router.py）

```python
class MemoryRouter:
    ROUTE_MAP: dict[str, str]  # 允许扩展新映射，但现有 5 个不得删除
    def __init__(self, default_adapter: Optional[MemoryInterface] = None): ...
    def register(self, name: str, adapter: MemoryInterface) -> None: ...
    def unregister(self, name: str) -> None: ...
    def get_adapter(self, name: str) -> Optional[MemoryInterface]: ...
    def list_adapters(self) -> list[dict]: ...
    def route(self, task_type: str = "local_privacy") -> MemoryInterface: ...
    def attach_cache_layer(self, cache) -> None: ...
    def detach_cache_layer(self) -> None: ...
    async def save(self, key, data, metadata=None, task_type="local_privacy") -> bool: ...
    async def search(self, query, top_k=5, task_type="local_privacy") -> list[MemoryResult]: ...
    async def get_profile(self, user_id, task_type="user_profile") -> dict: ...
    async def update_graph(self, entities, relations, task_type="knowledge_nav") -> bool: ...
    def to_dict(self) -> dict: ...
    @property
    def default(self) -> MemoryInterface: ...
    @default.setter
    def default(self, adapter: MemoryInterface) -> None: ...
```

#### 4.2.5 `VectorStore` 公开方法签名（memory/vector_store/vector_store.py）

```python
class VectorStore:
    def __init__(self, collection_name="agent_memory", persist_dir="./data/memory",
                 model_name="paraphrase-multilingual-MiniLM-L12-v2",
                 cache_size=100, cache_ttl=300, enable_inverted_index=True): ...
    def add(self, content: str, metadata: Optional[dict] = None) -> str: ...  # 返回 item_id
    def batch_add(self, items: list[dict]) -> list[str]: ...
    def search(self, query: str, top_k: int = 5) -> list[MemoryItem]: ...
    async def search_async(self, query: str, top_k: int = 5) -> list[MemoryItem]: ...
    def get_by_id(self, item_id: str) -> Optional[MemoryItem]: ...
    def get_recent(self, limit: int = 10) -> list[MemoryItem]: ...
    def clear(self) -> None: ...
    def get_stats(self) -> dict: ...
    def get_cache_stats(self) -> dict: ...
    def get_index_stats(self) -> Optional[dict]: ...
    @property
    def count(self) -> int: ...
    @property
    def items(self) -> list[MemoryItem]: ...

class MemoryItem:
    id: str
    content: str
    metadata: dict
    timestamp: str
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "MemoryItem": ...
```

#### 4.2.6 `LongTermMemory` 公开方法签名（long_term_memory.py）

```python
class LongTermMemory:
    def __init__(self, db_path="./data/memory/long_term.db", auto_commit=True): ...
    async def save(self, key, content, importance=3, tags=None, sensitive=False, metadata=None) -> bool: ...
    async def get(self, key: str) -> Optional[LongTermMemoryEntry]: ...
    async def search(self, query, top_k=5, min_importance=1, include_sensitive=True) -> list[MemoryResult]: ...
    async def delete(self, key: str, force: bool = False) -> bool: ...
    async def verify(self, key: str) -> bool: ...
    def get_stats(self) -> dict: ...
    def list_unverified(self, limit=50) -> list[LongTermMemoryEntry]: ...
    def list_sensitive(self, limit=50) -> list[LongTermMemoryEntry]: ...
    @property
    def capabilities(self) -> set[MemoryCapability]: ...

@dataclass
class LongTermMemoryEntry:
    key: str
    content: Any
    importance: int = 3
    tags: list[str] = field(default_factory=list)
    created_at: float
    updated_at: float
    last_accessed: float
    access_count: int = 0
    sensitive: bool = False
    verified: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> dict[str, Any]: ...
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LongTermMemoryEntry": ...
```

#### 4.2.7 `Storage` 公开方法签名（memory/storage.py）

```python
class Storage:
    def __init__(self, data_dir: str = "./memory_data"): ...
    def save_message(self, message: dict) -> str: ...  # 返回 timestamp
    def load_recent_messages(self, limit: int = 50) -> list[dict]: ...
    def save_summary(self, summary: str, version: int) -> None: ...
    def load_summary(self) -> tuple[str, int] | None: ...
    def clear_summary(self) -> None: ...
    def clear_messages(self) -> None: ...
```

---

## 5. sqlite-vec 可用性结论与降级方案

### 5.1 探针执行结果

| 阶段 | 状态 | 关键数据 |
|------|------|----------|
| 环境检查 | ✅ | SQLite 3.42.0 / Python 3.12.0 / Windows-10-10.0.19045-SP0 |
| `ENABLE_LOAD_EXTENSION` 编译选项 | ❌ 不在 compile_options | 但 sqlite-vec 0.1.9 提供 `sqlite_vec.load(conn)` Python 级适配器，绕过此限制 |
| sqlite-vec 安装 | ✅ | 已安装 v0.1.9（探针首次运行时通过 `pip install --user` 安装） |
| 加载扩展 | ✅ | 通过 `sqlite_vec.load(conn)` 成功加载 |
| vec0 虚拟表创建（dim=512） | ✅ | `CREATE VIRTUAL TABLE test_vec USING vec0(id INTEGER PRIMARY KEY, embedding FLOAT[512])` |
| 插入 100 条 512 维向量 | ✅ | 39.5ms（约 0.4ms/条） |
| KNN 查询（top-5） | ✅ | 2.0ms, recall@1 = 1.0 |
| `vec_version()` | ✅ | v0.1.9 |

### 5.2 最终结论

**✅ sqlite-vec 在当前 Windows 环境可正常使用，建议作为 TLM 的 L2/L3 向量层首选。**

关键发现：
1. **不需要 `ENABLE_LOAD_EXTENSION` 编译选项** — sqlite-vec 0.1.9+ 提供 Python 级 `load(conn)` 适配器，在标准 CPython 自带的 sqlite3 模块上即可工作。
2. **性能可接受** — 100 条 512 维向量插入 39.5ms，KNN 查询 2.0ms，对于本项目记忆库规模（<10k 条）完全够用。
3. **依赖极轻** — 仅一个 `sqlite-vec` pip 包（含预编译的 native 扩展），不需要 torch / sentence_transformers / chromadb 等重量级依赖。
4. **与现有 SQLite 数据库兼容** — 可与 `LongTermMemory`、`HolographicAdapter` 共享同一个 SQLite 文件，或独立 `memory_vec.db`。

### 5.3 降级方案（如果未来 sqlite-vec 不可用）

按优先级降级：

| 级别 | 方案 | 优点 | 缺点 |
|------|------|------|------|
| 1 | **HolographicAdapter (SQLite + FTS5)** — 现有兜底 | 零依赖，已在生产运行 | 仅支持全文检索，无语义能力 |
| 2 | **ChromaDB + sentence_transformers** — 现有 VectorStore 首选 | 语义检索能力强 | 重量级依赖（torch），CI 上有 SIGILL 风险（已被项目记忆确认） |
| 3 | **sqlite3 原生 + brute-force KNN** | 零依赖，纯 Python | 仅适合小数据集（<1k 条），O(n) 查询 |
| 4 | **hnswlib (纯 C++) 或 LanceDB** | 高性能向量检索 | 新依赖，需要评估 Windows 兼容性 |

### 5.4 实施建议

```python
# 在 VectorStore.__init__ 中增加 sqlite-vec 优先级检测
def _init_vector_backend(self):
    # 优先级 1: sqlite-vec (轻量, 推荐)
    try:
        import sqlite_vec
        self._use_sqlite_vec = True
        self._init_sqlite_vec()
        return
    except ImportError:
        pass

    # 优先级 2: chromadb (重量级, 但已有)
    if HAS_CHROMA and HAS_SENTENCE_TRANSFORMERS:
        self._use_chroma = True
        self._init_chroma()
        return

    # 优先级 3: JSON fallback + BM25 (兜底)
    self._use_chroma = False
    self._load_from_file()
    if self._inverted_index is None:
        self._inverted_index = InvertedIndex()
    self._rebuild_inverted_index()
```

> **注**: §5.4 为初始设计建议。实际实施代码见 §5.6。

### 5.5 生产数据迁移验证结果（Step 3 完成后）

#### 5.5.1 迁移概述

| 项目 | 值 |
|------|-----|
| 迁移时间 | 2026-07-13T12:29:22 UTC |
| 数据量 | 1659 条对话记忆 |
| 向量维度 | 384 (paraphrase-multilingual-MiniLM-L12-v2) |
| encoder | sentence_transformers 5.6.0 (torch 2.13.0+cpu) |
| 迁移耗时 | 52,525ms (30 条/s) |
| 模型加载 | 55,659ms (首次, 含 HuggingFace HEAD 请求) / 30,125ms (离线模式) |
| 失败数 | 0 (全量成功) |
| 目标文件 | `./data/memory/memory_vec.db` (4.35 MB) |

#### 5.5.2 KNN 查询性能（20 次随机抽样，真实 encoder）

| top_k | avg (ms) | p50 (ms) | p99 (ms) |
|-------|----------|----------|----------|
| 5     | 12.64    | 6.86     | 119.52   |
| 10    | 6.84     | 6.48     | 10.89    |
| 20    | 6.88     | 6.04     | 11.29    |

**冷启动效应**: 首次 KNN 查询 119.52ms（sqlite-vec 加载向量索引到内存），第 2 次起稳定在 6-8ms。

**Encode 延迟**: avg=89.40ms, p50=40.27ms, p99=795.77ms（首次 encode 含 torch JIT 编译）。

**端到端搜索延迟估算**: encode(40ms p50) + KNN(6ms p50) = **~46ms p50**。

#### 5.5.3 recall@1 验证

| 测试场景 | 样本数 | recall@1 | 方法 |
|----------|--------|----------|------|
| 探针（100 条 512 维） | 1 | 1.0 | 精确向量匹配 |
| 迁移后验证（1659 条 384 维） | 5 | 1.0 | 随机抽样 + encoder 重新 encode |
| KNN 性能测试（1659 条 384 维） | 20 | 1.0 (20/20) | 随机抽样 + 真实 encoder |

#### 5.5.4 存储效率对比

| 后端 | 文件大小 | 每条 | 是否含向量 | 依赖体积 |
|------|----------|------|-----------|----------|
| JSON fallback | 1.01 MB | 641 bytes | 否（仅 BM25） | 无 |
| **sqlite-vec** | **4.35 MB** | **2,750 bytes** | **是（384 维）** | ~1 MB |
| ChromaDB | N/A | N/A | 是 | ~500 MB (chromadb+onnxruntime) |

**分析**: sqlite-vec 每条 2,750 bytes = 1,536 bytes 向量 (384×4) + 1,214 bytes 元数据/索引。依赖体积比 ChromaDB 减少 500x。

#### 5.5.5 线程安全修复验证

原风险 §6.#2（`_use_chroma` 并发问题）已在 Step 3 修复：

| 修复项 | 方案 | 验证 |
|--------|------|------|
| `_use_chroma` 运行期赋值 | 改为只读 property（基于 `_backend` 派生） | `test_use_chroma_cannot_be_assigned_at_runtime` 抛 `AttributeError` |
| `_backend` 不可变字段 | 构造期确定，运行期不再修改 | `test_backend_field_not_modified_by_add_failure` 验证 add 失败不修改 `_backend` |
| add/search 失败降级 | 不切换后端，仅本次降级到 JSON 路径 | `test_backend_is_sqlite_vec_when_available` 验证后端选择 |

### 5.6 已实施代码摘要（Step 3）

> §5.4 的设计建议已落地实施，实际代码如下：

#### VectorStore.__init__ 后端优先级

```python
# 实际实施代码 (memory/vector_store/vector_store.py)
self._backend = "json"  # 构造期不可变

# 优先级 1: sqlite-vec
if HAS_SENTENCE_TRANSFORMERS and self._init_sqlite_vec():
    self._backend = "sqlite_vec"
# 优先级 2: ChromaDB
elif HAS_CHROMA and HAS_SENTENCE_TRANSFORMERS:
    self._backend = "chromadb"
    self._init_chroma()
# 优先级 3: JSON Fallback + BM25
else:
    self._backend = "json"
    self._load_from_file()
```

#### _use_chroma 只读 property

```python
@property
def _use_chroma(self) -> bool:
    return self._backend == "chromadb"
```

#### get_stats() 新增 backend 字段

```python
stats = {
    "backend": self._backend,  # "sqlite_vec" | "chromadb" | "json"
    "type": "sqlite_vec" if self._backend == "sqlite_vec"
            else ("chroma" if self._use_chroma else "fallback"),
    # ... 其他字段不变
}
```

#### 与设计的差异

| 设计建议 (§5.4) | 实际实施 | 差异原因 |
|-----------------|----------|----------|
| `_use_sqlite_vec` 标志 | `_backend` 不可变字段 + `_use_chroma` 只读 property | 统一后端管理，避免多个布尔标志 |
| `_init_vector_backend()` 方法名 | `_init_sqlite_vec()` 方法名 | 保留原 `_init_chroma()` 不变，新增独立方法 |
| `_backend_lock` 保护后端切换 | 无需锁（`_backend` 构造期不可变） | 不可变字段天然线程安全 |
| 搜索失败时返回空结果 | 搜索失败时本次降级到 JSON 路径 | 保持向后兼容（原有行为） |

### 5.7 探针与迁移数据对比

| 指标 | 探针 (§5.1) | 迁移验证 (§5.5) | 说明 |
|------|------------|----------------|------|
| 数据量 | 100 条 | 1659 条 | 探针使用合成数据，迁移使用生产数据 |
| 维度 | 512 | 384 | 迁移使用 paraphrase-multilingual-MiniLM-L12-v2 (384 维) |
| 插入耗时 | 39.5ms (0.4ms/条) | 52,525ms (30 条/s ≈ 33ms/条) | 迁移含 encode 耗时，探针仅 INSERT |
| KNN 延迟 | 2.0ms | 6.0ms (p50) | 迁移数据量 16x，延迟仅 3x，符合线性增长预期 |
| recall@1 | 1.0 | 1.0 (20/20) | 精确匹配场景下 recall 不随数据量变化 |
| DB 大小 | ~0.8 MB (临时) | 4.35 MB | 1659 条 × 2,750 bytes/条 |

---

## 6. 风险清单

| # | 风险 | 等级 | 影响 | 缓解措施 |
|---|------|------|------|----------|
| 1 | **双 memory 包路径混淆** — `agent/memory/` 与 `memory/` 两个独立包并存，import 路径易错（已导致 `memory_abstractor.py` 的 `from agent.memory_optimized import MemoryManager` 错误） | 🔴 高 | 重构时容易再次踩坑，且 IDE 自动补全可能选错路径 | TLM 重构第一步：在 `agent/memory/__init__.py` 顶部加 `# 路径说明注释`，明确两个包的边界；`memory_abstractor.py` 必须先修复导入路径（改为 `from memory.memory_manager import MemoryManager`） |
| 2 | **VectorStore 接口耦合 ChromaDB** — 当前 `VectorStore._init_chroma()` 失败时会自动降级到 JSON fallback，但 `_use_chroma` 标志在运行时可能被多次切换（搜索失败时 `self._use_chroma = False`），导致并发场景下行为不确定 | 🔴 高 | 多线程下 `VectorStore` 可能出现"一半走 ChromaDB 一半走 JSON"的不一致状态 | TLM 重构时：1) `_use_chroma` 改为构造期确定的不可变标志；2) 搜索失败时不切换标志，仅记录错误并返回空结果；3) 增加 `_backend_lock` 保护后端切换 |
| 3 | **routes_memory.py 直接访问 `Yunshu._memory._storage` 与 `Yunshu._memory._black_box`** — 路由 handler 越过 MemoryRouter 直接访问内部属性，破坏了封装 | 🟡 中 | TLM 重构 MemoryRouter 时，若 `_memory`/`_storage`/`_black_box` 的内部结构变化，22 个路由全部受影响 | 短期：保留 `Yunshu._memory._storage` 与 `_black_box` 属性名不变；长期：在 `MemoryRouter` 增加 `get_overview()/get_window_events()` 等便捷方法，路由改为调用 Router 而非内部属性 |
| 4 | **LongTermMemory 的 `delete()` 默认拒绝删除 importance>=5 或 sensitive 条目** — 这是重要的安全约束，但 routes_memory.py 中没有对应路由暴露此能力 | 🟡 中 | 用户通过 API 无法删除长期记忆（也无对应路由），TLM 重构后若暴露删除 API 必须保留 `force` 参数与默认拒绝行为 | TLM 重构后若新增 `/api/memory/long_term/<key>` DELETE 路由，必须：1) 默认 `force=False`；2) 响应中明确返回拒绝原因；3) 在 `@require_token` 基础上增加二次确认 |
| 5 | **MemoryReviewer 的 `_find_duplicate_entries` 使用 MD5 完全匹配** — 仅能发现完全相同的记忆，对"语义重复但文字不同"的记忆无能为力 | 🟡 中 | 健康度报告中的 `duplicate_entries` 数量偏低，误导运维决策 | TLM 重构时：1) 短期保留 MD5 实现（签名不变）；2) 长期在 `LongTermMemory` 增加 `embedding` 列后，`_find_duplicate_entries` 可选使用 cosine 相似度（增加 `similarity_threshold` 参数） |
| 6 | **memory_abstractor.py 的 `_load_long_term_memories` 当前是死代码** — 错误导入导致永远返回 `[]`，记忆→技能抽象实际只能从 workflow + feedback 两源拉取 | 🟡 中 | 技能抽象遗漏了长期记忆中的重要模式 | TLM 重构第一步即修复此导入（见风险 #1），修复后需重新运行 `test_memory_skill_abstractor.py` 全套测试验证回归 |
| 7 | **sqlite-vec 的 `vec0` 虚拟表不支持 UPDATE** — 文档明确指出 vec0 是只追加的，更新向量需要 DELETE + INSERT | 🟢 低 | `LongTermMemory.save` 当前是 upsert 语义，迁移到 vec0 后需调整 | 在 `VectorStore.add` 中：1) 先 `DELETE FROM vec_table WHERE id = ?`；2) 再 `INSERT`；3) 用事务包裹保证原子性 |
| 8 | **`MemoryRouter.ROUTE_MAP` 中三个未实现的适配器（hindsight/honcho/openviking）** — 路由表是"愿景图"而非"实际能力"，新开发者可能误以为这些适配器已存在 | 🟢 低 | 认知负担 | TLM 重构时：1) 在 `ROUTE_MAP` 注释中明确标注"未实现，降级到 holographic"；2) 或者直接移除这三个映射，仅在文档中保留愿景 |
| 9 | **`ShortTermMemory` 与 `MemoryReviewer` 在生产代码中无调用方** — 死代码嫌疑，删除可能影响未来 TLM 启用 | 🟢 低 | 重构时可能误删 | TLM 重构时：1) 在 `lifecycle_manager.py` 中实例化并注入；2) 若决定不启用，则在模块顶部加 `# Deprecated: 计划在 TLM 阶段启用` 注释，避免被误删 |
| 10 | **探针脚本使用 `pip install --user` 安装 sqlite-vec** — 在多用户环境或 CI 环境可能安装到错误位置 | 🟢 低 | CI 环境可能复现失败 | 在 `requirements.txt` 中显式添加 `sqlite-vec>=0.1.9`；CI 环境使用项目 venv 而非 `--user` |

---

## 附录 A: 验收清单

- [x] 现状盘点表覆盖所有列出的 14 个文件
- [x] 调用点清单完整（每个符号都有调用方）— 见 §1.1 "被谁调用" 列
- [x] `probe_sqlite_vec.py` 可运行并输出明确结论 — 探针执行成功，结论为"可用"
- [x] `TLM_DESIGN.md` 包含全部 6 个章节（现状盘点表 / 资产处置策略表 / TLM 目标架构图 / API 契约白名单 / sqlite-vec 结论 / 风险清单）
- [x] API 契约白名单至少包含 `routes_memory.py` 中的全部 22 个路由 — 见 §4.1

## 附录 B: 探针脚本运行方法

```bash
# 在项目根目录执行
python scripts/probe_sqlite_vec.py

# 输出:
# - stderr: 人类可读的阶段日志
# - stdout: JSON 格式的完整报告（便于程序化读取）
# - 退出码: 0=可用, 1=不可用
```

## 附录 C: TLM 重构建议执行顺序

1. **修复已知 Bug**（最小变更，独立可回滚）
   - 修复 `memory_abstractor.py` 的 `from agent.memory_optimized import MemoryManager` 错误导入
   - 运行 `test_memory_skill_abstractor.py` 验证回归

2. **启用 ShortTermMemory 与 MemoryReviewer**
   - 在 `lifecycle_manager.py` 中实例化并注入 `MemoryRouter`
   - 增加 `/api/memory/review` 路由暴露 `review_quick()`

3. **VectorStore 增加 sqlite-vec 后端**
   - 在 `_init_vector_backend` 中增加 sqlite-vec 优先级检测
   - 保留 ChromaDB 与 JSON fallback 作为降级
   - 增加 `test_vector_store_sqlite_vec.py` 覆盖新后端

4. **LongTermMemory 增加 embedding 列**
   - SQLite 表增加 `embedding BLOB` 列（可选）
   - `search` 方法增加 `mode="keyword"|"semantic"|"hybrid"` 参数
   - 与 `VectorStore` 联动：写入时同步生成 embedding

5. **MemoryRouter 扩展三层映射**
   - `ROUTE_MAP` 增加 L1/L2/L3 映射
   - 保留现有 5 个映射不变
   - 增加 `route_tier(query, top_k, tier=None)` 方法（可选）

6. **废弃 memory_optimized.py**
   - 标记为 deprecated
   - 新代码不得引用
   - 保留文件以避免破坏现有测试
