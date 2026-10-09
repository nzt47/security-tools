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

    brokered 档分身侧**不新增任何记忆工具**（本模块不触碰 toolset / actor_matrix）；
    scoped 档是**显式开启的受控档**：只有 memory_mode="scoped" 且 memory_scope 含
    tenant_id / workspace_id / subject_id 三要素且 memory_provider 非空时才放开，
    缺任一 ⇒ E_MEMORY_CONFIG（fail-closed，不静默降级）。

【本批：brokered + scoped】
    · none/brokered：与改动前逐字一致（默认档不变）；none 档给非空 memory_scope 仍拒绝。
    · scoped：放行并回显三要素 + provider + 配额；配额/熔断在
      agent/subagent/memory_quota.py（显式错误码，不静默丢）。四处判定
      （actor_matrix / toolset / assembly / capability_exposure）共用同一布尔，
      由 scoped_memory_enabled() 给出。

【依赖纪律】
    仅标准库 + 同包 memory_quota（亦仅标准库）。执行期真正的记忆读取在
    agent/memory/broker.py，由执行器惰性导入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from agent.subagent.memory_quota import (
    AUDIT_SCOPED_ENABLED,
    MemoryQuotaConfigError,
    emit_scoped_audit,
    guard_from_quota,
    normalize_memory_quota,
)

