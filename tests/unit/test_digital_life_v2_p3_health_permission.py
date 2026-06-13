"""DigitalLifeV2 P3.1 健康检查和权限逻辑测试"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from agent.digital_life_v2_p3 import DigitalLifeV2


class TestDigitalLifeV2P3HealthCheck:
    """测试健康检查功能"""

    def test_check_health_basic(self):
        """测试基本健康检查"""
        mock_reading = MagicMock()
        mock_reading.sensor_name = "test_sensor"
        mock_reading.value = 100
        mock_reading.unit = "%"
        mock_reading.severity = "normal"
        
        with patch('agent.digital_life_v2_p3.BodySensor') as mock_sensor:
            mock_sensor.return_value.collect_quick.return_value = [mock_reading]
            with patch('agent.digital_life_v2_p3.TraceRecorder') as mock_trace:
                with patch('agent.digital_life_v2_p3.MemoryRetriever'):
                    with patch('agent.digital_life_v2_p3.PersonaModel') as mock_persona_model:
                        mock_persona_model.return_value.persona = {"persona_id": "test"}
                        with patch('agent.digital_life_v2_p3.PersonaInjector'):
                            with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                                with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                                    with patch('agent.digital_life_v2_p3.MemoryManager') as mock_memory:
                                        mock_memory.return_value._llm_service = MagicMock()
                                        mock_memory.return_value._llm_service.provider = "test"
                                        mock_memory.return_value._llm_service.model = "test"
                                        with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                            with patch('agent.digital_life_v2_p3.BehaviorController') as mock_behavior:
                                                mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                                                with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                                    with patch('agent.digital_life_v2_p3.InitPerformanceTracker'):
                                                        dl = DigitalLifeV2()
                                                        dl._interaction_count = 1

                                                        readings = dl.check_health()

                                                        assert len(readings) == 1
                                                        mock_sensor.return_value.collect_quick.assert_called_once()
                                                        mock_trace.return_value.record_sensor.assert_called_once()

    def test_check_health_empty_readings(self):
        """测试空传感器读数"""
        with patch('agent.digital_life_v2_p3.BodySensor') as mock_sensor:
            mock_sensor.return_value.collect_quick.return_value = []
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.MemoryRetriever'):
                    with patch('agent.digital_life_v2_p3.PersonaModel') as mock_persona_model:
                        mock_persona_model.return_value.persona = {"persona_id": "test"}
                        with patch('agent.digital_life_v2_p3.PersonaInjector'):
                            with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                                with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                                    with patch('agent.digital_life_v2_p3.MemoryManager') as mock_memory:
                                        mock_memory.return_value._llm_service = MagicMock()
                                        mock_memory.return_value._llm_service.provider = "test"
                                        mock_memory.return_value._llm_service.model = "test"
                                        with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                            with patch('agent.digital_life_v2_p3.BehaviorController'):
                                                with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                                    with patch('agent.digital_life_v2_p3.InitPerformanceTracker'):
                                                        dl = DigitalLifeV2()
                                                        dl._interaction_count = 1

                                                        readings = dl.check_health()

                                                        assert readings is not None

    def test_check_health_warning_status(self):
        """测试健康检查警告状态"""
        mock_reading = MagicMock()
        mock_reading.sensor_name = "cpu"
        mock_reading.value = 95
        mock_reading.unit = "%"
        mock_reading.severity = "warning"
        
        with patch('agent.digital_life_v2_p3.BodySensor') as mock_sensor:
            mock_sensor.return_value.collect_quick.return_value = [mock_reading]
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.MemoryRetriever'):
                    with patch('agent.digital_life_v2_p3.PersonaModel') as mock_persona_model:
                        mock_persona_model.return_value.persona = {"persona_id": "test"}
                        with patch('agent.digital_life_v2_p3.PersonaInjector'):
                            with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                                with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                                    with patch('agent.digital_life_v2_p3.MemoryManager') as mock_memory:
                                        mock_memory.return_value._llm_service = MagicMock()
                                        mock_memory.return_value._llm_service.provider = "test"
                                        mock_memory.return_value._llm_service.model = "test"
                                        with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                            with patch('agent.digital_life_v2_p3.BehaviorController') as mock_behavior:
                                                mock_behavior.return_value.evaluate.return_value = MagicMock(value="WARNING")
                                                with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                                    with patch('agent.digital_life_v2_p3.InitPerformanceTracker'):
                                                        dl = DigitalLifeV2()
                                                        dl._interaction_count = 1

                                                        readings = dl.check_health()

                                                        assert len(readings) == 1
                                                        assert dl._current_mode.value == "WARNING"


class TestDigitalLifeV2P3Permission:
    """测试权限系统"""

    def test_permission_allowed(self):
        """测试权限允许"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.MemoryRetriever'):
                    with patch('agent.digital_life_v2_p3.PersonaModel') as mock_persona_model:
                        mock_persona_model.return_value.persona = {"persona_id": "test"}
                        with patch('agent.digital_life_v2_p3.PersonaInjector'):
                            with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                                with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                                    with patch('agent.digital_life_v2_p3.MemoryManager') as mock_memory:
                                        mock_memory.return_value._llm_service = MagicMock()
                                        mock_memory.return_value._llm_service.provider = "test"
                                        mock_memory.return_value._llm_service.model = "test"
                                        with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                            with patch('agent.digital_life_v2_p3.BehaviorController'):
                                                with patch('agent.digital_life_v2_p3.PermissionSystem') as mock_permission:
                                                    mock_permission.return_value.check_action.return_value = MagicMock(allowed=True, reason="允许")
                                                    with patch('agent.digital_life_v2_p3.InitPerformanceTracker'):
                                                        dl = DigitalLifeV2()

                                                        result = dl.request_permission("安全操作", "上下文")

                                                        assert result.allowed is True
                                                        assert result.reason == "允许"

    def test_permission_denied(self):
        """测试权限拒绝"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.MemoryRetriever'):
                    with patch('agent.digital_life_v2_p3.PersonaModel') as mock_persona_model:
                        mock_persona_model.return_value.persona = {"persona_id": "test"}
                        with patch('agent.digital_life_v2_p3.PersonaInjector'):
                            with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                                with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                                    with patch('agent.digital_life_v2_p3.MemoryManager') as mock_memory:
                                        mock_memory.return_value._llm_service = MagicMock()
                                        mock_memory.return_value._llm_service.provider = "test"
                                        mock_memory.return_value._llm_service.model = "test"
                                        with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                            with patch('agent.digital_life_v2_p3.BehaviorController'):
                                                with patch('agent.digital_life_v2_p3.PermissionSystem') as mock_permission:
                                                    mock_permission.return_value.check_action.return_value = MagicMock(allowed=False, reason="危险操作")
                                                    with patch('agent.digital_life_v2_p3.InitPerformanceTracker'):
                                                        dl = DigitalLifeV2()

                                                        result = dl.request_permission("危险操作", "上下文")

                                                        assert result.allowed is False
                                                        assert result.reason == "危险操作"

    def test_permission_with_context(self):
        """测试带上下文的权限检查"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.MemoryRetriever'):
                    with patch('agent.digital_life_v2_p3.PersonaModel') as mock_persona_model:
                        mock_persona_model.return_value.persona = {"persona_id": "test"}
                        with patch('agent.digital_life_v2_p3.PersonaInjector'):
                            with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                                with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                                    with patch('agent.digital_life_v2_p3.MemoryManager') as mock_memory:
                                        mock_memory.return_value._llm_service = MagicMock()
                                        mock_memory.return_value._llm_service.provider = "test"
                                        mock_memory.return_value._llm_service.model = "test"
                                        with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                            with patch('agent.digital_life_v2_p3.BehaviorController'):
                                                with patch('agent.digital_life_v2_p3.PermissionSystem') as mock_permission:
                                                    mock_permission.return_value.check_action.return_value = MagicMock(allowed=True)
                                                    with patch('agent.digital_life_v2_p3.InitPerformanceTracker'):
                                                        dl = DigitalLifeV2()

                                                        dl.request_permission("操作", "详细上下文信息")

                                                        mock_permission.return_value.check_action.assert_called_once_with("操作", "详细上下文信息")


