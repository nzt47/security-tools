"""知识库操作日志（log.md 顶部追加）。

任务1 契约的最小落地（任务2 Step 5 依赖）：
    append_log(action, slug, detail="") -> bool

【不易】AGENTS.md §4 日志规则：每次写操作在 log.md 顶部追加一条
    `## [YYYY-MM-DD] <action> | <slug> | <detail>`
【变易】追加可选 `log_path` 参数（默认 knowledge/log.md），测试与自定义布局可覆盖；
        与任务1 契约签名（前三参数）保持兼容。
【不易】并发安全：进程内 threading.Lock + 原子写（临时文件 + os.replace），
        不产生半行日志。
"""

from __future__ import annotations

import os
import threading
from datetime import date
from pathlib import Path

DEFAULT_LOG_PATH = "knowledge/log.md"

# 现有 log.md 的头部注释标记：新记录插入到该行下方（顶部语义）
_MARKER = "<!-- 新记录追加到此行下方（顶部） -->"

_LOCK = threading.Lock()


def _atomic_write(path: Path, text: str) -> None:
    """同目录临时文件 + os.replace，保证原子性（防并发半写）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def append_log(
    action: str,
    slug: str,
    detail: str = "",
    log_path: str | Path = DEFAULT_LOG_PATH,
) -> bool:
    """在 log.md 顶部追加时间戳记录（线程安全 + 原子写）。

    记录格式（AGENTS.md）：`## [YYYY-MM-DD] <action> | <slug> | <detail>`
    插入位置：现有 `<!-- 新记录追加到此行下方（顶部） -->` 标记之后；
    无标记时（新建/旧版文件）插到文件最顶部。
    """
    record = f"## [{date.today().isoformat()}] {action} | {slug}"
    if detail:
        record += f" | {detail}"

    with _LOCK:
        path = Path(log_path)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if _MARKER in text:
            text = text.replace(_MARKER, _MARKER + "\n" + record, 1)
        else:
            text = record + ("\n\n" + text if text else "")
        _atomic_write(path, text)
    return True
