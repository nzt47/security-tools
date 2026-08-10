#!/usr/bin/env python3
"""pre_commit_ci_guard 安装器 — 部署 guard 脚本并注册 pre-commit hook

【不易】只做两件事：把 guard 脚本复制到 <repo>/scripts/，写入 <repo>/.git/hooks/pre-commit。
【变易】--repo 支持任意仓库；--uninstall / --check 支持回滚与状态确认。
【简易】仅标准库，跨平台（Win/Mac/Linux）；失败时给出明确提示且不遗留半成品。

用法：
    python install.py [--repo <仓库根目录>]   # 默认当前目录；部署脚本 + 安装 hook
    python install.py --check [--repo ...]    # 检查部署状态
    python install.py --uninstall [--repo ...]# 移除 hook（不删除 guard 脚本）
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# ─── 常量 ────────────────────────────────────────────────────────────
# 与 scripts/pre_commit_ci_guard.py --install-hook 输出保持一致（容错 + 增量阻断 + 链式框架警告版）
HOOK_FAULT_TOLERANT = """#!/bin/sh
# 提交前 CI 护栏（避坑指南检查清单）— 存在性容错：脚本未部署到本 worktree 时跳过；
# --strict 增量阻断：存量 WARN 豁免（基线文件），新增 WARN 阻断提交；
# 链式调用 pre-commit 框架 commit-stage hooks（未安装 pre-commit 时跳过；失败仅警告放行）
GUARD="$(git rev-parse --show-toplevel)/scripts/pre_commit_ci_guard.py"
if [ ! -f "$GUARD" ]; then
  echo "[pre-commit-guard] 未部署 $GUARD，本次跳过（如需启用请部署脚本）"
  exit 0
fi
python "$GUARD" --static-only --strict || exit 1
if command -v pre-commit >/dev/null 2>&1; then
  if pre-commit run --hook-stage commit; then
    :
  else
    echo "[pre-commit-guard] 注意：pre-commit 框架 hook 未全部通过（详见 pre-commit 日志）。本次提交继续，请尽快处理框架问题。"
  fi
fi
"""
HOOK_TAG = "提交前 CI 护栏（避坑指南检查清单）"  # 用于识别 hook 是否为本工具安装
# pre-push：链式调用 pre-commit 框架 push 阶段 hooks（MEDIUM 提醒，失败仅警告不拦截推送）
PRE_PUSH_HOOK = """#!/bin/sh
# 推送前 pre-commit 框架 push 阶段 hooks（MEDIUM 提醒）— 链式调用 pre-commit 框架；
# 未安装 pre-commit 时跳过；框架失败仅警告放行（push 阶段为提醒级，不拦截推送）
if command -v pre-commit >/dev/null 2>&1; then
  if pre-commit run --hook-stage push; then
    :
  else
    echo "[pre-push-guard] 注意：pre-commit 框架 push 阶段 hook 未全部通过（详见 pre-commit 日志）。本次推送继续，请尽快处理。"
  fi
