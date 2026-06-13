# 浏览器状态泄漏 Bug 修复与极端边界测试总结

**生成时间**: 2026-06-09
**维护团队**: Agent Core Team
**关联文件**:
- 源代码: [agent/system_tools.py](file:///c:/Users/Administrator/agent/agent/system_tools.py)
- 测试文件: [tests/unit/test_system_tools_extreme_edge_cases.py](file:///c:/Users/Administrator/agent/tests/unit/test_system_tools_extreme_edge_cases.py)

---

## 一、Bug 概述

### 1.1 Bug 名称
`_browser_instance` 状态泄漏 (Browser Instance State Leak)

### 1.2 严重程度
**P1 - 高** — 可能导致浏览器功能完全不可用，且错误排查困难

### 1.3 影响范围
- 所有调用 `get_browser()` 的功能
- 包括 `browser_navigate`、`browser_screenshot`、`browser_close`

### 1.4 发现时间
2026-06-09，在覆盖率审查过程中发现

---

## 二、Bug 详细分析

### 2.1 原始代码

```python
def get_browser():
    """获取或创建无头浏览器实例（懒加载）"""
    global _browser_instance
    if _browser_instance is None:
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            opts = Options()
            opts.add_argument("--headless=new")
            # ... 更多 add_argument ...
            _browser_instance = webdriver.Chrome(options=opts)  # ✅ Chrome 启动成功
            logger.debug(f"Chrome浏览器实例创建成功...")

            page_load_timeout = 15
            _browser_instance.set_page_load_timeout(page_load_timeout)  # ❌ 失败！
            # _browser_instance 已被赋值为部分初始化的实例, 但此处抛异常

            try:
                window_handles = _browser_instance.window_handles  # 不会执行
                logger.debug(...)
            except Exception as handle_e:
                logger.debug(...)

            logger.info("无头浏览器已成功启动")  # 不会执行
        except ImportError:
            logger.warning("selenium 未安装...")
            return None
        except Exception as e:  # 捕获 set_page_load_timeout 抛的异常
            logger.warning(f"无头浏览器启动失败: {e}")
            return None  # ❌ 返回 None, 但 _browser_instance 已被赋值
    return _browser_instance
```

### 2.2 问题流程

```
时间线 1: 第一次调用 get_browser()
├─ _browser_instance is None ✓ (进入 try)
├─ webdriver.Chrome(options=opts) 成功 ✓
├─ _browser_instance = <chrome_instance>  ✓ 已赋值
├─ _browser_instance.set_page_load_timeout() 抛出异常 ❌
├─ except Exception 捕获
├─ return None
└─ 此时 _browser_instance = <chrome_instance> (但已损坏)

时间线 2: 第二次调用 get_browser()
├─ _browser_instance is None?  →  False ❌ (因为 _browser_instance 不为 None)
├─ 跳过整个 if 块
├─ return _browser_instance  ← 返回部分初始化的实例！
└─ 调用方使用此实例进行导航会得到不可预期的行为
```

### 2.3 触发条件

1. `webdriver.Chrome(options=opts)` 成功启动 Chrome 进程
2. 之后任意调用失败：
   - `set_page_load_timeout(15)` 抛异常（最常见）
   - 任何后续 `opts.add_argument()` 抛异常（理论上不应发生，但防御性考虑）
3. 再次调用 `get_browser()`，将返回已损坏的实例

### 2.4 可能的原因

1. ChromeDriver 与 Chrome 浏览器版本不匹配
2. 系统资源不足，无法设置超时
3. Chrome 内部状态异常
4. Selenium 与 Python 版本不兼容

---

## 三、修复方案

### 3.1 修复后的代码

```python
def get_browser():
    """获取或创建无头浏览器实例（懒加载）"""
    global _browser_instance
    if _browser_instance is None:
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            opts = Options()
            opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--disable-gpu")
            opts.add_argument("--disable-extensions")
            opts.add_argument("--disable-file-system")
            opts.add_argument("--remote-debugging-port=0")
            _browser_instance = webdriver.Chrome(options=opts)
            logger.debug(f"Chrome浏览器实例创建成功，对象ID: {id(_browser_instance)}")

            # 关键修复: 后续配置失败时必须清理 _browser_instance，
            # 避免下次调用 get_browser 时返回部分初始化的实例。
            try:
                page_load_timeout = 15
                _browser_instance.set_page_load_timeout(page_load_timeout)
                logger.info(f"页面加载超时时间设置为 {page_load_timeout} 秒")
            except Exception as timeout_e:
                logger.warning(f"设置页面加载超时失败: {timeout_e}")
                _cleanup_browser_instance()  # 显式清理
                return None

            try:
                window_handles = _browser_instance.window_handles
                logger.debug(f"浏览器窗口句柄: {window_handles}")
            except Exception as handle_e:
                logger.debug(f"获取窗口句柄失败: {handle_e}")

            logger.info("无头浏览器已成功启动")
        except ImportError:
            logger.warning("selenium 未安装，浏览器功能不可用")
            return None
        except Exception as e:
            logger.warning(f"无头浏览器启动失败: {e}")
            # 任何启动失败均清理 _browser_instance，防止状态泄漏
            _cleanup_browser_instance()
            return None
    return _browser_instance


def _cleanup_browser_instance():
    """清理浏览器实例：尝试 quit, 然后将全局变量重置为 None。

    用于 get_browser 部分初始化失败时释放资源，避免下次调用返回
    已损坏/部分初始化的实例。
    """
    global _browser_instance
    if _browser_instance is not None:
        try:
            _browser_instance.quit()
        except Exception:
            # quit 失败不应阻止清理流程
            pass
        _browser_instance = None
```

### 3.2 修复要点

| 编号 | 修复点 | 说明 |
|------|--------|------|
| 1 | 引入 `_cleanup_browser_instance()` 辅助函数 | 统一管理清理逻辑，避免重复代码 |
| 2 | 包装 `set_page_load_timeout()` 为内部 `try/except` | 失败时立即清理并返回 None |
| 3 | 在最外层 `except` 分支也调用清理函数 | 防御性编程，防止未来其他失败点 |
| 4 | `quit()` 自身失败也不阻止清理 | 使用 `try/except: pass` 模式 |

### 3.3 修复后流程

```
时间线 1: 第一次调用 get_browser()
├─ _browser_instance is None ✓
├─ webdriver.Chrome(options=opts) 成功 ✓
├─ _browser_instance = <chrome_instance>  ✓
├─ set_page_load_timeout() 抛出异常 ❌
├─ 内部 try/except 捕获
├─ _cleanup_browser_instance() 调用
│  ├─ _browser_instance.quit() 调用（释放资源）
│  ├─ _browser_instance = None
└─ return None ✓

时间线 2: 第二次调用 get_browser()
├─ _browser_instance is None?  →  True ✓
├─ 重新尝试启动 Chrome ✓
├─ 返回新的实例
└─ 功能正常 ✓
```

---

## 四、测试用例详解

### 4.1 测试文件
[test_system_tools_extreme_edge_cases.py](file:///c:/Users/Administrator/agent/tests/unit/test_system_tools_extreme_edge_cases.py)

### 4.2 Bug 修复验证测试 (5 个)

| 测试名称 | 验证内容 | 行号 |
|----------|----------|------|
| `test_set_page_load_timeout_failure_cleans_instance` | 核心修复：失败时 `_browser_instance` 被清理为 None | 807-816 |
| `test_set_page_load_timeout_failure_calls_quit` | 失败时调用 `quit()` 释放浏览器资源 | 817-833 |
| `test_set_page_load_timeout_failure_quit_also_fails` | quit 失败时仍能清理（双层异常安全） | 834-849 |
| `test_get_browser_after_partial_init_creates_new` | 部分初始化后下次调用会创建新实例 | 850-868 |
| `test_general_startup_failure_also_cleans_instance` | webdriver.Chrome 自身抛异常时也清理 | 869-881 |

### 4.3 `_cleanup_browser_instance` 辅助函数测试 (4 个)

| 测试名称 | 验证内容 |
|----------|----------|
| `test_cleanup_with_none_instance` | None 实例清理（无操作） |
| `test_cleanup_with_valid_instance` | 有效实例清理（调用 quit） |
| `test_cleanup_with_quit_exception` | quit 抛异常时仍清理 |
| `test_cleanup_with_no_quit_method` | 没有 quit 方法的实例（不抛 AttributeError） |

---

## 五、极端边界测试用例

### 5.1 process_management 异常分支 (15 个测试)

#### 5.1.1 start_process 异常 (6 个)

| 测试名称 | 验证内容 |
|----------|----------|
| `test_start_process_subprocess_popen_exception` | Popen 抛 OSError |
| `test_start_process_filenotfound` | 程序文件不存在 |
| `test_start_process_permission_denied` | 权限被拒绝 |
| `test_start_process_with_args` | 带参数启动 |
| `test_start_process_with_cwd` | 自定义工作目录 |
| `test_start_process_args_none` | args=None 时不附加参数 |

#### 5.1.2 list_processes 异常 (4 个)

| 测试名称 | 验证内容 |
|----------|----------|
| `test_list_processes_with_psutil_exception_in_iteration` | proc.info 抛异常时跳过 |
| `test_list_processes_with_proc_info_exception` | 单个 proc 异常不影响其他 |
| `test_list_processes_with_none_name` | 名称为 None 的进程被过滤 |
| `test_list_processes_non_whitelisted_filtered` | 非白名单进程被过滤 |

#### 5.1.3 stop_process 异常 (5 个)

| 测试名称 | 验证内容 |
|----------|----------|
| `test_stop_process_access_denied` | psutil.AccessDenied 异常 |
| `test_stop_process_zombie_process` | 僵尸进程（ZombieProcess 是 NoSuchProcess 子类） |
| `test_stop_process_timeout` | TimeoutExpired 异常 |
| `test_stop_process_name_none` | 进程名为 None 时拒绝终止 |
| `test_stop_process_general_exception` | 通用异常处理 |

### 5.2 pyperclip 缺失回退分支 (12 个测试)

#### 5.2.1 get_clipboard pyperclip 回退 (5 个)

| 测试名称 | 验证内容 |
|----------|----------|
| `test_get_clipboard_pyperclip_success` | pyperclip 正常路径 |
| `test_get_clipboard_pyperclip_missing_falls_back_to_powershell` | pyperclip 缺失时回退到 PowerShell |
| `test_get_clipboard_pyperclip_missing_powershell_timeout` | PowerShell 超时 |
| `test_get_clipboard_pyperclip_missing_powershell_not_found` | PowerShell 不存在 |
| `test_get_clipboard_content_truncation` | 内容截断到 10000 字符 |

#### 5.2.2 set_clipboard pyperclip 回退 (7 个)

| 测试名称 | 验证内容 |
|----------|----------|
| `test_set_clipboard_pyperclip_success` | pyperclip 正常路径 |
| `test_set_clipboard_pyperclip_missing_falls_back_to_powershell` | pyperclip 缺失时回退到 PowerShell |
| `test_set_clipboard_pyperclip_missing_powershell_timeout` | PowerShell 超时 |
| `test_set_clipboard_too_long` | 内容超过 50000 字符 |
| `test_set_clipboard_pyperclip_missing_truncation` | PowerShell 回退时截断到 5000 字符 |
| `test_set_clipboard_pyperclip_missing_powershell_not_found` | PowerShell 不存在 |
| `test_set_clipboard_pyperclip_missing_general_exception` | 通用异常处理 |

### 5.3 跨平台边界 (1 个)

| 测试名称 | 验证内容 |
|----------|----------|
| `test_get_clipboard_truncation_at_10000` | 10001 字符精确截断到 10000 |

---

## 六、关键 Mock 技术

### 6.1 pyperclip 缺失模拟

由于 `get_clipboard` / `set_clipboard` 内部 `import pyperclip`，需要 patch `builtins.__import__`：

```python
original_import = __import__

def mock_import(name, *args, **kwargs):
    if name == 'pyperclip':
        raise ImportError("No module named 'pyperclip'")
    return original_import(name, *args, **kwargs)

with patch('builtins.__import__', side_effect=mock_import):
    with patch('subprocess.run', return_value=mock_result):
        result = get_clipboard()
```

### 6.2 selenium 启动路径模拟

需要完整模拟 `selenium.webdriver.chrome.options` 模块路径：

```python
mock_selenium = MagicMock()
mock_options_module = MagicMock()
mock_options_module.Options.return_value = mock_options
mock_selenium.webdriver.chrome = MagicMock()
mock_selenium.webdriver.chrome.options = mock_options_module

with patch.dict(sys.modules, {
    'selenium': mock_selenium,
    'selenium.webdriver': mock_selenium.webdriver,
    'selenium.webdriver.chrome': mock_selenium.webdriver.chrome,
    'selenium.webdriver.chrome.options': mock_options_module,
}):
    ...
```

### 6.3 psutil 异常模拟

```python
import psutil

# AccessDenied
mock_proc.terminate.side_effect = psutil.AccessDenied("权限不足")

# ZombieProcess (是 NoSuchProcess 的子类)
mock_proc.terminate.side_effect = psutil.ZombieProcess(1234)

# TimeoutExpired
mock_proc.terminate.side_effect = psutil.TimeoutExpired("timeout")
```

---

## 七、回归测试运行

### 7.1 单文件运行

```bash
$env:PYTHONIOENCODING="utf-8"
python -m pytest tests/unit/test_system_tools_extreme_edge_cases.py -p no:cacheprovider --no-header -v
```

**结果**: 37 个测试全部通过 ✅

### 7.2 全量覆盖率运行

```bash
$env:PYTHONIOENCODING="utf-8"
python -m pytest tests/unit/test_system_tools.py tests/unit/test_system_tools_supplement.py tests/unit/test_system_tools_final.py tests/unit/test_system_tools_ultimate.py tests/unit/test_system_tools_final_complete.py tests/unit/test_system_tools_ultimate_2.py tests/unit/test_system_tools_sandbox_browser_ultimate.py tests/unit/test_system_tools_extreme_edge_cases.py --cov=agent.system_tools --cov-report=term -p no:cacheprovider --no-header
```

**结果**:
```
agent\system_tools.py       487     32   93%
```

---

## 八、后续改进建议

### 8.1 代码层面

1. **考虑使用 `__init__` 异常安全模式**：在 `__init__` 中启动 Chrome 是异常安全反模式，建议使用工厂方法
2. **添加健康检查**：定期检查 `_browser_instance` 是否健康（如 `is_displayed()`）
3. **添加重试机制**：在启动失败时自动重试 N 次
4. **添加超时控制**：在 `get_browser` 整体上加超时（当前只 `set_page_load_timeout` 失败有处理）

### 8.2 测试层面

1. **添加 E2E 测试**：使用真实 Chrome 浏览器验证修复（CI 中需安装 Chrome）
2. **添加并发测试**：多个线程同时调用 `get_browser()` 验证线程安全
3. **添加压力测试**：连续启动/关闭 1000 次，验证无资源泄漏
4. **添加跨平台测试**：在 Linux/macOS 上验证修复（Windows 与 Unix 的 `os.path` 行为不同）

### 8.3 监控层面

1. **添加指标**：记录 `_browser_instance` 启动成功率、失败原因
2. **添加告警**：连续 N 次启动失败时发出告警
3. **添加日志**：清理操作应记录 INFO 级别日志便于排查

---

## 九、FAQ

### Q1: 为什么 `window_handles` 的 `try/except` 不需要调用 `_cleanup_browser_instance`？

A: `window_handles` 失败是良性的，不影响浏览器功能。`get_browser` 仍应返回可用实例。这是设计选择。

### Q2: `quit()` 失败时，浏览器进程会怎样？

A: 浏览器进程可能会成为孤儿进程。建议未来添加定期清理机制（如 `psutil` 检查并 kill）。

### Q3: 为什么 `set_page_load_timeout` 失败如此关键，需要立即清理？

A: 因为这是 webdriver API 的一个核心方法。失败意味着 webdriver 与 Chrome 通信异常，后续所有操作都会失败。

### Q4: 这个 Bug 修复会影响性能吗？

A: 不会。`_cleanup_browser_instance()` 只在失败时调用，正常路径无额外开销。

### Q5: 修复后是否需要更新现有调用方代码？

A: 不需要。修复是向后兼容的。`get_browser()` 的返回值类型不变（可能是 `WebDriver` 实例或 `None`）。
