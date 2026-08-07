"""用户档案卡迁移脚本: 在 LongTermMemory 数据库新增 user_profile 表

背景:
    审计报告（2026-07-31）指出云枢记忆系统缺失"用户记忆档案卡"层级。
    本脚本在长期记忆数据库（默认 ./data/memory/long_term.db）中创建
    user_profile 强 Schema 表，用于结构化保存用户姓名、职业、核心目标等
    长期事实，确保事实的唯一性和准确性（user_id 主键 + upsert 语义）。

表结构:
    user_profile (
        user_id            TEXT PRIMARY KEY,      -- 用户唯一标识
        name               TEXT,                  -- 用户姓名
        occupation         TEXT,                  -- 职业
        core_goals         TEXT,                  -- 核心目标 (JSON array 字符串)
        preferences        TEXT,                  -- 偏好 (JSON object 字符串)
        communication_style TEXT,                 -- 沟通风格偏好
        timezone           TEXT,                  -- 时区（会话元数据关联）
        device_type        TEXT,                  -- 设备类型
        locale             TEXT,                  -- 语言环境
        created_at         REAL NOT NULL,         -- 创建时间戳
        updated_at         REAL NOT NULL          -- 更新时间戳
    )

用法:
    # 默认数据库
    python scripts/migrate_add_user_profile.py

    # 指定数据库 + Dry-run（仅检查不写入）
    python scripts/migrate_add_user_profile.py --db-path ./data/memory/long_term.db --dry-run

    # 同时创建会话元数据列的索引（可选）
    python scripts/migrate_add_user_profile.py --with-index

输出:
    - stdout: JSON 格式迁移报告
    - stderr: 人类可读进度日志
    - 退出码: 0=成功, 1=失败

约束:
    - 幂等：表已存在时跳过创建
    - 不修改既有表结构（long_term_memory / ltm_vec_index 不动）
    - --dry-run 时不写入任何数据
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── 常量 ──

DEFAULT_DB_PATH = "./data/memory/long_term.db"
PROFILE_TABLE = "user_profile"

# user_profile 表 DDL（幂等：IF NOT EXISTS）
PROFILE_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {PROFILE_TABLE} (
    user_id             TEXT PRIMARY KEY,
    name                TEXT DEFAULT NULL,
    occupation          TEXT DEFAULT NULL,
    core_goals          TEXT DEFAULT NULL,
    preferences         TEXT DEFAULT NULL,
    communication_style TEXT DEFAULT NULL,
    timezone            TEXT DEFAULT NULL,
    device_type         TEXT DEFAULT NULL,
    locale              TEXT DEFAULT NULL,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL
)
"""

# 会话元数据辅助索引（--with-index 时创建）
PROFILE_TIMEZONE_IDX = (
    f"CREATE INDEX IF NOT EXISTS idx_{PROFILE_TABLE}_timezone "
    f"ON {PROFILE_TABLE}(timezone)"
)
PROFILE_UPDATED_AT_IDX = (
    f"CREATE INDEX IF NOT EXISTS idx_{PROFILE_TABLE}_updated_at "
    f"ON {PROFILE_TABLE}(updated_at)"
)


# ── 工具函数 ──


def log(msg: str, *, level: str = "INFO"):
    """输出到 stderr 的日志"""
    print(f"[{level}] {msg}", file=sys.stderr, flush=True)


def _now() -> float:
    """当前 UTC 时间戳"""
    return time.time()


def check_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """检查表是否已存在"""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def inspect_db(db_path: Path) -> dict[str, Any]:
    """检查目标数据库现状（只读）"""
    info: dict[str, Any] = {"db_path": str(db_path), "exists": db_path.exists()}
    if not db_path.exists():
        return info

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            info["has_profile_table"] = check_table_exists(conn, PROFILE_TABLE)
            # 既有表清单
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
            ]
            info["tables"] = tables
            if info["has_profile_table"]:
                info["profile_count"] = conn.execute(
                    f"SELECT COUNT(*) FROM {PROFILE_TABLE}"
                ).fetchone()[0]
            # 长记忆表统计（用于报告上下文）
            if "long_term_memory" in tables:
                info["long_term_memory_count"] = conn.execute(
                    "SELECT COUNT(*) FROM long_term_memory"
                ).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as e:
        info["error"] = str(e)
    return info


