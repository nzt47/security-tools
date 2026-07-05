"""BT-005 feedback 模块边界测试

【生成日志摘要】
- 生成时间：2026-07-01
- 内容：BT-005 feedback 边界测试（v1.0）
- 模型：GLM-5.2
- 关键状态：覆盖 FeedbackManager 的 submit/get/list/resolve/summary 7 类边界场景
- 状态同步机制：使用 tmp_path fixture 隔离 SQLite 数据库，避免测试间数据污染

覆盖范围：
- 空值边界: None trace_id / 空字符串 / 不存在的 feedback_id
- 极值边界: 极端 rating (-100/999999) / days=0 / 超大 limit
- 类型边界: 非法 feedback_type / 非法 category
- 异常分支: resolve 不存在的 feedback 抛 ValueError
- 编码边界: Unicode comment / emoji / 特殊字符

源代码限制记录：
- submit_feedback(trace_id=None) 抛 sqlite3.IntegrityError（NOT NULL 约束）
- rating 未做范围校验，负数/超大值直接存储
- get_feedback(不存在id) 返回 None
- resolve_feedback(不存在id) 抛 ValueError
"""
import os
import json
import sqlite3
from pathlib import Path

import pytest

from agent.feedback import (
    FeedbackManager,
    FeedbackRecord,
    FeedbackType,
    FeedbackStatus,
    QualityCase,
)


@pytest.fixture
def feedback_mgr(tmp_path):
    """创建使用临时目录的 FeedbackManager 实例"""
    mgr = FeedbackManager(storage_path=str(tmp_path / "feedback_test"))
    mgr.initialize()
    yield mgr
    # 清理连接
    if hasattr(mgr._local, 'conn') and mgr._local.conn:
        mgr._local.conn.close()
        mgr._local.conn = None


# ═══════════════════════════════════════════════════════════════
#  submit_feedback 空值边界测试
# ═══════════════════════════════════════════════════════════════


class TestSubmitNullBoundary:
    """submit_feedback 空值边界测试"""

    def test_null_None作为trace_id抛出IntegrityError(self, feedback_mgr):
        """None 作为 trace_id 现在抛出 ValueError（边界显性化校验）

        更新说明: feedback-skill 绑定改造后，submit_feedback 在写库前
        会先做参数校验，None / 空字符串 trace_id 会被显性拒绝。
        """
        with pytest.raises(ValueError):
            feedback_mgr.submit_feedback(
                trace_id=None,  # type: ignore
                feedback_type="like",
            )

    def test_empty_空字符串trace_id正常提交(self, feedback_mgr):
        """空字符串 trace_id 现在被显性拒绝（边界显性化）"""
        with pytest.raises(ValueError):
            feedback_mgr.submit_feedback(
                trace_id="",
                feedback_type="like",
            )

    def test_empty_空字符串comment正常提交(self, feedback_mgr):
        """空字符串 comment 正常提交"""
        record = feedback_mgr.submit_feedback(
            trace_id="trace_001",
            feedback_type="like",
            comment="",
        )
        assert record.comment == ""

    def test_empty_默认参数正常提交(self, feedback_mgr):
        """使用默认参数正常提交"""
        record = feedback_mgr.submit_feedback(
            trace_id="trace_001",
            feedback_type="like",
        )
        assert record.rating == 0
        assert record.category == "other"
        assert record.user_id == ""
        assert record.status == "pending"


# ═══════════════════════════════════════════════════════════════
#  submit_feedback 极值边界测试
# ═══════════════════════════════════════════════════════════════


