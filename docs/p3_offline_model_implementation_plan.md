# P3 方案（离线模型下载）依赖冲突分析与实施计划

> 分支: `feature/tlm-step3-vectorstore-sqlite-vec`
> 日期: 2026-07-21
> 关联文档: [p2_p4_warmup_heapq_comparison_report.md](./p2_p4_warmup_heapq_comparison_report.md)
> 关联问题: Windows chromadb 路径问题（hnswlib `NotADirectoryError [WinError 267]`）

## 1. 背景与目标

### 1.1 问题背景

`test_search_performance` 在 chromadb 路径下的首次搜索耗时 ~3.2s，瓶颈在 `SentenceTransformer.encode()` 触发的模型推理。P2 预热缓存只能优化重复查询，无法降低首次搜索耗时。P3 方案旨在通过离线预下载模型，消除网络请求延迟，并结合 Windows 兼容性修复，进一步降低首搜耗时。

### 1.2 P3 目标

1. 离线预下载 SentenceTransformer 模型，避免运行时网络请求
2. 解决 Windows chromadb 路径问题（hnswlib `NotADirectoryError`）
3. 降低首次搜索耗时（目标：3.2s → < 1s）

## 2. 依赖冲突分析

### 2.1 核心依赖链

```
sentence-transformers
├── transformers (>=4.34.0)
│   ├── huggingface-hub (>=0.19.3)
│   ├── tokenizers (>=0.14.1)
│   ├── pyyaml
│   ├── regex
│   ├── numpy
│   └── safetensors
├── torch (>=1.11.0)
│   ├── filelock
│   ├── typing-extensions
│   ├── sympy
│   └── networkx
└── scikit-learn (用于 TSDAE 等无监督方法)
    ├── scipy
    └── joblib
```

### 2.2 已识别的依赖冲突

| 冲突类型 | 涉及包 | 版本要求 | 影响 | 解决方案 |
|---------|--------|---------|------|---------|
| **版本下限冲突** | `huggingface-hub` | `transformers>=4.34.0` 要求 `>=0.19.3`，但 `sentence-transformers` 新版要求 `>=0.20.0` | 模型下载失败 | 锁定 `huggingface-hub>=0.20.0` |
| **numpy ABI 冲突** | `numpy` | `torch` 编译时绑定的 numpy ABI 与 `numpy>=2.0` 不兼容 | 运行时 `ImportError: numpy.core.multiarray failed to import` | 锁定 `numpy<2.0` 或使用 `numpy>=1.24,<2.0` |
| **tokenizers 版本** | `tokenizers` | `transformers` 要求 `>=0.14.1`，但旧版 `tokenizers` 与 Rust 工具链不兼容 | 分词器加载失败 | 升级 `tokenizers>=0.15.0` |
| **chromadb 版本** | `chromadb` | 0.4.x 的 hnswlib 在 Windows 有路径 bug；0.5.x 修复但 API 变更 | Windows `NotADirectoryError` | 升级 `chromadb>=0.5.0` 并适配 API |
| **onnxruntime 冲突** | `onnxruntime` | chromadb 0.4.x 依赖 `onnxruntime<=1.16`，与 Python 3.12 不兼容 | CI Linux SIGILL (exit 132) | 升级 `chromadb>=0.5.0` 或禁用 onnxruntime |
| **torch + Python 3.12** | `torch` | `torch<2.2` 不支持 Python 3.12 | 导入失败 | 锁定 `torch>=2.2.0` |

### 2.3 Windows 特定冲突

| 冲突 | 原因 | 影响 |
|------|------|------|
| **hnswlib 路径问题** | chromadb 0.4.x 的 hnswlib 在 Windows 临时目录创建 `data_level0.bin` 时路径处理有 bug | `NotADirectoryError [WinError 267]` |
| **NTFS 性能问题** | Windows NTFS 对 43943 次 `nt.stat` 调用性能差 | transformers 导入慢 4.7s |
| **路径长度限制** | Windows 默认 MAX_PATH=260，长路径触发错误 | 模型缓存路径可能超限 |

## 3. Windows 兼容性问题修复方案

### 3.1 方案 A：升级 chromadb（推荐）

```bash
# 升级到 chromadb 0.5.x，修复 hnswlib Windows 路径问题
pip install "chromadb>=0.5.0,<0.6.0"
```

**API 适配**：
```python
# chromadb 0.4.x
client = chromadb.PersistentClient(path=..., settings=Settings(...))

# chromadb 0.5.x（API 兼容，但 Settings 参数有变化）
client = chromadb.PersistentClient(path=..., settings=Settings(...))
# 验证：get_or_create_collection 接口不变
```

**风险**：
- `chromadb 0.5.x` 的 `chromadb.telemetry` 默认开启，需显式关闭
- `hnswlib` 版本升级可能改变索引格式（需重建集合）

