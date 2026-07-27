"""v6.5 Reranker 模型加载验证（页面文件扩容后）

验证策略（按优先级）:
    Step 1: 离线模式加载 bge-reranker-v2-m3（有本地缓存，验证页面文件扩容是否解决 os error 1455）
    Step 2: 若 Step 1 成功，尝试联网下载 bge-reranker-base（HF 镜像 + 禁用离线）

输出: 加载耗时 + 排序分数 + 内存占用
"""
import os
import sys
import time
import traceback

# 【关键】先用离线模式加载 v2-m3（有缓存），避免联网验证导致超时
# 离线模式下 sentence_transformers 直接用本地缓存，不发起 HEAD 请求
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")

def _fmt_mem(mb: float) -> str:
    if mb < 1024:
        return f"{mb:.1f}MB"
    return f"{mb/1024:.2f}GB"

def _get_process_rss_mb() -> float:
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    except ImportError:
        return 0.0

def _try_load(model_name: str, timeout_label: str) -> dict:
    """尝试加载单个模型，返回结果字典"""
    print(f"\n[{timeout_label}] 尝试加载: {model_name}")
    print(f"  进程初始 RSS: {_fmt_mem(_get_process_rss_mb())}")

    t0 = time.time()
    result = {
        "model": model_name,
        "success": False,
        "load_time_s": 0.0,
        "error": None,
        "scores": None,
        "rss_after_load_mb": 0.0,
    }

    try:
        from sentence_transformers import CrossEncoder
        print(f"  sentence_transformers 导入成功 ({time.time()-t0:.2f}s)")

        t_load = time.time()
        model = CrossEncoder(model_name)
        load_elapsed = time.time() - t_load
        result["load_time_s"] = round(load_elapsed, 2)
        result["rss_after_load_mb"] = _get_process_rss_mb()
        print(f"  ✅ 模型加载成功 ({load_elapsed:.2f}s)")
        print(f"  加载后 RSS: {_fmt_mem(result['rss_after_load_mb'])}")

        # 排序质量验证
        t_pred = time.time()
        pairs = [
            ("语音识别", "语音交互助手 语音识别 语音转文字"),
            ("语音识别", "PDF 文件解析器 文档提取"),
            ("帮我反思刚才的回答", "自我反思 复盘 改进建议"),
            ("帮我反思刚才的回答", "PDF 文件解析器 文档提取"),
        ]
        scores = model.predict(pairs)
        pred_elapsed = time.time() - t_pred
        result["scores"] = [round(float(s), 4) for s in scores]
        print(f"  排序预测完成 ({pred_elapsed:.3f}s)")
        print(f"  分数: {result['scores']}")
        print(f"  语音匹配 > 语音不匹配: {scores[0] > scores[1]} ✅" if scores[0] > scores[1] else f"  语音匹配 > 语音不匹配: {scores[0] > scores[1]} ❌")
        print(f"  反思匹配 > 反思不匹配: {scores[2] > scores[3]} ✅" if scores[2] > scores[3] else f"  反思匹配 > 反思不匹配: {scores[2] > scores[3]} ❌")

        result["success"] = True
        del model  # 释放模型
        return result

    except Exception as e:
        load_elapsed = time.time() - t0
        result["load_time_s"] = round(load_elapsed, 2)
        result["error"] = f"{type(e).__name__}: {str(e)[:500]}"
        result["rss_after_load_mb"] = _get_process_rss_mb()
        print(f"  ❌ 加载失败 ({load_elapsed:.2f}s)")
        print(f"  异常类型: {type(e).__name__}")
        print(f"  异常消息: {str(e)[:300]}")
        # 打印完整堆栈（前 20 行）
        tb = traceback.format_exc().split('\n')
        print(f"  堆栈（前 20 行）:")
        for line in tb[:20]:
            if line.strip():
                print(f"    {line}")
        return result


def main() -> int:
    print("=" * 70)
    print("  v6.5 Reranker 模型加载验证（页面文件扩容后）")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 环境状态
    print(f"\n环境状态:")
    print(f"  Python: {sys.version.split()[0]}")
    print(f"  HF_HUB_OFFLINE: {os.environ.get('HF_HUB_OFFLINE')}")
    print(f"  HF_ENDPOINT: {os.environ.get('HF_ENDPOINT')}")
    print(f"  进程初始 RSS: {_fmt_mem(_get_process_rss_mb())}")

    results = []

    # Step 1: 验证 bge-reranker-v2-m3（有缓存，离线模式，验证页面文件扩容）
    print("\n" + "─" * 70)
    print("Step 1: 离线模式加载 bge-reranker-v2-m3（有本地缓存）")
    print("  目的: 确认页面文件扩容到 32GB 后，os error 1455 是否解决")
    print(f"  离线模式: HF_HUB_OFFLINE={os.environ.get('HF_HUB_OFFLINE')}")
    print("─" * 70)
    results.append(_try_load("BAAI/bge-reranker-v2-m3", "Step1"))

    # Step 2: 验证 bge-reranker-base（默认模型）
    # 若 Step 1 成功，说明页面文件扩容有效，尝试下载 base
    if results[-1]["success"]:
        print("\n" + "─" * 70)
        print("Step 2: 加载 bge-reranker-base（默认模型）")
        print("  目的: 确认默认模型可加载")
        print("  策略: 先尝试离线模式（若有缓存），再尝试联网下载")
        print("─" * 70)
        # 先用离线模式尝试（如果有缓存）
        results.append(_try_load("BAAI/bge-reranker-base", "Step2-offline"))
        # 若离线失败，尝试联网下载
        if not results[-1]["success"]:
            print("\n  离线加载失败，尝试联网下载...")
            os.environ["HF_HUB_OFFLINE"] = "0"
            os.environ["TRANSFORMERS_OFFLINE"] = "0"
            results.append(_try_load("BAAI/bge-reranker-base", "Step2-online"))
    else:
        print("\n⚠️ Step 1 失败，跳过 Step 2（页面文件扩容未生效）")
        results.append({
            "model": "BAAI/bge-reranker-base",
            "success": False,
            "load_time_s": 0,
            "error": "skipped (Step1 failed)",
            "scores": None,
            "rss_after_load_mb": 0,
        })

    # 汇总
    print("\n" + "=" * 70)
    print("  验证结果汇总")
    print("=" * 70)
    for r in results:
        status = "✅ 成功" if r["success"] else "❌ 失败"
        print(f"\n{r['model']}: {status}")
        print(f"  耗时: {r['load_time_s']}s")
        print(f"  RSS: {_fmt_mem(r['rss_after_load_mb'])}")
        if r["success"]:
            print(f"  排序分数: {r['scores']}")
        else:
            print(f"  错误: {r['error'][:200] if r['error'] else 'N/A'}")

    # 退出码：至少一个成功
    any_success = any(r["success"] for r in results)
    return 0 if any_success else 1


if __name__ == "__main__":
    sys.exit(main())
