"""外部记忆的本地读取器（全部只读，零第三方依赖）。

- leveldb：Electron/Chromium 的 IndexedDB 与 Local Storage 落盘格式；
- trae：Trae CN 的 state.vscdb 与 leveldb 目录；
- claude：Claude Code 的 CLAUDE.md / history.jsonl / 会话转录。
"""

from __future__ import annotations

import os

#: 单源记录上限的环境开关（登记在 agent/settings/registry.py →
#: memory_import.record_cap，规则见 agent/monitoring/observability_config.py）
RECORD_CAP_ENV = "CP_MEMORY_IMPORT_MAX_RECORDS"
#: clamp 范围（与 observability_config 的校验区间一致）
RECORD_CAP_MIN = 100
RECORD_CAP_MAX = 1000000


def record_cap(default: int) -> int:
    """读单源记录上限：非法/越界回退默认值（绝不因环境变量写坏而炸掉盘点）。"""
    raw = str(os.environ.get(RECORD_CAP_ENV, "") or "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return int(default)
    if value < RECORD_CAP_MIN or value > RECORD_CAP_MAX:
        return int(default)
    return value


__all__ = ["RECORD_CAP_ENV", "RECORD_CAP_MAX", "RECORD_CAP_MIN", "claude",
           "leveldb", "record_cap", "trae"]
