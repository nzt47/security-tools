"""新颖性事件学习钩子 — 感知侧学习管线沉淀层（TASK-06 Step 2）

ChangeDetector 出口旁路钩子: 分类 → 分级 → 记忆 / 建议草稿。
默认观察模式（sensor_learning.enabled=false → 零副作用）；
开启后只产 DRAFT 草稿，绝不注册技能（【不易】不变式）。

分级路由（任务书 §4 Step2；行为漂移验收"记录 + 草稿"）:
    低置信(<0.4)      → 写记忆（JSONL，event 标签）
    中置信(0.4~0.7)   → 写记忆（记录）
    高置信(≥0.7)      → 审计记录 + 建议草稿（仅 DRAFT，不注册技能）

开关/参数（优先级: 环境变量 > config.yaml > 硬编码默认值）:
    SENSOR_LEARNING_ENABLED                  / learning.sensor_learning.enabled                (默认 false)
    SENSOR_LEARNING_DRIFT_THRESHOLD          / learning.sensor_learning.drift_threshold         (默认 0.3)
    SENSOR_LEARNING_BASELINE_RETENTION_WEEKS / learning.sensor_learning.baseline_retention_weeks (默认 8)
    SENSOR_LEARNING_DRAFT_DIR                / learning.sensor_learning.draft_dir               (默认 data/learning/novelty_suggestions)
    SENSOR_LEARNING_AUDIT_FILE               / learning.sensor_learning.audit_file              (默认 data/learning/novelty_audit.jsonl)
    SENSOR_LEARNING_MEMORY_DIR               / learning.sensor_learning.memory_dir              (默认 data/learning/novelty_memory)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from sensor.novelty import (
    DEFAULT_DRIFT_THRESHOLD,
    EVENT_BEHAVIOR_DRIFT,
    EVENT_FILE_CHANGE,
    EVENT_HARDWARE_CHANGE,
    EVENT_PROCESS_CHANGE,
    NoveltyEvent,
    classify_changes,
)

logger = logging.getLogger(__name__)

DEFAULT_DRAFT_DIR = "data/learning/novelty_suggestions"
DEFAULT_AUDIT_FILE = "data/learning/novelty_audit.jsonl"
DEFAULT_MEMORY_DIR = "data/learning/novelty_memory"
DEFAULT_RETENTION_WEEKS = 8

# 草稿标题（供 TASK-04 审核链作为建议输入）
_DRAFT_TITLES: Dict[str, str] = {
    EVENT_HARDWARE_CHANGE: "硬件环境变化建议",
    EVENT_PROCESS_CHANGE: "进程变化建议",
    EVENT_FILE_CHANGE: "文件批量变更建议",
    EVENT_BEHAVIOR_DRIFT: "行为漂移复核建议",
}


# ═══════════════════════════════════════════════════════════════
#  配置读取（env > config.yaml > 硬编码默认值）
# ═══════════════════════════════════════════════════════════════

def _config_yaml() -> Optional[Dict[str, Any]]:
    """读取仓库根 config.yaml（失败返回 None，不抛异常）。"""
    cfg_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
    if not cfg_path.exists():
        return None
    import yaml as _yaml  # 延迟导入，避免硬依赖

    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            return _yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001 配置解析失败回退默认
        return None


def _cfg_value(key: str, default: Any) -> Any:
    """读取 learning.sensor_learning.<key>，失败回退 default。"""
    cfg = _config_yaml()
    if cfg is not None:
        val = ((cfg.get("learning", {}) or {}).get("sensor_learning", {}) or {}).get(key)
        if val is not None:
            return val
    return default


def _sensor_learning_enabled() -> bool:
    """优先级: env SENSOR_LEARNING_ENABLED > config learning.sensor_learning.enabled > 默认 false。"""
    env = os.environ.get("SENSOR_LEARNING_ENABLED")
    if env is not None and env.strip():
        return env.strip().lower() in ("true", "1", "yes")
    return str(_cfg_value("enabled", False)).strip().lower() in ("true", "1", "yes")


def _drift_threshold() -> float:
    """行为漂移判定阈值（默认 0.3）。"""
    env = os.environ.get("SENSOR_LEARNING_DRIFT_THRESHOLD")
    if env is not None and env.strip():
        try:
            return float(env.strip())
        except ValueError:
            logger.warning("[NoveltyHooks] 非法 drift_threshold=%r，使用默认 %s",
                           env, DEFAULT_DRIFT_THRESHOLD)
    try:
        return float(_cfg_value("drift_threshold", DEFAULT_DRIFT_THRESHOLD))
    except (TypeError, ValueError):
        return DEFAULT_DRIFT_THRESHOLD


def _baseline_retention_weeks() -> int:
    """行为基线保留周数（默认 8）。"""
    env = os.environ.get("SENSOR_LEARNING_BASELINE_RETENTION_WEEKS")
    if env is not None and env.strip():
        try:
            return max(1, int(env.strip()))
        except ValueError:
            pass
    try:
        return max(1, int(_cfg_value("baseline_retention_weeks", DEFAULT_RETENTION_WEEKS)))
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_WEEKS


def _draft_dir() -> str:
    """建议草稿目录（默认 data/learning/novelty_suggestions）。"""
    env = os.environ.get("SENSOR_LEARNING_DRAFT_DIR")
    if env is not None and env.strip():
        return env.strip()
    return str(_cfg_value("draft_dir", DEFAULT_DRAFT_DIR))


def _audit_path() -> str:
    """学习审计日志（默认 data/learning/novelty_audit.jsonl）。"""
    env = os.environ.get("SENSOR_LEARNING_AUDIT_FILE")
    if env is not None and env.strip():
        return env.strip()
    return str(_cfg_value("audit_file", DEFAULT_AUDIT_FILE))


def _memory_dir() -> str:
    """事件记忆目录（默认 data/learning/novelty_memory）。"""
    env = os.environ.get("SENSOR_LEARNING_MEMORY_DIR")
    if env is not None and env.strip():
        return env.strip()
    return str(_cfg_value("memory_dir", DEFAULT_MEMORY_DIR))


# ═══════════════════════════════════════════════════════════════
#  分级动作（记忆 / 审计 / 草稿）— 各自独立兜底
# ═══════════════════════════════════════════════════════════════

def _ts_slug() -> str:
    """文件名时间戳（毫秒级，避免同秒冲突）。"""
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:23]


def _memory_record(event: NoveltyEvent, memory_dir: Optional[str] = None) -> str:
    """写记忆（JSONL，event 标签）; 失败返回空串（不抛异常）。"""
    try:
        mdir = memory_dir or _memory_dir()
        path = Path(mdir) / "novelty_memory.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": "novelty_event",
            "event_type": event.event_type,
            "severity": event.severity,
            "confidence": event.confidence,
            "level": event.level,
            "diff_summary": event.diff_summary,
            "suggested_action": event.suggested_action,
            "detail": event.detail,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return str(path)
    except Exception as e:  # noqa: BLE001 记忆写入失败兜底（感知主链路不受影响）
        logger.warning("[NoveltyHooks] 记忆写入失败: %s", e)
        return ""


def _audit_record(event: NoveltyEvent, audit_path: Optional[str] = None) -> str:
    """学习审计记录（JSONL）; 失败返回空串。"""
    try:
        path = Path(audit_path or _audit_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": "novelty_learning",
            "event_type": event.event_type,
            "severity": event.severity,
            "confidence": event.confidence,
            "level": event.level,
            "diff_summary": event.diff_summary,
            "suggested_action": event.suggested_action,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return str(path)
    except Exception as e:  # noqa: BLE001 审计写入失败兜底
        logger.warning("[NoveltyHooks] 审计写入失败: %s", e)
        return ""


def _write_draft(event: NoveltyEvent, draft_dir: Optional[str] = None) -> str:
    """产建议草稿（仅 DRAFT 文件，绝不注册技能）; 失败返回空串。"""
    try:
        ddir = draft_dir or _draft_dir()
        path = Path(ddir) / f"novelty_suggestion_{event.event_type}_{_ts_slug()}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        draft = {
            "draft_status": "DRAFT",
            "title": _DRAFT_TITLES.get(event.event_type, "环境变化建议"),
            "event_type": event.event_type,
            "diff_summary": event.diff_summary,
            "suggested_action": event.suggested_action,
            "confidence": event.confidence,
            "severity": event.severity,
            "created_at": event.created_at,
            "detail": event.detail,
            "note": "TASK-06 环境变化提示草稿：供 TASK-04 审核链作为输入；不自动注册技能",
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(draft, f, ensure_ascii=False, indent=2)
        return str(path)
    except Exception as e:  # noqa: BLE001 草稿写入失败兜底
        logger.warning("[NoveltyHooks] 建议草稿写入失败: %s", e)
        return ""


def handle_novelty_event(event: NoveltyEvent, *, memory_dir: Optional[str] = None,
                         draft_dir: Optional[str] = None,
                         audit_path: Optional[str] = None) -> Dict[str, str]:
    """按置信度分级处理 NoveltyEvent（Step 2 钩子路由）。

    低/中置信 → 写记忆；高置信 → 审计 + 草稿。
    所有动作独立兜底，返回 {"memory"|"draft"|"audit": 路径} 执行摘要。
    """
    result: Dict[str, str] = {}
    if event.level == "high":
        result["audit"] = _audit_record(event, audit_path)
        result["draft"] = _write_draft(event, draft_dir)
    else:
        result["memory"] = _memory_record(event, memory_dir)
    return result


# ═══════════════════════════════════════════════════════════════
#  钩子构造与接线
# ═══════════════════════════════════════════════════════════════

def make_learning_hook(*, memory_dir: Optional[str] = None,
                       draft_dir: Optional[str] = None,
                       audit_path: Optional[str] = None
                       ) -> Callable[[List[Dict[str, Any]]], None]:
    """构造 ChangeDetector 出口学习钩子（默认观察模式）。

    钩子签名: hook(changes: List[dict]) -> None
    未开启 sensor_learning.enabled → 零副作用直接返回（观察模式）。
    """
    def hook(changes: List[Dict[str, Any]]) -> None:
        if not _sensor_learning_enabled():
            return  # 观察模式: 零学习副作用（仅既有日志）
        events = classify_changes(changes)
        for event in events:
            # 监控：NoveltyEvent 生成与分类（类型/置信度/分级）
            logger.info("[NoveltyHooks] NoveltyEvent 分类: %s confidence=%.2f level=%s",
                        event.event_type, event.confidence, event.level)
            try:
                handle_novelty_event(event, memory_dir=memory_dir,
                                     draft_dir=draft_dir, audit_path=audit_path)
            except Exception as e:  # noqa: BLE001 单事件失败不阻断
                logger.debug("[NoveltyHooks] 事件处理失败: %s", e)

    return hook


def wire_body_sensor(body_sensor) -> bool:
    """在 BodySensor 上挂载变更学习钩子（旁路）。

    未开启时钩子内部零副作用；任何异常仅日志，不影响感知采集。
    """
    try:
        return bool(body_sensor.attach_change_learning_hook(make_learning_hook()))
    except Exception as e:  # noqa: BLE001 挂载失败旁路
        logger.debug("[NoveltyHooks] 学习钩子挂载失败（旁路，不影响感知）: %s", e)
        return False


__all__: List[str] = [
    "make_learning_hook", "wire_body_sensor", "handle_novelty_event",
    "_sensor_learning_enabled", "_drift_threshold", "_baseline_retention_weeks",
    "_draft_dir", "_audit_path", "_memory_dir",
    "_memory_record", "_audit_record", "_write_draft",
]
