#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_bom_hook_stability.py — pre-commit hook BOM 拦截稳定性自动化测试。

目的: 循环模拟「每次提交前故意写入叠加 BOM 的临时 PS 文件」的真实 git commit,
断言 pre-commit hook 每次都稳定拦截(exit != 0 且 HEAD 不变), 验证拦截机制
(ENCODING_CHECK + BOMFIX 双段)在连续提交场景下的稳定性与冗余性。

背景: 2026-08-04 事故中 .ps1/.psm1 多次被批量写入叠加 BOM(EF BB BF xN),
check_ps1_encoding.py 与 fix_ps_bom.py 已集成进 hook 的 ENCODING_CHECK /
BOMFIX 段。本脚本把「手动演示一次拦截」升级为「自动化连续 N 次验证拦截」,
防止机制被后续改动无意破坏(守【不易】: 拦截机制 = 不变量)。

工作原理(每轮迭代 i):
  1. 写入 scripts/__bom_hook_stability_<i>__.ps1(前导 EF BB BF x2 的叠加 BOM)
  2. 直连两个检查脚本确认临时文件被检出(归因前置: 拦截来源可追溯)
  3. git add <临时文件>; 记录 HEAD
  4. git commit(真实 hook, TLM_HOOK_SOURCE_REPO=<repo>, 跳过无关段)
  5. 断言: commit 返回非零(被拦截) 且 HEAD 未变 且 输出含 BOM 检查标记
  6. 清理: git reset 取消暂存 + 删除临时文件(失败也走 finally 清理)

拦截层级(--mode, 默认 both = 双段拦截, 验证冗余):
  both     = ENCODING_CHECK + BOMFIX 同时生效(ENCODING 先触发)
  encoding = 仅 ENCODING_CHECK(SKIP_BOM_FIX_CHECK=1)
  bomfix   = 仅 BOMFIX(SKIP_ENCODING_CHECK=1, 验证独立冗余层)

默认跳过与 BOM 无关的 CI_GUARD / INVARIANT / WORKFLOW_SIM 段(工作区可能
存在历史遗留未跟踪文件, 会误触发 CI 守卫); 如需全段运行用 --no-skip-extra。

用法:
  python scripts/verify_bom_hook_stability.py                 # 5 轮迭代, 双段拦截
  python scripts/verify_bom_hook_stability.py --iterations 10
  python scripts/verify_bom_hook_stability.py --mode bomfix   # 仅 BOMFIX 冗余层
  python scripts/verify_bom_hook_stability.py --json          # stdout 仅单行 JSON
  python scripts/verify_bom_hook_stability.py --repo-root <repo>

退出码: 0 = 全部拦截成功(机制稳定) / 1 = 存在未拦截或断言失败 / 前置条件不满足
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BOM = b"\xef\xbb\xbf"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMP_PREFIX = "__bom_hook_stability_"
TEMP_SUFFIX = ".ps1"

# hook 输出中的 BOM 拦截归因标记(命中任一即视为「由 BOM 检查段拦截」)
BOM_BLOCK_MARKERS = (
    "编码检查未通过",          # ENCODING_CHECK 段 exit 分支
    "叠加 BOM",                # check_ps1_encoding.py BLOCK 文案
    "BOM 修复预检未通过",      # BOMFIX 段 exit 分支
    "待修复",                  # fix_ps_bom.py --check 问题行文案
    TEMP_PREFIX,               # 临时文件名(直连脚本输出会带路径)
)


def run(repo: Path, args: list, env=None, timeout: int = 600) -> subprocess.CompletedProcess:
    """在 repo 中执行命令, 捕获输出(UTF-8 容错解码)。"""
    return subprocess.run(
        ["git", "-C", str(repo)] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=timeout,
    )


def run_py(repo: Path, script: str, args: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PROJECT_ROOT / script)] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(repo),
    )


def build_env(repo: Path, mode: str, skip_extra: bool) -> dict:
    env = os.environ.copy()
    env["TLM_HOOK_SOURCE_REPO"] = str(repo)
    if skip_extra:
        env["SKIP_CI_GUARD"] = "1"
        env["SKIP_INVARIANT"] = "1"
        env["SKIP_WORKFLOW_SIM"] = "1"
    if mode == "encoding":
        env["SKIP_BOM_FIX_CHECK"] = "1"
    elif mode == "bomfix":
        env["SKIP_ENCODING_CHECK"] = "1"
    return env


