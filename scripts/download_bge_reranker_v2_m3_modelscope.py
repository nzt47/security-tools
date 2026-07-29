#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 bge-reranker-v2-m3 模型（modelscope 镜像）

目的:
    解决 huggingface.co 不可达问题，通过 modelscope 镜像下载 bge-reranker-v2-m3。
    v2-m3 是 BAAI 系列 Cross-Encoder 中中文 SOTA（~2.3GB），
    架构为 XLM-RoBERTa-large，比 bge-reranker-base 计算量大，
    CPU 推理 P99 4.6s（需调大 SKILL_RERANKER_RERANK_TIMEOUT=6.0）。

模型对比:
    | 模型 | 大小 | 架构 | CPU P99 | SLO(3s) | 中文支持 | 推荐度 |
    |------|------|------|---------|---------|---------|--------|
    | bge-reranker-v2-m3 | 2.3GB | XLM-RoBERTa-large | 4641ms | ❌ 需调大 | ✅ SOTA | ⭐⭐ 谨慎 |
    | bge-reranker-base | 1.1GB | XLM-RoBERTa-base | 待测 | ? | ✅ 良好 | ⭐⭐⭐ 优先 |
    | jina-reranker-v2 | 280MB | XLM-RoBERTa-large | 7960ms | ❌ | ✅ 良好 | ⭐ 当前（无区分度）|

下载说明:
    - v2-m3 不含预导出 ONNX（与 jina 不同），需额外运行 ONNX 转换脚本
    - 下载耗时较长（~2.3GB，建议网络稳定环境）
    - modelscope 镜像优先，huggingface.co 备选

使用方法:
    python scripts/download_bge_reranker_v2_m3_modelscope.py

    # 自定义下载路径
    BGE_V2_M3_LOCAL_DIR=/path/to/model python scripts/download_bge_reranker_v2_m3_modelscope.py

后续操作:
    1. ONNX 转换（加速 + 降低延迟）:
       python scripts/convert_bge_to_onnx.py  # 待创建，复用 convert_jina_to_onnx.py 模板
    2. 性能压测:
       python scripts/benchmark_v65_bge_base_reranker.py  # 复用，修改模型路径
    3. 若 P99 > 3s，在 .env 中调大:
       SKILL_RERANKER_RERANK_TIMEOUT=6.0
    4. 区分度对比评估:
       python scripts/compare_reranker_discrimination.py --bge <path>
