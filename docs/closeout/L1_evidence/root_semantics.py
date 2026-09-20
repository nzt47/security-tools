"""Prove 09-14 root verification failure is a gen/verify semantic mismatch, not tampering."""
import json, sqlite3, sys, os
sys.path.insert(0, os.getcwd())
from agent.audit.chain import merkle_root, AuditChain
out = open('_ci_logs/l1_repair/root_semantics.log', 'w', encoding='utf-8')
def P(*a):
    print(*a); print(*a, file=out)

DB = 'data/audit/audit_chain.db'
recs = [json.loads(l) for l in open('data/audit/daily_roots.jsonl', encoding='utf-8') if l.strip()]
chain = AuditChain.reader(DB, roots_path='data/audit/daily_roots.jsonl', signing_enabled=False)

P("=== GENERATOR semantics = entries(day=DAY)  vs  VERIFIER semantics = entries(start_seq..end_seq) ===")
for r in recs:
    day = r['date']
    by_day = chain.entries(day=day)
    by_range = chain.entries(start_seq=r['first_seq'], end_seq=r['last_seq'])
    md = merkle_root([e.self_hash for e in by_day])
    mr = merkle_root([e.self_hash for e in by_range])
    P(f"\n{day}: recorded leaf_count={r['leaf_count']} root={r['root_hash'][:20]}")
    P(f"   BY-DAY   : n={len(by_day):6d} seq {by_day[0].seq if by_day else 0}..{by_day[-1].seq if by_day else 0}"
      f"  merkle={md[:20]}  MATCHES_RECORD={md == r['root_hash']}")
    P(f"   BY-RANGE : n={len(by_range):6d} (range {r['first_seq']}..{r['last_seq']} = {r['last_seq']-r['first_seq']+1} slots)"
      f"  merkle={mr[:20]}  MATCHES_RECORD={mr == r['root_hash']}")
    foreign = [e.seq for e in by_range if e.ts[:10] != day]
    P(f"   rows in the seq range whose ts is NOT {day}: {len(foreign)}  e.g. seqs {foreign[:8]}")
out.close()
