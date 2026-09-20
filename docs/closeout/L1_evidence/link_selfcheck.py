"""Per-row link self-check + durability test (append must chain onto the repaired head)."""
import json, os, sqlite3, sys
sys.path.insert(0, os.getcwd())
from agent.audit.chain import GENESIS_PREV_HASH, compute_self_hash, AuditChain

out = open('_ci_logs/l1_repair/link_selfcheck.log', 'w', encoding='utf-8')
def P(*a):
    print(*a); print(*a, file=out)

DB = 'data/audit/audit_chain.db'
con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute("select * from audit_chain order by seq")]

breaks = []
prev = None
for r in rows:
    want = GENESIS_PREV_HASH if prev is None else prev['self_hash']
    if r['prev_hash'] != want:
        breaks.append((r['seq'], 'prev_hash'))
    chk = compute_self_hash(seq=r['seq'], ts=r['ts'], actor=r['actor'], action=r['action'],
                            subject=r['subject'], payload_hash=r['payload_hash'],
                            prev_hash=r['prev_hash'])
    if chk != r['self_hash']:
        breaks.append((r['seq'], 'self_hash'))
    prev = r

P("=== PER-ROW LINK SELF-CHECK (production data/audit/audit_chain.db) ===")
P(f"rows checked           : {len(rows)}")
P(f"seq span               : {rows[0]['seq']} .. {rows[-1]['seq']}")
P(f"expected span size     : {rows[-1]['seq'] - rows[0]['seq'] + 1}")
P(f"seq holes              : {rows[-1]['seq'] - rows[0]['seq'] + 1 - len(rows)}")
P(f"first row prev_hash    : {rows[0]['prev_hash']}  (genesis = {'0'*64})")
P(f"genesis correct        : {rows[0]['prev_hash'] == GENESIS_PREV_HASH}")
P(f"LINK BREAKS            : {len(breaks)}   {breaks[:10]}")
P(f"CHAIN HEAD self_hash   : {rows[-1]['self_hash']}")
P(f"VERDICT                : {'CONTIGUOUS / UNBROKEN' if not breaks else 'BROKEN'}")

# --- durability: append to the SANDBOX and confirm it chains onto the repaired head ---
sb = '_ci_logs/l1_repair/sandbox'
P("\n=== DURABILITY TEST (sandbox copy of the repaired chain) ===")
dbp = os.path.join(sb, 'audit_chain.db')
if os.path.exists(dbp):
    before = sqlite3.connect(f'file:{dbp}?mode=ro', uri=True).execute(
        "select seq, self_hash from audit_chain order by seq desc limit 1").fetchone()
    P(f"sandbox head before append: seq={before[0]} self_hash={before[1][:16]}")
    ch = AuditChain(dbp, roots_path=os.path.join(sb, 'daily_roots.jsonl'))
    try:
        e = ch.append("l1.durability.probe", actor="l1_repair",
                      subject="durability", payload={"probe": True})
        ch.flush(timeout=10)
        ch.close(timeout=10)
        P(f"appended seq={e.seq} prev_hash={e.prev_hash[:16]} self_hash={e.self_hash[:16]}")
        P(f"chained onto repaired head: {e.prev_hash == before[1]}")
    except Exception as exc:
        P(f"append failed: {type(exc).__name__}: {exc}")
    con2 = sqlite3.connect(f'file:{dbp}?mode=ro', uri=True)
    con2.row_factory = sqlite3.Row
    r2 = [dict(x) for x in con2.execute("select * from audit_chain order by seq")]
    b2 = []
    pv = None
    for r in r2:
        want = GENESIS_PREV_HASH if pv is None else pv['self_hash']
        if r['prev_hash'] != want:
            b2.append(r['seq'])
        pv = r
    P(f"sandbox after append: rows={len(r2)} head_seq={r2[-1]['seq']} BREAKS={len(b2)} {b2[:5]}")
out.close()
