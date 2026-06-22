"""Subagent — 云枢分身生命周期抽象

分身是一个独立容器，包含选配的 LLM、记忆提供商、工具集和独立的上下文窗口。

设计思想（设计文档 2.2, 2.3, 6.1）：
- 分身全选配：每个分身可独立配置 LLM、记忆、工具
- 热更新：运行时替换配置
- 沙箱隔离：基于显式权限声明的执行隔离

使用方式:
    from agent.subagent.container import SubagentConfig, SubagentContainer
    from agent.subagent.lifecycle import SubagentLifecycleManager

    mgr = SubagentLifecycleManager()
    config = SubagentConfig(name="code-helper", model_id="gpt-4", memory_provider="holographic")
    agent = mgr.create(config)
    result = agent.execute("帮我写一段 Python 代码")
    mgr.destroy(agent)
"""

from agent.subagent.container import SubagentConfig, SubagentContainer
from agent.subagent.lifecycle import SubagentLifecycleManager
from agent.subagent.sandbox import Sandbox, PermissionDenied

__all__ = [
    "SubagentConfig",
    "SubagentContainer",
    "SubagentLifecycleManager",
    "Sandbox",
    "PermissionDenied",
]
