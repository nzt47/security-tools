"""§6.7 SLO 指标周报的定时生成与存档（默认关闭）。

范式对齐 `skills_mgmt/cleanup_scheduler.py` / `learning_scheduler.py`：

- **开关优先级**：`CP_SLO_SCHEDULE_ENABLED` > `config.yaml slo_report.enabled` > 默认 `false`
- **注册方式**：注册为 `TaskScheduler` 的 cron 任务（默认**周一 09:00**，可配 day/hour/minute）
- **每次运行**：
    1) 调用 `scripts/report_slo_weekly.py` 生成 Markdown + JSON
    2) 存档到 `docs/zh/周报存档/slo_weekly_<YYYYMMDD>.md|.json`
    3) 追加审计 `data/slo_report_audit.jsonl`
- **安全底线**：默认关闭；失败只记录不抛（不阻断主流程）；注册失败同样不阻断

为什么挂调度而不是手动跑：运营期需要**连续可比**的周报序列；手动跑容易漏周，
而周报的价值在于"与 W0 基线和上周对比"。挂进云枢自身的调度器，
也让"周报生成"成为云枢真实运行的一部分（运营期口径）。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────
TASK_NAME = "SLO 指标周报"
_ENV_PREFIX = "CP_SLO_SCHEDULE"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_REPORT_SCRIPT = _REPO_ROOT / "scripts" / "report_slo_weekly.py"
_DEFAULT_OUT_DIR = _REPO_ROOT / "docs" / "zh" / "周报存档"
_DEFAULT_AUDIT_FILE = _REPO_ROOT / "data" / "slo_report_audit.jsonl"
_DEFAULT_DAY_OF_WEEK = 0      # 周一（Python weekday(): 周一=0）
_DEFAULT_HOUR = 9
_DEFAULT_MINUTE = 0
_DEFAULT_TIMEOUT_SEC = 600

_TRUE = ("true", "1", "yes", "on")


# ── 配置读取 ──────────────────────────────────────────────
def _config_yaml() -> Optional[Dict[str, Any]]:
    """读取仓库根 config.yaml（失败返回 None，不抛异常）。"""
    cfg_path = _REPO_ROOT / "config.yaml"
    if not cfg_path.exists():
        return None
    try:
        import yaml as _yaml

        with open(cfg_path, "r", encoding="utf-8") as f:
            return _yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001 配置解析失败回退默认
        logger.debug("[SloReportScheduler] config.yaml 读取失败: %s", e)
        return None


def _cfg_section() -> Dict[str, Any]:
    cfg = _config_yaml() or {}
    section = cfg.get("slo_report") or {}
    return section if isinstance(section, dict) else {}


def _flag(env_key: str, cfg_key: str, default: bool) -> bool:
    raw = os.environ.get(f"{_ENV_PREFIX}_{env_key}")
    if raw is not None and str(raw).strip():
        return str(raw).strip().lower() in _TRUE
    val = _cfg_section().get(cfg_key)
    if val is not None:
        return str(val).strip().lower() in _TRUE
    return default


def _int_opt(env_key: str, cfg_key: str, default: int,
             lo: int, hi: int) -> int:
    """读取整数配置；非法值回退默认（并 warn），越界则夹紧。"""
    raw = os.environ.get(f"{_ENV_PREFIX}_{env_key}")
    if raw is None or not str(raw).strip():
        raw = _cfg_section().get(cfg_key)
    if raw is None or not str(raw).strip():
        return default
    try:
        val = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("[SloReportScheduler] 非法 %s=%r，使用默认 %d",
                       env_key, raw, default)
        return default
    if val < lo or val > hi:
        logger.warning("[SloReportScheduler] %s=%d 越界 [%d,%d]，夹紧",
                       env_key, val, lo, hi)
        return max(lo, min(hi, val))
    return val


def _enabled() -> bool:
    return _flag("ENABLED", "enabled", False)


def _days() -> int:
    return _int_opt("DAYS", "days", 7, 1, 90)


def _schedule() -> Dict[str, int]:
    return {
        "day_of_week": _int_opt("DAY_OF_WEEK", "day_of_week",
                                _DEFAULT_DAY_OF_WEEK, 0, 6),
        "hour": _int_opt("HOUR", "hour", _DEFAULT_HOUR, 0, 23),
        "minute": _int_opt("MINUTE", "minute", _DEFAULT_MINUTE, 0, 59),
    }


def _out_dir() -> Path:
    raw = os.environ.get(f"{_ENV_PREFIX}_OUT_DIR") or _cfg_section().get("out_dir")
    return Path(str(raw)).expanduser() if raw else _DEFAULT_OUT_DIR


def _audit_file() -> Path:
    raw = os.environ.get(f"{_ENV_PREFIX}_AUDIT_FILE") or _cfg_section().get("audit_file")
    return Path(str(raw)).expanduser() if raw else _DEFAULT_AUDIT_FILE


# ── 执行 ──────────────────────────────────────────────────
def _default_runner(cmd: list, cwd: Path, timeout: int) -> Dict[str, Any]:
    """默认执行器：真实调用周报脚本（UTF-8 输出，超时保护）。"""
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    proc = subprocess.run(  # noqa: S603 固定脚本路径 + 参数列表，无 shell
        cmd, cwd=str(cwd), capture_output=True, text=True,
        timeout=timeout, env=env,
    )
    return {
        "returncode": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-800:],
        "stderr_tail": (proc.stderr or "")[-800:],
    }


def _append_audit(record: Dict[str, Any]) -> None:
    try:
        path = _audit_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 审计失败不影响主流程
        logger.warning("[SloReportScheduler] 审计写入失败: %s", e)


def run_once(*, days: Optional[int] = None, out_dir: Optional[Path] = None,
             runner: Optional[Callable[[list, Path, int], Dict[str, Any]]] = None,
             now: Optional[datetime] = None) -> Dict[str, Any]:
    """生成一份周报并存档（供调度调用，也可手动/测试调用）。

    Args:
        days: 窗口天数（缺省取配置，默认 7）
        out_dir: 存档目录（缺省 `docs/zh/周报存档`）
        runner: 执行器（缺省真实调用脚本；测试可注入桩）
        now: 时间注入（测试用）

    Returns:
        {ok, archived: {md, json}, exec: {...}, note}
    """
    ts = (now or datetime.now())
    stamp = ts.strftime("%Y%m%d")
    target_dir = Path(out_dir) if out_dir else _out_dir()
    window_days = int(days) if days else _days()

    md_path = target_dir / f"slo_weekly_{stamp}.md"
    json_path = target_dir / f"slo_weekly_{stamp}.json"

    result: Dict[str, Any] = {"ok": False, "archived": {}, "exec": {}}
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        if not _REPORT_SCRIPT.exists():
            result["note"] = f"周报脚本不存在: {_REPORT_SCRIPT}"
            _append_audit({"ts": ts.isoformat(), "ok": False,
                           "note": result["note"]})
            return result

        cmd = [sys.executable, str(_REPORT_SCRIPT),
               "--days", str(window_days),
               "--md", str(md_path),
               "--out", str(json_path)]
        exec_info = (runner or _default_runner)(cmd, _REPO_ROOT,
                                               _DEFAULT_TIMEOUT_SEC)
        result["exec"] = exec_info
        result["ok"] = int(exec_info.get("returncode", 1)) == 0
        result["archived"] = {
            "md": str(md_path) if md_path.exists() else "",
            "json": str(json_path) if json_path.exists() else "",
        }
        _append_audit({
            "ts": ts.isoformat(), "ok": result["ok"],
            "days": window_days, "returncode": exec_info.get("returncode"),
            "md": result["archived"]["md"], "json": result["archived"]["json"],
            "stderr_tail": exec_info.get("stderr_tail", "")[:300],
        })
        logger.info("[SloReportScheduler] 周报生成 %s (ok=%s)",
                    stamp, result["ok"])
    except Exception as e:  # noqa: BLE001 不阻断主流程
        result["note"] = f"周报生成异常: {e}"
        logger.warning("[SloReportScheduler] %s", result["note"])
        _append_audit({"ts": ts.isoformat(), "ok": False, "note": result["note"]})
    return result


def _job() -> None:
    """调度任务入口（TaskScheduler 调用；失败不抛）。"""
    run_once()


# ── 注册 ──────────────────────────────────────────────────
def register_slo_report_scheduler(scheduler: Any = None, *,
                                  day_of_week: Optional[int] = None,
                                  hour: Optional[int] = None,
                                  minute: Optional[int] = None) -> Dict[str, Any]:
    """把周报注册为 TaskScheduler 的 cron 任务（默认关闭，需显式开启）。

    Args:
        scheduler: `agent.task_scheduler.TaskScheduler` 实例（app_server 内已有）；
                   None 时返回 registered=False（不自行创建调度器）
    Returns:
        {ok, registered, reason?, task_id?, schedule?}
    """
    if not _enabled():
        logger.info("[SloReportScheduler] 未启用（CP_SLO_SCHEDULE_ENABLED / "
                    "config.yaml slo_report.enabled）")
        return {"ok": True, "registered": False, "reason": "disabled"}
    if scheduler is None:
        return {"ok": False, "registered": False,
                "reason": "no_scheduler（需传入 TaskScheduler 实例）"}

    sched = _schedule()
    if day_of_week is not None:
        sched["day_of_week"] = int(day_of_week)
    if hour is not None:
        sched["hour"] = int(hour)
    if minute is not None:
        sched["minute"] = int(minute)

    try:
        scheduler.add_cron_task(
            name=TASK_NAME, func=_job,
            day_of_week=sched["day_of_week"],
            hour=sched["hour"], minute=sched["minute"],
        )
        task_id = ""
        for t in reversed(getattr(scheduler, "tasks", []) or []):
            if t.get("name") == TASK_NAME:
                task_id = str(t.get("task_id", ""))
                break
        logger.info("[SloReportScheduler] 已注册: task_id=%s schedule=%s",
                    task_id, sched)
        return {"ok": True, "registered": True, "task_id": task_id,
                "schedule": sched, "out_dir": str(_out_dir())}
    except Exception as e:  # noqa: BLE001 注册失败不阻断
        logger.warning("[SloReportScheduler] 注册失败: %s", e)
        return {"ok": False, "registered": False, "reason": f"error: {e}"}
