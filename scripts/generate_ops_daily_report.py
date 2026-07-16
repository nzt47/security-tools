#!/usr/bin/env python
"""TLM 熔断器与向量层运维日报生成器

定期拉取应用日志，聚合统计熔断器相关事件，生成 Markdown 运维日报。
重点关注 vec.circuit_break（熔断触发）和 vec.write_exhausted（写入耗尽）。

日志格式（由 holographic_adapter.py 的 log_dict 产生）:
    2026-07-17 10:30:00 [   INFO] holographic_adapter: {"module_name": "...", "action": "vec.circuit_reset", "msg": "..."}

使用方式:
    # 分析今天的日志
    python scripts/generate_ops_daily_report.py --log-file logs/agent.log

    # 分析指定日期的日志
    python scripts/generate_ops_daily_report.py --log-file logs/agent.log --date 2026-07-17

    # 分析多个日志文件
    python scripts/generate_ops_daily_report.py --log-dir logs/

    # 从 stdin 读取
    cat logs/agent.log | python scripts/generate_ops_daily_report.py --stdin

    # 输出到指定文件
    python scripts/generate_ops_daily_report.py --log-file logs/agent.log --output docs/ops_daily/2026-07-17.md

定期执行（cron 示例）:
    # 每天凌晨 1 点生成昨天的日报
    0 1 * * * cd /path/to/agent && python scripts/generate_ops_daily_report.py --log-file logs/agent.log --date $(date -d 'yesterday' +%Y-%m-%d) --output docs/ops_daily/$(date -d 'yesterday' +%Y-%m-%d).md
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, date
from pathlib import Path

# [不易] 时区一致性：日报"生成时间"必须用 Asia/Shanghai 时区，
# 否则容器内（默认 UTC）与日志事件时间（北京时间）相差 8 小时造成视觉混淆。
# Python 3.9+ 内置 zoneinfo，无需 apt-get install tzdata；
# zoneinfo 在缺 tzdata 环境下可能抛 KeyError，try-except 兜底回退到本地时间。
try:
    from zoneinfo import ZoneInfo
    _SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    _SHANGHAI_TZ = None


def _now_shanghai() -> datetime:
    """获取 Asia/Shanghai 当前时间（兜底返回本地时间）"""
    if _SHANGHAI_TZ is not None:
        return datetime.now(_SHANGHAI_TZ)
    return datetime.now()


# ──────────────────────────────────────────────────────────────
# 关注的 action 清单（与代码 log_dict 一致）
# ──────────────────────────────────────────────────────────────

FOCUS_ACTIONS = {
    "vec.circuit_break": {
        "severity": "P0",
        "label": "熔断触发",
        "description": "连续失败达阈值（5 次），向量层自动降级",
    },
    "vec.write_exhausted": {
        "severity": "P1",
        "label": "写入重试耗尽",
        "description": "向量写入重试 3 次失败，已写入兜底表",
    },
}

ALL_TRACKED_ACTIONS = {
    **FOCUS_ACTIONS,
    "vec.circuit_reset": {
        "severity": "INFO",
        "label": "熔断恢复",
        "description": "_reset_vec_circuit 被调用，向量层恢复可用",
    },
    "vec.fail_count": {
        "severity": "DEBUG",
        "label": "失败计数累积",
        "description": "失败计数递增（未达阈值）",
    },
    "vec.degraded_skip": {
        "severity": "DEBUG",
        "label": "降级路径触发",
        "description": "search_vector 走降级路径返回 []",
    },
    "search_vector.failed": {
        "severity": "WARN",
        "label": "search_vector 单次失败",
        "description": "向量检索抛异常",
    },
    "vec.import_failed": {
        "severity": "P0",
        "label": "sqlite-vec 不可导入",
        "description": "sqlite-vec 未安装或损坏",
    },
    "vec.load_failed": {
        "severity": "P0",
        "label": "扩展加载失败",
        "description": "sqlite-vec 扩展加载全部失败",
    },
    "vec.init_failed": {
        "severity": "P0",
        "label": "向量表初始化失败",
        "description": "memories_vec 表初始化异常",
    },
}

# 日志行解析正则：时间 [级别] 模块: {JSON}
# 注意: 级别方括号内可能有前导空格对齐（如 "[   INFO]"），需 \s* 容忍
LOG_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,\d]*)"
    r"\s+\[\s*(?P<level>\w+)\s*\]"
    r"\s+(?P<logger>[\w.]+):"
    r"\s+(?P<msg>.*)$"
)


# ──────────────────────────────────────────────────────────────
# 日志解析
# ──────────────────────────────────────────────────────────────

def parse_log_line(line):
    """解析单行日志，返回 (timestamp, level, action, msg) 或 None"""
    line = line.strip()
    if not line:
        return None

    match = LOG_PATTERN.match(line)
    if not match:
        return None

    msg_field = match.group("msg")
    # 尝试解析 JSON 部分（log_dict 产生）
    json_start = msg_field.find("{")
    if json_start == -1:
        return None

    json_str = msg_field[json_start:]
    try:
        payload = json.loads(json_str)
    except json.JSONDecodeError:
        return None

    action = payload.get("action", "")
    if not action:
        return None

    return {
        "timestamp": match.group("timestamp"),
        "level": match.group("level"),
        "logger": match.group("logger"),
        "action": action,
        "msg": payload.get("msg", ""),
        "raw": payload,
    }


def filter_by_date(events, target_date):
    """过滤指定日期的事件"""
    if not target_date:
        return events
    date_prefix = target_date  # "2026-07-17"
    return [e for e in events if e["timestamp"].startswith(date_prefix)]


def scan_log_file(file_path):
    """扫描日志文件，返回事件列表"""
    events = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                event = parse_log_line(line)
                if event and event["action"] in ALL_TRACKED_ACTIONS:
                    events.append(event)
    except Exception as e:
        print(f"[WARN] 读取日志文件失败 {file_path}: {e}", file=sys.stderr)
    return events


def scan_log_dir(dir_path):
    """扫描目录下所有 .log 文件"""
    events = []
    for entry in Path(dir_path).iterdir():
        if entry.is_file() and entry.suffix in (".log", ".txt"):
            events.extend(scan_log_file(str(entry)))
    return events


# ──────────────────────────────────────────────────────────────
# 报告生成
# ──────────────────────────────────────────────────────────────

def generate_report(events, target_date_str, report_date):
    """生成 Markdown 运维日报"""
    action_counter = Counter(e["action"] for e in events)

    # 按小时聚合重点 action
    hourly = defaultdict(lambda: defaultdict(int))
    for e in events:
        if e["action"] in FOCUS_ACTIONS:
            # 提取小时 "2026-07-17 10:30:00" → "10"
            ts = e["timestamp"]
            hour_match = re.search(r"\s+(\d{2}):", ts)
            hour = hour_match.group(1) if hour_match else "??"
            hourly[e["action"]][hour] += 1

    # 重点事件详情（最近 10 条）
    focus_events = [e for e in events if e["action"] in FOCUS_ACTIONS]
    recent_focus = focus_events[-10:] if len(focus_events) > 10 else focus_events

    # 健康状态判断
    cb_count = action_counter.get("vec.circuit_break", 0)
    we_count = action_counter.get("vec.write_exhausted", 0)
    reset_count = action_counter.get("vec.circuit_reset", 0)

    if cb_count == 0 and we_count == 0:
        health_status = "🟢 健康"
        health_summary = "无熔断触发，无写入耗尽，向量层运行正常"
    elif cb_count > 0 and reset_count >= cb_count:
        health_status = "🟡 已恢复"
        health_summary = f"熔断触发 {cb_count} 次但已全部恢复（reset {reset_count} 次）"
    elif cb_count > 0:
        health_status = "🔴 异常"
        health_summary = f"熔断触发 {cb_count} 次，仅恢复 {reset_count} 次，需人工介入"
    else:
        health_status = "🟡 警告"
        health_summary = f"无熔断但写入耗尽 {we_count} 次，需检查兜底表补偿"

    # 生成 Markdown
    lines = []
    lines.append(f"# TLM 向量层运维日报 — {report_date}")
    lines.append("")
    lines.append(f"> 生成时间: {_now_shanghai().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 日志日期: {target_date_str or '全部'}")
    lines.append(f"> 事件总数: {len(events)}")
    lines.append("")

    lines.append("## 1. 健康状态")
    lines.append("")
    lines.append(f"**{health_status}** — {health_summary}")
    lines.append("")

    lines.append("## 2. 重点告警")
    lines.append("")
    lines.append("| action | 严重级别 | 次数 | 说明 |")
    lines.append("|--------|---------|------|------|")
    for action, info in FOCUS_ACTIONS.items():
        count = action_counter.get(action, 0)
        marker = "🔴" if count > 0 and info["severity"] == "P0" else "🟡" if count > 0 else "🟢"
        lines.append(f"| `{action}` | {info['severity']} | {count} | {marker} {info['description']} |")
    lines.append("")

    # 重点事件按小时分布
    if any(hourly.values()):
        lines.append("### 2.1 重点事件按小时分布")
        lines.append("")
        all_hours = sorted({h for action_hours in hourly.values() for h in action_hours})
        if all_hours:
            lines.append("| 小时 | " + " | ".join(
                f"{FOCUS_ACTIONS[a]['label']}" for a in FOCUS_ACTIONS
            ) + " |")
            lines.append("|------|" + "|".join(["------"] * len(FOCUS_ACTIONS)) + "|")
            for hour in all_hours:
                row = [hour]
                for action in FOCUS_ACTIONS:
                    row.append(str(hourly[action].get(hour, 0)))
                lines.append("| " + " | ".join(row) + " |")
            lines.append("")

    # 重点事件详情
    if recent_focus:
        lines.append("### 2.2 重点事件详情（最近 10 条）")
        lines.append("")
        lines.append("| 时间 | action | 消息 |")
        lines.append("|------|--------|------|")
        for e in recent_focus:
            msg_short = e["msg"][:80] + "..." if len(e["msg"]) > 80 else e["msg"]
            msg_short = msg_short.replace("|", "\\|")
            lines.append(f"| {e['timestamp']} | `{e['action']}` | {msg_short} |")
        lines.append("")

    lines.append("## 3. 全部事件统计")
    lines.append("")
    lines.append("| action | 级别 | 次数 | 标签 |")
    lines.append("|--------|------|------|------|")
    for action, info in ALL_TRACKED_ACTIONS.items():
        count = action_counter.get(action, 0)
        lines.append(f"| `{action}` | {info['severity']} | {count} | {info['label']} |")
    lines.append("")

    # 熔断→恢复配对分析
    if cb_count > 0 or reset_count > 0:
        lines.append("## 4. 熔断/恢复配对分析")
        lines.append("")
        lines.append(f"- 熔断触发: **{cb_count}** 次")
        lines.append(f"- 恢复事件: **{reset_count}** 次")
        if cb_count > reset_count:
            unresolved = cb_count - reset_count
            lines.append(f"- ⚠️ **未恢复: {unresolved} 次** — 可能仍有熔断状态未恢复，需检查")
        elif cb_count > 0 and cb_count == reset_count:
            lines.append("- ✅ 所有熔断均已恢复")
        lines.append("")

    # 运维建议
    lines.append("## 5. 运维建议")
    lines.append("")
    if cb_count > 0 and reset_count < cb_count:
        lines.append("- 🔴 **立即处理**: 存在未恢复的熔断状态，检查是否需要手动调用 `_reset_vec_circuit()` 或重启服务")
    if we_count > 0:
        lines.append(f"- 🟡 **检查兜底表**: 写入耗尽 {we_count} 次，需执行 `replay_vec_failed()` 补偿重放兜底表数据")
    degraded_count = action_counter.get("vec.degraded_skip", 0)
    if degraded_count > 100:
        lines.append(f"- 🟡 **降级频率高**: 降级路径触发 {degraded_count} 次，向量层持续不可用，检查 sqlite-vec 加载状态")
    fail_count = action_counter.get("vec.fail_count", 0)
    if fail_count > 50:
        lines.append(f"- 🟡 **失败趋势预警**: 失败计数累积 {fail_count} 次，接近熔断阈值，提前排查")
    if cb_count == 0 and we_count == 0 and degraded_count < 10:
        lines.append("- 🟢 **状态正常**: 无需特殊处理，建议定期检查兜底表 `memories_vec_failed` 行数")
    lines.append("")

    lines.append("## 6. 相关文档")
    lines.append("")
    lines.append("- [熔断器架构说明](../docs/TLM_CIRCUIT_BREAKER_ARCH.md)")
    lines.append("- [埋点审查报告](../docs/CIRCUIT_BREAKER_METRICS_AUDIT.md)")
    lines.append("- [Prometheus 告警规则](../deploy/prometheus/circuit_breaker_alerts.yml)")
    lines.append("- [性能对比报告](../docs/PERF_COMPARE_CIRCUIT_BREAKER.md)")
    lines.append("")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TLM 熔断器运维日报生成器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--log-file", help="日志文件路径")
    parser.add_argument("--log-dir", help="日志目录路径（扫描所有 .log 文件）")
    parser.add_argument("--stdin", action="store_true", help="从 stdin 读取日志")
    parser.add_argument("--date", help="过滤指定日期（YYYY-MM-DD），默认全部")
    parser.add_argument("--output", "-o", help="输出文件路径（默认 stdout）")
    parser.add_argument("--report-date", help="报告显示的日期（默认今天）")
    args = parser.parse_args()

    # 收集事件
    events = []

    if args.stdin:
        for line in sys.stdin:
            event = parse_log_line(line)
            if event and event["action"] in ALL_TRACKED_ACTIONS:
                events.append(event)
    elif args.log_file:
        events = scan_log_file(args.log_file)
    elif args.log_dir:
        events = scan_log_dir(args.log_dir)
    else:
        # 默认尝试 logs/agent.log
        default_log = os.path.join(os.path.dirname(_PROJECT_ROOT), "logs", "agent.log")
        if not os.path.exists(default_log):
            default_log = os.path.join(_PROJECT_ROOT, "logs", "agent.log")
        if os.path.exists(default_log):
            print(f"[INFO] 未指定日志路径，使用默认: {default_log}", file=sys.stderr)
            events = scan_log_file(default_log)
        else:
            parser.error("未指定日志来源，请使用 --log-file / --log-dir / --stdin 之一")

    # 日期过滤
    if args.date:
        events = filter_by_date(events, args.date)
        print(f"[INFO] 过滤日期 {args.date}: {len(events)} 条事件", file=sys.stderr)

    if not events:
        print("[WARN] 未找到任何熔断器相关事件", file=sys.stderr)

    # 报告日期
    report_date = args.report_date or args.date or date.today().strftime("%Y-%m-%d")
    target_date_str = args.date or "全部"

    # 生成报告
    report = generate_report(events, target_date_str, report_date)

    # 输出
    if args.output:
        os.makedirs(os.path.dirname(args.output), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"[OK] 运维日报已生成: {args.output}", file=sys.stderr)
    else:
        print(report)


# 项目根（用于默认日志路径）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


if __name__ == "__main__":
    main()
