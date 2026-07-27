"""下载 jina-reranker-v2-base-multilingual 模型（modelscope 镜像）

目的:
    解决 huggingface.co 不可达（WinError 10060）问题，
    通过 modelscope 镜像下载 jina-reranker-v2-base-multilingual 模型。

模型信息:
    - 名称: jinaai/jina-reranker-v2-base-multilingual
    - 大小: ~280MB
    - 维度: 多语言 Cross-Encoder
    - 中文支持: 良好
    - CPU 推理预期: ~200-400ms（满足 500ms SLO）

使用方法:
    python scripts/download_jina_reranker_modelscope.py

环境变量:
    MODELSCOPE_CACHE: 模型缓存目录（默认 ~/.cache/modelscope）
    JINA_RERANKER_LOCAL_DIR: 下载到本地目录（默认 ~/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual）

注意:
    下载完成后，需将模型转换为 sentence_transformers 兼容格式，
    或在 reranker.py 中支持 modelscope 加载路径。
"""
import os
import sys
import time
import shutil
from pathlib import Path


# 模型配置
MODELSCOPE_MODEL_ID = "jinaai/jina-reranker-v2-base-multilingual"
HF_MODEL_ID = "jinaai/jina-reranker-v2-base-multilingual"

# 默认下载路径（与 HF 缓存结构对齐，便于 sentence_transformers 复用）
DEFAULT_LOCAL_DIR = os.path.expanduser(
    "~/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual"
)


def check_modelscope_installed() -> bool:
    """检查 modelscope 是否已安装"""
    try:
        import modelscope  # noqa: F401
        return True
    except ImportError:
        return False


def install_modelscope() -> bool:
    """安装 modelscope"""
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
    """通过 modelscope 下载模型"""
    print(f"\n[1/3] 通过 modelscope 下载模型")
    print(f"  模型 ID: {MODELSCOPE_MODEL_ID}")
    print(f"  本地目录: {local_dir}")

    try:
        from modelscope import snapshot_download
        t0 = time.time()
        # download_modelscope 会下载到 MODELSCOPE_CACHE，再复制到 local_dir
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

        # 计算总大小
        total_size = sum(f.stat().st_size for f in local_path.rglob("*") if f.is_file())
        print(f"  ✅ 复制完成，总大小: {total_size / 1024 / 1024:.1f}MB")

        return True

    except Exception as e:
        print(f"  ❌ modelscope 下载失败: {type(e).__name__}: {str(e)[:300]}")
        return False


def verify_model_loadable(local_dir: str) -> bool:
    """验证模型可被 sentence_transformers 加载"""
    print(f"\n[3/3] 验证模型可加载性")
    print(f"  模型路径: {local_dir}")

    try:
        from sentence_transformers import CrossEncoder
        t0 = time.time()
        model = CrossEncoder(local_dir)
        elapsed = time.time() - t0
        print(f"  ✅ 模型加载成功 ({elapsed:.2f}s)")

        # 排序验证
        pairs = [
            ("语音识别", "语音交互助手 语音识别 语音转文字"),
            ("语音识别", "PDF 文件解析器 文档提取"),
        ]
        scores = model.predict(pairs)
        print(f"  排序分数: {[round(float(s), 4) for s in scores]}")
        print(f"  语音匹配 > 不匹配: {scores[0] > scores[1]} ✅" if scores[0] > scores[1] else f"  语音匹配 > 不匹配: {scores[0] > scores[1]} ❌")

        del model
        return True

    except Exception as e:
        print(f"  ❌ 模型加载失败: {type(e).__name__}: {str(e)[:300]}")
        return False


def main() -> int:
    print("=" * 60)
    print("  jina-reranker-v2 模型下载（modelscope 镜像）")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    local_dir = os.environ.get("JINA_RERANKER_LOCAL_DIR", DEFAULT_LOCAL_DIR)

    # 检查是否已下载
    if Path(local_dir).exists() and any(Path(local_dir).iterdir()):
        print(f"\n⚠️ 目标目录已存在且非空: {local_dir}")
        print("  跳过下载，直接验证...")
        if verify_model_loadable(local_dir):
            print("\n✅ 模型已就绪，可直接配置 SKILL_RERANKER_MODEL 使用")
            return 0
        else:
            print("\n⚠️ 已存在模型加载失败，建议删除后重新下载")
            return 1

    # 检查 modelscope
    if not check_modelscope_installed():
        if not install_modelscope():
            print("\n❌ modelscope 安装失败，无法下载")
            return 1

    # 下载
    if not download_via_modelscope(local_dir):
        print("\n❌ 下载失败")
        return 1

    # 验证
    if not verify_model_loadable(local_dir):
        print("\n⚠️ 下载完成但加载验证失败")
        print("  可能原因: 模型格式不兼容 sentence_transformers")
        print("  建议: 检查模型文件完整性或使用 modelscope 直接加载")
        return 1

    print("\n" + "=" * 60)
    print("  ✅ jina-reranker-v2 下载并验证成功")
    print("=" * 60)
    print(f"\n下一步操作:")
    print(f"  1. 在 .env 中设置:")
    print(f"     SKILL_RERANKER_MODEL={local_dir}")
    print(f"     SKILL_RERANKER_ENABLED=true")
    print(f"  2. 运行压测脚本验证性能:")
    print(f"     python scripts/benchmark_v65_reranker.py")
    print(f"  3. 预期: CPU 推理 ~300ms，满足 500ms SLO")

    return 0


if __name__ == "__main__":
    sys.exit(main())
