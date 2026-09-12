#!/usr/bin/env python3
"""能力最小暴露（§5.7 机制 3 + §7.0）单元测试

覆盖 `agent/guardrails/capability_exposure.py`：
    - §5.7 逐字四类绝对禁项与模式表；
    - 默认闭集的暴露判定（显式授权**不可**覆盖绝对禁项）；
    - 工具集裁剪的四个分区与范围口径；
    - 执行前置闸门与面向 UI 的拒绝解释；
    - 与 S4-01 Actor 矩阵的一致性自检。

【状态隔离】本模块无全局状态；`enforce_scope_consistency` 只读 Actor 矩阵。
"""

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent.guardrails.capability_exposure import (
    ACTOR_SUB_AGENT,
    CapabilityNotExposedError,
    DEFAULT_SUBAGENT_TOOLSET,
    EXECUTION_TOOLS_REQUIRING_GRANT,
    FORBIDDEN_CAPABILITY_CLASSES,
    SCOPE_AUTHORIZED_SUBSET,
    classify_capability,
    enforce_scope_consistency,
    explain_denial,
    exposure_state,
    is_exposed,
    is_forbidden,
    minimal_exposure_contract,
    require_exposed,
    trim_toolset,
)

#: 一条贯穿全篇的绝对禁项能力（审批权）
FORBIDDEN_CAPABILITY = "approval.approve"


class TestConstants:
    """§5.7 机制 3 的清单"""

    def test_forbidden_classes_exactly_four(self):
        """§5.7 逐字三类（记忆读写算两类）+ 审批权"""
        assert FORBIDDEN_CAPABILITY_CLASSES == (
            "memory_read", "memory_write", "core_rewrite", "approval")

    def test_default_toolset_and_grant_lists(self):
        """默认可见集是执行面最小集；执行类工具须显式授权"""
        assert "read_file" in DEFAULT_SUBAGENT_TOOLSET
        assert "write_file" in EXECUTION_TOOLS_REQUIRING_GRANT
        assert not set(DEFAULT_SUBAGENT_TOOLSET) & set(EXECUTION_TOOLS_REQUIRING_GRANT)


class TestClassifyCapability:
    """禁项分类"""

    def test_approval_capability_classified(self):
        """审批类能力命中 approval"""
        assert "approval" in classify_capability(FORBIDDEN_CAPABILITY)
        assert "approval" in classify_capability("approval.submit")

    @pytest.mark.parametrize(
        "capability, expected",
        [
            ("memory.layered_store.write", "memory_write"),
            ("knowledge.search", "memory_read"),
            ("governance.switch_forge", "core_rewrite"),
            ("meta_editor", "core_rewrite"),
            ("forge", "core_rewrite"),
        ],
    )
    def test_classify_expected_class(self, capability, expected):
        """记忆读写 / 核心改写各自命中对应类别"""
        assert expected in classify_capability(capability)

    @pytest.mark.parametrize("capability", ["read_file", "list_dir", "grep", "", None])
    def test_clean_names_have_no_class(self, capability):
        """干净能力名不命中任何禁项类别"""
        assert classify_capability(capability) == []

    @pytest.mark.parametrize("capability, expected",
                             [(FORBIDDEN_CAPABILITY, True), ("read_file", False)])
    def test_is_forbidden(self, capability, expected):
        """is_forbidden 与分类结果一致"""
        assert is_forbidden(capability) is expected


class TestIsExposed:
    """暴露判定（默认闭集）"""

    def test_non_subagent_actor_unaffected(self):
        """非 sub_agent 不受机制 3 裁剪约束（交 Actor 矩阵管）"""
        verdict = is_exposed(FORBIDDEN_CAPABILITY, actor_type="main")
        assert verdict.exposed is True
        assert verdict.classes == []

    def test_forbidden_never_exposed_even_when_authorized(self):
        """绝对禁项**永不**暴露：显式授权也覆盖不了（机制 3 的关键断言）"""
        verdict = is_exposed(FORBIDDEN_CAPABILITY,
                             authorized=[FORBIDDEN_CAPABILITY])
        assert verdict.exposed is False
        assert verdict.classes == ["approval"]
        assert "不可覆盖" in verdict.reason

    def test_default_toolset_is_exposed(self):
        """默认可见集内的读取类工具直接可见"""
        verdict = is_exposed("read_file")
        assert verdict.exposed is True
        assert "默认可见集" in verdict.reason

    def test_only_authorized_name_is_exposed(self):
        """仅出现在显式授权清单里的能力可见（§7.0 authorized_subset）"""
        assert is_exposed("write_file").exposed is False
        authorized = is_exposed("write_file", authorized=["write_file"])
        assert authorized.exposed is True
        assert SCOPE_AUTHORIZED_SUBSET in authorized.reason

    def test_unknown_capability_is_not_exposed(self):
        """未知能力默认不可见（闭集，不是白名单的补集）"""
        verdict = is_exposed("totally_unknown_tool")
        assert verdict.exposed is False
        assert verdict.classes == []

    def test_explicit_toolset_overrides_default(self):
        """显式 toolset 覆盖默认可见集（裁剪契约的可配置面）"""
        verdict = is_exposed("read_file", toolset=["only_this"])
        assert verdict.exposed is False


