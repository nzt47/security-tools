# kwarg-scanner

> Python 关键字参数冲突风险静态扫描器 — AST 级别检测 `func(explicit_kwarg=x, **dict)` 模式中 dict 含同名键的冲突风险

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-0-green.svg)](#)

## 问题背景

Python 中 `**dict` 展开与显式关键字参数混用时，如果 dict 含有与显式参数同名的键，会触发 `TypeError: got multiple values for argument`。

```python
# BUG: 如果 payload 含 "trace_id" → TypeError
def emit(action, *, trace_id=None, **kw):
    ...

emit("x", trace_id="t", **(payload or {}))  # 💥 冲突！
```

这类 bug 在运行时才暴露，难以通过类型检查发现。`kwarg-scanner` 通过 AST 静态分析在提交前拦截。

## 安装

```bash
pip install kwarg-scanner
# 或本地开发安装
pip install -e packages/kwarg_scanner/
```

## 快速开始

### CLI 使用

```bash
# 扫描项目（默认报告所有风险）
kwarg-scan --path src/

# 仅扫描 HIGH 风险（CI 拦截用，exit code 1 = 有高风险）
kwarg-scan --path src/ --min-risk HIGH

# 生成 JSON 报告
kwarg-scan --path src/ --format json --output report.json

# 排除额外目录
kwarg-scan --path src/ --exclude venv,node_modules,build
```

### 编程 API

```python
from kwarg_scanner import (
    KwargScanner, ScanConfig, RiskLevel,
    scan_directory, scan_file,
    format_text_report, format_json_report,
)

# 方式 1: 便捷函数
findings = scan_directory("src/")
for f in findings:
    if f.risk_level == "HIGH":
        print(f"  {f.file}:{f.lineno} {f.reason}")

# 方式 2: 配置化扫描
config = ScanConfig(
    min_risk=RiskLevel.HIGH,
    exclude_dirs={"venv", ".git", "node_modules"},
    filtered_name_prefixes=("safe_", "filtered_", "clean_"),
)
scanner = KwargScanner(config)
findings = scanner.scan("src/")

# 生成报告
print(format_text_report(findings))
json_str = format_json_report(findings)

# CI 集成: HIGH 风险阻断
high_count = sum(1 for f in findings if f.risk_level == "HIGH")
if high_count > 0:
    raise SystemExit(f"阻断: 发现 {high_count} 处 HIGH 风险")
```

### Pre-commit Hook

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: kwarg-scan
        name: kwarg-scanner (HIGH 风险拦截)
        entry: kwarg-scan --path src/ --min-risk HIGH
        language: system
        pass_filenames: false
        always_run: true
```

### GitHub Actions

```yaml
# .github/workflows/kwarg-check.yml
name: 关键字参数冲突扫描
on: [push, pull_request]
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.10'
      - run: pip install kwarg-scanner
      - run: kwarg-scan --path src/ --min-risk HIGH --format json --output report.json
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: kwarg-scan-report
          path: report.json
```

## 风险等级

| 等级 | 条件 | 处理 |
|------|------|------|
| **HIGH** | 同文件函数，显式 kwarg 与函数参数同名，`**variable` 展开 | 必须修复 |
| **MEDIUM** | 外部函数签名已知，`**kwargs` 转发可能冲突 | 建议审查 |
| **LOW** | 已过滤变量（`safe_` 前缀）或字典推导式含条件 | 无需处理 |

## 修复模板

```python
# 1. 定义保留键集合（与显式参数同名）
_RESERVED = {"trace_id", "duration_ms", "level", "action", "module_name"}

# 2. 过滤 **kwargs 中的保留键
safe_kwargs = {k: v for k, v in kwargs.items() if k not in _RESERVED}

# 3. 使用过滤后的变量展开
func(explicit_kwarg=value, **safe_kwargs)
```

扫描器通过变量名 `safe_`/`filtered_`/`clean_` 前缀识别已过滤变量，避免误报。

## API 参考

### `KwargScanner(config=None)`

| 方法 | 说明 |
|------|------|
| `scan(path) → List[ConflictFinding]` | 扫描文件或目录 |
| `scan_file(filepath) → List[ConflictFinding]` | 扫描单个文件 |

### `ScanConfig`

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `min_risk` | `RiskLevel` | `LOW` | 最低报告风险等级 |
| `exclude_dirs` | `Set[str]` | `{"__pycache__", ...}` | 排除的目录名 |
| `filtered_name_prefixes` | `Tuple[str, ...]` | `("safe_", "filtered_", "clean_")` | 已过滤变量名前缀 |
| `filtered_name_suffixes` | `Tuple[str, ...]` | `("_safe", "_filtered", "_clean")` | 已过滤变量名后缀 |
| `enable_logging` | `bool` | `False` | 是否输出结构化 JSON 日志 |

### `ConflictFinding`

| 属性 | 类型 | 说明 |
|------|------|------|
| `file` | `str` | 文件路径 |
| `lineno` | `int` | 行号 |
| `col` | `int` | 列号 |
| `func_name` | `str` | 被调函数名 |
| `explicit_kwargs` | `List[str]` | 显式关键字参数名 |
| `spread_expr` | `str` | `**展开` 表达式文本 |
| `risk_level` | `str` | 风险等级 `"HIGH"`/`"MEDIUM"`/`"LOW"` |
| `reason` | `str` | 风险原因说明 |
| `conflicting_params` | `List[str]` | 冲突参数名列表 |
| `suggested_fix` | `str` | 修复建议 |

## 特性

- **零依赖**: 仅使用 Python 标准库 (ast, json, argparse)
- **AST 级别**: 精确解析语法树，不是正则匹配
- **智能识别**: 自动识别 `safe_kwargs` 等已过滤变量，减少误报
- **可配置**: 支持自定义排除目录、过滤命名模式、风险等级
- **CI 友好**: HIGH 风险返回 exit code 1，支持 JSON 报告输出
- **结构化日志**: 可选 JSON 格式日志 (trace_id, module_name, action, duration_ms)

## License

MIT
