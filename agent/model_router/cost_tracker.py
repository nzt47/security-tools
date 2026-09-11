"""模型成本追踪——记录每次 LLM 调用的 token 消耗和费用

## 【TASK-S5-03 · Owner 裁定 D（2026-09-11）：双成本轨收敛到事件流】

**成本唯一数据源 ＝ 事件流**：`agent.observability.utc.record_cost()` → `data/events/`。
本模块原先是并存的**第二成本轨**（独立写 `data/cost_log.jsonl`），裁定后统一为：

1. **停写（默认）**：`CostTracker.record()` **不再**向 `cost_log.jsonl` 追加新记录。
   ``CP_COST_LEGACY_LOG_WRITE=1`` 可临时恢复旧写入行为（**回滚开关**，
   便于处置"收敛后才发现漏账"的情形）。
2. **只读兼容 ≤1 minor**：`_load_existing()` 仍读既有 `cost_log.jsonl`，
   历史数据继续可查（`get_summary()` / `iter_legacy_records()`），
   因此**历史数据不会无法查询**。
3. **归档不删**：本模块**任何情况下不删除**既有 `cost_log.jsonl`（按仓库惯例，旧文件归档保留）。
4. **不得静默丢账**：停写期间一旦 `record()` 被调用，会立即发出
   **告警日志 + 计数 + `metrics.delta` 事件 + 链式审计**
   （`cost.legacy_track.write_attempt`）——"先补报警再收敛"。
   残留的生产调用方据此可被立刻发现，而不是等到对账时才发现账不见了。

口径对账工具：`agent.observability.utc.reconcile_cost_log()`（**已降级为纯对账**，
不再被当作数据源）。

## 口径纪律

`MODEL_COSTS` 仍是**单价表的唯一来源**（`utc.price_usd_per_1k()` 的权威副本），
因此停写不影响归一化成本口径：价格锚定系数沿用本表。
"""

import json
import logging
import os
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

MODEL_COSTS = {
    "gpt-4": {"input": 0.03, "output": 0.06},
    "gpt-3.5-turbo": {"input": 0.0015, "output": 0.002},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
}

#: 旧轨写入回滚开关（**默认关闭 = 停写**；Owner 裁定 D）
ENV_LEGACY_WRITE = "CP_COST_LEGACY_LOG_WRITE"

#: 审计动作名（与 `agent.audit` 台账对齐）
AUDIT_LEGACY_WRITE_ATTEMPT = "cost.legacy_track.write_attempt"
#: 事件指标名（复用既有 `metrics.delta` 事件类型，不新增事件类型）
METRIC_LEGACY_WRITE_ATTEMPT = "cost.legacy_track.write_attempts"

#: 口径版本（与 `agent.observability.utc` 的标注保持一致）
COST_SOURCE_OF_TRUTH = "events"
CALIBRATION_VERSION = "price_anchor.v1"


def legacy_write_enabled(env: Optional[Dict[str, str]] = None) -> bool:
    """旧轨（`cost_log.jsonl`）写入是否已恢复（**默认 False = 停写**）

    非法取值一律回退 ``False``（保持停写，不回退到"会写"）。
    """
    raw = str((os.environ if env is None else env).get(ENV_LEGACY_WRITE, "")
              or "").strip().lower()
    if not raw:
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    logger.warning("%s=%r 非法布尔值，回退默认（停写）", ENV_LEGACY_WRITE, raw)
    return False


#: 停写期间调用 `record()` 的累计次数与"已报警"标记（进程级，锁保护）
_LEGACY_WRITE_ATTEMPTS = 0
_LEGACY_WRITE_ALARMED = False
_LEGACY_TOTAL_LOGGED = 0
_LEGACY_LOCK = threading.Lock()


def reset_legacy_alarm() -> None:
    """清除旧轨告警状态与计数（**测试专用**）"""
    global _LEGACY_WRITE_ATTEMPTS, _LEGACY_WRITE_ALARMED, _LEGACY_TOTAL_LOGGED
    with _LEGACY_LOCK:
        _LEGACY_WRITE_ATTEMPTS = 0
        _LEGACY_WRITE_ALARMED = False
        _LEGACY_TOTAL_LOGGED = 0


