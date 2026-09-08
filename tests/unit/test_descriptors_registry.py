"""agent/descriptors/registry.py — Registry 注册/合并/分裂/alias/持久化测试（TASK-S1-01）"""

import json

import pytest

from agent.descriptors.models import (
    DataClass,
    DescriptorValidationError,
    EvolutionStage,
    ProvenanceLevel,
    RiskLevel,
    SourceType,
)
from agent.descriptors.registry import (
    DescriptorRegistry,
    MERGE_THRESHOLD,
    RegisterResult,
    name_similarity,
    normalize_name,
    schema_diff_keys,
    structural_similarity,
    text_similarity,
)
from descriptors_util import destructive_descriptor, make_descriptor


@pytest.fixture
def reg(tmp_path):
    return DescriptorRegistry(tmp_path / "descriptors.json", autosave=False)


class TestSimilarityPrimitives:
    def test_normalize_name(self):
        assert normalize_name("read_File") == "read file"
        assert normalize_name(" a--b  ") == "a b"
        assert normalize_name("") == ""

    def test_name_similarity(self):
        assert name_similarity("read_file", "read_file") == 1.0
        assert name_similarity("", "") == 1.0
        assert name_similarity("read_file", "write_file") < 1.0
        assert name_similarity("read_file", "") == 0.0

    def test_text_similarity(self):
        assert text_similarity("hello world", "hello world") == 1.0
        assert text_similarity("", "") == 1.0
        assert text_similarity("abc", "xyz") < 0.9

    def test_structural_similarity_empty_rules(self):
        assert structural_similarity({}, {}) == 1.0
        assert structural_similarity({}, {"type": "object"}) == 0.0

    def test_structural_similarity_identical(self):
        a = {"type": "object", "properties": {"q": {"type": "string"}},
             "required": ["q"]}
        assert structural_similarity(a, json.loads(json.dumps(a))) == 1.0

    def test_structural_similarity_majority_overlap(self):
        base = {"type": "object",
                "properties": {f"p{i}": {"type": "string"} for i in range(10)}}
        ext = dict(base)
        ext["properties"]["p_extra"] = {"type": "string"}
        sim = structural_similarity(base, ext)
        # 10 相同 + 1 新增 → 2*10/21 ≈ 0.95 ≥ 0.9（合并阈值内）
        assert sim >= MERGE_THRESHOLD

    def test_structural_similarity_divergent(self):
        a = {"type": "object", "properties": {"x": {"type": "string"}}}
        b = {"type": "object", "properties": {"y": {"type": "integer"}}}
        assert structural_similarity(a, b) < 0.9

    def test_schema_diff_keys(self):
        a = {"type": "object", "properties": {"x": {"type": "string"}}}
        b = {"type": "object", "properties": {"y": {"type": "integer"}}}
        keys = schema_diff_keys(a, b)
        assert "prop.x" in keys and "prop.y" in keys


class TestRegisterBasics:
    def test_register_created(self, reg):
        d = make_descriptor(cid="cp.fs.read", source_type="mcp",
                            source_id="fs", prov="declared")
        res = reg.register(d)
        assert res.action == "created"
        assert reg.count() == 1
        got = reg.get("cp.fs.read")
        assert got is not None and got.capability.name == "read"

    def test_register_same_id_unchanged(self, reg):
        d = make_descriptor(cid="cp.fs.read", name="read")
        reg.register(d)
        d2 = make_descriptor(cid="cp.fs.read", name="read")
        res = reg.register(d2)
        assert res.action == "unchanged"

    def test_register_same_id_updated(self, reg):
        reg.register(make_descriptor(cid="cp.fs.read", name="read",
                                     description="v1"))
        res = reg.register(make_descriptor(cid="cp.fs.read", name="read",
                                           description="v2 描述更新"))
        assert res.action == "updated"
        assert reg.get("cp.fs.read").capability.description.startswith("v2")

    def test_register_invalid_rejected(self, reg):
        bad = make_descriptor(cid="cp.fs.del", risk="destructive",
                              approval=False, undo="u", comp="c")
        with pytest.raises(DescriptorValidationError):
            reg.register(bad)
        assert reg.count() == 0

    def test_register_dict_input(self, reg):
        d = make_descriptor(cid="cp.fs.read", name="read")
        res = reg.register(d.to_storage_dict())
        assert res.action == "created"

    def test_list_and_list_by_source_stage(self, reg):
        reg.register(make_descriptor(cid="cp.builtin.web_search",
                                     source_type="builtin", source_id="builtin"))
        reg.register(make_descriptor(cid="cp.mcp.s.read", source_type="mcp",
                                     source_id="s", prov="declared",
                                     stage="borrowed", trace_policy="t:1"))
        assert reg.count() == 2
        assert len(reg.list_by_source("mcp")) == 1
        assert len(reg.list_by_source(SourceType.BUILTIN)) == 1
        assert len(reg.list_by_stage("borrowed")) == 1
        assert len(reg.list_by_stage(None)) == 1  # builtin 未入轨

    def test_get_missing_returns_none(self, reg):
        assert reg.get("cp.nope.x") is None


