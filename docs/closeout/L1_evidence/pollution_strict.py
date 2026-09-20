"""Pollution vs real, with a STRICT reproducible criterion:
   a subject/action identifier that appears in tracked tests/** but NOWHERE in tracked
   production code (agent/, static/, scripts/, monitoring/, tools/, config/).
Such an identifier cannot legitimately occur in a production audit record.
"""
import collections, os, re, sqlite3, subprocess, sys
sys.path.insert(0, os.getcwd())
out = open('_ci_logs/l1_repair/pollution_quant.log', 'w', encoding='utf-8')
def P(*a):
    print(*a); print(*a, file=out)

TOKEN = re.compile(r'[A-Za-z_][A-Za-z0-9_\-\.]{4,}')

def read_set(pathspec, exts):
    if isinstance(pathspec, str):
        pathspec = [pathspec]
    files = subprocess.run(['git', 'ls-files', '--'] + list(pathspec),
                           capture_output=True, text=True).stdout.split()
    files = [f for f in files if os.path.splitext(f)[1].lower() in exts]
    toks, n = set(), 0
    for f in files:
        try:
            src = open(f, encoding='utf-8', errors='ignore').read()
        except OSError:
            continue
        n += len(src)
        toks |= set(TOKEN.findall(src))
    return files, n, toks

tf, tn, TESTSET = read_set('tests/', {'.py', '.json', '.yml', '.yaml', '.js'})
pf, pn, PRODSET = read_set(['agent/', 'static/', 'scripts/', 'monitoring/', 'tools/', 'config/'],
                           {'.py', '.js', '.yml', '.yaml', '.json'})
P(f"test files={len(tf)} bytes={tn} tokens={len(TESTSET)}")
P(f"prod files={len(pf)} bytes={pn} tokens={len(PRODSET)}")
TEST_ONLY = TESTSET - PRODSET
P(f"TEST-ONLY tokens (in tests, never in production code) = {len(TEST_ONLY)}")

con = sqlite3.connect('file:data/audit/audit_chain.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute("select * from audit_chain order by seq")]
P(f"\ntotal rows = {len(rows)}")

def hits(r):
    return sorted(t for t in (set(TOKEN.findall(r['subject'] or '')) | set(TOKEN.findall(r['action'] or '')))
                  if t in TEST_ONLY)

hit_rows = []
for r in rows:
    h = hits(r)
    if h:
        r['_h'] = h
        hit_rows.append(r)

P("\n=== CRITERION 1 (strict): subject/action carries a TEST-ONLY identifier ===")
P(f"  polluted rows = {len(hit_rows)}  ({len(hit_rows)*100.0/len(rows):.2f}% of ledger)")
P(f"  clean rows    = {len(rows)-len(hit_rows)}")
P("  matched TEST-ONLY tokens (count desc):")
for t, c in collections.Counter(h for r in hit_rows for h in r['_h']).most_common(40):
    P(f"    {c:6d}  {t}")
P("\n  actor distribution:")
for a, c in collections.Counter(r['actor'] for r in hit_rows).most_common(15):
    P(f"    {c:6d}  {a}")
P("\n  action distribution:")
for a, c in collections.Counter(r['action'] for r in hit_rows).most_common(15):
    P(f"    {c:6d}  {a}")
P(f"\n  source distribution: {collections.Counter(r['source'] for r in hit_rows).most_common()}")
P(f"  workspace_id       : {collections.Counter(r['workspace_id'] for r in hit_rows).most_common(8)}")
ts = [r['ts'] for r in hit_rows]
P(f"  ts range           : {min(ts)} .. {max(ts)}")
P(f"  seq ranges (first 20 holes-in-sequence): {[r['seq'] for r in hit_rows][:20]}")
byseq = collections.Counter(r['seq'] for r in hit_rows)
seqs = sorted(byseq)
runs, cur = [], [seqs[0]]
for s in seqs[1:]:
    if s == cur[-1] + 1:
        cur.append(s)
    else:
        runs.append((cur[0], cur[-1], len(cur))); cur = [s]
runs.append((cur[0], cur[-1], len(cur)))
P(f"  contiguous polluted seq runs (top 12 by size):")
for a, b, n in sorted(runs, key=lambda x: -x[2])[:12]:
    P(f"    seq {a}..{b}  n={n}")

P("\n=== CRITERION 2: ts outside the real operating window 2026-09-12..2026-09-21 ===")
oor = [r for r in rows if not ('2026-09-12' <= r['ts'][:10] <= '2026-09-21')]
P(f"  rows = {len(oor)}  ts {min(r['ts'] for r in oor)} .. {max(r['ts'] for r in oor)}")
P(f"  actors: {collections.Counter(r['actor'] for r in oor).most_common(8)}")
P("  NOTE: includes actor='backfill:s1-02' historical backfill, which may be legitimate;"
  " treat as 'synthetic-clock' evidence, not proof of pollution by itself.")

P("\n=== CRITERION 3: named fixture families ===")
FAM = {
    'tool_call:probe_approval_e2e_tool': lambda r: 'probe_approval_e2e_tool' in r['subject'],
    'probe_* subject prefix':            lambda r: r['subject'].startswith('probe_'),
    'env:SAME_KEY':                      lambda r: r['subject'] == 'env:SAME_KEY',
    'env:CONCURRENT_KEY_*':              lambda r: r['subject'].startswith('env:CONCURRENT_KEY'),
    'my-skill':                          lambda r: 'my-skill' in r['subject'],
    'skill-p':                           lambda r: 'skill-p' in r['subject'],
    '__sample__':                        lambda r: r['subject'] == '__sample__',
    '__selftest_*':                      lambda r: r['subject'].startswith('__selftest'),
    'global_test_action':                lambda r: r['action'] == 'global_test_action',
    'definitely_not_registered_tool_xyz':lambda r: 'definitely_not_registered' in r['subject'],
}
famseq = {}
for name, fn in FAM.items():
    sel = [r for r in rows if fn(r)]
    famseq[name] = {r['seq'] for r in sel}
    tss = [r['ts'] for r in sel]
    P(f"  {name:36s} n={len(sel):5d} ts={min(tss) if tss else '-'}..{max(tss) if tss else '-'}")

C1 = {r['seq'] for r in hit_rows}
C2 = {r['seq'] for r in oor}
C3 = set().union(*famseq.values())
P("\n=== UNION ===")
P(f"  C1 strict test-only-identifier : {len(C1)}")
P(f"  C2 out-of-window ts            : {len(C2)}")
P(f"  C3 named fixture families      : {len(C3)}")
P(f"  C1 ∪ C2 ∪ C3                   : {len(C1|C2|C3)}  ({(len(C1|C2|C3))*100.0/len(rows):.1f}%)")
P(f"  C1 ∪ C3 (high-confidence only) : {len(C1|C3)}")
P(f"  clean under C1∪C3              : {len(rows)-len(C1|C3)}")
out.close()
