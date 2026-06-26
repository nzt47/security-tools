# -*- coding: utf-8 -*-
"""
Pact 契约测试模块

【设计理念】
遵循 Pact 规范（https://pact.io/）的 Consumer-Driven Contract Testing 模式：
- Consumer 端定义期望的请求/响应契约（JSON 格式）
- Provider 端启动真实服务验证契约

【依赖策略】
优先使用 pact-python（开源），未安装时降级为 requests + jsonschema 验证。
不引入付费 Pact Broker，契约以本地 JSON 文件存储。
"""

import os
from pathlib import Path

# 契约文件存储目录
CONTRACTS_DIR = Path(__file__).parent / "contracts"
# Provider 验证结果输出目录
VERIFICATION_DIR = Path(__file__).parent.parent.parent / "docs" / "observability" / "contract_verification"

# Provider 基础地址（CI 中通过环境变量覆盖）
PROVIDER_BASE_URL = os.environ.get("PROVIDER_BASE_URL", "http://localhost:5678")
