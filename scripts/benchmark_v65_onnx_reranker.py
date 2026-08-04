"""jina-reranker-v2 ONNX 预导出格式压测

目的:
    jina 模型已预导出 7 种 ONNX 格式（int8/uint8/quantized/fp16/q4/bnb4/fp32），
    无需自己转换，直接压测选最优变体验证是否满足 500ms SLO。

测试矩阵:
    - 20 个候选技能（与 v65 既有压测一致）
    - 单次延迟：20 次迭代 P50/P99
    - 排序正确性：语音查询 > PDF 查询

使用方法:
    python scripts/benchmark_v65_onnx_reranker.py

输出:
    docs/v65_onnx_benchmark.json
"""
import os
import sys
import time
import json
import gc
import statistics
import traceback

# 行缓冲，后台运行可见进度
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

MODEL_PATH = "C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual"
ONNX_DIR = os.path.join(MODEL_PATH, "onnx")
OUTPUT_JSON = "c:/Users/Administrator/agent/docs/v65_onnx_benchmark.json"

# 按大小升序排列，小的优先试（量化版本通常更快）
ONNX_VARIANTS = [
    ("int8", "model_int8.onnx"),
    ("uint8", "model_uint8.onnx"),
    ("quantized", "model_quantized.onnx"),
    ("fp16", "model_fp16.onnx"),
    ("q4", "model_q4.onnx"),
    ("bnb4", "model_bnb4.onnx"),
    ("fp32", "model.onnx"),
]

# 候选技能（与既有压测一致）
SKILLS = [
    ("voice_interaction", "语音交互助手", "语音识别 语音转文字 语音合成"),
    ("self_reflection", "自我反思", "复盘 改进建议 自我评估"),
    ("pdf_parser", "PDF 解析器", "PDF 文件解析 文档提取"),
    ("memory_summary", "记忆摘要", "对话历史摘要 上下文压缩"),
    ("code_review", "代码审查", "代码质量审查 最佳实践"),
]


def make_candidates(n=20):
    """生成 n 个候选技能 (sid, name, desc)"""
    candidates = []
    for i in range(n):
        sid, name, desc = SKILLS[i % len(SKILLS)]
        candidates.append((sid, name, desc))
    return candidates


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


def build_feed(input_names, encoded):
    """按 ONNX 模型实际输入名构造 feed_dict，兼容不同变体"""
    feed = {}
    for name in input_names:
        if "input_ids" in name:
            feed[name] = encoded["input_ids"]
        elif "attention_mask" in name:
            feed[name] = encoded["attention_mask"]
        elif "token_type_ids" in name:
            # XLM-Roberta 通常无 token_type_ids，缺省填 0
            feed[name] = encoded.get(
                "token_type_ids",
                __import__("numpy").zeros_like(encoded["input_ids"]),
            )
    return feed


def benchmark_variant(variant_name, onnx_path, tokenizer, iterations=20):
    """压测单个 ONNX 变体"""
    print(f"\n{'─' * 60}")
    print(f"压测: {variant_name} ({os.path.basename(onnx_path)})")
    print(f"{'─' * 60}")

    result = {
        "variant": variant_name,
        "file": os.path.basename(onnx_path),
        "size_mb": round(os.path.getsize(onnx_path) / 1024 / 1024, 2),
    }

    try:
        import onnxruntime as ort
        import numpy as np  # noqa: F401

        # 加载 session
        t0 = time.time()
        try:
            sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
        except Exception as e:
            print(f"  ❌ 加载失败: {type(e).__name__}: {str(e)[:200]}")
            result["status"] = "load_failed"
            result["error"] = str(e)[:300]
            return result

        load_time = time.time() - t0
        result["load_time_s"] = round(load_time, 2)

        inputs = sess.get_inputs()
        input_names = [i.name for i in inputs]
        print(f"  加载耗时: {load_time:.2f}s")
        print(f"  输入: {input_names}")

        # 测试数据：20 候选 × 1 query
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

        # 预热 3 次
        for _ in range(3):
            try:
                sess.run(None, feed)
            except Exception as e:
                print(f"  ❌ 推理失败（预热）: {type(e).__name__}: {str(e)[:200]}")
                result["status"] = "inference_failed"
                result["error"] = str(e)[:300]
                return result

        # 正式压测
        latencies = []
        for _ in range(iterations):
            t0 = time.time()
            sess.run(None, feed)
            latencies.append((time.time() - t0) * 1000)

        p50 = percentile(latencies, 50)
        p99 = percentile(latencies, 99)
        mean = statistics.mean(latencies)
        qps = 1000 / mean if mean > 0 else 0

        result.update({
            "iterations": iterations,
            "min_ms": round(min(latencies), 2),
            "mean_ms": round(mean, 2),
            "p50_ms": round(p50, 2),
            "p95_ms": round(percentile(latencies, 95), 2),
            "p99_ms": round(p99, 2),
            "max_ms": round(max(latencies), 2),
            "qps": round(qps, 2),
            "target_p99_ms": 500,
            "passed": bool(p99 <= 500),
            "status": "ok",
        })

        print(f"  P50:  {p50:.2f}ms")
        print(f"  P99:  {p99:.2f}ms")
        print(f"  QPS:  {qps:.2f}")
        print(f"  目标 P99 ≤ 500ms: {'✅ 通过' if p99 <= 500 else '❌ 未达标'}")

        # 排序正确性验证：语音查询分数应高于 PDF 查询
        test_a = ["语音识别", "语音识别"]
        test_b = ["语音交互助手 语音识别", "PDF 文件解析器 文档提取"]
        test_enc = tokenizer(
            test_a, test_b,
            padding=True, truncation=True, max_length=512,
            return_tensors="np",
        )
        test_feed = build_feed(input_names, test_enc)
        scores = sess.run(None, test_feed)[0].flatten()
        ranking_correct = bool(scores[0] > scores[1])
        result["sort_correct"] = ranking_correct
        result["test_scores"] = [round(float(s), 4) for s in scores]
        print(f"  排序正确性: 语音 > PDF = {ranking_correct} {'✅' if ranking_correct else '❌'}")
        print(f"    分数: {[round(float(s), 4) for s in scores]}")

        del sess
        return result

    except Exception as e:
        print(f"  ❌ 异常: {type(e).__name__}: {str(e)[:200]}")
        traceback.print_exc()
        result["status"] = "error"
        result["error"] = str(e)[:300]
        return result


