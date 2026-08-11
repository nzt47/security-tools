#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按拓扑排序自动执行知识库重构计划的 8 个任务（T0-T7）。

每个任务按"验收门禁"执行：
  1. 前置依赖任务必须已 PASS（否则 BLOCKED，跳过）。
  2. 检查任务的交付物文件是否齐全。
  3. 交付物齐全后运行该任务对应的 pytest 测试。
  4. 汇总状态：PASS / FAIL / MISSING / BLOCKED。

执行顺序来自任务文档 `**前置依赖**` 字段的拓扑排序（与 verify_knowledge_plan_deps.py 一致）。

用法：
  python scripts/run_knowledge_tasks.py                 # 全量执行
  python scripts/run_knowledge_tasks.py --dry-run       # 仅检查交付物，不运行测试
  python scripts/run_knowledge_tasks.py --tasks T2 T4   # 只执行指定任务（仍校验依赖）
  python scripts/run_knowledge_tasks.py --verbose       # 输出 pytest 完整日志

退出码：0=全部 PASS；1=存在 FAIL；2=存在 MISSING/BLOCKED（无 FAIL）。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

TASK_ID_RE = re.compile(r"\*\*任务ID\*\*\s*[:：]\s*(T\d+)")
DEP_LINE_RE = re.compile(r"\*\*前置依赖\*\*\s*[:：]\s*(.*)")
TASK_REF_RE = re.compile(r"任务(\d+)")

# 每个任务的验收契约（来自各任务文档的"交付物清单"与"测试运行命令"）。
# 交付物全部命中才算该任务"已实现"，随后运行 tests。
TASK_SPECS: dict[str, dict] = {
    "T0": {
        "name": "知识库宪法与目录/卡片 Schema 定义",
        "deliverables": [
            "knowledge/AGENTS.md",
            "knowledge/index.md",
            "knowledge/log.md",
            "agent/knowledge/__init__.py",
            "agent/knowledge/schema.py",
            "agent/knowledge/lifecycle.py",
        ],
        "tests": [
            "tests/unit/test_knowledge_schema.py",
            "tests/unit/test_knowledge_lifecycle.py",
        ],
    },
    "T1": {
        "name": "素材层 Ingest 管道",
        "deliverables": [
            "agent/knowledge/ingest.py",
            "agent/knowledge/logbook.py",
            "agent/knowledge/watcher.py",
            "agent/knowledge/__main__.py",
        ],
        "tests": [
            "tests/unit/test_knowledge_ingest.py",
            "tests/unit/test_knowledge_watcher.py",
        ],
    },
    "T2": {
        "name": "知识层卡片引擎",
        "deliverables": [
            "agent/knowledge/card.py",
            "agent/knowledge/links.py",
            "agent/knowledge/index.py",
        ],
        "tests": [
            "tests/unit/test_knowledge_card.py",
            "tests/unit/test_knowledge_links.py",
            "tests/unit/test_knowledge_lifecycle_store.py",
        ],
    },
    "T3": {
        "name": "中间层提炼管线",
        "deliverables": [
            "agent/knowledge/distill.py",
            "agent/knowledge/prompts.py",
        ],
        "tests": ["tests/unit/test_knowledge_distill.py"],
    },
    "T4": {
        "name": "知识检索整合",
        "deliverables": ["agent/knowledge/search.py"],
        "tests": ["tests/unit/test_knowledge_search.py"],
    },
    "T5": {
        "name": "治理巡检与冲突裁决",
        "deliverables": [
            "agent/knowledge/lint.py",
            "agent/knowledge/conflict.py",
        ],
        "tests": [
            "tests/unit/test_knowledge_lint.py",
            "tests/unit/test_knowledge_conflict.py",
            "tests/unit/test_knowledge_incremental.py",
        ],
    },
    "T6": {
        "name": "前端知识库视图与 API",
        "deliverables": [
            "agent/server_routes/routes_knowledge.py",
            "yunshu-ui/src/pages/Knowledge.tsx",
            "yunshu-ui/src/api/knowledge.ts",
        ],
        "tests": ["tests/unit/test_routes_knowledge.py"],
    },
    "T7": {
        "name": "人机协同闭环工作流",
        "deliverables": [
            "agent/knowledge/workflow.py",
            "agent/knowledge/discuss.py",
            "scripts/knowledge_demo.py",
        ],
        "tests": ["tests/unit/test_knowledge_workflow.py"],
    },
}


