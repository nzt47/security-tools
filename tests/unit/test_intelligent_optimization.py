#!/usr/bin/env python3
"""
第八阶段智能优化闭环 - 单元测试

覆盖模块：
1. A/B 实验框架 (agent/ab_testing.py)
2. 自适应参数调优 (agent/auto_tuner.py)
3. 失败模式自动修复建议 (agent/cognitive/failure_analysis.py)
4. 用户反馈闭环 (agent/feedback.py)
"""

import os
import sys
import json
import time
import tempfile
import unittest
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestABTestingFramework(unittest.TestCase):
    """A/B 实验框架测试"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        from agent.ab_testing import ABTestManager, ExperimentType, ExperimentVariant
        self.manager = ABTestManager(storage_path=self.test_dir)
        self.manager.initialize()
        self.ExperimentType = ExperimentType
        self.ExperimentVariant = ExperimentVariant

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_create_experiment(self):
        variants = [
            self.ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
            self.ExperimentVariant(variant_id="test", name="实验组", weight=50, config={"param": "value"}),
        ]

        exp = self.manager.create_experiment(
            name="测试实验",
            experiment_type=self.ExperimentType.PROMPT_VERSION,
            variants=variants,
            description="单元测试实验",
            target_metric="quality_score"
        )

        self.assertIsNotNone(exp)
        self.assertEqual(exp.name, "测试实验")
        self.assertEqual(len(exp.variants), 2)
        self.assertEqual(exp.status.value, "draft")
        self.assertTrue(exp.experiment_id)

    def test_create_experiment_less_than_two_variants(self):
        with self.assertRaises(ValueError):
            self.manager.create_experiment(
                name="失败实验",
                variants=[self.ExperimentVariant(variant_id="v1", name="仅一个")]
            )

    def test_start_and_pause_experiment(self):
        variants = [
            self.ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
            self.ExperimentVariant(variant_id="test", name="实验组", weight=50),
        ]
        exp = self.manager.create_experiment(name="启停测试", variants=variants)

        result = self.manager.start_experiment(exp.experiment_id)
        self.assertTrue(result)

        exp2 = self.manager.get_experiment(exp.experiment_id)
        self.assertEqual(exp2.status.value, "running")

        result = self.manager.pause_experiment(exp.experiment_id)
        self.assertTrue(result)

        exp3 = self.manager.get_experiment(exp.experiment_id)
        self.assertEqual(exp3.status.value, "paused")

    def test_terminate_experiment(self):
        variants = [
            self.ExperimentVariant(variant_id="control", name="对照组", weight=50),
            self.ExperimentVariant(variant_id="test", name="实验组", weight=50),
        ]
        exp = self.manager.create_experiment(name="终止测试", variants=variants)
        self.manager.start_experiment(exp.experiment_id)

        result = self.manager.terminate_experiment(exp.experiment_id, reason="测试终止")
        self.assertTrue(result)

        exp2 = self.manager.get_experiment(exp.experiment_id)
        self.assertEqual(exp2.status.value, "terminated")

    def test_assign_variant_deterministic(self):
        variants = [
            self.ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
            self.ExperimentVariant(variant_id="test", name="实验组", weight=50),
        ]
        exp = self.manager.create_experiment(name="分流测试", variants=variants)
        self.manager.start_experiment(exp.experiment_id)

        v1 = self.manager.assign_variant(exp.experiment_id, "user_123")
        v2 = self.manager.assign_variant(exp.experiment_id, "user_123")

        self.assertIsNotNone(v1)
        self.assertIsNotNone(v2)
        self.assertEqual(v1.variant_id, v2.variant_id)

    def test_record_metric(self):
        variants = [
            self.ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
            self.ExperimentVariant(variant_id="test", name="实验组", weight=50),
        ]
        exp = self.manager.create_experiment(name="指标测试", variants=variants)
        self.manager.start_experiment(exp.experiment_id)

        result = self.manager.record_metric(
            experiment_id=exp.experiment_id,
            variant_id="control",
            metric_type="quality_score",
            value=85.0,
            trace_id="trace_123",
            user_id="user_1"
        )
        self.assertTrue(result)

    def test_analyze_results(self):
        variants = [
            self.ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
            self.ExperimentVariant(variant_id="test", name="实验组", weight=50),
        ]
        exp = self.manager.create_experiment(
            name="分析测试",
            variants=variants,
            target_metric="quality_score"
        )
        self.manager.start_experiment(exp.experiment_id)

        for i in range(50):
            self.manager.record_metric(
                exp.experiment_id, "control", "quality_score",
                70.0 + i * 0.2, trace_id=f"trace_c_{i}"
            )
            self.manager.record_metric(
                exp.experiment_id, "test", "quality_score",
                80.0 + i * 0.2, trace_id=f"trace_t_{i}"
            )

        result = self.manager.analyze_results(exp.experiment_id)

        self.assertIsNotNone(result)
        self.assertEqual(result.experiment_id, exp.experiment_id)
        self.assertEqual(result.sample_size, 100)
        self.assertIn("control", result.variant_results)
        self.assertIn("test", result.variant_results)

    def test_list_experiments(self):
        for i in range(5):
            variants = [
                self.ExperimentVariant(variant_id=f"c_{i}", name=f"对照{i}", weight=50),
                self.ExperimentVariant(variant_id=f"t_{i}", name=f"实验{i}", weight=50),
            ]
            self.manager.create_experiment(name=f"实验{i}", variants=variants)

        exps = self.manager.list_experiments(limit=3)
        self.assertEqual(len(exps), 3)

    def test_get_metrics_by_trace(self):
        variants = [
            self.ExperimentVariant(variant_id="control", name="对照组", weight=50),
            self.ExperimentVariant(variant_id="test", name="实验组", weight=50),
        ]
        exp = self.manager.create_experiment(name="Trace查询测试", variants=variants)
        self.manager.start_experiment(exp.experiment_id)

        self.manager.record_metric(
            exp.experiment_id, "control", "quality_score",
            90.0, trace_id="trace_test"
        )

        metrics = self.manager.get_metrics_by_trace("trace_test")
        self.assertGreaterEqual(len(metrics), 1)
        self.assertEqual(metrics[0].trace_id, "trace_test")


class TestAutoTuner(unittest.TestCase):
    """自适应参数调优测试"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        from agent.auto_tuner import AutoTuner
        self.tuner = AutoTuner(storage_path=self.test_dir)
        self.tuner.initialize()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_default_params(self):
        params = self.tuner.get_current_params()
        self.assertIn("critic_threshold", params)
        self.assertIn("max_retries", params)
        self.assertIn("temperature", params)
        self.assertEqual(params["critic_threshold"], 70)
        self.assertEqual(params["max_retries"], 3)

    def test_set_param(self):
        result = self.tuner.set_param("critic_threshold", 75)
        self.assertTrue(result)

        params = self.tuner.get_current_params()
        self.assertEqual(params["critic_threshold"], 75)

    def test_set_param_out_of_range(self):
        with self.assertRaises(ValueError):
            self.tuner.set_param("critic_threshold", 100)

    def test_set_unsupported_param(self):
        with self.assertRaises(ValueError):
            self.tuner.set_param("unknown_param", 123)

    def test_record_metric(self):
        result = self.tuner.record_metric("quality_score", 85.0)
        self.assertTrue(result)

    def test_generate_suggestion_insufficient_data(self):
        suggestion = self.tuner.generate_suggestion(objective="quality")
        self.assertIsNone(suggestion)

    def test_generate_suggestion_with_data(self):
        for i in range(20):
            self.tuner.record_metric("quality_score", 65.0 + i * 0.5)
            self.tuner.record_metric("response_time", 8.0)
            self.tuner.record_metric("cost", 0.3)

        suggestion = self.tuner.generate_suggestion(objective="quality", days=1)
        self.assertIsNotNone(suggestion)
        self.assertIn("critic_threshold", suggestion.proposed_params)
        self.assertEqual(suggestion.status, "pending")

    def test_approve_and_apply_suggestion(self):
        for i in range(20):
            self.tuner.record_metric("quality_score", 65.0)
            self.tuner.record_metric("response_time", 8.0)
            self.tuner.record_metric("cost", 0.3)

        suggestion = self.tuner.generate_suggestion(objective="quality", days=1)
        self.assertIsNotNone(suggestion)

        result = self.tuner.approve_suggestion(suggestion.suggestion_id, reviewer="tester")
        self.assertTrue(result)

        s = self.tuner.get_suggestion(suggestion.suggestion_id)
        self.assertEqual(s.status, "approved")

        apply_result = self.tuner.apply_suggestion(suggestion.suggestion_id)
        self.assertIn("new_params", apply_result)
        self.assertIn("old_params", apply_result)

        s2 = self.tuner.get_suggestion(suggestion.suggestion_id)
        self.assertEqual(s2.status, "applied")

    def test_reject_suggestion(self):
        for i in range(20):
            self.tuner.record_metric("quality_score", 65.0)

        suggestion = self.tuner.generate_suggestion(objective="quality", days=1)
        self.assertIsNotNone(suggestion)

        result = self.tuner.reject_suggestion(
            suggestion.suggestion_id,
            reviewer="tester",
            reason="测试拒绝"
        )
        self.assertTrue(result)

        s = self.tuner.get_suggestion(suggestion.suggestion_id)
        self.assertEqual(s.status, "rejected")

    def test_list_suggestions(self):
        for i in range(30):
            self.tuner.record_metric("quality_score", 65.0)

        for _ in range(3):
            self.tuner.generate_suggestion(objective="quality", days=1)

        suggestions = self.tuner.list_suggestions(limit=5)
        self.assertGreaterEqual(len(suggestions), 0)

    def test_generate_weekly_report(self):
        for i in range(20):
            self.tuner.record_metric("quality_score", 75.0)
            self.tuner.record_metric("response_time", 5.0)

        report = self.tuner.generate_weekly_report(objective="balanced")
        self.assertIsNotNone(report)
        self.assertIn("quality_score", report.metrics_summary)

    def test_rollback_to_snapshot(self):
        old_params = self.tuner.get_current_params()
        self.tuner.set_param("critic_threshold", 80)

        import uuid
        snapshot_id = str(uuid.uuid4())[:8]
        self.tuner._create_snapshot(snapshot_id, old_params, "测试快照")

        result = self.tuner.rollback_to_snapshot(snapshot_id)
        self.assertTrue(result)

        params = self.tuner.get_current_params()
        self.assertEqual(params["critic_threshold"], old_params["critic_threshold"])


