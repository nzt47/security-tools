"""成本刹车与断食（TASK-S5-03 / v7.2 §7 + P7.2-06 + §6.3 + §6.7）

本模块落地 v7.2 的**成本治理自动化**三层：

1. **日级硬熔断（P7.2-06，新增）** —— 当日归一成本 > ``budget.daily_cents``
   → 立即停**非关键** outbound（关键 = 用户显式请求 / 审批中任务），次日 00:00 自动恢复。
   *「日刹车管今天失控」*。
2. **周级断食（§7 滞后规则）** —— 进：日均成本 / 基线 > 1.3×（持续判定）；
   出：连续 24h ≤ 1.1×；出后冷却 12h（抑制抖动）。*「周刹车管持续失控」*。
3. **θ 分阶段阈值（§6.3）** —— W1-W4 不设 → W5-W9 ≤1.5×→1.3× → W10-W14 ≤1.0×
   → M4-M6 ≤0.8×→0.6× → M7+ ≤0.5×。**配置驱动**（`CP_BUDGET_THETA_TABLE` /
   `config.yaml:budget.theta`），表内数值是 §6.3 的**规格默认值**而非硬编码常量。

外加两项度量（披露不考核）：

4. **审批衰减率（§6.7「策略自动消化占比 ≥70%」）** —— 消费 S2-03 的 `approval`
   事件，计算自动消化占比 + 疲劳分桶分布；**只披露**，不阻断。
5. **性能预算口径**（见 `PERF_BUDGET_REBASED.md`）与 **shadow 开销审计**
   （`shadow_overhead_ms` 是否进入 UTC 口径）。

## 数据源与口径纪律（Owner 裁定 2026-09-11）

- **成本唯一数据源 ＝ 事件流**（`utc.record_cost()` → `data/events/`）。旧
  `data/cost_log.jsonl` 已**停写**，仅保留只读兼容（见 `agent.model_router.cost_tracker`
  与 `utc.reconcile_cost_log`）。
- **归一化口径版本 ＝ 价格锚定系数（未校准）**：`CALIBRATION_VERSION`；待 S5-02
  L2 Core-50 基线就绪后启动校准评估（建议季度复校）。本模块所有输出都带口径版本号。
- 本模块**不另建成本聚合**：日/周/窗口成本一律复用
  `agent.observability.utc.utc_daily()/utc_weekly()`。

## 安全底线（对齐批次总表 §三「通用硬约束」）

- **一切自动化开关默认关闭**：`CP_BUDGET_BRAKE_ENABLED` 未显式开启时，本模块
  **零影响** —— `allow_outbound()` 恒真、shadow 预算系数恒 1.0、不写状态、不发事件。
- **默认不误伤**：`allow_outbound()` 缺省 `kind="interactive"`（= 用户显式请求，
  属关键路径）恒放行；只有**显式声明**为非关键后台 kind 的调用方才可能被拦。
- **非法值回退默认**：所有阈值解析失败一律回退默认并告警（不抛、不静默）。
- **可观测**：状态、进入/退出时间、当前阈值来源、口径版本全部进 `status()`；
  每次状态跃迁写入链式审计 + 事件流。
- **可回滚**：状态机状态可持久化也可丢弃；影子预算系数与熔断判定彼此独立，
  关闭总开关即回到原行为。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time as _time, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent.observability import utc as _utc
from agent.observability.events import (
    ACTOR_AUTO,
    ACTOR_SYSTEM,
    EV_APPROVAL,
    EV_HEALING_TRIGGERED,
    EV_INTERVENTION,
    EV_METRICS_DELTA,
    emit,
    iter_events,
)

logger = logging.getLogger("agent.monitoring.cost_brake")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ════════════════════════════════════════════════════════════
#  口径版本（裁定 C：沿用价格锚定系数，本次不校准）
# ════════════════════════════════════════════════════════════

#: 本模块输出的口径版本（任何口径变更须改此号，便于追溯"当时按哪版判定"）
COST_SCHEMA_VERSION = "s5-03.v1"
#: 归一化系数口径版本（**当前＝价格锚定系数，未校准**）
CALIBRATION_VERSION = "price_anchor.v1"
#: 口径说明（裁定 C 的落地文案；进文档与指标输出）
CALIBRATION_NOTE = ("当前口径＝价格锚定系数（主力模型锚价 + 各模型价格比例），"
                    "**未经经验校准**")
#: 校准触发条件（裁定 C 后续触发条件，勿丢）
CALIBRATION_TRIGGER = ("待 S5-02 产出的 L2 Core-50 基线就绪后，启动校准评估"
                       "（跨模型成本-效果实测 → 经验系数 → 替换价格系数），"
                       "并定义校准数据来源与复校周期（建议季度）")
#: 成本唯一数据源（裁定 D）
COST_SOURCE_OF_TRUTH = "events"

# ════════════════════════════════════════════════════════════
#  环境键（全部可调参数走 .env；非法值回退默认）
# ════════════════════════════════════════════════════════════

ENV_ENABLED = "CP_BUDGET_BRAKE_ENABLED"
ENV_DAILY_CENTS = "CP_BUDGET_DAILY_CENTS"
ENV_WEEKLY_UTC_HIGH = "CP_BUDGET_WEEKLY_UTC_HIGH"
ENV_FASTING_IN_RATIO = "CP_BUDGET_FASTING_IN_RATIO"
ENV_FASTING_OUT_RATIO = "CP_BUDGET_FASTING_OUT_RATIO"
ENV_FASTING_OUT_HOURS = "CP_BUDGET_FASTING_OUT_HOURS"
ENV_FASTING_IN_CONSECUTIVE = "CP_BUDGET_FASTING_IN_CONSECUTIVE"
ENV_COOLDOWN_HOURS = "CP_BUDGET_COOLDOWN_HOURS"
ENV_BASELINE_DAYS = "CP_BUDGET_BASELINE_DAYS"
ENV_BASELINE_CENTS = "CP_BUDGET_BASELINE_CENTS"
ENV_SHADOW_FACTOR_FASTING = "CP_BUDGET_SHADOW_FACTOR_FASTING"
ENV_SHADOW_MS_CENTS_PER_S = "CP_BUDGET_SHADOW_MS_CENTS_PER_S"
ENV_FASTING_SIGNAL = "CP_BUDGET_FASTING_SIGNAL"
ENV_THETA_TABLE = "CP_BUDGET_THETA_TABLE"
ENV_THETA_BINDS_FASTING = "CP_BUDGET_THETA_BINDS_FASTING"
ENV_PHASE = "CP_BUDGET_PHASE"
ENV_PHASE_START = "CP_BUDGET_PHASE_START"
ENV_DRY_RUN = "CP_BUDGET_DRY_RUN"
ENV_STATE_PATH = "CP_BUDGET_STATE_PATH"
ENV_EVAL_MAX_AGE = "CP_BUDGET_EVAL_MAX_AGE_SECONDS"

# ════════════════════════════════════════════════════════════
#  默认值（§7 / §6.3 / §6.7 的规格值；可经 env / config 覆盖）
# ════════════════════════════════════════════════════════════

#: §7 断食进阈值（日均成本 / 基线）
DEFAULT_FASTING_IN_RATIO = 1.3
#: §7 断食出阈值（连续 24h ≤ 1.1×）
DEFAULT_FASTING_OUT_RATIO = 1.1
#: §7 断食出判定窗口（小时）
DEFAULT_FASTING_OUT_HOURS = 24.0
#: §7 冷却（小时）
DEFAULT_COOLDOWN_HOURS = 12.0
#: 「持续判定」的连续观测次数（防止单点尖峰误触发）
DEFAULT_FASTING_IN_CONSECUTIVE = 2
#: 基线窗口（近 N 日滚动日均；不含当日）
DEFAULT_BASELINE_DAYS = 7
#: 断食期 shadow 预算系数（0.0 = 归零，对齐 §6.7「错误预算耗尽→冻结非关键消化」）
DEFAULT_SHADOW_FACTOR_FASTING = 0.0
#: shadow_overhead_ms → cents 的换算率（cents / 秒）。
#: 默认 0.0 = **已计量但不计价**（诚实：未配置机时费率时不臆造金额）
DEFAULT_SHADOW_MS_CENTS_PER_S = 0.0
#: 评估结果最长复用时长（秒）；超龄时 `allow_outbound()` 会惰性重算
DEFAULT_EVAL_MAX_AGE_SECONDS = 60.0

#: 断食信号（§7 正文口径为「日均成本/基线」）
SIGNAL_DAILY_COST = "daily_cost"
SIGNAL_UTC_PER_TASK = "utc_per_task"
FASTING_SIGNALS: Tuple[str, ...] = (SIGNAL_DAILY_COST, SIGNAL_UTC_PER_TASK)

#: 断食状态机三态
STATE_NORMAL = "normal"
STATE_FASTING = "fasting"
STATE_COOLDOWN = "cooldown"
FASTING_STATES: Tuple[str, ...] = (STATE_NORMAL, STATE_FASTING, STATE_COOLDOWN)

#: outbound 关键性（**关键 = 用户显式请求 / 审批中任务**，P7.2-06）
CRITICAL_KINDS: Tuple[str, ...] = (
    "interactive",       # 用户显式发起的交互请求（**默认** → 恒放行）
    "user_request",      # 用户显式请求
    "approval_pending",  # 审批中任务（用户已介入，不得被熔断）
    "approval",
    "task_execution",    # 已受理任务的执行段
    "safety",            # 安全/自愈路径
)
#: 非关键后台 outbound（断食期可被限制；熔断期一律停）
BACKGROUND_KINDS: Tuple[str, ...] = (
    "shadow",        # S3-03 灰度影子任务
    "digestion",     # 消化流水线
    "internalize",   # 内化评估
    "reprobe",       # 重探
    "speculative",   # 投机/预取
    "background",
)

# ── 阶段与 θ（§6.3 分阶段启用）──

PHASE_W1_W4 = "w1_w4"
PHASE_W5_W9 = "w5_w9"
PHASE_W10_W14 = "w10_w14"
PHASE_M4_M6 = "m4_m6"
PHASE_M7_PLUS = "m7_plus"
PHASES: Tuple[str, ...] = (PHASE_W1_W4, PHASE_W5_W9, PHASE_W10_W14,
                           PHASE_M4_M6, PHASE_M7_PLUS)

#: §6.3 阈值表（**规格默认值**，非硬编码常量：可经 `CP_BUDGET_THETA_TABLE`
#: 或 `config.yaml:budget.theta` 整体覆盖；`None` = 该阶段不设 θ）
#:
#: ``{"start": a, "end": b}`` 表示阶段内由 ``a×`` 收紧到 ``b×``（``a == b`` 即恒定）。
DEFAULT_THETA_TABLE: Dict[str, Optional[Dict[str, float]]] = {
    PHASE_W1_W4: None,                                  # W1-W4 不设
    PHASE_W5_W9: {"start": 1.5, "end": 1.3},            # ≤1.5×→1.3×
    PHASE_W10_W14: {"start": 1.0, "end": 1.0},          # ≤1.0×
    PHASE_M4_M6: {"start": 0.8, "end": 0.6},            # ≤0.8×→0.6×
    PHASE_M7_PLUS: {"start": 0.5, "end": 0.5},          # ≤0.5×
}

#: 审批衰减率目标（§6.7：策略自动消化占比 ≥70%；**披露不考核**）
APPROVAL_DECAY_TARGET = 0.70
#: 视为「自动消化」的审批 kind（策略自动放行；§6.1 `auto_pass` 权重 0）
AUTO_APPROVAL_KINDS: Tuple[str, ...] = ("auto_pass", "auto", "policy_auto")
#: 披露而不下结论所需的最小样本量（§三口径纪律：样本不足须如实标注）
MIN_DISCLOSURE_SAMPLE = 20

#: 审计动作名（§13.3 事件名 ↔ 审计写入）
AUDIT_BREAKER_OPENED = "cost.daily_breaker.opened"
AUDIT_BREAKER_RECOVERED = "cost.daily_breaker.recovered"
AUDIT_FASTING_ENTERED = "cost.fasting.entered"
AUDIT_FASTING_EXITED = "cost.fasting.exited"
AUDIT_LEGACY_WRITE = "cost.legacy_track.write_attempt"

#: 状态持久化默认路径（运行时区；测试须显式注入 `state_path`）
DEFAULT_STATE_PATH = os.path.join(_PROJECT_ROOT, "data", "cost_brake_state.json")

#: 次态事件用的指标名（复用既有 `metrics.delta` 事件类型，不新增事件类型）
METRIC_DAILY_COST = "cost.daily_cents"
METRIC_FASTING_STATE = "cost.fasting.state"
METRIC_SHADOW_SUPPRESSED = "cost.shadow.suppressed"


class CostBrakeError(Exception):
    """成本刹车层基类异常"""


class OutboundBlockedError(CostBrakeError):
    """非关键 outbound 被成本刹车拦截（携带判定依据，便于调用方如实上报）"""

    def __init__(self, message: str, *, reason: str = "", state: str = "",
                 day: str = "", kind: str = "") -> None:
        super().__init__(message)
        self.reason = reason
        self.state = state
        self.day = day
        self.kind = kind


# ════════════════════════════════════════════════════════════
#  环境解析（非法值一律回退默认并告警 —— 与 shadow.py 同款纪律）
# ════════════════════════════════════════════════════════════


def _env(env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    return dict(os.environ if env is None else env)


def _env_flag(name: str, default: bool, env: Optional[Dict[str, str]] = None) -> bool:
    raw = str(_env(env).get(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    logger.warning("%s=%r 非法布尔值，回退默认 %s", name, raw, default)
    return bool(default)


def _env_float(name: str, default: float, env: Optional[Dict[str, str]] = None) -> float:
    raw = str(_env(env).get(name, "") or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r 非法数值，回退默认 %s", name, raw, default)
        return float(default)


def _env_int(name: str, default: int, env: Optional[Dict[str, str]] = None) -> int:
    raw = str(_env(env).get(name, "") or "").strip()
    if not raw:
        return int(default)
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        logger.warning("%s=%r 非法整数，回退默认 %s", name, raw, default)
        return int(default)


def _env_str(name: str, default: str, env: Optional[Dict[str, str]] = None) -> str:
    raw = str(_env(env).get(name, "") or "").strip()
    return raw or str(default)


def _flag_value(value: Any, name: str, default: bool = False) -> bool:
    """把 env / config.yaml 取值归一为布尔

    **非法值回退 ``default``（通常是 False = 不启用）**，绝不因 ``bool(non_empty_str)``
    而把 ``"maybe"`` 当成 True —— 那正是「默认不误伤」的反面。
    """
    if isinstance(value, bool):
        return value
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return bool(default)
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    logger.warning("%s=%r 非法布尔值，回退默认 %s", name, raw, default)
    return bool(default)


def _env_json(name: str, env: Optional[Dict[str, str]] = None) -> Any:
    """读取 JSON 型配置；非法 JSON → None（**不抛**，调用方回退默认）"""
    raw = str(_env(env).get(name, "") or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("%s 非法 JSON，回退默认表", name)
        return None


def _positive(value: float, default: float, *, name: str, allow_zero: bool = False
              ) -> float:
    """区间保护：``>0``（或 ``>=0``）否则回退默认并告警"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        logger.warning("%s=%r 非法数值，回退默认 %s", name, value, default)
        return float(default)
    if number < 0 or (number == 0 and not allow_zero):
        logger.warning("%s=%s 越界，回退默认 %s", name, number, default)
        return float(default)
    return number


