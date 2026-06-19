"""DigitalLifeV2 核心闭环逻辑单元测试"""
import pytest
from unittest.mock import MagicMock, patch

from agent.digital_life_v2 import DigitalLifeV2


class TestProcessUserInput:
    """测试 _process_user_input 核心闭环"""

    def test_process_user_input_basic_flow(self):
        """测试基本用户输入处理流程"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_reading = MagicMock()
                    mock_reading.sensor_name = "test"
                    mock_reading.value = 100
                    mock_reading.unit = "%"
                    mock_reading.severity = "normal"
                    mock_reading.to_dict.return_value = {"sensor": "test", "value": 100}
                    
                    mock_sensor.return_value.collect_quick.return_value = [mock_reading]
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    mock_behavior.return_value.can_execute.return_value = (True, "")
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常模式",
                        description="正常运行",
                        response_prefix="",
                        enable_reflection=False
                    )
                    
                    mock_recorder = MagicMock()
                    mock_persona = MagicMock()
                    mock_persona.should_refuse_task.return_value = (False, "")
                    mock_extractor = MagicMock()
                    mock_extractor.update_incremental = MagicMock()
                    
                    dl = DigitalLifeV2()
                    dl._llm = None  # 走离线响应路径
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = mock_recorder
                    dl._persona_initialized = True
                    dl._persona_injector = mock_persona
                    dl._persona_extractor = mock_extractor
                    dl._distillation_enabled = False
                    dl._memory_initialized = True
                    dl._old_memory = MagicMock()
                    dl._behavior = mock_behavior.return_value
                    dl.start()
                    
                    result = dl._process_user_input("Hello")
                    
                    assert result is not None
                    assert len(result) > 0
                    mock_recorder.record_chat.assert_called()

    def test_process_user_input_behavior_reject(self):
        """测试行为控制器拒绝请求"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="WARNING")
                    mock_behavior.return_value.can_execute.return_value = (False, "行为拒绝")
                    mock_behavior.return_value.profile = MagicMock(
                        label="警告模式",
                        description="警告",
                        response_prefix=""
                    )
                    
                    dl = DigitalLifeV2()
                    dl._behavior = mock_behavior.return_value
                    dl.start()
                    
                    result = dl._process_user_input("危险操作")
                    
                    assert "拒绝" in result or "无法执行" in result

    def test_process_user_input_persona_reject(self):
        """测试人格系统拒绝请求"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    mock_behavior.return_value.can_execute.return_value = (True, "")
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常模式",
                        description="正常运行",
                        response_prefix="",
                        enable_reflection=False
                    )
                    
                    mock_persona = MagicMock()
                    mock_persona.should_refuse_task.return_value = (True, "人格拒绝")
                    mock_extractor = MagicMock()
                    mock_extractor.update_incremental = MagicMock()
                    
                    dl = DigitalLifeV2()
                    dl._llm = None
                    dl._persona_initialized = True
                    dl._persona_injector = mock_persona
                    dl._persona_extractor = mock_extractor
                    dl._distillation_enabled = False
                    dl._behavior = mock_behavior.return_value
                    dl.start()
                    
                    result = dl._process_user_input("不合适的请求")
                    
                    assert "拒绝" in result or "无法执行" in result

    def test_process_user_input_distillation_enabled(self):
        """测试启用人格蒸馏时的增量更新"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    mock_behavior.return_value.can_execute.return_value = (True, "")
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常模式",
                        description="正常运行",
                        response_prefix="",
                        enable_reflection=False
                    )
                    
                    mock_persona = MagicMock()
                    mock_persona.should_refuse_task.return_value = (False, "")
                    mock_extractor = MagicMock()
                    mock_extractor.update_incremental = MagicMock()
                    
                    dl = DigitalLifeV2()
                    dl._llm = None
                    dl._persona_initialized = True
                    dl._persona_injector = mock_persona
                    dl._persona_extractor = mock_extractor
                    dl._distillation_enabled = True
                    dl._behavior = mock_behavior.return_value
                    dl.start()
                    
                    dl._process_user_input("测试输入")
                    
                    mock_extractor.update_incremental.assert_called_once()

    def test_process_user_input_reflection_enabled(self):
        """测试启用反思功能"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    mock_behavior.return_value.can_execute.return_value = (True, "")
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常模式",
                        description="正常运行",
                        response_prefix="",
                        enable_reflection=True
                    )
                    
                    mock_persona = MagicMock()
                    mock_persona.should_refuse_task.return_value = (False, "")
                    
                    dl = DigitalLifeV2()
                    dl._llm = None
                    dl._persona_initialized = True
                    dl._persona_injector = mock_persona
                    dl._distillation_enabled = False
                    dl._behavior = mock_behavior.return_value
                    dl.start()
                    
                    result = dl._process_user_input("测试输入")
                    
                    assert result is not None

    def test_process_user_input_periodic_distillation(self):
        """测试周期性人格蒸馏"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    mock_behavior.return_value.can_execute.return_value = (True, "")
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常模式",
                        description="正常运行",
                        response_prefix="",
                        enable_reflection=False
                    )
                    
                    mock_persona = MagicMock()
                    mock_persona.should_refuse_task.return_value = (False, "")
                    mock_extractor = MagicMock()
                    mock_extractor.update_incremental = MagicMock()
                    
                    dl = DigitalLifeV2()
                    dl._llm = None
                    dl._persona_initialized = True
                    dl._persona_injector = mock_persona
                    dl._persona_extractor = mock_extractor
                    dl._distillation_enabled = True
                    dl._distillation_interval = 1
                    dl._behavior = mock_behavior.return_value
                    
                    original_run_distillation = dl._run_persona_distillation
                    dl._run_persona_distillation = MagicMock()
                    
                    dl.start()
                    
                    dl._process_user_input("测试输入")
                    
                    dl._run_persona_distillation.assert_called_once()
                    dl._run_persona_distillation = original_run_distillation


