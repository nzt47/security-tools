"""ToolDescriptor 校验器（TASK-S1-01）

强制不变量（v7.2 §3.2 / §3.2 约束行）：

    I1  destructive 三件套：risk_level=destructive ⇒ requires_approval=True ∧
        undo_hint 非空 ∧ compensating_action 非空（缺一即拒绝）；
    I2  secret ⇒ 禁外部端点：data_class=secret ⇒ origin.external_endpoint=False；
    I3  borrowed ⇒ 必记完整轨迹：evolution.stage=borrowed ⇒ trace_policy 非空；
    I4  ID 规则：capability_id 必须匹配 cp.<source_id>.<upstream_id>（格式层）
        （模型字段校验已兜底，此处复核并给出结构化结果）；
    I5  cp.hint 注记（P7.2-24）：input_schema 字段级 "cp.hint" 键值必须是 dict。

分级设计：
- ``validate_descriptor()`` 永不抛异常，返回 DescriptorValidationResult（valid/errors/
  warnings），供批量检查（registry.load）与 UI 展示；
- 三不变量与 I4/I5 违例进 errors ⇒ valid=False；
- "未回填"项（risk_level/data_class/stage 为 None、provenance=unknown 等）进 warnings，
  提示 S1-02 回填目标，不阻断（与 S0-02 存量零迁移裁定一致）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from .models import (
    CP_HINT_KEY,
    DataClass,
    DescriptorValidationError,
    EvolutionStage,
    ProvenanceLevel,
    RiskLevel,
    ToolDescriptor,
    _ID_RE,
)

# 三不变量错误文案模板（便于测试与审计对账）
ERR_DESTRUCTIVE_APPROVAL = "risk=destructive ⇒ requires_approval 必须为 True"
ERR_DESTRUCTIVE_UNDO = "risk=destructive ⇒ governance.undo_hint 必填"
ERR_DESTRUCTIVE_COMPENSATION = "risk=destructive ⇒ governance.compensating_action 必填"
ERR_SECRET_EXTERNAL = "data_class=secret ⇒ 禁止声明外部端点 (origin.external_endpoint=False)"
ERR_BORROWED_TRACE = "stage=borrowed ⇒ evolution.trace_policy（轨迹引用策略）必填非空"
ERR_ID_FORMAT = "capability_id 必须符合 ID 规则 cp.<source_id>.<upstream_id>"
ERR_CP_HINT_TYPE = "input_schema 字段级 cp.hint 注记必须是 dict（P7.2-24）"
ERR_SCHEMA_NOT_DICT = "input_schema/output_schema 必须是 JSON Object（dict）"


@dataclass
class DescriptorValidationResult:
    """校验结果（valid=False 时 errors 非空）"""

    descriptor_id: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> Dict[str, Any]:
        return {
            "descriptor_id": self.descriptor_id,
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
        }

    def __bool__(self) -> bool:
        return self.valid


def _coerce(descriptor: Union[ToolDescriptor, Dict[str, Any]]) -> ToolDescriptor:
    """把 dict/模型规整为模型；域错误抛 DescriptorValidationError"""
    if isinstance(descriptor, ToolDescriptor):
        return descriptor
    if isinstance(descriptor, dict):
        try:
            return ToolDescriptor(**descriptor)
        except Exception as e:  # pydantic ValidationError
            raise DescriptorValidationError(
                [f"模型域校验失败: {e}"],
                descriptor_id=descriptor.get("meta", {}).get("id")
                if isinstance(descriptor.get("meta"), dict) else None,
            ) from e
    raise DescriptorValidationError(["descriptor 必须是 ToolDescriptor 或 dict"])


def _check_destructive(d: ToolDescriptor, errors: List[str]) -> None:
    if d.trust.risk_level != RiskLevel.DESTRUCTIVE:
        return
    if not d.trust.requires_approval:
        errors.append(ERR_DESTRUCTIVE_APPROVAL)
    if not (d.governance.undo_hint or "").strip():
        errors.append(ERR_DESTRUCTIVE_UNDO)
    if not (d.governance.compensating_action or "").strip():
        errors.append(ERR_DESTRUCTIVE_COMPENSATION)


def _check_secret_external(d: ToolDescriptor, errors: List[str]) -> None:
    if d.trust.data_class == DataClass.SECRET and d.origin.external_endpoint:
        errors.append(ERR_SECRET_EXTERNAL)


def _check_borrowed_trace(d: ToolDescriptor, errors: List[str]) -> None:
    if d.evolution.stage == EvolutionStage.BORROWED:
        if not (d.evolution.trace_policy or "").strip():
            errors.append(ERR_BORROWED_TRACE)


def _check_id_format(d: ToolDescriptor, errors: List[str]) -> None:
    cid = d.meta.id
    if not cid or not _ID_RE.match(cid):
        errors.append(f"{ERR_ID_FORMAT}（got: {cid!r}）")
        return
    # 附加语义复核：source_id / upstream_id 两段均可解析
    rest = cid[len("cp."):]
    if "." not in rest:
        errors.append(f"{ERR_ID_FORMAT}（缺少上游段，got: {cid!r}）")
    else:
        src, _, upstream = rest.partition(".")
        if not src or not upstream:
            errors.append(f"{ERR_ID_FORMAT}（存在空段，got: {cid!r}）")


def _check_cp_hints(d: ToolDescriptor, errors: List[str]) -> None:
    schema = d.capability.input_schema
    if not isinstance(schema, dict):
        errors.append(ERR_SCHEMA_NOT_DICT)
        return
    props = schema.get("properties")
    if props is None:
        return
    if not isinstance(props, dict):
        errors.append(ERR_SCHEMA_NOT_DICT)
        return
    for prop_name, prop_schema in props.items():
        if isinstance(prop_schema, dict) and CP_HINT_KEY in prop_schema:
            if not isinstance(prop_schema[CP_HINT_KEY], dict):
                errors.append(
                    f"{ERR_CP_HINT_TYPE}（属性 {prop_name!r} 的 cp.hint 非 dict）"
                )


def _collect_warnings(d: ToolDescriptor) -> List[str]:
    warnings: List[str] = []
    if d.trust.risk_level is None:
        warnings.append("trust.risk_level 未回填（None=未评估，S1-02 回填目标）")
    if d.trust.data_class is None:
        warnings.append("trust.data_class 未回填（None=未分级，S1-02 回填目标）")
    if d.evolution.stage is None:
        warnings.append("evolution.stage 未入轨（None=缺证据待 S3 补验，S0-02 §3.4）")
    if d.origin.provenance == ProvenanceLevel.UNKNOWN:
        warnings.append("origin.provenance=unknown（仅可人工单步执行，§2.3）")
    if d.trust.requires_approval and not d.governance.undo_hint:
        warnings.append("requires_approval=True 但未声明 undo_hint（建议补全）")
    # 质量组一致性提示
    if d.quality.sample_count > 0 and d.quality.success_rate == 0.0:
        warnings.append(
            f"quality.sample_count={d.quality.sample_count} 但 success_rate=0，"
            "指标疑似未回填（S2 埋点前置）"
        )
    return warnings


def validate_descriptor(
    descriptor: Union[ToolDescriptor, Dict[str, Any]],
) -> DescriptorValidationResult:
    """校验单个 descriptor，返回结构化结果（不抛异常）。

    域错误（dict 无法建模）以 DescriptorValidationResult.errors 表达；
    三不变量（I1-I3）、ID 规则（I4）、cp.hint（I5）违例进 errors。
    """
    try:
        d = _coerce(descriptor)
    except DescriptorValidationError as e:
        return DescriptorValidationResult(
            descriptor_id=e.descriptor_id,
            errors=list(e.errors),
            warnings=list(e.warnings),
        )

    errors: List[str] = []
    _check_id_format(d, errors)
    _check_destructive(d, errors)
    _check_secret_external(d, errors)
    _check_borrowed_trace(d, errors)
    _check_cp_hints(d, errors)

    for name in ("input_schema", "output_schema"):
        schema = getattr(d.capability, name)
        if schema is not None and not isinstance(schema, dict):
            errors.append(f"{name} 必须是 dict")

    return DescriptorValidationResult(
        descriptor_id=d.capability_id,
        errors=errors,
        warnings=_collect_warnings(d),
    )


def assert_valid(descriptor: Union[ToolDescriptor, Dict[str, Any]]) -> ToolDescriptor:
    """校验并返回规整后的 ToolDescriptor；失败抛 DescriptorValidationError。

    供 registry 写前校验 / update_trust / mark_provenance 等写入 API 使用。
    """
    d = _coerce(descriptor)
    result = validate_descriptor(d)
    if not result.valid:
        raise DescriptorValidationError(
            result.errors,
            descriptor_id=result.descriptor_id,
            warnings=result.warnings,
        )
    return d
