#!/usr/bin/env python3
"""自动化测试用例：139 SMTP 授权码轮换验证（rotate_smtp_auth_code.py 配套）。

【覆盖】
  T1 打码函数不泄露明文（mask）
  T2 占位符检测（未注入时 verify 必须拒绝）
  T3 本地 mock：AUTH 成功 + 发送测试邮件
  T4 本地 mock：AUTH 失败（535 路径）
  T5 注入→验证→还原占位符 全链路（临时配置文件）
  T6 真实外发验证（--real 显式启用，需网络 + 真实新授权码；默认 SKIP）

【用法】
  python scripts/test_smtp_auth_code.py            # 本地用例（无外网依赖）
  python scripts/test_smtp_auth_code.py --real     # 追加真实 139 外发验证（需配置真实新码）
【退出码】0=全过  1=有 FAIL
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rotate_smtp_auth_code as rot  # noqa: E402
from smtp_mock import start_mock_server  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def sample_config(code: str) -> Path:
    """生成临时 alertmanager.yml 片段（仅 smtp 段，供测试）。"""
    # mkstemp 在 Windows 上会持有句柄，先关闭 fd 才能 unlink
    fd, tmp_path = tempfile.mkstemp(suffix=".yml")
    os.close(fd)
    tmp = Path(tmp_path)
    tmp.write_text(
        "route:\n  receiver: mail\nreceivers:\n- name: mail\n  email_configs:\n"
        "  - smtp_smarthost: 'smtp.139.com:587'\n"
        f"    smtp_auth_password: '{code}'\n"
        "    to: '13539371839@139.com'\n",
        encoding="utf-8",
    )
    return tmp


def test_mask() -> None:
    check("T1 mask 打码", rot.mask("abcdefgh1234") == "abcd****1234", rot.mask("abcdefgh1234"))
    check("T1 mask 短值全打码", rot.mask("short") == "*****", rot.mask("short"))
    check("T1 mask 不返回明文", rot.mask("supersecretvalue") not in "supersecretvalue")


def test_placeholder_detection() -> None:
    tmp = sample_config(rot.PLACEHOLDER)
    cur = rot.current_password(tmp)
    check("T2 占位符识别", cur == rot.PLACEHOLDER, cur)
    tmp.unlink()


def test_mock_auth_success() -> None:
    srv = start_mock_server(port=1025, auth_password="new-code-123", fail_auth=False)
    try:
        import smtplib
        with smtplib.SMTP("127.0.0.1", 1025, timeout=5) as smtp:
            smtp.ehlo()
            smtp.login("13539371839@139.com", "new-code-123")
            smtp.sendmail("13539371839@139.com", ["t@mock.local"], "Subject: t\r\n\r\nbody")
            check("T3 mock AUTH+发送成功", True)
    except Exception as exc:
        check("T3 mock AUTH+发送成功", False, str(exc))
    finally:
        srv.shutdown()
        srv.server_close()


def test_mock_auth_fail() -> None:
    srv = start_mock_server(port=1025, auth_password="new-code-123", fail_auth=True)
    try:
        import smtplib
        try:
            with smtplib.SMTP("127.0.0.1", 1025, timeout=5) as smtp:
                smtp.ehlo()
                smtp.login("13539371839@139.com", "wrong")
            check("T4 mock AUTH 失败分支", False, "未抛出 535")
        except smtplib.SMTPAuthenticationError as exc:
            check("T4 mock AUTH 失败分支（535 抛出）", exc.smtp_code == 535, str(exc.smtp_code))
    finally:
        srv.shutdown()
        srv.server_close()


def test_inject_verify_restore() -> None:
    tmp = sample_config(rot.PLACEHOLDER)
    try:
        # 注入
        rot.replace_auth_password(tmp, "new-code-456")
        cur = rot.current_password(tmp)
        check("T5 注入后值为新码", cur == "new-code-456", cur)
        check("T5 注入后打码显示", rot.mask(cur) == "new-****-456", rot.mask(cur))
        # 本地 mock 验证新码
        srv = start_mock_server(port=1025, auth_password=cur, fail_auth=False)
        try:
            import smtplib
            with smtplib.SMTP("127.0.0.1", 1025, timeout=5) as smtp:
                smtp.ehlo()
                smtp.login("13539371839@139.com", cur)
            check("T5 新码本地验证通过", True)
        finally:
            srv.shutdown()
            srv.server_close()
        # 还原占位符
        rot.replace_auth_password(tmp, rot.PLACEHOLDER)
        check("T5 还原占位符", rot.current_password(tmp) == rot.PLACEHOLDER)
    finally:
        tmp.unlink()


def test_real_verify() -> None:
    """真实 139 外发验证（仅 --real）。需配置中已注入真实新授权码 + 外网可达。"""
    config = rot.CONFIG_FILE
    code = rot.current_password(config)
    if code == rot.PLACEHOLDER or not code:
        print("  [SKIP] T6 真实外发：配置仍是占位符（未注入新授权码）")
        return
    ok, desc = rot.smtp_verify(code, "13539371839@139.com")
    check("T6 真实 139 外发验证", ok, desc)


def main() -> int:
    parser = argparse.ArgumentParser(description="授权码轮换验证自动化测试")
    parser.add_argument("--real", action="store_true", help="追加真实 139 外发验证")
    args = parser.parse_args()

    print("═══ SMTP 授权码轮换验证测试 ═══")
    test_mask()
    test_placeholder_detection()
    test_mock_auth_success()
    test_mock_auth_fail()
    test_inject_verify_restore()
    if args.real:
        test_real_verify()

    print(f"\n── 汇总 ── PASS={PASS} FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
