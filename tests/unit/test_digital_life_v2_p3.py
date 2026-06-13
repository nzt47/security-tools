"""DigitalLifeV2 P3 并行初始化优化单元测试"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
import time

from agent.digital_life_v2_p3 import DigitalLifeV2


class TestParallelInit:
    """测试并行初始化功能"""

    def test_parallel_init_enabled_by_default(self):
        """测试并行初始化默认启用"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.PersonaModel'):
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController'):
                                        with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                            dl = DigitalLifeV2(enable_parallel_init=True)
                                            
                                            assert dl._perf_tracker is not None

    def test_parallel_init_disabled(self):
        """测试禁用并行初始化"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.PersonaModel'):
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController'):
                                        with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                            dl = DigitalLifeV2(enable_parallel_init=False)
                                            
                                            assert dl._perf_tracker is None

    def test_init_body_sensor(self):
        """测试 BodySensor 初始化"""
        with patch('agent.digital_life_v2_p3.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.PersonaModel'):
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController'):
                                        with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                            config = {
                                                "sensor": {
                                                    "watch_dirs": ["./test"],
                                                    "lazy_load": True
                                                }
                                            }
                                            
                                            dl = DigitalLifeV2(enable_parallel_init=False)
                                            dl._init_body_sensor(config)
                                            
                                            # 构造函数调用了一次，_init_body_sensor 又调用了一次
                                            assert mock_sensor.call_count >= 1
                                            assert dl.body is not None

    def test_init_trace_recorder(self):
        """测试 TraceRecorder 初始化"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder') as mock_recorder:
                with patch('agent.digital_life_v2_p3.PersonaModel'):
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController'):
                                        with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                            config = {
                                                "lifetrace": {
                                                    "data_dir": "./custom_lifetrace"
                                                }
                                            }
                                            
                                            dl = DigitalLifeV2(enable_parallel_init=False)
                                            dl._init_trace_recorder(config)
                                            
                                            assert mock_recorder.call_count >= 1
                                            assert dl._trace_recorder is not None

    def test_init_persona_model(self):
        """测试 PersonaModel 初始化"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.PersonaModel') as mock_model:
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController'):
                                        with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                            config = {
                                                "persona": {
                                                    "persona_path": "./custom_persona.json"
                                                }
                                            }
                                            
                                            dl = DigitalLifeV2(enable_parallel_init=False)
                                            dl._init_persona_model(config)
                                            
                                            assert mock_model.call_count >= 1
                                            assert dl._persona_model is not None

    def test_init_persona_extractor(self):
        """测试 PersonaExtractor 初始化"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.PersonaModel'):
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor') as mock_extractor:
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController'):
                                        with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                            config = {
                                                "distillation": {
                                                    "data_dir": "./custom_persona",
                                                    "enabled": True,
                                                    "interval": 20
                                                }
                                            }
                                            
                                            dl = DigitalLifeV2(enable_parallel_init=False)
                                            dl._init_persona_extractor(config)
                                            
                                            assert mock_extractor.call_count >= 1
                                            assert dl._distillation_enabled is True
                                            assert dl._distillation_interval == 20

    def test_init_persona_distiller(self):
        """测试 PersonaDistiller 初始化"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.PersonaModel') as mock_model:
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller') as mock_distiller:
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController'):
                                        with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                            config = {
                                                "distillation": {
                                                    "distiller_enabled": True,
                                                    "distiller": {
                                                        "strategy": "conservative",
                                                        "learning_rate": 0.05
                                                    }
                                                }
                                            }
                                            
                                            dl = DigitalLifeV2(enable_parallel_init=False)
                                            dl._persona_model = mock_model.return_value
                                            dl._init_persona_distiller(config)
                                            
                                            assert mock_distiller.call_count >= 1
                                            assert dl._distiller_enabled is True

    def test_init_old_memory(self):
        """测试旧版 MemoryManager 初始化"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.PersonaModel'):
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager') as mock_memory:
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController'):
                                        with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                            mock_llm = MagicMock()
                                            mock_memory.return_value._llm_service = mock_llm
                                            
                                            config = {
                                                "memory": {
                                                    "max_size": 1000
                                                }
                                            }
                                            
                                            dl = DigitalLifeV2(enable_parallel_init=False)
                                            dl._init_old_memory(config)
                                            
                                            assert mock_memory.call_count >= 1
                                            assert dl._llm == mock_llm

    def test_init_old_injector(self):
        """测试旧版 PromptInjector 初始化"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.PersonaModel'):
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector') as mock_injector:
                                    with patch('agent.digital_life_v2_p3.PromptConfig'):
                                        with patch('agent.digital_life_v2_p3.BehaviorController'):
                                            with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                                config = {
                                                    "cognitive": {
                                                        "config_path": "./cognitive.yaml"
                                                    }
                                                }
                                                
                                                dl = DigitalLifeV2(enable_parallel_init=False)
                                                dl._init_old_injector(config)
                                                
                                                assert mock_injector.call_count >= 1

    def test_init_behavior(self):
        """测试 BehaviorController 初始化"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.PersonaModel'):
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController') as mock_behavior:
                                        with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                            dl = DigitalLifeV2(enable_parallel_init=False)
                                            dl._init_behavior({})
                                            
                                            assert mock_behavior.call_count >= 1
                                            assert dl._behavior is not None

    def test_init_permission(self):
        """测试 PermissionSystem 初始化"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.PersonaModel'):
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController'):
                                        with patch('agent.digital_life_v2_p3.PermissionSystem') as mock_permission:
                                            config = {
                                                "backup_dir": "./custom_backups"
                                            }
                                            
                                            dl = DigitalLifeV2(enable_parallel_init=False)
                                            dl._init_permission(config)
                                            
                                            assert mock_permission.call_count >= 1


class TestLifecycle:
    """测试生命周期管理"""

    def test_start(self):
        """测试启动"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder') as mock_recorder:
                with patch('agent.digital_life_v2_p3.PersonaModel'):
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController') as mock_behavior:
                                        with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                            mock_body = MagicMock()
                                            mock_body.establish_baseline = MagicMock()
                                            
                                            dl = DigitalLifeV2(enable_parallel_init=False)
                                            dl.body = mock_body
                                            dl._trace_recorder = mock_recorder.return_value
                                            
                                            dl.start()
                                            
                                            assert dl._running is True
                                            assert dl._started_at is not None
                                            mock_body.establish_baseline.assert_called_once()
                                            mock_recorder.return_value.record_chat.assert_called()

    def test_stop(self):
        """测试停止"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder') as mock_recorder:
                with patch('agent.digital_life_v2_p3.PersonaModel'):
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController'):
                                        with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                            dl = DigitalLifeV2(enable_parallel_init=False)
                                            dl._running = True
                                            dl._trace_recorder = mock_recorder.return_value
                                            
                                            dl.stop()
                                            
                                            assert dl._running is False
                                            mock_recorder.return_value.record_chat.assert_called()

    def test_chat_not_running(self):
        """测试未运行时聊天返回提示"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.PersonaModel'):
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController'):
                                        with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                            dl = DigitalLifeV2(enable_parallel_init=False)
                                            dl._running = False
                                            
                                            result = dl.chat("Hello")
                                            
                                            assert "启动" in result or "start" in result.lower()


