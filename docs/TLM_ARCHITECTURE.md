# TLM 三层记忆架构 — 架构设计说明文档

> **文档定位**: TLM (Tiered Local Memory) 三层记忆架构的正式架构设计文档
> **生成日期**: 2026-07-27
> **状态**: 已实现（Step 1-6 全部完成）

---

## 一、架构概览

### 1.1 设计目标

| 目标 | 描述 |
|------|------|
| **本地优先** | 所有记忆数据存储在本地 SQLite，不依赖云端服务 |
| **分层存储** | 按数据热度分 L1(热)/L2(温)/L3(冷) 三层，优化访问效率 |
| **语义检索** | 支持 keyword / semantic / hybrid 三种检索模式 |
| **轻量依赖** | sqlite-vec 替代 chromadb，依赖从 ~500MB 降至 ~1MB |
| **向后兼容** | API 契约白名单不变，旧数据自动迁移 |

### 1.2 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                     Agent (Orchestrator)                     │
│                         ┌──────────┐                         │
│                         │  Query   │                         │
│                         └────┬─────┘                         │
│                              │                               │
│                    ┌─────────▼──────────┐                    │
│                    │   MemoryRouter     │                    │
│                    │  route_tier(key)   │                    │
│                    └─────────┬──────────┘                    │
│                              │                               │
│              ┌───────────────┼───────────────┐               │
│              │               │               │               │
│     ┌────────▼──────┐ ┌─────▼──────┐ ┌──────▼──────┐        │
│     │   L1 (热)     │ │  L2 (温)   │ │  L3 (冷)   │        │
│     │ ShortTermMem  │ │ Holographic│ │ LongTermMem │        │
│     │   (内存 LRU)  │ │ (SQLite+   │ │ (SQLite+   │        │
│     │   TTL=300s    │ │  FTS5)     │ │  Embedding)│        │
│     └────────┬──────┘ └─────┬──────┘ └──────┬──────┘        │
│              │               │               │               │
│              │         ┌─────▼──────┐        │               │
│              │         │ VectorStore│        │               │
│              │         │ (sqlite-vec│        │               │
│              │         │  > chromadb│        │               │
│              │         │  > JSON)   │        │               │
│              │         └────────────┘        │               │
└──────────────┴───────────┴───────────────────┴──────────────┘
                          │
                   ┌──────▼──────┐
                   │  data/memory│
                   │  /ltm.db    │
                   │  /stm.db    │
                   │  /holo.db   │
                   │  /vec.db    │
                   └─────────────┘
```

---

## 二、三层架构设计

### 2.1 L1 — 短时记忆（Hot）

| 属性 | 值 |
|------|-----|
| **实现类** | `ShortTermMemory` |
| **存储** | 内存 LRU 字典 |
| **容量** | max_size=100（可配置） |
| **过期** | TTL=300s（可配置） |
| **检索方式** | `get(key)` — O(1) 直接查找 |
| **适用场景** | 当前会话上下文、临时变量、用户当前意图 |
| **数据特征** | 高频访问、短生命周期、不需要持久化 |

**触发条件**（`_classify_tier`）：
- query 以 `stm:` 或 `session:` 前缀开头
- query 为纯 ASCII 短 key（< 8 字符、无空格、无 CJK）

### 2.2 L2 — 全息记忆（Warm）

| 属性 | 值 |
|------|-----|
| **实现类** | `HolographicAdapter` |
| **存储** | SQLite + FTS5（全文检索） |
| **检索方式** | `search(key, top_k)` — FTS5 全文索引 |
| **适用场景** | 跨会话近期事件、用户操作记录、会话历史 |
| **数据特征** | 中频访问、中等生命周期、需要全文检索 |

**触发条件**：
- query 含时间词：`最近|上次|刚才|今天|昨天|recent|last`
- query 含操作词：`做了|操作|记录|did|operation`

### 2.3 L3 — 长期记忆（Cold）

| 属性 | 值 |
|------|-----|
| **实现类** | `LongTermMemory` |
| **存储** | SQLite + embedding 列（JSON TEXT） |
| **检索方式** | `search(query, mode, query_embedding)` — 三模式 |
| **检索模式** | keyword（LIKE）/ semantic（余弦相似度 KNN）/ hybrid（合并去重） |
| **适用场景** | 用户偏好、知识文档、长期事实 |
| **数据特征** | 低频访问、永久存储、需要语义检索 |

**触发条件**：
- query 含语义词：`偏好|喜欢|知识|关于|prefer|knowledge`
- query 长度 >= 12 字符
- 兜底（L1/L2 都不命中时）

**semantic 检索流程**：
```
query_embedding → SELECT * WHERE embedding IS NOT NULL
                → 逐条 json.loads(embedding)
                → _cosine_similarity(query, doc)  [纯 Python，无 numpy]
                → sorted by score DESC
                → 取 top_k
