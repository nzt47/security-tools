#!/usr/bin/env python3
"""
第三阶段演化工程单元测试

测试内容：
1. Trace 持久化存储与可视化
2. 失败模式分类归档
3. Prompt 版本化管理
"""

import unittest
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import tempfile


class TestTracePersistence(unittest.TestCase):
    """Trace 持久化存储测试"""
    
    def setUp(self):
        """设置测试环境"""
        from agent.monitoring.tracing import TraceStorage, TraceRecord, get_trace_storage
        
        # 使用临时目录
        self.temp_dir = tempfile.mkdtemp()
        self.storage = TraceStorage(storage_path=self.temp_dir)
        self.TraceRecord = TraceRecord
    
    def tearDown(self):
        """清理测试环境"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_trace_record_creation(self):
        """测试 TraceRecord 创建"""
        record = self.TraceRecord(trace_id="test-trace-001")
        
        self.assertEqual(record.trace_id, "test-trace-001")
        self.assertEqual(record.spans, [])
        self.assertIsNotNone(record.created_at)
    
    def test_trace_add_span(self):
        """测试添加 Span"""
        record = self.TraceRecord(trace_id="test-trace-002")
        
        span_data = {
            "span_id": "span-001",
            "service": "TestService",
            "operation": "test_op",
            "start_time": 1234567890.0,
            "end_time": 1234567891.0,
            "duration_ms": 1000.0
        }
        
        record.add_span(span_data)
        
        self.assertEqual(len(record.spans), 1)
        self.assertEqual(record.spans[0]["span_id"], "span-001")
    
    def test_trace_save_and_load(self):
        """测试保存和加载 Trace"""
        record = self.TraceRecord(trace_id="test-trace-003")
        record.add_span({
            "span_id": "span-001",
            "service": "ServiceA",
            "operation": "operation1"
        })
        
        self.storage.save_trace(record)
        loaded = self.storage.load_trace("test-trace-003")
        
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.trace_id, "test-trace-003")
        self.assertEqual(len(loaded.spans), 1)
    
    def test_trace_list(self):
        """测试列出 Trace"""
        for i in range(3):
            record = self.TraceRecord(trace_id=f"test-trace-{i:03d}")
            record.add_span({"span_id": f"span-{i}"})
            self.storage.save_trace(record)
        
        traces = self.storage.list_traces()
        
        self.assertEqual(len(traces), 3)
        self.assertEqual(traces[0]["trace_id"], "test-trace-002")  # 按时间倒序
    
    def test_build_flow_chart_data(self):
        """测试构建流程图数据"""
        record = self.TraceRecord(trace_id="flow-test-001")
        record.add_span({
            "span_id": "span-1",
            "service": "ServiceA",
            "operation": "start",
            "start_time": 1234567890.0,
            "end_time": 1234567890.5,
            "duration_ms": 500.0
        })
        record.add_span({
            "span_id": "span-2",
            "parent_span_id": "span-1",
            "service": "ServiceB",
            "operation": "process",
            "start_time": 1234567890.5,
            "end_time": 1234567891.0,
            "duration_ms": 500.0
        })
        
        self.storage.save_trace(record)
        
        loaded_record = self.storage.load_trace("flow-test-001")
        self.assertIsNotNone(loaded_record)
        
        # 验证记录内容
        self.assertEqual(loaded_record.trace_id, "flow-test-001")
        self.assertEqual(len(loaded_record.spans), 2)


class TestFailureAnalysis(unittest.TestCase):
    """失败模式分类归档测试"""
    
    def setUp(self):
        """设置测试环境"""
        from agent.cognitive.failure_analysis import FailureAnalyzer
        
        self.temp_dir = tempfile.mkdtemp()
        self.analyzer = FailureAnalyzer(storage_path=self.temp_dir)
        self.analyzer.initialize()
    
    def tearDown(self):
        """清理测试环境"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_failure_type_classification(self):
        """测试失败类型分类"""
        test_cases = [
            ("调用不存在的API", "api_fiction"),
            ("字段类型错误", "field_error"),
            ("跳过必要步骤", "flow_skip"),
            ("虚构数据", "data_invention"),
            ("工具使用错误", "tool_misuse"),
            ("上下文丢失", "context_loss"),
            ("未知错误", "unknown"),
        ]
        
        for message, expected_type in test_cases:
            result = self.analyzer.classify_failure(message)
            self.assertEqual(result.value, expected_type, 
                           f"Failed for message: {message}")
    
    def test_record_failure(self):
        """测试记录失败案例"""
        from agent.cognitive.failure_analysis import FailureRecord, FailureType, FailureSeverity
        
        record = FailureRecord(
            trace_id="test-trace-001",
            failure_type=FailureType.API_FICTION,
            severity=FailureSeverity.HIGH,
            message="调用不存在的 API: get_user_info",
            source="test_module"
        )
        
        self.analyzer.record_failure(record)
        
        failures = self.analyzer.query_failures(trace_id="test-trace-001")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["failure_type"], "api_fiction")
    
    def test_generate_fix_suggestion(self):
        """测试生成优化建议"""
        from agent.cognitive.failure_analysis import FailureType
        
        suggestion = self.analyzer.generate_fix_suggestion(FailureType.API_FICTION)
        
        self.assertIn("检查 API 文档", suggestion)
        self.assertIn("验证 API 名称拼写", suggestion)
    
    def test_get_failure_summary(self):
        """测试获取失败汇总"""
        from agent.cognitive.failure_analysis import FailureRecord, FailureType, FailureSeverity
        
        for i in range(5):
            record = FailureRecord(
                trace_id=f"trace-{i}",
                failure_type=FailureType.API_FICTION if i % 2 == 0 else FailureType.FIELD_ERROR,
                severity=FailureSeverity.MEDIUM,
                message=f"Test failure {i}",
                source="test"
            )
            self.analyzer.record_failure(record)
        
        summary = self.analyzer.get_failure_summary(hours=1)
        
        self.assertEqual(summary["total_failures"], 5)
        self.assertIn("api_fiction", summary["by_type"])
        self.assertIn("field_error", summary["by_type"])


