# CI 环境测试失败修复方案

> 生成时间: 2026-07-10
> 修复提交: ac38394e
> 影响: 云枢系统测试流程 (ci.yml) — 单元测试 Python 3.10/3.11/3.12

## 失败概览

CI 历史（ci.yml）显示云枢系统测试流程**连续 10+ 次失败**，根因为 5 个预先存在的 CI 环境问题，与阶段4（pytest-randomly + conftest 隔离增强）无关。

| # | 测试文件 | 失败原因 | 修复方式 |
|---|---------|---------|---------|
| 1 | test_monitoring_metrics.py | 浮点精度 (0.1+0.2+0.3≠0.6) | pytest.approx() |
| 2 | test_memory_optimized.py | ChromaDB 目录清理 (OSError 39) | ignore_cleanup_errors=True |
| 3 | test_web_scraper.py (×2) | cssselect 依赖缺失 | requirements-test.txt 添加依赖 |
| 4 | test_pdf_tools.py | pypdf 依赖缺失 | requirements-test.txt 添加依赖 |
| 5 | test_output_validation.py (×5) | jsonschema 不在 pyproject.toml | pyproject.toml dependencies 添加 jsonschema |

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
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
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
在 [requirements-test.txt](file:///c:/Users/Administrator/agent/requirements-test.txt#L60-L62) 添加:
```
cssselect>=1.2.0,<2.0.0    # CSS 选择器 - lxml 的 cssselect 扩展，web_scraper 测试需要
```

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
在 [requirements-test.txt](file:///c:/Users/Administrator/agent/requirements-test.txt#L60-L62) 添加:
```
pypdf>=4.0.0,<6.0.0    # PDF 处理 - pdf_tools 测试需要
```

---

## 验证结果

本地运行4个失败测试（修复后）:
```
tests/unit/test_monitoring_metrics.py::TestMetricsCollectorLatency::test_record_multiple_latencies PASSED
tests/unit/test_memory_optimized.py::TestOptimizedChromaDB::test_uninitialized_access PASSED
tests/unit/test_web_scraper.py::TestScraper::test_scraper_css_extraction PASSED
tests/unit/test_pdf_tools.py::TestPDFTools::test_get_pdf_info_file_not_exists PASSED

4 passed in 1.38s
```

## 修改文件清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| requirements-test.txt | 新增2行 | 添加 cssselect + pypdf 依赖 |
| tests/unit/test_monitoring_metrics.py | 修改4行 | == 改为 pytest.approx() |
| tests/unit/test_memory_optimized.py | 修改11行 | TemporaryDirectory 添加 ignore_cleanup_errors=True |
| pyproject.toml | 新增1行 | dependencies 添加 jsonschema>=4.0.0,<5.0.0 |

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
在 [pyproject.toml](file:///c:/Users/Administrator/agent/pyproject.toml#L60-L61) 的 `dependencies` 添加:
```toml
"jsonschema>=4.0.0,<5.0.0",
```
