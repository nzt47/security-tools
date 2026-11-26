# -*- coding: utf-8 -*-
"""E0-1② 压缩/解压的写围栏（2026-11-27）

缺口（来自 data/handoff_next.json::dsh-parity-e0-1-followups）：
  compress/decompress 此前只有 safe_resolve_path + is_protected_path，**没有权限预设的
  写档闸** —— workspace-write 档下把输出指到工作区外照样落盘，而 write_file/edit 在同一档
  下会被拒（同一件事两套边界）。本文件钉住：产物与解压目录都走 fs_fence.check_write。
"""
from __future__ import annotations

import zipfile

import pytest

from agent import compression_tools as ct

DENIED = "[sandbox: file access denied under {} mode]"


@pytest.fixture(autouse=True)
def _clean_preset(monkeypatch):
    """每个用例从「未设置」开始（默认 full-access），避免相互串档"""
    monkeypatch.delenv("CP_PERMISSION_PRESET", raising=False)
    monkeypatch.delenv("CP_PERMISSION_SANDBOX_ROOT", raising=False)
    yield


def _src(tmp_path, name="a.txt"):
    path = tmp_path / name
    path.write_text("hello", encoding="utf-8")
    return path


def _zip(tmp_path, name="a.zip"):
    """造一个最小 zip（用标准库造，避免被测函数自己触发围栏）"""
    arc = tmp_path / name
    with zipfile.ZipFile(arc, "w") as zf:
        zf.writestr("inner.txt", "hi")
    return arc


class TestCompressFence:
    def test_默认全权档放行(self, tmp_path):
        out = tmp_path / "out.zip"
        got = ct.compress(str(_src(tmp_path)), output_path=str(out))
        assert got["ok"] is True and out.exists()

    def test_只读档拒绝且不留产物(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CP_PERMISSION_PRESET", "read-only")
        out = tmp_path / "out.zip"
        got = ct.compress(str(_src(tmp_path)), output_path=str(out))
        assert got["ok"] is False and got["blocked"] is True
        assert got["error"] == DENIED.format("read-only")
        assert not out.exists(), "被拒时不能留下半成品文件"

    def test_只能写工作区档_越界拒绝(self, tmp_path, monkeypatch):
        root = tmp_path / "ws"
        root.mkdir()
        monkeypatch.setenv("CP_PERMISSION_PRESET", "workspace-write")
        monkeypatch.setenv("CP_PERMISSION_SANDBOX_ROOT", str(root))
        src = _src(root)
        out = tmp_path / "outside.zip"          # 工作区外
        got = ct.compress(str(src), output_path=str(out))
        assert got["ok"] is False and got["blocked"] is True
        assert not out.exists()

    def test_只能写工作区档_档内放行(self, tmp_path, monkeypatch):
        root = tmp_path / "ws"
        root.mkdir()
        monkeypatch.setenv("CP_PERMISSION_PRESET", "workspace-write")
        monkeypatch.setenv("CP_PERMISSION_SANDBOX_ROOT", str(root))
        src = _src(root)
        out = root / "out.zip"
        got = ct.compress(str(src), output_path=str(out))
        assert got["ok"] is True and out.exists()

    def test_默认输出路径也过围栏(self, tmp_path, monkeypatch):
        """不传 output_path 时自动生成到源目录旁 —— 同样要过闸，不能绕道"""
        monkeypatch.setenv("CP_PERMISSION_PRESET", "read-only")
        got = ct.compress(str(_src(tmp_path)))
        assert got["ok"] is False and got["blocked"] is True


class TestDecompressFence:
    def test_默认全权档放行(self, tmp_path):
        arc = _zip(tmp_path)
        out = tmp_path / "out"
        got = ct.decompress(str(arc), output_dir=str(out))
        assert got["ok"] is True and (out / "inner.txt").exists()

    def test_只读档拒绝且不建目录(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CP_PERMISSION_PRESET", "read-only")
        arc = _zip(tmp_path)
        out = tmp_path / "out"
        got = ct.decompress(str(arc), output_dir=str(out))
        assert got["ok"] is False and got["blocked"] is True
        assert not out.exists(), "被拒时不能先建目录再失败"

    def test_只能写工作区档_越界拒绝(self, tmp_path, monkeypatch):
        root = tmp_path / "ws"
        root.mkdir()
        monkeypatch.setenv("CP_PERMISSION_PRESET", "workspace-write")
        monkeypatch.setenv("CP_PERMISSION_SANDBOX_ROOT", str(root))
        arc = _zip(root)
        out = tmp_path / "outside"              # 工作区外
        got = ct.decompress(str(arc), output_dir=str(out))
        assert got["ok"] is False and got["blocked"] is True
        assert not out.exists()

    def test_只能写工作区档_档内放行(self, tmp_path, monkeypatch):
        root = tmp_path / "ws"
        root.mkdir()
        monkeypatch.setenv("CP_PERMISSION_PRESET", "workspace-write")
        monkeypatch.setenv("CP_PERMISSION_SANDBOX_ROOT", str(root))
        arc = _zip(root)
        out = root / "out"
        got = ct.decompress(str(arc), output_dir=str(out))
        assert got["ok"] is True and (out / "inner.txt").exists()

