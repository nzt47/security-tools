#!/usr/bin/env python3
"""配置漂移检测工具

对比当前运行时配置与快照文件，识别 modified/removed/added 三类漂移。

使用方式：
  # 控制台报告
  python scripts/check_config_drift.py

  # JSON 报告（CI 使用）
  python scripts/check_config_drift.py --json --output drift_report.json

  # 阻断模式（检测到 high/critical 漂移时退出码 1）
  python scripts/check_config_drift.py --fail-on-drift

  # 指定快照文件
  python scripts/check_config_drift.py --snapshot path/to/snapshot.json

CI 集成：
  --fail-on-drift 参数使脚本在检测到 high/critical 漂移时以非零退出码退出
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


def _flatten_config(config: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """将嵌套 dict 展平为 {path: value} 形式

    示例：
      {"http": {"timeout": 30}} → {"http.timeout": 30}
    """
    result: Dict[str, Any] = {}
    for key, value in config.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_flatten_config(value, path))
        else:
            result[path] = value
    return result


def load_snapshot(snapshot_path: str) -> Dict[str, Any]:
    """加载快照文件"""
    with open(snapshot_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_current_config() -> Dict[str, Any]:
    """获取当前运行时配置（展平为 {path: value} 形式）"""
    from agent.monitoring.observability_config import (
        get_observability_config,
        reset_observability_config,
    )

    # 重置以读取默认值，避免被同进程中其他测试用例污染
    reset_observability_config()
    config = get_observability_config()
    return _flatten_config(config.get_all())


def _classify_severity(drift_type: str, path: str) -> str:
    """根据漂移类型和路径分类严重等级

    Returns:
        critical / high / medium / low
    """
    if drift_type == "removed":
        return "critical"
    if drift_type == "modified":
        # 关键基础设施配置（HTTP/缓存/调度器）的修改视为 high
        critical_prefixes = (
            "http.",
            "cache.",
            "scheduler.",
            "tracing_cache.",
        )
        if any(path.startswith(p) for p in critical_prefixes):
            return "high"
        return "medium"
    if drift_type == "added":
        return "low"
    return "low"


def detect_drift(
    snapshot: Dict[str, Any],
    current: Dict[str, Any],
    metadata: Dict[str, Any] = None,
) -> List[Dict[str, Any]]:
    """检测配置漂移

    Args:
        snapshot: 快照配置（展平后的 {path: value}）
        current: 当前运行时配置（展平后的 {path: value}）
        metadata: 可选，配置项元信息（用于生成可读报告）

    Returns:
        漂移列表，每项包含 path/type/severity/snapshot_value/current_value/description/suggestion
    """
    drifts: List[Dict[str, Any]] = []
    metadata = metadata or {}

    # 检测 modified 和 removed
    for path, snapshot_value in snapshot.items():
        if path not in current:
            drifts.append({
                "path": path,
                "type": "removed",
                "severity": _classify_severity("removed", path),
                "snapshot_value": snapshot_value,
                "current_value": None,
                "description": metadata.get(path, {}).get("description", "(无描述)"),
                "suggestion": (
                    f"配置项 {path} 在运行时缺失，请检查 observability_config.py"
                    f" 是否已移除该 ValidationRule"
                ),
            })
        elif current[path] != snapshot_value:
            drifts.append({
                "path": path,
                "type": "modified",
                "severity": _classify_severity("modified", path),
                "snapshot_value": snapshot_value,
                "current_value": current[path],
                "description": metadata.get(path, {}).get("description", "(无描述)"),
                "suggestion": (
                    "如需修改默认值，请更新 observability_config.py 的"
                    " ValidationRule.default 并重新生成快照"
                ),
            })

    # 检测 added
    for path, current_value in current.items():
        if path not in snapshot:
            drifts.append({
                "path": path,
                "type": "added",
                "severity": _classify_severity("added", path),
                "snapshot_value": None,
                "current_value": current_value,
                "description": "(新增配置项，未在快照中)",
                "suggestion": "如为新增配置项，请添加 ValidationRule 并重新生成快照",
            })

    # 按严重等级排序：critical > high > medium > low
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    drifts.sort(key=lambda d: (severity_order.get(d["severity"], 99), d["path"]))
    return drifts


def print_console_report(snapshot: Dict[str, Any], drifts: List[Dict[str, Any]]):
    """打印控制台友好的漂移报告"""
    print("\n" + "=" * 70)
    print("  配置漂移检测报告")
    print("=" * 70)
    print(f"  快照源:       {snapshot.get('generated_from', 'unknown')}")
    print(f"  快照生成时间: {snapshot.get('generated_at', 'unknown')}")
    print(f"  配置项总数:   {snapshot.get('total_paths', 0)}")
    print(f"  漂移数量:     {len(drifts)}")
    print("-" * 70)

    if not drifts:
        print("  ✓ 无漂移检测到，运行时配置与快照一致")
        print("=" * 70)
        return

    # 按类型分组统计
    by_type = {"modified": 0, "removed": 0, "added": 0}
    for d in drifts:
        by_type[d["type"]] += 1
    print(
        f"  modified: {by_type['modified']}  "
        f"removed: {by_type['removed']}  added: {by_type['added']}"
    )
    print("-" * 70)

    severity_icon = {
        "critical": "[C]",
        "high": "[H]",
        "medium": "[M]",
        "low": "[L]",
    }
    for d in drifts:
        icon = severity_icon.get(d["severity"], "[?]")
        print(f"\n  {icon} [{d['severity'].upper()}] {d['path']} ({d['type']})")
        print(f"      描述: {d['description']}")
        if d["type"] == "modified":
            print(f"      快照值: {d['snapshot_value']}")
            print(f"      当前值: {d['current_value']}")
        elif d["type"] == "removed":
            print(f"      快照值: {d['snapshot_value']} (运行时已移除)")
        elif d["type"] == "added":
            print(f"      当前值: {d['current_value']} (快照中不存在)")
        print(f"      建议: {d['suggestion']}")

    print("\n" + "=" * 70)


def build_report(
    snapshot_data: Dict[str, Any],
    snapshot_path: str,
    drifts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """构造 JSON 报告 dict（设计文档 3.2 节格式）"""
    return {
        "version": "1.0",
        "scan_at": datetime.now().isoformat(timespec="seconds"),
        "snapshot_source": snapshot_path,
        "snapshot_generated_at": snapshot_data.get("generated_at"),
        "current_config_source": "runtime",
        "summary": {
            "total_paths": snapshot_data.get("total_paths", 0),
            "drift_count": len(drifts),
            "modified": sum(1 for d in drifts if d["type"] == "modified"),
            "removed": sum(1 for d in drifts if d["type"] == "removed"),
            "added": sum(1 for d in drifts if d["type"] == "added"),
        },
        "drifts": drifts,
    }


def main():
    parser = argparse.ArgumentParser(description="配置漂移检测工具")
    parser.add_argument(
        "--snapshot",
        "-s",
        default="docs/observability/config_snapshot_master.json",
        help="快照文件路径（默认: docs/observability/config_snapshot_master.json）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式报告（默认: 控制台文本）",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="输出文件路径（仅 --json 模式有效，默认输出到 stdout）",
    )
    parser.add_argument(
        "--fail-on-drift",
        action="store_true",
        help="检测到 high/critical 漂移时以非零退出码退出（CI 模式）",
    )
    args = parser.parse_args()

    # 加载快照
    if not Path(args.snapshot).exists():
        print(f"错误: 快照文件不存在: {args.snapshot}", file=sys.stderr)
        print(
            f"请先运行: python scripts/config_snapshot.py --output {args.snapshot}",
            file=sys.stderr,
        )
        sys.exit(2)

    snapshot_data = load_snapshot(args.snapshot)
    snapshot_config = _flatten_config(snapshot_data["config"])
    metadata = snapshot_data.get("metadata", {})

    # 获取当前运行时配置
    current_config = get_current_config()

    # 检测漂移
    drifts = detect_drift(snapshot_config, current_config, metadata)

    # 输出报告
    if args.json:
        report = build_report(snapshot_data, args.snapshot, drifts)
        report_json = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(report_json)
            print(f"✓ 漂移报告已写入: {args.output}", file=sys.stderr)
        else:
            print(report_json)
    else:
        print_console_report(snapshot_data, drifts)

    # CI 阻断模式
    if args.fail_on_drift:
        critical_count = sum(1 for d in drifts if d["severity"] == "critical")
        high_count = sum(1 for d in drifts if d["severity"] == "high")
        if critical_count > 0 or high_count > 0:
            print(
                f"\n错误: 检测到 {critical_count} 个 critical + {high_count} 个 high 漂移",
                file=sys.stderr,
            )
            sys.exit(1)


if __name__ == "__main__":
    main()