class TestSubmitExtremeBoundary:
    """submit_feedback 极值边界测试"""

    def test_extreme_负数rating正常存储(self, feedback_mgr):
        """负数 rating 现在被显性拒绝（边界显性化：rating 必须 0-5）"""
        with pytest.raises(ValueError):
            feedback_mgr.submit_feedback(
                trace_id="trace_001",
                feedback_type="like",
                rating=-100,
            )

    def test_extreme_超大rating正常存储(self, feedback_mgr):
        """超大 rating 现在被显性拒绝（边界显性化：rating 必须 0-5）"""
        with pytest.raises(ValueError):
            feedback_mgr.submit_feedback(
                trace_id="trace_001",
                feedback_type="like",
                rating=999999,
            )

    def test_extreme_边界rating零值(self, feedback_mgr):
        """边界 rating=0 正常存储"""
        record = feedback_mgr.submit_feedback(
            trace_id="trace_001",
            feedback_type="like",
            rating=0,
        )
        assert record.rating == 0

    def test_extreme_边界rating最大值5(self, feedback_mgr):
        """边界 rating=5 正常存储"""
        record = feedback_mgr.submit_feedback(
            trace_id="trace_001",
            feedback_type="like",
            rating=5,
        )
        assert record.rating == 5

    def test_extreme_超长comment正常存储(self, feedback_mgr):
        """超长 comment 正常存储"""
        long_comment = "这是一条很长的评论" * 1000
        record = feedback_mgr.submit_feedback(
            trace_id="trace_001",
            feedback_type="like",
            comment=long_comment,
        )
        assert record.comment == long_comment


# ═══════════════════════════════════════════════════════════════
#  submit_feedback 编码边界测试
# ═══════════════════════════════════════════════════════════════


class TestSubmitEncodingBoundary:
    """submit_feedback 编码边界测试"""

    def test_encoding_Unicode评论正常存储(self, feedback_mgr):
        """Unicode 评论正常存储"""
        record = feedback_mgr.submit_feedback(
            trace_id="trace_001",
            feedback_type="like",
            comment="回复包含 emoji 😀 和特殊字符 <>&\"'",
        )
        fetched = feedback_mgr.get_feedback(record.feedback_id)
        assert "😀" in fetched.comment
        assert "<>&" in fetched.comment

    def test_encoding_中文评论正常存储(self, feedback_mgr):
        """中文评论正常存储"""
        record = feedback_mgr.submit_feedback(
            trace_id="trace_001",
            feedback_type="dislike",
            comment="回答完全错误，不准确",
            category="accuracy",
        )
        fetched = feedback_mgr.get_feedback(record.feedback_id)
        assert "错误" in fetched.comment

    def test_encoding_特殊字符trace_id正常存储(self, feedback_mgr):
        """特殊字符 trace_id 正常存储"""
        special_trace = "trace-001_测试@特殊"
        record = feedback_mgr.submit_feedback(
            trace_id=special_trace,
            feedback_type="like",
        )
        fetched = feedback_mgr.get_feedback(record.feedback_id)
        assert fetched.trace_id == special_trace


# ═══════════════════════════════════════════════════════════════
#  submit_feedback 类型边界测试
# ═══════════════════════════════════════════════════════════════


class TestSubmitTypeBoundary:
    """submit_feedback 类型边界测试"""

    def test_invalid_非法feedback_type正常存储(self, feedback_mgr):
        """非法 feedback_type 现在被显性拒绝（边界显性化：必须为 like/dislike/report/suggestion）"""
        with pytest.raises(ValueError):
            feedback_mgr.submit_feedback(
                trace_id="trace_001",
                feedback_type="unknown_type",
            )

    def test_invalid_空字符串feedback_type正常存储(self, feedback_mgr):
        """空字符串 feedback_type 现在被显性拒绝（边界显性化）"""
        with pytest.raises(ValueError):
            feedback_mgr.submit_feedback(
                trace_id="trace_001",
                feedback_type="",
            )

    def test_boundary_所有合法feedback_type正常存储(self, feedback_mgr):
        """所有合法 feedback_type 正常存储"""
        for ft in ["like", "dislike", "report", "suggestion"]:
            record = feedback_mgr.submit_feedback(
                trace_id=f"trace_{ft}",
                feedback_type=ft,
            )
            assert record.feedback_type == ft

    def test_invalid_非法category正常存储(self, feedback_mgr):
        """非法 category 正常存储"""
        record = feedback_mgr.submit_feedback(
            trace_id="trace_001",
            feedback_type="like",
            category="unknown_category",
        )
        assert record.category == "unknown_category"


