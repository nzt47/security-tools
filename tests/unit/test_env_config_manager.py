"""EnvConfigManager 单元测试

测试覆盖：
1. get_env_config_manager() 单例工厂函数返回值验证
2. 单例一致性（多次调用返回同一实例）
3. 返回实例的功能完整性（set/get/delete 可用）
4. 回归测试：显式验证返回值不为 None（防止 return 缺失回归）

背景：commit f8a457f2 曾遗漏 return _instance，导致返回 None，
引发 9 个 CI 测试失败。本文件为该 bug 的专项回归测试。
"""

import os
import pytest
from unittest.mock import patch

from agent.env_config_manager import (
    EnvConfigManager,
    get_env_config_manager,
)


class TestGetEnvConfigManager:
    """get_env_config_manager() 单例工厂函数测试"""

    def test_returns_env_config_manager_instance(self):
        """返回值应为 EnvConfigManager 实例（不是 None）"""
        result = get_env_config_manager()
        assert result is not None, (
            "get_env_config_manager() 返回 None——检查是否缺少 return _instance"
        )
        assert isinstance(result, EnvConfigManager), (
            f"返回类型错误：期望 EnvConfigManager，实际 {type(result)}"
        )

    def test_returns_not_none(self):
        """回归测试：显式验证返回值不为 None

        此测试在 commit f8a457f2 引入的 bug 中会失败：
        get_env_config_manager() 缺少 return 语句，隐式返回 None。
        """
        result = get_env_config_manager()
        assert result is not None

    def test_singleton_returns_same_instance(self):
        """多次调用返回同一实例（单例模式）"""
        instance1 = get_env_config_manager()
        instance2 = get_env_config_manager()
        assert instance1 is instance2, (
            "单例模式失效：两次调用返回了不同实例"
        )

    def test_returned_instance_has_set_method(self):
        """返回的实例应具有可用的 set() 方法"""
        mgr = get_env_config_manager()
        assert hasattr(mgr, 'set'), "返回的实例缺少 set 方法"
        assert callable(mgr.set), "set 属性不可调用"

    def test_returned_instance_has_get_method(self):
        """返回的实例应具有可用的 get() 方法"""
        mgr = get_env_config_manager()
        assert hasattr(mgr, 'get'), "返回的实例缺少 get 方法"
        assert callable(mgr.get), "get 属性不可调用"

    def test_returned_instance_has_delete_method(self):
        """返回的实例应具有可用的 delete() 方法"""
        mgr = get_env_config_manager()
        assert hasattr(mgr, 'delete'), "返回的实例缺少 delete 方法"
        assert callable(mgr.delete), "delete 属性不可调用"

    def test_returned_instance_set_writes_environ(self):
        """返回的实例 set() 应正确写入 os.environ"""
        mgr = get_env_config_manager()
        test_key = 'TEST_ECM_RETURN_CHECK'
        test_value = 'test_value_12345'
        try:
            mgr.set(test_key, test_value)
            assert os.getenv(test_key) == test_value, (
                "set() 未正确写入 os.environ"
            )
        finally:
            os.environ.pop(test_key, None)

    def test_returned_instance_get_reads_environ(self):
        """返回的实例 get() 应从 os.environ 读取"""
        mgr = get_env_config_manager()
        test_key = 'TEST_ECM_GET_CHECK'
        test_value = 'get_test_value'
        try:
            os.environ[test_key] = test_value
            result = mgr.get(test_key)
            assert result == test_value, (
                f"get() 读取错误：期望 {test_value}，实际 {result}"
            )
        finally:
            os.environ.pop(test_key, None)


class TestGetEnvConfigManagerSingletonIsolation:
    """单例隔离测试：验证 set 写入的值在同一实例上持久"""

    def test_set_then_get_same_instance(self):
        """同一单例实例上 set 的值应立即可通过 os.getenv 读取"""
        mgr = get_env_config_manager()
        test_key = 'TEST_SINGLETON_PERSIST'
        test_value = 'value_from_set'
        try:
            mgr.set(test_key, test_value)
            # 同一实例写入后，os.environ 应立即可读
            assert os.getenv(test_key) == test_value, (
                "set() 写入后 os.getenv 读取失败——单例实例可能未正确写入"
            )
        finally:
            os.environ.pop(test_key, None)


class TestEnvConfigManagerReturnTypeAnnotation:
    """类型注解合规性测试

    get_env_config_manager() 的返回类型注解为 -> EnvConfigManager，
    本类验证运行时行为与注解一致。
    """

    def test_return_type_annotation_matches_runtime(self):
        """运行时返回类型应与类型注解 -> EnvConfigManager 一致"""
        import inspect
        sig = inspect.signature(get_env_config_manager)
        return_annotation = sig.return_annotation

        # 返回类型注解应为 EnvConfigManager
        assert return_annotation is EnvConfigManager or (
            hasattr(return_annotation, '__name__') and
            return_annotation.__name__ == 'EnvConfigManager'
        ), f"返回类型注解错误：{return_annotation}"

        # 运行时返回值应为 EnvConfigManager 实例
        result = get_env_config_manager()
        assert isinstance(result, return_annotation), (
            f"运行时类型与注解不匹配：注解={return_annotation}，实际={type(result)}"
        )
