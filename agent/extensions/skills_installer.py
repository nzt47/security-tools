"""技能安装器 — 管理应用层技能

应用层技能的**数据主轨**是技能注册表（SkillRegistry：主轨 JSON + 文件轨 skill.md）。

支持：
  - 从内置注册表安装技能
  - 从 GitHub / 本地目录安装自定义技能
  - 启用/禁用/配置技能
  - 发现可用技能（含内置注册表 + 注册表当前启用态）

【2026-10-18】原「Claude Code 技能」三分支（list/install/uninstall_claude_skill）
已删除：它只把包拷进 ~/.claude/skills/ 并登记一条扩展记录，云枢自身从不读该目录。
外部 Claude 技能包改走 技能中心 · 维护 →「从 .claude/skills 导入」
（POST /api/skills-mgmt/import/claude → agent/skills_mgmt/claude_import.py）。
"""

import uuid
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime

from agent.extensions.base import (
    ExtensionType, ExtensionStatus, ExtensionMetadata, BUILTIN_EXTENSIONS,
)
from agent.extensions.installer import InstallEngine
from agent.extensions.store import ExtensionStore
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# 应用层技能配置文件（统一使用根目录 data/skills.json 作为唯一数据源）
_SKILLS_FILE = Path(__file__).parents[2] / "data" / "skills.json"


