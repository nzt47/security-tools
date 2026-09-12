"""EquivalenceCase 判定集资产（TASK-S3-02 步骤 1 / v7.2 §3.1 · §4.5 · P7.2-23）

**判定集是独立资产**（T1 修正落地）：每个候选内化能力持 30–100 组等价用例
（输入 → 期望输出/副作用集合）。用例一旦生成即**复制数据脱离 Trace 生命周期**，
只保留 ``origin_trace_id`` 作为溯源指针 —— 故 S2-01 统一台账的 90 天保留策略
**不影响**判定集可用性（§3.1 硬约束）。存储因此落在**独立目录**
（``data/digestion/cases/``），与 ``data/traces/`` 无任何共享文件。

三条用例生成通道（任务书步骤 1）：

| 通道 | 入口 | provenance.kind |
|---|---|---|
| ① Seed Pack 预置（P7.2-23：≥12 技能 × ≥3 组） | `seed_cases_for()` / `load_seed_pack()` | ``seed_pack`` |
| ② 从统一台账自动生成（复用 S3-01 清洗口径） | `cases_from_trace_set()` / `trace_set_for()` | ``trace`` |
| ③ LLM 生成 + 人工抽检 | `EquivalenceCase(kind=CASE_KIND_LLM)` + `merge_cases()` | ``llm`` |

**与 S3-01 的口径复用**（任务书 §零，勿重复实现）：

- 轨迹来源与同类分组：`models.Trajectory` / `TraceSet` + `cleaning.group_by_same_task`；
- 参数形态归一（路径/时间戳/随机值 → 形态占位符）：`generalize.normalize_param_value`
  —— 判定集的"副作用集合"比对因此**跨具体路径**可比（记形态不记原文，与 S2 载荷纪律一致）；
- 门槛常量**不引入第三套**：``MIN_CASE_SET_SIZE = 30`` 对应 §3.1 的 30–100 组区间，
  与 S3-01 的 `MIN_PATTERN_STEPS = 2`（成形）/ `MIN_ASCENSION_STEPS = 3`（可升格）
  语义正交，不覆盖也不复制它们。

**import 纪律**：与 S3-01 同包（``agent.digestion``），重依赖（`DigestionService`）
一律函数体内懒加载，故 ``import agent.digestion.cases`` 无文件/DB/网络副作用。
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from . import capability as capability_module
from .case_cost import record_case_build_from_case_set
from .generalize import (
    is_placeholder,
    normalize_param_value,
    shape_placeholder,
    slot_placeholder,
)
from .models import (
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    CandidatePattern,
    TraceSet,
    Trajectory,
)

logger = logging.getLogger("agent.digestion.cases")

# ════════════════════════════════════════════════════════════
#  常量（门槛/口径单点定义）
# ════════════════════════════════════════════════════════════

#: 用例来源通道
CASE_KIND_SEED = "seed"
CASE_KIND_TRACE = "trace"
CASE_KIND_LLM = "llm"
CASE_KIND_MANUAL = "manual"
CASE_KINDS: Tuple[str, ...] = (CASE_KIND_SEED, CASE_KIND_TRACE,
                               CASE_KIND_LLM, CASE_KIND_MANUAL)
#: provenance.kind 取值（与 kind 区分：provenance 记录"资产从哪来"，含测试复用）
PROV_SEED_PACK = "seed_pack"
PROV_TRACE = "trace"
PROV_REPO_SAMPLE = "repo_sample"
PROV_LLM_DRAFT = "llm_draft"

#: §3.1 判定集规模区间（每个候选内化能力 30–100 组）
MIN_CASE_SET_SIZE = 30
MAX_CASE_SET_SIZE = 100
#: P7.2-23 Seed Pack 起步下限（≥12 技能 × ≥3 组）
SEED_PACK_MIN_SKILLS = 12
MIN_SEED_CASES_PER_SKILL = 3

#: 用例生命周期
CASE_SCHEMA_VERSION = 1
MAX_STORE_HISTORY = 5

#: 期望状态取值 —— 采用**回放观测**的终态词汇（success/error/blocked/配额/未绑定/拒绝），
#: 另设 ``any``：负样本轨迹派生的用例**不主张**程序级终态（观测到的失败是任务级判定，
#: 不能反推为步骤程序的终态），此时如实标注为"不主张"，而不是编一个期望值去凑绿。
EXPECTED_STATUS_ANY = "any"
EXPECTED_STATUSES: Tuple[str, ...] = (
    OUTCOME_SUCCESS, "error", "blocked", "quota_exceeded", "unbound_input",
    "denied", EXPECTED_STATUS_ANY,
)

#: 副作用三类（与 `models.TrajectoryStep` / `mining.side_effect_profile` 同词汇）
SIDE_EFFECT_KINDS: Tuple[str, ...] = ("files_written", "files_deleted",
                                      "external_calls")
#: 副作用期望的证据来源（区分"确无副作用"与"台账未记录"，不静默）
SIDE_EFFECT_SOURCE_AUTHORED = "authored"
SIDE_EFFECT_SOURCE_TRACE = "trace"
SIDE_EFFECT_SOURCE_NONE = "none"
SIDE_EFFECT_SOURCES: Tuple[str, ...] = (SIDE_EFFECT_SOURCE_AUTHORED,
                                       SIDE_EFFECT_SOURCE_TRACE,
                                       SIDE_EFFECT_SOURCE_NONE)

# ════════════════════════════════════════════════════════════
#  用例 ↔ 候选 适用性（TASK-S3-03 / M4 收口）
# ════════════════════════════════════════════════════════════
#
# S3-02 曾以 ``case.active`` + ``notes`` **临时表达**"该用例描述单次读取契约、
# 与被评三段任务链形状不同 ⇒ 本次不纳入评估"。该表达有两个缺陷：
#   ① 不可机检（要读人写的 notes 才知道为何排除）；
#   ② 判定集重生成后**无处重新施加**（active 会被新生成的用例覆盖）。
# 故本任务引入**显式字段** `EquivalenceCase.applicability`：候选用**稳定词表**声明
# 适用/不适用，判定集重生成后可经 `apply_applicability()` **机器重新施加**。
#
# 词表与 `gate._candidate_kind()` 逐值同源（不改 gate 的公开行为，只是同词）：
CANDIDATE_KIND_SEED_NATIVE = "seed_pack_native"     # Seed Pack 候选骨架
CANDIDATE_KIND_PATTERN = "candidate_pattern"        # S3-01 候选模式（CandidatePattern）
CANDIDATE_KIND_IMPLEMENTATION = "implementation"    # 显式实现对象（含 name 细分）
CANDIDATE_KIND_PROVIDER = "provider"                # 按用例取实现的 callable
CANDIDATE_KIND_EXPLICIT = "explicit"                # 其他显式候选
CANDIDATE_KINDS: Tuple[str, ...] = (
    CANDIDATE_KIND_SEED_NATIVE, CANDIDATE_KIND_PATTERN,
    CANDIDATE_KIND_IMPLEMENTATION, CANDIDATE_KIND_PROVIDER,
    CANDIDATE_KIND_EXPLICIT,
)
#: ``implementation:<name>`` 这类细粒度标签的分隔符
CANDIDATE_KIND_SEP = ":"

#: 存储位置（**独立于** `data/traces/`；Trace 90 天过期不影响判定集）
DEFAULT_CASE_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "digestion", "cases")
CASE_ROOT_ENV = "CP_DIGESTION_CASE_DIR"
CASE_BACKEND_ENV = "CP_DIGESTION_CASE_BACKEND"
BACKEND_JSON = "json"
BACKEND_SQLITE = "sqlite"
BACKENDS: Tuple[str, ...] = (BACKEND_JSON, BACKEND_SQLITE)

#: 沙箱虚拟根（**不是真实路径语义**）：用例的所有读写都必须落在该前缀内，
#: 出界即 `sandbox.SandboxEscapeError`（"绝不双写真实环境"的第一道闸）
DEFAULT_SANDBOX_ROOT = "C:/sandbox"


class CaseError(Exception):
    """判定集基类异常"""


class CaseValidationError(CaseError):
    """用例/判定集不满足不变量（不静默）"""


# ════════════════════════════════════════════════════════════
#  小工具
# ════════════════════════════════════════════════════════════


def _now() -> float:
    return time.time()


def _canonical(value: Any) -> str:
    """稳定 JSON 串（哈希/比较用；不可序列化时退回 str）"""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        return str(value)


#: 公开别名（同包其他模块复用同一序列化口径，不各自实现一套）
canonical_json = _canonical


def slug_of(capability_id: str) -> str:
    """capability_id → 文件/ID 安全 slug（保留点号，便于人工辨识）"""
    text = str(capability_id or "").strip()
    safe = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in text)
    return safe or "unknown"


def case_id_for(capability_id: str, *, kind: str, index: int,
                origin_trace_id: str = "") -> str:
    """确定性 case_id（同一来源恒同一 id ⇒ 重生成不产生重复用例）"""
    material = f"{capability_id}|{kind}|{index}|{origin_trace_id}"
    return "case_" + hashlib.sha1(material.encode("utf-8")).hexdigest()[:12]


def _short_hash(text: str, length: int = 8) -> str:
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:length]


# ════════════════════════════════════════════════════════════
#  程序步（上游实现 / 候选原生的共同载体）
# ════════════════════════════════════════════════════════════


@dataclass
class ProgramStep:
    """一次能力调用（"上游实现"与"候选原生实现"都用它表达）

    ``params`` 可为字面量，也可为 ``${name}`` / ``${path}`` 形态占位符
    （方言沿用 S3-01：``generalize.SLOT_TEMPLATE``），回放前经
    `sandbox.bind_program()` 绑定到用例输入。
    """

    label: str
    params: Dict[str, Any] = field(default_factory=dict)
    capability_id: str = ""
    condition: str = ""

    def to_storage_dict(self) -> Dict[str, Any]:
        return {"label": self.label, "params": dict(self.params),
                "capability_id": self.capability_id, "condition": self.condition}

    @classmethod
    def from_storage_dict(cls, data: Any) -> "ProgramStep":
        if isinstance(data, str):
            return cls(label=data)
        if not isinstance(data, dict):
            raise CaseValidationError([f"步骤不是 dict/str: {type(data).__name__}"])
        return cls(label=str(data.get("label") or data.get("capability_id") or ""),
                   params=dict(data.get("params") or {}),
                   capability_id=str(data.get("capability_id") or ""),
                   condition=str(data.get("condition") or ""))


def program_from_storage(data: Any) -> List[ProgramStep]:
    if not data:
        return []
    if not isinstance(data, (list, tuple)):
        raise CaseValidationError(["upstream 必须是步骤列表"])
    return [ProgramStep.from_storage_dict(item) for item in data]


def program_to_storage(steps: Sequence[ProgramStep]) -> List[Dict[str, Any]]:
    return [s.to_storage_dict() for s in steps]


# ════════════════════════════════════════════════════════════
#  CaseApplicability（M4：显式的用例 ↔ 候选适用性）
# ════════════════════════════════════════════════════════════


@dataclass
class CaseApplicability:
    """一条用例对**哪些候选**适用（显式、可机检、可重新施加）

    语义（与 `active` 正交，二者不可互相冒充）：

    - 空对象 = **不限**（对任何候选都适用）—— 判定集默认状态；
    - ``include_kinds`` 非空 ⇒ 白名单：只对列出的候选类别适用；
    - ``exclude_kinds`` ⇒ 黑名单：对列出的候选类别不适用（在白名单命中后仍会否决）；
    - ``reason`` 必填才有可审计性：排除要有理由（对应 S3-02 曾写进 ``notes`` 的那句话）。

    类别取值见 `CANDIDATE_KINDS`；``implementation:<name>`` 这类细粒度标签同时匹配
    其**基类**（``implementation``）与本标签。
    """

    include_kinds: List[str] = field(default_factory=list)
    exclude_kinds: List[str] = field(default_factory=list)
    reason: str = ""
    declared_by: str = ""
    declared_at: float = 0.0

    def __post_init__(self) -> None:
        self.include_kinds = _normalize_kinds(self.include_kinds)
        self.exclude_kinds = _normalize_kinds(self.exclude_kinds)
        self.reason = str(self.reason or "")
        self.declared_by = str(self.declared_by or "")

    # ── 判定 ────────────────────────────────────────────────

    @property
    def restricted(self) -> bool:
        """是否**有约束**（空对象 = 不限；据 this 决定是否落盘）"""
        return bool(self.include_kinds or self.exclude_kinds)

    def applies_to(self, candidate_kind: str) -> bool:
        """该候选类别是否适用（白名单未命中或黑名单命中 ⇒ 不适用）"""
        kind = normalize_candidate_kind(candidate_kind)
        if any(candidate_kind_matches(k, kind) for k in self.exclude_kinds):
            return False
        if not self.include_kinds:
            return True
        return any(candidate_kind_matches(k, kind) for k in self.include_kinds)

    def explain(self, candidate_kind: str) -> str:
        """人类可读的判定依据（进报告与灰度台账，不静默）"""
        kind = normalize_candidate_kind(candidate_kind)
        if any(candidate_kind_matches(k, kind) for k in self.exclude_kinds):
            return (f"候选 {kind} 在排除清单 {self.exclude_kinds}"
                    f"（理由：{self.reason or '未填'}）")
        if self.include_kinds and not any(
                candidate_kind_matches(k, kind) for k in self.include_kinds):
            return (f"候选 {kind} 不在适用清单 {self.include_kinds}"
                    f"（理由：{self.reason or '未填'}）")
        return f"候选 {kind} 适用（白名单 {self.include_kinds or '不限'}）"

    # ── 序列化 ──────────────────────────────────────────────

    def to_storage_dict(self) -> Dict[str, Any]:
        return {"include_kinds": list(self.include_kinds),
                "exclude_kinds": list(self.exclude_kinds),
                "reason": self.reason, "declared_by": self.declared_by,
                "declared_at": self.declared_at}

    @classmethod
    def from_storage_dict(cls, data: Any) -> "CaseApplicability":
        if data is None:
            return cls()
        if isinstance(data, CaseApplicability):
            return data
        if not isinstance(data, dict):
            raise CaseValidationError(
                [f"applicability 不是 dict: {type(data).__name__}"])
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise CaseValidationError(
                [f"applicability 含未知字段（拒绝静默丢弃）: {sorted(unknown)}"])
        return cls(**{k: v for k, v in data.items()})


def _normalize_kinds(raw: Any) -> List[str]:
    """候选类别列表归一（去空、去重、保序；非法项剔除并告警，不静默）"""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    out: List[str] = []
    for item in raw:
        kind = normalize_candidate_kind(item)
        if not kind:
            logger.warning("适用性候选类别为空值，已忽略: %r", item)
            continue
        if kind not in out:
            out.append(kind)
    return out


def normalize_candidate_kind(target: Any) -> str:
    """候选 → 稳定类别标签（字符串原样归一，其他对象按类别推断）

    与 `gate._candidate_kind()` **同词表**：``seed_pack_native`` /
    ``candidate_pattern`` / ``implementation`` / ``provider`` / ``explicit``；
    细粒度标签（``implementation:upstream``）保留，匹配时同时命中其基类。
    """
    if target is None:
        return CANDIDATE_KIND_SEED_NATIVE
    if isinstance(target, str):
        return target.strip()
    name = getattr(target, "name", None)
    if isinstance(target, CandidatePattern):
        return CANDIDATE_KIND_PATTERN
    if callable(target):
        return CANDIDATE_KIND_PROVIDER
    if name:
        return f"{CANDIDATE_KIND_IMPLEMENTATION}{CANDIDATE_KIND_SEP}{name}"
    return CANDIDATE_KIND_EXPLICIT


def candidate_kind_matches(declared: str, candidate_kind: str) -> bool:
    """类别匹配规则（**基类标签**匹配其全部细粒度标签；两个不同细粒度标签不互相匹配）

    - ``implementation`` ↔ ``implementation:upstream``：匹配（声明基类 = 对该类全部实现）；
    - ``implementation:upstream`` ↔ ``implementation:candidate``：**不**匹配
      （两个不同的具体实现不是同一个候选）。
    """
    left = str(declared or "").strip()
    right = normalize_candidate_kind(candidate_kind)
    if not left or not right:
        return False
    if left == right:
        return True
    left_detailed = CANDIDATE_KIND_SEP in left
    right_detailed = CANDIDATE_KIND_SEP in right
    if left_detailed and right_detailed:
        return False
    return (left.split(CANDIDATE_KIND_SEP)[0]
            == right.split(CANDIDATE_KIND_SEP)[0])


# ════════════════════════════════════════════════════════════
#  EquivalenceCase
# ════════════════════════════════════════════════════════════


@dataclass
class EquivalenceCase:
    """一组等价用例：``输入 → 期望输出/副作用集合``（§3.1 / §4.5）

    字段语义（避免与 S3-01 模型混淆）：

    - ``input``：目标能力的**参数**（具体值；由 Seed 手写或由轨迹形态占位符合成）；
    - ``upstream``：**上游/现有实现**的步骤程序（录制或手写），沙箱双跑的左臂；
    - ``expected_output``：**具体值**期望（确定性目标；非空则按值比对）；
    - ``expected_output_schema``：**结构**期望（``{"键": "类型"}``；非确定性目标用）；
    - ``expected_side_effects``：三类副作用**目标集合**（写/删/外部调用）；
    - ``origin_trace_id``：Trace 生成通道的溯源指针（**唯一**与台账的关联，
      数据本体已复制脱离 Trace 生命周期）。
    """

    case_id: str
    capability_id: str
    input: Dict[str, Any] = field(default_factory=dict)
    upstream: List[ProgramStep] = field(default_factory=list)
    #: 本用例的**候选原生实现**骨架（可选覆盖；为空则用能力级 native_template）
    native: List[ProgramStep] = field(default_factory=list)
    expected_output: Dict[str, Any] = field(default_factory=dict)
    expected_output_schema: Dict[str, str] = field(default_factory=dict)
    expected_side_effects: Dict[str, List[str]] = field(default_factory=dict)
    expected_status: str = OUTCOME_SUCCESS
    kind: str = CASE_KIND_SEED
    origin_trace_id: str = ""
    origin_task_id: str = ""
    intent_key: str = ""
    fixtures: Dict[str, str] = field(default_factory=dict)
    bindings: Dict[str, Any] = field(default_factory=dict)
    sandbox_root: str = DEFAULT_SANDBOX_ROOT
    destructive: bool = False
    branch_tags: List[str] = field(default_factory=list)
    #: 副作用期望的**证据来源**：``authored``（人工/Seed 手写，必比对）|
    #: ``trace``（台账记录，必比对）| ``none``（台账**未记录**副作用 ⇒ 用例
    #: 不主张"无副作用"，契约比对跳过，但双跑一致性硬性比对始终执行）
    side_effects_source: str = "authored"
    provenance: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    active: bool = True
    title: str = ""
    notes: str = ""
    #: **显式**用例 ↔ 候选适用性（M4；空对象 = 不限 —— 与 `active` 正交：
    #: `active` 表达"这条用例本身是否生效"，`applicability` 表达"对**哪个候选**适用"）
    applicability: CaseApplicability = field(default_factory=CaseApplicability)

    # ── 规范化 ──────────────────────────────────────────────

    def __post_init__(self) -> None:
        self.capability_id = str(self.capability_id or "")
        self.kind = str(self.kind or CASE_KIND_SEED)
        if not self.case_id:
            self.case_id = case_id_for(self.capability_id, kind=self.kind, index=0,
                                       origin_trace_id=self.origin_trace_id)
        if not self.created_at:
            self.created_at = _now()
        self.expected_side_effects = _normalize_side_effects(
            self.expected_side_effects)
        self.provenance = dict(self.provenance or {})
        self.provenance.setdefault("kind", PROV_SEED_PACK if self.kind == CASE_KIND_SEED
                                   else self.kind)
        if self.applicability is None:
            self.applicability = CaseApplicability()
        elif isinstance(self.applicability, dict):
            self.applicability = CaseApplicability.from_storage_dict(self.applicability)

    # ── 派生视图 ────────────────────────────────────────────

    @property
    def labels(self) -> List[str]:
        """上游步骤标签序列（分支条件求值/展示用）"""
        return [s.label for s in self.upstream]

    @property
    def step_count(self) -> int:
        return len(self.upstream)

    @property
    def expected_output_kind(self) -> str:
        """期望输出形态：``value``（具体值）/ ``schema``（结构）/ ``none``"""
        if self.expected_output:
            return "value"
        if self.expected_output_schema:
            return "schema"
        return "none"

    def side_effect_set(self) -> Dict[str, List[str]]:
        """副作用**形态归一后**的集合视图（跨具体路径可比；与 S2 载荷纪律一致）"""
        out: Dict[str, List[str]] = {}
        for kind in SIDE_EFFECT_KINDS:
            values = [str(normalize_param_value(v)) for v in
                      (self.expected_side_effects.get(kind) or [])]
            out[kind] = sorted(set(values))
        return out

    def input_fingerprint(self) -> str:
        """输入指纹（判定"同一输入"；record-and-replay 与去重共用）"""
        material = _canonical({
            "case_id": self.case_id,
            "capability_id": self.capability_id,
            "input": self.input,
            "bindings": self.bindings,
            "sandbox_root": self.sandbox_root,
            "fixtures": sorted(self.fixtures),
        })
        return _short_hash(material, 16)

    def upstream_fingerprint(self) -> str:
        """上游实现的结构指纹（步骤标签 + 参数键形态 + 条件）——漂移重探比对基准"""
        material = _canonical([
            {"label": s.label, "keys": sorted(str(k) for k in (s.params or {})),
             "condition": s.condition, "capability_id": s.capability_id}
            for s in self.upstream
        ])
        return _short_hash(material, 16)

    # ── 适用性（M4）────────────────────────────────────────

    def applies_to(self, candidate_kind: Any) -> bool:
        """该用例是否适用于给定候选（``applies_to("candidate_pattern")``）"""
        return self.applicability.applies_to(candidate_kind)

    def applicability_reason(self, candidate_kind: Any) -> str:
        return self.applicability.explain(candidate_kind)

    # ── 校验 ────────────────────────────────────────────────

    def validate(self) -> List[str]:
        """不变量校验 → 违规理由列表（空列表 = 合法）"""
        reasons: List[str] = []
        if not self.case_id:
            reasons.append("case_id 为空")
        if not self.capability_id:
            reasons.append("capability_id 为空")
        if not isinstance(self.input, dict):
            reasons.append("input 必须是 dict")
        if not self.upstream:
            reasons.append("upstream 至少需要 1 步（否则无法双跑）")
        if self.kind not in CASE_KINDS:
            reasons.append(f"kind {self.kind!r} 不在 {CASE_KINDS}")
        if self.expected_status not in EXPECTED_STATUSES:
            reasons.append(f"expected_status {self.expected_status!r} 不在 {EXPECTED_STATUSES}")
        if self.kind == CASE_KIND_TRACE and not self.origin_trace_id:
            reasons.append("trace 通道用例必须带 origin_trace_id（§3.1 溯源要求）")
        for kind in self.expected_side_effects:
            if kind not in SIDE_EFFECT_KINDS:
                reasons.append(f"副作用类型 {kind!r} 不在 {SIDE_EFFECT_KINDS}")
        if self.side_effects_source not in SIDE_EFFECT_SOURCES:
            reasons.append(f"side_effects_source {self.side_effects_source!r} "
                           f"不在 {SIDE_EFFECT_SOURCES}")
        if not isinstance(self.expected_output_schema, dict):
            reasons.append("expected_output_schema 必须是 dict")
        if not isinstance(self.sandbox_root, str) or not self.sandbox_root:
            reasons.append("sandbox_root 必须是非空字符串")
        if not isinstance(self.applicability, CaseApplicability):
            reasons.append("applicability 必须是 CaseApplicability")
        elif self.applicability.restricted and not self.applicability.reason:
            reasons.append("声明了适用性约束但未给出 reason（排除必须可审计）")
        return reasons

    def require_valid(self) -> "EquivalenceCase":
        reasons = self.validate()
        if reasons:
            raise CaseValidationError(reasons)
        return self

    # ── 序列化 ──────────────────────────────────────────────

    def to_storage_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "case_id": self.case_id,
            "capability_id": self.capability_id,
            "title": self.title,
            "input": dict(self.input),
            "upstream": program_to_storage(self.upstream),
            "native": program_to_storage(self.native),
            "expected_output": dict(self.expected_output),
            "expected_output_schema": dict(self.expected_output_schema),
            "expected_side_effects": {k: list(v) for k, v in
                                      self.expected_side_effects.items()},
            "expected_status": self.expected_status,
            "kind": self.kind,
            "origin_trace_id": self.origin_trace_id,
            "origin_task_id": self.origin_task_id,
            "intent_key": self.intent_key,
            "fixtures": dict(self.fixtures),
            "bindings": dict(self.bindings),
            "sandbox_root": self.sandbox_root,
            "destructive": bool(self.destructive),
            "branch_tags": list(self.branch_tags),
            "side_effects_source": self.side_effects_source,
            "provenance": dict(self.provenance),
            "created_at": self.created_at,
            "active": bool(self.active),
            "notes": self.notes,
        }
        # 适用性：**未声明约束时不落盘**（既有判定集存储字节不变；一旦声明即持久化，
        # 重生成后可经 apply_applicability() 重新施加 —— M4 的核心诉求）
        if self.applicability.restricted:
            payload["applicability"] = self.applicability.to_storage_dict()
        return payload

    @classmethod
    def from_storage_dict(cls, data: Dict[str, Any]) -> "EquivalenceCase":
        if not isinstance(data, dict):
            raise CaseValidationError(["用例不是 dict"])
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise CaseValidationError(
                [f"用例含未知字段（拒绝静默丢弃）: {sorted(unknown)}"])
        payload = dict(data)
        payload["upstream"] = program_from_storage(payload.get("upstream"))
        payload["native"] = program_from_storage(payload.get("native"))
        payload["applicability"] = CaseApplicability.from_storage_dict(
            payload.get("applicability"))
        return cls(**payload)


def _normalize_side_effects(raw: Any) -> Dict[str, List[str]]:
    """副作用 → 三类固定键的字符串列表（未知键剔除并告警，不静默）"""
    out: Dict[str, List[str]] = {k: [] for k in SIDE_EFFECT_KINDS}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if key not in SIDE_EFFECT_KINDS:
            logger.debug("忽略未知副作用类型: %s", key)
            continue
        if isinstance(value, str):
            out[key] = [value]
        elif isinstance(value, (list, tuple)):
            out[key] = [str(v) for v in value]
        elif value:
            out[key] = [str(value)]
    return out


# ════════════════════════════════════════════════════════════
#  CaseSet
# ════════════════════════════════════════════════════════════


@dataclass
class CaseSet:
    """一个能力的判定集（带版本；**可失效重生成**）"""

    capability_id: str
    version: int = 1
    cases: List[EquivalenceCase] = field(default_factory=list)
    upstream_version: str = ""
    upstream_schema: Dict[str, Any] = field(default_factory=dict)
    active: bool = True
    drifted: bool = False
    drift_reason: str = ""
    drifted_at: float = 0.0
    drift_details: Dict[str, Any] = field(default_factory=dict)
    regeneration_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    last_probed_at: float = 0.0

    def __post_init__(self) -> None:
        self.capability_id = str(self.capability_id or "")
        self.version = int(self.version or 1)
        now = _now()
        self.created_at = self.created_at or now
        self.updated_at = self.updated_at or self.created_at

    # ── 视图 ────────────────────────────────────────────────

    def active_cases(self) -> List[EquivalenceCase]:
        """生效用例（确定性顺序：case_id 升序）"""
        return sorted([c for c in self.cases if c.active],
                      key=lambda c: c.case_id)

    @property
    def size(self) -> int:
        return len(self.cases)

    def kind_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for case in self.cases:
            counts[case.kind] = counts.get(case.kind, 0) + 1
        return counts

    def size_verdict(self) -> Dict[str, Any]:
        """规模裁定（§3.1 的 30–100 区间；不足即如实标注为 *起步集*）"""
        active = len(self.active_cases())
        total = self.size
        reasons: List[str] = []
        if active < MIN_CASE_SET_SIZE:
            reasons.append(
                f"生效用例 {active} 组 < §3.1 下限 {MIN_CASE_SET_SIZE} 组"
                f"（当前为 Seed Pack 起步集，未达判定集资产规模）")
        if active > MAX_CASE_SET_SIZE:
            reasons.append(
                f"生效用例 {active} 组 > §3.1 上限 {MAX_CASE_SET_SIZE} 组"
                f"（应采样收敛，避免判定集无界膨胀）")
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "active": active,
            "total": total,
            "min": MIN_CASE_SET_SIZE,
            "max": MAX_CASE_SET_SIZE,
            "complete": not reasons,
            "starter": active < MIN_CASE_SET_SIZE,
            "reasons": reasons,
            "kinds": self.kind_counts(),
        }

    def add(self, case: EquivalenceCase) -> bool:
        """加入用例（按 case_id 去重；返回是否新增）"""
        if any(c.case_id == case.case_id for c in self.cases):
            return False
        self.cases.append(case)
        self.updated_at = _now()
        return True

    def by_id(self, case_id: str) -> Optional[EquivalenceCase]:
        for case in self.cases:
            if case.case_id == case_id:
                return case
        return None

    def invalidate(self, reason: str, *, details: Optional[Dict[str, Any]] = None,
                   now: float = 0.0) -> None:
        """标记失效（漂移：判定集不再代表上游契约）——不清空用例，保留证据"""
        self.active = False
        self.drifted = True
        self.drift_reason = str(reason or "")
        self.drifted_at = float(now or _now())
        self.drift_details = dict(details or {})
        self.updated_at = self.drifted_at
        for case in self.cases:
            case.active = False

    def upstream_fingerprint(self) -> str:
        """上游结构指纹（全部用例的上游程序联合指纹）"""
        material = _canonical(sorted(
            (c.case_id, c.upstream_fingerprint()) for c in self.cases))
        return _short_hash(material, 16)

    def validate(self) -> List[str]:
        reasons: List[str] = []
        if not self.capability_id:
            reasons.append("capability_id 为空")
        if self.version < 1:
            reasons.append("version 必须 ≥1")
        seen: Dict[str, int] = {}
        for case in self.cases:
            case_reasons = case.validate()
            reasons.extend(f"{case.case_id}: {r}" for r in case_reasons)
            seen[case.case_id] = seen.get(case.case_id, 0) + 1
            if case.capability_id and case.capability_id != self.capability_id:
                reasons.append(
                    f"{case.case_id}: capability_id {case.capability_id!r} "
                    f"与判定集 {self.capability_id!r} 不一致")
        dupes = sorted(k for k, n in seen.items() if n > 1)
        if dupes:
            reasons.append(f"case_id 重复: {dupes}")
        return reasons

    # ── 序列化 ──────────────────────────────────────────────

    def to_storage_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": CASE_SCHEMA_VERSION,
            "capability_id": self.capability_id,
            "version": self.version,
            "upstream_version": self.upstream_version,
            "upstream_schema": dict(self.upstream_schema),
            "active": bool(self.active),
            "drifted": bool(self.drifted),
            "drift_reason": self.drift_reason,
            "drifted_at": self.drifted_at,
            "drift_details": dict(self.drift_details),
            "regeneration_count": self.regeneration_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_probed_at": self.last_probed_at,
            "upstream_fingerprint": self.upstream_fingerprint(),
            "size_verdict": self.size_verdict(),
            "cases": [c.to_storage_dict() for c in self.cases],
        }

    @classmethod
    def from_storage_dict(cls, data: Dict[str, Any]) -> "CaseSet":
        if not isinstance(data, dict):
            raise CaseValidationError(["判定集不是 dict"])
        payload = dict(data)
        for derived in ("schema_version", "upstream_fingerprint", "size_verdict"):
            payload.pop(derived, None)
        payload["cases"] = [EquivalenceCase.from_storage_dict(c)
                            for c in (payload.get("cases") or [])]
        known = set(cls.__dataclass_fields__)
        unknown = set(payload) - known
        if unknown:
            raise CaseValidationError(
                [f"判定集含未知字段（拒绝静默丢弃）: {sorted(unknown)}"])
        return cls(**payload)


def build_case_set(capability_id: str,
                   cases: Sequence[EquivalenceCase], *,
                   version: int = 1,
                   upstream_version: str = "",
                   upstream_schema: Optional[Dict[str, Any]] = None,
                   created_at: float = 0.0) -> CaseSet:
    """用例序列 → 判定集（去重 + 确定性排序；非法用例**显式抛出**，不静默丢弃）"""
    case_set = CaseSet(capability_id=str(capability_id or ""), version=int(version),
                       upstream_version=str(upstream_version or ""),
                       upstream_schema=dict(upstream_schema or {}),
                       created_at=float(created_at or 0.0))
    for case in cases:
        case.require_valid()
        case_set.add(case)
    reasons = case_set.validate()
    if reasons:
        raise CaseValidationError(reasons)
    return case_set


# ════════════════════════════════════════════════════════════
#  CaseStore（JSON / SQLite；独立于 Trace 存储）
# ════════════════════════════════════════════════════════════


def default_case_root() -> str:
    """判定集根目录（``CP_DIGESTION_CASE_DIR`` 覆盖；非法值回退默认）"""
    raw = str(os.environ.get(CASE_ROOT_ENV, "") or "").strip()
    return raw or DEFAULT_CASE_ROOT


class CaseStore:
    """判定集存储（抽象基类；子类只实现"全量读 / 单能力写 / 删"三件事）

    **独立性硬约束**（§3.1）：存储根目录与 S2-01 统一台账（``data/traces/``）
    无任何共享文件 —— Trace 90 天过期只影响追溯能力，判定集资产不受影响。
    """

    backend = "base"

    def __init__(self, root: str = "", *, cost_store: Any = None) -> None:
        """
        Args:
            root: 判定集根目录（空 → `default_case_root()`）。
            cost_store: 显式成本事件 store（测试/离线用；缺省按
                `<root>/_case_cost/` 取本模块缓存的 writer —— 见 `case_cost`）。
        """
        self.root = str(root or default_case_root())
        self._cost_store = cost_store
        os.makedirs(self.root, exist_ok=True)

    # ── 子类实现 ────────────────────────────────────────────

    def _read_all(self) -> Dict[str, List[Dict[str, Any]]]:
        raise NotImplementedError

    def _write(self, capability_id: str,
               versions: List[Dict[str, Any]]) -> None:
        raise NotImplementedError

    def _remove(self, capability_id: str) -> bool:
        raise NotImplementedError

    # ── 公共 API ────────────────────────────────────────────

    def save(self, case_set: CaseSet, *, keep_history: bool = True,
             cost_context: Optional[Mapping[str, Any]] = None,
             record_cost: bool = True) -> int:
        """写入判定集（按 ``version`` 幂等覆盖；保留有限历史）→ 返回落库版本

        **构建成本埋点（TASK-S7-06 R1）**：当写入的是一个**新版本**判定集时，
        记一条 ``cost`` 事件（``stage=case_build``）到 `<判定集根>/_case_cost/`，
        供 ROI 报告单列披露一次性建造成本。同版本重复落库（如施加适用性后重存）
        **不重复计费**（幂等键 = ``case_build:<capability>:v<version>``）。

        Args:
            keep_history: 是否保留有限历史版本。
            cost_context: 构建成本的**实测**输入（``tokens_in``/``tokens_out``/``model``/
                ``manual_review_minutes``/``replay_cpu_ms``/``extra_cents``/``source``/``note``）；
                缺省即 0（如实为 0，不臆造）。
            record_cost: 关闭埋点（仅供不需要成本流的内部重存调用）。
        """
        reasons = case_set.validate()
        if reasons:
            raise CaseValidationError(reasons)
        all_rows = self._read_all()
        versions = all_rows.get(case_set.capability_id, [])
        payload = case_set.to_storage_dict()
        is_new_version = not any(int(v.get("version") or 0) == case_set.version
                                 for v in versions)
        versions = [v for v in versions if int(v.get("version") or 0)
                    != case_set.version]
        versions.append(payload)
        versions.sort(key=lambda v: int(v.get("version") or 0))
        if keep_history and len(versions) > MAX_STORE_HISTORY:
            versions = versions[-MAX_STORE_HISTORY:]
        self._write(case_set.capability_id, versions)
        if record_cost and is_new_version:
            record_case_build_from_case_set(
                case_set, cost_context=cost_context,
                case_root=self.root, store=getattr(self, "_cost_store", None))
        return case_set.version

    def load(self, capability_id: str, *, version: Optional[int] = None
             ) -> Optional[CaseSet]:
        """读取判定集（``version=None`` 取最新版本；不存在返回 None）"""
        rows = self._read_all().get(str(capability_id or ""), [])
        if not rows:
            return None
        if version is None:
            row = max(rows, key=lambda v: int(v.get("version") or 0))
        else:
            matched = [v for v in rows if int(v.get("version") or 0) == int(version)]
            if not matched:
                return None
            row = matched[0]
        return CaseSet.from_storage_dict(row)

    def next_version(self, capability_id: str) -> int:
        rows = self._read_all().get(str(capability_id or ""), [])
        if not rows:
            return 1
        return max(int(v.get("version") or 0) for v in rows) + 1

    def history(self, capability_id: str) -> List[Dict[str, Any]]:
        """版本摘要（升序；供"失效重生成"取证）"""
        rows = self._read_all().get(str(capability_id or ""), [])
        out: List[Dict[str, Any]] = []
        for row in sorted(rows, key=lambda v: int(v.get("version") or 0)):
            out.append({
                "version": int(row.get("version") or 0),
                "active": bool(row.get("active", True)),
                "drifted": bool(row.get("drifted", False)),
                "cases": len(row.get("cases") or []),
                "updated_at": float(row.get("updated_at") or 0.0),
                "upstream_version": str(row.get("upstream_version") or ""),
            })
        return out

    def list_capabilities(self) -> List[str]:
        return sorted(self._read_all())

    def exists(self, capability_id: str) -> bool:
        return bool(self._read_all().get(str(capability_id or "")))

    def delete(self, capability_id: str) -> bool:
        return self._remove(str(capability_id or ""))

    def stats(self) -> Dict[str, Any]:
        """存储总览（供验收报告与面板）"""
        all_rows = self._read_all()
        total_cases = 0
        drifted = 0
        for rows in all_rows.values():
            latest = max(rows, key=lambda v: int(v.get("version") or 0))
            total_cases += len(latest.get("cases") or [])
            if latest.get("drifted"):
                drifted += 1
        return {
            "backend": self.backend,
            "root": self.root,
            "capabilities": len(all_rows),
            "cases": total_cases,
            "drifted_capabilities": drifted,
            "layout": "json:<slug>.json" if self.backend == BACKEND_JSON
                      else "sqlite:case_sets",
        }


class JsonCaseStore(CaseStore):
    """JSON 后端：``<root>/<slug>.json`` 一能力一文件（含有限版本历史）"""

    backend = BACKEND_JSON

    def _path(self, capability_id: str) -> str:
        return os.path.join(self.root, f"{slug_of(capability_id)}.json")

    def _read_all(self) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        try:
            names = sorted(os.listdir(self.root))
        except OSError as e:  # noqa: BLE001
            logger.warning("判定集目录不可读 %s: %s", self.root, e)
            return out
        for name in names:
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.root, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            except (OSError, ValueError) as e:  # noqa: BLE001
                logger.warning("判定集文件损坏（跳过，不静默删）: %s: %s", path, e)
                continue
            cid = str(payload.get("capability_id") or "")
            if not cid:
                continue
            out[cid] = list(payload.get("versions") or [])
        return out

    def _write(self, capability_id: str, versions: List[Dict[str, Any]]) -> None:
        path = self._path(capability_id)
        payload = {"schema_version": CASE_SCHEMA_VERSION,
                   "capability_id": capability_id,
                   "latest_version": max(int(v.get("version") or 0)
                                         for v in versions),
                   "updated_at": _now(),
                   "versions": versions}
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=False)
        os.replace(tmp, path)

    def _remove(self, capability_id: str) -> bool:
        path = self._path(capability_id)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False


class SqliteCaseStore(CaseStore):
    """SQLite 后端：``<root>/cases.db`` 单表 ``case_sets``（供规模化与并发读）"""

    backend = BACKEND_SQLITE
    DB_NAME = "cases.db"

    @property
    def db_path(self) -> str:
        return os.path.join(self.root, self.DB_NAME)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS case_sets ("
            "capability_id TEXT NOT NULL, version INTEGER NOT NULL, "
            "active INTEGER NOT NULL DEFAULT 1, drifted INTEGER NOT NULL DEFAULT 0, "
            "payload TEXT NOT NULL, updated_at REAL NOT NULL, "
            "PRIMARY KEY (capability_id, version))")
        return conn

    def _read_all(self) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT capability_id, payload FROM case_sets "
                "ORDER BY capability_id, version").fetchall()
        for cid, payload in rows:
            try:
                out.setdefault(str(cid), []).append(json.loads(payload))
            except ValueError as e:  # noqa: BLE001
                logger.warning("判定集行损坏（跳过）: %s: %s", cid, e)
        return out

    def _write(self, capability_id: str, versions: List[Dict[str, Any]]) -> None:
        with self._connect() as conn:
            existing = {int(r[0]) for r in conn.execute(
                "SELECT version FROM case_sets WHERE capability_id = ?",
                (capability_id,)).fetchall()}
            wanted = {int(v.get("version") or 0) for v in versions}
            for stale in sorted(existing - wanted):
                conn.execute(
                    "DELETE FROM case_sets WHERE capability_id = ? AND version = ?",
                    (capability_id, stale))
            for row in versions:
                conn.execute(
                    "INSERT INTO case_sets (capability_id, version, active, "
                    "drifted, payload, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(capability_id, version) DO UPDATE SET "
                    "active=excluded.active, drifted=excluded.drifted, "
                    "payload=excluded.payload, updated_at=excluded.updated_at",
                    (capability_id, int(row.get("version") or 0),
                     1 if row.get("active", True) else 0,
                     1 if row.get("drifted") else 0,
                     json.dumps(row, ensure_ascii=False), _now()))

    def _remove(self, capability_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM case_sets WHERE capability_id = ?",
                               (capability_id,))
            return bool(cur.rowcount)


def open_case_store(root: str = "", backend: str = "") -> CaseStore:
    """判定集存储工厂（后端取 ``CP_DIGESTION_CASE_BACKEND``；非法值回退 JSON）"""
    name = str(backend or os.environ.get(CASE_BACKEND_ENV, "") or BACKEND_JSON).strip().lower()
    if name not in BACKENDS:
        logger.warning("非法判定集后端 %r，回退 %s", name, BACKEND_JSON)
        name = BACKEND_JSON
    return SqliteCaseStore(root) if name == BACKEND_SQLITE else JsonCaseStore(root)


# ════════════════════════════════════════════════════════════
#  通道 ②：从统一台账生成（复用 S3-01 清洗口径）
# ════════════════════════════════════════════════════════════

#: 生成为"用例输入"的合成值（**无真实数据**；确定性、可复现）
_PATH_PARAM_HINTS = ("path", "file", "filepath", "file_path", "target", "dest",
                     "destination", "src", "source", "dir", "directory", "root")
_READ_LABEL_HINTS = ("read", "list", "grep", "stat", "open", "cat", "scan",
                     "search", "glob", "inspect")


def synthesize_value(placeholder: Any, *, key: str = "", index: int = 0,
                     root: str = DEFAULT_SANDBOX_ROOT) -> Any:
    """占位符 → 确定性合成值（不引入任何真实数据；同输入恒同值）"""
    text = str(placeholder or "")
    low_key = str(key or "").lower()
    if text == "${path}" or low_key in _PATH_PARAM_HINTS:
        return f"{root}/case{index:03d}/{low_key or 'file'}.txt"
    if text == "${timestamp}":
        return f"2026-01-{(index % 28) + 1:02d}T00:00:00Z"
    if text == "${uuid}":
        digest = _short_hash(f"uuid|{key}|{index}", 32)
        return (f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-"
                f"{digest[16:20]}-{digest[20:32]}")
    if text == "${hex_id}":
        return _short_hash(f"hex|{key}|{index}", 32)
    if text == "${url}":
        return f"https://sandbox.invalid/case{index:03d}"
    if text == "${email}":
        return f"user{index:03d}@sandbox.invalid"
    if text == "${number}":
        return index + 1
    return f"{key or 'value'}-{index:03d}"


def _is_path_like(key: str, value: Any) -> bool:
    lowered = str(key or "").lower()
    if lowered in _PATH_PARAM_HINTS:
        return True
    return str(value or "") == "${path}"


def _fixture_content(label: str, index: int) -> str:
    return f"# sandbox fixture {index:03d} for {label}\nvalue = {index}\n"


def _concrete_value(value: Any, *, key: str, index: int, root: str) -> Any:
    """记录的参数值 → 用例里的**具体值**

    占位符（``${path}`` / ``${cmd}`` …）→ 确定性合成值；字面量 → **原值**。
    后者是硬要求：若把字面量也"合成"掉，候选臂与上游臂会写/传不同的值，
    双跑的副作用硬性比对必然误红（实现期实测）。
    """
    if is_placeholder(value):
        return synthesize_value(value, key=key, index=index, root=root)
    return value


def _occurrence_index(steps: Sequence[Any]) -> Dict[str, List[int]]:
    """参数键 → 其**出现位次**列表（次序即首次出现顺序，去重）"""
    occurrences: Dict[str, List[int]] = {}
    for pos, step in enumerate(steps):
        for key in (getattr(step, "params", None) or {}):
            bucket = occurrences.setdefault(str(key), [])
            if pos not in bucket:
                bucket.append(pos)
    return occurrences


def _slot_name(key: str, pos: int, occurrences: Dict[str, List[int]]) -> str:
    """参数键 + 位次 → **参数槽名**（与 `generalize.infer_parameter_slots` 同规则）

    同键名在多处出现时：首位 ``${key}``，其后 ``${key}_2`` / ``${key}_3`` …
    本函数是该命名规则的**镜像**，由
    ``test_slot_names_mirror_generalize_rule`` 与既有实现逐值对账（防漂移）。
    """
    positions = occurrences.get(key) or [pos]
    ordinal = positions.index(pos) + 1 if pos in positions else 1
    return key if ordinal == 1 else f"{key}_{ordinal}"


def _positional_program(traj: Trajectory,
                        *, bindings: Optional[Dict[str, Any]] = None
                        ) -> List[ProgramStep]:
    """轨迹 → 用例的**上游程序**（形态占位符提升为**位次槽占位符**）

    清洗期把每个具体路径都压成同一个形态占位符 ``${path}``，故同名键在不同位次
    （读的 path、写的 path）**长得一样** —— 只按键名绑定会让两处都取到第一个值
    （实测缺陷：上游臂把报告写到被读的文件上）。此处按 `slot_names_for_program`
    的位次规则把占位符提升为 ``${path}`` / ``${path_2}``，使绑定**按位次**取到
    各自的具体值；方言仍是 S3-01 的 ``${name}``，未新造。
    """
    names = positional_slot_names(traj.steps)
    program: List[ProgramStep] = []
    for pos, step in enumerate(traj.steps):
        params: Dict[str, Any] = {}
        for key, value in (step.params or {}).items():
            key = str(key)
            slot = names[pos].get(key, key) if pos < len(names) else key
            if is_placeholder(value) and slot != key:
                params[key] = slot_placeholder(slot)
            else:
                params[key] = value
        program.append(ProgramStep(label=step.label, params=params,
                                   capability_id=step.capability_id))
    return program


def _bound_inputs_for_trajectory(traj: Trajectory, *, capability_id: str,
                                 index: int, root: str,
                                 concrete: Optional[Sequence[Any]] = None
                                 ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, str]]:
    """轨迹 → (用例输入, 绑定表, 虚拟夹具)

    - ``input`` 取**目标能力的种子步**参数（其参数形态即 intent_key 的来源）；
    - ``bindings`` 覆盖其余步骤的参数，键名按 `generalize.infer_parameter_slots`
      的槽命名规则（含 ``_2`` 后缀）—— 使 S3-01 挖出的骨架槽**全部可绑定**；
    - ``concrete``（可选）是该轨迹各步的**台账原始参数**（`args_redacted`，即
      §3.1 要求的"复制数据"）：给出时优先于形态合成值，使判定集保留参数的
      **语义内容**（如"该路径是测试文件"），从而能真实覆盖参数级分支；
    - 未给 ``concrete`` 时退化为形态占位符 → 确定性合成值（不引入真实数据）。
    """
    steps = list(traj.steps)
    occurrences = _occurrence_index(steps)
    raw = list(concrete) if concrete else []
    seed_pos = 0
    for pos, step in enumerate(steps):
        if step.capability_id and step.capability_id == capability_id:
            seed_pos = pos
            break
    inputs: Dict[str, Any] = {}
    bindings: Dict[str, Any] = {}
    fixtures: Dict[str, str] = {}
    path_inputs: List[str] = []

    for pos, step in enumerate(steps):
        is_read = any(hint in str(step.label or "").lower()
                      for hint in _READ_LABEL_HINTS)
        recorded = dict(raw[pos]) if pos < len(raw) and isinstance(raw[pos], dict) else {}
        for key, value in (step.params or {}).items():
            key = str(key)
            concrete_value = recorded.get(key)
            if concrete_value is not None:
                # §3.1「复制数据」：优先用台账原始参数（已脱敏），保留语义内容
                resolved = concrete_value
            else:
                resolved = _concrete_value(value, key=key, index=index, root=root)
            name = _slot_name(key, pos, occurrences)
            if pos == seed_pos:
                inputs.setdefault(key, resolved)
                bindings.setdefault(name, inputs[key])
            else:
                bindings.setdefault(name, resolved)
            if _is_path_like(key, value) and isinstance(resolved, str):
                if is_read:
                    fixtures.setdefault(resolved, _fixture_content(str(step.label),
                                                                  index))
                if pos == seed_pos:
                    path_inputs.append(resolved)
    for path in path_inputs:
        fixtures.setdefault(path, _fixture_content(str(capability_id), index))
    return inputs, bindings, fixtures


def derive_sandbox_root(values: Iterable[Any], *,
                        fallback: str = DEFAULT_SANDBOX_ROOT,
                        components: int = 2) -> str:
    """一组具体路径 → **沙箱根**（取其公共前缀，缺省回退 ``C:/sandbox``）

    沙箱根是本任务"绝不双写真实环境"的分界：用例复制的台账路径可能形如
    ``C:/repo/proj001/tests/test_x.py``，故沙箱根要覆盖到它们的公共前缀
    （仍然是**虚拟路径**：沙箱只把这些字符串当键，不做任何真实 I/O）。
    """
    prefixes: List[List[str]] = []
    for value in values or ():
        if not isinstance(value, str) or ":" not in value[:3]:
            continue
        parts = value.replace("\\", "/").split("/")
        if len(parts) > 1:
            prefixes.append(parts[:components + 1])
    if not prefixes:
        return fallback
    common = list(prefixes[0])
    for parts in prefixes[1:]:
        limit = min(len(common), len(parts))
        keep = 0
        while keep < limit and common[keep] == parts[keep]:
            keep += 1
        common = common[:keep]
    if len(common) < 2:
        return fallback
    return "/".join(common)


def concrete_args_from_rows(rows: Sequence[Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """台账行（同一任务、按序）→ ``[(标签, 原始参数), ...]``

    标签按 `cleaning._label_of` 同规则取（capability_id 末段并先经 L1 归一），
    使清洗后的轨迹步骤能与台账行**按标签对齐**（清洗会削掉探索前缀/合并重复，
    按下标硬对会错位 —— 实现期实测的真实缺陷）。
    """
    out: List[Tuple[str, Dict[str, Any]]] = []
    for row in rows:
        args = getattr(getattr(row, "request", None), "args_redacted", None)
        out.append((_row_label(row), dict(args) if isinstance(args, dict) else {}))
    return out


def _row_label(row: Any) -> str:
    """台账行 → 归一动作标签（与 `cleaning._label_of` 同口径）"""
    cid = str(getattr(row, "capability_id", "") or "")
    try:
        cid = str(capability_module.resolve(cid)["capability_id"] or cid)
    except Exception:  # noqa: BLE001  台账不可用 → 保留原文（不丢行）
        pass
    return cid.rsplit(".", 1)[-1] or cid


def align_concrete_args(
    steps: Sequence[Any],
    pairs: Optional[Sequence[Any]],
) -> List[Dict[str, Any]]:
    """清洗后步骤 × 台账 ``(标签, 原始参数)`` → **逐步对齐**的原始参数

    贪心按标签匹配（清洗保留合并重复的**首个**，故贪心取首个同标签行与之一致）；
    未匹配到的步骤给空字典（调用方退化为形态合成值，不臆造）。
    """
    out: List[Dict[str, Any]] = []
    cursor = 0
    rows = list(pairs or [])
    for step in steps or ():
        label = str(getattr(step, "label", "") or "")
        matched: Dict[str, Any] = {}
        for index in range(cursor, len(rows)):
            item = rows[index]
            if isinstance(item, (tuple, list)) and len(item) == 2:
                row_label, args = str(item[0]), item[1]
            elif isinstance(item, dict):
                row_label, args = _row_label(item), item
            else:
                continue
            if row_label == label:
                matched = dict(args) if isinstance(args, dict) else {}
                cursor = index + 1
                break
        out.append(matched)
    return out


def concrete_args_provider(
    store: Any,
    capability_id: str,
    *,
    registry: Any = None,
    limit: int = 400,
) -> Callable[[Trajectory], Optional[List[Tuple[str, Dict[str, Any]]]]]:
    """构造"按任务取原始参数"的取数器（§3.1：判定集**复制数据**脱离 Trace 生命周期）

    返回 ``callable(trajectory) -> [(标签, args), ...] | None``；台账不可用时一律
    返回 ``None``（advisory：退化到形态合成，不阻断用例生成）。
    """
    cache: Dict[str, Optional[List[Tuple[str, Dict[str, Any]]]]] = {}

    def _provider(traj: Trajectory) -> Optional[List[Tuple[str, Dict[str, Any]]]]:
        task_id = str(getattr(traj, "task_id", "") or "")
        if not task_id:
            return None
        if task_id not in cache:
            cache[task_id] = None
            try:
                rows, _ = capability_module.collect_rows(
                    store, capability_id, limit=limit, registry=registry)
                groups = capability_module.group_rows_by_task(rows)
                task_rows = [r for r in groups.get(task_id, [])
                             if str(getattr(r, "capability_id", "") or "")]
                cache[task_id] = (concrete_args_from_rows(task_rows)
                                  if task_rows else None)
            except Exception as e:  # noqa: BLE001
                logger.debug("按任务取原始参数失败（退化为形态合成）: %s", e)
                cache[task_id] = None
        return cache[task_id]

    return _provider


def _side_effects_from_trajectory(traj: Trajectory) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {k: [] for k in SIDE_EFFECT_KINDS}
    for step in traj.steps:
        out["files_written"].extend(str(p) for p in step.files_written)
        out["files_deleted"].extend(str(p) for p in step.files_deleted)
        out["external_calls"].extend(str(p) for p in step.external_calls)
    return {k: sorted(set(v)) for k, v in out.items()}


def cases_from_trace_set(
    trace_set: TraceSet,
    *,
    capability_id: str = "",
    sandbox_root: str = DEFAULT_SANDBOX_ROOT,
    include_negative: bool = False,
    limit: int = 0,
    created_at: float = 0.0,
    concrete_args: Optional[Callable[[Trajectory], Optional[Sequence[Any]]]] = None,
) -> List[EquivalenceCase]:
    """同类轨迹集 → 等价用例（通道 ②；**复制数据脱离 Trace 生命周期**）

    - 每条（默认成功的）轨迹生成 **1 组**用例：``input`` = 具体输入，
      ``upstream`` = 该轨迹的清洗后步序列（**上游实现的录制程序**），
      ``expected_side_effects`` = 轨迹聚合的副作用集合；
    - ``origin_trace_id`` 记录溯源指针（数据本体已复制，台账过期不影响）；
    - ``concrete_args``（可选）按任务取**台账原始参数**（§3.1 的"复制数据"）：
      给出时用例保留参数语义内容（如路径确为测试文件），参数级分支因此可被真实
      覆盖；缺省时退化为形态占位符 → 确定性合成值（不引入真实数据）；
    - 失败轨迹默认不进判定集（它是负样本，判定集的"等价"语义要求成功基线）；
      ``include_negative=True`` 可显式纳入（其终态记为"不主张"，见 `EXPECTED_STATUS_ANY`）。
    """
    cid = str(capability_id or trace_set.key.capability_id or "")
    trajectories = sorted(trace_set.trajectories,
                          key=lambda t: (t.trajectory_id, t.source_trace_id))
    cases: List[EquivalenceCase] = []
    for index, traj in enumerate(trajectories):
        if traj.is_negative and not include_negative:
            continue
        if not traj.steps:
            continue
        raw = (list(concrete_args(traj) or []) if concrete_args is not None else [])
        root = derive_sandbox_root(raw_to_paths(raw), fallback=sandbox_root)
        aligned = align_concrete_args(traj.steps, raw)
        inputs, bindings, fixtures = _bound_inputs_for_trajectory(
            traj, capability_id=cid, index=index, root=root, concrete=aligned)
        upstream = _positional_program(traj, bindings=bindings)
        recorded_effects = _side_effects_from_trajectory(traj)
        has_effects = any(recorded_effects[k] for k in SIDE_EFFECT_KINDS)
        case = EquivalenceCase(
            case_id=case_id_for(cid, kind=CASE_KIND_TRACE, index=index,
                                origin_trace_id=traj.source_trace_id),
            capability_id=cid,
            title=f"轨迹派生用例 #{index:03d}（{traj.step_count} 步）",
            input=inputs,
            upstream=upstream,
            expected_side_effects=recorded_effects,
            expected_status=(OUTCOME_SUCCESS if not traj.is_negative
                             else EXPECTED_STATUS_ANY),
            kind=CASE_KIND_TRACE,
            origin_trace_id=str(traj.source_trace_id or ""),
            origin_task_id=str(traj.task_id or ""),
            intent_key=str(traj.key.intent_key or ""),
            fixtures=fixtures,
            bindings=bindings,
            sandbox_root=root,
            side_effects_source=(SIDE_EFFECT_SOURCE_TRACE if has_effects
                                 else SIDE_EFFECT_SOURCE_NONE),
            provenance={
                "kind": PROV_TRACE,
                "source": "unified_trace_ledger",
                "ref": str(traj.source_trace_id or ""),
                "task_id": str(traj.task_id or ""),
                "concrete_args": bool(raw),
                "note": ("从 S2-01 统一台账同类轨迹生成；数据本体已复制，"
                         "仅保留 origin_trace_id 指针（§3.1：Trace 90 天过期不"
                         "影响判定集）"),
            },
            created_at=float(created_at or 0.0),
        )
        cases.append(case)
    if limit and limit > 0:
        cases = cases[:int(limit)]
    return cases


def raw_to_paths(raw: Sequence[Any]) -> List[str]:
    """原始参数（``(标签, args)`` 对或裸 args 字典）→ 其中的路径类取值（供推导沙箱根）"""
    out: List[str] = []
    for item in raw or ():
        args = item[1] if (isinstance(item, (tuple, list)) and len(item) == 2
                           and isinstance(item[1], dict)) else item
        if not isinstance(args, dict):
            continue
        for key, value in args.items():
            if isinstance(value, str) and _is_path_like(str(key), value):
                out.append(value)
    return out


def slot_names_for_program(steps: Sequence[Any]) -> Dict[str, List[str]]:
    """步骤序列 → ``{参数键: [槽名(按位次)]}``（供对账与用例补绑定）"""
    occurrences = _occurrence_index(steps)
    out: Dict[str, List[str]] = {}
    for pos, step in enumerate(steps):
        for key in (getattr(step, "params", None) or {}):
            name = _slot_name(str(key), pos, occurrences)
            bucket = out.setdefault(str(key), [])
            if name not in bucket:
                bucket.append(name)
    return out


def positional_slot_names(steps: Sequence[Any]) -> List[Dict[str, str]]:
    """逐步的 ``{参数键: 槽名}``（按位次区分同名键，如 ``path`` / ``path_2``）

    供"接口补齐"按**位次**取到正确的绑定值：同名键在多处出现时，位置不同取值不同
    （如读的 ``path`` 与写的 ``path_2``），只按键名取会张冠李戴 —— 实现期实测缺陷。
    """
    occurrences = _occurrence_index(steps)
    out: List[Dict[str, str]] = []
    for pos, step in enumerate(steps):
        params = getattr(step, "params", None) or {}
        out.append({str(key): _slot_name(str(key), pos, occurrences)
                    for key in params})
    return out


def trace_set_for(capability_id: str, *, service: Any = None, intent: str = "",
                  limit: int = 200) -> Optional[TraceSet]:
    """从统一台账取"同类成功轨迹集"（懒加载 `DigestionService`；不可用返回 None）

    复用 S3-01 的采集/清洗/同类分组口径（`cleaning.same_task_key` 三元组），
    保证"用例属于同一能力同一意图"这一前提由既有实现给出，而非本模块另立口径。
    """
    try:
        if service is None:
            from .service import DigestionService
            service = DigestionService(persist_drafts=False, emit_events=False)
        rows, _ = service.collect(capability_id, limit=limit)
        trajectories, _ = service.build_trajectories(
            rows, capability_id=capability_id, intent=intent, limit=limit)
        from .cleaning import group_by_same_task
        buckets = group_by_same_task(trajectories)
        success = [b for b in buckets.values() if not b.key.is_negative]
        if not success:
            return None
        return sorted(success, key=lambda b: (-b.size, b.key.as_str()))[0]
    except Exception as e:  # noqa: BLE001  台账不可用属 advisory
        logger.debug("从台账取同类轨迹集失败: %s", e)
        return None


# ════════════════════════════════════════════════════════════
#  通道 ①：Seed Pack（P7.2-23）
# ════════════════════════════════════════════════════════════

SEED_PACK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "seed_pack.json")
SEED_PACK_ENV = "CP_DIGESTION_SEED_PACK"

_seed_pack_cache: Dict[str, Any] = {}


def load_seed_pack(path: str = "") -> Dict[str, Any]:
    """加载 Seed Pack JSON（默认随包资产；``CP_DIGESTION_SEED_PACK`` 可覆盖）

    读失败返回**空骨架**并告警（advisory：Seed Pack 不可用不得阻断主管道）。
    """
    target = str(path or os.environ.get(SEED_PACK_ENV, "") or SEED_PACK_PATH)
    cache_key = target
    if cache_key in _seed_pack_cache:
        # 深拷贝返回：调用方对返回结构的任何修改都不会污染缓存（资产是共享单例）
        return copy.deepcopy(_seed_pack_cache[cache_key])
    empty: Dict[str, Any] = {"schema_version": CASE_SCHEMA_VERSION,
                             "skills": [], "source": "", "path": target}
    try:
        with open(target, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError) as e:  # noqa: BLE001
        logger.warning("Seed Pack 不可用（advisory，主管道不受影响）: %s: %s",
                       target, e)
        _seed_pack_cache[cache_key] = empty
        return empty
    payload = dict(payload or {})
    payload.setdefault("skills", [])
    payload["path"] = target
    _seed_pack_cache[cache_key] = payload
    return payload


def reset_seed_pack_cache() -> None:
    """清空 Seed Pack 缓存（用例注入自定义资产后调用）"""
    _seed_pack_cache.clear()


def seed_pack_skills(path: str = "") -> List[Dict[str, Any]]:
    return [dict(s) for s in (load_seed_pack(path).get("skills") or [])]


def seed_pack_capability_ids(path: str = "") -> List[str]:
    return sorted(str(s.get("capability_id") or "") for s in seed_pack_skills(path)
                  if s.get("capability_id"))


def _seed_case_from_dict(skill: Dict[str, Any], raw: Dict[str, Any], *,
                         index: int) -> EquivalenceCase:
    cid = str(skill.get("capability_id") or "")
    case_id = str(raw.get("case_id") or "").strip() or case_id_for(
        cid, kind=CASE_KIND_SEED, index=index)
    return EquivalenceCase(
        case_id=case_id,
        capability_id=cid,
        title=str(raw.get("title") or ""),
        input=dict(raw.get("input") or {}),
        upstream=program_from_storage(raw.get("upstream")),
        native=program_from_storage(raw.get("native")),
        expected_output=dict(raw.get("expected_output") or {}),
        expected_output_schema=dict(raw.get("expected_output_schema") or {}),
        expected_side_effects=dict(raw.get("expected_side_effects") or {}),
        expected_status=str(raw.get("expected_status") or OUTCOME_SUCCESS),
        kind=CASE_KIND_SEED,
        origin_trace_id=str(raw.get("origin_trace_id") or ""),
        intent_key=str(raw.get("intent_key") or ""),
        fixtures={str(k): str(v) for k, v in (raw.get("fixtures") or {}).items()},
        bindings=dict(raw.get("bindings") or {}),
        sandbox_root=str(raw.get("sandbox_root") or DEFAULT_SANDBOX_ROOT),
        destructive=bool(raw.get("destructive", False)),
        branch_tags=[str(t) for t in (raw.get("branch_tags") or [])],
        side_effects_source=str(raw.get("side_effects_source")
                                or SIDE_EFFECT_SOURCE_AUTHORED),
        provenance={
            "kind": PROV_SEED_PACK,
            "source": str(skill.get("provenance", {}).get("source") or "P7.2-23"),
            "ref": str(skill.get("provenance", {}).get("ref") or cid),
            "skill": str(skill.get("display_name") or cid),
            "note": str(raw.get("provenance_note")
                        or skill.get("provenance", {}).get("note") or ""),
        },
        notes=str(raw.get("notes") or ""),
    )


def seed_cases_for(capability_id: str, path: str = "") -> List[EquivalenceCase]:
    """某能力的 Seed 用例（无该能力则返回空列表）"""
    cid = str(capability_id or "")
    out: List[EquivalenceCase] = []
    for skill in seed_pack_skills(path):
        if str(skill.get("capability_id") or "") != cid:
            continue
        for index, raw in enumerate(skill.get("cases") or []):
            out.append(_seed_case_from_dict(skill, raw, index=index))
    return out


def seed_native_program(capability_id: str, path: str = "") -> List[ProgramStep]:
    """某能力的 Seed **候选原生实现**骨架模板（回放双跑的右臂）"""
    cid = str(capability_id or "")
    for skill in seed_pack_skills(path):
        if str(skill.get("capability_id") or "") == cid:
            return program_from_storage(skill.get("native_template"))
    return []


def seed_candidate_for(case: EquivalenceCase, path: str = "") -> List[ProgramStep]:
    """用例对应的候选原生实现：**用例级覆盖优先**，否则用能力级模板

    用例级覆盖是为"同一能力的不同用例走不同流程"准备的（例如"仅产出报告"的
    边界用例本就不该有写测试步骤）—— 用能力级模板硬套会让判定集自身失真。
    """
    return list(case.native) if case.native else seed_native_program(
        case.capability_id, path)


def seed_pack_case_sets(path: str = "") -> List[CaseSet]:
    """Seed Pack → 判定集列表（每技能一集；**起步集**，如实标注规模不足）"""
    sets: List[CaseSet] = []
    for skill in seed_pack_skills(path):
        cid = str(skill.get("capability_id") or "")
        if not cid:
            continue
        cases = seed_cases_for(cid, path)
        sets.append(build_case_set(
            cid, cases, version=1,
            upstream_version=str(skill.get("upstream_version") or ""),
            upstream_schema=dict(skill.get("upstream_schema") or {})))
    return sets


def seed_pack_summary(path: str = "") -> Dict[str, Any]:
    """Seed Pack 总览（P7.2-23 达标裁定：≥12 技能 × ≥3 组）"""
    skills = seed_pack_skills(path)
    per_skill: Dict[str, int] = {}
    for skill in skills:
        per_skill[str(skill.get("capability_id") or "")] = len(skill.get("cases") or [])
    short = sorted(cid for cid, n in per_skill.items()
                   if n < MIN_SEED_CASES_PER_SKILL)
    payload = load_seed_pack(path)
    return {
        "path": str(payload.get("path") or ""),
        "source": str(payload.get("source") or ""),
        "skills": len(per_skill),
        "cases": sum(per_skill.values()),
        "per_skill": per_skill,
        "skills_below_min": short,
        "min_skills": SEED_PACK_MIN_SKILLS,
        "min_cases_per_skill": MIN_SEED_CASES_PER_SKILL,
        "meets_p7_2_23": (len(per_skill) >= SEED_PACK_MIN_SKILLS
                          and not short),
    }


# ════════════════════════════════════════════════════════════
#  通道 ③ + 合并 / 失效重生成
# ════════════════════════════════════════════════════════════


def merge_cases(*groups: Sequence[EquivalenceCase]) -> List[EquivalenceCase]:
    """合并多通道用例（按 case_id 去重；同 id 取**先出现**者 → 顺序即优先级）

    优先级约定：Seed（人工）> Trace（自动）> LLM（草稿），与"宁可冗余不可误合"
    同向：人工审核过的用例不被自动产物覆盖。
    """
    merged: Dict[str, EquivalenceCase] = {}
    order: List[str] = []
    for group in groups:
        for case in group or []:
            if case.case_id in merged:
                continue
            merged[case.case_id] = case
            order.append(case.case_id)
    return [merged[cid] for cid in order]


def regenerate_case_set(
    capability_id: str,
    *,
    store: CaseStore,
    trace_set: Optional[TraceSet] = None,
    seed_backfill: bool = True,
    reason: str = "",
    created_at: float = 0.0,
    concrete_args: Optional[Callable[[Trajectory], Optional[Sequence[Any]]]] = None,
    cost_context: Optional[Mapping[str, Any]] = None,
) -> Optional[CaseSet]:
    """判定集**失效重生成**（§4.5 漂移重探末端：重新从 Trace 采样 + Seed 回填）

    ``cost_context``：本次重生成的**实测**成本输入（LLM token / 人工工时 / 回放算力），
    随构建成本事件一并入账 —— 漂移重生成开销正是 T1 指出的「未计成本」之一。

    Returns:
        新版本判定集；若既无轨迹集也无 Seed 回填则返回 ``None``（**不生成空集**，
        避免用"零用例的判定集"掩盖漂移）。
    """
    cid = str(capability_id or "")
    previous = store.load(cid)
    trace_cases = (cases_from_trace_set(trace_set, capability_id=cid,
                                       concrete_args=concrete_args)
                   if trace_set else [])
    seed_cases = seed_cases_for(cid) if seed_backfill else []
    combined = merge_cases(seed_cases, trace_cases)
    if not combined:
        logger.warning("重生成失败：%s 既无轨迹集也无 Seed 用例（不生成空判定集）", cid)
        return None
    version = store.next_version(cid)
    case_set = build_case_set(
        cid, combined, version=version,
        upstream_version=(previous.upstream_version if previous else ""),
        upstream_schema=(dict(previous.upstream_schema) if previous else {}),
        created_at=float(created_at or 0.0))
    case_set.regeneration_count = (previous.regeneration_count + 1
                                   if previous else 1)
    if previous is not None:
        case_set.drift_details = {
            "regenerated_from_version": previous.version,
            "regeneration_reason": str(reason or previous.drift_reason or ""),
        }
    case_set.updated_at = _now()
    store.save(case_set, cost_context=cost_context)
    return case_set


def case_set_for_capability(
    capability_id: str,
    *,
    store: CaseStore,
    trace_set: Optional[TraceSet] = None,
    seed_backfill: bool = True,
    created_at: float = 0.0,
    cost_context: Optional[Mapping[str, Any]] = None,
) -> Optional[CaseSet]:
    """取判定集；不存在则按可用通道生成并落库（幂等：已存在直接返回）"""
    cid = str(capability_id or "")
    existing = store.load(cid)
    if existing is not None:
        return existing
    return regenerate_case_set(cid, store=store, trace_set=trace_set,
                               seed_backfill=seed_backfill,
                               reason="首次生成", created_at=created_at,
                               cost_context=cost_context)


def pattern_capability_id(pattern: CandidatePattern) -> str:
    """候选模式 → 能力键（判定集以**能力**为维度，非模式）"""
    return str(getattr(getattr(pattern, "key", None), "capability_id", "") or "")


# ════════════════════════════════════════════════════════════
#  适用性施加（M4：判定集重生成后的**机器重新施加**通道）
# ════════════════════════════════════════════════════════════


def case_applies_to(case: EquivalenceCase, candidate_kind: Any) -> Tuple[bool, str]:
    """``(是否适用, 理由)`` —— 供灰度/内化把"为什么这条用例没算"写进报告"""
    applies = case.applies_to(candidate_kind)
    return applies, case.applicability_reason(candidate_kind)


def applicable_cases(cases: Sequence[EquivalenceCase],
                     candidate_kind: Any) -> Tuple[List[EquivalenceCase], List[Dict[str, Any]]]:
    """按适用性把用例分成 **(适用, 排除清单)**

    排除清单逐条带 ``case_id`` / ``reason``，使"少了哪些用例、为什么"可审计 ——
    这正是 S3-02 用 ``notes`` 表达时**做不到**的事（M4 的动机）。
    """
    kind = normalize_candidate_kind(candidate_kind)
    kept: List[EquivalenceCase] = []
    excluded: List[Dict[str, Any]] = []
    for case in cases or []:
        applies, reason = case_applies_to(case, kind)
        if applies:
            kept.append(case)
        else:
            excluded.append({
                "case_id": case.case_id, "capability_id": case.capability_id,
                "candidate_kind": kind, "reason": reason,
                "applicability": case.applicability.to_storage_dict(),
            })
    return kept, excluded


def _rule_matches(case: EquivalenceCase, match: Dict[str, Any]) -> bool:
    """适用性规则是否命中该用例（``case_ids`` / ``labels_contain`` / ``step_count``）"""
    if not match:
        return False
    case_ids = match.get("case_ids")
    if case_ids is not None and case.case_id not in {str(c) for c in case_ids}:
        return False
    contains = match.get("labels_contain")
    if contains:
        labels = set(case.labels)
        if not {str(c) for c in contains} & labels:
            return False
    step_count = match.get("step_count")
    if step_count is not None and int(case.step_count) != int(step_count):
        return False
    return True


def apply_applicability(
    case_set: CaseSet,
    *,
    rules: Sequence[Dict[str, Any]],
    declared_by: str = "",
    declared_at: float = 0.0,
    reset_first: bool = False,
) -> Dict[str, Any]:
    """把显式适用性**施加**到判定集（判定集重生成后重新跑本函数即可）

    ``rules`` 每条形如::

        {"match": {"case_ids": [...], "labels_contain": ["grep"], "step_count": 3},
         "include_kinds": [], "exclude_kinds": ["candidate_pattern"],
         "reason": "该用例描述单次读取契约，与被评三段任务链形状不同"}

    语义：**命中 ``match`` 的用例**得到该规则的 include/exclude；未命中任何规则的用例
    在 ``reset_first=True`` 时被重置为"不限"，否则保持既有声明。
    返回施加报告（含逐条命中的 case_id 与最终约束），**不落盘** —— 落盘由调用方
    （``store.save(case_set)``）决定，本函数是纯变换 + 报告。
    """
    report: Dict[str, Any] = {"capability_id": case_set.capability_id,
                              "rules": len(list(rules or [])), "applied": [],
                              "reset_first": bool(reset_first),
                              "declared_by": str(declared_by or "")}
    if reset_first:
        for case in case_set.cases:
            case.applicability = CaseApplicability()
    touched: List[str] = []
    for index, rule in enumerate(rules or []):
        if not isinstance(rule, dict):
            raise CaseValidationError([f"适用性规则 #{index} 不是 dict"])
        match = dict(rule.get("match") or {})
        applicability = CaseApplicability(
            include_kinds=list(rule.get("include_kinds") or []),
            exclude_kinds=list(rule.get("exclude_kinds") or []),
            reason=str(rule.get("reason") or ""),
            declared_by=str(declared_by or rule.get("declared_by") or ""),
            declared_at=float(declared_at or rule.get("declared_at") or 0.0))
        hit: List[str] = []
        for case in case_set.cases:
            if case.case_id in touched or not _rule_matches(case, match):
                continue
            case.applicability = applicability
            hit.append(case.case_id)
            touched.append(case.case_id)
        report["applied"].append({"rule_index": index, "matched": hit,
                                  "include_kinds": applicability.include_kinds,
                                  "exclude_kinds": applicability.exclude_kinds,
                                  "reason": applicability.reason})
    report["restricted"] = sorted(
        c.case_id for c in case_set.cases if c.applicability.restricted)
    report["unrestricted_count"] = sum(
        1 for c in case_set.cases if not c.applicability.restricted)
    case_set.updated_at = _now()
    return report


__all__ = [
    "CaseError", "CaseValidationError",
    # 常量
    "CASE_KIND_SEED", "CASE_KIND_TRACE", "CASE_KIND_LLM", "CASE_KIND_MANUAL",
    "CASE_KINDS", "PROV_SEED_PACK", "PROV_TRACE", "PROV_REPO_SAMPLE",
    "PROV_LLM_DRAFT",
    "MIN_CASE_SET_SIZE", "MAX_CASE_SET_SIZE", "SEED_PACK_MIN_SKILLS",
    "MIN_SEED_CASES_PER_SKILL", "CASE_SCHEMA_VERSION", "MAX_STORE_HISTORY",
    "EXPECTED_STATUSES", "EXPECTED_STATUS_ANY", "SIDE_EFFECT_KINDS",
    "SIDE_EFFECT_SOURCES",
    "SIDE_EFFECT_SOURCE_AUTHORED", "SIDE_EFFECT_SOURCE_TRACE",
    "SIDE_EFFECT_SOURCE_NONE",
    "DEFAULT_CASE_ROOT", "CASE_ROOT_ENV", "CASE_BACKEND_ENV", "BACKENDS",
    "BACKEND_JSON", "BACKEND_SQLITE", "DEFAULT_SANDBOX_ROOT",
    "SEED_PACK_PATH", "SEED_PACK_ENV",
    # 模型
    "ProgramStep", "program_from_storage", "program_to_storage",
    "EquivalenceCase", "CaseSet", "build_case_set",
    # 适用性（M4）
    "CaseApplicability", "CANDIDATE_KINDS", "CANDIDATE_KIND_SEED_NATIVE",
    "CANDIDATE_KIND_PATTERN", "CANDIDATE_KIND_IMPLEMENTATION",
    "CANDIDATE_KIND_PROVIDER", "CANDIDATE_KIND_EXPLICIT",
    "normalize_candidate_kind", "candidate_kind_matches",
    "case_applies_to", "applicable_cases", "apply_applicability",
    # 存储
    "CaseStore", "JsonCaseStore", "SqliteCaseStore", "open_case_store",
    "default_case_root",
    # 通道
    "cases_from_trace_set", "trace_set_for", "synthesize_value",
    "slot_names_for_program", "positional_slot_names",
    "concrete_args_provider", "concrete_args_from_rows",
    "derive_sandbox_root", "raw_to_paths", "align_concrete_args",
    "load_seed_pack", "seed_cases_for", "seed_native_program",
    "seed_pack_case_sets", "seed_pack_summary", "seed_pack_skills",
    "seed_pack_capability_ids", "reset_seed_pack_cache",
    "seed_candidate_for",
    "merge_cases", "regenerate_case_set", "case_set_for_capability",
    "case_id_for", "slug_of", "pattern_capability_id", "canonical_json",
]
