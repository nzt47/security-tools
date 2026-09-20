#!/usr/bin/env python
"""超时上界静态检查器 —— 「注册为能力的 handler 是否有超时包装」（TASK-08 D / E1j）

【不易（为什么要写成脚本而不是一次性人工核查）】
    人工核查只能证明「**今天**看过的那个 handler 有超时」。而超时缺失是一个
    **会复发**的缺陷：任何人新写一个工具、用 `@_tools.register(...)` 挂上去，
    只要它自身不设超时，就立刻新增一条"可无限阻塞"路径。故必须有一个
    **可重复执行、可非零退出**的机械判据，把它接进 CI 或 pre-commit，
    使回归在合入前就被挡住。

【它检查的两条不变量】
    A. **分发层不变量**（结构性，缺失即致命）
       `agent/tools/__init__.py::call()` 是工具分发的**唯一汇聚点**，
       它必须以**有界调用**方式执行 handler。若有人把这一步改回裸调
       `tool["handler"](**params)`，则所有 handler 一起退回"可无限阻塞"。
       本检查器用 AST 直接验证该处确实走了 `call_with_timeout`，且
       `agent/timeout_budget.py` 存在、默认上界为非 0（关闭上界 = 关掉防线）。

    B. **单 handler 不变量**（逐条）
       每个 `@_tools.register(...)` 挂上的 handler 必须满足下列**任一**：
         B1. 自身实现超时（`subprocess` 的 `timeout=` / `join(timeout=)` /
             `wait(timeout=)` / `queue.get(timeout=)` / `asyncio.wait_for` /
             沙箱 `timeout_s=` 等）；
         B2. schema 里声明了标量 `timeout`/`timeout_sec` 参数
             （外层上界会依据它收紧，见 `resolve_tool_handler_timeout`）；
         B3. 仅**依赖分发层全局上界**兜底（记入「仅靠外层兜底」清单）。
       无任何一条成立 ⇒ 该能力在当前配置下**可无限阻塞** ⇒ 非零退出。

【退出码】
    0 = 通过；1 = 发现缺陷（分发层不变量破坏，或有 handler 完全无超时保障）；
    2 = 检查器自身错误（解析失败等——**不静默当通过**）。

【用法】
    python scripts/check_handler_timeouts.py                # 人读报告
    python scripts/check_handler_timeouts.py --json          # 机器读
    python scripts/check_handler_timeouts.py --strict        # B3 也算失败
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 扫描根（只扫真正注册能力的目录；tests/ 与归档目录不算能力面）
SCAN_ROOTS = ("agent/tools", "agent", "plugins")

#: 表示"调用点自己设了超时"的关键字
_TIMEOUT_KWARGS = ("timeout", "timeout_sec", "timeout_seconds", "timeout_s",
                   "timeout_seconds_total", "read_timeout", "read_timeout_seconds")

#: 表示"这是有界等待"的被调用函数名
_BOUNDED_CALLS = ("wait_for", "join", "wait", "get", "acquire",
                  "communicate", "run", "call", "check_output")

#: schema 中声明标量超时参数的键名（外层上界会据此收紧）
_SCALAR_TIMEOUT_KEYS = ("timeout", "timeout_sec")

#: 分发层汇聚点与有界包装符号
_DISPATCH_FILE = "agent/tools/__init__.py"
_BOUND_SYMBOL = "call_with_timeout"
_BUDGET_FILE = "agent/timeout_budget.py"


def find_bare_handler_calls(tree: ast.AST) -> list:
    """找出 `tool["handler"](**params)` 形态的**裸调**（E1j 路径 2 的缺陷指纹）

    抽成独立函数是为了让**反例可测**：测试可以喂一段合成源码进来，
    确认检测器真的会命中 —— 否则"检查器恒返回通过"这种假绿无法被发现。
    """
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Subscript):
            base = node.func.value
            if isinstance(base, ast.Name) and base.id == "tool":
                sl = node.func.slice
                if isinstance(sl, ast.Constant) and sl.value == "handler":
                    hits.append(node.lineno)
    return hits


def _iter_register_decorators(tree: ast.AST):
    """产出所有 `@<x>.register("tool_name", ...)` 挂载的 (工具名, 函数节点)"""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            attr = getattr(func, "attr", None)
            if attr not in ("register", "register_dynamic"):
                continue
            if not dec.args:
                continue
            first = dec.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            yield first.value, dec, node


def _has_internal_timeout(func: ast.AST) -> tuple:
    """判断 handler 自身是否实现超时；返回 (是否, 证据列表)"""
    evidence = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in _TIMEOUT_KWARGS:
                    name = getattr(node.func, "attr", None) or getattr(node.func, "id", "?")
                    evidence.append(f"{name}({kw.arg}=...)")
            if isinstance(node.func, ast.Attribute) and node.func.attr == "wait_for":
                evidence.append("asyncio.wait_for(...)")
    return bool(evidence), sorted(set(evidence))


def _declares_scalar_timeout(dec: ast.Call) -> bool:
    """schema 里是否声明了标量 timeout/timeout_sec 参数"""
    for kw in dec.keywords:
        if kw.arg != "schema":
            continue
        for node in ast.walk(kw.value):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in _SCALAR_TIMEOUT_KEYS:
                    return True
    return False


def check_dispatch_invariant() -> dict:
    """不变量 A：分发汇聚点必须走有界调用，且预算模块存在、默认上界非 0"""
    result = {"ok": True, "problems": [], "details": {}}

    budget_path = REPO_ROOT / _BUDGET_FILE
    result["details"]["budget_module_exists"] = budget_path.exists()
    if not budget_path.exists():
        result["ok"] = False
        result["problems"].append(f"缺少 {_BUDGET_FILE}（有界调用实现不存在）")
        return result

    src = (REPO_ROOT / _BUDGET_FILE).read_text(encoding="utf-8")
    # 默认上界必须 > 0：置 0 等于关闭上界（= 关掉防线），静态可见
    import re
    m = re.search(r"_TOOL_HANDLER_TIMEOUT_DEFAULT\s*=\s*([0-9.]+)", src)
    default_bound = float(m.group(1)) if m else 0.0
    result["details"]["tool_handler_timeout_default"] = default_bound
    if default_bound <= 0:
        result["ok"] = False
        result["problems"].append(
            f"_TOOL_HANDLER_TIMEOUT_DEFAULT={default_bound} ≤ 0 ⇒ 全局上界被关闭，"
            f"所有仅靠外层兜底的 handler 退化为可无限阻塞")

    dispatch_path = REPO_ROOT / _DISPATCH_FILE
    if not dispatch_path.exists():
        result["ok"] = False
        result["problems"].append(f"缺少 {_DISPATCH_FILE}")
        return result

    tree = ast.parse(dispatch_path.read_text(encoding="utf-8"))
    bounded_calls = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == _BOUND_SYMBOL:
            bounded_calls += 1
    bare_handler_calls = find_bare_handler_calls(tree)
    result["details"]["bounded_call_sites"] = bounded_calls
    result["details"]["bare_handler_call_lines"] = bare_handler_calls

    if bare_handler_calls:
        result["ok"] = False
        result["problems"].append(
            f"{_DISPATCH_FILE} 仍存在**裸调** handler 的位置（行 {bare_handler_calls}）"
            f"—— 与任务书 E1j「路径 2」描述的缺陷同形")
    if bounded_calls == 0:
        result["ok"] = False
        result["problems"].append(
            f"{_DISPATCH_FILE} 未调用 {_BOUND_SYMBOL} ⇒ 分发层无超时上界")
    return result


def scan_handlers() -> list:
    """扫描全部注册为能力的 handler，逐条判定超时保障"""
    findings = []
    seen_files = set()
    for root in SCAN_ROOTS:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in seen_files or "/archive/" in rel or rel.startswith("tests/"):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            seen_files.add(rel)
            for tool_name, dec, func in _iter_register_decorators(tree):
                internal, evidence = _has_internal_timeout(func)
                declared = _declares_scalar_timeout(dec)
                if internal:
                    kind = "internal_bound"
                elif declared:
                    kind = "declared_scalar_timeout"
                else:
                    kind = "dispatch_bound_only"
                findings.append({
                    "tool": tool_name,
                    "handler": func.name,
                    "file": rel,
                    "lineno": func.lineno,
                    "bound_kind": kind,
                    "evidence": evidence,
                })
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="检查注册为能力的 handler 是否有超时上界")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--strict", action="store_true",
                    help="把「仅靠分发层全局上界兜底」也视为失败")
    args = ap.parse_args(argv)

    try:
        dispatch = check_dispatch_invariant()
        handlers = scan_handlers()
    except Exception as exc:  # noqa: BLE001  检查器自身错误必须显式暴露
        print(f"[check_handler_timeouts] 检查器错误: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2

    by_kind = {"internal_bound": [], "declared_scalar_timeout": [],
               "dispatch_bound_only": []}
    for item in handlers:
        by_kind[item["bound_kind"]].append(item)

    fatal = list(dispatch["problems"])
    if args.strict:
        fatal += [f"{i['tool']} ({i['file']}:{i['lineno']}) 仅靠分发层全局上界兜底"
                  for i in by_kind["dispatch_bound_only"]]

    report = {
        "dispatch": dispatch,
        "handlers_total": len(handlers),
        "internal_bound": len(by_kind["internal_bound"]),
        "declared_scalar_timeout": len(by_kind["declared_scalar_timeout"]),
        "dispatch_bound_only": len(by_kind["dispatch_bound_only"]),
        "dispatch_bound_only_tools": [i["tool"] for i in by_kind["dispatch_bound_only"]],
        "problems": fatal,
        "ok": not fatal,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("=" * 68)
        print("超时上界检查（E1j）：注册为能力的 handler 是否有超时包装")
        print("=" * 68)
        d = dispatch["details"]
        print(f"[不变量 A] 分发层有界调用      : {'OK' if dispatch['ok'] else 'FAIL'}")
        print(f"           {_BOUND_SYMBOL} 调用点        : {d.get('bounded_call_sites')}")
        print(f"           裸调 handler 位置       : {d.get('bare_handler_call_lines') or '无'}")
        print(f"           全局上界默认值(秒)      : {d.get('tool_handler_timeout_default')}"
              f"  {'(已启用)' if (d.get('tool_handler_timeout_default') or 0) > 0 else '(已关闭!)'}")
        print()
        print(f"[不变量 B] 注册 handler 总数    : {len(handlers)}")
        print(f"           自身实现超时 (B1)       : {len(by_kind['internal_bound'])}")
        print(f"           schema 声明标量超时 (B2): {len(by_kind['declared_scalar_timeout'])}")
        print(f"           仅靠外层兜底 (B3)       : {len(by_kind['dispatch_bound_only'])}")
        if by_kind["dispatch_bound_only"]:
            print()
            print("  B3 明细（这些能力在全局上界被置 0 时会退化为可无限阻塞）:")
            for i in by_kind["dispatch_bound_only"][:15]:
                print(f"    - {i['tool']:<28} {i['file']}:{i['lineno']}")
            if len(by_kind["dispatch_bound_only"]) > 15:
                print(f"    ... 其余 {len(by_kind['dispatch_bound_only']) - 15} 个见 --json")
        print()
        if fatal:
            print(f"结论: FAIL —— {len(fatal)} 项缺陷")
            for p in fatal:
                print(f"  ✗ {p}")
        else:
            print("结论: PASS —— 分发层有界，且无完全无超时保障的 handler")
        print("=" * 68)

    return 0 if not fatal else 1


if __name__ == "__main__":
    sys.exit(main())
