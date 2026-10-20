# -*- coding: utf-8 -*-
"""回归：SkillsManager.delete 不得默认删除「文件轨独占」技能目录

被断言的产品代码：
  - app_server.py::SkillsManager.delete（加固：force 门禁 + 结构化留痕）

【2026-10-20 · 路由侧已退役】该方法的唯一 HTTP 入口
POST /api/skills/delete 已按 deprecate 台账到期删除
（config/route_unreferenced_triage.json 的判定；前端/脚本/测试均无调用方）。
路由级用例随之移除（force 透传、拒绝原样回传等），**方法级门禁仍在此钉住**：
删除路由后已无生产调用方，方法本体随死代码清理轮再评估去留。

加固前的可达链路（当时实测）：
  POST /api/skills/delete -> app_server.py::SkillsManager.delete
  -> 主轨 svc.store.get(skill_id) 为 None、文件轨 get_metadata 非 None
  -> svc.file_store.delete() -> agent/skills_mgmt/file_store.py shutil.rmtree
  => data/skills_repo/<id>/（skill.md + scripts/ + temp/）整棵**不可逆**删除。
  原唯一守卫是「内置技能白名单」，实测只覆盖 data/skills_repo 下 25 个技能中的 7 个
  => 其余 18 个真实技能可被删。

加固后语义（本文件断言）：
  1. 文件轨独占 + 未显式 force => 默认**拒绝**，目录/文件原地保留，并写结构化
     warning（含 skill_id 与 has_extension_record）；
  2. 有扩展安装记录**不构成**授权（记录只进留痕字段，不参与门禁判定）；
  3. 显式 force=True => 允许删除（能力未丢失，并写留痕）；
  4. 主轨有记录 => 既有删除路径**完全不变**（仍多轨删除，与 force 无关）。

【隔离】主轨/文件轨/扩展存储全部落在 tmp_path：SkillsMgmtService 的 store_path 与
repo_path 都指向 tmp_path，ExtensionStore 也被替换为 tmp_path 实例（连带覆盖
cleanup.remove_skill_everywhere 第 6 步对 extensions.json 的清理）。绝不触碰真实
data/skills_repo 与 agent/data/extensions.json。

【为什么用 AST 抽取 SkillsManager，而不是 import app_server】
  import app_server 实测 80-100s，且带重副作用（起调度线程、加载向量模型……，见
  tests/unit/test_native_preimport.py:19-22 与
  test_server_routes_registration_inventory.py:39-40）。本文件只从 app_server.py
  **源码本体**取出 SkillsManager 类并 exec：被断言的仍是产品源码（把门禁改回
  「默认放行」，本文件立即变红——已实测），却零副作用、秒级完成。
"""

from __future__ import annotations

import ast
import pathlib
from pathlib import Path

import pytest

import agent.extensions.store as ext_store_mod
from agent.extensions.store import ExtensionStore
from agent.skills_mgmt.registry import SkillRegistry
from agent.skills_mgmt.service import SkillsMgmtService

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_SERVER = REPO_ROOT / "app_server.py"


# ═══════════════════════════════════════════════════════════════
#  SkillsManager 装载（AST 抽取源码本体，零 app_server 副作用）
# ═══════════════════════════════════════════════════════════════

class _StubLogger:
    """记录 logger.warning 调用（结构化留痕断言用）"""

    def __init__(self):
        self.warnings = []
        self.infos = []

    def warning(self, msg, *args, **kwargs):
        self.warnings.append(msg)

    def info(self, msg, *args, **kwargs):
        self.infos.append(msg)

    def error(self, msg, *args, **kwargs):
        pass

    def debug(self, msg, *args, **kwargs):
        pass


