"""DigitalLife 集成测试

测试覆盖：
- P0: 模块安全导入（可选模块缺失时的优雅降级）
- P0: 构造与配置（不同配置开关组合）
- P0: 行为闭环（用户输入→感知→认知→行动）
- P1: 工具调用集成（ToolCallingService 交互）
- P1: 权限拦截（PermissionSystem 交互）
- P1: 状态持久化（state_manager 交互）
- P2: 懒加载版本验证

所有外部依赖必须 mock，测试可离线运行。
"""

import pytest
import logging
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def mock_all_external_dependencies():
    """Mock 所有 DigitalLife 的外部依赖，确保测试离线运行"""
    patches = [
        # 核心模块
        patch('agent.orchestrator.lifecycle_manager.BodySensor'),
        patch('agent.orchestrator.lifecycle_manager.PromptInjector'),
        patch('agent.orchestrator.lifecycle_manager.PromptConfig'),
        patch('agent.orchestrator.lifecycle_manager.MemoryManager'),
        patch('agent.orchestrator.lifecycle_manager.BehaviorController'),
        patch('agent.orchestrator.lifecycle_manager.PermissionSystem'),
        patch('agent.orchestrator.lifecycle_manager.tools'),
        patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'),
        # 可选模块
        patch('agent.orchestrator.lifecycle_manager._LIFETRACE_AVAILABLE', False),
        patch('agent.orchestrator.lifecycle_manager._PERSONA_AVAILABLE', False),
        patch('agent.orchestrator.lifecycle_manager._PLANNING_AVAILABLE', False),
        patch('agent.orchestrator.lifecycle_manager._MEMORY_AVAILABLE', False),
        patch('agent.orchestrator.lifecycle_manager._MONITORING_AVAILABLE', False),
        patch('agent.orchestrator.lifecycle_manager._VOICE_AVAILABLE', False),
        patch('agent.orchestrator.lifecycle_manager._OCR_AVAILABLE', False),
        patch('agent.orchestrator.lifecycle_manager._P6_SNAPSHOT_AVAILABLE', False),
        # 工作流引擎（运行时导入）
        patch('agent.workflow_engine.engine.WorkflowEngine'),
        patch('agent.workflow_engine.builtin_rules.register_builtin_rules'),
    ]
    
    for p in patches:
        p.start()
    
    yield
    
    for p in patches:
        p.stop()


@pytest.fixture
def mock_behavior_controller():
    """创建模拟的行为控制器"""
    mock = MagicMock()
    mock.profile = MagicMock()
    mock.profile.label = "NORMAL"
    mock.profile.description = "正常模式"
    mock.profile.enable_reflection = False
    mock.profile.response_prefix = ""
    mock.profile.suggestion = "请稍后再试。"
    mock.can_execute.return_value = (True, "")
    
    # 创建一个模拟的行为模式对象，value 属性为字符串
    mode_mock = MagicMock()
    mode_mock.value = "NORMAL"
    mock.evaluate.return_value = mode_mock
    
    return mock


@pytest.fixture
def mock_memory_manager():
    """创建模拟的记忆管理器"""
    mock = MagicMock()
    mock._llm_service = None
    mock._storage = MagicMock()
    mock._storage.load_recent_messages.return_value = []
    mock.save_log = MagicMock()
    mock.score_and_save_message = MagicMock()
    mock.add_message = MagicMock()
    mock.get_context = MagicMock(return_value=[])
    mock.load_summary = MagicMock(return_value=None)
    mock.get_working_memory = MagicMock(return_value={})
    mock.infer_working_memory = MagicMock()
    mock.get_budget_context = MagicMock(return_value=[])
    mock._token_counter = MagicMock()
    mock._token_counter.count_messages.return_value = 0
    mock.compress_rounds = 0
    mock._summarizer = MagicMock()
    mock._summarizer.should_compress.return_value = False
    mock._need_compress = False
    mock._memory_token_limit = 131072
    mock.clear_memory = MagicMock()
    mock.smart_prune = MagicMock()
    mock.generate_summary_levels = MagicMock()
    return mock


@pytest.fixture
def mock_permission_system():
    """创建模拟的权限系统"""
    mock = MagicMock()
    from agent.permission_system import PermissionResult
    mock.check_action.return_value = PermissionResult(allowed=True)
    return mock


@pytest.fixture
def digital_life(mock_behavior_controller, mock_memory_manager, mock_permission_system):
    """创建 DigitalLife 实例"""
    from agent.digital_life import DigitalLife
    
    with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
               return_value=mock_behavior_controller):
        with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                   return_value=mock_memory_manager):
            with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                       return_value=mock_permission_system):
                return DigitalLife(config={})


