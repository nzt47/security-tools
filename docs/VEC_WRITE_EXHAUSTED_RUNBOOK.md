# VecWriteExhausted 故障排查手册

> 适用告警：`VecWriteExhausted` / `VecWriteExhaustedRateHigh`
> 相关代码：[holographic_adapter.py](../agent/memory/adapters/holographic_adapter.py) `_retry_vec_write` 方法
> 触发条件：向量写入重试 3 次均失败，数据已写入兜底表 `memories_vec_failed`

---

## 1. 告警概述

### 1.1 触发链路

```
save_with_embedding()
  └─ _retry_vec_write(key, embedding, max_retries=3)
       ├─ attempt 1: _write_vec_row() 失败 → sleep 1s
       ├─ attempt 2: _write_vec_row() 失败 → sleep 2s
       ├─ attempt 3: _write_vec_row() 失败 → sleep 4s
       └─ 重试耗尽：
            ├─ logger.error(action=vec.write_exhausted)   ← 告警来源
            ├─ _write_vec_failed() 写兜底表 memories_vec_failed
            └─ _record_vec_failure() 计入熔断（连续 5 次熔断）
```

### 1.2 影响评估

| 维度 | 影响 |
|------|------|
| 主表数据 | ✅ 不受影响（主表 `memory_items` 已写入） |
| FTS5 索引 | ✅ 不受影响（`memory_fts` 已同步） |
| 向量检索 | ⚠️ 该 key 暂时缺失，KNN 检索可能漏召回 |
| 系统可用性 | ✅ 降级路径仍可服务（FTS5 + BM25 兜底） |
| 后续风险 | ⚠️ 累计 5 次写入耗尽 → 触发熔断 `vec.circuit_break` |

---

## 2. 常见根因分类

### 根因 1：sqlite-vec 扩展加载失败（运行时）⭐ 高频

**症状**：`_write_vec_row` 抛 `sqlite3.OperationalError: no such module: vec0` 或 `AttributeError: 'sqlite3.Connection' object has no attribute 'enable_load_extension'`

**根因**：
- Python 适配器 `sqlite_vec.load(conn)` 在按需加载时失败（与初始化时不一致）
- SQLite 编译时未启用 `ENABLE_LOAD_EXTENSION` 选项
- thread-local 缓存的连接在 fork/线程迁移后丢失扩展加载状态

**验证**：
```bash
# 在容器内执行
python -c "
import sqlite3, sqlite_vec
conn = sqlite3.connect(':memory:')
conn.enable_load_extension(True)
sqlite_vec.load(conn)
print('sqlite-vec OK:', conn.execute('SELECT vec_version()').fetchone())
"
```

**修复**：
```bash
# 临时：重启服务，触发 _init_vec_table 重新加载
kubectl rollout restart deploy/digital-life -n production

# 根本：检查 sqlite-vec 包版本
pip show sqlite-vec
pip install --upgrade sqlite-vec
```

---

### 根因 2：SQLITE_BUSY 数据库锁竞争 ⭐ 高频

**症状**：`sqlite3.OperationalError: database is locked`，且 `busy_timeout=5000` 已配置但仍超时

**根因**：
- 多线程并发写入同一 `.db` 文件（如主应用 + ops-reporter 同时写）
- 长事务未提交（如批量 replay_vec_failed 持锁时间过长）
- WAL 模式未启用，读写互斥

**验证**：
```bash
# 检查当前锁状态
sqlite3 data/memory/holographic.db "PRAGMA journal_mode;"
# 推荐: wal
# 若为 delete/truncate，需要切换

# 检查 busy_timeout
sqlite3 data/memory/holographic.db "PRAGMA busy_timeout;"
# 应为 5000

# 查看活跃事务
sqlite3 data/memory/holographic.db "SELECT * FROM pragma_lock_status;"
```

**修复**：
```bash
# 临时：减少并发写入
# 根本：启用 WAL 模式（应用启动时执行一次）
sqlite3 data/memory/holographic.db "PRAGMA journal_mode=WAL;"
```

**代码层修复**：在 `_init_db` 中添加 `conn.execute("PRAGMA journal_mode=WAL")`（若项目约束允许）。

---

### 根因 3：磁盘空间不足或 IO 错误

**症状**：`sqlite3.OperationalError: disk I/O error` 或 `sqlite3.DatabaseError: database disk image is malformed`

**根因**：
- 磁盘空间耗尽（vec0 表 BLOB 写入需要空间）
- 存储后端异常（NFS/EBS 抖动）
- 文件系统损坏

**验证**：
```bash
# 检查磁盘空间
df -h /path/to/data/memory/

# 检查 inode 使用率
df -i /path/to/data/memory/

# 检查数据库完整性
sqlite3 data/memory/holographic.db "PRAGMA integrity_check;"
# 应返回 "ok"

# 检查 vec0 表
sqlite3 data/memory/holographic.db "SELECT COUNT(*) FROM memories_vec;"
```

