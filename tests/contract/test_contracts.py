# -*- coding: utf-8 -*-
"""
Consumer 端契约测试

【测试目标】
验证契约定义本身的正确性：
- 契约结构完整（consumer/provider/interactions）
- 字段规格合法（类型/必填/枚举）
- 示例数据符合契约
- Pact JSON 导出格式正确

【说明】
此测试不依赖 Provider 服务运行，仅校验契约定义。
Provider 端验证由 verify_provider.py 在 CI 中执行。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# 确保 contract 模块可导入
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests" / "contract"))

from contract_framework import (
    Contract,
    FieldSpec,
    FieldValidator,
    ContractViolationError,
    save_contract,
    _fields_to_example,
)
from contract_definitions import (
    build_chat_contract,
    build_health_contract,
    build_dashboard_contract,
    get_all_contracts,
)


# ═══════════════════════════════════════════════════════════════
#  契约结构完整性测试
# ═══════════════════════════════════════════════════════════════

class TestContractStructure:
    """契约结构完整性测试"""

    def test_chat_contract_structure(self, chat_contract: Contract):
        """功能测试：chat 契约结构完整"""
        assert chat_contract.name == "chat_api"
        assert chat_contract.consumer == "yunshu_frontend"
        assert chat_contract.provider == "yunshu_backend"
        assert len(chat_contract.interactions) >= 2

    def test_health_contract_structure(self, health_contract: Contract):
        """功能测试：health 契约结构完整"""
        assert health_contract.name == "health_api"
        assert len(health_contract.interactions) == 1

    def test_dashboard_contract_structure(self, dashboard_contract: Contract):
        """功能测试：dashboard 契约结构完整"""
        assert dashboard_contract.name == "dashboard_api"
        assert len(dashboard_contract.interactions) >= 2

    def test_all_contracts_registered(self, all_contracts: list):
        """功能测试：3 个契约全部注册"""
        names = {c.name for c in all_contracts}
        assert names == {"chat_api", "health_api", "dashboard_api"}

    @pytest.mark.parametrize("contract_name,build_fn", [
        ("chat_api", build_chat_contract),
        ("health_api", build_health_contract),
        ("dashboard_api", build_dashboard_contract),
    ])
    def test_each_contract_has_interactions(self, contract_name: str, build_fn):
        """功能测试：每个契约至少有 1 个交互"""
        contract = build_fn()
        assert len(contract.interactions) >= 1
        for it in contract.interactions:
            assert it.description, "交互描述不能为空"
            assert it.request_method in ("GET", "POST", "PUT", "DELETE")
            assert it.request_path.startswith("/api/")
            assert 100 <= it.response_status < 600


# ═══════════════════════════════════════════════════════════════
#  字段规格校验
# ═══════════════════════════════════════════════════════════════

class TestFieldSpecs:
    """字段规格合法性测试"""

    def test_chat_request_fields(self, chat_contract: Contract):
        """功能测试：chat 请求字段定义合法"""
        normal_interaction = next(
            it for it in chat_contract.interactions if "正常" in it.description
        )
        message_field = next(
            f for f in normal_interaction.request_body_fields if f.name == "message"
        )
        assert message_field.type == "string"
        assert message_field.required is True
        assert message_field.min_length == 1
        assert message_field.max_length == 10000

    def test_chat_response_has_mode_enum(self, chat_contract: Contract):
        """功能测试：chat 响应 mode 字段有枚举约束"""
        normal_interaction = next(
            it for it in chat_contract.interactions if "正常" in it.description
        )
        mode_field = next(
            f for f in normal_interaction.response_body_fields if f.name == "mode"
        )
        assert mode_field.enum is not None
        assert "normal" in mode_field.enum

    def test_dashboard_response_has_range_constraints(self, dashboard_contract: Contract):
        """功能测试：dashboard 响应有数值范围约束"""
        quality_interaction = next(
            it for it in dashboard_contract.interactions if "质量" in it.description
        )
        success_rate = next(
            f for f in quality_interaction.response_body_fields if f.name == "success_rate"
        )
        assert success_rate.minimum == 0
        assert success_rate.maximum == 100

    def test_health_response_is_array(self, health_contract: Contract):
        """功能测试：health 响应为数组类型"""
        interaction = health_contract.interactions[0]
        root_field = interaction.response_body_fields[0]
        assert root_field.type == "array"
        assert root_field.items is not None
        assert root_field.items.type == "object"
        # 嵌套属性校验
        prop_names = {p.name for p in root_field.items.properties}
        assert "sensor_name" in prop_names
        assert "severity" in prop_names


# ═══════════════════════════════════════════════════════════════
#  示例数据验证
# ═══════════════════════════════════════════════════════════════

class TestExampleValidation:
    """示例数据符合契约测试"""

    def test_chat_normal_example_valid(self, chat_contract: Contract):
        """功能测试：chat 正常请求示例符合契约"""
        interaction = next(
            it for it in chat_contract.interactions if "正常" in it.description
        )
        example = interaction.response_example
        # 验证示例符合响应字段规格
        FieldValidator.validate("chat_api", example, interaction.response_body_fields)

    def test_chat_error_example_valid(self, chat_contract: Contract):
        """功能测试：chat 错误请求示例符合契约"""
        interaction = next(
            it for it in chat_contract.interactions if "400" in it.description
        )
        example = interaction.response_example
        FieldValidator.validate("chat_api", example, interaction.response_body_fields)

    def test_dashboard_quality_example_valid(self, dashboard_contract: Contract):
        """功能测试：dashboard 质量数据示例符合契约"""
        interaction = next(
            it for it in dashboard_contract.interactions if "质量" in it.description
        )
        example = interaction.response_example
        FieldValidator.validate("dashboard_api", example, interaction.response_body_fields)

    def test_health_example_valid(self, health_contract: Contract):
        """功能测试：health 响应示例符合契约"""
        interaction = health_contract.interactions[0]
        example = interaction.response_example
        # health 响应是数组，需包装为对象进行校验
        wrapped = {"_root": example}
        FieldValidator.validate("health_api", wrapped, interaction.response_body_fields)


# ═══════════════════════════════════════════════════════════════
#  契约持久化测试
# ═══════════════════════════════════════════════════════════════

class TestContractPersistence:
    """契约持久化测试"""

    def test_save_and_load_all_contracts(self, all_contracts: list, contracts_dir: Path):
        """功能测试：保存并加载所有契约"""
        for contract in all_contracts:
            pact_path, spec_path = save_contract(contract, contracts_dir)
            assert pact_path.exists()
            assert spec_path.exists()

            # 验证 Pact JSON 格式
            pact_data = json.loads(pact_path.read_text(encoding="utf-8"))
            assert "consumer" in pact_data
            assert "provider" in pact_data
            assert "interactions" in pact_data
            assert len(pact_data["interactions"]) == len(contract.interactions)

            # 验证规格 JSON 格式
            spec_data = json.loads(spec_path.read_text(encoding="utf-8"))
            assert spec_data["name"] == contract.name
            assert len(spec_data["interactions"]) == len(contract.interactions)

    def test_pact_specification_version(self, all_contracts: list, contracts_dir: Path):
        """功能测试：Pact 规范版本正确"""
        for contract in all_contracts:
            pact_path, _ = save_contract(contract, contracts_dir)
            pact_data = json.loads(pact_path.read_text(encoding="utf-8"))
            assert pact_data["metadata"]["pact-specification"]["version"] == "2.0.0"


# ═══════════════════════════════════════════════════════════════
#  字段验证器单元测试
# ═══════════════════════════════════════════════════════════════

class TestFieldValidator:
    """字段验证器测试"""

    def test_validate_valid_data(self):
        """功能测试：合法数据通过校验"""
        fields = [
            FieldSpec(name="id", type="integer", required=True, minimum=1),
            FieldSpec(name="name", type="string", required=True, min_length=1),
        ]
        FieldValidator.validate("test", {"id": 1, "name": "ok"}, fields)

    def test_validate_missing_required_raises(self):
        """边界测试：缺失必填字段应抛异常"""
        fields = [FieldSpec(name="id", type="integer", required=True)]
        with pytest.raises(ContractViolationError, match="CONTRACT_VIOLATION"):
            FieldValidator.validate("test", {}, fields)

    def test_validate_wrong_type_raises(self):
        """边界测试：类型不符应抛异常"""
        fields = [FieldSpec(name="id", type="integer", required=True)]
        with pytest.raises(ContractViolationError):
            FieldValidator.validate("test", {"id": "not_int"}, fields)

    def test_validate_enum_violation_raises(self):
        """边界测试：枚举值不符应抛异常"""
        fields = [FieldSpec(name="level", type="string", enum=["low", "high"])]
        with pytest.raises(ContractViolationError):
            FieldValidator.validate("test", {"level": "medium"}, fields)

    def test_validate_range_violation_raises(self):
        """边界测试：数值超范围应抛异常"""
        fields = [FieldSpec(name="score", type="number", minimum=0, maximum=100)]
        with pytest.raises(ContractViolationError):
            FieldValidator.validate("test", {"score": 150}, fields)

    def test_validate_optional_field_absent_ok(self):
        """边界测试：可选字段缺失可通过"""
        fields = [FieldSpec(name="opt", type="string", required=False)]
        FieldValidator.validate("test", {}, fields)

    def test_validate_nested_object(self):
        """功能测试：嵌套对象校验"""
        fields = [
            FieldSpec(
                name="meta", type="object", required=True,
                properties=[FieldSpec(name="version", type="string", required=True)],
            ),
        ]
        # 合法
        FieldValidator.validate("test", {"meta": {"version": "1.0"}}, fields)
        # 非法：嵌套字段缺失
        with pytest.raises(ContractViolationError):
            FieldValidator.validate("test", {"meta": {}}, fields)

    def test_validate_array_items(self):
        """功能测试：数组元素类型校验"""
        fields = [
            FieldSpec(
                name="tags", type="array", required=True,
                items=FieldSpec(name="tag", type="string"),
            ),
        ]
        # 合法
        FieldValidator.validate("test", {"tags": ["a", "b"]}, fields)
        # 非法：元素类型不符
        with pytest.raises(ContractViolationError):
            FieldValidator.validate("test", {"tags": [1, 2]}, fields)

    def test_validate_boolean_not_integer(self):
        """边界测试：布尔值不应被接受为 integer"""
        fields = [FieldSpec(name="count", type="integer", required=True)]
        with pytest.raises(ContractViolationError):
            FieldValidator.validate("test", {"count": True}, fields)


# ═══════════════════════════════════════════════════════════════
#  Provider 验证器（mock 测试，不依赖真实服务）
# ═══════════════════════════════════════════════════════════════

class TestProviderVerifierMock:
    """Provider 验证器 mock 测试（不依赖真实服务）"""

    def test_verifier_initialization(self, provider_base_url: str):
        """功能测试：验证器初始化"""
        from contract_framework import ProviderVerifier
        verifier = ProviderVerifier(provider_base_url, timeout=5)
        assert verifier.base_url == provider_base_url.rstrip("/")
        assert verifier.timeout == 5

    def test_verify_returns_results_for_each_interaction(
        self, chat_contract: Contract, provider_base_url: str
    ):
        """功能测试：验证器对每个交互返回结果"""
        from contract_framework import ProviderVerifier
        from unittest.mock import patch, MagicMock

        verifier = ProviderVerifier(provider_base_url)
        # mock _send_request 返回成功响应
        mock_response = (200, {
            "response": "ok",
            "mode": "normal",
            "mode_label": "正常",
            "logs": ["log"],
            "timing": {"total": 1.0, "safety_check": 0.1, "chat_processing": 0.9},
        })
        with patch.object(verifier, "_send_request", return_value=mock_response):
            results = verifier.verify_contract(chat_contract)

        assert len(results) == len(chat_contract.interactions)
        # 至少第一个交互应通过（正常请求）
        assert any(r.passed for r in results)

    def test_verify_status_mismatch_detected(
        self, health_contract: Contract, provider_base_url: str
    ):
        """边界测试：状态码不匹配应被检测"""
        from contract_framework import ProviderVerifier
        from unittest.mock import patch

        verifier = ProviderVerifier(provider_base_url)
        # mock 返回 500，契约期望 200
        with patch.object(verifier, "_send_request", return_value=(500, {"error": "internal"})):
            results = verifier.verify_contract(health_contract)

        assert len(results) == 1
        assert results[0].passed is False
        assert "status" in (results[0].error or "")

    def test_verify_field_violation_detected(
        self, dashboard_contract: Contract, provider_base_url: str
    ):
        """边界测试：字段违反应被检测"""
        from contract_framework import ProviderVerifier
        from unittest.mock import patch

        verifier = ProviderVerifier(provider_base_url)
        # mock 返回缺失必填字段的响应
        bad_response = (200, {"total_requests": 100})  # 缺失多个必填字段
        with patch.object(verifier, "_send_request", return_value=bad_response):
            results = verifier.verify_contract(dashboard_contract)

        assert any(not r.passed for r in results)
