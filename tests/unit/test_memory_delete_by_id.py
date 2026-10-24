# -*- coding: utf-8 -*-
"""长期记忆按 id 删除（交接单 §9 T3）：存储层 / 管理者层 / HTTP 层。

背景：交接单里 T3 的现状是「rollback 对 memory_note 只回 manual」——根因是
`POST /api/memory/manual` **不回 msg_id**，而 `/api/memory/<int:index>` 是个
恒返回 ok:true 却什么都不删的占位实现（"假成功"）。本组用例钉住修复后的契约：

1. 按 id 物理删只删中目标行，未命中的行（含历史坏行）原样保留；
2. 删文件的同时必须清掉内存滑动窗口的副本（否则删了还在上下文里）；
3. HTTP 层默认 dry-run，真删必须 confirm=true；找不到/形态非法一律如实报错；
4. 按序号删除不再假装成功（501 + 指明正确入口）。
"""
from __future__ import annotations

import importlib
import json
import logging
import sys
import types
from pathlib import Path

import pytest

from memory.storage import Storage

ROOT = Path(__file__).resolve().parents[2]


def _append(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ════════════════════════════════════════════════════════════
#  存储层
# ════════════════════════════════════════════════════════════


def test_storage_deletes_only_target_and_keeps_bad_lines(tmp_path):
    storage = Storage(data_dir=str(tmp_path))
    _append(storage.messages_file, [
        {"role": "user", "content": "要删掉的这一条",
         "timestamp": "2026-10-25T00:00:00.000001+00:00"},
        {"role": "assistant", "content": "必须保留的一条",
         "timestamp": "2026-10-25T00:00:01.000002+00:00"},
    ])
    # 历史坏行：删除不得因它失败，也不得顺手删掉它
    with open(storage.messages_file, "a", encoding="utf-8") as f:
        f.write("{这一行不是合法 JSON\n")

    assert storage.find_message("2026-10-25T00:00:00.000001+00:00")["content"] == "要删掉的这一条"
    assert storage.find_message("2026-10-25T00:00:00.000001") is None
    assert storage.delete_message("不存在") is False

    assert storage.delete_message("2026-10-25T00:00:00.000001+00:00") is True
    text = storage.messages_file.read_text(encoding="utf-8")
    assert "要删掉的这一条" not in text
    assert "必须保留的一条" in text
    assert "{这一行不是合法 JSON" in text
    # 幂等：再删一次如实返回 False（不是静默成功）
    assert storage.delete_message("2026-10-25T00:00:00.000001+00:00") is False


def test_storage_delete_preserves_jsonl_and_leaves_no_tmp(tmp_path):
    storage = Storage(data_dir=str(tmp_path))
    _append(storage.messages_file, [
        {"role": "user", "content": "a", "timestamp": "2026-10-25T00:00:00.000001+00:00"},
        {"role": "user", "content": "b", "timestamp": "2026-10-25T00:00:01.000002+00:00"},
    ])
    assert storage.delete_message("2026-10-25T00:00:00.000001+00:00") is True
    lines = [line for line in storage.messages_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["content"] == "b"
    # 原子重写不留临时文件
    assert not (tmp_path / "messages.jsonl.tmp").exists()


# ════════════════════════════════════════════════════════════
#  管理者层（内存窗口必须同步）
# ════════════════════════════════════════════════════════════


def _manager(tmp_path):
    from memory.memory_manager import MemoryManager

    return MemoryManager(config={"data_dir": str(tmp_path / "mem"),
                                 "async_compress": {"enabled": False}})


def _stop(mgr) -> None:
    try:
        mgr._async_compressor.stop()
    except Exception:  # noqa: BLE001 测试收尾尽力而为
        pass


def test_manager_delete_clears_window_copy(tmp_path):
    mgr = _manager(tmp_path)
    try:
        msg_id = mgr.score_and_save_message("user", "要被删掉的记忆正文")
        assert mgr.find_message(msg_id)["content"] == "要被删掉的记忆正文"
        assert any(m.get("timestamp") == msg_id for m in mgr._message_window)

        assert mgr.delete_message(msg_id) is True
        assert not any(m.get("timestamp") == msg_id for m in mgr._message_window), \
            "文件删了但窗口里还有副本 => 已删除内容仍会出现在上下文里"
        assert mgr.find_message(msg_id) is None
        assert mgr.delete_message(msg_id) is False
    finally:
        _stop(mgr)


# ════════════════════════════════════════════════════════════
#  HTTP 层
# ════════════════════════════════════════════════════════════


@pytest.fixture()
def client(monkeypatch, tmp_path):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    mgr = _manager(tmp_path)

    class _Yunshu:
        _memory = mgr

    fake_app_server = types.ModuleType("app_server")
    fake_app_server._Yunshu = _Yunshu
    fake_app_server.logger = logging.getLogger("fake_app_server")
    # plugins/memory.py 的 _view 惰性取这两个装饰器；测试只关心响应契约
    fake_app_server.require_token = lambda f: f
    fake_app_server.log_request = lambda *a, **k: (lambda f: f)
    monkeypatch.setitem(sys.modules, "app_server", fake_app_server)

    # 审计替身：单测不写真实审计库（同时断言"删除会写审计"）
    fake_audit = types.ModuleType("agent.audit.facade")
    fake_audit.audit = lambda *a, **k: types.SimpleNamespace(seq=4242)
    monkeypatch.setitem(sys.modules, "agent.audit.facade", fake_audit)

    from flask import Flask

    from agent.api_envelope import install_error_handlers

    mem = importlib.import_module("plugins.memory")
    app = Flask(__name__)
    app.register_blueprint(mem.bp)
    install_error_handlers(app, enabled=True)
    app.config["TESTING"] = True
    try:
        yield app.test_client(), mgr
    finally:
        _stop(mgr)


def _seed(mgr, text: str = "外部记忆导入的记忆正文") -> str:
    return mgr.score_and_save_message("user", text)


def test_manual_returns_msg_id(client):
    http, _mgr = client
    resp = http.post("/api/memory/manual", json={"content": "记一笔"})
    assert resp.status_code == 200
    assert resp.headers.get("X-Envelope") == "v2"
    data = resp.get_json()["data"]
    assert data["ok"] is True
    assert data["msg_id"], "不回 msg_id 就等于没有删除入口（T3 的根因）"


def test_delete_defaults_to_dry_run_with_preview(client):
    http, mgr = client
    msg_id = _seed(mgr, "这段内容要先给人看一眼再删")
    resp = http.delete("/api/memory/messages/" + msg_id)
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["dry_run"] is True and data["found"] is True
    assert "这段内容" in data["preview"]
    assert "滚动摘要" in data["note"]
    # dry-run 不得动数据
    assert mgr.find_message(msg_id) is not None


def test_delete_without_confirm_is_rejected(client):
    http, mgr = client
    msg_id = _seed(mgr)
    resp = http.delete("/api/memory/messages/" + msg_id, json={"dry_run": False})
    assert resp.status_code == 400
    assert resp.mimetype == "application/problem+json"
    assert "confirm" in resp.get_json()["detail"]
    assert mgr.find_message(msg_id) is not None, "缺 confirm 时绝不能删"


def test_delete_with_confirm_removes_and_audits(client):
    http, mgr = client
    msg_id = _seed(mgr, "确认后应当被物理删除")
    resp = http.delete("/api/memory/messages/" + msg_id,
                       json={"dry_run": False, "confirm": True})
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["deleted"] is True
    assert data["audit"]["seq"] == 4242
    assert mgr.find_message(msg_id) is None
    assert "确认后应当被物理删除" not in mgr._storage.messages_file.read_text(encoding="utf-8")


def test_delete_unknown_id_is_404_and_invalid_shape_is_400(client):
    http, _mgr = client
    missing = http.delete("/api/memory/messages/2026-10-25T00:00:00.999999+00:00")
    assert missing.status_code == 404
    assert missing.get_json()["type"].endswith("message-not-found")

    # 太短 ⇒ 路由命中但形态闸门拒绝（含 "/" 的取值根本不会命中该路由，那是 Flask 的 404）
    bad = http.delete("/api/memory/messages/abc")
    assert bad.status_code == 400
    assert bad.get_json()["type"].endswith("invalid-msg-id")


def test_delete_by_index_no_longer_pretends_success(client):
    http, _mgr = client
    resp = http.delete("/api/memory/3")
    assert resp.status_code == 501, "旧实现恒回 ok:true 却什么都没删（假成功）"
    body = resp.get_json()
    assert "/api/memory/messages/" in body["detail"]
    assert body["type"].endswith("delete-by-index-unsupported")

