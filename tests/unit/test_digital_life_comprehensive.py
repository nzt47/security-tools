"""
DigitalLife 综合测试 - 覆盖完整测试场景
"""
import pytest
import os
import sys
import logging
from datetime import datetime
from unittest.mock import patch, MagicMock, Mock

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.digital_life import (
    DigitalLife, ModuleLoadError,
    _safe_import, _safe_import_from, _report_module_import_status,
    _module_import_results, DEFAULT_SYSTEM_PROMPT,
    _LIFETRACE_AVAILABLE, _PERSONA_AVAILABLE, _PLANNING_AVAILABLE,
    _MEMORY_AVAILABLE, _MONITORING_AVAILABLE, _VOICE_AVAILABLE,
    _OCR_AVAILABLE, _P6_SNAPSHOT_AVAILABLE,
)

class MockLLM:
    def __init__(self, response="Hello, how can I help you?"):
        self.response = response
        self.model = "mock-model"

    def chat(self, messages=None, system_prompt=None, max_tokens=1024, temperature=0.7, **kwargs):
        return self.response

    def _get_client(self):
        """返回模拟的 API 客户端"""
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.content = self.response
        mock_message.tool_calls = None
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response
        return mock_client

class MockBehavior:
    """模拟行为控制器"""
    def __init__(self, can_execute=True, reject_reason="", enable_reflection=False):
        self.can_execute_result = (can_execute, reject_reason)
        self.profile = MagicMock()
        self.profile.label = "default"
        self.profile.description = "Default mode"
        self.profile.enable_reflection = enable_reflection
        self.profile.response_prefix = ""
        self._reasons = []

    def can_execute(self, user_input):
        return self.can_execute_result

class MockMemory:
    """模拟记忆系统"""
    def __init__(self):
        self.messages = []
        self._storage = MagicMock()
        self._storage.load_recent_messages.return_value = []

    def save_log(self, log_type, data):
        pass

    def add_message(self, role, content):
        self.messages.append({"role": role, "content": content})

    def score_and_save_message(self, role, content):
        self.messages.append({"role": role, "content": content})

    def get_context(self, token_limit=None):
        return self.messages

    def load_summary(self):
        return None

    def get_working_memory(self):
        return {}

    def infer_working_memory(self, user_input, response):
        pass

    def get_budget_context(self, recent_messages=None, summary_text=None, tool_results=None):
        return []

class MockVectorMemory:
    """模拟向量记忆系统"""
    def __init__(self):
        self.data = []

    def add(self, content, metadata=None):
        self.data.append({"content": content, "metadata": metadata})
        return f"memory_{len(self.data)}"

    def search(self, query, top_k=3):
        return []


@pytest.fixture
def minimal_config():
    return {
        "llm": {"provider": "mock", "model": "test-model"},
        "enable_planning": False,
        "enable_vector_memory": False,
        "enable_voice": False,
        "enable_p6_snapshot": False,
        "enable_safety_monitor": True,
    }

@pytest.mark.p1
@pytest.mark.unit
class TestModuleLoadErrorComprehensive:
    """ModuleLoadError 异常类"""

    def test_module_load_error_creation(self):
        """测试异常创建"""
        original_error = ValueError("Test error")
        error = ModuleLoadError('test_module', original_error)

        assert error.module_name == 'test_module'
        assert error.error is original_error
        assert str(error).startswith('模块')

    def test_module_load_error_with_long_message(self):
        """测试长错误消息的处理"""
        long_error = Exception('x' * 200)
        error = ModuleLoadError('my_module', long_error)

        str_repr = str(error)
        assert 'my_module' in str_repr
        assert str_repr.startswith('模块')

@pytest.mark.p1
@pytest.mark.unit
class TestSafeImportComprehensive:
    """安全导入函数分支"""

    def test_safe_import_debug_logging(self, caplog):
        """测试调试日志"""
        with caplog.at_level(logging.DEBUG):
            _safe_import('test_debug', lambda: __import__('math'), None)

        assert '[模块导入] 📦 开始导入模块' in caplog.text
        assert '[模块导入] [OK] [成功]' in caplog.text

    def test_safe_import_warning_logging_on_import_error(self, caplog):
        """测试导入失败时的警告日志"""
        with caplog.at_level(logging.WARNING):
            _safe_import('nonexistent_xyz', lambda: __import__('nonexistent_xyz'), None)

        assert '[WARN] [警告]' in caplog.text
        assert 'nonexistent_xyz' in caplog.text

    def test_safe_import_error_logging_on_exception(self, caplog):
        """测试异常时的错误日志"""
        def raising_func():
            raise RuntimeError("Critical error")

        with caplog.at_level(logging.ERROR):
            _safe_import('error_module', raising_func, None)

        assert '[FAIL] [错误]' in caplog.text
        assert 'RuntimeError' in caplog.text

    def test_safe_import_success_with_fallback(self):
        """测试安全导入成功（带fallback）"""
        module, success = _safe_import(
            'test_module', lambda: __import__('math'), 'fallback_value'
        )
        assert success is True
        assert module is not None
        assert hasattr(module, 'sqrt')

    def test_safe_import_import_error(self):
        """测试安全导入 ImportError 分支"""
        module, success = _safe_import(
            'nonexistent_module_xyz_123', lambda: __import__('nonexistent_module_xyz_123'), None
        )
        assert success is False
        assert module is None

    def test_safe_import_general_exception(self):
        """测试安全导入通用异常分支"""
        def raising_func():
            raise ValueError("Test exception")

        module, success = _safe_import('error_module', raising_func, 'fallback')
        assert success is False
        assert module == 'fallback'

