"""提示词固定/动态排序验证测试

验证三个核心组装点的固定内容前置、动态内容后置：
1. PromptBuilder.build_context_messages — V1 路径 messages 组装
2. PersonaInjector.build_system_prompt — V2 路径 system_prompt 组装
3. Orchestrator._call_llm — V1 路径集成测试（验证最终传给 LLM 的 messages 顺序）

背景：为最大化 LLM Provider 端 prefix caching 命中率，固定内容（tool_urge、
人格、表达要求、工具状态）必须前置，动态内容（历史消息、身体状态、记忆、
用户输入）必须后置。本地 LLMResponseCache 基于 sha256(prompt) 完整哈希不
区分前缀，故此处只验证 messages/system_prompt 的结构顺序。
"""
import pytest
from unittest.mock import MagicMock, patch

from agent.orchestrator.prompt_builder import PromptBuilder
from persona.persona_injector import PersonaInjector
from persona.persona_model_enhanced import PersonaModel


# ============================================================================
#  测试 1: PromptBuilder.build_context_messages — V1 路径 messages 顺序
# ============================================================================

class FakeMemory:
    """可控的 Memory 替身，返回带可识别标记的 budget_context"""

    def __init__(self):
        self._storage = MagicMock()
        self._storage.load_recent_messages.return_value = []

    def load_summary(self):
        return None

    def get_budget_context(self, recent_messages=None, summary_text=None, tool_results=None):
        return [
            {"role": "user", "content": "BUDGET_MARKER_1"},
            {"role": "assistant", "content": "BUDGET_MARKER_2"},
        ]

    def get_context(self, token_limit=None):
        return []


class TestPromptBuilderMessagesOrder:
    """验证 build_context_messages 的固定/动态顺序"""

    def test_tool_urge_before_budget_context_before_user_input(self):
        """tool_urge(固定) → budget_context(动态) → user_input(动态)"""
        builder = PromptBuilder()
        memory = FakeMemory()
        tool_calling_service = MagicMock()  # 真值触发 tool_urge 前置

        messages = builder.build_context_messages(
            memory=memory,
            tool_calling_service=tool_calling_service,
            user_input="USER_INPUT_MARKER",
            last_tool_steps=[],
        )

        assert len(messages) == 4, "应有 1(tool_urge) + 2(budget_context) + 1(user_input) = 4 条消息"

        # 固定区：messages[0] 为 tool_urge
        assert messages[0]["role"] == "system"
        assert "立即检查" in messages[0]["content"], "固定区 tool_urge 应在 idx0"

        # 动态区：messages[1..2] 为 budget_context
        assert messages[1]["content"] == "BUDGET_MARKER_1"
        assert messages[2]["content"] == "BUDGET_MARKER_2"

        # 动态区：messages[3] 为 user_input
        assert messages[3]["role"] == "user"
        assert messages[3]["content"] == "USER_INPUT_MARKER"

    def test_no_tool_calling_service_skips_tool_urge(self):
        """无 tool_calling_service 时不追加 tool_urge（仅动态区）"""
        builder = PromptBuilder()
        memory = FakeMemory()

        messages = builder.build_context_messages(
            memory=memory,
            tool_calling_service=None,
            user_input="USER_INPUT",
            last_tool_steps=[],
        )

        assert len(messages) == 3
        assert messages[0]["content"] == "BUDGET_MARKER_1"
        assert messages[-1]["role"] == "user"


# ============================================================================
#  测试 2: PersonaInjector.build_system_prompt — V2 路径 system_prompt 顺序
# ============================================================================

