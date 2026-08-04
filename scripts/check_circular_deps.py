"""循环依赖风险扫描脚本 — 用 AST 检测 agent/ 下的顶层双向导入。

用法:
    python scripts/check_circular_deps.py              # CI 守卫模式, 人类可读输出, 发现循环 exit 1
    python scripts/check_circular_deps.py --verbose    # JSON 输出到 stdout, 人类可读输出走 stderr
    python scripts/check_circular_deps.py -v           # --verbose 的短选项

输出分离策略 (不易):
    - 默认模式: 人类可读输出到 stdout (CI 日志直接可读)
    - --verbose 模式: JSON 到 stdout (供 jq/脚本解析), 人类可读到 stderr (终端可见)
    - 退出码语义不变: 0=无循环, 1=有循环
"""
import argparse
import ast
import json
import os
import sys
from collections import defaultdict


def extract(filepath, top_imports, func_imports, pep562_modules, import_locations):
    """解析单个 .py 文件, 收集顶层导入 / 函数内导入 / PEP 562 标记 / 导入位置.

    import_locations: dict[(src_mod, dst_mod)] -> [(filepath, lineno, statement)]
        专供 --verbose 模式构建 JSON 的循环依赖路径.
    """
    with open(filepath, encoding="utf-8") as f:
        source = f.read()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return
    rel = os.path.relpath(filepath).replace("\\", "/")
    # 模块路径归一化 (agent/orchestrator/lifecycle_manager.py -> agent.orchestrator.lifecycle_manager)
    mod_path = rel.replace(".py", "").replace("/", ".")

    # 顶层导入 (模块级, 加载时执行) — 同时记录位置供 --verbose 使用
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("agent"):
            top_imports[mod_path].add(node.module)
            import_locations[(mod_path, node.module)].append(
                (rel, node.lineno, f"from {node.module} import ...")
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("agent"):
                    top_imports[mod_path].add(alias.name)
                    import_locations[(mod_path, alias.name)].append(
                        (rel, node.lineno, f"import {alias.name}")
                    )

    # 函数内导入 (运行时, 安全)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.ImportFrom) and sub.module and sub.module.startswith("agent"):
                    func_imports[mod_path].add(sub.module)

    # PEP 562 检测
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "__getattr__":
            pep562_modules.add(mod_path)


def build_cycles_json(found_cycles, import_locations):
    """将循环依赖结果构建为可 JSON 序列化的结构.

    结构 (简易): cycles[] -> edges[] -> locations[], 路径完整可程序化解析.
    """
    cycles_json = []
    for a, b in found_cycles:
        edges = []
        # 两个方向的有向边, 每条边携带触发它的文件:行号:语句
        for src, dst in [(a, b), (b, a)]:
            locs = [
                {"file": fp, "line": ln, "statement": stmt}
                for fp, ln, stmt in import_locations.get((src, dst), [])
            ]
            if locs:
                edges.append({"from": src, "to": dst, "locations": locs})
        cycles_json.append({
            "modules": sorted([a, b]),
            "edges": edges,
        })
    return {
        "cycles": cycles_json,
        "summary": {
            "total_cycles": len(found_cycles),
            "total_edges": sum(len(c["edges"]) for c in cycles_json),
        },
    }


def main(verbose=False):
    top_imports = defaultdict(set)
    func_imports = defaultdict(set)
    pep562_modules = set()
    import_locations = defaultdict(list)

    for dirpath, _, files in os.walk("agent"):
        for fn in files:
            if fn.endswith(".py"):
                extract(
                    os.path.join(dirpath, fn),
                    top_imports, func_imports, pep562_modules, import_locations,
                )

    # 输出目标: verbose 模式下人类可读走 stderr, JSON 走 stdout (变易)
    # 默认参数 msg="" 支持 log() 无参调用 (打印空行), 与 print() 行为对齐
    log = (lambda msg="": print(msg, file=sys.stderr)) if verbose else print

    # 1. 双向依赖检测
    log("=" * 70)
    log("  双向依赖检测 (顶层导入, 加载时循环风险)")
    log("=" * 70)
    edges = set()
    for src, dsts in top_imports.items():
        for d in dsts:
            edges.add((src, d))
    found_cycles = []
    seen = set()
    for a, b in edges:
        if (b, a) in edges and a != b:
            pair = tuple(sorted([a, b]))
            if pair not in seen:
                seen.add(pair)
                found_cycles.append((a, b))
                log(f"  [风险] {a}  <-->  {b}")
                if verbose:
                    for fp, ln, stmt in import_locations.get((a, b), []):
                        log(f"      {fp}:{ln}  {stmt}   ({a} -> {b})")
                    for fp, ln, stmt in import_locations.get((b, a), []):
                        log(f"      {fp}:{ln}  {stmt}   ({b} -> {a})")
    if not found_cycles:
        log("  [OK] 未发现顶层双向依赖")
    else:
        log(f"\n  [FAIL] 发现 {len(found_cycles)} 组顶层双向依赖, 见上方 [风险] 行")
    log()

    # 2. PEP 562 懒加载模块
    log("=" * 70)
    log("  使用 PEP 562 懒加载的模块 (已防御循环)")
    log("=" * 70)
    for m in sorted(pep562_modules):
        log(f"  [OK] {m}")
    log()

    # 3. 未使用 PEP 562 但有顶层重导入的 __init__.py
    log("=" * 70)
    log("  __init__.py 顶层导入重模块 (潜在风险点)")
    log("=" * 70)
    for mod_path, dsts in sorted(top_imports.items()):
        if mod_path.endswith(".__init__") and dsts:
            base = mod_path.replace(".__init__", "")
            self_pkg_imports = [d for d in dsts if d.startswith(base + ".")]
            if self_pkg_imports and mod_path not in pep562_modules:
                log(f"  [注意] {mod_path}: {self_pkg_imports}")
    log()

    # 4. 顶层导入 orchestrator/digital_life 的文件
    log("=" * 70)
    log("  顶层导入 agent.orchestrator / agent.digital_life 的文件")
    log("=" * 70)
    for mod_path, dsts in sorted(top_imports.items()):
        risky = [d for d in dsts if "orchestrator" in d or "digital_life" in d]
        if risky:
            tag = "[运行时安全]" if mod_path in func_imports else "[加载时]"
            log(f"  {tag} {mod_path} -> {risky}")
    log()

    # 5. 函数内导入最多的文件
    log("=" * 70)
    log("  函数内导入最多的文件 (潜在循环缓解迹象, Top 10)")
    log("=" * 70)
    sorted_func = sorted(func_imports.items(), key=lambda x: len(x[1]), reverse=True)[:10]
    for mod_path, dsts in sorted_func:
        log(f"  {mod_path}: {len(dsts)} 个 agent 导入在函数内")
        for d in sorted(dsts)[:5]:
            log(f"      -> {d}")
        if len(dsts) > 5:
            log(f"      ... 还有 {len(dsts) - 5} 个")

    # verbose 模式: JSON 输出到 stdout (供 jq/脚本解析循环依赖路径)
    if verbose:
        result = build_cycles_json(found_cycles, import_locations)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    return found_cycles


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="循环依赖风险扫描 (CI 守卫: 发现顶层双向依赖时 exit 1)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="JSON 格式输出到 stdout (人类可读输出走 stderr), 供脚本解析循环依赖路径",
    )
    args = parser.parse_args()

    # CI 守卫: 发现顶层双向依赖时以非零退出码阻断流水线.
    # 退出码语义不变 (不易): 0=无循环, 1=有循环.
    cycles = main(verbose=args.verbose)
    sys.exit(1 if cycles else 0)
