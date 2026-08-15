"""模型成本追踪——记录每次 LLM 调用的 token 消耗和费用"""
import json
import logging
import threading
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_COSTS = {
    "gpt-4": {"input": 0.03, "output": 0.06},
    "gpt-3.5-turbo": {"input": 0.0015, "output": 0.002},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
}

class CostTracker:
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
        # 文件写入在锁外（持锁纪律：锁内无 I/O）
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
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
                "total_calls": total_calls, "daily": daily_snapshot}

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
