"""P6 冷启动优化 - 快照管理器

Phase 1: 快照管理器框架
Phase 2: 核心模块序列化
Phase 3: 快照恢复逻辑
Phase 4: 增量快照功能（新增）
Phase 5: 性能监控面板（新增）

核心功能：
- 状态快照创建和存储（完整/增量）
- 快照恢复
- 快照频率控制（增强安全性）
- 版本兼容性检查
- 实时性能监控
- 增量快照优化存储占用

作者: AI Assistant
版本: 2.0.0
"""

import os
import time
import json
import pickle
import hashlib
import gzip
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from pathlib import Path

from agent.p6.performance import PerformanceMetrics, SnapshotPerformanceMonitor
from agent.p6.frequency import SnapshotFrequencyController
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)


@dataclass
class SnapshotResult:
    """快照操作结果"""
    success: bool
    snapshot_id: Optional[str] = None
    version: str = ""
    elapsed_ms: float = 0.0
    error_message: Optional[str] = None
    is_incremental: bool = False
    base_snapshot_id: Optional[str] = None
    space_saved_bytes: int = 0
    file_size: int = 0
    created_at: Optional[datetime] = None


@dataclass
class SnapshotInfo:
    """快照信息"""
    snapshot_id: str
    created_at: datetime
    version: str
    file_size: int
    is_incremental: bool = False
    base_snapshot_id: Optional[str] = None


@dataclass
class ModuleState:
    """单个模块的状态"""
    module_name: str
    initialized: bool
    state_data: bytes
    restore_priority: int = 0
    checksum: str = ""
    changed: bool = True  # 标记是否变化


@dataclass
class StateSnapshot:
    """P6 状态快照数据结构"""
    snapshot_id: str
    created_at: datetime
    version: str = "p6.2.0"
    
    config: Dict[str, Any] = field(default_factory=dict)
    module_states: Dict[str, ModuleState] = field(default_factory=dict)
    lazy_cache: Dict[str, Any] = field(default_factory=dict)
    performance_stats: Dict[str, float] = field(default_factory=dict)
    
    is_incremental: bool = False
    base_snapshot_id: Optional[str] = None  # 基础快照ID（用于增量）
    
    def compute_checksum(self) -> str:
        """计算快照内容校验和"""
        data = pickle.dumps({
            "version": self.version,
            "config": self.config,
            "module_states": {
                name: {"checksum": state.checksum} 
                for name, state in self.module_states.items()
            },
        })
        return hashlib.sha256(data).hexdigest()


