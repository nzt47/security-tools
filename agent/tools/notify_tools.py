"""通知工具（notify）— 让自主运行的结果**触达用户**

【任务定位】
    ``schedule_task`` 的语义是"周期重复执行"，**不是"到点提醒我"**（见
    ``docs/工具能力补全路线.md`` B 档）；``ext_send_channel`` 的语义是"把消息发给
    **外部**接收方"（Webhook / 邮件 …）。两者都覆盖不了最常见的一件事：
    **Agent 在后台自主跑完之后，用户怎么知道它跑完了、结果是什么。**
    本模块补上这一个原语：一条"本地告知 + 留痕"的通知。

    与 ``ext_send_channel`` 的分工（刻意不重复造功能）：
        - ``notify``           —— **本地告知/留痕**：写 ``logs/notifications.jsonl``，
          用户与审计在**本机**就能看到自主运行的结果；默认只走 ``log`` 通道，
          不产生任何出网副作用。
        - ``ext_send_channel`` —— **对外发消息**：通过已安装的 Webhook/邮件等通道
          把消息发给外部接收方，需要先配置通道、且属出网行为。
    需要真正外发时用 ``ext_send_channel``；只是"想让用户知道"时用 ``notify``
    （``channels`` 里点名通道时，本工具也会尽力经扩展管理器投递，但**绝不因此失败**）。

【不易】
    1. **通知路径永不因"送不出去"而崩**：单通道失败只进 ``failed``，其余通道照常投递，
       handler 也永不外抛异常。"没人知道结果"是本原语要解决的那个问题，
       所以它自己不能成为新的静默失败点。
    2. **默认零出网**：``channels`` 默认 ``("log",)``。通知原语被大量内部链路
       （长任务收尾、告警）调用，默认带上外部投递会让"记一条日志"变成出网动作。
    3. **追加一行 JSON、不重写文件**：``logs/notifications.jsonl`` 是 append-only 台账，
       与 ``data/*.db`` 那类台账同性质（``logs/`` 已在 ``.gitignore`` 中）。
       进程内用锁串行化写入，避免并发下写出半行 JSON（半行 JSON 会让整个台账不可解析）。
    4. **入参宽松处理**：``level`` 非法 → 归一到 ``info``（只记 warning），
       ``channels`` 传成单个字符串 → 当作单元素数组。通知是低风险告知路径，
       为了"参数写得不标准"而整体拒发，与本原语的目的相反。

【变易】
    schema 必须是**静态字面量**（``scripts/migrate_tools_to_yaml.py`` 用 AST
    ``literal_eval`` 抽取，不执行工具代码）：``level`` 的 enum 与 ``_VALID_LEVELS``
    逐字一致，``data/tool_definitions/notify.yaml`` 的 description/schema 与
    本模块 ``@_tools.register`` 的实参逐字节一致（单测 ``test_all_descriptions_match``
    守门）。台账路径、字段上限是这里的常量。

【简易】
    纯标准库（``json`` / ``os`` / ``threading`` / ``datetime``）：不新增依赖；
    权限校验用 ``getattr`` 守卫（宿主没暴露 ``_permission`` 时 fail-open），
    异常一律在 handler 内收口为 ``{"ok": False, "error": ...}``。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, List, Optional

from agent import tools as _tools

logger = logging.getLogger(__name__)

#: 合法级别（schema 的 enum 必须与本元组逐字一致，由单测守门）
_VALID_LEVELS = ("info", "warn", "error")

#: 默认通道：只落本地台账，零出网
_DEFAULT_CHANNELS = ("log",)

#: 本地留痕通道名
_LOG_CHANNEL = "log"

#: 通知台账（相对项目根目录；``logs/`` 已是 .gitignore 中的运行时目录）
_NOTIFY_LOG_REL = os.path.join("logs", "notifications.jsonl")

#: 单条通知的字段上限（超长**截断而非拒绝**：通知不该因为文案长就发不出去）
_MAX_TITLE_CHARS = 200
_MAX_MESSAGE_CHARS = 4000

#: 单次投递的通道数上限（防误传超长数组把一次通知变成广播风暴）
_MAX_CHANNELS = 8

#: 保护台账写入的进程内锁：append 一行必须是原子的，否则并发下会写出半行 JSON
_APPEND_LOCK = threading.Lock()


# ════════════════════════════════════════════════════════════
#  基础工具（路径 / 截断 / 权限）
# ════════════════════════════════════════════════════════════

def _repo_root() -> str:
    """项目根目录（``agent/tools/notify_tools.py`` → 上溯三级）"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _notify_log_path() -> str:
    """通知台账的绝对路径"""
    return os.path.join(_repo_root(), _NOTIFY_LOG_REL)


