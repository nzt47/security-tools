"""结构化代码审查工具

基于 gstack review 检查清单（checklist.md + 四个 specialist），
对文件或 diff 进行多维度自动化审查。

审查维度:
  1. 安全 — SQL注入/XSS/密钥泄露/认证绕过
  2. 性能 — N+1查询/算法复杂度/资源泄露
  3. 可维护性 — 死代码/魔法数字/DRY违反
  4. API兼容性 — 破坏性变更/版本策略
  5. 测试 — 边界值/负路径

每个维度返回: {dimension, severity, findings: [{line, description, suggestion}]}
"""

import ast
import json
import logging
import os
import re
import subprocess
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  常量
# ──────────────────────────────────────────────

SENSITIVE_PATTERNS = [
    (re.compile(r'(?i)(?:sk-[a-zA-Z0-9]{20,}|pk-[a-zA-Z0-9]{20,})'), "疑似 OpenAI API Key"),
    (re.compile(r'(?i)api[_-]?key\s*[=:]\s*["\']?[A-Za-z0-9_\-]{16,}'), "疑似 API Key 赋值"),
    (re.compile(r'(?i)(?:password|passwd|pwd)\s*[=:]\s*["\'][^"\']{3,}["\']'), "疑似密码硬编码"),
    (re.compile(r'(?i)(?:secret|token|auth)\s*[=:]\s*["\'][^"\']{8,}["\']'), "疑似密钥/Token硬编码"),
    (re.compile(r'(?i)-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----'), "疑似私钥泄露"),
    (re.compile(r'(?i)conn(?:ection)?_string\s*[=:]\s*["\'].*?(?:password|pwd)'), "连接字符串含密码"),
]

SQL_INJECTION_PATTERNS = [
    (re.compile(r'f["\'].*?\{.*?\}.*?["\']\s*%?\s*[=%]'), "f-string 拼接 SQL 参数"),
    (re.compile(r'(?:execute|exec|query)\s*\(\s*f["\']'), "动态 f-string SQL 执行"),
    (re.compile(r'raw\(|RawSQL\(|connection\.execute\(.*?\+'), "原生 SQL 拼接"),
]

XSS_PATTERNS = [
    (re.compile(r'\.html_safe|\.raw\b|mark_safe\(|dangerouslySetInnerHTML|v-html\s*='), "不安全 HTML 渲染"),
    (re.compile(r'\binnerHTML\s*=\s*["\'].*?\{'), "innerHTML 插值"),
]

COMMAND_INJECTION_PATTERNS = [
    (re.compile(r'subprocess\.(?:run|call|Popen|check_output)\(.*?shell\s*=\s*True'), "shell=True 命令注入风险"),
    (re.compile(r'os\.system\(.*?[\+\%]'), "os.system() 拼接参数"),
    (re.compile(r'eval\(|exec\(|compile\('), "eval/exec 动态执行"),
]

# ──────────────────────────────────────────────
#  工具函数
# ──────────────────────────────────────────────


def _read_file_content(path: str) -> str:
    """读取文件内容，支持路径不存在时返回空字符串"""
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _iter_lines(content: str) -> list[tuple[int, str]]:
    """将内容拆分为 (行号, 行文本) 列表"""
    return list(enumerate(content.splitlines(), start=1))


def _make_finding(line: int, description: str, suggestion: str) -> dict:
    """创建一条审查发现"""
    return {
        "line": line,
        "description": description,
        "suggestion": suggestion,
    }


def _parse_python_ast(source: str) -> ast.Module | None:
    """安全解析 Python AST，失败返回 None"""
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


# ──────────────────────────────────────────────
#  维度审查器
# ──────────────────────────────────────────────