def _config_yaml_budget() -> Dict[str, Any]:
    """``config.yaml:budget`` 段（可选；不可读/缺段 → ``{}``）

    只读叶子字段，不搬运 live 对象。用于让阈值走配置文件而非仅环境变量。
    """
    try:
        import yaml
        path = os.path.join(_PROJECT_ROOT, "config.yaml")
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        section = data.get("budget") if isinstance(data, dict) else None
        return dict(section) if isinstance(section, dict) else {}
    except Exception as e:  # noqa: BLE001 配置不可读 → 无覆盖
        logger.debug("config.yaml:budget 读取失败（按无覆盖处理）: %s", e)
        return {}


def _pick(env_value: Any, yaml_value: Any, default: Any) -> Tuple[Any, str]:
    """优先级：env > config.yaml > 默认；返回 ``(值, 来源)``"""
    if env_value not in (None, ""):
        return env_value, "env"
    if yaml_value not in (None, ""):
        return yaml_value, "config.yaml"
    return default, "default"


# ════════════════════════════════════════════════════════════
#  配置
# ════════════════════════════════════════════════════════════


@dataclass
class BrakeConfig:
    """成本刹车配置（**全部可调参数走 .env / config.yaml；非法值回退默认**）

    Attributes:
        enabled: 总开关（**默认 False = 零影响**）。
        daily_cents: 日级硬熔断阈值（cents）；``<= 0`` → 日熔断不启用。
        weekly_utc_high: 周级断食高阈（若显式给出则覆盖 ``fasting_in_ratio``）。
        fasting_in_ratio / fasting_out_ratio / fasting_out_hours / cooldown_hours:
            §7 断食滞后规则参数。
        fasting_in_consecutive: 「持续判定」连续观测次数（防单点尖峰）。
        baseline_days: 滚动基线窗口（近 N 日，不含当日）。
        baseline_cents: 基线覆盖值（``>0`` 时直接使用，不再滚动）。
        shadow_factor_fasting: 断食期 shadow 预算系数（0.0 归零 / 0.5 减半）。
        shadow_ms_cents_per_s: shadow_overhead_ms → cents 换算率（0 = 不臆造价）。
        fasting_signal: 断食信号（``daily_cost`` / ``utc_per_task``）。
        theta_table: θ 分阶段表（阶段 → ``{"start","end"}`` 或 ``None``）。
        theta_binds_fasting: θ 是否参与收紧断食进阈值（取更严者）。
        phase / phase_start: 当前阶段（显式）或阶段起点（据此推断）。
        dry_run: 干跑（只判定与留痕，不实际拦截、不影响 shadow 预算）。
        state_path: 状态持久化路径（``""`` → 不持久化）。
        eval_max_age_seconds: 判定结果最长复用时长。
        sources: 每个键的来源（env / config.yaml / default），供可观测性。
        warnings: 解析期的告警（非法值回退记录，**不静默**）。
    """

    enabled: bool = False
    daily_cents: float = 0.0
    weekly_utc_high: float = DEFAULT_FASTING_IN_RATIO
    fasting_in_ratio: float = DEFAULT_FASTING_IN_RATIO
    fasting_out_ratio: float = DEFAULT_FASTING_OUT_RATIO
    fasting_out_hours: float = DEFAULT_FASTING_OUT_HOURS
    fasting_in_consecutive: int = DEFAULT_FASTING_IN_CONSECUTIVE
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS
    baseline_days: int = DEFAULT_BASELINE_DAYS
    baseline_cents: float = 0.0
    shadow_factor_fasting: float = DEFAULT_SHADOW_FACTOR_FASTING
    shadow_ms_cents_per_s: float = DEFAULT_SHADOW_MS_CENTS_PER_S
    fasting_signal: str = SIGNAL_DAILY_COST
    theta_table: Dict[str, Optional[Dict[str, float]]] = field(
        default_factory=lambda: {k: (dict(v) if v else None)
                                 for k, v in DEFAULT_THETA_TABLE.items()})
    theta_binds_fasting: bool = True
    phase: str = ""
    phase_start: str = ""
    dry_run: bool = False
    state_path: str = DEFAULT_STATE_PATH
    eval_max_age_seconds: float = DEFAULT_EVAL_MAX_AGE_SECONDS
    sources: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    #: 当前 θ 表的来源（env / config.yaml / default）
    theta_source: str = "default"

    @property
    def day_breaker_configured(self) -> bool:
        """日级熔断是否已配置阈值（未配置 → 该层不启用）"""
        return bool(self.enabled) and self.daily_cents > 0

    @property
    def bypassed(self) -> bool:
        """总开关关闭 → 本模块对全局行为**零影响**"""
        return not self.enabled


def _parse_theta_table(raw: Any, warnings: List[str]
                       ) -> Optional[Dict[str, Optional[Dict[str, float]]]]:
    """解析 θ 表覆盖；形态非法 → None（回退默认表）"""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        warnings.append("theta 表覆盖形态非法（应为 object），回退 §6.3 默认表")
        return None
    out: Dict[str, Optional[Dict[str, float]]] = {}
    for key, value in raw.items():
        name = str(key)
        if name not in PHASES:
            warnings.append(f"theta 表含未登记阶段 {name!r}（已忽略）")
            continue
        if value is None:
            out[name] = None
            continue
        if isinstance(value, dict):
            raw_start = value.get("start")
            raw_end = value.get("end", raw_start)
            if raw_start is None or raw_end is None:
                warnings.append(f"theta[{name}] 缺 start 数值（已忽略该项）")
                continue
            try:
                start = float(raw_start)
                end = float(raw_end)
            except (TypeError, ValueError):
                warnings.append(f"theta[{name}] 数值非法（已忽略该项）")
                continue
        else:
            try:
                start = end = float(value)
            except (TypeError, ValueError):
                warnings.append(f"theta[{name}] 数值非法（已忽略该项）")
                continue
        if start <= 0 or end <= 0:
            warnings.append(f"theta[{name}] 须 >0（已忽略该项）")
            continue
        out[name] = {"start": start, "end": end}
    if not out:
        warnings.append("theta 表覆盖为空（回退 §6.3 默认表）")
        return None
    # 未覆盖的阶段沿用默认表，保证「无硬编码」且不会因漏配某阶段而失去限值
    merged: Dict[str, Optional[Dict[str, float]]] = {
        k: (dict(v) if v else None) for k, v in DEFAULT_THETA_TABLE.items()}
    merged.update(out)
    return merged


