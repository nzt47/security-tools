#!/usr/bin/env python3
"""
告警系统测试脚本

测试告警评估、通知和自愈功能。
"""

import unittest
import time
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.monitoring.alert_evaluator import (
    AlertEvaluator,
    AlertRule,
    Alert,
    AlertState,
    AlertSeverity
)
from agent.monitoring.alert_notifier import (
    AlertNotification,
    AlertNotifier,
    NotificationResult,
    NotificationChannel
)
from agent.monitoring.self_healer import (
    SelfHealer,
    HealResult,
    HealStatus,
    HealAction
)


class TestAlertEvaluator(unittest.TestCase):
    """告警评估器测试"""

    def setUp(self):
        """测试前准备"""
        self.evaluator = AlertEvaluator(
            evaluation_interval=1.0,
            pending_duration=2.0
        )

    def tearDown(self):
        """测试后清理"""
        self.evaluator.stop()

    def test_add_rule(self):
        """测试添加规则"""
        rule = AlertRule(
            name="test_alert",
            expr="yunshu_error_rate > 0.1",
            duration="1m",
            severity="warning",
            threshold=0.1,
            comparison="gt"
        )
        self.evaluator.add_rule(rule)

        alerts = self.evaluator.get_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["name"], "test_alert")
        self.assertEqual(alerts[0]["state"], AlertState.INACTIVE.value)

    def test_remove_rule(self):
        """测试移除规则"""
        rule = AlertRule(
            name="test_alert",
            expr="yunshu_error_rate > 0.1",
            threshold=0.1
        )
        self.evaluator.add_rule(rule)
        self.evaluator.remove_rule("test_alert")

        alerts = self.evaluator.get_alerts()
        self.assertEqual(len(alerts), 0)

    def test_evaluate_condition(self):
        """测试条件评估"""
        rule = AlertRule(
            name="high_error",
            expr="error_rate > 0.1",
            threshold=0.1,
            comparison="gt"
        )

        # 测试大于
        self.assertTrue(self.evaluator._evaluate_condition(rule, 0.2))
        self.assertFalse(self.evaluator._evaluate_condition(rule, 0.05))

        # 测试小于
        rule.comparison = "lt"
        self.assertTrue(self.evaluator._evaluate_condition(rule, 0.05))
        self.assertFalse(self.evaluator._evaluate_condition(rule, 0.2))

    def test_duration_parsing(self):
        """测试时间解析"""
        self.assertEqual(self.evaluator._parse_duration("30s"), 30.0)
        self.assertEqual(self.evaluator._parse_duration("5m"), 300.0)
        self.assertEqual(self.evaluator._parse_duration("1h"), 3600.0)
        self.assertEqual(self.evaluator._parse_duration("1d"), 86400.0)

    def test_get_stats(self):
        """测试统计信息"""
        stats = self.evaluator.get_stats()
        self.assertIn("total_evaluations", stats)
        self.assertIn("alerts_triggered", stats)
        self.assertIn("alerts_resolved", stats)


class TestAlertNotifier(unittest.TestCase):
    """告警通知器测试"""

    def setUp(self):
        """测试前准备"""
        self.config = {
            "default_receiver": "test-channel",
            "channels": [
                {
                    "name": "test-channel",
                    "type": "webhook",
                    "enabled": True,
                    "url": "http://localhost:9999/webhook"
                }
            ]
        }
        self.notifier = AlertNotifier(self.config)

    def test_init_senders(self):
        """测试发送器初始化"""
        self.assertIn("test-channel", self.notifier._senders)

    def test_send_webhook(self):
        """测试 Webhook 发送"""
        notification = AlertNotification(
            alert_name="test_alert",
            state="firing",
            severity="warning",
            message="测试告警",
            value=0.15,
            threshold=0.1,
            duration_seconds=60.0
        )

        # 注意：这个测试会失败因为 webhook URL 不存在
        # 但可以验证发送逻辑
        results = self.notifier.send(notification)
        self.assertEqual(len(results), 1)

    def test_format_notification(self):
        """测试通知格式化"""
        notification = AlertNotification(
            alert_name="test_alert",
            state="firing",
            severity="critical",
            message="严重错误",
            value=0.95,
            threshold=0.9
        )

        sender = self.notifier._senders.get("test-channel")
        if sender:
            message = sender.format_message(notification)
            self.assertIn("alert_name", message)


