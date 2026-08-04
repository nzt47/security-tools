#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mock Alertmanager — 告警接收验证器

【不易】接收 Alertmanager v2 API 格式的告警 payload，原样打印
【变易】支持 GET /api/v2/alerts（返回已收告警列表）和 POST /api/v2/alerts（接收告警）
【简易】标准库实现，零依赖，便于在 Pod 内运行

用法:
    python scripts/mock_alert_webhook.py --port 9093
    # 集群内 Pod 部署后，Grafana 通知策略 --webhook-url 指向 http://mock-alert-webhook.monitoring:9093/api/v2/alerts
"""
import argparse
import json
import sys
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler


class AlertHandler(BaseHTTPRequestHandler):
    # 类变量存储已收告警（便于 GET 查询验证）
    received_alerts = []

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length > 0 else '[]'

        ts = datetime.now().isoformat()
        print(f"\n{'='*60}", flush=True)
        print(f"[ALERT RECEIVED] {ts}", flush=True)
        print(f"  Path: {self.path}", flush=True)
        print(f"  Body:", flush=True)
        try:
            alerts = json.loads(body)
            for i, alert in enumerate(alerts):
                print(f"  ── Alert #{i+1} ──", flush=True)
                print(f"    alertname: {alert.get('labels', {}).get('alertname')}", flush=True)
                print(f"    severity:  {alert.get('labels', {}).get('severity')}", flush=True)
                print(f"    service:   {alert.get('labels', {}).get('service')}", flush=True)
                print(f"    patrol_id: {alert.get('labels', {}).get('patrol_id')}", flush=True)
                summary = alert.get('annotations', {}).get('summary', '')
                print(f"    summary:   {summary}", flush=True)
                self.received_alerts.append(alert)
        except json.JSONDecodeError:
            print(f"  [raw] {body}", flush=True)
        print(f"{'='*60}", flush=True)

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"status":"received"}')

    def do_GET(self):
        """返回已收告警列表（验证用）"""
        if self.path == '/api/v2/alerts' or self.path == '/alerts':
            body = json.dumps(self.received_alerts, ensure_ascii=False, indent=2)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(body.encode('utf-8'))
        elif self.path == '/health':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'ok')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        """抑制默认访问日志，仅保留告警输出"""
        pass


def main():
    parser = argparse.ArgumentParser(description="Mock Alertmanager 告警接收器")
    parser.add_argument('--port', type=int, default=9093, help='监听端口（默认 9093）')
    args = parser.parse_args()

    print(f"Mock Alertmanager listening on 0.0.0.0:{args.port}", flush=True)
    print(f"  POST /api/v2/alerts  - 接收告警", flush=True)
    print(f"  GET  /api/v2/alerts  - 查询已收告警", flush=True)
    print(f"  GET  /health         - 健康检查", flush=True)

    server = HTTPServer(('0.0.0.0', args.port), AlertHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...", flush=True)
        server.server_close()


if __name__ == '__main__':
    main()
