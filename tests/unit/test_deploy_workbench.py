# -*- coding: utf-8 -*-
"""scripts/deploy_workbench.py 的纯函数单测（不触发真实部署/重启）。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.deploy_workbench import (
    fetch_version,
    frontend_needs_rebuild,
    listening_pids,
    npm_command,
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
        src = tmp_path / "src"  # 不存在 ⇒ 没有「源码比产物新」的信号
        assert frontend_needs_rebuild(["agent/foo.py", "tests/unit/x.py"], html, src) is False

    def test_stale_index_triggers_rebuild(self, tmp_path):
        """上次构建失败/中断：产物比源码旧 ⇒ 即使本次没动前端也必须重建。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.tsx").write_text("x", encoding="utf-8")
        os.utime(src / "a.tsx", (1_700_000_000, 1_700_000_000))
        html = tmp_path / "yunshu.html"
        html.write_text("x", encoding="utf-8")
        os.utime(html, (1_600_000_000, 1_600_000_000))
        assert frontend_needs_rebuild([], html, src) is True
        os.utime(html, (1_800_000_000, 1_800_000_000))
        assert frontend_needs_rebuild([], html, src) is False


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


class TestNpmCommand:
    def test_returns_runnable_argv(self):
        cmd = npm_command("npm", ["run", "build:flask"])
        assert isinstance(cmd, list) and all(isinstance(x, str) for x in cmd)
        assert cmd[-2:] == ["run", "build:flask"]

    def test_windows_prefers_cmd_over_extensionless_shim(self, monkeypatch):
        """shutil.which("npm") 先命中无扩展名 shim 时，必须改选 npm.cmd（否则 WinError 193）。"""
        from scripts import deploy_workbench as dw

        def fake_which(name):
            low = str(name).lower()
            if low.endswith("npm.cmd"):
                return r"C:\nodejs\npm.cmd"
            if low.endswith("npm"):
                return r"C:\nodejs\npm"
            return None

        monkeypatch.setattr(dw.shutil, "which", fake_which)
        cmd = dw.npm_command("npm", ["run", "build:flask"])
        if dw.os.name == "nt":
            assert cmd[:3] == ["cmd", "/c", r"C:\nodejs\npm.cmd"], cmd
        else:
            assert cmd[0] == r"C:\nodejs\npm", cmd
