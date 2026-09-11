"""记忆四层模型（v7.2 §4.3 四层 + TTL；条目字段对齐 §3.11 MemoryEntry）

设计来源
--------
- v7.2 §4.3 记忆层：四层（工作/事实/偏好/策略）+ TTL；召回优先级
  ``project 事实 > global 偏好，同级新者胜``；遗忘三触发（成功率 30 天 < 基线×0.7 /
  来源失效 / 删除权），快照留 30 天。
- v7.2 §3.11 实体最小规格 —— ``MemoryEntry`` 字段逐字对齐
  （id / tenant_id / subject_id / type / content_redacted / content_hash / scope /
  source_task_id / confidence / created_at / last_hit_at / ttl_expires_at /
  forget_candidate / schema_version）。
- v7.2 P7.2-08 IDE 租户映射与记忆隔离：workspace(repository) = 逻辑租户
  （tenant_id = workspace-hash），subject_id = 登录用户。
- v7.2 §3.4：脱敏发生在哈希之前 —— 故 ``content_hash`` 恒由**脱敏后**文本派生。
- v7.2 P7.2-10 组装注入优先级：策略记忆（规避规则）> 事实记忆（项目约束）>
  偏好记忆（风格）。

职责边界
--------
本模块**只定义模型与策略值对象**（纯数据 + 纯函数，无 I/O、不依赖任何存储）：
- 存储落地（分片 SQLite，复用既有 ``LongTermMemory`` 引擎）见 ``agent.memory.layered_store``
- 隔离判定 / 召回可见性见 ``agent.memory.tenancy``
- 遗忘执行（三触发 / TTL 降级 / 快照 / 删除权）见 ``agent.memory.forgetting``

可调参数（TTL）走环境变量，非法值回退默认（批次总表 §三 硬约束 3）：

===========================  ===========================  ==================
记忆层                       环境变量                      默认 TTL
===========================  ===========================  ==================
working                      ``MEMORY_TTL_WORKING``        8 小时
fact                         ``MEMORY_TTL_FACT``           180 天
preference                   ``MEMORY_TTL_PREFERENCE``     365 天
strategy                     ``MEMORY_TTL_STRATEGY``       365 天
===========================  ===========================  ==================
"""

import enum
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

__all__ = [
    "MEMORY_SCHEMA_VERSION",
    "GLOBAL_SCOPE",
    "PROJECT_SCOPE_PREFIX",
    "MemoryEntryError",
    "MemoryType",
    "IsolationKind",
    "LayerPolicy",
    "LAYER_POLICY",
    "LAYER_ORDER",
    "SCOPE_RANK",
    "MemoryEntry",
    "new_memory_id",
    "coerce_memory_type",
    "project_scope",
    "is_project_scope",
    "scope_workspace_id",
    "scope_kind",
    "default_scope_for",
    "ttl_seconds_for",
    "expiry_for",
    "recall_priority_key",
    "sort_for_recall",
]


#: §3.11 MemoryEntry.schema_version 当前值
MEMORY_SCHEMA_VERSION = 1

#: 全局作用域（偏好记忆随 subject 跨租户携带时的落点，§4.3 / P7.2-08）
GLOBAL_SCOPE = "global"

#: 项目作用域前缀；完整形态 ``project:<workspace-hash>``（§3.11）
PROJECT_SCOPE_PREFIX = "project:"

#: 项目作用域 / 全局作用域的排序权重（project 优先，§4.3）
SCOPE_RANK: Dict[str, int] = {"project": 0, "global": 1}


class MemoryEntryError(ValueError):
    """记忆条目模型非法（字段缺失 / 枚举越界 / 作用域格式错误）"""


class MemoryType(str, enum.Enum):
    """记忆四层（§4.3）——``str`` 混入便于 JSON 往返与日志可读"""

    WORKING = "working"        # 工作记忆：任务内短时记忆，短 TTL
    FACT = "fact"              # 事实记忆：项目约束，按租户隔离，长 TTL
    PREFERENCE = "preference"  # 偏好记忆：风格偏好，跟随 subject 跨租户
    STRATEGY = "strategy"      # 策略记忆：规避规则，按租户隔离（org 级只读下发）

    @classmethod
    def coerce(cls, value: Any) -> "MemoryType":
        """把 str / MemoryType 归一为 MemoryType；越界抛 MemoryEntryError"""
        return coerce_memory_type(value)


