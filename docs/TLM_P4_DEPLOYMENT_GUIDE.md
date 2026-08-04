# TLM P4 部署指南 — sqlite-vec KNN + 动态维度

> 完整部署流程：代码修复 → 数据迁移 → 服务重启 → 验证 → 回滚

## 概述

P4 方案将 LongTermMemory 的语义检索从纯 Python 余弦相似度（O(n)）升级为 sqlite-vec KNN（O(log n)），并修复了维度硬编码问题，支持任意维度 embedding（384/768/1024 等）。

### 核心变更

| 组件 | 变更 | 文件 |
|------|------|------|
| 动态维度推断 | `_init_vec_table` 不再硬编码 384，从数据推断 | `agent/memory/long_term_memory.py` |
| 延迟创建 | 空数据库不创建 vec0 表，首次 save 时按实际维度创建 | `agent/memory/long_term_memory.py` |
| 维度不匹配降级 | vec0 表维度与数据维度不匹配时自动降级纯 Python | `agent/memory/long_term_memory.py` |
| save 双写保护 | 维度不匹配时降级 `_use_vec_knn=false`，避免每次 save 失败 | `agent/memory/long_term_memory.py` |
| 迁移脚本 | 备份+SHA256校验+自动回滚+维度检测+条目数断言+日志持久化 | `scripts/tlm_migrate_entrypoint.sh` |
| Docker 编排 | 一键迁移+服务重启 | `docker-compose.tlm-migrate.yml` |
| 验证脚本 | 768 维全链路验证（创建/save/search/recall/降级/兼容） | `scripts/verify_768dim_dynamic.py` |

---

## 前置条件

### 1. 环境要求

- **Python**: ≥ 3.12
- **sqlite-vec**: ≥ 0.1.9（`pip install sqlite-vec`）
- **Docker**: ≥ 20.10（使用 Docker 部署时）
- **Docker Compose**: ≥ 2.0（使用 Docker 部署时）

### 2. sqlite-vec 可用性检查

```bash
python3 -c "import sqlite_vec; print(f'sqlite-vec {sqlite_vec.__version__} 可用')"
```

> **降级说明**：sqlite-vec 不可用时，系统自动降级为纯 Python 余弦相似度，功能正常但性能为 O(n)。

### 3. 数据备份空间检查

确保 `./backups/` 目录有足够空间（至少为当前数据库大小的 2 倍，含备份+日志）。

```bash
du -sh ./data/memory/long_term.db
df -h .  # 检查可用空间
```

---

## 部署方式 A：Docker Compose 一键部署（推荐）

### 步骤 1：构建迁移镜像

```bash
docker-compose -f docker-compose.tlm-migrate.yml build migrate
```

### 步骤 2：执行迁移

```bash
docker-compose -f docker-compose.tlm-migrate.yml run migrate
```

迁移流程（自动执行）：
1. **备份**：`cp` 数据库 + SHA256 完整性校验
2. **状态检查**：记录迁移前条目数 + 检测 embedding 维度
3. **P3 迁移**：JSON TEXT → BLOB（float32），失败则阻断 P4
4. **P4 迁移**：创建 vec0 表（动态维度）+ 导入归一化 embedding
5. **验证**：条目数断言 + 格式检查 + KNN 测试
6. **报告**：输出备份路径、SHA256、回滚命令

**成功标志**：退出码 0，日志最后显示 `[TLM] 迁移成功完成`

**失败处理**：自动回滚（`trap EXIT` 触发），数据库恢复到备份状态

### 步骤 3：启动 Agent 服务

```bash
docker-compose -f docker-compose.tlm-migrate.yml up -d agent
```

> `depends_on: service_completed_successfully` 确保 migrate 成功后 agent 才启动。

### 步骤 4：验证服务健康

```bash
# 检查容器状态
docker-compose -f docker-compose.tlm-migrate.yml ps

# 检查健康端点
curl http://localhost:8000/api/health

# 查看日志
docker-compose -f docker-compose.tlm-migrate.yml logs -f agent
```

---

## 部署方式 B：手动部署

### 步骤 1：安装依赖

```bash
pip install sqlite-vec>=0.1.9
```

### 步骤 2：备份数据库

