"""状态持久化管理器

提供运行状态的持久化存储和恢复功能，支持：
- JSON序列化存储状态
- 状态保存/恢复API
- 状态文件管理
- 运行时日志级别动态调整

技术选型：
- JSON序列化存储状态
- 文件系统持久化
- logging模块动态调整级别

架构设计：
- 状态持久化服务独立模块
- 统一异常处理
- 日志级别管理API
"""

import os
import json
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class StateSaveResult:
    """状态保存结果"""
    success: bool
    state_id: str
    file_path: str
    elapsed_ms: float
    error_message: Optional[str] = None
    data_size: int = 0
    created_at: Optional[str] = None


@dataclass
class StateLoadResult:
    """状态加载结果"""
    success: bool
    state_id: str
    state_data: Dict[str, Any]
    elapsed_ms: float
    error_message: Optional[str] = None
    file_path: Optional[str] = None


@dataclass
class StateInfo:
    """状态信息"""
    state_id: str
    file_path: str
    created_at: datetime
    data_size: int
    version: str = "1.0"


class StateManager:
    """状态持久化管理器
    
    负责状态的序列化存储和恢复，支持：
    - JSON格式状态文件
    - 状态版本管理
    - 自动备份机制
    - 日志级别动态调整
    """
    
    VERSION = "1.0"
    DEFAULT_STATE_DIR = "./data/state"
    DEFAULT_STATE_FILE = "agent_state.json"
    MAX_BACKUPS = 5
    FILE_EXTENSION = ".json"
    
    def __init__(self, state_dir: str = None, auto_save_interval: int = 60):
        """
        初始化状态管理器
        
        Args:
            state_dir: 状态文件存储目录
            auto_save_interval: 自动保存间隔（秒），0表示禁用自动保存
        """
        self._state_dir = Path(state_dir or self.DEFAULT_STATE_DIR)
        self._auto_save_interval = auto_save_interval
        self._current_state: Dict[str, Any] = {}
        self._last_save_time = 0.0
        self._lock = threading.Lock()
        self._auto_save_thread = None
        self._auto_save_running = False
        
        # 确保状态目录存在
        self._ensure_state_dir()
        
        logger.info(f"状态管理器初始化完成，目录: {self._state_dir}")
        
        # 启动自动保存线程（如果配置了）
        if self._auto_save_interval > 0:
            self._start_auto_save()
    
    def _ensure_state_dir(self):
        """确保状态目录存在"""
        self._state_dir.mkdir(parents=True, exist_ok=True)
    
    def _generate_state_id(self) -> str:
        """生成唯一状态ID"""
        return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    
    def _get_state_path(self, state_id: Optional[str] = None) -> Path:
        """获取状态文件路径"""
        if state_id:
            return self._state_dir / f"{state_id}{self.FILE_EXTENSION}"
        return self._state_dir / self.DEFAULT_STATE_FILE
    
    def _serialize_state(self, state: Dict[str, Any]) -> str:
        """序列化状态为JSON字符串"""
        return json.dumps(state, ensure_ascii=False, indent=2, default=self._json_default)
    
    def _deserialize_state(self, json_str: str) -> Dict[str, Any]:
        """反序列化JSON字符串为状态字典"""
        return json.loads(json_str)
    
    def _json_default(self, obj):
        """JSON序列化默认处理函数"""
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Path):
            return str(obj)
        if hasattr(obj, 'to_dict'):
            return obj.to_dict()
        if hasattr(obj, '__dict__'):
            return obj.__dict__
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
    
    def _create_backup(self, state_id: str):
        """创建状态备份"""
        try:
            current_path = self._get_state_path()
            if not current_path.exists():
                return
            
            backup_name = f"{state_id}_backup{self.FILE_EXTENSION}"
            backup_path = self._state_dir / backup_name
            
            with open(current_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # 清理旧备份
            self._cleanup_backups()
            
            logger.debug(f"状态备份已创建: {backup_path}")
        except Exception as e:
            logger.warning(f"创建状态备份失败: {e}")
    
    def _cleanup_backups(self):
        """清理旧备份，保留最新的MAX_BACKUPS个"""
        try:
            backups = sorted(
                self._state_dir.glob(f"*_backup{self.FILE_EXTENSION}"),
                key=lambda p: p.stat().st_ctime,
                reverse=True
            )
            
            if len(backups) > self.MAX_BACKUPS:
                for backup in backups[self.MAX_BACKUPS:]:
                    backup.unlink()
                    logger.debug(f"已删除旧备份: {backup}")
        except Exception as e:
            logger.warning(f"清理备份失败: {e}")
    
    def save_state(
        self,
        state_data: Dict[str, Any],
        state_id: Optional[str] = None,
        include_timestamp: bool = True
    ) -> StateSaveResult:
        """
        保存状态到文件
        
        Args:
            state_data: 要保存的状态数据
            state_id: 状态ID，自动生成如果为None，指定后保存到独立文件
            include_timestamp: 是否包含时间戳
            
        Returns:
            保存结果
        """
        start_time = time.time()
        
        with self._lock:
            try:
                logger.debug(f"开始保存状态，state_id: {state_id}, 数据键数量: {len(state_data.keys())}")
                
                # 生成状态ID
                if state_id is None:
                    state_id = self._generate_state_id()
                    logger.debug(f"自动生成状态ID: {state_id}")
                else:
                    logger.debug(f"使用自定义状态ID: {state_id}")
                
                # 添加元数据
                state_to_save = state_data.copy()
                if include_timestamp:
                    logger.debug("开始构建元数据...")
                    # 先序列化一次获取数据大小
                    temp_state = state_to_save.copy()
                    temp_state['_metadata'] = {
                        'state_id': state_id,
                        'version': self.VERSION,
                        'created_at': datetime.now(timezone.utc).isoformat(),
                        'data_size': 0
                    }
                    json_str_for_size = self._serialize_state(temp_state)
                    
                    state_to_save['_metadata'] = {
                        'state_id': state_id,
                        'version': self.VERSION,
                        'created_at': datetime.now(timezone.utc).isoformat(),
                        'data_size': len(json_str_for_size.encode('utf-8'))
                    }
                    logger.debug(f"元数据构建完成，版本: {self.VERSION}")
                
                # 序列化并写入文件
                logger.debug("开始序列化状态数据...")
                json_str = self._serialize_state(state_to_save)
                file_path = self._get_state_path(state_id)
                logger.debug(f"状态文件路径: {file_path}")
                
                # 创建备份（仅对默认状态文件）
                if state_id is None:
                    logger.debug("创建状态备份...")
                    self._create_backup(state_id)
                    logger.debug("备份创建完成")
                
                # 写入文件
                logger.debug(f"开始写入状态文件: {file_path}")
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(json_str)
                logger.debug(f"状态文件写入完成")
                
                # 更新当前状态
                self._current_state = state_to_save
                self._last_save_time = time.time()
                
                elapsed_ms = (time.time() - start_time) * 1000
                data_size = len(json_str.encode('utf-8'))
                
                logger.info(f"状态保存成功: {state_id}, 文件: {file_path}, 大小: {data_size} bytes, 数据键数量: {len(state_to_save.keys())}, 耗时: {elapsed_ms:.2f}ms")
                
                return StateSaveResult(
                    success=True,
                    state_id=state_id,
                    file_path=str(file_path),
                    elapsed_ms=elapsed_ms,
                    data_size=data_size,
                    created_at=state_to_save['_metadata']['created_at'] if include_timestamp else None
                )
            
            except Exception as e:
                elapsed_ms = (time.time() - start_time) * 1000
                error_msg = str(e)
                
                logger.error(f"状态保存失败: {error_msg}")
                logger.error(f"失败详情 - state_id: {state_id}, 数据键数量: {len(state_data.keys()) if state_data else 0}")
                
                return StateSaveResult(
                    success=False,
                    state_id=state_id or self._generate_state_id(),
                    file_path="",
                    elapsed_ms=elapsed_ms,
                    error_message=error_msg
                )
    
    def load_state(self, state_id: Optional[str] = None) -> StateLoadResult:
        """
        从文件加载状态
        
        Args:
            state_id: 状态ID，加载默认文件如果为None
            
        Returns:
            加载结果
        """
        start_time = time.time()
        
        with self._lock:
            try:
                logger.debug(f"开始加载状态，state_id: {state_id}")
                
                file_path = self._get_state_path(state_id)
                logger.debug(f"计算状态文件路径: {file_path}")
                
                if not file_path.exists():
                    logger.debug(f"指定的状态文件不存在: {file_path}")
                    # 尝试查找最新的状态文件
                    if state_id is None:
                        logger.debug("未指定state_id，尝试查找最新状态文件...")
                        latest_file = self._find_latest_state()
                        if latest_file:
                            file_path = latest_file
                            logger.debug(f"找到最新状态文件: {file_path}")
                        else:
                            logger.error("未找到任何状态文件")
                            return StateLoadResult(
                                success=False,
                                state_id="",
                                state_data={},
                                elapsed_ms=(time.time() - start_time) * 1000,
                                error_message="状态文件不存在"
                            )
                    else:
                        logger.error(f"指定的状态文件不存在: {file_path}")
                        return StateLoadResult(
                            success=False,
                            state_id=state_id,
                            state_data={},
                            elapsed_ms=(time.time() - start_time) * 1000,
                            error_message=f"状态文件不存在: {file_path}"
                        )
                
                # 读取并解析状态文件
                logger.debug(f"开始读取状态文件: {file_path}")
                file_size = file_path.stat().st_size
                logger.debug(f"文件大小: {file_size} bytes")
                
                with open(file_path, 'r', encoding='utf-8') as f:
                    json_str = f.read()
                logger.debug("状态文件读取完成")
                
                logger.debug("开始反序列化状态数据...")
                state_data = self._deserialize_state(json_str)
                logger.debug(f"状态反序列化完成，数据键数量: {len(state_data.keys())}")
                
                # 提取状态ID
                loaded_state_id = state_data.get('_metadata', {}).get('state_id', state_id or 'unknown')
                logger.debug(f"提取到状态ID: {loaded_state_id}")
                
                # 更新当前状态
                self._current_state = state_data
                logger.debug("当前内存状态已更新")
                
                elapsed_ms = (time.time() - start_time) * 1000
                
                logger.info(f"状态加载成功: {loaded_state_id}, 文件: {file_path}, 大小: {file_size} bytes, 数据键数量: {len(state_data.keys())}, 耗时: {elapsed_ms:.2f}ms")
                
                return StateLoadResult(
                    success=True,
                    state_id=loaded_state_id,
                    state_data=state_data,
                    elapsed_ms=elapsed_ms,
                    file_path=str(file_path)
                )
            
            except json.JSONDecodeError as e:
                elapsed_ms = (time.time() - start_time) * 1000
                error_msg = f"JSON解析错误: {e}"
                
                logger.error(f"状态加载失败: {error_msg}")
                logger.error(f"失败详情 - state_id: {state_id}, 文件路径: {file_path if 'file_path' in dir() else '未知'}")
                
                return StateLoadResult(
                    success=False,
                    state_id=state_id or "unknown",
                    state_data={},
                    elapsed_ms=elapsed_ms,
                    error_message=error_msg
                )
            except Exception as e:
                elapsed_ms = (time.time() - start_time) * 1000
                error_msg = str(e)
                
                logger.error(f"状态加载失败: {error_msg}")
                logger.error(f"失败详情 - state_id: {state_id}, 文件路径: {file_path if 'file_path' in dir() else '未知'}")
                
                return StateLoadResult(
                    success=False,
                    state_id=state_id or "unknown",
                    state_data={},
                    elapsed_ms=elapsed_ms,
                    error_message=error_msg
                )
    
    def _find_latest_state(self) -> Optional[Path]:
        """查找最新的状态文件"""
        try:
            state_files = sorted(
                self._state_dir.glob(f"*{self.FILE_EXTENSION}"),
                key=lambda p: p.stat().st_ctime,
                reverse=True
            )
            
            # 排除备份文件
            for file in state_files:
                if "_backup" not in file.name:
                    return file
            
            return None
        except Exception as e:
            logger.warning(f"查找最新状态文件失败: {e}")
            return None
    
    def list_states(self) -> List[StateInfo]:
        """列出所有可用的状态文件"""
        states = []
        
        try:
            state_files = sorted(
                self._state_dir.glob(f"*{self.FILE_EXTENSION}"),
                key=lambda p: p.stat().st_ctime,
                reverse=True
            )
            
            for file_path in state_files:
                # 排除备份文件
                if "_backup" in file_path.name:
                    continue
                
                try:
                    stat = file_path.stat()
                    created_at = datetime.fromtimestamp(stat.st_ctime, timezone.utc)
                    
                    # 尝试读取版本信息
                    version = "1.0"
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            version = data.get('_metadata', {}).get('version', '1.0')
                    except:
                        pass
                    
                    states.append(StateInfo(
                        state_id=file_path.stem,
                        file_path=str(file_path),
                        created_at=created_at,
                        data_size=stat.st_size,
                        version=version
                    ))
                except Exception:
                    continue
                
        except Exception as e:
            logger.error(f"列出状态文件失败: {e}")
        
        return states
    
    def delete_state(self, state_id: str) -> bool:
        """删除指定状态文件"""
        try:
            file_path = self._get_state_path(state_id)
            if file_path.exists():
                file_path.unlink()
                logger.info(f"状态文件已删除: {file_path}")
                return True
            return False
        except Exception as e:
            logger.error(f"删除状态文件失败: {e}")
            return False
    
    def get_current_state(self) -> Dict[str, Any]:
        """获取当前内存中的状态"""
        with self._lock:
            return self._current_state.copy()
    
    def update_state(self, updates: Dict[str, Any]) -> None:
        """更新当前状态（增量更新）"""
        with self._lock:
            self._current_state.update(updates)
    
    def clear_state(self) -> None:
        """清除当前状态"""
        with self._lock:
            self._current_state = {}
    
    def get_last_save_time(self) -> float:
        """获取上次保存时间（时间戳）"""
        return self._last_save_time
    
    # ════════════════════════════════════════════════════════════════════════════
    #  日志级别管理 API
    # ════════════════════════════════════════════════════════════════════════════
    
    def set_log_level(self, level: str, logger_name: str = None) -> bool:
        """
        动态调整日志级别
        
        Args:
            level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            logger_name: 日志记录器名称，None表示根日志记录器
            
        Returns:
            是否成功
        """
        try:
            # 转换为logging模块的级别常量
            level_constant = getattr(logging, level.upper(), None)
            if level_constant is None:
                logger.error(f"无效的日志级别: {level}")
                return False
            
            # 获取目标日志记录器
            target_logger = logging.getLogger(logger_name) if logger_name else logging.getLogger()
            old_level = logging.getLevelName(target_logger.level)
            
            # 设置新级别
            target_logger.setLevel(level_constant)
            
            # 如果设置的是根日志记录器，同时更新所有处理器的级别
            if not logger_name:
                for handler in target_logger.handlers:
                    handler.setLevel(level_constant)
            
            logger.info(f"日志级别已调整: {logger_name or 'root'} 从 {old_level} 改为 {level.upper()}")
            return True
        
        except Exception as e:
            logger.error(f"设置日志级别失败: {e}")
            return False
    
    def get_log_level(self, logger_name: str = None) -> str:
        """
        获取当前日志级别
        
        Args:
            logger_name: 日志记录器名称，None表示根日志记录器
            
        Returns:
            当前日志级别字符串
        """
        target_logger = logging.getLogger(logger_name) if logger_name else logging.getLogger()
        return logging.getLevelName(target_logger.level)
    
    def list_loggers(self) -> List[Tuple[str, str]]:
        """
        列出所有已注册的日志记录器及其级别
        
        Returns:
            日志记录器名称和级别列表
        """
        loggers = []
        for name, logger_obj in logging.root.manager.loggerDict.items():
            if isinstance(logger_obj, logging.Logger):
                level = logging.getLevelName(logger_obj.level)
                loggers.append((name, level))
        
        # 添加根日志记录器
        root_level = logging.getLevelName(logging.root.level)
        loggers.insert(0, ('root', root_level))
        
        return loggers
    
    # ════════════════════════════════════════════════════════════════════════════
    #  自动保存功能
    # ════════════════════════════════════════════════════════════════════════════
    
    def _start_auto_save(self):
        """启动自动保存线程"""
        if self._auto_save_thread is not None:
            return
        
        self._auto_save_running = True
        
        def auto_save_loop():
            while self._auto_save_running:
                try:
                    time.sleep(self._auto_save_interval)
                    if self._current_state:
                        self.save_state(self._current_state)
                except Exception as e:
                    logger.error(f"自动保存异常: {e}")
        
        self._auto_save_thread = threading.Thread(target=auto_save_loop, daemon=True)
        self._auto_save_thread.start()
        
        logger.info(f"自动保存已启用，间隔: {self._auto_save_interval}秒")
    
    def stop_auto_save(self):
        """停止自动保存线程"""
        self._auto_save_running = False
        if self._auto_save_thread:
            self._auto_save_thread.join(timeout=5)
            self._auto_save_thread = None
        logger.info("自动保存已停止")
    
    def set_auto_save_interval(self, interval: int):
        """设置自动保存间隔"""
        self._auto_save_interval = interval
        if self._auto_save_running:
            self.stop_auto_save()
            self._start_auto_save()
        logger.info(f"自动保存间隔已更新为: {interval}秒")


# 全局状态管理器实例
_global_state_manager: Optional[StateManager] = None


def get_state_manager() -> StateManager:
    """获取全局状态管理器（单例）"""
    global _global_state_manager
    if _global_state_manager is None:
        _global_state_manager = StateManager()
    return _global_state_manager


# 便捷函数
def save_state(state_data: Dict[str, Any], **kwargs) -> StateSaveResult:
    """便捷函数：保存状态"""
    return get_state_manager().save_state(state_data, **kwargs)


def load_state(state_id: Optional[str] = None) -> StateLoadResult:
    """便捷函数：加载状态"""
    return get_state_manager().load_state(state_id)


def set_log_level(level: str, logger_name: Optional[str] = None) -> bool:
    """便捷函数：设置日志级别"""
    return get_state_manager().set_log_level(level, logger_name)


def get_log_level(logger_name: Optional[str] = None) -> str:
    """便捷函数：获取日志级别"""
    return get_state_manager().get_log_level(logger_name)


# ════════════════════════════════════════════════════════════════════════════
#  app_server 共享全局状态（从 server_state.py 迁入）
# ════════════════════════════════════════════════════════════════════════════


class ServerState:
    """app_server 全局状态容器"""

    def __init__(self):
        # ── DigitalLife 实例 ──
        self.Yunshu = None

        # ── 安全/权限 ──
        self.safety_guard = None
        self.permission_toggles = {}

        # ── UI 管理器 ──
        self.personality_mgr = None
        self.skills_mgr = None
        self.action_tracker = None

        # ── 会话 ──
        self.session_mgr = None

        # ── 网络/工具 ──
        self.http_client = None
        self.scraper = None
        self.search_engine = None
        self.processor = None
        self.crawler_controller = None
        self.network_config_mgr = None

        # ── 扩展 ──
        self.extension_mgr = None
        self.extension_market = None

        # ── 传感器 ──
        self.window_sensor = None
        self.window_sensor_consented = False

        # ── 工具 ──
        self.tools = None
        self.agent_tools = None

        # ── 告警 ──
        self.alert_queue = []

        # ── 工作区 ──
        self.workspace_path = None

        # ── 对话缓存（向后兼容） ──
        self.chat_history = []


# 全局 ServerState 单例
_server_state = ServerState()


def get_server_state() -> ServerState:
    """获取全局 ServerState 实例（app_server 各路由模块共享）"""
    return _server_state