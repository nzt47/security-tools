#!/usr/bin/env python3
"""
业务指标基线采集脚本

功能：
- 自动采集7天基线数据
- 计算核心指标的统计信息（均值、标准差、百分位等）
- 生成基线报告
- 支持定时任务集成

使用方式：
    python scripts/baseline_collector.py --days 7 --output data/baseline/
    python scripts/baseline_collector.py --generate-report
"""

import os
import sys
import json
import time
import argparse
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.monitoring.business_metrics import (
    BUSINESS_METRICS_DEFINITIONS,
    BusinessMetricsCollector,
    get_business_metrics_collector,
)

# 配置结构化日志
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger('baseline_collector')


def _log(module_name: str, action: str, extra: dict = None, duration_ms: float = 0):
    """输出统一格式的 JSON 结构化日志"""
    log_entry = {
        "trace_id": "",
        "module_name": module_name,
        "action": action,
        "duration_ms": round(duration_ms, 2),
        "level": "INFO"
    }
    if extra:
        log_entry.update(extra)
    logger.info(json.dumps(log_entry, ensure_ascii=False))


def _log_error(module_name: str, action: str, error: str, extra: dict = None):
    """输出统一格式的 JSON 错误日志"""
    log_entry = {
        "trace_id": "",
        "module_name": module_name,
        "action": action,
        "error": error,
        "duration_ms": 0,
        "level": "ERROR"
    }
    if extra:
        log_entry.update(extra)
    logger.error(json.dumps(log_entry, ensure_ascii=False))