fi
exit 0
"""
PRE_PUSH_TAG = "推送前 pre-commit 框架 push 阶段 hooks（MEDIUM 提醒）"  # 识别 pre-push 是否为本工具安装


def _repo(args: argparse.Namespace) -> Path:
    return Path(args.repo).expanduser().resolve()


def _guard_dest(repo: Path) -> Path:
    return repo / "scripts" / "pre_commit_ci_guard.py"


def _hook_path(repo: Path) -> Path:
    return repo / ".git" / "hooks" / "pre-commit"


def _ensure_git_repo(repo: Path) -> None:
    if not (repo / ".git").exists():
        sys.exit(f"[error] {repo} 不是 git 仓库（无 .git 目录），请用 --repo 指定仓库根目录")


def _deploy_guard(repo: Path) -> Path:
    """把本包自带的 guard 脚本复制到 <repo>/scripts/。返回目标路径。"""
    src = Path(__file__).resolve().parent / "pre_commit_ci_guard.py"
    if not src.exists():
        sys.exit(f"[error] 未找到 guard 脚本：{src}\n      请确认安装器与 pre_commit_ci_guard.py 放在同一目录")
    dest = _guard_dest(repo)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.read_text(encoding="utf-8", errors="replace") != src.read_text(encoding="utf-8"):
        print(f"[warn] {dest} 已存在且与发布包版本不同，将覆盖为发布包版本")
    shutil.copyfile(src, dest)
    print(f"[ok] guard 脚本已部署：{dest}")
    return dest


def _install_hook(repo: Path) -> None:
    """写入容错版 pre-commit hook。覆盖旧 hook 前先提示。"""
    hook = _hook_path(repo)
    if hook.exists():
        content = hook.read_text(encoding="utf-8", errors="replace")
        if HOOK_TAG in content:
            print(f"[info] 已有本工具安装的 hook，将覆盖为最新容错版")
        else:
            print(f"[warn] {hook} 已存在但非本工具安装（可能来自其他工具），将被覆盖。原内容备份为 pre-commit.bak")
            try:
                shutil.copyfile(hook, hook.with_name("pre-commit.bak"))
            except OSError as e:
                print(f"[warn] 备份失败（{e}），继续覆盖")
    hook.write_text(HOOK_FAULT_TOLERANT, encoding="utf-8")
    print(f"[ok] pre-commit hook 已安装（存在性容错版）：{hook}")
    # 同步 pre-push（框架 push 阶段提醒，失败不拦截推送）；非本工具 pre-push 先备份
    push_hook = repo / ".git" / "hooks" / "pre-push"
    if push_hook.exists() and PRE_PUSH_TAG not in push_hook.read_text(encoding="utf-8", errors="replace"):
        print(f"[warn] {push_hook} 已存在但非本工具安装，将被覆盖。原内容备份为 pre-push.bak")
        try:
            shutil.copyfile(push_hook, push_hook.with_name("pre-push.bak"))
        except OSError as e:
            print(f"[warn] 备份失败（{e}），继续覆盖")
    push_hook.write_text(PRE_PUSH_HOOK, encoding="utf-8")
    print(f"[ok] pre-push hook 已安装（框架 push 阶段提醒版）：{push_hook}")


def _verify(repo: Path) -> int:
    """端到端验证：hook 存在且为容错版、guard 可运行、退出码语义正确。"""
    guard = _guard_dest(repo)
    hook = _hook_path(repo)
    ok = True
    if not guard.exists():
        print(f"[FAIL] guard 脚本缺失：{guard}")
        ok = False
    else:
        print(f"[PASS] guard 脚本已部署：{guard}")
    if not hook.exists():
        print(f"[FAIL] pre-commit hook 缺失：{hook}")
        ok = False
    elif HOOK_TAG not in hook.read_text(encoding="utf-8", errors="replace"):
        print(f"[FAIL] hook 非本工具版本（可能被其他会话覆盖）：{hook}")
        ok = False
    else:
        print(f"[PASS] hook 为容错版：{hook}")
    push_hook = repo / ".git" / "hooks" / "pre-push"
    if push_hook.exists() and PRE_PUSH_TAG in push_hook.read_text(encoding="utf-8", errors="replace"):
        print(f"[PASS] pre-push 为框架 push 阶段提醒版：{push_hook}")
    else:
        print(f"[WARN] pre-push 缺失或非本工具版本（push 阶段 MEDIUM 提醒不会生效）：{push_hook}")
    if ok:
        r = subprocess.run([sys.executable, str(guard), "--static-only"], cwd=repo,
                           capture_output=True, text=True)
        tail = (r.stdout or "").strip().splitlines()
        print(f"[INFO] guard 静态检查：rc={r.returncode} {tail[-1] if tail else ''}")
        # 语义：FAIL 项才阻断提交，正常应 rc=0；rc=1 说明仓库当前有 FAIL 项
        ok = r.returncode == 0
        if not ok:
            print("[FAIL] guard 检查存在 FAIL 项，请先处理再提交（hook 会同样阻断）")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="pre_commit_ci_guard 安装器（发布包）")
    ap.add_argument("--repo", default=".", help="目标仓库根目录（默认当前目录）")
    ap.add_argument("--check", action="store_true", help="仅检查部署状态，不安装")
    ap.add_argument("--uninstall", action="store_true", help="移除 pre-commit hook（不删 guard 脚本）")
    args = ap.parse_args()

    repo = _repo(args)
    _ensure_git_repo(repo)

    if args.check:
        return _verify(repo)
    if args.uninstall:
        hook = _hook_path(repo)
        if hook.exists() and HOOK_TAG in hook.read_text(encoding="utf-8", errors="replace"):
            hook.unlink()
            print(f"[ok] 已移除 hook：{hook}")
        elif hook.exists():
            print(f"[warn] hook 非本工具安装，未删除（避免误删其他工具）：{hook}")
        else:
            print("[info] 未发现 pre-commit hook，无需卸载")
        push_hook = repo / ".git" / "hooks" / "pre-push"
        if push_hook.exists() and PRE_PUSH_TAG in push_hook.read_text(encoding="utf-8", errors="replace"):
            push_hook.unlink()
            print(f"[ok] 已移除 pre-push hook：{push_hook}")
        elif push_hook.exists():
            print(f"[warn] pre-push 非本工具安装，未删除：{push_hook}")
        else:
            print("[info] 未发现 pre-push hook，无需卸载")
        return 0

    _deploy_guard(repo)
    _install_hook(repo)
    print("[info] 验证安装结果：")
    return _verify(repo)


if __name__ == "__main__":
    sys.exit(main())
