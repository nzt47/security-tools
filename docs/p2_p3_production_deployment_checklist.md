# P2 + P3 生产环境部署最终检查清单

> 创建时间: 2026-07-22
> 基于: Linux 验证报告 `verify_report_20260721_122721.md` + P3 实施计划
> 适用范围: 生产环境部署 P2（预热缓存）+ P3（离线模型预下载）方案

## 部署阶段总览

| 阶段 | 内容 | 预计耗时 | 状态 |
|------|------|---------|------|
| 阶段 0 | 环境前置检查 | 5 分钟 | ☐ |
| 阶段 1 | P3 离线模型预下载 | 15-30 分钟 | ☐ |
| 阶段 2 | P2 预热缓存配置 | 5 分钟 | ☐ |
| 阶段 3 | chromadb 后端验证 | 10 分钟 | ☐ |
| 阶段 4 | 性能基准测试 | 15 分钟 | ☐ |
| 阶段 5 | 回归测试 | 5 分钟 | ☐ |
| 阶段 6 | 上线确认 | 5 分钟 | ☐ |

---

## 阶段 0: 环境前置检查

### 0.1 操作系统

- [ ] 确认操作系统为 **Linux**（Ubuntu 22.04/24.04 推荐）
  - 命令: `uname -a`
  - 预期: `Linux ... x86_64 ... GNU/Linux`
  - ⚠️ Windows 原生不支持 chromadb 1.x（Rust 绑定问题），参见[迁移指南](file:///c:/Users/Administrator/agent/docs/chromadb_windows_compatibility_migration_guide.md)

- [ ] 确认 Python 版本 **>= 3.10**
  - 命令: `python3 --version`
  - 预期: `Python 3.10+`（推荐 3.12）

### 0.2 磁盘空间

- [ ] 可用磁盘空间 **>= 3GB**
  - chromadb + sentence-transformers 安装: ~2.5GB
  - 模型缓存: ~500MB
  - 命令: `df -h .`

### 0.3 网络连通性

- [ ] PyPI 可访问（安装依赖）
  - 命令: `curl -sI https://pypi.org | head -1`
  - 国内备选: `PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`

- [ ] HuggingFace 可访问（模型下载）
  - 命令: `curl -sI https://huggingface.co | head -1`
  - 国内备选: `HF_ENDPOINT=https://hf-mirror.com`

- [ ] AWS S3 可访问（chromadb ONNX 模型下载）
  - 命令: `curl -sI https://chroma-onnx-models.s3.amazonaws.com | head -1`

### 0.4 Git 状态

- [ ] 确认在 `master` 分支
  - 命令: `git branch --show-current`
  - 预期: `master`

- [ ] 确认 P2/P3 commit 已合并
  - 命令: `git log --oneline -5 | grep -E "P2|P3|predownload"`
  - 预期: 包含 `4a3bce02`（P2+P4）、`ed6f2ce1`（P4 压测+Linux 脚本）、`8fe187d1`（P3 预下载）

- [ ] 确认工作区干净
  - 命令: `git status --short`
  - 预期: 无输出（或仅有 `.venv-verify/` 等忽略项）

---

## 阶段 1: P3 离线模型预下载

### 1.1 依赖安装

- [ ] 安装 chromadb + sentence-transformers
  ```bash
  pip install chromadb sentence-transformers
  ```
  - 国内加速: `PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple pip install chromadb sentence-transformers`

- [ ] 安装 tiktoken（memory 模块依赖）
  ```bash
  pip install tiktoken
  ```

- [ ] 验证依赖版本
  ```bash
  python -c "
  import chromadb, sentence_transformers, torch, numpy, huggingface_hub
  print(f'chromadb: {chromadb.__version__}')
  print(f'sentence_transformers: {sentence_transformers.__version__}')
  print(f'torch: {torch.__version__}')
  print(f'numpy: {numpy.__version__}')
  print(f'huggingface_hub: {huggingface_hub.__version__}')
  "
  ```
  - 预期版本:
    - chromadb: `1.5.9+`
    - sentence-transformers: `>=3.0.0`
    - torch: `>=2.0.0`
    - numpy: `>=1.24.0`
    - huggingface-hub: `>=0.20.0`

### 1.2 模型预下载

- [ ] 运行预下载脚本
  ```bash
  python scripts/predownload_model.py --all
  ```
  - 预期输出: `✓ 模型预下载完成` + 维度验证 + 编码验证通过
  - 模型: `paraphrase-multilingual-MiniLM-L12-v2`（470MB, 384 维）

- [ ] 验证离线模式可用
  ```bash
  python scripts/predownload_model.py --verify-offline
  ```
  - 预期: `✓ 离线模式验证通过`
  - 设置环境变量: `HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1`

- [ ] 生成 .env 配置
  ```bash
  python scripts/predownload_model.py --skip-deps-check >> .env
  ```
  - 预期: .env 中添加 `HF_HUB_OFFLINE=1` 等配置

### 1.3 chromadb ONNX 模型（可选）

> 仅当使用 chromadb 默认 ONNX embedding 时需要。VectorStore 使用 sentence-transformers 时可跳过。

- [ ] 预下载 ONNX 模型（如需要）
  ```bash
  mkdir -p ~/.cache/chroma/onnx_models/all-MiniLM-L6-v2/
  wget -O ~/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx.tar.gz \
    https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz
  ```
  - 预期文件大小: ~79MB
  - ⚠️ GitHub 下载极慢（29 kiB/s），必须用 AWS S3 源

---

## 阶段 2: P2 预热缓存配置

### 2.1 VectorStore 配置

- [ ] 确认 VectorStore 构造参数包含 `cache_size > 0`
  - 文件: [memory/vector_store/vector_store.py](file:///c:/Users/Administrator/agent/memory/vector_store/vector_store.py)
  - 默认值: `cache_size=100`（生产环境推荐 `200`）
  - 检查命令:
    ```bash
    grep -n "cache_size" memory/vector_store/vector_store.py | head -5
    ```

- [ ] 确认 LRU 缓存 TTL 配置
  - 默认值: `cache_ttl=300`（5 分钟）
  - 生产环境推荐: `600`（10 分钟，减少冷启动）

### 2.2 预热策略

- [ ] 确认应用启动时执行预热查询
  - P2 核心思想: 首次 search 触发 BM25 全量计算，预热后 100 次循环全部命中缓存
  - 预热查询应覆盖高频搜索词
  - 示例:
    ```python
    # 应用启动时预热
    store.search("常见查询词1", top_k=5)
    store.search("常见查询词2", top_k=5)
    ```

### 2.3 缓存监控

- [ ] 确认缓存统计接口可用
  ```python
  stats = store.get_cache_stats()
  # 预期: {'hits': N, 'misses': M, 'hit_rate': X.XX%, 'size': S}
  ```
  - 生产环境目标: 命中率 >= 95%

---

## 阶段 3: chromadb 后端验证

### 3.1 后端选择确认

- [ ] 确认 VectorStore 使用 chromadb 后端（非 json fallback）
  ```python
  from memory.vector_store import VectorStore
  store = VectorStore(persist_dir="./data/memory")
  assert store._backend == "chromadb", f"期望 chromadb，实际 {store._backend}"
  ```
  - ⚠️ 如果 `_backend == "json"`，说明 chromadb 初始化失败，检查日志

### 3.2 路径修复验证

- [ ] 运行 Linux 验证脚本
  ```bash
  bash scripts/verify_chromadb_p2_linux.sh
  ```
  - 预期结果（来自验证报告）:
    | STEP | 内容 | 预期结果 |
    |------|------|---------|
    | STEP 1 | 环境准备 | PASS |
    | STEP 2 | chromadb 路径问题修复 | **PASS** |
    | STEP 3 | P2 预热缓存效果 | PASS |
    | STEP 4 | JSON vs chromadb 对比 | DONE |
    | STEP 5 | 性能测试回归 | PASS（5 passed）|

### 3.3 API 兼容性测试

- [ ] 运行 API 兼容性测试
  ```bash
  python -m pytest tests/performance/test_chromadb_v05_api_compat.py -v --timeout=120
  ```
  - 预期: 全部 passed（Linux 上无 skipped）
  - ⚠️ Windows 上会 18 skipped（Rust 绑定问题），属正常

---

## 阶段 4: 性能基准测试

### 4.1 基础性能测试

- [ ] 运行向量存储性能测试
  ```bash
  python -m pytest tests/performance/test_vector_store_performance.py -v -s
  ```
  - 关注指标:
    - 向量存储初始化时间（目标 < 30s）
    - 添加 100 条记忆时间（目标 < 500ms）
    - 搜索 100 次时间（预热后，目标 < 5ms）
    - 缓存命中率（目标 >= 95%）

### 4.2 P4 heapq 大规模压测（可选）

- [ ] 运行 BM25 + heapq 压测
  ```bash
  python scripts/run_p4_benchmark.py
  ```
  - 预期（来自压测报告）:
    | 文档数 | heapq 提速 |
    |--------|-----------|
    | n=500 | +2.59%（波动）|
    | n=2000 | +10.73%（显著）|
    | n=3000 | +8.18%（稳定）|
    | n=5000 | +1.25%（波动）|

### 4.3 性能对比基准

- [ ] 记录生产环境性能基线
  | 指标 | JSON fallback | chromadb | 目标值 |
  |------|-------------|----------|--------|
  | 100 次搜索（预热后） | 0.35ms | 待测 | < 5ms |
  | 缓存命中率 | 99.01% | N/A | >= 95% |
  | 首次搜索（冷启动） | 2.19ms | 待测 | < 100ms |

---

## 阶段 5: 回归测试

### 5.1 核心功能回归

- [ ] 运行核心测试套件
  ```bash
  python -m pytest tests/ -v --timeout=300 -x
  ```
  - 预期: 全部 passed（无 failed）

- [ ] 运行性能测试专项
  ```bash
  python -m pytest tests/performance/ -v --timeout=300
  ```
  - 预期: 全部 passed

### 5.2 向量存储功能验证

- [ ] 添加记忆 + 搜索记忆
  ```python
  from memory.vector_store import VectorStore
  store = VectorStore(persist_dir="./data/memory")
  store.add("测试记忆内容", metadata={"type": "test"})
  results = store.search("测试", top_k=5)
  assert len(results) > 0
  ```

- [ ] 缓存失效验证
  ```python
  store.search("测试", top_k=5)  # 缓存命中
  store.add("新记忆", metadata={})  # 应触发缓存失效
  store.search("测试", top_k=5)  # 缓存未命中（重新计算）
  ```

---

## 阶段 6: 上线确认

### 6.1 配置文件检查

- [ ] 确认 `.env` 包含离线模式配置
  ```bash
  grep -E "HF_HUB_OFFLINE|TRANSFORMERS_OFFLINE" .env
  ```
  - 预期: `HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1`

- [ ] 确认模型缓存目录正确
  ```bash
  echo $HF_HOME
  ls -la ~/.cache/huggingface/hub/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2/
  ```
  - 预期: 模型文件存在

### 6.2 监控配置

- [ ] 确认 VectorStore 后端监控
  ```python
  # 监控指标
  print(f"后端: {store._backend}")  # 应为 "chromadb"
  print(f"缓存: {store.get_cache_stats()}")
  ```

- [ ] 确认日志级别适当
  - 生产环境: `WARNING` 或 `INFO`
  - 调试环境: `DEBUG`

### 6.3 回滚预案

- [ ] 确认回滚方案
  - 如 chromadb 后端异常，VectorStore 自动降级到 json fallback
  - 降级后功能可用（无语义搜索），不影响核心业务

- [ ] 确认回滚命令
  ```bash
  # 紧急回滚: 禁用 chromadb（强制 json fallback）
  export DISABLE_CHROMADB=1  # 如有此环境变量
  # 或在 .env 中设置
  ```

### 6.4 上线签字

- [ ] 所有阶段检查项通过
- [ ] 性能基线已记录
- [ ] 回滚预案已确认
- [ ] 运维团队已通知

---

## 附录 A: 常见问题排查

### 问题 1: VectorStore 降级到 json 后端

**现象**: `store._backend == "json"` 而非 `"chromadb"`

**排查步骤**:
1. 检查日志中是否有 `ChromaDB 初始化失败` 警告
2. 确认 chromadb 已安装: `python -c "import chromadb"`
3. 确认模型已预下载: `python scripts/predownload_model.py --verify-offline`
4. 确认网络: HuggingFace 可访问或已设置离线模式

### 问题 2: HuggingFace 模型下载失败

**现象**: `Network is unreachable` 或 `couldn't connect to 'https://huggingface.co'`

**解决方案**:
1. 使用国内镜像: `export HF_ENDPOINT=https://hf-mirror.com`
2. 或预下载后离线: `python scripts/predownload_model.py --all` → `export HF_HUB_OFFLINE=1`

### 问题 3: chromadb ONNX 模型下载极慢

**现象**: 下载速度 29 kiB/s，预计 45 分钟

**解决方案**:
1. 不要从 GitHub 下载，改用 AWS S3:
   ```bash
   wget -O ~/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx.tar.gz \
     https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz
   ```
2. AWS S3 速度: ~113 KB/s（7 分钟完成）

### 问题 4: 缓存命中率低

**现象**: `hit_rate < 95%`

**排查步骤**:
1. 确认预热查询覆盖高频搜索词
2. 检查 `cache_ttl` 是否过短（生产环境推荐 600s）
3. 检查 `cache_size` 是否过小（生产环境推荐 200）
4. 确认添加/删除记忆后缓存正确失效

---

## 附录 B: 验证脚本快速索引

| 脚本 | 阶段 | 命令 |
|------|------|------|
| 环境验证 | 阶段 0 | `uname -a && python3 --version && df -h .` |
| 模型预下载 | 阶段 1 | `python scripts/predownload_model.py --all` |
| 离线验证 | 阶段 1 | `python scripts/predownload_model.py --verify-offline` |
| 路径修复验证 | 阶段 3 | `bash scripts/verify_chromadb_p2_linux.sh` |
| API 兼容性 | 阶段 3 | `python -m pytest tests/performance/test_chromadb_v05_api_compat.py -v` |
| 性能测试 | 阶段 4 | `python -m pytest tests/performance/test_vector_store_performance.py -v -s` |
| heapq 压测 | 阶段 4 | `python scripts/run_p4_benchmark.py` |
| 回归测试 | 阶段 5 | `python -m pytest tests/ -v --timeout=300 -x` |

---

## 附录 C: 关键文件索引

| 文件 | 说明 |
|------|------|
| [memory/vector_store/vector_store.py](file:///c:/Users/Administrator/agent/memory/vector_store/vector_store.py) | VectorStore 核心（P2 预热 + P4 heapq）|
| [scripts/predownload_model.py](file:///c:/Users/Administrator/agent/scripts/predownload_model.py) | P3 离线模型预下载脚本 |
| [scripts/verify_chromadb_p2_linux.sh](file:///c:/Users/Administrator/agent/scripts/verify_chromadb_p2_linux.sh) | Linux 5 步验证脚本 |
| [tests/performance/test_chromadb_v05_api_compat.py](file:///c:/Users/Administrator/agent/tests/performance/test_chromadb_v05_api_compat.py) | chromadb API 兼容性测试 |
| [tests/performance/test_bm25_heapq_benchmark.py](file:///c:/Users/Administrator/agent/tests/performance/test_bm25_heapq_benchmark.py) | BM25 + heapq 压测 |
| [docs/p3_offline_model_implementation_plan.md](file:///c:/Users/Administrator/agent/docs/p3_offline_model_implementation_plan.md) | P3 实施计划 |
| [docs/p2_p4_warmup_heapq_comparison_report.md](file:///c:/Users/Administrator/agent/docs/p2_p4_warmup_heapq_comparison_report.md) | P2+P4 对比报告 |
| [docs/chromadb_windows_compatibility_migration_guide.md](file:///c:/Users/Administrator/agent/docs/chromadb_windows_compatibility_migration_guide.md) | Windows 兼容性迁移指南 |
