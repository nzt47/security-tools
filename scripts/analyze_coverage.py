"""分析覆盖率最低和最高的文件"""
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
cov_data = json.loads((ROOT / "coverage_report" / "coverage.json").read_text(encoding="utf-8"))

files = cov_data["files"]
items = []
for fp, d in files.items():
    s = d.get("summary", {})
    items.append({
        "file": fp.replace("\\", "/"),
        "stmts": s.get("num_statements", 0),
        "covered": s.get("covered_lines", 0),
        "missing": s.get("missing_lines", 0),
        "pct": round(s.get("percent_covered", 0), 2),
    })

items.sort(key=lambda x: x["pct"])

print("=== 覆盖率最低 15 个文件（语句数 > 30） ===")
low = [x for x in items if x["stmts"] > 30][:15]
for x in low:
    print(f"  {x['file']:<60} {x['pct']:>6}%  ({x['covered']}/{x['stmts']})")

print()
print("=== 覆盖率最高 15 个文件（语句数 > 30） ===")
high = [x for x in items if x["stmts"] > 30][-15:]
for x in reversed(high):
    print(f"  {x['file']:<60} {x['pct']:>6}%  ({x['covered']}/{x['stmts']})")

# 统计覆盖率分布
print()
print("=== 覆盖率分布 ===")
buckets = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]
for lo, hi in buckets:
    n = sum(1 for x in items if x["stmts"] > 30 and lo <= x["pct"] < hi)
    print(f"  {lo}-{hi}%: {n} 个文件")
