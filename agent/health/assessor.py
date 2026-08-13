"""评估 Agent——定期抽样评分系统健康度"""
import logging
import json
import uuid
import threading
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
    probe_details: dict = field(default_factory=dict)


# 五层探针默认权重（业务重要性，固定不可变）
DEFAULT_WEIGHTS = {
    "l1_process": 0.25,
    "l2_dependency": 0.20,
    "l3_llm_tool": 0.25,
    "l4_business": 0.20,
    "l5_semantic": 0.10,
}

# 哨兵：区分「无参调用」（历史接口返回默认健康分）与「显式传入 None」（无数据禁假满分）
_DEFAULT_METRICS = object()


class HealthAssessor:
    def __init__(self, weights: dict = None):
        self._history: list[HealthScore] = []
        self._weights = dict(weights) if weights else DEFAULT_WEIGHTS
        # Why Lock 保护 _history：append + pop(0) 截断为读-改-写序列，多线程
        # 并发时可能 IndexError（pop from empty）或历史错乱（自愈判定依据被
        # 污染）。模块级单例 health_assessor 被路由/采集/自愈多路调用。
        # 锁内仅内存列表变更与纯计算（无 I/O，持锁纪律）。
        self._lock = threading.Lock()

    def assess(self, metrics=_DEFAULT_METRICS) -> HealthScore:
        """评估健康度

        - 无参调用（历史遗留接口）→ 默认健康分（overall=1.0）
        - 显式 None / 空 dict → 无数据，禁止假满分（overall=None + issues 提示）
        - 传入业务指标 → 响应时间 / 错误率维度评分
        """
        with self._lock:  # 评分计算 + 历史追加/截断整体原子
            if metrics is _DEFAULT_METRICS:
                score = HealthScore()
            elif not metrics:
                score = HealthScore(overall=None, issues=["无数据"])
            else:
                score = HealthScore()
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

    def assess_with_probes(self, probes: dict = None) -> HealthScore:
        """基于五层探针结果计算健康评分

        【不易】评分规则：
        - 权重默认 DEFAULT_WEIGHTS，构造函数可注入覆盖（测试用）
        - available=False 的层不参与平均，其余层按权重重新归一化
        - overall 保留完整精度（不 round），无可用层时置 None
        """
        with self._lock:  # 评分计算 + 历史追加/截断整体原子
            if not probes:
                score = HealthScore(overall=None, issues=["无数据"])
                self._history.append(score)
                if len(self._history) > 100:
                    self._history.pop(0)
                return score

            details: dict = {}
            dimensions: dict = {}
            issues: list[str] = []
            for layer, result in probes.items():
                details[layer] = {
                    "score": result.score,
                    "available": result.available,
                    "detail": result.detail,
                }
                dimensions[layer] = result.score

            available_layers = [l for l, d in details.items() if d["available"]]
            if available_layers:
                total_w = sum(self._weights[l] for l in available_layers)
                overall = sum(self._weights[l] * details[l]["score"]
                              for l in available_layers) / total_w
                for l, d in details.items():
                    if not d["available"]:
                        issues.append(f"{l} 无数据")
            else:
                # 全部不可用：禁止假满分，仅提示无数据
                overall = None
                issues.append("无数据")

            score = HealthScore(
                overall=overall,
                dimensions=dimensions,
                issues=issues,
                probe_details=details,
            )
            self._history.append(score)
            if len(self._history) > 100:
                self._history.pop(0)
        return score

    def get_history(self, n: int = 10) -> list[HealthScore]:
        with self._lock:  # 切片与写并发互斥（快照一致性）
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
