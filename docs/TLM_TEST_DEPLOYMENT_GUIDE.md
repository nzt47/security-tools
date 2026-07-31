# TLM 测试部署指南

> **适用场景**：LongTermMemory (TLM) 模块的测试环境搭建、跨平台测试执行、CI/CD 集成
>
> **核心问题**：Windows 环境下 torch 加载导致 0xC0000005 崩溃，无法运行涉及 embedding 模型的测试
>
> **解决方案**：分层测试策略 — Windows 本地运行不涉及模型的测试 + Linux Docker 运行完整测试

---

## 目录

1. [分层测试策略](#1-分层测试策略)
2. [Windows 本地测试（L1/L2 层）](#2-windows-本地测试l1l2-层)
3. [Linux Docker 测试（L3 层）](#3-linux-docker-测试l3-层)
4. [预下载模型方案](#4-预下载模型方案)
5. [Mock 注入逻辑详解](#5-mock-注入逻辑详解)
6. [Docker 构建优化](#6-docker-构建优化)
7. [常见问题排查](#7-常见问题排查)
8. [文件清单](#8-文件清单)

---

## 1. 分层测试策略

```
┌────────────────────────────────────────────────────────────────┐
│                     分层测试架构                                │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  L1 层：Windows 本地（无需 Docker）                             │
│  ├─ test_long_term_memory_embedding.py   (50 测试)            │
│  ├─ test_tlm_memory_store.py             (22 测试)            │
│  ├─ test_memory_storage_boundary.py      (25 测试)            │
│  ├─ test_memory_refactor.py              (85 测试)            │
│  └─ 小计：182 测试，2-3 秒完成                                 │
│                                                                │
│  L2 层：Windows 本地 + Mock（无需 Docker）                      │
│  ├─ test_memory_vector_store.py           (6 测试)            │
│  └─ 使用 run_vector_store_tests_windows.py 脚本运行            │
│                                                                │
│  L3 层：Linux Docker（完整环境）                                │
│  ├─ test_vector_store_sqlite_vec.py       (sqlite-vec 后端)   │
│  ├─ test_long_term_memory_embedding.py   (KNN 路径)           │
│  └─ 所有涉及 torch/sentence_transformers 的测试                │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

| 层级 | 环境 | 依赖 | 测试范围 | 命令 |
|------|------|------|---------|------|
| L1 | Windows 本地 | 无额外依赖 | LongTermMemory + BLOB + 维度推断 | `pytest tests/unit/test_long_term_memory_embedding.py` |
| L2 | Windows + Mock | mock 脚本 | VectorStore JSON fallback | `python scripts/run_vector_store_tests_windows.py` |
| L3 | Linux Docker | Docker Desktop | 完整测试（含 torch + sqlite-vec） | `docker-compose -f docker-compose.linux-test.yml run --rm test-sqlite-vec` |

---

## 2. Windows 本地测试（L1/L2 层）

### 2.1 L1 层：不涉及模型的测试

直接运行，无需任何特殊配置：

```bash
# 运行所有不涉及模型的测试
python -m pytest tests/unit/test_long_term_memory_embedding.py \
                 tests/unit/test_tlm_memory_store.py \
                 tests/unit/test_memory_storage_boundary.py \
                 tests/unit/test_memory_refactor.py \
                 -v --tb=short
```

**预期结果**：182 passed，2-3 秒完成

### 2.2 L2 层：Mock 模式运行 VectorStore 测试

Windows 上直接运行 `test_memory_vector_store.py` 会触发 0xC0000005 崩溃。使用 mock 脚本绕过：

```bash
# 基本用法
python scripts/run_vector_store_tests_windows.py

# 详细输出
python scripts/run_vector_store_tests_windows.py --verbose

# 跳过 mock（仅在 Linux 或确认不会崩溃时使用）
python scripts/run_vector_store_tests_windows.py --skip-mock
```

**预期结果**：6 passed，2 秒完成，0 崩溃

### 2.3 Mock 脚本原理

```
┌─────────────────────────────────────────────────────────────────┐
│                     Mock 注入流程                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. pytest 启动前                                                │
│     └─ run_vector_store_tests_windows.py 注入 mock 到 sys.modules │
│        ├─ torch (含子模块 torch.nn, torch.cuda, torch.functional)│
│        ├─ sentence_transformers (SentenceTransformer 抛 ImportError)│
│        ├─ transformers                                           │
│        ├─ chromadb + chromadb.config                             │
│        ├─ onnxruntime / faiss / hnswlib                          │
│        └─ 共 11 个 mock 模块                                     │
│                                                                 │
│  2. VectorStore.__init__ 调用 _check_chroma_available()          │
│     └─ import sentence_transformers → 返回 mock                 │
│     └─ SentenceTransformer() → 抛 ImportError                   │
│     └─ HAS_SENTENCE_TRANSFORMERS = False                        │
│                                                                 │
│  3. test fixture _disable_sqlite_vec_for_legacy_tests 运行       │
│     └─ patch.dict(sys.modules, {'sqlite_vec': None})            │
│     └─ VectorStore 使用 JSON fallback 路径                       │
│                                                                 │
│  4. 测试执行                                                     │
│     └─ 验证 JSON fallback 逻辑，不触发模型加载                    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**双层防护**：
- **第一层**：mock 脚本（防崩溃）— 阻止 torch/sentence_transformers 加载
- **第二层**：test fixture（控制行为）— 禁用 sqlite_vec，使用 JSON fallback

---

## 3. Linux Docker 测试（L3 层）

### 3.1 前置条件

- Docker Desktop 已安装并运行（Linux 引擎模式）
- 项目根目录有 `Dockerfile.linux-test` 和 `docker-compose.linux-test.yml`

### 3.2 构建测试镜像

```bash
# 构建镜像（首次约 8-10 分钟，使用清华镜像源加速）
docker-compose -f docker-compose.linux-test.yml build test-sqlite-vec

# 验证镜像构建成功
docker images | grep agent-test-sqlite-vec
# 预期输出：agent-test-sqlite-vec:latest  ~3.6GB
```

### 3.3 运行测试

```bash
# 方式1：通过 docker-compose 运行（推荐）
docker-compose -f docker-compose.linux-test.yml run --rm test-sqlite-vec

# 方式2：直接 docker run（需要挂载 tests 目录）
docker run --rm -w /app \
  -v "${PWD}/tests:/app/tests" \
  -v "${PWD}/scripts:/app/scripts" \
  --entrypoint bash \
  agent-test-sqlite-vec:latest \
  -c "pip install pytest-timeout -q && python -m pytest tests/unit/test_long_term_memory_embedding.py -v --tb=short"

# 方式3：运行特定测试文件
docker run --rm -w /app \
  -v "${PWD}/tests:/app/tests" \
  --entrypoint python \
  agent-test-sqlite-vec:latest \
  -m pytest tests/unit/test_vector_store_sqlite_vec.py -v --tb=short
```

**注意**：`.dockerignore` 排除了 `tests/` 目录，必须通过 `-v` 挂载 tests 目录。

### 3.4 预下载模型（可选）

```bash
# 预下载模型到缓存卷（首次需要 5-10 分钟）
docker-compose -f docker-compose.linux-test.yml run --rm predownload-models

# 之后运行测试时自动使用缓存
docker-compose -f docker-compose.linux-test.yml run --rm test-sqlite-vec
```

### 3.5 Docker 镜像内容

```
agent-test-sqlite-vec:latest (~3.6 GB)
├── Python 3.12-slim (Debian trixie)
├── 系统依赖：gcc, g++, git, curl, sqlite3
├── Python 依赖：
│   ├── torch 2.12.0+cu130 (~2 GB)
│   ├── sentence-transformers
│   ├── transformers
│   ├── chromadb
│   ├── sqlite-vec 0.1.9
│   └── 项目所有依赖
├── 预下载模型（如果网络可用）：
│   ├── paraphrase-multilingual-MiniLM-L12-v2
│   ├── all-MiniLM-L6-v2
│   └── BAAI/bge-small-zh-v1.5
└── 项目代码（pip install -e .）
```

---

## 4. 预下载模型方案

### 4.1 为什么需要预下载

| 场景 | 无预下载 | 有预下载 |
|------|---------|---------|
| Docker 容器网络不通 | ❌ 测试超时失败 | ✅ 直接从缓存加载 |
| 首次运行测试 | ❌ 下载 5-10 分钟 | ✅ 0 秒（已在镜像中） |
| CI/CD 流水线 | ❌ 每次下载 | ✅ 镜像内置 |

### 4.2 预下载脚本

**文件**：`scripts/predownload_models.py`

```bash
# 在 Docker 构建时自动执行（已配置在 Dockerfile 中）
RUN python scripts/predownload_models.py || echo "[WARN] 模型预下载失败"

# 本地手动预下载
python scripts/predownload_models.py

# 指定模型列表
python scripts/predownload_models.py --models all-MiniLM-L6-v2 BAAI/bge-small-zh-v1.5

# 查看已缓存模型
python scripts/predownload_models.py --list
```

### 4.3 预下载的模型列表

| 模型名 | 维度 | 大小 | 用途 |
|--------|------|------|------|
| `paraphrase-multilingual-MiniLM-L12-v2` | 384 | ~470MB | vector_store.py 默认模型 |
| `all-MiniLM-L6-v2` | 384 | ~90MB | 常用英文模型（轻量） |
| `BAAI/bge-small-zh-v1.5` | 512 | ~95MB | 中文 embedding（HolographicAdapter） |

### 4.4 健康检查

Dockerfile 中配置了 HEALTHCHECK，验证模型缓存可用：

```dockerfile
HEALTHCHECK --interval=30s --timeout=60s --start-period=10s --retries=3 \
    CMD python -c "检查缓存目录中是否有模型" || exit 1
```

检查方式：
```bash
# 查看容器健康状态
docker inspect --format='{{.State.Health.Status}}' <container_id>

# 查看健康检查日志
docker inspect --format='{{json .State.Health.Log}}' <container_id> | python -m json.tool
```

---

## 5. Mock 注入逻辑详解

### 5.1 Mock 覆盖的模块

| 模块 | mock 方式 | 原因 |
|------|---------|------|
| `torch` | MagicMock + `__getattr__` | C 扩展在 Windows 上崩溃 |
| `torch.nn` | MagicMock | torch 子模块 |
| `torch.cuda` | `is_available()=False` | 无 GPU 环境 |
| `sentence_transformers` | `SentenceTransformer` 抛 ImportError | 依赖 torch |
| `transformers` | MagicMock | sentence_transformers 的依赖 |
| `chromadb` | MagicMock + config.Settings | 向量数据库 |
| `onnxruntime` | MagicMock | 备选推理后端 |
| `faiss` | MagicMock | 备选向量索引 |
| `hnswlib` | MagicMock | 备选向量索引 |
| `sqlite_vec` | **不 mock**（由 test fixture 处理） | 需要测试 sqlite-vec 后端时不干扰 |

### 5.2 Mock 模块的子模块访问

使用 `_make_mock_module()` 函数创建支持 `__getattr__` 的 mock 模块：

```python
def _make_mock_module(name, raise_on_instantiate=None):
    mock = types.ModuleType(name)
    mock.__getattr__ = lambda attr: MagicMock()  # 任何子模块访问返回 MagicMock
    if raise_on_instantiate:
        for class_name, msg in raise_on_instantiate.items():
            setattr(mock, class_name, _make_raising_class(msg))
    return mock
```

这样 `import torch.nn.functional` 不会报 `AttributeError`，而是返回 MagicMock。

### 5.3 适应性评估

| 变更场景 | 适应性 | 说明 |
|---------|--------|------|
| 新增 `from transformers import AutoModel` | ✅ | transformers 已 mock |
| 新增 `import torch.nn.functional` | ✅ | `__getattr__` 处理 |
| 新增 `import pinecone` | ❌ | 需手动添加到 mock 列表 |
| 改变导入语法（`import X as Y`） | ✅ | sys.modules 不依赖语法 |

---

## 6. Docker 构建优化

### 6.1 国内镜像源配置

**Dockerfile 中已配置**：
- **apt 源**：清华 Debian 镜像（`mirrors.tuna.tsinghua.edu.cn`）
- **pip 源**：清华 PyPI 镜像（`pypi.tuna.tsinghua.edu.cn`）

**加速效果**：

| 依赖 | 原速（官方源） | 加速后（清华源） | 提升 |
|------|--------------|----------------|------|
| apt 下载 | ~10 KB/s | ~7 MB/s | **700x** |
| pip torch | ~50 KB/s | ~10 MB/s | **200x** |
| 总构建时间 | 1+ 小时 | **8-10 分钟** | |

### 6.2 构建问题修复

| 问题 | 原因 | 修复 |
|------|------|------|
| `pip install -r /dev/stdin` 失败 | 新版 pip 不支持 | 改用临时文件 |
| `requirements-dev.txt` 的 `-r requirements.txt` 找不到文件 | pip 在 /tmp/ 下找不到 | 过滤 `^-r ` 行 |
| `pyproject.toml` 的 `torch<2.5.0` 冲突 | 版本约束不兼容 | `pip install --no-deps -e .` |
| `.dockerignore` 排除 tests/ | 构建上下文不含测试文件 | docker run 时 `-v` 挂载 |
| `pytest-timeout` 未安装 | requirements-dev.txt 未包含 | 运行时 `pip install pytest-timeout` |

### 6.3 Docker 层缓存策略

```dockerfile
# 先复制依赖文件（利用层缓存）
COPY requirements.txt requirements-dev.txt ./

# 安装依赖（依赖文件不变时缓存命中）
RUN pip install ... -r /tmp/req-filtered.txt

# 后复制项目代码（代码变更不影响依赖层缓存）
COPY . .
RUN pip install --no-deps -e .
```

---

## 7. 常见问题排查

### Q1: Windows 上运行 test_memory_vector_store.py 崩溃

**症状**：`Process finished with exit code -1073741819 (0xC0000005)`

**原因**：torch C 扩展在 Windows CPU 环境下触发 ACCESS_VIOLATION

**解决**：
```bash
# 使用 mock 脚本运行
python scripts/run_vector_store_tests_windows.py --verbose
```

### Q2: Docker 构建卡在 apt-get install

**症状**：构建日志长时间无更新

**原因**：默认 Debian 源（deb.debian.org）在国内访问极慢

**解决**：确认 Dockerfile 已配置清华镜像源（第 25-28 行）

### Q3: Docker 容器中测试找不到文件

**症状**：`ERROR: file or directory not found: tests/unit/...`

**原因**：`.dockerignore` 排除了 `tests/` 目录

**解决**：运行时挂载 tests 目录
```bash
docker run --rm -v "${PWD}/tests:/app/tests" ...
```

### Q4: Docker 容器中测试超时（模型下载）

**症状**：`Timeout ++++++++++++++++++++++++++++++++++++`

**原因**：Docker 容器无法访问 HuggingFace 下载模型

**解决**：
1. 使用预下载模型：`docker-compose -f docker-compose.linux-test.yml run --rm predownload-models`
2. 或挂载本地 HF 缓存：`-v "${HOME}/.cache/huggingface:/app/.hf_cache"`

### Q5: pytest 报 `unrecognized arguments: --timeout`

**症状**：`ERROR: unrecognized arguments: --timeout=60 --timeout-method=thread`

**原因**：pytest-timeout 插件未安装

**解决**：
```bash
# 在容器中安装
docker run --rm --entrypoint bash agent-test-sqlite-vec:latest -c "pip install pytest-timeout && pytest ..."
```

---

## 8. 文件清单

| 文件 | 用途 | 关键说明 |
|------|------|---------|
| `Dockerfile.linux-test` | Linux 测试环境镜像 | 清华源 + 预下载模型 + 健康检查 |
| `docker-compose.linux-test.yml` | Docker Compose 编排 | 5 个服务（predownload/test/test-integration/test-all/test-sqlite-vec） |
| `scripts/predownload_models.py` | 预下载 HF 模型 | 3 个默认模型，支持自定义列表 |
| `scripts/run_vector_store_tests_windows.py` | Windows mock 测试脚本 | 11 个 mock 模块，双层防护 |
| `docs/TLM_P4_DEPLOYMENT_GUIDE.md` | P4 迁移部署指南 | 备份/迁移/回滚/维度校验 |
| `docs/TLM_0xC0000005_CRASH_ANALYSIS.md` | 崩溃分析报告 | torch 崩溃原因 + 解决方案 |

---

## 附录：快速命令参考

```bash
# ── Windows 本地测试 ──
# L1: 不涉及模型的测试（182 个）
python -m pytest tests/unit/test_long_term_memory_embedding.py tests/unit/test_tlm_memory_store.py tests/unit/test_memory_storage_boundary.py -v

# L2: Mock 模式运行 VectorStore 测试（6 个）
python scripts/run_vector_store_tests_windows.py --verbose

# ── Linux Docker 测试 ──
# 构建镜像（8-10 分钟）
docker-compose -f docker-compose.linux-test.yml build test-sqlite-vec

# 预下载模型（5-10 分钟）
docker-compose -f docker-compose.linux-test.yml run --rm predownload-models

# 运行完整测试
docker-compose -f docker-compose.linux-test.yml run --rm test-sqlite-vec

# 运行特定测试文件
docker run --rm -w /app -v "${PWD}/tests:/app/tests" --entrypoint python agent-test-sqlite-vec:latest -m pytest tests/unit/test_long_term_memory_embedding.py -v
```
