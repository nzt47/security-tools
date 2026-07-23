"""探查 golden_set 中 voice_interaction 正样本，用于 v6.1 规则设计"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
data = json.load(open(ROOT / "tests/eval/skill_retrieval_golden_set.json",
                      "r", encoding="utf-8"))

# 找出所有 voice_interaction 正样本
voice_cases = []
for c in data["test_cases"]:
    expected = c.get("expected_skill_ids") or []
    if "voice_interaction" in expected:
        voice_cases.append(c)

print(f"=== voice_interaction 正样本用例数: {len(voice_cases)} ===")
for c in voice_cases:
    print(f"  - {c.get('case_id')}: {c.get('query')}  (category={c.get('category')})")

print()
print("=== 与 negative_booking 语义接近的正样本（动词+宾语模式） ===")
booking_keywords = ["点", "订", "买", "叫", "购"]
for c in data["test_cases"]:
    q = c.get("query", "")
    if any(k in q for k in booking_keywords):
        expected = c.get("expected_skill_ids") or []
        print(f"  - {c.get('case_id')}: {q}  expected={expected}")

print()
print("=== negative_booking 类别负样本 ===")
neg_data = json.load(open(ROOT / "tests/eval/negative_samples_extended.json",
                          "r", encoding="utf-8"))
for c in neg_data["test_cases"]:
    if c.get("category") == "negative_booking":
        print(f"  - {c.get('case_id')}: {c.get('query')}")
