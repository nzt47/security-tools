"""扩展数据存储 — 管理扩展的持久化配置和状态

使用 JSON 文件存储所有扩展的元数据，支持增删改查。
文件位置: agent/data/extensions.json
"""

import json
import uuid
import os
import shutil
import threading
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

from agent.extensions.base import ExtensionMetadata, ExtensionType, ExtensionStatus
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# 默认扩展数据目录
_EXTENSIONS_DATA_DIR = Path(__file__).parent.parent / "data"
_EXTENSIONS_DATA_FILE = _EXTENSIONS_DATA_DIR / "extensions.json"

#: 写盘备份保留份数（与 .env.backups 的 50 同族；这里每次保存都留一份，20 份足够）
_BACKUP_KEEP = 20

#: 进程内写锁：一次保存是"读-改-整份重写"，两个线程交错会把对方的改动写丢
_STORE_LOCK = threading.RLock()

# 扩展包存储目录
_EXTENSIONS_PACKAGES_DIR = _EXTENSIONS_DATA_DIR / "extensions_packages"


class ExtensionStore:
    """扩展数据存储 — 持久化管理所有扩展的元数据"""

    def __init__(self, data_file: str = None):
        self._data_file = Path(data_file) if data_file else _EXTENSIONS_DATA_FILE
        self._cache: Optional[Dict[str, List[Dict]]] = None

    def _load(self) -> Dict[str, List[Dict]]:
        """从文件加载扩展数据"""
        if self._cache is not None:
            return self._cache

        try:
            if self._data_file.exists():
                with open(self._data_file, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
                logger.info(log_dict({'module_name': 'store', 'action': 'self._data_file', 'msg': f'[扩展存储] 已加载扩展数据: {self._data_file}'}))
            else:
                self._cache = {
                    "skills": [],
                    "mcps": [],
                    "channels": [],
                    "plugins": [],
                }
                self._save(self._cache)
                logger.info(log_dict({'module_name': 'store', 'action': 'self._data_file', 'msg': f'[扩展存储] 已创建扩展数据文件: {self._data_file}'}))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(log_dict({'module_name': 'store', 'action': 'log', 'msg': f'[扩展存储] 加载失败: {e}，使用空数据'}))
            self._cache = {
                "skills": [], "mcps": [],
                "channels": [], "plugins": [],
            }

        return self._cache

    def _save(self, data: Dict[str, List[Dict]]):
        """保存扩展数据到文件（**原子写 + 写前备份**，2026-11-14）

        【为什么改】原实现是 open(..., "w") 直接截断写：写到一半进程被杀（或被并发
        写交错）就等于**整份扩展台账丢失** —— 而通道清单与通道值只存在这里；
        同一族的 .env 有 50 份备份（CHG-2026-0810），这里此前一份都没有。
        改为 tmp + os.replace（Windows 上同样可覆盖），并在写前把上一版复制到
        <file>.bak.<stamp>（保留 _BACKUP_KEEP 份）。
        """
        with _STORE_LOCK:
            target = self._data_file
            target.parent.mkdir(parents=True, exist_ok=True)
            self._backup_store_file(target)
            tmp = target.with_name(target.name + ".tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(str(tmp), str(target))
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:                    # pragma: no cover
                        pass

    @staticmethod
    def _backup_store_file(target: Path) -> None:
        """写前备份上一版（文件不存在/为空则跳过；失败只 warning，不阻塞保存）"""
        try:
            if not target.exists() or target.stat().st_size == 0:
                return
            stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            backup = target.with_name(target.name + ".bak." + stamp)
            index = 1
            while backup.exists():
                backup = target.with_name(target.name + ".bak." + stamp + "_" + str(index))
                index += 1
            shutil.copy2(target, backup)
            olds = sorted(target.parent.glob(target.name + ".bak.*"))
            for old in olds[:-_BACKUP_KEEP]:
                try:
                    old.unlink()
                except OSError:                        # pragma: no cover
                    pass
        except OSError as exc:                         # pragma: no cover
            logger.warning(log_dict({
                'module_name': 'store', 'action': 'backup_failed',
                'msg': f'[扩展存储] 写前备份失败（不阻塞保存）: {type(exc).__name__}',
            }))

    def _get_key(self, ext_type: ExtensionType) -> str:
        """扩展类型到存储键的映射"""
        mapping = {
            ExtensionType.SKILL: "skills",
            ExtensionType.MCP: "mcps",
            ExtensionType.CHANNEL: "channels",
            ExtensionType.PLUGIN: "plugins",
        }
        return mapping[ext_type]

    def list_all(self, ext_type: Optional[ExtensionType] = None) -> List[Dict]:
        """列出扩展，可按类型筛选"""
        data = self._load()
        if ext_type:
            key = self._get_key(ext_type)
            return data.get(key, [])
        result = []
        for key in ["skills", "mcps", "channels", "plugins"]:
            result.extend(data.get(key, []))
        return result

    def get(self, ext_type: ExtensionType, ext_id: str) -> Optional[Dict]:
        """获取单个扩展"""
        items = self.list_all(ext_type)
        return next((i for i in items if i.get("ext_id") == ext_id), None)

    def add(self, metadata: ExtensionMetadata):
        """添加扩展记录"""
        data = self._load()
        key = self._get_key(metadata.ext_type)
        existing = next(
            (i for i in data[key] if i.get("ext_id") == metadata.ext_id),
            None,
        )
        if existing:
            existing.update(metadata.to_dict())
            logger.info(log_dict({'module_name': 'store', 'action': 'metadata.ext_id', 'msg': f'[扩展存储] 已更新扩展: {metadata.ext_id}'}))
        else:
            data[key].append(metadata.to_dict())
            logger.info(log_dict({'module_name': 'store', 'action': 'metadata.ext_id', 'msg': f'[扩展存储] 已添加扩展: {metadata.ext_id}'}))
        self._save(data)
        self._cache = data

    def remove(self, ext_type: ExtensionType, ext_id: str) -> bool:
        """移除扩展记录"""
        data = self._load()
        key = self._get_key(ext_type)
        before = len(data[key])
        data[key] = [i for i in data[key] if i.get("ext_id") != ext_id]
        if len(data[key]) < before:
            self._save(data)
            self._cache = data
            logger.info(log_dict({'module_name': 'store', 'action': 'ext_id', 'msg': f'[扩展存储] 已移除扩展: {ext_id}'}))
            return True
        return False

    def update_status(
        self, ext_type: ExtensionType, ext_id: str,
        status: ExtensionStatus, config: Optional[Dict] = None,
        enabled: Optional[bool] = None,
    ) -> bool:
        """更新扩展状态和配置。

        【enabled 与 status 必须同步（2026-11-12 现场 bug）】此前 `toggle` 只改 `status`，
        而**真正决定发不发**的是 `enabled`（ChannelInstaller.send_message / channel_bridge
        都读它）⇒ 界面上点「停用」只改了标签，通道照发；再点一次还是同一个状态，
        于是"停用按钮不起作用"。这里一次写入两个字段，让"一条扩展只有一处真相"。
        """
        data = self._load()
        key = self._get_key(ext_type)
        for item in data[key]:
            if item.get("ext_id") == ext_id:
                item["status"] = status.value if isinstance(status, ExtensionStatus) else status
                item["updated_at"] = __import__("datetime").datetime.now().isoformat()
                if enabled is not None:
                    item["enabled"] = bool(enabled)
                if status == ExtensionStatus.INSTALLED and not item.get("installed_at"):
                    item["installed_at"] = item["updated_at"]
                if config:
                    item.setdefault("config", {}).update(config)
                self._save(data)
                self._cache = data
                logger.info(log_dict({'module_name': 'store', 'action': 'ext_id.status.value', 'msg': f'[扩展存储] 已更新状态: {ext_id} → {status.value}'}))
                return True
        return False

    def update_config(self, ext_type: ExtensionType, ext_id: str, config: Dict) -> bool:
        """只更新扩展配置（**不改 status / enabled**）

        【2026-11-14 修复】原实现委托 update_status(..., ENABLED, ...)：保存一次配置
        就把"停用"的通道顺手改回 enabled ⇒ 界面上的"改配置"与"启用"变成同一件事
        （现场表现：停用某通道后改了下收件人，它又开始发了）。
        现在这里只动 config + updated_at；启停仍然只走 toggle_channel/update_status。
        """
        if not config:
            return False
        data = self._load()
        key = self._get_key(ext_type)
        for item in data[key]:
            if item.get("ext_id") == ext_id:
                item.setdefault("config", {}).update(config)
                item["updated_at"] = datetime.now().isoformat()
                self._save(data)
                self._cache = data
                logger.info(log_dict({
                    'module_name': 'store', 'action': 'config_updated',
                    'msg': f'[扩展存储] 已更新配置（不改启停）: {ext_id}',
                }))
                return True
        return False

    def ensure_packages_dir(self) -> Path:
        """确保扩展包存储目录存在"""
        _EXTENSIONS_PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
        return _EXTENSIONS_PACKAGES_DIR

    def clear_cache(self):
        """清空内存缓存"""
        self._cache = None
