# Changelog — 2026-08-24

范围：memory 模块读写锁重构（方案 A）+ Loki 异步批量推送 + BM25 索引缓存

| 提交 | 类型 | 摘要 |
|---|---|---|
| `28f0ffe5` | perf | monitoring+knowledge: Loki 异步批量推送 + BM25 索引缓存 |
| `45274291` | fix | memory: 方案A 读写锁重构 — get() 访问统计修复 + reviewer 弃裸连 db |
| `6f690fd4` | docs | memory: 方案A 读写锁重构变更摘要 |

## 一、monitoring/loki.py — 同步 HTTP 推送 → 异步批量推送

**问题**：`push_log` 原为同步 `requests.post`，请求热路径上阻塞调用方（网络 I/O + 锁），高频日志场景放大延迟。

**改动**：
- 新增队列 + 后台 worker 批量聚合：`push_log` 入队即返回（调用方契约不变，仍同步返回）
- 同 labels 日志合并为单请求多 values（`_LOKI_BATCH_SIZE=20`，`_LOKI_FLUSH_INTERVAL=2.0s`）
- **背压保护**：队列上限 `_LOKI_QUEUE_MAX=1000`，满时退化为同步推送（不丢日志不 OOM）
- **生命周期兜底**：`_shutdown` 哨兵 drain + `atexit` 注册 + SingletonManager cleanup_fn，退出不丢已入队日志
- 开关：环境变量 `LOKI_ASYNC_PUSH=0` 退化为同步

## 二、knowledge/search.py — BM25 索引缓存

**问题**：`KnowledgeSearch.__init__` 每次实例化全量 `_build_index`（O(N·词) + 链接解析），高频构造场景耗时。

**改动**：
- 模块级缓存 `_INDEX_CACHE`：键 = `(wiki_root, 文件指纹)`，命中 O(1) 复用 bm25/cards/link_cache
- 指纹变化（新增卡片）自动失效重建，`threading.Lock` 保护读写
- 新增 `TestBM25IndexCache` 2 用例（二次构造命中 / 新卡失效），测试 53 passed

## 三、memory/long_term_memory.py — busy_timeout 兜底 + get() 隐藏 bug 修复

**问题**：
1. get() 访问统计 UPDATE 写在连接块之外（连接已关闭），每次抛 `ProgrammingError` 被吞 → `access_count` 恒 0、`last_accessed` 永不刷新，且每次 get 走错误日志路径
2. 连接未设 `PRAGMA busy_timeout`，违反项目硬约束，写锁竞争直接抛 `SQLITE_BUSY`

**改动**：
- `_get_conn()` / `_init_db()` / `_get_vec_conn()` 三处统一 `PRAGMA busy_timeout=5000`
- get() 访问统计 UPDATE 移进连接块内，随 SELECT 同连接执行

## 四、memory/reviewer.py — 弃裸连，走公开 API

**问题**：`_find_stale_entries` / `_find_duplicate_entries` 直接 `sqlite3.connect(db_path)` 裸连（绕过锁 + 私有成员 `_TABLE_NAME` 耦合 + 无 busy_timeout）。

**改动**：
- 改调 `self._ltm.list_recent(limit=_SCAN_LIMIT)` 公开 API，语义等价
- 陈旧判定 `last_accessed < threshold AND importance < 4`；重复判定 `md5(content)`（保留首条）
- 新增模块常量 `_SCAN_LIMIT = 10000`（list_recent 无分页参数，大 limit 近似全量防漏检）

## 五、测试变更

| 文件 | 内容 |
|---|---|
| `tests/unit/test_knowledge_search.py` | +`TestBM25IndexCache`（缓存命中 / 失效）|
| `tests/unit/test_long_term_memory_embedding.py` | +`TestGetAccessTracking`（access_count 递增 / last_accessed 刷新 / 缺失 key 无副作用）|

## 六、验证结果

| 批次 | 范围 | 结果 |
|---|---|---|
| 单元 | test_knowledge_search | 53 passed |
| 单元 | test_long_term_memory_embedding（含 3 新用例）| 65 passed |
| 单元 | test_memory_module | 62 passed, 21 skipped |
| 集成 | test_memory_reviewer_integration | 11 passed |
| 集成 | store/abstractor/lifecycle/router_tier | 189 passed |
| 集成 | router_concurrency/comprehensive/optimized/refactor/consistency/routes_memory_review | 158 passed, 32 skipped |
| 集成 | tlm三层路由/embedding_e2e/bidirectional_sync/short_term | 60 passed |

说明：全量 `tests/integration` 进程崩溃于已知 `0xC0000005`（ACCESS_VIOLATION，Cross-Encoder/Embedding 原生库问题，与本次改动无关，已实施子进程隔离）。

## 七、影响范围与兼容性

- 无 API 签名变更（LongTermMemory / MemoryReviewer / LokiClient 全部公开接口不变）
- reviewer 行为契约不变（stale/duplicate/sensitive/健康分字段不变）
- 行为变化：`access_count` / `last_accessed` 现真实更新（修复项），下游陈旧判定语义更准确
- Loki 推送延迟从同步阻塞变为异步批量（吞吐提升），调用方可观测行为不变
