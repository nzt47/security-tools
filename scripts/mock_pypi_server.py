#!/usr/bin/env python3
"""Mock PyPI 服务器 [TLM-L3]

用途：
- 模拟 PyPI upload 端点（https://upload.pypi.org/legacy/）
- 接收 twine 上传的 multipart/form-data 请求
- 返回 200 OK，不实际上传任何内容
- 用于"模拟上传"验证 twine 配置 + 包格式 + 认证流程

退出码：
- 0：正常退出（Ctrl+C）
- 1：端口被占用或启动失败

运行示例：
    python scripts/mock_pypi_server.py --port 8080
    # 另一个终端：
    TWINE_USERNAME=__token__ TWINE_PASSWORD=pypi-fake-token \
        twine upload --repository-url http://localhost:8080/legacy/ dist/*
"""

from __future__ import annotations

import argparse
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class MockPyPIHandler(BaseHTTPRequestHandler):
    """模拟 PyPI upload 端点的请求处理器"""

    def do_POST(self) -> None:
        """处理 twine 上传请求"""
        content_length = int(self.headers.get("Content-Length", 0))
        content_type = self.headers.get("Content-Type", "")
        auth = self.headers.get("Authorization", "")

        # 读取请求体（不保存，仅统计大小）
        body = self.rfile.read(content_length) if content_length else b""

        print()
        print("=" * 60)
        print("📨 收到 twine 上传请求")
        print("=" * 60)
        print(f"路径: {self.path}")
        print(f"Content-Type: {content_type}")
        print(f"Content-Length: {content_length} bytes ({content_length/1024:.1f} KB)")
        print(f"Authorization: {'Bearer ********' if auth else '(无)'}")

        # 解析 multipart/form-data，统计文件数
        if "multipart/form-data" in content_type:
            file_count = body.count(b'filename="')
            print(f"上传文件数: {file_count}")

        # 模拟 PyPI 认证检查
        if not auth:
            print("  [!] 未提供 Authorization 头")
            self._respond(401, "Unauthorized: 缺少认证信息")
            return

        # 模拟 PyPI 成功响应
        print("  [✓] 认证通过（模拟）")
        print("  [✓] 文件接收成功（模拟）")
        print("=" * 60)
        self._respond(200, "OK: 模拟上传成功")

    def _respond(self, status: int, message: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(message.encode("utf-8"))

    def log_message(self, format: str, *args) -> None:
        # 静默默认日志，用 do_POST 中的自定义输出
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock PyPI 服务器（模拟上传验证）")
    parser.add_argument("--port", type=int, default=8080, help="监听端口（默认 8080）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    args = parser.parse_args()

    try:
        server = HTTPServer((args.host, args.port), MockPyPIHandler)
    except OSError as e:
        print(f"[错误] 无法启动服务器: {e}", file=sys.stderr)
        return 1

    print(f"🚀 Mock PyPI 服务器已启动")
    print(f"   URL: http://{args.host}:{args.port}/legacy/")
    print(f"   等待 twine 上传请求...（Ctrl+C 退出）")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[✓] 服务器已停止")
        server.server_close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
