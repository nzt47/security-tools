"""特性开关（feature flag）统一求值入口 —— **骨架 + 阶段开关族登记处**（TASK-03 第 6 步）

【本模块解决什么】
    仓库现状：唯一的特性开关 `YUNSHU_FEATURE_SANDBOX` 由**多处**各自
    `os.getenv(...).lower() == "true"` 解析（`app_server.py:1481`、
    `plugins/system_tools.py:232`、`agent/server_routes/routes_workspace.py:172`）。
    各处自行解析的后果是三件事无处安放：
        1. **灰度**：没法只对一部分流量开；
        2. **审计**：开关被谁在什么时候翻过，没有记录；
        3. **一键回退**：改环境变量要重启进程（冷启动 60–90s）。
    而 v1.4 第 14 章要求"每阶段特性开关 + 一键回退"，`TASK-04~08` 全部依赖它。

【本模块与"配置项"的区别（**核心约定**）】
    | | 配置项（settings registry） | 特性开关（本模块） |
    |---|---|---|
    | 定位 | 系统参数：阈值/路径/等级 | 行为灰度：功能开不开 |
    | 生命周期 | 启动时读取，改多半要重启 | **运行时可切**，必须能一键回退 |
    | 守卫 | 零缺口 AST 守卫（D5） | 沿用同一张注册表 + 本模块的表驱动守卫 |
    ⇒ 特性开关**不是**第二套配置系统：它的默认值写在 `config.yaml` 的
      `features:` 节（**已存在**，`config.yaml:196`），运行期覆盖走
      `agent/settings` 的覆盖层，环境变量覆盖走 `YUNSHU_FEATURE_<NAME>` 前缀族。
      **禁止**为特性开关另建存储或另建 resolver。

【求值优先级（沿用 settings 的四层口径，不另造）】
    `env(YUNSHU_FEATURE_<NAME>)` > `覆盖层(data/ui_settings.json)` > `config.yaml:features.<name>` > 代码默认值
    实现方式：本模块只负责**声明 + 灰度 + 审计**，取值委托给
    `agent.settings.resolver.resolve()`（它的 docstring 自述就是
    "四层来源解析 env > ui_override > config > default"）。

【为什么环境变量是"前缀族"而不是"一个开关一个 env 名"】
    TASK-03 §2.8 记录：注册表有 `test_registry_has_no_phantom_switches`
    ——"注册了但代码没读 ⇒ 红"。若为每个开关都写一个字面量 env 名，代码里
    只有一处**动态拼接**的读取点，注册表里那些字面量行就全成了"幽灵开关"。
    所以特性开关的环境变量按**动态前缀族**登记（一次登记覆盖全部开关），
    新增开关只需在 `FEATURES` 表加一行 —— 这正是 E13 要量化的成本。

【诚实声明（骨架的边界）】
    本模块是**骨架**：`FEATURES` 表里的 5 条是给 `TASK-04~08` 预留的**阶段开关**，
    对应功能尚未实现 ⇒ 现在读它们只会拿到默认值 `False`。它们的作用是
    "先把登记与守卫跑通，让后续任务不再为加开关付学费"。
    已知债务：`YUNSHU_FEATURE_SANDBOX` 的 3 处直读**尚未收口**到本入口
    （收口属业务代码改动，越界风险见 TASK-03 E9），登记在
    `docs/rfc/特性开关规范.md` §6。
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 特性开关的环境变量前缀（**动态家族**；注册表以 dynamic_prefix 登记，见 registry.py）。
#: Why 单独定一个前缀：与配置项（`CP_*`、业务名）区分开，一眼能看出"这是个灰度开关"。
ENV_PREFIX = "YUNSHU_FEATURE_"

#: 灰度比例字段的后缀：`YUNSHU_FEATURE_<NAME>_ROLLOUT`（0–100）。
ROLLOUT_SUFFIX = "_ROLLOUT"

#: 生效方式：全部为 `hot`（下次求值即生效），因为本模块每次调用都重新求值。
EFFECT_HOT = "hot"


@dataclass(frozen=True)
class FeatureSpec:
    """一个特性开关的声明（**表驱动**；新增开关只加一行）"""

    name: str                # 规范名（小写下划线；env 名 = ENV_PREFIX + name.upper()）
    default: bool            # 代码默认值（config.yaml 缺项时用它）
    description: str         # 人读说明："这个开关控制什么行为"
    owner: str               # 归属模块（**必须是真实存在的文件**，由守卫断言）
    stage: str               # 归属阶段（TASK-04~08）；`builtin` = 既有开关
    rollout_default: int = 100   # 默认灰度比例（0–100）；100 = 全量

    @property
    def env_name(self) -> str:
        return ENV_PREFIX + self.name.upper()

    @property
    def rollout_env_name(self) -> str:
        return self.env_name + ROLLOUT_SUFFIX

    @property
    def config_path(self) -> str:
        return f"features.{self.name}"


# ════════════════════════════════════════════════════════════
#  阶段开关族（TASK-04~08 的注册处）
# ════════════════════════════════════════════════════════════
# 纪律：**任何阶段的任何新开关都必须登记在这里**，否则
# `tests/unit/test_feature_flags.py` 的守卫会红（双向断言：表 ↔ 注册表 ↔ config.yaml）。
FEATURES: Tuple[FeatureSpec, ...] = (
    # ── TASK-04：能力规格与分类正式化 ──
    FeatureSpec(
        name="capability_spec_v2", default=False, stage="TASK-04",
        description="启用 CapabilitySpec v2 字段集（version/owner/location/output_schema）的读写路径",
        owner="agent/settings/feature_flags.py",
    ),
    # ── TASK-05：Registry + Loader + 非 LLM 入口 ──
    FeatureSpec(
        name="capability_registry", default=False, stage="TASK-05",
        description="能力统一 Registry 作为派生视图生效（关闭时回落现有 YAML 扫描 + 内存 _registry）",
        owner="agent/settings/feature_flags.py",
    ),
    FeatureSpec(
        name="capability_non_llm_entry", default=False, stage="TASK-05", rollout_default=10,
        description="能力层的非 LLM 入口（service_account / human 触发）生效",
        owner="agent/settings/feature_flags.py",
    ),
    # ── TASK-06：身份与工具侧 L0–L3 确认分级 ──
    FeatureSpec(
        name="tool_confirm_levels", default=False, stage="TASK-06",
        description="工具侧 L0–L3 确认分级生效（关闭时回落现有'是否挂单'二值语义）",
        owner="agent/settings/feature_flags.py",
    ),
    # ── TASK-07：安全接线 ──
    FeatureSpec(
        name="sandbox_unified", default=False, stage="TASK-07",
        description="SSRF / 注入隔离 / 沙箱统一收口生效（关闭时走既有分散实现）",
        owner="agent/settings/feature_flags.py",
    ),
    # ── TASK-08：性能容量（P0-1 一行可修项）──
    FeatureSpec(
        name="hot_path_load_tool_meta_cache", default=False, stage="TASK-08",
        description="load_tool_meta() 的 CSafeLoader + 结果缓存生效（关闭时走原 yaml.safe_load 路径）",
        owner="agent/settings/feature_flags.py",
    ),
)

_BY_NAME: Dict[str, FeatureSpec] = {f.name: f for f in FEATURES}


# ════════════════════════════════════════════════════════════
#  环境变量读取点（**唯一的动态前缀族读取点**）
# ════════════════════════════════════════════════════════════
# ⚠️ 本函数是 `scripts/scan_settings.py` 用 AST 机械提取的读取点，被登记为
#    `dynamic_prefix="YUNSHU_FEATURE_"`（见 `agent/settings/registry.py` 的
#    "特性开关族" 段）。**不要**把它改写成 `os.getenv("YUNSHU_FEATURE_XXX")`
#    这样的字面量写法 —— 那会让注册表里的动态家族声明变成"声明了却从未命中"，
#    守卫用例 `test_dynamic_families_match_registry_declarations` 立刻变红。


def _env_text(name: str) -> Optional[str]:
    """读取 `YUNSHU_FEATURE_<NAME>` 的原始文本（未设置返回 None）。

    Why 单独抽一层：把"名字拼接"集中在一处，AST 提取器才认得出这是
    **前缀家族**（而不是一堆互不相关的字面量），注册表也才能用**一行**
    动态家族声明覆盖全部特性开关。
    """
    return os.getenv(ENV_PREFIX + name.upper())


def _env_rollout_text(name: str) -> Optional[str]:
    """读取 `YUNSHU_FEATURE_<NAME>_ROLLOUT` 的原始文本。

    Why 也必须写成"前缀 + 名字"的拼接，而不是 `os.getenv(spec.rollout_env_name)`：
    后者传的是**属性访问**，AST 提取器解析不出名字 ⇒ 整个读取点落进
    `<unresolved>` 兜底桶。实测（TASK-03）：写成属性访问时
    `check_gaps().unregistered_dynamic == ['<unresolved>', 'YUNSHU_FEATURE_']`，
    零缺口守卫变红；而 `<unresolved>` 是**兜底桶**，登记它等于开一个
    什么开关都能藏进去的黑洞——宁可改写法，也不登记兜底桶。
    """
    return os.getenv(ENV_PREFIX + name.upper() + ROLLOUT_SUFFIX)


def _to_bool(raw: Optional[str], default: bool) -> bool:
    """把环境变量文本转成布尔（与既有 `YUNSHU_FEATURE_SANDBOX` 口径一致）"""
    if raw is None:
        return default
    return raw.strip().lower() == "true"


# ════════════════════════════════════════════════════════════
#  灰度分桶
# ════════════════════════════════════════════════════════════

def rollout_bucket(name: str, subject: str) -> int:
    """把 `subject` 稳定地映射到 0–99（同一 subject 永远同一个桶）。

    Why 用哈希而不是随机：灰度必须**稳定**——同一个租户/会话这次见到新功能、
    下次见不到，会造出无法复现的 bug 报告。用 `sha256(name + subject)` 保证
    "同一开关 + 同一主体" 永远落在同一个桶；不同开关之间独立分桶（避免
    "命中一个开关就命中全部"的强相关）。
    """
    digest = hashlib.sha256(f"{name}:{subject}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 100


def _rollout_percentage(spec: FeatureSpec) -> int:
    """取灰度比例：env > 代码默认（0–100 裁剪）"""
    raw = _env_rollout_text(spec.name)
    value = spec.rollout_default
    if raw is not None:
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            logger.warning(
                "[FeatureFlags] %s 的值 %r 不是整数，回落默认 %s",
                spec.rollout_env_name, raw, spec.rollout_default,
            )
            value = spec.rollout_default
    return max(0, min(100, value))


# ════════════════════════════════════════════════════════════
#  审计（best-effort，**绝不因审计失败而阻断业务**）
# ════════════════════════════════════════════════════════════

def audit_change(name: str, value: Any, *, actor: str = "system",
                 source: str = "", subject: str = "") -> None:
    """把一次开关求值/变更落审计（D4：失败只告警，**不得阻塞**）。

    Why 复用 `agent.audit.facade`：审计链（`agent/audit/chain.py`）是
    append-only + 哈希链的实现，本模块不该再造一套。facade 不可用时
    降级为结构化日志——**特性开关的读路径不能因为审计不可用而抛错**。
    """
    payload = {"feature": name, "value": value, "source": source, "subject": subject}
    try:
        from agent.audit.facade import record as _record

        _record("feature_flag.evaluated", actor=actor, subject=name, payload=payload)
    except Exception as exc:  # noqa: BLE001 审计不可用不得影响业务读路径
        logger.debug("[FeatureFlags] 审计写入跳过（%s）: %s", name, exc)


# ════════════════════════════════════════════════════════════
#  求值与枚举（对外入口）
# ════════════════════════════════════════════════════════════

def get_spec(name: str) -> Optional[FeatureSpec]:
    """按规范名取声明（未声明 → None，调用方 fail-closed 返回 False）"""
    return _BY_NAME.get(name)


def feature_state(name: str, *, subject: str = "", store: Any = None) -> Dict[str, Any]:
    """返回一个开关的**完整状态**（值 + 来源 + 灰度 + 生效方式）。

    Why 需要"状态"而不只是"布尔值"：`TASK-04~08` 的问题排查会反复问
    "这个开关到底为什么是 false" ——是 env 钉的？覆盖层钉的？灰度没轮到？
    还是压根没声明？只返回布尔值就无法回答，只能靠猜。
    """
    spec = get_spec(name)
    if spec is None:
        return {"name": name, "declared": False, "enabled": False,
                "reason": "未在 FEATURES 表中声明（fail-closed）"}

    base = spec.default
    source = "default"
    keys = ("features", name)

    # 1) 环境变量覆盖（最高优先级）
    raw = _env_text(name)
    if raw is not None:
        base = _to_bool(raw, spec.default)
        source = f"env:{spec.env_name}"
    else:
        # 2) 覆盖层 → 3) config.yaml（顺序与 agent.settings.resolver 保持一致）
        try:
            from agent.settings.overrides import get_override_store

            _store = store or get_override_store()
            rec = _store.get(f"feature.{name}")
            if rec is not None:
                base = bool(rec.value)
                source = "override:data/ui_settings.json"
            else:
                from agent.settings.resolver import _config_lookup  # noqa: PLC2701

                found = _config_lookup(".".join(keys))
                if isinstance(found, bool):
                    base = found
                    source = f"config:config.yaml:{spec.config_path}"
        except Exception as exc:  # noqa: BLE001 配置层不可用时回落默认值
            logger.debug("[FeatureFlags] %s 配置层解析跳过: %s", name, exc)

    pct = _rollout_percentage(spec)
    if not base:
        reason = "开关关闭"
        effective = False
    elif pct >= 100 or not subject:
        reason = "开关开启" + ("" if pct >= 100 else "（未给 subject，跳过灰度判定）")
        effective = True
    else:
        bucket = rollout_bucket(name, subject)
        effective = bucket < pct
        reason = f"灰度 {bucket}/{pct}" + ("（命中）" if effective else "（未命中）")

    return {
        "name": name,
        "declared": True,
        "base": base,
        "source": source,
        "rollout_percentage": pct,
        "subject": subject,
        "enabled": effective,
        "reason": reason,
        "effect": EFFECT_HOT,
        "stage": spec.stage,
        "owner": spec.owner,
        "description": spec.description,
    }


def feature_enabled(name: str, *, subject: str = "", audit: bool = False) -> bool:
    """**统一求值入口**：`feature_enabled("capability_registry")`。

    Why 是函数的默认形态而不是常量：特性开关必须"运行时可切"，
    任何把值缓存在模块级常量里的写法都会让"一键回退"变成"重启才回退"。

    `audit=True` 时才落审计：读路径默认不打审计（否则每次工具调用都写一条
    审计链记录，会把链撑爆，属于典型的"可观测性反过来拖垮性能"）。
    变更路径（`agent/settings/service.py`）应当显式传 `audit=True`。
    """
    state = feature_state(name, subject=subject)
    if audit:
        audit_change(name, state.get("enabled"), source=str(state.get("source", "")))
    return bool(state.get("enabled"))


def list_features() -> List[Dict[str, Any]]:
    """列出全部特性开关的当前状态（UI / CLI / 测试用）"""
    return [feature_state(f.name) for f in FEATURES]


def feature_names() -> Tuple[str, ...]:
    return tuple(f.name for f in FEATURES)


__all__ = [
    "ENV_PREFIX", "ROLLOUT_SUFFIX", "EFFECT_HOT",
    "FeatureSpec", "FEATURES",
    "get_spec", "feature_state", "feature_enabled", "list_features",
    "feature_names", "rollout_bucket", "audit_change",
]
