#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mock Prometheus HTTP 服务（用于本地验证可见性趋势报告生成逻辑）

【用途】
当本地 Prometheus 历史数据不足时，启动本服务可模拟过去 7 天/30 天的指标数据，
用于验证 generate_visibility_trend.py 的趋势图渲染、趋势变化计算、降级逻辑等。

【用法】
    # 启动 mock 服务（默认端口 9099）
    python scripts/mock_prometheus_server.py

    # 指定端口
    python scripts/mock_prometheus_server.py --port 9099

    # 用 mock 服务生成周报
    python scripts/generate_visibility_trend.py \
        --prometheus-url http://localhost:9099 \
        --period weekly \
        --non-interactive

【实现的端点】
    GET /-/healthy         → 200 "Prometheus Server is Healthy."
    GET /api/v1/query_range → 模拟 7 天/30 天的指标矩阵数据
    GET /api/v1/query       → 模拟瞬时查询（返回最新值）

【生成日志摘要】
- 生成时间：2026-06-29
- 版本：v1.0.0
- 内容：Mock Prometheus 服务，覆盖 16 个可见性指标的 7 天历史数据
"""

from __future__ import annotations

import argparse
import json
import re
import time
import math
import uuid
import logging
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger("mock_prometheus")


def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


# ═══════════════════════════════════════════════════════════════
#  模拟数据定义：16 个指标的 7 天趋势
# ═══════════════════════════════════════════════════════════════

# 每个指标对应一个 7 元素列表，代表过去 7 天每天的采样值（从最旧到最新）
# 设计原则：覆盖上升/下降/波动/稳定四种趋势，便于验证趋势图渲染
MOCK_METRIC_VALUES: Dict[str, List[float]] = {
    # ── 总体 ──
    "yunshu_visibility_overall_status":            [0, 0, 1, 1, 2, 1, 0],       # pass→fail→degraded→恢复
    "yunshu_visibility_threshold_violations_total": [5, 4, 6, 7, 5, 3, 2],      # 波动后改善
    "yunshu_visibility_layer_passed":               [2, 2, 1, 1, 2, 3, 4],      # 逐渐改善
    "yunshu_visibility_report_duration_seconds":    [4.5, 4.2, 5.1, 4.8, 3.9, 3.5, 3.2],  # 性能优化
    # ── 运行时可见 ──
    "yunshu_visibility_runtime_structured_log_coverage": [75, 78, 82, 85, 88, 90, 92],   # 持续提升
    "yunshu_visibility_runtime_trace_coverage":          [60, 65, 68, 70, 72, 75, 78],   # 持续提升
    "yunshu_visibility_runtime_health_endpoints":        [2, 2, 2, 3, 3, 3, 3],         # 新增健康检查
    # ── 验证过程可见 ──
    "yunshu_visibility_verification_test_coverage":          [35, 38, 42, 45, 48, 50, 52],  # 覆盖率提升
    "yunshu_visibility_verification_boundary_test_coverage": [8, 10, 12, 15, 18, 20, 22],   # 边界测试增加
    "yunshu_visibility_verification_contract_test_count":    [2, 2, 3, 3, 3, 4, 4],         # 契约测试增加
    # ── 业务价值可见 ──
    "yunshu_visibility_business_track_event_coverage": [50, 55, 60, 65, 70, 75, 80],  # 埋点覆盖提升
    "yunshu_visibility_business_dashboard_count":      [7, 7, 8, 8, 9, 9, 9],          # 看板逐渐增加
    "yunshu_visibility_business_alert_rules_count":    [10, 11, 11, 12, 13, 13, 13],   # 告警规则增加
    # ── 架构影响可见 ──
    "yunshu_visibility_architecture_dependency_graph_nodes":   [200, 205, 208, 210, 212, 214, 215],  # 模块增加
    "yunshu_visibility_architecture_rule_violations":          [8, 7, 6, 5, 4, 3, 2],               # 违规减少
    "yunshu_visibility_architecture_impact_analysis_coverage": [85, 88, 90, 92, 95, 98, 100],       # 覆盖率提升
}


def _extract_metric_name(promql: str) -> Optional[str]:
    """从 PromQL 表达式中提取指标名

    支持的模式：
        max_over_time(yunshu_visibility_xxx[7d])
        max_over_time(yunshu_visibility_xxx{success="true"}[7d])
        sum(yunshu_visibility_layer_passed)
    """
    # 匹配 yunshu_visibility_ 开头的指标名（最长匹配）
    match = re.search(r'(yunshu_visibility_\w+)', promql)
    if match:
        return match.group(1)
    return None


def _generate_values_for_range(
    metric_name: str,
    start_ts: float,
    end_ts: float,
    step_seconds: int,
) -> List[Tuple[float, str]]:
    """为指定时间范围生成模拟采样点

    策略：将 7 天的基准数据插值到实际的查询粒度（如 1h step → 168 个点）
    """
    if metric_name not in MOCK_METRIC_VALUES:
        return []

    base_values = MOCK_METRIC_VALUES[metric_name]
    num_base = len(base_values)  # 7
    total_seconds = end_ts - start_ts
    num_points = int(total_seconds / step_seconds) + 1

    values: List[Tuple[float, str]] = []
    for i in range(num_points):
        t = start_ts + i * step_seconds
        if t > end_ts:
            break
        # 将当前点映射到 7 天基准数据的浮点位置
        # 0.0 = 7 天前, 6.0 = 今天
        day_offset = (t - start_ts) / total_seconds * (num_base - 1) if total_seconds > 0 else 0
        # 线性插值
        idx_low = int(math.floor(day_offset))
        idx_high = min(idx_low + 1, num_base - 1)
        frac = day_offset - idx_low
        interpolated = base_values[idx_low] * (1 - frac) + base_values[idx_high] * frac
        # 添加微小随机波动（±2%），使趋势图更真实
        # 用 sin 函数模拟周期性波动，避免引入 random 模块导致不可重现
        wave = math.sin(i * 0.3) * abs(interpolated) * 0.01
        final_val = interpolated + wave
        # 整数型指标保持整数
        if metric_name in (
            "yunshu_visibility_overall_status",
            "yunshu_visibility_layer_passed",
            "yunshu_visibility_runtime_health_endpoints",
            "yunshu_visibility_verification_contract_test_count",
            "yunshu_visibility_business_dashboard_count",
            "yunshu_visibility_business_alert_rules_count",
            "yunshu_visibility_architecture_dependency_graph_nodes",
            "yunshu_visibility_architecture_rule_violations",
            "yunshu_visibility_threshold_violations_total",
        ):
            final_val = round(final_val)
        else:
            final_val = round(final_val, 2)
        values.append((t, str(final_val)))
    return values


def _build_matrix_response(
    metric_name: str,
    start_ts: float,
    end_ts: float,
    step_seconds: int,
) -> Dict[str, Any]:
    """构造 Prometheus matrix 响应"""
    values = _generate_values_for_range(metric_name, start_ts, end_ts, step_seconds)
    if not values:
        return {
            "status": "success",
            "data": {"resultType": "matrix", "result": []},
        }
    # 构造 metric 标签
    metric_labels: Dict[str, str] = {"__name__": metric_name}
    # 带标签的指标保留 success="true" 标签
    if metric_name not in (
        "yunshu_visibility_overall_status",
        "yunshu_visibility_threshold_violations_total",
        "yunshu_visibility_layer_passed",
        "yunshu_visibility_report_duration_seconds",
        "yunshu_visibility_architecture_rule_violations",
    ):
        metric_labels["success"] = "true"

    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": metric_labels,
                    "values": values,
                }
            ],
        },
    }


# ═══════════════════════════════════════════════════════════════
#  HTTP Handler
# ═══════════════════════════════════════════════════════════════

class MockPrometheusHandler(BaseHTTPRequestHandler):
    """Mock Prometheus HTTP 请求处理器"""

    def log_message(self, format: str, *args: Any) -> None:
        """覆盖默认日志，输出结构化 JSON 日志"""
        trace_id = _trace_id()
        t0 = time.time()
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "mock_prometheus",
            "action": "http_request",
            "duration_ms": round((time.time() - t0) * 1000, 2),
            "method": self.command,
            "path": self.path,
            "message": format % args,
        }, ensure_ascii=False))

    def do_GET(self) -> None:
        """处理 GET 请求"""
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/-/healthy":
            self._respond(200, "text/plain", "Prometheus Server is Healthy.\n")
            return

        if path == "/api/v1/query_range":
            self._handle_query_range(params)
            return

        if path == "/api/v1/query":
            self._handle_query(params)
            return

        # 其他路径返回 404
        self._respond(404, "text/plain", "404 page not found\n")

    def _respond(self, status: int, content_type: str, body: str) -> None:
        """发送响应"""
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _respond_json(self, status: int, data: Dict[str, Any]) -> None:
        """发送 JSON 响应"""
        body = json.dumps(data, ensure_ascii=False)
        self._respond(status, "application/json", body)

    def _handle_query_range(self, params: Dict[str, List[str]]) -> None:
        """处理 /api/v1/query_range 请求"""
        trace_id = _trace_id()
        t0 = time.time()

        query = params.get("query", [""])[0]
        start = params.get("start", [""])[0]
        end = params.get("end", [""])[0]
        step = params.get("step", ["1h"])[0]

        # 解析时间参数（Unix 时间戳或 RFC3339）
        try:
            start_ts = float(start)
        except (ValueError, TypeError):
            # 默认 7 天前
            start_ts = time.time() - 7 * 24 * 3600
        try:
            end_ts = float(end)
        except (ValueError, TypeError):
            end_ts = time.time()

        # 解析 step（如 "1h" → 3600, "6h" → 21600）
        step_match = re.match(r'(\d+)([smhd])', step)
        if step_match:
            num = int(step_match.group(1))
            unit = step_match.group(2)
            unit_map = {"s": 1, "m": 60, "h": 3600, "d": 86400}
            step_seconds = num * unit_map[unit]
        else:
            step_seconds = 3600  # 默认 1 小时

        # 提取指标名
        metric_name = _extract_metric_name(query)

        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "mock_prometheus",
            "action": "query_range",
            "duration_ms": round((time.time() - t0) * 1000, 2),
            "query": query,
            "metric_name": metric_name,
            "start": start_ts,
            "end": end_ts,
            "step_seconds": step_seconds,
        }, ensure_ascii=False))

        if not metric_name:
            # 无法识别的查询，返回空结果
            self._respond_json(200, {
                "status": "success",
                "data": {"resultType": "matrix", "result": []},
            })
            return

        response = _build_matrix_response(metric_name, start_ts, end_ts, step_seconds)
        self._respond_json(200, response)

    def _handle_query(self, params: Dict[str, List[str]]) -> None:
        """处理 /api/v1/query 请求（瞬时查询，返回最新值）"""
        query = params.get("query", [""])[0]
        metric_name = _extract_metric_name(query)

        if not metric_name or metric_name not in MOCK_METRIC_VALUES:
            self._respond_json(200, {
                "status": "success",
                "data": {"resultType": "vector", "result": []},
            })
            return

        latest_value = MOCK_METRIC_VALUES[metric_name][-1]
        now_ts = time.time()
        metric_labels: Dict[str, str] = {"__name__": metric_name}
        if metric_name not in (
            "yunshu_visibility_overall_status",
            "yunshu_visibility_threshold_violations_total",
            "yunshu_visibility_layer_passed",
            "yunshu_visibility_report_duration_seconds",
            "yunshu_visibility_architecture_rule_violations",
        ):
            metric_labels["success"] = "true"

        self._respond_json(200, {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {
                        "metric": metric_labels,
                        "value": [now_ts, str(latest_value)],
                    }
                ],
            },
        })


# ═══════════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════════

def _collect_real_metrics(project_root: Path) -> Dict[str, float]:
    """调用 visibility_report.MetricCollector 采集真实当前指标值

    将 VisibilityReport 的四层指标映射到 MOCK_METRIC_VALUES 的 16 个指标名。
    采集失败时抛出 RuntimeError（业务错误码 MOCK_ERR_REAL_SOURCE_FAILED），
    遵循"边界显性化"原则——不静默返回空字典。

    【映射关系】
    VisibilityReport.layers[0] = 运行时可见（structured_log_coverage/trace_coverage/health_endpoints）
    VisibilityReport.layers[1] = 验证过程可见（test_coverage/boundary_test_coverage/contract_test_count/exception_coverage）
    VisibilityReport.layers[2] = 业务价值可见（track_event_coverage/dashboard_count/alert_rules_count）
    VisibilityReport.layers[3] = 架构影响可见（dependency_graph_nodes/rule_violations/impact_analysis_coverage）
    """
    trace_id = _trace_id()
    t0 = time.time()

    # 延迟导入，避免 mock 服务在未安装 visibility_report 依赖时启动失败
    try:
        from visibility_report import MetricCollector, load_thresholds
    except ImportError as e:
        raise RuntimeError(
            f"MOCK_ERR_REAL_SOURCE_FAILED: 无法导入 visibility_report 模块: {e}"
            f"（请确保 scripts/ 目录在 sys.path 中）"
        )

    # 加载阈值配置（使用默认 config.yaml）
    config_path = project_root / "config.yaml"
    thresholds = load_thresholds(config_path) if config_path.exists() else {}

    collector = MetricCollector(project_root, thresholds)
    layers = collector.collect_all()

    # 建立指标名 → 值的映射
    real_values: Dict[str, float] = {}
    # 层级名称 → MOCK_METRIC_VALUES 指标名前缀的映射
    layer_prefix_map = {
        "运行时可见": "yunshu_visibility_runtime_",
        "验证过程可见": "yunshu_visibility_verification_",
        "业务价值可见": "yunshu_visibility_business_",
        "架构影响可见": "yunshu_visibility_architecture_",
    }

    # metric.name → 完整指标名的映射（覆盖 MOCK_METRIC_VALUES 中的 16 个指标）
    metric_name_to_full = {
        "structured_log_coverage": "yunshu_visibility_runtime_structured_log_coverage",
        "trace_coverage": "yunshu_visibility_runtime_trace_coverage",
        "health_endpoints": "yunshu_visibility_runtime_health_endpoints",
        "test_coverage": "yunshu_visibility_verification_test_coverage",
        "boundary_test_coverage": "yunshu_visibility_verification_boundary_test_coverage",
        "contract_test_count": "yunshu_visibility_verification_contract_test_count",
        "exception_coverage": "yunshu_visibility_verification_exception_coverage",
        "track_event_coverage": "yunshu_visibility_business_track_event_coverage",
        "dashboard_count": "yunshu_visibility_business_dashboard_count",
        "alert_rules_count": "yunshu_visibility_business_alert_rules_count",
        "dependency_graph_nodes": "yunshu_visibility_architecture_dependency_graph_nodes",
        "arch_rule_violations": "yunshu_visibility_architecture_rule_violations",
        "impact_analysis_coverage": "yunshu_visibility_architecture_impact_analysis_coverage",
    }

    for layer in layers:
        for metric in layer.metrics:
            full_name = metric_name_to_full.get(metric.name)
            if full_name and full_name in MOCK_METRIC_VALUES:
                real_values[full_name] = float(metric.value)

    # 总体指标（从 layers 聚合）
    all_metrics_passed = all(layer.overall_passed for layer in layers)
    threshold_violations = sum(
        1 for layer in layers for metric in layer.metrics if metric.passed is False
    )
    passing_layers = sum(1 for layer in layers if layer.overall_passed)
    real_values["yunshu_visibility_overall_status"] = 0.0 if all_metrics_passed else 1.0
    real_values["yunshu_visibility_threshold_violations_total"] = float(threshold_violations)
    real_values["yunshu_visibility_layer_passed"] = float(passing_layers)
    real_values["yunshu_visibility_report_duration_seconds"] = 0.0  # 无法从采集器获取

    elapsed_ms = round((time.time() - t0) * 1000, 2)
    logger.info(json.dumps({
        "trace_id": trace_id,
        "module_name": "mock_prometheus",
        "action": "real_source.collect_success",
        "duration_ms": elapsed_ms,
        "real_metrics_count": len(real_values),
        "expected_count": len(MOCK_METRIC_VALUES),
    }, ensure_ascii=False))

    return real_values


def _merge_real_into_mock(real_values: Dict[str, float]) -> None:
    """将真实采集值合并到 MOCK_METRIC_VALUES（替换最新点，即第 7 天的值）

    策略：用真实值替换 base_values[-1]（最新点），保持前 6 天的模拟趋势不变。
    这样趋势图既能展示历史趋势，又能反映真实当前状态。
    """
    trace_id = _trace_id()
    t0 = time.time()
    replaced_count = 0

    for metric_name, real_value in real_values.items():
        if metric_name in MOCK_METRIC_VALUES:
            old_value = MOCK_METRIC_VALUES[metric_name][-1]
            MOCK_METRIC_VALUES[metric_name][-1] = real_value
            replaced_count += 1
            logger.debug(json.dumps({
                "trace_id": trace_id,
                "module_name": "mock_prometheus",
                "action": "real_source.merge_metric",
                "metric": metric_name,
                "old_value": old_value,
                "new_value": real_value,
            }, ensure_ascii=False))

    logger.info(json.dumps({
        "trace_id": trace_id,
        "module_name": "mock_prometheus",
        "action": "real_source.merge_complete",
        "duration_ms": round((time.time() - t0) * 1000, 2),
        "replaced_count": replaced_count,
        "total_real": len(real_values),
    }, ensure_ascii=False))


def main(argv: Optional[List[str]] = None) -> int:
    """启动 Mock Prometheus 服务"""
    parser = argparse.ArgumentParser(description="Mock Prometheus HTTP 服务（本地验证用）")
    parser.add_argument("--port", type=int, default=9099, help="监听端口（默认 9099）")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument(
        "--real-source",
        action="store_true",
        default=False,
        help="启动时调用 visibility_report.MetricCollector 采集真实指标，"
             "替换模拟数据的最新点（方案 B：Mock 内置真实采集）",
    )
    parser.add_argument(
        "--project-root",
        default=str(Path(__file__).resolve().parent.parent),
        help="项目根目录（用于真实指标采集，默认为脚本上级目录）",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.real_source else logging.INFO,
        format='%(asctime)s [%(levelname)8s] %(name)-25s: %(message)s',
        datefmt='%H:%M:%S',
    )

    trace_id = _trace_id()
    t0 = time.time()

    # 方案 B：启动前采集真实指标，替换模拟数据最新点
    if args.real_source:
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "mock_prometheus",
            "action": "real_source.start",
            "duration_ms": 0,
            "project_root": args.project_root,
        }, ensure_ascii=False))
        try:
            real_values = _collect_real_metrics(Path(args.project_root))
            _merge_real_into_mock(real_values)
            print(f"✅ 真实指标采集完成：{len(real_values)} 个指标已替换最新点")
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": trace_id,
                "module_name": "mock_prometheus",
                "action": "real_source.failed",
                "duration_ms": round((time.time() - t0) * 1000, 2),
                "error": str(e),
            }, ensure_ascii=False))
            print(f"❌ 真实指标采集失败: {e}")
            print("   回退到纯模拟数据模式")
            # 不退出，继续用纯模拟数据启动

    server = ThreadingHTTPServer((args.host, args.port), MockPrometheusHandler)

    logger.info(json.dumps({
        "trace_id": trace_id,
        "module_name": "mock_prometheus",
        "action": "server_start",
        "duration_ms": round((time.time() - t0) * 1000, 2),
        "host": args.host,
        "port": args.port,
        "metrics_count": len(MOCK_METRIC_VALUES),
        "real_source_enabled": args.real_source,
        "endpoints": ["/-/healthy", "/api/v1/query_range", "/api/v1/query"],
    }, ensure_ascii=False))

    print(f"Mock Prometheus 服务已启动: http://{args.host}:{args.port}")
    print(f"  指标数: {len(MOCK_METRIC_VALUES)}")
    print(f"  真实采集: {'✅ 已启用' if args.real_source else '❌ 未启用（纯模拟模式）'}")
    print(f"  端点: /-/healthy, /api/v1/query_range, /api/v1/query")
    print(f"  按 Ctrl+C 停止")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "mock_prometheus",
            "action": "server_stop",
            "duration_ms": round((time.time() - t0) * 1000, 2),
            "reason": "KeyboardInterrupt",
        }, ensure_ascii=False))
        print("\n服务已停止")
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