def load_config(env: Optional[Dict[str, str]] = None) -> BrakeConfig:
    """装配配置（env > config.yaml > §规格默认；所有非法值回退默认）

    **注意**：``enabled`` 默认 ``False`` —— 未显式开启时本模块零影响。
    """
    raw_env = _env(env)
    yaml_budget = _config_yaml_budget()
    warnings: List[str] = []
    sources: Dict[str, str] = {}

    def pick(env_key: str, yaml_key: str, default: Any) -> Tuple[Any, str]:
        env_value = raw_env.get(env_key)
        yaml_value = yaml_budget.get(yaml_key)
        value, source = _pick(env_value, yaml_value, default)
        sources[env_key] = source
        return value, source

    enabled_raw, _ = pick(ENV_ENABLED, "enabled", False)
    # 非法取值一律回退 False（**默认不误伤**）：绝不让 "maybe" 因 bool(非空串) 变成开
    enabled = _flag_value(enabled_raw, ENV_ENABLED, default=False)

    daily_raw, _ = pick(ENV_DAILY_CENTS, "daily_cents", 0.0)
    daily_cents = _positive(daily_raw, 0.0, name=ENV_DAILY_CENTS, allow_zero=True)

    weekly_raw, _ = pick(ENV_WEEKLY_UTC_HIGH, "weekly_utc_high", DEFAULT_FASTING_IN_RATIO)
    weekly_high = _positive(weekly_raw, DEFAULT_FASTING_IN_RATIO,
                            name=ENV_WEEKLY_UTC_HIGH)

    in_raw, _ = pick(ENV_FASTING_IN_RATIO, "fasting_in_ratio", weekly_high)
    fasting_in = _positive(in_raw, DEFAULT_FASTING_IN_RATIO, name=ENV_FASTING_IN_RATIO)

    out_raw, _ = pick(ENV_FASTING_OUT_RATIO, "fasting_out_ratio",
                      DEFAULT_FASTING_OUT_RATIO)
    fasting_out = _positive(out_raw, DEFAULT_FASTING_OUT_RATIO,
                            name=ENV_FASTING_OUT_RATIO)

    out_hours_raw, _ = pick(ENV_FASTING_OUT_HOURS, "fasting_out_hours",
                            DEFAULT_FASTING_OUT_HOURS)
    out_hours = _positive(out_hours_raw, DEFAULT_FASTING_OUT_HOURS,
                          name=ENV_FASTING_OUT_HOURS)

    consec_raw, _ = pick(ENV_FASTING_IN_CONSECUTIVE, "fasting_in_consecutive",
                         DEFAULT_FASTING_IN_CONSECUTIVE)
    try:
        consecutive = int(float(consec_raw))
    except (TypeError, ValueError):
        warnings.append(f"{ENV_FASTING_IN_CONSECUTIVE}={consec_raw!r} 非法，回退默认")
        consecutive = DEFAULT_FASTING_IN_CONSECUTIVE
    if consecutive < 1:
        warnings.append(f"{ENV_FASTING_IN_CONSECUTIVE}={consecutive} 须 ≥1，回退默认")
        consecutive = DEFAULT_FASTING_IN_CONSECUTIVE

    cooldown_raw, _ = pick(ENV_COOLDOWN_HOURS, "cooldown_hours", DEFAULT_COOLDOWN_HOURS)
    cooldown = _positive(cooldown_raw, DEFAULT_COOLDOWN_HOURS, name=ENV_COOLDOWN_HOURS)

    days_raw, _ = pick(ENV_BASELINE_DAYS, "baseline_days", DEFAULT_BASELINE_DAYS)
    try:
        baseline_days = int(float(days_raw))
    except (TypeError, ValueError):
        warnings.append(f"{ENV_BASELINE_DAYS}={days_raw!r} 非法，回退默认")
        baseline_days = DEFAULT_BASELINE_DAYS
    if baseline_days < 1:
        warnings.append(f"{ENV_BASELINE_DAYS}={baseline_days} 须 ≥1，回退默认")
        baseline_days = DEFAULT_BASELINE_DAYS

    base_cents_raw, _ = pick(ENV_BASELINE_CENTS, "baseline_cents", 0.0)
    baseline_cents = _positive(base_cents_raw, 0.0, name=ENV_BASELINE_CENTS,
                               allow_zero=True)

    shadow_raw, _ = pick(ENV_SHADOW_FACTOR_FASTING, "shadow_factor_fasting",
                         DEFAULT_SHADOW_FACTOR_FASTING)
    try:
        shadow_factor = float(shadow_raw)
    except (TypeError, ValueError):
        warnings.append(f"{ENV_SHADOW_FACTOR_FASTING}={shadow_raw!r} 非法，回退默认")
        shadow_factor = DEFAULT_SHADOW_FACTOR_FASTING
    if not (0.0 <= shadow_factor <= 1.0):
        warnings.append(f"{ENV_SHADOW_FACTOR_FASTING}={shadow_factor} 越界（须 0..1），"
                        f"回退默认")
        shadow_factor = DEFAULT_SHADOW_FACTOR_FASTING

    ms_rate_raw, _ = pick(ENV_SHADOW_MS_CENTS_PER_S, "shadow_ms_cents_per_s",
                          DEFAULT_SHADOW_MS_CENTS_PER_S)
    ms_rate = _positive(ms_rate_raw, DEFAULT_SHADOW_MS_CENTS_PER_S,
                        name=ENV_SHADOW_MS_CENTS_PER_S, allow_zero=True)

    signal_raw, _ = pick(ENV_FASTING_SIGNAL, "fasting_signal", SIGNAL_DAILY_COST)
    signal = str(signal_raw).strip()
    if signal not in FASTING_SIGNALS:
        warnings.append(f"{ENV_FASTING_SIGNAL}={signal!r} 未登记，回退 {SIGNAL_DAILY_COST}")
        signal = SIGNAL_DAILY_COST

    theta_env = _env_json(ENV_THETA_TABLE, env)
    theta_yaml = yaml_budget.get("theta")
    theta_source = "default"
    theta_table: Optional[Dict[str, Optional[Dict[str, float]]]] = None
    if theta_env is not None:
        theta_table = _parse_theta_table(theta_env, warnings)
        theta_source = "env" if theta_table else "default"
    if theta_table is None and theta_yaml is not None:
        theta_table = _parse_theta_table(theta_yaml, warnings)
        theta_source = "config.yaml" if theta_table else "default"
    if theta_table is None:
        theta_table = {k: (dict(v) if v else None)
                       for k, v in DEFAULT_THETA_TABLE.items()}

    theta_binds_raw, _ = pick(ENV_THETA_BINDS_FASTING, "theta_binds_fasting", True)
    theta_binds = _flag_value(theta_binds_raw, ENV_THETA_BINDS_FASTING, default=True)

    phase_raw, _ = pick(ENV_PHASE, "phase", "")
    phase = str(phase_raw or "").strip()
    if phase and phase not in PHASES:
        warnings.append(f"{ENV_PHASE}={phase!r} 未登记，回退自动推断")
        phase = ""
    phase_start_raw, _ = pick(ENV_PHASE_START, "phase_start", "")
    phase_start = str(phase_start_raw or "").strip()
    if phase_start:
        try:
            datetime.strptime(phase_start[:10], "%Y-%m-%d")
        except (TypeError, ValueError):
            warnings.append(f"{ENV_PHASE_START}={phase_start!r} 非法日期，忽略")
            phase_start = ""

    dry_raw, _ = pick(ENV_DRY_RUN, "dry_run", False)
    dry_run = _flag_value(dry_raw, ENV_DRY_RUN, default=False)

    state_raw, _ = pick(ENV_STATE_PATH, "state_path", DEFAULT_STATE_PATH)
    state_path = str(state_raw or "")

    age_raw, _ = pick(ENV_EVAL_MAX_AGE, "eval_max_age_seconds",
                      DEFAULT_EVAL_MAX_AGE_SECONDS)
    eval_age = _positive(age_raw, DEFAULT_EVAL_MAX_AGE_SECONDS,
                         name=ENV_EVAL_MAX_AGE, allow_zero=True)

    return BrakeConfig(
        enabled=bool(enabled), daily_cents=float(daily_cents),
        weekly_utc_high=float(weekly_high), fasting_in_ratio=float(fasting_in),
        fasting_out_ratio=float(fasting_out), fasting_out_hours=float(out_hours),
        fasting_in_consecutive=int(consecutive), cooldown_hours=float(cooldown),
        baseline_days=int(baseline_days), baseline_cents=float(baseline_cents),
        shadow_factor_fasting=float(shadow_factor),
        shadow_ms_cents_per_s=float(ms_rate), fasting_signal=signal,
        theta_table=theta_table, theta_binds_fasting=bool(theta_binds),
        phase=phase, phase_start=phase_start, dry_run=bool(dry_run),
        state_path=state_path, eval_max_age_seconds=float(eval_age),
        sources=sources, warnings=warnings, theta_source=theta_source)


