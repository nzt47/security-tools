#!/usr/bin/env python3
"""日志双重序列化反模式守护脚本

扫描代码库中 `logger.X(json.dumps(...))` / `_logger.X(json.dumps(...))` 等
调用方主动序列化的反模式，强制使用 log_dict 替代。

CI 集成（增量扫描，仅检查 PR 中新增/修改的文件）：
    python scripts/check_double_serialization.py \
        --base origin/main \
        --head HEAD \
        --exemption-file .trae/double_serialization_exemptions.json

CI 集成（全量扫描，用于基线审计）：
    python scripts/check_double_serialization.py --full-scan

退出码：
    0 = 无新增违规（或全部在豁免清单中）
    1 = 发现新增违规
    2 = 运行异常

机制说明：
- 边界显性化：违规文件列表按优先级排序输出，含文件路径、行号、代码片段
- 幂等性：豁免清单可通过 --update-exemptions 自动更新
- 竞态防御：git diff 用 --no-pager 避免分页器卡死
"""
import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Set, Optional


# 业务错误码
ERR_NEW_VIOLATION = "DOUBLE_SERIALIZATION_NEW_VIOLATION"
ERR_RUNTIME = "DOUBLE_SERIALIZATION_RUNTIME"

# 默认豁免目录（测试、迁移工具本身、文档、虚拟环境）
DEFAULT_EXCLUDE_DIRS = {
    "tests", "test", "__pycache__", ".git", ".trae", "docs",
    "venv", ".venv", "node_modules", "build", "dist",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
}

# 默认豁免文件（已优化的核心模块）
DEFAULT_EXEMPTION_FILES = {
    "agent/logging_utils.py",
    "agent/utils/perf_monitor.py",
    "scripts/migrate_to_log_dict.py",
    "scripts/check_double_serialization.py",
}

# 反模式正则：匹配 logger.X(json.dumps(...))
# 支持 logger / _logger / self.logger / cls.logger 等前缀
# 支持 info / debug / warning / error / critical / exception 等方法
LOGGER_JSON_DUMPS_PATTERN = re.compile(
    r"""
    (?P<logger>
        (?:self\.)?(?:_)?logger    # logger / _logger / self.logger / self._logger
        | (?:self\.)?(?:_)?log     # log / _log / self.log
        | cls\.logger
    )
    \s*\.\s*
    (?P<method>
        info|debug|warning|warn|error|critical|exception|log
    )
    \s*\(\s*
    json\.dumps\s*\(
    """,
    re.VERBOSE | re.IGNORECASE,
)


@dataclass
class Violation:
    """违规项"""
    file: str
    line: int
    column: int
    line_content: str
    matched_text: str

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "matched_text": self.matched_text,
            "line_content": self.line_content.strip(),
        }


@dataclass
class ScanResult:
    """扫描结果"""
    scanned_files: int = 0
    violations: List[Violation] = field(default_factory=list)
    exempted_violations: int = 0
    error_files: List[str] = field(default_factory=list)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="日志双重序列化反模式守护脚本"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--full-scan", action="store_true",
                      help="全量扫描（基线审计用）")
    mode.add_argument("--diff-scan", action="store_true",
                      help="增量扫描（仅检查 git diff 中变更的文件）")

    parser.add_argument("--base", type=str, default="origin/main",
                        help="增量扫描的基准分支（默认 origin/main）")
    parser.add_argument("--head", type=str, default="HEAD",
                        help="增量扫描的目标 HEAD（默认 HEAD）")
    parser.add_argument("--root", type=str, default=".",
                        help="项目根目录（默认当前目录）")
    parser.add_argument("--exemption-file", type=str,
                        default=".trae/double_serialization_exemptions.json",
                        help="豁免清单 JSON 文件路径")
    parser.add_argument("--update-exemptions", action="store_true",
                        help="更新豁免清单（全量扫描结果写入豁免文件）")
    parser.add_argument("--max-violations", type=int, default=0,
                        help="允许的最大新增违规数（默认 0，任何新增即阻断）")
    return parser.parse_args()


def _is_excluded(file_path: str) -> bool:
    """是否在默认排除目录中"""
    parts = Path(file_path).parts
    for part in parts:
        if part in DEFAULT_EXCLUDE_DIRS:
            return True
    # 排除非 .py 文件
    if not file_path.endswith(".py"):
        return True
    return False


def _is_exempted(file_path: str) -> bool:
    """是否在豁免文件清单中"""
    normalized = file_path.replace("\\", "/")
    for exempt in DEFAULT_EXEMPTION_FILES:
        if normalized.endswith(exempt):
            return True
    return False


def _scan_file(file_path: str) -> List[Violation]:
    """扫描单个文件的违规项"""
    violations = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except (IOError, OSError) as e:
        print(f"[WARN] 无法读取 {file_path}: {e}", file=sys.stderr)
        return violations

    for match in LOGGER_JSON_DUMPS_PATTERN.finditer(content):
        line_start = content.rfind("\n", 0, match.start()) + 1
        line_no = content.count("\n", 0, match.start()) + 1
        col = match.start() - line_start
        line_end = content.find("\n", match.end())
        if line_end == -1:
            line_end = len(content)
        line_content = content[line_start:line_end]

        violations.append(Violation(
            file=file_path,
            line=line_no,
            column=col,
            line_content=line_content,
            matched_text=match.group(0),
        ))
    return violations


