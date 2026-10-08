"""分身记忆档位口径（none | brokered | scoped）—— 唯一词表、校验与 config→ctx 桥

【任务定位（P3 brokered 档）】
    本模块只做三件事，且都在**母体侧**：

      1. **唯一档位词表与解析**（resolve_memory_config）：把创建/热更新/委派请求里的
         memory_mode / memory_scope 归一化并校验，失败收口为 error（端点据此 400）。
         与 role_templates.resolve_subagent_role 同款：不抛、不静默回退。
      2. **config→ctx 桥**（attach_memory_metadata）：把生效档位与域**标识**写进
         委派上下文 metadata，供执行器在 execute 里读取并调 broker。
         **只写标识不写正文**（正文由 agent/memory/broker.py 在执行期取）。
      3. **如实回显**（memory_view）：创建/列表/热更新响应的 memory 段。

    分身侧**不新增任何记忆工具**：本模块绝不触碰 toolset / actor_matrix。

【本批只交付 brokered】
    scoped 档尚未实现 ⇒ 解析期显式拒绝（E_MEMORY_CONFIG），**不静默降级为 none**
    （静默降级会让使用者以为 scoped 生效了）。none 档给非空 memory_scope 同样拒绝
    （与角色"默认档配自由文本 400"同款：不静默忽略）。

【依赖纪律】
    仅标准库。执行期真正的记忆读取在 agent/memory/broker.py，由执行器惰性导入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Tuple

__all__ = [
    "MEMORY_MODE_NONE",
    "MEMORY_MODE_BROKERED",
    "MEMORY_MODE_SCOPED",
    "MEMORY_MODES",
    "IMPLEMENTED_MEMORY_MODES",
    "MEMORY_SCOPE_KEYS",
    "MemoryConfigError",
    "MemoryResolution",
    "normalize_memory_mode",
    "resolve_memory_config",
    "memory_view",
    "attach_memory_metadata",
]

#: 不分身记忆：逐字旧行为（默认）
MEMORY_MODE_NONE = "none"

#: 母体代管：只读、限定域的一段记忆进 ②约束 + metadata 标识
MEMORY_MODE_BROKERED = "brokered"

#: 分身自带记忆工具域（**本批未实现**，解析期拒绝）
MEMORY_MODE_SCOPED = "scoped"

#: 档位全集（顺序 = 权限从低到高；UI 选择器与审计枚举都取这里）
MEMORY_MODES: Tuple[str, ...] = (MEMORY_MODE_NONE, MEMORY_MODE_BROKERED, MEMORY_MODE_SCOPED)

#: 本批**已实现**的档位
IMPLEMENTED_MEMORY_MODES: Tuple[str, ...] = (MEMORY_MODE_NONE, MEMORY_MODE_BROKERED)

#: scope 允许出现的键。与 agent/memory/broker.py 的 SCOPE_KEYS 同口径
#: （内存侧不反向依赖 subagent，故各留一份，由 tests/unit/test_subagent_memory_broker.py
#:   断言两者相等，防止漂移）。
MEMORY_SCOPE_KEYS: Tuple[str, ...] = (
    "tenant_id", "workspace_id", "subject_id", "workspace_root",
    "memory_types", "limit",
)

#: 视为"未表态 ⇒ none"的取值（大小写不敏感）
_NONE_SENTINELS: Tuple[str, ...] = ("", "default", "auto", "off", "0", "false")


class MemoryConfigError(ValueError):
    """记忆档位配置非法（端点据此转 400 E_MEMORY_CONFIG）"""


@dataclass(frozen=True)
class MemoryResolution:
    """一次"分身配置 → 该用哪档记忆"的解析结果（只读投影，**不含记忆正文**）

    Attributes:
        mode: 归一化后的档位（none / brokered / scoped）。
        scope: 归一化后的域（标识；空 dict = 未限定，broker 会如实降级）。
        implemented: 该档是否已实现（scoped = False；解析期直接 error）。
        audit_required: 是否必须留审计（brokered = True）。
        red: 是否红档（brokered **不是**：正文只进 ②约束，不进 system prompt）。
        error: 解析失败原因（非空 = 调用方应拒绝，不得带病委派）。
    """

    mode: str = MEMORY_MODE_NONE
    scope: Mapping[str, Any] = field(default_factory=dict)
    implemented: bool = True
    audit_required: bool = False
    red: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def enabled(self) -> bool:
        return self.mode != MEMORY_MODE_NONE

    def to_dict(self) -> Dict[str, Any]:
        """投影给 HTTP/UI（**不含记忆正文**）"""
        return {
            "mode": self.mode,
            "enabled": self.enabled,
            "implemented": bool(self.implemented),
            "scope": dict(self.scope),
            "audit_required": bool(self.audit_required),
            "red": bool(self.red),
            "error": self.error,
        }


def normalize_memory_mode(value: Any) -> str:
    """档位归一化：空/哨兵 ⇒ none；未知 ⇒ MemoryConfigError（不猜、不夹取）"""
    raw = str(value or "").strip().lower()
    if raw in _NONE_SENTINELS or raw == MEMORY_MODE_NONE:
        return MEMORY_MODE_NONE
    if raw in (MEMORY_MODE_BROKERED, MEMORY_MODE_SCOPED):
        return raw
    raise MemoryConfigError(
        "未知记忆档位 %r；词表: %s" % (value, " / ".join(MEMORY_MODES)))


def _clean_scope(scope: Any) -> Dict[str, Any]:
    if scope is None:
        return {}
    if not isinstance(scope, Mapping):
        raise MemoryConfigError("memory_scope 必须是对象（键值表），收到: %s"
                                % type(scope).__name__)
    out: Dict[str, Any] = dict(scope)
    unknown = [k for k in out.keys() if k not in MEMORY_SCOPE_KEYS]
    if unknown:
        raise MemoryConfigError(
            "memory_scope 含未知键 %s；允许: %s"
            % (sorted(str(k) for k in unknown), " / ".join(MEMORY_SCOPE_KEYS)))
    return out


def resolve_memory_config(mode: Any = MEMORY_MODE_NONE,
                          scope: Any = None) -> MemoryResolution:
    """(memory_mode, memory_scope) → MemoryResolution（**不抛**）

    失败一律收口为 error 非空（端点据此 400）：
      · 未知档位 ⇒ error；
      · scoped（本批未实现）⇒ error（不静默降级为 none）；
      · none 档给非空 scope ⇒ error（不静默忽略 —— 那会让使用者以为它生效了）；
      · scope 非对象 / 含未知键 ⇒ error。
    """
    try:
        normalized = normalize_memory_mode(mode)
    except MemoryConfigError as e:
        return MemoryResolution(mode=str(mode or ""), error=str(e))

    if normalized == MEMORY_MODE_SCOPED:
        return MemoryResolution(
            mode=normalized, implemented=False, error=(
                "scoped 档尚未实现（本批只交付 brokered）：分身侧不新增任何记忆工具，"
                "请使用 memory_mode='brokered'（母体代管只读记忆）"))

    try:
        clean_scope = _clean_scope(scope)
    except MemoryConfigError as e:
        return MemoryResolution(mode=normalized, error=str(e))

    if normalized == MEMORY_MODE_NONE and clean_scope:
        return MemoryResolution(
            mode=normalized, error=(
                "memory_mode='none' 却给了非空 memory_scope：默认档不接受记忆域，"
                "请显式 memory_mode='brokered'（不静默忽略 —— 那会让你以为它生效了）"))

    return MemoryResolution(
        mode=normalized,
        scope=clean_scope,
        implemented=True,
        audit_required=(normalized == MEMORY_MODE_BROKERED),
    )


def memory_view(config: Any) -> Dict[str, Any]:
    """配置 → memory 段投影（创建/列表/热更新回显用；**不含记忆正文**）"""
    plan = resolve_memory_config(
        getattr(config, "memory_mode", MEMORY_MODE_NONE),
        getattr(config, "memory_scope", None))
    view = plan.to_dict()
    view["provider"] = str(getattr(config, "memory_provider", "") or "")
    return view


def attach_memory_metadata(ctx: Any, config: Any) -> Any:
    """把生效档位与域**标识**写进委派上下文 metadata（config→ctx 桥）

    【为什么就地写】委派上下文是 frozen dataclass；本函数被
    SubagentContainer.run_delegation（单发/具名/工具路径）与
    SubagentLifecycleManager._prepare_config（delegate/delegate_many 共用）调用，
    后者只返回 config、不返回 ctx，故对 metadata 这一层做就地更新
    （标识级，幂等）。memory_mode='none' ⇒ **完全不碰** ctx（逐字旧行为）。

    Returns:
        传入的 ctx（便于链式调用）。
    """
    normalized = str(getattr(config, "memory_mode", MEMORY_MODE_NONE) or "").strip().lower()
    if normalized in _NONE_SENTINELS or normalized == MEMORY_MODE_NONE:
        return ctx
    scope = getattr(config, "memory_scope", None)
    current = getattr(ctx, "metadata", None)
    meta: Dict[str, Any] = dict(current) if isinstance(current, Mapping) else {}
    meta["memory_mode"] = normalized
    if isinstance(scope, Mapping) and scope:
        meta["memory_scope"] = dict(scope)
    if isinstance(current, dict):
        current.clear()
        current.update(meta)
    else:
        # metadata 是其它 Mapping（不可变）⇒ 绕过 frozen 只替换该字段
        object.__setattr__(ctx, "metadata", meta)
    return ctx
