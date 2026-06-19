"""
SafeFileReader Prometheus 指标导出器 - 薄包装
实际代码位于 agent/monitoring/prometheus.py
"""
from agent.monitoring.prometheus import (
    yunshu_safe_file_reader_errors_total,
    yunshu_safe_file_reader_encoding_fallbacks_total,
    yunshu_safe_file_reader_read_duration_seconds,
    yunshu_safe_file_reader_loaded_history_count,
    yunshu_safe_file_reader_invalid_ratio,
    record_error,
    record_encoding_fallback,
    record_read_duration,
    set_loaded_history_count,
    set_invalid_ratio,
)

__all__ = [
    "yunshu_safe_file_reader_errors_total",
    "yunshu_safe_file_reader_encoding_fallbacks_total",
    "yunshu_safe_file_reader_read_duration_seconds",
    "yunshu_safe_file_reader_loaded_history_count",
    "yunshu_safe_file_reader_invalid_ratio",
    "record_error", "record_encoding_fallback", "record_read_duration",
    "set_loaded_history_count", "set_invalid_ratio",
]
