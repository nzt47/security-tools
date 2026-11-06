# -*- coding: utf-8 -*-
"""交接卡的四项自检必须真的取到数（2026-11-07：卡片上写着「自检：（未跑）」）。

【根因】`_self_check` 用 `subprocess.run(capture_output=True, text=True, encoding="utf-8")`
跑子脚本。子进程 stdout 是**管道**，Windows 上 Python 据此退回本地码页（简中 cp936）
输出，父进程按 utf-8 解码 ⇒ 中文变替换符，四条正则全都匹配不上 ⇒ 卡片写「（未跑）」。
但四个脚本本身都 exit 0（所以 `bad` 列表也是空的，看起来"没跑"而不是"跑挂了"）。

本文件把两端编码的契约钉住：子进程必须拿到 `PYTHONIOENCODING=utf-8`，中文行必须能解析。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _mod():
    spec = importlib.util.spec_from_file_location(
        "handoff_snapshot_mod", _ROOT / "scripts" / "handoff_snapshot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestChildEncoding:
    def test_subprocess_gets_utf8_io_encoding(self, monkeypatch):
        mod = _mod()
        seen = {}

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, **kwargs):
            seen.update(kwargs)
            return _Proc()

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        mod._self_check("dual_track", "scripts/check_skills_dual_track.py")
        assert seen["encoding"] == "utf-8", "父进程按 utf-8 解码"
        assert (seen.get("env") or {}).get("PYTHONIOENCODING") == "utf-8", (
            "子进程必须被要求按 utf-8 输出，否则 Windows 管道走 cp936")

    def test_chinese_report_is_parsed(self, monkeypatch, tmp_path):
        """真实子进程：中文行能被四条正则解析（等价于卡片那四个数字）。"""
        mod = _mod()
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "fake_dual.py").write_text(
            "print('文件轨 47 条 / 主轨 49 条')\n"
            "print('[OK] 无孤儿：每条 skill.md 都能在主轨找到登记')\n",
            encoding="utf-8")
        monkeypatch.setattr(mod, "ROOT", tmp_path)
        data = mod._self_check("dual_track", "scripts/fake_dual.py")
        assert data["ok"] is True
        assert data["file_track"] == 47
        assert data["main_track"] == 49
        assert data["orphans_ok"] is True

    def test_audit_chain_breaks_parsed(self, monkeypatch, tmp_path):
        mod = _mod()
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "fake_audit.py").write_text(
            "print('[链式校验] OK — 链完整，已校验 82670 条（seq 1..82670）')\n",
            encoding="utf-8")
        monkeypatch.setattr(mod, "ROOT", tmp_path)
        data = mod._self_check("audit_chain", "scripts/fake_audit.py")
        assert data["rows"] == 82670
        assert data["breaks"] == 0, "「链完整」必须算 0 断点（-1 = 没解析到）"

    def test_render_selfcheck_shows_numbers_not_placeholder(self):
        mod = _mod()
        line = mod._render_selfcheck({
            "dual_track": {"ok": True, "file_track": 47, "main_track": 49,
                           "orphans_ok": True},
            "skill_md": {"ok": True, "files": 47, "errors": 0, "warnings": 1},
        })
        assert "47/49" in line
        assert "（未跑）" not in line
