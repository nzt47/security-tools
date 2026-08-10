# D9 持久化恢复：SQLite 落库技术方案

> 生成日期: 2026-08-11
> 状态: 技术方案（待排期实施）
> 关联: [test_planning_defect_d9.py](../../tests/unit/test_planning_defect_d9.py) · [capability 规格](../../tests/unit/test_planning_capability_baseline.py)

## 1. 现状与差距

| 维度 | 现状（已实现） | capability 规格（SQLite） |
|---|---|---|
| 持久化介质 | JSON 文件检查点（每计划一文件 `data/plans/{id}.json`） | SQLite 落库（计划/任务/执行记录） |
| 恢复能力 | 启动扫描目录恢复未完成计划（D9 P1 已满足） | 结构化查询 + 完整执行记录审计 |
| 执行记录 | 内存 `execution_history`（重启即失） | `execution_log` 落库可追溯 |
| 写入时机 | `plan()` 创建时 + 执行期 checkpoint | 同左，追加执行记录 |

结论：**P1 基本恢复能力（重启不丢未完成计划）已由 JSON 检查点满足且测试通过**；SQLite 是规格级增强——结构化查询、执行记录审计、事务一致。

## 2. 表结构设计

```sql
-- 计划主表
CREATE TABLE IF NOT EXISTS plans (
    id           TEXT PRIMARY KEY,
    original_task TEXT NOT NULL,
    state        TEXT NOT NULL,            -- PlanState 枚举值
    progress     REAL DEFAULT 0,
    current_step INTEGER DEFAULT 0,
    max_steps    INTEGER DEFAULT 20,
    result       TEXT,                     -- 序列化输出
    error        TEXT,
    context      TEXT,                     -- JSON 序列化
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- 子任务表
CREATE TABLE IF NOT EXISTS plan_tasks (
    plan_id      TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    task_id      TEXT NOT NULL,
    description  TEXT NOT NULL,
    status       TEXT NOT NULL,            -- TaskStatus 枚举值
    priority     INTEGER DEFAULT 0,
    task_type    TEXT DEFAULT 'tool_call',
    dependencies TEXT,                     -- JSON 数组
    params       TEXT,                     -- JSON 对象
    result       TEXT,
    error        TEXT,
    PRIMARY KEY (plan_id, task_id)
);

-- 执行记录表（审计/可追溯）
CREATE TABLE IF NOT EXISTS execution_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id    TEXT NOT NULL,
    task_id    TEXT,
    action_type TEXT,
    tool_name  TEXT,
    success    INTEGER,                    -- 0/1
    output     TEXT,
    error      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_exec_log_plan ON execution_log(plan_id, created_at);
```

## 3. 实施设计

### 3.1 新增 `planning/persistence.py`（SQLite 访问层）

- `PlanDB(path)`：连接管理（单连接 + `check_same_thread=False` + 写锁互斥），`sqlite3` 标准库（零第三方依赖）
- `init_schema()`：建表（幂等）
- `upsert_plan(plan)`：plans + plan_tasks 事务写入
- `load_unfinished_plans() -> Dict[str, Plan]`：按 plans/plan_tasks 重建 Plan 对象（状态过滤同现有 JSON 逻辑）
- `append_execution_log(record)`：追加执行记录
- `migrate_from_json(persist_dir)`：首次启动检测旧 JSON 检查点目录，逐文件导入后可选归档

### 3.2 core.py 改造（接口不变，切后端）

- `save_plan_checkpoint(plan)`：内部改调 `PlanDB.upsert_plan`（保留原签名与调用方语义）
- `_load_plans_from_disk()`：改调 `PlanDB.load_unfinished_plans`；检测到无库 + 有旧 JSON 时触发 `migrate_from_json`
- config.yaml：`planning.persist_db` 指向 DB 路径（默认 `data/plans/plans.db`）；保留 `persist_dir` 兼容旧 JSON

### 3.3 executor 埋点（执行记录落库）

- `_record_execution` 处追加 `PlanDB.append_execution_log`（action_type/tool/success/output/error）

### 3.4 测试

- D9 defect 测试扩展：断言 `plans.db` 存在 + `execution_log` 记录数 > 0
- capability `test_plan_persistence_and_recovery` 取消 skip 填断言：落库 → 新实例恢复未完成计划

## 4. 工作量评估

| 项 | 改动量 | 说明 |
|---|---|---|
| `planning/persistence.py` | ~180 行 | 建表/upsert/加载/迁移/日志（含注释） |
| `core.py` 后端切换 | ~30 行 | 两个方法内部改造 + config 读取 |
| `executor.py` 埋点 | ~15 行 | _record_execution 追加日志 |
| config.yaml | ~5 行 | persist_db 配置 |
| 测试扩展 | ~40 行 | d9 + capability 断言 |
| **合计** | **~270 行** | 新增 1 模块 + 3 文件改造 |

- 复杂度:中（单连接写入需加互斥，注意 GIL 下 asyncio 并发写）
- 依赖:零新增（`sqlite3` 标准库）
- 风险:迁移失败回退 JSON（迁移函数异常 → 保持 JSON 读取路径）;大 context 字段序列化开销
- 回归面:仅 core/executor 持久化路径，planning 全量套件可覆盖

## 5. 结论与建议

- D9 P1（重启恢复）已闭环（JSON 检查点，测试通过）；SQLite 为规格级增强，预计 ~270 行改动
- 建议排期顺序:persistence.py → core 切换 → executor 埋点 → 测试启用，单次提交可完成
