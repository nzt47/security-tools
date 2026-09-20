import collections, json, sqlite3, sys, os
sys.path.insert(0, os.getcwd())
out = open('_ci_logs/l1_repair/pollution_tiers.log', 'w', encoding='utf-8')
def P(*a):
    print(*a); print(*a, file=out)
con = sqlite3.connect('file:data/audit/audit_chain.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute("select * from audit_chain order by seq")]
P(f"total rows = {len(rows)}\n")

TIER_A = {
    'A1 tool_call:probe_approval_e2e_tool': lambda r: 'probe_approval_e2e_tool' in r['subject'],
    'A2 probe_* subject prefix':            lambda r: r['subject'].startswith('probe_'),
    'A3 env:SAME_KEY':                      lambda r: r['subject'] == 'env:SAME_KEY',
    'A4 env:CONCURRENT_KEY_*':              lambda r: r['subject'].startswith('env:CONCURRENT_KEY'),
    'A5 my-skill':                          lambda r: 'my-skill' in r['subject'],
    'A6 skill-p':                           lambda r: 'skill-p' in r['subject'],
    'A7 __sample__ (disclosed by subagent)':lambda r: r['subject'] == '__sample__',
    'A8 __selftest_* (in-test selftest)':   lambda r: r['subject'].startswith('__selftest'),
    'A9 action=global_test_action':         lambda r: r['action'] == 'global_test_action',
    'A10 definitely_not_registered_tool_xyz':lambda r: 'definitely_not_registered' in r['subject'],
}
P("=== TIER A — high confidence: named fixture families / explicit test markers ===")
A = set()
fam = {}
for n, fn in TIER_A.items():
    sel = [r for r in rows if fn(r)]
    fam[n] = {r['seq'] for r in sel}
    A |= fam[n]
    tss = [r['ts'] for r in sel]
    P(f"  {n:42s} n={len(sel):5d}  ts {min(tss) if tss else '-'} .. {max(tss) if tss else '-'}")
P(f"  --> TIER A union = {len(A)} rows  ({len(A)*100.0/len(rows):.2f}%)")

P("\n=== TIER B — synthetic clock: ts outside real operating window 2026-09-12..2026-09-21 ===")
B = {r['seq'] for r in rows if not ('2026-09-12' <= r['ts'][:10] <= '2026-09-21')}
P(f"  n = {len(B)}  ({len(B)*100.0/len(rows):.2f}%)")
P(f"  ts span: {min(r['ts'] for r in rows if r['seq'] in B)} .. {max(r['ts'] for r in rows if r['seq'] in B)}")
# backfill caveat
bf = {r['seq'] for r in rows if r['actor'] == 'backfill:s1-02'}
P(f"  of which actor='backfill:s1-02' (possibly legitimate historical backfill) = {len(B & bf)}")

P("\n=== TIER C — strict token test-only match (UPPER BOUND, may over-count) ===")
P(open('_ci_logs/l1_repair/pollution_quant.log', encoding='utf-8').read().split('=== CRITERION 1 (strict)')[0][:0] or "")
P("  see pollution_strict.py -> 5699 rows (28.35%); includes production identifiers that are")
P("  built dynamically in prod code (e.g. cp.fs.read) so it OVER-counts. Use as ceiling only.")

P("\n=== TIER A ∪ TIER B (recommended working figure) ===")
AB = A | B
P(f"  union = {len(AB)}  ({len(AB)*100.0/len(rows):.2f}%)")
P(f"  clean = {len(rows)-len(AB)}  ({(len(rows)-len(AB))*100.0/len(rows):.2f}%)")

P("\n=== per-day ledger composition (ts date) ===")
byday = collections.Counter(r['ts'][:10] for r in rows)
for d, c in sorted(byday.items()):
    pa = sum(1 for r in rows if r['ts'][:10] == d and r['seq'] in A)
    P(f"  {d}: total={c:6d}  tierA={pa:5d}")

P("\n=== the 3 __sample__ rows (subagent-disclosed E9 sample) ===")
for r in rows:
    if r['subject'] == '__sample__':
        P(f"  seq={r['seq']} ts={r['ts']} actor={r['actor']} action={r['action']} src={r['source']}")
P("\n=== the 2 definitely_not_registered_tool_xyz rows ===")
for r in rows:
    if 'definitely_not_registered' in r['subject']:
        P(f"  seq={r['seq']} ts={r['ts']} actor={r['actor']} action={r['action']} src={r['source']}")
out.close()
