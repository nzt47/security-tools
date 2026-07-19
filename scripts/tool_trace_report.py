#!/usr/bin/env python3
"""工具调用性能分析报告生成器

功能:
1. 慢查询分析函数(可复用,返回 list[dict],适合集成到监控面板)
2. 批量执行 8 类 SQL 查询,结果导出为 CSV 文件

用法:
    # 执行全部查询并导出 CSV 到 docs/tool_trace_reports/
    python scripts/tool_trace_report.py

    # 指定数据库路径和输出目录
    python scripts/tool_trace_report.py --db agent/data/tool_trace.db --out docs/tool_trace_reports

    # 仅查询慢调用(不导出 CSV,打印到终端)
    python scripts/tool_trace_report.py --slow-only --top 10

依赖: 仅标准库(sqlite3, csv, argparse)
"""

from __future__ import annotations

import os
import csv
import sqlite3
import argparse
from datetime import datetime
from typing import Any, List, Dict, Optional, Sequence


# ════════════════════════════════════════════════════════════
#  默认路径
# ════════════════════════════════════════════════════════════

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DB = os.path.join(_PROJECT_ROOT, "agent", "data", "tool_trace.db")
_DEFAULT_OUT = os.path.join(_PROJECT_ROOT, "docs", "tool_trace_reports")


# ════════════════════════════════════════════════════════════
#  可复用函数: 慢查询分析(适合监控面板集成)
# ════════════════════════════════════════════════════════════

def get_slow_queries(
    db_path: str = _DEFAULT_DB,
    top_n: int = 20,
    window_hours: int = 24,
) -> List[Dict[str, Any]]:
    """查询 Top N 慢调用(全局维度)

    Args:
        db_path: SQLite 数据库路径
        top_n: 返回前 N 条,默认 20
        window_hours: 时间窗口(小时),默认 24

    Returns:
        list[dict]: 每条含 trace_id/tool_name/latency_ms/success/error_type/call_time
        数据库不可用时返回空列表

    示例:
        >>> rows = get_slow_queries(top_n=5)
        >>> for r in rows:
        ...     print(f"{r['tool_name']} {r['latency_ms']}ms trace={r['trace_id']}")
    """
    sql = """
        SELECT
            trace_id,
            tool_name,
            ROUND(latency_ms, 2)  AS latency_ms,
            success,
            error_type,
            datetime(timestamp, 'unixepoch', 'localtime') AS call_time
        FROM tool_traces
        WHERE timestamp >= strftime('%s', 'now', ?)
        ORDER BY latency_ms DESC
        LIMIT ?
    """
    return _query_to_dicts(db_path, sql, [f'-{window_hours} hours', top_n])


def get_slow_queries_per_tool(
    db_path: str = _DEFAULT_DB,
    top_n_per_tool: int = 3,
    window_hours: int = 24,
) -> List[Dict[str, Any]]:
    """查询每个工具的 Top N 慢调用(工具维度)

    Args:
        db_path: SQLite 数据库路径
        top_n_per_tool: 每个工具返回前 N 条,默认 3
        window_hours: 时间窗口(小时),默认 24

    Returns:
        list[dict]: 每条含 tool_name/trace_id/latency_ms/success/call_time/rank
        数据库不可用时返回空列表

    示例:
        >>> rows = get_slow_queries_per_tool(top_n_per_tool=2)
        >>> for r in rows:
        ...     print(f"[{r['rank']}] {r['tool_name']} {r['latency_ms']}ms")
    """
    sql = """
        SELECT * FROM (
            SELECT
                trace_id,
                tool_name,
                ROUND(latency_ms, 2)  AS latency_ms,
                success,
                datetime(timestamp, 'unixepoch', 'localtime') AS call_time,
                ROW_NUMBER() OVER (PARTITION BY tool_name ORDER BY latency_ms DESC) AS rank
            FROM tool_traces
            WHERE timestamp >= strftime('%s', 'now', ?)
        )
        WHERE rank <= ?
        ORDER BY tool_name, latency_ms DESC
    """
    rows = _query_to_dicts(db_path, sql, [f'-{window_hours} hours', top_n_per_tool])
    # SQLite ROW_NUMBER() 需 3.25+,降级处理
    if not rows:
        rows = _slow_queries_per_tool_fallback(db_path, top_n_per_tool, window_hours)
    return rows


