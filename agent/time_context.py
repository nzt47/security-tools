"""每步时钟（time-context，E0-4①）：让模型知道「现在几点、这一轮已经跑了多久」。

【为什么要一个独立叶子模块】注入点只能有**一处**。工作台的易变尾簇（plugins/chat.py 的
_volatile_parts → 落到请求最后一条消息）已经是「每轮都变、不进缓存前缀」的正式通道；
时间块顺着它走，既不破坏缓存前缀（时间每轮都变），也不需要第二个注入点。

【默认关】CP_TIME_CONTEXT 未设置/空 ⇒ 不注入。理由：时间戳会改变「同一问题同一答案」的
复现性（排障/回归时先关它），且并非所有场景都需要。

【边界】
- 只做提示，不做硬约束：不会打断工具、不会取消请求、不参与超时判定（那是
  agent/timeout_budget.py 的事）；
- 时间取自**服务进程本地时钟**，不做时区换算（用户与服务同机）；
- 任何异常都返回空串（提示词装配失败绝不影响对话）。
"""
from __future__ import annotations

import os
import time
from datetime import datetime

#: 每步时钟开关（默认关：未设置 = 不注入）
ENV_TIME_CONTEXT = "CP_TIME_CONTEXT"
_TRUE_VALUES = ("1", "true", "yes", "on")
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def enabled() -> bool:
    """是否注入时间块（默认关；只有显式写 1/true/yes/on 才开）"""
    return str(os.environ.get(ENV_TIME_CONTEXT, "")).strip().lower() in _TRUE_VALUES


def stamp(now=None) -> str:
    """本地时间的人类可读形态：2026-11-27 09:12:33（周五）"""
    ts = datetime.fromtimestamp(time.time() if now is None else float(now))
    return "%s（%s）" % (ts.strftime("%Y-%m-%d %H:%M:%S"), _WEEKDAYS[ts.weekday()])


def tail_text(started_at=None, now=None) -> str:
    """时间块正文；开关关或计算失败 ⇒ 空串（调用方直接拼进易变尾簇即可）。

    Args:
        started_at: 本轮开始时间（time.time() 口径）。None = 不报耗时。
        now: 注入用「此刻」（测试注入固定时间；生产不传）。
    """
    if not enabled():
        return ""
    try:
        current = time.time() if now is None else float(now)
        parts = ["【时间上下文】现在 %s" % stamp(current)]
        if started_at is not None:
            try:
                elapsed = max(0.0, current - float(started_at))
                parts.append("本轮已进行 %.1fs" % elapsed)
            except (TypeError, ValueError):
                pass                      # 耗时算不出来就只报时刻，不猜
        parts.append("（仅供判断先后与等待；不是硬约束，不会打断工具）")
        return "；".join(parts)
    except Exception:  # noqa: BLE001 提示词装配失败绝不影响对话
        return ""


__all__ = ["ENV_TIME_CONTEXT", "enabled", "stamp", "tail_text"]
