"""查看 RRF 三方对比报告的关键指标"""
import json
from pathlib import Path

report_path = Path("tests/eval/rrf_fusion_report.json")
with report_path.open("r", encoding="utf-8") as f:
    data = json.load(f)

print("=" * 80)
print("  RRF 三方对比报告关键指标")
print("=" * 80)
print()

print("【整体指标】")
print(f"  {'方法':<10} {'Precision@3':>14} {'Recall@3':>12} {'MRR':>10} {'0分':>6} {'fallback':>10}")
for m in ["tfidf", "vector", "rrf"]:
    if data.get(m):
        o = data[m]["overall"]
        print(f"  {m:<10} {o['precision']:>14.4f} {o['recall']:>12.4f} "
              f"{o['mrr']:>10.4f} {data[m]['zero_case_count']:>6} "
              f"{data[m]['fallback_count']:>10}")

print()
print("【按难度分组 Precision】")
print(f"  {'难度':<10} {'TF-IDF':>12} {'Vector':>12} {'RRF':>12} {'RRF-T':>10} {'RRF-V':>10}")
for diff in ["easy", "medium", "hard", "tricky"]:
    t = data["tfidf"]["by_difficulty"].get(diff, {}).get("precision", 0)
    v = data["vector"]["by_difficulty"].get(diff, {}).get("precision", 0)
    r = data["rrf"]["by_difficulty"].get(diff, {}).get("precision", 0)
    print(f"  {diff:<10} {t:>12.4f} {v:>12.4f} {r:>12.4f} "
          f"{r-t:>+10.4f} {r-v:>+10.4f}")

print()
print("【按类别分组 Precision】")
all_cats = sorted(set(data["tfidf"]["by_category"].keys()) |
                  set(data["vector"]["by_category"].keys()) |
                  set(data["rrf"]["by_category"].keys()))
print(f"  {'类别':<20} {'TF-IDF':>12} {'Vector':>12} {'RRF':>12} {'RRF-T':>10} {'RRF-V':>10}")
for cat in all_cats:
    t = data["tfidf"]["by_category"].get(cat, {}).get("precision", 0)
    v = data["vector"]["by_category"].get(cat, {}).get("precision", 0)
    r = data["rrf"]["by_category"].get(cat, {}).get("precision", 0)
    print(f"  {cat:<20} {t:>12.4f} {v:>12.4f} {r:>12.4f} "
          f"{r-t:>+10.4f} {r-v:>+10.4f}")

print()
print("【逐用例 RRF vs TF-IDF 变化（仅显示有变化的）】")
changed = improved = regressed = 0
for tc, rc in zip(data["tfidf"]["cases"], data["rrf"]["cases"]):
    if tc["precision"] != rc["precision"] or tc["actual"] != rc["actual"]:
        changed += 1
        delta = rc["precision"] - tc["precision"]
        if delta > 0:
            improved += 1
            sign = "↑"
        elif delta < 0:
            regressed += 1
            sign = "↓"
        else:
            sign = "→"
        print(f"  {sign} {tc['case_id']:<10} [{tc['difficulty']:<7}/{tc['category']:<14}] "
              f"P: {tc['precision']:.2f} → {rc['precision']:.2f} (Δ={delta:+.2f})")
        print(f"    query : {tc['query']}")
        print(f"    tfidf : {tc['actual']}")
        print(f"    rrf   : {rc['actual']}")

print()
print(f"  共 {changed} 个用例变化：{improved} 改善, {regressed} 退化, {changed-improved-regressed} 平移")

print()
print("【负样本（tricky/negative）详情】")
for tc, rc in zip(data["tfidf"]["cases"], data["rrf"]["cases"]):
    if tc["category"] == "negative":
        print(f"  {tc['case_id']:<10} query: {tc['query']}")
        print(f"    tfidf: {tc['actual']} (P={tc['precision']:.2f})")
        print(f"    rrf  : {rc['actual']} (P={rc['precision']:.2f})")
