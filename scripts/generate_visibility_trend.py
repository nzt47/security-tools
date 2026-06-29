#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四层可见性趋势报告生成器（周报/月报）

【生成日志摘要】
- 生成时间：2026-06-28
- 版本：v1.0.0
- 内容：从 Prometheus query_range API 拉取四层可见性指标历史数据，
       生成 Markdown + HTML 双格式趋势报告（含 SVG 折线图）。
- 生成参数：--period weekly|monthly, --prometheus-url, --output
- 模型配置：标准库 urllib + concurrent.futures，无第三方依赖
- 关键状态变化：新增趋势报告能力，与 visibility_report.py 形成闭环

【可观测性约束】
- 结构化日志：trace_id / module_name / action / duration_ms
- 边界显性化：Prometheus 不可达、查询超时、数据为空均抛 TrendReportError
- 健康检查：脚本末尾输出依赖项状态（Prometheus 可达性、查询成功率、报告生成状态）
- 后端权威原则：所有数据来自 Prometheus，不在本地推导状态

【使用方式】
    # 周报（默认）
    python scripts/generate_visibility_trend.py \\
        --prometheus-url http://localhost:9091 \\
        --output docs/observability/trends/

    # 月报
    python scripts/generate_visibility_trend.py \\
        --period monthly \\
        --prometheus-url http://localhost:9091 \\
        --output docs/observability/trends/

    # CI 自动生成（失败不阻断）
    python scripts/generate_visibility_trend.py \\
        --prometheus-url http://prometheus:9090 \\
        --output docs/observability/trends/ \\
        --non-interactive
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 项目根目录
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

logger = logging.getLogger("visibility_trend")


