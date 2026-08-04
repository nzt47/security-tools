# model_cache_utils — 模型缓存路径解析工具

> 跨平台 + 环境变量优先级，供所有模型下载脚本复用，避免路径逻辑重复。

## 概述

`model_cache_utils.py` 提供统一的模型缓存路径解析，支持 4 级环境变量优先级和跨平台路径（Windows/Linux/macOS）。所有模型下载脚本（BGE/Jina/其他）复用此工具，确保路径行为一致。

---

## 快速上手

### 1. 基本用法 — 获取模型缓存路径

```python
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
```

### 2. 新建下载脚本模板 — 3 步接入

```python
#!/usr/bin/env python3
"""新的模型下载脚本"""
from model_cache_utils import get_hf_model_cache_dir, get_modelscope_cache_dir

MODEL_ID = "org/your-model-name"
_ENV_OVERRIDE = "YOUR_MODEL_LOCAL_DIR"  # 脚本专用环境变量

def main():
    local_dir = get_hf_model_cache_dir(MODEL_ID, env_override=_ENV_OVERRIDE)
    ms_cache = get_modelscope_cache_dir()
    # ... 下载逻辑 ...
```

### 3. 自检（命令行）

```bash
python scripts/model_cache_utils.py
# 打印各模型缓存路径，供调试用
```

---

## API 参考

### `get_hf_model_cache_dir(model_id, env_override=None) -> str`

返回 **模型特定缓存路径**（含 `models--xxx` 子目录）。

| 参数 | 类型 | 说明 |
|------|------|------|
| `model_id` | `str` | HF 模型 ID，如 `"BAAI/bge-reranker-v2-m3"` |
| `env_override` | `str\|None` | 脚本专用环境变量名（如 `"BGE_V2_M3_LOCAL_DIR"`） |

**返回**: 绝对路径字符串

**用途**: 直接定位模型目录（modelscope 下载、文件校验等）

```python
dir = get_hf_model_cache_dir("BAAI/bge-reranker-v2-m3", env_override="BGE_V2_M3_LOCAL_DIR")
# → ~/.cache/huggingface/hub/models--BAAI--bge-reranker-v2-m3
```

---

### `get_hf_cache_base(env_override=None) -> Path`

返回 **缓存基础路径**（不含 `models--xxx` 子目录）。

| 参数 | 类型 | 说明 |
|------|------|------|
| `env_override` | `str\|None` | 脚本专用环境变量名 |

**返回**: `Path` 对象

**用途**: 供 `huggingface_hub.snapshot_download(cache_dir=...)` 使用（huggingface_hub 会自动在此下创建 `models--xxx` 子目录）

```python
from huggingface_hub import snapshot_download

path = snapshot_download(
    repo_id="BAAI/bge-reranker-v2-m3",
    cache_dir=str(get_hf_cache_base(env_override="BGE_V2_M3_LOCAL_DIR")),
)
```

---

### `get_modelscope_cache_dir() -> str`

返回 modelscope 缓存目录。

**用途**: modelscope 下载脚本的 `cache_dir` 参数

```python
ms_cache = get_modelscope_cache_dir()
# → ~/.cache/modelscope
```

---

## 环境变量优先级（从高到低）

所有 HF 相关函数共用此优先级：

| 优先级 | 环境变量 | 说明 | `get_hf_model_cache_dir` 返回 | `get_hf_cache_base` 返回 |
|--------|---------|------|------------------------------|------------------------|
| 1 | `env_override`（如 `BGE_V2_M3_LOCAL_DIR`） | 脚本专用，完整路径覆盖 | 直接返回该路径 | 直接返回该路径 |
| 2 | `HF_HOME` | HuggingFace 官方约定 | `HF_HOME/hub/models--xxx` | `HF_HOME/hub` |
| 3 | `HUGGINGFACE_HUB_CACHE` | HF Hub 缓存 | `HUB_CACHE/models--xxx` | `HUB_CACHE`（直接返回） |
| 3 | `TRANSFORMERS_CACHE` | transformers 库缓存（后备） | `TRANSFORMERS_CACHE/models--xxx` | `TRANSFORMERS_CACHE`（直接返回） |
| 4 | （无） | 平台默认 | `~/.cache/huggingface/hub/models--xxx` | `~/.cache/huggingface/hub` |

**平台默认路径**：
- Windows: `%LOCALAPPDATA%\huggingface\hub`
- Linux/macOS: `~/.cache/huggingface/hub`

---

## 降级行为

未设置任何环境变量时，所有函数自动降级到平台默认路径。**同事无需配置任何环境变量即可使用**。

```
无 BGE_V2_M3_LOCAL_DIR → 降级到 HF_HOME → 降级到 HUGGINGFACE_HUB_CACHE → 降级到平台默认
```

验证结果（无环境变量时）：
```
get_hf_cache_base()           → C:\Users\xxx\AppData\Local\huggingface\hub
get_hf_model_cache_dir(...)   → ...\hub\models--BAAI--bge-reranker-v2-m3
```

---

## 单元测试

```bash
pytest tests/unit/test_model_cache_utils.py -v
# 27 个测试, ~1 秒
```

测试覆盖：
- `TestModelIdToSubdir` — model_id 到缓存子目录名转换（3 个测试）
- `TestGetHfModelCacheDir` — 4 级优先级 + 顺序验证 + 通用性（9 个测试）
- `TestGetHfCacheBase` — 基础路径 + 优先级 + Path 返回类型（9 个测试）
- `TestGetModelscopeCacheDir` — 默认路径 + 环境变量覆盖（3 个测试）
- `TestCrossPlatform` — 路径分隔符 + 绝对路径验证（3 个测试）

---

## CI 集成

工具脚本测试已接入 CI（`.github/workflows/test.yml` 的 `code-quality` job）：

```yaml
- name: 工具脚本测试
  run: |
    python -m pytest tests/unit/test_model_cache_utils.py tests/unit/test_check_circular_deps.py -v --tb=short
```

路径解析逻辑退化时 CI 会自动阻断（已验证：注入退化 → exit 1 → 恢复 → exit 0）。

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `scripts/model_cache_utils.py` | 源码 |
| `tests/unit/test_model_cache_utils.py` | 单元测试（27 个） |
| `scripts/download_bge_reranker_v2_m3_modelscope.py` | 已迁移（modelscope 下载） |
| `scripts/download_reranker.py` | 已迁移（huggingface_hub 下载） |
| `docs/reviews/pep562_migration_retrospective.md` | 技术复盘（6.2 节） |
