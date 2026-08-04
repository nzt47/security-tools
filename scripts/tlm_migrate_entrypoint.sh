#!/bin/bash
# TLM 迁移入口脚本 — 一键执行 P3+P4 迁移
#
# 流程：
# 1. 数据库备份 + SHA256 完整性校验
# 2. 迁移前状态检查 + embedding 维度自动检测
# 3. P3 迁移：JSON TEXT → BLOB（struct.pack float32）
# 4. P4 迁移：创建 vec0 虚拟表 + 导入归一化 embedding
# 5. 迁移后验证 + 条目数断言（防丢数据）
# 6. 输出迁移报告
#
# 安全机制：
# - trap EXIT 自动回滚（任何失败都恢复备份）
# - SHA256 校验确保备份完整
# - 维度从实际数据推断，不硬编码
# - 迁移前后条目数断言
# - P3 失败阻断 P4
# - 日志持久化到 backups/migration_<timestamp>.log

set -e

# ── 配置 ──
DB_PATH="${DB_PATH:-/data/memory/long_term.db}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
MIGRATE_P3="${MIGRATE_P3:-true}"
MIGRATE_P4="${MIGRATE_P4:-true}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$BACKUP_DIR/migration_$TIMESTAMP.log"
STATE_FILE="/tmp/tlm_state_$TIMESTAMP.env"

mkdir -p "$BACKUP_DIR"

# 日志重定向：stdout+stderr 同时输出到终端和日志文件
exec > >(tee -a "$LOG_FILE") 2>&1

# ── 回滚机制 ──
# [不易] 默认失败：只有走完全部步骤才标记成功，任何中途退出都触发回滚
MIGRATION_FAILED=true
ROLLBACK_NEEDED=false
BACKUP_PATH=""
BACKUP_HASH=""

rollback() {
    local exit_code=$?
    if [ "$MIGRATION_FAILED" = "true" ] && [ "$ROLLBACK_NEEDED" = "true" ]; then
        echo ""
        echo "[ROLLBACK] 检测到迁移失败 (exit=$exit_code)，正在回滚数据库..."
        if [ -n "$BACKUP_PATH" ] && [ -f "$BACKUP_PATH" ]; then
            cp "$BACKUP_PATH" "$DB_PATH"
            echo "[ROLLBACK] 数据库已恢复: $BACKUP_PATH → $DB_PATH"
            echo "[ROLLBACK] 请检查日志 $LOG_FILE 后重新执行迁移"
        else
            echo "[ROLLBACK] 备份文件不存在，无法回滚！请手动检查数据库完整性"
        fi
    fi
    # 清理临时状态文件
    rm -f "$STATE_FILE"
    echo ""
    echo "[TLM] 迁移日志已保存: $LOG_FILE"
}
trap rollback EXIT

fail() {
    echo "[ERROR] $1"
    exit 1
}

echo "======================================================"
echo "[TLM] 迁移服务启动"
echo "  DB_PATH: $DB_PATH"
echo "  BACKUP_DIR: $BACKUP_DIR"
echo "  LOG_FILE: $LOG_FILE"
echo "  MIGRATE_P3: $MIGRATE_P3"
echo "  MIGRATE_P4: $MIGRATE_P4"
echo "  TIMESTAMP: $TIMESTAMP"
echo "======================================================"

# ── Step 0: 前置检查 ──
echo ""
echo "[Step 0] 前置检查..."

if [ ! -f "$DB_PATH" ]; then
    fail "数据库文件不存在: $DB_PATH"
fi
echo "  [OK] 数据库文件存在"

# 检查 sqlite-vec（P4 依赖）
if [ "$MIGRATE_P4" = "true" ]; then
    python3 -c "import sqlite_vec; print(f'  [OK] sqlite-vec {sqlite_vec.__version__}')" || {
        echo "  [WARN] sqlite-vec 不可用，P4 迁移将跳过"
        MIGRATE_P4=false
    }
fi

# ── Step 1: 数据库备份 + 完整性校验 ──
echo ""
echo "[Step 1] 数据库备份 + 完整性校验..."
BACKUP_PATH="$BACKUP_DIR/long_term_backup_$TIMESTAMP.db"
cp "$DB_PATH" "$BACKUP_PATH"

# 大小校验（防止 cp 静默截断）
ORIG_SIZE=$(stat -c%s "$DB_PATH")
BACKUP_SIZE=$(stat -c%s "$BACKUP_PATH")
if [ "$ORIG_SIZE" != "$BACKUP_SIZE" ]; then
    fail "备份大小不一致: orig=$ORIG_SIZE backup=$BACKUP_SIZE（可能磁盘空间不足）"
fi
echo "  [OK] 大小校验通过: $BACKUP_SIZE bytes"

