"""GPU Reranker 压测（验证 GPU 推理 500ms SLO）

本机环境:
    - GPU: NVIDIA GeForce GTX 1650（4GB 显存）
    - CUDA: 13.2（驱动）
    - torch: 需安装 GPU 版本（当前 2.13.0+cpu 不支持 CUDA）

若 torch GPU 不可用，本脚本会检测并提示安装步骤。
若 GPU 可用，对比 CPU vs GPU 推理性能。

执行:
    python scripts/benchmark_v65_gpu_reranker.py
"""
import os
import sys
import time
import json
import statistics

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

MODEL_PATH = "C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual"

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


def _percentile(data, p):
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    if f == c:
        return sorted_data[f]
    return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)


def _fmt(ms):
    if ms < 1:
        return f"{ms*1000:.1f}μs"
    if ms < 1000:
        return f"{ms:.2f}ms"
    return f"{ms/1000:.2f}s"


def _make_candidate_texts(n=20):
    skills = [
        "语音交互助手 语音识别 语音转文字 语音合成",
        "自我反思 复盘 改进建议 自我评估",
        "PDF 解析器 PDF 文件解析 文档提取",
        "记忆摘要 对话历史摘要 上下文压缩",
        "代码审查 代码质量审查 最佳实践",
        "数据分析 数据统计分析 可视化",
        "网络搜索 联网检索 信息查询",
        "任务规划 任务分解 执行计划",
    ]
    pairs = []
    query = "帮我识别语音并转成文字"
    for i in range(n):
        pairs.append((query, skills[i % len(skills)]))
    return pairs


def check_gpu_available() -> bool:
    """检查 GPU 是否可用"""
    try:
        import torch
        if torch.cuda.is_available():
            print(f"  ✅ GPU 可用")
            print(f"  GPU 名称: {torch.cuda.get_device_name(0)}")
            print(f"  CUDA 版本: {torch.version.cuda}")
            print(f"  显存: {torch.cuda.get_device_properties(0).total_mem / 1024**3:.1f}GB")
            return True
        else:
            print(f"  ❌ GPU 不可用")
            print(f"  torch 版本: {torch.__version__}")
            print(f"  当前 torch 不支持 CUDA，需安装 GPU 版本")
            return False
    except Exception as e:
        print(f"  ❌ GPU 检测失败: {e}")
        return False


def benchmark_gpu_reranker(iterations=20) -> dict:
    """GPU 推理压测"""
    print(f"\n{'─'*60}")
    print(f"测试: jina-reranker-v2 GPU 推理（{iterations} 次）")
    print(f"{'─'*60}")

    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"  设备: {device}")

    print(f"  加载 tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)

    print(f"  加载模型到 {device}...")
    t0 = time.time()
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_PATH, trust_remote_code=True
    ).to(device)
    model.eval()
    load_time = time.time() - t0
    print(f"  加载耗时: {load_time:.2f}s")

    if device == "cuda":
        gpu_mem = torch.cuda.memory_allocated() / 1024**3
        print(f"  GPU 显存占用: {gpu_mem:.2f}GB")

    pairs = _make_candidate_texts(20)
    texts_a = [p[0] for p in pairs]
    texts_b = [p[1] for p in pairs]

    # 预热 3 次
    for _ in range(3):
        encoded = tokenizer(texts_a, texts_b, padding=True, truncation=True, max_length=512, return_tensors="pt").to(device)
        with torch.no_grad():
            model(**encoded)
    if device == "cuda":
        torch.cuda.synchronize()

    latencies = []
    for i in range(iterations):
        encoded = tokenizer(texts_a, texts_b, padding=True, truncation=True, max_length=512, return_tensors="pt").to(device)
        if device == "cuda":
            torch.cuda.synchronize()
        t0 = time.time()
        with torch.no_grad():
            outputs = model(**encoded)
        if device == "cuda":
            torch.cuda.synchronize()
        latencies.append((time.time() - t0) * 1000)

    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    mean = statistics.mean(latencies)
    qps = 1000 / mean if mean > 0 else 0

    print(f"  迭代: {iterations}")
    print(f"  Min:  {_fmt(min(latencies))}")
    print(f"  Mean: {_fmt(mean)}")
    print(f"  P50:  {_fmt(p50)}")
    print(f"  P95:  {_fmt(p95)}")
    print(f"  P99:  {_fmt(p99)}")
    print(f"  Max:  {_fmt(max(latencies))}")
    print(f"  QPS:  {qps:.2f}")
    print(f"  目标 P99 ≤ 500ms: {'✅ 通过' if p99 <= 500 else '❌ 未达标'}")

    return {
        "test": f"jina_gpu_{device}",
        "device": device,
        "load_time_s": round(load_time, 2),
        "iterations": iterations,
        "min_ms": round(min(latencies), 2),
        "mean_ms": round(mean, 2),
        "p50_ms": round(p50, 2),
        "p95_ms": round(p95, 2),
        "p99_ms": round(p99, 2),
        "max_ms": round(max(latencies), 2),
        "qps": round(qps, 2),
        "target_p99_ms": 500,
        "passed": p99 <= 500,
    }


