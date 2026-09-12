"""L1-L5 自愈语义层（TASK-S4-03 步骤 1 / v7.2 §4.4）

【本模块是什么】
    v7.2 §4.4 把自愈写成**五级升级链**（L1 重试→L2 降级→L3 重启/回退→L4 补偿→L5 租户回滚），
    而云枢既有自愈设施是**按故障域与动作**组织的（`agent/monitoring/self_healer.py` 的
    `HealAction`×`HealPolicy`、`agent/self_healing/policy.py` 的 `RESTORE_MAP`、
    `agent/graceful_degrade.py` / `agent/observability/model_degrade.py` 的降级链、
    `agent/p6_snapshot.py` 的快照、`agent/monitoring/alert_manager.py` 的告警）。
    本模块是两者之间的**声明式映射 + 升级判定 + 事故卡成型**，**不是第二套自愈栈**：
    它自己不重启服务、不清缓存、不跑命令——执行一律交给既有设施（见 `LevelSpec.legacy_facilities`）。

【术语纪律：两套「五层」不是一回事（本任务验收项「无同名不同义残留」）】
    健康探针五层（`agent/health/probes.py`）      l1_process / l2_dependency / l3_llm_tool / l4_business / l5_semantic
        —— 描述「**看哪里**」（可观测性分层，按业务重要性加权：0.25/0.20/0.25/0.20/0.10）。
    自愈升级五级（本模块）                        L1 / L2 / L3 / L4 / L5
        —— 描述「**做什么**」（故障升级链，按副作用强度与影响半径递增）。
    两者编号重合纯属巧合，语义正交：一次 L1（重试）可能要读 l3_llm_tool 探针，
    一次 L5（租户回滚）也可能由 l1_process 探针触发。`HEALTH_PROBE_LAYERS` 常量与
    `assert_no_level_confusion()` 把这条纪律变成机器可读的断言。

【不易（契约）】
    - 纯声明式：本模块不 import 任何执行型设施（不 import self_healer / snapshot / subprocess）；
      对外的执行建议只以**字符串动作名**表达，由调用方落到既有设施。
    - 只叠加不改写：新增本模块不改变 `SelfHealer`/`RESTORE_MAP`/告警阈值的任何既有行为。
    - 一切发射 best-effort：审计/事件发射失败绝不向上抛（沿用 S2 纪律）。
    - 可显式注入路径：事故卡落盘路径必须可传，用例不得污染真实数据目录（S3-02/S3-03 教训）。

【变易（扩展点）】
    - 新增级别：往 `LEVEL_SPECS` 加一项 + `LEVEL_ORDER` 追加（链尾）。
    - 新增触发信号：`resolve_level()` 的判定顺序表 `_TRIGGER_ORDER` 是数据，不是 if 链。

【简易】
    无第三方依赖；无全局可变状态（唯一全局是懒建的事件发射开关读取，无缓存）。
"""

from __future__ import annotations

import enum
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("agent.self_healing.levels")

# ════════════════════════════════════════════════════════════
#  常量
# ════════════════════════════════════════════════════════════

#: 事故卡落盘目录（`CP_HEALING_INCIDENTS_DIR` 可覆盖；用例必须显式传路径）
DEFAULT_INCIDENTS_DIR = os.path.join("data", "healing_incidents")
ENV_INCIDENTS_DIR = "CP_HEALING_INCIDENTS_DIR"
ENV_ENABLED = "CP_HEALING_LEVELS_ENABLED"

#: 事故卡文件名单写者纪律（同一路径仅一个 writer；与 S2-02/S2-03 同款）
_WRITER_LOCK = threading.Lock()
_WRITERS: Dict[str, str] = {}


class SingleWriterViolationError(RuntimeError):
    """同一事故卡文件路径出现第二个 writer（§5.5 单写者纪律）"""


class LevelError(Exception):
    """自愈语义层基类异常"""


class SagaRequiredError(LevelError):
    """risk ≥ high 的操作未走 Saga 前置（§4.6）——拒绝执行"""


# ════════════════════════════════════════════════════════════
#  L1-L5 枚举与规格
# ════════════════════════════════════════════════════════════


class HealLevel(str, enum.Enum):
    """v7.2 §4.4 自愈升级五级（**不是**健康探针五层，见模块 docstring）"""

    L1 = "L1"   # 重试≤2 → 降级 → 人工
    L2 = "L2"   # 劣化自动 downgrade
    L3 = "L3"   # kill → git revert → 重启 → 负面样本
    L4 = "L4"   # journal 补偿 → 快照
    L5 = "L5"   # 租户级回滚 + 最高告警


