"""agent/descriptors/bridge.py — 读侧桥接 + advisory + 演示测试（TASK-S1-01）"""

import pytest

from agent.descriptors.bridge import (
    BridgeBatchResult,
    bridge_to_skill,
    demo_pipeline,
    descriptor_from_builtin_tool,
    descriptor_from_mcp_tool,
    descriptors_from_builtin,
    descriptors_from_mcp_list,
    map_skill_stage,
    register_bridge_view,
    sanitize_id_part,
    skill_to_descriptor,
)
from agent.descriptors.models import (
    DataClass,
    EvolutionStage,
    ProvenanceLevel,
    RiskLevel,
    SourceType,
)
from agent.descriptors.registry import DescriptorRegistry
from descriptors_util import sample_skill


MCP_READ = {
    "name": "read_file",
    "description": "读取文件内容",
    "inputSchema": {"type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"]},
}
MCP_WRITE = {
    "name": "write_file",
    "description": "写入文件",
    "inputSchema": {"type": "object",
                    "properties": {"path": {"type": "string"},
                                   "content": {"type": "string"}},
                    "required": ["path", "content"]},
    "annotations": {"destructiveHint": True, "idempotentHint": True},
}


class TestSanitize:
    def test_basic(self):
        assert sanitize_id_part("My Tool!") == "my-tool"
        assert sanitize_id_part("read_File") == "read_file"  # 下划线保留（ID 允许）
        assert sanitize_id_part("a..b") == "a..b"  # 点保留（ID 段允许）

    def test_space_and_punct_only_becomes_empty(self):
        assert sanitize_id_part("!!!") == ""
        assert sanitize_id_part("  ") == ""


class TestMCPBridge:
    def test_mcp_tool_to_descriptor(self):
        d = descriptor_from_mcp_tool(MCP_READ, server="filesystem",
                                     transport="stdio")
        assert d.origin.source_type == SourceType.MCP
        assert d.origin.source_id == "filesystem"
        assert d.origin.provenance == ProvenanceLevel.DECLARED
        assert d.evolution.stage == EvolutionStage.BORROWED
        assert d.evolution.trace_policy  # borrowed 轨迹策略默认非空
        assert d.capability_id == "cp.filesystem.read_file"
        assert d.capability.input_schema["required"] == ["path"]
        assert d.origin.external_endpoint is False  # stdio 本地

    def test_mcp_sse_default_external(self):
        d = descriptor_from_mcp_tool(MCP_READ, server="fs", transport="sse")
        assert d.origin.external_endpoint is True

    def test_mcp_idempotent_from_annotations(self):
        d = descriptor_from_mcp_tool(MCP_WRITE, server="fs")
        assert d.runtime.idempotent is True

    def test_mcp_invalid_tool_advisory(self):
        # 名字被清洗为空 → ID 段空 → 建模失败 → advisory（不阻断其余条目）
        result = descriptors_from_mcp_list(
            [MCP_READ, {"name": "!!!"}], server="fs")
        assert isinstance(result, BridgeBatchResult)
        assert result.ok_count == 1
        assert len(result.errors) == 1
        assert result.errors[0].source.startswith("mcp:fs/")

    def test_mcp_register_via_registry(self, tmp_path):
        reg = DescriptorRegistry(tmp_path / "d.json", autosave=False)
        summary = register_bridge_view(reg, "mcp", [MCP_READ, MCP_WRITE],
                                       server="filesystem", transport="stdio")
        assert summary.total == 2
        assert summary.registered == 2
        assert summary.errors == []
        assert reg.count() == 2


class TestBuiltinBridge:
    def test_builtin_tool_descriptor(self):
        d = descriptor_from_builtin_tool(
            "web_search", "联网搜索",
            {"type": "object", "properties": {"q": {"type": "string"}}})
        assert d.origin.source_type == SourceType.BUILTIN
        assert d.origin.provenance == ProvenanceLevel.VERIFIED
        assert d.evolution.stage is None  # native 缺 30 天台账不置位（S0-02）
        assert d.capability_id == "cp.builtin.web_search"
        assert d.trust.risk_level is None  # 未回填（S1-02）

    def test_builtin_batch(self):
        result = descriptors_from_builtin([
            {"name": "a_tool", "description": "x",
             "schema": {"type": "object"}},
            {"name": "broken"},  # 缺 name 之外字段均可容错
        ])
        assert result.ok_count == 2


class TestSkillStageMapping:
    def test_deprecated_status_reuses_lifecycle(self):
        stage, why = map_skill_stage(category="custom", source="manual",
                                     status="deprecated")
        assert stage == EvolutionStage.DEPRECATED
        assert "同义复用" in why

    def test_external_category_borrowed(self):
        stage, why = map_skill_stage(category="claude", source="github:x/y",
                                     status="approved")
        assert stage == EvolutionStage.BORROWED
        assert "外来导入" in why

    def test_external_source_scheme_borrowed(self):
        stage, _ = map_skill_stage(category="custom",
                                   source="url:https://example.com/s.md")
        assert stage == EvolutionStage.BORROWED

    def test_external_agent_borrowed(self):
        stage, _ = map_skill_stage(category="custom", source="install",
                                   author="external_agent")
        assert stage == EvolutionStage.BORROWED

    def test_published_local_no_evidence_stays_none(self):
        stage, why = map_skill_stage(category="builtin", source="manual",
                                     status="published")
        assert stage is None
        assert "published" in why

    def test_local_asset_none(self):
        stage, _ = map_skill_stage(category="custom", source="manual",
                                   status="approved")
        assert stage is None


