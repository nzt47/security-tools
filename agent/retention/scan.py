"""数据资产机械扫描（TASK-S8-01 步骤 1 的底座）。

`scripts/scan_data_assets.py` 与 `agent/retention/archiver.py` 共用本模块：
扫描只读、不落盘、不改任何文件。所有数字（体积/条数/时间范围）都来自真实文件系统，
**不估算、不编造**。

【为什么单独一个模块】
    "数据资产普查"与"归档选片"必须用**同一套口径**：否则文档里的体积与归档时的体积
    会对不上（这正是 S7 各报告反复踩的坑）。故二者共享 `expand()` / `file_time_range()`
    / `class_footprint()`。
"""

from __future__ import annotations

import glob as _glob
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Sequence, Tuple

from agent.retention.policy import (
    KIND_JSONL,
    KIND_SQLITE,
    PROJECT_ROOT,
    RetentionClass,
    RetentionPolicy,
)

logger = logging.getLogger("agent.retention.scan")

#: 文件名里的日期形态：`YYYY-MM-DD`（温层分片）与 `YYYYMMDD`（既有归档名）
_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DAY_COMPACT_RE = re.compile(r"\d{8}")

#: 扫描时单文件最多读多少字节用于判定时间范围（避免把 GB 级文件整读进内存；
#: 超限时如实标注 `sampled=True`，不假装是全量时间范围）
TS_SCAN_MAX_BYTES = 8 << 20


class ScanError(RuntimeError):
    """扫描失败（路径不可访问等）。"""


# ════════════════════════════════════════════════════════════
#  路径展开
# ════════════════════════════════════════════════════════════


def class_base(cls: RetentionClass, root: str = "") -> str:
    """本类 globs 的基准目录（外部类用 `external_root`，其余用仓库根）。"""
    if cls.external:
        return os.path.abspath(os.path.expanduser(cls.external_root))
    return os.path.abspath(root or PROJECT_ROOT)


def expand(cls: RetentionClass, root: str = "") -> List[str]:
    """展开本类的 globs → 存在的文件清单（去重、按路径排序；只读）。"""
    base = class_base(cls, root)
    found: List[str] = []
    seen = set()
    for pattern in cls.globs:
        for hit in _glob.glob(os.path.join(base, pattern), recursive=True):
            if not os.path.isfile(hit):
                continue
            norm = os.path.normcase(os.path.abspath(hit))
            if norm in seen:
                continue
            seen.add(norm)
            found.append(os.path.abspath(hit))
    return sorted(found)


# ════════════════════════════════════════════════════════════
#  单文件度量
# ════════════════════════════════════════════════════════════


def read_ts(line: str) -> str:
    """从一行 JSON 里取 `ts`（容错：非 JSON / 缺字段 → 空串）。"""
    line = (line or "").strip()
    if not line or not line.startswith("{"):
        return ""
    try:
        rec = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(rec, dict):
        return ""
    return str(rec.get("ts") or "")


def count_lines(path: str) -> int:
    """统计非空行数（JSONL 记录数口径；与 `log_archiver` 的"跳过空行"同口径）。"""
    n = 0
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.strip():
                    n += 1
    except OSError as e:
        logger.debug("[retention.scan] 行数统计失败 %s: %s", path, e)
    return n


def file_time_range(path: str, *, max_bytes: int = TS_SCAN_MAX_BYTES) -> Tuple[str, str, int]:
    """返回 `(first_ts, last_ts, lines)`（按文件出现顺序，非排序）。

    超大文件只读前 `max_bytes` 字节（首 ts 准确；末 ts 退化为已读范围末条）。
    """
    first = ""
    last = ""
    lines = 0
    read = 0
    truncated = False
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                read += len(line)
                if read > max_bytes:
                    truncated = True
                    break
                text = line.strip()
                if not text:
                    continue
                lines += 1
                ts = read_ts(text)
                if ts:
                    if not first:
                        first = ts
                    last = ts
    except OSError as e:
        logger.debug("[retention.scan] 时间范围读取失败 %s: %s", path, e)
    if truncated:
        logger.info("[retention.scan] %s 超过 %d 字节，时间范围为采样值", path, max_bytes)
    return first, last, lines