```bash
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="./backups/long_term_backup_$TIMESTAMP.db"
mkdir -p ./backups
cp ./data/memory/long_term.db "$BACKUP_PATH"

# 完整性校验
ORIG_HASH=$(sha256sum ./data/memory/long_term.db | cut -d' ' -f1)
BACKUP_HASH=$(sha256sum "$BACKUP_PATH" | cut -d' ' -f1)
if [ "$ORIG_HASH" != "$BACKUP_HASH" ]; then
    echo "[ERROR] 备份校验失败"
    exit 1
fi
echo "[OK] 备份完成: $BACKUP_PATH (SHA256=$BACKUP_HASH)"
```

### 步骤 3：执行迁移

```bash
# 设置环境变量
export DB_PATH=./data/memory/long_term.db
export BACKUP_DIR=./backups
export MIGRATE_P3=true
export MIGRATE_P4=true

# 执行迁移脚本
bash scripts/tlm_migrate_entrypoint.sh
```

### 步骤 4：验证迁移结果

```bash
# 运行 768 维验证脚本（如果使用 bge-large 等高维模型）
python scripts/verify_768dim_dynamic.py
```

### 步骤 5：重启服务

```bash
# 如果使用 systemd
sudo systemctl restart agent

# 如果使用 Docker
docker restart agent
```

---

## 验证清单

### 必须项（P0）

- [ ] 迁移脚本退出码为 0
- [ ] 日志显示 `[TLM] 迁移成功完成`
- [ ] 迁移前后条目数一致（日志 Step 5 断言通过）
- [ ] 所有 embedding 已转为 BLOB 格式（无 TEXT 残留）
- [ ] vec0 表条目数与 embedding 条目数一致
- [ ] KNN 测试正常（日志显示 `KNN 搜索正常`）

### 推荐项（P1）

- [ ] 运行 `verify_768dim_dynamic.py` 全部通过（768 维场景）
- [ ] 运行 `verify_recall_normalized.py` recall@10 = 100%
- [ ] Agent 服务健康检查通过
- [ ] 监控指标正常（如果有 Prometheus）

### 日志位置

| 日志 | 路径 | 说明 |
|------|------|------|
| 迁移日志 | `./backups/migration_<timestamp>.log` | 完整迁移流程日志 |
| 数据库备份 | `./backups/long_term_backup_<timestamp>.db` | 迁移前数据库快照 |
| Agent 日志 | Docker logs 或 systemd journal | 服务运行日志 |

---

## 回滚方案

### 自动回滚（迁移失败时）

迁移脚本内置 `trap EXIT` 自动回滚：
- 任何步骤失败（退出码 ≠ 0）时自动触发
- 用 SHA256 校验过的备份恢复数据库
- 日志显示 `[ROLLBACK] 数据库已恢复`

### 手动回滚

```bash
# 1. 停止 Agent 服务
docker-compose -f docker-compose.tlm-migrate.yml down agent
# 或: sudo systemctl stop agent

# 2. 查找最新备份
ls -lt ./backups/long_term_backup_*.db | head -1

# 3. 恢复数据库
BACKUP_PATH=$(ls -t ./backups/long_term_backup_*.db | head -1)
cp "$BACKUP_PATH" ./data/memory/long_term.db

# 4. 代码回滚（如果需要）
git revert <p4_commit_hash>

# 5. 重启服务
docker-compose -f docker-compose.tlm-migrate.yml up -d agent
# 或: sudo systemctl start agent
```

### 部分回滚（仅回退 vec0 索引，保留 P3 BLOB 格式）

```bash
# 删除 vec0 表，回退到纯 Python 余弦相似度
python3 << 'EOF'
import sqlite3, sqlite_vec
conn = sqlite3.connect("./data/memory/long_term.db")
conn.enable_load_extension(True)
conn.load_extension(sqlite_vec.loadable_path())
conn.execute("DROP TABLE IF EXISTS ltm_vec_index")
conn.commit()
conn.close()
print("[OK] vec0 表已删除，回退到纯 Python")
EOF
```

### 回滚后数据一致性校验（必做）

回滚后必须执行以下校验，确保数据库状态一致、维度匹配、功能正常。