class TestCallLLM:
    """测试 _call_llm 方法"""

    def test_call_llm_basic(self):
        """测试基本 LLM 调用"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_llm = MagicMock()
                    mock_llm.chat.return_value = "LLM 响应"
                    
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常模式",
                        description="正常运行",
                        response_prefix=""
                    )
                    
                    mock_recorder = MagicMock()
                    mock_retriever = MagicMock()
                    mock_retriever.retrieve.return_value = []
                    mock_recorder.get_recent_chat.return_value = []
                    mock_recorder.global_tree.load_summary.return_value = ""
                    
                    mock_persona = MagicMock()
                    mock_persona.build_system_prompt.return_value = "系统提示词"
                    
                    dl = DigitalLifeV2()
                    dl._memory_initialized = True  # 避免 _ensure_memory 覆盖 _llm
                    dl._llm = mock_llm
                    dl._behavior = mock_behavior.return_value
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = mock_recorder
                    dl._memory_retriever = mock_retriever
                    dl._persona_initialized = True
                    dl._persona_injector = mock_persona
                    
                    result = dl._call_llm("Hello", "身体状态")
                    
                    assert result == "LLM 响应"
                    mock_llm.chat.assert_called_once()

    def test_call_llm_with_response_prefix(self):
        """测试带响应前缀的 LLM 调用"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_llm = MagicMock()
                    mock_llm.chat.return_value = "LLM 响应"
                    
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常模式",
                        description="正常运行",
                        response_prefix="【前缀】"
                    )
                    
                    mock_recorder = MagicMock()
                    mock_retriever = MagicMock()
                    mock_retriever.retrieve.return_value = []
                    mock_recorder.get_recent_chat.return_value = []
                    mock_recorder.global_tree.load_summary.return_value = ""
                    
                    dl = DigitalLifeV2()
                    dl._memory_initialized = True
                    dl._llm = mock_llm
                    dl._behavior = mock_behavior.return_value
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = mock_recorder
                    dl._memory_retriever = mock_retriever
                    dl._persona_initialized = False
                    
                    result = dl._call_llm("Hello", "身体状态")
                    
                    assert "【前缀】" in result
                    assert "LLM 响应" in result

    def test_call_llm_no_llm_service(self):
        """测试无 LLM 服务时的离线响应"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    dl._llm = None
                    
                    result = dl._call_llm("Hello", "身体状态")
                    
                    assert result is not None
                    assert len(result) > 0

    def test_call_llm_llm_error(self):
        """测试 LLM 调用异常"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    from memory.llm_service import LLMServiceError
                    
                    mock_llm = MagicMock()
                    mock_llm.chat.side_effect = LLMServiceError("连接失败")
                    
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常模式",
                        description="正常运行",
                        response_prefix=""
                    )
                    
                    mock_recorder = MagicMock()
                    mock_retriever = MagicMock()
                    mock_retriever.retrieve.return_value = []
                    mock_recorder.get_recent_chat.return_value = []
                    mock_recorder.global_tree.load_summary.return_value = ""
                    
                    dl = DigitalLifeV2()
                    dl._memory_initialized = True
                    dl._llm = mock_llm
                    dl._behavior = mock_behavior.return_value
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = mock_recorder
                    dl._memory_retriever = mock_retriever
                    dl._persona_initialized = False
                    
                    result = dl._call_llm("Hello", "身体状态")
                    
                    assert "LLM" in result or "失败" in result

    def test_call_llm_memory_context(self):
        """测试带记忆上下文的 LLM 调用"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_llm = MagicMock()
                    mock_llm.chat.return_value = "LLM 响应"
                    
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常模式",
                        description="正常运行",
                        response_prefix=""
                    )
                    
                    mock_recorder = MagicMock()
                    mock_retriever = MagicMock()
                    mock_retriever.retrieve.return_value = [
                        MagicMock(content="记忆1"),
                        MagicMock(content="记忆2")
                    ]
                    mock_recorder.get_recent_chat.return_value = []
                    mock_recorder.global_tree.load_summary.return_value = "全局摘要"
                    
                    dl = DigitalLifeV2()
                    dl._llm = mock_llm
                    dl._behavior = mock_behavior.return_value
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = mock_recorder
                    dl._memory_retriever = mock_retriever
                    dl._persona_initialized = False
                    
                    dl._call_llm("Hello", "身体状态")
                    
                    mock_retriever.retrieve.assert_called_once_with(query="Hello", limit=5)

    def test_call_llm_old_memory_context(self):
        """测试旧记忆系统上下文"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_llm = MagicMock()
                    mock_llm.chat.return_value = "LLM 响应"
                    
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常模式",
                        description="正常运行",
                        response_prefix=""
                    )
                    
                    mock_recorder = MagicMock()
                    mock_retriever = MagicMock()
                    mock_retriever.retrieve.return_value = []
                    mock_recorder.get_recent_chat.return_value = []
                    mock_recorder.global_tree.load_summary.return_value = ""
                    
                    mock_old_memory = MagicMock()
                    mock_old_memory.get_context.return_value = [
                        {"role": "user", "content": "历史消息"}
                    ]
                    
                    dl = DigitalLifeV2()
                    dl._llm = mock_llm
                    dl._behavior = mock_behavior.return_value
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = mock_recorder
                    dl._memory_retriever = mock_retriever
                    dl._persona_initialized = False
                    dl._memory_initialized = True
                    dl._old_memory = mock_old_memory
                    
                    dl._call_llm("Hello", "身体状态")
                    
                    mock_old_memory.get_context.assert_called_once_with(token_limit=2048)

    def test_call_llm_old_memory_error(self):
        """测试旧记忆系统异常处理"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_llm = MagicMock()
                    mock_llm.chat.return_value = "LLM 响应"
                    
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常模式",
                        description="正常运行",
                        response_prefix=""
                    )
                    
                    mock_recorder = MagicMock()
                    mock_retriever = MagicMock()
                    mock_retriever.retrieve.return_value = []
                    mock_recorder.get_recent_chat.return_value = []
                    mock_recorder.global_tree.load_summary.return_value = ""
                    
                    mock_old_memory = MagicMock()
                    mock_old_memory.get_context.side_effect = Exception("记忆错误")
                    
                    dl = DigitalLifeV2()
                    dl._llm = mock_llm
                    dl._behavior = mock_behavior.return_value
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = mock_recorder
                    dl._memory_retriever = mock_retriever
                    dl._persona_initialized = False
                    dl._memory_initialized = True
                    dl._old_memory = mock_old_memory
                    
                    result = dl._call_llm("Hello", "身体状态")
                    
                    assert result == "LLM 响应"


class TestGetLifetraceContext:
    """测试 _get_lifetrace_context 方法"""

    def test_get_lifetrace_context_not_initialized(self):
        """测试 LifeTrace 未初始化时返回默认值"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    dl._lifetrace_initialized = False
                    
                    result = dl._get_lifetrace_context("测试查询")
                    
                    assert "暂无记忆内容" in result

    def test_get_lifetrace_context_full(self):
        """测试完整的记忆上下文获取"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_recorder = MagicMock()
                    mock_retriever = MagicMock()
                    
                    mock_recorder.global_tree.load_summary.return_value = "全局摘要"
                    mock_retriever.retrieve.return_value = [
                        MagicMock(content="相关记忆1"),
                        MagicMock(content="相关记忆2")
                    ]
                    mock_node = MagicMock()
                    mock_node.metadata = {"role": "user"}
                    mock_node.content = "最近消息"
                    mock_recorder.get_recent_chat.return_value = [mock_node]
                    
                    dl = DigitalLifeV2()
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = mock_recorder
                    dl._memory_retriever = mock_retriever
                    
                    result = dl._get_lifetrace_context("测试查询")
                    
                    assert "全局摘要" in result
                    assert "相关记忆" in result
                    assert "最近对话" in result

    def test_get_lifetrace_context_retrieval_error(self):
        """测试记忆检索异常处理"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_recorder = MagicMock()
                    mock_retriever = MagicMock()
                    
                    mock_recorder.global_tree.load_summary.return_value = "全局摘要"
                    mock_retriever.retrieve.side_effect = Exception("检索失败")
                    mock_recorder.get_recent_chat.return_value = []
                    
                    dl = DigitalLifeV2()
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = mock_recorder
                    dl._memory_retriever = mock_retriever
                    
                    result = dl._get_lifetrace_context("测试查询")
                    
                    assert "全局摘要" in result
