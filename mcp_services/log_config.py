"""MCP服务日志配置模块

根据环境变量区分开发环境和生产环境的日志级别：
- 开发环境 (DEVELOPMENT): DEBUG级别，输出详细日志
- 生产环境 (PRODUCTION): ERROR级别，仅输出错误日志

环境变量:
- MCP_LOG_LEVEL: 覆盖默认日志级别
- ENV: 环境标识 (development/production)

使用方式:
    from mcp_services.log_config import configure_logging
    
    # 配置日志
    configure_logging()
    
    # 获取日志器
    logger = logging.getLogger("mcp_client")
    logger.debug("调试信息")  # 开发环境输出，生产环境不输出
    logger.error("错误信息")  # 所有环境都输出
"""

import logging
import os
from typing import Optional


def get_log_level() -> int:
    """根据环境变量获取日志级别"""
    # 优先使用 MCP_LOG_LEVEL 环境变量
    log_level_env = os.environ.get("MCP_LOG_LEVEL", "").upper()
    
    if log_level_env:
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        return level_map.get(log_level_env, logging.INFO)
    
    # 根据环境类型决定日志级别
    env = os.environ.get("ENV", "development").lower()
    
    if env == "production":
        return logging.ERROR
    else:
        return logging.DEBUG


def configure_logging(
    name: Optional[str] = None,
    format_string: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level: Optional[int] = None
) -> logging.Logger:
    """
    配置日志记录器
    
    Args:
        name: 日志器名称，默认为 None（根日志器）
        format_string: 日志格式
        level: 日志级别，若未指定则根据环境自动确定
    
    Returns:
        配置好的日志记录器
    """
    # 获取日志级别
    log_level = level if level is not None else get_log_level()
    
    # 创建日志记录器
    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    
    # 避免重复添加处理器
    if logger.handlers:
        return logger
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    
    # 创建格式化器
    formatter = logging.Formatter(format_string)
    console_handler.setFormatter(formatter)
    
    # 添加处理器
    logger.addHandler(console_handler)
    
    # 记录配置信息（仅在开发环境）
    if log_level <= logging.DEBUG:
        env = os.environ.get("ENV", "development").lower()
        logger.debug(f"日志配置完成 - 环境: {env}, 级别: {logging.getLevelName(log_level)}")
    
    return logger


def configure_mcp_logging() -> None:
    """配置所有MCP相关模块的日志"""
    # 配置 MCP 客户端日志
    configure_logging("mcp_client")
    
    # 配置 MCP 桥接器日志
    configure_logging("yunshu_mcp_bridge")
    
    # 配置多引擎搜索日志
    configure_logging("multi_search_mcp")
    
    # 配置扩展管理器日志
    configure_logging("agent.extensions.manager")
    
    # 配置扩展存储日志
    configure_logging("agent.extensions.store")


def get_env_info() -> dict:
    """获取当前环境配置信息"""
    return {
        "env": os.environ.get("ENV", "development"),
        "mcp_log_level": os.environ.get("MCP_LOG_LEVEL", "auto"),
        "log_level_name": logging.getLevelName(get_log_level()),
    }


if __name__ == "__main__":
    # 演示日志配置
    print("=" * 60)
    print("MCP日志配置演示")
    print("=" * 60)
    
    # 获取环境信息
    env_info = get_env_info()
    print(f"\n当前环境配置:")
    print(f"  ENV: {env_info['env']}")
    print(f"  MCP_LOG_LEVEL: {env_info['mcp_log_level']}")
    print(f"  实际日志级别: {env_info['log_level_name']}")
    
    # 配置日志
    configure_mcp_logging()
    
    # 获取日志器
    logger = logging.getLogger("mcp_client")
    
    print("\n日志输出测试:")
    logger.debug("这是 DEBUG 级别日志（开发环境可见）")
    logger.info("这是 INFO 级别日志（开发环境可见）")
    logger.warning("这是 WARNING 级别日志（开发环境可见）")
    logger.error("这是 ERROR 级别日志（所有环境可见）")
    
    print("\n" + "=" * 60)
    print("日志配置完成")
    print("=" * 60)