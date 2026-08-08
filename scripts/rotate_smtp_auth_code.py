#!/usr/bin/env python3
"""139 邮箱 SMTP 授权码：批量作废引导 + 重新生成注入 + 新码生效验证。

【背景】凭据泄露响应的一部分：旧授权码可能已随 _edge_profile 泄露，必须作废重生成。
注意：139 邮箱授权码的【作废与重新生成】是网页操作（无公开 API），本脚本负责：
  1) list   —— 列出当前配置中的授权码状态（打码，不泄露明文）
  2) guide  —— 输出作废/重生成的操作清单（人工在设置页执行）
  3) inject —— 注入新授权码（交互式，getpass 不回显）
  4) verify —— 自动化验证新码生效（SMTP 587 + STARTTLS + AUTH + 发送测试邮件）

【用法】
  python scripts/rotate_smtp_auth_code.py list
  python scripts/rotate_smtp_auth_code.py guide
  python scripts/rotate_smtp_auth_code.py inject [--config <yml>]   # 交互输入新码
  python scripts/rotate_smtp_auth_code.py verify [--to 收件邮箱]    # 用配置中的新码发测试信
  # 本地无外网时用 mock 验证逻辑：
  python scripts/rotate_smtp_auth_code.py verify --local-mock

【不易】授权码为敏感凭证：日志只打码；inject 走 getpass；verify 失败不输出明文。
"""

from __future__ import annotations

import argparse
import os
import smtplib
import socket
import subprocess
import sys
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parents[1] / "deploy" / "monitoring" / "prometheus" / "alertmanager.yml"
PLACEHOLDER = "REPLACE_WITH_SMTP_AUTH_CODE"
SMTP_HOST = "smtp.139.com"
SMTP_PORT = 587
RECIPIENT = "13539371839@139.com"


def mask(secret: str) -> str:
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}****{secret[-4:]}"


def current_password(config: Path) -> str:
    if not config.exists():
        return ""
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


def cmd_list(args) -> int:
    cur = current_password(Path(args.config))
    if not cur:
        print(f"[list] 未找到配置文件 {args.config} 或其中无 smtp_auth_password")
        return 2
    is_ph = cur == PLACEHOLDER
    print(f"[list] 配置: {args.config}")
    print(f"       当前值: {mask(cur)}  占位符={is_ph}")
    if is_ph:
        print("       状态: 未注入（安全，无泄露值）")
    else:
        print("       ⚠️ 状态: 已注入真实授权码")
        print("       ⚠️ 若此码曾随 _edge_profile 泄露 → 必须作废重生成（见 guide）")
    return 0


def cmd_guide(args) -> int:
    print("""[guide] 139 邮箱 SMTP 授权码作废与重新生成（人工在网页执行，无公开 API）

  ① 登录 139 邮箱网页版（mail.139.com）
  ② 设置 → 客户端设置 / POP3·SMTP /IMAP → 开启并管理 SMTP 授权码
  ③ 在授权码列表中找到当前使用的授权码（若记录过打码前4后4可对照）
  ④ 点击【作废/删除】→ 确认（旧码立即失效，告警链路将停止发送——先通知值班）
  ⑤ 重新点击【生成授权码】→ 按提示完成短信/安全验证 → 生成新码
  ⑥ 复制新码 → 本机执行注入：
       python scripts/rotate_smtp_auth_code.py inject
     （交互式输入，getpass 不回显、不进 shell 历史）
  ⑦ 验证新码生效：
       python scripts/rotate_smtp_auth_code.py verify --to 13539371839@139.com
     （SMTP 587 + STARTTLS + AUTH + 发送测试邮件；需外网可达）
  ⑧ 收件确认：邮箱收到测试信（含垃圾箱）→ 轮换完成

  ⚠️ 作废旧码前确保已取得新码或留有备用通道，避免告警链路中断。
  ⚠️ 作废/生成动作会记录在 139 邮箱安全日志中，留存备查。""")
    return 0