def _setup_logging(verbose: bool = False) -> None:
    """配置结构化日志"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)8s] %(name)-30s: %(message)s',
        datefmt='%H:%M:%S',
    )


def _trace_id() -> str:
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


def _emit_log(action: str, log_level: str, trace_id: Optional[str], **fields) -> None:
    """输出 JSON 格式结构化日志（遵循可观测性约束）"""
    payload: Dict[str, Any] = {
        "trace_id": trace_id or _trace_id(),
        "module_name": "visibility_trend",
        "action": action,
    }
    payload.update(fields)
    if "duration_ms" not in payload:
        payload["duration_ms"] = 0
    getattr(logger, log_level, logger.info)(json.dumps(payload, ensure_ascii=False, default=str))


# ═══════════════════════════════════════════════════════════════
#  异常定义
# ═══════════════════════════════════════════════════════════════

TREND_ERR_PROMETHEUS_UNREACHABLE = "TREND_ERR_001"   # Prometheus 不可达
TREND_ERR_QUERY_FAILED = "TREND_ERR_002"               # 查询失败（HTTP 错误或超时）
TREND_ERR_NO_DATA = "TREND_ERR_003"                    # 查询返回空数据
TREND_ERR_INVALID_PERIOD = "TREND_ERR_004"             # 周期参数无效
TREND_ERR_RENDER_FAILED = "TREND_ERR_005"              # 报告渲染失败
TREND_ERR_OUTPUT_WRITE_FAILED = "TREND_ERR_006"        # 输出文件写入失败


class TrendReportError(Exception):
    """趋势报告异常，携带业务错误码"""

    def __init__(self, code: str, message: str, details: Optional[Dict] = None) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"[{code}] {message}")


# ═══════════════════════════════════════════════════════════════
#  数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class MetricSeries:
    """单个指标的时间序列数据"""
    name: str
    description: str
    layer: str  # runtime / verification / business / architecture / overall
    unit: str
    timestamps: List[float] = field(default_factory=list)  # Unix 秒
    values: List[float] = field(default_factory=list)
    # 查询状态：success / empty / failed
    status: str = "success"
    error: Optional[str] = None

    @property
    def point_count(self) -> int:
        return len(self.values)

    @property
    def latest_value(self) -> Optional[float]:
        return self.values[-1] if self.values else None

    @property
    def first_value(self) -> Optional[float]:
        return self.values[0] if self.values else None

    @property
    def trend_delta(self) -> Optional[float]:
        """趋势变化值（最新 - 最早）"""
        if len(self.values) < 2:
            return None
        try:
            return float(self.values[-1]) - float(self.values[0])
        except (TypeError, ValueError):
            return None

    @property
    def trend_percent(self) -> Optional[float]:
        """趋势变化百分比"""
        if len(self.values) < 2:
            return None
        try:
            first = float(self.values[0])
            last = float(self.values[-1])
            if first == 0:
                return None  # 避免除零
            return round((last - first) / first * 100, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            return None


@dataclass
class TrendReport:
    """趋势报告"""
    trace_id: str
    period: str  # weekly / monthly
    start_time: str  # ISO 8601
    end_time: str
    generated_at: str
    duration_ms: float
    series: List[MetricSeries]
    prometheus_url: str
    overall_status: str  # pass / fail / degraded
    errors: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
#  指标查询定义
# ═══════════════════════════════════════════════════════════════

# 查询清单：覆盖四层 + 总体状态
# 每个查询定义：name / description / layer / unit / promql
TREND_QUERIES: List[Dict[str, str]] = [
    # ── 总体 ──
    {
        "name": "overall_status",
        "description": "总体可见性状态（0=pass, 1=fail, 2=degraded）",
        "layer": "overall",
        "unit": "",
        "promql": "max_over_time(yunshu_visibility_overall_status[{__range}])",
    },
    {
        "name": "threshold_violations",
        "description": "阈值违规项总数",
        "layer": "overall",
        "unit": "项",
        "promql": "max_over_time(yunshu_visibility_threshold_violations_total[{__range}])",
    },
    {
        "name": "passing_layers",
        "description": "通过层数（0-4）",
        "layer": "overall",
        "unit": "层",
        "promql": "sum(yunshu_visibility_layer_passed)",
    },
    {
        "name": "report_duration",
        "description": "报告生成耗时（秒）",
        "layer": "overall",
        "unit": "秒",
        "promql": "max_over_time(yunshu_visibility_report_duration_seconds[{__range}])",
    },
    # ── 运行时可见 ──
    {
        "name": "runtime_structured_log_coverage",
        "description": "结构化日志覆盖率",
        "layer": "runtime",
        "unit": "%",
        "promql": "max_over_time(yunshu_visibility_runtime_structured_log_coverage{success=\"true\"}[{__range}])",
    },
    {
        "name": "runtime_trace_coverage",
        "description": "链路追踪覆盖率",
        "layer": "runtime",
        "unit": "%",
        "promql": "max_over_time(yunshu_visibility_runtime_trace_coverage{success=\"true\"}[{__range}])",
    },
    {
        "name": "runtime_health_endpoints",
        "description": "健康检查端点数",
        "layer": "runtime",
        "unit": "个",
        "promql": "max_over_time(yunshu_visibility_runtime_health_endpoints{success=\"true\"}[{__range}])",
    },
    # ── 验证过程可见 ──
    {
        "name": "verification_test_coverage",
        "description": "测试覆盖率",
        "layer": "verification",
        "unit": "%",
        "promql": "max_over_time(yunshu_visibility_verification_test_coverage{success=\"true\"}[{__range}])",
    },
    {
        "name": "verification_boundary_test_coverage",
        "description": "边界测试覆盖率",
        "layer": "verification",
        "unit": "%",
        "promql": "max_over_time(yunshu_visibility_verification_boundary_test_coverage{success=\"true\"}[{__range}])",
    },
    {
        "name": "verification_contract_test_count",
        "description": "契约测试数",
        "layer": "verification",
        "unit": "个",
        "promql": "max_over_time(yunshu_visibility_verification_contract_test_count{success=\"true\"}[{__range}])",
    },
    # ── 业务价值可见 ──
    {
        "name": "business_track_event_coverage",
        "description": "埋点覆盖率",
        "layer": "business",
        "unit": "%",
        "promql": "max_over_time(yunshu_visibility_business_track_event_coverage{success=\"true\"}[{__range}])",
    },
    {
        "name": "business_dashboard_count",
        "description": "看板数量",
        "layer": "business",
        "unit": "个",
        "promql": "max_over_time(yunshu_visibility_business_dashboard_count{success=\"true\"}[{__range}])",
    },
    {
        "name": "business_alert_rules_count",
        "description": "告警规则数",
        "layer": "business",
        "unit": "条",
        "promql": "max_over_time(yunshu_visibility_business_alert_rules_count{success=\"true\"}[{__range}])",
    },
    # ── 架构影响可见 ──
    {
        "name": "architecture_dependency_graph_nodes",
        "description": "依赖图节点数",
        "layer": "architecture",
        "unit": "个",
        "promql": "max_over_time(yunshu_visibility_architecture_dependency_graph_nodes{success=\"true\"}[{__range}])",
    },
    {
        "name": "architecture_rule_violations",
        "description": "架构规则违规数（越少越好）",
        "layer": "architecture",
        "unit": "个",
        "promql": "max_over_time(yunshu_visibility_architecture_rule_violations[{__range}])",
    },
    {
        "name": "architecture_impact_analysis_coverage",
        "description": "影响分析覆盖率",
        "layer": "architecture",
        "unit": "%",
        "promql": "max_over_time(yunshu_visibility_architecture_impact_analysis_coverage{success=\"true\"}[{__range}])",
    },
]


# ═══════════════════════════════════════════════════════════════
#  Prometheus 查询客户端
# ═══════════════════════════════════════════════════════════════

class PrometheusClient:
    """Prometheus HTTP API 客户端

    使用 urllib 标准库，避免引入第三方 HTTP 依赖。
    支持查询超时、重试、错误码映射。
    """

    def __init__(self, base_url: str, timeout: int = 30, max_retries: int = 2) -> None:
        """初始化客户端

        Args:
            base_url: Prometheus 基础地址（如 http://localhost:9091）
            timeout: 单次查询超时（秒）
            max_retries: 失败重试次数（指数退避：1s, 2s, 4s）
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    def check_reachable(self) -> bool:
        """检查 Prometheus 是否可达（GET /-/healthy）"""
        url = f"{self.base_url}/-/healthy"
        t0 = time.time()
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                reachable = resp.status == 200
                _emit_log(
                    "prometheus.reachable_check_success",
                    "info",
                    None,
                    duration_ms=round((time.time() - t0) * 1000, 2),
                    url=url,
                    status=resp.status,
                    reachable=reachable,
                )
                return reachable
        except (urllib.error.URLError, OSError) as e:
            _emit_log(
                "prometheus.reachable_check_failed",
                "warning",
                None,
                duration_ms=round((time.time() - t0) * 1000, 2),
                url=url,
                error=f"{type(e).__name__}: {e}",
            )
            return False

    def query_range(
        self,
        promql: str,
        start: float,
        end: float,
        step: str,
        trace_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """执行 PromQL range query

        Args:
            promql: PromQL 查询表达式
            start: 起始时间（Unix 秒）
            end: 结束时间（Unix 秒）
            step: 步长（如 "300s" 表示 5 分钟一个点）
            trace_id: 追踪 ID

        Returns:
            数据点列表：[{"timestamp": float, "value": float}, ...]

        Raises:
            TrendReportError: 查询失败或返回错误
        """
        trace_id = trace_id or _trace_id()
        t0 = time.time()
        url = f"{self.base_url}/api/v1/query_range"
        params = {
            "query": promql,
            "start": str(start),
            "end": str(end),
            "step": step,
        }
        full_url = f"{url}?{urllib.parse.urlencode(params)}"

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(full_url, method="GET")
                req.add_header("Accept", "application/json")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if resp.status != 200:
                        raise TrendReportError(
                            TREND_ERR_QUERY_FAILED,
                            f"Prometheus 返回非 200 状态码: {resp.status}",
                            {"url": full_url, "status": resp.status, "promql": promql},
                        )
                    raw = resp.read().decode("utf-8")
                    data = json.loads(raw)

                if data.get("status") != "success":
                    raise TrendReportError(
                        TREND_ERR_QUERY_FAILED,
                        f"Prometheus 查询返回错误状态: {data.get('status')}",
                        {
                            "url": full_url,
                            "promql": promql,
                            "error_type": data.get("errorType"),
                            "error": data.get("error"),
                        },
                    )

                result = data.get("data", {}).get("result", [])
                # query_range 返回 matrix：[{metric: {...}, values: [[ts, "val"], ...]}]
                # 这里取第一个 series（visibility 指标通常只有一组标签）
                if not result:
                    _emit_log(
                        "prometheus.query_empty",
                        "info",
                        trace_id,
                        duration_ms=round((time.time() - t0) * 1000, 2),
                        promql=promql,
                        url=full_url,
                        attempt=attempt + 1,
                    )
                    return []

                series = result[0]
                values: List[Dict[str, Any]] = []
                for ts_str, val_str in series.get("values", []):
                    try:
                        values.append({
                            "timestamp": float(ts_str),
                            "value": float(val_str),
                        })
                    except (ValueError, TypeError) as e:
                        _emit_log(
                            "prometheus.value_parse_skipped",
                            "warning",
                            trace_id,
                            duration_ms=0,
                            ts=ts_str,
                            val=val_str,
                            error=str(e),
                        )

                _emit_log(
                    "prometheus.query_success",
                    "info",
                    trace_id,
                    duration_ms=round((time.time() - t0) * 1000, 2),
                    promql=promql,
                    points=len(values),
                    attempt=attempt + 1,
                )
                return values

            except urllib.error.URLError as e:
                last_error = e
                _emit_log(
                    "prometheus.query_retry",
                    "warning",
                    trace_id,
                    duration_ms=round((time.time() - t0) * 1000, 2),
                    promql=promql,
                    attempt=attempt + 1,
                    max_retries=self.max_retries + 1,
                    error=f"{type(e).__name__}: {e}",
                )
                if attempt < self.max_retries:
                    backoff = 2 ** attempt  # 1s, 2s, 4s
                    time.sleep(backoff)
            except (OSError, json.JSONDecodeError) as e:
                last_error = e
                _emit_log(
                    "prometheus.query_retry",
                    "warning",
                    trace_id,
                    duration_ms=round((time.time() - t0) * 1000, 2),
                    promql=promql,
                    attempt=attempt + 1,
                    max_retries=self.max_retries + 1,
                    error=f"{type(e).__name__}: {e}",
                )
                if attempt < self.max_retries:
                    backoff = 2 ** attempt
                    time.sleep(backoff)

        # 所有重试均失败
        raise TrendReportError(
            TREND_ERR_QUERY_FAILED,
            f"Prometheus 查询失败（已重试 {self.max_retries + 1} 次）: {last_error}",
            {"url": full_url, "promql": promql, "last_error": str(last_error)},
        )


# ═══════════════════════════════════════════════════════════════
#  报告生成器
# ═══════════════════════════════════════════════════════════════

class TrendReportGenerator:
    """趋势报告生成器"""

    # 周期配置：时长 + step + 文件名后缀
    PERIOD_CONFIG = {
        "weekly": {
            "days": 7,
            "step": "1h",   # 1 小时一个点，7 天共 168 个点
            "suffix": "weekly",
            "label": "周报",
        },
        "monthly": {
            "days": 30,
            "step": "6h",   # 6 小时一个点，30 天共 120 个点
            "suffix": "monthly",
            "label": "月报",
        },
    }

    def __init__(self, prometheus_url: str, timeout: int = 30, max_workers: int = 5) -> None:
        self.client = PrometheusClient(prometheus_url, timeout=timeout)
        self.max_workers = max_workers

    def generate(
        self,
        period: str,
        trace_id: Optional[str] = None,
    ) -> TrendReport:
        """生成趋势报告

        Args:
            period: 周期（weekly / monthly）
            trace_id: 追踪 ID

        Returns:
            TrendReport 对象

        Raises:
            TrendReportError: 周期无效或 Prometheus 不可达
        """
        trace_id = trace_id or _trace_id()
        t0 = time.time()

        if period not in self.PERIOD_CONFIG:
            raise TrendReportError(
                TREND_ERR_INVALID_PERIOD,
                f"无效的周期参数: {period}（支持: weekly / monthly）",
                {"period": period, "supported": list(self.PERIOD_CONFIG.keys())},
            )

        config = self.PERIOD_CONFIG[period]
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=config["days"])
        start_ts = start_dt.timestamp()
        end_ts = end_dt.timestamp()

        _emit_log(
            "generate.start",
            "info",
            trace_id,
            period=period,
            start=start_dt.isoformat(),
            end=end_dt.isoformat(),
            step=config["step"],
            queries=len(TREND_QUERIES),
        )

        # 健康检查
        if not self.client.check_reachable():
            raise TrendReportError(
                TREND_ERR_PROMETHEUS_UNREACHABLE,
                f"Prometheus 不可达: {self.client.base_url}",
                {"url": self.client.base_url},
            )

        # 并行查询所有指标
        series_list: List[MetricSeries] = []
        errors: List[str] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_query = {
                executor.submit(
                    self._query_single_metric,
                    q,
                    start_ts,
                    end_ts,
                    config["step"],
                    config["days"],
                    trace_id,
                ): q
                for q in TREND_QUERIES
            }
            for future in as_completed(future_to_query):
                query_def = future_to_query[future]
                try:
                    series = future.result()
                    series_list.append(series)
                except TrendReportError as e:
                    # 单个指标失败不阻塞整体报告，记录为降级
                    err_msg = f"{query_def['name']}: {e.message}"
                    errors.append(err_msg)
                    series_list.append(MetricSeries(
                        name=query_def["name"],
                        description=query_def["description"],
                        layer=query_def["layer"],
                        unit=query_def["unit"],
                        status="failed",
                        error=e.message,
                    ))
                    _emit_log(
                        "generate.metric_failed",
                        "warning",
                        trace_id,
                        metric=query_def["name"],
                        error=e.message,
                        code=e.code,
                    )

        # 总体状态：如果有任何指标失败则为 degraded，否则 pass
        failed_count = sum(1 for s in series_list if s.status == "failed")
        overall_status = "pass" if failed_count == 0 else "degraded" if failed_count < len(series_list) else "fail"

        duration_ms = round((time.time() - t0) * 1000, 2)
        report = TrendReport(
            trace_id=trace_id,
            period=period,
            start_time=start_dt.isoformat(),
            end_time=end_dt.isoformat(),
            generated_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=duration_ms,
            series=series_list,
            prometheus_url=self.client.base_url,
            overall_status=overall_status,
            errors=errors,
        )

        _emit_log(
            "generate.complete",
            "info",
            trace_id,
            duration_ms=duration_ms,
            overall_status=overall_status,
            series_count=len(series_list),
            failed_count=failed_count,
        )
        return report

    def _query_single_metric(
        self,
        query_def: Dict[str, str],
        start: float,
        end: float,
        step: str,
        days: int,
        trace_id: str,
    ) -> MetricSeries:
        """查询单个指标并构造 MetricSeries"""
        t0 = time.time()
        # 替换 PromQL 中的 {__range} 占位符为实际时长（如 7d / 30d）
        promql = query_def["promql"].replace("{__range}", f"{days}d")
        _emit_log(
            "generate.metric_query_start",
            "debug",
            trace_id,
            metric=query_def["name"],
            layer=query_def["layer"],
            promql=promql,
            step=step,
            days=days,
        )
        values = self.client.query_range(promql, start, end, step, trace_id=trace_id)

        if not values:
            return MetricSeries(
                name=query_def["name"],
                description=query_def["description"],
                layer=query_def["layer"],
                unit=query_def["unit"],
                status="empty",
                error="Prometheus 返回空数据（指标可能尚未采集）",
            )

        return MetricSeries(
            name=query_def["name"],
            description=query_def["description"],
            layer=query_def["layer"],
            unit=query_def["unit"],
            timestamps=[v["timestamp"] for v in values],
            values=[v["value"] for v in values],
            status="success",
        )


