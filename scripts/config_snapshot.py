#!/usr/bin/env python3
"""配置快照生成工具

导出当前 observability_config.py 的默认配置和元信息到 JSON 文件，
作为配置漂移检测的基准快照。

使用方式：
  # 生成快照到默认路径
  python scripts/config_snapshot.py

  # 指定输出路径
  python scripts/config_snapshot.py --output docs/observability/config_snapshot_master.json

  # 包含运行时值（默认仅导出默认值）
  python scripts/config_snapshot.py --include-runtime
"""

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path


def generate_snapshot(include_runtime: bool = False) -> dict:
    """生成配置快照

    Args:
        include_runtime: 是否包含运行时值（True 则同时记录默认值和运行时值）

    Returns:
        快照 dict，符合设计文档 3.1 节定义的格式
    """
    from agent.monitoring.observability_config import (
        OBSERVABILITY_VALIDATION_RULES,
        get_observability_config,
        reset_observability_config,
    )

    # 重置配置（确保读取的是默认值，而非被测试污染的运行时值）
    reset_observability_config()
    config = get_observability_config()
    config_tree = config.get_all()  # 嵌套 dict

    # 构建 metadata：每个配置路径的元信息
    metadata = {}
    for rule in OBSERVABILITY_VALIDATION_RULES:
        metadata[rule.path] = {
            "default": rule.default,
            "description": rule.description,
            "error_message": rule.error_message,
        }

    # 获取 git SHA（用于追溯）
    git_sha = _get_git_sha()

    snapshot = {
        "version": "1.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generated_from": f"observability_config.py@{git_sha}",
        "total_paths": len(OBSERVABILITY_VALIDATION_RULES),
        "config": config_tree,
        "metadata": metadata,
    }

    if include_runtime:
        snapshot["runtime_included"] = True

    return snapshot


def _get_git_sha() -> str:
    """获取当前 git HEAD SHA（失败返回 'unknown'）"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def main():
    parser = argparse.ArgumentParser(description="配置快照生成工具")
    parser.add_argument(
        "--output",
        "-o",
        default="docs/observability/config_snapshot_master.json",
        help="输出文件路径（默认: docs/observability/config_snapshot_master.json）",
    )
    parser.add_argument(
        "--include-runtime",
        action="store_true",
        help="包含运行时值（默认仅导出默认值）",
    )
    args = parser.parse_args()

    snapshot = generate_snapshot(include_runtime=args.include_runtime)

    # 确保输出目录存在
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"✓ 配置快照已生成: {output_path}")
    print(f"  版本: {snapshot['version']}")
    print(f"  生成时间: {snapshot['generated_at']}")
    print(f"  源: {snapshot['generated_from']}")
    print(f"  配置项总数: {snapshot['total_paths']}")


if __name__ == "__main__":
    main()
