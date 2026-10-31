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
    read_json,
    run_smoke,
    stamp_matches,
    write_build_stamp,
    write_json_atomic,
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


class TestJsonAtomic:
    def test_roundtrip_and_no_tmp_left(self, tmp_path):
        path = tmp_path / "sub" / "deploy_last.json"
        assert write_json_atomic(path, {"a": 1}) is True
        assert read_json(path) == {"a": 1}
        assert list(path.parent.glob("*.tmp")) == []

    def test_missing_or_garbage_is_none(self, tmp_path):
        assert read_json(tmp_path / "nope.json") is None
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert read_json(bad) is None


class TestBuildStamp:
    def test_write_then_match(self, tmp_path):
        stamp = tmp_path / ".build-stamp.json"
        assert write_build_stamp(stamp, "a" * 40, built_at="2026-10-16T12:00:00+08:00") is True
        data = read_json(stamp)
        assert data["commit"] == "a" * 40
        assert data["built_at"] == "2026-10-16T12:00:00+08:00"
        assert stamp_matches(data, "a" * 40) is True

    def test_prefix_and_mismatch(self):
        assert stamp_matches({"commit": "a" * 40}, "a" * 12) is True
        assert stamp_matches({"commit": "a" * 12}, "a" * 40) is True
        assert stamp_matches({"commit": "a" * 40}, "b" * 40) is False
        assert stamp_matches(None, "a" * 40) is False
        # 过短的 sha 不参与前缀互认，避免误判
        assert stamp_matches({"commit": "abcd"}, "a" * 40) is False


class TestFrontendNeedsRebuildWithStamp:
    def _html(self, tmp_path):
        html = tmp_path / "yunshu.html"
        html.write_text("x", encoding="utf-8")
        return html

    def test_missing_stamp_needs_build(self, tmp_path):
        assert frontend_needs_rebuild(
            [], self._html(tmp_path), tmp_path / "no-src",
            stamp_path=tmp_path / "no-stamp.json", expected_commit="a" * 40) is True

    def test_matching_stamp_skips_build(self, tmp_path):
        stamp = tmp_path / "stamp.json"
        write_build_stamp(stamp, "a" * 40)
        assert frontend_needs_rebuild(
            [], self._html(tmp_path), tmp_path / "no-src",
            stamp_path=stamp, expected_commit="a" * 40) is False

    def test_mismatched_stamp_needs_build(self, tmp_path):
        stamp = tmp_path / "stamp.json"
        write_build_stamp(stamp, "a" * 40)
        assert frontend_needs_rebuild(
            [], self._html(tmp_path), tmp_path / "no-src",
            stamp_path=stamp, expected_commit="b" * 40) is True

    def test_source_mtime_still_triggers_without_stamp_signal(self, tmp_path):
        """有匹配构建戳，但源码 mtime 比产物新（未走本脚本的构建）仍要重建。"""
        import os
        stamp = tmp_path / "stamp.json"
        write_build_stamp(stamp, "a" * 40)
        html = self._html(tmp_path)
        os.utime(html, (1_600_000_000, 1_600_000_000))
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.tsx").write_text("x", encoding="utf-8")
        os.utime(src / "a.tsx", (1_700_000_000, 1_700_000_000))
        assert frontend_needs_rebuild(
            [], html, src, stamp_path=stamp, expected_commit="a" * 40) is True


class TestSmoke:
    def test_unreachable_reports_no_response(self):
        results = run_smoke(59999, None, ["/api/health"], timeout=0.5)
        assert results == [{"path": "/api/health", "status": None, "ok": False}]

class TestRestartBackendNoDoubleStart:
    """2026-10-31 实测：看护恰好在 ready_timeout 差 1 秒时拉起 ⇒ 部署若盲目 spawn 会双主。

    判据：回退前再看一次端口；端口已被占用就**不许** spawn，而是等它真正就绪。
    """

    def _patch(self, monkeypatch, pids, waits, events):
        from scripts import deploy_workbench as dw

        monkeypatch.setattr(dw, "kill_pid", lambda pid: events.append(("kill", pid)))
        monkeypatch.setattr(
            dw, "spawn_backend", lambda root, py: events.append(("spawn", None)) or 4242)
        monkeypatch.setattr(dw, "watchdog_pid", lambda: 999)
        monkeypatch.setattr(dw, "listening_pids", lambda port: next(pids))
        monkeypatch.setattr(dw, "wait_health", lambda *a, **k: next(waits))
        return dw

    def test_recheck_adopts_listener_instead_of_spawning(self, monkeypatch):
        events = []
        dw = self._patch(
            monkeypatch,
            iter([[111], [222]]),
            iter([(False, 180.0), (True, 12.5)]),
            events,
        )

        mode, waited = dw.restart_backend(dw.REPO_ROOT, "python", 5678, 180.0)

        assert mode == "watchdog"
        assert ("spawn", None) not in events, "端口已有人监听时不许再起第二个后端"
        assert ("kill", 111) in events, "旧监听者仍要被停掉"
        assert waited == 192.5

    def test_recheck_empty_still_falls_back_to_direct_start(self, monkeypatch):
        events = []
        dw = self._patch(
            monkeypatch,
            iter([[111], []]),
            iter([(False, 180.0), (True, 3.0)]),
            events,
        )

        mode, waited = dw.restart_backend(dw.REPO_ROOT, "python", 5678, 180.0)

        assert mode == "direct"
        assert ("spawn", None) in events
        # 直接启动路径的 waited 只记「spawn 之后」的就绪耗时（既有口径）
        assert waited == 3.0

