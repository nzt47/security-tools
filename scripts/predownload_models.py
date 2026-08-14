"""预下载 HuggingFace embedding 模型到 Docker 镜像中

问题：
    测试运行时 VectorStore.__init__ → SentenceTransformer(model_name) 会从
    HuggingFace 下载模型（~100-500MB）。Docker 容器中网络不通或慢时，
    会导致测试超时失败。

方案：
    在 Docker 构建阶段预下载模型到 /app/.hf_cache，打包到镜像中。
    测试运行时直接从本地缓存加载，无需网络访问。

用法：
    # 在 Dockerfile 中调用
    RUN python scripts/predownload_models.py

    # 本地手动预下载
    python scripts/predownload_models.py --models all-MiniLM-L6-v2 paraphrase-multilingual-MiniLM-L12-v2

    # 查看已缓存模型
    python scripts/predownload_models.py --list

环境变量：
    HF_HOME: HuggingFace 缓存目录（默认 /app/.hf_cache）
    PRELOAD_MODELS: 逗号分隔的模型名列表（覆盖默认列表）
"""
import os
import sys
import time
import argparse
from pathlib import Path

# 确保项目根目录在 sys.path 中（Docker 中 WORKDIR /app 已包含，本地运行时兜底）
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.utils.docker_fault_tolerance import safe_download_resources, BatchResult

# 默认预下载的模型列表
DEFAULT_MODELS = [
    # vector_store.py 默认模型（多语言，384 维）
    "paraphrase-multilingual-MiniLM-L12-v2",
    # 常用英文模型（384 维，体积小，加载快）
    "all-MiniLM-L6-v2",
    # 中文 embedding 模型（512 维，HolographicAdapter 可能使用）
    "BAAI/bge-small-zh-v1.5",
]


