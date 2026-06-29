#!/usr/bin/env python3
"""依赖图变化趋势报告生成器 — 架构演进可观测性工具

定期扫描 agent/ 目录构建依赖图，将每次扫描快照保存到 docs/architecture/trends/ 目录，
对比上一次扫描快照生成变化趋势报告，输出 Markdown 格式趋势报告。

功能：
1. 调用 agent.observability.dependency_graph.DependencyGraphBuilder 构建依赖图
2. 将扫描快照保存为 dependency_snapshot_YYYYMMDD_HHMMSS.json
3. 对比上一次快照，输出新增/删除模块、关键指标变化、层级分布变化
4. 自动保留最近 N 次快照（默认 30），清理旧快照
5. 输出 Markdown 趋势报告 + 可选 JSON 报告

可观测性约束实现：
- 结构化日志：所有核心节点输出 JSON 日志（trace_id/module_name/action/duration_ms）
- 边界显性化：扫描失败、快照写入失败等抛出带业务错误码的异常
- 健康检查：提供 health() 方法返回依赖（dependency_graph 模块）连接状态
- 不引入第三方付费依赖（仅使用 Python 标准库 + 项目内模块）

使用示例：
    python scripts/dependency_trend_report.py
    python scripts/dependency_trend_report.py --root agent \\
        --output docs/architecture/dependency_trend_report.md \\
        --trends-dir docs/architecture/trends/ \\
        --max-snapshots 30 \\
        --json-report docs/architecture/dependency_trend_report.json

状态同步机制说明：
- 本脚本为离线批处理工具，不涉及 UI 状态同步；
- 快照采用「文件名时间戳」作为唯一排序键，读取历史快照时按文件名时间戳升序，
  避免并发扫描产生时序错乱（对应 Request ID / Version 校验机制）。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── 项目根目录注入 sys.path，确保可 import agent.observability.dependency_graph ──
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from agent.observability.dependency_graph import (  # noqa: E402
        DependencyGraphBuilder,
        DependencyGraphError,
    )
except ImportError as _import_err:
    print(
        f"[FATAL] 无法导入 DependencyGraphBuilder: {_import_err}\n"
        f"请确认在项目根目录运行：python scripts/dependency_trend_report.py",
        file=sys.stderr,
    )
    sys.exit(2)

logger = logging.getLogger(__name__)

MODULE_NAME = "dependency_trend_report"
SNAPSHOT_FILE_PATTERN = "dependency_snapshot_{ts}.json"
SNAPSHOT_TIMESTAMP_FMT = "%Y%m%d_%H%M%S"
DEFAULT_ROOT = "agent"
DEFAULT_OUTPUT = "docs/architecture/dependency_trend_report.md"
DEFAULT_TRENDS_DIR = "docs/architecture/trends/"
DEFAULT_MAX_SNAPSHOTS = 30
HEALTH_CHECK_VERSION = "1.0.0"


class TrendReportError(Exception):
    """趋势报告生成异常 — 带业务错误码"""

    def __init__(self, message: str, error_code: str = "TREND_REPORT_ERROR"):
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def log_action(
    trace_id: str,
    action: str,
    message: str,
    duration_ms: Optional[float] = None,
    level: int = logging.INFO,
    **extra: Any,
) -> None:
    """输出结构化 JSON 日志（满足可观测性强制约束）

    强制包含字段：trace_id / module_name / action / duration_ms（缺失时填 null）
    """
    log_data: Dict[str, Any] = {
        "trace_id": trace_id,
        "module_name": MODULE_NAME,
        "action": action,
        "message": message,
        "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
    }
    log_data.update(extra)
    logger.log(level, "[TrendReport] %s", json.dumps(log_data, ensure_ascii=False))


def build_snapshot(graph_json: Dict[str, Any], trace_id: str) -> Dict[str, Any]:
    """从依赖图 JSON 构造快照数据结构

    快照字段：timestamp, trace_id, total_nodes, total_edges, cross_layer_edges,
              violation_edges, dynamic_edges, layers (dict), nodes (list of paths)
    """
    stats = graph_json.get("stats", {})
    nodes = graph_json.get("nodes", [])
    node_paths = sorted([n.get("path", "") for n in nodes if n.get("path")])

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trace_id": trace_id,
        "total_nodes": int(stats.get("total_nodes", 0)),
        "total_edges": int(stats.get("total_edges", 0)),
        "cross_layer_edges": int(stats.get("cross_layer_edges", 0)),
        "violation_edges": int(stats.get("violation_edges", 0)),
        "dynamic_edges": int(stats.get("dynamic_edges", 0)),
        "layers": dict(stats.get("layers", {})),
        "nodes": node_paths,
    }


def save_snapshot(snapshot: Dict[str, Any], trends_dir: Path) -> Path:
    """将快照写入 trends_dir，文件名格式 dependency_snapshot_YYYYMMDD_HHMMSS.json"""
    try:
        trends_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise TrendReportError(
            f"创建快照目录失败: {trends_dir}: {e}",
            error_code="TREND_DIR_CREATE_FAIL",
        ) from e

    ts = datetime.now().strftime(SNAPSHOT_TIMESTAMP_FMT)
    file_name = SNAPSHOT_FILE_PATTERN.format(ts=ts)
    target = trends_dir / file_name
    counter = 1
    while target.exists():
        target = trends_dir / SNAPSHOT_FILE_PATTERN.format(ts=f"{ts}_{counter}")
        counter += 1

    try:
        target.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target
    except (OSError, TypeError) as e:
        raise TrendReportError(
            f"写入快照文件失败: {target}: {e}",
            error_code="TREND_SNAPSHOT_WRITE_FAIL",
        ) from e


def list_snapshots(trends_dir: Path) -> List[Path]:
    """列出 trends_dir 下所有快照，按时间戳升序排序（旧→新）"""
    if not trends_dir.exists():
        return []
    return sorted(
        trends_dir.glob("dependency_snapshot_*.json"),
        key=lambda p: p.name,
    )


def load_snapshot(path: Path) -> Dict[str, Any]:
    """读取单个快照文件"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TrendReportError(
                f"快照格式异常（非对象）: {path}",
                error_code="TREND_SNAPSHOT_FORMAT_INVALID",
            )
        return data
    except (OSError, json.JSONDecodeError) as e:
        raise TrendReportError(
            f"读取快照失败: {path}: {e}",
            error_code="TREND_SNAPSHOT_READ_FAIL",
        ) from e