def main():
    print("=" * 60)
    print("  jina-reranker-v2 ONNX 预导出格式压测")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模型: {MODEL_PATH}")
    print("=" * 60)

    # 检查 onnxruntime
    try:
        import onnxruntime as ort
        print(f"onnxruntime 版本: {ort.__version__}")
        print(f"可用 providers: {ort.get_available_providers()}")
    except ImportError:
        print("❌ onnxruntime 未安装，正在安装...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "onnxruntime"])
        import onnxruntime as ort

    # 加载 tokenizer
    from transformers import AutoTokenizer
    print(f"\n加载 tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
    print(f"✅ tokenizer 加载完成")

    # 压测所有变体
    results = []
    for variant_name, filename in ONNX_VARIANTS:
        onnx_path = os.path.join(ONNX_DIR, filename)
        if not os.path.exists(onnx_path):
            print(f"\n⚠️ 跳过 {variant_name}: 文件不存在")
            continue

        result = benchmark_variant(variant_name, onnx_path, tokenizer, iterations=20)
        results.append(result)
        gc.collect()

    # 汇总表格
    print("\n" + "=" * 60)
    print("  压测结果汇总")
    print("=" * 60)
    print(f"{'变体':<12} {'大小MB':<10} {'P50ms':<10} {'P99ms':<10} {'QPS':<8} {'排序':<6} {'SLO':<6}")
    print("-" * 70)

    for r in results:
        if r.get("status") == "ok":
            sort_ok = "✅" if r.get("sort_correct") else "❌"
            slo_ok = "✅" if r.get("passed") else "❌"
            print(f"{r['variant']:<12} {r['size_mb']:<10} {r['p50_ms']:<10} {r['p99_ms']:<10} {r['qps']:<8} {sort_ok:<6} {slo_ok:<6}")
        else:
            print(f"{r['variant']:<12} {r.get('size_mb', '-'):<10} {'-':<10} {'-':<10} {'-':<8} {'-':<6} ❌")

    # 选最优：先按 SLO 达标过滤，再按 P99 升序
    passed = [r for r in results if r.get("status") == "ok" and r.get("passed") and r.get("sort_correct")]
    if passed:
        best = min(passed, key=lambda r: r["p99_ms"])
        print(f"\n✅ 最优变体: {best['variant']} (P99 {best['p99_ms']}ms, 大小 {best['size_mb']}MB)")
    else:
        ok = [r for r in results if r.get("status") == "ok" and r.get("sort_correct")]
        if ok:
            best = min(ok, key=lambda r: r["p99_ms"])
            print(f"\n⚠️ 无变体达 500ms SLO，最快: {best['variant']} (P99 {best['p99_ms']}ms)")
        else:
            print(f"\n❌ 无可用变体（全部加载/推理失败或排序错误）")

    # 写入 JSON
    output = {
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
        "model": MODEL_PATH,
        "results": results,
        "all_passed": any(r.get("passed") for r in results if r.get("status") == "ok"),
    }

    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {OUTPUT_JSON}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
