"""FeedbackManager 集成测试

覆盖用户反馈闭环：
1. 反馈提交参数校验
2. 负面反馈触发失败分析（report_failure）
3. 正面反馈创建优质案例（QualityCase）
4. 多条件过滤查询
5. 技能反馈聚合统计与推荐动作
6. 反馈解决与报告生成
"""

import pytest
from unittest.mock import patch

from agent.feedback import FeedbackManager, FeedbackType

pytestmark = pytest.mark.integration


class TestFeedbackIntegration:
    """FeedbackManager 集成测试"""

    def test_submit_feedback_validates_inputs(self, feedback_manager):
        """测试 1：submit_feedback 参数校验"""
        mgr = feedback_manager

        # trace_id 为空
        with pytest.raises(ValueError, match="trace_id 不能为空"):
            mgr.submit_feedback(trace_id="", feedback_type="like")

        # rating 越界（< 0）
        with pytest.raises(ValueError, match="rating 必须在 0-5 之间"):
            mgr.submit_feedback(
                trace_id="t1", feedback_type="like", rating=-1
            )

        # rating 越界（> 5）
        with pytest.raises(ValueError, match="rating 必须在 0-5 之间"):
            mgr.submit_feedback(
                trace_id="t1", feedback_type="like", rating=6
            )

        # feedback_type 非法
        with pytest.raises(ValueError, match="feedback_type 非法"):
            mgr.submit_feedback(
                trace_id="t1", feedback_type="invalid_type"
            )

        # 正常提交（report 类型，不触发正/负反馈处理）
        record = mgr.submit_feedback(
            trace_id="trace_normal",
            feedback_type="report",
            rating=3,
            comment="测试报告",
            category="other",
        )
        assert record.feedback_id is not None
        assert record.trace_id == "trace_normal"
        assert record.feedback_type == "report"

    def test_negative_feedback_triggers_failure_analysis(self, feedback_manager):
        """测试 2：负面反馈触发失败分析"""
        mgr = feedback_manager

        with patch("agent.cognitive.failure_analysis.report_failure") as mock_rf:
            record = mgr.submit_feedback(
                trace_id="trace_negative",
                feedback_type="dislike",
                rating=1,
                comment="回答不准确，存在事实错误",
                category="accuracy",
                user_id="user_001",
            )
            mock_rf.assert_called_once()
            assert mock_rf.call_args.kwargs["source"] == "user_feedback"
            assert "feedback_id" in mock_rf.call_args.kwargs["context"]

        # 验证反馈状态变为 analyzed
        updated = mgr.get_feedback(record.feedback_id)
        assert updated.status == "analyzed"
        assert updated.analysis_result["entered_failure_analysis"] is True
        assert "failure_type" in updated.analysis_result

    def test_positive_feedback_creates_quality_case(self, feedback_manager):
        """测试 3：正面反馈创建优质案例"""
        mgr = feedback_manager

        record = mgr.submit_feedback(
            trace_id="trace_positive",
            feedback_type="like",
            rating=5,
            comment="回答非常准确，帮助很大",
            category="accuracy",
            user_id="user_002",
            skill_id="skill_search",
        )
        assert record.feedback_type == "like"

        # 验证状态变为 analyzed
        updated = mgr.get_feedback(record.feedback_id)
        assert updated.status == "analyzed"
        assert updated.analysis_result["archived_as_quality_case"] is True
        assert "case_id" in updated.analysis_result

        # 验证优质案例已创建，可按 skill_id 查询
        cases = mgr.list_quality_cases(skill_id="skill_search")
        assert len(cases) >= 1
        case = cases[0]
        assert case.skill_id == "skill_search"
        assert case.trace_id == "trace_positive"
        assert "accuracy" in case.tags

    def test_list_feedback_with_filters(self, feedback_manager):
        """测试 4：多条件过滤查询"""
        mgr = feedback_manager

        # 准备测试数据
        with patch("agent.cognitive.failure_analysis.report_failure"):
            mgr.submit_feedback(
                trace_id="trace_filter_1", feedback_type="dislike",
                rating=2, category="accuracy", user_id="user_a",
                skill_id="skill_1",
            )
        mgr.submit_feedback(
            trace_id="trace_filter_2", feedback_type="like",
            rating=5, category="quality", user_id="user_b",
            skill_id="skill_1",
        )
        mgr.submit_feedback(
            trace_id="trace_filter_3", feedback_type="like",
            rating=4, category="accuracy", user_id="user_a",
            skill_id="skill_2",
        )

        # 按 feedback_type 过滤
        dislikes = mgr.list_feedback(feedback_type="dislike")
        assert len(dislikes) >= 1
        assert all(f.feedback_type == "dislike" for f in dislikes)

        # 按 user_id 过滤
        user_a_feedback = mgr.list_feedback(user_id="user_a")
        assert len(user_a_feedback) >= 2
        assert all(f.user_id == "user_a" for f in user_a_feedback)

        # 按 skill_id 过滤
        skill1_feedback = mgr.get_feedback_by_skill("skill_1")
        assert len(skill1_feedback) >= 2
        assert all(f.skill_id == "skill_1" for f in skill1_feedback)

        # 按 trace_id 过滤
        trace_feedback = mgr.get_feedback_by_trace("trace_filter_2")
        assert len(trace_feedback) == 1
        assert trace_feedback[0].rating == 5

    def test_skill_feedback_summary_recommended_actions(self, feedback_manager):
        """测试 5：技能反馈聚合统计与推荐动作"""
        mgr = feedback_manager

        skill_id = "skill_summary_test"

        # 场景 1：无数据 → no_data
        summary = mgr.get_skill_feedback_summary(skill_id, days=30)
        assert summary["total_feedback"] == 0
        assert summary["recommended_action"] == "no_data"

        # 场景 2：高满意度（≥90%）→ promote_to_published
        with patch("agent.cognitive.failure_analysis.report_failure"):
            for i in range(9):
                mgr.submit_feedback(
                    trace_id=f"trace_high_{i}",
                    feedback_type="like",
                    rating=5,
                    skill_id=skill_id,
                )
            # 1 个 dislike，满意度 90%
            mgr.submit_feedback(
                trace_id="trace_high_dislike",
                feedback_type="dislike",
                rating=2,
                skill_id=skill_id,
            )

        summary = mgr.get_skill_feedback_summary(skill_id, days=30)
        assert summary["total_feedback"] == 10
        assert summary["like_count"] == 9
        assert summary["dislike_count"] == 1
        assert summary["satisfaction_rate_percent"] == 90.0
        assert summary["recommended_action"] == "promote_to_published"

        # 场景 3：低满意度（<50%）→ consider_deprecate_or_merge
        skill_low = "skill_low_sat"
        with patch("agent.cognitive.failure_analysis.report_failure"):
            for i in range(6):
                mgr.submit_feedback(
                    trace_id=f"trace_low_{i}",
                    feedback_type="dislike",
                    rating=1,
                    skill_id=skill_low,
                )
            for i in range(4):
                mgr.submit_feedback(
                    trace_id=f"trace_low_like_{i}",
                    feedback_type="like",
                    rating=4,
                    skill_id=skill_low,
                )

        summary_low = mgr.get_skill_feedback_summary(skill_low, days=30)
        assert summary_low["total_feedback"] == 10
        assert summary_low["satisfaction_rate_percent"] == 40.0
        assert summary_low["recommended_action"] == "consider_deprecate_or_merge"

    def test_resolve_feedback_and_generate_report(self, feedback_manager):
        """测试 6：反馈解决与报告生成"""
        mgr = feedback_manager

        # 提交几条反馈
        with patch("agent.cognitive.failure_analysis.report_failure"):
            mgr.submit_feedback(
                trace_id="trace_resolve_1",
                feedback_type="dislike",
                rating=2,
                comment="回答太慢",
                category="speed",
            )
        mgr.submit_feedback(
            trace_id="trace_resolve_2",
            feedback_type="like",
            rating=4,
            comment="回答很好",
            category="quality",
        )

        # 获取 dislike 反馈的 ID
        dislikes = mgr.list_feedback(feedback_type="dislike")
        assert len(dislikes) >= 1
        feedback_id = dislikes[0].feedback_id

        # 解决反馈
        assert mgr.resolve_feedback(
            feedback_id, resolution="已优化响应速度", resolver="admin"
        ) is True

        # 验证状态变为 resolved
        resolved = mgr.get_feedback(feedback_id)
        assert resolved.status == "resolved"
        assert resolved.resolved_at is not None

        # 解决不存在的反馈抛异常
        with pytest.raises(ValueError, match="反馈不存在"):
            mgr.resolve_feedback("nonexistent_id")

        # 生成报告
        report = mgr.generate_feedback_report(days=30)
        assert "summary" in report
        assert "top_issues" in report
        assert "quality_cases" in report
        assert "optimization_suggestions" in report
        assert "negative_feedback_count" in report
        assert report["report_period_days"] == 30

        # 验证全局摘要
        summary = mgr.get_feedback_summary(days=30)
        assert "total_feedback" in summary
        assert "by_type" in summary
        assert "satisfaction_rate_percent" in summary
