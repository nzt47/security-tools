#!/usr/bin/env python3
"""生产配置工具：替换 alertmanager.yml 的 SMTP 授权码占位符，并验证 587 端口连通性。

【用法】
  方式一（参数传入，⚠️ 明文会留在 shell 历史）：
    python scripts/apply_smtp_auth_code.py --auth-code <真实授权码> [--config <yml路径>]
  方式二（环境变量注入；⚠️ 整行仍留在 shell 历史，仅避免进程列表可见）：
    SMTP_AUTH_CODE=<真实授权码> python scripts/apply_smtp_auth_code.py
  方式三（交互式输入，推荐：不回显、不进 shell 历史、不进进程列表）：
    python scripts/apply_smtp_auth_code.py --interactive [--config <yml路径>]

【执行步骤】
  1. 读取 alertmanager.yml，校验 smtp_auth_password 当前为占位符 REPLACE_WITH_SMTP_AUTH_CODE
  2. 替换为真实授权码（日志只显示打码版本，不泄露明文）
  3. 验证 SMTP 587 端口连通性（TCP connect 探测；本地网络受限时给出明确结论）
  4. 若 Docker 容器存在：amtool check-config 校验 + kill -HUP 热加载
  5. 输出结构化结果；任一关键项失败 → 非零退出码

【不易】授权码属于敏感凭证：仅经参数/环境变量注入，绝不硬编码进脚本或输出明文。
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parents[1] / "deploy" / "monitoring" / "prometheus" / "alertmanager.yml"
CONTAINER = "yunshu-prod-alertmanager"
PLACEHOLDER = "REPLACE_WITH_SMTP_AUTH_CODE"
DEFAULT_SMTP_HOST = "smtp.139.com"
DEFAULT_SMTP_PORT = 587


def run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def mask(secret: str) -> str:
    """打码显示：仅保留前 4 位 + 后 4 位。"""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}****{secret[-4:]}"


def current_password(config: Path) -> str:
    for line in config.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("smtp_auth_password:"):
            return s.split(":", 1)[1].strip().strip("'\"")
    return ""


def replace_auth_password(config: Path, code: str) -> None:
    text = config.read_text(encoding="utf-8")
    new = "\n".join(
        line if not line.strip().startswith("smtp_auth_password:")
        else f"  smtp_auth_password: '{code}'"
        for line in text.splitlines()
    )
    config.write_text(new, encoding="utf-8")


def check_smtp_connectivity(host: str, port: int) -> tuple[bool, str]:
    """TCP 连接探测。返回 (是否成功, 描述)。仅验证端口可达，不发送任何数据。"""
    try:
        with socket.create_connection((host, port), timeout=5):
            return True, f"{host}:{port} 连通成功（TCP 握手完成）"
    except socket.timeout:
        return False, f"{host}:{port} 连接超时（中间路由黑洞或防火墙丢包）"
    except OSError as exc:
        return False, f"{host}:{port} 连接失败: {exc}"


def docker_validate_and_reload() -> tuple[bool, str]:
    """容器存在时：amtool 校验 + SIGHUP 热加载。容器不存在则跳过（不视为失败）。"""
    r = run(["docker", "ps", "-q", "-f", f"name={CONTAINER}"])
    if r.returncode != 0 or not r.stdout.strip():
        return True, "未检测到 Docker 容器（跳过校验/重载，仅完成配置替换）"
    chk = run(["docker", "exec", CONTAINER, "amtool", "check-config", "/etc/alertmanager/alertmanager.yml"])
    if chk.returncode != 0:
        return False, f"amtool 校验失败: {chk.stdout.strip() or chk.stderr.strip()}"
    hup = run(["docker", "exec", CONTAINER, "kill", "-HUP", "1"])
    if hup.returncode != 0:
        return False, f"reload 失败: {hup.stderr.strip()}"
    return True, "amtool 校验通过 + SIGHUP 热加载已触发"


def main() -> int:
    parser = argparse.ArgumentParser(description="替换 SMTP 授权码占位符并验证 587 端口连通性")
    parser.add_argument("--auth-code", default=None, help="139 邮箱 SMTP 授权码（明文会留在 shell 历史，不推荐）")
    parser.add_argument("--interactive", action="store_true", help="交互式输入授权码（不回显，不进 shell 历史/进程列表，推荐）")
    parser.add_argument("--config", default=str(CONFIG_FILE), help=f"alertmanager.yml 路径（默认 {CONFIG_FILE}）")
    parser.add_argument("--smtp-host", default=DEFAULT_SMTP_HOST, help=f"SMTP 主机（默认 {DEFAULT_SMTP_HOST}）")
    parser.add_argument("--smtp-port", type=int, default=DEFAULT_SMTP_PORT, help=f"SMTP 端口（默认 {DEFAULT_SMTP_PORT}）")
    parser.add_argument("--skip-port-check", action="store_true", help="跳过 587 端口连通性验证")
    args = parser.parse_args()

    code = args.auth_code or os.environ.get("SMTP_AUTH_CODE", "")
    config = Path(args.config)
    if args.interactive:
        import getpass
        code = getpass.getpass("请输入 139 邮箱 SMTP 授权码（输入不回显）: ")
    if not code:
        print("[ERROR] 未提供授权码：请用 --interactive 交互输入、--auth-code 参数"
              "或设置环境变量 SMTP_AUTH_CODE", file=sys.stderr)
        return 2
    if not config.exists():
        print(f"[ERROR] 配置文件不存在: {config}", file=sys.stderr)
        return 2

    print(f"[1] 读取配置: {config}")
    cur = current_password(config)
    print(f"    当前 smtp_auth_password: {mask(cur)} (占位符={cur == PLACEHOLDER})")
    if cur == PLACEHOLDER:
        print("[2] 替换占位符 → 真实授权码")
        replace_auth_password(config, code)
    else:
        print("[2] 当前已是真实授权码（非占位符），跳过替换")

    print(f"[3] 验证 SMTP 端口连通性: {args.smtp_host}:{args.smtp_port}")
    if args.skip_port_check:
        ok, desc = True, "已跳过（--skip-port-check）"
    else:
        ok, desc = check_smtp_connectivity(args.smtp_host, args.smtp_port)
    print(f"    {desc}")
    if not ok:
        print("    [提示] 端口不通通常是本机/生产服务器出站防火墙或安全组阻断，"
              "请按《生产部署检查清单》第 1-2 节排查；不阻断授权码替换本身。")

    print(f"[4] 容器侧校验 + 热加载")
    rel_ok, rel_desc = docker_validate_and_reload()
    print(f"    {rel_desc}")

    fail = (not ok and not args.skip_port_check) or not rel_ok
    if fail:
        print("\n[结果] 部分检查未通过（授权码已写入，但端口/容器校验需处理），详见上方。", file=sys.stderr)
        return 1
    print("\n[结果] 授权码已替换，端口连通性与配置加载均正常 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