def _slow_queries_per_tool_fallback(
    db_path: str,
    top_n: int,
    window_hours: int,
) -> List[Dict[str, Any]]:
    """不支持窗口函数时的降级方案(子查询法)"""
    # 先获取所有工具名
    tools = _query_to_dicts(
        db_path,
        "SELECT DISTINCT tool_name FROM tool_traces "
        "WHERE timestamp >= strftime('%s', 'now', ?)",
        [f'-{window_hours} hours'],
    )
    result: List[Dict[str, Any]] = []
    for t in tools:
        tool = t["tool_name"]
        rows = _query_to_dicts(
            db_path,
            "SELECT trace_id, tool_name, ROUND(latency_ms, 2) AS latency_ms, "
            "success, datetime(timestamp, 'unixepoch', 'localtime') AS call_time "
            "FROM tool_traces WHERE tool_name=? AND timestamp >= strftime('%s', 'now', ?) "
            "ORDER BY latency_ms DESC LIMIT ?",
            [tool, f'-{window_hours} hours', top_n],
        )
        for i, r in enumerate(rows, 1):
            r["rank"] = i
            result.append(r)
    result.sort(key=lambda x: (x["tool_name"], -x["latency_ms"]))
    return result


# ════════════════════════════════════════════════════════════
#  全量报告: 8 类 SQL 查询定义
# ════════════════════════════════════════════════════════════

def _build_report_queries() -> List[Dict[str, Any]]:
    """构建 8 类性能分析查询定义

    Returns:
        list[dict]: 每项含 name(文件名)/title(标题)/sql/params(参数)
    """
    return [
        {
            "name": "01_overview",
            "title": "总体概览: 调用量/成功率/平均延迟",
            "sql": """
                SELECT
                    COUNT(*) AS total_calls,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                    ROUND(100.0 * SUM(success) / COUNT(*), 2) AS success_rate_pct,
                    ROUND(AVG(latency_ms), 2) AS avg_latency_ms,
                    ROUND(MAX(latency_ms), 2) AS max_latency_ms
                FROM tool_traces
                WHERE timestamp >= strftime('%s', 'now', '-1 day')
            """,
            "params": [],
        },
        {
            "name": "02_daily_trend",
            "title": "近 7 天每日调用量趋势",
            "sql": """
                SELECT
                    date(timestamp, 'unixepoch', 'localtime') AS day,
                    COUNT(*) AS calls,
                    ROUND(100.0 * AVG(success), 2) AS success_rate_pct,
                    ROUND(AVG(latency_ms), 2) AS avg_latency_ms
                FROM tool_traces
                WHERE timestamp >= strftime('%s', 'now', '-7 day')
                GROUP BY day ORDER BY day DESC
            """,
            "params": [],
        },
        {
            "name": "03_tool_profile",
            "title": "工具维度: 调用量/成功率/延迟分布",
            "sql": """
                SELECT
                    tool_name,
                    COUNT(*) AS calls,
                    ROUND(100.0 * AVG(success), 2) AS success_rate_pct,
                    ROUND(AVG(latency_ms), 2) AS avg_ms,
                    ROUND(MIN(latency_ms), 2) AS min_ms,
                    ROUND(MAX(latency_ms), 2) AS max_ms
                FROM tool_traces
                WHERE timestamp >= strftime('%s', 'now', '-1 day')
                GROUP BY tool_name ORDER BY calls DESC
            """,
            "params": [],
        },
        {
            "name": "04_slow_queries_top20",
            "title": "Top 20 慢调用",
            "sql": """
                SELECT
                    trace_id, tool_name,
                    ROUND(latency_ms, 2) AS latency_ms,
                    success, error_type,
                    datetime(timestamp, 'unixepoch', 'localtime') AS call_time
                FROM tool_traces
                ORDER BY latency_ms DESC LIMIT 20
            """,
            "params": [],
        },
        {
            "name": "05_error_analysis",
            "title": "错误分析: 按 error_type 分组",
            "sql": """
                SELECT
                    COALESCE(NULLIF(error_type, ''), 'N/A') AS error_type,
                    COUNT(*) AS failures,
                    tool_name,
                    ROUND(AVG(latency_ms), 2) AS avg_latency_before_fail_ms
                FROM tool_traces
                WHERE success = 0 AND timestamp >= strftime('%s', 'now', '-1 day')
                GROUP BY error_type, tool_name
                ORDER BY failures DESC
            """,
            "params": [],
        },
        {
            "name": "06_high_fail_rate_tools",
            "title": "失败率最高的工具(失败 > 5 次)",
            "sql": """
                SELECT
                    tool_name,
                    COUNT(*) AS total,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failures,
                    ROUND(100.0 * SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) / COUNT(*), 2) AS fail_rate_pct
                FROM tool_traces
                WHERE timestamp >= strftime('%s', 'now', '-1 day')
                GROUP BY tool_name
                HAVING failures > 5
                ORDER BY fail_rate_pct DESC
            """,
            "params": [],
        },
        {
            "name": "07_dangerous_audit",
            "title": "危险操作审计(近 7 天)",
            "sql": """
                SELECT
                    trace_id, tool_name, input_hash,
                    success, permission_decision,
                    datetime(timestamp, 'unixepoch', 'localtime') AS call_time
                FROM tool_traces
                WHERE permission_decision != ''
                  AND timestamp >= strftime('%s', 'now', '-7 day')
                ORDER BY timestamp DESC
            """,
            "params": [],
        },
        {
            "name": "08_hourly_traffic",
            "title": "小时级流量画像(近 7 天)",
            "sql": """
                SELECT
                    strftime('%H', timestamp, 'unixepoch', 'localtime') AS hour,
                    COUNT(*) AS calls,
                    ROUND(100.0 * AVG(success), 2) AS success_rate_pct,
                    ROUND(AVG(latency_ms), 2) AS avg_latency_ms
                FROM tool_traces
                WHERE timestamp >= strftime('%s', 'now', '-7 day')
                GROUP BY hour ORDER BY hour ASC
            """,
            "params": [],
        },
    ]


