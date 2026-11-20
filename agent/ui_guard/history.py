# -*- coding: utf-8 -*-
"""ui_guard 检查历史（面板「趋势」行，2026-11-20）。

口径：
    - 只记**显式检查**：面板带 record=1 的 GET /check、工具 ui_guard_check；
      拓扑状态源 health 不记（30s 轮询会淹掉趋势）。
    - 记录是运行期状态（data/ui_guard_history.jsonl），**不入库**；写失败只告警。
    - 头部截断：只保留最近 MAX_RECORDS 条（默认 200），避免无限增长。
    - 读取容忍坏行（历史文件被手工改过也不影响面板）。

路径可用 UI_GUARD_HISTORY_PATH 覆盖（测试/多实例隔离用）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "ui_guard_history.jsonl"
MAX_RECORDS = 200

_LOCK = threading.Lock()


def _path(path: Optional[os.PathLike[str] | str] = None) -> Path:
    if path:
        return Path(path)
    env = os.environ.get("UI_GUARD_HISTORY_PATH")
    return Path(env) if env else DEFAULT_PATH


def _item(report: Any, source: str) -> Dict[str, Any]:
    counts = dict(getattr(report, "counts", {}) or {})
    baseline = getattr(report, "baseline", {}) or {}
    return {
        "ts": getattr(report, "generated_at", "") or datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "source": source,
        "mode": getattr(report, "mode", "fast"),
        "commit": getattr(report, "commit", ""),
        "error": int(counts.get("error", 0) or 0),
        "warning": int(counts.get("warning", 0) or 0),
        "total": int(counts.get("total", 0) or 0),
        "scanned_files": int(getattr(report, "scanned_files", 0) or 0),
        "regressions": len(baseline.get("regressions") or []),
        "duration_ms": float(getattr(report, "duration_ms", 0.0) or 0.0),
    }


def record(report: Any, *, source: str = "api",
           path: Optional[os.PathLike[str] | str] = None) -> bool:
    """追加一条检查记录（只保留最近 MAX_RECORDS 条）；失败不抛。"""
    item = _item(report, source)
    try:
        with _LOCK:
            p = _path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            lines: List[str] = []
            if p.exists():
                try:
                    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
                except OSError:
                    lines = []
            lines = lines[-(MAX_RECORDS - 1):]
            lines.append(json.dumps(item, ensure_ascii=False))
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(tmp, p)
        return True
    except OSError as exc:
        logger.warning("[ui_guard] 检查历史写入失败: %s", exc)
        return False


def recent(*, limit: int = 20,
           path: Optional[os.PathLike[str] | str] = None) -> List[Dict[str, Any]]:
    """返回最近 limit 条（新 → 旧；坏行跳过）。"""
    try:
        n = max(1, min(int(limit), MAX_RECORDS))
    except (TypeError, ValueError):
        n = 20
    p = _path(path)
    if not p.exists():
        return []
    try:
        raw = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    items: List[Dict[str, Any]] = []
    for line in raw[-n:]:
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            items.append(obj)
    items.reverse()
    return items
