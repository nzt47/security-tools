"""S2-03 遗留 #3：ACR 意图/难度启发式 → 真实信号的**切换路径**（TASK-S5-02 步骤 3）

## 现状与纪律（对齐审计 T5 结论）

`agent.observability.acr` 的意图/难度分类是**启发式**（词典 + 长度阈值），且难度权重
"等权起步"。审计结论是：**早期只披露不考核** —— 在拿到真实基线数据前，把未经拟合的
权重当考核依据，只会把噪声变成 KPI。

## 本模块交付什么

1. **拟合器**（`fit_difficulty_weights`）：以 L2 基线数据（`task.closed` 事件的
   介入强度/耗时/成本，按 difficulty 分层）估计各难度档的相对权重，产出版本化
   拟合件（`data/eval/acr_difficulty_fit.json`），带样本量、方法与基线哈希；
2. **切换闸门**（`switch_status`）：只有**同时**满足 ① L2 基线就绪 ② 拟合件可用
   （每档样本 ≥ `MIN_SAMPLES_PER_STRATUM`）③ 显式开关 `CP_ACR_DIFFICULTY_FIT=1`
   ④ 拟合件与当前 L2 用例集哈希一致，才判定为"可切换"；
3. **切换清单**（`switch_checklist`）：逐条列出切换要动哪个文件/参数、如何验证、
   如何回滚 —— 让"切换"是有据可循的工程动作，而不是悄悄改一行常量。

**默认行为**：闸门关闭 → `difficulty_weight()` 恒返回 ``1.0``（等权），
`acr.py` 的既有行为**逐字不变**（本模块**不修改** acr.py：口径纪律要求先有基线数据，
且"不改既有公开接口签名与行为"）。

## 与 L2 基线的对接

`fit_difficulty_weights(observations, l2_baseline=...)` 会把 L2 用例集哈希写入拟合件；
`switch_status(..., l2_baseline=...)` 据此判断"拟合数据是否仍对应当前 L2 基线"
（基线换版 → 拟合件失效，必须重拟合）。
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import agent.eval.anchor as A
from agent.observability import acr as ACR
from agent.observability.events import EV_TASK_ABANDONED, EV_TASK_CLOSED, EventEnvelope

logger = logging.getLogger("agent.eval.calibration")

#: 显式开关（**默认关闭**：切换前保持"只披露不考核"）
FIT_ENV = "CP_ACR_DIFFICULTY_FIT"

#: 拟合件版本（口径变更必须升版）
FIT_VERSION = "acr-difficulty-fit.v1"

#: 每档最小样本量（低于此不得拟合出可考核的权重）
MIN_SAMPLES_PER_STRATUM = 20

#: 拟合件默认落点（运行期产物，与锚分离）
DEFAULT_FIT_PATH = os.path.join(A._REPO_ROOT, "data", "eval", "acr_difficulty_fit.json")

#: 披露文本（报告/周报直接引用，避免口径漂移）
DISCLOSURE_ON = ("拟合权重已启用（CP_ACR_DIFFICULTY_FIT=1 且闸门全绿）：难度权重"
                 "参与 ACR 口径，属**考核**口径")
DISCLOSURE_OFF = ("**只披露不考核**：难度权重仍等权（启发式分类 + 等权起步）；"
                  "未经 L2 基线拟合前，本项不参与任何考核判定（审计 T5 结论）")


class CalibrationError(ValueError):
    """拟合/切换参数非法"""


@dataclass
class DifficultyFit:
    """难度权重拟合件（版本化 + 可追溯：样本量 / 方法 / 基线哈希）"""

    version: str = FIT_VERSION
    fitted_at: str = ""
    method: str = ""
    sample_count: int = 0
    samples_by_difficulty: Dict[str, int] = field(default_factory=dict)
    means_by_difficulty: Dict[str, float] = field(default_factory=dict)
    weights: Dict[str, float] = field(default_factory=dict)
    usable: bool = False
    status: str = "unfitted"
    l2_caseset_sha256: str = ""
    source: str = ""
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version, "fitted_at": self.fitted_at,
            "method": self.method, "sample_count": self.sample_count,
            "samples_by_difficulty": dict(sorted(self.samples_by_difficulty.items())),
            "means_by_difficulty": {k: round(v, 6) for k, v in
                                    sorted(self.means_by_difficulty.items())},
            "weights": {k: round(v, 6) for k, v in sorted(self.weights.items())},
            "usable": bool(self.usable), "status": self.status,
            "l2_caseset_sha256": self.l2_caseset_sha256, "source": self.source,
            "notes": self.notes,
            "min_samples_per_stratum": MIN_SAMPLES_PER_STRATUM,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DifficultyFit":
        if not isinstance(data, Mapping):
            raise CalibrationError("拟合件根必须是对象")
        version = str(data.get("version") or "")
        if version and version != FIT_VERSION:
            raise CalibrationError(f"未知拟合件版本: {version!r}（期望 {FIT_VERSION}）")
        return cls(
            version=version or FIT_VERSION,
            fitted_at=str(data.get("fitted_at") or ""),
            method=str(data.get("method") or ""),
            sample_count=int(data.get("sample_count") or 0),
            samples_by_difficulty={str(k): int(v) for k, v in
                                   (data.get("samples_by_difficulty") or {}).items()},
            means_by_difficulty={str(k): float(v) for k, v in
                                 (data.get("means_by_difficulty") or {}).items()},
            weights={str(k): float(v) for k, v in (data.get("weights") or {}).items()},
            usable=bool(data.get("usable")), status=str(data.get("status") or "unfitted"),
            l2_caseset_sha256=str(data.get("l2_caseset_sha256") or ""),
            source=str(data.get("source") or ""), notes=str(data.get("notes") or ""),
        )


# ════════════════════════════════════════════════════════════
#  观测抽取与拟合
# ════════════════════════════════════════════════════════════


def difficulty_observations(rows: Sequence[EventEnvelope]) -> List[Dict[str, Any]]:
    """从事件流抽取难度观测：``{difficulty, intervened, intervention_weight, duration_ms, cost_cents}``

    数据源：`task.closed`（§6.6）。介入强度取该任务在窗口内的介入权重和
    （``intervention`` 事件按 ``task_id`` 归集，口径与 ACR 一致：§6.1 七项 + 扩展项）。
    """
    weights: Dict[str, float] = {}
    for env in rows:
        if env.type != ACR.EV_INTERVENTION:
            continue
        payload = env.payload or {}
        task_id = str(payload.get("task_id") or "")
        if not task_id:
            continue
        try:
            weights[task_id] = weights.get(task_id, 0.0) + float(payload.get("weight") or 0.0)
        except (TypeError, ValueError):
            continue
    out: List[Dict[str, Any]] = []
    for env in rows:
        if env.type not in (EV_TASK_CLOSED, EV_TASK_ABANDONED):
            continue
        payload = env.payload or {}
        task_id = str(payload.get("task_id") or env.correlation_id or "")
        difficulty = str(payload.get("difficulty") or "")
        if difficulty not in ACR.DIFFICULTIES:
            continue
        try:
            duration = float(payload.get("duration_ms") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        try:
            cost = float(payload.get("cost_cents") or 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        out.append({
            "task_id": task_id,
            "difficulty": difficulty,
            "intervened": bool(payload.get("intervened")),
            "intervention_weight": round(weights.get(task_id, 0.0), 6),
            "duration_ms": duration,
            "cost_cents": cost,
        })
    return out


def fit_difficulty_weights(observations: Sequence[Mapping[str, Any]], *,
                           l2_baseline: Optional[Mapping[str, Any]] = None,
                           now: Optional[str] = None) -> DifficultyFit:
    """以 L2 基线观测拟合难度权重（**相对权重**：全档均值归一）

    方法（可复现、无自由参数）：
    ``raw_d = mean(介入强度 | difficulty=d)``；
    ``weight_d = raw_d / mean(介入强度 | 全部)``；
    分母为 0（窗口内没有任何介入）时**不产出权重**（如实标注 "no_signal"）——
    因为"没有介入"推不出难度差异。

    样本量不足（任一档 < `MIN_SAMPLES_PER_STRATUM`）时仍给出均值与权重（供披露），
    但 ``usable=False``、``status="insufficient_samples"``。
    """
    by_stratum: Dict[str, List[float]] = {d: [] for d in ACR.DIFFICULTIES}
    for row in observations:
        difficulty = str(row.get("difficulty") or "")
        if difficulty not in by_stratum:
            continue
        try:
            by_stratum[difficulty].append(float(row.get("intervention_weight") or 0.0))
        except (TypeError, ValueError):
            continue
    samples = {d: len(v) for d, v in by_stratum.items()}
    total = sum(samples.values())
    source = "task.closed + intervention（§6.6 事件流）"
    caseset_sha = ""
    if l2_baseline:
        caseset_sha = str((l2_baseline.get("caseset") or {}).get("caseset_sha256") or "")
        source = f"{source}；基线窗口 {l2_baseline.get('window')}"
    fit = DifficultyFit(
        fitted_at=str(now or time.strftime("%Y-%m-%dT%H:%M:%S%z")),
        method=("weight_d = mean(介入强度|难度=d) / mean(介入强度|全部)；"
                "介入强度口径 = §6.1 七项 + 扩展项（与 ACR 分子同源）"),
        sample_count=total, samples_by_difficulty=samples,
        l2_caseset_sha256=caseset_sha, source=source,
        notes=DISCLOSURE_OFF if not os.getenv(FIT_ENV, "0") == "1" else DISCLOSURE_ON,
    )
    if not total:
        fit.status = "no_observations"
        fit.notes = ("窗口内无 task.closed 观测（或难度字段全空）→ 拒绝产出权重；"
                     + DISCLOSURE_OFF)
        return fit
    means = {d: (statistics.fmean(v) if v else 0.0) for d, v in by_stratum.items()}
    overall = statistics.fmean([w for v in by_stratum.values() for w in v])
    fit.means_by_difficulty = means
    if overall <= 0:
        fit.status = "no_signal"
        fit.notes = ("窗口内介入强度全为 0（无人工介入）→ 无信号可拟合，保持等权；"
                     + DISCLOSURE_OFF)
        return fit
    fit.weights = {d: round(means[d] / overall, 6) for d in ACR.DIFFICULTIES}
    adequate = all(samples[d] >= MIN_SAMPLES_PER_STRATUM for d in ACR.DIFFICULTIES)
    fit.usable = bool(adequate)
    fit.status = "fitted" if adequate else "insufficient_samples"
    if not adequate:
        fit.notes = (f"{DISCLOSURE_OFF}；样本不足（各档需 ≥{MIN_SAMPLES_PER_STRATUM} 条）："
                     f"{samples} → 权重仅供披露，**不得**用于考核")
    return fit


def write_fit(fit: DifficultyFit, path: str = "") -> str:
    """写出拟合件（先过 `anchor.guard_write`）"""
    target = path or DEFAULT_FIT_PATH
    A.guard_write(target)
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(fit.to_dict(), ensure_ascii=False, indent=2) + "\n")
    return target


def load_fit(path: str = "") -> Optional[DifficultyFit]:
    """读拟合件（缺失/非法 → ``None``）"""
    target = path or DEFAULT_FIT_PATH
    if not os.path.exists(target):
        return None
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("拟合件非法（%s）: %s", target, e)
        return None
    try:
        return DifficultyFit.from_dict(data)
    except CalibrationError as e:
        logger.warning("拟合件口径不符（%s）: %s", target, e)
        return None


# ════════════════════════════════════════════════════════════
#  切换闸门
# ════════════════════════════════════════════════════════════


def switch_status(*, fit: Optional[DifficultyFit] = None,
                  l2_baseline: Optional[Mapping[str, Any]] = None,
                  env: Optional[Mapping[str, str]] = None,
                  fit_path: str = "") -> Dict[str, Any]:
    """判定"难度权重能否从启发式切到真实信号"（**四闸全绿**才可切换）

    闸门：
    ① ``l2_baseline_ready``：L2 Core-50 基线就绪（`calibration_trigger.l2_dataset_ready`）；
    ② ``fit_usable``：拟合件可用（每档样本 ≥ 阈值，且已拟合出权重）；
    ③ ``env_enabled``：显式开关 ``CP_ACR_DIFFICULTY_FIT=1``（**默认关闭**）；
    ④ ``fit_matches_baseline``：拟合件的 L2 用例集哈希与当前基线一致（换版即失效）。
    """
    environ = dict(env if env is not None else os.environ)
    resolved_fit = fit if fit is not None else load_fit(fit_path)
    trigger = dict((l2_baseline or {}).get("calibration_trigger") or {})
    baseline_ready = bool(trigger.get("l2_dataset_ready"))
    baseline_sha = str((((l2_baseline or {}).get("caseset") or {})
                        .get("caseset_sha256")) or "")
    gates = {
        "l2_baseline_ready": baseline_ready,
        "fit_usable": bool(resolved_fit and resolved_fit.usable),
        "env_enabled": str(environ.get(FIT_ENV, "0")).strip() == "1",
        "fit_matches_baseline": bool(
            resolved_fit and baseline_sha
            and resolved_fit.l2_caseset_sha256 == baseline_sha),
    }
    allowed = all(gates.values())
    reasons: List[str] = []
    if not gates["l2_baseline_ready"]:
        reasons.append("L2 Core-50 基线未就绪（无基线数据不得拟合考核权重）")
    if not gates["fit_usable"]:
        reasons.append(f"拟合件不可用（缺件或每档样本 < {MIN_SAMPLES_PER_STRATUM}）")
    if not gates["env_enabled"]:
        reasons.append(f"显式开关未打开（{FIT_ENV} != 1）")
    if not gates["fit_matches_baseline"]:
        reasons.append("拟合件与当前 L2 用例集哈希不一致（基线已换版，需重拟合）")
    return {
        "allowed": allowed,
        "mode": "assess（考核：使用拟合难度权重）" if allowed else "disclose_only（只披露不考核）",
        "gates": gates,
        "reasons": reasons,
        "disclosure": DISCLOSURE_ON if allowed else DISCLOSURE_OFF,
        "fit": resolved_fit.to_dict() if resolved_fit else None,
        "env": {FIT_ENV: environ.get(FIT_ENV, "0")},
        "checklist": switch_checklist(),
    }


def switch_checklist() -> List[Dict[str, str]]:
    """切换清单（逐条：动作 / 验证 / 回滚）——"切换路径已定义"的可执行形式"""
    return [
        {
            "step": "1",
            "action": "确认 L2 Core-50 基线就绪：`python scripts/run_eval.py --layer L2 "
                      "--record-baseline`（产出 eval/baselines/l2_baseline.json）",
            "verify": "`agent.eval.baseline.load_l2_baseline()` 的 "
                      "calibration_trigger.l2_dataset_ready == True",
            "rollback": "无需回滚（只读运行）",
        },
        {
            "step": "2",
            "action": "在同一窗口上拟合难度权重：`python scripts/report_slo_weekly.py "
                      "--fit-difficulty --out data/eval/weekly.json`",
            "verify": "拟合件 status == 'fitted' 且 samples_by_difficulty 各档 ≥ "
                      f"{MIN_SAMPLES_PER_STRATUM}",
            "rollback": "删除 data/eval/acr_difficulty_fit.json（闸门自动回到只披露）",
        },
        {
            "step": "3",
            "action": f"打开显式开关 {FIT_ENV}=1 并重启进程（配置走 .env）",
            "verify": "`agent.eval.calibration.switch_status()` 的 allowed == True",
            "rollback": f"{FIT_ENV}=0（无需改代码）",
        },
        {
            "step": "4",
            "action": "在 ACR 汇总层接入难度权重：`acr.record_task_closed(difficulty=...)` "
                      "的难度标签保持不变，权重经 `difficulty_weight()` 应用（接入点："
                      "ACR 汇总的按难度分层视图；**不改既有公开签名**）",
            "verify": "对同一批事件的 ACR 分层视图在切换前后可对账（权重来源标注为 "
                      "fit_version）",
            "rollback": f"{FIT_ENV}=0 → difficulty_weight() 恒返回 1.0（等权）",
        },
        {
            "step": "5",
            "action": "在周报与验收报告中把该项从「披露不考核」改为「考核」，"
                      "并记录拟合件版本与基线哈希",
            "verify": "周报 exploration/difficulty 段落的 disclosure 与闸门一致",
            "rollback": "报告口径回退（保留历史披露）",
        },
    ]


def difficulty_weight(difficulty: str, *, fit: Optional[DifficultyFit] = None,
                      env: Optional[Mapping[str, str]] = None,
                      l2_baseline: Optional[Mapping[str, Any]] = None) -> float:
    """难度权重查询：闸门未全绿 → **恒 1.0（等权，只披露不考核）**"""
    status = switch_status(fit=fit, l2_baseline=l2_baseline, env=env)
    if not status["allowed"]:
        return 1.0
    weights = (status.get("fit") or {}).get("weights") or {}
    try:
        return float(weights.get(str(difficulty), 1.0))
    except (TypeError, ValueError):
        return 1.0


def calibration_report(*, observations: Sequence[Mapping[str, Any]] = (),
                       l2_baseline: Optional[Mapping[str, Any]] = None,
                       fit: Optional[DifficultyFit] = None,
                       env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """拟合 + 闸门的一次性报告（周报/验收报告引用）"""
    resolved = fit if fit is not None else (
        fit_difficulty_weights(observations, l2_baseline=l2_baseline)
        if observations else load_fit())
    status = switch_status(fit=resolved, l2_baseline=l2_baseline, env=env)
    return {
        "s2_03_item": "#3 ACR 意图/难度启发式 → 真实信号",
        "fit": resolved.to_dict() if resolved else None,
        "gate": status,
        "disclosure": status["disclosure"],
    }


__all__ = [
    "FIT_ENV", "FIT_VERSION", "MIN_SAMPLES_PER_STRATUM", "DEFAULT_FIT_PATH",
    "DISCLOSURE_ON", "DISCLOSURE_OFF", "CalibrationError", "DifficultyFit",
    "difficulty_observations", "fit_difficulty_weights", "write_fit", "load_fit",
    "switch_status", "switch_checklist", "difficulty_weight", "calibration_report",
]
