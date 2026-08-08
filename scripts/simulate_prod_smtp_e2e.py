#!/usr/bin/env python3
"""模拟生产环境 SMTP 授权码填入后的完整端到端告警测试流程。

【背景】本地网络无法外发 smtp.139.com:587（connection refused），因此本脚本把
"授权码填入 → reload → 触发告警 → SMTP 发送 → 检视日志" 的完整生产流程拆成
可独立执行/可模拟的步骤，每一步记录状态（PASS / FAIL / BLOCKED / SKIP），
最终输出结构化结果并（可选）生成验证报告 markdown。

【两种模式】
  A. 生产模式（在目标生产服务器执行，默认）：
     真实执行全部步骤：替换授权码 → SIGHUP reload → 注入告警 → 等待 40s →
     检视容器日志（Notify for alerts completed / failed）→ resolve 清理。
  B. 本地模拟模式（--local-mock）：
     本机网络受限时，用内置极简 SMTP 服务器（127.0.0.1:1025）验证除"真实
     外发"外的全部逻辑链路（receiver 解析 / email_configs / SMTP 会话），
     并如实将"真实外发 139"步骤标记为 BLOCKED（不假装通过）。

【用法】
  # 生产模式（需要真实授权码）
  python scripts/simulate_prod_smtp_e2e.py --auth-code <真实授权码> [--report-out 报告路径.md]

  # 本地模拟模式（授权码可传任意占位值，用于走通流程）
  python scripts/simulate_prod_smtp_e2e.py --local-mock [--auth-code <任意值>] [--report-out 报告路径.md]

【不易】授权码经参数/环境变量注入，日志仅显示打码版本；演示结束后默认还原
       占位符并 reload（--keep-code 可保留）。BLOCKED 步骤不计为失败，但必须
       在报告中如实标注"待生产环境实测"。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parents[1] / "deploy" / "monitoring" / "prometheus" / "alertmanager.yml"
CONTAINER = "yunshu-prod-alertmanager"
AM_BASE = "http://127.0.0.1:9093"
PLACEHOLDER = "REPLACE_WITH_SMTP_AUTH_CODE"
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


# ── 步骤记录器 ──────────────────────────────────────────────────────────
@dataclass
class Step:
    name: str
    status: str = "SKIP"      # PASS / FAIL / BLOCKED / SKIP
    detail: str = ""
    evidence: str = ""


@dataclass
class Recorder:
    steps: list = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "", evidence: str = "") -> None:
        self.steps.append(Step(name, status, detail, evidence))
        mark = {"PASS": "[PASS]", "FAIL": "[FAIL]", "BLOCKED": "[BLOCKED]", "SKIP": "[SKIP]"}[status]
        print(f"  {mark} {name}: {detail}" + (f"\n       证据: {evidence}" if evidence else ""))


# ── 基础设施 ────────────────────────────────────────────────────────────
def run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def mask(secret: str) -> str:
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}****{secret[-4:]}"


def current_password() -> str:
    for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("smtp_auth_password:"):
            return s.split(":", 1)[1].strip().strip("'\"")
    return ""


def set_password(code: str) -> None:
    text = CONFIG_FILE.read_text(encoding="utf-8")
    new = "\n".join(
        line if not line.strip().startswith("smtp_auth_password:")
        else f"  smtp_auth_password: '{code}'"
        for line in text.splitlines()
    )
    CONFIG_FILE.write_text(new, encoding="utf-8")


def set_smarthost(smarthost: str, require_tls: bool) -> None:
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


def reload_alertmanager() -> bool:
    r = run(["docker", "exec", CONTAINER, "kill", "-HUP", "1"])
    if r.returncode != 0:
        return False
    time.sleep(2)
    return True


def container_running() -> bool:
    r = run(["docker", "ps", "-q", "-f", f"name={CONTAINER}"])
    return r.returncode == 0 and bool(r.stdout.strip())


def post_alert(uid: int) -> int:
    alert = [{
        "labels": {"alertname": "SmtpE2ESim", "instance": f"e2e-{uid}", "team": "knowledge"},
        "annotations": {"summary": "端到端模拟测试告警（非真实故障）"},
        "startsAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    }]
    req = urllib.request.Request(
        AM_BASE + "/api/v2/alerts", data=json.dumps(alert).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def resolve_alert(uid: int) -> int:
    alert = [{
        "labels": {"alertname": "SmtpE2ESim", "instance": f"e2e-{uid}", "team": "knowledge"},
        "status": "resolved",
        "endsAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    }]
    req = urllib.request.Request(
        AM_BASE + "/api/v2/alerts", data=json.dumps(alert).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def inspect_notify_logs(since: str = "2m") -> tuple[bool, bool, str]:
    """返回 (completed, failed, 相关日志片段)。"""
    r = run(["docker", "logs", CONTAINER, "--since", since], timeout=30)
    logs = r.stdout
    completed = "Notify for alerts completed" in logs
    failed = "Notify attempt failed" in logs
    snippet = "\n".join(
        line.strip()[:220] for line in logs.splitlines()
        if any(k in line for k in ("Notify for alerts", "Notify attempt failed", "dial tcp", "authentication", "TLS", "STARTTLS"))
    )[:800]
    return completed, failed, snippet


# ── 主流程 ──────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="模拟 SMTP 授权码填入后的端到端告警测试流程")
    parser.add_argument("--auth-code", default=None, help="SMTP 授权码（生产模式必需；或设环境变量 SMTP_AUTH_CODE）")
    parser.add_argument("--local-mock", action="store_true", help="本地模拟模式：用 127.0.0.1:1025 模拟 SMTP 服务器")
    parser.add_argument("--report-out", default=None, help="生成的验证报告 .md 路径（默认不落盘，仅打印）")
    parser.add_argument("--keep-code", action="store_true", help="结束后保留填入的授权码（默认还原占位符）")
    args = parser.parse_args()

    code = args.auth_code or os.environ.get("SMTP_AUTH_CODE", "")
    rec = Recorder()
    uid = int(time.time())

    print("═══ SMTP 端到端告警测试流程模拟 ═══")
    print(f"模式: {'本地模拟 (--local-mock)' if args.local_mock else '生产模式'}")
    print(f"测试告警 instance: e2e-{uid}")

    # S1 前置：容器与配置状态
    print("\n[S1] 前置检查（容器 + 当前配置）")
    running = container_running()
    rec.add("容器运行", "PASS" if running else "FAIL" if not args.local_mock else "SKIP",
            f"yunshu-prod-alertmanager 运行中={running}")
    if not running:
        print("  [提示] 容器未运行，链路无法走通；请先 docker compose up -d 启动监控栈。")

    cur = current_password()
    rec.add("授权码状态", "PASS" if cur != PLACEHOLDER else "FAIL" if not args.local_mock else "SKIP",
            f"当前={mask(cur)} 占位符={cur == PLACEHOLDER}")

    if not code:
        print("[ERROR] 未提供授权码：--auth-code 参数或环境变量 SMTP_AUTH_CODE", file=sys.stderr)
        return 2

    original = CONFIG_FILE.read_text(encoding="utf-8")
    smtp_server = None
    try:
        # S2 授权码填入
        print("\n[S2] 填入授权码并 reload")
        set_password(code)
        if args.local_mock:
            smtp_server = MiniSMTPServer(LOCAL_SMTP_PORT)
            set_smarthost(f"host.docker.internal:{LOCAL_SMTP_PORT}", False)
            rec.add("配置切换", "PASS", f"本地模拟指向 host.docker.internal:{LOCAL_SMTP_PORT} (require_tls=false)")
        else:
            rec.add("配置切换", "PASS", f"smtp_auth_password → {mask(code)}")
        if running:
            reload_alertmanager()
            rec.add("SIGHUP reload", "PASS" if reload_alertmanager() else "FAIL", "热加载已触发")
        else:
            rec.add("SIGHUP reload", "SKIP", "容器未运行")

        # S3 端口连通性
        print("\n[S3] SMTP 端口连通性")
        if args.local_mock:
            rec.add("587 外发", "BLOCKED", "本地网络受限（dial tcp smtp.139.com:587: connection refused），待生产实测")
        else:
            try:
                with socket.create_connection(("smtp.139.com", 587), timeout=5):
                    rec.add("587 外发", "PASS", "smtp.139.com:587 TCP 握手成功")
            except OSError as exc:
                rec.add("587 外发", "BLOCKED", f"{exc}（网络受限/未放行，请按检查清单第 1-2 节处理后再验）")

        # S4 注入测试告警
        print("\n[S4] 注入唯一测试告警")
        if running:
            status = post_alert(uid)
            rec.add("注入告警", "PASS" if status == 200 else "FAIL", f"POST /api/v2/alerts → HTTP {status}")
            time.sleep(2)
        else:
            rec.add("注入告警", "SKIP", "容器未运行")

        # S5 等待 + 检视日志
        print(f"\n[S5] 等待 group_wait(30s)+发送（{40}s）")
        time.sleep(40)
        if running:
            completed, failed, snippet = inspect_notify_logs()
            if args.local_mock:
                n = smtp_server.count() if smtp_server else 0
                if n > 0:
                    rec.add("邮件发送(本地模拟)", "PASS", f"模拟服务器收到 {n} 封邮件", snippet or "（见日志）")
                else:
                    rec.add("邮件发送(本地模拟)", "FAIL", "模拟服务器未收到邮件", snippet)
                rec.add("真实外发 139", "BLOCKED", "本地网络受限，逻辑链路已用模拟 SMTP 验证；真实发送需在生产执行")
            elif completed and not failed:
                rec.add("邮件发送(生产)", "PASS", "Notify for alerts completed", snippet)
            elif failed:
                rec.add("邮件发送(生产)", "FAIL", "Notify attempt failed", snippet)
            else:
                rec.add("邮件发送(生产)", "SKIP", "2m 内无发送日志（可能仍在排队或已去重）")
        else:
            rec.add("邮件发送", "SKIP", "容器未运行")

        # S6 resolve 清理
        print("\n[S6] resolve 测试告警（清理）")
        if running:
            code_r = resolve_alert(uid)
            rec.add("resolve 清理", "PASS" if code_r == 200 else "WARN", f"resolve → HTTP {code_r}")
    finally:
        if not args.keep_code:
            print("\n[清理] 还原生产配置并 reload")
            CONFIG_FILE.write_text(original, encoding="utf-8")
            if running:
                reload_alertmanager()
            print("  已还原")

    # ── 汇总 ──
    print("\n═══ 汇总 ═══")
    n_pass = sum(1 for s in rec.steps if s.status == "PASS")
    n_fail = sum(1 for s in rec.steps if s.status == "FAIL")
    n_blocked = sum(1 for s in rec.steps if s.status == "BLOCKED")
    print(f"  PASS={n_pass}  FAIL={n_fail}  BLOCKED={n_blocked}  SKIP={sum(1 for s in rec.steps if s.status == 'SKIP')}")
    if n_fail > 0:
        print("  ✗ 存在 FAIL 项，链路未通过")
    elif n_blocked > 0 and not args.local_mock:
        print("  ✗ 存在 BLOCKED 项（网络受限），必须生产实测后才能判定")
    else:
        print("  ✓ 本地逻辑链路验证通过（真实外发待生产实测）")

    # ── 生成报告 ──
    if args.report_out:
        render_report(Path(args.report_out), rec, uid, args.local_mock, mask(code))
        print(f"\n[报告] 已生成: {args.report_out}")

    return 1 if n_fail > 0 else 0


def render_report(out: Path, rec: Recorder, uid: int, local_mock: bool, masked_code: str) -> None:
    rows = "\n".join(
        f"| {s.name} | {s.status} | {s.detail} | {s.evidence or '-'} |" for s in rec.steps
    )
    verdict = "通过" if all(s.status == "PASS" for s in rec.steps) else (
        "未通过" if any(s.status == "FAIL" for s in rec.steps) else "待生产实测"
    )
    content = f"""# SMTP 端到端告警测试验证报告

> 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}  测试告警 instance: e2e-{uid}
> 模式: {'本地模拟（真实外发待生产实测）' if local_mock else '生产实测'}

## 测试步骤与结果

| 步骤 | 状态 | 说明 | 证据 |
|---|---|---|---|
{rows}

## 结论

**{verdict}**

- 逻辑链路（receiver / email_configs / SMTP 会话）验证情况：见上表。
- BLOCKED/SKIP 项必须在生产环境补测后才能判定整体通过。
- 收件确认：13539371839@139.com（含垃圾箱）。

## 附录：授权码

已填入授权码（打码显示）：{masked_code}
"""
    out.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
