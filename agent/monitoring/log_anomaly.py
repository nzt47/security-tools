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

【v2（2026-11-12）：加时序基线】v1 只有静态阈值（抓"突然爆炸"）。v2 增加一条周期任务
（默认 30 分钟）把每次尾窗计数写成样本（data/monitoring/log_anomaly_baseline.json，
最多 96 个 ≈ 48h），探针读样本算"当前占比 vs 历史中位数 × 3"。**探针仍然只读**（不写基线）。
诚实边界仍在：基线是 48h 滚动中位数，极缓慢的长期恶化会被中位数自己吸收 ——
它抓的是"比最近两天明显更糟"，不是"和上个月比更糟"。

【不读什么】不读 *.jsonl（watchdog 事件流 / 审计流是结构化数据，不是"错"），
不读 .log 之外的扩展名；只取尾部 TAIL_LINES 行，避免把 40MB 日志整读进内存。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

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

#: 时序基线（2026-11-12 v2）：周期任务写样本，探针只读样本算"相对基线"的倍数。
#: 基线文件是运行期状态（data/monitoring/ 未入库）。
BASELINE_REL_PATH = "data/monitoring/log_anomaly_baseline.json"
#: 保留多少个样本（30 分钟一个 ⇒ 96 个 ≈ 48 小时）
MAX_SAMPLES = 96
#: 至少多少样本才敢用"相对基线"判据（不足就退回 v1 静态阈值，不猜）
MIN_BASELINE_SAMPLES = 8
#: 当前密度 ≥ 基线中位数 × 该倍数才算"相对异常"
BASELINE_MULTIPLE = 3.0
#: 相对判据的绝对下限（0.5%）：比 v1 的 5% 低一个数量级 ——
#: 目的正是让"平时 0.3% → 现在 1%（3 倍以上）"这种**没到绝对阈值**的恶化也能被看见；
#: 但需要同时满足 MIN_ERRORS（20 条），避免小样本误报。
RELATIVE_FLOOR_RATIO = 0.005
#: 采样周期任务（秒；默认 30 分钟）
DEFAULT_BASELINE_INTERVAL_SECONDS = 1800
ENV_BASELINE_INTERVAL_SECONDS = "CP_LOG_BASELINE_INTERVAL_SECONDS"
TASK_NAME = "日志异常基线采样"
#: 两次采样最小间隔（秒）：手工误点不会把基线刷偏
MIN_SAMPLE_GAP_SECONDS = 600

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


def _baseline_path(path: Optional[Path] = None) -> Path:
    return Path(path) if path is not None else _root() / BASELINE_REL_PATH


def load_baseline(path: Optional[Path] = None) -> Optional[Dict[str, object]]:
    """读基线（缺失/坏文件 ⇒ None；**只读，不创建**）。"""
    try:
        raw = json.loads(_baseline_path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LogAnomaly] 基线读取失败（%s）", type(exc).__name__)
        return None
    return raw if isinstance(raw, dict) else None


def update_baseline(*, baseline_path: Optional[Path] = None,
                    log_dir: Optional[Path] = None,
                    now: Optional[float] = None) -> Dict[str, object]:
    """采样一次并追加到基线（周期任务调用；**探针不调用它**）。

    样本形状：{"ts", "lines", "errors", "tracebacks", "warnings", "ratio"}，上限 MAX_SAMPLES。
    没有日志可读时**不写样本**（否则基线会被"没有数据"污染成 0）。
    """
    ts = float(now if now is not None else time.time())
    data = scan(log_dir)
    if not data["available"]:
        return {"status": "skipped", "reason": "no_logs"}
    doc = load_baseline(baseline_path) or {}
    samples = [s for s in (doc.get("samples") or []) if isinstance(s, dict)]
    if samples:
        try:
            last_ts = float(samples[-1].get("ts") or 0.0)
        except (TypeError, ValueError):
            last_ts = 0.0
        if last_ts and (ts - last_ts) < MIN_SAMPLE_GAP_SECONDS:
            return {"status": "skipped", "reason": "too_soon",
                    "samples": len(samples)}
    sample = {
        "ts": round(ts, 3),
        "lines": int(data["lines"]),
        "errors": int(data["errors"]),
        "tracebacks": int(data["tracebacks"]),
        "warnings": int(data["warnings"]),
        "ratio": float(data["ratio"]),
    }
    samples.append(sample)
    samples = samples[-MAX_SAMPLES:]
    target = _baseline_path(baseline_path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "samples": samples},
                                  ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(target))
    except Exception as exc:  # noqa: BLE001 基线写失败不影响探针（退回 v1 静态判据）
        logger.warning("[LogAnomaly] 基线写入失败（%s）", type(exc).__name__)
        return {"status": "error", "reason": "write_failed", "samples": len(samples)}
    return {"status": "written", "samples": len(samples), "sample": sample}


