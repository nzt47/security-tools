"""验证 BGE-m3 集成到 vector_adapter 后是否正常工作"""
import os
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

from agent.skills_mgmt.vector_adapter import SkillVectorAdapter
from agent.skills_mgmt.file_store import SkillFileStore

print("=" * 80)
print("  BGE-m3 集成验证")
print("=" * 80)

fs = SkillFileStore()
adapter = SkillVectorAdapter(file_store=fs)

print()
print(">>> 初始化 adapter（首次加载 BGE-m3 可能需要 ~10s）...")
t0 = time.time()
adapter.ensure_indexed()
elapsed = time.time() - t0
print(f"  索引构建耗时: {elapsed:.2f}s")
print(f"  已索引技能数: {len(adapter._indexed_skill_ids)}")

if adapter._st_backend is not None:
    model, doc_ids, doc_vectors, doc_metas = adapter._st_backend
    print(f"  Backend: sentence-transformers (BGE-m3)")
    print(f"  模型: {adapter.model_name}")
    print(f"  文档向量 shape: {doc_vectors.shape}")
    print(f"  doc_ids: {doc_ids}")
else:
    print(f"  ⚠ sentence-transformers 后端未启用，回退 native_chroma")
    print(f"  Backend: {adapter._native_chroma is not None}")

print()
print(">>> 测试查询（关键用例）...")
queries = [
    ("请帮我反思刚才的回答", "self_reflection"),
    ("帮我梳理历史记忆并压缩", "memory_summary"),
    ("请总结一下之前的对话历史", "memory_summary"),
    ("今天天气真好", None),  # 负样本
    ("帮我订一张机票", None),  # 负样本
    ("安全", "safety_guard"),
    ("语音", "voice_interaction"),
]

for q, expected in queries:
    results = adapter.search(q, top_k=3, enabled_only=True, min_score=0.0)
    print(f"  Query: {q}")
    print(f"    Expected: {expected}")
    if results:
        for r in results[:3]:
            print(f"    → {r['skill_id']:<25} score={r['score']:.4f}")
    else:
        print(f"    → (no results)")
    print()

print("=" * 80)
print("  BGE-m3 集成验证完成")
print("=" * 80)
