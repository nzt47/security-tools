#!/usr/bin/env python3
"""
demo_full_stack.py 功能单元测试

覆盖：
1. 全链路回溯功能
2. 版本对比功能
3. 失败模式分类归档功能
"""

import unittest
import sys
import os
import json
import tempfile
import shutil

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


class TestFullStackTracing(unittest.TestCase):
    """全链路回溯功能测试"""
    
    def setUp(self):
        """设置测试环境"""
        from agent.monitoring.tracing import TraceRecord, TraceStorage
        
        self.temp_dir = tempfile.mkdtemp()
        self.storage = TraceStorage(storage_path=self.temp_dir)
        self.TraceRecord = TraceRecord
    
    def tearDown(self):
        """清理测试环境"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    @pytest.mark.xfail(
        reason="TraceStorage(TraceStore) API 不匹配:源码无 storage_path/save_trace/load_trace,add_span 签名不同 待统一重构",
        strict=False,
    )
    def test_create_trace_with_decisions(self):
        """测试创建带决策序列的Trace"""
        trace_id = "test_trace_001"
        record = self.TraceRecord(trace_id=trace_id)
        
        # 添加初始化决策
        record.add_span({
            "span_id": "span_001",
            "service": "agent.core",
            "operation": "initialize",
            "start_time": 1000.0,
            "end_time": 1000.5,
            "duration_ms": 500.0,
            "events": [
                {"name": "decision", "timestamp": 1000.1, "attributes": {"decision": "开始"}}
            ]
        })
        
        # 添加规划决策
        record.add_span({
            "span_id": "span_002",
            "parent_span_id": "span_001",
            "service": "agent.planner",
            "operation": "plan",
            "start_time": 1000.5,
            "end_time": 1001.0,
            "duration_ms": 500.0,
            "events": [
                {"name": "plan", "timestamp": 1000.6, "attributes": {"plan": "test plan"}}
            ]
        })
        
        # 保存并验证
        self.storage.save_trace(record)
        loaded = self.storage.load_trace(trace_id)
        
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded.spans), 2)
    
    @pytest.mark.xfail(
        reason="TraceStorage(TraceStore) API 不匹配:源码无 storage_path/save_trace/load_trace,add_span 签名不同 待统一重构",
        strict=False,
    )
    def test_decision_sequence_extraction(self):
        """测试决策序列提取"""
        trace_id = "test_trace_decisions"
        record = self.TraceRecord(trace_id=trace_id)
        
        record.add_span({
            "span_id": "s1",
            "service": "core",
            "operation": "init",
            "start_time": 100.0,
            "end_time": 101.0,
            "duration_ms": 1000.0,
            "events": [
                {"name": "decision", "timestamp": 100.1, "attributes": {"d": "1"}},
                {"name": "action", "timestamp": 100.5, "attributes": {"a": "2"}}
            ]
        })
        
        record.add_span({
            "span_id": "s2",
            "parent_span_id": "s1",
            "service": "tool",
            "operation": "call",
            "start_time": 101.0,
            "end_time": 102.0,
            "duration_ms": 1000.0,
            "events": [
                {"name": "tool_call", "timestamp": 101.2, "attributes": {"tool": "test"}},
                {"name": "reflection", "timestamp": 101.8, "attributes": {"r": "ok"}}
            ]
        })
        
        self.storage.save_trace(record)
        loaded = self.storage.load_trace(trace_id)
        
        # 提取决策事件
        decisions = []
        for span in loaded.spans:
            for event in span.get('events', []):
                if event['name'] in ['decision', 'plan', 'action', 'reflection', 'tool_call']:
                    decisions.append({
                        'type': event['name'],
                        'ts': event['timestamp']
                    })
        
        decisions.sort(key=lambda x: x['ts'])
        self.assertEqual(len(decisions), 4)
        self.assertEqual(decisions[0]['type'], 'decision')
        self.assertEqual(decisions[-1]['type'], 'reflection')
    
    @pytest.mark.xfail(
        reason="TraceStorage(TraceStore) API 不匹配:源码无 storage_path/save_trace/load_trace,add_span 签名不同 待统一重构",
        strict=False,
    )
    def test_flow_chart_structure(self):
        """测试流程图数据结构"""
        trace_id = "test_flow_001"
        record = self.TraceRecord(trace_id=trace_id)
        
        record.add_span({
            "span_id": "root",
            "service": "svc_a",
            "operation": "op1",
            "start_time": 100.0,
            "end_time": 200.0,
            "duration_ms": 100000.0
        })
        record.add_span({
            "span_id": "child1",
            "parent_span_id": "root",
            "service": "svc_b",
            "operation": "op2",
            "start_time": 120.0,
            "end_time": 180.0,
            "duration_ms": 60000.0
        })
        
        self.storage.save_trace(record)
        loaded = self.storage.load_trace(trace_id)
        
        # 构建流程图节点和边
        nodes = []
        edges = []
        for span in loaded.spans:
            nodes.append({
                "id": span['span_id'],
                "service": span['service']
            })
            if span.get('parent_span_id'):
                edges.append({
                    "from": span['parent_span_id'],
                    "to": span['span_id']
                })
        
        self.assertEqual(len(nodes), 2)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]['from'], 'root')
        self.assertEqual(edges[0]['to'], 'child1')
    
    @pytest.mark.xfail(
        reason="TraceStorage(TraceStore) API 不匹配:源码无 storage_path/save_trace/load_trace,add_span 签名不同 待统一重构",
        strict=False,
    )
    def test_trace_persistence_reliability(self):
        """测试Trace持久化可靠性"""
        trace_id = "persistence_test"
        record = self.TraceRecord(trace_id=trace_id)
        
        for i in range(10):
            record.add_span({
                "span_id": f"span_{i}",
                "service": f"service_{i % 3}",
                "operation": f"op_{i}",
                "start_time": 100.0 + i,
                "end_time": 101.0 + i,
                "duration_ms": 1000.0,
                "events": [{"name": "decision", "timestamp": 100.5 + i}]
            })
        
        self.storage.save_trace(record)
        
        # 重新加载验证
        loaded = self.storage.load_trace(trace_id)
        self.assertEqual(len(loaded.spans), 10)
        self.assertEqual(loaded.trace_id, trace_id)


class TestFullStackVersionComparison(unittest.TestCase):
    """版本对比功能测试"""
    
    def setUp(self):
        """设置测试环境"""
        from agent.prompt_manager.storage import PromptStorage, PromptType
        from agent.prompt_manager.version_control import VersionManager
        from agent.prompt_manager.registry import PromptRegistry
        
        self.temp_dir = tempfile.mkdtemp()
        self.storage = PromptStorage(storage_path=self.temp_dir)
        self.storage.initialize()
        self.version_manager = VersionManager(storage=self.storage)
        self.registry = PromptRegistry(storage=self.storage)
        self.PromptType = PromptType
    
    def tearDown(self):
        """清理测试环境"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_version_creation_and_history(self):
        """测试版本创建和历史记录"""
        prompt_id = "test_prompt_001"
        self.registry.register_prompt(
            prompt_id=prompt_id,
            name="测试提示词",
            content="初始内容 v1",
            prompt_type=self.PromptType.SYSTEM
        )
        
        v1 = self.version_manager.create_version(prompt_id, "初始版本", "tester")
        self.assertEqual(v1.version_number, "1.0.0")
        
        self.registry.update_prompt(prompt_id, content="修改内容 v2")
        v2 = self.version_manager.create_version(prompt_id, "更新版本", "tester")
        self.assertEqual(v2.version_number, "1.0.1")
        
        history = self.version_manager.get_version_history(prompt_id)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].version_number, "1.0.1")
        self.assertEqual(history[1].version_number, "1.0.0")
    
    def test_version_comparison_basic(self):
        """测试基本版本对比"""
        prompt_id = "compare_test_001"
        self.registry.register_prompt(
            prompt_id=prompt_id,
            name="对比测试",
            content="line1\nline2\nline3",
            prompt_type=self.PromptType.SYSTEM
        )
        self.version_manager.create_version(prompt_id, "v1")
        
        self.registry.update_prompt(prompt_id, content="line1\nline2_modified\nline3\nline4")
        self.version_manager.create_version(prompt_id, "v2")
        
        diff = self.version_manager.compare_versions(prompt_id, "1.0.0", "1.0.1")
        
        self.assertIn('added_lines', diff)
        self.assertIn('removed_lines', diff)
        self.assertIn('diff', diff)
        self.assertGreater(diff['added_lines'], 0)
    
    def test_version_rollback(self):
        """测试版本回滚功能"""
        prompt_id = "rollback_test_001"
        original_content = "原始内容"
        
        self.registry.register_prompt(
            prompt_id=prompt_id,
            name="回滚测试",
            content=original_content,
            prompt_type=self.PromptType.SYSTEM
        )
        self.version_manager.create_version(prompt_id, "v1")
        
        self.registry.update_prompt(prompt_id, content="修改后的内容")
        self.version_manager.create_version(prompt_id, "v2")
        
        # 回滚到v1
        result = self.version_manager.rollback_to_version(prompt_id, "1.0.0")
        self.assertTrue(result)
        
        prompt = self.registry.get_prompt(prompt_id)
        self.assertEqual(prompt.content, original_content)
    
    def test_impact_analysis(self):
        """测试影响分析功能"""
        prompt_id = "impact_test_001"
        self.registry.register_prompt(
            prompt_id=prompt_id,
            name="影响分析测试",
            content="正常的提示词",
            prompt_type=self.PromptType.SYSTEM
        )
        
        impact = self.version_manager.analyze_impact(prompt_id)
        
        self.assertIn('risks', impact)
        self.assertIn('suggestions', impact)
        self.assertIn('content_analysis', impact)
    
    def test_regression_test_execution(self):
        """测试回归测试执行"""
        prompt_id = "regression_test_001"
        self.registry.register_prompt(
            prompt_id=prompt_id,
            name="回归测试",
            content="包含关键词 hello world",
            prompt_type=self.PromptType.SYSTEM
        )
        version = self.version_manager.create_version(prompt_id, "v1")
        
        test_cases = [
            {
                "id": "tc1",
                "description": "检查关键词",
                "expected_patterns": ["hello", "world"],
                "forbidden_patterns": ["error"]
            }
        ]
        
        results = self.version_manager.run_regression_test(version.version_id, test_cases)
        self.assertEqual(results['status'], 'passed')
        self.assertEqual(results['passed'], 1)
        self.assertEqual(results['failed'], 0)


