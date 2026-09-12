"""shadow 灰度运行 + 三层比对（TASK-S3-03 步骤 1/2 / v7.2 §4.5）

**职责**：把 `mirrored → shadow` 的能力放进**灰度期**：按 §4.5 的抽样与预算规则
挑出该观察的流量，用 S3-02 的 `ReplaySandbox` **双跑**（上游 vs 候选）做三层比对
（`compare() → CompareVerdict`），把结果记成**观测台账**，并把负例、人工抽检与劣化
信号交出去（供 R4"学错能力"防护与 `internalize` 六条件引擎消费）。

## 三条守不易的契约（可被用例断言）

1. **shadow 只记录，不接管真实执行**：本模块**不**改任何真实调用路径；候选执行一律
   发生在 S3-02 的确定性回放沙箱里（虚拟文件系统、副作用只记录、`commit()` 抛错）。
   灰度 5% 在这里是**选中记录**（`gray.routed`），不是真实接管 —— 见下 §"隔离边界"。
2. **默认关闭**：shadow 自动运行需 `CP_DIGESTION_SHADOW_ENABLED=true`（或调用方显式
   `force=True`）；灰度 5% 需**显式阈值**（能力级 `evolution.shadow_config.gray_ratio`
   或 `CP_DIGESTION_GRAY_RATIO`），非法值一律**回退默认关闭**。
3. **凭通行证放行**：灰度前必须能从 `gate.PassportStore` 取到**合法**通行证
   （复用 `stage.acceptance_passport_ok()` 自洽校验）——**不自建开关绕过验收门**。
   取不到证 ⇒ `allowed=False` 并逐条给出理由（fail-closed，不静默放行）。

## 抽样与预算（§4.5）

- **每日预算** ``min(日均 × 15%, 50 次)``（`daily_budget()`）；低流量保底 1 次
  （`SHADOW_MIN_BUDGET`，可关）—— 与 T2 修正同源：避免"比例太小 ⇒ 永远没有样本 ⇒
  消化空转"。日均取自灰度台账历史（`ShadowLedger.daily_average()`）或显式传入。
- **确定性抽样**：``sha1(sample_id)`` 哈希排序取前 N（`deterministic_sample()`）——
  同一批两次结果**逐字一致**（可复现，故"为什么抽到这条"可审计）；不用随机数。
- 抽样键：trace 派生用例用 `origin_trace_id`（§3.1 的溯源指针），Seed 用例用
  ``case:<case_id>``；调用方也可显式传 `trace_ids` 作为抽样宇宙。

## M1 / M2 / M5 / M6 的落地方式（S3-02 移交遗留）

| # | 要求 | 本模块的落地 |
|---|---|---|
| M1 | 接入**真实 LLM-judge**（≥0.85）且 `judge_kind` 可区分 | `LLMJudge`（真模型调用，可注入 `invoke`/adapter）+ `resolve_judge()`；层③ 经 `judge_kind=` 写精确标签；模型不可用/无凭证时**如实标注回落** |
| M2 | 条件⑤ 用**真实墙钟 p99** | `ReplaySandbox(measure_wall=True)`：每臂记 `Observation.wall_ms`（`perf_counter`）；报告 `clock` 字段显式标注口径 |
| M5 | 10% 人工抽检**实际执行并留痕** | `ManualReviewQueue`：清单 + `record_review()`（复核人/角色/结论/时间落盘 + 链式审计）；`is_closed()` 未闭合即**不得视为已验收** |
| M6 | 隔离边界明确或显式声明 | `ShadowReport.isolation` 如实声明三层事实：实际执行模型（`mode`）、本次是否真接管（`real_takeover`）、环境最高等级（`available_level`）；并附**不保证边界**清单（S8-03） |

## 隔离边界（M6，显式声明；S8-03 起**可开但默认关**）

S3-02 的回放沙箱是**进程内确定性执行模型**，不是容器隔离。S3-03 期间本模块因此
**不打开**真实接管。S8-03 补齐了执行隔离（`agent.digestion.isolation`：容器 /
强隔离子进程）之后：

- `real_takeover` **默认仍然关闭**，需 `CP_DIGESTION_REAL_TAKEOVER=true` **且**给出
  显式比例（`CP_DIGESTION_REAL_TAKEOVER_RATIO` 或能力级
  `shadow_config.real_takeover_ratio`）；
- 隔离等级为 `in_process`（无 Docker 且子进程不可用）⇒ **拒绝**接管
  （`reasons` 写明，绝不"看起来能接管"）；
- 接管执行**仍在隔离边界内**，副作用只记录不双写；**产物不自动合入**
  （`TakeoverReport.adopted` 恒 `False`）；
- 连续失败 N 次 ⇒ 自动回落 `sandbox_replay_only` + 事故卡（见 `takeover.py`）。

`isolation_declaration()` 因此同时报告三件事：**本次实际用的执行模型**
（`mode`）、**本次是否真的接管了**（`real_takeover`）、**环境具备的最高等级**
（`available_level`）——三者分开，才谈得上"等级如实标注"。

**import 纪律**：与同包一致，重依赖（`agent.observability.events` / `agent.audit.facade` /
`agent.descriptors.registry` / `agent.observability.trace_v2` / `agent.model_router.adapters`）
一律**函数体内懒加载**；本模块导入期无文件/DB/网络副作用（唯一写盘是显式构造的
`ShadowLedger` / `ManualReviewQueue` / `TakeoverLedger`）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .cases import (
    CANDIDATE_KIND_SEED_NATIVE,
    CaseSet,
    CaseStore,
    EquivalenceCase,
    applicable_cases,
    normalize_candidate_kind,
    open_case_store,
    seed_candidate_for,
)
from .gate import PassportStore
from .isolation import (
    ISOLATION_IN_PROCESS,
    IsolationPlan,
    isolation_boundaries,
    resolve_isolation_level,
)
from .sandbox import (
    JUDGE_THRESHOLD,
    LAYER_JUDGE,
    LAYER_SIDE_EFFECTS,
    LAYER_STRUCTURE,
    ReplaySandbox,
    as_implementation,
    judge_similarity,
    manual_sample_ids,
)
from .takeover import (
    TAKEOVER_STATUS_NOT_REQUESTED,
    TAKEOVER_STATUS_NOT_SAMPLED,
    TRANSPORT_ISOLATED_TAKEOVER,
    TakeoverEngine,
    TakeoverLedger,
    TakeoverReport,
    resolve_takeover_policy,
)

logger = logging.getLogger("agent.digestion.shadow")

# ════════════════════════════════════════════════════════════
#  常量（门槛/口径单点定义）
# ════════════════════════════════════════════════════════════

#: 灰度器版本（进报告与台账；口径变更须改版本以便追溯"当时按哪版抽样"）
SHADOW_VERSION = "s3-03.1"

#: 每日预算 = min(日均 × 15%, 50 次)（§4.5）
SHADOW_BUDGET_RATIO = 0.15
SHADOW_BUDGET_CAP = 50
#: 低流量保底（T2 同源：比例太小则永远无样本 ⇒ 消化空转；可经 env 关闭）
SHADOW_MIN_BUDGET = 1

#: 开关与阈值（**默认关闭** —— 安全底线：显式阈值才开）
SHADOW_ENABLE_ENV = "CP_DIGESTION_SHADOW_ENABLED"
SHADOW_BUDGET_RATIO_ENV = "CP_DIGESTION_SHADOW_BUDGET_RATIO"
SHADOW_BUDGET_CAP_ENV = "CP_DIGESTION_SHADOW_BUDGET_CAP"
SHADOW_MIN_BUDGET_ENV = "CP_DIGESTION_SHADOW_MIN_BUDGET"
GRAY_ENABLE_ENV = "CP_DIGESTION_GRAY_ENABLED"
GRAY_RATIO_ENV = "CP_DIGESTION_GRAY_RATIO"
#: §4.5 的灰度比例基准（5%）：**基准值，不是默认值** —— 未显式给出即关闭
GRAY_RATIO_BASELINE = 0.05
GRAY_RATIO_DEFAULT = 0.0

#: judge 模式与注入
JUDGE_MODE_ENV = "CP_DIGESTION_JUDGE"
JUDGE_PROVIDER_ENV = "CP_DIGESTION_JUDGE_PROVIDER"
JUDGE_MODEL_ENV = "CP_DIGESTION_JUDGE_MODEL"
JUDGE_MODE_AUTO = "auto"
JUDGE_MODE_LLM = "llm"
JUDGE_MODE_LOCAL = "local"
JUDGE_MODES: Tuple[str, ...] = (JUDGE_MODE_AUTO, JUDGE_MODE_LLM, JUDGE_MODE_LOCAL)
#: `judge_kind` 取值（M1：确定性打分器与 LLM-judge **必须可区分**）
JUDGE_KIND_LLM = "llm_judge"
JUDGE_KIND_LOCAL = "deterministic_local"
JUDGE_KIND_INJECTED = "injected"
JUDGE_KIND_LLM_FALLBACK = "deterministic_local(llm_unavailable)"

#: 墙钟口径标注（M2）
CLOCK_WALL = "wall_clock(perf_counter; per-arm real elapsed)"
CLOCK_MODEL = "model_clock(标称延迟累加)"
TRANSPORT_SANDBOX_ONLY = "sandbox_replay_only"

#: 执行模型（`isolation.mode` 的取值；与 S3-03 的旧值**逐字兼容**）
MODE_IN_PROCESS = "in_process_deterministic_model"
MODE_ISOLATED = "isolated_execution"

#: 落盘位置（**运行时区**，gitignore；测试须显式隔离）
DEFAULT_SHADOW_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "digestion", "shadow")
SHADOW_DIR_ENV = "CP_DIGESTION_SHADOW_DIR"
SHADOW_LEDGER_FILENAME = "shadow_ledger.jsonl"
MANUAL_REVIEW_FILENAME = "manual_reviews.jsonl"

#: 劣化（R4：shadow 期持续劣化 → 降级/回退建议）
DEGRADE_WINDOW = 20
DEGRADE_RATE_FLOOR = 0.90
DEGRADE_CONSECUTIVE = 3
DEGRADE_MIN_SAMPLES = 20
DEGRADE_VERDICT_INSUFFICIENT = "insufficient_samples"
DEGRADE_VERDICT_STABLE = "stable"
DEGRADE_VERDICT_DEGRADED = "degraded"
DEGRADE_ACTION_OBSERVE = "observe"
DEGRADE_ACTION_WARN = "warn"
DEGRADE_ACTION_ROLLBACK = "recommend_degrade"

#: 人工抽检（M5）
REVIEW_VERDICT_PASS = "pass"
REVIEW_VERDICT_FAIL = "fail"
REVIEW_VERDICT_UNCERTAIN = "uncertain"
REVIEW_VERDICTS: Tuple[str, ...] = (REVIEW_VERDICT_PASS, REVIEW_VERDICT_FAIL,
                                    REVIEW_VERDICT_UNCERTAIN)
REVIEW_ROLE_HUMAN = "human"
REVIEW_ROLE_AGENT = "agent_assisted"

#: 事件与审计（**不新增事件类型** —— 复用 `digest.stage`，与 S3-02 同纪律）
EVENT_SCOPE_SHADOW = "shadow_gray"
AUDIT_ACTION_OBSERVED = "digest.shadow.observed"
AUDIT_ACTION_DEGRADED = "digest.shadow.degraded"
AUDIT_ACTION_BLOCKED = "digest.shadow.blocked"
AUDIT_ACTION_REVIEWED = "digest.shadow.manual_review"


# ════════════════════════════════════════════════════════════
#  环境开关（非法值一律回退默认，不抛不静默）
# ════════════════════════════════════════════════════════════


def _env(env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    return dict(os.environ if env is None else env)


def _env_flag(name: str, default: bool = False,
              env: Optional[Dict[str, str]] = None) -> bool:
    raw = str(_env(env).get(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    logger.warning("%s=%r 非法布尔值，回退默认 %s", name, raw, default)
    return bool(default)


def _env_float(name: str, default: float,
               env: Optional[Dict[str, str]] = None) -> float:
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


def shadow_enabled(*, reason: bool = False,
                   env: Optional[Dict[str, str]] = None) -> Any:
    """shadow 自动运行开关（**默认关闭**）

    ``reason=True`` 时返回 ``(enabled, 说明)`` —— 让"为什么没跑"可解释（不静默）。
    """
    flag = _env_flag(SHADOW_ENABLE_ENV, False, env)
    text = (f"{SHADOW_ENABLE_ENV}=true" if flag
            else f"{SHADOW_ENABLE_ENV} 未开启（默认关闭：shadow 不自动运行）")
    return (flag, text) if reason else flag


def budget_from_env(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """预算参数（非法值回退默认；比例/上限做区间保护）"""
    ratio = _env_float(SHADOW_BUDGET_RATIO_ENV, SHADOW_BUDGET_RATIO, env)
    if not (0.0 < ratio <= 1.0):
        logger.warning("%s=%s 越界（须 0<ratio<=1），回退默认 %s",
                       SHADOW_BUDGET_RATIO_ENV, ratio, SHADOW_BUDGET_RATIO)
        ratio = SHADOW_BUDGET_RATIO
    cap = _env_int(SHADOW_BUDGET_CAP_ENV, SHADOW_BUDGET_CAP, env)
    if cap < 0:
        logger.warning("%s=%s 非法（须 ≥0），回退默认 %s",
                       SHADOW_BUDGET_CAP_ENV, cap, SHADOW_BUDGET_CAP)
        cap = SHADOW_BUDGET_CAP
    floor = _env_int(SHADOW_MIN_BUDGET_ENV, SHADOW_MIN_BUDGET, env)
    if floor < 0:
        floor = SHADOW_MIN_BUDGET
    return {"ratio": float(ratio), "cap": int(cap), "min_budget": int(floor)}


# ════════════════════════════════════════════════════════════
#  抽样与预算（§4.5；确定性）
# ════════════════════════════════════════════════════════════


def hash_fraction(sample_id: str) -> float:
    """``sample_id`` → ``[0,1)`` 的确定性哈希位（sha1 前 12 位十六进制 / 2^48）"""
    digest = hashlib.sha1(str(sample_id or "").encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(16 ** 12)


def deterministic_sample(sample_ids: Iterable[str], size: int) -> List[str]:
    """按 ``sha1(sample_id)`` 排序取前 ``size`` 条 —— **同一批两次逐字一致**

    刻意不用随机数：抽样必须可复现，否则"为什么抽到这条"无法被审计（与 §4.5 的
    "trace_id 哈希确定性抽样"逐字对齐）。
    """
    ordered = sorted({str(s) for s in (sample_ids or [])},
                     key=lambda s: (hashlib.sha1(s.encode("utf-8")).hexdigest(), s))
    limit = max(0, int(size))
    return ordered[:limit]


def _cost_policy_factor(env: Optional[Dict[str, str]] = None) -> float:
    """S5-03 成本刹车联动系数（**默认 1.0 = 无影响**）

    断食/日熔断生效期返回 `CP_BUDGET_SHADOW_FACTOR_FASTING`（默认 0.0 = 归零，
    可配 0.5 = 减半），用于 §4.5 每日预算的降本联动。

    边界纪律：**任何异常、任何缺失都返回 1.0** —— 成本刹车是新增机制，
    其故障不得让影子任务停摆（「新增机制失败不得阻断主流程」）。
    """
    try:
        from agent.monitoring.cost_brake import shadow_budget_factor
        return float(shadow_budget_factor(env=env))
    except Exception as e:  # noqa: BLE001
        logger.debug("成本刹车系数不可用（按 1.0 无影响处理）: %s", e)
        return 1.0


def daily_budget(daily_avg: float, *, ratio: float = SHADOW_BUDGET_RATIO,
                 cap: int = SHADOW_BUDGET_CAP,
                 min_budget: int = SHADOW_MIN_BUDGET,
                 factor: Optional[float] = None) -> int:
    """每日预算 ``min(日均 × ratio, cap)``（§4.5；低流量保底 ``min_budget``）

    - ``日均 × ratio`` 取**下取整**（"不超"是硬要求）；
    - 结果为 0 且 ``日均 ≥ 1`` 且 ``min_budget > 0`` ⇒ 保底 ``min_budget``
      （T2 同源：否则低流量下 15% 永远抽不到样本）；
    - 上限 ``cap`` 优先于保底（"不超"仍是硬要求）。

    Args:
        factor: 成本刹车降本系数（S5-03 联动）。``None`` → 查询成本刹车
            （未开启时恒为 1.0，即**零影响**）；``0.0`` 表示断食期**归零**、
            ``0.5`` 表示减半。系数**在保底之后**生效，故 ``0.0`` 能真正归零
            （否则低流量保底会把影子任务又放回来，与 §6.7「冻结非关键消化」相悖）。
            ``0 < factor < 1`` 时下取整后不归零（至少保留 1 次，避免取整把影子任务关死）。
    """
    try:
        avg = max(0.0, float(daily_avg or 0.0))
    except (TypeError, ValueError):
        logger.warning("daily_avg=%r 非法，按 0 处理", daily_avg)
        avg = 0.0
    try:
        rate = float(ratio)
    except (TypeError, ValueError):
        rate = SHADOW_BUDGET_RATIO
    if not (0.0 < rate <= 1.0):
        rate = SHADOW_BUDGET_RATIO
    ceiling = max(0, int(cap))
    planned = int(avg * rate)
    if planned == 0 and avg >= 1.0 and int(min_budget) > 0:
        planned = int(min_budget)
    budget = max(0, min(planned, ceiling))

    coeff = _cost_policy_factor() if factor is None else factor
    try:
        coeff_value = float(coeff)
    except (TypeError, ValueError):
        logger.warning("成本刹车系数 %r 非法，按 1.0 处理", coeff)
        coeff_value = 1.0
    if not (0.0 <= coeff_value <= 1.0):
        logger.warning("成本刹车系数 %s 越界（须 0..1），按 1.0 处理", coeff_value)
        coeff_value = 1.0
    if coeff_value == 1.0:
        return budget
    scaled = int(budget * coeff_value)          # 下取整（"不超"是硬要求）
    if coeff_value > 0.0 and budget > 0 and scaled == 0:
        scaled = 1                              # 系数>0 ⇒ 不因取整关死影子任务
    return max(0, min(scaled, ceiling))


# ════════════════════════════════════════════════════════════
#  灰度 5% 策略（默认关闭；需显式阈值）
# ════════════════════════════════════════════════════════════

GRAY_SOURCE_DEFAULT = "default_off"
GRAY_SOURCE_ENV = "env"
GRAY_SOURCE_DESCRIPTOR = "descriptor.shadow_config"


def _valid_ratio(value: Any) -> Optional[float]:
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return None
    if not (0.0 < ratio <= 1.0):
        return None
    return ratio


def resolve_gray_policy(*, shadow_config: Optional[Dict[str, Any]] = None,
                        env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """灰度策略解析（**默认关闭**；开启需**显式阈值**）

    优先级：能力级 ``descriptor.evolution.shadow_config`` > 环境变量 > 默认关闭。

    两条硬规则（安全底线 + 可调参数纪律）：

    1. **只声明开启、不给阈值 ⇒ 不开**（理由如实给出，不静默当作 5%）；
    2. **非法阈值（非数值 / ≤0 / >1）⇒ 回退默认关闭**并说明。

    ``candidate_execution`` 恒为 ``sandbox_replay_only``、``real_takeover`` 恒为
    ``False``：本任务不打开真实接管（M6 的隔离边界声明）。
    """
    reasons: List[str] = []
    cfg = dict(shadow_config or {})
    env_map = _env(env)
    cfg_enabled = cfg.get("enabled")
    cfg_ratio = cfg.get("gray_ratio", cfg.get("ratio"))
    env_enabled = _env_flag(GRAY_ENABLE_ENV, False, env)
    env_ratio_raw = env_map.get(GRAY_RATIO_ENV)

    if cfg_enabled is not None or cfg_ratio is not None:
        enabled = bool(cfg_enabled) if cfg_enabled is not None else True
        ratio = _valid_ratio(cfg_ratio)
        if not enabled:
            reasons.append("能力级 shadow_config.enabled=false")
            chosen: Optional[float] = None
        elif ratio is None:
            reasons.append("能力级 shadow_config 声明开启但未给出合法 gray_ratio"
                           "（须 0<ratio<=1）⇒ 不开")
            chosen = None
        else:
            chosen = ratio
            reasons.append(f"能力级显式阈值 gray_ratio={ratio}")
        if chosen is not None:
            return {"enabled": True, "ratio": chosen,
                    "source": GRAY_SOURCE_DESCRIPTOR, "reasons": reasons,
                    "candidate_execution": TRANSPORT_SANDBOX_ONLY,
                    "real_takeover": False}
        # 能力级声明了但不可用 ⇒ 明确回退关闭（不静默改用 env）
        return {"enabled": False, "ratio": GRAY_RATIO_DEFAULT,
                "source": GRAY_SOURCE_DESCRIPTOR, "reasons": reasons,
                "candidate_execution": TRANSPORT_SANDBOX_ONLY,
                "real_takeover": False}

    if env_enabled or str(env_ratio_raw or "").strip():
        ratio = _valid_ratio(env_ratio_raw)
        if ratio is None:
            reasons.append(f"{GRAY_RATIO_ENV}={env_ratio_raw!r} 非法或缺失"
                           f"（须 0<ratio<=1）⇒ 回退默认关闭")
            return {"enabled": False, "ratio": GRAY_RATIO_DEFAULT,
                    "source": GRAY_SOURCE_ENV, "reasons": reasons,
                    "candidate_execution": TRANSPORT_SANDBOX_ONLY,
                    "real_takeover": False}
        if not env_enabled:
            reasons.append(f"{GRAY_ENABLE_ENV} 未开启（仅有阈值不构成开启）⇒ 不开")
            return {"enabled": False, "ratio": GRAY_RATIO_DEFAULT,
                    "source": GRAY_SOURCE_ENV, "reasons": reasons,
                    "candidate_execution": TRANSPORT_SANDBOX_ONLY,
                    "real_takeover": False}
        reasons.append(f"环境变量显式阈值 {GRAY_RATIO_ENV}={ratio}")
        return {"enabled": True, "ratio": ratio, "source": GRAY_SOURCE_ENV,
                "reasons": reasons,
                "candidate_execution": TRANSPORT_SANDBOX_ONLY,
                "real_takeover": False}

    reasons.append("未给出显式阈值（能力级 shadow_config 与环境变量均未设置）"
                   f"⇒ 默认关闭（§4.5 的 {GRAY_RATIO_BASELINE:.0%} 是基准值，不是默认值）")
    return {"enabled": False, "ratio": GRAY_RATIO_DEFAULT,
            "source": GRAY_SOURCE_DEFAULT, "reasons": reasons,
            "candidate_execution": TRANSPORT_SANDBOX_ONLY, "real_takeover": False}


def gray_routed_ids(sample_ids: Iterable[str], *, ratio: float) -> List[str]:
    """灰度选中集合（确定性：``hash_fraction < ratio`` 且按哈希序稳定）"""
    rate = _valid_ratio(ratio)
    if rate is None:
        return []
    hit = [str(s) for s in (sample_ids or []) if hash_fraction(s) < rate]
    return sorted(hit, key=lambda s: (hashlib.sha1(s.encode("utf-8")).hexdigest(), s))


# ════════════════════════════════════════════════════════════
#  judge（M1：真实 LLM-judge + 如实标注 judge_kind）
# ════════════════════════════════════════════════════════════

_JUDGE_PROMPT = """你是"实现等价性判定器"。下面给出同一任务的两次执行观测（已做路径形态归一）。
请只判断二者在**语义上是否等价**（步骤/输出/副作用一致），并给出 0 到 1 的相似度分数。

