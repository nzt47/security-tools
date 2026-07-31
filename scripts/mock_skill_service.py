"""Mock 技能检索服务 — 本地压测指标验证

【不易】复用 agent.monitoring.prometheus 中的指标定义（不重新定义）:
    - skill_match_latency_ms (Histogram)
    - skill_match_count_total (Counter)
    - Yunshu_active_connections (Gauge, via PrometheusMetricsExporter)

【变易】模拟真实服务的 /match + /health + /metrics 端点
【简易】单文件自包含，ThreadingHTTPServer 支持并发

端口:
    - 8080: HTTP 服务（/match, /health）
    - 9091: prometheus_client /metrics 端点

运行:
    python scripts/mock_skill_service.py
"""
from __future__ import annotations

import json
import os
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

os.environ.setdefault("SKILLS_OFFLINE", "1")

# 导入 prometheus_client 原生指标（模块级定义）
from agent.monitoring.prometheus import (
    record_skill_match_latency,
    record_skill_match_count,
)

# 导入 PrometheusMetricsExporter 获取 active_connections Gauge
# 【不易】复用 prometheus.py 中的定义，不重复创建
try:
    from agent.monitoring.prometheus import PrometheusMetricsExporter
    _exporter = PrometheusMetricsExporter()
    _active_connections = _exporter.active_connections
    _set_active_connections = _exporter.set_active_connections
except Exception as e:
    print(f"[WARN] PrometheusMetricsExporter 初始化失败，降级为独立 Gauge: {e}")
    from prometheus_client import Gauge
    _active_connections = Gauge("Yunshu_active_connections", "Number of active connections")
    def _set_active_connections(count):
        _active_connections.set(count)

from prometheus_client import start_http_server

# ═══════════════════════════════════════════════════════════════════
#  活跃连接跟踪
# ═══════════════════════════════════════════════════════════════════

_conn_lock = Lock()
_active_count = 0


def _inc_connection():
    global _active_count
    with _conn_lock:
        _active_count += 1
        _set_active_connections(_active_count)


def _dec_connection():
    global _active_count
    with _conn_lock:
        _active_count = max(0, _active_count - 1)
        _set_active_connections(_active_count)


# 模拟技能库
MOCK_SKILLS = [
    {"skill_id": "pdf_parser", "name": "PDF解析", "description": "解析PDF文件并提取内容"},
    {"skill_id": "report_gen", "name": "报告生成", "description": "生成市场分析报告"},
    {"skill_id": "code_review", "name": "代码审查", "description": "审查代码质量"},
    {"skill_id": "translation", "name": "翻译", "description": "多语言翻译"},
    {"skill_id": "debug_helper", "name": "调试助手", "description": "调试运行时错误"},
]


class SkillHandler(BaseHTTPRequestHandler):
    """技能检索 HTTP Handler"""

    def log_message(self, format, *args):
        # 静默访问日志（压测时输出太多）
        pass

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "timestamp": time.time()})
        elif self.path == "/ready":
            self._send_json(200, {"status": "ready"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        _inc_connection()
        try:
            if self.path == "/match":
                self._handle_match()
            else:
                self._send_json(404, {"error": "not found"})
        finally:
            _dec_connection()

    def _handle_match(self):
        start = time.perf_counter()

        # 读取请求体
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"query": ""}

        query = payload.get("query", "")
        top_k = payload.get("top_k", 5)

        # 模拟检索延迟（5-35ms，模拟真实 RRF 融合延迟）
        # 5000 技能 ON 模式 P99≈42ms，这里模拟 p50≈15ms
        latency_ms = random.uniform(5, 35)
        time.sleep(latency_ms / 1000)

        elapsed = (time.perf_counter() - start) * 1000

        # 模拟检索结果
        matches = []
        for i in range(min(top_k, len(MOCK_SKILLS))):
            skill = MOCK_SKILLS[i]
            matches.append({
                "skill_id": skill["skill_id"],
                "name": skill["name"],
                "description": skill["description"],
                "score": round(0.9 - i * 0.15, 4),
            })

        # 【不易】记录 prometheus 指标（复用 prometheus.py 定义）
        method = "rrf"
        record_skill_match_latency("1", method, True, elapsed)
        record_skill_match_count("1", method, True)

        self._send_json(200, {
            "matches": matches,
            "match_count": len(matches),
            "elapsed_ms": round(elapsed, 2),
            "retrieval_method": method,
        })

    def _send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    # 启动 prometheus_client HTTP 服务器（/metrics 端点）
    start_http_server(9091)
    print("[OK] Prometheus metrics 端点: http://localhost:9091/metrics")

    # 初始化 active_connections
    _set_active_connections(0)

    # 启动 HTTP 服务
    server = ThreadingHTTPServer(("0.0.0.0", 8080), SkillHandler)
    print("[OK] Mock 技能检索服务: http://localhost:8080/match")
    print("[OK] 健康检查: http://localhost:8080/health")
    print("[OK] 按 Ctrl+C 停止服务")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] 服务已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
