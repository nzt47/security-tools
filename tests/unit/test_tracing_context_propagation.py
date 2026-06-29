#!/usr/bin/env python3
"""
跨服务调用场景下追踪上下文传播的单元测试

测试场景：
1. 跨服务调用时上下文正确传播
2. 异步场景下上下文隔离
3. 多线程场景下上下文隔离
4. 边界条件测试（空上下文、无效上下文等）
"""

import unittest
import threading
import asyncio
import logging
import sys
sys.path.insert(0, '.')

from agent.monitoring.tracing import (
    TraceContext,
    get_trace_id,
    get_span_id,
    set_trace_id,
    set_span_id,
    extract_trace_context,
    inject_trace_context
)

# 设置日志级别
logging.basicConfig(level=logging.DEBUG)


class TestCrossServicePropagation(unittest.TestCase):
    """跨服务调用上下文传播测试"""
    
    def setUp(self):
        """测试前重置上下文"""
        set_trace_id(None)
        set_span_id(None)
    
    def tearDown(self):
        """测试后清理上下文"""
        set_trace_id(None)
        set_span_id(None)
    
    def test_cross_service_propagation(self):
        """测试跨服务调用时上下文正确传播"""
        # 服务A创建上下文
        with TraceContext("ServiceA", "operation") as ctx_a:
            trace_id_a = ctx_a.trace_id
            span_id_a = ctx_a.span_id
            
            # 注入到请求头
            headers = inject_trace_context()
            
            # 模拟网络传输到服务B
            # 服务B提取上下文
            extracted = extract_trace_context(headers)
            
            # 验证提取的上下文
            self.assertEqual(extracted['trace_id'], trace_id_a)
            self.assertEqual(extracted['span_id'], span_id_a)
            
            # 服务B设置上下文并创建子Span
            set_trace_id(extracted['trace_id'])
            set_span_id(extracted['span_id'])
            
            with TraceContext("ServiceB", "sub_operation") as ctx_b:
                # 验证trace_id保持不变（传播成功）
                self.assertEqual(ctx_b.trace_id, trace_id_a)
                # 验证span_id不同（创建了新的子Span）
                self.assertNotEqual(ctx_b.span_id, span_id_a)
    
    def test_chain_service_propagation(self):
        """测试链式服务调用（A -> B -> C）"""
        trace_id_root = "abc123def4567890abc123def4567890"
        set_trace_id(trace_id_root)
        
        spans = []
        
        # 服务A
        with TraceContext("ServiceA", "entry") as ctx_a:
            spans.append(("A", ctx_a.trace_id, ctx_a.span_id))
            headers_a = inject_trace_context()
            
            # 服务B
            extracted_b = extract_trace_context(headers_a)
            set_trace_id(extracted_b['trace_id'])
            set_span_id(extracted_b['span_id'])
            
            with TraceContext("ServiceB", "process") as ctx_b:
                spans.append(("B", ctx_b.trace_id, ctx_b.span_id))
                headers_b = inject_trace_context()
                
                # 服务C
                extracted_c = extract_trace_context(headers_b)
                set_trace_id(extracted_c['trace_id'])
                set_span_id(extracted_c['span_id'])
                
                with TraceContext("ServiceC", "exit") as ctx_c:
                    spans.append(("C", ctx_c.trace_id, ctx_c.span_id))
        
        # 验证所有服务共享同一个trace_id
        for service, trace_id, span_id in spans:
            self.assertEqual(trace_id, trace_id_root, 
                           f"Service {service} trace_id不匹配")
        
        # 验证每个服务有不同的span_id
        span_ids = [s[2] for s in spans]
        self.assertEqual(len(set(span_ids)), 3, "Span IDs 应该各不相同")
    
    def test_empty_headers_propagation(self):
        """测试空headers的处理"""
        headers = {}
        extracted = extract_trace_context(headers)
        
        # 空headers应该返回空字典
        self.assertEqual(extracted, {})
        
        # 基于空上下文创建新的TraceContext
        with TraceContext("Service", "test") as ctx:
            self.assertIsNotNone(ctx.trace_id)
            self.assertIsNotNone(ctx.span_id)
    
    def test_invalid_traceparent(self):
        """测试无效traceparent格式的处理"""
        test_cases = [
            {"traceparent": "invalid-format"},
            {"traceparent": "00-trace-id-only"},
            {"traceparent": "00-abc123-456-01"},  # 非十六进制字符
            {"traceparent": "01-abc123def4567890abc123def4567890-1234567812345678-01"},  # 版本不支持
        ]
        
        for headers in test_cases:
            extracted = extract_trace_context(headers)
            self.assertEqual(extracted, {}, f"无效格式 {headers} 应该返回空字典")
    
    def test_valid_traceparent_formats(self):
        """测试有效traceparent格式"""
        # W3C标准格式
        headers = {"traceparent": "00-abc123def4567890abc123def4567890-1234567812345678-01"}
        extracted = extract_trace_context(headers)
        self.assertEqual(extracted['trace_id'], "abc123def4567890abc123def4567890")
        self.assertEqual(extracted['span_id'], "1234567812345678")
        
        # Jaeger格式
        headers = {"uber-trace-id": "abc123def4567890:12345678:0:1"}
        extracted = extract_trace_context(headers)
        self.assertEqual(extracted['trace_id'], "abc123def4567890")
        self.assertEqual(extracted['span_id'], "12345678")


