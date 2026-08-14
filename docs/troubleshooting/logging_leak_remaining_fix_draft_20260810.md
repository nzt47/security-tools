# logging 泄漏治理遗留项 — 修复方案草稿（2026-08-10）

> 性质：基于 [logging_leak_scan_report_20260810.md](logging_leak_scan_report_20260810.md) §7 遗留项的修复方案草稿（**待确认后实施**）
> 范围：webhook_server.py 编码问题 / agent·memory tests conftest 兜底 / search_engine_test.py 模块级 basicConfig

---

## 遗留项 1：scripts/webhook_server.py 编码问题（SyntaxError）

### 根因

`scripts/webhook_server.py:56` bytes 字面量 `b"""..."""` 内含非 ASCII 字符（`✅`、`🧪` 等 emoji/中文）：

```python
# L56-78 修复前
self.wfile.write(b"""
<!DOCTYPE html>
<html>
...
<h1>🧪 Webhook Test Server</h1>
...
<p><strong>✅ Server is running!</strong></p>
...
""")
```

Python 3 中 bytes 字面量**只能包含 ASCII**，非 ASCII 字符触发 `SyntaxError: bytes can only contain ASCII literal characters`。

### 影响

1. **该文件无法 import/运行**（语法错误在编译期抛出）——即当前 `webhook_server.py` 是死代码
2. 扫描器 `check_logging_disable_leak.py` 对其 AST 解析失败，在报告中标记"跳过"（人工核查已确认无 logging 全局状态调用，安全）

### 修复方案 A（推荐）：bytes 字面量改为 str + encode

保持 HTML 内容不变，仅调整字面量类型：

```python
# L56 修复后
self.wfile.write("""
<!DOCTYPE html>
<html>
...
<h1>🧪 Webhook Test Server</h1>
...
<p><strong>✅ Server is running!</strong></p>
...
""".encode("utf-8"))
```

- 改动点：开头 `b"""` → `"""`，结尾 `""")` → `""".encode("utf-8"))`（其余内容不动）
- 语义等价：响应体仍为 bytes（UTF-8 编码），浏览器正常渲染
- 收益：文件恢复可运行，扫描器恢复对该文件的 AST 覆盖

### 修复方案 B（备选）：非 ASCII 字符替换为 ASCII

```python
# 例如
<h1>🧪 Webhook Test Server</h1>  →  <h1>[TEST] Webhook Test Server</h1>
<p><strong>✅ Server is running!</strong></p>  →  <p><strong>Server is running!</strong></p>
```

- 保留 `b"""` 语义，但改动内容（展示文案降级）

### 验证清单

| 项 | 命令/标准 |
|---|---|
| 语法 | `python -m py_compile scripts/webhook_server.py` 通过 |
| 运行 | `python scripts/webhook_server.py` 可启动（Ctrl+C 停止） |
| 扫描覆盖 | `python scripts/check_logging_disable_leak.py --root .` 不再显示"跳过"该文件 |
| 回归 | 手动 POST 测试 webhook 接收（`test_webhook_integration.py` 若存在） |

---

## 遗留项 2：agent/tests、memory/tests 无 conftest 兜底

### 现状（2026-08-10 核查）

| 目录 | conftest.py | 运行入口 |
|---|---|---|
| agent/tests/ | ❌ 无 | ❌ 无 workflow/脚本引用（不在 observability-ci 分片 root=tests） |
| memory/tests/ | ❌ 无 | ❌ 同上 |

- 两目录模块级 `basicConfig`（无 force）当前**无兜底**，但因无 CI 运行入口，实际风险暴露面为 0
- agent/tests/ 已有归档计划（feature/archive-agent-tests，归档至 docs/archive/agent_tests_20260810）

### 方案

**方案 a（推荐，最小动作）**：维持现状。agent/tests 走既有归档计划；memory/tests 若长期无人运行，同样建议归档或删除。

**方案 b（启用时才需要）**：若未来将任一目录纳入 CI，必须先补最小 conftest（root 状态快照/恢复，对齐 tests/conftest.py 模式）：

```python
# <目录>/conftest.py（启用时添加）
import logging
import pytest


@pytest.fixture(autouse=True)
def _isolate_logging():
    _root = logging.getLogger()
    _saved_handlers = _root.handlers[:]
    _saved_level = _root.level
    _saved_disable = logging.root.manager.disable
    yield
    _root.handlers = _saved_handlers
    _root.setLevel(_saved_level)
    logging.root.manager.disable = _saved_disable
```

### 触发条件

仅当下列任一成立时才需落地：目录出现 pytest 运行入口（workflow/runner）、或出现 caplog/assertLogs 敏感测试。

---

## 遗留项 3：tests/search_engine_test.py:20 模块级 basicConfig（可选）

- 现状：进入 pytest 收集（`_test.py` 后缀），模块级 `basicConfig(level=...)` 无 force → root 已有 handler（tests/conftest.py L100）时是 **no-op**；即使生效也被 conftest 恢复
- **建议：不动**（当前安全；若未来改 force=True，扫描器与防线会拦截）

---

## 实施优先级汇总

| 项 | 优先级 | 建议 |
|---|---|---|
| webhook_server.py 编码 | 🟡 中 | 方案 A（str + encode），随手可修、消除死代码 |
| agent/tests、memory/tests conftest | 🟢 低 | 维持现状（方案 a）；归档或启用时补最小 conftest |
| search_engine_test.py basicConfig | 🟢 低 | 保持不动 |
