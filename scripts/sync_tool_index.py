"""工具定义索引同步脚本 —— Git → 检索索引 的守门人。

【不易】
  - YAML 为 source of truth；本脚本只读 YAML，不读 Python 注册表。
  - 校验失败必须以非零退出码终止（CI 守门）。
  - 不修改任何 YAML，仅生成 data/tool_index.json。
【变易】
  - 支持 --check 模式：只校验不写索引（pre-commit 用）。
  - 与 tool_router 的分类一致性交叉校验（可降级：导入失败时仅告警）。
【简易】
  - 纯标准库 + PyYAML，无额外依赖。
  - semver 用正则校验，不引入 semver 包。

用法:
    python scripts/sync_tool_index.py              # 校验 + 生成索引
    python scripts/sync_tool_index.py --check      # 仅校验（CI/pre-commit）
    python scripts/sync_tool_index.py --verbose    # 详细报告
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import re
import sys
from typing import Any

import yaml

logger = logging.getLogger("sync_tool_index")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFS_DIR = os.path.join(_PROJECT_ROOT, "data", "tool_definitions")
_INDEX_PATH = os.path.join(_PROJECT_ROOT, "data", "tool_index.json")

# 必填字段（顶层）
REQUIRED_FIELDS = ["name", "category", "description", "deprecated", "version", "schema"]

# 已知分类键（与 tool_router 默认一致；uncategorized 为未路由工具的占位）
KNOWN_CATEGORIES = {
    "core", "web", "file", "code", "system",
    "extension", "pdf", "software", "async", "schedule", "v2",
    "uncategorized",
}

# semver 正则（简化版，覆盖 MAJOR.MINOR.PATCH + 可选 prerelease/build）
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


class ValidationError(Exception):
    """校验错误。"""


def _validate_semver(version: Any) -> bool:
    return isinstance(version, str) and _SEMVER_RE.match(version) is not None


def _validate_schema(schema: Any) -> list[str]:
    """校验 schema 是 JSON-Schema 子集（type=object, properties 为 dict）。

    返回错误信息列表（空表示通过）。
    """
    errs: list[str] = []
    if not isinstance(schema, dict):
        return ["schema 必须是对象(dict)"]
    stype = schema.get("type")
    if stype != "object":
        errs.append(f"schema.type 应为 'object'，实际为 {stype!r}")
    props = schema.get("properties")
    if props is not None and not isinstance(props, dict):
        errs.append("schema.properties 应为 dict")
    required = schema.get("required")
    if required is not None and not isinstance(required, list):
        errs.append("schema.required 应为 list")
    if required and not all(isinstance(r, str) for r in required):
        errs.append("schema.required 元素必须为 str")
    return errs


def _validate_doc(doc: Any, filename: str) -> tuple[dict | None, list[str]]:
    """校验单个 YAML 文档。返回 (doc_or_None, errors)。"""
    errs: list[str] = []
    if not isinstance(doc, dict):
        return None, [f"{filename}: 顶层不是对象"]

    for field in REQUIRED_FIELDS:
        if field not in doc:
            errs.append(f"{filename}: 缺少必填字段 {field!r}")

    name = doc.get("name")
    if not isinstance(name, str) or not name:
        errs.append(f"{filename}: name 必须为非空字符串")
    # 文件名应与 name 一致（约定）
    expected_file = f"{name}.yaml" if isinstance(name, str) else None
    if expected_file and expected_file != filename:
        errs.append(f"{filename}: 文件名与 name({name!r}) 不一致，应为 {expected_file!r}")

    category = doc.get("category")
    if not isinstance(category, str) or not category:
        errs.append(f"{filename}: category 必须为非空字符串")
    elif category not in KNOWN_CATEGORIES:
        errs.append(f"{filename}: category {category!r} 不在已知分类中 {sorted(KNOWN_CATEGORIES)}")

    if not isinstance(doc.get("description"), str):
        errs.append(f"{filename}: description 必须为字符串")

    if not isinstance(doc.get("deprecated"), bool):
        errs.append(f"{filename}: deprecated 必须为 bool")

    if not _validate_semver(doc.get("version")):
        errs.append(f"{filename}: version {doc.get('version')!r} 不是合法 semver")

    schema_errs = _validate_schema(doc.get("schema"))
    errs.extend(f"{filename}: {e}" for e in schema_errs)

    examples = doc.get("examples")
    if examples is None:
        examples = []  # 允许缺失，视为空
    if not isinstance(examples, list):
        errs.append(f"{filename}: examples 应为 list")
    return (doc if not errs else None), errs


def _load_all(defs_dir: str) -> tuple[list[dict], list[str]]:
    """加载并校验所有 YAML。返回 (valid_docs, all_errors)。"""
    if not os.path.isdir(defs_dir):
        return [], [f"定义目录不存在: {defs_dir}"]

    docs: list[dict] = []
    errors: list[str] = []
    seen_names: set[str] = set()

    files = sorted(f for f in os.listdir(defs_dir) if f.endswith(".yaml"))
    for fname in files:
        path = os.path.join(defs_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = yaml.safe_load(f)
        except yaml.YAMLError as e:
            errors.append(f"{fname}: YAML 解析失败: {e}")
            continue
        valid, errs = _validate_doc(doc, fname)
        errors.extend(errs)
        # 重复名检测：基于 name 字段独立追踪（即使其他字段校验失败也要查重），
        # 避免文件名不匹配时漏检同名重复定义。
        name_val = doc.get("name") if isinstance(doc, dict) else None
        if isinstance(name_val, str) and name_val:
            if name_val in seen_names:
                errors.append(f"{fname}: 工具名 {name_val!r} 重复定义")
            else:
                seen_names.add(name_val)
        if valid:
            docs.append(valid)
    return docs, errors


def _cross_check_categories(docs: list[dict]) -> list[str]:
    """与 tool_router.TOOL_CATEGORIES 交叉校验分类一致性。

    可降级：若 tool_router 无法导入，仅返回告警（不视为错误）。
    """
    warnings: list[str] = []
    try:
        sys.path.insert(0, _PROJECT_ROOT)
        from agent.tool_router import TOOL_CATEGORIES  # noqa: WPS433
    except Exception as e:
        warnings.append(f"[warn] 无法导入 tool_router 进行交叉校验: {e}")
        return warnings

    # tool_router 期望：每个已知分类下的工具集合
    router_tools_by_cat: dict[str, set[str]] = {
        cat: set(info.get("tools", [])) for cat, info in TOOL_CATEGORIES.items()
    }
    router_all: set[str] = set()
    for s in router_tools_by_cat.values():
        router_all |= s

    yaml_by_cat: dict[str, set[str]] = {}
    for d in docs:
        yaml_by_cat.setdefault(d["category"], set()).add(d["name"])

    # 仅对已知分类（非 uncategorized）做一致性比对
    errors: list[str] = []
    for cat, router_set in router_tools_by_cat.items():
        yaml_set = yaml_by_cat.get(cat, set())
        # 注意：tool_router 自身已从 YAML 派生时，两者天然一致；
        # 这里主要防止 YAML 与兜底默认漂移。仅报告差异，不阻断（避免循环依赖）。
        missing = router_set - yaml_set
        extra = yaml_set - router_set
        if missing:
            warnings.append(f"[warn] 分类 {cat!r}: YAML 缺少工具 {sorted(missing)}")
        if extra:
            warnings.append(f"[warn] 分类 {cat!r}: YAML 多出工具 {sorted(extra)}")
    return errors + warnings


def _build_index(docs: list[dict]) -> dict:
    """生成索引结构。"""
    return {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "tool_count": len(docs),
        "categories": sorted({d["category"] for d in docs}),
        "tools": [
            {
                "name": d["name"],
                "category": d["category"],
                "description": d["description"],
                "version": d["version"],
                "deprecated": d["deprecated"],
            }
            for d in sorted(docs, key=lambda x: (x["category"], x["name"]))
        ],
    }


def _parse_semver(version: str) -> tuple[int, int, int] | None:
    """解析 semver 为 (major, minor, patch)；非法返回 None。"""
    m = _SEMVER_RE.match(version) if isinstance(version, str) else None
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def check_version_compat(tool_name: str, referenced_version: str,
                         index: dict, log: logging.Logger | None = None) -> bool:
    """版本兼容性检查：旧工作流引用旧版本时记录 warning 但不报错。

    【不易】不抛异常、不阻断；仅记录 warning（向后兼容契约）。
    【简易】按 major.minor.patch 数值比较。

    Args:
        tool_name: 工具名
        referenced_version: 工作流中引用的版本（旧）
        index: data/tool_index.json 解析后的 dict
        log: 可选 logger

    Returns:
        True=兼容（含降级警告）; False=无法判定（工具不在索引或版本非法，仍不报错）
    """
    log = log or logging.getLogger("version_compat")
    tools = {t["name"]: t for t in index.get("tools", [])} if isinstance(index, dict) else {}
    current = tools.get(tool_name)
    if not current:
        log.warning("[version_compat] 工具 %r 不在索引中，跳过版本检查", tool_name)
        return False
    ref = _parse_semver(referenced_version)
    cur = _parse_semver(current.get("version", ""))
    if ref is None or cur is None:
        log.warning("[version_compat] %s 版本非法 referenced=%r current=%r",
                    tool_name, referenced_version, current.get("version"))
        return False
    if ref < cur:
        # 旧版本引用 —— 记录 warning 但不报错（向后兼容）
        log.warning("[version_compat] %s 引用旧版本 %s < 当前 %s（向后兼容，不阻断）",
                    tool_name, referenced_version, current.get("version"))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="工具定义索引同步与校验")
    parser.add_argument("--check", action="store_true",
                        help="仅校验不写索引（pre-commit/CI 用）")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--defs-dir", default=_DEFS_DIR, help="YAML 定义目录")
    parser.add_argument("--index", default=_INDEX_PATH, help="索引输出路径")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    docs, errors = _load_all(args.defs_dir)

    # 交叉校验（仅产生告警，不阻断）
    warnings = _cross_check_categories(docs)

    # 报告
    logger.info("校验完成: %d 个有效工具, %d 个错误, %d 个告警",
                len(docs), len(errors), len(warnings))
    for e in errors:
        logger.error(e)
    for w in warnings:
        logger.warning(w)

    if errors:
        logger.error("校验失败，未生成索引。请修复上述错误。")
        return 1

    if args.check:
        logger.info("--check 模式：校验通过，跳过索引写入。")
        return 0

    index = _build_index(docs)
    os.makedirs(os.path.dirname(args.index), exist_ok=True)
    with open(args.index, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    logger.info("索引已写入 %s (%d 个工具)", args.index, len(docs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
