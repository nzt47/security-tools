"""ToolDescriptor v2.1 契约模型（TASK-S1-01 / v7.2 §3.2 字段组落地）

设计对齐 v7.2 §3.2 的九组字段：

    meta(id/version/created_at) · origin(source_type/source_id/provenance)
    tenancy(tenant_id/scope)    · capability(name/description/input_schema/output_schema)
    trust(risk_level 四级/data_class 四级/requires_approval)
    runtime(timeout_ms/retry_policy/idempotent)
    evolution(stage 七态/internalize_attempts/shadow_config)
    quality(success_rate/p99_latency/sample_count/regression_baseline_id)  # P7.2-24
    governance(policy_ref/audit_level/undo_hint/compensating_action)

约束（§3.2）：risk=destructive ⇒ requires_approval ∧ undo_hint ∧ compensating_action
全必填；secret ⇒ 禁外部端点；borrowed ⇒ 必记完整轨迹。
ID 规则：``cp.<source_id>.<upstream_id>``。

云枢裁定（本包对设计文档未闭合点的显式化，均在模块/字段注释标注）：
1. **cross-field 不变量不放进本模块**（模型只做字段级域校验：枚举值域/ID 格式/
   数值范围/SemVer），三不变量由 ``validator.py`` 强制、``registry.py`` 写前校验
   兜底——与 TASK-S1-01 §二步骤 1"validator 强制不变量"的分层一致。
2. **None 语义**：设计文档无 risk/data_class 的 "unknown" 取值，v7.2 provenance 才有
   四级 unknown；存量云枢资产 risk/data_class/evolution.stage 全缺（S0-02 摸底），
   若强行给默认值会造成"假安全/假演进"污染。裁定：``risk_level=None``=未评估
   （S1-02 回填目标，校验器出 warning 不出 error）、``data_class=None``=未分级、
   ``evolution.stage=None``=未入轨（缺 30 天零回退/验收门证据，S3 补验，S0-02 §3.4）。
3. **origin.external_endpoint**：§3.2 无独立"外部端点"字段，但约束行要求
   "secret ⇒ 禁外部端点"（§2.5 Router 规则 data_class=secret 且目标外部 ⇒ 直接拒绝）。
   本字段为不变量落点：True=该能力可经外部/远端端点触达（如 sse 远端 MCP server），
   data_class=secret 时校验器拒绝。
4. **evolution.trace_policy**：§3.2 要求 "borrowed ⇒ 必记完整轨迹"，云枢统一 Trace
   台账尚未建成（S0-02 §3.5，S2 前置）；本字段承载"轨迹引用策略"声明
   （如 ``trace:<ledger>:<policy>``），borrowed 态必填非空，S2 台账建成后指向真实 ledger。
5. **audit_level**：设计文档未给枚举值域；裁定 none/summary/full 三级（summary 默认）。
6. 枚举值域纪律：provenance/data_class/risk 四级与七态取值必须与 v7.2 文档一致；
   source_type 取运行时 SOURCE_*（builtin/plugin/mcp/generated/market）∪ 六类适配器
   （mcp/cli/subagent/sdk/rest/skill）∪ manual，双来源枚举（S0-01 术语表待办）在
   descriptor 层以 source_type 归一，细节保留于 origin.source_id/evidence。
"""

from __future__ import annotations

import enum
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─────────────────────────────────────────────────────────────
# 枚举（值域纪律：与 v7.2 文档一致）
# ─────────────────────────────────────────────────────────────


class ProvenanceLevel(str, enum.Enum):
    """provenance 四级（§2.3 manifest / §3.2）"""

    UNKNOWN = "unknown"
    DECLARED = "declared"
    VERIFIED = "verified"
    SIGNED = "signed"


