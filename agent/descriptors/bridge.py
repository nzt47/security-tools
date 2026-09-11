"""现有资产 → Descriptor 视图桥接（TASK-S1-01 步骤 2，只读、不改存量主轨）

双写视图（advisory 语义）：
    - MCP list_tools 结果 → descriptor（source_type=mcp，provenance=declared，
      stage=borrowed——外部上游接入即借，S0-02 §3.2 作用域矩阵 L1 行）；
    - 内置运行时工具 → descriptor（source_type=builtin，provenance=verified——
      本地代码即证据；stage=None——native 需 30 天零回退台账，缺证据不置位）；
    - 外部 SKILL.md / skills_mgmt 资产 → 轻量 descriptor 视图（source_type=skill，
      evolution.stage 经 map_skill_stage 映射自 TASK-S0-02 状态机作用域矩阵
      §3.2/§3.4：外来导入≈borrowed 特例、deprecated 同义复用、published 不自动
      internalized、本土资产留 None 待 S3 补验）。

守则（对齐任务与 S0-02）：
    - 本模块**绝不反向写 Skill/工具主轨**（Skill↔Descriptor 单向只读映射）；
    - 单条转换/注册失败 → 记入 error 清单继续（advisory），不阻断技能中心主流程；
    - 模块级零依赖 skills_mgmt（duck-typed；bridge_to_skill 在函数体内懒加载）。
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

from .models import (
    CapabilityInfo,
    DataClass,
    EvolutionInfo,
    EvolutionStage,
    GovernanceInfo,
    MetaInfo,
    OriginInfo,
    ProvenanceLevel,
    QualityInfo,
    RiskLevel,
    RuntimeInfo,
    SourceType,
    TenancyInfo,
    ToolDescriptor,
    TrustInfo,
)
from .validator import DescriptorValidationError

logger = logging.getLogger(__name__)

# 外来导入判定关键词（S0-02 §3.4 外来导入近似 borrowed 的 L3 特例）
_EXTERNAL_CATEGORIES = {"claude", "community", "mcp", "ai_generated"}
_LOCAL_SOURCES = {"manual", "builtin", "knowledge_distill", "process_distill"}
_EXTERNAL_SOURCE_PREFIXES = ("github:", "url:", "http://", "https://", "market:", "registry:")
_EXTERNAL_SOURCES = {"external_agent", "install", "install_from_zip", "market"}

# 内置工具默认超时（运行时无声明，取进程默认 60s）
_BUILTIN_TIMEOUT_MS = 60_000


def sanitize_id_part(value: str) -> str:
    """capability_id 段清洗：小写、非 [a-z0-9_.-] → '-'; 折叠并去首尾分隔符"""
    s = re.sub(r"[^0-9A-Za-z_.\-]+", "-", (value or "").strip().lower())
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("._-")
    return s[:120]


# ═════════════════════════════════════════════════════════════
# borrowed 轨迹策略：真实统一轨迹台账引用（TASK-S3-01 / 消费 S2-01 遗留 #9）
# ═════════════════════════════════════════════════════════════

#: 统一轨迹台账（S2-01 交付）——append-only 表，与既有 `tool_traces` **同库**
TRACE_LEDGER_TABLE = "unified_traces"
#: 台账数据库（相对仓库根；S2-01 明确「不新增第二存储介质/第二存储轨」）
TRACE_LEDGER_DB = "agent/data/tool_trace.db"
#: 按能力取轨迹的读接口（`list_by_capability(capability_id, limit)`，S3 挖掘数据源）
TRACE_LEDGER_READER = "UnifiedTraceStore.list_by_capability"


def ledger_trace_policy(*, source: str, capability_id: str,
                        route: str = "") -> str:
    """borrowed/opaque 能力的 ``evolution.trace_policy``：**真实台账引用**。

    S1-01/S2 期该字段承载的是**占位串**（``…call-side(S2-ledger-pending)`` /
    ``…import-ledger(S2-pending)``）——当时统一台账尚未建成，只能声明"策略待定"。
    S2-01 已交付统一台账与读接口，S3-01 据此把默认串切换为可反查的真实引用：

    ``trace:<source>[:<route>]:ledger=<表>@<库>#capability_id=<能力ID>#read=<读接口>``

    四个要素齐备 ⇒ 人工或工具都能据此回答「该 borrowed 能力的完整轨迹落在哪张表、
    用哪个读接口、按什么键取」；``capability_id`` 即 join 键，与
    ``capability_reference()`` / `data/descriptors.json` 台账同源（S3-01 步骤 1 的
    能力地图统一口径）。

    Args:
        source: 来源段（MCP server 名 / `skill-import` 等），经 ``sanitize_id_part`` 清洗
        capability_id: 该能力的 canonical capability_id（join 键）
        route: 可选的接入路径标注（如 `call-side` / `import-ledger`），空则省略
    """
    src = sanitize_id_part(source) or "unknown"
    head = f"trace:{src}"
    if route:
        head += f":{sanitize_id_part(route) or route}"
    cid = str(capability_id or "").strip()
    return (f"{head}:ledger={TRACE_LEDGER_TABLE}@{TRACE_LEDGER_DB}"
            f"#capability_id={cid}#read={TRACE_LEDGER_READER}")


# ═════════════════════════════════════════════════════════════
# 运行时工具名 → capability_id（TASK-S3-01 / 消费 S2-01 遗留 #1）
# ═════════════════════════════════════════════════════════════

#: 名字扫描索引缓存（键 = (registry 实例 id, registry.count())）
_NAME_INDEX_CACHE: Dict[Any, Dict[str, str]] = {}
_NAME_INDEX_CACHE_MAX = 16
_NAME_INDEX_LOCK = threading.Lock()

# 解析来源标注（供用例/报告断言「改写确实发生了」）
RESOLVED_EMPTY = "empty"
RESOLVED_ALREADY_CANONICAL = "already_canonical"
RESOLVED_ALIAS = "alias"
RESOLVED_REGISTRY_ID = "registry_id"
RESOLVED_REGISTRY_NAME = "registry_name"
RESOLVED_DERIVED = "derived"
RESOLVED_FALLBACK = "advisory_fallback"


def canonical_capability_id(tool_name: str, *, source_type: str = "builtin",
                            source_id: str = "builtin") -> str:
    """运行时工具名 → canonical capability_id（**确定性派生，不查台账**）

    与 `descriptor_from_builtin_tool` / `descriptor_from_mcp_tool` 的 id 构式同构
    （``cp.<source_id>.<sanitize(name)>``）——这是 S2-01 交付的 join 键口径
    （``capability_reference()`` 用同一键查台账）。已是 ``cp.*`` 形式的原样返回。
    """
    raw = str(tool_name or "").strip()
    if not raw:
        return ""
    if raw.startswith("cp."):
        return raw
    safe_src = (sanitize_id_part(source_id) or sanitize_id_part(source_type)
                or "builtin")
    safe_name = sanitize_id_part(raw)
    if not safe_name:
        return ""
    return f"cp.{safe_src}.{safe_name}"


def _name_index(registry: Any) -> Dict[str, str]:
    """`capability.name`（原样 + 小写）→ capability_id 的映射（带缓存）

    仅用于**派生候选与 alias 都未命中**时的兜底解析（如名字与 id 段不一致的
    外来登记项）。缓存键含 ``registry.count()``，登记数量变化即自动失效。
    """
    try:
        stamp = (id(registry), int(registry.count()))
    except Exception:  # noqa: BLE001
        stamp = (id(registry), -1)
    with _NAME_INDEX_LOCK:
        cached = _NAME_INDEX_CACHE.get(stamp)
    if cached is not None:
        return cached
    index: Dict[str, str] = {}
    try:
        for desc in registry.list():
            cid = str(getattr(desc, "capability_id", "") or "")
            name = str(getattr(getattr(desc, "capability", None), "name", "") or "")
            if not cid or not name:
                continue
            index.setdefault(name, cid)
            index.setdefault(name.lower(), cid)
    except Exception:  # noqa: BLE001  名字索引为 advisory 兜底，失败即空表
        index = {}
    with _NAME_INDEX_LOCK:
        if len(_NAME_INDEX_CACHE) >= _NAME_INDEX_CACHE_MAX:
            _NAME_INDEX_CACHE.clear()
        _NAME_INDEX_CACHE[stamp] = index
    return index


def resolve_capability_id(tool_name: str, *, registry: Any = None,
                          source_type: str = "builtin",
                          source_id: str = "builtin") -> Dict[str, Any]:
    """运行时**工具名 → descriptor capability_id** 改写（TASK-S3-01 消费 S2-01 遗留 #1）

    S2-01 的工具级统一 Trace 直接以**工具名**落账 `trace.capability_id`，join 依赖
    registry 侧事后匹配；本函数把改写提前到**落账点**，使
    ``UnifiedTraceStore.list_by_capability(capability_id)`` 与
    `data/descriptors.json` 台账可用**同一个键**直接 join（S3 模式挖掘的前置）。

    解析顺序（确定性，逐级降级，**绝不伪造 provenance**）：

    1. 空名 → ``capability_id=""``；
    2. 已是 ``cp.*`` → 解析 alias 后原样（`resolved_by=already_canonical`）；
    3. 名字本身命中 alias 表 → canonical（`alias`）；
    4. 派生候选 ``cp.<source_id>.<name>`` 命中台账 → 直接采用（`registry_id`，
       O(1) 字典查，工具链热路径的主命中）；
    5. 台账中 ``capability.name`` 等于工具名 → 采用其 id（`registry_name`）；
    6. 均未命中 → 返回**派生候选**并如实标注 ``joined=False``（`derived`）——
       不冒充已登记，台账补齐后同键即可 join；
    7. 台账不可用 → 回退工具名原文（`advisory_fallback`），**不丢轨迹**。

    Args:
        tool_name: 运行时工具名（或已是 capability_id）
        registry: DescriptorRegistry；缺省懒加载默认台账 `data/descriptors.json`
        source_type / source_id: 派生候选的来源段（内置工具 / MCP server 等）

    Returns:
        {"capability_id", "tool_name", "joined", "rewritten", "resolved_by"}
    """
    raw = str(tool_name or "").strip()
    if not raw:
        return {"capability_id": "", "tool_name": "", "joined": False,
                "rewritten": False, "resolved_by": RESOLVED_EMPTY}
    candidate = canonical_capability_id(
        raw, source_type=source_type, source_id=source_id)
    try:
        reg = registry
        if reg is None:
            from agent.descriptors.registry import DescriptorRegistry
            reg = DescriptorRegistry()
        if raw.startswith("cp."):
            canonical = reg.resolve_alias(raw) or raw
            return {"capability_id": canonical, "tool_name": raw,
                    "joined": reg.get(canonical) is not None,
                    "rewritten": canonical != raw,
                    "resolved_by": RESOLVED_ALREADY_CANONICAL}
        for key in (raw, sanitize_id_part(raw)):
            if not key:
                continue
            via_alias = reg.resolve_alias(key)
            if via_alias:
                return {"capability_id": via_alias, "tool_name": raw,
                        "joined": reg.get(via_alias) is not None,
                        "rewritten": via_alias != raw,
                        "resolved_by": RESOLVED_ALIAS}
        if candidate and reg.get(candidate) is not None:
            return {"capability_id": candidate, "tool_name": raw, "joined": True,
                    "rewritten": candidate != raw,
                    "resolved_by": RESOLVED_REGISTRY_ID}
        hit = _name_index(reg).get(raw) or _name_index(reg).get(raw.lower())
        if hit:
            return {"capability_id": hit, "tool_name": raw, "joined": True,
                    "rewritten": hit != raw,
                    "resolved_by": RESOLVED_REGISTRY_NAME}
        return {"capability_id": candidate or raw, "tool_name": raw,
                "joined": False, "rewritten": (candidate or raw) != raw,
                "resolved_by": RESOLVED_DERIVED}
    except Exception:  # noqa: BLE001  台账不可用 → 回退原文，绝不影响落账
        return {"capability_id": raw, "tool_name": raw, "joined": False,
                "rewritten": False, "resolved_by": RESOLVED_FALLBACK}


# ═════════════════════════════════════════════════════════════
# 结果类型
# ═════════════════════════════════════════════════════════════


@dataclass
class BridgeItemError:
    source: str
    error: str


@dataclass
class BridgeBatchResult:
    """一批转换结果（item 级失败 advisory）"""

    items: List[ToolDescriptor] = field(default_factory=list)
    errors: List[BridgeItemError] = field(default_factory=list)
    notes: List[Dict[str, str]] = field(default_factory=list)  # {id, stage, rationale}

    @property
    def ok_count(self) -> int:
        return len(self.items)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok_count,
            "errors": [{"source": e.source, "error": e.error} for e in self.errors],
            "notes": self.notes,
        }


@dataclass
class BridgeRunSummary:
    """register_bridge_view 汇总（advisory：任何单条失败不阻断）"""

    kind: str
    total: int = 0
    registered: int = 0
    merged: int = 0
    variant: int = 0
    updated: int = 0
    errors: List[BridgeItemError] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "total": self.total,
            "registered": self.registered,
            "merged": self.merged,
            "variant": self.variant,
            "updated": self.updated,
            "errors": [{"source": e.source, "error": e.error} for e in self.errors],
        }


# ═════════════════════════════════════════════════════════════
# MCP list_tools → descriptor（source_type=mcp）
# ═════════════════════════════════════════════════════════════


def descriptor_from_mcp_tool(
    tool: Mapping[str, Any],
    *,
    server: str,
    transport: str = "stdio",
    provenance: Union[ProvenanceLevel, str] = ProvenanceLevel.DECLARED,
    stage: Optional[Union[EvolutionStage, str]] = EvolutionStage.BORROWED,
    trace_policy: Optional[str] = None,
    risk_level: Optional[Union[RiskLevel, str]] = None,
    data_class: Optional[Union[DataClass, str]] = None,
    external_endpoint: Optional[bool] = None,
    idempotent: Optional[bool] = None,
    timeout_ms: Optional[int] = None,
) -> ToolDescriptor:
    """MCP list_tools 单条 tool 结果 → ToolDescriptor

    tool 字典形如 MCP SDK Tool.model_dump()/mcp_adapter._tool_to_dict 输出：
    {name, description, inputSchema|input_schema, annotations?...}。
    默认 stage=borrowed（外部上游接入即借）；trace_policy 默认引用调用侧轨迹
    （S2 统一 Trace 台账建成后切换 ledger 引用——S0-02 §3.5 前置声明）。
    """
    tool_name = str(tool.get("name") or "unknown")
    description = str(tool.get("description") or f"MCP tool {tool_name}")
    input_schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    if not isinstance(input_schema, dict):
        input_schema = {}
    safe_server = sanitize_id_part(server)
    safe_tool = sanitize_id_part(tool_name)
    cid = f"cp.{safe_server}.{safe_tool}"
    if not trace_policy:
        # TASK-S3-01（S2-01 遗留 #9）：占位串 → 真实统一轨迹台账引用
        trace_policy = ledger_trace_policy(
            source=safe_server, capability_id=cid, route="call-side")

    ann = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
    if idempotent is None:
        idempotent = bool(ann.get("idempotentHint")) if ann else False
    ext = external_endpoint if external_endpoint is not None else (transport == "sse")

    return ToolDescriptor(
        meta=MetaInfo(id=cid, version="0.1.0"),
        origin=OriginInfo(
            source_type=SourceType.MCP,
            source_id=server,
            provenance=provenance,
            external_endpoint=ext,
            manifest_ref=f"mcp:{server}",
        ),
        tenancy=TenancyInfo(),
        capability=CapabilityInfo(
            name=tool_name,
            description=description[:4000],
            input_schema=input_schema,
        ),
        trust=TrustInfo(
            risk_level=risk_level,
            data_class=data_class,
            requires_approval=False,
        ),
        runtime=RuntimeInfo(
            timeout_ms=timeout_ms or 30_000,
            idempotent=idempotent,
        ),
        evolution=EvolutionInfo(
            stage=stage,
            trace_policy=trace_policy,
        ),
        quality=QualityInfo(),
        governance=GovernanceInfo(),
    )


def descriptors_from_mcp_list(
    tools: List[Mapping[str, Any]],
    *,
    server: str,
    transport: str = "stdio",
    **kwargs: Any,
) -> BridgeBatchResult:
    """批量：MCP list_tools → descriptors（单条失败 advisory）"""
    result = BridgeBatchResult()
    for tool in tools:
        source = f"mcp:{server}/{tool.get('name', '?')}"
        try:
            result.items.append(
                descriptor_from_mcp_tool(tool, server=server,
                                         transport=transport, **kwargs))
        except Exception as e:  # noqa: BLE001
            logger.warning("[descriptors] 桥接失败 %s: %s", source, e)
            result.errors.append(BridgeItemError(source=source, error=str(e)))
    return result


# ═════════════════════════════════════════════════════════════
# 内置运行时工具 → descriptor（source_type=builtin）
# ═════════════════════════════════════════════════════════════


def descriptor_from_builtin_tool(
    name: str,
    description: str = "",
    schema: Optional[Mapping[str, Any]] = None,
    *,
    source_id: str = "builtin",
) -> ToolDescriptor:
    """内置运行时工具（agent.tools 注册项 name/description/schema）→ descriptor

    provenance=verified（本地代码即证据）；stage=None（native 需 30 天零回退台账，
    S0-02 §3.4 存量不自动置位，S3 补验）；risk/data_class 未回填（None，S1-02）。
    """
    safe = sanitize_id_part(source_id) or "builtin"
    cid = f"cp.{safe}.{sanitize_id_part(name)}"
    return ToolDescriptor(
        meta=MetaInfo(id=cid),
        origin=OriginInfo(
            source_type=SourceType.BUILTIN,
            source_id=source_id,
            provenance=ProvenanceLevel.VERIFIED,
        ),
        tenancy=TenancyInfo(),
        capability=CapabilityInfo(
            name=name,
            description=description or "",
            input_schema=dict(schema or {}),
        ),
        trust=TrustInfo(),
        runtime=RuntimeInfo(
            timeout_ms=_BUILTIN_TIMEOUT_MS,
            idempotent=False,
        ),
        evolution=EvolutionInfo(stage=None),
        quality=QualityInfo(),
        governance=GovernanceInfo(),
    )


def descriptors_from_builtin(
    entries: List[Mapping[str, Any]],
) -> BridgeBatchResult:
    """批量：内置工具条目 {name, description, schema?} → descriptors（advisory）"""
    result = BridgeBatchResult()
    for entry in entries:
        name = str(entry.get("name") or "?")
        try:
            result.items.append(
                descriptor_from_builtin_tool(
                    name,
                    description=str(entry.get("description") or ""),
                    schema=entry.get("schema"),
                    source_id=str(entry.get("source_id") or "builtin"),
                ))
        except Exception as e:  # noqa: BLE001
            logger.warning("[descriptors] 内置工具桥接失败 %s: %s", name, e)
            result.errors.append(BridgeItemError(source=f"builtin:{name}", error=str(e)))
    return result


# ═════════════════════════════════════════════════════════════
# SKILL.md / skills_mgmt 资产 → 轻量 descriptor 视图（source_type=skill）
# ═════════════════════════════════════════════════════════════


def map_skill_stage(
    *,
    category: Optional[str] = None,
    source: Optional[str] = None,
    status: Optional[str] = None,
    author: Optional[str] = None,
) -> Tuple[Optional[EvolutionStage], str]:
    """L3 资产 evolution.stage 映射（TASK-S0-02 §3.2/§3.4 决策表，确定性）

    规则（按序）：
    1. status ∈ {deprecated, archived} → DEPRECATED（发布轨同义复用，§3.4）；
    2. 外来导入（category ∈ {claude, community, mcp, ai_generated}，或 source 为
       external_agent/install/market 或 github:/url:/http: 等 scheme）→ BORROWED
       （外来导入 L3 近似特例，§3.2 L3 列 ◐；需 trace_policy）；
    3. 其余本土资产 → None（published≈internalized 仅条件成立，存量无验收证据不
       置位，§3.4——internalized/native 待 S3 补验）。
    """
    c = (category or "").lower()
    s = (source or "").lower()
    st = (status or "").lower()
    if st in ("deprecated", "archived"):
        return EvolutionStage.DEPRECATED, "L3 lifecycle 同义复用（S0-02 §3.4）"
    external = (
        c in _EXTERNAL_CATEGORIES
        or s in _EXTERNAL_SOURCES
        or s.startswith(_EXTERNAL_SOURCE_PREFIXES)
    )
    if external:
        return EvolutionStage.BORROWED, \
            "外来导入 L3 近似 borrowed 特例（S0-02 §3.2 L3 列 ◐）"
    if author == "external_agent":
        return EvolutionStage.BORROWED, "external_agent 外来导入（S0-02 §3.4）"
    if st == "published":
        return None, "published≈internalized 仅条件成立；无验收证据不置位，待 S3 补验"
    return None, "本土资产无七态证据（待 S1-02/S3），S0-02 §3.4 存量零迁移"


def _as_mapping(skill: Any) -> Mapping[str, Any]:
    """Skill 对象/dict → 只读 Mapping（pydantic 对象优先 model_dump）"""
    if isinstance(skill, Mapping):
        return skill
    if hasattr(skill, "model_dump"):
        return skill.model_dump()
    if hasattr(skill, "to_storage_dict"):
        return skill.to_storage_dict()
    if hasattr(skill, "dict"):
        return skill.dict()
    raise TypeError(f"不支持的 skill 载体: {type(skill)!r}")


def skill_to_descriptor(
    skill: Any,
    *,
    trace_policy: Optional[str] = None,
) -> Tuple[Optional[ToolDescriptor], Dict[str, str]]:
    """Skill（skills_mgmt 主轨模型/存储 dict）→ 轻量 Descriptor 视图（只读映射）

    映射：id→meta.id(cp.skill.<id>)、name/description→capability、
    config_schema→capability.input_schema、output_schema→capability.output_schema、
    metrics→quality.sample_count/success_rate、source/category/status→
    origin/evolution（map_skill_stage）。

    Returns:
        (descriptor 或 None, notes)；失败（不可建模）返回 (None, {"error": ...})，
        不抛异常——advisory。
    """
    notes: Dict[str, str] = {}
    try:
        s = _as_mapping(skill)
    except Exception as e:  # noqa: BLE001
        return None, {"error": f"无法读取 skill: {e}"}

    skill_id = str(s.get("id") or "")
    if not skill_id:
        return None, {"error": "skill 缺少 id"}
    safe_id = sanitize_id_part(skill_id)
    cid = f"cp.skill.{safe_id}"

    category = str(s.get("category") or "custom")
    source = str(s.get("source") or "manual")
    status = str(s.get("status") or "draft")
    author = str(s.get("author") or "unknown")

    stage, rationale = map_skill_stage(
        category=category, source=source, status=status, author=author)
    notes["stage_rationale"] = rationale

    # provenance 回填规则占位（S1-02 将统一实施；桥接只读视图按来源声明）
    if category == "builtin":
        prov = ProvenanceLevel.VERIFIED
    elif category == "custom" and source in ("manual", "knowledge_distill",
                                              "process_distill"):
        prov = ProvenanceLevel.DECLARED
    else:
        prov = ProvenanceLevel.UNKNOWN
        notes["provenance_note"] = "外来资产无签名证据，provenance=unknown（S1-02 回填）"

    metrics = s.get("metrics") if isinstance(s.get("metrics"), dict) else {}
    sample_count = int(metrics.get("usage_count") or 0)
    success_rate = float(metrics.get("success_rate") or 0.0)
    if sample_count > 0 and not metrics.get("success_rate"):
        ok = int(metrics.get("success_count") or 0)
        success_rate = round(ok / sample_count, 4)

    if stage == EvolutionStage.BORROWED and not trace_policy:
        # TASK-S3-01（S2-01 遗留 #9）：占位串 → 真实统一轨迹台账引用
        trace_policy = ledger_trace_policy(
            source="skill-import", capability_id=cid, route="import-ledger")

    try:
        desc = ToolDescriptor(
            meta=MetaInfo(id=cid),
            origin=OriginInfo(
                source_type=SourceType.SKILL,
                source_id=skill_id,
                provenance=prov,
                manifest_ref=f"skill:{category}",
            ),
            tenancy=TenancyInfo(),
            capability=CapabilityInfo(
                name=str(s.get("name") or skill_id),
                description=str(s.get("description") or "")[:4000],
                input_schema=s.get("config_schema")
                if isinstance(s.get("config_schema"), dict) else {},
                output_schema=s.get("output_schema")
                if isinstance(s.get("output_schema"), dict) else {},
            ),
            trust=TrustInfo(),
            runtime=RuntimeInfo(),
            evolution=EvolutionInfo(
                stage=stage,
                trace_policy=trace_policy or "",
            ),
            quality=QualityInfo(
                sample_count=sample_count,
                success_rate=success_rate,
            ),
            governance=GovernanceInfo(),
        )
    except Exception as e:  # noqa: BLE001
        return None, {"error": f"Descriptor 建模失败: {e}"}
    return desc, notes


def bridge_to_skill(
    skill_id: str,
    skill: Any = None,
    *,
    loader: Optional[Callable[[str], Any]] = None,
    error_sink: Optional[List[str]] = None,
) -> Optional[ToolDescriptor]:
    """Skill ↔ Descriptor 只读桥接入口（按 skill_id 取主轨资产生成视图）

    loader 未提供时函数体内懒加载 skills_mgmt.store.SkillStore（模块级零依赖）；
    任何失败（资产不存在/读取异常/建模失败）返回 None 并写入 error_sink，
    绝不反向写 Skill/抛异常阻断调用方——advisory 语义。
    """
    def _record(msg: str) -> None:
        if error_sink is not None:
            error_sink.append(msg)
        logger.warning("[descriptors] bridge_to_skill(%s) 失败: %s", skill_id, msg)

    if skill is None:
        if loader is not None:
            try:
                skill = loader(skill_id)
            except Exception as e:  # noqa: BLE001
                _record(f"loader 异常: {e}")
                return None
        else:
            try:
                from agent.skills_mgmt.store import SkillStore
                skill = SkillStore().get(skill_id)
            except Exception as e:  # noqa: BLE001
                _record(f"懒加载 SkillStore 失败: {e}")
                return None
        if skill is None:
            _record("资产不存在")
            return None
    desc, notes = skill_to_descriptor(skill)
    if desc is None:
        _record(notes.get("error", "未知错误"))
    return desc


# ═════════════════════════════════════════════════════════════
# 批量注册包装（advisory：单条失败不阻断）
# ═════════════════════════════════════════════════════════════


def register_bridge_view(
    registry: Any,
    kind: str,
    items: List[Any],
    **kwargs: Any,
) -> BridgeRunSummary:
    """把一批资产视图注册进 DescriptorRegistry（item 级失败 advisory）

    kind: 'mcp'（items=tool dicts，需传 server/transport）｜'builtin'
    （items={name,description,schema?}）｜'skill'（items=skill 对象/dict）。
    单条转换或校验失败仅计入 errors，继续处理其余条目。
    """
    summary = BridgeRunSummary(kind=kind)
    descriptors: List[ToolDescriptor] = []
    if kind == "mcp":
        batch = descriptors_from_mcp_list(items, **kwargs)
        descriptors = batch.items
        summary.errors.extend(batch.errors)
    elif kind == "builtin":
        batch = descriptors_from_builtin(items)
        descriptors = batch.items
        summary.errors.extend(batch.errors)
    elif kind == "skill":
        for it in items:
            desc, notes = skill_to_descriptor(it)
            if desc is not None:
                descriptors.append(desc)
            else:
                src = "skill:?"
                try:
                    m = _as_mapping(it)
                    src = f"skill:{m.get('id', '?')}"
                except Exception:  # noqa: BLE001
                    pass
                summary.errors.append(BridgeItemError(
                    source=src, error=notes.get("error", "转换失败")))
    else:
        raise ValueError(f"未知 kind: {kind}（mcp/builtin/skill）")

    summary.total = len(items)
    for desc in descriptors:
        src = desc.capability_id
        try:
            res = registry.register(desc)
            summary.registered += 1
            if res.action == "merged":
                summary.merged += 1
            elif res.action == "variant":
                summary.variant += 1
            elif res.action == "updated":
                summary.updated += 1
        except Exception as e:  # noqa: BLE001
            summary.errors.append(BridgeItemError(source=src, error=str(e)))
    return summary


# ═════════════════════════════════════════════════════════════
# 演示（验收 #4：MCP list_tools → descriptor 自动生成演示）
# ═════════════════════════════════════════════════════════════


def demo_pipeline(registry: Optional[Any] = None) -> Dict[str, Any]:
    """端到端演示：MCP list_tools / 内置工具 / SKILL.md → DescriptorRegistry

    演示流程（纯样例数据，不落盘、不触碰真实工具注册表）：
      1) MCP server(filesystem-mcp) list_tools 2 个 tool → descriptor(borrowed)；
      2) 内置工具 2 个（含 schema）→ descriptor(builtin)；
      3) SKILL.md 资产 2 条（published 本土 + claude 外来导入）→ 轻量视图；
      4) 第二个 MCP server(fs-dup) 声明同 schema 的 read_file → 三路投票合并留别名；
      5) 导出 list_with_trust() 能力清单。

    【S1-02】registry=None 时改用一次性临时路径（autosave=False，永不落盘），
    与共享运行时台账 data/descriptors.json 彻底隔离——演示不读不写真实台账，
    避免被既有台账内容干扰断言（S1-02 起台账由存量回填/安装接线常态化写入）。
    """
    from .registry import DescriptorRegistry

    if registry is not None:
        reg = registry
    else:
        import os as _os
        import tempfile as _tempfile
        import uuid as _uuid
        _tmp = _os.path.join(
            _tempfile.gettempdir(),
            f"descriptors_demo_{_uuid.uuid4().hex[:12]}.json")
        reg = DescriptorRegistry(path=_tmp, autosave=False)
    steps: Dict[str, Any] = {}

    mcp_tools = [
        {"name": "read_file",
         "description": "读取文件内容",
         "inputSchema": {"type": "object",
                         "properties": {"path": {"type": "string",
                                                 "cp.hint": {"id": "fs.path"}}},
                         "required": ["path"]}},
        {"name": "write_file",
         "description": "写入文件",
         "inputSchema": {"type": "object",
                         "properties": {"path": {"type": "string"},
                                        "content": {"type": "string"}},
                         "required": ["path", "content"]},
         "annotations": {"destructiveHint": True, "idempotentHint": False}},
    ]
    r1 = register_bridge_view(reg, "mcp", mcp_tools,
                              server="filesystem-mcp", transport="stdio")
    steps["mcp_discover"] = r1.as_dict()

    builtin = [
        {"name": "web_search", "description": "联网搜索",
         "schema": {"type": "object",
                    "properties": {"query": {"type": "string"}}}},
        {"name": "shell_execute", "description": "执行 shell 命令",
         "schema": {"type": "object",
                    "properties": {"command": {"type": "string"}}}},
    ]
    r2 = register_bridge_view(reg, "builtin", builtin)
    steps["builtin"] = r2.as_dict()

    skills = [
        {"id": "self_reflection", "name": "自我反思", "category": "builtin",
         "source": "manual", "status": "published",
         "description": "persona 内置行为技能",
         "config_schema": {"type": "object", "properties": {}}},
        {"id": "community-hello", "name": "Hello Community", "category": "claude",
         "source": "github:demo/hello", "status": "pending_review",
         "description": "外来社区技能示例",
         "config_schema": {"type": "object",
                           "properties": {"name": {"type": "string"}}}},
    ]
    r3 = register_bridge_view(reg, "skill", skills)
    steps["skills"] = r3.as_dict()

    # 4) 同 schema 去重合并演示
    dup = [{"name": "read_file",
            "description": "读取文件内容(同schema副本)",
            "inputSchema": {"type": "object",
                            "properties": {"path": {"type": "string",
                                                    "cp.hint": {"id": "fs.path"}}},
                            "required": ["path"]}}]
    r4 = register_bridge_view(reg, "mcp", dup,
                              server="fs-dup", transport="stdio")
    steps["dedupe_merge"] = r4.as_dict()

    steps["stats"] = reg.snapshot_stats()
    steps["capability_count"] = reg.count()
    steps["aliases"] = reg.alias_records()
    steps["capability_map_sample"] = reg.list_with_trust()[:3]
    return {"ok": True, "steps": steps, "registry": reg}