@pytest.mark.p1
@pytest.mark.unit
class TestSafeImportFromComprehensive:
    """从包导入函数分支"""

    def test_safe_import_from_attribute_error(self, caplog):
        """测试属性错误分支"""
        with caplog.at_level(logging.WARNING):
            result, success = _safe_import_from('math', 'sqrt', 'non_existent_func')

        assert success is False
        assert result['sqrt'] is not None
        assert result['non_existent_func'] is None
        assert '不存在名称' in caplog.text

    def test_safe_import_from_import_error(self, caplog):
        """测试导入错误分支"""
        with caplog.at_level(logging.WARNING):
            result, success = _safe_import_from('nonexistent_package_xyz', 'func1', 'func2')

        assert success is False
        assert all(v is None for v in result.values())

    def test_safe_import_from_general_exception(self, caplog):
        """测试通用异常分支"""
        original_import = __import__
        def mock_import(name, *args, **kwargs):
            if name == 'math':
                raise RuntimeError("Test exception")
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=mock_import):
            with caplog.at_level(logging.ERROR):
                result, success = _safe_import_from('math', 'sqrt')

        assert success is False
        assert '[FAIL] [错误]' in caplog.text

    def test_safe_import_from_success_all(self):
        """测试从包导入全部成功"""
        result, success = _safe_import_from('math', 'sqrt', 'pi', 'sin')
        assert success is True
        assert result['sqrt'] is not None
        assert result['pi'] is not None
        assert result['sin'] is not None

    def test_safe_import_from_partial_success(self):
        """测试从包导入部分成功"""
        result, success = _safe_import_from('math', 'sqrt', 'nonexistent_func_xyz')
        assert success is False
        assert result['sqrt'] is not None
        assert result['nonexistent_func_xyz'] is None

@pytest.mark.p1
@pytest.mark.unit
class TestModuleImportStatusReport:
    """模块导入状态报告函数"""

    def test_report_module_import_status_logging(self, caplog):
        """测试报告函数的日志输出"""
        with caplog.at_level(logging.INFO):
            _report_module_import_status()

        assert "模块导入状态汇总" in caplog.text
        assert "已加载" in caplog.text or "未加载" in caplog.text

    def test_report_module_import_status_exists(self):
        """测试报告函数存在"""
        assert callable(_report_module_import_status)

    def test_module_import_results_structure(self):
        """测试模块导入结果字典结构"""
        assert isinstance(_module_import_results, dict)
        expected_modules = ['lifetrace', 'persona', 'planning', 'vector_memory',
                           'monitoring', 'voice', 'ocr', 'p6_snapshot']
        for module in expected_modules:
            assert module in _module_import_results
            assert isinstance(_module_import_results[module], bool)

@pytest.mark.p1
@pytest.mark.unit
class TestDigitalLifeInitializationComprehensive:
    """DigitalLife 初始化分支"""

    def test_configure_v2_features_all_disabled(self):
        """测试所有 V2 功能禁用"""
        config = {
            'features': {
                'v2_lifetrace': False,
                'v2_persona': False,
                'v2_distillation': False,
            }
        }

        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'):

            digital_life = DigitalLife(config=config)

            assert digital_life._v2_lifetrace is False
            assert digital_life._v2_persona is False
            assert digital_life._v2_distillation is False

    def test_configure_v2_features_warnings(self, caplog):
        """测试 V2 功能不可用时的警告日志"""
        config = {
            'features': {
                'v2_lifetrace': True,
                'v2_persona': True,
                'v2_distillation': True,
            }
        }

        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'), \
             patch('agent.orchestrator.lifecycle_manager._LIFETRACE_AVAILABLE', False), \
             patch('agent.orchestrator.lifecycle_manager._PERSONA_AVAILABLE', False):

            with caplog.at_level(logging.WARNING):
                digital_life = DigitalLife(config=config)

            assert "[WARN]" in caplog.text
            assert "v2_lifetrace" in caplog.text or "v2_persona" in caplog.text

    def test_module_availability_check(self, caplog):
        """测试模块可用性检查"""
        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'):

            with caplog.at_level(logging.INFO):
                digital_life = DigitalLife()

            assert "模块可用性检查" in caplog.text

    def test_empty_config_initialization(self):
        """测试空配置初始化"""
        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'):

            digital_life = DigitalLife(config={})

            assert digital_life._config == {}
            assert digital_life._v2_lifetrace is False

    def test_default_health_check_interval(self):
        """测试默认健康检查间隔"""
        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'):

            digital_life = DigitalLife()

            assert digital_life._health_check_interval == 30

    def test_init_with_sensor_config(self):
        """测试带传感器配置的初始化"""
        config = {
            'sensor': {
                'watch_dirs': ['./data'],
                'enable_change_detection': True,
                'enable_event_monitor': False
            }
        }

        with patch('agent.orchestrator.lifecycle_manager.BodySensor') as mock_body, \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'):

            digital_life = DigitalLife(config=config)
            mock_body.assert_called_once()
            call_kwargs = mock_body.call_args
            assert call_kwargs[1]['enable_event_monitor'] is False

    def test_init_with_behavior_config(self):
        """测试带行为配置的初始化"""
        config = {
            'behavior': {
                'check_interval': 60
            }
        }

        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'):

            digital_life = DigitalLife(config=config)
            assert digital_life._health_check_interval == 60

    def test_init_with_default_config(self):
        """测试使用默认配置初始化 DigitalLife"""
        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'):

            digital_life = DigitalLife()
            assert digital_life is not None
            assert hasattr(digital_life, '_config')

    def test_init_with_custom_config(self):
        """测试使用自定义配置初始化"""
        custom_config = {
            'theme': 'dark',
            'language': 'zh-CN',
            'log_level': 'DEBUG',
            'features': {
                'v2_lifetrace': False,
                'v2_persona': False,
                'v2_distillation': False
            }
        }

        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'):

            digital_life = DigitalLife(config=custom_config)
            assert digital_life is not None
            assert digital_life._config['theme'] == 'dark'

    def test_init_with_v2_features_requested_but_unavailable(self):
        """测试请求 V2 功能但模块不可用"""
        config = {
            'features': {
                'v2_lifetrace': True,
                'v2_persona': True
            }
        }

        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'), \
             patch('agent.orchestrator.lifecycle_manager._LIFETRACE_AVAILABLE', False), \
             patch('agent.orchestrator.lifecycle_manager._PERSONA_AVAILABLE', False):

            digital_life = DigitalLife(config=config)
            assert digital_life._v2_lifetrace is False
            assert digital_life._v2_persona is False

    def test_init_planning_disabled(self):
        """测试规划引擎禁用"""
        config = {'planning': {'enabled': False}}

        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'), \
             patch('agent.orchestrator.lifecycle_manager._PLANNING_AVAILABLE', True):

            digital_life = DigitalLife(config=config)
            assert digital_life._planning_enabled is False

