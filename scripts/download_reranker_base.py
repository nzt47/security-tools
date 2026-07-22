"""使用 modelscope 下载 BAAI/bge-reranker-base 模型（中文专用，约 1.1GB）

对比 bge-reranker-v2-m3 (2.2GB)：
- 内存占用：约 1.1GB vs 2.2GB（减半）
- 加载速度：预期更快
- 中文判别力：base 专为中文优化，v2-m3 多语言
"""
import os
import sys
import time
from pathlib import Path

DEFAULT_CACHE = os.path.expanduser("~/.cache/modelscope")


def main():
    print(f"[start] downloading BAAI/bge-reranker-base via modelscope...", flush=True)
    print(f"[cache_dir] {DEFAULT_CACHE}", flush=True)

    try:
        from modelscope import snapshot_download
    except ImportError as e:
        print(f"[fail] modelscope not installed: {e}", flush=True)
        return 2

    t0 = time.time()
    try:
        path = snapshot_download(
            model_id="BAAI/bge-reranker-base",
            cache_dir=DEFAULT_CACHE,
            allow_patterns=[
                "config.json",
                "tokenizer_config.json",
                "tokenizer.json",
                "special_tokens_map.json",
                "vocab.txt",
                "*.safetensors",
                "*.bin",
            ],
        )
        elapsed = time.time() - t0
        print(f"[ok] DOWNLOAD_OK path={path} elapsed={elapsed:.1f}s", flush=True)
        return 0
    except Exception as e:
        elapsed = time.time() - t0
        print(f"[fail] DOWNLOAD_FAIL elapsed={elapsed:.1f}s", flush=True)
        print(f"[error_type] {type(e).__name__}", flush=True)
        print(f"[error_msg] {str(e)[:500]}", flush=True)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
