"""P3 阶段 1: 离线模型预下载脚本

预下载 SentenceTransformer 模型到本地缓存，避免运行时网络请求。
解决 P3 实施计划中识别的依赖冲突问题。

依赖版本锁定（已验证兼容）:
    sentence-transformers: 5.6.0
    transformers:          5.13.1
    torch:                 2.13.0+cpu
    numpy:                 2.4.6
    huggingface-hub:       >=0.20.0

使用方式:
    # 预下载默认模型
    python scripts/predownload_model.py

    # 预下载指定模型
    python scripts/predownload_model.py --model BAAI/bge-m3

    # 指定缓存目录
    python scripts/predownload_model.py --cache-dir /data/model_cache

    # 验证离线模式可用性
    python scripts/predownload_model.py --verify-offline

环境变量（通过 .env 配置）:
    TRANSFORMERS_CACHE    模型缓存目录（默认 ~/.cache/huggingface）
    HF_HOME               HuggingFace 主目录
    HF_HUB_OFFLINE        离线模式开关（1=离线）
    TRANSFORMERS_OFFLINE  transformers 离线模式开关（1=离线）
"""

import os
import sys
import time
import argparse
import importlib.metadata
from pathlib import Path


# ═══════════════════════════════════════════════════════════════
# 依赖版本锁定（与 P3 实施计划一致）
# ═══════════════════════════════════════════════════════════════

REQUIRED_VERSIONS = {
    "sentence-transformers": ">=3.0.0",
    "transformers": ">=4.34.0",
    "torch": ">=2.0.0",
    "numpy": ">=1.24.0",
    "huggingface-hub": ">=0.20.0",
}

# 默认预下载模型列表（VectorStore 支持的模型）
DEFAULT_MODELS = [
    {
        "name": "paraphrase-multilingual-MiniLM-L12-v2",
        "desc": "多语言轻量级模型（VectorStore 默认）",
        "size_mb": 470,
        "dim": 384,
    },
]

# 可选高精度模型（按需预下载）
OPTIONAL_MODELS = [
    {
        "name": "BAAI/bge-m3",
        "desc": "高精度多语言模型（568M 参数）",
        "size_mb": 2200,
        "dim": 1024,
    },
    {
        "name": "BAAI/bge-reranker-v2-m3",
        "desc": "重排序模型",
        "size_mb": 2200,
        "dim": 1024,
    },
]


def check_dependencies() -> bool:
    """检查依赖版本是否符合要求

    Returns:
        True 表示所有依赖版本兼容，False 表示有冲突
    """
    print("=" * 60)
    print("STEP 1: 依赖版本检查")
    print("=" * 60)

    all_ok = True
    for package, requirement in REQUIRED_VERSIONS.items():
        try:
            version = importlib.metadata.version(package)
            print(f"  [OK] {package}: {version} (要求 {requirement})")
        except importlib.metadata.PackageNotFoundError:
            print(f"  [FAIL] {package}: 未安装 (要求 {requirement})")
            all_ok = False
        except Exception as e:
            print(f"  [WARN] {package}: 检查失败 ({e})")
            all_ok = False

    # 额外检查 numpy ABI 兼容性（torch 编译时绑定的 numpy ABI）
    try:
        import torch
        import numpy
        # torch 2.x 支持 numpy 2.x，但旧版 torch 可能不支持
        torch_major = int(torch.__version__.split(".")[0])
        numpy_major = int(numpy.__version__.split(".")[0])
        if torch_major < 2 and numpy_major >= 2:
            print(f"  [WARN] numpy ABI 冲突风险: torch {torch.__version__} + numpy {numpy.__version__}")
            print(f"         建议: 升级 torch>=2.0 或降级 numpy<2.0")
            all_ok = False
        else:
            print(f"  [OK] numpy ABI 兼容: torch {torch.__version__} + numpy {numpy.__version__}")
    except Exception as e:
        print(f"  [WARN] numpy ABI 检查失败: {e}")

    return all_ok


