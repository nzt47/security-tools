#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证知识库重构计划任务文件的依赖关系是否闭环。

功能：
1. 扫描任务目录下所有 `任务*.md` 文件，解析 `**任务ID**` 与 `**前置依赖**` 字段。
2. 构建依赖图，检测：
   - 循环依赖（闭环：A 依赖 B、B 依赖 A 或更长环）
   - 悬空依赖（引用了不存在的任务）
   - 任务 ID 缺失/重复
   - 未被任何任务依赖的"孤立任务"（警告，非致命）
3. 输出拓扑排序（建议执行顺序）。
4. 退出码：0=闭环通过，1=存在错误。

用法：
    python scripts/verify_knowledge_plan_deps.py [--dir 任务目录] [--no-warn]
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

TASK_ID_RE = re.compile(r"\*\*任务ID\*\*\s*[:：]\s*(T\d+)")
DEP_LINE_RE = re.compile(r"\*\*前置依赖\*\*\s*[:：]\s*(.*)")
TASK_REF_RE = re.compile(r"任务(\d+)")


def parse_task_file(path: Path) -> tuple[str | None, list[str]]:
    """解析单个任务文件，返回 (任务ID, 前置依赖任务ID列表)。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    m = TASK_ID_RE.search(text)
    task_id = m.group(1) if m else None
    deps: list[str] = []
    m = DEP_LINE_RE.search(text)
    if m:
        dep_line = m.group(1)
        if "无" not in dep_line.strip():
            deps = [f"T{n}" for n in TASK_REF_RE.findall(dep_line)]
    return task_id, deps


def find_cycle(graph: dict[str, set[str]], nodes: set[str]) -> list[str] | None:
    """DFS 三色标记找环，返回第一个环路径（含首尾重复节点）或 None。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes}
    path: list[str] = []

    def dfs(u: str) -> list[str] | None:
        color[u] = GRAY
        path.append(u)
        for v in sorted(graph.get(u, [])):
            if v not in color:
                continue  # 悬空引用在别处已报，此处跳过避免 KeyError
            if color[v] == GRAY:
                idx = path.index(v)
                return path[idx:] + [v]
            if color[v] == WHITE:
                res = dfs(v)
                if res:
                    return res
        path.pop()
        color[u] = BLACK
        return None

    for n in sorted(nodes):
        if color[n] == WHITE:
            res = dfs(n)
            if res:
                return res
    return None


def topo_sort(graph: dict[str, set[str]], nodes: set[str]) -> list[str]:
    """Kahn 拓扑排序，返回建议执行顺序（前置先于依赖者）。

    边方向约定：graph[tid] = tid 的前置任务集合。
    indeg[tid] = tid 尚未满足的前置数量；完成后对其下游（依赖 tid 的任务）减一。
    有环时环内节点不会出现在结果中。
    """
    indeg = {n: len(graph.get(n, set())) for n in nodes}
    rev: dict[str, set[str]] = defaultdict(set)  # 被依赖者 -> 依赖者
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


def main() -> int:
    parser = argparse.ArgumentParser(description="验证知识库重构计划任务依赖闭环")
    parser.add_argument(
        "--dir",
        default=str(Path(__file__).resolve().parent.parent / "docs" / "zh" / "知识库重构计划"),
        help="任务文件目录（默认: docs/zh/知识库重构计划）",
    )
    parser.add_argument("--no-warn", action="store_true", help="不输出孤立任务警告")
    args = parser.parse_args()

    task_dir = Path(args.dir)
    if not task_dir.is_dir():
        print(f"[错误] 任务目录不存在: {task_dir}", file=sys.stderr)
        return 1

    files = sorted(task_dir.glob("任务*.md"))
    if not files:
        print(f"[错误] 未在 {task_dir} 中找到任何 任务*.md 文件", file=sys.stderr)
        return 1

    # ---- 解析 ----
    task_id_of: dict[str, str] = {}     # 任务ID -> 文件名
    raw_deps: dict[str, list[str]] = {}  # 任务ID -> 依赖
    missing_id: list[str] = []
    duplicates: list[str] = []

    for f in files:
        tid, deps = parse_task_file(f)
        if tid is None:
            missing_id.append(f.name)
            continue
        if tid in task_id_of:
            duplicates.append(f"{tid}（{task_id_of[tid]} 与 {f.name}）")
            continue
        task_id_of[tid] = f.name
        raw_deps[tid] = deps

    # ---- 校验 ----
    errors: list[str] = []
    known = set(task_id_of)

    # 悬空依赖
    for tid, deps in sorted(raw_deps.items()):
        for d in deps:
            if d not in known:
                errors.append(f"[悬空依赖] {tid} 依赖 {d}，但 {d} 不存在（{task_id_of[tid]}）")

    # 构建依赖图（仅保留已知节点）
    graph: dict[str, set[str]] = {tid: set() for tid in known}
    for tid, deps in raw_deps.items():
        graph[tid] = {d for d in deps if d in known}

    # 循环依赖
    cycle = find_cycle(graph, known)
    if cycle:
        errors.append(f"[循环依赖] 存在闭环: {' -> '.join(cycle)}")

    # 孤立任务（无入边且无出边 = 完全孤立；无入边 = 无前置也无人依赖）
    if not args.no_warn:
        for tid in sorted(known):
            has_dep = bool(graph[tid])
            has_in = any(tid in vs for vs in graph.values())
            if not has_dep and not has_in:
                print(f"[警告] {tid} 无前置依赖且不被任何任务依赖（孤立任务），请确认是否符合预期")

    # ---- 输出 ----
    print(f"=== 知识库重构计划依赖验证 ===")
    print(f"任务目录: {task_dir}")
    print(f"解析任务数: {len(known)}，文件总数: {len(files)}")
    if missing_id:
        print(f"[警告] {len(missing_id)} 个文件缺少任务ID字段（已跳过）: {', '.join(missing_id)}")
    if duplicates:
        errors.append(f"[重复ID] {len(duplicates)} 个任务ID重复: {', '.join(duplicates)}")

    print()
    print("依赖关系:")
    for tid in sorted(known):
        deps = sorted(graph[tid])
        print(f"  {tid} <- {', '.join(deps) if deps else '无'}")

    order = topo_sort(graph, known)
    print()
    print("建议执行顺序（拓扑排序，环内任务缺失）:")
    for i, tid in enumerate(order, 1):
        print(f"  第{i}批: {tid}  ({task_id_of[tid]})")

    # ---- 汇总 ----
    print()
    if errors:
        print(f"验证失败: {len(errors)} 个问题")
        for e in errors:
            print(f"  {e}")
        return 1
    if missing_id:
        print(f"验证完成（{len(missing_id)} 个文件因缺少任务ID未纳入，请人工确认是否为误放文件）")
        return 0
    print("验证通过: 依赖关系闭环，无循环依赖、无悬空引用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
