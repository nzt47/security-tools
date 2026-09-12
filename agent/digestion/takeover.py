"""真实流量接管（TASK-S8-03 步骤 4 / v7.2 §5.1 · §5.2）

**它解决什么问题**：S3-03 的 shadow 灰度只能"**记录选中**"（`gray.routed` 表示
"若接管，这条流量会走候选实现"），因为候选没有可安全执行的环境。S8-03 的
`isolation` 模块补齐了这个环境之后，本模块把**接管**这件事接上：按抽样比例把
真实流量交给候选实现，**在隔离边界内**执行，比对后决定是否采用。

## 四条不肯让的契约（全部可被用例断言）

1. **默认关闭**：接管需 `CP_DIGESTION_REAL_TAKEOVER=true` **且**给出显式比例
   （`CP_DIGESTION_REAL_TAKEOVER_RATIO` 或能力级
   `evolution.shadow_config.real_takeover_ratio`）。只有开关没有比例 ⇒ 不开；
   非法比例 ⇒ 回退关闭。与 S3-03 的灰度 5% 同一条纪律。
2. **无隔离即拒绝**：隔离等级为 `in_process` ⇒ 接管**不开**（`reasons` 写明），
   绝不"看起来能接管"（任务书 §五 风险条款）。
3. **有每日预算上限**：沿用 S3-03 的 `min(日均 × 比例, 上限)`（含低流量保底与
   S5-03 成本刹车系数）；预算为 0 ⇒ 一次都不跑（不是"跑一点"）。
4. **连续失败自动回落**：连续 `N` 次（默认 3）接管失败 ⇒ 本能力自动回落
   `sandbox_replay_only` + 开事故卡（`self_healing.levels.raise_incident`，L2）
   + 审计。回落后**不再执行**接管，直到有人显式处理事故卡。

## 不做什么

- ❌ **不自动合入候选产物**（任务书 §五：L2 白名单自动合入属后续决策）。故
  `TakeoverReport.adopted` **恒为 `False`**，产物只作为证据留在报告与台账里；
- ❌ 不做集群级隔离（P5 Backlog）；
- ❌ 不改 `record-and-replay` 语义：接管执行仍然**只记录副作用**，唯一可写根是
  一次性临时目录（见 `isolation`），真实环境的前后指纹由
  `isolation.snapshot_paths()/diff_snapshot()` 实测比对。

## 全程留痕

每一次接管运行写：审计（`digest.shadow.takeover` /
`digest.shadow.takeover_fallback`）+ 事件（复用 `digest.stage`，**不新增事件
类型**）+ 能力级 Trace（`actor="digestion.takeover"`，仅在拿到 facade 或允许
best-effort 写入时）。台账（`TakeoverLedger`）为 JSONL，是"连续失败"的
唯一事实来源（不靠内存计数，跨进程重启仍然成立）。

**import 纪律**：模块级只依赖标准库与同包 `isolation`/`sandbox`/`cases`；
`shadow`（预算公式）、`self_healing`（事故卡）、`observability`（审计/事件/Trace）
一律函数体内懒加载。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .cases import EquivalenceCase, ProgramStep
from .generalize import normalize_param_value
from .isolation import (
    ISOLATION_IN_PROCESS,
    IsolationExecutor,
    IsolationPlan,
    IsolationResult,
    normalize_level,
    resolve_isolation_level,
)

logger = logging.getLogger("agent.digestion.takeover")

# ════════════════════════════════════════════════════════════
#  常量
# ════════════════════════════════════════════════════════════

TAKEOVER_VERSION = "s8-03.1"

#: 接管传输方式（进报告与审计；与 S3-03 的 `TRANSPORT_SANDBOX_ONLY` 并列）
TRANSPORT_SANDBOX_ONLY = "sandbox_replay_only"
TRANSPORT_ISOLATED_TAKEOVER = "isolated_real_takeover"

#: 开关与阈值（**默认关闭**）
ENV_REAL_TAKEOVER = "CP_DIGESTION_REAL_TAKEOVER"
ENV_TAKEOVER_RATIO = "CP_DIGESTION_REAL_TAKEOVER_RATIO"
ENV_TAKEOVER_BUDGET_RATIO = "CP_DIGESTION_REAL_TAKEOVER_BUDGET_RATIO"
ENV_TAKEOVER_BUDGET_CAP = "CP_DIGESTION_REAL_TAKEOVER_BUDGET_CAP"
ENV_TAKEOVER_MIN_BUDGET = "CP_DIGESTION_REAL_TAKEOVER_MIN_BUDGET"
ENV_FAIL_THRESHOLD = "CP_DIGESTION_REAL_TAKEOVER_FAIL_THRESHOLD"
ENV_LEDGER_DIR = "CP_DIGESTION_TAKEOVER_DIR"

#: 默认预算口径（§4.5 同源，缺省与 shadow 一致以免出现第二套口径）
DEFAULT_TAKEOVER_BUDGET_RATIO = 0.15
DEFAULT_TAKEOVER_BUDGET_CAP = 50
DEFAULT_TAKEOVER_MIN_BUDGET = 1
#: 连续失败阈值（默认 3；与 shadow 的 `DEGRADE_CONSECUTIVE` 同值同义）
DEFAULT_FAIL_THRESHOLD = 3

#: 接管策略来源
TAKEOVER_SOURCE_DEFAULT = "default_off"
TAKEOVER_SOURCE_ENV = "env"
TAKEOVER_SOURCE_DESCRIPTOR = "descriptor.shadow_config"
TAKEOVER_SOURCE_REFUSED = "refused_no_isolation"

#: 接管状态（每个样本一档；**"没跑"与"跑了失败"必须可分**）
TAKEOVER_STATUS_NOT_REQUESTED = "not_requested"
TAKEOVER_STATUS_REFUSED = "refused_no_isolation"
TAKEOVER_STATUS_NO_BUDGET = "no_budget"
TAKEOVER_STATUS_NOT_SAMPLED = "not_sampled"
TAKEOVER_STATUS_FALLBACK = "fallback_engaged"
TAKEOVER_STATUS_RAN = "ran"
TAKEOVER_STATUS_FAILED = "failed"
TAKEOVER_STATUSES: Tuple[str, ...] = (
    TAKEOVER_STATUS_NOT_REQUESTED, TAKEOVER_STATUS_REFUSED,
    TAKEOVER_STATUS_NO_BUDGET, TAKEOVER_STATUS_NOT_SAMPLED,
    TAKEOVER_STATUS_FALLBACK, TAKEOVER_STATUS_RAN, TAKEOVER_STATUS_FAILED)

#: 审计动作（与 S3-03 的 `digest.shadow.*` 同族；**不新增事件类型**）
AUDIT_ACTION_TAKEOVER = "digest.shadow.takeover"
AUDIT_ACTION_TAKEOVER_FALLBACK = "digest.shadow.takeover_fallback"
EVENT_SCOPE_TAKEOVER = "shadow_takeover"
#: Trace 的 actor 标注（"全程 Trace（actor 标注）"的落点）
ACTOR_TAKEOVER = "digestion.takeover"

#: 台账
DEFAULT_TAKEOVER_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "digestion", "shadow")
TAKEOVER_LEDGER_FILENAME = "takeover_ledger.jsonl"

#: 回落目标（与 S3-03 同词）
FALLBACK_TRANSPORT = TRANSPORT_SANDBOX_ONLY


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


def _env_int(name: str, default: int, env: Optional[Dict[str, str]] = None) -> int:
    raw = str(_env(env).get(name, "") or "").strip()
    if not raw:
        return int(default)
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        logger.warning("%s=%r 非法整数，回退默认 %s", name, raw, default)
        return int(default)


def _valid_ratio(value: Any) -> Optional[float]:
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return None
    return ratio if 0.0 < ratio <= 1.0 else None


# ════════════════════════════════════════════════════════════
#  策略解析（默认关闭 + 无隔离即拒绝 + 显式比例）
# ════════════════════════════════════════════════════════════


@dataclass
class TakeoverPolicy:
    """接管策略决议（进报告与审计；"为什么开/不开"逐条可读）"""

    enabled: bool = False
    ratio: float = 0.0
    source: str = TAKEOVER_SOURCE_DEFAULT
    reasons: List[str] = field(default_factory=list)
    requested: bool = False
    isolation_level: str = ISOLATION_IN_PROCESS
    budget_ratio: float = DEFAULT_TAKEOVER_BUDGET_RATIO
    budget_cap: int = DEFAULT_TAKEOVER_BUDGET_CAP
    min_budget: int = DEFAULT_TAKEOVER_MIN_BUDGET
    fail_threshold: int = DEFAULT_FAIL_THRESHOLD

    @property
    def transport(self) -> str:
        return TRANSPORT_ISOLATED_TAKEOVER if self.enabled else TRANSPORT_SANDBOX_ONLY

    def to_dict(self) -> Dict[str, Any]:
        return {"enabled": self.enabled, "requested": self.requested,
                "ratio": self.ratio, "source": self.source,
                "reasons": list(self.reasons),
                "transport": self.transport,
                "isolation_level": self.isolation_level,
                "budget": {"ratio": self.budget_ratio, "cap": self.budget_cap,
                           "min_budget": self.min_budget},
                "fail_threshold": self.fail_threshold,
                "formula": ("每日预算 = min(日均 × ratio, budget_cap)"
                            "（低流量保底 min_budget；S5-03 成本刹车系数照旧生效）")}


def resolve_takeover_policy(*, shadow_config: Optional[Dict[str, Any]] = None,
                            env: Optional[Dict[str, str]] = None,
                            isolation: Optional[IsolationPlan] = None
                            ) -> TakeoverPolicy:
    """接管策略解析（**默认关闭**；无隔离即拒绝；非法值一律回退关闭）

    优先级：能力级 `shadow_config.real_takeover*` > 环境变量 > 默认关闭。
    """
    cfg = dict(shadow_config or {})
    env_map = _env(env)
    # 未显式传入隔离决议 ⇒ **自己去探**（而不是假定 in_process）：假定会让
    # "调用方忘了传" 与 "确实没有隔离" 变成同一件事，而前者会静默关掉接管。
    if isolation is None:
        isolation = resolve_isolation_level(env=env)
    level = normalize_level(isolation.level) or ISOLATION_IN_PROCESS
    policy = TakeoverPolicy(
        isolation_level=level,
        budget_ratio=DEFAULT_TAKEOVER_BUDGET_RATIO,
        budget_cap=_env_int(ENV_TAKEOVER_BUDGET_CAP, DEFAULT_TAKEOVER_BUDGET_CAP, env),
        min_budget=_env_int(ENV_TAKEOVER_MIN_BUDGET, DEFAULT_TAKEOVER_MIN_BUDGET, env),
        fail_threshold=max(1, _env_int(ENV_FAIL_THRESHOLD, DEFAULT_FAIL_THRESHOLD, env)),
    )
    policy.budget_ratio = _env_float_(ENV_TAKEOVER_BUDGET_RATIO,
                                      DEFAULT_TAKEOVER_BUDGET_RATIO, env,
                                      lower=0.0, upper=1.0)

    cfg_enabled = cfg.get("real_takeover")
    cfg_ratio = cfg.get("real_takeover_ratio")
    env_enabled = _env_flag(ENV_REAL_TAKEOVER, False, env)
    env_ratio_raw = env_map.get(ENV_TAKEOVER_RATIO)
    policy.requested = bool(cfg_enabled) if cfg_enabled is not None else env_enabled
    if cfg_ratio is not None:
        policy.requested = bool(cfg_enabled) if cfg_enabled is not None else True

    # ① 无隔离 ⇒ 拒绝（**优先判**：隔离等级不因配置而改变）
    if policy.requested and level == ISOLATION_IN_PROCESS:
        policy.enabled = False
        policy.source = TAKEOVER_SOURCE_REFUSED
        policy.reasons.append(
            "隔离等级为 in_process（无执行隔离环境）⇒ **拒绝** real_takeover；"
            "须先具备 container 或 subprocess_hardened（见 isolation.resolve_"
            "isolation_level 的降级理由）")
        return policy

    # ② 能力级配置优先
    if cfg_enabled is not None or cfg_ratio is not None:
        policy.source = TAKEOVER_SOURCE_DESCRIPTOR
        if not policy.requested:
            policy.reasons.append("能力级 shadow_config.real_takeover=false")
            return policy
        ratio = _valid_ratio(cfg_ratio)
        if ratio is None:
            policy.reasons.append(
                "能力级声明开启接管但未给出合法 real_takeover_ratio"
                "（须 0<ratio<=1）⇒ 不开")
            return policy
        policy.enabled, policy.ratio = True, ratio
        policy.reasons.append(f"能力级显式阈值 real_takeover_ratio={ratio}")
        return policy

    # ③ 环境变量
    if policy.requested or str(env_ratio_raw or "").strip():
        policy.source = TAKEOVER_SOURCE_ENV
        ratio = _valid_ratio(env_ratio_raw)
        if ratio is None:
            policy.reasons.append(
                f"{ENV_TAKEOVER_RATIO}={env_ratio_raw!r} 非法或缺失"
                "（须 0<ratio<=1）⇒ 回退默认关闭")
            return policy
        if not env_enabled:
            policy.reasons.append(
                f"{ENV_REAL_TAKEOVER} 未开启（仅有比例不构成开启）⇒ 不开")
            return policy
        policy.enabled, policy.ratio = True, ratio
        policy.reasons.append(
            f"环境变量显式开启 {ENV_REAL_TAKEOVER}=true 且 "
            f"{ENV_TAKEOVER_RATIO}={ratio}")
        return policy

    policy.reasons.append(
        f"{ENV_REAL_TAKEOVER} 未开启（默认关闭：接管真实流量是高危动作，"
        "开启需显式配置 + 显式比例）")
    return policy


def _env_float_(name: str, default: float, env: Optional[Dict[str, str]] = None,
                *, lower: float = 0.0, upper: float = 1.0) -> float:
    raw = str(_env(env).get(name, "") or "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r 非法数值，回退默认 %s", name, raw, default)
        return float(default)
    if not (lower < value <= upper):
        logger.warning("%s=%s 越界（须 %s<x<=%s），回退默认 %s",
                       name, value, lower, upper, default)
        return float(default)
    return value


def takeover_budget(daily_avg: float, *, ratio: float = DEFAULT_TAKEOVER_BUDGET_RATIO,
                    cap: int = DEFAULT_TAKEOVER_BUDGET_CAP,
                    min_budget: int = DEFAULT_TAKEOVER_MIN_BUDGET,
                    factor: Optional[float] = None) -> int:
    """接管每日预算（**沿用 S3-03 的公式**，不引入第二套口径）

    直接委托 `shadow.daily_budget`（懒加载避免与 shadow 构成导入环）；万一不可用，
    用**逐字相同**的本地实现兜底，并如实记一条警告——绝不静默换一套算法。
    """
    try:
        from .shadow import daily_budget
        return int(daily_budget(daily_avg, ratio=ratio, cap=cap,
                                min_budget=min_budget, factor=factor))
    except Exception as exc:  # noqa: BLE001
        logger.warning("shadow.daily_budget 不可用（改用同公式兜底实现）: %s", exc)
    try:
        avg = max(0.0, float(daily_avg or 0.0))
    except (TypeError, ValueError):
        avg = 0.0
    rate = _valid_ratio(ratio) or DEFAULT_TAKEOVER_BUDGET_RATIO
    ceiling = max(0, int(cap))
    planned = int(avg * rate)
    if planned == 0 and avg >= 1.0 and int(min_budget) > 0:
        planned = int(min_budget)
    return max(0, min(planned, ceiling))


# ════════════════════════════════════════════════════════════
#  台账（"连续失败"的唯一事实来源：跨进程重启仍然成立）
# ════════════════════════════════════════════════════════════


@dataclass
class TakeoverLedgerEntry:
    """一次接管运行的台账记录（**只记录**，不做任何执行动作）"""

    capability_id: str
    at: float = 0.0
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    level: str = ""
    fallback: bool = False
    fallback_reason: str = ""
    incident_id: str = ""
    audit_seq: int = 0

    @property
    def healthy(self) -> bool:
        return self.attempted > 0 and self.failed == 0

    def to_dict(self) -> Dict[str, Any]:
        return {"capability_id": self.capability_id, "at": self.at,
                "attempted": self.attempted, "succeeded": self.succeeded,
                "failed": self.failed, "level": self.level,
                "fallback": self.fallback,
                "fallback_reason": self.fallback_reason,
                "incident_id": self.incident_id, "audit_seq": self.audit_seq,
                "takeover_version": TAKEOVER_VERSION}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TakeoverLedgerEntry":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in dict(data or {}).items() if k in known})


class TakeoverLedger:
    """接管台账（JSONL；追加写，读时容错）"""

    def __init__(self, path: str = "", *, directory: str = "",
                 filename: str = TAKEOVER_LEDGER_FILENAME) -> None:
        if path:
            self.path = str(path)
            self.dir = os.path.dirname(os.path.abspath(self.path))
        else:
            self.dir = str(directory or os.environ.get(ENV_LEDGER_DIR)
                           or DEFAULT_TAKEOVER_DIR)
            self.path = os.path.join(self.dir, filename)

    def record(self, entry: TakeoverLedgerEntry) -> Dict[str, Any]:
        try:
            os.makedirs(self.dir, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:  # noqa: BLE001  台账写失败不得影响接管结论
            logger.warning("接管台账写入失败（结论不受影响）: %s", exc)
        return entry.to_dict()

    def entries(self, capability_id: str = "") -> List[TakeoverLedgerEntry]:
        out: List[TakeoverLedgerEntry] = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = TakeoverLedgerEntry.from_dict(json.loads(line))
                    except ValueError:
                        continue
                    if capability_id and entry.capability_id != capability_id:
                        continue
                    out.append(entry)
        except OSError:
            return out
        return out

    def rows(self, capability_id: str = "") -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self.entries(capability_id)]

    def consecutive_failures(self, capability_id: str) -> int:
        """**尾部连续**失败轮数（一轮失败 = 该轮 attempted>0 且 failed>0）

        只有"跑了但失败"才计入：没跑（预算为 0 / 未抽样）不算失败，否则
        "因为没预算所以没跑"会被误读成"一直失败"。
        """
        count = 0
        for entry in reversed(self.entries(capability_id)):
            if entry.attempted <= 0:
                continue
            if entry.failed > 0:
                count += 1
                continue
            break
        return count

    def engaged_fallback(self, capability_id: str) -> Optional[TakeoverLedgerEntry]:
        """最近一次已触发回落且**之后没有恢复正常**的记录（None = 未处于回落态）"""
        for entry in reversed(self.entries(capability_id)):
            if entry.fallback:
                return entry
            if entry.attempted > 0 and entry.failed == 0:
                return None
        return None

    def clear(self) -> None:
        try:
            os.remove(self.path)
        except OSError:
            pass


# ════════════════════════════════════════════════════════════
#  隔离执行结果 ↔ 回放观测的比对（复用三层比对的**硬性层**口径）
# ════════════════════════════════════════════════════════════

#: 状态词表映射（隔离执行体的状态与 `sandbox.OBS_*` **同词**；此处显式钉死，
#: 万一将来任一侧改词，这个映射会让用例当场失败而不是静默放过）
STATUS_ALIASES: Dict[str, str] = {
    "success": "success", "error": "error", "quota_exceeded": "quota_exceeded",
    "escape_blocked": "escape_blocked", "denied": "denied",
    "timeout": "quota_exceeded", "killed": "quota_exceeded",
    "unbound_input": "unbound_input", "refused": "refused", "not_run": "not_run",
}


def _norm_status(status: Any) -> str:
    return STATUS_ALIASES.get(str(status or "").strip(), str(status or "").strip())


def _norm_effects(effects: Dict[str, Any]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for key in ("files_written", "files_deleted", "external_calls"):
        values = [str(normalize_param_value(v)) for v in (effects or {}).get(key) or []]
        out[key] = sorted(set(values))
    return out


def compare_takeover(candidate_obs: Any,
                     result: IsolationResult) -> Dict[str, Any]:
    """隔离执行结果 vs 回放候选观测 —— **硬性两层**（结构 / 副作用）

    为什么只做硬性两层：软性 judge 层比的是"语义相似度"，而两条通道的输出形态
    本就不同（回放是模型时钟下的虚拟工具结果，隔离执行是真实进程的输出），
    拿 judge 去抹平这个差异只会制造"看起来等价"的假象。故软性层**如实标注
    `not_run`**，理由写清；最终判定只由硬性层给出。
    """
    layers: List[Dict[str, Any]] = []
    reasons: List[str] = []

    up_status = _norm_status(getattr(candidate_obs, "status", ""))
    iso_status = _norm_status(result.status)
    up_steps = [str(s) for s in (getattr(candidate_obs, "steps", None) or [])]
    iso_steps = [str(s) for s in (result.steps or [])]
    expected_effects = _norm_effects(getattr(candidate_obs, "side_effects", None) or {})
    actual_effects = _norm_effects(result.side_effect_set)
    structure_ok = (up_status == iso_status) and (up_steps == iso_steps)
    if not structure_ok:
        reasons.append(f"结构层：状态 {up_status!r} ≠ {iso_status!r}"
                       f" 或步骤 {up_steps} ≠ {iso_steps}")
    layers.append({"layer": "structure", "kind": "hard", "passed": structure_ok,
                   "detail": {"status_replay": up_status, "status_isolated": iso_status,
                              "steps_replay": up_steps, "steps_isolated": iso_steps}})

    effects_ok = expected_effects == actual_effects
    if not effects_ok:
        reasons.append(f"副作用层：{expected_effects} ≠ {actual_effects}")
    layers.append({"layer": "side_effects", "kind": "hard", "passed": effects_ok,
                   "detail": {"replay": expected_effects, "isolated": actual_effects}})
    layers.append({"layer": "judge", "kind": "soft", "passed": None,
                   "detail": {"status": "not_run",
                              "why": ("隔离执行与回放的输出形态不同，"
                                      "软性相似度不外推（避免制造假等价）")}})
    return {"matched": bool(structure_ok and effects_ok), "layers": layers,
            "reasons": reasons, "status_replay": up_status,
            "status_isolated": iso_status,
            "layers_passed": [l["layer"] for l in layers if l["passed"] is True]}


# ════════════════════════════════════════════════════════════
#  候选 → 隔离作业（步骤程序 → worker op 词表）
# ════════════════════════════════════════════════════════════


def steps_to_job(steps: Sequence[ProgramStep], case: EquivalenceCase, *,
                 job_id: str = "", work_root_rel: str = "candidate") -> Dict[str, Any]:
    """把（已绑定的）候选步骤程序转成隔离作业

    路径重定向：用例里的路径形态（如 ``C:/sandbox/out/a.txt``）在真实边界内**不可
    直接使用**（容器里没有盘符）。故把读写目标统一改写到一次性临时目录下，并
    **保留沙箱根之后的相对结构**（``C:/sandbox/out/a.txt`` →
    ``candidate/out/a.txt``）——既让候选真的跑起来，又不让它碰任何真实路径，
    同时"两个不同目录的两个文件"不会塌成同一个路径。
    **与回放同源的绑定**：参数补齐与占位符绑定直接复用 `sandbox.complete_params()`
    / `bind_params()`（与 `run_program()` 逐步同序），故两条通道拿到的是**同一套
    参数值**——否则"接管结果与回放不一致"就分不清是候选的问题还是绑定的问题。
    """
    from .sandbox import bind_params, complete_params

    program = list(steps or [])
    job_steps: List[Dict[str, Any]] = []
    unbound: List[str] = []
    root = str(getattr(case, "sandbox_root", "") or "")
    for index, step in enumerate(program):
        label = str(step.label or "").strip()
        params, _fills = complete_params(label, index, step.params, program, case)
        params, missing = bind_params(params, case)
        unbound.extend(missing)
        if label in _PATH_OPS:
            if "path" in params:
                params["path"] = _redirect_path(params.get("path"), work_root_rel, root)
            elif label in ("list_dir", "grep"):
                # 无路径的目录类操作：指向作业内的相对根（而非宿主任一目录）
                params["path"] = work_root_rel
        job_steps.append({"op": label, "params": params})
    if unbound:
        return {"job_id": job_id or f"takeover-{case.case_id}", "status": "unbound",
                "unbound": sorted(set(unbound)), "steps": []}
    fixtures = {_redirect_path(path, work_root_rel, root): content
                for path, content in (getattr(case, "fixtures", None) or {}).items()}
    return {"job_id": job_id or f"takeover-{case.case_id}", "status": "ok",
            "steps": job_steps, "fixtures": fixtures}


#: 含路径参数的操作（重定向的作用域；与 worker 的 op 词表对齐）
_PATH_OPS: Tuple[str, ...] = (
    "read_file", "write_file", "append_file", "delete_file", "create_dir",
    "stat", "list_dir", "grep", "apply_patch", "replace_in_file")


def _redirect_path(path: Any, rel_root: str, sandbox_root: str = "") -> str:
    """用例路径 → 隔离作业内的相对路径（保留沙箱根之后的**相对结构**）"""
    text = str(path or "").replace("\\", "/").strip()
    if not text:
        return f"{rel_root}/unnamed"
    root = str(sandbox_root or "").replace("\\", "/").strip().rstrip("/")
    if root and (text == root or text.startswith(root + "/")):
        remainder = text[len(root):].lstrip("/")
        return f"{rel_root}/{remainder}" if remainder else rel_root
    base = text.rstrip("/").rsplit("/", 1)[-1] or "unnamed"
    return f"{rel_root}/{base}"


# ════════════════════════════════════════════════════════════
#  接管报告
# ════════════════════════════════════════════════════════════


@dataclass
class TakeoverAttempt:
    """一次接管尝试（**成功/失败/没跑** 三态分明）"""

    sample_id: str
    case_id: str
    level: str
    status: str
    ran: bool = False
    ok: bool = False
    matched: bool = False
    failed: bool = False
    error_code: str = ""
    error: str = ""
    quota_exceeded: bool = False
    killed: bool = False
    wall_ms: float = 0.0
    duration_ms: float = 0.0
    reasons: List[str] = field(default_factory=list)
    side_effects: Dict[str, List[str]] = field(default_factory=dict)
    layers: List[Dict[str, Any]] = field(default_factory=list)
    #: **恒为 False**：不自动合入候选产物（L2 白名单自动合入属后续决策）
    adopted: bool = False
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"sample_id": self.sample_id, "case_id": self.case_id,
                "level": self.level, "status": self.status, "ran": self.ran,
                "ok": self.ok, "matched": self.matched, "failed": self.failed,
                "error_code": self.error_code, "error": self.error,
                "quota_exceeded": self.quota_exceeded, "killed": self.killed,
                "wall_ms": round(self.wall_ms, 3),
                "duration_ms": round(self.duration_ms, 3),
                "reasons": list(self.reasons),
                "side_effects": {k: list(v) for k, v in self.side_effects.items()},
                "layers": list(self.layers), "adopted": self.adopted,
                "note": self.note}


@dataclass
class TakeoverReport:
    """一次接管运行的完整结果（可 JSON 序列化；供报告/面板/结案消费）"""

    capability_id: str = ""
    policy: Dict[str, Any] = field(default_factory=dict)
    isolation: Dict[str, Any] = field(default_factory=dict)
    level: str = ""
    enabled: bool = False
    transport: str = TRANSPORT_SANDBOX_ONLY
    budget: int = 0
    daily_avg: float = 0.0
    candidates: List[str] = field(default_factory=list)
    attempts: List[TakeoverAttempt] = field(default_factory=list)
    consecutive_failures: int = 0
    fallback: bool = False
    fallback_reason: str = ""
    fallback_transport: str = FALLBACK_TRANSPORT
    incident_id: str = ""
    reasons: List[str] = field(default_factory=list)
    audit_seq: int = 0
    audit_hash: str = ""
    event_id: str = ""
    trace_id: str = ""
    #: **恒为 False**（不自动合入）
    adopted: bool = False

    @property
    def executed(self) -> int:
        return sum(1 for a in self.attempts if a.ran)

    @property
    def failed(self) -> int:
        return sum(1 for a in self.attempts if a.failed)

    @property
    def matched(self) -> int:
        return sum(1 for a in self.attempts if a.matched)

    @property
    def pass_rate(self) -> float:
        return round(self.matched / self.executed, 4) if self.executed else 0.0

    def to_dict(self, *, include_attempts: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "capability_id": self.capability_id, "enabled": self.enabled,
            "level": self.level, "transport": self.transport,
            "policy": dict(self.policy), "isolation": dict(self.isolation),
            "budget": self.budget, "daily_avg": self.daily_avg,
            "candidates": list(self.candidates),
            "executed": self.executed, "failed": self.failed,
            "matched": self.matched, "pass_rate": self.pass_rate,
            "consecutive_failures": self.consecutive_failures,
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
            "fallback_transport": self.fallback_transport,
            "incident_id": self.incident_id, "reasons": list(self.reasons),
            "adopted": self.adopted,
            "audit_seq": self.audit_seq, "audit_hash": self.audit_hash,
            "event_id": self.event_id, "trace_id": self.trace_id,
            "takeover_version": TAKEOVER_VERSION,
            "note": ("接管在隔离边界内执行，产物**不自动合入**（adopted 恒 False）；"
                     "副作用只记录不双写"),
        }
        if include_attempts:
            payload["attempts"] = [a.to_dict() for a in self.attempts]
        return payload

    def markdown(self) -> str:
        lines = [
            "# 真实流量接管报告",
            "",
            f"- 接管版本：{TAKEOVER_VERSION}｜能力：`{self.capability_id}`",
            f"- 开关：**{'开启' if self.enabled else '关闭'}**"
            f"（来源 {self.policy.get('source')}）｜传输：`{self.transport}`",
            f"- 隔离等级：`{self.level}`"
            f"（容器隔离={self.isolation.get('container_isolated')}，"
            f"内核级={self.isolation.get('kernel_isolation')}）",
            f"- 每日预算：{self.budget}（日均 {self.daily_avg}）"
            f"｜候选样本 {len(self.candidates)} 条",
            f"- 结果：执行 {self.executed}｜一致 {self.matched}"
            f"（通过率 {self.pass_rate}）｜失败 {self.failed}",
            f"- 连续失败：{self.consecutive_failures}"
            f"｜回落：{'是' if self.fallback else '否'}"
            + (f"（→ `{self.fallback_transport}`，事故卡 `{self.incident_id}`）"
               if self.fallback else ""),
            f"- **未自动合入**：adopted={self.adopted}"
            "（自动合入属 L2 白名单，另议）",
        ]
        if self.reasons:
            lines += ["", "**说明**"] + [f"- {r}" for r in self.reasons]
        return "\n".join(lines) + "\n"


# ════════════════════════════════════════════════════════════
#  接管引擎
# ════════════════════════════════════════════════════════════


class TakeoverEngine:
    """真实接管执行器（抽样 → 隔离执行 → 比对 → 台账 → 失败回落）

    用法::

        engine = TakeoverEngine(executor=SubprocessHardenedExecutor(), env={})
        report = engine.run(capability_id, policy=floor,
                            gray_routed=["t1"], daily_avg=100,
                            cases={"t1": case}, candidate=lambda c: steps)
    """

    def __init__(self, *, executor: Optional[IsolationExecutor] = None,
                 ledger: Optional[TakeoverLedger] = None,
                 env: Optional[Dict[str, str]] = None,
                 incident_dir: str = "",
                 trace: Any = None,
                 trace_db: str = "",
                 emit: bool = True,
                 actor: str = ACTOR_TAKEOVER,
                 sampler: Optional[Callable[[Iterable[str], int], List[str]]] = None
                 ) -> None:
        self.executor = executor
        self.env = dict(env or {})
        self.ledger = ledger
        self.incident_dir = str(incident_dir or "")
        self.trace = trace
        #: Trace 落盘位置（**显式可注入**）：默认走 `TraceFacade` 的默认库，
        #: 但用例/演示必须指到临时目录，否则"跑一次测试"就往仓库运行时区写一条
        #: Trace；那不是证据，是污染。
        self.trace_db = str(trace_db or "")
        self.emit = bool(emit)
        self.actor = str(actor or ACTOR_TAKEOVER)
        self._sampler = sampler

    # ── 依赖（懒加载） ──────────────────────────────────────

    @property
    def _ledger(self) -> TakeoverLedger:
        if self.ledger is None:
            self.ledger = TakeoverLedger()
        return self.ledger

    def _sample(self, ids: Iterable[str], size: int) -> List[str]:
        if self._sampler is not None:
            return list(self._sampler(ids, size))
        try:
            from .shadow import deterministic_sample
            return list(deterministic_sample(ids, size))
        except Exception as exc:  # noqa: BLE001
            logger.warning("确定性抽样不可用（改用哈希序本地实现）: %s", exc)
            ordered = sorted({str(i) for i in ids},
                             key=lambda s: (hashlib.sha1(s.encode("utf-8"))
                                            .hexdigest(), s))
            return ordered[:max(0, int(size))]

    # ── 主流程 ──────────────────────────────────────────────

    def run(self, capability_id: str, *,
            policy: TakeoverPolicy,
            gray_routed: Sequence[str] = (),
            cases: Optional[Dict[str, EquivalenceCase]] = None,
            candidate_for: Optional[Callable[[EquivalenceCase], Sequence[ProgramStep]]] = None,
            replay_obs_for: Optional[Callable[[str], Any]] = None,
            daily_avg: float = 0.0,
            isolation: Optional[IsolationPlan] = None,
            now: float = 0.0) -> TakeoverReport:
        """执行一次接管（**任何"没跑"都写进 reasons，不静默**）"""
        report = TakeoverReport(capability_id=str(capability_id or ""),
                                policy=policy.to_dict(),
                                level=policy.isolation_level,
                                enabled=bool(policy.enabled),
                                transport=policy.transport,
                                daily_avg=float(daily_avg or 0.0))
        if isolation is not None:
            report.isolation = isolation.to_dict()
        report.adopted = False
        report.reasons.extend(policy.reasons)

        if not policy.enabled:
            report.reasons.append("接管未开启 ⇒ 只做回放记录（sandbox_replay_only）")
            self._record_ledger(report, attempted=0, succeeded=0, failed=0,
                                fallback=False, fallback_reason="", incident_id="")
            return report

        engaged = self._ledger.engaged_fallback(report.capability_id)
        if engaged is not None:
            report.fallback = True
            report.fallback_transport = FALLBACK_TRANSPORT
            report.fallback_reason = (
                f"该能力已处于回落态（事故卡 {engaged.incident_id or '-'}，"
                f"发生于 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(engaged.at))}）"
                "⇒ 本次不执行接管；须先处理事故卡")
            report.consecutive_failures = self._ledger.consecutive_failures(
                report.capability_id)
            report.reasons.append(report.fallback_reason)
            self._record_ledger(report, attempted=0, succeeded=0, failed=0,
                                fallback=True, fallback_reason=report.fallback_reason,
                                incident_id=engaged.incident_id)
            return report

        budget = takeover_budget(daily_avg, ratio=policy.ratio,
                                 cap=policy.budget_cap,
                                 min_budget=policy.min_budget)
        report.budget = int(budget)
        if budget <= 0:
            report.reasons.append(
                f"每日预算为 0（日均 {report.daily_avg} × {policy.ratio}）⇒ 本次不执行接管")
            self._record_ledger(report, attempted=0, succeeded=0, failed=0,
                                fallback=False, fallback_reason="", incident_id="")
            return report

        selected = self._sample([str(s) for s in gray_routed or []], budget)
        report.candidates = list(selected)
        if not selected:
            report.reasons.append(
                f"灰度选中集为空 ⇒ 无候选可接管（预算 {budget} 未被使用）")
            self._record_ledger(report, attempted=0, succeeded=0, failed=0,
                                fallback=False, fallback_reason="", incident_id="")
            return report
        if self.executor is None:
            report.reasons.append("未配置隔离执行器 ⇒ 拒绝执行接管（fail-closed）")
            self._record_ledger(report, attempted=0, succeeded=0, failed=0,
                                fallback=False, fallback_reason="", incident_id="")
            return report

        case_map = dict(cases or {})
        for sample_id in selected:
            case = case_map.get(sample_id)
            if case is None:
                report.attempts.append(TakeoverAttempt(
                    sample_id=sample_id, case_id="", level=policy.isolation_level,
                    status="missing_case", ran=False,
                    error="抽样键无对应用例（不猜、也不静默跳过成「成功」）"))
                continue
            report.attempts.append(self._attempt(
                sample_id, case, policy=policy,
                candidate_for=candidate_for, replay_obs_for=replay_obs_for))

        failed = report.failed
        report.consecutive_failures = self._ledger.consecutive_failures(
            report.capability_id) + (1 if failed > 0 else 0)
        if failed > 0 and report.consecutive_failures >= policy.fail_threshold:
            self._engage_fallback(report, policy)
        self._record_ledger(report, attempted=report.executed,
                            succeeded=report.executed - failed, failed=failed,
                            fallback=report.fallback,
                            fallback_reason=report.fallback_reason,
                            incident_id=report.incident_id)
        return report

    # ── 单次尝试 ────────────────────────────────────────────

    def _attempt(self, sample_id: str, case: EquivalenceCase, *,
                 policy: TakeoverPolicy,
                 candidate_for: Optional[Callable[[EquivalenceCase], Sequence[ProgramStep]]],
                 replay_obs_for: Optional[Callable[[str], Any]]) -> TakeoverAttempt:
        assert self.executor is not None
        steps: Sequence[ProgramStep] = (candidate_for(case)
                                        if candidate_for is not None else [])
        attempt = TakeoverAttempt(sample_id=sample_id, case_id=case.case_id,
                                  level=self.executor.level,
                                  status=TAKEOVER_STATUS_RAN)
        job = steps_to_job(list(steps), case,
                           job_id=f"takeover-{case.case_id}")
        if job.get("status") == "unbound":
            attempt.status = TAKEOVER_STATUS_FAILED
            attempt.failed = True
            attempt.error_code = "E_TAKEOVER_UNBOUND_INPUT"
            attempt.error = f"候选步骤缺输入: {job.get('unbound')}"
            attempt.note = "未绑定输入 ⇒ 不进边界执行（不臆造值）"
            return attempt
        result = self.executor.run(job)
        attempt.ran = bool(result.ran)
        attempt.ok = bool(result.ok)
        attempt.status = result.status if result.ran else TAKEOVER_STATUS_FAILED
        attempt.error_code = result.error_code
        attempt.error = result.error
        attempt.quota_exceeded = bool(result.quota_exceeded)
        attempt.killed = bool(result.killed)
        attempt.wall_ms = float(result.wall_ms)
        attempt.duration_ms = float(result.duration_ms)
        attempt.side_effects = dict(result.side_effect_set)
        if result.quota_exceeded:
            attempt.reasons.append(
                "资源/时间超限被终止（quota_exceeded；**不静默**，计入失败）")

        replay_obs = replay_obs_for(sample_id) if replay_obs_for is not None else None
        if replay_obs is None:
            attempt.status = TAKEOVER_STATUS_FAILED
            attempt.failed = True
            attempt.reasons.append(
                "缺回放候选观测 ⇒ 无法比对（不把「跑了」当成「一致」）")
            return attempt
        verdict = compare_takeover(replay_obs, result)
        attempt.layers = list(verdict["layers"])
        attempt.matched = bool(verdict["matched"])
        attempt.reasons.extend(verdict["reasons"])
        # 失败 = 没跑成 或 跑成了但与回放不一致（两条都算"接管失败"，都要计数）
        attempt.failed = (not result.ok) or (not attempt.matched)
        if attempt.failed:
            attempt.status = TAKEOVER_STATUS_FAILED
            if not result.ok:
                attempt.reasons.insert(0, f"隔离执行未成功：{result.status}"
                                          f"{('／' + result.error) if result.error else ''}")
        attempt.note = ("隔离执行成功且与回放一致；**未自动合入**（adopted=False）"
                        if not attempt.failed else "")
        return attempt

    # ── 失败回落 ────────────────────────────────────────────

    def _engage_fallback(self, report: TakeoverReport,
                         policy: TakeoverPolicy) -> None:
        """连续失败 ⇒ 自动回落 `sandbox_replay_only` + 事故卡 + 审计"""
        report.fallback = True
        report.fallback_transport = FALLBACK_TRANSPORT
        report.fallback_reason = (
            f"连续 {report.consecutive_failures} 次接管失败（阈值 "
            f"{policy.fail_threshold}）⇒ 自动回落 `{FALLBACK_TRANSPORT}`")
        report.reasons.append(report.fallback_reason)
        report.incident_id = self._raise_incident(report, policy)
        report.audit_seq, report.audit_hash = self._audit(
            AUDIT_ACTION_TAKEOVER_FALLBACK, report,
            status="fallback",
            payload={"consecutive_failures": report.consecutive_failures,
                     "threshold": policy.fail_threshold,
                     "incident_id": report.incident_id,
                     "failed": report.failed, "executed": report.executed})
        report.event_id = self._emit_event(report, verdict="takeover_fallback")
        report.trace_id = self._record_trace(report, status="error",
                                             error_code="E_TAKEOVER_FALLBACK")

    def _raise_incident(self, report: TakeoverReport,
                        policy: TakeoverPolicy) -> str:
        """开事故卡（L2：能力级回退；best-effort，失败不阻断回落本身）"""
        try:
            from agent.self_healing.levels import HealLevel, raise_incident
            card = raise_incident(
                HealLevel.L2,
                signal="shadow_takeover_consecutive_failure",
                root_cause=report.fallback_reason,
                fatal_change=(f"capability:{report.capability_id} 的候选实现"
                              f"在 {policy.isolation_level} 等级的接管连续失败"),
                trace_ids=[a.case_id for a in report.attempts][:10],
                directory=(self.incident_dir or None),
                detail={"capability_id": report.capability_id,
                        "level": policy.isolation_level,
                        "transport": policy.transport,
                        "consecutive_failures": report.consecutive_failures,
                        "failed": report.failed, "executed": report.executed,
                        "fallback_transport": FALLBACK_TRANSPORT,
                        "takeover_version": TAKEOVER_VERSION})
            return str(getattr(card, "incident_id", "") or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("接管回落事故卡写入失败（回落本身不受影响）: %s", exc)
            return ""

    # ── 留痕 ────────────────────────────────────────────────

    def _record_ledger(self, report: TakeoverReport, *, attempted: int,
                       succeeded: int, failed: int, fallback: bool,
                       fallback_reason: str, incident_id: str) -> None:
        entry = TakeoverLedgerEntry(
            capability_id=report.capability_id, at=time.time(),
            attempted=int(attempted), succeeded=int(succeeded), failed=int(failed),
            level=report.level, fallback=bool(fallback),
            fallback_reason=str(fallback_reason or ""),
            incident_id=str(incident_id or ""), audit_seq=int(report.audit_seq or 0))
        self._ledger.record(entry)
        if not fallback and report.enabled and attempted > 0:
            report.audit_seq, report.audit_hash = self._audit(
                AUDIT_ACTION_TAKEOVER, report, status="executed",
                payload={"budget": report.budget, "executed": attempted,
                         "matched": report.matched, "failed": failed,
                         "level": report.level, "transport": report.transport,
                         "adopted": False})
            report.event_id = self._emit_event(report, verdict="takeover_observed")
            report.trace_id = self._record_trace(
                report, status=("success" if failed == 0 else "error"),
                error_code=("" if failed == 0 else "E_TAKEOVER_MISMATCH"))

    def _audit(self, action: str, report: TakeoverReport, *, status: str,
               payload: Dict[str, Any]) -> Tuple[int, str]:
        if not self.emit:
            return 0, ""
        try:
            from agent.audit.facade import audit
            entry = audit.record(
                action, actor=self.actor,
                subject=f"capability:{report.capability_id}",
                payload=dict(payload, isolation_level=report.level,
                             fallback=report.fallback),
                source="agent", status=status,
                technical={"takeover_version": TAKEOVER_VERSION,
                           "isolation_level": report.level})
            if entry is None:
                return 0, ""
            return (int(getattr(entry, "seq", 0) or 0),
                    str(getattr(entry, "self_hash", "") or ""))
        except Exception as exc:  # noqa: BLE001
            logger.debug("接管审计写入失败: %s", exc)
            return 0, ""

    def _emit_event(self, report: TakeoverReport, *, verdict: str) -> str:
        """事件（**复用既有 `digest.stage` 类型**，不新增事件类型）"""
        if not self.emit:
            return ""
        try:
            from agent.observability.events import EV_DIGEST_STAGE, emit, trace_fields
            fields = trace_fields()
            body = {
                "capability_id": report.capability_id, "from_stage": "shadow",
                "to_stage": "shadow", "applied": False, "verdict": verdict,
                "scope": EVENT_SCOPE_TAKEOVER,
                "reasons": [f"isolation_level={report.level}",
                            f"transport={report.transport}",
                            f"budget={report.budget}",
                            f"executed={report.executed}",
                            f"failed={report.failed}",
                            f"fallback={report.fallback}",
                            f"adopted={report.adopted}"],
                "digest_run_id": str(report.policy.get("source") or ""),
                "executed": report.executed, "pass_rate": report.pass_rate,
                "workspace_id": fields.get("workspace_id", ""),
                "subject_id": fields.get("subject_id", ""),
                "trace_id": fields.get("trace_id", ""),
                "note": ("接管在隔离边界内执行，副作用只记录不双写；产物不自动合入"),
            }
            correlation = (f"takeover:{report.capability_id}:"
                           f"{int(time.time())}")
            envelope = emit(EV_DIGEST_STAGE, body, correlation_id=correlation,
                            idempotency_key=f"{correlation}:{report.executed}")
            return str(getattr(envelope, "event_id", "") or "")
        except Exception as exc:  # noqa: BLE001  事件失败不得影响接管结论
            logger.debug("接管事件发送失败: %s", exc)
            return ""

    def _record_trace(self, report: TakeoverReport, *, status: str,
                      error_code: str) -> str:
        """能力级 Trace（``actor="digestion.takeover"``）—— best-effort"""
        if not self.emit:
            return ""
        try:
            from agent.observability.trace_v2 import SideEffects
            facade = self.trace
            if facade is None:
                from agent.observability.trace_v2 import TraceFacade
                facade = TraceFacade(self.trace_db or None)
            effects = SideEffects()
            written: List[str] = []
            for attempt in report.attempts:
                written.extend(attempt.side_effects.get("files_written") or [])
            effects.files_written = written[:32]
            effects.notes = [f"isolation_level:{report.level}",
                             f"transport:{report.transport}",
                             f"adopted:{report.adopted}"]
            trace = facade.record(
                f"digestion.takeover.{report.capability_id}",
                args={"level": report.level, "budget": report.budget,
                      "candidates": len(report.candidates),
                      "fallback": report.fallback},
                output={"executed": report.executed, "matched": report.matched,
                        "failed": report.failed, "pass_rate": report.pass_rate,
                        "adopted": report.adopted,
                        "fallback_transport": (report.fallback_transport
                                               if report.fallback else "")},
                actor=ACTOR_TAKEOVER, status=status, error_code=error_code,
                side_effects=effects)
            return str(getattr(trace, "trace_id", "") or "")
        except Exception as exc:  # noqa: BLE001
            logger.debug("接管 Trace 写入失败: %s", exc)
            return ""


__all__ = [
    "TAKEOVER_VERSION", "TRANSPORT_SANDBOX_ONLY", "TRANSPORT_ISOLATED_TAKEOVER",
    "ENV_REAL_TAKEOVER", "ENV_TAKEOVER_RATIO", "ENV_TAKEOVER_BUDGET_RATIO",
    "ENV_TAKEOVER_BUDGET_CAP", "ENV_TAKEOVER_MIN_BUDGET", "ENV_FAIL_THRESHOLD",
    "ENV_LEDGER_DIR", "DEFAULT_TAKEOVER_BUDGET_RATIO", "DEFAULT_TAKEOVER_BUDGET_CAP",
    "DEFAULT_TAKEOVER_MIN_BUDGET", "DEFAULT_FAIL_THRESHOLD",
    "TAKEOVER_SOURCE_DEFAULT", "TAKEOVER_SOURCE_ENV", "TAKEOVER_SOURCE_DESCRIPTOR",
    "TAKEOVER_SOURCE_REFUSED",
    "TAKEOVER_STATUS_NOT_REQUESTED", "TAKEOVER_STATUS_REFUSED",
    "TAKEOVER_STATUS_NO_BUDGET", "TAKEOVER_STATUS_NOT_SAMPLED",
    "TAKEOVER_STATUS_FALLBACK", "TAKEOVER_STATUS_RAN", "TAKEOVER_STATUS_FAILED",
    "TAKEOVER_STATUSES",
    "AUDIT_ACTION_TAKEOVER", "AUDIT_ACTION_TAKEOVER_FALLBACK",
    "EVENT_SCOPE_TAKEOVER", "ACTOR_TAKEOVER", "STATUS_ALIASES",
    "DEFAULT_TAKEOVER_DIR", "TAKEOVER_LEDGER_FILENAME", "FALLBACK_TRANSPORT",
    "TakeoverPolicy", "resolve_takeover_policy", "takeover_budget",
    "TakeoverLedgerEntry", "TakeoverLedger", "TakeoverAttempt", "TakeoverReport",
    "TakeoverEngine", "compare_takeover", "steps_to_job", "_PATH_OPS",
]