【上游观测】{reference}

【候选观测】{observed}

只输出一行 JSON：{{"score": <0..1 的小数>, "reason": "<一句话>"}}"""

_JSON_SCORE = re.compile(r'"score"\s*:\s*"?([0-9]*\.?[0-9]+)"?\s*(%?)')
_ANY_SCORE = re.compile(r"([0-9]*\.?[0-9]+)\s*(%?)")


class JudgeUnavailable(RuntimeError):
    """LLM-judge 不可用（无凭证 / 无适配器 / 调用失败）—— 调用方据此**如实回落**"""


def parse_judge_score(text: Any) -> Optional[float]:
    """模型回复 → ``[0,1]`` 分数（解析失败返回 ``None``，不猜、不截断成假分）

    支持 ``{"score": 0.87}`` / ``0.87`` / ``87%``；越界值按百分比或 [0,1] 归一。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    match = _JSON_SCORE.search(raw)
    if match is None:
        match = _ANY_SCORE.search(raw)
    if match is None:
        return None
    try:
        value = float(match.group(1))
    except (TypeError, ValueError):
        return None
    percent = (match.lastindex or 1) >= 2 and str(match.group(2)) == "%"
    if percent or value > 1.0:
        value = value / 100.0
    if value < 0.0:
        return None
    return round(min(1.0, value), 4)


def _extract_reply(out: Any) -> str:
    """适配器返回值 → 文本（dict 取常见文本键；其他直接 str）"""
    if isinstance(out, dict):
        for key in ("text", "content", "output", "response"):
            if out.get(key):
                return str(out[key])
        return json.dumps(out, ensure_ascii=False, default=str)
    return str(out)


