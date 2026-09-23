"""主线档案注册表 —— data/agent_lines/*.yaml 的读写与激活状态

【设计约束】
    【不易】档案是**数据**，不是代码 —— 新增一条主线只写 YAML，零代码改动。
    【不易】破坏性操作（删除/改名）必须显式传 `confirm=True`。
    【变易】目录可用环境变量 `YUNSHU_AGENT_LINES_DIR` 覆盖（测试隔离用）。
    【简易】纯文件 IO，无全局可变状态（除 active 指针，落盘在 _active.json）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional

import yaml

from .models import AGENT_LINES_DIR, LineProfile, load_tool_meta
from .skillpack import known_skill_ids

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
_ACTIVE_FILE = "_active.json"

#: 允许的主线 id：小写字母/数字/下划线/连字符
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")


def lines_dir() -> str:
    return os.environ.get("YUNSHU_AGENT_LINES_DIR") or AGENT_LINES_DIR


def _path_for(line_id: str) -> str:
    return os.path.join(lines_dir(), f"{line_id}.yaml")


def _active_path() -> str:
    return os.path.join(lines_dir(), _ACTIVE_FILE)


class LineRegistryError(Exception):
    """主线档案操作异常"""


class LineRegistry:
    """主线档案的读写入口（无状态，可随时新建）"""

    # ── 读 ──

    def list_ids(self) -> List[str]:
        d = lines_dir()
        if not os.path.isdir(d):
            return []
        return sorted(
            os.path.splitext(f)[0]
            for f in os.listdir(d)
            if f.endswith(".yaml")
        )

    def load(self, line_id: str) -> Optional[LineProfile]:
        path = _path_for(line_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as e:
            raise LineRegistryError(f"主线档案读取失败 {line_id}: {e}") from e
        if not isinstance(raw, dict):
            raise LineRegistryError(f"主线档案格式非法（应为字典）: {line_id}")
        raw.setdefault("id", line_id)
        try:
            return LineProfile.from_dict(raw)
        except ValueError as e:
            raise LineRegistryError(f"主线档案校验失败 {line_id}: {e}") from e

    def load_all(self) -> Dict[str, LineProfile]:
        out: Dict[str, LineProfile] = {}
        for lid in self.list_ids():
            try:
                prof = self.load(lid)
            except LineRegistryError as e:
                logger.warning("[lines] 跳过损坏的档案: %s", e)
                continue
            if prof is not None:
                out[lid] = prof
        return out

    def effective(self, line_id: Optional[str]) -> Optional[LineProfile]:
        """解析"本次请求生效的主线"：显式指定优先，否则激活指针，否则 None"""
        if line_id:
            return self.load(line_id)
        active = self.get_active()
        return self.load(active) if active else None

    # ── 写 ──

    def save(self, profile: LineProfile, *, overwrite: bool = True) -> str:
        if not _ID_RE.match(profile.id or ""):
            raise LineRegistryError(
                f"主线 id 非法: {profile.id!r}（需小写字母开头，含小写字母/数字/_/-，2-41 字符）")
        # 与 known_tools 同款口径：把**真实**的工具名与技能 id 传进校验，
        # 让 typo 在保存时就失败，而不是等运行时静默失效。
        # 【不易】技能目录为空 ⇒ 传 None（只做结构校验）：环境读不到目录时
        # 不能把所有技能声明一律判成「未注册」——那是把环境故障说成数据错误。
        known = set(load_tool_meta().keys())
        known_skills = set(known_skill_ids())
        issues = profile.validate(known_tools=known or None,
                                  known_skills=known_skills or None)
        if issues:
            raise LineRegistryError("主线档案校验失败: " + "; ".join(issues))

        d = lines_dir()
        os.makedirs(d, exist_ok=True)
        path = _path_for(profile.id)
        with _LOCK:
            if os.path.exists(path) and not overwrite:
                raise LineRegistryError(f"主线已存在: {profile.id}")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(profile.to_yaml())
            os.replace(tmp, path)   # 原子替换，避免半写文件被读到
        logger.info("[lines] 主线档案已保存: %s", profile.id)
        return path

    def create(self, raw: Dict[str, Any]) -> LineProfile:
        prof = LineProfile.from_dict(raw)
        self.save(prof, overwrite=False)
        return prof

    def delete(self, line_id: str, *, confirm: bool = False) -> bool:
        if not confirm:
            raise LineRegistryError("删除主线需要 confirm=True")
        path = _path_for(line_id)
        with _LOCK:
            if not os.path.exists(path):
                return False
            os.remove(path)
            if self.get_active() == line_id:
                self.set_active(None)
        logger.warning("[lines] 主线档案已删除: %s", line_id)
        return True

    # ── 激活指针 ──

    def get_active(self) -> Optional[str]:
        try:
            with open(_active_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            val = data.get("active")
            return str(val) if val else None
        except (OSError, ValueError):
            return None

    def set_active(self, line_id: Optional[str]) -> Optional[str]:
        """设置全局激活主线；传 None 表示回到"不装线"（全量工具）"""
        d = lines_dir()
        os.makedirs(d, exist_ok=True)
        if line_id and not os.path.exists(_path_for(line_id)):
            raise LineRegistryError(f"主线不存在: {line_id}")
        with _LOCK:
            tmp = _active_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"active": line_id}, f, ensure_ascii=False)
            os.replace(tmp, _active_path())
        logger.info("[lines] 激活主线: %s", line_id)
        return line_id


#: 模块级单例（无状态，安全）
_default = LineRegistry()


def get_line_registry() -> LineRegistry:
    return _default


__all__ = ["LineRegistry", "LineRegistryError", "get_line_registry", "lines_dir"]
