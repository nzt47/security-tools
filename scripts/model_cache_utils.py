#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型缓存路径解析工具 — 跨平台 + 环境变量优先级.

供所有模型下载脚本复用 (BGE/Jina/其他), 避免路径逻辑重复.

优先级 (不易): 显式覆盖 > HF 约定变量 > 平台默认
跨平台 (变易): 用 pathlib.Path 自动处理 Windows/Linux 路径分隔符

══════════════════════════════════════════════════════════════════════════════
  使用示例 (供其他同事直接参考)
══════════════════════════════════════════════════════════════════════════════

  1. 基本用法 — 获取模型缓存路径::

      from model_cache_utils import get_hf_model_cache_dir, get_modelscope_cache_dir

      # BGE reranker (脚本专用环境变量覆盖默认路径)
      bge_dir = get_hf_model_cache_dir(
          "BAAI/bge-reranker-v2-m3",
          env_override="BGE_V2_M3_LOCAL_DIR",
      )

      # Jina reranker (复用同一函数, 不同 model_id)
      jina_dir = get_hf_model_cache_dir(
          "jinaai/jina-reranker-v2-base-multilingual",
          env_override="JINA_RERANKER_LOCAL_DIR",
      )

      # modelscope 缓存目录
      ms_cache = get_modelscope_cache_dir()

  2. 新建下载脚本模板 — 3 步接入::

      #!/usr/bin/env python3
      "新的模型下载脚本"
      from model_cache_utils import get_hf_model_cache_dir, get_modelscope_cache_dir

      MODEL_ID = "org/your-model-name"
      _ENV_OVERRIDE = "YOUR_MODEL_LOCAL_DIR"  # 脚本专用环境变量

      def main():
          local_dir = get_hf_model_cache_dir(MODEL_ID, env_override=_ENV_OVERRIDE)
          ms_cache = get_modelscope_cache_dir()
          # ... 下载逻辑 ...

  3. 环境变量优先级 (从高到低)::

      ┌─────────────────────────────┬──────────────────────────────────────┐
      │ 环境变量                    │ 说明                                 │
      ├─────────────────────────────┼──────────────────────────────────────┤
      │ BGE_V2_M3_LOCAL_DIR (示例)  │ 脚本专用, 完整路径覆盖 (env_override) │
      │ HF_HOME                     │ HuggingFace 官方约定                 │
      │ HUGGINGFACE_HUB_CACHE       │ HF Hub 缓存 (旧变量, 仍支持)          │
      │ TRANSFORMERS_CACHE          │ transformers 库缓存 (最后备选)       │
      │ (无)                        │ 平台默认路径                         │
      └─────────────────────────────┴──────────────────────────────────────┘

      平台默认:
        - Windows: %LOCALAPPDATA%\\huggingface\\hub
        - Linux:   ~/.cache/huggingface/hub

  4. 自检 (命令行)::

      python scripts/model_cache_utils.py
      # 打印各模型缓存路径, 供调试用

  5. 单元测试::

      pytest tests/unit/test_model_cache_utils.py -v
      # 覆盖所有优先级分支 + 跨平台路径分隔符
