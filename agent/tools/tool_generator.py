"""工具生成引擎 — 云枢自生成工具的能力

支持两种模式：
1. generate_simple(): 不落盘，直接注册到内存，用完即弃
2. generate_persistent(): 保存到 tools/custom/ 目录，持久化
"""
import ast
import logging
import os
from typing import Any

from agent import tools as _tools

logger = logging.getLogger(__name__)

# 自定义工具存储目录
_CUSTOM_TOOLS_DIR = os.path.join(os.path.dirname(__file__), "custom")


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
            # 编译验证语法
            compiled = compile(code, "<generated>", "exec")

            # 在沙盒命名空间中执行
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
