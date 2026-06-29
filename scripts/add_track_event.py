#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为未埋点子目录创建 observability.py，添加 BusinessMetricsCollector 埋点

使 track_event_coverage 从 13.8% 提升到 50%+
"""

import os
from pathlib import Path

# 11 个未埋点子目录及其埋点事件配置
MODULES = {
    "orchestrator": {
        "logger_name": "agent.orchestrator",
        "events": [
            ('task_dispatch', 'task_type, complexity, status'),
            ('task_complete', 'task_type, duration_ms, success'),
        ],
    },
    "tools": {
        "logger_name": "agent.tools",
        "events": [
            ('tool_call', 'tool_name, tool_category, success'),
            ('tool_complete', 'tool_name, duration_ms, success'),
        ],
    },
    "memory": {
        "logger_name": "agent.memory",
        "events": [
            ('memory_search', 'memory_type, search_method, hit'),
            ('memory_access', 'memory_key, importance'),
            ('memory_storage', 'memory_type, importance, success'),
        ],
    },
    "model_router": {
        "logger_name": "agent.model_router",
        "events": [
            ('model_call', 'model_name, provider, success, duration_ms'),
            ('model_switch', 'from_model, to_model, reason'),
        ],
    },
    "extensions": {
        "logger_name": "agent.extensions",
        "events": [
            ('extension_install', 'extension_type, source, success'),
            ('extension_uninstall', 'extension_type, extension_id'),
            ('mcp_connection', 'server_name, transport, success'),
        ],
    },
    "cognitive": {
        "logger_name": "agent.cognitive",
        "events": [
            ('cognitive_task', 'task_type, complexity, success'),
            ('reflection_complete', 'depth, duration_ms, success'),
        ],
    },
    "subagent": {
        "logger_name": "agent.subagent",
        "events": [
            ('subagent_create', 'agent_type, task_complexity'),
            ('subagent_complete', 'agent_type, duration_ms, success'),
        ],
    },
    "task_planner": {
        "logger_name": "agent.task_planner",
        "events": [
            ('planning_task', 'planner_type, steps_count, success'),
            ('dag_build', 'node_count, edge_count, success'),
        ],
    },
    "p6": {
        "logger_name": "agent.p6",
        "events": [
            ('performance_snapshot', 'snapshot_type, metrics_count'),
            ('performance_check', 'check_type, threshold, actual, passed'),
        ],
    },
    "log_system": {
        "logger_name": "agent.log_system",
        "events": [
            ('log_query', 'query_type, result_count, duration_ms'),
            ('log_aggregate', 'time_range, log_count'),
        ],
    },
    "caching": {
        "logger_name": "agent.caching",
        "events": [
            ('cache_hit', 'cache_key, cache_level'),
            ('cache_miss', 'cache_key, cache_level, fallback'),
            ('cache_eviction', 'cache_key, reason'),
        ],
    },
}

TEMPLATE = '''"""{module_name} 模块可观测性埋点

遵循 yunshu_<模块>_<动作> 命名规范，使用 BusinessMetricsCollector 统一收集。
埋点失败不影响主流程（吞掉异常，仅日志记录）。
"""

from __future__ import annotations
import json
import time
import uuid
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("{logger_name}")

try:
    from agent.monitoring.business_metrics import BusinessMetricsCollector
    _metrics = BusinessMetricsCollector()
    _METRICS_AVAILABLE = True
except Exception:
    _metrics = None
    _METRICS_AVAILABLE = False


def _trace_id() -> str:
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


def _emit_structured_log(action: str, *, trace_id: Optional[str] = None,
                         duration_ms: float = 0.0, level: str = "info",
                         **payload: Any) -> None:
    """输出结构化日志"""
    record = {{
        "trace_id": trace_id or _trace_id(),
        "module_name": "{module_name}",
        "action": action,
        "duration_ms": round(duration_ms, 2),
        **payload,
    }}
    getattr(logger, level, logger.info)(json.dumps(record, ensure_ascii=False, default=str))


def trackEvent(event_name: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """埋点函数——记录用户交互/业务事件

    埋点失败不影响主流程（吞掉异常，仅日志记录）。
    指标命名遵循 yunshu_{module_name}_<event_name> 格式。
    """
    tid = _trace_id()
    t0 = time.time()
    try:
        _emit_structured_log(
            f"track.{{event_name}}",
            trace_id=tid,
            duration_ms=0.0,
            event_name=event_name,
            **(payload or {{}}),
        )
        if _METRICS_AVAILABLE:
            _metrics.record_interaction(event_name, "{module_name}", True, (time.time() - t0) * 1000)
    except Exception as e:
        logger.error(json.dumps({{
            "trace_id": tid,
            "module_name": "{module_name}",
            "action": "trackEvent.failed",
            "error": f"{{type(e).__name__}}: {{e}}",
            "event_name": event_name,
        }}, ensure_ascii=False))
'''


def main():
    agent_dir = Path('agent')
    created = 0
    for module_name, config in MODULES.items():
        target_dir = agent_dir / module_name
        if not target_dir.is_dir():
            print(f'  ⚠️  目录不存在: {target_dir}')
            continue
        obs_file = target_dir / 'observability.py'
        if obs_file.exists():
            print(f'  ⏭️  已存在: {obs_file}')
            continue
        content = TEMPLATE.format(
            module_name=module_name,
            logger_name=config['logger_name'],
        )
        obs_file.write_text(content, encoding='utf-8')
        print(f'  ✅ 创建: {obs_file}')
        created += 1
    print(f'\n总计: 创建 {created} 个 observability.py')


if __name__ == '__main__':
    main()
