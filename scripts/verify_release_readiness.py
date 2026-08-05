#!/usr/bin/env python3
"""发布就绪检查 — 将 RELEASE_PROCESS_TEMPLATE.md §8 检查清单自动化

三态分级（与 scripts/env_health_check.py 同契约）:
    PASS  — 通过
    WARN  — 有风险但可继续（不阻断）
    BLOCK — 阻断发布（退出码 1）

用法:
    python scripts/verify_release_readiness.py --version v1.5.0-bm25-normalization
    python scripts/verify_release_readiness.py --version v1.5.0 --remote origin,gitee --json
    python scripts/verify_release_readiness.py --version v1.5.0 --quiet   # 仅输出 BLOCK

【不易】BLOCK 项必须解决才能发布；WARN 项记录风险不阻断
【变易】--remote 可扩展多远程；--json 供 CI 消费
【简易】8 项检查对应模板 §8 检查清单，逐项独立可读
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# 临时文件残留模式（发布后应收尾清理）
TMP_PATTERNS = ("*.tmp", ".commit_msg_*", ".release_notes_*", "__bom_hook_stability_*")


def _run(args: list[str]) -> str:
    p = subprocess.run(args, capture_output=True, text=True, cwd=REPO_ROOT)
    return p.stdout.strip()


def git(*args: str) -> str:
    return _run(["git", *args])


def gh(*args: str) -> str:
    return _run(["gh", *args])


# ════════════════════════════════════════════════════════════
#  检查项（与 RELEASE_PROCESS_TEMPLATE.md §8 一一对应）
# ════════════════════════════════════════════════════════════

def check_working_tree() -> tuple[str, str]:
    """§8-1 前置检查: 工作树干净"""
    dirty = git("status", "--porcelain")
    if dirty:
        n = len(dirty.splitlines())
        return "BLOCK", f"工作树非干净（{n} 个文件待处理），发布前须提交或暂存"
    return "PASS", "工作树干净"


def check_branch_sync() -> tuple[str, str]:
    """§8-1 前置检查: master 与 origin 同步"""
    ahead = int(git("rev-list", "--count", "origin/master..master") or 0)
    behind = int(git("rev-list", "--count", "master..origin/master") or 0)
    if ahead or behind:
        return "WARN", f"master 与 origin/master 不同步（ahead={ahead} behind={behind}），建议先 fetch/push"
    return "PASS", "master 与 origin/master 同步"


def check_release_notes(version: str) -> tuple[str, str]:
    """§8-2 RELEASE_NOTES 已更新"""
    path = REPO_ROOT / "RELEASE_NOTES.md"
    if not path.exists():
        return "BLOCK", "RELEASE_NOTES.md 不存在，须先创建发布说明"
    if f"## {version}" not in path.read_text(encoding="utf-8"):
        return "BLOCK", f"RELEASE_NOTES.md 缺少版本章节 '## {version}'"
    return "PASS", f"RELEASE_NOTES.md 含 {version} 章节"


def check_local_tag(version: str) -> tuple[str, str]:
    """§8-3 Tag 已创建"""
    if git("rev-parse", "-q", "--verify", f"refs/tags/{version}"):
        return "PASS", f"本地 tag {version} 存在"
    return "BLOCK", f"本地 tag {version} 不存在（先 git tag -a {version}）"


def check_remote_tags(version: str, remotes: list[str]) -> tuple[str, str]:
    """§8-3 Tag 已推送（origin + gitee）"""
    missing = []
    for r in remotes:
        out = _run(["git", "ls-remote", "--tags", r, version])
        if f"refs/tags/{version}" not in out:
            missing.append(r)
    if missing:
        return "WARN", f"tag {version} 未推送到远程: {', '.join(missing)}"
    return "PASS", f"tag {version} 已推送: {', '.join(remotes)}"


def check_release(version: str) -> tuple[str, str]:
    """§8-4 GitHub Release 已创建（非 draft/prerelease）"""
    try:
        rel = json.loads(gh("release", "view", version, "--json", "isDraft,isPrerelease"))
    except Exception:  # noqa: BLE001  gh 未认证/Release 未创建
        return "WARN", f"gh release view {version} 失败（Release 未创建或 gh 未认证）"
    if rel.get("isDraft") or rel.get("isPrerelease"):
        return "WARN", f"Release {version} 是 draft/prerelease，须正式发布"
    return "PASS", f"GitHub Release {version} 已正式发布"


def check_release_ci(version: str) -> tuple[str, str]:
    """§8-5 Release 事件 CI conclusion=success"""
    try:
        runs = json.loads(gh("run", "list", "--event", "release", "--limit", "5",
                             "--json", "displayTitle,conclusion,headBranch"))
    except Exception:  # noqa: BLE001  gh 未认证
        return "WARN", "gh run list 失败（gh 未认证或网络异常）"
    latest = next((r for r in runs if version in (r.get("headBranch") or "")), None)
    if not latest:
        return "WARN", f"未找到关联 {version} 的 release 事件运行"
    c = latest.get("conclusion")
    if c == "success":
        return "PASS", f"最近 release 事件 CI conclusion=success（{latest.get('displayTitle')}）"
    return "WARN", f"最近 release 事件 CI conclusion={c}，发布前须确认"


def check_tmp_files() -> tuple[str, str]:
    """§8-7 临时文件已清理"""
    found = []
    for pat in TMP_PATTERNS:
        found.extend(REPO_ROOT.glob(pat))
    if found:
        names = ", ".join(f.name for f in found[:5])
        return "WARN", f"发现临时文件残留: {names}"
    return "PASS", "无临时文件残留"


# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(description="发布就绪检查（RELEASE_PROCESS_TEMPLATE.md §8 自动化）")
    ap.add_argument("--version", required=True, help="版本号，如 v1.5.0-bm25-normalization")
    ap.add_argument("--remote", default="origin,gitee", help="远程列表（逗号分隔，默认 origin,gitee）")
    ap.add_argument("--json", action="store_true", help="JSON 输出（供 CI 消费）")
    ap.add_argument("--quiet", action="store_true", help="仅输出 BLOCK 明细")
    args = ap.parse_args()

    checks = [
        ("工作树干净", check_working_tree()),
        ("master 同步", check_branch_sync()),
        ("RELEASE_NOTES 章节", check_release_notes(args.version)),
        ("本地 tag", check_local_tag(args.version)),
        ("远程 tag 推送", check_remote_tags(args.version, args.remote.split(","))),
        ("GitHub Release", check_release(args.version)),
        ("Release 事件 CI", check_release_ci(args.version)),
        ("临时文件残留", check_tmp_files()),
    ]
    blocked = [name for name, (st, _) in checks if st == "BLOCK"]

    if args.json:
        print(json.dumps({
            "version": args.version,
            "checks": [{"name": n, "status": st, "message": msg} for n, (st, msg) in checks],
            "blocked": blocked,
        }, ensure_ascii=False, indent=2))
    else:
        for name, (st, msg) in checks:
            if not (args.quiet and st != "BLOCK"):
                print(f"[{st:<5}] {name}: {msg}")
        print("-" * 60)
        total = len(checks)
        if blocked:
            print(f"存在 BLOCK 项（{len(blocked)}/{total}），发布前必须解决: {', '.join(blocked)}")
        else:
            print(f"发布就绪检查通过（{total}/{total} 项）")

    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
