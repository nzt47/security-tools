#!/usr/bin/env python3
"""139 邮箱 SMTP 授权码：多账号【批量作废引导 + 重生成注入 + 新码生效批量验证】。

【背景】凭据泄露响应：_edge_profile 泄露后，139 邮箱的 SMTP 授权码必须作废重生成。
139 邮箱的【作废与重新生成】是网页操作（无公开 API），本脚本以清单驱动批量处理：
  1) list   —— 列出清单中全部账号的授权码状态（打码，不泄露明文）
  2) guide  —— 输出每个账号的网页作废/重生成操作步骤（人工在 mail.139.com 执行）
  3) inject —— 逐账号交互注入新授权码（getpass 不回显），写入状态文件（0600）
  4) verify —— 逐账号自动验证新码生效（SMTP 587 + STARTTLS + AUTH + 发送测试信）

【用法】
  python scripts/rotate_smtp_auth_codes_batch.py list
  python scripts/rotate_smtp_auth_codes_batch.py guide [--email 13539371839@139.com]
  python scripts/rotate_smtp_auth_codes_batch.py inject --interactive [--email 账号]
  python scripts/rotate_smtp_auth_codes_batch.py verify [--email 账号] [--local-mock]

【清单】scripts/smtp_auth_codes.manifest.json（只含账号元数据；授权码不落清单）
【状态】scripts/.smtp_auth_codes.json（inject 后暂存新码，0600；verify 读取）

【不易】授权码为敏感凭证：日志/输出一律打码；inject 走 getpass；状态文件 0600；
verify 失败仅输出错误类别（535/TLS/网络），不打印授权码。
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import socket
import sys
from email.message import EmailMessage
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MANIFEST_DEFAULT = SCRIPT_DIR / "smtp_auth_codes.manifest.json"
STATE_DEFAULT = SCRIPT_DIR / ".smtp_auth_codes.json"
SMTP_HOST = "smtp.139.com"
SMTP_PORT = 587
PLACEHOLDER = "REPLACE_WITH_SMTP_AUTH_CODE"


def mask(secret: str) -> str:
    """打码：前 4 + **** + 后 4；过短全星。"""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}****{secret[-4:]}"


def load_manifest(path: Path) -> list[dict]:
    if not path.exists():
        print(f"[ERROR] 清单不存在: {path}", file=sys.stderr)
        sys.exit(2)
    data = json.loads(path.read_text(encoding="utf-8"))
    accounts = data.get("accounts", [])
    for a in accounts:
        if not a.get("email") or "REPLACE_WITH" in str(a.get("email", "")):
            print(f"[ERROR] 清单中存在未填写的账号占位符，请先在 {path} 填写: {a}", file=sys.stderr)
            sys.exit(2)
    return accounts


def load_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(path: Path, state: dict[str, str]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)  # 仅本人可读写（Windows 上尽力而为）
    except OSError:
        pass


def smtp_verify_code(email: str, code: str, to: str, host: str = SMTP_HOST,
                     port: int = SMTP_PORT, timeout: int = 10,
                     require_tls: bool = True) -> tuple[bool, str]:
    """单个账号 SMTP + AUTH + 发送测试邮件。返回 (是否成功, 描述)。
    require_tls=False 用于本地 mock（mock 不实现 STARTTLS，仅验证 AUTH 逻辑）。"""
    try:
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            smtp.ehlo()
            if require_tls:
                smtp.starttls()
                smtp.ehlo()
            smtp.login(email, code)
            msg = EmailMessage()
            msg["From"] = email
            msg["To"] = to
            msg["Subject"] = "[AuthCodeRotate] SMTP 新授权码批量验证测试"
            msg.set_content("本邮件由 rotate_smtp_auth_codes_batch.py 发送，用于验证新授权码生效。")
            smtp.send_message(msg)
        return True, "AUTH 成功并已发送测试邮件"
    except smtplib.SMTPAuthenticationError as exc:
        return False, f"AUTH 失败（535 授权码错误）: {exc.smtp_code}"
    except smtplib.SMTPNotSupportedError as exc:
        return False, f"STARTTLS 不受支持（确认 587 端口）: {exc}"
    except (smtplib.SMTPException, OSError, socket.timeout) as exc:
        return False, f"SMTP 连接/发送异常: {exc}"


def verify_with_mock(email: str, code: str, fail_auth: bool = False) -> tuple[bool, str]:
    """本地 mock 验证（无外网时）：复用 smtp_mock 的最小 SMTP 服务。"""
    import smtp_mock  # 同目录（scripts/smtp_mock.py）
    server = smtp_mock.start_mock_server(port=0, auth_password=code, fail_auth=fail_auth)
    try:
        ok, desc = smtp_verify_code(email, code, email, host="127.0.0.1",
                                    port=server.server_address[1], timeout=5,
                                    require_tls=False)  # mock 无 STARTTLS，仅验 AUTH
    finally:
        server.shutdown()  # serve_forever 运行于 daemon 线程，此处调用安全
        server.server_close()
    return ok, ("[mock] " + desc)


def cmd_list(args) -> int:
    accounts = load_manifest(Path(args.manifest))
    state = load_state(Path(args.state))
    print(f"[list] 清单账号数: {len(accounts)}   已注入新码账号数: {len(state)}")
    for a in accounts:
        email = a["email"]
        code = state.get(email, "")
        if not code:
            print(f"  · {email}  状态: 未注入（等待作废重生成）")
        elif code == PLACEHOLDER:
            print(f"  · {email}  状态: 占位符")
        else:
            print(f"  · {email}  状态: 已注入新码 {mask(code)}  → 可 verify")
    return 0


def cmd_guide(args) -> int:
    accounts = load_manifest(Path(args.manifest))
    if args.email:
        accounts = [a for a in accounts if a["email"] == args.email]
        if not accounts:
            print(f"[ERROR] 清单中无该账号: {args.email}", file=sys.stderr)
            return 2
    print(f"[guide] 共 {len(accounts)} 个账号：139 邮箱授权码【作废+重生成】网页操作（无公开 API）")
    for i, a in enumerate(accounts, 1):
        print(f"\n── 账号 {i}/{len(accounts)}: {a['email']}（{a.get('note','')}）──")
        print("""  ① 登录 mail.139.com（建议隐私窗口/换设备，勿用泄露过的会话）
  ② 设置 → 客户端设置 → POP3/SMTP/IMAP → 管理 SMTP 授权码
  ③ 在列表中找到当前授权码 → 点击【作废/删除】→ 确认
     ⚠️ 作废后旧码立即失效（告警链路停发）→ 先通知值班，再逐个操作
  ④ 重新【生成授权码】→ 短信/安全验证 → 生成新码并复制
  ⑤ 在本机注入新码：
     python scripts/rotate_smtp_auth_codes_batch.py inject --interactive --email <本账号>
  ⑥ 验证新码：
     python scripts/rotate_smtp_auth_codes_batch.py verify --email <本账号>
     （外网不通时: 加 --local-mock 验证 AUTH 逻辑）
  ⑦ 收件箱（含垃圾箱）确认收到测试信 → 该账号轮换完成""")
    print("\n[guide] 全部账号操作完成后，可批量 verify 收尾。")
    return 0


def cmd_inject(args) -> int:
    accounts = load_manifest(Path(args.manifest))
    if args.email:
        accounts = [a for a in accounts if a["email"] == args.email]
        if not accounts:
            print(f"[ERROR] 清单中无该账号: {args.email}", file=sys.stderr)
            return 2
    state_path = Path(args.state)
    state = load_state(state_path)
    import getpass
    for a in accounts:
        email = a["email"]
        if args.auth_code and len(accounts) == 1:
            code = args.auth_code
        elif args.interactive:
            code = getpass.getpass(f"[inject] 请输入 {email} 的新 SMTP 授权码（输入不回显）: ")
        else:
            code = os.environ.get(f"SMTP_AUTH_CODE_{email.split('@')[0]}", "")
        if not code:
            print(f"[inject] 跳过 {email}: 未提供授权码（用 --interactive）", file=sys.stderr)
            continue
        old = state.get(email, "")
        state[email] = code
        print(f"[inject] {email}: {mask(old) if old else '空'} → 新码 {mask(code)}")
    save_state(state_path, state)
    print(f"[inject] 已写入状态文件: {state_path}（权限 0600）")
    return 0


def cmd_verify(args) -> int:
    accounts = load_manifest(Path(args.manifest))
    if args.email:
        accounts = [a for a in accounts if a["email"] == args.email]
    state_path = Path(args.state)
    state = load_state(state_path)
    passed = failed = 0
    for a in accounts:
        email = a["email"]
        code = state.get(email, "")
        if not code or code == PLACEHOLDER:
            print(f"[verify] ✗ {email}: 状态文件中无新授权码 → 先执行 inject")
            failed += 1
            continue
        if args.local_mock:
            ok, desc = verify_with_mock(email, code, fail_auth=args.mock_fail)
        else:
            ok, desc = smtp_verify_code(email, code, a.get("to") or email)
        print(f"[verify] {'✓' if ok else '✗'} {email}: {desc}")
        if ok:
            passed += 1
        else:
            failed += 1
    print(f"[verify] 汇总: PASS={passed}  FAIL={failed}")
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="139 邮箱 SMTP 授权码批量轮换工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出清单账号与授权码状态")
    p_list.add_argument("--manifest", default=str(MANIFEST_DEFAULT))
    p_list.add_argument("--state", default=str(STATE_DEFAULT))

    p_guide = sub.add_parser("guide", help="输出各账号网页作废/重生成步骤")
    p_guide.add_argument("--manifest", default=str(MANIFEST_DEFAULT))
    p_guide.add_argument("--email", default=None)

    p_inj = sub.add_parser("inject", help="逐账号注入新授权码")
    p_inj.add_argument("--manifest", default=str(MANIFEST_DEFAULT))
    p_inj.add_argument("--state", default=str(STATE_DEFAULT))
    p_inj.add_argument("--email", default=None)
    p_inj.add_argument("--auth-code", default=None, help="⚠️ 明文进 shell 历史，仅单账号时可用")
    p_inj.add_argument("--interactive", action="store_true", help="交互式输入（推荐）")

    p_ver = sub.add_parser("verify", help="批量验证新授权码生效")
    p_ver.add_argument("--manifest", default=str(MANIFEST_DEFAULT))
    p_ver.add_argument("--state", default=str(STATE_DEFAULT))
    p_ver.add_argument("--email", default=None)
    p_ver.add_argument("--local-mock", action="store_true", help="本地 mock 验证（无外网时）")
    p_ver.add_argument("--mock-fail", action="store_true", help="mock 模拟 AUTH 失败（测试 535）")

    args = parser.parse_args()
    handlers = {"list": cmd_list, "guide": cmd_guide, "inject": cmd_inject, "verify": cmd_verify}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