"""
import os
import sys
import time
import shutil
from pathlib import Path


# 模型配置
MODELSCOPE_MODEL_ID = "BAAI/bge-reranker-v2-m3"
HF_MODEL_ID = "BAAI/bge-reranker-v2-m3"

# 默认下载路径（与 HF 缓存结构对齐）
DEFAULT_LOCAL_DIR = os.path.expanduser(
    "~/.cache/huggingface/hub/models--BAAI--bge-reranker-v2-m3"
)

# v2-m3 模型预期文件（用于完整性校验）
EXPECTED_FILES = [
    "config.json",
    "pytorch_model.bin",  # 或 model.safetensors
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
]


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
    """通过 modelscope 镜像下载模型

    【不易】modelscope 镜像优先（huggingface.co 可能不可达）
    【简易】snapshot_download 一次拉取全部文件
    """
    print(f"\n[1/4] 通过 modelscope 下载模型")
    print(f"  模型 ID: {MODELSCOPE_MODEL_ID}")
    print(f"  本地目录: {local_dir}")
    print(f"  预计大小: ~2.3GB（下载耗时较长，请耐心等待）")

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
        print(f"\n[2/4] 复制模型到 HF 兼容目录")
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
        print(f"\n  备选方案：")
        print(f"    1. 直接用 huggingface-cli download {HF_MODEL_ID}")
        print(f"    2. 或用 git lfs clone: git clone https://huggingface.co/{HF_MODEL_ID}")
        return False


def verify_integrity(local_dir: str) -> bool:
    """校验模型文件完整性

    【不易】v2-m3 下载耗时较长，校验完整性避免后续加载失败浪费时间
    """
    print(f"\n[3/4] 校验模型文件完整性")
    local_path = Path(local_dir)

    missing = []
    found = []
    for fname in EXPECTED_FILES:
        fpath = local_path / fname
        if fpath.exists():
            size_mb = fpath.stat().st_size / 1024 / 1024
            found.append(f"{fname} ({size_mb:.1f}MB)")
        else:
            missing.append(fname)

    if found:
        print(f"  ✅ 已找到:")
        for f in found:
            print(f"    - {f}")

    if missing:
        print(f"  ⚠️ 缺失文件:")
        for f in missing:
            print(f"    - {f}")
        # model.safetensors 可替代 pytorch_model.bin
        if "pytorch_model.bin" in missing:
            safetensors = local_path / "model.safetensors"
            if safetensors.exists():
                size_mb = safetensors.stat().st_size / 1024 / 1024
                print(f"  ✅ 找到 model.safetensors ({size_mb:.1f}MB)，可替代 pytorch_model.bin")
                missing.remove("pytorch_model.bin")

    if missing:
        print(f"\n  ❌ 关键文件缺失，建议删除目录后重新下载")
        print(f"     rmdir /s /q {local_dir}")
        return False

    print(f"  ✅ 完整性校验通过")
    return True


def verify_model_loadable(local_dir: str) -> bool:
    """验证模型可加载性

    【不易】用 v2-m3 验证中文区分度（与 jina 的 stddev=0.0 对比）
    """
    print(f"\n[4/4] 验证模型可加载性")
    print(f"  模型路径: {local_dir}")

    try:
        from sentence_transformers import CrossEncoder
        t0 = time.time()
        # v2-m3 是标准 transformers 模型，不需要 trust_remote_code
        model = CrossEncoder(local_dir)
        elapsed = time.time() - t0
        print(f"  ✅ 模型加载成功 ({elapsed:.2f}s)")

        # 中文区分度测试（与黄金集 query 同源）
        pairs = [
            ("请帮我反思刚才的回答", "自我反思技能 帮助用户反思和检查回答质量"),
            ("请帮我反思刚才的回答", "语音交互技能 语音识别和 TTS 合成"),
            ("请帮我反思刚才的回答", "PDF 解析技能 解析和提取 PDF 文件内容"),
        ]
        scores = model.predict(pairs)
        print(f"  排序分数（raw logits）: {[round(float(s), 4) for s in scores]}")

        # 验证区分度：self_reflection 应高于其他两个
        ok = scores[0] > scores[1] and scores[0] > scores[2]
        print(f"  self_reflection 分数最高: {ok} {'✅ 有区分度' if ok else '❌ 无区分度'}")

        if not ok:
            print(f"  ⚠️ v2-m3 也无区分度，需进一步调研（扩大黄金集或换模型）")

        del model
        return True

    except Exception as e:
        print(f"  ❌ 模型加载失败: {type(e).__name__}: {str(e)[:300]}")
        return False


def main() -> int:
    print("=" * 72)
    print("  bge-reranker-v2-m3 模型下载（modelscope 镜像）")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模型大小: ~2.3GB（下载耗时较长）")
    print("=" * 72)

    local_dir = os.environ.get("BGE_V2_M3_LOCAL_DIR", DEFAULT_LOCAL_DIR)

    # 已存在则直接验证
    if Path(local_dir).exists() and any(Path(local_dir).iterdir()):
        print(f"\n⚠️ 目标目录已存在且非空: {local_dir}")
        print("  跳过下载，直接验证...")
        if verify_integrity(local_dir) and verify_model_loadable(local_dir):
            print("\n✅ 模型已就绪")
            _print_next_steps(local_dir)
            return 0
        else:
            print("\n⚠️ 已存在模型验证失败，建议删除后重新下载")
            print(f"  rmdir /s /q \"{local_dir}\"")
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

    # 校验完整性
    if not verify_integrity(local_dir):
        print("\n⚠️ 下载完成但完整性校验失败")
        return 1

    # 验证可加载
    if not verify_model_loadable(local_dir):
        print("\n⚠️ 下载完成但加载验证失败")
        return 1

    print("\n" + "=" * 72)
    print("  ✅ bge-reranker-v2-m3 下载并验证成功")
    print("=" * 72)
    _print_next_steps(local_dir)
    return 0


def _print_next_steps(local_dir: str) -> None:
    """打印后续操作指引"""
    print(f"\n后续操作:")
    print(f"  1. ONNX 转换（加速，v2-m3 不含预导出 ONNX）:")
    print(f"     python scripts/convert_bge_to_onnx.py  # 待创建")
    print(f"  2. 性能压测:")
    print(f"     python scripts/benchmark_v65_bge_base_reranker.py  # 复用，改模型路径")
    print(f"  3. 若 P99 > 3s，在 .env 中调大 timeout:")
    print(f"     SKILL_RERANKER_RERANK_TIMEOUT=6.0")
    print(f"  4. 切换模型（在 .env 中设置）:")
    print(f"     SKILL_RERANKER_MODEL={local_dir}")
    print(f"     SKILL_RERANKER_USE_ONNX=true  # 转换后启用")
    print(f"  5. 区分度对比评估:")
    print(f"     python scripts/compare_reranker_discrimination.py --bge \"{local_dir}\"")
    print(f"  6. 集成方案详见: docs/RERANKER_BGE_V2_M3_INTEGRATION_TODO.md")


if __name__ == "__main__":
    sys.exit(main())
