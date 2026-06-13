"""DigitalLifeV2 chat 和 _process_user_input 核心方法测试"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timezone

from agent.digital_life_v2 import DigitalLifeV2


class TestDigitalLifeV2Chat:
    """测试 chat 方法"""

    def test_chat_not_running(self):
        """测试未运行时的聊天"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()

                    result = dl.chat("Hello")

                    assert result == "我还没有被唤醒。请先调用 start() 让我醒来。"

    def test_chat_normal_flow(self):
        """测试正常对话流程"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    dl._running = True
                    dl._interaction_count = 0
                    
                    # 模拟 process_user_input 返回
                    mock_response = "这是一个回复"
                    dl._process_user_input = MagicMock(return_value=mock_response)

                    result = dl.chat("Hello")

                    assert result == mock_response
                    assert dl._interaction_count == 1
                    dl._process_user_input.assert_called_once_with("Hello")

    def test_chat_empty_input(self):
        """测试空输入"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    dl._running = True
                    dl._interaction_count = 0
                    
                    dl._process_user_input = MagicMock(return_value="")

                    result = dl.chat("")

                    assert dl._interaction_count == 1


class TestDigitalLifeV2ProcessUserInput:
    """测试 _process_user_input 方法"""

    def test_process_user_input_normal_flow(self):
        """测试正常处理流程"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            mock_sensor.return_value.collect_quick.return_value = []
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                mock_behavior.return_value.can_execute.return_value = (True, None)
                mock_behavior.return_value.profile.enable_reflection = False
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    
                    # 模拟所有懒加载模块已初始化
                    dl._lifetrace_initialized = True
                    dl._persona_initialized = True
                    dl._memory_initialized = True
                    dl._injector_initialized = True
                    dl._trace_recorder = MagicMock()
                    dl._persona_extractor = MagicMock()
                    dl._persona_injector = MagicMock()
                    dl._persona_injector.should_refuse_task.return_value = (False, "")
                    dl._llm = MagicMock()
                    dl._old_memory = MagicMock()
                    dl._interaction_count = 1
                    dl._distillation_enabled = False
                    dl._behavior = mock_behavior.return_value  # 设置行为控制器
                    
                    # 模拟方法返回值
                    dl.check_health = MagicMock(return_value=[])
                    dl._build_body_status = MagicMock(return_value="Body status")
                    dl._call_llm = MagicMock(return_value="LLM response")
                    dl.self_reflect = MagicMock()

                    result = dl._process_user_input("Hello")

                    assert result == "LLM response"
                    dl.check_health.assert_called_once()
                    dl._build_body_status.assert_called_once()
                    dl._call_llm.assert_called_once_with("Hello", "Body status")

    def test_process_user_input_rejected_by_behavior(self):
        """测试被行为控制器拒绝"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            mock_sensor.return_value.collect_quick.return_value = []
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                mock_behavior.return_value.can_execute.return_value = (False, "行为拒绝")
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = MagicMock()
                    dl._interaction_count = 1
                    
                    dl.check_health = MagicMock(return_value=[])
                    dl._build_reject_response = MagicMock(return_value="拒绝响应")

                    result = dl._process_user_input("危险输入")

                    assert result == "拒绝响应"
                    dl._build_reject_response.assert_called_once()

    def test_process_user_input_rejected_by_persona(self):
        """测试被人格系统拒绝"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            mock_sensor.return_value.collect_quick.return_value = []
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                mock_behavior.return_value.can_execute.return_value = (True, None)
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    
                    dl._lifetrace_initialized = True
                    dl._persona_initialized = True
                    dl._persona_extractor = MagicMock()  # 添加这个
                    dl._trace_recorder = MagicMock()
                    dl._persona_injector = MagicMock()
                    dl._persona_injector.should_refuse_task.return_value = (True, "人格拒绝")
                    dl._interaction_count = 1
                    dl._behavior = mock_behavior.return_value
                    
                    dl.check_health = MagicMock(return_value=[])
                    dl._build_reject_response = MagicMock(return_value="拒绝响应")

                    result = dl._process_user_input("不适合的任务")

                    assert result == "拒绝响应"
                    dl._persona_injector.should_refuse_task.assert_called_once()

    def test_process_user_input_with_distillation(self):
        """测试带人格蒸馏的处理流程"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            mock_sensor.return_value.collect_quick.return_value = []
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                mock_behavior.return_value.can_execute.return_value = (True, None)
                mock_behavior.return_value.profile.enable_reflection = False
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    
                    dl._lifetrace_initialized = True
                    dl._persona_initialized = True
                    dl._memory_initialized = True
                    dl._injector_initialized = True
                    dl._trace_recorder = MagicMock()
                    dl._persona_extractor = MagicMock()
                    dl._persona_injector = MagicMock()
                    dl._persona_injector.should_refuse_task.return_value = (False, "")
                    dl._llm = MagicMock()
                    dl._old_memory = MagicMock()
                    dl._interaction_count = 1
                    dl._distillation_enabled = True
                    dl._behavior = mock_behavior.return_value
                    
                    dl.check_health = MagicMock(return_value=[])
                    dl._build_body_status = MagicMock(return_value="Body status")
                    dl._call_llm = MagicMock(return_value="LLM response")
                    dl.self_reflect = MagicMock()
                    dl._run_persona_distillation = MagicMock()

                    result = dl._process_user_input("Hello")

                    assert result == "LLM response"
                    dl._persona_extractor.update_incremental.assert_called_once()

    def test_process_user_input_periodic_distillation(self):
        """测试周期性人格蒸馏"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            mock_sensor.return_value.collect_quick.return_value = []
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                mock_behavior.return_value.can_execute.return_value = (True, None)
                mock_behavior.return_value.profile.enable_reflection = False
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    
                    dl._lifetrace_initialized = True
                    dl._persona_initialized = True
                    dl._memory_initialized = True
                    dl._injector_initialized = True
                    dl._trace_recorder = MagicMock()
                    dl._persona_extractor = MagicMock()
                    dl._persona_injector = MagicMock()
                    dl._persona_injector.should_refuse_task.return_value = (False, "")
                    dl._llm = MagicMock()
                    dl._old_memory = MagicMock()
                    dl._interaction_count = 10  # 正好触发周期
                    dl._distillation_enabled = True
                    dl._distillation_interval = 10
                    dl._behavior = mock_behavior.return_value
                    
                    dl.check_health = MagicMock(return_value=[])
                    dl._build_body_status = MagicMock(return_value="Body status")
                    dl._call_llm = MagicMock(return_value="LLM response")
                    dl.self_reflect = MagicMock()
                    dl._run_persona_distillation = MagicMock()

                    result = dl._process_user_input("Hello")

                    assert result == "LLM response"
                    dl._run_persona_distillation.assert_called_once()


class TestDigitalLifeV2CallLLM:
    """测试 _call_llm 方法"""

    def test_call_llm_success(self):
        """测试LLM调用成功"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                mock_profile = MagicMock()
                mock_profile.response_prefix = ""
                mock_behavior.return_value.profile = mock_profile
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    
                    dl._lifetrace_initialized = True
                    dl._persona_initialized = True
                    dl._memory_initialized = True
                    dl._llm = MagicMock()
                    dl._llm.chat.return_value = "LLM response"
                    dl._trace_recorder = MagicMock()
                    dl._memory_retriever = MagicMock()
                    dl._persona_injector = MagicMock()
                    dl._persona_injector.build_system_prompt.return_value = "System prompt"
                    dl._old_memory = MagicMock()
                    dl._old_memory.get_context.return_value = []
                    dl._current_mode = MagicMock()

                    result = dl._call_llm("Hello", "Body status")

                    assert result == "LLM response"
                    dl._llm.chat.assert_called_once()

    def test_call_llm_with_response_prefix(self):
        """测试带响应前缀的LLM调用"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                mock_profile = MagicMock()
                mock_profile.response_prefix = "【云枢】"
                mock_behavior.return_value.profile = mock_profile
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    
                    dl._lifetrace_initialized = True
                    dl._persona_initialized = True
                    dl._memory_initialized = True
                    dl._llm = MagicMock()
                    dl._llm.chat.return_value = "LLM response"
                    dl._trace_recorder = MagicMock()
                    dl._memory_retriever = MagicMock()
                    dl._persona_injector = MagicMock()
                    dl._persona_injector.build_system_prompt.return_value = "System prompt"
                    dl._old_memory = MagicMock()
                    dl._old_memory.get_context.return_value = []
                    dl._current_mode = MagicMock()

                    result = dl._call_llm("Hello", "Body status")

                    assert "【云枢】" in result

    def test_call_llm_service_error(self):
        """测试LLM服务错误"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    
                    dl._lifetrace_initialized = True
                    dl._persona_initialized = True
                    dl._memory_initialized = True
                    dl._llm = MagicMock()
                    # 使用正确的异常类型
                    from memory.llm_service import LLMServiceError
                    dl._llm.chat.side_effect = LLMServiceError("API error")
                    dl._trace_recorder = MagicMock()
                    dl._memory_retriever = MagicMock()
                    dl._persona_injector = MagicMock()
                    dl._persona_injector.build_system_prompt.return_value = "System prompt"
                    dl._old_memory = MagicMock()
                    dl._current_mode = MagicMock()

                    result = dl._call_llm("Hello", "Body status")

                    assert "LLM 调用失败" in result

    def test_call_llm_no_llm(self):
        """测试无LLM时的离线响应"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    
                    dl._llm = None
                    dl._build_offline_response = MagicMock(return_value="离线响应")

                    result = dl._call_llm("Hello", "Body status")

                    assert result == "离线响应"


class TestDigitalLifeV2BuildBodyStatus:
    """测试 _build_body_status 方法"""

    def test_build_body_status_empty(self):
        """测试空读数"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                mock_profile = MagicMock()
                mock_profile.label = "正常"
                mock_profile.description = "一切正常"
                mock_behavior.return_value.profile = mock_profile
                mock_behavior.return_value._reasons = []
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()

                    result = dl._build_body_status([])

                    assert "我感觉很好" in result

    def test_build_body_status_with_readings(self):
        """测试带传感器读数"""
        mock_reading = MagicMock()
        mock_reading.to_dict.return_value = {"sensor_name": "test", "value": 100}
        
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                mock_profile = MagicMock()
                mock_profile.label = "正常"
                mock_profile.description = "一切正常"
                mock_behavior.return_value.profile = mock_profile
                mock_behavior.return_value._reasons = []
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    dl._injector_initialized = True
                    dl._old_injector = MagicMock()
                    dl._old_injector.inject.return_value = "传感器数据"

                    result = dl._build_body_status([mock_reading])

                    assert "传感器数据" in result
                    assert "当前行为模式" in result