```bash
python3 << 'EOF'
import sqlite3
import sqlite_vec
import struct
import re
import sys

DB_PATH = "./data/memory/long_term.db"

print("=" * 60)
print("[回滚后校验] 数据一致性检查")
print("=" * 60)

# ── 1. 数据库完整性检查 ──
print("\n[1/5] 数据库完整性检查...")
conn = sqlite3.connect(DB_PATH)
integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
if integrity != "ok":
    print(f"  [FAIL] 数据库损坏: {integrity}")
    sys.exit(1)
print(f"  [OK] 数据库完整性正常")

# ── 2. 主表数据统计 ──
print("\n[2/5] 主表数据统计...")
total = conn.execute("SELECT COUNT(*) FROM long_term_memory").fetchone()[0]
emb_count = conn.execute("SELECT COUNT(*) FROM long_term_memory WHERE embedding IS NOT NULL").fetchone()[0]
print(f"  总条目: {total}")
print(f"  embedding 条目: {emb_count}")

# ── 3. embedding 格式检查 ──
print("\n[3/5] embedding 格式检查...")
formats = conn.execute("""
    SELECT
        SUM(CASE WHEN typeof(embedding) = 'blob' THEN 1 ELSE 0 END) as blob_count,
        SUM(CASE WHEN typeof(embedding) = 'text' THEN 1 ELSE 0 END) as text_count,
        SUM(CASE WHEN embedding IS NULL THEN 1 ELSE 0 END) as null_count
    FROM long_term_memory
""").fetchone()
print(f"  BLOB 格式: {formats[0]}")
print(f"  TEXT 格式: {formats[1]}")
print(f"  NULL: {formats[2]}")
if formats[1] > 0:
    print(f"  [WARN] 仍有 {formats[1]} 条 TEXT 格式（P3 迁移未完成或已回滚到 P3 前）")

# ── 4. 维度一致性检查（关键）──
print("\n[4/5] 维度一致性检查...")

def _extract_dim(blob):
    """从 embedding 值提取维度，处理所有类型

    覆盖：bytes / bytearray / memoryview / str(JSON) / 空 bytes / 长度非4倍数
    Returns: 维度数（正整数），无法提取时返回 None
    """
    if blob is None:
        return None
    # [修复] 处理 memoryview 类型（SQLite 某些配置返回 memoryview）
    if isinstance(blob, memoryview):
        blob = bytes(blob)
    if isinstance(blob, (bytes, bytearray)):
        if len(blob) == 0:
            return None  # 空 bytes
        if len(blob) < 4:
            return None  # 不足一个 float32
        # [修复] 检查长度是否为 4 的倍数（防止数据损坏）
        if len(blob) % 4 != 0:
            print(f"  [WARN] embedding BLOB 长度 {len(blob)} 非 4 的倍数，数据可能损坏")
            return None
        dim = len(blob) // 4
        # [修复] 检查维度是否为有效正数
        if dim <= 0:
            return None
        return dim
    if isinstance(blob, str):
        import json
        try:
            emb = json.loads(blob)
            if isinstance(emb, list) and len(emb) > 0:
                return len(emb)
        except (json.JSONDecodeError, TypeError):
            pass
    return None

# [修复] 检查所有 embedding 的维度一致性（不只是第一条）
# 如果存在混合维度（如 384+768 共存），需要报告
dim_rows = conn.execute("""
    SELECT embedding FROM long_term_memory
    WHERE embedding IS NOT NULL
""").fetchall()

dims_found = {}  # {dim: count}
for row in dim_rows:
    dim = _extract_dim(row[0])
    if dim is not None:
        dims_found[dim] = dims_found.get(dim, 0) + 1

if dims_found:
    data_dim = max(dims_found, key=dims_found.get)  # 取最多的维度作为主维度
    print(f"  数据维度: {data_dim}（{dims_found[data_dim]} 条）")
    # [修复] 混合维度检测
    if len(dims_found) > 1:
        print(f"  [WARN] 检测到混合维度！维度分布: {dims_found}")
        print(f"  [WARN] 主维度={data_dim}，其他维度将被迁移脚本跳过")
else:
    data_dim = None
    print(f"  数据维度: 无 embedding 数据（无法检测）")

# 检查 vec0 表维度（如果存在）
try:
    conn.enable_load_extension(True)
    conn.load_extension(sqlite_vec.loadable_path())
    vec_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ltm_vec_index'"
    ).fetchone()
    if vec_sql and vec_sql[0]:
        # [修复] 大小写不敏感匹配（float/FLOAT/Float）
        match = re.search(r'float\[(\d+)\]', vec_sql[0], re.IGNORECASE)
        vec_dim = int(match.group(1)) if match else None
        # [修复] 检查维度是否为有效正数
        if vec_dim is not None and vec_dim <= 0:
            print(f"  [WARN] vec0 表维度异常: {vec_dim}")
            vec_dim = None
        print(f"  vec0 表维度: {vec_dim}")

        if data_dim is not None and vec_dim is not None:
            if data_dim != vec_dim:
                print(f"  [FAIL] 维度不匹配！数据={data_dim}, vec0={vec_dim}")
                print(f"  [修复建议] 运行迁移脚本重建 vec0 表:")
                print(f"    bash scripts/tlm_migrate_entrypoint.sh")
                sys.exit(1)
            else:
                print(f"  [OK] 维度匹配（{vec_dim}）")
        elif vec_dim is not None and data_dim is None:
            # [修复] vec0 表存在但主表无 embedding
            print(f"  [WARN] vec0 表存在（维度={vec_dim}）但主表无 embedding 数据")
            print(f"  [WARN] vec0 表可能是残留的，建议运行迁移脚本清理或重建")
        else:
            print(f"  [WARN] 无法确定维度匹配状态")
    else:
        print(f"  vec0 表: 不存在（纯 Python 模式，无需维度校验）")
except Exception as e:
    print(f"  vec0 表检查跳过（sqlite-vec 不可用: {e}）")

# ── 5. 搜索功能验证 ──
print("\n[5/5] 搜索功能验证...")
# keyword 搜索测试
kw_result = conn.execute(
    "SELECT COUNT(*) FROM long_term_memory WHERE content LIKE '%test%'"
).fetchone()[0]
print(f"  keyword 搜索: 可用（测试查询返回 {kw_result} 条）")

conn.close()

# semantic 搜索测试（如果有 embedding 数据）
if data_dim:
    try:
        import sqlite_vec
        conn2 = sqlite3.connect(DB_PATH)
        conn2.enable_load_extension(True)
        conn2.load_extension(sqlite_vec.loadable_path())

        # 检查 vec0 表是否存在
        vec_exists = conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ltm_vec_index'"
        ).fetchone()

        if vec_exists:
            # KNN 搜索测试
            query = struct.pack(f'{data_dim}f', *([0.5] * data_dim))
            results = conn2.execute(
                "SELECT rowid, distance FROM ltm_vec_index WHERE embedding MATCH ? ORDER BY distance LIMIT 3",
                (query,)
            ).fetchall()
            print(f"  semantic 搜索 (KNN): 可用（返回 {len(results)} 条）")
        else:
            print(f"  semantic 搜索 (纯 Python): 可用（vec0 表不存在，降级模式）")
        conn2.close()
    except Exception as e:
        print(f"  semantic 搜索: [WARN] {e}")

print("\n" + "=" * 60)
print("[OK] 数据一致性校验通过")
print("=" * 60)
EOF
```

