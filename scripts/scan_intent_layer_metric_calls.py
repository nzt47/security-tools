#!/usr/bin/env python3
"""扫描 orchestrator.py 中所有 _record_intent_layer 调用，检测重复计数风险

检测逻辑（AST 静态分析）：
1. 解析 orchestrator.py 为 AST，定位所有 _record_intent_layer("xxx") 调用
2. 记录每个调用点的行号、layer 参数、所在函数
3. 对每个函数内的多个调用点，分析控制流：
   - 同执行路径（无 return/raise/break 隔开）→ 高风险（双重计数）
   - 互斥路径（有 return 隔开）→ 低风险（正常多分支）
4. 输出风险报告 + ratio 总和影响分析

用法:
    python scripts/scan_intent_layer_metric_calls.py
    python scripts/scan_intent_layer_metric_calls.py --file path/to/orchestrator.py
    python scripts/scan_intent_layer_metric_calls.py --json  # 机器可读输出

【不易】只读分析，不修改源码；守 INV-1（每分支有且仅有一次埋点）
【简易】自包含 AST 分析，无第三方依赖
"""
import argparse
import ast
import json
import os
import sys


# 已知的合法 layer 值（与 prometheus.py 标签对齐）
KNOWN_LAYERS = {"rule", "template", "semantic", "llm", "reject", "llm_low_confidence_fallback"}

# 标准四层（用于判断 fallback 等子指标是否纳入总占比）
STANDARD_FOUR_LAYERS = {"rule", "template", "semantic", "llm", "reject"}


class _CallInfo:
    """单个 _record_intent_layer 调用信息"""

    def __init__(self, line, layer, func_name, func_line):
        self.line = line              # 调用行号
        self.layer = layer            # layer 参数值（字符串字面量）
        self.func_name = func_name   # 所在函数名
        self.func_line = func_line   # 函数定义行号

    def to_dict(self):
        return {
            "line": self.line,
            "layer": self.layer,
            "function": self.func_name,
            "func_line": self.func_line,
        }


def _extract_layer_arg(node):
    """从 Call 节点提取 layer 参数（仅支持字符串字面量）

    Returns:
        layer 字符串；非字面量或无法解析返回 None
    """
    if not isinstance(node, ast.Call):
        return None
    # 函数名:_record_intent_layer
    func = node.func
    name = None
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    if name != "_record_intent_layer":
        return None
    # 第一个位置参数（字符串字面量）
    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    if isinstance(first, ast.Str):  # Python <3.8 兼容
        return first.s
    return None  # 动态参数，无法静态分析


def _find_enclosing_func(tree, target_line):
    """查找某行所在的函数定义（返回函数名和定义行号）"""
    result = [None, None]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 函数体行范围
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            if start <= target_line <= end:
                result = [node.name, start]
    return result[0], result[1]


def _has_return_between(lines, start_line, end_line):
    """检查两个行号之间是否有 return/raise/break 语句（简单行扫描）

    用于判断两个调用是否在同一执行路径上。
    Returns:
        True 表示有隔开语句（互斥路径），False 表示同路径（双重计数风险）
    """
    for i in range(start_line, end_line):
        if i >= len(lines):
            break
        line = lines[i].strip()
        # 跳过空行和注释
        if not line or line.startswith("#"):
            continue
        # 检测隔开语句（注意排除嵌套在 if/try 中的 return，简化处理）
        if line.startswith("return ") or line == "return" \
                or line.startswith("raise ") or line == "raise":
            return True
    return False


def scan_file(file_path):
    """扫描单个文件，返回调用点列表和风险分析

    Returns:
        {
            "calls": [_CallInfo...],
            "risks": [{type, severity, description, lines}...],
            "layers": set of layer values,
        }
    """
    with open(file_path, encoding="utf-8") as f:
        source = f.read()
    lines = source.splitlines()
    tree = ast.parse(source, filename=file_path)

    calls = []
    for node in ast.walk(tree):
        layer = _extract_layer_arg(node)
        if layer is not None:
            func_name, func_line = _find_enclosing_func(tree, node.lineno)
            calls.append(_CallInfo(node.lineno, layer, func_name or "<module>", func_line or 0))

    # 按行号排序
    calls.sort(key=lambda c: c.line)

    # 风险分析：同一函数内的多个调用点
    risks = []
    from collections import defaultdict
    func_calls = defaultdict(list)
    for c in calls:
        func_calls[c.func_name].append(c)

    for func_name, func_call_list in func_calls.items():
        if len(func_call_list) < 2:
            continue
        # 分析相邻调用对
        for i in range(len(func_call_list) - 1):
            c1 = func_call_list[i]
            for c2 in func_call_list[i + 1:]:
                # 同一函数内的两个调用
                has_separator = _has_return_between(lines, c1.line, c2.line)
                if not has_separator:
                    # 同执行路径：双重计数风险
                    risks.append({
                        "type": "dual_counting",
                        "severity": "HIGH",
                        "description": "同执行路径双重计数：%s(L%d) → %s(L%d)，中间无 return 隔开"
                                       % (c1.layer, c1.line, c2.layer, c2.line),
                        "call1": c1.to_dict(),
                        "call2": c2.to_dict(),
                        "function": func_name,
                    })
                else:
                    # 互斥路径：正常多分支
                    risks.append({
                        "type": "mutually_exclusive",
                        "severity": "LOW",
                        "description": "互斥路径（有 return 隔开）：%s(L%d) ⊥ %s(L%d)"
                                       % (c1.layer, c1.line, c2.layer, c2.line),
                        "call1": c1.to_dict(),
                        "call2": c2.to_dict(),
                        "function": func_name,
                    })

    layers = set(c.layer for c in calls)
    return {"calls": calls, "risks": risks, "layers": layers}


