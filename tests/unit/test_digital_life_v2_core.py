"""DigitalLifeV2 核心方法单元测试"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timezone

from agent.digital_life_v2 import DigitalLifeV2, LazyLoader


class TestLazyLoader:
    """测试懒加载辅助类"""

    def test_lazy_loader_init(self):
        """测试懒加载器初始化"""
        init_func = MagicMock(return_value="initialized")
        loader = LazyLoader(init_func, "test_module")
        
        assert loader._init_func == init_func
        assert loader._name == "test_module"
        assert loader._instance is None
        assert loader._initialized is False

    def test_lazy_loader_get_first_call(self):
        """测试首次获取时初始化"""
        init_func = MagicMock(return_value="initialized")
        loader = LazyLoader(init_func, "test_module")
        
        result = loader.get()
        
        assert result == "initialized"
        assert loader._initialized is True
        assert loader._instance == "initialized"
        init_func.assert_called_once()

    def test_lazy_loader_get_cached(self):
        """测试缓存机制"""
        init_func = MagicMock(return_value="initialized")
        loader = LazyLoader(init_func, "test_module")
        
        # 第一次调用
        result1 = loader.get()
        # 第二次调用（应该使用缓存）
        result2 = loader.get()
        
        assert result1 == result2 == "initialized"
        init_func.assert_called_once()  # 只调用一次


class TestDigitalLifeV2Initialization:
    """测试初始化"""

    def test_initialization_basic(self):
        """测试基本初始化"""
        with patch('agent.digital_life_v2.BodySensor') as mock_sensor:
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()

                    assert dl._running is False
                    assert dl._session_id is not None
                    assert dl._behavior is not None
                    assert dl._permission is not None
                    mock_sensor.assert_called_once()


class TestDigitalLifeV2Lifecycle:
    """测试生命周期管理"""

    def test_chat_not_running(self):
        """测试未运行时的聊天"""
        with patch('agent.digital_life_v2.BodySensor'):
            with patch('agent.digital_life_v2.BehaviorController'):
                with patch('agent.digital_life_v2.PermissionSystem'):
                    dl = DigitalLifeV2()

                    result = dl.chat("Hello")

                    assert "还没有被唤醒" in result or "start()" in result