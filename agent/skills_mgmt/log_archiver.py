"""评审-消化日志按日归档

把 digest 事件 / 人工复核审计这类 JSONL 按 ts 的日期分档：
- 当日记录保留在原文件；
- 历史记录（ts 早于今天）移入 `<stem>-YYYYMMDD<ext>` 归档文件（追加模式）。
幂等：进程内按“路径→日期”记忆已归档，重复调用/轮询廉价；
文件缺失/无历史行时零操作。
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 进程内归档记忆：{str(resolved_path): "YYYY-MM-DD"}
_ARCHIVED: Dict[str, str] = {}


def _ts_day(line: str) -> Optional[str]:
    """从 JSONL 行的 ts 字段取 YYYY-MM-DD；无法解析返回 None。"""
    try:
        rec = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    ts = str(rec.get("ts", "") or "")
    return ts[:10] if len(ts) >= 10 else None


def archive_daily_file(path: Path | str) -> dict:
    """把 path（JSONL）中的历史行按日归档到同目录 `<stem>-YYYYMMDD<ext>`。

    Returns:
        {"archived": 当天归档行总数, "files": [归档文件列表], "today": 今日行数}
    """
    p = Path(path).resolve()
    if not p.exists():
        return {"archived": 0, "files": [], "today": 0}
    today = date.today().isoformat()
    if _ARCHIVED.get(str(p)) == today:
        return {"archived": 0, "files": [], "today": 0}

    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as e:
        logger.warning("[LogArchive] 读取失败 %s: %s", p, e)
        return {"archived": 0, "files": [], "today": 0}

    today_lines: list[str] = []
    buckets: Dict[str, list[str]] = {}
    moved = 0
    for line in lines:
        line = (line or "").strip()
        if not line:
            continue
        day = _ts_day(line)
        if day is None or day >= today:
            today_lines.append(line)
        else:
            buckets.setdefault(day, []).append(line)
            moved += 1

    if not buckets:
        _ARCHIVED[str(p)] = today
        return {"archived": 0, "files": [], "today": len(today_lines)}

    files: list[str] = []
    try:
        if today_lines:
            p.write_text("\n".join(today_lines) + ("\n" if today_lines else ""), encoding="utf-8")
        else:
            p.unlink(missing_ok=True)
        for day, day_lines in sorted(buckets.items()):
            arch = p.with_name(f"{p.stem}-{day}{p.suffix}")
            with open(arch, "a", encoding="utf-8") as f:
                for dl in day_lines:
                    f.write(dl + "\n")
            files.append(str(arch))
    except OSError as e:
        logger.warning("[LogArchive] 归档写入失败 %s: %s", p, e)
        return {"archived": 0, "files": files, "today": len(today_lines)}

    _ARCHIVED[str(p)] = today
    logger.info("[LogArchive] %s → 归档 %d 行 → %s（今日保留 %d 行）",
                p.name, moved, ",".join(files) or "-", len(today_lines))
    return {"archived": moved, "files": files, "today": len(today_lines)}