# ════════════════════════════════════════════════════════════
#  阶段推断与 θ（§6.3；配置驱动，无硬编码）
# ════════════════════════════════════════════════════════════


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def _parse_day(value: Any) -> Optional[date]:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def resolve_phase(now: datetime, *, phase: str = "", phase_start: str = "") -> Dict[str, Any]:
    """阶段解析（§6.3）→ ``{"phase", "progress", "source", "week", "month"}``

    优先级：显式 ``phase`` > 由 ``phase_start`` 按周/月推进 > 默认 ``w1_w4``（不设 θ）。
    显式 ``phase`` 表示「按该阶段**起点**取值」（``progress = 0``），不随日期收紧；
    需要阶段内收紧（如 W5-W9 的 1.5×→1.3×）请配 ``phase_start``。

    分段规则（**无空档**）：月 ≥7 → ``m7_plus``；月 4–6 → ``m4_m6``；
    周 ≥10 → ``w10_w14``（含 W15 起至 M4 之前的保持段）；周 5–9 → ``w5_w9``；
    其余 → ``w1_w4``。
    """
    if phase:
        spec = {"phase": phase, "progress": 0.0, "source": "explicit",
                "week": 0, "month": 0}
        if phase == PHASE_W5_W9:
            spec["progress"] = 0.0
        elif phase == PHASE_M4_M6:
            spec["progress"] = 0.0
        return spec
    start = _parse_day(phase_start)
    if start is None:
        return {"phase": PHASE_W1_W4, "progress": 0.0, "source": "default",
                "week": 0, "month": 0}
    days = (now.date() - start).days
    if days < 0:
        return {"phase": PHASE_W1_W4, "progress": 0.0, "source": "before_start",
                "week": 0, "month": 0}
    week = days // 7 + 1
    months = (now.year - start.year) * 12 + (now.month - start.month) + 1
    if now.day < start.day:
        months -= 1
    months = max(1, months)
    if months >= 7:
        return {"phase": PHASE_M7_PLUS, "progress": 1.0, "source": "phase_start",
                "week": week, "month": months}
    if months >= 4:
        return {"phase": PHASE_M4_M6, "progress": _clamp01((months - 4) / 2.0),
                "source": "phase_start", "week": week, "month": months}
    if week >= 10:
        return {"phase": PHASE_W10_W14,
                "progress": _clamp01((week - 10) / 4.0), "source": "phase_start",
                "week": week, "month": months}
    if week >= 5:
        return {"phase": PHASE_W5_W9, "progress": _clamp01((week - 5) / 4.0),
                "source": "phase_start", "week": week, "month": months}
    return {"phase": PHASE_W1_W4, "progress": _clamp01((week - 1) / 3.0),
            "source": "phase_start", "week": week, "month": months}


def theta_limit(now: datetime, *, config: Optional[BrakeConfig] = None
                ) -> Dict[str, Any]:
    """当前阶段的 θ 上限（UTC 倍数）→ ``{"limit", "phase", "progress", "source", ...}``

    ``limit is None`` ⇒ 该阶段**不设** θ（W1-W4）。阶段内由 ``start`` 线性收紧到
    ``end``（如 W5-W9 的 1.5×→1.3×）。
    """
    cfg = config if config is not None else load_config()
    info = resolve_phase(now, phase=cfg.phase, phase_start=cfg.phase_start)
    spec = cfg.theta_table.get(str(info["phase"]))
    if not spec:
        return {**info, "limit": None, "start": None, "end": None,
                "table_source": cfg.theta_source,
                "note": "该阶段不设 θ（§6.3）"}
    start = float(spec.get("start", 0.0))
    end = float(spec.get("end", start))
    limit = start + (end - start) * float(info["progress"])
    return {**info, "limit": round(limit, 6), "start": start, "end": end,
            "table_source": cfg.theta_source,
            "note": f"{start}× → {end}× 阶段内线性收紧"}


# ════════════════════════════════════════════════════════════
#  断食状态机（§7：进 >1.3× / 出连续 24h ≤1.1× / 冷却 12h）
# ════════════════════════════════════════════════════════════


@dataclass
class FastingSnapshot:
    """断食状态机快照（可观测：状态 / 进入退出时间 / 当前阈值来源）"""

    state: str = STATE_NORMAL
    entered_at: str = ""
    exited_at: str = ""
    cooldown_until: str = ""
    below_since: str = ""
    consecutive_high: int = 0
    last_ratio: Optional[float] = None
    last_threshold: Optional[float] = None
    threshold_source: str = "config"
    transitions: int = 0
    last_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "entered_at": self.entered_at,
            "exited_at": self.exited_at,
            "cooldown_until": self.cooldown_until,
            "below_since": self.below_since,
            "consecutive_high": int(self.consecutive_high),
            "last_ratio": self.last_ratio,
            "last_threshold": self.last_threshold,
            "threshold_source": self.threshold_source,
            "transitions": int(self.transitions),
            "last_reason": self.last_reason,
        }


class FastingMachine:
    """周级断食状态机（**纯逻辑 + 注入时钟**，便于确定性测试）

    三态：``normal`` →（ratio > 进阈值 连续 N 次）→ ``fasting``
    →（连续 ``out_hours`` 小时 ratio ≤ 出阈值）→ ``cooldown``
    →（``cooldown_hours`` 后）→ ``normal``。

    冷却期内**限制已解除**，但**抑制重新进入**（防抖动）——这是 §7「滞后规则」的本意。
    """

    def __init__(self, *, in_ratio: float = DEFAULT_FASTING_IN_RATIO,
                 out_ratio: float = DEFAULT_FASTING_OUT_RATIO,
                 out_hours: float = DEFAULT_FASTING_OUT_HOURS,
                 cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
                 in_consecutive: int = DEFAULT_FASTING_IN_CONSECUTIVE) -> None:
        self.in_ratio = float(in_ratio)
        self.out_ratio = float(out_ratio)
        self.out_hours = float(out_hours)
        self.cooldown_hours = float(cooldown_hours)
        self.in_consecutive = max(1, int(in_consecutive))
        self.snapshot = FastingSnapshot()

    # ── 状态查询 ──

    @property
    def state(self) -> str:
        return self.snapshot.state

    @property
    def restricted(self) -> bool:
        """当前是否处于降本模式（**冷却期不降本**，只抑制重进）"""
        return self.snapshot.state == STATE_FASTING

    def to_dict(self) -> Dict[str, Any]:
        return {**self.snapshot.to_dict(), "restricted": self.restricted,
                "in_ratio": self.in_ratio, "out_ratio": self.out_ratio,
                "out_hours": self.out_hours, "cooldown_hours": self.cooldown_hours,
                "in_consecutive": self.in_consecutive}

    def restore(self, data: Optional[Dict[str, Any]]) -> None:
        """从持久化 dict 恢复（非法/缺失字段一律回退默认，不抛）"""
        if not isinstance(data, dict):
            return
        snap = self.snapshot
        state = str(data.get("state") or STATE_NORMAL)
        snap.state = state if state in FASTING_STATES else STATE_NORMAL
        snap.entered_at = str(data.get("entered_at") or "")
        snap.exited_at = str(data.get("exited_at") or "")
        snap.cooldown_until = str(data.get("cooldown_until") or "")
        snap.below_since = str(data.get("below_since") or "")
        snap.threshold_source = str(data.get("threshold_source") or "config")
        try:
            snap.consecutive_high = int(data.get("consecutive_high") or 0)
            snap.transitions = int(data.get("transitions") or 0)
        except (TypeError, ValueError):
            snap.consecutive_high = 0
        for name in ("last_ratio", "last_threshold"):
            raw = data.get(name)
            try:
                setattr(snap, name, None if raw is None else float(raw))
            except (TypeError, ValueError):
                setattr(snap, name, None)

    # ── 状态推进 ──

    def observe(self, ratio: Optional[float], now: datetime, *,
                threshold: Optional[float] = None,
                threshold_source: str = "config") -> Dict[str, Any]:
        """喂入一次比值观测 → 推进状态机；返回 ``{"from", "to", "transition", ...}``

        ``ratio is None``（无基线/无数据）时**不推进**（不臆断），仅记录来源。
        任何异常都不抛出（调用方按"零影响"处理）。
        """
        snap = self.snapshot
        snap.last_threshold = threshold
        snap.threshold_source = str(threshold_source or "config")
        before = snap.state
        result: Dict[str, Any] = {"from": before, "to": before, "transition": False,
                                  "reason": "", "restricted": self.restricted}
        if ratio is None:
            snap.last_reason = "无基线/无成本数据 → 不推进（不臆断）"
            result["reason"] = snap.last_reason
            return result
        try:
            value = float(ratio)
        except (TypeError, ValueError):
            snap.last_reason = f"比值非法 {ratio!r} → 不推进"
            result["reason"] = snap.last_reason
            return result
        snap.last_ratio = round(value, 6)
        in_threshold = float(threshold if threshold is not None else self.in_ratio)

        if before == STATE_NORMAL:
            if value > in_threshold:
                snap.consecutive_high += 1
                if snap.consecutive_high >= self.in_consecutive:
                    snap.state = STATE_FASTING
                    snap.entered_at = now.isoformat()
                    snap.below_since = ""
                    snap.consecutive_high = 0
                    snap.last_reason = (f"日均成本比 {value:.4f} > 进阈值 {in_threshold:.4f} "
                                        f"（连续 {self.in_consecutive} 次）")
                else:
                    snap.last_reason = (
                        f"比值 {value:.4f} > 进阈值 {in_threshold:.4f}"
                        f"（连续 {snap.consecutive_high}/{self.in_consecutive} 次，"
                        f"未达持续判定要求）")
            else:
                snap.consecutive_high = 0
                snap.last_reason = f"比值 {value:.4f} ≤ 进阈值 {in_threshold:.4f}"
        elif before == STATE_FASTING:
            if value <= self.out_ratio:
                if not snap.below_since:
                    snap.below_since = now.isoformat()
                since = _parse_ts(snap.below_since) or now
                held = (now - since).total_seconds() / 3600.0
                if held >= self.out_hours:
                    snap.state = STATE_COOLDOWN
                    snap.exited_at = now.isoformat()
                    snap.cooldown_until = (now + timedelta(
                        hours=self.cooldown_hours)).isoformat()
                    snap.below_since = ""
                    snap.last_reason = (f"连续 {held:.2f}h ≤ {self.out_ratio:.4f}"
                                        f" → 退出断食，冷却 {self.cooldown_hours}h")
                else:
                    snap.last_reason = (f"低于出阈值已 {held:.2f}h / "
                                        f"{self.out_hours}h（继续计时）")
            else:
                snap.below_since = ""
                snap.last_reason = f"比值 {value:.4f} > 出阈值 {self.out_ratio:.4f}"
        elif before == STATE_COOLDOWN:
            until = _parse_ts(snap.cooldown_until)
            if until is None or now >= until:
                snap.state = STATE_NORMAL
                snap.cooldown_until = ""
                snap.last_reason = f"冷却结束（{self.cooldown_hours}h）→ 恢复常态"
            else:
                snap.last_reason = (f"冷却中（至 {snap.cooldown_until}），"
                                    f"抑制重新进入")
                if value > in_threshold:
                    snap.consecutive_high = 0

        if snap.state != before:
            snap.transitions += 1
            result.update({"transition": True, "to": snap.state,
                           "restricted": self.restricted})
        result["reason"] = snap.last_reason
        result["restricted"] = self.restricted
        return result


