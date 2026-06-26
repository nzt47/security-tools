# -*- coding: utf-8 -*-
"""
契约框架核心：定义/验证/导出工具

【生成日志摘要】
- 生成时间：2026-06-26
- 版本：v1.0.0
- 内容：Pact 规范契约定义、Provider 验证、降级实现

【可观测性约束】
- 结构化日志：trace_id / module_name / action / duration_ms
- 边界显性化：契约校验失败抛出 ContractViolationError
- 健康检查：verify_all() 输出各契约验证状态
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("contract_framework")


def _trace_id() -> str:
    """生成简易 trace_id"""
    return uuid.uuid4().hex[:16]


# ═══════════════════════════════════════════════════════════════
#  异常定义
# ═══════════════════════════════════════════════════════════════

class ContractViolationError(AssertionError):
    """契约违反异常（边界显性化）"""

    def __init__(self, contract_name: str, field_path: str, expected: Any, actual: Any):
        self.contract_name = contract_name
        self.field_path = field_path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"契约 '{contract_name}' 违反: 字段 '{field_path}' "
            f"期望={expected}, 实际={actual} "
            f"[error_code=CONTRACT_VIOLATION]"
        )


class ContractNotFoundError(FileNotFoundError):
    """契约文件不存在"""


# ═══════════════════════════════════════════════════════════════
#  契约数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class FieldSpec:
    """字段规格"""
    name: str
    type: str  # string / integer / number / boolean / array / object / null
    required: bool = True
    enum: Optional[List[Any]] = None
    description: str = ""
    # 嵌套字段（type=object 时）
    properties: Optional[List["FieldSpec"]] = None
    # 数组元素类型（type=array 时）
    items: Optional["FieldSpec"] = None
    # 最小/最大值约束
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    # 最小/最大长度
    min_length: Optional[int] = None
    max_length: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        d: Dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "description": self.description,
        }
        if self.enum:
            d["enum"] = self.enum
        if self.properties:
            d["properties"] = [p.to_dict() for p in self.properties]
        if self.items:
            d["items"] = self.items.to_dict()
        if self.minimum is not None:
            d["minimum"] = self.minimum
        if self.maximum is not None:
            d["maximum"] = self.maximum
        if self.min_length is not None:
            d["min_length"] = self.min_length
        if self.max_length is not None:
            d["max_length"] = self.max_length
        return d


@dataclass
class Interaction:
    """单个交互（请求-响应对）"""
    description: str
    request_method: str  # GET / POST / PUT / DELETE
    request_path: str
    request_headers: Dict[str, str] = field(default_factory=dict)
    request_query: Dict[str, Any] = field(default_factory=dict)
    request_body_fields: List[FieldSpec] = field(default_factory=list)
    response_status: int = 200
    response_headers: Dict[str, str] = field(default_factory=lambda: {"Content-Type": "application/json"})
    response_body_fields: List[FieldSpec] = field(default_factory=list)
    # 响应示例（用于文档生成）
    response_example: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "request": {
                "method": self.request_method,
                "path": self.request_path,
                "headers": self.request_headers,
                "query": self.request_query,
                "body_fields": [f.to_dict() for f in self.request_body_fields],
            },
            "response": {
                "status": self.response_status,
                "headers": self.response_headers,
                "body_fields": [f.to_dict() for f in self.response_body_fields],
                "example": self.response_example or {},
            },
        }


@dataclass
class Contract:
    """完整契约定义"""
    name: str
    consumer: str
    provider: str
    version: str = "1.0.0"
    description: str = ""
    interactions: List[Interaction] = field(default_factory=list)

    def to_pact_dict(self) -> Dict[str, Any]:
        """导出为 Pact 规范兼容的 JSON 字典"""
        return {
            "consumer": {"name": self.consumer},
            "provider": {"name": self.provider},
            "interactions": [
                {
                    "description": it.description,
                    "request": {
                        "method": it.request_method,
                        "path": it.request_path,
                        "headers": it.request_headers,
                        "query": it.request_query or None,
                        "body": _fields_to_example(it.request_body_fields),
                    },
                    "response": {
                        "status": it.response_status,
                        "headers": it.response_headers,
                        "body": _fields_to_example(it.response_body_fields),
                    },
                }
                for it in self.interactions
            ],
            "metadata": {
                "pact-specification": {"version": "2.0.0"},
                "contract-version": self.version,
                "description": self.description,
                "generated_at": datetime.now().isoformat(),
            },
        }

    def to_spec_dict(self) -> Dict[str, Any]:
        """导出为含字段规格的完整契约（用于文档与验证）"""
        return {
            "name": self.name,
            "consumer": self.consumer,
            "provider": self.provider,
            "version": self.version,
            "description": self.description,
            "interactions": [it.to_dict() for it in self.interactions],
            "generated_at": datetime.now().isoformat(),
        }


def _fields_to_example(fields: List[FieldSpec]) -> Dict[str, Any]:
    """将字段规格转为示例值（用于 Pact body）"""
    example: Dict[str, Any] = {}
    for f in fields:
        if not f.required:
            continue
        if f.type == "string":
            example[f.name] = f.enum[0] if f.enum else "sample"
        elif f.type == "integer":
            example[f.name] = int(f.minimum) if f.minimum is not None else 1
        elif f.type == "number":
            example[f.name] = float(f.minimum) if f.minimum is not None else 1.0
        elif f.type == "boolean":
            example[f.name] = True
        elif f.type == "array":
            example[f.name] = []
        elif f.type == "object":
            example[f.name] = {}
        elif f.type == "null":
            example[f.name] = None
    return example


# ═══════════════════════════════════════════════════════════════
#  字段验证器
# ═══════════════════════════════════════════════════════════════

class FieldValidator:
    """字段验证器"""

    TYPE_MAP = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
        "null": type(None),
    }

    @classmethod
    def validate(
        cls,
        contract_name: str,
        data: Any,
        fields: List[FieldSpec],
        path: str = "",
    ) -> None:
        """验证 data 是否符合字段规格，不符合抛出 ContractViolationError"""
        if not isinstance(data, dict):
            raise ContractViolationError(contract_name, path or "$", "object", type(data).__name__)

        for spec in fields:
            field_path = f"{path}.{spec.name}" if path else spec.name
            value = data.get(spec.name)

            # 必填校验
            if value is None and spec.required:
                # null 类型允许 None
                if spec.type != "null":
                    raise ContractViolationError(
                        contract_name, field_path, "非空值", "None/缺失"
                    )

            if value is None:
                continue  # 可选字段为空，跳过后续校验

            # 类型校验
            # 特殊处理：bool 是 int 的子类，但 integer/number 不应接受 bool
            if spec.type in ("integer", "number") and isinstance(value, bool):
                raise ContractViolationError(
                    contract_name, field_path, spec.type, "boolean"
                )
            expected_type = cls.TYPE_MAP.get(spec.type)
            if expected_type and not isinstance(value, expected_type):
                raise ContractViolationError(
                    contract_name, field_path, spec.type, type(value).__name__
                )

            # 枚举校验
            if spec.enum and value not in spec.enum:
                raise ContractViolationError(
                    contract_name, field_path, f"枚举值 {spec.enum}", value
                )

            # 数值范围校验
            if spec.type in ("integer", "number") and isinstance(value, (int, float)):
                if spec.minimum is not None and value < spec.minimum:
                    raise ContractViolationError(
                        contract_name, field_path, f">= {spec.minimum}", value
                    )
                if spec.maximum is not None and value > spec.maximum:
                    raise ContractViolationError(
                        contract_name, field_path, f"<= {spec.maximum}", value
                    )

            # 字符串长度校验
            if spec.type == "string" and isinstance(value, str):
                if spec.min_length is not None and len(value) < spec.min_length:
                    raise ContractViolationError(
                        contract_name, field_path, f"len >= {spec.min_length}", len(value)
                    )
                if spec.max_length is not None and len(value) > spec.max_length:
                    raise ContractViolationError(
                        contract_name, field_path, f"len <= {spec.max_length}", len(value)
                    )

            # 嵌套对象校验
            if spec.type == "object" and spec.properties and isinstance(value, dict):
                cls.validate(contract_name, value, spec.properties, field_path)

            # 数组元素校验
            if spec.type == "array" and spec.items and isinstance(value, list):
                for i, item in enumerate(value):
                    item_path = f"{field_path}[{i}]"
                    if spec.items.type == "object" and spec.items.properties:
                        cls.validate(contract_name, item, spec.items.properties, item_path)
                    else:
                        expected_item_type = cls.TYPE_MAP.get(spec.items.type)
                        if expected_item_type and not isinstance(item, expected_item_type):
                            raise ContractViolationError(
                                contract_name, item_path, spec.items.type, type(item).__name__
                            )


# ═══════════════════════════════════════════════════════════════
#  Provider 验证器
# ═══════════════════════════════════════════════════════════════

@dataclass
class VerificationResult:
    """单个交互的验证结果"""
    contract_name: str
    interaction_description: str
    passed: bool
    error: Optional[str] = None
    duration_ms: float = 0.0
    actual_status: Optional[int] = None
    actual_body: Optional[Any] = None


class ProviderVerifier:
    """Provider 端验证器：启动真实服务并验证契约"""

    def __init__(self, base_url: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def verify_contract(self, contract: Contract) -> List[VerificationResult]:
        """验证整个契约，返回每个交互的结果"""
        results: List[VerificationResult] = []
        for interaction in contract.interactions:
            result = self._verify_interaction(contract.name, interaction)
            results.append(result)
        return results

    def _verify_interaction(self, contract_name: str, interaction: Interaction) -> VerificationResult:
        """验证单个交互"""
        trace_id = _trace_id()
        start = time.time()
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "contract_verification",
            "action": "verify.start",
            "contract": contract_name,
            "interaction": interaction.description,
        }, ensure_ascii=False))

        result = VerificationResult(
            contract_name=contract_name,
            interaction_description=interaction.description,
            passed=False,
        )

        try:
            # 发送 HTTP 请求
            status, body = self._send_request(interaction)
            result.actual_status = status
            result.actual_body = body

            # 状态码校验
            if status != interaction.response_status:
                raise ContractViolationError(
                    contract_name, "$.status", interaction.response_status, status
                )

            # 响应体字段校验
            if interaction.response_body_fields:
                FieldValidator.validate(
                    contract_name, body, interaction.response_body_fields
                )

            result.passed = True

        except ContractViolationError as e:
            result.error = str(e)
            logger.warning(f"契约验证失败: {e}")
        except Exception as e:
            result.error = f"{type(e).__name__}: {e} [error_code=PROVIDER_VERIFY_ERROR]"
            logger.error(f"Provider 验证异常: {e}", exc_info=True)

        result.duration_ms = round((time.time() - start) * 1000, 2)
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "contract_verification",
            "action": "verify.complete",
            "contract": contract_name,
            "interaction": interaction.description,
            "passed": result.passed,
            "duration_ms": result.duration_ms,
        }, ensure_ascii=False))
        return result

    def _send_request(self, interaction: Interaction) -> Tuple[int, Any]:
        """发送 HTTP 请求，返回 (status_code, response_body)"""
        try:
            import requests
        except ImportError as e:
            raise ImportError(
                "requests 未安装，请运行: pip install requests "
                "[error_code=DEPENDENCY_MISSING]"
            ) from e

        url = self.base_url + interaction.request_path
        # 拼接 query 参数
        if interaction.request_query:
            query_str = "&".join(
                f"{k}={v}" for k, v in interaction.request_query.items()
            )
            url = f"{url}?{query_str}"

        # 构造请求体
        json_body = None
        if interaction.request_body_fields:
            json_body = _fields_to_example(interaction.request_body_fields)

        headers = {"Content-Type": "application/json"}
        headers.update(interaction.request_headers)

        try:
            if interaction.request_method == "GET":
                resp = requests.get(url, headers=headers, timeout=self.timeout)
            elif interaction.request_method == "POST":
                resp = requests.post(url, json=json_body, headers=headers, timeout=self.timeout)
            elif interaction.request_method == "PUT":
                resp = requests.put(url, json=json_body, headers=headers, timeout=self.timeout)
            elif interaction.request_method == "DELETE":
                resp = requests.delete(url, headers=headers, timeout=self.timeout)
            else:
                raise ValueError(f"不支持的 HTTP 方法: {interaction.request_method}")
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(
                f"无法连接到 Provider ({self.base_url}): {e} "
                f"[error_code=PROVIDER_UNREACHABLE]"
            ) from e
        except requests.exceptions.Timeout as e:
            raise TimeoutError(
                f"Provider 响应超时 ({self.timeout}s): {e} "
                f"[error_code=PROVIDER_TIMEOUT]"
            ) from e

        try:
            body = resp.json()
        except ValueError:
            body = {"_raw_text": resp.text}

        return resp.status_code, body


# ═══════════════════════════════════════════════════════════════
#  契约持久化
# ═══════════════════════════════════════════════════════════════

def save_contract(contract: Contract, output_dir: Path) -> Tuple[Path, Path]:
    """保存契约为 Pact JSON + 规格 JSON 两个文件"""
    output_dir.mkdir(parents=True, exist_ok=True)

    pact_path = output_dir / f"{contract.name}_pact.json"
    spec_path = output_dir / f"{contract.name}_contract.json"

    pact_path.write_text(
        json.dumps(contract.to_pact_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    spec_path.write_text(
        json.dumps(contract.to_spec_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"契约已保存: {pact_path.name}, {spec_path.name}")
    return pact_path, spec_path


def load_contract(spec_path: Path) -> Dict[str, Any]:
    """加载契约规格 JSON"""
    if not spec_path.exists():
        raise ContractNotFoundError(f"契约文件不存在: {spec_path}")
    return json.loads(spec_path.read_text(encoding="utf-8"))


def save_verification_report(
    contract_name: str,
    results: List[VerificationResult],
    output_dir: Path,
) -> Path:
    """保存验证结果报告"""
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{contract_name}_verification.json"

    report = {
        "contract_name": contract_name,
        "timestamp": datetime.now().isoformat(),
        "trace_id": _trace_id(),
        "total_interactions": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "overall_passed": all(r.passed for r in results),
        "results": [
            {
                "interaction": r.interaction_description,
                "passed": r.passed,
                "error": r.error,
                "duration_ms": r.duration_ms,
                "actual_status": r.actual_status,
            }
            for r in results
        ],
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report_path
