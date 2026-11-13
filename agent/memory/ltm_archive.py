# -*- coding: utf-8 -*-
"""压缩摘要 → 长期记忆库（2026-11-13，零 token）。

【为什么】单位看板长期把 `memory.longterm` 记成 **never**：`data/memory/long_term.db`
里 `long_term_memory` 一行都没有。而系统其实一直在产出"会话压缩摘要"
（`data/summary*.txt`，当前 v102 / 657 字节）—— 只是**从来没有写进长期记忆库**。
也就是说：长期记忆不是"用不上"，是**没接线**。

【做什么】把 `data/summary*.txt` 里**非空**的摘要 upsert 进 `long_term.db`：
    key      = "summary:<文件名 stem>:v<版本>"
    content  = 摘要正文（截断到 MAX_CONTENT_CHARS）
    tags     = ["summary", "compression"]
    metadata = {file, version, source: "compression"}
幂等：同一份摘要重复归档只会覆盖同一条（key 不变）。

【口径（别越界）】
- **只读源文件**：不删摘要、不改 `memory_data`/`data/summary*`。
- **跳过空摘要**：0 字节/纯空白一律跳过并计数（2026-10 的"摘要 v88 0 字节"就是这种）。
- **不产生新文本**：归档的是已有摘要，不调 LLM（零 token）。
- **不碰数据目录以外的东西**；`dry_run` 只统计不写。
- 输出是**计数**，不打印摘要正文（对话可能含隐私）。

用法：
    python -m agent.memory.ltm_archive            # 归档（作业调用这个）
    python -m agent.memory.ltm_archive --dry-run  # 只统计
    python -m agent.memory.ltm_archive --json     # 机器可读
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 仓库根（agent/memory/ltm_archive.py -> 上三级）
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 摘要目录（MemoryManager 的 data_dir 默认 "./data"）与长期记忆库
SUMMARY_DIR = REPO_ROOT / "data"
LTM_DB = REPO_ROOT / "data" / "memory" / "long_term.db"

#: 单条摘要最多归档多少字符（摘要本身通常 < 2k；防止异常大文件把 DB 撑爆）
MAX_CONTENT_CHARS = 20_000

#: 归档用的固定元数据（便于以后按来源过滤/重建）
TAG_SUMMARY = "summary"
TAG_COMPRESSION = "compression"
SOURCE_COMPRESSION = "compression"

#: 单元看板读的表名（与 agent/memory/long_term_memory.py 一致）
TABLE_NAME = "long_term_memory"


def discover_summaries(summary_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """列出可归档的摘要文件（跳过 0 字节/空白），返回 [{name, version, text}]。"""
    base = Path(summary_dir) if summary_dir is not None else SUMMARY_DIR
    out: List[Dict[str, Any]] = []
    try:
        files = sorted(base.glob("summary*.txt"))
    except OSError as exc:  # noqa: BLE001 目录读不到 ⇒ 没得归档
        logger.warning("[ltm-archive] 摘要目录读取失败（%s）", type(exc).__name__)
        return []
    for path in files:
        name = path.name
        # 版本文件形如 summary_version.txt / summary_<hash>.version.txt：不是正文
        if name.endswith(".version.txt") or name == "summary_version.txt":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:  # noqa: BLE001 单个文件读失败不影响其它
            logger.warning("[ltm-archive] 读摘要失败（%s）", type(exc).__name__)
            continue
        if not text.strip():
            out.append({"name": name, "version": 0, "text": "", "empty": True})
            continue
        version = _read_version(path)
        out.append({"name": name, "version": version, "text": text[:MAX_CONTENT_CHARS], "empty": False})
    return out


def _read_version(summary_path: Path) -> int:
    """读版本文件。

    两种命名并存（见 memory/storage.py）：全局是 `summary_version.txt`，
    分会话是 `summary_<hash>.version.txt`。两种都试，读不到按 0。
    """
    stem = summary_path.stem  # "summary" / "summary_<hash>"
    candidates = [summary_path.with_name(stem + ".version.txt")]
    if stem == "summary":
        candidates.insert(0, summary_path.with_name("summary_version.txt"))
    for version_path in candidates:
        try:
            return int(version_path.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            continue
    return 0


def _count_rows(db_path: Path) -> Optional[int]:
    try:
        with sqlite3.connect(str(db_path)) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM %s" % TABLE_NAME).fetchone()[0])
    except Exception:  # noqa: BLE001 库还没建/读不到 ⇒ None（不假装 0）
        return None


async def _archive_one(db_path: Path, item: Dict[str, Any]) -> bool:
    from agent.memory.long_term_memory import LongTermMemory

    ltm = LongTermMemory(db_path=str(db_path))
    key = "summary:%s:v%d" % (Path(item["name"]).stem, int(item.get("version") or 0))
    return await ltm.save(
        key,
        item["text"],
        importance=3,
        tags=[TAG_SUMMARY, TAG_COMPRESSION],
        metadata={"file": item["name"], "version": int(item.get("version") or 0),
                  "source": SOURCE_COMPRESSION},
    )


def archive(*, db_path: Optional[Path] = None, summary_dir: Optional[Path] = None,
            dry_run: bool = False) -> Dict[str, Any]:
    """把非空摘要归档进长期记忆库；返回计数（永不抛）。"""
    target_db = Path(db_path) if db_path is not None else LTM_DB
    items = discover_summaries(summary_dir)
    if not items:
        return {"ok": True, "files": 0, "archived": 0, "skipped_empty": 0,
                "skipped_same": 0, "dry_run": dry_run, "rows_before": None, "rows_after": None}
    # dry-run 连"读行数"都不做：sqlite3.connect 会**创建**文件，等于偷偷写了盘
    rows_before = None if dry_run else _count_rows(target_db)
    archived = 0
    skipped_empty = 0
    skipped_same = 0
    existing = _existing_keys(target_db) if not dry_run else set()
    for item in items:
        if item.get("empty"):
            skipped_empty += 1
            continue
        key = "summary:%s:v%d" % (Path(item["name"]).stem, int(item.get("version") or 0))
        if key in existing:
            skipped_same += 1
            continue
        if dry_run:
            archived += 1
            continue
        try:
            ok = asyncio.run(_archive_one(target_db, item))
        except Exception as exc:  # noqa: BLE001 单条失败不影响其它摘要
            logger.warning("[ltm-archive] 归档失败（%s）", type(exc).__name__)
            ok = False
        if ok:
            archived += 1
    rows_after = None if dry_run else _count_rows(target_db)
    return {"ok": True, "files": len(items), "archived": archived,
            "skipped_empty": skipped_empty, "skipped_same": skipped_same,
            "dry_run": dry_run, "rows_before": rows_before, "rows_after": rows_after}


def _existing_keys(db_path: Path) -> set:
    try:
        with sqlite3.connect(str(db_path)) as conn:
            return {row[0] for row in conn.execute(
                "SELECT key FROM %s WHERE key LIKE 'summary:%%'" % TABLE_NAME)}
    except Exception:  # noqa: BLE001 库不存在 ⇒ 没有已归档
        return set()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="把压缩摘要归档进长期记忆库（零 token）")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)
    result = archive(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("摘要文件 %(files)d 个：归档 %(archived)d / 空摘要 %(skipped_empty)d / "
              "已存在 %(skipped_same)d ⇒ 行数 %(rows_before)s → %(rows_after)s"
              % {"files": result["files"], "archived": result["archived"],
                 "skipped_empty": result["skipped_empty"], "skipped_same": result["skipped_same"],
                 "rows_before": result["rows_before"], "rows_after": result["rows_after"]})
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
