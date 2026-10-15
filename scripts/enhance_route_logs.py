#!/usr/bin/env python3
"""
增强 routes_dashboard.py 的路由日志

目标：在关键路由入口处添加更详细的日志打印，
包含具体的参数值和 trace_id 变化过程，方便排查链路追踪问题。

修改点：
1. routes_dashboard.py - api_dashboard_quality 入口/出口日志增强
2. routes_dashboard.py - api_dashboard_traces  入口/出口日志增强

【2026-10-15】原脚本同时增强 routes_chat.py；该模块未接线已退役删除，
故移除 routes_chat 相关常量与函数，只保留 routes_dashboard 部分。
"""

import os
import sys
import re

# ── 工具函数 ──────────────────────────────────────────────

def read_file_safe(path):
    """尝试多种编码读取文件"""
    for enc in ('utf-8', 'utf-8-sig', 'gbk', 'latin-1'):
        try:
            with open(path, 'r', encoding=enc) as f:
                content = f.read()
            print(f"  [OK] 读取成功: {path} (encoding={enc}, len={len(content)})")
            return content, enc
        except (UnicodeDecodeError, OSError) as e:
            print(f"  [FAIL] {enc}: {e}")
    raise RuntimeError(f"无法读取文件: {path}")


def write_file_safe(path, content, enc):
    """写回文件"""
    with open(path, 'w', encoding=enc, newline='') as f:
        f.write(content)
    print(f"  [OK] 写入成功: {path} (encoding={enc}, len={len(content)})")


def replace_once(content, old, new, label):
    """精确替换一次，失败时报错"""
    count = content.count(old)
    if count == 0:
        print(f"  [FAIL] 未找到: {label}")
        print(f"         old (前60字符): {repr(old[:60])}")
        return content, False
    if count > 1:
        print(f"  [WARN] 匹配多处({count}): {label}，仅替换第一处")
    content = content.replace(old, new, 1)
    print(f"  [OK] 替换成功: {label}")
    return content, True


# ── 增强日志模板 ──────────────────────────────────────────

QUALITY_OLD = '''        # trace_id 链路追踪验证日志（@trace_route 已创建 TraceContext，此处可读取）
        _tid = get_trace_id()
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_dashboard", "action": "quality.start", "duration_ms": 0, "time_range": "%s"}',
            _tid, time_range
        )'''

QUALITY_NEW = '''        # ── 链路追踪：入口日志（含完整参数值 + 初始 trace_id） ──
        # @trace_route 已创建 TraceContext，此处读取的 trace_id 为本次请求的唯一标识
        # 记录 trace_id_entry 作为基准，后续节点对比 trace_id_changed 以排查链路断裂
        _tid_entry = get_trace_id()
        _q_start = time.time()
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_dashboard", "action": "quality.entry", '
            '"duration_ms": 0, "phase": "entry", "params": {"time_range": "%s", "start_time": %s, "end_time": %s}, '
            '"trace_id_phase": "entry"}',
            _tid_entry, time_range,
            str(start_time) if start_time else "null",
            str(end_time) if end_time else "null"
        )'''

# 在 quality 的结果返回前添加 trace_id 变化日志
QUALITY_END_OLD = '''        result = _get_quality_metrics(start_time, end_time)
        return jsonify(result)'''

QUALITY_END_NEW = '''        result = _get_quality_metrics(start_time, end_time)
        # ── 链路追踪：出口日志（记录 trace_id 变化 + 结果摘要） ──
        _tid_exit = get_trace_id()
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_dashboard", "action": "quality.exit", '
            '"duration_ms": %.2f, "phase": "exit", "schema_pass_rate": %s, "critic_avg": %s, "total_failures": %s, '
            '"trace_id_entry": "%s", "trace_id_changed": %s}',
            _tid_exit, (time.time() - _q_start) * 1000,
            str(result.get("schema_validation", {}).get("pass_rate", "null")),
            str(result.get("critic_scores", {}).get("average_score", "null")),
            str(result.get("failure_distribution", {}).get("total_failures", "null")),
            _tid_entry, str(_tid_exit != _tid_entry)
        )
        return jsonify(result)'''