class TestFailureAnalysisFix(unittest.TestCase):
    """失败模式自动修复建议测试"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        from agent.cognitive.failure_analysis import FailureAnalyzer, FailureType
        self.analyzer = FailureAnalyzer(storage_path=self.test_dir)
        self.analyzer.initialize()
        self.FailureType = FailureType

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_generate_auto_fix_suggestion(self):
        suggestion = self.analyzer.generate_auto_fix_suggestion(
            failure_type=self.FailureType.API_FICTION,
            target_type="prompt",
            target_id="prompt_123"
        )

        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["failure_type"], "api_fiction")
        self.assertIn("generated_prompt", suggestion)
        self.assertIn("template", suggestion)
        self.assertIn("confidence", suggestion)
        self.assertGreater(suggestion["confidence"], 0)

    def test_generate_auto_fix_suggestion_unknown_type(self):
        suggestion = self.analyzer.generate_auto_fix_suggestion(
            failure_type=self.FailureType.UNKNOWN
        )

        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["confidence"], 0.4)

    def test_generate_fix_for_all_types(self):
        for ft in self.FailureType:
            if ft == self.FailureType.UNKNOWN:
                continue
            suggestion = self.analyzer.generate_auto_fix_suggestion(ft)
            self.assertIsNotNone(suggestion)
            self.assertIn("generated_prompt", suggestion)

    def test_get_high_frequency_failures(self):
        from agent.cognitive.failure_analysis import FailureRecord, FailureSeverity

        for i in range(10):
            record = FailureRecord(
                trace_id=f"trace_{i}",
                failure_type=self.FailureType.API_FICTION,
                severity=FailureSeverity.HIGH,
                message=f"测试失败 {i}",
                source="test"
            )
            self.analyzer.record_failure(record)

        high_freq = self.analyzer.get_high_frequency_failures(threshold=5, hours=24)
        self.assertGreaterEqual(len(high_freq), 1)

    def test_batch_generate_fix_suggestions(self):
        from agent.cognitive.failure_analysis import FailureRecord, FailureSeverity

        for i in range(10):
            record = FailureRecord(
                trace_id=f"trace_batch_{i}",
                failure_type=self.FailureType.FIELD_ERROR,
                severity=FailureSeverity.MEDIUM,
                message=f"字段错误测试 {i}",
                source="test"
            )
            self.analyzer.record_failure(record)

        suggestions = self.analyzer.batch_generate_fix_suggestions(threshold=5, hours=24)
        self.assertIsInstance(suggestions, list)

    def test_track_fix_effectiveness(self):
        from agent.cognitive.failure_analysis import FailureRecord, FailureSeverity

        fix_time = time.time()

        for i in range(20):
            record = FailureRecord(
                trace_id=f"trace_before_{i}",
                failure_type=self.FailureType.TOOL_MISUSE,
                severity=FailureSeverity.LOW,
                message=f"工具使用错误 {i}",
                source="test"
            )
            record.timestamp = fix_time - 3600 - i * 60
            self.analyzer.record_failure(record)

        for i in range(5):
            record = FailureRecord(
                trace_id=f"trace_after_{i}",
                failure_type=self.FailureType.TOOL_MISUSE,
                severity=FailureSeverity.LOW,
                message=f"修复后的错误 {i}",
                source="test"
            )
            record.timestamp = fix_time + i * 60
            self.analyzer.record_failure(record)

        result = self.analyzer.track_fix_effectiveness(
            failure_type=self.FailureType.TOOL_MISUSE,
            fix_start_time=fix_time,
            window_hours=1
        )

        self.assertIsNotNone(result)
        self.assertIn("before_period", result)
        self.assertIn("after_period", result)
        self.assertIn("reduction_rate_percent", result)
        self.assertIn("is_improved", result)
        self.assertTrue(result["is_improved"])

    def test_mark_fix_applied(self):
        from agent.cognitive.failure_analysis import FailureRecord, FailureSeverity

        record = FailureRecord(
            trace_id="trace_fix_mark",
            failure_type=self.FailureType.API_FICTION,
            severity=FailureSeverity.HIGH,
            message="测试修复标记",
            source="test"
        )
        self.analyzer.record_failure(record)

        result = self.analyzer.mark_fix_applied(
            trace_id="trace_fix_mark",
            failure_type=self.FailureType.API_FICTION,
            fix_description="已修复API调用问题"
        )
        self.assertTrue(result)

    def test_format_fix_prompt(self):
        template = {
            "instruction": "测试指令",
            "constraints": ["约束1", "约束2"],
            "example": "示例内容"
        }

        prompt = self.analyzer._format_fix_prompt(template)
        self.assertIn("【系统指令】", prompt)
        self.assertIn("【约束条件】", prompt)
        self.assertIn("【示例】", prompt)
        self.assertIn("测试指令", prompt)


class TestFeedbackLoop(unittest.TestCase):
    """用户反馈闭环测试"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        from agent.feedback import FeedbackManager
        self.manager = FeedbackManager(storage_path=self.test_dir)
        self.manager.initialize()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_submit_like_feedback(self):
        record = self.manager.submit_feedback(
            trace_id="trace_test_1",
            feedback_type="like",
            rating=5,
            comment="回答很好",
            category="quality",
            user_id="user_1"
        )

        self.assertIsNotNone(record)
        self.assertEqual(record.feedback_type, "like")
        self.assertEqual(record.trace_id, "trace_test_1")
        self.assertEqual(record.rating, 5)
        self.assertIsNotNone(record.feedback_id)

    def test_submit_dislike_feedback(self):
        record = self.manager.submit_feedback(
            trace_id="trace_test_2",
            feedback_type="dislike",
            rating=2,
            comment="回答不准确",
            category="accuracy",
            user_id="user_2"
        )

        self.assertIsNotNone(record)
        self.assertEqual(record.feedback_type, "dislike")
        self.assertEqual(record.category, "accuracy")

    def test_get_feedback(self):
        record = self.manager.submit_feedback(
            trace_id="trace_get",
            feedback_type="like",
            comment="测试获取"
        )

        fetched = self.manager.get_feedback(record.feedback_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.feedback_id, record.feedback_id)

    def test_list_feedback(self):
        for i in range(10):
            self.manager.submit_feedback(
                trace_id=f"trace_list_{i}",
                feedback_type="like" if i % 2 == 0 else "dislike",
                category="quality" if i % 3 == 0 else "accuracy"
            )

        all_feedback = self.manager.list_feedback(limit=20)
        self.assertGreaterEqual(len(all_feedback), 10)

        likes = self.manager.list_feedback(feedback_type="like", limit=20)
        self.assertGreaterEqual(len(likes), 5)

    def test_get_feedback_by_trace(self):
        self.manager.submit_feedback(
            trace_id="trace_multi",
            feedback_type="like",
            comment="第一个反馈"
        )
        self.manager.submit_feedback(
            trace_id="trace_multi",
            feedback_type="dislike",
            comment="第二个反馈"
        )

        feedbacks = self.manager.get_feedback_by_trace("trace_multi")
        self.assertGreaterEqual(len(feedbacks), 2)

    def test_get_feedback_summary(self):
        for i in range(5):
            self.manager.submit_feedback(
                trace_id=f"trace_sum_{i}",
                feedback_type="like",
                category="quality"
            )
        for i in range(3):
            self.manager.submit_feedback(
                trace_id=f"trace_sum_dis_{i}",
                feedback_type="dislike",
                category="accuracy"
            )

        summary = self.manager.get_feedback_summary(days=1)
        self.assertIn("total_feedback", summary)
        self.assertIn("by_type", summary)
        self.assertIn("satisfaction_rate_percent", summary)
        self.assertGreaterEqual(summary["total_feedback"], 8)

    def test_quality_cases_generated(self):
        self.manager.submit_feedback(
            trace_id="trace_quality",
            feedback_type="like",
            rating=5,
            comment="非常好的回答",
            category="quality"
        )

        cases = self.manager.list_quality_cases(limit=10)
        self.assertGreaterEqual(len(cases), 1)
        self.assertEqual(cases[0].trace_id, "trace_quality")

    def test_resolve_feedback(self):
        record = self.manager.submit_feedback(
            trace_id="trace_resolve",
            feedback_type="dislike",
            comment="需要解决的问题"
        )

        result = self.manager.resolve_feedback(
            feedback_id=record.feedback_id,
            resolution="已修复相关问题",
            resolver="admin"
        )
        self.assertTrue(result)

        fetched = self.manager.get_feedback(record.feedback_id)
        self.assertEqual(fetched.status, "resolved")

    def test_generate_feedback_report(self):
        for i in range(5):
            self.manager.submit_feedback(
                trace_id=f"trace_report_{i}",
                feedback_type="like" if i < 3 else "dislike",
                category="quality" if i % 2 == 0 else "accuracy",
                comment=f"测试反馈{i}"
            )

        report = self.manager.generate_feedback_report(days=1)
        self.assertIsNotNone(report)
        self.assertIn("summary", report)
        self.assertIn("top_issues", report)
        self.assertIn("optimization_suggestions", report)
        self.assertIn("quality_cases", report)

    def test_submit_suggestion_feedback(self):
        record = self.manager.submit_feedback(
            trace_id="trace_suggestion",
            feedback_type="suggestion",
            comment="希望增加更多功能",
            category="usability"
        )

        self.assertIsNotNone(record)
        self.assertEqual(record.feedback_type, "suggestion")

    def test_submit_report_feedback(self):
        record = self.manager.submit_feedback(
            trace_id="trace_report_feedback",
            feedback_type="report",
            comment="举报不当内容",
            category="safety"
        )

        self.assertIsNotNone(record)
        self.assertEqual(record.feedback_type, "report")


