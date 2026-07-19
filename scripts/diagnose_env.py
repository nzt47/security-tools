"""诊断 sentence-transformers / BGE-m3 / Cross-Encoder 依赖可用性"""
import os
import sys

print(f"Python: {sys.version}")
print()

# 1. sentence-transformers
try:
    import sentence_transformers
    print(f"sentence_transformers: {sentence_transformers.__version__}")
except ImportError as e:
    print(f"sentence_transformers: NOT INSTALLED ({e})")

# 2. torch
try:
    import torch
    print(f"torch: {torch.__version__}")
    print(f"torch.cuda.is_available: {torch.cuda.is_available()}")
except Exception as e:
    print(f"torch: ERROR ({e})")

# 3. onnxruntime
try:
    import onnxruntime
    print(f"onnxruntime: {onnxruntime.__version__}")
except Exception as e:
    print(f"onnxruntime: ERROR ({e})")

# 4. chromadb
try:
    import chromadb
    print(f"chromadb: {chromadb.__version__}")
except Exception as e:
    print(f"chromadb: ERROR ({e})")

# 5. transformers (Cross-Encoder 用)
try:
    import transformers
    print(f"transformers: {transformers.__version__}")
except Exception as e:
    print(f"transformers: ERROR ({e})")

print()
print(f"HF_ENDPOINT: {os.environ.get('HF_ENDPOINT', 'not set')}")
print(f"ANONYMIZED_TELEMETRY: {os.environ.get('ANONYMIZED_TELEMETRY', 'not set')}")

# 6. 检查 BGE-m3 模型缓存
print()
print("=== HuggingFace 缓存检查 ===")
cache_dir = os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
print(f"HF_HOME: {cache_dir}")
if os.path.exists(cache_dir):
    for entry in os.listdir(cache_dir)[:10]:
        print(f"  {entry}")
