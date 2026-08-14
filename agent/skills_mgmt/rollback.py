"""自动回滚机制（AutoRollback）— 任务 EVO-T6 安全护栏

【任务定位】
    落地设计文档"防止不可逆错误演化"：新版本上线后（评估窗口内）真实指标
    劣化超阈值时，自动从谱系库恢复上一版本，且回滚本身写谱系
    （decision=rolled_back）可审计。

【解决缺陷（来自审计）】
    无自动回滚触发机制（设计文档"防止不可逆错误演化"未落地）。

【触发条件（.env 可配）】
    - 成功率相对下降 > ROLLBACK_SUCCESS_DROP_PCT%（默认 20）；
    - 或 P95 延迟相对上升 > ROLLBACK_LATENCY_RISE_PCT%（默认 50）。
    基线取自上一 committed 谱系记录的 eval_result（metric_provider 可覆盖）。

【安全阀（必须项，防上线→劣化→回滚→再进化抖动循环）】
    - 单对象单日回滚次数上限 ROLLBACK_MAX_DAILY（默认 2）；
    - 超限 → 该对象停止自动进化（halt_callback 触发）并告警；
    - 人工确认后可 resume(object_id) 恢复自动进化。

【配置（.env，全部带默认值）】
    ROLLBACK_MAX_DAILY          单对象单日回滚上限，默认 2
    ROLLBACK_SUCCESS_DROP_PCT   成功率相对下降阈值（%），默认 20
    ROLLBACK_LATENCY_RISE_PCT   P95 延迟相对上升阈值（%），默认 50
    ROLLBACK_WINDOW_MIN         评估窗口（分钟），默认 1440（1 天）
    ROLLBACK_STATE_PATH         回滚状态 JSONL 路径，默认 agent/data/rollback_state.jsonl
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .lineage import EvolutionArchive, EvolutionRecord, get_default_archive
from .observability import logger, emit_metric

_ENV_MAX_DAILY = "ROLLBACK_MAX_DAILY"
_ENV_SUCCESS_DROP_PCT = "ROLLBACK_SUCCESS_DROP_PCT"
_ENV_LATENCY_RISE_PCT = "ROLLBACK_LATENCY_RISE_PCT"
_ENV_ERROR_RISE_PCT = "ROLLBACK_ERROR_RISE_PCT"
_ENV_WINDOW_MIN = "ROLLBACK_WINDOW_MIN"
_ENV_STATE_PATH = "ROLLBACK_STATE_PATH"

_DEFAULT_STATE_PATH = Path(__file__).parent.parent.parent / "data" / "rollback_state.jsonl"


def _env_max_daily() -> int:
    try:
        return max(1, int(os.getenv(_ENV_MAX_DAILY, "2")))
    except (TypeError, ValueError):
        return 2


def _env_success_drop_pct() -> float:
    try:
        return max(0.0, float(os.getenv(_ENV_SUCCESS_DROP_PCT, "20")))
    except (TypeError, ValueError):
        return 20.0


def _env_latency_rise_pct() -> float:
    try:
        return max(0.0, float(os.getenv(_ENV_LATENCY_RISE_PCT, "50")))
    except (TypeError, ValueError):
        return 50.0


def _env_error_rise_pct() -> float:
    try:
        return max(0.0, float(os.getenv(_ENV_ERROR_RISE_PCT, "50")))
    except (TypeError, ValueError):
        return 50.0


def _env_window_min() -> int:
    try:
        return max(1, int(os.getenv(_ENV_WINDOW_MIN, "1440")))
    except (TypeError, ValueError):
        return 1440


def _env_state_path() -> Path:
    return Path(os.getenv(_ENV_STATE_PATH, str(_DEFAULT_STATE_PATH)))


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════

@dataclass
class RollbackResult:
    """一次自动回滚判定的结果"""
    object_id: str = ""
    triggered: bool = False          # 是否触发回滚
    reason: str = ""                 # 触发/未触发原因
    triggered_metric: str = ""       # 触发指标（success_drop / latency_rise）
    suppressed: bool = False         # 安全阀抑制（超限未回滚）
    halted: bool = False             # 本次判定触发熔断（停止该对象自动进化）
    restored: bool = False           # 是否成功恢复上一版本
    parent_version: str = ""         # 恢复到的版本
    record_id: str = ""              # 谱系 rolled_back 记录 ID
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ════════════════════════════════════════════════════════════
#  底层 IO（原子写入 / 损坏容错，与 lineage 同策略）
# ════════════════════════════════════════════════════════════

def _atomic_write_lines(path: Path, lines: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=str(path.parent), suffix=".tmp",
    ) as tmp:
        for line in lines:
            tmp.write(line)
            tmp.write("\n")
        tmp_path = tmp.name
    for attempt in range(3):
        try:
            os.replace(tmp_path, path)
            return
        except OSError:
            if attempt == 2:
                logger.error("[Rollback] 回滚状态写入失败 path=%s（重试 3 次后放弃）", path)
                raise
            time.sleep(0.05)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    logger.warning("[Rollback] %s 跳过损坏行", path)
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("[Rollback] %s 读取失败（按空处理）: %s", path, e)
    return records


# ════════════════════════════════════════════════════════════
#  AutoRollback
# ════════════════════════════════════════════════════════════

class AutoRollback:
    """自动回滚 — 指标劣化判定 + 谱系恢复 + 安全阀（线程安全）

    用法:
        rb = AutoRollback(archive=archive, restorer=restorer_fn,
                          halt_callback=halt_fn)
        result = rb.check_and_rollback("my-skill", "1.2.0",
                                       {"success_rate": 0.4, "p95_latency_ms": 9000})
        if result.triggered:
            ...  # 已自动回滚并写谱系（decision=rolled_back）

    注入约定（简易解耦）:
        restorer: callable(object_id, parent_version, parent_params) -> bool
                  恢复回调（None=仅记录不实际恢复，供无存储环境的只读场景）
        metric_provider: callable(object_id, version) -> {"success_rate", "p95_latency_ms"}
                  当前版本真实指标来源（None=使用传入 metrics 参数）
        halt_callback: callable(object_id, reason)
                  安全阀熔断回调（None=仅记录告警日志）
    """

    def __init__(self, archive: Optional[EvolutionArchive] = None, *,
                 restorer: Optional[Callable[[str, str, Optional[Dict[str, Any]]], bool]] = None,
                 metric_provider: Optional[Callable[[str, str], Optional[Dict[str, Any]]]] = None,
                 halt_callback: Optional[Callable[[str, str], None]] = None,
                 max_daily: Optional[int] = None,
                 success_drop_pct: Optional[float] = None,
                 latency_rise_pct: Optional[float] = None,
                 error_rise_pct: Optional[float] = None,
                 window_min: Optional[int] = None,
                 state_path: Optional[str] = None):
        """Args 见模块 docstring；全部阈值 None=读 .env 默认。"""
        self._archive = archive if archive is not None else get_default_archive()
        self._restorer = restorer
        self._metric_provider = metric_provider
        self._halt_callback = halt_callback
        self._max_daily = max_daily if max_daily is not None else _env_max_daily()
        self._success_drop_pct = (
            success_drop_pct if success_drop_pct is not None else _env_success_drop_pct())
        self._latency_rise_pct = (
            latency_rise_pct if latency_rise_pct is not None else _env_latency_rise_pct())
        self._error_rise_pct = (
            error_rise_pct if error_rise_pct is not None else _env_error_rise_pct())
        self._window_min = window_min if window_min is not None else _env_window_min()
        self._state_path = Path(state_path) if state_path else _env_state_path()
        self._lock = threading.RLock()
        self._state: List[Dict[str, Any]] = []
        self._loaded = False

    # ─── 主入口 ───

    def check_and_rollback(self, object_id: str, current_version: str,
                           metrics: Optional[Dict[str, Any]] = None,
                           trigger: str = "scheduler") -> RollbackResult:
        """判定并执行自动回滚（验收 3 / 4）

        流程:
            1. 安全阀预检：单日回滚次数超限 → suppressed + 熔断（halt）；
            2. 谱系定位上一 committed 版本（基线）；
            3. 指标劣化判定（相对阈值）；
            4. 恢复上一版本（restorer）+ 写谱系 decision=rolled_back。

        Args:
            object_id: 进化对象 ID
            current_version: 当前（疑似劣化）版本
            metrics: 当前版本真实指标 {success_rate, p95_latency_ms, sample_count}
            trigger: 触发来源（scheduler/api），写入谱系
        """
        logger.info(
            "[Rollback] 开始劣化判定 object=%s version=%s trigger=%s "
            "max_daily=%d success_drop>%s%% latency_rise>%s%% error_rise>%s%%",
            object_id, current_version, trigger,
            self._max_daily, self._success_drop_pct, self._latency_rise_pct,
            self._error_rise_pct)
        # 步骤 1: 安全阀预检（守不易：熔断优先于一切判定）
        if self._rollback_count_today(object_id) >= self._max_daily:
            self._halt(object_id, f"单日回滚次数已达上限 {self._max_daily}")
            result = RollbackResult(
                object_id=object_id, triggered=False,
                reason=f"安全阀：单日回滚次数已达上限 {self._max_daily}，停止自动回滚",
                suppressed=True, halted=True,
                details={"max_daily": self._max_daily})
            logger.warning(
                "[Rollback] 安全阀熔断 %s 当日回滚 %d/%d 次已达上限，"
                "停止自动进化并告警",
                object_id, self._rollback_count_today(object_id),
                self._max_daily)
            return result

        # 步骤 2: 定位上一 committed 版本（基线）
        parent = self._find_previous_committed(object_id, current_version)
        if parent is None:
            logger.info(
                "[Rollback] %s v%s 无可回滚基线"
                "（评估窗口 %s 分钟内无上一 committed 版本），跳过",
                object_id, current_version, self._window_min)
            return RollbackResult(
                object_id=object_id, triggered=False,
                reason="谱系中无上一 committed 版本，无法建立基线",
                details={"current_version": current_version})

        # 步骤 3: 指标劣化判定
        baseline = self._baseline_metrics(parent, object_id)
        verdict = self._evaluate_degradation(baseline, metrics)
        if not verdict["triggered"]:
            logger.info(
                "[Rollback] %s v%s 未触发回滚: %s baseline=%s current=%s",
                object_id, current_version, verdict["reason"],
                baseline, metrics or {})
            return RollbackResult(
                object_id=object_id, triggered=False, reason=verdict["reason"],
                details={"current_version": current_version,
                         "baseline": baseline, "current": metrics or {}})

        # 步骤 4: 执行回滚 + 写谱系
        restored = False
        if self._restorer is not None:
            try:
                restored = bool(self._restorer(
                    object_id, parent.new_version, parent.params))
            except Exception as e:  # noqa: BLE001 恢复失败不致命，记录后继续审计
                logger.error("[Rollback] 恢复执行失败 %s → %s: %s",
                             object_id, parent.new_version, e)
        record_id = self._record_rollback(
            object_id, parent, current_version, verdict,
            restored=restored, trigger=trigger)
        self._append_state(object_id, "rollback")
        emit_metric("yunshu_skill_auto_rollback", value=1,
                    labels={"restored": str(restored).lower()})
        logger.warning(
            "[Rollback] 自动回滚 %s v%s→v%s metric=%s delta=%s restored=%s record=%s",
            object_id, current_version, parent.new_version,
            verdict["metric"], verdict.get("delta", {}), restored,
            record_id or "(无)")
        return RollbackResult(
            object_id=object_id, triggered=True,
            reason=verdict["reason"], triggered_metric=verdict["metric"],
            restored=restored, parent_version=parent.new_version,
            record_id=record_id or "",
            details={"current_version": current_version,
                     "baseline": baseline, "current": metrics or {},
                     "delta": verdict.get("delta", {})})

    # ─── 熔断管理（安全阀）───

    def halted_objects(self) -> List[str]:
        """当前被熔断（停止自动进化）的对象列表"""
        with self._lock:
            self._ensure_loaded()
            return [e["object_id"] for e in self._state
                    if e["event"] == "halt"
                    and not any(x["object_id"] == e["object_id"]
                                and x["event"] == "resume"
                                and x["timestamp"] >= e["timestamp"]
                                for x in self._state)]

    def halt(self, object_id: str, reason: str = "人工熔断") -> None:
        """人工熔断（停止该对象自动进化）"""
        self._append_state(object_id, "halt", detail=reason)
        logger.warning("[Rollback] 人工熔断 %s: %s", object_id, reason)

    def resume(self, object_id: str, reason: str = "人工确认恢复") -> None:
        """人工恢复该对象自动进化（重置当日回滚计数）"""
        self._append_state(object_id, "resume", detail=reason)
        logger.info("[Rollback] 恢复 %s 自动进化: %s", object_id, reason)

    def rollback_count_today(self, object_id: str) -> int:
        """该对象当日回滚次数（安全阀查询）"""
        return self._rollback_count_today(object_id)

    # ─── 内部 ───

    def _find_previous_committed(self, object_id: str,
                                 current_version: str) -> Optional[EvolutionRecord]:
        """谱系中找当前版本之前最近一条 committed 记录（基线）

        窗口过滤（EVO-T6 配置 ROLLBACK_WINDOW_MIN，默认 1440 分钟）:
            仅评估窗口内的 committed 记录可作基线——过旧记录不代表当前
            版本的正常表现，与历史表现比较易产生误触发。窗口内无记录
            → 视为无基线，不触发回滚。
        """
        try:
            recs = self._archive.list_by_object(object_id)
        except Exception as e:  # noqa: BLE001 谱系不可用 → 无基线
            logger.warning("[Rollback] 谱系查询失败 %s: %s", object_id, e)
            return None
        cutoff = datetime.now().timestamp() - self._window_min * 60
        for rec in reversed(recs):
            if rec.decision != "committed":
                continue
            if rec.new_version and rec.new_version == current_version:
                continue  # 跳过自身（可能是刚提交的劣化版本）
            ts = self._record_timestamp(rec)
            if ts is not None and ts < cutoff:
                logger.debug(
                    "[Rollback] %s 基线记录 v%s 超出评估窗口"
                    "（created_at 早于 %s 分钟前），跳过",
                    object_id, rec.new_version, self._window_min)
                continue
            logger.debug(
                "[Rollback] %s 基线=谱系 committed 记录 v%s record=%s",
                object_id, rec.new_version, rec.record_id)
            return rec
        logger.debug("[Rollback] %s 无可用 committed 基线（评估窗口内）", object_id)
        return None

    @staticmethod
    def _record_timestamp(rec: EvolutionRecord) -> Optional[float]:
        """谱系记录时间戳（epoch 秒）；缺失/非法 → None（视为窗口内，保守不排除）"""
        raw = getattr(rec, "created_at", None)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw)).timestamp()
        except (ValueError, TypeError):
            logger.debug(
                "[Rollback] 谱系记录时间戳无法解析 created_at=%r，视为窗口内",
                raw)
            return None

    def _baseline_metrics(self, parent: EvolutionRecord,
                          object_id: str) -> Dict[str, Any]:
        """基线指标：metric_provider 优先，否则从谱系 eval_result 提取"""
        if self._metric_provider is not None:
            try:
                provided = self._metric_provider(object_id, parent.new_version) or {}
                if isinstance(provided, dict) and provided:
                    return provided
            except Exception as e:  # noqa: BLE001
                logger.debug("[Rollback] metric_provider 不可用: %s", e)
        dims = {}
        if isinstance(parent.eval_result, dict):
            d = parent.eval_result.get("dimensions")
            if isinstance(d, dict):
                dims = d
        result = {
            "success_rate": dims.get("success_rate"),
            "p95_latency_ms": self._latency_from_dims(dims, parent.eval_result),
            "error_rate": self._error_from_record(dims, parent.eval_result),
        }
        logger.debug("[Rollback] 基线指标 object=%s version=%s baseline=%s",
                     object_id, parent.new_version, result)
        return result

    @staticmethod
    def _error_from_record(dims: Dict[str, Any],
                           eval_result: Optional[Dict[str, Any]]) -> Optional[float]:
        """从维度/评估结果恢复异常率：优先 eval_result 顶层，其次 dimensions。"""
        if isinstance(eval_result, dict) and eval_result.get("error_rate") is not None:
            return float(eval_result["error_rate"])
        return dims.get("error_rate")

    @staticmethod
    def _latency_from_dims(dims: Dict[str, Any],
                           eval_result: Optional[Dict[str, Any]]) -> Optional[float]:
        """从维度/评估结果恢复 P95 延迟近似值

        真实评估 latency_ms 存于 eval_result（latency_ms 字段）；dimensions 仅含
        latency_norm 归一化值，不可直接当 P95 用。优先真实 latency_ms。
        """
        if isinstance(eval_result, dict) and eval_result.get("latency_ms") is not None:
            return float(eval_result["latency_ms"])
        return None

    def _evaluate_degradation(self, baseline: Dict[str, Any],
                              metrics: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """相对阈值劣化判定（验收 3）

        判定（任一命中即触发）:
            - success_drop: (prev_success - cur_success) / prev_success > 阈值
            - latency_rise: (cur_p95 - prev_p95) / prev_p95 > 阈值
            - error_rate_rise: (cur_error - prev_error) / prev_error > 阈值
        """
        m = metrics or {}
        prev_success = baseline.get("success_rate")
        prev_p95 = baseline.get("p95_latency_ms")
        prev_error = baseline.get("error_rate")
        cur_success = m.get("success_rate")
        cur_p95 = m.get("p95_latency_ms")
        cur_error = m.get("error_rate")
        logger.debug(
            "[Rollback] 劣化判定 baseline_success=%s cur_success=%s "
            "baseline_p95=%s cur_p95=%s baseline_error=%s cur_error=%s "
            "阈值=success_drop>%s%%/latency_rise>%s%%/error_rise>%s%%",
            prev_success, cur_success, prev_p95, cur_p95,
            prev_error, cur_error,
            self._success_drop_pct, self._latency_rise_pct, self._error_rise_pct)
        logger.info(
            "[Rollback] 劣化判定输入 baseline=%s current=%s "
            "阈值=success_drop>%s%%/latency_rise>%s%%/error_rise>%s%%",
            baseline, m, self._success_drop_pct, self._latency_rise_pct,
            self._error_rise_pct)
        if prev_success is None and prev_p95 is None and prev_error is None:
            return {"triggered": False, "reason": "基线无可用指标，无法判定劣化",
                    "metric": "", "delta": {}}
        if cur_success is None and cur_p95 is None and cur_error is None:
            return {"triggered": False, "reason": "当前指标缺失，无法判定劣化",
                    "metric": "", "delta": {}}

        delta: Dict[str, Any] = {}
        if (prev_success is not None and cur_success is not None
                and prev_success > 0):
            drop = (prev_success - cur_success) / prev_success
            delta["success_drop_ratio"] = round(drop, 4)
            if drop > self._success_drop_pct / 100.0:
                return {
                    "triggered": True,
                    "metric": "success_drop",
                    "reason": (f"成功率相对下降 {drop * 100:.1f}%"
                               f"（prev={prev_success:.2f} cur={cur_success:.2f}）"
                               f"超过阈值 {self._success_drop_pct}%"),
                    "delta": delta}
        if (prev_p95 is not None and cur_p95 is not None
                and prev_p95 > 0):
            rise = (cur_p95 - prev_p95) / prev_p95
            delta["latency_rise_ratio"] = round(rise, 4)
            if rise > self._latency_rise_pct / 100.0:
                return {
                    "triggered": True,
                    "metric": "latency_rise",
                    "reason": (f"P95 延迟相对上升 {rise * 100:.1f}%"
                               f"（prev={prev_p95:.0f}ms cur={cur_p95:.0f}ms）"
                               f"超过阈值 {self._latency_rise_pct}%"),
                    "delta": delta}
        if (prev_error is not None and cur_error is not None
                and prev_error > 0):
            rise = (cur_error - prev_error) / prev_error
            delta["error_rate_rise_ratio"] = round(rise, 4)
            if rise > self._error_rise_pct / 100.0:
                return {
                    "triggered": True,
                    "metric": "error_rate_rise",
                    "reason": (f"异常率相对上升 {rise * 100:.1f}%"
                               f"（prev={prev_error:.2f} cur={cur_error:.2f}）"
                               f"超过阈值 {self._error_rise_pct}%"),
                    "delta": delta}
        return {"triggered": False,
                "reason": "指标未超回滚阈值",
                "metric": "", "delta": delta}

    def _record_rollback(self, object_id: str, parent: EvolutionRecord,
                         current_version: str, verdict: Dict[str, Any], *,
                         restored: bool, trigger: str) -> Optional[str]:
        """回滚本身写谱系（decision=rolled_back，可审计）"""
        try:
            rec = EvolutionRecord(
                object_type=parent.object_type,
                object_id=object_id,
                parent_record_id=parent.record_id,
                parent_version=parent.new_version,
                new_version=current_version,
                strategy="rollback",
                change_summary=(f"自动回滚到 v{parent.new_version}（指标劣化）"
                                f"{'，已恢复' if restored else '，恢复失败待人工处理'}"),
                decision="rolled_back",
                decision_reason=verdict["reason"],
                trigger=trigger,
                eval_result={"score": parent.get_score(),
                             "metric": verdict["metric"]},
            )
            self._archive.append(rec)
            logger.info(
                "[Rollback] 已写谱系 decision=rolled_back record=%s "
                "object=%s →v%s restored=%s", rec.record_id, object_id,
                parent.new_version, restored)
            return rec.record_id
        except Exception as e:  # noqa: BLE001 谱系写入失败不阻断回滚主流程
            logger.error("[Rollback] 回滚谱系记录失败 %s: %s", object_id, e)
            return None

    def _halt(self, object_id: str, reason: str) -> None:
        """安全阀熔断：记录 + 告警 + 回调（停止该对象自动进化）"""
        self._append_state(object_id, "halt", detail=reason)
        if self._halt_callback is not None:
            try:
                self._halt_callback(object_id, reason)
            except Exception as e:  # noqa: BLE001
                logger.error("[Rollback] halt_callback 失败 %s: %s", object_id, e)

    def _rollback_count_today(self, object_id: str) -> int:
        with self._lock:
            self._ensure_loaded()
            today = date.today().isoformat()
            return sum(
                1 for e in self._state
                if e["object_id"] == object_id
                and e["event"] == "rollback"
                and e["timestamp"].startswith(today))

    def _append_state(self, object_id: str, event: str, detail: str = "") -> None:
        with self._lock:
            self._ensure_loaded()
            self._state.append({
                "object_id": object_id,
                "event": event,
                "detail": detail,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            })
            self._persist_state()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._state = _read_jsonl(self._state_path)
        self._loaded = True

    def _persist_state(self) -> None:
        lines = [json.dumps(e, ensure_ascii=False) for e in self._state]
        _atomic_write_lines(self._state_path, lines)