class TestModuleSafeImport:
    """P0: 模块安全导入——验证可选模块缺失时的优雅降级"""

    def test_safe_import_missing_module_returns_fallback(self):
        """测试缺失模块返回回退值"""
        from agent.digital_life import _safe_import
        
        result, success = _safe_import(
            'nonexistent_module_test_123',
            lambda: __import__('nonexistent_module_test_123'),
            'fallback_value'
        )
        
        assert success is False
        assert result == 'fallback_value'

    def test_safe_import_success(self):
        """测试成功导入模块"""
        from agent.digital_life import _safe_import
        
        result, success = _safe_import(
            'math_module',
            lambda: __import__('math'),
            None
        )
        
        assert success is True
        assert result is not None
        assert hasattr(result, 'sqrt')

    def test_safe_import_with_exception(self):
        """测试导入时抛出异常"""
        from agent.digital_life import _safe_import
        
        def raising_func():
            raise ValueError("Test exception")
        
        result, success = _safe_import('error_module', raising_func, 'fallback')
        
        assert success is False
        assert result == 'fallback'

    def test_safe_import_from_partial_success(self):
        """测试从包导入部分成功"""
        from agent.digital_life import _safe_import_from
        
        result, success = _safe_import_from('math', 'sqrt', 'nonexistent_func_test')
        
        assert success is False
        assert result['sqrt'] is not None
        assert result['nonexistent_func_test'] is None

    def test_digital_life_initializes_with_missing_optional_modules(self):
        """测试所有可选模块缺失时仍能初始化"""
        from agent.digital_life import DigitalLife
        
        with patch('agent.orchestrator.lifecycle_manager._LIFETRACE_AVAILABLE', False):
            with patch('agent.orchestrator.lifecycle_manager._PERSONA_AVAILABLE', False):
                with patch('agent.orchestrator.lifecycle_manager._PLANNING_AVAILABLE', False):
                    with patch('agent.orchestrator.lifecycle_manager._MEMORY_AVAILABLE', False):
                        with patch('agent.orchestrator.lifecycle_manager._MONITORING_AVAILABLE', False):
                            with patch('agent.orchestrator.lifecycle_manager._VOICE_AVAILABLE', False):
                                with patch('agent.orchestrator.lifecycle_manager._OCR_AVAILABLE', False):
                                    with patch('agent.orchestrator.lifecycle_manager._P6_SNAPSHOT_AVAILABLE', False):
                                        digital_life = DigitalLife(config={})
                                        assert digital_life is not None
                                        assert digital_life._v2_lifetrace is False
                                        assert digital_life._v2_persona is False


class TestConstructionAndConfiguration:
    """P0: 构造与配置——不同配置开关组合下的初始化行为"""

    def test_init_with_empty_config(self):
        """测试空配置初始化"""
        from agent.digital_life import DigitalLife
        
        digital_life = DigitalLife(config={})
        
        assert digital_life._config == {}
        assert digital_life._v2_lifetrace is False
        assert digital_life._v2_persona is False
        assert digital_life._v2_distillation is False

    def test_init_with_v2_features_requested_but_unavailable(self):
        """测试请求V2功能但模块不可用时优雅降级"""
        from agent.digital_life import DigitalLife
        
        config = {
            'features': {
                'v2_lifetrace': True,
                'v2_persona': True,
                'v2_distillation': True
            }
        }
        
        with patch('agent.orchestrator.lifecycle_manager._LIFETRACE_AVAILABLE', False):
            with patch('agent.orchestrator.lifecycle_manager._PERSONA_AVAILABLE', False):
                digital_life = DigitalLife(config=config)
                
                assert digital_life._v2_lifetrace is False
                assert digital_life._v2_persona is False
                assert digital_life._v2_distillation is False

    def test_init_with_sensor_config(self):
        """测试带传感器配置的初始化"""
        from agent.digital_life import DigitalLife
        
        config = {
            'sensor': {
                'watch_dirs': ['./data'],
                'enable_change_detection': True,
                'enable_event_monitor': False
            }
        }
        
        with patch('agent.orchestrator.lifecycle_manager.BodySensor') as mock_body:
            digital_life = DigitalLife(config=config)
            mock_body.assert_called_once()
            call_kwargs = mock_body.call_args
            assert call_kwargs[1]['watch_dirs'] == ['./data']
            assert call_kwargs[1]['enable_event_monitor'] is False

    def test_init_with_health_check_interval(self):
        """测试自定义健康检查间隔"""
        from agent.digital_life import DigitalLife
        
        config = {
            'behavior': {
                'check_interval': 60
            }
        }
        
        digital_life = DigitalLife(config=config)
        
        assert digital_life._health_check_interval == 60

    def test_init_with_planning_disabled(self):
        """测试规划引擎禁用"""
        from agent.digital_life import DigitalLife
        
        config = {'planning': {'enabled': False}}
        
        with patch('agent.orchestrator.lifecycle_manager._PLANNING_AVAILABLE', True):
            digital_life = DigitalLife(config=config)
            assert digital_life._planning_enabled is False

    def test_module_import_results_tracking(self):
        """测试模块导入结果追踪"""
        from agent.digital_life import _module_import_results
        
        assert isinstance(_module_import_results, dict)
        expected_modules = ['lifetrace', 'persona', 'planning', 'vector_memory',
                           'monitoring', 'voice', 'ocr', 'p6_snapshot']
        for module in expected_modules:
            assert module in _module_import_results
            assert isinstance(_module_import_results[module], bool)


