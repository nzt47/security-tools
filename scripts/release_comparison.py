#!/usr/bin/env python3
"""
上线效果自动对比机制

功能：
- 上线前自动采集7天基线数据
- 上线后每日对比核心指标
- 异常指标自动告警
- 自动生成效果评估报告

使用方式：
    python scripts/release_comparison.py --baseline-days 7 --release-date "2024-01-15"
    python scripts/release_comparison.py --auto-alert
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
    get_business_metrics_collector,
)

# 配置结构化日志
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger('release_comparison')


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


class ReleaseComparison:
    """上线效果对比器"""

    def __init__(self, baseline_storage: str = None, comparison_storage: str = None):
        self.baseline_storage = baseline_storage or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data', 'baseline'
        )
        self.comparison_storage = comparison_storage or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data', 'comparison'
        )
        os.makedirs(self.comparison_storage, exist_ok=True)
        self._db_path = os.path.join(self.comparison_storage, 'comparison.db')
        self._collector = get_business_metrics_collector()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self):
        """初始化对比数据库"""
        t0 = time.time()
        _log("release_comparison", "initialize_start", {
            "baseline_storage": self.baseline_storage,
            "comparison_storage": self.comparison_storage,
            "db_path": self._db_path
        })

        with self._get_conn() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS release_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    release_id TEXT NOT NULL UNIQUE,
                    release_name TEXT NOT NULL,
                    release_date REAL NOT NULL,
                    baseline_start REAL NOT NULL,
                    baseline_end REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    description TEXT DEFAULT '',
                    metadata TEXT DEFAULT '{}',
                    created_at REAL NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS comparison_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    release_id TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    label_key TEXT NOT NULL,
                    baseline_mean REAL NOT NULL,
                    baseline_std_dev REAL NOT NULL,
                    baseline_count INTEGER NOT NULL,
                    post_release_mean REAL NOT NULL,
                    post_release_std_dev REAL NOT NULL,
                    post_release_count INTEGER NOT NULL,
                    change_percent REAL NOT NULL,
                    z_score REAL NOT NULL,
                    p_value REAL NOT NULL,
                    is_significant BOOLEAN NOT NULL DEFAULT 0,
                    is_anomaly BOOLEAN NOT NULL DEFAULT 0,
                    anomaly_severity TEXT DEFAULT 'none',
                    comparison_date REAL NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(release_id, metric_name, label_key, comparison_date)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alert_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    release_id TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    label_key TEXT NOT NULL,
                    alert_type TEXT NOT NULL,
                    severity TEXT NOT NULL DEFAULT 'warning',
                    message TEXT NOT NULL,
                    threshold REAL NOT NULL,
                    actual_value REAL NOT NULL,
                    created_at REAL NOT NULL
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_release_date ON release_records(release_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_comp_release ON comparison_results(release_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_comp_metric ON comparison_results(metric_name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_comp_date ON comparison_results(comparison_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_alert_release ON alert_records(release_id)")

            conn.commit()

        _log("release_comparison", "initialize_complete", {"db_path": self._db_path}, duration_ms=(time.time()-t0)*1000)

    def create_release_record(self, release_name: str, release_date: float = None,
                              baseline_days: int = 7, description: str = "") -> Dict:
        """创建上线记录"""
        t0 = time.time()
        self.initialize()

        release_date = release_date or time.time()
        baseline_end = release_date
        baseline_start = baseline_end - (baseline_days * 24 * 3600)

        release_id = f"rel_{int(release_date)}"

        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO release_records
                       (release_id, release_name, release_date, baseline_start,
                        baseline_end, status, description, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (release_id, release_name, release_date, baseline_start,
                     baseline_end, 'pending', description, time.time())
                )
                conn.commit()
            _log("release_comparison", "create_release_record", {
                "release_id": release_id,
                "release_name": release_name,
                "baseline_days": baseline_days
            }, duration_ms=(time.time()-t0)*1000)
        except sqlite3.IntegrityError:
            _log("release_comparison", "create_release_record_exists", {"release_id": release_id})
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM release_records WHERE release_id = ?",
                    (release_id,)
                )
                row = cursor.fetchone()
                if row:
                    return {
                        "release_id": row['release_id'],
                        "release_name": row['release_name'],
                        "release_date": datetime.fromtimestamp(row['release_date']).isoformat(),
                        "baseline_start": datetime.fromtimestamp(row['baseline_start']).isoformat(),
                        "baseline_end": datetime.fromtimestamp(row['baseline_end']).isoformat(),
                        "status": row['status']
                    }

        return {
            "release_id": release_id,
            "release_name": release_name,
            "release_date": datetime.fromtimestamp(release_date).isoformat(),
            "baseline_start": datetime.fromtimestamp(baseline_start).isoformat(),
            "baseline_end": datetime.fromtimestamp(baseline_end).isoformat(),
            "status": "pending",
            "description": description
        }

    def compare_metrics(self, release_id: str, days_after_release: int = 1) -> Dict:
        """对比上线后指定天数的指标"""
        t0 = time.time()
        self.initialize()
        _log("release_comparison", "compare_metrics_start", {
            "release_id": release_id,
            "days_after_release": days_after_release
        })

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM release_records WHERE release_id = ?",
                (release_id,)
            )
            release_row = cursor.fetchone()

        if not release_row:
            _log_error("release_comparison", "compare_metrics_release_not_found", "release_not_found", {"release_id": release_id})
            return {"error": "release_not_found", "release_id": release_id}

        release_date = release_row['release_date']
        baseline_start = release_row['baseline_start']
        baseline_end = release_row['baseline_end']

        post_start = release_date
        post_end = release_date + (days_after_release * 24 * 3600)

        _log("release_comparison", "compare_metrics_load_baseline", {
            "release_id": release_id,
            "baseline_period": f"{datetime.fromtimestamp(baseline_start).isoformat()} ~ {datetime.fromtimestamp(baseline_end).isoformat()}",
            "post_period": f"{datetime.fromtimestamp(post_start).isoformat()} ~ {datetime.fromtimestamp(post_end).isoformat()}"
        })

        baseline_stats = self._load_baseline_stats(baseline_start, baseline_end)
        post_stats = self._collect_current_stats(post_start, post_end)

        _log("release_comparison", "compare_metrics_stats_loaded", {
            "baseline_metric_count": len(baseline_stats),
            "post_metric_count": len(post_stats)
        })

        comparison_results = {}
        alerts = []
        compared_count = 0

        for metric_name in set(baseline_stats.keys()) | set(post_stats.keys()):
            baseline_label_data = baseline_stats.get(metric_name, {})
            post_label_data = post_stats.get(metric_name, {})

            comparison_results[metric_name] = {}

            for label_key in set(baseline_label_data.keys()) | set(post_label_data.keys()):
                baseline = baseline_label_data.get(label_key, {
                    "mean": 0, "std_dev": 0, "count": 0
                })
                post = post_label_data.get(label_key, {
                    "mean": 0, "std_dev": 0, "count": 0
                })

                if baseline['count'] > 0 and post['count'] > 0:
                    change_percent, z_score, p_value = self._calculate_change(
                        baseline['mean'], baseline['std_dev'], baseline['count'],
                        post['mean'], post['std_dev'], post['count']
                    )

                    is_significant = p_value < 0.05
                    is_anomaly, severity = self._detect_anomaly(
                        metric_name, label_key, change_percent, z_score
                    )

                    if is_anomaly:
                        alerts.append({
                            "metric_name": metric_name,
                            "label_key": label_key,
                            "alert_type": "metric_anomaly" if severity != "critical" else "critical_anomaly",
                            "severity": severity,
                            "message": self._generate_alert_message(
                                metric_name, label_key, change_percent, z_score
                            ),
                            "threshold": self._get_threshold(metric_name),
                            "actual_value": change_percent
                        })

                        self._save_alert(release_id, metric_name, label_key, alerts[-1])
                else:
                    change_percent = 0
                    z_score = 0
                    p_value = 1.0
                    is_significant = False
                    is_anomaly = False
                    severity = "none"

                comparison_results[metric_name][label_key] = {
                    "baseline_mean": baseline['mean'],
                    "baseline_std_dev": baseline['std_dev'],
                    "baseline_count": baseline['count'],
                    "post_release_mean": post['mean'],
                    "post_release_std_dev": post['std_dev'],
                    "post_release_count": post['count'],
                    "change_percent": round(change_percent, 4),
                    "z_score": round(z_score, 4),
                    "p_value": round(p_value, 6),
                    "is_significant": is_significant,
                    "is_anomaly": is_anomaly,
                    "anomaly_severity": severity
                }

                self._save_comparison(release_id, metric_name, label_key, {
                    "baseline_mean": baseline['mean'],
                    "baseline_std_dev": baseline['std_dev'],
                    "baseline_count": baseline['count'],
                    "post_release_mean": post['mean'],
                    "post_release_std_dev": post['std_dev'],
                    "post_release_count": post['count'],
                    "change_percent": change_percent,
                    "z_score": z_score,
                    "p_value": p_value,
                    "is_significant": is_significant,
                    "is_anomaly": is_anomaly,
                    "anomaly_severity": severity
                }, time.time())
                compared_count += 1

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE release_records SET status = ? WHERE release_id = ?",
                ("completed", release_id)
            )
            conn.commit()

        significant_count = sum(
            1 for m in comparison_results.values() for s in m.values() if s.get('is_significant')
        )
        duration_ms = (time.time() - t0) * 1000
        _log("release_comparison", "compare_metrics_complete", {
            "release_id": release_id,
            "compared_count": compared_count,
            "significant_count": significant_count,
            "alert_count": len(alerts),
            "alert_severities": {s: sum(1 for a in alerts if a['severity'] == s) for s in set(a['severity'] for a in alerts)}
        }, duration_ms=duration_ms)

        return {
            "release_id": release_id,
            "release_name": release_row['release_name'],
            "release_date": datetime.fromtimestamp(release_date).isoformat(),
            "baseline_period": {
                "start": datetime.fromtimestamp(baseline_start).isoformat(),
                "end": datetime.fromtimestamp(baseline_end).isoformat()
            },
            "post_release_period": {
                "start": datetime.fromtimestamp(post_start).isoformat(),
                "end": datetime.fromtimestamp(post_end).isoformat()
            },
            "days_after_release": days_after_release,
            "comparison_results": comparison_results,
            "alerts": alerts,
            "alert_count": len(alerts)
        }

    def _load_baseline_stats(self, start_time: float, end_time: float) -> Dict:
        """从基线数据库加载统计数据"""
        baseline_db_path = os.path.join(self.baseline_storage, 'baseline.db')

        if not os.path.exists(baseline_db_path):
            return {}

        with sqlite3.connect(baseline_db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """SELECT metric_name, label_key, mean, std_dev, sample_count
                   FROM baseline_stats
                   WHERE period_start <= ? AND period_end >= ?""",
                (start_time, end_time)
            )
            rows = cursor.fetchall()

        results = {}
        for row in rows:
            metric_name = row['metric_name']
            label_key = row['label_key']
            if metric_name not in results:
                results[metric_name] = {}
            results[metric_name][label_key] = {
                "mean": row['mean'],
                "std_dev": row['std_dev'],
                "count": row['sample_count']
            }

        return results

    def _collect_current_stats(self, start_time: float, end_time: float) -> Dict:
        """采集当前统计数据"""
        dashboard_data = self._collector.get_dashboard_data()

        results = {}
        for category in ['interaction', 'task', 'knowledge', 'extension']:
            if category in dashboard_data:
                for metric_name, metric_data in dashboard_data[category].items():
                    definition = BUSINESS_METRICS_DEFINITIONS.get(metric_name)
                    if not definition:
                        continue

                    data = metric_data.get('data', {})
                    if not isinstance(data, dict):
                        continue

                    if metric_name not in results:
                        results[metric_name] = {}

                    for label_key, value in data.items():
                        if isinstance(value, (int, float)):
                            results[metric_name][label_key] = {
                                "mean": value,
                                "std_dev": 0,
                                "count": 1
                            }
                        elif isinstance(value, dict):
                            results[metric_name][label_key] = {
                                "mean": value.get('mean', value.get('sum', 0)),
                                "std_dev": value.get('std_dev', 0),
                                "count": value.get('count', 1)
                            }

        return results

    def _calculate_change(self, mean1: float, std1: float, n1: int,
                          mean2: float, std2: float, n2: int) -> tuple:
        """计算变化百分比和统计显著性"""
        if mean1 == 0:
            change_percent = float('inf') if mean2 != 0 else 0
        else:
            change_percent = ((mean2 - mean1) / mean1) * 100

        if n1 <= 1 or n2 <= 1:
            return change_percent, 0.0, 1.0

        se = (std1 ** 2 / n1 + std2 ** 2 / n2) ** 0.5
        if se == 0:
            z_score = 0.0
        else:
            z_score = (mean2 - mean1) / se

        p_value = 2 * (1 - self._normal_cdf(abs(z_score)))

        return change_percent, z_score, p_value

    def _normal_cdf(self, x: float) -> float:
        """标准正态分布累积分布函数"""
        import math
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    def _detect_anomaly(self, metric_name: str, label_key: str,
                        change_percent: float, z_score: float) -> tuple:
        """检测异常"""
        threshold = self._get_threshold(metric_name)

        abs_change = abs(change_percent)
        abs_z = abs(z_score)

        is_anomaly = abs_change > threshold or abs_z > 2.0
        severity = "none"

        if is_anomaly:
            if abs_z > 3.0 or abs_change > threshold * 2:
                severity = "critical"
            elif abs_z > 2.5 or abs_change > threshold * 1.5:
                severity = "high"
            else:
                severity = "warning"
            _log("release_comparison", "anomaly_detected", {
                "metric_name": metric_name,
                "label_key": label_key,
                "change_percent": round(change_percent, 2),
                "z_score": round(z_score, 2),
                "threshold": threshold,
                "severity": severity
            })

        return is_anomaly, severity

    def _get_threshold(self, metric_name: str) -> float:
        """获取指标的异常阈值"""
        thresholds = {
            "yunshu_interaction_total": 10.0,
            "yunshu_task_completion_rate": 5.0,
            "yunshu_memory_search_hit_rate": 5.0,
            "yunshu_tool_call_total": 10.0,
            "yunshu_model_call_total": 10.0,
            "yunshu_model_success_rate": 5.0,
        }
        return thresholds.get(metric_name, 15.0)

    def _generate_alert_message(self, metric_name: str, label_key: str,
                                change_percent: float, z_score: float) -> str:
        """生成告警消息"""
        definition = BUSINESS_METRICS_DEFINITIONS.get(metric_name)
        metric_desc = definition.description if definition else metric_name

        direction = "上升" if change_percent > 0 else "下降"
        return f"指标[{metric_desc}] {direction} {abs(change_percent):.2f}% (Z-score: {z_score:.2f})"

    def _save_comparison(self, release_id: str, metric_name: str, label_key: str,
                         data: Dict, comparison_date: float):
        """保存对比结果"""
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT OR REPLACE INTO comparison_results
                       (release_id, metric_name, label_key,
                        baseline_mean, baseline_std_dev, baseline_count,
                        post_release_mean, post_release_std_dev, post_release_count,
                        change_percent, z_score, p_value,
                        is_significant, is_anomaly, anomaly_severity,
                        comparison_date, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (release_id, metric_name, label_key,
                     data['baseline_mean'], data['baseline_std_dev'], data['baseline_count'],
                     data['post_release_mean'], data['post_release_std_dev'], data['post_release_count'],
                     data['change_percent'], data['z_score'], data['p_value'],
                     int(data['is_significant']), int(data['is_anomaly']), data['anomaly_severity'],
                     comparison_date, time.time())
                )
                conn.commit()
        except Exception as e:
            print(f"保存对比结果失败: {e}")

    def _save_alert(self, release_id: str, metric_name: str, label_key: str, alert: Dict):
        """保存告警记录"""
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO alert_records
                       (release_id, metric_name, label_key,
                        alert_type, severity, message, threshold, actual_value, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (release_id, metric_name, label_key,
                     alert['alert_type'], alert['severity'], alert['message'],
                     alert['threshold'], alert['actual_value'], time.time())
                )
                conn.commit()
            _log("release_comparison", "alert_saved", {
                "release_id": release_id,
                "metric_name": metric_name,
                "severity": alert['severity'],
                "alert_type": alert['alert_type']
            })
        except Exception as e:
            _log_error("release_comparison", "save_alert_failed", str(e), {
                "release_id": release_id, "metric_name": metric_name
            })

    def generate_evaluation_report(self, release_id: str, days_after_release: int = 7) -> str:
        """生成效果评估报告"""
        t0 = time.time()
        self.initialize()
        _log("release_comparison", "generate_evaluation_report_start", {
            "release_id": release_id,
            "days_after_release": days_after_release
        })

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM release_records WHERE release_id = ?",
                (release_id,)
            )
            release_row = cursor.fetchone()

        if not release_row:
            _log_error("release_comparison", "generate_report_release_not_found", "release_not_found", {"release_id": release_id})
            return f"上线记录不存在: {release_id}"

        results = self.compare_metrics(release_id, days_after_release)
        if 'error' in results:
            _log_error("release_comparison", "generate_report_compare_failed", results['error'], {"release_id": release_id})
            return f"对比失败: {results['error']}"

        report = {
            "report_id": f"eval_{int(time.time())}",
            "generated_at": datetime.fromtimestamp(time.time()).isoformat(),
            "release_id": release_id,
            "release_name": release_row['release_name'],
            "release_date": datetime.fromtimestamp(release_row['release_date']).isoformat(),
            "baseline_period": results['baseline_period'],
            "post_release_period": results['post_release_period'],
            "days_after_release": days_after_release,
            "summary": {},
            "key_metrics": {},
            "alerts": results['alerts'],
            "recommendations": [],
            "overall_evaluation": "pending"
        }

        significant_metrics = []
        improved_metrics = []
        degraded_metrics = []

        for metric_name, label_data in results['comparison_results'].items():
            for label_key, stats in label_data.items():
                if stats['is_significant']:
                    significant_metrics.append({
                        "metric_name": metric_name,
                        "label_key": label_key,
                        "change_percent": stats['change_percent'],
                        "p_value": stats['p_value']
                    })
                    if stats['change_percent'] > 0:
                        improved_metrics.append(f"{metric_name}: {stats['change_percent']:.2f}%")
                    else:
                        degraded_metrics.append(f"{metric_name}: {stats['change_percent']:.2f}%")

        report['summary'] = {
            "total_metrics": sum(len(v) for v in results['comparison_results'].values()),
            "significant_metrics": len(significant_metrics),
            "improved_metrics": len(improved_metrics),
            "degraded_metrics": len(degraded_metrics),
            "alert_count": len(results['alerts']),
            "critical_alerts": len([a for a in results['alerts'] if a['severity'] == 'critical']),
            "high_alerts": len([a for a in results['alerts'] if a['severity'] == 'high']),
            "warning_alerts": len([a for a in results['alerts'] if a['severity'] == 'warning'])
        }

        key_metric_names = [
            "yunshu_interaction_total",
            "yunshu_task_completion_rate",
            "yunshu_memory_search_hit_rate",
            "yunshu_model_success_rate"
        ]
        for name in key_metric_names:
            if name in results['comparison_results']:
                report['key_metrics'][name] = results['comparison_results'][name]

        if len(degraded_metrics) == 0 and len(improved_metrics) > 0:
            report['overall_evaluation'] = "success"
            report['recommendations'].append({
                "type": "success",
                "message": "上线效果良好，核心指标有显著提升",
                "severity": "success"
            })
        elif len(degraded_metrics) == 0:
            report['overall_evaluation'] = "neutral"
            report['recommendations'].append({
                "type": "neutral",
                "message": "上线后指标无显著变化，建议继续观察",
                "severity": "info"
            })
        elif any(a['severity'] == 'critical' for a in results['alerts']):
            report['overall_evaluation'] = "critical"
            report['recommendations'].append({
                "type": "critical",
                "message": "检测到严重异常指标，建议立即排查",
                "severity": "critical"
            })
        else:
            report['overall_evaluation'] = "warning"
            report['recommendations'].append({
                "type": "warning",
                "message": "部分指标出现恶化，建议分析原因",
                "severity": "warning"
            })

        output_path = os.path.join(self.comparison_storage, f'evaluation_{release_id}.json')
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        duration_ms = (time.time() - t0) * 1000
        _log("release_comparison", "generate_evaluation_report_complete", {
            "release_id": release_id,
            "output_path": output_path,
            "overall_evaluation": report['overall_evaluation'],
            "alert_count": report['summary']['alert_count'],
            "significant_metrics": report['summary']['significant_metrics']
        }, duration_ms=duration_ms)
        return output_path

    def run_daily_comparison(self):
        """运行每日对比"""
        t0 = time.time()
        self.initialize()
        _log("release_comparison", "daily_comparison_start")

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM release_records WHERE status = 'pending' OR status = 'completed'"
            )
            releases = cursor.fetchall()

        processed_count = 0
        for release in releases:
            release_date = release['release_date']
            days_since_release = (time.time() - release_date) / (24 * 3600)

            if days_since_release >= 1:
                days = int(days_since_release)
                _log("release_comparison", "daily_comparison_process", {
                    "release_id": release['release_id'],
                    "days_since_release": days
                })
                self.compare_metrics(release['release_id'], days)
                processed_count += 1

        duration_ms = (time.time() - t0) * 1000
        _log("release_comparison", "daily_comparison_complete", {
            "release_count": len(releases),
            "processed_count": processed_count
        }, duration_ms=duration_ms)


def main():
    parser = argparse.ArgumentParser(description='上线效果自动对比机制')
    parser.add_argument('--release-name', type=str, required=True, help='上线名称')
    parser.add_argument('--release-date', type=str, help='上线日期 (YYYY-MM-DD)')
    parser.add_argument('--baseline-days', type=int, default=7, help='基线天数')
    parser.add_argument('--days-after', type=int, default=7, help='上线后对比天数')
    parser.add_argument('--auto-alert', action='store_true', help='自动告警')
    parser.add_argument('--daily', action='store_true', help='每日对比')
    parser.add_argument('--generate-report', action='store_true', help='生成评估报告')

    args = parser.parse_args()

    comparison = ReleaseComparison()
    comparison.initialize()

    if args.daily:
        comparison.run_daily_comparison()
    else:
        if args.release_date:
            release_date = datetime.strptime(args.release_date, '%Y-%m-%d').timestamp()
        else:
            release_date = time.time()

        release = comparison.create_release_record(
            args.release_name,
            release_date,
            args.baseline_days
        )
        print(f"创建上线记录: {json.dumps(release, ensure_ascii=False, indent=2)}")

        if args.generate_report:
            comparison.generate_evaluation_report(release['release_id'], args.days_after)


if __name__ == '__main__':
    main()