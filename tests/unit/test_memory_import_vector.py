# -*- coding: utf-8 -*-
"""外部记忆导入改走向量记忆（交接单 §10 第 2 步）。

这一组钉住"载体换了、回滚凭据也换了"的四件事：

1. `targets.write_memory_note` 写的是**向量库**（POST /api/vector/add），
   正文不再进 messages.jsonl；
2. metadata 必须带 {source: memory_import, batch_id, origin, period, digest_source}
   —— 少一个就没法按批过滤/回滚/追责；
3. 写入回执的 vector_ids 落进批次清单；没有 id 就当场失败（不写一条删不掉的向量）；
4. `rollback` 按 vector_ids **真删**；失败如实回 manual 且**不删清单**；
   404（本来就不在）单独记 missing，不冒充"删掉了"。
"""
from __future__ import annotations

import io
import json

import pytest

from agent.memory_import import service, targets
from agent.memory_import.models import Batch, CARRIER_CARD, CARRIER_MEMORY


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


def _batch(**over) -> Batch:
    base = dict(batch_id="mb_0123456789ab", carrier=CARRIER_MEMORY,
                title="claude 会话摘要 agent 2026-07", source="claude",
                digest="## 摘要\n原句一\n原句二", record_ids=["mr_1", "mr_2"],
                date="2026-07", digest_source="rule")
    base.update(over)
    return Batch(**base)


# ════════════════════════════════════════════════════════════
#  写入：走向量库，metadata 齐全，回执带 vector_ids
# ════════════════════════════════════════════════════════════


