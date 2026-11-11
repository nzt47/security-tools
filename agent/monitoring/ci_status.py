"""CI 结果同步（gh → data/ci/status.json）与周期任务（2026-11-12）。

【为什么要有周期任务（§11.94 C 节）】
报警器的 CI 事件源读 `data/ci/status.json`，文件超过 `CI_MAX_AGE_SEC`（2h）未同步
即按"数据源不可用"处理 —— 实测 2026-11-11 18:14 之后就没人再同步，7 小时未更新，
于是 CI 覆盖**实际离线**（`last_collect.checked.ci=false`）。同步脚本本身早就有
（`scripts/sync_ci_status.py`），缺的是"有人按周期去跑它"。

【为什么同步放在本模块、而不是报警采集线程里】
采集线程每 60s 一轮，跑 `gh`（网络 + 子进程）会把它变成新的不稳定源；故仍保持
"取数（gh，周期任务）"与"判读（读文件，采集线程）"分离。本模块是取数侧：
`sync_once()` 起一次 `gh run list`、写状态文件；`scripts/sync_ci_status.py` 只做 CLI 包装。

【失败口径】
- gh 缺失 / 未登录 / 超时 / 输出非 JSON ⇒ `status="unavailable"` + 原因，**不抛异常**：
  调度器 python_func 只看异常判失败，抛出去会让报警器每 30 分钟报一条"计划任务失败"
  （CI 本身不可用 ≠ 同步任务坏，真相由覆盖读数的"最后同步时间"如实披露）。
- 写文件失败同样只记日志。

环境变量（已进 agent/settings/registry.py）：
    CP_CI_SYNC_ENABLED             总开关（默认 true）
    CP_CI_SYNC_INTERVAL_SECONDS    同步间隔（默认 1800s = 30 分钟；阈值 2h 的 1/4）
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

#: 调度任务名（UI/日志可读；也用于按名注销）
TASK_NAME = "CI 状态同步"
#: 同步间隔（秒）：2h 离线阈值 ÷ 4，容许连续三次失败仍未判离线
DEFAULT_INTERVAL_SECONDS = 1800
#: 报警器判"不可用"的年龄上限（与 agent/alarms/collector.py 的 CI_MAX_AGE_SEC 同口径）
STALE_MAX_AGE_SECONDS = 7200
#: gh 单次调用超时（秒）
GH_TIMEOUT_SEC = 60
#: 取最近多少次运行（报警器只报每个工作流的最新一次，10 条足够覆盖 4 个工作流）
DEFAULT_LIMIT = 10
#: gh 的 JSON 字段（少取字段 = 少泄露面；url 是浏览器可点的 URL，不含令牌）
GH_FIELDS = "databaseId,name,displayTitle,workflowName,status,conclusion,headBranch,createdAt,url"

ENV_ENABLED = "CP_CI_SYNC_ENABLED"
ENV_INTERVAL_SECONDS = "CP_CI_SYNC_INTERVAL_SECONDS"

#: 状态文件（运行期状态；.gitignore 已忽略 data/ci/）
OUT_REL_PATH = "data/ci/status.json"

_TRUTHY = frozenset({"1", "true", "yes", "on", "enable", "enabled"})
_FALSY = frozenset({"0", "false", "no", "off", "disable", "disabled"})

#: runner 契约：接收 argv，返回 (returncode, stdout, stderr)
Runner = Callable[[List[str]], Tuple[int, str, str]]


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    return default


def enabled() -> bool:
    return _env_bool(ENV_ENABLED, True)


def interval_seconds() -> int:
    """同步间隔（最小 300s：再密就是拿 gh 打 GitHub 配额）。"""
    raw = (os.environ.get(ENV_INTERVAL_SECONDS) or "").strip()
    if not raw:
        return DEFAULT_INTERVAL_SECONDS
    try:
        return max(300, int(raw))
    except ValueError:
        logger.warning("[CISync] 非法 %s=%r，使用默认 %s",
                       ENV_INTERVAL_SECONDS, raw, DEFAULT_INTERVAL_SECONDS)
        return DEFAULT_INTERVAL_SECONDS


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _out_path(out_path: Optional[Path] = None) -> Path:
    return Path(out_path) if out_path is not None else _repo_root() / OUT_REL_PATH


def _subprocess_runner(argv: List[str]) -> Tuple[int, str, str]:
    """默认 runner：真实起子进程（cwd=仓库根；**必须显式 utf-8**）。

    【必须显式 utf-8】Windows 默认按本地代码页（gbk）解码子进程输出，gh 的中文
    （工作流名/标题）会直接抛 UnicodeDecodeError（2026-11-11 实测踩到）。
    """
    proc = subprocess.run(argv, cwd=str(_repo_root()), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=GH_TIMEOUT_SEC)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _run_gh(limit: int, runner: Optional[Runner] = None) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """跑 `gh run list`；失败返回 (None, 可操作原因)。"""
    run = runner or _subprocess_runner
    argv = ["gh", "run", "list", "--limit", str(limit), "--json", GH_FIELDS]
    try:
        code, out, err = run(argv)
    except FileNotFoundError:
        return None, "找不到 gh 命令（本机没装 GitHub CLI 或不在 PATH）"
    except subprocess.TimeoutExpired:
        return None, "gh 超时（%ds）" % GH_TIMEOUT_SEC
    except Exception as exc:  # noqa: BLE001 子进程异常按不可用处理
        logger.warning("[CISync] gh 调用异常（%s）", type(exc).__name__)
        return None, "gh 调用异常（%s）" % type(exc).__name__
    if code != 0:
        tail = (err or "").strip().splitlines()[-1:] or [""]
        return None, "gh 失败（exit=%s）：%s" % (code, tail[0][:200])
    try:
        data = json.loads(out or "[]")
    except json.JSONDecodeError:
        return None, "gh 输出不是合法 JSON"
    return (data if isinstance(data, list) else None), ""


def _repo_slug(runner: Optional[Runner] = None) -> str:
    """当前仓库 slug（失败留空，不影响同步）。"""
    run = runner or _subprocess_runner
    try:
        code, out, _err = run(["gh", "repo", "view", "--json", "nameWithOwner"])
        if code == 0:
            return str((json.loads(out or "{}") or {}).get("nameWithOwner") or "")
    except Exception:  # noqa: BLE001 取不到仓库名不算失败
        pass
    return ""


def build_payload(runs: List[Dict[str, Any]], *, repo: str = "") -> Dict[str, Any]:
    """gh 运行列表 → 报警器读的状态文件（字段名与 collector.ci_candidates 对齐）。"""
    rows: List[Dict[str, Any]] = []
    for run in runs or []:
        if not isinstance(run, dict):
            continue
        rows.append({
            "workflow": str(run.get("workflowName") or run.get("name") or ""),
            "name": str(run.get("name") or ""),
            "title": str(run.get("displayTitle") or ""),
            "status": str(run.get("status") or ""),
            "conclusion": str(run.get("conclusion") or ""),
            "branch": str(run.get("headBranch") or ""),
            "created_at": str(run.get("createdAt") or ""),
            "database_id": run.get("databaseId"),
            "url": str(run.get("url") or ""),
        })
    return {
        "synced_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "gh run list",
        "repo": repo,
        "runs": rows,
    }


def sync_once(*, limit: int = DEFAULT_LIMIT, out_path: Optional[Path] = None,
              runner: Optional[Runner] = None,
              repo: Optional[str] = None) -> Dict[str, Any]:
    """同步一次 gh 运行结果到状态文件（**不抛异常**；失败如实返回 unavailable）。

    Returns:
        {"status": "written"|"unavailable", "synced_at":..., "runs": n,
         "path": str, "reason": str}
    """
    runs, reason = _run_gh(max(1, int(limit)), runner=runner)
    if runs is None:
        logger.warning("[CISync] 本轮同步未完成：%s", reason)
        return {"status": "unavailable", "reason": reason, "path": str(_out_path(out_path))}
    payload = build_payload(runs, repo=repo if repo is not None else _repo_slug(runner))
    path = _out_path(out_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 写盘失败只记日志
        logger.warning("[CISync] 状态写入失败（%s）", type(exc).__name__)
        return {"status": "unavailable", "reason": "状态写入失败（%s）" % type(exc).__name__,
                "path": str(path)}
    logger.info("[CISync] 已同步 %d 次运行 → %s", len(payload["runs"]), path)
    return {"status": "written", "synced_at": payload["synced_at"],
            "runs": len(payload["runs"]), "path": str(path)}


def read_status(out_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """读状态文件（缺失/坏文件 ⇒ None）。"""
    try:
        raw = json.loads(_out_path(out_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[CISync] 状态读取失败（%s）", type(exc).__name__)
        return None
    return raw if isinstance(raw, dict) else None


def _age_seconds(iso_text: Optional[str], now: float) -> Optional[float]:
    """ISO 时间 → 距今秒数（解析失败/空 ⇒ None）。"""
    if not iso_text:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_text).replace("Z", "+00:00"))
        return max(0.0, now - dt.timestamp())
    except (TypeError, ValueError):
        return None


def gap_summary(*, out_path: Optional[Path] = None,
                now: Optional[float] = None) -> Dict[str, Any]:
    """给 `/api/alarms/coverage` 的"最后同步时间"读数（只读，不联网）。

    【为什么要它】"过期 = 静默"是本轮要消灭的两个缺口之一：CI 源离线时，
    `last_collect.checked.ci=false` 只说"这一轮没读到"，不说"上次同步是几点"。
    """
    ts = float(now if now is not None else time.time())
    payload = read_status(out_path)
    if payload is None:
        return {
            "available": False,
            "last_sync_at": None,
            "age_seconds": None,
            "stale": True,
            "max_age_seconds": STALE_MAX_AGE_SECONDS,
            "runs": 0,
            "detail": "从未同步过（data/ci/status.json 不存在）⇒ CI 事件源不可用",
        }
    synced_at = payload.get("synced_at")
    age = _age_seconds(synced_at, ts)
    runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
    stale = age is None or age > STALE_MAX_AGE_SECONDS
    if age is None:
        detail = "状态文件里没有可解析的 synced_at ⇒ 视为过期"
    elif stale:
        detail = "最后同步 %.1f 小时前 > 阈值 %.0f 小时 ⇒ CI 事件源按不可用处理" % (
            age / 3600.0, STALE_MAX_AGE_SECONDS / 3600.0)
    else:
        detail = "最后同步 %.0f 分钟前（阈值 %.0f 小时）" % (
            age / 60.0, STALE_MAX_AGE_SECONDS / 3600.0)
    return {
        "available": True,
        "last_sync_at": synced_at,
        "age_seconds": age,
        "stale": stale,
        "max_age_seconds": STALE_MAX_AGE_SECONDS,
        "runs": len(runs),
        "detail": detail,
    }


# ════════════════════════════════════════════════════════════
#  调度注册
# ════════════════════════════════════════════════════════════

def _remove_same_name(scheduler: Any) -> bool:
    for task in list(getattr(scheduler, "tasks", []) or []):
        if task.get("name") == TASK_NAME:
            scheduler.remove_task(task["task_id"])
            return True
    return False


def register_ci_sync_scheduler(scheduler: Optional[Any] = None) -> Dict[str, Any]:
    """注册"CI 状态同步"间隔任务（幂等）。"""
    if not enabled():
        logger.info("[CISync] 周期同步已关闭（%s=false）", ENV_ENABLED)
        return {"status": "disabled", "reason": "%s=false" % ENV_ENABLED}
    try:
        if scheduler is None:
            from agent.task_scheduler import get_scheduler
            scheduler = get_scheduler()
    except Exception as exc:  # noqa: BLE001
        logger.error("[CISync] 调度器不可用（%s）", type(exc).__name__)
        return {"status": "error", "reason": "scheduler_unavailable"}
    _remove_same_name(scheduler)
    interval = interval_seconds()
    scheduler.add_interval_task(
        TASK_NAME, func=sync_once, interval_seconds=interval,
        zero_token=True, owner="agent/monitoring/ci_status.py",
        zero_token_note="调 gh 查运行结论并落文件，不调 LLM")
    task_id = scheduler.tasks[-1]["task_id"] if getattr(scheduler, "tasks", None) else ""
    logger.info("[CISync] 周期同步已注册 interval=%ds task_id=%s", interval, task_id)
    return {"status": "scheduled", "task_id": task_id, "interval_seconds": interval}


def unschedule_ci_sync_scheduler(scheduler: Optional[Any] = None) -> bool:
    """注销周期同步任务（按固定任务名定位）。"""
    try:
        if scheduler is None:
            from agent.task_scheduler import get_scheduler
            scheduler = get_scheduler()
    except Exception as exc:  # noqa: BLE001
        logger.error("[CISync] 调度注销失败（%s）", type(exc).__name__)
        return False
    return _remove_same_name(scheduler)


__all__: List[str] = [
    "TASK_NAME", "ENV_ENABLED", "ENV_INTERVAL_SECONDS", "STALE_MAX_AGE_SECONDS",
    "enabled", "interval_seconds", "build_payload", "sync_once", "read_status",
    "gap_summary", "register_ci_sync_scheduler", "unschedule_ci_sync_scheduler",
]
