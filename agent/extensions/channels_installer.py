"""通道安装器 — 管理通信输出通道

通道是云枢向外部世界发送消息的"出口"。
每个通道封装了一种通信协议/平台：
  - Webhook:     通过 HTTP Webhook 发送消息
  - Email (SMTP): 通过 SMTP 发送电子邮件
  - Slack:        发送消息到 Slack 频道
  - Discord:      发送消息到 Discord
  - Telegram:     通过 Telegram Bot 发送消息
  - 等等...

设计：
  - 每个通道是一个可插拔的模块
  - 有统一的消息发送接口
  - 配置存储在扩展存储中
  - 支持启用/禁用/测试
"""

import json
import uuid
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Callable
from datetime import datetime

from agent.extensions.base import (
    ExtensionType, ExtensionStatus, ExtensionMetadata, BUILTIN_EXTENSIONS,
)
from agent.extensions.installer import InstallEngine
from agent.extensions.store import ExtensionStore
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



class ChannelInstaller:
    """通道安装器 — 管理通信通道"""

    def __init__(self, store: ExtensionStore):
        self._store = store
        self._engine = InstallEngine()
        # 运行时通道处理器注册表
        self._handlers: Dict[str, Callable] = {}

    def register_handler(self, channel_id: str, handler: Callable):
        """注册通道消息处理器

        Args:
            channel_id: 通道 ID
            handler: 处理函数，签名 (channel_config: dict, message: str, **kwargs) -> dict
        """
        self._handlers[channel_id] = handler
        logger.info(log_dict({'module_name': 'channels_installer', 'action': 'channel_id', 'msg': f'[通道安装器] 已注册通道处理器: {channel_id}'}))

    def get_handler(self, channel_id: str) -> Optional[Callable]:
        """获取通道处理器"""
        return self._handlers.get(channel_id)

    # ── 通道管理 ──

    def list_installed_channels(self) -> List[Dict]:
        """列出所有已安装的通道"""
        return self._store.list_all(ExtensionType.CHANNEL)

    def install_channel(
        self, channel_id: str, name: str, channel_type: str,
        description: str = "",
        config: Optional[Dict] = None,
    ) -> Tuple[bool, str]:
        """安装通道

        Args:
            channel_id: 通道唯一 ID
            name: 通道显示名称
            channel_type: 通道类型 (webhook, smtp, slack, discord, telegram, ...)
            description: 通道描述
            config: 通道配置（如 webhook_url, smtp_server 等）

        Returns:
            (成功标志, 消息)
        """
        # 检查是否已存在
        existing = self._store.get(ExtensionType.CHANNEL, channel_id)
        if existing and existing.get("status") != ExtensionStatus.UNINSTALLED.value:
            return False, f"通道已存在: {channel_id}"

        meta = ExtensionMetadata(
            ext_id=channel_id,
            ext_type=ExtensionType.CHANNEL,
            name=name,
            description=description,
            source=f"manual:type={channel_type}",
            status=ExtensionStatus.INSTALLED,
            config={
                "channel_type": channel_type,
                **(config or {}),
            },
        )
        meta.touch()
        meta.installed_at = meta.created_at
        self._store.add(meta)

        logger.info(log_dict({'module_name': 'channels_installer', 'action': 'channel_id.type.channel_type', 'msg': f'[通道安装器] 已安装通道: {channel_id} (type={channel_type})'}))
        return True, f"已安装通道: {name}"

    def install_builtin_channel(self, channel_id: str) -> Tuple[bool, str]:
        """安装内置通道

        Args:
            channel_id: 内置通道 ID ("webhook" 或 "email_smtp")
        """
        builtin = None
        for s in BUILTIN_EXTENSIONS.get("channel", []):
            if s["id"] == channel_id and s.get("builtin"):
                builtin = s
                break

        if not builtin:
            return False, f"未找到内置通道: {channel_id}"

        default_configs = {
            "webhook": {
                "channel_type": "webhook",
                "webhook_url": "",
                "method": "POST",
                "headers": {"Content-Type": "application/json"},
            },
            "email_smtp": {
                "channel_type": "smtp",
                "smtp_server": "",
                "smtp_port": 587,
                "smtp_username": "",
                "smtp_password": "",
                "use_tls": True,
                "from_address": "",
            },
        }

        return self.install_channel(
            channel_id=channel_id,
            name=builtin["name"],
            channel_type=channel_id,
            description=builtin["description"],
            config=default_configs.get(channel_id, {"channel_type": channel_id}),
        )

    def configure_channel(
        self, channel_id: str, config: Dict
    ) -> Tuple[bool, str]:
        """配置通道参数"""
        existing = self._store.get(ExtensionType.CHANNEL, channel_id)
        if not existing:
            return False, f"通道不存在: {channel_id}"

        self._store.update_config(ExtensionType.CHANNEL, channel_id, config)
        logger.info(log_dict({'module_name': 'channels_installer', 'action': 'channel_id', 'msg': f'[通道安装器] 已配置通道: {channel_id}'}))
        return True, f"已更新通道配置: {channel_id}"

    def uninstall_channel(self, channel_id: str) -> Tuple[bool, str]:
        """卸载通道"""
        success = self._store.remove(ExtensionType.CHANNEL, channel_id)
        if success:
            logger.info(log_dict({'module_name': 'channels_installer', 'action': 'channel_id', 'msg': f'[通道安装器] 已卸载通道: {channel_id}'}))
            return True, f"已卸载通道: {channel_id}"
        return False, f"通道不存在: {channel_id}"

    def toggle_channel(self, channel_id: str, enabled: bool = None) -> Tuple[bool, str, bool]:
        """切换通道启用状态"""
        existing = self._store.get(ExtensionType.CHANNEL, channel_id)
        if not existing:
            return False, f"通道不存在: {channel_id}", False

        new_enabled = enabled if enabled is not None else not existing.get("enabled", True)
        status = ExtensionStatus.ENABLED if new_enabled else ExtensionStatus.DISABLED
        self._store.update_status(ExtensionType.CHANNEL, channel_id, status)

        action = "已启用" if new_enabled else "已禁用"
        return True, f"{action} 通道: {channel_id}", new_enabled

    # ── 消息发送 ──

    def send_message(
        self, channel_id: str, message: str, **kwargs
    ) -> Tuple[bool, str]:
        """通过指定通道发送消息

        Args:
            channel_id: 通道 ID
            message: 消息内容
            **kwargs: 额外参数传给处理器

        Returns:
            (成功标志, 消息)
        """
        existing = self._store.get(ExtensionType.CHANNEL, channel_id)
        if not existing:
            return False, f"通道不存在: {channel_id}"

        if not existing.get("enabled", True):
            return False, f"通道已禁用: {channel_id}"

        handler = self._handlers.get(channel_id)
        if not handler:
            # 内置通用处理器
            channel_type = existing.get("config", {}).get("channel_type", "")
            handler = self._get_default_handler(channel_type)

        if not handler:
            return False, f"通道无处理器: {channel_id} (type={channel_type})"

        try:
            config = existing.get("config", {})
            result = handler(config, message, **kwargs)
            logger.info(log_dict({'module_name': 'channels_installer', 'action': 'channel_id', 'msg': f'[通道安装器] 消息发送成功: {channel_id}'}))
            return True, f"消息已发送: {result}"
        except Exception as e:
            logger.error(log_dict({'module_name': 'channels_installer', 'action': 'channel_id', 'msg': f'[通道安装器] 消息发送失败: {channel_id}: {e}'}))
            return False, f"发送失败: {e}"

    def _get_default_handler(self, channel_type: str) -> Optional[Callable]:
        """获取默认通道处理器"""
        handlers = {
            "webhook": self._handle_webhook,
            "smtp": self._handle_smtp,
        }
        return handlers.get(channel_type)

    @staticmethod
    def _handle_webhook(config: Dict, message: str, **kwargs) -> str:
        """Webhook 默认处理器"""
        import urllib.request
        import json

        webhook_url = config.get("webhook_url", "")
        if not webhook_url:
            raise ValueError("Webhook URL 未配置")

        data = json.dumps({
            "text": message,
            "source": "yunshu",
            **kwargs,
        }).encode("utf-8")

        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method=config.get("method", "POST"),
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return f"HTTP {resp.status}"

    @staticmethod
    def _handle_smtp(config: Dict, message: str, **kwargs) -> str:
        """SMTP 默认处理器"""
        import smtplib
        from email.mime.text import MIMEText

        smtp_server = config.get("smtp_server", "")
        if not smtp_server:
            raise ValueError("SMTP 服务器未配置")

        msg = MIMEText(message, "plain", "utf-8")
        msg["Subject"] = kwargs.get("subject", "来自云枢的消息")
        msg["From"] = config.get("from_address", "")
        msg["To"] = kwargs.get("to", config.get("default_to", ""))

        port = config.get("smtp_port", 587)
        use_tls = config.get("use_tls", True)

        with smtplib.SMTP(smtp_server, port) as server:
            if use_tls:
                server.starttls()
            username = config.get("smtp_username", "")
            password = config.get("smtp_password", "")
            if username and password:
                server.login(username, password)
            server.send_message(msg)

        return f"邮件已发送到 {msg['To']}"

    # ── 发现 ──

    def discover_available_channels(self) -> Dict[str, List[Dict]]:
        """发现所有可用的通道"""
        builtin_channels = BUILTIN_EXTENSIONS.get("channel", [])
        installed = self.list_installed_channels()
        installed_ids = {s.get("ext_id") for s in installed}

        available = []
        for s in builtin_channels:
            available.append({
                **s,
                "installed": s["id"] in installed_ids,
                "type": "channel",
            })

        return {
            "builtin_channels": available,
            "installed_channels": installed,
        }
