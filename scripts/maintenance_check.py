#!/usr/bin/env python3
"""维护巡检脚本: 将 BOM 修复与环境加固总结报告 §6 的 7 条维护建议自动化

覆盖(映射总结报告 7 条建议):
  M1 定期体检     调 env_health_check.py --json 汇总(可透传 --with-hook-test)   [建议1]
  M2 提交前核对   git status 未跟踪/暂存/已修改 + 最新提交                        [建议2]
  M3 BOM 回归防护 调 check_ps1_encoding.py, 全仓 BLOCK 计数                       [建议3]
  M4 后台干扰治理 .gitignore "后台干扰进程产物" 防线段存在性                      [建议4]
  M5 CI 守卫演进  guard-master-commit-origin.yml 存在 + 默认 dry-run              [建议5]
  M6 Slack 待办   workflow 中 SLACK_WEBHOOK_URL 引用状态(配置需人工确认)          [建议6]
  M7 已知残留     CI_FIX_INDEX.md 未提交修改状态                                  [建议7]

状态分级: pass/WARN(需人工关注不阻止)/BLOCK(环境异常); 退出码仅按 BLOCK 判定。
清理类动作(§9 步骤 8/9)仅提示, 不自动执行(守【不易】)。

用法:
    python scripts/maintenance_check.py                    # 常规巡检
    python scripts/maintenance_check.py --with-hook-test   # M1 含 hook 拦截实测(真实提交)
    python scripts/maintenance_check.py --json             # stdout 仅 JSON
    python scripts/maintenance_check.py --quiet            # 仅输出汇总
退出码: 0 = 无 BLOCK; 1 = 存在 BLOCK
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KNOWN_REMAINING = "docs/observability/CI_FIX_INDEX.md"  # 建议7: 已知残留文件(并行会话进行中)
GITIGNORE_SEGMENT = "后台干扰进程产物"                   # 建议4: .gitignore 防线段注释
GUARD_WORKFLOW = ".github/workflows/guard-master-commit-origin.yml"


def _run(cmd: list[str], cwd: Path = PROJECT_ROOT, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _item(iid: str, path: str, desc: str, status: str, detail: str) -> dict:
    return {"id": iid, "path": path, "desc": desc, "status": status, "detail": detail}


def check_m1(with_hook_test: bool) -> dict:
    """建议1 定期体检: 调 env_health_check.py 汇总环境状态。"""
    desc = "环境体检(env_health_check.py 汇总)"
    cmd = [sys.executable, "scripts/env_health_check.py", "--quiet", "--json"]
    if with_hook_test:
        cmd.append("--with-hook-test")
    r = _run(cmd, timeout=900 if with_hook_test else 180)
    try:
        rep = json.loads(r.stdout)
    except (ValueError, json.JSONDecodeError):
        return _item("M1", "scripts/env_health_check.py", desc, "BLOCK",
                     f"env_health_check 输出解析失败, 尾部: {(r.stdout + r.stderr)[-200:]}")
    detail = (f"env_health_check: {rep['status'].upper()} "
              f"({rep['meta']['total']} 项, BLOCK {rep['meta']['blocked']} / "
              f"WARN {rep['meta']['warned']})")
    return _item("M1", "scripts/env_health_check.py", desc,
                 "BLOCK" if rep["status"] == "fail" else "pass", detail)


def check_m2() -> dict:
    """建议2 提交前固定动作: 核对工作区未跟踪/暂存/已修改 + 最新提交。"""
    desc = "提交前工作区核对(git status 未跟踪/暂存/已修改)"
    r = _run(["git", "status", "--porcelain=v1", "--untracked-files=all"])
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    log = _run(["git", "log", "-1", "--oneline"])
    head = log.stdout.strip()
    if not lines:
        return _item("M2", "", desc, "pass", f"工作区干净 | 最新提交: {head}")
    untracked = [ln[3:] for ln in lines if ln.startswith("??")]
    staged = [ln[3:] for ln in lines if ln.startswith(("A ", "M ", "D ", "R ", "C "))]
    modified = [ln[3:] for ln in lines if ln.startswith(" M")]
    detail = f"未跟踪 {len(untracked)} / 暂存 {len(staged)} / 已修改 {len(modified)}"
    if untracked:
        detail += " | 未跟踪: " + ", ".join(untracked[:5])
    if staged:
        detail += " | 暂存: " + ", ".join(staged[:5])
    if modified:
        detail += " | 已修改: " + ", ".join(modified[:5])
    detail += f" | 最新提交: {head}"
    return _item("M2", "", desc, "WARN", detail)


def check_m3(root: Path) -> dict:
    """建议3 BOM 回归防护: 全仓编码契约检查。"""
    desc = "BOM 回归防护(check_ps1_encoding.py 全仓)"
    r = _run([sys.executable, "scripts/check_ps1_encoding.py",
              "--quiet", "--repo-root", str(root)])
    if r.returncode == 0:
        return _item("M3", "scripts/check_ps1_encoding.py", desc, "pass",
                     "BLOCK 0, 编码契约通过")
    return _item("M3", "scripts/check_ps1_encoding.py", desc, "BLOCK",
                 "存在 BLOCK 级编码问题: python scripts/check_ps1_encoding.py --repo-root .")


def check_m4(root: Path) -> dict:
    """建议4 后台干扰治理: .gitignore 防线段存在性。"""
    desc = ".gitignore 后台干扰产物防线段"
    gi = root / ".gitignore"
    if not gi.exists():
        return _item("M4", ".gitignore", desc, "WARN", ".gitignore 缺失")
    text = gi.read_text(encoding="utf-8", errors="replace")
    if GITIGNORE_SEGMENT in text:
        return _item("M4", ".gitignore", desc, "pass", f"防线段 '{GITIGNORE_SEGMENT}' 存在")
    return _item("M4", ".gitignore", desc, "WARN",
                 f"缺 '{GITIGNORE_SEGMENT}' 段(后台产物可能再次被 git add . 混入)")


def check_m5(root: Path) -> dict:
    """建议5 CI 守卫演进: guard workflow 存在 + 灰度模式。"""
    desc = "master 提交来源守卫(guard-master-commit-origin.yml)"
    wf = root / GUARD_WORKFLOW
    if not wf.exists():
        return _item("M5", GUARD_WORKFLOW, desc, "WARN", "guard workflow 不存在(未启用守卫)")
    text = wf.read_text(encoding="utf-8", errors="replace")
    if "GUARD_MODE" in text and "dry-run" in text:
        return _item("M5", GUARD_WORKFLOW, desc, "pass",
                     "守卫存在且默认 dry-run(灰度期); 切换 enforce 后重跑本项会变 WARN")
    if "GUARD_MODE" in text and "enforce" in text:
        return _item("M5", GUARD_WORKFLOW, desc, "WARN",
                     "守卫已切 enforce(阻断模式), 确认是否符合预期灰度进度")
    return _item("M5", GUARD_WORKFLOW, desc, "WARN", "guard workflow 结构异常, 请人工核对")


def check_m6(root: Path) -> dict:
    """建议6 Slack 通知待办: workflow 引用 SLACK_WEBHOOK_URL 的状态。"""
    desc = "Slack 通知(SLACK_WEBHOOK_URL secret)"
    wf = root / GUARD_WORKFLOW
    if not wf.exists():
        return _item("M6", GUARD_WORKFLOW, desc, "pass", "guard workflow 不存在, 无 Slack 步骤")
    text = wf.read_text(encoding="utf-8", errors="replace")
    if "SLACK_WEBHOOK_URL" not in text:
        return _item("M6", GUARD_WORKFLOW, desc, "pass", "workflow 未引用 SLACK_WEBHOOK_URL")
    return _item("M6", GUARD_WORKFLOW, desc, "WARN",
                 "Slack 步骤已就绪但 secret 配置状态需人工核对: "
                 "gh secret list --repo nzt47/security-tools 或 GitHub 网页 → Settings → Secrets")


def check_m7() -> dict:
    """建议7 已知残留: CI_FIX_INDEX.md 未提交修改状态。"""
    desc = "已知残留文件核对(CI_FIX_INDEX.md)"
    r = _run(["git", "status", "--porcelain=v1", "--", KNOWN_REMAINING])
    if r.stdout.strip():
        return _item("M7", KNOWN_REMAINING, desc, "WARN",
                     "存在未提交修改(并行会话进行中), 保持现状或由对应会话自行提交")
    return _item("M7", KNOWN_REMAINING, desc, "pass", "无未提交修改(残留已清除)")


def main() -> int:
    ap = argparse.ArgumentParser(description="维护巡检(总结报告 §6 建议自动化)")
    ap.add_argument("--repo-root", default=".", help="仓库根目录(默认当前目录)")
    ap.add_argument("--with-hook-test", action="store_true",
                    help="M1 含 hook 拦截稳定性实测(会真实触发 git commit, 需 TLM_HOOK_SOURCE_REPO)")
    ap.add_argument("--json", action="store_true", help="stdout 仅输出 JSON")
    ap.add_argument("--quiet", action="store_true", help="仅输出结果汇总")
    args = ap.parse_args()

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"[ERROR] 仓库根目录不存在: {root}", file=sys.stderr)
        return 1

    items = [
        check_m1(args.with_hook_test),
        check_m2(),
        check_m3(root),
        check_m4(root),
        check_m5(root),
        check_m6(root),
        check_m7(),
    ]

    blocked = [i for i in items if i["status"] == "BLOCK"]
    warned = [i for i in items if i["status"] == "WARN"]
    report = {
        "tool": "maintenance_check",
        "status": "fail" if blocked else "pass",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "meta": {
            "repo_root": str(root),
            "source": "docs/observability/bom_fix_env_hardening_summary_20260805.md §6",
            "total": len(items),
            "blocked": len(blocked),
            "warned": len(warned),
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

    status = "PASS" if not blocked else "FAIL"
    print(f"=== maintenance_check: {status} 通过 {len(items) - len(blocked)}/{len(items)}"
          f" (BLOCK {len(blocked)} / WARN {len(warned)}) ===")
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
