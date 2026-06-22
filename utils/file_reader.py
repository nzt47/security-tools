# -*- coding: utf-8 -*-
"""文件读取容错工具类

提供安全、容错的文件读取能力，支持：
- 文件存在性检查
- 文件大小限制
- 逐行解析容错（单行失败不影响整体）
- 编码自动降级（utf-8 → utf-8-sig → gbk）
- 字段验证
- 详细日志记录

使用示例:
    from utils.file_reader import SafeFileReader
    
    reader = SafeFileReader("data/config.jsonl", max_size_mb=10)
    result = reader.read_json_lines(required_fields=["role", "content"])
    
    if result.success:
        for line in result.valid_lines:
            process(line)
    else:
        logger.warning("读取失败: %s", result.error)
"""

import os
import json
import logging
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Prometheus 指标（可选，如果 prometheus_client 未安装则跳过）
try:
    from prometheus_client import Counter, Histogram, Gauge
    from prometheus_client import REGISTRY as _REG
    _PROMETHEUS_AVAILABLE = True

    def _safe_metric(cls, name, *args, **kwargs):
        """安全创建或复用已注册的 Prometheus 指标"""
        # Counter 的内部名称去掉 _total 后缀，Histogram/Gauge 保持原名
        registry_name = name
        if cls is Counter and name.endswith('_total'):
            registry_name = name[:-6]
        try:
            return cls(name, *args, **kwargs)
        except ValueError:
            # 已存在同名指标，返回已有实例
            return _REG._names_to_collectors[registry_name]

    # 错误计数器
    _metrics_errors = _safe_metric(
        Counter, 'yunshu_safe_file_reader_errors_total',
        'SafeFileReader 错误总数', ['error_type', 'file_path'],
    )

    # 编码降级计数器
    _metrics_fallbacks = _safe_metric(
        Counter, 'yunshu_safe_file_reader_encoding_fallbacks_total',
        'SafeFileReader 编码降级次数', ['from_encoding', 'to_encoding', 'file_path'],
    )

    # 读取耗时直方图
    _metrics_duration = _safe_metric(
        Histogram, 'yunshu_safe_file_reader_read_duration_seconds',
        'SafeFileReader 读取耗时', ['file_path'],
        buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0],
    )

    # 历史加载计数
    _metrics_history_count = _safe_metric(
        Gauge, 'yunshu_safe_file_reader_loaded_history_count',
        'SafeFileReader 加载的历史对话数', ['file_path'],
    )

    # 无效行比例
    _metrics_invalid_ratio = _safe_metric(
        Gauge, 'yunshu_safe_file_reader_invalid_ratio',
        'SafeFileReader 无效行比例', ['file_path'],
    )
except ImportError:
    _PROMETHEUS_AVAILABLE = False
    _metrics_errors = None
    _metrics_fallbacks = None
    _metrics_duration = None
    _metrics_history_count = None
    _metrics_invalid_ratio = None


def _record_error(error_type: str, file_path: str):
    """记录错误指标"""
    if _metrics_errors:
        try:
            _metrics_errors.labels(error_type=error_type, file_path=file_path).inc()
        except Exception:
            pass


def _record_fallback(from_enc: str, to_enc: str, file_path: str):
    """记录编码降级指标"""
    if _metrics_fallbacks:
        try:
            _metrics_fallbacks.labels(from_encoding=from_enc, to_encoding=to_enc, file_path=file_path).inc()
        except Exception:
            pass


def _record_duration(file_path: str, duration: float):
    """记录读取耗时指标"""
    if _metrics_duration:
        try:
            _metrics_duration.labels(file_path=file_path).observe(duration)
        except Exception:
            pass


def _record_history_count(file_path: str, count: int):
    """记录历史加载数指标"""
    if _metrics_history_count:
        try:
            _metrics_history_count.labels(file_path=file_path).set(count)
        except Exception:
            pass


def _record_invalid_ratio(file_path: str, ratio: float):
    """记录无效行比例指标"""
    if _metrics_invalid_ratio:
        try:
            _metrics_invalid_ratio.labels(file_path=file_path).set(ratio)
        except Exception:
            pass


@dataclass
class ReadResult:
    """文件读取结果"""
    success: bool = True
    """是否成功完成读取"""
    
    valid_lines: List[Any] = field(default_factory=list)
    """成功解析的行数据"""
    
    valid_count: int = 0
    """有效行数"""
    
    invalid_count: int = 0
    """无效行数"""
    
    skipped_count: int = 0
    """跳过的行数"""
    
    error: Optional[str] = None
    """错误信息（如果有）"""
    
    file_size_kb: float = 0.0
    """文件大小（KB）"""
    
    encoding_used: str = "utf-8"
    """实际使用的编码"""