def sqlite_row_count(db_path: str, table: str = "") -> int:
    """SQLite 行数（表名缺省 → 全库所有用户表行数之和）。只读连接。"""
    if not os.path.exists(db_path):
        return 0
    try:
        uri = "file:%s?mode=ro" % db_path.replace("?", "%3f").replace("#", "%23")
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as e:
        logger.debug("[retention.scan] 只读打开失败 %s: %s", db_path, e)
        return 0
    try:
        if table:
            row = conn.execute(
                f'SELECT COUNT(*) FROM "{table}"').fetchone()  # noqa: S608 表名来自策略表常量
            return int(row[0]) if row else 0
        total = 0
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'").fetchall()]
        for name in names:
            row = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()  # noqa: S608
            total += int(row[0]) if row else 0
        return total
    except sqlite3.Error as e:
        logger.debug("[retention.scan] 行数统计失败 %s: %s", db_path, e)
        return 0
    finally:
        conn.close()


def sqlite_tables(db_path: str) -> List[str]:
    """用户表清单（只读）。"""
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=5.0)
    except sqlite3.Error:
        return []
    try:
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def file_records(cls: RetentionClass, path: str) -> int:
    """按类形态给出"记录数"：JSONL=行数；SQLite=表行数；树=1（一个文件一条）。"""
    if cls.kind == KIND_JSONL:
        return count_lines(path)
    if cls.kind == KIND_SQLITE:
        return sqlite_row_count(path, cls.sqlite_table)
    return 1


def sqlite_row_digest(db_path: str, table: str = "") -> str:
    """SQLite 行级摘要：按 rowid 顺序把每行规范化后 sha256（**与源库无关**）。

    用途：冷归档的一致性备份是**副本字节**（sha256 必然不同于活动库本身），
    故还原的"往返一致"不能比字节，要比**行内容**。摘要对行序敏感、对 sqlite
    页布局不敏感 —— 正是我们要证明的东西。
    """
    import hashlib

    if not os.path.exists(db_path):
        return ""
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=5.0)
    except sqlite3.Error as e:
        logger.debug("[retention.scan] 行摘要只读打开失败 %s: %s", db_path, e)
        return ""
    try:
        h = hashlib.sha256()
        names = [table] if table else [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        for name in names:
            h.update(("T:" + str(name) + "\n").encode("utf-8"))
            try:
                cur = conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid')  # noqa: S608
            except sqlite3.Error:
                cur = conn.execute(f'SELECT * FROM "{name}"')  # noqa: S608
                # WITHOUT ROWID 表：退化为按全部列排序，保证确定性
            rows = cur.fetchall()
            for row in rows:
                h.update((repr(tuple(row)) + "\n").encode("utf-8", "replace"))
        return h.hexdigest()
    except sqlite3.Error as e:
        logger.debug("[retention.scan] 行摘要失败 %s: %s", db_path, e)
        return ""
    finally:
        conn.close()


def sqlite_consistent_backup(db_path: str, dest: str) -> bool:
    """SQLite 一致性备份（在线备份 API）：**不锁活动库、不读半写页**。

    为什么不用 `shutil.copyfile`：活动库开着 WAL 且后台有写线程时，文件级复制会得到
    一个"页撕裂"的库（能打开但行数不对）。`Connection.backup()` 是 SQLite 官方
    在线备份 API，产出的副本保证事务一致。
    """
    if not os.path.exists(db_path):
        return False
    try:
        src = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=10.0)
    except sqlite3.Error as e:
        logger.warning("[retention.scan] 备份源打开失败 %s: %s", db_path, e)
        return False
    try:
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        dst = sqlite3.connect(dest, timeout=10.0)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
        return True
    except sqlite3.Error as e:
        logger.warning("[retention.scan] 一致性备份失败 %s: %s", db_path, e)
        return False
    finally:
        src.close()


