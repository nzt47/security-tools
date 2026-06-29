#!/usr/bin/env python3
"""
追踪上下文调试测试脚本

用于验证 [Tracing] 日志是否能正常捕获上下文丢失场景
支持调试级别日志输出
包含 OpenTelemetry 采样器配置检查
"""

import os
import sys
import logging
import json
import threading
import time

# 设置调试级别日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# 加载追踪模块
from agent.monitoring.tracing import (
    TraceContext, 
    get_trace_id, 
    get_span_id, 
    set_trace_id, 
    set_span_id,
    extract_trace_context, 
    inject_trace_context,
    trace, 
    async_trace,
    is_opentelemetry_available,
    _init_opentelemetry,
    _tracer,
    diagnose_opentelemetry_config,
    print_diagnosis_report
)

def test_context_loss_scenario():
    """测试上下文丢失场景"""
    logger.info("\n" + "="*80)
    logger.info("🎯 测试场景：上下文丢失检测")
    logger.info("="*80)
    
    # 场景1：在 TraceContext 外部获取上下文
    logger.info("\n--- 场景1：TraceContext 外部获取上下文 ---")
    logger.info(f"[测试] 在上下文外部调用 get_trace_id(): {get_trace_id()}")
    logger.info(f"[测试] 在上下文外部调用 get_span_id(): {get_span_id()}")
    
    # 场景2：正常上下文流程
    logger.info("\n--- 场景2：正常上下文流程 ---")
    with TraceContext("TestService", "normal_operation") as ctx:
        logger.info(f"[测试] 在上下文内部 - trace_id: {ctx.trace_id}, span_id: {ctx.span_id}")
        logger.info(f"[测试] 通过 get_trace_id() 获取: {get_trace_id()}")
        logger.info(f"[测试] 通过 get_span_id() 获取: {get_span_id()}")
    
    # 场景3：上下文退出后获取
    logger.info("\n--- 场景3：上下文退出后获取 ---")
    logger.info(f"[测试] 上下文退出后调用 get_trace_id(): {get_trace_id()}")
    logger.info(f"[测试] 上下文退出后调用 get_span_id(): {get_span_id()}")
    
    # 场景4：手动设置上下文（使用符合W3C规范的十六进制ID）
    logger.info("\n--- 场景4：手动设置上下文 ---")
    set_trace_id("abc123def4567890abc123def4567890")
    set_span_id("1234567812345678")
    logger.info(f"[测试] 手动设置后 - trace_id: {get_trace_id()}, span_id: {get_span_id()}")
    
    # 场景5：新上下文覆盖手动设置
    logger.info("\n--- 场景5：新上下文覆盖手动设置 ---")
    with TraceContext("AnotherService", "override_operation") as ctx:
        logger.info(f"[测试] 新上下文内 - trace_id: {ctx.trace_id}, span_id: {ctx.span_id}")
    
    # 场景6：多线程上下文隔离
    logger.info("\n--- 场景6：多线程上下文隔离测试 ---")
    thread_results = []
    
    def thread_worker(thread_id):
        logger.info(f"[线程{thread_id}] 启动")
        with TraceContext(f"ThreadService", f"thread_{thread_id}_op") as ctx:
            time.sleep(0.1)
            result = {
                "thread_id": thread_id,
                "trace_id": ctx.trace_id,
                "span_id": ctx.span_id,
                "get_trace_id": get_trace_id(),
                "get_span_id": get_span_id()
            }
            thread_results.append(result)
            logger.info(f"[线程{thread_id}] 完成 - {result}")
    
    threads = []
    for i in range(3):
        t = threading.Thread(target=thread_worker, args=(i,))
        threads.append(t)
        t.start()
    
    for t in threads:
        t.join()
    
    logger.info(f"\n[测试] 多线程结果: {json.dumps(thread_results, indent=2)}")

def test_extract_inject_context():
    """测试上下文提取和注入"""
    logger.info("\n" + "="*80)
    logger.info("🎯 测试场景：上下文提取和注入")
    logger.info("="*80)
    
    # 测试 W3C Trace Context 格式
    logger.info("\n--- 测试 W3C Trace Context 格式 ---")
    headers_w3c = {'traceparent': '00-abc123def4567890abc123def4567890-1234567812345678-01'}
    extracted = extract_trace_context(headers_w3c)
    logger.info(f"[测试] W3C 格式提取结果: {extracted}")
    
    # 测试 Jaeger 格式
    logger.info("\n--- 测试 Jaeger 格式 ---")
    headers_jaeger = {'uber-trace-id': 'abc123def4567890:12345678:0:1'}
    extracted = extract_trace_context(headers_jaeger)
    logger.info(f"[测试] Jaeger 格式提取结果: {extracted}")
    
    # 测试空 headers
    logger.info("\n--- 测试空 headers ---")
    headers_empty = {}
    extracted = extract_trace_context(headers_empty)
    logger.info(f"[测试] 空 headers 提取结果: {extracted}")
    
    # 测试注入功能
    logger.info("\n--- 测试注入功能 ---")
    injected = inject_trace_context()
    logger.info(f"[测试] 注入结果: {injected}")
    
    # 测试在有上下文时的注入
    logger.info("\n--- 测试有上下文时的注入 ---")
    with TraceContext("InjectTest", "test"):
        injected_with_ctx = inject_trace_context()
        logger.info(f"[测试] 有上下文时注入结果: {injected_with_ctx}")