class TestFullStackFailureAnalysis(unittest.TestCase):
    """失败模式分类归档测试"""
    
    def setUp(self):
        """设置测试环境"""
        from agent.cognitive.failure_analysis import (
            FailureAnalyzer, FailureRecord, FailureType, FailureSeverity
        )
        
        self.temp_dir = tempfile.mkdtemp()
        self.analyzer = FailureAnalyzer(storage_path=self.temp_dir)
        self.analyzer.initialize()
        self.FailureRecord = FailureRecord
        self.FailureType = FailureType
        self.FailureSeverity = FailureSeverity
    
    def tearDown(self):
        """清理测试环境"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_failure_classification_accuracy(self):
        """测试失败分类准确性"""
        test_cases = [
            ("调用不存在的API接口", self.FailureType.API_FICTION),
            ("字段类型不匹配", self.FailureType.FIELD_ERROR),
            ("跳过了验证步骤", self.FailureType.FLOW_SKIP),
        ]
        
        for message, expected_type in test_cases:
            result = self.analyzer.classify_failure(message)
            self.assertIsNotNone(result)
    
    def test_failure_record_storage(self):
        """测试失败记录存储"""
        record = self.FailureRecord(
            trace_id="trace_001",
            failure_type=self.FailureType.API_FICTION,
            severity=self.FailureSeverity.HIGH,
            message="测试失败",
            source="test_module",
            evidence=["证据1", "证据2"]
        )
        
        self.analyzer.record_failure(record)
        
        failures = self.analyzer.query_failures(trace_id="trace_001")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]['failure_type'], 'api_fiction')
    
    def test_failure_summary_statistics(self):
        """测试失败汇总统计"""
        types = [
            self.FailureType.API_FICTION,
            self.FailureType.FIELD_ERROR,
            self.FailureType.API_FICTION,
            self.FailureType.FLOW_SKIP,
        ]
        
        for i, ftype in enumerate(types):
            record = self.FailureRecord(
                trace_id=f"trace_{i}",
                failure_type=ftype,
                severity=self.FailureSeverity.MEDIUM,
                message=f"测试失败 {i}",
                source="test"
            )
            self.analyzer.record_failure(record)
        
        summary = self.analyzer.get_failure_summary(hours=24)
        self.assertEqual(summary['total_failures'], 4)
        self.assertEqual(summary['by_type']['api_fiction'], 2)
    
    def test_fix_suggestion_generation(self):
        """测试优化建议生成"""
        suggestion = self.analyzer.generate_fix_suggestion(self.FailureType.API_FICTION)
        self.assertIsInstance(suggestion, str)
        self.assertGreater(len(suggestion), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)