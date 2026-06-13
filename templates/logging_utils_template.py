#!/usr/bin/env python3
"""
通用日志配置模板

使用方法：
1. 将此文件复制到新项目中
2. 在项目入口导入并调用 setup_logging()
3. 配置 LOGGING_CONFIG 字典调整参数

示例：
    from logging_utils import setup_logging, get_logger
    
    setup_logging(debug_mode=True)
    logger = get_logger("my_app")
    logger.info("应用启动")
"""

import os
import sys
import logging
from typing import Optional, Dict, List


# ───────────────────────────────────────────────────────────────
# 可配置参数
# ───────────────────────────────────────────────────────────────

LOGGING_CONFIG = {
    # 默认日志级别
    'default_level': 'INFO',
    
    # 调试模式日志级别
    'debug_level': 'DEBUG',
    
    # 日志格式
    'format': "%(asctime)s [%(levelname)8s] %(name)-25s: %(message)s",
    
    # 日期格式
    'date_format': "%H:%M:%S",
    
    # 需要降低日志级别的第三方库
    'quiet_modules': [
        'urllib3',
        'httpx',
        'httpcore',
        'anthropic',
        'openai',
        'transformers',
        'torch',
    ],
    
    # 需要设置日志级别的模块
    'module_levels': {
        # 'my_module': 'DEBUG',
        # 'my_app.core': 'INFO',
    },
    
    # 是否启用颜色输出
    'use_color': True,
    
    # 日志输出位置 (stdout/file/both)
    'output': 'stdout',
    
    # 日志文件路径（如果 output 包含 'file'）
    'log_file': 'app.log',
    
    # 日志文件大小限制（字节）
    'max_file_size': 10 * 1024 * 1024,  # 10MB
    
    # 备份日志文件数量
    'backup_count': 5,
}


# ───────────────────────────────────────────────────────────────
# 颜色支持
# ───────────────────────────────────────────────────────────────

class ColorFormatter(logging.Formatter):
    """带颜色的日志格式化器"""
    
    COLORS = {
        'DEBUG': '\033[94m',      # 蓝色
        'INFO': '\033[92m',       # 绿色
        'WARNING': '\033[93m',    # 黄色
        'ERROR': '\033[91m',      # 红色
        'CRITICAL': '\033[95m',   # 紫色
        'RESET': '\033[0m',       # 重置
    }
    
    def format(self, record):
        levelname = record.levelname
        if levelname in self.COLORS:
            record.levelname = f"{self.COLORS[levelname]}{levelname}{self.COLORS['RESET']}"
        return super().format(record)


# ───────────────────────────────────────────────────────────────
# 日志配置函数
# ───────────────────────────────────────────────────────────────