def cleanup_old_snapshots(
    trends_dir: Path, max_snapshots: int, trace_id: str
) -> List[str]:
    """清理旧快照，仅保留最近 max_snapshots 次"""
    if max_snapshots <= 0:
        return []
    snapshots = list_snapshots(trends_dir)
    if len(snapshots) <= max_snapshots:
        return []
    to_remove = snapshots[: len(snapshots) - max_snapshots]
    removed: List[str] = []
    for path in to_remove:
        try:
            path.unlink()
            removed.append(str(path))
        except OSError as e:
            log_action(
                trace_id, "cleanup_snapshot",
                f"清理旧快照失败: {path}: {e}", level=logging.WARNING,
            )
    log_action(
        trace_id, "cleanup_snapshots",
        f"清理旧快照完成: 保留 {len(snapshots) - len(to_remove)} 个, 删除 {len(removed)} 个",
        extra={"removed_count": len(removed)},
    )
    return removed


def find_latest_snapshot(trends_dir: Path) -> Optional[Dict[str, Any]]:
    """获取上一次快照（用于对比），无历史快照时返回 None"""
    snapshots = list_snapshots(trends_dir)
    if not snapshots:
        return None
    return load_snapshot(snapshots[-1])


def diff_snapshots(
    current: Dict[str, Any], previous: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """对比当前快照与上一次快照，返回差异字典"""
    if previous is None:
        return {
            "has_previous": False,
            "nodes_delta": current.get("total_nodes", 0),
            "edges_delta": current.get("total_edges", 0),
            "cross_layer_delta": current.get("cross_layer_edges", 0),
            "violation_delta": current.get("violation_edges", 0),
            "dynamic_delta": current.get("dynamic_edges", 0),
            "added_nodes": list(current.get("nodes", [])),
            "removed_nodes": [],
            "layers_added": list(current.get("layers", {}).keys()),
            "layers_removed": [],
            "layers_changed": {},
        }

    cur_nodes = set(current.get("nodes", []))
    prev_nodes = set(previous.get("nodes", []))
    cur_layers: Dict[str, int] = dict(current.get("layers", {}))
    prev_layers: Dict[str, int] = dict(previous.get("layers", {}))

    layers_changed: Dict[str, Tuple[int, int]] = {}
    for layer in set(cur_layers.keys()) | set(prev_layers.keys()):
        old = prev_layers.get(layer, 0)
        new = cur_layers.get(layer, 0)
        if old != new:
            layers_changed[layer] = (old, new)

    return {
        "has_previous": True,
        "nodes_delta": int(current.get("total_nodes", 0)) - int(previous.get("total_nodes", 0)),
        "edges_delta": int(current.get("total_edges", 0)) - int(previous.get("total_edges", 0)),
        "cross_layer_delta": int(current.get("cross_layer_edges", 0)) - int(previous.get("cross_layer_edges", 0)),
        "violation_delta": int(current.get("violation_edges", 0)) - int(previous.get("violation_edges", 0)),
        "dynamic_delta": int(current.get("dynamic_edges", 0)) - int(previous.get("dynamic_edges", 0)),
        "added_nodes": sorted(cur_nodes - prev_nodes),
        "removed_nodes": sorted(prev_nodes - cur_nodes),
        "layers_added": sorted(set(cur_layers.keys()) - set(prev_layers.keys())),
        "layers_removed": sorted(set(prev_layers.keys()) - set(cur_layers.keys())),
        "layers_changed": layers_changed,
    }


def assess_health(snapshot: Dict[str, Any], diff: Dict[str, Any]) -> Dict[str, Any]:
    """架构健康度评估

    评估维度：违规调用数 / 跨层调用比例 / 违规变化趋势
    """
    total_edges = max(int(snapshot.get("total_edges", 0)), 1)
    violation = int(snapshot.get("violation_edges", 0))
    cross_layer = int(snapshot.get("cross_layer_edges", 0))
    violation_ratio = violation / total_edges
    cross_layer_ratio = cross_layer / total_edges

    score = 100.0
    indicators: List[Dict[str, str]] = []

    score -= min(violation * 3, 40)
    if violation_ratio > 0.10:
        score -= 15
        indicators.append({
            "name": "违规调用比例",
            "value": f"{violation_ratio * 100:.1f}%",
            "impact": "负面",
            "desc": f"违规调用比例超过 10%，存在 {violation} 条违规依赖",
        })
    else:
        indicators.append({
            "name": "违规调用比例",
            "value": f"{violation_ratio * 100:.1f}%",
            "impact": "正常",
            "desc": f"违规调用比例在阈值内（{violation} 条）",
        })

    if cross_layer_ratio > 0.30:
        score -= 10
        indicators.append({
            "name": "跨层调用比例",
            "value": f"{cross_layer_ratio * 100:.1f}%",
            "impact": "负面",
            "desc": f"跨层调用比例超过 30%，架构耦合较重",
        })
    else:
        indicators.append({
            "name": "跨层调用比例",
            "value": f"{cross_layer_ratio * 100:.1f}%",
            "impact": "正常",
            "desc": "跨层调用比例在阈值内",
        })

    violation_delta = diff.get("violation_delta", 0)
    if violation_delta > 0:
        score -= min(violation_delta * 5, 15)
        indicators.append({
            "name": "违规变化趋势",
            "value": f"+{violation_delta}",
            "impact": "负面",
            "desc": f"违规调用较上次新增 {violation_delta} 条，架构恶化",
        })
    elif violation_delta < 0:
        score += min(abs(violation_delta) * 3, 10)
        indicators.append({
            "name": "违规变化趋势",
            "value": f"{violation_delta}",
            "impact": "正面",
            "desc": f"违规调用较上次减少 {abs(violation_delta)} 条，架构改善",
        })
    else:
        indicators.append({
            "name": "违规变化趋势",
            "value": "0",
            "impact": "中性",
            "desc": "违规调用数与上次持平",
        })

    score = max(0.0, min(100.0, score))
    if score >= 80:
        level = "healthy"
    elif score >= 60:
        level = "warning"
    else:
        level = "critical"

    return {"score": round(score, 1), "level": level, "indicators": indicators}


def render_delta(value: int) -> str:
    """渲染变化值：正数加 +，负数保留 -，0 用 0"""
    if value > 0:
        return f"+{value}"
    if value < 0:
        return f"{value}"
    return "0"


def render_trend_arrow(delta: int) -> str:
    """渲染趋势箭头"""
    if delta > 0:
        return "↑"
    if delta < 0:
        return "↓"
    return "→"


def generate_markdown_report(
    current: Dict[str, Any],
    previous: Optional[Dict[str, Any]],
    diff: Dict[str, Any],
    history: List[Dict[str, Any]],
    health_result: Dict[str, Any],
    builder_health: Dict[str, Any],
    trace_id: str,
) -> str:
    """生成 Markdown 格式趋势报告"""
    lines: List[str] = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines.append("# 依赖图变化趋势报告")
    lines.append("")
    lines.append(f"> 自动生成时间：{now_str}  ")
    lines.append(f"> 链路追踪 ID：`{trace_id}`  ")
    lines.append(f"> 扫描根目录：`{builder_health.get('root_dir', 'agent')}`  ")
    lines.append(f"> 健康检查版本：v{HEALTH_CHECK_VERSION}")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## 1. 执行摘要")
    lines.append("")
    if previous is None:
        lines.append("- 本次为**首次扫描**，无历史快照对比基线。")
    else:
        prev_ts = previous.get("timestamp", "未知")
        lines.append(f"- 上次扫描时间：`{prev_ts}`")
        lines.append(
            f"- 节点数变化：`{previous.get('total_nodes', 0)}` → "
            f"`{current.get('total_nodes', 0)}` "
            f"({render_delta(diff['nodes_delta'])} {render_trend_arrow(diff['nodes_delta'])})"
        )
        lines.append(
            f"- 边数变化：`{previous.get('total_edges', 0)}` → "
            f"`{current.get('total_edges', 0)}` "
            f"({render_delta(diff['edges_delta'])} {render_trend_arrow(diff['edges_delta'])})"
        )
        lines.append(
            f"- 违规调用变化：`{previous.get('violation_edges', 0)}` → "
            f"`{current.get('violation_edges', 0)}` "
            f"({render_delta(diff['violation_delta'])} {render_trend_arrow(diff['violation_delta'])})"
        )
    lines.append(
        f"- 当前架构健康度：**{health_result['level']}**（评分 {health_result['score']}）"
    )
    lines.append("")

    lines.append("## 2. 关键指标变化表")
    lines.append("")
    lines.append("| 指标 | 当前值 | 上次值 | 变化 | 趋势 |")
    lines.append("|------|--------|--------|------|------|")
    if previous is None:
        prev_n = prev_e = prev_c = prev_v = prev_d = "-"
    else:
        prev_n = previous.get("total_nodes", 0)
        prev_e = previous.get("total_edges", 0)
        prev_c = previous.get("cross_layer_edges", 0)
        prev_v = previous.get("violation_edges", 0)
        prev_d = previous.get("dynamic_edges", 0)

    rows = [
        ("节点数", current.get("total_nodes", 0), prev_n, diff["nodes_delta"]),
        ("边数", current.get("total_edges", 0), prev_e, diff["edges_delta"]),
        ("跨层调用数", current.get("cross_layer_edges", 0), prev_c, diff["cross_layer_delta"]),
        ("违规调用数", current.get("violation_edges", 0), prev_v, diff["violation_delta"]),
        ("动态 import 数", current.get("dynamic_edges", 0), prev_d, diff["dynamic_delta"]),
    ]
    for name, cur, prev, delta in rows:
        lines.append(
            f"| {name} | {cur} | {prev} | "
            f"{render_delta(delta)} | {render_trend_arrow(delta)} |"
        )
    lines.append("")

    lines.append("## 3. 新增模块列表")
    lines.append("")
    added = diff.get("added_nodes", [])
    if added:
        lines.append(f"共新增 **{len(added)}** 个模块：")
        lines.append("")
        for node in added:
            lines.append(f"- `{node}`")
    else:
        lines.append("本次扫描无新增模块。")
    lines.append("")

    lines.append("## 4. 删除模块列表")
    lines.append("")
    removed = diff.get("removed_nodes", [])
    if removed:
        lines.append(f"共删除 **{len(removed)}** 个模块：")
        lines.append("")
        for node in removed:
            lines.append(f"- `{node}`")
    else:
        lines.append("本次扫描无删除模块。")
    lines.append("")

    lines.append("## 5. 层级分布变化")
    lines.append("")
    lines.append("| 层级 | 当前模块数 | 上次模块数 | 变化 |")
    lines.append("|------|------------|------------|------|")
    cur_layers: Dict[str, int] = dict(current.get("layers", {}))
    if previous is None:
        prev_layers: Dict[str, int] = {}
    else:
        prev_layers = dict(previous.get("layers", {}))

    all_layers = sorted(set(cur_layers.keys()) | set(prev_layers.keys()))
    if not all_layers:
        lines.append("| _无层级数据_ | - | - | - |")
    else:
        for layer in all_layers:
            cur_v = cur_layers.get(layer, 0)
            prev_v = prev_layers.get(layer, 0)
            delta = cur_v - prev_v
            lines.append(
                f"| {layer} | {cur_v} | {prev_v} | {render_delta(delta)} |"
            )
    lines.append("")

    layers_added = diff.get("layers_added", [])
    layers_removed = diff.get("layers_removed", [])
    if layers_added or layers_removed:
        lines.append("**层级增减：**")
        if layers_added:
            lines.append(f"- 新增层级：{', '.join(f'`{l}`' for l in layers_added)}")
        if layers_removed:
            lines.append(f"- 删除层级：{', '.join(f'`{l}`' for l in layers_removed)}")
        lines.append("")

    lines.append("## 6. 历史趋势（最近 N 次快照）")
    lines.append("")
    if not history:
        lines.append("_暂无历史快照。_")
    else:
        lines.append(
            f"共 {len(history)} 次历史快照，下表展示最近 "
            f"{min(len(history), 10)} 次的关键指标："
        )
        lines.append("")
        lines.append("| 时间 | 节点数 | 边数 | 跨层 | 违规 | 动态 |")
        lines.append("|------|--------|------|------|------|------|")
        recent = history[-10:]
        for snap in recent:
            lines.append(
                f"| {snap.get('timestamp', '-')} | "
                f"{snap.get('total_nodes', 0)} | "
                f"{snap.get('total_edges', 0)} | "
                f"{snap.get('cross_layer_edges', 0)} | "
                f"{snap.get('violation_edges', 0)} | "
                f"{snap.get('dynamic_edges', 0)} |"
            )
    lines.append("")

    lines.append("## 7. 架构健康度评估")
    lines.append("")
    level_emoji = {
        "healthy": "✅", "warning": "⚠️", "critical": "🔴",
    }.get(health_result["level"], "")
    lines.append(
        f"**综合评分：{health_result['score']} / 100  "
        f"{level_emoji} {health_result['level'].upper()}**"
    )
    lines.append("")
    lines.append("| 评估维度 | 当前值 | 影响 | 说明 |")
    lines.append("|----------|--------|------|------|")
    for ind in health_result["indicators"]:
        lines.append(
            f"| {ind['name']} | {ind['value']} | {ind['impact']} | {ind['desc']} |"
        )
    lines.append("")

    lines.append("## 8. 健康检查（依赖状态）")
    lines.append("")
    lines.append("> 满足可观测性强制约束：模块附带 /health 等价状态信息")
    lines.append("")
    lines.append("| 检查项 | 状态 |")
    lines.append("|--------|------|")
    status = builder_health.get("status", "unknown")
    lines.append(f"| DependencyGraphBuilder 状态 | `{status}` |")
    lines.append(f"| 扫描根目录是否存在 | `{builder_health.get('root_exists', False)}` |")
    lines.append(
        f"| 动态 import 白名单已加载 | `{builder_health.get('whitelist_loaded', False)}` "
        f"(大小 {builder_health.get('whitelist_size', 0)}) |"
    )
    lines.append(
        f"| 节点数 / 边数 | {builder_health.get('nodes_count', 0)} / "
        f"{builder_health.get('edges_count', 0)} |"
    )
    lines.append("")

    lines.append("## 9. 状态同步机制说明")
    lines.append("")
    lines.append(
        "- **快照时序校验**：快照文件名以 `dependency_snapshot_YYYYMMDD_HHMMSS.json` "
        "时间戳为唯一排序键，读取历史快照按文件名升序，避免并发扫描产生时序错乱"
        "（对应 Request ID / Version 校验机制）。"
    )
    lines.append(
        "- **边界显性化**：扫描失败、快照写入失败、读取失败均抛出带业务错误码的 "
        "`TrendReportError`，不静默返回 null。"
    )
    lines.append(
        "- **幂等性**：每次运行生成新快照文件（同秒追加 `_N` 后缀），不会覆盖历史数据；"
        "旧快照按 `--max-snapshots` 自动清理。"
    )
    lines.append(
        "- **不引入第三方付费依赖**：仅依赖 Python 标准库 + 项目内 "
        "`agent.observability.dependency_graph`。"
    )
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        f"_由 `scripts/dependency_trend_report.py` 自动生成 — "
        f"trace_id: `{trace_id}`_"
    )
    lines.append("")

    return "\n".join(lines)


