# -*- coding: utf-8 -*-
"""派生面指纹守卫的判据（tests/unit/_derived_guard.py + conftest 的 session fixture）。

背景（为什么要有这个守卫）：2026-11-10 00:00 一次全量 `pytest tests/unit -p no:randomly`
期间，`data/skills_repo/gh-cli`（随后 `gstack`）的 skill.md/CARD.md 被删、
`data/skills_mgmt.json` 少行（线上技能 53→52）、能力清单被重派生、3 个基线测试被改；
当时只能用"全量跑 20 分钟 + 事后 git status"发现。审计链后来给出证据：两次都是
`DELETE /api/skills-mgmt/<id>`（带 bearer token）——**HTTP 调用**，不是测试直接删文件。

本文件测**判据本身**（纯函数 + 目标集 + 关闸），不碰真实 data/、也**不二次加载 conftest**
（那会把 conftest 的导入期副作用跑第二遍，实测会打红 `test_network_config_test_floor`）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _guard():
    """按路径加载判据模块（`tests/unit` 虽在 pythonpath，但显式加载更稳）"""
    path = _ROOT / "tests" / "unit" / "_derived_guard.py"
    spec = importlib.util.spec_from_file_location("_derived_guard_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def guard():
    return _guard()


def test_targets_cover_derived_assets_and_sentinels(guard):
    targets = set(guard.TARGETS)
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

    before = guard.snapshot(tmp_path)
    assert set(before) == {"data/skills_repo/s1/skill.md",
                           "data/skills_repo/s1/CARD.md",
                           "data/skills_mgmt.json"}

    f_del.unlink()
    f_mod.write_text('{"a": 2}', encoding="utf-8")
    (tmp_path / "data" / "skills_repo" / "s2").mkdir()
    (tmp_path / "data" / "skills_repo" / "s2" / "skill.md").write_text("new", encoding="utf-8")

    d = guard.diff(before, guard.snapshot(tmp_path))
    assert d["deleted"] == ["data/skills_repo/s1/CARD.md"]
    assert d["modified"] == ["data/skills_mgmt.json"]
    assert d["added"] == ["data/skills_repo/s2/skill.md"]


def test_internal_dot_dirs_are_ignored(guard, tmp_path):
    """`.index` / `.migration` / `.vector_index` 是服务合法重建的内部库，不算污染。"""
    skill = tmp_path / "data" / "skills_repo" / "s1"
    skill.mkdir(parents=True)
    (skill / "skill.md").write_text("body", encoding="utf-8")
    for d in (".index", ".migration", ".vector_index"):
        sub = tmp_path / "data" / "skills_repo" / d
        sub.mkdir()
        (sub / "cache.json").write_text("{}", encoding="utf-8")

    snap = guard.snapshot(tmp_path)
    assert list(snap) == ["data/skills_repo/s1/skill.md"]


def test_added_split_runtime_file_is_soft_skill_md_is_hard(guard, tmp_path):
    """干净检出里 `data/skills_mgmt.json` 本来不存在（构造服务就会创建）⇒ 只告警；
    `data/skills_repo/` 下新增 .md = 凭空多一条技能 ⇒ 硬失败。"""
    (tmp_path / "data").mkdir()
    before = guard.snapshot(tmp_path)
    (tmp_path / "data" / "skills_mgmt.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data" / "skills_repo" / "s-new").mkdir(parents=True)
    (tmp_path / "data" / "skills_repo" / "s-new" / "skill.md").write_text("x", encoding="utf-8")

    hard, soft = guard.split_added(guard.diff(before, guard.snapshot(tmp_path)))
    assert hard == ["data/skills_repo/s-new/skill.md"]
    assert soft == ["data/skills_mgmt.json"]


def test_no_change_means_empty_diff(guard, tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "capability_manifest.json").write_text("{}", encoding="utf-8")
    snap = guard.snapshot(tmp_path)
    assert guard.diff(snap, guard.snapshot(tmp_path)) == {
        "deleted": [], "modified": [], "added": []}


def test_kill_switch(guard, monkeypatch):
    monkeypatch.delenv(guard.ENV_DISABLED, raising=False)
    assert guard.disabled() is False
    monkeypatch.setenv(guard.ENV_DISABLED, "0")
    assert guard.disabled() is True
    monkeypatch.setenv(guard.ENV_DISABLED, "OFF")
    assert guard.disabled() is True
    monkeypatch.setenv(guard.ENV_DISABLED, "1")
    assert guard.disabled() is False


def test_fixture_is_session_scoped_autouse():
    """守卫必须是 session 级 autouse（否则起止指纹只覆盖单个用例 = 没守）。

    【为什么用"读源码"而不是 import conftest】再加载一次 conftest 会把它的**导入期副作用**
    跑第二遍（网络配置地板 / .env 地板 / sys.path 钉扎）—— 实测把
    `test_network_config_test_floor` 打红（地板目录落进仓库的 .pytest_tmp）。
    源码断言足以钉住"session + autouse"这两件事。
    """
    src = (_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    idx = src.find("def _guard_derived_assets")
    assert idx > 0, "conftest 里没有 _guard_derived_assets"
    decorator = src[max(0, idx - 200):idx]
    assert "@pytest.fixture(autouse=True, scope=\"session\")" in decorator, (
        "守卫必须是 @pytest.fixture(autouse=True, scope=\"session\")，实际装饰器段："
        + decorator[-120:])
