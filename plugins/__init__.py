"""云枢插件机制包（阶段 1：后端插件化）。

T1.1 建立插件注册表与装配器骨架；后续任务（T1.2–T1.9）把各域插件
模块在此显式引入，保证加载顺序确定。
"""
from . import memory
from . import admin
from . import system_tools
from . import skills
from . import mcp_scheduler
from . import chat
from . import status
from . import safety

__all__ = ["memory", "admin", "system_tools", "skills", "mcp_scheduler", "chat", "status", "safety"]
