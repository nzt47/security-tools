"""模拟 Slack webhook 接收服务器 [验证用]

用途：
- 启动本地 HTTP 服务器接收 Slack webhook 请求
- 打印请求内容，验证监控脚本是否正确发送告警
- 不发送真实 Slack 请求，避免干扰

运行：
    python scripts/mock_slack_server.py --port 9999
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class SlackWebhookHandler(BaseHTTPRequestHandler):
    """模拟 Slack webhook 接收器"""

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")

        print("\n" + "=" * 60, flush=True)
        print("📨 收到 Slack webhook 请求", flush=True)
        print("=" * 60, flush=True)
        print(f"路径: {self.path}", flush=True)
        print(f"Content-Type: {self.headers.get('Content-Type')}", flush=True)

        try:
            payload = json.loads(body)
            print(f"请求体（JSON）:", flush=True)
            print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
        except Exception:
            print(f"请求体（原始）: {body}", flush=True)

        print("=" * 60 + "\n", flush=True)

        # 返回 200 OK（Slack 期望的响应）
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        """静默默认日志（只打印我们关心的内容）"""
        pass


def main():
    parser = argparse.ArgumentParser(description="模拟 Slack webhook 服务器")
    parser.add_argument("--port", type=int, default=9999, help="监听端口（默认 9999）")
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), SlackWebhookHandler)
    print(f"🚀 模拟 Slack webhook 服务器已启动", flush=True)
    print(f"   URL: http://127.0.0.1:{args.port}/webhook", flush=True)
    print(f"   等待请求...（Ctrl+C 退出）", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n服务器已停止", flush=True)
        server.server_close()


if __name__ == "__main__":
    main()