### 3.2 方案 B：使用持久化目录替代临时目录

```python
# 问题代码：Windows 临时目录触发 NotADirectoryError
with tempfile.TemporaryDirectory() as tmpdir:
    store = VectorStore(persist_dir=tmpdir, ...)

# 修复代码：使用项目内持久化目录
import os
PERSIST_DIR = os.path.join(os.getcwd(), "data", "chromadb_test")
os.makedirs(PERSIST_DIR, exist_ok=True)
store = VectorStore(persist_dir=PERSIST_DIR, ...)
```

**适用场景**：测试代码（生产环境本来就用持久化目录）

### 3.3 方案 C：禁用 chromadb 走 JSON fallback（已实施）

当前 P2 压测已采用此方案（`mock.patch.object(vs_module, 'HAS_CHROMA', False)`），但无法验证 chromadb 路径的真实性能。

## 4. P3 实施计划

### 4.1 阶段划分

| 阶段 | 内容 | 前置条件 | 预期效果 |
|------|------|---------|---------|
| **阶段 1** | 离线模型预下载 + 缓存 | 无 | 消除运行时网络请求 |
| **阶段 2** | Windows chromadb 兼容性修复 | 阶段 1 | 解决 NotADirectoryError |
| **阶段 3** | 模型预热 + 首搜优化 | 阶段 1 + 2 | 首搜 3.2s → < 1s |
| **阶段 4** | Linux 生产环境验证 | 阶段 1-3 | 端到端验证 |

### 4.2 阶段 1：离线模型预下载

#### 4.2.1 模型选型

| 模型 | 参数量 | 维度 | 大小 | 适用场景 |
|------|--------|------|------|---------|
| `paraphrase-multilingual-MiniLM-L12-v2`（当前默认） | 117M | 384 | ~470MB | 多语言，平衡精度与速度 |
| `paraphrase-MiniLM-L3-v2`（P1 尝试失败） | 23M | 384 | ~120MB | 轻量级，但未本地缓存 |
| `BAAI/bge-m3`（本地可用） | 568M | 1024 | ~2.2GB | 高精度，多语言 |

**决策**：继续使用 `paraphrase-multilingual-MiniLM-L12-v2`（已本地缓存，无需下载）

#### 4.2.2 预下载脚本

```bash
# scripts/predownload_model.py
"""预下载 SentenceTransformer 模型到本地缓存"""
import os
from sentence_transformers import SentenceTransformer

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
CACHE_DIR = os.environ.get("TRANSFORMERS_CACHE", os.path.expanduser("~/.cache/huggingface"))

print(f"预下载模型: {MODEL_NAME}")
print(f"缓存目录: {CACHE_DIR}")

model = SentenceTransformer(MODEL_NAME)
dim = model.get_sentence_embedding_dimension()
print(f"模型维度: {dim}")
print(f"预下载完成")

# 验证编码功能
embedding = model.encode(["测试句子"])
print(f"编码验证: shape={embedding.shape}, dtype={embedding.dtype}")
```

#### 4.2.3 环境变量配置

```bash
# .env 新增配置
# P3: 离线模型缓存
TRANSFORMERS_CACHE=${HOME}/.cache/huggingface
HF_HOME=${HOME}/.cache/huggingface
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

### 4.3 阶段 2：Windows chromadb 兼容性修复

#### 4.3.1 升级 chromadb

```bash
pip install "chromadb>=0.5.0,<0.6.0" --upgrade
```

#### 4.3.2 验证 hnswlib 修复

```python
# tests/performance/test_chromadb_windows_compat.py
"""验证 chromadb 0.5.x 在 Windows 临时目录下不再触发 NotADirectoryError"""
import tempfile
import pytest
import chromadb
from chromadb.config import Settings


def test_chromadb_tempdir_no_error():
    """验证 Windows 临时目录下 chromadb 正常工作"""
    with tempfile.TemporaryDirectory() as tmpdir:
        client = chromadb.PersistentClient(
            path=tmpdir,
            settings=Settings(anonymized_telemetry=False)
        )
        collection = client.get_or_create_collection(name="compat_test")
        collection.add(
            documents=["test doc 1", "test doc 2"],
            ids=["1", "2"]
        )
        results = collection.query(query_texts=["test"], n_results=1)
        assert len(results["ids"][0]) == 1
```

#### 4.3.3 测试代码适配

修改 `tests/performance/test_vector_store_performance.py`，移除 chromadb mock：

```python
# 修改前：强制禁用 chromadb
with mock.patch.object(vs_module, 'HAS_CHROMA', False):
    ...

# 修改后：恢复 chromadb 路径（阶段 2 验证通过后）
with tempfile.TemporaryDirectory() as tmpdir:
    store = VectorStore(persist_dir=tmpdir, ...)
    # chromadb 0.5.x 应正常工作
