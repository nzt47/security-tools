"""DigitalLife 懒加载版本单元测试"""
import pytest
import time
from unittest.mock import MagicMock, patch, PropertyMock

from agent.digital_life_lazy import LazyDigitalLife, PerformanceTimer, profile_load


class TestPerformanceTimer:
    """测试性能计时器"""

    def test_timer_basic(self):
        """测试基本计时功能"""
        with PerformanceTimer("test") as timer:
            time.sleep(0.01)
        
        assert timer.elapsed_ms > 0

    def test_timer_name(self):
        """测试计时器名称"""
        timer = PerformanceTimer("my_timer")
        assert timer.name == "my_timer"


class TestProfileLoadDecorator:
    """测试性能分析装饰器"""

    def test_profile_load_decorator(self):
        """测试装饰器功能"""
        @profile_load
        def test_func():
            time.sleep(0.01)
            return "result"
        
        result = test_func()
        assert result == "result"


class TestLazyDigitalLifeInit:
    """测试 LazyDigitalLife 初始化"""

    @patch('agent.digital_life_lazy.get_lazy_loader')
    @patch('agent.digital_life_lazy.get_sensor_health_monitor')
    def test_init_basic(self, mock_health, mock_loader):
        """测试基本初始化"""
        mock_loader.return_value.register = MagicMock()
        mock_loader.return_value.load_level = MagicMock()
        mock_loader.return_value.get_stats.return_value = {
            'successful_loads': 5,
            'failed_loads': 0,
            'avg_load_time_ms': 10.0
        }
        
        dl = LazyDigitalLife()
        
        assert dl._initialized is True
        assert dl._started is False
        mock_loader.return_value.register.assert_called()
        mock_loader.return_value.load_level.assert_called_once()

    @patch('agent.digital_life_lazy.get_lazy_loader')
    @patch('agent.digital_life_lazy.get_sensor_health_monitor')
    def test_init_with_config(self, mock_health, mock_loader):
        """测试带配置的初始化"""
        mock_loader.return_value.register = MagicMock()
        mock_loader.return_value.load_level = MagicMock()
        mock_loader.return_value.get_stats.return_value = {
            'successful_loads': 5,
            'failed_loads': 0,
            'avg_load_time_ms': 10.0
        }
        
        config = {"sensor": {"lazy_load": True}}
        dl = LazyDigitalLife(config=config)
        
        assert dl._config == config

    @patch('agent.digital_life_lazy.get_lazy_loader')
    @patch('agent.digital_life_lazy.get_sensor_health_monitor')
    def test_register_modules(self, mock_health, mock_loader):
        """测试模块注册"""
        mock_loader.return_value.register = MagicMock()
        mock_loader.return_value.load_level = MagicMock()
        mock_loader.return_value.get_stats.return_value = {
            'successful_loads': 5,
            'failed_loads': 0,
            'avg_load_time_ms': 10.0
        }
        mock_loader.return_value.modules = {}
        
        dl = LazyDigitalLife()
        
        # 验证注册了正确数量的模块
        assert mock_loader.return_value.register.call_count >= 10  # 至少注册了10个模块


class TestLazyLoadingLevels:
    """测试懒加载级别"""

    @patch('agent.digital_life_lazy.get_lazy_loader')
    @patch('agent.digital_life_lazy.get_sensor_health_monitor')
    def test_critical_modules_loaded_on_init(self, mock_health, mock_loader):
        """测试 Critical 模块在初始化时加载"""
        mock_loader.return_value.register = MagicMock()
        mock_loader.return_value.load_level = MagicMock()
        mock_loader.return_value.get_stats.return_value = {
            'successful_loads': 5,
            'failed_loads': 0,
            'avg_load_time_ms': 10.0
        }
        
        dl = LazyDigitalLife()
        
        from agent.lazy_loader import LoadLevel
        mock_loader.return_value.load_level.assert_called_once_with(LoadLevel.CRITICAL)

    @patch('agent.digital_life_lazy.get_lazy_loader')
    @patch('agent.digital_life_lazy.get_sensor_health_monitor')
    def test_important_modules_lazy_load(self, mock_health, mock_loader):
        """测试 Important 模块延迟加载"""
        mock_loader.return_value.register = MagicMock()
        mock_loader.return_value.load_level = MagicMock()
        mock_loader.return_value.get_stats.return_value = {
            'successful_loads': 5,
            'failed_loads': 0,
            'avg_load_time_ms': 10.0
        }
        
        dl = LazyDigitalLife()
        
        assert dl._important_loaded is False
        assert dl._important_loading is False
        
        # 触发第一次交互
        dl._ensure_important_loaded()
        
        assert dl._important_loading is True
        
        # 等待后台线程完成
        time.sleep(0.1)
        
        from agent.lazy_loader import LoadLevel
        mock_loader.return_value.load_level.assert_called_with(LoadLevel.IMPORTANT)

    @patch('agent.digital_life_lazy.get_lazy_loader')
    @patch('agent.digital_life_lazy.get_sensor_health_monitor')
    def test_optional_modules_on_demand(self, mock_health, mock_loader):
        """测试 Optional 模块按需加载"""
        mock_loader.return_value.register = MagicMock()
        mock_loader.return_value.load_level = MagicMock()
        mock_loader.return_value.get_stats.return_value = {
            'successful_loads': 5,
            'failed_loads': 0,
            'avg_load_time_ms': 10.0
        }
        mock_loader.return_value.should_load.return_value = True
        mock_loader.return_value.load.return_value = MagicMock()
        
        dl = LazyDigitalLife()
        
        result = dl._ensure_optional_loaded("lifetrace")
        
        assert result is True
        mock_loader.return_value.load.assert_called_once_with("lifetrace")

    @patch('agent.digital_life_lazy.get_lazy_loader')
    @patch('agent.digital_life_lazy.get_sensor_health_monitor')
    def test_optional_modules_already_loaded(self, mock_health, mock_loader):
        """测试已加载的 Optional 模块"""
        mock_loader.return_value.register = MagicMock()
        mock_loader.return_value.load_level = MagicMock()
        mock_loader.return_value.get_stats.return_value = {
            'successful_loads': 5,
            'failed_loads': 0,
            'avg_load_time_ms': 10.0
        }
        mock_loader.return_value.should_load.return_value = False
        mock_loader.return_value.is_loaded.return_value = True
        
        dl = LazyDigitalLife()
        
        result = dl._ensure_optional_loaded("lifetrace")
        
        assert result is True
        mock_loader.return_value.load.assert_not_called()