def _get_changed_files(base: str, head: str, root: str) -> List[str]:
    """通过 git diff 获取变更文件列表"""
    try:
        result = subprocess.run(
            ["git", "diff", "--no-pager", "--name-only",
             "--diff-filter=AM", base, head],
            cwd=root, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            # 退化：尝试使用 merge-base
            result = subprocess.run(
                ["git", "diff", "--no-pager", "--name-only",
                 "--diff-filter=AM",
                 f"origin/{base}..." if not base.startswith("origin/")
                 else f"{base}...", head],
                cwd=root, capture_output=True, text=True, timeout=30,
            )
        return [f for f in result.stdout.strip().split("\n") if f]
    except subprocess.TimeoutExpired:
        print(f"[{ERR_RUNTIME}] git diff 超时", file=sys.stderr)
        sys.exit(2)
    except FileNotFoundError:
        print(f"[{ERR_RUNTIME}] git 命令未找到", file=sys.stderr)
        sys.exit(2)


def _load_exemptions(exemption_file: str) -> Set[str]:
    """加载豁免清单（已知的存量违规文件路径集合）"""
    if not os.path.exists(exemption_file):
        return set()
    try:
        with open(exemption_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("exempted_files", []))
    except (IOError, json.JSONDecodeError) as e:
        print(f"[WARN] 读取豁免清单失败: {e}", file=sys.stderr)
        return set()


def _update_exemptions(exemption_file: str, scan_result: ScanResult,
                       root: str) -> None:
    """更新豁免清单（全量扫描结果写入）"""
    exempted_files = sorted({v.file for v in scan_result.violations})
    data = {
        "version": "1.0",
        "description": "存量双重序列化反模式豁免清单（基线审计）",
        "exempted_files": exempted_files,
        "total_violations": len(scan_result.violations),
    }
    os.makedirs(os.path.dirname(exemption_file) or ".", exist_ok=True)
    with open(exemption_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"豁免清单已更新: {exemption_file} "
          f"({len(exempted_files)} 个文件, "
          f"{len(scan_result.violations)} 处违规)")


def _full_scan(root: str) -> ScanResult:
    """全量扫描"""
    result = ScanResult()
    root_path = Path(root)

    for py_file in root_path.rglob("*.py"):
        file_str = str(py_file.relative_to(root_path)).replace("\\", "/")
        if _is_excluded(file_str):
            continue
        if _is_exempted(file_str):
            continue

        result.scanned_files += 1
        violations = _scan_file(str(py_file))
        result.violations.extend(violations)

    return result


def _diff_scan(base: str, head: str, root: str,
               exemptions: Set[str]) -> ScanResult:
    """增量扫描（仅检查变更文件）"""
    result = ScanResult()
    changed_files = _get_changed_files(base, head, root)

    for rel_path in changed_files:
        if _is_excluded(rel_path):
            continue
        if _is_exempted(rel_path):
            continue

        abs_path = os.path.join(root, rel_path)
        if not os.path.exists(abs_path):
            continue

        result.scanned_files += 1
        violations = _scan_file(abs_path)

        # 过滤豁免清单中的文件
        normalized = rel_path.replace("\\", "/")
        if normalized in exemptions:
            result.exempted_violations += len(violations)
            continue

        for v in violations:
            v.file = rel_path  # 用相对路径
        result.violations.extend(violations)

    return result


def _print_report(result: ScanResult, max_violations: int) -> int:
    """输出扫描报告，返回退出码"""
    print("=" * 60)
    print("日志双重序列化反模式扫描报告")
    print("=" * 60)
    print(f"扫描文件数: {result.scanned_files}")
    print(f"违规总数: {len(result.violations)}")
    print(f"豁免违规数: {result.exempted_violations}")
    print()

    if not result.violations:
        print("✅ 无新增违规")
        return 0

    # 按文件分组统计
    by_file = {}
    for v in result.violations:
        by_file.setdefault(v.file, []).append(v)

    print(f"违规文件列表（按违规数排序，共 {len(by_file)} 个文件）:")
    for file_path, viols in sorted(by_file.items(),
                                   key=lambda x: -len(x[1])):
        print(f"\n  {file_path} ({len(viols)} 处):")
        for v in viols[:5]:  # 每个文件最多展示 5 处
            print(f"    L{v.line}: {v.line_content.strip()[:100]}")
        if len(viols) > 5:
            print(f"    ... 还有 {len(viols) - 5} 处")

    print(f"\n新增违规数: {len(result.violations)} "
          f"(允许上限: {max_violations})")

    if len(result.violations) > max_violations:
        print(f"\n❌ 发现 {len(result.violations)} 处新增违规，"
              f"超过上限 {max_violations}")
        print("\n迁移建议:")
        print("  将 logger.info(json.dumps(payload, ensure_ascii=False)) 替换为:")
        print("  logger.info(log_dict(payload))")
        print("  其中 log_dict 来自 agent.logging_utils")
        return 1

    print("✅ 新增违规在允许范围内")
    return 0


def main() -> None:
    args = _parse_args()

    if args.full_scan:
        print("=== 全量扫描模式 ===")
        result = _full_scan(args.root)
        if args.update_exemptions:
            _update_exemptions(args.exemption_file, result, args.root)
            return
    else:
        print(f"=== 增量扫描模式: {args.base} → {args.head} ===")
        exemptions = _load_exemptions(args.exemption_file)
        result = _diff_scan(args.base, args.head, args.root, exemptions)

    exit_code = _print_report(result, args.max_violations)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