__all__ = [
    "MEMORY_MODE_NONE",
    "MEMORY_MODE_BROKERED",
    "MEMORY_MODE_SCOPED",
    "MEMORY_MODES",
    "IMPLEMENTED_MEMORY_MODES",
    "MEMORY_SCOPE_KEYS",
    "SCOPED_REQUIRED_SCOPE_KEYS",
    "SUPPORTED_MEMORY_PROVIDERS",
    "MemoryConfigError",
    "normalize_memory_provider",
    "MemoryResolution",
    "normalize_memory_mode",
    "resolve_memory_config",
    "resolve_config_memory",
    "scoped_memory_enabled",
    "scoped_guard",
    "scoped_memory_domain",
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

#: 本批**已实现**的档位（scoped 为显式开启的受控档）
IMPLEMENTED_MEMORY_MODES: Tuple[str, ...] = (
    MEMORY_MODE_NONE, MEMORY_MODE_BROKERED, MEMORY_MODE_SCOPED)

#: scoped 档**必须齐全**的三要素（缺任一 ⇒ 400，fail-closed）
SCOPED_REQUIRED_SCOPE_KEYS: Tuple[str, ...] = (
    "tenant_id", "workspace_id", "subject_id")

#: scope 允许出现的键。与 agent/memory/broker.py 的 SCOPE_KEYS 同口径
#: （内存侧不反向依赖 subagent，故各留一份，由 tests/unit/test_subagent_memory_broker.py
#:   断言两者相等，防止漂移）。
MEMORY_SCOPE_KEYS: Tuple[str, ...] = (
    "tenant_id", "workspace_id", "subject_id", "workspace_root",
    "memory_types", "limit",
)

#: 视为"未表态 ⇒ none"的取值（大小写不敏感）
_NONE_SENTINELS: Tuple[str, ...] = ("", "default", "auto", "off", "0", "false")

#: scoped provider 词表/别名的**唯一权威**在 agent.memory.scoped_store（memory 域）。
#: 本模块不定义第二份，避免漂移；同时保持 subagent -> memory 的单向依赖
#: （反向 import 会与 memory_broker -> scoped_store 构成 no_circular_dependency 环）。


def __getattr__(name: str) -> Any:
    """兼容旧用法：agent.subagent.memory_broker.SUPPORTED_MEMORY_PROVIDERS

    PEP 562 模块级 __getattr__：只有真正被访问时才从 memory 域取，import 期不拉重依赖。
    """
    if name == "SUPPORTED_MEMORY_PROVIDERS":
        from agent.memory.scoped_store import SUPPORTED_MEMORY_PROVIDERS

        return SUPPORTED_MEMORY_PROVIDERS
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def normalize_memory_provider(provider: Any) -> str:
    """provider 别名归一化（不校验词表；校验由 resolve_memory_config 统一做）

    别名表与 scoped_store 同源（懒加载，避免 subagent 在 import 期拉入 memory 重依赖）。
    """
    from agent.memory.scoped_store import normalize_provider_alias

    return normalize_provider_alias(provider)


class MemoryConfigError(ValueError):
    """记忆档位配置非法（端点据此转 400 E_MEMORY_CONFIG）"""


@dataclass(frozen=True)
class MemoryResolution:
    """一次"分身配置 → 该用哪档记忆"的解析结果（只读投影，**不含记忆正文**）

    Attributes:
        mode: 归一化后的档位（none / brokered / scoped）。
        scope: 归一化后的域（标识；空 dict = 未限定，broker 会如实降级）。
        implemented: 该档是否已实现（none / brokered / scoped = True）。
        audit_required: 是否必须留审计（brokered / scoped = True）。
        red: 是否红档（brokered / scoped **都不是**：正文不进 system prompt）。
        error: 解析失败原因（非空 = 调用方应拒绝，不得带病委派）。
        provider: scoped 档的记忆 provider（默认档为空，回显用）。
        quota: scoped 档归一化后的配额配置（none/brokered 恒为空）。
    """

    mode: str = MEMORY_MODE_NONE
    scope: Mapping[str, Any] = field(default_factory=dict)
    implemented: bool = True
    audit_required: bool = False
    red: bool = False
    error: str = ""
    provider: str = ""
    quota: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def enabled(self) -> bool:
        return self.mode != MEMORY_MODE_NONE

    @property
    def is_scoped(self) -> bool:
        return self.mode == MEMORY_MODE_SCOPED

    def to_dict(self) -> Dict[str, Any]:
        """投影给 HTTP/UI（**不含记忆正文**）

        none / brokered 的键集与改动前**逐字一致**；scoped 额外带
        scoped / provider / quota 三个键。
        """
        data: Dict[str, Any] = {
            "mode": self.mode,
            "enabled": self.enabled,
            "implemented": bool(self.implemented),
            "scope": dict(self.scope),
            "audit_required": bool(self.audit_required),
            "red": bool(self.red),
            "error": self.error,
        }
        if self.is_scoped and not self.error:
            data["scoped"] = True
            data["provider"] = str(self.provider or "")
            data["quota"] = dict(self.quota)
        return data


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
                          scope: Any = None,
                          provider: Any = None,
                          quota: Any = None) -> MemoryResolution:
    """(memory_mode, memory_scope[, provider, quota]) → MemoryResolution（**不抛**）

    失败一律收口为 error 非空（端点据此 400）：
      · 未知档位 ⇒ error；
      · none：与改动前逐字一致（给非空 scope 仍 error）；
      · brokered：与改动前逐字一致（不需要三要素）；给 quota ⇒ error（不静默忽略）；
      · scoped：**显式开启的受控档**，要求 provider 非空且 scope 含
        tenant_id/workspace_id/subject_id 三要素，缺任一 ⇒ error（fail-closed）；
      · scope 非对象 / 含未知键 ⇒ error；memory_quota 非对象 / 未知键 / 非正整数 ⇒ error。
    """
    try:
        normalized = normalize_memory_mode(mode)
    except MemoryConfigError as e:
        return MemoryResolution(mode=str(mode or ""), error=str(e))

    try:
        clean_scope = _clean_scope(scope)
    except MemoryConfigError as e:
        return MemoryResolution(mode=normalized, error=str(e))

    try:
        clean_quota = normalize_memory_quota(quota)
    except MemoryQuotaConfigError as e:
        return MemoryResolution(mode=normalized, error=str(e))

    if normalized == MEMORY_MODE_NONE:
        if clean_scope:
            return MemoryResolution(
                mode=normalized, error=(
                    "memory_mode='none' 却给了非空 memory_scope：默认档不接受记忆域，"
                    "请显式 memory_mode='brokered'（不静默忽略 —— 那会让你以为它生效了）"))
        if clean_quota:
            return MemoryResolution(
                mode=normalized, error=(
                    "memory_mode='none' 却给了 memory_quota：默认档不使用配额，"
                    "请显式 memory_mode='scoped'（不静默忽略）"))
        return MemoryResolution(mode=normalized, scope=clean_scope, implemented=True)

    if normalized == MEMORY_MODE_BROKERED:
        if clean_quota:
            return MemoryResolution(
                mode=normalized, error=(
                    "memory_mode='brokered' 不接受 memory_quota：brokered 为母体代管只读，"
                    "配额仅 scoped 档使用（不静默忽略）"))
        return MemoryResolution(
            mode=normalized, scope=clean_scope, implemented=True, audit_required=True)

    # ── scoped：显式开启的受控档（三要素 + provider 缺一不可）──
    provider_name = str(provider or "").strip()
    if not provider_name:
        return MemoryResolution(
            mode=normalized, implemented=True, error=(
                "memory_mode='scoped' 需要非空 memory_provider（分身自带私人记忆域必须有"
                "明确 provider；缺失即拒绝，不静默降级）"))
    from agent.memory.scoped_store import SUPPORTED_MEMORY_PROVIDERS

    canonical_provider = normalize_memory_provider(provider_name)
    if canonical_provider not in SUPPORTED_MEMORY_PROVIDERS:
        return MemoryResolution(
            mode=normalized, implemented=True, error=(
                "memory_mode='scoped' 的 memory_provider=%r 未接线；已支持: %s"
                "（不静默回退默认后端）"
                % (provider_name, " / ".join(SUPPORTED_MEMORY_PROVIDERS))))
    missing = [k for k in SCOPED_REQUIRED_SCOPE_KEYS
               if not str(clean_scope.get(k) or "").strip()]
    if missing:
        return MemoryResolution(
            mode=normalized, implemented=True, error=(
                "memory_mode='scoped' 的 memory_scope 缺三要素 %s"
                "（tenant_id / workspace_id / subject_id 缺一不可；不静默降级）"
                % " / ".join(missing)))
    return MemoryResolution(
        mode=normalized, scope=clean_scope, implemented=True, audit_required=True,
        provider=canonical_provider, quota=clean_quota)