class TestSensorHealthMonitor:
    """测试传感器健康监控器"""

    @patch('agent.digital_life_lazy.get_lazy_loader')
    @patch('agent.digital_life_lazy.get_sensor_health_monitor')
    def test_init_sensor_health_monitor(self, mock_health, mock_loader):
        """测试传感器健康监控器初始化"""
        mock_loader.return_value.register = MagicMock()
        mock_loader.return_value.load_level = MagicMock()
        mock_loader.return_value.get_stats.return_value = {
            'successful_loads': 5,
            'failed_loads': 0,
            'avg_load_time_ms': 10.0
        }
        
        dl = LazyDigitalLife()
        
        mock_health.return_value.set_restart_callback.assert_called_once()

    @patch('agent.digital_life_lazy.get_lazy_loader')
    @patch('agent.digital_life_lazy.get_sensor_health_monitor')
    def test_sensor_failure_restart(self, mock_health, mock_loader):
        """测试传感器失败重启机制"""
        mock_loader.return_value.register = MagicMock()
        mock_loader.return_value.load_level = MagicMock()
        mock_loader.return_value.get_stats.return_value = {
            'successful_loads': 5,
            'failed_loads': 0,
            'avg_load_time_ms': 10.0
        }
        mock_loader.return_value.load_level.reset_mock()
        
        dl = LazyDigitalLife()
        dl._started = True
        
        # 获取回调函数并调用
        callback = mock_health.return_value.set_restart_callback.call_args[0][0]
        callback()
        
        # 验证 stop 被调用
        # 重启会在后台线程中执行，这里只验证 stop
        assert dl._started is False


class TestLifecycle:
    """测试生命周期管理"""

    @patch('agent.digital_life_lazy.get_lazy_loader')
    @patch('agent.digital_life_lazy.get_sensor_health_monitor')
    def test_start(self, mock_health, mock_loader):
        """测试启动"""
        mock_loader.return_value.register = MagicMock()
        mock_loader.return_value.load_level = MagicMock()
        mock_loader.return_value.get_stats.return_value = {
            'successful_loads': 5,
            'failed_loads': 0,
            'avg_load_time_ms': 10.0
        }
        
        dl = LazyDigitalLife()
        
        dl.start()
        
        assert dl._started is True

    @patch('agent.digital_life_lazy.get_lazy_loader')
    @patch('agent.digital_life_lazy.get_sensor_health_monitor')
    def test_stop(self, mock_health, mock_loader):
        """测试停止"""
        mock_loader.return_value.register = MagicMock()
        mock_loader.return_value.load_level = MagicMock()
        mock_loader.return_value.get_stats.return_value = {
            'successful_loads': 5,
            'failed_loads': 0,
            'avg_load_time_ms': 10.0
        }
        
        dl = LazyDigitalLife()
        dl._started = True
        
        dl.stop()
        
        assert dl._started is False

    @patch('agent.digital_life_lazy.get_lazy_loader')
    @patch('agent.digital_life_lazy.get_sensor_health_monitor')
    def test_quick_start(self, mock_health, mock_loader):
        """测试快速启动"""
        mock_loader.return_value.register = MagicMock()
        mock_loader.return_value.load_level = MagicMock()
        mock_loader.return_value.get_stats.return_value = {
            'successful_loads': 5,
            'failed_loads': 0,
            'avg_load_time_ms': 10.0
        }
        
        dl = LazyDigitalLife.quick_start()
        
        assert dl._started is True


class TestModuleDependencies:
    """测试模块依赖关系"""

    @patch('agent.digital_life_lazy.get_lazy_loader')
    @patch('agent.digital_life_lazy.get_sensor_health_monitor')
    def test_module_with_dependencies(self, mock_health, mock_loader):
        """测试带依赖的模块注册"""
        mock_loader.return_value.register = MagicMock()
        mock_loader.return_value.load_level = MagicMock()
        mock_loader.return_value.get_stats.return_value = {
            'successful_loads': 5,
            'failed_loads': 0,
            'avg_load_time_ms': 10.0
        }
        
        dl = LazyDigitalLife()
        
        # 验证依赖被正确传递
        calls = mock_loader.return_value.register.call_args_list
        for call in calls:
            args, kwargs = call
            module_name = args[0]
            if module_name in ["llm_service", "vector_memory"]:
                assert "dependencies" in kwargs
                assert "memory_manager" in kwargs["dependencies"]
            elif module_name == "persona":
                assert "dependencies" in kwargs
                assert "lifetrace" in kwargs["dependencies"]
