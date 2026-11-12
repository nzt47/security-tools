# -*- coding: utf-8 -*-
"""日志异常聚合（2026-11-12）：把"日志里 ERROR 有没有爆炸"读成一个单位读数。

【要解决什么】六条事件源与单位看板此前都不看日志文本：进程活着、探针全绿，也可能
正在每秒钟刷 ERROR（本仓 2026-11 的部署日志里就出现过 4 万行级别）。这里做**最小可用**
的一层：取 logs/ 下最新的几个 *.log 的尾部窗口，统计 ERROR / WARNING / Traceback 行数，
按"绝对条数 + 占比"判 warn。它只回答一个问题：**现在这批日志像不像在爆错**。

【口径（写死，改要同批改测试）】
- never：一个可读的 .log 都找不到（没部署 / 日志被清理）⇒ 不进健康分分母。
- warn：ERROR 行数 >= MIN_ERRORS(20) **且** ERROR 占比 >= MIN_ERROR_RATIO(5%)（尾窗内）。
  两个条件同时满足才算：只看占比会被"小文件里 1 条错"误伤；只看条数会被"巨量 INFO 淹没"。
  Traceback 头行按 ERROR 计（一次未捕获异常通常先写 Traceback 再写 ERROR）。
- ok：其余情况（含"这个窗口里真的没有错"）。
- 只读：不写基线文件、不轮转日志、不建单例（探针每 60s 被报警采集线程调一次）。

【v1 的诚实边界】这是**尾窗密度启发式**，不是时序基线：缓慢恶化（每天多一点）抓不到，
只抓"突发/持续爆炸"。升级方向：按分钟分桶 + 与过去 N 个窗口的中位数比较，但那需要
一个写基线的周期任务（属于新数据面，先不做）；当前实现宁可漏报也不误报。

【不读什么】不读 *.jsonl（watchdog 事件流 / 审计流是结构化数据，不是"错"），
不读 .log 之外的扩展名；只取尾部 TAIL_LINES 行，避免把 40MB 日志整读进内存。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

#: 日志目录（相对仓库根）
LOG_DIR_REL = "logs"
#: 每个文件只看尾部多少行
TAIL_LINES = 5000
#: 最多聚合几个文件（按 mtime 最新）
MAX_FILES = 3
#: 判 warn 的两个条件（同时满足）
MIN_ERRORS = 20
MIN_ERROR_RATIO = 0.05

#: 单位 ID / 展示名 / 家族（与包声明里的 id 一致）
UNIT_ID = "ops.logs"
UNIT_LABEL = "日志异常聚合"
UNIT_FAMILY = "ops"

_ERROR_RE = re.compile(r"(^|\s)(\[ERROR\s*\]|ERROR\s+\[|ERROR\s+\|)")
_WARN_RE = re.compile(r"(^|\s)(\[WARNING\s*\]|WARNING\s+\[|WARNING\s+\|)")
_TB_RE = re.compile(r"Traceback \(most recent call last\)")


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def candidate_logs(log_dir: Optional[Path] = None, limit: int = MAX_FILES) -> List[Path]:
    """按 mtime 取最新的若干个 *.log（不读 *.jsonl；目录缺失 ⇒ 空）。"""
    base = Path(log_dir) if log_dir is not None else _root() / LOG_DIR_REL
    try:
        files = [p for p in base.iterdir() if p.is_file() and p.suffix == ".log"]
    except OSError:
        return []
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
    return files[:max(1, int(limit))]


def _tail_lines(path: Path, tail_lines: int) -> List[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as exc:
        logger.debug("[LogAnomaly] 读取 %s 失败（%s）", path.name, type(exc).__name__)
        return []
    return lines[-max(1, int(tail_lines)):]


def scan(log_dir: Optional[Path] = None, *, tail_lines: int = TAIL_LINES,
         max_files: int = MAX_FILES) -> Dict[str, object]:
    """扫当前应用日志尾部 → {available, status, lines, errors, warnings, tracebacks, files, ratio}。"""
    files = candidate_logs(log_dir, limit=max_files)
    total = errors = warnings = tracebacks = 0
    scanned: List[str] = []
    for path in files:
        lines = _tail_lines(path, tail_lines)
        if not lines:
            continue
        scanned.append(path.name)
        total += len(lines)
        for line in lines:
            if _ERROR_RE.search(line):
                errors += 1
            if _WARN_RE.search(line):
                warnings += 1
            if _TB_RE.search(line):
                tracebacks += 1
    if not scanned or total <= 0:
        return {
            "available": False, "status": "never", "lines": 0, "errors": 0,
            "warnings": 0, "tracebacks": 0, "files": [], "ratio": 0.0,
        }
    ratio = (errors + tracebacks) / float(total)
    bad = (errors + tracebacks) >= MIN_ERRORS and ratio >= MIN_ERROR_RATIO
    return {
        "available": True,
        "status": "warn" if bad else "ok",
        "lines": total,
        "errors": errors,
        "warnings": warnings,
        "tracebacks": tracebacks,
        "files": scanned,
        "ratio": round(ratio, 6),
    }


def unit_probe(log_dir: Optional[Path] = None) -> Dict[str, object]:
    """单位看板探针：日志尾窗的 ERROR 密度是否异常（只读）。"""
    data = scan(log_dir)
    if not data["available"]:
        return {
            "status": "never",
            "summary": "没有可读的日志文件（logs/*.log 不存在或不可读）",
            "metrics": {"lines": 0, "errors": 0, "warnings": 0},
            "issues": [],
            "last_activity": None,
        }
    files = data["files"] if isinstance(data["files"], list) else []
    summary = "尾窗 %d 行：ERROR %d / WARNING %d / Traceback %d（%.2f%%）" % (
        int(data["lines"]), int(data["errors"]), int(data["warnings"]),
        int(data["tracebacks"]), float(data["ratio"]) * 100.0)
    issues = []
    if data["status"] == "warn":
        issues.append("ERROR 密度异常：%d 条 / %d 行（阈值 ≥%d 条且 ≥%.0f%%）"
                      % (int(data["errors"]) + int(data["tracebacks"]), int(data["lines"]),
                         MIN_ERRORS, MIN_ERROR_RATIO * 100))
    return {
        "status": str(data["status"]),
        "summary": summary,
        "metrics": {
            "lines": int(data["lines"]),
            "errors": int(data["errors"]),
            "warnings": int(data["warnings"]),
            "tracebacks": int(data["tracebacks"]),
            "ratio": float(data["ratio"]),
            "files": len(files),
        },
        "issues": issues,
        "last_activity": None,
    }


__all__: List[str] = [
    "LOG_DIR_REL", "TAIL_LINES", "MIN_ERRORS", "MIN_ERROR_RATIO",
    "UNIT_ID", "UNIT_LABEL", "UNIT_FAMILY", "candidate_logs", "scan", "unit_probe",
]