class TestCheckHealth:
    """测试健康检查"""

    def test_check_health_basic(self):
        """测试基本健康检查"""
        with patch('agent.digital_life_v2_p3.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2_p3.TraceRecorder') as mock_recorder:
                with patch('agent.digital_life_v2_p3.PersonaModel'):
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController') as mock_behavior:
                                        with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                            mock_reading = MagicMock()
                                            mock_reading.sensor_name = "test"
                                            mock_reading.value = 100
                                            mock_reading.unit = "%"
                                            mock_reading.severity = "normal"
                                            
                                            mock_sensor.return_value.collect_quick.return_value = [mock_reading]
                                            mock_behavior.return_value.evaluate.return_value = MagicMock(value="NORMAL")
                                            
                                            dl = DigitalLifeV2(enable_parallel_init=False)
                                            dl.body = mock_sensor.return_value
                                            dl._behavior = mock_behavior.return_value
                                            dl._trace_recorder = mock_recorder.return_value
                                            dl._current_mode = mock_behavior.return_value.evaluate().value
                                            
                                            readings = dl.check_health()
                                            
                                            assert len(readings) == 1


class TestConfigHandling:
    """测试配置处理"""

    def test_default_config(self):
        """测试默认配置"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.PersonaModel'):
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController'):
                                        with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                            dl = DigitalLifeV2()
                                            
                                            assert dl._health_check_interval == 30
                                            assert dl._session_id is not None
                                            assert dl._interaction_count == 0
                                            assert dl._reflection_history == []

    def test_custom_health_check_interval(self):
        """测试自定义健康检查间隔"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.PersonaModel'):
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController'):
                                        with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                            config = {
                                                "behavior": {
                                                    "check_interval": 60
                                                }
                                            }
                                            
                                            dl = DigitalLifeV2(config=config)
                                            
                                            assert dl._health_check_interval == 60

    def test_data_flow_enabled(self):
        """测试数据流启用配置"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.PersonaModel'):
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController'):
                                        with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                            config = {
                                                "data_flow": {
                                                    "enabled": True
                                                }
                                            }
                                            
                                            dl = DigitalLifeV2(config=config)
                                            
                                            assert dl._data_flow_enabled is True

    def test_data_flow_disabled(self):
        """测试数据流禁用配置"""
        with patch('agent.digital_life_v2_p3.BodySensor'):
            with patch('agent.digital_life_v2_p3.TraceRecorder'):
                with patch('agent.digital_life_v2_p3.PersonaModel'):
                    with patch('agent.digital_life_v2_p3.PersonalityPreferenceExtractor'):
                        with patch('agent.digital_life_v2_p3.PersonaDistiller'):
                            with patch('agent.digital_life_v2_p3.MemoryManager'):
                                with patch('agent.digital_life_v2_p3.OldPromptInjector'):
                                    with patch('agent.digital_life_v2_p3.BehaviorController'):
                                        with patch('agent.digital_life_v2_p3.PermissionSystem'):
                                            config = {
                                                "data_flow": {
                                                    "enabled": False
                                                }
                                            }
                                            
                                            dl = DigitalLifeV2(config=config)
                                            
                                            assert dl._data_flow_enabled is False
