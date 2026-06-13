"""云枢扩展系统 — 自主安装 Skills / MCP / Channels / Plugins

我是云枢的"工具箱"——让我能够自主发现、安装、配置和管理各种扩展能力。

扩展类型：
  - skill:      应用层技能（自省反思、记忆摘要等行为特征）
  - claude_skill: Claude Code 技能（.claude/skills/ 中的技能包）
  - mcp:        MCP 服务（外部网络服务接入）
  - channel:    通信通道（Slack、Discord、邮件等输出渠道）
  - plugin:     通用插件（扩展新功能的 Python 代码包）

核心入口：
    from agent.extensions.manager import ExtensionManager
    mgr = ExtensionManager()
    result = mgr.install("skill", "my_skill", source="github:user/repo")
"""

from agent.extensions.base import ExtensionType, ExtensionStatus, ExtensionMetadata
from agent.extensions.manager import ExtensionManager
from agent.extensions.store import ExtensionStore

__all__ = [
    "ExtensionType",
    "ExtensionStatus",
    "ExtensionMetadata",
    "ExtensionManager",
    "ExtensionStore",
]
