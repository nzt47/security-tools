# -*- coding: utf-8 -*-
"""`agent/safe_paths.enumerate_path`：逐段列目录 + 名字等值匹配。

这是 C-6-B1 的共用口径（用户输入**不进路径表达式**）。锁五件事：
  1. 绝对/相对路径都能解析到真实存在的条目；
  2. 不存在 ⇒ None（调用方据此 404/空集，不静默回落）；
  3. `..` 一律拒（不跟上跳）；
  4. 内嵌 NUL / **不存在的盘** ⇒ None；
  5. Windows 上**另一个真实存在的盘**与 **8.3 短名段**都能解析 —— 这两条不是便利，
     而是 2026-11-15 windows CI 上「成本台账 spent=0.0」的真根因（见下方两条用例）。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from agent import safe_paths
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


def test_不存在的盘返回_None(tmp_path):
    """盘符只能取系统上**真实存在**的那几个（用户串只与 OS 枚举结果比较）"""
    if os.name != "nt":
        return
    drive = Path(os.path.abspath(tmp_path)).drive  # 例如 "C:"
    other = "Z:" if drive.upper() != "Z:" else "Y:"
    assert enumerate_path(other + "/whatever") is None


def test_存在的另一个盘也能解析(tmp_path):
    """Windows：**另一个真实存在的盘**同样可解析（盘符来自 OS 枚举，不是用户串拼路径）

    【为什么必须锁】原实现要求绝对路径与 `cwd` 同盘（"本应用的数据都在同一个盘"）。
    实测这条会静默吃掉读侧数据：CI 上仓库在 `D:\`、pytest 的临时目录在 `C:\`
    ⇒ 事件目录被判"不存在" ⇒ `event_files()` 空 ⇒ **成本护栏读不到自己刚写的账**
    （spent 恒 0、日预算熔断永不触发，且不报错）。用例把那条链走一遍。
    """
    if os.name != "nt":
        return
    here = Path(os.path.abspath(tmp_path)).drive.upper()
    others = [d for d in safe_paths._drive_roots() if d[:2].upper() != here]
    if not others:
        pytest.skip("本机只有一个盘，跨盘行为无法验证")
    probe = tempfile.mkdtemp(prefix="_safe_paths_probe_", dir=others[0])
    try:
        (Path(probe) / "events").mkdir()
        got = enumerate_path(str(Path(probe) / "events"))
        assert got is not None and got.name == "events"
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def test_短名段也能解析(tmp_path, monkeypatch):
    """Windows 的 8.3 短名与长名是同一个目录（`RUNNER~1` ↔ `runneradmin`）

    【为什么必须锁】GitHub Actions 的 `TEMP` 就是短名写法（实测 `.env` 建在
    `C:/Users/RUNNER~1/AppData/Local/Temp/...`）⇒ 逐段按名字严格相等会把整条路径
    判成"不存在"，读侧看到空目录。本机 8dot3 是关的（造不出真短名），故注入
    短名解析函数来钉住**比较逻辑**本身。
    """
    if os.name != "nt":
        return
    real = tmp_path / "pytest-of-runneradmin"
    real.mkdir()

    def fake_short(path):
        text = str(path).replace("\\", "/").lower()
        return text.replace("pytest-of-runneradmin", "pytest~1")

    monkeypatch.setattr(safe_paths, "_short_name", fake_short)
    got = enumerate_path(str(tmp_path / "PYTEST~1"))
    assert got is not None and got.name == "pytest-of-runneradmin"


def test_短名解析只对带波浪号的段尝试(tmp_path, monkeypatch):
    """性能守卫：普通段不触发 `GetShortPathNameW`（每次列目录都问 OS 太贵）"""
    if os.name != "nt":
        return
    target = tmp_path / "plain"
    target.mkdir()
    calls = []

    def counting_short(path):
        calls.append(str(path))
        return ""

    monkeypatch.setattr(safe_paths, "_short_name", counting_short)
    assert enumerate_path(str(target)) is not None
    assert calls == []


def test_空段与点段被忽略(tmp_path):
    f = tmp_path / "keep.txt"
    f.write_text("x", encoding="utf-8")
    assert enumerate_path(str(tmp_path) + "//./" + "keep.txt") is not None