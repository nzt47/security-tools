"""工具结果裁剪（E0-2 ②）—— 超长结果保留头/尾，裁掉中段

【解决什么】spill（E0-2 ①）只在 >100KB 时把全文落盘；但 8–100KB 的结果仍会整段
进上下文：一次 `git diff`、一次大文件读取、一次宽表查询就够把窗口填满。本模块在
**spill 之后**再收一道：只留头 70% + 尾 30%，中段替换成可读的裁剪标记。

【与 spill 的分工】
  spill  ：内容保全（全文落盘 + locator + read_spill 分段取回），阈值 100KB
  pruner ：上下文预算（**不落盘**，直接裁；要细节请用更窄的参数重跑），阈值 32KB
两者串行：先 spill 后 prune；被 spill 吸收的结果（信封很小）不会被再裁。

【为什么默认开、阈值给得高】契约/单测普遍断言"小结果的原样相等"；32KB 以下的
结果不受影响。需要完全关掉：`CP_TOOL_RESULT_PRUNER=0`。

【不改形状】只替换 dict 里超长的字符串字段值，不新增键、不改键名；调用方/用例看到
的仍是同一份 schema（内容被裁剪过，标记写在字符串里）。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: 开关与阈值（字符数）
PRUNER_ENABLED_ENV = "CP_TOOL_RESULT_PRUNER"
PRUNE_CHARS_ENV = "CP_TOOL_RESULT_PRUNE_CHARS"

DEFAULT_PRUNE_CHARS = 32_000
MIN_PRUNE_CHARS = 4_000
MAX_PRUNE_CHARS = 200_000

#: 头/尾比例（头多尾少：错误栈与结论常在尾部）
HEAD_RATIO = 0.7

#: 会被裁剪的字段名（工具返回契约里的长文本载体）
PRUNE_FIELDS: Tuple[str, ...] = (
    "stdout", "stderr", "content", "result", "output", "text", "diff", "body",
)


def enabled() -> bool:
    """裁剪是否开启（`=0/false/no/off` 关闭；缺省开）"""
    raw = str(os.environ.get(PRUNER_ENABLED_ENV, "") or "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def prune_limit() -> int:
    """阈值（字符）：非法/越界回落默认，不静默放大"""
    raw = str(os.environ.get(PRUNE_CHARS_ENV, "") or "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PRUNE_CHARS
    return max(MIN_PRUNE_CHARS, min(value, MAX_PRUNE_CHARS))


def prune_text(text: str, limit: Optional[int] = None) -> Tuple[str, int]:
    """按头/尾裁剪一段文本；返回 (新文本, 被裁字符数)

    未超限 ⇒ 原样返回、裁掉 0（调用方据此判断"这条结果有没有被动过"）。
    """
    body = str(text or "")
    cap = int(limit or prune_limit())
    if len(body) <= cap:
        return body, 0
    head_len = max(1, int(cap * HEAD_RATIO))
    tail_len = max(1, cap - head_len)
    removed = len(body) - head_len - tail_len
    marker = ("\n…[工具结果已裁剪：中间 %d 字符未进入上下文；"
              "需要细节请用更窄的参数重跑或分页读取]…\n" % removed)
    return body[:head_len] + marker + body[-tail_len:], removed


def prune_result(tool: str, result: Any,
                 limit: Optional[int] = None) -> Tuple[Any, Dict[str, Any]]:
    """裁剪 dict 结果里的长文本字段；返回 (结果, 元信息)

    元信息恒为 dict：`{pruned: bool, fields: [...], removed_chars: N, limit: N}`，
    供审计/日志使用（不写进工具结果本身）。
    """
    meta: Dict[str, Any] = {"pruned": False, "fields": [], "removed_chars": 0,
                            "limit": int(limit or prune_limit())}
    if not enabled():
        return result, meta
    if not isinstance(result, dict):
        return result, meta
    cap = meta["limit"]
    changed = False
    out = dict(result)
    for field in PRUNE_FIELDS:
        value = out.get(field)
        if not isinstance(value, str) or len(value) <= cap:
            continue
        new_value, removed = prune_text(value, cap)
        out[field] = new_value
        meta["fields"].append(field)
        meta["removed_chars"] += removed
        changed = True
    if changed:
        meta["pruned"] = True
        logger.info("[tool-result-pruner] %s 裁剪 %s，共 %d 字符（阈值 %d）",
                    tool, ",".join(meta["fields"]), meta["removed_chars"], cap)
        return out, meta
    return result, meta


__all__ = [
    "PRUNER_ENABLED_ENV", "PRUNE_CHARS_ENV", "DEFAULT_PRUNE_CHARS",
    "MIN_PRUNE_CHARS", "MAX_PRUNE_CHARS", "HEAD_RATIO", "PRUNE_FIELDS",
    "enabled", "prune_limit", "prune_text", "prune_result",
]
