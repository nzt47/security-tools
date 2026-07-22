"""尝试加载 BGE-m3 模型，验证 Windows DLL 冲突是否仍存在

BGE-m3 (BAAI/bge-m3) 特性:
- 多语言（中文支持优秀）
- 1024 维向量
- 支持稠密/稀疏/多向量检索
- 模型大小约 2.27GB

【变易】如果加载失败，降级方案：
1. 使用 BGE-small-zh（中文专用，500MB）
2. 使用 text2vec-base-chinese
3. 保留 all-MiniLM-L6-v2 但增加 query 重写
"""
import os
import sys
import time

# 设置 HF 镜像（国内访问）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

print("=" * 80)
print("  BGE-m3 模型加载测试")
print("=" * 80)
print(f"Python: {sys.version}")
print(f"HF_ENDPOINT: {os.environ.get('HF_ENDPOINT')}")
print()

# 尝试加载 BGE-m3
print(">>> 尝试加载 BAAI/bge-m3 ...")
t0 = time.time()
try:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-m3", device="cpu")
    load_time = time.time() - t0
    print(f"  ✅ 加载成功，耗时 {load_time:.2f}s")
    print(f"  模型维度: {model.get_sentence_embedding_dimension()}")

    # 测试中文编码
    print()
    print(">>> 测试中文编码...")
    queries = ["请帮我反思刚才的回答", "帮我梳理历史记忆并压缩", "今天天气真好"]
    docs = [
        "自我反思技能 — 让模型回顾自身推理与回答过程",
        "记忆摘要技能 — 对长对话或历史记忆做结构化压缩",
        "天气预报查询",
    ]
    q_vec = model.encode(queries, normalize_embeddings=True)
    d_vec = model.encode(docs, normalize_embeddings=True)
    print(f"  query 向量 shape: {q_vec.shape}")
    print(f"  doc 向量 shape: {d_vec.shape}")
    print()
    # 计算相似度
    import numpy as np
    for i, q in enumerate(queries):
        sims = (d_vec @ q_vec[i].T)
        top = np.argsort(-sims)[:3]
        print(f"  Query[{i}]: {q}")
        for j in top:
            print(f"    → {docs[j][:30]:<30} sim={sims[j]:.4f}")
        print()
except Exception as e:
    load_time = time.time() - t0
    print(f"  ❌ 加载失败，耗时 {load_time:.2f}s")
    print(f"  错误类型: {type(e).__name__}")
    print(f"  错误信息: {str(e)[:500]}")
    import traceback
    print()
    print("Traceback:")
    traceback.print_exc()
    sys.exit(1)
