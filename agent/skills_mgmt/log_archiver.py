"""评审-评估日志按日归档

把评估事件 / 人工复核审计这类 JSONL 按 ts 的日期分档：
- 当日记录保留在原文件；
- 历史记录（ts 早于今天）移入 `<stem>-YYYYMMDD<ext>` 归档文件（追加模式）。
幂等：进程内按“路径→日期”记忆已归档，重复调用/轮询廉价；
文件缺失/无历史行时零操作。

术语纪律（TASK-S0-01）：云枢旧 digest（评审语义）已更名 review/assess——
事件文件新名 data/skills_assessment_events.jsonl，旧名只读兼容（见下）。
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import date
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 进程内归档记忆：{str(resolved_path): "YYYY-MM-DD"}
_ARCHIVED: Dict[str, str] = {}

# 评估事件文件名（新名为主；旧名 skills_digest_events.jsonl 为评审语义旧名，
# ≤1 minor 只读兼容：首用迁移 copy + 读取兜底，不删除旧档）
ASSESSMENT_EVENTS_BASENAME = "skills_assessment_events.jsonl"
LEGACY_DIGEST_EVENTS_BASENAME = "skills_digest_events.jsonl"


def repo_data_dir() -> Path:
    """仓库根 data/ 目录（与 store/service 的落盘位置一致，绝对路径）。"""
    return Path(__file__).resolve().parent.parent.parent / "data"


def active_events_file() -> Path:
    """评估事件活动文件（新名为主）。

    兼容策略：新文件不存在而旧名 live 文件存在时，一次性 copy 旧内容到新文件
    （旧文件保留只读兼容，供仍读旧名的旧版本使用；≤1 minor 后随旧 API 移除）。
    """
    new_p = repo_data_dir() / ASSESSMENT_EVENTS_BASENAME
    old_p = repo_data_dir() / LEGACY_DIGEST_EVENTS_BASENAME
    if not new_p.exists() and old_p.exists():
        try:
            repo_data_dir().mkdir(parents=True, exist_ok=True)
            shutil.copyfile(old_p, new_p)
            logger.info("[LogArchive] 评估事件文件迁移 %s → %s（旧名保留只读兼容）",
                        old_p.name, new_p.name)
        except OSError as e:  # noqa: BLE001 迁移失败不阻断：读取端会兜底旧名
            logger.warning("[LogArchive] 事件文件迁移失败 %s: %s", old_p.name, e)
    return new_p


def events_files() -> Dict[str, Path]:
    """当前可用的评估事件 live 文件（primary 新名 + legacy 旧名，供读取/清理）。"""
    return {
        "primary": active_events_file(),
        "legacy": repo_data_dir() / LEGACY_DIGEST_EVENTS_BASENAME,
    }


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
