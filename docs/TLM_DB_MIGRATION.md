# TLM 三表合一数据库迁移步骤文档

> 配套文档：[`PR_TLM_REFACTOR.md`](./PR_TLM_REFACTOR.md)
> 适用版本：commit `96697c7a` 及之后
> 涉及文件：[`agent/memory/adapters/holographic_adapter.py`](../agent/memory/adapters/holographic_adapter.py)

---

## 1. 迁移概述

### 1.1 目标
将 HolographicAdapter 从单表（`memory_items` + FTS5 `memory_fts`）扩展为三表合一架构：

| 表名 | 类型 | 用途 |
|------|------|------|
| `memory_items` | 普通表 | 主表，存储 key/data/metadata + TLM 扩展字段 |
| `memory_fts` | FTS5 虚拟表 | 全文索引，与主表同事务 |
| `memories_vec` | vec0 虚拟表 | 向量 KNN 检索（可选，sqlite-vec 不可用时降级） |
| `memories_vec_failed` | 普通表 | 向量写入失败兜底，供后台补偿重放 |

### 1.2 迁移特性
- **幂等**：所有 DDL 使用 `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ADD COLUMN`（带存在性检查）
- **非破坏性**：不修改现有表数据，仅追加字段和新表
- **可降级**：sqlite-vec 不可用时自动跳过向量表创建，不影响主流程
- **零停机**：迁移在 `__init__` 中自动执行，无需独立迁移脚本

### 1.3 迁移执行时机
`HolographicAdapter.__init__` 中按以下顺序自动执行：
```
_init_db()                  # 1. 创建主表 + FTS5（已有，幂等）
_init_vec_table()           # 2. 创建向量表（新增，可降级）
_migrate_schema_if_needed() # 3. 补齐主表扩展字段 + 兜底表（新增）
```

---

## 2. 前置检查

### 2.1 环境检查清单

| 检查项 | 命令 | 期望结果 |
|--------|------|----------|
| Python 版本 | `python --version` | ≥ 3.10（typing 支持） |
| sqlite3 模块 | `python -c "import sqlite3; print(sqlite3.sqlite_version)"` | ≥ 3.39（FTS5 默认启用） |
| sqlite-vec 安装 | `pip show sqlite-vec` | 已安装则正常模式，未安装则降级模式 |
| 数据库目录可写 | `ls -ld ./data/memory/` | drwxr-xr-x |

### 2.2 sqlite-vec 可用性验证

```python
# 单独验证 sqlite-vec 是否可加载
import sqlite3
import sqlite_vec

conn = sqlite3.connect(":memory:")
conn.enable_load_extension(True)
sqlite_vec.load(conn)
result = conn.execute("SELECT vec_version()").fetchone()
print(f"sqlite-vec 版本: {result[0]}")
conn.close()
```

若上述脚本失败，迁移仍会进行（降级模式），但向量检索不可用。

### 2.3 备份建议

```powershell
# 迁移前备份现有数据库
Copy-Item .\data\memory\holographic.db .\data\memory\holographic.db.bak.$(Get-Date -Format "yyyyMMddHHmmss")
```

---

## 3. 表结构 DDL（按执行顺序）

### 3.1 Step 1: 主表 `memory_items`（已有，幂等）

```sql
CREATE TABLE IF NOT EXISTS memory_items (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    hit_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_items(created_at);
```

