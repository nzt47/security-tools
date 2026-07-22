# chromadb 0.5.x 降级详细操作手册（Windows 专用）

> 适用对象: Windows 10/11 用户
> 前置条件: Python 3.10+、pip、已安装 chromadb 1.x
> 预计耗时: 20-40 分钟（含依赖下载）
> 降级目标: chromadb 0.5.x（最后的纯 Python + hnswlib 稳定版本）

---

## 1. 降级背景

### 1.1 为什么需要降级

chromadb 1.x 在 Windows 上存在**根本性不兼容**：

```
AttributeError: 'RustBindingsAPI' object has no attribute 'bindings'
chromadb.errors.InternalError: 文件名、目录名或卷标语法不正确 (os error 123)
```

chromadb 1.x 使用 Rust 后端（chromadb-rust），其 Rust 绑定在 Windows 上缺失，导致 VectorStore 自动降级到 JSON fallback（仅 BM25 关键词搜索，无语义向量能力）。

### 1.2 为什么选择 0.5.x

| chromadb 版本 | 后端 | Windows 兼容 | HNSW 临时目录修复 | 推荐度 |
|--------------|------|-------------|------------------|--------|
| 0.4.x | 纯 Python + hnswlib | ✅ | ❌（[WinError 267]） | ★★☆☆☆ |
| **0.5.x** | **纯 Python + hnswlib** | **✅** | **✅** | **★★★★★** |
| 1.x | Rust 后端 | ❌ | N/A | ★☆☆☆☆ |

### 1.3 降级影响评估

| 项目 | 降级前（1.x 不兼容） | 降级后（0.5.x） | 改善 |
|------|---------------------|-----------------|------|
| VectorStore 后端 | json（降级） | chromadb（正常） | ✅ 恢复语义搜索 |
| 搜索能力 | BM25 关键词 | HNSW 语义向量 | ✅ 语义理解 |
| P2 预热缓存 | ✅ 正常 | ✅ 正常 | 无影响 |
| P4 heapq 排序 | ✅ 正常 | ✅ 正常 | 无影响 |

---

## 2. 降级前准备

### 2.1 环境检查

```powershell
# 检查 Python 版本（需 3.10+）
python --version
# 预期: Python 3.10.x / 3.11.x / 3.12.x

# 检查当前 chromadb 版本
python -c "import chromadb; print(f'当前版本: {chromadb.__version__}')"
# 预期: 1.x.x（如 1.5.9）

# 检查 pip 可用性
pip --version
# 预期: pip 23.x+ 
```

### 2.2 依赖备份

```powershell
# 导出当前依赖列表（降级前快照）
pip freeze > requirements_before_downgrade.txt

# 查看当前 chromadb 相关依赖
pip freeze | Select-String "chromadb|hnswlib|onnx|pypika|typer"
# 预期输出示例:
# chromadb==1.5.9
# onnxruntime==1.27.0
# pypika==0.48.9
# typer==0.15.1
```

### 2.3 数据备份

```powershell
# 备份 chromadb 持久化数据（如果存在）
if (Test-Path "data/memory") {
    Copy-Item -Recurse "data/memory" "data/memory_backup_$(Get-Date -Format 'yyyyMMddHHmmss')"
    Write-Host "数据已备份到 data/memory_backup_*"
}

# 备份 .env 配置
if (Test-Path ".env") {
    Copy-Item ".env" ".env_backup_$(Get-Date -Format 'yyyyMMddHHmmss')"
    Write-Host ".env 已备份"
}
```

### 2.4 依赖冲突预检查

```powershell
# 检查可能冲突的依赖
python -c "
import importlib
packages = ['chromadb', 'sentence_transformers', 'torch', 'numpy', 'transformers']
for pkg in packages:
    try:
        mod = importlib.import_module(pkg)
        ver = getattr(mod, '__version__', 'unknown')
        print(f'{pkg}: {ver}')
    except ImportError:
        print(f'{pkg}: NOT INSTALLED')
"
# 记录输出，降级后对比
```

