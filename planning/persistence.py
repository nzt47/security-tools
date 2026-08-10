"""D9 持久化: SQLite 访问层（计划/任务/执行记录落库）

【不易】对外接口与既有 JSON 检查点语义对齐（save_plan_checkpoint /
  _load_plans_from_disk 调用方无感知切换后端）；执行记录落库失败仅告警不影响主流程。
【变易】支持 JSON 检查点迁移导入（幂等）；执行记录追加审计；任务表用 JSON payload
  保持模型字段扩展不迁移表结构。
【简易】sqlite3 标准库（零第三方依赖）；单连接 + 线程锁互斥（asyncio 并发写安全）。
"""

import json
import logging
import os
import sqlite3
import threading
from typing import Dict, Optional

from planning.models import Plan, PlanState

logger = logging.getLogger(__name__)

# 未完成计划状态（恢复过滤，与 JSON 检查点语义一致）
_RECOVERABLE_STATES = (
    PlanState.INIT, PlanState.DECOMPOSING,
    PlanState.READY, PlanState.EXECUTING, PlanState.PAUSED,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id            TEXT PRIMARY KEY,
    original_task TEXT NOT NULL,
    state         TEXT NOT NULL,
    progress      REAL NOT NULL DEFAULT 0,
    current_step  INTEGER NOT NULL DEFAULT 0,
    max_steps     INTEGER NOT NULL DEFAULT 20,
    result        TEXT,
    error         TEXT,
    context       TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_tasks (
    plan_id  TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    task_id  TEXT NOT NULL,
    payload  TEXT NOT NULL,
    PRIMARY KEY (plan_id, task_id)
);
CREATE TABLE IF NOT EXISTS execution_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id     TEXT NOT NULL,
    task_id     TEXT,
    action_type TEXT,
    tool_name   TEXT,
    success     INTEGER,
    output      TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_plans_state ON plans(state);
CREATE INDEX IF NOT EXISTS idx_exec_log_plan ON execution_log(plan_id, created_at);
"""


class PlanDB:
    """计划持久化访问层（SQLite）"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        """关闭连接（进程退出时调用）"""
        with self._lock:
            self._conn.close()

    # ── 计划落库 ──────────────────────────────────────────────────
    def upsert_plan(self, plan: Plan) -> None:
        """upsert 计划及其子任务（事务写入）"""
        d = plan.to_dict()
        with self._lock:
            self._conn.execute(
                """INSERT INTO plans (id, original_task, state, progress, current_step,
                                     max_steps, result, error, context, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     state=excluded.state, progress=excluded.progress,
                     current_step=excluded.current_step, result=excluded.result,
                     error=excluded.error, updated_at=excluded.updated_at""",
                (
                    d["id"], d["original_task"], d["state"], d["progress"],
                    d["current_step"], d["max_steps"],
                    json.dumps(d["result"], ensure_ascii=False) if d["result"] is not None else None,
                    d["error"],
                    json.dumps(d["context"], ensure_ascii=False),
                    d["created_at"], d["updated_at"],
                ),
            )
            self._conn.execute("DELETE FROM plan_tasks WHERE plan_id=?", (plan.id,))
            for t in d["tasks"]:
                self._conn.execute(
                    "INSERT INTO plan_tasks (plan_id, task_id, payload) VALUES (?,?,?)",
                    (plan.id, t["id"], json.dumps(t, ensure_ascii=False)),
                )
            self._conn.commit()

    def load_unfinished_plans(self) -> Dict[str, Plan]:
        """恢复未完成计划（状态过滤同 JSON 检查点语义）"""
        state_values = tuple(s.value for s in _RECOVERABLE_STATES)
        plans: Dict[str, Plan] = {}
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM plans WHERE state IN ({','.join('?' * len(state_values))})",
                state_values,
            ).fetchall()
            for row in rows:
                try:
                    d = {
                        "id": row["id"],
                        "original_task": row["original_task"],
                        "state": row["state"],
                        "progress": row["progress"],
                        "current_step": row["current_step"],
                        "max_steps": row["max_steps"],
                        "result": row["result"],
                        "error": row["error"],
                        "context": json.loads(row["context"]) if row["context"] else {},
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    }
                    task_rows = self._conn.execute(
                        "SELECT payload FROM plan_tasks WHERE plan_id=?", (row["id"],)
                    ).fetchall()
                    d["tasks"] = [json.loads(t["payload"]) for t in task_rows]
                    plans[row["id"]] = Plan.from_dict(d)
                except Exception as e:
                    logger.warning(f"[D9] 加载计划 {row['id']} 失败: {e}")
        return plans

    def count_plans(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) AS c FROM plans").fetchone()["c"]

    # ── 执行记录审计 ──────────────────────────────────────────────
    def append_execution_log(self, *, plan_id: str, task_id: Optional[str],
                             action_type: Optional[str], tool_name: Optional[str],
                             success: bool, output: Optional[str],
                             error: Optional[str]) -> None:
        """追加一条执行记录（审计/可追溯）"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO execution_log (plan_id, task_id, action_type, tool_name,"
                " success, output, error) VALUES (?,?,?,?,?,?,?)",
                (plan_id, task_id, action_type, tool_name, 1 if success else 0, output, error),
            )
            self._conn.commit()

    def count_execution_logs(self, plan_id: Optional[str] = None) -> int:
        with self._lock:
            if plan_id:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS c FROM execution_log WHERE plan_id=?", (plan_id,)
                ).fetchone()
            else:
                row = self._conn.execute("SELECT COUNT(*) AS c FROM execution_log").fetchone()
            return row["c"]

    # ── JSON 检查点迁移 ───────────────────────────────────────────
    def migrate_from_json(self, persist_dir: str) -> int:
        """将旧 JSON 检查点目录导入 SQLite（幂等：仅当库为空时执行）"""
        if not os.path.isdir(persist_dir):
            return 0
        if self.count_plans() > 0:
            return 0
        imported = 0
        for fname in os.listdir(persist_dir):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(persist_dir, fname), encoding="utf-8") as f:
                    plan = Plan.from_dict(json.load(f))
                self.upsert_plan(plan)
                imported += 1
            except Exception as e:
                logger.warning(f"[D9] JSON 迁移失败 {fname}: {e}")
        if imported:
            logger.info(f"[D9] 已从 JSON 检查点迁移 {imported} 个计划到 SQLite")
        return imported
