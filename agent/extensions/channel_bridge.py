"""扩展通道的**事件接线层** —— 把产品事件发到「消息中心里装的通道」

【解决什么】`/api/extensions/channels/send` 此前**没有任何产品调用方**：通道能装、
能手动调试发一条，但装完之后没有任何流程会用它 —— 界面上只能写"未接线"。
本模块把「监控告警触发 / 恢复」这个真实事件源接到扩展通道上：

    AlertManager._on_evaluator_state_change()
        → dispatch_alert(alert, "firing" | "resolved")
        → 遍历已安装且 enabled 的通道
        → ChannelInstaller.send_message(channel_id, 摘要)

【边界（都是刻意的）】

1. **只发摘要**：告警名 / 级别 / 状态 / 指标值与阈值 / 白名单 labels。
   不拼 `alert.annotations` 原文、不带通道 config、不带任何 payload —— 通道地址本身
   就是外部系统，脱敏必须发生在**出站之前**（`_sanitize_value` / `_SECRET_KEY_RE`）。
2. **风暴抑制**：同一 (通道, 事件, 告警名) 在 `_COOLDOWN_SEC` 内只发一次。
   告警抖动时宁可少发，也不能把外部通道打爆（那会让运维直接静音整个通道）。
3. **失败只重试 1 次**，随后放弃并记日志；整条链路 try/except 包住，
   **绝不允许通道故障影响告警判定与自愈主流程**。
4. **可关闭，且分两层**：总开关 `CP_CHANNEL_EVENTS=0`（或 false/no/off）⇒ 直接
   return，零副作用；总开关开着时，还能按事件类型单独关（`EVENT_SWITCHES`：
   `CP_CHANNEL_EVENT_ALERTS` / `CP_CHANNEL_EVENT_TASK_FAILED` /
   `CP_CHANNEL_EVENT_TASK_COMPLETED`，默认都开）。四个键都在
   `agent/settings/registry.py` 登记，可在「开关中心」或「消息中心 · 事件出站」改，
   **热生效**（resolver 直接写进程环境变量，桥接层每次调用现读）。
5. 没有 enabled 通道 ⇒ 什么都不做（不缓存"待发"，不补发历史事件）。
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

#: 冷却窗口（秒）：同一通道对同一告警的同一事件，窗口内只发一次
_COOLDOWN_SEC = 60.0

#: 出站文本上限（字符）：通道多为 IM/webhook，过长会被截断或丢弃
_MAX_MESSAGE_CHARS = 1500
_MAX_LABELS = 5
_MAX_VALUE_CHARS = 160

#: 失败重试：总共 2 次尝试（首发 + 1 次重试）
_MAX_ATTEMPTS = 2
_RETRY_BACKOFF_SEC = 0.3

#: 键名像密钥的 label 一律不发（宁可漏一条元数据，也不漏一个密钥）
_SECRET_KEY_RE = re.compile(r"(pass|pwd|secret|token|key|cookie|auth|credential|sign)", re.I)

_state_lock = threading.Lock()
_last_sent: Dict[str, float] = {}


#: 事件类型 → 独立开关的环境变量名（总开关之外的细分；默认全开）
#:
#: 【为什么要有这一层】总开关只能"全关"：运维想留任务失败告警、只关告警抖动，
#: 或反过来，都得去改代码里的调用点。细分开关让"哪些事件出站"成为可配置事实。
EVENT_SWITCHES: Dict[str, str] = {
    "alert": "CP_CHANNEL_EVENT_ALERTS",
    "task.failed": "CP_CHANNEL_EVENT_TASK_FAILED",
    "task.completed": "CP_CHANNEL_EVENT_TASK_COMPLETED",
}


def _truthy(raw: Optional[str], default: bool = True) -> bool:
    """把环境变量原文解释成布尔（缺省/空串 ⇒ default；只认 0/false/no/off 为关）"""
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _master_enabled() -> bool:
    return _truthy(os.environ.get("CP_CHANNEL_EVENTS"))


def _event_enabled(event_kind: str) -> bool:
    """该事件类型的细分开关（**逐个字面量**读，不做"变量名查 env"）

    【为什么不用 {kind: ENV} 映射后按变量读】本仓的「开关零缺口」门禁用 AST 机械
    提取所有 env 读取点（scripts/scan_settings.py）。`os.environ.get(变量)` 会被
    记成未声明的动态家族 `<unresolved>*`，让 `test_zero_gap_between_scan_and_registry`
    直接红。故这里三个分支各写一次字面量；映射表只用于回报"被哪个键关的"。
    """
    if event_kind == "alert":
        return _truthy(os.environ.get("CP_CHANNEL_EVENT_ALERTS"))
    if event_kind == "task.failed":
        return _truthy(os.environ.get("CP_CHANNEL_EVENT_TASK_FAILED"))
    if event_kind == "task.completed":
        return _truthy(os.environ.get("CP_CHANNEL_EVENT_TASK_COMPLETED"))
    return True          # 未知事件类型只受总开关约束


def events_enabled(event_kind: str = "") -> bool:
    """事件接线开关（总开关 + 该事件类型的细分开关；默认都开）

    Args:
        event_kind: "alert" / "task.failed" / "task.completed"；空串或未知类型
            只看总开关（未知事件不会被"细分开关缺失"误关）。
    """
    if not _master_enabled():
        return False
    return _event_enabled(event_kind)


def disabled_reason(event_kind: str = "") -> str:
    """被哪个开关关掉 ⇒ 返回原因串；没关 ⇒ 空串

    两个原因串与既有单测/前端文案同源：总开关 = disabled_by_env，
    细分开关 = disabled_by_event_switch（并附 `switch` 字段给出具体键）。
    """
    if not _master_enabled():
        return "disabled_by_env"
    if not _event_enabled(event_kind):
        return "disabled_by_event_switch"
    return ""


def _reset_state_for_tests() -> None:
    """测试专用：清空冷却表（不在生产路径调用）"""
    with _state_lock:
        _last_sent.clear()


def _cooldown_ok(key: str, now: Optional[float] = None,
                 cooldown: float = _COOLDOWN_SEC) -> bool:
    """是否允许发送（并记账）。now 可注入，便于测试"""
    ts = time.monotonic() if now is None else now
    with _state_lock:
        last = _last_sent.get(key)
        if last is not None and ts - last < cooldown:
            return False
        _last_sent[key] = ts
        return True


def _sanitize_value(value: Any, limit: int = _MAX_VALUE_CHARS) -> str:
    """把任意值压成单行可出站文本（换行折叠、超长截断）"""
    text = " ".join(str(value).replace("\r", " ").replace("\n", " ").split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _truncate(text: str, limit: int = _MAX_MESSAGE_CHARS) -> str:
    """整段截断（**保留换行**：告警摘要按行读比挤成一行易读）"""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _sanitize_labels(labels: Optional[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """白名单式出站：丢掉像密钥的键，最多 `_MAX_LABELS` 条"""
    out: List[Tuple[str, str]] = []
    for key, value in (labels or {}).items():
        k = str(key)
        if _SECRET_KEY_RE.search(k):
            continue
        out.append((k, _sanitize_value(value)))
        if len(out) >= _MAX_LABELS:
            break
    return out


def _attr(alert: Any, name: str, default: Any = "") -> Any:
    """兼容 dataclass / dict 两种告警表示（测试与旧调用方都见过）"""
    if isinstance(alert, dict):
        return alert.get(name, default)
    return getattr(alert, name, default)


def render_alert(alert: Any, transition: str) -> str:
    """渲染一条告警摘要（**只含白名单字段**）

    transition: "firing" | "resolved"
    """
    name = _sanitize_value(_attr(alert, "name", "unknown"), 120)
    severity = _attr(alert, "severity", "")
    severity = _sanitize_value(getattr(severity, "value", severity), 24)
    verb = "触发" if transition == "firing" else "恢复"

    lines = [f"[云枢告警][{severity}]{name} {verb}（{transition}）"]

    if transition == "firing":
        value = _attr(alert, "value", None)
        threshold = _attr(alert, "threshold", None)
        if value is not None or threshold is not None:
            lines.append(
                "指标 %s（阈值 %s）" % (_sanitize_value(value, 32), _sanitize_value(threshold, 32))
            )
        condition = _sanitize_value(_attr(alert, "condition", ""), 120)
        if condition:
            lines.append("条件 " + condition)
    else:
        started = _attr(alert, "started_at", None)
        resolved = _attr(alert, "resolved_at", None)
        try:
            if started is not None and resolved is not None:
                lines.append("持续 %.0fs" % max(0.0, float(resolved) - float(started)))
        except (TypeError, ValueError):
            pass

    message = _sanitize_value(_attr(alert, "message", ""), 240)
    if message:
        lines.append("说明 " + message)

    labels = _sanitize_labels(_attr(alert, "labels", None))
    if labels:
        lines.append("标签 " + " ".join(f"{k}={v}" for k, v in labels))

    return _truncate("\n".join(lines), _MAX_MESSAGE_CHARS)


def _enabled_channels(installer: Any) -> List[Dict[str, Any]]:
    """已安装且 enabled 的通道（disabled 的一律不发）"""
    rows = installer.list_installed_channels() or []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("status") == "uninstalled":
            continue
        if row.get("enabled", True) is False:
            continue
        if not row.get("ext_id"):
            continue
        out.append(row)
    return out


def _send_with_retry(installer: Any, channel_id: str, message: str,
                     sleep: Any = time.sleep) -> Tuple[bool, str]:
    """最多 `_MAX_ATTEMPTS` 次；失败不抛，返回 (ok, 最后一次消息)"""
    last = ""
    for attempt in range(_MAX_ATTEMPTS):
        try:
            ok, last = installer.send_message(channel_id, message)
        except Exception as e:  # noqa: BLE001 通道实现各异，一律不外抛
            ok, last = False, f"{type(e).__name__}: {e}"
        if ok:
            return True, last
        if attempt + 1 < _MAX_ATTEMPTS:
            try:
                sleep(_RETRY_BACKOFF_SEC)
            except Exception:  # noqa: BLE001 注入的 sleep 可能被测试替换
                pass
    return False, last


def dispatch_alert(alert: Any, transition: str, *,
                   installer: Any = None,
                   now: Optional[float] = None,
                   cooldown: float = _COOLDOWN_SEC) -> Dict[str, Any]:
    """把**一条告警状态变化**发到所有已启用通道

    Args:
        alert: Alert 实例（或同字段 dict）
        transition: "firing" / "resolved"
        installer: ChannelInstaller（None ⇒ 新建默认实例；测试可注入）
        now / cooldown: 冷却注入（测试用）

    Returns:
        {ok, enabled, sent, skipped, failed, reason?} —— 永不抛
    """
    result: Dict[str, Any] = {"ok": False, "enabled": 0, "sent": [],
                              "skipped": [], "failed": []}
    reason = disabled_reason("alert")
    if reason:
        result["reason"] = reason
        if reason == "disabled_by_event_switch":
            result["switch"] = EVENT_SWITCHES["alert"]
        return result

    try:
        if installer is None:
            from agent.extensions.channels_installer import ChannelInstaller
            from agent.extensions.store import ExtensionStore
            installer = ChannelInstaller(ExtensionStore())

        channels = _enabled_channels(installer)
        result["enabled"] = len(channels)
        if not channels:
            result["ok"] = True
            result["reason"] = "no_enabled_channel"
            return result

        message = render_alert(alert, transition)
        alert_name = _sanitize_value(_attr(alert, "name", "unknown"), 120)

        for row in channels:
            channel_id = str(row.get("ext_id"))
            key = f"{channel_id}|{transition}|{alert_name}"
            if not _cooldown_ok(key, now=now, cooldown=cooldown):
                result["skipped"].append(channel_id)
                continue
            ok, detail = _send_with_retry(installer, channel_id, message)
            if ok:
                result["sent"].append(channel_id)
                logger.info(log_dict({
                    'module_name': 'channel_bridge', 'action': 'alert_sent',
                    'channel_id': channel_id, 'transition': transition,
                    'alert': alert_name,
                    'msg': f'[通道接线] 告警已发往 {channel_id}',
                }))
            else:
                result["failed"].append({"channel_id": channel_id, "error": _sanitize_value(detail, 200)})
                logger.warning(log_dict({
                    'module_name': 'channel_bridge', 'action': 'alert_send_failed',
                    'channel_id': channel_id, 'transition': transition,
                    'alert': alert_name,
                    'msg': f'[通道接线] 发送失败 {channel_id}: {detail}',
                }))

        result["ok"] = bool(result["sent"])
        return result
    except Exception as e:  # noqa: BLE001 绝不打断告警/自愈主流程
        result["reason"] = f"{type(e).__name__}: {e}"
        logger.warning(log_dict({
            'module_name': 'channel_bridge', 'action': 'dispatch_error',
            'msg': f'[通道接线] 派发异常（已忽略）: {e}',
        }))
        return result


def dispatch_event(event_type: str, summary: str, *,
                   severity: str = "info",
                   fields: Optional[Dict[str, Any]] = None,
                   dedup_key: Optional[str] = None,
                   installer: Any = None,
                   now: Optional[float] = None,
                   cooldown: float = _COOLDOWN_SEC) -> Dict[str, Any]:
    """通用事件入口（任务完成 / 定时作业等后续事件的统一落点）

    与 `dispatch_alert` 同一套冷却、脱敏、重试与开关；fields 走白名单式脱敏。
    """
    result: Dict[str, Any] = {"ok": False, "enabled": 0, "sent": [],
                              "skipped": [], "failed": []}
    reason = disabled_reason(event_type)
    if reason:
        result["reason"] = reason
        if reason == "disabled_by_event_switch":
            result["switch"] = EVENT_SWITCHES.get(event_type, "")
        return result
    try:
        if installer is None:
            from agent.extensions.channels_installer import ChannelInstaller
            from agent.extensions.store import ExtensionStore
            installer = ChannelInstaller(ExtensionStore())

        channels = _enabled_channels(installer)
        result["enabled"] = len(channels)
        if not channels:
            result["ok"] = True
            result["reason"] = "no_enabled_channel"
            return result

        lines = [f"[云枢][{_sanitize_value(severity, 24)}]{_sanitize_value(event_type, 80)}"]
        if summary:
            lines.append(_sanitize_value(summary, _MAX_VALUE_CHARS))
        label_pairs = _sanitize_labels(fields)
        if label_pairs:
            lines.append(" ".join(f"{k}={v}" for k, v in label_pairs))
        message = _truncate("\n".join(lines), _MAX_MESSAGE_CHARS)

        for row in channels:
            channel_id = str(row.get("ext_id"))
            key = f"{channel_id}|{event_type}|{dedup_key or ''}"
            if not _cooldown_ok(key, now=now, cooldown=cooldown):
                result["skipped"].append(channel_id)
                continue
            ok, detail = _send_with_retry(installer, channel_id, message)
            if ok:
                result["sent"].append(channel_id)
            else:
                result["failed"].append({"channel_id": channel_id,
                                         "error": _sanitize_value(detail, 200)})
        result["ok"] = bool(result["sent"])
        return result
    except Exception as e:  # noqa: BLE001
        result["reason"] = f"{type(e).__name__}: {e}"
        return result
