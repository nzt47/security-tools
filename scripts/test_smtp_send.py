"""真实 SMTP 发信链路测试（上线前检查单 B 项）

复用生产发送函数（【不易】：与告警同一链路）：
  - scripts/analyze_audit_logs.send_mail / load_smtp_config
发送一封测试邮件，校验 sendmail 真实成功，退出码 0/1。

用法：
  python scripts/test_smtp_send.py [--subject 标题] [--to 收件人]

配置来源（优先级同告警）：
  环境变量 > .env（SMTP_HOST/PORT/USER/PASS/TO/SSL；SSL=0 走明文/STARTTLS）

本地无真实 SMTP 时联调（捕获服务器验证协议链路）：
  # 终端1: python scripts/dev/smtp_capture_server.py --port 2525 --out captured.eml
  # 终端2（设置环境变量后运行）:
  #   $env:SMTP_HOST='127.0.0.1'; $env:SMTP_PORT='2525'; $env:SMTP_SSL='0'
  #   $env:SMTP_USER=''; $env:SMTP_PASS=''; $env:SMTP_FROM='alert@test'
  #   python scripts/test_smtp_send.py --to ops@example.com
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analyze_audit_logs import load_smtp_config, send_mail  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="真实 SMTP 发信链路测试")
    ap.add_argument("--subject", default="[云枢] SMTP 链路测试邮件",
                    help="邮件标题（默认：云枢 SMTP 链路测试）")
    ap.add_argument("--to", default="", help="收件人；缺省读 .env SMTP_TO")
    args = ap.parse_args()

    cfg = load_smtp_config()
    if args.to:
        cfg["to"] = args.to

    print(f"[测试] SMTP 配置: host={cfg['host']} port={cfg['port']} "
          f"ssl={'SSL' if cfg['use_ssl'] else '明文/STARTTLS'} to={cfg['to']}")
    if not (cfg["host"] and cfg["to"]):
        print("[FAIL] 未配置 SMTP_HOST / SMTP_TO（.env 缺失或为空）")
        print("       先复制 .env.example 的「审计日志告警配置」段到 .env 并填真实值")
        sys.exit(1)

    body = (
        "这是一封由 scripts/test_smtp_send.py 发送的 SMTP 链路测试邮件。\n"
        "收到即代表邮件发送链路正常（告警邮件将走同一发送函数）。\n"
        "来源: scripts/test_smtp_send.py"
    )
    ok = send_mail(args.subject, body, cfg)
    print(f"\n{'[OK] SMTP 发信链路正常（退出码 0）' if ok else '[FAIL] 发信失败（详见上方 WARN）'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
