"""
P6 配置加载器测试
"""

import pytest
import tempfile
import json
import os

from agent.p6_config_loader import (
    P6ConfigLoader,
    create_snapshot_manager_from_config,
)


class TestP6ConfigLoaderInit:
    """测试配置加载器初始化"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_default_config_file(self):
        """测试默认配置文件初始化"""
        loader = P6ConfigLoader()
        assert loader.config_file == "p6_config.json"
        assert loader.config == {}
        assert not loader.loaded

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_custom_config_file(self):
        """测试自定义配置文件初始化"""
        loader = P6ConfigLoader("custom_config.json")
        assert loader.config_file == "custom_config.json"
        assert loader.config == {}


class TestConfigLoading:
    """测试配置加载"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_missing_file(self):
        """测试加载不存在的文件"""
        loader = P6ConfigLoader("/nonexistent/path/config.json")
        result = loader.load()
        
        assert not result
        assert not loader.loaded
        # 应该使用默认配置
        assert loader.config != {}

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_valid_file(self):
        """测试加载有效配置文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "test_config.json")
            config_data = {
                "p6_snapshot": {
                    "enabled": True,
                    "snapshot_directory": "./test_snapshots",
                    "frequency_control": {
                        "min_interval_seconds": 60,
                        "max_snapshots": 10,
                    },
                    "compression": {
                        "enabled": False,
                        "level": 3,
                    },
                }
            }
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(config_data, f)
            
            loader = P6ConfigLoader(config_file)
            result = loader.load()
            
            assert result
            assert loader.loaded
            assert loader.config["p6_snapshot"]["enabled"] is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_invalid_json(self):
        """测试加载无效 JSON 文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "invalid_config.json")
            with open(config_file, "w", encoding="utf-8") as f:
                f.write("invalid json content {{{")
            
            loader = P6ConfigLoader(config_file)
            result = loader.load()
            
            assert not result
            assert not loader.loaded
            # 应该使用默认配置
            assert loader.config != {}

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_with_override_path(self):
        """测试使用覆盖路径加载"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file1 = os.path.join(tmpdir, "config1.json")
            config_file2 = os.path.join(tmpdir, "config2.json")
            
            config_data1 = {"p6_snapshot": {"enabled": False}}
            config_data2 = {"p6_snapshot": {"enabled": True}}
            
            with open(config_file1, "w") as f:
                json.dump(config_data1, f)
            with open(config_file2, "w") as f:
                json.dump(config_data2, f)
            
            loader = P6ConfigLoader(config_file1)
            loader.load()
            assert loader.config["p6_snapshot"]["enabled"] is False
            
            # 使用覆盖路径重新加载
            loader.load(config_file2)
            assert loader.config_file == config_file2
            assert loader.config["p6_snapshot"]["enabled"] is True


class TestDefaultConfig:
    """测试默认配置"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_use_default_config(self):
        """测试使用默认配置"""
        loader = P6ConfigLoader()
        loader._use_default_config()
        
        assert loader.config["p6_snapshot"]["enabled"] is True
        assert loader.config["p6_snapshot"]["snapshot_directory"] == "./.p6_snapshots"
        assert loader.config["p6_snapshot"]["frequency_control"]["min_interval_seconds"] == 300
        assert loader.config["p6_snapshot"]["frequency_control"]["max_snapshots"] == 5
        assert loader.config["p6_snapshot"]["compression"]["enabled"] is True
        assert loader.config["p6_snapshot"]["compression"]["level"] == 6

    @pytest.mark.unit
    @pytest.mark.p0
    def test_default_modules_config(self):
        """测试默认模块配置"""
        loader = P6ConfigLoader()
        loader._use_default_config()
        
        modules = loader.config["p6_snapshot"]["modules"]
        assert "body_sensor" in modules
        assert "behavior" in modules
        assert "permission" in modules
        assert "tools_registry" in modules
        
        assert modules["body_sensor"]["restore_priority"] == 100
        assert modules["behavior"]["restore_priority"] == 90