class LLMJudge:
    """真实 LLM-judge（§4.5 层③ 的"软性 ≥0.85"；M1）

    - 真模型调用：``invoke`` 可注入（测试/自有通道），缺省走 `ModelAdapterFactory`；
    - **可用性如实**：适配器不可用或调用抛错 ⇒ `JudgeUnavailable`，
      由 `resolve_judge()` 回落到确定性打分器并**标注回落原因**（绝不静默冒充 LLM）；
    - judge 自身的 token/耗时计入 `usage`（shadow_overhead，S2-03 字段）。
    """

    def __init__(self, *, invoke: Optional[Callable[[str], str]] = None,
                 adapter: Any = None, provider: str = "", model: str = "",
                 threshold: float = JUDGE_THRESHOLD) -> None:
        self.provider = str(provider or "")
        self.model = str(model or "")
        self.threshold = float(threshold)
        self._invoke = invoke
        self._adapter = adapter
        self._unavailable = ""
        self.calls = 0
        self.usage: Dict[str, Any] = {"prompt_chars": 0, "reply_chars": 0}

    # ── 可用性 ──────────────────────────────────────────────

    @property
    def unavailable_reason(self) -> str:
        return self._unavailable

    def _resolve_invoke(self) -> Callable[[str], str]:
        """解析出真正可用的调用通道（**不做探针式模型调用**：只查适配器可用性）"""
        if self._invoke is not None:
            return self._invoke
        adapter = self._adapter
        if adapter is None:
            try:
                from agent.model_router.adapters import ModelAdapterFactory
            except Exception as e:  # noqa: BLE001
                raise JudgeUnavailable(f"模型适配层不可导入: {e}") from e
            if not (self.provider or self.model):
                raise JudgeUnavailable("未配置 provider/model（"
                                       f"{JUDGE_PROVIDER_ENV} / {JUDGE_MODEL_ENV}）")
            try:
                adapter = ModelAdapterFactory.create(self.provider, self.model)
            except Exception as e:  # noqa: BLE001
                raise JudgeUnavailable(
                    f"模型适配器构造失败: {type(e).__name__}: {e}") from e
        if adapter is None:
            raise JudgeUnavailable("未能构造模型适配器（provider/model 未知）")
        try:
            if not adapter.is_available():
                raise JudgeUnavailable(
                    f"模型适配器不可用（provider={self.provider or 'default'} "
                    f"model={self.model or 'default'}：缺凭证或依赖）")
        except JudgeUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            raise JudgeUnavailable(f"适配器可用性探测失败: {type(e).__name__}: {e}") from e
        self._adapter = adapter

        def _invoke(prompt: str) -> str:
            try:
                out = adapter.generate(prompt)
            except Exception as e:  # noqa: BLE001
                raise JudgeUnavailable(f"模型调用失败: {type(e).__name__}: {e}") from e
            return _extract_reply(out)

        self._invoke = _invoke
        return _invoke

    def is_available(self) -> bool:
        """**通道可用性**探测（只解析调用通道，不发起模型调用 —— 不做探针浪费）

        真正的端到端探针由 `probe()` 负责（`ShadowRunner` 在灰度开始前调用一次），
        因为"能构造出通道"不等于"模型真的答得上来"。
        """
        if self._unavailable:
            return False
        try:
            self._resolve_invoke()
        except JudgeUnavailable as e:
            self._unavailable = str(e)
            return False
        except Exception as e:  # noqa: BLE001
            self._unavailable = f"{type(e).__name__}: {e}"
            return False
        return True

    def probe(self) -> Dict[str, Any]:
        """端到端探针（一次真实调用；失败原因如实返回，不抛）"""
        if not self.is_available():
            return {"ok": False, "reason": self._unavailable}
        try:
            result = self.score("probe: equal texts", "probe: equal texts")
        except JudgeUnavailable as e:
            self._unavailable = str(e)
            return {"ok": False, "reason": str(e)}
        except Exception as e:  # noqa: BLE001
            self._unavailable = f"{type(e).__name__}: {e}"
            return {"ok": False, "reason": self._unavailable}
        return {"ok": True, "score": result["score"], "kind": JUDGE_KIND_LLM}

    # ── 打分 ────────────────────────────────────────────────

    def score(self, reference: str, observed: str) -> Dict[str, Any]:
        """返回 ``{"score": float, "kind": ..., "raw": ...}``；不可用/解析失败抛错"""
        if self._unavailable:
            raise JudgeUnavailable(self._unavailable)
        invoke = self._resolve_invoke()
        prompt = _JUDGE_PROMPT.format(reference=str(reference or ""),
                                      observed=str(observed or ""))
        self.calls += 1
        self.usage["prompt_chars"] += len(prompt)
        try:
            raw = invoke(prompt)
        except JudgeUnavailable:
            raise
        except Exception as e:  # noqa: BLE001  通道异常一律如实转为"不可用"
            raise JudgeUnavailable(f"模型调用失败: {type(e).__name__}: {e}") from e
        self.usage["reply_chars"] += len(str(raw or ""))
        score = parse_judge_score(raw)
        if score is None:
            raise JudgeUnavailable(f"judge 回复无法解析为分数: {str(raw)[:120]!r}")
        return {"score": score, "kind": JUDGE_KIND_LLM, "raw": str(raw)[:400],
                "provider": self.provider, "model": self.model}

    def __call__(self, reference: str, observed: str) -> float:
        return float(self.score(reference, observed)["score"])


@dataclass
class ResolvedJudge:
    """判定器解析结果（**实际所用**的 scorer 与其如实标注）

    ``detail`` **只放 JSON 可序列化字段**；活的 judge 对象在 ``judge`` 字段
    （不进报告序列化 —— 内部活数据一律不序列化）。
    """

    scorer: Callable[[str, str], float]
    kind: str
    mode: str
    detail: Dict[str, Any] = field(default_factory=dict)
    judge: Optional[LLMJudge] = None

    @property
    def is_llm(self) -> bool:
        return self.kind == JUDGE_KIND_LLM

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "mode": self.mode, "is_llm": self.is_llm,
                "detail": dict(self.detail)}


class JudgeGuard:
    """判定器守卫（M1：LLM 通道**失败即如实回落**，不把样本误判成负例）

    为什么需要它：若 LLM-judge 通道在灰度期中断（凭证过期/网络抖动），`diff_judge`
    会把每次 judge 异常都记成**层③失败** ⇒ 整批样本变负例 ⇒ 触发虚假的"劣化"信号。
    故本守卫在**首次失败**时切换到确定性打分器，并如实把 ``judge_kind`` 改成
    ``deterministic_local(llm_unavailable)``（附回落原因）。

    - `probe()`：灰度开始前一次端到端探针 ⇒ 标签在**样本之前**就是准的；
    - `__call__`：运行中若仍失败，同样回落（`fallbacks` 记录次数与原因）。
    """

    def __init__(self, primary: Callable[[str, str], float], *,
                 kind_primary: str, fallback: Optional[Callable[[str, str], float]] = None,
                 kind_fallback: str = JUDGE_KIND_LLM_FALLBACK) -> None:
        self._primary = primary
        self._fallback = fallback or judge_similarity
        self.kind_primary = str(kind_primary)
        self.kind_fallback = str(kind_fallback)
        self.active = "primary"
        self.fallbacks = 0
        self.reasons: List[str] = []
        self.probe_result: Dict[str, Any] = {}

    # ── 标签 ────────────────────────────────────────────────

    @property
    def effective_kind(self) -> str:
        return self.kind_primary if self.active == "primary" else self.kind_fallback

    def _fall_back(self, why: str) -> None:
        if self.active != "fallback":
            self.active = "fallback"
            self.reasons.append(why)
            logger.warning("judge 回落确定性打分器：%s", why)
        self.fallbacks += 1

    # ── 探针与调用 ──────────────────────────────────────────

    def probe(self) -> Dict[str, Any]:
        """灰度开始前的一次端到端探针（失败即回落，返回如实标签与原因）"""
        if self.active == "fallback" or not (self.kind_primary == JUDGE_KIND_LLM):
            self.probe_result = {"ok": self.active == "primary",
                                 "kind": self.effective_kind,
                                 "reason": "" if self.active == "primary" else "已回落"}
            return self.probe_result
        try:
            self._primary("probe: equal", "probe: equal")
        except Exception as e:  # noqa: BLE001  LLM 通道任何失败都回落
            self._fall_back(f"judge 探针失败: {type(e).__name__}: {e}")
            self.probe_result = {"ok": False, "kind": self.effective_kind,
                                 "reason": self.reasons[-1]}
            return self.probe_result
        self.probe_result = {"ok": True, "kind": self.effective_kind, "reason": ""}
        return self.probe_result

    def __call__(self, reference: str, observed: str) -> float:
        if self.active == "fallback":
            return float(self._fallback(reference, observed))
        try:
            return float(self._primary(reference, observed))
        except Exception as e:  # noqa: BLE001  运行期失败同样如实回落
            self._fall_back(f"judge 调用失败: {type(e).__name__}: {e}")
            return float(self._fallback(reference, observed))

    def to_dict(self) -> Dict[str, Any]:
        return {"effective_kind": self.effective_kind, "primary_kind": self.kind_primary,
                "active": self.active, "fallbacks": self.fallbacks,
                "reasons": list(self.reasons), "probe": dict(self.probe_result)}


def resolve_judge(mode: str = "", *, invoke: Optional[Callable[[str], str]] = None,
                  judge: Optional[Callable[[str, str], float]] = None,
                  provider: str = "", model: str = "",
                  env: Optional[Dict[str, str]] = None) -> ResolvedJudge:
    """判定器解析（M1 的"可区分"落到这里）

    优先级：显式 ``judge=`` > ``mode``（env ``CP_DIGESTION_JUDGE``，默认 ``auto``）。
    ``auto`` = 先试 LLM-judge，不可用则**如实回落**确定性打分器并标注
    ``deterministic_local(llm_unavailable)``。
    """
    if judge is not None:
        return ResolvedJudge(scorer=judge, kind=JUDGE_KIND_INJECTED, mode="injected",
                             detail={"note": "调用方显式注入的判定器"})
    env_map = _env(env)
    resolved_mode = str(mode or env_map.get(JUDGE_MODE_ENV) or JUDGE_MODE_AUTO).strip().lower()
    if resolved_mode not in JUDGE_MODES:
        logger.warning("%s=%r 非法，回退 %s", JUDGE_MODE_ENV, resolved_mode, JUDGE_MODE_AUTO)
        resolved_mode = JUDGE_MODE_AUTO
    if resolved_mode == JUDGE_MODE_LOCAL:
        return ResolvedJudge(scorer=judge_similarity, kind=JUDGE_KIND_LOCAL,
                             mode=resolved_mode,
                             detail={"note": "显式要求本地确定性打分器"})
    provider = provider or str(env_map.get(JUDGE_PROVIDER_ENV) or "")
    model = model or str(env_map.get(JUDGE_MODEL_ENV) or "")
    llm = LLMJudge(invoke=invoke, provider=provider, model=model)
    if resolved_mode == JUDGE_MODE_LLM:
        if llm.is_available():
            return ResolvedJudge(scorer=llm, kind=JUDGE_KIND_LLM, mode=resolved_mode,
                                 detail={"provider": provider, "model": model},
                                 judge=llm)
        return ResolvedJudge(scorer=judge_similarity, kind=JUDGE_KIND_LLM_FALLBACK,
                             mode=resolved_mode,
                             detail={"unavailable_reason": llm.unavailable_reason,
                                     "provider": provider, "model": model,
                                     "note": "显式要求 LLM-judge 但不可用 ⇒ 如实回落"
                                             "确定性打分器（不冒充 LLM）"})
    if llm.is_available():
        return ResolvedJudge(scorer=llm, kind=JUDGE_KIND_LLM, mode=resolved_mode,
                             detail={"provider": provider, "model": model},
                             judge=llm)
    return ResolvedJudge(scorer=judge_similarity, kind=JUDGE_KIND_LLM_FALLBACK,
                         mode=resolved_mode,
                         detail={"unavailable_reason": llm.unavailable_reason,
                                 "note": "auto 模式：LLM-judge 不可用 ⇒ 回落确定性"
                                         "打分器（judge_kind 如实标注）"})


# ════════════════════════════════════════════════════════════
#  人工抽检队列（M5：10% 抽检 + **实际复核留痕**）
# ════════════════════════════════════════════════════════════


@dataclass
class ManualReviewItem:
    """一条人工抽检项（S3-02 只产出清单；本任务**记录复核动作**）"""

    case_id: str
    capability_id: str = ""
    sample_id: str = ""
    candidate_kind: str = ""
    reasons: List[str] = field(default_factory=list)
    queued_at: float = 0.0
    queued_by: str = ""
    verdict: str = ""
    reviewer: str = ""
    role: str = ""
    note: str = ""
    reviewed_at: float = 0.0

    @property
    def decided(self) -> bool:
        return self.verdict in REVIEW_VERDICTS

    @property
    def approved(self) -> bool:
        return self.verdict == REVIEW_VERDICT_PASS

    def to_dict(self) -> Dict[str, Any]:
        return {"case_id": self.case_id, "capability_id": self.capability_id,
                "sample_id": self.sample_id, "candidate_kind": self.candidate_kind,
                "reasons": list(self.reasons), "queued_at": self.queued_at,
                "queued_by": self.queued_by, "verdict": self.verdict,
                "reviewer": self.reviewer, "role": self.role, "note": self.note,
                "reviewed_at": self.reviewed_at, "decided": self.decided}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ManualReviewItem":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in dict(data or {}).items() if k in known})


