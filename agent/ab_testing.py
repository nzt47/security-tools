#!/usr/bin/env python3
"""
A/B 实验框架模块

功能：
- 支持 Prompt 版本、模型参数、工具策略的 A/B 测试
- 自动分流，按用户/会话维度分配实验组
- 指标收集：质量评分、用户反馈、任务成功率
- 实验结果统计分析（显著性检验）
- 实验暂停/恢复/终止
- 结构化日志输出（包含 trace_id、module_name、action、duration_ms）
"""

import os
import json
import time
import math
import sqlite3
import logging
import threading
import hashlib
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger(__name__)


class ExperimentStatus(Enum):
    """实验状态枚举"""
    DRAFT = "draft"           # 草稿
    RUNNING = "running"       # 运行中
    PAUSED = "paused"         # 已暂停
    COMPLETED = "completed"   # 已完成
    TERMINATED = "terminated" # 已终止


class ExperimentType(Enum):
    """实验类型枚举"""
    PROMPT_VERSION = "prompt_version"     # Prompt 版本对比
    MODEL_PARAMS = "model_params"         # 模型参数调优
    TOOL_STRATEGY = "tool_strategy"       # 工具策略对比
    WORKFLOW = "workflow"                 # 工作流对比
    OTHER = "other"                       # 其他类型


class MetricType(Enum):
    """指标类型枚举"""
    QUALITY_SCORE = "quality_score"       # 质量评分
    USER_FEEDBACK = "user_feedback"       # 用户反馈（点赞/点踩）
    TASK_SUCCESS = "task_success"         # 任务成功率
    RESPONSE_TIME = "response_time"       # 响应时间
    COST = "cost"                         # 成本
    RETRY_COUNT = "retry_count"           # 重试次数


@dataclass
class ExperimentVariant:
    """实验变体（实验组）"""
    variant_id: str                       # 变体ID
    name: str                             # 变体名称
    description: str = ""                 # 变体描述
    weight: int = 50                      # 流量权重（0-100）
    config: Dict[str, Any] = field(default_factory=dict)  # 变体配置
    is_control: bool = False              # 是否为对照组


@dataclass
class ExperimentRecord:
    """实验记录"""
    experiment_id: str                    # 实验ID
    name: str                             # 实验名称
    description: str = ""                 # 实验描述
    experiment_type: ExperimentType = ExperimentType.PROMPT_VERSION
    status: ExperimentStatus = ExperimentStatus.DRAFT
    variants: List[ExperimentVariant] = field(default_factory=list)
    target_metric: str = "quality_score"  # 目标指标
    min_samples: int = 100                # 最小样本量
    significance_level: float = 0.05      # 显著性水平
    layer: int = 0                        # 实验层级（0为最高优先级）
    whitelist: List[str] = field(default_factory=list)  # 白名单用户ID
    blacklist: List[str] = field(default_factory=list)  # 黑名单用户ID
    traffic_ratio: float = 1.0            # 流量比例（0.05/0.1/0.2/0.5/1.0）
    auto_stop_threshold: float = 0.2      # 自动熔断阈值（指标恶化超过此比例自动停止）
    max_duration_hours: int = 168         # 最大持续时间（小时）
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    created_by: str = "system"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['experiment_type'] = self.experiment_type.value
        d['status'] = self.status.value
        d['variants'] = [v.__dict__ for v in self.variants]
        d['created_at_iso'] = datetime.fromtimestamp(self.created_at).isoformat()
        if self.started_at:
            d['started_at_iso'] = datetime.fromtimestamp(self.started_at).isoformat()
        if self.ended_at:
            d['ended_at_iso'] = datetime.fromtimestamp(self.ended_at).isoformat()
        return d


@dataclass
class ExperimentMetric:
    """实验指标记录"""
    metric_id: str
    experiment_id: str
    variant_id: str
    trace_id: str
    user_id: str = ""
    session_id: str = ""
    metric_type: str = ""
    value: float = 0.0
    timestamp: float = field(default_factory=time.time)
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['timestamp_iso'] = datetime.fromtimestamp(self.timestamp).isoformat()
        return d


@dataclass
class ExperimentResult:
    """实验结果统计"""
    experiment_id: str
    variant_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    winner: Optional[str] = None
    is_significant: bool = False
    p_value: float = 1.0
    sample_size: int = 0
    analysis_time: float = field(default_factory=time.time)


