"""一次性迁移脚本：从 agent/tools/*.py 的 @register 调用抽取工具定义到 YAML。

【不易】仅读取静态字面量（name/description/schema），不执行任何工具代码，
        不触碰运行时行为。TOOL_CATEGORIES 分类映射来源于现有 tool_router。
【变易】可重复运行（覆盖写），便于增量补充新工具。
【简易】AST 抽取 + 反查分类，无运行时依赖。

用法:
    python scripts/migrate_tools_to_yaml.py
    python scripts/migrate_tools_to_yaml.py --dry-run   # 仅报告不写文件
"""
from __future__ import annotations

import argparse
import ast
import logging
import os
import sys
from typing import Any

import yaml

logger = logging.getLogger("migrate_tools")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS_DIR = os.path.join(_PROJECT_ROOT, "agent", "tools")
_OUT_DIR = os.path.join(_PROJECT_ROOT, "data", "tool_definitions")

# 已知分类键（与 tool_router 默认值保持一致，作为分类白名单）
# 这些键的元数据（label/icon/description/always）保留在 tool_router 兜底默认中，
# YAML 仅承载工具列表与 schema。
KNOWN_CATEGORIES = [
    "core", "web", "file", "code", "system",
    "extension", "pdf", "software", "async", "schedule", "v2",
]


def _literal(node: ast.AST) -> Any:
    """安全求值字面量节点；非字面量返回 None。"""
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def _is_register_call(node: ast.AST) -> bool:
    """识别 `_tools.register(...)` 形式的装饰器调用（任意变量名 + .register 属性）。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    # 仅匹配 <Name>.register(...) —— 全局 tools 注册表
    # 排除 dl._planning_tools.register 等子注册表（planning 工具不进入 TOOL_CATEGORIES）
    if isinstance(func, ast.Attribute) and func.attr == "register":
        if isinstance(func.value, ast.Name):
            return True
    return False


def _extract_from_call(call: ast.Call) -> dict | None:
    """从 register(...) 调用中抽取 name/description/schema。

    约定：第一个位置参数为 name(str)，第二个为 description(str)，
          schema 以关键字参数 schema={...} 提供（可缺失）。
    """
    args = call.args
    if not args:
        return None
    name = _literal(args[0])
    if not isinstance(name, str):
        return None
    description = ""
    if len(args) >= 2:
        d = _literal(args[1])
        if isinstance(d, str):
            description = d
    schema: dict | None = None
    for kw in call.keywords:
        if kw.arg == "schema" and isinstance(kw.value, ast.Dict):
            schema = _literal(kw.value)
            if not isinstance(schema, dict):
                schema = None
    return {"name": name, "description": description, "schema": schema}


def _scan_file(path: str) -> list[dict]:
    """扫描单个 .py 文件，返回所有 register 调用抽取结果。"""
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        logger.error("语法错误，跳过 %s: %s", path, e)
        return []

    results: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                # 装饰器可能是 @_tools.register(...) 调用
                target = dec
                if isinstance(dec, ast.Call):
                    target = dec
                if _is_register_call(target):
                    extracted = _extract_from_call(target)
                    if extracted:
                        results.append(extracted)
    return results


def _build_category_map() -> dict[str, str]:
    """从 tool_router 构建 tool_name -> category 反查表。"""
    sys.path.insert(0, _PROJECT_ROOT)
    try:
        from agent.tool_router import TOOL_CATEGORIES  # noqa: WPS433
    except Exception as e:  # 兜底：导入失败直接报错（迁移依赖现有分类）
        raise SystemExit(f"无法导入 TOOL_CATEGORIES: {e}")
    mapping: dict[str, str] = {}
    for cat_key, cat_info in TOOL_CATEGORIES.items():
        for tool in cat_info.get("tools", []):
            mapping[tool] = cat_key
    return mapping


def _to_yaml_doc(tool: dict) -> dict:
    """构造单个工具的 YAML 文档结构。"""
    schema = tool.get("schema") or {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }
    return {
        "name": tool["name"],
        "category": tool["category"],
        "description": tool["description"],
        "deprecated": False,
        "version": "1.0.0",
        "schema": schema,
        "examples": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移工具定义到 YAML")
    parser.add_argument("--dry-run", action="store_true", help="仅报告，不写文件")
    parser.add_argument("--out", default=_OUT_DIR, help="输出目录")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    category_map = _build_category_map()

    all_tools: dict[str, dict] = {}  # name -> tool dict
    files = sorted(
        os.path.join(_TOOLS_DIR, f)
        for f in os.listdir(_TOOLS_DIR)
        if f.endswith(".py") and f != "__init__.py"
    )

    for path in files:
        for extracted in _scan_file(path):
            name = extracted["name"]
            if name in all_tools:
                # 同名重复注册（如 core_tools 中 search_memory 在多模块注册），
                # 保留第一个出现的；记录差异仅当 schema 不同。
                prev = all_tools[name]
                if extracted["schema"] and not prev.get("schema"):
                    prev["schema"] = extracted["schema"]
                    prev["description"] = prev["description"] or extracted["description"]
                logger.debug("重复工具 %s 已存在，跳过", name)
                continue
            cat = category_map.get(name, "uncategorized")
            extracted["category"] = cat
            all_tools[name] = extracted

    # 统计
    in_categories = [n for n, t in all_tools.items() if t["category"] != "uncategorized"]
    uncategorized = [n for n, t in all_tools.items() if t["category"] == "uncategorized"]
    missing_in_yaml = [n for n in category_map if n not in all_tools]

    logger.info("扫描完成: 共 %d 个工具", len(all_tools))
    logger.info("  - 在 TOOL_CATEGORIES 中: %d", len(in_categories))
    logger.info("  - 未分类(uncategorized): %d -> %s", len(uncategorized), uncategorized)
    logger.info("  - YAML 缺失但分类中存在: %d -> %s", len(missing_in_yaml), missing_in_yaml)

    if args.dry_run:
        for name in sorted(all_tools):
            t = all_tools[name]
            logger.info("[dry-run] %s cat=%s schema=%s",
                        name, t["category"], "yes" if t.get("schema") else "NO")
        return 0

    os.makedirs(args.out, exist_ok=True)
    written = 0
    for name, tool in sorted(all_tools.items()):
        doc = _to_yaml_doc(tool)
        out_path = os.path.join(args.out, f"{name}.yaml")
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        written += 1
    logger.info("已写入 %d 个 YAML 文件到 %s", written, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
