#!/usr/bin/env python3
"""
追踪上下文测试脚本
测试 extract_trace_context 和 inject_trace_context 的完整链路
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.monitoring.tracing import (
    TraceContext,
    extract_trace_context,
    inject_trace_context,
    get_trace_id,
    get_span_id,
    set_trace_id,
    set_span_id,
    is_opentelemetry_available,
    trace
)

import json

def test_w3c_trace_context():
    """测试 W3C Trace Context 格式的提取和注入"""
    print("\n=== 测试 W3C Trace Context 格式 ===")
    
    # 模拟外部请求的 traceparent
    external_trace_id = "abc123def4567890abc123def4567890"
    external_span_id = "1234567812345678"
    headers = {
        "traceparent": f"00-{external_trace_id}-{external_span_id}-01",
        "Content-Type": "application/json"
    }
    
    print(f"原始请求头: {headers}")
    
    # 提取追踪上下文
    context = extract_trace_context(headers)
    print(f"提取的上下文: {context}")
    
    # 设置上下文
    if context.get('trace_id'):
        set_trace_id(context['trace_id'])
        set_span_id(context['span_id'])
    
    # 注入新的追踪上下文
    new_headers = inject_trace_context()
    print(f"注入的新请求头: {new_headers}")
    
    # 验证新的 traceparent 格式
    new_traceparent = new_headers['traceparent']
    parts = new_traceparent.split('-')
    assert len(parts) == 4, "traceparent 格式不正确"
    assert parts[0] == "00", "版本号不正确"
    assert parts[1] == external_trace_id, "Trace ID 应该保持一致"
    assert len(parts[2]) == 16, "Span ID 应该是16位"
    assert parts[3] == "01", "标志位应该是01"
    
    print("✓ W3C Trace Context 测试通过")

def test_jaeger_trace_context():
    """测试 Jaeger 格式的提取"""
    print("\n=== 测试 Jaeger 格式 ===")
    
    headers = {
        "uber-trace-id": "abc123def4567890:12345678:0:1",
        "Content-Type": "application/json"
    }
    
    print(f"原始请求头: {headers}")
    
    context = extract_trace_context(headers)
    print(f"提取的上下文: {context}")
    
    assert context['trace_id'] == "abc123def4567890", "Trace ID 提取错误"
    assert context['span_id'] == "12345678", "Span ID 提取错误"
    
    print("✓ Jaeger 格式测试通过")

def test_trace_context_manager():
    """测试 TraceContext 上下文管理器"""
    print("\n=== 测试 TraceContext 上下文管理器 ===")
    
    with TraceContext("TestService", "test_operation", "internal") as ctx:
        print(f"进入上下文 - trace_id: {ctx.trace_id}, span_id: {ctx.span_id}")
        print(f"get_trace_id(): {get_trace_id()}")
        print(f"get_span_id(): {get_span_id()}")
        
        ctx.add_event("test_event", {"key": "value"})
        ctx.set_attribute("custom_attr", "test_value")
        
        # 嵌套追踪
        with TraceContext("NestedService", "nested_operation") as nested_ctx:
            print(f"嵌套上下文 - trace_id: {nested_ctx.trace_id}, span_id: {nested_ctx.span_id}")
            print(f"嵌套中 get_trace_id(): {get_trace_id()}")
            print(f"嵌套中 get_span_id(): {get_span_id()}")
        
        print(f"退出嵌套后 - trace_id: {get_trace_id()}, span_id: {get_span_id()}")
    
    print(f"退出上下文后 - trace_id: {get_trace_id()}, span_id: {get_span_id()}")
    print("✓ TraceContext 上下文管理器测试通过")

def test_decorator():
    """测试追踪装饰器"""
    print("\n=== 测试 @trace 装饰器 ===")
    
    @trace("DecoratedService", "decorated_method")
    def test_method():
        print(f"方法内部 - trace_id: {get_trace_id()}")
        return "success"
    
    result = test_method()
    assert result == "success"
    print("✓ @trace 装饰器测试通过")

def test_empty_headers():
    """测试空请求头的情况"""
    print("\n=== 测试空请求头 ===")
    
    headers = {}
    context = extract_trace_context(headers)
    print(f"空请求头提取的上下文: {context}")
    assert context == {}, "空请求头应该返回空字典"
    
    new_headers = inject_trace_context()
    print(f"无上下文时注入的请求头: {new_headers}")
    assert 'traceparent' in new_headers, "应该生成新的 traceparent"
    
    print("✓ 空请求头测试通过")

def test_opentelemetry_availability():
    """测试 OpenTelemetry 可用性"""
    print("\n=== 测试 OpenTelemetry 可用性 ===")
    print(f"OpenTelemetry 可用: {is_opentelemetry_available()}")
    print("✓ OpenTelemetry 可用性测试通过")

if __name__ == "__main__":
    print("=" * 60)
    print("追踪上下文完整链路测试")
    print("=" * 60)
    
    test_opentelemetry_availability()
    test_w3c_trace_context()
    test_jaeger_trace_context()
    test_empty_headers()
    test_trace_context_manager()
    test_decorator()
    
    print("\n" + "=" * 60)
    print("所有测试通过！✓")
    print("=" * 60)