def file_bytes(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


# ════════════════════════════════════════════════════════════
#  类级普查
# ════════════════════════════════════════════════════════════


def class_footprint(cls: RetentionClass, root: str = "") -> Dict[str, Any]:
    """一个数据类的真实占用（只读）。

    Returns:
        {class_id, title, kind, base, exists, file_count, bytes, records,
         first_ts, last_ts, files: [{path, rel_path, bytes, records, first_ts, last_ts}],
         redline, deletable, retention_days, ...}
    """
    base = class_base(cls, root)
    files = expand(cls, root)
    rows: List[Dict[str, Any]] = []
    total_bytes = 0
    total_records = 0
    firsts: List[str] = []
    lasts: List[str] = []
    for path in files:
        size = file_bytes(path)
        records = file_records(cls, path)
        if cls.kind == KIND_JSONL:
            f_ts, l_ts, _lines = file_time_range(path)
        else:
            f_ts, l_ts = "", ""
        total_bytes += size
        total_records += records
        if f_ts:
            firsts.append(f_ts)
        if l_ts:
            lasts.append(l_ts)
        rel = ""
        try:
            rel = os.path.relpath(path, PROJECT_ROOT).replace("\\", "/")
        except ValueError:  # pragma: no cover - 跨盘符
            rel = path
        rows.append({
            "path": path, "rel_path": rel, "bytes": size, "records": records,
            "first_ts": f_ts, "last_ts": l_ts,
        })
    return {
        "class_id": cls.class_id,
        "title": cls.title,
        "kind": cls.kind,
        "base": base,
        "globs": list(cls.globs),
        "external": cls.external,
        # `exists` = **本类是否有数据**（有匹配文件才算）；`base_exists` = 基准目录是否存在。
        # 二者分开是口径纪律：把"目录在"当成"有数据"会让普查虚增（§0.3）。
        "exists": bool(files),
        "base_exists": os.path.exists(base),
        "file_count": len(files),
        "bytes": total_bytes,
        "records": total_records,
        "first_ts": min(firsts) if firsts else "",
        "last_ts": max(lasts) if lasts else "",
        "retention_days": cls.retention_days,
        "retention_label": cls.retention_label(),
        "archive_mode": cls.archive_mode,
        "delete_mode": cls.delete_mode,
        "deletable": cls.deletable,
        "can_purge": cls.can_purge,
        "redline": cls.redline,
        "warm_days": cls.warm_days,
        "warm_allowed": cls.warm_allowed,
        "reader_shard_aware": cls.reader_shard_aware,
        "cold_days": cls.cold_days,
        "metric_dependencies": list(cls.metric_dependencies),
        "owner": cls.owner,
        "basis": cls.basis,
        "files": rows,
    }


def scan_all(policy: RetentionPolicy, root: str = "") -> List[Dict[str, Any]]:
    """按策略表逐类普查（顺序与策略表一致，便于与文档逐行对照）。"""
    return [class_footprint(cls, root) for cls in policy.classes]


def footprint_totals(footprints: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """合计（**含"未落盘类"计数**：不存在的类不计入体积，但计入类数披露）。"""
    present = [f for f in footprints if f.get("file_count")]
    return {
        "classes": len(footprints),
        "classes_present": len(present),
        "classes_missing": len(footprints) - len(present),
        "files": sum(int(f.get("file_count") or 0) for f in footprints),
        "bytes": sum(int(f.get("bytes") or 0) for f in footprints),
        "records": sum(int(f.get("records") or 0) for f in footprints),
        "redline_classes": [f["class_id"] for f in footprints if f.get("redline")],
        "deletable_classes": [f["class_id"] for f in footprints if f.get("can_purge")],
    }


# ════════════════════════════════════════════════════════════
#  分层选片（dry-run 与执行共用同一套判定）
# ════════════════════════════════════════════════════════════


def file_day(path: str) -> str:
    """文件的"归属日"（`YYYY-MM-DD`）。

    判定顺序（全部基于**文件名**，取不到才退回 mtime）：
        1. 文件名里的 `YYYY-MM-DD`（温层分片：`events-2026-09-01.jsonl`）
        2. 文件名里的 `YYYYMMDD`（既有归档名：`...-20260810.jsonl`）
        3. mtime 的日期
    取不到 → 空串（**空串一律视为"不冷"：不确定就不归档**，默认保守）。
    """
    name = os.path.basename(path)
    m = _DAY_RE.search(name)
    if m:
        return m.group(0)
    m2 = _DAY_COMPACT_RE.search(name)
    if m2:
        try:
            return datetime.strptime(m2.group(0), "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
    except OSError:
        return ""


def cold_files(cls: RetentionClass, root: str, *, now: datetime,
               ) -> Tuple[List[str], List[str]]:
    """按 `cold_days` 划分 `(cold, hot)`。

    冷判定口径：`day < (now - cold_days).date()`；`day` 取不到 → 归热（保守）。
    """
    cutoff = (now - timedelta(days=int(cls.cold_days))).date().isoformat()
    cold: List[str] = []
    hot: List[str] = []
    for path in expand(cls, root):
        day = file_day(path)
        if day and day < cutoff:
            cold.append(path)
        else:
            hot.append(path)
    return cold, hot


def warm_plan(cls: RetentionClass, root: str, *, now: datetime) -> Dict[str, Any]:
    """温层**计划**（只读计算，不落盘）。

    【判定口径与既有实现逐字一致】
        `agent/skills_mgmt/log_archiver.archive_daily_file()` 的规则是：
        **`ts[:10] >= today` 的行留在活动文件，其余历史行移入 `<stem>-<day><ext>` 分片**。
        故 `warm_days` 只有 `0`（= 活动文件只保留当日）能由既有实现表达；
        任何 `> 0` 的窗口都与它不等价 —— 那样会并存两套分片语义（批次总表 §二③ 禁止），
        因此本模块**不擅自改写 log_archiver**，而是显式报告"温层不可表达"。
    """
    today = now.date().isoformat()
    info: Dict[str, Any] = {
        "class_id": cls.class_id, "enabled": False, "today": today,
        "warm_days": cls.warm_days, "targets": [], "lines": 0, "bytes": 0,
        "buckets": {}, "reason": "",
    }
    if not cls.reader_shard_aware:
        info["reason"] = "读端非分片感知：开启温层会让读端丢数据（等于改变统计口径）"
        return info
    if cls.archive_mode == "none" or cls.warm_days is None:
        info["reason"] = "本类未开温层"
        return info
    if int(cls.warm_days) != 0:
        info["reason"] = (
            f"warm_days={cls.warm_days} 无法由既有 log_archiver（以「非今日」为界）表达；"
            "为避免并存两套分片语义，本类温层不做显式分片（交由写入侧跨日触发）")
        return info
    info["enabled"] = True
    for path in expand(cls, root):
        if _DAY_RE.search(os.path.basename(path)):
            continue  # 已是分片文件（温层产物），不重复分片
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    text = line.strip()
                    if not text:
                        continue
                    ts = read_ts(text)
                    day = ts[:10] if len(ts) >= 10 else ""
                    if day and day < today:
                        info["lines"] += 1
                        info["bytes"] += len(line.encode("utf-8"))
                        info["buckets"][day] = info["buckets"].get(day, 0) + 1
        except OSError as e:
            logger.warning("[retention.scan] 温层计划读取失败 %s: %s", path, e)
            continue
        info["targets"].append(path)
    return info


__all__ = [
    "TS_SCAN_MAX_BYTES", "ScanError",
    "class_base", "expand", "read_ts", "count_lines", "file_time_range",
    "sqlite_row_count", "sqlite_row_digest", "sqlite_consistent_backup",
    "sqlite_tables", "file_records", "file_bytes",
    "class_footprint", "scan_all", "footprint_totals",
    "file_day", "cold_files", "warm_plan",
]