class RiskLevel(str, enum.Enum):
    """risk_level 四级（§3.2 / S1-02 口径：低/中/高/destructive）"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    DESTRUCTIVE = "destructive"


class DataClass(str, enum.Enum):
    """data_class 四级（§3.2 / S1-02 口径：public/internal/confidential/secret）"""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    SECRET = "secret"


class EvolutionStage(str, enum.Enum):
    """v7.2 §3.3 消化状态机七态（S0-02 §3.2 作用域矩阵取值，顺序＝演进主链）"""

    BORROWED = "borrowed"
    MIRRORED = "mirrored"
    SHADOW = "shadow"
    INTERNALIZED = "internalized"
    NATIVE = "native"
    PERMANENT_BORROWED = "permanent_borrowed"
    DEPRECATED = "deprecated"


class SourceType(str, enum.Enum):
    """Descriptor 来源类型（source_type）

    归一化：运行时 SOURCE_* 五类（agent/tools/__init__.py builtin/plugin/mcp/
    generated/market）∪ v7.2 §2.2 六类适配器（mcp/cli/subagent/sdk/rest/skill）
    ∪ manual（本地手工）。L3 资产统一以 ``skill`` 承载轻量视图，其上游类别
    （claude/community/…）细节放 origin.source_id/evidence。
    """

    BUILTIN = "builtin"
    MCP = "mcp"
    SKILL = "skill"
    PLUGIN = "plugin"
    GENERATED = "generated"
    MARKET = "market"
    CLI = "cli"
    SUBAGENT = "subagent"
    SDK = "sdk"
    REST = "rest"
    MANUAL = "manual"


class TenancyScope(str, enum.Enum):
    """scope（§3.7 SKILL.md scope：local/project/org）"""

    LOCAL = "local"
    PROJECT = "project"
    ORG = "org"


class RetryMode(str, enum.Enum):
    """runtime.retry_policy.mode（云枢裁定：none/fixed/exponential）"""

    NONE = "none"
    FIXED = "fixed"
    EXPONENTIAL = "exponential"


class AuditLevel(str, enum.Enum):
    """governance.audit_level（云枢裁定三级，见模块 docstring #5）"""

    NONE = "none"
    SUMMARY = "summary"
    FULL = "full"


# ─────────────────────────────────────────────────────────────
# 常量与正则
# ─────────────────────────────────────────────────────────────

# ID 规则：cp.<source_id>.<upstream_id>（§3.2）。两段均非空、可含 [A-Za-z0-9_.:-]，
# 上游段允许含点（如 cp.filesystem.github.write），禁空白/前后缀点。
_ID_RE = re.compile(
    r"^cp\.[A-Za-z0-9][A-Za-z0-9_.:-]*\.[A-Za-z0-9][A-Za-z0-9_.:-]*$"
)

_SEMVER_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z\-\.]+)?(?:\+[0-9A-Za-z\-\.]+)?$"
)

CP_PREFIX = "cp."
# input_schema 字段级注记键（P7.2-24）
CP_HINT_KEY = "cp.hint"

_RISK_ORDER = ["low", "medium", "high", "destructive"]
_DATA_ORDER = ["public", "internal", "confidential", "secret"]
_PROV_ORDER = ["unknown", "declared", "verified", "signed"]


def _now_iso() -> str:
    return datetime.now().isoformat()


class DescriptorValidationError(Exception):
    """Descriptor 校验失败（模型域 / 不变量 / 写入前校验共用）

    Attributes:
        descriptor_id: 失败对象 capability_id（未解析时为 None）
        errors: 错误清单（一条或多条，均人类可读）
        warnings: 警告清单（不阻断，如 None 语义未回填项）
        code: 错误类别，默认 INVALID_DESCRIPTOR
    """

    def __init__(
        self,
        errors: List[str],
        *,
        descriptor_id: Optional[str] = None,
        warnings: Optional[List[str]] = None,
        code: str = "INVALID_DESCRIPTOR",
    ) -> None:
        self.errors = list(errors)
        self.warnings = list(warnings or [])
        self.descriptor_id = descriptor_id
        self.code = code
        prefix = f"[{descriptor_id}] " if descriptor_id else ""
        super().__init__(f"{prefix}Descriptor 校验失败: {'; '.join(self.errors)}")


def severity_index(level) -> int:
    """风险/数据/来源等级 → 保守序索引（大＝更严格），None 视为最低（未评估）。"""
    if level is None:
        return -1
    v = level.value if isinstance(level, enum.Enum) else str(level)
    for order in (_RISK_ORDER, _DATA_ORDER, _PROV_ORDER):
        if v in order:
            return order.index(v)
    return -1


