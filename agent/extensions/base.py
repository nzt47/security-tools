"""扩展系统基础类型定义"""

import enum
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)


class ExtensionType(str, enum.Enum):
    """扩展类型枚举"""
    SKILL = "skill"              # 应用层技能（SkillsManager）
    CLAUDE_SKILL = "claude_skill"  # Claude Code 技能
    MCP = "mcp"                  # MCP 服务
    CHANNEL = "channel"          # 通信通道
    PLUGIN = "plugin"            # 通用插件


class ExtensionStatus(str, enum.Enum):
    """扩展状态枚举"""
    PENDING = "pending"          # 待安装
    INSTALLING = "installing"    # 安装中
    INSTALLED = "installed"      # 已安装
    ENABLED = "enabled"          # 已启用
    DISABLED = "disabled"        # 已禁用
    ERROR = "error"              # 错误
    UNINSTALLED = "uninstalled"  # 已卸载


@dataclass
class ExtensionMetadata:
    """扩展元数据"""
    # 核心标识
    ext_id: str                       # 扩展唯一 ID
    ext_type: ExtensionType           # 扩展类型
    name: str                         # 显示名称
    version: str = "0.1.0"           # 版本号

    # 描述信息
    description: str = ""             # 简要描述
    author: str = ""                  # 作者
    homepage: str = ""                # 项目主页
    license: str = ""                 # 许可证

    # 来源信息
    source: str = ""                  # 来源（github:user/repo, url:..., local:...）
    source_url: str = ""              # 下载 URL
    install_path: str = ""            # 本地安装路径

    # 依赖信息
    dependencies: List[str] = field(default_factory=list)     # Python 依赖
    extension_dependencies: List[str] = field(default_factory=list)  # 其他扩展依赖

    # 运行时配置
    config: Dict[str, Any] = field(default_factory=dict)      # 配置参数
    status: ExtensionStatus = ExtensionStatus.PENDING         # 当前状态
    enabled: bool = True                                      # 是否启用

    # 时间戳
    created_at: str = ""              # 创建时间
    updated_at: str = ""              # 更新时间
    installed_at: str = ""            # 安装时间

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典"""
        d = asdict(self)
        d["ext_type"] = self.ext_type.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExtensionMetadata":
        """从字典恢复"""
        data = data.copy()
        data["ext_type"] = ExtensionType(data["ext_type"])
        data["status"] = ExtensionStatus(data["status"])
        return cls(**data)

    def touch(self):
        """更新时间戳"""
        now = datetime.now().isoformat()
        if not self.created_at:
            self.created_at = now
        self.updated_at = now


# 内置扩展注册表 — 官方维护的可用扩展列表
BUILTIN_EXTENSIONS: Dict[str, List[Dict[str, Any]]] = {
    "skill": [
        {
            "id": "self_reflection",
            "name": "自省反思",
            "description": "每次交互后自动反思自身状态，不断成长",
            "builtin": True,
        },
        {
            "id": "memory_summary",
            "name": "记忆摘要",
            "description": "定期压缩历史对话为结构化摘要",
            "builtin": True,
        },
        {
            "id": "emotion_expression",
            "name": "情感表达",
            "description": "在对话中表达情感色彩，让回应更生动",
            "builtin": True,
        },
        {
            "id": "proactive_suggestion",
            "name": "主动建议",
            "description": "在适当时机主动提出建议和想法",
            "builtin": True,
        },
        {
            "id": "context_aware",
            "name": "上下文感知",
            "description": "感知对话上下文变化，自动调整回应策略",
            "builtin": True,
        },
        {
            "id": "safety_guard",
            "name": "安全守护",
            "description": "检测和过滤不安全的内容和操作",
            "builtin": True,
        },
        {
            "id": "voice_interaction",
            "name": "语音交互",
            "description": "通过语音与用户进行交互",
            "builtin": True,
        },
    ],
    "claude_skill": [
        # Claude Code 技能 — 来自 .claude/skills/ 的技能
        # 这些由云枢通过 GitHub 或文件系统发现
    ],
    "mcp": [
        # 内置 MCP 服务模板
        {
            "id": "filesystem",
            "name": "文件系统 MCP",
            "description": "安全的文件系统操作服务",
            "protocol": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
            "builtin": True,
        },
        {
            "id": "github",
            "name": "GitHub MCP",
            "description": "GitHub API 集成服务",
            "protocol": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "builtin": True,
        },
        {
            "id": "brave-search",
            "name": "Brave 搜索 MCP",
            "description": "Brave 搜索引擎集成",
            "protocol": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-brave-search"],
            "builtin": True,
        },
    ],
    "channel": [
        {
            "id": "webhook",
            "name": "Webhook 通道",
            "description": "通过 Webhook 发送消息到外部服务",
            "builtin": True,
        },
        {
            "id": "email_smtp",
            "name": "邮件通道 (SMTP)",
            "description": "通过 SMTP 发送电子邮件",
            "builtin": True,
        },
    ],
    "plugin": [],
}
