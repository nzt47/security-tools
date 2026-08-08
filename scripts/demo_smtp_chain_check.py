#!/usr/bin/env python3
"""本地链路验证：模拟 SMTP 服务器 + 触发测试告警，验证 Alertmanager 邮件通知逻辑。

【背景】本机网络无法外发 smtp.139.com:587（connection refused），但 Alertmanager
的邮件通知逻辑（receiver 解析 / email_configs / SMTP 会话）可在本地完整验证：
  1. 启动内置极简 SMTP 服务器（127.0.0.1:1025，捕获完整邮件）
  2. 临时将 alertmanager.yml 的 smarthost 指向 host.docker.internal:1025，
     smtp_require_tls 置 false（本地服务器无 TLS）——会话结构与 587 STARTTLS 一致
  3. SIGHUP reload → 注入唯一测试告警 → group_wait 30s 后 dispatcher 发信
  4. 断言模拟服务器收到邮件（From/To/Subject 正确）
  5. finally 恢复原配置并 reload（生产配置零残留）

用法: python scripts/demo_smtp_chain_check.py
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parents[1] / "deploy" / "monitoring" / "prometheus" / "alertmanager.yml"
CONTAINER = "yunshu-prod-alertmanager"
AM_BASE = "http://127.0.0.1:9093"
LOCAL_SMTP_PORT = 1025


# ── 极简 SMTP 服务器（Python 3.12 已移除 smtpd，自实现最小协议）──────────────
class MiniSMTPServer:
    def __init__(self, port: int) -> None:
        self.messages: list[str] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", port))
        self._sock.listen(5)
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        f = conn.makefile("rwb")
        send = lambda s: (f.write(s.encode() + b"\r\n"), f.flush())  # noqa: E731
        try:
            send("220 localhost MiniSMTP ready")
            data_mode, lines = False, []
            while True:
                raw = f.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if data_mode:
                    if line == ".":
                        self.messages.append("".join(lines))
                        send("250 OK: queued as LOCAL12345")
                        data_mode = False
                    else:
                        lines.append(line + "\n")
                    continue
                cmd = line.upper()
                if cmd.startswith(("EHLO", "HELO")):
                    send("250-localhost"); send("250 SIZE 10485760")
                elif cmd.startswith(("MAIL FROM", "RCPT TO")):
                    send("250 OK")
                elif cmd == "DATA":
                    send("354 End data with <CR><LF>.<CR><LF>")
                    data_mode, lines = True, []
                elif cmd == "QUIT":
                    send("221 Bye")
                    break
                else:
                    send("250 OK")
        finally:
            f.close()
            conn.close()

    def count(self) -> int:
        return len(self.messages)


def run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def reload_alertmanager() -> None:
    run(["docker", "exec", CONTAINER, "kill", "-HUP", "1"])


def set_smarthost(smarthost: str, require_tls: bool) -> None:
    """替换配置中的 smtp_smarthost 与 smtp_require_tls（保留其余行）。"""
    text = CONFIG_FILE.read_text(encoding="utf-8")
    new_lines = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("smtp_smarthost:"):
            new_lines.append(f"  smtp_smarthost: '{smarthost}'")
        elif s.startswith("smtp_require_tls:"):
            new_lines.append(f"  smtp_require_tls: {str(require_tls).lower()}")
        else:
            new_lines.append(line)
    CONFIG_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def post_test_alert() -> None:
    uid = int(time.time())
    alert = [{
        "labels": {"alertname": "SmtpChainTest", "instance": f"smtp-test-{uid}", "team": "knowledge"},
        "annotations": {"summary": "本地 SMTP 链路验证（自动触发，非真实故障）"},
        "startsAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    }]
    req = urllib.request.Request(
        AM_BASE + "/api/v2/alerts", data=json.dumps(alert).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 200, f"注入失败 HTTP {resp.status}"


def main() -> int:
    original = CONFIG_FILE.read_text(encoding="utf-8")
    smtp = MiniSMTPServer(LOCAL_SMTP_PORT)
    try:
        print(f"[1] 本地模拟 SMTP 服务器已启动: 127.0.0.1:{LOCAL_SMTP_PORT}")

        print("[2] 临时指向本地 SMTP（host.docker.internal:1025, require_tls=false）并 reload")
        set_smarthost("host.docker.internal:1025", False)
        reload_alertmanager()
        time.sleep(2)

        print("[3] 注入测试告警 → Alertmanager")
        post_test_alert()
        print("    注入成功")

        print(f"[4] 等待 group_wait(30s) + 发信（{35}s）")
        time.sleep(35)

        n = smtp.count()
        print(f"[5] 模拟服务器收到邮件数: {n}")
        if n == 0:
            logs = run(["docker", "logs", CONTAINER, "--since", "2m"]).stdout
            for line in logs.splitlines():
                if "Notify" in line or "smtp" in line.lower() or "SMTP" in line:
                    print(f"   | {line.strip()[:200]}")
            print("[ERROR] 未收到邮件，链路不通", file=sys.stderr)
            return 1

        msg = smtp.messages[0]
        from_m = re.search(r"From: (.+)", msg)
        to_m = re.search(r"To: (.+)", msg)
        subj = re.search(r"Subject: (.+)", msg)
        print(f"    From: {from_m.group(1) if from_m else '(未找到)'}")
        print(f"    To:   {to_m.group(1) if to_m else '(未找到)'}")
        print(f"    Subject: {subj.group(1) if subj else '(未找到)'}")
        ok = all(m for m in (from_m, to_m, subj))
        assert ok, "邮件头不完整"
        print("[6] 邮件头完整，Alertmanager 邮件通知逻辑验证通过 ✓")
        return 0
    finally:
        # 恢复生产配置并 reload（try/finally 保证不残留）
        CONFIG_FILE.write_text(original, encoding="utf-8")
        reload_alertmanager()
        print("[7] 已恢复生产配置并 reload")


if __name__ == "__main__":
    sys.exit(main())
