# 云枢系统兼容性说明

## 概述

本文档详细描述了云枢系统的兼容性要求和支持情况，确保系统在不同环境下稳定运行。

---

## 一、Python 版本兼容性

### 支持的版本

| 版本 | 状态 | 说明 |
|------|------|------|
| Python 3.8 | ✅ 支持 | LTS 版本，完整测试覆盖 |
| Python 3.9 | ✅ 支持 | 完整测试覆盖 |
| Python 3.10 | ✅ 支持 | 推荐版本，完整测试覆盖 |
| Python 3.11 | ✅ 支持 | 完整测试覆盖 |
| Python 3.12 | ✅ 支持 | 完整测试覆盖 |

### 版本检查

系统启动时会自动检查 Python 版本，不兼容版本会抛出 `RuntimeError`：

```python
from agent.utils import assert_python_version

assert_python_version()
```

### 版本特性差异

| Python 版本 | 特性差异 | 影响模块 |
|------------|---------|---------|
| 3.8 | `typing.Literal` 需从 `typing_extensions` 导入 | 类型注解 |
| 3.9+ | `typing.Literal` 内置 | 类型注解 |
| 3.10+ | `match`/`case` 语法 | 条件分支 |
| 3.11+ | `tomllib` 内置 | 配置文件解析 |
| 3.12+ | 更严格的类型检查 | 类型注解 |

---

## 二、操作系统兼容性

### 支持的平台

| 平台 | 状态 | 说明 |
|------|------|------|
| Windows 10/11 | ✅ 支持 | 完整功能 |
| Ubuntu 20.04+ | ✅ 支持 | 完整功能 |
| Debian 10+ | ✅ 支持 | 完整功能 |
| macOS 10.15+ | ⚠️ 有限支持 | 部分功能受限 |

### 平台特定依赖

#### Windows 特有依赖

```txt
wmi>=1.5.0          # Windows Management Instrumentation
pythoncom>=0.0.1    # COM 接口支持
pywin32>=300        # Windows API 封装
pypiwin32>=223      # pywin32 的 pip 包
```

#### Linux 特有依赖

```txt
# 需要手动安装的系统依赖
# sudo apt-get install python3-dev libasound2-dev portaudio19-dev
# sudo apt-get install espeak libespeak-dev  # 用于 pyttsx3
```

### 平台功能差异

| 功能 | Windows | Linux | macOS |
|------|---------|-------|-------|
| 语音合成 (TTS) | ✅ Windows SAPI | ✅ eSpeak | ⚠️ 有限 |
| 语音识别 | ✅ 完整 | ✅ 完整 | ✅ 完整 |
| 屏幕捕获 | ✅ 完整 | ✅ 完整 | ⚠️ 有限 |
| 窗口管理 | ✅ 完整 | ⚠️ 有限 | ⚠️ 有限 |
| 系统传感器 | ✅ 完整 | ✅ 完整 | ⚠️ 有限 |
| 硬件监控 | ✅ WMI | ✅ sysfs | ⚠️ 有限 |

---

## 三、依赖版本锁定

### 锁定文件

系统使用以下文件管理依赖版本：

| 文件 | 用途 | 更新方式 |
|------|------|---------|
| `pyproject.toml` | 依赖声明与版本约束 | 手动编辑 |
| `requirements.txt` | 锁定的依赖版本 | `pip-compile` 生成 |

### 生成锁定文件

```bash
# 安装 pip-tools
pip install pip-tools

# 生成锁定文件
pip-compile --output-file=requirements.txt pyproject.toml

# 安装锁定版本
pip install -r requirements.txt
```

### 依赖版本策略

- **核心依赖**：固定主版本，允许次版本更新（如 `>=2.0.0,<2.5.0`）
- **测试依赖**：允许较大范围更新
- **平台特定依赖**：使用环境标记区分

---

## 四、兼容性检查 API

### 检查函数

```python
from agent.utils import (
    check_compatibility,
    get_compatibility_report,
    is_python_version_compatible,
    is_platform_supported,
    get_python_version_string,
    get_platform,
)

# 获取兼容性状态
result = check_compatibility()
print(result)
# {
#     'python_version': '3.11.2',
#     'python_compatible': True,
#     'platform': 'Windows',
#     'platform_supported': True,
#     'known_issues': {...},
#     ...
# }

# 获取兼容性报告
print(get_compatibility_report())
```

### 平台检测

```python
from agent.utils import get_platform, import_with_fallback

platform = get_platform()  # 'Windows' 或 'Linux'

# 条件导入平台特定模块
wmi = import_with_fallback('wmi', fallback_value=None)
if wmi:
    # Windows 特定逻辑
    pass
```

---

## 五、已知问题与解决方案

### Windows

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| `wmi` 模块导入失败 | 缺少 pywin32 | `pip install pywin32` |
| 语音合成无声音 | 未安装语音引擎 | 安装 Windows 语音包 |

### Linux

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| `pyttsx3` 无声音 | 缺少 eSpeak | `sudo apt-get install espeak` |
| `pyautogui` 无法控制鼠标 | 缺少 X11 依赖 | `sudo apt-get install python3-xlib` |
| 权限问题 | 需要 sudo | 运行时添加 sudo 或调整权限 |

### macOS

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| 屏幕捕获权限 | 安全设置限制 | 在系统设置中授权 |
| 语音合成 | 缺少语音引擎 | 使用 `pyttsx3` 的 NSSpeechSynthesizer 后端 |

---

## 六、测试覆盖

### CI/CD 测试矩阵

| Python 版本 | Windows | Ubuntu |
|------------|---------|--------|
| 3.8 | ✅ | ✅ |
| 3.9 | ✅ | ✅ |
| 3.10 | ✅ | ✅ |
| 3.11 | ✅ | ✅ |
| 3.12 | ✅ | ✅ |

### 测试类型

- **单元测试**：覆盖核心功能，多版本多平台运行
- **集成测试**：验证模块间协作
- **性能测试**：验证性能稳定性
- **兼容性测试**：验证版本和平台兼容性

---

## 七、迁移指南

### 从 Python 3.8 升级到 3.12

1. 检查 `typing.Literal` 和 `typing.Union` 的使用
2. 更新 `setup.py` 到 `pyproject.toml`
3. 测试所有平台特定代码
4. 重新生成 `requirements.txt`

### 跨平台迁移

1. 使用 `platform.system()` 检测平台
2. 使用条件导入处理平台特定模块
3. 测试所有平台相关功能

---

## 八、版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2024-01 | 初始版本，支持 Python 3.8-3.10 |
| v1.1 | 2024-06 | 添加 Python 3.11 支持 |
| v2.0 | 2025-01 | 添加 Python 3.12 支持，重构兼容性模块 |

---

## 九、联系方式

如有兼容性问题，请提交 Issue 到 GitHub 仓库。