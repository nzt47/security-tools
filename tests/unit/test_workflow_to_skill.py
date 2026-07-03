"""工作流 → 技能 转换 — 单元测试

测试覆盖：
1. 质量门控：未达门控抛 WorkflowConvertError
2. 转换闭环：满足门控的 workflow 被成功转换为 Skill
3. 幂等：重复调用返回已存在的 skill_id
4. 内容编译：SKILL.md 正文包含步骤、触发条件、来源
5. 强制转换：force=True 跳过质量门控
6. 外部技能翻译：规则式（无 LLM）能正确转换
7. 外部技能 ID 冲突解决：同名技能自动加后缀
8. list_convertible_workflows：列出可转换候选
9. WorkflowLearningService.convert_to_skill 端到端

状态同步机制：
- 后端权威原则：转换完成后从 store 重新读取验证
- 幂等性：重复调用不创建重复技能
"""
import os
import sys

import pytest

# 让 tests/ 可以导入 agent 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from agent.workflow_learning.models import (
    LearnedWorkflow, WorkflowStep, WorkflowStatus,
)
from agent.workflow_learning.repository import WorkflowRepository
from agent.workflow_learning.skill_converter import (
    WorkflowToSkillConverter,
    WorkflowConvertError,
    MIN_SUCCESS_COUNT,
    MIN_CONFIDENCE,
)
from agent.workflow_learning.service import WorkflowLearningService
from agent.skills_mgmt.service import SkillsMgmtService


# ═══════════════════════════════════════════════════════════════════
#  Fixture
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def wf_repo(tmp_path):
    return WorkflowRepository(path=str(tmp_path / "workflows.json"))


@pytest.fixture
def skills_svc(tmp_path):
    return SkillsMgmtService(store_path=str(tmp_path / "skills.json"))


@pytest.fixture
def converter(wf_repo, skills_svc):
    return WorkflowToSkillConverter(skills_svc, wf_repo)


def _make_workflow(wf_id="wf-demo", *,
                   success_count=10,
                   confidence=0.85,
                   priority=70,
                   enabled=True,
                   status=WorkflowStatus.ACTIVE,
                   steps=None,
                   tags=None,
                   converted_to_skill_id=""):
    """构造一个测试 workflow"""
    wf = LearnedWorkflow(
        id=wf_id,
        name=f"测试工作流 {wf_id}",
        description=f"用于测试的 {wf_id}",
        task_signature=f"signature-{wf_id}",
        trigger_patterns=[f"trigger-{wf_id}"],
        steps=steps or [
            WorkflowStep(
                step_id="s1",
                tool_name="search",
                params_template={"query": "$input"},
                output_key="result",
                description="第一步：搜索",
            ),
            WorkflowStep(
                step_id="s2",
                tool_name="format",
                params_template={"data": "$prev_output"},
                output_key="formatted",
                description="第二步：格式化",
            ),
        ],
        expected_output_pattern=r"\d+ results",
        source_session_id="sess-001",
        source_user_input="帮我搜索并格式化",
        success_count=success_count,
        failure_count=1,
        confidence=confidence,
        priority=priority,
        status=status,
        enabled=enabled,
        tags=tags or ["test"],
        converted_to_skill_id=converted_to_skill_id,
    )
    return wf


# ═══════════════════════════════════════════════════════════════════
#  1. 质量门控
# ═══════════════════════════════════════════════════════════════════

class TestQualityGate:
    """质量门控测试"""

    def test_low_success_count_rejected(self, converter, wf_repo):
        wf = _make_workflow(success_count=2, confidence=0.9)
        wf_repo.upsert(wf)
        with pytest.raises(WorkflowConvertError) as exc:
            converter.convert_workflow_to_skill(wf.id)
        assert "QUALITY_GATE_FAILED" in exc.value.code
        assert "success_count" in str(exc.value)

    def test_low_confidence_rejected(self, converter, wf_repo):
        wf = _make_workflow(success_count=10, confidence=0.5)
        wf_repo.upsert(wf)
        with pytest.raises(WorkflowConvertError) as exc:
            converter.convert_workflow_to_skill(wf.id)
        assert "confidence" in str(exc.value)

    def test_disabled_workflow_rejected(self, converter, wf_repo):
        wf = _make_workflow(enabled=False)
        wf_repo.upsert(wf)
        with pytest.raises(WorkflowConvertError) as exc:
            converter.convert_workflow_to_skill(wf.id)
        assert "未启用" in str(exc.value)

    def test_deprecated_workflow_rejected(self, converter, wf_repo):
        wf = _make_workflow(status=WorkflowStatus.DEPRECATED)
        wf_repo.upsert(wf)
        with pytest.raises(WorkflowConvertError) as exc:
            converter.convert_workflow_to_skill(wf.id)
        assert "状态" in str(exc.value)

    def test_workflow_not_found(self, converter):
        with pytest.raises(WorkflowConvertError) as exc:
            converter.convert_workflow_to_skill("non-existent-wf")
        assert exc.value.code == "NOT_FOUND"


