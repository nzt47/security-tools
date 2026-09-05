"""技能清理自动调度器 — 定期自动清除无用技能与孤儿残留。

技能中心"自动清除无用技能"的后台定时能力：
    1. 孤儿清理：legacy/文件轨/分类中主轨已无的脏数据（默认 dry-run）
    2. 无用淘汰：归档超期零使用 / 停用零使用超期的技能（默认 dry-run）

安全边界（默认保守，与 lifecycle 一致）：
    - 默认 dry_run=true：只报告不删除，人工在技能中心一键确认后再执行
    - 配置（env > config.yaml > 硬编码）：
        SKILL_CLEANUP_ENABLED            / skills_mgmt.cleanup.enabled        (false)
        SKILL_CLEANUP_INTERVAL_HOURS     / skills_mgmt.cleanup.interval_hours (24)
        SKILL_CLEANUP_ORPHANS_DRY_RUN    / skills_mgmt.cleanup.orphans_dry_run (true)
        SKILL_CLEANUP_UNUSED_DRY_RUN     / skills_mgmt.cleanup.unused_dry_run (true)
        SKILL_CLEANUP_UNUSED_DAYS        / skills_mgmt.cleanup.unused_days     (90)
        SKILL_CLEANUP_ARCHIVED_DAYS      / skills_mgmt.cleanup.archived_days   (180)

用法（与 learning_scheduler 同模式）：
    from agent.skills_mgmt.cleanup_scheduler import register_cleanup_schedulers
    register_cleanup_schedulers()
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

_ENV_PREFIX = "SKILL_CLEANUP"


# ═══════════════════════════════════════════════════════════════
#  配置
# ═══════════════════════════════════════════════════════════════

def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(f"{_ENV_PREFIX}_{name}")
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(f"{_ENV_PREFIX}_{name}")
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _cfg() -> Dict[str, Any]:
    """读取配置（env 优先，config.yaml 次之，硬编码兜底）。"""
    cfg: Dict[str, Any] = {}
    try:
        from agent.config import Config  # noqa: F401  (兼容主配置入口)
    except Exception:  # noqa: BLE001
        pass
    # config.yaml skills_mgmt.cleanup
    try:
        import yaml
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "config.yaml")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            cleanup = (data.get("skills_mgmt", {}) or {}).get("cleanup", {}) or {}
            cfg = cleanup
    except Exception:  # noqa: BLE001
        pass

    def _get(key: str, default):
        if key in cfg:
            return cfg[key]
        return default

    return {
        "enabled": _env_bool("ENABLED", bool(_get("enabled", False))),
        "interval_hours": _env_int(
            "INTERVAL_HOURS", int(_get("interval_hours", 24))),
        "orphans_dry_run": _env_bool(
            "ORPHANS_DRY_RUN", bool(_get("orphans_dry_run", True))),
        "unused_dry_run": _env_bool(
            "UNUSED_DRY_RUN", bool(_get("unused_dry_run", True))),
        "unused_days": _env_int(
            "UNUSED_DAYS", int(_get("unused_days", 90))),
        "archived_days": _env_int(
            "ARCHIVED_DAYS", int(_get("archived_days", 180))),
    }


# ═══════════════════════════════════════════════════════════════
#  执行
# ═══════════════════════════════════════════════════════════════

def run_cleanup_once() -> Dict[str, Any]:
    """执行一次清理（按配置 dry_run）。返回报告。"""
    cfg = _cfg()
    from agent.state_manager import get_skills_mgmt_service
    svc = get_skills_mgmt_service()

    orphans_result = svc.cleanup_orphans(dry_run=cfg["orphans_dry_run"])
    unused_result = svc.cleanup_unused(
        dry_run=cfg["unused_dry_run"],
        unused_days=cfg["unused_days"],
        archived_days=cfg["archived_days"],
    )
    result = {
        "ok": True,
        "cfg": cfg,
        "orphans": orphans_result,
        "unused": unused_result,
    }
    logger.info(log_dict({
        'module_name': 'cleanup_scheduler',
        'action': 'cleanup_scheduler.run',
        'orphans_found': orphans_result.get("found", 0),
        'orphans_cleaned': len(orphans_result.get("cleaned", [])),
        'unused_found': unused_result.get("found", 0),
        'unused_removed': len(unused_result.get("removed", [])),
        'dry_run': cfg["orphans_dry_run"] or cfg["unused_dry_run"],
    }))
    return result


# ═══════════════════════════════════════════════════════════════
#  调度注册（与 learning_scheduler 同模式）
# ═══════════════════════════════════════════════════════════════

_TASK_NAME = "技能清理"
_REGISTERED_ID: Optional[str] = None


def register_cleanup_schedulers() -> Dict[str, Any]:
    """注册周期清理任务（默认关闭，需 enabled=true）。"""
    global _REGISTERED_ID
    cfg = _cfg()
    if not cfg["enabled"]:
        logger.info("[CleanupScheduler] 技能清理调度未启用"
                    "(SKILL_CLEANUP_ENABLED / skills_mgmt.cleanup.enabled)")
        return {"ok": True, "registered": False,
                "reason": "disabled", "cfg": cfg}

    try:
        from agent.scheduling import get_schedule_scheduler
        sched = get_schedule_scheduler()
        task = sched.add_task(
            name=_TASK_NAME,
            action="run_cleanup_once",
            params={},
            interval_minutes=int(cfg["interval_hours"]) * 60,
            enabled=True,
        )
        _REGISTERED_ID = str(task.get("id", ""))
        logger.info(log_dict({
            'module_name': 'cleanup_scheduler',
            'action': 'cleanup_scheduler.register',
            'task_id': _REGISTERED_ID,
            'interval_hours': cfg["interval_hours"],
        }))
        return {"ok": True, "registered": True,
                "task_id": _REGISTERED_ID, "cfg": cfg}
    except Exception as e:  # noqa: BLE001
        logger.warning("[CleanupScheduler] 注册失败: %s", e)
        return {"ok": False, "registered": False, "error": str(e)}


def unregister_cleanup_schedulers() -> Dict[str, bool]:
    """注销清理任务（测试/重载用）。"""
    global _REGISTERED_ID
    if not _REGISTERED_ID:
        return {"ok": True, "unregistered": False}
    try:
        from agent.scheduling import get_schedule_scheduler
        get_schedule_scheduler().remove_task(_REGISTERED_ID)
        _REGISTERED_ID = None
        return {"ok": True, "unregistered": True}
    except Exception as e:  # noqa: BLE001
        logger.warning("[CleanupScheduler] 注销失败: %s", e)
        return {"ok": False, "unregistered": False, "error": str(e)}


def main() -> None:
    """CLI 手动执行一次清理。"""
    result = run_cleanup_once()
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
