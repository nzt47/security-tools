#!/usr/bin/env python3
"""L1 遗留修复：生产审计链完整性问题 — 恢复 + 重链接 + 日根重生成

处置原则：
  1. **不做删除**：只 INSERT 缺失的日志行、只 UPDATE 两个哈希列、只重签日根。
  2. **先 dry-run**：默认只报告不落盘；`--apply` 才写。
  3. 全程使用生产代码（agent.audit.chain）计算，避免自造哈希口径。

三类动作：
  A) WAL 恢复  —— 把 seqjournal 中「已分配但从未入库」的行插回 DB（内容零改写）
  B) 重链接    —— 从首个 prev_hash 失配的 seq 起，重算 prev_hash/self_hash
  C) 日根重生成 —— 按重新链接后的 self_hash 重算并重签每日 Merkle 根
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import shutil
import hashlib
import datetime

def _find_root(start):
    d = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(d, "agent", "audit", "chain.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            raise RuntimeError("repo root not found")
        d = parent


_ROOT = _find_root(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent.audit.chain import (  # noqa: E402
    GENESIS_PREV_HASH, AuditChain, DailyRoot, RootsSigner, canonical_json,
    compute_self_hash, merkle_root, sha256_hex, day_of_ts,
)

COLS = ["seq", "ts", "actor", "action", "subject", "payload_hash", "prev_hash",
        "self_hash", "source", "trace_id", "workspace_id", "schema_version", "payload"]


def log(msg, fh=None):
    print(msg)
    if fh:
        fh.write(msg + "\n")


def table_columns(con):
    return [r[1] for r in con.execute("PRAGMA table_info(audit_chain)")]


def load_rows(con):
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute("select * from audit_chain order by seq")]


def load_journal(path):
    rows = []
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            t = line.strip()
            if not t:
                continue
            try:
                d = json.loads(t)
            except Exception:
                continue
            if isinstance(d, dict) and d.get("seq"):
                rows.append(d)
    return rows


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--journal", required=True)
    p.add_argument("--roots", default="")
    p.add_argument("--key", default="")
    p.add_argument("--log", default="")
    p.add_argument("--apply", action="store_true", help="真正落盘（缺省 dry-run）")
    p.add_argument("--relink", action="store_true", help="执行 B) 重链接")
    p.add_argument("--reseal", action="store_true", help="执行 C) 日根重生成")
    p.add_argument("--receipt", default="", help="回执 JSON 路径")
    args = p.parse_args(argv)

    fh = open(args.log, "w", encoding="utf-8") if args.log else None
    def P(m):
        log(m, fh)

    phase = "APPLY" if args.apply else "DRY-RUN"
    P(f"===== L1 audit-chain repair [{phase}] =====")
    P(f"db      = {args.db}")
    P(f"journal = {args.journal}")
    P(f"roots   = {args.roots}")
    P(f"relink  = {args.relink}   reseal = {args.reseal}")

    con = sqlite3.connect(args.db)
    cols = table_columns(con)
    before_rows = load_rows(con)
    before = {r["seq"]: r for r in before_rows}
    P(f"\n[before] rows={len(before_rows)} seq={before_rows[0]['seq']}..{before_rows[-1]['seq']}")

    # ── 链路自检（恢复前） ─────────────────────────────────────
    def link_report(rows_by_seq):
        seqs = sorted(rows_by_seq)
        breaks = []
        prev = None
        for s in seqs:
            r = rows_by_seq[s]
            if prev is None:
                if r["prev_hash"] != GENESIS_PREV_HASH:
                    breaks.append((s, "genesis", r["prev_hash"], GENESIS_PREV_HASH))
            elif r["prev_hash"] != prev["self_hash"]:
                breaks.append((s, "prev_hash", r["prev_hash"], prev["self_hash"]))
            prev = r
        holes = [s for s in range(seqs[0], seqs[-1] + 1) if s not in rows_by_seq]
        return breaks, holes

    b, h = link_report(before)
    P(f"[before] holes={len(h)} {h if len(h) < 25 else str(h[:25]) + '...'}")
    P(f"[before] link_breaks={len(b)}")
    for s, kind, got, exp in b:
        P(f"    seq={s} {kind}: got={got[:16]} expected={exp[:16]}")

    # ── A) WAL 恢复 ───────────────────────────────────────────
    jrows = load_journal(args.journal)
    jby = {int(r["seq"]): r for r in jrows}
    P(f"\n[A] journal rows={len(jrows)} seq={min(jby)}..{max(jby)}")
    missing = [s for s in range(1, before_rows[-1]["seq"] + 1) if s not in before]
    recoverable = [s for s in missing if s in jby]
    unrecoverable = [s for s in missing if s not in jby]
    P(f"[A] db holes={len(missing)} {missing}")
    P(f"[A] recoverable from journal = {len(recoverable)} {recoverable}")
    P(f"[A] NOT in journal (unrecoverable) = {len(unrecoverable)} {unrecoverable}")
    for s in recoverable:
        jr = jby[s]
        ok = (before.get(s - 1, {}).get("self_hash") == jr.get("prev_hash"))
        P(f"    seq={s}: journal prev_hash matches db seq {s-1} self_hash = {ok}"
          f" | self_hash={str(jr.get('self_hash'))[:16]} ts={jr.get('ts')} "
          f"actor={jr.get('actor')} action={jr.get('action')}")

    if args.apply and recoverable:
        placeholders = ",".join(["?"] * len(COLS))
        with con:
            for s in recoverable:
                jr = jby[s]
                con.execute(
                    f"INSERT INTO audit_chain ({','.join(COLS)}) VALUES ({placeholders})",
                    [int(jr["seq"]), str(jr["ts"]), str(jr["actor"]), str(jr["action"]),
                     str(jr.get("subject") or ""), str(jr["payload_hash"]),
                     str(jr["prev_hash"]), str(jr["self_hash"]),
                     str(jr.get("source") or "agent"), str(jr.get("trace_id") or ""),
                     str(jr.get("workspace_id") or ""), int(jr.get("schema_version") or 1),
                     str(jr.get("payload") or "{}")])
        P(f"[A] INSERTED {len(recoverable)} rows")

    work = load_rows(con)
    byseq = {r["seq"]: r for r in work}
    P(f"\n[after A] rows={len(work)} holes={len([s for s in range(1, work[-1]['seq']+1) if s not in byseq])}")

    # ── B) 重链接 ────────────────────────────────────────────
    breaks_a, holes_a = link_report(byseq)
    P(f"[B] link breaks after A = {len(breaks_a)} -> {[(s, k) for s, k, _, _ in breaks_a]}")
    updates = []          # (seq, new_prev, new_self, old_prev, old_self)
    new_hashes = {}       # seq -> self_hash (post-repair)
    prev_self = None
    dirty = False
    for s in sorted(byseq):
        r = byseq[s]
        want_prev = GENESIS_PREV_HASH if prev_self is None else prev_self
        if r["prev_hash"] != want_prev:
            dirty = True
        if dirty:
            nh = compute_self_hash(seq=s, ts=r["ts"], actor=r["actor"], action=r["action"],
                                   subject=r["subject"], payload_hash=r["payload_hash"],
                                   prev_hash=want_prev)
            if r["prev_hash"] != want_prev or r["self_hash"] != nh:
                updates.append((s, want_prev, nh, r["prev_hash"], r["self_hash"]))
            new_hashes[s] = nh
        else:
            # 未污染前缀：保持原值（并断言自洽）
            chk = compute_self_hash(seq=s, ts=r["ts"], actor=r["actor"], action=r["action"],
                                    subject=r["subject"], payload_hash=r["payload_hash"],
                                    prev_hash=r["prev_hash"])
            assert chk == r["self_hash"], f"pre-existing self_hash mismatch at seq={s}"
            new_hashes[s] = r["self_hash"]
        prev_self = new_hashes[s]
    first_dirty = updates[0][0] if updates else None
    P(f"[B] relink first affected seq = {first_dirty}  rows to update = {len(updates)}")
    if updates:
        P(f"[B]   range {updates[0][0]}..{updates[-1][0]}")
        for s, np_, ns, op, os_ in updates[:4]:
            P(f"      seq={s} prev {op[:12]}->{np_[:12]}  self {os_[:12]}->{ns[:12]}")
        if len(updates) > 4:
            P(f"      ... ({len(updates)-4} more)")
    if args.apply and args.relink and updates:
        with con:
            con.executemany("UPDATE audit_chain SET prev_hash=?, self_hash=? WHERE seq=?",
                            [(np_, ns, s) for s, np_, ns, _, _ in updates])
        P(f"[B] UPDATED {len(updates)} rows")

    work2 = load_rows(con)
    byseq2 = {r["seq"]: r for r in work2}
    b2, h2 = link_report(byseq2)
    P(f"[after B] holes={len(h2)} link_breaks={len(b2)}")
    for s, kind, got, exp in b2[:10]:
        P(f"    seq={s} {kind}: got={got[:16]} expected={exp[:16]}")

    # 逐条自洽校验
    bad = 0
    for s, r in byseq2.items():
        chk = compute_self_hash(seq=s, ts=r["ts"], actor=r["actor"], action=r["action"],
                                subject=r["subject"], payload_hash=r["payload_hash"],
                                prev_hash=r["prev_hash"])
        if chk != r["self_hash"]:
            bad += 1
    P(f"[after B] self_hash self-consistency failures = {bad}")

    # ── C) 日根重生成 ────────────────────────────────────────
    if args.roots and os.path.exists(args.roots):
        recs = []
        with open(args.roots, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    recs.append(json.loads(line))
        P(f"\n[C] existing root entries = {len(recs)}")
        chain_r = AuditChain.reader(args.db, roots_path=args.roots, signing_enabled=False)
        signer = RootsSigner(args.key or None, enabled=True) if args.key else None
        if signer is not None:
            P(f"[C] signer scheme={signer.scheme} degraded={signer.degraded} pubkey={signer.public_key_hex[:24]}")
        newrecs = []
        prev_eh = GENESIS_PREV_HASH
        for rec in recs:
            day = rec["date"]
            ents = chain_r.entries(day=day)
            leaves = [e.self_hash for e in ents]
            old_root = rec["root_hash"]
            nr = merkle_root(leaves)
            obj = DailyRoot(
                date=day, root_hash=nr, leaf_count=len(leaves),
                first_seq=ents[0].seq if ents else 0,
                last_seq=ents[-1].seq if ents else 0,
                first_self_hash=ents[0].self_hash if ents else "",
                last_self_hash=ents[-1].self_hash if ents else "",
            )
            obj.algorithm = rec.get("algorithm", obj.algorithm)
            obj.created_at = rec.get("created_at", "")
            obj.protected = bool(rec.get("protected", False))
            obj.schema_version = int(rec.get("schema_version", 1))
            if signer is not None:
                obj.signature_scheme = signer.scheme
                obj.degraded = signer.degraded
                obj.degraded_reason = signer.degraded_reason
                obj.signer_public_key = signer.public_key_hex
                obj.signature = signer.sign(obj.signed_message())
            obj.prev_entry_hash = prev_eh
            obj.entry_hash = obj.compute_entry_hash(prev_eh)
            prev_eh = obj.entry_hash
            newrecs.append(obj)
            P(f"    {day}: leaf {rec['leaf_count']}->{obj.leaf_count}  seq "
              f"{rec['first_seq']}..{rec['last_seq']} -> {obj.first_seq}..{obj.last_seq}  "
              f"root {old_root[:12]}->{nr[:12]}  changed={old_root != nr}")
        if args.apply and args.reseal:
            try:
                os.chmod(args.roots, 0o666)
            except OSError:
                pass
            tmp = args.roots + ".l1new"
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                for o in newrecs:
                    f.write(canonical_json(o.to_dict()) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, args.roots)
            P(f"[C] REWROTE {args.roots} with {len(newrecs)} re-signed entries")

    # ── D) 同步 journal 哈希字段（否则下一次 append 会立刻再次断链） ──
    jmiss = []
    if args.apply and args.relink and updates:
        chg = {s: (np_, ns) for s, np_, ns, _, _ in updates}
        out_lines = []
        touched = 0
        for r in jrows:
            s = int(r["seq"])
            if s in chg:
                r = dict(r)
                r["prev_hash"], r["self_hash"] = chg[s]
                touched += 1
            out_lines.append(json.dumps(r, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":"), default=str))
        tmp = args.journal + ".l1new"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(out_lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, args.journal)
        P(f"\n[D] journal hash fields rewrote: {touched} rows (of {len(jrows)})")
        # 断言 journal 头部与 DB 头部一致
        con.row_factory = sqlite3.Row
        head = dict(con.execute("select * from audit_chain order by seq desc limit 1").fetchone())
        last = json.loads(out_lines[-1])
        same = (int(last["seq"]) == head["seq"] and last["self_hash"] == head["self_hash"])
        P(f"[D] journal head == db head ? {same}  (journal seq={last['seq']}, db seq={head['seq']})")

    if args.receipt:
        with open(args.receipt, "w", encoding="utf-8") as f:
            json.dump({
                "phase": phase,
                "db": args.db, "journal": args.journal, "roots": args.roots,
                "before": {"rows": len(before_rows), "breaks": len(b),
                           "holes": missing},
                "recovered_seqs": recoverable, "unrecoverable_seqs": unrecoverable,
                "relink_first_affected": first_dirty, "relink_rows": len(updates),
                "after": {"rows": len(work2), "breaks": len(b2), "holes": len(h2),
                          "self_consistency_failures": bad},
                "ts": datetime.datetime.now().isoformat(),
            }, f, ensure_ascii=False, indent=2)
    con.close()
    if fh:
        fh.close()
    print(f"\n[{'APPLY' if args.apply else 'DRY-RUN'}] done; breaks before={len(b)} after={len(b2)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
