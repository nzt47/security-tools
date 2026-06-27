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

import json
import logging
import os
import shutil
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
        backup_root: 备份根目录
        max_backups: 每个资源保留的最大备份数
        auto_repair: 是否自动修复损坏数据
    """

    def __init__(
        self,
        backup_root: str | Path,
        max_backups: int = 5,
        auto_repair: bool = True,
    ):
        self.backup_root = Path(backup_root)
        self.backup_root.mkdir(parents=True, exist_ok=True)
        self.max_backups = max_backups
        self.auto_repair = auto_repair
        self._resources: dict[str, ResourceState] = {}
        self._lock = threading.RLock()
        # 配置热重载回调
        self._reload_callbacks: dict[str, Callable] = {}

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
