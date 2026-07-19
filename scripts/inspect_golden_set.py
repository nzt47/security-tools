"""快速分析黄金集"""
import json
from pathlib import Path

p = Path("tests/eval/skill_retrieval_golden_set.json")
g = json.loads(p.read_text(encoding="utf-8"))
cases = g["test_cases"]

print(f"黄金集路径: {p}")
print(f"总用例数: {len(cases)}")

# 负样本
neg = [c for c in cases if not c.get("expected_skill_ids")]
print(f"\n负样本用例数: {len(neg)}")
for c in neg:
    print(f"  {c['case_id']:<10} query={c['query']}")

# 按难度统计
from collections import Counter
diff_cnt = Counter(c.get("difficulty", "?") for c in cases)
print(f"\n按难度: {dict(diff_cnt)}")
cat_cnt = Counter(c.get("category", "?") for c in cases)
print(f"按类别: {dict(cat_cnt)}")

# 看前 3 个用例结构
print("\n前 3 个用例结构:")
for c in cases[:3]:
    print(f"  {json.dumps(c, ensure_ascii=False, indent=2)}")
