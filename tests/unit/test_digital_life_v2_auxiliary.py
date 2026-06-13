"""DigitalLifeV2 辅助方法与 Chat 流程单元测试"""
import pytest
from unittest.mock import MagicMock, patch

from agent.digital_life_v2 import DigitalLifeV2


class TestBuildBodyStatus:
    """测试 _build_body_status 方法"""

    def test_build_body_status_empty_readings(self):
        """测试空读数时的身体状态构建"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    dl._behavior = MagicMock()
                    dl._behavior.profile = MagicMock(label="正常", description="测试")
                    dl._behavior._reasons = []
                    
                    result = dl._build_body_status([])
                    
                    assert "我感觉很好" in result or "正常" in result

    def test_build_body_status_with_readings(self):
        """测试带传感器读数的身体状态构建"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_reading = MagicMock()
                    mock_reading.sensor_name = "heart_rate"
                    mock_reading.value = 72
                    mock_reading.unit = "bpm"
                    mock_reading.severity = "normal"
                    mock_reading.to_dict.return_value = {"sensor": "heart_rate", "value": 72}
                    
                    mock_profile = MagicMock()
                    mock_profile.label = "正常模式"
                    mock_profile.description = "身体状态良好"
                    
                    dl = DigitalLifeV2()
                    dl._behavior = MagicMock()
                    dl._behavior.profile = mock_profile
                    dl._behavior._reasons = []
                    
                    mock_old_injector = MagicMock()
                    mock_old_injector.inject.return_value = "心跳 72bpm，状态良好。"
                    dl._old_injector = mock_old_injector
                    dl._injector_initialized = True  # 避免再次初始化
                    
                    result = dl._build_body_status([mock_reading])
                    
                    # 验证行为模式信息包含在结果中
                    assert "正常模式" in result

    def test_build_body_status_with_reasons(self):
        """测试带触发原因的身体状态构建"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_reading = MagicMock()
                    mock_reading.sensor_name = "energy"
                    mock_reading.value = 20
                    mock_reading.unit = "%"
                    mock_reading.severity = "warning"
                    mock_reading.to_dict.return_value = {"sensor": "energy", "value": 20}
                    
                    mock_profile = MagicMock()
                    mock_profile.label = "节能模式"
                    mock_profile.description = "电量低"
                    
                    dl = DigitalLifeV2()
                    dl._behavior = MagicMock()
                    dl._behavior.profile = mock_profile
                    dl._behavior._reasons = ["能量不足", "需要休息"]
                    
                    mock_old_injector = MagicMock()
                    mock_old_injector.inject.return_value = "能量 20%，偏低。"
                    dl._old_injector = mock_old_injector
                    
                    result = dl._build_body_status([mock_reading])
                    
                    assert "节能模式" in result
                    assert "能量不足" in result or "触发原因" in result

    def test_build_body_status_no_injector(self):
        """测试无 PromptInjector 时的身体状态构建"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_reading = MagicMock()
                    mock_reading.sensor_name = "test"
                    mock_reading.value = 100
                    mock_reading.unit = "%"
                    mock_reading.severity = "normal"
                    mock_reading.to_dict.return_value = {"sensor": "test", "value": 100}
                    
                    mock_profile = MagicMock()
                    mock_profile.label = "正常"
                    mock_profile.description = "正常"
                    
                    dl = DigitalLifeV2()
                    dl._behavior = MagicMock()
                    dl._behavior.profile = mock_profile
                    dl._behavior._reasons = []
                    dl._old_injector = None
                    
                    result = dl._build_body_status([mock_reading])
                    
                    assert "正常" in result


