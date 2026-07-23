"""检查 v6 评估报告 - 详细结构探查"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
data = json.load(open(ROOT / "tests/eval/negative_rejection_v6_verify.json",
                      "r", encoding="utf-8"))

neg_rerank = data["negatives_rerank"]
print("=== negatives_rerank keys ===")
print(list(neg_rerank.keys()))
print()
print("=== summary ===")
for k in ("total", "correctly_rejected", "wrongly_recalled", "rejection_rate"):
    if k in neg_rerank:
        print(f"  {k}: {neg_rerank[k]}")
print()

# 找 cases
cases = neg_rerank.get("cases") or neg_rerank.get("details") or []
print(f"=== cases count: {len(cases)} ===")
if cases:
    print("first case keys:", list(cases[0].keys()))
    print()

print("=== 误召回的负样本 ===")
for c in cases:
    actual = c.get("actual") or c.get("actual_skill_ids") or []
    is_rejected = c.get("correctly_rejected")
    if is_rejected is None:
        is_rejected = (len(actual) == 0)
    if not is_rejected:
        print(f"  {c.get('case_id')} [{c.get('category')}] {c.get('query')}")
        print(f"    actual: {actual}")
        print(f"    retrieval_method: {c.get('retrieval_method')}")
        print(f"    actual_scores: {c.get('actual_scores')}")

print()
print("=== query_pattern 命中的负样本 ===")
hit = 0
for c in cases:
    rm = c.get("retrieval_method", "")
    if rm == "query_pattern":
        hit += 1
        print(f"  {c.get('case_id')} [{c.get('category')}] {c.get('query')}")
print(f"  总计: {hit} 个 query_pattern 命中")

print()
print("=== 5 类 0% 类别负样本的检索方法 ===")
target_cats = {"negative_keyword_trap", "negative_similar", "negative_translation",
               "negative_creative", "negative_math"}
for c in cases:
    if c.get("category") in target_cats:
        actual = c.get("actual") or []
        print(f"  {c.get('case_id'):12} [{c.get('category'):25}] {c.get('query'):30} "
              f"method={c.get('retrieval_method'):15} actual={actual}")