@pytest.mark.p1
@pytest.mark.unit
class TestDigitalLifePlanningEngine:
    """规划引擎初始化"""

    def test_planning_engine_available(self):
        """测试规划引擎可用时初始化"""
        config = {'planning': {'enabled': True, 'max_iterations': 20}}

        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager') as mock_memory, \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'), \
             patch('agent.orchestrator.lifecycle_manager._PLANNING_AVAILABLE', True), \
             patch('agent.orchestrator.lifecycle_manager.ToolRegistry'), \
             patch('agent.orchestrator.lifecycle_manager.PlanningCore'), \
             patch('agent.orchestrator.lifecycle_manager.ReActLoop'):

            mock_memory.return_value._llm_service = MagicMock()
            digital_life = DigitalLife(config=config)

            assert digital_life._planning_enabled is True

    def test_planning_engine_disabled_in_config(self):
        """测试配置中禁用规划引擎"""
        config = {'planning': {'enabled': False}}

        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'), \
             patch('agent.orchestrator.lifecycle_manager._PLANNING_AVAILABLE', True):

            digital_life = DigitalLife(config=config)
            assert digital_life._planning_enabled is False

    def test_planning_engine_unavailable(self):
        """测试规划引擎不可用"""
        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'), \
             patch('agent.orchestrator.lifecycle_manager._PLANNING_AVAILABLE', False):

            digital_life = DigitalLife()

            assert digital_life._planner is None
            assert digital_life._react_loop is None
            assert digital_life._planning_enabled is False

@pytest.mark.p1
@pytest.mark.unit
class TestDigitalLifeLazyLoading:
    """懒加载机制"""

    def test_ensure_lifetrace_disabled(self):
        """测试 Lifetrace 禁用时返回 False"""
        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'):

            digital_life = DigitalLife()
            digital_life._v2_lifetrace = False

            result = digital_life._ensure_lifetrace()
            assert result is False

    def test_ensure_lifetrace_already_initialized(self):
        """测试 Lifetrace 已初始化时返回 True"""
        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'):

            digital_life = DigitalLife()
            digital_life._v2_lifetrace = True
            digital_life._lifetrace_initialized = True

            result = digital_life._ensure_lifetrace()
            assert result is True