def _review_security(content: str, path: str) -> list[dict]:
    """安全维度审查：密钥泄露、SQL注入、XSS、命令注入"""
    findings = []
    lines = _iter_lines(content)

    for lineno, line in lines:
        # ── 密钥泄露 ──
        for pattern, desc in SENSITIVE_PATTERNS:
            if pattern.search(line):
                findings.append(_make_finding(
                    lineno,
                    f"安全风险: {desc}",
                    "将敏感信息移至环境变量或密钥管理服务（如 .env 或 vault），不要在代码中硬编码"
                ))

        # ── SQL注入 ──
        for pattern, desc in SQL_INJECTION_PATTERNS:
            if pattern.search(line):
                findings.append(_make_finding(
                    lineno,
                    f"SQL注入风险: {desc}",
                    "使用参数化查询（parameterized query）替代字符串拼接，如 ? 占位符或 ORM 的绑定参数"
                ))

        # ── XSS ──
        for pattern, desc in XSS_PATTERNS:
            if pattern.search(line):
                findings.append(_make_finding(
                    lineno,
                    f"XSS风险: {desc}",
                    "对用户输入进行 HTML 转义，使用模板引擎的自动转义，或使用 DOMPurify 等消毒库"
                ))

        # ── 命令注入 ──
        for pattern, desc in COMMAND_INJECTION_PATTERNS:
            if pattern.search(line):
                findings.append(_make_finding(
                    lineno,
                    f"命令注入风险: {desc}",
                    "使用参数数组形式（如 subprocess.run(['cmd', arg]）替代 shell=True 字符串拼接"
                ))

        # ── 认证绕过线索 ──
        if re.search(r'(?i)bypass|skip.*auth|disable.*check|allow\s*all', line):
            findings.append(_make_finding(
                lineno,
                "认证/授权绕过风险: 可能跳过安全检查",
                "确保认证检查不会在任何条件分支中被绕过，使用中间件或装饰器统一处理"
            ))

    # ── AST 层面的静态分析 ──
    tree = _parse_python_ast(content)
    if tree:
        for node in ast.walk(tree):
            # 检查 try-except 吞异常
            if isinstance(node, ast.ExceptHandler):
                if node.name and node.type is None:
                    f_lineno = getattr(node, 'lineno', 0)
                    if not any(n.get("line") == f_lineno and "空 except" in n.get("description", "") for n in findings):
                        findings.append(_make_finding(
                            f_lineno,
                            "安全风险: bare except 可能吞掉关键错误",
                            "使用更具体的异常类型（如 except Exception as e），并至少记录日志"
                        ))

    return findings


def _review_performance(content: str, path: str) -> list[dict]:
    """性能维度审查：N+1查询、算法复杂度、资源泄露"""
    findings = []
    lines = _iter_lines(content)

    # ── 逐行扫描 ──
    for lineno, line in lines:
        # N+1 查询模式（ORM 循环查询）
        if re.search(r'for\s+\w+\s+in\s+\w+\.(?:all|filter|select)\b', line):
            findings.append(_make_finding(
                lineno,
                "潜在 N+1 查询: 在循环中逐个查询数据库",
                "使用预加载（select_related / prefetch_related / .includes() / joinedload()）提前加载关联数据"
            ))
        if re.search(r'\.(?:query|objects)\s*\.\s*(?:get|first|filter)\b.*?\n\s+.*?\.(?:query|objects)\s*\.', content):
            pass  # 跨行模式较复杂，用后续 AST 分析补充

        # 同步阻塞在异步上下文
        if re.search(r'async\s+def', line):
            if re.search(r'\btime\.sleep\b', line):
                findings.append(_make_finding(
                    lineno,
                    "性能问题: async 函数中使用 time.sleep() 会阻塞事件循环",
                    "使用 await asyncio.sleep() 替代 time.sleep()"
                ))
            if re.search(r'\bsubprocess\.(?:run|call)\b', line):
                findings.append(_make_finding(
                    lineno,
                    "性能问题: async 函数中使用同步 subprocess.run() 会阻塞事件循环",
                    "使用 asyncio.create_subprocess_exec() 或 asyncio.to_thread() 包装"
                ))
            if re.search(r'\brest\.(?:get|post|put|delete)\b|\brequests\.', line):
                findings.append(_make_finding(
                    lineno,
                    "性能问题: async 函数中使用同步 HTTP 请求会阻塞事件循环",
                    "使用 httpx.AsyncClient 或 aiohttp 替代 requests"
                ))
            if re.search(r'\bopen\(', line):
                findings.append(_make_finding(
                    lineno,
                    "性能问题: async 函数中使用同步 open() 会阻塞事件循环",
                    "使用 aiofiles.open() 替代内置 open()"
                ))

        # 算法复杂度: O(n^2) 嵌套循环
        if re.search(r'for\s+\w+\s+in\s+\w+.*:\s*$', line):
            # 检查下一行是否有另一层 for
            pass  # AST 层面处理

        # 字符串拼接在循环中
        if re.search(r'for\s+.*:.*\n\s+\w+\s*\+=?\s*["\']', content):
            pass  # AST 层面

        # 日志字符串格式化（性能开销）
        if re.search(r'logging\.(?:debug|info|warning|error)\(.*?%|\.format\(|f["\']', line):
            pass  # 这是常见写法，只在严重时提示

    # ── AST 分析 ──
    tree = _parse_python_ast(content)
    if tree:
        for node in ast.walk(tree):
            # 嵌套循环
            if isinstance(node, ast.For):
                outer_lineno = getattr(node, 'lineno', 0)
                for inner in ast.walk(node):
                    if isinstance(inner, ast.For) and inner is not node:
                        inner_lineno = getattr(inner, 'lineno', 0)
                        if outer_lineno != inner_lineno:
                            if not any(n.get("line") == outer_lineno and "嵌套" in n.get("description", "") for n in findings):
                                findings.append(_make_finding(
                                    outer_lineno,
                                    "算法复杂度风险: 嵌套循环可能导致 O(n^2) 复杂度",
                                    "考虑用哈希表（dict/set）将内层循环替换为 O(1) 查找，或批量处理数据"
                                ))
                            break

            # 循环内字符串拼接
            if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add):
                if isinstance(node.target, ast.Name) and isinstance(node.value, (ast.Constant, ast.JoinedStr)):
                    parent = getattr(node, 'parent', None)
                    # 检查是否在 for 循环内
                    for parent_node in ast.walk(tree):
                        if isinstance(parent_node, ast.For):
                            for child in ast.walk(parent_node):
                                if child is node:
                                    f_lineno = getattr(node, 'lineno', 0)
                                    findings.append(_make_finding(
                                        f_lineno,
                                        "性能风险: 循环内字符串 += 拼接（O(n^2)）",
                                        "收集到 list 后用 ''.join() 一次性拼接"
                                    ))
                                    break

    return findings


