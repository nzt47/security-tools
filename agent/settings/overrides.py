"""开关覆盖层（TASK-S7-01 步骤 2 的落盘部分）

【不易（守不易：绝不改配置文件）】
    UI 的每一次开关变更**只写覆盖层** `data/ui_settings.json`（gitignore 的运行时
    留痕），**绝不修改** `.env` 与 `config.yaml`：

    - 写路径守卫 `_guard_path()` 显式拒绝这两个文件（用例断言文件未被改动）；
    - 覆盖层是**薄薄一层 JSON**，可随时整份删除回落到 `config.yaml` / 代码默认。

【生效优先级（与仓库既有口径一致：环境变量 > 配置文件 > 硬编码默认）】
    `env > ui_override > config > default`
    其中 `env` 是**进程启动时由运维注入**的环境变量（`resolve()` 能区分
    「运维设置的 env」与「本进程自己为了热生效而写入的 env」——后者记为覆盖层）。

【原子写】
    先写同目录临时文件再 `os.replace`，避免半截 JSON（进程被杀也不会留坏文件）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 覆盖层路径（相对仓库根）；可用环境变量覆盖（测试隔离用）
ENV_OVERRIDE_PATH = "CP_UI_SETTINGS_PATH"

#: 默认覆盖层文件
DEFAULT_OVERRIDE_PATH = "data/ui_settings.json"

#: 受保护的配置文件（**任何情况下都不得被本模块写入**）
PROTECTED_FILES = (".env", "config.yaml")

#: 覆盖层文件格式版本
SCHEMA_VERSION = 1


def repo_root() -> Path:
    """仓库根（本文件位于 <root>/agent/settings/overrides.py）"""
    return Path(__file__).resolve().parent.parent.parent


def default_override_path() -> Path:
    """覆盖层默认绝对路径（env `CP_UI_SETTINGS_PATH` 优先，测试据此隔离）"""
    raw = str(os.getenv(ENV_OVERRIDE_PATH, "") or "").strip()
    if raw:
        return Path(raw)
    return repo_root() / DEFAULT_OVERRIDE_PATH


def _guard_path(path: Path) -> None:
    """守不易：拒绝把覆盖层写到 `.env` / `config.yaml`

    Raises:
        ValueError: 目标路径命中受保护文件。
    """
    name = path.name.lower()
    if name in PROTECTED_FILES:
        raise ValueError(
            f"覆盖层拒绝写入受保护配置文件 {path}："
            "UI 开关只写覆盖层（守不易，绝不改 .env / config.yaml）")
    resolved = str(path.resolve()).lower().replace("\\", "/")
    for protected in PROTECTED_FILES:
        if resolved.endswith("/" + protected):
            raise ValueError(
                f"覆盖层拒绝写入受保护配置文件 {path}（命中 {protected}）")


@dataclass
class OverrideRecord:
    """一条覆盖记录"""

    key: str
    value: Any
    actor: str = ""
    risk: str = ""
    updated_at: str = ""
    previous: Any = None
    reason: str = ""
    #: 本进程是否已把它落到运行态（env / ObservabilityConfig）
    applied_in_process: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "value": self.value, "actor": self.actor,
            "risk": self.risk, "updated_at": self.updated_at,
            "previous": self.previous, "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, key: str, data: Dict[str, Any]) -> "OverrideRecord":
        return cls(key=key, value=data.get("value"),
                   actor=str(data.get("actor", "") or ""),
                   risk=str(data.get("risk", "") or ""),
                   updated_at=str(data.get("updated_at", "") or ""),
                   previous=data.get("previous"),
                   reason=str(data.get("reason", "") or ""))


class OverrideStore:
    """覆盖层读写（线程安全；带 mtime 缓存）

    Attributes:
        path: 覆盖层文件路径。
    """

    def __init__(self, path: Optional[Any] = None) -> None:
        self._path = Path(path) if path else default_override_path()
        _guard_path(self._path)
        self._lock = threading.RLock()
        self._records: Dict[str, OverrideRecord] = {}
        self._mtime: float = -1.0
        self._loaded = False
        #: 本进程为热生效而写进 os.environ 的开关名 → 原值（None 表示原本未设置）
        self._env_backup: Dict[str, Optional[str]] = {}
        self._load()

    # ── 路径 ──

    @property
    def path(self) -> Path:
        return self._path

    # ── 读 ──

    def _load(self, *, force: bool = False) -> None:
        with self._lock:
            try:
                mtime = self._path.stat().st_mtime if self._path.exists() else -1.0
            except OSError:
                mtime = -1.0
            if self._loaded and not force and mtime == self._mtime:
                return
            records: Dict[str, OverrideRecord] = {}
            if self._path.exists():
                try:
                    raw = json.loads(self._path.read_text(encoding="utf-8") or "{}")
                    items = raw.get("overrides") if isinstance(raw, dict) else None
                    if isinstance(items, dict):
                        for key, data in items.items():
                            if isinstance(data, dict):
                                records[str(key)] = OverrideRecord.from_dict(
                                    str(key), data)
                except (OSError, ValueError) as e:
                    # 覆盖层损坏 → 视为空（回落 config/default），并留日志；不阻断启动
                    logger.warning("[Settings] 覆盖层读取失败（按空处理）: %s", e)
                    records = {}
            self._records = records
            self._mtime = mtime
            self._loaded = True

    def reload(self) -> None:
        """强制重读（测试/多进程场景）"""
        self._load(force=True)

    def get(self, key: str) -> Optional[OverrideRecord]:
        self._load()
        with self._lock:
            rec = self._records.get(str(key))
            return rec

    def entries(self) -> Dict[str, OverrideRecord]:
        self._load()
        with self._lock:
            return dict(self._records)

    def has(self, key: str) -> bool:
        return self.get(key) is not None

    def keys(self) -> List[str]:
        return sorted(self.entries())

    # ── 写 ──

    def _flush_locked(self) -> None:
        """原子落盘（临时文件 + os.replace）"""
        _guard_path(self._path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "note": ("云枢开关覆盖层（TASK-S7-01）。UI 只写本文件；"
                     ".env / config.yaml 永不被本功能修改。"),
            "overrides": {k: v.to_dict() for k, v in sorted(self._records.items())},
        }
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self._path)
        try:
            self._mtime = self._path.stat().st_mtime
        except OSError:                          # pragma: no cover - 极端竞态
            self._mtime = -1.0

    def set(self, key: str, value: Any, *, actor: str = "", risk: str = "",
            reason: str = "", previous: Any = None) -> OverrideRecord:
        """写入/更新一条覆盖（返回落盘后的记录）"""
        with self._lock:
            self._load()
            old = self._records.get(str(key))
            rec = OverrideRecord(
                key=str(key), value=value, actor=str(actor or ""),
                risk=str(risk or ""), reason=str(reason or ""),
                updated_at=_now_iso(),
                previous=previous if previous is not None
                else (old.value if old is not None else None))
            self._records[rec.key] = rec
            self._flush_locked()
            return rec

    def clear(self, key: str) -> Optional[OverrideRecord]:
        """删除一条覆盖（回到 config/default）；不存在返回 None"""
        with self._lock:
            self._load()
            rec = self._records.pop(str(key), None)
            if rec is not None:
                self._flush_locked()
            return rec

    def clear_all(self) -> List[str]:
        """清空覆盖层（测试/应急用）；返回被清除的键"""
        with self._lock:
            self._load()
            keys = sorted(self._records)
            self._records = {}
            self._flush_locked()
            return keys

    # ── 进程内 env 应用记账 ──

    def mark_env_applied(self, env_name: str, previous: Optional[str]) -> None:
        """记录「本进程把该 env 设成了覆盖值」，以及原值（回滚用）"""
        with self._lock:
            self._env_backup.setdefault(str(env_name), previous)

    def env_applied(self, env_name: str) -> bool:
        """该 env 是否由**本进程**（而非运维）设置"""
        with self._lock:
            return str(env_name) in self._env_backup

    def env_backup(self, env_name: str) -> Optional[str]:
        with self._lock:
            return self._env_backup.get(str(env_name))

    def forget_env_applied(self, env_name: str) -> None:
        with self._lock:
            self._env_backup.pop(str(env_name), None)

    def applied_env_names(self) -> List[str]:
        with self._lock:
            return sorted(self._env_backup)

    # ── 测试隔离 ──

    def bind(self, path: Any) -> "OverrideStore":
        """把本实例指向另一个覆盖层文件（测试用；不复制旧内容）"""
        new_path = Path(path)
        _guard_path(new_path)
        with self._lock:
            self._path = new_path
            self._records = {}
            self._loaded = False
        self._load(force=True)
        return self

    def snapshot(self) -> Dict[str, Any]:
        return {
            "path": str(self._path),
            "exists": self._path.exists(),
            "count": len(self.entries()),
            "keys": self.keys(),
            "env_applied": self.applied_env_names(),
        }


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


#: 单例（与仓库既有 `get_observability_config()` 同款）
_GLOBAL_STORE: Optional[OverrideStore] = None
_GLOBAL_LOCK = threading.Lock()


def get_override_store() -> OverrideStore:
    """取覆盖层单例"""
    global _GLOBAL_STORE
    with _GLOBAL_LOCK:
        if _GLOBAL_STORE is None:
            _GLOBAL_STORE = OverrideStore()
        return _GLOBAL_STORE


def reset_override_store() -> None:
    """丢弃单例（测试隔离用；下次调用会按当前 env 重新定位文件）"""
    global _GLOBAL_STORE
    with _GLOBAL_LOCK:
        _GLOBAL_STORE = None


__all__ = [
    "ENV_OVERRIDE_PATH", "DEFAULT_OVERRIDE_PATH", "PROTECTED_FILES",
    "SCHEMA_VERSION", "repo_root", "default_override_path",
    "OverrideRecord", "OverrideStore", "get_override_store",
    "reset_override_store",
]