def stricter(levels) -> Any:
    """取一组等级中最严格者；None 忽略；全 None 返回 None。"""
    present = [lv for lv in levels if lv is not None]
    if not present:
        return None
    return max(present, key=severity_index)


# ─────────────────────────────────────────────────────────────
# 子模型（按 §3.2 字段组）
# ─────────────────────────────────────────────────────────────


class MetaInfo(BaseModel):
    """meta(id/version/created_at)"""

    id: str = Field(..., min_length=len(CP_PREFIX) + 2, max_length=200,
                    description="capability_id，ID 规则 cp.<source_id>.<upstream_id>")
    version: str = Field("0.1.0", description="SemVer（云枢裁定默认 0.1.0）")
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)

    model_config = ConfigDict(extra="ignore")

    @field_validator("id")
    @classmethod
    def _validate_capability_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(
                "capability_id 必须符合 ID 规则 cp.<source_id>.<upstream_id> "
                f"(段内仅允许字母/数字/._:-，禁空白) got: {v!r}"
            )
        return v

    @field_validator("version")
    @classmethod
    def _validate_version(cls, v: str) -> str:
        if not _SEMVER_RE.match(v):
            raise ValueError(f"非法版本号: {v} (应为 MAJOR.MINOR.PATCH)")
        return v


class OriginInfo(BaseModel):
    """origin(source_type/source_id/provenance)

    补充字段（模块 docstring #3/#4）：external_endpoint、manifest_ref、evidence。
    """

    source_type: SourceType = SourceType.BUILTIN
    source_id: str = Field(..., min_length=1, max_length=200,
                           description="来源实体标识（如 mcp:filesystem / skill id）")
    provenance: ProvenanceLevel = ProvenanceLevel.UNKNOWN
    evidence: List[str] = Field(default_factory=list,
                                description="provenance 证据/签名引用，随 level 提升累积")
    # secret ⇒ 禁外部端点 的落点字段（云枢裁定 #3）
    external_endpoint: bool = Field(
        False, description="True=能力可经外部/远端端点触达（secret 时校验拒绝）")
    manifest_ref: str = Field("", description="来源 manifest 引用（§2.3 source.manifest）")

    model_config = ConfigDict(extra="ignore")


class TenancyInfo(BaseModel):
    """tenancy(tenant_id/scope)

    tenant_id 默认 "default"；S1-02 按 P7.2-08 以 workspace-hash 覆写。
    """

    tenant_id: str = Field("default", min_length=1, max_length=128)
    scope: TenancyScope = TenancyScope.PROJECT

    model_config = ConfigDict(extra="ignore")


class CapabilityInfo(BaseModel):
    """capability(name/description/input_schema/output_schema)

    input_schema 支持字段级 cp.hint 注记（P7.2-24）：property schema 内允许出现
    "cp.hint" 键（值为 dict），用 extract_cp_hints() 提取。
    """

    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=4000)
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")


class TrustInfo(BaseModel):
    """trust(risk_level 四级/data_class 四级/requires_approval)

    risk_level/data_class 允许 None＝未评估/未分级（云枢裁定 #2，S1-02 回填）。
    destructive ⇒ requires_approval 必须为 True（validator 强制）。
    """

    risk_level: Optional[RiskLevel] = None
    data_class: Optional[DataClass] = None
    requires_approval: bool = Field(False, description="destructive 必为 True")

    model_config = ConfigDict(extra="ignore")


class RetryPolicy(BaseModel):
    """runtime.retry_policy（云枢裁定 mode: none/fixed/exponential）"""

    mode: RetryMode = RetryMode.NONE
    max_retries: int = Field(0, ge=0)
    backoff_ms: int = Field(0, ge=0)
    backoff_factor: float = Field(1.0, ge=1.0, le=10.0)

    model_config = ConfigDict(extra="ignore")


class RuntimeInfo(BaseModel):
    """runtime(timeout_ms/retry_policy/idempotent)"""

    timeout_ms: int = Field(60000, ge=0, le=3_600_000)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    idempotent: bool = False

    model_config = ConfigDict(extra="ignore")


