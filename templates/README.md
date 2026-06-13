# 📋 通用模板使用说明

## 概述

本目录包含可复用的模板文件，方便新项目快速集成日志配置和测试框架。

## 📁 模板文件

```
templates/
├── logging_utils_template.py    # 日志配置模板
├── test_template.py             # 测试模板
└── README.md                    # 本说明文档
```

## 🚀 快速开始

### 1. 复制模板到新项目

```bash
# 复制日志配置模板
cp templates/logging_utils_template.py your_project/utils/logging_utils.py

# 复制测试模板
cp templates/test_template.py your_project/tests/test_logging.py
```

### 2. 配置日志系统

**方式1: 基本使用**

```python
from utils.logging_utils import setup_logging, get_logger

# 配置日志（默认 INFO 级别）
setup_logging()

# 获取日志记录器
logger = get_logger("my_app")

# 使用日志
logger.info("应用启动")
logger.debug("调试信息")
logger.warning("警告信息")
logger.error("错误信息")
```

**方式2: 调试模式**

```python
# 启用调试模式（DEBUG 级别）
setup_logging(debug_mode=True)
```

**方式3: 自定义配置**

```python
from utils.logging_utils import setup_logging, LOGGING_CONFIG

# 自定义配置
custom_config = LOGGING_CONFIG.copy()
custom_config.update({
    'default_level': 'DEBUG',
    'output': 'both',          # 同时输出到控制台和文件
    'log_file': 'app.log',
    'module_levels': {
        'my_module': 'DEBUG',
        'third_party_lib': 'WARNING',
    },
})

# 应用自定义配置
setup_logging(config=custom_config)
```

### 3. 使用安全监控器

```python
from utils.logging_utils import SafetyMonitor

# 创建监控器（单例模式建议）
monitor = SafetyMonitor(
    max_iterations_per_minute=100,      # 每分钟最大迭代
    state_stuck_threshold_seconds=10,   # 状态卡死阈值
)

# 记录迭代（检测快速循环）
if not monitor.record_iteration("my_task"):
    print("检测到异常循环！")

# 检查状态（检测状态卡死）
if not monitor.check_state("my_task", "running"):
    print("检测到状态卡死！")

# 重置监控数据
monitor.reset("my_task")  # 重置特定任务
# monitor.reset()  # 重置所有
```

### 4. 使用安全执行包装器

```python
from utils.logging_utils import safe_execute

# 正常执行
def my_task():
    return "完成"

result = safe_execute(my_task, timeout=30.0)

# 带超时和默认返回值
def slow_task():
    import time
    time.sleep(60)
    return "完成"

result = safe_execute(
    slow_task,
    timeout=10.0,
    default_return="超时了"
)

# 使用标识符（用于监控）
result = safe_execute(
    my_task,
    timeout=30.0,
    identifier="critical_task"
)
```

### 5. 运行测试

```bash
# 运行所有测试
python tests/test_logging.py

# 输出示例
======================================================
运行通用测试套件
======================================================
测试日志系统...
✅ 日志系统
测试安全监控器...
✅ 安全监控器
测试安全执行包装器...
✅ 安全执行包装器
测试模块导出...
✅ 模块导出
测试集成测试...
✅ 集成测试

======================================================
测试总结
======================================================
  日志系统                : ✅ 通过
  安全监控器              : ✅ 通过
  安全执行包装器          : ✅ 通过
  模块导出                : ✅ 通过
  集成测试                : ✅ 通过
======================================================

🎉 所有 5 个测试通过！
```

## ⚙️ 配置参数

### LOGGING_CONFIG 配置项

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `default_level` | str | 'INFO' | 默认日志级别 |
| `debug_level` | str | 'DEBUG' | 调试模式日志级别 |
| `format` | str | 见代码 | 日志格式字符串 |
| `date_format` | str | '%H:%M:%S' | 日期格式 |
| `quiet_modules` | list | [] | 需要降低日志级别的模块 |
| `module_levels` | dict | {} | 自定义模块日志级别 |
| `use_color` | bool | True | 是否启用颜色输出 |
| `output` | str | 'stdout' | 输出位置 (stdout/file/both) |
| `log_file` | str | 'app.log' | 日志文件路径 |
| `max_file_size` | int | 10MB | 日志文件大小限制 |
| `backup_count` | int | 5 | 备份日志文件数量 |

### SafetyMonitor 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_iterations_per_minute` | int | 100 | 每分钟最大迭代次数 |
| `state_stuck_threshold_seconds` | int | 10 | 状态卡死检测阈值（秒） |

### safe_execute 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `func` | callable | - | 要执行的函数 |
| `timeout` | float | 30.0 | 超时时间（秒） |
| `default_return` | any | None | 超时时的默认返回值 |
| `identifier` | str | 自动生成 | 任务标识符 |