def _analyze_ratio_impact(layers, calls):
    """分析 ratio 总和影响"""
    standard_layers = layers & STANDARD_FOUR_LAYERS
    extra_layers = layers - STANDARD_FOUR_LAYERS
    return {
        "total_layers": len(layers),
        "standard_layers": sorted(standard_layers),
        "extra_layers": sorted(extra_layers),
        "ratio_sum_always_1": True,  # ratio = count/total，总和恒 = 1.0
        "counter_sum_exceeds_requests": bool(extra_layers),
        "note": "ratio 总和恒 = 1.0（count/total 求和）；"
                "但 Counter 总和 > 实际请求数（若存在子指标层如 llm_low_confidence_fallback）",
    }


def print_report(result, file_path):
    """打印人类可读报告"""
    calls = result["calls"]
    risks = result["risks"]
    layers = result["layers"]

    print("=" * 72)
    print("_record_intent_layer 调用扫描报告")
    print("=" * 72)
    print("文件: %s" % file_path)
    print("调用点总数: %d" % len(calls))
    print("Layer 值集合: %s" % sorted(layers))
    print()

    # 调用点列表
    print("-" * 72)
    print("[1] 调用点列表")
    print("-" * 72)
    print("%-8s %-45s %-20s" % ("行号", "调用", "所在函数"))
    for c in calls:
        print("L%-7d _record_intent_layer(\"%s\") %s" % (c.line, c.layer, c.func_name))
    print()

    # 风险分析
    print("-" * 72)
    print("[2] 重复计数风险分析")
    print("-" * 72)
    high_risks = [r for r in risks if r["severity"] == "HIGH"]
    low_risks = [r for r in risks if r["severity"] == "LOW"]

    if not risks:
        print("  无风险：每个函数内仅一个调用点")
    else:
        if high_risks:
            print("  [高风险] 同执行路径双重计数（%d 项）:" % len(high_risks))
            for r in high_risks:
                print("    ⚠ %s" % r["description"])
                print("      函数: %s" % r["function"])
                print("      影响: 一次请求同时计入两个 Counter → Counter 总和虚高")
            print()
        if low_risks:
            print("  [低风险] 互斥路径（%d 项，正常多分支）:" % len(low_risks))
            for r in low_risks:
                print("    ✓ %s" % r["description"])
    print()

    # 未知 layer 值
    unknown = layers - KNOWN_LAYERS
    if unknown:
        print("-" * 72)
        print("[3] 未知 Layer 值（可能拼写错误）")
        print("-" * 72)
        for layer in unknown:
            print("  ⚠ \"%s\" 不在已知 layer 集合 %s" % (layer, sorted(KNOWN_LAYERS)))
        print()

    # ratio 影响
    print("-" * 72)
    print("[4] ratio 总和影响分析")
    print("-" * 72)
    impact = _analyze_ratio_impact(layers, calls)
    print("  标准 4+1 层: %s" % impact["standard_layers"])
    if impact["extra_layers"]:
        print("  额外子指标层: %s" % impact["extra_layers"])
        print("  ⚠ Counter 总和 > 实际请求数（子指标层导致双重计数）")
        print("  ✓ ratio 总和始终 = 1.0（不超 100%，分母同步增大）")
        print("  建议: dashboard 用 PromQL 排除子指标层:")
        print('    yunshu_intent_layer_ratio{layer=~"rule|template|semantic|llm|reject"}')
    else:
        print("  ✓ 无子指标层，Counter 总和 = 实际请求数")
        print("  ✓ ratio 总和 = 1.0")
    print()

    # 总结
    print("=" * 72)
    if high_risks:
        print("[FAIL] 发现 %d 项高风险双重计数，建议修复" % len(high_risks))
        return 1
    elif unknown:
        print("[WARN] 发现未知 layer 值，建议检查拼写" % ())
        return 1
    else:
        print("[PASS] 无重复计数风险，埋点调用点正常")
        return 0


def main():
    parser = argparse.ArgumentParser(
        description="扫描 orchestrator.py 中 _record_intent_layer 调用，检测重复计数风险"
    )
    parser.add_argument("--file", default="agent/orchestrator/orchestrator.py",
                        help="待扫描的文件路径（默认 agent/orchestrator/orchestrator.py）")
    parser.add_argument("--json", action="store_true",
                        help="输出 JSON 格式（机器可读）")
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print("错误: 文件不存在: %s" % args.file)
        return 2

    result = scan_file(args.file)

    if args.json:
        output = {
            "file": args.file,
            "calls": [c.to_dict() for c in result["calls"]],
            "risks": result["risks"],
            "layers": sorted(result["layers"]),
            "ratio_impact": _analyze_ratio_impact(result["layers"], result["calls"]),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if not any(r["severity"] == "HIGH" for r in result["risks"]) else 1

    return print_report(result, args.file)


if __name__ == "__main__":
    raise SystemExit(main())
