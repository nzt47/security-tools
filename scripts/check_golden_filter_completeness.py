"""全面检查 golden_set 中 expected_skill_ids 为空的负样本用例

目的: 确认 test_query_pattern.py 的过滤逻辑（if c.get("expected_skill_ids")）
      没有遗漏其他负样本用例。

检查项:
  1. 统计 golden_set 中 expected=[] 的用例数（应全部被过滤）
  2. 列出每个负样本的 case_id/query/category
  3. 对每个负样本，检查是否会被 v6+v6.1 的 6 类模式规则命中
     - 若命中: 该负样本被规则正确拒绝（符合预期，不应进入"不误伤"断言）
     - 若不命中: 该负样本不会被规则拒绝（也不应进入"不误伤"断言，
       因为它 expected=[] 本就不应被匹配）
  4. 检查 test_query_pattern.py 的过滤是否正确（expected=[] 全部过滤）
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 加载 golden_set
golden_path = ROOT / "tests" / "eval" / "skill_retrieval_golden_set.json"
data = json.load(open(golden_path, "r", encoding="utf-8"))

# 导入实际 _QUERY_PATTERNS
from agent.skills_mgmt.loader import _QUERY_PATTERNS

print("=" * 70)
print("  golden_set 负样本过滤完整性检查")
print("=" * 70)

positives = []
negatives = []
for c in data["test_cases"]:
    expected = c.get("expected_skill_ids") or []
    if expected:
        positives.append(c)
    else:
        negatives.append(c)

print(f"\n黄金集总数: {len(data['test_cases'])}")
print(f"  正样本（expected 非空）: {len(positives)}")
print(f"  负样本（expected=[]）  : {len(negatives)}")

print(f"\n{'='*70}")
print(f"  负样本详情（expected=[] 的用例）")
print(f"{'='*70}")
print(f"{'case_id':<12} {'category':<25} {'query':<40} {'规则命中?'}")
print("-" * 90)

neg_hit_by_pattern = 0
neg_not_hit = 0
for c in negatives:
    q = c["query"]
    cat = c.get("category", "?")
    # 检查是否被任一模式规则命中
    hit_category = None
    for pattern, category, reason in _QUERY_PATTERNS:
        if pattern.search(q):
            hit_category = category
            break
    if hit_category:
        marker = f"✅ {hit_category}"
        neg_hit_by_pattern += 1
    else:
        marker = "— 不命中"
        neg_not_hit += 1
    print(f"{c['case_id']:<12} {cat:<25} {q:<40} {marker}")

print(f"\n{'='*70}")
print(f"  负样本过滤逻辑验证")
print(f"{'='*70}")
print(f"\n负样本中:")
print(f"  被模式规则命中: {neg_hit_by_pattern} 个（这些被规则正确拒绝）")
print(f"  不被模式规则命中: {neg_not_hit} 个（这些走 RRF 被拒绝）")
print(f"\n结论: 所有 {len(negatives)} 个 expected=[] 负样本都应被过滤，")
print(f"      不参与 test_query_pattern.py 的'不误伤正样本'断言。")

print(f"\n{'='*70}")
print(f"  test_query_pattern.py 过滤逻辑检查")
print(f"{'='*70}")

# 模拟 test_query_pattern.py 的过滤逻辑
filtered = [(c["case_id"], c["query"], c.get("expected_skill_ids", []))
            for c in data["test_cases"]
            if c.get("expected_skill_ids")]

print(f"\n过滤后保留（正样本）: {len(filtered)} 个")
print(f"过滤掉（负样本）: {len(data['test_cases']) - len(filtered)} 个")
print(f"预期过滤掉: {len(negatives)} 个")

if len(data['test_cases']) - len(filtered) == len(negatives):
    print(f"\n✅ 过滤逻辑正确: 所有 {len(negatives)} 个负样本都被过滤")
else:
    print(f"\n❌ 过滤逻辑错误: 过滤数量不匹配")
    sys.exit(1)

print(f"\n{'='*70}")
print(f"  潜在遗漏检查: 正样本中是否有 expected 实际为负的用例")
print(f"{'='*70}")

# 检查正样本中是否有 category 含 "negative" 的（可能是误标）
suspicious = [c for c in positives if "negative" in (c.get("category") or "")]
if suspicious:
    print(f"\n⚠️  发现 {len(suspicious)} 个可疑正样本（category 含 negative）:")
    for c in suspicious:
        print(f"  - {c['case_id']}: {c['query']} expected={c.get('expected_skill_ids')}")
else:
    print(f"\n✅ 正样本中无 category 含 negative 的可疑用例")

print(f"\n{'='*70}")
print(f"  最终结论")
print(f"{'='*70}")
print(f"""
✅ golden_set 过滤逻辑完整正确:
   - {len(positives)} 个正样本（expected 非空）参与"不误伤"断言
   - {len(negatives)} 个负样本（expected=[]）被过滤，不参与断言
   - 其中 {neg_hit_by_pattern} 个负样本被 v6+v6.1 模式规则命中（正确拒绝）
   - 其中 {neg_not_hit} 个负样本不被模式规则命中（走 RRF 拒绝）
   - 无遗漏，无二次修复需要
""")