def write_stacked_bom_file(path: Path) -> None:
    """写入叠加 BOM(EF BB BF x2)的临时 PS 文件, 模拟事故文件头。"""
    content = BOM + BOM + b"Write-Output 'intentional stacked BOM for hook stability test'\n"
    path.write_bytes(content)


def detect_direct(repo: Path, temp_name: str) -> tuple[bool, str]:
    """直连两个检查脚本, 确认临时文件确实被检出(拦截归因前置)。

    Windows 下 Path.relative_to 输出反斜杠, 检查脚本也可能输出正斜杠,
    因此同时匹配 posix/win 两种分隔符形式。
    """
    posix = f"scripts/{temp_name}"
    win = f"scripts\\{temp_name}"
    hits = []
    enc = run_py(repo, "scripts/check_ps1_encoding.py",
                 ["--repo-root", str(repo)])
    if posix in enc.stdout or win in enc.stdout:
        hits.append("check_ps1_encoding")
    fix = run_py(repo, "scripts/fix_ps_bom.py",
                 ["--check", "--repo-root", str(repo)])
    if posix in fix.stdout or win in fix.stdout:
        hits.append("fix_ps_bom")
    return bool(hits), "+".join(hits) if hits else "未被任一检查脚本检出"


def analyze_commit(proc: subprocess.CompletedProcess, mode: str) -> tuple[bool, str]:
    """归因提交被拦截的来源: 是否由 BOM 检查段拦截。"""
    out = (proc.stdout or "") + (proc.stderr or "")
    if any(m in out for m in BOM_BLOCK_MARKERS):
        marker = next(m for m in BOM_BLOCK_MARKERS if m in out)
        return True, f"归因标记: {marker}"
    return False, f"exit={proc.returncode} 但输出无 BOM 拦截标记(前段拦截?): {out[-300:]}"