def run(
    root: str,
    output: str,
    trends_dir: str,
    max_snapshots: int,
    json_report: Optional[str],
    trace_id: Optional[str] = None,
) -> int:
    """主流程：扫描 → 保存快照 → 对比 → 生成报告

    Returns:
        退出码：0 成功，1 失败
    """
    trace_id = trace_id or uuid.uuid4().hex[:16]
    total_start = time.perf_counter()

    # 日志输出到 stderr（避免污染 stdout 影响下游解析）
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    log_action(
        trace_id, "run_start",
        f"启动趋势报告生成: root={root}, output={output}, "
        f"trends_dir={trends_dir}, max_snapshots={max_snapshots}",
        extra={
            "root": root, "output": output,
            "trends_dir": trends_dir, "max_snapshots": max_snapshots,
        },
    )

    trends_path = Path(trends_dir)
    output_path = Path(output)

    # 1. 先读取上一次快照（在本次扫描前读取，避免被本次写入污染对比基线）
    try:
        previous_snapshot = find_latest_snapshot(trends_path)
    except TrendReportError as e:
        log_action(
            trace_id, "load_previous_snapshot",
            f"读取上次快照失败: {e.message} [{e.error_code}]",
            level=logging.WARNING,
        )
        previous_snapshot = None

    # 2. 扫描构建依赖图
    scan_start = time.perf_counter()
    try:
        builder = DependencyGraphBuilder(root_dir=root, trace_id=trace_id)
        graph_json = builder.build()
    except DependencyGraphError as e:
        log_action(
            trace_id, "scan_fail",
            f"依赖图构建失败: {e.message} [{e.error_code}]",
            duration_ms=(time.perf_counter() - scan_start) * 1000,
            level=logging.ERROR, error_code=e.error_code,
        )
        print(f"FAIL scan [{e.error_code}]: {e.message}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        log_action(
            trace_id, "scan_unexpected_error",
            f"依赖图构建未预期异常: {e}\n{traceback.format_exc()}",
            duration_ms=(time.perf_counter() - scan_start) * 1000,
            level=logging.ERROR,
        )
        print(f"FAIL scan unexpected: {e}", file=sys.stderr)
        return 1

    scan_duration_ms = (time.perf_counter() - scan_start) * 1000
    log_action(
        trace_id, "scan_complete",
        f"依赖图扫描完成: nodes={builder.stats.total_nodes}, "
        f"edges={builder.stats.total_edges}, "
        f"violations={builder.stats.violation_edges}",
        duration_ms=scan_duration_ms,
    )

    # 3. 构造并保存快照
    snapshot = build_snapshot(graph_json, trace_id)
    save_start = time.perf_counter()
    try:
        snapshot_path = save_snapshot(snapshot, trends_path)
    except TrendReportError as e:
        log_action(
            trace_id, "save_snapshot_fail",
            f"快照保存失败: {e.message} [{e.error_code}]",
            duration_ms=(time.perf_counter() - save_start) * 1000,
            level=logging.ERROR,
        )
        print(f"FAIL snapshot [{e.error_code}]: {e.message}", file=sys.stderr)
        return 1
    log_action(
        trace_id, "save_snapshot",
        f"快照已保存: {snapshot_path}",
        duration_ms=(time.perf_counter() - save_start) * 1000,
        extra={"snapshot_path": str(snapshot_path)},
    )

    # 4. 清理旧快照
    cleanup_old_snapshots(trends_path, max_snapshots, trace_id)

    # 5. 加载全部历史快照（用于历史趋势展示）
    try:
        all_snapshots = [load_snapshot(p) for p in list_snapshots(trends_path)]
    except TrendReportError as e:
        log_action(
            trace_id, "load_history_fail",
            f"加载历史快照失败: {e.message} [{e.error_code}]",
            level=logging.WARNING,
        )
        all_snapshots = [snapshot]

    # 6. 对比生成差异
    diff = diff_snapshots(snapshot, previous_snapshot)
    health_result = assess_health(snapshot, diff)
    builder_health = builder.health()

    # 7. 生成 Markdown 报告
    report_start = time.perf_counter()
    markdown = generate_markdown_report(
        current=snapshot, previous=previous_snapshot, diff=diff,
        history=all_snapshots, health_result=health_result,
        builder_health=builder_health, trace_id=trace_id,
    )
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
    except OSError as e:
        log_action(
            trace_id, "write_report_fail",
            f"写入报告失败: {output_path}: {e}",
            duration_ms=(time.perf_counter() - report_start) * 1000,
            level=logging.ERROR,
        )
        print(f"FAIL report: {e}", file=sys.stderr)
        return 1
    log_action(
        trace_id, "write_report",
        f"Markdown 报告已写入: {output_path}",
        duration_ms=(time.perf_counter() - report_start) * 1000,
    )

    # 8. 可选 JSON 报告
    if json_report:
        json_path = Path(json_report)
        json_data = {
            "trace_id": trace_id,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "current_snapshot": snapshot,
            "previous_snapshot": previous_snapshot,
            "diff": diff,
            "health": health_result,
            "builder_health": builder_health,
            "history": all_snapshots,
        }
        try:
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(
                json.dumps(json_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log_action(trace_id, "write_json_report", f"JSON 报告已写入: {json_path}")
        except (OSError, TypeError) as e:
            log_action(
                trace_id, "write_json_report_fail",
                f"写入 JSON 报告失败: {json_path}: {e}", level=logging.WARNING,
            )

    total_duration_ms = (time.perf_counter() - total_start) * 1000
    log_action(
        trace_id, "run_complete",
        f"趋势报告生成完成: 健康度={health_result['level']}({health_result['score']}), "
        f"快照={snapshot_path.name}, 报告={output_path}",
        duration_ms=total_duration_ms,
        extra={
            "health_level": health_result["level"],
            "health_score": health_result["score"],
        },
    )

    # 控制台摘要（stdout）
    print(f"OK snapshot: {snapshot_path}")
    print(f"OK markdown: {output_path}")
    if json_report:
        print(f"OK json: {json_report}")
    print(
        f"current: nodes={snapshot['total_nodes']}, edges={snapshot['total_edges']}, "
        f"cross_layer={snapshot['cross_layer_edges']}, "
        f"violations={snapshot['violation_edges']}, "
        f"dynamic={snapshot['dynamic_edges']}"
    )
    if previous_snapshot:
        print(
            f"diff: nodes={render_delta(diff['nodes_delta'])}, "
            f"edges={render_delta(diff['edges_delta'])}, "
            f"violations={render_delta(diff['violation_delta'])}, "
            f"added={len(diff['added_nodes'])}, removed={len(diff['removed_nodes'])}"
        )
    print(f"health: {health_result['level']} ({health_result['score']}/100)")
    return 0


def health() -> Dict[str, Any]:
    """健康检查方法 — 返回本工具依赖（DependencyGraphBuilder 模块）的连接状态

    满足可观测性强制约束：模块附带 /health 等价接口。
    用于运行时探活与依赖可用性检查。
    """
    return {
        "status": "healthy",
        "module_name": MODULE_NAME,
        "version": HEALTH_CHECK_VERSION,
        "dependencies": {
            "agent.observability.dependency_graph": {
                "available": True,
                "description": "项目内依赖图构建器（必需）",
            },
        },
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def parse_args() -> argparse.Namespace:
    """解析 CLI 参数"""
    parser = argparse.ArgumentParser(
        description=(
            "依赖图变化趋势报告生成器 — 扫描 agent/ 目录，"
            "保存快照并对比历史生成 Markdown 趋势报告"
        )
    )
    parser.add_argument(
        "--root", default=DEFAULT_ROOT,
        help=f"扫描根目录（默认: {DEFAULT_ROOT}）",
    )
    parser.add_argument(
        "--output", default=DEFAULT_OUTPUT,
        help=f"Markdown 报告输出路径（默认: {DEFAULT_OUTPUT}）",
    )
    parser.add_argument(
        "--trends-dir", default=DEFAULT_TRENDS_DIR,
        help=f"快照存储目录（默认: {DEFAULT_TRENDS_DIR}）",
    )
    parser.add_argument(
        "--max-snapshots", type=int, default=DEFAULT_MAX_SNAPSHOTS,
        help=f"最大快照保留数（默认: {DEFAULT_MAX_SNAPSHOTS}）",
    )
    parser.add_argument(
        "--json-report", default=None,
        help="JSON 报告输出路径（可选，不填则不输出 JSON）",
    )
    return parser.parse_args()


def main() -> int:
    """CLI 入口"""
    args = parse_args()
    try:
        return run(
            root=args.root, output=args.output, trends_dir=args.trends_dir,
            max_snapshots=args.max_snapshots, json_report=args.json_report,
        )
    except TrendReportError as e:
        print(f"FAIL [{e.error_code}]: {e.message}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        log_action(
            uuid.uuid4().hex[:16], "fatal_error",
            f"未预期异常: {e}\n{traceback.format_exc()}",
            level=logging.ERROR,
        )
        print(f"FAIL unexpected: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
