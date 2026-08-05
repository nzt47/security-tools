#!/usr/bin/env python3
"""环境体检脚本: 将 docs/GIT_OPERATION_SAFETY_GUIDE.md §9 排查清单自动化

覆盖 §9 检查项(1-7) + 清理提示(8/9, 仅报告不自动执行, 守【不易】):
  C1  无活跃干扰进程(匹配 stop_agitator_processes.ps1 默认模式)
  C2  无自定义计划任务(schtasks, 仅 Windows)
  C3  提交时间线(区分 +0000 workflow 自动提交 / +0800 本地会话提交)
  C4  工作区污染(未跟踪/暂存/已修改文件清单)
  C5  BOM 污染(调 check_ps1_encoding.py)
  C6  hook 拦截能力(可选, --with-hook-test, 会真实触发 git commit)
  C7  关键文件不变量(调 verify_core_invariants.py)

§9 步 8/9(清理) 属人工确认操作: 本脚本仅列出清单与提示命令, 不删除任何文件。

状态分级: pass = 健康; WARN = 需人工关注但不阻止(如并发会话提交、未跟踪产物);
         BLOCK = 环境异常(如 BOM 污染、不变量破坏)。退出码仅按 BLOCK 判定。

用法:
    python scripts/env_health_check.py                    # 全量体检
    python scripts/env_health_check.py --with-hook-test   # 含 hook 拦截稳定性实测(真实提交)
    python scripts/env_health_check.py --json             # stdout 仅 JSON(机器可读)
    python scripts/env_health_check.py --quiet            # 仅输出汇总

退出码: 0 = 无 BLOCK; 1 = 存在 BLOCK(环境异常)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# stop_agitator_processes.ps1 默认匹配模式(§9-1 / §8 真相澄清)
AGITATOR_PATTERNS = ("verify_bom_hook_stability", "simulate_workflow_closed_loop")
# §9-3 时间线区分: +0800 = 本地会话提交; +0000 + [skip ci] = workflow 自动提交(正常行为)
LOG_FORMAT = "--format=%h|%ci|%s"


def _run(cmd: list[str], cwd: Path = PROJECT_ROOT, timeout: int = 120) -> subprocess.CompletedProcess:
    """执行命令, utf-8 容错解码(仓库工具统一约定)"""
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _pwsh(script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=timeout)


def _item(iid: str, path: str, desc: str, status: str, detail: str) -> dict:
    return {"id": iid, "path": path, "desc": desc, "status": status, "detail": detail}


def check_c1() -> dict:
    """§9-1 无活跃干扰进程(仅报告, 绝不自动终止——§8 澄清 verify_bom_hook_stability 可能是人工验证)"""
    desc = "无活跃干扰进程(verify_bom_hook_stability/simulate_workflow_closed_loop)"
    pat = "|".join(AGITATOR_PATTERNS)
    if os.name != "nt":
        r = _run(["sh", "-c", f"ps -eo pid=,args= | grep -E '{pat}' | grep -v grep"])
        hits = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    else:
        ps = (
            "Get-CimInstance Win32_Process -Filter 'Name = ''python.exe'' OR Name = ''python3.exe'' OR Name = ''py.exe''' | "
            f"ForEach-Object {{ if ($_.CommandLine -match '{pat}') {{ $_.ProcessId.ToString() + ': ' + $_.CommandLine }} }}"
        )
        r = _pwsh(ps)
        hits = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    if hits:
        return _item("C1", "", desc, "WARN",
                     "存在匹配进程(可能是人工验证, 用 stop_agitator_processes.ps1 核对, 勿盲杀): "
                     + "; ".join(hits[:3]))
    return _item("C1", "", desc, "pass", "未发现匹配进程")


def check_c2() -> dict:
    """§9-2 无自定义计划任务(schtasks, 仅 Windows)"""
    desc = "无自定义计划任务(匹配 agent|python|git)"
    if os.name != "nt":
        return _item("C2", "", desc, "pass", "平台跳过(仅 Windows 支持 schtasks)")
    r = _pwsh("schtasks /query /fo CSV /nh")
    hits = [ln for ln in r.stdout.splitlines()
            if ln and any(k in ln for k in ("agent", "python", "git"))]
    if hits:
        return _item("C2", "", desc, "WARN",
                     "发现疑似自定义计划任务(请人工核对): " + "; ".join(hits[:3]))
    return _item("C2", "", desc, "pass", "无匹配计划任务")


def check_c3() -> dict:
    """§9-3 核对今日提交时间线(区分 workflow 自动提交 / 本地会话提交)"""
    desc = "提交时间线核对(区分 workflow 自动提交 / 本地会话提交)"
    r = _run(["git", "log", "--since=midnight", LOG_FORMAT])
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    workflow = [ln for ln in lines if "+0000" in ln and "[skip ci]" in ln]
    local = [ln for ln in lines if "+0800" in ln]
    if local:
        return _item("C3", "", desc, "WARN",
                     f"今日存在 {len(local)} 条 +0800 本地/会话提交, 请按 §9-3 人工核对来源; "
                     f"workflow 自动提交 {len(workflow)} 条(+0000/[skip ci])属正常行为")
    return _item("C3", "", desc, "pass",
                 f"今日提交 {len(lines)} 条(workflow 自动 {len(workflow)} 条, 无 +0800 本地提交)")


def check_c4() -> dict:
    """§9-4 检查工作区污染(未跟踪/暂存/已修改)"""
    desc = "工作区污染检查(未跟踪/暂存/已修改文件)"
    r = _run(["git", "status", "--porcelain=v1", "--untracked-files=all"])
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if not lines:
        return _item("C4", "", desc, "pass", "工作区干净")
    untracked = [ln[3:] for ln in lines if ln.startswith("??")]
    staged = [ln[3:] for ln in lines if ln.startswith(("A ", "M ", "D ", "R ", "C "))]
    modified = [ln[3:] for ln in lines if ln.startswith(" M")]
    detail = f"未跟踪 {len(untracked)} / 暂存 {len(staged)} / 已修改 {len(modified)}"
    if untracked:
        detail += " | 未跟踪文件(按 §9-9 逐项核对来源): " + ", ".join(untracked[:5])
    if staged:
        detail += " | 暂存文件(提交前核对): " + ", ".join(staged[:5])
    if modified:
        detail += " | 已修改(未暂存): " + ", ".join(modified[:5])
    return _item("C4", "", desc, "WARN", detail)


def check_c5(root: Path) -> dict:
    """§9-5 BOM 污染(调 check_ps1_encoding.py, 退出码语义即 BLOCK 判定)"""
    desc = "BOM 污染检查(check_ps1_encoding.py)"
    r = _run([sys.executable, "scripts/check_ps1_encoding.py", "--quiet", "--repo-root", str(root)])
    if r.returncode == 0:
        return _item("C5", "scripts/check_ps1_encoding.py", desc, "pass", "BLOCK 0, 编码契约通过")
    return _item("C5", "scripts/check_ps1_encoding.py", desc, "BLOCK",
                 "存在 BLOCK 级编码问题, 查看明细: python scripts/check_ps1_encoding.py --repo-root .")


def check_c6(root: Path, with_hook_test: bool) -> dict:
    """§9-6 验证 hook 拦截能力(可选, --with-hook-test 会真实触发 git commit)"""
    desc = "hook 拦截能力(叠加 BOM 提交应被拦截)"
    if not with_hook_test:
        return _item("C6", "scripts/verify_bom_hook_stability.py", desc, "pass",
                     "未启用(用 --with-hook-test 开启真实提交实测)")
    r = _run([sys.executable, "scripts/verify_bom_hook_stability.py",
              "--iterations", "2", "--mode", "both"],
             timeout=600)
    if r.returncode == 0:
        return _item("C6", "scripts/verify_bom_hook_stability.py", desc, "pass",
                     "hook 拦截实测通过(叠加 BOM 提交均被拦截)")
    tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
    return _item("C6", "scripts/verify_bom_hook_stability.py", desc, "BLOCK",
                 "hook 拦截异常, 输出尾部: " + " | ".join(tail))


def check_c7(root: Path) -> dict:
    """§9-7 关键文件不变量(调 verify_core_invariants.py)"""
    desc = "关键文件不变量(verify_core_invariants.py)"
    r = _run([sys.executable, "scripts/verify_core_invariants.py", "--quiet", "--repo-root", str(root)])
    if r.returncode == 0:
        return _item("C7", "scripts/verify_core_invariants.py", desc, "pass",
                     "核心不变量全部通过")
    return _item("C7", "scripts/verify_core_invariants.py", desc, "BLOCK",
                 "不变量被破坏, 查看明细: python scripts/verify_core_invariants.py --repo-root .")


def _hints(items: list[dict]) -> list[str]:
    """§9-8/9 清理提示(仅建议命令, 不自动执行)"""
    hints = []
    c4 = next((i for i in items if i["id"] == "C4"), None)
    if c4:
        if "未跟踪" in c4["detail"]:
            hints.append("git status --porcelain 逐个核对未跟踪文件来源后删除或 git add(§9-9)")
        if "暂存" in c4["detail"]:
            hints.append("git diff --cached --name-only 核对暂存区, 误跟踪文件用 git restore --staged <file> 移出(§9-8)")
    return hints


def main() -> int:
    ap = argparse.ArgumentParser(description="环境体检(§9 排查清单自动化)")
    ap.add_argument("--repo-root", default=".", help="仓库根目录(默认当前目录)")
    ap.add_argument("--with-hook-test", action="store_true",
                    help="含 hook 拦截稳定性实测(会真实触发 git commit, 需 TLM_HOOK_SOURCE_REPO)")
    ap.add_argument("--json", action="store_true", help="stdout 仅输出 JSON(机器可读)")
    ap.add_argument("--quiet", action="store_true", help="仅输出结果汇总")
    args = ap.parse_args()

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"[ERROR] 仓库根目录不存在: {root}", file=sys.stderr)
        return 1

    items = [
        check_c1(),
        check_c2(),
        check_c3(),
        check_c4(),
        check_c5(root),
        check_c6(root, args.with_hook_test),
        check_c7(root),
    ]

    blocked = [i for i in items if i["status"] == "BLOCK"]
    warned = [i for i in items if i["status"] == "WARN"]
    report = {
        "tool": "env_health_check",
        "status": "fail" if blocked else "pass",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "meta": {
            "repo_root": str(root),
            "guide": "docs/GIT_OPERATION_SAFETY_GUIDE.md §9",
            "total": len(items),
            "blocked": len(blocked),
            "warned": len(warned),
            "hints": _hints(items),
        },
        "items": items,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if blocked else 0

    if not args.quiet:
        mark = {"pass": "[PASS]", "WARN": "[WARN]", "BLOCK": "[BLOCK]"}
        for i in items:
            print(f"{mark[i['status']]} {i['id']} {i['desc']}")
            if i["detail"]:
                print(f"        {i['detail']}")
        for h in _hints(items):
            print(f"[HINT] {h}")

    status = "PASS" if not blocked else "FAIL"
    print(f"=== env_health_check: {status} 通过 {len(items) - len(blocked)}/{len(items)}"
          f" (BLOCK {len(blocked)} / WARN {len(warned)}) ===")
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