**修复**：
```bash
# 清理空间
docker system prune -af
# 或清理旧日志
find logs/ -name "*.log" -mtime +7 -delete

# 数据库损坏需恢复
cp data/memory/holographic.db data/memory/holographic.db.bak
sqlite3 data/memory/holographic.db ".recover" > /tmp/recovered.sql
sqlite3 data/memory/holographic_new.db < /tmp/recovered.sql
```

---

### 根因 4：向量维度不匹配

**症状**：`sqlite3.OperationalError: float[] column dimension mismatch`，日志中伴随 `回调 embedding 维度不匹配: 期望 512, 实际 XXX`

**根因**：
- `_embedding_func` 回调返回的向量维度与 `_VEC_DIM=512` 不一致
- 模型升级（如从 768 维切换到 512 维）未同步更新 DDL

**验证**：
```bash
# 查看实际表结构
sqlite3 data/memory/holographic.db ".schema memories_vec"
# 应为: CREATE VIRTUAL TABLE memories_vec USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[512])

# 测试 embedding 回调
python -c "
from agent.embedding import get_embedding_func
fn = get_embedding_func()
vec = fn('test text')
print('dim:', len(vec) if vec else 'None')
"
```

**修复**：
```bash
# 临时：跳过该 key（_async_embed_and_write 已自动跳过，无需操作）
# 根本：修正 embedding 模型维度
# 1. 重建 vec 表
sqlite3 data/memory/holographic.db "DROP TABLE memories_vec;"
# 2. 重启服务，_init_vec_table 会按新维度重建
# 3. 触发向量重新生成
python -c "
from agent.memory.adapters.holographic_adapter import HolographicAdapter
a = HolographicAdapter('data/memory/holographic.db')
a.replay_vec_failed(max_items=10000)
"
```

---

### 根因 5：vec0 虚拟表损坏

**症状**：`sqlite3.DatabaseError: database disk image is malformed` 仅在访问 `memories_vec` 表时出现

**根因**：
- 进程崩溃时 vec0 表 BLOB 写入未完整提交
- 非正常关机导致 SQLite WAL 文件损坏
- vec0 扩展版本不兼容（升级后旧数据格式）

**验证**：
```bash
# 仅检查 vec 表
sqlite3 data/memory/holographic.db "SELECT COUNT(*) FROM memories_vec;" 2>&1
# 若报错即损坏

# 检查兜底表
sqlite3 data/memory/holographic.db "SELECT COUNT(*) FROM memories_vec_failed;"
```

**修复**：
```bash
# 完整重建流程
sqlite3 data/memory/holographic.db "DROP TABLE memories_vec;"
# 重启服务，_init_vec_table 重建空表
# 触发兜底表重放
python scripts/replay_vec_failed.py --all
```

---

### 根因 6：内存不足（serialize_float32 失败）

**症状**：`MemoryError` 或 `sqlite3.DataError: serialize_float32 failed`

**根因**：
- 大批量并发写入（如 replay_vec_failed 一次性重放 10000 条）
- 单条 embedding 异常大（如维度错误返回 10000+ 维）

**验证**：
```bash
# 查看进程内存
ps aux | grep python | grep -v grep
# 或容器内
cat /proc/1/status | grep VmRSS

# 查看兜底表堆积量
sqlite3 data/memory/holographic.db "SELECT COUNT(*) FROM memories_vec_failed;"
```

**修复**：
```bash
# 减少 replay 批量
python -c "
from agent.memory.adapters.holographic_adapter import HolographicAdapter
a = HolographicAdapter('data/memory/holographic.db')
# 改为小批量多次
for _ in range(100):
    n = a.replay_vec_failed(max_items=50)
    if n == 0: break
"
```

---

## 3. 标准排查流程

### 3.1 第一步：确认告警真实性

```bash
# 查看最近 1 小时的告警事件
grep "vec.write_exhausted" logs/agent.log | tail -20

# 查看当日运维日报
ls docs/ops_daily/
cat docs/ops_daily/$(date +%Y-%m-%d).md | grep -A 5 "vec.write_exhausted"
```

### 3.2 第二步：定位失败 key 与错误信息

```bash
# 提取告警中的 key 和错误
grep "vec.write_exhausted" logs/agent.log | \
  grep -oE 'key=[a-zA-Z0-9_]+' | sort -u

# 查看具体错误堆栈（告警前 3 行）
grep -B 3 "vec.write_exhausted" logs/agent.log | tail -30
```

### 3.3 第三步：检查兜底表堆积

