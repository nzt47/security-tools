"""清理任意 JSONL 台账里的「未来时间戳」脏数据（2026-10-30，§11.26 U2 泛化）。

背景：先有 `scripts/purge_future_assessment_events.py` 专治评估事件台账里的 2027 污染；
排查发现同样的「超前本机时钟」行还散落在 7 个数据文件里（动态事件、治理写、策略决策、
学习novelty、合并备份、归档），把「最新动态 / 最近一次」这类读侧排序钉在未来。
本脚本把原实现**泛化**为任意 jsonl 都能跑，判定口径与读侧防御同源（同一个
`agent.skills_mgmt.service.is_future_timestamp` / `FUTURE_TS_SKEW_SECONDS`）。

流程（与 `--execute` 时一致）：
  1. 备份原文件（`<file>.bak.<时间戳>`，不覆盖已有备份）；
  2. 丢掉 ts 超前本机时钟超过 300 秒的行（坏行原样保留：不是本脚本的清理目标）；
  3. 原子替换（同目录 `.tmp` → `os.replace`）+ 回读自证（未来行必须为 0）；
  4. 每个文件写一条审计 `purge_future_rows`（subject=相对路径 / total / removed / backup / dates）。

时间戳字段按 `ts → timestamp → created_at` 依次取第一个非空值；可用 `--ts-field` 覆盖。

用法：
    python scripts/purge_future_rows.py --file data/events/events.jsonl              # dry-run（默认）
    python scripts/purge_future_rows.py --file a.jsonl --file b.jsonl --execute       # 逐个真删
    python scripts/purge_future_rows.py --file a.jsonl --json                         # 机器可读
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from agent.skills_mgmt.service import FUTURE_TS_SKEW_SECONDS, is_future_timestamp  # noqa: E402

#: 取时间戳的候选字段（按优先级）。不同台账的历史字段名不一样，统一在这里收口。
TS_FIELDS: Tuple[str, ...] = ("ts", "timestamp", "created_at")
#: 审计动作名（U2 口径）。
AUDIT_ACTION = "purge_future_rows"


def _row_ts(rec: Any, ts_fields: Sequence[str] = TS_FIELDS) -> str:
    """取一行的「时间戳」文本；没有则空串（无时间戳的行不参与未来判定）。"""
    if not isinstance(rec, dict):
        return ""
    for key in ts_fields:
        val = rec.get(key)
        if val:
            return str(val)
    return ""


def scan(path: Path, ts_fields: Sequence[str] = TS_FIELDS) -> Tuple[List[str], List[Dict[str, Any]]]:
    """返回 (非空原始行, 未来行记录)。坏 JSON 行原样计入 rows、不参与清理。"""
    rows: List[str] = []
    bad: List[Dict[str, Any]] = []
    if not path.exists():
        return rows, bad
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            raw = line.strip()
            if not raw:
                continue
            rows.append(raw)
            try:
                rec = json.loads(raw)
            except ValueError:
                continue  # 坏行原样保留（不是本脚本的清理目标）
            if is_future_timestamp(_row_ts(rec, ts_fields)):
                bad.append(rec if isinstance(rec, dict) else {"ts": ""})
    return rows, bad


def purge(path: Path, *, execute: bool = False,
          ts_fields: Sequence[str] = TS_FIELDS,
          audit_action: str = AUDIT_ACTION,
          audit_subject: str | None = None) -> Dict[str, Any]:
    """清一个文件的未来行。默认 dry-run（只报数，不碰文件）。"""
    path = Path(path)
    rows, bad = scan(path, ts_fields)
    dates: Dict[str, int] = {}
    for rec in bad:
        day = _row_ts(rec, ts_fields)[:10]
        dates[day] = dates.get(day, 0) + 1
    result: Dict[str, Any] = {
        "file": str(path),
        "total_rows": len(rows),
        "future_rows": len(bad),
        "future_dates": dict(sorted(dates.items())),
        "skew_seconds": FUTURE_TS_SKEW_SECONDS,
        "dry_run": not execute,
    }
    if not execute or not bad:
        return result

    backup = path.with_name(path.name + ".bak." + time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, backup)
    kept: List[str] = []
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            raw = line.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                kept.append(raw)
                continue
            if is_future_timestamp(_row_ts(rec, ts_fields)):
                continue
            kept.append(raw)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(r + "\n" for r in kept), encoding="utf-8")
    os.replace(tmp, path)

    _rows2, bad2 = scan(path, ts_fields)
    result.update({"backup": str(backup), "kept_rows": len(kept),
                   "future_after": len(bad2)})
    subject = audit_subject
    if not subject:
        try:
            subject = Path(os.path.relpath(str(path), str(_ROOT))).as_posix()
        except ValueError:  # 跨盘符时 relpath 会抛，退回绝对路径
            subject = str(path)
    try:
        from agent.audit import audit as _audit
        _audit.record(audit_action, actor="ops", subject=subject,
                      payload={"total": result["total_rows"], "removed": len(bad),
                               "backup": backup.name, "dates": result["future_dates"]},
                      source="agent", status="ok")
    except Exception as e:  # noqa: BLE001 审计失败不回滚清理（备份可追溯）
        print("[warn] 审计写入失败：%s" % e)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="清理 jsonl 台账里的未来时间戳行（默认 dry-run）")
    ap.add_argument("--file", dest="files", action="append", required=True,
                    help="目标 jsonl（可重复传入）")
    ap.add_argument("--execute", action="store_true", help="真删（默认 dry-run）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--ts-field", default=None,
                    help="时间戳字段名（默认自动取 ts/timestamp/created_at）")
    args = ap.parse_args(argv)

    ts_fields: Sequence[str] = (args.ts_field,) if args.ts_field else TS_FIELDS
    results: List[Dict[str, Any]] = []
    failed = False
    for name in args.files:
        path = Path(name)
        if not path.exists():
            results.append({"file": str(path), "error": "file not found"})
            failed = True
            continue
        res = purge(path, execute=args.execute, ts_fields=ts_fields)
        if res.get("future_after", 0) not in (0,):
            failed = True
        results.append(res)

    if args.json:
        print(json.dumps(results, ensure_ascii=False))
        return 1 if failed else 0

    for res in results:
        if res.get("error"):
            print("[ERROR] %s：%s" % (res["file"], res["error"]))
            continue
        print("[%s] %s" % ("EXECUTE" if args.execute else "DRY-RUN", res["file"]))
        print("  总行数 %d / 未来时间戳 %d（允许时差 %ds）" % (
            res["total_rows"], res["future_rows"], res["skew_seconds"]))
        if res["future_dates"]:
            print("  按日期：%s" % res["future_dates"])
        if args.execute and res["future_rows"]:
            print("  备份：%s" % res.get("backup"))
            print("  保留 %d 行；回读未来行 %d" % (res.get("kept_rows", 0), res.get("future_after", -1)))
        elif not args.execute and res["future_rows"]:
            print("  （dry-run：加 --execute 才动文件）")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
