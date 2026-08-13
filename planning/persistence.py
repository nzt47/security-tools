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
import time
from contextlib import contextmanager
from typing import Dict, List, Optional

from planning.models import Plan, PlanState

logger = logging.getLogger(__name__)


def _ts() -> str:
    """wall-clock 毫秒时间戳（锁竞争时序日志统一入口）"""
    return time.strftime("%H:%M:%S", time.localtime()) + f".{int(time.time() * 1000) % 1000:03d}"


@contextmanager
def _locked(lock: threading.Lock, op: str):
    """持锁操作统一入口：锁外记录等待开始/释放时刻（DEBUG 级，排查竞态时开启）。

    不变量：日志均在锁外输出（持锁期间禁 I/O——logging 调用不持锁即可满足）。
    用途：并发写 SQLite 时通过等待/持锁耗时定位锁竞争热点与死锁嫌疑。
    """
    t0 = time.monotonic()
    logger.debug(f"[DB时序] {op} 等待锁 @{_ts()}")
    lock.acquire()
    wait_ms = (time.monotonic() - t0) * 1000
    logger.debug(f"[DB时序] {op} 获取锁 @{_ts()} | 等待 {wait_ms:.1f}ms")
    try:
        yield
    finally:
        logger.debug(f"[DB时序] {op} 释放锁 @{_ts()} | 持锁 {(time.monotonic() - t0) * 1000:.1f}ms")
        lock.release()

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
    metadata      TEXT,
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
CREATE TABLE IF NOT EXISTS transition_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id    TEXT NOT NULL,
    from_state TEXT,
    to_state   TEXT NOT NULL,
    reason     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_plans_state ON plans(state);
CREATE INDEX IF NOT EXISTS idx_exec_log_plan ON execution_log(plan_id, created_at);
CREATE INDEX IF NOT EXISTS idx_transition_plan ON transition_history(plan_id, created_at);
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
            self._migrate_legacy_schema()

    def _migrate_legacy_schema(self) -> None:
        """存量库结构迁移：老库 plans 表无 metadata 列时补列（幂等）"""
        cols = [r["name"] for r in self._conn.execute("PRAGMA table_info(plans)").fetchall()]
        if "metadata" not in cols:
            self._conn.execute("ALTER TABLE plans ADD COLUMN metadata TEXT")
            self._conn.commit()

    def close(self) -> None:
        """关闭连接（进程退出时调用）"""
        with self._lock:
            self._conn.close()

    # ── 计划落库 ──────────────────────────────────────────────────
    def upsert_plan(self, plan: Plan) -> None:
        """upsert 计划及其子任务（事务写入）"""
        d = plan.to_dict()
        with _locked(self._lock, "upsert_plan"):
            self._conn.execute(
                """INSERT INTO plans (id, original_task, state, progress, current_step,
                                     max_steps, result, error, context, metadata,
                                     created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     state=excluded.state, progress=excluded.progress,
                     current_step=excluded.current_step, result=excluded.result,
                     error=excluded.error, context=excluded.context,
                     metadata=excluded.metadata, updated_at=excluded.updated_at""",
                (
                    d["id"], d["original_task"], d["state"], d["progress"],
                    d["current_step"], d["max_steps"],
                    json.dumps(d["result"], ensure_ascii=False) if d["result"] is not None else None,
                    d["error"],
                    json.dumps(d["context"], ensure_ascii=False),
                    json.dumps(d.get("metadata", {}), ensure_ascii=False),
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
        with _locked(self._lock, "load_unfinished_plans"):
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
                        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
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
        with _locked(self._lock, "count_plans"):
            return self._conn.execute("SELECT COUNT(*) AS c FROM plans").fetchone()["c"]

    # ── 执行记录审计 ──────────────────────────────────────────────
    def append_execution_log(self, *, plan_id: str, task_id: Optional[str],
                             action_type: Optional[str], tool_name: Optional[str],
                             success: bool, output: Optional[str],
                             error: Optional[str]) -> None:
        """追加一条执行记录（审计/可追溯）"""
        with _locked(self._lock, "append_execution_log"):
            self._conn.execute(
                "INSERT INTO execution_log (plan_id, task_id, action_type, tool_name,"
                " success, output, error) VALUES (?,?,?,?,?,?,?)",
                (plan_id, task_id, action_type, tool_name, 1 if success else 0, output, error),
            )
            self._conn.commit()

    def count_execution_logs(self, plan_id: Optional[str] = None) -> int:
        with _locked(self._lock, "count_execution_logs"):
            if plan_id:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS c FROM execution_log WHERE plan_id=?", (plan_id,)
                ).fetchone()
            else:
                row = self._conn.execute("SELECT COUNT(*) AS c FROM execution_log").fetchone()
            return row["c"]

    # ── 状态转换历史（阶段 2 / D9 升级）────────────────────────────
    def record_transition(self, *, plan_id: str, from_state: Optional[str],
                          to_state: str, reason: Optional[str]) -> None:
        """追加一条状态转换记录（增量落库，审计可追溯）"""
        with _locked(self._lock, "record_transition"):
            self._conn.execute(
                "INSERT INTO transition_history (plan_id, from_state, to_state, reason)"
                " VALUES (?,?,?,?)",
                (plan_id, from_state, to_state, reason),
            )
            self._conn.commit()

    def get_transition_history(self, plan_id: Optional[str] = None,
                               limit: int = 50) -> List[Dict]:
        """查询状态转换历史（按时间倒序）"""
        with _locked(self._lock, "get_transition_history"):
            if plan_id:
                rows = self._conn.execute(
                    "SELECT * FROM transition_history WHERE plan_id=?"
                    " ORDER BY id DESC LIMIT ?",
                    (plan_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM transition_history ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        return [dict(r) for r in rows]

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
