"""WorkflowToSkillConverter 集成测试

覆盖工作流→技能转换器的核心功能：
1. 工作流不存在异常
2. 幂等转换（已转换返回 already_converted）
3. 质量门控校验
4. force 跳过门控
5. 正常转换全流程
6. ID 冲突解决
7. SKILL.md 内容编译
8. 外部技能规则式翻译
9. 外部技能 LLM 翻译
10. ID 派生逻辑
"""

import json
import pytest
from unittest.mock import MagicMock

from agent.workflow_learning.models import LearnedWorkflow, WorkflowStep, WorkflowStatus
from agent.workflow_learning.skill_converter import (
    WorkflowToSkillConverter,
    WorkflowConvertError,
    MIN_SUCCESS_COUNT,
    MIN_CONFIDENCE,
    MIN_PRIORITY,
)

pytestmark = pytest.mark.integration


def _make_qualified_workflow(
    wf_id: str = "wf-qualified-test",
    name: str = " qualified 工作流",
    success_count: int = 10,
    confidence: float = 0.9,
    priority: int = 80,
) -> LearnedWorkflow:
    """构造通过质量门控的工作流"""
    return LearnedWorkflow(
        id=wf_id,
        name=name,
        description="测试用工作流",
        task_signature="search_and_summarize",
        trigger_patterns=["搜索*", "查找*"],
        steps=[
            WorkflowStep(
                step_id="s1",
                tool_name="web_search",
                params_template={"query": "$input"},
                output_key="search_results",
                description="执行搜索",
                timeout_ms=30000,
            ),
            WorkflowStep(
                step_id="s2",
                tool_name="summarize",
                params_template={"text": "$prev_output"},
                output_key="summary",
                timeout_ms=15000,
            ),
        ],
        expected_output_pattern=r"^\{.*\}$",
        source_session_id="session-001",
        source_user_input="帮我搜索并总结",
        success_count=success_count,
        failure_count=1,
        confidence=confidence,
        priority=priority,
        status=WorkflowStatus.ACTIVE,
        enabled=True,
        tags=["test", "search"],
    )


