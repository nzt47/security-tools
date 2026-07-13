# hasattr 潜在隐患检查报告

**日期**: 2026-07-13
**检查范围**: `agent/network/config_manager.py`
**检查目的**: 根据开发总结中的经验教训，检查是否有其他类似 `hasattr` 吞异常的潜在隐患

---

## 检查结果

共发现 **13 处**相关代码，分为 3 类：

| 类型 | 数量 | 风险等级 | 已修复 |
|------|------|----------|--------|
| `hasattr` 使用 | 4 处 | 1 处中风险 + 3 处低风险 | 1 处 |
| `getattr` 使用 | 3 处 | 极低 | 无需修复 |
| 过宽 `except Exception` | 6 处 | 2 处中风险 + 4 处合理 | 2 处 |

---

## 已修复的隐患

### 隐患 1: L1152 `hasattr` 不在 try-except 内（中风险）

**位置**: `apply_to_app` 方法中的 LLM 配置块

**问题**:
```python
# 修复前：hasattr 不在 try-except 内
if app_instance and hasattr(app_instance, 'configure_llm'):
    # ... 60 行 LLM 配置逻辑
```

Python 3 的 `hasattr` 只捕获 `AttributeError`。如果 `configure_llm` 是 `@property` 且 getter 抛出 `RuntimeError`/`ConnectionError` 等非 `AttributeError` 异常，`hasattr` 会传播异常，导致 `apply_to_app` 整个方法崩溃。

**修复**:
```python
# 修复后：用 try-except 保护 hasattr 检查
try:
    _has_configure_llm = app_instance and hasattr(app_instance, 'configure_llm')
except Exception:
    _has_configure_llm = False

if _has_configure_llm:
    # ... 60 行 LLM 配置逻辑
```

**实际风险**: 低（`configure_llm` 通常是普通方法而非 property），但修复后更健壮。

---

### 隐患 2: L264/L274 `_save_secure`/`_load_secure` 过宽 `except Exception`（中风险）

**位置**: 加密存储的保存/加载方法

**问题**:
```python
# 修复前：捕获所有异常（包括 SystemExit、KeyboardInterrupt）
except Exception as e:
    logger.error(...)
```

`except Exception` 会捕获 `SystemExit` 和 `KeyboardInterrupt` 的子类（如 `SystemExit` 本身不继承 `Exception`，但某些库可能抛出继承 `Exception` 的严重错误），可能隐藏加密库的严重内部错误。

**修复**:
```python
# 修复后：只捕获预期异常
except (OSError, ValueError, TypeError, RuntimeError) as e:
    logger.error(...)
```

**实际风险**: 中（加密失败时静默降级为明文存储或默认值，可能导致安全问题）。

---

## 无需修复的项

### L1106/L1119/L1143: `hasattr` 在 try-except 内（低风险）

```python
try:
    if app_instance and hasattr(app_instance, '_web_http'):
        # ...
except Exception as e:
    logger.warning(...)
```

这些 `hasattr` 调用都在 `try-except Exception` 块内，即使 `hasattr` 传播异常也会被捕获。**无需修复**。

### L613/L1107/L1111: `getattr` 使用（极低风险）

```python
handler = getattr(search_engine, f'_search_{engine_type}', None)
old_timeout = getattr(app_instance._web_http, 'timeout', None)
```

`getattr` 有默认值参数时只捕获 `AttributeError`，行为与 `hasattr` 一致。但这些调用获取的都是普通属性（非 property），且都在 try-except 内。**无需修复**。

### L1114/L1138/L1148/L1206: `apply_to_app` 中的 `except Exception`（合理）

```python
try:
    # 应用 HTTP 配置
except Exception as e:
    logger.warning(...)
```

这些是合理的防御性编程——不应该因为一个配置项失败而中断整个应用启动。**无需修复**。

---

## 修复验证

- 143 个单元测试全部通过 ✓
- 248 个测试（含 snapshot + 性能基准）无回归 ✓
- 覆盖率 95% 不变 ✓