class TestPromptManager(unittest.TestCase):
    """Prompt 版本化管理测试"""
    
    def setUp(self):
        """设置测试环境"""
        from agent.prompt_manager.storage import PromptStorage, get_prompt_storage
        from agent.prompt_manager.version_control import VersionManager, get_version_manager
        from agent.prompt_manager.registry import PromptRegistry, get_prompt_registry
        
        self.temp_dir = tempfile.mkdtemp()
        
        # 重新初始化全局实例
        global _global_prompt_storage, _global_version_manager, _global_prompt_registry
        _global_prompt_storage = None
        _global_version_manager = None
        _global_prompt_registry = None
        
        self.storage = PromptStorage(storage_path=self.temp_dir)
        self.storage.initialize()
        
        self.version_manager = VersionManager(storage=self.storage)
        self.registry = PromptRegistry(storage=self.storage)
    
    def tearDown(self):
        """清理测试环境"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_register_prompt(self):
        """测试注册提示词"""
        from agent.prompt_manager.storage import PromptType
        
        record = self.registry.register_prompt(
            prompt_id="test-prompt-001",
            name="Test Prompt",
            content="This is a test prompt",
            prompt_type=PromptType.SYSTEM,
            description="A test prompt",
            tags=["test", "system"]
        )
        
        self.assertEqual(record.prompt_id, "test-prompt-001")
        self.assertEqual(record.name, "Test Prompt")
        self.assertEqual(record.prompt_type, PromptType.SYSTEM)
    
    def test_create_version(self):
        """测试创建版本"""
        from agent.prompt_manager.storage import PromptType
        
        self.registry.register_prompt(
            prompt_id="test-prompt-002",
            name="Version Test",
            content="Version 1 content",
            prompt_type=PromptType.SYSTEM
        )
        
        version = self.version_manager.create_version(
            prompt_id="test-prompt-002",
            change_log="Initial version",
            author="test_user"
        )
        
        self.assertEqual(version.version_number, "1.0.0")
        self.assertEqual(version.status, "draft")
    
    def test_version_history(self):
        """测试版本历史"""
        from agent.prompt_manager.storage import PromptType
        
        self.registry.register_prompt(
            prompt_id="test-prompt-003",
            name="History Test",
            content="v1",
            prompt_type=PromptType.SYSTEM
        )
        
        # 创建多个版本
        self.version_manager.create_version("test-prompt-003", "v1")
        self.registry.update_prompt("test-prompt-003", content="v2")
        self.version_manager.create_version("test-prompt-003", "v2")
        
        history = self.version_manager.get_version_history("test-prompt-003")
        
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].version_number, "1.0.1")
        self.assertEqual(history[1].version_number, "1.0.0")
    
    def test_compare_versions(self):
        """测试版本对比"""
        from agent.prompt_manager.storage import PromptType
        
        self.registry.register_prompt(
            prompt_id="test-prompt-004",
            name="Compare Test",
            content="Hello World",
            prompt_type=PromptType.SYSTEM
        )
        
        self.version_manager.create_version("test-prompt-004", "Initial")
        self.registry.update_prompt("test-prompt-004", content="Hello Universe")
        self.version_manager.create_version("test-prompt-004", "Changed world to universe")
        
        diff = self.version_manager.compare_versions(
            "test-prompt-004", "1.0.0", "1.0.1"
        )
        
        self.assertEqual(diff["added_lines"], 1)
        self.assertEqual(diff["removed_lines"], 1)
        self.assertIn("Universe", diff["diff"])
    
    def test_rollback_version(self):
        """测试版本回滚"""
        from agent.prompt_manager.storage import PromptType
        
        self.registry.register_prompt(
            prompt_id="test-prompt-005",
            name="Rollback Test",
            content="Original content",
            prompt_type=PromptType.SYSTEM
        )
        
        self.version_manager.create_version("test-prompt-005", "v1")
        self.registry.update_prompt("test-prompt-005", content="Modified content")
        self.version_manager.create_version("test-prompt-005", "v2")
        
        # 回滚到 v1
        result = self.version_manager.rollback_to_version("test-prompt-005", "1.0.0")
        
        self.assertTrue(result)
        
        prompt = self.registry.get_prompt("test-prompt-005")
        self.assertEqual(prompt.content, "Original content")
    
    def test_analyze_impact(self):
        """测试影响分析"""
        from agent.prompt_manager.storage import PromptType
        
        self.registry.register_prompt(
            prompt_id="test-prompt-006",
            name="Impact Test",
            content="This is a test prompt with api_key and secret",
            prompt_type=PromptType.SYSTEM
        )
        
        impact = self.version_manager.analyze_impact("test-prompt-006")
        
        self.assertIn("risks", impact)
        self.assertIn("检测到敏感信息关键词", impact["risks"])
    
    def test_run_regression_test(self):
        """测试回归测试"""
        from agent.prompt_manager.storage import PromptType
        
        self.registry.register_prompt(
            prompt_id="test-prompt-007",
            name="Regression Test",
            content="Hello World with required_pattern",
            prompt_type=PromptType.SYSTEM
        )
        
        version = self.version_manager.create_version("test-prompt-007", "v1")
        
        test_cases = [
            {
                "id": "test-001",
                "description": "Check required pattern",
                "expected_patterns": ["required_pattern"],
                "forbidden_patterns": ["bad_pattern"]
            }
        ]
        
        results = self.version_manager.run_regression_test(version.version_id, test_cases)
        
        self.assertEqual(results["passed"], 1)
        self.assertEqual(results["failed"], 0)
        self.assertEqual(results["status"], "passed")


# 全局变量重置
_global_prompt_storage = None
_global_version_manager = None
_global_prompt_registry = None


if __name__ == "__main__":
    unittest.main(verbosity=2)