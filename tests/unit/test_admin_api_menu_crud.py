# -*- coding: utf-8 -*-
"""管理后台「菜单管理」CRUD 契约回归（2026-10-18 · 前端 CRUD 全 404 的缺口）。

【背景】前端 yunshu-ui/src/api/menu.ts 按标准 REST 调 POST /api/menu、
PUT /api/menu/<id>、DELETE /api/menu/<id>，而 plugins/admin_api.py 原先只注册了
GET /api/menu/tree ⇒ 菜单管理页「新增/编辑/删除」全部 404（能读、不能写）。
本文件锁住补上的三条写端点，**并**锁住树的形状。

【为什么要专门断言"树"】读端点早先直接回扁平表：HTTP 200、前端却永远看不到
「系统管理」下的子菜单（MenuList 渲染的是 `item.children`）。这类"状态码绿、
功能坏"的缺口只有结构断言能拦 —— 与 tests/unit/test_admin_rbac_persistence.py
用 AST 钉"写端点必须落盘"是同一个理由。

【为什么每个用例都要还原 _MENU_TABLE】菜单表是模块级内存态（演示数据，不落盘），
用例之间必须隔离，否则顺序一换就互相污染。
"""
from __future__ import annotations

import pytest
from flask import Flask

#: 共享 API 令牌（_require_admin 接受「本插件会话令牌」或「共享令牌」二者之一）
TOKEN = "unit-test-token-" + "n" * 40


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FLASK_API_TOKEN", TOKEN)
    import plugins.admin_api as admin_api

    app = Flask(__name__)
    app.register_blueprint(admin_api.bp)
    app.config.update(TESTING=True)
    snapshot = [dict(m) for m in admin_api._MENU_TABLE]
    yield app.test_client(), admin_api
    # 原地还原：不重绑定模块级名字（见 admin_menu_delete 的同款理由）
    admin_api._MENU_TABLE[:] = snapshot


def _auth():
    return {"Authorization": "Bearer " + TOKEN}


def _data(resp):
    return (resp.get_json() or {}).get("data")


def _code(resp):
    return (resp.get_json() or {}).get("code")


def _tree(client):
    return _data(client.get("/api/menu/tree", headers=_auth()))


def _system_children(client):
    """「系统管理」(id=3) 下的子菜单标题，按树上的顺序。"""
    system = next(n for n in _tree(client) if n["id"] == 3)
    return [c["title"] for c in system["children"]]


class TestMenuTreeShape:
    def test_树按_parentId_归并_同级按_order_升序(self, client):
        c, _ = client
        tree = _tree(c)
        # order 1 / 2 / 10 ⇒ 系统管理排最后
        assert [n["title"] for n in tree] == ["仪表盘", "工作台", "系统管理"]
        assert [n["path"] for n in tree] == ["/dashboard", "/workbench", "/system"]
        # 子菜单必须挂在父节点的 children 上（不是并列在顶层）
        assert _system_children(c) == ["用户列表", "角色权限"]
        # 叶子节点的 children 是空数组（前端 `item.children?.length` 直接可用）
        assert tree[0]["children"] == []

    def test_孤儿菜单按根处理而不是消失(self, client):
        """父节点被删/不存在时，子菜单仍要出现在树里 —— 否则数据静默丢。"""
        c, admin_api = client
        admin_api._MENU_TABLE.append({
            "id": 9, "parentId": 404, "title": "孤儿菜单", "path": "/ghost", "icon": "",
            "authority": "", "order": 3, "hideInMenu": False,
        })
        assert "孤儿菜单" in [n["title"] for n in _tree(c)]


