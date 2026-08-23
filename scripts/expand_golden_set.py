# -*- coding: utf-8 -*-
"""扩展黄金集：45 → 65 个 case，聚焦 discrimination/multi_skill/negative

【不易】不修改现有 case_001~045，仅追加 case_046~065
【变易】新增 20 个 case 覆盖四类难样本:
  - discrimination: 语义近义查询（考验 reranker 区分度）
  - multi_skill: 多技能组合查询
  - negative: 无关查询（考验拒绝能力）
  - hard: 单技能语义变体
【简易】直接操作 JSON 结构，保持 schema 一致
"""
import json
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PATH = r"C:\Users\Administrator\agent\tests\eval\skill_retrieval_golden_set.json"

with open(PATH, encoding="utf-8") as f:
    data = json.load(f)

existing = {c["case_id"] for c in data["test_cases"]}
assert len(existing) == 45, f"预期 45 个 case，实际 {len(existing)}"

NEW_CASES = [
    # ── discrimination: 语义近义，考验 reranker 区分度 ──
    {
        "case_id": "case_046",
        "query": "帮我回顾一下我之前的推理过程有没有错",
        "expected_skill_ids": ["self_reflection"],
        "expected_top_k": 1,
        "difficulty": "hard",
        "category": "discrimination",
        "notes": "语义近义：'回顾推理过程' 指向 self_reflection（reflection），与 memory_summary（'回顾记忆'）易混淆",
    },
    {
        "case_id": "case_047",
        "query": "把咱们之前的对话重点归纳成一段话",
        "expected_skill_ids": ["memory_summary"],
        "expected_top_k": 1,
        "difficulty": "hard",
        "category": "discrimination",
        "notes": "语义近义：'归纳对话' 指向 memory_summary（总结），与 self_reflection（'归纳'易误配）需区分",
    },
    {
        "case_id": "case_048",
        "query": "帮我看看我最近的想法是不是有偏见",
        "expected_skill_ids": ["self_reflection"],
        "expected_top_k": 1,
        "difficulty": "hard",
        "category": "discrimination",
        "notes": "语义近义：'想法有偏见' 指向 self_reflection（自省），safety_guard 的'过滤危险内容'是干扰项",
    },
    {
        "case_id": "case_049",
        "query": "总结一下这次对话里我提到的关键诉求",
        "expected_skill_ids": ["memory_summary"],
        "expected_top_k": 1,
        "difficulty": "hard",
        "category": "discrimination",
        "notes": "语义近义：'总结诉求' 指向 memory_summary，proactive_suggestion 的'识别潜在需求'是强干扰项",
    },
    # ── multi_skill: 多技能组合 ──
    {
        "case_id": "case_050",
        "query": "请帮我反思刚才的回答，并且主动给我一些改进建议",
        "expected_skill_ids": ["self_reflection", "proactive_suggestion"],
        "expected_top_k": 2,
        "difficulty": "hard",
        "category": "multi_skill",
        "notes": "组合：反思 + 主动建议",
    },
    {
        "case_id": "case_051",
        "query": "用有感情的语气说话，同时确保内容安全",
        "expected_skill_ids": ["emotion_expression", "safety_guard"],
        "expected_top_k": 2,
        "difficulty": "hard",
        "category": "multi_skill",
        "notes": "组合：情感表达 + 安全守护",
    },
    {
        "case_id": "case_052",
        "query": "请总结之前的对话，然后用语音读出来",
        "expected_skill_ids": ["memory_summary", "voice_interaction"],
        "expected_top_k": 2,
        "difficulty": "hard",
        "category": "multi_skill",
        "notes": "组合：记忆总结 + 语音交互",
    },
    {
        "case_id": "case_053",
        "query": "检测上下文话题变化，并主动建议下一步行动",
        "expected_skill_ids": ["context_aware", "proactive_suggestion"],
        "expected_top_k": 2,
        "difficulty": "hard",
        "category": "multi_skill",
        "notes": "组合：上下文感知 + 主动建议",
    },
    # ── negative: 无关查询（拒绝能力） ──
    {
        "case_id": "case_054",
        "query": "帮我算一下 2 的 10 次方是多少",
        "expected_skill_ids": [],
        "expected_top_k": 0,
        "difficulty": "tricky",
        "category": "negative",
        "notes": "负样本：数学计算，无对应技能",
    },
    {
        "case_id": "case_055",
        "query": "今天股市行情怎么样",
        "expected_skill_ids": [],
        "expected_top_k": 0,
        "difficulty": "tricky",
        "category": "negative",
        "notes": "负样本：金融资讯，无对应技能",
    },
    {
        "case_id": "case_056",
        "query": "帮我写一首关于秋天的诗",
        "expected_skill_ids": [],
        "expected_top_k": 0,
        "difficulty": "tricky",
        "category": "negative",
        "notes": "负样本：创作请求，无对应技能",
    },
    {
        "case_id": "case_057",
        "query": "推荐一部好看的科幻电影",
        "expected_skill_ids": [],
        "expected_top_k": 0,
        "difficulty": "tricky",
        "category": "negative",
        "notes": "负样本：娱乐推荐，无对应技能",
    },
    {
        "case_id": "case_058",
        "query": "这个 Python 报错怎么解决",
        "expected_skill_ids": [],
        "expected_top_k": 0,
        "difficulty": "tricky",
        "category": "negative",
        "notes": "负样本：编程问答，scripted-selftest（'脚本'）为干扰项需拒绝",
    },
    # ── hard: 单技能语义变体 ──
    {
        "case_id": "case_059",
        "query": "我回答得不够好，请你重新审视一遍",
        "expected_skill_ids": ["self_reflection"],
        "expected_top_k": 1,
        "difficulty": "hard",
        "category": "self_reflection",
        "notes": "语义变体：'重新审视回答' 指向 self_reflection",
    },
    {
        "case_id": "case_060",
        "query": "之前的聊天内容帮我提炼一下重点",
        "expected_skill_ids": ["memory_summary"],
        "expected_top_k": 1,
        "difficulty": "hard",
        "category": "memory_summary",
        "notes": "语义变体：'提炼聊天重点' 指向 memory_summary",
    },
    {
        "case_id": "case_061",
        "query": "你用词太生硬了，稍微热情一点",
        "expected_skill_ids": ["emotion_expression"],
        "expected_top_k": 1,
        "difficulty": "hard",
        "category": "emotion",
        "notes": "语义变体：'热情一点' 指向 emotion_expression",
    },
    {
        "case_id": "case_062",
        "query": "我还没想好下一步做什么，你能给点思路吗",
        "expected_skill_ids": ["proactive_suggestion"],
        "expected_top_k": 1,
        "difficulty": "hard",
        "category": "suggestion",
        "notes": "语义变体：'给点思路' 指向 proactive_suggestion",
    },
    {
        "case_id": "case_063",
        "query": "别忘了我之前提到过的偏好设置",
        "expected_skill_ids": ["context_aware"],
        "expected_top_k": 1,
        "difficulty": "hard",
        "category": "context",
        "notes": "语义变体：'记住偏好' 指向 context_aware",
    },
    {
        "case_id": "case_064",
        "query": "帮我把这段话里的敏感信息找出来",
        "expected_skill_ids": ["safety_guard"],
        "expected_top_k": 1,
        "difficulty": "hard",
        "category": "safety",
        "notes": "语义变体：'敏感信息检测' 指向 safety_guard",
    },
    {
        "case_id": "case_065",
        "query": "你能朗读这段文字给我听吗",
        "expected_skill_ids": ["voice_interaction"],
        "expected_top_k": 1,
        "difficulty": "hard",
        "category": "voice",
        "notes": "语义变体：'朗读' 指向 voice_interaction",
    },
]

for c in NEW_CASES:
    assert c["case_id"] not in existing, f"重复 case_id: {c['case_id']}"
    existing.add(c["case_id"])

data["test_cases"].extend(NEW_CASES)
data["description"] = data.get("description", "") + (
    " 2026-08-24 扩展：case_046~065 追加 20 个难样本"
    "（discrimination 4 / multi_skill 4 / negative 5 / hard 7），"
    "用于验证 reranker 区分度与拒绝能力。"
)
data["version"] = "2.0"

with open(PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

total = len(data["test_cases"])
from collections import Counter
diff = Counter(c.get("difficulty", "?") for c in data["test_cases"])
cat = Counter(c.get("category", "?") for c in data["test_cases"])
print(f"扩展完成: {total} 个 case (原 45 + 新增 20)")
print(f"难度分布: {dict(diff)}")
print(f"类别分布: {dict(cat)}")
