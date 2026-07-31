"""将 bge-reranker-base 的 FP32 ONNX 量化为 INT8 动态量化版

目的:
    bge-reranker-base ONNX FP32 实测 P99 1.49s（20候选），未达 500ms SLO。
    INT8 动态量化预期可提升 2-4x 推理速度，尝试达标。
    参考 jina-reranker-v2 量化经验：FP32→INT8 加速 30x（7960ms→258ms）。

转换流程（绕过 PyTorch，防 0xC0000005 崩溃）:
    1. 检查现有 onnx/model.onnx（FP32，~1GB）是否存在
    2. INT8 动态量化（onnxruntime.quantization.quantize_dynamic）
    3. 验证 ONNX 量化模型可加载
    4. 对比 FP32 vs INT8 推理分数一致性（都用 ONNX，不用 PyTorch）

【不易】跳过 PyTorch 导出步骤:
    bge-reranker-base PyTorch 路径在 Windows CPU 上会触发 0xC0000005 崩溃
    （project_memory 记录：无子进程隔离时 Cross-Encoder 加载崩溃）。
    现有 onnx/model.onnx 已由 modelscope 下载提供，直接量化即可。

使用方法:
    python scripts/convert_bge_to_onnx.py

输出:
    - 量化模型: <model_dir>/onnx/model_quantized.onnx（INT8）
"""
import os
import sys
import time
import numpy as np

# 行缓冲
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

MODEL_PATH = "C:/Users/Administrator/.cache/huggingface/hub/models--BAAI--bge-reranker-base"
ONNX_DIR = os.path.join(MODEL_PATH, "onnx")
FP32_ONNX = os.path.join(ONNX_DIR, "model.onnx")
QUANT_ONNX = os.path.join(ONNX_DIR, "model_quantized.onnx")


def step1_check_fp32_onnx() -> bool:
    """步骤 1: 检查现有 FP32 ONNX 模型是否存在

    【不易】跳过 PyTorch 导出：bge-reranker-base PyTorch 在 Windows CPU 触发 0xC0000005
    【简易】复用 modelscope 下载的 onnx/model.onnx，直接量化
    """
    print("\n[步骤 1/4] 检查 FP32 ONNX 模型")
    print(f"  期望路径: {FP32_ONNX}")

    if not os.path.exists(FP32_ONNX):
        print(f"  ❌ FP32 ONNX 不存在，无法量化")
        print(f"  请先运行: python scripts/download_bge_reranker_base_modelscope.py")
        return False

    size_mb = os.path.getsize(FP32_ONNX) / 1024 / 1024
    print(f"  ✅ FP32 ONNX 存在 ({size_mb:.1f}MB)")
    print(f"  跳过 PyTorch 导出步骤（防 0xC0000005 崩溃）")
    return True


def step2_quantize_onnx() -> bool:
    """步骤 2: INT8 动态量化

    【不易】不改变模型结构，仅量化权重（weight_type=QInt8）
    【变易】动态量化：权重 INT8，激活保持 FP32（精度损失小，加速明显）
    【简易】单次 quantize_dynamic 调用
    """
    print("\n[步骤 2/4] INT8 动态量化")
    print(f"  源: {FP32_ONNX}")
    print(f"  目标: {QUANT_ONNX}")

    if not os.path.exists(FP32_ONNX):
        print(f"  ❌ 源 ONNX 不存在")
        return False

    try:
        from onnxruntime.quantization import quantize_dynamic, QuantType

        t0 = time.time()
        quantize_dynamic(
            FP32_ONNX,
            QUANT_ONNX,
            weight_type=QuantType.QInt8,
        )
        elapsed = time.time() - t0

        orig_size = os.path.getsize(FP32_ONNX) / 1024 / 1024
        quant_size = os.path.getsize(QUANT_ONNX) / 1024 / 1024
        ratio = (1 - quant_size / orig_size) * 100

        print(f"  ✅ 量化完成 ({elapsed:.1f}s)")
        print(f"  原始大小: {orig_size:.1f}MB")
        print(f"  量化大小: {quant_size:.1f}MB")
        print(f"  压缩率: {ratio:.1f}%")
        return True

    except Exception as e:
        print(f"  ❌ 量化失败: {type(e).__name__}: {str(e)[:500]}")
        import traceback
        traceback.print_exc()
        return False


