"""
统一配置管理
Phase 3 - Core Abstraction
"""
import os
import json
import logging
from typing import Any, Dict, Optional, List
from pathlib import Path

logger = logging.getLogger(__name__)


class Config:
    """统一配置管理类
    
    支持点语法访问嵌套配置
    支持从多个源加载配置
    """
    
    def __init__(self, data: Dict[str, Any] = None):
        self._data = data or {}
    
    def get(self, path: str, default: Any = None) -> Any:
        """获取配置值 (支持点语法)
        
        Args:
            path: 配置路径，如 "a.b.c"
            default: 默认值
            
        Returns:
            配置值
        """
        keys = path.split(".")
        current = self._data
        
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        
        return current
    
    def set(self, path: str, value: Any) -> None:
        """设置配置值 (支持点语法)
        
        Args:
            path: 配置路径
            value: 配置值
        """
        keys = path.split(".")
        current = self._data
        
        # 创建路径
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        
        current[keys[-1]] = value
        logger.debug(f"⚙️ Config set: {path} = {value}")
    
    def merge(self, other: Dict[str, Any]) -> None:
        """合并其他配置
        
        Args:
            other: 要合并的配置字典
        """
        self._deep_merge(self._data, other)
        logger.debug(f"⚙️ Config merged: {len(other)} items")
    
    def _deep_merge(self, base: Dict, update: Dict) -> None:
        """深度合并字典"""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return dict(self._data)
    
    def save(self, filepath: str) -> None:
        """保存到文件
        
        Args:
            filepath: 保存路径
        """
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"💾 Config saved to: {filepath}")
    
    @classmethod
    def load(cls, filepath: str) -> 'Config':
        """从文件加载
        
        Args:
            filepath: 文件路径
            
        Returns:
            Config实例
        """
        if not Path(filepath).exists():
            logger.warning(f"⚠️ Config file not found: {filepath}")
            return cls({})
        
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        logger.info(f"📥 Config loaded from: {filepath}")
        return cls(data)
    
    @classmethod
    def from_env(cls, prefix: str = "Yunshu_") -> 'Config':
        """从环境变量加载
        
        Args:
            prefix: 环境变量前缀
            
        Returns:
            Config实例
        """
        data = {}
        
        for key, value in os.environ.items():
            if key.startswith(prefix):
                path = key[len(prefix):].lower().replace("__", ".")
                cls._set_nested(data, path, value)
        
        logger.info(f"📥 Config loaded from env: {len(data)} items")
        return cls(data)
    
    @staticmethod
    def _set_nested(data: Dict, path: str, value: str) -> None:
        """设置嵌套值"""
        keys = path.split(".")
        current = data
        
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        
        current[keys[-1]] = value
    
    def __repr__(self) -> str:
        return f"Config({self._data})"
