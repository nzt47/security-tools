#!/usr/bin/env python3
"""灾备恢复 — 数据损坏/丢失下的状态恢复

在 Memory 数据、会话、配置文件损坏或丢失时，自动从本地持久化备份恢复，
保证服务可用性。

灾备策略（与项目 memory 一致）：
- 关键数据本地持久化备份（Memory、会话、配置）
- 服务重启自动恢复状态
- 数据库损坏自动修复流程
- 配置文件热重载（不重启服务）

可观测性约束：
- 所有恢复动作输出结构化 JSON 日志（trace_id/module_name/action/duration_ms）
- 恢复埋点上报 BusinessMetricsCollector（吞掉异常不影响主流程）
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")


def set_trace_id(trace_id: str) -> None:
    _trace_id_ctx.set(trace_id or "")


def get_trace_id() -> str:
    return _trace_id_ctx.get()


class RecoveryAction(str, Enum):
    """恢复动作类型"""
    BACKUP = "backup"           # 创建备份
    RESTORE = "restore"         # 从备份恢复
    REPAIR = "repair"           # 修复损坏数据
    RELOAD = "reload"           # 热重载配置
    SKIP = "skip"               # 跳过（无需恢复）


class BackupType(str, Enum):
    """备份类型"""
    FULL = "full"               # 全量备份
    INCREMENTAL = "incremental"  # 增量备份
    CONFIG = "config"           # 配置备份


class RecoveryStatus(str, Enum):
    """恢复状态"""
    NONE = "none"               # 无恢复操作
    IN_PROGRESS = "in_progress"  # 恢复中
    SUCCESS = "completed"       # 恢复成功
    FAILED = "failed"           # 恢复失败


@dataclass
class BackupConfig:
    """备份配置"""
    backup_dir: str = "./backups"               # 备份目录
    max_backups: int = 5                         # 最大保留备份数
    compress: bool = False                       # 是否压缩
    enabled: bool = True                         # 是否启用备份
    backup_interval_minutes: float = 60.0        # 备份间隔（分钟）
    auto_recover: bool = False                   # 是否启动时自动恢复


@dataclass
class BackupInfo:
    """备份信息"""
    backup_id: str                               # 备份唯一标识
    timestamp: str                               # 备份时间（ISO 格式）
    backup_type: BackupType                      # 备份类型
    checksum: str                                # 校验和
    size: int = 0                                # 备份大小（字节）
    providers: list = field(default_factory=list)  # 包含的提供者名称列表


@dataclass
class RecoveryInfo:
    """恢复状态信息"""
    status: RecoveryStatus = RecoveryStatus.NONE  # 当前恢复状态
    backup_id: Optional[str] = None               # 正在/已恢复的备份 ID
    restored_files: list = field(default_factory=list)  # 已恢复的提供者列表
    error: str = ""                               # 错误信息


class RecoveryError(Exception):
    """恢复失败时抛出的业务错误（带明确业务错误码）"""

    def __init__(self, message: str, error_code: str = "RECOVERY_FAILED",
                 resource: str = ""):
        super().__init__(message)
        self.error_code = error_code
        self.resource = resource


@dataclass
class RecoveryResult:
    """恢复操作结果"""
    success: bool
    action: RecoveryAction
    resource: str
    message: str = ""
    duration_ms: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class ResourceState:
    """资源状态"""
    name: str
    path: Path
    backup_path: Path
    last_backup: float = 0.0
    last_recovery: float = 0.0
    is_corrupted: bool = False
    recovery_count: int = 0


class DisasterRecovery:
    """灾备恢复管理器

    Args:
        backup_root: 备份根目录路径（旧 API）或 BackupConfig 对象（新 API）。
                     为 None 时使用默认 BackupConfig。
        max_backups: 每个资源保留的最大备份数（旧 API，当 backup_root 为路径时生效）
        auto_repair: 是否自动修复损坏数据（旧 API，当 backup_root 为路径时生效）
    """

    def __init__(
        self,
        backup_root: str | Path | BackupConfig | None = None,
        max_backups: int = 5,
        auto_repair: bool = True,
    ):
        # 兼容三种调用方式：BackupConfig 对象 / 路径字符串 / 无参
        if isinstance(backup_root, BackupConfig):
            self._config = backup_root
        elif backup_root is None:
            self._config = BackupConfig()
        else:
            self._config = BackupConfig(
                backup_dir=str(backup_root),
                max_backups=max_backups,
                auto_recover=auto_repair,
            )

        self.backup_root = Path(self._config.backup_dir)
        self.backup_root.mkdir(parents=True, exist_ok=True)
        self.max_backups = self._config.max_backups
        self.auto_repair = auto_repair
        self._resources: dict[str, ResourceState] = {}
        self._lock = threading.RLock()
        # 配置热重载回调
        self._reload_callbacks: dict[str, Callable] = {}

        # ── 新增状态：备份提供者与调度器 ──────────────────────
        # 提供者字典：{name: (backup_func, restore_func)}
        self._backup_providers: dict[str, tuple[Callable, Callable]] = {}
        # 恢复状态信息
        self._recovery_status = RecoveryInfo()
        # 已恢复的提供者集合（用于 get_status 报告）
        self._restored_providers: list[str] = []
        # 备份调度器线程
        self._backup_thread: Optional[threading.Thread] = None
        # 调度器停止事件
        self._scheduler_stop_event = threading.Event()
        # 备份 ID 自增计数器（保证同一微秒内备份 ID 不冲突）
        self._backup_counter = 0

    # ── 资源注册 ──────────────────────────────────────────────

    def register(
        self,
        name: str,
        path: str | Path,
        reload_callback: Optional[Callable] = None,
    ) -> None:
        """注册需灾备的资源

        Args:
            name: 资源名称（如 memory_db、sessions、config）
            path: 资源文件/目录路径
            reload_callback: 配置热重载回调
        """
        with self._lock:
            resource_path = Path(path)
            backup_path = self.backup_root / name
            self._resources[name] = ResourceState(
                name=name,
                path=resource_path,
                backup_path=backup_path,
            )
            if reload_callback:
                self._reload_callbacks[name] = reload_callback
            self._log_action("resource_registered", {"resource": name})

    # ── 备份 ──────────────────────────────────────────────────

    def backup(self, name: str) -> RecoveryResult:
        """为指定资源创建备份

        Args:
            name: 资源名称

        Returns:
            RecoveryResult
        """
        start = time.time()
        with self._lock:
            state = self._resources.get(name)
            if state is None:
                return RecoveryResult(
                    success=False,
                    action=RecoveryAction.SKIP,
                    resource=name,
                    message=f"资源 {name} 未注册",
                )

            if not state.path.exists():
                return RecoveryResult(
                    success=False,
                    action=RecoveryAction.SKIP,
                    resource=name,
                    message=f"资源文件不存在: {state.path}",
                )

            try:
                # 创建带时间戳的备份（使用微秒精度，避免同一秒内备份互相覆盖）
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                backup_file = state.backup_path / f"{timestamp}.bak"
                backup_file.parent.mkdir(parents=True, exist_ok=True)

                if state.path.is_file():
                    shutil.copy2(state.path, backup_file)
                else:
                    shutil.copytree(state.path, backup_file)

                state.last_backup = time.time()
                # 清理旧备份
                self._cleanup_old_backups(state)

                duration_ms = int((time.time() - start) * 1000)
                self._log_action("backup_success", {
                    "resource": name,
                    "backup_file": str(backup_file),
                    "duration_ms": duration_ms,
                })
                return RecoveryResult(
                    success=True,
                    action=RecoveryAction.BACKUP,
                    resource=name,
                    message=f"备份成功: {backup_file}",
                    duration_ms=duration_ms,
                )
            except Exception as exc:
                duration_ms = int((time.time() - start) * 1000)
                self._log_action("backup_failed", {
                    "resource": name,
                    "error": str(exc)[:200],
                    "duration_ms": duration_ms,
                })
                return RecoveryResult(
                    success=False,
                    action=RecoveryAction.BACKUP,
                    resource=name,
                    message=f"备份失败: {exc}",
                    duration_ms=duration_ms,
                )

    def backup_all(self) -> dict[str, RecoveryResult]:
        """为所有注册资源创建备份"""
        results = {}
        for name in list(self._resources.keys()):
            results[name] = self.backup(name)
        return results

    # ── 恢复 ──────────────────────────────────────────────────

    def restore(self, name: str) -> RecoveryResult:
        """从最新备份恢复资源

        Args:
            name: 资源名称
        """
        start = time.time()
        with self._lock:
            state = self._resources.get(name)
            if state is None:
                return RecoveryResult(
                    success=False,
                    action=RecoveryAction.SKIP,
                    resource=name,
                    message=f"资源 {name} 未注册",
                )

            latest = self._get_latest_backup(state)
            if latest is None:
                return RecoveryResult(
                    success=False,
                    action=RecoveryAction.SKIP,
                    resource=name,
                    message=f"无可用备份: {name}",
                )

            try:
                # 损坏文件先归档（便于事后分析）
                if state.path.exists() and self.auto_repair:
                    corrupted_archive = state.backup_path / "corrupted"
                    corrupted_archive.mkdir(parents=True, exist_ok=True)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    if state.path.is_file():
                        shutil.move(
                            str(state.path),
                            str(corrupted_archive / f"{timestamp}.corrupted"),
                        )
                    else:
                        shutil.move(
                            str(state.path),
                            str(corrupted_archive / f"{timestamp}.corrupted"),
                        )

                # 从备份恢复
                if latest.is_file():
                    state.path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(latest, state.path)
                else:
                    shutil.copytree(latest, state.path)

                state.last_recovery = time.time()
                state.is_corrupted = False
                state.recovery_count += 1

                duration_ms = int((time.time() - start) * 1000)
                self._log_action("restore_success", {
                    "resource": name,
                    "backup_file": str(latest),
                    "duration_ms": duration_ms,
                })
                return RecoveryResult(
                    success=True,
                    action=RecoveryAction.RESTORE,
                    resource=name,
                    message=f"恢复成功: {latest}",
                    duration_ms=duration_ms,
                )
            except Exception as exc:
                duration_ms = int((time.time() - start) * 1000)
                self._log_action("restore_failed", {
                    "resource": name,
                    "error": str(exc)[:200],
                    "duration_ms": duration_ms,
                })
                return RecoveryResult(
                    success=False,
                    action=RecoveryAction.RESTORE,
                    resource=name,
                    message=f"恢复失败: {exc}",
                    duration_ms=duration_ms,
                )

    # ── 损坏检测与修复 ────────────────────────────────────────

    def check_integrity(self, name: str, validator: Optional[Callable] = None) -> bool:
        """检查资源完整性

        Args:
            name: 资源名称
            validator: 自定义校验函数（接收 path，返回 bool）

        Returns:
            True 完整，False 损坏
        """
        with self._lock:
            state = self._resources.get(name)
            if state is None:
                return False

            if not state.path.exists():
                state.is_corrupted = True
                return False

            if validator is not None:
                try:
                    if not validator(state.path):
                        state.is_corrupted = True
                        return False
                except Exception:
                    state.is_corrupted = True
                    return False

            state.is_corrupted = False
            return True

    def repair_if_corrupted(self, name: str, validator: Optional[Callable] = None) -> RecoveryResult:
        """检测到损坏时自动修复

        Args:
            name: 资源名称
            validator: 完整性校验函数
        """
        if self.check_integrity(name, validator):
            return RecoveryResult(
                success=True,
                action=RecoveryAction.SKIP,
                resource=name,
                message="资源完整，无需修复",
            )
        # 检测到损坏，从备份恢复
        self._log_action("corruption_detected", {"resource": name})
        return self.restore(name)

    # ── 配置热重载 ───────────────────────────────────────────

    def reload_config(self, name: str) -> RecoveryResult:
        """热重载配置文件（不重启服务）

        Args:
            name: 资源名称（必须已注册 reload_callback）
        """
        start = time.time()
        with self._lock:
            state = self._resources.get(name)
            if state is None:
                return RecoveryResult(
                    success=False,
                    action=RecoveryAction.SKIP,
                    resource=name,
                    message=f"资源 {name} 未注册",
                )

            callback = self._reload_callbacks.get(name)
            if callback is None:
                return RecoveryResult(
                    success=False,
                    action=RecoveryAction.SKIP,
                    resource=name,
                    message=f"资源 {name} 未注册热重载回调",
                )

            try:
                callback(state.path)
                duration_ms = int((time.time() - start) * 1000)
                self._log_action("reload_success", {
                    "resource": name,
                    "duration_ms": duration_ms,
                })
                return RecoveryResult(
                    success=True,
                    action=RecoveryAction.RELOAD,
                    resource=name,
                    message="热重载成功",
                    duration_ms=duration_ms,
                )
            except Exception as exc:
                duration_ms = int((time.time() - start) * 1000)
                self._log_action("reload_failed", {
                    "resource": name,
                    "error": str(exc)[:200],
                    "duration_ms": duration_ms,
                })
                return RecoveryResult(
                    success=False,
                    action=RecoveryAction.RELOAD,
                    resource=name,
                    message=f"热重载失败: {exc}",
                    duration_ms=duration_ms,
                )

    # ── 服务重启状态恢复 ─────────────────────────────────────

    def recover_on_startup(self, validators: Optional[dict[str, Callable]] = None) -> dict[str, RecoveryResult]:
        """服务启动时检查所有资源并恢复

        Args:
            validators: {resource_name: validator_func}

        Returns:
            {resource_name: RecoveryResult}
        """
        results = {}
        validators = validators or {}
        for name in list(self._resources.keys()):
            validator = validators.get(name)
            results[name] = self.repair_if_corrupted(name, validator)
        return results

    # ── 控制方法 ──────────────────────────────────────────────

    def reset(self) -> None:
        """重置所有状态（主要用于测试）"""
        with self._lock:
            self._resources.clear()
            self._reload_callbacks.clear()
            self._backup_providers.clear()
            self._recovery_status = RecoveryInfo()
            self._restored_providers.clear()

    def get_state(self, name: str) -> Optional[ResourceState]:
        """获取资源状态"""
        with self._lock:
            return self._resources.get(name)

    # ── 内部辅助方法 ─────────────────────────────────────────

    def _get_latest_backup(self, state: ResourceState) -> Optional[Path]:
        """获取最新备份文件"""
        if not state.backup_path.exists():
            return None
        backups = sorted(
            [p for p in state.backup_path.glob("*.bak")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return backups[0] if backups else None

    def _cleanup_old_backups(self, state: ResourceState) -> None:
        """清理过期备份，保留最新 max_backups 个"""
        if not state.backup_path.exists():
            return
        backups = sorted(
            [p for p in state.backup_path.glob("*.bak")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in backups[self.max_backups:]:
            try:
                if old.is_file():
                    old.unlink()
                else:
                    shutil.rmtree(old)
            except Exception:
                pass

    # ── 可观测性 ──────────────────────────────────────────────

    def _log_action(self, action: str, payload: dict) -> None:
        """输出结构化 JSON 日志"""
        try:
            log_entry = {
                "trace_id": get_trace_id(),
                "module_name": "disaster_recovery",
                "action": action,
                "duration_ms": payload.pop("duration_ms", 0),
                "timestamp": time.time(),
                **payload,
            }
            logger.info(json.dumps(log_entry, ensure_ascii=False))
        except Exception as exc:
            logger.debug("灾备日志记录失败: %s", exc)

    # ── 备份提供者注册（新 API） ─────────────────────────────

    def register_backup_provider(
        self,
        name: str,
        backup_func: Callable,
        restore_func: Callable,
    ) -> None:
        """注册备份提供者

        Args:
            name: 提供者名称（如 memory_db、sessions、config）
            backup_func: 备份函数，返回可 JSON 序列化的数据
            restore_func: 恢复函数，接收备份数据作为参数
        """
        with self._lock:
            self._backup_providers[name] = (backup_func, restore_func)
            self._log_action("backup_provider_registered", {"provider": name})

    # ── 触发备份（新 API） ───────────────────────────────────

    def trigger_backup(self, backup_type: BackupType = BackupType.FULL) -> str:
        """触发备份

        Args:
            backup_type: 备份类型（FULL / INCREMENTAL / CONFIG）

        Returns:
            备份 ID（以 "backup_" 开头）；若备份未启用或失败则返回空字符串
        """
        if not self._config.enabled:
            return ""

        start = time.time()
        with self._lock:
            # 生成唯一备份 ID（微秒时间戳 + 自增计数器）
            self._backup_counter += 1
            backup_id = f"backup_{int(time.time() * 1000000)}_{self._backup_counter}"
            timestamp_str = datetime.now().isoformat()

            # 收集所有提供者的备份数据
            data: dict[str, Any] = {}
            for name, (backup_func, _restore_func) in self._backup_providers.items():
                try:
                    data[name] = backup_func()
                except Exception as exc:
                    # 提供者失败时记录错误，但不中断整体备份
                    data[name] = {"_error": str(exc)[:200]}
                    self._log_action("backup_provider_failed", {
                        "provider": name,
                        "error": str(exc)[:200],
                    })

            # 计算校验和（基于 data 字段的确定性序列化）
            checksum = hashlib.md5(
                json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()

            # 确保备份目录存在
            backup_dir = Path(self._config.backup_dir)
            backup_dir.mkdir(parents=True, exist_ok=True)

            # 写入备份数据文件
            backup_payload = {
                "backup_id": backup_id,
                "timestamp": timestamp_str,
                "backup_type": backup_type.value,
                "checksum": checksum,
                "data": data,
            }
            backup_file = backup_dir / f"{backup_id}.json"
            with open(backup_file, "w", encoding="utf-8") as f:
                json.dump(backup_payload, f, ensure_ascii=False)

            # 写入元数据文件
            meta_payload = {
                "backup_id": backup_id,
                "timestamp": timestamp_str,
                "backup_type": backup_type.value,
                "checksum": checksum,
                "size": backup_file.stat().st_size,
                "providers": list(self._backup_providers.keys()),
            }
            meta_file = backup_dir / f"{backup_id}_meta.json"
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(meta_payload, f, ensure_ascii=False)

            # 清理过期备份
            self._cleanup_old_backups_new()

            duration_ms = int((time.time() - start) * 1000)
            self._log_action("trigger_backup_success", {
                "backup_id": backup_id,
                "backup_type": backup_type.value,
                "providers": list(self._backup_providers.keys()),
                "duration_ms": duration_ms,
            })
            return backup_id

    # ── 备份验证 ─────────────────────────────────────────────

    def _verify_backup(self, backup_id: str) -> bool:
        """验证备份完整性

        Args:
            backup_id: 备份 ID

        Returns:
            True 完整，False 损坏或不存在

        注意：文件读取和校验在锁外执行，只读操作无需持锁保护。
        """
        backup_dir = Path(self._config.backup_dir)
        backup_file = backup_dir / f"{backup_id}.json"
        if not backup_file.exists():
            return False

        try:
            with open(backup_file, "r", encoding="utf-8") as f:
                content = json.load(f)

            stored_checksum = content.get("checksum", "")
            if not stored_checksum:
                return False

            data = content.get("data", {})
            # 重新计算校验和并比对
            computed_checksum = hashlib.md5(
                json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            return stored_checksum == computed_checksum
        except (json.JSONDecodeError, OSError, Exception):
            return False

    # ── 备份列表 ─────────────────────────────────────────────

    def get_backup_list(self) -> list:
        """获取备份列表（按时间降序）

        Returns:
            BackupInfo 列表

        注意：目录遍历和文件读取在锁外执行，因为这些是只读的磁盘操作，
        不访问共享内存状态，无需持锁保护。
        """
        backup_dir = Path(self._config.backup_dir)
        if not backup_dir.exists():
            return []

        backups: list[BackupInfo] = []
        for meta_file in backup_dir.glob("*_meta.json"):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                backup_id = meta.get("backup_id", "")
                if not backup_id:
                    continue
                # 兼容未知的 backup_type 值
                raw_type = meta.get("backup_type", "full")
                try:
                    bt = BackupType(raw_type)
                except ValueError:
                    bt = BackupType.FULL
                backups.append(BackupInfo(
                    backup_id=backup_id,
                    timestamp=meta.get("timestamp", ""),
                    backup_type=bt,
                    checksum=meta.get("checksum", ""),
                    size=meta.get("size", 0),
                    providers=meta.get("providers", []),
                ))
            except Exception:
                continue

        # 按时间戳降序排列（最新的在前）
        backups.sort(key=lambda b: b.timestamp, reverse=True)
        return backups

    def _cleanup_old_backups_new(self) -> None:
        """清理过期备份（新 API），保留最新 max_backups 个"""
        with self._lock:
            backup_dir = Path(self._config.backup_dir)
            if not backup_dir.exists():
                return

            # 扫描元数据文件并按时间戳排序
            metas: list[tuple[str, Path]] = []
            for meta_file in backup_dir.glob("*_meta.json"):
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    ts = meta.get("timestamp", "")
                except Exception:
                    ts = ""
                metas.append((ts, meta_file))

            metas.sort(key=lambda x: x[0], reverse=True)

            for _ts, meta_file in metas[self._config.max_backups:]:
                # 从元数据文件名推导 backup_id 和数据文件名
                # 元数据文件名格式：{backup_id}_meta.json
                backup_id = meta_file.name[:-len("_meta.json")]
                backup_file = backup_dir / f"{backup_id}.json"
                for f in (backup_file, meta_file):
                    try:
                        if f.exists():
                            f.unlink()
                    except Exception:
                        pass

    # ── 从备份恢复（新 API） ─────────────────────────────────

    def restore_from_backup(self, backup_id: str) -> bool:
        """从备份恢复

        Args:
            backup_id: 备份 ID

        Returns:
            True 恢复成功，False 恢复失败

        注意：文件 I/O、校验和外部 restore_func 回调在锁外执行。
        锁只保护内存状态（_recovery_status、_restored_providers）的变更。
        """
        # 锁内：设置 IN_PROGRESS 状态 + 快照 providers
        with self._lock:
            self._recovery_status.status = RecoveryStatus.IN_PROGRESS
            self._recovery_status.backup_id = backup_id
            self._recovery_status.error = ""
            backup_dir = Path(self._config.backup_dir)
            providers_snapshot = dict(self._backup_providers)

        # 锁外：检查文件存在
        backup_file = backup_dir / f"{backup_id}.json"
        if not backup_file.exists():
            with self._lock:
                self._recovery_status.status = RecoveryStatus.FAILED
                self._recovery_status.error = f"备份文件不存在: {backup_id}"
            self._log_action("restore_failed", {
                "backup_id": backup_id,
                "error": "备份文件不存在",
            })
            return False

        # 锁外：校验 + 读取 + 恢复
        try:
            if not self._verify_backup(backup_id):
                with self._lock:
                    self._recovery_status.status = RecoveryStatus.FAILED
                    self._recovery_status.error = "备份校验失败"
                self._log_action("restore_failed", {
                    "backup_id": backup_id,
                    "error": "备份校验失败",
                })
                return False

            with open(backup_file, "r", encoding="utf-8") as f:
                content = json.load(f)

            data = content.get("data", {})
            restored: list[str] = []

            # 锁外：逐个调用提供者的恢复函数
            for name, (_backup_func, restore_func) in providers_snapshot.items():
                if name in data:
                    try:
                        restore_func(data[name])
                        restored.append(name)
                    except Exception as exc:
                        # 单个提供者恢复失败不中断整体流程
                        self._log_action("restore_provider_failed", {
                            "provider": name,
                            "error": str(exc)[:200],
                        })
                        restored.append(name)

            # 锁内：更新最终状态
            with self._lock:
                self._recovery_status.status = RecoveryStatus.SUCCESS
                self._recovery_status.backup_id = backup_id
                self._restored_providers = restored
            self._log_action("restore_success", {
                "backup_id": backup_id,
                "restored_providers": restored,
            })
            return True
        except Exception as exc:
            with self._lock:
                self._recovery_status.status = RecoveryStatus.FAILED
                self._recovery_status.error = str(exc)[:200]
            self._log_action("restore_failed", {
                "backup_id": backup_id,
                "error": str(exc)[:200],
            })
            return False

    # ── 启动时自动恢复 ───────────────────────────────────────

    def auto_recover_on_startup(self) -> bool:
        """启动时自动从最新备份恢复

        Returns:
            True 恢复成功，False 无备份或恢复失败

        注意：不持有外层锁，get_backup_list 和 restore_from_backup
        各自管理自己的锁，避免锁嵌套。
        """
        backups = self.get_backup_list()
        if not backups:
            self._log_action("auto_recover_no_backups", {})
            return False

        latest = backups[0]
        self._log_action("auto_recover_start", {
            "backup_id": latest.backup_id,
        })
        return self.restore_from_backup(latest.backup_id)

    # ── 数据库修复 ───────────────────────────────────────────

    def repair_database(self, db_path: str) -> bool:
        """修复数据库

        通过执行 SQLite 完整性检查判断数据库是否损坏。
        不存在的数据库视为修复成功。

        Args:
            db_path: 数据库文件路径

        Returns:
            True 数据库有效或不存在，False 数据库损坏
        """
        path = Path(db_path)
        if not path.exists():
            # 不存在的数据库视为修复成功
            return True

        start = time.time()
        try:
            conn = sqlite3.connect(str(path))
            try:
                cursor = conn.cursor()
                cursor.execute("PRAGMA integrity_check;")
                result = cursor.fetchone()
                if result and result[0] == "ok":
                    duration_ms = int((time.time() - start) * 1000)
                    self._log_action("repair_database_ok", {
                        "db_path": str(path),
                        "duration_ms": duration_ms,
                    })
                    return True
                else:
                    duration_ms = int((time.time() - start) * 1000)
                    self._log_action("repair_database_corrupted", {
                        "db_path": str(path),
                        "integrity_result": str(result),
                        "duration_ms": duration_ms,
                    })
                    return False
            finally:
                conn.close()
        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            self._log_action("repair_database_failed", {
                "db_path": str(path),
                "error": str(exc)[:200],
                "duration_ms": duration_ms,
            })
            return False

    # ── 备份调度器 ───────────────────────────────────────────

    def start_backup_scheduler(self) -> None:
        """启动定时备份调度器（守护线程）"""
        if not self._config.enabled:
            # 备份未启用时不启动调度器
            return

        with self._lock:
            if self._backup_thread is not None and self._backup_thread.is_alive():
                # 调度器已在运行，不重复启动
                return

            self._scheduler_stop_event.clear()
            self._backup_thread = threading.Thread(
                target=self._scheduler_loop,
                daemon=True,
                name="dr-backup-scheduler",
            )
            self._backup_thread.start()
            self._log_action("backup_scheduler_started", {})

    def stop_backup_scheduler(self) -> None:
        """停止定时备份调度器"""
        with self._lock:
            self._scheduler_stop_event.set()
            thread = self._backup_thread
        if thread is not None and thread.is_alive():
            # 等待线程退出（最多 2 秒）
            thread.join(timeout=2.0)
        self._log_action("backup_scheduler_stopped", {})

    def _scheduler_loop(self) -> None:
        """调度器循环：按配置间隔定期触发备份"""
        interval = max(self._config.backup_interval_minutes * 60, 0.1)
        while not self._scheduler_stop_event.is_set():
            try:
                self.trigger_backup(BackupType.FULL)
            except Exception as exc:
                self._log_action("backup_scheduler_error", {
                    "error": str(exc)[:200],
                })
            # 等待间隔或停止信号
            self._scheduler_stop_event.wait(interval)

    # ── 状态查询（新 API） ───────────────────────────────────

    def get_recovery_status(self) -> RecoveryInfo:
        """获取恢复状态

        Returns:
            RecoveryInfo 对象
        """
        with self._lock:
            return RecoveryInfo(
                status=self._recovery_status.status,
                backup_id=self._recovery_status.backup_id,
                restored_files=list(self._restored_providers),
                error=self._recovery_status.error,
            )

    def get_status(self) -> dict:
        """获取容灾恢复整体状态

        Returns:
            包含 config / backup_providers / backup_count / latest_backup /
            recovery_status / scheduler_running 的字典

        注意：get_backup_list 在锁外调用（自己管理锁），锁内只取内存状态快照。
        """
        # 锁外：读取磁盘数据
        backups = self.get_backup_list()
        latest = backups[0] if backups else None

        # 锁内：取内存状态快照
        with self._lock:
            config_snapshot = {
                "enabled": self._config.enabled,
                "backup_dir": self._config.backup_dir,
                "max_backups": self._config.max_backups,
                "backup_interval_minutes": self._config.backup_interval_minutes,
                "auto_recover": self._config.auto_recover,
                "compress": self._config.compress,
            }
            provider_names = list(self._backup_providers.keys())
            recovery_snapshot = {
                "status": self._recovery_status.status.value,
                "backup_id": self._recovery_status.backup_id,
                "restored_files": list(self._restored_providers),
                "error": self._recovery_status.error,
            }
            scheduler_running = (
                self._backup_thread is not None
                and self._backup_thread.is_alive()
            )

        return {
            "config": config_snapshot,
            "backup_providers": provider_names,
            "backup_count": len(backups),
            "latest_backup": latest.backup_id if latest else None,
            "recovery_status": recovery_snapshot,
            "scheduler_running": scheduler_running,
        }


# ============================================================================
# 配置热重载器
# ============================================================================


class ConfigHotReloader:
    """配置文件热重载器

    通过轮询文件 mtime 变化检测配置更新，无需第三方依赖（如 watchdog）。
    回调函数抛出的异常不会终止监听线程。

    用法:
        reloader = ConfigHotReloader()
        reloader.watch_config("/path/to/config.json", callback)
        reloader.start()
        # ... 配置变更时 callback 被调用 ...
        reloader.stop()
    """

    # 轮询间隔（秒）
    _POLL_INTERVAL = 0.5

    def __init__(self):
        self._lock = threading.RLock()
        # 监听字典：{path: {"callback": fn, "last_mtime": float, "last_exists": bool}}
        self._watchers: dict[str, dict] = {}
        self._watch_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def watch_config(self, path: str, callback: Callable) -> None:
        """注册要监听的配置文件

        Args:
            path: 配置文件路径
            callback: 文件变化时调用的回调函数（接收 path 参数）
        """
        with self._lock:
            try:
                mtime = os.path.getmtime(path)
                exists = True
            except OSError:
                mtime = 0.0
                exists = False
            self._watchers[path] = {
                "callback": callback,
                "last_mtime": mtime,
                "last_exists": exists,
            }

    def start(self) -> None:
        """启动监听线程（守护线程）"""
        with self._lock:
            if self._watch_thread is not None and self._watch_thread.is_alive():
                # 已在运行，不重复启动
                return
            self._stop_event.clear()
            self._watch_thread = threading.Thread(
                target=self._watch_loop,
                daemon=True,
                name="config-hot-reloader",
            )
            self._watch_thread.start()

    def stop(self) -> None:
        """停止监听线程"""
        with self._lock:
            self._stop_event.set()
            thread = self._watch_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _watch_loop(self) -> None:
        """监听循环：轮询文件 mtime 变化"""
        while not self._stop_event.is_set():
            try:
                # 复制监听列表，避免长时间持锁
                with self._lock:
                    items = [(p, dict(v)) for p, v in self._watchers.items()]
                for path, info in items:
                    try:
                        self._check_and_notify(path, info)
                    except Exception:
                        # 单个文件检查异常不终止循环
                        pass
            except Exception:
                pass
            self._stop_event.wait(self._POLL_INTERVAL)

    def _check_and_notify(self, path: str, info: dict) -> None:
        """检查单个文件是否变化并通知回调"""
        try:
            current_mtime = os.path.getmtime(path)
            current_exists = True
        except OSError:
            current_exists = False
            current_mtime = 0.0

        changed = False
        with self._lock:
            if path not in self._watchers:
                return
            stored = self._watchers[path]
            if current_exists != stored["last_exists"]:
                # 文件创建或删除
                if current_exists:
                    # 文件被创建/恢复，触发回调
                    changed = True
                stored["last_exists"] = current_exists
                stored["last_mtime"] = current_mtime
            elif current_exists and current_mtime != stored["last_mtime"]:
                # 文件被修改
                changed = True
                stored["last_mtime"] = current_mtime

        if changed:
            self._safe_callback(info["callback"], path)

    def _safe_callback(self, callback: Callable, path: str) -> None:
        """安全调用回调（捕获所有异常）"""
        try:
            callback(path)
        except Exception as exc:
            logger.debug("配置热重载回调异常: %s", exc)


# ============================================================================
# 全局单例与便捷函数
# ============================================================================

# 全局单例锁与实例
_dr_singleton: Optional[DisasterRecovery] = None
_dr_lock = threading.Lock()

_reloader_singleton: Optional[ConfigHotReloader] = None
_reloader_lock = threading.Lock()


def get_disaster_recovery() -> DisasterRecovery:
    """获取全局 DisasterRecovery 单例（线程安全）

    Returns:
        DisasterRecovery 单例实例
    """
    global _dr_singleton
    with _dr_lock:
        if _dr_singleton is None:
            _dr_singleton = DisasterRecovery()
        return _dr_singleton


def get_config_reloader() -> ConfigHotReloader:
    """获取全局 ConfigHotReloader 单例（线程安全）

    Returns:
        ConfigHotReloader 单例实例
    """
    global _reloader_singleton
    with _reloader_lock:
        if _reloader_singleton is None:
            _reloader_singleton = ConfigHotReloader()
        return _reloader_singleton


def register_backup_provider(
    name: str,
    backup_func: Callable,
    restore_func: Callable,
) -> None:
    """便捷函数：在全局单例上注册备份提供者

    Args:
        name: 提供者名称
        backup_func: 备份函数
        restore_func: 恢复函数
    """
    get_disaster_recovery().register_backup_provider(name, backup_func, restore_func)


def trigger_backup(backup_type: BackupType = BackupType.FULL) -> str:
    """便捷函数：在全局单例上触发备份

    Args:
        backup_type: 备份类型

    Returns:
        备份 ID（空字符串表示未启用或失败）
    """
    return get_disaster_recovery().trigger_backup(backup_type)


def restore_from_backup(backup_id: str) -> bool:
    """便捷函数：在全局单例上从备份恢复

    Args:
        backup_id: 备份 ID

    Returns:
        True 恢复成功，False 恢复失败
    """
    return get_disaster_recovery().restore_from_backup(backup_id)