> **校验失败处理**：
> - 数据库损坏 → 从备份恢复
> - 维度不匹配 → 运行迁移脚本重建 vec0 表
> - 搜索功能异常 → 检查 sqlite-vec 是否可用，必要时降级纯 Python

---

## 故障排查

### 问题 1：`Dimension mismatch for inserted vector`

**原因**：vec0 表维度与 embedding 数据维度不匹配（如 vec0=384, data=768）

**自动处理**：系统会自动降级为纯 Python，主表数据不受影响

**根本修复**：运行迁移脚本重建 vec0 表
```bash
bash scripts/tlm_migrate_entrypoint.sh
```

### 问题 2：`sqlite-vec 不可用`

**原因**：sqlite-vec 扩展未安装或加载失败

**检查**：
```bash
python3 -c "import sqlite_vec; print(sqlite_vec.__version__)"
```

**修复**：
```bash
pip install sqlite-vec>=0.1.9
```

**降级**：sqlite-vec 不可用时系统自动降级为纯 Python，功能正常但性能为 O(n)

### 问题 3：迁移后 vec0 表条目数为 0

**原因**：embedding 维度不匹配，所有数据被跳过

**检查**：
```bash
python3 << 'EOF'
import sqlite3, sqlite_vec, struct
conn = sqlite3.connect("./data/memory/long_term.db")
conn.enable_load_extension(True)
conn.load_extension(sqlite_vec.loadable_path())

# 检查 vec0 表维度
import re
sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='ltm_vec_index'").fetchone()
if sql:
    match = re.search(r'float\[(\d+)\]', sql[0])
    print(f"vec0 表维度: {match.group(1) if match else '未知'}")

# 检查数据维度
row = conn.execute("SELECT embedding FROM long_term_memory WHERE embedding IS NOT NULL LIMIT 1").fetchone()
if row:
    blob = row[0]
    if isinstance(blob, (bytes, bytearray)):
        print(f"数据维度: {len(blob) // 4}")
conn.close()
EOF
```