def _baseline_ratio_median(baseline: Optional[Dict[str, object]]) -> Optional[float]:
    """基线里历史样本的 ratio 中位数（样本不足 ⇒ None，不猜）。"""
    if not baseline:
        return None
    samples = baseline.get("samples")
    if not isinstance(samples, list):
        return None
    ratios: List[float] = []
    for item in samples:
        if not isinstance(item, dict):
            continue
        try:
            ratios.append(float(item.get("ratio") or 0.0))
        except (TypeError, ValueError):
            continue
    if len(ratios) < MIN_BASELINE_SAMPLES:
        return None
    ratios.sort()
    mid = len(ratios) // 2
    return ratios[mid] if len(ratios) % 2 else (ratios[mid - 1] + ratios[mid]) / 2.0


def unit_probe(log_dir: Optional[Path] = None,
               baseline_path: Optional[Path] = None) -> Dict[str, object]:
    """单位看板探针：日志尾窗的 ERROR 密度是否异常（只读：不写基线、不轮转）。

    两条判据（任一命中即 warn）：
    - **绝对**：ERROR+Traceback ≥ MIN_ERRORS 且占比 ≥ MIN_ERROR_RATIO（v1，抓突发爆炸）；
    - **相对**：基线样本 ≥ MIN_BASELINE_SAMPLES 时，当前占比 ≥ max(RELATIVE_FLOOR_RATIO(0.5%),
      基线中位数 × BASELINE_MULTIPLE) 且 ERROR+Traceback ≥ MIN_ERRORS（v2，抓"比平时多得多"）。
    相对判据只在样本足够时启用：样本不足就退回 v1，**不拿一个空基线当 0 判**。
    """
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
    bad = int(data["errors"]) + int(data["tracebacks"])
    ratio = float(data["ratio"])
    baseline = load_baseline(baseline_path)
    baseline_ratio = _baseline_ratio_median(baseline)
    relative_hit = (baseline_ratio is not None
                    and bad >= MIN_ERRORS
                    and ratio >= max(RELATIVE_FLOOR_RATIO,
                                     baseline_ratio * BASELINE_MULTIPLE))
    absolute_hit = data["status"] == "warn"

    summary = "尾窗 %d 行：ERROR %d / WARNING %d / Traceback %d（%.2f%%）" % (
        int(data["lines"]), int(data["errors"]), int(data["warnings"]),
        int(data["tracebacks"]), ratio * 100.0)
    if baseline_ratio is not None:
        summary += " · 基线 %.2f%%（%d 样本）" % (baseline_ratio * 100.0,
                                              len((baseline or {}).get("samples") or []))

    issues = []
    if absolute_hit:
        issues.append("ERROR 密度异常：%d 条 / %d 行（绝对阈值 ≥%d 条且 ≥%.0f%%）"
                      % (bad, int(data["lines"]), MIN_ERRORS, MIN_ERROR_RATIO * 100))
    if relative_hit:
        issues.append("ERROR 密度相对基线上升：当前 %.2f%% ≥ 基线 %.2f%% × %.1f"
                      % (ratio * 100.0, baseline_ratio * 100.0, BASELINE_MULTIPLE))

    status = "warn" if (absolute_hit or relative_hit) else str(data["status"])
    return {
        "status": status,
        "summary": summary,
        "metrics": {
            "lines": int(data["lines"]),
            "errors": int(data["errors"]),
            "warnings": int(data["warnings"]),
            "tracebacks": int(data["tracebacks"]),
            "ratio": ratio,
            "files": len(files),
            "baseline_ratio": None if baseline_ratio is None else round(baseline_ratio, 6),
            "baseline_samples": len((baseline or {}).get("samples") or []),
        },
        "issues": issues,
        "last_activity": None,
    }


