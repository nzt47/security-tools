#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""根目录整洁度检查（TASK-02 · W1 仓库卫生）。

功能
----
统计**仓库根目录**（不含子目录）的文件数、类型分布与"一次性产物"数量，
并对阈值给出结论。用于 pre-commit 或 CI 的**可选**接入点——本脚本
**不**修改 .pre-commit-config.yaml / .github/workflows（接入由 TASK-06 决定）。

用法
----
    python scripts/check_root_hygiene.py                 # 人类可读报告
    python scripts/check_root_hygiene.py --json          # 机器可读（CI 用）
    python scripts/check_root_hygiene.py --max-py 10 --max-txt 10

退出码
------
    0 = 全部阈值达标
    1 = 有阈值被突破（报告里逐项列出）
    2 = 用法/环境错误（例如不在仓库根运行）

设计约束（为什么这样写）
------------------------
* **只读**：本脚本不创建、不移动、不删除任何文件。根目录已被
  `quality_gate_report.json` 这类"测试写回仓库根"的事故污染过一次
  （见 docs/closeout/REPO_HYGIENE_W1_TASK02_20260921.md），
  一个"整洁度检查器"自己去写根目录是自相矛盾的。
* 一次性产物的判据是**保守**的：只认下划线前缀的探针 + 已知调试扩展名，
  不猜测"看起来像临时文件"。误报会让门禁变成噪声，噪声会被绕过。
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# 一次性/调试产物的文件名模式（保守判据）。
# 与 .gitignore 的「根目录临时脚本/日志」段保持一致（/_*.py、/_*.txt、/*.log 等）。
ONESHOT_PREFIXES = ("_",)
ONESHOT_SUFFIXES = (".log",)
# 明确属于"必要文件"、即使命中模式也不计为一次性产物的白名单。
ONESHOT_WHITELIST = frozenset()

# 默认阈值：来自 TASK-02 · W1 的验收目标（根目录 .py 40 -> <=10）。
DEFAULT_MAX_PY = 10
DEFAULT_MAX_TXT = 10
DEFAULT_MAX_ONESHOT = 0


def repo_root() -> Path:
    """仓库根 = 本脚本的上一级目录（不依赖 CWD）。"""
    return Path(__file__).resolve().parents[1]


def _tracked_root_names(root: Path):
    """根目录下**受版本控制**的文件名集合（git ls-files，只取无 '/' 的项）。"""
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=str(root), capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return {line for line in out.stdout.splitlines() if line and "/" not in line}


def is_oneshot(name: str) -> bool:
    """是否属于"一次性产物"（保守判据）。"""
    if name in ONESHOT_WHITELIST:
        return False
    return name.startswith(ONESHOT_PREFIXES) or name.endswith(ONESHOT_SUFFIXES)


def collect(root: Path) -> dict:
    """扫描根目录（仅顶层文件），返回统计字典。"""
    entries = [p for p in root.iterdir() if p.is_file()]
    names = sorted(p.name for p in entries)
    tracked = _tracked_root_names(root)

    def _count(pred):
        return sum(1 for n in names if pred(n))

    oneshot = sorted(n for n in names if is_oneshot(n))
    return {
        "root": str(root),
        "files_total": len(names),
        "py": _count(lambda n: n.endswith(".py")),
        "txt": _count(lambda n: n.endswith(".txt")),
        "log": _count(lambda n: n.endswith(".log")),
        "oneshot": len(oneshot),
        "oneshot_names": oneshot,
        "tracked": (len(tracked) if tracked is not None else None),
        "tracked_unknown_reason": (
            None if tracked is not None else "git ls-files 不可用（非 git 工作区？）"
        ),
    }


def evaluate(stats: dict, max_py: int, max_txt: int, max_oneshot: int):
    """返回违规项列表；空列表 = 达标。"""
    violations = []
    if stats["py"] > max_py:
        violations.append(
            f"根目录 .py = {stats['py']} > 阈值 {max_py}（把一次性脚本移到 _scratch/ 或 scripts/）")
    if stats["txt"] > max_txt:
        violations.append(
            f"根目录 .txt = {stats['txt']} > 阈值 {max_txt}（调试输出请落到 _scratch/ 或临时目录）")
    if stats["oneshot"] > max_oneshot:
        violations.append(
            f"根目录一次性产物 = {stats['oneshot']} > 阈值 {max_oneshot}"
            "（命名以下划线开头或以 .log 结尾）")
    return violations


def print_human(stats: dict, violations, max_py: int, max_txt: int, max_oneshot: int) -> None:
    line = "=" * 66
    print(line)
    print("根目录整洁度检查")
    print(line)
    print(f"仓库根            : {stats['root']}")
    print(f"根目录文件总数    : {stats['files_total']}")
    print(f"  受版本控制      : {stats['tracked']}"
          + (f"  ({stats['tracked_unknown_reason']})" if stats["tracked"] is None else ""))
    print(f"  .py             : {stats['py']}   (阈值 <= {max_py})")
    print(f"  .txt            : {stats['txt']}   (阈值 <= {max_txt})")
    print(f"  .log            : {stats['log']}")
    print(f"  一次性产物      : {stats['oneshot']}   (阈值 <= {max_oneshot})")
    if stats["oneshot_names"]:
        print(line)
        print("一次性产物清单:")
        for n in stats["oneshot_names"]:
            print(f"  - {n}")
    print(line)
    if violations:
        print("[FAIL] 未达标:")
        for v in violations:
            print(f"  - {v}")
        print("\n提示：归档（移动）而不是删除 —— 保留可追溯性，且删除不可逆。")
    else:
        print("[OK] 根目录整洁度达标。")
    print(line)


def main() -> int:
    # 【不易】Windows 控制台默认 GBK，直接 print 非 GBK 字符（emoji/部分符号）会抛
    # UnicodeEncodeError 让门禁"假红"——本仓库已为此修过两个门禁。
    # 双保险：① stdout 以 errors="replace" 重配；② 本脚本输出一律用 ASCII 标记。
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover - 老 Python / 非常规 stdout
        pass

    parser = argparse.ArgumentParser(description="根目录整洁度检查（只读）")
    parser.add_argument("--json", action="store_true", help="输出 JSON（CI 消费）")
    parser.add_argument("--max-py", type=int, default=DEFAULT_MAX_PY,
                        help=f"根目录 .py 数量上限（默认 {DEFAULT_MAX_PY}）")
    parser.add_argument("--max-txt", type=int, default=DEFAULT_MAX_TXT,
                        help=f"根目录 .txt 数量上限（默认 {DEFAULT_MAX_TXT}）")
    parser.add_argument("--max-oneshot", type=int, default=DEFAULT_MAX_ONESHOT,
                        help=f"根目录一次性产物数量上限（默认 {DEFAULT_MAX_ONESHOT}）")
    args = parser.parse_args()

    root = repo_root()
    if not root.is_dir():
        print(f"[root-hygiene] 仓库根不存在: {root}", file=sys.stderr)
        return 2

    stats = collect(root)
    violations = evaluate(stats, args.max_py, args.max_txt, args.max_oneshot)

    if args.json:
        payload = dict(stats)
        payload["thresholds"] = {
            "max_py": args.max_py, "max_txt": args.max_txt,
            "max_oneshot": args.max_oneshot,
        }
        payload["violations"] = violations
        payload["ok"] = not violations
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_human(stats, violations, args.max_py, args.max_txt, args.max_oneshot)

    return 0 if not violations else 1


if __name__ == "__main__":
    sys.exit(main())