class TestContextIsolation(unittest.TestCase):
    """上下文隔离测试"""
    
    def setUp(self):
        set_trace_id(None)
        set_span_id(None)
    
    def tearDown(self):
        set_trace_id(None)
        set_span_id(None)
    
    def test_thread_isolation(self):
        """测试多线程场景下上下文隔离"""
        results = []
        
        def worker(thread_id):
            with TraceContext(f"Thread{thread_id}", "operation") as ctx:
                # 等待确保其他线程也在执行
                import time
                time.sleep(0.01)
                results.append({
                    "thread_id": thread_id,
                    "trace_id": ctx.trace_id,
                    "span_id": ctx.span_id,
                    "get_trace_id": get_trace_id()
                })
        
        threads = []
        for i in range(3):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # 验证每个线程有独立的trace_id
        trace_ids = [r['trace_id'] for r in results]
        self.assertEqual(len(set(trace_ids)), 3, "每个线程应该有独立的trace_id")
        
        # 验证get_trace_id在上下文中能正确获取
        for r in results:
            self.assertEqual(r['trace_id'], r['get_trace_id'])
    
    def test_context_nesting(self):
        """测试嵌套上下文"""
        with TraceContext("Outer", "operation") as outer:
            outer_trace = outer.trace_id
            outer_span = outer.span_id
            
            # 验证内部可以获取外部上下文
            self.assertEqual(get_trace_id(), outer_trace)
            self.assertEqual(get_span_id(), outer_span)
            
            # 创建嵌套上下文
            with TraceContext("Inner", "sub_operation") as inner:
                # 内部应该继承trace_id但有新的span_id
                self.assertEqual(inner.trace_id, outer_trace)
                self.assertNotEqual(inner.span_id, outer_span)
                
                # 内部获取的应该是内部的上下文
                self.assertEqual(get_trace_id(), outer_trace)
                self.assertEqual(get_span_id(), inner.span_id)
            
            # 退出内部后应该回到外部上下文
            self.assertEqual(get_trace_id(), outer_trace)
            self.assertEqual(get_span_id(), outer_span)
    
    def test_context_exit_cleanup(self):
        """测试上下文退出后清理"""
        # 设置初始上下文
        set_trace_id("initial-trace")
        set_span_id("initial-span")
        
        with TraceContext("Test", "operation") as ctx:
            pass
        
        # 退出后应该恢复到初始上下文
        self.assertEqual(get_trace_id(), "initial-trace")
        self.assertEqual(get_span_id(), "initial-span")


class TestBoundaryConditions(unittest.TestCase):
    """边界条件测试"""
    
    def setUp(self):
        set_trace_id(None)
        set_span_id(None)
    
    def tearDown(self):
        set_trace_id(None)
        set_span_id(None)
    
    def test_null_trace_id(self):
        """测试空trace_id处理"""
        result = get_trace_id()
        self.assertIsNone(result)
        
        # 即使没有trace_id也应该能创建上下文
        with TraceContext("Test", "operation") as ctx:
            self.assertIsNotNone(ctx.trace_id)
    
    def test_null_span_id(self):
        """测试空span_id处理"""
        result = get_span_id()
        self.assertIsNone(result)
        
        with TraceContext("Test", "operation") as ctx:
            self.assertIsNotNone(ctx.span_id)
    
    def test_case_insensitive_headers(self):
        """测试headers大小写不敏感"""
        headers_lower = {"traceparent": "00-abc123def4567890abc123def4567890-1234567812345678-01"}
        headers_upper = {"Traceparent": "00-abc123def4567890abc123def4567890-1234567812345678-01"}
        
        extracted_lower = extract_trace_context(headers_lower)
        extracted_upper = extract_trace_context(headers_upper)
        
        self.assertEqual(extracted_lower, extracted_upper)
    
    def test_trace_id_length(self):
        """测试不同长度的trace_id"""
        # 128位trace_id
        headers_128 = {"traceparent": "00-abc123def4567890abc123def4567890-1234567812345678-01"}
        extracted = extract_trace_context(headers_128)
        self.assertEqual(len(extracted['trace_id']), 32)
        
        # 64位trace_id（旧格式）
        headers_64 = {"traceparent": "00-abc123def4567890-1234567812345678-01"}
        extracted = extract_trace_context(headers_64)
        self.assertEqual(len(extracted['trace_id']), 16)
    
    def test_concurrent_context_creation(self):
        """测试并发创建上下文"""
        created_contexts = []
        
        def create_context(id):
            with TraceContext(f"Service{id}", "op") as ctx:
                import time
                time.sleep(0.005)
                created_contexts.append((id, ctx.trace_id))
        
        threads = []
        for i in range(5):
            t = threading.Thread(target=create_context, args=(i,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # 验证所有上下文都成功创建
        self.assertEqual(len(created_contexts), 5)
        
        # 验证trace_id唯一性
        trace_ids = [c[1] for c in created_contexts]
        self.assertEqual(len(set(trace_ids)), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)