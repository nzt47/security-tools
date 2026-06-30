#!/usr/bin/env python3
"""Grafana 功能看板生成脚本

基于模板 monitoring/grafana_dashboards/templates/feature_template.json，
替换模块名占位符生成可导入 Grafana 的看板 JSON。

使用示例：
    # 生成 chat 模块看板到默认位置
    python scripts/generate_dashboard.py --module chat

    # 指定输出路径
    python scripts/generate_dashboard.py --module chat --output ./chat_dashboard.json

    # 列出模板中的占位符与指标
    python scripts/generate_dashboard.py --module chat --dry-run

生成后操作：
    1. 打开 Grafana → Dashboards → Import
    2. 上传生成的 JSON 文件
    3. 选择 Prometheus 数据源
    4. 点击 Import 完成导入

指标命名规范：yunshu_<module>_total / yunshu_<module>_duration_seconds
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# 模板文件路径（相对项目根目录）
TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "monitoring" / "grafana_dashboards" / "templates" / "feature_template.json"

# 占位符标记（在模板中以双下划线包裹）
PLACEHOLDER = "__MODULE__"

# 模块名校验规则：仅允许小写字母、数字、下划线
MODULE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_module_name(module: str) -> bool:
    """校验模块名合法性（防止注入与非法指标名）"""
    if not module:
        return False
    return bool(MODULE_PATTERN.match(module))


def load_template(template_path: Path) -> dict:
    """加载模板 JSON"""
    if not template_path.exists():
        raise FileNotFoundError(f"模板文件不存在: {template_path}")
    with open(template_path, "r", encoding="utf-8") as f:
        return json.load(f)


def replace_placeholder(obj, module: str):
    """递归替换 JSON 结构中的占位符"""
    if isinstance(obj, str):
        return obj.replace(PLACEHOLDER, module)
    if isinstance(obj, dict):
        return {k: replace_placeholder(v, module) for k, v in obj.items()}
    if isinstance(obj, list):
        return [replace_placeholder(item, module) for item in obj]
    return obj


def collect_metrics(dashboard: dict) -> list:
    """提取看板中引用的 PromQL 指标名（去重）"""
    metrics = set()
    pattern = re.compile(r"yunshu_[a-z_]+(?:_total|_duration_seconds_bucket|_duration_seconds)")

    def _scan(node):
        if isinstance(node, str):
            for match in pattern.finditer(node):
                metrics.add(match.group(0))
        elif isinstance(node, dict):
            for v in node.values():
                _scan(v)
        elif isinstance(node, list):
            for item in node:
                _scan(item)

    _scan(dashboard)
    return sorted(metrics)


def generate(module: str, output: str, dry_run: bool) -> int:
    """生成看板 JSON

    Args:
        module: 模块名
        output: 输出路径（空则使用默认）
        dry_run: 仅预览不写文件

    Returns:
        0 表示成功，非 0 表示失败
    """
    # 1. 校验模块名
    if not validate_module_name(module):
        print(f"[ERROR] 模块名非法: {module!r}（需匹配 {MODULE_PATTERN.pattern}）", file=sys.stderr)
        return 2

    # 2. 加载模板
    try:
        template = load_template(TEMPLATE_PATH)
    except Exception as e:
        print(f"[ERROR] 加载模板失败: {e}", file=sys.stderr)
        return 3

    # 3. 替换占位符
    result = replace_placeholder(template, module)

    # 4. 收集引用的指标（用于提示用户需确保埋点存在）
    metrics = collect_metrics(result)

    # 5. 计算输出路径
    if not output:
        output_path = TEMPLATE_PATH.parent.parent / f"yunshu_{module}_dashboard.json"
    else:
        output_path = Path(output)

    # 6. 干跑模式：仅打印信息不写文件
    if dry_run:
        print(f"[DRY-RUN] 模块: {module}")
        print(f"[DRY-RUN] 输出路径: {output_path}")
        print(f"[DRY-RUN] 引用指标:")
        for m in metrics:
            print(f"  - {m}")
        print(f"[DRY-RUN] 看板标题: {result.get('title', '(未知)')}")
        print(f"[DRY-RUN] 看板 UID: {result.get('uid', '(未知)')}")
        return 0

    # 7. 写入文件（确保父目录存在）
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERROR] 写入文件失败: {e}", file=sys.stderr)
        return 4

    # 8. 输出成功信息
    print(f"[OK] 看板已生成: {output_path}")
    print(f"[OK] 模块: {module}")
    print(f"[OK] 引用指标 ({len(metrics)} 个):")
    for m in metrics:
        print(f"     - {m}")
    print(f"\n[INFO] 导入步骤:")
    print(f"  1. 打开 Grafana → Dashboards → Import")
    print(f"  2. 上传: {output_path}")
    print(f"  3. 选择 Prometheus 数据源")
    print(f"  4. 点击 Import")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="基于模板生成 Grafana 功能监控看板",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/generate_dashboard.py --module chat
  python scripts/generate_dashboard.py --module chat --output ./out/chat.json
  python scripts/generate_dashboard.py --module tool_call --dry-run
        """,
    )
    parser.add_argument(
        "--module", "-m",
        required=True,
        help="模块名（小写字母/数字/下划线，如 chat/tool_call/memory_search）",
    )
    parser.add_argument(
        "--output", "-o",
        default="",
        help="输出文件路径（默认: monitoring/grafana_dashboards/yunshu_<module>_dashboard.json）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅预览引用指标与输出路径，不写文件",
    )
    args = parser.parse_args()

    sys.exit(generate(args.module, args.output, args.dry_run))


if __name__ == "__main__":
    main()
