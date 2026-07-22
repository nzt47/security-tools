"""验证 bge-reranker-base 模型加载与中文判别能力

对比 bge-reranker-v2-m3:
- 模型大小: 1.1GB vs 2.2GB（减半）
- 加载速度: 预期更快
- 中文判别力: base 专为中文优化，v2-m3 多语言
- 内存占用: 预期减半
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.skills_mgmt.reranker import SkillReranker, _candidate_local_paths

MODELS_TO_TEST = [
    {
        "name": "BAAI/bge-reranker-base",
        "label": "bge-reranker-base (1.1GB, 中文专用)",
        "cache_key": "bge-reranker-base",
    },
    {
        "name": "BAAI/bge-reranker-v2-m3",
        "label": "bge-reranker-v2-m3 (2.2GB, 多语言)",
        "cache_key": "bge-reranker-v2-m3",
    },
]


def test_model(model_info: dict) -> dict:
    """测试单个模型的加载与推理"""
    print()
    print("=" * 80)
    print(f"  测试: {model_info['label']}")
    print("=" * 80)

    model_name = model_info["name"]

    # 1. 本地缓存检查
    print(f"\n>>> 1. 本地缓存检查...")
    paths = _candidate_local_paths(model_name)
    print(f"  发现 {len(paths)} 个本地缓存:")
    for p in paths:
        print(f"    {p}")
    if not paths:
        print("  ❌ 无本地缓存，跳过")
        return {"model": model_name, "load_ok": False, "reason": "no_cache"}

    # 2. 模型加载
    print(f"\n>>> 2. 模型加载...")
    import psutil
    proc = psutil.Process()
    mem_before = proc.memory_info().rss / 1024 / 1024
    t0 = time.time()
    reranker = SkillReranker(model_name=model_name)
    model = reranker._ensure_model()
    load_time = time.time() - t0
    mem_after = proc.memory_info().rss / 1024 / 1024
    mem_delta = mem_after - mem_before

    if model is None:
        print(f"  ❌ 加载失败")
        return {
            "model": model_name, "load_ok": False,
            "load_time": load_time, "mem_delta_mb": mem_delta,
        }

    print(f"  ✅ 加载成功，耗时 {load_time:.2f}s")
    print(f"  内存增量: {mem_delta:.1f} MB (before={mem_before:.1f}MB, after={mem_after:.1f}MB)")

    # 3. 推理测试（真匹配）
    print(f"\n>>> 3. 真匹配推理...")
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
    infer_time = (time.time() - t0) * 1000

    print(f"  Query: {query}")
    print(f"  推理耗时: {infer_time:.1f}ms")
    print(f"  rerank 结果:")
    true_match_score = 0
    for i, r in enumerate(reranked):
        if r["skill_id"] == "self_reflection":
            true_match_score = r.get("rerank_score", 0)
        print(f"    [{i+1}] {r['skill_id']:<20} "
              f"rerank_score={r.get('rerank_score', 0):+.4f} "
              f"orig_rank={r.get('original_rank')}")

    # 4. 负样本推理
    print(f"\n>>> 4. 负样本推理...")
    neg_query = "帮我订一张机票"
    t0 = time.time()
    neg_reranked = reranker.rerank(neg_query, candidates, top_k=4)
    neg_infer_time = (time.time() - t0) * 1000

    print(f"  Query: {neg_query}")
    print(f"  推理耗时: {neg_infer_time:.1f}ms")
    neg_max_score = max(
        (r.get("rerank_score", 0) for r in neg_reranked), default=0,
    )
    print(f"  最高 rerank_score: {neg_max_score:+.4f}")
    print(f"  rerank 结果:")
    for i, r in enumerate(neg_reranked):
        print(f"    [{i+1}] {r['skill_id']:<20} rerank_score={r.get('rerank_score', 0):+.4f}")

    # 判别力指标
    discrimination = true_match_score - neg_max_score
    print(f"\n  判别力: 真匹配({true_match_score:+.4f}) - 负样本最高({neg_max_score:+.4f}) = {discrimination:+.4f}")

    # 释放模型
    del reranker
    del model

    return {
        "model": model_name,
        "load_ok": True,
        "load_time_sec": load_time,
        "mem_delta_mb": mem_delta,
        "infer_time_ms": infer_time,
        "neg_infer_time_ms": neg_infer_time,
        "true_match_score": true_match_score,
        "neg_max_score": neg_max_score,
        "discrimination": discrimination,
    }


def main():
    print("=" * 80)
    print("  bge-reranker-base vs bge-reranker-v2-m3 对比测试")
    print("=" * 80)

    results = []
    for model_info in MODELS_TO_TEST:
        try:
            r = test_model(model_info)
            results.append(r)
        except Exception as e:
            print(f"\n  ❌ 测试异常: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            results.append({"model": model_info["name"], "load_ok": False, "error": str(e)})

    # 汇总对比
    print()
    print("=" * 100)
    print("  对比汇总")
    print("=" * 100)
    print()
    print(f"  {'指标':<20} {'base (1.1GB)':>18} {'v2-m3 (2.2GB)':>18} {'差异':>14}")
    print("  " + "-" * 80)
    metrics = [
        ("加载耗时(s)", "load_time_sec", "{:.2f}"),
        ("内存增量(MB)", "mem_delta_mb", "{:.1f}"),
        ("推理耗时(ms)", "infer_time_ms", "{:.1f}"),
        ("负样本推理(ms)", "neg_infer_time_ms", "{:.1f}"),
        ("真匹配分数", "true_match_score", "{:.4f}"),
        ("负样本最高分", "neg_max_score", "{:.4f}"),
        ("判别力", "discrimination", "{:.4f}"),
    ]
    base_r = results[0] if results[0].get("load_ok") else None
    v2_r = results[1] if len(results) > 1 and results[1].get("load_ok") else None

    for label, key, fmt in metrics:
        b = base_r.get(key, 0) if base_r else 0
        v = v2_r.get(key, 0) if v2_r else 0
        delta = b - v if isinstance(b, (int, float)) and isinstance(v, (int, float)) else 0
        # 判别力/分数用带符号格式，其他用普通格式
        if key in ("true_match_score", "neg_max_score", "discrimination"):
            b_str = ("+" + fmt.format(b)) if b >= 0 else fmt.format(b)
            v_str = ("+" + fmt.format(v)) if v >= 0 else fmt.format(v)
            d_str = ("+" + fmt.format(delta)) if delta >= 0 else fmt.format(delta)
        else:
            b_str = fmt.format(b)
            v_str = fmt.format(v)
            d_str = ("+" + fmt.format(delta)) if delta >= 0 else fmt.format(delta)
        print(f"  {label:<20} {b_str:>18} {v_str:>18} {d_str:>14}")

    # 结论
    print()
    print("=" * 100)
    if base_r and v2_r:
        mem_ratio = base_r["mem_delta_mb"] / v2_r["mem_delta_mb"] if v2_r["mem_delta_mb"] > 0 else 0
        speed_ratio = base_r["load_time_sec"] / v2_r["load_time_sec"] if v2_r["load_time_sec"] > 0 else 0
        disc_b = base_r["discrimination"]
        disc_v = v2_r["discrimination"]
        print(f"  内存优化: base 是 v2-m3 的 {mem_ratio:.1%} (节省 {(1-mem_ratio)*100:.0f}%)")
        print(f"  加载速度: base 是 v2-m3 的 {1/speed_ratio:.2f}x ({'更快' if speed_ratio < 1 else '更慢'})")
        print(f"  判别力: base={disc_b:+.4f} vs v2-m3={disc_v:+.4f} "
              f"({'base 更优' if disc_b > disc_v else 'v2-m3 更优'})")
    print("=" * 100)


if __name__ == "__main__":
    main()