class TestSkillConverterIntegration:
    """WorkflowToSkillConverter 集成测试"""

    def test_convert_workflow_not_found(self):
        """测试 1：工作流不存在抛异常"""
        mock_svc = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get.return_value = None

        converter = WorkflowToSkillConverter(mock_svc, mock_repo)

        with pytest.raises(WorkflowConvertError) as exc_info:
            converter.convert_workflow_to_skill("wf-nonexistent")

        assert exc_info.value.code == "NOT_FOUND"
        assert "wf-nonexistent" in str(exc_info.value)

    def test_convert_workflow_idempotent(self):
        """测试 2：已转换的工作流返回 already_converted"""
        mock_svc = MagicMock()
        mock_repo = MagicMock()

        wf = _make_qualified_workflow()
        wf.converted_to_skill_id = "existing-skill-id"
        mock_repo.get.return_value = wf

        existing_skill = MagicMock()
        existing_skill.name = "已存在技能"
        existing_skill.version = "1.0.0"
        mock_svc.get.return_value = existing_skill

        converter = WorkflowToSkillConverter(mock_svc, mock_repo)
        result = converter.convert_workflow_to_skill(wf.id)

        assert result["action"] == "already_converted"
        assert result["skill_id"] == "existing-skill-id"
        assert result["skill_name"] == "已存在技能"
        # 不应调用 create_manual
        mock_svc.create_manual.assert_not_called()

    def test_convert_workflow_quality_gate_failed(self):
        """测试 3：质量门控不通过"""
        mock_svc = MagicMock()
        mock_repo = MagicMock()

        # 构造不达标的工作流
        wf = _make_qualified_workflow(
            success_count=2,  # < MIN_SUCCESS_COUNT(5)
            confidence=0.3,   # < MIN_CONFIDENCE(0.7)
            priority=30,      # < MIN_PRIORITY(50)
        )
        mock_repo.get.return_value = wf

        converter = WorkflowToSkillConverter(mock_svc, mock_repo)

        with pytest.raises(WorkflowConvertError) as exc_info:
            converter.convert_workflow_to_skill(wf.id)

        assert exc_info.value.code == "QUALITY_GATE_FAILED"
        error_msg = str(exc_info.value)
        assert "success_count" in error_msg
        assert "confidence" in error_msg
        assert "priority" in error_msg

    def test_convert_workflow_force_skips_quality_gate(self):
        """测试 4：force=True 跳过质量门控"""
        mock_svc = MagicMock()
        mock_repo = MagicMock()

        wf = _make_qualified_workflow(success_count=0, confidence=0.1)
        mock_repo.get.return_value = wf

        # mock skill 不存在（用于 _resolve_id_conflict）
        mock_svc.get.side_effect = Exception("not found")

        created_skill = MagicMock()
        created_skill.id = "new-skill-id"
        created_skill.name = wf.name
        created_skill.version = "1.0.0"
        mock_svc.create_manual.return_value = created_skill

        converter = WorkflowToSkillConverter(mock_svc, mock_repo)
        result = converter.convert_workflow_to_skill(wf.id, force=True)

        assert result["action"] == "created"
        assert result["skill_id"] == "new-skill-id"
        mock_svc.create_manual.assert_called_once()

    def test_convert_workflow_success(self):
        """测试 5：正常转换全流程"""
        mock_svc = MagicMock()
        mock_repo = MagicMock()

        wf = _make_qualified_workflow()
        mock_repo.get.return_value = wf

        # mock skill 不存在
        mock_svc.get.side_effect = Exception("not found")

        created_skill = MagicMock()
        created_skill.id = "wf-qualified-test-skill"
        created_skill.name = wf.name
        created_skill.version = "1.0.0"
        mock_svc.create_manual.return_value = created_skill

        converter = WorkflowToSkillConverter(mock_svc, mock_repo)
        result = converter.convert_workflow_to_skill(wf.id)

        assert result["action"] == "created"
        assert result["skill_id"] == "wf-qualified-test-skill"
        assert result["skill_name"] == wf.name

        # 验证 create_manual 被调用，且参数正确
        mock_svc.create_manual.assert_called_once()
        skill_data = mock_svc.create_manual.call_args[0][0]
        assert skill_data["name"] == wf.name
        assert skill_data["category"] == "custom"
        assert "from_workflow" in skill_data["tags"]
        assert "auto_converted" in skill_data["tags"]
        assert "web_search" in skill_data["dependencies"]
        assert "summarize" in skill_data["dependencies"]
        assert skill_data["content_type"] == "markdown"

        # 验证回写 converted_to_skill_id
        assert wf.converted_to_skill_id == "wf-qualified-test-skill"
        mock_repo.upsert.assert_called_once_with(wf)

    def test_convert_workflow_id_conflict_resolution(self):
        """测试 6：ID 冲突时加后缀"""
        mock_svc = MagicMock()
        mock_repo = MagicMock()

        wf = _make_qualified_workflow()
        mock_repo.get.return_value = wf

        # 第一次 get 返回已存在（冲突），第二次返回不存在
        call_count = [0]
        def mock_get(skill_id):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock()  # 原始 ID 已存在
            raise Exception("not found")

        mock_svc.get.side_effect = mock_get

        created_skill = MagicMock()
        created_skill.id = "wf-qualified-test-skill-2"
        created_skill.name = wf.name
        created_skill.version = "1.0.0"
        mock_svc.create_manual.return_value = created_skill

        converter = WorkflowToSkillConverter(mock_svc, mock_repo)
        result = converter.convert_workflow_to_skill(wf.id)

        assert result["action"] == "created"
        # 验证使用了带后缀的 ID
        skill_data = mock_svc.create_manual.call_args[0][0]
        assert skill_data["id"] == "wf-qualified-test-skill-2"

    def test_compile_skill_content(self):
        """测试 7：SKILL.md 内容编译"""
        mock_svc = MagicMock()
        mock_repo = MagicMock()
        converter = WorkflowToSkillConverter(mock_svc, mock_repo)

        wf = _make_qualified_workflow()
        content = converter._compile_skill_content(wf)

        # 验证内容结构
        assert f"# {wf.name}" in content
        assert "## 触发条件" in content
        assert "## 步骤清单" in content
        assert "web_search" in content
        assert "summarize" in content
        assert "## 预期输出特征" in content
        assert "## 来源" in content
        assert f"workflow_id: `{wf.id}`" in content
        assert f"success_count: {wf.success_count}" in content

    def test_convert_external_skill_rule_translate(self):
        """测试 8：外部技能规则式翻译"""
        mock_svc = MagicMock()
        mock_repo = MagicMock()

        # mock skill 不存在
        mock_svc.get.side_effect = Exception("not found")

        created_skill = MagicMock()
        created_skill.id = "gpt-search-helper"
        created_skill.name = "GPT Search Helper"
        created_skill.version = "1.0.0"
        mock_svc.create_manual.return_value = created_skill

        converter = WorkflowToSkillConverter(mock_svc, mock_repo)

        external_data = {
            "name": "GPT Search Helper",
            "description": "A search helper skill from GPT",
            "steps": [
                {"tool": "search", "params": {"query": "$input"}},
                {"tool": "format", "params": {"output": "$prev_output"}},
            ],
            "prompt": "Use this skill to search and format results.",
            "source_format": "openai_gpts",
        }

        result = converter.convert_external_skill(external_data)

        assert result["action"] == "created"
        assert result["skill_id"] == "gpt-search-helper"
        assert result["source_format"] == "openai_gpts"

        # 验证 skill 数据
        skill_data = mock_svc.create_manual.call_args[0][0]
        assert skill_data["name"] == "GPT Search Helper"
        assert "external" in skill_data["tags"]
        assert "imported" in skill_data["tags"]
        assert "search" in skill_data["dependencies"]
        assert "format" in skill_data["dependencies"]
        assert "## 使用说明" in skill_data["content"]
        assert "## 步骤" in skill_data["content"]

    def test_convert_external_skill_llm_translate(self):
        """测试 9：外部技能 LLM 翻译"""
        mock_svc = MagicMock()
        mock_repo = MagicMock()

        # mock skill 不存在
        mock_svc.get.side_effect = Exception("not found")

        created_skill = MagicMock()
        created_skill.id = "llm-translated-skill"
        created_skill.name = "LLM Translated Skill"
        created_skill.version = "1.0.0"
        mock_svc.create_manual.return_value = created_skill

        # mock LLM client
        mock_llm = MagicMock()
        mock_llm.chat.return_value = json.dumps({
            "id": "llm-translated-skill",
            "name": "LLM Translated Skill",
            "description": "Translated by LLM",
            "content": "# LLM Translated Skill\n\nContent here",
            "tags": ["llm", "translated"],
            "dependencies": ["tool_a", "tool_b"],
        })

        converter = WorkflowToSkillConverter(mock_svc, mock_repo)

        external_data = {
            "name": "External Skill",
            "description": "Some external skill",
            "source_format": "claude_skills",
        }

        result = converter.convert_external_skill(external_data, llm_client=mock_llm)

        assert result["action"] == "created"
        assert result["source_format"] == "claude_skills"

        # 验证 LLM 被调用
        mock_llm.chat.assert_called_once()

        # 验证 skill 数据来自 LLM
        skill_data = mock_svc.create_manual.call_args[0][0]
        assert skill_data["name"] == "LLM Translated Skill"
        assert "llm" in skill_data["tags"]

    def test_derive_skill_id_and_external_id(self):
        """测试 10：ID 派生逻辑"""
        mock_svc = MagicMock()
        mock_repo = MagicMock()
        converter = WorkflowToSkillConverter(mock_svc, mock_repo)

        # _derive_skill_id: workflow_id → skill_id
        assert converter._derive_skill_id("wf-test-123") == "wf-test-123-skill"
        assert converter._derive_skill_id("WF-UPPER-Case") == "wf-upper-case-skill"
        # _derive_skill_id 不合并连续 -（与 _derive_external_id 行为不同）
        assert converter._derive_skill_id("wf-special@chars!") == "wf-special-chars--skill"

        # _derive_external_id: name → skill_id
        assert converter._derive_external_id("GPT Helper", "") == "gpt-helper"
        assert converter._derive_external_id("", "custom-id") == "custom-id"
        assert converter._derive_external_id("123numeric", "") == "ext-123numeric"
        assert converter._derive_external_id("", "") == "external-skill"

    def test_extract_json_from_response_variants(self):
        """测试 11：LLM 响应 JSON 提取的多种场景"""
        # 直接返回 dict
        assert WorkflowToSkillConverter._extract_json_from_response({"a": 1}) == {"a": 1}

        # 纯 JSON 字符串
        result = WorkflowToSkillConverter._extract_json_from_response('{"key": "value"}')
        assert result == {"key": "value"}

        # 带前后缀的 JSON
        result = WorkflowToSkillConverter._extract_json_from_response(
            'Here is the JSON:\n{"name": "test", "tags": ["a"]}\nDone.'
        )
        assert result == {"name": "test", "tags": ["a"]}

        # 无效 JSON 返回 None
        assert WorkflowToSkillConverter._extract_json_from_response("no json here") is None
        assert WorkflowToSkillConverter._extract_json_from_response("{invalid}") is None

    def test_convert_workflow_create_failed(self):
        """测试 12：create_manual 失败抛异常"""
        mock_svc = MagicMock()
        mock_repo = MagicMock()

        wf = _make_qualified_workflow()
        mock_repo.get.return_value = wf
        mock_svc.get.side_effect = Exception("not found")
        mock_svc.create_manual.side_effect = RuntimeError("DB connection failed")

        converter = WorkflowToSkillConverter(mock_svc, mock_repo)

        with pytest.raises(WorkflowConvertError) as exc_info:
            converter.convert_workflow_to_skill(wf.id)

        assert exc_info.value.code == "CREATE_FAILED"
        assert "DB connection failed" in str(exc_info.value)