### 3.2 Step 2: FTS5 表 `memory_fts`（已有，幂等）

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
USING fts5(key, data, metadata, tokenize='unicode61');
```

### 3.3 Step 3: 向量表 `memories_vec`（新增，可降级）

```sql
-- 仅当 sqlite-vec 扩展加载成功时执行
CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec
USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[512]);
```

**维度说明**：`FLOAT[512]` 与类常量 `_VEC_DIM = 512` 必须一致。若需修改维度，参见 §6 维度变更。

### 3.4 Step 4: 主表扩展字段（新增，幂等 ALTER）

```sql
-- 逐列检查，缺失则 ADD COLUMN
ALTER TABLE memory_items ADD COLUMN access_count INTEGER DEFAULT 0;
ALTER TABLE memory_items ADD COLUMN last_accessed REAL;
ALTER TABLE memory_items ADD COLUMN type TEXT;
ALTER TABLE memory_items ADD COLUMN category TEXT;
```

### 3.5 Step 5: 兜底表 `memories_vec_failed`（新增，幂等）

```sql
CREATE TABLE IF NOT EXISTS memories_vec_failed (
    key TEXT PRIMARY KEY,
    embedding BLOB,           -- JSON bytes 序列化的向量
    error TEXT,               -- 失败原因
    created_at REAL NOT NULL,
    retries INTEGER DEFAULT 0
);
```

---

## 4. 迁移执行步骤

### 4.1 自动迁移（推荐）

无需手动操作。首次实例化 `HolographicAdapter` 时自动完成全部迁移：

```python
from agent.memory.adapters.holographic_adapter import HolographicAdapter

# 首次实例化即触发迁移
adapter = HolographicAdapter(db_path="./data/memory/holographic.db")

# 查看迁移结果
print(f"向量层可用: {adapter._vec_available}")
```

**预期日志输出**：
```
[HolographicAdapter][vec] sqlite_vec.load(conn) 加载成功（Python 适配器路径）
[HolographicAdapter][vec] 向量表就绪: table=memories_vec, dim=512 → _vec_available=True
[HolographicAdapter][migrate] 迁移完成: 新增字段=['access_count', 'last_accessed', 'type', 'category'], 兜底表=memories_vec_failed 已就绪
[HolographicAdapter] 初始化完成: db=./data/memory/holographic.db, vec_available=True
```

### 4.2 手动迁移（可选，用于预演或排错）

```python
# scripts/manual_migrate.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.memory.adapters.holographic_adapter import HolographicAdapter

DB_PATH = "./data/memory/holographic.db"
adapter = HolographicAdapter(db_path=DB_PATH, enable_cache=False)

# 验证迁移结果
import sqlite3
with sqlite3.connect(DB_PATH) as conn:
    conn.row_factory = sqlite3.Row
    # 检查表是否存在
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
    ).fetchall()]
    print(f"现有表: {tables}")
    
    # 检查主表字段
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(memory_items)").fetchall()]
    print(f"memory_items 字段: {cols}")
    
    # 检查向量表（需加载扩展）
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        vec_cols = [r["name"] for r in conn.execute("PRAGMA table_info(memories_vec)").fetchall()]
        print(f"memories_vec 字段: {vec_cols}")
    except Exception as e:
        print(f"向量表检查失败（降级模式）: {e}")
```

运行：
```powershell
python scripts/manual_migrate.py
```

---

## 5. 数据迁移（从旧版本升级）

### 5.1 旧数据兼容性

| 旧表状态 | 迁移后行为 |
|----------|------------|
| `memory_items` 有数据，无扩展字段 | ALTER 后旧数据 `access_count=0`、`last_accessed=NULL`、`type=NULL`、`category=NULL` |
| `memory_fts` 有索引 | 保持不变，与新数据共存 |
| 无 `memories_vec` 表 | 创建空表，旧数据无向量（需后续补写） |
| 无 `memories_vec_failed` 表 | 创建空表 |

### 5.2 为旧数据补写向量（可选）

若需要为旧数据补向量，编写一次性脚本：

```python
# scripts/backfill_vectors.py
import asyncio
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.memory.adapters.holographic_adapter import HolographicAdapter