def _config_field(config: Any, name: str, default: Any = None) -> Any:
    """从配置对象或 Mapping 读字段（兼容两种形态，口径唯一）"""
    if isinstance(config, Mapping):
        return config.get(name, default)
    return getattr(config, name, default)


def resolve_config_memory(config: Any) -> MemoryResolution:
    """配置对象 / Mapping → MemoryResolution（**四处判定与路由共用的同一入口**）"""
    return resolve_memory_config(
        _config_field(config, "memory_mode", MEMORY_MODE_NONE),
        _config_field(config, "memory_scope", None),
        _config_field(config, "memory_provider", ""),
        _config_field(config, "memory_quota", None))


def scoped_memory_enabled(config: Any) -> bool:
    """配置 → 是否**已显式且完整**开启 scoped 私人记忆域（唯一口径）

    这是四处判定（actor_matrix / toolset / assembly / capability_exposure）共用的
    同一份判据：只有 memory_mode='scoped' 且三要素齐全且 provider 非空才为 True。
    默认档（none/brokered/未表态）恒为 False。
    """
    plan = resolve_config_memory(config)
    return bool(plan.ok and plan.is_scoped)


def scoped_guard(config: Any, *, audit: Any = None, actor: str = "", subject: str = "",
                 clock: Any = None) -> Any:
    """配置 → scoped 配额/熔断守卫（非 scoped 或配置非法 ⇒ None）

    配额/熔断为**显式开启**的一部分；调用方拿到 None 即不应走 scoped 写入路径。
    """
    plan = resolve_config_memory(config)
    if not (plan.ok and plan.is_scoped):
        return None
    return guard_from_quota(plan.quota, audit=audit, actor=actor, subject=subject,
                            clock=clock)


