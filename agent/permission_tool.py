"""权限与菜单管理工具（通用、可扩展）

设计原则：
- 数据（菜单目录 / 角色注册表）与逻辑（过滤 / 判定）分离
- PermissionManager 为统一入口；数据源可替换（代码常量 / JSON / 数据库）
- 角色支持热注册（register_role），权限码集中判定（has_permission），
  菜单过滤与操作级权限共用同一套权限语义

契约（与前端 yunshu-ui 共用）：
- 权限码：如 'system:user:view'（菜单级）/ 'system:log:export'（操作级）
- admin 角色通配：拥有全部权限，无需权限码
- 其余角色：权限码需命中 user.permissions 集合
"""

from __future__ import annotations

from typing import Iterable


class PermissionManager:
    """菜单过滤 + 权限判定 + 角色注册的统一服务

    用法：
        pm = PermissionManager(menu_catalog, roles)
        visible_menus = pm.filter_menus(user)      # 按角色/权限过滤后的菜单树
        can_export = pm.has_permission(user, "system:log:export")  # 操作级判定
    """

    def __init__(self, menu_catalog: list[dict], roles: dict[str, dict] | None = None):
        self._catalog = menu_catalog
        self._roles: dict[str, dict] = dict(roles or {})

    # ─────────────────────────── 角色管理（扩展点） ───────────────────────────

    def register_role(self, role: dict) -> None:
        """注册 / 覆盖角色：{"name": "manager", "label": "...", "permissions": [...]}

        支持从数据库 / 配置动态加载新角色（热注册），无需改类。
        """
        self._roles[role["name"]] = role

    def get_role(self, name: str) -> dict | None:
        """按角色名取角色定义（无则 None）"""
        return self._roles.get(name)

    def has_role(self, name: str) -> bool:
        return name in self._roles

    # ─────────────────────────── 权限判定 ───────────────────────────

    def has_permission(self, user: dict, code: str) -> bool:
        """操作级权限判定：空权限码视为公开；admin 角色通配；其余命中 permissions 集合"""
        if not code:
            return True
        if user.get("role") == "admin":
            return True
        return code in (user.get("permissions") or [])

    def has_any_permission(self, user: dict, codes: Iterable[str]) -> bool:
        """任一权限码命中（OR 语义）"""
        return any(self.has_permission(user, code) for code in codes)

    # ─────────────────────────── 菜单过滤 ───────────────────────────

    def filter_menus(self, user: dict, catalog: list[dict] | None = None) -> list[dict]:
        """按用户角色/权限过滤菜单树（默认使用构造时传入的目录，也可临时指定）

        规则：
        1. 节点 authority 未命中 → 剔除
        2. 子项全部被剔除的分组 → 一并剔除；有部分子项可见时分组保留
        """
        return self._filter_nodes(catalog if catalog is not None else self._catalog, user)

    def _filter_nodes(self, nodes: list[dict], user: dict) -> list[dict]:
        result: list[dict] = []
        for node in nodes:
            authority = node.get("authority")
            if authority and not self.has_permission(user, authority):
                continue
            children = None
            if node.get("children"):
                children = self._filter_nodes(node["children"], user)
                if not children:
                    continue
            item = dict(node)
            item["children"] = children
            result.append(item)
        return result
