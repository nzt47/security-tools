#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试用的本地 Webhook 服务器
用于接收和显示错误报告
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import threading
import socket

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        
        print("\n" + "="*80)
        print("📨 NEW WEBHOOK RECEIVED")
        print("="*80)
        print(f"From: {self.client_address}")
        print(f"Headers: {dict(self.headers)}")
        
        try:
            data = json.loads(body)
            print("\n📊 Error Report:")
            print(json.dumps(data, indent=2, ensure_ascii=False))
            
            # 提取关键信息
            print(f"\n🚨 Alert Level: {data.get('level', 'UNKNOWN')}")
            print(f"📝 Error Type: {data.get('error_type', 'UNKNOWN')}")
            print(f"💬 Message: {data.get('error_message', 'UNKNOWN')}")
            print(f"🕐 Time: {data.get('timestamp', 'UNKNOWN')}")
            
            if data.get('trace_id'):
                print(f"🆔 Trace ID: {data['trace_id']}")
            
            if data.get('context'):
                print(f"📋 Context: {json.dumps(data['context'], ensure_ascii=False)}")
            
        except Exception as e:
            print(f"\n⚠️ Error parsing body: {e}")
            print(f"Raw body: {body}")
        
        print("="*80)
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'status': 'ok', 'received': True}).encode())
    
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(b"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Webhook Test Server</title>
            <style>
                body { font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; }
                .status { background: #e8f4e8; padding: 20px; border-radius: 8px; }
                code { background: #f4f4f4; padding: 2px 6px; border-radius: 4px; }
            </style>
        </head>
        <body>
            <h1>🧪 Webhook Test Server</h1>
            <div class="status">
                <p><strong>✅ Server is running!</strong></p>
                <p><strong>Endpoint:</strong> <code>POST http://localhost:8888/webhook</code></p>
                <p><strong>Check the console for incoming webhooks!</strong></p>
            </div>
            <h3>Quick test:</h3>
            <p>Run the <code>test_webhook_integration.py</code> script to send test errors.</p>
        </body>
        </html>
        """)
    
    def log_message(self, format, *args):
        # 不显示默认日志，让输出更整洁
        pass


def get_local_ip():
    """获取本机 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def start_webhook_server(port=8888):
    """启动本地 Webhook 服务器"""
    server_address = ('', port)
    httpd = HTTPServer(server_address, WebhookHandler)
    
    local_ip = get_local_ip()
    
    print("\n" + "="*80)
    print("🧪 WEBHOOK TEST SERVER STARTED")
    print("="*80)
    print(f"\n📡 Endpoints:")
    print(f"  - Local:  http://localhost:{port}/webhook")
    print(f"  - Network: http://{local_ip}:{port}/webhook")
    print(f"\n📖 View dashboard: http://localhost:{port}")
    print(f"\n⏹️ Press Ctrl+C to stop")
    print("="*80)
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n\n🛑 Server stopped.")
        httpd.shutdown()


if __name__ == "__main__":
    start_webhook_server()