#: 升级链顺序（索引即级别；链尾不再升级）
LEVEL_ORDER: Tuple[HealLevel, ...] = (
    HealLevel.L1, HealLevel.L2, HealLevel.L3, HealLevel.L4, HealLevel.L5,
)

#: 健康探针五层（`agent/health/probes.py`）——与 HealLevel **语义正交**，此处仅用于断言纪律
HEALTH_PROBE_LAYERS: Tuple[str, ...] = (
    "l1_process", "l2_dependency", "l3_llm_tool", "l4_business", "l5_semantic",
)

#: 告警严重度（复用既有告警域的措辞；L5 = 最高告警）
SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_HIGH = "high"
SEVERITY_CRITICAL = "critical"

#: 影响半径（L5 是「租户级」，其余为进程/组件/整机）
SCOPE_PROCESS = "process"
SCOPE_COMPONENT = "component"
SCOPE_HOST = "host"
SCOPE_BUNDLE = "bundle"
SCOPE_TENANT = "tenant"


@dataclass(frozen=True)
class LevelSpec:
    """单级自愈的声明式规格（**数据，不是逻辑**）"""

    level: HealLevel
    code: str                      # 稳定代号（事件/审计用，避免只发 "L1"）
    title: str                     # 中文标题
    semantic: str                  # v7.2 §4.4 原文语义（逐字）
    trigger: str                   # 触发条件（声明式描述）
    actions: Tuple[str, ...]       # 动作名（**字符串**，由既有设施落实）
    legacy_facilities: Tuple[str, ...]  # 既有落点（模块::符号）
    scope: str                     # 影响半径
    alert_severity: str            # 告警严重度
    requires_approval: bool        # 是否必须人工审批后才可执行
    automated: bool                # 是否允许自动执行（五类永不自动化 → False）
    negative_sample: bool          # 是否要求产出负面样本（L3 起为真）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level.value,
            "code": self.code,
            "title": self.title,
            "semantic": self.semantic,
            "trigger": self.trigger,
            "actions": list(self.actions),
            "legacy_facilities": list(self.legacy_facilities),
            "scope": self.scope,
            "alert_severity": self.alert_severity,
            "requires_approval": self.requires_approval,
            "automated": self.automated,
            "negative_sample": self.negative_sample,
        }


#: 五级规格表（v7.2 §4.4 逐字语义 ↔ 云枢既有落点）
LEVEL_SPECS: Dict[HealLevel, LevelSpec] = {
    HealLevel.L1: LevelSpec(
        level=HealLevel.L1,
        code="retry_degrade_human",
        title="受限重试 → 降级 → 人工",
        semantic="L1 重试≤2→降级→人工",
        trigger="瞬态失败且未验证为持续性劣化（error_handler.category ∈ transient/timeout）",
        actions=("retry_limited", "degrade_llm_router", "escalate_to_human"),
        legacy_facilities=(
            "agent.self_healing.policy::RESTORE_MAP[llm_timeout].actions",
            "agent.monitoring.self_healer::SelfHealer.execute_action",
            "agent.observability.model_degrade::call_with_model_fallback",
            "agent.error_handler",
        ),
        scope=SCOPE_PROCESS,
        alert_severity=SEVERITY_INFO,
        requires_approval=False,
        automated=True,
        negative_sample=False,
    ),
    HealLevel.L2: LevelSpec(
        level=HealLevel.L2,
        code="auto_downgrade",
        title="劣化自动降级",
        semantic="L2 劣化自动 downgrade",
        trigger="同类失败达阈值或质量/延迟相对基线劣化（非单次瞬态）",
        actions=("auto_downgrade", "recover_circuit_breaker", "clear_cache"),
        legacy_facilities=(
            "agent.graceful_degrade::GracefulDegrade",
            "agent.observability.model_degrade::handle_primary_failure",
            "agent.skills_mgmt.rollback::AutoRollback.check_and_rollback",
            "agent.monitoring.self_healer::SelfHealer",
        ),
        scope=SCOPE_COMPONENT,
        alert_severity=SEVERITY_WARNING,
        requires_approval=False,
        automated=True,
        negative_sample=False,
    ),
    HealLevel.L3: LevelSpec(
        level=HealLevel.L3,
        code="kill_revert_restart",
        title="杀进程 → 回退代码 → 重启 → 负面样本",
        semantic="L3 kill→git revert→重启→负面样本",
        trigger="L2 降级后仍劣化，或验证失败连续达阈值（SelfHealer._verify_failure_counts）",
        actions=("kill_process", "git_revert", "restart_service", "record_negative_sample"),
        legacy_facilities=(
            "agent.monitoring.self_healer::SelfHealer._restart_service",
            "agent.monitoring.self_healer::SelfHealer.verify_action",
            "agent.evolution.defect_case::build_failure_case",
            "agent.monitoring.alert_manager::AlertManager",
        ),
        scope=SCOPE_HOST,
        alert_severity=SEVERITY_HIGH,
        requires_approval=False,
        automated=True,
        negative_sample=True,
    ),
    HealLevel.L4: LevelSpec(
        level=HealLevel.L4,
        code="journal_compensate_snapshot",
        title="journal 补偿 → 快照恢复",
        semantic="L4 journal 补偿→快照",
        trigger="Saga 补偿失败，或检测到整包回滚不一致（只回技能不回代码，P7.2-15）",
        actions=("compensate_saga", "restore_snapshot", "freeze_and_alert"),
        legacy_facilities=(
            "agent.self_healing.saga::Saga.compensate",
            "agent.self_healing.release_bundle::rollback_bundle",
            "agent.p6_snapshot::StateSnapshotManager",
            "agent.monitoring.alert_manager::AlertManager",
        ),
        scope=SCOPE_BUNDLE,
        alert_severity=SEVERITY_HIGH,
        requires_approval=False,
        automated=True,
        negative_sample=True,
    ),
    HealLevel.L5: LevelSpec(
        level=HealLevel.L5,
        code="tenant_rollback_max_alert",
        title="租户级回滚 + 最高告警",
        semantic="L5 租户级回滚+最高告警",
        trigger="影响面跨出租户边界，或 L4 快照恢复失败/事件涉及多租户数据一致性",
        actions=("tenant_scoped_rollback", "isolate_tenant", "raise_max_alert"),
        legacy_facilities=(
            "agent.self_healing.release_bundle::rollback_bundle",
            "agent.monitoring.alert_manager::AlertManager",
            "agent.audit.facade::audit.record",
        ),
        scope=SCOPE_TENANT,
        alert_severity=SEVERITY_CRITICAL,
        requires_approval=True,
        automated=False,
        negative_sample=True,
    ),
}


