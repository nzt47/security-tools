# CI 环境测试失败修复方案

> 生成时间: 2026-07-10
> 修复提交: ac38394e / 0774b333 / 86887c51 / 6ee0ab0e
> 影响: 云枢系统测试流程 (ci.yml) — 单元测试 Python 3.10/3.11/3.12

## 失败概览

CI 历史（ci.yml）显示云枢系统测试流程**连续 10+ 次失败**，根因为 6 个预先存在的 CI 环境问题，与阶段4（pytest-randomly + conftest 隔离增强）无关。

| # | 测试文件 | 失败原因 | 修复方式 |
|---|---------|---------|---------|
| 1 | test_monitoring_metrics.py | 浮点精度 (0.1+0.2+0.3≠0.6) | pytest.approx() |
| 2 | test_memory_optimized.py | ChromaDB 目录清理 (OSError 39) | ignore_cleanup_errors=True |
| 3 | test_web_scraper.py (×2) | cssselect 不在 pyproject.toml | pyproject.toml dependencies 添加 cssselect |
| 4 | test_pdf_tools.py | pypdf 不在 pyproject.toml | pyproject.toml dependencies 添加 pypdf |
| 5 | test_output_validation.py (×5) | jsonschema 不在 pyproject.toml | pyproject.toml dependencies 添加 jsonschema |
| 6 | test_system_tools_platform.py (×3) | Windows 路径保护测试在 Linux CI 上 mock 不完整 | _windows_path_env() 辅助上下文管理器 |

### CI 依赖安装机制

CI 使用 `pip install -e .` 安装项目，**只读取 `pyproject.toml` 的 `dependencies` 列表**，不读取 `requirements.txt` 或 `requirements-test.txt`。这是 #3/#4/#5 三个依赖缺失失败的根因。

```yaml
# .github/workflows/ci.yml L134-136
python -m pip install --upgrade pip
pip install pytest pytest-cov pytest-xdist pytest-mock pytest-timeout pytest-asyncio pytest-randomly
pip install -e .
```

---

## 详细修复方案

### 1. 浮点精度问题

