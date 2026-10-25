# -*- coding: utf-8 -*-
"""存量迁移：消息流 -> 向量记忆（交接单 §10 第 3 步）。

钉住五件事：
1. dry-run 只盘点，不写向量、不删消息；
2. 真迁：写向量（metadata 带 source/batch/origin/period/digest_source）→ 清单记
   vector_ids → 按 msg_id 删原消息 → 标 migrated → 写审计；
3. 删原消息失败时**补偿删除刚写的向量**，清单不得标 migrated（不许留下双写）；
4. 清单说写过、消息里却没有的批次：不假装迁成功，单独报 missing_in_messages；
5. 同一 timestamp 被两条批次共用时，第二条 deleted=False 也算迁移完成
   （它的正文确实已不在消息流里），并如实记录。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.memory_import import migrate, service
from agent.memory_import.models import CARRIER_CARD, CARRIER_MEMORY

MB_A = "mb_aaaaaaaaaaaa"
MB_B = "mb_bbbbbbbbbbbb"
MB_C = "mb_cccccccccccc"


def _manifest(root: Path, batch_id: str, *, carrier: str = CARRIER_MEMORY,
              detail: dict | None = None, migrated: bool = False) -> Path:
    service.batch_dir(root).mkdir(parents=True, exist_ok=True)
    entry = {
        "batch_id": batch_id, "carrier": carrier, "title": f"title-{batch_id}",
        "records": 3, "chars": 40, "ref": "memory/manual",
        "detail": dict(detail or {}), "plan_id": "mp_x",
        "committed_at": "2026-10-24T10:44:24+08:00", "audit": {"seq": 1},
    }
    if migrated:
        entry["migrated"] = True
    path = service.batch_dir(root) / f"{batch_id}.json"
    path.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    return path


def _messages(path: Path, rows: list) -> Path:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")
    return path


def _row(batch_id: str, ts: str, body: str = "原句一\n原句二") -> dict:
    return {"role": "user", "timestamp": ts,
            "content": f"[手动记忆·优先级:low] [外部记忆导入 {batch_id} | t] {body}",
            "importance_score": 0.5}


def test_split_and_index_only_recognises_import_rows():
    rows = [_row(MB_A, "2026-10-24T02:44:24.513746+00:00"),
            {"role": "user", "timestamp": "x", "content": "[手动记忆·优先级:low] 普通记忆"},
            {"role": "assistant", "timestamp": "y", "content": "外部记忆导入 但不是头"}]
    index = migrate.index_messages(rows)
    assert list(index) == [MB_A]
    assert index[MB_A]["msg_id"] == "2026-10-24T02:44:24.513746+00:00"
    assert index[MB_A]["content"].startswith("原句一")


def test_scan_reports_pending_and_missing(tmp_path):
    root = tmp_path / "mi"
    _manifest(root, MB_A)
    _manifest(root, MB_B)
    _manifest(root, MB_C, carrier=CARRIER_CARD)
    msgs = _messages(tmp_path / "messages.jsonl", [_row(MB_A, "ts-a")])
    plan = migrate.scan(root=root, messages_path=msgs)
    assert [b["batch_id"] for b in plan["pending"]] == [MB_A]
    assert plan["missing_in_messages"] == [MB_B], "清单有、消息里没有的批次不能算迁成功"
    assert plan["messages_without_manifest"] == []


def test_dry_run_writes_and_deletes_nothing(tmp_path):
    root = tmp_path / "mi"
    _manifest(root, MB_A)
    msgs = _messages(tmp_path / "messages.jsonl", [_row(MB_A, "ts-a")])
    calls = []
    out = migrate.migrate(root=root, messages_path=msgs,
                          vector_sink=lambda c, metadata=None: calls.append("sink") or {},
                          delete_message=lambda i: calls.append("del") or {"ok": True})
    assert out["dry_run"] is True and out["applied"] is False
    assert [w["batch_id"] for w in out["would"]] == [MB_A]
    assert calls == []


def test_commit_migrates_and_marks_manifest(tmp_path):
    root = tmp_path / "mi"
    path = _manifest(root, MB_A)
    msgs = _messages(tmp_path / "messages.jsonl", [_row(MB_A, "ts-a", "正文甲")])
    seen = {}
    audits = []

    def fake_sink(content, *, metadata=None):
        seen["content"] = content
        seen["metadata"] = metadata
        return {"ok": True, "ref": "vector/mem_1", "detail": {"vector_ids": ["mem_1"]}}

    out = migrate.migrate(root=root, messages_path=msgs, commit=True, confirm=True,
                          vector_sink=fake_sink,
                          delete_message=lambda i: seen.__setitem__("deleted", i) or {"ok": True, "deleted": True},
                          audit_fn=lambda b, ids, d: audits.append((b["batch_id"], list(ids))) or {"seq": 7},
                          flush=lambda: True)
    assert out["ok"] is True
    assert seen["deleted"] == "ts-a"
    assert seen["metadata"]["source"] == "memory_import"
    assert seen["metadata"]["batch_id"] == MB_A
    assert seen["metadata"]["migrated_from"] == "messages.jsonl"
    assert "正文甲" in seen["content"], "搬的必须是当时写进去的实际正文"
    entry = json.loads(path.read_text(encoding="utf-8"))
    assert entry["migrated"] is True and entry["migration_state"] == "done"
    assert entry["detail"]["vector_ids"] == ["mem_1"]
    assert audits == [(MB_A, ["mem_1"])]
    assert out["migrated"][0]["audit"] == {"seq": 7}


def test_delete_failure_compensates_vector_and_keeps_manifest_unmigrated(tmp_path):
    root = tmp_path / "mi"
    path = _manifest(root, MB_A)
    msgs = _messages(tmp_path / "messages.jsonl", [_row(MB_A, "ts-a")])
    compensated = []

    def boom(msg_id):
        raise RuntimeError("HTTP 500")

    out = migrate.migrate(root=root, messages_path=msgs, commit=True, confirm=True,
                          vector_sink=lambda c, metadata=None: {"detail": {"vector_ids": ["mem_9"]}},
                          delete_message=boom,
                          delete_vector=lambda i: compensated.append(i),
                          audit_fn=lambda *a, **k: {"seq": 0}, flush=lambda: True)
    assert out["ok"] is False and out["failed"]
    assert "删除原消息失败" in out["failed"][0]["error"]
    entry = json.loads(path.read_text(encoding="utf-8"))
    assert entry.get("migrated") is not True
    assert entry["migration_state"] == "failed"
    assert "vector_ids" not in (entry.get("detail") or {}), "补偿成功后不该留下可回滚的旧 id"


def test_shared_timestamp_second_batch_counts_as_migrated(tmp_path):
    root = tmp_path / "mi"
    _manifest(root, MB_A)
    _manifest(root, MB_B)
    msgs = _messages(tmp_path / "messages.jsonl",
                     [_row(MB_A, "same-ts"), _row(MB_B, "same-ts")])
    results = {}

    def fake_delete(msg_id):
        # 按 timestamp 删除会一次删掉两行：第二次调用已经没有可删的了
        if results.get("first"):
            return {"ok": True, "deleted": False}
        results["first"] = True
        return {"ok": True, "deleted": True}

    out = migrate.migrate(root=root, messages_path=msgs, commit=True, confirm=True,
                          vector_sink=lambda c, metadata=None: {
                              "detail": {"vector_ids": ["mem_" + ("a" if MB_A in json.dumps(metadata) else "b")]}},
                          delete_message=fake_delete,
                          audit_fn=lambda *a, **k: {"seq": 0}, flush=lambda: True)
    assert out["ok"] is True
    assert [m["deleted"] for m in out["migrated"]] == [True, False]
    assert all(json.loads((service.batch_dir(root) / f"{m['batch_id']}.json")
                          .read_text(encoding="utf-8"))["migrated"] for m in out["migrated"])


def test_commit_requires_confirm(tmp_path):
    root = tmp_path / "mi"
    _manifest(root, MB_A)
    msgs = _messages(tmp_path / "messages.jsonl", [_row(MB_A, "ts-a")])
    from agent.memory_import.models import ConfirmationRequired
    with pytest.raises(ConfirmationRequired):
        migrate.migrate(root=root, messages_path=msgs, commit=True, confirm=False)


def test_already_migrated_batches_are_skipped(tmp_path):
    root = tmp_path / "mi"
    _manifest(root, MB_A, detail={"vector_ids": ["mem_1"]})
    _manifest(root, MB_B, migrated=True)
    msgs = _messages(tmp_path / "messages.jsonl", [_row(MB_A, "ts-a"), _row(MB_B, "ts-b")])
    plan = migrate.scan(root=root, messages_path=msgs)
    assert plan["pending"] == []
    assert plan["messages_without_manifest"] == [], "已有 vector_ids 的批次不算孤儿"
