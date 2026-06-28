#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
structured_log 格式验证脚本

用途：快速检查指定文件/目录中的 logger 调用是否已转换为 JSON 结构化格式。
使用方法：
    python scripts/verify_structured_log.py agent/p6_snapshot.py
    python scripts/verify_structured_log.py agent/orchestrator/
    python scripts/verify_structured_log.py agent/  # 递归扫描整个目录

验证规则：
    1. 所有 logger.info/warning/error 调用必须使用 json.dumps 格式
    2. JSON 内容必须包含 trace_id、module_name、action 三个必需字段
    3. 输出转换覆盖率和不合规文件清单
"""

import ast
import json
import re
import sys
import time
from pathlib import Path
from typing import List, Dict, Tuple


# 必需字段（structured_log 规范）
REQUIRED_FIELDS = {"trace_id", "module_name", "action"}

# logger 调用正则：匹配 logger.info( / logger.warning( / logger.error(
LOGGER_CALL_PATTERN = re.compile(r"logger\.(info|warning|error)\(")

# JSON 格式正则：匹配 logger.xxx(json.dumps(
JSON_LOGGER_PATTERN = re.compile(r"logger\.(info|warning|error)\(json\.dumps")


def _trace_id() -> str:
    """生成简短 trace_id 用于本脚本自身的日志输出"""
    import uuid
    return str(uuid.uuid4())[:8]


def scan_file(file_path: Path) -> Dict:
    """扫描单个 .py 文件，统计 logger 调用和 JSON 转换情况

    返回字典结构：
    {
        "file": str,           # 文件路径
        "total_calls": int,    # logger 调用总数
        "json_calls": int,     # 已转换为 json.dumps 的调用数
        "coverage": float,     # 转换覆盖率（百分比）
        "missing_lines": list, # 未转换的行号列表
        "missing_fields": list # JSON 调用中缺失必需字段的行号
    }
    """
    result = {
        "file": str(file_path),
        "total_calls": 0,
        "json_calls": 0,
        "coverage": 0.0,
        "missing_lines": [],
        "missing_fields": [],
    }

    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"[WARN] 无法读取文件 {file_path}: {e}")
        return result

    lines = source.splitlines()
    for line_num, line in enumerate(lines, start=1):
        # 跳过注释行
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        # 检查是否是 logger 调用
        if LOGGER_CALL_PATTERN.search(line):
            result["total_calls"] += 1

            # 检查是否是 json.dumps 格式
            if JSON_LOGGER_PATTERN.search(line):
                result["json_calls"] += 1

                # 检查必需字段
                line_lower = line.lower()
                missing = [
                    f for f in REQUIRED_FIELDS if f.lower() not in line_lower
                ]
                if missing:
                    result["missing_fields"].append(
                        {
                            "line": line_num,
                            "missing": missing,
                            "content": stripped[:120],
                        }
                    )
            else:
                # 未转换为 JSON 格式
                result["missing_lines"].append(
                    {"line": line_num, "content": stripped[:120]}
                )

    result["coverage"] = (
        round(result["json_calls"] / result["total_calls"] * 100, 1)
        if result["total_calls"] > 0
        else 0.0
    )

    return result


def scan_directory(dir_path: Path) -> List[Dict]:
    """递归扫描目录下所有 .py 文件"""
    results = []
    for py_file in sorted(dir_path.rglob("*.py")):
        # 跳过 __pycache__ 和 __init__.py
        if "__pycache__" in py_file.parts or py_file.name.startswith("__"):
            continue
        result = scan_file(py_file)
        if result["total_calls"] > 0:
            results.append(result)
    return results


def print_report(results: List[Dict], target: str) -> int:
    """打印验证报告，返回不合规文件数"""

    trace_id = _trace_id()
    t0 = time.time()

    print("=" * 80)
    print("structured_log 格式验证报告")
    print(f"扫描目标: {target}")
    print(f"扫描文件数: {len(results)}")
    print("=" * 80)

    total_calls = sum(r["total_calls"] for r in results)
    total_json = sum(r["json_calls"] for r in results)
    overall_coverage = (
        round(total_json / total_calls * 100, 1) if total_calls > 0 else 0.0
    )

    print(f"\n总 logger 调用数: {total_calls}")
    print(f"已转换 JSON 数:   {total_json}")
    print(f"整体覆盖率:       {overall_coverage}%")
    print()

    # 按覆盖率排序（覆盖率低的排前面）
    sorted_results = sorted(results, key=lambda x: x["coverage"])

    non_compliant = 0
    for r in sorted_results:
        status = "✅ PASS" if r["coverage"] == 100.0 else "❌ FAIL"
        if r["coverage"] < 100.0:
            non_compliant += 1

        print(
            f"  {status}  {r['coverage']:5.1f}%  "
            f"({r['json_calls']}/{r['total_calls']})  {r['file']}"
        )

        # 显示未转换的行
        if r["missing_lines"]:
            print(f"         未转换行（共 {len(r['missing_lines'])} 处）:")
            for item in r["missing_lines"][:5]:  # 只显示前 5 处
                print(f"           L{item['line']}: {item['content']}")
            if len(r["missing_lines"]) > 5:
                print(f"           ... 还有 {len(r['missing_lines']) - 5} 处")

        # 显示缺失字段的行
        if r["missing_fields"]:
            print(f"         缺失必需字段（共 {len(r['missing_fields'])} 处）:")
            for item in r["missing_fields"][:5]:
                print(
                    f"           L{item['line']}: 缺失 {item['missing']} "
                    f"| {item['content']}"
                )
            if len(r["missing_fields"]) > 5:
                print(f"           ... 还有 {len(r['missing_fields']) - 5} 处")

    print("\n" + "=" * 80)
    if non_compliant == 0:
        print("✅ 所有文件已 100% 转换为 JSON 结构化日志格式")
    else:
        print(f"❌ {non_compliant} 个文件未完全转换，请检查上述清单")

    elapsed_ms = round((time.time() - t0) * 1000, 2)
    print(
        json.dumps(
            {
                "trace_id": trace_id,
                "module_name": "verify_structured_log",
                "action": "scan.complete",
                "duration_ms": elapsed_ms,
                "target": target,
                "files_scanned": len(results),
                "total_calls": total_calls,
                "json_calls": total_json,
                "coverage_percent": overall_coverage,
                "non_compliant_files": non_compliant,
            },
            ensure_ascii=False,
        )
    )
    print("=" * 80)

    return non_compliant


def main():
    """主入口：解析命令行参数并执行扫描"""
    if len(sys.argv) < 2:
        print("用法: python scripts/verify_structured_log.py <文件或目录路径>")
        print("示例:")
        print("  python scripts/verify_structured_log.py agent/p6_snapshot.py")
        print("  python scripts/verify_structured_log.py agent/orchestrator/")
        print("  python scripts/verify_structured_log.py agent/")
        sys.exit(1)

    target_path = Path(sys.argv[1])
    if not target_path.exists():
        print(f"错误: 路径不存在 {target_path}")
        sys.exit(1)

    if target_path.is_file():
        # 单文件扫描
        result = scan_file(target_path)
        results = [result] if result["total_calls"] > 0 else []
    else:
        # 目录递归扫描
        results = scan_directory(target_path)

    if not results:
        print(f"未在 {target_path} 中找到任何 logger 调用")
        sys.exit(0)

    non_compliant = print_report(results, str(target_path))

    # 退出码：0=全部合规，1=有不合规文件
    sys.exit(1 if non_compliant > 0 else 0)


if __name__ == "__main__":
    main()