class TestPersonaInjectorPromptOrder:
    """验证 build_system_prompt 的固定区/动态区顺序"""

    @pytest.fixture
    def persona_injector(self):
        return PersonaInjector(PersonaModel())

    def test_fixed_before_dynamic(self, persona_injector):
        """固定区(persona+表达要求+tool_status) 在 动态区(body_status+memory+rules) 之前"""
        prompt = persona_injector.build_system_prompt(
            body_status="CPU:70%",
            memory_context="用户喜欢编程",
            tool_status="工具A",
            additional_rules=["规则1"],
        )

        idx_persona = prompt.find("你的身份")
        idx_expression = prompt.find("表达要求")
        idx_tool_status = prompt.find("当前工具与技能状态")
        idx_body_status = prompt.find("当前状态")
        idx_memory = prompt.find("记忆上下文")
        idx_rules = prompt.find("额外指令")

        # 所有节都应存在
        assert idx_persona != -1, "应包含 persona"
        assert idx_expression != -1, "应包含 表达要求"
        assert idx_tool_status != -1, "应包含 tool_status"
        assert idx_body_status != -1, "应包含 body_status"
        assert idx_memory != -1, "应包含 memory_context"
        assert idx_rules != -1, "应包含 additional_rules"

        # 固定区顺序：persona < 表达要求 < tool_status
        assert idx_persona < idx_expression, "persona 应在 表达要求 之前"
        assert idx_expression < idx_tool_status, "表达要求 应在 tool_status 之前"

        # 动态区顺序：body_status < memory < rules
        assert idx_body_status < idx_memory, "body_status 应在 memory 之前"
        assert idx_memory < idx_rules, "memory 应在 rules 之前"

        # 固定区整体在动态区整体之前
        assert idx_tool_status < idx_body_status, "固定区(tool_status) 应在 动态区(body_status) 之前"

    def test_dynamic_sections_optional(self, persona_injector):
        """仅传 body_status 时，固定区仍在前"""
        prompt = persona_injector.build_system_prompt(body_status="CPU:70%")

        idx_expression = prompt.find("表达要求")
        idx_body_status = prompt.find("当前状态")

        assert idx_expression != -1
        assert idx_body_status != -1
        assert idx_expression < idx_body_status


# ============================================================================
#  测试 3: Orchestrator._call_llm — V1 路径集成测试
# ============================================================================

