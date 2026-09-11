"""TASK-S3-01 SKILL.md 草稿生成单测（桥接既有 solidify / skill_converter）

验收对应：产物为 **draft 态** SKILL.md，不自动发布、不越过审批。
"""

from __future__ import annotations

import os

import pytest

from agent.digestion import generation as gen
from agent.digestion.models import (
    BranchCondition,
    CandidatePattern,
    SameTaskKey,
    ParameterSlot,
    PatternStep,
)


def make_pattern(*, support=22, sample_size=24, coverage=0.92, confidence=0.88,
                 steps=3, branches=None, slots=None, profile=None) -> CandidatePattern:
    key = SameTaskKey("cp.builtin.read_file", "shape:path", "success")
    psteps = []
    for i in range(steps):
        label = ["read_file", "shell_execute", "write_file"][i % 3]
        psteps.append(PatternStep(
            seq=i + 1, label=label, support=round(1.0 - i * 0.1, 4),
            capability_id=f"cp.builtin.{label}", samples=support,
            params={}))
    return CandidatePattern(
        pattern_id="pat_test0001", key=key, steps=psteps,
        slots=list(slots or []),
        branches=list(branches or []),
        support=support, sample_size=sample_size, coverage=coverage,
        lcs_length=len(psteps), confidence=confidence,
        side_effect_profile=profile or {
            "files_written_shape": [], "files_deleted_shape": [],
            "external_calls_shape": [], "files_written_count": 0,
            "undo_hint": "仅写类副作用"},
        negative_samples=sample_size - support)


# ════════════════════════════════════════════════════════════
#  1. 质量门控与阈值一致性
# ════════════════════════════════════════════════════════════


class TestQualityGate:
    def test_pass(self):
        ok, reasons = gen.pattern_quality_gate(make_pattern())
        assert ok is True and reasons == []

    def test_low_support_rejected(self):
        ok, reasons = gen.pattern_quality_gate(make_pattern(support=2))
        assert ok is False and any("支撑轨迹数" in r for r in reasons)

    def test_low_confidence_rejected(self):
        ok, reasons = gen.pattern_quality_gate(make_pattern(confidence=0.2))
        assert ok is False and any("置信度" in r for r in reasons)

    def test_low_coverage_rejected(self):
        ok, reasons = gen.pattern_quality_gate(make_pattern(coverage=0.1))
        assert ok is False and any("覆盖率" in r for r in reasons)

    def test_shallow_skeleton_rejected(self):
        ok, reasons = gen.pattern_quality_gate(make_pattern(steps=1))
        assert ok is False and any("骨架步骤数" in r for r in reasons)

    def test_two_step_skeleton_rejected_by_ascension_gate(self):
        """2 步骨架：**骨架成形**（mining 口径）但**不足以升格**（升格前门口径）

        该接缝是实现期实测到的：2 步产物能过 mining 门槛，却会在下一跳
        `solidify._quality_check`（rule 门 ≥3 步）被拒 ⇒ 故升格前门必须更严。
        """
        two = make_pattern(steps=2)
        assert two.is_shallow is False            # mining 口径：成形
        ok, reasons = gen.pattern_quality_gate(two)
        assert ok is False                         # 升格口径：不足
        assert any("3" in r for r in reasons)
        assert gen.solidify_min_rule_steps() == 3

    def test_ascension_steps_align_with_solidify(self):
        """`MIN_ASCENSION_STEPS` 与既有 `solidify._MIN_RULE_STEPS` **逐值对账**"""
        assert gen.MIN_ASCENSION_STEPS == gen.solidify_min_rule_steps()

    def test_gate_pass_when_all_thresholds_met(self):
        ok, reasons = gen.pattern_quality_gate(
            make_pattern(steps=gen.MIN_ASCENSION_STEPS))
        assert ok is True and reasons == []

    def test_gate_constants_align_with_skill_converter(self):
        """阈值与既有 `skill_converter` **同词汇**（防两套阈值随版本漂移）"""
        ms, mc, mp = gen.converter_gate_constants()
        assert (ms, mc, mp) == (gen.MIN_SUCCESS_COUNT, gen.MIN_CONFIDENCE,
                                gen.MIN_PRIORITY)

    def test_digestion_extra_gate_is_stricter_or_equal(self):
        assert gen.MIN_PATTERN_COVERAGE >= 0.5

    def test_priority_in_range_and_monotonic(self):
        low = gen.pattern_to_priority(make_pattern(support=5, coverage=0.6,
                                                   confidence=0.7))
        high = gen.pattern_to_priority(make_pattern(support=24, coverage=1.0,
                                                    confidence=1.0))
        assert 0 <= low <= high <= 100


