"""jina-reranker-v2 ONNX quantized 长稳压测（1000 次迭代 + 内存监控）

目的:
    验证 ONNX quantized 变体在长时间运行下内存稳定，无泄漏。
    1000 次迭代 × ~250ms/次 ≈ 250s，监控 RSS 随时间变化。

监控指标:
    - 延迟: P50/P95/P99/max（每 100 次输出快照）
    - 内存: RSS 起始/结束/峰值/增量（每 50 次采样）
    - 排序正确性: 每 200 次验证一次（语音 > PDF）
    - 内存泄漏判定: RSS 增量 > 50MB 视为泄漏

使用方法:
    python scripts/benchmark_v65_onnx_long_stability.py

输出:
    docs/v65_onnx_long_stability.json
"""
import os
import sys
import time
import json
import gc
import statistics
import traceback

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

MODEL_PATH = "C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual"
ONNX_PATH = os.path.join(MODEL_PATH, "onnx", "model_quantized.onnx")
OUTPUT_JSON = "c:/Users/Administrator/agent/docs/v65_onnx_long_stability.json"

ITERATIONS = 1000
SAMPLE_RSS_EVERY = 50          # 每 50 次采样 RSS
SNAPSHOT_LATENCY_EVERY = 100   # 每 100 次输出延迟快照
VERIFY_SORT_EVERY = 200        # 每 200 次验证排序正确性
RSS_LEAK_THRESHOLD_MB = 50     # RSS 增量超此值视为泄漏

SKILLS = [
    ("voice_interaction", "语音交互助手", "语音识别 语音转文字 语音合成"),
    ("self_reflection", "自我反思", "复盘 改进建议 自我评估"),
    ("pdf_parser", "PDF 解析器", "PDF 文件解析 文档提取"),
    ("memory_summary", "记忆摘要", "对话历史摘要 上下文压缩"),
    ("code_review", "代码审查", "代码质量审查 最佳实践"),
]


def make_candidates(n=20):
    return [(sid, name, desc) for i in range(n) for sid, name, desc in [SKILLS[i % len(SKILLS)]]]


def percentile(data, p):
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    if f == c:
        return sorted_data[f]
    return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)


def get_rss_mb():
    """获取当前进程 RSS（MB）"""
    try:
        import psutil
        return round(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024, 2)
    except Exception:
        return 0.0


def build_feed(input_names, encoded):
    import numpy as np
    feed = {}
    for name in input_names:
        if "input_ids" in name:
            feed[name] = encoded["input_ids"]
        elif "attention_mask" in name:
            feed[name] = encoded["attention_mask"]
        elif "token_type_ids" in name:
            feed[name] = encoded.get("token_type_ids", np.zeros_like(encoded["input_ids"]))
    return feed


