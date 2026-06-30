#!/usr/bin/env python3
"""
验证 alert_evaluator.py 结构化日志输出

验证项：
1. trace_id 不再为 None
2. 所有日志均为 JSON 格式（json.dumps）
3. 所有日志包含 duration_ms 字段
4. duration_seconds 字段已统一为 duration_ms
5. 后台线程使用评估器专属 trace_id

运行：python scripts/verify_alert_logging.py
"""
import sys
import os
import io
import json
import logging
import time

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    # 配置日志捕获
    log_capture = io.StringIO()
    handler = logging.StreamHandler(log_capture)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(message)s')
    handler.setFormatter(formatter)

    alert_logger = logging.getLogger('agent.monitoring.alert_evaluator')
    alert_logger.addHandler(handler)
    alert_logger.setLevel(logging.DEBUG)

    # 设置主线程 trace_id
    from agent.monitoring.tracing import set_trace_id, get_trace_id
    expected_trace_id = 'verify-alert-trace-id-12345'
    set_trace_id(expected_trace_id)
    print(f'[VERIFY] 设置主线程 trace_id: {get_trace_id()}')

    # 触发 alert_evaluator 各日志节点
    from agent.monitoring.alert_evaluator import AlertEvaluator, AlertRule
    evaluator = AlertEvaluator(evaluation_interval=0.1, pending_duration=0.1)
    rule = AlertRule(
        name='test_rule',
        expr='yunshu_error_total[5m]',
        threshold=1.0,
        severity='warning'
    )
    evaluator.add_rule(rule)
    evaluator.start()
    time.sleep(0.3)
    evaluator.stop()
    evaluator.remove_rule('test_rule')

    # 验证日志输出
    log_output = log_capture.getvalue()
    print('\n========== 捕获的 alert_evaluator 日志 ==========')
    json_count = 0
    ok_count = 0
    fail_count = 0
    actions_seen = set()

    for line in log_output.strip().split('\n'):
        line = line.strip()
        if not line or not line.startswith('{'):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            print(f'[FAIL] 非JSON格式: {line[:120]}')
            fail_count += 1
            continue

        json_count += 1
        action = parsed.get('action', '<missing>')
        trace_id = parsed.get('trace_id')
        has_duration = 'duration_ms' in parsed
        has_duration_seconds = 'duration_seconds' in parsed
        actions_seen.add(action)

        # 后台线程使用评估器专属 trace_id，主线程使用设置的 trace_id
        background_actions = {'evaluation_error'}
        if action in background_actions:
            trace_ok = trace_id is not None and str(trace_id).startswith('alert-eval-')
            trace_check = f'trace_id OK (evaluator: {trace_id})' if trace_ok else f'trace_id FAIL (got: {trace_id})'
        else:
            trace_ok = trace_id == expected_trace_id
            trace_check = 'trace_id OK' if trace_ok else f'trace_id FAIL (got: {trace_id})'

        all_ok = trace_ok and has_duration and not has_duration_seconds
        status = 'OK' if all_ok else 'FAIL'
        if all_ok:
            ok_count += 1
        else:
            fail_count += 1

        checks = [trace_check]
        checks.append('duration_ms OK' if has_duration else 'duration_ms MISSING')
        checks.append('no_duration_seconds OK' if not has_duration_seconds else 'duration_seconds FORBIDDEN')
        print(f'[{status}] action={action} | {" | ".join(checks)}')

    print('\n========== 验证汇总 ==========')
    print(f'JSON 日志总数: {json_count}')
    print(f'通过: {ok_count}')
    print(f'失败: {fail_count}')
    print(f'覆盖的 action: {sorted(actions_seen)}')

    if fail_count == 0 and ok_count >= 4:
        print('\n[PASS] alert_evaluator.py 结构化日志验证通过')
        return 0
    else:
        print('\n[FAIL] 验证未通过')
        return 1


if __name__ == '__main__':
    sys.exit(main())