async def backfill(adapter, embed_func):
    """为所有旧数据补写向量"""
    adapter._embedding_func = embed_func  # 注入真实 embedding 模型
    with adapter._get_conn() as conn:
        rows = conn.execute("SELECT key, data FROM memory_items").fetchall()
    
    success, fail = 0, 0
    for row in rows:
        try:
            # embedding=None 触发回调生成
            await adapter.save_with_embedding(row["key"], row["data"], embedding=None)
            success += 1
        except Exception as e:
            print(f"补写失败 key={row['key']}: {e}")
            fail += 1
    
    print(f"补写完成: 成功 {success}, 失败 {fail}")
    # 等待后台线程完成
    import time; time.sleep(5)
    # 触发兜底表重放
    replayed = adapter.replay_vec_failed(max_items=1000)
    print(f"兜底表重放: {replayed} 条")

if __name__ == "__main__":
    adapter = HolographicAdapter(db_path="./data/memory/holographic.db", enable_cache=False)
    if not adapter._vec_available:
        print("[ERROR] 向量层不可用，无法补写")
        sys.exit(1)
    
    # TODO: 替换为真实 embedding 模型
    def mock_embed(text: str) -> list[float]:
        return [0.0] * 512
    
    asyncio.run(backfill(adapter, mock_embed))
```

---

## 6. 维度变更迁移（高级）

> ⚠️ **审查发现的缺口 A**：`CREATE VIRTUAL TABLE IF NOT EXISTS` 不会更新已存在表的维度。若 `_VEC_DIM` 修改，需手动迁移。

### 6.1 维度变更步骤

```python
# scripts/migrate_vec_dimension.py
"""从旧维度迁移到新维度（会清空向量表，需重新生成 embedding）"""
import sqlite3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.memory.adapters.holographic_adapter import HolographicAdapter

OLD_DIM = 512
NEW_DIM = 768  # 修改为目标维度
DB_PATH = "./data/memory/holographic.db"

def migrate_dimension():
    # 1. 备份
    import shutil
    backup = f"{DB_PATH}.dim_migrate.bak"
    shutil.copy(DB_PATH, backup)
    print(f"[1/4] 已备份: {backup}")
    
    # 2. DROP 旧向量表
    with sqlite3.connect(DB_PATH) as conn:
        conn.enable_load_extension(True)
        try:
            import sqlite_vec
            sqlite_vec.load(conn)
        except Exception as e:
            print(f"[ERROR] sqlite-vec 不可用: {e}")
            return
        conn.execute("DROP TABLE IF EXISTS memories_vec")
        conn.execute("DELETE FROM memories_vec_failed")  # 旧维度兜底记录也清空
        conn.commit()
    print(f"[2/4] 已 DROP 旧向量表（dim={OLD_DIM}）")
    
    # 3. 修改类常量（需手动编辑 holographic_adapter.py 的 _VEC_DIM）
    print(f"[3/4] 请手动将 _VEC_DIM 从 {OLD_DIM} 改为 {NEW_DIM}，然后按 Enter 继续")
    input()
    
    # 4. 重新初始化（自动创建新维度表）
    adapter = HolographicAdapter(db_path=DB_PATH, enable_cache=False)
    print(f"[4/4] 新向量表就绪: vec_available={adapter._vec_available}")
    
    # 5. 运行 §5.2 backfill 脚本重新生成向量
    print("下一步：运行 scripts/backfill_vectors.py 重新生成向量")

if __name__ == "__main__":
    migrate_dimension()
