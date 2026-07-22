"""测试 chromadb 在当前环境是否能正常工作"""
import os
import sys
import time

# 禁用遥测，避免网络阻塞
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_TELEMETRY_IMPL"] = "none"
os.environ["CHROMA_OTEL_COLLECTION"] = "false"

print(">>> Step 1: import chromadb", flush=True)
t0 = time.time()
import chromadb
print(f"  import took {time.time()-t0:.2f}s, version: {chromadb.__version__}", flush=True)

print(">>> Step 2: create PersistentClient", flush=True)
t0 = time.time()
client = chromadb.PersistentClient(path="./data/test_chroma_db3")
print(f"  client created in {time.time()-t0:.2f}s", flush=True)

print(">>> Step 3: create collection", flush=True)
t0 = time.time()
col = client.get_or_create_collection("test_skill")
print(f"  collection '{col.name}' created in {time.time()-t0:.2f}s, count={col.count()}", flush=True)

print(">>> Step 4: add items", flush=True)
t0 = time.time()
col.add(
    ids=["s1", "s2"],
    documents=["自我反思技能", "记忆摘要技能"],
    metadatas=[{"skill_id": "self_reflection"}, {"skill_id": "memory_summary"}],
)
print(f"  added 2 items in {time.time()-t0:.2f}s, count={col.count()}", flush=True)

print(">>> Step 5: query", flush=True)
t0 = time.time()
results = col.query(query_texts=["帮我反思刚才的回答"], n_results=2)
print(f"  query took {time.time()-t0:.2f}s", flush=True)
print(f"    ids: {results['ids']}", flush=True)
print(f"    distances: {results['distances']}", flush=True)

print(">>> All steps done!", flush=True)
