#!/usr/bin/env python3
"""rotate_smtp_auth_codes_batch.py 自动化测试用例（本地 mock，无外网依赖）。

覆盖：
  B1  清单解析 + 打码
  B2  mock 批量 verify 全部成功（多账号）
  B3  mock 批量 verify 含失败（535）
  B4  inject→verify 全链路（临时清单/状态文件，CLI 级）
  B5  清单含未填写占位符 → 拒绝执行（SystemExit=2）

退出码：0=全部通过  1=存在失败项
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import rotate_smtp_auth_codes_batch as batch

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name} {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def make_tmp_files(accounts: list[dict], state: dict | None = None):
    """生成临时清单与状态文件，返回 (manifest_path, state_path)。"""
    fd1, m_path = tempfile.mkstemp(suffix=".json")
    fd2, s_path = tempfile.mkstemp(suffix=".json")
    os.close(fd1); os.close(fd2)
    Path(m_path).write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
    if state is not None:
        Path(s_path).write_text(json.dumps(state), encoding="utf-8")
    return Path(m_path), Path(s_path)


def t1() -> None:
    print("── B1 清单解析 + 打码 ──")
    acc = [
        {"email": "a@139.com", "to": "a@139.com", "note": ""},
        {"email": "b@139.com", "to": "b@139.com", "note": ""},
    ]
    m, _ = make_tmp_files(acc)
    parsed = batch.load_manifest(m)
    check("B1 解析 2 账号", len(parsed) == 2, f"got {len(parsed)}")
    check("B1 mask 前4后4", batch.mask("new-code-12345678") == "new-****5678",
          batch.mask("new-code-12345678"))
    check("B1 mask 短值全星", batch.mask("short") == "*****")
    m.unlink(missing_ok=True)


def t2() -> None:
    print("── B2 mock 批量 verify 全部成功 ──")
    ok1, d1 = batch.verify_with_mock("a@139.com", "code-aaa-111")
    ok2, d2 = batch.verify_with_mock("b@139.com", "code-bbb-222")
    check("B2 账号 a AUTH 成功", ok1, d1)
    check("B2 账号 b AUTH 成功", ok2, d2)


def t3() -> None:
    print("── B3 mock 批量 verify 含失败（535）──")
    ok1, d1 = batch.verify_with_mock("a@139.com", "code-ccc-333")
    ok2, d2 = batch.verify_with_mock("b@139.com", "code-ddd-444", fail_auth=True)
    check("B3 正常账号成功", ok1, d1)
    check("B3 fail_auth 账号失败(535)", (not ok2) and ("535" in d2), d2)


def t4() -> None:
    print("── B4 inject→verify 全链路（CLI 级，mock）──")
    acc = [{"email": "cli-test@139.com", "to": "cli-test@139.com", "note": ""}]
    m, s = make_tmp_files(acc)
    # inject 非交互：通过环境变量 SMTP_AUTH_CODE_cli-test 提供新码
    env = dict(os.environ)
    env["SMTP_AUTH_CODE_cli-test"] = "batch-new-code-9999"
    r = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("rotate_smtp_auth_codes_batch.py")),
         "inject", "--manifest", str(m), "--state", str(s)],
        capture_output=True, text=True, env=env,
    )
    check("B4 inject 退出码 0", r.returncode == 0, r.stderr.strip()[:80])
    state = batch.load_state(s)
    check("B4 状态已写入新码", state.get("cli-test@139.com") == "batch-new-code-9999")
    r = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("rotate_smtp_auth_codes_batch.py")),
         "verify", "--manifest", str(m), "--state", str(s), "--local-mock"],
        capture_output=True, text=True, env=env,
    )
    check("B4 verify 退出码 0（mock 全通过）", r.returncode == 0, (r.stdout + r.stderr).strip()[-120:])
    check("B4 verify 输出 PASS=1", "PASS=1" in (r.stdout + r.stderr))
    m.unlink(missing_ok=True); s.unlink(missing_ok=True)


def t5() -> None:
    print("── B5 清单占位符拒绝 ──")
    acc = [{"email": "REPLACE_WITH_ACCOUNT2@139.com", "to": "", "note": ""}]
    m, _ = make_tmp_files(acc)
    try:
        batch.load_manifest(m)
        check("B5 占位符应拒绝", False, "未抛 SystemExit")
    except SystemExit as e:
        check("B5 占位符拒绝(SystemExit=2)", e.code == 2, f"code={e.code}")
    m.unlink(missing_ok=True)


if __name__ == "__main__":
    print("═══ rotate_smtp_auth_codes_batch.py 测试 ═══")
    t1(); t2(); t3(); t4(); t5()
    print(f"═══ 汇总 PASS={PASS} FAIL={FAIL} ═══")
    sys.exit(0 if FAIL == 0 else 1)
