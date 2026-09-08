"""agent/descriptors/validator.py — 不变量校验测试（TASK-S1-01）

覆盖三不变量：destructive 三件套必填 / secret 禁外部端点 / borrowed 必记轨迹策略；
ID 规则；cp.hint 注记；warnings（未回填项）；validate/assert 分层。
"""

import pytest

from agent.descriptors.models import (
    DataClass,
    DescriptorValidationError,
    EvolutionStage,
    ProvenanceLevel,
    RiskLevel,
    ToolDescriptor,
)
from agent.descriptors.validator import (
    DescriptorValidationResult,
    assert_valid,
    validate_descriptor,
)
from agent.descriptors.validator import (
    ERR_BORROWED_TRACE,
    ERR_DESTRUCTIVE_APPROVAL,
    ERR_DESTRUCTIVE_COMPENSATION,
    ERR_DESTRUCTIVE_UNDO,
    ERR_SECRET_EXTERNAL,
)
from descriptors_util import make_descriptor


class TestMinimalValid:
    def test_minimal_descriptor_valid_with_warnings(self):
        result = validate_descriptor(make_descriptor())
        assert result.valid
        assert result.errors == []
        # 未回填项 → warning（S1-02 目标）
        texts = " | ".join(result.warnings)
        assert "risk_level" in texts and "data_class" in texts

    def test_accepts_dict_input(self):
        d = make_descriptor()
        result = validate_descriptor(d.to_storage_dict())
        assert result.valid
        assert result.descriptor_id == d.capability_id

    def test_result_shapes(self):
        result = validate_descriptor(make_descriptor())
        assert isinstance(result, DescriptorValidationResult)
        payload = result.as_dict()
        assert set(payload) == {
            "descriptor_id", "valid", "errors", "warnings",
            "error_count", "warning_count",
        }
        assert bool(result) is True

    def test_validate_never_raises_on_garbage(self):
        result = validate_descriptor({"meta": {"id": "cp.a.b"}})
        assert result.valid is False
        assert result.errors  # 模型域缺失字段 → errors


class TestDestructiveInvariant:
    """I1: risk=destructive ⇒ requires_approval ∧ undo_hint ∧ compensating_action"""

    def test_destructive_missing_approval_rejected(self):
        d = make_descriptor(risk="destructive", approval=False,
                            undo="u", comp="c")
        result = validate_descriptor(d)
        assert not result.valid
        assert ERR_DESTRUCTIVE_APPROVAL in result.errors

    def test_destructive_missing_undo_rejected(self):
        d = make_descriptor(risk="destructive", approval=True,
                            undo="", comp="c")
        result = validate_descriptor(d)
        assert not result.valid
        assert ERR_DESTRUCTIVE_UNDO in result.errors

    def test_destructive_missing_compensation_rejected(self):
        d = make_descriptor(risk="destructive", approval=True,
                            undo="u", comp="")
        result = validate_descriptor(d)
        assert not result.valid
        assert ERR_DESTRUCTIVE_COMPENSATION in result.errors

    def test_destructive_triple_complete_accepted(self):
        d = make_descriptor(risk="destructive", approval=True,
                            undo="备份", comp="恢复", description="删除")
        result = validate_descriptor(d)
        assert result.valid, result.errors

    def test_non_destructive_does_not_need_triple(self):
        d = make_descriptor(risk="high", approval=False, undo="", comp="")
        assert validate_descriptor(d).valid

    def test_assert_valid_raises_with_code_and_id(self):
        d = make_descriptor(risk="destructive", approval=False,
                            undo="u", comp="c")
        with pytest.raises(DescriptorValidationError) as exc:
            assert_valid(d)
        assert exc.value.descriptor_id == d.capability_id
        assert ERR_DESTRUCTIVE_APPROVAL in exc.value.errors
        assert exc.value.code == "INVALID_DESCRIPTOR"


