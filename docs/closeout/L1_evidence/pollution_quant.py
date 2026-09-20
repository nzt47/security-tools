"""Quantify pollution with a REPRODUCIBLE criterion: subject token is a literal in tracked tests/**."""
import json, os, re, subprocess, sqlite3, collections, sys
sys.path.insert(0, os.getcwd())
out = open('_ci_logs/l1_repair/pollution_quant.log', 'w', encoding='utf-8')
def P(*a):
    print(*a); print(*a, file=out)

con = sqlite3.connect('file:data/audit/audit_chain.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute("select * from audit_chain order by seq")]
P(f"total rows = {len(rows)}")

# collect tracked test-file text once
files = subprocess.run(['git', 'ls-files', 'tests/'], capture_output=True, text=True).stdout.split()
P(f"tracked test files = {len(files)}")
blob = []
for f in files:
    try:
        blob.append(open(f, encoding='utf-8', errors='ignore').read())
    except OSError:
        pass
TESTSRC = "\n".join(blob)
P(f"test source bytes = {len(TESTSRC)}")
TESTSET = set(re.findall(r'[A-Za-z_][A-Za-z0-9_\-\.]{4,}', TESTSRC))
P(f"test-source identifier tokens = {len(TESTSET)}")

# candidate subject tokens: for each distinct subject, extract identifier-ish tokens
def toks(s):
    return set(re.findall(r'[A-Za-z_][A-Za-z0-9_\-\.]{4,}', s or ''))
subjects = collections.Counter(r['subject'] for r in rows)
P(f"distinct subjects = {len(subjects)}")

def is_fixture(r):
    """Reproducible criterion: some >=5-char token of subject/action is a literal in tests/**."""
    hits = []
    for t in toks(r['subject']) | toks(r['action']):
        if t in TESTSET:
            hits.append(t)
    return hits

fixture_rows, real_rows = [], []
for r in rows:
    h = is_fixture(r)
    if h:
        r['_hits'] = h
        fixture_rows.append(r)
    else:
        real_rows.append(r)

P(f"\n=== CRITERION 1: subject/action token is a verbatim literal in a tracked tests/ file ===")
P(f"  fixture-sourced rows = {len(fixture_rows)}")
P(f"  no-token-hit rows    = {len(real_rows)}")
by_hit = collections.Counter(h for r in fixture_rows for h in r['_hits'])
P("  top matched tokens:")
for t, c in by_hit.most_common(25):
    P(f"    {c:6d}  {t}")

P("\n  actor / ts / action breakdown of fixture rows:")
P(f"    actors : {collections.Counter(r['actor'] for r in fixture_rows).most_common(12)}")
P(f"    sources: {collections.Counter(r['source'] for r in fixture_rows).most_common()}")
P(f"    actions: {collections.Counter(r['action'] for r in fixture_rows).most_common(12)}")
ts = [r['ts'] for r in fixture_rows]
P(f"    ts range: {min(ts)} .. {max(ts)}")

P("\n=== CRITERION 2: ts outside the real operating window [2026-09-12 .. 2026-09-21] ===")
def outwin(r):
    d = r['ts'][:10]
    return not ('2026-09-12' <= d <= '2026-09-21')
oor = [r for r in rows if outwin(r)]
P(f"  rows with synthetic/past/future ts = {len(oor)}")
P(f"    ts range: {min(r['ts'] for r in oor)} .. {max(r['ts'] for r in oor)}")
P(f"    actors: {collections.Counter(r['actor'] for r in oor).most_common(10)}")
P(f"    actions: {collections.Counter(r['action'] for r in oor).most_common(10)}")
P(f"    example seqs: {[r['seq'] for r in oor][:15]}")

P("\n=== CRITERION 3: the specific fixture families the brief named ===")
FAM = {
    'tool_call:probe_approval_e2e_tool': lambda r: 'probe_approval_e2e_tool' in r['subject'],
    'probe_* (subject prefix)':          lambda r: r['subject'].startswith('probe_'),
    'env:SAME_KEY':                      lambda r: r['subject'] == 'env:SAME_KEY',
    'env:CONCURRENT_KEY_*':              lambda r: r['subject'].startswith('env:CONCURRENT_KEY'),
    'my-skill':                          lambda r: 'my-skill' in r['subject'],
    'skill-p':                           lambda r: 'skill-p' in r['subject'],
    "__sample__":                        lambda r: r['subject'] == '__sample__',
    '__selftest_*':                      lambda r: r['subject'].startswith('__selftest'),
    'global_test_action':                lambda r: r['action'] == 'global_test_action',
    'definitely_not_registered_tool_xyz':lambda r: 'definitely_not_registered' in r['subject'],
}
for name, fn in FAM.items():
    sel = [r for r in rows if fn(r)]
    tss = [r['ts'] for r in sel]
    P(f"  {name:38s} n={len(sel):5d} seq={[r['seq'] for r in sel][:5]} ts={min(tss) if tss else '-'}..{max(tss) if tss else '-'}")

P("\n=== UNION of all criteria -> pollution total ===")
pol = {r['seq'] for r in fixture_rows} | {r['seq'] for r in oor}
for fn in FAM.values():
    pol |= {r['seq'] for r in rows if fn(r)}
P(f"  union(pollution) = {len(pol)}  ({len(pol)*100.0/len(rows):.1f}% of ledger)")
P(f"  clean            = {len(rows) - len(pol)}")
P(f"  NO row deleted; pollution is retained (recommend annotate, not purge)")
out.close()
