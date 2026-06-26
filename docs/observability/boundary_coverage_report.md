# 边界覆盖扫描报告（降级）

- **生成时间**：2026-06-27T00:51:34.219875
- **Trace ID**：`e8f7e8d9473847f9`
- **状态**：❌ 扫描失败

## 错误信息

```
FileNotFoundError: 边界覆盖配置文件不存在: C:\Users\Administrator\agent\tests\boundary_config.yaml [error_code=BOUNDARY_CONFIG_NOT_FOUND]
```

## 错误堆栈

```
Traceback (most recent call last):
  File "C:\Users\Administrator\agent\scripts\check_boundary_coverage.py", line 861, in main
    config = ConfigLoader(config_path).load()
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\Administrator\agent\scripts\check_boundary_coverage.py", line 135, in load
    raise FileNotFoundError(
FileNotFoundError: 边界覆盖配置文件不存在: C:\Users\Administrator\agent\tests\boundary_config.yaml [error_code=BOUNDARY_CONFIG_NOT_FOUND]

```

## 处置建议

1. 检查 `tests/boundary_config.yaml` 配置文件是否存在且格式正确
2. 确认 PyYAML 已安装：`pip install pyyaml`
3. 确认 `tests/` 目录存在且包含 `test_*.py` 文件
4. 如问题持续，请联系平台研发组

---
_降级报告：扫描过程中发生异常，主报告未能生成_