class TestBuildRejectResponse:
    """测试 _build_reject_response 方法"""

    def test_build_reject_response_basic(self):
        """测试基本的拒绝响应构建"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_profile = MagicMock()
                    mock_profile.suggestion = "请稍后再试"
                    
                    dl = DigitalLifeV2()
                    dl._behavior = MagicMock()
                    dl._behavior.profile = mock_profile
                    
                    result = dl._build_reject_response("能量不足", [])
                    
                    assert "抱歉" in result
                    assert "能量不足" in result
                    assert "请稍后再试" in result

    def test_build_reject_response_with_readings(self):
        """测试带传感器读数的拒绝响应"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_reading = MagicMock()
                    mock_reading.severity = "warning"
                    mock_reading.description = "能量"
                    mock_reading.value = 20
                    mock_reading.unit = "%"
                    
                    mock_reading_normal = MagicMock()
                    mock_reading_normal.severity = "normal"
                    mock_reading_normal.description = "心跳"
                    mock_reading_normal.value = 72
                    mock_reading_normal.unit = "bpm"
                    
                    mock_profile = MagicMock()
                    mock_profile.suggestion = "请休息一下"
                    
                    dl = DigitalLifeV2()
                    dl._behavior = MagicMock()
                    dl._behavior.profile = mock_profile
                    
                    result = dl._build_reject_response("状态不佳", [mock_reading, mock_reading_normal])
                    
                    assert "抱歉" in result
                    assert "能量" in result
                    assert "warning" in result
                    assert "心跳" not in result  # 正常的读数不应该被包含

    def test_build_reject_response_critical_reading(self):
        """测试严重警告级别的拒绝响应"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_reading = MagicMock()
                    mock_reading.severity = "critical"
                    mock_reading.description = "系统温度"
                    mock_reading.value = 95
                    mock_reading.unit = "°C"
                    
                    mock_profile = MagicMock()
                    mock_profile.suggestion = "请立即停止并检查系统"
                    
                    dl = DigitalLifeV2()
                    dl._behavior = MagicMock()
                    dl._behavior.profile = mock_profile
                    
                    result = dl._build_reject_response("系统过热", [mock_reading])
                    
                    assert "抱歉" in result
                    assert "critical" in result
                    assert "系统温度" in result
                    assert "95" in result

    def test_build_reject_response_no_suggestion(self):
        """测试无建议时的拒绝响应"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_profile = MagicMock()
                    mock_profile.suggestion = None
                    
                    dl = DigitalLifeV2()
                    dl._behavior = MagicMock()
                    dl._behavior.profile = mock_profile
                    
                    result = dl._build_reject_response("测试原因", [])
                    
                    assert "抱歉" in result
                    assert "测试原因" in result