class ManualReviewQueue:
    """人工抽检队列（JSONL 落盘；**同 (能力, 用例) 以最后一条为准**）

    M5 的口径："人工复核**未完成前不得视为已验收**" —— 故 `summary()["closed"]`
    为假时，调用方（`ShadowReport`/内化决策）一律标注 `manual_review_incomplete`，
    绝不把"抽检清单已产出"说成"已人工复核"。
    """

    def __init__(self, path: str = "", *, directory: str = "",
                 filename: str = MANUAL_REVIEW_FILENAME) -> None:
        base = str(directory or os.getenv(SHADOW_DIR_ENV) or DEFAULT_SHADOW_DIR)
        self.dir = base
        self.path = str(path or os.path.join(base, filename))
        # **构造期不建目录**（只读调用方不应产生文件系统副作用）；
        # 写入时经 `_ensure_dir()` 惰性创建。

    def _ensure_dir(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    # ── 写入 ────────────────────────────────────────────────

    def _append(self, item: ManualReviewItem, kind: str) -> None:
        row = item.to_dict()
        row["kind"] = kind
        try:
            self._ensure_dir()
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except OSError as e:  # 落盘失败不得中断灰度
            logger.warning("人工抽检台账写入失败（advisory）: %s", e)

    def enqueue(self, capability_id: str, case_ids: Iterable[str], *,
                candidate_kind: str = "", reasons: Optional[Dict[str, List[str]]] = None,
                queued_by: str = "shadow_runner") -> List[ManualReviewItem]:
        """把 10% 抽中的用例入队（**幂等**：已入队且未裁定的不重复追加）"""
        existing = {(i.capability_id, i.case_id) for i in self.items()
                    if not i.decided}
        out: List[ManualReviewItem] = []
        for case_id in case_ids or []:
            cid = str(case_id)
            if (str(capability_id), cid) in existing:
                continue
            item = ManualReviewItem(
                case_id=cid, capability_id=str(capability_id), sample_id=cid,
                candidate_kind=normalize_candidate_kind(candidate_kind),
                reasons=list((reasons or {}).get(cid) or []),
                queued_at=time.time(), queued_by=str(queued_by or ""))
            self._append(item, "queued")
            out.append(item)
        return out

    def record_review(self, case_id: str, *, capability_id: str, verdict: str,
                      reviewer: str, role: str = REVIEW_ROLE_HUMAN,
                      note: str = "") -> ManualReviewItem:
        """记录**人工复核动作**（M5 的核心；结论 + 复核人 + 角色 + 时间落盘并审计）

        Raises:
            ValueError: 非法结论（只允许 pass/fail/uncertain）
        """
        resolved = str(verdict or "").strip().lower()
        if resolved not in REVIEW_VERDICTS:
            raise ValueError(f"非法复核结论 {verdict!r}（允许 {REVIEW_VERDICTS}）")
        item = ManualReviewItem(
            case_id=str(case_id), capability_id=str(capability_id),
            sample_id=str(case_id), verdict=resolved,
            reviewer=str(reviewer or ""), role=str(role or REVIEW_ROLE_HUMAN),
            note=str(note or ""), reviewed_at=time.time())
        self._append(item, "reviewed")
        _audit(AUDIT_ACTION_REVIEWED, capability_id=str(capability_id),
               payload={"case_id": str(case_id), "verdict": resolved,
                        "reviewer": str(reviewer or ""), "role": item.role,
                        "note": str(note or "")},
               status="reviewed", actor=str(reviewer or "reviewer"))
        return item

    # ── 读取 ────────────────────────────────────────────────

    def rows(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            return out
        return out

    def items(self, capability_id: str = "") -> List[ManualReviewItem]:
        """当前状态（同 (能力, 用例) 以最后一条为准；按入队时间排序）"""
        latest: Dict[Tuple[str, str], ManualReviewItem] = {}
        for row in self.rows():
            item = ManualReviewItem.from_dict(row)
            if capability_id and item.capability_id != str(capability_id):
                continue
            latest[(item.capability_id, item.case_id)] = item
        return sorted(latest.values(), key=lambda i: (i.queued_at, i.case_id))

    def pending(self, capability_id: str = "") -> List[ManualReviewItem]:
        return [i for i in self.items(capability_id) if not i.decided]

    def summary(self, capability_id: str = "") -> Dict[str, Any]:
        """抽检状态摘要（``closed`` 为假 ⇒ 不得视为已验收）"""
        items = self.items(capability_id)
        decided = [i for i in items if i.decided]
        by_verdict: Dict[str, int] = {}
        for item in decided:
            by_verdict[item.verdict] = by_verdict.get(item.verdict, 0) + 1
        human = [i for i in decided if i.role == REVIEW_ROLE_HUMAN]
        return {
            "sampled": len(items),
            "pending": len(items) - len(decided),
            "decided": len(decided),
            "by_verdict": by_verdict,
            "human_reviewed": len(human),
            "agent_assisted_reviewed": len(decided) - len(human),
            "closed": bool(items) and not [i for i in items if not i.decided],
            "pending_case_ids": sorted(i.case_id for i in items if not i.decided),
            "path": self.path,
            "note": ("人工复核未完成前不得视为已验收（M5 口径）"
                     if [i for i in items if not i.decided] else "抽检全部已裁定"),
        }

    def is_closed(self, capability_id: str = "") -> bool:
        return bool(self.summary(capability_id).get("closed"))

    def review_sheet(self, capability_id: str = "") -> str:
        """人工复核工作表（Markdown；把清单交给 Owner 逐条裁定 —— 不是走过场）"""
        items = self.items(capability_id)
        lines = [f"# 人工抽检复核表 — {capability_id or '(全部)'}", "",
                 f"抽检项 {len(items)} 条（10% 确定性抽样，可复现）。",
                 "复核口径（M5）：逐条判断候选实现与上游是否**行为等价**；",
                 "未裁定前该能力**不得视为已验收**。", "",
                 "| # | case_id | 抽样键 | 候选 | 待核理由 | 结论 | 复核人 |",
                 "|---|---|---|---|---|---|---|"]
        for index, item in enumerate(items, 1):
            lines.append("| {} | `{}` | {} | {} | {} | {} | {} |".format(
                index, item.case_id, item.sample_id or "-",
                item.candidate_kind or "-",
                "；".join(item.reasons) or "-",
                item.verdict or "**待裁定**", item.reviewer or "-"))
        return "\n".join(lines) + "\n"


# ════════════════════════════════════════════════════════════
#  灰度台账（抽样历史 ⇒ 日均预算的输入）
# ════════════════════════════════════════════════════════════


class ShadowLedger:
    """shadow 观测台账（JSONL；一行一次灰度运行/一条样本）"""

    def __init__(self, path: str = "", *, directory: str = "",
                 filename: str = SHADOW_LEDGER_FILENAME) -> None:
        base = str(directory or os.getenv(SHADOW_DIR_ENV) or DEFAULT_SHADOW_DIR)
        self.dir = base
        self.path = str(path or os.path.join(base, filename))
        # 构造期不建目录（与 `ManualReviewQueue` 同纪律）；写入时惰性创建

    def record(self, report: "ShadowReport") -> Dict[str, Any]:
        row = {"kind": "shadow_run", "capability_id": report.capability_id,
               "generated_at": report.generated_at, "allowed": report.allowed,
               "budget": report.plan.budget, "sampled": len(report.samples),
               "passed": report.passed, "negative": report.negative,
               "judge_kind": report.judge_kind,
               "degradation": report.degradation.get("verdict", ""),
               "p99_wall_candidate_ms": report.p99_wall_candidate_ms(),
               "p99_wall_upstream_ms": report.p99_wall_upstream_ms(),
               "shadow_version": SHADOW_VERSION}
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except OSError as e:  # advisory
            logger.warning("灰度台账写入失败（advisory）: %s", e)
        return row

    def rows(self, capability_id: str = "") -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if capability_id and row.get("capability_id") != capability_id:
                        continue
                    out.append(row)
        except OSError:
            return out
        return out

    def daily_counts(self, *, days: int = 7,
                     capability_id: str = "") -> Dict[str, int]:
        """按日聚合的**执行样本数**（抽样宇宙规模的历史代理量）"""
        buckets: Dict[str, int] = {}
        for row in self.rows(capability_id):
            day = time.strftime("%Y-%m-%d", time.localtime(float(row.get("generated_at") or 0.0)))
            buckets[day] = buckets.get(day, 0) + int(row.get("sampled") or 0)
        ordered = sorted(buckets.items())[-max(1, int(days)):]
        return dict(ordered)

    def daily_average(self, *, days: int = 7, capability_id: str = "") -> float:
        counts = list(self.daily_counts(days=days, capability_id=capability_id).values())
        if not counts:
            return 0.0
        return round(sum(counts) / float(len(counts)), 4)


# ════════════════════════════════════════════════════════════
#  抽样计划与结果
# ════════════════════════════════════════════════════════════


@dataclass
class ShadowPlan:
    """一次灰度运行的抽样计划（**先计划后执行**：预算与抽样都可审计）"""

    capability_id: str = ""
    enabled: bool = False
    enabled_reason: str = ""
    budget: int = 0
    daily_avg: float = 0.0
    ratio: float = SHADOW_BUDGET_RATIO
    cap: int = SHADOW_BUDGET_CAP
    min_budget: int = SHADOW_MIN_BUDGET
    universe: List[str] = field(default_factory=list)
    sampled: List[str] = field(default_factory=list)
    gray: Dict[str, Any] = field(default_factory=dict)
    gray_routed: List[str] = field(default_factory=list)
    skipped_reason: str = ""
    #: S5-03 成本刹车降本系数（1.0 = 无影响；0.0 = 断食/熔断期归零）
    #: 记录它是为了让「为什么今天预算是 0」可解释（§1.5⑥ 不可做不可见之事）
    cost_factor: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {"capability_id": self.capability_id, "enabled": self.enabled,
                "enabled_reason": self.enabled_reason, "budget": self.budget,
                "daily_avg": self.daily_avg, "ratio": self.ratio, "cap": self.cap,
                "min_budget": self.min_budget, "universe": len(self.universe),
                "sampled": len(self.sampled), "sampled_ids": list(self.sampled),
                "gray": dict(self.gray), "gray_routed": list(self.gray_routed),
                "skipped_reason": self.skipped_reason,
                "cost_factor": self.cost_factor,
                "formula": ("min(日均 × ratio, cap)（低流量保底 min_budget）"
                            "× cost_factor（S5-03 成本刹车降本系数，1.0=无影响）")}


@dataclass
class ShadowSample:
    """一条灰度观测样本（双跑 + 三层比对 + **真实墙钟**）"""

    sample_id: str
    case_id: str
    trace_id: str = ""
    passed: bool = True
    negative: bool = False
    failed_layers: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    judge_score: float = 0.0
    wall_ms_candidate: float = 0.0
    wall_ms_upstream: float = 0.0
    model_clock_ms_candidate: float = 0.0
    model_clock_ms_upstream: float = 0.0
    manual_flagged: bool = False
    gray_routed: bool = False
    #: ── 真实接管（TASK-S8-03；默认全为"未请求"，不改变既有样本形态）──
    takeover: bool = False
    takeover_status: str = ""
    takeover_matched: bool = False
    takeover_level: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"sample_id": self.sample_id, "case_id": self.case_id,
                "trace_id": self.trace_id, "passed": self.passed,
                "negative": self.negative, "failed_layers": list(self.failed_layers),
                "reasons": list(self.reasons), "judge_score": self.judge_score,
                "wall_ms_candidate": self.wall_ms_candidate,
                "wall_ms_upstream": self.wall_ms_upstream,
                "model_clock_ms_candidate": self.model_clock_ms_candidate,
                "model_clock_ms_upstream": self.model_clock_ms_upstream,
                "manual_flagged": self.manual_flagged,
                "gray_routed": self.gray_routed,
                "takeover": self.takeover,
                "takeover_status": self.takeover_status,
                "takeover_matched": self.takeover_matched,
                "takeover_level": self.takeover_level}


def _p99(values: Sequence[float]) -> float:
    """p99（样本 <100 取最大值 —— 与 S3-02/S5 同口径：小样本不虚报分位）"""
    ordered = sorted(float(v) for v in values if v is not None)
    if not ordered:
        return 0.0
    if len(ordered) < 100:
        return round(ordered[-1], 3)
    idx = min(len(ordered) - 1, int(0.99 * len(ordered)))
    return round(ordered[idx], 3)


# ════════════════════════════════════════════════════════════
#  三层比对判定（`compare()` → `CompareVerdict`）
# ════════════════════════════════════════════════════════════


@dataclass
class CompareVerdict:
    """§4.5 三层比对的**判定结论**（`compare()` 的返回类型）

    | 层 | 权重 | 内容 |
    |---|---|---|
    | `structure` | **硬性** | 输出 schema（键/类型/列表基数 + 用例声明契约） |
    | `side_effects` | **硬性** | 副作用具体目标 + 内容指纹 + 用例契约 |
    | `judge` | 软性 | judge 相似度 ≥0.85（`judge_kind` 如实标注实际判定器） |

    **任一层失败 ⇒ `passed=False` ⇒ 该样本记负例**（供 R4 劣化检测）。
    人工抽检（10%）由 `manual_flagged` 标记，进入 `ManualReviewQueue` 复核。
    """

    case_id: str = ""
    capability_id: str = ""
    passed: bool = True
    failed_layers: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    layers: List[Dict[str, Any]] = field(default_factory=list)
    judge_score: float = 0.0
    judge_kind: str = ""
    manual_flagged: bool = False
    clock: str = CLOCK_WALL
    wall_ms_candidate: float = 0.0
    wall_ms_upstream: float = 0.0
    model_clock_ms_candidate: float = 0.0
    model_clock_ms_upstream: float = 0.0

    @property
    def negative(self) -> bool:
        """负例（供 R4"学错能力"劣化检测与降级/回退）"""
        return not self.passed

    @property
    def hard_layer_failures(self) -> List[str]:
        return [name for name in self.failed_layers
                if name in (LAYER_STRUCTURE, LAYER_SIDE_EFFECTS)]

    def to_dict(self) -> Dict[str, Any]:
        return {"case_id": self.case_id, "capability_id": self.capability_id,
                "passed": self.passed, "negative": self.negative,
                "failed_layers": list(self.failed_layers),
                "hard_layer_failures": self.hard_layer_failures,
                "reasons": list(self.reasons), "layers": list(self.layers),
                "judge_score": self.judge_score, "judge_kind": self.judge_kind,
                "manual_flagged": self.manual_flagged, "clock": self.clock,
                "wall_ms_candidate": self.wall_ms_candidate,
                "wall_ms_upstream": self.wall_ms_upstream,
                "model_clock_ms_candidate": self.model_clock_ms_candidate,
                "model_clock_ms_upstream": self.model_clock_ms_upstream}

    @staticmethod
    def of(replay: Any, *, judge_kind: str = "") -> "CompareVerdict":
        """由一次双跑结果构造判定（两层硬性 + 一层软性 + 真实墙钟）"""
        judge_layer = replay.diff.layer(LAYER_JUDGE)
        kind = str((getattr(judge_layer, "detail", {}) or {}).get("judge_kind")
                   or judge_kind or "")
        return CompareVerdict(
            case_id=str(replay.case_id),
            capability_id=str(replay.capability_id),
            passed=bool(replay.passed),
            failed_layers=list(replay.diff.failed_layers),
            reasons=list(replay.failures()),
            layers=[layer.to_dict() for layer in replay.diff.layers],
            judge_score=float(getattr(judge_layer, "score", 0.0) or 0.0),
            judge_kind=kind,
            manual_flagged=bool(
                (getattr(judge_layer, "detail", {}) or {}).get("manual_review_flagged")),
            wall_ms_candidate=float(getattr(replay.candidate, "wall_ms", 0.0) or 0.0),
            wall_ms_upstream=float(getattr(replay.upstream, "wall_ms", 0.0) or 0.0),
            model_clock_ms_candidate=float(replay.candidate.duration_ms or 0.0),
            model_clock_ms_upstream=float(replay.upstream.duration_ms or 0.0))


def compare(case: EquivalenceCase, candidate: Any, *,
            upstream: Any = None, sandbox: Optional[ReplaySandbox] = None,
            judge: Optional[Callable[[str, str], float]] = None,
            judge_kind: str = "", manual_flagged: bool = False) -> CompareVerdict:
    """**三层比对流水线**（§4.5 步骤 2 的对外入口）

    复用 S3-02 的 `ReplaySandbox` 双跑（**不自建第二套回放**）；候选执行发生在
    进程内确定性模型中，副作用只记录不双写。真实墙钟由 `measure_wall=True` 采集。
    """
    box = sandbox or ReplaySandbox(judge=judge, measure_wall=True,
                                   judge_kind=judge_kind)
    replay = box.replay_case(case, candidate, upstream=upstream,
                            manual_flagged=manual_flagged, measure_wall=True)
    return CompareVerdict.of(replay, judge_kind=judge_kind)


# ════════════════════════════════════════════════════════════
#  劣化检测（R4：shadow 期持续劣化 → 降级/回退建议）
# ════════════════════════════════════════════════════════════


def assess_degradation(samples: Sequence[ShadowSample], *,
                       window: int = DEGRADE_WINDOW,
                       floor: float = DEGRADE_RATE_FLOOR,
                       consecutive: int = DEGRADE_CONSECUTIVE,
                       min_samples: int = DEGRADE_MIN_SAMPLES) -> Dict[str, Any]:
    """劣化判定（负例 → 降级/回退建议；**样本不足时不下结论**）

    - 样本 < ``min_samples`` ⇒ ``insufficient_samples``（action=observe）——
      不肯在 3 个样本上宣布"稳定"或"劣化"；
    - 最近 ``window`` 条通过率 < ``floor`` 或**连续负例** ≥ ``consecutive``
      ⇒ ``degraded``（action=recommend_degrade，供 stage 回退/关灰度决策）；
    - 否则 ``stable``。
    """
    total = len(samples or [])
    window_rows = list(samples or [])[-max(1, int(window)):]
    passed = sum(1 for s in window_rows if s.passed)
    rate = round(passed / len(window_rows), 4) if window_rows else 0.0
    run = 0
    tail = 0
    for sample in reversed(samples or []):
        if sample.passed:
            break
        tail += 1
    run = tail
    reasons: List[str] = []
    if total < int(min_samples):
        verdict = DEGRADE_VERDICT_INSUFFICIENT
        action = DEGRADE_ACTION_OBSERVE
        reasons.append(f"样本 {total} < {min_samples}：不足以判定劣化（继续观察）")
    elif rate < float(floor) or run >= int(consecutive):
        verdict = DEGRADE_VERDICT_DEGRADED
        action = DEGRADE_ACTION_ROLLBACK
        if rate < float(floor):
            reasons.append(f"最近 {len(window_rows)} 条通过率 {rate} < 下限 {floor}")
        if run >= int(consecutive):
            reasons.append(f"连续负例 {run} 条 ≥ {consecutive}"
                           "（疑似『学错能力』R4 防护触发）")
    else:
        verdict = DEGRADE_VERDICT_STABLE
        action = DEGRADE_ACTION_OBSERVE
        reasons.append(f"最近 {len(window_rows)} 条通过率 {rate} ≥ 下限 {floor}，"
                       f"无连续负例 ≥ {consecutive}")
    return {"verdict": verdict, "action": action, "total": total,
            "window": len(window_rows), "pass_rate": rate, "floor": float(floor),
            "consecutive_negatives": run, "consecutive_threshold": int(consecutive),
            "min_samples": int(min_samples), "reasons": reasons,
            "note": ("劣化仅产出**降级/回退建议**；真正的 stage 回退由人工/治理通道执行"
                     if verdict == DEGRADE_VERDICT_DEGRADED else "")}


# ════════════════════════════════════════════════════════════
#  shadow 报告
# ════════════════════════════════════════════════════════════


@dataclass
class ShadowReport:
    """一次灰度运行的完整结果（可 JSON 序列化；供报告/内化引擎/S6 面板消费）"""

    capability_id: str = ""
    allowed: bool = True
    blocked_reasons: List[str] = field(default_factory=list)
    plan: ShadowPlan = field(default_factory=ShadowPlan)
    passport: Dict[str, Any] = field(default_factory=dict)
    passport_reasons: List[str] = field(default_factory=list)
    samples: List[ShadowSample] = field(default_factory=list)
    judge: Dict[str, Any] = field(default_factory=dict)
    candidate_kind: str = ""
    applicability: Dict[str, Any] = field(default_factory=dict)
    manual_sample: List[str] = field(default_factory=list)
    manual_review: Dict[str, Any] = field(default_factory=dict)
    layer_failures: Dict[str, int] = field(default_factory=dict)
    degradation: Dict[str, Any] = field(default_factory=dict)
    overhead: Dict[str, Any] = field(default_factory=dict)
    quality_patch: Dict[str, Any] = field(default_factory=dict)
    isolation: Dict[str, Any] = field(default_factory=dict)
    #: 真实接管报告（TASK-S8-03；默认关闭时只有"未开启"的理由，无副作用）
    takeover: Dict[str, Any] = field(default_factory=dict)
    generated_at: float = 0.0
    event_id: str = ""
    audit_seq: int = 0
    audit_hash: str = ""

    # ── 汇总 ────────────────────────────────────────────────

    @property
    def total(self) -> int:
        return len(self.samples)

    @property
    def passed(self) -> int:
        return sum(1 for s in self.samples if s.passed)

    @property
    def negative(self) -> int:
        return sum(1 for s in self.samples if s.negative)

    @property
    def pass_rate(self) -> float:
        return round(self.passed / self.total, 4) if self.total else 0.0

    @property
    def judge_kind(self) -> str:
        return str(self.judge.get("kind") or "")

    @property
    def judge_is_llm(self) -> bool:
        return self.judge_kind == JUDGE_KIND_LLM

    def negative_samples(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self.samples if s.negative]

    def p99_wall_candidate_ms(self) -> float:
        return _p99([s.wall_ms_candidate for s in self.samples])

    def p99_wall_upstream_ms(self) -> float:
        return _p99([s.wall_ms_upstream for s in self.samples])

    def p99_model_candidate_ms(self) -> float:
        return _p99([s.model_clock_ms_candidate for s in self.samples])

    def p99_model_upstream_ms(self) -> float:
        return _p99([s.model_clock_ms_upstream for s in self.samples])

    def manual_review_closed(self) -> bool:
        return bool(self.manual_review.get("closed"))

    def to_dict(self, *, include_samples: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "capability_id": self.capability_id,
            "allowed": self.allowed,
            "blocked_reasons": list(self.blocked_reasons),
            "plan": self.plan.to_dict(),
            "passport_id": str(self.passport.get("passport_id") or ""),
            "passport_reasons": list(self.passport_reasons),
            "candidate_kind": self.candidate_kind,
            "applicability": dict(self.applicability),
            "judge": dict(self.judge),
            "judge_kind": self.judge_kind,
            "judge_is_llm": self.judge_is_llm,
            "total": self.total, "passed": self.passed, "negative": self.negative,
            "pass_rate": self.pass_rate,
            "layer_failures": dict(self.layer_failures),
            "manual_sample": list(self.manual_sample),
            "manual_review": dict(self.manual_review),
            "manual_review_closed": self.manual_review_closed(),
            "degradation": dict(self.degradation),
            "overhead": dict(self.overhead),
            "quality_patch": dict(self.quality_patch),
            "isolation": dict(self.isolation),
            "takeover": dict(self.takeover),
            "clock": CLOCK_WALL,
            "model_clock": CLOCK_MODEL,
            "p99_wall_candidate_ms": self.p99_wall_candidate_ms(),
            "p99_wall_upstream_ms": self.p99_wall_upstream_ms(),
            "p99_model_candidate_ms": self.p99_model_candidate_ms(),
            "p99_model_upstream_ms": self.p99_model_upstream_ms(),
            "negative_samples": self.negative_samples(),
            "generated_at": self.generated_at,
            "shadow_version": SHADOW_VERSION,
            "event_id": self.event_id,
            "audit_seq": self.audit_seq, "audit_hash": self.audit_hash,
        }
        if include_samples:
            payload["samples"] = [s.to_dict() for s in self.samples]
        return payload

    def markdown(self) -> str:
        """人类可读摘要（验收报告与 PR 描述复用；口径逐项标注）"""
        lines = [
            f"# shadow 灰度报告 — `{self.capability_id}`",
            "",
            f"- shadow 版本：{SHADOW_VERSION}｜允许运行：{self.allowed}"
            f"{'' if self.allowed else '（被阻断）'}",
            f"- 通行证：`{self.passport.get('passport_id') or '（无）'}`",
            f"- 抽样：宇宙 {len(self.plan.universe)} → 预算 {self.plan.budget} → 执行 {self.total}"
            f"（日均 {self.plan.daily_avg} × {self.plan.ratio} 上限 {self.plan.cap}）",
            f"- 候选身份：{self.candidate_kind or '-'}｜"
            f"适用性排除 {self.applicability.get('excluded', 0)} 条",
            f"- judge：`{self.judge_kind}`（LLM={self.judge_is_llm}）"
            f"｜门槛 {self.judge.get('threshold')}",
            f"- 结果：{self.passed}/{self.total} 通过（通过率 {self.pass_rate}）"
            f"｜负例 {self.negative}",
            f"- 时钟口径：{CLOCK_WALL}（模型时钟另列 "
            f"{self.p99_model_candidate_ms()}ms）",
            f"- 墙钟 p99：候选 {self.p99_wall_candidate_ms()}ms ≤ 上游 "
            f"{self.p99_wall_upstream_ms()}ms",
            f"- 人工抽检：{self.manual_review.get('sampled', 0)} 条，"
            f"待裁定 {self.manual_review.get('pending', 0)} 条，"
            f"闭合={self.manual_review_closed()}",
            f"- 劣化判定：{self.degradation.get('verdict')}"
            f"（action={self.degradation.get('action')}）",
            f"- 隔离：{self.isolation.get('mode')}"
            f"（生效等级={self.isolation.get('level')}，"
            f"容器隔离={self.isolation.get('container_isolated')}，"
            f"真实接管={self.isolation.get('real_takeover')}）",
        ]
        if self.takeover:
            lines.append(
                f"- 接管：{'开启' if self.takeover.get('enabled') else '关闭'}"
                f"（传输 `{self.takeover.get('transport')}`）"
                f"｜执行 {self.takeover.get('executed', 0)}"
                f"／一致 {self.takeover.get('matched', 0)}"
                f"／失败 {self.takeover.get('failed', 0)}"
                f"｜预算 {self.takeover.get('budget', 0)}"
                + (f"｜**已回落** `{self.takeover.get('fallback_transport')}`"
                   f"（事故卡 `{self.takeover.get('incident_id')}`）"
                   if self.takeover.get("fallback") else ""))
        if self.blocked_reasons:
            lines += ["", "**阻断理由**"] + [f"- {r}" for r in self.blocked_reasons]
        if self.negative:
            lines += ["", "**负例（供 R4 劣化检测）**"]
            for item in self.negative_samples()[:10]:
                lines.append(f"- `{item['case_id']}` 失败层 {item['failed_layers']}："
                             f"{'；'.join(item['reasons'][:2])}")
        return "\n".join(lines) + "\n"


# ════════════════════════════════════════════════════════════
#  事件与审计（复用 digest.stage；不新增事件类型）
# ════════════════════════════════════════════════════════════


def _emit_shadow_event(payload: Dict[str, Any], *, correlation_id: str,
                       idempotency_key: str) -> str:
    try:
        from agent.observability.events import EV_DIGEST_STAGE, emit, trace_fields
        fields = trace_fields()
        body = dict(payload)
        body.setdefault("workspace_id", fields.get("workspace_id", ""))
        body.setdefault("subject_id", fields.get("subject_id", ""))
        body.setdefault("trace_id", fields.get("trace_id", ""))
        envelope = emit(EV_DIGEST_STAGE, body, correlation_id=correlation_id,
                        idempotency_key=idempotency_key)
        return getattr(envelope, "event_id", "") or ""
    except Exception as e:  # noqa: BLE001  事件失败不得影响灰度结果
        logger.debug("digest.stage（shadow）事件发送失败: %s", e)
        return ""


def _audit(action: str, *, capability_id: str, payload: Dict[str, Any],
           status: str, actor: str) -> Tuple[int, str]:
    try:
        from agent.audit.facade import audit
        entry = audit.record(action, actor=actor,
                             subject=f"capability:{capability_id}",
                             payload=payload, source="agent", status=status,
                             technical={"shadow_version": SHADOW_VERSION})
        if entry is None:
            return 0, ""
        return (int(getattr(entry, "seq", 0) or 0),
                str(getattr(entry, "self_hash", "") or ""))
    except Exception as e:  # noqa: BLE001
        logger.debug("shadow 审计写入失败: %s", e)
        return 0, ""


# ════════════════════════════════════════════════════════════
#  ShadowRunner
# ════════════════════════════════════════════════════════════


def sample_id_for(case: EquivalenceCase) -> str:
    """用例的抽样键：trace 派生用溯源指针，Seed 用例用 ``case:<id>``"""
    return str(case.origin_trace_id or f"case:{case.case_id}")


class ShadowRunner:
    """shadow 灰度器（抽样 + 双跑 + 三层比对 + 劣化信号 + 人工抽检）

    用法::

        runner = ShadowRunner()                       # judge=auto，沙箱 measure_wall=True
        report = runner.run(capability_id, case_set=cs, candidate=pattern)
        report.to_dict()["p99_wall_candidate_ms"], report.markdown()
    """

    def __init__(self, *, sandbox: Optional[ReplaySandbox] = None,
                 judge: Optional[Callable[[str, str], float]] = None,
                 judge_kind: str = "", judge_mode: str = "",
                 judge_invoke: Optional[Callable[[str], str]] = None,
                 passport_store: Optional[PassportStore] = None,
                 case_store: Optional[CaseStore] = None,
                 ledger: Optional[ShadowLedger] = None,
                 review_queue: Optional[ManualReviewQueue] = None,
                 env: Optional[Dict[str, str]] = None,
                 emit_events: bool = True,
                 actor: str = "digestion_service",
                 isolation_level: str = "",
                 isolation_plan: Optional[IsolationPlan] = None,
                 isolation_executor: Any = None,
                 takeover_ledger: Optional[TakeoverLedger] = None,
                 incident_dir: str = "",
                 trace: Any = None,
                 trace_db: str = "") -> None:
        self.env = dict(env or {})
        self.emit_events = bool(emit_events)
        self.actor = str(actor or "digestion_service")
        #: ── 执行隔离（TASK-S8-03）──
        #: 显式等级 > 显式决议 > 环境变量/探测（**懒解析**：不构造期探测 Docker，
        #: 免得"只是想跑一次灰度"也付一次 `docker info` 的代价）
        self._isolation_level = str(isolation_level or "")
        self._isolation_plan = isolation_plan
        self._isolation_executor = isolation_executor
        self._takeover_ledger = takeover_ledger
        self.incident_dir = str(incident_dir or "")
        self.trace = trace
        self.trace_db = str(trace_db or "")
        resolved = resolve_judge(judge_mode, invoke=judge_invoke, judge=judge,
                                 env=self.env or None)
        if judge_kind:
            # 调用方给出**精确标签**（如 `deterministic_local(llm_unavailable)`）：
            # 覆盖推断值，使报告里的 judge_kind 与实际所用判定器**逐字一致**（M1）
            resolved = ResolvedJudge(scorer=resolved.scorer, kind=str(judge_kind),
                                     mode=resolved.mode, detail=resolved.detail,
                                     judge=resolved.judge)
        self.judge = resolved
        if sandbox is not None:
            self.sandbox = sandbox
            # 显式沙箱：尊重其 judge，但**如实标注**实际所用判定器
            if sandbox.judge is not None and judge is None:
                self.judge = ResolvedJudge(
                    scorer=sandbox.judge,
                    kind=(judge_kind or getattr(sandbox, "judge_kind", "")
                          or JUDGE_KIND_INJECTED),
                    mode="sandbox", detail={"note": "沙箱自带判定器"})
        else:
            self.sandbox = ReplaySandbox(
                judge=resolved.scorer, measure_wall=True,
                judge_kind=(judge_kind or resolved.kind))
        self._judge_kind = str(judge_kind or self.judge.kind)
        # **判定器守卫**：LLM 通道失败即如实回落（M1），标签在样本之前就准
        self.judge_guard = JudgeGuard(self.judge.scorer, kind_primary=self.judge.kind)
        if sandbox is None:
            self.sandbox.judge = self.judge_guard
        elif self.sandbox.judge is None:
            self.sandbox.judge = self.judge_guard
        self._passport_store = passport_store
        self._case_store = case_store
        self._ledger = ledger
        self._review_queue = review_queue

    # ── 依赖（懒加载；显式传入才读写运行时区） ──────────────

    @property
    def passport_store(self) -> PassportStore:
        if self._passport_store is None:
            self._passport_store = PassportStore()
        return self._passport_store

    @property
    def case_store(self) -> CaseStore:
        if self._case_store is None:
            self._case_store = open_case_store()
        return self._case_store

    @property
    def ledger(self) -> ShadowLedger:
        if self._ledger is None:
            self._ledger = ShadowLedger()
        return self._ledger

    @property
    def review_queue(self) -> ManualReviewQueue:
        if self._review_queue is None:
            self._review_queue = ManualReviewQueue()
        return self._review_queue

    # ── 执行隔离（TASK-S8-03；懒解析，不改回放通道）──────────

    @property
    def isolation_plan(self) -> IsolationPlan:
        """生效隔离决议（首次访问才探测 Docker；结果进报告与审计）"""
        if self._isolation_plan is None:
            requested = self._isolation_level or ""
            if not requested and self.sandbox.isolation_level != ISOLATION_IN_PROCESS:
                # 显式构造的沙箱已声明等级 ⇒ 尊重它（不重复探测、不覆盖）
                requested = self.sandbox.isolation_level
            self._isolation_plan = resolve_isolation_level(
                requested=requested, env=self.env or None)
            self.sandbox.isolation_level = self._isolation_plan.level
        return self._isolation_plan

    @property
    def takeover_ledger(self) -> TakeoverLedger:
        if self._takeover_ledger is None:
            self._takeover_ledger = TakeoverLedger()
        return self._takeover_ledger

    def takeover_policy(self, *, shadow_config: Optional[Dict[str, Any]] = None
                        ) -> Any:
        """接管策略（**默认关闭**；无隔离即拒绝）"""
        return resolve_takeover_policy(shadow_config=shadow_config,
                                       env=self.env or None,
                                       isolation=self.isolation_plan)

    def takeover_engine(self, *, shadow_config: Optional[Dict[str, Any]] = None
                        ) -> TakeoverEngine:
        """接管引擎（隔离执行器按生效等级取；**不隐式升/降级**）"""
        executor = self._isolation_executor
        if executor is None:
            from .isolation import executor_for
            executor = executor_for(self.isolation_plan, env=self.env or None)
        return TakeoverEngine(executor=executor, ledger=self.takeover_ledger,
                              env=self.env or None, incident_dir=self.incident_dir,
                              trace=self.trace, trace_db=self.trace_db,
                              emit=self.emit_events,
                              actor=f"{self.actor}.takeover")

    # ── 门禁：凭通行证放行 ──────────────────────────────────

    def passport_status(self, capability_id: str) -> Dict[str, Any]:
        """灰度放行的**唯一**依据：`PassportStore` 里的合法通行证

        复用 `stage.acceptance_passport_ok()` 的自洽校验（不重复实现门槛常量）；
        无证 / 证不符 ⇒ 拒绝（fail-closed）。**不自建开关绕过验收门。**
        """
        from .stage import ACCEPTANCE_PASSPORT_KEY, acceptance_passport_ok
        passport = self.passport_store.latest(capability_id) or {}
        ok, reasons = acceptance_passport_ok(
            capability_id, {ACCEPTANCE_PASSPORT_KEY: passport})
        return {"ok": bool(ok), "reasons": list(reasons), "passport": dict(passport),
                "passport_id": str(passport.get("passport_id") or ""),
                "source": "gate.PassportStore"}

    # ── 计划 ────────────────────────────────────────────────

    def plan(self, capability_id: str, *, sample_ids: Iterable[str] = (),
             daily_avg: Optional[float] = None,
             shadow_config: Optional[Dict[str, Any]] = None) -> ShadowPlan:
        """抽样计划：开关 → 预算 → 确定性抽样 → 灰度路由（全部先计划后执行）"""
        params = budget_from_env(self.env or None)
        enabled, reason = shadow_enabled(reason=True, env=self.env or None)
        avg = (float(daily_avg) if daily_avg is not None
               else self.ledger.daily_average(capability_id=capability_id))
        # S5-03 成本刹车联动：断食/日熔断期预算系数（未开启时恒为 1.0 = 零影响）
        factor = _cost_policy_factor(self.env or None)
        budget = daily_budget(avg, ratio=params["ratio"], cap=params["cap"],
                              min_budget=params["min_budget"], factor=factor)
        universe = sorted({str(s) for s in (sample_ids or [])})
        sampled = deterministic_sample(universe, budget)
        gray = resolve_gray_policy(shadow_config=shadow_config,
                                   env=self.env or None)
        routed = (gray_routed_ids(sampled, ratio=gray["ratio"])
                  if gray.get("enabled") else [])
        return ShadowPlan(capability_id=str(capability_id or ""), enabled=enabled,
                          enabled_reason=reason, budget=budget, daily_avg=round(avg, 4),
                          ratio=params["ratio"], cap=params["cap"],
                          min_budget=params["min_budget"], universe=universe,
                          sampled=sampled, gray=gray, gray_routed=routed,
                          cost_factor=float(factor))

    # ── 执行 ────────────────────────────────────────────────

    def run(self, capability_id: str, *,
            case_set: Optional[CaseSet] = None,
            cases: Optional[Sequence[EquivalenceCase]] = None,
            candidate: Any = None,
            trace_ids: Sequence[str] = (),
            daily_avg: Optional[float] = None,
            pattern: Any = None,
            candidate_kind: str = "",
            upstream_provider: Optional[Callable[[EquivalenceCase], Any]] = None,
            shadow_config: Optional[Dict[str, Any]] = None,
            registry: Any = None,
            write_quality: bool = False,
            write_ledger: bool = True,
            enqueue_manual: bool = True,
            force: bool = False,
            now: float = 0.0) -> ShadowReport:
        """执行一次灰度运行（**默认关闭**；``force=True`` = 调用方显式开启）

        返回 `ShadowReport`；任何"未运行"的原因都写在 ``blocked_reasons`` 里
        （不静默、不假装成功）。
        """
        report = ShadowReport(capability_id=str(capability_id or ""),
                              generated_at=float(now or time.time()))
        # 隔离决议：**先解析再声明**（声明必须描述本次实际会发生的事，不是愿望）
        # 【命名纪律】变量名刻意叫 `iso_plan` 而不是 `plan`：本方法下文中
        # `plan` 已被**抽样计划**（`ShadowPlan`）占用，同名会让最终声明拿到
        # 一个没有 `.level` 的对象、静默回落成 in_process —— 实现期实测踩到过。
        iso_plan = self.isolation_plan
        report.isolation = isolation_declaration(plan=iso_plan)
        # judge 探针：把有效标签定在**样本之前**（LLM 不可用 ⇒ 如实回落并标注）
        probe = self.judge_guard.probe()
        self.sandbox.judge_kind = self.judge_guard.effective_kind
        report.judge = self.judge.to_dict() | {
            "threshold": JUDGE_THRESHOLD,
            "kind": self.judge_guard.effective_kind,
            "effective_kind": self.judge_guard.effective_kind,
            "probe": probe, "guard": self.judge_guard.to_dict()}
        report.quality_patch = {}

        # ① 开关（默认关闭）
        enabled, reason = shadow_enabled(reason=True, env=self.env or None)
        if not enabled and not force:
            report.allowed = False
            report.blocked_reasons.append(
                f"shadow 未开启：{reason}（调用方显式 force=True 可单次开启）")
        # ② 通行证（凭 PassportStore；无证不放行）
        status = self.passport_status(report.capability_id)
        report.passport = status["passport"]
        report.passport_reasons = status["reasons"]
        if not status["ok"]:
            report.allowed = False
            report.blocked_reasons.append(
                "灰度未获通行证（§4.5 验收门是唯一入口）："
                + "；".join(status["reasons"]))
        # ③ 判定集
        resolved_set = case_set
        if resolved_set is None and cases is not None:
            resolved_set = CaseSet(capability_id=report.capability_id,
                                   cases=list(cases or []))
        if resolved_set is None:
            resolved_set = self.case_store.load(report.capability_id)
        if resolved_set is None:
            report.allowed = False
            report.blocked_reasons.append(
                "判定集缺失（须先由 cases 通道生成；无判定集即无灰度依据）")
        if not report.allowed:
            report.degradation = assess_degradation([])
            report.manual_review = self.review_queue.summary(report.capability_id)
            report.plan = self.plan(report.capability_id, sample_ids=(),
                                    daily_avg=daily_avg, shadow_config=shadow_config)
            report.takeover = self._blocked_takeover(shadow_config)
            if write_ledger:
                self.ledger.record(report)
            report.audit_seq, report.audit_hash = _audit(
                AUDIT_ACTION_BLOCKED, capability_id=report.capability_id,
                payload={"reasons": report.blocked_reasons,
                         "passport_id": report.passport.get("passport_id", "")},
                status="blocked", actor=self.actor)
            return report

        resolved_active = (resolved_set.active_cases()
                           if resolved_set is not None else [])
        active = list(resolved_active)
        candidate_kind = (str(candidate_kind or "").strip()
                          or self._candidate_kind(candidate))
        candidate_kind = normalize_candidate_kind(candidate_kind)
        report.candidate_kind = candidate_kind
        applicable, excluded = applicable_cases(active, candidate_kind)
        report.applicability = {
            "candidate_kind": candidate_kind,
            "active": len(active), "applicable": len(applicable),
            "excluded": len(excluded), "excluded_cases": excluded,
            "field": "EquivalenceCase.applicability（M4 显式字段）"}

        # ④ 抽样宇宙与计划
        if trace_ids:
            ids: List[str] = []
            for index, case in enumerate(applicable):
                ids.append(str(trace_ids[index % len(trace_ids)]))
            pairs = list(zip(ids, applicable))
        else:
            pairs = [(sample_id_for(case), case) for case in applicable]
        plan = self.plan(report.capability_id, sample_ids=[p[0] for p in pairs],
                         daily_avg=daily_avg, shadow_config=shadow_config)
        report.plan = plan
        selected = set(plan.sampled)
        chosen = [(sid, case) for sid, case in pairs if sid in selected]
        gray_set = set(plan.gray_routed)

        # ⑤ 10% 人工抽检（确定性；先定清单再执行，避免"事后挑样本"）
        manual = set(manual_sample_ids([case.case_id for _, case in chosen],
                                       ratio=self.sandbox.manual_ratio))
        report.manual_sample = sorted(manual)

        # ⑥ 逐样本双跑（沙箱；副作用只记录）
        replays: Dict[str, Any] = {}
        for sample_id, case in chosen:
            picked = self._resolve_candidate(candidate, case)
            upstream = (upstream_provider(case) if upstream_provider is not None
                        else None)
            replay = self.sandbox.replay_case(
                case, picked, upstream=upstream, manual_flagged=case.case_id in manual,
                measure_wall=True)
            replays[sample_id] = replay
            sample = self._sample_of(sample_id, case, replay,
                                     manual_flagged=case.case_id in manual,
                                     gray_routed=sample_id in gray_set)
            report.samples.append(sample)
            for layer in sample.failed_layers:
                report.layer_failures[layer] = report.layer_failures.get(layer, 0) + 1

        # ⑦ 人工抽检入队（M5：清单 + 待复核）
        if enqueue_manual and report.manual_sample:
            self.review_queue.enqueue(
                report.capability_id, report.manual_sample,
                candidate_kind=candidate_kind,
                reasons={s.case_id: (s.reasons or ["10% 确定性抽检"]) for s in report.samples
                         if s.case_id in set(report.manual_sample)},
                queued_by=self.actor)
        report.manual_review = self.review_queue.summary(report.capability_id)

        # ⑥.5 真实接管（TASK-S8-03；**默认关闭**、有预算、失败自动回落）
        takeover = self._run_takeover(
            report, shadow_config=shadow_config, daily_avg=daily_avg,
            chosen=chosen, gray_set=gray_set, replays=replays,
            candidate=candidate)
        report.takeover = takeover.to_dict()
        self._annotate_takeover(report, takeover)

        # ⑧ 劣化信号（R4）+ 开销 + 质量统计补丁
        report.degradation = assess_degradation(report.samples)
        report.judge["effective_kind"] = self.judge_guard.effective_kind
        report.judge["guard"] = self.judge_guard.to_dict()
        report.overhead = self._overhead(report)
        report.quality_patch = self._quality_patch(report)
        if write_quality and registry is not None:
            self.apply_quality(report, registry=registry)

        if write_ledger:
            self.ledger.record(report)

        # 隔离声明：**跑完才定稿**（本次到底接管没有、用的哪一档，跑完才知道）
        report.isolation = isolation_declaration(
            plan=iso_plan,
            real_takeover=bool(takeover.enabled and takeover.executed > 0),
            real_takeover_enabled=bool(takeover.enabled),
            executed=int(takeover.executed))

        correlation = f"shadow:{report.capability_id}:{int(report.generated_at)}"
        if self.emit_events:
            report.event_id = _emit_shadow_event(
                {"capability_id": report.capability_id, "from_stage": "shadow",
                 "to_stage": "shadow", "applied": False,
                 "verdict": ("shadow_degraded"
                             if report.degradation.get("verdict") == DEGRADE_VERDICT_DEGRADED
                             else "shadow_observed"),
                 "scope": EVENT_SCOPE_SHADOW,
                  "reasons": [f"judge_kind={report.judge_kind}",
                              f"pass_rate={report.pass_rate}",
                              f"isolation_level={report.isolation.get('level')}",
                              f"real_takeover={report.isolation.get('real_takeover')}",
                              f"takeover_fallback={report.takeover.get('fallback')}",
                              f"p99_wall_candidate_ms={report.p99_wall_candidate_ms()}",
                              f"p99_wall_upstream_ms={report.p99_wall_upstream_ms()}"],
                 "digest_run_id": report.passport.get("passport_id", ""),
                 "passport_id": report.passport.get("passport_id", ""),
                 "executed": report.total, "pass_rate": report.pass_rate,
                 "note": ("shadow 只记录不接管真实执行（" + TRANSPORT_SANDBOX_ONLY + "）"
                          if not report.isolation.get("real_takeover")
                          else ("接管在隔离边界内执行（等级 "
                                + str(report.isolation.get("level"))
                                + "），副作用只记录不双写"))},
                correlation_id=correlation,
                idempotency_key=f"{correlation}:{report.total}")
        action = (AUDIT_ACTION_DEGRADED
                  if report.degradation.get("verdict") == DEGRADE_VERDICT_DEGRADED
                  else AUDIT_ACTION_OBSERVED)
        report.audit_seq, report.audit_hash = _audit(
            action, capability_id=report.capability_id,
            payload={"passport_id": report.passport.get("passport_id", ""),
                     "judge_kind": report.judge_kind,
                     "total": report.total, "pass_rate": report.pass_rate,
                     "negative": report.negative,
                     "clock": CLOCK_WALL,
                     "isolation_level": report.isolation.get("level"),
                     "real_takeover": report.isolation.get("real_takeover"),
                     "takeover": {"enabled": report.takeover.get("enabled"),
                                  "executed": report.takeover.get("executed"),
                                  "failed": report.takeover.get("failed"),
                                  "fallback": report.takeover.get("fallback"),
                                  "incident_id": report.takeover.get("incident_id"),
                                  "adopted": report.takeover.get("adopted")},
                     "p99_wall_candidate_ms": report.p99_wall_candidate_ms(),
                     "p99_wall_upstream_ms": report.p99_wall_upstream_ms(),
                     "manual_review": report.manual_review,
                     "degradation": report.degradation},
            status=("degraded" if action == AUDIT_ACTION_DEGRADED else "observed"),
            actor=self.actor)
        return report

    @staticmethod
    def _annotate_takeover(report: ShadowReport,
                           takeover: TakeoverReport) -> None:
        """把接管结果回填到样本上（**"没跑"三态可分**：未请求 / 未抽样 / 已跑）

        刻意不把"未抽样"写成"成功"：那正是"看起来接管了"的起点。回放判定的
        `passed/negative` 字段**不因接管而改写**——接管只增列事实，不改判定口径。
        """
        by_sample = {a.sample_id: a for a in takeover.attempts}
        for sample in report.samples:
            attempt = by_sample.get(sample.sample_id)
            if attempt is not None:
                sample.takeover = True
                sample.takeover_status = attempt.status
                sample.takeover_matched = attempt.matched
                sample.takeover_level = attempt.level
                continue
            sample.takeover = False
            sample.takeover_level = takeover.level if takeover.enabled else ""
            sample.takeover_status = (TAKEOVER_STATUS_NOT_SAMPLED if takeover.enabled
                                      else TAKEOVER_STATUS_NOT_REQUESTED)

    # ── 真实接管（TASK-S8-03） ───────────────────────────────

    def _blocked_takeover(self, shadow_config: Optional[Dict[str, Any]]
                          ) -> Dict[str, Any]:
        """被阻断的灰度运行**不做接管**（但仍如实给出策略与理由）"""
        policy = self.takeover_policy(shadow_config=shadow_config)
        return TakeoverReport(capability_id="", policy=policy.to_dict(),
                              level=policy.isolation_level,
                              enabled=False, transport=TRANSPORT_SANDBOX_ONLY,
                              reasons=list(policy.reasons) + [
                                  "本次灰度被阻断（未获通行证/未开启/无判定集）"
                                  "⇒ 不执行接管"]).to_dict()

    def _candidate_steps(self, candidate: Any, case: EquivalenceCase) -> Any:
        """候选 → 步骤程序（与回放**同一解析路径**：`_resolve_candidate` +
        `as_implementation().steps_for()`），故两条通道跑的是同一个候选定义"""
        picked = self._resolve_candidate(candidate, case)
        implementation = as_implementation(picked, name="candidate")
        return implementation.steps_for(case)

    def _run_takeover(self, report: ShadowReport, *,
                      shadow_config: Optional[Dict[str, Any]],
                      daily_avg: Optional[float],
                      chosen: Sequence[Tuple[str, EquivalenceCase]],
                      gray_set: set,
                      replays: Dict[str, Any],
                      candidate: Any) -> TakeoverReport:
        """按策略执行真实接管（**默认关闭**；任何"没跑"都写进 reasons）

        三条纪律在本方法里落死：

        1. 只有**被灰度选中**（``gray_routed``）且在**预算内**的样本才接管；
        2. 接管执行一律经隔离执行器（等级来自 `isolation_plan`，`in_process`
           时策略层就已拒绝）；
        3. 比对对象是**同一候选**的回放观测（`replays`），不是上游——比的是
           "真实执行与回放是否一致"，这样"接管"才有可判定的意义。
        """
        policy = self.takeover_policy(shadow_config=shadow_config)
        engine = self.takeover_engine(shadow_config=shadow_config)
        case_map = {sample_id: case for sample_id, case in chosen}
        avg = (float(daily_avg) if daily_avg is not None
               else self.ledger.daily_average(capability_id=report.capability_id))
        return engine.run(
            report.capability_id, policy=policy,
            gray_routed=sorted(str(s) for s in gray_set),
            cases=case_map,
            candidate_for=lambda case: self._candidate_steps(candidate, case),
            replay_obs_for=lambda sample_id: getattr(
                replays.get(sample_id), "candidate", None),
            daily_avg=avg, isolation=self.isolation_plan)

    # ── 内部工具 ────────────────────────────────────────────

    @staticmethod
    def _candidate_kind(candidate: Any) -> str:
        """候选身份标签（与 `gate._candidate_kind` 同词表；M4 的匹配键）"""
        if candidate is None:
            return CANDIDATE_KIND_SEED_NATIVE
        if callable(candidate) and not hasattr(candidate, "run"):
            return "provider"
        name = getattr(candidate, "name", "")
        if name:
            from .cases import CANDIDATE_KIND_IMPLEMENTATION, CANDIDATE_KIND_SEP
            return f"{CANDIDATE_KIND_IMPLEMENTATION}{CANDIDATE_KIND_SEP}{name}"
        return normalize_candidate_kind(candidate)

    @staticmethod
    def _resolve_candidate(candidate: Any, case: EquivalenceCase) -> Any:
        if candidate is None:
            return seed_candidate_for(case)
        if callable(candidate) and not hasattr(candidate, "run"):
            return candidate(case)
        return candidate

    @staticmethod
    def _sample_of(sample_id: str, case: EquivalenceCase, replay: Any, *,
                   manual_flagged: bool, gray_routed: bool) -> ShadowSample:
        verdict = CompareVerdict.of(replay)
        return ShadowSample(
            sample_id=str(sample_id), case_id=case.case_id,
            trace_id=str(case.origin_trace_id or ""),
            passed=bool(verdict.passed),
            negative=bool(verdict.negative),
            failed_layers=list(verdict.failed_layers),
            reasons=list(verdict.reasons),
            judge_score=float(verdict.judge_score),
            wall_ms_candidate=float(verdict.wall_ms_candidate),
            wall_ms_upstream=float(verdict.wall_ms_upstream),
            model_clock_ms_candidate=float(verdict.model_clock_ms_candidate),
            model_clock_ms_upstream=float(verdict.model_clock_ms_upstream),
            manual_flagged=bool(manual_flagged),
            gray_routed=bool(gray_routed))

    def _overhead(self, report: ShadowReport) -> Dict[str, Any]:
        """shadow_overhead（S2-03 字段口径）：灰度自身的额外开销，**非业务成本**"""
        wall_total = round(sum(s.wall_ms_candidate + s.wall_ms_upstream
                               for s in report.samples), 3)
        judge = self.judge.judge or getattr(self.judge_guard, "_primary", None)
        judge_usage = dict(getattr(judge, "usage", {}) or {})
        return {
            "shadow_overhead_ms": wall_total,
            "sample_count": report.total,
            "judge_calls": int(getattr(judge, "calls", 0) or 0),
            "judge_usage": judge_usage,
            "judge_kind": report.judge_kind,
            "clock": CLOCK_WALL,
            "note": ("灰度开销只记 shadow 侧观测成本；真实流量成本仍由 S2-03 UTC "
                     "口径统计（本模块不重复记账）"),
        }

    def _quality_patch(self, report: ShadowReport) -> Dict[str, Any]:
        """capability 级 quality 统计补丁（S1-01 的 descriptor.quality 字段）"""
        return {
            "success_rate": report.pass_rate,
            "sample_count": report.total,
            "p99_latency_ms": report.p99_wall_candidate_ms(),
            "note": ("来自 shadow 灰度（judge_kind=" + report.judge_kind
                     + "；p99 为**真实墙钟**口径，" + CLOCK_WALL + "）"),
        }

    def apply_quality(self, report: ShadowReport, *, registry: Any) -> Dict[str, Any]:
        """把灰度质量统计写入 descriptor.quality（**opt-in**：默认不写运行时区）"""
        patch = dict(report.quality_patch or {})
        patch.pop("note", None)
        if not patch:
            return {"applied": False, "reason": "无质量统计可写"}
        try:
            registry.update_fields(report.capability_id, {"quality": patch},
                                   actor=self.actor,
                                   reason="shadow 灰度质量统计回填（TASK-S3-03）")
        except Exception as e:  # noqa: BLE001  回填失败不得影响灰度结论
            logger.warning("quality 回填失败（advisory）: %s", e)
            return {"applied": False, "reason": f"{type(e).__name__}: {e}"}
        return {"applied": True, "patch": patch}


def isolation_declaration(*, plan: Optional[IsolationPlan] = None,
                          real_takeover: bool = False,
                          real_takeover_enabled: bool = False,
                          executed: int = 0,
                          not_guaranteed: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """隔离边界声明（M6 / S8-03：**三层事实分开报告**，不合并成一句好听话）

    | 字段 | 含义 | 为什么必须分开 |
    |---|---|---|
    | `mode` | **本次实际**用的执行模型 | 开关关着时"实际用的"就是进程内回放 |
    | `real_takeover` | **本次是否真的接管了**真实流量 | "开关开着但预算为 0 ⇒ 一次没跑"必须能看出来 |
    | `available_level` | 环境**具备**的最高隔离等级 | 不能因为"这次没用"就把能力说成没有，反之也不行 |
    | `level` / `container_isolated` | 本次**生效**的等级 | 容器不可用时这里绝不会是 `container` |
    | `not_guaranteed` | **不保证的边界**清单 | 诚实底线：等级弱在哪，必须写在报告里 |

    向后兼容：无参调用逐字保持 S3-03 的形态（`mode=in_process_deterministic_model`、
    `container_isolated=False`、`real_takeover=False`、`candidate_execution=sandbox_replay_only`）。
    """
    level = str(getattr(plan, "level", "") or ISOLATION_IN_PROCESS) \
        if plan is not None else ISOLATION_IN_PROCESS
    effective_takeover = bool(real_takeover and executed > 0
                              and level != ISOLATION_IN_PROCESS)
    boundaries = isolation_boundaries(level).to_dict()
    gaps = list(not_guaranteed) if not_guaranteed is not None \
        else list(boundaries["not_guaranteed"])
    payload: Dict[str, Any] = {
        "mode": MODE_ISOLATED if effective_takeover else MODE_IN_PROCESS,
        "container_isolated": bool(effective_takeover
                                   and level == "container"),
        "real_takeover": effective_takeover,
        "real_takeover_enabled": bool(real_takeover_enabled),
        "real_takeover_executed": int(executed),
        "level": level,
        "available_level": level,
        "available_container_isolated": bool(level == "container"),
        "kernel_isolation": bool(boundaries["kernel_isolation"]),
        "display": str(boundaries["display"]),
        "candidate_execution": (TRANSPORT_ISOLATED_TAKEOVER if effective_takeover
                                else TRANSPORT_SANDBOX_ONLY),
        "guarantees": list(boundaries["guarantees"]),
        "not_guaranteed": gaps,
        "enforcement": dict(boundaries["enforcement"]),
        "plan": plan.to_dict() if plan is not None else {},
        "note": (
            ("接管**在隔离边界内**执行（等级 " + level + "），副作用只记录不双写；"
             "产物**不自动合入**。")
            if effective_takeover else
            ("本次未接管真实流量：候选执行一律在 S3-02 的进程内确定性回放模型里"
             "（副作用只记录，`ReplayEnv.commit()` 恒抛）。"
             + ("接管开关已开启但本次未执行（预算/抽样/回落所致），"
                "隔离等级为 " + level + "。" if real_takeover_enabled
                else "接管默认关闭（需显式配置 + 显式比例）。")
             + "环境具备的最高隔离等级为 " + level
             + ("（容器，内核级）" if level == "container"
                else "（**非**内核级，差距见 not_guaranteed）"
                if level != ISOLATION_IN_PROCESS else "（无执行隔离）"))),
    }
    return payload


def shadow_quality(
    capability_id: str,
    *,
    runner: Optional[ShadowRunner] = None,
    store: Optional[CaseStore] = None,
    case_set: Optional[CaseSet] = None,
    candidate: Any = None,
    trace_ids: Sequence[str] = (),
    daily_avg: Optional[float] = None,
    force: bool = True,
    **kwargs: Any,
) -> ShadowReport:
    """便捷入口：跑一次灰度并返回报告（``force=True`` = 显式开启，见 `ShadowRunner.run`）"""
    box = runner or ShadowRunner()
    return box.run(capability_id, case_set=case_set, candidate=candidate,
                   trace_ids=trace_ids, daily_avg=daily_avg, force=force, **kwargs)


__all__ = [
    # 常量
    "SHADOW_VERSION", "SHADOW_BUDGET_RATIO", "SHADOW_BUDGET_CAP",
    "SHADOW_MIN_BUDGET", "SHADOW_ENABLE_ENV", "SHADOW_BUDGET_RATIO_ENV",
    "SHADOW_BUDGET_CAP_ENV", "SHADOW_MIN_BUDGET_ENV", "GRAY_ENABLE_ENV",
    "GRAY_RATIO_ENV", "GRAY_RATIO_BASELINE", "GRAY_RATIO_DEFAULT",
    "JUDGE_MODE_ENV", "JUDGE_PROVIDER_ENV", "JUDGE_MODEL_ENV", "JUDGE_MODES",
    "JUDGE_MODE_AUTO", "JUDGE_MODE_LLM", "JUDGE_MODE_LOCAL",
    "JUDGE_KIND_LLM", "JUDGE_KIND_LOCAL", "JUDGE_KIND_INJECTED",
    "JUDGE_KIND_LLM_FALLBACK", "CLOCK_WALL", "CLOCK_MODEL",
    "TRANSPORT_SANDBOX_ONLY", "TRANSPORT_ISOLATED_TAKEOVER",
    "MODE_IN_PROCESS", "MODE_ISOLATED",
    "DEFAULT_SHADOW_DIR", "SHADOW_DIR_ENV",
    "SHADOW_LEDGER_FILENAME", "MANUAL_REVIEW_FILENAME",
    "DEGRADE_WINDOW", "DEGRADE_RATE_FLOOR", "DEGRADE_CONSECUTIVE",
    "DEGRADE_MIN_SAMPLES", "DEGRADE_VERDICT_INSUFFICIENT",
    "DEGRADE_VERDICT_STABLE", "DEGRADE_VERDICT_DEGRADED",
    "DEGRADE_ACTION_OBSERVE", "DEGRADE_ACTION_WARN", "DEGRADE_ACTION_ROLLBACK",
    "REVIEW_VERDICT_PASS", "REVIEW_VERDICT_FAIL", "REVIEW_VERDICT_UNCERTAIN",
    "REVIEW_VERDICTS", "REVIEW_ROLE_HUMAN", "REVIEW_ROLE_AGENT",
    "EVENT_SCOPE_SHADOW", "AUDIT_ACTION_OBSERVED", "AUDIT_ACTION_DEGRADED",
    "AUDIT_ACTION_BLOCKED", "AUDIT_ACTION_REVIEWED",
    # 开关与抽样
    "shadow_enabled", "budget_from_env", "hash_fraction", "deterministic_sample",
    "daily_budget", "resolve_gray_policy", "gray_routed_ids",
    # judge
    "LLMJudge", "JudgeUnavailable", "ResolvedJudge", "JudgeGuard", "resolve_judge",
    "parse_judge_score",
    # 人工抽检
    "ManualReviewItem", "ManualReviewQueue",
    # 台账与报告
    "ShadowLedger", "ShadowPlan", "ShadowSample", "ShadowReport",
    "assess_degradation", "isolation_declaration", "sample_id_for",
    # 真实接管（TASK-S8-03）
    "TakeoverEngine", "TakeoverLedger", "TakeoverReport",
    "resolve_takeover_policy",
    # 三层比对流水线
    "CompareVerdict", "compare",
    # 门面
    "ShadowRunner", "shadow_quality",
]
