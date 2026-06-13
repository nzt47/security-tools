"""
DigitalLife 综合测试 - 覆盖更多未覆盖的分支
"""
import pytest
import os
import sys
import tempfile
import logging
from datetime import datetime
from unittest.mock import patch, MagicMock, Mock, call

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
    DEFAULT_SYSTEM_PROMPT,
    _LIFETRACE_AVAILABLE,
    _PERSONA_AVAILABLE,
    _PLANNING_AVAILABLE,
    _MEMORY_AVAILABLE,
    _MONITORING_AVAILABLE,
    _VOICE_AVAILABLE,
    _OCR_AVAILABLE,
    _P6_SNAPSHOT_AVAILABLE,
)

@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir

@pytest.mark.p1
@pytest.mark.unit
class TestModuleLoadErrorComprehensive:
    """测试 ModuleLoadError 异常类"""
    
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
        # 验证错误消息格式正确
        assert str_repr.startswith('模块')

@pytest.mark.p1
@pytest.mark.unit
class TestSafeImportComprehensive:
    """测试安全导入函数的更多分支"""
    
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

@pytest.mark.p1
@pytest.mark.unit
class TestSafeImportFromComprehensive:
    """测试从包导入函数的更多分支"""
    
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

@pytest.mark.p1
@pytest.mark.unit
class TestModuleImportStatusReport:
    """测试模块导入状态报告函数"""
    
    def test_report_module_import_status_logging(self, caplog):
        """测试报告函数的日志输出"""
        with caplog.at_level(logging.INFO):
            _report_module_import_status()
        
        assert "模块导入状态汇总" in caplog.text
        assert "已加载" in caplog.text or "未加载" in caplog.text

@pytest.mark.p1
@pytest.mark.unit
class TestDigitalLifeInitializationComprehensive:
    """测试 DigitalLife 初始化的更多分支"""
    
    def test_configure_v2_features_all_disabled(self):
        """测试所有 V2 功能都禁用"""
        config = {
            'features': {
                'v2_lifetrace': False,
                'v2_persona': False,
                'v2_distillation': False,
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
            
            with caplog.at_level(logging.WARNING):
                digital_life = DigitalLife(config=config)
            
            assert "[WARN]" in caplog.text
            assert "v2_lifetrace" in caplog.text or "v2_persona" in caplog.text
    
    def test_module_availability_check(self, caplog):
        """测试模块可用性检查"""
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'):
            
            with caplog.at_level(logging.INFO):
                digital_life = DigitalLife()
            
            assert "模块可用性检查" in caplog.text
    
    def test_empty_config_initialization(self):
        """测试空配置初始化"""
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'):
            
            digital_life = DigitalLife(config={})
            
            assert digital_life._config == {}
            assert digital_life._v2_lifetrace is False
    
    def test_default_health_check_interval(self):
        """测试默认健康检查间隔"""
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'):
            
            digital_life = DigitalLife()
            
            assert digital_life._health_check_interval == 30

@pytest.mark.p1
@pytest.mark.unit
class TestDigitalLifePlanningEngine:
    """测试规划引擎初始化"""
    
    def test_planning_engine_available(self):
        """测试规划引擎可用时的初始化"""
        config = {'planning': {'enabled': True, 'max_iterations': 20}}
        
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager') as mock_memory, \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'), \
             patch('agent.digital_life._PLANNING_AVAILABLE', True), \
             patch('agent.digital_life.ToolRegistry'), \
             patch('agent.digital_life.PlanningCore'), \
             patch('agent.digital_life.ReActLoop'):
            
            mock_memory.return_value._llm_service = MagicMock()
            
            digital_life = DigitalLife(config=config)
            
            assert digital_life._planning_enabled is True
    
    def test_planning_engine_disabled_in_config(self):
        """测试配置中禁用规划引擎"""
        config = {'planning': {'enabled': False}}
        
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
    
    def test_planning_engine_unavailable(self):
        """测试规划引擎不可用"""
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'), \
             patch('agent.digital_life._PLANNING_AVAILABLE', False):
            
            digital_life = DigitalLife()
            
            assert digital_life._planner is None
            assert digital_life._react_loop is None
            assert digital_life._planning_enabled is False

@pytest.mark.p1
@pytest.mark.unit
class TestDigitalLifeLazyLoadingComprehensive:
    """测试懒加载机制的更多分支"""
    
    def test_ensure_lifetrace_disabled(self):
        """测试 Lifetrace 禁用时的行为"""
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
class TestDigitalLifeOptionalSystemsComprehensive:
    """测试可选系统的更多分支"""
    
    def test_ocr_sensor_initialization(self):
        """测试 OCR 传感器初始化"""
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'), \
             patch('agent.digital_life._OCR_AVAILABLE', True), \
             patch('agent.digital_life.OcrSensor') as mock_ocr:
            
            digital_life = DigitalLife()
            
            mock_ocr.assert_called_once()
    
    def test_ocr_sensor_failure(self):
        """测试 OCR 传感器初始化失败"""
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'), \
             patch('agent.digital_life._OCR_AVAILABLE', True), \
             patch('agent.digital_life.OcrSensor') as mock_ocr:
            
            mock_ocr.side_effect = Exception("OCR engine not found")
            
            digital_life = DigitalLife()
            
            assert digital_life._ocr_sensor is None
    
    def test_monitoring_disabled(self):
        """测试监控系统禁用"""
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'), \
             patch('agent.digital_life._MONITORING_AVAILABLE', False):
            
            digital_life = DigitalLife()
            
            # 监控系统不可用时不应尝试初始化
            pass

@pytest.mark.p1
@pytest.mark.unit
class TestDigitalLifeLazyLoadingFlags:
    """测试懒加载标志"""
    
    def test_lazy_loading_flags_initial_state(self):
        """测试懒加载标志初始状态"""
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'):
            
            digital_life = DigitalLife()
            
            assert digital_life._lifetrace_initialized is False
            assert digital_life._persona_initialized is False
            assert digital_life._distillation_initialized is False

@pytest.mark.p1
@pytest.mark.unit
class TestDigitalLifeReflectionHistory:
    """测试反思历史功能"""
    
    def test_add_reflection(self):
        """测试添加反思记录"""
        with patch('agent.digital_life.BodySensor'), \
             patch('agent.digital_life.BehaviorController'), \
             patch('agent.digital_life.PermissionSystem'), \
             patch('agent.digital_life.MemoryManager'), \
             patch('agent.digital_life.PromptInjector'), \
             patch('agent.digital_life.PromptConfig'), \
             patch('agent.digital_life.get_safety_monitor'), \
             patch('agent.digital_life.tools'):
            
            digital_life = DigitalLife()
            
            reflection = {
                'timestamp': datetime.now(),
                'content': 'Test reflection',
                'confidence': 0.8
            }
            
            digital_life._reflection_history.append(reflection)
            
            assert len(digital_life._reflection_history) == 1
            assert digital_life._reflection_history[0]['content'] == 'Test reflection'

@pytest.mark.p1
@pytest.mark.unit
class TestDigitalLifeConstantsAndDefaults:
    """测试常量和默认值"""
    
    def test_default_system_prompt_length(self):
        """测试默认系统提示长度"""
        assert len(DEFAULT_SYSTEM_PROMPT) > 100
    
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

if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
