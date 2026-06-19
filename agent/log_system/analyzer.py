"""
分析层 — 两阶段分析引擎

第一阶段（规则引擎）：实时、轻量级规则匹配与标记
第二阶段（统计引擎）：周期性深度统计分析与异常检测

产出物：标记后的异常数据、趋势报告、模式发现，准备好送入内省学习层
"""

import time
import logging
import json
import statistics
from collections import defaultdict, Counter
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime, timezone

from .storage import get_storage
from .models import (
    LogLevel, LogCategory, LogEntry,
    LogQuery, LogStats,
)

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
# 第一阶段：规则引擎
# ════════════════════════════════════════════════════════════

class Rule:
    """分析规则基类"""

    def __init__(self, name: str, description: str = "", severity: str = "info"):
        self.name = name
        self.description = description
        self.severity = severity
        self.hit_count = 0

    def evaluate(self, stats: LogStats, context: dict) -> Optional[dict]:
        """评估规则，返回匹配结果或 None"""
        raise NotImplementedError


class ThresholdRule(Rule):
    """阈值规则 — 当指标超过阈值时触发"""

    def __init__(self, name: str, metric: str, threshold: float,
                 operator: str = "gt", description: str = "", severity: str = "warning"):
        super().__init__(name, description, severity)
        self.metric = metric
        self.threshold = threshold
        self.operator = operator  # gt / lt / gte / lte

    def evaluate(self, stats: LogStats, context: dict) -> Optional[dict]:
        """评估阈值"""
        metric_value = context.get(self.metric) or getattr(stats, self.metric, None)
        if metric_value is None:
            return None

        triggered = False
        if self.operator == "gt" and metric_value > self.threshold:
            triggered = True
        elif self.operator == "lt" and metric_value < self.threshold:
            triggered = True
        elif self.operator == "gte" and metric_value >= self.threshold:
            triggered = True
        elif self.operator == "lte" and metric_value <= self.threshold:
            triggered = True

        if triggered:
            self.hit_count += 1
            return {
                'rule': self.name,
                'severity': self.severity,
                'metric': self.metric,
                'value': metric_value,
                'threshold': self.threshold,
                'operator': self.operator,
                'description': self.description,
                'timestamp': time.time(),
            }
        return None


class FrequencyRule(Rule):
    """频率规则 — 当事件出现频率超过阈值时触发"""

    def __init__(self, name: str, field: str, pattern: str,
                 min_count: int = 10, time_window: int = 300,
                 description: str = "", severity: str = "warning"):
        super().__init__(name, description, severity)
        self.field = field
        self.pattern = pattern
        self.min_count = min_count
        self.time_window = time_window
        self._recent_hits = []

    def evaluate(self, stats: LogStats, context: dict) -> Optional[dict]:
        """评估频率"""
        events = context.get('recent_events', [])
        matching = [e for e in events if e.get(self.field) == self.pattern]

        if len(matching) >= self.min_count:
            self.hit_count += 1
            return {
                'rule': self.name,
                'severity': self.severity,
                'field': self.field,
                'pattern': self.pattern,
                'count': len(matching),
                'threshold': self.min_count,
                'time_window': self.time_window,
                'description': self.description,
                'timestamp': time.time(),
            }
        return None


class TrendRule(Rule):
    """趋势规则 — 当指标环比变化超过阈值时触发"""

    def __init__(self, name: str, metric: str,
                 change_pct: float = 0.5, description: str = "",
                 severity: str = "warning"):
        super().__init__(name, description, severity)
        self.metric = metric
        self.change_pct = change_pct

    def evaluate(self, stats: LogStats, context: dict) -> Optional[dict]:
        """评估趋势变化"""
        current = context.get(f'{self.metric}_current')
        previous = context.get(f'{self.metric}_previous')
        if current is None or previous is None or previous == 0:
            return None

        change = abs(current - previous) / previous
        if change >= self.change_pct:
            direction = "上升" if current > previous else "下降"
            self.hit_count += 1
            return {
                'rule': self.name,
                'severity': self.severity,
                'metric': self.metric,
                'change_pct': change,
                'direction': direction,
                'current': current,
                'previous': previous,
                'description': self.description,
                'timestamp': time.time(),
            }
        return None


