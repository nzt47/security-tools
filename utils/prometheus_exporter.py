#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SafeFileReader Prometheus 指标导出器"""

from prometheus_client import Counter, Histogram, Gauge, REGISTRY
from prometheus_client.core import GaugeMetricFamily

# SafeFileReader 指标定义
yunshu_safe_file_reader_errors_total = Counter(
    'yunshu_safe_file_reader_errors_total',
    'SafeFileReader 错误总数',
    ['error_type', 'file_path']
)

yunshu_safe_file_reader_encoding_fallbacks_total = Counter(
    'yunshu_safe_file_reader_encoding_fallbacks_total',
    'SafeFileReader 编码降级次数',
    ['file_path']
)

yunshu_safe_file_reader_read_duration_seconds = Histogram(
    'yunshu_safe_file_reader_read_duration_seconds',
    'SafeFileReader 读取耗时',
    ['file_path'],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
)

yunshu_safe_file_reader_loaded_history_count = Gauge(
    'yunshu_safe_file_reader_loaded_history_count',
    'SafeFileReader 加载的历史对话数',
    ['file_path']
)

yunshu_safe_file_reader_invalid_ratio = Gauge(
    'yunshu_safe_file_reader_invalid_ratio',
    'SafeFileReader 无效行比例',
    ['file_path']
)

def record_error(error_type, file_path):
    """记录错误"""
    yunshu_safe_file_reader_errors_total.labels(error_type=error_type, file_path=file_path).inc()

def record_encoding_fallback(file_path):
    """记录编码降级"""
    yunshu_safe_file_reader_encoding_fallbacks_total.labels(file_path=file_path).inc()

def record_read_duration(file_path, duration):
    """记录读取耗时"""
    yunshu_safe_file_reader_read_duration_seconds.labels(file_path=file_path).observe(duration)

def set_loaded_history_count(file_path, count):
    """设置加载的历史对话数"""
    yunshu_safe_file_reader_loaded_history_count.labels(file_path=file_path).set(count)

def set_invalid_ratio(file_path, ratio):
    """设置无效行比例"""
    yunshu_safe_file_reader_invalid_ratio.labels(file_path=file_path).set(ratio)
