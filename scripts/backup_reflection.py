#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data/reflection 基线备份脚本（阶段5 C1 灰度发布前置）

用途：灰度放量前建立 data/reflection 基线备份，回滚演练时可恢复。
由计划任务调用（data/backups/reflection/backup_reflection_task.cmd）：

    python scripts/backup_reflection.py \
        --source data/reflection \
        --backup-root data/backups/reflection \
        --keep 14

行为契约（对齐历史日志 data/backups/reflection/backup_reflection.log）：
1. 快照目录名 = <backup-root>/<YYYYmmdd_HHMMSS_fff>（毫秒级，避免同秒冲突）
2. 递归复制 source 全部内容到快照目录
3. 轮转：仅保留最近 --keep 份快照，删除更旧的
4. 结构化 JSON 日志（module_name=backup_reflection, action=backup/cleanup）到 stdout
   （任务 cmd 会把 stdout 重定向到 backup_reflection.log）

退出码：0 成功；1 参数/源目录错误；2 备份失败（保留错误现场，不静默）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

MODULE = "backup_reflection"


def log_json(action: str, msg: str, **extra) -> None:
    """结构化 JSON 日志（单行，供重定向采集）"""
    entry = {
        "module_name": MODULE,
        "action": action,
        "msg": msg,
        **extra,
    }
    print(json.dumps(entry, ensure_ascii=False), flush=True)


def backup(source: Path, backup_root: Path, keep: int) -> int:
    if not source.is_dir():
        log_json("backup", f"源目录不存在: {source}", action_result="failed")
        return 1

    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    snapshot = backup_root / stamp
    # 同毫秒连续调用会撞名（copytree 目标已存在 → WinError 183），追加序号兜底
    i = 1
    while snapshot.exists():
        snapshot = backup_root / f"{stamp}_{i}"
        i += 1

    try:
        # 源可能含子目录/文件，copytree 全量复制；目标快照目录必须不存在
        shutil.copytree(source, snapshot)
    except Exception as exc:  # 保留错误现场，不静默
        log_json("backup", f"备份失败: {exc}", action_result="failed")
        return 2

    n_files = sum(1 for _ in snapshot.rglob("*") if _.is_file())
    log_json("backup", f"备份完成: {source} -> {snapshot}（{n_files} 个文件）")

    # 轮转：按快照目录名（时间戳字典序）保留最近 keep 份
    snapshots = sorted(p for p in backup_root.iterdir() if p.is_dir())
    excess = len(snapshots) - keep
    removed = 0
    for old in snapshots[: max(excess, 0)]:
        shutil.rmtree(old, ignore_errors=True)
        removed += 1
    log_json(
        "cleanup",
        f"备份任务完成，保留 {keep} 份快照于 {backup_root}",
        kept=min(len(snapshots), keep),
        removed=removed,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="data/reflection 基线备份（阶段5 C1 前置）")
    parser.add_argument("--source", required=True, help="要备份的源目录（如 data/reflection）")
    parser.add_argument("--backup-root", required=True, help="快照根目录（如 data/backups/reflection）")
    parser.add_argument("--keep", type=int, default=14, help="保留快照份数（默认 14）")
    args = parser.parse_args(argv)

    if args.keep < 1:
        parser.error("--keep 必须 >= 1")

    return backup(Path(args.source), Path(args.backup_root), args.keep)


if __name__ == "__main__":
    sys.exit(main())