def coerce_memory_type(value: Any) -> MemoryType:
    """把 str / MemoryType 归一为 MemoryType（大小写与空白不敏感）"""
    if isinstance(value, MemoryType):
        return value
    key = str(value or "").strip().lower()
    for member in MemoryType:
        if member.value == key:
            return member
    raise MemoryEntryError(
        "未知记忆层 type=%r（合法值：%s）"
        % (value, ", ".join(m.value for m in MemoryType))
    )


class IsolationKind(str, enum.Enum):
    """隔离维度（P7.2-08 铁律）"""

    TENANT = "tenant"    # 按 tenant_id（= workspace-hash）隔离：事实 / 策略 / 工作
    SUBJECT = "subject"  # 跟随 subject_id 跨租户携带：偏好


@dataclass(frozen=True)
class LayerPolicy:
    """单层记忆策略（§4.3 + P7.2-08 + P7.2-10 的合并表达）

    Attributes:
        memory_type: 所属层
        isolation: 隔离维度（TENANT=按租户隔离，SUBJECT=随 subject 跨租户）
        scope_kind: 默认作用域种类（``project`` / ``global``）
        default_ttl_seconds: 默认 TTL 秒数（None = 永不到期）
        ttl_env: 覆盖 TTL 的环境变量名
        injection_rank: 组装注入优先级（越小越优先，P7.2-10）
        org_level_default: 该层写入是否默认 org 级（企业策略记忆只读下发）
        subject_write_forbidden: 个人通道是否禁止写该层（策略层铁律）
    """

    memory_type: MemoryType
    isolation: IsolationKind
    scope_kind: str
    default_ttl_seconds: Optional[float]
    ttl_env: str
    injection_rank: int
    org_level_default: bool = False
    subject_write_forbidden: bool = False

    @property
    def carried_across_tenants(self) -> bool:
        """该层记忆是否跨租户携带（P7.2-08：仅偏好为真）"""
        return self.isolation is IsolationKind.SUBJECT

    @property
    def tenant_isolated(self) -> bool:
        """该层记忆是否按租户隔离（P7.2-08：事实/策略/工作为真）"""
        return self.isolation is IsolationKind.TENANT


#: 四层策略表 —— 全任务唯一的层语义来源（隔离矩阵的可执行定义）
LAYER_POLICY: Dict[MemoryType, LayerPolicy] = {
    MemoryType.WORKING: LayerPolicy(
        memory_type=MemoryType.WORKING,
        isolation=IsolationKind.TENANT,
        scope_kind="project",
        default_ttl_seconds=8 * 3600.0,          # 工作记忆短 TTL（§4.3）
        ttl_env="MEMORY_TTL_WORKING",
        injection_rank=3,
    ),
    MemoryType.FACT: LayerPolicy(
        memory_type=MemoryType.FACT,
        isolation=IsolationKind.TENANT,
        scope_kind="project",
        default_ttl_seconds=180 * 86400.0,       # 事实记忆长 TTL（§4.3）
        ttl_env="MEMORY_TTL_FACT",
        injection_rank=1,
    ),
    MemoryType.PREFERENCE: LayerPolicy(
        memory_type=MemoryType.PREFERENCE,
        isolation=IsolationKind.SUBJECT,
        scope_kind="global",                     # 偏好恒 global：随 subject 跨租户携带
        default_ttl_seconds=365 * 86400.0,
        ttl_env="MEMORY_TTL_PREFERENCE",
        injection_rank=2,
    ),
    MemoryType.STRATEGY: LayerPolicy(
        memory_type=MemoryType.STRATEGY,
        isolation=IsolationKind.TENANT,
        scope_kind="project",
        default_ttl_seconds=365 * 86400.0,       # 策略记忆长 TTL（§4.3）
        ttl_env="MEMORY_TTL_STRATEGY",
        injection_rank=0,                        # P7.2-10：规避规则最优先
        org_level_default=True,                  # 企业阶段策略记忆 org 级只读下发
        subject_write_forbidden=True,            # 铁律：个人写入不得污染策略层
    ),
}

