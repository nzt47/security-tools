#!/usr/bin/env python3
"""
简化版高并发压测脚本

快速验证在大量并发请求下，追踪上下文是否依然能稳定传播。
"""

import sys
import time
import concurrent.futures
from collections import defaultdict

sys.path.insert(0, '.')

# 先设置日志级别为 ERROR，减少输出
import logging
logging.getLogger('agent.monitoring.tracing').setLevel(logging.ERROR)

from agent.monitoring import (
    TraceContext,
    get_trace_id,
    set_trace_id,
    set_span_id,
    extract_trace_context,
    inject_trace_context,
)


def single_request(request_id: int) -> dict:
    """模拟单个请求，返回结果"""
    result = {
        'request_id': request_id,
        'success': True,
        'trace_id': None,
        'error': None,
        'context_leak': False,
    }
    
    try:
        # 确保初始状态干净
        set_trace_id(None)
        set_span_id(None)
        
        # API Gateway 入口
        with TraceContext("APIGateway", f"req_{request_id}") as gateway_ctx:
            root_trace_id = gateway_ctx.trace_id
            result['trace_id'] = root_trace_id
            headers = inject_trace_context()
            
            # ServiceA
            ctx_a = extract_trace_context(headers)
            if not ctx_a or ctx_a['trace_id'] != root_trace_id:
                raise ValueError(f"ServiceA 上下文不匹配: {ctx_a}")
            
            set_trace_id(ctx_a['trace_id'])
            set_span_id(ctx_a['span_id'])
            
            with TraceContext("ServiceA", "process"):
                headers_a = inject_trace_context()
                
                # ServiceB
                ctx_b = extract_trace_context(headers_a)
                if not ctx_b or ctx_b['trace_id'] != root_trace_id:
                    raise ValueError(f"ServiceB 上下文不匹配")
                
                set_trace_id(ctx_b['trace_id'])
                set_span_id(ctx_b['span_id'])
                
                with TraceContext("ServiceB", "process"):
                    pass
        
        # 检查上下文泄漏
        if get_trace_id() is not None:
            result['context_leak'] = True
            set_trace_id(None)
            set_span_id(None)
    
    except Exception as e:
        result['success'] = False
        result['error'] = str(e)
    finally:
        set_trace_id(None)
        set_span_id(None)
    
    return result


def run_stress_test(num_requests: int, max_workers: int, name: str) -> dict:
    """运行压测"""
    print(f"\n🚀 {name}: {num_requests} 请求, {max_workers} 并发")
    
    start_time = time.time()
    results = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(single_request, i) for i in range(num_requests)]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    
    duration = time.time() - start_time
    
    # 统计结果
    success = sum(1 for r in results if r['success'])
    failed = num_requests - success
    leaks = sum(1 for r in results if r['context_leak'])
    
    # 检查唯一 trace_id 数量（应该等于请求数）
    trace_ids = set(r['trace_id'] for r in results if r['trace_id'])
    unique_count = len(trace_ids)
    
    # 错误列表
    errors = [r['error'] for r in results if r['error']][:5]
    
    print(f"  ✅ 成功: {success}/{num_requests}")
    print(f"  ❌ 失败: {failed}")
    print(f"  💧 上下文泄漏: {leaks}")
    print(f"  🆔 唯一 trace_id: {unique_count}")
    print(f"  ⏱️  耗时: {duration:.2f}s")
    print(f"  ⚡ QPS: {num_requests/duration:.1f}")
    
    if errors:
        print(f"  🐛 错误样例:")
        for err in errors:
            print(f"     - {err}")
    
    passed = (failed == 0 and leaks == 0 and unique_count == num_requests)
    print(f"  结果: {'✅ 通过' if passed else '❌ 失败'}")
    
    return {
        'name': name,
        'success': success,
        'failed': failed,
        'leaks': leaks,
        'unique_trace_ids': unique_count,
        'total': num_requests,
        'duration': duration,
        'qps': num_requests / duration,
        'passed': passed,
    }


def main():
    print("="*60)
    print("🔬 追踪上下文高并发稳定性压测")
    print("="*60)
    
    scenarios = [
        (100, 10, "低并发 (100/10)"),
        (500, 50, "中并发 (500/50)"),
        (1000, 100, "高并发 (1000/100)"),
    ]
    
    all_results = []
    all_passed = True
    
    for num_req, workers, name in scenarios:
        result = run_stress_test(num_req, workers, name)
        all_results.append(result)
        if not result['passed']:
            all_passed = False
    
    # 总结
    print("\n" + "="*60)
    print("📊 压测总结")
    print("="*60)
    
    for r in all_results:
        status = "✅" if r['passed'] else "❌"
        print(f"  {status} {r['name']}: {r['success']}/{r['total']} 成功, QPS={r['qps']:.1f}")
    
    print(f"\n  整体结果: {'✅ 全部通过' if all_passed else '❌ 存在问题'}")
    print("="*60)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())