@pytest.mark.p1
@pytest.mark.unit
class TestDigitalLifeOptionalSystemsComprehensive:
    """可选系统分支"""

    def test_ocr_sensor_initialization(self):
        """测试 OCR 传感器初始化"""
        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'), \
             patch('agent.orchestrator.lifecycle_manager._OCR_AVAILABLE', True), \
             patch('agent.orchestrator.lifecycle_manager.OcrSensor') as mock_ocr:

            DigitalLife()
            mock_ocr.assert_called_once()

    def test_ocr_sensor_failure(self):
        """测试 OCR 传感器初始化失败"""
        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'), \
             patch('agent.orchestrator.lifecycle_manager._OCR_AVAILABLE', True), \
             patch('agent.orchestrator.lifecycle_manager.OcrSensor') as mock_ocr:

            mock_ocr.side_effect = Exception("OCR engine not found")
            digital_life = DigitalLife()

            assert digital_life._ocr_sensor is None




    def test_vector_memory_initialization_failure(self):
        """测试向量记忆初始化失败"""
        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'), \
             patch('agent.orchestrator.lifecycle_manager._MEMORY_AVAILABLE', True), \
             patch('agent.orchestrator.lifecycle_manager.VectorStore') as mock_vector_store:

            mock_vector_store.side_effect = Exception("Connection failed")
            digital_life = DigitalLife()

            assert digital_life._vector_memory is None
            assert digital_life._knowledge_base is None

    def test_error_reporting_initialization(self):
        """测试错误上报系统初始化"""
        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'), \
             patch('agent.orchestrator.lifecycle_manager._MONITORING_AVAILABLE', True), \
             patch('agent.orchestrator.lifecycle_manager.get_error_reporter') as mock_reporter:

            DigitalLife()
            mock_reporter.assert_called_once()

    def test_voice_manager_initialization_failure(self):
        """测试语音管理器初始化失败"""
        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'), \
             patch('agent.orchestrator.lifecycle_manager._VOICE_AVAILABLE', True), \
             patch('agent.orchestrator.lifecycle_manager.VoiceManager') as mock_voice:

            mock_voice.side_effect = Exception("TTSEngine not found")
            digital_life = DigitalLife()

            assert digital_life._voice_manager is None

    def test_p6_snapshot_initialization(self):
        """测试 P6 快照管理器初始化"""
        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'), \
             patch('agent.orchestrator.lifecycle_manager._P6_SNAPSHOT_AVAILABLE', True), \
             patch('agent.orchestrator.lifecycle_manager.StateSnapshotManager') as mock_snapshot:

            DigitalLife()
            mock_snapshot.assert_called_once()

@pytest.mark.p1
@pytest.mark.unit
class TestDigitalLifeLazyLoadingFlags:
    """懒加载标志"""

    def test_lazy_loading_flags_initial_state(self):
        """测试懒加载标志初始状态"""
        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'):

            digital_life = DigitalLife()

            assert digital_life._lifetrace_initialized is False
            assert digital_life._persona_initialized is False
            assert digital_life._distillation_initialized is False

@pytest.mark.p1
@pytest.mark.unit
class TestDigitalLifeReflectionHistory:
    """反思历史功能"""

    def test_add_reflection(self):
        """测试添加反思记录"""
        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'):

            digital_life = DigitalLife()

            reflection = {
                'timestamp': datetime.now(),
                'content': 'Test reflection',
                'confidence': 0.8
            }
            digital_life._reflection_history.append(reflection)

            assert len(digital_life._reflection_history) == 1
            assert digital_life._reflection_history[0]['content'] == 'Test reflection'

    def test_interaction_count_initial(self):
        """测试交互计数初始值"""
        with patch('agent.orchestrator.lifecycle_manager.BodySensor'), \
             patch('agent.orchestrator.lifecycle_manager.BehaviorController'), \
             patch('agent.orchestrator.lifecycle_manager.PermissionSystem'), \
             patch('agent.orchestrator.lifecycle_manager.MemoryManager'), \
             patch('agent.orchestrator.lifecycle_manager.PromptInjector'), \
             patch('agent.orchestrator.lifecycle_manager.PromptConfig'), \
             patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'), \
             patch('agent.orchestrator.lifecycle_manager.tools'):

            digital_life = DigitalLife()
            assert digital_life._interaction_count == 0
            assert digital_life._reflection_history == []

@pytest.mark.p1
@pytest.mark.unit
class TestDigitalLifeConstantsAndDefaults:
    """常量和默认值"""

    def test_default_system_prompt_length(self):
        """测试默认系统提示长度"""
        assert len(DEFAULT_SYSTEM_PROMPT) > 100

    def test_default_system_prompt_exists(self):
        """测试默认系统提示存在"""
        assert DEFAULT_SYSTEM_PROMPT is not None
        assert isinstance(DEFAULT_SYSTEM_PROMPT, str)
        assert len(DEFAULT_SYSTEM_PROMPT) > 0

    def test_default_system_prompt_placeholders(self):
        """测试默认系统提示包含占位符"""
        content = DEFAULT_SYSTEM_PROMPT
        assert '{body_status}' in content
        assert '{mode_name}' in content
        assert '{mode_description}' in content
        assert '{memory_context}' in content

    def test_default_system_prompt_content(self):
        """测试默认系统提示内容关键词"""
        content = DEFAULT_SYSTEM_PROMPT
        assert '云枢' in content
        assert '数字生命体' in content
        assert '感知' in content
        assert '认知' in content
        assert '行动' in content

    def test_module_import_results_defaults(self):
        """测试模块导入结果默认值"""
        assert isinstance(_module_import_results, dict)
        assert 'lifetrace' in _module_import_results
        assert 'persona' in _module_import_results
        assert 'planning' in _module_import_results
        assert 'vector_memory' in _module_import_results
        assert 'monitoring' in _module_import_results
        assert 'voice' in _module_import_results
        assert 'ocr' in _module_import_results
        assert 'p6_snapshot' in _module_import_results

