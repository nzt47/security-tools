"""
统一注册表抽象层
Phase 3 - Core Abstraction
"""
import logging
from abc import ABC, abstractmethod
from typing import Any, List, Dict, Optional, Callable, TypeVar, Generic

T = TypeVar('T')

logger = logging.getLogger(__name__)


class BaseRegistry(ABC, Generic[T]):
    """基础注册表抽象接口"""
    
    @abstractmethod
    def register(self, name: str, item: T) -> None:
        """注册一个项目
        
        Args:
            name: 项目名称
            item: 要注册的项目
        """
        pass
    
    @abstractmethod
    def get(self, name: str, default: Optional[T] = None) -> Optional[T]:
        """获取项目
        
        Args:
            name: 项目名称
            default: 默认值
            
        Returns:
            项目或默认值
        """
        pass
    
    @abstractmethod
    def has(self, name: str) -> bool:
        """检查项目是否存在
        
        Args:
            name: 项目名称
            
        Returns:
            是否存在
        """
        pass
    
    @abstractmethod
    def list(self) -> List[str]:
        """列出所有项目名称
        
        Returns:
            名称列表
        """
        pass
    
    @abstractmethod
    def remove(self, name: str) -> bool:
        """移除项目
        
        Args:
            name: 项目名称
            
        Returns:
            是否成功移除
        """
        pass


class SimpleRegistry(BaseRegistry[T]):
    """简单的内存注册表实现"""
    
    def __init__(self, name: str = "Registry"):
        self.name = name
        self._items: Dict[str, T] = {}
        logger.info(f"📋 {name} initialized")
    
    def register(self, name: str, item: T) -> None:
        self._items[name] = item
        logger.debug(f"📝 Registered: {name}")
    
    def get(self, name: str, default: Optional[T] = None) -> Optional[T]:
        return self._items.get(name, default)
    
    def has(self, name: str) -> bool:
        return name in self._items
    
    def list(self) -> List[str]:
        return list(self._items.keys())
    
    def remove(self, name: str) -> bool:
        if name in self._items:
            del self._items[name]
            logger.debug(f"🗑️ Removed: {name}")
            return True
        return False
    
    def clear(self) -> None:
        """清空注册表"""
        self._items.clear()
        logger.warning(f"⚠️ {self.name} cleared")
    
    def all(self) -> Dict[str, T]:
        """获取所有项目"""
        return dict(self._items)
    
    def count(self) -> int:
        """获取项目数量"""
        return len(self._items)
    
    def update(self, other: Dict[str, T]) -> None:
        """批量更新"""
        self._items.update(other)


class CallbackRegistry(SimpleRegistry[Callable]):
    """回调注册表 - 专门用于存储可调用对象"""
    
    def __init__(self, name: str = "CallbackRegistry"):
        super().__init__(name)
    
    def trigger(self, name: str, *args, **kwargs) -> Any:
        """触发回调
        
        Args:
            name: 回调名称
            *args: 位置参数
            **kwargs: 关键字参数
            
        Returns:
            回调返回值
        """
        callback = self.get(name)
        if callback:
            return callback(*args, **kwargs)
        return None


class TypeRegistry(SimpleRegistry[type]):
    """类型注册表 - 专门用于存储类"""
    
    def __init__(self, name: str = "TypeRegistry"):
        super().__init__(name)
    
    def create_instance(self, name: str, *args, **kwargs) -> Any:
        """创建实例
        
        Args:
            name: 类名称
            *args: 构造参数
            **kwargs: 构造参数
            
        Returns:
            实例
        """
        cls = self.get(name)
        if cls:
            return cls(*args, **kwargs)
        return None


# 装饰器支持
def register(registry: BaseRegistry, name: str = None):
    """装饰器用于自动注册
    
    Args:
        registry: 目标注册表
        name: 注册名称 (默认使用函数/类名)
        
    Returns:
        装饰器
    """
    def decorator(func_or_cls):
        reg_name = name or func_or_cls.__name__
        registry.register(reg_name, func_or_cls)
        return func_or_cls
    return decorator
