#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L3 CI 预检脚本 — Docker build context 一致性校验

背景（2026-08-16 实证，见 docs/ci_l3_context_sync_verify_20260816.md）：
  L3 镜像 context 与工作区代码漂移（缺 agent/skills_mgmt/lineage.py 等已跟踪
  模块）导致挂载的最新 conftest.py import 失败 → 130 项测试全部 ERROR。
  本脚本在 CI 流水线 build-image 之前自动执行，把"测试阶段才暴露的镜像缺模块"
  提前到"构建前 fail fast"。

校验项：
  1. 关键构建文件存在（Dockerfile / compose / 预下载脚本）
  2. 关键业务模块存在（conftest autouse fixture 引用链 agent.skills_mgmt.*）
  3. agent/ 等被打包目录无未提交修改（保证镜像快照 == 工作区 == HEAD）
  4. 已跟踪文件在磁盘上的覆盖度（防"git 里有但 context 打包时缺失"）

用法：
  python scripts/ci_l3_context_preflight.py            # 默认文本报告
  python scripts/ci_l3_context_preflight.py --json     # JSON 输出（CI 友好）
  python scripts/ci_l3_context_preflight.py --git-clean-only  # 仅 git 一致性

退出码：0 = 全部通过；1 = 任一校验失败（CI 应中断流水线）。

接入 CI（.github/workflows/l3-docker-tests.yml，build-image job 内）：
  - name: L3 context 一致性预检
    run: python scripts/ci_l3_context_preflight.py --json
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# 【变易】PREFLIGHT_ROOT 环境变量可覆盖项目根（测试/沙箱注入点；默认取脚本所在仓库根）
PROJECT_ROOT = Path(
    os.environ.get("PREFLIGHT_ROOT")
    or Path(__file__).resolve().parent.parent
)

# 【不易】conftest.py autouse fixture 实际 import 的模块（缺任一即全量 ERROR）
CRITICAL_MODULES = [
    "agent/skills_mgmt/lineage.py",
    "agent/skills_mgmt/meta_editor.py",
    "agent/skills_mgmt/service.py",
    "memory/vector_store/vector_store.py",
    "memory/vector_store/sqlite_vec_backend.py",
]

# 构建链路必需文件
CRITICAL_BUILD_FILES = [
    "Dockerfile.linux-test",
    "docker-compose.linux-test.yml",
    "scripts/predownload_models.py",
    "scripts/run_l3_regression_tests.ps1",
]

# 进入镜像 context 的核心代码目录（校验无未提交修改 → 镜像快照一致）
CONTEXT_DIRS = ["agent", "memory", "scripts", "tests"]


def _git(*args: str) -> str:
    """运行 git 并返回 stdout（失败时抛 RuntimeError）"""
    proc = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {proc.stderr.strip()}")
    return proc.stdout.strip()


def check_build_files() -> list[str]:
    """校验 1：构建链路关键文件存在"""
    missing = [f for f in CRITICAL_BUILD_FILES if not (PROJECT_ROOT / f).exists()]
    return missing


def check_critical_modules() -> list[str]:
    """校验 2：conftest 引用链关键模块存在（缺任一 → 挂载 conftest import 失败）"""
    return [f for f in CRITICAL_MODULES if not (PROJECT_ROOT / f).exists()]


def check_git_clean() -> list[str]:
    """校验 3：被打包目录无未提交修改（镜像快照 == HEAD 的保证）"""
    dirty = []
    proc = subprocess.run(
        ["git", "status", "--porcelain", *CONTEXT_DIRS],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    for line in proc.stdout.splitlines():
        # 未跟踪（??）不影响镜像快照一致性（会进 context 但无 git 版本），
        # 仅 D/M/A 等已跟踪变更会导致"镜像缺/旧文件"。
        if not line.startswith("??"):
            dirty.append(line)
    return dirty


def check_tracked_coverage() -> list[str]:
    """校验 4：git 已跟踪文件在磁盘上的覆盖度（防 context 打包遗漏）"""
    missing = []
    tracked = _git("ls-files", "agent", "memory", "scripts")
    for rel in tracked.splitlines():
        if not (PROJECT_ROOT / rel).exists():
            missing.append(rel)
    return missing


def main(argv: list[str] | None = None) -> int:
    """预检入口（argv 可注入，便于测试；None 时取 sys.argv[1:]）"""
    parser = argparse.ArgumentParser(
        description="L3 Docker build context 一致性预检（CI 构建前 fail fast）"
    )
    parser.add_argument("--json", action="store_true", help="JSON 输出（CI 友好）")
    parser.add_argument("--git-clean-only", action="store_true",
                        help="仅执行 git 一致性校验（跳过文件存在性）")
    args = parser.parse_args(argv)

    checks = {
        "build_files": ("构建文件存在", check_build_files, not args.git_clean_only),
        "critical_modules": ("关键模块存在", check_critical_modules, not args.git_clean_only),
        "git_clean": ("context 目录无未提交修改", check_git_clean, True),
        "tracked_coverage": ("已跟踪文件磁盘覆盖", check_tracked_coverage, not args.git_clean_only),
    }

    results = {}
    failures = {}
    for key, (label, fn, enabled) in checks.items():
        if not enabled:
            continue
        try:
            issues = fn()
            results[key] = {"label": label, "ok": not issues, "issues": issues}
            if issues:
                failures[key] = issues
        except Exception as exc:  # 环境性失败（如 git 不可用）视作失败
            results[key] = {"label": label, "ok": False, "issues": [f"执行异常: {exc}"]}
            failures[key] = [f"执行异常: {exc}"]

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("=== L3 context 一致性预检 ===")
        for key, r in results.items():
            mark = "[OK]" if r["ok"] else "[FAIL]"
            print(f"{mark} {r['label']}")
            for issue in r["issues"]:
                print(f"      - {issue}")
        if failures:
            print("\n结论: FAILED — 修复上述问题后重试（避免镜像缺模块导致测试全量 ERROR）")
        else:
            print("\n结论: 全部通过，可进入镜像构建阶段")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