def _now_iso() -> str:
    """当前 UTC 时间（ISO 8601，秒级；台账是给人和审计看的，不需要微秒）"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clip(text: Any, limit: int) -> str:
    """把任意值转成字符串并截断到 ``limit`` 字符（超长时留一个可见标记）"""
    raw = text if isinstance(text, str) else str(text)
    if len(raw) <= limit:
        return raw
    return raw[:limit] + f"…（已截断，原长 {len(raw)} 字符）"


def _permission_denied(dl, action: str, context: str):
    """权限闸门（可选依赖，缺失时放行）

    ``dl`` 未必暴露 ``_permission``（单测 stub / 精简宿主），故用 ``getattr`` 守卫；
    校验器自身抛异常时按放行处理（与 ``agent/tools/test_tools.py`` 同一策略）——
    本工具的兜底闸门是"默认只写本地台账"，不依赖权限系统可用。
    """
    check = getattr(getattr(dl, "_permission", None), "check_action", None)
    if not callable(check):
        return None
    try:
        result = check(action, context)
    except Exception as e:  # noqa: BLE001 校验故障不阻断
        logger.warning("[notify] 权限校验异常，按放行处理: %s — %s", action, e)
        return None
    if isinstance(result, dict):
        allowed, reason = result.get("allowed"), result.get("reason", "")
    else:
        allowed, reason = getattr(result, "allowed", None), getattr(result, "reason", "")
    if allowed is False:
        return {"ok": False, "error": f"权限系统拒绝: {reason}", "blocked": True}
    return None


# ════════════════════════════════════════════════════════════
#  通道投递
# ════════════════════════════════════════════════════════════

def _deliver_log(entry: dict) -> str:
    """``log`` 通道：把一条通知**追加**为一行 JSON

    Returns:
        投递说明（成功时）。失败一律抛异常，由 ``notify`` 归入 ``failed``。
    """
    path = _notify_log_path()
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with _APPEND_LOCK:
        # encoding 显式声明：Windows 默认 GBK 会把中文通知写成乱码台账
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write(line)
            f.flush()
    return path


def _deliver_channel(dl, channel: str, entry: dict) -> str:
    """``log`` 以外的通道：尽力经**已安装的扩展管理器**投递

    【不易】这是"锦上添花"，不是本原语的依赖：宿主没有扩展管理器、没有该通道、
    通道发送失败 —— 全部只是进 ``failed``，不影响 ``log`` 通道与整体返回。
    因此这里**只做一件事**：拿到管理器、把消息发出去，任何异常原样抛出给调用方归类。

    Returns:
        投递说明（成功时）。
    """
    getter = getattr(dl, "_get_ext_manager", None)
    if not callable(getter):
        raise RuntimeError(f"宿主未提供扩展管理器（_get_ext_manager），通道 {channel} 未投递")
    manager = getter()
    sender = getattr(manager, "send_channel_message", None)
    if not callable(sender):
        raise RuntimeError(f"扩展管理器无 send_channel_message 方法，通道 {channel} 未投递")
    text = f"[{entry['level']}] {entry['title']}\n{entry['message']}"
    result = sender(channel, text)
    if isinstance(result, dict) and result.get("ok") is False:
        raise RuntimeError(str(result.get("error") or result.get("message") or "通道投递失败"))
    return f"扩展通道 {channel}"


# ════════════════════════════════════════════════════════════
#  实现体
# ════════════════════════════════════════════════════════════

def _normalize_channels(channels: Any) -> List[str]:
    """归一化通道列表：``None`` → 默认；字符串 → 单元素；去重保序

    Returns:
        通道名列表。**非法形态一律退回默认通道**而不是报错（通知要比参数严格）。
    """
    if channels is None:
        return list(_DEFAULT_CHANNELS)
    if isinstance(channels, str):
        channels = [channels]
    if not isinstance(channels, (list, tuple)):
        logger.warning("[notify] channels 形态非法（%r），改用默认通道", type(channels).__name__)
        return list(_DEFAULT_CHANNELS)
    out: List[str] = []
    for item in channels:
        name = str(item or "").strip()
        if name and name not in out:
            out.append(name)
    if not out:
        return list(_DEFAULT_CHANNELS)
    if len(out) > _MAX_CHANNELS:
        logger.warning("[notify] 通道数 %d 超过上限 %d，仅保留前 %d 个",
                       len(out), _MAX_CHANNELS, _MAX_CHANNELS)
        out = out[:_MAX_CHANNELS]
    return out


def notify(title: Any, message: Any, level: Any = "info",
           channels: Any = None, dl: Any = None) -> dict:
    """``notify`` 的实现体（由工具包装；测试与内部链路亦可直调）

    Args:
        title: 通知标题（必填，超长截断到 ``_MAX_TITLE_CHARS``）。
        message: 通知正文（必填，超长截断到 ``_MAX_MESSAGE_CHARS``）。
        level: ``info`` / ``warn`` / ``error``；非法值归一到 ``info``（只记 warning）。
        channels: 要投递的通道名数组，默认 ``["log"]``。
        dl: 宿主实例；**仅**用于权限闸门与可选的外部通道投递，``None`` 时两者都跳过。

    Returns:
        ``{"ok": True, "delivered": [...], "failed": [...]}``：
        ``delivered`` 是投递成功的**通道名**列表；``failed`` 是
        ``{"channel", "error"}`` 列表。全部通道都失败时 ``ok=False`` 且带 ``error``。
        入参缺失（title/message 为空）时 ``ok=False`` 且**不产生任何写入**。
    """
    title_text = _clip(title, _MAX_TITLE_CHARS).strip() if title is not None else ""
    message_text = _clip(message, _MAX_MESSAGE_CHARS).strip() if message is not None else ""
    if not title_text:
        return {"ok": False, "error": "请提供通知标题（title）"}
    if not message_text:
        return {"ok": False, "error": "请提供通知内容（message）"}

    level_text = str(level or "info").strip().lower()
    if level_text not in _VALID_LEVELS:
        logger.warning("[notify] level %r 非法，归一到 info", level)
        level_text = "info"

    denied = _permission_denied(dl, "notify", f"发送通知[{level_text}]: {title_text}")
    if denied:
        return denied

    entry = {
        "timestamp": _now_iso(),
        "title": title_text,
        "message": message_text,
        "level": level_text,
    }
    targets = _normalize_channels(channels)

    delivered: List[str] = []
    failed: List[dict] = []
    for channel in targets:
        try:
            if channel == _LOG_CHANNEL:
                detail = _deliver_log(entry)
            else:
                detail = _deliver_channel(dl, channel, entry)
            delivered.append(channel)
            logger.debug("[notify] %s 已投递: %s", channel, detail)
        except Exception as e:  # noqa: BLE001 单通道失败不影响其余通道
            failed.append({"channel": channel, "error": f"{type(e).__name__}: {e}"})
            logger.warning("[notify] 通道 %s 投递失败: %s", channel, e)

    result: dict = {
        "ok": bool(delivered),
        "delivered": delivered,
        "failed": failed,
        "level": level_text,
        "title": title_text,
        "log_path": _notify_log_path() if _LOG_CHANNEL in delivered else "",
    }
    if not delivered:
        result["error"] = "全部通道投递失败: " + "; ".join(
            f"{item['channel']}（{item['error']}）" for item in failed)
    return result


# ════════════════════════════════════════════════════════════
#  工具注册
# ════════════════════════════════════════════════════════════

def register_all(dl):
    """注册通知工具（``notify``）

    Args:
        dl: DigitalLife / LifecycleManager 实例。用于两处可选能力，均以
            ``getattr`` 守卫：``dl._permission``（权限闸门）与
            ``dl._get_ext_manager``（``log`` 以外通道的对外投递）。
    """

    @_tools.register("notify",
        "主动向用户发出通知/提醒并留痕（通知、提醒、告警、报警、notify、notification、"
        "alert、提醒我）。与 ext_send_channel 的分工：notify 是\"本地告知/留痕\"——"
        "把消息追加写入 logs/notifications.jsonl，用户与审计在本机即可看到自主运行的"
        "结果，默认只走 log 通道、不产生出网副作用；ext_send_channel 是\"对外发消息\""
        "——经已安装的 Webhook/邮件等通道发给外部接收方。需要真正外发时用 "
        "ext_send_channel，只是想让用户知道结果时用 notify。"
        "参数：title 标题（必填）、message 内容（必填）、level 取 "
        "info/warn/error（默认 info）、channels 通道数组（默认 [\"log\"]，可点名已安装的"
        "扩展通道，投递失败只记 failed 不影响其余通道）。"
        "Send a local notification, alert the user, notify",
        schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "通知标题（必填）"},
                "message": {"type": "string", "description": "通知内容（必填）"},
                "level": {
                    "type": "string",
                    "enum": ["info", "warn", "error"],
                    "description": "通知级别：info 普通 / warn 警告 / error 错误，默认 info",
                },
                "channels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": ("投递通道数组，默认 [\"log\"]（写 logs/notifications.jsonl）。"
                                    "可点名已安装的扩展通道（需宿主提供扩展管理器）"),
                },
            },
            "required": ["title", "message"],
        })
    def _notify(**kwargs):
        """通知入口（参数见 schema；异常一律收口为 ok=False）"""
        try:
            return notify(
                kwargs.get("title"),
                kwargs.get("message"),
                level=kwargs.get("level", "info"),
                channels=kwargs.get("channels"),
                dl=dl,
            )
        except Exception as e:  # noqa: BLE001 工具链路上绝不外抛异常
            logger.error("[notify] 投递异常: %s", e, exc_info=True)
            return {"ok": False, "error": f"发送通知异常: {e}"}


__all__ = ["register_all", "notify", "_VALID_LEVELS", "_DEFAULT_CHANNELS"]
