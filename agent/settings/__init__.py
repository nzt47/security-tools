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
from agent.settings.resolver import (
    SOURCE_LABELS,
    SOURCE_PRIORITY,
    ResolvedSetting,
    resolve,
    resolve_all,
)
from agent.settings.service import (
    ChangeOutcome,
    SettingsService,
    get_settings_service,
    reset_settings_service,
)

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