---

## 3. 降级操作

### 3.1 步骤 1: 卸载 chromadb 1.x

```powershell
# 卸载当前版本
pip uninstall -y chromadb

# 验证卸载成功
python -c "import chromadb" 2>&1
# 预期输出:
# ModuleNotFoundError: No module named 'chromadb'
```

**如果卸载失败**（依赖冲突）:

```powershell
# 强制卸载（含依赖）
pip uninstall -y chromadb chromadb-client

# 清理残留
pip cache purge
```

### 3.2 步骤 2: 清理冲突依赖

chromadb 1.x 可能安装了 0.5.x 不需要的依赖，需要清理：

```powershell
# 卸载 1.x 特有的 Rust 相关包（如果有）
pip uninstall -y chromadb-rust 2>$null

# 卸载可能冲突的 onnxruntime（0.5.x 不强制依赖）
pip uninstall -y onnxruntime 2>$null

# 验证清理
pip list | Select-String "chroma|onnx|rust"
# 预期: 无输出（全部清理完毕）
```

### 3.3 步骤 3: 安装 chromadb 0.5.x

```powershell
# 安装 0.5.x 最新版（推荐 0.5.20，最后的 0.5.x 版本）
pip install "chromadb>=0.5.0,<0.6.0"

# 国内镜像加速（如果下载慢）
pip install "chromadb>=0.5.0,<0.6.0" -i https://pypi.tuna.tsinghua.edu.cn/simple

# 如果需要锁定具体版本
pip install chromadb==0.5.20
```

**安装预期输出**:

```
Collecting chromadb>=0.5.0,<0.6.0
  Downloading chromadb-0.5.20-py3-none-any.whl (...)
Collecting hnswlib>=0.7
  Downloading hnswlib-0.8.0-...
Collecting pypika>=0.48.9
  Downloading pypika-0.48.9-...
Successfully installed chromadb-0.5.20 hnswlib-0.8.0 ...
```

### 3.4 步骤 4: 验证版本

```powershell
python -c "import chromadb; print(f'chromadb 版本: {chromadb.__version__}')"
# 预期输出:
# chromadb 版本: 0.5.20
```

**如果版本不是 0.5.x**:

```powershell
# 检查是否有多个 chromadb 安装
pip list | Select-String "chroma"

# 强制重装
pip install --force-reinstall "chromadb>=0.5.0,<0.6.0"
```

### 3.5 步骤 5: 验证依赖完整性

```powershell
python -c "
import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions
print(f'chromadb: {chromadb.__version__}')
print(f'Settings: OK')
print(f'embedding_functions: OK')
print('依赖完整性验证通过')
"
# 预期输出:
# chromadb: 0.5.20
# Settings: OK
# embedding_functions: OK
# 依赖完整性验证通过
```

---

## 4. 详细验证步骤

### 4.1 验证 1: PersistentClient API

```powershell
python -c "
import chromadb
from chromadb.config import Settings
import tempfile

# 创建持久化客户端
with tempfile.TemporaryDirectory() as tmpdir:
    client = chromadb.PersistentClient(
        path=tmpdir,
        settings=Settings(anonymized_telemetry=False)
    )
    print(f'PersistentClient: OK')
    print(f'  path: {tmpdir}')
    
    # 创建集合
    collection = client.get_or_create_collection(name='test_v05')
    print(f'get_or_create_collection: OK')
    print(f'  name: {collection.name}')
"
# 预期: 无 AttributeError，无 InternalError
```

**如果失败**（`AttributeError: 'RustBindingsAPI'`）:

```powershell
# 说明 1.x 未完全卸载
pip uninstall -y chromadb
pip install "chromadb>=0.5.0,<0.6.0" --force-reinstall
```

### 4.2 验证 2: Collection CRUD 操作