## 🔧 自定义扩展

### 添加新的日志处理器

```python
from logging_utils import setup_logging, LOGGING_CONFIG

# 添加自定义处理器
custom_config = LOGGING_CONFIG.copy()
custom_config['handlers'] = [
    # 添加自定义处理器
    {
        'type': 'stream',
        'level': 'INFO',
    },
    {
        'type': 'file',
        'level': 'DEBUG',
        'path': 'debug.log',
    },
]

setup_logging(config=custom_config)
```

### 创建自定义监控器

```python
from logging_utils import SafetyMonitor

class MyMonitor(SafetyMonitor):
    def __init__(self):
        super().__init__(
            max_iterations_per_minute=200,
            state_stuck_threshold_seconds=30,
        )
    
    def custom_check(self, task_id):
        """自定义检查逻辑"""
        # 实现自定义检查
        pass
```

## 📊 性能考虑

### 日志系统
- 配置开销：< 10ms
- 运行时开销：< 1ms/条日志
- 内存占用：< 1MB

### 安全监控器
- 迭代检查：< 1ms
- 状态检查：< 1ms
- 内存占用：O(并发任务数)

### 安全执行包装器
- 线程创建开销：< 1ms
- 超时检测精度：±100ms

## 🧪 测试覆盖

模板测试套件覆盖：

| 测试模块 | 测试内容 |
|---------|---------|
| 日志系统 | 默认配置、调试模式、自定义配置、日志输出 |
| 安全监控器 | 正常迭代、快速循环检测、状态卡死检测、重置功能、统计信息 |
| 安全执行包装器 | 正常执行、超时保护、异常传播、标识符参数 |
| 模块导出 | 所有导出项验证 |
| 集成测试 | 日志 + 监控器 + 安全执行联合测试 |

## 📚 最佳实践

### 1. 日志使用规范

```python
# ✅ 推荐：使用描述性消息
logger.info(f"用户 {user_id} 登录成功")
logger.error(f"数据库连接失败: {error}")

# ❌ 不推荐：模糊消息
logger.info("用户登录")
logger.error("出错了")
```

### 2. 监控器使用规范

```python
# ✅ 推荐：使用唯一标识符
monitor.record_iteration(f"task_{task_id}")

# ❌ 不推荐：重复标识符
monitor.record_iteration("task")  # 多个任务共享同一个标识符
```

### 3. 安全执行规范

```python
# ✅ 推荐：设置合理超时
result = safe_execute(long_task, timeout=60.0)

# ❌ 不推荐：超时设置不合理
result = safe_execute(quick_task, timeout=0.1)  # 可能误判超时
```

### 4. 异常处理规范

```python
# ✅ 推荐：捕获特定异常
from logging_utils import TimeoutException

try:
    result = safe_execute(my_task)
except TimeoutException:
    logger.error("任务超时")
except ValueError:
    logger.error("参数错误")

# ❌ 不推荐：捕获所有异常
try:
    result = safe_execute(my_task)
except Exception:
    logger.error("出错了")  # 无法定位问题
```

## 📁 项目结构建议

```
your_project/
├── utils/
│   └── logging_utils.py      # 日志工具
├── tests/
│   └── test_logging.py       # 日志测试
├── main.py                   # 主入口
└── config.py                 # 配置文件
```

### main.py 示例

```python
"""项目主入口"""
from utils.logging_utils import setup_logging, get_logger
from config import CONFIG

# 配置日志
setup_logging(debug_mode=CONFIG.get('debug', False))
logger = get_logger("main")

def main():
    logger.info("应用启动")
    
    # 应用逻辑
    try:
        # 执行任务
        logger.info("任务执行完成")
    except Exception as e:
        logger.error(f"任务失败: {e}")
        raise

if __name__ == "__main__":
    main()
```

## ✅ 检查清单

在使用模板前，请确认：

- [ ] 已复制模板文件到项目
- [ ] 已配置 `sys.path` 或安装为包
- [ ] 已根据项目需求调整配置参数
- [ ] 已添加必要的第三方库到依赖
- [ ] 已运行测试验证功能正常

## 🤝 贡献指南

如果你有改进模板的建议，请：

1. Fork 本仓库
2. 创建功能分支
3. 提交改进
4. 创建 Pull Request

## 📝 更新记录

| 版本 | 日期 | 更新内容 |
|------|------|---------|
| v1.0 | 2024-01-15 | 初始版本 |
| v1.1 | 2024-01-16 | 添加颜色输出、文件滚动 |
| v1.2 | 2024-01-17 | 添加安全监控器、安全执行包装器 |

---

**有问题？查看代码注释或联系开发者！** 🚀
