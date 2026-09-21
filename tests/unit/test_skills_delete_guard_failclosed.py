# -*- coding: utf-8 -*-
"""L38 回归：POST /api/skills/delete 的安全守卫**不得静默失效**（fail-closed）

被断言的产品代码：plugins/skills.py::api_skills_delete（提交 adb09347 之前为 fail-open）

背景（主会话实测）：
  该路由 → app_server.py:751-765 SkillsManager.delete → 主轨空则 svc.file_store.delete()
  → agent/skills_mgmt/file_store.py 的 shutil.rmtree —— **不可逆的整树删除**。
  原实现把「内置技能不可删除」这段守卫包在
      except Exception: pass
  里：一旦 `from agent.extensions.base import BUILTIN_EXTENSIONS` 失败
  （该 import 因规避循环导入而**延迟到函数内**，确有可能失败），
  守卫就**静默消失**、删除照常继续。
  ⇒ 本测试钉住：清单加载失败时**必须拒绝删除**，且**不得**调用 delete。

【为什么用桩】测试绝不触碰真实 data/skills_repo 与 agent/data/extensions.json：
  app_server 与 agent.extensions.* 一律以桩注入 sys.modules（monkeypatch 自动还原）。
"""
from __future__ import annotations

import sys
import types

import pytest
from flask import Flask

import plugins.skills as sk


def _raw_view():
    """剥掉 @_require_token / @_log_request 的惰性包装，取回原始视图函数。

    plugins/skills.py 的 _lazy_wrap 用 functools.wraps，故 __wrapped__ 链可逐层解开。
    这样测试无需真实令牌，也不会触发 app_server 的重量级导入。
    """
    fn = sk.api_skills_delete
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


class _SpyMgr:
    """记录 delete 调用的替身；调用即视为「不可逆删除被放行」。"""

    def __init__(self):
        self.calls = []

    def delete(self, skill_id):
        self.calls.append(skill_id)
        return {"ok": True}


def _stub_app_server(monkeypatch, spy):
    mod = types.ModuleType("app_server")
    mod._skills_mgr = spy
    monkeypatch.setitem(sys.modules, "app_server", mod)


def _stub_base_with_list(monkeypatch, skills):
    """base 桩：提供真实 ExtensionType + 可控 BUILTIN_EXTENSIONS。"""
    import agent.extensions.base as real_base
    mod = types.ModuleType("agent.extensions.base")
    mod.BUILTIN_EXTENSIONS = {"skill": skills}
    mod.ExtensionType = real_base.ExtensionType
    monkeypatch.setitem(sys.modules, "agent.extensions.base", mod)
    return mod


def _stub_base_unimportable(monkeypatch):
    """base 桩：属性访问即抛 ImportError —— 复现 `from ... import ...` 失败。"""

    class _Blocked(types.ModuleType):
        def __getattr__(self, name):
            raise ImportError(f"blocked for L38 test: {name}")

    monkeypatch.setitem(sys.modules, "agent.extensions.base",
                        _Blocked("agent.extensions.base"))


def _call_delete(payload):
    app = Flask(__name__)
    with app.test_request_context("/api/skills/delete", method="POST", json=payload):
        resp = _raw_view()()
        return resp.get_json()


def test_builtin_skill_is_refused_and_delete_never_called(monkeypatch):
    """正路：内置技能直接拒绝，且**不触碰** delete（该分支在 import _skills_mgr 之前返回）。"""
    spy = _SpyMgr()
    _stub_app_server(monkeypatch, spy)
    _stub_base_with_list(monkeypatch, [
        {"id": "memory_summary", "name": "记忆摘要", "builtin": True},
    ])
    out = _call_delete({"id": "memory_summary"})
    assert out["ok"] is False
    assert "内置技能不可删除" in out["error"]
    assert spy.calls == [], "内置技能被拒绝时不得调用 delete"


def test_guard_fails_closed_when_builtin_list_unavailable(monkeypatch):
    """核心回归：清单加载失败 ⇒ **拒绝删除**，且**不得**调用 delete（改造前为静默放行）。"""
    spy = _SpyMgr()
    _stub_app_server(monkeypatch, spy)
    _stub_base_unimportable(monkeypatch)
    out = _call_delete({"id": "pd-test-driven-development-8562c8ad-skill"})
    assert out["ok"] is False, "守卫加载失败时必须 fail-closed（拒绝）"
    assert "加载失败" in out["error"]
    assert "拒绝删除" in out["error"]
    assert spy.calls == [], (
        "fail-closed 失效：清单加载失败时仍调用了 delete ⇒ 不可逆删除被放行")


def test_normal_path_still_deletes_non_builtin_skill(monkeypatch):
    """反向对照：清单可用且非内置 ⇒ 正常路径**未被改坏**，delete 恰好被调用一次。"""
    spy = _SpyMgr()
    _stub_app_server(monkeypatch, spy)
    _stub_base_with_list(monkeypatch, [
        {"id": "memory_summary", "builtin": True},
    ])
    # 扩展存储桩：避免真实写 agent/data/extensions.json
    store_mod = types.ModuleType("agent.extensions.store")

    class _Store:
        def __init__(self, *a, **k):
            pass

        def remove(self, *a, **k):
            return False

    store_mod.ExtensionStore = _Store
    monkeypatch.setitem(sys.modules, "agent.extensions.store", store_mod)
    out = _call_delete({"id": "some_custom_skill"})
    assert spy.calls == ["some_custom_skill"], (
        "正常路径被改坏：可删除的非内置技能应放行到 delete")
    assert out.get("ok") is True
