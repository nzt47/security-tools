"""
DigitalLife 扩展测试 - 覆盖更多未覆盖的分支
"""
import pytest
import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import patch, MagicMock, Mock

# 修复路径
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.digital_life import (
    DigitalLife, 
    ModuleLoadError, 
    _safe_import, 
    _safe_import_from, 
    _report_module_import_status,
    _module_import_results,
    DEFAULT_SYSTEM_PROMPT
)

@pytest.fixture
def temp_config_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir

@pytest.mark.p1
@pytest.mark.unit
class TestSafeImportExtended:
    """测试安全导入机制 - 覆盖更多分支"""
    
    def test_safe_import_success_with_fallback(self):
        """测试安全导入成功（带fallback）"""
        module, success = _safe_import(
            'test_module',
            lambda: __import__('math'),
            'fallback_value'
        )
        assert success is True
        assert module is not None
        assert hasattr(module, 'sqrt')
    
    def test_safe_import_import_error(self):
        """测试安全导入 ImportError 分支"""
        module, success = _safe_import(
            'nonexistent_module_xyz_123',
            lambda: __import__('nonexistent_module_xyz_123'),
            None
        )
        assert success is False
        assert module is None
    
    def test_safe_import_general_exception(self):
        """测试安全导入通用异常分支"""
        def raising_func():
            raise ValueError("Test exception")
        
        module, success = _safe_import(
            'error_module',
            raising_func,
            'fallback'
        )
        assert success is False
        assert module == 'fallback'
    
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
class TestModuleLoadErrorExtended:
    """测试模块加载错误处理"""
    
    def test_module_load_error_attributes(self):
        """测试错误属性"""
        original_error = ValueError("Missing dependency")
        error = ModuleLoadError('test_module', original_error)
        
        assert error.module_name == 'test_module'
        assert error.error is original_error
        assert 'test_module' in str(error)
        assert 'Missing dependency' in str(error)
    
    def test_module_load_error_str_format(self):
        """测试错误字符串格式"""
        error = ModuleLoadError('my_module', Exception('test error'))
        str_repr = str(error)
        assert str_repr.startswith('模块')
        assert 'my_module' in str_repr
        assert 'test error' in str_repr

@pytest.mark.p1
@pytest.mark.unit
class TestModuleImportStatusReport:
    """测试模块导入状态报告"""
    
    def test_report_module_import_status_exists(self):
        """测试报告函数存在"""
        assert callable(_report_module_import_status)
    
    def test_module_import_results_structure(self):
        """测试模块导入结果字典"""
        assert isinstance(_module_import_results, dict)
        
        expected_modules = ['lifetrace', 'persona', 'planning', 'vector_memory', 
                           'monitoring', 'voice', 'ocr', 'p6_snapshot']
        
        for module in expected_modules:
            assert module in _module_import_results
            assert isinstance(_module_import_results[module], bool)

@pytest.mark.p1
@pytest.mark.unit
class TestDefaultSystemPrompt:
    """测试默认系统提示"""
    
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
        """测试默认系统提示内容"""
        content = DEFAULT_SYSTEM_PROMPT
        assert '云枢' in content
        assert '数字生命体' in content
        assert '感知' in content
        assert '认知' in content
        assert '行动' in content