def create_profile_table(conn: sqlite3.Connection, with_index: bool = False) -> dict[str, Any]:
    """创建 user_profile 表（幂等）

    Returns:
        操作结果 {created, with_index}
    """
    result: dict[str, Any] = {"created": False, "with_index": False}
    if not check_table_exists(conn, PROFILE_TABLE):
        conn.execute(PROFILE_TABLE_DDL)
        result["created"] = True
        log(f"创建表 {PROFILE_TABLE} 成功")
    else:
        log(f"表 {PROFILE_TABLE} 已存在，跳过创建（幂等）")

    if with_index:
        conn.execute(PROFILE_TIMEZONE_IDX)
        conn.execute(PROFILE_UPDATED_AT_IDX)
        result["with_index"] = True
        log("已创建会话元数据辅助索引")
    return result


def backup_db(db_path: Path) -> Optional[str]:
    """备份目标数据库（带时间戳），返回备份路径；失败返回 None"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(f"{db_path.stem}.bak.{ts}{db_path.suffix}")
    try:
        shutil.copy2(db_path, backup_path)
        log(f"已备份数据库 → {backup_path}")
        return str(backup_path)
    except OSError as e:
        log(f"数据库备份失败（继续执行，不阻断）: {e}", level="WARN")
        return None


def run_migration(db_path: Path, dry_run: bool, with_index: bool) -> dict[str, Any]:
    """执行迁移主流程

    Returns:
        JSON 序列化报告
    """
    report: dict[str, Any] = {
        "script": "migrate_add_user_profile",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "db_path": str(db_path),
    }

    # 1. 检查现状
    info = inspect_db(db_path)
    report["inspect"] = info
    if info.get("error"):
        log(f"数据库检查失败: {info['error']}", level="ERROR")
        report["status"] = "failed"
        report["error"] = info["error"]
        return report

    if not db_path.exists():
        # 数据库不存在：仍创建（LongTermMemory 首次启动会建库）
        log(f"数据库不存在，将新建: {db_path}", level="WARN")

    # 2. 备份（仅真实执行时）
    if not dry_run:
        backup = backup_db(db_path)
        report["backup_path"] = backup

    # 3. 建表
    if dry_run:
        log("DRY-RUN 模式：仅检查，不写入")
        report["status"] = "dry_run"
        report["would_create"] = not info.get("has_profile_table", False)
        report["would_create_index"] = with_index
        return report

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            result = create_profile_table(conn, with_index=with_index)
            conn.commit()
            report["created"] = result["created"]
            report["with_index"] = result["with_index"]
            report["status"] = "success"
            log("迁移完成 ✓")
        finally:
            conn.close()
    except sqlite3.Error as e:
        log(f"迁移失败: {e}", level="ERROR")
        report["status"] = "failed"
        report["error"] = str(e)

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="为 LongTermMemory 数据库新增 user_profile 用户档案卡表",
    )
    parser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help=f"长期记忆数据库路径（默认 {DEFAULT_DB_PATH}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅检查现状，不写入任何数据",
    )
    parser.add_argument(
        "--with-index",
        action="store_true",
        help="同时创建会话元数据辅助索引（timezone/updated_at）",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    log(f"目标数据库: {db_path} (存在={db_path.exists()})")

    report = run_migration(
        db_path=db_path,
        dry_run=args.dry_run,
        with_index=args.with_index,
    )

    # stdout: JSON 报告
    print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stdout)
    return 0 if report.get("status") == "success" else (0 if report.get("status") == "dry_run" else 1)


if __name__ == "__main__":
    sys.exit(main())
