"""自动生成的再导出类型存根（S11-10 / R2）。**仅供类型检查，不参与运行期。**

存在意义：让 `__init__.py` 可以安全地做 PEP 562 惰性再导出（打断"包 ↔ 子模块"环，
见 architecture-check 的 no_circular_dependency），同时保住 mypy 的静态类型。
本仓依赖图只扫 `*.py`，故本存根不产生依赖边。
改动 `__init__.py` 的再导出清单后，请重跑 `scripts/dev/gen_reexport_pyi.py`。
"""
from __future__ import annotations

from agent.settings.masking import (assert_no_plaintext as assert_no_plaintext, fingerprint as fingerprint, mask_display as mask_display, mask_for_log as mask_for_log)
from agent.settings.overrides import (OverrideRecord as OverrideRecord, OverrideStore as OverrideStore, get_override_store as get_override_store, reset_override_store as reset_override_store)
from agent.settings.registry import (CATEGORY_LABELS as CATEGORY_LABELS, CATEGORY_ORDER as CATEGORY_ORDER, EFFECT_LABELS as EFFECT_LABELS, REGISTRY as REGISTRY, RISK_LABELS as RISK_LABELS, SettingSpec as SettingSpec, all_specs as all_specs, categories as categories, counts_by_risk as counts_by_risk, get_spec as get_spec, registered_env_names as registered_env_names)
from agent.settings.resolver import (SOURCE_LABELS as SOURCE_LABELS, SOURCE_PRIORITY as SOURCE_PRIORITY, ResolvedSetting as ResolvedSetting, resolve as resolve, resolve_all as resolve_all)
from agent.settings.service import (ChangeOutcome as ChangeOutcome, SettingsService as SettingsService, get_settings_service as get_settings_service, reset_settings_service as reset_settings_service)