# SHA256 校验（防止位腐烂/磁盘错误）
ORIG_HASH=$(sha256sum "$DB_PATH" | cut -d' ' -f1)
BACKUP_HASH=$(sha256sum "$BACKUP_PATH" | cut -d' ' -f1)
if [ "$ORIG_HASH" != "$BACKUP_HASH" ]; then
    fail "备份 SHA256 不一致: orig=$ORIG_HASH backup=$BACKUP_HASH"
fi
echo "  [OK] SHA256 校验通过: $BACKUP_HASH"

# 备份完成，后续失败可回滚
ROLLBACK_NEEDED=true

# ── Step 2: 迁移前状态检查 + 维度检测 ──
echo ""
echo "[Step 2] 迁移前状态检查 + 维度检测..."
export DB_PATH
python3 << 'EOF'
import sqlite3
import os
import json

db_path = os.environ["DB_PATH"]
conn = sqlite3.connect(db_path)

# 迁移前条目数（用于 Step 5 断言）
before_count = conn.execute("SELECT COUNT(*) FROM long_term_memory").fetchone()[0]
before_emb_count = conn.execute("SELECT COUNT(*) FROM long_term_memory WHERE embedding IS NOT NULL").fetchone()[0]
print(f"  迁移前总条目: {before_count}")
print(f"  迁移前 embedding 条目: {before_emb_count}")

# 格式分布
rows = conn.execute("""
    SELECT
        SUM(CASE WHEN typeof(embedding) = 'blob' THEN 1 ELSE 0 END) as blob_count,
        SUM(CASE WHEN typeof(embedding) = 'text' THEN 1 ELSE 0 END) as text_count
    FROM long_term_memory WHERE embedding IS NOT NULL
""").fetchone()
print(f"  BLOB 格式（P3 新）: {rows[0]}")
print(f"  TEXT 格式（旧 JSON）: {rows[1]}")

# [P4 补全] 维度自动检测：从第一条 embedding 推断，不硬编码 384
dim = 384  # 默认值（无数据时）
sample = conn.execute("SELECT embedding FROM long_term_memory WHERE embedding IS NOT NULL LIMIT 1").fetchone()
if sample:
    blob = sample[0]
    if isinstance(blob, (bytes, bytearray)) and len(blob) >= 4:
        # BLOB 格式：float32 = 4 bytes
        dim = len(blob) // 4
    elif isinstance(blob, str):
        # 旧 JSON TEXT 格式
        try:
            emb = json.loads(blob)
            if isinstance(emb, list) and emb:
                dim = len(emb)
        except json.JSONDecodeError:
            pass
print(f"  检测到 embedding 维度: {dim}")

# 检查 vec0 表是否存在
tables = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
has_vec_table = "ltm_vec_index" in tables
print(f"  vec0 表存在: {has_vec_table}")

conn.close()

# 通过临时文件传递状态（避免 heredoc 变量替换风险）
state_file = f"/tmp/tlm_state_{os.environ.get('TIMESTAMP', 'default')}.env"
# fallback：使用固定文件名
state_file = os.environ.get("STATE_FILE", "/tmp/tlm_state.env")
with open(state_file, "w") as f:
    f.write(f"DIM={dim}\n")
    f.write(f"BEFORE_COUNT={before_count}\n")
    f.write(f"BEFORE_EMB_COUNT={before_emb_count}\n")
EOF

# 读取状态（逐行解析，避免 source 的命令注入风险）
STATE_FILE="$STATE_FILE" python3 -c "
import os
state_file = os.environ['STATE_FILE']
with open(state_file) as f:
    for line in f:
        print(line.strip())
" > /tmp/tlm_state_read.txt || true

# 直接用 grep 读取（更简单可靠）
DIM=$(grep '^DIM=' "$STATE_FILE" | cut -d= -f2)
BEFORE_COUNT=$(grep '^BEFORE_COUNT=' "$STATE_FILE" | cut -d= -f2)
BEFORE_EMB_COUNT=$(grep '^BEFORE_EMB_COUNT=' "$STATE_FILE" | cut -d= -f2)
export DIM BEFORE_COUNT BEFORE_EMB_COUNT
rm -f /tmp/tlm_state_read.txt

if [ -z "$DIM" ] || [ -z "$BEFORE_COUNT" ]; then
    fail "状态读取失败: DIM=$DIM, BEFORE_COUNT=$BEFORE_COUNT"
fi
echo "  [OK] 状态已记录: DIM=$DIM, BEFORE_COUNT=$BEFORE_COUNT, BEFORE_EMB_COUNT=$BEFORE_EMB_COUNT"

# ── Step 3: P3 迁移：JSON TEXT → BLOB ──
if [ "$MIGRATE_P3" = "true" ]; then
    echo ""
    echo "[Step 3] P3 迁移：JSON TEXT → BLOB..."
    python3 << 'EOF'