@pytest.mark.p1
@pytest.mark.unit
class TestDigitalLifeInitializationExtended:
    """测试 DigitalLife 初始化的更多分支"""
    
    def test_init_with_v2_features_enabled(self):
        """测试启用 V2 功能的初始化"""
        config = {
            'features': {
                'v2_lifetrace': False,
                'v2_persona': False,
                'v2_distillation': False
            }
        }
        
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'), \
             patch('agent.digital_life._LIFETRACE_AVAILABLE', False), \
             patch('agent.digital_life._PERSONA_AVAILABLE', False):
            
            digital_life = DigitalLife(config=config)
            assert digital_life is not None
            assert digital_life._v2_lifetrace is False
            assert digital_life._v2_persona is False
            assert digital_life._v2_distillation is False
    
    def test_init_with_v2_features_requested_but_unavailable(self):
        """测试请求启用 V2 功能但模块不可用"""
        config = {
            'features': {
                'v2_lifetrace': True,
                'v2_persona': True
            }
        }
        
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'), \
             patch('agent.digital_life._LIFETRACE_AVAILABLE', False), \
             patch('agent.digital_life._PERSONA_AVAILABLE', False):
            
            digital_life = DigitalLife(config=config)
            assert digital_life._v2_lifetrace is False
            assert digital_life._v2_persona is False
    
    def test_init_with_sensor_config(self):
        """测试带传感器配置的初始化"""
        config = {
            'sensor': {
                'watch_dirs': ['./data'],
                'enable_change_detection': True,
                'enable_event_monitor': False
            }
        }
        
        with patch('agent.digital_life.BodySensor') as mock_body, \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'):
            
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
        
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'):
            
            digital_life = DigitalLife(config=config)
            assert digital_life._health_check_interval == 60
    
    def test_init_planning_disabled(self):
        """测试规划引擎禁用的情况"""
        config = {
            'planning': {
                'enabled': False
            }
        }
        
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'), \
             patch('agent.digital_life._PLANNING_AVAILABLE', True):
            
            digital_life = DigitalLife(config=config)
            assert digital_life._planning_enabled is False

@pytest.mark.p1
@pytest.mark.unit
class TestDigitalLifeOptionalSystems:
    """测试可选系统初始化"""
    
    def test_vector_memory_initialization_failure(self):
        """测试向量记忆初始化失败"""
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'), \
             patch('agent.digital_life._MEMORY_AVAILABLE', True), \
             patch('agent.digital_life.VectorStore') as mock_vector_store:
            
            mock_vector_store.side_effect = Exception("Connection failed")
            
            digital_life = DigitalLife()
            assert digital_life._vector_memory is None
            assert digital_life._knowledge_base is None
    
    def test_error_reporting_initialization(self):
        """测试错误上报系统初始化"""
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'), \
             patch('agent.digital_life._MONITORING_AVAILABLE', True), \
             patch('agent.digital_life.get_error_reporter') as mock_reporter:
            
            digital_life = DigitalLife()
            mock_reporter.assert_called_once()
    
    def test_voice_manager_initialization_failure(self):
        """测试语音管理器初始化失败"""
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'), \
             patch('agent.digital_life._VOICE_AVAILABLE', True), \
             patch('agent.digital_life.VoiceManager') as mock_voice:
            
            mock_voice.side_effect = Exception("TTSEngine not found")
            
            digital_life = DigitalLife()
            assert digital_life._voice_manager is None
    
    def test_p6_snapshot_initialization(self):
        """测试 P6 快照管理器初始化"""
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'), \
             patch('agent.digital_life._P6_SNAPSHOT_AVAILABLE', True), \
             patch('agent.digital_life.StateSnapshotManager') as mock_snapshot:
            
            digital_life = DigitalLife()
            mock_snapshot.assert_called_once()

@pytest.mark.p1
@pytest.mark.unit
class TestDigitalLifeLazyLoading:
    """测试 P5 懒加载机制"""
    
    def test_ensure_lifetrace_disabled(self):
        """测试 Lifetrace 未启用时的懒加载"""
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'):
            
            digital_life = DigitalLife()
            digital_life._v2_lifetrace = False
            
            result = digital_life._ensure_lifetrace()
            assert result is False
    
    def test_ensure_lifetrace_already_initialized(self):
        """测试 Lifetrace 已初始化"""
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'):
            
            digital_life = DigitalLife()
            digital_life._v2_lifetrace = True
            digital_life._lifetrace_initialized = True
            
            result = digital_life._ensure_lifetrace()
            assert result is True

@pytest.mark.p1
@pytest.mark.unit
class TestDigitalLifeSessionManagement:
    """测试会话管理"""
    
    def test_session_id_format(self):
        """测试会话ID格式"""
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        assert len(session_id) == 15
        assert '_' in session_id
        assert session_id[:8].isdigit()
        assert session_id[9:].isdigit()
    
    def test_interaction_count_initial(self):
        """测试交互计数初始值"""
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'):
            
            digital_life = DigitalLife()
            assert digital_life._interaction_count == 0
            assert digital_life._reflection_history == []

if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
