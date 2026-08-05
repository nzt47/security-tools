"""未入库依赖与 .pyc 缓存陷阱巡检工具

分类检测:
    1. workflow 引用的 .py 是否全部入库(git ls-tree HEAD)
    2. __pycache__/*.pyc 对应 .py 源文件是否丢失 / 未跟踪 / 曾有历史

Why:
- run_ci_guard 事件复盘(2026-08-05)发现: 模块仅存 .pyc 从未入库 → 本地假绿, CI 必挂。
- 本工具作为预防建议第 3 条落地, 定期巡检暴露同类隐患。
  参见 docs/observability/ci_hidden_failure_fix_report_20260805.md

用法:
    python scripts/scan_missing_deps.py            # 默认扫描当前仓库
    python scripts/scan_missing_deps.py --json     # 结构化 JSON 输出
    python scripts/scan_missing_deps.py --repo-root <path>
    python scripts/scan_missing_deps.py --strict   # 发现 LOST 即 exit 1(CI 守卫用)
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone


def _git(root: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True,
        encoding="utf-8", errors="replace")


def scan(repo_root: str) -> dict:
    """执行扫描, 返回结构化结果 {lost, workflow_refs, security_tools, timestamp}"""
    root = os.path.abspath(repo_root)
    r = _git(root, ["ls-tree", "-r", "--name-only", "HEAD"])
    tracked = set(r.stdout.splitlines())
    tracked_py = {p for p in tracked if p.endswith(".py")}

    # ── 1. workflow 引用但未入库的 .py ──
    workflow_refs_missing: list[str] = []
    wf_glob = os.path.join(root, ".github", "workflows", "*.yml")
    for wf in glob.glob(wf_glob):
        try:
            text = open(wf, encoding="utf-8").read()
        except OSError:
            continue
        for m in re.finditer(r"(?:run:|paths:\s*-)\s*['\"]?([\w./-]+\.py)", text):
            ref = m.group(1)
            if ref.startswith(("scripts/", "tests/", ".github/")):
                if ref not in tracked_py and not os.path.exists(
                        os.path.join(root, ref)):
                    workflow_refs_missing.append(f"{os.path.basename(wf)} -> {ref}")

    # ── 2. .pyc 缓存陷阱分类 ──
    lost: list[str] = []
    for cache in glob.glob(os.path.join(root, "**", "__pycache__"),
                           recursive=True):
        for pyc in glob.glob(os.path.join(cache, "*.pyc")):
            base = os.path.splitext(os.path.basename(pyc))[0]
            mod = base.split(".cpython-")[0]
            rel_dir = os.path.relpath(os.path.dirname(cache), root)
            rel_py = os.path.join(rel_dir, mod + ".py").replace("\\", "/")
            if rel_py in tracked_py:
                continue
            disk_py = os.path.join(root, rel_py.replace("/", os.sep))
            if not os.path.exists(disk_py):
                r2 = _git(root, ["log", "--all", "--oneline", "--", rel_py])
                hist = "HISTORY" if r2.stdout.strip() else "NEVER-COMMITTED"
                lost.append({"path": rel_py, "class": f"LOST[{hist}]"})

    # ── 3. security-tools 嵌套仓库标记 ──
    st = os.path.join(root, "security-tools")
    security_tools = None
    if os.path.isdir(os.path.join(st, ".git")):
        security_tools = {
            "path": "security-tools/",
            "note": ".gitignore 忽略的独立嵌套仓库副本, 不处理",
            "py_count": len(glob.glob(os.path.join(st, "**", "*.py"),
                                      recursive=True)),
        }

    return {
        "tool": "scan_missing_deps",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repo_root": root,
        "workflow_refs_missing": sorted(set(workflow_refs_missing)),
        "lost": sorted(lost, key=lambda x: x["path"]),
        "security_tools": security_tools,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="未入库依赖/.pyc 缓存陷阱巡检")
    p.add_argument("--repo-root", default=".",
                   help="仓库根目录(默认当前目录)")
    p.add_argument("--json", action="store_true", help="输出结构化 JSON")
    p.add_argument("--strict", action="store_true",
                   help="存在 LOST/缺失引用时 exit 1(CI 守卫模式)")
    args = p.parse_args()

    result = scan(args.repo_root)
    n_lost = len(result["lost"])
    n_missing = len(result["workflow_refs_missing"])

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("=" * 64)
        print(f"未入库依赖/.pyc 缓存陷阱巡检 | {result['repo_root']}")
        print("=" * 64)
        if result["workflow_refs_missing"]:
            print(f"[1] workflow 引用但未入库的 .py ({n_missing} 项):")
            for line in result["workflow_refs_missing"]:
                print(f"    {line}")
        else:
            print("[1] workflow 引用但未入库的 .py: 无")
        print(f"\n[2] .pyc 缓存陷阱(仅有 .pyc 无 .py, {n_lost} 项):")
        for item in result["lost"]:
            print(f"    {item['class']}: {item['path']}")
        if result["security_tools"]:
            st = result["security_tools"]
            print(f"\n[3] security-tools/: 嵌套独立仓库副本({st['py_count']} 个 .py), "
                  f"{st['note']}")
        print("\n结论:", "发现隐患" if (n_lost or n_missing) else "无隐患")

    if args.strict and (n_lost or n_missing):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