class TestMergeDedupe:
    """schema 相同 → 三路投票 ≥0.9 → 去重合并（合并留别名）"""

    def _mcp(self, cid, server, name="read_file", **kw):
        kw.setdefault("prov", "declared")
        kw.setdefault("stage", "borrowed")
        kw.setdefault("trace_policy", f"t:{server}")
        kw.setdefault("description", "读取文件内容")
        kw.setdefault("input_schema", {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        })
        return make_descriptor(
            cid=cid, name=name, source_type="mcp", source_id=server, **kw)

    def test_merge_keeps_alias(self, reg):
        reg.register(self._mcp("cp.server-a.read_file", "server-a"))
        res = reg.register(self._mcp("cp.server-b.read_file", "server-b"))
        assert res.action == "merged"
        assert res.merged_from == ["cp.server-b.read_file"]
        assert res.votes["mean"] >= MERGE_THRESHOLD
        assert reg.count() == 1  # 合并后唯一 canonical
        assert reg.resolve_alias("cp.server-b.read_file") == "cp.server-a.read_file"
        assert reg.get("cp.server-b.read_file").capability_id == \
            "cp.server-a.read_file"  # alias 自动解析
        assert reg.aliases_of("cp.server-a.read_file") == ["cp.server-b.read_file"]
        alias_rec = reg.alias_records()["cp.server-b.read_file"]
        assert alias_rec["reason"] == "dedupe-merge"
        assert alias_rec["canonical_id"] == "cp.server-a.read_file"

    def test_merge_conservative_fields(self, reg):
        """保守合并：risk 取更严格、requires_approval OR、provenance 取更高"""
        a = self._mcp("cp.server-a.write", "server-a", name="write_file",
                      risk="high", approval=True, undo="u", comp="c",
                      prov="declared", data_class="internal",
                      input_schema={"type": "object", "properties": {
                          "p": {"type": "string"}, "c": {"type": "string"}},
                          "required": ["p", "c"]})
        reg.register(a)
        b = self._mcp("cp.server-b.write", "server-b", name="write_file",
                      risk="medium", approval=False,
                      prov="verified", evidence=["probe"],
                      input_schema={"type": "object", "properties": {
                          "p": {"type": "string"}, "c": {"type": "string"}},
                          "required": ["p", "c"]})
        res = reg.register(b)
        assert res.action == "merged"
        got = reg.get("cp.server-a.write")
        assert got.trust.risk_level == RiskLevel.HIGH       # 更严格
        assert got.trust.data_class == DataClass.INTERNAL
        assert got.trust.requires_approval is True          # OR
        assert got.origin.provenance == ProvenanceLevel.VERIFIED  # 更高
        assert got.evolution.trace_policy == f"t:server-a"  # 既有优先

    def test_cross_tenant_no_merge(self, reg):
        a = self._mcp("cp.server-a.read_file", "server-a", tenant="t1")
        reg.register(a)
        b = self._mcp("cp.server-b.read_file", "server-b", tenant="t2")
        res = reg.register(b)
        assert res.action == "created"  # 跨租户独立登记
        assert reg.count() == 2
        assert any("跨租户" in w for w in res.warnings)

    def test_same_name_same_schema_different_tenant_still_independent(self, reg):
        reg.register(self._mcp("cp.a.read", "a"))
        d = self._mcp("cp.b.read", "b", tenant="other")
        res = reg.register(d)
        assert res.action == "created"