# ═══════════════════════════════════════════════════════════════════
#  2. 转换闭环
# ═══════════════════════════════════════════════════════════════════

class TestConversionE2E:
    """端到端转换测试"""

    def test_convert_creates_skill_and_marks_workflow(
            self, converter, wf_repo, skills_svc):
        wf = _make_workflow()
        wf_repo.upsert(wf)

        result = converter.convert_workflow_to_skill(wf.id)

        assert result["action"] == "created"
        assert result["workflow_id"] == wf.id
        assert result["skill_id"].endswith("-skill")

        # 验证 skill 真的落库了
        skill = skills_svc.get(result["skill_id"])
        assert skill is not None
        assert skill.source == "workflow_learning"
        assert "from_workflow" in skill.tags
        assert "auto_converted" in skill.tags

        # 验证 workflow 已被回写 converted_to_skill_id
        updated_wf = wf_repo.get(wf.id)
        assert updated_wf.converted_to_skill_id == result["skill_id"]

    def test_idempotent_conversion(self, converter, wf_repo, skills_svc):
        wf = _make_workflow()
        wf_repo.upsert(wf)

        # 第一次转换
        result1 = converter.convert_workflow_to_skill(wf.id)
        assert result1["action"] == "created"

        # 第二次调用应该幂等返回
        result2 = converter.convert_workflow_to_skill(wf.id)
        assert result2["action"] == "already_converted"
        assert result2["skill_id"] == result1["skill_id"]

        # store 里只应该有 1 个技能
        all_skills = skills_svc.list_all()
        assert len(all_skills) == 1

    def test_force_skips_quality_gate(self, converter, wf_repo, skills_svc):
        wf = _make_workflow(success_count=0, confidence=0.1)
        wf_repo.upsert(wf)

        # 不带 force → 抛错
        with pytest.raises(WorkflowConvertError):
            converter.convert_workflow_to_skill(wf.id)

        # 带 force → 转换成功
        result = converter.convert_workflow_to_skill(wf.id, force=True)
        assert result["action"] == "created"


# ═══════════════════════════════════════════════════════════════════
#  3. 内容编译
# ═══════════════════════════════════════════════════════════════════

class TestSkillContentCompilation:
    """SKILL.md 正文内容编译测试"""

    def test_content_contains_steps_and_triggers(
            self, converter, wf_repo, skills_svc):
        wf = _make_workflow()
        wf_repo.upsert(wf)

        result = converter.convert_workflow_to_skill(wf.id)
        skill = skills_svc.get(result["skill_id"])

        assert "## 触发条件" in skill.content
        assert "trigger-wf-demo" in skill.content
        assert "## 步骤清单" in skill.content
        assert "search" in skill.content
        assert "format" in skill.content
        assert "## 来源" in skill.content
        assert "success_count: 10" in skill.content

    def test_dependencies_extracted_from_steps(
            self, converter, wf_repo, skills_svc):
        wf = _make_workflow()
        wf_repo.upsert(wf)

        result = converter.convert_workflow_to_skill(wf.id)
        skill = skills_svc.get(result["skill_id"])

        assert "search" in skill.dependencies
        assert "format" in skill.dependencies


# ═══════════════════════════════════════════════════════════════════
#  4. ID 冲突解决
# ═══════════════════════════════════════════════════════════════════

class TestIdConflictResolution:
    """同名技能自动加后缀"""

    def test_id_conflict_resolved_with_suffix(
            self, converter, wf_repo, skills_svc):
        # 先手工创建一个 wf-demo-skill 占位
        skills_svc.create_manual({
            "id": "wf-demo-skill",
            "name": "占位",
            "description": "已存在",
            "content": "...",
            "content_type": "markdown",
            "category": "custom",
            "tags": [],
            "author": "test",
        })

        wf = _make_workflow()
        wf_repo.upsert(wf)

        # 转换时应该自动加 -2 后缀
        result = converter.convert_workflow_to_skill(wf.id)
        assert result["skill_id"] == "wf-demo-skill-2"

        # store 里现在应该有 2 个技能
        assert len(skills_svc.list_all()) == 2


# ═══════════════════════════════════════════════════════════════════
#  5. 外部技能翻译（规则式）
# ═══════════════════════════════════════════════════════════════════