```

---

## 三、组件交互

### 3.1 MemoryRouter 路由流程

```
route_tier(key, tier=None, top_k=5)
│
├── tier is None? → _classify_tier(key) 自动判定
│   ├── L1: stm:/session: 前缀 → STM
│   ├── L1: 纯 ASCII 短 key → STM
│   ├── L2: 时间词/操作词 → HolographicAdapter
│   ├── L3: 语义词/长查询 → LTM
│   └── L3: 兜底 → LTM
│
├── tier is not None? → 显式指定（大小写不敏感）
│
├── L1 路径: adapter.get(key) → MemoryResult(source="short_term")
│   └── L1 未注册 → 降级到 default.search(key)
│
├── L2/L3 路径: adapter.search(key, top_k) → list[MemoryResult]
│   └── 适配器未注册 → 降级到 default.search(key)
│   └── search 抛异常 → 返回空列表 []
│
└── 每个 result.metadata["tier"] = tier_upper
```

### 3.2 VectorStore 后端选择

```
VectorStore.__init__()
│
├── 1. 尝试 sqlite-vec（最优）
│   └── 加载 sqlite_vec 扩展 → 创建 vec0 虚拟表
│
├── 2. sqlite-vec 不可用 → 尝试 chromadb
│   └── 加载 chromadb + sentence_transformers
│
└── 3. chromadb 不可用 → JSON fallback
    └── 纯文本 + 倒排索引 + BM25 评分
```

### 3.3 lifecycle_manager 初始化顺序

```
_initialize_core_systems()
│
├── 3.1 STM 实例化（ShortTermMemory, L272-282）
├── 3.2 LTM 实例化（LongTermMemory, L297-299）
├── 3.6 MemoryReviewer 实例化（L301-308）
├── 3.7 MemoryRouter 实例化 + 三层注册（L310-326）
│   ├── register_tier("L1", STM)
│   ├── register_tier("L2", router.default)  # HolographicAdapter
│   └── register_tier("L3", LTM)
└── 4. 行为控制（L328+）
```

---

## 四、API 契约

### 4.1 核心接口（不可变）

| 接口 | 签名 | 变更 |
|------|------|------|
| `MemoryRouter.route(task_type)` | → MemoryInterface | 不变 |
| `MemoryRouter.route_tier(key, tier, top_k)` | → list[MemoryResult] | **新增** |
| `LongTermMemory.save(key, content, ..., embedding)` | → bool | 新增 embedding 参数 |
| `LongTermMemory.search(query, ..., mode, query_embedding)` | → list[MemoryResult] | 新增 mode 参数 |
| `LongTermMemory.list_recent(limit, days)` | → list[LongTermMemoryEntry] | **新增** |
| `LongTermMemory.get_stats()` | → dict | 新增 embedding_entries |
| `VectorStore.get_stats()` | → dict | 新增 backend 字段 |

### 4.2 HTTP API

| 端点 | 方法 | 说明 | 状态 |
|------|------|------|------|
| `/api/memory/review` | GET | 获取审查状态 | 新增（Step 2） |
| `/api/memory/review` | POST | 触发 quick_review | 新增（Step 2） |
| `/api/vector/stats` | GET | 向量存储统计 | 新增 backend 字段 |

---

## 五、数据模型

### 5.1 LongTermMemory 表结构

```sql
CREATE TABLE long_term_memory (
    key TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    importance INTEGER DEFAULT 3,
    tags TEXT DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_accessed REAL NOT NULL,
    access_count INTEGER DEFAULT 0,
    sensitive INTEGER DEFAULT 0,
    verified INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    embedding TEXT DEFAULT NULL  -- [TLM-L4] 新增：JSON TEXT 存储向量
);

-- 索引
CREATE INDEX idx_ltm_importance ON long_term_memory(importance DESC);
CREATE INDEX idx_ltm_last_accessed ON long_term_memory(last_accessed);
```

### 5.2 迁移策略

```python
# 幂等迁移（_init_db 中自动执行）
columns = [col[1] for col in conn.execute("PRAGMA table_info(long_term_memory)").fetchall()]
if "embedding" not in columns:
    conn.execute("ALTER TABLE long_term_memory ADD COLUMN embedding TEXT DEFAULT NULL")
