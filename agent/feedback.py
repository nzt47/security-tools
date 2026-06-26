#!/usr/bin/env python3
"""
用户反馈闭环模块

功能：
- 收集用户点赞/点踩反馈
- 将用户反馈关联到具体的 trace_id
- 负面反馈自动进入失败模式分析
- 正面反馈作为优质案例存入知识库
- 反馈数据驱动 Prompt 优化
- 结构化日志输出（包含 trace_id、module_name、action、duration_ms）
"""

import os
import json
import time
import sqlite3
import logging
import threading
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class FeedbackType(Enum):
    """反馈类型枚举"""
    LIKE = "like"           # 点赞（正面反馈）
    DISLIKE = "dislike"     # 点踩（负面反馈）
    REPORT = "report"       # 举报
    SUGGESTION = "suggestion"  # 建议


class FeedbackStatus(Enum):
    """反馈处理状态"""
    PENDING = "pending"           # 待处理
    ANALYZED = "analyzed"         # 已分析
    RESOLVED = "resolved"         # 已解决
    ARCHIVED = "archived"         # 已归档


class FeedbackCategory(Enum):
    """反馈分类"""
    QUALITY = "quality"           # 质量问题
    ACCURACY = "accuracy"         # 准确性问题
    RELEVANCE = "relevance"       # 相关性问题
    COMPLETENESS = "completeness" # 完整性问题
    SPEED = "speed"               # 速度问题
    SAFETY = "safety"             # 安全问题
    USABILITY = "usability"       # 可用性问题
    OTHER = "other"               # 其他


@dataclass
class FeedbackRecord:
    """用户反馈记录"""
    feedback_id: str
    trace_id: str
    feedback_type: str
    rating: int = 0
    comment: str = ""
    category: str = "other"
    user_id: str = ""
    session_id: str = ""
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    analyzed_at: Optional[float] = None
    resolved_at: Optional[float] = None
    analysis_result: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['created_at_iso'] = datetime.fromtimestamp(self.created_at).isoformat()
        d['updated_at_iso'] = datetime.fromtimestamp(self.updated_at).isoformat()
        if self.analyzed_at:
            d['analyzed_at_iso'] = datetime.fromtimestamp(self.analyzed_at).isoformat()
        if self.resolved_at:
            d['resolved_at_iso'] = datetime.fromtimestamp(self.resolved_at).isoformat()
        return d


@dataclass
class QualityCase:
    """优质案例（正面反馈归档）"""
    case_id: str
    trace_id: str
    user_id: str = ""
    feedback_id: str = ""
    title: str = ""
    content_summary: str = ""
    tags: List[str] = field(default_factory=list)
    quality_score: float = 0.0
    created_at: float = field(default_factory=time.time)
    context: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['created_at_iso'] = datetime.fromtimestamp(self.created_at).isoformat()
        return d