class TestVariantSplit:
    """schema 不同 → variant 分裂"""

    def _tool(self, cid, server, schema, name="read_file"):
        return make_descriptor(
            cid=cid, name=name, source_type="mcp", source_id=server,
            prov="declared", stage="borrowed", trace_policy=f"t:{server}",
            description="读取文件内容",
            input_schema=schema)

    def test_schema_diff_variant(self, reg):
        schema_a = {"type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"]}
        schema_b = {"type": "object",
                    "properties": {"path": {"type": "string"},
                                   "encoding": {"type": "string"}},
                    "required": ["path"]}
        reg.register(self._tool("cp.srv-a.read_file", "srv-a", schema_a))
        res = reg.register(self._tool("cp.srv-b.read_file", "srv-b", schema_b))
        assert res.action == "variant"
        assert res.variant_of == "cp.srv-a.read_file"
        assert reg.count() == 2  # 各自保留
        variants = reg.variants_of("cp.srv-a.read_file")
        assert "cp.srv-b.read_file" in variants
        assert variants["cp.srv-b.read_file"]["reason"] == "schema_diff"
        assert "prop.encoding" in variants["cp.srv-b.read_file"]["input_diff_keys"]

    def test_different_name_different_schema_created(self, reg):
        reg.register(self._tool("cp.srv-a.read_file", "srv-a",
                                {"type": "object", "properties": {}}))
        res = reg.register(make_descriptor(
            cid="cp.srv-a.web_fetch", name="web_fetch", source_type="mcp",
            source_id="srv-a", prov="declared", stage="borrowed",
            trace_policy="t:1",
            input_schema={"type": "object",
                          "properties": {"url": {"type": "string"}}}))
        assert res.action == "created"

    def test_same_schema_different_name_no_merge(self, reg):
        """schema 同门但名称/描述明显不同 → 宁冗余勿误合（P7.2-05）"""
        schema = {"type": "object",
                  "properties": {"q": {"type": "string"}},
                  "required": ["q"]}
        reg.register(self._tool("cp.srv-a.search", "srv-a", schema,
                                name="search"))
        d = self._tool("cp.srv-b.find", "srv-b", schema, name="find")
        res = reg.register(d)
        assert res.action == "created"  # 不合并
        assert reg.count() == 2