def legacy_track_status() -> Dict[str, Any]:
    """旧轨状态（供验收/对账/面板查询）：写开关、路径、告警计数、唯一数据源"""
    with _LEGACY_LOCK:
        attempts = _LEGACY_WRITE_ATTEMPTS
        alarmed = _LEGACY_WRITE_ALARMED
        total_logged = _LEGACY_TOTAL_LOGGED
    return {
        "write_enabled": legacy_write_enabled(),
        "rollback_env": ENV_LEGACY_WRITE,
        "source_of_truth": COST_SOURCE_OF_TRUTH,
        "calibration_version": CALIBRATION_VERSION,
        "singleton_log_path": str(cost_tracker._log_path),  # noqa: SLF001 只读展示
        "suppressed_write_attempts": attempts,
        "suppressed_records_total": total_logged,
        "alarmed": alarmed,
        "note": ("旧轨已停写（Owner 裁定 D）；历史文件只读兼容、归档不删；"
                 "停写期间的 record() 调用会告警并计入 suppressed_write_attempts"),
    }


def _alarm_suppressed_write(tracker: "CostTracker", model: str,
                            tokens_in: int, tokens_out: int) -> None:
    """停写期间的**可见告警**（日志 + 计数 + 事件 + 审计）

    「不得静默丢账」：金额本身已由调用方计入内存统计（`get_summary()`），
    但**不再落旧轨文件**，故必须让"有人还在往旧轨记账"这件事可见。

    本函数 best-effort：任何失败都不影响 `record()` 的主路径。
    """
    global _LEGACY_WRITE_ATTEMPTS, _LEGACY_WRITE_ALARMED, _LEGACY_TOTAL_LOGGED
    with _LEGACY_LOCK:
        _LEGACY_WRITE_ATTEMPTS += 1
        first = not _LEGACY_WRITE_ALARMED
        _LEGACY_WRITE_ALARMED = True
        _LEGACY_TOTAL_LOGGED += 1
        attempts = _LEGACY_WRITE_ATTEMPTS
    if not first:
        logger.debug("[成本轨道收敛] 旧轨 record() 再次被调用（累计 %d 次）", attempts)
        return
    logger.warning(
        "[成本轨道收敛] 检测到仍在调用 CostTracker.record() 的调用方："
        "旧轨 %s 已停写（Owner 裁定 D：成本唯一数据源＝事件流）。"
        "本次调用**未丢账**（内存统计仍更新），但不会落旧文件；"
        "请改用 agent.observability.utc.record_cost()。"
        "如需临时回滚旧行为：%s=1。model=%s tokens_in=%s tokens_out=%s",
        tracker._log_path, ENV_LEGACY_WRITE, model, tokens_in, tokens_out)  # noqa: SLF001
    payload = {
        "path": str(tracker._log_path),
        "model": str(model or ""),
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "source_of_truth": COST_SOURCE_OF_TRUTH,
        "rollback_env": ENV_LEGACY_WRITE,
    }
    try:
        from agent.observability.events import EV_METRICS_DELTA, emit
        emit(EV_METRICS_DELTA,
             {"metric": METRIC_LEGACY_WRITE_ATTEMPT, "value": 1, **payload},
             actor="system", correlation_id="cost_legacy_track",
             idempotency_key=f"cost_legacy_track:first_attempt:{tracker._log_path}")
    except Exception as e:  # noqa: BLE001 best-effort
        logger.debug("旧轨告警事件发射失败: %s", e)
    try:
        from agent.audit.facade import audit as _audit_facade
        _audit_facade.record(AUDIT_LEGACY_WRITE_ATTEMPT, actor="system",
                             subject=f"cost:legacy:{tracker._log_path}",
                             payload=payload, source="agent", status="warn")
    except Exception as e:  # noqa: BLE001 best-effort
        logger.debug("旧轨告警审计写入失败: %s", e)


