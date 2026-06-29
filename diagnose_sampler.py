#!/usr/bin/env python3
"""
OpenTelemetry 采样器诊断脚本

验证当前采样器是否确实配置为 AlwaysOnSampler
并检查完整的追踪链路配置
"""

import os
import sys
import logging

# 设置 DEBUG 级别日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

# 添加项目路径
sys.path.insert(0, '.')

def diagnose_sampler_config():
    """诊断采样器配置"""
    logger.info("\n" + "="*80)
    logger.info("🔍 OpenTelemetry 采样器配置诊断")
    logger.info("="*80)
    
    try:
        from agent.monitoring.tracing import (
            _OPENTELEMETRY_AVAILABLE,
            _tracer,
            _init_opentelemetry,
            diagnose_opentelemetry_config,
            ALWAYS_ON,
            TraceContext,
            get_trace_id,
            set_trace_id
        )
        
        # 初始化 OpenTelemetry
        logger.info("\n📦 初始化 OpenTelemetry...")
        _init_opentelemetry()
        
        # 获取诊断信息
        diagnosis = diagnose_opentelemetry_config()
        
        # 详细检查采样器
        logger.info("\n🎯 采样器配置详细检查:")
        
        if _OPENTELEMETRY_AVAILABLE:
            from opentelemetry import trace as ot_trace
            from opentelemetry.sdk.trace.sampling import ALWAYS_ON as SDK_ALWAYS_ON
            
            provider = ot_trace.get_tracer_provider()
            
            # 检查采样器
            if hasattr(provider, '_sampler'):
                sampler = provider._sampler
                sampler_type = type(sampler).__name__
                
                logger.info(f"   • 当前采样器实例: {sampler}")
                logger.info(f"   • 采样器类型: {sampler_type}")
                logger.info(f"   • 是否为 ALWAYS_ON: {sampler is SDK_ALWAYS_ON}")
                
                # 验证采样决策
                logger.info("\n🔬 测试采样决策:")
                from opentelemetry.sdk.trace.sampling import SamplingResult, Decision
                
                # 创建测试 Trace ID
                test_trace_id = "abc123def4567890abc123def4567890"
                set_trace_id(test_trace_id)
                
                # 模拟采样决策
                try:
                    # 创建一个模拟的采样上下文
                    from opentelemetry.trace import SpanContext, TraceFlags
                    from opentelemetry.sdk.trace.sampling import Context
                    
                    test_span_context = SpanContext(
                        trace_id=int(test_trace_id, 16),
                        span_id=1234567890123456,
                        trace_flags=TraceFlags(0x01),
                        trace_state=None,
                        is_remote=False
                    )
                    
                    # 调用采样器的 should_sample 方法
                    sampling_result = sampler.should_sample(
                        parent_context=Context(),
                        trace_id=int(test_trace_id, 16),
                        name="test-span",
                        span_kind=None,
                        attributes={},
                        links=[],
                        trace_state=None
                    )
                    
                    logger.info(f"   • 采样结果: {sampling_result}")
                    logger.info(f"   • 采样决策: {sampling_result.decision}")
                    logger.info(f"   • 决策值: {sampling_result.decision.value}")
                    
                    if sampling_result.decision == Decision.RECORD_AND_SAMPLE:
                        logger.info("   ✅ 采样决策: RECORD_AND_SAMPLE (记录并采样)")
                    elif sampling_result.decision == Decision.RECORD_ONLY:
                        logger.info("   ⚠️ 采样决策: RECORD_ONLY (仅记录)")
                    elif sampling_result.decision == Decision.DROP:
                        logger.info("   ❌ 采样决策: DROP (丢弃)")
                        
                except Exception as e:
                    logger.warning(f"   ⚠️ 采样决策测试失败: {e}")
                
                # 验证实际 Span 创建
                logger.info("\n🔷 验证实际 Span 创建:")
                with TraceContext("Diagnosis", "sampler_test") as ctx:
                    logger.info(f"   • 创建 Span 成功: trace_id={ctx.trace_id}, span_id={ctx.span_id}")
                    logger.info(f"   • OTel Span 已创建: {ctx._otel_span is not None}")
                    
                    if ctx._otel_span:
                        span_context = ctx._otel_span.get_span_context()
                        logger.info(f"   • SpanContext: trace_id=0x{span_context.trace_id:x}, span_id=0x{span_context.span_id:x}")
                        logger.info(f"   • Trace flags: 0x{span_context.trace_flags:02x}")
                        logger.info(f"   • Is sampled: {bool(span_context.trace_flags & 0x01)}")
        
        # 输出诊断报告
        logger.info("\n📊 诊断报告总结:")
        logger.info("-" * 60)
        
        if diagnosis['opentelemetry_available']:
            logger.info("✅ OpenTelemetry 可用")
        else:
            logger.error("❌ OpenTelemetry 不可用")
            
        if diagnosis['tracer_initialized']:
            logger.info("✅ Tracer 已初始化")
        else:
            logger.error("❌ Tracer 未初始化")
            
        sampler_type = diagnosis['sampler_info'].get('type', '未知')
        logger.info(f"📈 当前采样器: {sampler_type}")
        
        if 'AlwaysOnSampler' in sampler_type or 'ALWAYS_ON' in str(diagnosis['sampler_info']):
            logger.info("✅ 采样器配置正确: AlwaysOnSampler")
        else:
            logger.warning(f"⚠️ 采样器不是 AlwaysOnSampler，当前为: {sampler_type}")
            
        logger.info("\n💡 结论:")
        if diagnosis['opentelemetry_available'] and 'AlwaysOnSampler' in sampler_type:
            logger.info("✅ 所有检查通过！采样器已正确配置为 AlwaysOnSampler")
            logger.info("   所有 Span 都会被记录，适合调试场景")
        else:
            logger.warning("⚠️ 配置可能需要调整")
        
        return diagnosis
        
    except Exception as e:
        logger.error(f"❌ 诊断过程出错: {e}", exc_info=True)
        return None

def test_span_creation():
    """测试 Span 创建过程（DEBUG 级别详细日志）"""
    logger.info("\n" + "="*80)
    logger.info("🔧 Span 创建过程测试 (DEBUG 级别)")
    logger.info("="*80)
    
    from agent.monitoring.tracing import TraceContext, set_trace_id
    
    # 设置一个已知的 trace_id 以便追踪
    test_trace_id = "abc123def4567890abc123def4567890"
    logger.debug(f"[测试] 设置 trace_id: {test_trace_id}")
    set_trace_id(test_trace_id)
    
    logger.debug("[测试] 创建 TraceContext...")
    with TraceContext("TestService", "test_operation") as ctx:
        logger.debug(f"[测试] 进入 TraceContext: trace_id={ctx.trace_id}, span_id={ctx.span_id}")
        
        # 测试添加属性
        logger.debug("[测试] 添加属性...")
        ctx.set_attribute("test.key", "test.value")
        
        # 测试添加事件
        logger.debug("[测试] 添加事件...")
        ctx.add_event("test_event", {"detail": "test_data"})
        
        logger.debug("[测试] 执行业务逻辑...")
        # 模拟业务逻辑
        import time
        time.sleep(0.01)
        
        logger.debug(f"[测试] 准备退出 TraceContext")
    
    logger.debug("[测试] TraceContext 退出完成")

if __name__ == "__main__":
    logger.info("🚀 OpenTelemetry 采样器诊断脚本启动")
    
    # 运行采样器诊断
    diagnose_sampler_config()
    
    # 运行 Span 创建测试
    test_span_creation()
    
    logger.info("\n✅ 诊断完成")