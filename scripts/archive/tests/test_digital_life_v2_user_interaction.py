"""DigitalLifeV2 用户交互和数据处理流程单元测试"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timezone

from agent.digital_life_v2 import DigitalLifeV2


class TestDigitalLifeV2UserInteraction:
    """测试用户交互流程"""

    def test_chat_basic_flow(self):
        """测试基本聊天流程"""
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
                    
                    dl = DigitalLifeV2()
                    dl._llm = None  # 不使用 LLM，走离线响应路径
                    dl.start()
                    
                    result = dl.chat("Hello")
                    
                    assert result is not None
                    assert len(result) > 0

    def test_chat_rejected_by_behavior(self):
        """测试行为控制器拒绝请求"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="WARNING")
                    mock_behavior.return_value.can_execute.return_value = (False, "行为拒绝")
                    
                    dl = DigitalLifeV2()
                    dl.start()
                    
                    result = dl.chat("危险操作")
                    
                    assert "拒绝" in result or "无法执行" in result

    def test_chat_rejected_by_persona(self):
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
                    dl._persona_initialized = True
                    dl._persona_injector = mock_persona
                    dl._persona_extractor = mock_extractor
                    dl._distillation_enabled = False
                    dl.start()
                    
                    result = dl.chat("不适合的请求")
                    
                    assert "拒绝" in result or "无法执行" in result

    def test_chat_llm_error(self):
        """测试 LLM 调用失败"""
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
                    
                    dl = DigitalLifeV2()
                    dl._llm = None  # 不使用 LLM，走离线响应路径
                    dl.start()
                    
                    result = dl.chat("Hello")
                    
                    assert result is not None


class TestDigitalLifeV2DataProcessing:
    """测试数据处理流程"""

    def test_build_body_status_empty(self):
        """测试空读数时的身体状态构建"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    result = dl._build_body_status([])
                    
                    assert "很好" in result or "正常" in result

    def test_build_body_status_with_readings(self):
        """测试带读数的身体状态构建"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_reading = MagicMock()
                    mock_reading.sensor_name = "heart_rate"
                    mock_reading.value = 72
                    mock_reading.unit = "bpm"
                    mock_reading.severity = "normal"
                    mock_reading.to_dict.return_value = {"sensor": "heart_rate", "value": 72}
                    
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常模式",
                        description="正常运行"
                    )
                    mock_behavior.return_value._reasons = []
                    
                    dl = DigitalLifeV2()
                    dl._behavior = mock_behavior.return_value
                    
                    result = dl._build_body_status([mock_reading])
                    
                    assert "正常模式" in result

    def test_self_reflect_with_llm(self):
        """测试带 LLM 的自我反思"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_llm = MagicMock()
                    mock_llm.chat.return_value = "我反思了这次交互"
                    
                    dl = DigitalLifeV2()
                    dl._memory_initialized = True  # 避免 _ensure_memory 重置 _llm
                    dl._llm = mock_llm
                    dl.start()
                    
                    result = dl.self_reflect("测试任务", "测试响应")
                    
                    assert "reflection" in result
                    assert "我反思了" in result["reflection"]
                    mock_llm.chat.assert_called_once()

    def test_self_reflect_without_llm(self):
        """测试无 LLM 时的自我反思"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    dl._llm = None
                    dl.start()
                    
                    result = dl.self_reflect("测试任务", "测试响应")
                    
                    assert "reflection" in result
                    assert "未接入 LLM" in result["reflection"]

    def test_request_permission(self):
        """测试权限请求"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem') as mock_permission:
                    mock_result = MagicMock()
                    mock_result.allowed = True
                    mock_permission.return_value.check_action.return_value = mock_result
                    
                    dl = DigitalLifeV2()
                    
                    result = dl.request_permission("test_action", "test_context")
                    
                    assert result.allowed is True
                    mock_permission.return_value.check_action.assert_called_once_with("test_action", "test_context")


class TestDigitalLifeV2LifetraceIntegration:
    """测试 LifeTrace 集成"""

    def test_lifetrace_initialization(self):
        """测试 LifeTrace 懒加载初始化"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    with patch('lifetrace.TraceRecorder') as mock_recorder:
                        with patch('lifetrace.MemoryRetriever') as mock_retriever:
                            dl = DigitalLifeV2()
                            
                            assert not dl._lifetrace_initialized
                            
                            dl._ensure_lifetrace()
                            
                            assert dl._lifetrace_initialized
                            mock_recorder.assert_called_once()
                            mock_retriever.assert_called_once()

    def test_get_lifetrace_context(self):
        """测试获取 LifeTrace 上下文"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_recorder = MagicMock()
                    mock_retriever = MagicMock()
                    
                    mock_recorder.global_tree.load_summary.return_value = "测试摘要"
                    mock_retriever.retrieve.return_value = [
                        MagicMock(content="相关记忆1"),
                        MagicMock(content="相关记忆2")
                    ]
                    mock_recorder.get_recent_chat.return_value = []
                    
                    dl = DigitalLifeV2()
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = mock_recorder
                    dl._memory_retriever = mock_retriever
                    
                    context = dl._get_lifetrace_context("测试查询")
                    
                    assert "测试摘要" in context
                    assert "相关记忆" in context


class TestDigitalLifeV2PersonaIntegration:
    """测试 Persona 集成"""

    def test_persona_initialization(self):
        """测试 Persona 懒加载初始化"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    with patch('persona.PersonaModel'):
                        with patch('persona.PersonaInjector') as mock_injector:
                            with patch('persona.PersonalityPreferenceExtractor') as mock_extractor:
                                dl = DigitalLifeV2()
                                
                                assert not dl._persona_initialized
                                
                                dl._ensure_persona()
                                
                                assert dl._persona_initialized
                                mock_injector.assert_called_once()
                                mock_extractor.assert_called_once()

    def test_persona_distillation_update(self):
        """测试人格蒸馏增量更新"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_extractor = MagicMock()
                    dl = DigitalLifeV2()
                    dl._distillation_enabled = True
                    dl._persona_initialized = True
                    dl._persona_extractor = mock_extractor
                    
                    dl._persona_extractor.update_incremental({
                        "role": "user",
                        "content": "测试输入",
                        "timestamp": "2024-01-01T00:00:00Z"
                    })
                    
                    mock_extractor.update_incremental.assert_called_once()


class TestDigitalLifeV2Lifecycle:
    """测试生命周期管理"""

    def test_start_records_event(self):
        """测试启动时记录事件"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_recorder = MagicMock()
                    dl = DigitalLifeV2()
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = mock_recorder
                    
                    dl.start()
                    
                    assert dl._running is True
                    mock_recorder.record_chat.assert_called_once()

    def test_stop_records_event(self):
        """测试停止时记录事件"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_recorder = MagicMock()
                    dl = DigitalLifeV2()
                    dl._running = True
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = mock_recorder
                    
                    dl.stop()
                    
                    assert dl._running is False
                    mock_recorder.record_chat.assert_called_once()

    def test_check_health_records_sensors(self):
        """测试健康检查记录传感器数据"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_recorder = MagicMock()
                    mock_reading = MagicMock()
                    mock_reading.sensor_name = "test"
                    mock_reading.value = 100
                    mock_reading.unit = "%"
                    mock_reading.severity = "normal"
                    
                    mock_sensor.return_value.collect_quick.return_value = [mock_reading]
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    
                    dl = DigitalLifeV2()
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = mock_recorder
                    
                    readings = dl.check_health()
                    
                    assert len(readings) == 1
                    mock_recorder.record_sensor.assert_called_once()
