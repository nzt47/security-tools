"""分析 rerank_score 分布，找最优阈值

从 v4 报告中提取所有用例的 rerank_score，按真匹配/负样本分类，
绘制分布直方图，找出能区分两者的最优阈值
"""
import json
from pathlib import Path

report = json.loads(
    Path("tests/eval/rrf_fusion_v4_report.json").read_text(encoding="utf-8")
)

# 收集所有 rerank_score
true_match_scores = []  # 真匹配（actual 在 expected 中）
false_positive_scores = []  # 误召回（actual 不在 expected 中，且 expected 非空）
negative_scores = []  # 负样本（expected 为空但 actual 非空）

for kc in report["rrf_rerank"]["cases"]:
    expected = set(kc["expected"])
    actual = kc["actual"]
    breakdowns = kc["actual_breakdowns"] or []

    if not expected:
        # 负样本用例
        for sid, bd in zip(actual, breakdowns):
            if bd and "rerank_score" in bd:
                negative_scores.append((kc["case_id"], sid, bd["rerank_score"]))
    else:
        # 有期望的用例
        for sid, bd in zip(actual, breakdowns):
            if bd and "rerank_score" in bd:
                score = bd["rerank_score"]
                if sid in expected:
                    true_match_scores.append((kc["case_id"], sid, score))
                else:
                    false_positive_scores.append((kc["case_id"], sid, score))

print("=" * 100)
print("  rerank_score 分布分析")
print("=" * 100)
print()

print(f"真匹配 (expected 命中): {len(true_match_scores)} 个")
print(f"  最低 5:")
for cid, sid, sc in sorted(true_match_scores, key=lambda x: x[2])[:5]:
    print(f"    {cid:<10} {sid:<22} rerank={sc:+.4f}")
print(f"  最高 5:")
for cid, sid, sc in sorted(true_match_scores, key=lambda x: -x[2])[:5]:
    print(f"    {cid:<10} {sid:<22} rerank={sc:+.4f}")
print(f"  中位数: {sorted([s[2] for s in true_match_scores])[len(true_match_scores)//2]:+.4f}")
print()

print(f"误召回 (expected 非空但未命中): {len(false_positive_scores)} 个")
for cid, sid, sc in sorted(false_positive_scores, key=lambda x: x[2]):
    print(f"    {cid:<10} {sid:<22} rerank={sc:+.4f}")
print()

print(f"负样本 (expected=[] 但有召回): {len(negative_scores)} 个")
for cid, sid, sc in sorted(negative_scores, key=lambda x: x[2]):
    print(f"    {cid:<10} {sid:<22} rerank={sc:+.4f}")
print()

# 最优阈值分析
print("=" * 100)
print("  阈值优化分析")
print("=" * 100)
print()

true_scores = sorted([s[2] for s in true_match_scores])
neg_scores = sorted([s[2] for s in negative_scores] + [s[2] for s in false_positive_scores])

if true_scores and neg_scores:
    min_true = true_scores[0]
    max_neg = neg_scores[-1]
    print(f"真匹配最低 rerank_score: {min_true:+.4f}")
    print(f"非真匹配最高 rerank_score: {max_neg:+.4f}")
    print()

    # 测试不同阈值
    for threshold in [0.0001, 0.0005, 0.001, 0.005, 0.01, 0.02, 0.05, 0.1]:
        tp = sum(1 for s in true_scores if s >= threshold)
        fn = len(true_scores) - tp
        tn = sum(1 for s in neg_scores if s < threshold)
        fp = len(neg_scores) - tn
        recall = tp / len(true_scores) if true_scores else 0
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        print(f"  阈值={threshold:<6}  TP={tp:>2} FN={fn:>2}  "
              f"FP={fp:>2} TN={tn:>2}  "
              f"真匹配召回率={recall:.2%}  精确率={precision:.2%}")
