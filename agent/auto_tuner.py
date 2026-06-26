#!/usr/bin/env python3
"""
自适应参数调优模块

功能：
- 根据历史数据自动调整 Critic 阈值、重试次数等参数
- 支持多目标优化（质量 vs 速度 vs 成本）
- 每周自动生成调优建议报告
- 人工确认后才能应用新参数
- 结构化日志输出（包含 trace_id、module_name、action、duration_ms）
"""

import os
import json
import time
import sqlite3
import logging
import threading
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger(__name__)


class TunableParam(Enum):
    """可调节参数枚举"""
    CRITIC_THRESHOLD = "critic_threshold"           # Critic 质量阈值
    MAX_RETRIES = "max_retries"                     # 最大重试次数
    TEMPERATURE = "temperature"                     # 模型温度
    TOP_P = "top_p"                                 # Top-p 采样
    MAX_TOKENS = "max_tokens"                       # 最大 token 数
    TOOL_MAX_CONCURRENCY = "tool_max_concurrency"   # 工具最大并发数
    BATCH_SIZE = "batch_size"                       # 批处理大小
    TIMEOUT_SECONDS = "timeout_seconds"             # 超时时间（秒）


class OptimizationObjective(Enum):
    """优化目标枚举"""
    QUALITY = "quality"             # 质量优先
    SPEED = "speed"                 # 速度优先
    COST = "cost"                   # 成本优先
    BALANCED = "balanced"           # 平衡型


class SuggestionStatus(Enum):
    """调优建议状态"""
    PENDING = "pending"             # 待确认
    APPROVED = "approved"           # 已批准
    REJECTED = "rejected"           # 已拒绝
    APPLIED = "applied"             # 已应用
    ROLLED_BACK = "rolled_back"     # 已回滚


@dataclass
class ParameterSnapshot:
    """参数快照（用于回滚）"""
    snapshot_id: str
    params: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    description: str = ""
    metrics: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['created_at_iso'] = datetime.fromtimestamp(self.created_at).isoformat()
        return d


@dataclass
class TuningSuggestion:
    """调优建议"""
    suggestion_id: str
    title: str = ""
    description: str = ""
    objective: str = "balanced"
    current_params: Dict[str, Any] = field(default_factory=dict)
    proposed_params: Dict[str, Any] = field(default_factory=dict)
    expected_impact: Dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    reviewed_at: Optional[float] = None
    applied_at: Optional[float] = None
    reviewer: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['created_at_iso'] = datetime.fromtimestamp(self.created_at).isoformat()
        if self.reviewed_at:
            d['reviewed_at_iso'] = datetime.fromtimestamp(self.reviewed_at).isoformat()
        if self.applied_at:
            d['applied_at_iso'] = datetime.fromtimestamp(self.applied_at).isoformat()
        return d


@dataclass
class TuningReport:
    """调优报告"""
    report_id: str
    period_start: float
    period_end: float
    objective: str = "balanced"
    summary: str = ""
    suggestions: List[TuningSuggestion] = field(default_factory=list)
    metrics_summary: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['created_at_iso'] = datetime.fromtimestamp(self.created_at).isoformat()
        d['period_start_iso'] = datetime.fromtimestamp(self.period_start).isoformat()
        d['period_end_iso'] = datetime.fromtimestamp(self.period_end).isoformat()
        d['suggestions'] = [s.to_dict() for s in self.suggestions]
        return d