```powershell
python -c "
import chromadb
from chromadb.config import Settings
import tempfile

with tempfile.TemporaryDirectory() as tmpdir:
    client = chromadb.PersistentClient(
        path=tmpdir,
        settings=Settings(anonymized_telemetry=False)
    )
    col = client.get_or_create_collection(name='crud_test')
    
    # Create
    col.add(
        documents=['doc1 content', 'doc2 content', 'doc3 content'],
        metadatas=[{'id': 1}, {'id': 2}, {'id': 3}],
        ids=['id1', 'id2', 'id3']
    )
    assert col.count() == 3, f'期望 3，实际 {col.count()}'
    print(f'add: OK (count={col.count()})')
    
    # Read
    result = col.get(ids=['id1'])
    assert len(result['ids']) == 1
    print(f'get: OK')
    
    # Update
    col.update(ids=['id1'], documents=['updated content'])
    result = col.get(ids=['id1'])
    assert result['documents'][0] == 'updated content'
    print(f'update: OK')
    
    # Delete
    col.delete(ids=['id1'])
    assert col.count() == 2
    print(f'delete: OK (count={col.count()})')
    
    print('CRUD 验证全部通过')
"
```

### 4.3 验证 3: 语义搜索（query）

```powershell
python -c "
import chromadb
from chromadb.config import Settings
import tempfile

with tempfile.TemporaryDirectory() as tmpdir:
    client = chromadb.PersistentClient(
        path=tmpdir,
        settings=Settings(anonymized_telemetry=False)
    )
    col = client.get_or_create_collection(name='query_test')
    
    col.add(
        documents=['apple fruit', 'banana fruit', 'car vehicle'],
        ids=['1', '2', '3']
    )
    
    results = col.query(query_texts=['fruit'], n_results=2)
    
    # 验证返回格式
    assert 'ids' in results
    assert 'documents' in results
    assert 'distances' in results
    assert 'metadatas' in results
    assert len(results['ids'][0]) == 2
    
    print(f'query 返回格式: OK')
    print(f'  ids: {results[\"ids\"]}')
    print(f'  documents: {results[\"documents\"]}')
    print(f'  distances: {results[\"distances\"]}')
    print('语义搜索验证通过')
"
```

### 4.4 验证 4: Windows 临时目录兼容性

> 这是 1.x → 0.5.x 降级的核心验证：0.5.x 修复了 [WinError 267] 临时目录问题

```powershell
python -c "
import chromadb
from chromadb.config import Settings
import tempfile
import os

# 使用 Windows 临时目录（1.x 在此路径失败）
tmpdir = tempfile.mkdtemp()
print(f'临时目录: {tmpdir}')

client = chromadb.PersistentClient(
    path=tmpdir,
    settings=Settings(anonymized_telemetry=False)
)
col = client.get_or_create_collection(name='winpath_test')
col.add(documents=['test doc'], ids=['1'])

# 验证 data_level0.bin 不再触发 NotADirectoryError
result = col.query(query_texts=['test'], n_results=1)
assert len(result['ids'][0]) == 1
print(f'Windows 临时目录: OK (无 NotADirectoryError)')

# 清理
import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
"
# 预期: 无 NotADirectoryError [WinError 267]
```

### 4.5 验证 5: VectorStore 集成

```powershell
python -c "
from memory.vector_store import VectorStore
import tempfile

store = VectorStore(
    persist_dir=tempfile.mkdtemp(),
    collection_name='downgrade_verify',
    cache_size=100
)
print(f'VectorStore 后端: {store._backend}')
assert store._backend == 'chromadb', f'期望 chromadb，实际 {store._backend}'

# 添加记忆
store.add('测试记忆内容', metadata={'type': 'verify'})
store.add('另一条记忆', metadata={'type': 'test'})

# 搜索
results = store.search('测试', top_k=5)
print(f'搜索结果数: {len(results)}')
assert len(results) > 0

# 缓存统计
stats = store.get_cache_stats()
print(f'缓存统计: {stats}')
print('VectorStore 集成验证通过')
"
# 预期: backend=chromadb
```

### 4.6 验证 6: P2 预热缓存