def assert_no_level_confusion() -> None:
    """纪律断言：自愈五级代号与健康探针五层不得互相冒充

    两套「五层」编号重合是历史事实（探针先命名）。本函数把「不许把
    `l1_process` 当成 L1 自愈级」写成可执行断言——验收项「无同名不同义残留」
    的机器可读证据。探针层名一律小写且带语义后缀，自愈级一律 `L<n>` 纯编号。
    """
    for level in LEVEL_ORDER:
        assert level.value in ("L1", "L2", "L3", "L4", "L5"), level
        assert level.value.lower() not in HEALTH_PROBE_LAYERS, level
    for probe in HEALTH_PROBE_LAYERS:
        assert probe not in {lv.value for lv in LEVEL_ORDER}, probe
        assert probe.islower(), probe


def level_of(value: Any) -> Optional[HealLevel]:
    """宽松解析级别（`"l1"` / `HealLevel.L1` / `"L1"` → HealLevel；未知返回 None）"""
    if isinstance(value, HealLevel):
        return value
    text = str(value or "").strip().upper()
    for level in LEVEL_ORDER:
        if level.value == text:
            return level
    return None


def spec_for(level: Any) -> Optional[LevelSpec]:
    """取级别规格（未知级别返回 None，不抛）"""
    parsed = level_of(level)
    return LEVEL_SPECS.get(parsed) if parsed else None


def next_level(level: Any) -> Optional[HealLevel]:
    """升级到下一级（链尾 L5 返回 None）"""
    parsed = level_of(level)
    if parsed is None:
        return None
    idx = LEVEL_ORDER.index(parsed)
    return LEVEL_ORDER[idx + 1] if idx + 1 < len(LEVEL_ORDER) else None


def escalate(level: Any, *, default: HealLevel = HealLevel.L1) -> HealLevel:
    """升级（链尾保持 L5）——L4/L5 是「补偿失败」与「跨租户」的落点，只进不退。

    【未知输入的处理（实现期修正，勿改回）】第一版对未知/None 输入返回 **L5**，
    那与本模块"宁可少做也不越级执行重动作"的原则相悖——L5 = 租户级回滚 + 最高告警，
    是整条链上最重的动作；因解析失败而升级到最重动作是危险的失败模式。
    现与 `resolve_level()` 同口径：**未知输入告警并回退到最轻一级**（`default=L1`）。
    链尾（L5）无处可升，保持 L5。
    """
    parsed = level_of(level)
    if parsed is None:
        logger.warning("escalate 收到未知级别 %r → 回退 %s（不越级升级到重动作）",
                       level, default.value)
        return default
    return next_level(parsed) or parsed


# ════════════════════════════════════════════════════════════
#  触发判定
# ════════════════════════════════════════════════════════════

