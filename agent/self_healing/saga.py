"""Saga 补偿事务 —— 高风险操作的可回滚执行单元（TASK-S4-03 步骤 3 / v7.2 §4.6）

【规则原文（§4.6）】
    prepare（前置快照 + 意图哈希入 journal）
      → execute（前后状态哈希入 journal）
      → confirm（结果哈希入 journal）
    失败路径：重放 compensating_action → 标记 aborted
            → 补偿也失败 → 升级 L4（快照恢复）+ 最高告警
    恢复顺序：先重放补偿 → 再处理未完成事务 → 一致性校验
    journal.log 每条：{saga_id, step, intent_hash, before_hash, after_hash, ts, trace_id}
    高风险操作（risk ≥ high）必须走 Saga；undo_hint 必须指向真实可执行命令。

【本模块解决什么】
    云枢此前对"做了一半的破坏性操作"没有统一语义：`skills_mgmt/rollback.py` 回版本、
    `p6_snapshot.py` 存状态、审批只记录"谁批了"，但**没有任何地方记录"这一步打算做什么、
    做完前后状态是什么、失败后该补偿什么"**。§4.6 的 journal 就是那张账。本模块把它落地。

【三条不变量（对应验收项）】
    1. **三态齐**：`prepare → execute → confirm` 每态各一条 journal，字段严格为 §4.6 的七元
       （`JOURNAL_FIELDS`）；`assert_entry_shape()` 是机器可读的形状断言。
    2. **补偿幂等可重放**：`compensate()` 先读 journal，**已成功补偿过的步骤不再执行**
       （重放安全）；同一 saga 重复调用返回同一结果，且 journal 不产生重复成功条目。
    3. **补偿失败必须升级**：补偿抛错 → 记 `compensate:<step>` 失败条目 → 升级 **L4**
       （事故卡 + 最高告警）；**绝不静默**。这条路径有独立用例。

【step 取值约定（七元字段不变，用前缀承载目标步骤）】
    `prepare` / `execute` / `confirm` / `abort` / `escalate`
    `compensate:<步骤名>`（补偿某一步）或 `compensate`（saga 级补偿）
    用 `step_kind()` 取前缀前的基类；这样七元 schema 与 §4.6 逐字一致，不额外加字段。

【不易】纯标准库 + `agent.self_healing.levels`；journal 路径可显式注入；无全局单例。
【变易】`SagaStep` 列表可增删；补偿函数可注入（`compensator=`），便于不同操作类型复用。
【简易】不做分布式协调、不做两阶段提交——单机 Saga，与 §4.6 的范围一致。
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from agent.self_healing.levels import (
    HealLevel,
    SagaRequiredError,
    emit_healing_triggered,
    raise_incident,
    record_healing_audit,
)

logger = logging.getLogger("agent.self_healing.saga")

# ════════════════════════════════════════════════════════════
#  常量
# ════════════════════════════════════════════════════════════

#: journal 七元（§4.6 逐字：{saga_id, step, intent_hash, before_hash, after_hash, ts, trace_id}）
JOURNAL_FIELDS: Tuple[str, ...] = (
    "saga_id", "step", "intent_hash", "before_hash", "after_hash", "ts", "trace_id",
)

#: 三态步骤名（§4.6 prepare/execute/confirm）
STEP_PREPARE = "prepare"
STEP_EXECUTE = "execute"
STEP_CONFIRM = "confirm"
STEP_ABORT = "abort"
STEP_ESCALATE = "escalate"
STEP_COMPENSATE = "compensate"

#: 三态完整性所需的步骤（验收项「prepare/execute/confirm 三态 + journal 字段齐」）
REQUIRED_STEPS: Tuple[str, ...] = (STEP_PREPARE, STEP_EXECUTE, STEP_CONFIRM)

#: 默认 journal 目录（`CP_SAGA_JOURNAL_DIR` 可覆盖；用例必须显式传路径）
DEFAULT_JOURNAL_DIR = os.path.join("data", "saga")
JOURNAL_FILENAME = "journal.log"
ENV_JOURNAL_DIR = "CP_SAGA_JOURNAL_DIR"

#: 哈希算法前缀（与 S2-02 审计链/本任务 release_bundle 同款）
HASH_ALGO = "sha256"

#: risk 序（与 `agent.descriptors.models` 的四级一致；本地副本避免 import 环）
_RISK_ORDER: Tuple[str, ...] = ("low", "medium", "high", "destructive")

#: 「必须走 Saga」的起始风险级（§4.6：risk ≥ high）
SAGA_REQUIRED_RISK = "high"

#: placeholder 识别（undo_hint 校验用）：这些值不算"真实可执行动作"
_PLACEHOLDER_RE = re.compile(
    r"^\s*(?:[-—–]+|n/?a|none|null|todo|tbd|待补|待定|待人工复核补齐|无|暂无|未实现)\s*$",
    re.IGNORECASE,
)

#: 「真实可执行动作」的锚点：机制标识符（dotted / snake_case）或已知命令动词
_ANCHOR_RE = re.compile(
    r"(?:"
    r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*"   # SkillRegistry.set_enabled
    r"|[a-z][a-z0-9]*_[a-z0-9_]+"                        # rollback_version / set_enabled
    r"|git\s+(?:revert|checkout|reset)"                  # git 回退
    r"|(?:snapshot|backup|restore|rollback|revert|disable|enable|kill|restart)\b"
    r"|人工(?:恢复|回滚|处理|介入)"
    r")"
)

#: journal 单写者纪律（同一路径仅一个 writer；§5.5）
_WRITER_LOCK = threading.Lock()
_WRITERS: Dict[str, str] = {}


class SagaError(Exception):
    """Saga 基类异常"""


class SagaStateError(SagaError):
    """Saga 状态机非法转移（如未 prepare 就 execute）"""


class JournalWriteError(SagaError):
    """journal 写入失败（**不吞**——账写不下去就不能继续做破坏性动作）"""


class SingleWriterViolationError(SagaError):
    """同一 journal 路径出现第二个 writer（§5.5 单写者纪律）"""


class UndoHintError(SagaError):
    """`undo_hint` 未指向真实可执行动作（§4.6；拒绝执行）"""


# ════════════════════════════════════════════════════════════
#  状态
# ════════════════════════════════════════════════════════════


class SagaState(str, enum.Enum):
    """Saga 生命周期状态（§4.6：三态 + 失败路径）"""

    INIT = "init"                 # 未 prepare
    PREPARED = "prepared"         # 前置快照 + 意图哈希已入 journal
    EXECUTED = "executed"         # execute 完成，前后状态哈希已入 journal
    CONFIRMED = "confirmed"       # confirm 完成，结果哈希已入 journal
    ABORTED = "aborted"           # 失败并已标记 aborted（进入补偿）
    COMPENSATED = "compensated"   # 补偿成功
    COMPENSATION_FAILED = "compensation_failed"  # 补偿失败但**调用方显式关闭了升级**
    ESCALATED = "escalated"       # 补偿失败 → 升级 L4


#: 由 journal 反推状态时，各步骤对应的状态（**最新一步说了算**）
_STEP_TO_STATE: Dict[str, "SagaState"] = {}   # 定义在 SagaState 之后回填（见下）


def step_kind(step: str) -> str:
    """取步骤基类（`compensate:foo` → `compensate`）"""
    return str(step or "").split(":", 1)[0]


def _ensure_step_state_map() -> None:
    """惰性回填 `_STEP_TO_STATE`（在 `SagaState` 定义之后；幂等）"""
    if _STEP_TO_STATE:
        return
    _STEP_TO_STATE.update({
        STEP_ESCALATE: SagaState.ESCALATED,
        STEP_CONFIRM: SagaState.CONFIRMED,
        STEP_ABORT: SagaState.ABORTED,
        STEP_EXECUTE: SagaState.EXECUTED,
        STEP_PREPARE: SagaState.PREPARED,
    })


# ════════════════════════════════════════════════════════════
#  journal
# ════════════════════════════════════════════════════════════


def hash_state(value: Any) -> str:
    """状态/结果 → 稳定哈希（None → 空串；不可序列化 → repr 兜底）"""
    if value is None:
        return ""
    try:
        material = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        material = repr(value)
    return f"{HASH_ALGO}:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def hash_intent(intent: Any) -> str:
    """意图 → 稳定哈希（与状态哈希同法；单独命名以自解释）"""
    return hash_state(intent)


def now_ts() -> str:
    """当前时刻 ISO-8601（带本地时区偏移 + 毫秒；与 events.now_ts 同口径）"""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


@dataclass
class JournalEntry:
    """一行 journal（**字段严格等于 §4.6 的七元**）"""

    saga_id: str
    step: str
    intent_hash: str = ""
    before_hash: str = ""
    after_hash: str = ""
    ts: str = field(default_factory=now_ts)
    trace_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """**只**输出七元（顺序即 §4.6 书写顺序）"""
        return {name: getattr(self, name) for name in JOURNAL_FIELDS}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "JournalEntry":
        return cls(**{name: str(data.get(name) or "") for name in JOURNAL_FIELDS})


def assert_entry_shape(entry: JournalEntry) -> None:
    """形状断言：恰好七元、无多余键（验收项「journal 字段齐」的机器可读证据）"""
    payload = entry.to_dict()
    if tuple(payload.keys()) != JOURNAL_FIELDS:
        raise SagaStateError(
            f"journal 条目字段非法: {tuple(payload.keys())} != {JOURNAL_FIELDS}"
        )
    if not payload["saga_id"] or not payload["step"] or not payload["ts"]:
        raise SagaStateError(f"journal 条目缺 saga_id/step/ts: {payload}")


def journal_dir(directory: Optional[str] = None) -> Path:
    """journal 目录（显式 > 环境变量 > 默认）"""
    raw = directory or os.environ.get(ENV_JOURNAL_DIR) or DEFAULT_JOURNAL_DIR
    return Path(raw)


def register_journal_writer(path: Any, owner: str = "") -> None:
    """登记 journal writer（同一路径仅一个 writer）"""
    key = str(Path(str(path)).resolve()) if str(path or "").strip() else ""
    with _WRITER_LOCK:
        current = _WRITERS.get(key)
        if current is not None and current != owner:
            raise SingleWriterViolationError(
                f"journal 路径已有 writer: {key} (owner={current})"
            )
        _WRITERS[key] = owner or "anonymous"


def release_journal_writer(path: Any, owner: str = "") -> None:
    """释放 writer 登记"""
    key = str(Path(str(path)).resolve()) if str(path or "").strip() else ""
    with _WRITER_LOCK:
        if _WRITERS.get(key) == (owner or "anonymous"):
            _WRITERS.pop(key, None)


def reset_journal_writers() -> None:
    """清空 writer 登记（用例隔离）"""
    with _WRITER_LOCK:
        _WRITERS.clear()


class SagaJournal:
    """追加式 journal（一行一条 JSON；**路径显式**）

    【不易】只追加、不修改、不删除：补偿的幂等性依赖"历史可重读"。
    【变易】可传 `path=`（单文件）或 `directory=`（目录 + journal.log）。
    """

    def __init__(self, path: Optional[Any] = None, *, directory: Optional[str] = None,
                 writer: str = "self_healing.saga") -> None:
        self._explicit_path = Path(str(path)) if path else None
        self._directory = directory
        self._writer = writer
        self._registered = False
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        if self._explicit_path is not None:
            return self._explicit_path
        return journal_dir(self._directory) / JOURNAL_FILENAME

    # ── 写 ──

    def append(self, entry: JournalEntry) -> JournalEntry:
        """追加一行（**写失败必须抛**——账写不下去不能继续做破坏性动作）"""
        assert_entry_shape(entry)
        with self._lock:
            try:
                if not self._registered:
                    register_journal_writer(self.path, self._writer)
                    self._registered = True
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(entry.to_json() + "\n")
            except SingleWriterViolationError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise JournalWriteError(
                    f"journal 写入失败 {self.path}: {type(exc).__name__}: {exc}"
                ) from exc
        return entry

    def log(self, saga_id: str, step: str, *, intent_hash: str = "",
            before_hash: str = "", after_hash: str = "",
            trace_id: str = "", ts: Optional[str] = None) -> JournalEntry:
        """便捷写入（§4.6 七元）"""
        return self.append(JournalEntry(
            saga_id=str(saga_id), step=str(step), intent_hash=str(intent_hash or ""),
            before_hash=str(before_hash or ""), after_hash=str(after_hash or ""),
            ts=str(ts or now_ts()), trace_id=str(trace_id or ""),
        ))

    # ── 读 ──

    def entries(self, saga_id: str = "") -> List[JournalEntry]:
        """读取 journal（可按 saga_id 过滤；损坏行跳过并告警）"""
        path = self.path
        if not path.exists():
            return []
        out: List[JournalEntry] = []
        want = str(saga_id or "")
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for lineno, line in enumerate(handle, 1):
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        entry = JournalEntry.from_dict(json.loads(text))
                    except Exception as exc:  # noqa: BLE001 单行损坏不影响其余
                        logger.warning("journal 第 %d 行解析失败，跳过: %s", lineno, exc)
                        continue
                    if want and entry.saga_id != want:
                        continue
                    out.append(entry)
        except Exception as exc:  # noqa: BLE001
            logger.warning("journal 读取失败 %s: %s", path, exc)
        return out

    def steps_of(self, saga_id: str) -> List[str]:
        """某 saga 已记录的步骤序列（按写入序）"""
        return [e.step for e in self.entries(saga_id)]

    def has_step(self, saga_id: str, step: str) -> bool:
        """是否已有某步骤条目"""
        return any(e.step == step for e in self.entries(saga_id))

    def last(self, saga_id: str, step: str = "") -> Optional[JournalEntry]:
        """最后一条（可按步骤过滤）"""
        rows = [e for e in self.entries(saga_id) if not step or e.step == step]
        return rows[-1] if rows else None

    def sagas(self) -> List[str]:
        """全部 saga_id（按首次出现序）"""
        seen: List[str] = []
        for entry in self.entries():
            if entry.saga_id not in seen:
                seen.append(entry.saga_id)
        return seen

    def is_three_phase_complete(self, saga_id: str) -> bool:
        """三态齐备（prepare/execute/confirm 各至少一条）"""
        steps = set(self.steps_of(saga_id))
        return all(s in steps for s in REQUIRED_STEPS)

    def incomplete_sagas(self) -> List[str]:
        """未完成事务（无 confirm 且无最终补偿/升级结论）— §4.6「再处理未完成事务」"""
        pending: List[str] = []
        for saga_id in self.sagas():
            steps = self.steps_of(saga_id)
            if STEP_CONFIRM in steps:
                continue
            if any(step_kind(s) == STEP_COMPENSATE for s in steps):
                continue
            if STEP_ESCALATE in steps:
                continue
            pending.append(saga_id)
        return pending

    def reset(self) -> None:
        """清空 journal 文件（**仅用例隔离用**；生产路径无此操作）"""
        path = self.path
        if path.exists():
            path.unlink()
        self._registered = False


# ════════════════════════════════════════════════════════════
#  高风险强制（risk ≥ high 必须走 Saga；undo_hint 必须真实可执行）
# ════════════════════════════════════════════════════════════


def risk_rank(risk: Any) -> int:
    """风险等级 → 序（未知/None → -1；与 descriptors 四级一致）"""
    if risk is None:
        return -1
    value = getattr(risk, "value", risk)
    text = str(value or "").strip().lower()
    return _RISK_ORDER.index(text) if text in _RISK_ORDER else -1


def is_saga_required(risk: Any) -> bool:
    """是否必须走 Saga（risk ≥ high，§4.6）"""
    return risk_rank(risk) >= risk_rank(SAGA_REQUIRED_RISK)


def extract_risk(descriptor: Any) -> Any:
    """从 descriptor / dict / 裸值中取 risk_level"""
    if descriptor is None:
        return None
    if isinstance(descriptor, Mapping):
        trust = descriptor.get("trust") or {}
        if isinstance(trust, Mapping) and trust.get("risk_level"):
            return trust.get("risk_level")
        return descriptor.get("risk_level")
    trust = getattr(descriptor, "trust", None)
    if trust is not None:
        return getattr(trust, "risk_level", None)
    return getattr(descriptor, "risk_level", None)


def extract_governance(descriptor: Any) -> Mapping[str, Any]:
    """从 descriptor / dict 中取 governance 字段组"""
    if descriptor is None:
        return {}
    if isinstance(descriptor, Mapping):
        gov = descriptor.get("governance") or {}
        return gov if isinstance(gov, Mapping) else {}
    gov = getattr(descriptor, "governance", None)
    if gov is None:
        return {}
    if isinstance(gov, Mapping):
        return gov
    return {
        "undo_hint": getattr(gov, "undo_hint", ""),
        "compensating_action": getattr(gov, "compensating_action", ""),
        "policy_ref": getattr(gov, "policy_ref", ""),
    }


def check_undo_hint(descriptor: Any) -> Dict[str, Any]:
    """校验 `undo_hint` 指向**真实可执行动作**（§4.6）

    判定（S1-02 回填口径：可信描述**引用真实机制**，不是空白也不是占位符）：
        1. 非空、非 placeholder（`-`/`N/A`/`待补`/`无` … 一律不过）；
        2. 至少含一个**可执行锚点**：机制标识符（`SkillRegistry.set_enabled` /
           `rollback_version`）、命令动词（`git revert` / `snapshot` / `rollback` …）
           或显式人工处置（`人工恢复`）。

    Returns:
        {ok, reason, anchors, text}——`ok=False` 时调用方必须拒绝执行。
    """
    gov = extract_governance(descriptor)
    text = str(gov.get("undo_hint") or "").strip()
    compensating = str(gov.get("compensating_action") or "").strip()
    if not text:
        return {"ok": False, "reason": "undo_hint 为空（§4.6：必须指向真实可执行命令）",
                "anchors": [], "text": text, "compensating_action": compensating}
    if _PLACEHOLDER_RE.match(text):
        return {"ok": False, "reason": f"undo_hint 是占位符 {text!r}，非真实可执行动作",
                "anchors": [], "text": text, "compensating_action": compensating}
    anchors = sorted(set(_ANCHOR_RE.findall(text)))
    if not anchors:
        return {"ok": False,
                "reason": "undo_hint 无可执行锚点（机制标识符/命令动词/人工处置）",
                "anchors": [], "text": text, "compensating_action": compensating}
    return {"ok": True, "reason": "", "anchors": anchors, "text": text,
            "compensating_action": compensating}


def require_saga(
    descriptor: Any,
    *,
    saga: Optional["Saga"] = None,
    operation: str = "",
    check_hint: bool = True,
) -> Optional["Saga"]:
    """**高风险强制 Saga 前置闸门**（§4.6：无强制即拒绝执行）

    在真正执行任何高副作用动作**之前**调用：
        - `risk < high` → 不强制，返回传入的 saga（可能为 None）；
        - `risk ≥ high` 且 `saga is None` → 抛 `SagaRequiredError`；
        - `risk ≥ high` 且 `check_hint` 且 `undo_hint` 不合格 → 抛 `UndoHintError`。

    Args:
        descriptor: ToolDescriptor / dict / 裸 risk 值。
        saga: 已 prepare 的 Saga 实例（调用方负责 prepare）。
        operation: 操作名（错误文案与审计用）。
        check_hint: 是否校验 undo_hint（默认校验）。

    Returns:
        传入的 saga（未强制时可能为 None）。

    Raises:
        SagaRequiredError: risk ≥ high 但未提供 Saga。
        UndoHintError: undo_hint 未指向真实可执行动作。
    """
    risk = extract_risk(descriptor)
    if not is_saga_required(risk):
        return saga
    name = operation or str(
        (descriptor.capability_id if hasattr(descriptor, "capability_id")
         else (descriptor or {}).get("capability_id") if isinstance(descriptor, Mapping) else "")
        or "unknown"
    )
    if saga is None:
        message = (
            f"高风险操作 {name}（risk={getattr(risk, 'value', risk)}）必须走 Saga 前置"
            f"（§4.6：risk ≥ high 必须 prepare + 意图哈希），当前未提供 Saga —— 拒绝执行"
        )
        record_healing_audit(
            HealLevel.L4, action="saga.required_denied",
            subject=f"capability:{name}",
            payload={"risk": str(getattr(risk, "value", risk)), "operation": name},
        )
        raise SagaRequiredError(message)
    if check_hint:
        verdict = check_undo_hint(descriptor)
        if not verdict["ok"]:
            record_healing_audit(
                HealLevel.L4, action="saga.undo_hint_denied",
                subject=f"capability:{name}",
                payload={"risk": str(getattr(risk, "value", risk)),
                         "reason": verdict["reason"]},
            )
            raise UndoHintError(f"高风险操作 {name} 的 {verdict['reason']} —— 拒绝执行")
    return saga


# ════════════════════════════════════════════════════════════
#  Saga
# ════════════════════════════════════════════════════════════


@dataclass
class SagaStep:
    """Saga 单步描述

    Attributes:
        name: 步骤名（journal 里出现在 `compensate:<name>` 中）。
        action: 动作标识（人类可读，如 `write_config`）。
        compensating_action: 补偿描述（来自 descriptor.governance.compensating_action）。
        compensator: **可执行**补偿回调（`() -> Any`）。None 表示该步无可自动补偿
            ——此时 `compensate()` 会把它记为"需人工"，并按"补偿失败"升级 L4
            （§4.6：补偿也失败 → 升级 L4；不可自动补偿不应被静默当作成功）。
    """

    name: str
    action: str = ""
    compensating_action: str = ""
    compensator: Optional[Callable[[], Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "action": self.action,
                "compensating_action": self.compensating_action,
                "has_compensator": self.compensator is not None}


@dataclass
class CompensationResult:
    """补偿结果（幂等重放的可查询形态）"""

    saga_id: str
    compensated: List[str] = field(default_factory=list)      # 本次真正执行的步骤
    skipped: List[str] = field(default_factory=list)          # 幂等跳过（已补偿过）
    failed: List[str] = field(default_factory=list)           # 补偿失败的步骤
    escalated: bool = False
    incident_id: str = ""
    state: str = ""

    @property
    def ok(self) -> bool:
        return not self.failed and not self.escalated

    def to_dict(self) -> Dict[str, Any]:
        return {"saga_id": self.saga_id, "compensated": list(self.compensated),
                "skipped": list(self.skipped), "failed": list(self.failed),
                "escalated": self.escalated, "incident_id": self.incident_id,
                "state": self.state, "ok": self.ok}


class Saga:
    """一次补偿事务（§4.6：prepare → execute → confirm；失败 → compensate → L4）

    Usage:
        saga = Saga(steps=[SagaStep("write_cfg", compensator=lambda: remove_cfg())],
                    journal=SagaJournal(path=tmp), trace_id="t-1")
        saga.prepare({"op": "write_cfg"}, snapshot={"cfg": "before"})
        saga.execute(lambda: do_write())
        saga.confirm()
        # 失败时：
        saga.abort("写入失败")
    """

    def __init__(
        self,
        *,
        saga_id: Optional[str] = None,
        journal: Optional[SagaJournal] = None,
        steps: Optional[Sequence[SagaStep]] = None,
        trace_id: str = "",
        tenant_id: str = "default",
        incidents_dir: Optional[str] = None,
        escalator: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> None:
        self.saga_id = str(saga_id or ("saga-" + uuid.uuid4().hex[:16]))
        self.journal = journal if journal is not None else SagaJournal()
        self.steps: List[SagaStep] = list(steps or [])
        self.trace_id = str(trace_id or "")
        self.tenant_id = str(tenant_id or "default")
        self._incidents_dir = incidents_dir
        self._escalator = escalator
        self._state = SagaState.INIT
        self._intent_hash = ""
        self._result_hash = ""
        self._lock = threading.RLock()

    # ── 状态 ──

    @property
    def state(self) -> SagaState:
        return self._state

    def state_from_journal(self) -> SagaState:
        """从 journal 重建状态（恢复路径用；**不依赖内存**）

        【判定口径：最新一步说了算（实现期修正）】
        第一版用固定优先级（escalate > confirm > compensate > …），导致
        "confirm 之后又被补偿"的 saga 反推为 `confirmed`——与内存状态、与补偿幂等键
        （`_compensated_steps`）都不一致。现改为**逆序扫描，取最后一条带状态的步骤**：
        状态是"截至此刻的结论"，后发生的事覆盖先发生的。这既符合 journal 的追加语义，
        也让内存态与磁盘态在任意时点可对齐。
        """
        _ensure_step_state_map()
        for entry in reversed(self.journal.entries(self.saga_id)):
            kind = step_kind(entry.step)
            if kind == STEP_COMPENSATE:
                # 补偿条目：成功（after_hash 非空）→ 已补偿；失败 → 补偿失败
                return (SagaState.COMPENSATED if entry.after_hash
                        else SagaState.COMPENSATION_FAILED)
            state = _STEP_TO_STATE.get(kind)
            if state is not None:
                return state
        return SagaState.INIT

    def _transition(self, to: SagaState, *, allowed: Iterable[SagaState]) -> None:
        """状态转移校验（非法转移抛 `SagaStateError`，不静默）"""
        if self._state not in tuple(allowed):
            raise SagaStateError(
                f"saga {self.saga_id} 非法状态转移: {self._state.value} → {to.value}"
                f"（允许自 {[s.value for s in allowed]}）"
            )
        self._state = to

    # ── prepare ──

    def prepare(self, intent: Any, *, snapshot: Any = None,
                before_state: Any = None) -> JournalEntry:
        """prepare：前置快照 + 意图哈希入 journal（§4.6 第一步）

        Args:
            intent: 意图（操作参数/目标），入 `intent_hash`。
            snapshot: 前置快照（§4.6「前置快照」）→ `before_hash`。
            before_state: 若已有状态快照对象，优先用它作 `before_hash`。

        Returns:
            写下的 journal 条目。
        """
        with self._lock:
            self._transition(SagaState.PREPARED,
                             allowed=(SagaState.INIT, SagaState.PREPARED))
            self._intent_hash = hash_intent(intent)
            before = hash_state(before_state if before_state is not None else snapshot)
            return self.journal.log(
                self.saga_id, STEP_PREPARE,
                intent_hash=self._intent_hash, before_hash=before,
                trace_id=self.trace_id,
            )

    # ── execute ──

    def execute(self, fn: Callable[[], Any], *,
                before_state: Any = None,
                state_fn: Optional[Callable[[Any], Any]] = None) -> Any:
        """execute：执行动作并把**前后状态哈希**入 journal（§4.6 第二步）

        Args:
            fn: 真正执行动作的回调。
            before_state: 显式前置状态（缺省用 prepare 的 before_hash 对应的状态不可得，
                故缺省写 `""` 之外的哈希由调用方保证——推荐传）。
            state_fn: 由返回值推导后置状态（缺省用返回值本身）。

        Returns:
            `fn()` 的返回值。

        Raises:
            任意 `fn()` 抛出的异常（**同时**写一条 execute 失败条目后原样上抛）。
        """
        with self._lock:
            self._transition(SagaState.EXECUTED,
                             allowed=(SagaState.PREPARED, SagaState.EXECUTED))
            before_hash = hash_state(before_state)
            if not before_hash:
                last_prepare = self.journal.last(self.saga_id, STEP_PREPARE)
                before_hash = last_prepare.before_hash if last_prepare else ""
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001 记失败条目后原样上抛
                self.journal.log(
                    self.saga_id, STEP_EXECUTE,
                    intent_hash=self._intent_hash, before_hash=before_hash,
                    after_hash="", trace_id=self.trace_id,
                )
                logger.error("saga %s execute 失败: %s: %s",
                             self.saga_id, type(exc).__name__, exc)
                raise
            after_state = state_fn(result) if state_fn is not None else result
            self._result_hash = hash_state(after_state)
            self.journal.log(
                self.saga_id, STEP_EXECUTE,
                intent_hash=self._intent_hash, before_hash=before_hash,
                after_hash=self._result_hash, trace_id=self.trace_id,
            )
        return result

    # ── confirm ──

    def confirm(self, *, result: Any = None) -> JournalEntry:
        """confirm：结果哈希入 journal（§4.6 第三步）"""
        with self._lock:
            self._transition(SagaState.CONFIRMED,
                             allowed=(SagaState.EXECUTED, SagaState.CONFIRMED))
            if result is not None:
                self._result_hash = hash_state(result)
            return self.journal.log(
                self.saga_id, STEP_CONFIRM,
                intent_hash=self._intent_hash, after_hash=self._result_hash,
                trace_id=self.trace_id,
            )

    # ── 失败路径 ──

    def abort(self, reason: str = "", *,
              escalate_on_compensation_failure: bool = True) -> CompensationResult:
        """失败路径：标记 aborted → 重放 compensating_action（§4.6）

        Returns:
            `CompensationResult`（`escalated=True` 表示补偿失败已升级 L4）。
        """
        with self._lock:
            self.journal.log(
                self.saga_id, STEP_ABORT,
                intent_hash=self._intent_hash, after_hash=hash_state({"reason": str(reason or "")}),
                trace_id=self.trace_id,
            )
            self._state = SagaState.ABORTED
        return self.compensate(reason=reason,
                               escalate_on_failure=escalate_on_compensation_failure)

    def compensate(self, *, reason: str = "",
                   escalate_on_failure: bool = True) -> CompensationResult:
        """重放补偿（**幂等可重放**：已成功补偿过的步骤不再执行）

        幂等依据：journal 中每个步骤至多一条**成功**的 `compensate:<step>`（`after_hash`
        非空）。重复调用时已成功的步骤进 `skipped`，不重复执行业务补偿。

        Args:
            reason: 补偿原因（写审计）。
            escalate_on_failure: 补偿失败是否升级 L4（默认是；§4.6 硬要求）。

        Returns:
            `CompensationResult`。
        """
        result = CompensationResult(saga_id=self.saga_id)
        done = self._compensated_steps()
        # 逆序补偿（后做的先撤）——Saga 语义的标准顺序
        for step in reversed(self.steps):
            if step.name in done:
                result.skipped.append(step.name)
                continue
            entry_step = f"{STEP_COMPENSATE}:{step.name}"
            if step.compensator is None:
                # 无可自动补偿 → 不静默当作成功；记失败条目
                self.journal.log(
                    self.saga_id, entry_step,
                    intent_hash=hash_state({"reason": str(reason or "")}),
                    before_hash="", after_hash="",
                    trace_id=self.trace_id,
                )
                result.failed.append(step.name)
                logger.warning("saga %s 步骤 %s 无可执行补偿（compensator=None）",
                               self.saga_id, step.name)
                continue
            try:
                outcome = step.compensator()
            except Exception as exc:  # noqa: BLE001
                self.journal.log(
                    self.saga_id, entry_step,
                    intent_hash=hash_state({"reason": str(reason or "")}),
                    before_hash="", after_hash="",
                    trace_id=self.trace_id,
                )
                result.failed.append(step.name)
                logger.error("saga %s 步骤 %s 补偿失败: %s: %s",
                             self.saga_id, step.name, type(exc).__name__, exc)
                continue
            self.journal.log(
                self.saga_id, entry_step,
                intent_hash=hash_state({"reason": str(reason or "")}),
                before_hash="", after_hash=hash_state(outcome if outcome is not None else {"ok": True}),
                trace_id=self.trace_id,
            )
            result.compensated.append(step.name)

        if result.failed and escalate_on_failure:
            incident_id = self._escalate_l4(result)
            result.escalated = True
            result.incident_id = incident_id
            result.state = SagaState.ESCALATED.value
            self._state = SagaState.ESCALATED
        elif result.failed:
            # 【实现期修正】调用方显式关闭升级时，状态必须是**另一个**值，
            # 否则"内存态说 escalated、journal 里没有 escalate 条目、也没有事故卡"
            # 三者互相矛盾。用 COMPENSATION_FAILED 如实表达"补偿失败但未升级"。
            result.state = SagaState.COMPENSATION_FAILED.value
            self._state = SagaState.COMPENSATION_FAILED
        else:
            result.state = SagaState.COMPENSATED.value
            self._state = SagaState.COMPENSATED
        record_healing_audit(
            HealLevel.L4 if result.failed else HealLevel.L2,
            action="saga.compensate",
            subject=f"saga:{self.saga_id}",
            payload=result.to_dict(),
            trace_id=self.trace_id,
        )
        return result

    def _compensated_steps(self) -> set:
        """journal 中已**成功**补偿的步骤名集合（幂等键）"""
        done = set()
        for entry in self.journal.entries(self.saga_id):
            if step_kind(entry.step) != STEP_COMPENSATE:
                continue
            if not entry.after_hash:
                continue  # 失败条目（after_hash 空）不算已补偿
            name = entry.step.split(":", 1)[1] if ":" in entry.step else ""
            if name:
                done.add(name)
        return done

    def _escalate_l4(self, result: CompensationResult) -> str:
        """补偿失败 → 升级 L4（快照恢复路径）+ 最高告警（§4.6「补偿也失败」）"""
        card = raise_incident(
            HealLevel.L4,
            signal="compensation_failed",
            root_cause=f"Saga {self.saga_id} 补偿失败，需快照恢复：{result.failed}",
            trace_ids=[self.trace_id] if self.trace_id else [],
            tenant_id=self.tenant_id,
            directory=self._incidents_dir,
            detail={"saga_id": self.saga_id, "failed_steps": list(result.failed),
                    "compensated_steps": list(result.compensated),
                    "intent_hash": self._intent_hash},
        )
        self.journal.log(
            self.saga_id, STEP_ESCALATE,
            intent_hash=self._intent_hash,
            after_hash=hash_state({"level": HealLevel.L4.value,
                                   "incident_id": card.incident_id,
                                   "failed": list(result.failed)}),
            trace_id=self.trace_id,
        )
        emit_healing_triggered(
            HealLevel.L4, signal="compensation_failed", tenant_id=self.tenant_id,
            incident_id=card.incident_id,
            extra={"saga_id": self.saga_id, "failed_steps": list(result.failed)},
        )
        if self._escalator is not None:
            try:
                self._escalator(card.to_dict())
            except Exception as exc:  # noqa: BLE001 外部升级回调失败不影响已升级事实
                logger.warning("saga 外部升级回调失败: %s: %s", type(exc).__name__, exc)
        return card.incident_id

    # ── 恢复 ──

    def recover(self) -> Dict[str, Any]:
        """恢复顺序（§4.6）：先重放补偿 → 再处理未完成事务 → 一致性校验

        【只补偿"未确认"的事务（实现期修正）】`confirm` 表示事务**已提交**；
        对已提交的事务重放补偿是语义错误（把已成功的操作撤掉），且会让
        `state_from_journal()` 从 `confirmed` 翻成 `compensated`。故 `recover()`
        仅对**未 confirm** 且无最终结论（补偿/升级）的 saga 执行补偿。

        Returns:
            {saga_id, state, compensation, skipped_compensation, incomplete,
             consistency, ok}
        """
        current = self.state_from_journal()
        terminal = {SagaState.CONFIRMED, SagaState.COMPENSATED,
                    SagaState.COMPENSATION_FAILED, SagaState.ESCALATED}
        skip_reason = ""
        if current in terminal:
            skip_reason = (f"当前状态 {current.value} 已是终态，跳过补偿"
                           f"（confirm 后不得撤销已提交事务）")
            compensation = None
        elif self.steps:
            compensation = self.compensate(reason="recover")
        else:
            compensation = None

        incomplete = self.journal.incomplete_sagas()
        final_state = self.state_from_journal()
        three_phase = self.journal.is_three_phase_complete(self.saga_id)
        consistency = {
            "three_phase_complete": three_phase,
            "state": final_state.value,
            "memory_state": self._state.value,
            "memory_matches_journal": self._state == final_state,
            # 一致 = 终态 且 内存态与 journal 推导态一致
            "ok": bool(final_state in terminal and self._state == final_state),
        }
        return {
            "saga_id": self.saga_id,
            "state": self._state.value,
            "compensation": compensation.to_dict() if compensation else None,
            "skipped_compensation": skip_reason,
            "incomplete": incomplete,
            "consistency": consistency,
            "ok": bool(consistency["ok"] and not (compensation and compensation.failed)),
        }


def make_saga_for(
    descriptor: Any,
    *,
    journal: Optional[SagaJournal] = None,
    steps: Optional[Sequence[SagaStep]] = None,
    trace_id: str = "",
    tenant_id: str = "default",
    incidents_dir: Optional[str] = None,
) -> Saga:
    """按 descriptor 建 Saga（自动带 `compensating_action` 描述 + 风险闸门）

    Raises:
        SagaRequiredError / UndoHintError: 见 `require_saga`。
    """
    gov = extract_governance(descriptor)
    name = str(
        (descriptor.capability_id if hasattr(descriptor, "capability_id")
         else (descriptor or {}).get("capability_id") if isinstance(descriptor, Mapping) else "")
        or "operation"
    )
    owned_steps = list(steps or [SagaStep(
        name=name, action=name,
        compensating_action=str(gov.get("compensating_action") or ""),
        compensator=None,
    )])
    saga = Saga(journal=journal, steps=owned_steps, trace_id=trace_id,
                tenant_id=tenant_id, incidents_dir=incidents_dir)
    require_saga(descriptor, saga=saga, operation=name)
    return saga


def reset_saga_state() -> None:
    """清空模块级 writer 登记（用例隔离）"""
    reset_journal_writers()


__all__ = [
    # 常量
    "JOURNAL_FIELDS", "REQUIRED_STEPS", "STEP_PREPARE", "STEP_EXECUTE", "STEP_CONFIRM",
    "STEP_ABORT", "STEP_ESCALATE", "STEP_COMPENSATE", "SAGA_REQUIRED_RISK",
    "DEFAULT_JOURNAL_DIR", "JOURNAL_FILENAME", "ENV_JOURNAL_DIR", "HASH_ALGO",
    # 异常
    "SagaError", "SagaStateError", "JournalWriteError",
    "SingleWriterViolationError", "UndoHintError",
    # 状态与条目
    "SagaState", "step_kind", "JournalEntry", "assert_entry_shape",
    "hash_state", "hash_intent", "now_ts",
    # journal
    "SagaJournal", "journal_dir", "register_journal_writer",
    "release_journal_writer", "reset_journal_writers",
    # 风险闸门
    "risk_rank", "is_saga_required", "extract_risk", "extract_governance",
    "check_undo_hint", "require_saga",
    # Saga
    "SagaStep", "CompensationResult", "Saga", "make_saga_for", "reset_saga_state",
]
