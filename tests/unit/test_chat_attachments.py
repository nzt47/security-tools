# -*- coding: utf-8 -*-
"""E3-3 聊天附件上传（2026-11-27）

为什么：聊天里贴一张截图/一份 PDF，之前只能靠用户把内容粘进来。附件接口只负责把它
**稳定落盘并回可读路径**，前端在消息里写一行 manifest，模型按需 read_file。

本文件钉住安全边界（顺序与实现一致）：文件名清洗、常量落点、写围栏、体积上限、可执行后缀拒收，
以及图片附件的 OCR 旁路（不可用要如实说不可用，不能假装识别成功）。
"""
from __future__ import annotations

import io
import os
import sys
import types

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """装配桩 app_server + 注册 plugins.chat 蓝图；附件根落到 tmp"""
    fake = types.ModuleType("app_server")
    fake.require_token = lambda f: f
    fake.log_request = lambda *a, **k: (lambda f: f)
    fake._get_current_session_id = lambda: "sess-attach"
    fake.logger = types.SimpleNamespace(info=lambda *a, **k: None,
                                       warning=lambda *a, **k: None,
                                       error=lambda *a, **k: None,
                                       debug=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "app_server", fake)

    import plugins.chat as chat_mod
    from flask import Flask

    root = tmp_path / "uploads"
    monkeypatch.setattr(chat_mod, "_attachments_root", lambda: str(root))
    app = Flask(__name__)
    app.register_blueprint(chat_mod.bp)
    app.config.update(TESTING=True)
    return app.test_client(), chat_mod, root


def _upload(client, name="report.txt", data=b"hello", session="s1"):
    return client.post(
        "/api/chat/attachments",
        data={"session_id": session, "file": (io.BytesIO(data), name)},
        content_type="multipart/form-data",
    )


class TestUpload:
    def test_文本附件落盘并回路径(self, client):
        cli, _mod, root = client
        resp = _upload(cli, "report.txt", b"hello world")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["code"] == 200
        data = body["data"]
        assert data["ok"] is True and data["name"] == "report.txt"
        assert data["bytes"] == 11 and data["kind"] == "text"
        # 落点必须在附件根内，且文件名前缀了随机 id
        files = list((root / "s1").iterdir())
        assert len(files) == 1 and files[0].read_bytes() == b"hello world"
        assert files[0].name.endswith("__report.txt")
        # 回给模型的是可读路径：生产在 data/uploads 下（仓库相对）；测试把根换到 tmp，
        # 跨盘时路由会回**绝对路径**（relpath 跨盘会抛 ValueError）——两种都要求指向同一个文件
        assert data["path"].endswith(files[0].name)

    def test_文件名清洗挡住路径穿越(self, client):
        cli, _mod, root = client
        resp = _upload(cli, "../../evil.sh", b"x")
        assert resp.status_code == 200
        stored = list((root / "s1").iterdir())
        assert len(stored) == 1
        assert stored[0].parent == root / "s1", "必须落在会话目录内，不许上跳"
        assert ".." not in stored[0].name

    def test_可执行后缀拒收(self, client):
        cli, _mod, root = client
        resp = _upload(cli, "setup.exe", b"MZ")
        assert resp.status_code == 403
        assert resp.get_json()["type"].endswith("/blocked_extension")
        assert not (root / "s1").exists() or not list((root / "s1").iterdir())

    def test_空文件拒收(self, client):
        cli, _mod, root = client
        resp = _upload(cli, "empty.txt", b"")
        assert resp.status_code == 400
        assert not (root / "s1").exists() or not list((root / "s1").iterdir())

    def test_缺文件字段拒收(self, client):
        cli, _mod, _root = client
        resp = cli.post("/api/chat/attachments", data={"session_id": "s1"},
                        content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_超限即删半成品(self, client, monkeypatch):
        cli, mod, root = client
        monkeypatch.setattr(mod, "ATTACHMENT_MAX_BYTES", 1024)
        resp = _upload(cli, "big.bin", b"x" * 4096)
        assert resp.status_code == 413
        assert not (root / "s1").exists() or not list((root / "s1").iterdir())

    def test_写围栏拒绝时拒收(self, client, monkeypatch):
        cli, _mod, root = client
        import agent.sandbox.fs_fence as fence
        monkeypatch.setattr(fence, "check_write",
                            lambda path, tool: {"ok": False, "blocked": True,
                                                "sandbox": "workspace-write",
                                                "error": "[sandbox: file access denied]"})
        resp = _upload(cli, "a.txt", b"x")
        assert resp.status_code == 403
        assert resp.get_json()["type"].endswith("/sandbox_denied")

    def test_图片走OCR旁路且不可用要如实说(self, client, monkeypatch):
        cli, _mod, _root = client
        import agent.context.image_offload as img
        monkeypatch.setattr(img, "ocr_text",
                            lambda path, ext=None: {"available": False, "note": "OCR 不可用"})
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        resp = _upload(cli, "shot.png", png)
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["kind"] == "image"
        assert data["ocr"]["available"] is False
        assert data["ocr"]["note"]

    def test_会话名清洗后仍落在附件根内(self, client):
        cli, _mod, root = client
        resp = _upload(cli, "a.txt", b"x", session="../../etc")
        assert resp.status_code == 200
        subdirs = [p for p in root.iterdir() if p.is_dir()]
        assert len(subdirs) == 1
        assert ".." not in subdirs[0].name
        assert subdirs[0].parent == root

