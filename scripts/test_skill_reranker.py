"""验证 SkillReranker 能正确加载 modelscope 缓存的 BGE-reranker-v2-m3 模型"""
import os
import sys
import time
from pathlib import Path

# 设置环境变量
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

# 把项目根目录加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

print("=" * 80)
print("  SkillReranker 加载测试")
print("=" * 80)
print()

from agent.skills_mgmt.reranker import SkillReranker, _candidate_local_paths

# 1. 验证本地缓存路径探测
print(">>> 1. 本地缓存路径探测...")
paths = _candidate_local_paths("BAAI/bge-reranker-v2-m3")
print(f"  发现 {len(paths)} 个本地缓存:")
for p in paths:
    print(f"    {p}")

# 2. 验证 SkillReranker 加载
print()
print(">>> 2. SkillReranker 加载测试...")
t0 = time.time()
reranker = SkillReranker()
model = reranker._ensure_model()
load_time = time.time() - t0

if model is None:
    print(f"  ❌ 加载失败，耗时 {load_time:.2f}s")
    print(f"  health: {reranker.health()}")
    sys.exit(1)

print(f"  ✅ 加载成功，耗时 {load_time:.2f}s")
print(f"  health: {reranker.health()}")

# 3. 验证 rerank 功能
print()
print(">>> 3. rerank() 功能测试...")
query = "请帮我反思刚才的回答"
candidates = [
    {
        "skill_id": "self_reflection",
        "name": "self_reflection",
        "score": 0.5,
        "metadata": {"description": "自我反思技能 — 让模型回顾自身推理与回答过程"},
    },
    {
        "skill_id": "memory_summary",
        "name": "memory_summary",
        "score": 0.45,
        "metadata": {"description": "记忆摘要技能 — 对长对话或历史记忆做结构化压缩"},
    },
    {
        "skill_id": "context_aware",
        "name": "context_aware",
        "score": 0.4,
        "metadata": {"description": "上下文感知技能 — 维护对话上下文与话题切换检测"},
    },
    {
        "skill_id": "emotion_expression",
        "name": "emotion_expression",
        "score": 0.35,
        "metadata": {"description": "情感表达技能 — 调整回应语气和感情色彩"},
    },
]

t0 = time.time()
reranked = reranker.rerank(query, candidates, top_k=4)
elapsed = (time.time() - t0) * 1000

print(f"  Query: {query}")
print(f"  推理耗时: {elapsed:.1f}ms")
print(f"  rerank 结果:")
for i, r in enumerate(reranked):
    print(f"    [{i+1}] {r['skill_id']:<20} "
          f"rerank_score={r.get('rerank_score', 0):+.4f} "
          f"orig_rank={r.get('original_rank')}")

# 4. 负样本测试
print()
print(">>> 4. 负样本测试...")
neg_query = "今天天气真好"
neg_reranked = reranker.rerank(neg_query, candidates, top_k=4)
print(f"  Query: {neg_query}")
print(f"  rerank 结果:")
for i, r in enumerate(neg_reranked):
    print(f"    [{i+1}] {r['skill_id']:<20} rerank_score={r.get('rerank_score', 0):+.4f}")

print()
print("=" * 80)
print("  ✅ SkillReranker 测试通过")
print("=" * 80)
