"""
统一日志工具
Phase 3 - Core Abstraction
"""
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime


def log_section(
    logger: logging.Logger,
    title: str,
    items: Dict[str, Any],
    level: int = logging.INFO
) -> None:
    """
    统一的日志记录格式 - 章节式输出
    
    Args:
        logger: 日志记录器
        title: 章节标题
        items: 要记录的项目字典
        level: 日志级别
    """
    logger.log(level, title)
    
    if not items:
        return
    
    items_list = list(items.items())
    for i, (key, value) in enumerate(items_list):
        prefix = "  ├─" if i < len(items_list) - 1 else "  └─"
        logger.log(level, f"{prefix} {key}: {value}")


def log_operation(
    logger: logging.Logger,
    operation: str,
    status: str = "done",
    metadata: Dict[str, Any] = None
) -> None:
    """
    操作日志记录
    
    Args:
        logger: 日志记录器
        operation: 操作名称
        status: 状态
        metadata: 元数据
    """
    timestamp = datetime.now().isoformat()
    status_icon = "✓" if status == "done" else "✗" if status == "error" else "○"
    
    msg = f"[{timestamp}] {status_icon} {operation}"
    if metadata:
        meta_str = ", ".join(f"{k}={v}" for k, v in metadata.items())
        msg += f" ({meta_str})"
    
    logger.info(msg)


def setup_logger(
    name: str = "Yunshu",
    level: int = logging.INFO,
    log_file: str = None,
    format_str: str = None
) -> logging.Logger:
    """
    快速设置日志记录器
    
    Args:
        name: 日志记录器名称
        level: 日志级别
        log_file: 日志文件路径 (可选)
        format_str: 自定义格式 (可选)
        
    Returns:
        配置好的日志记录器
    """
    logger = logging.getLogger(name)
    
    # 避免重复添加handler
    if logger.handlers:
        return logger
    
    logger.setLevel(level)
    
    if not format_str:
        format_str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    formatter = logging.Formatter(format_str)
    
    # 控制台handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 文件handler
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


class ProgressLogger:
    """进度日志记录器"""
    
    def __init__(self, logger: logging.Logger, total: int, name: str = "Progress"):
        self.logger = logger
        self.total = total
        self.name = name
        self.current = 0
        self.start_time = datetime.now()
    
    def update(self, increment: int = 1, message: str = None) -> None:
        """更新进度"""
        self.current += increment
        progress = (self.current / self.total) * 100
        
        msg = f"{self.name}: {self.current}/{self.total} ({progress:.1f}%)"
        if message:
            msg += f" - {message}"
        
        self.logger.info(msg)
    
    def finish(self, message: str = None) -> None:
        """完成"""
        elapsed = datetime.now() - self.start_time
        msg = f"{self.name} completed in {elapsed}"
        if message:
            msg += f": {message}"
        self.logger.info(msg)
