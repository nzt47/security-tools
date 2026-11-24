"""大输出 spill（E0-2）：超过阈值的结果落盘，回 `{spilled, locator, preview}`，可按 locator 取回。

【为什么】现状是 `_truncate_output` / 各工具的硬上限：**丢了就没了**。DSH 的做法是
`spill` + `locator` + `read_spill`：工具结果超过阈值时把全文写到会话/天级别的目录，
只把「头 + 尾 + 一句提示」回给模型，模型需要细节时再按 locator 分段取。

【边界】
- 这是**内容保全**，不是权限边界：spill 文件在 `agent/data/spill/`（运行期、不提交），
  路径由**常量目录 + 校验过的 locator** 决定（不接受任意路径），见 `read_locator`。
- 只对 `dict` 结果生效（工具返回契约都是 dict）；`read_spill` 自身不 spill（防递归）。
- 结果里的 `ok` 原样保留（下游/审批链路依赖它）；`blocked`/`needs_approval` 类结果很小，
  一般不会触发阈值。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SPILL_ENABLED_ENV = "CP_SPILL_ENABLED"
SPILL_THRESHOLD_ENV = "CP_SPILL_THRESHOLD_BYTES"
SPILL_PREVIEW_ENV = "CP_SPILL_PREVIEW_BYTES"

#: locator 形状：`spill:<16 hex>`（id 由时间 + 随机 + 摘要派生，不接受任意路径）
_LOCATOR_RE = re.compile(r"^spill:([0-9a-f]{16})$")

_THRESHOLD_DEFAULT = 100_000
_THRESHOLD_MIN = 4_096
_THRESHOLD_MAX = 1_048_576
_PREVIEW_DEFAULT = 8_000
_PREVIEW_MIN = 512
_PREVIEW_MAX = 65_536
#: 预览头尾比（头 : 尾 = 4 : 1）
_TAIL_RATIO = 4
#: 打开 spill 时把**生产者**的硬截断抬到 1 MiB（否则 spill 到的已经是被截断的文本）
_CAPTURE_LIMIT = 1_048_576
#: `read_spill` 单次返回的字符上限（防止模型一次又要回整篇）
_READ_LIMIT_MAX = 200_000
_READ_LIMIT_DEFAULT = 20_000


def spill_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "spill"


def enabled() -> bool:
    raw = str(os.environ.get("CP_SPILL_ENABLED", "1") or "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _clamp_int(raw: Any, default: int, lo: int, hi: int, name: str) -> int:
    """把环境变量原文钳到区间；空/非法回落默认（**调用方必须字面量传入**）。"""
    text = str(raw or "").strip()
    if not text:
        return default
    try:
        value = int(float(text))
    except (TypeError, ValueError):
        logger.warning("[spill] %s=%r 不是整数，回落到 %s", name, text, default)
        return default
    return max(lo, min(hi, value))


def threshold_bytes() -> int:
    # 逐条字面量读取（settings 零缺口门禁不接受动态家族）
    return _clamp_int(os.environ.get("CP_SPILL_THRESHOLD_BYTES"), _THRESHOLD_DEFAULT,
                      _THRESHOLD_MIN, _THRESHOLD_MAX, "CP_SPILL_THRESHOLD_BYTES")


def preview_bytes() -> int:
    return _clamp_int(os.environ.get("CP_SPILL_PREVIEW_BYTES"), _PREVIEW_DEFAULT,
                      _PREVIEW_MIN, _PREVIEW_MAX, "CP_SPILL_PREVIEW_BYTES")


def capture_limit(default: int) -> int:
    """生产者的硬截断上限：spill 开着时抬到 1 MiB，否则维持原值。"""
    if not enabled():
        return int(default)
    return max(int(default), _CAPTURE_LIMIT)


def _serialize(result: Any) -> Optional[str]:
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None


def _new_id(text: str) -> str:
    seed = f"{time.time_ns()}:{os.getpid()}:{len(text)}:{text[:64]}"
    return hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()[:16]


def spill_text(tool: str, text: str) -> Dict[str, Any]:
    """把 text 落盘并返回信封（调用方负责决定是否要用它替换原结果）。"""
    directory = spill_dir()
    directory.mkdir(parents=True, exist_ok=True)
    spill_id = _new_id(text)
    path = directory / f"{spill_id}.txt"
    path.write_text(text, encoding="utf-8", errors="replace")
    meta = {
        "locator": f"spill:{spill_id}",
        "tool": str(tool),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "chars": len(text),
        "bytes": len(text.encode("utf-8", "replace")),
        "sha256_16": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16],
    }
    try:
        (directory / f"{spill_id}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return meta


def _preview(text: str) -> str:
    limit = preview_bytes()
    if len(text) <= limit:
        return text
    tail = max(1, limit // _TAIL_RATIO)
    head = max(1, limit - tail)
    omitted = len(text) - head - tail
    return (text[:head] + f"\n...（中间省略 {omitted} 字符；用 read_spill 取回）...\n"
            + text[-tail:])


def spill_result(tool: str, result: Any) -> Optional[Dict[str, Any]]:
    """结果过大时返回 spill 信封；不需要 spill 返回 None。

    Args:
        tool: 工具名（`read_spill` 自身会被跳过）
        result: handler 的原始返回值（非 dict 一律不处理）
    """
    if not enabled() or str(tool) == "read_spill":
        return None
    if not isinstance(result, dict):
        return None
    if result.get("spilled") is True:
        return None
    text = _serialize(result)
    if text is None or len(text) <= threshold_bytes():
        return None
    meta = spill_text(tool, text)
    envelope: Dict[str, Any] = {
        "ok": bool(result.get("ok", True)),
        "spilled": True,
        "locator": meta["locator"],
        "bytes": meta["bytes"],
        "chars": meta["chars"],
        "tool": str(tool),
        "preview": _preview(text),
        "hint": "结果过大已落盘：用 read_spill(locator=..., offset=..., limit=...) 分段读取",
    }
    if result.get("error"):
        envelope["error"] = result["error"]
    return envelope


def read_locator(locator: Any, offset: Any = 0, limit: Any = _READ_LIMIT_DEFAULT) -> Dict[str, Any]:
    """按 locator 分段读取落盘内容（offset/limit 以**字符**计）。"""
    match = _LOCATOR_RE.match(str(locator or "").strip())
    if not match:
        return {"ok": False,
                "error": "locator 不合法（期望形如 spill:<16 位十六进制>）"}
    spill_id = match.group(1)
    path = spill_dir() / f"{spill_id}.txt"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"ok": False, "locator": f"spill:{spill_id}",
                "error": "locator 对应的落盘文件不存在（可能已被清理）"}
    except OSError:
        return {"ok": False, "locator": f"spill:{spill_id}",
                "error": "读取落盘文件失败（详见服务日志）"}
    try:
        start = max(0, int(offset))
    except (TypeError, ValueError):
        start = 0
    try:
        take = int(limit)
    except (TypeError, ValueError):
        take = _READ_LIMIT_DEFAULT
    take = max(1, min(_READ_LIMIT_MAX, take))
    chunk = text[start:start + take]
    end = start + len(chunk)
    return {
        "ok": True,
        "locator": f"spill:{spill_id}",
        "offset": start,
        "limit": take,
        "chars_total": len(text),
        "returned_chars": len(chunk),
        "next_offset": end if end < len(text) else None,
        "content": chunk,
    }


def status() -> Dict[str, Any]:
    directory = spill_dir()
    files = 0
    total_bytes = 0
    if directory.is_dir():
        for path in directory.glob("*.txt"):
            files += 1
            try:
                total_bytes += path.stat().st_size
            except OSError:
                pass
    return {
        "enabled": enabled(),
        "threshold_bytes": threshold_bytes(),
        "preview_bytes": preview_bytes(),
        "capture_limit_bytes": capture_limit(0),
        "dir": str(directory),
        "files": files,
        "total_bytes": total_bytes,
    }


__all__ = ["SPILL_ENABLED_ENV", "SPILL_THRESHOLD_ENV", "SPILL_PREVIEW_ENV", "spill_dir",
           "enabled", "threshold_bytes", "preview_bytes", "capture_limit", "spill_text",
           "spill_result", "read_locator", "status"]