def parse_task_file(path: Path) -> tuple[str | None, list[str]]:
    """解析任务文件，返回 (任务ID, 前置依赖ID列表)。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    m = TASK_ID_RE.search(text)
    tid = m.group(1) if m else None
    deps: list[str] = []
    m = DEP_LINE_RE.search(text)
    if m:
        line = m.group(1)
        if "无" not in line.strip():
            deps = [f"T{n}" for n in TASK_REF_RE.findall(line)]
    return tid, deps


def topo_order(graph: dict[str, set[str]], nodes: set[str]) -> list[str]:
    """Kahn 拓扑排序（前置先于依赖者）。"""
    indeg = {n: len(graph.get(n, set())) for n in nodes}
    rev: dict[str, set[str]] = defaultdict(set)
    for u, vs in graph.items():
        for v in vs:
            rev[v].add(u)
    q = deque(sorted(n for n in nodes if indeg[n] == 0))
    order: list[str] = []
    while q:
        u = q.popleft()
        order.append(u)
        for v in sorted(rev.get(u, [])):
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    return order


def run_tests(tests: list[str], verbose: bool) -> bool:
    """运行 pytest；测试文件缺失返回 False 并提示。"""
    missing = [t for t in tests if not (PROJECT_ROOT / t).exists()]
    if missing:
        print(f"    [警告] 测试文件缺失（未运行）: {', '.join(missing)}")
        return False
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, "-m", "pytest", *tests, "-p", "no:cacheprovider", "--no-header"]
    proc = subprocess.run(
        cmd, cwd=PROJECT_ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if verbose:
        print(proc.stdout[-4000:])
        if proc.stderr:
            print(proc.stderr[-2000:])
    return proc.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="按拓扑顺序执行知识库重构任务（验收门禁）")
    parser.add_argument("--tasks-dir", default=str(PROJECT_ROOT / "docs/zh/知识库重构计划"),
                        help="任务文件目录（默认 docs/zh/知识库重构计划）")
    parser.add_argument("--dry-run", action="store_true", help="仅检查交付物，不运行测试")
    parser.add_argument("--tasks", nargs="+", default=None, help="只执行指定任务，如 --tasks T2 T4")
    parser.add_argument("--verbose", action="store_true", help="输出 pytest 完整日志")
    args = parser.parse_args()

    task_dir = Path(args.tasks_dir)
    if not task_dir.is_dir():
        print(f"[错误] 任务目录不存在: {task_dir}", file=sys.stderr)
        return 2

    # 解析任务文件 → 依赖图
    files = sorted(task_dir.glob("任务*.md"))
    task_id_of: dict[str, str] = {}
    raw_deps: dict[str, list[str]] = {}
    for f in files:
        tid, deps = parse_task_file(f)
        if tid is not None:
            task_id_of[tid] = f.name
            raw_deps[tid] = deps

    unknown = set(raw_deps) - set(TASK_SPECS)
    if unknown:
        print(f"[警告] 发现未在 TASK_SPECS 中定义的任务: {sorted(unknown)}，将跳过")
    known = set(raw_deps) & set(TASK_SPECS)
    if not known:
        print("[错误] 未解析到任何已知任务（T0-T7）", file=sys.stderr)
        return 2

    graph = {tid: {d for d in deps if d in known} for tid, deps in raw_deps.items() if tid in known}
    order = topo_order(graph, known)

    requested = set(args.tasks) if args.tasks else None
    if requested:
        unknown_req = requested - known
        if unknown_req:
            print(f"[错误] 指定了不存在的任务: {sorted(unknown_req)}", file=sys.stderr)
            return 2
        # 执行指定任务时，按拓扑顺序过滤，但保留其依赖先执行
        requested_with_deps: set[str] = set()
        def collect_deps(t: str) -> None:
            if t in requested_with_deps:
                return
            requested_with_deps.add(t)
            for d in graph.get(t, ()):
                collect_deps(d)
        for t in requested:
            collect_deps(t)
        order = [t for t in order if t in requested_with_deps]

    # 依次执行
    status: dict[str, str] = {}
    results: dict[str, list[str]] = {}

    print(f"=== 知识库重构任务自动执行 ===")
    print(f"任务目录: {task_dir}")
    print(f"执行顺序（拓扑排序）: {' -> '.join(order)}")
    print(f"模式: {'dry-run（不运行测试）' if args.dry_run else '完整（交付物检查 + 测试）'}")
    print()

    for tid in order:
        spec = TASK_SPECS[tid]
        print(f"--- {tid} {spec['name']} ---")

        # 1) 依赖门禁
        blocked_by = [d for d in sorted(graph.get(tid, ())) if status.get(d) != "PASS"]
        if blocked_by:
            status[tid] = "BLOCKED"
            results[tid] = [f"前置依赖未通过: {', '.join(blocked_by)}"]
            print(f"  [BLOCKED] 前置依赖 {', '.join(blocked_by)} 未通过，跳过")
            print()
            continue

        # 2) 交付物检查
        missing = [d for d in spec["deliverables"] if not (PROJECT_ROOT / d).exists()]
        if missing:
            status[tid] = "MISSING"
            results[tid] = [f"交付物缺失: {', '.join(missing)}"]
            print(f"  [MISSING] 交付物缺失 {len(missing)}/{len(spec['deliverables'])}:")
            for d in missing:
                print(f"    - {d}")
            print()
            continue

        print(f"  交付物齐全（{len(spec['deliverables'])} 项）")

        # 3) 测试门禁
        if args.dry_run:
            status[tid] = "PASS"  # dry-run 视为通过（交付物齐全）
            results[tid] = ["交付物齐全（dry-run 未运行测试）"]
            print("  [PASS] 交付物齐全（dry-run）")
        else:
            ok = run_tests(spec["tests"], args.verbose)
            if ok:
                status[tid] = "PASS"
                results[tid] = [f"测试通过: {', '.join(spec['tests'])}"]
                print("  [PASS]")
            else:
                status[tid] = "FAIL"
                results[tid] = ["测试失败或缺失"]
                print("  [FAIL]")
        print()

    # 汇总
    print("=== 汇总 ===")
    summary = {"PASS": [], "FAIL": [], "MISSING": [], "BLOCKED": []}
    for tid in order:
        summary[status[tid]].append(tid)
    for state, tids in summary.items():
        if tids:
            print(f"  {state:<8} {len(tids):>2}: {', '.join(tids)}")

    print()
    for tid in order:
        print(f"  {tid} [{status[tid]}] {TASK_SPECS[tid]['name']}")
        for line in results[tid]:
            print(f"      {line}")

    if summary["FAIL"]:
        return 1
    if summary["MISSING"] or summary["BLOCKED"]:
        return 2
    print()
    print("全部任务通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
