# -*- coding: utf-8 -*-
"""动态工具持久化：来源脚本目录不导入、重复加载不产生别名。

2026-10-27 实测的两个副作用：
1. `register` 把来源脚本复制进 `custom/<id>/scripts/` 后，`_iter_custom_modules` 会把
   那里的 .py 当工具模块导入 ⇒ 每次启动一条「未导出 register_all（跳过）」误导性 warning；
2. `load_dynamic_tools` 不幂等：启动期与 `_get_ext_manager` 各调一次会重复注册，
   工具名被自动加后缀（`name_2`）⇒ 工具集出现重复项。
"""
from __future__ import annotations

from agent.tools import persistence


def test_iter_custom_modules_skips_source_scripts(monkeypatch, tmp_path):
    custom = tmp_path / "custom" / "ingested"
    (custom / "scripts").mkdir(parents=True)
    (custom / "scripts" / "deploy.sh").write_text("echo hi\n", encoding="utf-8")
    (custom / "scripts" / "subscribe_cdn.py").write_text("print(1)\n", encoding="utf-8")
    (custom / "demo_dyn.py").write_text(
        "def register_all(dl=None):\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(persistence, "CUSTOM_TOOLS_DIR", str(tmp_path / "custom"))

    paths = [p.replace("\\", "/") for p in persistence._iter_custom_modules()]
    assert any(p.endswith("demo_dyn.py") for p in paths)
    assert not any("scripts/" in p for p in paths), paths


def test_load_dynamic_tools_is_idempotent(monkeypatch, tmp_path):
    from agent import tools as T

    custom = tmp_path / "custom" / "ingested"
    custom.mkdir(parents=True)
    (custom / "demo_dyn.py").write_text(
        "def register_all(dl=None):\n"
        "    from agent.tools import register_dynamic\n"
        "    register_dynamic('demo_dyn_tool', '演示', handler=lambda **k: {'ok': True},\n"
        "                     schema={'type': 'object', 'properties': {}})\n",
        encoding="utf-8")
    monkeypatch.setattr(persistence, "CUSTOM_TOOLS_DIR", str(tmp_path / "custom"))
    monkeypatch.setattr(persistence, "TOOL_DEFS_DIR", str(tmp_path / "defs"))
    monkeypatch.setattr(persistence, "_loaded_modules", {})
    monkeypatch.setattr(persistence, "_read_ledger", lambda: {"version": 1, "tools": {}})
    monkeypatch.setattr(persistence, "_write_ledger", lambda data: None)
    try:
        assert persistence.load_dynamic_tools() == 1
        names = [t.get("name") for t in T.list_tools()]
        assert names.count("demo_dyn_tool") == 1, names
        # 启动期与 _get_ext_manager 都会调用：第二次必须跳过，不得变成 demo_dyn_tool_2
        assert persistence.load_dynamic_tools() == 0
        names = [t.get("name") for t in T.list_tools()]
        assert names.count("demo_dyn_tool") == 1, names
    finally:
        for key in ("demo_dyn_tool", "demo_dyn_tool_2"):
            T._registry.pop(key, None)  # noqa: SLF001 测试清理：注册表是模块级单例
