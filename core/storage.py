"""
统一存储抽象层
Phase 3 - Core Abstraction
"""
import os
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, List, Dict, Optional
from pathlib import Path
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class StorableItem:
    """可存储的数据项基类"""
    id: str
    created_at: str
    updated_at: str
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StorableItem':
        return cls(**data)


class BaseStorage(ABC):
    """基础存储抽象接口"""
    
    @abstractmethod
    def load(self, key: str, default: Any = None) -> Any:
        """根据键加载数据
        
        Args:
            key: 数据键
            default: 默认值
            
        Returns:
            加载的数据
        """
        pass
    
    @abstractmethod
    def save(self, key: str, data: Any) -> None:
        """保存数据
        
        Args:
            key: 数据键
            data: 要保存的数据
        """
        pass
    
    @abstractmethod
    def list_keys(self, prefix: str = None) -> List[str]:
        """列出所有键
        
        Args:
            prefix: 键前缀过滤
            
        Returns:
            键列表
        """
        pass
    
    @abstractmethod
    def delete(self, key: str) -> bool:
        """删除数据
        
        Args:
            key: 数据键
            
        Returns:
            是否成功删除
        """
        pass
    
    @abstractmethod
    def exists(self, key: str) -> bool:
        """检查键是否存在
        
        Args:
            key: 数据键
            
        Returns:
            是否存在
        """
        pass


class JSONFileStorage(BaseStorage):
    """JSON文件存储实现"""
    
    def __init__(self, base_dir: str = "./data", ensure_dir: bool = True):
        """
        Args:
            base_dir: 基础存储目录
            ensure_dir: 是否确保目录存在
        """
        logger.info("[JSONFileStorage] __init__ 开始初始化")
        self.base_dir = Path(base_dir)
        
        logger.info(f"[JSONFileStorage] 基础目录: {self.base_dir}")
        
        if ensure_dir:
            logger.info(f"[JSONFileStorage] 确保目录存在: {self.base_dir}")
            self.base_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"[JSONFileStorage] 目录创建/检查完成")
        
        logger.info(f"[JSONFileStorage] JSONFileStorage initialized: {self.base_dir}")
        logger.info("[JSONFileStorage] __init__ 初始化完成")
    
    def _get_filepath(self, key: str) -> Path:
        """获取键对应的文件路径"""
        logger.debug(f"[JSONFileStorage._get_filepath] key: {key}")
        
        # 规范化键名，避免路径遍历问题
        safe_key = key.replace("/", "_").replace("\\", "_").replace("..", "")
        logger.debug(f"[JSONFileStorage._get_filepath] safe_key: {safe_key}")
        
        filepath = self.base_dir / f"{safe_key}.json"
        logger.debug(f"[JSONFileStorage._get_filepath] filepath: {filepath}")
        
        return filepath
    
    def load(self, key: str, default: Any = None) -> Any:
        logger.info(f"[JSONFileStorage.load] 开始加载: key={key}, default={default}")
        filepath = self._get_filepath(key)
        
        if not filepath.exists():
            logger.warning(f"[JSONFileStorage.load] 文件不存在: {filepath}, 返回默认值")
            return default
        
        logger.info(f"[JSONFileStorage.load] 文件存在，开始读取: {filepath}")
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info(f"[JSONFileStorage.load] 读取成功: {filepath}")
            logger.info(f"[JSONFileStorage.load] Loaded: {key}")
            return data
        except Exception as e:
            logger.error(f"[JSONFileStorage.load] 读取异常: {e}")
            logger.warning(f"Failed to load {key}: {e}")
            return default
    
    def save(self, key: str, data: Any) -> None:
        logger.info(f"[JSONFileStorage.save] 开始保存: key={key}")
        filepath = self._get_filepath(key)
        
        logger.info(f"[JSONFileStorage.save] 写入文件: {filepath}")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"[JSONFileStorage.save] 写入成功: {filepath}")
            logger.info(f"[JSONFileStorage.save] Saved: {key}")
        except Exception as e:
            logger.error(f"[JSONFileStorage.save] 写入异常: {e}")
            logger.error(f"Failed to save {key}: {e}")
            raise
    
    def list_keys(self, prefix: str = None) -> List[str]:
        logger.info(f"[JSONFileStorage.list_keys] 开始列出键: prefix={prefix}")
        
        keys = []
        for filepath in self.base_dir.glob("*.json"):
            key = filepath.stem
            logger.debug(f"[JSONFileStorage.list_keys] 发现键: {key}")
            
            if prefix is None or key.startswith(prefix):
                keys.append(key)
        
        logger.info(f"[JSONFileStorage.list_keys] 返回 {len(keys)} 个键")
        return sorted(keys)
    
    def delete(self, key: str) -> bool:
        logger.info(f"[JSONFileStorage.delete] 开始删除: key={key}")
        filepath = self._get_filepath(key)
        
        if not filepath.exists():
            logger.warning(f"[JSONFileStorage.delete] 文件不存在: {filepath}")
            return False
        
        logger.info(f"[JSONFileStorage.delete] 删除文件: {filepath}")
        try:
            filepath.unlink()
            logger.info(f"[JSONFileStorage.delete] 删除成功")
            logger.info(f"[JSONFileStorage.delete] Deleted: {key}")
            return True
        except Exception as e:
            logger.error(f"[JSONFileStorage.delete] 删除异常: {e}")
            logger.error(f"Failed to delete {key}: {e}")
            return False
    
    def exists(self, key: str) -> bool:
        logger.debug(f"[JSONFileStorage.exists] 检查键: {key}")
        filepath = self._get_filepath(key)
        exists = filepath.exists()
        logger.debug(f"[JSONFileStorage.exists] 结果: {exists}")
        return exists


