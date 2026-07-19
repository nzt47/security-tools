"""诊断负样本与 hard 用例在向量路的分数分布"""
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.skills_mgmt.loader import SkillLoader  # noqa: E402

# 关注的 case_id 列表
TARGET_CASES = {
    "case_007",   # hard, 改善（memory_summary）
    "case_038",   # tricky/negative, 退化
    "case_042",   # tricky/negative, 退化
    "case_043",   # hard/discrimination, 改善
    "case_002",   # hard, 平移
    "case_003",   # hard, 平移
}

golden_path = _PROJECT_ROOT / "tests" / "eval" / "skill_retrieval_golden_set.json"
with golden_path.open("r", encoding="utf-8") as f:
    golden = json.load(f)

loader = SkillLoader()
adapter = loader._get_vector_adapter()

print("=" * 90)
print("  向量路分数分布诊断（TF-IDF 路过滤后为空的 case）")
print("=" * 90)
print()

for case in golden["test_cases"]:
    if case["case_id"] not in TARGET_CASES:
        continue
    query = case["query"]
    expected = case.get("expected_skill_ids", [])
    print(f"  {case['case_id']:<10} [{case['difficulty']:<7}/{case['category']:<14}]")
    print(f"    query   : {query}")
    print(f"    expected: {expected}")

    # TF-IDF 路
    from agent.skills_mgmt.loader import _tokenize, _meta_to_meta_text, _match_score
    index = loader.fs.load_metadata_index()
    query_tokens = _tokenize(query)
    tfidf_scores = []
    for skill_id, meta in index.items():
        if not meta.get("enabled", True):
            continue
        meta_text = _meta_to_meta_text(meta)
        score = _match_score(meta_text, query_tokens)
        if score >= 0.01:
            tfidf_scores.append((skill_id, score))
    tfidf_scores.sort(key=lambda x: x[1], reverse=True)
    print(f"    TF-IDF 路 (min_score=0.01 过滤后): "
          f"{[(s, round(sc, 3)) for s, sc in tfidf_scores[:5]]}")

    # 向量路
    if adapter is not None:
        try:
            vec_results = adapter.search(query, top_k=5, enabled_only=True, min_score=0.0)
            vec_scores = [(r["skill_id"], round(r["score"], 4)) for r in vec_results[:5]]
            print(f"    向量路 (min_score=0.0 不过滤): {vec_scores}")
        except Exception as e:
            print(f"    向量路异常: {e}")
    print()
