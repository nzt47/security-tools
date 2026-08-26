"""端到端 Schema 裁剪 token 消耗验证脚本

用途:
  从 data/tool_definitions/*.yaml 直接加载真实工具定义,
  对比 prune_tool_defs 前后的 token 占用,验证任务要求的 ≥30% 减幅。

诚实声明(守 [不易]):
  - 生产环境实际减幅取决于 tool_defs 中 deprecated:true 字段密度与 description 长度
  - 同时展示「真实场景」与「verbose 场景(强制注入 deprecated/超长 description)」两种结果

用法:
  python scripts/verify_schema_token_reduction.py
  python scripts/verify_schema_token_reduction.py --whitelist web_search,read_file
  python scripts/verify_schema_token_reduction.py --yaml-dir data/tool_definitions_deprecated_test
"""
import sys
import json
import argparse
from pathlib import Path

# 把项目根加入 sys.path(脚本独立运行)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 加载 .env 配置(SCHEMA_DESC_MAX_LEN 等)
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

from agent.tools import get_tool_defs
from agent.tool_schema_pruner import prune_tool_defs


def _estimate_tokens(text: str) -> int:
    """估算 token 数。优先 tiktoken(cl100k_base),降级用字符数/4 近似。"""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def _load_real_tool_defs_from_yaml(yaml_dir: str = None) -> list:
    """从 data/tool_definitions/*.yaml 直接加载真实工具定义(转 OpenAI tool_defs 格式)。

    Why: agent.tools.get_tool_defs() 需项目完整初始化才注册工具(脚本独立运行返回空)。
         yaml 是工具定义源头,直接读取可展示真实场景 token 数据。
    """
    if yaml_dir is None:
        yaml_dir = str(_PROJECT_ROOT / "data" / "tool_definitions")
    try:
        import yaml
    except ImportError:
        print("⚠ PyYAML 未安装,无法加载真实 yaml 工具定义")
        return []
    tool_defs = []
    yaml_path = Path(yaml_dir)
    if not yaml_path.exists():
        return []
    for yf in sorted(yaml_path.glob("*.yaml")):
        try:
            with open(yf, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data or not data.get("name"):
                continue
            func = {
                "name": data["name"],
                "description": data.get("description", ""),
            }
            if data.get("deprecated") is True:
                func["deprecated"] = True
            schema = data.get("schema")
            if isinstance(schema, dict):
                func["parameters"] = schema
            tool_defs.append({"type": "function", "function": func})
        except Exception as e:
            print("  跳过 %s: %s" % (yf.name, e))
    return tool_defs


def _run_scenario(name: str, tool_defs: list, threshold: float) -> dict:
    """运行单个场景:裁剪前后 token 对比"""
    print("=" * 70)
    print("场景: %s" % name)
    print("=" * 70)

    orig_json = json.dumps(tool_defs, ensure_ascii=False)
    pruned_defs = prune_tool_defs(tool_defs, intent_context={"selected_tools": []})
    pruned_json = json.dumps(pruned_defs, ensure_ascii=False)

    orig_tokens = _estimate_tokens(orig_json)
    pruned_tokens = _estimate_tokens(pruned_json)
    reduction = (orig_tokens - pruned_tokens) / orig_tokens * 100 if orig_tokens > 0 else 0

    # 裁剪细节统计
    deprecated_props = orig_json.count('"deprecated": true') - pruned_json.count('"deprecated": true')
    tool_level_removed = len(tool_defs) - len(pruned_defs)
    desc_truncated = sum(
        1 for t in pruned_defs
        if t.get("function", {}).get("description", "").endswith("...")
    )
    addl_props_removed = orig_json.count('"additionalProperties": true') - pruned_json.count('"additionalProperties": true')

    print("工具数: %d → %d (移除 %d 个工具级 deprecated)" % (
        len(tool_defs), len(pruned_defs), tool_level_removed,
    ))
    print("Token: %d → %d (减少 %d, %.2f%%)" % (
        orig_tokens, pruned_tokens, orig_tokens - pruned_tokens, reduction,
    ))
    print("裁剪细节:")
    print("  - 移除 deprecated 属性: %d" % deprecated_props)
    print("  - 移除工具级 deprecated: %d" % tool_level_removed)
    print("  - 截断 description: %d" % desc_truncated)
    print("  - 移除冗余 additionalProperties: %d" % addl_props_removed)
    passed = reduction >= threshold
    print("验收(≥ %d%%): %s" % (threshold, "✓ 通过" if passed else "✗ 未达标"))
    return {"name": name, "orig_tokens": orig_tokens, "pruned_tokens": pruned_tokens,
            "reduction_pct": reduction, "passed": passed}


def main():
    parser = argparse.ArgumentParser(description="Schema 裁剪 token 验证")
    parser.add_argument("--whitelist", type=str, default="",
                        help="工具白名单逗号分隔(默认全部)")
    parser.add_argument("--verbose-only", action="store_true",
                        help="只跑 verbose fixture 场景")
    parser.add_argument("--threshold", type=float, default=30.0,
                        help="减幅阈值(默认 30%%)")
    parser.add_argument("--yaml-dir", type=str, default="",
                        help="yaml 工具定义目录(默认 data/tool_definitions;可指向测试副本)")
    args = parser.parse_args()

    print("Schema 裁剪 Token 验证")
    print("项目根: %s" % _PROJECT_ROOT)
    all_results = []

    if not args.verbose_only:
        try:
            whitelist = [s.strip() for s in args.whitelist.split(",") if s.strip()] or None
            yaml_dir = args.yaml_dir or None
            real_defs = _load_real_tool_defs_from_yaml(yaml_dir=yaml_dir)
            if not real_defs:
                real_defs = get_tool_defs(whitelist=whitelist)
            if whitelist:
                wl_set = set(whitelist)
                real_defs = [t for t in real_defs if t.get("function", {}).get("name") in wl_set]
            source_label = args.yaml_dir or "data/tool_definitions"
            print("\n加载真实 tool_defs: %d 个工具 (whitelist=%s, yaml_dir=%s)" % (
                len(real_defs), whitelist or "ALL", source_label,
            ))
            if not real_defs:
                print("⚠ 真实 tool_defs 为空 — 跳过真实场景")
            else:
                scenario_name = "测试副本(注入 deprecated)" if args.yaml_dir else "真实场景(生产 yaml tool_defs)"
                all_results.append(_run_scenario(scenario_name, real_defs, args.threshold))
        except Exception as e:
            print("真实场景加载失败: %s: %s" % (type(e).__name__, e))

    # verbose 场景:强制注入 deprecated + 超长 description
    verbose_fixture = [{
        "type": "function",
        "function": {
            "name": "verbose_tool",
            "description": ("超长工具描述" * 50) + "…",
            "parameters": {
                "type": "object",
                "required": ["core"],
                "properties": {
                    "core": {"type": "string", "description": "核心参数(必填,保留)"},
                    "old_a": {"type": "string", "deprecated": True, "description": "旧参数A(废弃,移除)"},
                    "old_b": {"type": "integer", "deprecated": True, "description": "旧参数B(废弃,移除)"},
                },
                "additionalProperties": True,
            },
        },
    }, {
        "type": "function",
        "function": {"name": "legacy_tool", "deprecated": True, "description": "旧工具(整工具废弃)"},
    }]
    all_results.append(_run_scenario("verbose 场景(强制 deprecated + 超长 description)", verbose_fixture, args.threshold))

    # 汇总
    print("\n" + "=" * 70)
    print("总结")
    print("=" * 70)
    for r in all_results:
        print("  %-45s %s (%.2f%%)" % (r["name"], "✓ 通过" if r["passed"] else "✗ 未达标", r["reduction_pct"]))
    print("\n说明(守 [不易] 诚实报告):")
    print("  - 真实场景减幅取决于生产 tool_defs 中 deprecated:true 字段密度")
    print("  - verbose 场景强制注入 deprecated + 超长 description,可稳定 ≥ 30%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