class FeedbackManager:
    """用户反馈管理器

    收集、分析、处理用户反馈，形成闭环优化。

    用法:
        manager = FeedbackManager()
        manager.submit_feedback(
            trace_id="trace_123",
            feedback_type="dislike",
            comment="回答不准确",
            category="accuracy"
        )
    """

    def __init__(self, storage_path: str = None):
        self.storage_path = storage_path or os.path.join(
            os.path.dirname(__file__), '..', 'data', 'feedback'
        )
        os.makedirs(self.storage_path, exist_ok=True)
        self._db_path = os.path.join(self.storage_path, 'feedback.db')
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._initialized = False

        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "feedback",
            "action": "init",
            "storage_path": self.storage_path,
            "duration_ms": 0,
            "level": "INFO"
        }))

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
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    feedback_id TEXT NOT NULL UNIQUE,
                    trace_id TEXT NOT NULL,
                    feedback_type TEXT NOT NULL,
                    rating INTEGER DEFAULT 0,
                    comment TEXT DEFAULT '',
                    category TEXT DEFAULT 'other',
                    user_id TEXT DEFAULT '',
                    session_id TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    analyzed_at REAL,
                    resolved_at REAL,
                    analysis_result TEXT DEFAULT '{}',
                    context TEXT DEFAULT '{}',
                    metadata TEXT DEFAULT '{}'
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS quality_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL UNIQUE,
                    trace_id TEXT NOT NULL,
                    user_id TEXT DEFAULT '',
                    feedback_id TEXT DEFAULT '',
                    title TEXT DEFAULT '',
                    content_summary TEXT DEFAULT '',
                    tags TEXT DEFAULT '[]',
                    quality_score REAL DEFAULT 0,
                    created_at REAL NOT NULL,
                    context TEXT DEFAULT '{}',
                    metadata TEXT DEFAULT '{}'
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_trace ON feedback(trace_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_type ON feedback(feedback_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_user ON feedback(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_time ON feedback(created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_quality_trace ON quality_cases(trace_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_quality_tags ON quality_cases(tags)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_quality_time ON quality_cases(created_at)")

            conn.commit()

        self._initialized = True
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "feedback",
            "action": "initialize",
            "duration_ms": 0,
            "level": "INFO"
        }))

    def submit_feedback(self, trace_id: str, feedback_type: str,
                        rating: int = 0, comment: str = "",
                        category: str = "other", user_id: str = "",
                        session_id: str = "",
                        context: Dict[str, Any] = None) -> FeedbackRecord:
        """提交用户反馈

        Args:
            trace_id: 关联的追踪ID
            feedback_type: 反馈类型（like/dislike/report/suggestion）
            rating: 评分（1-5）
            comment: 反馈评论
            category: 反馈分类
            user_id: 用户ID
            session_id: 会话ID
            context: 上下文信息

        Returns:
            FeedbackRecord 反馈记录
        """
        self.initialize()
        start_time = time.time()

        import uuid
        feedback_id = str(uuid.uuid4())[:8]

        record = FeedbackRecord(
            feedback_id=feedback_id,
            trace_id=trace_id,
            feedback_type=feedback_type,
            rating=rating,
            comment=comment,
            category=category,
            user_id=user_id,
            session_id=session_id,
            context=context or {}
        )

        try:
            with self._write_lock, self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO feedback
                       (feedback_id, trace_id, feedback_type, rating, comment,
                        category, user_id, session_id, status, created_at,
                        updated_at, analysis_result, context, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (record.feedback_id, record.trace_id, record.feedback_type,
                     record.rating, record.comment, record.category,
                     record.user_id, record.session_id, record.status,
                     record.created_at, record.updated_at,
                     json.dumps(record.analysis_result, ensure_ascii=False),
                     json.dumps(record.context, ensure_ascii=False),
                     json.dumps(record.metadata, ensure_ascii=False))
                )
                conn.commit()

            # 自动处理：负面反馈进入失败分析，正面反馈存入优质案例
            if feedback_type == FeedbackType.DISLIKE.value:
                self._process_negative_feedback(record)
            elif feedback_type == FeedbackType.LIKE.value:
                self._process_positive_feedback(record)

            duration_ms = (time.time() - start_time) * 1000
            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "feedback",
                "action": "submit_feedback",
                "feedback_id": feedback_id,
                "feedback_type": feedback_type,
                "category": category,
                "duration_ms": round(duration_ms, 2),
                "level": "INFO"
            }))

            return record
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": trace_id,
                "module_name": "feedback",
                "action": "submit_feedback",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            raise

    def _process_negative_feedback(self, record: FeedbackRecord):
        """处理负面反馈：自动进入失败模式分析"""
        try:
            from agent.cognitive.failure_analysis import (
                report_failure, FailureType, FailureSeverity
            )

            # 映射反馈分类到失败类型
            failure_type_map = {
                "accuracy": FailureType.LOGIC_ERROR,
                "quality": FailureType.DATA_INVENTION,
                "relevance": FailureType.CONTEXT_LOSS,
                "completeness": FailureType.FLOW_SKIP,
                "other": FailureType.UNKNOWN,
            }

            failure_type = failure_type_map.get(record.category, FailureType.UNKNOWN)

            # 报告失败
            report_failure(
                trace_id=record.trace_id,
                message=f"用户负面反馈: {record.comment[:200]}",
                source="user_feedback",
                context={
                    "feedback_id": record.feedback_id,
                    "category": record.category,
                    "rating": record.rating,
                    "user_id": record.user_id,
                    "feedback_type": record.feedback_type
                },
                evidence=[record.comment] if record.comment else []
            )

            # 更新反馈分析结果
            analysis_result = {
                "auto_analyzed": True,
                "failure_type": failure_type.value,
                "severity": FailureSeverity.MEDIUM.value,
                "entered_failure_analysis": True,
                "analysis_note": "负面反馈自动进入失败模式分析流程"
            }

            self._update_analysis(record.feedback_id, analysis_result)

            logger.info(json.dumps({
                "trace_id": record.trace_id,
                "module_name": "feedback",
                "action": "_process_negative_feedback",
                "feedback_id": record.feedback_id,
                "failure_type": failure_type.value,
                "duration_ms": 0,
                "level": "INFO"
            }))

        except Exception as e:
            logger.warning(json.dumps({
                "trace_id": record.trace_id,
                "module_name": "feedback",
                "action": "_process_negative_feedback",
                "warning": f"处理负面反馈失败: {e}",
                "feedback_id": record.feedback_id,
                "duration_ms": 0,
                "level": "WARNING"
            }))

    def _process_positive_feedback(self, record: FeedbackRecord):
        """处理正面反馈：存入优质案例知识库"""
        try:
            import uuid
            case_id = str(uuid.uuid4())[:8]

            title = f"优质案例 - {record.category}"
            content_summary = record.comment[:200] if record.comment else "用户点赞的回答"

            tags = [record.category, "positive_feedback"]

            quality_case = QualityCase(
                case_id=case_id,
                trace_id=record.trace_id,
                user_id=record.user_id,
                feedback_id=record.feedback_id,
                title=title,
                content_summary=content_summary,
                tags=tags,
                quality_score=float(record.rating) if record.rating > 0 else 4.5,
                context={
                    "session_id": record.session_id,
                    "feedback_comment": record.comment
                }
            )

            self._save_quality_case(quality_case)

            # 更新反馈分析结果
            analysis_result = {
                "auto_analyzed": True,
                "archived_as_quality_case": True,
                "case_id": case_id,
                "analysis_note": "正面反馈已归档为优质案例"
            }

            self._update_analysis(record.feedback_id, analysis_result)

            logger.info(json.dumps({
                "trace_id": record.trace_id,
                "module_name": "feedback",
                "action": "_process_positive_feedback",
                "feedback_id": record.feedback_id,
                "case_id": case_id,
                "duration_ms": 0,
                "level": "INFO"
            }))

        except Exception as e:
            logger.warning(json.dumps({
                "trace_id": record.trace_id,
                "module_name": "feedback",
                "action": "_process_positive_feedback",
                "warning": f"处理正面反馈失败: {e}",
                "feedback_id": record.feedback_id,
                "duration_ms": 0,
                "level": "WARNING"
            }))

    def _save_quality_case(self, case: QualityCase):
        """保存优质案例"""
        with self._write_lock, self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO quality_cases
                   (case_id, trace_id, user_id, feedback_id, title,
                    content_summary, tags, quality_score, created_at,
                    context, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (case.case_id, case.trace_id, case.user_id, case.feedback_id,
                 case.title, case.content_summary,
                 json.dumps(case.tags, ensure_ascii=False),
                 case.quality_score, case.created_at,
                 json.dumps(case.context, ensure_ascii=False),
                 json.dumps(case.metadata, ensure_ascii=False))
            )
            conn.commit()

    def _update_analysis(self, feedback_id: str, analysis_result: Dict[str, Any]):
        """更新反馈分析结果"""
        with self._write_lock, self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE feedback
                   SET status = 'analyzed', analyzed_at = ?,
                       analysis_result = ?, updated_at = ?
                   WHERE feedback_id = ?""",
                (time.time(), json.dumps(analysis_result, ensure_ascii=False),
                 time.time(), feedback_id)
            )
            conn.commit()

    def get_feedback(self, feedback_id: str) -> Optional[FeedbackRecord]:
        self.initialize()

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM feedback WHERE feedback_id = ?",
                (feedback_id,)
            )
            row = cursor.fetchone()

        if not row:
            return None

        return self._row_to_feedback(row)

    def list_feedback(self, feedback_type: str = None, status: str = None,
                      category: str = None, user_id: str = "",
                      trace_id: str = "", limit: int = 50,
                      offset: int = 0) -> List[FeedbackRecord]:
        self.initialize()

        sql = "SELECT * FROM feedback WHERE 1=1"
        params = []

        if feedback_type:
            sql += " AND feedback_type = ?"
            params.append(feedback_type)

        if status:
            sql += " AND status = ?"
            params.append(status)

        if category:
            sql += " AND category = ?"
            params.append(category)

        if user_id:
            sql += " AND user_id = ?"
            params.append(user_id)

        if trace_id:
            sql += " AND trace_id = ?"
            params.append(trace_id)

        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        return [self._row_to_feedback(row) for row in rows]

    def _row_to_feedback(self, row: sqlite3.Row) -> FeedbackRecord:
        return FeedbackRecord(
            feedback_id=row['feedback_id'],
            trace_id=row['trace_id'],
            feedback_type=row['feedback_type'],
            rating=row['rating'],
            comment=row['comment'],
            category=row['category'],
            user_id=row['user_id'],
            session_id=row['session_id'],
            status=row['status'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            analyzed_at=row['analyzed_at'],
            resolved_at=row['resolved_at'],
            analysis_result=json.loads(row['analysis_result']),
            context=json.loads(row['context']),
            metadata=json.loads(row['metadata'])
        )

    def get_feedback_by_trace(self, trace_id: str) -> List[FeedbackRecord]:
        return self.list_feedback(trace_id=trace_id)

    def get_feedback_summary(self, days: int = 7) -> Dict[str, Any]:
        self.initialize()
        start_time = time.time()

        since = time.time() - days * 86400

        with self._get_conn() as conn:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT COUNT(*) as cnt FROM feedback WHERE created_at >= ?",
                (since,)
            )
            total = cursor.fetchone()['cnt']

            cursor.execute(
                """SELECT feedback_type, COUNT(*) as cnt
                   FROM feedback WHERE created_at >= ?
                   GROUP BY feedback_type""",
                (since,)
            )
            by_type = {row['feedback_type']: row['cnt'] for row in cursor.fetchall()}

            cursor.execute(
                """SELECT status, COUNT(*) as cnt
                   FROM feedback WHERE created_at >= ?
                   GROUP BY status""",
                (since,)
            )
            by_status = {row['status']: row['cnt'] for row in cursor.fetchall()}

            cursor.execute(
                """SELECT category, COUNT(*) as cnt
                   FROM feedback WHERE created_at >= ?
                   GROUP BY category ORDER BY cnt DESC LIMIT 10""",
                (since,)
            )
            by_category = {row['category']: row['cnt'] for row in cursor.fetchall()}

            cursor.execute(
                "SELECT COUNT(*) as cnt FROM quality_cases WHERE created_at >= ?",
                (since,)
            )
            quality_cases = cursor.fetchone()['cnt']

        like_count = by_type.get('like', 0)
        dislike_count = by_type.get('dislike', 0)
        total_votes = like_count + dislike_count
        satisfaction_rate = (like_count / total_votes * 100) if total_votes > 0 else 0.0

        result = {
            "time_range_days": days,
            "total_feedback": total,
            "by_type": by_type,
            "by_status": by_status,
            "by_category": by_category,
            "quality_cases_count": quality_cases,
            "satisfaction_rate_percent": round(satisfaction_rate, 2),
            "like_count": like_count,
            "dislike_count": dislike_count,
        }

        duration_ms = (time.time() - start_time) * 1000
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "feedback",
            "action": "get_feedback_summary",
            "days": days,
            "total": total,
            "satisfaction_rate": round(satisfaction_rate, 2),
            "duration_ms": round(duration_ms, 2),
            "level": "INFO"
        }))

        return result

    def list_quality_cases(self, tags: List[str] = None,
                           limit: int = 50, offset: int = 0) -> List[QualityCase]:
        self.initialize()

        sql = "SELECT * FROM quality_cases WHERE 1=1"
        params = []

        if tags:
            for tag in tags:
                sql += " AND tags LIKE ?"
                params.append(f'%{tag}%')

        sql += " ORDER BY quality_score DESC, created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        results = []
        for row in rows:
            results.append(QualityCase(
                case_id=row['case_id'],
                trace_id=row['trace_id'],
                user_id=row['user_id'],
                feedback_id=row['feedback_id'],
                title=row['title'],
                content_summary=row['content_summary'],
                tags=json.loads(row['tags']),
                quality_score=row['quality_score'],
                created_at=row['created_at'],
                context=json.loads(row['context']),
                metadata=json.loads(row['metadata'])
            ))

        return results

    def resolve_feedback(self, feedback_id: str,
                         resolution: str = "",
                         resolver: str = "") -> bool:
        """标记反馈为已解决"""
        self.initialize()
        start_time = time.time()

        feedback = self.get_feedback(feedback_id)
        if not feedback:
            raise ValueError(f"反馈不存在: {feedback_id}")

        try:
            with self._write_lock, self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """UPDATE feedback
                       SET status = 'resolved', resolved_at = ?,
                           updated_at = ?,
                           metadata = json_set(metadata, '$.resolution', ?,
                                              '$.resolver', ?)
                       WHERE feedback_id = ?""",
                    (time.time(), time.time(), resolution, resolver, feedback_id)
                )
                conn.commit()

            duration_ms = (time.time() - start_time) * 1000
            logger.info(json.dumps({
                "trace_id": feedback.trace_id,
                "module_name": "feedback",
                "action": "resolve_feedback",
                "feedback_id": feedback_id,
                "resolver": resolver,
                "duration_ms": round(duration_ms, 2),
                "level": "INFO"
            }))
            return True
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "feedback",
                "action": "resolve_feedback",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            raise

    def generate_feedback_report(self, days: int = 7) -> Dict[str, Any]:
        """生成反馈分析报告

        包含：满意度趋势、TOP问题分类、优质案例、优化建议等
        """
        self.initialize()
        start_time = time.time()

        summary = self.get_feedback_summary(days=days)

        # 获取负面反馈详情
        negative_feedback = self.list_feedback(
            feedback_type="dislike", limit=20
        )

        # 获取优质案例
        quality_cases = self.list_quality_cases(limit=10)

        # 生成优化建议
        suggestions = self._generate_optimization_suggestions(summary)

        report = {
            "report_period_days": days,
            "generated_at": time.time(),
            "generated_at_iso": datetime.now().isoformat(),
            "summary": summary,
            "top_issues": self._get_top_issues(negative_feedback),
            "quality_cases": [c.to_dict() for c in quality_cases],
            "optimization_suggestions": suggestions,
            "negative_feedback_count": len(negative_feedback),
        }

        duration_ms = (time.time() - start_time) * 1000
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "feedback",
            "action": "generate_feedback_report",
            "days": days,
            "suggestion_count": len(suggestions),
            "duration_ms": round(duration_ms, 2),
            "level": "INFO"
        }))

        return report

    def _get_top_issues(self, feedback_list: List[FeedbackRecord]) -> List[Dict[str, Any]]:
        """提取主要问题"""
        category_count = {}
        for fb in feedback_list:
            cat = fb.category
            category_count[cat] = category_count.get(cat, 0) + 1

        sorted_cats = sorted(category_count.items(), key=lambda x: x[1], reverse=True)

        issues = []
        for cat, count in sorted_cats[:5]:
            sample_comments = [
                fb.comment for fb in feedback_list
                if fb.category == cat and fb.comment
            ][:3]

            issues.append({
                "category": cat,
                "count": count,
                "sample_comments": sample_comments,
                "description": self._get_category_description(cat)
            })

        return issues

    def _get_category_description(self, category: str) -> str:
        descriptions = {
            "quality": "质量问题：回答内容质量不高",
            "accuracy": "准确性问题：回答内容不准确或有错误",
            "relevance": "相关性问题：回答与问题不相关",
            "completeness": "完整性问题：回答不完整或过于简略",
            "speed": "速度问题：响应速度太慢",
            "safety": "安全问题：涉及不安全或违规内容",
            "usability": "可用性问题：界面或交互不好用",
            "other": "其他问题",
        }
        return descriptions.get(category, "其他问题")

    def _generate_optimization_suggestions(self, summary: Dict[str, Any]) -> List[Dict[str, Any]]:
        """根据反馈数据生成优化建议"""
        suggestions = []

        by_category = summary.get('by_category', {})
        total = summary.get('total_feedback', 0)

        if total == 0:
            return suggestions

        # 按问题比例生成建议
        for category, count in sorted(by_category.items(), key=lambda x: x[1], reverse=True):
            ratio = count / total * 100
            if ratio > 15:
                priority = "high"
            elif ratio > 5:
                priority = "medium"
            else:
                continue

            suggestion = {
                "category": category,
                "count": count,
                "ratio_percent": round(ratio, 2),
                "priority": priority,
                "description": self._get_category_description(category),
                "suggestions": self._get_category_suggestions(category)
            }
            suggestions.append(suggestion)

        # 满意度建议
        satisfaction = summary.get('satisfaction_rate_percent', 0)
        if satisfaction < 60:
            suggestions.append({
                "category": "overall",
                "priority": "high",
                "description": f"用户满意度较低（{satisfaction}%），建议全面排查质量问题",
                "suggestions": [
                    "增加质量校验环节",
                    "优化 Prompt 提升回答质量",
                    "增加人工审核机制",
                    "建立用户反馈快速响应流程"
                ]
            })

        return suggestions

    def _get_category_suggestions(self, category: str) -> List[str]:
        suggestions_map = {
            "accuracy": [
                "增加事实核查步骤",
                "优化知识库检索精度",
                "增加多轮验证机制",
                "对关键信息添加引用来源"
            ],
            "quality": [
                "优化系统提示词",
                "增加输出格式规范",
                "提高 Critic 质量阈值",
                "增加多示例学习"
            ],
            "relevance": [
                "优化问题理解模块",
                "增加对话上下文保持机制",
                "改进意图识别算法",
                "增加话题漂移检测"
            ],
            "completeness": [
                "增加回答完整性检查",
                "优化 Prompt 要求详细回答",
                "增加输出结构模板",
                "提供追问机制"
            ],
            "speed": [
                "优化模型参数减少响应时间",
                "增加流式输出",
                "优化缓存策略",
                "使用更快的模型"
            ],
            "safety": [
                "加强内容安全过滤",
                "增加敏感词检测",
                "优化安全策略规则",
                "增加人工复核环节"
            ],
        }
        return suggestions_map.get(category, ["持续收集更多反馈，分析具体问题"])


_global_feedback_manager = None


def get_feedback_manager() -> FeedbackManager:
    global _global_feedback_manager
    if _global_feedback_manager is None:
        _global_feedback_manager = FeedbackManager()
        _global_feedback_manager.initialize()
    return _global_feedback_manager


__all__ = [
    'FeedbackType', 'FeedbackStatus', 'FeedbackCategory',
    'FeedbackRecord', 'QualityCase', 'FeedbackManager',
    'get_feedback_manager'
]
