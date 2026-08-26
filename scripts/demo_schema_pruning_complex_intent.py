"""复杂意图场景 Schema 裁剪效果 Demo

构造包含 deprecated 字段 + 无关 optional 字段 + 嵌套 deprecated + 超长 description
+ 冗余 additionalProperties 的复杂意图场景,手动查看裁剪前后对比。

用法:
  python scripts/demo_schema_pruning_complex_intent.py            # 前后对比
  python scripts/demo_schema_pruning_complex_intent.py --json     # 仅输出裁剪后 JSON
  python scripts/demo_schema_pruning_complex_intent.py --diff     # 字段级 diff
"""
import sys
import json
import argparse
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 加载 .env 配置(SCHEMA_DESC_MAX_LEN 等)
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

from agent.tool_schema_pruner import prune_tool_defs, SCHEMA_DESC_MAX_LEN


def _build_complex_fixture() -> list:
    """构造复杂意图场景 fixture(4 个工具)

    search_files: 主工具 — 2 deprecated optional + 1 嵌套 deprecated + 超长 description + 冗余 additionalProperties
    read_file:    正常工具 — required + optional
    legacy_tool:  工具级 deprecated:true → 整工具移除
    write_file:   正常工具 — 无关 optional(应保留)
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "search_files",
                "description": (
                    "按文件名模式搜索本地文件系统,支持 glob 通配符。"
                    "返回匹配文件列表(路径、大小、修改时间)。"
                    "该工具为深度文件搜索场景提供底层支撑,支持递归扫描子目录,"
                    "可配合 read_file 进行内容读取,适用于代码库导航、资源定位、"
                    "构建产物排查等场景,也支持在指定目录内限定范围搜索以提高效率。"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["query", "directory"],
                    "properties": {
                        "query": {"type": "string", "description": "文件名 glob 模式,如 *.py"},
                        "directory": {"type": "string", "description": "搜索起始目录"},
                        "file_types": {
                            "type": "array", "items": {"type": "string"},
                            "description": "限定文件类型(可选)",
                        },
                        "max_results": {
                            "type": "integer", "default": 50,
                            "description": "最大返回条数(可选)",
                        },
                        # deprecated optional(应移除,非 required)
                        "legacy_encoding": {
                            "type": "string", "deprecated": True,
                            "description": "旧版编码参数,已废弃",
                        },
                        "legacy_verbose": {
                            "type": "boolean", "deprecated": True,
                            "description": "旧版详细输出开关,已废弃",
                        },
                        "options": {
                            "type": "object",
                            "properties": {
                                "nested_deprecated_flag": {
                                    "type": "boolean", "deprecated": True,
                                    "description": "嵌套废弃标记",
                                },
                                "nested_kept": {
                                    "type": "boolean",
                                    "description": "嵌套保留字段",
                                },
                            },
                        },
                    },
                    "additionalProperties": True,  # 冗余,应移除
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取本地文件的全部内容(文本),支持指定编码。",
                "parameters": {
                    "type": "object",
                    "required": ["path"],
                    "properties": {
                        "path": {"type": "string", "description": "文件绝对路径"},
                        "encoding": {
                            "type": "string", "default": "utf-8",
                            "description": "文件编码(可选)",
                        },
                    },
                    "additionalProperties": True,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "legacy_tool",
                "deprecated": True,  # 工具级 deprecated → 整工具移除
                "description": "旧版工具,整工具废弃",
                "parameters": {
                    "type": "object",
                    "required": ["old_param"],
                    "properties": {
                        "old_param": {"type": "string", "description": "旧参数"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "将内容写入本地文件(可创建新文件或覆盖已有文件)。",
                "parameters": {
                    "type": "object",
                    "required": ["path", "content"],
                    "properties": {
                        "path": {"type": "string", "description": "目标文件路径"},
                        "content": {"type": "string", "description": "文件内容"},
                        "append": {
                            "type": "boolean", "default": False,
                            "description": "是否追加模式(可选)",
                        },
                    },
                    "additionalProperties": True,
                },
            },
        },
    ]


def _flatten_diff(orig: dict, pruned: dict, prefix: str = "") -> list:
    """字段级 diff:返回 (操作, 路径) 列表, 操作 ∈ {'removed','kept','truncated'}"""
    diffs = []
    if isinstance(orig, dict) and isinstance(pruned, dict):
        for k, v in orig.items():
            path = "%s.%s" % (prefix, k) if prefix else k
            if k not in pruned:
                diffs.append(("removed", path))
            else:
                if isinstance(v, dict) and isinstance(pruned[k], dict):
                    diffs.extend(_flatten_diff(v, pruned[k], path))
                elif isinstance(v, str) and isinstance(pruned[k], str) and len(v) != len(pruned[k]):
                    diffs.append(("truncated", path))
                else:
                    diffs.append(("kept", path))
        for k in pruned:
            if k not in orig:
                diffs.append(("added", "%s.%s" % (prefix, k) if prefix else k))
    return diffs


def main():
    parser = argparse.ArgumentParser(description="复杂意图场景 Schema 裁剪 Demo")
    parser.add_argument("--json", action="store_true", help="仅输出裁剪后 JSON")
    parser.add_argument("--diff", action="store_true", help="输出字段级 diff")
    args = parser.parse_args()

    fixture = _build_complex_fixture()
    pruned = prune_tool_defs(fixture, intent_context={"selected_tools": ["search_files", "read_file", "write_file"]})

    orig_json = json.dumps(fixture, ensure_ascii=False)
    pruned_json = json.dumps(pruned, ensure_ascii=False)
    char_reduction = (len(orig_json) - len(pruned_json)) / len(orig_json) * 100

    print("复杂意图场景 Schema 裁剪 Demo")
    print("SCHEMA_DESC_MAX_LEN=%d" % SCHEMA_DESC_MAX_LEN)
    print("=" * 70)

    if args.json:
        print(json.dumps(pruned, ensure_ascii=False, indent=2))
        return 0

    print("\n工具数: %d → %d (移除 %d 个工具级 deprecated)" % (
        len(fixture), len(pruned), len(fixture) - len(pruned),
    ))
    print("字符数: %d → %d (减少 %.2f%%)" % (len(orig_json), len(pruned_json), char_reduction))

    if args.diff:
        print("\n字段级 diff:")
        orig_by_name = {t["function"]["name"]: t for t in fixture}
        pruned_by_name = {t["function"]["name"]: t for t in pruned}
        for name, otd in orig_by_name.items():
            print("\n工具: %s" % name)
            if name not in pruned_by_name:
                print("  ✗ 整工具被移除(工具级 deprecated:true)")
                continue
            for op, path in _flatten_diff(otd, pruned_by_name[name]):
                short = path.replace("function.parameters.", "params.")
                if op == "removed":
                    print("  - 移除: %s" % short)
                elif op == "truncated":
                    print("  ~ 截断: %s" % short)
                # kept 不打印(噪音)
    else:
        print("\n裁剪前(截断显示):")
        for t in fixture:
            f = t["function"]
            print("  %s: %s" % (f["name"], f["description"][:60]))
        print("\n裁剪后(截断显示):")
        for t in pruned:
            f = t["function"]
            print("  %s: %s" % (f["name"], f["description"][:60]))

    print("\n验证(不变量):")
    checks = [
        ("required 字段保留", all(
            p.get("function", {}).get("parameters", {}).get("required")
            for p in pruned
            if p.get("function", {}).get("name") in ("search_files", "read_file", "write_file")
        )),
        ("deprecated optional 移除", "legacy_encoding" not in json.dumps(pruned)),
        ("工具级 deprecated 移除", all(t.get("function", {}).get("deprecated") is not True for t in pruned)),
        ("description 截断", all(
            len(t.get("function", {}).get("description", "")) <= SCHEMA_DESC_MAX_LEN + 3
            for t in pruned
        )),
        ("冗余 additionalProperties 移除", "additionalProperties" not in json.dumps(pruned)),
    ]
    for label, ok in checks:
        print("  %s %s" % ("✓" if ok else "✗", label))

    return 0


if __name__ == "__main__":
    sys.exit(main())
