# TLM 三层记忆架构 — 部署清单

> **文档定位**: TLM 重构（Step 1-6）的生产环境部署操作清单
> **生成日期**: 2026-07-27
> **适用版本**: TLM Step 1-6 全部完成后

---

## 一、前置条件检查

### 1.1 依赖版本要求

| 依赖 | 版本 | 用途 | 是否必须 |
|------|------|------|----------|
| Python | >= 3.12 | 运行时 | ✅ 必须 |
| sqlite-vec | >= 0.1.9 | L3 向量存储后端 | ✅ 必须 |
| chromadb | == 1.5.9 | L3 fallback 后端 | ⚠️ 可选（保留为降级） |
| sentence-transformers | == 5.5.1 | embedding 生成 | ⚠️ 可选（无则降级 keyword） |

### 1.2 验证命令

```powershell
# 检查 Python 版本
python --version  # 应 >= 3.12

# 检查 sqlite-vec 是否安装
python -c "import sqlite_vec; print('sqlite-vec OK')"

# 检查 chromadb 是否可用（可选）
python -c "import chromadb; print('chromadb OK')"

# 检查 sentence-transformers（可选）
python -c "from sentence_transformers import SentenceTransformer; print('ST OK')"
```

---

## 二、环境变量

TLM 重构**未新增环境变量**。现有配置通过 `config.yaml` 管理，无需额外环境变量设置。

### 2.1 可选环境变量（已有，非新增）

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `CI` | 未设置 | CI 环境下 conftest.py 会禁用 chromadb 原生扩展 |
| `SKILLS_OFFLINE` | 未设置 | 离线模式下跳过 sentence_transformers 加载 |
| `LOG_LEVEL` | INFO | 日志级别（DEBUG 可查看路由判定日志） |

---

## 三、部署步骤

### 3.1 安装依赖

```powershell
# 进入项目目录
cd c:\Users\Administrator\agent

# 安装新增依赖（sqlite-vec）
pip install sqlite-vec>=0.1.9

# 或完整安装
pip install -r requirements.txt
```

### 3.2 数据库备份（关键！）

```powershell
# 备份现有记忆数据（Step 3-5 涉及 schema 变更）
Copy-Item -Path .\data\memory\ -Destination .\data\memory_backup_$(Get-Date -Format "yyyyMMdd") -Recurse
```

### 3.3 数据库迁移（自动）

TLM 重构的 schema 迁移是**自动且幂等**的，无需手动执行：

- `LongTermMemory._init_db()` 会自动检测并添加 `embedding` 列（ALTER TABLE）
- `VectorStore.__init__()` 会自动选择后端（sqlite-vec > chromadb > JSON）
- 首次启动时自动完成迁移，日志会输出 `[LongTermMemory] 迁移: 已添加 embedding 列`

### 3.4 可选：向量数据迁移（JSON → sqlite-vec）

```powershell
# Dry-run 模式（不写入，只验证）
python scripts/migrate_to_sqlite_vec.py --dry-run

# 实际迁移
python scripts/migrate_to_sqlite_vec.py
```

预期输出：`{migrated: 1659, failed: 0, recall@1: 1.0}`

### 3.5 验证 DeprecationWarning（Step 6）

```powershell
# 验证 memory_optimized.py 已废弃
python scripts/verify_deprecation.py
```

预期输出：5 种场景全部 `[PASS]`。

### 3.6 启动服务

```powershell
# 正常启动（无变化）
python -m agent.server

# 或使用现有启动脚本
python scripts/start.py
```

### 3.7 启动后验证

```powershell
# 1. 检查记忆系统健康
curl http://localhost:8000/api/memory/review

# 2. 检查向量存储后端
curl http://localhost:8000/api/vector/stats
# 预期响应包含 "backend": "sqlite_vec"

# 3. 检查日志中的路由判定
# 设置 LOG_LEVEL=DEBUG 可查看 [MemoryRouter] route_tier 自动判定日志
```

---

## 四、回滚步骤

### 4.1 快速回滚（< 5 分钟）

```powershell
# 1. 回退代码
git revert <merge_commit>

# 2. 恢复数据库备份
Copy-Item -Path .\data\memory_backup_yyyyMMdd\* -Destination .\data\memory\ -Recurse -Force

# 3. 重启服务
python -m agent.server
```

### 4.2 分步回滚

| Step | 回滚方式 | 预计耗时 |
|------|----------|----------|
| Step 6（废弃） | `git revert` + 重新启用 memory_optimized | < 10 分钟 |
| Step 5（路由） | `git revert`（无 schema 变更） | < 5 分钟 |
| Step 4（embedding） | `git revert` + 恢复 SQLite 备份 | < 30 分钟 |
| Step 3（sqlite-vec） | `git revert` + 恢复 JSON 后端 | < 30 分钟 |
| Step 2（STM/Reviewer） | `git revert` | < 5 分钟 |
| Step 1（Bug 修复） | `git revert` | < 5 分钟 |

---

## 五、部署检查清单

- [ ] Python >= 3.12 已确认
- [ ] `sqlite-vec>=0.1.9` 已安装
- [ ] `data/memory/` 目录已备份
- [ ] 数据库迁移日志确认（`[LongTermMemory] 迁移: 已添加 embedding 列`）
- [ ] `/api/vector/stats` 返回 `backend: "sqlite_vec"`
- [ ] `/api/memory/review` 返回 200
- [ ] `memory_optimized.py` 导入触发 DeprecationWarning
- [ ] `pytest tests/unit/test_long_term_memory_embedding.py` 全通过
- [ ] `pytest tests/integration/test_tlm三层路由_e2e.py` 全通过
- [ ] 生产代码无 `from agent.memory_optimized import` 引用

---

## 六、已知限制

| 限制 | 影响 | 缓解措施 |
|------|------|----------|
| sqlite-vec 原生扩展在部分 CPU 不支持 | ACCESS_VIOLATION 崩溃 | 自动降级到 chromadb 或 JSON |
| semantic 搜索 1000 条 ~220ms | 大数据量延迟偏高 | 后续优化：只 SELECT 必要列 + heapq.nlargest |
| `test_vector_store_sqlite_vec.py` 在本地 Windows 崩溃 | 无法本地运行 | CI Linux 环境下通过 conftest.py 禁用原生扩展 |