class TestSkillToDescriptor:
    def test_basic_mapping(self):
        skill = sample_skill(
            "data-sync", name="数据同步", category="claude",
            source="github:acme/skills", status="pending_review",
            config_schema={"type": "object",
                           "properties": {"target": {"type": "string"}}},
            output_schema={"type": "object",
                           "properties": {"ok": {"type": "boolean"}}},
            metrics={"usage_count": 12, "success_count": 9,
                     "success_rate": 0.75})
        d, notes = skill_to_descriptor(skill)
        assert d is not None
        assert d.capability_id == "cp.skill.data-sync"
        assert d.origin.source_type == SourceType.SKILL
        assert d.capability.input_schema["properties"]["target"]["type"] == "string"
        assert d.capability.output_schema["properties"]["ok"]["type"] == "boolean"
        assert d.evolution.stage == EvolutionStage.BORROWED
        assert d.evolution.trace_policy  # 外来导入自动带轨迹策略
        assert d.quality.sample_count == 12
        assert d.quality.success_rate == 0.75
        assert notes["stage_rationale"]

    def test_local_published_no_stage(self):
        skill = sample_skill("self_reflection", category="builtin",
                             source="manual", status="published")
        d, notes = skill_to_descriptor(skill)
        assert d.evolution.stage is None
        assert d.origin.provenance == ProvenanceLevel.VERIFIED

    def test_metrics_derived_success_rate(self):
        skill = sample_skill("m1", metrics={"usage_count": 10,
                                            "success_count": 7})
        d, _ = skill_to_descriptor(skill)
        assert d.quality.sample_count == 10
        assert d.quality.success_rate == pytest.approx(0.7)

    def test_missing_id_advisory(self):
        d, notes = skill_to_descriptor({"name": "no-id"})
        assert d is None
        assert "id" in notes.get("error", "")

    def test_unreadable_skill_advisory(self):
        class _Weird:
            pass
        d, notes = skill_to_descriptor(_Weird())
        assert d is None
        assert notes.get("error")

    def test_read_only_no_write_back(self):
        """Skill↔Descriptor 只读：原 skill 字典不被改写"""
        import copy
        skill = sample_skill("ro-skill")
        snapshot = copy.deepcopy(skill)
        skill_to_descriptor(skill)
        assert skill == snapshot


class TestBridgeToSkill:
    def test_with_loader(self):
        loader = lambda sid: sample_skill(sid)  # noqa: E731
        d = bridge_to_skill("via-loader", loader=loader)
        assert d is not None
        assert d.capability_id == "cp.skill.via-loader"

    def test_loader_missing_returns_none_advisory(self):
        errors = []
        d = bridge_to_skill("ghost", loader=lambda sid: None,
                            error_sink=errors)
        assert d is None
        assert errors and "不存在" in errors[0]

    def test_loader_raises_advisory(self):
        def boom(_sid):
            raise RuntimeError("backend down")
        errors = []
        d = bridge_to_skill("x", loader=boom, error_sink=errors)
        assert d is None
        assert errors

    def test_with_explicit_skill_object(self):
        d = bridge_to_skill("obj", skill=sample_skill("obj"))
        assert d is not None


class TestRegisterBridgeViewAdvisory:
    def test_invalid_item_does_not_block(self, tmp_path):
        """桥接失败不阻断技能中心主流程：单条畸形 tool 仅记 error，其余正常入库"""
        reg = DescriptorRegistry(tmp_path / "d.json", autosave=False)
        items = [
            MCP_READ,
            {"name": "write_file",  # 与 MCP_READ 同批，无 annotations 的写工具
             "description": "写入文件",
             "inputSchema": {"type": "object",
                             "properties": {"path": {"type": "string"}}}},
            {"name": "!!!"},  # 名字清洗后为空 → ID 非法 → 转换失败（advisory）
        ]
        summary = register_bridge_view(reg, "mcp", items,
                                       server="filesystem", transport="stdio")
        assert summary.total == 3
        assert summary.registered == 2
        assert summary.merged == 0
        assert len(summary.errors) >= 1
        # 主流程不阻断：合法条目全部入库
        assert reg.count() == 2

    def test_skill_items_errors_collected(self, tmp_path):
        reg = DescriptorRegistry(tmp_path / "d.json", autosave=False)
        summary = register_bridge_view(reg, "skill",
                                       [sample_skill("ok-skill"),
                                        {"name": "no-id-skill"}])
        assert summary.registered == 1
        assert len(summary.errors) == 1
        assert summary.errors[0].source == "skill:no-id-skill" or \
            summary.errors[0].source.startswith("skill:")

    def test_unknown_kind_raises(self, tmp_path):
        reg = DescriptorRegistry(tmp_path / "d.json", autosave=False)
        with pytest.raises(ValueError):
            register_bridge_view(reg, "wat", [])


class TestDemo:
    def test_demo_pipeline(self):
        out = demo_pipeline()
        assert out["ok"] is True
        steps = out["steps"]
        assert steps["mcp_discover"]["registered"] == 2
        assert steps["builtin"]["registered"] == 2
        assert steps["skills"]["registered"] == 2
        assert steps["dedupe_merge"]["merged"] == 1
        assert steps["capability_count"] == 6
        assert len(steps["aliases"]) == 1
        assert steps["stats"]["total"] == 6

    def test_demo_accepts_external_registry(self, tmp_path):
        reg = DescriptorRegistry(tmp_path / "d.json", autosave=False)
        out = demo_pipeline(registry=reg)
        assert out["ok"] is True
        assert reg.count() == 6
        rows = reg.list_with_trust()
        # 能力清单导出包含 mcp/builtin/skill 三类来源
        assert {r["source_type"] for r in rows} == {"mcp", "builtin", "skill"}
