#!/usr/bin/env python3
"""
全链路回溯与版本对比功能演示脚本
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import tempfile
from datetime import datetime


def demo_trace_backtracking():
    """演示全链路回溯功能"""
    print("\n" + "="*60)
    print("🎯 全链路回溯功能演示")
    print("="*60)
    
    from agent.monitoring.tracing import TraceRecord, TraceStorage, get_trace_storage
    
    # 使用全局存储实例
    storage = get_trace_storage()
    
    # 构造模拟的 trace_id 和决策序列数据
    trace_id = "trace_20260624_demo_001"
    record = TraceRecord(trace_id=trace_id)
    
    # 添加模拟的执行链路
    record.add_span({
        "span_id": "span_init",
        "service": "agent.core",
        "operation": "initialize",
        "start_time": 1719216000.0,
        "end_time": 1719216000.5,
        "duration_ms": 500.0,
        "status": "success",
        "attributes": {"agent_id": "yunshu-001", "version": "3.0.0"},
        "events": [
            {
                "name": "decision",
                "timestamp": 1719216000.2,
                "attributes": {"decision": "开始处理用户请求", "confidence": 0.95}
            }
        ]
    })
    
    record.add_span({
        "span_id": "span_analyze",
        "parent_span_id": "span_init",
        "service": "agent.planner",
        "operation": "analyze_intent",
        "start_time": 1719216000.5,
        "end_time": 1719216001.2,
        "duration_ms": 700.0,
        "status": "success",
        "attributes": {"intent": "查询天气", "confidence": 0.92},
        "events": [
            {
                "name": "plan",
                "timestamp": 1719216000.8,
                "attributes": {"plan": "调用天气API获取实时天气"}
            }
        ]
    })
    
    record.add_span({
        "span_id": "span_tool",
        "parent_span_id": "span_analyze",
        "service": "agent.tool",
        "operation": "call_weather_api",
        "start_time": 1719216001.2,
        "end_time": 1719216002.5,
        "duration_ms": 1300.0,
        "status": "success",
        "attributes": {"api_endpoint": "https://api.weather.com/v3/forecast", "city": "北京"},
        "events": [
            {
                "name": "tool_call",
                "timestamp": 1719216001.5,
                "attributes": {"tool_name": "weather_api", "params": {"city": "北京"}}
            },
            {
                "name": "reflection",
                "timestamp": 1719216002.3,
                "attributes": {"reflection": "API调用成功，获取到天气数据"}
            }
        ]
    })
    
    record.add_span({
        "span_id": "span_response",
        "parent_span_id": "span_tool",
        "service": "agent.core",
        "operation": "generate_response",
        "start_time": 1719216002.5,
        "end_time": 1719216003.0,
        "duration_ms": 500.0,
        "status": "success",
        "attributes": {"response_length": 128},
        "events": [
            {
                "name": "decision",
                "timestamp": 1719216002.8,
                "attributes": {"decision": "生成最终响应", "confidence": 0.98}
            }
        ]
    })
    
    # 保存 Trace
    storage.save_trace(record)
    print(f"✅ 模拟 Trace 数据已保存: {trace_id}")
    print(f"📊 Span 数量: {len(record.spans)}")
    
    # 测试全链路回溯（使用存储实例直接查询）
    print("\n📋 决策序列回溯结果:")
    loaded_record = storage.load_trace(trace_id)
    if loaded_record:
        decisions = []
        for span in loaded_record.spans:
            events = span.get('events', [])
            for event in events:
                event_name = event.get('name', '')
                if event_name in ['decision', 'plan', 'action', 'reflection', 'tool_call']:
                    decisions.append({
                        "timestamp": event.get('timestamp', span.get('start_time', 0)),
                        "span_id": span.get('span_id'),
                        "service": span.get('service'),
                        "operation": span.get('operation'),
                        "decision_type": event_name,
                        "details": event.get('attributes', {}),
                        "duration_ms": span.get('duration_ms', 0)
                    })
        
        decisions.sort(key=lambda x: x['timestamp'])
        decision_seq = {
            "trace_id": trace_id,
            "decision_count": len(decisions),
            "decisions": decisions
        }
        print(json.dumps(decision_seq, ensure_ascii=False, indent=2))
    else:
        print(f"❌ 无法加载 Trace: {trace_id}")
    
    # 测试流程图数据构建
    print("\n📈 可视化流程图数据:")
    if loaded_record:
        nodes = []
        edges = []
        for span in loaded_record.spans:
            node = {
                "id": span.get('span_id'),
                "name": f"{span.get('service')}.{span.get('operation')}",
                "service": span.get('service'),
                "operation": span.get('operation'),
                "node_type": "operation"
            }
            nodes.append(node)
            
            parent_span_id = span.get('parent_span_id')
            if parent_span_id:
                edges.append({
                    "from": parent_span_id,
                    "to": span.get('span_id'),
                    "label": span.get('operation')
                })
        
        print(f"  - 节点数量: {len(nodes)}")
        print(f"  - 边数量: {len(edges)}")
        print(f"  - 总耗时: {loaded_record.get_total_duration():.2f}ms")
    else:
        print("  - 节点数量: 0")
        print("  - 边数量: 0")
        print("  - 总耗时: 0.00ms")
    
    return trace_id


def demo_failure_analysis_logging():
    """演示失败模式分类归档模块的日志记录"""
    print("\n" + "="*60)
    print("🔍 失败模式分类归档演示")
    print("="*60)
    
    from agent.cognitive.failure_analysis import (
        FailureAnalyzer, FailureRecord, FailureType, FailureSeverity
    )
    
    # 创建临时存储
    temp_dir = tempfile.mkdtemp()
    analyzer = FailureAnalyzer(storage_path=temp_dir)
    analyzer.initialize()
    
    # 模拟不同类型的失败案例
    failure_cases = [
        FailureRecord(
            trace_id="trace_failure_001",
            failure_type=FailureType.API_FICTION,
            severity=FailureSeverity.HIGH,
            message="调用不存在的API: get_user_info_v2",
            source="agent.tool",
            evidence=["日志显示尝试调用未定义的API", "API文档中不存在该接口"]
        ),
        FailureRecord(
            trace_id="trace_failure_002",
            failure_type=FailureType.FIELD_ERROR,
            severity=FailureSeverity.MEDIUM,
            message="字段类型错误: expected int, got str",
            source="agent.parser",
            evidence=["输入数据类型不匹配", "JSON解析失败"]
        ),
        FailureRecord(
            trace_id="trace_failure_003",
            failure_type=FailureType.FLOW_SKIP,
            severity=FailureSeverity.HIGH,
            message="跳过必要步骤: 未验证用户权限",
            source="agent.security",
            evidence=["安全检查被绕过", "权限验证步骤缺失"]
        ),
        FailureRecord(
            trace_id="trace_failure_004",
            failure_type=FailureType.DATA_INVENTION,
            severity=FailureSeverity.CRITICAL,
            message="虚构数据: 生成了不存在的用户信息",
            source="agent.generator",
            evidence=["数据与数据库不一致", "无来源证明"]
        )
    ]
    
    print("\n📝 记录失败案例（日志将显示详细信息）:")
    for i, case in enumerate(failure_cases, 1):
        analyzer.record_failure(case)
        print(f"  {i}. [{case.failure_type.value}] {case.message[:50]}...")
    
    # 查询失败案例
    print("\n🔎 查询失败案例:")
    failures = analyzer.query_failures(limit=10)
    print(f"   共查询到 {len(failures)} 条失败记录")
    
    # 获取失败汇总
    print("\n📊 失败汇总统计:")
    summary = analyzer.get_failure_summary(hours=24)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    
    # 清理临时目录
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)


def demo_version_comparison():
    """演示版本对比功能"""
    print("\n" + "="*60)
    print("📌 版本对比功能演示")
    print("="*60)
    
    from agent.prompt_manager.storage import PromptStorage, PromptType
    from agent.prompt_manager.version_control import VersionManager
    from agent.prompt_manager.registry import PromptRegistry
    
    # 创建临时存储
    temp_dir = tempfile.mkdtemp()
    
    # 初始化存储
    storage = PromptStorage(storage_path=temp_dir)
    storage.initialize()
    
    # 创建版本管理器和注册中心
    version_manager = VersionManager(storage=storage)
    registry = PromptRegistry(storage=storage)
    
    # 注册提示词
    prompt_id = "demo_prompt_001"
    registry.register_prompt(
        prompt_id=prompt_id,
        name="用户问候提示词",
        content="""你是一个友好的AI助手。
