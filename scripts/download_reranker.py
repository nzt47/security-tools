"""后台下载 BGE-reranker-v2-m3 模型 — 支持断点续传

策略:
    1. 通过 hf-mirror.com 镜像下载(国内稳定)
    2. 禁用 Xet 协议(HF_XET_HIGH_PERFORMANCE=0),避免 CAS Server 401 鉴权失败
    3. 失败时输出明确错误码

【简易】单一职责:仅下载,不做其他事
"""
import os
import sys
import time

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 【不易】HF 新版(>=1.20)默认启用 Xet 协议传输大文件,CAS Server 需鉴权会 401 失败
# 必须显式禁用 Xet,强制走传统 HTTP 下载
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "0")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")


def main():
    print(f"[start] downloading BAAI/bge-reranker-v2-m3 ...", flush=True)
    print(f"[env] HF_ENDPOINT={os.environ.get('HF_ENDPOINT')}", flush=True)
    print(f"[env] HF_HUB_DISABLE_XET={os.environ.get('HF_HUB_DISABLE_XET')}", flush=True)
    print(f"[env] HF_XET_HIGH_PERFORMANCE={os.environ.get('HF_XET_HIGH_PERFORMANCE')}", flush=True)

    try:
        from huggingface_hub import snapshot_download
    except ImportError as e:
        print(f"[fail] huggingface_hub not installed: {e}", flush=True)
        return 2

    t0 = time.time()
    try:
        path = snapshot_download(
            repo_id="BAAI/bge-reranker-v2-m3",
            # 仅下载必需文件，避免 .gitattributes 等无关文件拖慢
            allow_patterns=["*.json", "*.txt", "*.safetensors", "*.bin", "tokenizer*"],
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
