# 云枢规划引擎 - 使用说明

## 概述

规划引擎是云枢的核心能力增强模块，使云枢具备复杂任务的分析、规划和执行能力。

## 功能特性

- **任务分解器**：将复杂任务自动分解为可执行的子任务序列
- **执行引擎**：执行子任务、管理状态、处理错误重试
- **ReAct循环**：推理-行动-观察交替执行的完整闭环
- **反思引擎**：执行效果评估与经验学习
- **状态机**：管理任务执行状态的完整生命周期

## 快速开始

### 1. 基础使用（作为独立模块）

```python
from planning import PlanningCore

# 创建规划引擎核心
planner = PlanningCore(
    llm_service=your_llm_service,  # 可选，可无LLM运行
    config={
        "planning": {
            "enabled": True,
            "max_iterations": 10,
            "complexity_threshold": 0.5
        }
    }
)

# 注册工具
def my_tool(**kwargs):
    return "工具执行结果"
planner.register_tool("my_tool", my_tool)

# 执行任务
result = await planner.chat("帮我检查一下系统状态")
print(result.response)
```

### 2. 在云枢中集成使用

规划引擎已集成到 `DigitalLife` 类中，只需配置即可启用：

```python
from agent.digital_life import DigitalLife

# 配置并启动云枢
config = {
    "planning": {
        "enabled": True,
        "max_iterations": 10,
        "complexity_threshold": 0.5,
        "decomposer": {
            "max_subtasks": 20
        },
        "executor": {
            "max_retries": 3
        }
    }
}

Yunshu = DigitalLife(config)
Yunshu.start()

# 现在云枢可以自动处理复杂任务了！
response = Yunshu.chat("帮我分析一下我的系统状态，然后检查一下历史记录")
```

## 目录结构

```
planning/
├── __init__.py          # 模块入口，导出主要API
├── core.py              # 核心协调器，规划引擎门面
├── decomposer.py        # 任务分解器
├── executor.py          # 执行引擎
├── reflector.py         # 反思引擎
├── state_machine.py     # 状态机
├── react.py             # ReAct循环引擎
└── models/              # 数据模型
    ├── __init__.py
    ├── task.py          # Task, TaskType, TaskStatus
    ├── plan.py          # Plan, PlanState
    ├── action.py        # Action, ActionType, ActionResult
    ├── record.py        # ExecutionRecord
    └── react.py         # ReActStep, ReActResult, ThoughtResult
```

## 核心概念

### 1. ReAct循环

```
+--------+      +--------+      +-------------+
| 思考  |----->|  行动  |----->|  观察结果    |
+--------+      +--------+      +-------------+
    ^                                  |
    |                                  |
    +----------------------------------+
```

- **思考 (Thought)**：分析现状，决定下一步
- **行动 (Action)**：执行选定的动作（工具调用）
- **观察 (Observation)**：获取执行结果

### 2. 任务状态流转

```
        INIT
         |
         v
    DECOMPOSING <---分解任务
         |
         v
       READY
         |
         v
     EXECUTING
    /    |    \
   /     |     \
  v      v      v
PAUSED COMPLETED FAILED
         |
      (终态)
```

## 配置选项

### 完整配置示例

```python
{
    "planning": {
        "enabled": True,                     # 启用规划引擎
        "max_iterations": 10,                # ReAct最大迭代次数
        "complexity_threshold": 0.5,         # 复杂度阈值（控制何时启动规划）

        "decomposer": {                      # 任务分解器配置
            "strategy": "hybrid",            # hybrid/llm_only/rule_only
            "max_subtasks": 20
        },

        "executor": {                        # 执行引擎配置
            "max_retries": 3,                # 失败重试次数
            "retry_delay_seconds": 2,
            "parallel_execution": True
        },

        "reflector": {                       # 反思引擎配置
            "enabled": True,
            "reflect_interval": 5,           # 每N步反思一次
            "store_learning": True           # 是否保存学习结果
        }
    }
}
```

## 测试

### 运行基础测试

```bash
python test_planning.py
```

### 测试覆盖内容

- ✅ 数据模型 (Task, Plan, Action, 等)
- ✅ 任务分解器 (规则模式)
- ✅ 执行引擎与工具注册
- ✅ 状态机
- ✅ 核心协调器

## 高级功能

### 自定义工具注册

```python
from planning.executor import ToolRegistry

registry = ToolRegistry()

@registry.register("my_custom_tool", "工具描述")
def my_custom_tool(param1, param2=None):
    """自定义工具"""
    result = do_something(param1, param2)
    return result
```

### 状态转换钩子

```python
from planning.state_machine import PlanStateMachine, PlanState

sm = PlanStateMachine()

def on_completion_hook(plan):
    print(f"计划 {plan.id} 完成了！")

sm.register_hook(
    PlanState.EXECUTING,
    PlanState.COMPLETED,
    on_completion_hook
)
```

## 架构设计

### 分层架构

```
┌─────────────────────────────────────────┐
│        DigitalLife (应用层)              │
├─────────────────────────────────────────┤
│      PlanningCore (门面协调层)           │
├─────────────────────────────────────────┤
│  分解器  │  执行器  │ 反思器  │ ReAct  │
├─────────────────────────────────────────┤
│            数据模型层                   │
└─────────────────────────────────────────┘
```

### 数据流

```
用户输入
    ↓
复杂度判断
    ↓ (复杂任务)
┌───────────────────┐
│ PlanningCore.chat │
└─────────┬─────────┘
          ↓
    ┌─────────────┐
    │ ReActLoop   │
    └────┬────────┘
         ↓
    ┌────────────────┐
    │循环执行:       │
    │ 1. 思考        │
    │ 2. 行动        │
    │ 3. 观察        │
    │ 4. (可选)反思  │
    └────────────────┘
         ↓
    ChatResult
```

## 设计原则

1. **渐进式增强**：原有功能不受影响，规划作为可选增强
2. **降级策略**：LLM不可用时降级为规则模式
3. **异步优先**：支持异步执行，同时保持同步API兼容
4. **模块化设计**：各组件可独立测试和演进

## 后续改进方向

- [ ] 完善异步API和测试
- [ ] 实现向量数据库长期记忆
- [ ] 优化任务调度与并行执行
- [ ] 添加更多工具和模板
- [ ] 实现完整的MCP协议支持

## 问题排查

### 导入错误

如果遇到导入错误，请确保：

```bash
cd agent
python -c "import planning; print('OK')"
```

### 测试失败

检查是否缺少依赖：

```bash
# 规划引擎核心不需要额外依赖
# 如果需要高级功能（可选）
pip install networkx structlog
```

## 相关资源

- [规划引擎技术方案](./docs/规划引擎技术方案.md) - 完整技术文档
- [规划引擎集成指南](./docs/规划引擎集成指南.md) - 集成详细说明

---

**云枢规划引擎 v1.0 - 2024**