# ════════════════════════════════════════════════════════════
#  2. 草稿身份与 draft 纪律
# ════════════════════════════════════════════════════════════


class TestDraftIdentity:
    def test_id_is_stable_and_prefixed(self):
        p = make_pattern()
        assert gen.draft_skill_id(p) == gen.draft_skill_id(p)
        assert gen.draft_skill_id(p).startswith("dig-")

    def test_id_matches_skills_mgmt_regex(self):
        import re
        assert re.match(r"^[a-z0-9][a-z0-9_\-]*$", gen.draft_skill_id(make_pattern()))

    def test_different_patterns_get_different_ids(self):
        a = make_pattern()
        b = make_pattern()
        b.pattern_id = "pat_other"
        assert gen.draft_skill_id(a) != gen.draft_skill_id(b)


class TestDraftDiscipline:
    def test_status_is_draft(self):
        assert gen.build_skill_draft(make_pattern()).status == "draft"

    def test_front_matter_status_draft(self):
        draft = gen.build_skill_draft(make_pattern())
        assert draft.front_matter["status"] == "draft"

    def test_enabled_is_false(self):
        """草稿不得被运行时加载 ⇒ enabled=False"""
        assert gen.build_skill_draft(make_pattern()).front_matter["enabled"] is False

    def test_source_marks_digestion(self):
        meta = gen.build_skill_draft(make_pattern()).front_matter
        assert meta["source"] == "digestion"
        assert "from_digestion" in meta["tags"]
        assert "draft" in meta["tags"]

    def test_banner_present_in_markdown(self):
        md = gen.build_skill_draft(make_pattern()).markdown
        assert "自动挖掘草稿" in md
        assert "未经人工审核" in md

    def test_front_matter_fence(self):
        md = gen.build_skill_draft(make_pattern()).markdown
        assert md.startswith("---\n")
        assert "\n---\n" in md

    def test_persist_goes_to_staging_not_skills_repo(self, tmp_path):
        draft = gen.build_skill_draft(make_pattern())
        path = gen.persist_skill_draft(draft, draft_dir=str(tmp_path / "drafts"))
        assert path and os.path.isfile(path)
        assert "skills_repo" not in path
        assert draft.path == path

    def test_persist_failure_is_advisory(self):
        draft = gen.build_skill_draft(make_pattern())
        assert gen.persist_skill_draft(draft, draft_dir="") or True
        # 不可写路径 → 返回 ""，不抛
        assert gen.persist_skill_draft(draft, draft_dir="\0bad") == ""

    def test_default_draft_dir_is_outside_skills_repo(self):
        assert "skills_repo" not in gen.DEFAULT_DRAFT_DIR
        assert gen.DEFAULT_DRAFT_DIR.endswith(os.path.join("data", "digestion",
                                                           "drafts"))


# ════════════════════════════════════════════════════════════
#  3. 正文编译（复用既有编译器）
# ════════════════════════════════════════════════════════════


class TestCompileBody:
    def test_reuses_solidify_compiler_structure(self):
        body = gen.compile_skill_body(make_pattern())
        # `solidify._compile_skill_content` 的固定小节
        assert "## 触发条件" in body
        assert "## 步骤清单" in body
        assert "## 来源" in body
        assert "### 步骤 1:" in body

    def test_side_effect_profile_section(self):
        body = gen.compile_skill_body(make_pattern(profile={
            "files_written_shape": ["${path}"], "files_deleted_shape": [],
            "external_calls_shape": [], "files_written_count": 3,
            "undo_hint": "仅写类副作用"}))
        assert "## 副作用画像" in body
        assert "${path}" in body

    def test_branch_section_rendered(self):
        body = gen.compile_skill_body(make_pattern(branches=[
            BranchCondition(at_step=2, condition="步骤 `fix` 出现", support=5,
                            total=24, outcome="success", advice="Δ=+40%")]))
        assert "## 分支条件（决策树提取）" in body
        assert "历史倾向 **success**" in body
        assert "第 2 步" in body

    def test_trajectory_level_branch_rendered(self):
        body = gen.compile_skill_body(make_pattern(branches=[
            BranchCondition(at_step=-1, condition="步数 < 3", support=4, total=28,
                            outcome="failure")]))
        assert "整条轨迹" in body

    def test_negative_sample_count_disclosed(self):
        body = gen.compile_skill_body(make_pattern(support=22, sample_size=26))
        assert "负样本: 4 条" in body

    def test_slots_rendered_into_steps(self):
        pattern = make_pattern(steps=2, slots=[
            ParameterSlot(name="cmd", placeholder="${cmd}",
                          step_label="shell_execute", sample_count=20,
                          distinct_values=9, examples=["pytest"])])
        body = gen.compile_skill_body(pattern)
        assert "${cmd}" in body

    def test_deterministic(self):
        p = make_pattern()
        assert gen.compile_skill_body(p) == gen.compile_skill_body(p)


