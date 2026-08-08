#!/usr/bin/env python3
"""修复生产 Alertmanager：空目录配置 → 邮件告警配置文件 + 启动验证。

【背景】2026-08-07 快照发现 deploy/monitoring/prometheus/alertmanager.yml
是空目录（非文件），bind-mount 后 Alertmanager 启动即崩（Exited 127，宕机 11 天）。
本脚本修复:
  1. 校验该路径为空目录后删除（非空则中止，守不易）
  2. 写入邮件告警配置（收件 13539371839@139.com，139 邮箱 SMTP）
  3. docker run --check-config 校验配置语法
  4. docker start 启动容器
  5. 容器内健康端点探测验证

【SMTP 密码】smtp_auth_password 为 139 邮箱 SMTP 授权码（非登录密码），
需用户填入后告警邮件才能发出；Alertmanager 启动不依赖 SMTP 认证，
故本脚本验证"能否正常启动"不受影响。

用法: python scripts/repair_alertmanager.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parents[1] / "deploy" / "monitoring" / "prometheus" / "alertmanager.yml"
CONTAINER = "yunshu-prod-alertmanager"
IMAGE = "prom/alertmanager:v0.27.0"

CONFIG_CONTENT = """# 生产 Alertmanager 配置（2026-08-07 重建：原路径为空目录导致容器启动失败）
# 告警路由：全量告警 → 邮件通知 13539371839@139.com
# ⚠️ smtp_auth_password 需替换为 139 邮箱的 SMTP 授权码（设置页生成，非登录密码）
global:
  smtp_smarthost: 'smtp.139.com:465'
  smtp_from: '13539371839@139.com'
  smtp_auth_username: '13539371839@139.com'
  smtp_auth_password: 'REPLACE_WITH_SMTP_AUTH_CODE'
  smtp_require_tls: true

route:
  group_by: ['alertname', 'team']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: 'email-ops'

receivers:
  - name: 'email-ops'
    email_configs:
      - to: '13539371839@139.com'
        send_resolved: true
"""


def run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def main() -> int:
    # ── 1. 清理空目录 ─────────────────────────────────────────────────────
    if CONFIG_FILE.is_dir():
        entries = list(CONFIG_FILE.iterdir())
        if entries:
            print(f"[ABORT] {CONFIG_FILE} 是非空目录（{len(entries)} 项），拒绝删除", file=sys.stderr)
            return 2
        CONFIG_FILE.rmdir()
        print(f"[ok] 已删除空目录: {CONFIG_FILE}")
    elif CONFIG_FILE.is_file():
        print(f"[info] 已是文件（跳过删除）: {CONFIG_FILE}")
    else:
        print(f"[info] 路径不存在，将创建文件: {CONFIG_FILE}")

    # ── 2. 写入邮件告警配置 ───────────────────────────────────────────────
    CONFIG_FILE.write_text(CONFIG_CONTENT, encoding="utf-8")
    print(f"[ok] 已写入配置（{len(CONFIG_CONTENT)}B，收件 {CONFIG_FILE}）")

    # ── 3. 配置语法校验（Python YAML + Alertmanager --check-config 双保险）──
    try:
        import yaml  # noqa: PLC0415
        data = yaml.safe_load(CONFIG_CONTENT)
        assert data and data.get("receivers") and data["receivers"][0]["email_configs"], "配置结构不完整"
        print("[ok] YAML 语法校验通过")
    except Exception as exc:
        print(f"[ERROR] YAML 校验失败: {exc}", file=sys.stderr)
        return 2

    r = run(["docker", "run", "--rm", "--entrypoint", "amtool",
             "-v", f"{CONFIG_FILE}:/tmp/alertmanager.yml:ro",
             IMAGE, "check-config", "/tmp/alertmanager.yml"])
    if r.returncode != 0:
        print(f"[ERROR] amtool check-config 失败:\n{r.stderr[:800]}\n{r.stdout[:800]}", file=sys.stderr)
        return 2
    print("[ok] amtool check-config 通过（容器镜像校验）")

    # ── 4. 启动容器 ───────────────────────────────────────────────────────
    r = run(["docker", "start", CONTAINER])
    if r.returncode != 0:
        print(f"[ERROR] docker start 失败: {r.stderr[:400]}", file=sys.stderr)
        return 1
    print(f"[ok] docker start {CONTAINER}")

    # ── 5. 健康验证（容器内探测，端口未映射到宿主机）────────────────────────
    for attempt in range(6):
        time.sleep(3)
        r = run(["docker", "inspect", CONTAINER, "--format", "{{.State.Status}}"])
        status = r.stdout.strip()
        if status == "running":
            health = run(["docker", "exec", CONTAINER, "sh", "-c",
                          "wget -qO- http://127.0.0.1:9093/-/healthy 2>/dev/null || curl -sf http://127.0.0.1:9093/-/healthy 2>/dev/null || echo DOWN"])
            body = health.stdout.strip()[:120]
            # v0.27 的 /-/healthy 返回 "OK"；空/DOWN 视为不健康
            if body and "DOWN" not in body:
                print(f"[ok] 健康探测通过: {body}")
                break
            print(f"[{attempt+1}/6] 容器运行中，健康探测: {body or '(空)'}")
        else:
            print(f"[{attempt+1}/6] 容器状态: {status}")
    else:
        logs = run(["docker", "logs", CONTAINER, "--tail", "20"]).stdout
        print(f"[ERROR] 启动验证超时，最近日志:\n{logs[-1500:]}", file=sys.stderr)
        return 1

    print("✅ Alertmanager 修复完成。注意: smtp_auth_password 需填入 139 邮箱授权码后方可发送邮件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