class RuleEngine:
    """规则引擎 — 管理与执行所有分析规则"""

    def __init__(self):
        self.rules: List[Rule] = []
        self._setup_default_rules()

    def _setup_default_rules(self):
        """设置默认规则"""
        self.rules = [
            # 性能阈值规则
            ThresholdRule("high_p95_latency", "p95_duration_ms", 5000,
                          description="P95 响应时间超过 5 秒", severity="warning"),
            ThresholdRule("high_error_rate", "error_rate", 0.1,
                          description="错误率超过 10%", severity="critical"),
            ThresholdRule("high_p99_latency", "p99_duration_ms", 10000,
                          description="P99 响应时间超过 10 秒", severity="critical"),
            # 趋势规则
            TrendRule("error_rate_spike", "error_rate", change_pct=0.5,
                      description="错误率环比增长超 50%", severity="warning"),
            TrendRule("volume_drop", "total_count", change_pct=0.4,
                      description="日志总量环比下降超 40%（可能采集异常）", severity="warning"),
        ]

    def add_rule(self, rule: Rule):
        """添加自定义规则"""
        self.rules.append(rule)

    def evaluate_all(self, stats: LogStats, context: dict = None) -> List[dict]:
        """评估所有规则，返回触发的规则结果列表"""
        ctx = context or {}
        results = []
        for rule in self.rules:
            try:
                result = rule.evaluate(stats, ctx)
                if result:
                    results.append(result)
                    logger.info("[LogAnalyzer] 规则触发: %s - %s (value=%s)",
                                rule.name, rule.description, result.get('value'))
            except Exception as e:
                logger.warning("[LogAnalyzer] 规则 %s 评估异常: %s", rule.name, e)
        return results


# ════════════════════════════════════════════════════════════
# 第二阶段：统计引擎
# ════════════════════════════════════════════════════════════

class StatsEngine:
    """统计引擎 — 趋势识别、异常检测、模式发现"""

    def __init__(self):
        self.storage = get_storage()

    def compute_trends(self, hours: float = 24) -> Dict[str, Any]:
        """趋势识别 — 计算各指标趋势"""
        if not self.storage:
            return {'error': '存储未初始化'}

        trends = {}

        # 操作量趋势（每小时）
        op_trend = self.storage.get_metric_trend('operation_count', hours, 60)
        trends['operation_volume'] = {
            'samples': len(op_trend),
            'data': op_trend,
        }

        # 错误趋势
        err_trend = self.storage.get_error_trend(hours, 30)
        trends['error_trend'] = {
            'samples': len(err_trend),
            'data': err_trend,
        }

        # 计算平均耗时趋势
        perf_data = self.storage.get_metric_trend('response_time', hours, 10)
        trends['latency'] = {
            'samples': len(perf_data),
            'data': perf_data,
        }

        return trends

    def detect_anomalies(self, hours: float = 24) -> List[Dict[str, Any]]:
        """异常检测 — 基于标准差识别离群点"""
        if not self.storage:
            return []

        anomalies = []

        # 检查性能指标异常
        perf_records = self.storage.query_performance(limit=1000)
        if perf_records:
            values = [r['value'] for r in perf_records if r.get('value')]
            if len(values) >= 5:
                mean = statistics.mean(values)
                stdev = statistics.stdev(values)
                threshold = 2.5  # 2.5 标准差

                for r in perf_records:
                    val = r.get('value', 0)
                    if abs(val - mean) > threshold * stdev:
                        anomalies.append({
                            'type': 'performance_outlier',
                            'metric': r.get('metric_name'),
                            'value': val,
                            'mean': round(mean, 2),
                            'std': round(stdev, 2),
                            'z_score': round((val - mean) / stdev, 2) if stdev > 0 else 0,
                            'timestamp': r.get('timestamp'),
                            'severity': 'warning' if abs(val - mean) > 3 * stdev else 'info',
                        })

        # 检查错误分布异常
        recent_errors = self.storage.query_errors(limit=500)
        if recent_errors:
            source_counts = Counter(e.get('source', 'unknown') for e in recent_errors)
            total = sum(source_counts.values())
            for source, count in source_counts.most_common(5):
                ratio = count / total if total > 0 else 0
                if ratio > 0.3 and count >= 10:  # 单一来源错误占比超 30%
                    anomalies.append({
                        'type': 'error_concentration',
                        'source': source,
                        'count': count,
                        'ratio': round(ratio, 3),
                        'severity': 'warning',
                        'timestamp': time.time(),
                    })

        return anomalies

    def discover_patterns(self, hours: float = 24) -> List[Dict[str, Any]]:
        """模式发现 — 识别常见操作序列和错误链"""
        if not self.storage:
            return []

        patterns = []
        q = LogQuery(start_time=time.time() - hours * 3600, limit=2000)
        operations = self.storage.query_operations(q)

        if not operations:
            return patterns

        # 按来源分组统计
        source_counts = Counter(op.get('source', 'unknown') for op in operations)
        for source, count in source_counts.most_common(10):
            if count >= 5:
                patterns.append({
                    'type': 'frequent_source',
                    'source': source,
                    'count': count,
                    'ratio': round(count / len(operations), 3),
                })

        # 按操作名称分组
        op_counts = Counter(op.get('operation', 'unknown')[:80] for op in operations)
        for op_name, count in op_counts.most_common(10):
            if count >= 5:
                patterns.append({
                    'type': 'frequent_operation',
                    'operation': op_name,
                    'count': count,
                    'ratio': round(count / len(operations), 3),
                })

        # 耗时高操作
        slow_ops = [op for op in operations if op.get('duration_ms', 0) > 3000]
        if slow_ops:
            slow_by_source = Counter(op.get('source', 'unknown') for op in slow_ops)
            for source, count in slow_by_source.most_common(5):
                patterns.append({
                    'type': 'slow_operation_cluster',
                    'source': source,
                    'count': count,
                    'detail': f"{source} 模块有 {count} 次耗时超过 3 秒的操作",
                })

        return patterns