class ABTestManager:
    """A/B 实验管理器

    支持实验的创建、启动、暂停、终止、分流、指标收集和统计分析。

    用法:
        manager = ABTestManager()
        exp = manager.create_experiment(
            name="Prompt优化实验",
            experiment_type=ExperimentType.PROMPT_VERSION,
            variants=[...]
        )
        manager.start_experiment(exp.experiment_id)
        variant = manager.assign_variant(exp.experiment_id, user_id="user123")
        manager.record_metric(exp.experiment_id, variant.variant_id, "quality_score", 85.0)
        result = manager.analyze_results(exp.experiment_id)
    """

    def __init__(self, storage_path: str = None):
        self.storage_path = storage_path or os.path.join(
            os.path.dirname(__file__), '..', 'data', 'ab_testing'
        )
        os.makedirs(self.storage_path, exist_ok=True)
        self._db_path = os.path.join(self.storage_path, 'ab_testing.db')
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._initialized = False

        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "ab_testing",
            "action": "init",
            "storage_path": self.storage_path,
            "duration_ms": 0,
            "level": "INFO"
        }))

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._create_conn()
        else:
            try:
                self._local.conn.execute("SELECT 1")
            except sqlite3.ProgrammingError:
                self._create_conn()
        return self._local.conn

    def _create_conn(self):
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._local.conn = sqlite3.connect(
            self._db_path, check_same_thread=False
        )
        self._local.conn.row_factory = sqlite3.Row

    def initialize(self):
        if self._initialized:
            return

        with self._write_lock, self._get_conn() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS experiments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    experiment_type TEXT NOT NULL DEFAULT 'prompt_version',
                    status TEXT NOT NULL DEFAULT 'draft',
                    variants TEXT DEFAULT '[]',
                    target_metric TEXT DEFAULT 'quality_score',
                    min_samples INTEGER DEFAULT 100,
                    significance_level REAL DEFAULT 0.05,
                    layer INTEGER DEFAULT 0,
                    whitelist TEXT DEFAULT '[]',
                    blacklist TEXT DEFAULT '[]',
                    traffic_ratio REAL DEFAULT 1.0,
                    auto_stop_threshold REAL DEFAULT 0.2,
                    max_duration_hours INTEGER DEFAULT 168,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    ended_at REAL,
                    created_by TEXT DEFAULT 'system',
                    metadata TEXT DEFAULT '{}'
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS experiment_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    metric_id TEXT NOT NULL UNIQUE,
                    experiment_id TEXT NOT NULL,
                    variant_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    user_id TEXT DEFAULT '',
                    session_id TEXT DEFAULT '',
                    metric_type TEXT NOT NULL,
                    value REAL NOT NULL DEFAULT 0,
                    timestamp REAL NOT NULL,
                    context TEXT DEFAULT '{}'
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_assignments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    variant_id TEXT NOT NULL,
                    assigned_at REAL NOT NULL,
                    UNIQUE(experiment_id, user_id)
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_exp ON experiment_metrics(experiment_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_variant ON experiment_metrics(variant_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_type ON experiment_metrics(metric_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_user ON experiment_metrics(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_assignments_exp ON user_assignments(experiment_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_assignments_user ON user_assignments(user_id)")

            conn.commit()

        self._initialized = True
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "ab_testing",
            "action": "initialize",
            "duration_ms": 0,
            "level": "INFO"
        }))

    def create_experiment(
        self,
        name: str,
        experiment_type: ExperimentType = ExperimentType.PROMPT_VERSION,
        variants: List[ExperimentVariant] = None,
        description: str = "",
        target_metric: str = "quality_score",
        min_samples: int = 100,
        significance_level: float = 0.05,
        layer: int = 0,
        whitelist: List[str] = None,
        blacklist: List[str] = None,
        traffic_ratio: float = 1.0,
        auto_stop_threshold: float = 0.2,
        max_duration_hours: int = 168,
        created_by: str = "system",
        metadata: Dict[str, Any] = None
    ) -> ExperimentRecord:
        self.initialize()

        import uuid
        experiment_id = str(uuid.uuid4())[:8]

        variants = variants or []
        if len(variants) < 2:
            raise ValueError("A/B 实验至少需要 2 个变体")

        total_weight = sum(v.weight for v in variants)
        if total_weight <= 0:
            raise ValueError("变体权重之和必须大于 0")

        if traffic_ratio <= 0 or traffic_ratio > 1.0:
            raise ValueError("流量比例必须在 (0, 1.0] 范围内")

        exp = ExperimentRecord(
            experiment_id=experiment_id,
            name=name,
            description=description,
            experiment_type=experiment_type,
            variants=variants,
            target_metric=target_metric,
            min_samples=min_samples,
            significance_level=significance_level,
            layer=layer,
            whitelist=whitelist or [],
            blacklist=blacklist or [],
            traffic_ratio=traffic_ratio,
            auto_stop_threshold=auto_stop_threshold,
            max_duration_hours=max_duration_hours,
            created_by=created_by,
            metadata=metadata or {}
        )

        try:
            with self._write_lock, self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO experiments
                       (experiment_id, name, description, experiment_type, status,
                        variants, target_metric, min_samples, significance_level,
                        layer, whitelist, blacklist, traffic_ratio, auto_stop_threshold,
                        max_duration_hours, created_at, created_by, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (exp.experiment_id, exp.name, exp.description,
                     exp.experiment_type.value, exp.status.value,
                     json.dumps([v.__dict__ for v in exp.variants], ensure_ascii=False),
                     exp.target_metric, exp.min_samples, exp.significance_level,
                     exp.layer,
                     json.dumps(exp.whitelist, ensure_ascii=False),
                     json.dumps(exp.blacklist, ensure_ascii=False),
                     exp.traffic_ratio, exp.auto_stop_threshold,
                     exp.max_duration_hours,
                     exp.created_at, exp.created_by,
                     json.dumps(exp.metadata, ensure_ascii=False))
                )
                conn.commit()

            logger.info(json.dumps({
                "trace_id": "",
                "module_name": "ab_testing",
                "action": "create_experiment",
                "experiment_id": experiment_id,
                "name": name,
                "variant_count": len(variants),
                "layer": layer,
                "traffic_ratio": traffic_ratio,
                "duration_ms": 0,
                "level": "INFO"
            }))

            return exp
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "ab_testing",
                "action": "create_experiment",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            raise

    def start_experiment(self, experiment_id: str) -> bool:
        self.initialize()
        start_time = time.time()

        exp = self.get_experiment(experiment_id)
        if not exp:
            raise ValueError(f"实验不存在: {experiment_id}")

        if exp.status not in [ExperimentStatus.DRAFT, ExperimentStatus.PAUSED]:
            raise ValueError(f"无法启动实验，当前状态: {exp.status.value}")

        try:
            with self._write_lock, self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """UPDATE experiments SET status = ?, started_at = ?
                       WHERE experiment_id = ?""",
                    (ExperimentStatus.RUNNING.value, time.time(), experiment_id)
                )
                conn.commit()

            duration_ms = (time.time() - start_time) * 1000
            logger.info(json.dumps({
                "trace_id": "",
                "module_name": "ab_testing",
                "action": "start_experiment",
                "experiment_id": experiment_id,
                "duration_ms": round(duration_ms, 2),
                "level": "INFO"
            }))
            return True
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "ab_testing",
                "action": "start_experiment",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            raise

    def pause_experiment(self, experiment_id: str) -> bool:
        self.initialize()
        start_time = time.time()

        exp = self.get_experiment(experiment_id)
        if not exp:
            raise ValueError(f"实验不存在: {experiment_id}")

        if exp.status != ExperimentStatus.RUNNING:
            raise ValueError(f"只能暂停运行中的实验，当前状态: {exp.status.value}")

        try:
            with self._write_lock, self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """UPDATE experiments SET status = ? WHERE experiment_id = ?""",
                    (ExperimentStatus.PAUSED.value, experiment_id)
                )
                conn.commit()

            duration_ms = (time.time() - start_time) * 1000
            logger.info(json.dumps({
                "trace_id": "",
                "module_name": "ab_testing",
                "action": "pause_experiment",
                "experiment_id": experiment_id,
                "duration_ms": round(duration_ms, 2),
                "level": "INFO"
            }))
            return True
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "ab_testing",
                "action": "pause_experiment",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            raise

    def terminate_experiment(self, experiment_id: str, reason: str = "") -> bool:
        self.initialize()
        start_time = time.time()

        exp = self.get_experiment(experiment_id)
        if not exp:
            raise ValueError(f"实验不存在: {experiment_id}")

        if exp.status == ExperimentStatus.TERMINATED:
            return True

        try:
            with self._write_lock, self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """UPDATE experiments SET status = ?, ended_at = ?
                       WHERE experiment_id = ?""",
                    (ExperimentStatus.TERMINATED.value, time.time(), experiment_id)
                )
                conn.commit()

            duration_ms = (time.time() - start_time) * 1000
            logger.info(json.dumps({
                "trace_id": "",
                "module_name": "ab_testing",
                "action": "terminate_experiment",
                "experiment_id": experiment_id,
                "reason": reason,
                "duration_ms": round(duration_ms, 2),
                "level": "INFO"
            }))
            return True
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "ab_testing",
                "action": "terminate_experiment",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            raise

    def get_experiment(self, experiment_id: str) -> Optional[ExperimentRecord]:
        self.initialize()

        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM experiments WHERE experiment_id = ?",
            (experiment_id,)
        )
        row = cursor.fetchone()

        if not row:
            return None

        variants_data = json.loads(row['variants'])
        variants = [ExperimentVariant(**v) for v in variants_data]

        traffic_ratio_val = row['traffic_ratio']
        auto_stop_val = row['auto_stop_threshold']
        max_duration_val = row['max_duration_hours']

        return ExperimentRecord(
            experiment_id=row['experiment_id'],
            name=row['name'],
            description=row['description'],
            experiment_type=ExperimentType(row['experiment_type']),
            status=ExperimentStatus(row['status']),
            variants=variants,
            target_metric=row['target_metric'],
            min_samples=row['min_samples'],
            significance_level=row['significance_level'],
            layer=row['layer'] if 'layer' in row.keys() else 0,
            whitelist=json.loads(row['whitelist']) if 'whitelist' in row.keys() else [],
            blacklist=json.loads(row['blacklist']) if 'blacklist' in row.keys() else [],
            traffic_ratio=traffic_ratio_val,
            auto_stop_threshold=auto_stop_val,
            max_duration_hours=max_duration_val,
            created_at=row['created_at'],
            started_at=row['started_at'],
            ended_at=row['ended_at'],
            created_by=row['created_by'],
            metadata=json.loads(row['metadata'])
        )

    def list_experiments(self, status: ExperimentStatus = None,
                         limit: int = 50, offset: int = 0) -> List[ExperimentRecord]:
        self.initialize()

        sql = "SELECT * FROM experiments WHERE 1=1"
        params = []

        if status:
            sql += " AND status = ?"
            params.append(status.value)

        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        results = []
        for row in rows:
            variants_data = json.loads(row['variants'])
            variants = [ExperimentVariant(**v) for v in variants_data]
            results.append(ExperimentRecord(
                experiment_id=row['experiment_id'],
                name=row['name'],
                description=row['description'],
                experiment_type=ExperimentType(row['experiment_type']),
                status=ExperimentStatus(row['status']),
                variants=variants,
                target_metric=row['target_metric'],
                min_samples=row['min_samples'],
                significance_level=row['significance_level'],
                layer=row['layer'] if 'layer' in row.keys() else 0,
                whitelist=json.loads(row['whitelist']) if 'whitelist' in row.keys() else [],
                blacklist=json.loads(row['blacklist']) if 'blacklist' in row.keys() else [],
                traffic_ratio=row['traffic_ratio'] if 'traffic_ratio' in row.keys() else 1.0,
                auto_stop_threshold=row['auto_stop_threshold'] if 'auto_stop_threshold' in row else 0.2,
                max_duration_hours=row['max_duration_hours'] if 'max_duration_hours' in row.keys() else 168,
                created_at=row['created_at'],
                started_at=row['started_at'],
                ended_at=row['ended_at'],
                created_by=row['created_by'],
                metadata=json.loads(row['metadata'])
            ))

        return results

    def assign_variant(self, experiment_id: str, user_id: str,
                       session_id: str = "") -> Optional[ExperimentVariant]:
        """为用户分配实验变体（确定性分流）

        支持：
        - 白名单用户强制参与
        - 黑名单用户强制排除
        - 流量比例控制
        - 基于哈希的确定性分配（同一用户始终在同一组）
        """
        self.initialize()
        start_time = time.time()

        exp = self.get_experiment(experiment_id)
        if not exp:
            raise ValueError(f"实验不存在: {experiment_id}")

        if exp.status != ExperimentStatus.RUNNING:
            logger.warning(json.dumps({
                "trace_id": "",
                "module_name": "ab_testing",
                "action": "assign_variant",
                "experiment_id": experiment_id,
                "warning": "实验未运行，不进行分流",
                "duration_ms": 0,
                "level": "WARNING"
            }))
            return None

        if user_id in exp.blacklist:
            logger.info(json.dumps({
                "trace_id": "",
                "module_name": "ab_testing",
                "action": "assign_variant",
                "experiment_id": experiment_id,
                "user_id": user_id,
                "reason": "user_in_blacklist",
                "duration_ms": 0,
                "level": "INFO"
            }))
            return None

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT variant_id FROM user_assignments WHERE experiment_id = ? AND user_id = ?",
                (experiment_id, user_id)
            )
            row = cursor.fetchone()

        if row:
            variant_id = row['variant_id']
            for v in exp.variants:
                if v.variant_id == variant_id:
                    return v

        if not self._check_traffic_eligibility(exp, user_id):
            logger.info(json.dumps({
                "trace_id": "",
                "module_name": "ab_testing",
                "action": "assign_variant",
                "experiment_id": experiment_id,
                "user_id": user_id,
                "reason": "not_in_traffic_sample",
                "duration_ms": 0,
                "level": "INFO"
            }))
            return None

        if user_id in exp.whitelist:
            variant = exp.variants[0] if exp.variants else None
        else:
            variant = self._deterministic_assign(exp.variants, user_id, experiment_id)

        if variant:
            try:
                with self._write_lock, self._get_conn() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """INSERT OR IGNORE INTO user_assignments
                           (experiment_id, user_id, variant_id, assigned_at)
                           VALUES (?, ?, ?, ?)""",
                        (experiment_id, user_id, variant.variant_id, time.time())
                    )
                    conn.commit()
            except Exception as e:
                logger.warning(json.dumps({
                    "trace_id": "",
                    "module_name": "ab_testing",
                    "action": "assign_variant",
                    "warning": f"保存分配记录失败: {e}",
                    "duration_ms": 0,
                    "level": "WARNING"
                }))

        duration_ms = (time.time() - start_time) * 1000
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "ab_testing",
            "action": "assign_variant",
            "experiment_id": experiment_id,
            "user_id": user_id,
            "variant_id": variant.variant_id if variant else None,
            "variant_name": variant.name if variant else None,
            "duration_ms": round(duration_ms, 2),
            "level": "INFO"
        }))

        return variant

    def _check_traffic_eligibility(self, exp: ExperimentRecord, user_id: str) -> bool:
        """检查用户是否在流量采样范围内"""
        if exp.traffic_ratio >= 1.0:
            return True

        hash_val = int(hashlib.md5(f"traffic:{exp.experiment_id}:{user_id}".encode('utf-8')).hexdigest(), 16)
        bucket = hash_val % 10000

        return bucket < int(exp.traffic_ratio * 10000)

    def _deterministic_assign(self, variants: List[ExperimentVariant],
                              user_id: str, experiment_id: str) -> ExperimentVariant:
        """确定性分配算法（基于哈希的加权随机）"""
        key = f"{experiment_id}:{user_id}"
        hash_val = int(hashlib.md5(key.encode('utf-8')).hexdigest(), 16)

        total_weight = sum(v.weight for v in variants)
        bucket = hash_val % total_weight

        cumulative = 0
        for v in variants:
            cumulative += v.weight
            if bucket < cumulative:
                return v

        return variants[-1]

    def record_metric(self, experiment_id: str, variant_id: str,
                      metric_type: str, value: float,
                      trace_id: str = "", user_id: str = "",
                      session_id: str = "", context: Dict[str, Any] = None) -> bool:
        self.initialize()
        start_time = time.time()

        import uuid
        metric_id = str(uuid.uuid4())

        try:
            with self._write_lock, self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO experiment_metrics
                       (metric_id, experiment_id, variant_id, trace_id, user_id,
                        session_id, metric_type, value, timestamp, context)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (metric_id, experiment_id, variant_id, trace_id, user_id,
                     session_id, metric_type, value, time.time(),
                     json.dumps(context or {}, ensure_ascii=False))
                )
                conn.commit()

            duration_ms = (time.time() - start_time) * 1000
            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "ab_testing",
                "action": "record_metric",
                "experiment_id": experiment_id,
                "variant_id": variant_id,
                "metric_type": metric_type,
                "value": value,
                "duration_ms": round(duration_ms, 2),
                "level": "INFO"
            }))
            return True
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": trace_id,
                "module_name": "ab_testing",
                "action": "record_metric",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            raise

    def analyze_results(self, experiment_id: str) -> ExperimentResult:
        """分析实验结果（使用双样本 Z 检验进行显著性检验）"""
        self.initialize()
        start_time = time.time()

        exp = self.get_experiment(experiment_id)
        if not exp:
            raise ValueError(f"实验不存在: {experiment_id}")

        result = ExperimentResult(
            experiment_id=experiment_id,
            analysis_time=time.time()
        )

        control_variant = None
        for v in exp.variants:
            if v.is_control:
                control_variant = v
                break
        if not control_variant and exp.variants:
            control_variant = exp.variants[0]

        metric_type = exp.target_metric
        variant_stats = {}

        for variant in exp.variants:
            stats = self._get_variant_stats(experiment_id, variant.variant_id, metric_type)
            variant_stats[variant.variant_id] = {
                "variant_name": variant.name,
                "is_control": variant.is_control,
                **stats
            }
            result.sample_size += stats['count']

        result.variant_results = variant_stats

        if control_variant and len(exp.variants) >= 2:
            control_stats = variant_stats.get(control_variant.variant_id, {})
            control_mean = control_stats.get('mean', 0)
            control_var = control_stats.get('variance', 0)
            control_n = control_stats.get('count', 0)

            best_variant = None
            best_p_value = 1.0

            for variant in exp.variants:
                if variant.variant_id == control_variant.variant_id:
                    continue

                test_stats = variant_stats.get(variant.variant_id, {})
                test_mean = test_stats.get('mean', 0)
                test_var = test_stats.get('variance', 0)
                test_n = test_stats.get('count', 0)

                if control_n > 1 and test_n > 1:
                    z_score, p_value = self._two_sample_z_test(
                        control_mean, control_var, control_n,
                        test_mean, test_var, test_n
                    )
                else:
                    z_score = 0
                    p_value = 1.0

                variant_stats[variant.variant_id]['z_score'] = z_score
                variant_stats[variant.variant_id]['p_value'] = p_value
                variant_stats[variant.variant_id]['significant'] = p_value < exp.significance_level

                if test_mean > control_mean and p_value < best_p_value:
                    best_variant = variant
                    best_p_value = p_value

            if best_variant and best_p_value < exp.significance_level:
                result.winner = best_variant.variant_id
                result.is_significant = True
                result.p_value = best_p_value

        duration_ms = (time.time() - start_time) * 1000
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "ab_testing",
            "action": "analyze_results",
            "experiment_id": experiment_id,
            "sample_size": result.sample_size,
            "is_significant": result.is_significant,
            "winner": result.winner,
            "duration_ms": round(duration_ms, 2),
            "level": "INFO"
        }))

        return result

    def _get_variant_stats(self, experiment_id: str, variant_id: str,
                           metric_type: str) -> Dict[str, Any]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT COUNT(*) as cnt, AVG(value) as avg_val,
                          MIN(value) as min_val, MAX(value) as max_val
                   FROM experiment_metrics
                   WHERE experiment_id = ? AND variant_id = ? AND metric_type = ?""",
                (experiment_id, variant_id, metric_type)
            )
            row = cursor.fetchone()

        count = row['cnt'] if row else 0
        mean = row['avg_val'] if row and row['avg_val'] else 0
        min_val = row['min_val'] if row and row['min_val'] else 0
        max_val = row['max_val'] if row and row['max_val'] else 0

        variance = 0.0
        if count > 1:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT value FROM experiment_metrics
                       WHERE experiment_id = ? AND variant_id = ? AND metric_type = ?""",
                    (experiment_id, variant_id, metric_type)
                )
                rows = cursor.fetchall()

            if rows:
                values = [r['value'] for r in rows]
                variance = sum((x - mean) ** 2 for x in values) / (count - 1)

        return {
            "count": count,
            "mean": round(mean, 4),
            "variance": round(variance, 4),
            "std_dev": round(math.sqrt(variance), 4),
            "min": min_val,
            "max": max_val
        }

    def _two_sample_z_test(self, mean1: float, var1: float, n1: int,
                           mean2: float, var2: float, n2: int) -> Tuple[float, float]:
        """双样本 Z 检验（用于大样本显著性检验）

        Returns:
            (z_score, p_value) 元组
        """
        if n1 <= 1 or n2 <= 1:
            return 0.0, 1.0

        se = math.sqrt(var1 / n1 + var2 / n2)
        if se == 0:
            return 0.0, 1.0

        z_score = (mean2 - mean1) / se
        p_value = self._normal_cdf(-abs(z_score)) * 2

        return round(z_score, 4), round(p_value, 6)

    def _normal_cdf(self, x: float) -> float:
        """标准正态分布累积分布函数（近似）"""
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    def get_metrics_by_trace(self, trace_id: str) -> List[ExperimentMetric]:
        self.initialize()

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM experiment_metrics WHERE trace_id = ? ORDER BY timestamp DESC",
                (trace_id,)
            )
            rows = cursor.fetchall()

        results = []
        for row in rows:
            results.append(ExperimentMetric(
                metric_id=row['metric_id'],
                experiment_id=row['experiment_id'],
                variant_id=row['variant_id'],
                trace_id=row['trace_id'],
                user_id=row['user_id'],
                session_id=row['session_id'],
                metric_type=row['metric_type'],
                value=row['value'],
                timestamp=row['timestamp'],
                context=json.loads(row['context'])
            ))

        return results

    def get_layer_experiments(self, layer: int) -> List[ExperimentRecord]:
        """获取指定层级的所有运行中实验"""
        self.initialize()

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM experiments WHERE layer = ? AND status = ?",
                (layer, ExperimentStatus.RUNNING.value)
            )
            rows = cursor.fetchall()

        results = []
        for row in rows:
            variants_data = json.loads(row['variants'])
            variants = [ExperimentVariant(**v) for v in variants_data]
            results.append(ExperimentRecord(
                experiment_id=row['experiment_id'],
                name=row['name'],
                description=row['description'],
                experiment_type=ExperimentType(row['experiment_type']),
                status=ExperimentStatus(row['status']),
                variants=variants,
                target_metric=row['target_metric'],
                min_samples=row['min_samples'],
                significance_level=row['significance_level'],
                layer=row['layer'] if 'layer' in row.keys() else 0,
                whitelist=json.loads(row['whitelist']) if 'whitelist' in row.keys() else [],
                blacklist=json.loads(row['blacklist']) if 'blacklist' in row.keys() else [],
                traffic_ratio=row['traffic_ratio'] if 'traffic_ratio' in row.keys() else 1.0,
                auto_stop_threshold=row['auto_stop_threshold'] if 'auto_stop_threshold' in row else 0.2,
                max_duration_hours=row['max_duration_hours'] if 'max_duration_hours' in row.keys() else 168,
                created_at=row['created_at'],
                started_at=row['started_at'],
                ended_at=row['ended_at'],
                created_by=row['created_by'],
                metadata=json.loads(row['metadata'])
            ))

        return results

    def assign_variant_with_layers(self, user_id: str, session_id: str = "") -> Dict[str, ExperimentVariant]:
        """基于分层实验为用户分配所有层级的变体

        返回: {experiment_id: variant} 字典
        """
        self.initialize()
        assignments = {}

        layers = set()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT layer FROM experiments WHERE status = ?",
                          (ExperimentStatus.RUNNING.value,))
            for row in cursor.fetchall():
                layers.add(row['layer'])

        for layer in sorted(layers):
            experiments = self.get_layer_experiments(layer)
            for exp in experiments:
                variant = self.assign_variant(exp.experiment_id, user_id, session_id)
                if variant:
                    assignments[exp.experiment_id] = variant

        return assignments

    def check_auto_stop(self, experiment_id: str) -> bool:
        """检查实验是否需要自动停止（指标恶化超过阈值）"""
        self.initialize()

        exp = self.get_experiment(experiment_id)
        if not exp:
            return False

        if exp.status != ExperimentStatus.RUNNING:
            return False

        if exp.started_at and exp.max_duration_hours > 0:
            elapsed_hours = (time.time() - exp.started_at) / 3600
            if elapsed_hours >= exp.max_duration_hours:
                logger.warning(json.dumps({
                    "trace_id": "",
                    "module_name": "ab_testing",
                    "action": "check_auto_stop",
                    "experiment_id": experiment_id,
                    "reason": "max_duration_exceeded",
                    "elapsed_hours": round(elapsed_hours, 2),
                    "max_duration_hours": exp.max_duration_hours,
                    "level": "WARNING"
                }))
                self.terminate_experiment(experiment_id, reason="max_duration_exceeded")
                return True

        result = self.analyze_results(experiment_id)
        if not result.variant_results:
            return False

        control_variant = None
        for v in exp.variants:
            if v.is_control:
                control_variant = v
                break
        if not control_variant and exp.variants:
            control_variant = exp.variants[0]

        if not control_variant:
            return False

        control_stats = result.variant_results.get(control_variant.variant_id, {})
        control_mean = control_stats.get('mean', 0)

        for variant in exp.variants:
            if variant.variant_id == control_variant.variant_id:
                continue

            variant_stats = result.variant_results.get(variant.variant_id, {})
            variant_mean = variant_stats.get('mean', 0)

            if control_mean > 0:
                degradation_ratio = (control_mean - variant_mean) / control_mean
                if degradation_ratio > exp.auto_stop_threshold:
                    logger.warning(json.dumps({
                        "trace_id": "",
                        "module_name": "ab_testing",
                        "action": "check_auto_stop",
                        "experiment_id": experiment_id,
                        "variant_id": variant.variant_id,
                        "reason": "metric_degradation",
                        "control_mean": control_mean,
                        "variant_mean": variant_mean,
                        "degradation_ratio": round(degradation_ratio, 4),
                        "threshold": exp.auto_stop_threshold,
                        "level": "WARNING"
                    }))
                    self.terminate_experiment(experiment_id, reason="metric_degradation")
                    return True

        return False

    def get_trend_data(self, experiment_id: str, metric_type: str,
                       interval_hours: int = 1) -> Dict[str, List[Dict[str, Any]]]:
        """获取实验指标趋势数据

        Args:
            experiment_id: 实验ID
            metric_type: 指标类型
            interval_hours: 时间间隔（小时）

        Returns:
            {variant_id: [{'timestamp': ..., 'count': ..., 'mean': ...}, ...]}
        """
        self.initialize()

        exp = self.get_experiment(experiment_id)
        if not exp:
            return {}

        results = {}
        for variant in exp.variants:
            results[variant.variant_id] = []

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT variant_id, timestamp, value
                   FROM experiment_metrics
                   WHERE experiment_id = ? AND metric_type = ?
                   ORDER BY timestamp ASC""",
                (experiment_id, metric_type)
            )
            rows = cursor.fetchall()

        if not rows:
            return results

        intervals = {}
        for row in rows:
            interval_start = (row['timestamp'] // (interval_hours * 3600)) * (interval_hours * 3600)
            key = (row['variant_id'], interval_start)
            if key not in intervals:
                intervals[key] = {'values': [], 'count': 0}
            intervals[key]['values'].append(row['value'])
            intervals[key]['count'] += 1

        for (variant_id, interval_start), data in intervals.items():
            mean_val = sum(data['values']) / len(data['values']) if data['values'] else 0
            results[variant_id].append({
                'timestamp': interval_start,
                'timestamp_iso': datetime.fromtimestamp(interval_start).isoformat(),
                'count': data['count'],
                'mean': round(mean_val, 4),
                'min': min(data['values']),
                'max': max(data['values'])
            })

        for variant_id in results:
            results[variant_id].sort(key=lambda x: x['timestamp'])

        return results

    def generate_conclusion(self, experiment_id: str) -> Dict[str, Any]:
        """生成实验结论报告"""
        self.initialize()

        exp = self.get_experiment(experiment_id)
        if not exp:
            return {"error": "实验不存在"}

        result = self.analyze_results(experiment_id)
        trend_data = self.get_trend_data(experiment_id, exp.target_metric, interval_hours=24)

        conclusion = {
            "experiment_id": experiment_id,
            "experiment_name": exp.name,
            "experiment_type": exp.experiment_type.value,
            "status": exp.status.value,
            "created_at": datetime.fromtimestamp(exp.created_at).isoformat(),
            "started_at": datetime.fromtimestamp(exp.started_at).isoformat() if exp.started_at else None,
            "ended_at": datetime.fromtimestamp(exp.ended_at).isoformat() if exp.ended_at else None,
            "target_metric": exp.target_metric,
            "significance_level": exp.significance_level,
            "min_samples": exp.min_samples,
            "sample_size": result.sample_size,
            "is_significant": result.is_significant,
            "p_value": result.p_value,
            "winner": result.winner,
            "variant_results": result.variant_results,
            "trend_data": trend_data,
            "recommendations": []
        }

        if result.sample_size < exp.min_samples:
            conclusion["recommendations"].append({
                "type": "insufficient_sample",
                "message": f"当前样本量({result.sample_size})低于最小样本量({exp.min_samples})，建议继续收集数据",
                "severity": "warning"
            })

        if result.is_significant and result.winner:
            conclusion["recommendations"].append({
                "type": "significant_winner",
                "message": f"实验结果显著，建议将{result.winner}方案全量上线",
                "severity": "success"
            })
        elif result.is_significant:
            conclusion["recommendations"].append({
                "type": "significant_no_winner",
                "message": "实验结果显著，但未找到最优方案，建议进一步分析",
                "severity": "info"
            })
        else:
            conclusion["recommendations"].append({
                "type": "not_significant",
                "message": "实验结果不显著，建议增加样本量或延长实验时间",
                "severity": "info"
            })

        control_variant = None
        for v in exp.variants:
            if v.is_control:
                control_variant = v
                break
        if control_variant and result.winner:
            control_stats = result.variant_results.get(control_variant.variant_id, {})
            winner_stats = result.variant_results.get(result.winner, {})
            control_mean = control_stats.get('mean', 0)
            winner_mean = winner_stats.get('mean', 0)
            if control_mean > 0:
                improvement = (winner_mean - control_mean) / control_mean * 100
                conclusion["improvement_percent"] = round(improvement, 2)
                conclusion["recommendations"].append({
                    "type": "improvement",
                    "message": f"相对对照组提升 {round(improvement, 2)}%",
                    "severity": "success"
                })

        conclusion["generated_at"] = datetime.fromtimestamp(time.time()).isoformat()

        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "ab_testing",
            "action": "generate_conclusion",
            "experiment_id": experiment_id,
            "is_significant": result.is_significant,
            "winner": result.winner,
            "sample_size": result.sample_size,
            "duration_ms": 0,
            "level": "INFO"
        }))

        return conclusion

    def get_assignment_stats(self, experiment_id: str) -> Dict[str, Any]:
        """获取实验分配统计信息"""
        self.initialize()

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT variant_id, COUNT(*) as cnt
                   FROM user_assignments
                   WHERE experiment_id = ?
                   GROUP BY variant_id""",
                (experiment_id,)
            )
            rows = cursor.fetchall()

        stats = {}
        total = 0
        for row in rows:
            stats[row['variant_id']] = row['cnt']
            total += row['cnt']

        return {
            "total_assignments": total,
            "variant_distribution": stats
        }


_global_ab_test_manager = None


def get_ab_test_manager() -> ABTestManager:
    global _global_ab_test_manager
    if _global_ab_test_manager is None:
        _global_ab_test_manager = ABTestManager()
        _global_ab_test_manager.initialize()
    return _global_ab_test_manager


__all__ = [
    'ExperimentStatus', 'ExperimentType', 'MetricType',
    'ExperimentVariant', 'ExperimentRecord', 'ExperimentMetric',
    'ExperimentResult', 'ABTestManager', 'get_ab_test_manager'
]
