"""轻量级 metrics server，用于端到端验证 Prometheus 抓取链路（修复实例隔离后版本）

【背景】app_server.py 启动需加载 SentenceTransformer 模型（耗时/网络依赖），
本脚本作为降级方案，验证修复后的完整链路：
    emit_eval_score_metric → emit_metric → get_business_metrics_collector() 全局单例
    → export_prometheus() → /metrics 端点 → Prometheus scrape → /api/v1/query

监听 0.0.0.0:5678 供 Docker 内 Prometheus 通过 host.docker.internal 抓取。
"""
from __future__ import annotations

import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

# 确保项目根在 sys.path（脚本在 scripts/ 下，需加父目录）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

# 导入 observability（已修复为用全局单例）+ business_metrics 的 export_prometheus
from agent.skills_mgmt.observability import emit_eval_score_metric
from agent.monitoring.business_metrics import get_business_metrics_collector

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)


def trigger_metrics() -> None:
    """触发指标发射（通过 observability 层，验证全局单例修复）"""
    # 场景1: 正常 eval_score → yunshu_skill_eval_score histogram
    emit_eval_score_metric(
        skill_id="prom-verify-normal",
        eval_score={
            "task_success": True,
            "instruction_followed": True,
            "hallucination_detected": False,
            "score": 0.92,
        },
        trace_id="prom-verify-001",
    )

    # 场景2: 幻觉 eval_score → yunshu_skill_eval_score histogram + yunshu_skill_hallucination_total counter
    emit_eval_score_metric(
        skill_id="prom-verify-hallucination",
        eval_score={
            "task_success": False,
            "instruction_followed": False,
            "hallucination_detected": True,
            "score": 0.3,
        },
        trace_id="prom-verify-002",
    )

    # 场景3: 多次幻觉，让 counter 累积到 4（触发 YunshuSkillHallucinationSpike 告警 ≥3）
    for i in range(3):
        emit_eval_score_metric(
            skill_id="prom-verify-hallucination",
            eval_score={
                "task_success": False,
                "instruction_followed": False,
                "hallucination_detected": True,
                "score": 0.2,
            },
            trace_id=f"prom-verify-hallu-{i}",
        )


class MetricsHandler(BaseHTTPRequestHandler):
    """HTTP handler: 暴露 /metrics + /health

    【不易】/metrics 端点合并两部分输出：
      1. prometheus_client 默认注册表（python_gc_* 等）— generate_latest()
      2. BusinessMetricsCollector 内部存储（yunshu_skill_* 等）— export_prometheus()
    与 app_server.py 的 /metrics 端点行为一致（routes_logging.py:926-954）。
    """

    def do_GET(self):  # noqa: N802
        if self.path == "/metrics":
            # 合并 prometheus_client 默认注册表 + BusinessMetricsCollector 内部存储
            body_parts = [generate_latest().decode("utf-8")]
            try:
                body_parts.append(get_business_metrics_collector().export_prometheus())
            except Exception as e:  # noqa: BLE001
                print(f"[mock-metrics] export_prometheus 失败: {e}", file=sys.stderr)
            body = "\n".join(body_parts).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/health", "/api/skills-mgmt/health"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","mock":true}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):  # noqa: A002
        pass  # 静默访问日志


def main() -> int:
    print("[mock-metrics] 触发指标发射（通过 observability.emit_eval_score_metric）...", file=sys.stderr)
    trigger_metrics()
    print("[mock-metrics] 启动 HTTP server on 0.0.0.0:5678", file=sys.stderr)
    print("[mock-metrics] /metrics 端点合并 generate_latest() + export_prometheus()", file=sys.stderr)
    print("[mock-metrics] Prometheus scrape url: http://host.docker.internal:5678/metrics", file=sys.stderr)

    server = HTTPServer(("0.0.0.0", 5678), MetricsHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[mock-metrics] 收到中断信号，退出", file=sys.stderr)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
