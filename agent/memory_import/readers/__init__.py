"""外部记忆的本地读取器（全部只读，零第三方依赖）。

- leveldb：Electron/Chromium 的 IndexedDB 与 Local Storage 落盘格式；
- trae：Trae CN 的 state.vscdb 与 leveldb 目录；
- claude：Claude Code 的 CLAUDE.md / history.jsonl / 会话转录。
"""

from __future__ import annotations

__all__ = ["claude", "leveldb", "trae"]
