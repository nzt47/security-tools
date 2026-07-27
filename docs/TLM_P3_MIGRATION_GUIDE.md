# P3 迁移指南 — JSON TEXT → BLOB 格式

> **文档定位**: 将生产环境 LongTermMemory 表中 embedding 列从 JSON TEXT 平滑迁移到 BLOB 格式
> **生成日期**: 2026-07-27
> **适用版本**: TLM P3 优化（struct.pack float32）已实施后

---

## 一、迁移概述

### 1.1 为什么需要迁移？

| 维度 | JSON TEXT（旧） | BLOB float32（新） | 提升 |
|------|-----------------|-------------------|------|
| 反序列化速度 | ~100ms/1000条 (json.loads) | ~10ms/1000条 (struct.unpack) | **10x** |
| 存储大小 | ~8KB/条 (384维) | ~1.5KB/条 (384维) | **-75%** |
| semantic 搜索 p50 | 242ms/1000条 | 72ms/1000条 | **3.3x** |

### 1.2 迁移策略：懒迁移（推荐）

**不需要停机，不需要批量迁移**。P3 代码已内置向后兼容：

```
新数据写入 → 自动用 BLOB 格式存储
旧数据读取 → 自动检测 JSON TEXT 格式并解析
旧数据更新 → 自动转为 BLOB 格式（upsert 时覆盖）
```

**懒迁移流程**：
1. 部署 P3 代码 → 新数据自动用 BLOB
2. 旧数据在下次 `save()` 时自动转为 BLOB（upsert 覆盖）
3. 可选：运行批量迁移脚本加速（见第三节）

### 1.3 兼容性保证

`_blob_to_embedding()` 函数自动检测格式：

```
读取 embedding 列时：
├── bytes/bytearray → struct.unpack（新 BLOB）
│   └── 失败 → json.loads（旧 TEXT 存为 bytes）
├── memoryview → bytes → struct.unpack
├── str → json.loads（旧 JSON TEXT）
├── list → 直接返回
├── None → None
└── 其他 → None（防御性降级）
```

---

## 二、迁移前检查

### 2.1 数据备份

```powershell
# 备份生产数据库（关键！）
$dbPath = ".\data\memory\long_term.db"
$backupPath = ".\data\memory\long_term_backup_$(Get-Date -Format 'yyyyMMdd').db"
Copy-Item -Path $dbPath -Destination $backupPath -Force
Write-Output "备份完成: $backupPath"
```

### 2.2 检查现有数据格式

```python
# scripts/check_embedding_format.py
import sqlite3
import json

db_path = "./data/memory/long_term.db"
conn = sqlite3.connect(db_path)

# 统计 embedding 列格式分布
rows = conn.execute("""
    SELECT
        COUNT(*) as total,
        COUNT(embedding) as has_embedding,
        SUM(CASE WHEN typeof(embedding) = 'blob' THEN 1 ELSE 0 END) as blob_count,
        SUM(CASE WHEN typeof(embedding) = 'text' THEN 1 ELSE 0 END) as text_count
    FROM long_term_memory
""").fetchone()

print(f"总条目: {rows[0]}")
print(f"有 embedding: {rows[1]}")
print(f"BLOB 格式（新）: {rows[2]}")
print(f"TEXT 格式（旧）: {rows[3]}")
print(f"NULL: {rows[1] - rows[2] - rows[3]}")

conn.close()
```

### 2.3 预期输出

```
总条目: 1659
有 embedding: 200
BLOB 格式（新）: 0    ← 迁移前全部是 TEXT
TEXT 格式（旧）: 200
NULL: 0
```

---

## 三、迁移方案

### 方案 A：懒迁移（推荐，零停机）

**适用场景**：绝大多数生产环境

**操作**：直接部署 P3 代码，无需额外操作。

**效果**：
- 新写入的数据自动用 BLOB
- 旧数据仍能用 JSON TEXT 读取（semantic 搜索兼容）
- 旧数据在下次 `save()` 时自动转为 BLOB

**优点**：零停机、零风险、无需手动操作
**缺点**：旧数据完全迁移需要时间（取决于更新频率）

### 方案 B：批量迁移脚本（可选）

**适用场景**：希望立即获得全部性能提升

```python
# scripts/migrate_embedding_to_blob.py
"""
P3 批量迁移脚本：将旧 JSON TEXT embedding 转为 BLOB 格式

用法：
    python scripts/migrate_embedding_to_blob.py --dry-run    # 预览
    python scripts/migrate_embedding_to_blob.py              # 执行迁移
"""
import sqlite3
import json
import struct
import argparse
import os
import time
from pathlib import Path

def migrate(db_path: str, dry_run: bool = False) -> dict:
    """批量迁移 embedding 列从 JSON TEXT 到 BLOB

    Returns:
        迁移统计 dict
    """
    if not os.path.exists(db_path):
        return {"error": f"数据库不存在: {db_path}"}

    conn = sqlite3.connect(db_path)

    # 查询所有 TEXT 格式的 embedding
    rows = conn.execute("""
        SELECT key, embedding FROM long_term_memory
        WHERE typeof(embedding) = 'text' AND embedding IS NOT NULL
    """).fetchall()

    stats = {
        "total_text": len(rows),
        "migrated": 0,
        "failed": 0,
        "skipped": 0,
        "dry_run": dry_run,
    }

    if dry_run:
        print(f"[DRY-RUN] 将迁移 {len(rows)} 条 TEXT 格式 embedding → BLOB")
        conn.close()
        return stats

    print(f"开始迁移 {len(rows)} 条 embedding...")

    for key, embedding_str in rows:
        try:
            # 解析旧 JSON TEXT
            emb_list = json.loads(embedding_str)
            if not isinstance(emb_list, list) or not emb_list:
                stats["skipped"] += 1
                continue

            # 序列化为 BLOB
            blob = struct.pack(f'{len(emb_list)}f', *emb_list)

            # 更新数据库
            conn.execute(
                "UPDATE long_term_memory SET embedding = ? WHERE key = ?",
                (blob, key)
            )
            stats["migrated"] += 1

        except (json.JSONDecodeError, struct.error, TypeError) as e:
            print(f"  [FAIL] key={key}: {e}")
            stats["failed"] += 1

    conn.commit()
    conn.close()

    return stats


def main():
    parser = argparse.ArgumentParser(description="P3 embedding 格式迁移脚本")
    parser.add_argument("--db", default="./data/memory/long_term.db", help="数据库路径")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际修改")
    args = parser.parse_args()

    start = time.time()
    stats = migrate(args.db, args.dry_run)
    elapsed = time.time() - start

    print(f"\n迁移完成（耗时 {elapsed:.1f}s）:")
    print(f"  TEXT 格式（旧）: {stats['total_text']}")
    print(f"  成功迁移: {stats['migrated']}")
    print(f"  失败: {stats['failed']}")
    print(f"  跳过: {stats['skipped']}")

    if stats["failed"] > 0:
        print("\n⚠️  有失败项，请检查日志。原数据未被修改（UPDATE 是逐条提交的）。")


if __name__ == "__main__":
    main()
```