#: 四层展示顺序（策略 → 事实 → 偏好 → 工作，与注入优先级一致）
LAYER_ORDER: Tuple[MemoryType, ...] = (
    MemoryType.STRATEGY,
    MemoryType.FACT,
    MemoryType.PREFERENCE,
    MemoryType.WORKING,
)


# ════════════════════════════════════════════════════════════
#  作用域（§3.11 scope: "project:<hash>" | "global"）
# ════════════════════════════════════════════════════════════


def project_scope(workspace_id: str) -> str:
    """构造项目作用域 ``project:<workspace-hash>``（§3.11）"""
    ws = str(workspace_id or "").strip()
    if not ws:
        raise MemoryEntryError("project_scope 需要非空 workspace_id（P7.1-19 不变量）")
    return PROJECT_SCOPE_PREFIX + ws


def is_project_scope(scope: Any) -> bool:
    """是否为 ``project:<hash>`` 形态"""
    return str(scope or "").startswith(PROJECT_SCOPE_PREFIX)


def scope_workspace_id(scope: Any) -> Optional[str]:
    """取出 ``project:<hash>`` 中的 hash；非项目作用域返回 None"""
    raw = str(scope or "")
    if not raw.startswith(PROJECT_SCOPE_PREFIX):
        return None
    ws = raw[len(PROJECT_SCOPE_PREFIX):].strip()
    return ws or None


def scope_kind(scope: Any) -> str:
    """作用域种类：``project`` / ``global``（未知形态按 global 处理并告警）"""
    if is_project_scope(scope):
        return "project"
    raw = str(scope or "").strip()
    if raw == GLOBAL_SCOPE:
        return "global"
    if raw:
        logger.warning(log_dict({
            "module_name": "memory.taxonomy",
            "action": "scope.unknown",
            "msg": "[taxonomy] 未知 scope=%r，按 global 处理" % raw,
        }))
    return "global"


def default_scope_for(memory_type: Any, workspace_id: str = "") -> str:
    """按层推导默认作用域

    - 项目级层（工作/事实/策略）：有 workspace_id → ``project:<ws>``，否则 ``global``
    - 偏好层：恒 ``global``（P7.2-08：偏好跟随 subject 跨租户携带）
    """
    mt = coerce_memory_type(memory_type)
    policy = LAYER_POLICY[mt]
    if policy.scope_kind != "project":
        return GLOBAL_SCOPE
    ws = str(workspace_id or "").strip()
    return project_scope(ws) if ws else GLOBAL_SCOPE


# ════════════════════════════════════════════════════════════
#  TTL（§4.3：四层 + TTL；工作短、事实/策略长，可配）
# ════════════════════════════════════════════════════════════


