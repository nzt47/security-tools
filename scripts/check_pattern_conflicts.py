"""临时脚本：检查正样本黄金集是否匹配 query 模式规则"""
import json
import re
import sys

# 加载黄金集
with open('tests/eval/skill_retrieval_golden_set.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

queries = [(c['case_id'], c['query'], c.get('expected_skill_ids', []))
           for c in data['test_cases']]

# 规划中的 5 类模式规则
QUERY_PATTERNS = [
    (re.compile(r"(.+?)\s*是什么(意思|含义|东西)"), "keyword_trap"),
    (re.compile(r"(.+?)\s*概念解释"), "keyword_trap"),
    (re.compile(r"(.+?)\s*的(定义|含义)"), "keyword_trap"),
    (re.compile(r"(帮我|请).{0,4}翻译"), "translation"),
    (re.compile(r"(帮我|请).{0,2}写.{0,2}(诗|歌|故事|小说|文章|散文)"), "creative"),
    (re.compile(r"(帮我|请).{0,2}算"), "math"),
    (re.compile(r"[\d]+\s*[+\-*/]\s*[\d]+"), "math"),
    (re.compile(r"(删除|移动|复制|重命名)\s*(文件|目录|文件夹)"), "similar"),
    (re.compile(r"(重启|关闭|启动)\s*(服务器|服务|进程|系统)"), "similar"),
]

print("=" * 70)
print("正样本黄金集模式冲突检查")
print("=" * 70)
print(f"总用例数: {len(queries)}")
print()

conflicts = []
for case_id, query, expected in queries:
    for pattern, category in QUERY_PATTERNS:
        if pattern.search(query):
            conflicts.append((case_id, query, category, expected))
            break

if conflicts:
    print("⚠ 发现冲突（正样本会被模式规则误伤）:")
    for case_id, query, category, expected in conflicts:
        print(f"  {case_id} [{category}]: {query}")
        print(f"    expected: {expected}")
    print(f"\n冲突总数: {len(conflicts)}")
else:
    print("✅ 无冲突：所有 45 个正样本 query 都不匹配任何模式规则")
    print("可以安全实施 query 模式识别优化")

print()
print("=" * 70)
print("所有 45 个正样本 query 完整列表（供单元测试参考）")
print("=" * 70)
for case_id, query, expected in queries:
    print(f"  {case_id}: {query}  -> {expected}")