class TestConfigGet:
    """测试配置获取"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_simple_key(self):
        """测试获取简单键"""
        loader = P6ConfigLoader()
        loader.config = {"key": "value"}
        
        assert loader.get("key") == "value"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_nested_key(self):
        """测试获取嵌套键"""
        loader = P6ConfigLoader()
        loader.config = {
            "p6_snapshot": {
                "frequency_control": {
                    "min_interval_seconds": 60
                }
            }
        }
        
        assert loader.get("p6_snapshot.frequency_control.min_interval_seconds") == 60

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_missing_key_with_default(self):
        """测试获取缺失键返回默认值"""
        loader = P6ConfigLoader()
        loader.config = {}
        
        assert loader.get("missing.key", default="default_value") == "default_value"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_partial_key(self):
        """测试获取部分键"""
        loader = P6ConfigLoader()
        loader.config = {
            "p6_snapshot": {
                "enabled": True
            }
        }
        
        # 获取部分路径
        result = loader.get("p6_snapshot")
        assert result == {"enabled": True}

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_empty_key(self):
        """测试获取空键"""
        loader = P6ConfigLoader()
        loader.config = {"key": "value"}
        
        # 空键应该返回默认值（因为无法找到空键）
        result = loader.get("")
        assert result is None  # 空键分割后无法匹配


class TestConfigHelpers:
    """测试配置辅助方法"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_frequency_control_config(self):
        """测试获取频率控制配置"""
        loader = P6ConfigLoader()
        loader._use_default_config()
        
        freq_config = loader.get_frequency_control_config()
        assert freq_config["min_interval_seconds"] == 300
        assert freq_config["max_snapshots"] == 5

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_frequency_control_custom(self):
        """测试获取自定义频率控制配置"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "config.json")
            config_data = {
                "p6_snapshot": {
                    "frequency_control": {
                        "min_interval_seconds": 120,
                        "max_snapshots": 20,
                    }
                }
            }
            with open(config_file, "w") as f:
                json.dump(config_data, f)
            
            loader = P6ConfigLoader(config_file)
            loader.load()
            
            freq_config = loader.get_frequency_control_config()
            assert freq_config["min_interval_seconds"] == 120
            assert freq_config["max_snapshots"] == 20

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_compression_config(self):
        """测试获取压缩配置"""
        loader = P6ConfigLoader()
        loader._use_default_config()
        
        comp_config = loader.get_compression_config()
        assert comp_config["enabled"] is True
        assert comp_config["level"] == 6

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_snapshot_directory(self):
        """测试获取快照目录"""
        loader = P6ConfigLoader()
        loader._use_default_config()
        
        assert loader.get_snapshot_directory() == "./.p6_snapshots"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_snapshot_directory_custom(self):
        """测试获取自定义快照目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "config.json")
            config_data = {
                "p6_snapshot": {
                    "snapshot_directory": "/custom/path"
                }
            }
            with open(config_file, "w") as f:
                json.dump(config_data, f)
            
            loader = P6ConfigLoader(config_file)
            loader.load()
            
            assert loader.get_snapshot_directory() == "/custom/path"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_is_enabled(self):
        """测试检查是否启用"""
        loader = P6ConfigLoader()
        loader._use_default_config()
        
        assert loader.is_enabled() is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_is_enabled_disabled(self):
        """测试检查是否禁用"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "config.json")
            config_data = {
                "p6_snapshot": {
                    "enabled": False
                }
            }
            with open(config_file, "w") as f:
                json.dump(config_data, f)
            
            loader = P6ConfigLoader(config_file)
            loader.load()
            
            assert loader.is_enabled() is False


class TestCreateSnapshotManager:
    """测试从配置创建快照管理器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_create_with_valid_config(self):
        """测试使用有效配置创建管理器"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "config.json")
            config_data = {
                "p6_snapshot": {
                    "enabled": True,
                    "snapshot_directory": os.path.join(tmpdir, "snapshots"),
                    "frequency_control": {
                        "min_interval_seconds": 60,
                        "max_snapshots": 10,
                    },
                    "compression": {
                        "enabled": False,
                    },
                }
            }
            with open(config_file, "w") as f:
                json.dump(config_data, f)
            
            manager, loader = create_snapshot_manager_from_config(config_file)
            
            assert manager is not None
            assert loader is not None
            assert loader.loaded

    @pytest.mark.unit
    @pytest.mark.p0
    def test_create_with_missing_config(self):
        """测试使用缺失配置创建管理器"""
        # 使用不存在的配置文件
        manager, loader = create_snapshot_manager_from_config("/nonexistent/config.json")
        
        assert manager is not None
        assert loader is not None
        # 应该使用默认配置
        assert not loader.loaded