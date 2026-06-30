# 边界覆盖扫描报告（降级）

- **生成时间**：2026-06-28T09:06:13.347514
- **Trace ID**：`0cfe34e1d5cc48a9`
- **状态**：❌ 扫描失败

## 错误信息

```
ImportError: PyYAML 未安装，请运行: pip install pyyaml [error_code=DEPENDENCY_MISSING]
```

## 错误堆栈

```
Traceback (most recent call last):
  File "C:\Users\Administrator\agent\scripts\check_boundary_coverage.py", line 140, in load
    import yaml
ModuleNotFoundError: No module named 'yaml'

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "C:\Users\Administrator\agent\scripts\check_boundary_coverage.py", line 861, in main
    config = ConfigLoader(config_path).load()
  File "C:\Users\Administrator\agent\scripts\check_boundary_coverage.py", line 142, in load
    raise ImportError(
    ...<2 lines>...
    ) from e
ImportError: PyYAML 未安装，请运行: pip install pyyaml [error_code=DEPENDENCY_MISSING]

```

## 处置建议

1. 检查 `tests/boundary_config.yaml` 配置文件是否存在且格式正确
2. 确认 PyYAML 已安装：`pip install pyyaml`
3. 确认 `tests/` 目录存在且包含 `test_*.py` 文件
4. 如问题持续，请联系平台研发组

---
_降级报告：扫描过程中发生异常，主报告未能生成_