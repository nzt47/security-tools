"""DigitalLifeV2 懒加载初始化单元测试"""
import pytest
from unittest.mock import MagicMock, patch

from agent.digital_life_v2 import DigitalLifeV2


class TestEnsureLifetrace:
    """测试 _ensure_lifetrace 方法"""

    def test_ensure_lifetrace_first_call(self):
        """测试首次调用时初始化 LifeTrace"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    with patch('lifetrace.TraceRecorder') as mock_recorder:
                        with patch('lifetrace.MemoryRetriever') as mock_retriever:
                            dl = DigitalLifeV2()
                            
                            assert dl._lifetrace_initialized is False
                            
                            dl._ensure_lifetrace()
                            
                            assert dl._lifetrace_initialized is True
                            mock_recorder.assert_called_once()
                            mock_retriever.assert_called_once()
                            assert dl._trace_recorder is not None
                            assert dl._memory_retriever is not None

    def test_ensure_lifetrace_already_initialized(self):
        """测试已初始化时跳过重复初始化"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    with patch('lifetrace.TraceRecorder') as mock_recorder:
                        with patch('lifetrace.MemoryRetriever') as mock_retriever:
                            dl = DigitalLifeV2()
                            dl._lifetrace_initialized = True
                            
                            dl._ensure_lifetrace()
                            
                            mock_recorder.assert_not_called()
                            mock_retriever.assert_not_called()

    def test_ensure_lifetrace_with_custom_config(self):
        """测试使用自定义配置初始化 LifeTrace"""
        custom_config = {
            "lifetrace": {
                "data_dir": "./custom_lifetrace"
            }
        }
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    with patch('lifetrace.TraceRecorder') as mock_recorder:
                        with patch('lifetrace.MemoryRetriever'):
                            dl = DigitalLifeV2(config=custom_config)
                            
                            dl._ensure_lifetrace()
                            
                            mock_recorder.assert_called_once_with(data_dir="./custom_lifetrace")

    def test_ensure_lifetrace_import_failure(self):
        """测试模块导入失败时的处理"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    with patch.dict('sys.modules', {'lifetrace': None}):
                        dl = DigitalLifeV2()
                        
                        with pytest.raises(ImportError):
                            dl._ensure_lifetrace()


class TestEnsurePersona:
    """测试 _ensure_persona 方法"""

    def test_ensure_persona_first_call(self):
        """测试首次调用时初始化 Persona"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    with patch('persona.PersonaModel') as mock_model:
                        with patch('persona.PersonaInjector') as mock_injector:
                            with patch('persona.PersonalityPreferenceExtractor') as mock_extractor:
                                dl = DigitalLifeV2()
                                
                                assert dl._persona_initialized is False
                                
                                dl._ensure_persona()
                                
                                assert dl._persona_initialized is True
                                mock_model.assert_called_once()
                                mock_injector.assert_called_once()
                                mock_extractor.assert_called_once()
                                assert dl._persona_model is not None
                                assert dl._persona_injector is not None
                                assert dl._persona_extractor is not None

    def test_ensure_persona_already_initialized(self):
        """测试已初始化时跳过重复初始化"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    with patch('persona.PersonaModel') as mock_model:
                        with patch('persona.PersonaInjector') as mock_injector:
                            with patch('persona.PersonalityPreferenceExtractor') as mock_extractor:
                                dl = DigitalLifeV2()
                                dl._persona_initialized = True
                                
                                dl._ensure_persona()
                                
                                mock_model.assert_not_called()
                                mock_injector.assert_not_called()
                                mock_extractor.assert_not_called()

    def test_ensure_persona_with_distiller_enabled(self):
        """测试启用蒸馏器时的初始化"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    with patch('persona.PersonaModel'):
                        with patch('persona.PersonaInjector'):
                            with patch('persona.PersonalityPreferenceExtractor'):
                                with patch('persona.distiller.PersonaDistiller') as mock_distiller:
                                    with patch('persona.distiller.DistillationConfig'):
                                        with patch('persona.distiller.DistillationStrategy'):
                                            dl = DigitalLifeV2()
                                            dl._distiller_enabled = True
                                            
                                            dl._ensure_persona()
                                            
                                            mock_distiller.assert_called_once()
                                            assert dl._persona_distiller is not None

    def test_ensure_persona_with_custom_config(self):
        """测试使用自定义配置初始化 Persona"""
        custom_config = {
            "persona": {
                "persona_path": "./custom_persona.json"
            },
            "distillation": {
                "data_dir": "./custom_distillation"
            }
        }
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    with patch('persona.PersonaModel') as mock_model:
                        with patch('persona.PersonaInjector'):
                            with patch('persona.PersonalityPreferenceExtractor') as mock_extractor:
                                dl = DigitalLifeV2(config=custom_config)
                                
                                dl._ensure_persona()
                                
                                mock_model.assert_called_once_with(persona_path="./custom_persona.json")
                                mock_extractor.assert_called_once_with(data_dir="./custom_distillation")

    def test_ensure_persona_import_failure(self):
        """测试模块导入失败时的处理"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    with patch.dict('sys.modules', {'persona': None}):
                        dl = DigitalLifeV2()
                        
                        with pytest.raises(ImportError):
                            dl._ensure_persona()


class TestEnsureMemory:
    """测试 _ensure_memory 方法"""

    def test_ensure_memory_first_call(self):
        """测试首次调用时初始化 Memory"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    with patch('memory.MemoryManager') as mock_memory:
                        mock_llm = MagicMock()
                        mock_memory.return_value._llm_service = mock_llm
                        
                        dl = DigitalLifeV2()
                        
                        assert dl._memory_initialized is False
                        
                        dl._ensure_memory()
                        
                        assert dl._memory_initialized is True
                        mock_memory.assert_called_once()
                        assert dl._old_memory is not None
                        assert dl._llm == mock_llm

    def test_ensure_memory_already_initialized(self):
        """测试已初始化时跳过重复初始化"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    with patch('memory.MemoryManager') as mock_memory:
                        dl = DigitalLifeV2()
                        dl._memory_initialized = True
                        
                        dl._ensure_memory()
                        
                        mock_memory.assert_not_called()


class TestEnsureInjector:
    """测试 _ensure_injector 方法"""

    def test_ensure_injector_first_call(self):
        """测试首次调用时初始化 Injector"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    with patch('cognitive.PromptConfig'):
                        with patch('agent.digital_life_v2.OldPromptInjector') as mock_injector:
                            dl = DigitalLifeV2()
                            
                            assert dl._injector_initialized is False
                            
                            dl._ensure_injector()
                            
                            assert dl._injector_initialized is True
                            mock_injector.assert_called_once()
                            assert dl._old_injector is not None

    def test_ensure_injector_already_initialized(self):
        """测试已初始化时跳过重复初始化"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    with patch('agent.digital_life_v2.OldPromptInjector') as mock_injector:
                        dl = DigitalLifeV2()
                        dl._injector_initialized = True
                        
                        dl._ensure_injector()
                        
                        mock_injector.assert_not_called()
