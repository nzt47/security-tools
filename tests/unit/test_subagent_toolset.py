"""TASK-S4-04 §5.7 机制 3 工具裁剪子集 单元测试（对齐 S4-01 Actor 矩阵）

覆盖：
- 裁剪子集 = 申请 ∩ 授权子集 − 矩阵拒绝项；**不存在「申请即获得」路径**
- §5.7 机制 3 点名三类（记忆读写 / 核心改写 / 审批权）**全部不可用**
- §7.0 矩阵对齐：拒绝原因来自 ``agent.security.actor_matrix.decide``（单一权威）
- **间接调用路径**（包装名 / 别名槽位 / 别名列表）同样被拒
- 未登记的受保护类别 fail-closed 拒绝
- ``invoke`` / ``require`` 在调用点拦截；``as_manifest`` 只含白名单
"""

from __future__ import annotations

import pytest

from agent.security.actor_matrix import (
    ACTOR_SUB_AGENT,
    OP_APPROVE,
    OP_EXECUTE_CAPABILITY,
    OP_WRITE_MEMORY,
    PermissionContext,
    decide,
    infer_actor_type,
)
from agent.subagent.toolset import (
    E_TOOL_NOT_AUTHORIZED,
    PROTECTED_TOOL_PREFIXES,
    SubAgentToolset,
    ToolNotAuthorized,
    name_candidates,
    normalize_tool_name,
    tool_spec_names,
)

SAFE_TOOLS = ("read_file", "search_docs", "summarize")


def make_toolset(requested=None, authorized=None, **kwargs) -> SubAgentToolset:
    return SubAgentToolset.build(
        requested if requested is not None else SAFE_TOOLS,
        authorized if authorized is not None else SAFE_TOOLS,
        actor="sub_agent:dlg-test", **kwargs)


# ════════════════════════════════════════════════════════════
#  名称规范化与间接路径展开
# ════════════════════════════════════════════════════════════


class TestNameNormalisation:
    def test_lowercase_and_trim(self):
        assert normalize_tool_name("  Read_File ") == "read_file"

    def test_hyphen_and_space_unified_to_underscore(self):
        assert normalize_tool_name("read-file") == "read_file"
        assert normalize_tool_name("read file") == "read_file"

    def test_plain_name_single_candidate(self):
        assert name_candidates("read_file") == ("read_file",)

    def test_wrapped_name_yields_suffix_candidates(self):
        candidates = name_candidates("mcp:filesystem::memory.write")
        assert "memory.write" in candidates
        assert "write" in candidates

    def test_namespaced_dotted_name(self):
        candidates = name_candidates("mcp:fs.memory.write")
        assert "memory.write" in candidates

    def test_empty_name_yields_nothing(self):
        assert name_candidates("") == ()
        assert name_candidates(None) == ()

    def test_spec_names_from_string(self):
        assert tool_spec_names("read_file") == ("read_file",)

    def test_spec_names_scans_alias_slot(self):
        names = tool_spec_names({"name": "read_file", "alias": "memory.write"})
        assert "read_file" in names
        assert "memory.write" in names

    def test_spec_names_scans_alias_list(self):
        names = tool_spec_names({"target": ["a", "b"]})
        assert names == ("a", "b")

    def test_spec_names_recurses_into_lists(self):
        names = tool_spec_names([{"name": "x"}, {"name": "y"}])
        assert names == ("x", "y")

    def test_protected_prefixes_cover_mechanism_3(self):
        """§5.7 机制 3 点名的三类必须在受保护前缀里"""
        for prefix in ("memory.", "approval.", "core."):
            assert prefix in PROTECTED_TOOL_PREFIXES


# ════════════════════════════════════════════════════════════
#  裁剪子集
# ════════════════════════════════════════════════════════════


