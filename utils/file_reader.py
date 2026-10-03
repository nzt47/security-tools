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

# ============================================================================
# 【2026-10-03 · 死代码收口】**本模块当前无生产调用方**（决策：保留，不删）。
#
# ① 现状：SafeFileReader 在非测试代码里 0 个调用方 —— 启动期"整文件读取历史"的接线已移除，
#    现行历史读取走 agent/jsonl_history.py 的**尾部窗口**（只读末尾 256 KiB、坏行跳过、文件缺失返回空）。
#    全仓仅剩 4 类引用，没有一个是生产链路：
#      - tests/unit/test_safe_file_reader_alerts.py（单测 4 条，随本文件一起保留）
#      - scripts/verify_business_metrics_registration.py:176、scripts/verify_skill_retrieval_metrics.py:188
#        （两个验证脚本里的 import）
#      - scripts/deploy_automation.py、scripts/deployment_drill.py（均已标注"作废"）
#      - docs/deployment_guide_history_fix.md 等历史文档
#
# ② 为什么保留：它是**通用工具类**（存在性检查 / 10MB 上限 / 逐行容错 /
#    编码降级 utf-8→utf-8-sig→gbk / 字段校验），将来真要接线可直接复用；
#    删掉它只会让"接线"这件事被重写一遍，而这正是 A-1 想避免的。
#
# ③ 本文件内联的 5 个指标（见下 52-81 行，自带 prometheus_client 缺失时的 noop 降级）
#    **自成一套**，与 agent/monitoring/prometheus.py:596-660 的同名定义互不冲突
#    （两边都走"重复注册则复用 REGISTRY 里已有实例"）。
#    ⚠ 已知不一致（本次仍不修，理由见下）：本文件 _metrics_fallbacks 的标签是
#    ['from_encoding','to_encoding','file_path']，而 prometheus.py:612 的同名 Counter 只有
#    ['file_path']。
#
#    【2026-10-03 实测：这不是"文档层面的口径差异"，而是一个**已存在的静默失效**，但故意不改】
#    · 机制：prometheus_client 对同名指标只允许注册一次。app_server 装配期先 import
#      agent.server_routes.routes_logging（app_server.py:1557）→ 它 import agent.monitoring.prometheus
#      → prometheus.py:612 先注册，**标签固定为 (file_path,)**。
#      本文件的 _safe_metric() 随后触发 ValueError，走 _REG._names_to_collectors 复用**同一个** collector
#      （实测：两处拿到的是同一个对象，_labelnames 均为 ('file_path',)，见下表）。
#    · 后果：本文件 :135 的 .labels(from_encoding=…, to_encoding=…, file_path=…) 会抛
#      ValueError("Incorrect label names")，被 :136 的 except 静默吞掉 ⇒
#      **编码降级事件不会体现在该指标上**（指标值保持 0）。
#    · 反向同理：若先 import 本文件，registry 标签变成 3 个，prometheus.py:655 的
#      record_encoding_fallback(file_path) 反而会抛异常（实测确认）。
#    · 实测命令（两种导入顺序各跑一次）：python -c "..." 见
#      docs/closeout/过期运维指引收口_第二批_20261003.md §4 —— 结论：
#        app_server 装配序 → REGISTRY labelnames = ('file_path',)；3 标签 .labels() RAISED ValueError
#    · **为什么不改**：SafeFileReader 在本仓**无任何生产调用方**（见本段 ①），这条路径线上永不执行；
#      改它收益为 0，而"统一标签"要同时动本文件与 prometheus.py 两处定义（后者有生产 import，见
#      prometheus.py:546-555），属独立立项。此处只记录事实，零代码改动。
#
# ④ 恢复接线时的参考位置（按顺序读）：
#    - 先读现行实现：agent/jsonl_history.py（确认"整文件读取 + 编码降级"是否真是你要的语义）
#    - 原始接线点已随重构移除：git log -S"_load_chat_history_from_file" -- app_server.py
#    - 设计/用法示例：docs/deployment_guide_history_fix.md:119-140
#    - 若接线，请**同时**决定是否恢复告警（否则又是一个"恒不触发"）：
#      git show <A-1 删除提交>^:monitoring/alerts_safe_file_reader.yml
# ============================================================================

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
    # 【2026-10-03 实测】下面这 3 个标签**在 app_server 装配序下拿不到**：
    #   prometheus.py:612 的同名 Counter 先注册且只有 (file_path,)，_safe_metric() 命中 ValueError 后
    #   复用其 collector ⇒ 本对象实际 _labelnames = ('file_path',)，:135 的 3 标签 .labels() 会抛
    #   ValueError 并被 :136 静默吞掉（编码降级不计入指标）。本模块无生产调用方，故**只记录不改**。
    #   详见文件头 ③ 与 docs/closeout/过期运维指引收口_第二批_20261003.md §4。
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
    """记录编码降级指标

    ⚠【2026-10-03 实测】在 app_server 装配序下，_metrics_fallbacks 被 prometheus.py:612 抢先注册为
    单标签 (file_path,)，下面的 3 标签 .labels() 会抛 ValueError 并被 except 静默吞掉
    ⇒ 编码降级不会体现在指标上。本模块无生产调用方，故**只记录不改**（见文件头 ③）。
    """
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

        # [埋点·2026-10-02 补] 本次从该文件加载到的**有效条数**（历史记录条数）。
        # 【为什么必须无条件上报（不能塞进下面的 if total > 0）】
        #   告警 SafeFileReaderHistoryLoadEmpty 的判据是
        #   "yunshu_safe_file_reader_loaded_history_count == 0"，而文件为空时
        #   total 恰好 == 0 —— 若跟着无效行比例一起放进 if 分支，**恰恰漏掉要告警的
        #   那一种场景**（原实现的问题正是这个 gauge 从来没人 set，序列根本不存在）。
        # 【为什么埋在这里而不是调用方】条数只有读取器自己知道（ReadResult.valid_count），
        #   与同段的 _record_duration / _record_invalid_ratio 保持同一上报口径（按 file_path）。
        # 【失败隔离】_record_history_count 内部 try/except，绝不影响读取主流程。
        _record_history_count(self.file_path, result.valid_count)

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
