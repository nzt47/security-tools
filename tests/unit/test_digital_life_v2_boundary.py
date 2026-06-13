"""DigitalLifeV2 边界条件测试"""
import pytest
from unittest.mock import MagicMock, patch

from agent.digital_life_v2 import DigitalLifeV2


class TestBoundaryConditions:
    """测试边界条件场景"""

    def test_empty_string_input(self):
        """测试空字符串输入"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    mock_behavior.return_value.can_execute.return_value = (True, "")
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常",
                        description="正常",
                        response_prefix="",
                        enable_reflection=False
                    )
                    
                    dl = DigitalLifeV2()
                    dl._llm = None
                    dl.start()
                    
                    result = dl.chat("")
                    
                    assert result is not None
                    assert len(result) > 0

    def test_whitespace_only_input(self):
        """测试纯空白输入"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    mock_behavior.return_value.can_execute.return_value = (True, "")
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常",
                        description="正常",
                        response_prefix="",
                        enable_reflection=False
                    )
                    
                    dl = DigitalLifeV2()
                    dl._llm = None
                    dl.start()
                    
                    result = dl.chat("   \t\n  ")
                    
                    assert result is not None

    def test_extremely_long_input(self):
        """测试超长输入"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    mock_behavior.return_value.can_execute.return_value = (True, "")
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常",
                        description="正常",
                        response_prefix="",
                        enable_reflection=False
                    )
                    
                    dl = DigitalLifeV2()
                    dl._llm = None
                    dl.start()
                    
                    long_input = "A" * 50000
                    result = dl.chat(long_input)
                    
                    assert result is not None

    def test_null_byte_input(self):
        """测试包含 NULL 字节的输入"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    mock_behavior.return_value.can_execute.return_value = (True, "")
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常",
                        description="正常",
                        response_prefix="",
                        enable_reflection=False
                    )
                    
                    dl = DigitalLifeV2()
                    dl._llm = None
                    dl.start()
                    
                    null_input = "Hello\x00World"
                    result = dl.chat(null_input)
                    
                    assert result is not None

    def test_mixed_language_input(self):
        """测试混合语言输入"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    mock_behavior.return_value.can_execute.return_value = (True, "")
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常",
                        description="正常",
                        response_prefix="",
                        enable_reflection=False
                    )
                    
                    dl = DigitalLifeV2()
                    dl._llm = None
                    dl.start()
                    
                    mixed_input = "Hello 世界 こんにちは 你好"
                    result = dl.chat(mixed_input)
                    
                    assert result is not None

    def test_emoji_input(self):
        """测试 emoji 输入"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    mock_behavior.return_value.can_execute.return_value = (True, "")
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常",
                        description="正常",
                        response_prefix="",
                        enable_reflection=False
                    )
                    
                    dl = DigitalLifeV2()
                    dl._llm = None
                    dl.start()
                    
                    emoji_input = "Hello 🎉🌍❤️"
                    result = dl.chat(emoji_input)
                    
                    assert result is not None

    def test_special_characters_input(self):
        """测试特殊字符输入"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    mock_behavior.return_value.can_execute.return_value = (True, "")
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常",
                        description="正常",
                        response_prefix="",
                        enable_reflection=False
                    )
                    
                    dl = DigitalLifeV2()
                    dl._llm = None
                    dl.start()
                    
                    special_input = "!@#$%^&*()_+-=[]{}|;':\",./<>?`~"
                    result = dl.chat(special_input)
                    
                    assert result is not None

    def test_unicode_control_characters(self):
        """测试 Unicode 控制字符"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    mock_behavior.return_value.can_execute.return_value = (True, "")
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常",
                        description="正常",
                        response_prefix="",
                        enable_reflection=False
                    )
                    
                    dl = DigitalLifeV2()
                    dl._llm = None
                    dl.start()
                    
                    control_input = "Hello\u0000\u0001\u0002World"
                    result = dl.chat(control_input)
                    
                    assert result is not None


class TestEdgeCases:
    """测试边缘场景"""

    def test_health_check_interval_boundary(self):
        """测试健康检查间隔边界"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    
                    dl = DigitalLifeV2(config={"behavior": {"check_interval": 0}})
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = MagicMock()
                    dl.start()
                    
                    dl.check_health()
                    dl.check_health()
                    
                    assert dl._last_health_check > 0

    def test_health_check_interval_negative(self):
        """测试健康检查间隔为负数"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    
                    dl = DigitalLifeV2(config={"behavior": {"check_interval": -1}})
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = MagicMock()
                    dl.start()
                    
                    dl.check_health()
                    
                    assert dl._last_health_check > 0

    def test_max_interaction_count(self):
        """测试最大交互计数"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    mock_behavior.return_value.can_execute.return_value = (True, "")
                    mock_behavior.return_value.profile = MagicMock(
                        label="正常",
                        description="正常",
                        response_prefix="",
                        enable_reflection=False
                    )
                    
                    dl = DigitalLifeV2()
                    dl._llm = None
                    dl._interaction_count = 2**31 - 1
                    dl.start()
                    
                    dl.chat("Hello")
                    
                    assert dl._interaction_count == 2**31

    def test_start_stop_start(self):
        """测试启动-停止-启动循环"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = MagicMock()
                    
                    dl.start()
                    assert dl._running is True
                    
                    dl.stop()
                    assert dl._running is False
                    
                    dl.start()
                    assert dl._running is True

    def test_multiple_stop_calls(self):
        """测试多次停止调用"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    dl._running = False
                    
                    dl.stop()
                    dl.stop()

    def test_check_health_not_running(self):
        """测试未运行时调用健康检查"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    
                    dl = DigitalLifeV2()
                    dl._running = False
                    
                    readings = dl.check_health()
                    
                    assert readings is not None

    def test_chat_after_stop(self):
        """测试停止后调用聊天"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    dl.start()
                    dl.stop()
                    
                    result = dl.chat("Hello")
                    
                    assert "启动" in result or "start" in result.lower()

    def test_register_builtin_tools(self):
        """测试工具注册方法"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    dl._register_builtin_tools()
                    
                    from agent import tools
                    tools_list = tools.list_tools()
                    assert len(tools_list) > 0

    def test_register_builtin_tools_multiple_calls(self):
        """测试多次调用工具注册"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    dl._register_builtin_tools()
                    dl._register_builtin_tools()
                    
                    from agent import tools
                    tools_list = tools.list_tools()
                    assert len(tools_list) > 0