class TestTrimmedSubset:
    def test_safe_tools_visible(self):
        assert make_toolset().visible_tools() == SAFE_TOOLS

    def test_tool_outside_authorized_subset_denied(self):
        toolset = make_toolset(requested=["read_file", "delete_repo"],
                               authorized=["read_file"])
        assert toolset.visible_tools() == ("read_file",)
        assert toolset.allows("delete_repo") is False

    def test_denied_reason_mentions_authorized_subset(self):
        toolset = make_toolset(requested=["delete_repo"], authorized=["read_file"])
        decision = toolset.evaluate("delete_repo")
        assert "授权子集" in decision.reason
        assert decision.matrix_scope == "authorized_subset"

    def test_empty_subset_means_no_tools(self):
        """fail-closed：未声明授权子集 → 一个工具都不可用"""
        toolset = make_toolset(requested=["read_file"], authorized=[])
        assert toolset.visible_tools() == ()
        assert toolset.allows("read_file") is False

    def test_report_records_requested_visible_denied(self):
        toolset = make_toolset(requested=["read_file", "memory.write"],
                               authorized=["read_file", "memory.write"])
        assert toolset.report.requested == ("read_file", "memory.write")
        assert toolset.report.visible == ("read_file",)
        assert len(toolset.report.denied) == 1

    def test_manifest_excludes_denied_tools(self):
        toolset = make_toolset(requested=["read_file", "memory.write"],
                               authorized=["read_file", "memory.write"])
        manifest = toolset.as_manifest()
        assert manifest["tools"] == ["read_file"]
        assert "memory.write" not in manifest["tools"]
        assert manifest["denied_count"] == 1

    def test_duplicate_requests_deduplicated(self):
        toolset = make_toolset(requested=["read_file", "read_file"], authorized=["read_file"])
        assert toolset.visible_tools() == ("read_file",)

    def test_actor_default_is_sub_agent_prefixed(self):
        assert make_toolset().actor.startswith("sub_agent:")
        assert infer_actor_type(make_toolset().actor) == ACTOR_SUB_AGENT


# ════════════════════════════════════════════════════════════
#  §5.7 机制 3 三类：记忆读写 / 核心改写 / 审批权
# ════════════════════════════════════════════════════════════


class TestMechanism3CategoriesDenied:
    @pytest.mark.parametrize("tool", [
        "memory.read", "memory.recall", "memory.search", "memory.query", "memory.list",
    ])
    def test_memory_read_denied(self, tool):
        assert make_toolset(requested=[tool], authorized=[tool]).allows(tool) is False

    @pytest.mark.parametrize("tool", [
        "memory.write", "memory.forget", "memory.delete", "memory.update", "memory.promote",
    ])
    def test_memory_write_denied(self, tool):
        assert make_toolset(requested=[tool], authorized=[tool]).allows(tool) is False

    @pytest.mark.parametrize("tool", [
        "approval.approve", "approval.deny", "approval.reject", "approval.submit",
    ])
    def test_approval_tools_denied(self, tool):
        """§5.7 机制 3「不含审批权」+ S4-01 §7.0「sub_agent 不可 approve/reject」"""
        assert make_toolset(requested=[tool], authorized=[tool]).allows(tool) is False

    @pytest.mark.parametrize("tool", [
        "core.rewrite", "core.modify", "core.switch", "core.patch",
        "prompt.rewrite", "self_rewrite",
    ])
    def test_core_rewrite_denied(self, tool):
        assert make_toolset(requested=[tool], authorized=[tool]).allows(tool) is False

    @pytest.mark.parametrize("tool", [
        "governance.switch_forge", "governance.modify_policy",
        "governance.force_stage", "governance.remove_source",
    ])
    def test_governance_write_denied(self, tool):
        assert make_toolset(requested=[tool], authorized=[tool]).allows(tool) is False

    def test_denied_even_when_explicitly_authorized(self):
        """矩阵拒绝行**优先于**授权子集：显式授权也不能解锁禁区（fail-closed）"""
        toolset = make_toolset(requested=["memory.write"], authorized=["memory.write"])
        decision = toolset.evaluate("memory.write")
        assert decision.allowed is False
        assert decision.in_authorized_subset is True
        assert "§7.0 矩阵拒绝" in decision.reason

    def test_denial_reason_carries_matrix_operation(self):
        decision = make_toolset(requested=["approval.approve"],
                                authorized=["approval.approve"]).evaluate("approval.approve")
        assert decision.matrix_operation == OP_APPROVE
        assert decision.matrix_scope == "none"

    def test_unregistered_protected_prefix_is_fail_closed(self):
        """未登记映射的受保护类别同样拒绝（「未登记」≠「允许」）"""
        decision = make_toolset(requested=["memory.dream"],
                                authorized=["memory.dream"]).evaluate("memory.dream")
        assert decision.allowed is False
        assert "fail-closed" in decision.reason

    def test_empty_tool_name_denied(self):
        assert make_toolset().allows("") is False
        assert make_toolset().allows(None) is False


