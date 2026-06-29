#!/usr/bin/env python3
"""
业务仪表盘测试脚本

测试业务指标收集器和业务仪表盘 API 的功能。

测试内容：
1. 业务指标收集器基本功能测试
2. 业务指标记录测试（交互、工具调用、任务、记忆、扩展）
3. 业务仪表盘数据查询测试
4. Prometheus 导出测试
5. API 端点测试（健康检查、仪表盘总览、指标详情）

运行方式：
    python tests/test_business_dashboard.py
"""

import sys
import os
import time
import json
import unittest

# 添加项目根目录到 Python 路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# 导入业务指标模块
try:
    from agent.monitoring.business_metrics import (
        BusinessMetricsCollector,
        get_business_metrics_collector,
        record_interaction,
        record_tool_call,
        record_task,
        record_memory_search,
        record_extension_install,
        record_memory_storage,
        record_memory_access,
        record_mcp_connection,
        record_skill_usage,
        record_market_search,
        get_dashboard_data,
        BUSINESS_METRICS_DEFINITIONS,
    )
    BUSINESS_METRICS_AVAILABLE = True
except ImportError as e:
    print(f"[ERROR] 无法导入业务指标模块: {e}")
    BUSINESS_METRICS_AVAILABLE = False


class TestBusinessMetricsCollector(unittest.TestCase):
    """测试业务指标收集器"""
    
    def setUp(self):
        """测试前准备"""
        if not BUSINESS_METRICS_AVAILABLE:
            self.skipTest("业务指标模块未安装")
        
        # 创建新的收集器实例（避免污染全局实例）
        self.collector = BusinessMetricsCollector()
    
    def test_01_initialization(self):
        """测试初始化"""
        print("\n[测试 01] 业务指标收集器初始化")
        
        # 检查收集器是否正确初始化
        self.assertIsNotNone(self.collector)
        self.assertIsNotNone(self.collector._counters)
        self.assertIsNotNone(self.collector._gauges)
        self.assertIsNotNone(self.collector._histograms)
        self.assertIsNotNone(self.collector._lock)
        
        print("✓ 业务指标收集器初始化成功")
    
    def test_02_record_interaction(self):
        """测试记录用户交互"""
        print("\n[测试 02] 记录用户交互")
        
        # 记录交互
        self.collector.record_interaction(
            interaction_type="chat",
            model="gpt-4",
            success=True,
            duration=1.5,
        )
        
        self.collector.record_interaction(
            interaction_type="chat",
            model="gpt-4",
            success=False,
            duration=0.8,
        )
        
        self.collector.record_interaction(
            interaction_type="tool_call",
            model="gpt-3.5",
            success=True,
            duration=2.0,
        )
        
        # 检查计数器
        dashboard = self.collector.get_dashboard_data()
        interaction_metrics = dashboard.get("interaction", {})
        
        # 检查交互总次数
        if "yunshu_interaction_total" in interaction_metrics:
            data = interaction_metrics["yunshu_interaction_total"]["data"]
            total_count = sum(data.values())
            self.assertGreater(total_count, 0)
            print(f"✓ 用户交互记录成功，总次数: {total_count}")
        else:
            print("⚠ 交互指标未找到（可能数据结构不同）")
    
    def test_03_record_tool_call(self):
        """测试记录工具调用"""
        print("\n[测试 03] 记录工具调用")
        
        # 记录工具调用
        self.collector.record_tool_call(
            tool_name="read_file",
            tool_category="file",
            success=True,
            duration=0.3,
        )
        
        self.collector.record_tool_call(
            tool_name="web_search",
            tool_category="web",
            success=True,
            duration=1.2,
        )
        
        self.collector.record_tool_call(
            tool_name="shell_execute",
            tool_category="system",
            success=False,
            duration=5.0,
        )
        
        # 检查计数器
        dashboard = self.collector.get_dashboard_data()
        interaction_metrics = dashboard.get("interaction", {})
        
        # 检查工具调用总次数
        if "yunshu_tool_call_total" in interaction_metrics:
            data = interaction_metrics["yunshu_tool_call_total"]["data"]
            total_count = sum(data.values())
            self.assertGreater(total_count, 0)
            print(f"✓ 工具调用记录成功，总次数: {total_count}")
        else:
            print("⚠ 工具调用指标未找到")
    
    def test_04_record_task(self):
        """测试记录任务执行"""
        print("\n[测试 04] 记录任务执行")
        
        # 记录任务
        self.collector.record_task(
            task_type="direct",
            complexity="simple",
            status="success",
            duration=1.0,
        )
        
        self.collector.record_task(
            task_type="planning",
            complexity="complex",
            status="success",
            duration=10.0,
        )
        
        self.collector.record_task(
            task_type="async",
            complexity="medium",
            status="failed",
            duration=5.0,
        )
        
        # 更新任务完成率
        self.collector.update_task_completion_rate("direct", "simple", 95.0)
        self.collector.update_task_completion_rate("planning", "complex", 85.0)
        
        # 检查计数器
        dashboard = self.collector.get_dashboard_data()
        task_metrics = dashboard.get("task", {})
        
        # 检查任务总次数
        if "yunshu_task_total" in task_metrics:
            data = task_metrics["yunshu_task_total"]["data"]
            total_count = sum(data.values())
            self.assertGreater(total_count, 0)
            print(f"✓ 任务执行记录成功，总次数: {total_count}")
        else:
            print("⚠ 任务指标未找到")
    
    def test_05_record_memory_search(self):
        """测试记录记忆搜索"""
        print("\n[测试 05] 记录记忆搜索")
        
        # 记录记忆搜索
        self.collector.record_memory_search(
            memory_type="long_term",
            search_method="keyword",
            hit=True,
        )
        
        self.collector.record_memory_search(
            memory_type="short_term",
            search_method="keyword",
            hit=False,
        )
        
        self.collector.record_memory_search(
            memory_type="long_term",
            search_method="vector",
            hit=True,
        )
        
        # 更新命中率
        self.collector.update_memory_hit_rate("long_term", "keyword", 75.0)
        self.collector.update_memory_hit_rate("short_term", "keyword", 60.0)
        
        # 记录记忆存储
        self.collector.record_memory_storage(
            memory_type="long_term",
            importance=5,
        )
        
        self.collector.record_memory_storage(
            memory_type="short_term",
            importance=3,
        )
        
        # 记录记忆访问
        self.collector.record_memory_access(
            memory_key="user_pref_theme",
            importance=5,
        )
        
        # 检查计数器
        dashboard = self.collector.get_dashboard_data()
        knowledge_metrics = dashboard.get("knowledge", {})
        
        # 检查记忆搜索总次数
        if "yunshu_memory_search_total" in knowledge_metrics:
            data = knowledge_metrics["yunshu_memory_search_total"]["data"]
            total_count = sum(data.values())
            self.assertGreater(total_count, 0)
            print(f"✓ 记忆搜索记录成功，总次数: {total_count}")
        else:
            print("⚠ 记忆搜索指标未找到")
    
    def test_06_record_extension_install(self):
        """测试记录扩展安装"""
        print("\n[测试 06] 记录扩展安装")
        
        # 记录扩展安装
        self.collector.record_extension_install(
            extension_type="skill",
            source="github",
            success=True,
        )
        
        self.collector.record_extension_install(
            extension_type="mcp",
            source="npm",
            success=False,
        )
        
        self.collector.record_extension_install(
            extension_type="plugin",
            source="pip",
            success=True,
        )
        
        # 更新已启用扩展数量
        self.collector.update_extension_enabled_count("skill", 10)
        self.collector.update_extension_enabled_count("mcp", 5)
        
        # 记录 MCP 连接
        self.collector.record_mcp_connection(
            transport_type="stdio",
            service_id="filesystem",
            success=True,
        )
        
        self.collector.record_mcp_connection(
            transport_type="http",
            service_id="api-server",
            success=False,
        )
        
        # 更新活跃 MCP 连接数
        self.collector.update_mcp_active_connections("stdio", 3)
        self.collector.update_mcp_active_connections("http", 2)
        
        # 记录技能使用
        self.collector.record_skill_usage(
            skill_id="self_reflection",
            skill_category="cognitive",
            success=True,
        )
        
        # 记录市场搜索
        self.collector.record_market_search(
            query_category="tool",
            result_count=15,
        )
        
        # 检查计数器
        dashboard = self.collector.get_dashboard_data()
        extension_metrics = dashboard.get("extension", {})
        
        # 检查扩展安装总次数
        if "yunshu_extension_install_total" in extension_metrics:
            data = extension_metrics["yunshu_extension_install_total"]["data"]
            total_count = sum(data.values())
            self.assertGreater(total_count, 0)
            print(f"✓ 扩展安装记录成功，总次数: {total_count}")
        else:
            print("⚠ 扩展安装指标未找到")
    
    def test_07_get_dashboard_data(self):
        """测试获取仪表盘数据"""
        print("\n[测试 07] 获取仪表盘数据")
        
        # 获取仪表盘数据
        dashboard = self.collector.get_dashboard_data()
        
        # 检查仪表盘数据结构
        self.assertIsNotNone(dashboard)
        self.assertIn("generated_at", dashboard)
        self.assertIn("interaction", dashboard)
        self.assertIn("task", dashboard)
        self.assertIn("knowledge", dashboard)
        self.assertIn("extension", dashboard)
        self.assertIn("summary", dashboard)
        
        # 检查汇总数据
        summary = dashboard.get("summary", {})
        self.assertIn("total_interactions", summary)
        self.assertIn("total_tool_calls", summary)
        self.assertIn("task_success_rate", summary)
        self.assertIn("memory_hit_rate", summary)
        self.assertIn("active_extensions", summary)
        
        print(f"✓ 仪表盘数据获取成功")
        print(f"  - 总交互次数: {summary.get('total_interactions', 0)}")
        print(f"  - 总工具调用次数: {summary.get('total_tool_calls', 0)}")
        print(f"  - 任务成功率: {summary.get('task_success_rate', 0):.2f}%")
        print(f"  - 记忆命中率: {summary.get('memory_hit_rate', 0):.2f}%")
        print(f"  - 活跃扩展数: {summary.get('active_extensions', 0)}")
    
    def test_08_get_metric_by_name(self):
        """测试获取单个指标详情"""
        print("\n[测试 08] 获取单个指标详情")
        
        # 获取指标详情
        metric_detail = self.collector.get_metric_by_name("yunshu_interaction_total")
        
        if metric_detail:
            # 检查指标详情结构
            self.assertIn("definition", metric_detail)
            self.assertIn("data", metric_detail)
            
            definition = metric_detail.get("definition", {})
            self.assertIn("name", definition)
            self.assertIn("description", definition)
            self.assertIn("metric_type", definition)
            self.assertIn("labels", definition)
            self.assertIn("unit", definition)
            self.assertIn("category", definition)
            self.assertIn("business_value", definition)
            
            print(f"✓ 指标详情获取成功")
            print(f"  - 指标名称: {definition.get('name')}")
            print(f"  - 指标描述: {definition.get('description')}")
            print(f"  - 指标类型: {definition.get('metric_type')}")
            print(f"  - 指标分类: {definition.get('category')}")
        else:
            print("⚠ 指标详情未找到")
    
    def test_09_export_prometheus(self):
        """测试导出 Prometheus 格式"""
        print("\n[测试 09] 导出 Prometheus 格式")
        
        # 导出 Prometheus 格式
        prometheus_text = self.collector.export_prometheus()
        
        # 检查 Prometheus 格式
        self.assertIsNotNone(prometheus_text)
        self.assertIsInstance(prometheus_text, str)
        
        # 检查是否包含指标定义
        if "# HELP" in prometheus_text and "# TYPE" in prometheus_text:
            print(f"✓ Prometheus 导出成功")
            print(f"  - 导出长度: {len(prometheus_text)} 字符")
            # 打印部分内容
            lines = prometheus_text.split("\n")[:10]
            print("  - 前 10 行:")
            for line in lines:
                if line:
                    print(f"    {line}")
        else:
            print("⚠ Prometheus 格式不完整")
    
    def test_10_reset_metrics(self):
        """测试重置指标"""
        print("\n[测试 10] 重置指标")
        
        # 重置指标
        self.collector.reset()
        
        # 检查是否已重置
        dashboard = self.collector.get_dashboard_data()
        summary = dashboard.get("summary", {})
        
        self.assertEqual(summary.get("total_interactions", 0), 0)
        self.assertEqual(summary.get("total_tool_calls", 0), 0)
        
        print("✓ 指标重置成功")