class SkillsInstaller:
    """技能安装器 — 管理应用层技能"""

    def __init__(self, store: ExtensionStore):
        self._store = store
        self._engine = InstallEngine()

    # ── 应用层技能管理 ──

    def list_installed_skills(self) -> List[Dict]:
        """列出已安装的应用层技能（统一读 SkillRegistry 视图）"""
        try:
            from agent.skills_mgmt.registry import SkillRegistry
            return SkillRegistry().as_legacy_rows()
        except Exception as e:  # noqa: BLE001
            logger.warning(log_dict({'module_name': 'skills_installer', 'action': 'log', 'msg': f'[技能安装器] 读取技能注册表失败: {e}'}))
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

        # 【legacy 迁移】内置 persona 技能在文件轨已有 skill.md（front matter
        # enabled），安装 = 确保 registry 能查到并启用；不再直写 data/skills.json
        try:
            from agent.skills_mgmt.registry import SkillRegistry
            reg = SkillRegistry()
            if reg.is_enabled(skill_id):
                # 已存在且启用 → 幂等成功（但确保 ExtensionStore 有记录）
                pass
            else:
                res = reg.set_enabled(skill_id, True)
                if not res.get("ok"):
                    # 文件轨也没有 → 落主轨创建
                    svc = reg._svc()
                    svc.create_manual({
                        "id": skill_id,
                        "name": builtin.get("name", skill_id),
                        "description": builtin.get("description", ""),
                        "content": f"# {builtin.get('name', skill_id)}\n\n"
                                   f"{builtin.get('description', '')}",
                        "content_type": "markdown",
                        "enabled": True,
                    })
        except Exception as e:  # noqa: BLE001
            return False, f"写入技能注册表失败: {e}"

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

        logger.info(log_dict({'module_name': 'skills_installer', 'action': 'skill_id', 'msg': f'[技能安装器] 已安装内置技能: {skill_id}'}))
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

        # 【legacy 迁移】技能数据落主轨（SkillsMgmtService），不再直写
        # data/skills.json（该文件已是只读兼容快照）
        try:
            from agent.skills_mgmt.registry import SkillRegistry
            reg = SkillRegistry()
            reg._svc().create_manual({
                "id": skill_id,
                "name": name,
                "description": description or "",
                "content": f"# {name}\n\n{description}",
                "content_type": "markdown",
                "enabled": True,
                "default_params": params or {},
            })
        except Exception as e:  # noqa: BLE001
            return False, f"写入技能注册表失败: {e}"

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

        logger.info(log_dict({'module_name': 'skills_installer', 'action': 'skill_id', 'msg': f'[技能安装器] 已添加自定义技能: {skill_id}'}))
        return True, f"已添加技能: {name}"

    def remove_skill(self, skill_id: str, force: bool = False) -> Tuple[bool, str]:
        """移除应用层技能（registry 主轨/文件轨删除，同步 ExtensionStore）

        【L20 加固 · 文件轨独占技能不再被整棵删目录】
        可达条件（实测）：主轨 svc.store.get(skill_id) 为 None（该技能没有主轨行）
        **且**文件轨 svc.file_store.get_metadata(skill_id) 非 None ⇒ 原实现直接走
        svc.file_store.delete() → shutil.rmtree(skill_dir)
        （agent/skills_mgmt/file_store.py:694-712），把 data/skills_repo/<id>/ 整棵树
        （含 scripts/ 与 temp/ 业务模板）不可逆删除。

        这类"文件轨独占"技能是**技能仓库/内置 persona 技能**（front matter 提供），
        并非扩展安装产物：扩展安装（add_builtin_skill / add_custom_skill）要么经
        create_manual 落主轨，要么只登记一条 extension 记录（实测
        agent/data/extensions.json:29-39 中 memory_summary 为 source="builtin"、
        install_path=""），磁盘目录从来不是扩展装出来的。
        ⇒ 本方法作为**扩展卸载**入口，对"文件轨独占"技能默认**拒绝删除**；
        确需删除必须显式 force=True（并写结构化留痕）。

        Args:
            skill_id: 技能 ID
            force: True 时允许删除"文件轨独占"技能（默认 False；
                主轨存在的既有卸载路径与 force 无关，完全不变）

        Returns:
            (成功标志, 消息)
        """
        try:
            from agent.skills_mgmt.registry import SkillRegistry
            reg = SkillRegistry()
            svc = reg._svc()
            if svc.store.get(skill_id) is not None:
                # ── 既有合法卸载路径（主轨存在 → registry 多轨删除）：一字未改 ──
                svc.delete(skill_id)
            else:
                meta = svc.file_store.get_metadata(skill_id)
                if meta is None:
                    return False, f"技能不存在: {skill_id}"
                # ── L20 加固：文件轨独占 ⇒ 拒绝（除非显式 force）──
                ext_meta = self._store.get(ExtensionType.SKILL, skill_id)
                if not force:
                    logger.warning(log_dict({
                        'module_name': 'skills_installer',
                        'action': 'remove_skill.refused_file_track_only',
                        'skill_id': skill_id,
                        'has_extension_record': ext_meta is not None,
                        'extension_source': (ext_meta or {}).get('source'),
                        'msg': ('[技能安装器] 拒绝删除文件轨独占技能 %s：'
                                '主轨无记录 ⇒ 该目录不是扩展安装产物'
                                '（确认删除请显式 force=True）' % skill_id),
                    }))
                    return False, (
                        f"拒绝删除: 技能 {skill_id} 仅存在于文件轨（主轨无记录），"
                        f"其目录由技能仓库/内置技能提供，删除不可逆；"
                        f"如确需删除请显式 force=True"
                    )
                logger.warning(log_dict({
                    'module_name': 'skills_installer',
                    'action': 'remove_skill.force_file_track_only',
                    'skill_id': skill_id,
                    'has_extension_record': ext_meta is not None,
                    'msg': '[技能安装器] 显式 force=True 删除文件轨独占技能: %s' % skill_id,
                }))
                svc.file_store.delete(skill_id)
            self._store.remove(ExtensionType.SKILL, skill_id)
            logger.info(log_dict({'module_name': 'skills_installer', 'action': 'skill_id', 'msg': f'[技能安装器] 已移除技能: {skill_id}'}))
            return True, f"已移除技能: {skill_id}"
        except Exception as e:  # noqa: BLE001
            return False, f"移除失败: {e}"

    def toggle_skill(self, skill_id: str, enabled: bool = None) -> Tuple[bool, str, bool]:
        """切换技能启用状态（registry 主轨/文件轨，同步 ExtensionStore）"""
        try:
            from agent.skills_mgmt.registry import SkillRegistry
            reg = SkillRegistry()
            if not reg.is_enabled(skill_id) and enabled is None:
                cur = False
            else:
                cur = reg.is_enabled(skill_id)
            target = enabled if enabled is not None else (not cur)
            res = reg.set_enabled(skill_id, target)
            if not res.get("ok"):
                return False, f"技能不存在: {skill_id}", False

            status = ExtensionStatus.ENABLED if target else ExtensionStatus.DISABLED
            self._store.update_status(ExtensionType.SKILL, skill_id, status)
            logger.info(log_dict({'module_name': 'skills_installer', 'action': 'skill_id.status.value', 'msg': f'[技能安装器] 已切换技能状态: {skill_id} → {status.value}'}))
            return True, f"技能 {'已启用' if target else '已禁用'}: {skill_id}", target
        except Exception as e:  # noqa: BLE001
            return False, f"切换失败: {e}", False

    def update_skill_params(self, skill_id: str, params: Dict) -> Tuple[bool, str]:
        """更新技能参数（registry 主轨 default_params，同步 ExtensionStore）"""
        try:
            from agent.skills_mgmt.registry import SkillRegistry
            svc = SkillRegistry()._svc()
            if svc.store.get(skill_id) is None:
                return False, f"技能不存在: {skill_id}"
            svc.update(skill_id, {"default_params": params})
            self._store.update_config(ExtensionType.SKILL, skill_id, params)
            logger.info(log_dict({'module_name': 'skills_installer', 'action': 'skill_id', 'msg': f'[技能安装器] 已更新技能参数: {skill_id}'}))
            return True, f"已更新技能参数: {skill_id}"
        except Exception as e:  # noqa: BLE001
            return False, f"更新失败: {e}"

    # ── 发现 ──

    def discover_available_skills(self) -> Dict[str, List[Dict]]:
        """发现所有可用的技能

        返回内置和已安装的合并列表。

        【2026-10-18 · 状态口径统一】每条内置技能额外带 `enabled`（**事实源是
        SkillRegistry**，不是扩展存储）与 `managed_in="skill-center"`。
        【为什么必须补】这 7 个内置 persona 技能在文件轨里是 `enabled: true`（随仓库
        分发、早已参与注入），而扩展存储里可能没有安装记录 ⇒ 市场会把它们显示成
        "可安装"，用户以为没启用。运行时（digital_life_persona._is_skill_enabled /
        _build_skill_instructions）只认 SkillRegistry，故这里也以它为准。
        """
        builtin_skills = BUILTIN_EXTENSIONS.get("skill", [])
        installed = self.list_installed_skills()
        installed_ids = {s["id"] for s in installed}

        reg = None
        try:
            from agent.skills_mgmt.registry import SkillRegistry
            reg = SkillRegistry()
        except Exception:  # noqa: BLE001 注册表不可用 ⇒ enabled 一律 False（不假装已启用）
            reg = None

        available = []
        for s in builtin_skills:
            enabled = False
            if reg is not None:
                try:
                    enabled = bool(reg.is_enabled(s["id"]))
                except Exception:  # noqa: BLE001
                    enabled = False
            available.append({
                **s,
                "installed": s["id"] in installed_ids,
                "enabled": enabled,
                "managed_in": "skill-center",
                "type": "skill",
            })

        return {
            "builtin_skills": available,
            "installed_skills": installed,
        }