def _parse_ts(value: Any) -> Optional[datetime]:
    """解析 ISO-8601 时间戳（带/不带偏移）；失败 → None"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.astimezone()


# ════════════════════════════════════════════════════════════
#  基线与成本读取（**复用 utc.py，不另建聚合**）
# ════════════════════════════════════════════════════════════


def rolling_baseline(now: datetime, *, days: int = DEFAULT_BASELINE_DAYS,
                     directory: Optional[str] = None) -> Dict[str, Any]:
    """近 ``days`` 日滚动基线（**不含当日**）——逐日复用 ``utc.utc_daily()``

    Returns:
        ``{"cost_cents_per_day", "utc_cents_per_task", "days", "rows", "source"}``
    """
    span = max(1, int(days))
    rows: List[Dict[str, Any]] = []
    for offset in range(span, 0, -1):
        day = (now.date() - timedelta(days=offset)).isoformat()
        rows.append(_utc.utc_daily(day, directory=directory))
    costs = [float(r.get("cost_normalized_cents") or 0.0) for r in rows]
    utcs = [float(r["utc_cents_per_task"]) for r in rows
            if r.get("utc_cents_per_task") is not None]
    with_tasks = [r for r in rows if r.get("utc_cents_per_task") is not None]
    return {
        "days": len(rows),
        "days_with_tasks": len(with_tasks),
        "cost_cents_per_day": (sum(costs) / len(costs)) if costs else None,
        "utc_cents_per_task": (sum(utcs) / len(utcs)) if utcs else None,
        "rows": rows,
        "source": "utc.utc_daily(rolling)",
    }


def shadow_overhead_audit(*, day: Optional[str] = None,
                          directory: Optional[str] = None,
                          now: Optional[datetime] = None,
                          config: Optional[BrakeConfig] = None,
                          daily_row: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """shadow 自身开销是否进入 UTC 口径（S3-03 交付物的口径衔接审计）

    拆分三段，**不把未计价的部分说成已计价**：

    1. ``shadow_overhead_cents`` —— S3-03 已直接落到 cost 事件的**金额**，
       `utc.normalize_cost()` 已把它加进 ``cost_normalized_cents``（**已在 UTC 口径内**）；
    2. ``shadow_overhead_ms`` —— S3-03 `ShadowReport.overhead.shadow_overhead_ms`
       记录的**机时**；只有配置了 ``CP_BUDGET_SHADOW_MS_CENTS_PER_S`` 才折价计入；
    3. ``included`` —— 实际参与断食/熔断判定的金额合计。

    ``priced=False`` 时如实标注「已计量、未计价」，并说明缺口（不臆造费率）。

    Args:
        daily_row: 已算好的 `utc.utc_daily()` 行（避免重复读盘）。
    """
    cfg = config if config is not None else load_config()
    target = str(day or (now or _now()).date().isoformat())[:10]
    row = daily_row if daily_row is not None else _utc.utc_daily(
        target, directory=directory)
    direct = float(row.get("shadow_overhead_cents") or 0.0)
    ms = float(row.get("shadow_overhead_ms") or 0.0)
    rate = float(cfg.shadow_ms_cents_per_s or 0.0)
    from_ms = ms / 1000.0 * rate
    return {
        "date": target,
        "shadow_overhead_ms": round(ms, 3),
        "shadow_overhead_cents_direct": round(direct, 6),
        "shadow_ms_cents_per_s": rate,
        "shadow_overhead_cents_from_ms": round(from_ms, 6),
        "included_cents": round(direct + from_ms, 6),
        "priced": bool(rate > 0.0),
        "in_utc_scope": True,          # ms 已被聚合进 by-day 行（只是未折价）
        "note": ("shadow_overhead_ms 已纳入 UTC 聚合（utc.utc_daily 的 "
                 "shadow_overhead_ms 字段）并参与断食/熔断判定；金额部分："
                 "direct 已在 cost_normalized_cents 内，ms 部分"
                 + ("已按 CP_BUDGET_SHADOW_MS_CENTS_PER_S 折价计入"
                    if rate > 0 else
                    "**未折价**（未配置机时费率，不臆造金额）")),
        "cost_schema_version": COST_SCHEMA_VERSION,
        "calibration_version": CALIBRATION_VERSION,
    }


# ════════════════════════════════════════════════════════════
#  归一化日成本视图（复用 utc.utc_daily，不另建聚合）
# ════════════════════════════════════════════════════════════

#: 归一日成本视图默认落盘位置（运行时产物；测试须显式传路径）
DEFAULT_COST_DAILY_PATH = os.path.join(_PROJECT_ROOT, "data", "cost_daily.json")


def cost_daily_view(*, day: Optional[str] = None, now: Optional[datetime] = None,
                    directory: Optional[str] = None,
                    config: Optional[BrakeConfig] = None) -> Dict[str, Any]:
    """归一化日成本视图（`data/cost_daily.json` 的内容）

    **不另建聚合**：成本字段逐字来自 `utc.utc_daily()`（唯一数据源＝事件流），
    此处只补上刹车判定所需的派生量与口径标注：
    ``over_budget``（日熔断判据）、``baseline`` / ``ratio``（断食判据）、
    ``theta``（§6.3 上限）、``shadow``（S3 开销口径审计）。
    """
    cfg = config if config is not None else load_config()
    moment = now or _now()
    target = str(day or moment.date().isoformat())[:10]
    row = _utc.utc_daily(target, directory=directory)
    shadow = shadow_overhead_audit(day=target, directory=directory, config=cfg,
                                   daily_row=row)
    effective = float(row.get("cost_normalized_cents") or 0.0) \
        + float(shadow.get("shadow_overhead_cents_from_ms") or 0.0)
    if cfg.baseline_cents > 0:
        baseline: Dict[str, Any] = {
            "cost_cents_per_day": float(cfg.baseline_cents),
            "utc_cents_per_task": None, "source": "config", "days": 0,
            "days_with_tasks": 0, "rows": []}
    else:
        baseline = rolling_baseline(moment, days=cfg.baseline_days,
                                    directory=directory)
    base_cost = baseline.get("cost_cents_per_day")
    return {
        "date": target,
        # ── 成本（utc.utc_daily 原样透传）──
        "cost_raw_cents": row.get("cost_raw_cents"),
        "cost_normalized_cents": row.get("cost_normalized_cents"),
        "cost_effective_cents": round(effective, 6),
        "llm_calls": row.get("llm_calls"),
        "cache_hits": row.get("cache_hits"),
        "cache_hit_rate": row.get("cache_hit_rate"),
        "tokens_in": row.get("tokens_in"),
        "tokens_out": row.get("tokens_out"),
        "billable_tokens_in": row.get("billable_tokens_in"),
        "billable_tokens_out": row.get("billable_tokens_out"),
        "retries": row.get("retries"),
        "errors": row.get("errors"),
        "by_model": row.get("by_model"),
        "tasks": row.get("tasks"),
        # ── UTC（单位任务成本）──
        "utc_cents_per_task": row.get("utc_cents_per_task"),
        "utc_cents_per_task_acr_cohort": row.get("utc_cents_per_task_acr_cohort"),
        "utc_formula": row.get("utc_formula"),
        # ── 刹车判据 ──
        "daily_budget_cents": float(cfg.daily_cents),
        "over_budget": bool(cfg.daily_cents > 0 and effective > cfg.daily_cents),
        "baseline_cents_per_day": (None if base_cost is None
                                   else round(float(base_cost), 6)),
        "baseline_source": str(baseline.get("source") or ""),
        "ratio": (round(effective / float(base_cost), 6) if base_cost else None),
        "fasting_in_ratio": float(cfg.fasting_in_ratio),
        "fasting_out_ratio": float(cfg.fasting_out_ratio),
        "theta": theta_limit(moment, config=cfg),
        # ── 口径标注（裁定 C / D：指标输出必须带版本号）──
        "shadow": shadow,
        "cost_schema_version": COST_SCHEMA_VERSION,
        "calibration_version": CALIBRATION_VERSION,
        "calibration_note": CALIBRATION_NOTE,
        "source_of_truth": COST_SOURCE_OF_TRUTH,
        "source": "utc.utc_daily（成本唯一数据源＝事件流；本视图不另建聚合）",
    }


def write_cost_daily(path: Optional[str] = None, *, day: Optional[str] = None,
                     now: Optional[datetime] = None,
                     directory: Optional[str] = None,
                     config: Optional[BrakeConfig] = None) -> Dict[str, Any]:
    """输出 `data/cost_daily.json`（**归一化日成本数据源**；best-effort）

    既有成本监控脚本（`scripts/verify_budget_degrade.py` 等）与运维查询共用此文件；
    内容全部由 `cost_daily_view()` 产出，带口径版本号。
    """
    view = cost_daily_view(day=day, now=now, directory=directory, config=config)
    target = path or DEFAULT_COST_DAILY_PATH
    try:
        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps(view, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    except OSError as e:  # noqa: BLE001 best-effort
        logger.warning("写出 cost_daily.json 失败: %s", e)
    return view


# ════════════════════════════════════════════════════════════
#  审批衰减率（§6.7「策略自动消化占比」；披露不考核）
# ════════════════════════════════════════════════════════════


def approval_decay_rate(*, day: Optional[str] = None, days: int = 30,
                        directory: Optional[str] = None) -> Dict[str, Any]:
    """审批衰减率 ＝ **策略自动消化占比**（§6.7；目标 ≥70%，**披露不考核**）

    口径（消费 S2-03 的 ``approval`` / ``intervention`` 事件，不新建数据源）：

    - 分母 = 窗口内观测到的审批决策数（``approval`` 事件去重后条数）；
    - 分子 = 其中**无需人工**即完成的：``kind`` ∈ `AUTO_APPROVAL_KINDS`
      （``auto_pass`` 等），或 ``actor`` ∈ {``auto``, ``system``}；
    - 样本 < `MIN_DISCLOSURE_SAMPLE` 时标 ``insufficient_sample``（**不据此下结论**）。

    附带披露：疲劳分桶分布（§6.1）与审批迟滞中位数，供人读「是自动消化还是人工疲劳」。
    """
    window_days = max(1, int(days))
    window: Dict[str, Any]
    if day:
        rows = iter_events(day=str(day)[:10], directory=directory)
        window = {"day": str(day)[:10]}
    else:
        start = (_now().date() - timedelta(days=window_days - 1)).isoformat()
        rows = iter_events(since=start, directory=directory)
        window = {"start": start, "end": _now().date().isoformat(),
                  "days": window_days}

    approvals = [e for e in rows if e.type == EV_APPROVAL]
    auto_interventions = [e for e in rows
                          if e.type == EV_INTERVENTION
                          and str((e.payload or {}).get("kind") or "") in AUTO_APPROVAL_KINDS]
    auto = 0
    human = 0
    fatigue: Dict[str, int] = {}
    latencies: List[float] = []
    for env in approvals:
        payload = env.payload or {}
        kind = str(payload.get("kind") or "")
        actor = str(env.actor or "")
        if kind in AUTO_APPROVAL_KINDS or actor in (ACTOR_AUTO, ACTOR_SYSTEM):
            auto += 1
        else:
            human += 1
        bucket = str(payload.get("fatigue_bucket") or "unknown")
        fatigue[bucket] = fatigue.get(bucket, 0) + 1
        raw = payload.get("latency_ms")
        try:
            if raw is not None:
                latencies.append(float(raw))
        except (TypeError, ValueError):
            pass
    total = auto + human
    rate: Optional[float] = round(auto / total, 6) if total else None
    latencies.sort()
    median = None
    if latencies:
        mid = len(latencies) // 2
        median = (latencies[mid] if len(latencies) % 2
                  else round((latencies[mid - 1] + latencies[mid]) / 2.0, 3))
    return {
        "window": window,
        "metric": "approval_decay_rate",
        "definition": "策略自动消化占比 = 无需人工即完成的审批决策数 / 全部审批决策数",
        "auto_disposed": auto,
        "human_disposed": human,
        "total_disposed": total,
        "auto_pass_interventions": len(auto_interventions),
        "decay_rate": rate,
        "target": APPROVAL_DECAY_TARGET,
        "meets_target": None if rate is None else bool(rate >= APPROVAL_DECAY_TARGET),
        "insufficient_sample": bool(total < MIN_DISCLOSURE_SAMPLE),
        "min_sample": MIN_DISCLOSURE_SAMPLE,
        "fatigue_buckets": dict(sorted(fatigue.items())),
        "latency_median_ms": median,
        "disclosure_only": True,
        "note": ("披露不考核（§6.7 起步口径）：样本不足时只报数不下结论；"
                 "达标与否不阻断任何流程"),
        "cost_schema_version": COST_SCHEMA_VERSION,
    }


# ════════════════════════════════════════════════════════════
#  审计与事件（best-effort，绝不阻断主路径）
# ════════════════════════════════════════════════════════════


def _audit(action: str, *, subject: str, payload: Dict[str, Any],
           status: str = "success", actor: str = ACTOR_SYSTEM) -> Tuple[int, str]:
    """链式审计留痕（复用 S2-02 facade；失败只 debug，不影响判定）"""
    try:
        from agent.audit.facade import audit as _audit_facade
        entry = _audit_facade.record(
            action, actor=actor, subject=subject, payload=payload,
            source="agent", status=status,
            technical={"cost_schema_version": COST_SCHEMA_VERSION,
                       "calibration_version": CALIBRATION_VERSION})
        if entry is None:
            return 0, ""
        return (int(getattr(entry, "seq", 0) or 0),
                str(getattr(entry, "self_hash", "") or ""))
    except Exception as e:  # noqa: BLE001
        logger.debug("成本刹车审计写入失败 action=%s: %s", action, e)
        return 0, ""


def _emit(event_type: str, payload: Dict[str, Any], *, idempotency_key: str,
          ts: str = "", correlation_id: str = "",
          store: Any = None) -> Optional[Any]:
    """事件留痕（best-effort；`emit` 自身也不抛）"""
    try:
        return emit(event_type, payload, actor=ACTOR_SYSTEM,
                    correlation_id=correlation_id or "cost_brake",
                    idempotency_key=idempotency_key, ts=ts or None, store=store)
    except Exception as e:  # noqa: BLE001
        logger.debug("成本刹车事件发射失败 type=%s: %s", event_type, e)
        return None


# ════════════════════════════════════════════════════════════
#  成本刹车（日熔断 + 周断食）门面
# ════════════════════════════════════════════════════════════


def _now() -> datetime:
    return datetime.now().astimezone()


def next_midnight(now: datetime) -> datetime:
    """次日 00:00（带本地时区）——日熔断的自动恢复时点"""
    tomorrow = now.date() + timedelta(days=1)
    return datetime.combine(tomorrow, _time(0, 0), tzinfo=now.tzinfo)


@dataclass
class BrakeStatus:
    """一次判定的完整状态（**可观测**：状态 / 时间 / 阈值来源 / 口径版本）"""

    enabled: bool = False
    dry_run: bool = False
    evaluated_at: str = ""
    day: str = ""
    reason: str = ""
    # 日级熔断
    daily_cost_cents: float = 0.0
    daily_budget_cents: float = 0.0
    day_breaker_open: bool = False
    day_breaker_opened_at: str = ""
    day_breaker_opened_day: str = ""
    resume_at: str = ""
    # 周级断食
    fasting: Dict[str, Any] = field(default_factory=dict)
    ratio: Optional[float] = None
    baseline_cents_per_day: Optional[float] = None
    baseline_source: str = ""
    entry_threshold: Optional[float] = None
    entry_threshold_source: str = ""
    utc_cents_per_task: Optional[float] = None
    baseline_utc_per_task: Optional[float] = None
    utc_ratio: Optional[float] = None
    weekly: Dict[str, Any] = field(default_factory=dict)
    # θ / 阶段
    theta: Dict[str, Any] = field(default_factory=dict)
    theta_breached: Optional[bool] = None
    current_utc: Optional[float] = None
    theta_ceiling_cents: Optional[float] = None
    # 口径 / 配置
    shadow: Dict[str, Any] = field(default_factory=dict)
    config_warnings: List[str] = field(default_factory=list)
    config_sources: Dict[str, str] = field(default_factory=dict)
    cost_schema_version: str = COST_SCHEMA_VERSION
    calibration_version: str = CALIBRATION_VERSION
    calibration_note: str = CALIBRATION_NOTE
    source_of_truth: str = COST_SOURCE_OF_TRUTH
    audit_seq: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @property
    def restricted(self) -> bool:
        """是否处于降本模式（断食期）"""
        return str(self.fasting.get("state") or STATE_NORMAL) == STATE_FASTING

    def blocking(self) -> bool:
        """日熔断是否正在拦截非关键 outbound（干跑不计）"""
        return bool(self.day_breaker_open and not self.dry_run)


class CostBrake:
    """成本刹车门面（日级硬熔断 + 周级断食 + θ 披露）

    用法::

        brake = get_cost_brake()
        if not brake.allow_outbound(kind="shadow"):
            return {"skipped": "cost_brake"}
        status = brake.evaluate()            # 调度器/演练显式评估

    线程安全：状态更新在锁内完成；判定失败一律 **fail-open**（返回放行），
    保证「新增机制失败不得阻断主流程」。
    """

    def __init__(self, *, config: Optional[BrakeConfig] = None,
                 clock: Optional[Callable[[], datetime]] = None,
                 events_dir: Optional[str] = None,
                 state_path: Optional[str] = None,
                 persist: bool = True) -> None:
        self.config = config if config is not None else load_config()
        self._clock = clock or _now
        self._events_dir = events_dir
        self._state_path = (self.config.state_path if state_path is None
                            else str(state_path or ""))
        self._persist = bool(persist)
        self._lock = threading.RLock()
        self._machine = FastingMachine(
            in_ratio=self.config.fasting_in_ratio,
            out_ratio=self.config.fasting_out_ratio,
            out_hours=self.config.fasting_out_hours,
            cooldown_hours=self.config.cooldown_hours,
            in_consecutive=self.config.fasting_in_consecutive)
        self._status = BrakeStatus(enabled=bool(self.config.enabled))
        self._opened_at = ""
        self._opened_day = ""
        self._loaded = False
        self._load_state()

    # ── 状态持久化（best-effort；路径可注入以便测试隔离）──

    def _load_state(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not (self._persist and self._state_path):
            return
        try:
            path = Path(self._state_path)
            if not path.exists():
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            self._machine.restore(data.get("fasting"))
            self._opened_at = str(data.get("day_breaker_opened_at") or "")
            self._opened_day = str(data.get("day_breaker_opened_day") or "")
            # 冷启动即把持久化状态反映到可见状态（避免"重启后 status 说谎"）
            with self._lock:
                self._status.enabled = bool(self.config.enabled)
                self._status.day_breaker_open = bool(self._opened_at)
                self._status.day_breaker_opened_at = self._opened_at
                self._status.day_breaker_opened_day = self._opened_day
                self._status.fasting = self._machine.to_dict()
                self._status.reason = "冷启动：已从持久化状态恢复"
        except Exception as e:  # noqa: BLE001 状态不可读 → 冷启动
            logger.debug("成本刹车状态读取失败（冷启动）: %s", e)

    def _save_state(self, status: BrakeStatus) -> None:
        if not (self._persist and self._state_path):
            return
        payload = {
            "cost_schema_version": COST_SCHEMA_VERSION,
            "calibration_version": CALIBRATION_VERSION,
            "evaluated_at": status.evaluated_at,
            "enabled": status.enabled,
            "day_breaker_open": status.day_breaker_open,
            "day_breaker_opened_at": status.day_breaker_opened_at,
            "day_breaker_opened_day": status.day_breaker_opened_day,
            "fasting": self._machine.to_dict(),
        }
        try:
            target = Path(self._state_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        except OSError as e:  # noqa: BLE001 best-effort
            logger.debug("成本刹车状态写入失败: %s", e)

    # ── 查询 ──

    @property
    def machine(self) -> FastingMachine:
        return self._machine

    def status(self) -> Dict[str, Any]:
        """最近一次判定结果（**不触发重算**）

        `fasting` 字段按**状态机的实时状态**刷新——状态机是断食状态的**唯一**事实源，
        避免"直接推进状态机后 `status()` 还在报旧状态"的两源不一致。
        """
        with self._lock:
            self._status.fasting = self._machine.to_dict()
            return self._status.to_dict()

    def _is_stale(self, now: datetime) -> bool:
        evaluated = _parse_ts(self._status.evaluated_at)
        if evaluated is None:
            return True
        age = (now - evaluated).total_seconds()
        return age > float(self.config.eval_max_age_seconds)

    # ── 判定 ──

    def evaluate(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """执行一次完整判定（日熔断 + 周断食 + θ）并留痕

        **总开关关闭时直接返回 disabled 状态、不读盘、不发事件、不写状态**
        —— 保证「默认零影响」。
        """
        moment = now or self._clock()
        cfg = self.config
        if cfg.bypassed:
            with self._lock:
                self._status = BrakeStatus(
                    enabled=False, dry_run=cfg.dry_run,
                    evaluated_at=moment.isoformat(),
                    day=moment.date().isoformat(),
                    reason=f"{ENV_ENABLED} 未开启（默认关闭：成本刹车零影响）",
                    daily_budget_cents=cfg.daily_cents,
                    config_warnings=list(cfg.warnings),
                    config_sources=dict(cfg.sources),
                    fasting=self._machine.to_dict(),
                    theta=theta_limit(moment, config=cfg))
            return self._status.to_dict()
        try:
            return self._evaluate_inner(moment)
        except Exception as e:  # noqa: BLE001 fail-open：判定失败不得阻断主流程
            logger.warning("成本刹车判定失败（fail-open，不拦截）: %s", e)
            with self._lock:
                self._status.reason = f"判定失败（fail-open）: {type(e).__name__}: {e}"
            return self._status.to_dict()

    def _evaluate_inner(self, moment: datetime) -> Dict[str, Any]:
        cfg = self.config
        day = moment.date().isoformat()
        daily_row = _utc.utc_daily(day, directory=self._events_dir)
        shadow = shadow_overhead_audit(day=day, directory=self._events_dir,
                                       config=cfg, daily_row=daily_row)
        daily_cost = float(daily_row.get("cost_normalized_cents") or 0.0) \
            + float(shadow.get("shadow_overhead_cents_from_ms") or 0.0)

        transitions: List[Dict[str, Any]] = []
        with self._lock:
            # ── 1) 日级硬熔断 ──
            was_open = bool(self._opened_at)
            prev_opened_day = self._opened_day
            opened_this_call = False
            recovered = False
            recovery_reason = ""
            if cfg.day_breaker_configured:
                over = daily_cost > cfg.daily_cents
                if over and not was_open:
                    self._opened_at = moment.isoformat()
                    self._opened_day = day
                    opened_this_call = True
                elif was_open:
                    # 次日自动恢复（§7「次日 00:00 自动恢复」）或当日回到阈值内
                    if prev_opened_day != day:
                        recovered = True
                        recovery_reason = ("次日自动恢复（§7：日刹车只锁当日）")
                    elif not over:
                        recovered = True
                        recovery_reason = "当日成本回到阈值内"
                    if recovered:
                        self._opened_at = ""
                        self._opened_day = ""
            elif was_open:
                # 阈值被撤下（配置变更）→ 释放，避免"改了配置还锁着"
                recovered = True
                recovery_reason = "预算阈值已撤下（配置变更）→ 释放"
                self._opened_at = ""
                self._opened_day = ""
            breaker_open = bool(self._opened_at)

            snapshot = self._machine.snapshot
            fasting_before = snapshot.state

            # ── 2) 基线 / 比值 / 进阈值 ──
            if cfg.baseline_cents > 0:
                baseline: Dict[str, Any] = {
                    "cost_cents_per_day": float(cfg.baseline_cents),
                    "utc_cents_per_task": None, "source": "config",
                    "days": 0, "days_with_tasks": 0, "rows": []}
            else:
                baseline = rolling_baseline(moment, days=cfg.baseline_days,
                                            directory=self._events_dir)
            base_cost = baseline.get("cost_cents_per_day")
            ratio = (round(daily_cost / float(base_cost), 6)
                     if base_cost else None)

            theta = theta_limit(moment, config=cfg)
            entry_threshold = float(cfg.fasting_in_ratio)
            entry_source = f"{ENV_FASTING_IN_RATIO}(default {DEFAULT_FASTING_IN_RATIO})"
            if cfg.weekly_utc_high != DEFAULT_FASTING_IN_RATIO:
                entry_threshold = float(cfg.weekly_utc_high)
                entry_source = ENV_WEEKLY_UTC_HIGH
            if cfg.theta_binds_fasting and theta.get("limit") is not None:
                if float(theta["limit"]) < entry_threshold:
                    entry_threshold = float(theta["limit"])
                    entry_source = (f"θ({theta['phase']}, {theta['table_source']})"
                                    f" 严于断食进阈值")

            signal = ratio
            if cfg.fasting_signal == SIGNAL_UTC_PER_TASK:
                base_utc = baseline.get("utc_cents_per_task")
                today_utc = daily_row.get("utc_cents_per_task")
                signal = (round(float(today_utc) / float(base_utc), 6)
                          if (base_utc and today_utc is not None) else None)
            step = self._machine.observe(signal, moment, threshold=entry_threshold,
                                         threshold_source=entry_source)
            if step.get("transition"):
                transitions.append({"kind": "fasting", **step})

            # ── 3) θ 披露（UTC 上限）──
            base_utc_avg = baseline.get("utc_cents_per_task")
            current_utc = daily_row.get("utc_cents_per_task")
            theta_ceiling = None
            theta_breached = None
            if theta.get("limit") is not None and base_utc_avg and current_utc is not None:
                theta_ceiling = round(float(base_utc_avg) * float(theta["limit"]), 6)
                theta_breached = bool(float(current_utc) > theta_ceiling)

            weekly = _utc.utc_weekly(moment.date().isoformat(),
                                     directory=self._events_dir)
            status = BrakeStatus(
                enabled=True, dry_run=bool(cfg.dry_run),
                evaluated_at=moment.isoformat(), day=day,
                reason=("成本刹车已启用"
                        + ("（干跑：只留痕不拦截）" if cfg.dry_run else "")),
                daily_cost_cents=round(daily_cost, 6),
                daily_budget_cents=float(cfg.daily_cents),
                day_breaker_open=breaker_open,
                day_breaker_opened_at=self._opened_at,
                day_breaker_opened_day=self._opened_day,
                resume_at=(next_midnight(moment).isoformat() if breaker_open else ""),
                fasting=self._machine.to_dict(),
                ratio=ratio,
                baseline_cents_per_day=(None if base_cost is None
                                        else round(float(base_cost), 6)),
                baseline_source=str(baseline.get("source") or ""),
                entry_threshold=round(entry_threshold, 6),
                entry_threshold_source=entry_source,
                utc_cents_per_task=current_utc,
                baseline_utc_per_task=(None if base_utc_avg is None
                                       else round(float(base_utc_avg), 6)),
                utc_ratio=(round(float(current_utc) / float(base_utc_avg), 6)
                           if (base_utc_avg and current_utc is not None) else None),
                weekly={"iso_week": weekly.get("iso_week"),
                        "cost_normalized_cents": weekly.get("cost_normalized_cents"),
                        "window": weekly.get("window")},
                theta=dict(theta), theta_breached=theta_breached,
                current_utc=current_utc, theta_ceiling_cents=theta_ceiling,
                shadow=shadow,
                config_warnings=list(cfg.warnings),
                config_sources=dict(cfg.sources),
            )
            self._status = status
            if opened_this_call:
                transitions.append({"kind": "day_breaker", "transition": True,
                                    "from": "closed", "to": "open",
                                    "reason": (f"当日成本 {daily_cost:.6f} > 预算 "
                                               f"{cfg.daily_cents:.6f} cents")})
            if recovered:
                transitions.append({"kind": "day_breaker", "transition": True,
                                    "from": "open", "to": "closed",
                                    "reason": recovery_reason})
            if fasting_before != snapshot.state and not any(
                    t.get("kind") == "fasting" for t in transitions):
                transitions.append({"kind": "fasting", "transition": True,
                                    "from": fasting_before, "to": snapshot.state,
                                    "reason": snapshot.last_reason})

        for item in transitions:
            self._record_transition(item, status)
        self._save_state(status)
        return status.to_dict()

    # ── 跃迁留痕（审计 + 事件）──

    def _record_transition(self, item: Dict[str, Any], status: BrakeStatus) -> None:
        kind = str(item.get("kind") or "")
        to = str(item.get("to") or "")
        frm = str(item.get("from") or "")
        ts = status.evaluated_at
        if kind == "day_breaker":
            action = AUDIT_BREAKER_OPENED if to == "open" else AUDIT_BREAKER_RECOVERED
            payload = {
                "kind": "day_breaker", "from": frm, "to": to,
                "day": status.day, "daily_cost_cents": status.daily_cost_cents,
                "daily_budget_cents": status.daily_budget_cents,
                "resume_at": status.resume_at, "reason": item.get("reason", ""),
                "cost_schema_version": COST_SCHEMA_VERSION,
                "action": "stop_non_critical_outbound" if to == "open" else "resume",
            }
            seq, _hash = _audit(action, subject=f"cost:day:{status.day}",
                                payload=payload,
                                status="success" if to == "open" else "recovered")
            status.audit_seq = seq or status.audit_seq
            key = f"cost_brake:day:{status.day}:{to}"
            _emit(EV_HEALING_TRIGGERED,
                  {"type": "cost.daily_breaker", "severity": "high",
                   "mttd_ms": 0, "mttr_ms": 0, **payload},
                  idempotency_key=key, ts=ts)
            _emit(EV_METRICS_DELTA,
                  {"metric": METRIC_DAILY_COST,
                   "value": status.daily_cost_cents,
                   "threshold": status.daily_budget_cents,
                   "state": to, "day": status.day},
                  idempotency_key=f"{key}:delta", ts=ts)
            return
        if kind == "fasting":
            action = AUDIT_FASTING_ENTERED if to == STATE_FASTING else AUDIT_FASTING_EXITED
            payload = {
                "kind": "fasting", "from": frm, "to": to,
                "ratio": status.ratio,
                "entry_threshold": status.entry_threshold,
                "entry_threshold_source": status.entry_threshold_source,
                "out_ratio": self.config.fasting_out_ratio,
                "cooldown_hours": self.config.cooldown_hours,
                "shadow_factor": self.config.shadow_factor_fasting,
                "reason": item.get("reason", ""),
                "cost_schema_version": COST_SCHEMA_VERSION,
            }
            seq, _hash = _audit(action, subject="cost:fasting", payload=payload)
            status.audit_seq = seq or status.audit_seq
            _emit(EV_METRICS_DELTA,
                  {"metric": METRIC_FASTING_STATE, "from": frm, "to": to,
                   "ratio": status.ratio, "state": to,
                   "restricted": bool(to == STATE_FASTING)},
                  idempotency_key=f"cost_brake:fasting:{ts}:{to}", ts=ts)

    # ── 拦截判定 ──

    def allow_outbound(self, *, critical: bool = False, kind: str = "interactive",
                       now: Optional[datetime] = None) -> bool:
        """非关键 outbound 是否放行（P7.2-06）

        **默认放行**：``kind="interactive"``（用户显式请求）与 ``critical=True``
        （审批中任务等）恒放行 —— 这是「默认不误伤」的硬底线。

        只有**显式声明**为非关键后台 kind 的调用方才可能被拦：
        日熔断（OPEN）拦全部非关键；断食期只拦 `BACKGROUND_KINDS` 中的 kind。
        总开关关闭 / 干跑 / 判定异常 → 放行（fail-open）。
        """
        if critical or str(kind) in CRITICAL_KINDS:
            return True
        cfg = self.config
        if cfg.bypassed or cfg.dry_run:
            return True
        moment = now or self._clock()
        try:
            with self._lock:
                if self._is_stale(moment):
                    need_eval = True
                else:
                    need_eval = False
            if need_eval:
                self.evaluate(moment)
        except Exception as e:  # noqa: BLE001 fail-open
            logger.warning("成本刹车放行判定失败（fail-open）: %s", e)
            return True
        with self._lock:
            status = self._status
            if status.blocking():
                return False
            # 断食态取**状态机**（唯一事实源），不读上次判定的快照
            if self._machine.restricted and str(kind) in BACKGROUND_KINDS:
                return False
            return True

    def block_reason(self, *, critical: bool = False, kind: str = "interactive",
                     now: Optional[datetime] = None) -> str:
        """拦截原因（放行时返回 ""；供调用方如实上报，不静默跳过）"""
        if self.allow_outbound(critical=critical, kind=kind, now=now):
            return ""
        with self._lock:
            status = self._status
            if status.blocking():
                return (f"日级成本熔断 OPEN（当日 {status.daily_cost_cents} > 预算 "
                        f"{status.daily_budget_cents} cents，{status.resume_at} 自动恢复）"
                        f"，已停非关键 outbound kind={kind}")
            return (f"断食降本模式（{self._machine.state}，比值 "
                    f"{status.ratio}），已限制非关键影子/消化任务 kind={kind}")

    def suppression(self) -> Dict[str, Any]:
        """降本联动视图（供 S3 shadow / 消化调度查询）——**关闭时零影响**

        断食状态直接取**状态机**（唯一事实源），不读最近一次判定的快照。
        """
        cfg = self.config
        with self._lock:
            status = self._status
            state = self._machine.state
            breaker_open = bool(status.day_breaker_open)
            self._status.fasting = self._machine.to_dict()
        if cfg.bypassed:
            return {"enabled": False, "dry_run": cfg.dry_run,
                    "day_breaker_open": False, "fasting": state,
                    "restricted": False, "shadow_budget_factor": 1.0,
                    "blocked_kinds": [], "reason": "成本刹车未开启（零影响）"}
        effective = bool(breaker_open or state == STATE_FASTING) and not cfg.dry_run
        return {
            "enabled": True,
            "dry_run": bool(cfg.dry_run),
            "day_breaker_open": breaker_open,
            "fasting": state,
            "restricted": effective,
            "shadow_budget_factor": (float(cfg.shadow_factor_fasting)
                                     if effective else 1.0),
            "blocked_kinds": (sorted(BACKGROUND_KINDS) if effective else []),
            "reason": ("日熔断 OPEN" if breaker_open
                       else ("断食降本模式" if state == STATE_FASTING
                             else "常态（不干预）")),
        }

    def reset(self) -> None:
        """清除内存 + 持久化状态（**测试/运维手动恢复**用；不删审计）"""
        with self._lock:
            self._machine = FastingMachine(
                in_ratio=self.config.fasting_in_ratio,
                out_ratio=self.config.fasting_out_ratio,
                out_hours=self.config.fasting_out_hours,
                cooldown_hours=self.config.cooldown_hours,
                in_consecutive=self.config.fasting_in_consecutive)
            self._opened_at = ""
            self._opened_day = ""
            self._status = BrakeStatus(enabled=bool(self.config.enabled))
        if self._persist and self._state_path:
            try:
                Path(self._state_path).unlink(missing_ok=True)
            except OSError as e:  # noqa: BLE001
                logger.debug("成本刹车状态删除失败: %s", e)


# ════════════════════════════════════════════════════════════
#  进程单例 + 模块级便捷入口
# ════════════════════════════════════════════════════════════

_BRAKE: Optional[CostBrake] = None
_BRAKE_LOCK = threading.Lock()


def get_cost_brake(reload: bool = False) -> CostBrake:
    """进程级成本刹车单例（懒加载；``reload=True`` 重新装配配置）"""
    global _BRAKE
    if _BRAKE is None or reload:
        with _BRAKE_LOCK:
            if _BRAKE is None or reload:
                _BRAKE = CostBrake()
    return _BRAKE


def reset_cost_brake() -> None:
    """清除进程单例（**测试专用**；不触碰磁盘状态）"""
    global _BRAKE
    with _BRAKE_LOCK:
        _BRAKE = None


def evaluate_brakes(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    """模块级判定入口（调度器/演练/运维查询）"""
    return get_cost_brake().evaluate(now)


def allow_outbound(*, critical: bool = False, kind: str = "interactive",
                   now: Optional[datetime] = None) -> bool:
    """模块级放行判定（**默认放行**，见 `CostBrake.allow_outbound`）"""
    return get_cost_brake().allow_outbound(critical=critical, kind=kind, now=now)


def cost_policy_snapshot() -> Dict[str, Any]:
    """降本联动视图（模块级；供 digestion/shadow 等查询）"""
    return get_cost_brake().suppression()


def shadow_budget_factor(*, env: Optional[Dict[str, str]] = None) -> float:
    """断食/熔断期的 shadow 预算系数（1.0 = 无影响）

    供 S3-03 `shadow.daily_budget()` 联动（§4.5 每日预算 × 系数）。
    **任何异常都返回 1.0**（不阻断影子任务；默认关闭时零影响）。
    """
    try:
        if env is not None and not _env_flag(ENV_ENABLED, False, env):
            return 1.0
        return float(get_cost_brake().suppression().get("shadow_budget_factor", 1.0))
    except Exception as e:  # noqa: BLE001
        logger.debug("shadow 预算系数查询失败（按 1.0 无影响处理）: %s", e)
        return 1.0


def digestion_restricted() -> Tuple[bool, str]:
    """消化/内化/重探等非关键后台任务是否应被抑制 → ``(受限, 原因)``

    供 S3 调度任务体在 ``_tick`` 开始处查询（默认 ``(False, "")`` = 不干预）。
    """
    try:
        snap = get_cost_brake().suppression()
        if snap.get("restricted"):
            return True, str(snap.get("reason") or "成本刹车降本模式")
        return False, ""
    except Exception as e:  # noqa: BLE001
        logger.debug("消化抑制查询失败（按不受限处理）: %s", e)
        return False, ""


class outbound_guard:
    """非关键 outbound 的守卫上下文（**拦截即抛**，便于调用方如实上报）

    用法::

        with outbound_guard(kind="shadow"):
            run_shadow()          # 熔断/断食期直接抛 OutboundBlockedError

    Args:
        kind: outbound 种类（见 `CRITICAL_KINDS` / `BACKGROUND_KINDS`）。
        critical: 显式声明为关键路径（用户显式请求 / 审批中任务）→ 永不拦截。
        brake: 注入刹车实例（默认进程单例；测试可注入）。
        now: 注入时钟。
    """

    def __init__(self, *, kind: str = "interactive", critical: bool = False,
                 brake: Optional[CostBrake] = None,
                 now: Optional[datetime] = None) -> None:
        self._kind = str(kind)
        self._critical = bool(critical)
        self._brake = brake
        self._now = now

    def __enter__(self) -> "outbound_guard":
        brake = self._brake or get_cost_brake()
        if not brake.allow_outbound(critical=self._critical, kind=self._kind,
                                    now=self._now):
            raise OutboundBlockedError(
                brake.block_reason(critical=self._critical, kind=self._kind,
                                   now=self._now),
                reason=brake.status().get("reason", ""),
                state=str(brake.status().get("fasting", {}).get("state") or ""),
                day=str(brake.status().get("day") or ""), kind=self._kind)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """不吞异常（返回 None ⇒ 假值 ⇒ 异常继续向上传播）"""
        return None


__all__ = [
    # 口径
    "COST_SCHEMA_VERSION", "CALIBRATION_VERSION", "CALIBRATION_NOTE",
    "CALIBRATION_TRIGGER", "COST_SOURCE_OF_TRUTH",
    # 环境键
    "ENV_ENABLED", "ENV_DAILY_CENTS", "ENV_WEEKLY_UTC_HIGH", "ENV_FASTING_IN_RATIO",
    "ENV_FASTING_OUT_RATIO", "ENV_FASTING_OUT_HOURS", "ENV_FASTING_IN_CONSECUTIVE",
    "ENV_COOLDOWN_HOURS", "ENV_BASELINE_DAYS", "ENV_BASELINE_CENTS",
    "ENV_SHADOW_FACTOR_FASTING", "ENV_SHADOW_MS_CENTS_PER_S", "ENV_FASTING_SIGNAL",
    "ENV_THETA_TABLE", "ENV_THETA_BINDS_FASTING", "ENV_PHASE", "ENV_PHASE_START",
    "ENV_DRY_RUN", "ENV_STATE_PATH", "ENV_EVAL_MAX_AGE",
    # 默认值 / 口径常量
    "DEFAULT_FASTING_IN_RATIO", "DEFAULT_FASTING_OUT_RATIO",
    "DEFAULT_FASTING_OUT_HOURS", "DEFAULT_COOLDOWN_HOURS",
    "DEFAULT_FASTING_IN_CONSECUTIVE", "DEFAULT_BASELINE_DAYS",
    "DEFAULT_SHADOW_FACTOR_FASTING", "DEFAULT_SHADOW_MS_CENTS_PER_S",
    "DEFAULT_EVAL_MAX_AGE_SECONDS", "DEFAULT_THETA_TABLE", "DEFAULT_STATE_PATH",
    "DEFAULT_COST_DAILY_PATH",
    "SIGNAL_DAILY_COST", "SIGNAL_UTC_PER_TASK", "FASTING_SIGNALS",
    "STATE_NORMAL", "STATE_FASTING", "STATE_COOLDOWN", "FASTING_STATES",
    "CRITICAL_KINDS", "BACKGROUND_KINDS",
    "PHASE_W1_W4", "PHASE_W5_W9", "PHASE_W10_W14", "PHASE_M4_M6", "PHASE_M7_PLUS",
    "PHASES", "APPROVAL_DECAY_TARGET", "AUTO_APPROVAL_KINDS",
    "MIN_DISCLOSURE_SAMPLE",
    "AUDIT_BREAKER_OPENED", "AUDIT_BREAKER_RECOVERED", "AUDIT_FASTING_ENTERED",
    "AUDIT_FASTING_EXITED", "AUDIT_LEGACY_WRITE",
    "METRIC_DAILY_COST", "METRIC_FASTING_STATE", "METRIC_SHADOW_SUPPRESSED",
    # 异常
    "CostBrakeError", "OutboundBlockedError",
    # 配置
    "BrakeConfig", "load_config",
    # 阶段 / θ
    "resolve_phase", "theta_limit",
    # 断食机制
    "FastingMachine", "FastingSnapshot", "next_midnight",
    # 基线与度量
    "rolling_baseline", "shadow_overhead_audit", "approval_decay_rate",
    "cost_daily_view", "write_cost_daily",
    # 门面
    "BrakeStatus", "CostBrake", "get_cost_brake", "reset_cost_brake",
    "evaluate_brakes", "allow_outbound", "cost_policy_snapshot",
    "shadow_budget_factor", "digestion_restricted", "outbound_guard",
]