def main():
    print("=" * 60)
    print("  jina-reranker-v2 ONNX quantized 长稳压测")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  迭代次数: {ITERATIONS}")
    print(f"  ONNX 模型: {ONNX_PATH}")
    print("=" * 60)

    import onnxruntime as ort
    from transformers import AutoTokenizer

    # ──────────────────────────────────────────────
    # 加载 tokenizer + ONNX session
    # ──────────────────────────────────────────────
    rss_before = get_rss_mb()
    print(f"\n压测前 RSS: {rss_before}MB")

    print("\n加载 tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
    print("✅ tokenizer 加载完成")

    print(f"\n加载 ONNX 模型: {os.path.basename(ONNX_PATH)}")
    t0 = time.time()
    sess = ort.InferenceSession(ONNX_PATH, providers=["CPUExecutionProvider"])
    load_time = time.time() - t0
    input_names = [i.name for i in sess.get_inputs()]
    rss_after_load = get_rss_mb()
    print(f"✅ ONNX 加载完成 ({load_time:.2f}s)")
    print(f"  输入: {input_names}")
    print(f"  加载后 RSS: {rss_after_load}MB (增量 +{rss_after_load - rss_before}MB)")

    # ──────────────────────────────────────────────
    # 准备测试数据
    # ──────────────────────────────────────────────
    candidates = make_candidates(20)
    query = "帮我识别语音并转成文字"
    texts_a = [query] * len(candidates)
    texts_b = [c[2] for c in candidates]
    encoded = tokenizer(
        texts_a, texts_b,
        padding=True, truncation=True, max_length=512,
        return_tensors="np",
    )
    feed = build_feed(input_names, encoded)

    # 排序正确性验证数据
    verify_a = ["语音识别", "语音识别"]
    verify_b = ["语音交互助手 语音识别", "PDF 文件解析器 文档提取"]
    verify_enc = tokenizer(
        verify_a, verify_b,
        padding=True, truncation=True, max_length=512,
        return_tensors="np",
    )
    verify_feed = build_feed(input_names, verify_enc)

    # ──────────────────────────────────────────────
    # 预热 5 次
    # ──────────────────────────────────────────────
    print("\n预热 5 次...")
    for _ in range(5):
        sess.run(None, feed)
    rss_after_warmup = get_rss_mb()
    print(f"  预热后 RSS: {rss_after_warmup}MB")

    # ──────────────────────────────────────────────
    # 正式压测 1000 次
    # ──────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print(f"正式压测: {ITERATIONS} 次迭代")
    print(f"{'─' * 60}")

    latencies = []
    rss_samples = []  # [(iteration, rss_mb)]
    latency_snapshots = []  # [{iteration, p50, p95, p99, max}]
    sort_verifications = []  # [{iteration, correct, scores}]

    rss_peak = rss_after_warmup
    t_start = time.time()

    for i in range(1, ITERATIONS + 1):
        t0 = time.time()
        sess.run(None, feed)
        elapsed_ms = (time.time() - t0) * 1000
        latencies.append(elapsed_ms)

        # RSS 采样
        if i % SAMPLE_RSS_EVERY == 0:
            rss = get_rss_mb()
            rss_samples.append({"iteration": i, "rss_mb": rss})
            if rss > rss_peak:
                rss_peak = rss
            print(f"  [{i:>4}/{ITERATIONS}] RSS={rss}MB (peak={rss_peak}MB)  last={elapsed_ms:.1f}ms")

        # 延迟快照
        if i % SNAPSHOT_LATENCY_EVERY == 0:
            recent = latencies[-SNAPSHOT_LATENCY_EVERY:]
            snap = {
                "iteration": i,
                "p50": round(percentile(recent, 50), 2),
                "p95": round(percentile(recent, 95), 2),
                "p99": round(percentile(recent, 99), 2),
                "max": round(max(recent), 2),
            }
            latency_snapshots.append(snap)
            print(f"  [{i:>4}/{ITERATIONS}] 快照 P50={snap['p50']}ms P99={snap['p99']}ms max={snap['max']}ms")

        # 排序正确性验证
        if i % VERIFY_SORT_EVERY == 0:
            scores = sess.run(None, verify_feed)[0].flatten()
            correct = bool(scores[0] > scores[1])
            sort_verifications.append({
                "iteration": i,
                "correct": correct,
                "scores": [round(float(s), 4) for s in scores],
            })
            print(f"  [{i:>4}/{ITERATIONS}] 排序验证: {'✅' if correct else '❌'} 分数={[round(float(s), 4) for s in scores]}")

    t_total = time.time() - t_start
    rss_final = get_rss_mb()
    rss_delta = rss_final - rss_after_warmup

    # ──────────────────────────────────────────────
    # 汇总
    # ──────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"  长稳压测结果汇总")
    print(f"{'=' * 60}")

    p50 = percentile(latencies, 50)
    p95 = percentile(latencies, 95)
    p99 = percentile(latencies, 99)
    p999 = percentile(latencies, 99.9)
    mean = statistics.mean(latencies)
    qps = ITERATIONS / t_total

    print(f"  总迭代: {ITERATIONS}")
    print(f"  总耗时: {t_total:.1f}s")
    print(f"  QPS:    {qps:.2f}")
    print(f"  延迟 P50:  {p50:.2f}ms")
    print(f"  延迟 P95:  {p95:.2f}ms")
    print(f"  延迟 P99:  {p99:.2f}ms")
    print(f"  延迟 P99.9: {p999:.2f}ms")
    print(f"  延迟 max:  {max(latencies):.2f}ms")
    print(f"  延迟 min:  {min(latencies):.2f}ms")
    print(f"")
    print(f"  内存 RSS 起始（预热后）: {rss_after_warmup}MB")
    print(f"  内存 RSS 结束: {rss_final}MB")
    print(f"  内存 RSS 峰值: {rss_peak}MB")
    print(f"  内存 RSS 增量: {rss_delta:+.2f}MB")
    print(f"  内存泄漏判定: {'❌ 疑似泄漏（增量 > ' + str(RSS_LEAK_THRESHOLD_MB) + 'MB）' if rss_delta > RSS_LEAK_THRESHOLD_MB else '✅ 稳定（增量 ≤ ' + str(RSS_LEAK_THRESHOLD_MB) + 'MB）'}")
    print(f"")
    print(f"  排序正确性: {sum(1 for v in sort_verifications if v['correct'])}/{len(sort_verifications)} 次 ✅")

    slo_passed = p99 <= 500
    memory_stable = rss_delta <= RSS_LEAK_THRESHOLD_MB
    sort_correct = all(v["correct"] for v in sort_verifications)

    print(f"\n  SLO (P99 ≤ 500ms): {'✅ 通过' if slo_passed else '❌ 未达标'}")
    print(f"  内存稳定: {'✅ 通过' if memory_stable else '❌ 疑似泄漏'}")
    print(f"  排序正确: {'✅ 通过' if sort_correct else '❌ 错误'}")
    print(f"  综合结论: {'✅ 全部通过，可投入生产' if (slo_passed and memory_stable and sort_correct) else '❌ 存在问题，需排查'}")

    # ──────────────────────────────────────────────
    # 写入 JSON
    # ──────────────────────────────────────────────
    output = {
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
        "model": MODEL_PATH,
        "onnx_file": os.path.basename(ONNX_PATH),
        "iterations": ITERATIONS,
        "total_time_s": round(t_total, 2),
        "latency": {
            "min_ms": round(min(latencies), 2),
            "mean_ms": round(mean, 2),
            "p50_ms": round(p50, 2),
            "p95_ms": round(p95, 2),
            "p99_ms": round(p99, 2),
            "p999_ms": round(p999, 2),
            "max_ms": round(max(latencies), 2),
        },
        "qps": round(qps, 2),
        "memory": {
            "rss_before_mb": rss_before,
            "rss_after_load_mb": rss_after_load,
            "rss_after_warmup_mb": rss_after_warmup,
            "rss_final_mb": rss_final,
            "rss_peak_mb": rss_peak,
            "rss_delta_mb": round(rss_delta, 2),
            "leak_threshold_mb": RSS_LEAK_THRESHOLD_MB,
            "stable": bool(memory_stable),
            "samples": rss_samples,
        },
        "latency_snapshots": latency_snapshots,
        "sort_verifications": sort_verifications,
        "load_time_s": round(load_time, 2),
        "slo_passed": bool(slo_passed),
        "memory_stable": bool(memory_stable),
        "sort_correct": bool(sort_correct),
        "all_passed": bool(slo_passed and memory_stable and sort_correct),
    }

    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {OUTPUT_JSON}")

    return 0 if (slo_passed and memory_stable and sort_correct) else 1


if __name__ == "__main__":
    sys.exit(main())
