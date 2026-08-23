"""极简 SMTP 捕获服务器（本地联调告警邮件发送，无需真实 SMTP）

Python 3.12 已移除 smtpd 模块，此处用标准库 socket 实现最小 SMTP 会话：
接收 EHLO/MAIL/RCPT/DATA/QUIT，保存邮件原文到 .eml 文件并打印。

用法：
  python scripts/dev/smtp_capture_server.py --port 2525 --out capture.eml
配合 analyze_audit_logs.py 联调：
  $env:SMTP_HOST='127.0.0.1'; $env:SMTP_PORT='2525'; $env:SMTP_SSL='0'
  $env:SMTP_TO='test@local'; $env:SMTP_USER=''
  python scripts/analyze_audit_logs.py --audit-dir <含异常数据集目录>
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class _LineReader:
    """带缓冲的 SMTP 行读取（DATA 段一次到达多行也能正确消费）

    约定：正常行返回 bytes（空行返回 b""）；连接关闭返回 None。
    调用方必须用 `if raw is None` 判断 EOF —— 空行 b"" 是合法的 DATA 内容，
    不能当作结束（否则正文中的空行会提前断开连接）。
    """

    def __init__(self, conn: socket.socket):
        self._conn = conn
        self._buf = b""

    def readline(self) -> bytes | None:
        self._conn.settimeout(30)
        while b"\r\n" not in self._buf:
            chunk = self._conn.recv(4096)
            if not chunk:
                # recv 返回空即对端关闭；缓冲区还有未换行的残留时按最后一行返回
                if not self._buf:
                    return None
                break
            self._buf += chunk
        if b"\r\n" in self._buf:
            line, self._buf = self._buf.split(b"\r\n", 1)
            return line
        line, self._buf = self._buf, b""
        return line


class _Log:
    """双写日志：stdout + 可选文件（后台进程 stdout 不可见时仍可查）"""

    def __init__(self, path: Path | None):
        self._fh = open(path, "a", encoding="utf-8") if path else None

    def __call__(self, msg: str):
        print(msg, flush=True)
        if self._fh:
            self._fh.write(msg + "\n")
            self._fh.flush()


_log = _Log(None)


def _handle(conn: socket.socket, addr, out_file: Path) -> None:
    def send(text: str):
        conn.sendall((text + "\r\n").encode())

    reader = _LineReader(conn)
    try:
        send("220 yunshu-smtp-capture ready")
        data_lines: list[str] = []
        in_data = False
        sender = rcpt = ""
        while True:
            raw = reader.readline()
            if raw is None:
                break
            line = raw.decode("utf-8", errors="replace")
            cmd = line.upper()
            if cmd.startswith("EHLO") or cmd.startswith("HELO"):
                send("250 OK")
            elif cmd.startswith("MAIL FROM"):
                sender = line.split(":", 1)[-1].strip()
                send("250 OK")
            elif cmd.startswith("RCPT TO"):
                rcpt = line.split(":", 1)[-1].strip()
                send("250 OK")
            elif cmd == "DATA":
                send("354 End data with <CR><LF>.<CR><LF>")
                in_data = True
            elif in_data:
                if line == ".":
                    in_data = False
                    email = "\n".join(data_lines) + "\n"
                    _log(f"[capture] 收到邮件 from={sender} to={rcpt} 长度={len(email)}")
                    target = out_file
                    if target.exists():
                        target = out_file.with_name(
                            f"{out_file.stem}_{datetime.now().strftime('%H%M%S')}{out_file.suffix}")
                    target.write_text(email, encoding="utf-8")
                    _log(f"[capture] 已保存: {target}")
                    send("250 OK: message accepted")
                else:
                    data_lines.append(line)
            elif cmd.startswith("QUIT"):
                send("221 Bye")
                break
            else:
                send("250 OK")
    except Exception as e:  # noqa: BLE001 会话异常不中断主循环
        _log(f"[capture] 会话异常（已关闭连接）: {type(e).__name__}: {e}")
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def main():
    ap = argparse.ArgumentParser(description="极简 SMTP 捕获服务器（本地告警联调）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2525)
    ap.add_argument("--out", type=Path, default=Path("./captured_email.eml"))
    ap.add_argument("--log", type=Path, default=None,
                    help="日志文件（后台运行时 stdout 不可见，落盘可查）")
    args = ap.parse_args()

    global _log
    _log = _Log(args.log)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(5)
    _log(f"[capture] SMTP 捕获服务器监听 {args.host}:{args.port}，保存到 {args.out}")
    _log("[capture] Ctrl+C 退出")
    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=_handle, args=(conn, addr, args.out),
                             daemon=True).start()
    except KeyboardInterrupt:
        _log("[capture] 退出")


if __name__ == "__main__":
    main()