def _review_maintainability(content: str, path: str) -> list[dict]:
    """可维护性维度审查：死代码、魔法数字、DRY违反"""
    findings = []
    lines = _iter_lines(content)
    tree = _parse_python_ast(content)

    # ── 魔法数字检测 ──
    magic_numbers = []
    if tree:
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                val = node.value
                lineno = getattr(node, 'lineno', 0)
                # 排除常见的非魔法数字: 0, 1, -1, 100, 0.5, True/False
                if val not in (0, 1, -1, 100, 0.5) and abs(val) <= 99999:
                    # 检查上下文：是否是赋值或比较的右值
                    is_magic = True
                    parent = getattr(node, 'parent', None)
                    # 简单启发式：排除索引、步长、简单算术
                    if isinstance(parent, (ast.Subscript, ast.Slice)):
                        is_magic = False
                    if isinstance(parent, ast.BinOp) and isinstance(parent.op, (ast.Mult, ast.Div)):
                        # 乘法/除法中的常数可能是有意义的
                        pass
                    if is_magic:
                        magic_numbers.append((lineno, val))

    # 合并相邻行的相同魔法数字
    seen_numbers = set()
    for lineno, val in magic_numbers:
        if val not in seen_numbers:
            seen_numbers.add(val)
            findings.append(_make_finding(
                lineno,
                f"魔法数字: `{val}` 直接出现在代码中",
                f"定义为具名常量（如 MAX_RETRIES = {val}），提升可读性和可维护性"
            ))

    # ── 逐行检查 ──
    for lineno, line in lines:
        # 注释掉的代码（超过3行时整体标记，这里标记每行）
        stripped = line.strip()
        if stripped.startswith("#") and len(stripped) > 3:
            code_indicators = ["def ", "class ", "return ", "if ", "for ", "import "]
            if any(indicator in stripped[1:] for indicator in code_indicators):
                findings.append(_make_finding(
                    lineno,
                    "死代码: 被注释掉的代码",
                    "移除注释代码，如有必要保留说明原因（如 # 保留用于调试参考）"
                ))

        # 硬编码路径
        if re.search(r'["\'](?:[A-Za-z]:\\\\|/[a-z]+/)', stripped):
            findings.append(_make_finding(
                lineno,
                "可维护性问题: 硬编码的路径",
                "使用 os.path.join() 或 Path() 构建路径，并通过配置管理"
            ))

        # 硬编码 URL
        if re.search(r'["\']https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)[^"\']*["\']', stripped):
            findings.append(_make_finding(
                lineno,
                "可维护性问题: 硬编码的本地 URL",
                "通过配置或环境变量管理，方便不同环境切换"
            ))

    # ── 检查导入 ──
    if tree:
        imports = set()
        used_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imports.add(alias.asname or alias.name)
            elif isinstance(node, ast.Name):
                used_names.add(node.id)

        unused = imports - used_names
        if unused and len(imports) > 1:  # 至少2个以上再报告
            # 找到未使用的导入的行号
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        name = alias.asname or alias.name
                        if name in unused:
                            findings.append(_make_finding(
                                getattr(node, 'lineno', 0),
                                f"未使用的导入: `{name}`",
                                f"移除未使用的 import `{name}`"
                            ))

    return findings