def test_vector_sink_posts_to_vector_add_with_metadata(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen.setdefault("methods", []).append(request.get_method())
        if request.get_method() == "POST":
            seen["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse({"ok": True, "item_id": "mem_abc123"})
        # 写入后回读校验（PR：向量写入不许假成功）
        return _FakeResponse({"id": "mem_abc123", "content": "正文"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = targets._default_vector_sink("正文", metadata={"source": "memory_import"})
    assert seen["methods"] == ["POST", "GET"], "写入后必须回读一次（不许假成功）"
    assert seen["url"].startswith("http://127.0.0.1:5678/api/vector/")
    assert seen["body"]["metadata"] == {"source": "memory_import"}
    assert out["detail"]["vector_ids"] == ["mem_abc123"]
    assert out["ref"] == "vector/mem_abc123"


def test_vector_sink_rejects_response_without_item_id(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda request, timeout=None: _FakeResponse({"ok": True}))
    with pytest.raises(RuntimeError, match="item_id"):
        targets._default_vector_sink("正文")


def test_write_memory_note_writes_vector_with_required_metadata(monkeypatch):
    captured = {}

    def fake_sink(content, *, metadata=None):
        captured["content"] = content
        captured["metadata"] = metadata
        return {"ok": True, "ref": "vector/mem_x", "detail": {"vector_ids": ["mem_x"]}}

    out = targets.write_memory_note(_batch(), sink=fake_sink)
    meta = captured["metadata"]
    assert meta["source"] == "memory_import"
    assert meta["batch_id"] == "mb_0123456789ab"
    assert meta["origin"] == "claude"
    assert meta["period"] == "2026-07"
    assert meta["digest_source"] == "rule"
    assert "mb_0123456789ab" in captured["content"], "正文仍要带批次号，便于人工核对"
    assert out["detail"]["vector_ids"] == ["mem_x"]
    assert out["carrier"] == CARRIER_MEMORY


def test_write_memory_note_fails_when_no_vector_id():
    with pytest.raises(RuntimeError, match="vector_ids"):
        targets.write_memory_note(_batch(), sink=lambda content, metadata=None: {"ok": True})


# ════════════════════════════════════════════════════════════
#  删除：404 单独报 missing
# ════════════════════════════════════════════════════════════


def test_delete_vector_item_404_is_missing_not_failure(monkeypatch):
    import urllib.error

    def boom(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {},
                                     io.BytesIO(b'{"title":"not found"}'))

    monkeypatch.setattr("urllib.request.urlopen", boom)
    out = targets.delete_vector_item("mem_gone")
    assert out["ok"] is True and out["deleted"] is False and out["missing"] is True


def test_delete_vector_item_parses_envelope(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        return _FakeResponse({"code": 200, "data": {"ok": True, "deleted": True,
                                                   "item_id": "mem_1", "audit": {"seq": 7}}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = targets.delete_vector_item("mem_1")
    assert seen["method"] == "DELETE"
    assert seen["url"].endswith("/api/vector/item/mem_1")
    assert out["deleted"] is True and out["audit"]["seq"] == 7


# ════════════════════════════════════════════════════════════
#  回滚：按 vector_ids 真删 / 失败不删清单 / 清单标 auto
# ════════════════════════════════════════════════════════════


def _manifest(root, batch_id, *, carrier=CARRIER_MEMORY, vector_ids=None,
              msg_id="", migrated=False) -> None:
    service.batch_dir(root).mkdir(parents=True, exist_ok=True)
    detail = {}
    if vector_ids:
        detail["vector_ids"] = list(vector_ids)
    if msg_id:
        detail["msg_id"] = msg_id
    detail["origin"] = "trae"
    detail["period"] = "2026-05"
    entry = {"batch_id": batch_id, "carrier": carrier, "title": "t",
             "records": 2, "chars": 10, "ref": "vector/mem_1", "detail": detail,
             "plan_id": "mp_x", "committed_at": "2026-10-25T00:00:00+08:00",
             "audit": {"seq": 1}, "migrated": bool(migrated)}
    (service.batch_dir(root) / f"{batch_id}.json").write_text(
        json.dumps(entry, ensure_ascii=False), encoding="utf-8")


def test_rollback_vectors_dry_run_lists_ids(tmp_path, monkeypatch):
    _manifest(tmp_path, "mb_aaaaaaaaaaaa", vector_ids=["mem_1", "mem_2"])
    called = []
    monkeypatch.setattr(targets, "delete_vector_item",
                        lambda *a, **k: called.append(a) or {"ok": True, "deleted": True})
    out = service.rollback("mb_aaaaaaaaaaaa", root=tmp_path)
    assert out["dry_run"] is True
    assert out["would_delete"] == "vector:mem_1,mem_2"
    assert called == [], "dry-run 绝不能真删"


def test_rollback_vectors_deletes_all_and_removes_manifest(tmp_path, monkeypatch):
    _manifest(tmp_path, "mb_bbbbbbbbbbbb", vector_ids=["mem_1", "mem_2"])
    seen = []

    def fake_delete(item_id, **kwargs):
        seen.append(item_id)
        return {"ok": True, "deleted": True, "audit": {"seq": 9003}}

    monkeypatch.setattr(targets, "delete_vector_item", fake_delete)
    monkeypatch.setattr(service, "_audit", lambda *a, **k: {"seq": 9003})
    monkeypatch.setattr(service, "_flush_audit", lambda: True)
    out = service.rollback("mb_bbbbbbbbbbbb", confirm=True, dry_run=False, root=tmp_path)
    assert out["ok"] is True and out["dry_run"] is False
    assert seen == ["mem_1", "mem_2"]
    assert out["deleted"] == "vector:mem_1,mem_2"
    assert not (service.batch_dir(tmp_path) / "mb_bbbbbbbbbbbb.json").exists()


def test_rollback_vectors_partial_failure_keeps_manifest_and_reports_manual(tmp_path, monkeypatch):
    _manifest(tmp_path, "mb_cccccccccccc", vector_ids=["mem_1", "mem_2"])

    def fake_delete(item_id, **kwargs):
        if item_id == "mem_2":
            raise RuntimeError("HTTP 500")
        return {"ok": True, "deleted": True}

    monkeypatch.setattr(targets, "delete_vector_item", fake_delete)
    monkeypatch.setattr(service, "_audit", lambda *a, **k: {})
    monkeypatch.setattr(service, "_flush_audit", lambda: True)
    out = service.rollback("mb_cccccccccccc", confirm=True, dry_run=False, root=tmp_path)
    assert out["ok"] is False and out["manual"] is True
    assert out["deleted"] == "vector:mem_1", "删掉的那条要如实记账"
    assert out["failed"] and "mem_2" in out["failed"][0]
    assert (service.batch_dir(tmp_path) / "mb_cccccccccccc.json").exists(), \
        "失败时不能删清单，否则界面会看不见这条还没回滚的批次"


def test_rollback_vectors_requires_confirm(tmp_path):
    _manifest(tmp_path, "mb_dddddddddddd", vector_ids=["mem_1"])
    from agent.memory_import.models import ConfirmationRequired
    with pytest.raises(ConfirmationRequired):
        service.rollback("mb_dddddddddddd", confirm=False, dry_run=False, root=tmp_path)


def test_rollback_prefers_vector_ids_over_legacy_msg_id(tmp_path, monkeypatch):
    """迁移后的批次既有 vector_ids（新）又留着老 msg_id：必须走向量，不动消息流。"""
    _manifest(tmp_path, "mb_eeeeeeeeeeee", vector_ids=["mem_9"], msg_id="2026-10-24T02:44:24.5+00:00")
    called = {"vector": [], "memory": []}
    monkeypatch.setattr(targets, "delete_vector_item",
                        lambda i, **k: called["vector"].append(i) or {"ok": True, "deleted": True})
    monkeypatch.setattr(targets, "delete_memory_note",
                        lambda i, **k: called["memory"].append(i) or {"ok": True})
    monkeypatch.setattr(service, "_audit", lambda *a, **k: {})
    monkeypatch.setattr(service, "_flush_audit", lambda: True)
    out = service.rollback("mb_eeeeeeeeeeee", confirm=True, dry_run=False, root=tmp_path)
    assert out["ok"] is True
    assert called["vector"] == ["mem_9"] and called["memory"] == []


def test_list_batches_marks_vector_batches_auto_and_shows_source(tmp_path):
    _manifest(tmp_path, "mb_ffffffffffff", vector_ids=["mem_1"])
    _manifest(tmp_path, "mb_000000000000", carrier=CARRIER_CARD)
    _manifest(tmp_path, "mb_111111111111", migrated=True, vector_ids=["mem_2"])
    rows = {row["batch_id"]: row for row in service.list_batches(root=tmp_path)}
    assert rows["mb_ffffffffffff"]["rollback"] == "auto"
    assert rows["mb_ffffffffffff"]["vector_ids"] == ["mem_1"]
    assert rows["mb_ffffffffffff"]["origin"] == "trae"
    assert rows["mb_111111111111"]["migrated"] is True
    assert rows["mb_000000000000"]["carrier_label"] == "知识卡片"
    assert rows["mb_ffffffffffff"]["carrier_label"] == "向量记忆"
