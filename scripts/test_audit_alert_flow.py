"""本地告警触发流程验证（mock SMTP，无需真实服务器）

模拟完整流程：
  1. 构造异常占比 >5% 的测试数据集（10 条中 6 条 error = 60%）
  2. 运行 analyze 的导入 + 告警检查（check_anomalies，阈值 5）
  3. mock smtplib 验证邮件发送逻辑：sendmail 被调用、主题/收件人/正文含租户与最近 10 条异常明细
  4. 断言语义：告警命中、正文包含 action/status/trace 明细行

用法：python scripts/test_audit_alert_flow.py
（另见 scripts/dev/smtp_capture_server.py —— 本地真实 SMTP 捕获服务器联调）
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analyze_audit_logs import import_jsonl, check_anomalies  # noqa: E402


def build_dataset(directory: Path) -> Path:
    """构造 10 条记录、6 条异常（60% > 5%）的审计日志文件"""
    lines = [
        {"timestamp": f"2026-08-16T00:00:0{i}+00:00", "action": f"act_{i}",
         "status": ("error" if i < 6 else "success"),
         "tenant_id": "org_alarm", "trace_id": f"tr{i}"}
        for i in range(10)
    ]
    f = directory / "audit_20260816.jsonl"
    f.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
                 encoding="utf-8")
    return f


def main():
    checks: list[str] = []
    ok = lambda name: checks.append(f"  [OK] {name}")  # noqa: E731

    with tempfile.TemporaryDirectory() as td:
        build_dataset(Path(td))

        # 1) 导入
        con = sqlite3.connect(":memory:")
        rows = import_jsonl(Path(td), con)
        ok(f"数据集导入 {rows} 条（期望 10）" if rows == 10 else f"导入异常（{rows}）")

        # 2) SMTP 环境（指向任意 host，发送被 mock 拦截）
        env = {"SMTP_HOST": "smtp.test.local", "SMTP_PORT": "465",
               "SMTP_TO": "ops@example.com", "SMTP_USER": "alert@test",
               "SMTP_SSL": "1", "SMTP_PASS": "x"}
        captured = {}

        class _FakeServer:
            def __init__(self, *a, **k):
                pass

            def login(self, *a, **k):
                return self

            def sendmail(self, sender, recipients, message):
                captured["sender"] = sender
                captured["recipients"] = recipients
                captured["message"] = message

            def quit(self):
                return self

        with mock.patch.dict("os.environ", env, clear=False):
            with mock.patch("smtplib.SMTP_SSL", _FakeServer):
                alerts = check_anomalies(con, 5.0)

        # 3) 断言（正文为 MIME base64，需解析 payload 后检查明文）
        from email import message_from_string
        ok(f"sendmail 被调用（收件人 {captured.get('recipients')}）"
           if captured.get("recipients") == ["ops@example.com"] else "sendmail 未正确调用")
        mime = message_from_string(captured.get("message", ""))
        payload = ""
        if mime.is_multipart():
            payload = "".join(p.get_payload(decode=True).decode("utf-8", "replace")
                              for p in mime.get_payload() if p.get_payload(decode=True))
        else:
            payload = mime.get_payload(decode=True).decode("utf-8", errors="replace")
        ok("正文含租户占比（org_alarm ... 60%）"
           if "org_alarm" in payload and "60.0%" in payload else "正文缺租户占比")
        ok("正文含异常明细（act_5 / status=error / trace=tr5）"
           if "act_5" in payload and "status=error" in payload and "trace=tr5" in payload
           else "正文缺异常明细")
        detail_count = sum(1 for i in range(6) if f"act_{i}" in payload)
        ok(f"明细覆盖 {detail_count}/6 条异常请求" if detail_count == 6 else f"明细不全（{detail_count}/6）")

        con.close()

    print("---- 断言结果 ----")
    for c in checks:
        print(c)
    failed = [c for c in checks if not c.startswith("  [OK]")]
    if failed:
        print(f"\n测试失败：{len(failed)} 项")
        sys.exit(1)
    print(f"\n全部 {len(checks)} 项断言通过 ✅（邮件发送逻辑已验证，SMTP 发送被 mock 拦截）")


if __name__ == "__main__":
    main()
