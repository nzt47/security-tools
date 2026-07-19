"""网络配置管理包"""
# 【P3 已清理】兼容层 agent/network/config_manager.py 已删除
# 所有符号从新版 agent.network_config 重新导出，保持 from agent.network import XXX 兼容
from agent.network_config import (
    NetworkConfigManager,
    _NETWORK_CONFIG_FILE,
    _DEFAULT_NETWORK_CONFIG,
    _DEFAULT_LLM_INSTANCE,
    _DEFAULT_SEARCH_INSTANCE,
    _DEFAULT_MCP_SERVICE,
)
from agent.network.config_validator import validate_llm_instance, validate_mcp_service

__all__ = [
    "NetworkConfigManager",
    "_NETWORK_CONFIG_FILE",
    "_DEFAULT_NETWORK_CONFIG",
    "_DEFAULT_LLM_INSTANCE",
    "_DEFAULT_SEARCH_INSTANCE",
    "_DEFAULT_MCP_SERVICE",
    "validate_llm_instance",
    "validate_mcp_service",
]
