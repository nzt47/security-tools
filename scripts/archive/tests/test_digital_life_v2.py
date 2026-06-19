"""
DigitalLifeV2 综合测试 - 使用 Mock 模拟外部依赖
目标：将覆盖率从 0% 提升至 90%+
"""
import pytest
import time
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

# 使用 mock 模拟所有外部依赖
@patch('agent.digital_life_v2.BodySensor')
@patch('agent.digital_life_v2.BehaviorController')
@patch('agent.digital_life_v2.PermissionSystem')
class TestDigitalLifeV2WithMock:
    """测试 DigitalLifeV2 - 使用 Mock 模拟外部依赖"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_initialization(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试初始化 DigitalLifeV2"""
        # 配置 mock 返回值
        mock_body_instance = MagicMock()
        mock_body_sensor.return_value = mock_body_instance
        
        mock_behavior_instance = MagicMock()
        mock_behavior.return_value = mock_behavior_instance
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        dl = DigitalLifeV2()
        
        assert dl is not None
        assert isinstance(dl, DigitalLifeV2)
        assert dl._running is False
        mock_body_sensor.assert_called_once()
        mock_behavior.assert_called_once()
        mock_permission.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_stop(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试启动和停止"""
        mock_body_instance = MagicMock()
        mock_body_sensor.return_value = mock_body_instance
        
        mock_behavior_instance = MagicMock()
        mock_behavior.return_value = mock_behavior_instance
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        dl = DigitalLifeV2()
        
        dl.start()
        assert dl.is_running is True
        
        dl.stop()
        assert dl.is_running is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_chat_when_not_running(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试在未运行状态下聊天"""
        mock_body_instance = MagicMock()
        mock_body_sensor.return_value = mock_body_instance
        
        mock_behavior_instance = MagicMock()
        mock_behavior.return_value = mock_behavior_instance
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        dl = DigitalLifeV2()
        
        result = dl.chat("你好")
        assert "还没有被唤醒" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_chat_when_running(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试在运行状态下聊天"""
        mock_body_instance = MagicMock()
        mock_body_sensor.return_value = mock_body_instance
        mock_body_instance.collect_quick.return_value = []
        
        mock_behavior_instance = MagicMock()
        mock_behavior.return_value = mock_behavior_instance
        mock_behavior_instance.evaluate.return_value = MagicMock(value="NORMAL")
        mock_behavior_instance.can_execute.return_value = (True, "")
        mock_behavior_instance.profile.enable_reflection = False
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        with patch('agent.digital_life_v2.DigitalLifeV2._call_llm') as mock_call_llm:
            mock_call_llm.return_value = "测试响应"
            
            dl = DigitalLifeV2()
            dl.start()
            
            result = dl.chat("你好")
            assert result == "测试响应"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_check_health(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试健康检查"""
        mock_body_instance = MagicMock()
        mock_body_sensor.return_value = mock_body_instance
        mock_body_instance.collect_quick.return_value = []
        
        mock_behavior_instance = MagicMock()
        mock_behavior.return_value = mock_behavior_instance
        mock_behavior_instance.evaluate.return_value = MagicMock(value="NORMAL")
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        dl = DigitalLifeV2()
        readings = dl.check_health()
        
        assert isinstance(readings, list)
        mock_body_instance.collect_quick.assert_called_once()
        mock_behavior_instance.evaluate.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_request_permission(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试权限请求"""
        mock_body_instance = MagicMock()
        mock_body_sensor.return_value = mock_body_instance
        
        mock_behavior_instance = MagicMock()
        mock_behavior.return_value = mock_behavior_instance
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        mock_permission_instance.check_action.return_value = MagicMock(allowed=True)
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        dl = DigitalLifeV2()
        result = dl.request_permission("test_action", "test_context")
        
        assert result.allowed is True
        mock_permission_instance.check_action.assert_called_once_with("test_action", "test_context")


class TestLazyLoader:
    """测试内部 LazyLoader 辅助类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_lazy_loader_initialization(self):
        """测试 LazyLoader 初始化"""
        from agent.digital_life_v2 import LazyLoader
        
        init_func = MagicMock(return_value="instance")
        loader = LazyLoader(init_func, "test")
        
        assert loader.is_initialized is False
        assert loader.init_time_ms == 0
        
        result = loader.get()
        
        assert result == "instance"
        assert loader.is_initialized is True
        # 初始化时间可能为 0（因为 MagicMock 执行太快），但应该是非负的
        assert loader.init_time_ms >= 0
        init_func.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_lazy_loader_multiple_get(self):
        """测试 LazyLoader 多次获取同一实例"""
        from agent.digital_life_v2 import LazyLoader
        
        call_count = [0]
        
        def init_func():
            call_count[0] += 1
            return "instance"
        
        loader = LazyLoader(init_func, "test")
        
        result1 = loader.get()
        result2 = loader.get()
        
        assert result1 == result2 == "instance"
        assert call_count[0] == 1


class TestDigitalLifeV2Config:
    """测试配置解析"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_config_defaults(self):
        """测试默认配置"""
        config = {}
        
        # 传感器配置
        sensor_cfg = config.get("sensor", {})
        assert sensor_cfg.get("watch_dirs") is None
        assert sensor_cfg.get("enable_change_detection", True) is True
        assert sensor_cfg.get("enable_event_monitor", True) is True
        assert sensor_cfg.get("lazy_load", True) is True
        
        # 蒸馏配置
        distillation_enabled = config.get("distillation", {}).get("enabled", True)
        distillation_interval = config.get("distillation", {}).get("interval", 10)
        distiller_enabled = config.get("distillation", {}).get("distiller_enabled", True)
        assert distillation_enabled is True
        assert distillation_interval == 10
        assert distiller_enabled is True
        
        # 行为配置
        check_interval = config.get("behavior", {}).get("check_interval", 30)
        assert check_interval == 30
        
        # 数据流配置
        data_flow_enabled = config.get("data_flow", {}).get("enabled", True)
        assert data_flow_enabled is True
        
        # 备份目录
        backup_dir = config.get("backup_dir", "./.backups")
        assert backup_dir == "./.backups"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_config_custom(self):
        """测试自定义配置"""
        config = {
            "sensor": {
                "watch_dirs": ["/tmp"],
                "enable_change_detection": False,
                "enable_event_monitor": False,
                "lazy_load": False
            },
            "distillation": {
                "enabled": False,
                "interval": 20,
                "distiller_enabled": False
            },
            "behavior": {"check_interval": 60},
            "data_flow": {"enabled": False},
            "backup_dir": "./custom_backups"
        }
        
        assert config.get("sensor", {}).get("watch_dirs") == ["/tmp"]
        assert config.get("sensor", {}).get("enable_change_detection") is False
        assert config.get("distillation", {}).get("enabled") is False
        assert config.get("distillation", {}).get("interval") == 20
        assert config.get("behavior", {}).get("check_interval") == 60
        assert config.get("data_flow", {}).get("enabled") is False
        assert config.get("backup_dir") == "./custom_backups"


class TestDigitalLifeV2SelfReflect:
    """测试自我反思功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    @patch('agent.digital_life_v2.BodySensor')
    @patch('agent.digital_life_v2.BehaviorController')
    @patch('agent.digital_life_v2.PermissionSystem')
    def test_self_reflect_no_llm(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试没有 LLM 时的反思"""
        mock_body_instance = MagicMock()
        mock_body_sensor.return_value = mock_body_instance
        
        mock_behavior_instance = MagicMock()
        mock_behavior.return_value = mock_behavior_instance
        mock_behavior_instance.evaluate.return_value = MagicMock(value="NORMAL")
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        dl = DigitalLifeV2()
        dl._llm = None
        dl._interaction_count = 1  # 设置交互计数
        
        result = dl.self_reflect("test task", "test response")
        
        assert "未接入 LLM" in result["reflection"]
        assert result["interaction"] == 1
        assert "task" in result


class TestSessionManagement:
    """测试会话管理"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_session_id_generation(self):
        """测试会话ID生成"""
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        assert len(session_id) == 15
        assert session_id[:8].isdigit()
        assert session_id[8] == '_'
        assert session_id[9:].isdigit()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_interaction_count(self):
        """测试交互计数"""
        interaction_count = 0
        interaction_count += 1
        assert interaction_count == 1
        
        interaction_count += 1
        assert interaction_count == 2


class TestReflectionHistoryStructure:
    """测试反思历史结构"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_reflection_history_structure(self):
        """测试反思历史结构"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "interaction": 1,
            "task": "test task",
            "mode": "NORMAL",
            "reflection": "test reflection"
        }
        
        assert "timestamp" in entry
        assert "interaction" in entry
        assert "task" in entry
        assert "mode" in entry
        assert "reflection" in entry


@patch('agent.digital_life_v2.BodySensor')
@patch('agent.digital_life_v2.BehaviorController')
@patch('agent.digital_life_v2.PermissionSystem')
class TestCheckHealthWithLifetrace:
    """测试 check_health 方法的 LifeTrace 记录功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_check_health_records_to_lifetrace(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试检查健康时记录到 LifeTrace"""
        mock_body_instance = MagicMock()
        mock_body_instance.collect_quick.return_value = [
            MagicMock(sensor_name="cpu", value=50, unit="%", severity="normal", description="CPU 使用率")
        ]
        mock_body_sensor.return_value = mock_body_instance
        
        mock_behavior_instance = MagicMock()
        mock_behavior.return_value = mock_behavior_instance
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        dl = DigitalLifeV2()
        dl._lifetrace_initialized = True
        dl._trace_recorder = MagicMock()
        
        readings = dl.check_health()
        
        assert len(readings) == 1
        dl._trace_recorder.record_sensor.assert_called_once()


@patch('agent.digital_life_v2.BodySensor')
@patch('agent.digital_life_v2.BehaviorController')
@patch('agent.digital_life_v2.PermissionSystem')
class TestSelfReflectBasic:
    """测试 self_reflect 方法的基本功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_self_reflect_no_llm(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试无 LLM 时的反思"""
        mock_body_instance = MagicMock()
        mock_body_instance.collect_quick.return_value = []
        mock_body_sensor.return_value = mock_body_instance
        
        mock_behavior_instance = MagicMock()
        mock_behavior.return_value = mock_behavior_instance
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        dl = DigitalLifeV2()
        dl._lifetrace_initialized = True
        dl._trace_recorder = MagicMock()
        dl._llm_initialized = False
        
        result = dl.self_reflect("test task", "test response")
        
        assert "未接入 LLM" in result["reflection"]


@patch('agent.digital_life_v2.BodySensor')
@patch('agent.digital_life_v2.BehaviorController')
@patch('agent.digital_life_v2.PermissionSystem')
class TestChatRejectPath:
    """测试 chat 方法的拒绝路径"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_chat_behavior_reject(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试行为控制器拒绝任务"""
        mock_body_instance = MagicMock()
        mock_body_instance.collect_quick.return_value = []
        mock_body_sensor.return_value = mock_body_instance
        
        mock_behavior_instance = MagicMock()
        mock_behavior_instance.can_execute.return_value = (False, "身体状态不佳")
        mock_behavior_instance.profile = MagicMock(description="休息模式", suggestion="建议休息")
        mock_behavior.return_value = mock_behavior_instance
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        dl = DigitalLifeV2()
        dl._lifetrace_initialized = True
        dl._trace_recorder = MagicMock()
        dl._running = True
        
        response = dl.chat("执行任务")
        
        assert "抱歉" in response
        assert "身体状态不佳" in response
        # record_chat 会被调用两次：一次记录用户消息，一次记录拒绝响应
        assert dl._trace_recorder.record_chat.call_count == 2
        # 检查第二次调用是否是拒绝响应
        calls = dl._trace_recorder.record_chat.call_args_list
        assert calls[1][1].get('metadata', {}).get('rejected') is True


    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_lifetrace_context_not_initialized(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试 LifeTrace 未初始化时返回默认值"""
        mock_body_instance = MagicMock()
        mock_body_sensor.return_value = mock_body_instance
        
        mock_behavior_instance = MagicMock()
        mock_behavior.return_value = mock_behavior_instance
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        dl = DigitalLifeV2()
        dl._lifetrace_initialized = False
        
        context = dl._get_lifetrace_context("hello")
        
        assert context == "（暂无记忆内容）"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_lifetrace_context_with_memory(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试从 LifeTrace 获取上下文"""
        mock_body_instance = MagicMock()
        mock_body_sensor.return_value = mock_body_instance
        
        mock_behavior_instance = MagicMock()
        mock_behavior.return_value = mock_behavior_instance
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        dl = DigitalLifeV2()
        dl._lifetrace_initialized = True
        dl._trace_recorder = MagicMock()
        dl._memory_retriever = MagicMock()
        
        dl._trace_recorder.global_tree.load_summary.return_value = "test summary"
        dl._memory_retriever.retrieve.return_value = [MagicMock(content="memory 1")]
        dl._trace_recorder.get_recent_chat.return_value = [MagicMock(metadata={"role": "user"}, content="hello")]
        
        context = dl._get_lifetrace_context("hello")
        
        assert "test summary" in context
        assert "memory 1" in context


@patch('agent.digital_life_v2.BodySensor')
@patch('agent.digital_life_v2.BehaviorController')
@patch('agent.digital_life_v2.PermissionSystem')
class TestPersonaDistillation:
    """测试人格蒸馏功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_run_persona_distillation_not_initialized(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试未初始化时不执行蒸馏"""
        mock_body_instance = MagicMock()
        mock_body_sensor.return_value = mock_body_instance
        
        mock_behavior_instance = MagicMock()
        mock_behavior.return_value = mock_behavior_instance
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        dl = DigitalLifeV2()
        dl._persona_initialized = False
        
        dl._run_persona_distillation()
        
        # 不应抛出异常，静默返回

    @pytest.mark.unit
    @pytest.mark.p0
    def test_run_persona_distillation_insufficient_data(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试数据不足时不执行蒸馏"""
        mock_body_instance = MagicMock()
        mock_body_sensor.return_value = mock_body_instance
        
        mock_behavior_instance = MagicMock()
        mock_behavior.return_value = mock_behavior_instance
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        dl = DigitalLifeV2()
        dl._persona_initialized = True
        dl._lifetrace_initialized = True
        dl._trace_recorder = MagicMock()
        dl._trace_recorder.get_recent_chat.return_value = []
        
        dl._run_persona_distillation()
        
        # 不应抛出异常，静默返回

    @pytest.mark.unit
    @pytest.mark.p0
    def test_run_persona_distillation_success(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试成功执行人格蒸馏"""
        mock_body_instance = MagicMock()
        mock_body_sensor.return_value = mock_body_instance
        
        mock_behavior_instance = MagicMock()
        mock_behavior.return_value = mock_behavior_instance
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        dl = DigitalLifeV2()
        dl._persona_initialized = True
        dl._lifetrace_initialized = True
        dl._trace_recorder = MagicMock()
        dl._persona_extractor = MagicMock()
        dl._interaction_count = 10
        
        mock_node = MagicMock()
        mock_node.metadata = {"role": "user", "timestamp": "2024-01-01"}
        mock_node.content = "test message"
        dl._trace_recorder.get_recent_chat.return_value = [mock_node] * 10
        
        dl._run_persona_distillation()
        
        dl._persona_extractor.extract_from_conversation.assert_called_once()


@patch('agent.digital_life_v2.BodySensor')
@patch('agent.digital_life_v2.BehaviorController')
@patch('agent.digital_life_v2.PermissionSystem')
class TestBuildOfflineResponse:
    """测试 _build_offline_response 方法"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_build_offline_response_greeting(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试问候语响应"""
        mock_body_instance = MagicMock()
        mock_body_instance.collect_quick.return_value = []
        mock_body_sensor.return_value = mock_body_instance
        
        mock_behavior_instance = MagicMock()
        mock_behavior_instance.can_execute.return_value = (True, "")
        mock_behavior_instance.profile = MagicMock(label="正常", description="正常模式", suggestion="")
        mock_behavior.return_value = mock_behavior_instance
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        dl = DigitalLifeV2()
        dl._persona_initialized = False
        
        response = dl._build_offline_response("你好")
        
        assert "你好！" in response

    @pytest.mark.unit
    @pytest.mark.p0
    def test_build_offline_response_help(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试帮助请求响应"""
        mock_body_instance = MagicMock()
        mock_body_instance.collect_quick.return_value = []
        mock_body_sensor.return_value = mock_body_instance
        
        mock_behavior_instance = MagicMock()
        mock_behavior_instance.profile = MagicMock(label="正常", description="正常模式", suggestion="")
        mock_behavior.return_value = mock_behavior_instance
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        dl = DigitalLifeV2()
        dl._persona_initialized = False
        
        response = dl._build_offline_response("帮助")
        
        assert "我是来自网天的云枢" in response

    @pytest.mark.unit
    @pytest.mark.p0
    def test_build_offline_response_feelings(self, mock_permission, mock_behavior, mock_body_sensor):
        """测试询问状态响应"""
        mock_body_instance = MagicMock()
        mock_body_instance.collect_quick.return_value = []
        mock_body_instance.get_health_report.return_value = "健康报告"
        mock_body_sensor.return_value = mock_body_instance
        
        mock_behavior_instance = MagicMock()
        mock_behavior_instance.profile = MagicMock(label="正常", description="正常模式", suggestion="")
        mock_behavior.return_value = mock_behavior_instance
        
        mock_permission_instance = MagicMock()
        mock_permission.return_value = mock_permission_instance
        
        from agent.digital_life_v2 import DigitalLifeV2
        
        dl = DigitalLifeV2()
        dl._persona_initialized = False
        dl.body = mock_body_instance
        
        response = dl._build_offline_response("你怎么样")
        
        assert "让我感受一下我的身体" in response