class SafeFileReader:
    """安全文件读取器（带容错机制）
    
    核心特性:
    - 逐行解析容错，单行失败不影响整体
    - 编码自动降级（utf-8 → utf-8-sig → gbk）
    - 文件大小限制，防止大文件 DoS
    - 字段验证，确保数据完整性
    - 详细日志记录，便于排查问题
    
    Args:
        file_path: 文件路径
        max_size_mb: 最大文件大小（MB），超过则拒绝读取
        log_prefix: 日志前缀，用于区分不同调用方
    """
    
    # 编码降级链
    ENCODING_CHAIN = ["utf-8", "utf-8-sig", "gbk"]
    
    def __init__(self, file_path: str, max_size_mb: float = 10.0, log_prefix: str = "文件读取"):
        self.file_path = file_path
        self.max_size_bytes = int(max_size_mb * 1024 * 1024)
        self.log_prefix = log_prefix
    
    def _log(self, level: str, message: str, *args):
        """统一日志输出"""
        prefix = f"[{self.log_prefix}]"
        getattr(logger, level)("%s %s", prefix, message % args if args else message)
    
    def read_json_lines(self, required_fields: Optional[List[str]] = None) -> ReadResult:
        """读取 JSON Lines 文件（每行一个 JSON 对象）
        
        Args:
            required_fields: 必须包含的字段列表，如 ["role", "content"]
        
        Returns:
            ReadResult: 读取结果
        """
        start_time = time.time()
        result = ReadResult()
        
        # 1. 文件存在性检查
        if not self._check_file_exists(result):
            _record_error("file_not_found", self.file_path)
            _record_duration(self.file_path, time.time() - start_time)
            return result
        
        # 2. 文件大小检查
        if not self._check_file_size(result):
            _record_error("file_too_large", self.file_path)
            _record_duration(self.file_path, time.time() - start_time)
            return result
        
        # 3. 尝试不同编码读取
        if not self._read_with_encoding_fallback(result, required_fields):
            _record_duration(self.file_path, time.time() - start_time)
            return result
        
        # 记录无效行比例
        total = result.valid_count + result.invalid_count
        if total > 0:
            ratio = result.invalid_count / total
            _record_invalid_ratio(self.file_path, ratio)
        
        self._log("info", "读取完成 - 有效: %d 条，无效: %d 条", result.valid_count, result.invalid_count)
        _record_duration(self.file_path, time.time() - start_time)
        return result
    
    def read_text_lines(self) -> ReadResult:
        """读取纯文本文件（不进行 JSON 解析）
        
        Returns:
            ReadResult: 读取结果，valid_lines 包含所有非空文本行
        """
        result = ReadResult()
        
        if not self._check_file_exists(result):
            return result
        
        if not self._check_file_size(result):
            return result
        
        if not self._read_text_with_encoding_fallback(result):
            return result
        
        self._log("info", "读取完成 - 共 %d 行", result.valid_count)
        return result
    
    def _check_file_exists(self, result: ReadResult) -> bool:
        """检查文件是否存在"""
        if not os.path.exists(self.file_path):
            self._log("warning", "文件不存在，跳过加载")
            result.success = False
            result.error = "文件不存在"
            return False
        return True
    
    def _check_file_size(self, result: ReadResult) -> bool:
        """检查文件大小"""
        try:
            file_size = os.path.getsize(self.file_path)
            result.file_size_kb = file_size / 1024
            self._log("info", "文件大小: %.2f KB", result.file_size_kb)
            
            if file_size > self.max_size_bytes:
                max_mb = self.max_size_bytes / (1024 * 1024)
                self._log("error", "文件过大 (%.2f MB > %.1f MB)，拒绝读取", file_size / (1024 * 1024), max_mb)
                result.success = False
                result.error = f"文件过大 ({file_size / (1024*1024):.1f}MB > {max_mb}MB)"
                return False
        except OSError as e:
            self._log("error", "无法获取文件信息: %s", e)
            result.success = False
            result.error = str(e)
            return False
        return True
    
    def _read_with_encoding_fallback(self, result: ReadResult, required_fields: Optional[List[str]]) -> bool:
        """尝试不同编码读取 JSON Lines"""
        for i, encoding in enumerate(self.ENCODING_CHAIN):
            try:
                self._read_json_lines_with_encoding(result, encoding, required_fields)
                result.encoding_used = encoding
                if encoding != "utf-8":
                    self._log("info", "使用 %s 编码读取成功", encoding)
                    _record_fallback("utf-8", encoding, self.file_path)
                return True
            except UnicodeDecodeError as e:
                if encoding == self.ENCODING_CHAIN[-1]:
                    self._log("error", "所有编码均失败: %s", e)
                    result.success = False
                    result.error = f"编码不兼容: {e}"
                    _record_error("encoding_failed", self.file_path)
                    return False
                self._log("warning", "%s 编码失败，尝试降级...", encoding)
                _record_fallback(encoding, self.ENCODING_CHAIN[i + 1], self.file_path)
                continue
            except OSError as e:
                self._log("error", "文件读取失败: %s", e)
                result.success = False
                result.error = str(e)
                return False
        
        return False
    
    def _read_text_with_encoding_fallback(self, result: ReadResult) -> bool:
        """尝试不同编码读取纯文本"""
        for encoding in self.ENCODING_CHAIN:
            try:
                with open(self.file_path, 'r', encoding=encoding) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            result.valid_lines.append(line)
                            result.valid_count += 1
                result.encoding_used = encoding
                return True
            except UnicodeDecodeError:
                if encoding == self.ENCODING_CHAIN[-1]:
                    result.success = False
                    result.error = "编码不兼容"
                    return False
                continue
            except OSError as e:
                self._log("error", "文件读取失败: %s", e)
                result.success = False
                result.error = str(e)
                return False
        return False
    
    def _read_json_lines_with_encoding(self, result: ReadResult, encoding: str, required_fields: Optional[List[str]]):
        """使用指定编码读取 JSON Lines"""
        with open(self.file_path, 'r', encoding=encoding) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    result.skipped_count += 1
                    continue
                
                try:
                    obj = json.loads(line)
                    
                    # 字段验证
                    if required_fields:
                        missing = [fld for fld in required_fields if fld not in obj]
                        if missing:
                            result.invalid_count += 1
                            self._log("warning", "第 %d 行缺少字段 %s，跳过", line_num, missing)
                            continue
                    
                    result.valid_lines.append(obj)
                    result.valid_count += 1
                    
                except json.JSONDecodeError as e:
                    result.invalid_count += 1
                    _record_error("json_parse_failed", self.file_path)
                    self._log("warning", "第 %d 行 JSON 解析失败，跳过: %s", line_num, str(e)[:60])
