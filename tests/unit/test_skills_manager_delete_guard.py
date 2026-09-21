# -*- coding: utf-8 -*-
"""回归：SkillsManager.delete 不得默认删除「文件轨独占」技能目录

被断言的产品代码：
  - app_server.py::SkillsManager.delete（本次加固：force 门禁 + 结构化留痕）
  - plugins/skills.py::api_skills_delete（force 透传 + 拒绝原因原样回传）

加固前的可达链路（实测）：
  POST /api/skills/delete -> app_server.py::SkillsManager.delete
  -> 主轨 svc.store.get(skill_id) 为 None、文件轨 get_metadata 非 None
  -> svc.file_store.delete() -> agent/skills_mgmt/file_store.py:702 shutil.rmtree
  => data/skills_repo/<id>/（skill.md + scripts/ + temp/）整棵**不可逆**删除。
  该路由唯一的原始守卫是「内置技能白名单」（plugins/skills.py:567-570），
  实测只覆盖 data/skills_repo 下 25 个技能中的 7 个 => 其余 18 个真实技能可被删。

加固后语义（本文件断言）：
  1. 文件轨独占 + 未显式 force => 默认**拒绝**，目录/文件原地保留，并写结构化
     warning（含 skill_id 与 has_extension_record）；
  2. 有扩展安装记录**不构成**授权（记录只进留痕字段，不参与门禁判定）；
  3. 显式 force=True => 允许删除（能力未丢失，并写留痕）；
  4. 主轨有记录 => 既有删除路径**完全不变**（仍多轨删除，与 force 无关）；
  5. 路由：force 只认 JSON 布尔 true（fail-closed），显式拒绝时回传可读原因且
     **不再**走扩展存储清理；「未找到」等其它失败仍走既有清理（Claude Code 技能
     卸载路径不回归）。

【隔离】主轨/文件轨/扩展存储全部落在 tmp_path：SkillsMgmtService 的 store_path 与
repo_path 都指向 tmp_path，ExtensionStore 也被替换为 tmp_path 实例（连带覆盖
cleanup.remove_skill_everywhere 第 6 步对 extensions.json 的清理）。绝不触碰真实
data/skills_repo 与 agent/data/extensions.json。路由用例里的 _skills_mgr 一律用桩。

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
import sys
import types
from pathlib import Path

import pytest
from flask import Flask

import agent.extensions.store as ext_store_mod
import plugins.skills as sk
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


# ═══════════════════════════════════════════════════════════════
#  ④ 路由：force 透传 + 拒绝原样回传（真实 plugins/skills.py）
# ═══════════════════════════════════════════════════════════════

def _raw_delete_view():
    """剥掉 @_require_token / @_log_request 的惰性包装，取回原始视图函数"""
    fn = sk.api_skills_delete
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


class _SpyMgr:
    """记录 delete 调用的替身"""

    def __init__(self, result=None):
        self.calls = []
        self._result = {"ok": True} if result is None else result

    def delete(self, skill_id, force=False):
        self.calls.append((skill_id, force))
        return dict(self._result)


class _SpyExtStore:
    """扩展存储替身：记录 remove 调用（拒绝时**不得**被触碰）"""

    instances = []
    remove_calls = []

    def __init__(self, *a, **k):
        _SpyExtStore.instances.append(self)

    def remove(self, ext_type, ext_id):
        _SpyExtStore.remove_calls.append((ext_type, ext_id))
        return False


@pytest.fixture
def route_env(monkeypatch):
    """路由隔离：app_server / BUILTIN_EXTENSIONS / ExtensionStore 全部用桩"""
    _SpyExtStore.instances = []
    _SpyExtStore.remove_calls = []
    import agent.extensions.base as real_base
    base_stub = types.ModuleType("agent.extensions.base")
    base_stub.BUILTIN_EXTENSIONS = {"skill": [
        {"id": "memory_summary", "name": "记忆摘要", "builtin": True}]}
    base_stub.ExtensionType = real_base.ExtensionType
    monkeypatch.setitem(sys.modules, "agent.extensions.base", base_stub)
    store_stub = types.ModuleType("agent.extensions.store")
    store_stub.ExtensionStore = _SpyExtStore
    monkeypatch.setitem(sys.modules, "agent.extensions.store", store_stub)

    def _install(spy_mgr):
        mod = types.ModuleType("app_server")
        mod._skills_mgr = spy_mgr
        monkeypatch.setitem(sys.modules, "app_server", mod)

    return _install


def _call_route(payload):
    app = Flask(__name__)
    with app.test_request_context("/api/skills/delete", method="POST",
                                  json=payload):
        return _raw_delete_view()().get_json()


def test_route_passes_force_through(route_env):
    """请求体 force=true => 透传 force=True（前端加一个字段即可保留删除能力）"""
    spy = _SpyMgr()
    route_env(spy)
    out = _call_route({"id": "ut_x", "force": True})
    assert spy.calls == [("ut_x", True)]
    assert out.get("ok") is True


def test_route_defaults_to_no_force(route_env):
    """不带 force => 透传 False（默认拒绝，门禁不可被前端省略绕过）"""
    spy = _SpyMgr()
    route_env(spy)
    _call_route({"id": "ut_x"})
    assert spy.calls == [("ut_x", False)]


def test_route_non_boolean_force_is_not_authorization(route_env):
    """force 只认 JSON 布尔 true：字符串 / 数字一律不作为授权（fail-closed）"""
    spy = _SpyMgr()
    route_env(spy)
    _call_route({"id": "ut_x", "force": "true"})
    _call_route({"id": "ut_x", "force": 1})
    _call_route({"id": "ut_x", "force": "false"})
    assert spy.calls == [("ut_x", False)] * 3


def test_route_returns_readable_reason_and_skips_ext_store(route_env):
    """显式拒绝 => 原样回传可读原因，且**不再**走扩展存储清理（避免假成功）"""
    spy = _SpyMgr({"ok": False, "refused": True,
                   "error": "拒绝删除: 技能 ut_x 仅存在于文件轨（主轨无记录），"
                            "其目录由技能仓库/内置技能提供，删除不可逆；"
                            "如确需删除请显式 force=True"})
    route_env(spy)
    out = _call_route({"id": "ut_x"})
    assert out["ok"] is False
    assert "拒绝删除" in out["error"] and "force=True" in out["error"], (
        "拒绝原因必须回传到调用方，不能被路由的「未找到技能」兜底覆盖")
    assert _SpyExtStore.remove_calls == [], (
        "拒绝路径不得触碰扩展存储（否则会返回 ok=True 的假成功）")


def test_route_unknown_skill_still_consults_ext_store(route_env):
    """反向对照：「未找到」（非 refused）仍走既有扩展存储清理 ——
    Claude Code 技能卸载等既有行为不得回归"""
    spy = _SpyMgr({"ok": False, "error": "未知技能: ut_claude_skill"})
    route_env(spy)
    out = _call_route({"id": "ut_claude_skill"})
    assert _SpyExtStore.remove_calls, "非 refused 失败路径仍应查扩展存储"
    assert out["ok"] is False and "未找到技能" in out["error"]
