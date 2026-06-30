"""技能安装器 — 管理应用层技能和 Claude Code 技能

应用层技能：存储在 data/skills.json 中，由 SkillsManager 管理
Claude Code 技能：存储在 .claude/skills/ 中，是独立的技能包

支持：
  - 从内置注册表安装技能
  - 从 GitHub 安装自定义技能
  - 从本地目录安装技能
  - 启用/禁用/配置技能
  - 发现 Claude Code 技能
"""

import json
import uuid
import logging
import os
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime

from agent.extensions.base import (
    ExtensionType, ExtensionStatus, ExtensionMetadata, BUILTIN_EXTENSIONS,
)
from agent.extensions.installer import InstallEngine
from agent.extensions.store import ExtensionStore

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# 应用层技能配置文件（统一使用根目录 data/skills.json 作为唯一数据源）
_SKILLS_FILE = Path(__file__).parents[2] / "data" / "skills.json"

# Claude Code 技能目录
_CLAUDE_SKILLS_DIR = Path.home() / ".claude" / "skills"


class SkillsInstaller:
    """技能安装器 — 管理应用层技能"""

    def __init__(self, store: ExtensionStore):
        self._store = store
        self._engine = InstallEngine()

    # ── 应用层技能管理 ──

    def list_installed_skills(self) -> List[Dict]:
        """列出已安装的应用层技能"""
        try:
            if _SKILLS_FILE.exists():
                with open(_SKILLS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("skills", [])
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "skills_installer", "action": "log", "msg": f"[技能安装器] 加载技能文件失败: {e}"}, ensure_ascii=False))
        return []

    def add_builtin_skill(self, skill_id: str) -> Tuple[bool, str]:
        """从内置注册表安装应用层技能

        Args:
            skill_id: 技能 ID（如 "self_reflection"）

        Returns:
            (成功标志, 消息)
        """
        # 查找内置技能
        builtin = None
        for s in BUILTIN_EXTENSIONS.get("skill", []):
            if s["id"] == skill_id and s.get("builtin"):
                builtin = s
                break

        if not builtin:
            return False, f"未找到内置技能: {skill_id}"

        # 读取现有技能
        skills = self.list_installed_skills()

        # 检查是否已存在
        if any(s["id"] == skill_id for s in skills):
            return True, f"技能已存在: {skill_id}"

        # 添加技能
        skills.append({
            "id": skill_id,
            "name": builtin["name"],
            "enabled": True,
            "description": builtin["description"],
            "params": {},
        })

        # 保存
        _SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_SKILLS_FILE, "w", encoding="utf-8") as f:
            json.dump({"skills": skills}, f, ensure_ascii=False, indent=2)

        # 记录到扩展存储
        meta = ExtensionMetadata(
            ext_id=skill_id,
            ext_type=ExtensionType.SKILL,
            name=builtin["name"],
            description=builtin["description"],
            source="builtin",
            status=ExtensionStatus.ENABLED,
        )
        meta.touch()
        meta.installed_at = meta.created_at
        self._store.add(meta)

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "skills_installer", "action": "skill_id", "msg": f"[技能安装器] 已安装内置技能: {skill_id}"}, ensure_ascii=False))
        return True, f"已安装技能: {builtin['name']}"

    def add_custom_skill(
        self, skill_id: str, name: str, description: str = "",
        params: Optional[Dict] = None,
    ) -> Tuple[bool, str]:
        """添加自定义应用层技能

        Args:
            skill_id: 技能唯一 ID
            name: 技能显示名称
            description: 技能描述
            params: 技能参数

        Returns:
            (成功标志, 消息)
        """
        skills = self.list_installed_skills()

        if any(s["id"] == skill_id for s in skills):
            return False, f"技能已存在: {skill_id}"

        if not name or not name.strip():
            return False, "自定义技能名称不能为空"

        skills.append({
            "id": skill_id,
            "name": name,
            "enabled": True,
            "description": description,
            "params": params or {},
        })

        _SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_SKILLS_FILE, "w", encoding="utf-8") as f:
            json.dump({"skills": skills}, f, ensure_ascii=False, indent=2)

        # 记录到扩展存储
        meta = ExtensionMetadata(
            ext_id=skill_id,
            ext_type=ExtensionType.SKILL,
            name=name,
            description=description,
            source="manual",
            status=ExtensionStatus.ENABLED,
            config=params or {},
        )
        meta.touch()
        meta.installed_at = meta.created_at
        self._store.add(meta)

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "skills_installer", "action": "skill_id", "msg": f"[技能安装器] 已添加自定义技能: {skill_id}"}, ensure_ascii=False))
        return True, f"已添加技能: {name}"

    def remove_skill(self, skill_id: str) -> Tuple[bool, str]:
        """移除应用层技能"""
        skills = self.list_installed_skills()
        before = len(skills)
        skills = [s for s in skills if s["id"] != skill_id]

        if len(skills) < before:
            _SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(_SKILLS_FILE, "w", encoding="utf-8") as f:
                json.dump({"skills": skills}, f, ensure_ascii=False, indent=2)

            self._store.remove(ExtensionType.SKILL, skill_id)
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "skills_installer", "action": "skill_id", "msg": f"[技能安装器] 已移除技能: {skill_id}"}, ensure_ascii=False))
            return True, f"已移除技能: {skill_id}"

        return False, f"技能不存在: {skill_id}"

    def toggle_skill(self, skill_id: str, enabled: bool = None) -> Tuple[bool, str, bool]:
        """切换技能启用状态

        Returns:
            (成功标志, 消息, 当前启用状态)
        """
        skills = self.list_installed_skills()
        for s in skills:
            if s["id"] == skill_id:
                if enabled is not None:
                    s["enabled"] = enabled
                else:
                    s["enabled"] = not s.get("enabled", True)

                _SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(_SKILLS_FILE, "w", encoding="utf-8") as f:
                    json.dump({"skills": skills}, f, ensure_ascii=False, indent=2)

                status = ExtensionStatus.ENABLED if s["enabled"] else ExtensionStatus.DISABLED
                self._store.update_status(ExtensionType.SKILL, skill_id, status)
                logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "skills_installer", "action": "skill_id.status.value", "msg": f"[技能安装器] 已切换技能状态: {skill_id} → {status.value}"}, ensure_ascii=False))
                return True, f"技能 {'已启用' if s['enabled'] else '已禁用'}: {skill_id}", s["enabled"]

        return False, f"技能不存在: {skill_id}", False

    def update_skill_params(self, skill_id: str, params: Dict) -> Tuple[bool, str]:
        """更新技能参数"""
        skills = self.list_installed_skills()
        for s in skills:
            if s["id"] == skill_id:
                s.setdefault("params", {}).update(params)

                _SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(_SKILLS_FILE, "w", encoding="utf-8") as f:
                    json.dump({"skills": skills}, f, ensure_ascii=False, indent=2)

                self._store.update_config(ExtensionType.SKILL, skill_id, params)
                logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "skills_installer", "action": "skill_id", "msg": f"[技能安装器] 已更新技能参数: {skill_id}"}, ensure_ascii=False))
                return True, f"已更新技能参数: {skill_id}"

        return False, f"技能不存在: {skill_id}"

    # ── Claude Code 技能管理 ──

    def list_claude_skills(self) -> List[Dict]:
        """列出磁盘上所有 Claude Code 技能文件（不等于已注册为扩展）"""
        skills_dir = Path(_CLAUDE_SKILLS_DIR)
        if not skills_dir.exists():
            return []

        # 获取已在扩展商店中注册的 Claude 技能 ID
        registered_ids = set()
        try:
            for m in self._store.list_all(ExtensionType.CLAUDE_SKILL):
                registered_ids.add(m.ext_id)
        except Exception:
            pass

        result = []
        for item in skills_dir.iterdir():
            if item.is_dir() or item.is_symlink():
                skill_info = self._read_claude_skill_info(item)
                if skill_info:
                    skill_info["registered"] = skill_info["id"] in registered_ids
                    result.append(skill_info)

        return result

    def _read_claude_skill_info(self, skill_path: Path) -> Optional[Dict]:
        """读取 Claude Code 技能信息"""
        info = {
            "id": skill_path.name,
            "name": skill_path.name,
            "path": str(skill_path),
            "description": "",
            "type": "claude_skill",
        }

        # 尝试从 README.md 获取描述
        readme = skill_path / "README.md"
        if readme.exists():
            try:
                import re
                content = readme.read_text(encoding="utf-8", errors="replace")
                # 取第一段作为描述
                match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
                if match:
                    info["name"] = match.group(1).strip()
                # 取第一段非标题文本
                paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
                for p in paragraphs:
                    if not p.startswith("#"):
                        info["description"] = p[:200]
                        break
            except Exception:
                pass

        # 尝试从 frontmatter 获取
        meta_file = skill_path / "meta.json"  # Claude Code skills 没有固定标准
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text())
                info.update(meta)
            except Exception:
                pass

        return info

    def install_claude_skill(self, source: str, skill_name: str = None) -> Tuple[bool, str]:
        """安装 Claude Code 技能

        source 格式: github:user/repo, url:https://..., local:/path
        skill_name: 可选，指定技能目录名（默认从 source 推断）

        如果技能文件已存在于磁盘上，自动注册到扩展商店。
        """
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "skills_installer", "action": "claude.code.source", "msg": f"[技能安装器] 安装 Claude Code 技能: source={source}, name={skill_name}"}, ensure_ascii=False))

        ext_type, location, subpath = self._engine.parse_source(source)
        skills_dir = _CLAUDE_SKILLS_DIR
        skills_dir.mkdir(parents=True, exist_ok=True)

        # 确定目标目录名
        target_name = skill_name or location.split("/")[-1].replace(".git", "")
        target_dir = skills_dir / target_name

        # 如果目录已存在，直接注册到商店（视为已安装）
        if target_dir.exists():
            # 检查是否已在商店中注册
            existing = self._store.get(ExtensionType.CLAUDE_SKILL, target_name)
            if existing:
                return True, f"Claude Code 技能已安装: {target_name}"

            # 目录存在但未注册 → 注册到商店
            skill_info = self._read_claude_skill_info(target_dir)
            meta = ExtensionMetadata(
                ext_id=target_name,
                ext_type=ExtensionType.CLAUDE_SKILL,
                name=skill_info.get("name", target_name),
                description=skill_info.get("description", ""),
                source=source,
                source_url=source,
                install_path=str(target_dir),
                status=ExtensionStatus.INSTALLED,
            )
            meta.touch()
            meta.installed_at = meta.created_at
            self._store.add(meta)
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "skills_installer", "action": "claude.code.target_name", "msg": f"[技能安装器] 已注册本地 Claude Code 技能: {target_name}"}, ensure_ascii=False))
            return True, f"已注册 Claude Code 技能: {target_name}（文件已存在）"

        success = False
        if ext_type == "github":
            success = self._engine.download_from_github(location, subpath, target_dir)
        elif ext_type == "url":
            success = self._engine.download_from_url(location, target_dir)
        elif ext_type == "local":
            success = self._engine.copy_from_local(location, target_dir)

        if not success:
            return False, f"安装失败: 无法从 {source} 获取技能包"

        # 检查是否包含技能文件
        if not list(target_dir.iterdir()):
            shutil.rmtree(target_dir, ignore_errors=True)
            return False, "安装失败: 技能包为空"

        skill_info = self._read_claude_skill_info(target_dir)

        # 记录到扩展存储
        meta = ExtensionMetadata(
            ext_id=target_name,
            ext_type=ExtensionType.CLAUDE_SKILL,
            name=skill_info.get("name", target_name),
            description=skill_info.get("description", ""),
            source=source,
            source_url=source,
            install_path=str(target_dir),
            status=ExtensionStatus.INSTALLED,
        )
        meta.touch()
        meta.installed_at = meta.created_at
        self._store.add(meta)

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "skills_installer", "action": "claude.code.target_name", "msg": f"[技能安装器] Claude Code 技能安装完成: {target_name}"}, ensure_ascii=False))
        return True, f"已安装 Claude Code 技能: {target_name}"

    def uninstall_claude_skill(self, skill_name: str) -> Tuple[bool, str]:
        """卸载 Claude Code 技能"""
        target_dir = _CLAUDE_SKILLS_DIR / skill_name
        if not target_dir.exists():
            return False, f"技能不存在: {skill_name}"

        shutil.rmtree(target_dir, ignore_errors=True)
        self._store.remove(ExtensionType.CLAUDE_SKILL, skill_name)

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "skills_installer", "action": "claude.code.skill_name", "msg": f"[技能安装器] 已卸载 Claude Code 技能: {skill_name}"}, ensure_ascii=False))
        return True, f"已卸载 Claude Code 技能: {skill_name}"

    # ── 发现 ──

    def discover_available_skills(self) -> Dict[str, List[Dict]]:
        """发现所有可用的技能

        返回内置和已安装的合并列表
        """
        builtin_skills = BUILTIN_EXTENSIONS.get("skill", [])
        installed = self.list_installed_skills()
        installed_ids = {s["id"] for s in installed}

        available = []
        for s in builtin_skills:
            available.append({
                **s,
                "installed": s["id"] in installed_ids,
                "type": "skill",
            })

        return {
            "builtin_skills": available,
            "installed_skills": installed,
            "claude_skills": self.list_claude_skills(),
        }