class EvolutionInfo(BaseModel):
    """evolution(stage 七态/internalize_attempts/shadow_config)

    stage None=未入轨（云枢裁定 #2）；trace_policy 承载 borrowed 轨迹引用策略（#4）。
    """

    stage: Optional[EvolutionStage] = None
    internalize_attempts: int = Field(0, ge=0)
    shadow_config: Dict[str, Any] = Field(default_factory=dict)
    trace_policy: str = Field("", description="borrowed 必填：轨迹引用策略")

    model_config = ConfigDict(extra="ignore")


class QualityInfo(BaseModel):
    """quality(success_rate/p99_latency/sample_count/regression_baseline_id)

    regression_baseline_id 为 P7.2-24 增补。
    """

    success_rate: float = Field(0.0, ge=0.0, le=1.0)
    p99_latency_ms: float = Field(0.0, ge=0.0)
    sample_count: int = Field(0, ge=0)
    regression_baseline_id: str = Field("", description="回归基线指针（P7.2-24）")

    model_config = ConfigDict(extra="ignore")


class GovernanceInfo(BaseModel):
    """governance(policy_ref/audit_level/undo_hint/compensating_action)"""

    policy_ref: str = Field("", description="策略引用（§5 Policy id/OPA 包）")
    audit_level: AuditLevel = AuditLevel.SUMMARY
    undo_hint: str = Field("", description="destructive 必填：撤销指引")
    compensating_action: str = Field("", description="destructive 必填：补偿动作")

    model_config = ConfigDict(extra="ignore")


# ─────────────────────────────────────────────────────────────
# 主模型
# ─────────────────────────────────────────────────────────────


class ToolDescriptor(BaseModel):
    """ToolDescriptor v2.1 — 覆盖 §3.2 全部九个字段组"""

    meta: MetaInfo
    origin: OriginInfo
    tenancy: TenancyInfo = Field(default_factory=TenancyInfo)
    capability: CapabilityInfo
    trust: TrustInfo = Field(default_factory=TrustInfo)
    runtime: RuntimeInfo = Field(default_factory=RuntimeInfo)
    evolution: EvolutionInfo = Field(default_factory=EvolutionInfo)
    quality: QualityInfo = Field(default_factory=QualityInfo)
    governance: GovernanceInfo = Field(default_factory=GovernanceInfo)

    model_config = ConfigDict(extra="ignore")

    # ── 便捷属性 ──

    @property
    def capability_id(self) -> str:
        """capability_id（= meta.id）"""
        return self.meta.id

    @property
    def is_destructive(self) -> bool:
        return self.trust.risk_level == RiskLevel.DESTRUCTIVE

    @property
    def is_secret(self) -> bool:
        return self.trust.data_class == DataClass.SECRET

    @property
    def is_borrowed(self) -> bool:
        return self.evolution.stage == EvolutionStage.BORROWED

    @property
    def is_external(self) -> bool:
        """是否经外部/远端端点触达"""
        return self.origin.external_endpoint

    def touch(self) -> None:
        self.meta.updated_at = _now_iso()

    # ── 序列化 ──

    def to_storage_dict(self) -> Dict[str, Any]:
        """JSON 可序列化（枚举 → 字符串）"""
        return self.model_dump(mode="json")

    @classmethod
    def from_storage_dict(cls, data: Dict[str, Any]) -> "ToolDescriptor":
        """从存储字典恢复（缺省组回填默认工厂）"""
        return cls(**data)


def extract_cp_hints(input_schema: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """提取 input_schema 字段级 cp.hint 注记（P7.2-24）

    Returns:
        {属性名: cp.hint dict}；注记键必须为 dict，非 dict 时计入提示性异常
        由调用方决定处置（validator 将其判为错误）。
    """
    hints: Dict[str, Dict[str, Any]] = {}
    props = (input_schema or {}).get("properties") or {}
    if not isinstance(props, dict):
        return hints
    for prop_name, prop_schema in props.items():
        if not isinstance(prop_schema, dict):
            continue
        hint = prop_schema.get(CP_HINT_KEY)
        if isinstance(hint, dict):
            hints[str(prop_name)] = hint
    return hints