def cmd_inject(args) -> int:
    config = Path(args.config)
    if not config.exists():
        print(f"[ERROR] 配置文件不存在: {config}", file=sys.stderr)
        return 2
    code = args.auth_code or os.environ.get("SMTP_AUTH_CODE", "")
    if args.interactive:
        import getpass
        code = getpass.getpass("请输入新的 139 邮箱 SMTP 授权码（输入不回显）: ")
    if not code:
        print("[ERROR] 未提供新授权码：用 --interactive 交互输入或设置 SMTP_AUTH_CODE", file=sys.stderr)
        return 2
    cur = current_password(config)
    if cur == PLACEHOLDER:
        print(f"[inject] 占位符 → 替换新授权码 {mask(code)}")
    else:
        print(f"[inject] 替换旧授权码 {mask(cur)} → 新授权码 {mask(code)}")
    replace_auth_password(config, code)
    print(f"[inject] 已写入: {config}")
    print("        提示: 生产环境需触发 Alertmanager 热加载（amtool + SIGHUP），再执行 verify")
    return 0


def smtp_verify(code: str, to: str, host: str = SMTP_HOST, port: int = SMTP_PORT,
                timeout: int = 10) -> tuple[bool, str]:
    """SMTP 587 + STARTTLS + AUTH + 发送测试邮件。返回 (是否成功, 描述)。"""
    try:
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login("13539371839@139.com", code)
            msg = (
                "From: 13539371839@139.com\r\n"
                f"To: {to}\r\n"
                "Subject: [AuthCodeRotate] SMTP 新授权码验证测试\r\n"
                "\r\n"
                "本邮件由 rotate_smtp_auth_code.py 发送，用于验证新授权码生效。\r\n"
            )
            smtp.sendmail("13539371839@139.com", [to], msg)
        return True, f"AUTH 成功并已发送测试邮件至 {to}"
    except smtplib.SMTPAuthenticationError as exc:
        return False, f"AUTH 失败（535 授权码错误）: {exc.smtp_code}"
    except smtplib.SMTPNotSupportedError as exc:
        return False, f"STARTTLS 不受支持（确认 587 端口）: {exc}"
    except (smtplib.SMTPException, OSError, socket.timeout) as exc:
        return False, f"SMTP 连接/发送异常: {exc}"


def cmd_verify(args) -> int:
    config = Path(args.config)
    code = current_password(config)
    if code == PLACEHOLDER or not code:
        print("[verify] 配置中仍是占位符或为空 → 请先 inject 新授权码", file=sys.stderr)
        return 1
    print(f"[verify] 使用当前配置授权码 {mask(code)} 验证（SMTP {SMTP_HOST}:{SMTP_PORT}）")
    if args.local_mock:
        from smtp_mock import run_verify_mock  # 延迟导入，见 scripts/smtp_mock.py
        ok, desc = run_verify_mock(code, fail_auth=args.mock_fail)
    else:
        ok, desc = smtp_verify(code, args.to)
    print(f"[verify] {'✓' if ok else '✗'} {desc}")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="139 邮箱 SMTP 授权码轮换工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出当前授权码状态")
    p_list.add_argument("--config", default=str(CONFIG_FILE))

    sub.add_parser("guide", help="输出作废/重生成网页操作清单")

    p_inj = sub.add_parser("inject", help="注入新授权码")
    p_inj.add_argument("--config", default=str(CONFIG_FILE))
    p_inj.add_argument("--auth-code", default=None, help="⚠️ 明文进 shell 历史，不推荐")
    p_inj.add_argument("--interactive", action="store_true", help="交互式输入（推荐）")

    p_ver = sub.add_parser("verify", help="验证新授权码生效")
    p_ver.add_argument("--config", default=str(CONFIG_FILE))
    p_ver.add_argument("--to", default=RECIPIENT, help="测试邮件收件地址")
    p_ver.add_argument("--local-mock", action="store_true", help="本地模拟验证（无外网时）")
    p_ver.add_argument("--mock-fail", action="store_true", help="本地模拟 AUTH 失败（测试 535 分支）")

    args = parser.parse_args()
    handlers = {"list": cmd_list, "guide": cmd_guide, "inject": cmd_inject, "verify": cmd_verify}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