class TestBackfillAPIs:
    def test_update_trust_patch(self, reg):
        reg.register(make_descriptor(cid="cp.fs.read", name="read"))
        d = reg.update_trust("cp.fs.read",
                             {"risk_level": "high",
                              "data_class": "confidential",
                              "requires_approval": True})
        assert d.trust.risk_level == RiskLevel.HIGH
        assert d.trust.data_class == DataClass.CONFIDENTIAL
        assert d.trust.requires_approval is True

    def test_update_trust_unknown_key_rejected(self, reg):
        reg.register(make_descriptor(cid="cp.fs.read", name="read"))
        with pytest.raises(DescriptorValidationError) as exc:
            reg.update_trust("cp.fs.read", {"bogus": 1})
        assert exc.value.code == "TRUST_PATCH_INVALID"

    def test_update_trust_destructive_requires_governance_first(self, reg):
        reg.register(make_descriptor(cid="cp.fs.del", name="del"))
        with pytest.raises(DescriptorValidationError):
            reg.update_trust("cp.fs.del", {"risk_level": "destructive",
                                           "data_class": "internal"})
        reg.set_governance("cp.fs.del", undo_hint="备份",
                           compensating_action="恢复")
        d = reg.update_trust("cp.fs.del",
                             {"risk_level": "destructive",
                              "requires_approval": True})
        assert d.trust.risk_level == RiskLevel.DESTRUCTIVE
        assert d.governance.undo_hint == "备份"

    def test_mark_provenance_monotonic(self, reg):
        reg.register(make_descriptor(cid="cp.fs.read", name="read"))
        d = reg.mark_provenance("cp.fs.read", "declared", reason="来源声明")
        assert d.origin.provenance == ProvenanceLevel.DECLARED
        d = reg.mark_provenance("cp.fs.read", "verified",
                                evidence=["probe-2026-09-09"])
        assert d.origin.provenance == ProvenanceLevel.VERIFIED
        assert "probe-2026-09-09" in d.origin.evidence

    def test_mark_provenance_downgrade_rejected(self, reg):
        reg.register(make_descriptor(cid="cp.fs.read", name="read",
                                     prov="verified", evidence=["e1"]))
        with pytest.raises(DescriptorValidationError) as exc:
            reg.mark_provenance("cp.fs.read", "declared")
        assert exc.value.code == "PROVENANCE_DOWNGRADE"
        # force 允许降级
        d = reg.mark_provenance("cp.fs.read", "declared", force=True)
        assert d.origin.provenance == ProvenanceLevel.DECLARED

    def test_mark_provenance_verified_needs_evidence(self, reg):
        reg.register(make_descriptor(cid="cp.fs.read", name="read"))
        with pytest.raises(DescriptorValidationError) as exc:
            reg.mark_provenance("cp.fs.read", "verified")
        assert exc.value.code == "PROVENANCE_NO_EVIDENCE"
        with pytest.raises(DescriptorValidationError):
            reg.mark_provenance("cp.fs.read", "signed")

    def test_mark_provenance_missing_descriptor(self, reg):
        with pytest.raises(DescriptorValidationError) as exc:
            reg.mark_provenance("cp.nope.x", "declared")
        assert exc.value.code == "NOT_FOUND"

    def test_set_stage_borrowed_needs_trace_policy(self, reg):
        reg.register(make_descriptor(cid="cp.fs.read", name="read"))
        with pytest.raises(DescriptorValidationError):
            reg.set_stage("cp.fs.read", "borrowed")
        d = reg.set_stage("cp.fs.read", "borrowed", trace_policy="t:ledger",
                          reason="接入 MCP")
        assert d.evolution.stage == EvolutionStage.BORROWED
        assert d.evolution.trace_policy == "t:ledger"
        d = reg.set_stage("cp.fs.read", None)
        assert d.evolution.stage is None

    def test_set_governance(self, reg):
        reg.register(make_descriptor(cid="cp.fs.del", name="del"))
        d = reg.set_governance("cp.fs.del", undo_hint="备份",
                               compensating_action="恢复",
                               audit_level="full")
        assert d.governance.undo_hint == "备份"
        assert d.governance.audit_level.value == "full"

    def test_update_fields_dotted(self, reg):
        reg.register(make_descriptor(cid="cp.fs.read", name="read"))
        d = reg.update_fields("cp.fs.read",
                              {"governance.undo_hint": "undo-x"})
        assert d.governance.undo_hint == "undo-x"

    def test_patch_missing_descriptor(self, reg):
        with pytest.raises(DescriptorValidationError) as exc:
            reg.update_trust("cp.nope.x", {"risk_level": "low"})
        assert exc.value.code == "NOT_FOUND"


class TestListWithTrustAndUnregister:
    def test_list_with_trust_shape(self, reg):
        reg.register(destructive_descriptor())
        rows = reg.list_with_trust()
        assert len(rows) == 1
        row = rows[0]
        assert row["capability_id"] == "cp.fs.delete"
        assert row["risk_level"] == "destructive"
        assert row["requires_approval"] is True
        assert row["has_undo_hint"] is True
        assert row["has_compensating_action"] is True
        assert row["source_type"] == "mcp"
        assert row["external_endpoint"] is False
        assert row["aliases"] == []
        assert row["variant_count"] == 0

    def test_unregister_removes_aliases_and_variants(self, reg):
        a = make_descriptor(cid="cp.a.read", name="read",
                            source_type="mcp", source_id="a", prov="declared",
                            stage="borrowed", trace_policy="t:a",
                            input_schema={"type": "object",
                                          "properties": {"p": {"type": "string"}}})
        b = make_descriptor(cid="cp.b.read", name="read",
                            source_type="mcp", source_id="b", prov="declared",
                            stage="borrowed", trace_policy="t:b",
                            input_schema={"type": "object",
                                          "properties": {"p": {"type": "string"},
                                                         "q": {"type": "string"}}})
        reg.register(a)
        reg.register(b)  # variant
        assert reg.count() == 2
        assert reg.unregister("cp.a.read") is True
        assert reg.count() == 1
        assert reg.variants_of("cp.a.read") == {}
        assert reg.unregister("cp.nope.x") is False

    def test_audit_trail(self, reg):
        reg.register(make_descriptor(cid="cp.fs.read", name="read"))
        reg.update_trust("cp.fs.read", {"risk_level": "medium"})
        reg.mark_provenance("cp.fs.read", "declared")
        reg.unregister("cp.fs.read")
        actions = [e["action"] for e in reg.audit_trail()]
        assert "descriptor.register" in actions
        assert "descriptor.patch" in actions
        assert "descriptor.provenance" in actions
        assert "descriptor.unregister" in actions