def _make_test_orch(**overrides):
    """创建带 mock 属性的测试 Orchestrator"""
    from agent.orchestrator.orchestrator import Orchestrator
    from agent.guardrails.input_guard import GuardAction, GuardResult
    from agent.guardrails.output_guard import OutputResult
    orch = Orchestrator.__new__(Orchestrator)
    defaults = {
        "_running": True,
        "_interaction_count": 1, "_last_context_warning": None, "_last_was_template": False,
        "_guardrails_input_guard": MagicMock(check=lambda x: GuardResult(GuardAction.ALLOW)),
        "_guardrails_output_guard": MagicMock(check=lambda x: OutputResult(filtered=x)),
        "_workflow_engine": MagicMock(try_match=lambda x: None),
        "_memory": MagicMock(),
        "_behavior": MockBehavior(can_execute=True),
        "_build_body_status": MagicMock(return_value="Body status"),
        "_build_reject_response": MagicMock(return_value="Request rejected"),
        "_call_llm": MagicMock(return_value="Response"),
        "_call_llm_v2": MagicMock(return_value="Response"),
        "_set_thinking_mode": MagicMock(),
        "_check_context_usage": MagicMock(return_value=None),
        "_v2_lifetrace": False, "_v2_distillation": False, "_v2_persona": False,
        "_vector_memory": None, "_trace_recorder": None,
        "_current_mode": MagicMock(value="test_mode"),
        "_persona_injector": None, "_persona_extractor": None,
        "_memory_token_limit": 4096,
        "_planning_enabled": False, "_planner": None, "_needs_planning": lambda x: False,
        "_is_skill_enabled": lambda x: False,
        "check_health": MagicMock(return_value=[]),
    }
    for k, v in defaults.items():
        setattr(orch, k, v)
    for k, v in overrides.items():
        setattr(orch, k, v)
    return orch


@pytest.mark.p1
@pytest.mark.unit
class TestProcessUserInput:
    """process() 统一对话链路（原 _process_user_input）"""

    def test_process_user_input_success(self):
        """测试正常处理用户输入"""
        memory = MagicMock()
        orch = _make_test_orch(_memory=memory, _call_llm=MagicMock(return_value="Response"))
        result = orch.process("Hello")
        assert result["success"] is True
        assert result["data"] == "Response"

    def test_process_user_input_rejected(self):
        """测试用户输入被拒绝"""
        memory = MagicMock()
        behavior = MockBehavior(can_execute=False, reject_reason="Content rejected")
        orch = _make_test_orch(
            _memory=memory, _behavior=behavior,
            _build_reject_response=MagicMock(return_value="Request rejected"),
        )
        result = orch.process("Malicious input")
        assert result["success"] is False
        assert "rejected" in result.get("msg", result.get("error", "")).lower()

    def test_process_user_input_with_vector_memory(self):
        """测试带向量记忆的处理"""
        memory = MockMemory()
        vm = MockVectorMemory()
        orch = _make_test_orch(_memory=memory, _vector_memory=vm,
                               _call_llm=MagicMock(return_value="Response"))
        result = orch.process("Test input")
        assert result["success"] is True

    def test_process_user_input_vector_memory_failure(self):
        """测试向量记忆保存失败时仍返回响应"""
        vm = MagicMock()
        vm.add.side_effect = Exception("Memory save failed")
        orch = _make_test_orch(_vector_memory=vm,
                               _call_llm=MagicMock(return_value="Response"))
        result = orch.process("Test input")
        assert result["success"] is True
        assert result["data"] == "Response"

    def test_process_user_input_with_reflection(self):
        """测试启用反思功能"""
        memory = MockMemory()
        behavior = MockBehavior(can_execute=True)
        behavior.profile.enable_reflection = True
        orch = _make_test_orch(
            _memory=memory, _behavior=behavior,
            _call_llm=MagicMock(return_value="Response"),
            self_reflect=MagicMock(),
        )
        result = orch.process("Test input")
        assert result["success"] is True

@pytest.mark.p1
@pytest.mark.unit
class TestBuildBodyStatus:
    """_build_body_status 方法"""

    def test_build_body_status_empty_readings(self):
        """测试空读数时返回默认状态"""
        digital_life = MagicMock(spec=DigitalLife)
        result = DigitalLife._build_body_status(digital_life, [])
        assert result == "我感觉很好，一切正常。"

    def test_build_body_status_with_readings(self):
        """测试有读数时构建状态"""
        digital_life = MagicMock(spec=DigitalLife)
        mock_reading = MagicMock()
        mock_reading.to_dict.return_value = {"type": "test", "value": 100}
        digital_life._injector = MagicMock()
        digital_life._injector.inject.return_value = "Injected status"
        behavior = MagicMock()
        behavior.profile.label = "Test Mode"
        behavior.profile.description = "Testing"
        behavior._reasons = []
        digital_life._behavior = behavior

        result = DigitalLife._build_body_status(digital_life, [mock_reading])

        assert "Injected status" in result
        assert "当前行为模式：Test Mode — Testing" in result

    def test_build_body_status_with_reasons(self):
        """测试带有触发原因的状态"""
        digital_life = MagicMock(spec=DigitalLife)
        mock_reading = MagicMock()
        mock_reading.to_dict.return_value = {"type": "test", "value": 100}
        digital_life._injector = MagicMock()
        digital_life._injector.inject.return_value = "Status"
        behavior = MagicMock()
        behavior.profile.label = "Mode"
        behavior.profile.description = "Desc"
        behavior._reasons = ["reason1", "reason2"]
        digital_life._behavior = behavior

        result = DigitalLife._build_body_status(digital_life, [mock_reading])

        assert "触发原因：reason1；reason2" in result

