# -*- coding: utf-8 -*-
"""scripts/deploy_workbench.py 的纯函数单测（不触发真实部署/重启）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.deploy_workbench import (
    fetch_version,
    frontend_needs_rebuild,
    listening_pids,
    overwrites,
    parse_porcelain,
    read_env_token,
)


class TestParsePorcelain:
    def test_modified_untracked_rename_and_quoted(self):
        text = (
            " M data/x.json\n"
            "?? rag/\n"
            "R  old.py -> new.py\n"
            ' M "docs/zh/周报.md"\n'
            "\n"
        )
        assert parse_porcelain(text) == [
            "data/x.json", "rag/", "new.py", "docs/zh/周报.md",
        ]


class TestOverwrites:
    def test_intersection_only(self):
        assert overwrites(["a.py", "b.py"], ["b.py", "c.py"]) == ["b.py"]

    def test_no_overlap(self):
        assert overwrites(["a.py"], ["b.py"]) == []


class TestFrontendNeedsRebuild:
    def test_missing_index_needs_build(self, tmp_path):
        assert frontend_needs_rebuild([], tmp_path / "missing.html") is True

    def test_frontend_source_change_needs_build(self, tmp_path):
        html = tmp_path / "yunshu.html"
        html.write_text("x", encoding="utf-8")
        assert frontend_needs_rebuild(["yunshu-ui/src/a.tsx"], html) is True
        assert frontend_needs_rebuild(["templates/yunshu.html"], html) is True

    def test_unrelated_change_skips_build(self, tmp_path):
        html = tmp_path / "yunshu.html"
        html.write_text("x", encoding="utf-8")
        assert frontend_needs_rebuild(["agent/foo.py", "tests/unit/x.py"], html) is False


class TestReadEnvToken:
    def test_reads_token_and_strips_quotes(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text('FLASK_API_TOKEN="secret-value"\nOTHER=1\n', encoding="utf-8")
        assert read_env_token(tmp_path) == "secret-value"

    def test_missing_env(self, tmp_path):
        assert read_env_token(tmp_path) is None


class TestPortAndVersion:
    def test_listening_pids_returns_list(self):
        # 非 Windows 返回 []；Windows 返回 pid 列表。都不应抛异常。
        assert isinstance(listening_pids(59999), list)

    def test_fetch_version_unreachable_returns_none(self):
        assert fetch_version(59999, None, timeout=0.5) is None
