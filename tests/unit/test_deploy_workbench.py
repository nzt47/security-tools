# -*- coding: utf-8 -*-
"""scripts/deploy_workbench.py 的纯函数单测（不触发真实部署/重启）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# 【为什么要自己补 sys.path】本文件 import 的是 `scripts.deploy_workbench`（仓根包），
# 而 pytest.ini 用 `--import-mode=importlib` + `pythonpath = tests/unit`：仓根进 sys.path
# 靠的是 tests/conftest.py 执行 `sys.path.insert(0, PROJECT_ROOT)`。一旦同 shard 里有
# 别的用例改过 sys.path / cwd，本模块在**收集期**就 `ModuleNotFoundError: No module named
# 'scripts'`（2026-11-07 PR #1475 的 Shard 6 实测两次，重跑同错；分片成员一变就可能复发）。
# 故与 tests/boundary/* 同款：导入被测模块前显式补仓根，不依赖 import 顺序。
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

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

class TestColdStartRetry:
    """冒烟/版本探测的冷启动容忍（2026-11-08）：判据仍是 200，只是不再一次探测就红。"""

    def test_无响应或5xx才重试_4xx立即返回(self, monkeypatch):
        from scripts import deploy_workbench as dw

        seq = [None, 503, 404]
        calls = []

        def fake_status(url, token=None, timeout=5.0):
            calls.append(url)
            return seq[min(len(calls) - 1, len(seq) - 1)]

        monkeypatch.setattr(dw, "http_status", fake_status)
        slept = []
        code = dw.http_status_retry("http://x/api/version", window=60.0,
                                    interval=0.5, sleep=slept.append)
        assert code == 404, "4xx 必须原样返回（401/404 重试没有意义，只会让部署变慢）"
        assert len(calls) == 3, calls
        assert slept == [0.5, 0.5], slept

    def test_健康时零等待(self, monkeypatch):
        from scripts import deploy_workbench as dw
        monkeypatch.setattr(dw, "http_status", lambda *a, **kw: 200)
        slept = []
        assert dw.http_status_retry("http://x", window=60.0, sleep=slept.append) == 200
        assert slept == [], "第一次就 200 时不许 sleep（部署不能被这个改动拖慢）"

    def test_窗口用完仍然无响应就如实返回_None(self, monkeypatch):
        from scripts import deploy_workbench as dw
        monkeypatch.setattr(dw, "http_status", lambda *a, **kw: None)
        # window=0 ⇒ 一次探测，不睡（既有单测与"--check"都依赖这个语义）
        slept = []
        assert dw.http_status_retry("http://x", window=0.0, sleep=slept.append) is None
        assert slept == []

    def test_run_smoke_走重试版探测(self, monkeypatch):
        from scripts import deploy_workbench as dw
        seen = []

        def fake_retry(url, token=None, timeout=5.0, window=dw.SMOKE_RETRY_WINDOW_SEC, sleep=None):
            seen.append((url, window))
            return 200

        monkeypatch.setattr(dw, "http_status_retry", fake_retry)
        res = dw.run_smoke(port=59998, token="t", paths=("/api/version",))
        assert res == [{"path": "/api/version", "status": 200, "ok": True}]
        assert seen and seen[0][1] == dw.SMOKE_RETRY_WINDOW_SEC, "smoke 必须带冷启动窗口"

    def test_fetch_version_窗口默认关闭_保持老语义(self):
        from scripts import deploy_workbench as dw
        # 不可达端口：window=0 ⇒ 立刻返回 None（与既有用例同一判据）
        assert dw.fetch_version(59998, None, timeout=0.2, window=0.0) is None
