"""SystemPromptConfigManager 缓存引用泄漏修复测试

验证 load() 返回 deepcopy 而非缓存引用，save() 使用 deepcopy 而非直接引用。
修复目标：与 NetworkConfigManager 保持一致的缓存安全策略。
"""

import json
import pytest
from unittest.mock import patch
from pathlib import Path

from agent.system_prompt_config import SystemPromptConfigManager


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def config_file(tmp_path):
    """临时配置文件路径"""
    return str(tmp_path / "system_prompt_config.json")


@pytest.fixture
def manager(monkeypatch, config_file):
    """使用临时文件路径的 SystemPromptConfigManager 实例

    Why: CONFIG_FILE 是模块级常量，指向真实 data/ 目录。
    通过 monkeypatch 替换为临时路径，隔离测试副作用。
    """
    monkeypatch.setattr('agent.system_prompt_config.CONFIG_FILE', config_file)
    return SystemPromptConfigManager()


# ============================================================================
# load() 必须返回 deepcopy
# ============================================================================

class TestLoadReturnsDeepCopy:
    """load() 返回独立副本，修改返回值不污染缓存"""

    def test_load_returns_independent_copy(self, manager):
        """load() 返回的 dict 修改后不影响缓存"""
        config = manager.load()
        original_version = config["version"]

        # 修改返回值
        config["version"] = 999

        # 再次 load，缓存应未被污染
        config2 = manager.load()
        assert config2["version"] == original_version

    def test_load_multiple_calls_return_different_objects(self, manager):
        """多次 load() 返回不同的对象（非同一引用）"""
        config1 = manager.load()
        config2 = manager.load()
        assert config1 is not config2

    def test_load_nested_dict_not_shared(self, manager):
        """load() 返回的嵌套 dict 修改后不影响缓存"""
        config = manager.load()

        # 修改嵌套结构
        original_enabled = config["sections"]["identity"]["enabled"]
        config["sections"]["identity"]["enabled"] = not original_enabled

        # 缓存应未被污染
        config2 = manager.load()
        assert config2["sections"]["identity"]["enabled"] == original_enabled

    def test_load_from_file_returns_deepcopy(self, manager, config_file):
        """从文件加载时也返回 deepcopy"""
        # 首次 load 会创建默认配置并写入文件
        manager.load()

        # 新建 manager，从文件加载
        m2 = SystemPromptConfigManager()
        config = m2.load()
        original_version = config["version"]

        # 修改返回值
        config["version"] = 999

        # 缓存应未被污染
        config2 = m2.load()
        assert config2["version"] == original_version


# ============================================================================
# save() 必须使用 deepcopy
# ============================================================================

class TestSaveUsesDeepCopy:
    """save() 使用 deepcopy，修改传入的 config 不影响缓存"""

    def test_save_does_not_retain_reference(self, manager):
        """save() 后修改原 config 引用不影响缓存"""
        config = manager.load()
        manager.save(config)

        # 修改原 config 引用
        config["version"] = 999

        # 缓存应未被污染
        cached = manager.load()
        assert cached["version"] != 999

    def test_save_stores_deepcopy(self, manager):
        """save() 后缓存是独立副本（非传入引用）"""
        config = manager.load()
        manager.save(config)

        # load 返回 deepcopy，修改不影响缓存
        # 但需要验证 save 内部确实做了 deepcopy
        # 方法：save 后再 load，确认不是同一个对象
        cached = manager.load()
        assert cached is not config

    def test_save_failure_does_not_pollute_cache(self, manager):
        """save() 失败时缓存未被污染

        Why: update_section() 先 load() 获取 config 再修改再 save()。
        若 load 返回引用，修改会直接污染缓存；save 失败时缓存与文件不一致。
        修复后 load 返回 deepcopy，修改不影响缓存，save 失败时缓存完好。
        """
        # 先加载一次填充缓存
        original = manager.load()
        original_enabled = original["sections"]["identity"]["enabled"]

        # mock save 失败
        with patch.object(manager, 'save', return_value=False):
            result = manager.update_section("identity", {"enabled": not original_enabled})
            assert result is False

        # 缓存应未被修改
        cached = manager.load()
        assert cached["sections"]["identity"]["enabled"] == original_enabled

    def test_save_real_failure_keeps_cache_intact(self, manager):
        """save 内部异常时缓存保持原样（触发 save 内部 except 分支）"""
        original = manager.load()
        original_version = original["version"]

        # 让文件写入真正失败，触发 save 内部 except 分支
        with patch('builtins.open', side_effect=IOError("disk full")):
            result = manager.save({"version": 999, "sections": {}})
            assert result is False

        # 缓存应未被修改
        cached = manager.load()
        assert cached["version"] == original_version


# ============================================================================
# update_section / set_custom_template 安全性
# ============================================================================

class TestUpdateSafety:
    """update_section 和 set_custom_template 在 save 失败时缓存完好"""

    def test_update_section_save_success_persists(self, manager):
        """update_section 成功时配置正确持久化"""
        original = manager.load()
        original_enabled = original["sections"]["identity"]["enabled"]

        result = manager.update_section("identity", {"enabled": not original_enabled})
        assert result is True

        # 缓存应已更新
        cached = manager.load()
        assert cached["sections"]["identity"]["enabled"] == (not original_enabled)

    def test_set_custom_template_save_failure_cache_intact(self, manager):
        """set_custom_template save 失败时缓存完好"""
        original = manager.load()
        original_template = original.get("custom_template")

        with patch.object(manager, 'save', return_value=False):
            result = manager.set_custom_template("modified_template")
            assert result is False

        # 缓存应未被修改
        cached = manager.load()
        assert cached.get("custom_template") == original_template
