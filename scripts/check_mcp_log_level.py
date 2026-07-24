#!/usr/bin/env python
"""MCP 日志级别健康检查 + Prometheus 指标导出器

【不易】不触发 agent/__init__.py 重型导入链(直接 sys.path 注入 agent 目录)
【变易】双模式: 默认 healthcheck(exit 0/1) | --serve Prometheus 指标导出
【简易】仅依赖 stdlib(http.server),无第三方包;30s 可读

使用方式:
  # 1. Docker healthcheck(默认模式,检测回退则 exit 1)
  python scripts/check_mcp_log_level.py

  # 2. Prometheus 指标导出(后台运行,暴露 /metrics)
  python scripts/check_mcp_log_level.py --serve --port 9102

  # 3. JSON 输出(调试用)
  python scripts/check_mcp_log_level.py --json

指标说明(/metrics):
  mcp_log_level_current         Gauge  当前生效级别数值(10/20/30/40)
  mcp_log_level_fallback        Gauge  是否发生回退(0=正常, 1=回退)
  mcp_log_level_configured_value Gauge  配置的原始值数值(无效值=0)

告警规则参考:
  - mcp_log_level_fallback == 1  → 日志级别异常回退
  - mcp_log_level_current == 0   → 级别未知(启动异常)
"""
from __future__ import annotations

import os
import sys
import json
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler

# ════════════════════════════════════════════════════════════════
#  直接导入 mcp_executor,绕过 agent/__init__.py 重型导入链
# ════════════════════════════════════════════════════════════════
# agent/__init__.py 导入 digital_life → sensor → watchdog 等重型依赖,
# 在轻量容器(python:3.11-slim)中会因缺少依赖而失败。
# 本脚本通过 sys.path 直接导入 mcp_executor 模块,不经过 agent 包。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AGENT_DIR = os.path.join(_PROJECT_ROOT, "agent")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

import mcp_executor  # noqa: E402 — 直接导入,非 agent.mcp_executor

# 级别名称 → 数值映射(Prometheus 需要 numeric)
_LEVEL_NUMERIC = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}


def get_status() -> dict:
    """获取日志级别状态(封装 mcp_executor.get_log_level_status)。"""
    return mcp_executor.get_log_level_status()


def render_prometheus_metrics(status: dict) -> str:
    """将状态渲染为 Prometheus text exposition 格式。"""
    level_name = status["level"]
    level_num = _LEVEL_NUMERIC.get(level_name, 0)
    fallback = 1 if status["fallback"] else 0
    configured = status["configured"]
    configured_num = _LEVEL_NUMERIC.get(configured, 0)

    original = status.get("original") or "none"

    lines = [
        "# HELP mcp_log_level_current MCP executor 当前生效日志级别数值(10=DEBUG,20=INFO,30=WARNING,40=ERROR)",
        "# TYPE mcp_log_level_current gauge",
        f'mcp_log_level_current{{level="{level_name}"}} {level_num}',
        "",
        "# HELP mcp_log_level_fallback MCP 日志级别是否发生无效值回退(0=正常,1=回退)",
        "# TYPE mcp_log_level_fallback gauge",
        f'mcp_log_level_fallback{{original="{original}",configured="{configured}"}} {fallback}',
        "",
        "# HELP mcp_log_level_configured_value MCP_LOG_LEVEL 环境变量配置的原始值数值(无效值=0)",
        "# TYPE mcp_log_level_configured_value gauge",
        f'mcp_log_level_configured_value{{configured="{configured}"}} {configured_num}',
        "",
    ]
    return "\n".join(lines)


def run_healthcheck() -> int:
    """健康检查模式: 无回退返回 0,发生回退返回 1。"""
    status = get_status()
    if status["fallback"]:
        original = status["original"]
        print(
            f"[UNHEALTHY] MCP_LOG_LEVEL={original} 无效,已回退到 INFO。"
            f"有效值: {status['valid_levels']}",
            file=sys.stderr,
        )
        return 1
    else:
        level = status["level"]
        print(f"[HEALTHY] MCP 日志级别={level} (配置值有效,无回退)")
        return 0


def run_json_output() -> int:
    """JSON 输出模式: 打印状态 JSON,始终返回 0。"""
    status = get_status()
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return 0


class _MetricsHandler(BaseHTTPRequestHandler):
    """Prometheus /metrics 端点处理器。"""

    def do_GET(self):
        if self.path == "/metrics":
            status = get_status()
            body = render_prometheus_metrics(status).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            status = get_status()
            healthy = not status["fallback"]
            body = json.dumps({"healthy": healthy, **status}).encode("utf-8")
            self.send_response(200 if healthy else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # 静默访问日志(避免污染 stdout)
        pass


def run_server(port: int) -> int:
    """Prometheus 指标导出模式: 启动 HTTP 服务器。"""
    status = get_status()
    print(f"[exporter] MCP 日志级别指标导出器启动 (port={port})")
    print(f"[exporter] 当前级别={status['level']} 回退={status['fallback']}")

    server = HTTPServer(("0.0.0.0", port), _MetricsHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[exporter] 已停止")
        server.server_close()
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="MCP 日志级别健康检查 + Prometheus 指标导出器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="启动 Prometheus 指标导出 HTTP 服务器(默认: 单次健康检查)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9102,
        help="指标导出端口(默认: 9102)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式状态(调试用)",
    )
    args = parser.parse_args()

    if args.serve:
        return run_server(args.port)
    elif args.json:
        return run_json_output()
    else:
        return run_healthcheck()


if __name__ == "__main__":
    raise SystemExit(main())
