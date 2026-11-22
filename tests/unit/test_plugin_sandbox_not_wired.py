"""守卫：PluginSandbox 不得被生产代码误接成“安全边界”（P0 诚实化）。"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROD_ROOTS = [ROOT / "agent", ROOT / "plugins", ROOT / "app_server.py"]


def _iter_py():
    for root in PROD_ROOTS:
        if root.is_file():
            yield root
        else:
            yield from root.rglob("*.py")


def test_no_production_import_of_experimental_sandbox():
    offenders = []
    for path in _iter_py():
        if path.name == "sandbox.py" and path.parent.name == "extensions":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == "agent.extensions.sandbox" or mod.endswith(".extensions.sandbox"):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
                for alias in node.names:
                    if alias.name == "sandbox" and mod.endswith("extensions"):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "agent.extensions.sandbox":
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, (
        "生产代码又 import 了未接线的 PluginSandbox；它不是隔离边界，"
        "要么删除该 import，要么先把子进程隔离真正接上：\n  " + "\n  ".join(offenders)
    )


def test_plugin_installer_does_not_use_sandbox():
    src = (ROOT / "agent" / "extensions" / "plugins_installer.py").read_text(encoding="utf-8")
    assert "execute_in_sandbox" not in src
    assert "PluginSandbox" not in src