def main() -> int:
    ap = argparse.ArgumentParser(description="pre-commit hook BOM 拦截稳定性自动化测试")
    ap.add_argument("--iterations", type=int, default=5,
                    help="连续提交模拟轮数(默认 5, 范围 1-50)")
    ap.add_argument("--mode", choices=["both", "encoding", "bomfix"], default="both",
                    help="拦截层级: both=双段 / encoding=仅编码检查 / bomfix=仅 BOMFIX 冗余层(默认 both)")
    ap.add_argument("--repo-root", default=str(PROJECT_ROOT),
                    help="仓库根目录(默认脚本所在仓库; hook 经 TLM_HOOK_SOURCE_REPO 寻址)")
    ap.add_argument("--no-skip-extra", action="store_true",
                    help="不跳过 CI_GUARD/INVARIANT/WORKFLOW_SIM 段(全段真实运行)")
    ap.add_argument("--json", action="store_true", help="stdout 仅输出单行 JSON(人类输出走 stderr)")
    args = ap.parse_args()

    # --json 强制安静: 人类可读进度走 stderr, stdout 仅 JSON
    def log(msg: str) -> None:
        if not args.json:
            print(msg)

    if not 1 <= args.iterations <= 50:
        print(f"[ERROR] --iterations 需在 1-50 范围, 收到 {args.iterations}", file=sys.stderr)
        return 1

    repo = Path(args.repo_root).resolve()
    hook = repo / ".git" / "hooks" / "pre-commit"
    if not hook.is_file():
        print(f"[ERROR] 未找到部署态 hook: {hook}", file=sys.stderr)
        print("  请先运行 scripts/dev/sync_precommit_hook.ps1 部署后再测试", file=sys.stderr)
        return 1
    if not (PROJECT_ROOT / "scripts" / "check_ps1_encoding.py").is_file() or \
       not (PROJECT_ROOT / "scripts" / "fix_ps_bom.py").is_file():
        print("[ERROR] 检查脚本缺失(check_ps1_encoding.py / fix_ps_bom.py)", file=sys.stderr)
        return 1

    env = build_env(repo, args.mode, skip_extra=not args.no_skip_extra)
    mode_desc = {"both": "双段拦截", "encoding": "仅 ENCODING_CHECK", "bomfix": "仅 BOMFIX"}[args.mode]

    log(f"[bom-stability] 仓库: {repo}")
    log(f"[bom-stability] hook: {hook.name} | 模式: {mode_desc} | 迭代: {args.iterations}")
    log(f"[bom-stability] 跳过无关段: {not args.no_skip_extra} | 环境变量: TLM_HOOK_SOURCE_REPO={env['TLM_HOOK_SOURCE_REPO']}")

    items = []
    passed = 0
    failed = 0

    for i in range(args.iterations):
        temp_name = f"{TEMP_PREFIX}{i}{TEMP_SUFFIX}"
        temp_path = repo / "scripts" / temp_name
        temp_rel = f"scripts/{temp_name}"
        item = {"id": f"iter-{i}", "path": temp_rel, "status": "PASS", "desc": "", "detail": ""}
        try:
            # 1. 写入叠加 BOM 临时文件
            write_stacked_bom_file(temp_path)
            item["desc"] = "叠加BOM提交"
            log(f"\n[iter-{i}] 写入 {temp_rel} (叠加 BOM x2)")

            # 2. 直连检查确认检出(归因前置)
            detected, det_detail = detect_direct(repo, temp_name)
            item["detail"] = f"检出: {det_detail}; "

            # 3. 暂存 + 记录 HEAD
            r = run(repo, ["add", "--", temp_rel], env=env)
            if r.returncode != 0:
                raise RuntimeError(f"git add 失败: {r.stderr[-300:]}")
            head_before = run(repo, ["rev-parse", "HEAD"], env=env).stdout.strip()

            # 4. 真实 git commit(触发部署态 hook)
            log(f"[iter-{i}] 尝试提交 (git commit, 期望被拦截)...")
            proc = run(repo, ["commit", "-m", f"test(bom-stability): iteration {i}"], env=env)

            # 5. 断言: 非零退出 + HEAD 不变 + 归因 BOM
            head_after = run(repo, ["rev-parse", "HEAD"], env=env).stdout.strip()
            blocked = proc.returncode != 0
            head_stable = head_before == head_after and bool(head_before)

            ok, marker_note = analyze_commit(proc, args.mode)
            ok = blocked and head_stable and ok
            item["detail"] += f"exit={proc.returncode}; HEAD稳定={head_stable}; {marker_note}"

            if ok:
                passed += 1
                log(f"[iter-{i}] PASS: 提交被稳定拦截")
            else:
                failed += 1
                item["status"] = "BLOCK"
                if not blocked:
                    item["detail"] += "; 异常: 提交未被拦截(机制失效!)"
                elif not head_stable:
                    item["detail"] += "; 异常: HEAD 发生变化(产生了新提交)"
                log(f"[iter-{i}] FAIL: {item['detail']}")
        except Exception as e:  # noqa: BLE001 - 测试脚本需兜底记录并继续
            failed += 1
            item["status"] = "BLOCK"
            item["detail"] += f"; 异常: {e}"
            log(f"[iter-{i}] FAIL: 异常 {e}")
        finally:
            # 6. 清理: 取消暂存 + 删除临时文件(绝不残留测试伪影)
            try:
                if run(repo, ["reset", "-q", "HEAD", "--", temp_rel], env=env).returncode != 0:
                    run(repo, ["reset", "-q", "--", temp_rel], env=env)
            except Exception:
                pass
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
        items.append(item)

    # 收尾: 清理可能残留的临时文件 + 确认无测试伪影
    leftover = list((repo / "scripts").glob(f"{TEMP_PREFIX}*.ps1"))
    for p in leftover:
        try:
            run(repo, ["reset", "-q", "HEAD", "--", str(p.relative_to(repo))], env=env)
            p.unlink(missing_ok=True)
        except Exception:
            pass
    leftover_after = list((repo / "scripts").glob(f"{TEMP_PREFIX}*.ps1"))
    if leftover_after:
        failed += 1
        log(f"[ERROR] 清理残留临时文件失败: {leftover_after}")

    # 汇总
    status = "PASS" if failed == 0 else "BLOCK"
    log("")
    log(f"=== BOM 拦截稳定性测试汇总 ===")
    log(f"  迭代 {args.iterations} | 模式 {mode_desc} | 拦截成功 {passed} | 失败 {failed}")
    log(f"  结论: {'机制稳定, 每次提交均被拦截' if status == 'PASS' else '存在拦截失效, 需排查'}")
    log(f"  清理: 临时文件已{'全部清除' if not leftover_after else '未完全清除!'}")

    report = {
        "tool": "verify_bom_hook_stability",
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "mode_desc": mode_desc,
        "iterations": args.iterations,
        "total": args.iterations,
        "passed": passed,
        "failed": failed,
        "leftover": [str(p) for p in leftover_after],
        "items": items,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
