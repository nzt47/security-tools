# 云枢向量系统接口设计

## 概述

为云枢的向量存储系统（VectorStore + KnowledgeBase）提供对外 HTTP API 和前端管理界面，使各模块和用户可以通过统一接口使用语义搜索和知识管理能力。

## 现状

- 底层能力完备：`memory/vector_store/vector_store.py` 提供 VectorStore（ChromaDB + BM25 倒排索引 + JSON fallback）和 KnowledgeBase
- DigitalLife 启动时已初始化 `_vector_memory` 和 `_knowledge_base`
- `digital_life_state.py` 提供 search_memory()、get_memory_stats()、clear_memory() mixin 方法
- **缺少**：HTTP API 端点、前端管理界面

## 设计

### 接口范围

采用轻量集成方案：API 端点追加到现有 `routes_memory.py`，前端在记忆视图新增「向量记忆」子标签页。

### API 端点

所有端点通过 `state.Yunshu._vector_memory` / `_knowledge_base` 访问，未初始化时返回 `{"available": false}`。

| 方法 | 路径 | 请求体 | 返回 |
|------|------|--------|------|
| GET | `/api/vector/stats` | — | `{available, type, count, cache, inverted_index}` |
| POST | `/api/vector/search` | `{query, top_k}` | `{results: [{id, content, metadata, timestamp}]}` |
| POST | `/api/vector/add` | `{content, metadata}` | `{ok, item_id}` |
| POST | `/api/vector/batch_add` | `{items: [{content, metadata}]}` | `{ok, item_ids, count}` |
| GET | `/api/vector/item/<id>` | — | `{id, content, metadata, timestamp}` |
| DELETE | `/api/vector/clear` | — | `{ok}` |
| POST | `/api/knowledge/query` | `{question, top_k}` | `{result}` |
| POST | `/api/knowledge/add` | `{content, source, tags}` | `{ok}` |

### 前端 UI

在记忆管理视图新增第三个子标签页「🧠 向量记忆」，包含：

1. **统计卡片**：记忆数、存储类型（ChromaDB/BM25）、缓存命中率、索引统计
2. **搜索区**：搜索框 + top_k 下拉 + 搜索结果列表（相似度排序）
3. **操作区**：添加记忆、批量导入、清空
4. **最近记忆列表**：最近添加的 20 条
5. **知识库查询**：可折叠面板，支持查询和添加文档

### 修改文件

- `agent/server_routes/routes_memory.py` — 追加 8 个 API 端点
- `templates/index.html` — 记忆视图增加向量记忆子标签页和面板
- `static/js/sidebar/memory.js` — 增加向量记忆管理 JS 函数
