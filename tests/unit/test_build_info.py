# -*- coding: utf-8 -*-
"""agent/build_info.py 单测（部署漂移可视化的数据面）。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import agent.build_info as bi


@pytest.fixture(autouse=True)
def _clear_cache():
    bi._BACKEND_CACHE = None
    yield
    bi._BACKEND_CACHE = None


class TestBackendInfo:
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
