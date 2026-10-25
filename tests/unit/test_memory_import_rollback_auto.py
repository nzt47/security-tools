# -*- coding: utf-8 -*-
"""外部记忆导入 · 长期记忆的自动回滚（交接单 §9 T3 遗留）。

T3 之前：rollback 对 memory_note 只能回 manual（因为没有 msg_id、也没有按 id 删除入口）。
T3 之后：写入方把服务端回执里的 msg_id 落进批次清单 detail；rollback 凭它**按 id 物理删**。

这组用例钉住四件事：
1. 写入回执要留下 msg_id（否则回滚永远只能是 manual）；
2. 删除请求必须显式带 dry_run=False & confirm=True（不接受"只给 id 就删"）；
3. rollback 默认 dry_run 只回 would_delete，真删才调 delete_memory_note；
4. 没有 msg_id 的**老批次**仍如实报 manual（不假装能删）。
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from agent.memory_import import service, targets
from agent.memory_import.models import CARRIER_CARD, CARRIER_MEMORY


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ════════════════════════════════════════════════════════════
#  写入回执带 msg_id
# ════════════════════════════════════════════════════════════


def test_memory_sink_captures_msg_id(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        return _FakeResponse({"code": 200, "data": {"ok": True, "msg_id": "2026-10-25T00:00:00.1+00:00"}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = targets._default_memory_sink("正文", priority="low")
    assert out["ok"] is True
    assert out["detail"]["msg_id"] == "2026-10-25T00:00:00.1+00:00", \
        "写入没留下 msg_id ⇒ 这条记忆以后没有任何稳定入口可删"
    assert out["ref"] == "memory/manual"


def test_memory_sink_without_msg_id_keeps_detail_empty(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda request, timeout=None: _FakeResponse({"code": 200, "data": {"ok": True}}))
    out = targets._default_memory_sink("正文")
    assert out["detail"] == {}, "服务端没回 msg_id 时不要编一个假的"


# ════════════════════════════════════════════════════════════
#  删除请求的判据
# ════════════════════════════════════════════════════════════


def test_delete_memory_note_sends_confirm_and_parses_envelope(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["method"] = request.get_method()
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse({"code": 200, "data": {"ok": True, "deleted": True,
                                                    "msg_id": "m1", "dry_run": False,
                                                    "audit": {"seq": 9001}}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = targets.delete_memory_note("m1")
    assert seen["method"] == "DELETE"
    assert seen["url"].endswith("/api/memory/messages/m1")
    assert seen["body"] == {"dry_run": False, "confirm": True}
    assert out["ok"] is True and out["deleted"] is True
    assert out["audit"]["seq"] == 9001


def test_delete_memory_note_rejects_empty_id():
    with pytest.raises(ValueError):
        targets.delete_memory_note("   ")


# ════════════════════════════════════════════════════════════
#  rollback 三条路（有 msg_id / 无 msg_id / dry-run）
# ════════════════════════════════════════════════════════════


def _manifest(root: Path, batch_id: str, *, carrier: str, ref: str = "",
              msg_id: str = "") -> None:
    service.batch_dir(root).mkdir(parents=True, exist_ok=True)
    entry = {"batch_id": batch_id, "carrier": carrier, "title": "t",
             "records": 2, "chars": 10, "ref": ref,
             "detail": ({"msg_id": msg_id} if msg_id else {}),
             "plan_id": "mp_x", "committed_at": "2026-10-25T00:00:00+08:00",
             "audit": {"seq": 1}}
    (service.batch_dir(root) / f"{batch_id}.json").write_text(
        json.dumps(entry, ensure_ascii=False), encoding="utf-8")


def test_rollback_memory_with_msg_id_dry_run(tmp_path, monkeypatch):
    _manifest(tmp_path, "mb_aaaaaaaaaaaa", carrier=CARRIER_MEMORY, msg_id="m1")
    called = []
    monkeypatch.setattr(targets, "delete_memory_note",
                        lambda *a, **k: called.append((a, k)) or {"ok": True})
    out = service.rollback("mb_aaaaaaaaaaaa", root=tmp_path)
    assert out["dry_run"] is True and out["would_delete"] == "memory:m1"
    assert called == [], "dry-run 绝不能真删"


def test_rollback_memory_with_msg_id_deletes(tmp_path, monkeypatch):
    _manifest(tmp_path, "mb_bbbbbbbbbbbb", carrier=CARRIER_MEMORY, msg_id="m2")
    seen = {}

    def fake_delete(msg_id, **kwargs):
        seen["msg_id"] = msg_id
        return {"ok": True, "deleted": True, "audit": {"seq": 9002}}

    monkeypatch.setattr(targets, "delete_memory_note", fake_delete)
    out = service.rollback("mb_bbbbbbbbbbbb", confirm=True, dry_run=False, root=tmp_path)
    assert out["ok"] is True and out["deleted"] == "memory:m2"
    assert seen["msg_id"] == "m2"
    # 成功后批次清单应被移除（否则界面会一直显示一条已删的批次）
    assert not (service.batch_dir(tmp_path) / "mb_bbbbbbbbbbbb.json").exists()


def test_rollback_memory_without_msg_id_stays_manual(tmp_path):
    _manifest(tmp_path, "mb_cccccccccccc", carrier=CARRIER_MEMORY)
    out = service.rollback("mb_cccccccccccc", root=tmp_path)
    assert out["manual"] is True and out["ok"] is False
    assert "msg_id" in out["error"], "要如实说明缺什么（老批次没有 msg_id）"


def test_rollback_memory_delete_failure_is_honest(tmp_path, monkeypatch):
    _manifest(tmp_path, "mb_dddddddddddd", carrier=CARRIER_MEMORY, msg_id="m4")

    def boom(msg_id, **kwargs):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(targets, "delete_memory_note", boom)
    out = service.rollback("mb_dddddddddddd", confirm=True, dry_run=False, root=tmp_path)
    assert out["ok"] is False and out["manual"] is True
    assert "RuntimeError" in out["error"]
    # 失败时不能删清单（否则就"看不见"这条还没回滚的批次了）
    assert (service.batch_dir(tmp_path) / "mb_dddddddddddd.json").exists()


def test_list_batches_marks_memory_with_msg_id_as_auto(tmp_path):
    _manifest(tmp_path, "mb_eeeeeeeeeeee", carrier=CARRIER_MEMORY, msg_id="m5")
    _manifest(tmp_path, "mb_ffffffffffff", carrier=CARRIER_MEMORY)
    _manifest(tmp_path, "mb_000000000000", carrier=CARRIER_CARD, ref="slug-1")
    rows = {row["batch_id"]: row for row in service.list_batches(root=tmp_path)}
    assert rows["mb_eeeeeeeeeeee"]["rollback"] == "auto"
    assert rows["mb_ffffffffffff"]["rollback"] == "manual"
    assert rows["mb_000000000000"]["rollback"] == "auto"