class TestTrimToolset:
    """工具集裁剪"""

    def test_partitions_and_preserves_request_order(self):
        """四个分区正确，且 allowed 保持请求序"""
        result = trim_toolset(
            ["write_file", "read_file", FORBIDDEN_CAPABILITY, "unknown_tool"],
            authorized=["write_file"],
        )
        assert result["allowed"] == ["write_file", "read_file"]
        assert result["denied"] == [FORBIDDEN_CAPABILITY, "unknown_tool"]
        assert result["forbidden"] == [FORBIDDEN_CAPABILITY]
        assert result["not_authorized"] == ["unknown_tool"]
        assert result["requested_count"] == 4

    def test_scope_is_authorized_subset_for_subagent(self):
        """sub_agent 的范围口径恒为 authorized_subset"""
        assert trim_toolset(["read_file"])["scope"] == SCOPE_AUTHORIZED_SUBSET
        assert trim_toolset(["read_file"])["actor_type"] == ACTOR_SUB_AGENT

    def test_scope_is_all_for_other_actors(self):
        """非 sub_agent 不受裁剪（scope=all）"""
        result = trim_toolset([FORBIDDEN_CAPABILITY, "anything"],
                              actor_type="main")
        assert result["scope"] == "all"
        assert result["forbidden"] == []
        assert len(result["allowed"]) == 2


class TestRequireExposed:
    """执行前置闸门"""

    def test_raises_with_classes_for_forbidden(self):
        """禁项能力被拒且异常自带命中类别"""
        with pytest.raises(CapabilityNotExposedError) as excinfo:
            require_exposed(FORBIDDEN_CAPABILITY)
        assert excinfo.value.classes == ["approval"]
        assert excinfo.value.capability == FORBIDDEN_CAPABILITY
        assert excinfo.value.actor_type == ACTOR_SUB_AGENT

    def test_returns_none_for_exposed(self):
        """已暴露能力静默通过（闸门不返回判定对象）"""
        assert require_exposed("read_file") is None
        assert require_exposed("write_file", authorized=["write_file"]) is None


class TestContractAndConsistency:
    """契约导出与一致性自检"""

    def test_minimal_exposure_contract_keys(self):
        """契约含 S4-04 需要的全部字段，且默认闭集"""
        contract = minimal_exposure_contract()
        assert set(contract) >= {
            "contract_version", "actor_type", "forbidden_classes", "class_labels",
            "forbidden_patterns", "default_toolset", "requires_grant", "scope",
            "closed_by_default", "authorized_subset_required", "source",
        }
        assert contract["closed_by_default"] is True
        assert contract["forbidden_classes"] == list(FORBIDDEN_CAPABILITY_CLASSES)
        assert contract["scope"] == SCOPE_AUTHORIZED_SUBSET

    def test_enforce_scope_consistency_returns_report(self):
        """一致性自检返回 available / ok 等键，且**不抛异常**（矩阵不可得也不失败）"""
        report = enforce_scope_consistency()
        assert set(report) >= {"available", "ok", "stricter_than_matrix",
                               "too_permissive", "matrix_denied_sample"}
        assert isinstance(report["available"], bool)
        # ok 的语义：本模块不得比 Actor 矩阵更宽
        assert report["ok"] is (not report["too_permissive"])

    def test_exposure_state_wraps_contract_and_consistency(self):
        """状态快照 = 契约 + 一致性报告"""
        state = exposure_state()
        assert set(state) == {"contract", "consistency"}
        assert state["contract"]["contract_version"] == "minimal_exposure.v1"


class TestExplainDenial:
    """面向 UI 的拒绝解释"""

    def test_forbidden_explained_as_permanently_invisible(self):
        """禁项 → "永久不可见"（且说明授权不可覆盖）"""
        text = explain_denial(FORBIDDEN_CAPABILITY)
        assert "永久不可见" in text
        assert "不可覆盖" in text

    def test_closed_set_explained_as_default_invisible(self):
        """闭集未授权 → "默认不可见"（与永久不可见可区分）"""
        text = explain_denial("unknown_tool")
        assert "默认不可见" in text
        assert "永久不可见" not in text

    def test_exposed_capability_explained(self):
        """可见能力也被解释（面板统一走同一入口）"""
        assert "可见" in explain_denial("read_file")