class AutoTuner:
    """自适应参数调优器

    根据历史数据自动分析并生成参数调优建议，需人工确认后生效。

    用法:
        tuner = AutoTuner()
        tuner.record_metric("quality_score", 85.0, {"critic_threshold": 70})
        suggestion = tuner.generate_suggestion(objective="quality")
        tuner.approve_suggestion(suggestion.suggestion_id, reviewer="admin")
        tuner.apply_suggestion(suggestion.suggestion_id)
    """

    def __init__(self, storage_path: str = None):
        self.storage_path = storage_path or os.path.join(
            os.path.dirname(__file__), '..', 'data', 'auto_tuning'
        )
        os.makedirs(self.storage_path, exist_ok=True)
        self._db_path = os.path.join(self.storage_path, 'auto_tuning.db')
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._initialized = False

        self._current_params = self._get_default_params()
        self._param_ranges = self._get_param_ranges()

        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "auto_tuner",
            "action": "init",
            "storage_path": self.storage_path,
            "duration_ms": 0,
            "level": "INFO"
        }))

    def _get_default_params(self) -> Dict[str, Any]:
        return {
            "critic_threshold": 70,
            "max_retries": 3,
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": 2048,
            "tool_max_concurrency": 5,
            "batch_size": 10,
            "timeout_seconds": 30,
        }

    def _get_param_ranges(self) -> Dict[str, Dict[str, Any]]:
        return {
            "critic_threshold": {"min": 50, "max": 95, "step": 5, "type": "int"},
            "max_retries": {"min": 1, "max": 10, "step": 1, "type": "int"},
            "temperature": {"min": 0.1, "max": 1.5, "step": 0.1, "type": "float"},
            "top_p": {"min": 0.5, "max": 1.0, "step": 0.05, "type": "float"},
            "max_tokens": {"min": 512, "max": 8192, "step": 256, "type": "int"},
            "tool_max_concurrency": {"min": 1, "max": 20, "step": 1, "type": "int"},
            "batch_size": {"min": 1, "max": 100, "step": 5, "type": "int"},
            "timeout_seconds": {"min": 5, "max": 120, "step": 5, "type": "int"},
        }

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            self._local.conn = sqlite3.connect(
                self._db_path, check_same_thread=False
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def initialize(self):
        if self._initialized:
            return

        with self._write_lock, self._get_conn() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tuning_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    metric_name TEXT NOT NULL,
                    value REAL NOT NULL,
                    params_snapshot TEXT DEFAULT '{}',
                    context TEXT DEFAULT '{}',
                    timestamp REAL NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tuning_suggestions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    suggestion_id TEXT NOT NULL UNIQUE,
                    title TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    objective TEXT DEFAULT 'balanced',
                    current_params TEXT DEFAULT '{}',
                    proposed_params TEXT DEFAULT '{}',
                    expected_impact TEXT DEFAULT '{}',
                    confidence REAL DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    created_at REAL NOT NULL,
                    reviewed_at REAL,
                    applied_at REAL,
                    reviewer TEXT DEFAULT '',
                    metadata TEXT DEFAULT '{}'
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS parameter_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT NOT NULL UNIQUE,
                    params TEXT DEFAULT '{}',
                    created_at REAL NOT NULL,
                    description TEXT DEFAULT '',
                    metrics TEXT DEFAULT '{}'
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tuning_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id TEXT NOT NULL UNIQUE,
                    period_start REAL NOT NULL,
                    period_end REAL NOT NULL,
                    objective TEXT DEFAULT 'balanced',
                    summary TEXT DEFAULT '',
                    suggestions TEXT DEFAULT '[]',
                    metrics_summary TEXT DEFAULT '{}',
                    created_at REAL NOT NULL
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_name ON tuning_metrics(metric_name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_time ON tuning_metrics(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_suggestions_status ON tuning_suggestions(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_time ON parameter_snapshots(created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_reports_time ON tuning_reports(created_at)")

            conn.commit()

        self._initialized = True
        self._load_current_params()

        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "auto_tuner",
            "action": "initialize",
            "duration_ms": 0,
            "level": "INFO"
        }))

    def _load_current_params(self):
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT params FROM parameter_snapshots
                       ORDER BY created_at DESC LIMIT 1"""
                )
                row = cursor.fetchone()
                if row:
                    self._current_params = json.loads(row['params'])
                    logger.info(json.dumps({
                        "trace_id": "",
                        "module_name": "auto_tuner",
                        "action": "_load_current_params",
                        "params_count": len(self._current_params),
                        "duration_ms": 0,
                        "level": "INFO"
                    }))
        except Exception as e:
            logger.warning(json.dumps({
                "trace_id": "",
                "module_name": "auto_tuner",
                "action": "_load_current_params",
                "warning": f"加载参数失败，使用默认值: {e}",
                "duration_ms": 0,
                "level": "WARNING"
            }))

    def get_current_params(self) -> Dict[str, Any]:
        self.initialize()
        return dict(self._current_params)

    def set_param(self, param_name: str, value: Any) -> bool:
        self.initialize()

        if param_name not in self._param_ranges:
            raise ValueError(f"不支持的参数: {param_name}")

        pr = self._param_ranges[param_name]
        if value < pr['min'] or value > pr['max']:
            raise ValueError(
                f"参数值超出范围: {param_name}={value}, "
                f"范围: [{pr['min']}, {pr['max']}]"
            )

        self._current_params[param_name] = value
        return True

    def record_metric(self, metric_name: str, value: float,
                      params: Dict[str, Any] = None,
                      context: Dict[str, Any] = None) -> bool:
        self.initialize()
        start_time = time.time()

        try:
            with self._write_lock, self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO tuning_metrics
                       (metric_name, value, params_snapshot, context, timestamp)
                       VALUES (?, ?, ?, ?, ?)""",
                    (metric_name, value,
                     json.dumps(params or self._current_params, ensure_ascii=False),
                     json.dumps(context or {}, ensure_ascii=False),
                     time.time())
                )
                conn.commit()

            duration_ms = (time.time() - start_time) * 1000
            logger.info(json.dumps({
                "trace_id": "",
                "module_name": "auto_tuner",
                "action": "record_metric",
                "metric_name": metric_name,
                "value": value,
                "duration_ms": round(duration_ms, 2),
                "level": "INFO"
            }))
            return True
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "auto_tuner",
                "action": "record_metric",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            raise

    def generate_suggestion(self, objective: str = "balanced",
                            param_name: str = None,
                            days: int = 7) -> Optional[TuningSuggestion]:
        self.initialize()
        start_time = time.time()

        metrics = self._collect_metrics(days)
        if not metrics or len(metrics.get('quality_score', [])) < 10:
            logger.warning(json.dumps({
                "trace_id": "",
                "module_name": "auto_tuner",
                "action": "generate_suggestion",
                "warning": "样本不足，无法生成可靠建议",
                "duration_ms": 0,
                "level": "WARNING"
            }))
            return None

        proposed_params, expected_impact, confidence = self._analyze_and_propose(
            metrics, objective, param_name
        )

        if not proposed_params:
            return None

        import uuid
        suggestion_id = str(uuid.uuid4())[:8]

        obj_name = OptimizationObjective(objective).value if objective in [
            o.value for o in OptimizationObjective] else "balanced"

        suggestion = TuningSuggestion(
            suggestion_id=suggestion_id,
            title=f"{objective}优化建议 - {datetime.now().strftime('%Y-%m-%d')}",
            description=f"基于过去 {days} 天的数据，针对 {obj_name} 目标的参数调优建议",
            objective=objective,
            current_params=dict(self._current_params),
            proposed_params=proposed_params,
            expected_impact=expected_impact,
            confidence=confidence,
            status="pending"
        )

        try:
            with self._write_lock, self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO tuning_suggestions
                       (suggestion_id, title, description, objective,
                        current_params, proposed_params, expected_impact,
                        confidence, status, created_at, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (suggestion.suggestion_id, suggestion.title,
                     suggestion.description, suggestion.objective,
                     json.dumps(suggestion.current_params, ensure_ascii=False),
                     json.dumps(suggestion.proposed_params, ensure_ascii=False),
                     json.dumps(suggestion.expected_impact, ensure_ascii=False),
                     suggestion.confidence, suggestion.status,
                     suggestion.created_at,
                     json.dumps(suggestion.metadata, ensure_ascii=False))
                )
                conn.commit()

            duration_ms = (time.time() - start_time) * 1000
            logger.info(json.dumps({
                "trace_id": "",
                "module_name": "auto_tuner",
                "action": "generate_suggestion",
                "suggestion_id": suggestion_id,
                "objective": objective,
                "confidence": confidence,
                "param_changes": len(proposed_params),
                "duration_ms": round(duration_ms, 2),
                "level": "INFO"
            }))

            return suggestion
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "auto_tuner",
                "action": "generate_suggestion",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            raise

    def _collect_metrics(self, days: int) -> Dict[str, List[float]]:
        since = time.time() - days * 86400

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT metric_name, value FROM tuning_metrics
                   WHERE timestamp >= ? ORDER BY timestamp""",
                (since,)
            )
            rows = cursor.fetchall()

        metrics: Dict[str, List[float]] = {}
        for row in rows:
            name = row['metric_name']
            if name not in metrics:
                metrics[name] = []
            metrics[name].append(row['value'])

        return metrics

    def _analyze_and_propose(self, metrics: Dict[str, List[float]],
                             objective: str, target_param: str = None
                             ) -> Tuple[Dict[str, Any], Dict[str, float], float]:
        proposed = {}
        expected_impact = {}
        confidence = 0.0

        quality_scores = metrics.get('quality_score', [])
        response_times = metrics.get('response_time', [])
        costs = metrics.get('cost', [])

        if quality_scores:
            avg_quality = sum(quality_scores) / len(quality_scores)
        else:
            avg_quality = 70.0

        if response_times:
            avg_response_time = sum(response_times) / len(response_times)
        else:
            avg_response_time = 5.0

        if costs:
            avg_cost = sum(costs) / len(costs)
        else:
            avg_cost = 0.1

        params_to_tune = [target_param] if target_param else list(self._current_params.keys())

        for param_name in params_to_tune:
            if param_name not in self._param_ranges:
                continue

            current_val = self._current_params[param_name]
            pr = self._param_ranges[param_name]
            step = pr['step']

            if objective == "quality":
                new_val = self._tune_for_quality(param_name, current_val, avg_quality, pr)
            elif objective == "speed":
                new_val = self._tune_for_speed(param_name, current_val, avg_response_time, pr)
            elif objective == "cost":
                new_val = self._tune_for_cost(param_name, current_val, avg_cost, pr)
            else:
                new_val = self._tune_balanced(param_name, current_val,
                                               avg_quality, avg_response_time, avg_cost, pr)

            if new_val != current_val:
                proposed[param_name] = new_val

                if param_name == "critic_threshold":
                    diff = new_val - current_val
                    expected_impact['quality_score'] = diff * 0.5
                    expected_impact['response_time'] = diff * 0.1
                elif param_name == "max_retries":
                    diff = new_val - current_val
                    expected_impact['quality_score'] = diff * 2.0
                    expected_impact['cost'] = diff * 0.05
                elif param_name == "temperature":
                    diff = new_val - current_val
                    expected_impact['creativity'] = diff * 10
                    expected_impact['quality_score'] = -abs(diff) * 5
                elif param_name == "max_tokens":
                    diff = new_val - current_val
                    expected_impact['completeness'] = diff * 0.01
                    expected_impact['cost'] = diff * 0.0001
                else:
                    expected_impact[param_name] = new_val - current_val

        sample_size = len(quality_scores)
        if sample_size >= 500:
            confidence = 0.9
        elif sample_size >= 200:
            confidence = 0.75
        elif sample_size >= 50:
            confidence = 0.6
        else:
            confidence = 0.4

        return proposed, expected_impact, confidence

    def _tune_for_quality(self, param_name: str, current: float,
                          avg_quality: float, pr: Dict) -> float:
        if avg_quality < 70:
            if param_name == "critic_threshold":
                return max(pr['min'], current - pr['step'] * 2)
            elif param_name == "max_retries":
                return min(pr['max'], current + pr['step'])
            elif param_name == "max_tokens":
                return min(pr['max'], current + pr['step'] * 4)
        elif avg_quality > 85:
            if param_name == "critic_threshold":
                return min(pr['max'], current + pr['step'])
        return current

    def _tune_for_speed(self, param_name: str, current: float,
                        avg_time: float, pr: Dict) -> float:
        if avg_time > 10:
            if param_name == "max_retries":
                return max(pr['min'], current - pr['step'])
            elif param_name == "max_tokens":
                return max(pr['min'], current - pr['step'] * 4)
            elif param_name == "timeout_seconds":
                return max(pr['min'], current - pr['step'] * 2)
        elif avg_time < 2:
            if param_name == "max_tokens":
                return min(pr['max'], current + pr['step'] * 2)
        return current

    def _tune_for_cost(self, param_name: str, current: float,
                       avg_cost: float, pr: Dict) -> float:
        if avg_cost > 0.5:
            if param_name == "max_tokens":
                return max(pr['min'], current - pr['step'] * 8)
            elif param_name == "max_retries":
                return max(pr['min'], current - pr['step'])
            elif param_name == "temperature":
                return max(pr['min'], current - pr['step'])
        return current

    def _tune_balanced(self, param_name: str, current: float,
                       avg_quality: float, avg_time: float, avg_cost: float,
                       pr: Dict) -> float:
        new_val = current

        if avg_quality < 75 and avg_time < 8:
            if param_name == "max_retries":
                new_val = min(pr['max'], current + pr['step'])
            elif param_name == "max_tokens":
                new_val = min(pr['max'], current + pr['step'] * 2)

        if avg_time > 8 and avg_quality > 80:
            if param_name == "max_retries":
                new_val = max(pr['min'], current - pr['step'])
            elif param_name == "max_tokens":
                new_val = max(pr['min'], current - pr['step'] * 2)

        return new_val

    def list_suggestions(self, status: str = None,
                         limit: int = 20, offset: int = 0) -> List[TuningSuggestion]:
        self.initialize()

        sql = "SELECT * FROM tuning_suggestions WHERE 1=1"
        params = []

        if status:
            sql += " AND status = ?"
            params.append(status)

        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        results = []
        for row in rows:
            results.append(TuningSuggestion(
                suggestion_id=row['suggestion_id'],
                title=row['title'],
                description=row['description'],
                objective=row['objective'],
                current_params=json.loads(row['current_params']),
                proposed_params=json.loads(row['proposed_params']),
                expected_impact=json.loads(row['expected_impact']),
                confidence=row['confidence'],
                status=row['status'],
                created_at=row['created_at'],
                reviewed_at=row['reviewed_at'],
                applied_at=row['applied_at'],
                reviewer=row['reviewer'],
                metadata=json.loads(row['metadata'])
            ))

        return results

    def get_suggestion(self, suggestion_id: str) -> Optional[TuningSuggestion]:
        self.initialize()

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tuning_suggestions WHERE suggestion_id = ?",
                (suggestion_id,)
            )
            row = cursor.fetchone()

        if not row:
            return None

        return TuningSuggestion(
            suggestion_id=row['suggestion_id'],
            title=row['title'],
            description=row['description'],
            objective=row['objective'],
            current_params=json.loads(row['current_params']),
            proposed_params=json.loads(row['proposed_params']),
            expected_impact=json.loads(row['expected_impact']),
            confidence=row['confidence'],
            status=row['status'],
            created_at=row['created_at'],
            reviewed_at=row['reviewed_at'],
            applied_at=row['applied_at'],
            reviewer=row['reviewer'],
            metadata=json.loads(row['metadata'])
        )

    def approve_suggestion(self, suggestion_id: str, reviewer: str = "") -> bool:
        self.initialize()
        start_time = time.time()

        suggestion = self.get_suggestion(suggestion_id)
        if not suggestion:
            raise ValueError(f"建议不存在: {suggestion_id}")

        if suggestion.status != "pending":
            raise ValueError(f"只能审批待确认的建议，当前状态: {suggestion.status}")

        try:
            with self._write_lock, self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """UPDATE tuning_suggestions
                       SET status = ?, reviewed_at = ?, reviewer = ?
                       WHERE suggestion_id = ?""",
                    (SuggestionStatus.APPROVED.value, time.time(), reviewer, suggestion_id)
                )
                conn.commit()

            duration_ms = (time.time() - start_time) * 1000
            logger.info(json.dumps({
                "trace_id": "",
                "module_name": "auto_tuner",
                "action": "approve_suggestion",
                "suggestion_id": suggestion_id,
                "reviewer": reviewer,
                "duration_ms": round(duration_ms, 2),
                "level": "INFO"
            }))
            return True
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "auto_tuner",
                "action": "approve_suggestion",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            raise

    def reject_suggestion(self, suggestion_id: str, reviewer: str = "",
                          reason: str = "") -> bool:
        self.initialize()
        start_time = time.time()

        suggestion = self.get_suggestion(suggestion_id)
        if not suggestion:
            raise ValueError(f"建议不存在: {suggestion_id}")

        if suggestion.status != "pending":
            raise ValueError(f"只能拒绝待确认的建议，当前状态: {suggestion.status}")

        try:
            with self._write_lock, self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """UPDATE tuning_suggestions
                       SET status = ?, reviewed_at = ?, reviewer = ?,
                           metadata = json_set(metadata, '$.reject_reason', ?)
                       WHERE suggestion_id = ?""",
                    (SuggestionStatus.REJECTED.value, time.time(), reviewer,
                     reason, suggestion_id)
                )
                conn.commit()

            duration_ms = (time.time() - start_time) * 1000
            logger.info(json.dumps({
                "trace_id": "",
                "module_name": "auto_tuner",
                "action": "reject_suggestion",
                "suggestion_id": suggestion_id,
                "reviewer": reviewer,
                "reason": reason,
                "duration_ms": round(duration_ms, 2),
                "level": "INFO"
            }))
            return True
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "auto_tuner",
                "action": "reject_suggestion",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            raise

    def apply_suggestion(self, suggestion_id: str) -> Dict[str, Any]:
        self.initialize()
        start_time = time.time()

        suggestion = self.get_suggestion(suggestion_id)
        if not suggestion:
            raise ValueError(f"建议不存在: {suggestion_id}")

        if suggestion.status != "approved":
            raise ValueError(f"只能应用已批准的建议，当前状态: {suggestion.status}")

        old_params = dict(self._current_params)

        import uuid
        snapshot_id = str(uuid.uuid4())[:8]
        self._create_snapshot(snapshot_id, old_params,
                              f"应用建议前快照: {suggestion_id}")

        for param_name, value in suggestion.proposed_params.items():
            self._current_params[param_name] = value

        try:
            with self._write_lock, self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """UPDATE tuning_suggestions
                       SET status = ?, applied_at = ?
                       WHERE suggestion_id = ?""",
                    (SuggestionStatus.APPLIED.value, time.time(), suggestion_id)
                )
                conn.commit()

            self._create_snapshot(
                f"applied_{suggestion_id}",
                dict(self._current_params),
                f"应用建议后快照: {suggestion_id}"
            )

            duration_ms = (time.time() - start_time) * 1000
            logger.info(json.dumps({
                "trace_id": "",
                "module_name": "auto_tuner",
                "action": "apply_suggestion",
                "suggestion_id": suggestion_id,
                "snapshot_id": snapshot_id,
                "param_changes": len(suggestion.proposed_params),
                "duration_ms": round(duration_ms, 2),
                "level": "INFO"
            }))

            return {
                "old_params": old_params,
                "new_params": dict(self._current_params),
                "snapshot_id": snapshot_id,
                "changes": suggestion.proposed_params
            }
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "auto_tuner",
                "action": "apply_suggestion",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            raise

    def _create_snapshot(self, snapshot_id: str, params: Dict[str, Any],
                         description: str = "") -> ParameterSnapshot:
        snapshot = ParameterSnapshot(
            snapshot_id=snapshot_id,
            params=dict(params),
            description=description
        )

        with self._write_lock, self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO parameter_snapshots
                   (snapshot_id, params, created_at, description, metrics)
                   VALUES (?, ?, ?, ?, ?)""",
                (snapshot.snapshot_id,
                 json.dumps(snapshot.params, ensure_ascii=False),
                 snapshot.created_at, snapshot.description,
                 json.dumps(snapshot.metrics, ensure_ascii=False))
            )
            conn.commit()

        return snapshot

    def rollback_to_snapshot(self, snapshot_id: str) -> bool:
        self.initialize()
        start_time = time.time()

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM parameter_snapshots WHERE snapshot_id = ?",
                (snapshot_id,)
            )
            row = cursor.fetchone()

        if not row:
            raise ValueError(f"快照不存在: {snapshot_id}")

        old_params = dict(self._current_params)
        self._current_params = json.loads(row['params'])

        duration_ms = (time.time() - start_time) * 1000
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "auto_tuner",
            "action": "rollback_to_snapshot",
            "snapshot_id": snapshot_id,
            "duration_ms": round(duration_ms, 2),
            "level": "INFO"
        }))

        return True

    def generate_weekly_report(self, objective: str = "balanced") -> TuningReport:
        self.initialize()
        start_time = time.time()

        period_end = time.time()
        period_start = period_end - 7 * 86400

        metrics = self._collect_metrics(7)
        suggestions = [self.generate_suggestion(objective) for _ in range(1)]
        suggestions = [s for s in suggestions if s is not None]

        metrics_summary = {}
        for name, values in metrics.items():
            if values:
                metrics_summary[name] = {
                    "count": len(values),
                    "avg": round(sum(values) / len(values), 4),
                    "min": min(values),
                    "max": max(values)
                }

        import uuid
        report_id = str(uuid.uuid4())[:8]

        report = TuningReport(
            report_id=report_id,
            period_start=period_start,
            period_end=period_end,
            objective=objective,
            summary=f"本周调优报告 - {len(suggestions)} 条建议",
            suggestions=suggestions,
            metrics_summary=metrics_summary
        )

        try:
            with self._write_lock, self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO tuning_reports
                       (report_id, period_start, period_end, objective,
                        summary, suggestions, metrics_summary, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (report.report_id, report.period_start, report.period_end,
                     report.objective, report.summary,
                     json.dumps([s.to_dict() for s in suggestions], ensure_ascii=False),
                     json.dumps(metrics_summary, ensure_ascii=False),
                     report.created_at)
                )
                conn.commit()

            duration_ms = (time.time() - start_time) * 1000
            logger.info(json.dumps({
                "trace_id": "",
                "module_name": "auto_tuner",
                "action": "generate_weekly_report",
                "report_id": report_id,
                "suggestion_count": len(suggestions),
                "duration_ms": round(duration_ms, 2),
                "level": "INFO"
            }))

            return report
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "auto_tuner",
                "action": "generate_weekly_report",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            raise


_global_auto_tuner = None


def get_auto_tuner() -> AutoTuner:
    global _global_auto_tuner
    if _global_auto_tuner is None:
        _global_auto_tuner = AutoTuner()
        _global_auto_tuner.initialize()
    return _global_auto_tuner


__all__ = [
    'TunableParam', 'OptimizationObjective', 'SuggestionStatus',
    'ParameterSnapshot', 'TuningSuggestion', 'TuningReport',
    'AutoTuner', 'get_auto_tuner'
]
