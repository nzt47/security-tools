"""生效来源解析——**不谎报**（TASK-S7-01 步骤 2 的技术核心）

【要解决的坑（UI 五坑 + S7 纪律）】
    "UI 改了但没生效" 与 "面板显示的值不是真实生效的值"。故本模块把
    **四层来源**逐条摊开，并如实标注"被谁覆盖"：

        env（运维注入） > ui_override（覆盖层 / 本进程热写入） > config（config.yaml
        / ObservabilityConfig 运行态） > default（代码默认）

【两个关键判定】
    1. **env 是否"运维设置的"**：进程启动时若 env 已存在且不是本进程为热生效
       写进去的（`OverrideStore.env_applied`），才算运维来源 → 该开关**置灰**，
       并在 `locked_reason` 里写明"被 X 覆盖，UI 不可改"。
    2. **能不能改**（`editable`）：只有"改完真的会生效"的开关才可编辑：
       - 有 env_name 且未被运维 env 锁定 → 可改（写 env，热生效；或按需重启）；
       - 是 ObservabilityConfig 的配置路径 → 可改（走该模块的运行态 set，热生效）；
       - 仅存在于 config.yaml 的项 → **不可改**（守不易：不修改 config.yaml），
         如实标注原因，UI 置灰；
       - C 级（密钥/端点/路径）与动态开关族 → 只读脱敏。

【本模块不做的事】
    不改任何既有模块的读取路径（守不易：不动既有公开接口行为）；只在**改开关**
    时把覆盖值落到既有的读取入口（os.environ / ObservabilityConfig）。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.settings import masking
from agent.settings.overrides import OverrideStore, get_override_store, repo_root
from agent.settings.registry import (
    CATEGORY_LABELS,
    EFFECT_HOT,
    RISK_C,
    SettingSpec,
    all_specs,
    get_spec,
    observability_rule_paths,
)

logger = logging.getLogger(__name__)

#: 来源标识（顺序即优先级）
SOURCE_ENV = "env"
SOURCE_OVERRIDE = "ui_override"
SOURCE_CONFIG = "config"
SOURCE_DEFAULT = "default"

SOURCE_PRIORITY: tuple = (SOURCE_ENV, SOURCE_OVERRIDE, SOURCE_CONFIG,
                          SOURCE_DEFAULT)

#: 来源中文标签（前端只用本表，不自造）
SOURCE_LABELS: Dict[str, str] = {
    SOURCE_ENV: "环境变量（运维注入）",
    SOURCE_OVERRIDE: "开关中心覆盖层",
    SOURCE_CONFIG: "config.yaml / 运行时配置",
    SOURCE_DEFAULT: "代码默认值",
}

_SENTINEL = object()


# ════════════════════════════════════════════════════════════
#  config.yaml 读取（只读；**永不写**）
# ════════════════════════════════════════════════════════════

_CONFIG_CACHE: Dict[str, Any] = {"mtime": -1.0, "data": {}}


def config_yaml_path() -> Path:
    return repo_root() / "config.yaml"


def read_config_yaml() -> Dict[str, Any]:
    """读 `config.yaml`（只读 + mtime 缓存；任何异常都降级为空 dict）"""
    path = config_yaml_path()
    try:
        mtime = path.stat().st_mtime if path.exists() else -1.0
    except OSError:                                    # pragma: no cover
        mtime = -1.0
    if _CONFIG_CACHE["mtime"] == mtime and _CONFIG_CACHE["data"] is not None:
        cached = _CONFIG_CACHE["data"]
        return dict(cached) if isinstance(cached, dict) else {}
    data: Dict[str, Any] = {}
    if path.exists():
        try:
            import yaml
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                data = loaded
        except Exception as e:                         # noqa: BLE001 配置坏了不影响 UI
            logger.warning("[Settings] config.yaml 读取失败（按空处理）: %s", e)
    _CONFIG_CACHE["mtime"] = mtime
    _CONFIG_CACHE["data"] = data
    return data


def _config_lookup(dot_path: str) -> Any:
    """按点分路径取值（不存在 → 哨兵）"""
    if not dot_path:
        return _SENTINEL
    node: Any = read_config_yaml()
    for part in dot_path.split("."):
        if not isinstance(node, dict) or part not in node:
            return _SENTINEL
        node = node[part]
    return node


def _observability_lookup(dot_path: str) -> Any:
    """从 ObservabilityConfig 运行态取值（不存在 → 哨兵）

    ★ 这是**运行态**而非文件：`resource_monitor.sample_interval_sec` 一类阈值
      的真实生效值就在这个对象里，UI 的修改也落到它身上（热生效）。
    """
    try:
        from agent.monitoring.observability_config import (
            get_observability_config,
        )
        cfg = get_observability_config()
        value = cfg.get(dot_path, _SENTINEL)
        return value
    except Exception as e:                             # noqa: BLE001
        logger.debug("[Settings] 可观测配置读取失败 %s: %s", dot_path, e)
        return _SENTINEL


# ════════════════════════════════════════════════════════════
#  解析结果
# ════════════════════════════════════════════════════════════

@dataclass
class ResolvedSetting:
    """一个开关的"真实生效状态"（元数据 + 值 + 来源 + 可改性）"""

    spec: SettingSpec
    value: Any = None
    source: str = SOURCE_DEFAULT
    shadowed_by: List[str] = field(default_factory=list)
    env_present: bool = False
    env_locked: bool = False
    override_present: bool = False
    config_present: bool = False
    editable: bool = False
    locked_reason: str = ""
    hot_applied: bool = False
    masked: bool = False
    configured: bool = True
    display_value: Any = None
    fingerprint: str = ""
    value_len_bucket: str = ""

    # ── 投影 ──

    def to_public_dict(self) -> Dict[str, Any]:
        """对外投影（**C 级不含明文**）"""
        out = dict(self.spec.to_public_dict())
        out.update({
            "source": self.source,
            "source_label": SOURCE_LABELS.get(self.source, self.source),
            "shadowed_by": list(self.shadowed_by),
            "env_present": bool(self.env_present),
            "env_locked": bool(self.env_locked),
            "override_present": bool(self.override_present),
            "config_present": bool(self.config_present),
            "editable": bool(self.editable),
            "locked": not bool(self.editable),
            "locked_reason": self.locked_reason,
            "hot_applied": bool(self.hot_applied),
            "masked": bool(self.masked),
            "configured": bool(self.configured),
            "value": self.value,
            "display_value": self.display_value,
            "fingerprint": self.fingerprint,
            "value_len_bucket": self.value_len_bucket,
        })
        return out

    def to_audit_leaves(self) -> Dict[str, Any]:
        """审计载荷叶子（C 级只留**指纹**；**不含明文**）

        说明：C 级的 `value` 在解析时已被抹成 None（明文不出 `resolve()`），
        故此处对 C 级输出 `old_fingerprint`——既能证明"值换没换过"，
        又不把明文写进链式审计。
        """
        def leaf(value: Any) -> Any:
            if self.spec.risk == RISK_C:
                return masking.mask_for_log(value) if value is not None else ""
            if isinstance(value, (bool, int, float)) or value is None:
                return value
            text = str(value)
            return text[:120]

        out = {
            "key": self.spec.key,
            "category": self.spec.category,
            "risk": self.spec.risk,
            "source": self.source,
            "shadowed_by": list(self.shadowed_by),
            "effect": self.spec.effect,
            "old": leaf(self.value),
            "editable": self.editable,
        }
        if self.spec.risk == RISK_C:
            out["old_fingerprint"] = self.fingerprint
            out["configured"] = self.configured
        return out


def _env_value(env_name: str) -> Any:
    return os.environ.get(env_name, _SENTINEL)


def _is_observability_path(spec: SettingSpec) -> bool:
    return bool(spec.config_path) and spec.config_path in observability_rule_paths()


def _coerce_from_env(spec: SettingSpec, raw: Any) -> Any:
    """把 env 字符串按声明类型还原（bool 采用"非 false 即 true"的既有口径）"""
    if raw is _SENTINEL:
        return _SENTINEL
    text = str(raw)
    if spec.type == "bool":
        return text.strip().lower() not in ("0", "false", "no", "off", "")
    if spec.type == "int":
        try:
            return int(float(text))
        except (TypeError, ValueError):
            return text
    if spec.type == "float":
        try:
            return float(text)
        except (TypeError, ValueError):
            return text
    return text


def _lock_reason(spec: SettingSpec, *, env_locked: bool,
                 config_only: bool) -> str:
    """置灰原因（UI 必须原样展示；**不允许含糊**）"""
    if spec.risk == RISK_C:
        if spec.secret:
            return ("C 级密钥/凭据：只读脱敏，永不返回明文，UI 不支持修改"
                    "（沿用 S4-01 裁定 B 的掩码口径）")
        return "C 级端点/路径类：只读脱敏展示，UI 不支持修改"
    if spec.dynamic_prefix:
        return (f"动态开关族 {spec.dynamic_prefix}*：名字由运行时拼接，"
                "UI 不支持逐条修改（请用环境变量按需配置）")
    if env_locked:
        return (f"被环境变量 {spec.env_name} 锁定："
                "env 优先级高于覆盖层，此处修改不会生效（UI 不可改）")
    if config_only:
        return ("该项仅存在于 config.yaml：守不易——开关中心不修改配置文件，"
                "如需变更请改 config.yaml 后重启")
    return ""


def resolve(key: str, *, store: Optional[OverrideStore] = None) -> Optional[ResolvedSetting]:
    """解析单个开关的真实生效状态（未知键 → None，调用方 fail-closed）"""
    spec = get_spec(key)
    if spec is None:
        return None
    store = store or get_override_store()
    env_present = bool(spec.env_name) and not store.env_applied(spec.env_name) \
        and spec.env_name in os.environ
    override = store.get(spec.key)
    obs_path = _is_observability_path(spec)
    config_value = _SENTINEL
    config_source_is_file = False
    if spec.config_path and not obs_path:
        config_value = _config_lookup(spec.config_path)
        config_source_is_file = config_value is not _SENTINEL
    obs_value = _observability_lookup(spec.config_path) if obs_path else _SENTINEL
    # 运行态与声明默认值不同 → 才算"config 提供了值"（否则就是默认值本身）
    config_present = config_source_is_file or (
        obs_value is not _SENTINEL and obs_value != spec.default)

    res = ResolvedSetting(spec=spec)
    res.env_present = env_present
    res.override_present = override is not None
    res.config_present = bool(config_present)

    # ── 取值 + 来源（严格按优先级）──
    if env_present:
        res.value = _coerce_from_env(spec, os.environ.get(spec.env_name))
        if res.value is _SENTINEL:                    # pragma: no cover 竞态兜底
            res.value = spec.default
        res.source = SOURCE_ENV
        res.env_locked = True
        if override is not None:
            res.shadowed_by.append(SOURCE_OVERRIDE)
        if config_present:
            res.shadowed_by.append(SOURCE_CONFIG)
    elif override is not None:
        res.value = override.value
        res.source = SOURCE_OVERRIDE
        res.hot_applied = spec.effect == EFFECT_HOT
        if config_present:
            res.shadowed_by.append(SOURCE_CONFIG)
    elif config_present:
        res.value = config_value if config_source_is_file else obs_value
        res.source = SOURCE_CONFIG
    else:
        res.value = spec.default
        res.source = SOURCE_DEFAULT

    # ── 可改性 ──
    config_only = bool(spec.config_path) and not spec.env_name and not obs_path
    res.locked_reason = _lock_reason(spec, env_locked=res.env_locked,
                                     config_only=config_only)
    res.editable = (
        spec.risk != RISK_C
        and not spec.dynamic_prefix
        and not res.env_locked
        and not config_only
        and (bool(spec.env_name) or obs_path)
    )

    # ── 展示口径（C 级永不含明文）──
    if spec.risk == RISK_C:
        masked = masking.mask_display(res.value)
        res.masked = True
        res.configured = masked["configured"]
        res.display_value = masked["display_value"]
        res.fingerprint = masked["fingerprint"]
        res.value_len_bucket = masked["value_len_bucket"]
        res.value = None                       # ★ 明文不出本函数
    else:
        res.display_value = _display(spec, res.value)
        res.configured = str(res.value) != ""
    return res


def _display(spec: SettingSpec, value: Any) -> str:
    """非 C 级的展示值（布尔/数值/短字符串直接展示；长字符串截断）"""
    if value is None:
        return "（未声明默认值）"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if len(text) > 120:
        return text[:117] + "..."
    return text or "（空）"


def resolve_all(*, store: Optional[OverrideStore] = None) -> List[ResolvedSetting]:
    """解析全部开关（UI 列表用）"""
    store = store or get_override_store()
    out: List[ResolvedSetting] = []
    for spec in all_specs():
        resolved = resolve(spec.key, store=store)
        if resolved is not None:
            out.append(resolved)
    return out


def apply_override_to_runtime(resolved: ResolvedSetting, *,
                              store: Optional[OverrideStore] = None) -> Dict[str, Any]:
    """把覆盖值落到**既有读取入口**（这是"改完真的生效"的唯一实现）

    落点只有两个（不改既有代码的读取路径）：
        - 有 env_name：写 `os.environ[env_name]`（并记账原值，便于 reset 还原）；
        - 是 ObservabilityConfig 路径：调用 `get_observability_config().set(path, v)`。

    Returns:
        `{"applied": bool, "target": str, "detail": str}`
    """
    store = store or get_override_store()
    spec = resolved.spec
    if spec.env_name and not resolved.env_locked:
        if spec.needs_restart:
            return {"applied": False, "target": "env",
                    "detail": "该开关标注 needs_restart：已写入覆盖层，"
                              "下次进程启动时由 bootstrap 应用"}
        previous = os.environ.get(spec.env_name)
        os.environ[spec.env_name] = _to_env_text(spec, resolved.value)
        store.mark_env_applied(spec.env_name, previous)
        return {"applied": True, "target": "env",
                "detail": f"已写入进程环境变量 {spec.env_name}（热生效）"}
    if _is_observability_path(spec):
        try:
            from agent.monitoring.observability_config import (
                get_observability_config,
            )
            ok = bool(get_observability_config().set(spec.config_path,
                                                      resolved.value))
            return {"applied": ok, "target": "observability",
                    "detail": (f"已写入运行态配置 {spec.config_path}"
                               if ok else "运行态写入被校验拒绝（值回退默认）")}
        except Exception as e:                          # noqa: BLE001
            return {"applied": False, "target": "observability",
                    "detail": f"运行态写入失败：{type(e).__name__}: {e}"}
    return {"applied": False, "target": "none",
            "detail": "该项没有可用的运行态落点（覆盖层已记录，但不生效）"}


def restore_runtime(key: str, *, store: Optional[OverrideStore] = None) -> Dict[str, Any]:
    """撤销运行态落点（reset 用）：还原本进程写过的 env / 回写运行态默认值"""
    store = store or get_override_store()
    spec = get_spec(key)
    if spec is None:
        return {"applied": False, "target": "none", "detail": "未知开关"}
    details: List[str] = []
    applied = False
    if spec.env_name and store.env_applied(spec.env_name):
        previous = store.env_backup(spec.env_name)
        if previous is None:
            os.environ.pop(spec.env_name, None)
            details.append(f"已移除本进程写入的 {spec.env_name}")
        else:
            os.environ[spec.env_name] = previous
            details.append(f"已还原 {spec.env_name} 为原值")
        store.forget_env_applied(spec.env_name)
        applied = True
    if _is_observability_path(spec):
        try:
            from agent.monitoring.observability_config import (
                get_observability_config,
            )
            ok = bool(get_observability_config().set(spec.config_path, spec.default))
            details.append(f"运行态 {spec.config_path} 回落到默认 "
                           f"{spec.default!r}（{ok}）")
            applied = applied or ok
        except Exception as e:                          # noqa: BLE001
            details.append(f"运行态回落失败：{type(e).__name__}")
    return {"applied": applied, "target": "runtime",
            "detail": "；".join(details) or "无需还原（未落到运行态）"}


def _to_env_text(spec: SettingSpec, value: Any) -> str:
    """覆盖值 → 环境变量文本

    bool 统一写 `true`/`false`：仓库里两种读取口径（"非 false 即 true" 与
    "必须命中 true 列表"）对 `true`/`false` 的解释一致，故这是唯一安全写法。
    """
    if spec.type == "bool" or isinstance(value, bool):
        return "true" if bool(value) else "false"
    if value is None:
        return ""
    return str(value)


def _json_scalar(value: Any) -> Any:
    """JSON 安全标量（审计 payload 用）"""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):                     # pragma: no cover
        return str(value)


__all__ = [
    "SOURCE_ENV", "SOURCE_OVERRIDE", "SOURCE_CONFIG", "SOURCE_DEFAULT",
    "SOURCE_PRIORITY", "SOURCE_LABELS", "ResolvedSetting", "resolve",
    "resolve_all", "read_config_yaml", "config_yaml_path",
    "apply_override_to_runtime", "restore_runtime", "CATEGORY_LABELS",
]