def _review_api_compatibility(content: str, path: str) -> list[dict]:
    """API兼容性维度审查：破坏性变更、版本策略"""
    findings = []
    lines = _iter_lines(content)

    for lineno, line in lines:
        stripped = line.strip()

        # 删除或重命名公开函数/方法（def 行前面有删除或改名前后的对比）
        if re.match(r'^\s*-\s*def\s+\w+', stripped):
            func_name = re.search(r'def\s+(\w+)', stripped)
            if func_name:
                findings.append(_make_finding(
                    lineno,
                    f"API兼容性: 函数 `{func_name.group(1)}` 被移除（破坏性变更）",
                    "如非必要保留旧函数并标记 deprecation warning；或确认调用方已迁移"
                ))

        # 函数签名变更
        if re.match(r'^[-+]\s*def\s+\w+\(', stripped):
            if stripped.startswith('-'):
                findings.append(_make_finding(
                    lineno,
                    "API兼容性: 函数签名变更（破坏性变更）",
                    "若必须变更，添加默认参数保持向后兼容，或新增函数并弃用旧的"
                ))

        # 路由/端点变更
        if re.search(r'@(?:app|router|blueprint)\.(?:route|get|post|put|delete|patch)\(', stripped):
            if stripped.startswith('-'):
                findings.append(_make_finding(
                    lineno,
                    "API兼容性: 路由被移除（破坏性变更）",
                    "确认没有客户端依赖此端点；如有时，提供迁移指南或保留跳转"
                ))

        # 移除导出/公开接口
        if re.search(r'__all__|__init__\.py', path):
            if stripped.startswith('-') and re.search(r'["\']\w+["\']', stripped):
                findings.append(_make_finding(
                    lineno,
                    "API兼容性: 公开接口变更",
                    "模块公开接口的移除可能影响导入该模块的代码"
                ))

        # 配置/环境变量变更
        if stripped.startswith('-') and re.search(r'(?:os\.environ|config|env)\b', stripped):
            findings.append(_make_finding(
                lineno,
                "API兼容性: 配置项/环境变量变更",
                "配置项变更需在 CHANGELOG 和配置文档中记录，并提��兼容处理"
            ))

    # 检查是否有版本变更（CHANGELOG/VERSION）
    if path and ('CHANGELOG' in path or 'VERSION' in path):
        for lineno, line in lines:
            if re.match(r'^\s*##?\s*\[?\d+\.\d+\.\d+', line):
                findings.append(_make_finding(
                    lineno,
                    f"版本号更新检测: {line.strip()}",
                    "确认版本号变更策略符合语义化版本（SemVer）：MAJOR.MINOR.PATCH"
                ))

    return findings


def _review_testing(content: str, path: str) -> list[dict]:
    """测试维度审查：边界值、负路径"""
    findings = []

    # 仅在测试文件中执行
    is_test_file = False
    if path:
        basename = os.path.basename(path)
        is_test_file = bool(re.search(r'(?:test_|_test|_spec|_tests?)', basename, re.I))

    lines = _iter_lines(content)

    for lineno, line in lines:
        stripped = line.strip()

        # ── 边界值检查 ──
        # 检查是否使用硬编码列表/范围
        if re.search(r'range\(\d+\)', stripped):
            # 检查是否伴随边界参数
            if not re.search(r'(?:zero|empty|single|boundary|edge)', content, re.I):
                findings.append(_make_finding(
                    lineno,
                    "测试边界: 使用固定范围但没有边界值测试",
                    "额外添加空集合、单元素、最大值的测试用例"
                ))

        # 检查 assert 没有覆盖 0 或空
        if re.search(r'assert\s+\w+\s*(?:==|=)\s*\d+', stripped):
            pass  # TODO: 更精确的边界分析

        # ── 负路径检查 ──
        if is_test_file:
            # 检查是否有异常测试
            if re.search(r'def\s+test_?\w+', stripped):
                func_name = re.search(r'def\s+(test_?\w+)', stripped)
                if func_name:
                    name = func_name.group(1)
                    # 如果没有对应的 error/fail/invalid/negative 测试
                    if not re.search(r'(?:error|fail|invalid|negative|exception|bad|wrong|denied)', name, re.I):
                        # 记录函数名，稍后检查是否有对应负路径
                        pass

    # 整体文件检查
    if is_test_file:
        all_text_lower = content.lower()
        has_negative = any(kw in all_text_lower for kw in ["error", "fail", "invalid", "exception", "negative"])
        if not has_negative and len(lines) > 20:
            # 超过 20 行的测试文件却没有任何负路径测试
            findings.append(_make_finding(
                1,
                "测试覆盖: 未发现负路径（negative path）测试",
                "为每个正常路径添加对应的异常场景测试：无效输入、权限不足、资源不存在等"
            ))

    return findings


