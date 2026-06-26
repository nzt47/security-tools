"""
决策日志工具类 - 用于记录和展示复杂决策过程的详细日志

功能：
1. 支持多种决策类型的日志记录
2. 可配置的日志级别和输出格式（支持 JSON 和文本格式）
3. 支持统计汇总和结果展示
4. 可复用于其他模块的决策过程追踪
5. 支持导出为 JSON 格式便于自动化分析
"""

import logging
import json
import time
from typing import Any, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum
from datetime import datetime


class DecisionType(Enum):
    """决策类型枚举"""
    SELECTION = "selection"      # 选择决策
    FILTERING = "filtering"       # 过滤决策
    PRIORITIZATION = "prioritize" # 优先级决策
    MERGING = "merging"           # 合并决策
    LIMITING = "limiting"         # 限制决策


class SkipReason(Enum):
    """跳过原因枚举"""
    PRIORITY = "priority"         # 优先级去重
    ALIAS = "alias"               # 别名合并
    LIMIT = "limit"               # 数量限制
    WHITELIST = "whitelist"       # 白名单过滤
    DUPLICATE = "duplicate"       # 重复项
    INVALID = "invalid"           # 无效项


@dataclass
class DecisionRecord:
    """单条决策记录"""
    timestamp: str = ""                    # 时间戳
    item: str = ""                         # 决策项名称
    action: str = ""                       # 决策动作（selected/skipped）
    reason: Optional[str] = None           # 跳过原因
    detail: Optional[str] = None           # 详细说明
    source: Optional[str] = None           # 来源类别/模块
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "timestamp": self.timestamp,
            "item": self.item,
            "action": self.action,
            "reason": self.reason.value if self.reason and isinstance(self.reason, SkipReason) else self.reason,
            "detail": self.detail,
            "source": self.source,
        }