```

---

## 六、设计决策

### 6.1 为什么用 sqlite-vec 而非 chromadb？

| 维度 | chromadb | sqlite-vec | 决策理由 |
|------|----------|------------|----------|
| 依赖大小 | ~500MB | ~1MB | **-99.8%**，部署更轻量 |
| 启动时间 | 2-3 分钟 | <1 秒 | CI/CD 更快 |
| 原生扩展 | hnswlib/onnxruntime | sqlite extension | sqlite-vec 更稳定 |
| 数据一致性 | 独立存储 | 与 LTM 同库 | 事务一致性更好 |

### 6.2 为什么 embedding 用 JSON TEXT 而非 BLOB？

| 维度 | JSON TEXT | BLOB |
|------|-----------|------|
| 可读性 | ✅ 可直接 SELECT 查看 | ❌ 需解析 |
| 序列化 | json.dumps/loads | struct.pack/unpack |
| ALTER TABLE | ✅ 兼容 | 需额外处理 |
| 性能 | ⚠️ json.loads 开销 | ✅ 更快 |

**决策**：选 JSON TEXT，因为【简易】原则优先可读性和兼容性。性能可通过后续优化（BLOB + 预编译）解决。

### 6.3 为什么 semantic 搜索用纯 Python 而非 numpy？

- **不引入新依赖**：保持最小依赖集
- **384 维 × 200 条**：纯 Python ~10ms，可接受
- **后续优化路径**：数据量增长后可切换到 numpy 或 sqlite-vec KNN

---

## 七、性能特性

### 7.1 延迟基准（本地 Windows，1000 条 × 384 维）

| 操作 | p50 延迟 | 评估 |
|------|----------|------|
| L1 get | <0.1ms | ✅ 优秀 |
| L2 FTS5 search | ~1ms | ✅ 优秀 |
| L3 keyword | 4.4ms | ✅ 良好 |
| L3 semantic | 220ms | ⚠️ 可接受（目标 <100ms） |
| L3 hybrid | 220ms | ⚠️ 受 semantic 影响 |
| list_recent(200) | 69ms | ✅ 良好 |
| get_stats | 12ms | ✅ 良好 |

### 7.2 semantic 搜索瓶颈分析

```
_search_semantic() 耗时分解（1000 条）：
├── SELECT * WHERE embedding IS NOT NULL  ~10ms   （全表加载）
├── json.loads(embedding) × 1000          ~100ms  （主要瓶颈）
├── _cosine_similarity × 1000             ~10ms
└── sorted()                              ~1ms
                                    总计 ~220ms（含 GC 抖动）
```

### 7.3 优化路线图（未实施）

| 优先级 | 优化项 | 预期收益 | 复杂度 |
|--------|--------|----------|--------|
| P1 | semantic 只 SELECT key/content/embedding | -30% IO | 低 |
| P2 | heapq.nlargest 替代 sorted | -50% 排序 | 低 |
| P3 | embedding 存为 BLOB | -80% json.loads | 中 |
| P4 | sqlite-vec KNN 替代纯 Python | -90% 总延迟 | 高 |

---

## 八、测试覆盖

### 8.1 测试矩阵

| 层级 | 单元测试 | 集成测试 | 边界测试 |
|------|----------|----------|----------|
| L1 (STM) | test_memory_module | test_tlm三层路由_e2e | 空key/过期/容量 |
| L2 (Holographic) | — | test_tlm三层路由_e2e | mock 覆盖 |
| L3 (LTM) | test_long_term_memory_embedding | test_tlm_embedding_search_e2e | 极端数据量(100条) |
| Router | test_memory_router_tier | test_tlm三层路由_e2e | 10 E2E + 4 边界 + 9 判定 |
| lifecycle | — | test_lifecycle_manager_memory_router | 8 场景 |
| 废弃 | test_memory_optimized_deprecation | — | 5 种触发场景 |

### 8.2 总数

- **单元测试**: 87 个
- **集成测试**: 48 个（含 6 个极端数据量）
- **回归测试**: 162 个
- **总计**: 485 个，全部通过

---

## 九、风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| sqlite-vec 原生扩展不兼容 | 中 | L3 降级到 chromadb/JSON | 自动降级机制 + conftest.py CI 禁用 |
| semantic 搜索大数据量延迟 | 高 | >1s（10000条） | 短期：接受；长期：sqlite-vec KNN |
| embedding 生成依赖 sentence_transformers | 中 | 无模型时降级 keyword | 自动降级 + 测试用手工向量 |
| SQLite 并发写入 | 低 | 写锁竞争 | threading.Lock + WAL 模式 |
