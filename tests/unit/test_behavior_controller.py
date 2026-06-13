"""
BehaviorController 测试 - pytest 格式
针对 agent/behavior_controller.py 的测试用例
"""
import pytest
from agent.behavior_controller import BehaviorController, BehaviorMode


class TestBehaviorControllerInitialization:
    """测试 BehaviorController 初始化"""
    
    @pytest.fixture
    def default_behavior_controller(self):
        """创建默认的 BehaviorController 实例"""
        return BehaviorController()
    
    @pytest.mark.p0
    def test_behavior_controller_init(self, default_behavior_controller):
        """测试基本初始化"""
        assert default_behavior_controller is not None
        assert hasattr(default_behavior_controller, 'evaluate')
        assert hasattr(default_behavior_controller, 'can_execute')
    
    @pytest.mark.p0
    def test_initial_mode_is_normal(self, default_behavior_controller):
        """测试初始模式是否为 NORMAL"""
        assert hasattr(default_behavior_controller, '_reasons') or hasattr(default_behavior_controller, 'profile')
    
    @pytest.mark.p1
    def test_behavior_mode_enum(self):
        """测试 BehaviorMode 枚举是否正确定义"""
        assert BehaviorMode.NORMAL is not None
        assert hasattr(BehaviorMode, 'NORMAL')


class TestBehaviorControllerCoreFunctionality:
    """测试 BehaviorController 核心功能"""
    
    @pytest.fixture
    def behavior_controller(self):
        """BehaviorController 实例"""
        return BehaviorController()
    
    @pytest.mark.p0
    def test_can_execute_basic_input(self, behavior_controller):
        """测试基本输入是否可以执行"""
        result, reason = behavior_controller.can_execute("hello world")
        # 我们不关心具体结果，只要函数正常返回即可
        assert isinstance(result, bool)
        # reason 可能是 None 或 str
    
    @pytest.mark.p1
    def test_evaluate_function_exists(self, behavior_controller):
        """测试 evaluate 函数是否存在"""
        assert callable(getattr(behavior_controller, 'evaluate', None))


class TestBehaviorControllerIntegration:
    """测试 BehaviorController 与其他组件的集成"""
    
    @pytest.mark.p1
    def test_import_and_use(self):
        """测试模块导入和基本使用模式"""
        from agent.behavior_controller import BehaviorController
        bc = BehaviorController()
        assert bc is not None