# ════════════════════════════════════════════════════════════
# 分析器主类
# ════════════════════════════════════════════════════════════

class LogAnalyzer:
    """日志分析器 — 组合规则引擎与统计引擎"""

    def __init__(self):
        self.rule_engine = RuleEngine()
        self.stats_engine = StatsEngine()
        self._last_analysis = {}

    def analyze(self, hours: float = 24) -> Dict[str, Any]:
        """执行完整分析"""
        storage = get_storage()
        if not storage:
            return {'error': '存储未初始化'}

        result = {
            'timestamp': time.time(),
            'time_range_hours': hours,
            'stats': None,
            'rule_hits': [],
            'trends': {},
            'anomalies': [],
            'patterns': [],
            'summary': '',
        }

        # 获取统计
        stats = storage.get_stats(hours)
        result['stats'] = {
            'total_count': stats.total_count,
            'by_category': stats.by_category,
            'by_level': stats.by_level,
            'error_rate': stats.error_rate,
            'avg_duration_ms': round(stats.avg_duration_ms, 2),
            'p95_duration_ms': round(stats.p95_duration_ms, 2),
            'p99_duration_ms': round(stats.p99_duration_ms, 2),
            'top_sources': stats.top_sources[:5],
        }

        # 第一阶段：规则引擎
        context = {
            'recent_events': storage.query_operations(
                LogQuery(start_time=time.time() - 3600, limit=500)
            ),
        }
        rule_hits = self.rule_engine.evaluate_all(stats, context)
        result['rule_hits'] = rule_hits

        # 第二阶段：统计引擎
        result['trends'] = self.stats_engine.compute_trends(hours)
        result['anomalies'] = self.stats_engine.detect_anomalies(hours)
        result['patterns'] = self.stats_engine.discover_patterns(hours)

        # 生成摘要
        summary_parts = []
        summary_parts.append(f"近 {hours} 小时共 {stats.total_count} 条操作日志")
        if stats.error_rate > 0.05:
            summary_parts.append(f"错误率 {stats.error_rate:.1%}")
        if stats.p95_duration_ms > 2000:
            summary_parts.append(f"P95 耗时 {stats.p95_duration_ms:.0f}ms")
        if rule_hits:
            summary_parts.append(f"触发 {len(rule_hits)} 条规则")
        if result['anomalies']:
            summary_parts.append(f"发现 {len(result['anomalies'])} 个异常")
        result['summary'] = '，'.join(summary_parts) if summary_parts else "分析完成，未发现异常"

        self._last_analysis = result
        return result

    def get_llm_candidates(self, analysis_result: dict = None) -> List[Dict[str, Any]]:
        """筛选适合送入 LLM 深度分析的高价值数据"""
        result = analysis_result or self._last_analysis
        if not result:
            return []

        candidates = []

        # 规则触发的高严重性问题
        for hit in result.get('rule_hits', []):
            if hit.get('severity') in ('warning', 'critical'):
                candidates.append({
                    'source': 'rule_engine',
                    'type': 'rule_trigger',
                    'severity': hit['severity'],
                    'content': hit,
                    'priority': 'high' if hit['severity'] == 'critical' else 'medium',
                })

        # 统计异常
        for anomaly in result.get('anomalies', []):
            if anomaly.get('severity') in ('warning', 'critical'):
                candidates.append({
                    'source': 'stats_engine',
                    'type': 'anomaly',
                    'severity': anomaly['severity'],
                    'content': anomaly,
                    'priority': 'high' if anomaly['severity'] == 'critical' else 'medium',
                })

        # 耗时高的异常模式
        for pat in result.get('patterns', []):
            if pat.get('type') == 'slow_operation_cluster':
                candidates.append({
                    'source': 'stats_engine',
                    'type': 'pattern_slow',
                    'severity': 'warning',
                    'content': pat,
                    'priority': 'medium',
                })

        return candidates