```

---

## 7. 降级路径处理

### 7.1 自动降级触发条件

`_init_vec_table` 三级防线任一失败均触发降级：

| 失败点 | 触发条件 | 降级动作 |
|--------|----------|----------|
| `import sqlite_vec` | 模块未安装 | `_vec_available=False`，return |
| `sqlite_vec.load(conn)` | 扩展文件损坏/权限不足 | 尝试原生 load_extension |
| `conn.load_extension('sqlite_vec')` | ENABLE_LOAD_EXTENSION 未启用 | `_vec_available=False`，return |
| `CREATE VIRTUAL TABLE` | DDL 失败（维度非法等） | 外层 except 兜底，`_vec_available=False` |
| 任意未预期异常 | - | 外层 except 兜底，`_vec_available=False` |

### 7.2 降级模式行为契约

| 操作 | 正常模式 | 降级模式 |
|------|----------|----------|
| `save()` | 主表 + FTS 同事务 | 主表 + FTS 同事务（不变） |
| `save_with_embedding()` | 主表+FTS + 异步向量写入 | 主表+FTS（跳过向量层） |
| `search()` | FTS5 + BM25 | FTS5 + BM25（不变） |
| `search_vector()` | KNN 查询 | 返回 `[]`，不抛异常 |
| `clear()` | 清空三表 + 兜底表 | 清空主表+FTS+兜底表（跳过向量表） |

### 7.3 运行时降级（扩展初始化后失效）

若 `_vec_available=True` 但运行时扩展持续不可用：
- `search_vector`: try-except 兜底返回 `[]`
- `_write_vec_row`: 通过 `_retry_vec_write` 重试 3 次后写入兜底表
- `replay_vec_failed`: 后台补偿重放

> ⚠️ **审查发现的缺口 D**：当前无熔断机制，连续失败不会自动设 `_vec_available=False`。建议未来引入失败计数器。

---

## 8. 回滚步骤

### 8.1 完全回滚（恢复到迁移前）

```powershell
# 1. 恢复备份
Copy-Item .\data\memory\holographic.db.bak.* .\data\memory\holographic.db -Force

# 2. 回滚代码
git revert 96697c7a
```

### 8.2 部分回滚（保留主表+FTS，仅移除向量层）

```sql
-- 手动删除向量相关表
DROP TABLE IF EXISTS memories_vec;
DROP TABLE IF EXISTS memories_vec_failed;

-- 可选：移除主表扩展字段（SQLite 不支持 DROP COLUMN，需重建表）
-- 建议保留字段，不影响功能
```

### 8.3 代码回滚后行为

旧版本代码读取新 schema 数据库：
- `memory_items` 多出的 `access_count` 等字段：旧代码不查询，无影响
- `memories_vec` / `memories_vec_failed` 表：旧代码不访问，无影响
- `memory_fts` 结构未变：正常工作

**结论**：迁移是**向前兼容**的，代码回滚后旧版本仍可正常运行新 schema 数据库。

---

## 9. 迁移验证

### 9.1 自动化验证脚本

```powershell
# 1. 降级路径验证（35 项契约不变量）
python scripts/verify_vec_degradation.py

# 2. 三表一致性集成测试（5 场景）
python scripts/run_tlm_vec_integration.py

# 3. 单元测试
python -m pytest tests/unit/test_tlm_memory_store.py -v
```

**期望结果**：
- 降级契约：35/35 PASS
- 集成测试：5/5 PASS
- 单元测试：17/17 PASS

### 9.2 手动验证清单

```python
# scripts/verify_migration.py
import sqlite3, sqlite_vec
from agent.memory.adapters.holographic_adapter import HolographicAdapter

DB_PATH = "./data/memory/holographic.db"
adapter = HolographicAdapter(db_path=DB_PATH, enable_cache=False)

# 检查 1: 三表存在
with sqlite3.connect(DB_PATH) as conn:
    conn.row_factory = sqlite3.Row
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    assert "memory_items" in tables, "主表缺失"
    assert "memory_fts" in tables, "FTS 表缺失"
    assert "memories_vec_failed" in tables, "兜底表缺失"
    print("[1/4] 三表存在性: PASS")

# 检查 2: 主表扩展字段
with sqlite3.connect(DB_PATH) as conn:
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(memory_items)").fetchall()]
    for expected in ["access_count", "last_accessed", "type", "category"]:
        assert expected in cols, f"字段 {expected} 缺失"
    print("[2/4] 主表扩展字段: PASS")

# 检查 3: 向量表（若可用）
if adapter._vec_available:
    with sqlite3.connect(DB_PATH) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        vec_cols = [r["name"] for r in conn.execute("PRAGMA table_info(memories_vec)").fetchall()]
        assert "id" in vec_cols and "embedding" in vec_cols
        print(f"[3/4] 向量表字段: {vec_cols} PASS")
