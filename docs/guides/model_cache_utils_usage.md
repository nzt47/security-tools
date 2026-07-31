# model_cache_utils 使用指南

> 模型缓存路径解析工具 — 跨平台 + 环境变量优先级，供所有模型下载脚本复用。

## 1. 基本用法 — 获取模型缓存路径

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

## 2. 新建下载脚本模板 — 3 步接入

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

## 3. 环境变量优先级（从高到低）

| 环境变量 | 说明 |
|---------|------|
| `BGE_V2_M3_LOCAL_DIR`（示例） | 脚本专用，完整路径覆盖（`env_override` 参数） |
| `HF_HOME` | HuggingFace 官方约定 |
| `HUGGINGFACE_HUB_CACHE` | HF Hub 缓存（旧变量，仍支持） |
| `TRANSFORMERS_CACHE` | transformers 库缓存（最后备选） |
| （无） | 平台默认路径 |

**平台默认路径**：
- Windows: `%LOCALAPPDATA%\huggingface\hub`
- Linux: `~/.cache/huggingface/hub`

## 4. 自检（命令行）

```bash
python scripts/model_cache_utils.py
# 打印各模型缓存路径，供调试用
```

输出示例：
```
=== HuggingFace 模型缓存路径 ===
  BAAI/bge-reranker-v2-m3:
    C:\Users\xxx\AppData\Local\huggingface\hub\models--BAAI--bge-reranker-v2-m3
  jinaai/jina-reranker-v2-base-multilingual:
    C:\Users\xxx\AppData\Local\huggingface\hub\models--jinaai--jina-reranker-v2-base-multilingual

=== modelscope 缓存 ===
  C:\Users\xxx\AppData\Local\modelscope
```

## 5. 单元测试

```bash
pytest tests/unit/test_model_cache_utils.py -v
# 覆盖所有优先级分支 + 跨平台路径分隔符 (18 个测试, ~1 秒)
```

测试覆盖：
- `TestModelIdToSubdir` — model_id 到缓存子目录名转换（3 个测试）
- `TestGetHfModelCacheDir` — 4 级优先级 + 顺序验证 + 通用性（9 个测试）
- `TestGetModelscopeCacheDir` — 默认路径 + 环境变量覆盖（3 个测试）
- `TestCrossPlatform` — 路径分隔符 + 绝对路径验证（3 个测试）

## CI 集成

工具脚本测试已接入 CI（`.github/workflows/test.yml` 的 `code-quality` job）：

```yaml
- name: 工具脚本测试
  run: |
    python -m pytest tests/unit/test_model_cache_utils.py tests/unit/test_check_circular_deps.py -v --tb=short
```

路径解析逻辑退化时 CI 会自动阻断。

## 相关文件

- 源码：`scripts/model_cache_utils.py`
- 测试：`tests/unit/test_model_cache_utils.py`
- 使用方：`scripts/download_bge_reranker_v2_m3_modelscope.py`
- 技术复盘：`docs/reviews/pep562_migration_retrospective.md`（6.2 节）
