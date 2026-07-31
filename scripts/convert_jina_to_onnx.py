"""将 jina-reranker-v2 转换为 ONNX 格式并量化（INT8 动态量化）

目的:
    jina-reranker-v2 PyTorch CPU 推理 P99 7960ms（20候选），远超 500ms SLO。
    ONNX Runtime + INT8 量化预期可提升 2-5x 推理速度，尝试达标。

转换流程:
    1. 加载 PyTorch 模型（trust_remote_code=True）
    2. 导出为 ONNX 格式（torch.onnx.export）
    3. INT8 动态量化（onnxruntime.quantization.quantize_dynamic）
    4. 验证 ONNX 模型可加载且排序结果一致

使用方法:
    python scripts/convert_jina_to_onnx.py

输出:
    - ONNX 原始模型: <model_dir>/model.onnx
    - ONNX 量化模型: <model_dir>/model_quantized.onnx
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

MODEL_PATH = "C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual"


def step1_export_onnx() -> bool:
    """步骤 1: 将 PyTorch 模型导出为 ONNX"""
    print("\n[步骤 1/4] 导出 ONNX 模型")
    print(f"  源模型: {MODEL_PATH}")

    try:
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification

        print("  加载 tokenizer...")
        tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)

        print("  加载 PyTorch 模型...")
        # jina-reranker-v2 权重为 BFloat16，转为 float32 以支持 ONNX 导出和 numpy
        model = AutoModelForSequenceClassification.from_pretrained(
            MODEL_PATH, trust_remote_code=True, torch_dtype=torch.float32
        )
        model.eval()

        # 构造 dummy input
        print("  构造 dummy input...")
        dummy_text = ["语音识别", "PDF 解析"]
        dummy_pairs = list(zip(dummy_text, dummy_text))
        encoded = tokenizer(
            dummy_text, dummy_text,
            padding=True, truncation=True, max_length=512, return_tensors="pt"
        )

        onnx_path = os.path.join(MODEL_PATH, "model.onnx")
        print(f"  导出 ONNX: {onnx_path}")

        # jina-reranker-v2 的 forward 可能需要特定参数，用 dynamic_axes 支持动态 batch
        with torch.no_grad():
            torch.onnx.export(
                model,
                (encoded["input_ids"], encoded["attention_mask"]),
                onnx_path,
                opset_version=14,
                input_names=["input_ids", "attention_mask"],
                output_names=["logits"],
                dynamic_axes={
                    "input_ids": {0: "batch", 1: "sequence"},
                    "attention_mask": {0: "batch", 1: "sequence"},
                    "logits": {0: "batch"},
                },
            )

        file_size = os.path.getsize(onnx_path) / 1024 / 1024
        print(f"  ✅ ONNX 导出成功 ({file_size:.1f}MB)")
        return True

    except Exception as e:
        print(f"  ❌ ONNX 导出失败: {type(e).__name__}: {str(e)[:500]}")
        import traceback
        traceback.print_exc()
        return False


def step2_quantize_onnx() -> bool:
    """步骤 2: INT8 动态量化"""
    print("\n[步骤 2/4] INT8 动态量化")

    try:
        from onnxruntime.quantization import quantize_dynamic, QuantType

        onnx_path = os.path.join(MODEL_PATH, "model.onnx")
        quantized_path = os.path.join(MODEL_PATH, "model_quantized.onnx")

        if not os.path.exists(onnx_path):
            print(f"  ❌ 源 ONNX 不存在: {onnx_path}")
            return False

        print(f"  量化: {onnx_path}")
        print(f"  输出: {quantized_path}")

        t0 = time.time()
        quantize_dynamic(
            onnx_path,
            quantized_path,
            weight_type=QuantType.QInt8,
        )
        elapsed = time.time() - t0

        orig_size = os.path.getsize(onnx_path) / 1024 / 1024
        quant_size = os.path.getsize(quantized_path) / 1024 / 1024
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
    """步骤 3: 验证 ONNX 模型可加载"""
    print("\n[步骤 3/4] 验证 ONNX 模型")

    try:
        import onnxruntime as ort

        for name, path in [
            ("原始 ONNX", os.path.join(MODEL_PATH, "model.onnx")),
            ("量化 ONNX", os.path.join(MODEL_PATH, "model_quantized.onnx")),
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
    """步骤 4: 对比 PyTorch vs ONNX 排序分数一致性"""
    print("\n[步骤 4/4] 排序分数一致性对比")

    try:
        import torch
        import onnxruntime as ort
        from transformers import AutoTokenizer, AutoModelForSequenceClassification

        tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)

        pairs = [
            ("语音识别", "语音交互助手 语音识别 语音转文字"),
            ("语音识别", "PDF 文件解析器 文档提取"),
        ]
        texts_a = [p[0] for p in pairs]
        texts_b = [p[1] for p in pairs]

        # PyTorch 推理
        print("  PyTorch 推理...")
        model = AutoModelForSequenceClassification.from_pretrained(
            MODEL_PATH, trust_remote_code=True
        ).float()
        model.eval()
        encoded = tokenizer(texts_a, texts_b, padding=True, truncation=True, max_length=512, return_tensors="pt")
        with torch.no_grad():
            pt_scores = model(**encoded).logits.cpu().numpy().flatten()
        print(f"    分数: {[round(float(s), 4) for s in pt_scores]}")

        # ONNX 推理
        onnx_path = os.path.join(MODEL_PATH, "model.onnx")
        quantized_path = os.path.join(MODEL_PATH, "model_quantized.onnx")

        for name, path in [("ONNX", onnx_path), ("量化ONNX", quantized_path)]:
            if not os.path.exists(path):
                continue
            print(f"  {name} 推理...")
            sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
            input_feed = {
                "input_ids": encoded["input_ids"].cpu().numpy(),
                "attention_mask": encoded["attention_mask"].cpu().numpy(),
            }
            onnx_scores = sess.run(None, input_feed)[0].flatten()
            print(f"    分数: {[round(float(s), 4) for s in onnx_scores]}")

            # 一致性检查
            diff = np.abs(pt_scores - onnx_scores).max()
            print(f"    最大误差: {diff:.6f} {'✅' if diff < 0.1 else '⚠️'}")

        return True

    except Exception as e:
        print(f"  ❌ 对比失败: {type(e).__name__}: {str(e)[:500]}")
        import traceback
        traceback.print_exc()
        return False


def main() -> int:
    print("=" * 60)
    print("  jina-reranker-v2 ONNX 转换 + 量化")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模型: {MODEL_PATH}")
    print("=" * 60)

    # 检查 onnx 模块
    try:
        import onnx  # noqa: F401
    except ImportError:
        print("\n安装 onnx 模块...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "onnx"])
        print("✅ onnx 安装成功")

    results = []
    results.append(("ONNX 导出", step1_export_onnx()))
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
        print("\n下一步: 运行 ONNX 压测验证性能")
        print("  python scripts/benchmark_v65_onnx_reranker.py")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