# ═══════════════════════════════════════════════════════════════
#  get_feedback 查询边界测试
# ═══════════════════════════════════════════════════════════════


class TestGetFeedbackBoundary:
    """get_feedback 查询边界测试"""

    def test_empty_不存在的feedback_id返回None(self, feedback_mgr):
        """不存在的 feedback_id 返回 None"""
        result = feedback_mgr.get_feedback("nonexistent_id")
        assert result is None

    def test_empty_空字符串feedback_id返回None(self, feedback_mgr):
        """空字符串 feedback_id 返回 None"""
        result = feedback_mgr.get_feedback("")
        assert result is None

    def test_boundary_已存在的feedback_id返回记录(self, feedback_mgr):
        """已存在的 feedback_id 返回记录"""
        record = feedback_mgr.submit_feedback(
            trace_id="trace_001",
            feedback_type="like",
            comment="测试评论",
        )
        fetched = feedback_mgr.get_feedback(record.feedback_id)
        assert fetched is not None
        assert fetched.feedback_id == record.feedback_id
        assert fetched.comment == "测试评论"

    def test_boundary_get_feedback_by_trace查询(self, feedback_mgr):
        """get_feedback_by_trace 按 trace_id 查询"""
        feedback_mgr.submit_feedback(
            trace_id="trace_001",
            feedback_type="like",
        )
        feedback_mgr.submit_feedback(
            trace_id="trace_001",
            feedback_type="dislike",
        )
        results = feedback_mgr.get_feedback_by_trace("trace_001")
        assert len(results) == 2

    def test_empty_不存在的trace_id返回空列表(self, feedback_mgr):
        """不存在的 trace_id 返回空列表"""
        results = feedback_mgr.get_feedback_by_trace("nonexistent_trace")
        assert results == []


# ═══════════════════════════════════════════════════════════════
#  list_feedback 查询边界测试
# ═══════════════════════════════════════════════════════════════


class TestListFeedbackBoundary:
    """list_feedback 查询边界测试"""

    def test_empty_空数据库返回空列表(self, feedback_mgr):
        """空数据库返回空列表"""
        results = feedback_mgr.list_feedback()
        assert results == []

    def test_extreme_limit零值返回空列表(self, feedback_mgr):
        """limit=0 返回空列表（SQL LIMIT 0）"""
        feedback_mgr.submit_feedback(trace_id="t1", feedback_type="like")
        results = feedback_mgr.list_feedback(limit=0)
        assert len(results) == 0

    def test_extreme_limit负值(self, feedback_mgr):
        """limit=-1 的行为（SQLite 对负数 LIMIT 返回所有行）"""
        feedback_mgr.submit_feedback(trace_id="t1", feedback_type="like")
        results = feedback_mgr.list_feedback(limit=-1)
        # SQLite LIMIT -1 表示无限制
        assert len(results) >= 1

    def test_extreme_limit大于记录数返回全部(self, feedback_mgr):
        """limit 大于记录数返回全部"""
        feedback_mgr.submit_feedback(trace_id="t1", feedback_type="like")
        results = feedback_mgr.list_feedback(limit=1000)
        assert len(results) == 1

    def test_boundary_offset跳过记录(self, feedback_mgr):
        """offset 跳过指定数量的记录"""
        for i in range(5):
            feedback_mgr.submit_feedback(
                trace_id=f"t{i}", feedback_type="like"
            )
        results = feedback_mgr.list_feedback(limit=10, offset=3)
        assert len(results) == 2  # 5 - 3 = 2

    def test_boundary_按feedback_type过滤(self, feedback_mgr):
        """按 feedback_type 过滤"""
        feedback_mgr.submit_feedback(trace_id="t1", feedback_type="like")
        feedback_mgr.submit_feedback(trace_id="t2", feedback_type="dislike")
        results = feedback_mgr.list_feedback(feedback_type="like")
        assert len(results) == 1
        assert results[0].feedback_type == "like"

    def test_boundary_按status过滤(self, feedback_mgr):
        """按 status 过滤

        源代码行为: like 类型提交后 _process_positive_feedback 会调用
        _update_analysis 将 status 改为 'analyzed'。
        dislike 类型同理也会被自动分析。
        查询 status='analyzed' 应返回自动分析过的反馈。
        """
        feedback_mgr.submit_feedback(trace_id="t1", feedback_type="like")
        # like 类型自动处理后 status 变为 'analyzed'
        results_analyzed = feedback_mgr.list_feedback(status="analyzed")
        assert len(results_analyzed) == 1
        assert results_analyzed[0].status == "analyzed"

    def test_boundary_按category过滤(self, feedback_mgr):
        """按 category 过滤"""
        feedback_mgr.submit_feedback(
            trace_id="t1", feedback_type="like", category="accuracy"
        )
        feedback_mgr.submit_feedback(
            trace_id="t2", feedback_type="like", category="speed"
        )
        results = feedback_mgr.list_feedback(category="accuracy")
        assert len(results) == 1
        assert results[0].category == "accuracy"

    def test_boundary_按user_id过滤(self, feedback_mgr):
        """按 user_id 过滤"""
        feedback_mgr.submit_feedback(
            trace_id="t1", feedback_type="like", user_id="user_001"
        )
        feedback_mgr.submit_feedback(
            trace_id="t2", feedback_type="like", user_id="user_002"
        )
        results = feedback_mgr.list_feedback(user_id="user_001")
        assert len(results) == 1
        assert results[0].user_id == "user_001"