"""
import os
import sys
from pathlib import Path


def _model_id_to_subdir(model_id: str) -> str:
    """将 HF model_id 转为缓存子目录名.

    BAAI/bge-reranker-v2-m3 -> models--BAAI--bge-reranker-v2-m3
    (HuggingFace Hub 缓存目录结构约定)
    """
    return "models--" + model_id.replace("/", "--")


def _get_hf_cache_base() -> Path:
    """获取 HF 缓存基础路径 (平台感知, 内部函数).

    Windows: %LOCALAPPDATA%\\huggingface\\hub  (HF 官方 Windows 约定)
    Linux/macOS: ~/.cache/huggingface/hub      (XDG 风格)
    """
    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local_appdata) / "huggingface" / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def get_hf_cache_base(env_override: str = None) -> Path:
    """获取 HuggingFace 缓存基础路径 (不含模型子目录).

    供 huggingface_hub.snapshot_download(cache_dir=...) 使用 —
    huggingface_hub 会自动在此路径下创建 models--xxx 子目录.

    与 get_hf_model_cache_dir() 的优先级一致, 但返回基础路径 (不含 models--xxx).

    Args:
        env_override: 脚本专用环境变量名 (如 "BGE_V2_M3_LOCAL_DIR"),
                     设定时优先读取该变量作为完整路径覆盖

    Returns:
        缓存基础路径的 Path 对象

    优先级 (不易):
        1. env_override 环境变量  — 完整路径覆盖 (测试/定制路径)
        2. HF_HOME               — 返回 HF_HOME/hub (HF 官方约定)
        3. HUGGINGFACE_HUB_CACHE / TRANSFORMERS_CACHE — 直接返回
        4. 平台默认               — Windows: %LOCALAPPDATA%/huggingface/hub; Linux: ~/.cache/huggingface/hub
    """
    # 1. 脚本专用覆盖
    if env_override:
        custom = os.environ.get(env_override)
        if custom:
            return Path(custom).expanduser()

    # 2. HF_HOME (HuggingFace 官方约定, 优先于 HUB_CACHE)
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return Path(hf_home).expanduser() / "hub"

    # 3. HUGGINGFACE_HUB_CACHE / TRANSFORMERS_CACHE (已是缓存路径, 不加 hub/)
    hf_cache = os.environ.get("HUGGINGFACE_HUB_CACHE") or os.environ.get("TRANSFORMERS_CACHE")
    if hf_cache:
        return Path(hf_cache).expanduser()

    # 4. 平台默认缓存路径
    return _get_hf_cache_base()


def get_hf_model_cache_dir(model_id: str, env_override: str = None) -> str:
    """获取 HuggingFace 模型缓存目录, 遵循环境变量优先级 + 跨平台路径.

    Args:
        model_id: HF 模型 ID, 如 "BAAI/bge-reranker-v2-m3"
        env_override: 脚本专用环境变量名 (如 "BGE_V2_M3_LOCAL_DIR"),
                     设定时优先读取该变量作为完整路径覆盖

    Returns:
        模型缓存目录的绝对路径字符串

    优先级 (不易):
        1. env_override 环境变量  — 完整路径覆盖 (测试/定制路径)
        2. HF_HOME               — HuggingFace 官方约定 (优先于 HUB_CACHE)
        3. HUGGINGFACE_HUB_CACHE / TRANSFORMERS_CACHE — HF Hub 缓存
        4. 平台默认               — Windows: %LOCALAPPDATA%; Linux: ~/.cache
    """
    subdir = _model_id_to_subdir(model_id)

    # 1. 脚本专用覆盖
    if env_override:
        custom = os.environ.get(env_override)
        if custom:
            return str(Path(custom).expanduser())

    # 2. HF_HOME (HuggingFace 官方约定, 优先于 HUB_CACHE)
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return str(Path(hf_home).expanduser() / "hub" / subdir)

    # 3. HUGGINGFACE_HUB_CACHE / TRANSFORMERS_CACHE
    hf_cache = os.environ.get("HUGGINGFACE_HUB_CACHE") or os.environ.get("TRANSFORMERS_CACHE")
    if hf_cache:
        return str(Path(hf_cache).expanduser() / subdir)

    # 4. 平台默认缓存路径
    return str(_get_hf_cache_base() / subdir)


def get_modelscope_cache_dir() -> str:
    """获取 modelscope 缓存目录, 遵循环境变量优先级 + 跨平台路径.

    优先级 (不易): MODELSCOPE_CACHE > 平台默认
    """
    # 1. 环境变量覆盖
    custom = os.environ.get("MODELSCOPE_CACHE")
    if custom:
        return str(Path(custom).expanduser())

    # 2. 平台默认
    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return str(Path(local_appdata) / "modelscope")
    return str(Path.home() / ".cache" / "modelscope")


if __name__ == "__main__":
    # 自检: 打印各模型缓存路径 (供调试用)
    print("=== HuggingFace 模型缓存路径 ===")
    for mid in ["BAAI/bge-reranker-v2-m3", "jinaai/jina-reranker-v2-base-multilingual"]:
        print(f"  {mid}:")
        print(f"    {get_hf_model_cache_dir(mid)}")
    print(f"\n=== modelscope 缓存 ===")
    print(f"  {get_modelscope_cache_dir()}")
