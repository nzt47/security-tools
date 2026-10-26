# -*- coding: utf-8 -*-
"""agent/build_info.py 单测（部署漂移可视化的数据面）。"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import agent.build_info as bi


@pytest.fixture(autouse=True)
def _clear_cache():
    bi._BACKEND_CACHE = None
    yield
    bi._BACKEND_CACHE = None


class TestPorcelainPath:
    """`git status --porcelain` 行的路径解析（2026-10-26 线上首字符丢失的回归点）。

    porcelain 是 `XY<space>PATH`，路径从下标 3 开始；但 `_run()` 对整体 stdout 做了
    `.strip()`，首行的前导空格会被削掉。两种形态都必须取到完整路径。
    """

    @pytest.mark.parametrize("line,expected", [
        (" M data/x.json", "data/x.json"),   # 未 strip 的正常形态
        ("M data/x.json", "data/x.json"),    # 被 _run().strip() 削掉前导空格后的首行
        ("?? .ciart/", ".ciart/"),
        ("?? data/_shots/", "data/_shots/"),
        ("R  old.py -> new.py", "old.py -> new.py"),
        ("", ""),
    ])
    def test_path_parsing(self, line, expected):
        assert bi._porcelain_path(line) == expected


class TestBackendInfo:
    def test_real_git_repo_first_dirty_path_keeps_first_char(self, tmp_path):
        """真实 git 链路：首行 " M data/x.json" 经 _run().strip() 后仍须是 data/x.json。

        修复前 `ln[3:]` 会得到 "ata/x.json"（线上 /api/version 的实际症状）。
        """
        git = shutil.which("git")
        if not git:
            pytest.skip("git 不在 PATH")
        (tmp_path / "data").mkdir()
        target = tmp_path / "data" / "x.json"
        target.write_text("{}", encoding="utf-8")

        def git_run(*args):
            subprocess.run([git, *args], cwd=str(tmp_path), check=True,
                           capture_output=True)

        git_run("init", "-q")
        git_run("config", "user.email", "test@example.com")
        git_run("config", "user.name", "test")
        git_run("add", ".")
        git_run("commit", "-q", "-m", "init")
        target.write_text('{"a": 1}', encoding="utf-8")

        info = bi.backend_info(tmp_path, refresh=True)
        assert info["dirty"] is True
        assert info["dirty_files"] == ["data/x.json"]

    def test_parses_git_outputs(self, monkeypatch):
        def fake_run(cmd, cwd, timeout=10.0):
            if "rev-parse" in cmd:
                return "a" * 40, ""
            if "--format=%s" in cmd:
                return "feat: x", ""
            if "--format=%cI" in cmd:
                return "2026-10-16T08:00:00+08:00", ""
            if "status" in cmd:
                return " M data/x.json", ""
            return "", ""

        monkeypatch.setattr(bi, "_run", fake_run)
        info = bi.backend_info(Path("."))
        assert info["commit"] == "a" * 40
        assert info["commit_subject"] == "feat: x"
        assert info["committed_at"] == "2026-10-16T08:00:00+08:00"
        assert info["dirty"] is True
        assert info["dirty_files"] == ["data/x.json"]

    def test_missing_git_reports_error_not_raise(self, monkeypatch):
        monkeypatch.setattr(bi, "_run", lambda *a, **k: (None, "not a git repo"))
        info = bi.backend_info(Path("."))
        assert info["commit"] is None
        assert info["error"]

    def test_cache_returns_loaded_commit(self, monkeypatch):
        calls = {"n": 0}

        def fake_run(cmd, cwd, timeout=10.0):
            calls["n"] += 1
            return ("b" * 40, "") if "rev-parse" in cmd else ("", "")

        monkeypatch.setattr(bi, "_run", fake_run)
        first = bi.backend_info(Path("."))
        n_after_first = calls["n"]
        second = bi.backend_info(Path("."))
        assert second["commit"] == first["commit"]
        assert calls["n"] == n_after_first, "缓存命中后不应再调 git"


class TestFrontendInfo:
    def test_parses_main_bundle(self, tmp_path):
        html = tmp_path / "templates" / "yunshu.html"
        html.parent.mkdir(parents=True)
        html.write_text(
            '<script type="module" crossorigin src="/static/assets/index-BjKvrIBE.js"></script>',
            encoding="utf-8",
        )
        info = bi.frontend_info(tmp_path)
        assert info["index_html_present"] is True
        assert info["bundle"] == "index-BjKvrIBE.js"
        assert info["built_at"]

    def test_missing_html_is_reported(self, tmp_path):
        info = bi.frontend_info(tmp_path)
        assert info["index_html_present"] is False
        assert info["bundle"] is None
        assert info["error"] is None


class TestCollect:
    def test_shape_and_process_pid(self, tmp_path):
        data = bi.collect(tmp_path)
        assert set(data) == {"backend", "frontend", "process"}
        assert data["process"]["pid"] == os.getpid()
        assert "python" in data["process"]
        assert "started_at" in data["process"]

    def test_real_repo_does_not_raise(self):
        data = bi.collect(bi.REPO_ROOT)
        commit = data["backend"]["commit"]
        assert commit is None or (isinstance(commit, str) and len(commit) >= 7)


class TestFrontendBuildStamp:
    def test_stamp_supplies_build_commit_and_time(self, tmp_path):
        html = tmp_path / "templates" / "yunshu.html"
        html.parent.mkdir(parents=True)
        html.write_text('x', encoding="utf-8")
        stamp = tmp_path / "static" / "assets" / ".build-stamp.json"
        stamp.parent.mkdir(parents=True)
        stamp.write_text(
            '{"commit": "' + "a" * 40 + '", "built_at": "2026-10-16T12:00:00+08:00"}',
            encoding="utf-8",
        )
        info = bi.frontend_info(tmp_path)
        assert info["build_commit"] == "a" * 40
        assert info["built_at"] == "2026-10-16T12:00:00+08:00"
        assert info["built_at_source"] == "build-stamp"

    def test_no_stamp_falls_back_to_mtime(self, tmp_path):
        html = tmp_path / "templates" / "yunshu.html"
        html.parent.mkdir(parents=True)
        html.write_text("x", encoding="utf-8")
        info = bi.frontend_info(tmp_path)
        assert info["build_commit"] is None
        assert info["built_at"]
        assert info["built_at_source"] == "mtime"