class TestSecretInvariant:
    """I2: data_class=secret ⇒ 禁外部端点"""

    def test_secret_with_external_endpoint_rejected(self):
        d = make_descriptor(data_class="secret", external=True)
        result = validate_descriptor(d)
        assert not result.valid
        assert ERR_SECRET_EXTERNAL in result.errors

    def test_secret_internal_accepted(self):
        d = make_descriptor(data_class="secret", external=False)
        assert validate_descriptor(d).valid

    def test_external_non_secret_accepted(self):
        d = make_descriptor(data_class="confidential", external=True)
        assert validate_descriptor(d).valid

    def test_public_external_accepted(self):
        d = make_descriptor(data_class="public", external=True)
        assert validate_descriptor(d).valid


class TestBorrowedInvariant:
    """I3: stage=borrowed ⇒ trace_policy 必填（轨迹引用策略）"""

    def test_borrowed_without_trace_rejected(self):
        d = make_descriptor(stage="borrowed", trace_policy="")
        result = validate_descriptor(d)
        assert not result.valid
        assert ERR_BORROWED_TRACE in result.errors

    def test_borrowed_with_trace_accepted(self):
        d = make_descriptor(stage="borrowed", trace_policy="trace:ledger:x")
        assert validate_descriptor(d).valid

    def test_other_stages_do_not_require_trace(self):
        for stage in ("mirrored", "shadow", "internalized", "native",
                      "permanent_borrowed", "deprecated"):
            d = make_descriptor(stage=stage, trace_policy="")
            assert validate_descriptor(d).valid, stage

    def test_enum_and_none_stage(self):
        d = make_descriptor(stage=EvolutionStage.BORROWED, trace_policy="t:1")
        assert validate_descriptor(d).valid
        d2 = make_descriptor(stage=None)
        assert validate_descriptor(d2).valid


class TestIdAndCpHintChecks:
    def test_invalid_id_reported(self):
        # 'cp.filesystem' 长度合法但缺上游段 → 模型域错误文案含 ID 规则
        result = validate_descriptor({"meta": {"id": "cp.filesystem"},
                                      "origin": {"source_id": "s"},
                                      "capability": {"name": "n"}})
        assert not result.valid
        assert any("cp.<source_id>.<upstream_id>" in e for e in result.errors)

    def test_invalid_id_via_descriptor_object(self):
        from agent.descriptors.validator import _check_id_format

        d = make_descriptor(cid="cp.ok.read")
        errors: list = []
        d.meta.id = "not-cp-format"  # 模型可构造后再改，校验器复核 I4
        _check_id_format(d, errors)
        assert errors

    def test_cp_hint_non_dict_is_error(self):
        schema = {"type": "object", "properties": {
            "path": {"type": "string", "cp.hint": "not-a-dict"}}}
        d = make_descriptor(input_schema=schema)
        result = validate_descriptor(d)
        assert not result.valid
        assert any("cp.hint" in e and "dict" in e for e in result.errors)

    def test_cp_hint_dict_accepted(self):
        schema = {"type": "object", "properties": {
            "path": {"type": "string", "cp.hint": {"id": "fs.path"}}}}
        d = make_descriptor(input_schema=schema)
        assert validate_descriptor(d).valid


class TestWarnings:
    def test_provenance_unknown_warns(self):
        d = make_descriptor(prov=ProvenanceLevel.UNKNOWN)
        assert any("unknown" in w for w in validate_descriptor(d).warnings)

    def test_verified_no_warning_for_provenance(self):
        d = make_descriptor(prov=ProvenanceLevel.VERIFIED, evidence=["code"])
        warnings = validate_descriptor(d).warnings
        assert not any("unknown" in w for w in warnings)

    def test_quality_inconsistency_warns(self):
        d = make_descriptor(sample_count=10, success_rate=0.0)
        assert any("sample_count" in w for w in validate_descriptor(d).warnings)

    def test_approval_without_undo_warns(self):
        d = make_descriptor(approval=True, undo="")
        assert any("undo_hint" in w for w in validate_descriptor(d).warnings)