```

### 4.4 阶段 3：模型预热 + 首搜优化

#### 4.4.1 应用启动时预热

```python
# memory/vector_store/vector_store.py 新增
class VectorStore:
    def warmup_encoder(self, sample_texts: list = None):
        """预热 encoder，避免首次搜索的模型加载延迟

        Args:
            sample_texts: 预热用的样本文本，默认使用内置样本
        """
        if self._encoder is None:
            return  # JSON fallback 模式无需预热

        if sample_texts is None:
            sample_texts = [
                "预热样本：测试向量编码性能",
                "warmup sample: testing vector encoding performance",
            ]

        start = time.perf_counter()
        self._encoder.encode(sample_texts)
        elapsed = (time.perf_counter() - start) * 1000
        logger.info(f"encoder 预热完成: {elapsed:.2f}ms ({len(sample_texts)} 样本)")
```

#### 4.4.2 应用集成

```python
# app_server.py 或 agent 启动流程
from memory.vector_store import VectorStore

def initialize_memory():
    store = VectorStore(collection_name="agent_memory")
    # P3: 启动时预热 encoder
    if store._backend in ("chromadb", "sqlite_vec"):
        store.warmup_encoder()
    return store
```

### 4.5 阶段 4：Linux 生产环境验证

使用 [scripts/verify_chromadb_p2_linux.sh](../scripts/verify_chromadb_p2_linux.sh) 脚本进行端到端验证。

## 5. 风险与回滚

### 5.1 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| chromadb 0.5.x API 不兼容 | 中 | 高 | 先在测试环境验证，准备 API 适配补丁 |
| 模型缓存丢失 | 低 | 中 | 预下载脚本 + CI 缓存 |
| onnxruntime SIGILL（CI Linux） | 中 | 高 | 禁用 onnxruntime 或使用 chromadb 0.5.x 的纯 Python 后端 |
| numpy ABI 冲突 | 低 | 高 | 锁定 `numpy<2.0` |

### 5.2 回滚方案

```bash
# 回滚 P3 阶段 2（chromadb 升级）
pip install "chromadb==0.4.24"

# 回滚 P3 阶段 1（模型预下载）
# 删除本地缓存
rm -rf ~/.cache/huggingface/hub/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2

# 回滚代码
git revert <P3_commit_hash>
```

## 6. 实施时间表

| 阶段 | 任务 | 依赖 | 产出 |
|------|------|------|------|
| 阶段 1.1 | 编写预下载脚本 | 无 | `scripts/predownload_model.py` |
| 阶段 1.2 | 配置 .env 环境变量 | 阶段 1.1 | `.env` 更新 |
| 阶段 1.3 | CI 集成模型缓存 | 阶段 1.1 | `.github/workflows/ci.yml` 更新 |
| 阶段 2.1 | 升级 chromadb 到 0.5.x | 无 | `pip install` 验证 |
| 阶段 2.2 | 编写 Windows 兼容性测试 | 阶段 2.1 | `tests/performance/test_chromadb_windows_compat.py` |
| 阶段 2.3 | 移除测试中的 chromadb mock | 阶段 2.2 | `test_vector_store_performance.py` 更新 |
| 阶段 3.1 | 实现 `warmup_encoder` 方法 | 阶段 1 | `vector_store.py` 更新 |
| 阶段 3.2 | 应用启动时集成预热 | 阶段 3.1 | `app_server.py` 更新 |
| 阶段 4.1 | Linux 端到端验证 | 阶段 1-3 | 验证报告 |
| 阶段 4.2 | 性能数据对比 | 阶段 4.1 | 性能对比报告 |

## 7. 验收标准

| 指标 | 目标 | 验证方式 |
|------|------|---------|
| Windows chromadb 路径问题 | 不再触发 `NotADirectoryError` | `test_chromadb_windows_compat.py` 通过 |
| 首次搜索耗时 | < 1s（chromadb 路径） | `test_search_performance` 输出 |
| 模型离线加载 | 无网络请求 | `HF_HUB_OFFLINE=1` 下正常工作 |
| 回归测试 | 240+ 测试全过 | `pytest tests/` 全过 |
| Linux 生产环境 | P2 预热后 100 次搜索 < 200ms | `verify_chromadb_p2_linux.sh` 报告 |

## 8. 附录

### 8.1 相关文档

- [P2/P4 对比报告](./p2_p4_warmup_heapq_comparison_report.md)
- [18.5s 瓶颈排查报告](./test_generate_weekly_report_bottleneck_report.md)
- [测试优化回滚备注](./test_optimization_rollback_notes.md)

### 8.2 参考链接

- [chromadb 0.5.x Release Notes](https://github.com/chroma-core/chroma/releases)
- [sentence-transformers 文档](https://www.sbert.net/)
- [HuggingFace 离线模式](https://huggingface.co/docs/transformers/main/en/installation#offline-mode)