import sqlite3
import json
import struct
import os
import sys

db_path = os.environ["DB_PATH"]
conn = sqlite3.connect(db_path)

rows = conn.execute("""
    SELECT key, embedding FROM long_term_memory
    WHERE typeof(embedding) = 'text' AND embedding IS NOT NULL
""").fetchall()

print(f"  待迁移 TEXT 条目: {len(rows)}")

migrated = 0
failed = 0
for key, embedding_str in rows:
    try:
        emb_list = json.loads(embedding_str)
        if not isinstance(emb_list, list) or not emb_list:
            continue
        blob = struct.pack(f'{len(emb_list)}f', *emb_list)
        conn.execute("UPDATE long_term_memory SET embedding = ? WHERE key = ?", (blob, key))
        migrated += 1
    except (json.JSONDecodeError, struct.error, TypeError) as e:
        print(f"    [FAIL] key={key}: {e}")
        failed += 1

conn.commit()
conn.close()

print(f"  [OK] P3 迁移完成: 成功={migrated}, 失败={failed}")

# [P5 补全] 失败阈值检查：有失败则阻断 P4，防止基于损坏数据构建索引
if failed > 0:
    print(f"  [BLOCK] P3 有 {failed} 条失败，阻断后续迁移以防止数据污染")
    print(f"  [BLOCK] 请检查失败条目并修复后重新执行迁移")
    sys.exit(1)
EOF
else
    echo ""
    echo "[Step 3] P3 迁移已跳过（MIGRATE_P3=false）"
fi

# ── Step 4: P4 迁移：创建 vec0 索引表 ──
if [ "$MIGRATE_P4" = "true" ]; then
    echo ""
    echo "[Step 4] P4 迁移：创建 vec0 索引表（维度=$DIM）..."
    export DIM
    python3 << 'EOF'
import sqlite3
import sqlite_vec
import struct
import os
import sys
import json

db_path = os.environ["DB_PATH"]
dim = int(os.environ["DIM"])

def normalize(vec):
    norm = sum(x * x for x in vec) ** 0.5
    if norm == 0:
        return vec
    return [x / norm for x in vec]

def blob_to_embedding(blob):
    if blob is None:
        return None
    if isinstance(blob, (bytes, bytearray)):
        if len(blob) == 0:
            return None
        try:
            count = len(blob) // 4
            return list(struct.unpack(f'{count}f', bytes(blob)))
        except struct.error:
            try:
                return json.loads(blob)
            except:
                return None
    if isinstance(blob, str):
        try:
            return json.loads(blob)
        except:
            return None
    return None

conn = sqlite3.connect(db_path)
conn.enable_load_extension(True)
conn.load_extension(sqlite_vec.loadable_path())

# [P4 补全] 检查 vec0 表是否存在 + 维度是否匹配
tables = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ltm_vec_index'").fetchall()]
if tables:
    # 试探维度：INSERT 测试数据，失败说明维度不匹配
    test_blob = struct.pack(f'{dim}f', *([0.0] * dim))
    try:
        conn.execute("INSERT INTO ltm_vec_index (rowid, embedding) VALUES (-1, ?)", (test_blob,))
        conn.execute("DELETE FROM ltm_vec_index WHERE rowid = -1")
        print(f"  [OK] vec0 表已存在且维度匹配 ({dim})")
    except Exception as e:
        print(f"  [WARN] vec0 表维度不匹配，DROP 重建: {e}")
        conn.execute("DROP TABLE ltm_vec_index")
        conn.execute(f"CREATE VIRTUAL TABLE ltm_vec_index USING vec0(embedding float[{dim}])")
else:
    conn.execute(f"CREATE VIRTUAL TABLE ltm_vec_index USING vec0(embedding float[{dim}])")
    print(f"  [OK] vec0 虚拟表已创建（维度={dim}）")

# 查询需要导入的 embedding（vec0 表中不存在的）
rows = conn.execute("""
    SELECT l.rowid, l.embedding
    FROM long_term_memory l
    LEFT JOIN ltm_vec_index v ON l.rowid = v.rowid
    WHERE l.embedding IS NOT NULL AND v.rowid IS NULL
""").fetchall()

print(f"  待导入 vec0 条目: {len(rows)}")

imported = 0
skipped = 0
for rowid, blob in rows:
    emb = blob_to_embedding(blob)
    if emb and len(emb) == dim:
        normalized = normalize(emb)
        norm_blob = struct.pack(f'{len(normalized)}f', *normalized)
        conn.execute(
            "INSERT INTO ltm_vec_index (rowid, embedding) VALUES (?, ?)",
            (rowid, norm_blob)
        )
        imported += 1
    else:
        skipped += 1
        if skipped <= 3:
            got = len(emb) if emb else 0
            print(f"    [SKIP] rowid={rowid}: 维度不匹配 (expected={dim}, got={got})")