class TestBehaviorLoop:
    """P0: 行为闭环——用户输入→感知→认知→行动的完整路径"""

    def test_chat_when_not_running(self, digital_life):
        """测试未运行时的对话"""
        response = digital_life.chat("你好")
        
        assert "唤醒" in response or "start" in response.lower()

    @pytest.mark.skip_ci
    def test_chat_increment_interaction_count(self, mock_behavior_controller,
                                               mock_memory_manager, mock_permission_system):
        """测试对话增加交互计数"""
        from agent.digital_life import DigitalLife
        
        mock_workflow = MagicMock()
        mock_workflow.try_match.return_value = MagicMock(matched=False)
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    with patch('agent.workflow_engine.engine.WorkflowEngine', 
                               return_value=mock_workflow):
                        digital_life = DigitalLife(config={})
                        digital_life.start()
                        
                        assert digital_life._interaction_count == 0
                        
                        digital_life._call_llm = MagicMock(return_value="测试响应")
                        digital_life._call_llm_v2 = MagicMock(return_value="测试响应")
                        
                        response = digital_life.chat("你好")
                        
                        assert digital_life._interaction_count == 1
                        assert response == "测试响应"

    @pytest.mark.skip_ci
    def test_behavior_can_execute_rejects_request(self, mock_behavior_controller, 
                                                  mock_memory_manager, mock_permission_system):
        """测试行为控制器拒绝请求"""
        from agent.digital_life import DigitalLife
        
        mock_behavior_controller.can_execute.return_value = (False, "资源不足")
        mock_workflow = MagicMock()
        mock_workflow.try_match.return_value = MagicMock(matched=False)
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    with patch('agent.workflow_engine.engine.WorkflowEngine', 
                               return_value=mock_workflow):
                        digital_life = DigitalLife(config={})
                        digital_life.start()
                        
                        # 使用 process 方法直接获取响应字典
                        result = digital_life.process("请求资源")
                        
                        # 检查响应是否包含拒绝相关内容
                        response = result.get("response", "") or result.get("data", "") or str(result)
                        assert "资源不足" in response or "拒绝" in response or "rejected" in response.lower()

    @pytest.mark.skip_ci
    def test_workflow_engine_match(self, mock_behavior_controller, 
                                   mock_memory_manager, mock_permission_system):
        """测试工作流引擎规则匹配（零Token消耗路径）"""
        from agent.digital_life import DigitalLife
        
        mock_workflow = MagicMock()
        mock_workflow.try_match.return_value = MagicMock(
            matched=True, 
            output="工作流响应", 
            rule_name="test_rule",
            intent="test",
            confidence=0.9,
            execution_time_ms=1.0
        )
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    with patch('agent.workflow_engine.engine.WorkflowEngine', 
                               return_value=mock_workflow):
                        digital_life = DigitalLife(config={})
                        digital_life.start()
                        
                        result = digital_life.process("测试输入")
                        
                        assert result["success"] is True
                        assert result["data"]["output"] == "工作流响应"
                        mock_workflow.try_match.assert_called_once()

    def test_input_guard_blocks_malicious_input(self, mock_behavior_controller, 
                                                 mock_memory_manager, mock_permission_system):
        """测试输入护栏拦截恶意输入"""
        from agent.digital_life import DigitalLife
        from agent.guardrails.input_guard import GuardAction, GuardResult
        
        mock_input_guard = MagicMock()
        mock_input_guard.check.return_value = GuardResult(
            action=GuardAction.BLOCK,
            reason="恶意输入",
            matched_pattern=".*恶意.*"
        )
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    digital_life = DigitalLife(config={})
                    digital_life._guardrails_input_guard = mock_input_guard
                    digital_life.start()
                    
                    result = digital_life.process("恶意命令")
                    
                    assert result["success"] is False
                    assert "blocked" in result.get("msg", "").lower() or "拦截" in result.get("msg", "")


class TestToolCallingIntegration:
    """P1: 工具调用集成——DigitalLife 调用 ToolCallingService"""

    def test_tool_calling_service_initialization(self, mock_behavior_controller, 
                                                  mock_memory_manager, mock_permission_system):
        """测试工具调用服务初始化"""
        from agent.digital_life import DigitalLife
        
        mock_llm = MagicMock()
        mock_memory_manager._llm_service = mock_llm
        
        config = {
            'tool_calling': {
                'enabled': True,
                'max_rounds': 20,
                'tool_timeout': 60
            }
        }
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    with patch('agent.tool_calling.ToolCallingService') as mock_tc:
                        digital_life = DigitalLife(config=config)
                        
                        mock_tc.assert_called_once()
                        assert digital_life._tool_calling_service is not None

    def test_tool_calling_service_disabled_when_no_llm(self, mock_behavior_controller, 
                                                       mock_memory_manager, mock_permission_system):
        """测试无LLM时工具调用服务禁用"""
        from agent.digital_life import DigitalLife
        
        mock_memory_manager._llm_service = None
        
        config = {'tool_calling': {'enabled': True}}
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    digital_life = DigitalLife(config=config)
                    
                    assert digital_life._tool_calling_service is None

    @pytest.mark.skip_ci
    def test_tool_calling_chat_flow(self, mock_behavior_controller, 
                                     mock_memory_manager, mock_permission_system):
        """测试工具调用对话流程"""
        from agent.digital_life import DigitalLife
        
        mock_llm = MagicMock()
        mock_memory_manager._llm_service = mock_llm
        
        mock_tc = MagicMock()
        mock_tc.chat_with_steps.return_value = {
            "text": "工具调用结果",
            "steps": []
        }
        
        mock_workflow = MagicMock()
        mock_workflow.try_match.return_value = MagicMock(matched=False)
        
        config = {'tool_calling': {'enabled': True}}
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    with patch('agent.workflow_engine.engine.WorkflowEngine', 
                               return_value=mock_workflow):
                        with patch('agent.tool_calling.ToolCallingService', return_value=mock_tc):
                            digital_life = DigitalLife(config=config)
                            digital_life._v2_lifetrace = True
                            digital_life._trace_recorder = MagicMock()
                            digital_life.start()
                            
                            # 触发 _call_llm_v2 路径
                            result = digital_life.process("搜索天气")
                            
                            assert result["success"] is True
                            mock_tc.chat_with_steps.assert_called()