class CostTracker:
    #: 成本唯一数据源（Owner 裁定 D；供调用方自查）
    source_of_truth = COST_SOURCE_OF_TRUTH

    def __init__(self, log_path: str = "./data/cost_log.jsonl"):
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._daily_stats: dict[str, dict] = {}
        # Why Lock 保护 _daily_stats：record 的「setdefault 初始化→+= 累加」为
        # 读-改-写序列（非原子），多线程并发会丢失费用/调用计数（关键业务数据）。
        # 锁内仅内存 dict 变更，文件写入在锁外（持锁纪律：锁内无 I/O）。
        self._lock = threading.Lock()
        self._load_existing()

    def record(self, model: str, input_tokens: int, output_tokens: int,
               duration_ms: float, task_type: str = "", trace_id: str = ""):
        """记录一次 LLM 调用成本（§6.6 cost 埋点）

        【TASK-S5-03 / 裁定 D】**默认停写** `cost_log.jsonl`：新写入只落事件流
        （`agent.observability.utc.record_cost()`）。内存日聚合仍照常更新，
        故 `get_summary()` 行为不变；旧文件只读兼容、归档不删。

        停写期间的每次调用都会走 `_alarm_suppressed_write()`（首次告警，
        之后计数），确保**不静默丢账**。
        """
        costs = MODEL_COSTS.get(model, {"input": 0.01, "output": 0.02})
        cost = (input_tokens / 1000 * costs["input"] +
                output_tokens / 1000 * costs["output"])
        record = {
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 6),
            "duration_ms": round(duration_ms, 2),
            "task_type": task_type,
            "trace_id": trace_id,
        }
        # 旧轨写入：默认停写；CP_COST_LEGACY_LOG_WRITE=1 可回滚旧行为。
        # 文件写入在锁外（持锁纪律：锁内无 I/O）
        if legacy_write_enabled():
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        else:
            _alarm_suppressed_write(self, model, input_tokens, output_tokens)
        today = date.today().isoformat()
        # setdefault 原子初始化消除「if-in+赋值」TOCTOU；+= 为读-改-写非原子，
        # 锁保护防多线程并发丢更新（费用/调用计数为关键业务数据）
        with self._lock:
            daily = self._daily_stats.setdefault(
                today, {"total_cost": 0, "total_tokens": 0, "calls": 0})
            daily["total_cost"] += cost
            daily["total_tokens"] += input_tokens + output_tokens
            daily["calls"] += 1
        logger.debug(f"[Cost] {model}: {input_tokens}+{output_tokens}tok, ${cost:.6f}")

    def get_summary(self) -> dict:
        # 锁内拷贝快照：避免调用方遍历 daily 时读到并发 record 的半更新值
        with self._lock:
            daily_snapshot = {day: dict(s) for day, s in self._daily_stats.items()}
        total_cost = sum(s["total_cost"] for s in daily_snapshot.values())
        total_calls = sum(s["calls"] for s in daily_snapshot.values())
        return {"total_cost_usd": round(total_cost, 4),
                "total_calls": total_calls, "daily": daily_snapshot,
                "source_of_truth": COST_SOURCE_OF_TRUTH,
                "legacy_write_enabled": legacy_write_enabled()}

    def iter_legacy_records(self) -> Iterator[Dict[str, Any]]:
        """**只读兼容**：逐条读取既有 `cost_log.jsonl`（归档不删，历史可查）

        非 JSON 行、非 dict 行一律跳过（与 `_load_existing()` 同款容错）。
        """
        if not self._log_path.exists():
            return
        try:
            with open(self._log_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(row, dict):
                        yield row
        except OSError as e:  # noqa: BLE001 读取失败 → 如实空读
            logger.warning("旧轨读取失败: %s", e)

    def legacy_records(self) -> List[Dict[str, Any]]:
        """旧轨全部记录（只读兼容；历史数据查询入口）"""
        return list(self.iter_legacy_records())

    def _load_existing(self):
        if not self._log_path.exists():
            return
        try:
            with open(self._log_path, "r", encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line.strip())
                    day = r["timestamp"][:10]
                    if day not in self._daily_stats:
                        self._daily_stats[day] = {"total_cost": 0, "total_tokens": 0, "calls": 0}
                    self._daily_stats[day]["total_cost"] += r["cost_usd"]
                    self._daily_stats[day]["total_tokens"] += r["input_tokens"] + r["output_tokens"]
                    self._daily_stats[day]["calls"] += 1
        except Exception as e:
            logger.warning(f"加载成本日志失败: {e}")

cost_tracker = CostTracker()