class TestChatMethod:
    """测试 chat 方法的完整流程"""

    def test_chat_basic_flow(self):
        """测试基本聊天流程"""
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
                    dl._llm = None  # 走离线响应路径
                    dl.start()
                    
                    result = dl.chat("Hello")
                    
                    assert result is not None
                    assert len(result) > 0

    def test_chat_not_running(self):
        """测试未运行时的聊天提示"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    dl._running = False
                    
                    result = dl.chat("Hello")
                    
                    assert "启动" in result or "start" in result.lower()

    def test_chat_increments_interaction_count(self):
        """测试聊天会增加交互计数"""
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
                    initial_count = dl._interaction_count
                    dl.start()
                    
                    dl.chat("Hello")
                    
                    assert dl._interaction_count == initial_count + 1

    def test_chat_process_user_input_called(self):
        """测试聊天会调用 _process_user_input"""
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
                    
                    original_process = dl._process_user_input
                    dl._process_user_input = MagicMock(return_value="测试响应")
                    
                    result = dl.chat("Hello")
                    
                    dl._process_user_input.assert_called_once_with("Hello")
                    assert result == "测试响应"
                    dl._process_user_input = original_process


class TestChatMethodExceptions:
    """测试 chat 方法的异常场景"""

    def test_chat_with_llm_error_in_process(self):
        """测试 _process_user_input 中 LLM 错误 - 验证异常传播"""
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
                    dl._behavior = mock_behavior.return_value
                    dl.start()
                    
                    # 模拟 _process_user_input 抛出异常
                    original_process = dl._process_user_input
                    dl._process_user_input = MagicMock(side_effect=Exception("处理错误"))
                    
                    # chat 方法会直接传播异常
                    with pytest.raises(Exception, match="处理错误"):
                        dl.chat("Hello")
                    
                    dl._process_user_input = original_process

    def test_chat_empty_input(self):
        """测试空输入"""
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

    def test_chat_long_input(self):
        """测试长输入"""
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
                    
                    long_input = "A" * 10000
                    result = dl.chat(long_input)
                    
                    assert result is not None

    def test_chat_special_characters(self):
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
                    
                    special_input = "!@#$%^&*()_+-=[]{}|;':\",./<>?`~\n\t\r"
                    result = dl.chat(special_input)
                    
                    assert result is not None

    def test_chat_unicode_input(self):
        """测试 Unicode 输入"""
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
                    
                    unicode_input = "你好世界！🎉🌍 你叫什么名字？αβγδ"
                    result = dl.chat(unicode_input)
                    
                    assert result is not None

    def test_chat_multiple_rapid_calls(self):
        """测试多次快速调用"""
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
                    
                    for i in range(5):
                        result = dl.chat(f"Message {i}")
                        assert result is not None
                    
                    assert dl._interaction_count >= 5


class TestCheckHealth:
    """测试 check_health 方法"""

    def test_check_health_basic(self):
        """测试基本健康检查"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_reading = MagicMock()
                    mock_reading.sensor_name = "test"
                    mock_reading.value = 100
                    mock_reading.unit = "%"
                    mock_reading.severity = "normal"
                    
                    mock_sensor.return_value.collect_quick.return_value = [mock_reading]
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    
                    dl = DigitalLifeV2()
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = MagicMock()
                    dl.start()
                    
                    readings = dl.check_health()
                    
                    assert len(readings) == 1
                    assert dl._current_mode.value == "NORMAL"

    def test_check_health_sets_timestamp(self):
        """测试健康检查设置时间戳"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController') as mock_behavior:
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_sensor.return_value.collect_quick.return_value = []
                    mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                    
                    dl = DigitalLifeV2()
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = MagicMock()
                    dl._last_health_check = 0
                    dl.start()
                    
                    dl.check_health()
                    
                    assert dl._last_health_check > 0


class TestSelfReflect:
    """测试 self_reflect 方法"""

    def test_self_reflect_with_llm(self):
        """测试带 LLM 的自我反思"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    mock_llm = MagicMock()
                    mock_llm.chat.return_value = "反思内容"
                    
                    dl = DigitalLifeV2()
                    dl._llm = mock_llm
                    dl._memory_initialized = True
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = MagicMock()
                    dl._current_mode = MagicMock(value="NORMAL")
                    dl.start()
                    
                    result = dl.self_reflect("任务", "响应")
                    
                    assert "reflection" in result
                    mock_llm.chat.assert_called_once()

    def test_self_reflect_without_llm(self):
        """测试无 LLM 时的自我反思"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    dl._llm = None
                    dl._memory_initialized = True
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = MagicMock()
                    dl._current_mode = MagicMock(value="NORMAL")
                    dl.start()
                    
                    result = dl.self_reflect("任务", "响应")
                    
                    assert "reflection" in result
                    assert "未接入 LLM" in result["reflection"]

    def test_self_reflect_records_history(self):
        """测试反思记录到历史"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()
                    dl._llm = None
                    dl._memory_initialized = True
                    dl._lifetrace_initialized = True
                    dl._trace_recorder = MagicMock()
                    dl._current_mode = MagicMock(value="NORMAL")
                    initial_history_len = len(dl._reflection_history)
                    dl.start()
                    
                    dl.self_reflect("任务", "响应")
                    
                    assert len(dl._reflection_history) == initial_history_len + 1
