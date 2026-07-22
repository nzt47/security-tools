#!/usr/bin/env python
"""mock_metrics_server.py — 轻量级 /metrics 端点，模拟技能质量指标供 Prometheus 抓取。

数据流:
  emit_eval_score_metric -> emit_metric -> inc_counter/observe_histogram
  -> BusinessMetricsCollector 单例 -> export_prometheus -> /metrics -> Prometheus

挂载: Prometheus yunshu job -> http://host.docker.internal:5678/metrics

发射策略（守不易：保持告警 Normal，不误触发）:
  - 启动时发射 4 条幻觉 + 1 条正常（建立基线，NoData -> Normal）
  - 后台每 90s 发射 1 条幻觉（5m 内约 3-4 次 < warning 阈值 5）
  - rate[5m] ≈ 0.011/s，趋势图有微弱斜率可见
"""
from __future__ import annotations
import http.server
import socketserver
import threading
import time
import sys
import os
import uuid

from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

# [不易] 必须在 import observability 之前确保 business_metrics 单例可用
from agent.skills_mgmt.observability import emit_eval_score_metric
from agent.monitoring.business_metrics import get_business_metrics_collector

PORT = int(os.environ.get("MOCK_METRICS_PORT", "5678"))


def _emit_hallucination(skill_id: str, score: float, trace_id: str | None = None) -> None:
    """发射一条幻觉指标（hallucination_detected=True）"""
    emit_eval_score_metric(
        skill_id,
        {
            "task_success": False,
            "instruction_followed": False,
            "hallucination_detected": True,
            "score": score,
        },
        trace_id=trace_id or f"mock-{uuid.uuid4().hex[:8]}",
    )


def _emit_normal(skill_id: str, score: float, trace_id: str | None = None) -> None:
    """发射一条正常指标（hallucination_detected=False）"""
    emit_eval_score_metric(
        skill_id,
        {
            "task_success": True,
            "instruction_followed": True,
            "hallucination_detected": False,
            "score": score,
        },
        trace_id=trace_id or f"mock-{uuid.uuid4().hex[:8]}",
    )


def _emit_initial_metrics() -> None:
    """启动时发射初始指标，建立基线"""
    print("[mock-metrics] 发射初始指标...", file=sys.stderr)
    _emit_normal("prom-verify-normal", 0.92, "prom-verify-001")
    _emit_hallucination("prom-verify-hallucination", 0.3, "prom-verify-002")
    _emit_hallucination("prom-verify-hallucination", 0.2, "prom-verify-hallu-0")
    _emit_hallucination("prom-verify-hallucination", 0.2, "prom-verify-hallu-1")
    _emit_hallucination("skill-code-review", 0.4, "mock-init-code-review")
    print("[mock-metrics] 初始指标发射完成（4 条幻觉 + 1 条正常）", file=sys.stderr)


def _periodic_emitter() -> None:
    """[变易] 后台线程：每 90s 发射 1 条幻觉指标，让 counter 缓慢增长。

    5 分钟内约 3-4 次幻觉 < warning 阈值 5，保持 Normal 状态。
    rate[5m] ≈ 1/90 ≈ 0.011/s，趋势图有微弱斜率可见。
    """
    while True:
        time.sleep(90)
        try:
            _emit_hallucination("prom-verify-hallucination", 0.25)
            print(f"[mock-metrics] 定期发射幻觉指标 @ {time.strftime('%H:%M:%S')}",
                  file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[mock-metrics] 定期发射失败: {e}", file=sys.stderr)


class MetricsHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return
        collector = get_business_metrics_collector()
        body_parts = [generate_latest().decode("utf-8")]
        try:
            body_parts.append(collector.export_prometheus())
        except Exception as e:  # noqa: BLE001
            print(f"[mock-metrics] export_prometheus 失败: {e}", file=sys.stderr)
        body = "\n".join(body_parts).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPE_LATEST)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002
        # 静默默认访问日志，只保留 stderr 调试输出
        pass


def main() -> int:
    _emit_initial_metrics()
    # 启动后台定期发射线程（daemon=True，主进程退出时自动结束）
    t = threading.Thread(target=_periodic_emitter, daemon=True)
    t.start()
    # 启动 HTTP server
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", PORT), MetricsHandler) as httpd:
        print(f"[mock-metrics] 启动 HTTP server on 0.0.0.0:{PORT}", file=sys.stderr)
        print(f"[mock-metrics] /metrics 端点合并 generate_latest() + export_prometheus()",
              file=sys.stderr)
        print(f"[mock-metrics] Prometheus scrape url: http://host.docker.internal:{PORT}/metrics",
              file=sys.stderr)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("[mock-metrics] 收到中断信号，退出", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
