"""网络配置验证函数

提供 LLM 实例和 MCP 服务的配置验证。
"""
from typing import List


def validate_llm_instance(instance: dict) -> List[str]:
        """验证 LLM 实例配置"""
        errors = []

        if not instance.get('name'):
            errors.append('服务名称不能为空')
        if not instance.get('api_endpoint'):
            errors.append('API 端点 URL 不能为空')
        if not instance.get('provider'):
            errors.append('提供商不能为空')

        # URL 格式验证
        if instance.get('api_endpoint'):
            try:
                from urllib.parse import urlparse
                parsed = urlparse(instance['api_endpoint'])
                if not parsed.scheme or not parsed.netloc:
                    errors.append('API 端点 URL 格式无效')
            except Exception:
                errors.append('API 端点 URL 格式无效')

        # 数值验证
        if 'max_concurrent_requests' in instance:
            if not isinstance(instance['max_concurrent_requests'], int) or instance['max_concurrent_requests'] < 1:
                errors.append('最大并发请求数必须是正整数')

        if 'timeout' in instance:
            if not isinstance(instance['timeout'], int) or instance['timeout'] < 1 or instance['timeout'] > 300:
                errors.append('超时时间必须在 1-300 秒之间')

        if 'max_retries' in instance:
            if not isinstance(instance['max_retries'], int) or instance['max_retries'] < 0 or instance['max_retries'] > 10:
                errors.append('最大重试次数必须在 0-10 之间')

        return errors


def validate_mcp_service(service: dict) -> List[str]:
    """验证 MCP 服务配置

    Args:
        service: MCP 服务配置字典

    Returns:
        错误信息列表，为空表示验证通过
    """
    errors = []

    if not service.get('name'):
        errors.append('服务名称不能为空')
    if not service.get('address'):
        errors.append('MCP 服务地址不能为空')
    if not service.get('port'):
        errors.append('通信端口不能为空')

    # 端口范围验证
    if service.get('port'):
        if not isinstance(service['port'], int) or service['port'] < 1 or service['port'] > 65535:
            errors.append('通信端口必须在 1-65535 之间')

    # 协议类型验证
    if service.get('protocol') and service['protocol'] not in ['http', 'https']:
        errors.append('协议类型必须是 HTTP 或 HTTPS')

    # 超时时间验证
    if service.get('timeout'):
        if not isinstance(service['timeout'], int) or service['timeout'] < 1 or service['timeout'] > 300:
            errors.append('超时时间必须在 1-300 秒之间')

    # 重试策略验证
    if service.get('retry_strategy') and service['retry_strategy'] not in ['fixed', 'exponential', 'none']:
        errors.append('重试策略必须是固定间隔/指数退避/无重试')

    # 重试次数验证
    if service.get('max_retries'):
        if not isinstance(service['max_retries'], int) or service['max_retries'] < 0 or service['max_retries'] > 10:
            errors.append('重试次数必须在 0-10 之间')

    return errors