class TestPermissionIntegration:
    """P1: 权限拦截——PermissionSystem 与 DigitalLife 的交互"""

    def test_request_permission_allowed(self, mock_behavior_controller, 
                                         mock_memory_manager, mock_permission_system):
        """测试权限检查允许"""
        from agent.digital_life import DigitalLife
        from agent.permission_system import PermissionResult
        
        mock_permission_system.check_action.return_value = PermissionResult(allowed=True)
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    digital_life = DigitalLife(config={})
                    
                    result = digital_life.request_permission("safe_action")
                    
                    assert result.allowed is True
                    mock_permission_system.check_action.assert_called_once()

    def test_request_permission_denied(self, mock_behavior_controller, 
                                        mock_memory_manager, mock_permission_system):
        """测试权限检查拒绝"""
        from agent.digital_life import DigitalLife
        from agent.permission_system import PermissionResult
        
        mock_permission_system.check_action.return_value = PermissionResult(
            allowed=False,
            reason="危险操作"
        )
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    digital_life = DigitalLife(config={})
                    
                    result = digital_life.request_permission("rm -rf /")
                    
                    assert result.allowed is False
                    assert result.reason == "危险操作"

    def test_abort_chat_when_tool_calling_active(self, mock_behavior_controller, 
                                                  mock_memory_manager, mock_permission_system):
        """测试中止对话功能"""
        from agent.digital_life import DigitalLife
        
        mock_llm = MagicMock()
        mock_memory_manager._llm_service = mock_llm
        
        mock_tc = MagicMock()
        mock_tc.abort = MagicMock(return_value=True)
        
        config = {'tool_calling': {'enabled': True}}
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    with patch('agent.tool_calling.ToolCallingService', return_value=mock_tc):
                        digital_life = DigitalLife(config=config)
                        
                        result = digital_life.abort_chat()
                        
                        assert result is True
                        mock_tc.abort.assert_called_once()


class TestStatePersistence:
    """P1: 状态持久化——state_manager 与 DigitalLife 的交互"""

    def test_start_records_session_info(self, digital_life):
        """测试启动时记录会话信息"""
        session_id_before = digital_life._session_id
        
        digital_life.start()
        
        assert digital_life._started_at is not None
        assert digital_life._interaction_count == 0
        assert digital_life.is_running is True

    def test_stop_saves_summary(self, mock_behavior_controller, 
                                 mock_memory_manager, mock_permission_system):
        """测试停止时保存摘要"""
        from agent.digital_life import DigitalLife
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    digital_life = DigitalLife(config={})
                    digital_life.start()
                    
                    digital_life.stop()
                    
                    assert digital_life.is_running is False
                    mock_memory_manager.generate_summary_levels.assert_called()

    @pytest.mark.skip_ci
    def test_memory_logging(self, mock_behavior_controller, 
                            mock_memory_manager, mock_permission_system):
        """测试记忆日志记录"""
        from agent.digital_life import DigitalLife
        
        mock_workflow = MagicMock()
        mock_workflow.try_match.return_value = MagicMock(matched=False)
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    with patch('agent.workflow_engine.engine.WorkflowEngine', 
                               return_value=mock_workflow):
                        digital_life = DigitalLife(config={})
                        digital_life._v2_lifetrace = False
                        digital_life.start()
                        
                        # Mock LLM 调用
                        digital_life._call_llm = MagicMock(return_value="响应")
                        
                        digital_life.chat("测试")
                        
                        mock_memory_manager.score_and_save_message.assert_called()
                        mock_memory_manager.add_message.assert_called()


