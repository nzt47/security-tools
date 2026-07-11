"""工具生成引擎 — 云枢自生成工具的能力

支持两种模式：
1. generate_simple(): 不落盘，直接注册到内存，用完即弃
2. generate_persistent(): 保存到 tools/custom/ 目录，持久化
"""
import ast
import logging
import multiprocessing
import os
from typing import Any

from agent import tools as _tools

logger = logging.getLogger(__name__)

# 自定义工具存储目录
_CUSTOM_TOOLS_DIR = os.path.join(os.path.dirname(__file__), "custom")

# 工具代码执行超时（秒）——防止 while True: pass 等无限循环阻塞注册流程
_TOOL_CODE_TIMEOUT_SEC = 5.0


def _validate_tool_code_safety(code: str) -> tuple[bool, str]:
    """AST 静态校验：顶层仅允许函数/类定义、导入、赋值、文档字符串

    拒绝 while/for/if/with/try/裸表达式等可能在顶层阻塞的语句。
    函数体内部的循环不受限制（在工具调用时执行，非注册阶段）。

    Returns:
        (True, "") 安全； (False, reason) 不安全
    Raises:
        SyntaxError: 代码语法错误
    """
    tree = ast.parse(code)

    _SAFE_TOP_LEVEL = (
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Import,
        ast.ImportFrom,
        ast.Assign,
        ast.AnnAssign,
    )

    for node in tree.body:
        if isinstance(node, _SAFE_TOP_LEVEL):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # 文档字符串
        return False, f"不允许的顶层语句: {type(node).__name__}（仅允许函数/类定义、导入、赋值）"

    return True, ""


def _tool_code_worker(code_str: str, result_queue):
    """子进程入口：执行工具代码定义，验证可安全执行

    Why: 必须是模块级函数，multiprocessing.spawn 模式下才能被 pickle。
    不限制 builtins（工具代码需要完整 Python 能力来定义函数）。
    """
    try:
        compiled = compile(code_str, "<generated>", "exec")
        namespace = {}
        exec(compiled, namespace)
        result_queue.put({"ok": True})
    except Exception as e:
        result_queue.put({"ok": False, "error": f"{type(e).__name__}: {e}"})


def _exec_with_timeout(code: str, timeout_sec: float = _TOOL_CODE_TIMEOUT_SEC) -> tuple[bool, str]:
    """子进程超时动态校验：在 spawn 子进程中执行代码定义

    Why: AST 校验无法捕获阻塞型赋值（如 x = blocking_call()）和阻塞型 import，
    子进程超时是第二层防御。验证通过后主进程会重新 exec 获取函数对象。

    Returns:
        (True, "") 验证通过； (False, error_msg) 验证失败或超时
    """
    ctx = multiprocessing.get_context("spawn")
    result_queue = ctx.Queue()
    process = ctx.Process(
        target=_tool_code_worker,
        args=(code, result_queue),
        daemon=True,
    )
    process.start()
    process.join(timeout=timeout_sec)

    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join(timeout=1)
        result_queue.close()
        result_queue.join_thread()
        return False, f"代码执行超时（{timeout_sec}秒）——可能包含无限循环或阻塞操作"

    try:
        result = result_queue.get(timeout=1)
    except Exception:
        result_queue.close()
        result_queue.join_thread()
        return False, "子进程异常终止，未返回结果"

    result_queue.close()
    result_queue.join_thread()

    if not result.get("ok"):
        return False, result.get("error", "未知错误")

    return True, ""


class ToolGenEngine:
    """工具代码生成引擎 — D 能力的核心"""

    def generate_simple(self, name: str, description: str,
                        code: str, schema: dict | None = None) -> bool:
        """注册一个简单的内联工具（不落盘）

        Args:
            name: 工具名称
            description: 工具描述
            code: Python 函数代码
            schema: JSON Schema（可选，自动推断）

        Returns:
            是否成功注册
        """
        try:
            # 第一层：AST 静态校验——拒绝顶层 while/for/if/裸表达式等危险语句
            safe, reason = _validate_tool_code_safety(code)
            if not safe:
                logger.error(f"工具代码安全校验失败: {reason}")
                return False

            # 第二层：子进程超时动态校验——防止 AST 遗漏的阻塞型赋值/import
            ok, error = _exec_with_timeout(code)
            if not ok:
                logger.error(f"工具代码执行验证失败: {error}")
                return False

            # 验证通过，主进程 exec 获取函数对象（函数无法跨进程序列化）
            compiled = compile(code, "<generated>", "exec")
            namespace = {}
            exec(compiled, namespace)

            # 查找与工具名匹配的函数
            handler = namespace.get(name)
            if not handler or not callable(handler):
                # 尝试找第一个可调用对象
                for v in namespace.values():
                    if callable(v) and not v.__name__.startswith("_"):
                        handler = v
                        break
            if not handler or not callable(handler):
                logger.error(f"生成的代码中未找到可调用函数: {name}")
                return False

            _tools.register_dynamic(
                name, description, handler=handler,
                schema=schema or {"type": "object", "properties": {}},
                source="generated",
            )
            logger.info(f"内联工具已注册: {name}")
            return True
        except SyntaxError as e:
            logger.error(f"生成工具语法错误: {e}")
            return False
        except Exception as e:
            logger.error(f"生成工具注册失败: {e}")
            return False

    def generate_persistent(self, name: str, description: str,
                            code: str, schema: dict | None = None,
                            category: str = "custom") -> bool:
        """注册一个持久化工具（保存到 tools/custom/ 目录）

        Args:
            name: 工具名称
            description: 工具描述
            code: Python 函数代码
            schema: JSON Schema（可选）
            category: 分类子目录名

        Returns:
            是否成功生成并注册
        """
        try:
            # 先注册到内存
            ok = self.generate_simple(name, description, code, schema)
            if not ok:
                return False

            # 确保目录存在
            target_dir = os.path.join(_CUSTOM_TOOLS_DIR, category)
            os.makedirs(target_dir, exist_ok=True)

            # 生成完整的模块文件（含 register_all 函数）
            schema_str = str(schema or {"type": "object", "properties": {}})
            file_path = os.path.join(target_dir, f"{name}.py")
            module_code = f'''"""自动生成的工具: {name}"""
import logging
from agent import tools as _tools

logger = logging.getLogger(__name__)


def register_all(dl=None):
    """注册 {name} 工具到全局注册表"""
{self._indent(code, 4)}

    # 在全局注册表中注册
    _tools.register_dynamic(
        "{name}",
        "{description}",
        handler={name},
        schema={schema_str},
        source="generated",
        source_id="custom_{name}",
    )
    logger.info("自定义工具已注册: {name}")
'''
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(module_code)
            logger.info(f"自定义工具已持久化: {file_path}")
            return True
        except Exception as e:
            logger.error(f"持久化工具失败: {e}")
            return False

    @staticmethod
    def _indent(code: str, spaces: int = 4) -> str:
        """给代码块添加缩进"""
        indent = " " * spaces
        return indent + code.replace("\n", f"\n{indent}")
