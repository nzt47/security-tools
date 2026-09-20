"""`CapabilityRecord` —— 能力规格的**只读**内存视图（v1.4 §5.1）

## 🔴 命名：为什么不叫 `CapabilitySpec`

`v1.4` 的术语是 `CapabilitySpec`。**但本仓库里 `CapabilitySpec` 这个名字已经属于
`agent/lines/models.py::ToolMeta`** —— `TASK-04` 的决定是"**`CapabilitySpec` 必须
就是 `ToolMeta`**，不得存在第二个能力定义类"，并把它写成了**自动化守卫**：

    tests/unit/test_capability_spec.py::test_能力定义只有一个结构
        ── 全仓 `agent/**.py` 里出现第二个 `^class CapabilitySpec` ⇒ 直接失败（违反 D1）

本模块的第一版就叫 `CapabilitySpec`，**被那条守卫当场拦下**（实测：
`offenders == ['agent\\capregistry\\spec.py']`）。正确的处置是**改名**，而不是去
放宽 `TASK-04` 的守卫 —— 那条守卫守的正是 **D1（单一真相源）**，
放宽它等于把"允许两份能力定义"写进测试。

⇒ 本类因此叫 **`CapabilityRecord`**：它是 `CapabilitySpec`（= `ToolMeta`）
   在 Registry 里的**归一化记录**，语义上就是"能力规格的一条记录"。

## 与 `agent/lines/models.py::ToolMeta` 的关系（**不要造第二份定义**）

`ToolMeta` 已经是完整的 `CapabilitySpec`（TASK-04 扩展）。本模块**不重定义**它，
只做一件事：把 `ToolMeta` + 技能侧声明 + 运行时事实 + TASK-04 的派生产物
**归一化成一个可查询、可序列化的视图对象**。

- **权威仍是** `data/tool_definitions/*.yaml`（工具）与 `data/skill_callability.yaml`
  （技能）—— 见 `TASK-00` §0.5 的 D1。
- 本对象**没有**任何"注册/写入"方法，也没有 `to_yaml()` 之类的反向出口。
  `tests/unit/test_capregistry_core.py` 的 grep 断言锁死这条不变量。
- 字段一律**只增不减**、带默认值（D2）。

## `callable_by`（v1.4 §5.1）

值域 `llm / human / system / service_account`。它是**入口身份白名单**，
**不表示执行是否依赖 LLM**（一条 `callable_by=["human"]` 的能力完全可能内部调模型；
一条 `callable_by` 含 `llm` 的能力也可能纯本地计算）。

派生规则（可复现、有测试；见 `derive_callable_by`）：

| 情形 | 结果 |
|---|---|
| `deprecated=True` | `[]`（不再接受新调用） |
| `trigger=none` | `[]` |
| `trigger=model` | `["llm","human","system","service_account"]` |
| `trigger=system` | `["system","human","service_account"]` |
| `trigger=human` | `["human"]` |
| `llm_callable=False` | 去掉 `llm` |
| `permission_level=restricted` | 去掉 `service_account`（v1.4 §10.2：SA 需预授权，预授权在 TASK-06） |

`service_account` 是**新增值**（仓库现状 `trigger` 只有 model/system/human/none）——
它就是"CI/定时任务以服务账号身份调用"的那个入口身份，也是 `TASK-05` 要补的缺口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "CALLABLE_BY",
    "IMPL_STATUS",
    "CapabilityRecord",
    "derive_callable_by",
]

#: 入口身份白名单值域（v1.4 §5.1）
CALLABLE_BY: Tuple[str, ...] = ("llm", "human", "system", "service_account")

#: 实现状态（**「谎报成功」类能力的可见化手段**，TASK-05 第 0 步第 6 项）
#: `not_implemented`：清单/注册表里在，但底层实现是空壳（`schedule_task` 是当前唯一一例）
IMPL_STATUS: Tuple[str, ...] = ("implemented", "not_implemented", "unavailable", "unknown")


def derive_callable_by(*, trigger: str, llm_callable: bool,
                       permission_level: str, deprecated: bool) -> List[str]:
    """按模块 docstring 的表格派生 `callable_by`（纯函数，无 IO、无副作用）"""
    if deprecated:
        return []
    trig = str(trigger or "").strip().lower()
    if trig == "none":
        return []
    if trig == "model":
        out = ["llm", "human", "system", "service_account"]
    elif trig == "system":
        out = ["system", "human", "service_account"]
    elif trig == "human":
        out = ["human"]
    else:
        # 未登记 trigger 的保守侧：只允许人类入口（不假设它可被模型或后台调用）
        out = ["human"]
    if not llm_callable:
        out = [x for x in out if x != "llm"]
    if str(permission_level or "").strip().lower() == "restricted":
        out = [x for x in out if x != "service_account"]
    return out


@dataclass(frozen=True)
class CapabilityRecord:
    """一条能力的规格（**不可变**；`frozen=True` 是"只读派生视图"的类型级保证）

    【为什么 frozen】`CapabilityRegistry` 会把同一批 spec 分发给 HTTP / CLI /
    模型三条链路并发读取（waitress 16 线程）。不可变对象天然无锁、无竞态，
    也杜绝了"某个调用方顺手改了共享 spec"这类极难查的污染。
    """

    # ── 标识 ──
    tool_name: str
    #: `tenant_id:namespace:name@version`；缺省空串（构造便捷性；Registry 内恒有值）
    capability_id: str = ""
    tenant_id: str = "default"
    namespace: str = "yunshu"
    version: str = "1.0.0"
    aliases: Tuple[str, ...] = ()

    # ── 形态与归属 ──
    kind: str = "tool"
    tool_type: str = "tool"
    owner: str = "builtin"
    registry_source: str = "global"
    declared_in: str = ""

    # ── 位置（Loader 的唯一分叉点）──
    location: str = "remote"
    location_reason: str = ""
    #: 由 `agent/lines/location.py` 的 AST 判定器判出的位置来源
    location_source: str = ""
    location_declared: str = ""
    location_confidence: str = ""

    # ── 治理面（`agent/tool_gate.py` 的判据来源）──
    plane: str = "act"
    effect: str = "execute"
    risk: str = "medium"
    permission_level: str = ""
    needs_approval: bool = False
    internal: bool = False
    enabled: bool = True
    deprecated: bool = False

    # ── 可调用性 ──
    llm_callable: bool = True
    callable_mode: str = "auto"
    callable_by: Tuple[str, ...] = ()
    trigger: str = ""

    # ── 契约 ──
    description: str = ""
    schema_registered: bool = False
    input_schema: Optional[Dict[str, Any]] = None
    result_schema: Optional[Dict[str, Any]] = None

    # ── 执行面 ──
    host_executor: str = ""

    # ── 事实/派生（TASK-04 的 AST 与主线装配产物，重算昂贵 ⇒ 读快照）──
    mark: str = ""
    reachable: bool = True
    main_line_status: str = ""
    #: 实现状态：让 `/capabilities/tools` 的调用方**看出哪个是坏的**（TASK-05 §3 第 0 步第 6 项）
    impl_status: str = "implemented"
    impl_status_reason: str = ""

    # ── 健康（TASK-05 接线：`agent/health/` + Loader 状态机）──
    health: str = "unknown"

    #: 数据来源标记："authority"（YAML+技能声明）| "snapshot"（manifest 降级）
    spec_source: str = "authority"

    def __post_init__(self) -> None:
        # dataclass 是 frozen 的；这里只用 object.__setattr__ 做**规范化**（不是写入业务数据）
        if not self.callable_by:
            object.__setattr__(self, "callable_by", tuple(derive_callable_by(
                trigger=self.trigger or ("model" if self.llm_callable else "system"),
                llm_callable=bool(self.llm_callable),
                permission_level=self.permission_level,
                deprecated=bool(self.deprecated),
            )))
        else:
            object.__setattr__(self, "callable_by", tuple(self.callable_by))
        if self.impl_status not in IMPL_STATUS:
            object.__setattr__(self, "impl_status", "unknown")

    # ── 便捷视图 ──

    @property
    def is_remote(self) -> bool:
        return self.location == "remote"

    @property
    def is_skill(self) -> bool:
        return self.kind == "skill"

    def callable_by_identity(self, identity: str) -> bool:
        """该入口身份是否有权调用（**白名单判定**，不含任何策略层）"""
        return str(identity or "").strip().lower() in self.callable_by

    def to_dict(self) -> Dict[str, Any]:
        """对外序列化（HTTP/CLI/清单三处**同一份**结构；键序固定便于对拍）"""
        return {
            "name": self.tool_name,
            "capability_id": self.capability_id,
            "tenant_id": self.tenant_id,
            "namespace": self.namespace,
            "version": self.version,
            "aliases": list(self.aliases),
            "kind": self.kind,
            "tool_type": self.tool_type,
            "owner": self.owner,
            "registry_source": self.registry_source,
            "declared_in": self.declared_in,
            "location": self.location,
            "location_reason": self.location_reason,
            "location_source": self.location_source,
            "location_declared": self.location_declared,
            "location_confidence": self.location_confidence,
            "plane": self.plane,
            "effect": self.effect,
            "risk": self.risk,
            "permission_level": self.permission_level,
            "needs_approval": bool(self.needs_approval),
            "internal": bool(self.internal),
            "enabled": bool(self.enabled),
            "deprecated": bool(self.deprecated),
            "llm_callable": bool(self.llm_callable),
            "callable_mode": self.callable_mode,
            "callable_by": list(self.callable_by),
            "trigger": self.trigger,
            "description": self.description,
            "schema_registered": bool(self.schema_registered),
            "input_schema": self.input_schema,
            "result_schema": self.result_schema,
            "host_executor": self.host_executor,
            "mark": self.mark,
            "reachable": bool(self.reachable),
            "main_line_status": self.main_line_status,
            "impl_status": self.impl_status,
            "impl_status_reason": self.impl_status_reason,
            "health": self.health,
        }

    # ── 从两个来源构造（都是**读**，没有写）──

    @classmethod
    def from_tool_meta(cls, meta: Any, *,
                       facts: Optional[Mapping[str, Any]] = None,
                       derived: Optional[Mapping[str, Any]] = None) -> "CapabilityRecord":
        """从 `agent/lines/models.py::ToolMeta`（YAML 权威）构造

        Args:
            meta: `ToolMeta` 实例
            facts: `agent/tools/registry_facts()` 里该名字对应的事实（运行时）
            derived: `data/capability_manifest.json` 里该名字对应的派生字段
                （`mark` / `reachable` / `main_line_status` / `location_source` /
                `impl_status`）—— 见模块 docstring 的"补充源"说明
        """
        facts = facts or {}
        derived = derived or {}
        name = str(getattr(meta, "name", "") or "")
        return cls(
            tool_name=name,
            capability_id=str(getattr(meta, "capability_id", "") or ""),
            tenant_id=str(getattr(meta, "tenant_id", "default") or "default"),
            namespace=str(getattr(meta, "namespace", "yunshu") or "yunshu"),
            version=str(getattr(meta, "version", "1.0.0") or "1.0.0"),
            aliases=tuple(getattr(meta, "aliases", ()) or ()),
            kind=str(getattr(meta, "kind", "tool") or "tool"),
            tool_type=str(getattr(meta, "tool_type", "tool") or "tool"),
            owner=str(getattr(meta, "owner", "builtin") or "builtin"),
            registry_source=str(getattr(meta, "registry_source", "global") or "global"),
            declared_in=str(derived.get("declared_in")
                            or f"data/tool_definitions/{name}.yaml"),
            location=str(getattr(meta, "location", "remote") or "remote"),
            location_reason=str(getattr(meta, "location_reason", "") or ""),
            location_source=str(derived.get("location_source") or "declaration"),
            location_declared=str(derived.get("location_declared") or ""),
            location_confidence=str(derived.get("location_confidence") or ""),
            plane=str(getattr(meta, "plane", "act") or "act"),
            effect=str(getattr(meta, "effect", "execute") or "execute"),
            risk=str(getattr(meta, "risk", "medium") or "medium"),
            permission_level=str(getattr(meta, "permission_level", "") or ""),
            needs_approval=bool(getattr(meta, "needs_approval", False)),
            internal=bool(getattr(meta, "internal", False)),
            enabled=bool(derived.get("enabled", True)),
            deprecated=bool(getattr(meta, "deprecated", False)),
            llm_callable=bool(getattr(meta, "llm_callable", True)),
            callable_mode=str(getattr(meta, "callable_mode", "auto") or "auto"),
            trigger=str(derived.get("trigger") or (
                "model" if getattr(meta, "llm_callable", True) else "system")),
            description=str(getattr(meta, "description", "") or ""),
            schema_registered=bool(facts.get("schema_registered",
                                             getattr(meta, "input_schema", None) is not None)),
            input_schema=getattr(meta, "input_schema", None),
            result_schema=getattr(meta, "output_schema", None),
            host_executor=str(facts.get("host_executor")
                              or derived.get("host_executor") or ""),
            mark=str(derived.get("mark") or ""),
            reachable=bool(derived.get("reachable", True)),
            main_line_status=str(derived.get("main_line_status") or ""),
            impl_status=str(derived.get("impl_status") or "implemented"),
            impl_status_reason=str(derived.get("impl_status_reason") or ""),
            health=str(getattr(meta, "health", "") or ""),
            spec_source="authority",
        )

    @classmethod
    def from_manifest_entry(cls, entry: Mapping[str, Any], *,
                            spec_source: str = "snapshot") -> "CapabilityRecord":
        """从 `data/capability_manifest.json` 的一条 entry 构造（**降级路径专用**）

        `spec_source="snapshot"` 时 Registry 会整体标记 `degraded=True`：
        快照缺 `input_schema` / `result_schema`（清单为控制体积未收录），
        故降级期间"参数契约"维度不可用 —— 这是**如实披露**而不是隐藏。
        """
        e = dict(entry)
        return cls(
            tool_name=str(e.get("tool_name") or ""),
            capability_id=str(e.get("capability_id") or ""),
            tenant_id=str(e.get("tenant_id") or "default"),
            namespace=str(e.get("namespace") or "yunshu"),
            version=str(e.get("version") or "1.0.0"),
            aliases=tuple(e.get("aliases") or ()),
            kind=str(e.get("kind") or "tool"),
            tool_type=str(e.get("tool_type") or "tool"),
            owner=str(e.get("owner") or "builtin"),
            registry_source=str(e.get("registry_source") or "global"),
            declared_in=str(e.get("declared_in") or ""),
            location=str(e.get("location") or "remote"),
            location_reason=str(e.get("location_reason") or ""),
            location_source=str(e.get("location_source") or ""),
            location_declared=str(e.get("location_declared") or ""),
            location_confidence=str(e.get("location_confidence") or ""),
            plane=str(e.get("plane") or "act"),
            effect=str(e.get("effect") or "execute"),
            risk=str(e.get("risk") or "medium"),
            permission_level=str(e.get("permission_level") or ""),
            needs_approval=bool(e.get("needs_approval", False)),
            internal=bool(e.get("internal", False)),
            enabled=bool(e.get("enabled", True)),
            deprecated=bool(e.get("deprecated", False)),
            llm_callable=bool(e.get("llm_callable", False)),
            callable_mode=str(e.get("callable_mode") or "manual"),
            trigger=str(e.get("trigger") or ""),
            description=str(e.get("description") or ""),
            schema_registered=bool(e.get("schema_registered", False)),
            input_schema=None,
            result_schema=None,
            host_executor=str(e.get("host_executor") or ""),
            mark=str(e.get("mark") or ""),
            reachable=bool(e.get("reachable", False)),
            main_line_status=str(e.get("main_line_status") or ""),
            impl_status=str(e.get("impl_status") or "implemented"),
            impl_status_reason=str(e.get("impl_status_reason") or ""),
            health=str(e.get("health") or ""),
            spec_source=spec_source,
        )


def distinct_names(specs: Sequence[CapabilityRecord]) -> List[str]:
    """按名字去重排序（诊断/测试便利函数）"""
    return sorted({s.tool_name for s in specs if s.tool_name})