def memory_view(config: Any, guard: Any = None) -> Dict[str, Any]:
    """配置 → memory 段投影（创建/列表/热更新回显用；**不含记忆正文**）

    none / brokered 的输出与改动前**逐字一致**；scoped 额外回显 provider/store/
    离线 degraded、quota 与熔断状态（guard 缺省 ⇒ 按配置构造静态视图，breaker 恒 closed）。
    """
    plan = resolve_config_memory(config)
    view = plan.to_dict()
    view["provider"] = str(_config_field(config, "memory_provider", "") or "")
    if plan.ok and plan.is_scoped:
        live = guard or guard_from_quota(plan.quota)
        view["breaker"] = live.status()
        # 真实承载面（provider→后端标签 + 离线可用性）——回显事实，不承诺实现细节
        try:
            from agent.memory.scoped_store import provider_runtime_view

            view.update(provider_runtime_view(plan.provider))
        except Exception as e:  # noqa: BLE001 承载面不可用 ⇒ 如实降级，不让回显挂掉
            view["store"] = ""
            view["available"] = False
            view["degraded"] = "provider_view_unavailable:%s" % type(e).__name__
    return view


def scoped_memory_domain(config: Any, *, audit: Any = None, root: str = "",
                         store: Any = None, store_factory: Any = None) -> Any:
    """配置 → scoped 私人记忆域（非 scoped 或配置非法 ⇒ None）

    真实读写后端的选择与守卫在 agent/memory/scoped_store.py；本函数只是配置桥
    （与 scoped_guard 同款：调用方拿到 None 即不应走 scoped 写入路径）。
    """
    plan = resolve_config_memory(config)
    if not (plan.ok and plan.is_scoped):
        return None
    from agent.memory.scoped_store import scoped_domain_from_scope

    return scoped_domain_from_scope(
        plan.provider, plan.scope, quota=plan.quota, audit=audit, root=root,
        store=store, store_factory=store_factory)


def attach_memory_metadata(ctx: Any, config: Any, audit: Any = None) -> Any:
    """把生效档位与域**标识**写进委派上下文 metadata（config→ctx 桥）

    【为什么就地写】委派上下文是 frozen dataclass；本函数被
    SubagentContainer.run_delegation（单发/具名/工具路径）与
    SubagentLifecycleManager._prepare_config（delegate/delegate_many 共用）调用，
    后者只返回 config、不返回 ctx，故对 metadata 这一层做就地更新
    （标识级，幂等）。memory_mode='none' ⇒ **完全不碰** ctx（逐字旧行为）。

    【scoped 开启审计】显式开启 scoped 是安全姿态变更；传入 audit 时写一条
    subagent.memory.scoped.enabled（fail-soft）。默认档不写（噪声不是留痕）。

    Returns:
        传入的 ctx（便于链式调用）。
    """
    normalized = str(_config_field(config, "memory_mode", MEMORY_MODE_NONE)
                     or "").strip().lower()
    if normalized in _NONE_SENTINELS or normalized == MEMORY_MODE_NONE:
        return ctx
    scope = _config_field(config, "memory_scope", None)
    if normalized == MEMORY_MODE_SCOPED and audit is not None:
        emit_scoped_audit(
            audit, AUDIT_SCOPED_ENABLED,
            actor=str(getattr(ctx, "delegate_actor", "") or "sub_agent"),
            subject="delegation:%s" % (getattr(ctx, "delegation_id", "") or ""),
            payload={"mode": "scoped",
                     "provider": str(_config_field(config, "memory_provider", "") or ""),
                     "scope_keys": sorted(str(k) for k in (scope or {}) if str(k))},
            status="enabled")
    current = getattr(ctx, "metadata", None)
    meta: Dict[str, Any] = dict(current) if isinstance(current, Mapping) else {}
    meta["memory_mode"] = normalized
    if isinstance(scope, Mapping) and scope:
        meta["memory_scope"] = dict(scope)
    if normalized == MEMORY_MODE_SCOPED:
        # scoped 额外携带 provider 与配额**标识**（供执行器回显/留痕；不含正文）
        meta["memory_provider"] = str(_config_field(config, "memory_provider", "") or "")
        quota = _config_field(config, "memory_quota", None)
        if isinstance(quota, Mapping) and quota:
            meta["memory_quota"] = dict(quota)
    if isinstance(current, dict):
        current.clear()
        current.update(meta)
    else:
        # metadata 是其它 Mapping（不可变）⇒ 绕过 frozen 只替换该字段
        object.__setattr__(ctx, "metadata", meta)
    return ctx