@pytest.mark.p1
@pytest.mark.unit
class TestChatV2Flow:
    """process() V2 对话流程（原 _chat_v2）"""

    def test_chat_v2_basic(self):
        """测试 V2 对话基本流程"""
        trace_recorder = MagicMock()
        orch = _make_test_orch(
            _v2_lifetrace=True, _trace_recorder=trace_recorder,
            _call_llm_v2=MagicMock(return_value="Response"),
        )
        result = orch.process("Hello")
        assert result["success"] is True
        assert result["data"] == "Response"

    def test_chat_v2_rejected(self):
        """测试 V2 对话被拒绝"""
        trace_recorder = MagicMock()
        behavior = MockBehavior(can_execute=False, reject_reason="Rejected")
        orch = _make_test_orch(
            _v2_lifetrace=True, _trace_recorder=trace_recorder,
            _behavior=behavior,
            _build_reject_response=MagicMock(return_value="Rejected response"),
        )
        result = orch.process("Hello")
        assert result["success"] is False

@pytest.mark.p1
@pytest.mark.unit
class TestChatMethodComplete:
    """chat() 完整流程（原 _chat_impl）"""

    def test_chat_not_running(self):
        """测试云枢未运行时返回提示"""
        orch = _make_test_orch(_running=False, _interaction_count=0)
        result = orch.chat("Hello")
        assert "唤醒" in result or "start" in result

    def test_chat_v2_lifetrace_enabled(self):
        """测试 V2 LifeTrace 流程"""
        trace_recorder = MagicMock()
        orch = _make_test_orch(
            _v2_lifetrace=True, _trace_recorder=trace_recorder,
            _call_llm_v2=MagicMock(return_value="V2 Response"),
        )
        result = orch.chat("Hello")
        assert result == "V2 Response"

    def test_chat_planning_mode_enabled(self):
        """测试 Planning 模式下对话"""
        orch = _make_test_orch(
            _v2_lifetrace=True, _trace_recorder=MagicMock(),
            _call_llm_v2=MagicMock(return_value="Planning Response"),
        )
        result = orch.chat("Hello")
        assert result == "Planning Response"

@pytest.mark.p1
@pytest.mark.unit
class TestCallLLMComplete:
    """_call_llm 完整场景"""

    def test_call_llm_success_with_response_prefix(self):
        """测试带响应前缀的 LLM 调用"""
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._behavior.profile.response_prefix = "[云枢]"
        mock_llm = MockLLM(response="Hello")
        digital_life._llm = mock_llm
        digital_life._vector_memory = None
        digital_life._select_model_for_request = MagicMock(return_value=(mock_llm, mock_llm.model))
        digital_life._tool_calling_service = None
        digital_life._get_enabled_tools_whitelist = MagicMock(return_value=[])
        digital_life._llm_pro = None
        digital_life._last_tool_steps = []

        result = DigitalLife._call_llm(digital_life, "Hi", "Body status")

        assert "[云枢]" in result
        assert "Hello" in result

    def test_call_llm_llm_service_error(self):
        """测试无 LLM 服务时使用离线响应"""
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._behavior.profile.response_prefix = ""
        digital_life._llm = None
        digital_life._build_offline_response = MagicMock(return_value="Offline response")

        result = DigitalLife._call_llm(digital_life, "Hello", "Body status")

        assert result == "Offline response"
        digital_life._build_offline_response.assert_called_once_with("Hello")

    def test_call_llm_no_llm_service(self):
        """测试无 LLM 服务时使用离线响应（无 LLM 属性）"""
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._llm = None
        digital_life._build_offline_response = MagicMock(return_value="Offline response")

        result = DigitalLife._call_llm(digital_life, "Hello", "Body status")

        assert result == "Offline response"
        digital_life._build_offline_response.assert_called_once_with("Hello")

    def test_call_llm_with_memory_context(self):
        """测试带记忆上下文的 LLM 调用（P12 后通过 _prompt_builder 访问记忆）"""
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._behavior.profile.response_prefix = ""
        mock_llm = MockLLM(response="Response")
        digital_life._llm = mock_llm
        digital_life._vector_memory = None
        digital_life._select_model_for_request = MagicMock(return_value=(mock_llm, mock_llm.model))
        digital_life._tool_calling_service = None
        digital_life._get_enabled_tools_whitelist = MagicMock(return_value=[])
        digital_life._llm_pro = None
        digital_life._last_tool_steps = []
        digital_life._memory = MagicMock()
        digital_life._prompt_builder = MagicMock()

        result = DigitalLife._call_llm(digital_life, "New question", "Body status")

        assert "Response" in result

    def test_call_llm_with_vector_memory_search(self):
        """测试带向量记忆配置的 LLM 调用（_call_llm 已不再搜索向量记忆）"""
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._behavior.profile.response_prefix = ""
        mock_llm = MockLLM(response="Response")
        digital_life._llm = mock_llm
        digital_life._select_model_for_request = MagicMock(return_value=(mock_llm, mock_llm.model))
        digital_life._tool_calling_service = None
        digital_life._get_enabled_tools_whitelist = MagicMock(return_value=[])
        digital_life._llm_pro = None
        digital_life._last_tool_steps = []
        digital_life._vector_memory = MagicMock()
        digital_life._memory = MockMemory()

        result = DigitalLife._call_llm(digital_life, "Question", "Body status")
        assert "Response" in result

    def test_call_llm_vector_memory_search_failure(self):
        """测试向量记忆配置异常时仍返回响应"""
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._behavior.profile.response_prefix = ""
        mock_llm = MockLLM(response="Response")
        digital_life._llm = mock_llm
        digital_life._select_model_for_request = MagicMock(return_value=(mock_llm, mock_llm.model))
        digital_life._tool_calling_service = None
        digital_life._get_enabled_tools_whitelist = MagicMock(return_value=[])
        digital_life._llm_pro = None
        digital_life._last_tool_steps = []
        digital_life._vector_memory = MagicMock()
        digital_life._memory = MockMemory()

        with patch('agent.orchestrator.lifecycle_manager.logger'):
            result = DigitalLife._call_llm(digital_life, "Question", "Body status")
        assert "Response" in result

    def test_call_llm_memory_context_failure(self):
        """测试记忆上下文获取失败时仍返回响应"""
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._behavior.profile.response_prefix = ""
        mock_llm = MockLLM(response="Response")
        digital_life._llm = mock_llm
        digital_life._vector_memory = None
        digital_life._select_model_for_request = MagicMock(return_value=(mock_llm, mock_llm.model))
        digital_life._tool_calling_service = None
        digital_life._get_enabled_tools_whitelist = MagicMock(return_value=[])
        digital_life._llm_pro = None
        digital_life._last_tool_steps = []
        mock_memory = MagicMock()
        mock_memory.get_context.side_effect = Exception("Memory error")
        digital_life._memory = mock_memory

        with patch('agent.orchestrator.lifecycle_manager.logger'):
            result = DigitalLife._call_llm(digital_life, "Question", "Body status")
        assert "Response" in result