# ════════════════════════════════════════════════════════════
#  批量导出 CSV
# ════════════════════════════════════════════════════════════

def run_all_reports(
    db_path: str = _DEFAULT_DB,
    output_dir: str = _DEFAULT_OUT,
) -> List[str]:
    """执行全部 8 类 SQL 查询,结果导出为 CSV 文件

    Args:
        db_path: SQLite 数据库路径
        output_dir: CSV 输出目录

    Returns:
        list[str]: 生成的 CSV 文件路径列表
    """
    os.makedirs(output_dir, exist_ok=True)
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    queries = _build_report_queries()
    generated_files: List[str] = []

    for q in queries:
        rows = _query_to_dicts(db_path, q["sql"], q.get("params", []))
        filename = f"{q['name']}_{timestamp_str}.csv"
        filepath = os.path.join(output_dir, filename)
        _write_csv(filepath, rows)
        generated_files.append(filepath)
        print(f"  [{q['name']}] {q['title']}")
        print(f"    → {filepath} ({len(rows)} 行)")

    return generated_files


def _write_csv(filepath: str, rows: List[Dict[str, Any]]) -> None:
    """将 dict 列表写入 CSV(UTF-8 BOM,Excel 兼容)"""
    if not rows:
        # 空结果也写一个带表头的 CSV
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            f.write("(无数据)\n")
        return
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


# ════════════════════════════════════════════════════════════
#  底层工具: DB 查询
# ════════════════════════════════════════════════════════════

def _query_to_dicts(
    db_path: str,
    sql: str,
    params: Optional[Sequence[Any]] = None,
) -> List[Dict[str, Any]]:
    """执行 SQL 查询,返回 dict 列表(数据库不可用时返回空列表)

    Args:
        db_path: SQLite 数据库路径
        sql: SQL 语句
        params: 参数列表(可选)

    Returns:
        list[dict]: 每行一个 dict,键为列名
    """
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(sql, list(params) if params else [])
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"  [警告] 查询失败: {e}")
        return []


