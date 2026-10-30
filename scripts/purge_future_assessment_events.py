"""清理评估事件台账里的「未来时间戳」脏数据（2026-10-30）。

背景：`data/skills_assessment_events.jsonl` 里积了 190 条 2027-10-19/20 的记录
（skill_id 形如 `s-promote` / `conc-0` / `test-skill`，来自早期未隔离的测试/演练直接写生产台账）。
它们按时间倒序永远排在最前，于是「动态中心」的最新动态钉在 2027。

写入侧已用 `datetime.now()`（产生不了未来值），服务读侧也加了防御；本脚本只负责**清存量**：
  1. 备份原文件（`<file>.bak.<时间戳>`，不覆盖已有备份）；
  2. 丢掉 ts 超前本机时钟超过 300 秒的行（阈值与 service.is_future_timestamp 同一口径）；
  3. 原子替换 + 回读自证（再扫一遍，未来行必须为 0）；
  4. 写审计 `skills.assess.events.purge_future`（total / removed / backup）。

用法：
    python scripts/purge_future_assessment_events.py            # dry-run（默认）
    python scripts/purge_future_assessment_events.py --execute  # 真删（先备份）
    python scripts/purge_future_assessment_events.py --json     # 机器可读
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from agent.skills_mgmt.service import FUTURE_TS_SKEW_SECONDS, is_future_timestamp  # noqa: E402

DEFAULT_FILE = _ROOT / "data" / "skills_assessment_events.jsonl"


def scan(path: Path):
    rows = []
    bad = []
    if not path.exists():
        return rows, bad
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            raw = line.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                rows.append(raw)  # 坏行原样保留（不是本脚本的清理目标）
                continue
            rows.append(raw)
            if is_future_timestamp(rec.get("ts", "") if isinstance(rec, dict) else ""):
                bad.append(rec)
    return rows, bad


def purge(path: Path, *, execute: bool = False) -> dict:
    rows, bad = scan(path)
    dates = {}
    for rec in bad:
        d = str(rec.get("ts", ""))[:10]
        dates[d] = dates.get(d, 0) + 1
    result = {
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
    kept = []
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
            if isinstance(rec, dict) and is_future_timestamp(rec.get("ts", "")):
                continue
            kept.append(raw)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(r + "\n" for r in kept), encoding="utf-8")
    os.replace(tmp, path)

    _rows2, bad2 = scan(path)
    result.update({"backup": str(backup), "kept_rows": len(kept),
                   "future_after": len(bad2)})
    try:
        from agent.audit import audit as _audit
        _audit.record("skills.assess.events.purge_future", actor="ops",
                      subject="data/skills_assessment_events.jsonl",
                      payload={"total": result["total_rows"], "removed": len(bad),
                               "backup": backup.name, "dates": result["future_dates"]},
                      source="agent", status="ok")
    except Exception as e:  # noqa: BLE001 审计失败不回滚清理（备份可追溯）
        print("[warn] 审计写入失败：%s" % e)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(DEFAULT_FILE))
    ap.add_argument("--execute", action="store_true", help="真删（默认 dry-run）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()
    res = purge(Path(args.file), execute=args.execute)
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
