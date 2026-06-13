# 🚀 Agent 模块 - 快速参考

## 快速启动

```bash
# 正常模式
python main.py

# 调试模式（详细日志）
python main.py --debug

# 单次对话
python main.py --chat "你好"
```

## 导入模块

```python
from agent import (
    # 核心组件
    DigitalLife,

    # 日志系统
    setup_agent_logging,

    # 安全监控
    get_safety_monitor,
    AgentSafetyMonitor,

    # 安全执行
    safe_execute,
    safe_execute_async,

    # 异常类型
    AgentTimeoutException,
    AgentLoopException,
    AgentStateStuckException,
)
```

## 日志配置

```python
# 基本配置
setup_agent_logging()

# 调试模式
setup_agent_logging(debug_mode=True)

# 获取 logger
import logging
logger = logging.getLogger("agent.my_module")
logger.info("日志消息")
```

## 安全监控

```python
# 获取监控器（单例）
monitor = get_safety_monitor()

# 记录迭代
monitor.record_iteration("task_id")  # 返回 False 表示检测到循环

# 检查状态
monitor.check_state("task_id", "processing")  # 返回 False 表示状态卡死

# 重置监控
monitor.reset("task_id")  # 重置特定任务
monitor.reset()  # 重置所有
```

## 安全执行

```python
# 同步执行（带超时）
result = safe_execute(
    func=my_function,
    timeout=30.0,
    default_return=None
)

# 异步执行
result, error = await safe_execute_async(
    func=my_async_function,
    timeout=30.0
)
```

## DigitalLife 使用

```python
from agent import DigitalLife

# 创建实例
Yunshu = DigitalLife()
Yunshu.start()

# 对话（自动使用日志和安全保护）
response = Yunshu.chat("你好")

# 查看状态
status = Yunshu.get_planning_status()
print(f"规划引擎: {status['enabled']}")
print(f"可用工具: {status['stats']['registered_tools']}")

# 停止
Yunshu.stop()
```

## 日志级别

| 级别 | 使用场景 | 输出内容 |
|------|---------|---------|
| INFO | 正常模式 | 初始化、关键步骤、成功信息 |
| DEBUG | 调试模式 | 详细流程、变量值、堆栈跟踪 |
| WARNING | 警告 | 非致命问题、降级操作 |
| ERROR | 错误 | 异常、失败、堆栈跟踪 |

## 安全阈值

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_iterations_per_minute` | 100 | 每分钟最大迭代次数 |
| `state_stuck_threshold_seconds` | 10 | 状态卡死检测阈值 |
| `CHAT_TIMEOUT_SECONDS` | 30 | 单次对话超时 |
| `PLAN_EXECUTION_TIMEOUT_SECONDS` | 60 | 计划执行超时 |

## 可视化符号

- 💬 对话请求
- 🔍 分析/查询
- 📊 统计/数据
- ✅ 成功
- ⚠️ 警告
- ❌ 错误
- ⏱️ 超时
- 🔄 循环/状态变化
- 🧠 规划/反思
- 🚀 启动/执行
- ⚡ 行动

## 测试验证

```bash
# 运行测试
python test_agent_logging.py

# 预期输出
🎉 所有 Agent 模块测试通过！
```

## 日志查看

```bash
# 所有日志
python main.py 2>&1

# 只看 agent 模块
python main.py 2>&1 | grep "agent"

# 只看规划引擎
python main.py 2>&1 | grep "planning"

# 只看 ERROR
python main.py 2>&1 | grep "ERROR"

# 保存到文件
python main.py 2>&1 | tee output.log
```

## 异常处理

所有异常都会：
1. 记录详细日志（堆栈跟踪）
2. 生成友好提示
3. 返回兜底响应
4. 不影响主程序

```python
try:
    result = Yunshu.chat("你好")
except AgentTimeoutException:
    print("处理超时")
except AgentLoopException:
    print("检测到循环")
except AgentStateStuckException:
    print("状态卡死")
```

## 文档资源

- [完整总结](file:///c:/Users/Administrator/agent/AGENT_LOGGING_AND_SAFETY_SUMMARY.md) - 详细说明
- [规划引擎总结](file:///c:/Users/Administrator/agent/PLANNING_COMPLETE_SUMMARY.md) - 规划引擎功能
- [日志指南](file:///c:/Users/Administrator/agent/PLANNING_LOGGING_GUIDE.md) - 日志使用
- [安全机制](file:///c:/Users/Administrator/agent/PLANNING_SAFETY_MECHANISM.md) - 安全保护
- [快速参考](file:///c:/Users/Administrator/agent/PLANNING_QUICK_REFERENCE.md) - 规划引擎

## 常见问题

**Q: 如何启用调试日志？**
```bash
python main.py --debug
```

**Q: 如何调整超时时间？**
```python
# main.py 中
CHAT_TIMEOUT_SECONDS = 60
```

**Q: 如何禁用安全监控？**
```python
# 不获取监控器即可
# monitor = get_safety_monitor()
```

**Q: 如何过滤日志？**
```bash
python main.py 2>&1 | grep "INFO"
python main.py 2>&1 | grep -v "DEBUG"
```

**Q: 如何查看规划引擎状态？**
```python
status = Yunshu.get_planning_status()
print(status)
```

---

**有问题？看文档！** 📚
