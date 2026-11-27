# -*- coding: utf-8 -*-
"""P5 / E5-1 Phase A：会话事件溯源**只读投影**（2026-11-27）

把 data/sessions/<sid>/messages.jsonl 投影成 data/session_events/<sid>.jsonl（事件 v1）：
  {"v":1,"type":"message.user|assistant|...","sid":...,"seq":N,"id":"evt_...",
   "ts":"...","payload":{原始记录},"src":"legacy"}

【不变量（与 docs/plan/2026-11-27-p5-会话事件溯源迁移方案.md 一致）】
  1. 只读源文件：messages.jsonl 一个字节都不改（本脚本只 open(..., "r")）；
  2. 冻结池（无 session_id 的行）**跳过并计数**，不迁移；
  3. 幂等：事件 id = evt_ + sha256(sid|ts|role|content)[:16]，重跑只补新事件、不改旧事件；
  4. 坏行进 quarantine（只记文件/行号/原因，不记内容），不中断整批；
  5. 输出原子写（tmp + os.replace），写失败不破坏已有投影。

【边界】Phase A 是影子投影：对话链路仍读 messages.jsonl；读切换（Phase C）要另开开关。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

# 【为什么从 agent.session_events 取】幂等键与事件类型是 Phase A（投影）与 Phase B
# （双写）**共用的事实**：两处各写一份，改一边就会让同一行在两条轨上得到不同 id
# （幂等/对拍同时失效）。本脚本只负责"读 messages、算差异、原子写"。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.session_events import event_id, event_type as _event_type  # noqa: E402


def _load_existing(path: Path) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """读已有投影：返回 (事件列表, {id: seq})；坏行忽略（投影可重建，不因半行失败）"""
    events: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    if not path.exists():
        return events, seen
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("id"):
            events.append(rec)
            try:
                seen[str(rec["id"])] = int(rec.get("seq") or 0)
            except (TypeError, ValueError):
                seen[str(rec["id"])] = 0
    return events, seen


def project_session(sid: str, source: Path, out_dir: Path, dry_run: bool = False) -> Dict[str, Any]:
    """投影单个会话；返回 {sid, rows, events, written, skipped, quarantine[]}"""
    rows, quarantine = [], []
    for lineno, line in enumerate(source.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            quarantine.append({"file": str(source), "line": lineno, "reason": "invalid_json"})
            continue
        if not isinstance(rec, dict):
            quarantine.append({"file": str(source), "line": lineno, "reason": "not_object"})
            continue
        ts = str(rec.get("timestamp") or "").strip()
        if not ts:
            quarantine.append({"file": str(source), "line": lineno, "reason": "missing_timestamp"})
            continue
        role = str(rec.get("role") or "").strip().lower()
        evt = {
            "v": 1,
            "type": _event_type(role),
            "sid": sid,
            "seq": 0,
            "id": event_id(sid, ts, role, str(rec.get("content") or "")),
            "ts": ts,
            "payload": rec,
            "src": "legacy",
        }
        rows.append(evt)

    # 同一会话内按 (ts, id) 稳定排序后再编号；已有事件保持原 seq
    rows.sort(key=lambda e: (str(e["ts"]), str(e["id"])))
    out_path = out_dir / (sid + ".jsonl")
    existing, seen = _load_existing(out_path)
    max_seq = max(seen.values(), default=0)
    written = 0
    merged = list(existing)
    for evt in rows:
        if evt["id"] in seen:
            continue
        max_seq += 1
        evt["seq"] = max_seq
        merged.append(evt)
        written += 1

    if written and not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in merged), encoding="utf-8")
        os.replace(tmp, out_path)

    return {
        "sid": sid,
        "rows": len(rows),
        "events": len(merged),
        "written": written,
        "quarantine": quarantine,
    }


def project_all(root: Path, dry_run: bool = False) -> Dict[str, Any]:
    """扫全部会话 + 冻结池；返回迁移报告（可直接 json.dump）"""
    root = Path(root)
    sessions_dir = root / "data" / "sessions"
    out_dir = root / "data" / "session_events"
    started = time.time()
    per_session: Dict[str, Any] = {}
    quarantine: List[Dict[str, Any]] = []
    total_rows = total_events = total_written = 0

    for session_dir in sorted(p for p in sessions_dir.iterdir() if p.is_dir()) if sessions_dir.exists() else []:
        src = session_dir / "messages.jsonl"
        if not src.exists():
            continue
        got = project_session(session_dir.name, src, out_dir, dry_run=dry_run)
        per_session[got["sid"]] = {k: got[k] for k in ("rows", "events", "written")}
        quarantine.extend(got["quarantine"])
        total_rows += got["rows"]
        total_events += got["events"]
        total_written += got["written"]

    # 冻结池：data/messages.jsonl 里无 session_id 的行（口径 §13）——只计数、不迁移
    legacy_path = root / "data" / "messages.jsonl"
    legacy_rows = legacy_skipped = 0
    if legacy_path.exists():
        for line in legacy_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            legacy_rows += 1
            try:
                rec = json.loads(line)
            except ValueError:
                legacy_skipped += 1
                continue
            if not str((rec or {}).get("session_id") or "").strip():
                legacy_skipped += 1

    report = {
        "schema_version": 1,
        "mode": "phase-a-read-only-projection",
        "dry_run": bool(dry_run),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "sessions": len(per_session),
        "rows": total_rows,
        "events": total_events,
        "written": total_written,
        "legacy_rows": legacy_rows,
        "skipped_legacy": legacy_skipped,
        "quarantine": quarantine,
        "per_session": per_session,
        "elapsed_s": round(time.time() - started, 2),
    }
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "_migration_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return report


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="会话事件溯源 Phase A：只读投影（幂等、不改源文件）")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--dry-run", action="store_true", help="只算不写（用于核对）")
    ap.add_argument("--json", action="store_true", help="把报告打到 stdout")
    args = ap.parse_args(argv)
    report = project_all(Path(args.root), dry_run=args.dry_run)
    print("[session_events] sessions=%s rows=%s events=%s written=%s skipped_legacy=%s quarantine=%s%s" % (
        report["sessions"], report["rows"], report["events"], report["written"],
        report["skipped_legacy"], len(report["quarantine"]), " (dry-run)" if report["dry_run"] else ""))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