class TestIntegration(unittest.TestCase):
    """集成测试：验证各模块协同工作"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_ab_test_with_feedback(self):
        """A/B 实验 + 用户反馈集成测试"""
        from agent.ab_testing import ABTestManager, ExperimentVariant
        from agent.feedback import FeedbackManager

        ab_mgr = ABTestManager(storage_path=os.path.join(self.test_dir, "ab"))
        fb_mgr = FeedbackManager(storage_path=os.path.join(self.test_dir, "fb"))
        ab_mgr.initialize()
        fb_mgr.initialize()

        variants = [
            ExperimentVariant(variant_id="v1", name="版本A", weight=50, is_control=True),
            ExperimentVariant(variant_id="v2", name="版本B", weight=50),
        ]
        exp = ab_mgr.create_experiment(
            name="集成测试实验",
            variants=variants,
            target_metric="user_feedback"
        )
        ab_mgr.start_experiment(exp.experiment_id)

        for i in range(20):
            user_id = f"user_{i}"
            variant = ab_mgr.assign_variant(exp.experiment_id, user_id)
            self.assertIsNotNone(variant)

            feedback_type = "like" if variant.variant_id == "v2" else "dislike"
            rating = 5 if feedback_type == "like" else 3

            fb_record = fb_mgr.submit_feedback(
                trace_id=f"trace_{i}",
                feedback_type=feedback_type,
                rating=rating,
                user_id=user_id
            )

            value = 1.0 if feedback_type == "like" else 0.0
            ab_mgr.record_metric(
                exp.experiment_id, variant.variant_id,
                "user_feedback", value,
                trace_id=f"trace_{i}", user_id=user_id
            )

        result = ab_mgr.analyze_results(exp.experiment_id)
        self.assertIsNotNone(result)
        self.assertGreater(result.sample_size, 0)

    def test_failure_analysis_with_feedback(self):
        """失败分析 + 用户反馈集成测试"""
        from agent.cognitive.failure_analysis import FailureAnalyzer, FailureType
        from agent.feedback import FeedbackManager

        fa_mgr = FailureAnalyzer(storage_path=os.path.join(self.test_dir, "fa"))
        fb_mgr = FeedbackManager(storage_path=os.path.join(self.test_dir, "fb"))
        fa_mgr.initialize()
        fb_mgr.initialize()

        for i in range(10):
            fb_mgr.submit_feedback(
                trace_id=f"trace_fail_{i}",
                feedback_type="dislike",
                rating=2,
                comment=f"回答不准确，存在错误{i}",
                category="accuracy"
            )

        suggestions = fa_mgr.batch_generate_fix_suggestions(threshold=1, hours=24)
        self.assertIsInstance(suggestions, list)

    def test_auto_tuner_with_metrics(self):
        """自动调优 + 指标反馈集成测试"""
        from agent.auto_tuner import AutoTuner
        from agent.ab_testing import ABTestManager, ExperimentVariant

        tuner = AutoTuner(storage_path=os.path.join(self.test_dir, "tuner"))
        ab_mgr = ABTestManager(storage_path=os.path.join(self.test_dir, "ab"))
        tuner.initialize()
        ab_mgr.initialize()

        for i in range(100):
            params = tuner.get_current_params()
            quality = 70 + i * 0.1
            response_time = 5.0 - i * 0.02
            cost = 0.2 + i * 0.001

            tuner.record_metric("quality_score", quality, params=params)
            tuner.record_metric("response_time", response_time, params=params)
            tuner.record_metric("cost", cost, params=params)

        suggestion = tuner.generate_suggestion(objective="balanced", days=1)
        if suggestion:
            self.assertIsNotNone(suggestion.proposed_params)


if __name__ == '__main__':
    unittest.main(verbosity=2)
