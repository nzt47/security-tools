"""快速测试 SkillVectorAdapter 的 native_chroma 模式"""
import os
import sys
import time

os.environ["ANONYMIZED_TELEMETRY"] = "False"
sys.path.insert(0, ".")

print(">>> import SkillVectorAdapter", flush=True)
from agent.skills_mgmt.vector_adapter import SkillVectorAdapter
from agent.skills_mgmt.file_store import SkillFileStore

print(">>> create adapter", flush=True)
fs = SkillFileStore()
adapter = SkillVectorAdapter(file_store=fs)

print(">>> ensure_indexed (首次构建索引)...", flush=True)
t0 = time.time()
n = adapter.ensure_indexed()
print(f">>> indexed {n} skills in {time.time()-t0:.2f}s", flush=True)
print(f">>> health: {adapter.health()}", flush=True)

queries = [
    "帮我反思刚才的回答",
    "请总结一下之前的对话历史",
    "这个操作安全吗，帮我过滤一下危险内容",
    "你能主动给我点建议吗",
    "帮我订一张机票",  # 负样本
]

for q in queries:
    print(f"\n>>> query: {q}", flush=True)
    results = adapter.search(q, top_k=3, enabled_only=True)
    if not results:
        print("  (空)", flush=True)
    for r in results:
        sid = r["skill_id"]
        score = r["score"]
        print(f"  {sid:<25} score={score:.4f}", flush=True)

print("\n>>> all done", flush=True)
