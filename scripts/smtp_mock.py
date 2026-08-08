#!/usr/bin/env python3
"""本地极简 SMTP 服务器（测试/离线验证用）。

Python 3.12 已移除 smtpd，这里用 socketserver 实现最小 SMTP 协议：
  EHLO/HELO → 250    STARTTLS → 220（不真正加密）
  AUTH LOGIN → 334/334/235（或 535 模拟失败）
  MAIL/RCPT → 250    DATA → 354 → 250    QUIT → 221

【用途】
  1. scripts/rotate_smtp_auth_code.py verify --local-mock（离线验证授权码逻辑）
  2. scripts/test_smtp_auth_code.py（自动化测试用例的本地桩）

【用法（命令行直接起服务）】
  python scripts/smtp_mock.py [--port 1025] [--fail-auth] [--capture <文件>]
"""

from __future__ import annotations

import argparse
import base64
import socketserver
import sys
import threading


class MiniSMTPHandler(socketserver.StreamRequestHandler):
    """极简 SMTP 会话处理：支持 AUTH LOGIN 成功/失败模拟。"""

    # 会话状态（实例级）
    def setup(self):
        super().setup()
        self._awaiting_user = False
        self._awaiting_pass = False
        self._auth_ok = False

    def _send(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())

    def handle(self) -> None:
        self._send("220 mock-smtp ESMTP ready")
        while True:
            try:
                line = self.rfile.readline()
            except Exception:
                break
            if not line:
                break
            cmd_raw = line.decode("utf-8", errors="replace").strip()
            cmd = cmd_raw.split(" ", 1)[0].upper()
            arg = cmd_raw.split(" ", 1)[1] if " " in cmd_raw else ""

            if self._awaiting_user:
                if cmd_raw == "*":
                    self._send("501 auth aborted")
                    self._awaiting_user = False
                else:
                    self.username = self._b64dec(cmd_raw)
                    self._send("334 UGFzc3dvcmQ6")  # Password:
                self._awaiting_user = False
                self._awaiting_pass = True
                continue
            if self._awaiting_pass:
                password = self._b64dec(cmd_raw)
                expected = getattr(self.server, "auth_password", "mock-pass")
                if password == expected and not getattr(self.server, "fail_auth", False):
                    self._auth_ok = True
                    self._send("235 2.7.0 Authentication successful")
                else:
                    self._auth_ok = False
                    self._send("535 5.7.8 Authentication credentials invalid")
                self._awaiting_pass = False
                continue

            if cmd in ("EHLO", "HELO"):
                self._send("250-mock-smtp")
                self._send("250 AUTH LOGIN")
            elif cmd == "STARTTLS":
                self._send("220 2.0.0 Ready to start TLS")
            elif cmd == "AUTH":
                # smtplib 对 LOGIN 机制可能带初始响应：AUTH LOGIN <base64(user)>
                if arg.upper().startswith("LOGIN"):
                    _, _, initial = arg.partition(" ")
                    if initial:
                        self.username = self._b64dec(initial.strip())
                        self._send("334 UGFzc3dvcmQ6")  # Password:
                        self._awaiting_pass = True
                    else:
                        self._send("334 VXNlcm5hbWU6")  # Username:
                        self._awaiting_user = True
                else:
                    self._send("504 5.5.4 Unrecognized authentication type")
            elif cmd in ("MAIL", "RCPT"):
                self._send("250 2.1.0 OK")
            elif cmd == "DATA":
                self._send("354 End data with <CR><LF>.<CR><LF>")
                self._collect_data()
            elif cmd == "NOOP":
                self._send("250 2.0.0 OK")
            elif cmd == "QUIT":
                self._send("221 2.0.0 Bye")
                break
            else:
                self._send("500 5.5.2 Command unrecognized")

    def _collect_data(self) -> None:
        data = []
        while True:
            line = self.rfile.readline()
            if not line:
                break
            if line == b".\r\n":
                break
            data.append(line)
        capture = getattr(self.server, "capture_path", None)
        if capture:
            with open(capture, "ab") as f:
                f.write(b"".join(data) + b"\n===EOM===\n")
        self._send("250 2.0.0 OK: queued")

    @staticmethod
    def _b64dec(s: str) -> str:
        try:
            return base64.b64decode(s).decode("utf-8", errors="replace")
        except Exception:
            return s


class MiniSMTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_mock_server(port: int = 1025, auth_password: str = "mock-pass",
                      fail_auth: bool = False, capture_path: str | None = None) -> MiniSMTPServer:
    server = MiniSMTPServer(("127.0.0.1", port), MiniSMTPHandler)
    server.auth_password = auth_password
    server.fail_auth = fail_auth
    server.capture_path = capture_path
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def run_verify_mock(code: str, fail_auth: bool = False) -> tuple[bool, str]:
    """rotate_smtp_auth_code.verify --local-mock 的入口：用 code 对本地 mock 做 AUTH。"""
    if fail_auth:
        return False, "本地模拟 AUTH 失败（535 授权码错误路径）"
    server = start_mock_server(port=1025, auth_password=code, fail_auth=False)
    try:
        import smtplib
        with smtplib.SMTP("127.0.0.1", 1025, timeout=5) as smtp:
            smtp.ehlo()
            smtp.login("13539371839@139.com", code)
            smtp.sendmail("13539371839@139.com", ["test@mock.local"], "Subject: mock\r\n\r\nhi")
        return True, "本地模拟 AUTH+发送成功（mock 服务器已收信）"
    except smtplib.SMTPAuthenticationError:
        return False, "本地模拟 AUTH 失败（mock 返回 535）"
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="本地极简 SMTP 服务器（测试用）")
    parser.add_argument("--port", type=int, default=1025)
    parser.add_argument("--fail-auth", action="store_true", help="模拟 AUTH 失败(535)")
    parser.add_argument("--capture", default=None, help="捕获邮件内容到文件")
    args = parser.parse_args()
    srv = start_mock_server(port=args.port, fail_auth=args.fail_auth, capture_path=args.capture)
    print(f"mock SMTP 监听 127.0.0.1:{args.port}（fail_auth={args.fail_auth}）Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
        srv.server_close()
        print("\nstopped")
        sys.exit(0)