# ════════════════════════════════════════════════════════════
#  CLI 入口
# ════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="工具调用性能分析报告生成器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 执行全部查询导出 CSV
  python scripts/tool_trace_report.py

  # 指定 DB 路径和输出目录
  python scripts/tool_trace_report.py --db agent/data/tool_trace.db --out docs/reports

  # 仅查看 Top 10 慢调用(终端输出)
  python scripts/tool_trace_report.py --slow-only --top 10

  # 查看每个工具 Top 2 慢调用
  python scripts/tool_trace_report.py --slow-per-tool --top 2
        """,
    )
    parser.add_argument(
        "--db", default=_DEFAULT_DB,
        help=f"SQLite 数据库路径(默认: {_DEFAULT_DB})",
    )
    parser.add_argument(
        "--out", default=_DEFAULT_OUT,
        help=f"CSV 输出目录(默认: {_DEFAULT_OUT})",
    )
    parser.add_argument(
        "--slow-only", action="store_true",
        help="仅查询全局 Top N 慢调用,打印到终端(不导出 CSV)",
    )
    parser.add_argument(
        "--slow-per-tool", action="store_true",
        help="仅查询每工具 Top N 慢调用,打印到终端(不导出 CSV)",
    )
    parser.add_argument(
        "--top", type=int, default=20,
        help="Top N 条数(默认: 20)",
    )
    parser.add_argument(
        "--window", type=int, default=24,
        help="时间窗口(小时,默认: 24)",
    )
    args = parser.parse_args()

    # 模式 1: 仅慢查询(终端输出)
    if args.slow_only:
        print(f"\n{'='*60}")
        print(f"  Top {args.top} 慢调用(近 {args.window} 小时)")
        print(f"{'='*60}")
        rows = get_slow_queries(args.db, top_n=args.top, window_hours=args.window)
        if not rows:
            print("  (无数据或数据库不存在)")
            return
        _print_table(rows, ["trace_id", "tool_name", "latency_ms", "success", "error_type", "call_time"])
        return

    if args.slow_per_tool:
        print(f"\n{'='*60}")
        print(f"  每工具 Top {args.top} 慢调用(近 {args.window} 小时)")
        print(f"{'='*60}")
        rows = get_slow_queries_per_tool(args.db, top_n_per_tool=args.top, window_hours=args.window)
        if not rows:
            print("  (无数据或数据库不存在)")
            return
        _print_table(rows, ["rank", "tool_name", "trace_id", "latency_ms", "success", "call_time"])
        return

    # 模式 2: 全量报告导出 CSV
    print(f"\n{'='*60}")
    print(f"  工具调用性能分析报告")
    print(f"  DB: {args.db}")
    print(f"  输出目录: {args.out}")
    print(f"{'='*60}\n")

    if not os.path.exists(args.db):
        print(f"  [错误] 数据库不存在: {args.db}")
        print(f"  提示: 运行项目后 tool_trace.db 会自动创建")
        return

    files = run_all_reports(args.db, args.out)
    print(f"\n{'='*60}")
    print(f"  完成! 共生成 {len(files)} 个 CSV 文件")
    print(f"  位置: {args.out}")
    print(f"{'='*60}")


def _print_table(rows: List[Dict[str, Any]], columns: List[str]) -> None:
    """简单的终端表格打印"""
    if not rows:
        print("  (无数据)")
        return
    # 计算列宽
    widths = {}
    for col in columns:
        widths[col] = max(len(col), max(len(str(r.get(col, ""))) for r in rows))
    # 表头
    header = "  " + " | ".join(col.ljust(widths[col]) for col in columns)
    print(header)
    print("  " + "-" * (len(header) - 2))
    # 数据行
    for r in rows:
        line = "  " + " | ".join(str(r.get(col, "")).ljust(widths[col]) for col in columns)
        print(line)


if __name__ == "__main__":
    main()
