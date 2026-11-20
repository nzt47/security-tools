# -*- coding: utf-8 -*-
"""UI 原语量具的口径守卫（2026-11-20）。

锁三件事：
  1. `.test.` 文件不计入 raw_* / raw_heading（合成反例不是产品代码）；
  2. CSS 自定义属性**定义**行（--x: #hex）不计入硬编码颜色（色板声明）；
  3. 规则里的字面值照常计数。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _mod():
    spec = importlib.util.spec_from_file_location(
        "ui_primitive_convergence_under_test",
        _ROOT / "scripts" / "audit" / "ui_primitive_convergence.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_测试文件不计入原生控件(tmp_path):
    src = tmp_path / "src"
    (src / "components" / "ui").mkdir(parents=True)
    (src / "A.tsx").write_text("<div><input /><button>x</button><h1>t</h1></div>\n",
                               encoding="utf-8")
    (src / "A.test.tsx").write_text("<div><input /><button>x</button><h1>t</h1></div>\n",
                                    encoding="utf-8")
    c = _mod().scan(src)
    assert c["raw_input"] == 1 and c["raw_button"] == 1 and c["raw_heading"] == 1


def test_css_变量定义不算硬编码_规则里的照常算(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "theme.css").write_text(
        ":root {\n  --brand: #58a6ff;\n  --panel: rgba(13, 17, 23, 0.9);\n}\n"
        ".a { color: #fff; background: var(--panel); }\n",
        encoding="utf-8")
    c = _mod().scan(src)
    assert c["css_hardcoded_color"] == 1, c



def test_css_import_必须在规则之前(tmp_path):
    """2026-11-21 事故：调色板 :root 插到最前面，把 @import 挤到规则后 ⇒ 整条被忽略。"""
    src = tmp_path / "src"
    src.mkdir()
    (src / "ok.css").write_text(
        "@charset \"utf-8\";\n@import 'lib/base.css';\n.a { color: red; }\n",
        encoding="utf-8")
    (src / "bad.css").write_text(
        ":root { --x: #fff; }\n.a { color: var(--x); }\n@import 'lib/base.css';\n",
        encoding="utf-8")
    mod = _mod()
    bad = mod.style_import_violations(src)
    assert [row[0] for row in bad] == ["bad.css"], bad


def test_仓库里的css_import都在最前面():
    mod = _mod()
    bad = mod.style_import_violations(_ROOT / "yunshu-ui" / "src")
    assert bad == [], bad