def _env_seconds(name: str, default: Optional[float]) -> Optional[float]:
    """读取秒级环境变量；缺失/非法/非正 → 回退默认（批次总表 §三 硬约束 3）"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(log_dict({
            "module_name": "memory.taxonomy",
            "action": "ttl.env.invalid",
            "msg": "[taxonomy] %s=%r 非法，回退默认 %s" % (name, raw, default),
        }))
        return default
    if value <= 0:
        logger.warning(log_dict({
            "module_name": "memory.taxonomy",
            "action": "ttl.env.invalid",
            "msg": "[taxonomy] %s=%r 非正，回退默认 %s" % (name, raw, default),
        }))
        return default
    return value


def ttl_seconds_for(memory_type: Any, override: Optional[float] = None) -> Optional[float]:
    """该层生效 TTL 秒数：显式 override > 环境变量 > 层默认

    ``override=None`` 表示"未指定"（走环境变量/默认）；
    显式传 ``0`` 或负值表示**不过期**（返回 None）。
    """
    mt = coerce_memory_type(memory_type)
    if override is not None:
        try:
            val = float(override)
        except (TypeError, ValueError):
            return LAYER_POLICY[mt].default_ttl_seconds
        return None if val <= 0 else val
    policy = LAYER_POLICY[mt]
    return _env_seconds(policy.ttl_env, policy.default_ttl_seconds)


def expiry_for(
    memory_type: Any,
    *,
    ttl_seconds: Optional[float] = None,
    created_at: Optional[float] = None,
) -> Optional[float]:
    """由创建时刻 + TTL 推导 ``ttl_expires_at``（None = 永不到期）"""
    ttl = ttl_seconds_for(memory_type, ttl_seconds)
    if ttl is None:
        return None
    base = time.time() if created_at is None else float(created_at)
    return base + ttl


# ════════════════════════════════════════════════════════════
#  §3.11 MemoryEntry
# ════════════════════════════════════════════════════════════


def new_memory_id() -> str:
    """生成记忆条目 id（``mem_`` 前缀 + 16 位 hex，与既有 trace_id 风格一致）"""
    return "mem_" + uuid.uuid4().hex[:16]


@dataclass
class MemoryEntry:
    """记忆条目（v7.2 §3.11 最小规格 + 云枢工程扩展字段）

    §3.11 字段（名称与语义逐字对齐）：

        id / tenant_id / subject_id / type / content_redacted / content_hash /
        scope / source_task_id / confidence / created_at / last_hit_at /
        ttl_expires_at / forget_candidate / schema_version

    云枢工程扩展字段（**全部带默认值**，故按 §3.11 最小集构造亦合法）：
        source_capability_id（遗忘触发②"来源失效"的指针）/ org_level（org 级只读下发）/
        degraded + degradation_reason（显式降级标注，hard constraint 1）/
        forget_reason / hit_count / source_trace_id / extra。

    Note:
        ``content_redacted`` 是**脱敏后**文本；``content_hash`` 恒由脱敏后文本派生
        （§3.4：脱敏发生在哈希之前），故空 ``content_hash`` 会在 ``__post_init__``
        自动补齐。
    """

    # ── §3.11 必填字段 ──
    id: str
    tenant_id: str
    subject_id: str
    type: MemoryType
    content_redacted: str
    content_hash: str
    scope: str

    # ── §3.11 带默认字段 ──
    source_task_id: str = ""
    confidence: float = 0.5
    created_at: float = field(default_factory=time.time)
    last_hit_at: float = 0.0
    ttl_expires_at: Optional[float] = None
    forget_candidate: bool = False
    schema_version: int = MEMORY_SCHEMA_VERSION

    # ── 云枢工程扩展（默认值齐备，兼容 §3.11 最小构造）──
    source_capability_id: str = ""
    source_trace_id: str = ""
    org_level: bool = False
    degraded: bool = False
    degradation_reason: str = ""
    forget_reason: str = ""
    hit_count: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.type = coerce_memory_type(self.type)
        self.id = str(self.id or "").strip() or new_memory_id()
        self.tenant_id = str(self.tenant_id or "").strip()
        self.subject_id = str(self.subject_id or "").strip()
        self.content_redacted = "" if self.content_redacted is None else str(self.content_redacted)
        self.source_task_id = str(self.source_task_id or "")
        self.source_capability_id = str(self.source_capability_id or "")
        self.source_trace_id = str(self.source_trace_id or "")
        self.forget_reason = str(self.forget_reason or "")
        self.degradation_reason = str(self.degradation_reason or "")
        try:
            self.confidence = float(self.confidence)
        except (TypeError, ValueError):
            self.confidence = 0.5
        self.confidence = min(1.0, max(0.0, self.confidence))
        try:
            self.created_at = float(self.created_at)
        except (TypeError, ValueError):
            self.created_at = time.time()
        try:
            self.last_hit_at = float(self.last_hit_at or 0.0)
        except (TypeError, ValueError):
            self.last_hit_at = 0.0
        if self.ttl_expires_at is not None:
            try:
                self.ttl_expires_at = float(self.ttl_expires_at)
            except (TypeError, ValueError):
                self.ttl_expires_at = None
        # scope 归一：空 → 按层推导；project 形态需带 hash
        scope_raw = str(self.scope or "").strip()
        if not scope_raw:
            scope_raw = default_scope_for(self.type, self.tenant_id)
        if is_project_scope(scope_raw) and scope_workspace_id(scope_raw) is None:
            raise MemoryEntryError("scope=%r 缺少 workspace-hash（形态 project:<hash>）" % self.scope)
        self.scope = scope_raw
        # §3.4：脱敏先于哈希 —— content_hash 由 content_redacted 派生
        if not self.content_hash:
            from agent.observability.trace_v2 import hash_content

            self.content_hash = hash_content(self.content_redacted)

    # ── 衍生属性 ──

    @property
    def is_project_scoped(self) -> bool:
        return is_project_scope(self.scope)

    @property
    def scope_workspace_id(self) -> Optional[str]:
        return scope_workspace_id(self.scope)

    @property
    def isolation(self) -> IsolationKind:
        return LAYER_POLICY[self.type].isolation

    @property
    def carried_across_tenants(self) -> bool:
        """偏好层为真：跨租户携带（P7.2-08）"""
        return LAYER_POLICY[self.type].carried_across_tenants

    @property
    def tenant_isolated(self) -> bool:
        """事实/策略/工作层为真：按租户隔离（P7.2-08）"""
        return LAYER_POLICY[self.type].tenant_isolated

    @property
    def is_org_level(self) -> bool:
        return bool(self.org_level)

    # ── 时序 ──

    def age_seconds(self, now: Optional[float] = None) -> float:
        """条目存在时长（秒）；时钟可注入，避免真实时钟边界（START 坑 3）"""
        return max(0.0, (time.time() if now is None else float(now)) - self.created_at)

    def is_expired(self, now: Optional[float] = None) -> bool:
        """TTL 是否到期（``ttl_expires_at=None`` 恒为未到期）"""
        if self.ttl_expires_at is None:
            return False
        return (time.time() if now is None else float(now)) >= self.ttl_expires_at

    def record_hit(self, now: Optional[float] = None) -> None:
        """记录一次命中（§3.11 last_hit_at）"""
        self.last_hit_at = time.time() if now is None else float(now)
        self.hit_count = int(self.hit_count or 0) + 1

    def mark_forget_candidate(self, reason: str) -> None:
        """标记为遗忘候选（§3.11 forget_candidate；不删除，等待快照/删除）"""
        self.forget_candidate = True
        self.forget_reason = str(reason or "")

    def clear_forget_candidate(self) -> None:
        """撤销遗忘候选标记（来源恢复 / 成功率回升）"""
        self.forget_candidate = False
        self.forget_reason = ""

    # ── 序列化 ──

    def to_dict(self) -> Dict[str, Any]:
        """完整字典（含 §3.11 全字段 + 扩展字段）；``type`` 落字符串"""
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
            "type": self.type.value,
            "content_redacted": self.content_redacted,
            "content_hash": self.content_hash,
            "scope": self.scope,
            "source_task_id": self.source_task_id,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "last_hit_at": self.last_hit_at,
            "ttl_expires_at": self.ttl_expires_at,
            "forget_candidate": bool(self.forget_candidate),
            "schema_version": int(self.schema_version),
            "source_capability_id": self.source_capability_id,
            "source_trace_id": self.source_trace_id,
            "org_level": bool(self.org_level),
            "degraded": bool(self.degraded),
            "degradation_reason": self.degradation_reason,
            "forget_reason": self.forget_reason,
            "hit_count": int(self.hit_count or 0),
            "extra": dict(self.extra or {}),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        """从字典还原（容忍缺失字段；``extra`` 中的未知键原样保留）"""
        payload = dict(data or {})
        known = {
            "id", "tenant_id", "subject_id", "type", "content_redacted", "content_hash",
            "scope", "source_task_id", "confidence", "created_at", "last_hit_at",
            "ttl_expires_at", "forget_candidate", "schema_version",
            "source_capability_id", "source_trace_id", "org_level", "degraded",
            "degradation_reason", "forget_reason", "hit_count", "extra",
        }
        extra = dict(payload.get("extra") or {})
        for key in list(payload.keys()):
            if key not in known:
                extra.setdefault(key, payload.pop(key))
        return cls(
            id=str(payload.get("id") or new_memory_id()),
            tenant_id=str(payload.get("tenant_id") or ""),
            subject_id=str(payload.get("subject_id") or ""),
            type=coerce_memory_type(payload.get("type")),
            content_redacted=str(payload.get("content_redacted") or ""),
            content_hash=str(payload.get("content_hash") or ""),
            scope=str(payload.get("scope") or ""),
            source_task_id=str(payload.get("source_task_id") or ""),
            confidence=payload.get("confidence", 0.5),
            created_at=payload.get("created_at") or time.time(),
            last_hit_at=payload.get("last_hit_at") or 0.0,
            ttl_expires_at=payload.get("ttl_expires_at"),
            forget_candidate=bool(payload.get("forget_candidate", False)),
            schema_version=int(payload.get("schema_version") or MEMORY_SCHEMA_VERSION),
            source_capability_id=str(payload.get("source_capability_id") or ""),
            source_trace_id=str(payload.get("source_trace_id") or ""),
            org_level=bool(payload.get("org_level", False)),
            degraded=bool(payload.get("degraded", False)),
            degradation_reason=str(payload.get("degradation_reason") or ""),
            forget_reason=str(payload.get("forget_reason") or ""),
            hit_count=int(payload.get("hit_count") or 0),
            extra=extra,
        )

    def to_persist_metadata(self) -> Dict[str, Any]:
        """落库用 metadata 载荷（复用既有 ``LongTermMemory.metadata`` JSON 列）"""
        return self.to_dict()

    def to_audit_leaf(self) -> Dict[str, Any]:
        """审计载荷叶子字段（**不含内容、不含原始 subject_id**）

        审计链是 append-only 的：一旦落入原始标识符或内容，事后无法在不破坏链条的
        前提下撤回（§3.5 self_hash 覆盖 subject/actor）。故入链一律只带
        ``content_hash`` 等不可还原的叶字段；主体标识只以伪名出现（§8 被遗忘权）。
        """
        return {
            "memory_id": self.id,
            "type": self.type.value,
            "scope": self.scope,
            "tenant_id": self.tenant_id,
            "content_hash": self.content_hash,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "ttl_expires_at": self.ttl_expires_at,
            "forget_candidate": bool(self.forget_candidate),
            "forget_reason": self.forget_reason,
            "source_capability_id": self.source_capability_id,
            "source_task_id": self.source_task_id,
            "org_level": bool(self.org_level),
            "degraded": bool(self.degraded),
            "schema_version": int(self.schema_version),
        }

    def with_updates(self, **changes: Any) -> "MemoryEntry":
        """派生新条目（不改动原对象）"""
        return replace(self, **changes)

    def recall_priority_key(self) -> Tuple[int, int, float, str]:
        """召回优先级键（升序：越靠前越优先，§4.3 + P7.2-10）"""
        return recall_priority_key(self)


def recall_priority_key(entry: MemoryEntry) -> Tuple[int, int, float, str]:
    """召回优先级键（升序排序；越小的元组越优先）

    排序口径（§4.3 + P7.2-10 的合并表达）::

        (注入层序, 作用域序, -created_at, id)

    1. **层序**（P7.2-10）：策略 0 < 事实 1 < 偏好 2 < 工作 3；故
       ``策略记忆 > 事实记忆 > 偏好记忆`` 恒成立，与作用域无关。
    2. **作用域序**（§4.3）：project 0 < global 1；故同层内
       ``project 事实 > global 事实``。
       跨层组合 ``project 事实 (1,0,…)`` 亦优于 ``global 偏好 (2,1,…)``，
       即 §4.3 的 ``project 事实 > global 偏好``。
    3. **同级新者胜**（§4.3）：同层同作用域下 ``created_at`` 大者优先。
    4. ``id`` 兜底，保证排序**全序且确定**（同刻创建条目顺序稳定）。
    """
    mt = coerce_memory_type(entry.type)
    return (
        LAYER_POLICY[mt].injection_rank,
        SCOPE_RANK.get(scope_kind(entry.scope), 1),
        -float(entry.created_at or 0.0),
        str(entry.id or ""),
    )


def sort_for_recall(entries: List[MemoryEntry]) -> List[MemoryEntry]:
    """按 §4.3 召回优先级排序（返回新列表，不改动入参）"""
    return sorted(list(entries or []), key=recall_priority_key)