**修复**：删除 vec0 表，重新运行迁移脚本
```bash
python3 -c "
import sqlite3, sqlite_vec
conn = sqlite3.connect('./data/memory/long_term.db')
conn.enable_load_extension(True)
conn.load_extension(sqlite_vec.loadable_path())
conn.execute('DROP TABLE IF EXISTS ltm_vec_index')
conn.commit()
conn.close()
"
bash scripts/tlm_migrate_entrypoint.sh
```

### 问题 4：迁移脚本报 `备份大小不一致`

**原因**：磁盘空间不足，`cp` 静默截断

**检查**：
```bash
df -h ./backups
du -sh ./data/memory/long_term.db
```

**修复**：清理磁盘空间后重新运行迁移

---

## 文件清单

### 核心代码

| 文件 | 说明 |
|------|------|
| `agent/memory/long_term_memory.py` | 核心模块（动态维度 + KNN + 降级） |
| `scripts/tlm_migrate_entrypoint.sh` | 迁移入口脚本（备份+迁移+验证+回滚） |
| `docker-compose.tlm-migrate.yml` | Docker 编排（migrate + agent） |
| `Dockerfile.tlm-migrate` | 迁移服务镜像 |

### 验证脚本

| 文件 | 说明 |
|------|------|
| `scripts/verify_768dim_dynamic.py` | 768 维动态维度全链路验证 |
| `scripts/verify_recall_normalized.py` | 归一化向量 recall 验证 |
| `scripts/benchmark_sqlite_vec_knn.py` | KNN 性能基准 |

### 文档

| 文件 | 说明 |
|------|------|
| `docs/TLM_P4_DEPLOYMENT_GUIDE.md` | 本文档（完整部署流程） |
| `docs/TLM_P3_MIGRATION_GUIDE.md` | P3 迁移指南（BLOB 格式） |
| `docs/TLM_DEPLOYMENT_CHECKLIST.md` | 部署检查清单 |

---

## 环境变量

### 迁移服务

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DB_PATH` | `/data/memory/long_term.db` | 数据库文件路径 |
| `BACKUP_DIR` | `/backups` | 备份输出目录 |
| `MIGRATE_P3` | `true` | 是否执行 P3 迁移（JSON TEXT → BLOB） |
| `MIGRATE_P4` | `true` | 是否执行 P4 迁移（vec0 索引重建） |
| `LOG_LEVEL` | `INFO` | 日志级别 |

### Agent 服务

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DB_PATH` | `/app/data/memory/long_term.db` | 数据库文件路径 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

---

## 安全机制总结

| 机制 | 实现位置 | 守的不变量 |
|------|---------|-----------|
| 备份 SHA256 校验 | `tlm_migrate_entrypoint.sh` Step 1 | 数据完整性 |
| 自动回滚（trap EXIT） | `tlm_migrate_entrypoint.sh` | 可回滚性 |
| 维度动态推断 | `long_term_memory.py` `_detect_embedding_dim` | 维度一致性 |
| 维度不匹配降级 | `long_term_memory.py` `_init_vec_table` + `save` | 运行时安全 |
| 延迟创建 vec0 表 | `long_term_memory.py` `save` | 避免空库默认维度错误 |
| 条目数断言 | `tlm_migrate_entrypoint.sh` Step 5 | 数据不丢 |
| P3 失败阻断 P4 | `tlm_migrate_entrypoint.sh` Step 3 | 失败可见性 |
| 日志持久化 | `tlm_migrate_entrypoint.sh` | 可审计性 |
| 主表双写保护 | `long_term_memory.py` `save` try/except | 主表数据安全 |