class InMemoryStorage(BaseStorage):
    """内存存储实现 - 用于测试"""
    
    def __init__(self):
        logger.info("[InMemoryStorage] __init__ 开始初始化")
        self._data: Dict[str, Any] = {}
        logger.info("[InMemoryStorage] InMemoryStorage initialized")
        logger.info("[InMemoryStorage] __init__ 初始化完成")
    
    def load(self, key: str, default: Any = None) -> Any:
        logger.info(f"[InMemoryStorage.load] 开始加载: key={key}, default={default}")
        
        if key in self._data:
            logger.info(f"[InMemoryStorage.load] 键存在: {key}")
            return self._data[key]
        else:
            logger.warning(f"[InMemoryStorage.load] 键不存在: {key}, 返回默认值")
            return default
    
    def save(self, key: str, data: Any) -> None:
        logger.info(f"[InMemoryStorage.save] 开始保存: key={key}")
        self._data[key] = data
        logger.info(f"[InMemoryStorage.save] 保存成功: {key}")
        logger.debug(f"[InMemoryStorage.save] Saved (in-memory): {key}")
    
    def list_keys(self, prefix: str = None) -> List[str]:
        logger.info(f"[InMemoryStorage.list_keys] 开始列出键: prefix={prefix}")
        
        keys = list(self._data.keys())
        if prefix:
            logger.debug(f"[InMemoryStorage.list_keys] 应用前缀过滤: {prefix}")
            keys = [k for k in keys if k.startswith(prefix)]
        
        logger.info(f"[InMemoryStorage.list_keys] 返回 {len(keys)} 个键")
        return sorted(keys)
    
    def delete(self, key: str) -> bool:
        logger.info(f"[InMemoryStorage.delete] 开始删除: key={key}")
        
        if key in self._data:
            logger.info(f"[InMemoryStorage.delete] 键存在，准备删除: {key}")
            del self._data[key]
            logger.info(f"[InMemoryStorage.delete] 删除成功: {key}")
            logger.debug(f"[InMemoryStorage.delete] Deleted (in-memory): {key}")
            return True
        else:
            logger.warning(f"[InMemoryStorage.delete] 键不存在: {key}")
            return False
    
    def exists(self, key: str) -> bool:
        logger.debug(f"[InMemoryStorage.exists] 检查键: {key}")
        exists = key in self._data
        logger.debug(f"[InMemoryStorage.exists] 结果: {exists}")
        return exists


# 快捷函数
def create_storage(storage_type: str = "json", **kwargs) -> BaseStorage:
    """
    创建存储实例
    
    Args:
        storage_type: "json" 或 "memory"
        **kwargs: 其他参数
        
    Returns:
        存储实例
    """
    logger.info(f"[create_storage] 开始创建存储: type={storage_type}, kwargs={kwargs}")
    
    if storage_type == "json":
        logger.info("[create_storage] 创建 JSONFileStorage")
        
        valid_json_args = {"base_dir", "ensure_dir"}
        invalid_args = [k for k in kwargs if k not in valid_json_args]
        if invalid_args:
            logger.warning(f"[create_storage] JSONFileStorage 不支持的参数将被忽略: {invalid_args}")
            kwargs = {k: v for k, v in kwargs.items() if k in valid_json_args}
        
        storage = JSONFileStorage(**kwargs)
        logger.info("[create_storage] JSONFileStorage 创建完成")
        return storage
    elif storage_type == "memory":
        logger.info("[create_storage] 创建 InMemoryStorage")
        
        if kwargs:
            logger.warning(f"[create_storage] InMemoryStorage 不接受参数，传入的参数将被忽略: {kwargs.keys()}")
        
        storage = InMemoryStorage()
        logger.info("[create_storage] InMemoryStorage 创建完成")
        return storage
    else:
        logger.error(f"[create_storage] 未知的存储类型: {storage_type}")
        raise ValueError(f"Unknown storage type: {storage_type}")