else:
    print("[3/4] 向量表降级模式: SKIP")

# 检查 4: 读写功能
import asyncio
asyncio.run(adapter.save("migration_test_key", "迁移验证内容", {"t": "verify"}))
results = asyncio.run(adapter.search("迁移验证", top_k=3))
assert len(results) >= 1
print("[4/4] 读写功能: PASS")

print("\n✅ 迁移验证全部通过")
```

运行：
```powershell
python scripts/verify_migration.py
```

---

## 10. 常见问题排查

### Q1: 日志显示 `sqlite-vec 扩展加载全部失败`

**可能原因**：
1. `sqlite-vec` 未安装 → `pip install sqlite-vec`
2. Python 版本不兼容 → 需要 Python ≥ 3.10
3. SQLite 编译时禁用了 `ENABLE_LOAD_EXTENSION` → 重新编译 SQLite 或使用 Python 适配器路径

**验证**：
```python
import sqlite3
conn = sqlite3.connect(":memory:")
print(hasattr(conn, 'enable_load_extension'))  # 应为 True
```

### Q2: `memories_vec` 表存在但 `_vec_available=False`

**可能原因**：首次初始化时扩展可用，后续运行时扩展失效。

**解决**：删除 `memories_vec` 表后重启，让 `_init_vec_table` 重新尝试：
```sql
DROP TABLE IF EXISTS memories_vec;
```

### Q3: 迁移后 `memory_items` 字段缺失

**可能原因**：`_migrate_schema_if_needed` 失败（查看日志 `migrate.failed`）。

**手动修复**：
```sql
ALTER TABLE memory_items ADD COLUMN access_count INTEGER DEFAULT 0;
ALTER TABLE memory_items ADD COLUMN last_accessed REAL;
ALTER TABLE memory_items ADD COLUMN type TEXT;
ALTER TABLE memory_items ADD COLUMN category TEXT;
```

### Q4: 兜底表 `memories_vec_failed` 数据持续增长

**原因**：向量写入持续失败，重放也失败。

**处理**：
```python
# 1. 检查失败原因
with adapter._get_conn() as conn:
    rows = conn.execute("SELECT key, error, retries FROM memories_vec_failed LIMIT 10").fetchall()
    for r in rows:
        print(f"key={r['key']}, error={r['error']}, retries={r['retries']}")

# 2. 手动重放
adapter.replay_vec_failed(max_items=1000)

# 3. 若无法恢复，清空兜底表（数据丢失风险）
# with adapter._get_conn() as conn:
#     conn.execute("DELETE FROM memories_vec_failed")
#     conn.commit()
```

---

## 11. 迁移决策树

```
是否首次部署？
├── 是 → 直接实例化 HolographicAdapter，自动完成全部迁移
└── 否 → 是否从旧版本升级？
    ├── 是 → 备份数据库 → 实例化触发迁移 → 运行 verify_migration.py
    │       └── 是否需要为旧数据补向量？
    │           ├── 是 → 运行 backfill_vectors.py
    │           └── 否 → 完成
    └── 否 → 是否需要变更向量维度？
        ├── 是 → 运行 migrate_vec_dimension.py → 修改 _VEC_DIM → backfill
        └── 否 → 无需迁移
```

---

## 12. 约束遵循

- 【不易】主表 + FTS 事务完整性不变；`save`/`search` 接口签名未变
- 【变易】向量层可选，`_VEC_DIM` 可调整（需配合维度变更脚本）
- 【简易】迁移全自动，幂等可重复执行；降级路径三级 fallback 直白

**关联约束**（来自 project_memory.md）：
- 持锁禁 I/O：`_migrate_schema_if_needed` 锁内仅 PRAGMA + DDL，无外部回调
- 单文件 .db 部署：三表在同一 SQLite 文件
- 事务性写入：主表 + FTS 同事务；向量写入异步独立事务
- 不破坏 HolographicAdapter 现有接口
- 不新建 MemoryStore 类
- 不引入 chromadb 依赖