```powershell
# 设置离线模式（避免 HuggingFace 网络请求）
$env:HF_HUB_OFFLINE=1
$env:TRANSFORMERS_OFFLINE=1

python -m pytest tests/performance/test_vector_store_performance.py::TestVectorStorePerformance::test_search_performance -v -s --timeout=120
```

**预期输出**:

```
PASSED
搜索100次时间(预热后): X.XXms
缓存统计: 命中=N, 未命中=M, 命中率=99.XX%
```

### 4.7 验证 7: API 兼容性测试套件

```powershell
# 运行完整的 API 兼容性测试（Windows 上不再跳过）
python -m pytest tests/performance/test_chromadb_v05_api_compat.py -v --timeout=120
```

**预期结果**:

```
test_version_info PASSED
test_persistent_client_with_path PASSED
test_persistent_client_settings PASSED
test_get_or_create_collection PASSED
test_get_or_create_collection_with_hnsw_params PASSED
test_collection_add_documents PASSED
test_collection_add_embeddings PASSED
test_collection_query_texts PASSED
test_collection_query_embeddings PASSED
test_collection_query_with_where_filter PASSED
test_collection_count PASSED
test_collection_get PASSED
test_collection_update PASSED
test_collection_delete PASSED
test_api_compat_risk_report PASSED
=================== 15+ passed, 0 skipped ===================
```

> ⚠️ 如果仍有 skipped，说明 `_skip_windows` 标记未正确移除。需编辑测试文件移除 `@_skip_windows` 装饰器。

### 4.8 验证 8: 完整回归测试

```powershell
$env:HF_HUB_OFFLINE=1
$env:TRANSFORMERS_OFFLINE=1

python -m pytest tests/performance/ -v --timeout=300
```

**预期结果**: 全部 passed，无 failed，无 skipped（0.5.x 在 Windows 上兼容）

---

## 5. 故障排除

### 5.1 问题: 安装 0.5.x 后 import 失败

```
ImportError: cannot import name 'PersistentClient' from 'chromadb'
```

**原因**: 0.5.x 与 1.x 的 API 路径不同

**解决**:

```powershell
# 检查实际安装的版本
pip show chromadb

# 如果显示 1.x，说明安装失败
pip uninstall -y chromadb
pip cache purge
pip install "chromadb>=0.5.0,<0.6.0" --no-cache-dir
```

### 5.2 问题: hnswlib 安装失败

```
ERROR: Failed building wheel for hnswlib
```

**解决**:

```powershell
# 安装 Visual C++ Build Tools（如果未安装）
# 下载: https://visualstudio.microsoft.com/visual-cpp-build-tools/

# 或使用预编译 wheel
pip install hnswlib --only-binary :all:

# 然后安装 chromadb
pip install "chromadb>=0.5.0,<0.6.0" --no-deps
pip install pypika tqdm pydantic
```

### 5.3 问题: sentence-transformers 版本冲突

```
ERROR: chromadb 0.5.x requires sentence-transformers<3.0, but you have 5.6.0
```

**解决**: chromadb 0.5.x 不强制依赖 sentence-transformers，VectorStore 自行管理：

```powershell
# 忽略依赖检查安装
pip install "chromadb>=0.5.0,<0.6.0" --no-deps

# 手动安装必要的 chromadb 依赖
pip install pypika hnswlib tqdm pydantic posthog

# sentence-transformers 保持当前版本（VectorStore 直接使用）
pip show sentence-transformers
# 预期: 5.6.0（无需降级）
```

### 5.4 问题: VectorStore 仍降级到 json 后端

```powershell
python -c "from memory.vector_store import VectorStore; import tempfile; s=VectorStore(persist_dir=tempfile.mkdtemp()); print(s._backend)"
# 输出: json（期望 chromadb）
```

**排查**:

```powershell
# 检查 chromadb 是否可导入
python -c "import chromadb; print(chromadb.__version__)"

# 检查日志
python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from memory.vector_store import VectorStore
import tempfile
s = VectorStore(persist_dir=tempfile.mkdtemp())
" 2>&1 | Select-String "chroma|backend|fallback"
```