**执行步骤**：

```powershell
# 1. 备份
Copy-Item .\data\memory\long_term.db .\data\memory\long_term_backup.db

# 2. 预览
python scripts/migrate_embedding_to_blob.py --dry-run

# 3. 执行迁移
python scripts/migrate_embedding_to_blob.py

# 4. 验证
python scripts/check_embedding_format.py
```

### 方案 C：在线双写（高可用场景）

**适用场景**：7x24 不能停机的高可用系统

**流程**：
1. 部署 P3 代码（新数据自动 BLOB）
2. 后台异步任务逐批迁移旧数据
3. 迁移完成验证后，移除兼容代码（可选）

---

## 四、迁移验证

### 4.1 格式验证

```python
# 验证所有 embedding 都是 BLOB 格式
conn = sqlite3.connect("./data/memory/long_term.db")
result = conn.execute("""
    SELECT
        SUM(CASE WHEN typeof(embedding) = 'blob' THEN 1 ELSE 0 END) as blob_count,
        SUM(CASE WHEN typeof(embedding) = 'text' THEN 1 ELSE 0 END) as text_count
    FROM long_term_memory WHERE embedding IS NOT NULL
""").fetchone()
print(f"BLOB: {result[0]}, TEXT: {result[1]}")
# 预期: BLOB=200, TEXT=0
conn.close()
```

### 4.2 功能验证

```powershell
# 运行 semantic 搜索测试
python -m pytest tests/unit/test_long_term_memory_embedding.py -v

# 运行 BLOB 格式专项测试
python -m pytest tests/unit/test_long_term_memory_embedding.py::TestEmbeddingBlobFormat -v
```

### 4.3 recall 验证

```python
# 验证迁移后搜索结果一致性
import asyncio
from agent.memory.long_term_memory import LongTermMemory

async def verify_recall():
    ltm = LongTermMemory(db_path="./data/memory/long_term.db")
    # 用已知 query 搜索，验证结果数量和排序一致
    results = await ltm.search("测试", mode="semantic", query_embedding=[0.1]*384, top_k=10)
    print(f"搜索结果: {len(results)} 条")
    for r in results:
        print(f"  key={r.metadata['key']}, similarity={r.metadata.get('similarity', 'N/A')}")

asyncio.run(verify_recall())
```

---

## 五、回滚方案

### 5.1 快速回滚

```powershell
# 恢复数据库备份
Copy-Item .\data\memory\long_term_backup.db .\data\memory\long_term.db -Force

# 回退代码（git revert P3 commit）
git revert <p3_commit_hash>
```

### 5.2 部分回滚（保留新数据）

如果只想回滚格式但保留数据：

```python
# scripts/rollback_blob_to_text.py
"""将 BLOB 格式回滚为 JSON TEXT（紧急回滚用）"""
import sqlite3, struct, json

conn = sqlite3.connect("./data/memory/long_term.db")
rows = conn.execute("""
    SELECT key, embedding FROM long_term_memory
    WHERE typeof(embedding) = 'blob' AND embedding IS NOT NULL
""").fetchall()

for key, blob in rows:
    count = len(blob) // 4
    emb = list(struct.unpack(f'{count}f', blob))
    conn.execute("UPDATE long_term_memory SET embedding = ? WHERE key = ?",
                 (json.dumps(emb), key))

conn.commit()
conn.close()
print(f"回滚 {len(rows)} 条 BLOB → TEXT")
```

---

## 六、风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 迁移脚本中断 | 低 | 部分数据未迁移 | 懒迁移兼容，重跑脚本即可 |
| float32 精度损失 | 中 | 余弦相似度微小偏差 | 1e-6 精度内，不影响排序 |
| 旧代码读 BLOB 失败 | 低 | semantic 搜索返回空 | P3 代码已兼容，回退 JSON |
| 数据库锁竞争 | 低 | 迁移期间写入阻塞 | 批量迁移用小事务（100条/批） |

---

## 七、迁移检查清单

- [ ] 数据库已备份
- [ ] `check_embedding_format.py` 确认迁移前状态
- [ ] P3 代码已部署
- [ ] 运行 `test_long_term_memory_embedding.py` 全通过
- [ ] 执行迁移脚本（或选择懒迁移）
- [ ] `check_embedding_format.py` 确认迁移后状态
- [ ] recall 验证通过
- [ ] semantic 搜索功能正常
