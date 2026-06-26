# -*- coding: utf-8 -*-
"""契约测试公共夹具"""
import os
import sys
from pathlib import Path

import pytest

# 将项目根目录与 contract 目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests" / "contract"))

from contract_framework import (
    Contract,
    FieldSpec,
    Interaction,
    FieldValidator,
    ProviderVerifier,
    save_contract,
    save_verification_report,
    VerificationResult,
)
from contract_definitions import (
    build_chat_contract,
    build_health_contract,
    build_dashboard_contract,
    get_all_contracts,
)

# 兼容包导入与直接运行
try:
    from . import CONTRACTS_DIR, VERIFICATION_DIR, PROVIDER_BASE_URL
except ImportError:
    from tests.contract import CONTRACTS_DIR, VERIFICATION_DIR, PROVIDER_BASE_URL


# 排除 CLI 脚本被 pytest 误收集为测试
collect_ignore = ["verify_provider.py"]


@pytest.fixture
def contracts_dir() -> Path:
    """契约文件存储目录"""
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    return CONTRACTS_DIR


@pytest.fixture
def verification_dir() -> Path:
    """验证报告目录"""
    VERIFICATION_DIR.mkdir(parents=True, exist_ok=True)
    return VERIFICATION_DIR


@pytest.fixture
def provider_base_url() -> str:
    """Provider 基础地址"""
    return os.environ.get("PROVIDER_BASE_URL", PROVIDER_BASE_URL)


@pytest.fixture
def chat_contract() -> Contract:
    """/api/chat 契约"""
    return build_chat_contract()


@pytest.fixture
def health_contract() -> Contract:
    """/api/health 契约"""
    return build_health_contract()


@pytest.fixture
def dashboard_contract() -> Contract:
    """/api/dashboard 契约"""
    return build_dashboard_contract()


@pytest.fixture
def all_contracts() -> list:
    """所有契约"""
    return get_all_contracts()