# ════════════════════════════════════════════════════════════
#  基线采样周期任务（写基线；探针永远只读）
# ════════════════════════════════════════════════════════════

def baseline_interval_seconds() -> int:
    """采样间隔（秒；默认 1800，最小 300）。"""
    raw = (os.environ.get(ENV_BASELINE_INTERVAL_SECONDS) or "").strip()
    if not raw:
        return DEFAULT_BASELINE_INTERVAL_SECONDS
    try:
        return max(300, int(raw))
    except ValueError:
        logger.warning("[LogAnomaly] 非法 %s=%r，使用默认 %s",
                       ENV_BASELINE_INTERVAL_SECONDS, raw, DEFAULT_BASELINE_INTERVAL_SECONDS)
        return DEFAULT_BASELINE_INTERVAL_SECONDS


def _remove_same_name(scheduler: Any) -> bool:
    for task in list(getattr(scheduler, "tasks", []) or []):
        if task.get("name") == TASK_NAME:
            scheduler.remove_task(task["task_id"])
            return True
    return False


def register_baseline_scheduler(scheduler: Optional[Any] = None) -> Dict[str, object]:
    """注册"日志异常基线采样"间隔任务（幂等）。"""
    try:
        if scheduler is None:
            from agent.task_scheduler import get_scheduler
            scheduler = get_scheduler()
    except Exception as exc:  # noqa: BLE001
        logger.error("[LogAnomaly] 调度器不可用（%s）", type(exc).__name__)
        return {"status": "error", "reason": "scheduler_unavailable"}
    _remove_same_name(scheduler)
    interval = baseline_interval_seconds()
    scheduler.add_interval_task(
        TASK_NAME, func=update_baseline, interval_seconds=interval,
        zero_token=True, owner="agent/monitoring/log_anomaly.py",
        zero_token_note="只读日志尾部计数并落基线样本，不调 LLM")
    task_id = scheduler.tasks[-1]["task_id"] if getattr(scheduler, "tasks", None) else ""
    logger.info("[LogAnomaly] 基线采样已注册 interval=%ds task_id=%s", interval, task_id)
    return {"status": "scheduled", "task_id": task_id, "interval_seconds": interval}


def unschedule_baseline_scheduler(scheduler: Optional[Any] = None) -> bool:
    """注销基线采样任务（按固定任务名定位）。"""
    try:
        if scheduler is None:
            from agent.task_scheduler import get_scheduler
            scheduler = get_scheduler()
    except Exception:  # noqa: BLE001
        return False
    return _remove_same_name(scheduler)


__all__: List[str] = [
    "LOG_DIR_REL", "TAIL_LINES", "MIN_ERRORS", "MIN_ERROR_RATIO",
    "BASELINE_REL_PATH", "MAX_SAMPLES", "MIN_BASELINE_SAMPLES", "BASELINE_MULTIPLE",
    "RELATIVE_FLOOR_RATIO",
    "TASK_NAME", "ENV_BASELINE_INTERVAL_SECONDS",
    "UNIT_ID", "UNIT_LABEL", "UNIT_FAMILY", "candidate_logs", "scan",
    "load_baseline", "update_baseline", "baseline_interval_seconds",
    "register_baseline_scheduler", "unschedule_baseline_scheduler", "unit_probe",
]
