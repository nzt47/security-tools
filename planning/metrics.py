"""规划指标埋点（阶段 4 / D16）

【不易】埋点为增量可观测层：`planning.metrics.enabled=false` 时全部方法静默跳过
  （零行为变化，回滚开关）；`get_metrics()` 汇总接口键结构稳定，供状态面板/
  健康检查复用。
【变易】收集器可注入（默认系统全局收集器 agent.monitoring.metrics /
  agent.monitoring.business_metrics），便于测试隔离与扩展；
  指标名统一 `planning.*` 前缀。
【简易】本地聚合 + 透出双通道：无标签指标走 MetricsCollector（Prometheus 可导出）；
  带 task_type 标签的经验命中率走 BusinessMetricsCollector（支持标签维度）。
"""

import logging
from collections import defaultdict
from typing import Dict, Optional

from agent.monitoring.metrics import get_metrics_collector
from agent.monitoring.business_metrics import get_business_metrics_collector

logger = logging.getLogger(__name__)


class PlanningMetrics:
    """规划指标收集器

    指标清单（阶段 4 规格）：
      planning.plans.total / .success / .failed  计数器（计划执行收尾各 +1）
      planning.iterations_avg / .cost_total / .duration_ms  直方图/计数器（透出）
      planning.experience_hit_rate               按 task_type 标签（查询/命中计数）

    本地聚合提供 get_metrics() 汇总（含 per-task_type 经验命中率）。
    """

    def __init__(self, collector=None, business_collector=None, enabled: bool = True):
        """
        Args:
            collector: 无标签指标收集器（MetricsCollector 兼容：increment_counter/
                       record_latency）；默认系统全局收集器。
            business_collector: 带标签指标收集器（BusinessMetricsCollector 兼容：
                       inc_counter）；默认系统全局业务收集器。
            enabled: 埋点总开关（planning.metrics.enabled，默认 true）。
        """
        self.enabled = bool(enabled)
        self._collector = collector if collector is not None else get_metrics_collector()
        self._business = business_collector if business_collector is not None else get_business_metrics_collector()

        # 本地聚合（get_metrics 汇总 + 状态面板复用）
        self._total = 0
        self._success = 0
        self._failed = 0
        self._iterations: list = []
        self._durations_ms: list = []
        self._costs: list = []
        self._exp_queries: Dict[str, int] = defaultdict(int)
        self._exp_hits: Dict[str, int] = defaultdict(int)

    # ── 埋点入口 ────────────────────────────────────────────────────────────

    def record_decompose(self, duration_ms: float) -> None:
        """规划生成（decompose）耗时埋点：PlanningCore.plan 出口"""
        if not self.enabled:
            return
        try:
            self._collector.record_latency("planning.decompose_duration_ms", float(duration_ms))
        except Exception as e:  # 埋点失败隔离：不阻断主流程
            logger.warning(f"[规划指标] record_decompose 失败: {e}")

    def record_plan_result(self, *, task_type: str, success: bool, iterations: int,
                           duration_ms: float, cost: float = 0.0) -> None:
        """一次计划执行收尾埋点：execute_plan 出口 / ReAct 循环结束点

        Args:
            task_type: 任务类型标签（query/create/analyze/... 复用 reflector 分类）
            success: 是否成功
            iterations: 迭代步数
            duration_ms: 执行耗时（毫秒）
            cost: 成本（USD，ReAct 路径取自 react_result.cost，Plan 路径暂为 0.0）
        """
        if not self.enabled:
            return
        try:
            self._total += 1
            if success:
                self._success += 1
            else:
                self._failed += 1
            self._iterations.append(int(iterations))
            self._durations_ms.append(float(duration_ms))
            self._costs.append(float(cost))

            self._collector.increment_counter("planning.plans.total")
            if success:
                self._collector.increment_counter("planning.plans.success")
            else:
                self._collector.increment_counter("planning.plans.failed")
            self._collector.record_latency("planning.duration_ms", float(duration_ms))
            self._collector.record_latency("planning.iterations_avg", float(iterations))
            self._collector.increment_counter("planning.cost_total", value=float(cost))
        except Exception as e:  # 埋点失败隔离：不阻断主流程
            logger.warning(f"[规划指标] record_plan_result 失败: {e}")

    def record_experience_lookup(self, task_type: str, hit: bool) -> None:
        """经验检索埋点（按 task_type 标签）：decompose/_think 每次 get_advice_for_task

        Args:
            task_type: 任务类型标签
            hit: 是否命中经验（advice 非空）
        """
        if not self.enabled:
            return
        try:
            self._exp_queries[task_type] += 1
            if hit:
                self._exp_hits[task_type] += 1
            # 带 task_type 标签透出（BusinessMetricsCollector 支持标签）
            self._business.inc_counter("planning.experience_queries_total", {"task_type": task_type})
            if hit:
                self._business.inc_counter("planning.experience_hits_total", {"task_type": task_type})
        except Exception as e:  # 埋点失败隔离：不阻断主流程
            logger.warning(f"[规划指标] record_experience_lookup 失败: {e}")

    # ── 汇总观测 ────────────────────────────────────────────────────────────

    def get_metrics(self) -> Dict:
        """规划指标汇总（供状态面板/健康检查复用；关闭时返回 enabled=false）"""
        if not self.enabled:
            return {"enabled": False}
        duration_avg = (sum(self._durations_ms) / len(self._durations_ms)) if self._durations_ms else 0.0
        iterations_avg = (sum(self._iterations) / len(self._iterations)) if self._iterations else 0.0
        by_type = {
            t: {
                "queries": self._exp_queries.get(t, 0),
                "hits": self._exp_hits.get(t, 0),
                "hit_rate": round(self._exp_hits.get(t, 0) / self._exp_queries[t], 4)
                if self._exp_queries.get(t, 0) else 0.0,
            }
            for t in sorted(set(self._exp_queries) | set(self._exp_hits))
        }
        total_queries = sum(self._exp_queries.values())
        total_hits = sum(self._exp_hits.values())
        return {
            "enabled": True,
            "plans": {
                "total": self._total,
                "success": self._success,
                "failed": self._failed,
                "success_rate": round(self._success / self._total, 4) if self._total else 0.0,
            },
            "iterations_avg": round(iterations_avg, 2),
            "iterations_total": sum(self._iterations),
            "cost_total": round(sum(self._costs), 6),
            "duration_ms": {
                "count": len(self._durations_ms),
                "avg": round(duration_avg, 2),
            },
            "experience_hit_rate": {
                "overall": round(total_hits / total_queries, 4) if total_queries else 0.0,
                "by_task_type": by_type,
            },
        }
