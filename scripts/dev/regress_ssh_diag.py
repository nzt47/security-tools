#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diagnose_ssh 自动化回归测试 — 覆盖 Mock 环境的三种模式

场景：
  1) 默认   启动 mock_ssh_server.py（banner 正常）→ 期望层3 TCP PASS、层4 Banner PASS、层5 走到认证
  2) 沙盒   启动 mock_ssh_server.py --no-banner（模拟无 sshd 的非 SSH 服务）
            → 期望层4 Banner FAIL、层5 仍走到（受限/无 sshd 环境的典型失败）
  3) 真实   不启动 mock，直连用户提供的真实服务器（--real-*）→ 校验 5 层结构与最终汇总；
            未提供参数时该场景 SKIP（真实服务器无法自动构造）

用法：
  python scripts/dev/regress_ssh_diag.py                          # 三场景（真实 SKIP）
  python scripts/dev/regress_ssh_diag.py --script ps1             # 被测脚本：ps1（默认）| sh
  python scripts/dev/regress_ssh_diag.py --port 2223              # 自定义 mock 端口
  python scripts/dev/regress_ssh_diag.py --real-host <ip> --real-port 22 \\
        --real-user <user> --real-key <key>                       # 附带真实场景

退出码：0 = 全部通过/SKIP；1 = 任一场景断言失败。
"""

import argparse
import os
import re
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEV_DIR = os.path.join(ROOT, "scripts", "dev")
MOCK_PATH = os.path.join(DEV_DIR, "mock_ssh_server.py")
DIAG_PS1 = os.path.join(DEV_DIR, "diagnose_ssh.ps1")
DIAG_SH = os.path.join(DEV_DIR, "diagnose_ssh.sh")

# 场景断言表：(场景名, mock 参数列表, 断言列表)
# 断言：("PASS"|"FAIL"|"SKIP", 层关键字, 期望文本子串)
SCENARIOS = {
    "默认": {
        "mock_args": [],
        "expect": [
            ("PASS", "[3/5]", "TCP"),
            ("PASS", "[4/5]", "sshd responds"),
            ("ANY",  "[5/5]", "SSH authentication"),
        ],
    },
    "沙盒": {
        "mock_args": ["--no-banner"],
        "expect": [
            ("PASS", "[3/5]", "TCP"),
            ("FAIL", "[4/5]", "no SSH banner"),
            ("ANY",  "[5/5]", "SSH authentication"),
        ],
    },
}


def wait_port(port, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def run_diag(script, args):
    """运行诊断脚本，返回 (exit_code, 输出文本)。参数为统一关键字 dict。"""
    if script == "ps1":
        mapped = ["-Target", args["host"], "-Port", str(args["port"])]
        if args.get("user") and args.get("key"):
            mapped += ["-User", args["user"], "-Key", args["key"]]
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", DIAG_PS1] + mapped
    else:
        mapped = ["--host", args["host"], "--port", str(args["port"])]
        if args.get("user") and args.get("key"):
            mapped += ["--user", args["user"], "--key", args["key"]]
        cmd = ["bash", DIAG_SH] + mapped
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def check_expect(text, expect):
    """逐条断言；返回 (通过数, 总条数, 失败详情列表)。"""
    passed, fails = 0, []
    for mode, layer, keyword in expect:
        # 截取当前层段落：从该层标题到下一层标题
        layer_idx = text.find(layer)
        if layer_idx < 0:
            fails.append("缺少层 %s" % layer)
            continue
        end_idx = len(text)
        nxt = re.search(r"\[\d/5\]", text[layer_idx + len(layer):])
        if nxt:
            end_idx = layer_idx + len(layer) + nxt.start()
        seg = text[layer_idx:end_idx]
        if mode == "PASS" and "PASS" in seg and keyword in seg:
            passed += 1
        elif mode == "FAIL" and "FAIL" in seg and keyword in seg:
            passed += 1
        elif mode == "ANY" and ("PASS" in seg or "FAIL" in seg or "SKIP" in seg):
            passed += 1
        else:
            fails.append("层 %s 期望 %s 命中 %r 失败" % (layer, mode, keyword))
    return passed, len(expect), fails


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--script", choices=["ps1", "sh"], default="ps1",
                    help="被测诊断脚本（默认 ps1；sh 在沙盒中受 bash /dev/tcp 隔离影响）")
    ap.add_argument("--port", type=int, default=2223)
    ap.add_argument("--real-host", default="")
    ap.add_argument("--real-port", type=int, default=22)
    ap.add_argument("--real-user", default="")
    ap.add_argument("--real-key", default="")
    args = ap.parse_args()

    if args.script == "sh" and not os.path.exists("/usr/bin/ssh") \
            and os.name == "nt" and "sandbox" in os.environ.get("TRAE_SANDBOX", ""):
        print("[WARN] 沙盒中 bash /dev/tcp 被隔离，sh 版层 3/4 预期 FAIL，可换 --script ps1 复测")

    results = []
    fail = 0

    for name, spec in SCENARIOS.items():
        print("\n== 场景[%s] ==" % name)
        # 启动 mock
        mock = subprocess.Popen([sys.executable, "-u", MOCK_PATH,
                                 "--port", str(args.port)] + spec["mock_args"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            if not wait_port(args.port):
                print("  [FAIL] mock 未就绪（端口 %d）" % args.port)
                fail += 1
                continue
            code, text = run_diag(args.script, {"host": "127.0.0.1", "port": args.port})
            passed, total, fl = check_expect(text, spec["expect"])
            ok = (passed == total and not fl)
            print("  [%s] 断言 %d/%d  诊断 exit=%d" % ("PASS" if ok else "FAIL",
                                                   passed, total, code))
            for f in fl:
                print("    - " + f)
            if not ok:
                fail += 1
        finally:
            mock.terminate()
            try:
                mock.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mock.kill()

    # 真实场景
    print("\n== 场景[真实] ==")
    if args.real_host:
        real_args = {"host": args.real_host, "port": args.real_port}
        if args.real_user and args.real_key:
            real_args["user"] = args.real_user
            real_args["key"] = args.real_key
        code, text = run_diag(args.script, real_args)
        has_summary = "[OK]" in text or "failures found" in text
        has_5 = "[5/5]" in text
        ok = has_5 and has_summary
        print("  [%s] 诊断 exit=%d（真实服务器，校验 5 层结构与汇总）"
              % ("PASS" if ok else "FAIL", code))
        if not ok:
            fail += 1
    else:
        print("  [SKIP] 未提供 --real-host/--real-user/--real-key，跳过真实场景")

    print("\n== 汇总 ==")
    if fail == 0:
        print("[PASS] 全部场景通过")
        return 0
    print("[FAIL] %d 个场景断言失败" % fail)
    return 1


if __name__ == "__main__":
    sys.exit(main())
