"""离线包下载守卫（agent/subagent/offline_pack.py + routes_subagent 的 offline_pack 路由）

【为什么有这份守卫（不这样会怎样）】
    离线包是"可带走"的交付物，但它由**文件路径拼接**得到 —— 最容易出的两类事故：
      ① 路径穿越：名字里带 `../` 或分隔符 ⇒ 读走配置目录之外的文件；
      ② 假装有包：目录没配 / 包没构建却回 200 空文件，使用者带着空包上路。
    本守卫把两条都钉死：
      P1 纯定位：未配置 / 非法名 / 未构建 ⇒ 各自稳定 error_code，且绝不越出配置目录；
      P2 路由：404 + 对应 error_code；真存在时 200 + **字节一致** + attachment 文件名；
      P3 读面投影：pack_status 只回状态不回绝对路径。

不 import app_server；用最小 Flask app + 替身（本仓既有教训："手搓 Flask 全绿、线上 404"）。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from agent.server_routes import routes_subagent as routes_module
from agent.server_routes.routes_subagent import register_routes
from agent.subagent.delegation_history import DelegationHistory
from agent.subagent.offline_pack import (ENV_OFFLINE_PACK_DIR, OfflinePackError,
                                         offline_pack_dir, pack_status, resolve_pack,
                                         safe_pack_name)


def _make_client(monkeypatch, tmp_path):
    monkeypatch.setattr(routes_module, "delegation_history",
                        DelegationHistory(path=str(tmp_path / "d.jsonl")))
    tm = __import__("agent.subagent.task_board", fromlist=["task_board"])
    monkeypatch.setattr(tm, "task_board",
                        tm.TaskBoard(path=str(tmp_path / "b.jsonl")))
    monkeypatch.setattr(routes_module, "task_board", tm.task_board)
    app = Flask(__name__)
    app.config.update(TESTING=True)
    register_routes(app, SimpleNamespace(Yunshu=None))
    return app.test_client(), app


class TestP1Resolve:
    def test_未配置即not_configured(self):
        with pytest.raises(OfflinePackError) as ei:
            resolve_pack("sa-1", environ={})
        assert ei.value.code == "E_OFFLINE_PACK_NOT_CONFIGURED"

    @pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\\b", "a..b", "a\x00b",
                                     "../etc/passwd"])
    def test_非法名拒绝(self, bad, tmp_path):
        assert safe_pack_name(bad) == ""
        with pytest.raises(OfflinePackError) as ei:
            resolve_pack(bad, pack_dir=str(tmp_path))
        assert ei.value.code == "E_OFFLINE_PACK_BAD_NAME"

    def test_未构建即not_built(self, tmp_path):
        with pytest.raises(OfflinePackError) as ei:
            resolve_pack("sa-1", pack_dir=str(tmp_path))
        assert ei.value.code == "E_OFFLINE_PACK_NOT_BUILT"

    def test_存在即返回配置目录内路径(self, tmp_path):
        target = tmp_path / "sa-1.tar.gz"
        target.write_bytes(b"pack")
        got = resolve_pack("sa-1", pack_dir=str(tmp_path))
        assert Path(got).resolve() == target.resolve()
        assert Path(got).parent.resolve() == tmp_path.resolve()

    def test_目录读取来自env(self):
        assert offline_pack_dir({ENV_OFFLINE_PACK_DIR: "  /x  "}) == "/x"
        assert offline_pack_dir({}) == ""


class TestP3Status:
    def test_不可用时只回状态码不回路径(self, tmp_path):
        st = pack_status("sa-1", pack_dir=str(tmp_path))
        assert st["available"] is False
        assert st["code"] == "E_OFFLINE_PACK_NOT_BUILT"
        assert str(tmp_path) not in st["reason"], "读面不得回显绝对路径"

    def test_可用时中性状态(self, tmp_path):
        (tmp_path / "sa-1.tar.gz").write_bytes(b"x")
        assert pack_status("sa-1", pack_dir=str(tmp_path)) == {
            "available": True, "code": "", "reason": ""}


class TestP2Route:
    def test_未配置_404_not_configured(self, monkeypatch, tmp_path):
        monkeypatch.delenv(ENV_OFFLINE_PACK_DIR, raising=False)
        client, _ = _make_client(monkeypatch, tmp_path)
        resp = client.get("/api/subagent/sa-1/offline_pack")
        assert resp.status_code == 404
        assert resp.get_json()["error_code"] == "E_OFFLINE_PACK_NOT_CONFIGURED"

    def test_未构建_404_not_built(self, monkeypatch, tmp_path):
        monkeypatch.setenv(ENV_OFFLINE_PACK_DIR, str(tmp_path / "packs"))
        client, _ = _make_client(monkeypatch, tmp_path)
        resp = client.get("/api/subagent/sa-1/offline_pack")
        assert resp.status_code == 404
        assert resp.get_json()["error_code"] == "E_OFFLINE_PACK_NOT_BUILT"

    def test_存在_200_字节一致且attachment(self, monkeypatch, tmp_path):
        packs = tmp_path / "packs"
        packs.mkdir()
        (packs / "sa-1.tar.gz").write_bytes(b"PACKBYTES\x00\x01")
        monkeypatch.setenv(ENV_OFFLINE_PACK_DIR, str(packs))
        client, _ = _make_client(monkeypatch, tmp_path)
        resp = client.get("/api/subagent/sa-1/offline_pack")
        assert resp.status_code == 200
        assert resp.data == b"PACKBYTES\x00\x01"
        cd = resp.headers.get("Content-Disposition", "")
        assert "attachment" in cd and "sa-1-offline-pack.tar.gz" in cd

    def test_绝不回落到别的文件(self, monkeypatch, tmp_path):
        packs = tmp_path / "packs"
        packs.mkdir()
        (packs / "other.tar.gz").write_bytes(b"OTHER")
        monkeypatch.setenv(ENV_OFFLINE_PACK_DIR, str(packs))
        client, _ = _make_client(monkeypatch, tmp_path)
        resp = client.get("/api/subagent/sa-1/offline_pack")
        assert resp.status_code == 404
        assert resp.get_data() != b"OTHER"

    def test_路由已注册(self, monkeypatch, tmp_path):
        _, app = _make_client(monkeypatch, tmp_path)
        rules = {str(r.rule) for r in app.url_map.iter_rules()}
        assert "/api/subagent/<name>/offline_pack" in rules

