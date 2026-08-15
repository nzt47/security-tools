"""新颖性感知模型与分类器 — 感知侧学习管线数据面（TASK-06 Step 1）

把 ChangeDetector 的 diff 结果映射为 NoveltyEvent（事件类型 + 置信度 + 分级），
并承担 change_log.json 容量控制（trim_change_log / default_max_entries）。

【不易】约束:
    - 纯数据/纯函数模块，零运行时依赖（yaml 延迟导入）
    - 不 import agent（sensor 是底层包，禁止反向依赖，防循环）
    - 不修改 ChangeDetector diff 逻辑与 SensorReading 模型（本文件仅被它们引用）
    - 分类规则: 硬件变更→高置信；进程新增/移除→中；文件批量变更→低；行为漂移→中

配置优先级（env > config.yaml > 硬编码默认值）:
    SENSOR_LEARNING_CHANGE_LOG_MAX_ENTRIES / learning.sensor_learning.change_log_max_entries (默认 10000)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# 事件类型常量（与 agent/learning/novelty_hooks.py 共享契约）
EVENT_HARDWARE_CHANGE = "hardware_change"
EVENT_PROCESS_CHANGE = "process_change"
EVENT_FILE_CHANGE = "file_change"
EVENT_BEHAVIOR_DRIFT = "behavior_drift"

DEFAULT_DRIFT_THRESHOLD = 0.3
DEFAULT_CHANGE_LOG_MAX_ENTRIES = 10000

# 各事件类型的建议动作文案（供 TASK-04 审核链作为建议输入）
_SUGGESTED_ACTIONS: Dict[str, str] = {
    EVENT_HARDWARE_CHANGE: "检查硬件变更是否影响运行环境，必要时更新环境适配或驱动校验策略",
    EVENT_PROCESS_CHANGE: "核对新增/移除进程是否影响既有工作流，可评估沉淀为新技能建议（走审核链）",
    EVENT_FILE_CHANGE: "文件批量变更提示，评估是否纳入监控清单或备份策略",
    EVENT_BEHAVIOR_DRIFT: "周级行为漂移复核：对比基线差异，评估是否需要更新技能或工作流建议（走审核链）",
}

# 分类规则（ChangeDetector diff type → 事件类型 + 置信度）
_HARDWARE_TYPES = {
    "device_added", "device_removed", "device_modified",
    "disk_mounted", "disk_unmounted",
}
_PROCESS_TYPES = {
    "process_started", "process_stopped", "service_state_changed",
}
_FILE_TYPES = {
    "file_added", "file_modified", "file_removed", "file_changed",
}


@dataclass
class NoveltyEvent:
    """新颖性事件（ChangeDetector diff 的语义化封装）。"""

    event_type: str
    severity: str
    diff_summary: str
    confidence: float
    suggested_action: str
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def level(self) -> str:
        """置信度分级: ≥0.7 high；≥0.4 medium；<0.4 low。"""
        if self.confidence >= 0.7:
            return "high"
        if self.confidence >= 0.4:
            return "medium"
        return "low"

    def to_dict(self) -> Dict[str, Any]:
        """序列化为可 JSON 落盘的字典。"""
        return {
            "event_type": self.event_type,
            "severity": self.severity,
            "diff_summary": self.diff_summary,
            "confidence": self.confidence,
            "suggested_action": self.suggested_action,
            "created_at": self.created_at,
            "detail": self.detail,
            "level": self.level,
        }


# ═══════════════════════════════════════════════════════════════
#  配置（env > config.yaml > 硬编码默认值；不 import agent）
# ═══════════════════════════════════════════════════════════════

def _config_value(key: str, default: Any) -> Any:
    """读取仓库根 config.yaml 的 learning.sensor_learning.<key>（失败回退 default）。"""
    cfg_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
    if not os.path.exists(cfg_path):
        return default
    try:
        import yaml as _yaml  # 延迟导入，避免硬依赖
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = _yaml.safe_load(f) or {}
        val = ((cfg.get("learning", {}) or {}).get("sensor_learning", {}) or {}).get(key)
        return val if val is not None else default
    except Exception:  # noqa: BLE001 配置解析失败回退默认
        return default


def default_max_entries() -> int:
    """change_log.json 容量上限（env > config.yaml > 默认 10000）。"""
    env = os.environ.get("SENSOR_LEARNING_CHANGE_LOG_MAX_ENTRIES")
    if env is not None and env.strip():
        try:
            return max(1, int(env.strip()))
        except ValueError:
            pass
    try:
        return max(1, int(_config_value("change_log_max_entries", DEFAULT_CHANGE_LOG_MAX_ENTRIES)))
    except (TypeError, ValueError):
        return DEFAULT_CHANGE_LOG_MAX_ENTRIES


def trim_change_log(entries: List[Dict[str, Any]],
                    max_entries: Optional[int] = None) -> List[Dict[str, Any]]:
    """滚动裁剪: 超出 max_entries 删除最旧（保持顺序）；未超/未设上限原样返回。"""
    if not max_entries or max_entries <= 0:
        return entries
    if len(entries) <= max_entries:
        return entries
    return entries[-max_entries:]


# ═══════════════════════════════════════════════════════════════
#  分类器（diff → NoveltyEvent）
# ═══════════════════════════════════════════════════════════════

def _build_event(event_type: str, confidence: float,
                 change: Dict[str, Any]) -> NoveltyEvent:
    """由一条 diff 条目构造 NoveltyEvent（severity 透传源条目）。"""
    raw_sev = change.get("severity")
    sev = raw_sev if isinstance(raw_sev, str) and raw_sev in (
        "normal", "warning", "critical") else "normal"
    return NoveltyEvent(
        event_type=event_type,
        severity=sev,
        diff_summary=change.get("description") or f"检测到 {event_type} 变更",
        confidence=confidence,
        suggested_action=_SUGGESTED_ACTIONS.get(
            event_type, "复核环境变化，评估是否需要沉淀为学习信号"),
        detail={k: change.get(k) for k in ("name", "previous", "current", "detail")
                if k in change},
    )


def classify_change(change: Optional[Dict[str, Any]]) -> Optional[NoveltyEvent]:
    """将单条 ChangeDetector diff 映射为事件类型与置信度。

    规则: 硬件变更→高置信(0.85)；进程新增/移除→中(0.55)；
         文件批量变更→低(0.30)；行为漂移→中(0.50)。
    未命中规则（注册表/环境变量/系统信息等噪音）→ None（不学习）。
    """
    if not change:
        return None
    ctype = str(change.get("type", ""))
    if ctype.startswith("hardware_") or ctype in _HARDWARE_TYPES:
        return _build_event(EVENT_HARDWARE_CHANGE, 0.85, change)
    if ctype in _PROCESS_TYPES:
        return _build_event(EVENT_PROCESS_CHANGE, 0.55, change)
    if ctype in _FILE_TYPES:
        return _build_event(EVENT_FILE_CHANGE, 0.30, change)
    if ctype == EVENT_BEHAVIOR_DRIFT:
        return _build_event(EVENT_BEHAVIOR_DRIFT, 0.50, change)
    return None


def classify_changes(changes: List[Dict[str, Any]]) -> List[NoveltyEvent]:
    """批量分类，过滤未命中规则（噪音）的条目。"""
    events: List[NoveltyEvent] = []
    for change in changes or []:
        event = classify_change(change)
        if event is not None:
            events.append(event)
    return events


# ═══════════════════════════════════════════════════════════════
#  行为漂移（周级基线对比）
# ═══════════════════════════════════════════════════════════════

def week_key(day: Optional[Any] = None) -> str:
    """周基线键 = 本周周一日期（YYYY-MM-DD，跨会话周级对齐）。

    例: 2026-08-14（周五）→ "2026-08-10"；2026-08-10（周一）→ "2026-08-10"。
    """
    if day is None:
        day = datetime.now().date()
    if isinstance(day, datetime):
        day = day.date()
    monday = day - timedelta(days=day.weekday())
    return monday.isoformat()


def compute_drift_score(prev_metrics: Dict[str, Any],
                        cur_metrics: Dict[str, Any]) -> float:
    """周级基线漂移度 = 重叠指标相对偏差均值（|cur-prev|/prev）。

    基线为 0 / 非数值 / 无重叠指标 → 跳过，最终无样本返回 0.0。
    """
    scores: List[float] = []
    for key, prev_val in (prev_metrics or {}).items():
        if key not in (cur_metrics or {}):
            continue
        try:
            prev_f = float(prev_val)
            cur_f = float(cur_metrics[key])
        except (TypeError, ValueError):
            continue
        if prev_f == 0:
            continue  # 基线为 0 无法计算相对偏差，跳过
        scores.append(abs(cur_f - prev_f) / abs(prev_f))
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def detect_behavior_drift(previous: Optional[Dict[str, Any]],
                          current: Optional[Dict[str, Any]],
                          threshold: float) -> Optional[NoveltyEvent]:
    """对比最近两份周基线，漂移度 ≥ 阈值 → behavior_drift 事件（中置信 0.5）。

    缺基线 / 低于阈值 → None（不产事件）。
    """
    if not previous or not current:
        return None
    score = compute_drift_score(
        (previous.get("metrics") or {}), (current.get("metrics") or {}))
    if score < threshold:
        return None
    return NoveltyEvent(
        event_type=EVENT_BEHAVIOR_DRIFT,
        severity="warning",
        diff_summary=(
            f"周级行为漂移: 基线相对偏差均值 {score:.4f} ≥ 阈值 {threshold:.2f}"),
        confidence=0.50,
        suggested_action=_SUGGESTED_ACTIONS[EVENT_BEHAVIOR_DRIFT],
        detail={"drift_score": round(score, 6), "threshold": threshold},
    )


__all__: List[str] = [
    "NoveltyEvent",
    "classify_change", "classify_changes",
    "trim_change_log", "default_max_entries",
    "week_key", "compute_drift_score", "detect_behavior_drift",
    "EVENT_HARDWARE_CHANGE", "EVENT_PROCESS_CHANGE",
    "EVENT_FILE_CHANGE", "EVENT_BEHAVIOR_DRIFT",
    "DEFAULT_DRIFT_THRESHOLD", "DEFAULT_CHANGE_LOG_MAX_ENTRIES",
]