# ═══════════════════════════════════════════════════════════════
#  resolve_feedback 异常分支测试
# ═══════════════════════════════════════════════════════════════


class TestResolveFeedbackBoundary:
    """resolve_feedback 异常分支测试"""

    def test_exception_不存在的feedback_id抛出ValueError(self, feedback_mgr):
        """不存在的 feedback_id 抛出 ValueError

        源代码限制: resolve_feedback 先调用 get_feedback，不存在时抛 ValueError
        """
        with pytest.raises(ValueError):
            feedback_mgr.resolve_feedback("nonexistent_id")

    def test_boundary_已存在的feedback_id正常解决(self, feedback_mgr):
        """已存在的 feedback_id 正常解决"""
        record = feedback_mgr.submit_feedback(
            trace_id="t1", feedback_type="dislike"
        )
        result = feedback_mgr.resolve_feedback(
            record.feedback_id,
            resolution="已修复",
            resolver="admin"
        )
        assert result is True
        fetched = feedback_mgr.get_feedback(record.feedback_id)
        assert fetched.status == "resolved"
        assert fetched.resolved_at is not None

    def test_empty_空字符串resolution正常解决(self, feedback_mgr):
        """空字符串 resolution 正常解决"""
        record = feedback_mgr.submit_feedback(
            trace_id="t1", feedback_type="dislike"
        )
        result = feedback_mgr.resolve_feedback(record.feedback_id, resolution="")
        assert result is True


# ═══════════════════════════════════════════════════════════════
#  get_feedback_summary 统计边界测试
# ═══════════════════════════════════════════════════════════════