class TestPersistence:
    def test_save_load_roundtrip(self, tmp_path):
        path = tmp_path / "d.json"
        reg1 = DescriptorRegistry(path, autosave=False)
        reg1.register(destructive_descriptor())
        read_schema = {"type": "object",
                       "properties": {"p": {"type": "string"}}}
        reg1.register(make_descriptor(cid="cp.fs.read", name="read",
                                      source_type="builtin", source_id="builtin",
                                      prov="verified", input_schema=read_schema))
        # 制造合并 → alias（同 schema 同名的 mcp 副本）
        dup = make_descriptor(cid="cp.other.read", name="read",
                              source_type="mcp", source_id="other",
                              prov="declared",
                              input_schema=read_schema)
        res = reg1.register(dup)
        assert res.action == "merged"
        assert reg1.count() == 2
        reg1.save()

        reg2 = DescriptorRegistry(path, autosave=False)
        reg2.load()
        assert reg2.count() == 2
        assert reg2.get("cp.fs.delete").trust.risk_level == \
            RiskLevel.DESTRUCTIVE
        # alias 往返
        assert reg2.resolve_alias("cp.other.read") == "cp.fs.read"
        assert reg2.alias_records()["cp.other.read"]["reason"] == "dedupe-merge"

    def test_autosave_on_mutation(self, tmp_path):
        path = tmp_path / "auto.json"
        reg = DescriptorRegistry(path, autosave=True)
        reg.register(make_descriptor(cid="cp.fs.read", name="read"))
        reg2 = DescriptorRegistry(path, autosave=False)
        assert reg2.count() == 1  # 自动落盘可被新实例读到

    def test_load_missing_file_empty(self, tmp_path):
        reg = DescriptorRegistry(tmp_path / "nope.json", autosave=False)
        reg.load()
        assert reg.count() == 0
        assert reg.invalid_entries() == {}

    def test_load_corrupt_backs_up(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        reg = DescriptorRegistry(path, autosave=False)
        reg.load()
        assert reg.count() == 0
        assert reg.load_warnings()
        assert path.with_suffix(".corrupted.json").exists()

    def test_load_invalid_entries_advisory(self, tmp_path):
        path = tmp_path / "mix.json"
        good = make_descriptor(cid="cp.fs.read", name="read").to_storage_dict()
        bad = make_descriptor(cid="cp.fs.bad", name="bad",
                              risk="destructive", approval=False,
                              undo="", comp="").to_storage_dict()
        payload = {
            "schema_version": 1,
            "descriptors": {"cp.fs.read": good, "cp.fs.bad": bad},
            "aliases": {}, "variants": {},
        }
        path.write_text(json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8")
        reg = DescriptorRegistry(path, autosave=False)
        reg.load()
        assert reg.count() == 1  # 合法条目正常加载
        assert "cp.fs.bad" in reg.invalid_entries()  # 非法条目 advisory 记录
        assert reg.get("cp.fs.read") is not None


class TestSnapshotStats:
    def test_snapshot_stats(self, reg):
        reg.register(destructive_descriptor())
        reg.register(make_descriptor(cid="cp.fs.read", name="read",
                                     source_type="builtin", source_id="builtin"))
        stats = reg.snapshot_stats()
        assert stats["total"] == 2
        assert stats["destructive"] == 1
        assert stats["by_source"]["builtin"] == 1
        assert stats["requires_approval"] == 1