class TestOrchestratorV1MessagesOrder:
    """验证 _call_llm 最终传给 LLM API 的 messages 顺序

    策略：用 object.__new__ 绕过 __init__，手动注入 mock 依赖，
    捕获 _client.chat.completions.create 的 messages 参数。
    """

    def _build_orchestrator_with_mocks(self):
        """构建 mock 好的 Orchestrator 实例"""
        from agent.orchestrator.orchestrator import Orchestrator

        orch = object.__new__(Orchestrator)

        # 基本属性
        orch._current_mode = "default"
        orch._behavior = MagicMock()
        orch._behavior.profile.label = "默认"
        orch._behavior.profile.description = "默认模式"
        orch._memory_token_limit = 8000
        orch._interaction_count = 0
        orch._last_tool_steps = []
        orch._current_tool_steps = []
        orch._last_reasoning = None
        orch._llm_pro = None

        # mock _set_thinking_mode（空操作）
        orch._set_thinking_mode = MagicMock()

        # mock _memory
        memory = MagicMock()
        memory.load_summary.return_value = None
        memory.get_context.return_value = []
        memory.get_working_memory.return_value = {}
        memory._storage.load_recent_messages.return_value = []
        memory.get_budget_context.return_value = [
            {"role": "user", "content": "BUDGET_MARKER_1"},
            {"role": "assistant", "content": "BUDGET_MARKER_2"},
        ]
        memory._token_counter.count.return_value = 100
        memory._token_counter.count_messages.return_value = 100
        orch._memory = memory

        # mock tool_calling_service（真值触发 tool_urge 前置）
        orch._tool_calling_service = MagicMock()

        # mock _build_tool_status_text / _build_skill_instructions
        orch._build_tool_status_text = MagicMock(return_value="TOOL_STATUS_MARKER")
        orch._build_skill_instructions = MagicMock(return_value="SKILL_MARKER")

        # mock _get_enabled_tools_whitelist / _is_smart_tool_selection_enabled
        orch._get_enabled_tools_whitelist = MagicMock(return_value=[])
        orch._is_smart_tool_selection_enabled = MagicMock(return_value=False)

        # mock _select_model_for_request（返回主模型，不切换到 pro）
        orch._select_model_for_request = MagicMock(return_value=("main-model", "main-model"))

        # mock _llm
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "LLM_RESPONSE"
        mock_response.choices[0].message.tool_calls = None
        mock_client.chat.completions.create.return_value = mock_response

        llm = MagicMock()
        llm._get_client.return_value = mock_client
        llm.model = "main-model"
        llm._is_openai_compat.return_value = True
        orch._llm = llm

        # mock _get_lifetrace_context（V1 路径不调用，但以防万一）
        orch._get_lifetrace_context = MagicMock(return_value="")

        return orch, mock_client

    @patch("agent.orchestrator.orchestrator._get_template")
    @patch("agent.tools.get_tool_defs")
    def test_v1_messages_order(self, mock_get_tool_defs, mock_get_template):
        """V1 路径：tool_urge(固定) → budget_context(动态) → user_input(动态)"""
        mock_get_template.return_value = (
            "你是云枢。\n当前日期：{current_date}\n{body_status}\n{mode_name}\n"
            "{mode_description}\n{memory_context}\n{tool_status}\n{skill_instructions}"
        )
        mock_get_tool_defs.return_value = []

        orch, mock_client = self._build_orchestrator_with_mocks()

        result = orch._call_llm("USER_INPUT_MARKER", "CPU: 正常")

        # 捕获 _client.chat.completions.create 的 messages 参数
        assert mock_client.chat.completions.create.called, "应调用 LLM API"
        call_kwargs = mock_client.chat.completions.create.call_args
        api_messages = call_kwargs[1].get("messages") or call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1]["messages"]

        # 验证结构：[system_prompt(固定), tool_urge(固定), budget_context(动态), user_input(动态)]
        assert len(api_messages) >= 4, "至少应有 system_prompt + tool_urge + 2(budget) + user_input"

        # api_messages[0] 为 system_prompt（固定，来自 _get_template + .format()）
        assert api_messages[0]["role"] == "system"

        # api_messages[1] 为 tool_urge（固定）
        assert api_messages[1]["role"] == "system"
        assert "立即检查" in api_messages[1]["content"], "固定区 tool_urge 应在 system_prompt 之后、budget_context 之前"

        # api_messages[2..3] 为 budget_context（动态）
        assert api_messages[2]["content"] == "BUDGET_MARKER_1"
        assert api_messages[3]["content"] == "BUDGET_MARKER_2"

        # api_messages[-1] 为 user_input（动态）
        assert api_messages[-1]["role"] == "user"
        assert api_messages[-1]["content"] == "USER_INPUT_MARKER"

        # 顺序断言：tool_urge index < budget_context index < user_input index
        tool_urge_idx = 1
        budget_idx = 2
        user_idx = len(api_messages) - 1
        assert tool_urge_idx < budget_idx < user_idx, "固定区应在动态区之前"

    @patch("agent.orchestrator.orchestrator._get_template")
    @patch("agent.tools.get_tool_defs")
    def test_v1_without_tool_calling_service(self, mock_get_tool_defs, mock_get_template):
        """无 tool_calling_service 时跳过 tool_urge（仅动态区）"""
        mock_get_template.return_value = (
            "你是云枢。\n{current_date}\n{body_status}\n{mode_name}\n"
            "{mode_description}\n{memory_context}\n{tool_status}\n{skill_instructions}"
        )
        mock_get_tool_defs.return_value = []

        orch, mock_client = self._build_orchestrator_with_mocks()
        orch._tool_calling_service = None  # 关闭 tool_urge

        orch._call_llm("USER_INPUT", "CPU: 正常")

        call_kwargs = mock_client.chat.completions.create.call_args
        api_messages = call_kwargs[1].get("messages") or call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1]["messages"]

        # 无 tool_urge：[system_prompt, budget_context..., user_input]
        assert api_messages[0]["role"] == "system"  # system_prompt
        assert api_messages[1]["content"] == "BUDGET_MARKER_1"  # 直接是 budget_context
        assert api_messages[-1]["role"] == "user"


