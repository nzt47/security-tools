# -*- coding: utf-8 -*-
"""L20 回归：remove_skill 不得删除"文件轨独占"技能目录

实测链路（L20 事实）：
    tests/performance/test_ecosystem_performance.py:132-133
    -> agent/extensions/manager.py:185 installer.remove_skill(ext_id)
    -> agent/extensions/skills_installer.py else 分支（主轨为 None、文件轨 metadata 非 None）
    -> agent/skills_mgmt/file_store.py:702 shutil.rmtree(skill_dir)
    => data/skills_repo/memory_summary/ 整棵树（skill.md + scripts/ + temp/）被真实删除。

加固后语义（本文件所断言）：
  1. 主轨无记录 + 文件轨有目录  => 默认**拒绝删除**，目录仍在（与是否有 extension
     记录无关：实测 agent/data/extensions.json:29-39 中 memory_summary 的记录
     source="builtin"/install_path="" 正是 add_builtin_skill 登记出来的，光看
     "有无扩展记录" 挡不住这条链路）；
  2. 显式 force=True => 允许删除（能力未丢失，且写留痕）；
  3. 主轨有记录（真正的"扩展已安装 -> 卸载"）=> 行为**完全不变**，仍然多轨删除。

隔离：主轨/文件轨/扩展存储全部落在 tmp_path（沿用 TASK-05 的隔离手段），绝不触碰
data/skills_repo 与 agent/data/extensions.json。
"""

from pathlib import Path

import pytest

from agent.extensions.base import (
    ExtensionMetadata, ExtensionStatus, ExtensionType,
)
from agent.extensions.skills_installer import SkillsInstaller
from agent.extensions.store import ExtensionStore
from agent.skills_mgmt.registry import SkillRegistry
from agent.skills_mgmt.service import SkillsMgmtService


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离的技能服务 + 扩展存储 + 安装器（绝不写生产数据）"""
    svc = SkillsMgmtService(store_path=str(tmp_path / "skills_mgmt.json"),
                           repo_path=str(tmp_path / "skills_repo"))
    monkeypatch.setattr(SkillRegistry, "_svc", lambda self: svc)
    store = ExtensionStore(data_file=str(tmp_path / "extensions.json"))
    return svc, store, SkillsInstaller(store)


def _make_file_track_only(svc, skill_id="memory_summary"):
    """造一个"文件轨独占"技能（等价于仓库里的 data/skills_repo/<id>/）"""
    svc.file_store.create(
        skill_id,
        meta={"id": skill_id, "name": "记忆摘要", "enabled": True,
              "status": "approved"},
        instruction="# 记忆摘要")
    return Path(svc.file_store.repo_path) / skill_id


def _add_builtin_like_record(store, skill_id="memory_summary"):
    """复刻 add_builtin_skill 登记的扩展记录（source=builtin、install_path 空）"""
    meta = ExtensionMetadata(
        ext_id=skill_id,
        ext_type=ExtensionType.SKILL,
        name="记忆摘要",
        description="定期压缩历史对话为结构化摘要",
        source="builtin",
        status=ExtensionStatus.ENABLED,
    )
    meta.touch()
    meta.installed_at = meta.created_at
    store.add(meta)


def test_file_track_only_is_refused_and_dir_survives(env):
    """无主轨记录 => 拒绝删除，目录/文件原地保留"""
    svc, store, installer = env
    skill_dir = _make_file_track_only(svc)
    before = sorted(p.name for p in skill_dir.rglob("*"))

    ok, msg = installer.remove_skill("memory_summary")

    assert ok is False
    assert "拒绝删除" in msg and "force=True" in msg
    assert skill_dir.exists() and (skill_dir / "skill.md").exists()
    assert sorted(p.name for p in skill_dir.rglob("*")) == before


def test_extension_record_alone_does_not_authorize_deletion(env):
    """有扩展安装记录但主轨为空（= L20 事故的真实形态）=> 仍然拒绝"""
    svc, store, installer = env
    skill_dir = _make_file_track_only(svc)
    _add_builtin_like_record(store)   # 事故现场确有这条记录（source=builtin）
    assert store.get(ExtensionType.SKILL, "memory_summary") is not None

    ok, msg = installer.remove_skill("memory_summary")

    assert ok is False, "有扩展记录就放行 ⇒ 复现 L20 事故（目录会被整棵删掉）"
    assert skill_dir.exists() and (skill_dir / "skill.md").exists()
    # 拒绝时不得篡改扩展存储（保持与磁盘一致）
    assert store.get(ExtensionType.SKILL, "memory_summary") is not None


def test_force_true_still_deletes(env):
    """显式 force=True => 允许删除（能力显式保留，不再默认发生）"""
    svc, store, installer = env
    skill_dir = _make_file_track_only(svc)
    _add_builtin_like_record(store)

    ok, msg = installer.remove_skill("memory_summary", force=True)

    assert ok is True, msg
    assert not skill_dir.exists()
    assert store.get(ExtensionType.SKILL, "memory_summary") is None


def test_main_track_uninstall_path_unchanged(env):
    """真正的"扩展已安装 -> 卸载"（主轨有记录）=> 照常多轨删除"""
    svc, store, installer = env
    ok, msg = installer.add_custom_skill("my_tool", "我的工具", "自定义技能")
    assert ok is True, msg
    skill_dir = _make_file_track_only(svc, "my_tool")   # 多轨态：主轨 + 文件轨都在
    assert svc.store.get("my_tool") is not None

    ok, msg = installer.remove_skill("my_tool")

    assert ok is True, msg
    assert svc.store.get("my_tool") is None
    assert not skill_dir.exists()
    assert store.get(ExtensionType.SKILL, "my_tool") is None


def test_missing_skill_reports_not_found(env):
    """两轨皆无 => 仍然是"技能不存在"（错误语义不变）"""
    svc, store, installer = env
    ok, msg = installer.remove_skill("ghost_skill")
    assert ok is False and "技能不存在" in msg