class TestMenuCreate:
    def test_新增子菜单_分配下一个_id_并挂到父节点下(self, client):
        c, _ = client
        resp = c.post("/api/menu", json={
            "parentId": 3, "title": "菜单管理", "path": "/system/menu",
            "icon": "Menu", "authority": "system:role:view", "order": 3, "hideInMenu": False,
        }, headers=_auth())
        assert _code(resp) == 200
        created = _data(resp)
        assert created["id"] == 6, "新 id 应为当前最大值 + 1"
        assert created["parentId"] == 3
        assert _system_children(c) == ["用户列表", "角色权限", "菜单管理"]

    def test_新增顶级菜单_按_order_插入到正确位置(self, client):
        c, _ = client
        c.post("/api/menu", json={"parentId": 0, "title": "组件的演示", "path": "/demo", "order": 5},
               headers=_auth())
        assert [n["title"] for n in _tree(c)] == ["仪表盘", "工作台", "组件的演示", "系统管理"]

    @pytest.mark.parametrize("payload,expected", [
        ({"parentId": 0, "title": "", "path": "/x"}, "菜单名与路径不能为空"),
        ({"parentId": 0, "title": "x", "path": "   "}, "菜单名与路径不能为空"),
        ({"parentId": 404, "title": "x", "path": "/x"}, "父菜单不存在"),
    ])
    def test_非法入参被拒且不落表(self, client, payload, expected):
        c, _ = client
        before = len(_tree(c))
        resp = c.post("/api/menu", json=payload, headers=_auth())
        assert _code(resp) == 400
        assert (resp.get_json() or {}).get("message") == expected
        assert len(_tree(c)) == before, "被拒的请求不得改变菜单表"

    def test_hideInMenu_接受布尔与字符串_true(self, client):
        c, _ = client
        a = _data(c.post("/api/menu", json={"parentId": 0, "title": "A", "path": "/a", "hideInMenu": True},
                        headers=_auth()))
        b = _data(c.post("/api/menu", json={"parentId": 0, "title": "B", "path": "/b", "hideInMenu": "true"},
                        headers=_auth()))
        assert a["hideInMenu"] is True
        assert b["hideInMenu"] is True


class TestMenuUpdate:
    def test_编辑只改传入字段_未传字段保持原值(self, client):
        c, _ = client
        resp = c.put("/api/menu/4", json={"title": "用户管理", "order": 9, "hideInMenu": True},
                     headers=_auth())
        assert _code(resp) == 200
        updated = _data(resp)
        assert updated["title"] == "用户管理"
        assert updated["order"] == 9
        assert updated["hideInMenu"] is True
        assert updated["path"] == "/system/user", "未传的 path 不得被清空"
        assert updated["parentId"] == 3, "parentId 不在本端点修改"
        assert _system_children(c) == ["角色权限", "用户管理"], "order 9 ⇒ 排到最后"

    def test_编辑不存在的菜单返回_404_业务码(self, client):
        c, _ = client
        resp = c.put("/api/menu/999", json={"title": "x"}, headers=_auth())
        assert _code(resp) == 404
        assert (resp.get_json() or {}).get("data") is None


class TestMenuDelete:
    def test_删除叶子菜单成功且从树中消失(self, client):
        c, _ = client
        resp = c.delete("/api/menu/4", headers=_auth())
        assert _code(resp) == 200
        assert _system_children(c) == ["角色权限"]

    def test_有子菜单的父节点拒绝删除(self, client):
        c, _ = client
        resp = c.delete("/api/menu/3", headers=_auth())
        assert _code(resp) == 400
        assert (resp.get_json() or {}).get("message") == "请先删除子菜单"
        assert _system_children(c) == ["用户列表", "角色权限"], "拒绝后表不得被改"

    def test_删除不存在的菜单返回_404_业务码(self, client):
        c, _ = client
        assert _code(c.delete("/api/menu/999", headers=_auth())) == 404


class TestMenuAuth:
    @pytest.mark.parametrize("method,path,body", [
        ("post", "/api/menu", {"title": "x", "path": "/x"}),
        ("put", "/api/menu/1", {"title": "x"}),
        ("delete", "/api/menu/4", None),
        ("get", "/api/menu/tree", None),
    ])
    def test_无令牌一律拒绝(self, client, method, path, body):
        c, _ = client
        call = getattr(c, method)
        resp = call(path, json=body) if body is not None else call(path)
        assert _code(resp) == 401, method.upper() + " " + path + " 无令牌时必须拒绝"

    def test_无令牌的写请求不得落表(self, client):
        c, _ = client
        before = [m["title"] for m in _tree(c)]
        c.post("/api/menu", json={"title": "偷偷加", "path": "/sneak"})
        assert [m["title"] for m in _tree(c)] == before