# ════════════════════════════════════════════════════════════
#  与 S4-01 矩阵一致性（单一权威）
# ════════════════════════════════════════════════════════════


class TestActorMatrixAlignment:
    def test_capability_execute_uses_authorized_subset_scope(self):
        ctx = PermissionContext(actor="sub_agent:x", actor_type=ACTOR_SUB_AGENT,
                                authorized_capabilities=frozenset({"read_file"}))
        allowed = decide(OP_EXECUTE_CAPABILITY, ctx, object_id="read_file")
        denied = decide(OP_EXECUTE_CAPABILITY, ctx, object_id="other")
        assert allowed.allowed is True
        assert allowed.scope == "authorized_subset"
        assert denied.allowed is False

    def test_memory_write_denied_by_matrix(self):
        ctx = PermissionContext(actor="sub_agent:x", actor_type=ACTOR_SUB_AGENT)
        assert decide(OP_WRITE_MEMORY, ctx).allowed is False

    def test_toolset_denial_matches_matrix_denial(self):
        """工具层的拒绝与矩阵判定结论一致（不出现「工具放行、矩阵拒绝」的分裂）"""
        toolset = make_toolset(requested=["approval.approve"], authorized=["approval.approve"])
        assert toolset.evaluate("approval.approve").allowed is False
        ctx = PermissionContext(actor=toolset.actor, actor_type=ACTOR_SUB_AGENT)
        assert decide(OP_APPROVE, ctx).allowed is False

    def test_toolset_allowance_matches_matrix_allowance(self):
        toolset = make_toolset(requested=["read_file"], authorized=["read_file"])
        assert toolset.evaluate("read_file").allowed is True
        ctx = PermissionContext(actor=toolset.actor, actor_type=ACTOR_SUB_AGENT,
                                authorized_capabilities=frozenset({"read_file"}))
        assert decide(OP_EXECUTE_CAPABILITY, ctx, object_id="read_file").allowed is True


# ════════════════════════════════════════════════════════════
#  间接调用路径
# ════════════════════════════════════════════════════════════


