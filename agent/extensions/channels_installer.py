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
import time
import uuid
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Callable
from datetime import datetime

# 【按子模块 import，别写 from agent.extensions import ...】后者会触发包 __init__
# （它 import manager → manager import 本模块），arch_rules 的循环依赖检查会红
# （2026-11-14 当场踩过：manager.extensions → channels_installer 被判高严重度）。
from agent.extensions.channel_env_bridge import (
    clear_env as bridge_clear_env,
    effective_config as bridge_effective_config,
    normalize_config as bridge_normalize_config,
    public_view as bridge_public_view,
    rollback_env as bridge_rollback_env,
    sync_env as bridge_sync_env,
)
from agent.extensions.base import (
    ExtensionType, ExtensionStatus, ExtensionMetadata, BUILTIN_EXTENSIONS,
)
from agent.extensions.installer import InstallEngine
from agent.extensions.store import ExtensionStore
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

#: SMTP 缺省端口（STARTTLS 惯用 587）与隐式 TLS 端口（465）
SMTP_DEFAULT_PORT = 587
SMTP_SSL_PORT = 465
#: SMTP 连接/读写超时（秒）：不设超时时 smtplib 会一直挂着，采集线程会被一封发不出去的邮件拖住。
#: 【2026-11-13 由 15 调到 25】现场一次「TLS 握手超时」(_ssl.c:983) —— 同一分钟手工探针
#: 握手只要 103ms，说明是**瞬时抖动**；15s 对「一次 SYN 重传 + TLS 重试」的预算太紧。
SMTP_TIMEOUT_SEC = 25
#: 手动发送（界面「测试发送」）遇到瞬时网络错误时的重试次数与退避（秒）。
#: 只给手动路径用：报警外发另有 channel_bridge 自己的重试，两层叠起来会把采集线程拖太久。
SEND_RETRY_ATTEMPTS = 2
SEND_RETRY_BACKOFF_SEC = 0.8
#: 对外失败文案（**只给类别与下一步，不回异常原文** —— 原文进日志；原文进响应体是
#: py/stack-trace-exposure，且 _ssl.c:983 这种文字对用户没有任何可操作信息）
SEND_ERR_TLS = "邮件服务器 TLS 握手超时（网络抖动或被中间设备拦截），已自动重试一次；请稍后再发"
SEND_ERR_TIMEOUT = "连接邮件服务器超时，已自动重试一次；请检查出网/代理后重试"
SEND_ERR_AUTH = "邮件服务器拒绝登录（账号或授权码不对），详见服务日志"
SEND_ERR_GENERIC = "发送失败（原因见服务日志）"
#: Webhook 单次投递超时（秒）与响应体读取上限（业务码/原因只在前 4KB 里）
WEBHOOK_TIMEOUT_SEC = 10
WEBHOOK_BODY_MAX_BYTES = 4096
#: webhook 目标主机 → 载荷方言。飞书与钉钉**都**是「HTTP 200 + 业务码非 0 表示失败」，
#: 且各自的成功形状与通用 {"text"} 不同（2026-11-22 现场：飞书回 code=19002 仍被当成功）。
_FEISHU_HOSTS = ("open.feishu.cn", "open.larksuite.com", "larksuite.com", "feishu.cn")
_DINGTALK_HOSTS = ("oapi.dingtalk.com", "api.dingtalk.com", "dingtalk.com")
#: 服务端拒收码 → 一句可执行的下一步（没有的就只报 code/msg）
_REJECTION_HINTS = {
    "19021": "（该机器人启用了加签：请检查「加签密钥」是否与飞书后台一致、本机时间是否准确）",
    "19002": "（载荷形状被拒：飞书要 msg_type + content.text）",
    "310000": "（钉钉拒收：常见于加签不匹配或关键词未命中）",
}