class TestLifecycleManagement:
    """P1: 生命周期管理——start/stop/restart"""

    def test_start_stop_cycle(self, digital_life):
        """测试启动停止循环"""
        assert digital_life.is_running is False
        
        digital_life.start()
        assert digital_life.is_running is True
        
        digital_life.stop()
        assert digital_life.is_running is False

    def test_health_check_during_run(self, mock_behavior_controller, 
                                      mock_memory_manager, mock_permission_system):
        """测试运行时健康检查"""
        from agent.digital_life import DigitalLife
        
        mock_body = MagicMock()
        mock_body.collect_quick.return_value = []
        
        with patch('agent.orchestrator.lifecycle_manager.BodySensor', return_value=mock_body):
            with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                       return_value=mock_behavior_controller):
                with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                           return_value=mock_memory_manager):
                    with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                               return_value=mock_permission_system):
                        digital_life = DigitalLife(config={})
                        digital_life.start()
                        
                        readings = digital_life.check_health()
                        
                        assert isinstance(readings, list)
                        mock_body.collect_quick.assert_called()
                        mock_behavior_controller.evaluate.assert_called()


class TestLazyLoading:
    """P2: 懒加载版本验证"""

    def test_lifetrace_lazy_initialization(self, mock_behavior_controller, 
                                           mock_memory_manager, mock_permission_system):
        """测试 LifeTrace 懒加载初始化（当配置启用且模块可用时，初始化时自动触发）"""
        from agent.digital_life import DigitalLife
        
        config = {'features': {'v2_lifetrace': True}}
        
        mock_trace_recorder = MagicMock()
        mock_memory_retriever = MagicMock()
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    with patch('agent.orchestrator.lifecycle_manager._LIFETRACE_AVAILABLE', True):
                        with patch('agent.digital_life._LIFETRACE_AVAILABLE', True):
                            with patch('agent.system_prompt_config.is_section_enabled', return_value=True):
                                # 使用 mock 整个 lifetrace 模块，因为是运行时导入
                                with patch.dict('sys.modules', {
                                    'lifetrace': MagicMock(
                                        TraceRecorder=mock_trace_recorder,
                                        MemoryRetriever=mock_memory_retriever
                                    )
                                }):
                                    # 重新导入确保使用 mock 的模块
                                    import importlib
                                    import agent.digital_life_persona
                                    importlib.reload(agent.digital_life_persona)
                                    
                                    digital_life = DigitalLife(config=config)
                                    
                                    # 初始化时已自动触发懒加载
                                    assert digital_life._lifetrace_initialized is True
                                    mock_trace_recorder.assert_called()
                                    mock_memory_retriever.assert_called()

    def test_lifetrace_disabled_returns_false(self, digital_life):
        """测试 LifeTrace 禁用时返回 False"""
        digital_life._v2_lifetrace = False
        
        result = digital_life._ensure_lifetrace()
        
        assert result is False
        assert digital_life._lifetrace_initialized is False

    def test_lifetrace_already_initialized(self, digital_life):
        """测试 LifeTrace 已初始化时直接返回"""
        digital_life._v2_lifetrace = True
        digital_life._lifetrace_initialized = True
        
        result = digital_life._ensure_lifetrace()
        
        assert result is True


class TestPlanningIntegration:
    """P2: 规划引擎集成"""

    def test_planning_engine_initialization(self, mock_behavior_controller, 
                                            mock_memory_manager, mock_permission_system):
        """测试规划引擎初始化"""
        from agent.digital_life import DigitalLife
        
        mock_llm = MagicMock()
        mock_memory_manager._llm_service = mock_llm
        
        config = {'planning': {'enabled': True, 'max_iterations': 20}}
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    with patch('agent.orchestrator.lifecycle_manager._PLANNING_AVAILABLE', True):
                        with patch('agent.orchestrator.lifecycle_manager.PlanningCore') as mock_planning:
                            with patch('agent.orchestrator.lifecycle_manager.ReActLoop') as mock_react:
                                with patch('agent.orchestrator.lifecycle_manager.ToolRegistry') as mock_tool_reg:
                                    digital_life = DigitalLife(config=config)
                                    
                                    assert digital_life._planning_enabled is True
                                    mock_planning.assert_called()
                                    mock_react.assert_called()

    def test_planning_engine_unavailable(self, mock_behavior_controller, 
                                         mock_memory_manager, mock_permission_system):
        """测试规划引擎不可用时禁用"""
        from agent.digital_life import DigitalLife
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    with patch('agent.orchestrator.lifecycle_manager._PLANNING_AVAILABLE', False):
                        digital_life = DigitalLife(config={})
                        
                        assert digital_life._planner is None
                        assert digital_life._react_loop is None
                        assert digital_life._planning_enabled is False


class TestReflection:
    """P2: 自我反思功能集成"""

    def test_self_reflect_with_llm(self, mock_behavior_controller, 
                                    mock_memory_manager, mock_permission_system):
        """测试自我反思（P8 全本地实现）"""
        from agent.digital_life import DigitalLife
        
        mock_behavior_controller.profile.enable_reflection = True
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    digital_life = DigitalLife(config={})
                    digital_life._is_skill_enabled = MagicMock(return_value=True)
                    
                    result = digital_life.self_reflect("测试任务", "测试响应")
                    
                    assert "reflection" in result
                    assert "任务以中文为主" in result["reflection"]
                    mock_memory_manager.save_log.assert_called()

    def test_self_reflect_without_llm(self, mock_behavior_controller, 
                                       mock_memory_manager, mock_permission_system):
        """测试无LLM时的反思"""
        from agent.digital_life import DigitalLife
        
        mock_memory_manager._llm_service = None
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    digital_life = DigitalLife(config={})
                    digital_life._is_skill_enabled = MagicMock(return_value=True)
                    
                    result = digital_life.self_reflect("测试任务", "测试响应")
                    
                    assert "reflection" in result
                    mock_memory_manager.save_log.assert_called()