### 5.5 问题: 查询结果格式变化

0.5.x 与 1.x 的 `collection.query()` 返回格式可能略有差异：

```powershell
# 验证返回格式
python -c "
import chromadb
from chromadb.config import Settings
import tempfile

with tempfile.TemporaryDirectory() as tmpdir:
    client = chromadb.PersistentClient(path=tmpdir, settings=Settings(anonymized_telemetry=False))
    col = client.get_or_create_collection(name='format_test')
    col.add(documents=['test'], ids=['1'])
    result = col.query(query_texts=['test'], n_results=1)
    
    # 0.5.x 返回格式
    print(f'keys: {list(result.keys())}')
    print(f'ids: {result[\"ids\"]}')
    print(f'documents: {result[\"documents\"]}')
    print(f'distances: {result[\"distances\"]}')
    print(f'metadatas: {result[\"metadatas\"]}')
"
```

---

## 6. 降级后检查清单

完成所有降级步骤后，逐项确认:

- [ ] `chromadb.__version__` 输出 `0.5.x`（非 1.x）
- [ ] `import chromadb` 无 `AttributeError`
- [ ] `PersistentClient(path=...)` 无 `InternalError: os error 123`
- [ ] 临时目录下 `get_or_create_collection` 无 `NotADirectoryError`
- [ ] `VectorStore._backend` 为 `"chromadb"`（非 `"json"`）
- [ ] `collection.add/query/get/update/delete` 全部正常
- [ ] `test_chromadb_v05_api_compat.py` 全部 passed（无 skipped）
- [ ] `test_search_performance` 测试通过
- [ ] P2 预热缓存命中率 >= 95%
- [ ] P4 heapq 排序正常工作
- [ ] 应用启动无报错

---

## 7. 回滚（恢复到 chromadb 1.x）

如果 0.5.x 降级后出现问题，可恢复到 1.x:

```powershell
# 卸载 0.5.x
pip uninstall -y chromadb

# 安装 1.x
pip install chromadb==1.5.9

# 验证
python -c "import chromadb; print(f'chromadb {chromadb.__version__}')"
# 预期: chromadb 1.5.9
```

> ⚠️ 恢复到 1.x 后，Windows 上 chromadb 后端仍不兼容，VectorStore 会自动降级到 json 后端。如需使用 chromadb 后端，请迁移到 Linux 环境。

---

## 8. 版本兼容性矩阵

| 组件 | 推荐版本 | 最低版本 | 验证状态 |
|------|---------|---------|---------|
| chromadb | 0.5.20 | 0.5.0 | ✅ Windows 验证通过 |
| sentence-transformers | 5.6.0 | 3.0.0 | ✅ 兼容 |
| torch | 2.13.0+cpu | 2.0.0 | ✅ 兼容 |
| numpy | 2.4.6 | 1.24.0 | ✅ 兼容 |
| hnswlib | 0.8.0 | 0.7.0 | ✅ chromadb 0.5.x 依赖 |
| Python | 3.12.0 | 3.10.0 | ✅ 验证通过 |

---

## 9. 相关文档

| 文档 | 说明 |
|------|------|
| [chromadb Windows 降级操作手册（概览）](file:///c:/Users/Administrator/agent/docs/chromadb_windows_downgrade_guide.md) | 3 种降级方案概览（0.5.x/0.4.x/纯JSON）|
| [chromadb Windows 兼容性迁移指南](file:///c:/Users/Administrator/agent/docs/chromadb_windows_compatibility_migration_guide.md) | 完整迁移方案（Linux/WSL2/降级）|
| [上线发布说明](file:///c:/Users/Administrator/agent/docs/release_notes_p2_p3.md) | P2/P3 变更内容 + 回滚方案 |
| [P2+P3 生产环境部署检查清单](file:///c:/Users/Administrator/agent/docs/p2_p3_production_deployment_checklist.md) | 7 阶段部署验证清单 |
