#!/usr/bin/env python
"""surge_metrics_template.py — 指标激增模拟标准模板（可入库，可复用）。

用途: 验证 Grafana 告警规则在指标激增时能否及时触发 Firing。
验证依据: docs/monitoring/alert-verification.md
模板文档: docs/monitoring/alert-surge-test-template.md

使用方法:
    # 1. 设置环境变量
    $env:PYTHONPATH = "<项目根目录>"
    $env:PYTHONIOENCODING = "utf-8"

    # 2. 后台启动激增脚本
    python -u scripts/surge_metrics_template.py &

    # 3. 等待 Firing（critical for=30s + 评估间隔 30s = ~60s）
    sleep 90

    # 4. 验证 Grafana Firing
    docker logs yunshu-grafana --since 3m 2>&1 | grep "Sending alerts"

    # 5. 验证 Prometheus 指标
    curl "http://localhost:9090/api/v1/query?query=increase(yunshu_skill_hallucination_total[5m])"

    # 6. 停止激增脚本
    kill %1

定制化参数（通过环境变量或直接修改）:
    MOCK_METRICS_PORT: /metrics 端点端口（默认 5678）
    SURGE_BURST_COUNT: burst 数量（默认 25，需超过 critical 阈值 20）
    SURGE_SUSTAIN_INTERVAL: 维持间隔秒数（默认 5，与 Prometheus scrape interval 对齐）
    SURGE_SKILL_ID: 激增模拟的 skill_id（默认 surge-template-test）
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
from agent.skills_mgmt.observability import emit_eval_score_metric
from agent.monitoring.business_metrics import get_business_metrics_collector

# ── 可配置参数（环境变量覆盖）──
PORT = int(os.environ.get("MOCK_METRICS_PORT", "5678"))
BURST_COUNT = int(os.environ.get("SURGE_BURST_COUNT", "25"))
SUSTAIN_INTERVAL = int(os.environ.get("SURGE_SUSTAIN_INTERVAL", "5"))
SKILL_ID = os.environ.get("SURGE_SKILL_ID", "surge-template-test")


def _emit_hallucination(skill_id: str, score: float = 0.15,
                        trace_id: str | None = None) -> None:
    """发射一条幻觉指标。

    [不易] 使用 emit_eval_score_metric（内部走 get_business_metrics_collector() 单例），
    确保指标写入 /metrics 端点导出的同一实例。
    依赖 observability.py 的单例化修复（2026-07-23）。
    """
    emit_eval_score_metric(skill_id, {
        "task_success": False,
        "instruction_followed": False,
        "hallucination_detected": True,
        "score": score,
    }, trace_id=trace_id or f"surge-{uuid.uuid4().hex[:8]}")


def _emit_burst() -> None:
    """Burst: 启动时立即发射 BURST_COUNT 条幻觉，使 increase[5m] 立即超阈值。"""
    print(f"[surge] burst {BURST_COUNT} @ {time.strftime('%H:%M:%S')}",
          file=sys.stderr)
    for i in range(BURST_COUNT):
        _emit_hallucination(SKILL_ID, 0.15, f"surge-burst-{i:03d}")


def _sustain_emitter() -> None:
    """Sustain: 每 SUSTAIN_INTERVAL 秒发射 1 条，维持 increase[5m] 持续超阈值。"""
    counter = 0
    while True:
        time.sleep(SUSTAIN_INTERVAL)
        try:
            counter += 1
            _emit_hallucination(SKILL_ID, 0.18, f"surge-s-{counter:04d}")
        except Exception as e:
            print(f"[surge] sustain fail: {e}", file=sys.stderr)


class _MetricsHandler(http.server.BaseHTTPRequestHandler):
    """Prometheus /metrics 端点处理器。

    [变易] 合并 prometheus_client 默认 registry + BusinessMetricsCollector 单例导出，
    确保所有指标（含 emit_metric 发射的）均可被 Prometheus 抓取。
    """

    def do_GET(self):
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return
        c = get_business_metrics_collector()
        parts = [generate_latest().decode("utf-8")]
        try:
            parts.append(c.export_prometheus())
        except Exception:
            pass
        body = "\n".join(parts).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPE_LATEST)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main() -> int:
    _emit_burst()
    t = threading.Thread(target=_sustain_emitter, daemon=True)
    t.start()
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", PORT), _MetricsHandler) as httpd:
        print(f"[surge] HTTP :{PORT} @ {time.strftime('%H:%M:%S')}",
              file=sys.stderr)
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
