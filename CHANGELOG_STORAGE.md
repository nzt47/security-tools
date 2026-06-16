# Core/Storage 模块修复变更日志

## 版本: 2026-06-16

### 修复内容

#### 1. 修复 Windows 命令行 emoji 编码问题

**问题描述**：日志中使用 emoji 字符（📦、📥、📤、🗑️）在 Windows 命令行环境下可能导致 Unicode 编码错误。

**修复方案**：将所有 emoji 替换为标准文本格式的日志前缀

**修改位置**：`core/storage.py` 第 113、142、158、190、211、228、248 行

**变更详情**：
- `📦 JSONFileStorage initialized` → `[JSONFileStorage] JSONFileStorage initialized`
- `📥 Loaded: {key}` → `[JSONFileStorage.load] Loaded: {key}`
- `📤 Saved: {key}` → `[JSONFileStorage.save] Saved: {key}`
- `🗑️ Deleted: {key}` → `[JSONFileStorage.delete] Deleted: {key}`
- `📦 InMemoryStorage initialized` → `[InMemoryStorage] InMemoryStorage initialized`
- `📤 Saved (in-memory): {key}` → `[InMemoryStorage.save] Saved (in-memory): {key}`
- `🗑️ Deleted (in-memory): {key}` → `[InMemoryStorage.delete] Deleted (in-memory): {key}`

---

#### 2. 优化 create_storage 函数参数验证

**问题描述**：`create_storage` 函数对传入的无效参数没有验证，可能导致意外行为。

**修复方案**：增加参数验证逻辑

**修改位置**：`core/storage.py` 第 262-298 行

**变更详情**：
- **JSONFileStorage**：验证参数只接受 `base_dir` 和 `ensure_dir`，其他参数会被忽略并记录警告日志
- **InMemoryStorage**：检测到传入参数时记录警告并忽略

**新增测试**：
- `test_create_storage_json_with_invalid_args` - 验证 JSON 存储的无效参数被正确处理
- `test_create_storage_memory_with_args` - 验证内存存储的参数被正确忽略

---

#### 3. 为 _get_filepath 方法补充单元测试

**问题描述**：`_get_filepath` 方法缺少独立的单元测试，无法验证路径遍历防护逻辑。

**修复方案**：增加三个测试用例

**新增测试**：
- `test_json_storage_get_filepath_normal_key` - 测试正常键的处理
- `test_json_storage_get_filepath_path_traversal` - 测试路径遍历攻击防护
- `test_json_storage_get_filepath_slashes` - 测试斜杠字符处理

---

### 测试覆盖率

| 文件 | 语句数 | 未覆盖 | 覆盖率 |
|------|--------|--------|--------|
| `core/storage.py` | 176 | 5 | **97%** |

**未覆盖的5行**：`BaseStorage` 抽象类中的抽象方法占位符（`pass` 语句），无法被测试覆盖，属于正常现象。

---

### 依赖模块兼容性检查

**检查结果**：所有依赖模块无需修改

| 依赖模块 | 路径 | 使用方式 | 兼容性 |
|---------|------|---------|--------|
| `vector_store_optimized.py` | `agent/memory/` | `create_storage("json", base_dir=...)` | ✅ 兼容 |
| `test_core.py` | `tests/unit/` | 测试文件 | ✅ 兼容 |
| `test_core_comprehensive.py` | `tests/unit/` | 测试文件 | ✅ 兼容 |

**说明**：`vector_store_optimized.py` 只使用了 `base_dir` 参数，这是 `JSONFileStorage` 支持的有效参数，因此无需修改。

---

### 测试结果

```
测试统计: 62 passed, 0 failed, 0 skipped
执行时间: 0.68s
```

---

### 代码位置

- **修改文件**：[core/storage.py](file:///C:/Users/Administrator/agent/core/storage.py)
- **测试文件**：[tests/unit/test_core_comprehensive.py](file:///C:/Users/Administrator/agent/tests/unit/test_core_comprehensive.py)
- **测试报告**：[tests/unit/core_storage_test_report.md](file:///C:/Users/Administrator/agent/tests/unit/core_storage_test_report.md)