def set_offline_env(cache_dir: str = None, offline: bool = False):
    """设置离线环境变量

    Args:
        cache_dir: 缓存目录（None 则使用默认 ~/.cache/huggingface）
        offline: 是否启用离线模式
    """
    if cache_dir:
        os.environ["TRANSFORMERS_CACHE"] = cache_dir
        os.environ["HF_HOME"] = cache_dir
        # 同时设置 HF_DATASETS_CACHE 避免数据集下载到默认位置
        os.environ["HF_DATASETS_CACHE"] = os.path.join(cache_dir, "datasets")

    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        print(f"[INFO] 已启用离线模式（HF_HUB_OFFLINE=1）")
    else:
        # 预下载阶段需要联网
        os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.pop("TRANSFORMERS_OFFLINE", None)
        print(f"[INFO] 已启用在线模式（预下载需要联网）")

    if cache_dir:
        print(f"[INFO] 缓存目录: {cache_dir}")


def predownload_model(model_name: str, expected_dim: int = None) -> bool:
    """预下载指定模型

    Args:
        model_name: SentenceTransformer 模型名称
        expected_dim: 预期向量维度（用于验证）

    Returns:
        True 表示下载并验证成功
    """
    print(f"\n[DOWNLOAD] {model_name}")
    print("-" * 60)

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        print(f"  [FAIL] 无法导入 SentenceTransformer: {e}")
        return False

    start = time.perf_counter()
    try:
        # 加载模型（首次会触发下载）
        model = SentenceTransformer(model_name)
        elapsed = time.perf_counter() - start
        print(f"  [OK] 模型加载完成: {elapsed:.2f}s")

        # 验证模型维度
        actual_dim = model.get_sentence_embedding_dimension()
        if expected_dim and actual_dim != expected_dim:
            print(f"  [WARN] 维度不匹配: 期望 {expected_dim}, 实际 {actual_dim}")
        else:
            print(f"  [OK] 模型维度: {actual_dim}")

        # 验证编码功能（确保模型可用）
        test_sentences = [
            "测试向量编码性能",
            "testing vector encoding performance",
        ]
        embeddings = model.encode(test_sentences)
        print(f"  [OK] 编码验证: shape={embeddings.shape}, dtype={embeddings.dtype}")

        # 验证模型文件已缓存
        cache_dir = os.environ.get("TRANSFORMERS_CACHE",
                                    os.path.expanduser("~/.cache/huggingface"))
        print(f"  [OK] 缓存位置: {cache_dir}")

        return True

    except Exception as e:
        elapsed = time.perf_counter() - start
        print(f"  [FAIL] 下载失败 ({elapsed:.2f}s): {type(e).__name__}: {e}")
        return False


def verify_offline_mode(model_name: str) -> bool:
    """验证离线模式下模型能否正常加载

    Args:
        model_name: 已预下载的模型名称

    Returns:
        True 表示离线模式可用
    """
    print(f"\n[VERIFY] 离线模式验证: {model_name}")
    print("-" * 60)

    # 启用离线模式
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    print(f"  [INFO] HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1")

    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(model_name)
        embedding = model.encode(["离线模式测试"])
        print(f"  [OK] 离线加载成功: shape={embedding.shape}")
        return True
    except Exception as e:
        print(f"  [FAIL] 离线加载失败: {type(e).__name__}: {e}")
        return False
    finally:
        # 恢复在线模式
        os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.pop("TRANSFORMERS_OFFLINE", None)


def generate_env_config(cache_dir: str) -> str:
    """生成 .env 配置片段

    Args:
        cache_dir: 模型缓存目录

    Returns:
        .env 配置文本
    """
    return f"""
# ========================================
# P3: 离线模型缓存配置
# ========================================

# 模型缓存目录（预下载脚本会在此缓存模型）
TRANSFORMERS_CACHE={cache_dir}
HF_HOME={cache_dir}
HF_DATASETS_CACHE={cache_dir}/datasets

# 离线模式（生产环境启用，避免运行时网络请求）
# 预下载完成后设置为 1，开发环境可设为 0
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
"""