class ChannelRejected(ValueError):
    """通道服务端**业务拒绝**：HTTP 2xx，但响应体里的业务码非 0（或 ok=false）。

    【为什么单列一类（2026-11-22 现场）】飞书自定义机器人对形状不对的载荷回
    HTTP **200** + {"code":19002,"msg":"params error, msg_type need"}；旧实现只看
    "urlopen 有没有抛异常" ⇒ 界面「消息已发送」，机器人一个字都没收到。
    这类原因不是堆栈（是对方给的 code/msg），可以直接回给界面 ⇒ 挂 .message，
    路由层/JSON 不用 str(exc)（与 llm_idle_gate.GateConfigError 同款口径）。
    """

    def __init__(self, message: str, *, code: str = ""):
        self.message = message
        self.code = str(code or "")
        super().__init__(message)


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

    @staticmethod
    def is_channel_enabled(row: Dict) -> bool:
        """一条通道"现在会不会发"（**唯一判据**，别各处自己拼）。

        口径：`enabled` 为真 **且** 状态不是 uninstalled/disabled。为什么两个都看：
        历史数据里出现过"status=disabled 但 enabled 还是 True"（toggle 只改 status 的遗留），
        只看 enabled 会让用户的「停用」无效；只看 status 又会把"刚装、没启用过"误判成停用。
        """
        if not row:
            return False
        if row.get("enabled", True) is False:
            return False
        return str(row.get("status") or "") not in (
            ExtensionStatus.UNINSTALLED.value, ExtensionStatus.DISABLED.value,
        )

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
                # 587 = STARTTLS；465 = 隐式 TLS（use_ssl 不写时按端口推断，见 _handle_smtp）
                "smtp_port": SMTP_DEFAULT_PORT,
                "smtp_username": "",
                "smtp_password": "",
                "use_tls": True,
                # 可选 use_ssl：不写时按端口推断（465 ⇒ 隐式 TLS）；写了则显式优先
                "from_address": "",
                # 收件人：报警外发不带 to 参数，只认这个键（多个用逗号分隔）
                "default_to": "",
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
        """配置通道参数（**同时镜像进 .env**，2026-11-14）

        写入顺序是安全边界的一部分：
        ① 先把值写进 .env（EnvConfigManager：跨进程锁 / 原子写 / 600 / 写前备份）；
        ② .env 成功后才落本地 extensions.json（同样是原子写 + 备份）；
        ③ ② 失败 ⇒ 用 ① 返回的 previous 回滚 .env，并如实报错（不假装成功）。

        【为什么先写 .env 再写本地】.env 有备份与跨进程锁，本地 store 只有单文件原子写；
        先做"更难回滚、更敏感"的那一步，失败时还能用内存里的 previous 退回。

        【空值语义】字段留空 ⇒ 从 .env 删除该键（不是写空串）；口令留空/掩码 ⇒ 不改。
        详见 agent/extensions/channel_env_bridge.py 的模块 docstring。
        """
        existing = self._store.get(ExtensionType.CHANNEL, channel_id)
        if not existing:
            return False, f"通道不存在: {channel_id}"

        # ① .env（只对主通道生效；非主通道是 no-op）
        try:
            env_result = bridge_sync_env(existing, config or {})
        except Exception as exc:  # noqa: BLE001 写 .env 失败 ⇒ 不落本地，如实报失败
            logger.error(log_dict({
                'module_name': 'channels_installer', 'action': 'channel.env_sync_failed',
                'msg': f'[通道安装器] 写 .env 失败，配置未保存: {type(exc).__name__}',
            }))
            return False, '写入 .env 失败，配置未保存（详见服务日志）'

        # ② 本地存储（口令留空/掩码时保留原值）
        normalized = bridge_normalize_config(existing, config or {})
        try:
            # 本次只有"不改口令"一类空操作 ⇒ 本地无需重写（也不算失败）
            ok = True if not normalized else self._store.update_config(
                ExtensionType.CHANNEL, channel_id, normalized)
        except Exception as exc:  # noqa: BLE001 本地失败 ⇒ 回滚 .env
            ok = False
            logger.error(log_dict({
                'module_name': 'channels_installer', 'action': 'channel.store_failed',
                'msg': f'[通道安装器] 本地通道配置写入失败: {type(exc).__name__}',
            }))
        if not ok:
            try:
                bridge_rollback_env(existing, env_result.get('previous') or {})
            except Exception as exc:  # noqa: BLE001 回滚失败也要把失败如实报出去
                logger.error(log_dict({
                    'module_name': 'channels_installer', 'action': 'channel.env_rollback_failed',
                    'msg': f'[通道安装器] .env 回滚失败: {type(exc).__name__}',
                }))
            return False, '保存通道配置失败，已尝试回滚 .env（详见服务日志）'

        written = len(env_result.get('written') or [])
        removed = len(env_result.get('removed') or [])
        logger.info(log_dict({'module_name': 'channels_installer', 'action': 'channel_configured',
                              'msg': f'[通道安装器] 已配置通道: {channel_id}'
                                     f'（.env 改 {written} 项 / 删 {removed} 项）'}))
        if not env_result.get('primary'):
            return True, f"已更新通道配置: {channel_id}（非主通道：值只存本机，不写 .env）"
        return True, f"已更新通道配置: {channel_id}（.env 改 {written} 项 / 删 {removed} 项）"

    def uninstall_channel(self, channel_id: str) -> Tuple[bool, str]:
        """卸载通道（主通道一并清掉它在 .env 里的专用键）

        【为什么要清】专用键（CP_ALERT_*）是**这条通道的**值。通道没了却留着键，
        等于把凭据留在 .env 里、且后续任何"回落"都会读到一个没有主人的配置。
        清空同样是"删键"而不是写空串；.env 写前有备份（.env.backups），可回滚。
        """
        existing = self._store.get(ExtensionType.CHANNEL, channel_id)
        success = self._store.remove(ExtensionType.CHANNEL, channel_id)
        if success:
            cleared: List[str] = []
            if existing:
                try:
                    cleared = list(bridge_clear_env(existing).get("removed") or [])
                except Exception as exc:  # noqa: BLE001 清键失败不阻塞卸载，但要留存
                    logger.error(log_dict({
                        'module_name': 'channels_installer',
                        'action': 'channel.env_clear_failed',
                        'msg': f'[通道安装器] 卸载后清理 .env 失败: {type(exc).__name__}',
                    }))
            logger.info(log_dict({'module_name': 'channels_installer', 'action': 'channel_uninstalled',
                                  'msg': f'[通道安装器] 已卸载通道: {channel_id}'
                                         f'（.env 清理 {len(cleared)} 个键）'}))
            if cleared:
                return True, f"已卸载通道: {channel_id}（同时从 .env 清掉 {len(cleared)} 个键）"
            return True, f"已卸载通道: {channel_id}"
        return False, f"通道不存在: {channel_id}"

    def toggle_channel(self, channel_id: str, enabled: bool = None) -> Tuple[bool, str, bool]:
        """切换通道启用状态"""
        existing = self._store.get(ExtensionType.CHANNEL, channel_id)
        if not existing:
            return False, f"通道不存在: {channel_id}", False

        new_enabled = enabled if enabled is not None else not existing.get("enabled", True)
        status = ExtensionStatus.ENABLED if new_enabled else ExtensionStatus.DISABLED
        # enabled 与 status 一次写两个：前者才是"发不发"的判据（见 store.update_status 注释）
        self._store.update_status(ExtensionType.CHANNEL, channel_id, status,
                                  enabled=new_enabled)

        action = "已启用" if new_enabled else "已禁用"
        return True, f"{action} 通道: {channel_id}", new_enabled

    # ── 消息发送 ──

    @staticmethod
    def _is_transient_send_error(exc: BaseException) -> bool:
        """瞬时网络错误（值得立刻重试一次）：TLS 握手/连接超时、连接被断。"""
        import smtplib
        import socket
        import ssl
        return isinstance(exc, (ssl.SSLError, socket.timeout, TimeoutError,
                                ConnectionError, smtplib.SMTPServerDisconnected))

    @staticmethod
    def _send_error_reason(exc: BaseException) -> str:
        """异常 → 对外文案（类别 + 下一步）。**不包含异常原文**。"""
        import smtplib
        import ssl
        if isinstance(exc, ChannelRejected):
            # 对方明确拒收（code/msg 是服务端给的、不含密钥与堆栈）⇒ 原样回给界面
            return exc.message
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            return SEND_ERR_AUTH
        if isinstance(exc, ssl.SSLError) or "handshake" in str(exc).lower():
            return SEND_ERR_TLS
        if isinstance(exc, (TimeoutError, ConnectionError, smtplib.SMTPServerDisconnected)):
            return SEND_ERR_TIMEOUT
        return SEND_ERR_GENERIC

    @staticmethod
    def _send_error_code(exc: Optional[BaseException]) -> str:
        """异常 → 投递台账的**类别**（与 _send_error_reason 同判据，台账只存类别不存原文）。"""
        import smtplib
        import ssl
        # 【按子模块 import，别写 from agent.extensions import ...】后者会触发包 __init__
        # （它 import manager → manager import 本模块），arch_rules 的循环依赖检查会红。
        from agent.extensions.channel_health import (
            CODE_AUTH, CODE_DISCONNECTED, CODE_OTHER, CODE_TIMEOUT, CODE_TLS,
        )

        if isinstance(exc, smtplib.SMTPAuthenticationError):
            return CODE_AUTH
        if isinstance(exc, ssl.SSLError) or "handshake" in str(exc).lower():
            return CODE_TLS
        if isinstance(exc, smtplib.SMTPServerDisconnected):
            return CODE_DISCONNECTED
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return CODE_TIMEOUT
        return CODE_OTHER

    @classmethod
    def _record_delivery(cls, channel_id: str, *, success: bool,
                         exc: Optional[BaseException] = None) -> None:
        """把这次投递结果写进 channel_health（旁路：失败只记日志，绝不影响发送）。

        【为什么在发送收口记账】手动发送、报警外发、覆盖缺口外发三条链路最终都走
        `send_message`；在调用方各记一次会漏（2026-11-12 的教训：同一件事两处写点必漂）。
        """
        try:
            from agent.extensions.channel_health import record_failure, record_success
            if success:
                record_success(channel_id)
            else:
                record_failure(channel_id, cls._send_error_code(exc))
        except Exception as e:  # noqa: BLE001 记账坏掉不能让"发消息"失败
            logger.debug(log_dict({'module_name': 'channels_installer',
                                   'action': 'channel_health_record_failed',
                                   'msg': f'[通道安装器] 投递台账写入跳过: {type(e).__name__}'}))

    def send_message(
        self, channel_id: str, message: str, *, retry_transient: bool = False, **kwargs
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

        if not self.is_channel_enabled(existing):
            return False, f"通道已禁用: {channel_id}"

        handler = self._handlers.get(channel_id)
        if not handler:
            # 内置通用处理器
            channel_type = existing.get("config", {}).get("channel_type", "")
            handler = self._get_default_handler(channel_type)

        if not handler:
            return False, f"通道无处理器: {channel_id} (type={channel_type})"

        # 【一处真相（2026-11-14）】发送用的是**有效配置**：本地值 > .env 专用键
        # （CP_ALERT_*）> 旧键（SMTP_* / ERROR_REPORTING_WEBHOOK_URL）> 默认。
        # 与页面展示（discover 的 resolved_channels）和依赖探针同源，否则会出现
        # "页面显示 A、实际发的是 B"。只读，不写回存储。
        config = bridge_effective_config(existing)
        attempts = SEND_RETRY_ATTEMPTS if retry_transient else 1
        last_exc: Optional[BaseException] = None
        for attempt in range(attempts):
            try:
                result = handler(config, message, **kwargs)
            except Exception as e:  # noqa: BLE001 通道实现各异，失败要如实回给调用方
                last_exc = e
                # 原文只进日志（响应里只给类别 + 下一步，见 _send_error_reason）
                logger.error(log_dict({'module_name': 'channels_installer', 'action': 'channel_id',
                                       'msg': f'[通道安装器] 消息发送失败: {channel_id}: {type(e).__name__}: {e}'}))
                if not retry_transient or not self._is_transient_send_error(e) or attempt + 1 >= attempts:
                    break
                time.sleep(SEND_RETRY_BACKOFF_SEC)
                continue
            logger.info(log_dict({'module_name': 'channels_installer', 'action': 'channel_id', 'msg': f'[通道安装器] 消息发送成功: {channel_id}'}))
            # 投递台账（2026-11-13）：成功 ⇒ 连续失败清零（告警下一轮自动恢复）
            self._record_delivery(channel_id, success=True)
            return True, f"消息已发送: {result}"
        # 投递台账（2026-11-13）：重试之后仍失败 ⇒ 记一次失败（只存类别），
        # 采集线程据此报 channel.<通道>，不再"只有点按钮的人知道"。
        self._record_delivery(channel_id, success=False, exc=last_exc)
        return False, self._send_error_reason(last_exc) if last_exc is not None else SEND_ERR_GENERIC

    def _get_default_handler(self, channel_type: str) -> Optional[Callable]:
        """获取默认通道处理器"""
        handlers = {
            "webhook": self._handle_webhook,
            "smtp": self._handle_smtp,
        }
        return handlers.get(channel_type)

    @staticmethod
    def _webhook_dialect(url: str) -> str:
        """按主机判定 webhook 方言：feishu / dingtalk / generic（未命中即通用）。"""
        from urllib.parse import urlsplit
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            host = ""
        for h in _FEISHU_HOSTS:
            if host == h or host.endswith("." + h):
                return "feishu"
        for h in _DINGTALK_HOSTS:
            if host == h or host.endswith("." + h):
                return "dingtalk"
        return "generic"

    @staticmethod
    def _feishu_sign(secret: str, timestamp: str) -> str:
        """飞书自定义机器人「加签」：base64(HmacSHA256(key="{ts}\n{secret}", msg=""))。

        与飞书官方示例逐字一致（key 是拼接串、msg 为空 —— 与钉钉的方向相反，别抄串）。
        """
        import base64
        import hashlib
        import hmac
        string_to_sign = f"{timestamp}\n{secret}"
        digest = hmac.new(string_to_sign.encode("utf-8"), b"",
                          digestmod=hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    @staticmethod
    def _dingtalk_sign(secret: str, timestamp_ms: str) -> str:
        """钉钉自定义机器人「加签」：base64(HmacSHA256(key=secret, msg="{ts}\n{secret}"))。

        与飞书**方向相反**（钉钉 key=密钥、msg=拼接串），且时间戳是毫秒、签在 URL 查询串上。
        """
        import base64
        import hashlib
        import hmac
        string_to_sign = f"{timestamp_ms}\n{secret}"
        digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                          digestmod=hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    @staticmethod
    def _signed_webhook_url(url: str, secret: str) -> str:
        """钉钉：把 timestamp/sign 追加到查询串（保留原有参数）。其它方言原样返回。"""
        if not secret or ChannelInstaller._webhook_dialect(url) != "dingtalk":
            return url
        from urllib.parse import quote, urlsplit
        ts = str(int(time.time() * 1000))
        sign = ChannelInstaller._dingtalk_sign(secret, ts)
        parts = urlsplit(url)
        query = (parts.query + "&" if parts.query else "") + \
            f"timestamp={ts}&sign={quote(sign, safe='')}"
        return parts._replace(query=query).geturl()

    @staticmethod
    def _build_webhook_payload(url: str, message: str,
                               extra: Dict[str, Any],
                               sign_secret: str = "") -> Dict[str, Any]:
        """按目标服务端拼载荷（含可选加签）。

        - 飞书：{"msg_type": "text", "content": {"text": ...}}；开了加签再加 timestamp/sign
          （发聚合形状 {"text": ...} ⇒ 200 + code=19002 "params error, msg_type need"）
        - 钉钉：{"msgtype": "text", "text": {"content": ...}}；加签在 URL 查询串上（见 _signed_webhook_url）
        - Slack / 其它（Slack 兼容端点）：{"text": ..., "source": "yunshu"}，不签名
        """
        dialect = ChannelInstaller._webhook_dialect(url)
        if dialect == "feishu":
            payload: Dict[str, Any] = {"msg_type": "text", "content": {"text": message}}
            if sign_secret:
                ts = str(int(time.time()))
                payload["timestamp"] = ts
                payload["sign"] = ChannelInstaller._feishu_sign(sign_secret, ts)
            return payload
        if dialect == "dingtalk":
            return {"msgtype": "text", "text": {"content": message}}
        return {"text": message, "source": "yunshu", **extra}

    @staticmethod
    def _webhook_rejection(body_text: str, status: int) -> Optional["ChannelRejected"]:
        """HTTP 2xx 也可能是失败：解析响应体业务码，非 0 / ok=false ⇒ 拒收。

        飞书 {"code":0}、钉钉 {"errcode":0} 才算成功；Slack 之类回纯文本 "ok" ⇒ 不拦。
        """
        if not body_text:
            return None
        try:
            data = json.loads(body_text)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        raw_code = data.get("code", data.get("errcode"))
        if raw_code is not None and str(raw_code) != "0":
            detail = str(data.get("msg") or data.get("errmsg") or data.get("message") or "")[:120]
            suffix = ("：" + detail) if detail else ""
            # 已知码给一句"下一步"（19021 飞书加签失败最常见：密钥填错/没填/时钟偏差）
            hint = _REJECTION_HINTS.get(str(raw_code), "")
            return ChannelRejected(
                f"服务端拒收本次消息（HTTP {status}，code={raw_code}{suffix}）{hint}",
                code=str(raw_code))
        if data.get("ok") is False:
            detail = str(data.get("error") or data.get("msg") or "")[:120]
            suffix = ("：" + detail) if detail else ""
            return ChannelRejected(
                f"服务端拒收本次消息（HTTP {status}{suffix}）", code="ok=false")
        return None

    @staticmethod
    def _handle_webhook(config: Dict, message: str, **kwargs) -> str:
        """Webhook 默认处理器（飞书 / 钉钉 / 通用三种方言 + **业务码校验**）。

        【2026-11-22 修的两个坑】①载荷形状：飞书/钉钉各有自己的成功形状，
        发通用 {"text"} 会被拒；②成功判据：两家的失败也是 HTTP 200，只看状态码会把
        "拒收"当"已发送"（现场：飞书 code=19002，界面却显示成功）。
        """
        import urllib.error
        import urllib.request

        webhook_url = config.get("webhook_url", "")
        if not webhook_url:
            raise ValueError("Webhook URL 未配置")

        # 加签密钥（可选；空 = 该机器人未开加签）。飞书签在 body，钉钉签在 URL 查询串
        sign_secret = str(config.get("sign_secret") or "").strip()
        payload = ChannelInstaller._build_webhook_payload(
            webhook_url, message, kwargs, sign_secret=sign_secret)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            ChannelInstaller._signed_webhook_url(webhook_url, sign_secret),
            data=data,
            headers={"Content-Type": "application/json"},
            method=config.get("method", "POST"),
        )
        try:
            with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT_SEC) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                try:
                    body_text = resp.read(WEBHOOK_BODY_MAX_BYTES).decode("utf-8", "replace")
                except Exception:  # noqa: BLE001 读不到响应体不改判"已投递"
                    body_text = ""
        except urllib.error.HTTPError as exc:
            # 4xx/5xx：对方明确回了状态码，如实转成可读原因（原文只进日志）
            raise ChannelRejected(f"服务端返回 HTTP {exc.code}（详情见服务日志）",
                                  code=str(exc.code)) from None

        rejected = ChannelInstaller._webhook_rejection(body_text, status)
        if rejected is not None:
            raise rejected
        return f"HTTP {status}"

    @staticmethod
    def _handle_smtp(config: Dict, message: str, **kwargs) -> str:
        """SMTP 默认处理器（两条传输：STARTTLS 587 / 隐式 TLS 465）。

        【为什么必须支持 465（2026-11-12 现场实证）】配好 139 邮箱后发送失败：
        `smtp.139.com:587` 从本机 **连接超时**，而 `:465` 通（同机到 qq:587 / 163:465 都通 ⇒
        不是出网被封，是这个服务端的 587 不可达）。原实现只有 `SMTP()+starttls()`，
        465 的隐式 TLS 根本连不上 ⇒ 用户"配好了却发不出去"，且报错只是 WinError 10060。

        口径：`use_ssl` 显式优先；未写时按端口推断（465 ⇒ 隐式 TLS）。`use_tls` 仅对
        STARTTLS 分支有效（SMTP_SSL 下不再 starttls）。超时用模块常量，避免无超时长期挂住
        采集线程（默认 `smtplib` 没有超时）。
        """
        import smtplib
        from email.mime.text import MIMEText

        smtp_server = config.get("smtp_server", "")
        if not smtp_server:
            raise ValueError("SMTP 服务器未配置")

        msg = MIMEText(message, "plain", "utf-8")
        msg["Subject"] = kwargs.get("subject", "来自云枢的消息")
        msg["From"] = config.get("from_address", "")
        msg["To"] = kwargs.get("to", config.get("default_to", ""))
        if not str(msg["To"]).strip():
            raise ValueError("收件人未配置（通道配置 default_to，或发送时传 to）")

        port = int(config.get("smtp_port") or SMTP_DEFAULT_PORT)
        use_tls = bool(config.get("use_tls", True))
        use_ssl = config.get("use_ssl")
        implicit_ssl = use_tls and (bool(use_ssl) if use_ssl is not None
                                    else port == SMTP_SSL_PORT)

        if implicit_ssl:
            client = smtplib.SMTP_SSL(smtp_server, port, timeout=SMTP_TIMEOUT_SEC)
        else:
            client = smtplib.SMTP(smtp_server, port, timeout=SMTP_TIMEOUT_SEC)
        with client as server:
            if use_tls and not implicit_ssl:
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
        # 模板卡片要对"装了吗 / 现在发不发"给出**同一处真相**：此前模板只带 installed，
        # 界面上 `b.enabled` 恒 undefined ⇒ 装好的通道永远显示"已装（未启用）"（2026-11-12 实测）。
        state = {s.get("ext_id"): s for s in installed}

        available = []
        for s in builtin_channels:
            row = state.get(s["id"]) or {}
            available.append({
                **s,
                "installed": s["id"] in installed_ids,
                "enabled": self.is_channel_enabled(row) if row else False,
                "type": "channel",
            })

        return {
            "builtin_channels": available,
            "installed_channels": installed,
            # 【2026-11-14】给界面用的解析视图：逐字段"值 + 来源（通道 / .env / 旧键 / 默认）"，
            # 口令永久掩码，附空态范例与说明（后端是唯一文案来源，前端不抄第二份）。
            "resolved_channels": [
                bridge_public_view(row) for row in installed
                if isinstance(row, dict) and row.get("ext_id")
            ],
        }