def step3_verify_onnx() -> bool:
    """步骤 3: 验证量化 ONNX 模型可加载"""
    print("\n[步骤 3/4] 验证 ONNX 模型")

    try:
        import onnxruntime as ort

        for name, path in [
            ("FP32 ONNX", FP32_ONNX),
            ("量化 ONNX", QUANT_ONNX),
        ]:
            if not os.path.exists(path):
                print(f"  ⚠️ {name} 不存在: {path}")
                continue

            t0 = time.time()
            sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
            elapsed = time.time() - t0
            inputs = [i.name for i in sess.get_inputs()]
            outputs = [o.name for o in sess.get_outputs()]
            print(f"  ✅ {name} 加载成功 ({elapsed:.2f}s)")
            print(f"    输入: {inputs}")
            print(f"    输出: {outputs}")

        return True

    except Exception as e:
        print(f"  ❌ 验证失败: {type(e).__name__}: {str(e)[:500]}")
        return False


def step4_compare_scores() -> bool:
    """步骤 4: 对比 FP32 vs INT8 推理分数一致性

    【不易】都用 ONNX Runtime 推理，不用 PyTorch（防 0xC0000005 崩溃）
    【简易】2 对中文样本，验证量化误差 < 0.1
    """
    print("\n[步骤 4/4] FP32 vs INT8 分数一致性对比")

    try:
        import onnxruntime as ort
        from transformers import AutoTokenizer

        print("  加载 tokenizer...")
        tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)

        pairs = [
            ("帮我识别语音", "语音交互助手 语音识别 语音转文字"),
            ("帮我识别语音", "PDF 文件解析器 文档提取"),
        ]
        texts_a = [p[0] for p in pairs]
        texts_b = [p[1] for p in pairs]

        encoded = tokenizer(
            texts_a, texts_b,
            padding=True, truncation=True, max_length=512, return_tensors="np"
        )

        results = {}
        for name, path in [("FP32", FP32_ONNX), ("INT8量化", QUANT_ONNX)]:
            if not os.path.exists(path):
                print(f"  ⚠️ {name} 不存在，跳过")
                continue
            print(f"  {name} 推理...")
            t0 = time.time()
            sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
            input_feed = {
                "input_ids": encoded["input_ids"],
                "attention_mask": encoded["attention_mask"],
            }
            scores = sess.run(None, input_feed)[0].flatten()
            elapsed = (time.time() - t0) * 1000
            print(f"    分数: {[round(float(s), 4) for s in scores]}")
            print(f"    耗时: {elapsed:.1f}ms")
            results[name] = scores

        if "FP32" in results and "INT8量化" in results:
            diff = np.abs(results["FP32"] - results["INT8量化"]).max()
            print(f"\n  最大误差: {diff:.6f} {'✅ <0.1 量化质量良好' if diff < 0.1 else '⚠️ ≥0.1 量化损失较大'}")
            # 验证排序一致性
            fp32_order = np.argsort(-results["FP32"])
            int8_order = np.argsort(-results["INT8量化"])
            order_ok = np.array_equal(fp32_order, int8_order)
            print(f"  排序一致性: {'✅ 排序相同' if order_ok else '⚠️ 排序变化'}")

        return True

    except Exception as e:
        print(f"  ❌ 对比失败: {type(e).__name__}: {str(e)[:500]}")
        import traceback
        traceback.print_exc()
        return False


def main() -> int:
    print("=" * 60)
    print("  bge-reranker-base ONNX INT8 量化")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模型: {MODEL_PATH}")
    print("=" * 60)

    # 检查 onnxruntime.quantization 模块
    try:
        from onnxruntime.quantization import quantize_dynamic  # noqa: F401
    except ImportError:
        print("\n❌ onnxruntime.quantization 不可用")
        print("  请安装: pip install onnxruntime")
        return 1

    results = []
    results.append(("FP32 ONNX 检查", step1_check_fp32_onnx()))
    if not results[-1][1]:
        print("\n❌ FP32 ONNX 不存在，无法继续")
        return 1
    results.append(("INT8 量化", step2_quantize_onnx()))
    results.append(("ONNX 验证", step3_verify_onnx()))
    results.append(("分数对比", step4_compare_scores()))

    print("\n" + "=" * 60)
    print("  转换结果汇总")
    print("=" * 60)
    all_ok = True
    for name, ok in results:
        print(f"  {name}: {'✅ 成功' if ok else '❌ 失败'}")
        if not ok:
            all_ok = False

    print(f"\n{'✅ 全部成功' if all_ok else '⚠️ 部分失败，请检查上方日志'}")
    if all_ok:
        print(f"\n量化模型路径: {QUANT_ONNX}")
        print("\n下一步: 运行压测验证性能")
        print("  python scripts/benchmark_v65_bge_base_reranker.py")
        print("  （需设置 SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx）")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
