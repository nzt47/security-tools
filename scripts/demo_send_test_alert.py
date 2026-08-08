#!/usr/bin/env python3
"""演示脚本：模拟填入 SMTP 授权码 → 触发测试告警邮件 → 验证整条告警链路。

【链路分层验证】（诚实：邮件发送依赖真实 SMTP 授权码，无法编造）
  段 A  Prometheus → Alertmanager 推送/接收（不依赖 SMTP，可完整验证）
       - POST /api/v2/alerts 注入测试告警 → 查询确认 Alertmanager 已接收
  段 B  Alertmanager → SMTP 邮件发送（依赖 smtp_auth_password 有效性）
       - 读取当前配置；--smtp-auth-code 提供时替换占位符并重启
       - 查容器日志: "Notify for alerts completed" = 成功；SMTP 认证错误 = 授权码无效

【安全性】演示结束后自动将 smtp_auth_password 还原为占位符并重启，
         保持生产配置干净（--keep-code 可保留填入的授权码）。

用法:
  python scripts/demo_send_test_alert.py                          # 用当前配置触发
  python scripts/demo_send_test_alert.py --smtp-auth-code <code>  # 模拟填入授权码后触发
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parents[1] / "deploy" / "monitoring" / "prometheus" / "alertmanager.yml"
ALERTMANAGER_BASE = "http://127.0.0.1:9093"
CONTAINER = "yunshu-prod-alertmanager"
PLACEHOLDER = "REPLACE_WITH_SMTP_AUTH_CODE"
TEST_ALERT = {
    "labels": {
        "alertname": "LinkCacheTestAlert",
        "severity": "test",
        "team": "knowledge",
    },
    "annotations": {
        "summary": "链路测试告警（自动触发，用于验证通知链路）",
        "description": "此告警由 demo_send_test_alert.py 触发，非真实故障",
    },
}


def run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def http_post(path: str, payload) -> int:
    req = urllib.request.Request(
        ALERTMANAGER_BASE + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def http_get(path: str) -> dict:
    try:
        with urllib.request.urlopen(ALERTMANAGER_BASE + path, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}


def get_auth_password() -> str:
    text = CONFIG_FILE.read_text(encoding="utf-8")
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("smtp_auth_password:"):
            return s.split(":", 1)[1].strip().strip("'\"")
    return ""


def set_auth_password(code: str) -> None:
    text = CONFIG_FILE.read_text(encoding="utf-8")
    new = "\n".join(
        line if not line.strip().startswith("smtp_auth_password:")
        else f"  smtp_auth_password: '{code}'"
        for line in text.splitlines()
    )
    CONFIG_FILE.write_text(new, encoding="utf-8")


def restart_alertmanager() -> bool:
    r = run(["docker", "restart", CONTAINER])
    if r.returncode != 0:
        print(f"[ERROR] docker restart 失败: {r.stderr[:300]}", file=sys.stderr)
        return False
    for _ in range(10):
        time.sleep(3)
        try:
            with urllib.request.urlopen(ALERTMANAGER_BASE + "/-/healthy", timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
    return False


def check_notification_logs() -> None:
    """查最近日志中邮件通知结果（group_wait=30s，告警后 ~35s 才有结果）。"""
    time.sleep(40)
    r = run(["docker", "logs", CONTAINER, "--since", "2m"], timeout=30)
    logs = r.stdout
    completed = "Notify for alerts completed" in logs
    failed = "Notify attempt failed" in logs
    auth_err = ("login" in logs.lower() and "error" in logs.lower()) or ("rejected" in logs.lower())
    print(f"\n[日志检视] notify completed={completed} notify_failed={failed} smtp_auth_error={auth_err}")
    # 打印关键日志片段
    for keyword in ("Notify for alerts", "Notify attempt failed", "SMTP", "login", "rejected", "authentication", "dial tcp"):
        for line in logs.splitlines():
            if keyword in line:
                print(f"   | {line.strip()[:200]}")
    return


def main() -> int:
    parser = argparse.ArgumentParser(description="触发测试告警并验证链路")
    parser.add_argument("--smtp-auth-code", default=None, help="模拟填入的 139 邮箱 SMTP 授权码")
    parser.add_argument("--keep-code", action="store_true", help="演示结束后保留授权码（不还原占位符）")
    args = parser.parse_args()

    orig = get_auth_password()
    print(f"[1] 当前 smtp_auth_password: {orig[:8]}... (占位符={orig == PLACEHOLDER})")

    if args.smtp_auth_code:
        print(f"[2] 模拟填入授权码（{args.smtp_auth_code[:4]}...）并重启 Alertmanager")
        set_auth_password(args.smtp_auth_code)
        if not restart_alertmanager():
            print("[ERROR] 重启后健康检查失败", file=sys.stderr)
            return 1
        print("    Alertmanager 已重启且健康")
    else:
        print("[2] 使用当前配置（未提供授权码）")

    print("[3] 注入测试告警 → Alertmanager API /api/v2/alerts")
    # 唯一 instance 标签：避免与历史测试告警去重（相同 labels 不会触发新通知）
    alert = {**TEST_ALERT, "labels": {**TEST_ALERT["labels"], "instance": f"demo-{int(time.time())}"}}
    code = http_post("/api/v2/alerts", [alert])
    print(f"    POST 返回 HTTP {code}（200=已接收）")
    if code != 200:
        print("[ERROR] 测试告警注入失败，链路 A 不通", file=sys.stderr)
        return 1

    time.sleep(2)
    alerts = http_get("/api/v2/alerts")
    # /api/v2/alerts 返回数组（list）；异常时返回 {}
    alert_list = alerts if isinstance(alerts, list) else alerts.get("data", [])
    found = any(a.get("labels", {}).get("alertname") == "LinkCacheTestAlert" and a.get("labels", {}).get("instance", "").startswith("demo-") for a in alert_list)
    print(f"[4] Alertmanager 已接收测试告警: {found}")
    if not found:
        print("[WARN] 未在 /api/v2/alerts 查到测试告警（可能已进入静默/分组）")

    print("[5] 等待通知发送（group_wait=30s）并检视日志")
    check_notification_logs()

    # 清理：resolve 测试告警，避免 repeat 噪音
    resolved = dict(alert)
    resolved["status"] = "resolved"
    resolved["endsAt"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    http_post("/api/v2/alerts", [resolved])
    print("[6] 测试告警已 resolve（清理完成）")

    if args.smtp_auth_code and not args.keep_code:
        print("[7] 还原占位符并重启（保持生产配置干净）")
        set_auth_password(PLACEHOLDER)
        restart_alertmanager()
        print("    已还原")

    print("\n结论: 链路 A（注入/接收）应通过；链路 B（SMTP 邮件）结果见上方日志检视。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
