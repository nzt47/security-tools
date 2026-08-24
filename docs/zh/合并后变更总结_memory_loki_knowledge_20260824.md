# 变更总结 — memory 读写锁方案A + Loki 异步推送 + BM25 缓存

- 日期：2026-08-24
- 分支：`feat/memory-lock-refactor-20260824` → `develop`
- 提交：`28f0ffe5`、`45274291`、`6f690fd4`、`7d9b1640`（4 个）
- 双平台 PR：GitHub #793 / Gitee #1

## 一、变更概述

| 模块 | 类型 | 变更 |
|---|---|---|
| `agent/memory/long_term_memory.py` | fix | 修复 get() 访问统计隐藏 bug；`_get_conn/_init_db/_get_vec_conn` 统一 `PRAGMA busy_timeout=5000` |
| `agent/memory/reviewer.py` | refactor | 弃裸连 db_path，改走 `list_recent` 公开 API（消除绕过锁 + 私有成员耦合），语义等价 |
| `agent/monitoring/loki.py` | perf | 同步 HTTP 推送 → 队列 + 后台批量聚合 + 背压回退 + shutdown/atexit 兜底 |
| `agent/knowledge/search.py` | perf | BM25 模块级索引缓存（wiki_root + 指纹键），命中 O(1) 复用 |
| `tests/` | test | 新增 `TestBM25IndexCache`（2 用例）、`TestGetAccessTracking`（3 用例）|
| `docs/zh/` | docs | 变更摘要 + Changelog 两份文档 |

## 二、详细变更

### 1. memory 读写锁重构（方案 A）

**get() 隐藏 bug**：访问统计 UPDATE 写在连接块外（连接已关闭），每次抛 `ProgrammingError` 被 except 吞掉 → `access_count` 恒 0、`last_accessed` 永不刷新、每次 get 走错误日志路径。已修复（UPDATE 移进连接块内）。

**busy_timeout 兜底**：三处 SQLite 连接统一 `PRAGMA busy_timeout=5000`，写锁竞争等待而非 `SQLITE_BUSY`（守项目硬约束）。

**reviewer 弃裸连**：`_find_stale_entries` / `_find_duplicate_entries` 从 `sqlite3.connect(db_path)` 改为 `self._ltm.list_recent(limit=_SCAN_LIMIT)`，语义等价（陈旧：`last_accessed < threshold AND importance < 4`；重复：`md5(content)` 保留首条）。

### 2. monitoring/loki — 异步批量推送

- `push_log` 入队即返回（调用方契约不变）
- 同 labels 合并为单请求多 values（`_LOKI_BATCH_SIZE=20`，`_LOKI_FLUSH_INTERVAL=2.0s`）
- 背压：队列上限 `_LOKI_QUEUE_MAX=1000`，满时退化同步推送（不丢日志不 OOM）
- 生命周期：`_shutdown` 哨兵 drain + `atexit` + SingletonManager cleanup_fn
- 开关：`LOKI_ASYNC_PUSH=0` 退化为同步

### 3. knowledge/search — BM25 索引缓存

- 模块级 `_INDEX_CACHE`：键 = `(wiki_root, 文件指纹)`，命中 O(1) 复用 bm25/cards/link_cache
- 指纹变化（新增卡片）自动失效重建，`threading.Lock` 保护
- 验证：25 条日志批量聚合为 2 次请求（异步验证脚本通过）、背压同步回退、shutdown drain 均验证通过

## 三、验证结果

| 批次 | 范围 | 结果 |
|---|---|---|
| 单元 | `test_knowledge_search.py`（含新缓存用例）| 53 passed |
| 单元 | `test_long_term_memory_embedding.py`（含新 access tracking 用例）| 65 passed |
| 单元 | `test_memory_module.py` | 62 passed, 21 skipped |
| 集成 | `test_memory_reviewer_integration.py` | 11 passed |
| 集成 | tlm store / abstractor / lifecycle / router_tier | 189 passed |
| 集成 | router 并发/综合 / optimized / refactor / consistency / routes_memory_review | 158 passed, 32 skipped |
| 集成 | tlm三层路由 / embedding_e2e / bidirectional_sync / short_term | 60 passed |

本地全绿；memory 与上层 review 流程无冲突。

## 四、CI 状态（双平台 PR）

### GitHub PR #793
- 状态：`OPEN` / `MERGEABLE`，无进行中任务
- 通过：集成测试（3.10-3.12 × ubuntu/windows 全绿）、性能/E2E/混沌/知识库/Lint/循环依赖/日志守护等 20+ 项
- 失败（28 项，均为**预存在/环境性问题**，与本次改动无关）：
  - 单元测试 shard：`RuntimeError: can't start new thread`（8661 测试累积致 runner 线程资源耗尽）
  - 文档链接预检：`incident_report_template.md` 占位符失效（develop 也不存在）
  - Boundary Guard：基线文件不匹配；架构规则/安全扫描：`fix/ci-pre-existing-20260824` 专项处理范畴
  - 全项目覆盖率 shard：同上资源问题

### Gitee PR #1
- 状态：`open` / `mergeable=True`，head `7d9b1640`，base `develop`
- 无 CI 报错（CI 均在 GitHub Actions 侧）

## 五、交付物

| 文件 | 说明 |
|---|---|
| `docs/zh/memory模块读写锁重构方案A_变更摘要_20260824.md` | 方案 A 变更摘要 |
| `docs/zh/Changelog_20260824.md` | 详细变更日志（PR 描述依据）|

## 六、遗留

- 全量 `tests/integration` 进程崩溃于已知 `0xC0000005`（Cross-Encoder/Embedding 原生库），已实施子进程隔离，建议 CI 隔离环境重跑
- Gitee 侧无 CI 流水线对接（CI 在 GitHub Actions）