#: 触发信号 → 级别（**顺序敏感：靠前优先**，即「越严重的信号优先命中」）
#: 这是数据表而非 if 链——新增信号只需插入一项。
_TRIGGER_ORDER: Tuple[Tuple[str, HealLevel], ...] = (
    ("tenant_scope_breach", HealLevel.L5),
    ("snapshot_restore_failed", HealLevel.L5),
    ("partial_rollback", HealLevel.L4),
    ("compensation_failed", HealLevel.L4),
    ("saga_compensated", HealLevel.L4),
    ("verified_failure", HealLevel.L3),
    ("restart_required", HealLevel.L3),
    ("sustained_degradation", HealLevel.L2),
    ("quality_regression", HealLevel.L2),
    ("transient_failure", HealLevel.L1),
)

#: 已登记的触发信号（供调用方自检；未知信号退回 L1 并告警）
TRIGGER_SIGNALS: Tuple[str, ...] = tuple(sig for sig, _ in _TRIGGER_ORDER)


def resolve_level(signal: str, *, default: HealLevel = HealLevel.L1) -> HealLevel:
    """按触发信号解析自愈级别

    Args:
        signal: 触发信号名（见 `TRIGGER_SIGNALS`）。
        default: 未知信号时的兜底级别（默认 L1——**从最轻一级开始**，
            宁可少做也不越级执行重动作）。

    Returns:
        HealLevel。

    Notes:
        未知信号只告警不抛：自愈链是故障路径，解析失败不得成为新的故障源。
    """
    text = str(signal or "").strip().lower()
    for sig, level in _TRIGGER_ORDER:
        if sig == text:
            return level
    if text:
        logger.warning("未知自愈触发信号 %r → 兜底 %s", text, default.value)
    return default


# ════════════════════════════════════════════════════════════
#  事故卡（§3 IncidentCard：六要素齐才可 resolved）
# ════════════════════════════════════════════════════════════

#: §3 事故卡六要素中的「齐备」判定字段（YAML 形态的六个必填语义项）
#: 注：id/severity/status/created_at 是卡片骨架字段，不计入「六要素」。
INCIDENT_ELEMENTS: Tuple[str, ...] = (
    "root_cause", "fatal_change", "evasion_rule",
    "in_strategy_memory", "regression_case_added", "trace_ids",
)

STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"


@dataclass
class IncidentCard:
    """自愈事故卡（v7.2 §3 IncidentCard；S6-01「自愈事故」面板与 §8.6 恢复向导的数据源）

    【不易】`is_resolvable()` = 六要素齐备。缺要素的卡片**不得**转 `resolved`——
    这是「不可追溯的 97% 比没有数字更危险」（§7 UI 五坑⑤）的机制化。
    """

    severity: HealLevel
    root_cause: str = ""
    fatal_change: str = ""                       # 致命变更 commit
    evasion_rule: str = ""                       # 规避规则（policy_id|memory_id）
    in_strategy_memory: Optional[Dict[str, Any]] = None   # {yes, id}
    regression_case_added: Optional[Dict[str, Any]] = None  # {yes, case_id}
    trace_ids: List[str] = field(default_factory=list)
    mttd_ms: Optional[float] = None
    mttr_ms: Optional[float] = None
    status: str = STATUS_OPEN
    incident_id: str = field(default_factory=lambda: "inc-" + uuid.uuid4().hex[:12])
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    resolved_at: str = ""
    tenant_id: str = "default"
    detail: Dict[str, Any] = field(default_factory=dict)

    # ── 六要素判定 ──

    def missing_elements(self) -> List[str]:
        """返回缺失的六要素名（空列表 = 齐备）"""
        missing: List[str] = []
        if not str(self.root_cause or "").strip():
            missing.append("root_cause")
        if not str(self.fatal_change or "").strip():
            missing.append("fatal_change")
        if not str(self.evasion_rule or "").strip():
            missing.append("evasion_rule")
        if not _present_flag(self.in_strategy_memory):
            missing.append("in_strategy_memory")
        if not _present_flag(self.regression_case_added):
            missing.append("regression_case_added")
        if not [t for t in (self.trace_ids or []) if str(t or "").strip()]:
            missing.append("trace_ids")
        return missing

    def is_resolvable(self) -> bool:
        """六要素齐备才可 resolved（§3「六要素齐才可 resolved」）"""
        return not self.missing_elements()

    def resolve(self) -> "IncidentCard":
        """转 resolved（六要素不齐则抛 `LevelError`，绝不静默降级）"""
        missing = self.missing_elements()
        if missing:
            raise LevelError(
                f"事故卡 {self.incident_id} 缺要素，不得 resolved: {', '.join(missing)}"
            )
        self.status = STATUS_RESOLVED
        self.resolved_at = datetime.now().isoformat()
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.incident_id,
            "severity": self.severity.value,
            "root_cause": self.root_cause,
            "fatal_change": self.fatal_change,
            "evasion_rule": self.evasion_rule,
            "in_strategy_memory": self.in_strategy_memory or {"yes": False, "id": ""},
            "regression_case_added": self.regression_case_added or {"yes": False, "case_id": ""},
            "trace_ids": list(self.trace_ids or []),
            "mttd_ms": self.mttd_ms,
            "mttr_ms": self.mttr_ms,
            "status": self.status,
            "tenant_id": self.tenant_id,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "missing_elements": self.missing_elements(),
            "detail": dict(self.detail or {}),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IncidentCard":
        """从字典恢复（缺省字段回退默认；severity 非法回退 L1）"""
        payload = dict(data or {})
        return cls(
            severity=level_of(payload.get("severity")) or HealLevel.L1,
            root_cause=str(payload.get("root_cause") or ""),
            fatal_change=str(payload.get("fatal_change") or ""),
            evasion_rule=str(payload.get("evasion_rule") or ""),
            in_strategy_memory=payload.get("in_strategy_memory"),
            regression_case_added=payload.get("regression_case_added"),
            trace_ids=list(payload.get("trace_ids") or []),
            mttd_ms=payload.get("mttd_ms"),
            mttr_ms=payload.get("mttr_ms"),
            status=str(payload.get("status") or STATUS_OPEN),
            incident_id=str(payload.get("id") or ("inc-" + uuid.uuid4().hex[:12])),
            created_at=str(payload.get("created_at") or datetime.now().isoformat()),
            resolved_at=str(payload.get("resolved_at") or ""),
            tenant_id=str(payload.get("tenant_id") or "default"),
            detail=dict(payload.get("detail") or {}),
        )


