"""测试 Cross-Encoder Reranker 模型加载与中文判别能力

Cross-Encoder vs Bi-Encoder:
- Bi-Encoder（BGE-m3）：query/doc 独立编码 → 点积相似度，快但精度一般
- Cross-Encoder：query+doc 拼接 → 单一相关性分数，准但慢

候选模型:
1. BAAI/bge-reranker-v2-m3 — 多语言，中文友好，1.1GB
2. BAAI/bge-reranker-base — 中文专用
3. cross-encoder/ms-marco-MiniLM-L-6-v2 — 英文，小但快
"""
import os
import sys
import time

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

print("=" * 80)
print("  Cross-Encoder Reranker 加载测试")
print("=" * 80)
print()

# 尝试加载 BGE-reranker-v2-m3（与 BGE-m3 配套）
print(">>> 尝试加载 BAAI/bge-reranker-v2-m3 ...")
t0 = time.time()
try:
    from sentence_transformers import CrossEncoder
    # max_length=512 避免长文档截断
    reranker = CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=512)
    load_time = time.time() - t0
    print(f"  ✅ 加载成功，耗时 {load_time:.2f}s")

    # 测试中文 query 与技能文档的相关性
    print()
    print(">>> 测试中文 query 与技能文档相关性...")
    query = "请帮我反思刚才的回答"
    docs = [
        "自我反思技能 — 让模型回顾自身推理与回答过程，识别可能的疏漏并改进",
        "记忆摘要技能 — 对长对话或历史记忆做结构化压缩",
        "上下文感知技能 — 维护对话上下文与话题切换检测",
        "情感表达技能 — 调整回应语气和感情色彩",
    ]
    # Cross-Encoder 输入是 (query, doc) pairs，输出相关性分数
    pairs = [(query, doc) for doc in docs]
    scores = reranker.predict(pairs)

    print(f"  Query: {query}")
    for doc, score in sorted(zip(docs, scores), key=lambda x: -x[1]):
        print(f"    score={score:+.4f}  {doc[:40]}")
    print()

    # 测试负样本
    print(">>> 测试负样本判别能力...")
    negative_queries = ["今天天气真好", "帮我订一张机票", "12345"]
    for neg_q in negative_queries:
        pairs = [(neg_q, doc) for doc in docs]
        scores = reranker.predict(pairs)
        max_score = max(scores)
        print(f"  Query: {neg_q}")
        print(f"    max score: {max_score:+.4f}  (应 < 真匹配分数)")
        print()

except Exception as e:
    load_time = time.time() - t0
    print(f"  ❌ 加载失败，耗时 {load_time:.2f}s")
    print(f"  错误类型: {type(e).__name__}")
    print(f"  错误信息: {str(e)[:500]}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