class BaselineCollector:
    """业务指标基线采集器"""

    def __init__(self, storage_path: str = None):
        self.storage_path = storage_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data', 'baseline'
        )
        os.makedirs(self.storage_path, exist_ok=True)
        self._db_path = os.path.join(self.storage_path, 'baseline.db')
        self._collector = get_business_metrics_collector()

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接，失败时记录详细错误"""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as e:
            _log_error("baseline_collector", "db_connect_failed", str(e), {
                "db_path": self._db_path,
                "error_type": type(e).__name__
            })
            raise

    def initialize(self):
        """初始化基线数据库"""
        t0 = time.time()
        _log("baseline_collector", "initialize_start", {
            "storage_path": self.storage_path,
            "db_path": self._db_path
        })

        with self._get_conn() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS baseline_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT NOT NULL UNIQUE,
                    collected_at REAL NOT NULL,
                    metrics_data TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    record_count INTEGER NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS baseline_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    metric_name TEXT NOT NULL,
                    label_key TEXT NOT NULL,
                    period_start REAL NOT NULL,
                    period_end REAL NOT NULL,
                    sample_count INTEGER NOT NULL,
                    mean REAL NOT NULL,
                    std_dev REAL NOT NULL,
                    min REAL NOT NULL,
                    max REAL NOT NULL,
                    p25 REAL NOT NULL,
                    p50 REAL NOT NULL,
                    p75 REAL NOT NULL,
                    p95 REAL NOT NULL,
                    p99 REAL NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(metric_name, label_key, period_start, period_end)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS baseline_config (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    config_key TEXT NOT NULL UNIQUE,
                    config_value TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_time ON baseline_snapshots(collected_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_stats_metric ON baseline_stats(metric_name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_stats_period ON baseline_stats(period_start, period_end)")

            conn.commit()

        _log("baseline_collector", "initialize_complete", {"db_path": self._db_path}, duration_ms=(time.time()-t0)*1000)

    def collect_snapshot(self) -> Dict[str, Any]:
        """采集当前时刻的业务指标快照"""
        start_time = time.time()
        _log("baseline_collector", "collect_snapshot_start")

        try:
            dashboard_data = self._collector.get_dashboard_data()
        except Exception as e:
            _log_error("baseline_collector", "collect_snapshot_fetch_failed", str(e), {
                "error_type": type(e).__name__,
                "collector_type": type(self._collector).__name__
            })
            raise

        snapshot = {
            "snapshot_id": f"snap_{int(start_time)}",
            "collected_at": start_time,
            "collected_at_iso": datetime.fromtimestamp(start_time).isoformat(),
            "data": dashboard_data,
            "duration_seconds": round(time.time() - start_time, 4),
            "record_count": self._count_records(dashboard_data)
        }

        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO baseline_snapshots
                       (snapshot_id, collected_at, metrics_data, duration_seconds, record_count)
                       VALUES (?, ?, ?, ?, ?)""",
                    (snapshot['snapshot_id'], snapshot['collected_at'],
                     json.dumps(snapshot['data'], ensure_ascii=False),
                     snapshot['duration_seconds'], snapshot['record_count'])
                )
                conn.commit()
            _log("baseline_collector", "collect_snapshot_saved", {
                "snapshot_id": snapshot['snapshot_id'],
                "record_count": snapshot['record_count']
            })
        except Exception as e:
            _log_error("baseline_collector", "collect_snapshot_save_failed", str(e), {
                "snapshot_id": snapshot['snapshot_id']
            })

        duration_ms = (time.time() - start_time) * 1000
        _log("baseline_collector", "collect_snapshot_complete", {
            "snapshot_id": snapshot['snapshot_id'],
            "record_count": snapshot['record_count'],
            "collected_at": snapshot['collected_at_iso']
        }, duration_ms=duration_ms)

        return snapshot

    def _count_records(self, dashboard_data: Dict) -> int:
        """统计快照中的记录数，异常时记录详细错误"""
        count = 0
        try:
            for category in ['interaction', 'task', 'knowledge', 'extension']:
                if category in dashboard_data:
                    for metric_name, metric_data in dashboard_data[category].items():
                        data = metric_data.get('data', {})
                        if isinstance(data, dict):
                            count += sum(data.values()) if all(isinstance(v, int) for v in data.values()) else len(data)
        except Exception as e:
            _log_error("baseline_collector", "count_records_failed", str(e), {
                "error_type": type(e).__name__,
                "dashboard_keys": list(dashboard_data.keys()) if isinstance(dashboard_data, dict) else "not_a_dict"
            })
        return count

    def calculate_baseline(self, days: int = 7) -> Dict[str, Any]:
        """计算指定天数的基线统计"""
        t0 = time.time()
        self.initialize()
        _log("baseline_collector", "calculate_baseline_start", {"days": days})

        end_time = time.time()
        start_time = end_time - (days * 24 * 3600)

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM baseline_snapshots WHERE collected_at >= ? AND collected_at <= ?",
                (start_time, end_time)
            )
            rows = cursor.fetchall()

        if not rows:
            _log_error("baseline_collector", "calculate_baseline_no_data", f"未找到 {days} 天内的快照数据", {"days": days})
            return {"error": "no_data", "days": days}

        all_metrics = {}
        parse_success = 0
        parse_failed = 0
        for row in rows:
            try:
                data = json.loads(row['metrics_data'])
                self._extract_metrics(data, all_metrics)
                parse_success += 1
            except Exception as e:
                parse_failed += 1
                _log_error("baseline_collector", "parse_snapshot_failed", str(e), {
                    "snapshot_id": row.get('snapshot_id', 'unknown'),
                    "error_type": type(e).__name__,
                    "collected_at": row.get('collected_at', 'unknown')
                })

        _log("baseline_collector", "snapshot_parse_summary", {
            "total": len(rows),
            "success": parse_success,
            "failed": parse_failed,
            "metrics_extracted": len(all_metrics)
        })

        baseline_results = {}
        saved_count = 0
        for metric_name, label_data in all_metrics.items():
            baseline_results[metric_name] = {}
            for label_key, values in label_data.items():
                stats = self._calculate_stats(values)
                baseline_results[metric_name][label_key] = stats

                try:
                    with self._get_conn() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            """INSERT OR REPLACE INTO baseline_stats
                               (metric_name, label_key, period_start, period_end,
                                sample_count, mean, std_dev, min, max,
                                p25, p50, p75, p95, p99, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (metric_name, label_key, start_time, end_time,
                             stats['count'], stats['mean'], stats['std_dev'],
                             stats['min'], stats['max'], stats['p25'],
                             stats['p50'], stats['p75'], stats['p95'],
                             stats['p99'], time.time())
                        )
                        conn.commit()
                    saved_count += 1
                except Exception as e:
                    _log_error("baseline_collector", "save_baseline_stat_failed", str(e), {
                        "metric_name": metric_name, "label_key": label_key
                    })

        baseline_summary = {
            "period_days": days,
            "period_start": datetime.fromtimestamp(start_time).isoformat(),
            "period_end": datetime.fromtimestamp(end_time).isoformat(),
            "snapshot_count": len(rows),
            "metric_count": len(baseline_results),
            "stats_saved": saved_count,
            "baseline": baseline_results
        }

        duration_ms = (time.time() - t0) * 1000
        _log("baseline_collector", "calculate_baseline_complete", {
            "days": days,
            "snapshot_count": len(rows),
            "metric_count": len(baseline_results),
            "stats_saved": saved_count
        }, duration_ms=duration_ms)

        return baseline_summary

    def _extract_metrics(self, dashboard_data: Dict, all_metrics: Dict):
        """从快照数据中提取指标值，含详细的跳过日志"""
        skipped_no_definition = 0
        skipped_invalid_data = 0
        skipped_non_numeric = 0
        extracted_count = 0

        for category in ['interaction', 'task', 'knowledge', 'extension']:
            if category not in dashboard_data:
                continue
            for metric_name, metric_data in dashboard_data[category].items():
                definition = BUSINESS_METRICS_DEFINITIONS.get(metric_name)
                if not definition:
                    skipped_no_definition += 1
                    continue

                data = metric_data.get('data', {})
                if not isinstance(data, dict):
                    skipped_invalid_data += 1
                    _log("baseline_collector", "extract_metric_skip_invalid_data", {
                        "metric_name": metric_name,
                        "data_type": type(data).__name__,
                        "category": category
                    })
                    continue

                for label_key, value in data.items():
                    numeric_value = None
                    if isinstance(value, (int, float)):
                        numeric_value = value
                    elif isinstance(value, dict) and 'count' in value:
                        numeric_value = value['count']
                    else:
                        skipped_non_numeric += 1
                        continue

                    if metric_name not in all_metrics:
                        all_metrics[metric_name] = {}
                    if label_key not in all_metrics[metric_name]:
                        all_metrics[metric_name][label_key] = []

                    all_metrics[metric_name][label_key].append(numeric_value)
                    extracted_count += 1

        if skipped_no_definition > 0 or skipped_invalid_data > 0 or skipped_non_numeric > 0:
            _log("baseline_collector", "extract_metrics_skip_summary", {
                "skipped_no_definition": skipped_no_definition,
                "skipped_invalid_data": skipped_invalid_data,
                "skipped_non_numeric": skipped_non_numeric,
                "extracted_count": extracted_count
            })

    def _calculate_stats(self, values: List[float]) -> Dict[str, float]:
        """计算统计指标"""
        if not values:
            return {
                "count": 0, "mean": 0.0, "std_dev": 0.0,
                "min": 0.0, "max": 0.0,
                "p25": 0.0, "p50": 0.0, "p75": 0.0, "p95": 0.0, "p99": 0.0
            }

        n = len(values)
        sorted_values = sorted(values)
        mean_val = sum(values) / n
        variance = sum((x - mean_val) ** 2 for x in values) / n
        std_dev = variance ** 0.5

        def percentile(p: float) -> float:
            idx = int(p * (n - 1))
            return sorted_values[idx]

        return {
            "count": n,
            "mean": round(mean_val, 4),
            "std_dev": round(std_dev, 4),
            "min": min(values),
            "max": max(values),
            "p25": round(percentile(0.25), 4),
            "p50": round(percentile(0.5), 4),
            "p75": round(percentile(0.75), 4),
            "p95": round(percentile(0.95), 4),
            "p99": round(percentile(0.99), 4)
        }

    def generate_baseline_report(self, days: int = 7, output_path: str = None) -> str:
        """生成基线报告"""
        t0 = time.time()
        _log("baseline_collector", "generate_report_start", {"days": days})

        baseline = self.calculate_baseline(days)
        if 'error' in baseline:
            _log_error("baseline_collector", "generate_report_failed", baseline['error'], {"days": days})
            return f"基线计算失败: {baseline['error']}"

        output_path = output_path or os.path.join(self.storage_path, 'baseline_report.json')

        anomalies = self._detect_anomalies(baseline['baseline'])
        report = {
            "report_id": f"baseline_{int(time.time())}",
            "generated_at": datetime.fromtimestamp(time.time()).isoformat(),
            "period_days": days,
            "period_start": baseline['period_start'],
            "period_end": baseline['period_end'],
            "snapshot_count": baseline['snapshot_count'],
            "summary": {},
            "metrics": baseline['baseline'],
            "anomalies": anomalies
        }

        for metric_name, label_data in baseline['baseline'].items():
            definition = BUSINESS_METRICS_DEFINITIONS.get(metric_name)
            if definition:
                category = definition.category
                if category not in report['summary']:
                    report['summary'][category] = {
                        "metric_count": 0,
                        "total_mean": 0.0,
                        "total_std_dev": 0.0
                    }
                report['summary'][category]['metric_count'] += len(label_data)
                for label_key, stats in label_data.items():
                    report['summary'][category]['total_mean'] += stats['mean']
                    report['summary'][category]['total_std_dev'] += stats['std_dev']

        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        except Exception as e:
            _log_error("baseline_collector", "write_report_failed", str(e), {
                "output_path": output_path,
                "error_type": type(e).__name__,
                "report_size_estimate": len(json.dumps(report, ensure_ascii=False))
            })
            raise

        duration_ms = (time.time() - t0) * 1000
        _log("baseline_collector", "generate_report_complete", {
            "output_path": output_path,
            "anomaly_count": len(anomalies),
            "metric_count": len(baseline['baseline'])
        }, duration_ms=duration_ms)
        return output_path

    def _detect_anomalies(self, baseline: Dict) -> List[Dict]:
        """检测基线中的异常值"""
        anomalies = []
        for metric_name, label_data in baseline.items():
            for label_key, stats in label_data.items():
                if stats['count'] > 0:
                    cv = stats['std_dev'] / stats['mean'] if stats['mean'] > 0 else float('inf')
                    if cv > 2.0:
                        _log("baseline_collector", "anomaly_detected", {
                            "metric_name": metric_name,
                            "label_key": label_key,
                            "cv": round(cv, 2),
                            "type": "high_variance"
                        })
                        anomalies.append({
                            "metric_name": metric_name,
                            "label_key": label_key,
                            "type": "high_variance",
                            "message": f"变异系数({cv:.2f})过高，数据波动较大",
                            "mean": stats['mean'],
                            "std_dev": stats['std_dev'],
                            "count": stats['count']
                        })
        return anomalies

    def get_latest_baseline(self, metric_name: str = None) -> Dict:
        """获取最新的基线数据"""
        t0 = time.time()
        _log("baseline_collector", "get_latest_baseline_start", {
            "metric_name": metric_name or "all"
        })

        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                if metric_name:
                    cursor.execute(
                        """SELECT * FROM baseline_stats
                           WHERE metric_name = ?
                           ORDER BY created_at DESC LIMIT 1""",
                        (metric_name,)
                    )
                else:
                    cursor.execute(
                        """SELECT * FROM baseline_stats
                           ORDER BY created_at DESC LIMIT 100"""
                    )
                rows = cursor.fetchall()
        except sqlite3.Error as e:
            _log_error("baseline_collector", "get_latest_baseline_query_failed", str(e), {
                "metric_name": metric_name or "all",
                "error_type": type(e).__name__
            })
            raise

        results = {}
        for row in rows:
            key = row['metric_name']
            if key not in results:
                results[key] = {}
            results[key][row['label_key']] = {
                "period_start": datetime.fromtimestamp(row['period_start']).isoformat(),
                "period_end": datetime.fromtimestamp(row['period_end']).isoformat(),
                "sample_count": row['sample_count'],
                "mean": row['mean'],
                "std_dev": row['std_dev'],
                "min": row['min'],
                "max": row['max'],
                "p25": row['p25'],
                "p50": row['p50'],
                "p75": row['p75'],
                "p95": row['p95'],
                "p99": row['p99']
            }

        _log("baseline_collector", "get_latest_baseline_complete", {
            "metric_name": metric_name or "all",
            "result_count": len(results)
        }, duration_ms=(time.time()-t0)*1000)

        return results


def main():
    parser = argparse.ArgumentParser(description='业务指标基线采集脚本')
    parser.add_argument('--days', type=int, default=7, help='基线采集天数')
    parser.add_argument('--output', type=str, help='输出目录')
    parser.add_argument('--collect', action='store_true', help='采集快照')
    parser.add_argument('--calculate', action='store_true', help='计算基线')
    parser.add_argument('--report', action='store_true', help='生成报告')
    parser.add_argument('--latest', action='store_true', help='获取最新基线')

    args = parser.parse_args()

    collector = BaselineCollector(args.output)
    collector.initialize()

    if args.collect:
        collector.collect_snapshot()
    elif args.calculate:
        baseline = collector.calculate_baseline(args.days)
        print(json.dumps(baseline, ensure_ascii=False, indent=2))
    elif args.report:
        collector.generate_baseline_report(args.days, args.output)
    elif args.latest:
        latest = collector.get_latest_baseline()
        print(json.dumps(latest, ensure_ascii=False, indent=2))
    else:
        collector.collect_snapshot()
        collector.generate_baseline_report(args.days, args.output)


if __name__ == '__main__':
    main()