def _load_skills_manager_class(logger_stub):
    """从 app_server.py 源码抽取 SkillsManager 类并 exec（不 import 整个模块）"""
    tree = ast.parse(APP_SERVER.read_text(encoding="utf-8"),
                     filename=str(APP_SERVER))
    node = next((n for n in tree.body
                 if isinstance(n, ast.ClassDef) and n.name == "SkillsManager"),
                None)
    assert node is not None, "app_server.py 中找不到 SkillsManager 类"
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {"logger": logger_stub, "__name__": "app_server"}
    exec(compile(module, str(APP_SERVER), "exec"), ns)
    return ns["SkillsManager"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离的技能服务 + 扩展存储 + 被测 SkillsManager（绝不写生产数据）"""
    svc = SkillsMgmtService(store_path=str(tmp_path / "skills_mgmt.json"),
                            repo_path=str(tmp_path / "skills_repo"))
    monkeypatch.setattr(SkillRegistry, "_svc", lambda self: svc)
    store = ExtensionStore(data_file=str(tmp_path / "extensions.json"))
    # 替换模块属性：函数内 from ... import ExtensionStore 取到的即此隔离实例
    monkeypatch.setattr(ext_store_mod, "ExtensionStore", lambda *a, **k: store)
    logger_stub = _StubLogger()
    mgr = _load_skills_manager_class(logger_stub)()
    return svc, store, mgr, logger_stub


def _make_file_track_only(svc, skill_id):
    """造一个「文件轨独占」技能（等价于仓库里的 data/skills_repo/<id>/）"""
    svc.file_store.create(
        skill_id,
        meta={"id": skill_id, "name": skill_id, "enabled": True,
              "status": "approved"},
        instruction="# " + skill_id)
    return Path(svc.file_store.repo_path) / skill_id


def _structured_warnings(logger_stub):
    return [w for w in logger_stub.warnings if isinstance(w, dict)]


# ═══════════════════════════════════════════════════════════════
#  ① 文件轨独占默认被拒
# ═══════════════════════════════════════════════════════════════

def test_file_track_only_is_refused_and_dir_survives(env):
    """无主轨记录 => 默认拒绝删除，目录与其内容原地保留，并写结构化留痕"""
    svc, store, mgr, log = env
    skill_dir = _make_file_track_only(svc, "ut_file_track_only")
    before = sorted(str(p.relative_to(skill_dir)) for p in skill_dir.rglob("*"))

    out = mgr.delete("ut_file_track_only")

    assert out["ok"] is False, "文件轨独占技能被默认放行删除"
    assert out.get("refused") is True
    assert "拒绝删除" in out["error"] and "force=True" in out["error"]
    assert skill_dir.exists(), "拒绝后技能目录应当仍在（原实现会整棵删掉）"
    assert (skill_dir / "skill.md").exists()
    assert sorted(str(p.relative_to(skill_dir))
                  for p in skill_dir.rglob("*")) == before
    assert svc.file_store.get_metadata("ut_file_track_only") is not None

    warns = _structured_warnings(log)
    assert warns, "拒绝时必须写结构化 warning 留痕"
    assert warns[0]["action"] == "skills_manager.delete.refused_file_track_only"
    assert warns[0]["skill_id"] == "ut_file_track_only"
    assert warns[0]["has_extension_record"] is False


def test_extension_record_alone_does_not_authorize_deletion(env):
    """有扩展记录但主轨为空 => **仍然拒绝**（记录只进留痕字段）"""
    svc, store, mgr, log = env
    skill_dir = _make_file_track_only(svc, "ut_with_ext_record")
    from agent.extensions.base import (ExtensionMetadata, ExtensionStatus,
                                       ExtensionType)
    rec = ExtensionMetadata(ext_id="ut_with_ext_record",
                            ext_type=ExtensionType.SKILL,
                            name="ut_with_ext_record", description="",
                            source="builtin",
                            status=ExtensionStatus.ENABLED)
    rec.touch()
    rec.installed_at = rec.created_at
    store.add(rec)
    assert store.get(ExtensionType.SKILL, "ut_with_ext_record") is not None

    out = mgr.delete("ut_with_ext_record")

    assert out["ok"] is False, "有扩展记录就放行 => 复现原事故（目录被整棵删掉）"
    assert out.get("refused") is True
    assert skill_dir.exists() and (skill_dir / "skill.md").exists()
    warnings = _structured_warnings(log)
    assert warnings and warnings[0]["has_extension_record"] is True, (
        "留痕应如实记录「有扩展记录」，门禁却不得因此放行")
    # 拒绝时不得篡改扩展存储（保持与磁盘一致）
    assert store.get(ExtensionType.SKILL, "ut_with_ext_record") is not None


# ═══════════════════════════════════════════════════════════════
#  ② force=True 确实删除
# ═══════════════════════════════════════════════════════════════

def test_force_true_still_deletes(env):
    """显式 force=True => 允许删除（能力显式保留，不再默认发生），并写留痕"""
    svc, store, mgr, log = env
    skill_dir = _make_file_track_only(svc, "ut_force_delete")

    out = mgr.delete("ut_force_delete", force=True)

    assert out["ok"] is True, out
    assert not skill_dir.exists(), "force=True 时应当真的删除目录"
    assert svc.file_store.get_metadata("ut_force_delete") is None
    warnings = _structured_warnings(log)
    assert warnings and warnings[0]["action"] == (
        "skills_manager.delete.force_file_track_only")
    assert warnings[0]["skill_id"] == "ut_force_delete"


# ═══════════════════════════════════════════════════════════════
#  ③ 主轨存在的既有路径不变
# ═══════════════════════════════════════════════════════════════

def test_main_track_path_unchanged(env):
    """主轨有记录（真正的「已安装 -> 删除」）=> 仍走 svc.delete 多轨删除，
    与 force 无关、不需要 force、也不写拒绝留痕"""
    svc, store, mgr, log = env
    svc.create_manual({"id": "ut_main_track", "name": "主轨技能",
                       "description": "", "content": "# 主轨技能",
                       "content_type": "markdown", "enabled": True})
    skill_dir = _make_file_track_only(svc, "ut_main_track")   # 多轨态
    assert svc.store.get("ut_main_track") is not None

    out = mgr.delete("ut_main_track")          # 注意：不带 force

    assert out["ok"] is True, out
    assert "refused" not in out
    assert svc.store.get("ut_main_track") is None
    assert not skill_dir.exists()
    assert not _structured_warnings(log), "主轨路径不应触发文件轨独占留痕"


def test_missing_skill_reports_unknown(env):
    """两轨皆无 => 仍是「未知技能」，且**不得**被标成 refused

    （路由靠 refused 区分「显式拒绝」与「未找到」：后者仍要走扩展存储清理，
    否则 Claude Code 技能卸载会回归为删不掉）"""
    svc, store, mgr, log = env

    out = mgr.delete("ut_ghost_skill")

    assert out["ok"] is False
    assert "未知技能" in out["error"]
    assert "refused" not in out