class TestIndirectCallPaths:
    @pytest.mark.parametrize("wrapped", [
        "mcp:filesystem::memory.write",
        "sub_agent/approval.approve",
        "mcp:fs.memory.write",
        "builtin|memory.write",
        "core.rewrite->alias",
    ])
    def test_wrapped_names_denied(self, wrapped):
        toolset = make_toolset(requested=[wrapped], authorized=[wrapped])
        assert toolset.allows(wrapped) is False

    def test_wrapped_name_reports_matched_inner_name(self):
        toolset = make_toolset(requested=["mcp:fs::memory.write"],
                               authorized=["mcp:fs::memory.write"])
        decision = toolset.evaluate("mcp:fs::memory.write")
        assert decision.matched == "memory.write"

    def test_alias_slot_denied(self):
        toolset = make_toolset(requested=["read_file"], authorized=["read_file"])
        decision = toolset.check_spec({"name": "read_file", "alias": "memory.write"})
        assert decision.allowed is False
        assert decision.in_authorized_subset is False

    def test_alias_list_slot_denied(self):
        toolset = make_toolset(requested=["read_file"], authorized=["read_file"])
        assert toolset.check_spec({"name": "read_file",
                                   "aliases": ["read_file", "approval.approve"]}).allowed is False

    def test_redirect_slot_denied(self):
        toolset = make_toolset(requested=["read_file"], authorized=["read_file"])
        assert toolset.check_spec({"name": "read_file",
                                   "redirect": "core.rewrite"}).allowed is False

    def test_clean_spec_allowed(self):
        toolset = make_toolset(requested=["read_file"], authorized=["read_file"])
        decision = toolset.check_spec({"name": "read_file", "tool": "read_file"})
        assert decision.allowed is True

    def test_spec_without_recognisable_name_denied(self):
        toolset = make_toolset()
        assert toolset.check_spec({"unrelated": 1}).allowed is False

    def test_spec_string_form(self):
        toolset = make_toolset(requested=["read_file"], authorized=["read_file"])
        assert toolset.check_spec("read_file").allowed is True
        assert toolset.check_spec("memory.write").allowed is False

    def test_require_spec_raises_with_matched_name(self):
        toolset = make_toolset(requested=["read_file"], authorized=["read_file"])
        with pytest.raises(ToolNotAuthorized) as excinfo:
            toolset.require_spec({"name": "read_file", "target": "memory.write"})
        assert excinfo.value.matched == "memory.write"
        assert excinfo.value.code == E_TOOL_NOT_AUTHORIZED


# ════════════════════════════════════════════════════════════
#  调用点闸门
# ════════════════════════════════════════════════════════════


class TestInvocationGate:
    def test_require_returns_normalised_name(self):
        toolset = make_toolset(requested=["Read-File"], authorized=["Read-File"])
        assert toolset.require("Read-File") == "read_file"

    def test_require_raises_on_denied(self):
        toolset = make_toolset(requested=["read_file"], authorized=["read_file"])
        with pytest.raises(ToolNotAuthorized) as excinfo:
            toolset.require("memory.write")
        assert excinfo.value.tool == "memory.write"
        assert excinfo.value.to_dict()["code"] == E_TOOL_NOT_AUTHORIZED

    def test_invoke_calls_allowed_function(self):
        called = []

        def fn(value):
            called.append(value)
            return value * 2

        toolset = make_toolset(requested=["read_file"], authorized=["read_file"])
        assert toolset.invoke("read_file", fn, 21) == 42
        assert called == [21]

    def test_invoke_refuses_denied_function(self):
        """越界在**调用点**被拒：函数体根本不会执行"""
        def fn():  # pragma: no cover - 不应被执行
            raise AssertionError("越界工具不应被调用")

        toolset = make_toolset(requested=["read_file"], authorized=["read_file"])
        with pytest.raises(ToolNotAuthorized):
            toolset.invoke("memory.write", fn)
        with pytest.raises(ToolNotAuthorized):
            toolset.invoke("approval.approve", fn)


class TestDecisionSerialisation:
    def test_decision_to_dict_shape(self):
        decision = make_toolset(requested=["read_file"],
                                authorized=["read_file"]).evaluate("read_file")
        payload = decision.to_dict()
        assert payload["allowed"] is True
        assert payload["matrix_operation"] == OP_EXECUTE_CAPABILITY
        assert payload["in_authorized_subset"] is True

    def test_report_to_dict_shape(self):
        toolset = make_toolset(requested=["read_file", "memory.write"],
                               authorized=["read_file", "memory.write"])
        payload = toolset.report.to_dict()
        assert payload["denied_count"] == 1
        assert payload["visible"] == ["read_file"]
