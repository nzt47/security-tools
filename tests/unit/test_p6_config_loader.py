import pytest
import os
import json
from agent.p6_config_loader import P6ConfigLoader, create_snapshot_manager_from_config


class TestP6ConfigLoader:
    """P6 配置加载器测试"""

    def test_load_default_config(self):
        """测试加载默认配置"""
        loader = P6ConfigLoader()
        loader.load()
        
        assert loader.config is not None
        assert "p6_snapshot" in loader.config
        assert loader.get("p6_snapshot.enabled") is True

    def test_load_nonexistent_file(self, tmp_path):
        """测试加载不存在的配置文件"""
        loader = P6ConfigLoader(str(tmp_path / "nonexistent.json"))
        result = loader.load()
        
        assert result is False
        assert loader.loaded is False
        assert "p6_snapshot" in loader.config

    def test_load_valid_config(self, tmp_path):
        """测试加载有效配置文件"""
        config_data = {
            "p6_snapshot": {
                "enabled": True,
                "snapshot_directory": "./custom_snapshots",
                "frequency_control": {
                    "min_interval_seconds": 120,
                    "max_snapshots": 10,
                },
                "compression": {
                    "enabled": True,
                    "level": 9,
                },
            }
        }
        config_file = tmp_path / "p6_config.json"
        config_file.write_text(json.dumps(config_data))
        
        loader = P6ConfigLoader(str(config_file))
        result = loader.load()
        
        assert result is True
        assert loader.loaded is True
        assert loader.get("p6_snapshot.snapshot_directory") == "./custom_snapshots"
        assert loader.get("p6_snapshot.frequency_control.min_interval_seconds") == 120
        assert loader.get("p6_snapshot.frequency_control.max_snapshots") == 10

    def test_load_invalid_config(self, tmp_path):
        """测试加载无效配置文件（JSON 错误）"""
        config_file = tmp_path / "invalid.json"
        config_file.write_text("not valid json")
        
        loader = P6ConfigLoader(str(config_file))
        result = loader.load()
        
        assert result is False
        assert loader.loaded is False
        assert "p6_snapshot" in loader.config

    def test_get_nested_key(self):
        """测试获取嵌套配置键"""
        loader = P6ConfigLoader()
        loader.load()
        
        value = loader.get("p6_snapshot.frequency_control.min_interval_seconds")
        assert value == 300

    def test_get_default_value(self):
        """测试获取不存在的键返回默认值"""
        loader = P6ConfigLoader()
        loader.load()
        
        value = loader.get("nonexistent.key.path", "default_value")
        assert value == "default_value"

    def test_get_frequency_control_config(self):
        """测试获取频率控制配置"""
        loader = P6ConfigLoader()
        loader.load()
        
        config = loader.get_frequency_control_config()
        
        assert "min_interval_seconds" in config
        assert "max_snapshots" in config
        assert config["min_interval_seconds"] == 300
        assert config["max_snapshots"] == 5

    def test_get_compression_config(self):
        """测试获取压缩配置"""
        loader = P6ConfigLoader()
        loader.load()
        
        config = loader.get_compression_config()
        
        assert "enabled" in config
        assert "level" in config
        assert config["enabled"] is True
        assert config["level"] == 6

    def test_get_snapshot_directory(self):
        """测试获取快照目录"""
        loader = P6ConfigLoader()
        loader.load()
        
        directory = loader.get_snapshot_directory()
        assert directory == "./.p6_snapshots"

    def test_is_enabled(self):
        """测试检查 P6 快照是否启用"""
        loader = P6ConfigLoader()
        loader.load()
        
        assert loader.is_enabled() is True

    def test_custom_config_values(self, tmp_path):
        """测试自定义配置值"""
        config_data = {
            "p6_snapshot": {
                "enabled": False,
                "snapshot_directory": "/custom/path",
                "frequency_control": {
                    "min_interval_seconds": 60,
                    "max_snapshots": 3,
                },
                "compression": {
                    "enabled": False,
                    "level": 1,
                },
                "modules": {
                    "body_sensor": {"enabled": True, "restore_priority": 100},
                    "behavior": {"enabled": False, "restore_priority": 90},
                },
            }
        }
        config_file = tmp_path / "custom_config.json"
        config_file.write_text(json.dumps(config_data))
        
        loader = P6ConfigLoader(str(config_file))
        loader.load()
        
        assert loader.is_enabled() is False
        assert loader.get_snapshot_directory() == "/custom/path"
        
        freq_config = loader.get_frequency_control_config()
        assert freq_config["min_interval_seconds"] == 60
        assert freq_config["max_snapshots"] == 3
        
        comp_config = loader.get_compression_config()
        assert comp_config["enabled"] is False
        assert comp_config["level"] == 1

    def test_create_snapshot_manager_from_config(self, tmp_path):
        """测试从配置创建快照管理器"""
        config_data = {
            "p6_snapshot": {
                "enabled": True,
                "snapshot_directory": str(tmp_path / "snapshots"),
                "frequency_control": {
                    "min_interval_seconds": 30,
                    "max_snapshots": 5,
                },
                "compression": {
                    "enabled": True,
                    "level": 6,
                },
            }
        }
        config_file = tmp_path / "test_config.json"
        config_file.write_text(json.dumps(config_data))
        
        manager, loader = create_snapshot_manager_from_config(str(config_file))
        
        assert manager is not None
        assert loader is not None
        assert loader.get_snapshot_directory() == str(tmp_path / "snapshots")