conn.commit()

count = conn.execute("SELECT COUNT(*) FROM ltm_vec_index").fetchone()[0]
print(f"  [OK] P4 迁移完成: 导入={imported}, 跳过={skipped}, vec0 总数={count}")

if skipped > 0:
    print(f"  [WARN] {skipped} 条 embedding 因维度不匹配被跳过，请检查数据一致性")

conn.close()
EOF
else
    echo ""
    echo "[Step 4] P4 迁移已跳过（MIGRATE_P4=false）"
fi

# ── Step 5: 迁移后验证 + 条目数断言 ──
echo ""
echo "[Step 5] 迁移后验证 + 条目数断言..."
export BEFORE_COUNT
export BEFORE_EMB_COUNT
export DIM
python3 << 'EOF'
import sqlite3
import os
import sys
import struct

db_path = os.environ["DB_PATH"]
before_count = int(os.environ["BEFORE_COUNT"])
before_emb_count = int(os.environ["BEFORE_EMB_COUNT"])
dim = int(os.environ["DIM"])

conn = sqlite3.connect(db_path)

# [P4 补全] 条目数断言（防止迁移过程丢数据）
after_count = conn.execute("SELECT COUNT(*) FROM long_term_memory").fetchone()[0]
after_emb_count = conn.execute("SELECT COUNT(*) FROM long_term_memory WHERE embedding IS NOT NULL").fetchone()[0]

print(f"  条目数: before={before_count}, after={after_count}")
print(f"  embedding 条目数: before={before_emb_count}, after={after_emb_count}")

if before_count != after_count:
    print(f"  [FAIL] 总条目数不一致！可能丢数据（before={before_count}, after={after_count}）")
    sys.exit(1)
if before_emb_count != after_emb_count:
    print(f"  [FAIL] embedding 条目数不一致！可能丢数据（before={before_emb_count}, after={after_emb_count}）")
    sys.exit(1)
print(f"  [OK] 条目数断言通过")

# 验证 embedding 格式
rows = conn.execute("""
    SELECT
        SUM(CASE WHEN typeof(embedding) = 'blob' THEN 1 ELSE 0 END) as blob_count,
        SUM(CASE WHEN typeof(embedding) = 'text' THEN 1 ELSE 0 END) as text_count
    FROM long_term_memory WHERE embedding IS NOT NULL
""").fetchone()

print(f"  embedding 格式: BLOB={rows[0]}, TEXT={rows[1]}")
if rows[1] > 0:
    print(f"  [WARN] 仍有 {rows[1]} 条 TEXT 格式未迁移")
else:
    print(f"  [OK] 所有 embedding 已转为 BLOB")

conn.close()

# 验证 vec0 表
try:
    import sqlite_vec
    conn2 = sqlite3.connect(db_path)
    conn2.enable_load_extension(True)
    conn2.load_extension(sqlite_vec.loadable_path())

    vec_count = conn2.execute("SELECT COUNT(*) FROM ltm_vec_index").fetchone()[0]
    print(f"  vec0 表条目数: {vec_count}")

    if vec_count != before_emb_count:
        print(f"  [WARN] vec0 条目数 ({vec_count}) 与 embedding 条目数 ({before_emb_count}) 不一致")
    else:
        print(f"  [OK] vec0 条目数与 embedding 条目数一致")

    # KNN 测试（验证索引可用）
    query = struct.pack(f'{dim}f', *([0.5] * dim))
    results = conn2.execute(
        "SELECT rowid, distance FROM ltm_vec_index WHERE embedding MATCH ? ORDER BY distance LIMIT 5",
        (query,)
    ).fetchall()
    print(f"  KNN 测试: 返回 {len(results)} 条结果")
    print(f"  [OK] KNN 搜索正常")

    conn2.close()
except ImportError:
    print("  [SKIP] sqlite-vec 不可用，跳过 vec0 验证")
EOF

# ── Step 6: 迁移报告 ──
echo ""
echo "======================================================"
echo "[TLM] 迁移完成报告"
echo "======================================================"
echo "  备份路径: $BACKUP_PATH"
echo "  备份 SHA256: $BACKUP_HASH"
echo "  数据库路径: $DB_PATH"
echo "  日志文件: $LOG_FILE"
echo "  embedding 维度: $DIM"
echo "  条目数: $BEFORE_COUNT (迁移前后一致)"
echo ""
echo "回滚命令（如需手动回滚）:"
echo "  cp $BACKUP_PATH $DB_PATH"
echo "  git revert <p4_commit_hash>"
echo "======================================================"

# [不易] 走完全部步骤才标记成功，trap EXIT 不触发回滚
MIGRATION_FAILED=false
echo ""
echo "[TLM] 迁移成功完成"
exit 0