def _present_flag(value: Any) -> bool:
    """要素存在性判定：支持 {yes: bool, ...} 形态与裸真值/非空串"""
    if isinstance(value, dict):
        return bool(value.get("yes")) or bool(value.get("id")) or bool(value.get("case_id"))
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


# ════════════════════════════════════════════════════════════
#  事故卡归档（单写者 + 显式路径）
# ════════════════════════════════════════════════════════════


def incidents_dir(directory: Optional[str] = None) -> Path:
    """事故卡目录（显式 > 环境变量 > 默认）"""
    raw = directory or os.environ.get(ENV_INCIDENTS_DIR) or DEFAULT_INCIDENTS_DIR
    return Path(raw)


def incident_path(incident_id: str, *, directory: Optional[str] = None) -> Path:
    """单个事故卡的路径"""
    return incidents_dir(directory) / f"{incident_id}.json"


def register_incident_writer(path: Any, owner: str = "") -> None:
    """登记事故卡 writer（同一路径仅一个 writer；§5.5 单写者纪律）"""
    key = str(Path(str(path)).resolve()) if str(path or "").strip() else ""
    with _WRITER_LOCK:
        current = _WRITERS.get(key)
        if current is not None and current != owner:
            raise SingleWriterViolationError(
                f"事故卡路径已有 writer: {key} (owner={current})"
            )
        _WRITERS[key] = owner or "anonymous"


def release_incident_writer(path: Any, owner: str = "") -> None:
    """释放 writer 登记（owner 不匹配则不动，防误释放他人）"""
    key = str(Path(str(path)).resolve()) if str(path or "").strip() else ""
    with _WRITER_LOCK:
        if _WRITERS.get(key) == (owner or "anonymous"):
            _WRITERS.pop(key, None)


def reset_incident_writers() -> None:
    """清空 writer 登记（用例隔离用）"""
    with _WRITER_LOCK:
        _WRITERS.clear()


def active_incident_writers() -> Dict[str, str]:
    """当前 writer 快照（诊断用）"""
    with _WRITER_LOCK:
        return dict(_WRITERS)