def verify_sort_correctness() -> dict:
    """验证 GPU 排序正确性"""
    print(f"\n{'─'*60}")
    print(f"验证: GPU 排序正确性")
    print(f"{'─'*60}")

    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_PATH, trust_remote_code=True
    ).to(device)
    model.eval()

    pairs = [
        ("帮我识别语音", "语音交互助手 语音识别 语音转文字"),
        ("帮我识别语音", "PDF 解析器 文档提取"),
        ("帮我识别语音", "自我反思 复盘 改进"),
    ]
    encoded = tokenizer([p[0] for p in pairs], [p[1] for p in pairs],
                        padding=True, truncation=True, max_length=512, return_tensors="pt").to(device)
    with torch.no_grad():
        scores = model(**encoded).logits.cpu().numpy().flatten()

    voice_ok = scores[0] > scores[1] and scores[0] > scores[2]
    print(f"  语音查询分数: {[round(float(s), 4) for s in scores]}")
    print(f"  语音匹配最高: {'✅' if voice_ok else '❌'}")

    return {"test": "gpu_sort", "passed": voice_ok}


def main() -> int:
    print("=" * 60)
    print("  jina-reranker-v2 GPU 压测")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    print("\n检查 GPU 可用性...")
    gpu_ok = check_gpu_available()

    if not gpu_ok:
        print("\n" + "=" * 60)
        print("  GPU 不可用 - 安装指南")
        print("=" * 60)
        print("\n当前 torch 是 CPU 版本，需安装 GPU 版本:")
        print("\n  步骤 1: 卸载 CPU 版本")
        print("    pip uninstall torch torchvision torchaudio -y")
        print("\n  步骤 2: 安装 CUDA 12.1 版本（推荐）")
        print("    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121")
        print("\n  步骤 3: 验证 GPU")
        print("    python -c \"import torch; print(torch.cuda.is_available())\"")
        print("\n  步骤 4: 重新运行本压测")
        print("    python scripts/benchmark_v65_gpu_reranker.py")
        print("\n注意: GTX 1650（4GB）足够运行 jina-reranker-v2（280MB）")
        print("      预期 GPU 推理 P99 < 100ms，满足 500ms SLO")
        return 1

    results = []
    results.append(verify_sort_correctness())
    results.append(benchmark_gpu_reranker(iterations=20))

    print("\n" + "=" * 60)
    print("  GPU 压测结果汇总")
    print("=" * 60)
    all_passed = True
    for r in results:
        test_name = r["test"]
        passed = r.get("passed", False)
        status = "✅ 通过" if passed else "❌ 未达标"
        p99 = r.get("p99_ms", "N/A")
        print(f"  {test_name}: {status}" + (f" (P99={p99}ms)" if p99 != "N/A" else ""))
        if not passed:
            all_passed = False

    # 对比 CPU 基准
    print("\n" + "=" * 60)
    print("  CPU vs GPU 对比")
    print("=" * 60)
    print(f"  {'推理方式':<20} {'P99 延迟':<15} {'QPS':<10} {'SLO':<10}")
    print(f"  {'─'*55}")
    print(f"  {'CPU (PyTorch)':<20} {'7960ms':<15} {'0.15':<10} {'❌':<10}")
    for r in results:
        if "p99_ms" in r:
            p99 = r["p99_ms"]
            qps = r.get("qps", 0)
            slo = "✅" if r.get("passed") else "❌"
            print(f"  {r['test']:<20} {p99}ms{'':<8} {qps:<10} {slo:<10}")

    report_path = os.path.join(project_root, "docs", "v65_gpu_benchmark.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
            "model": "jinaai/jina-reranker-v2-base-multilingual (GPU)",
            "cpu_baseline_p99_ms": 7960,
            "results": results,
            "all_passed": all_passed,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {report_path}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