```bash
sqlite3 data/memory/holographic.db <<'EOF'
.mode column
.headers on
SELECT
    COUNT(*) AS total,
    SUM(CASE WHEN created_at < strftime('%s','now')-86400 THEN 1 ELSE 0 END) AS older_than_1d,
    SUM(CASE WHEN created_at < strftime('%s','now')-604800 THEN 1 ELSE 0 END) AS older_than_7d
FROM memories_vec_failed;
EOF
```

### 3.4 第四步：按根因表对照排查

按 [第 2 节](#2-常见根因分类) 的 6 类根因逐一排查：
1. sqlite-vec 加载测试 → 根因 1
2. 数据库锁状态 → 根因 2
3. 磁盘空间 + 完整性 → 根因 3
4. 向量维度 → 根因 4
5. vec0 表查询 → 根因 5
6. 内存 + 兜底表堆积 → 根因 6

### 3.5 第五步：执行修复 + 验证

```bash
# 修复后触发兜底表重放
python -c "
from agent.memory.adapters.holographic_adapter import HolographicAdapter
a = HolographicAdapter('data/memory/holographic.db')
n = a.replay_vec_failed(max_items=100)
print(f'重放成功: {n} 条')
"

# 验证兜底表清空
sqlite3 data/memory/holographic.db "SELECT COUNT(*) FROM memories_vec_failed;"
# 应为 0

# 验证向量检索恢复
python -c "
from agent.memory.adapters.holographic_adapter import HolographicAdapter
import asyncio
a = HolographicAdapter('data/memory/holographic.db')
r = asyncio.run(a.search_vector([0.1]*512, top_k=3))
print(f'检索结果: {len(r)} 条')
"
```

---

## 4. 预防措施

### 4.1 监控告警

| 指标 | 阈值 | 处理 |
|------|------|------|
| `vec.write_exhausted` 5min 内 >0 | P1 | 立即排查 |
| `vec.write_exhausted` 10min 内 >3 | P1 | 系统性问题 |
| `memories_vec_failed` 行数 >100 | 预警 | 触发批量重放 |
| `_vec_fail_count` 10min 内 >20 | 预警 | 接近熔断阈值 |

### 4.2 定期维护

```bash
# 每日：cron 触发兜底表重放（添加到 crontab）
0 3 * * * cd /path/to/agent && python scripts/replay_vec_failed.py --max-items 1000

# 每周：数据库完整性检查
0 5 * * 0 sqlite3 data/memory/holographic.db "PRAGMA integrity_check;" >> logs/integrity.log

# 每月：vec 表统计
0 5 1 * * sqlite3 data/memory/holographic.db "SELECT COUNT(*) FROM memories_vec;" >> logs/vec_stats.log
```

### 4.3 容量规划

- 单条向量占用：512 维 × 4 字节 = 2KB
- 100 万条向量：约 2GB（不含索引开销）
- vec0 索引开销：约 1.5-2x 数据量
- 兜底表建议监控阈值：>1000 行触发告警

---

## 5. 附录

### 5.1 相关代码位置

| 位置 | 说明 |
|------|------|
| [holographic_adapter.py:734](../agent/memory/adapters/holographic_adapter.py) | `_retry_vec_write` 重试逻辑 |
| [holographic_adapter.py:769](../agent/memory/adapters/holographic_adapter.py) | `_write_vec_row` 单次写入 |
| [holographic_adapter.py:783](../agent/memory/adapters/holographic_adapter.py) | `_write_vec_failed` 兜底表写入 |
| [holographic_adapter.py:814](../agent/memory/adapters/holographic_adapter.py) | `replay_vec_failed` 重放兜底表 |

### 5.2 相关文档

- [TLM 熔断器架构说明](TLM_CIRCUIT_BREAKER_ARCH.md)
- [埋点审查报告](CIRCUIT_BREAKER_METRICS_AUDIT.md)
- [Prometheus 告警规则](../deploy/prometheus/circuit_breaker_alerts.yml)
- [Helm Chart 部署](../deploy/helm/tlm-ops-reporter/README.md)

### 5.3 SQL 速查

```sql
-- 查看兜底表详情
.mode column
.headers on
SELECT key, error, datetime(created_at, 'unixepoch', 'localtime') AS time, retries
FROM memories_vec_failed
ORDER BY created_at DESC
LIMIT 20;

-- 查看向量表统计
SELECT
    (SELECT COUNT(*) FROM memories_vec) AS vec_count,
    (SELECT COUNT(*) FROM memories_vec_failed) AS failed_count,
    (SELECT COUNT(*) FROM memory_items) AS main_count;

-- 检查熔断状态（通过日志反推）
-- vec.circuit_break 出现 = 已熔断
-- vec.circuit_reset 出现 = 已恢复
```

### 5.4 应急联系

- TLM 团队值班：#tlm-oncall（Slack）
- 升级路径：P1 告警未处理 15 分钟 → 升级 P0 → 电话值班