class TestFeedbackSummaryBoundary:
    """get_feedback_summary 统计边界测试"""

    def test_empty_空数据库summary正常返回(self, feedback_mgr):
        """空数据库 summary 正常返回"""
        summary = feedback_mgr.get_feedback_summary(days=7)
        assert summary["total_feedback"] == 0
        assert summary["like_count"] == 0
        assert summary["dislike_count"] == 0
        assert summary["satisfaction_rate_percent"] == 0.0

    def test_extreme_days零值正常返回(self, feedback_mgr):
        """days=0 正常返回（since = now - 0 = now，查询无结果）"""
        summary = feedback_mgr.get_feedback_summary(days=0)
        assert summary["total_feedback"] == 0

    def test_extreme_days负值正常返回(self, feedback_mgr):
        """days=-1 正常返回（since = now + 86400，查询未来时间，无结果）"""
        summary = feedback_mgr.get_feedback_summary(days=-1)
        assert summary["total_feedback"] == 0

    def test_extreme_days超大值正常返回(self, feedback_mgr):
        """days=999999 正常返回"""
        summary = feedback_mgr.get_feedback_summary(days=999999)
        assert isinstance(summary, dict)
        assert "total_feedback" in summary

    def test_boundary_有数据时summary正确统计(self, feedback_mgr):
        """有数据时 summary 正确统计"""
        feedback_mgr.submit_feedback(trace_id="t1", feedback_type="like", rating=5)
        feedback_mgr.submit_feedback(trace_id="t2", feedback_type="like", rating=4)
        feedback_mgr.submit_feedback(trace_id="t3", feedback_type="dislike", rating=1)
        summary = feedback_mgr.get_feedback_summary(days=7)
        assert summary["total_feedback"] == 3
        assert summary["like_count"] == 2
        assert summary["dislike_count"] == 1
        # satisfaction_rate = 2/3 * 100 ≈ 66.67
        assert summary["satisfaction_rate_percent"] == pytest.approx(66.67, rel=0.1)


# ═══════════════════════════════════════════════════════════════
#  FeedbackRecord / QualityCase 数据结构边界测试
# ═══════════════════════════════════════════════════════════════


class TestDataClassBoundary:
    """FeedbackRecord / QualityCase 数据结构边界测试"""

    def test_boundary_FeedbackRecord默认值(self):
        """FeedbackRecord 默认值正确"""
        record = FeedbackRecord(
            feedback_id="fb_001",
            trace_id="trace_001",
            feedback_type="like",
        )
        assert record.rating == 0
        assert record.comment == ""
        assert record.category == "other"
        assert record.user_id == ""
        assert record.status == "pending"
        assert record.analysis_result == {}
        assert record.context == {}
        assert record.metadata == {}

    def test_boundary_FeedbackRecord_to_dict包含ISO时间(self):
        """FeedbackRecord.to_dict() 包含 ISO 时间"""
        record = FeedbackRecord(
            feedback_id="fb_001",
            trace_id="trace_001",
            feedback_type="like",
        )
        d = record.to_dict()
        assert "created_at_iso" in d
        assert "updated_at_iso" in d
        assert "analyzed_at_iso" not in d  # analyzed_at 为 None

    def test_boundary_QualityCase默认值(self):
        """QualityCase 默认值正确"""
        case = QualityCase(case_id="qc_001", trace_id="trace_001")
        assert case.user_id == ""
        assert case.feedback_id == ""
        assert case.title == ""
        assert case.tags == []
        assert case.quality_score == 0.0
        assert case.context == {}
        assert case.metadata == {}

    def test_boundary_QualityCase_to_dict包含ISO时间(self):
        """QualityCase.to_dict() 包含 ISO 时间"""
        case = QualityCase(case_id="qc_001", trace_id="trace_001")
        d = case.to_dict()
        assert "created_at_iso" in d

    def test_boundary_FeedbackType枚举值(self):
        """FeedbackType 枚举值正确"""
        assert FeedbackType.LIKE.value == "like"
        assert FeedbackType.DISLIKE.value == "dislike"
        assert FeedbackType.REPORT.value == "report"
        assert FeedbackType.SUGGESTION.value == "suggestion"

    def test_boundary_FeedbackStatus枚举值(self):
        """FeedbackStatus 枚举值正确"""
        assert FeedbackStatus.PENDING.value == "pending"
        assert FeedbackStatus.ANALYZED.value == "analyzed"
        assert FeedbackStatus.RESOLVED.value == "resolved"
        assert FeedbackStatus.ARCHIVED.value == "archived"