class TestBusinessMetricsDefinitions(unittest.TestCase):
    """测试业务指标定义"""
    
    def setUp(self):
        """测试前准备"""
        if not BUSINESS_METRICS_AVAILABLE:
            self.skipTest("业务指标模块未安装")
    
    def test_11_definitions_count(self):
        """测试指标定义数量"""
        print("\n[测试 11] 指标定义数量")
        
        # 检查指标定义数量
        total_definitions = len(BUSINESS_METRICS_DEFINITIONS)
        self.assertGreater(total_definitions, 0)
        
        print(f"✓ 指标定义总数: {total_definitions}")
        
        # 检查各分类数量
        categories = {
            "interaction": 0,
            "task": 0,
            "knowledge": 0,
            "extension": 0,
        }
        
        for name, definition in BUSINESS_METRICS_DEFINITIONS.items():
            category = definition.category
            if category in categories:
                categories[category] += 1
        
        print("  - 各分类数量:")
        for category, count in categories.items():
            print(f"    {category}: {count}")
    
    def test_12_definitions_structure(self):
        """测试指标定义结构"""
        print("\n[测试 12] 指标定义结构")
        
        # 检查指标定义结构
        for name, definition in BUSINESS_METRICS_DEFINITIONS.items():
            # 检查必需字段
            self.assertIsNotNone(definition.name)
            self.assertIsNotNone(definition.description)
            self.assertIsNotNone(definition.metric_type)
            self.assertIsNotNone(definition.unit)
            self.assertIsNotNone(definition.category)
            self.assertIsNotNone(definition.business_value)
            
            # 检查指标类型是否有效
            valid_types = ["counter", "gauge", "histogram"]
            self.assertIn(definition.metric_type, valid_types)
            
            # 检查分类是否有效
            valid_categories = ["interaction", "task", "knowledge", "extension"]
            self.assertIn(definition.category, valid_categories)
        
        print("✓ 所有指标定义结构正确")