class TestDigitalLifeV2P3Chat:
    """测试聊天功能"""

    def test_chat_not_running(self):
        """测试未运行时的聊天"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.MemoryRetriever'):
                    with patch('agent.digital_life_v2_p3.PersonaModel') as mock_persona_model:
                        mock_persona_model.return_value.persona = {"persona_id": "test"}
                        with patch('agent.digital_life_v2_p3.PersonaInjector'):
                            with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                                with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                                    with patch('agent.digital_life_v2_p3.MemoryManager') as mock_memory:
                                        mock_memory.return_value._llm_service = MagicMock()
                                        mock_memory.return_value._llm_service.provider = "test"
                                        mock_memory.return_value._llm_service.model = "test"
                                        with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                            with patch('agent.digital_life_v2_p3.BehaviorController'):
                                                with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                                    with patch('agent.digital_life_v2_p3.InitPerformanceTracker'):
                                                        dl = DigitalLifeV2()

                                                        result = dl.chat("Hello")

                                                        assert "还没有被唤醒" in result or "start()" in result

    def test_chat_normal_flow(self):
        """测试正常聊天流程"""
        with patch('agent.digital_life_v2_p3.BodySensor') as mock_sensor:
            mock_sensor.return_value.collect_quick.return_value = []
            with patch('agent.digital_life_v2_p3.TraceRecorder') as mock_trace:
                with patch('agent.digital_life_v2_p3.MemoryRetriever'):
                    with patch('agent.digital_life_v2_p3.PersonaModel') as mock_persona_model:
                        mock_persona_model.return_value.persona = {"persona_id": "test"}
                        with patch('agent.digital_life_v2_p3.PersonaInjector'):
                            with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                                with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                                    with patch('agent.digital_life_v2_p3.MemoryManager') as mock_memory:
                                        mock_memory.return_value._llm_service = MagicMock()
                                        mock_memory.return_value._llm_service.provider = "test"
                                        mock_memory.return_value._llm_service.model = "test"
                                        with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                            with patch('agent.digital_life_v2_p3.BehaviorController') as mock_behavior:
                                                mock_behavior.return_value.can_execute.return_value = (True, None)
                                                with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                                    with patch('agent.digital_life_v2_p3.InitPerformanceTracker'):
                                                        dl = DigitalLifeV2()
                                                        dl._running = True
                                                        dl._interaction_count = 0
                                                        
                                                        dl._process_user_input = MagicMock(return_value="Response")

                                                        result = dl.chat("Hello")

                                                        assert result == "Response"
                                                        assert dl._interaction_count == 1