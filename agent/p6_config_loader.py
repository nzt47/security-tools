#!/usr/bin/env python3
"""
P6 快照管理器配置加载器

从 config.json 加载配置并应用到快照管理器
"""

import os
import json
import logging
from typing import Dict, Any, Optional


logger = logging.getLogger("P6-Config")


class P6ConfigLoader:
    """P6 配置加载器"""
    
    def __init__(self, config_file: str = "p6_config.json"):
        """初始化配置加载器
        
        Args:
            config_file: 配置文件路径
        """
        self.config_file = config_file
        self.config: Dict[str, Any] = {}
        self.loaded = False
        
    def load(self, config_file: Optional[str] = None) -> bool:
        """加载配置文件
        
        Args:
            config_file: 可选的配置文件路径
            
        Returns:
            是否加载成功
        """
        if config_file:
            self.config_file = config_file
            
        if not os.path.exists(self.config_file):
            logger.warning(f"配置文件不存在: {self.config_file}，使用默认配置")
            self._use_default_config()
            return False
            
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                self.config = json.load(f)
            logger.info(f"配置加载成功: {self.config_file}")
            self.loaded = True
            return True
        except Exception as e:
            logger.error(f"配置加载失败: {e}")
            self._use_default_config()
            return False
    
    def _use_default_config(self):
        """使用默认配置"""
        self.config = {
            "p6_snapshot": {
                "enabled": True,
                "snapshot_directory": "./.p6_snapshots",
                "frequency_control": {
                    "min_interval_seconds": 300,
                    "max_snapshots": 5,
                },
                "compression": {
                    "enabled": True,
                    "level": 6,
                },
                "modules": {
                    "body_sensor": {"enabled": True, "restore_priority": 100},
                    "behavior": {"enabled": True, "restore_priority": 90},
                    "permission": {"enabled": True, "restore_priority": 80},
                    "tools_registry": {"enabled": True, "restore_priority": 70},
                },
            }
        }
        self.loaded = False
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值
        
        Args:
            key: 配置键（支持点号分隔，如 "p6_snapshot.frequency_control.min_interval_seconds"）
            default: 默认值
            
        Returns:
            配置值
        """
        keys = key.split(".")
        value = self.config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
                
        return value
    
    def get_frequency_control_config(self) -> Dict[str, Any]:
        """获取频率控制配置"""
        return {
            "min_interval_seconds": self.get("p6_snapshot.frequency_control.min_interval_seconds", 300),
            "max_snapshots": self.get("p6_snapshot.frequency_control.max_snapshots", 5),
        }
    
    def get_compression_config(self) -> Dict[str, Any]:
        """获取压缩配置"""
        return {
            "enabled": self.get("p6_snapshot.compression.enabled", True),
            "level": self.get("p6_snapshot.compression.level", 6),
        }
    
    def get_snapshot_directory(self) -> str:
        """获取快照目录"""
        return self.get("p6_snapshot.snapshot_directory", "./.p6_snapshots")
    
    def is_enabled(self) -> bool:
        """检查P6快照是否启用"""
        return self.get("p6_snapshot.enabled", True)


def create_snapshot_manager_from_config(config_file: str = "p6_config.json"):
    """从配置文件创建快照管理器
    
    Args:
        config_file: 配置文件路径
        
    Returns:
        快照管理器实例和配置加载器
    """
    import sys
    import os
    # 确保 agent 目录在 Python 路径中
    agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)
    
    from agent.p6_snapshot import StateSnapshotManager, SnapshotFrequencyController
    
    # 加载配置
    loader = P6ConfigLoader(config_file)
    loader.load()
    
    # 获取频率控制配置
    freq_config = loader.get_frequency_control_config()
    
    # 创建频率控制器
    frequency_controller = SnapshotFrequencyController(
        min_interval_seconds=freq_config["min_interval_seconds"],
        max_snapshots=freq_config["max_snapshots"],
    )
    
    # 创建快照管理器
    manager = StateSnapshotManager(
        snapshot_dir=loader.get_snapshot_directory(),
        enable_compression=loader.get_compression_config()["enabled"],
    )
    
    # 替换频率控制器
    manager.frequency_controller = frequency_controller
    
    logger.info(f"快照管理器创建成功")
    logger.info(f"  - 快照目录: {loader.get_snapshot_directory()}")
    logger.info(f"  - 最小间隔: {freq_config['min_interval_seconds']}秒")
    logger.info(f"  - 最大快照: {freq_config['max_snapshots']}个")
    logger.info(f"  - 压缩: {loader.get_compression_config()['enabled']}")
    
    return manager, loader


def main():
    """主函数 - 测试配置加载"""
    print("=" * 70)
    print("P6 配置加载器测试")
    print("=" * 70)
    
    # 测试默认配置
    print("\n1. 测试默认配置...")
    loader1 = P6ConfigLoader()
    loader1.load()
    print(f"   快照目录: {loader1.get_snapshot_directory()}")
    print(f"   最小间隔: {loader1.get('p6_snapshot.frequency_control.min_interval_seconds')}秒")
    print(f"   最大快照: {loader1.get('p6_snapshot.frequency_control.max_snapshots')}个")
    
    # 测试配置文件
    print("\n2. 测试 p6_config.json...")
    if os.path.exists("p6_config.json"):
        loader2 = P6ConfigLoader("p6_config.json")
        loader2.load()
        print(f"   配置加载: {'成功' if loader2.loaded else '失败（使用默认）'}")
        print(f"   快照目录: {loader2.get_snapshot_directory()}")
        print(f"   最小间隔: {loader2.get('p6_snapshot.frequency_control.min_interval_seconds')}秒")
        print(f"   最大快照: {loader2.get('p6_snapshot.frequency_control.max_snapshots')}个")
        print(f"   压缩级别: {loader2.get('p6_snapshot.compression.level')}")
        print(f"   BodySensor优先级: {loader2.get('p6_snapshot.modules.body_sensor.restore_priority')}")
        print(f"   Behavior优先级: {loader2.get('p6_snapshot.modules.behavior.restore_priority')}")
        print(f"   Permission优先级: {loader2.get('p6_snapshot.modules.permission.restore_priority')}")
        
        # 测试创建管理器
        print("\n3. 测试从配置创建快照管理器...")
        manager, loader = create_snapshot_manager_from_config("p6_config.json")
        print(f"   管理器创建成功!")
    else:
        print(f"   ⚠️ 配置文件不存在: p6_config.json")
    
    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()

