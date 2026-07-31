"""下载 bge-reranker-base 模型（modelscope 镜像）

目的:
    解决 huggingface.co 不可达问题，通过 modelscope 镜像下载 bge-reranker-base。
    bge-reranker-base 是 BAAI 系列中较小的 Cross-Encoder（~1.1GB），
    架构为 XLM-RoBERTa-base，比 jina-v2（XLM-RoBERTa-large）计算量小，
    是 CPU 环境下满足 500ms SLO 的最后希望。

模型对比:
    | 模型 | 大小 | 架构 | CPU P99 | SLO |
    |------|------|------|---------|-----|
    | bge-reranker-v2-m3 | 2.3GB | - | 4641ms | ❌ |
    | jina-reranker-v2 | 280MB | XLM-RoBERTa-large | 7960ms | ❌ |
    | bge-reranker-base | 1.1GB | XLM-RoBERTa-base | 待测 | ? |

使用方法:
    python scripts/download_bge_reranker_base_modelscope.py
"""
import os
import sys
import time
import shutil
from pathlib import Path


# 模型配置
MODELSCOPE_MODEL_ID = "BAAI/bge-reranker-base"
HF_MODEL_ID = "BAAI/bge-reranker-base"

# 默认下载路径（与 HF 缓存结构对齐）
DEFAULT_LOCAL_DIR = os.path.expanduser(
    "~/.cache/huggingface/hub/models--BAAI--bge-reranker-base"
)


def check_modelscope_installed() -> bool:
    try:
        import modelscope  # noqa: F401
        return True
    except ImportError:
        return False


def install_modelscope() -> bool:
    print("正在安装 modelscope...")
    import subprocess
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "modelscope"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("✅ modelscope 安装成功")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ modelscope 安装失败: {e}")
        return False


def download_via_modelscope(local_dir: str) -> bool:
    print(f"\n[1/3] 通过 modelscope 下载模型")
    print(f"  模型 ID: {MODELSCOPE_MODEL_ID}")
    print(f"  本地目录: {local_dir}")

    try:
        from modelscope import snapshot_download
        t0 = time.time()
        cache_dir = os.environ.get(
            "MODELSCOPE_CACHE",
            os.path.expanduser("~/.cache/modelscope"),
        )
        print(f"  modelscope 缓存: {cache_dir}")

        downloaded_path = snapshot_download(
            model_id=MODELSCOPE_MODEL_ID,
            cache_dir=cache_dir,
            revision="master",
        )
        elapsed = time.time() - t0
        print(f"  ✅ modelscope 下载成功 ({elapsed:.1f}s)")
        print(f"  下载路径: {downloaded_path}")

        # 复制到 HF 兼容目录
        local_path = Path(local_dir)
        local_path.mkdir(parents=True, exist_ok=True)
        src = Path(downloaded_path)
        print(f"\n[2/3] 复制模型到 HF 兼容目录")
        print(f"  源: {src}")
        print(f"  目标: {local_path}")

        for item in src.rglob("*"):
            if item.is_file():
                rel = item.relative_to(src)
                dst = local_path / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dst)

        total_size = sum(f.stat().st_size for f in local_path.rglob("*") if f.is_file())
        print(f"  ✅ 复制完成，总大小: {total_size / 1024 / 1024:.1f}MB")
        return True

    except Exception as e:
        print(f"  ❌ modelscope 下载失败: {type(e).__name__}: {str(e)[:300]}")
        return False


def verify_model_loadable(local_dir: str) -> bool:
    print(f"\n[3/3] 验证模型可加载性")
    print(f"  模型路径: {local_dir}")

    try:
        from sentence_transformers import CrossEncoder
        t0 = time.time()
        # bge-reranker-base 是标准 transformers 模型，不需要 trust_remote_code
        model = CrossEncoder(local_dir)
        elapsed = time.time() - t0
        print(f"  ✅ 模型加载成功 ({elapsed:.2f}s)")

        pairs = [
            ("语音识别", "语音交互助手 语音识别 语音转文字"),
            ("语音识别", "PDF 文件解析器 文档提取"),
        ]
        scores = model.predict(pairs)
        print(f"  排序分数: {[round(float(s), 4) for s in scores]}")
        ok = scores[0] > scores[1]
        print(f"  语音匹配 > 不匹配: {ok} {'✅' if ok else '❌'}")
        del model
        return True

    except Exception as e:
        print(f"  ❌ 模型加载失败: {type(e).__name__}: {str(e)[:300]}")
        return False


def main() -> int:
    print("=" * 60)
    print("  bge-reranker-base 模型下载（modelscope 镜像）")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    local_dir = os.environ.get("BGE_BASE_LOCAL_DIR", DEFAULT_LOCAL_DIR)

    if Path(local_dir).exists() and any(Path(local_dir).iterdir()):
        print(f"\n⚠️ 目标目录已存在且非空: {local_dir}")
        print("  跳过下载，直接验证...")
        if verify_model_loadable(local_dir):
            print("\n✅ 模型已就绪")
            return 0
        else:
            print("\n⚠️ 已存在模型加载失败，建议删除后重新下载")
            return 1

    if not check_modelscope_installed():
        if not install_modelscope():
            print("\n❌ modelscope 安装失败，无法下载")
            return 1

    if not download_via_modelscope(local_dir):
        print("\n❌ 下载失败")
        return 1

    if not verify_model_loadable(local_dir):
        print("\n⚠️ 下载完成但加载验证失败")
        return 1

    print("\n" + "=" * 60)
    print("  ✅ bge-reranker-base 下载并验证成功")
    print("=" * 60)
    print(f"\n下一步操作:")
    print(f"  1. 运行压测脚本验证性能:")
    print(f"     python scripts/benchmark_v65_bge_base_reranker.py")
    print(f"  2. 若 P99 ≤ 500ms，在 .env 中设置:")
    print(f"     SKILL_RERANKER_MODEL={local_dir}")
    print(f"     SKILL_RERANKER_ENABLED=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
