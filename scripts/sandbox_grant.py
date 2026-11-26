#!/usr/bin/env python
"""E0-1 沙箱写身份初始化 / 撤销（一次性、可重跑）。

权限预设切到 `workspace-write` 之前必须先跑本脚本：受限令牌只对 ACL 里带写身份组的对象
放行，而 `icacls` 的递归授权是**一次性**成本（仓库树越大越慢，20k 文件实测 ~10s）。
marker 命中后本脚本是 O(1)（直接输出 already-granted）。

用法：
  python scripts/sandbox_grant.py --status
  python scripts/sandbox_grant.py                 # 给默认工作区根（仓库根）授权
  python scripts/sandbox_grant.py --root D:/work  # 指定根
  python scripts/sandbox_grant.py --revoke        # 撤 ACE + 删写身份组 + 清 marker
  python scripts/sandbox_grant.py --revoke --keep-group   # 只撤 ACE
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.sandbox import win_acl  # noqa: E402
from agent.sandbox import preset as preset_mod  # noqa: E402


#: --probe-shell 探测的三个 shell（命令都是"只打印 ok"的最小形态，
#: 不读工作区、不写任何东西；目的是**能力**而不是功能）。
_PROBE_SHELLS = {
    "cmd": ["cmd", "/c", "echo ok"],
    "powershell": ["powershell", "-NoProfile", "-Command", "Write-Output ok"],
    "bash": ["bash", "-c", "echo ok"],
}


def probe_shell(timeout: float = 15.0) -> dict:
    """在**受限档**（workspace-write）下逐个试 cmd / powershell / bash 能不能跑。

    【为什么需要这个探针】E0-1 后续项 ① 的事实是：Windows 受限令牌下 WSL 的 bash.exe
    走 LxssManager 服务、Git Bash 的 MSYS2 运行时建共享内存段，两者都被 pass-2 拒绝。
    切档（CP_PERMISSION_PRESET=workspace-write）之前必须先知道**哪几个 shell 还能用**，
    否则切完才发现「带 unix 特征的命令全线失败」——那是能力回退，不是安全收益。

    【边界】探测本身就是一次受限执行：每个 shell 起一个进程，命令是 echo ok；
    失败只记异常**类名**与领域异常的 message（不打印路径原文/异常堆栈正文）。
    """
    from agent.sandbox import shell_run

    results = {}
    for name, argv in _PROBE_SHELLS.items():
        try:
            outcome = shell_run.run_captured(
                argv, str(preset_mod.sandbox_root()), float(timeout),
                preset=preset_mod.WORKSPACE_WRITE)
            results[name] = {
                "ok": bool(outcome.get("ok")),
                "exit_code": outcome.get("exit_code"),
                "timed_out": bool(outcome.get("timed_out")),
                # 失败时只留前 200 字符的 stderr，够判断「E_ACCESSDENIED / shared memory」
                "stderr_head": str(outcome.get("stderr") or "")[:200],
            }
        except Exception as exc:  # noqa: BLE001 探针失败即"该 shell 不可用"，不掩盖其它 shell
            results[name] = {
                "ok": False,
                "error_class": type(exc).__name__,
                "message": str(getattr(exc, "message", "") or "")[:200],
            }
    available = sorted(k for k, v in results.items() if v.get("ok"))
    return {
        "preset_probed": preset_mod.WORKSPACE_WRITE,
        "preset_current": preset_mod.permission_preset(),
        "available": available,
        "unavailable": sorted(k for k in results if k not in available),
        "shells": results,
        "note": ("切档前跑本探针：available 里没有 bash ⇒ 切到 workspace-write 后"
                 "带 unix 特征的命令会失败（工具会回可执行提示，不会静默降级）。"),
    }


def _print(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="云枢 E0-1 沙箱写身份初始化/撤销")
    parser.add_argument("--root", default="", help="授权根（默认：权限预设的工作区根）")
    parser.add_argument("--revoke", action="store_true", help="撤销授权")
    parser.add_argument("--keep-group", action="store_true", help="撤销时保留写身份组")
    parser.add_argument("--status", action="store_true", help="只打印当前状态（不写任何东西）")
    parser.add_argument("--probe-shell", action="store_true",
                        help="在受限档下探 cmd/powershell/bash 是否可用（切档前必跑）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--timeout", type=float, default=7200.0,
                        help="递归授权/撤销的超时秒数（默认 7200；本机 80 万文件实测 >30 分钟）")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve() if args.root else preset_mod.sandbox_root()

    if args.probe_shell:
        _print(probe_shell(), args.json)
        return 0

    if args.status:
        payload = win_acl.status()
        payload["root"] = str(root)
        payload["granted"] = bool(win_acl.group_sid()) and win_acl.is_granted(root)
        payload["preset"] = preset_mod.permission_preset()
        _print(payload, args.json)
        return 0

    if args.revoke:
        started = time.time()
        ok = win_acl.revoke(root, timeout=args.timeout)
        group_removed = True
        if not args.keep_group:
            group_removed = win_acl.delete_group()
        _print({
            "revoked": ok,
            "group_removed": group_removed,
            "root": str(root),
            "seconds": round(time.time() - started, 1),
        }, args.json)
        return 0 if ok else 1

    if not root.exists():
        _print({"error": f"根不存在: {root}"}, args.json)
        return 1
    started = time.time()
    sid = win_acl.ensure_group()
    win_acl.grant(root, sid=sid, timeout=args.timeout)
    _print({
        "granted": True,
        "root": str(root),
        "group": win_acl.GROUP_NAME,
        "group_sid": sid,
        "seconds": round(time.time() - started, 1),
        "state": str(win_acl.state_path()),
        "note": "此后 CP_PERMISSION_PRESET=workspace-write 才会放行该根内的写",
    }, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())


