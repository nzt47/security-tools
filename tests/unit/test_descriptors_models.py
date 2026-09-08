"""agent/descriptors/models.py — 模型字段校验测试（TASK-S1-01）"""

import json

import pytest
from pydantic import ValidationError

from agent.descriptors.models import (
    DataClass,
    DescriptorValidationError,
    EvolutionStage,
    MetaInfo,
    ProvenanceLevel,
    RiskLevel,
    SourceType,
    ToolDescriptor,
    extract_cp_hints,
)
from descriptors_util import make_descriptor


class TestEnumDomains:
    """枚举值域与文档一致（§3.2 / S0-02 §3.2）"""

    def test_provenance_four_levels(self):
        assert [e.value for e in ProvenanceLevel] == \
            ["unknown", "declared", "verified", "signed"]

    def test_risk_four_levels(self):
        assert [e.value for e in RiskLevel] == \
            ["low", "medium", "high", "destructive"]

    def test_data_class_four_levels(self):
        assert [e.value for e in DataClass] == \
            ["public", "internal", "confidential", "secret"]

    def test_evolution_seven_states(self):
        assert [e.value for e in EvolutionStage] == [
            "borrowed", "mirrored", "shadow", "internalized",
            "native", "permanent_borrowed", "deprecated",
        ]

    def test_source_type_covers_runtime_and_adapters(self):
        values = {e.value for e in SourceType}
        assert {"builtin", "mcp", "skill", "plugin", "generated", "market",
                "cli", "subagent", "sdk", "rest", "manual"} <= values


class TestIdFormat:
    """ID 规则 cp.<source_id>.<upstream_id>"""

    def test_valid_simple(self):
        d = make_descriptor(cid="cp.filesystem.write_file")
        assert d.capability_id == "cp.filesystem.write_file"

    def test_valid_dotted_upstream(self):
        d = make_descriptor(cid="cp.filesystem.github.write")
        assert d.capability_id == "cp.filesystem.github.write"

    def test_invalid_missing_cp_prefix(self):
        with pytest.raises(ValidationError):
            make_descriptor(cid="filesystem.write")

    def test_invalid_empty_source(self):
        with pytest.raises(ValidationError):
            make_descriptor(cid="cp..write")

    def test_invalid_empty_upstream(self):
        with pytest.raises(ValidationError):
            make_descriptor(cid="cp.filesystem.")

    def test_invalid_whitespace(self):
        with pytest.raises(ValidationError):
            make_descriptor(cid="cp.filesystem.write file")

    def test_invalid_short(self):
        with pytest.raises(ValidationError):
            make_descriptor(cid="cp.ab")


class TestVersion:
    def test_valid_semver(self):
        assert make_descriptor(version="1.2.3").meta.version == "1.2.3"

    def test_invalid_semver(self):
        with pytest.raises(ValidationError):
            make_descriptor(version="v1.0")


class TestDefaultsAndRanges:
    def test_default_groups_are_neutral(self):
        """未回填语义：risk/data_class/stage=None、provenance=unknown"""
        d = make_descriptor()
        assert d.trust.risk_level is None
        assert d.trust.data_class is None
        assert d.evolution.stage is None
        assert d.origin.provenance == ProvenanceLevel.UNKNOWN
        assert d.trust.requires_approval is False
        assert d.tenancy.tenant_id == "default"
        assert d.governance.audit_level.value == "summary"

    def test_quality_range_violation(self):
        with pytest.raises(ValidationError):
            make_descriptor(success_rate=1.5)
        with pytest.raises(ValidationError):
            make_descriptor(sample_count=-1)

    def test_timeout_range_violation(self):
        with pytest.raises(ValidationError):
            make_descriptor(timeout_ms=-5)

    def test_requires_approval_flag_roundtrip(self):
        d = make_descriptor(approval=True)
        assert d.trust.requires_approval is True

    def test_retry_policy_defaults(self):
        d = make_descriptor()
        rp = d.runtime.retry_policy
        assert rp.mode.value == "none"
        assert rp.max_retries == 0


class TestSerialization:
    def test_storage_roundtrip_keeps_enums_as_strings(self):
        d = make_descriptor(
            cid="cp.fs.write", source_type="mcp", prov="declared",
            risk="high", stage="borrowed", trace_policy="trace:x",
        )
        payload = d.to_storage_dict()
        raw = json.dumps(payload, ensure_ascii=False)
        assert '"risk_level": "high"' in raw
        assert '"provenance": "declared"' in raw
        d2 = ToolDescriptor.from_storage_dict(json.loads(raw))
        assert d2.capability_id == d.capability_id
        assert d2.trust.risk_level == RiskLevel.HIGH
        assert d2.evolution.stage == EvolutionStage.BORROWED

    def test_unknown_top_level_keys_ignored(self):
        d = ToolDescriptor(**{**make_descriptor().model_dump(), "future": 1})
        assert not hasattr(d, "future")

    def test_model_extra_ignored_nested(self):
        payload = make_descriptor().to_storage_dict()
        payload["trust"]["future_flag"] = True
        d = ToolDescriptor(**payload)
        assert d.trust.risk_level is None


class TestCpHintExtraction:
    def test_extracts_dict_hints(self):
        schema = {
            "type": "object",
            "properties": {
                "path": {"type": "string", "cp.hint": {"id": "fs.path"}},
                "mode": {"type": "string"},
            },
        }
        hints = extract_cp_hints(schema)
        assert hints == {"path": {"id": "fs.path"}}

    def test_ignores_non_dict_hint_and_empty(self):
        schema = {"type": "object", "properties": {
            "bad": {"type": "string", "cp.hint": "oops"}}}
        assert extract_cp_hints(schema) == {}
        assert extract_cp_hints({}) == {}
        assert extract_cp_hints(None) == {}


class TestErrorType:
    def test_validation_error_carries_errors_and_id(self):
        err = DescriptorValidationError(
            ["a", "b"], descriptor_id="cp.x.y", warnings=["w"])
        assert err.errors == ["a", "b"]
        assert err.warnings == ["w"]
        assert err.descriptor_id == "cp.x.y"
        assert err.code == "INVALID_DESCRIPTOR"
        assert "cp.x.y" in str(err)
        assert "a" in str(err)

    def test_meta_id_required(self):
        with pytest.raises(ValidationError):
            MetaInfo()  # id 必填