def test_decorators():
    """测试装饰器"""
    logger.info("\n" + "="*80)
    logger.info("🎯 测试场景：追踪装饰器")
    logger.info("="*80)
    
    @trace("DecoratorTest", "sync_function")
    def sync_test_function():
        logger.info("[测试] 同步函数执行中")
        return "sync_result"
    
    result = sync_test_function()
    logger.info(f"[测试] 同步函数返回: {result}")

def check_opentelemetry_config():
    """检查 OpenTelemetry 配置"""
    logger.info("\n" + "="*80)
    logger.info("🎯 OpenTelemetry 配置检查")
    logger.info("="*80)
    
    logger.info(f"[检查] OpenTelemetry 可用: {is_opentelemetry_available()}")
    logger.info(f"[检查] Tracer 实例: {_tracer}")
    
    if _tracer:
        try:
            # 检查 TracerProvider 和采样器
            from opentelemetry import trace as ot_trace
            provider = ot_trace.get_tracer_provider()
            
            logger.info(f"[检查] TracerProvider: {provider}")
            logger.info(f"[检查] TracerProvider 类型: {type(provider).__name__}")
            
            # 检查采样器配置
            if hasattr(provider, 'get_sampler'):
                sampler = provider.get_sampler()
                logger.info(f"[检查] 采样器: {sampler}")
                logger.info(f"[检查] 采样器类型: {type(sampler).__name__}")
            else:
                logger.info(f"[检查] 采样器: 默认（未显式配置）")
            
            # 检查 Span Processors
            if hasattr(provider, '_span_processors'):
                processors = provider._span_processors
                logger.info(f"[检查] Span Processors 数量: {len(processors)}")
                for i, processor in enumerate(processors):
                    logger.info(f"[检查]   Processor {i}: {type(processor).__name__}")
            
            logger.info("[检查] ✅ OpenTelemetry 配置检查完成")
            
        except Exception as e:
            logger.error(f"[检查] ❌ OpenTelemetry 配置检查失败: {e}", exc_info=True)

def test_context_propagation():
    """测试上下文传播"""
    logger.info("\n" + "="*80)
    logger.info("🎯 测试场景：上下文传播")
    logger.info("="*80)
    
    # 模拟跨服务调用
    logger.info("\n--- 模拟跨服务调用 ---")
    
    # 服务A创建上下文（使用符合W3C规范的trace_id）
    set_trace_id("abc123def4567890abc123def4567890")
    with TraceContext("ServiceA", "operation") as ctx:
        logger.info(f"[服务A] 创建上下文 - trace_id: {ctx.trace_id}, span_id: {ctx.span_id}")
        
        # 注入到请求头
        headers = inject_trace_context()
        logger.info(f"[服务A] 注入请求头: {headers}")
        
        # 模拟网络传输到服务B
        logger.info("[服务A] 发送请求到服务B...")
        
        # 服务B提取上下文
        logger.info("[服务B] 接收请求")
        extracted = extract_trace_context(headers)
        logger.info(f"[服务B] 提取上下文: {extracted}")
        
        # 服务B设置上下文并创建子Span
        if extracted.get('trace_id') and extracted.get('span_id'):
            set_trace_id(extracted['trace_id'])
            set_span_id(extracted['span_id'])
            logger.info(f"[服务B] 设置上下文 - trace_id: {get_trace_id()}, span_id: {get_span_id()}")
            
            with TraceContext("ServiceB", "sub_operation") as ctx_b:
                logger.info(f"[服务B] 子上下文 - trace_id: {ctx_b.trace_id}, span_id: {ctx_b.span_id}")
                logger.info(f"[服务B] 验证父trace_id是否保留: {ctx.trace_id == ctx_b.trace_id}")
        else:
            logger.warning("[服务B] 无法提取有效的追踪上下文")

def main():
    """主测试入口"""
    logger.info("\n" + "="*100)
    logger.info("🚀 追踪上下文调试测试启动")
    logger.info("="*100)
    
    # 初始化 OpenTelemetry（使用 AlwaysOnSampler）
    logger.info("[主程序] 初始化 OpenTelemetry...")
    _init_opentelemetry()
    
    # 打印详细诊断报告
    logger.info("[主程序] 生成 OpenTelemetry 配置诊断报告...")
    print_diagnosis_report()
    
    # 运行测试场景
    logger.info("[主程序] 开始运行测试场景...")
    test_context_loss_scenario()
    test_extract_inject_context()
    test_decorators()
    test_context_propagation()
    
    logger.info("\n" + "="*100)
    logger.info("✅ 追踪上下文调试测试完成")
    logger.info("="*100)

if __name__ == "__main__":
    main()