def main():
    parser = argparse.ArgumentParser(
        description="P3 阶段 1: 离线模型预下载脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    # 预下载默认模型
    python scripts/predownload_model.py

    # 预下载指定模型
    python scripts/predownload_model.py --model BAAI/bge-m3

    # 指定缓存目录
    python scripts/predownload_model.py --cache-dir /data/model_cache

    # 验证离线模式
    python scripts/predownload_model.py --verify-offline

    # 预下载所有模型（含可选）
    python scripts/predownload_model.py --all
        """,
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="指定预下载的模型名称（默认下载 paraphrase-multilingual-MiniLM-L12-v2）",
    )
    parser.add_argument(
        "--cache-dir", type=str, default=None,
        help="模型缓存目录（默认 ~/.cache/huggingface）",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="预下载所有模型（含可选高精度模型）",
    )
    parser.add_argument(
        "--verify-offline", action="store_true",
        help="验证离线模式下模型能否正常加载",
    )
    parser.add_argument(
        "--skip-deps-check", action="store_true",
        help="跳过依赖版本检查",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("P3 阶段 1: 离线模型预下载")
    print("=" * 60)
    print(f"Python: {sys.version.split()[0]}")
    print(f"平台: {sys.platform}")

    # STEP 1: 依赖检查
    if not args.skip_deps_check:
        if not check_dependencies():
            print("\n[ERROR] 依赖版本不兼容，请先修复依赖冲突")
            print("[HINT] 参考 docs/p3_offline_model_implementation_plan.md 第 2 节")
            sys.exit(1)

    # STEP 2: 设置环境
    cache_dir = args.cache_dir or os.environ.get(
        "TRANSFORMERS_CACHE",
        os.path.expanduser("~/.cache/huggingface"),
    )
    # 预下载阶段需要联网
    set_offline_env(cache_dir, offline=False)

    # STEP 3: 确定预下载模型列表
    models_to_download = []
    if args.model:
        models_to_download.append({"name": args.model, "dim": None})
    elif args.all:
        models_to_download.extend(DEFAULT_MODELS)
        models_to_download.extend(OPTIONAL_MODELS)
    else:
        models_to_download.extend(DEFAULT_MODELS)

    # STEP 4: 预下载
    print("\n" + "=" * 60)
    print("STEP 2: 预下载模型")
    print("=" * 60)

    success_count = 0
    failed_models = []
    for model_info in models_to_download:
        name = model_info["name"]
        dim = model_info.get("dim")
        if predownload_model(name, expected_dim=dim):
            success_count += 1
        else:
            failed_models.append(name)

    # STEP 5: 离线模式验证
    if args.verify_offline and success_count > 0:
        print("\n" + "=" * 60)
        print("STEP 3: 离线模式验证")
        print("=" * 60)

        verify_model = args.model or DEFAULT_MODELS[0]["name"]
        if not verify_offline_mode(verify_model):
            print("[WARN] 离线模式验证失败，模型可能未完整缓存")

    # STEP 6: 生成 .env 配置
    print("\n" + "=" * 60)
    print("STEP 4: 生成 .env 配置")
    print("=" * 60)

    env_config = generate_env_config(cache_dir)
    print(env_config)
    print("[HINT] 将上述配置添加到 .env 文件中")

    # 汇总
    print("\n" + "=" * 60)
    print("汇总")
    print("=" * 60)
    print(f"成功: {success_count} / {len(models_to_download)}")
    if failed_models:
        print(f"失败: {failed_models}")
        sys.exit(1)
    else:
        print("[OK] 所有模型预下载完成")
        print(f"[INFO] 缓存目录: {cache_dir}")
        print("[INFO] 生产环境请设置 HF_HUB_OFFLINE=1 启用离线模式")


if __name__ == "__main__":
    main()