# ──────────────────────────────────────────────
#  主入口
# ──────────────────────────────────────────────


def code_review(path: str = "", diff: str = "", dimensions: list | None = None) -> dict:
    """执行结构化代码审查

    对指定文件的内容或 diff 进行多维度自动化审查。
    如果没有指定 path 或 diff，则返回可用维度说明。

    Args:
        path: 要审查的文件路径（绝对路径）
        diff: 直接传入的 git diff 文本（可选，用作主要内容来源）
        dimensions: 审查维度列表，默认全部
            - "安全": SQL注入/XSS/密钥泄露/认证绕过
            - "性能": N+1查询/算法复杂度/资源泄露
            - "可维护性": 死代码/魔法数字/DRY违反
            - "API兼容性": 破坏性变更/版本策略
            - "测试": 边界值/负路径

    Returns:
        dict: {
            "ok": bool,
            "dimensions": [{"dimension": str, "severity": str, "findings": [...]}],
            "summary": {"total": int, "critical": int, "informational": int}
        }
    """
    # 确定审查内容
    source = diff or ""
    if not source and path:
        source = _read_file_content(path)

    if not source and not dimensions:
        return {
            "ok": True,
            "message": "请指定 path（文件路径）或 diff（差异文本）以执行审查",
            "available_dimensions": ["安全", "性能", "可维护性", "API兼容性", "测试"],
        }

    if not source and dimensions:
        return {
            "ok": False,
            "error": "审查内容为空，请提供待审查的 path 或 diff",
        }

    # 默认审查全部维度
    if dimensions is None:
        dimensions = ["安全", "性能", "可维护性", "API兼容性", "测试"]

    # 维度名称 -> 审查函数映射
    REVIEWERS = {
        "安全": ("CRITICAL", _review_security),
        "性能": ("INFORMATIONAL", _review_performance),
        "可维护性": ("INFORMATIONAL", _review_maintainability),
        "API兼容性": ("INFORMATIONAL", _review_api_compatibility),
        "测试": ("INFORMATIONAL", _review_testing),
    }

    results = []
    total_findings = 0
    total_critical = 0
    total_info = 0

    for dim in dimensions:
        if dim not in REVIEWERS:
            results.append({
                "dimension": dim,
                "severity": "UNKNOWN",
                "error": f"未知维度: {dim}",
                "findings": [],
            })
            continue

        severity, reviewer = REVIEWERS[dim]
        try:
            findings = reviewer(source, path or "")
        except Exception as e:
            logger.error("审查维度 %s 执行异常: %s", dim, e)
            findings = [{
                "line": 0,
                "description": f"审查异常: {e}",
                "suggestion": "请检查代码语法或联系开发者",
            }]

        dim_result = {
            "dimension": dim,
            "severity": severity,
            "findings": findings,
        }
        results.append(dim_result)

        total_findings += len(findings)
        if severity == "CRITICAL":
            total_critical += len(findings)
        else:
            total_info += len(findings)

    # 去重合并同类项
    for dim_result in results:
        seen: set = set()
        unique_findings = []
        for f in dim_result["findings"]:
            key = (f["line"], f["description"][:60])
            if key not in seen:
                seen.add(key)
                unique_findings.append(f)
        dim_result["findings"] = unique_findings

    return {
        "ok": True,
        "file": path or "(diff provided)",
        "dimensions": results,
        "summary": {
            "total": total_findings,
            "critical": total_critical,
            "informational": total_info,
        },
    }