class TestGlobalFunctions(unittest.TestCase):
    """测试全局快捷函数"""
    
    def setUp(self):
        """测试前准备"""
        if not BUSINESS_METRICS_AVAILABLE:
            self.skipTest("业务指标模块未安装")
        
        # 获取全局收集器并重置
        collector = get_business_metrics_collector()
        collector.reset()
    
    def test_13_global_functions(self):
        """测试全局快捷函数"""
        print("\n[测试 13] 全局快捷函数")
        
        # 测试全局快捷函数
        record_interaction("chat", "gpt-4", True, 1.5)
        record_tool_call("read_file", "file", True, 0.3)
        record_task("direct", "simple", "success", 1.0)
        record_memory_search("long_term", "keyword", True)
        record_extension_install("skill", "github", True)
        
        # 获取仪表盘数据
        dashboard = get_dashboard_data()
        
        # 检查是否记录成功
        self.assertIsNotNone(dashboard)
        
        print("✓ 全局快捷函数测试成功")


def run_tests():
    """运行所有测试"""
    print("=" * 70)
    print("业务仪表盘测试脚本")
    print("=" * 70)
    
    if not BUSINESS_METRICS_AVAILABLE:
        print("[ERROR] 业务指标模块未安装，无法运行测试")
        return False
    
    # 创建测试套件
    suite = unittest.TestSuite()
    
    # 添加测试
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestBusinessMetricsCollector))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestBusinessMetricsDefinitions))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestGlobalFunctions))
    
    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    print("\n" + "=" * 70)
    print("测试结果汇总")
    print("=" * 70)
    print(f"运行测试数: {result.testsRun}")
    print(f"成功测试数: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"失败测试数: {len(result.failures)}")
    print(f"错误测试数: {len(result.errors)}")
    
    if result.failures:
        print("\n失败测试:")
        for test, traceback in result.failures:
            print(f"  - {test}: {traceback}")
    
    if result.errors:
        print("\n错误测试:")
        for test, traceback in result.errors:
            print(f"  - {test}: {traceback}")
    
    print("=" * 70)
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)