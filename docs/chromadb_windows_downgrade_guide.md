# chromadb Windows 降级操作手册

> 适用对象: Windows 用户
> 前置条件: 已安装 Python 3.10+ 和 pip
> 预计耗时: 15-30 分钟

本手册提供两种降级方案，按推荐度排序。如需迁移到 Linux，请参阅 [chromadb Windows 兼容性迁移指南](file:///c:/Users/Administrator/agent/docs/chromadb_windows_compatibility_migration_guide.md)。

---

## 方案 A: 降级到 chromadb 0.5.x（推荐）

> chromadb 0.5.x 是最后一个纯 Python + hnswlib 的稳定版本，Windows 兼容性良好。

### 步骤 1: 卸载当前版本

```powershell
pip uninstall -y chromadb
```

验证卸载成功:

```powershell
python -c "import chromadb" 2>&1
# 预期: ModuleNotFoundError: No module named 'chromadb'
```

### 步骤 2: 安装 0.5.x

```powershell
pip install "chromadb>=0.5.0,<0.6.0"
```

国内镜像加速:

```powershell
pip install "chromadb>=0.5.0,<0.6.0" -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 步骤 3: 验证版本

```powershell
python -c "import chromadb; print(f'chromadb {chromadb.__version__}')"
# 预期: chromadb 0.5.x
```

### 步骤 4: 验证 API 兼容性

```powershell
python -m pytest tests/performance/test_chromadb_v05_api_compat.py -v --timeout=120
```

**预期结果**: 全部 passed（Windows 上不再跳过）

如果仍有 skipped，检查测试文件的 `_skip_windows` 标记是否需要调整。

### 步骤 5: 验证 VectorStore 后端

```powershell
python -c "
from memory.vector_store import VectorStore
import tempfile
store = VectorStore(persist_dir=tempfile.mkdtemp(), cache_size=100)
print(f'backend: {store._backend}')
assert store._backend == 'chromadb', f'期望 chromadb，实际 {store._backend}'
print('OK: chromadb 后端正常')
"
```

### 步骤 6: 验证 P2 预热缓存

```powershell
python -m pytest tests/performance/test_vector_store_performance.py::TestVectorStorePerformance::test_search_performance -v -s --timeout=120
```

### 已知风险

| 风险 | 说明 | 缓解 |
|------|------|------|
| 安全漏洞 | 0.5.x 不再接收安全补丁 | 限制网络访问，定期检查 CVE |
| HNSW 临时目录 | 0.5.x 修复了 [WinError 267] | 使用项目内持久化目录 |
| API 变化 | `Settings` 参数可能微调 | API 兼容性测试已覆盖 |

---

## 方案 B: 降级到 chromadb 0.4.x

> 仅当方案 A 不工作时使用。0.4.x 是最旧的稳定版本，API 差异较大。

### 步骤 1-3: 同方案 A（替换版本号）

```powershell
pip uninstall -y chromadb
pip install "chromadb>=0.4.0,<0.5.0"
python -c "import chromadb; print(f'chromadb {chromadb.__version__}')"
```

### 0.4.x 特有限制

- `PersistentClient` 接口与 1.x 有差异
- 无 HNSW 优化（使用 IVF Flat）
- `collection.query` 返回格式可能略有不同

### 验证

```powershell
python -m pytest tests/performance/test_chromadb_v05_api_compat.py -v --timeout=120
```

如果 API 兼容性测试失败，需根据失败项调整 VectorStore 代码。

---

## 方案 C: 纯 JSON fallback（零依赖降级）

> 完全卸载 chromadb，VectorStore 自动降级到 BM25 关键词搜索。

### 步骤 1: 卸载 chromadb

```powershell
pip uninstall -y chromadb
```

### 步骤 2: 验证降级

```powershell
python -c "
from memory.vector_store import VectorStore
import tempfile
store = VectorStore(persist_dir=tempfile.mkdtemp(), cache_size=100)
print(f'backend: {store._backend}')
assert store._backend == 'json', f'期望 json，实际 {store._backend}'
print('OK: JSON fallback 后端正常')
"
```

### 步骤 3: 验证 P2 预热缓存

```powershell
# 设置离线模式（避免 sentence-transformers 网络请求）
$env:HF_HUB_OFFLINE=1
$env:TRANSFORMERS_OFFLINE=1
python -m pytest tests/performance/test_vector_store_performance.py::TestVectorStorePerformance::test_search_performance -v -s --timeout=120
```

### JSON fallback 性能基准

| 指标 | JSON fallback (BM25) | chromadb (HNSW) |
|------|---------------------|-----------------|
| 100 次搜索（预热后） | 0.35ms | 需 Linux |
| 缓存命中率 | 99.01% | N/A |
| 语义理解能力 | ❌ 仅关键词 | ✅ 语义向量 |
| 安装复杂度 | 零依赖 | 需 chromadb + torch |

---

## 降级后验证清单

无论选择哪种方案，降级后执行以下验证:

- [ ] `chromadb.__version__` 输出正确版本（方案 C 跳过）
- [ ] `VectorStore._backend` 为预期值（`chromadb` 或 `json`）
- [ ] `test_search_performance` 测试通过
- [ ] `test_chromadb_v05_api_compat.py` 测试通过或合理跳过
- [ ] 应用启动无报错
- [ ] 搜索功能正常返回结果

---

## 回滚降级（恢复到 chromadb 1.x）

如果降级后出现问题，可恢复到 1.x:

```powershell
pip uninstall -y chromadb
pip install chromadb==1.5.9
```

> ⚠️ 恢复到 1.x 后，Windows 上 chromadb 后端仍不兼容，VectorStore 会降级到 json。
> 如需使用 chromadb 后端，请迁移到 Linux 环境。

---

## FAQ

### Q1: 降级后数据需要迁移吗？

**不需要**。VectorStore 的 JSON 数据（`data/memory/*.json`）与 chromadb 后端独立存储。切换后端后：
- JSON → chromadb: 需要重新导入数据到 chromadb
- chromadb → JSON: 自动从 JSON 文件加载
- chromadb 0.5.x → 0.4.x: 需要重建索引（API 差异）

### Q2: 降级后 P2 预热缓存还有效吗？

**有效**。P2 预热缓存是 VectorStore 层面的优化，与后端无关。无论 chromadb 还是 json 后端，LRU 缓存都正常工作。

### Q3: 降级后 P4 heapq 排序还有效吗？

**有效**。P4 heapq 优化仅影响 BM25 排序（json 后端路径），与 chromadb 无关。

### Q4: 如何判断当前使用哪个后端？

```powershell
python -c "
from memory.vector_store import VectorStore
store = VectorStore(persist_dir='./data/memory')
print(f'当前后端: {store._backend}')
# chromadb = 语义向量搜索
# json = BM25 关键词搜索
"
```

---

## 相关文档

| 文档 | 说明 |
|------|------|
| [chromadb Windows 兼容性迁移指南](file:///c:/Users/Administrator/agent/docs/chromadb_windows_compatibility_migration_guide.md) | 完整迁移方案（Linux/WSL2/降级）|
| [P2+P3 生产环境部署检查清单](file:///c:/Users/Administrator/agent/docs/p2_p3_production_deployment_checklist.md) | 7 阶段部署验证清单 |
| [上线发布说明](file:///c:/Users/Administrator/agent/docs/release_notes_p2_p3.md) | P2/P3 变更内容 + 回滚方案 |
