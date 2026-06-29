"""评估 Agent——定期抽样评分系统健康度"""
import logging
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


@dataclass
class HealthScore:
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    overall: float = 1.0
    dimensions: dict = field(default_factory=lambda: {
        "response_time": 1.0, "error_rate": 1.0, "tool_success": 1.0,
    })
    issues: list[str] = field(default_factory=list)

class HealthAssessor:
    def __init__(self):
        self._history: list[HealthScore] = []

    def assess(self, metrics: dict = None) -> HealthScore:
        score = HealthScore()
        if metrics:
            avg = metrics.get("avg_response_ms", 0)
            if avg > 10000:
                score.dimensions["response_time"] = 0.3
                score.issues.append("响应时间超10秒")
            elif avg > 5000:
                score.dimensions["response_time"] = 0.6

            err = metrics.get("error_rate", 0)
            if err > 0.2:
                score.dimensions["error_rate"] = 0.2
                score.issues.append(f"错误率: {err:.1%}")
            elif err > 0.1:
                score.dimensions["error_rate"] = 0.6

        score.overall = sum(score.dimensions.values()) / len(score.dimensions)
        self._history.append(score)
        if len(self._history) > 100:
            self._history.pop(0)
        return score

    def get_history(self, n: int = 10) -> list[HealthScore]:
        return self._history[-n:]

health_assessor = HealthAssessor()


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "assessor",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
