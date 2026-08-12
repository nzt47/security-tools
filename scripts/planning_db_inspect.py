"""规划库独立读取校验工具（阶段 2 / D9 评估标准 3）

不依赖 planning 包（纯 sqlite3），可独立读取校验 ./data/planning/plans.db
（或任意 --db 指定路径），输出计划/任务/执行记录/状态转换历史摘要。

用法:
    python scripts/planning_db_inspect.py [--db data/planning/plans.db]
                                          [--tasks] [--logs] [--transitions]
"""

import argparse
import json
import os
import sqlite3
import sys


def _open(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        sys.exit(f"[error] 数据库不存在: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _list_plans(conn: sqlite3.Connection) -> list:
    return conn.execute(
        "SELECT id, original_task, state, progress, current_step,"
        " max_steps, error, created_at, updated_at, metadata"
        " FROM plans ORDER BY created_at"
    ).fetchall()


def _list_tasks(conn: sqlite3.Connection, plan_id: str) -> list:
    return conn.execute(
        "SELECT task_id, payload FROM plan_tasks WHERE plan_id=? ORDER BY task_id",
        (plan_id,),
    ).fetchall()


def _state_distribution(plans: list) -> dict:
    dist = {}
    for p in plans:
        dist[p["state"]] = dist.get(p["state"], 0) + 1
    return dist


def main() -> int:
    parser = argparse.ArgumentParser(description="规划 SQLite 库独立读取校验")
    parser.add_argument("--db", default=os.path.join("data", "planning", "plans.db"),
                        help="SQLite 库路径（默认 data/planning/plans.db）")
    parser.add_argument("--tasks", action="store_true", help="展示各计划的任务明细")
    parser.add_argument("--logs", action="store_true", help="展示执行记录（execution_log）")
    parser.add_argument("--transitions", action="store_true", help="展示状态转换历史")
    args = parser.parse_args()

    conn = _open(args.db)
    plans = _list_plans(conn)
    print(f"=== 计划总数: {len(plans)} | 状态分布: {json.dumps(_state_distribution(plans), ensure_ascii=False)}")

    for p in plans:
        tasks = _list_tasks(conn, p["id"])
        meta = ""
        if p["metadata"]:
            try:
                meta = f" | metadata={p['metadata'][:80]}"
            except TypeError:
                pass
        try:
            progress = f"{float(p['progress']):.0%}"
        except (TypeError, ValueError):
            progress = str(p["progress"])
        print(f"\n- [{p['state']}] {p['id']} | 任务: {p['original_task']}"
              f" | 进度: {progress} | 子任务: {len(tasks)}"
              f" | 创建: {p['created_at']}{meta}")
        if p["error"]:
            print(f"    error: {p['error']}")
        if args.tasks:
            for t in tasks:
                try:
                    payload = json.loads(t["payload"])
                    desc = payload.get("description", "")
                    deps = payload.get("dependencies", [])
                    print(f"    - [{payload.get('status', '?')}] {t['task_id']}: {desc}"
                          f" | 依赖: {deps}")
                except json.JSONDecodeError:
                    print(f"    - {t['task_id']}: <payload 非 JSON>")

    if args.logs:
        print("\n=== 执行记录（execution_log）===")
        for row in conn.execute(
                "SELECT plan_id, task_id, action_type, tool_name, success, output,"
                " error, created_at FROM execution_log ORDER BY id").fetchall():
            print(f"- [{row['created_at']}] {row['plan_id']} / {row['task_id']}"
                  f" | {row['action_type']} {row['tool_name']}"
                  f" | {'OK' if row['success'] else 'FAIL'}"
                  f" | {row['output'] or ''} {row['error'] or ''}")

    if args.transitions:
        print("\n=== 状态转换历史（transition_history）===")
        for row in conn.execute(
                "SELECT plan_id, from_state, to_state, reason, created_at"
                " FROM transition_history ORDER BY id").fetchall():
            print(f"- [{row['created_at']}] {row['plan_id']}:"
                  f" {row['from_state']} -> {row['to_state']}"
                  f" | {row['reason'] or ''}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
