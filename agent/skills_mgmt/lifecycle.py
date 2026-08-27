"""Skill 生命周期自动淘汰判定器（TASK-05 Step 3）

背景（Why）:
    Skill 生命周期状态机齐全（DRAFT→…→PUBLISHED→DEPRECATED→ARCHIVED），
    但此前无"长期零使用自动淘汰"判定器。本模块实现：
        PUBLISHED 闲置 > unused_days（默认 90）  → DEPRECATED
        DEPRECATED 闲置 > archive_days（默认 180） → ARCHIVED
        技能总数 > upgrade_threshold（默认 30）    → 检索升级建议（仅报告，不改配置）
    last_used_at 缺失时以 usage_count==0 且创建时间距今超阈值判定；
    状态迁移只改 models.py 状态机，绝不物理删除文件（物理删除仅人工允许）。

【不易】约束（禁止触碰）:
    - 不修改 models.py 状态机转换表（迁移只赋合法状态值）
    - 淘汰判定绝不删除文件：DEPRECATED/ARCHIVED 仅是状态迁移
    - 默认 dry_run=true：不产生任何状态迁移 / 审计写入
    - 不自动修改检索配置（容量超限只输出建议）

开关/参数（优先级: 环境变量 > config.yaml > 硬编码默认值）:
    LEARNING_LIFECYCLE_ENABLED          / learning.lifecycle.enabled          (默认 false)
    LEARNING_LIFECYCLE_INTERVAL_HOURS   / learning.lifecycle.interval_hours   (默认 24)
    LEARNING_LIFECYCLE_UNUSED_DAYS      / learning.lifecycle.unused_days      (默认 90)
    LEARNING_LIFECYCLE_ARCHIVE_DAYS     / learning.lifecycle.archive_days     (默认 180)
    LEARNING_LIFECYCLE_DRY_RUN          / learning.lifecycle.dry_run          (默认 true)
    LEARNING_LIFECYCLE_AUDIT_FILE       / learning.lifecycle.audit_file       (默认 data/skill_lifecycle_audit.jsonl)
    LEARNING_LIFECYCLE_UPGRADE_THRESHOLD / skills_mgmt.scale.upgrade_threshold (默认 30)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

TASK_NAME = "生命周期检查"
DEFAULT_INTERVAL_HOURS = 24
DEFAULT_UNUSED_DAYS = 90
DEFAULT_ARCHIVE_DAYS = 180
DEFAULT_UPGRADE_THRESHOLD = 30
DEFAULT_AUDIT_FILE = "data/skill_lifecycle_audit.jsonl"
DEFAULT_DRY_RUN = True
_ENV_PREFIX = "LEARNING_LIFECYCLE"

# 状态迁移判定用合法状态值（models.py SkillStatus 枚举值）
_STATUS_PUBLISHED = "published"
_STATUS_DEPRECATED = "deprecated"
_STATUS_ARCHIVED = "archived"


def _config_yaml() -> Optional[Dict[str, Any]]:
    """读取仓库根 config.yaml（失败返回 None，不抛异常）。"""
    cfg_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
    if not cfg_path.exists():
        return None
    import yaml as _yaml

    with open(cfg_path, "r", encoding="utf-8") as f:
        return _yaml.safe_load(f) or {}


def _enabled() -> bool:
    """优先级: 环境变量 > config.yaml learning.lifecycle.enabled > 默认 false。"""
    env = os.environ.get(f"{_ENV_PREFIX}_ENABLED")
    if env is not None and env.strip():
        return env.strip().lower() in ("true", "1", "yes")
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("learning", {}) or {}).get("lifecycle", {})
                   or {}).get("enabled")
            if val is not None:
                return str(val).strip().lower() in ("true", "1", "yes")
    except Exception as e:  # noqa: BLE001 配置解析失败回退默认
        logger.debug(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle.failed', 'msg': "[Lifecycle] config.yaml 读取失败: %s" % e}))
    return False


def _interval_hours() -> int:
    """优先级: 环境变量 > config.yaml learning.lifecycle.interval_hours > 默认 24。"""
    env = os.environ.get(f"{_ENV_PREFIX}_INTERVAL_HOURS")
    if env is not None and env.strip():
        try:
            return max(1, int(env.strip()))
        except ValueError:
            logger.warning(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle', 'msg': "[Lifecycle] 非法 interval_hours=%r，使用默认 %d" % (env, DEFAULT_INTERVAL_HOURS)}))
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("learning", {}) or {}).get("lifecycle", {})
                   or {}).get("interval_hours")
            if val is not None:
                try:
                    return max(1, int(val))
                except (TypeError, ValueError):
                    pass
    except Exception as e:  # noqa: BLE001
        logger.debug(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle.failed', 'msg': "[Lifecycle] config.yaml 读取失败: %s" % e}))
    return DEFAULT_INTERVAL_HOURS


def _unused_days() -> int:
    """PUBLISHED → DEPRECATED 闲置阈值（默认 90）。"""
    env = os.environ.get(f"{_ENV_PREFIX}_UNUSED_DAYS")
    if env is not None and env.strip():
        try:
            return max(0, int(env.strip()))
        except ValueError:
            logger.warning(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle', 'msg': "[Lifecycle] 非法 unused_days=%r，使用默认 %d" % (env, DEFAULT_UNUSED_DAYS)}))
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("learning", {}) or {}).get("lifecycle", {})
                   or {}).get("unused_days")
            if val is not None:
                try:
                    return max(0, int(val))
                except (TypeError, ValueError):
                    pass
    except Exception as e:  # noqa: BLE001
        logger.debug(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle.failed', 'msg': "[Lifecycle] config.yaml 读取失败: %s" % e}))
    return DEFAULT_UNUSED_DAYS


def _archive_days() -> int:
    """DEPRECATED → ARCHIVED 闲置阈值（默认 180）。"""
    env = os.environ.get(f"{_ENV_PREFIX}_ARCHIVE_DAYS")
    if env is not None and env.strip():
        try:
            return max(0, int(env.strip()))
        except ValueError:
            logger.warning(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle', 'msg': "[Lifecycle] 非法 archive_days=%r，使用默认 %d" % (env, DEFAULT_ARCHIVE_DAYS)}))
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("learning", {}) or {}).get("lifecycle", {})
                   or {}).get("archive_days")
            if val is not None:
                try:
                    return max(0, int(val))
                except (TypeError, ValueError):
                    pass
    except Exception as e:  # noqa: BLE001
        logger.debug(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle.failed', 'msg': "[Lifecycle] config.yaml 读取失败: %s" % e}))
    return DEFAULT_ARCHIVE_DAYS


def _upgrade_threshold() -> int:
    """容量超限阈值：env > config.yaml skills_mgmt.scale.upgrade_threshold > 默认 30。"""
    env = os.environ.get(f"{_ENV_PREFIX}_UPGRADE_THRESHOLD")
    if env is not None and env.strip():
        try:
            return max(1, int(env.strip()))
        except ValueError:
            logger.warning(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle', 'msg': "[Lifecycle] 非法 upgrade_threshold=%r，使用默认 %d" % (env, DEFAULT_UPGRADE_THRESHOLD)}))
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("skills_mgmt", {}) or {}).get("scale", {})
                   or {}).get("upgrade_threshold")
            if val is None:
                val = ((cfg.get("learning", {}) or {}).get("lifecycle", {})
                       or {}).get("upgrade_threshold")
            if val is not None:
                try:
                    return max(1, int(val))
                except (TypeError, ValueError):
                    pass
    except Exception as e:  # noqa: BLE001
        logger.debug(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle.failed', 'msg': "[Lifecycle] config.yaml 读取失败: %s" % e}))
    return DEFAULT_UPGRADE_THRESHOLD


def _dry_run() -> bool:
    """dry-run 默认 true（不可变约束：默认不产生写操作）。"""
    env = os.environ.get(f"{_ENV_PREFIX}_DRY_RUN")
    if env is not None and env.strip():
        return env.strip().lower() in ("true", "1", "yes")
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("learning", {}) or {}).get("lifecycle", {})
                   or {}).get("dry_run")
            if val is not None:
                return str(val).strip().lower() in ("true", "1", "yes")
    except Exception as e:  # noqa: BLE001
        logger.debug(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle.failed', 'msg': "[Lifecycle] config.yaml 读取失败: %s" % e}))
    return DEFAULT_DRY_RUN


def _audit_file() -> str:
    """审计日志路径（默认 data/skill_lifecycle_audit.jsonl，env 覆盖）。"""
    env = os.environ.get(f"{_ENV_PREFIX}_AUDIT_FILE")
    if env is not None and env.strip():
        return env.strip()
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("learning", {}) or {}).get("lifecycle", {})
                   or {}).get("audit_file")
            if val:
                return str(val)
    except Exception as e:  # noqa: BLE001
        logger.debug(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle.failed', 'msg': "[Lifecycle] config.yaml 读取失败: %s" % e}))
    return DEFAULT_AUDIT_FILE


def _idle_days(skill: Any, now: datetime) -> Optional[int]:
    """技能闲置天数。

    last_used_at 存在 → 以其为准；缺失时仅当 usage_count==0 才以创建时间
    近似（从未使用视为从创建起闲置）。usage_count>0 但 last_used_at 缺失
    属异常数据，保守返回 None（不迁移，防误判）。
    """
    def _days_since(iso_str: Optional[str]) -> Optional[int]:
        if not iso_str:
            return None
        try:
            used = datetime.fromisoformat(iso_str)
        except (TypeError, ValueError):
            return None
        delta = now - used
        if delta.total_seconds() < 0:
            return 0
        return delta.days

    last_used = getattr(skill.metrics, "last_used_at", None)
    if last_used:
        return _days_since(last_used)
    if skill.metrics.usage_count == 0:
        return _days_since(getattr(skill, "created_at", None))
    return None


class LifecycleManager:
    """Skill 生命周期自动淘汰判定器（默认 dry-run，绝不删除文件）"""

    def __init__(self, *, service: Optional[Any] = None,
                 audit_path: Optional[str] = None,
                 now: Optional[datetime] = None):
        """Args:
            service: SkillsMgmtService 实例（None 时懒加载默认实例）
            audit_path: 审计日志路径（None 时按配置/默认）
            now: 判定基准时间（None 取当前时间；测试可注入固定时间）
        """
        self._service = service
        self._audit_path = audit_path
        self._now = now
        self._scheduled_task_id: Optional[str] = None

    def _get_service(self) -> Any:
        if self._service is None:
            from agent.skills_mgmt.service import SkillsMgmtService
            self._service = SkillsMgmtService()
        return self._service

    # ─── 主入口 ───

    def run_lifecycle_check(self, dry_run: bool = True) -> Dict[str, Any]:
        """扫描全部 Skill 执行生命周期判定（默认 dry-run）。

        Returns:
            报告 dict：started_at/finished_at/dry_run/total_skills/
            deprecated/archived/suggestions/errors/audit_log
        """
        svc = self._get_service()
        now = self._now or datetime.now()
        unused = _unused_days()
        archive = _archive_days()
        threshold = _upgrade_threshold()
        report: Dict[str, Any] = {
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "dry_run": bool(dry_run),
            "total_skills": 0,
            "deprecated": [],
            "archived": [],
            "suggestions": [],
            "errors": [],
            "audit_log": self._audit_path or _audit_file(),
        }
        try:
            skills = svc.store.list_all()
        except Exception as e:  # noqa: BLE001
            logger.error(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle.failed', 'msg': "[Lifecycle] 读取技能库失败: %s" % e}))
            report["errors"].append({"skill_id": "*", "error": str(e)})
            report["finished_at"] = datetime.now().isoformat(timespec="seconds")
            return report

        report["total_skills"] = len(skills)
        for skill in skills:
            # 逐 skill try/except：任一失败不中断批量
            try:
                self._process_skill(svc, skill, now=now, unused_days=unused,
                                    archive_days=archive, dry_run=dry_run,
                                    report=report)
            except Exception as e:  # noqa: BLE001
                logger.error(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle.failed', 'msg': "[Lifecycle] 技能 %s 判定失败: %s" % (skill.id, e)}))
                report["errors"].append(
                    {"skill_id": skill.id, "error": str(e)})

        # 容量超限 → 检索升级建议（只写报告，不自动改检索配置）
        if report["total_skills"] > threshold:
            logger.info(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle', 'msg': "[Lifecycle] 容量超限建议 skill_count=%s > 阈值 %s ""（只报告，不自动变更检索配置）" % (report["total_skills"], threshold)}))
            report["suggestions"].append({
                "type": "retrieval_upgrade",
                "skill_count": report["total_skills"],
                "threshold": threshold,
                "message": (
                    f"技能总数 {report['total_skills']} 超过检索升级阈值 "
                    f"{threshold}，建议评估升级检索/索引方案（不自动变更配置）"),
            })
        report["finished_at"] = datetime.now().isoformat(timespec="seconds")
        return report

    def _process_skill(self, svc: Any, skill: Any, *, now: datetime,
                       unused_days: int, archive_days: int, dry_run: bool,
                       report: Dict[str, Any]) -> None:
        """单个技能的状态迁移判定。"""
        from .models import SkillStatus
        status = skill.status.value if hasattr(skill.status, "value") else skill.status
        idle = _idle_days(skill, now)
        logger.info(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle', 'msg': "[Lifecycle] 判定 skill=%s status=%s idle_days=%s dry_run=%s ""(阈值: unused_days=%s archive_days=%s)" % (skill.id, status, idle, dry_run, unused_days, archive_days)}))
        if idle is None and status in (_STATUS_PUBLISHED, _STATUS_DEPRECATED):
            logger.info(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle', 'msg': "[Lifecycle] skill=%s 无闲置时间依据（usage>0 且缺 last_used_at），""保守不迁移" % skill.id}))

        if status == _STATUS_PUBLISHED:
            if idle is not None and idle > unused_days:
                if dry_run:
                    logger.info(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle', 'msg': "[Lifecycle] dry_run 预演 deprecate skill=%s idle_days=%s ""> 阈值 %s（仅报告不迁移，不删文件）" % (skill.id, idle, unused_days)}))
                    report["deprecated"].append({
                        "skill_id": skill.id, "from_status": status,
                        "to_status": _STATUS_DEPRECATED, "idle_days": idle,
                        "threshold": unused_days})
                    return
                logger.info(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle', 'msg': "[Lifecycle] 正式迁移 deprecate skill=%s from=%s to=%s ""idle_days=%s 阈值=%s（仅状态迁移，不删文件，可人工改回）" % (skill.id, status, _STATUS_DEPRECATED, idle, unused_days)}))
                skill.status = SkillStatus.DEPRECATED
                svc.store.upsert(skill)
                record = {
                    "skill_id": skill.id, "action": "deprecate",
                    "from_status": status, "to_status": _STATUS_DEPRECATED,
                    "idle_days": idle, "threshold_days": unused_days,
                }
                report["deprecated"].append(record)
                self._audit(record)
            return

        if status == _STATUS_DEPRECATED:
            if idle is not None and idle > archive_days:
                if dry_run:
                    logger.info(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle', 'msg': "[Lifecycle] dry_run 预演 archive skill=%s idle_days=%s ""> 阈值 %s（仅报告不迁移，不删文件）" % (skill.id, idle, archive_days)}))
                    report["archived"].append({
                        "skill_id": skill.id, "from_status": status,
                        "to_status": _STATUS_ARCHIVED, "idle_days": idle,
                        "threshold": archive_days})
                    return
                logger.info(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle', 'msg': "[Lifecycle] 正式迁移 archive skill=%s from=%s to=%s ""idle_days=%s 阈值=%s（仅状态迁移，不删文件，可人工改回）" % (skill.id, status, _STATUS_ARCHIVED, idle, archive_days)}))
                skill.status = SkillStatus.ARCHIVED
                svc.store.upsert(skill)
                record = {
                    "skill_id": skill.id, "action": "archive",
                    "from_status": status, "to_status": _STATUS_ARCHIVED,
                    "idle_days": idle, "threshold_days": archive_days,
                }
                report["archived"].append(record)
                self._audit(record)

    # ─── 审计 ───

    def _audit(self, record: Dict[str, Any]) -> None:
        """正式迁移逐条写 JSONL 审计日志（失败仅告警不阻断）。"""
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": "lifecycle_action",
            **record,
        }
        try:
            path = Path(self._audit_path or _audit_file())
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle.failed', 'msg': "[Lifecycle] 审计日志写入失败: %s" % e}))

    # ─── 调度注册（与 TASK-04 同一 task_scheduler 收口） ───

    def schedule(self, *, interval_hours: Optional[int] = None) -> Dict[str, Any]:
        """注册每日生命周期检查任务（默认关闭，安全底线）。"""
        hours = interval_hours if interval_hours is not None else _interval_hours()
        if not _enabled():
            logger.warning(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle', 'msg': "[Lifecycle] 调度默认关闭（安全底线）；""开启: config learning.lifecycle.enabled=true / "".env LEARNING_LIFECYCLE_ENABLED=true"}))
            return {
                "status": "disabled",
                "interval_hours": hours,
                "dry_run": _dry_run(),
                "note": "learning.lifecycle.enabled=false，生命周期检查默认关闭（安全底线）",
            }
        try:
            from agent.task_scheduler import get_scheduler
            sched = get_scheduler()
        except Exception as e:  # noqa: BLE001 调度器不可用
            logger.error(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle.failed', 'msg': "[Lifecycle] 调度器不可用: %s" % e}))
            return {"status": "error", "error": str(e)}
        sched.add_interval_task(
            TASK_NAME, func=self._scheduled_run, interval_seconds=hours * 3600)
        self._scheduled_task_id = (
            sched.tasks[-1]["task_id"] if sched.tasks else TASK_NAME)
        logger.info(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle', 'msg': "[Lifecycle] 生命周期检查任务已注册 interval_hours=%d ""dry_run=%s" % (hours, _dry_run())}))
        return {
            "status": "scheduled",
            "task_id": self._scheduled_task_id,
            "interval_hours": hours,
            "dry_run": _dry_run(),
            "note": "定时执行 run_lifecycle_check(dry_run=配置值)，仅状态迁移不删文件",
        }

    def unschedule(self) -> bool:
        """注销生命周期检查任务（按固定任务名定位，可跨实例）。"""
        try:
            from agent.task_scheduler import get_scheduler
            sched = get_scheduler()
        except Exception as e:  # noqa: BLE001
            logger.error(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle.failed', 'msg': "[Lifecycle] 调度注销失败: %s" % e}))
            return False
        for task in sched.tasks:
            if task.get("name") == TASK_NAME:
                removed = sched.remove_task(task["task_id"])
                self._scheduled_task_id = None
                return removed
        return False

    def _scheduled_run(self) -> None:
        """调度触发入口：跑一轮生命周期检查；异常不抛出（调度线程稳定性）。"""
        logger.info(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle', 'msg': "[Lifecycle] scheduled_run.start dry_run=%s" % _dry_run()}))
        try:
            self.run_lifecycle_check(dry_run=_dry_run())
        except Exception as e:  # noqa: BLE001
            logger.error(log_dict({'module_name': 'lifecycle', 'action': 'lifecycle.failed', 'msg': "[Lifecycle] scheduled_run 失败: %s" % e}))


__all__: List[str] = [
    "LifecycleManager", "TASK_NAME", "DEFAULT_INTERVAL_HOURS",
    "DEFAULT_UNUSED_DAYS", "DEFAULT_ARCHIVE_DAYS",
    "DEFAULT_UPGRADE_THRESHOLD", "DEFAULT_AUDIT_FILE",
    "_enabled", "_interval_hours", "_unused_days", "_archive_days",
    "_upgrade_threshold", "_dry_run", "_audit_file", "_idle_days",
]
