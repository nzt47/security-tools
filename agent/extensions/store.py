"""扩展数据存储 — 管理扩展的持久化配置和状态

使用 JSON 文件存储所有扩展的元数据，支持增删改查。
文件位置: agent/data/extensions.json
"""

import json
import os
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

from agent.extensions.base import ExtensionMetadata, ExtensionType, ExtensionStatus

logger = logging.getLogger(__name__)

# 默认扩展数据目录
_EXTENSIONS_DATA_DIR = Path(__file__).parent.parent / "data"
_EXTENSIONS_DATA_FILE = _EXTENSIONS_DATA_DIR / "extensions.json"

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
                logger.info(f"[扩展存储] 已加载扩展数据: {self._data_file}")
            else:
                self._cache = {
                    "skills": [],
                    "claude_skills": [],
                    "mcps": [],
                    "channels": [],
                    "plugins": [],
                }
                self._save(self._cache)
                logger.info(f"[扩展存储] 已创建扩展数据文件: {self._data_file}")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[扩展存储] 加载失败: {e}，使用空数据")
            self._cache = {
                "skills": [], "claude_skills": [], "mcps": [],
                "channels": [], "plugins": [],
            }

        return self._cache

    def _save(self, data: Dict[str, List[Dict]]):
        """保存扩展数据到文件"""
        self._data_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._data_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _get_key(self, ext_type: ExtensionType) -> str:
        """扩展类型到存储键的映射"""
        mapping = {
            ExtensionType.SKILL: "skills",
            ExtensionType.CLAUDE_SKILL: "claude_skills",
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
        for key in ["skills", "claude_skills", "mcps", "channels", "plugins"]:
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
            logger.info(f"[扩展存储] 已更新扩展: {metadata.ext_id}")
        else:
            data[key].append(metadata.to_dict())
            logger.info(f"[扩展存储] 已添加扩展: {metadata.ext_id}")
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
            logger.info(f"[扩展存储] 已移除扩展: {ext_id}")
            return True
        return False

    def update_status(
        self, ext_type: ExtensionType, ext_id: str,
        status: ExtensionStatus, config: Optional[Dict] = None,
    ) -> bool:
        """更新扩展状态和配置"""
        data = self._load()
        key = self._get_key(ext_type)
        for item in data[key]:
            if item.get("ext_id") == ext_id:
                item["status"] = status.value if isinstance(status, ExtensionStatus) else status
                item["updated_at"] = __import__("datetime").datetime.now().isoformat()
                if status == ExtensionStatus.INSTALLED and not item.get("installed_at"):
                    item["installed_at"] = item["updated_at"]
                if config:
                    item.setdefault("config", {}).update(config)
                self._save(data)
                self._cache = data
                logger.info(f"[扩展存储] 已更新状态: {ext_id} → {status.value}")
                return True
        return False

    def update_config(self, ext_type: ExtensionType, ext_id: str, config: Dict) -> bool:
        """更新扩展配置"""
        return self.update_status(ext_type, ext_id, ExtensionStatus.ENABLED, config)

    def ensure_packages_dir(self) -> Path:
        """确保扩展包存储目录存在"""
        _EXTENSIONS_PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
        return _EXTENSIONS_PACKAGES_DIR

    def clear_cache(self):
        """清空内存缓存"""
        self._cache = None
