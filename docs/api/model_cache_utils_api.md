# model_cache_utils API 参考

> 模型缓存路径解析工具 — 跨平台 + 环境变量优先级

## 函数签名

### `get_hf_model_cache_dir(model_id, env_override=None) -> str`

返回 **模型特定缓存路径**（含 `models--xxx` 子目录）。

| 参数 | 类型 | 说明 |
|------|------|------|
| `model_id` | `str` | HF 模型 ID，如 `"BAAI/bge-reranker-v2-m3"` |
| `env_override` | `str\|None` | 脚本专用环境变量名（如 `"BGE_V2_M3_LOCAL_DIR"`） |

**返回**: 绝对路径字符串，如 `~/.cache/huggingface/hub/models--BAAI--bge-reranker-v2-m3`

**用途**: 直接定位模型目录（modelscope 下载、文件校验等）

```python
from model_cache_utils import get_hf_model_cache_dir

dir = get_hf_model_cache_dir("BAAI/bge-reranker-v2-m3", env_override="BGE_V2_M3_LOCAL_DIR")
# → ~/.cache/huggingface/hub/models--BAAI--bge-reranker-v2-m3
```

---

### `get_hf_cache_base(env_override=None) -> Path`

返回 **缓存基础路径**（不含 `models--xxx` 子目录）。

| 参数 | 类型 | 说明 |
|------|------|------|
| `env_override` | `str\|None` | 脚本专用环境变量名 |

**返回**: `Path` 对象，如 `~/.cache/huggingface/hub`

**用途**: 供 `huggingface_hub.snapshot_download(cache_dir=...)` 使用（huggingface_hub 会自动在此下创建 `models--xxx` 子目录）

```python
from model_cache_utils import get_hf_cache_base
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
from model_cache_utils import get_modelscope_cache_dir

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

## 降级行为

未设置任何环境变量时，所有函数自动降级到平台默认路径。**同事无需配置任何环境变量即可使用**。

```
无 BGE_V2_M3_LOCAL_DIR → 降级到 HF_HOME → 降级到 HUGGINGFACE_HUB_CACHE → 降级到平台默认
```

## 相关文件

- 源码: `scripts/model_cache_utils.py`
- 测试: `tests/unit/test_model_cache_utils.py`（27 个测试）
- 使用指南: `docs/guides/model_cache_utils_usage.md`（含 5 个示例）
- 使用方: `scripts/download_bge_reranker_v2_m3_modelscope.py`, `scripts/download_reranker.py`
