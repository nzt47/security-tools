# -*- coding: utf-8 -*-
"""管理后台 RBAC 持久化回归（2026-10-07 · M-31 裁决「补成持久化」）。

【锁死两件事】
  1. 行为面：_load_rbac / _save_rbac / reset_rbac 的落盘与重载语义；
     路径由 CP_ADMIN_RBAC_FILE **调用期**解析（换环境变量立刻跟随，不是导入期快照）；
  2. 结构面：**每一个**会改内存态用户/角色的写端点都必须调用 _save_rbac()。
     这是 AST 断言 —— 行为用例覆盖不到"新加一个写端点忘了落盘"，只有结构断言能拦。
"""
from __future__ import annotations

import ast
import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 会修改 _USERS / _ROLES 的写端点（与 plugins/admin_api.py 的路由一一对应）
WRITE_VIEWS = (
    "admin_user_delete",
    "admin_user_create",
    "admin_user_update",
    "admin_role_create",
    "admin_role_permissions",
    "admin_role_data_scope",
    "admin_role_update",
    "admin_role_delete",
)


def _admin():
    return importlib.import_module("plugins.admin_api")


def test_路径由环境变量调用期解析(tmp_path, monkeypatch):
    a = _admin()
    monkeypatch.setenv("CP_ADMIN_RBAC_FILE", str(tmp_path / "a.json"))
    assert a._rbac_file() == str(tmp_path / "a.json")
    # 换环境变量立刻跟随 —— 证明不是导入期快照（本仓第四批 §4.10 的同款要求）
    monkeypatch.setenv("CP_ADMIN_RBAC_FILE", str(tmp_path / "b.json"))
    assert a._rbac_file() == str(tmp_path / "b.json")


def test_文件缺失时回落种子(tmp_path, monkeypatch):
    a = _admin()
    monkeypatch.setenv("CP_ADMIN_RBAC_FILE", str(tmp_path / "missing.json"))
    users, roles = a._load_rbac()
    assert any(u["username"] == "admin" for u in users)
    assert any(r["name"] == "admin" for r in roles)
    assert not (tmp_path / "missing.json").exists(), "只读加载不应创建文件"


def test_落盘后重载可见(tmp_path, monkeypatch):
    a = _admin()
    path = tmp_path / "rbac.json"
    monkeypatch.setenv("CP_ADMIN_RBAC_FILE", str(path))
    a.reset_rbac()
    a._USERS.append({"id": 999, "username": "persisted_probe", "role": "user",
                     "status": 1, "createdAt": "2026-10-07 00:00:00", "permissions": []})
    a._save_rbac()
    assert path.exists()

    # 模拟"进程重启"：把内存态换掉再从磁盘重载
    a._USERS = []
    a._ROLES = []
    a.reset_rbac()
    assert any(u["username"] == "persisted_probe" for u in a._USERS), (
        "落盘的数据重载后应仍在 —— 否则「持久化」名不副实"
    )


def test_写端点都必须落盘_结构断言():
    src = (ROOT / "plugins" / "admin_api.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    missing = []
    for name in WRITE_VIEWS:
        fn = fns.get(name)
        assert fn is not None, name + " 不见了（活体换文件？请同步更新本守卫）"
        calls = [n for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "_save_rbac"]
        if not calls:
            missing.append(name)
    assert not missing, (
        "这些写端点改了内存态却没落盘（M-31 要求持久化）：" + repr(missing)
        + "。加 _save_rbac() 或从 WRITE_VIEWS 里删掉（若它已不写内存态）。"
    )


def test_结构断言对合成样例成立():
    demo = ast.parse("def view():\n    _USERS.append(1)\n    return 0\n")
    fn = [n for n in ast.walk(demo) if isinstance(n, ast.FunctionDef)][0]
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "_save_rbac"]
    assert calls == [], "本判据应能识别出「改了内存态但没调 _save_rbac」"
