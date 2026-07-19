"""轻量级 metrics server，用于端到端验证 Prometheus 抓取链路

【背景】app_server.py 启动需加载 SentenceTransformer 模型（耗时/网络依赖），
本脚本作为降级方案，验证两条链路：
    1. observability 层: emit_eval_score_metric → [Observability:fill] 日志（验证逻辑层）
    2. prometheus 层: prometheus_client 注册 → /metrics 端点 → Prometheus scrape（验证物理层）

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

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

# 导入 observability，触发 BusinessMetricsCollector 初始化
from agent.skills_mgmt.observability import emit_eval_score_metric

# 【不易】直接用 prometheus_client 注册 yunshu_skill_* 指标到默认注册表
# 原因: BusinessMetricsCollector 用内部存储 + BUSINESS_METRICS_DEFINITIONS 白名单，
# emit_metric 触发的 yunshu_skill_eval_score / yunshu_skill_hallucination_total
# 不在定义表中，export_prometheus() 不会输出。
# 这里直接注册到 prometheus_client 默认注册表，让 generate_latest() 能输出，
# 验证 Prometheus scrape → query 端到端链路。
_SKILL_EVAL_SCORE = Histogram(
    "yunshu_skill_eval_score",
    "Skill evaluation score (0-1)",
    ["skill_id", "task_success"],
)
_SKILL_HALLUCINATION = Counter(
    "yunshu_skill_hallucination_total",
    "Total skill hallucination detections",
    ["skill_id"],
)

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)


def trigger_metrics() -> None:
    """触发指标发射（两条链路并行验证）"""
    # 链路1: observability 层 — emit_eval_score_metric → [Observability:fill] 日志
    # 这条链路的指标不会出现在 /metrics（因不在定义表），但日志会打印
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

    # 链路2: prometheus_client 直接注册 — 出现在 /metrics 端点供 Prometheus 抓取
    # 模拟 emit_eval_score_metric 的指标发射逻辑（相同 name/value/labels）
    _SKILL_EVAL_SCORE.labels(
        skill_id="prom-verify-normal", task_success="true"
    ).observe(0.92)
    _SKILL_EVAL_SCORE.labels(
        skill_id="prom-verify-hallucination", task_success="false"
    ).observe(0.3)
    # 4 次幻觉（触发 YunshuSkillHallucinationSpike 告警 ≥3）
    _SKILL_HALLUCINATION.labels(skill_id="prom-verify-hallucination").inc(4)


class MetricsHandler(BaseHTTPRequestHandler):
    """HTTP handler: 暴露 /metrics + /health"""

    def do_GET(self):  # noqa: N802
        if self.path == "/metrics":
            body = generate_latest()
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
    print("[mock-metrics] 触发指标发射...", file=sys.stderr)
    trigger_metrics()
    print("[mock-metrics] 启动 HTTP server on 0.0.0.0:5678", file=sys.stderr)
    print("[mock-metrics] /metrics 端点就绪，等待 Prometheus 抓取", file=sys.stderr)
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