@pytest.mark.p1
@pytest.mark.unit
class TestCallLLMV2:
    """_call_llm_v2 方法"""

    def test_call_llm_v2_with_persona(self):
        """测试带 Persona 的 V2 调用"""
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._behavior.profile.response_prefix = ""
        digital_life._v2_persona = True
        mock_persona_injector = MagicMock()
        mock_persona_injector.build_system_prompt.return_value = "Persona prompt"
        digital_life._persona_injector = mock_persona_injector
        mock_lifetrace = MagicMock()
        mock_lifetrace.return_value = "Lifetrace context"
        digital_life._get_lifetrace_context = mock_lifetrace
        mock_llm = MockLLM(response="V2 Response")
        digital_life._llm = mock_llm
        digital_life._memory = MockMemory()
        digital_life._select_model_for_request = MagicMock(return_value=(mock_llm, mock_llm.model))
        digital_life._tool_calling_service = None

        result = DigitalLife._call_llm_v2(digital_life, "Hello", "Body status")

        assert result == "V2 Response"
        mock_persona_injector.build_system_prompt.assert_called_once()

    def test_call_llm_v2_without_persona(self):
        """测试无 Persona 的 V2 调用"""
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Test"
        digital_life._behavior.profile.description = "Test mode"
        digital_life._behavior.profile.response_prefix = ""
        digital_life._v2_persona = False
        digital_life._v2_lifetrace = False
        digital_life._persona_injector = None
        mock_llm = MockLLM(response="V2 Response")
        digital_life._llm = mock_llm
        digital_life._memory = MockMemory()
        digital_life._select_model_for_request = MagicMock(return_value=(mock_llm, mock_llm.model))
        digital_life._tool_calling_service = None

        result = DigitalLife._call_llm_v2(digital_life, "Hello", "Body status")

        assert result == "V2 Response"

@pytest.mark.p1
@pytest.mark.unit
class TestNeedsPlanning:
    """规划需求判断"""

    def test_needs_planning_complex_keywords(self):
        """测试复杂关键词触发规划"""
        digital_life = MagicMock()
        digital_life._complexity_threshold = 0.5
        complex_input = "请帮我分析这个问题并制定一个详细的计划来解决"

        with patch('agent.orchestrator.lifecycle_manager.logger'):
            result = DigitalLife._needs_planning(digital_life, complex_input)

        assert result is True

    def test_needs_planning_simple_input(self):
        """测试简单输入不触发规划"""
        digital_life = MagicMock()
        digital_life._complexity_threshold = 0.5
        simple_input = "你好"

        with patch('agent.orchestrator.lifecycle_manager.logger'):
            result = DigitalLife._needs_planning(digital_life, simple_input)

        assert result is False

    def test_needs_planning_action_keywords(self):
        """测试动作关键词触发规划"""
        digital_life = MagicMock()
        digital_life._complexity_threshold = 0.5
        action_input = "请检查系统状态并创建报告"

        with patch('agent.orchestrator.lifecycle_manager.logger'):
            result = DigitalLife._needs_planning(digital_life, action_input)

        assert result is True

@pytest.mark.p1
@pytest.mark.unit
class TestBuildOfflineResponse:
    """离线响应构建"""

    def test_build_offline_response(self):
        """离线响应构建"""
        digital_life = MagicMock()
        digital_life._behavior = MagicMock()
        digital_life._behavior.profile.label = "Default"

        result = DigitalLife._build_offline_response(digital_life, "Hello")

        assert result is not None
        assert len(result) > 0