请用友好、热情的语气回应用户。
保持回答简洁。""",
        prompt_type=PromptType.SYSTEM,
        tags=["greeting", "friendly"]
    )
    print(f"✅ 注册提示词: {prompt_id}")
    
    # 创建第一个版本
    v1 = version_manager.create_version(
        prompt_id=prompt_id,
        change_log="初始版本 - 基础问候逻辑",
        author="admin"
    )
    print(f"📦 创建版本: {v1.version_number}")
    
    # 更新提示词内容
    registry.update_prompt(
        prompt_id=prompt_id,
        content="""你是一个专业且友好的AI助手。
请用礼貌、专业的语气回应用户。
保持回答简洁明了。
避免使用过于随意的语言。""",
        tags=["greeting", "professional", "friendly"]
    )
    
    # 创建第二个版本
    v2 = version_manager.create_version(
        prompt_id=prompt_id,
        change_log="优化版本 - 添加专业性要求，增强语气规范",
        author="admin"
    )
    print(f"📦 创建版本: {v2.version_number}")
    
    # 演示版本对比
    print("\n🔄 版本对比结果 (v1.0.0 vs v1.0.1):")
    diff = version_manager.compare_versions(prompt_id, "1.0.0", "1.0.1")
    
    print(f"\n📊 统计信息:")
    print(f"  - 添加行数: {diff['added_lines']}")
    print(f"  - 删除行数: {diff['removed_lines']}")
    print(f"  - 修改行数: {diff['modified_lines']}")
    
    print("\n📋 详细差异:")
    print("-" * 50)
    print(diff["diff"])
    print("-" * 50)
    
    # 演示版本历史
    print("\n📜 版本历史:")
    history = version_manager.get_version_history(prompt_id)
    for version in history:
        print(f"  • {version.version_number} - {version.change_log}")
    
    # 演示影响分析
    print("\n⚠️ 影响分析:")
    impact = version_manager.analyze_impact(prompt_id)
    print(json.dumps(impact, ensure_ascii=False, indent=2))
    
    # 清理临时目录
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    print("🚀 云枢智能体第三阶段功能演示")
    print("="*60)
    
    # 演示全链路回溯
    demo_trace_backtracking()
    
    # 演示失败模式分类归档
    demo_failure_analysis_logging()
    
    # 演示版本对比
    demo_version_comparison()
    
    print("\n" + "="*60)
    print("🎉 所有演示完成！")
    print("="*60)