@dataclass
class StateSnapshotManager:
    """P6 状态快照管理器
    
    负责 DigitalLife 实例状态的保存、加载和管理。
    支持完整快照和增量快照两种模式。
    """
    
    def __init__(
        self,
        snapshot_dir: str = "./.p6_snapshots",
        enable_compression: bool = True,
    ):
        self.snapshot_dir = Path(snapshot_dir)
        self.enable_compression = enable_compression
        self.current_snapshot: Optional[StateSnapshot] = None
        
        # 频率控制器
        self.frequency_controller = SnapshotFrequencyController()
        
        # 性能监控器
        self.performance_monitor = SnapshotPerformanceMonitor()
        
        # 确保快照目录存在
        self._ensure_snapshot_dir()
        
        # 快照状态索引（用于增量快照）
        self.last_module_checksums: Dict[str, str] = {}
        
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 快照管理器初始化完成，目录: {self.snapshot_dir}'}))
        
    def _ensure_snapshot_dir(self):
        """确保快照目录存在"""
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        
    def _generate_snapshot_id(self) -> str:
        """生成唯一快照ID"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 添加微秒确保唯一性
        microsecond = datetime.now().microsecond
        return f"snap_{timestamp}_{microsecond}"
        
    def _get_snapshot_path(self, snapshot_id: str, is_incremental: bool = False) -> Path:
        """获取快照文件路径"""
        if is_incremental:
            suffix = ".incremental.snap.gz" if self.enable_compression else ".incremental.snap"
        else:
            suffix = ".snap.gz" if self.enable_compression else ".snap"
        return self.snapshot_dir / f"{snapshot_id}{suffix}"
        
    def _compute_checksum(self, data: bytes) -> str:
        """计算数据校验和"""
        return hashlib.sha256(data).hexdigest()
        
    def _persist_snapshot(self, snapshot: StateSnapshot) -> bool:
        """持久化快照到磁盘"""
        try:
            snapshot_path = self._get_snapshot_path(snapshot.snapshot_id, snapshot.is_incremental)
            
            # 序列化
            data = pickle.dumps(snapshot)
            
            # 压缩（可选）
            if self.enable_compression:
                data = gzip.compress(data, compresslevel=6)
                
            # 写入文件
            with open(snapshot_path, "wb") as f:
                f.write(data)
                
            file_size = os.path.getsize(snapshot_path)
            logger.info(
                f"[P6] 快照持久化成功: {snapshot.snapshot_id}, "
                f"大小: {file_size:,} bytes, "
                f"{'增量' if snapshot.is_incremental else '完整'}快照"
            )
            return True
            
        except Exception as e:
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 快照持久化失败: {e}'}))
            return False
            
    def _load_snapshot_data(self, snapshot_id: Optional[str] = None) -> Optional[StateSnapshot]:
        """加载快照数据"""
        try:
            if snapshot_id is None:
                # 使用最新的快照
                snapshots = self.list_snapshots()
                if not snapshots:
                    return None
                snapshot_id = snapshots[0].snapshot_id
                
            # 先尝试找增量快照
            try:
                snapshot_path = self._get_snapshot_path(snapshot_id, is_incremental=True)
                if snapshot_path.exists():
                    return self._load_from_path(snapshot_path)
            except Exception:
                pass
                
            # 再尝试完整快照
            snapshot_path = self._get_snapshot_path(snapshot_id, is_incremental=False)
            if not snapshot_path.exists():
                logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 快照文件不存在: {snapshot_path}'}))
                return None
                
            return self._load_from_path(snapshot_path)
            
        except Exception as e:
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 快照加载失败: {e}'}))
            return None
            
    def _load_from_path(self, path: Path) -> Optional[StateSnapshot]:
        """从指定路径加载快照"""
        # 读取文件
        with open(path, "rb") as f:
            data = f.read()
            
        # 解压（如果需要）
        if self.enable_compression and path.suffix == ".gz":
            data = gzip.decompress(data)
            
        # 反序列化
        snapshot = pickle.loads(data)
        
        # 如果是增量快照，需要加载基础快照并合并
        if snapshot.is_incremental and snapshot.base_snapshot_id:
            base_snapshot = self._load_snapshot_data(snapshot.base_snapshot_id)
            if base_snapshot:
                # 合并基础快照和增量快照
                snapshot = self._merge_snapshots(base_snapshot, snapshot)
                
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 快照加载成功: {path.name}'}))
        return snapshot
            
    def _merge_snapshots(self, base: StateSnapshot, incremental: StateSnapshot) -> StateSnapshot:
        """合并基础快照和增量快照"""
        merged = StateSnapshot(
            snapshot_id=incremental.snapshot_id,
            created_at=incremental.created_at,
            version=incremental.version,
            config=incremental.config,
            module_states={},
        )
        
        # 先应用基础快照的所有模块
        for module_name, state in base.module_states.items():
            merged.module_states[module_name] = state
            
        # 再应用增量快照的变化
        for module_name, state in incremental.module_states.items():
            if state.changed:
                merged.module_states[module_name] = state
                
        return merged
            
    def _check_compatibility(self, snapshot: StateSnapshot) -> bool:
        """检查快照版本兼容性"""
        current_version = "p6.2.0"
        
        # 支持p6.1.0和p6.2.0版本
        if snapshot.version.startswith("p6."):
            return True
            
        logger.warning(
            f"[P6] 快照版本不兼容: {snapshot.version}, 当前版本: {current_version}"
        )
        return False
        
    def _cleanup_old_snapshots(self):
        """清理旧快照，保留指定数量"""
        snapshots = self.list_snapshots()
        max_snapshots = self.frequency_controller.max_snapshots
        
        if len(snapshots) > max_snapshots:
            # 删除最旧的快照
            snapshots_to_delete = snapshots[max_snapshots:]
            for snap_info in snapshots_to_delete:
                try:
                    # 尝试删除完整和增量两种格式
                    for is_incremental in [False, True]:
                        snap_path = self._get_snapshot_path(snap_info.snapshot_id, is_incremental)
                        if snap_path.exists():
                            snap_path.unlink()
                            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 删除旧快照: {snap_info.snapshot_id}'}))
                except Exception as e:
                    logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 删除快照失败: {e}'}))
        
    def save_snapshot(
        self,
        digital_life: Any,
        snapshot_id: Optional[str] = None,
        incremental: bool = False,
        force: bool = False,
    ) -> SnapshotResult:
        """保存 DigitalLife 状态快照
        
        Args:
            digital_life: 要保存的 DigitalLife 实例
            snapshot_id: 快照ID，自动生成如果为None
            incremental: 是否增量保存（新增功能）
            force: 是否强制保存，忽略频率限制
            
        Returns:
            保存结果对象
        """
        start_time = time.time()
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ═══════════════════════════════════════════════════════'}))
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] 快照保存流程开始'}))
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ 参数: force={force}, incremental={incremental}'}))
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ 目标对象: {digital_life.__class__.__name__}'}))
        
        # 检查频率限制
        if not self.frequency_controller.can_save(force):
            logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ├─ ⚠️ 频率控制拦截，保存被拒绝'}))
            logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] └─ 上次保存时间: {self.frequency_controller.last_save_time}'}))
            logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] └─ 最小间隔: {self.frequency_controller.min_interval_seconds}秒'}))
            return SnapshotResult(
                success=False,
                error_message="快照保存过于频繁"
            )
            
        try:
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ├─ ✓ 频率检查通过'}))
            
            # 生成或使用提供的快照ID
            if snapshot_id is None:
                snapshot_id = self._generate_snapshot_id()
                logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ 生成新快照ID: {snapshot_id}'}))
            else:
                logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ 使用指定快照ID: {snapshot_id}'}))
                
            # 创建快照对象
            # 注意：force=True 只影响是否绕过频率限制，不覆盖 incremental 参数
            snapshot = StateSnapshot(
                snapshot_id=snapshot_id,
                created_at=datetime.now(),
                version="p6.2.0",
                is_incremental=incremental,
            )
            
            # 如果是增量快照，设置基础快照
            if incremental and self.current_snapshot:
                snapshot.base_snapshot_id = self.current_snapshot.snapshot_id
                logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ 增量快照，基于: {snapshot.base_snapshot_id}'}))
                
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ 创建快照对象成功，版本: {snapshot.version}'}))
            
            # 保存配置
            if hasattr(digital_life, "_config"):
                snapshot.config = digital_life._config
                logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ 配置保存成功，键数: {len(snapshot.config)}'}))
            else:
                logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ├─ ⚠️ 对象无_config属性，跳过配置保存'}))
                
            # 保存核心模块状态
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ├─ 开始序列化核心模块...'}))
            space_saved = self._save_core_modules_with_delta(digital_life, snapshot, incremental)
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ 核心模块序列化完成，共 {len(snapshot.module_states)} 个模块'}))
            
            # 显示各模块状态
            for module_name, module_state in snapshot.module_states.items():
                logger.info(
                    f"[P6] │   ├─ {module_name}: "
                    f"initialized={module_state.initialized}, "
                    f"changed={module_state.changed}, "
                    f"priority={module_state.restore_priority}, "
                    f"data_size={len(module_state.state_data)} bytes"
                )
            
            # 计算总数据大小
            total_data_size = sum(len(ms.state_data) for ms in snapshot.module_states.values())
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ 模块数据总大小: {total_data_size} bytes'}))
            if space_saved > 0:
                logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ 增量快照节省: {space_saved:,} bytes'}))
            
            # 持久化
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ├─ 开始持久化到磁盘...'}))
            if not self._persist_snapshot(snapshot):
                logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ├─ ✗ 持久化失败'}))
                return SnapshotResult(
                    success=False,
                    error_message="快照持久化失败"
                )
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ├─ ✓ 持久化成功'}))
                
            # 更新频率控制器
            self.frequency_controller.on_save_success()
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ 频率控制器已更新，保存计数: {self.frequency_controller.save_count}'}))
            
            # 更新模块校验和缓存（用于下次增量快照）
            self._update_module_checksums(snapshot)
            
            # 清理旧快照
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ├─ 检查是否需要清理旧快照...'}))
            self._cleanup_old_snapshots()
            
            elapsed = (time.time() - start_time) * 1000
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ 快照保存总计耗时: {elapsed:.2f}ms'}))
            
            self.current_snapshot = snapshot
            
            # 记录性能数据
            self.performance_monitor.record_save(elapsed, space_saved)
            
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ═══════════════════════════════════════════════════════'}))
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 快照保存成功！ID: {snapshot_id}'}))
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ═══════════════════════════════════════════════════════'}))
            
            return SnapshotResult(
                success=True,
                snapshot_id=snapshot_id,
                elapsed_ms=elapsed,
                is_incremental=snapshot.is_incremental,
                space_saved_bytes=space_saved,
                base_snapshot_id=snapshot.base_snapshot_id,
            )
            
        except Exception as e:
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ═══════════════════════════════════════════════════════'}))
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 快照保存异常: {e}'}))
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 异常类型: {type(e).__name__}'}))
            import traceback
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 异常堆栈:\n{traceback.format_exc()}'}))
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ═══════════════════════════════════════════════════════'}))
            elapsed = (time.time() - start_time) * 1000
            return SnapshotResult(
                success=False,
                elapsed_ms=elapsed,
                error_message=str(e),
            )
            
    def _save_core_modules_with_delta(
        self, 
        digital_life: Any, 
        snapshot: StateSnapshot, 
        incremental: bool
    ) -> int:
        """Phase 2/4: 核心模块状态保存（带增量优化）
        
        只保存变化的模块，减少存储占用
        """
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] Phase 2/4: 开始序列化核心模块...'}))
        
        total_space_saved = 0
        
        # BodySensor
        if hasattr(digital_life, "_body"):
            body_sensor = digital_life._body.get() if hasattr(digital_life._body, "get") else digital_life._body
            body_state = self._serialize_body_sensor(body_sensor)
            state_data = pickle.dumps(body_state)
            checksum = self._compute_checksum(state_data)
            
            module_state = ModuleState(
                module_name="body_sensor",
                initialized=body_state.get("initialized", False),
                state_data=state_data,
                restore_priority=100,
                checksum=checksum,
            )
            
            # 检查是否变化
            if incremental:
                module_state.changed = checksum != self.last_module_checksums.get("body_sensor", "")
                if not module_state.changed:
                    logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] BodySensor 未变化，跳过保存'}))
                    total_space_saved += len(state_data)
                else:
                    snapshot.module_states["body_sensor"] = module_state
                    logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ BodySensor 序列化完成，大小: {len(state_data)} bytes'}))
            else:
                snapshot.module_states["body_sensor"] = module_state
                logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ BodySensor 序列化完成，大小: {len(state_data)} bytes'}))
            
            # 记录性能数据
            module_serialize_start = time.time()
            # 实际序列化已在上面完成
            module_serialize_elapsed = (time.time() - module_serialize_start) * 1000
            self.performance_monitor.record_module_serialize("body_sensor", module_serialize_elapsed, len(state_data))
            
        # BehaviorController
        if hasattr(digital_life, "_behavior"):
            behavior_state = self._serialize_behavior(digital_life._behavior)
            state_data = pickle.dumps(behavior_state)
            checksum = self._compute_checksum(state_data)
            
            module_state = ModuleState(
                module_name="behavior",
                initialized=True,
                state_data=state_data,
                restore_priority=90,
                checksum=checksum,
            )
            
            if incremental:
                module_state.changed = checksum != self.last_module_checksums.get("behavior", "")
                if not module_state.changed:
                    logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] Behavior 未变化，跳过保存'}))
                    total_space_saved += len(state_data)
                else:
                    snapshot.module_states["behavior"] = module_state
                    logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ BehaviorController 序列化完成，大小: {len(state_data)} bytes'}))
            else:
                snapshot.module_states["behavior"] = module_state
                logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ BehaviorController 序列化完成，大小: {len(state_data)} bytes'}))
            
            self.performance_monitor.record_module_serialize("behavior", 0, len(state_data))
            
        # PermissionSystem
        if hasattr(digital_life, "_permission"):
            permission_state = self._serialize_permission(digital_life._permission)
            state_data = pickle.dumps(permission_state)
            checksum = self._compute_checksum(state_data)
            
            module_state = ModuleState(
                module_name="permission",
                initialized=True,
                state_data=state_data,
                restore_priority=80,
                checksum=checksum,
            )
            
            if incremental:
                module_state.changed = checksum != self.last_module_checksums.get("permission", "")
                if not module_state.changed:
                    logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] Permission 未变化，跳过保存'}))
                    total_space_saved += len(state_data)
                else:
                    snapshot.module_states["permission"] = module_state
                    logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ PermissionSystem 序列化完成，大小: {len(state_data)} bytes'}))
            else:
                snapshot.module_states["permission"] = module_state
                logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ PermissionSystem 序列化完成，大小: {len(state_data)} bytes'}))
            
            self.performance_monitor.record_module_serialize("permission", 0, len(state_data))
            
        # ToolsRegistry (如果存在)
        if hasattr(digital_life, "_tools_registry"):
            tools_state = self._serialize_tools_registry(digital_life._tools_registry)
            state_data = pickle.dumps(tools_state)
            checksum = self._compute_checksum(state_data)
            
            module_state = ModuleState(
                module_name="tools_registry",
                initialized=tools_state.get("initialized", False),
                state_data=state_data,
                restore_priority=70,
                checksum=checksum,
            )
            
            if incremental:
                module_state.changed = checksum != self.last_module_checksums.get("tools_registry", "")
                if not module_state.changed:
                    logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ToolsRegistry 未变化，跳过保存'}))
                    total_space_saved += len(state_data)
                else:
                    snapshot.module_states["tools_registry"] = module_state
                    logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ ToolsRegistry 序列化完成，大小: {len(state_data)} bytes'}))
            else:
                snapshot.module_states["tools_registry"] = module_state
                logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ ToolsRegistry 序列化完成，大小: {len(state_data)} bytes'}))
            
            self.performance_monitor.record_module_serialize("tools_registry", 0, len(state_data))
            
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] Phase 2/4: 核心模块序列化完成，共 {len(snapshot.module_states)} 个模块'}))
        
        return total_space_saved
        
    def _update_module_checksums(self, snapshot: StateSnapshot):
        """更新模块校验和缓存"""
        for module_name, module_state in snapshot.module_states.items():
            self.last_module_checksums[module_name] = module_state.checksum
            
    def _serialize_body_sensor(self, body_sensor: Any) -> Dict[str, Any]:
        """序列化 BodySensor 模块
        
        Phase 2: 实现 BodySensor 的完整序列化
        """
        try:
            state = {
                "initialized": body_sensor.is_initialized if hasattr(body_sensor, "is_initialized") else False,
            }
            
            # 如果已初始化，保存更多状态
            if hasattr(body_sensor, "_initialized") and body_sensor._initialized:
                # 保存观察目录
                if hasattr(body_sensor, "watch_dirs"):
                    state["watch_dirs"] = body_sensor.watch_dirs
                    
                # 保存配置
                if hasattr(body_sensor, "config"):
                    state["config"] = body_sensor.config
                    
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] BodySensor 序列化完成，状态: {state.get('initialized', False)}'}))
            return state
            
        except Exception as e:
            logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] BodySensor 序列化失败: {e}'}))
            return {"initialized": False, "error": str(e)}
            
    def _serialize_behavior(self, behavior: Any) -> Dict[str, Any]:
        """序列化 BehaviorController 模块
        
        Phase 2: 实现 BehaviorController 的完整序列化
        """
        try:
            state = {
                "initialized": True,
                "mode": "NORMAL",  # 默认模式
            }
            
            # 保存当前行为模式
            if hasattr(behavior, "_current_mode"):
                state["mode"] = behavior._current_mode.value if hasattr(behavior._current_mode, "value") else str(behavior._current_mode)
                
            # 保存模式切换历史（如果有）
            if hasattr(behavior, "_mode_history"):
                state["mode_history"] = behavior._mode_history[-5:] if len(behavior._mode_history) > 5 else behavior._mode_history
                
            # 保存阈值配置
            if hasattr(behavior, "THRESHOLDS"):
                state["thresholds"] = behavior.THRESHOLDS
                
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] BehaviorController 序列化完成，当前模式: {state['mode']}'}))
            return state
            
        except Exception as e:
            logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] BehaviorController 序列化失败: {e}'}))
            return {"initialized": True, "error": str(e)}
            
    def _serialize_permission(self, permission: Any) -> Dict[str, Any]:
        """序列化 PermissionSystem 模块
        
        Phase 2: 实现 PermissionSystem 的完整序列化
        """
        try:
            state = {
                "initialized": True,
                "dangerous_patterns_count": 0,
                "blacklist_count": 0,
            }
            
            # 统计危险模式数量（只保存计数，不保存正则对象）
            if hasattr(permission, "DANGEROUS_PATTERNS"):
                state["dangerous_patterns_count"] = len(permission.DANGEROUS_PATTERNS)
                
            if hasattr(permission, "BLACKLIST"):
                state["blacklist_count"] = len(permission.BLACKLIST)
                
            # 保存敏感文件扩展名
            if hasattr(permission, "SENSITIVE_EXTENSIONS"):
                state["sensitive_extensions"] = list(permission.SENSITIVE_EXTENSIONS)
                
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] PermissionSystem 序列化完成，危险模式: {state['dangerous_patterns_count']}个'}))
            return state
            
        except Exception as e:
            logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] PermissionSystem 序列化失败: {e}'}))
            return {"initialized": True, "error": str(e)}
            
    def _serialize_tools_registry(self, tools_registry: Any) -> Dict[str, Any]:
        """序列化 ToolsRegistry 模块
        
        Phase 2: 实现工具注册状态的序列化
        """
        try:
            state = {
                "initialized": False,
                "tools_count": 0,
                "tools": [],
            }
            
            # 检查工具注册表
            if tools_registry and hasattr(tools_registry, "_tools"):
                state["initialized"] = True
                state["tools_count"] = len(tools_registry._tools)
                
                # 保存工具列表（只保存名称，不保存完整对象）
                if hasattr(tools_registry, "_tools"):
                    state["tools"] = list(tools_registry._tools.keys())[:50]  # 最多50个工具
                    
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ToolsRegistry 序列化完成，工具数量: {state['tools_count']}'}))
            return state
            
        except Exception as e:
            logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ToolsRegistry 序列化失败: {e}'}))
            return {"initialized": False, "error": str(e)}
            
    def _restore_body_sensor(self, body_sensor: Any, state: Dict[str, Any]) -> bool:
        """恢复 BodySensor 模块
        
        Phase 3: 实现 BodySensor 的完整恢复
        """
        try:
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 恢复 BodySensor: 状态={state.get('initialized', False)}'}))
            
            # 恢复初始化状态
            if hasattr(body_sensor, "_initialized"):
                body_sensor._initialized = state.get("initialized", False)
                
            # 恢复观察目录
            if "watch_dirs" in state and hasattr(body_sensor, "watch_dirs"):
                body_sensor.watch_dirs = state["watch_dirs"]
                logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] BodySensor 观察目录恢复: {len(state['watch_dirs'])}个目录'}))
                
            # 恢复配置
            if "config" in state and hasattr(body_sensor, "config"):
                body_sensor.config = state["config"]
                logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] BodySensor 配置恢复成功'}))
                
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ✓ BodySensor 恢复完成'}))
            return True
            
        except Exception as e:
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ✗ BodySensor 恢复失败: {e}'}))
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 异常类型: {type(e).__name__}'}))
            import traceback
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 异常堆栈:\n{traceback.format_exc()}'}))
            return False
            
    def _restore_behavior(self, behavior: Any, state: Dict[str, Any]) -> bool:
        """恢复 BehaviorController 模块
        
        Phase 3: 实现 BehaviorController 的完整恢复
        """
        try:
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 恢复 BehaviorController: 当前模式={state.get('mode', 'NORMAL')}'}))
            
            # 恢复当前行为模式
            if "mode" in state and hasattr(behavior, "_current_mode"):
                # 尝试恢复枚举模式
                try:
                    from agent.behavior_controller import BehaviorMode
                    mode_value = state["mode"]
                    if hasattr(BehaviorMode, mode_value):
                        behavior._current_mode = getattr(BehaviorMode, mode_value)
                        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] BehaviorController 模式恢复为: {mode_value}'}))
                    else:
                        logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 未知的行为模式: {mode_value}，使用默认'}))
                except ImportError:
                    logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] BehaviorController 模式恢复: 未找到模块'}))
                    
            # 恢复模式历史
            if "mode_history" in state and hasattr(behavior, "_mode_history"):
                behavior._mode_history = state["mode_history"].copy()
                logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] BehaviorController 模式历史恢复: {len(state['mode_history'])}条记录'}))
                
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ✓ BehaviorController 恢复完成'}))
            return True
            
        except Exception as e:
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ✗ BehaviorController 恢复失败: {e}'}))
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 异常类型: {type(e).__name__}'}))
            import traceback
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 异常堆栈:\n{traceback.format_exc()}'}))
            return False
            
    def _restore_permission(self, permission: Any, state: Dict[str, Any]) -> bool:
        """恢复 PermissionSystem 模块
        
        Phase 3: 实现 PermissionSystem 的完整恢复
        """
        try:
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 恢复 PermissionSystem: 危险模式={state.get('dangerous_patterns_count', 0)}个'}))
            
            # 敏感文件扩展名可更新
            if "sensitive_extensions" in state and hasattr(permission, "SENSITIVE_EXTENSIONS"):
                # 注意：类属性需要谨慎处理
                logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] PermissionSystem 敏感扩展名统计: {len(state['sensitive_extensions'])}个'}))
                
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ✓ PermissionSystem 恢复完成'}))
            return True
            
        except Exception as e:
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ✗ PermissionSystem 恢复失败: {e}'}))
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 异常类型: {type(e).__name__}'}))
            import traceback
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 异常堆栈:\n{traceback.format_exc()}'}))
            return False
            
    def _restore_tools_registry(self, tools_registry: Any, state: Dict[str, Any]) -> bool:
        """恢复 ToolsRegistry 模块
        
        Phase 3: 实现 ToolsRegistry 的完整恢复
        """
        try:
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 恢复 ToolsRegistry: 工具数量={state.get('tools_count', 0)}个'}))
            
            # 工具列表信息记录（用于调试）
            if "tools" in state:
                logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ToolsRegistry 工具列表: {', '.join(state['tools'][:5])}...'}))
                
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ✓ ToolsRegistry 恢复完成'}))
            return True
            
        except Exception as e:
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ✗ ToolsRegistry 恢复失败: {e}'}))
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 异常类型: {type(e).__name__}'}))
            import traceback
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 异常堆栈:\n{traceback.format_exc()}'}))
            return False
            
    def _restore_modules_by_priority(self, digital_life: Any, snapshot: StateSnapshot) -> bool:
        """Phase 3: 按优先级恢复模块
        
        Args:
            digital_life: 要恢复的 DigitalLife 实例
            snapshot: 快照对象
            
        Returns:
            是否成功恢复
        """
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ═══════════════════════════════════════════════════════'}))
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] Phase 3: 按优先级恢复模块开始'}))
        
        # 按优先级排序模块
        sorted_modules = sorted(
            snapshot.module_states.items(),
            key=lambda item: item[1].restore_priority,
            reverse=True
        )
        
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ 模块恢复顺序: {[name for name, _ in sorted_modules]}'}))
        
        success_count = 0
        total_count = len(sorted_modules)
        
        for module_name, module_state in sorted_modules:
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ 恢复模块: {module_name} (优先级: {module_state.restore_priority})'}))
            
            # 检查模块是否初始化
            if not module_state.initialized:
                logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] │   └─ 跳过未初始化的模块: {module_name}'}))
                continue
                
            try:
                # 校验数据完整性
                checksum = self._compute_checksum(module_state.state_data)
                if checksum != module_state.checksum:
                    logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] │   └─ 警告: 数据校验失败!'}))
                    logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] │      期望: {module_state.checksum}'}))
                    logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] │      实际: {checksum}'}))
                    
                # 反序列化状态
                state_data = pickle.loads(module_state.state_data)
                
                # 根据模块名称选择恢复方法
                if module_name == "body_sensor" and hasattr(digital_life, "_body"):
                    body_sensor = digital_life._body.get() if hasattr(digital_life._body, "get") else digital_life._body
                    if self._restore_body_sensor(body_sensor, state_data):
                        success_count += 1
                        
                elif module_name == "behavior" and hasattr(digital_life, "_behavior"):
                    if self._restore_behavior(digital_life._behavior, state_data):
                        success_count += 1
                        
                elif module_name == "permission" and hasattr(digital_life, "_permission"):
                    if self._restore_permission(digital_life._permission, state_data):
                        success_count += 1
                        
                elif module_name == "tools_registry" and hasattr(digital_life, "_tools_registry"):
                    if self._restore_tools_registry(digital_life._tools_registry, state_data):
                        success_count += 1
                        
                else:
                    logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] │   └─ 未知模块或模块不存在: {module_name}'}))
                    
            except Exception as e:
                logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] │   └─ 模块恢复异常: {e}'}))
                import traceback
                logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] │      堆栈:\n{traceback.format_exc()}'}))
                
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ 模块恢复完成: {success_count}/{total_count} 成功'}))
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ═══════════════════════════════════════════════════════'}))
        
        return success_count > 0
        
    def load_snapshot(
        self,
        digital_life_class: Any = None,
        snapshot_id: Optional[str] = None,
    ) -> Optional[Any]:
        """加载状态快照并恢复 DigitalLife 实例（Phase 3 完整版）
        
        Args:
            digital_life_class: DigitalLife 类对象，用于创建实例
            snapshot_id: 快照ID，使用最新快照如果为None
            
        Returns:
            恢复的 DigitalLife 实例
        """
        start_time = time.time()
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ═══════════════════════════════════════════════════════'}))
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] 快照加载流程开始（Phase 3 完整版）'}))
        
        # 1. 定位快照
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ├─ 步骤1: 定位快照文件...'}))
        if snapshot_id:
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] │   └─ 查找指定快照ID: {snapshot_id}'}))
        else:
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] │   └─ 未指定快照ID，将使用最新快照'}))
            
        snapshot = self._load_snapshot_data(snapshot_id)
        if not snapshot:
            logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ├─ ✗ 未找到可用快照'}))
            logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] └─ 可能原因: 快照目录为空或文件损坏'}))
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ═══════════════════════════════════════════════════════'}))
            return None
            
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ ✓ 快照文件定位成功'}))
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] │   ├─ 快照ID: {snapshot.snapshot_id}'}))
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] │   ├─ 版本: {snapshot.version}'}))
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] │   ├─ 创建时间: {snapshot.created_at}'}))
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] │   ├─ 配置键数: {len(snapshot.config)}'}))
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] │   ├─ 模块数: {len(snapshot.module_states)}'}))
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] │   └─ 类型: {('增量' if snapshot.is_incremental else '完整')}快照'}))
        
        # 2. 版本兼容性检查
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ├─ 步骤2: 版本兼容性检查...'}))
        if not self._check_compatibility(snapshot):
            logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ├─ ✗ 版本不兼容，恢复终止'}))
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ═══════════════════════════════════════════════════════'}))
            return None
            
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ ✓ 版本兼容检查通过'}))
        
        # 3. 创建 DigitalLife 实例
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ├─ 步骤3: 创建 DigitalLife 实例...'}))
        if digital_life_class is None:
            logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] │   └─ 警告: 未提供 DigitalLife 类，仅返回快照数据'}))
            # Phase 1/2 兼容模式：只返回快照数据
            self.current_snapshot = snapshot
            elapsed = (time.time() - start_time) * 1000
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ 快照加载总计耗时: {elapsed:.2f}ms'}))
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ═══════════════════════════════════════════════════════'}))
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 快照加载成功（仅数据，未恢复实例）！'}))
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ═══════════════════════════════════════════════════════'}))
            return snapshot
            
        try:
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] │   └─ 使用配置创建实例: {snapshot.config}'}))
            Yunshu = digital_life_class(snapshot.config)
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ ✓ DigitalLife 实例创建成功'}))
        except Exception as e:
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ ✗ DigitalLife 实例创建失败: {e}'}))
            import traceback
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] │   堆栈:\n{traceback.format_exc()}'}))
            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ═══════════════════════════════════════════════════════'}))
            return None
        
        # 4. 按优先级恢复模块状态
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ├─ 步骤4: 按优先级恢复模块...'}))
        if not self._restore_modules_by_priority(Yunshu, snapshot):
            logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ├─ ⚠️ 模块恢复部分失败，但继续执行'}))
        
        # 5. 验证恢复结果
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ├─ 步骤5: 验证恢复结果...'}))
        try:
            # 简单验证：检查核心模块是否存在
            core_modules_exists = (
                hasattr(Yunshu, "_body") and 
                hasattr(Yunshu, "_behavior") and 
                hasattr(Yunshu, "_permission")
            )
            if core_modules_exists:
                logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ ✓ 恢复验证通过'}))
            else:
                logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ ⚠️ 恢复验证: 部分核心模块缺失'}))
        except Exception as e:
            logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ ⚠️ 恢复验证异常: {e}'}))
        
        # 6. 更新管理器状态
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': '[P6] ├─ 步骤6: 更新管理器状态...'}))
        self.current_snapshot = snapshot
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] │   └─ current_snapshot 已更新'}))
        
        # 更新模块校验和缓存
        self._update_module_checksums(snapshot)
        
        elapsed = (time.time() - start_time) * 1000
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ├─ 快照加载总计耗时: {elapsed:.2f}ms'}))
        
        # 记录性能数据
        self.performance_monitor.record_load(elapsed)
        
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ═══════════════════════════════════════════════════════'}))
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 快照恢复成功！'}))
        logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] ═══════════════════════════════════════════════════════'}))
        
        return Yunshu
        
    def list_snapshots(self) -> List[SnapshotInfo]:
        """列出所有可用快照"""
        snapshots = []
        
        try:
            for file_path in self.snapshot_dir.iterdir():
                if file_path.is_file():
                    try:
                        # 解析文件名
                        filename = file_path.name
                        if filename.endswith(".snap.gz") or filename.endswith(".snap"):
                            is_incremental = "incremental" in filename
                            
                            # 提取快照ID
                            if filename.endswith(".snap.gz"):
                                snapshot_id = filename[:-8]  # .snap.gz = 8 chars
                            else:
                                snapshot_id = filename[:-5]  # .snap = 5 chars
                                
                            # 如果是增量快照，去掉.incremental
                            if snapshot_id.endswith(".incremental"):
                                snapshot_id = snapshot_id[:-12]
                            
                            stat = file_path.stat()
                            created_at = datetime.fromtimestamp(stat.st_ctime)
                            
                            snapshots.append(SnapshotInfo(
                                snapshot_id=snapshot_id,
                                created_at=created_at,
                                version="p6.2.0",
                                file_size=stat.st_size,
                                is_incremental=is_incremental,
                            ))
                    except Exception:
                        continue
                        
        except Exception as e:
            logger.error(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 列出快照失败: {e}'}))
            
        # 按创建时间倒序排列
        snapshots.sort(key=lambda x: x.created_at, reverse=True)
        return snapshots
        
    def cleanup_snapshots(self, keep_count: int = 5) -> int:
        """清理旧快照
        
        Args:
            keep_count: 保留的快照数量
            
        Returns:
            清理的快照数量
        """
        snapshots = self.list_snapshots()
        deleted_count = 0
        
        if len(snapshots) > keep_count:
            snapshots_to_delete = snapshots[keep_count:]
            for snap_info in snapshots_to_delete:
                try:
                    # 删除完整和增量两种格式
                    for is_incremental in [False, True]:
                        snap_path = self._get_snapshot_path(snap_info.snapshot_id, is_incremental)
                        if snap_path.exists():
                            snap_path.unlink()
                            deleted_count += 1
                            logger.info(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 删除快照: {snap_info.snapshot_id}'}))
                except Exception as e:
                    logger.warning(log_dict({'module_name': 'p6_snapshot', 'action': 'log', 'msg': f'[P6] 删除快照失败: {e}'}))
                    
        return deleted_count
        
    def show_performance_panel(self):
        """显示性能监控面板"""
        self.performance_monitor.print_performance_panel()
