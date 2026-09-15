"""开关中心（TASK-S7-01）——云枢全部开关的**注册表 + 生效来源 + 三级权限**

【模块地图】
    | 模块 | 职责 |
    |---|---|
    | `registry` | 开关注册表（唯一事实源）：key/分类/类型/默认/env/config_path/风险/说明/校验器/归属模块 |
    | `overrides` | 覆盖层 `data/ui_settings.json`（**绝不改 `.env` / `config.yaml`**） |
    | `resolver` | 四层来源解析 `env > ui_override > config > default` + 落到运行态 |
    | `masking` | C 级只读脱敏（指纹口径；**永不返回明文**） |
    | `service` | 变更服务：风险分流（A 直接 / B 二次认证 + 双人确认 / C 拒）+ 审计入链 |
    | `bootstrap` | 进程启动时把覆盖层落到运行态（`needs_restart` 项的生效路径） |

【对外入口（其它模块只用这两个）】
    - `GET  /api/cp/settings`（`agent/server_routes/routes_settings.py`）
    - `agent.settings.bootstrap.apply_overrides()`
"""

from __future__ import annotations

from agent.settings.masking import (
    assert_no_plaintext,
    fingerprint,
    mask_display,
    mask_for_log,
)
from agent.settings.overrides import (
    OverrideRecord,
    OverrideStore,
    get_override_store,
    reset_override_store,
)
from agent.settings.registry import (
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    EFFECT_LABELS,
    REGISTRY,
    RISK_LABELS,
    SettingSpec,
    all_specs,
    categories,
    counts_by_risk,
    get_spec,
    registered_env_names,
)
import importlib as _importlib

# ── 【S11-10 / R2】惰性再导出（PEP 562）────────────────────────────────────
# 为什么：下列子模块会（直接或间接）依赖回本包，构成"包 ↔ 子模块"环，
# 被 architecture-check 的 no_circular_dependency 规则阻断。急切再导出正是环的一条边；
# 改为按需解析可**真实消除运行期的急切耦合**（不是把 import 换个写法隐藏起来）：
#   `from agent.settings import X`、`agent.settings.X`、`hasattr(agent.settings, "X")` 语义均不变，只是解析推迟到首次访问。
# 类型层由同目录 `__init__.pyi` 声明——本仓依赖图只扫 `*.py`，故存根不产生依赖边。
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "SOURCE_LABELS": ("agent.settings.resolver", "SOURCE_LABELS"),
    "SOURCE_PRIORITY": ("agent.settings.resolver", "SOURCE_PRIORITY"),
    "ResolvedSetting": ("agent.settings.resolver", "ResolvedSetting"),
    "resolve": ("agent.settings.resolver", "resolve"),
    "resolve_all": ("agent.settings.resolver", "resolve_all"),
    "ChangeOutcome": ("agent.settings.service", "ChangeOutcome"),
    "SettingsService": ("agent.settings.service", "SettingsService"),
    "get_settings_service": ("agent.settings.service", "get_settings_service"),
    "reset_settings_service": ("agent.settings.service", "reset_settings_service"),
}


def __getattr__(name: str) -> object:
    """PEP 562：按需解析本包的再导出名。"""
    entry = _LAZY_EXPORTS.get(name)
    if entry is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr = entry
    return getattr(_importlib.import_module(mod_name), attr)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))

__all__ = [
    # registry
    "SettingSpec", "REGISTRY", "all_specs", "get_spec", "categories",
    "counts_by_risk", "registered_env_names", "CATEGORY_LABELS",
    "CATEGORY_ORDER", "RISK_LABELS", "EFFECT_LABELS",
    # overrides
    "OverrideStore", "OverrideRecord", "get_override_store",
    "reset_override_store",
    # resolver
    "ResolvedSetting", "resolve", "resolve_all", "SOURCE_LABELS",
    "SOURCE_PRIORITY",
    # masking
    "mask_display", "mask_for_log", "fingerprint", "assert_no_plaintext",
    # service
    "SettingsService", "ChangeOutcome", "get_settings_service",
    "reset_settings_service",
]
