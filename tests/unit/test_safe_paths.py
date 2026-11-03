# -*- coding: utf-8 -*-
"""`agent/safe_paths.enumerate_path`：逐段列目录 + 名字等值匹配。

这是 C-6-B1 的共用口径（用户输入**不进路径表达式**）。锁四件事：
  1. 绝对/相对路径都能解析到真实存在的条目；
  2. 不存在 ⇒ None（调用方据此 404/空集，不静默回落）；
  3. `..` 一律拒（不跟上跳）；
  4. 内嵌 NUL / 跨盘 ⇒ None。
"""
from __future__ import annotations

import os
from pathlib import Path

from agent.safe_paths import enumerate_path


def test_绝对路径与相对路径都能解析(tmp_path):
    deep = tmp_path / "a" / "b" / "c.txt"
    deep.parent.mkdir(parents=True)
    deep.write_text("x", encoding="utf-8")

    got = enumerate_path(str(deep))
    assert got is not None and got.name == "c.txt"

    # 相对路径必须落在 base 之下（不做上跳）：直接把 base 指到 tmp_path
    rel = enumerate_path("a/b/c.txt", base=tmp_path)
    assert rel is not None and rel.name == "c.txt"


def test_不存在返回_None(tmp_path):
    assert enumerate_path(str(tmp_path / "nope")) is None
    assert enumerate_path("") is None
    assert enumerate_path(None) is None


def test_拒绝上跳(tmp_path):
    (tmp_path / "sub").mkdir()
    assert enumerate_path(str(tmp_path / "sub" / "..")) is None
    assert enumerate_path("../up") is None


def test_拒绝内嵌NUL(tmp_path):
    assert enumerate_path("bad\x00name") is None


def test_跨盘拒绝(tmp_path):
    if os.name != "nt":
        return
    drive = Path(os.path.abspath(tmp_path)).drive  # 例如 "C:"
    other = "Z:" if drive.upper() != "Z:" else "Y:"
    assert enumerate_path(other + "/whatever") is None


def test_空段与点段被忽略(tmp_path):
    f = tmp_path / "keep.txt"
    f.write_text("x", encoding="utf-8")
    assert enumerate_path(str(tmp_path) + "//./" + "keep.txt") is not None