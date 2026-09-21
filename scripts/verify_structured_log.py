#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
structured_log 格式验证脚本

用途：快速检查指定文件/目录中的 logger 调用是否已转换为结构化格式。
使用方法：
    python scripts/verify_structured_log.py agent/p6_snapshot.py
    python scripts/verify_structured_log.py agent/orchestrator/
    python scripts/verify_structured_log.py agent/  # 递归扫描整个目录

验证规则：
    1. 所有 logger.info/warning/error 调用必须使用**结构化包装**，本仓库并存两种：
         · log_dict({...})    —— 现行主流写法（agent/logging_utils.py::log_dict）
         · json.dumps({...})  —— 早期写法，由调用方自行序列化
    2. 结构化内容必须包含 trace_id、module_name、action 三个必需字段
       （两种写法的"必需"口径不同，见 REQUIRED_FIELDS_BY_FORM 的说明）
    3. 输出转换覆盖率和不合规文件清单

## 判据口径（缺陷 L36 ① 修复记录）
旧版只把 json.dumps 认作结构化日志，于是对本仓库**主流**写法
logger.info(log_dict({...})) 一律记成"未转换"，导致大量模块被报
「结构化日志覆盖率 0%」—— 这是**假阴性**（门禁说"没有结构化日志"，
代码里明明有）。判据过窄会误导人以为那些模块没有结构化日志。
本版判据同时识别两种写法，且以 AST 解析**调用实参**判定，
多行写法（logger.info( 换行后才是 log_dict( ）同样能识别 —— 逐物理行
做正则匹配会把这类调用再次漏判成"未转换"。

## 控制台编码（缺陷 L36 ② 修复记录）
中文 Windows 的 GBK 控制台无法编码 ✅/❌ 等字符，旧版会在打印报告时抛
UnicodeEncodeError ⇒ 进程以退出码 1 结束 ⇒ 门禁"假红"（检查结果与退出码
不一致）。修法与同型先例一致：scripts/audit_call_paths.py、
scripts/verify_llm_off_entrypoints.py。
"""

import ast
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional


# ── 控制台编码加固（L36 ② · 同型"假红"修复）─────────────────────────────────
# Why: 中文 Windows 的 GBK 控制台无法编码本脚本**成功路径**上的 ✓/✅ 等字符，
#      print() 抛 UnicodeEncodeError ⇒ 进程以退出码 1 结束 ⇒ 门禁产生"假红"
#      （检查本身通过，但按退出码判定会误判为失败，进而可能让真实失败被忽略）。
# How: 只放宽错误处理策略（errors="replace"），**不改编码**，保证中文仍正常显示；
#      在 UTF-8 环境（CI / Linux）下等价于无操作。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass


# 必需字段（structured_log 规范）
REQUIRED_FIELDS = ("trace_id", "module_name", "action")

#: json.dumps 形式：三个必需字段都要在调用处写出来（调用方手工拼 JSON，
#: 缺哪个就是真的缺）。
REQUIRED_FIELDS_JSON = frozenset(REQUIRED_FIELDS)

#: log_dict 形式：trace_id 由 log_dict 逐条注入
#: （logging_utils.log_dict: if "trace_id" not in data: data["trace_id"] = _trace_id()），
#: 因此要求调用处写出 trace_id 是**无意义**的，只会制造新的假红；
#: module_name / action 缺省时会被填成 'unknown'，属于真实的信息缺失，仍然要报。
REQUIRED_FIELDS_LOG_DICT = frozenset({"module_name", "action"})

#: 两种结构化写法的必需字段口径
REQUIRED_FIELDS_BY_FORM = {
    "log_dict": REQUIRED_FIELDS_LOG_DICT,
    "json.dumps": REQUIRED_FIELDS_JSON,
}

# logger 调用正则：匹配 logger.info( / logger.warning( / logger.error(
LOGGER_METHODS = ("info", "warning", "error")
LOGGER_CALL_PATTERN = re.compile(r"logger\.(info|warning|error)\(")

# 结构化日志正则：两种写法都认（L36 ① 修复点）
#   logger.info(log_dict(   /   logger.info(json.dumps(   /   logger.info( log_dict(
JSON_LOGGER_PATTERN = re.compile(
    r"logger\.(info|warning|error)\(\s*(json\.dumps|log_dict)\s*\("
)


def _trace_id() -> str:
    """生成简短 trace_id 用于本脚本自身的日志输出"""
    import uuid
    return str(uuid.uuid4())[:8]


def _new_result(file_path: Path) -> Dict:
    """单个文件的统计骨架"""
    return {
        "file": str(file_path),
        "total_calls": 0,
        # 已结构化的调用数：json.dumps + log_dict 两种写法之和。
        # 键名 json_calls 为向后兼容保留（等价于 structured_calls）。
        "json_calls": 0,
        "log_dict_calls": 0,
        "json_dumps_calls": 0,
        "coverage": 0.0,
        "missing_lines": [],
        "missing_fields": [],
        # 该文件是否走 AST 解析（False = 语法无法解析，降级为正则扫描）
        "ast_parsed": True,
    }


def _finalize(result: Dict) -> Dict:
    """结算覆盖率"""
    result["coverage"] = (
        round(result["json_calls"] / result["total_calls"] * 100, 1)
        if result["total_calls"] > 0
        else 0.0
    )
    result["missing_lines"].sort(key=lambda x: x["line"])
    result["missing_fields"].sort(key=lambda x: x["line"])
    return result


# ─────────────────────────────────────────────────────────────────────────────
# AST 判据（主路径）
# ─────────────────────────────────────────────────────────────────────────────

def _call_name(func: ast.AST) -> str:
    """取调用表达式的限定名：Name('log_dict') -> 'log_dict'；Attribute -> 'json.dumps'"""
    parts: List[str] = []
    node = func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _is_logger_method(func: ast.AST) -> bool:
    r"""logger.info / self.logger.warning / self._logger.error 一类的 logger 方法调用

    名字口径按"以 logger 结尾"放宽（logger / _logger / self.logger / self._logger）：
    旧版正则 logger\.(info|warning|error)\( 是**子串**匹配，本来就覆盖
    self._logger.error(...) 这类真实调用；若 AST 判据收紧到 Name('logger')，
    这些调用会从"总调用数"里消失 —— 那是拿一次"修假阴性"换来一次漏报（假绿），
    所以口径必须与旧版等价。
    """
    if not isinstance(func, ast.Attribute) or func.attr not in LOGGER_METHODS:
        return False
    value = func.value
    if isinstance(value, ast.Name):
        return value.id.endswith("logger")
    return isinstance(value, ast.Attribute) and value.attr.endswith("logger")


def _structured_form(call: ast.Call) -> Optional[str]:
    """logger 调用的实参是否已被结构化包装；返回 'log_dict' / 'json.dumps' / None"""
    arg: Optional[ast.AST] = call.args[0] if call.args else None
    if arg is None:
        for kw in call.keywords:
            if kw.arg in ("msg", "message"):  # logger.info(msg=log_dict({...}))
                arg = kw.value
                break
    if not isinstance(arg, ast.Call):
        return None
    name = _call_name(arg.func)
    if name == "log_dict" or name.endswith(".log_dict"):
        return "log_dict"
    if name == "json.dumps":
        return "json.dumps"
    return None


def _scan_with_ast(source: str, result: Dict) -> bool:
    """AST 扫描；返回 False 表示本文件无法解析，调用方应降级为正则扫描"""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False

    lines = source.splitlines()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_logger_method(node.func)):
            continue

        result["total_calls"] += 1
        form = _structured_form(node)
        segment = ast.get_source_segment(source, node) or ""
        # 折叠空白：多行调用也能在一行里看清内容
        content = " ".join(segment.split())[:120] or (
            lines[node.lineno - 1].strip()[:120] if 0 < node.lineno <= len(lines) else ""
        )

        if form:
            result["json_calls"] += 1
            if form == "log_dict":
                result["log_dict_calls"] += 1
            else:
                result["json_dumps_calls"] += 1

            required = REQUIRED_FIELDS_BY_FORM[form]
            segment_lower = segment.lower()
            missing = sorted(f for f in required if f.lower() not in segment_lower)
            if missing:
                result["missing_fields"].append(
                    {"line": node.lineno, "missing": missing, "content": content}
                )
        else:
            result["missing_lines"].append({"line": node.lineno, "content": content})

    return True


# ─────────────────────────────────────────────────────────────────────────────
# 正则判据（降级路径：文件语法无法解析时）
# ─────────────────────────────────────────────────────────────────────────────

def _scan_with_regex(source: str, result: Dict) -> bool:
    """逐行正则扫描（旧口径）。仅在文件无法被 AST 解析时使用。

    局限：跨行的 logger 调用会被漏判 —— 这也是本脚本改用 AST 做主线的原因。
    """
    result["ast_parsed"] = False
    for line_num, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        # 跳过注释行
        if stripped.startswith("#"):
            continue

        if not LOGGER_CALL_PATTERN.search(line):
            continue

        result["total_calls"] += 1
        match = JSON_LOGGER_PATTERN.search(line)
        if match:
            form = match.group(2)
            result["json_calls"] += 1
            if form == "log_dict":
                result["log_dict_calls"] += 1
            else:
                result["json_dumps_calls"] += 1

            required = REQUIRED_FIELDS_BY_FORM[form]
            line_lower = line.lower()
            missing = sorted(f for f in required if f.lower() not in line_lower)
            if missing:
                result["missing_fields"].append(
                    {"line": line_num, "missing": missing, "content": stripped[:120]}
                )
        else:
            result["missing_lines"].append({"line": line_num, "content": stripped[:120]})

    return True


def scan_file(file_path: Path) -> Dict:
    """扫描单个 .py 文件，统计 logger 调用与结构化转换情况

    返回字典结构：
    {
        "file": str,             # 文件路径
        "total_calls": int,      # logger 调用总数
        "json_calls": int,       # 已结构化（json.dumps + log_dict）的调用数
        "log_dict_calls": int,   # 其中 log_dict 写法
        "json_dumps_calls": int, # 其中 json.dumps 写法
        "coverage": float,       # 转换覆盖率（百分比）
        "missing_lines": list,   # 未转换的行号列表
        "missing_fields": list,  # 结构化调用中缺失必需字段的行号
        "ast_parsed": bool       # 是否走 AST 判据（False = 降级为正则）
    }
    """
    result = _new_result(file_path)

    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"[WARN] 无法读取文件 {file_path}: {e}")
        return result

    if not _scan_with_ast(source, result):
        _scan_with_regex(source, result)

    return _finalize(result)


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
    total_log_dict = sum(r["log_dict_calls"] for r in results)
    total_json_dumps = sum(r["json_dumps_calls"] for r in results)
    overall_coverage = (
        round(total_json / total_calls * 100, 1) if total_calls > 0 else 0.0
    )

    print(f"\n总 logger 调用数: {total_calls}")
    print(f"已结构化数:       {total_json}")
    print(f"  · log_dict:     {total_log_dict}")
    print(f"  · json.dumps:   {total_json_dumps}")
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

    degraded = [r for r in results if not r["ast_parsed"]]
    if degraded:
        print(
            f"\n[NOTE] {len(degraded)} 个文件语法无法解析，已降级为正则扫描"
            f"（跨行调用可能漏判）"
        )

    print("\n" + "=" * 80)
    if non_compliant == 0:
        print("✅ 所有文件已 100% 转换为结构化日志格式（log_dict / json.dumps）")
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
                "structured_calls": total_json,
                "log_dict_calls": total_log_dict,
                "json_dumps_calls": total_json_dumps,
                "coverage_percent": overall_coverage,
                "non_compliant_files": non_compliant,
                "degraded_files": len(degraded),
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