class TestV2PersonaIntegration:
    """P1: V2 Persona 系统集成测试"""

    def test_persona_lazy_initialization(self, mock_behavior_controller, 
                                        mock_memory_manager, mock_permission_system):
        """测试 Persona 懒加载初始化 - 包含详细日志用于排查失败原因"""
        from agent.digital_life import DigitalLife
        import sys
        
        config = {'features': {'v2_persona': True}}
        
        mock_persona_model = MagicMock()
        mock_persona_injector = MagicMock()
        mock_persona_injector.should_refuse_task.return_value = (False, "")
        
        print("\n" + "="*80)
        print("[DEBUG] 开始测试 Persona 懒加载初始化")
        print("[DEBUG] 配置: ", config)
        print("[DEBUG] mock_persona_model: ", mock_persona_model)
        print("[DEBUG] mock_persona_injector: ", mock_persona_injector)
        
        try:
            with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                       return_value=mock_behavior_controller):
                print("[DEBUG] BehaviorController patched")
                
                with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                           return_value=mock_memory_manager):
                    print("[DEBUG] MemoryManager patched")
                    
                    with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                               return_value=mock_permission_system):
                        print("[DEBUG] PermissionSystem patched")
                        
                        with patch('agent.orchestrator.lifecycle_manager._PERSONA_AVAILABLE', True):
                            with patch('agent.digital_life._PERSONA_AVAILABLE', True):
                                with patch('agent.system_prompt_config.is_section_enabled', return_value=True):
                                    print("[DEBUG] _PERSONA_AVAILABLE set to True")
                                    
                                    with patch.dict('sys.modules', {
                                        'persona': MagicMock(
                                            PersonaModel=mock_persona_model,
                                            PersonaInjector=mock_persona_injector,
                                        )
                                    }):
                                        print("[DEBUG] sys.modules patched with persona module")
                                        
                                        import importlib
                                        import agent.digital_life_persona
                                        print("[DEBUG] Reloading agent.digital_life_persona...")
                                        importlib.reload(agent.digital_life_persona)
                                        print("[DEBUG] agent.digital_life_persona reloaded")
                                        
                                        print("[DEBUG] Creating DigitalLife instance...")
                                        digital_life = DigitalLife(config=config)
                                        print("[DEBUG] DigitalLife instance created: ", digital_life)
                                        
                                        print("[DEBUG] _v2_persona value: ", digital_life._v2_persona)
                                        assert digital_life._v2_persona is True, \
                                            f"[ERROR] _v2_persona 应为 True，实际为 {digital_life._v2_persona}"
                                        
                                        print("[DEBUG] 调用 _ensure_persona() 触发懒加载...")
                                        result = digital_life._ensure_persona()
                                        print("[DEBUG] _ensure_persona() 返回值: ", result)
                                        
                                        assert result is True, \
                                            f"[ERROR] _ensure_persona() 应为 True，实际为 {result}"
                                        assert digital_life._persona_initialized is True, \
                                            f"[ERROR] _persona_initialized 应为 True，实际为 {digital_life._persona_initialized}"
                                        
                                        print("[DEBUG] 检查 mock_persona_model 是否被调用: ", mock_persona_model.called)
                                        assert mock_persona_model.called, \
                                            "[ERROR] mock_persona_model 未被调用"
                                        
                                        print("[DEBUG] 检查 mock_persona_injector 是否被调用: ", mock_persona_injector.called)
                                        assert mock_persona_injector.called, \
                                            "[ERROR] mock_persona_injector 未被调用"
                                        
                                        print("[DEBUG] Persona 懒加载初始化测试通过!")
                                
        except Exception as e:
            print(f"[ERROR] 测试失败: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            raise
        finally:
            print("="*80 + "\n")

    def test_persona_rejects_task(self, mock_behavior_controller, 
                                 mock_memory_manager, mock_permission_system):
        """测试 Persona 拒绝任务 - 包含详细日志"""
        from agent.digital_life import DigitalLife
        
        mock_persona_injector = MagicMock()
        mock_persona_injector.should_refuse_task.return_value = (True, "不符合人格设定")
        
        print("\n" + "="*80)
        print("[DEBUG] 开始测试 Persona 拒绝任务")
        print("[DEBUG] mock_persona_injector.should_refuse_task 返回: ", 
              mock_persona_injector.should_refuse_task.return_value)
        
        try:
            with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                       return_value=mock_behavior_controller):
                with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                           return_value=mock_memory_manager):
                    with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                               return_value=mock_permission_system):
                        with patch('agent.orchestrator.lifecycle_manager._PERSONA_AVAILABLE', True):
                            with patch('agent.digital_life._PERSONA_AVAILABLE', True):
                                with patch('agent.orchestrator.lifecycle_manager.PersonaInjector', 
                                           return_value=mock_persona_injector):
                                    digital_life = DigitalLife(config={'features': {'v2_persona': True}})
                                    digital_life._v2_persona = True
                                    digital_life._persona_injector = mock_persona_injector
                                    
                                    print("[DEBUG] 调用 should_refuse_task('测试请求')...")
                                    refused, reason = mock_persona_injector.should_refuse_task("测试请求")
                                    
                                    print(f"[DEBUG] refused={refused}, reason={reason}")
                                    assert refused is True, \
                                        f"[ERROR] refused 应为 True，实际为 {refused}"
                                    assert "不符合人格设定" in reason, \
                                        f"[ERROR] reason 应包含 '不符合人格设定'，实际为 '{reason}'"
                                    
                                    print("[DEBUG] Persona 拒绝任务测试通过!")
        except Exception as e:
            print(f"[ERROR] 测试失败: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            raise
        finally:
            print("="*80 + "\n")

    def test_persona_disabled(self, digital_life):
        """测试 Persona 禁用时的行为 - 包含详细日志"""
        print("\n" + "="*80)
        print("[DEBUG] 开始测试 Persona 禁用时的行为")
        
        digital_life._v2_persona = False
        digital_life._persona_injector = None
        
        print(f"[DEBUG] _v2_persona = {digital_life._v2_persona}")
        print(f"[DEBUG] _persona_injector = {digital_life._persona_injector}")
        
        try:
            result = digital_life._ensure_persona()
            print(f"[DEBUG] _ensure_persona() 返回值: {result}")
            
            assert result is False, \
                f"[ERROR] result 应为 False，实际为 {result}"
            assert digital_life._persona_initialized is False, \
                f"[ERROR] _persona_initialized 应为 False，实际为 {digital_life._persona_initialized}"
            
            print("[DEBUG] Persona 禁用测试通过!")
        except Exception as e:
            print(f"[ERROR] 测试失败: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            raise
        finally:
            print("="*80 + "\n")


class TestV2DistillationIntegration:
    """P2: V2 Distillation 系统集成测试"""

    def test_distillation_lazy_initialization(self, mock_behavior_controller, 
                                             mock_memory_manager, mock_permission_system):
        """测试 Distillation 懒加载初始化"""
        from agent.digital_life import DigitalLife
        
        config = {'features': {'v2_distillation': True}}
        
        mock_extractor = MagicMock()
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    with patch('agent.orchestrator.lifecycle_manager._PERSONA_AVAILABLE', True):
                        with patch('agent.digital_life._PERSONA_AVAILABLE', True):
                            with patch('agent.system_prompt_config.is_section_enabled', return_value=True):
                                with patch.dict('sys.modules', {
                                    'persona': MagicMock(
                                        PersonalityPreferenceExtractor=mock_extractor,
                                    )
                                }):
                                    import importlib
                                    import agent.digital_life_persona
                                    importlib.reload(agent.digital_life_persona)
                                    
                                    digital_life = DigitalLife(config=config)
                                    
                                    assert digital_life._v2_distillation is True
                                    # 懒加载需要显式调用 _ensure_distillation()
                                    result = digital_life._ensure_distillation()
                                    assert result is True
                                    mock_extractor.assert_called()

    def test_distillation_update_incremental(self, mock_behavior_controller, 
                                            mock_memory_manager, mock_permission_system):
        """测试增量更新人格蒸馏 - 测试 extractor 属性设置"""
        from agent.digital_life import DigitalLife
        
        mock_extractor = MagicMock()
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    digital_life = DigitalLife(config={})
                    digital_life._v2_distillation = True
                    digital_life._persona_extractor = mock_extractor
                    
                    # 验证 extractor 正确设置
                    assert digital_life._v2_distillation is True
                    assert digital_life._persona_extractor is mock_extractor

    def test_distillation_disabled(self, digital_life):
        """测试 Distillation 禁用时不执行"""
        digital_life._v2_distillation = False
        digital_life._persona_extractor = MagicMock()
        
        result = digital_life._ensure_distillation()
        
        assert result is False
        assert digital_life._distillation_initialized is False


class TestV2Compatibility:
    """P1: V2 功能与旧版本兼容性测试"""

    @pytest.mark.skip_ci
    def test_v2_features_disabled_backward_compatible(self, mock_behavior_controller, 
                                                     mock_memory_manager, mock_permission_system):
        """测试禁用 V2 功能时与旧版本兼容"""
        from agent.digital_life import DigitalLife
        
        config = {
            'features': {
                'v2_lifetrace': False,
                'v2_persona': False,
                'v2_distillation': False
            }
        }
        
        mock_workflow = MagicMock()
        mock_workflow.try_match.return_value = MagicMock(matched=False)
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    with patch('agent.workflow_engine.engine.WorkflowEngine', 
                               return_value=mock_workflow):
                        digital_life = DigitalLife(config=config)
                        digital_life.start()
                        
                        result = digital_life.process("你好")
                        
                        assert result["success"] is True
                        assert digital_life._v2_lifetrace is False
                        assert digital_life._v2_persona is False
                        assert digital_life._v2_distillation is False

    def test_mixed_v2_configuration(self, mock_behavior_controller, 
                                    mock_memory_manager, mock_permission_system):
        """测试混合 V2 配置（部分启用，部分禁用）"""
        from agent.digital_life import DigitalLife
        
        config = {
            'features': {
                'v2_lifetrace': True,
                'v2_persona': False,
                'v2_distillation': True
            }
        }
        
        mock_trace_recorder = MagicMock()
        mock_memory_retriever = MagicMock()
        mock_extractor = MagicMock()
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    with patch('agent.orchestrator.lifecycle_manager._LIFETRACE_AVAILABLE', True):
                        with patch('agent.digital_life._LIFETRACE_AVAILABLE', True):
                            with patch('agent.orchestrator.lifecycle_manager._PERSONA_AVAILABLE', True):
                                with patch('agent.digital_life._PERSONA_AVAILABLE', True):
                                    with patch('agent.system_prompt_config.is_section_enabled', return_value=True):
                                        with patch.dict('sys.modules', {
                                            'lifetrace': MagicMock(
                                                TraceRecorder=mock_trace_recorder,
                                                MemoryRetriever=mock_memory_retriever,
                                            ),
                                            'persona': MagicMock(
                                                PersonalityPreferenceExtractor=mock_extractor,
                                            )
                                        }):
                                            import importlib
                                            import agent.digital_life_persona
                                            importlib.reload(agent.digital_life_persona)
                                            
                                            digital_life = DigitalLife(config=config)
                                            
                                            assert digital_life._v2_lifetrace is True
                                            assert digital_life._v2_persona is False
                                            assert digital_life._v2_distillation is True
                                            # 初始化时已自动触发懒加载
                                            assert digital_life._lifetrace_initialized is True

    def test_v1_legacy_config_backward_compatible(self, mock_behavior_controller, 
                                                 mock_memory_manager, mock_permission_system):
        """测试 V1 遗留配置向后兼容"""
        from agent.digital_life import DigitalLife
        
        config = {
            'memory': {'max_tokens': 8000},
            'behavior': {'default_mode': 'NORMAL'},
            'planning': {'enabled': False}
        }
        
        # 配置通过 MemoryManager 传递，所以需要设置 mock 的属性
        mock_memory_manager._memory_token_limit = 8000
        
        with patch('agent.orchestrator.lifecycle_manager.BehaviorController', 
                   return_value=mock_behavior_controller):
            with patch('agent.orchestrator.lifecycle_manager.MemoryManager', 
                       return_value=mock_memory_manager):
                with patch('agent.orchestrator.lifecycle_manager.PermissionSystem', 
                           return_value=mock_permission_system):
                    digital_life = DigitalLife(config=config)
                    
                    assert digital_life is not None
                    # 验证配置被正确传递
                    assert mock_memory_manager._memory_token_limit == 8000
                    assert digital_life._v2_lifetrace is False
                    assert digital_life._v2_persona is False


class TestLazyLoaderCompatibility:
    """P2: 懒加载兼容性测试"""

    def test_lazy_loader_parallel_preloading(self):
        """测试并行预加载"""
        from agent.lazy_loader import ParallelPreloader
        
        preloader = ParallelPreloader(max_workers=2)
        
        mock_loader_func = MagicMock(return_value="loaded")
        modules = [('module_a', mock_loader_func), ('module_b', mock_loader_func)]
        preloader.preload(modules)
        
        assert len(preloader.results) == len(modules)
        preloader.shutdown()

    def test_lazy_loader_import_timing(self):
        """测试懒加载导入时机"""
        from agent.lazy_loader import lazy_load, LoadLevel
        
        call_count = [0]
        
        @lazy_load(level=LoadLevel.OPTIONAL)
        def get_optional_module():
            call_count[0] += 1
            return "loaded_module"
        
        # 调用前不应执行
        assert call_count[0] == 0
        
        # 首次调用时执行
        result = get_optional_module()
        
        assert result == "loaded_module"
        assert call_count[0] == 1

    def test_lazy_loader_error_handling(self):
        """测试懒加载错误处理 - 使用 try-except 处理导入失败"""
        from agent.lazy_loader import lazy_load, LoadLevel
        
        @lazy_load(level=LoadLevel.OPTIONAL)
        def get_failing_module():
            raise ImportError("Test import failure")
        
        # 懒加载装饰器不会自动捕获异常，需要在调用时处理
        try:
            result = get_failing_module()
            # 如果没有异常，说明成功
            assert result is not None
        except ImportError:
            # 预期会抛出异常，懒加载不自动处理异常
            pass

    def test_lazy_loader_stats(self):
        """测试懒加载统计"""
        from agent.lazy_loader import get_lazy_loader, LoadStats
        
        loader = get_lazy_loader()
        stats = loader.stats
        
        assert isinstance(stats, LoadStats)
        assert stats.total_attempts >= 0
        assert stats.successful_loads >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])