class TestDistilledProcessBridge:
    def test_maps_steps_to_capabilities(self):
        proc = gen.pattern_to_distilled_process(make_pattern(steps=3))
        assert len(proc.steps) == 3
        assert proc.steps[0].tool == "cp.builtin.read_file"
        assert proc.method == "rule"

    def test_sources_carry_trace_set_and_pattern(self):
        proc = gen.pattern_to_distilled_process(make_pattern())
        assert any(s.startswith("trace-set:") for s in proc.sources)
        assert any(s.startswith("pattern:") for s in proc.sources)

    def test_description_discloses_evidence(self):
        proc = gen.pattern_to_distilled_process(make_pattern(support=22))
        assert "22/24" in proc.description
        assert "覆盖率" in proc.description

    def test_trigger_patterns_from_capability_and_intent(self):
        proc = gen.pattern_to_distilled_process(make_pattern())
        assert "cp.builtin.read_file" in proc.trigger_patterns

    def test_step_note_reports_support(self):
        proc = gen.pattern_to_distilled_process(make_pattern())
        assert "支撑率" in proc.steps[0].note


# ════════════════════════════════════════════════════════════
#  4. opt-in 升格桥接
# ════════════════════════════════════════════════════════════


class TestOptInPromotionBridge:
    def test_to_learned_workflow_shape(self):
        wf = gen.to_learned_workflow(make_pattern(steps=3))
        assert wf.workflow_type == "toolchain"
        assert len(wf.steps) == 3
        assert all(not s.need_llm for s in wf.steps)

    def test_learned_workflow_stats_fill_gate_semantics(self):
        wf = gen.to_learned_workflow(make_pattern(support=22, confidence=0.88))
        assert wf.success_count == 22
        assert wf.confidence == pytest.approx(0.88, abs=1e-6)
        assert 0 <= wf.priority <= 100

    def test_learned_workflow_id_is_kebab(self):
        import re
        wf = gen.to_learned_workflow(make_pattern())
        assert re.match(r"^[a-z0-9][a-z0-9_\-]*$", wf.id)

    def test_learned_workflow_flags_ungated_pattern(self):
        wf = gen.to_learned_workflow(make_pattern(support=1, confidence=0.1))
        assert "未达升格门槛" in wf.description

    def test_promote_delegates_to_existing_converter(self):
        class _Converter:
            def __init__(self):
                self.calls = []

            def convert_workflow_to_skill(self, wf_id, *, force=False):
                self.calls.append((wf_id, force))
                return {"action": "created", "workflow_id": wf_id}

        conv = _Converter()
        out = gen.promote_workflow_to_skill(make_pattern(), converter=conv)
        assert out["action"] == "created"
        assert len(conv.calls) == 1

    def test_solidify_draft_forces_run_review_false(self, monkeypatch):
        """`run_review=True` 是既有默认值且会改写技能状态 ⇒ 桥接必须显式关掉"""
        import agent.process_distill.solidify as solid

        seen = {}

        def _fake(proc, *, skills_svc=None, run_review=True):
            seen["run_review"] = run_review
            return {"action": "created"}

        monkeypatch.setattr(solid, "solidify_to_skill", _fake)
        out = gen.solidify_draft(make_pattern(), skills_svc=object())
        assert out["action"] == "created"
        assert seen["run_review"] is False

    def test_pipeline_never_auto_promotes(self):
        """流水线只产 draft：升格入口在 service 层**没有任何调用点**（AST 判定）"""
        import ast
        import pathlib

        from agent.digestion import service as service_mod
        source = pathlib.Path(service_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                called.add(node.attr)
            elif isinstance(node, ast.Name):
                called.add(node.id)
        # 仅允许在**文档/导入**中出现，不得出现在任何可执行调用位
        assert "promote_workflow_to_skill" not in called
        assert "solidify_draft" not in called
        # 正向：流水线确实调用了草稿生成（否则上面的断言会因为"什么都没做"而假通过）
        assert "build_skill_draft" in called

    def test_pipeline_does_not_call_review_or_publish(self):
        """不越过审批：service 层不得出现 review/publish/optimize_with_feedback"""
        import ast
        import pathlib

        from agent.digestion import service as service_mod
        source = pathlib.Path(service_mod.__file__).read_text(encoding="utf-8")
        called = {n.attr for n in ast.walk(ast.parse(source))
                  if isinstance(n, ast.Attribute)}
        for forbidden in ("review", "publish", "optimize_with_feedback"):
            assert forbidden not in called
