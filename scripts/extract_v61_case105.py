"""提取 case_105 专项验证数据 + 统计 query_pattern 命中分布"""
import json
from pathlib import Path

neg_data = json.load(
    open("tests/eval/negative_rejection_v6_1_verify.json", "r", encoding="utf-8")
)

# 找到 cases 列表（可能在不同字段下）
cases = []
for key in ["negatives_rerank", "rrf_rerank", "cases", "negatives"]:
    if key in neg_data:
        v = neg_data[key]
        if isinstance(v, dict) and "cases" in v:
            cases = v["cases"]
            break
        elif isinstance(v, list):
            cases = v
            break

print("=" * 70)
print("  case_105 专项验证")
print("=" * 70)
for c in cases:
    if c.get("case_id") == "case_105":
        print(f"case_id: {c['case_id']}")
        print(f"query: {c['query']}")
        print(f"retrieval_method: {c.get('retrieval_method', '?')}")
        actual = c.get("actual") or c.get("retrieved") or c.get("matches") or []
        print(f"actual: {actual}")
        rejected = c.get("correctly_rejected")
        if rejected is None:
            rejected = c.get("rejected")
        if rejected is None:
            rejected = c.get("is_correct")
        print(f"correctly_rejected: {rejected}")
        print(f"category: {c.get('category', '?')}")

print("\n" + "=" * 70)
print("  query_pattern 命中分布（从所有 cases 提取）")
print("=" * 70)
pattern_hits = {}
total_neg = 0
pattern_hit_count = 0
for c in cases:
    total_neg += 1
    method = c.get("retrieval_method", "")
    if method == "query_pattern":
        pattern_hit_count += 1
        # 尝试从日志字段获取 category
        cat = c.get("category") or c.get("pattern_category") or "unknown"
        pattern_hits[cat] = pattern_hits.get(cat, 0) + 1

print(f"负样本总数: {total_neg}")
print(f"query_pattern 命中: {pattern_hit_count}")
print(f"走 RRF+Reranker: {total_neg - pattern_hit_count}")
print(f"\n按 category 分布:")
for cat, n in sorted(pattern_hits.items()):
    print(f"  {cat}: {n}")

# 也从日志文件提取 category
print("\n" + "=" * 70)
print("  从日志文件提取 query_pattern category 分布")
print("=" * 70)
import re
log_cats = {}
for log_file in ["tests/eval/v61_eval_negative.log"]:
    try:
        content = open(log_file, "r", encoding="utf-8").read()
        # 匹配 {"action":"match.query_pattern.rejected",...,"category":"xxx",...}
        for m in re.finditer(r'"action":"match\.query_pattern\.rejected".*?"category":"([^"]+)"', content):
            cat = m.group(1)
            log_cats[cat] = log_cats.get(cat, 0) + 1
    except FileNotFoundError:
        pass

print(f"日志中 query_pattern 命中分布:")
for cat, n in sorted(log_cats.items()):
    print(f"  {cat}: {n}")

print("\n" + "=" * 70)
print("  booking 类别专项检查")
print("=" * 70)
booking_count = log_cats.get("booking", 0)
print(f"booking 类别命中数: {booking_count}")
print(f"预期: 1 (case_105 帮我点外卖)")
if booking_count >= 1:
    print("✅ booking 规则已生效，case_105 被正确拒绝")
else:
    print("⚠️  booking 规则未命中，检查 case_105 是否走 RRF 被拒绝")