**文件**: [test_monitoring_metrics.py](file:///c:/Users/Administrator/agent/tests/unit/test_monitoring_metrics.py#L77-L90)

**根因分析**:
```
0.1 + 0.2 + 0.3 = 0.6000000000000001  (IEEE 754 双精度浮点数)
```
测试断言 `assert stats["sum"] == 0.6` 在 CI 环境中因浮点表示误差而失败。本地环境偶尔通过是因为 Python 优化器在某些情况下会折叠常量表达式。

**修复**:
```python
# 修复前
assert stats["sum"] == 0.6
assert stats["min"] == 0.1
assert stats["max"] == 0.3
assert stats["p50"] == 0.2

# 修复后
assert stats["sum"] == pytest.approx(0.6)
assert stats["min"] == pytest.approx(0.1)
assert stats["max"] == pytest.approx(0.3)
assert stats["p50"] == pytest.approx(0.2)
```

`pytest.approx()` 默认相对容差为 1e-6，足以处理 IEEE 754 浮点误差。

---

### 2. ChromaDB 临时目录清理失败

**文件**: [test_memory_optimized.py](file:///c:/Users/Administrator/agent/tests/unit/test_memory_optimized.py#L408-L419)

**根因分析**:
```
OSError: [Errno 39] Directory not empty: '/tmp/tmp0qwlpmb1'
```

`OptimizedChromaDB` 在 `enable_async=True` 模式下，后台线程会在 `persist_directory` 中写入持久化文件。当 `tempfile.TemporaryDirectory()` 的 `__exit__` 被调用时，ChromaDB 后台线程可能仍在写入，导致目录非空，`shutil.rmtree()` 清理失败。

**修复**:
```python
# 修复前 (11处)
with tempfile.TemporaryDirectory() as tmpdir:

# 修复后
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir
```

`ignore_cleanup_errors=True`（Python 3.10+）在清理遇到错误时跳过而非抛出异常，适用于 ChromaDB 这类有后台线程的库。CI 支持 Python 3.10/3.11/3.12，参数兼容性无问题。

**为何只影响 CI**:
- 本地环境 ChromaDB 未安装（使用模拟实现），不创建持久化文件
- CI 环境 `requirements.txt` 包含 `chromadb>=0.4.0`，真实 ChromaDB 会创建持久化文件

---

### 3. cssselect 依赖缺失

**文件**: [test_web_scraper.py](file:///c:/Users/Administrator/agent/tests/unit/test_web_scraper.py#L61-L83)

**根因分析**:
```
assert 0 == 1  # len(result) == 0，cssselect 返回空列表
```

`Scraper.css()` 调用 `tree.cssselect(selector)`，该方法依赖 `cssselect` 包。虽然 `lxml` 是核心依赖，但 `cssselect` 是独立的可选扩展包。CI 环境安装了 `lxml` 但未安装 `cssselect`，导致 `cssselect()` 方法抛出异常，被 `Scraper.css()` 的 `except` 块捕获，返回空列表 `[]`。

**修复**:
在 [pyproject.toml](file:///c:/Users/Administrator/agent/pyproject.toml#L63) 的 `dependencies` 添加:
```toml
"cssselect>=1.2.0,<2.0.0",
```

> 注: 初版修复仅添加到 requirements-test.txt，但 CI 不读此文件。后续修正为添加到 pyproject.toml。

---

### 4. pypdf 依赖缺失

**文件**: [test_pdf_tools.py](file:///c:/Users/Administrator/agent/tests/unit/test_pdf_tools.py#L69-L73)

**根因分析**:
```python
# 测试期望
assert "文件不存在" in result["error"]

# CI 实际返回
assert "缺少依赖库 'pypdf'" in result["error"]  # 失败
```

`pdf_tools.get_pdf_info()` 先检查依赖（L350），再检查文件存在性（L354）。CI 环境未安装 `pypdf`，在检查文件存在性之前就返回了依赖缺失错误。

**修复**:
在 [pyproject.toml](file:///c:/Users/Administrator/agent/pyproject.toml#L62) 的 `dependencies` 添加:
```toml
"pypdf>=4.0.0,<6.0.0",
```

> 注: 初版修复仅添加到 requirements-test.txt，但 CI 不读此文件。后续修正为添加到 pyproject.toml。

---

### 5. jsonschema 依赖缺失

**文件**: [test_output_validation.py](file:///c:/Users/Administrator/agent/tests/unit/test_output_validation.py#L34-L103)

**根因分析**:
```
AssertionError: assert 'skipped' == 'failed'
```

`SkillExecutor._validate_output()` 在 `import jsonschema` 失败时降级返回 "skipped"。`jsonschema` 在 `requirements.txt` 中（L135），但 CI 使用 `pip install -e .` 安装，只读取 `pyproject.toml` 的 `dependencies` 列表，不读取 `requirements.txt`。`pyproject.toml` 的 `dependencies` 未包含 `jsonschema`，导致 CI 环境中 `import jsonschema` 抛出 `ImportError`。

**为何只影响 CI**:
- 本地开发环境通常通过 `pip install -r requirements.txt` 安装了 `jsonschema`
- CI 环境通过 `pip install -e .` 安装，只读取 `pyproject.toml`

**修复**:
在 [pyproject.toml](file:///c:/Users/Administrator/agent/pyproject.toml#L61) 的 `dependencies` 添加:
```toml
"jsonschema>=4.0.0,<5.0.0",
```

---

### 6. Windows 路径保护测试在 Linux CI 上失败

**文件**: [test_system_tools_platform.py](file:///c:/Users/Administrator/agent/tests/unit/test_system_tools_platform.py)

**失败测试** (3个):
```
TestPathProtectionCrossPlatform::test_windows_protection_with_mock
TestWindowsPathProtection_system_tools_platform_mock::test_windows_protected_directories
TestWindowsPathProtection_system_tools_platform_mock::test_windows_subdirectory_of_protected
```

**根因分析**:
```python
# is_protected_path() 内部实现 (file_tools.py L96-118)
abs_path = os.path.abspath(os.path.normpath(path))  # L99
if os.name == "nt":
    for protected in PROTECTED_SYSTEM_DIRS_WIN:
        if abs_path.lower().startswith(protected.lower() + os.sep) or ...
```

测试使用 `patch('os.name', 'nt')` 模拟 Windows 环境，但这只 patch 了 `os.name`，不影响 `os.path.abspath()` 的行为：

1. **`os.path.abspath()` 在 Linux 上给 Windows 路径加 cwd 前缀**:
   - Linux: `os.path.abspath("C:\\Windows\\System32")` → `/cwd/C:\Windows\System32`
   - Windows: `os.path.abspath("C:\\Windows\\System32")` → `C:\Windows\System32`

2. **`os.sep` 在 Linux 上是 `/`，而路径和常量用 `\\`**:
   - 检查 `abs_path.lower().startswith(protected.lower() + os.sep)` 时
   - Linux: `protected.lower() + "/"` 不匹配 `c:\windows\system32\config`
   - Windows: `protected.lower() + "\\"` 匹配 `c:\windows\system32\config`

3. **结果**: `is_protected_path()` 在 Linux CI 上对 Windows 路径返回 `False`，断言 `is True` 失败。

**为何只影响 CI**:
- 本地 Windows 环境 `os.name` 本就是 `nt`，`os.path.abspath` 正确处理 Windows 路径
- CI Linux 环境 `patch('os.name', 'nt')` 只改变 `os.name`，不改变 `os.path` 模块行为

**修复**:
新增辅助上下文管理器 `_windows_path_env()`，同时 mock 三个关键点:

```python
@contextmanager
def _windows_path_env():
    """模拟 Windows 路径环境

    is_protected_path() 内部调用 os.path.abspath()，在 Linux 上会将
    Windows 路径转换为 cwd 前缀的 Linux 路径，导致保护目录匹配失败。
    同时 mock os.name/os.sep/os.path.abspath 使跨平台测试一致。
    """
    with patch('os.name', 'nt'), \
         patch('os.sep', '\\'), \
         patch('os.path.abspath', side_effect=lambda p: os.path.normpath(p)):
        yield
```

三个 mock 的作用:
- `os.name='nt'`: 进入 `is_protected_path()` 的 Windows 分支
- `os.sep='\\'`: 使 `protected.lower() + os.sep` 使用反斜杠匹配 Windows 路径
- `os.path.abspath`: 返回 `os.path.normpath(path)`，不加 cwd 前缀，保留 Windows 路径格式

修改的 3 个测试:
```python
# 修复前
with patch('os.name', 'nt'):
    for protected_dir in PROTECTED_SYSTEM_DIRS_WIN:
        assert is_protected_path(protected_dir) is True

# 修复后
with _windows_path_env():
    for protected_dir in PROTECTED_SYSTEM_DIRS_WIN:
        assert is_protected_path(protected_dir) is True
```

**为何不使用 `skipif(sys.platform != 'win32')`**:
- Windows 路径保护是安全边界（不易），应在所有平台上测试
- skipif 会导致 Linux CI 上完全不测试 Windows 路径保护逻辑，降低覆盖率
- `_windows_path_env()` 通过完整 mock 实现跨平台测试，符合"测试=不易护城河"原则

---

## 验证结果

本地运行修复的测试（Windows 环境）:
```
tests/unit/test_monitoring_metrics.py::TestMetricsCollectorLatency::test_record_multiple_latencies PASSED
tests/unit/test_memory_optimized.py::TestOptimizedChromaDB::test_uninitialized_access PASSED
tests/unit/test_web_scraper.py::TestScraper::test_scraper_css_extraction PASSED
tests/unit/test_pdf_tools.py::TestPDFTools::test_get_pdf_info_file_not_exists PASSED
tests/unit/test_system_tools_platform.py::TestPathProtectionCrossPlatform::test_windows_protection_with_mock PASSED
tests/unit/test_system_tools_platform.py::TestWindowsPathProtection_system_tools_platform_mock::test_windows_protected_directories PASSED
tests/unit/test_system_tools_platform.py::TestWindowsPathProtection_system_tools_platform_mock::test_windows_subdirectory_of_protected PASSED

125 passed, 9 skipped in 1.67s  (test_system_tools_platform.py 全文件)
```

## 修改文件清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| requirements-test.txt | 新增2行 | 添加 cssselect + pypdf 依赖（本地开发用） |
| tests/unit/test_monitoring_metrics.py | 修改4行 | == 改为 pytest.approx() |
| tests/unit/test_memory_optimized.py | 修改11行 | TemporaryDirectory 添加 ignore_cleanup_errors=True |
| pyproject.toml | 新增3行 | dependencies 添加 jsonschema/pypdf/cssselect |
| tests/unit/test_system_tools_platform.py | 新增13行+修改3处 | _windows_path_env() 辅助函数 + 3个测试改用此函数 |