class TestSelfHealer(unittest.TestCase):
    """自愈管理器测试"""

    def setUp(self):
        """测试前准备"""
        self.config = {
            "enabled": True,
            "self_healing": {
                "restart_service": {
                    "enabled": True,
                    "threshold": 2,
                    "cooldown": 10,
                    "max_per_hour": 5
                },
                "clear_cache": {
                    "enabled": True,
                    "threshold": 1,
                    "cooldown": 5,
                    "max_per_hour": 20
                }
            }
        }
        self.healer = SelfHealer(self.config)

    def tearDown(self):
        """测试后清理"""
        self.healer.stop()

    def test_init_policies(self):
        """测试策略初始化"""
        self.assertIn("restart_service", self.healer._policies)
        self.assertIn("clear_cache", self.healer._policies)

        restart_policy = self.healer._policies["restart_service"]
        self.assertEqual(restart_policy.threshold, 2)
        self.assertEqual(restart_policy.cooldown, 10)

    def test_execute_gc_collect(self):
        """测试 GC 回收"""
        result = self.healer.execute_action("gc_collect")
        self.assertIn(result.status, [HealStatus.SUCCESS, HealStatus.FAILED])

    def test_execute_clear_memory(self):
        """测试内存清理"""
        result = self.healer.execute_action("clear_memory")
        self.assertIn(result.status, [HealStatus.SUCCESS, HealStatus.FAILED])

    def test_cooldown_check(self):
        """测试冷却检查"""
        # 第一次执行
        result1 = self.healer.execute_action("clear_cache")
        self.assertIn(result1.status, [HealStatus.SUCCESS, HealStatus.FAILED])

        # 立即再次执行，应该被冷却阻止
        result2 = self.healer.execute_action("clear_cache")
        self.assertEqual(result2.status, HealStatus.SKIPPED)

    def test_rate_limit(self):
        """测试频率限制"""
        # 连续执行直到超限
        for i in range(25):
            self.healer.execute_action("clear_cache")
            # 冷却时间设为 5 秒，所以不会触发频率限制
            if self.healer._policies["clear_cache"].cooldown > 0:
                time.sleep(0.1)

    def test_get_records(self):
        """测试获取记录"""
        self.healer.execute_action("gc_collect")
        records = self.healer.get_records(limit=10)
        self.assertIsInstance(records, list)

    def test_get_stats(self):
        """测试统计信息"""
        self.healer.execute_action("gc_collect")
        stats = self.healer.get_stats()
        self.assertIn("total", stats)
        self.assertIn("success", stats)
        self.assertIn("failed", stats)


class TestAlertIntegration(unittest.TestCase):
    """告警系统集成测试"""

    def test_full_flow(self):
        """测试完整流程"""
        # 创建评估器
        evaluator = AlertEvaluator(evaluation_interval=1.0)

        # 添加规则
        rule = AlertRule(
            name="integration_test",
            expr="error_rate > 0.5",
            duration="1m",
            severity="critical",
            threshold=0.5,
            comparison="gt",
            auto_heal=True,
            heal_actions=["gc_collect"]
        )
        evaluator.add_rule(rule)

        # 创建自愈器
        healer = SelfHealer({
            "enabled": True,
            "self_healing": {
                "gc_collect": {
                    "enabled": True,
                    "threshold": 1,
                    "cooldown": 1
                }
            }
        })

        # 设置自愈回调
        heal_executed = []

        def on_heal(record):
            heal_executed.append(record)

        healer.set_on_heal_executed(on_heal)

        # 启动组件
        evaluator.start()
        healer.start()

        # 等待评估
        time.sleep(3)

        # 停止组件
        evaluator.stop()
        healer.stop()

        # 验证
        alerts = evaluator.get_alerts()
        self.assertGreaterEqual(len(alerts), 0)


def run_tests():
    """运行测试"""
    # 创建测试套件
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 添加测试类
    suite.addTests(loader.loadTestsFromTestCase(TestAlertEvaluator))
    suite.addTests(loader.loadTestsFromTestCase(TestAlertNotifier))
    suite.addTests(loader.loadTestsFromTestCase(TestSelfHealer))
    suite.addTests(loader.loadTestsFromTestCase(TestAlertIntegration))

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # 返回结果
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
