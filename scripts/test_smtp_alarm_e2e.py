"""端到端联调：启动真实 SMTP 捕获服务器 → 构造 >5% 数据集 → 发送真实告警邮件

与 test_audit_alert_flow.py（mock smtplib）互补：本脚本走真实 socket/SMTP 协议，
邮件经 subprocess 启动的 scripts/dev/smtp_capture_server.py 落盘 .eml 后断言内容。

两种运行模式：
  - 默认（本地直连）：subprocess 启动捕获服务器（127.0.0.1:<port>）→ 发信 → 断言
  - 外部服务器（容器联调）：--external-host 指向已运行的捕获服务器（如 compose
    service 名 smtp-capture），--external-out 指向共享挂载的 .eml 路径

链路：
  1. 构造 10 条记录 6 条异常（60% > 5%）数据集（复用 test_audit_alert_flow.build_dataset）
  2. 启动/连接捕获服务器
  3. import_jsonl → check_anomalies(con, 5.0)：SMTP_HOST=127.0.0.1 SMTP_SSL=0 → 真实发信
  4. 解析 .eml → 断言正文含租户占比与 6 条异常明细

用法：
  python -X utf8 scripts/test_smtp_alarm_e2e.py [--port 2525] [--timeout 30]
  python -X utf8 scripts/test_smtp_alarm_e2e.py --external-host smtp-capture --external-out /captured/captured.eml
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from email import message_from_string
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analyze_audit_logs import import_jsonl, check_anomalies  # noqa: E402
from scripts.test_audit_alert_flow import build_dataset  # noqa: E402

CAPTURE_SCRIPT = ROOT / "scripts" / "dev" / "smtp_capture_server.py"


def wait_port(host: str, port: int, timeout: float,
              proc: subprocess.Popen | None = None) -> bool:
    """轮询端口就绪；若传入 proc 且其提前退出（如端口被占用）返回 False"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main():
    ap = argparse.ArgumentParser(description="SMTP 捕获服务器 + 真实告警邮件端到端联调")
    ap.add_argument("--host", default="127.0.0.1", help="本机启动捕获服务器时的监听地址")
    ap.add_argument("--port", type=int, default=2525)
    ap.add_argument("--timeout", type=float, default=30, help="等待服务器就绪秒数")
    ap.add_argument("--external-host", default=None,
                    help="连接已运行的捕获服务器（如 compose service 名），不本地启动")
    ap.add_argument("--external-out", type=Path, default=None,
                    help="外部模式 .eml 输出路径（共享挂载目录，用于断言）")
    args = ap.parse_args()

    checks: list[str] = []
    ok = lambda name: checks.append(f"  [OK] {name}")  # noqa: E731
    proc: subprocess.Popen | None = None
    external = args.external_host is not None
    host = args.external_host or args.host
    if args.external_out and not external:
        ap.error("--external-out 仅在与 --external-host 同时使用时有意义")
    out_eml = args.external_out if external else None

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        data_dir = tmp / "data"
        data_dir.mkdir()
        if not external:
            out_eml = tmp / "captured.eml"
        server_log = tmp / "server.log"
        build_dataset(data_dir)

        # 1) 启动（默认模式）或探测（外部模式）捕获服务器
        if external:
            if not wait_port(host, args.port, args.timeout):
                print(f"[FAIL] 外部捕获服务器不可达（{host}:{args.port}）")
                sys.exit(1)
            ok(f"连接外部捕获服务器（{host}:{args.port}）")
        else:
            proc = subprocess.Popen(
                [sys.executable, "-X", "utf8", str(CAPTURE_SCRIPT),
                 "--host", args.host, "--port", str(args.port),
                 "--out", str(out_eml), "--log", str(server_log)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if not wait_port(args.host, args.port, args.timeout, proc):
                log_text = server_log.read_text(encoding="utf-8", errors="replace") if server_log.exists() else ""
                print(f"[FAIL] 捕获服务器未就绪（端口 {args.host}:{args.port} 可能被占用）")
                if log_text:
                    print(log_text)
                sys.exit(1)
            ok(f"捕获服务器就绪（{args.host}:{args.port}）")

        # 2) 设置 SMTP 环境（指向捕获服务器；user 留空跳过 login，捕获服务器不校验认证）
        os.environ.update({
            "SMTP_HOST": host,
            "SMTP_PORT": str(args.port),
            "SMTP_SSL": "0",              # 明文/STARTTLS 端口
            "SMTP_USER": "", "SMTP_PASS": "",
            "SMTP_FROM": "alert@test", "SMTP_TO": "ops@example.com",
        })

        # 3) 完整链路：导入 → 告警检查（真实 SMTP 发送到捕获服务器）
        con = sqlite3.connect(":memory:")
        rows = import_jsonl(data_dir, con)
        ok(f"数据集导入 {rows} 条（期望 10）" if rows == 10 else f"导入异常（{rows}）")
        alerts = check_anomalies(con, 5.0)
        ok(f"告警命中 {alerts} 个租户（期望 1）" if alerts == 1 else f"告警未命中（{alerts}）")
        con.close()

        # 4) 默认模式：终止本地服务器（确保 .eml 写入完成）；外部模式由 compose 管理
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        proc = None

        # 5) 断言 .eml 内容（MIME base64 正文需解码）
        ok(f"捕获邮件落盘（{out_eml.name}）" if out_eml.exists() else "未捕获到邮件")
        mime = message_from_string(out_eml.read_text(encoding="utf-8", errors="replace"))
        payload = ""
        if mime.is_multipart():
            payload = "".join(p.get_payload(decode=True).decode("utf-8", "replace")
                              for p in mime.get_payload() if p.get_payload(decode=True))
        else:
            payload = mime.get_payload(decode=True).decode("utf-8", errors="replace")
        ok("收件人正确（ops@example.com）" if "ops@example.com" in str(mime["To"])
           else f"收件人异常（{mime['To']}）")
        ok("正文含租户占比（org_alarm ... 60%"
           if "org_alarm" in payload and "60.0%" in payload else "正文缺租户占比")
        detail_count = sum(1 for i in range(6) if f"act_{i}" in payload)
        ok(f"明细覆盖 {detail_count}/6 条异常请求" if detail_count == 6 else f"明细不全（{detail_count}/6）")

    print("\n---- 断言结果 ----")
    for c in checks:
        print(c)
    failed = [c for c in checks if not c.startswith("  [OK]")]
    if failed:
        print(f"\n测试失败：{len(failed)} 项")
        sys.exit(1)
    print(f"\n全部 {len(checks)} 项断言通过（真实 SMTP 链路：发送 → 捕获 → 落盘 → 校验）")


if __name__ == "__main__":
    main()