@dataclass
class DecisionLog:
    """决策日志集合"""
    id: str = ""                           # 日志唯一标识
    context: str = ""                       # 决策上下文描述
    start_time: float = 0.0                # 开始时间
    end_time: float = 0.0                  # 结束时间
    duration_ms: float = 0.0               # 耗时（毫秒）
    records: list = field(default_factory=list)  # 决策记录列表
    selected: list = field(default_factory=list)  # 选中的项目
    skipped_by_priority: list = field(default_factory=list)  # 优先级去重跳过的
    skipped_by_alias: list = field(default_factory=list)    # 别名合并跳过的
    skipped_by_limit: list = field(default_factory=list)    # 数量限制跳过的
    skipped_by_whitelist: list = field(default_factory=list) # 白名单过滤跳过的
    summary: dict = field(default_factory=dict)             # 汇总信息
    
    def add_selected(self, item: str, source: Optional[str] = None, detail: Optional[str] = None):
        """添加选中记录"""
        record = DecisionRecord(
            timestamp=datetime.now().isoformat(),
            item=item,
            action="selected",
            source=source,
            detail=detail
        )
        self.records.append(record)
        self.selected.append(item)
        return record
    
    def add_skipped(self, item: str, reason: SkipReason, 
                    detail: Optional[str] = None, source: Optional[str] = None):
        """添加跳过记录"""
        record = DecisionRecord(
            timestamp=datetime.now().isoformat(),
            item=item,
            action="skipped",
            reason=reason,
            detail=detail,
            source=source
        )
        self.records.append(record)
        
        # 分类记录
        record_tuple = {"item": item, "source": source or "", "detail": detail or ""}
        if reason == SkipReason.PRIORITY:
            self.skipped_by_priority.append(record_tuple)
        elif reason == SkipReason.ALIAS:
            self.skipped_by_alias.append(record_tuple)
        elif reason == SkipReason.LIMIT:
            self.skipped_by_limit.append(record_tuple)
        elif reason == SkipReason.WHITELIST:
            self.skipped_by_whitelist.append(record_tuple)
        
        return record
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "context": self.context,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
            "statistics": {
                "total_records": len(self.records),
                "selected_count": len(self.selected),
                "skipped_priority": len(self.skipped_by_priority),
                "skipped_alias": len(self.skipped_by_alias),
                "skipped_limit": len(self.skipped_by_limit),
                "skipped_whitelist": len(self.skipped_by_whitelist),
                "selection_rate": len(self.selected) / len(self.records) if self.records else 0.0,
            },
            "selected": self.selected,
            "skipped_by_priority": self.skipped_by_priority,
            "skipped_by_alias": self.skipped_by_alias,
            "skipped_by_limit": self.skipped_by_limit,
            "skipped_by_whitelist": self.skipped_by_whitelist,
            "records": [r.to_dict() if isinstance(r, DecisionRecord) else r for r in self.records],
        }
    
    def to_json(self, indent: int = 2) -> str:
        """导出为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
    
    def to_json_file(self, filepath: str) -> bool:
        """导出到 JSON 文件"""
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(self.to_json())
            return True
        except Exception:
            return False


class DecisionLogger:
    """决策日志工具类"""
    
    # 输出格式枚举
    class OutputFormat(Enum):
        TEXT = "text"
        JSON = "json"
        BOTH = "both"
    
    def __init__(self, verbose: bool = False, 
                 output_format: str = "text",
                 logger: Optional[logging.Logger] = None):
        """
        初始化决策日志器
        
        Args:
            verbose: 是否输出详细日志到控制台
            output_format: 输出格式（text/json/both）
            logger: Python logging.Logger 实例，用于记录到日志文件
        """
        self.verbose = verbose
        self.output_format = self.OutputFormat(output_format.lower())
        self.logger = logger or logging.getLogger(__name__)
        self.current_log: Optional[DecisionLog] = None
    
    def start_log(self, context: str, input_data: Any = None) -> DecisionLog:
        """
        开始新的决策日志
        
        Args:
            context: 决策上下文描述
            input_data: 输入数据（可选）
        
        Returns:
            DecisionLog 实例
        """
        import uuid
        self.current_log = DecisionLog(
            id=uuid.uuid4().hex[:12],
            context=context,
            start_time=time.time(),
        )
        
        if self.verbose:
            if self.output_format in (self.OutputFormat.TEXT, self.OutputFormat.BOTH):
                print(f"\n[决策日志] 开始决策: {context}")
                if input_data:
                    print(f"[决策日志] 输入数据: {input_data}")
        
        return self.current_log
    
    def log_category(self, category: str, priority: int, label: str, item_count: int):
        """
        记录类别处理开始
        
        Args:
            category: 类别标识
            priority: 优先级
            label: 类别标签
            item_count: 该类别的项目数量
        """
        if self.verbose and self.output_format in (self.OutputFormat.TEXT, self.OutputFormat.BOTH):
            print(f"\n[类别处理] [{priority}] {label} ({category}): {item_count} 个项目")
    
    def log_selected(self, item: str, source: Optional[str] = None, 
                     extra_info: Optional[str] = None):
        """
        记录选中决策
        
        Args:
            item: 选中的项目名称
            source: 来源类别/模块
            extra_info: 额外信息
        """
        if self.current_log:
            self.current_log.add_selected(item, source, extra_info)
        
        if self.verbose and self.output_format in (self.OutputFormat.TEXT, self.OutputFormat.BOTH):
            msg = f"  ✅ [{item}] 选中"
            if extra_info:
                msg += f" - {extra_info}"
            print(msg)
        
        # 记录到 logger
        log_entry = {
            "event": "decision_selected",
            "item": item,
            "source": source,
            "extra_info": extra_info,
        }
        self.logger.debug("决策选中: %s", json.dumps(log_entry, ensure_ascii=False))
    
    def log_skipped(self, item: str, reason: SkipReason, 
                    source: Optional[str] = None, detail: Optional[str] = None):
        """
        记录跳过决策
        
        Args:
            item: 跳过的项目名称
            reason: 跳过原因
            source: 来源类别/模块
            detail: 详细说明
        """
        if self.current_log:
            self.current_log.add_skipped(item, reason, detail, source)
        
        if self.verbose and self.output_format in (self.OutputFormat.TEXT, self.OutputFormat.BOTH):
            reason_text = {
                SkipReason.PRIORITY: "已被更高优先级选中",
                SkipReason.ALIAS: "是别名工具，已合并",
                SkipReason.LIMIT: "已达到数量限制",
                SkipReason.WHITELIST: "不在白名单中",
                SkipReason.DUPLICATE: "重复项",
                SkipReason.INVALID: "无效项",
            }
            
            msg = f"  ⏭️ [{item}] 跳过 - {reason_text.get(reason, reason.value)}"
            if detail:
                msg += f" ({detail})"
            print(msg)
        
        # 记录到 logger
        log_entry = {
            "event": "decision_skipped",
            "item": item,
            "reason": reason.value,
            "source": source,
            "detail": detail,
        }
        self.logger.debug("决策跳过: %s", json.dumps(log_entry, ensure_ascii=False))
    
    def log_limit_reached(self, limit: int):
        """
        记录达到限制
        
        Args:
            limit: 限制值
        """
        if self.verbose and self.output_format in (self.OutputFormat.TEXT, self.OutputFormat.BOTH):
            print(f"  ⚠️ 已达到最大数量限制 ({limit})")
    
    def end_log(self, summary: Optional[dict] = None) -> DecisionLog:
        """
        结束决策日志并输出汇总
        
        Args:
            summary: 汇总信息字典
        
        Returns:
            DecisionLog 实例
        """
        if not self.current_log:
            return DecisionLog(context="empty")
        
        log = self.current_log
        log.end_time = time.time()
        log.duration_ms = (log.end_time - log.start_time) * 1000
        if summary:
            log.summary = summary
        
        # 文本格式输出
        if self.verbose and self.output_format in (self.OutputFormat.TEXT, self.OutputFormat.BOTH):
            print(f"\n[结果汇总]")
            print(f"  上下文: {log.context}")
            print(f"  耗时: {log.duration_ms:.2f}ms")
            print(f"  选中项目: {len(log.selected)} 个")
            
            if log.skipped_by_priority:
                print(f"  优先级去重跳过: {len(log.skipped_by_priority)} 个")
                for item in log.skipped_by_priority:
                    print(f"    - {item['item']} (来源: {item['source']}, {item['detail']})")
            
            if log.skipped_by_alias:
                print(f"  别名合并跳过: {len(log.skipped_by_alias)} 个")
                for item in log.skipped_by_alias:
                    print(f"    - {item['item']} (来源: {item['source']}, {item['detail']})")
            
            if log.skipped_by_limit:
                print(f"  数量限制跳过: {len(log.skipped_by_limit)} 个")
                for item in log.skipped_by_limit:
                    print(f"    - {item['item']}")
            
            if log.skipped_by_whitelist:
                print(f"  白名单过滤跳过: {len(log.skipped_by_whitelist)} 个")
                for item in log.skipped_by_whitelist:
                    print(f"    - {item['item']}")
            
            if summary:
                for key, value in summary.items():
                    print(f"  {key}: {value}")
        
        # JSON 格式输出
        if self.output_format in (self.OutputFormat.JSON, self.OutputFormat.BOTH):
            json_output = log.to_json()
            if self.verbose:
                print(f"\n[JSON 格式日志]")
                print(json_output)
        
        # 记录到 logger
        log_entry = log.to_dict()
        self.logger.info(
            "决策完成: %s → 选中 %d 项 (优先级去重=%d, 别名合并=%d, 数量限制=%d, 耗时=%.2fms)",
            log.context[:50], len(log.selected),
            len(log.skipped_by_priority), len(log.skipped_by_alias),
            len(log.skipped_by_limit), log.duration_ms
        )
        # 同时记录 JSON 格式的详细信息
        self.logger.debug("决策详情: %s", json.dumps(log_entry, ensure_ascii=False))
        
        return log
    
    def get_statistics(self) -> dict:
        """
        获取决策统计信息
        
        Returns:
            统计信息字典
        """
        if not self.current_log:
            return {}
        
        log = self.current_log
        return {
            "id": log.id,
            "context": log.context,
            "duration_ms": log.duration_ms,
            "total_records": len(log.records),
            "selected_count": len(log.selected),
            "skipped_priority": len(log.skipped_by_priority),
            "skipped_alias": len(log.skipped_by_alias),
            "skipped_limit": len(log.skipped_by_limit),
            "skipped_whitelist": len(log.skipped_by_whitelist),
            "selection_rate": len(log.selected) / len(log.records) if log.records else 0.0,
        }
    
    def get_json_output(self) -> str:
        """
        获取当前日志的 JSON 格式输出
        
        Returns:
            JSON 字符串
        """
        if not self.current_log:
            return "{}"
        return self.current_log.to_json()


def create_decision_logger(verbose: bool = False, 
                           output_format: str = "text",
                           logger_name: str = "decision_logger") -> DecisionLogger:
    """
    创建决策日志器的便捷函数
    
    Args:
        verbose: 是否输出详细日志
        output_format: 输出格式（text/json/both）
        logger_name: 日志器名称
    
    Returns:
        DecisionLogger 实例
    """
    logger = logging.getLogger(logger_name)
    return DecisionLogger(verbose=verbose, output_format=output_format, logger=logger)