def get_cache_dir() -> Path:
    """获取 HuggingFace 缓存目录"""
    cache_dir = os.environ.get("HF_HOME", "/app/.hf_cache")
    path = Path(cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _set_cache_env(cache_dir: Path, timeout: int):
    """设置 HuggingFace 缓存目录和下载超时环境变量（外层一次性设置）

    【不易】4 个环境变量必须设置：HF_HOME/TRANSFORMERS_CACHE/SENTENCE_TRANSFORMERS_HOME/HF_HUB_DOWNLOAD_TIMEOUT
    【简易】集中设置避免每个模型重复设置

    Args:
        cache_dir: 缓存目录
        timeout: 下载超时秒数（HF_HUB_DOWNLOAD_TIMEOUT 控制 HTTP 层超时）
    """
    os.environ["HF_HOME"] = str(cache_dir)
    os.environ["TRANSFORMERS_CACHE"] = str(cache_dir)
    os.environ["SENTENCE_TRANSFORMERS_HOME"] = str(cache_dir)
    os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = str(timeout)


def list_cached_models(cache_dir: Path):
    """列出已缓存的模型

    【不易】HF 实际缓存结构为 {HF_HOME}/hub/models--<org>--<name>/（无 org 前缀
    模型自动补全 sentence-transformers 组织），早期实现漏了 hub/ 子目录导致
    已下载模型被误报为"缓存目录无模型"（build 日志误导排查）。此处统一走
    hub/ 子目录统计，与 vector_store._is_model_fully_cached 检查路径一致。
    """
    models_dir = cache_dir / "hub" / "models--"
    if not models_dir.exists():
        print(f"  缓存目录无模型: {cache_dir}")
        return

    print(f"  缓存目录: {cache_dir}")
    models = sorted(models_dir.iterdir()) if models_dir.exists() else []
    if not models:
        print("  无已缓存模型")
        return

    total_size = 0
    for model_dir in models:
        # models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2
        # → sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
        model_name = model_dir.name.replace("models--", "").replace("--", "/")
        size = sum(f.stat().st_size for f in model_dir.rglob("*") if f.is_file())
        size_mb = size / (1024 * 1024)
        total_size += size_mb
        print(f"  {model_name}: {size_mb:.1f}MB")

    print(f"  总计: {total_size:.1f}MB ({len(models)} 个模型)")


def main():
    parser = argparse.ArgumentParser(
        description="预下载 HuggingFace embedding 模型到 Docker 镜像"
    )
    parser.add_argument(
        "--models", nargs="*", default=None,
        help=f"要下载的模型列表（默认: {' '.join(DEFAULT_MODELS)}）"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="列出已缓存的模型"
    )
    parser.add_argument(
        "--timeout", type=int, default=300,
        help="每个模型的下载超时秒数（默认 300）"
    )
    args = parser.parse_args()

    cache_dir = get_cache_dir()

    if args.list:
        print("=== 已缓存模型 ===")
        list_cached_models(cache_dir)
        return

    models = args.models or os.environ.get("PRELOAD_MODELS", "").split(",")
    models = [m.strip() for m in models if m.strip()] or DEFAULT_MODELS

    print("=" * 60)
    print("预下载 HuggingFace embedding 模型")
    print("=" * 60)
    print(f"缓存目录: {cache_dir}")
    print(f"模型列表: {models}")
    print(f"超时秒数: {args.timeout}")
    print()

    # 检查 sentence_transformers 是否可用
    try:
        import sentence_transformers
        print(f"sentence_transformers 版本: {sentence_transformers.__version__}")
    except ImportError:
        print("[ERROR] sentence_transformers 未安装，无法预下载模型")
        sys.exit(1)

    print()

    # 设置缓存环境变量（外层一次性，避免每个模型重复设置）
    _set_cache_env(cache_dir, args.timeout)

    # 下载函数（闭包绑定 cache_dir，保留 dim/size 验证逻辑）
    # 【不易】model.encode(["test"]) 验证 + dim/size 输出必须保留
    # 【变易】适配 safe_download_resources 的 download_fn 签名（接收单 resource 参数）
    def _download_fn(model_name: str):
        """单个模型下载函数（由 safe_download_single 包裹 try/except）"""
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(model_name)
        # 验证模型可用（生成测试向量，不变量）
        test_vec = model.encode(["test"])
        dim = len(test_vec[0])

        # 缓存大小统计（路径需含 hub/ 子目录；无 org 前缀模型 HF 自动存为
        # models--sentence-transformers--<name>，两种形式都探测，与
        # vector_store._is_model_fully_cached 检查逻辑一致）
        candidates = [
            cache_dir / "hub" / "models--" / model_name.replace("/", "--"),
        ]
        if "/" not in model_name:
            candidates.append(
                cache_dir / "hub" / "models--sentence-transformers--" / model_name
            )
        size_mb = 0.0
        for model_path in candidates:
            if model_path.exists():
                for f in model_path.rglob("*"):
                    if f.is_file():
                        size_mb += f.stat().st_size
                size_mb = size_mb / (1024 * 1024)
                break
        print(f"dim={dim} {size_mb:.1f}MB", end=" ")

    # 调用容错工具批量下载（替代手写 for 循环 + try/except）
    result: BatchResult = safe_download_resources(
        resources=models,
        download_fn=_download_fn,
        timeout=args.timeout,
        timeout_env_var="HF_HUB_DOWNLOAD_TIMEOUT",
        exit_zero_on_partial_failure=False,  # 由 main() 控制 sys.exit
    )

    print()
    print("=" * 60)
    print(f"预下载完成: {result.succeeded}/{result.total} 成功")
    if result.failed_resources:
        print(f"失败模型: {result.failed_resources}")
        print("[WARN] 部分模型下载失败，测试时可能需要网络访问")
    print("=" * 60)

    # 显示缓存大小
    print()
    list_cached_models(cache_dir)

    # 【不易】即使部分失败也返回 0（不阻断 Docker 构建）
    sys.exit(0)


if __name__ == "__main__":
    main()