# ============================================================================
#  测试 4: Few-shot 注入(动态区,user_input 之前)
# ============================================================================

class TestPromptBuilderFewshotInjection:
    """验证 build_context_messages 的 Few-shot 注入位置与兜底"""

    def _build(self):
        builder = PromptBuilder()
        memory = FakeMemory()
        return builder, memory

    def test_fewshot_inserted_before_user_input(self):
        """传 fewshot_samples 时:tool_urge → budget_context → fewshot → user_input"""
        builder, memory = self._build()
        samples = {"web_search": [{"input": {"q": "x"}, "output": {"ok": True}}]}

        messages = builder.build_context_messages(
            memory=memory,
            tool_calling_service=MagicMock(),
            user_input="USER_INPUT_MARKER",
            last_tool_steps=[],
            fewshot_samples=samples,
        )

        # tool_urge(0) → budget1(1) → budget2(2) → fewshot(3) → user_input(4)
        assert len(messages) == 5
        assert "立即检查" in messages[0]["content"]
        assert messages[1]["content"] == "BUDGET_MARKER_1"
        assert messages[2]["content"] == "BUDGET_MARKER_2"
        # fewshot 是 system 消息
        assert messages[3]["role"] == "system"
        assert "脱敏示例" in messages[3]["content"]
        # user_input 始终在末尾(守 [不易])
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "USER_INPUT_MARKER"

    def test_fewshot_absent_when_no_samples(self):
        """fewshot_samples=None:行为与原版一致,user_input 在末尾"""
        builder, memory = self._build()
        messages = builder.build_context_messages(
            memory=memory,
            tool_calling_service=MagicMock(),
            user_input="USER",
            last_tool_steps=[],
            fewshot_samples=None,
        )
        assert len(messages) == 4  # tool_urge + 2 budget + user
        assert messages[-1]["role"] == "user"

    def test_fewshot_absent_when_empty_dict(self):
        """fewshot_samples={} 不注入"""
        builder, memory = self._build()
        messages = builder.build_context_messages(
            memory=memory,
            tool_calling_service=MagicMock(),
            user_input="USER",
            last_tool_steps=[],
            fewshot_samples={},
        )
        assert len(messages) == 4
        assert messages[-1]["role"] == "user"

    def test_fewshot_absent_when_build_returns_none(self):
        """build_fewshot_message 返回 None 时不注入"""
        builder, memory = self._build()
        with patch("agent.orchestrator.prompt_builder.build_fewshot_message",
                   return_value=None):
            messages = builder.build_context_messages(
                memory=memory,
                tool_calling_service=MagicMock(),
                user_input="USER",
                last_tool_steps=[],
                fewshot_samples={"tool": [{"input": {}, "output": {}}]},
            )
        assert len(messages) == 4
        assert messages[-1]["role"] == "user"

    def test_build_fewshot_message_format(self):
        """build_fewshot_message 返回合法 JSON,含 extracted_params/missing_params"""
        from agent.orchestrator.prompt_builder import build_fewshot_message
        samples = {"web_search": [{"input": {"query": "q"}, "output": {"ok": True}}]}
        msg = build_fewshot_message(samples)
        assert msg is not None
        assert msg["role"] == "system"
        # content 含可解析 JSON
        import json as _json
        # 提取 JSON 部分(跳过前导文案)
        content = msg["content"]
        json_start = content.find("{")
        payload = _json.loads(content[json_start:])
        assert "examples" in payload
        ex = payload["examples"][0]
        assert ex["tool"] == "web_search"
        assert ex["input"] == {"query": "q"}
        assert ex["extracted_params"] == ["query"]
        assert ex["missing_params"] == []

    def test_build_fewshot_message_none_for_empty(self):
        from agent.orchestrator.prompt_builder import build_fewshot_message
        assert build_fewshot_message(None) is None
        assert build_fewshot_message({}) is None
        assert build_fewshot_message({"t": []}) is None