class TestExternalSkillConversion:
    """外部技能翻译测试（无 LLM）"""

    def test_rule_translate_basic(self, converter, skills_svc):
        external_data = {
            "name": "ExternalPDFParser",
            "description": "Parse PDF files",
            "source_format": "openai_gpt",
            "steps": [
                {"tool": "pdf_read", "params": {"path": "$input"}},
                {"tool": "text_extract", "params": {}},
            ],
            "prompt": "Use this skill to parse PDFs.",
        }
        result = converter.convert_external_skill(external_data)
        assert result["action"] == "created"
        assert result["source_format"] == "openai_gpt"

        skill = skills_svc.get(result["skill_id"])
        assert skill is not None
        assert skill.source == "external_agent"
        assert "external" in skill.tags
        # 验证依赖被提取
        assert "pdf_read" in skill.dependencies
        assert "text_extract" in skill.dependencies

    def test_rule_translate_with_target_id(self, converter, skills_svc):
        external_data = {
            "name": "TestSkill",
            "description": "test",
        }
        result = converter.convert_external_skill(
            external_data, target_id="custom-skill-id",
        )
        assert result["skill_id"] == "custom-skill-id"

    def test_rule_translate_fallback_on_invalid_name(
            self, converter, skills_svc):
        external_data = {
            "name": "",  # 空名
            "description": "edge case",
        }
        result = converter.convert_external_skill(external_data)
        # 应该使用兜底名
        assert result["skill_id"].startswith("external-skill") or \
               result["skill_id"].startswith("ext-")


# ═══════════════════════════════════════════════════════════════════
#  6. LLM 翻译（mock）
# ═══════════════════════════════════════════════════════════════════

class TestLLMTranslation:
    """LLM 翻译测试（使用 mock）"""

    def test_llm_translate_success(self, converter, skills_svc):
        class MockLLMClient:
            def chat(self, prompt):
                import json
                return json.dumps({
                    "id": "llm-generated-skill",
                    "name": "LLM 转换的技能",
                    "description": "由 LLM 翻译而来",
                    "content": "# LLM 转换的技能\n\n由 LLM 自动生成",
                    "tags": ["llm", "external"],
                    "dependencies": [],
                })

        external_data = {"name": "原始外部技能", "description": "..."}
        result = converter.convert_external_skill(
            external_data, llm_client=MockLLMClient(),
        )
        assert result["skill_id"] == "llm-generated-skill"

        skill = skills_svc.get("llm-generated-skill")
        assert skill is not None
        assert "llm" in skill.tags

    def test_llm_failure_falls_back_to_rule(self, converter, skills_svc):
        class BrokenLLMClient:
            def chat(self, prompt):
                raise RuntimeError("LLM 服务不可用")

        external_data = {
            "name": "FallbackTest",
            "description": "test",
        }
        # LLM 失败时降级到规则转换
        result = converter.convert_external_skill(
            external_data, llm_client=BrokenLLMClient(),
        )
        assert result["action"] == "created"


# ═══════════════════════════════════════════════════════════════════
#  7. list_convertible_workflows
# ═══════════════════════════════════════════════════════════════════

class TestListConvertible:
    """列出可转换 workflow"""

    def test_only_qualified_workflows_listed(
            self, wf_repo, skills_svc):
        # 准备：3 个 workflow，只有 1 个满足门控
        wf_ok = _make_workflow(wf_id="wf-ok")
        wf_low_success = _make_workflow(
            wf_id="wf-low", success_count=2, confidence=0.9,
        )
        wf_low_conf = _make_workflow(
            wf_id="wf-low-conf", success_count=10, confidence=0.3,
        )
        wf_converted = _make_workflow(
            wf_id="wf-converted",
            converted_to_skill_id="existing-skill-id",
        )
        for wf in [wf_ok, wf_low_success, wf_low_conf, wf_converted]:
            wf_repo.upsert(wf)

        svc = WorkflowLearningService(repo_path=str(wf_repo._path))
        candidates = svc.list_convertible_workflows()

        assert len(candidates) == 1
        assert candidates[0]["workflow_id"] == "wf-ok"


# ═══════════════════════════════════════════════════════════════════
#  8. WorkflowLearningService 端到端
# ═══════════════════════════════════════════════════════════════════

class TestServiceEndToEnd:
    """WorkflowLearningService.convert_to_skill 端到端"""

    def test_service_convert_to_skill(self, wf_repo, skills_svc, tmp_path):
        # 准备：让 service 用独立的 repo
        svc = WorkflowLearningService(repo_path=str(wf_repo._path))
        wf = _make_workflow()
        # 直接通过 svc.repo 写入，避免 cache 不一致
        svc.repo.upsert(wf)
        svc._rebuild_index()

        result = svc.convert_to_skill(wf.id, skills_service=skills_svc)
        assert result["action"] == "created"

        # 验证 skill 落库
        skill = skills_svc.get(result["skill_id"])
        assert skill is not None

    def test_service_convert_external_skill(self, wf_repo, skills_svc):
        svc = WorkflowLearningService(repo_path=str(wf_repo._path))
        external_data = {
            "name": "ServiceExternalTest",
            "description": "via service",
        }
        result = svc.convert_external_skill(
            external_data, skills_service=skills_svc,
        )
        assert result["action"] == "created"