# ═══════════════════════════════════════════════════════════════
#  报告渲染器
# ═══════════════════════════════════════════════════════════════

class TrendReportRenderer:
    """趋势报告渲染器（Markdown + HTML 双格式）"""

    # 层级中文名映射
    LAYER_NAMES = {
        "overall": "总体状态",
        "runtime": "运行时可见",
        "verification": "验证过程可见",
        "business": "业务价值可见",
        "architecture": "架构影响可见",
    }

    # 趋势图标
    TREND_ICONS = {
        "up": "📈",
        "down": "📉",
        "flat": "➖",
        "unknown": "❓",
    }

    def __init__(self, report: TrendReport) -> None:
        self.report = report

    def render_markdown(self) -> str:
        """渲染 Markdown 报告"""
        lines: List[str] = []
        period_label = TrendReportGenerator.PERIOD_CONFIG[self.report.period]["label"]

        # 头部
        lines.append(f"# 四层可见性趋势{period_label}")
        lines.append("")
        lines.append(f"- **生成时间**：{self.report.generated_at}")
        lines.append(f"- **Trace ID**：`{self.report.trace_id}`")
        lines.append(f"- **报告周期**：{self.report.start_time} ~ {self.report.end_time}")
        lines.append(f"- **数据源**：{self.report.prometheus_url}")
        lines.append(f"- **生成耗时**：{self.report.duration_ms:.2f} ms")
        lines.append(f"- **总体状态**：{self._status_badge(self.report.overall_status)}")
        if self.report.errors:
            lines.append(f"- **降级指标**：{len(self.report.errors)} 项")
        lines.append("")

        # 概览统计
        lines.append("## 概览统计")
        lines.append("")
        lines.append("| 指标 | 当前值 | 周期初 | 趋势 | 变化 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for series in self.report.series:
            if series.status != "success":
                lines.append(
                    f"| `{series.name}` | — | — | {self.TREND_ICONS['unknown']} | 数据缺失 |"
                )
                continue
            trend_icon, trend_text = self._trend_summary(series)
            lines.append(
                f"| `{series.name}` | {self._format_value(series.latest_value, series.unit)} | "
                f"{self._format_value(series.first_value, series.unit)} | {trend_icon} | {trend_text} |"
            )
        lines.append("")

        # 按层分组的详情
        layers_order = ["overall", "runtime", "verification", "business", "architecture"]
        for layer in layers_order:
            layer_series = [s for s in self.report.series if s.layer == layer]
            if not layer_series:
                continue
            layer_name = self.LAYER_NAMES.get(layer, layer)
            lines.append(f"## {layer_name}")
            lines.append("")
            for series in layer_series:
                lines.append(f"### {series.description}")
                lines.append("")
                if series.status != "success":
                    lines.append(f"> ⚠️ **数据缺失**：{series.error or series.status}")
                    lines.append("")
                    continue
                lines.append(f"- **指标名**：`{series.name}`")
                lines.append(f"- **数据点数**：{series.point_count}")
                lines.append(f"- **当前值**：{self._format_value(series.latest_value, series.unit)}")
                lines.append(f"- **周期初值**：{self._format_value(series.first_value, series.unit)}")
                trend_icon, trend_text = self._trend_summary(series)
                lines.append(f"- **趋势**：{trend_icon} {trend_text}")
                if series.trend_percent is not None:
                    lines.append(f"- **变化百分比**：{series.trend_percent:+.2f}%")
                lines.append("")

        # 降级指标清单
        if self.report.errors:
            lines.append("## 降级指标清单")
            lines.append("")
            for err in self.report.errors:
                lines.append(f"- ❌ {err}")
            lines.append("")

        # 健康检查
        lines.append("## 健康检查")
        lines.append("")
        lines.append("| 检查项 | 状态 |")
        lines.append("| --- | --- |")
        lines.append(f"| Prometheus 可达性 | ✅ 已连接 |")
        lines.append(f"| 查询成功率 | {len([s for s in self.report.series if s.status == 'success'])}/{len(self.report.series)} |")
        lines.append(f"| 报告生成状态 | {self._status_badge(self.report.overall_status)} |")
        lines.append("")

        lines.append("---")
        lines.append(f"_由 `scripts/generate_visibility_trend.py` 自动生成_")
        return "\n".join(lines)

    def render_html(self) -> str:
        """渲染 HTML 报告（内嵌 SVG 趋势图）"""
        period_label = TrendReportGenerator.PERIOD_CONFIG[self.report.period]["label"]
        html_parts: List[str] = []
        html_parts.append("<!DOCTYPE html>")
        html_parts.append('<html lang="zh-CN">')
        html_parts.append('<head>')
        html_parts.append('<meta charset="UTF-8">')
        html_parts.append(f'<title>四层可见性趋势{period_label}</title>')
        html_parts.append('<style>')
        html_parts.append(self._css_styles())
        html_parts.append('</style>')
        html_parts.append('</head>')
        html_parts.append('<body>')
        html_parts.append(f'<h1>四层可见性趋势{period_label}</h1>')
        html_parts.append('<div class="meta">')
        html_parts.append(f'<span>生成时间：{self.report.generated_at}</span>')
        html_parts.append(f'<span>Trace ID：<code>{self.report.trace_id}</code></span>')
        html_parts.append(f'<span>周期：{self.report.start_time} ~ {self.report.end_time}</span>')
        html_parts.append(f'<span>数据源：{self.report.prometheus_url}</span>')
        html_parts.append(f'<span>状态：{self._status_badge(self.report.overall_status)}</span>')
        html_parts.append('</div>')

        # 概览表格
        html_parts.append('<h2>概览统计</h2>')
        html_parts.append('<table class="overview">')
        html_parts.append('<thead><tr><th>指标</th><th>当前值</th><th>周期初</th><th>趋势</th><th>变化</th></tr></thead>')
        html_parts.append('<tbody>')
        for series in self.report.series:
            if series.status != "success":
                html_parts.append(
                    f'<tr><td><code>{series.name}</code></td><td>—</td><td>—</td>'
                    f'<td>{self.TREND_ICONS["unknown"]}</td><td>数据缺失</td></tr>'
                )
                continue
            trend_icon, trend_text = self._trend_summary(series)
            html_parts.append(
                f'<tr><td><code>{series.name}</code></td>'
                f'<td>{self._format_value(series.latest_value, series.unit)}</td>'
                f'<td>{self._format_value(series.first_value, series.unit)}</td>'
                f'<td>{trend_icon}</td><td>{trend_text}</td></tr>'
            )
        html_parts.append('</tbody></table>')

        # 各层详情（含 SVG 趋势图）
        layers_order = ["overall", "runtime", "verification", "business", "architecture"]
        for layer in layers_order:
            layer_series = [s for s in self.report.series if s.layer == layer]
            if not layer_series:
                continue
            layer_name = self.LAYER_NAMES.get(layer, layer)
            html_parts.append(f'<h2>{layer_name}</h2>')
            for series in layer_series:
                html_parts.append(f'<h3>{series.description}</h3>')
                if series.status != "success":
                    html_parts.append(f'<div class="warning">⚠️ 数据缺失：{series.error or series.status}</div>')
                    continue
                # 渲染 SVG 折线图
                svg = self._render_svg_chart(series)
                html_parts.append(svg)

        # 健康检查
        html_parts.append('<h2>健康检查</h2>')
        html_parts.append('<table class="health">')
        html_parts.append('<tr><td>Prometheus 可达性</td><td>✅ 已连接</td></tr>')
        success_count = len([s for s in self.report.series if s.status == "success"])
        html_parts.append(f'<tr><td>查询成功率</td><td>{success_count}/{len(self.report.series)}</td></tr>')
        html_parts.append(f'<tr><td>报告生成状态</td><td>{self._status_badge(self.report.overall_status)}</td></tr>')
        html_parts.append('</table>')

        html_parts.append('<footer>')
        html_parts.append('<p>由 <code>scripts/generate_visibility_trend.py</code> 自动生成</p>')
        html_parts.append('</footer>')
        html_parts.append('</body></html>')
        return "\n".join(html_parts)

    def _trend_summary(self, series: MetricSeries) -> Tuple[str, str]:
        """生成趋势摘要：返回 (icon, text)"""
        delta = series.trend_delta
        if delta is None:
            return self.TREND_ICONS["unknown"], "数据不足"
        # 对于违规数等逆向指标，delta<0 是改善
        is_inverse = series.name in ("architecture_rule_violations", "threshold_violations")
        threshold = 0.01  # 1% 变化阈值
        if abs(delta) < threshold:
            return self.TREND_ICONS["flat"], "持平"
        if is_inverse:
            if delta < 0:
                return self.TREND_ICONS["down"], f"改善 {abs(delta):.2f}{series.unit}"
            return self.TREND_ICONS["up"], f"恶化 +{delta:.2f}{series.unit}"
        if delta > 0:
            return self.TREND_ICONS["up"], f"提升 +{delta:.2f}{series.unit}"
        return self.TREND_ICONS["down"], f"下降 {delta:.2f}{series.unit}"

    def _format_value(self, value: Optional[float], unit: str) -> str:
        """格式化数值显示"""
        if value is None:
            return "—"
        if unit == "%":
            return f"{value:.1f}%"
        if unit == "":
            return f"{value:.0f}"
        return f"{value:.2f} {unit}".rstrip()

    def _status_badge(self, status: str) -> str:
        """生成状态徽章"""
        badges = {
            "pass": "✅ 通过",
            "fail": "❌ 失败",
            "degraded": "⚠️ 降级",
        }
        return badges.get(status, status)

    def _render_svg_chart(self, series: MetricSeries) -> str:
        """渲染 SVG 折线图

        使用纯 SVG（无 JS 依赖），自适应宽度。
        """
        if not series.values:
            return '<div class="warning">无数据可绘制</div>'

        width = 800
        height = 240
        padding = 50
        chart_w = width - padding * 2
        chart_h = height - padding * 2

        # 计算坐标范围
        values = series.values
        ts = series.timestamps
        min_val = min(values)
        max_val = max(values)
        if min_val == max_val:
            # 避免平线占满整个图，添加 1 的余量
            max_val = min_val + 1
        min_ts = min(ts)
        max_ts = max(ts)
        if min_ts == max_ts:
            max_ts = min_ts + 1

        # 坐标变换函数
        def x(t: float) -> float:
            return padding + (t - min_ts) / (max_ts - min_ts) * chart_w

        def y(v: float) -> float:
            return padding + chart_h - (v - min_val) / (max_val - min_val) * chart_h

        # 构造折线 path
        points = [(x(t), y(v)) for t, v in zip(ts, values)]
        path_d = " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
        path_d = f"M {path_d.replace(' ', ' L ')}"

        # 构造坐标轴
        svg_parts = [
            f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" class="chart">',
            f'<rect width="{width}" height="{height}" fill="#fafafa" stroke="#ddd"/>',
            # 标题
            f'<text x="{padding}" y="20" font-size="12" fill="#666">{series.name} ({series.unit})</text>',
            # Y 轴
            f'<line x1="{padding}" y1="{padding}" x2="{padding}" y2="{padding + chart_h}" stroke="#999"/>',
            f'<text x="5" y="{padding + 10}" font-size="10" fill="#666">{max_val:.1f}</text>',
            f'<text x="5" y="{padding + chart_h}" font-size="10" fill="#666">{min_val:.1f}</text>',
            # X 轴
            f'<line x1="{padding}" y1="{padding + chart_h}" x2="{padding + chart_w}" y2="{padding + chart_h}" stroke="#999"/>',
            f'<text x="{padding}" y="{height - 10}" font-size="10" fill="#666">{datetime.fromtimestamp(min_ts).strftime("%m-%d %H:%M")}</text>',
            f'<text x="{padding + chart_w - 80}" y="{height - 10}" font-size="10" fill="#666">{datetime.fromtimestamp(max_ts).strftime("%m-%d %H:%M")}</text>',
            # 折线
            f'<path d="{path_d}" fill="none" stroke="#3b82f6" stroke-width="2"/>',
            # 数据点（仅当点数较少时显示）
        ]
        if len(points) <= 50:
            for px, py in points:
                svg_parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2" fill="#3b82f6"/>')
        svg_parts.append('</svg>')
        return "\n".join(svg_parts)

    def _css_styles(self) -> str:
        """内嵌 CSS 样式"""
        return """
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 20px; color: #333; }
        h1 { color: #1e40af; border-bottom: 2px solid #1e40af; padding-bottom: 10px; }
        h2 { color: #1e3a8a; margin-top: 30px; }
        h3 { color: #374151; margin-top: 20px; }
        .meta { display: flex; flex-wrap: wrap; gap: 15px; background: #f3f4f6; padding: 12px; border-radius: 6px; margin: 15px 0; font-size: 13px; }
        .meta code { background: #e5e7eb; padding: 2px 6px; border-radius: 3px; }
        table { border-collapse: collapse; width: 100%; margin: 15px 0; }
        th, td { border: 1px solid #d1d5db; padding: 8px 12px; text-align: left; }
        th { background: #f9fafb; font-weight: 600; }
        table.overview tbody tr:hover { background: #f9fafb; }
        table.health td:first-child { font-weight: 500; width: 200px; }
        .chart { max-width: 100%; height: auto; display: block; margin: 15px 0; border-radius: 4px; }
        .warning { background: #fef3c7; border-left: 4px solid #f59e0b; padding: 10px 15px; margin: 10px 0; border-radius: 4px; }
        footer { margin-top: 40px; padding-top: 15px; border-top: 1px solid #e5e7eb; color: #6b7280; font-size: 12px; }
        code { background: #f3f4f6; padding: 2px 6px; border-radius: 3px; font-family: "SFMono-Regular", Consolas, monospace; font-size: 13px; }
        """


# ═══════════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════════

def main(argv=None) -> int:
    """CLI 入口：0 成功 / 1 部分降级 / 2 失败"""
    parser = argparse.ArgumentParser(
        description="四层可见性趋势报告生成器（周报/月报）"
    )
    parser.add_argument(
        "--prometheus-url",
        default="http://localhost:9091",
        help="Prometheus 服务地址（默认 http://localhost:9091，生产环境部署端口）",
    )
    parser.add_argument(
        "--period",
        choices=["weekly", "monthly"],
        default="weekly",
        help="报告周期（weekly=7天, monthly=30天，默认 weekly）",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="报告输出目录（默认 docs/observability/trends/）",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Prometheus 查询超时（秒，默认 30）",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=5,
        help="并行查询线程数（默认 5）",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "html", "both"],
        default="both",
        help="输出格式（markdown / html / both，默认 both）",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="非交互模式（CI 使用，失败时输出降级报告而非抛异常）",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    trace_id = _trace_id()
    t0 = time.time()

    _emit_log(
        "main.start",
        "info",
        trace_id,
        prometheus_url=args.prometheus_url,
        period=args.period,
        format=args.format,
        non_interactive=args.non_interactive,
    )

    # 输出目录
    if args.output:
        output_dir = Path(args.output)
    else:
        output_dir = PROJECT_ROOT / "docs" / "observability" / "trends"
    output_dir.mkdir(parents=True, exist_ok=True)
    _emit_log(
        "main.output_dir_ready",
        "info",
        trace_id,
        output_dir=str(output_dir),
    )

    try:
        generator = TrendReportGenerator(
            prometheus_url=args.prometheus_url,
            timeout=args.timeout,
            max_workers=args.max_workers,
        )
        _emit_log(
            "main.generator_initialized",
            "info",
            trace_id,
            prometheus_url=args.prometheus_url,
            timeout=args.timeout,
            max_workers=args.max_workers,
        )
        report = generator.generate(args.period, trace_id=trace_id)

        renderer = TrendReportRenderer(report)

        # 文件名：visibility_trend_weekly_20260628.md
        date_str = datetime.now().strftime("%Y%m%d")
        period_suffix = TrendReportGenerator.PERIOD_CONFIG[args.period]["suffix"]
        base_name = f"visibility_trend_{period_suffix}_{date_str}"

        written_files: List[str] = []

        if args.format in ("markdown", "both"):
            md_path = output_dir / f"{base_name}.md"
            _emit_log("main.render_markdown_start", "debug", trace_id, path=str(md_path))
            md_content = renderer.render_markdown()
            try:
                md_path.write_text(md_content, encoding="utf-8")
            except OSError as e:
                raise TrendReportError(
                    TREND_ERR_OUTPUT_WRITE_FAILED,
                    f"Markdown 报告写入失败: {e}",
                    {"path": str(md_path), "error": str(e)},
                )
            written_files.append(str(md_path))
            _emit_log(
                "main.write_markdown",
                "info",
                trace_id,
                duration_ms=round((time.time() - t0) * 1000, 2),
                path=str(md_path),
                size=len(md_content),
            )

        if args.format in ("html", "both"):
            html_path = output_dir / f"{base_name}.html"
            _emit_log("main.render_html_start", "debug", trace_id, path=str(html_path))
            html_content = renderer.render_html()
            try:
                html_path.write_text(html_content, encoding="utf-8")
            except OSError as e:
                raise TrendReportError(
                    TREND_ERR_OUTPUT_WRITE_FAILED,
                    f"HTML 报告写入失败: {e}",
                    {"path": str(html_path), "error": str(e)},
                )
            written_files.append(str(html_path))
            _emit_log(
                "main.write_html",
                "info",
                trace_id,
                duration_ms=round((time.time() - t0) * 1000, 2),
                path=str(html_path),
                size=len(html_content),
            )

        # 同时输出 JSON 元数据（便于后续工具消费）
        json_path = output_dir / f"{base_name}.json"
        json_data = {
            "trace_id": report.trace_id,
            "period": report.period,
            "start_time": report.start_time,
            "end_time": report.end_time,
            "generated_at": report.generated_at,
            "duration_ms": report.duration_ms,
            "overall_status": report.overall_status,
            "prometheus_url": report.prometheus_url,
            "errors": report.errors,
            "series": [
                {
                    "name": s.name,
                    "description": s.description,
                    "layer": s.layer,
                    "unit": s.unit,
                    "status": s.status,
                    "point_count": s.point_count,
                    "latest_value": s.latest_value,
                    "first_value": s.first_value,
                    "trend_delta": s.trend_delta,
                    "trend_percent": s.trend_percent,
                    "error": s.error,
                }
                for s in report.series
            ],
            "files": written_files,
        }
        json_path.write_text(
            json.dumps(json_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written_files.append(str(json_path))
        _emit_log(
            "main.write_json_metadata",
            "info",
            trace_id,
            path=str(json_path),
            series_count=len(json_data["series"]),
        )

        _emit_log(
            "main.complete",
            "info",
            trace_id,
            duration_ms=round((time.time() - t0) * 1000, 2),
            overall_status=report.overall_status,
            files=written_files,
        )

        print(json.dumps({
            "ok": True,
            "overall_status": report.overall_status,
            "files": written_files,
            "series_count": len(report.series),
            "errors": report.errors,
        }, ensure_ascii=False, indent=2))

        # 退出码：0=成功，1=降级（部分指标失败）
        exit_code = 0 if report.overall_status == "pass" else 1
        _emit_log(
            "main.exit_decision",
            "info",
            trace_id,
            duration_ms=round((time.time() - t0) * 1000, 2),
            exit_code=exit_code,
            overall_status=report.overall_status,
            reason="pass→0 / degraded→1",
        )
        return exit_code

    except TrendReportError as e:
        _emit_log(
            "main.failed",
            "error",
            trace_id,
            duration_ms=round((time.time() - t0) * 1000, 2),
            error_code=e.code,
            error_message=e.message,
            error_details=e.details,
        )
        # 非交互模式：输出降级报告
        if args.non_interactive:
            degraded_path = output_dir / f"visibility_trend_{args.period}_degraded_{datetime.now().strftime('%Y%m%d')}.md"
            degraded_content = _degraded_report(e, args, trace_id)
            try:
                degraded_path.write_text(degraded_content, encoding="utf-8")
            except OSError as write_err:
                _emit_log(
                    "main.degraded_report_write_failed",
                    "error",
                    trace_id,
                    path=str(degraded_path),
                    error=f"{type(write_err).__name__}: {write_err}",
                )
            else:
                _emit_log(
                    "main.degraded_report_written",
                    "info",
                    trace_id,
                    path=str(degraded_path),
                    size=len(degraded_content),
                )
            print(json.dumps({
                "ok": False,
                "error_code": e.code,
                "error": e.message,
                "degraded_report": str(degraded_path),
            }, ensure_ascii=False, indent=2))
            _emit_log(
                "main.exit_decision",
                "warning",
                trace_id,
                duration_ms=round((time.time() - t0) * 1000, 2),
                exit_code=2,
                error_code=e.code,
                reason="TrendReportError + non-interactive → 降级报告",
            )
            return 2
        # 交互模式：抛异常
        print(json.dumps({
            "ok": False,
            "error_code": e.code,
            "error": e.message,
            "details": e.details,
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        _emit_log(
            "main.exit_decision",
            "warning",
            trace_id,
            duration_ms=round((time.time() - t0) * 1000, 2),
            exit_code=2,
            error_code=e.code,
            reason="TrendReportError + interactive → stderr",
        )
        return 2

    except Exception as e:
        _emit_log(
            "main.unexpected_error",
            "error",
            trace_id,
            duration_ms=round((time.time() - t0) * 1000, 2),
            error=f"{type(e).__name__}: {e}",
            stack=traceback.format_exc(),
        )
        print(json.dumps({
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "trace_id": trace_id,
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        _emit_log(
            "main.exit_decision",
            "error",
            trace_id,
            duration_ms=round((time.time() - t0) * 1000, 2),
            exit_code=2,
            error_type=type(e).__name__,
            reason="unexpected exception → 2",
        )
        return 2


def _degraded_report(error: TrendReportError, args: argparse.Namespace, trace_id: str) -> str:
    """生成降级报告（Markdown 格式）"""
    period_label = TrendReportGenerator.PERIOD_CONFIG[args.period]["label"]
    return f"""# 四层可见性趋势{period_label}（降级）

- **生成时间**：{datetime.now(timezone.utc).isoformat()}
- **Trace ID**：`{trace_id}`
- **状态**：❌ 报告生成失败

## 错误信息

- **错误码**：{error.code}
- **错误消息**：{error.message}
- **错误详情**：{json.dumps(error.details, ensure_ascii=False, indent=2)}

## 错误堆栈

```
{traceback.format_exc()}
```

## 处置建议

1. 检查 Prometheus 服务是否运行：`curl {args.prometheus_url}/-/healthy`
2. 确认 visibility-exporter 容器已启动并暴露 /metrics
3. 检查 Prometheus 抓取配置（deploy/monitoring/prometheus/prometheus.yml）
4. 如问题持续，请查看 docs/deploy/observability_deploy_guide.md 排错章节

---

_降级报告：趋势报告生成过程中发生异常_
"""


if __name__ == "__main__":
    sys.exit(main())