def save_incident(card: IncidentCard, *, directory: Optional[str] = None,
                  writer: str = "self_healing.levels") -> Path:
    """落盘事故卡（原子写；**best-effort**，失败只告警不抛）

    【返回值的语义（如实说明）】返回的是**目标路径**，不是"写入成功"的证明——
    本函数按 best-effort 设计（归档失败不得成为新的故障源），写失败时只告警。
    需要确认落盘请用 `load_incident()` 复读；或直接看返回值路径是否存在。
    单写者冲突（`SingleWriterViolationError`）**会**上抛——那是配置错误，不是瞬时故障。
    """
    path = incident_path(card.incident_id, directory=directory)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        register_incident_writer(path, writer)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(card.to_dict(), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(path)
    except SingleWriterViolationError:
        raise
    except Exception as exc:  # noqa: BLE001 归档失败不得成为新故障源
        logger.warning("事故卡落盘失败（不影响自愈主路径）: %s: %s", type(exc).__name__, exc)
    return path


def load_incident(incident_id: str, *, directory: Optional[str] = None) -> Optional[IncidentCard]:
    """读取事故卡（不存在/损坏返回 None）"""
    path = incident_path(incident_id, directory=directory)
    try:
        if not path.exists():
            return None
        return IncidentCard.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001
        logger.warning("事故卡读取失败 %s: %s", path, exc)
        return None


def list_incidents(*, directory: Optional[str] = None,
                   severity: Any = None, status: str = "") -> List[IncidentCard]:
    """列出事故卡（可按 severity/status 过滤；损坏文件跳过）"""
    base = incidents_dir(directory)
    if not base.exists():
        return []
    want_level = level_of(severity) if severity else None
    out: List[IncidentCard] = []
    for path in sorted(base.glob("inc-*.json")):
        card = load_incident(path.stem, directory=directory)
        if card is None:
            continue
        if want_level is not None and card.severity != want_level:
            continue
        if status and card.status != status:
            continue
        out.append(card)
    return out


# ════════════════════════════════════════════════════════════
#  事件与审计发射（best-effort；喂 S5-02 MTTD/MTTR 契约与 S6-01 面板）
# ════════════════════════════════════════════════════════════


def _levels_enabled() -> bool:
    """总开关（默认开：本层是**只读语义映射 + 事件发射**，开启不改既有行为）"""
    return str(os.environ.get(ENV_ENABLED, "1")).strip().lower() \
        not in ("0", "false", "no", "off")


def emit_healing_triggered(
    level: Any,
    *,
    signal: str = "",
    mttd_ms: Optional[float] = None,
    mttr_ms: Optional[float] = None,
    tenant_id: str = "default",
    subject: str = "",
    incident_id: str = "",
    extra: Optional[Dict[str, Any]] = None,
    actor: str = "auto",
    idempotency_key: str = "",
    trace_id: str = "",
) -> Optional[Any]:
    """发射 `healing.triggered`（§3.6 八事件之一）

    【为什么必须由本任务发射】S5-02 已定义数据源契约：MTTD/MTTR 取自
    `healing.triggered` 的 `mttd_ms`/`mttr_ms` 字段（`agent/eval/metrics.py::
    compute_healing_latency`，目标 MTTD <3s / MTTR <30s）。此前**无发射方**，
    指标只能出 `framework_only`；本函数即该发射方。

    【载荷纪律】只放叶子字段（级别/代号/信号/耗时/租户），不放原始用户文本。

    Returns:
        EventEnvelope（成功）或 None（事件层关闭/失败）；**绝不抛**。
    """
    if not _levels_enabled():
        return None
    parsed = level_of(level)
    if parsed is None:
        logger.warning("healing.triggered 级别非法: %r", level)
        return None
    spec = LEVEL_SPECS[parsed]
    payload: Dict[str, Any] = {
        "level": parsed.value,
        "level_code": spec.code,
        "level_title": spec.title,
        "signal": str(signal or ""),
        "scope": spec.scope,
        "severity": spec.alert_severity,
        "automated": spec.automated,
        "requires_approval": spec.requires_approval,
        # MTTD/MTTR 契约字段（S5-02）：无观测值时为 None → 该条不参与中位数
        "mttd_ms": float(mttd_ms) if mttd_ms is not None else None,
        "mttr_ms": float(mttr_ms) if mttr_ms is not None else None,
        "tenant_id": str(tenant_id or "default"),
        "incident_id": str(incident_id or ""),
    }
    if extra:
        payload.update({str(k): v for k, v in extra.items()})
    try:
        from agent.observability.events import EV_HEALING_TRIGGERED, emit
        return emit(
            EV_HEALING_TRIGGERED, payload,
            actor=str(actor or "auto"),
            idempotency_key=idempotency_key or (
                f"healing:{parsed.value}:{signal}:{incident_id or subject}:{tenant_id}"),
        )
    except Exception as exc:  # noqa: BLE001 发射失败不得阻断自愈
        logger.warning("healing.triggered 发射失败: %s: %s", type(exc).__name__, exc)
        return None


def record_healing_audit(
    level: Any,
    *,
    action: str = "healing.triggered",
    subject: str = "",
    payload: Optional[Dict[str, Any]] = None,
    trace_id: str = "",
) -> Optional[Any]:
    """写一条自愈审计（`agent.audit.facade::audit.record`；best-effort）"""
    if not _levels_enabled():
        return None
    parsed = level_of(level)
    if parsed is None:
        return None
    spec = LEVEL_SPECS[parsed]
    body: Dict[str, Any] = {
        "level": parsed.value,
        "level_code": spec.code,
        "scope": spec.scope,
        "severity": spec.alert_severity,
    }
    if payload:
        body.update({str(k): v for k, v in payload.items()})
    try:
        from agent.audit.facade import audit
        return audit.record(action, actor="self_healing.levels",
                            subject=str(subject or f"heal:{parsed.value}"),
                            payload=body, trace_id=trace_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("自愈审计写入失败: %s: %s", type(exc).__name__, exc)
        return None


def raise_incident(
    level: Any,
    *,
    signal: str = "",
    root_cause: str = "",
    fatal_change: str = "",
    trace_ids: Optional[Sequence[str]] = None,
    tenant_id: str = "default",
    directory: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    mttd_ms: Optional[float] = None,
    started_at: Optional[float] = None,
) -> IncidentCard:
    """开一张事故卡（落盘 + 审计 + 事件三件套）

    L5 会额外发最高告警（`severity=critical`）——「L5 租户级回滚+最高告警」的落点。

    Args:
        started_at: 故障发现时刻（perf_counter 或 time.time 均可，仅用于算 MTTR）。
    """
    parsed = level_of(level) or HealLevel.L1
    mttr = None
    if started_at is not None:
        mttr = max(0.0, (time.perf_counter() - float(started_at)) * 1000.0)
    card = IncidentCard(
        severity=parsed,
        root_cause=root_cause,
        fatal_change=fatal_change,
        trace_ids=list(trace_ids or []),
        tenant_id=str(tenant_id or "default"),
        mttd_ms=mttd_ms,
        mttr_ms=mttr,
        detail=dict(detail or {}),
    )
    save_incident(card, directory=directory)
    record_healing_audit(
        parsed, action="healing.incident",
        subject=f"incident:{card.incident_id}",
        payload={"signal": signal, "incident_id": card.incident_id,
                 "missing_elements": card.missing_elements()},
    )
    emit_healing_triggered(
        parsed, signal=signal, mttd_ms=mttd_ms, mttr_ms=mttr,
        tenant_id=card.tenant_id, incident_id=card.incident_id, extra=detail,
    )
    if parsed == HealLevel.L5:
        _raise_max_alert(card, signal=signal)
    return card


def _raise_max_alert(card: IncidentCard, *, signal: str = "") -> bool:
    """L5 最高告警（走既有 `AlertManager.escalate` → critical 通知 + 人工接管条目）

    【为什么复用 escalate 而不是新发一条告警】既有 `AlertManager.escalate()` 已经把
    「critical 通知 + 人工接管入队（TakeoverRecord）」做全了（任务 7 的失败升级路径
    也走它）。L5 的「最高告警」在云枢里的正确落点就是这条**升级**语义，而不是并行
    造一条新的告警规则——否则同一事故会产生两套互不相关的人工接管记录。

    【构造细节】`escalate` 在 `from_level == to_level` 时返回 None（不重复升级），
    故先以 WARNING 建 Alert，再升级到 CRITICAL——与 `_on_heal_escalated` 同款做法。

    **best-effort**：告警通道失败绝不阻断回滚本身。
    """
    try:
        from agent.monitoring.alert_evaluator import Alert, AlertSeverity, AlertState
        from agent.monitoring.alert_manager import get_alert_manager

        alert = Alert(
            name=f"healing.{card.severity.value.lower()}:{signal or 'tenant_rollback'}",
            state=AlertState.FIRING,
            severity=AlertSeverity.WARNING,
            value=0.0,
            threshold=0.0,
            condition=f"heal_level_{card.severity.value.lower()}",
            message=f"[{card.incident_id}] 租户级自愈升级 {card.severity.value}"
                    f"（signal={signal or '-'}）",
            labels={"heal_level": card.severity.value,
                    "incident_id": card.incident_id,
                    "tenant_id": card.tenant_id},
            annotations={"detail": str(_compact_detail(card))},
        )
        takeover = get_alert_manager().escalate(
            alert, AlertSeverity.CRITICAL,
            reason=f"L5 租户级回滚：{signal or 'tenant_rollback'}",
            evidence={"incident_id": card.incident_id, "signal": signal,
                      "tenant_id": card.tenant_id},
        )
        return takeover is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("L5 最高告警发送失败（不影响回滚）: %s: %s", type(exc).__name__, exc)
        return False


def _compact_detail(card: IncidentCard) -> Dict[str, Any]:
    """告警 annotations 用的紧凑叶子（**不放原始用户文本**）"""
    return {
        "incident_id": card.incident_id,
        "level": card.severity.value,
        "tenant_id": card.tenant_id,
        "missing_elements": card.missing_elements(),
        "trace_ids": list(card.trace_ids or [])[:10],
    }


# ════════════════════════════════════════════════════════════
#  映射表导出（验收交付物「自愈语义映射表」的机器可读形态）
# ════════════════════════════════════════════════════════════


def mapping_table() -> List[Dict[str, Any]]:
    """自愈语义映射表（v7.2 L1-L5 ↔ 云枢既有层级 ↔ 缺口）

    Returns:
        五个 dict，字段：level / v7.2 语义 / 触发 / 云枢落点 / 影响半径 /
        告警级别 / 自动执行 / 需审批 / 负面样本 / 缺口（gap）。
    """
    gaps = {
        "L1": "既有 `retry_limited`/`degrade_llm_router` 在 RESTORE_MAP 中标 `unimplemented`；"
              "本任务只做**语义对齐与升级判定**，不代实现该两动作（仍由既有 SKIPPED 语义承接）。",
        "L2": "既有 AutoRollback 面向 skill 版本；模型/组件降级分别由 graceful_degrade 与 "
              "model_degrade 承担，二者此前无统一「L2」口径——本任务补齐口径，不合并实现。",
        "L3": "既有 SelfHealer 已有 restart/verify 与失败升级回调；「git revert」与「负面样本」"
              "此前分散（skills_mgmt.rollback / evolution.defect_case），本任务只做链接与命名。",
        "L4": "此前**完全缺失**：无 Saga journal、无整包回滚原子单位 → 本任务新增 saga.py 与 "
              "release_bundle.py（唯一实做级）。",
        "L5": "此前**完全缺失**：无租户级回滚入口与最高告警分级 → 本任务新增（集群化留 P5，"
              "P7.2-16 明示）。",
    }
    rows: List[Dict[str, Any]] = []
    for level in LEVEL_ORDER:
        spec = LEVEL_SPECS[level]
        row = spec.to_dict()
        row["v7.2_semantic"] = spec.semantic
        row["gap"] = gaps.get(level.value, "")
        rows.append(row)
    return rows


def render_mapping_markdown() -> str:
    """自愈语义映射表（Markdown 表；供交付文档直接粘贴）"""
    lines = [
        "| v7.2 级别 | 语义（§4.4 逐字） | 云枢既有落点 | 触发 | 影响半径 | 告警 | 自动化 | 审批 | 负面样本 | 缺口 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in mapping_table():
        lines.append(
            "| **{level}** {title} | {sem} | {fac} | {trig} | {scope} | {sev} | {auto} | {appr} | {neg} | {gap} |".format(
                level=row["level"], title=row["title"], sem=row["semantic"],
                fac="<br>".join(f"`{f}`" for f in row["legacy_facilities"]),
                trig=row["trigger"], scope=row["scope"], sev=row["alert_severity"],
                auto="是" if row["automated"] else "**否**",
                appr="是" if row["requires_approval"] else "否",
                neg="是" if row["negative_sample"] else "否",
                gap=row["gap"],
            )
        )
    return "\n".join(lines)


def reset_levels_state() -> None:
    """清空模块级状态（用例隔离；writer 登记是唯一可变全局）"""
    reset_incident_writers()


__all__ = [
    # 枚举与常量
    "HealLevel", "LEVEL_ORDER", "LEVEL_SPECS", "LevelSpec", "HEALTH_PROBE_LAYERS",
    "SEVERITY_INFO", "SEVERITY_WARNING", "SEVERITY_HIGH", "SEVERITY_CRITICAL",
    "SCOPE_PROCESS", "SCOPE_COMPONENT", "SCOPE_HOST", "SCOPE_BUNDLE", "SCOPE_TENANT",
    "STATUS_OPEN", "STATUS_RESOLVED", "INCIDENT_ELEMENTS",
    # 异常
    "LevelError", "SagaRequiredError", "SingleWriterViolationError",
    # 级别解析
    "level_of", "spec_for", "next_level", "escalate", "resolve_level",
    "TRIGGER_SIGNALS", "assert_no_level_confusion",
    # 事故卡
    "IncidentCard", "save_incident", "load_incident", "list_incidents",
    "incidents_dir", "incident_path", "raise_incident",
    # 单写者
    "register_incident_writer", "release_incident_writer", "reset_incident_writers",
    "active_incident_writers",
    # 发射
    "emit_healing_triggered", "record_healing_audit",
    # 映射表
    "mapping_table", "render_mapping_markdown", "reset_levels_state",
]
