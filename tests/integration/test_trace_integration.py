#!/usr/bin/env python3
"""
跨服务调用的端到端集成测试

模拟真实的微服务架构，验证追踪上下文在多个服务间正确传播：
1. API Gateway -> ServiceA -> ServiceB -> ServiceC
2. 异步任务队列中的上下文传播
3. 错误场景下的上下文保留
"""

import unittest
import asyncio
import logging
import sys
sys.path.insert(0, '.')

from agent.monitoring import (
    TraceContext,
    get_trace_id,
    set_trace_id,
    set_span_id,
    extract_trace_context,
    inject_trace_context,
    diagnose_opentelemetry_config,
    print_diagnosis_report,
    print_context_diagnosis,
    capture_context,
    restore_context,
    run_with_context
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MockService:
    """模拟微服务"""
    
    def __init__(self, name):
        self.name = name
    
    def process(self, headers=None):
        """处理请求"""
        if headers:
            # 提取追踪上下文
            context = extract_trace_context(headers)
            if context:
                set_trace_id(context.get('trace_id'))
                set_span_id(context.get('span_id'))
        
        with TraceContext(self.name, "process") as ctx:
            logger.info(f"[{ctx.trace_id}] {self.name} 处理请求")
            # 模拟处理时间
            import time
            time.sleep(0.01)
            
            # 返回新的追踪上下文
            return inject_trace_context()


class MockAsyncService:
    """模拟异步微服务"""
    
    def __init__(self, name):
        self.name = name
    
    async def process(self, headers=None):
        """异步处理请求"""
        if headers:
            context = extract_trace_context(headers)
            if context:
                set_trace_id(context.get('trace_id'))
                set_span_id(context.get('span_id'))
        
        with TraceContext(self.name, "async_process") as ctx:
            logger.info(f"[{ctx.trace_id}] {self.name} 异步处理请求")
            await asyncio.sleep(0.01)
            return inject_trace_context()


class TestCrossServiceIntegration(unittest.TestCase):
    """跨服务集成测试"""
    
    def setUp(self):
        """测试前重置上下文"""
        set_trace_id(None)
        set_span_id(None)
    
    def tearDown(self):
        """测试后清理上下文"""
        set_trace_id(None)
        set_span_id(None)
    
    def test_chain_service_call(self):
        """测试链式服务调用 (API Gateway -> ServiceA -> ServiceB -> ServiceC)"""
        logger.info("\n=== 测试链式服务调用 ===")
        
        # API Gateway 入口
        with TraceContext("APIGateway", "request") as gateway_ctx:
            root_trace_id = gateway_ctx.trace_id
            logger.info(f"入口 Trace ID: {root_trace_id}")
            
            # 注入上下文到请求头
            headers = inject_trace_context()
            logger.info(f"请求头: {headers}")
            
            # ServiceA 处理
            service_a = MockService("ServiceA")
            headers = service_a.process(headers)
            logger.info(f"ServiceA 响应头: {headers}")
            
            # ServiceB 处理
            service_b = MockService("ServiceB")
            headers = service_b.process(headers)
            logger.info(f"ServiceB 响应头: {headers}")
            
            # ServiceC 处理
            service_c = MockService("ServiceC")
            headers = service_c.process(headers)
            logger.info(f"ServiceC 响应头: {headers}")
        
        # 验证所有服务共享同一个 trace_id
        extracted = extract_trace_context(headers)
        self.assertEqual(extracted['trace_id'], root_trace_id, 
                        "所有服务应该共享同一个 trace_id")
        
        logger.info(f"✅ 链式调用成功！所有服务共享 trace_id: {root_trace_id}")
    
    def test_parallel_service_calls(self):
        """测试并行服务调用（多个服务同时处理同一个请求）"""
        logger.info("\n=== 测试并行服务调用 ===")
        
        with TraceContext("APIGateway", "parallel_request") as gateway_ctx:
            root_trace_id = gateway_ctx.trace_id
            headers = inject_trace_context()
            
            # 多个服务并行处理
            services = [MockService(f"Service{i}") for i in range(1, 4)]
            responses = []
            
            for service in services:
                resp = service.process(headers)
                responses.append(resp)
            
            # 验证所有响应共享同一个 trace_id
            for i, resp in enumerate(responses):
                extracted = extract_trace_context(resp)
                self.assertEqual(extracted['trace_id'], root_trace_id,
                                f"Service{i+1} 应该继承 trace_id")
        
        logger.info(f"✅ 并行调用成功！所有服务共享 trace_id: {root_trace_id}")
    
    def test_context_isolation_between_requests(self):
        """测试不同请求之间的上下文隔离"""
        logger.info("\n=== 测试请求间上下文隔离 ===")
        
        trace_ids = []
        
        # 第一个请求
        with TraceContext("APIGateway", "request_1") as ctx1:
            trace_ids.append(ctx1.trace_id)
            headers = inject_trace_context()
            MockService("ServiceA").process(headers)
        
        # 确保上下文已清理
        self.assertIsNone(get_trace_id(), "请求结束后上下文应该为空")
        
        # 第二个请求
        with TraceContext("APIGateway", "request_2") as ctx2:
            trace_ids.append(ctx2.trace_id)
        
        # 验证两个请求有不同的 trace_id
        self.assertNotEqual(trace_ids[0], trace_ids[1],
                          "不同请求应该有不同的 trace_id")
        
        logger.info(f"✅ 上下文隔离成功！请求1: {trace_ids[0]}, 请求2: {trace_ids[1]}")
    
    def test_error_propagation(self):
        """测试错误场景下的上下文传播"""
        logger.info("\n=== 测试错误场景下的上下文传播 ===")
        
        with TraceContext("APIGateway", "error_request") as gateway_ctx:
            root_trace_id = gateway_ctx.trace_id
            headers = inject_trace_context()
            
            # 模拟服务出错
            service = MockService("FailingService")
            try:
                with TraceContext("FailingService", "process_error") as ctx:
                    headers = inject_trace_context()
                    raise ValueError("模拟服务错误")
            except ValueError as e:
                # 即使出错，trace_id 应该仍然可用
                self.assertEqual(get_trace_id(), root_trace_id,
                              "错误场景下 trace_id 应该保持不变")
        
        logger.info(f"✅ 错误传播成功！trace_id 保持: {root_trace_id}")


class TestAsyncIntegration(unittest.TestCase):
    """异步场景集成测试"""
    
    def setUp(self):
        set_trace_id(None)
        set_span_id(None)
    
    def tearDown(self):
        set_trace_id(None)
        set_span_id(None)
    
    def test_async_service_chain(self):
        """测试异步服务链"""
        logger.info("\n=== 测试异步服务链 ===")
        
        async def run_async_chain():
            with TraceContext("APIGateway", "async_request") as gateway_ctx:
                root_trace_id = gateway_ctx.trace_id
                headers = inject_trace_context()
                
                service_a = MockAsyncService("AsyncServiceA")
                headers = await service_a.process(headers)
                
                service_b = MockAsyncService("AsyncServiceB")
                headers = await service_b.process(headers)
            
            extracted = extract_trace_context(headers)
            return root_trace_id, extracted['trace_id']
        
        root_id, final_id = asyncio.run(run_async_chain())
        self.assertEqual(root_id, final_id, "异步链中 trace_id 应该保持一致")
        
        logger.info(f"✅ 异步服务链成功！trace_id: {root_id}")
    
    def test_context_preservation_in_callback(self):
        """测试回调中的上下文保留"""
        logger.info("\n=== 测试回调中的上下文保留 ===")
        
        captured_trace_id = None
        
        def callback():
            nonlocal captured_trace_id
            captured_trace_id = get_trace_id()
        
        with TraceContext("Main", "operation") as ctx:
            # 在上下文中调用回调
            run_with_context(capture_context(), callback)
        
        self.assertEqual(captured_trace_id, ctx.trace_id,
                        "回调应该能够访问原始上下文")
        
        logger.info(f"✅ 回调上下文保留成功！trace_id: {captured_trace_id}")


class TestDiagnosticsIntegration(unittest.TestCase):
    """诊断功能集成测试"""

    def setUp(self):
        """每个测试前初始化 OpenTelemetry SDK

        diagnose_opentelemetry_config() 通过检测 TracerProvider 是否为 Proxy 实现
        来判断 tracer_initialized。默认情况下 opentelemetry.trace.get_tracer_provider()
        返回 ProxyTracerProvider，需调用 init_observability() 创建真正的 TracerProvider。
        """
        from agent.monitoring.tracing import init_observability
        init_observability()

    def test_diagnosis_report(self):
        """测试诊断报告功能"""
        logger.info("\n=== 测试诊断报告 ===")

        # 初始化追踪
        with TraceContext("DiagnosticTest", "operation"):
            pass

        # 运行诊断
        diagnosis = diagnose_opentelemetry_config()

        self.assertTrue(diagnosis['opentelemetry_available'],
                      "OpenTelemetry 应该可用")
        self.assertTrue(diagnosis['tracer_initialized'],
                      "Tracer 应该已初始化")

        logger.info("✅ 诊断报告生成成功")
    
    def test_context_diagnosis(self):
        """测试上下文诊断功能"""
        logger.info("\n=== 测试上下文诊断 ===")
        
        with TraceContext("ContextTest", "operation"):
            print_context_diagnosis()
        
        logger.info("✅ 上下文诊断完成")


if __name__ == "__main__":
    # 先打印 OpenTelemetry 配置诊断
    print("\n" + "="*80)
    print("📊 集成测试前配置诊断")
    print("="*80)
    print_diagnosis_report()
    
    # 运行测试
    unittest.main(verbosity=2)