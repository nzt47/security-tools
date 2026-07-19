"""使用 modelscope 下载 BGE-reranker-v2-m3 模型

【变易】modelscope 在国内访问速度优于 hf-mirror
【简易】单一职责：下载到本地缓存
"""
import os
import sys
import time

# modelscope 下载路径配置
DEFAULT_CACHE = os.path.expanduser("~/.cache/modelscope")


def main():
    print(f"[start] downloading BAAI/bge-reranker-v2-m3 via modelscope...", flush=True)
    print(f"[cache_dir] {DEFAULT_CACHE}", flush=True)

    try:
        from modelscope import snapshot_download
    except ImportError as e:
        print(f"[fail] modelscope not installed: {e}", flush=True)
        return 2

    t0 = time.time()
    try:
        path = snapshot_download(
            model_id="BAAI/bge-reranker-v2-m3",
            cache_dir=DEFAULT_CACHE,
            # 仅下载必需文件
            allow_patterns=[
                "config.json",
                "tokenizer_config.json",
                "tokenizer.json",
                "special_tokens_map.json",
                "vocab.txt",
                "*.safetensors",
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