def setup_logging(
    debug_mode: bool = False,
    config: Optional[Dict] = None,
) -> logging.Logger:
    """
    配置完整的日志系统

    Args:
        debug_mode: 是否启用调试模式
        config: 自定义配置字典（覆盖默认配置）

    Returns:
        主日志记录器
    """
    # 合并配置
    cfg = LOGGING_CONFIG.copy()
    if config:
        cfg.update(config)

    # 确定日志级别
    level = logging.DEBUG if debug_mode else logging.INFO

    # 配置根日志
    handlers = []

    # 控制台输出
    if cfg['output'] in ('stdout', 'both'):
        console_handler = logging.StreamHandler(sys.stdout)
        if cfg['use_color']:
            console_handler.setFormatter(ColorFormatter(cfg['format'], cfg['date_format']))
        else:
            console_handler.setFormatter(logging.Formatter(cfg['format'], cfg['date_format']))
        console_handler.setLevel(level)
        handlers.append(console_handler)

    # 文件输出
    if cfg['output'] in ('file', 'both'):
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            cfg['log_file'],
            maxBytes=cfg['max_file_size'],
            backupCount=cfg['backup_count'],
            encoding='utf-8',
        )
        file_handler.setFormatter(logging.Formatter(cfg['format'], cfg['date_format']))
        file_handler.setLevel(level)
        handlers.append(file_handler)

    # 配置根记录器
    logging.basicConfig(
        level=level,
        handlers=handlers,
        format=cfg['format'],
        datefmt=cfg['date_format'],
    )

    # 降低第三方库日志噪音
    for module in cfg['quiet_modules']:
        logging.getLogger(module).setLevel(logging.WARNING)

    # 设置指定模块的日志级别
    for module_name, module_level in cfg['module_levels'].items():
        level_const = getattr(logging, module_level.upper(), logging.INFO)
        logging.getLogger(module_name).setLevel(level_const)

    # 获取主日志记录器
    main_logger = logging.getLogger("app")
    
    # 输出初始化信息
    main_logger.info("=" * 70)
    main_logger.info("日志系统配置完成")
    main_logger.info(f"调试模式: {'启用' if debug_mode else '关闭'}")
    main_logger.info(f"日志级别: {'DEBUG' if debug_mode else 'INFO'}")
    main_logger.info(f"输出位置: {cfg['output']}")
    if cfg['output'] in ('file', 'both'):
        main_logger.info(f"日志文件: {cfg['log_file']}")
    main_logger.info("=" * 70)

    return main_logger


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的日志记录器

    Args:
        name: 日志记录器名称

    Returns:
        日志记录器实例
    """
    return logging.getLogger(name)


# ───────────────────────────────────────────────────────────────
# 安全监控器
# ───────────────────────────────────────────────────────────────

class SafetyMonitor:
    """
    安全监控器 - 防止死循环和状态卡死

    使用示例：
        monitor = SafetyMonitor()
        
        # 记录迭代
        if not monitor.record_iteration("my_task"):
            print("检测到快速循环！")
        
        # 检查状态
        if not monitor.check_state("my_task", "running"):
            print("检测到状态卡死！")
    """

    def __init__(
        self,
        max_iterations_per_minute: int = 100,
        state_stuck_threshold_seconds: int = 10,
    ):
        """
        初始化安全监控器

        Args:
            max_iterations_per_minute: 每分钟最大迭代次数
            state_stuck_threshold_seconds: 状态卡死阈值（秒）
        """
        self._iteration_count = {}
        self._last_state = {}
        self._state_change_time = {}
        self._max_iterations = max_iterations_per_minute
        self._stuck_threshold = state_stuck_threshold_seconds
        self._logger = get_logger("safety")

    def record_iteration(self, identifier: str) -> bool:
        """
        记录一次迭代，检查是否异常

        Args:
            identifier: 任务标识符

        Returns:
            是否正常（未检测到异常）
        """
        from datetime import datetime

        current_time = datetime.now()

        if identifier not in self._iteration_count:
            self._iteration_count[identifier] = {
                'total': 0,
                'window_start': current_time,
                'window_count': 0,
            }

        record = self._iteration_count[identifier]
        time_diff = (current_time - record['window_start']).total_seconds()

        if time_diff >= 60:
            record['window_start'] = current_time
            record['window_count'] = 0
        else:
            record['window_count'] += 1

            if record['window_count'] > self._max_iterations:
                self._logger.error(
                    f"⚠️ 检测到快速循环: {identifier}, "
                    f"1分钟内迭代 {record['window_count']} 次"
                )
                return False

        record['total'] += 1
        return True

    def check_state(self, identifier: str, state: str) -> bool:
        """
        检查状态变化，检测是否卡死

        Args:
            identifier: 任务标识符
            state: 当前状态

        Returns:
            是否正常（未检测到卡死）
        """
        from datetime import datetime

        current_time = datetime.now()

        if identifier not in self._last_state:
            self._last_state[identifier] = state
            self._state_change_time[identifier] = current_time
            return True

        old_state = self._last_state[identifier]

        if old_state == state:
            stuck_time = (
                current_time - self._state_change_time[identifier]
            ).total_seconds()

            if stuck_time > self._stuck_threshold:
                self._logger.error(
                    f"⚠️ 检测到状态卡死: {identifier}, "
                    f"状态 '{state}' 保持 {stuck_time:.1f} 秒"
                )
                return False
        else:
            self._last_state[identifier] = state
            self._state_change_time[identifier] = current_time

        return True

    def reset(self, identifier: str = None):
        """重置监控数据"""
        if identifier:
            self._iteration_count.pop(identifier, None)
            self._last_state.pop(identifier, None)
            self._state_change_time.pop(identifier, None)
        else:
            self._iteration_count.clear()
            self._last_state.clear()
            self._state_change_time.clear()

    def get_stats(self) -> Dict:
        """获取监控统计"""
        return {
            'tracked_tasks': len(self._iteration_count),
            'max_iterations_per_minute': self._max_iterations,
            'state_stuck_threshold': self._stuck_threshold,
        }


# ───────────────────────────────────────────────────────────────
# 安全执行包装器
# ───────────────────────────────────────────────────────────────

def safe_execute(
    func,
    timeout: float = 30.0,
    default_return=None,
    identifier: str = None,
) -> any:
    """
    带超时保护的函数执行包装器

    Args:
        func: 要执行的函数
        timeout: 超时时间（秒）
        default_return: 超时时的默认返回值
        identifier: 任务标识符（用于监控）

    Returns:
        函数返回值或默认值
    """
    import threading
    from datetime import datetime

    logger = get_logger("safety.execute")
    task_id = identifier or f"task_{datetime.now().timestamp()}"

    result_container = {'value': None, 'exception': None}

    def target():
        try:
            result_container['value'] = func()
        except Exception as e:
            result_container['exception'] = e
            logger.error(f"执行异常: {e}")

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        logger.warning(f"⏱️ 执行超时（{timeout}秒）: {task_id}")
        return default_return

    if result_container['exception']:
        raise result_container['exception']

    return result_container['value']


# ───────────────────────────────────────────────────────────────
# 异常类型
# ───────────────────────────────────────────────────────────────

class TimeoutException(Exception):
    """超时异常"""
    pass


class LoopDetectionException(Exception):
    """循环检测异常"""
    pass


class StateStuckException(Exception):
    """状态卡死异常"""
    pass


# ───────────────────────────────────────────────────────────────
# 导出
# ───────────────────────────────────────────────────────────────

__all__ = [
    'LOGGING_CONFIG',
    'setup_logging',
    'get_logger',
    'SafetyMonitor',
    'safe_execute',
    'TimeoutException',
    'LoopDetectionException',
    'StateStuckException',
]


# ───────────────────────────────────────────────────────────────
# 测试代码
# ───────────────────────────────────────────────────────────────

def test_logging():
    """测试日志系统"""
    logger = setup_logging(debug_mode=True)
    
    logger.debug("这是 DEBUG 日志")
    logger.info("这是 INFO 日志")
    logger.warning("这是 WARNING 日志")
    logger.error("这是 ERROR 日志")
    
    return True


def test_safety_monitor():
    """测试安全监控器"""
    monitor = SafetyMonitor(max_iterations_per_minute=5)
    
    # 测试正常迭代
    assert monitor.record_iteration("test") is True
    
    # 测试快速循环
    for i in range(6):
        result = monitor.record_iteration("fast_loop")
        if i < 5:
            assert result is True
        else:
            assert result is False
    
    # 测试状态卡死
    import time
    monitor2 = SafetyMonitor(state_stuck_threshold_seconds=1)
    monitor2.check_state("stuck", "running")
    time.sleep(1.1)
    result = monitor2.check_state("stuck", "running")
    assert result is False
    
    return True


def test_safe_execute():
    """测试安全执行包装器"""
    # 正常执行
    def quick_task():
        return "done"
    assert safe_execute(quick_task) == "done"
    
    # 超时测试
    def slow_task():
        import time
        time.sleep(2)
        return "done"
    result = safe_execute(slow_task, timeout=0.5, default_return="timeout")
    assert result == "timeout"
    
    return True


if __name__ == "__main__":
    print("测试日志系统...")
    test_logging()
    
    print("\n测试安全监控器...")
    test_safety_monitor()
    
    print("\n测试安全执行包装器...")
    test_safe_execute()
    
    print("\n✅ 所有测试通过！")