@pytest.mark.p1
@pytest.mark.unit
class TestSafetyMonitorIntegration:
    """安全监控器集成"""

    def test_safety_monitor_initialized(self, minimal_config):
        """测试安全监控器初始化"""
        with patch('agent.orchestrator.lifecycle_manager.MemoryManager'):
            with patch('agent.orchestrator.lifecycle_manager.get_safety_monitor') as mock_safety:
                mock_safety.return_value = MagicMock()
                dl = DigitalLife(config=minimal_config)

                assert hasattr(dl, '_safety_monitor')
                assert dl._safety_monitor is not None

    def test_safety_monitor_not_available(self, minimal_config):
        """测试安全监控器不可用时的降级处理"""
        with patch('agent.orchestrator.lifecycle_manager.MemoryManager'):
            with patch('agent.orchestrator.lifecycle_manager.get_safety_monitor', return_value=None):
                dl = DigitalLife(config=minimal_config)

                assert not hasattr(dl, '_safety_monitor') or dl._safety_monitor is None

    def test_safety_monitor_check_text(self, minimal_config):
        """测试安全文本检查功能"""
        with patch('agent.orchestrator.lifecycle_manager.MemoryManager'):
            with patch('agent.orchestrator.lifecycle_manager.get_safety_monitor') as mock_safety:
                mock_monitor = MagicMock()
                mock_monitor.check_text.return_value = {"level": "safe"}
                mock_safety.return_value = mock_monitor

                dl = DigitalLife(config=minimal_config)
                result = dl._safety_monitor.check_text("测试内容")

                assert result["level"] == "safe"
                mock_monitor.check_text.assert_called_once_with("测试内容")

    def test_safety_monitor_check_critical(self, minimal_config):
        """测试检测到危险内容"""
        with patch('agent.orchestrator.lifecycle_manager.MemoryManager'):
            with patch('agent.orchestrator.lifecycle_manager.get_safety_monitor') as mock_safety:
                mock_monitor = MagicMock()
                mock_monitor.check_text.return_value = {
                    "level": "critical",
                    "matches": [{"description": "危险内容"}]
                }
                mock_safety.return_value = mock_monitor

                dl = DigitalLife(config=minimal_config)
                result = dl._safety_monitor.check_text("危险内容")

                assert result["level"] == "critical"

@pytest.mark.p1
@pytest.mark.unit
class TestToolRegistration:
    """工具注册逻辑"""

    def test_register_builtin_tools_called(self, minimal_config):
        """测试内置工具注册方法被调用"""
        with patch('agent.orchestrator.lifecycle_manager.MemoryManager'):
            with patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'):
                with patch.object(DigitalLife, '_register_builtin_tools') as mock_register:
                    dl = DigitalLife(config=minimal_config)
                    mock_register.assert_called_once()

    def test_planning_tools_registration(self, minimal_config):
        """测试规划工具注册"""
        minimal_config["enable_planning"] = True

        with patch('agent.orchestrator.lifecycle_manager.MemoryManager'):
            with patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'):
                with patch('agent.orchestrator.lifecycle_manager.PlanningCore'):
                    dl = DigitalLife(config=minimal_config)

                    assert hasattr(dl, '_planning_tools')

    def test_planning_tool_registration_failure(self, minimal_config):
        """测试规划工具注册失败的处理"""
        minimal_config["enable_planning"] = True

        with patch('agent.orchestrator.lifecycle_manager.MemoryManager'):
            with patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'):
                with patch('agent.orchestrator.lifecycle_manager.ToolRegistry') as mock_registry:
                    mock_registry.return_value = None
                    dl = DigitalLife(config=minimal_config)

                    assert dl._planning_tools is None

@pytest.mark.p1
@pytest.mark.unit
class TestPermissionSystemIntegration:
    """权限系统集成"""

    def test_permission_system_initialized(self, minimal_config):
        """测试权限系统初始化"""
        with patch('agent.orchestrator.lifecycle_manager.MemoryManager'):
            with patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'):
                dl = DigitalLife(config=minimal_config)

                assert hasattr(dl, '_permission')
                assert dl._permission is not None

    def test_permission_check_action(self, minimal_config):
        """测试权限检查操作"""
        with patch('agent.orchestrator.lifecycle_manager.MemoryManager'):
            with patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'):
                dl = DigitalLife(config=minimal_config)

                mock_perm = Mock()
                mock_perm.allowed = True
                dl._permission.check_action = Mock(return_value=mock_perm)

                result = dl._permission.check_action("test_action", "测试操作")

                assert result.allowed is True

@pytest.mark.p1
@pytest.mark.unit
class TestSecurityMonitoring:
    """安全监控功能"""

    def test_check_health_method(self, minimal_config):
        """测试检查健康状态方法"""
        with patch('agent.orchestrator.lifecycle_manager.MemoryManager'):
            with patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'):
                dl = DigitalLife(config=minimal_config)

                result = dl.check_health()
                assert result is not None
                assert isinstance(result, list)

    def test_get_status_method(self, minimal_config):
        """测试获取状态方法"""
        with patch('agent.orchestrator.lifecycle_manager.MemoryManager'):
            with patch('agent.orchestrator.lifecycle_manager.get_safety_monitor'):
                dl = DigitalLife(config=minimal_config)

                result = dl.get_status()
                assert result is not None
                assert isinstance(result, dict)
                assert "云枢" in result

if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
