"""从 rrf_fusion_v4_report.json 提取关键指标摘要"""
import json
import sys
from pathlib import Path

report_path = Path("tests/eval/rrf_fusion_v4_report.json")
r = json.loads(report_path.read_text(encoding="utf-8"))

print("=" * 100)
print("  四方对比摘要")
print("=" * 100)
print()
print(f"  {'方法':<14} {'Precision@3':>14} {'Recall@3':>14} {'MRR':>14} "
      f"{'0分用例':>10} {'fallback':>10}")
print("  " + "-" * 90)

baseline_p = r["tfidf"]["overall"]["precision"]
for m in ["tfidf", "vector", "rrf", "rrf_rerank"]:
    if not r.get(m):
        continue
    x = r[m]
    p = x["overall"]["precision"]
    delta = p - baseline_p
    marker = ""
    if m != "tfidf":
        if delta > 0.0001:
            marker = f"  Δ={delta:+.4f}"
        elif delta < -0.0001:
            marker = f"  Δ={delta:+.4f}"
        else:
            marker = "  (持平)"
    print(f"  {m:<14} {p:>14.4f} {x['overall']['recall']:>14.4f} "
          f"{x['overall']['mrr']:>14.4f} "
          f"{x['zero_case_count']:>10} {x['fallback_count']:>10}{marker}")
print()

# 按难度分组（仅 RRF vs RRF+Reranker）
print("=" * 100)
print("  按难度分组（RRF vs RRF+Reranker）")
print("=" * 100)
print()
print(f"  {'难度':<10} {'RRF P':>10} {'RRF MRR':>10} "
      f"{'RRF+Rk P':>10} {'RRF+Rk MRR':>12} {'Δ P':>10} {'Δ MRR':>10}")
print("  " + "-" * 80)
for diff in ["easy", "medium", "hard", "tricky"]:
    rrf_d = r["rrf"]["by_difficulty"].get(diff, {})
    rk_d = r["rrf_rerank"]["by_difficulty"].get(diff, {})
    rp = rrf_d.get("precision", 0)
    rm = rrf_d.get("mrr", 0)
    kp = rk_d.get("precision", 0)
    km = rk_d.get("mrr", 0)
    print(f"  {diff:<10} {rp:>10.4f} {rm:>10.4f} "
          f"{kp:>10.4f} {km:>12.4f} {kp-rp:>+10.4f} {km-rm:>+10.4f}")
print()

# 逐用例 rerank_score 统计
print("=" * 100)
print("  Cross-Encoder rerank_score 判别力分析（仅显示有真匹配的用例）")
print("=" * 100)
print()
print(f"  {'case_id':<10} {'query':<30} {'top1_skill':<22} {'rrf_score':>10} {'rerank_score':>14}")
print("  " + "-" * 100)
for tc, rc, kc in zip(r["tfidf"]["cases"], r["rrf"]["cases"], r["rrf_rerank"]["cases"]):
    if not tc["expected"]:
        continue
    if not kc["actual"]:
        continue
    top_skill = kc["actual"][0]
    top_breakdown = kc["actual_breakdowns"][0] if kc["actual_breakdowns"] else {}
    rerank_sc = top_breakdown.get("rerank_score", 0) if top_breakdown else 0
    rrf_sc = top_breakdown.get("rrf_normalized", 0) if top_breakdown else 0
    query = kc["query"][:28]
    print(f"  {kc['case_id']:<10} {query:<30} {top_skill:<22} {rrf_sc:>10.3f} {rerank_sc:>+14.3f}")
print()

# 负样本用例
print("=" * 100)
print("  负样本用例（expected=[]）的 rerank_score 应该接近 0")
print("=" * 100)
print()
for kc in r["rrf_rerank"]["cases"]:
    if kc["expected"]:
        continue
    print(f"  {kc['case_id']:<10} query={kc['query']}")
    if kc["actual_breakdowns"]:
        for sid, bd in zip(kc["actual"], kc["actual_breakdowns"]):
            if bd:
                print(f"    {sid:<22} rerank={bd.get('rerank_score', 0):+.4f}")
    print()