# 4. routes_dashboard.py - api_dashboard_traces
TRACES_OLD = '''        # trace_id 链路追踪验证日志
        _tid = get_trace_id()
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_dashboard", "action": "traces.list", "duration_ms": 0, "limit": %d, "filter": "%s"}',
            _tid, limit, trace_id_filter
        )'''

TRACES_NEW = '''        # ── 链路追踪：入口日志（含完整参数值 + 初始 trace_id） ──
        # 记录 trace_id_entry 作为基准，出口处对比 trace_id_changed 以排查链路断裂
        _tid_entry = get_trace_id()
        _t_start = time.time()
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_dashboard", "action": "traces.entry", '
            '"duration_ms": 0, "phase": "entry", "params": {"limit": %d, "trace_id_filter": "%s", "filter_len": %d}, '
            '"trace_id_phase": "entry"}',
            _tid_entry, limit, trace_id_filter, len(trace_id_filter)
        )'''

# 在 traces 的结果返回前添加 trace_id 变化日志
TRACES_END_OLD = '''        traces = _get_trace_list(limit, trace_id_filter)
        
        return jsonify({
            "traces": traces,
            "total": len(traces),
            "limit": limit,
            "timestamp": time.time()
        })'''

TRACES_END_NEW = '''        traces = _get_trace_list(limit, trace_id_filter)

        # ── 链路追踪：出口日志（记录 trace_id 变化 + 结果摘要） ──
        _tid_exit = get_trace_id()
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_dashboard", "action": "traces.exit", '
            '"duration_ms": %.2f, "phase": "exit", "traces_count": %d, "limit": %d, "has_filter": %s, '
            '"trace_id_entry": "%s", "trace_id_changed": %s}',
            _tid_exit, (time.time() - _t_start) * 1000, len(traces), limit,
            str(bool(trace_id_filter)), _tid_entry, str(_tid_exit != _tid_entry)
        )

        return jsonify({
            "traces": traces,
            "total": len(traces),
            "limit": limit,
            "timestamp": time.time()
        })'''


# ── 主流程 ──────────────────────────────────────────────

def enhance_routes_dashboard():
    """增强 routes_dashboard.py 的路由日志"""
    path = os.path.join('agent', 'server_routes', 'routes_dashboard.py')
    print(f"\n[1/1] 增强 {path}")
    content, enc = read_file_safe(path)

    # 替换 api_dashboard_quality 入口日志
    content, ok1 = replace_once(content, QUALITY_OLD, QUALITY_NEW, "quality 入口日志")
    # 替换 api_dashboard_quality 出口日志
    content, ok2 = replace_once(content, QUALITY_END_OLD, QUALITY_END_NEW, "quality 出口日志")
    # 替换 api_dashboard_traces 入口日志
    content, ok3 = replace_once(content, TRACES_OLD, TRACES_NEW, "traces 入口日志")
    # 替换 api_dashboard_traces 出口日志
    content, ok4 = replace_once(content, TRACES_END_OLD, TRACES_END_NEW, "traces 出口日志")

    if ok1 and ok2 and ok3 and ok4:
        write_file_safe(path, content, enc)
        print(f"  [DONE] routes_dashboard.py 全部 4 处替换完成")
    else:
        print(f"  [SKIP] routes_dashboard.py 有未匹配项，未写入文件（避免部分修改）")
        return False
    return True


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_root)
    print(f"工作目录: {project_root}")

    ok = enhance_routes_dashboard()

    print("\n" + "=" * 60)
    print("增强结果汇总:")
    print(f"  routes_dashboard.py: {'成功' if ok else '失败'}")
    print("=" * 60)

    if ok:
        print("\n路由日志增强完成！")
        print("新增日志节点：")
        print("  - api_dashboard_quality: entry / exit")
        print("  - api_dashboard_traces:  entry / exit")
        print("\n每个日志节点都包含:")
        print("  - trace_id:        当前节点的 trace_id")
        print("  - trace_id_entry:  入口基准 trace_id")
        print("  - trace_id_changed: trace_id 是否发生变化（true 表示链路可能断裂）")
        print("  - phase:           当前阶段标识")
        print("  - params:          具体参数值")
        return 0
    else:
        print("\n部分替换失败，请检查上述 [FAIL] 信息")
        return 1


if __name__ == '__main__':
    sys.exit(main())
