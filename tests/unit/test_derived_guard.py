# -*- coding: utf-8 -*-
"""派生面指纹守卫（tests/conftest.py::_guard_derived_assets）—— 2026-11-10 P2。

背景（为什么要有这个守卫）：一次全量 `pytest tests/unit -p no:randomly` 期间，
`data/skills_repo/gh-cli`、`gstack` 的 skill.md/CARD.md 被删、`data/skills_mgmt.json`
少行（线上技能 53→52）、能力清单被重派生、3 个基线测试被改；而当时**定位不到写入方**，
只能靠事后 `git status` 发现。守卫把这件事变成"会话结束点名失败"。

本文件测**守卫自己的逻辑**（纯函数 + 目标集），不依赖真实 data/：
  · 快照/差集：删除 / 改内容 / 新增 三类都能点名；
  · 点开头的内部目录（.index/.migration/.vector_index）不计入 —— 服务会合法重建它们；
  · 目标集必须包含五个"派生物/资产"与三个基线哨兵；
  · 关闸开关 `CP_TEST_GUARD_DERIVED=0`。
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _guard():
    """按路径加载 conftest（rootdir conftest 不能 `import conftest`）"""
    spec = importlib.util.spec_from_file_location(
        "tests_conftest_derived_guard", _ROOT / "tests" / "conftest.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def guard():
    return _guard()


def test_targets_cover_derived_assets_and_sentinels(guard):
    targets = set(guard._DERIVED_GUARD_TARGETS)
    for must in ("data/skills_repo",
                 "data/skills_mgmt.json",
                 "data/capability_manifest.json",
                 "docs/rfc/云枢能力清单盘点表.md",
                 "tests/unit/test_capability_spec.py",
                 "tests/unit/test_capregistry_core.py",
                 "tests/unit/test_confirm_level.py"):
        assert must in targets, f"守卫漏了 {must}"


def test_snapshot_and_diff_report_all_three_kinds(guard, tmp_path):
    (tmp_path / "data" / "skills_repo" / "s1").mkdir(parents=True)
    f_keep = tmp_path / "data" / "skills_repo" / "s1" / "skill.md"
    f_del = tmp_path / "data" / "skills_repo" / "s1" / "CARD.md"
    f_mod = tmp_path / "data" / "skills_mgmt.json"
    f_keep.write_text("keep", encoding="utf-8")
    f_del.write_text("delete me", encoding="utf-8")
    f_mod.write_text('{"a": 1}', encoding="utf-8")

    before = guard._derived_snapshot(tmp_path)
    assert set(before) == {"data/skills_repo/s1/skill.md",
                           "data/skills_repo/s1/CARD.md",
                           "data/skills_mgmt.json"}

    f_del.unlink()                              # 删除
    f_mod.write_text('{"a": 2}', encoding="utf-8")   # 改内容
    (tmp_path / "data" / "skills_repo" / "s2").mkdir()
    (tmp_path / "data" / "skills_repo" / "s2" / "skill.md").write_text("new", encoding="utf-8")

    diff = guard._derived_diff(before, guard._derived_snapshot(tmp_path))
    assert diff["deleted"] == ["data/skills_repo/s1/CARD.md"]
    assert diff["modified"] == ["data/skills_mgmt.json"]
    assert diff["added"] == ["data/skills_repo/s2/skill.md"]


def test_internal_dot_dirs_are_ignored(guard, tmp_path):
    """`.index` / `.migration` / `.vector_index` 是服务合法重建的内部库，不算污染。"""
    skill = tmp_path / "data" / "skills_repo" / "s1"
    skill.mkdir(parents=True)
    (skill / "skill.md").write_text("body", encoding="utf-8")
    for d in (".index", ".migration", ".vector_index"):
        sub = tmp_path / "data" / "skills_repo" / d
        sub.mkdir()
        (sub / "cache.json").write_text("{}", encoding="utf-8")

    snap = guard._derived_snapshot(tmp_path)
    assert snap == {"data/skills_repo/s1/skill.md": snap["data/skills_repo/s1/skill.md"]}


def test_no_change_means_empty_diff(guard, tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "capability_manifest.json").write_text("{}", encoding="utf-8")
    snap = guard._derived_snapshot(tmp_path)
    assert guard._derived_diff(snap, guard._derived_snapshot(tmp_path)) == {
        "deleted": [], "modified": [], "added": []}


def test_kill_switch(guard, monkeypatch):
    monkeypatch.delenv(guard._DERIVED_GUARD_ENV, raising=False)
    assert guard._derived_guard_disabled() is False
    monkeypatch.setenv(guard._DERIVED_GUARD_ENV, "0")
    assert guard._derived_guard_disabled() is True
    monkeypatch.setenv(guard._DERIVED_GUARD_ENV, "OFF")
    assert guard._derived_guard_disabled() is True
    monkeypatch.setenv(guard._DERIVED_GUARD_ENV, "1")
    assert guard._derived_guard_disabled() is False


def test_guard_is_session_scoped_autouse(guard):
    """必须是 session 级 autouse —— 否则起止指纹只覆盖单个用例，等于没守。

    pytest 9 把 `@pytest.fixture` 装饰过的函数包成 `FixtureFunctionDefinition`，
    scope/autouse 在内部 marker 上（`_fixture_function_marker`），故按 marker 断言。
    """
    fx = guard._guard_derived_assets
    marker = getattr(fx, "_fixture_function_marker", None)
    assert marker is not None, f"守卫不是 pytest fixture：{type(fx)}"
    assert marker.scope == "session", f"守卫 scope 必须是 session，实际 {marker.scope}"
    assert marker.autouse is True, "守卫必须 autouse（否则没人用就守不住）"
