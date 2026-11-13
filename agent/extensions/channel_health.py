# -*- coding: utf-8 -*-
"""出站通道投递健康台账（2026-11-13）。

【为什么要有这一本账】2026-11-13 现场：邮件通道 TLS 抖动，Owner 点「测试发送」
只看到一行失败文案，顶栏铃铛没有任何告警 —— 也就是说**通道发不出去这件事，
只有当时点按钮的那一个人知道**。报警器原有的 6 个事件源（健康 / 单位看板 / 规则 /
审计链 / 计划任务 / CI）没有一个在看出站投递。

【做法】在唯一发送收口 ``ChannelInstaller.send_message`` 记账：
- 成功 ⇒ ``consecutive_failures = 0`` + ``last_ok_at``；
- 失败（内部重试之后仍失败）⇒ ``consecutive_failures += 1`` + ``last_code``。
采集线程每 60s 读这本账，连续失败达到阈值就报 ``channel.<通道>``（critical）。

【口径（改要同批改用例）】
1. **只记类别，不记原文**：``auth`` / ``tls`` / ``timeout`` / ``disconnected`` / ``other``。
   异常原文、SMTP 地址、账号、授权码**一律不落盘**（按本仓口径，凭据连指纹/长度也不进日志）。
2. **一次失败就算一次**：``send_message`` 内部已经做过瞬时错误重试（手动路径），
   走到记账处的失败 = "重试过还是不行"，所以阈值是 1，不再叠加等待。
3. **文件缺失 = 没有测量**：采集源返回 None（不报警、也不判恢复），
   由 ``agent/alarms/collector.py`` 的 keep 语义兜底。
4. **测试进程不许写生产台账**：判据是 ``sys.modules``（不新增 env 开关，否则
   「开关零缺口」门禁会把测试专用的读取点判成未登记开关 —— 2026-11-13 踩过）。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: 台账路径（运行期状态；data/ 已在 .gitignore）
STATE_REL_PATH = os.path.join("data", "extensions", "channel_health.json")

#: 失败类别（**只存类别**；中文短语只在告警 detail 里拼）
CODE_AUTH = "auth"
CODE_TLS = "tls"
CODE_TIMEOUT = "timeout"
CODE_DISCONNECTED = "disconnected"
CODE_OTHER = "other"

CODE_LABELS: Dict[str, str] = {
    CODE_AUTH: "登录被拒（账号或授权码不对，详见服务日志）",
    CODE_TLS: "TLS 握手超时（网络抖动或被中间设备拦截）",
    CODE_TIMEOUT: "连接超时（检查出网/代理）",
    CODE_DISCONNECTED: "连接被对端断开",
    CODE_OTHER: "发送失败（原因详见服务日志）",
}

#: 连续失败达到该值就报警。
#: 【为什么是 1】记账点在 ``send_message`` 的**最终失败**处：手动路径内部已重试过一次
#: （SEND_RETRY_ATTEMPTS=2），报警外发路径由 channel_bridge 自己重试 —— 能走到这里，
#: 说明"已经试过、确实没发出去"；再等一轮只是让 Owner 晚 60s 知道。
FAIL_ALARM_THRESHOLD = 1

#: 仓库根（**真实**路径，不受用例改写影响）——用于"测试期不写生产台账"的判据
_ROOT = Path(__file__).resolve().parents[2]

#: 台账文件（用例可 monkeypatch 到 tmp）
STATE_PATH = _ROOT / STATE_REL_PATH

_LOCK = threading.Lock()


def _path() -> Path:
    return Path(STATE_PATH)


def _now_iso(now: Optional[float] = None) -> str:
    ts = time.time() if now is None else float(now)
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _running_under_pytest() -> bool:
    """当前进程是否在跑测试（不读环境变量，避免被「开关零缺口」门禁判成未登记开关）。"""
    return "pytest" in sys.modules or "_pytest" in sys.modules


def _write_blocked(target: Path) -> bool:
    """测试进程 + 目标是真实仓库 ⇒ 不写（与 zero_token_jobs._save_pending 同款闸）。"""
    if not _running_under_pytest():
        return False
    try:
        resolved = str(Path(target).resolve())
    except OSError:
        return False
    return resolved.startswith(str(_ROOT) + os.sep)


def load() -> Dict[str, Any]:
    """读台账（文件缺失/坏 JSON ⇒ 空台账；坏文件只记类别，不抛）。"""
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"updated_at": None, "channels": {}}
    except Exception as exc:  # noqa: BLE001 台账坏了不能影响发送
        logger.warning("[channel-health] 台账读取失败（%s）", type(exc).__name__)
        return {"updated_at": None, "channels": {}}
    if not isinstance(raw, dict) or not isinstance(raw.get("channels"), dict):
        return {"updated_at": None, "channels": {}}
    return raw


def _save(state: Dict[str, Any]) -> None:
    """原子写（tmp + os.replace）；失败只记日志 —— 记账是旁路，不能拖垮发送。"""
    target = _path()
    if _write_blocked(target):
        logger.debug("[channel-health] 测试进程不写生产台账: %s", target)
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(str(tmp), str(target))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[channel-health] 台账写入失败（%s）", type(exc).__name__)


def _row(state: Dict[str, Any], channel_id: str) -> Dict[str, Any]:
    channels = state.setdefault("channels", {})
    row = channels.get(channel_id)
    if not isinstance(row, dict):
        row = {"last_ok_at": None, "last_fail_at": None, "consecutive_failures": 0,
               "last_code": "", "total_ok": 0, "total_fail": 0}
        channels[channel_id] = row
    return row


def record_success(channel_id: str, *, now: Optional[float] = None) -> None:
    """记一次成功投递（把连续失败清零 ⇒ 告警下一轮自动恢复）。"""
    cid = str(channel_id or "").strip()
    if not cid:
        return
    with _LOCK:
        state = load()
        row = _row(state, cid)
        stamp = _now_iso(now)
        row["last_ok_at"] = stamp
        row["consecutive_failures"] = 0
        row["last_code"] = ""
        row["total_ok"] = int(row.get("total_ok") or 0) + 1
        state["updated_at"] = stamp
        _save(state)


def record_failure(channel_id: str, code: str = CODE_OTHER, *, now: Optional[float] = None) -> None:
    """记一次失败投递（只写类别；阈值见 FAIL_ALARM_THRESHOLD）。"""
    cid = str(channel_id or "").strip()
    if not cid:
        return
    normalized = code if code in CODE_LABELS else CODE_OTHER
    with _LOCK:
        state = load()
        row = _row(state, cid)
        stamp = _now_iso(now)
        row["last_fail_at"] = stamp
        row["consecutive_failures"] = int(row.get("consecutive_failures") or 0) + 1
        row["last_code"] = normalized
        row["total_fail"] = int(row.get("total_fail") or 0) + 1
        state["updated_at"] = stamp
        _save(state)


def snapshot(*, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """台账快照；**文件不存在 ⇒ None**（没有测量 ≠ 正常，交给采集层 keep）。"""
    path = _path()
    if not path.exists():
        return None
    state = load()
    return {
        "updated_at": state.get("updated_at"),
        "channels": dict(state.get("channels") or {}),
        "generated_at": _now_iso(now),
    }


def reset_for_tests() -> None:
    """清空台账（只给用例用；生产路径不调用）。"""
    with _LOCK:
        try:
            _path().unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:  # noqa: BLE001
            logger.debug("[channel-health] 清空失败（%s）", type(exc).__name__)


__all__ = [
    "STATE_REL_PATH", "STATE_PATH", "FAIL_ALARM_THRESHOLD",
    "CODE_AUTH", "CODE_TLS", "CODE_TIMEOUT", "CODE_DISCONNECTED", "CODE_OTHER", "CODE_LABELS",
    "load", "record_success", "record_failure", "snapshot", "reset_for_tests",
]
