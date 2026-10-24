# -*- coding: utf-8 -*-
"""外部记忆导入：读取器 / 脱敏 / 流水线 / 预览提交闸门。

覆盖三条硬约束：
1. 只读（读取器不写任何文件，预览只落计划快照）；
2. 默认 dry-run，真写必须 confirm=True；
3. 敏感信息先掩码、再闸门复检，命中即拒绝（fail-closed）。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import struct
import sys
import types
from pathlib import Path

import pytest

from agent.memory_import import pipeline, redact, service
from agent.memory_import.models import (
    CARRIER_CARD, CARRIER_MEMORY, KIND_MESSAGE, KIND_PREFERENCE, KIND_PROMPT,
    ROLE_ASSISTANT, ROLE_USER, ConfirmationRequired, MemoryRecord, PlanNotFound,
    UnknownBatch,
)
from agent.memory_import.readers import claude as claude_reader
from agent.memory_import.readers import leveldb, trae as trae_reader


# ════════════════════════════════════════════════════════════
#  合成数据
# ════════════════════════════════════════════════════════════


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _put(key: bytes, value: bytes) -> bytes:
    return b"\x01" + _varint(len(key)) + key + _varint(len(value)) + value


def _delete(key: bytes) -> bytes:
    return b"\x00" + _varint(len(key)) + key


def _write_log(path: Path, batches) -> None:
    blob = b""
    for seq, items in batches:
        record = struct.pack("<Q", seq) + struct.pack("<I", len(items)) + b"".join(items)
        blob += struct.pack("<IHB", 0, len(record), 1) + record
    path.write_bytes(blob)


def _make_trae(root: Path, values: dict) -> Path:
    db = root / "Trae CN" / "User" / "globalStorage" / "state.vscdb"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("create table ItemTable(key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
    con.executemany("insert into ItemTable(key, value) values (?, ?)", list(values.items()))
    con.commit()
    con.close()
    return db


def _query_value(items) -> str:
    return json.dumps({"version": 1, "response": {"resp": {"success": True, "code": 0,
                                                          "error": "", "result": items}}},
                      ensure_ascii=False)


# ════════════════════════════════════════════════════════════
#  LevelDB 读取器
# ════════════════════════════════════════════════════════════


def test_leveldb_log_put_delete_and_later_wins(tmp_path):
    log = tmp_path / "000001.log"
    _write_log(log, [
        (1, [_put(b"a", b"1"), _put(b"b", b"2")]),
        (2, [_put(b"a", b"1-new")]),
        (3, [_delete(b"b")]),
    ])
    scan = leveldb.scan_leveldb(tmp_path)
    assert scan.warnings == []
    got = {entry.key: entry.value for entry in scan.entries}
    assert got == {b"a": b"1-new"}
    assert scan.log_entries == 3


def test_leveldb_snappy_literal_and_copy():
    # 目标 abcdefabcdef：先字面量 6 字节，再以偏移 6 复制 6 字节
    data = _varint(12) + bytes([(6 - 1) << 2]) + b"abcdef" + bytes([(2 << 2) | 1, 6])
    assert leveldb.snappy_decompress(data) == b"abcdefabcdef"


def test_leveldb_json_candidates_ignore_unbalanced():
    text = 'noise {"a": 1} tail {"b": [1, 2]} broken {"c": '
    found = leveldb.json_candidates(text)
    assert found[0] == '{"a": 1}'
    assert found[1] == '{"b": [1, 2]}'
    assert len(found) == 2


def test_best_effort_text_prefers_utf16():
    blob = "你好世界 chat".encode("utf-16-le")
    assert "你好世界" in leveldb.best_effort_text(blob)


# ════════════════════════════════════════════════════════════
#  脱敏
# ════════════════════════════════════════════════════════════


def test_redact_masks_sk_and_password_without_plaintext():
    secret = "sk-" + "a1b2c3d4e5f6g7h8"
    text = f"key={secret} password=hunter2hunter2 结束"
    outcome = redact.redact_text(text)
    assert outcome.changed and not outcome.blocked
    assert secret not in outcome.text
    assert "hunter2hunter2" not in outcome.text
    assert outcome.hits.get("openai_sk") == 1
    # 命中明细只有形态名+次数，不含明文
    assert secret not in json.dumps(outcome.to_dict(), ensure_ascii=False)


def test_redact_fails_closed_when_gate_unavailable(monkeypatch):
    monkeypatch.setattr(redact, "gate_check",
                        lambda text: ([redact.GATE_UNAVAILABLE], False))
    outcome = redact.redact_text("普通文本没有密钥")
    assert outcome.blocked is True
    assert redact.GATE_UNAVAILABLE in outcome.gate_forms


# ════════════════════════════════════════════════════════════
#  流水线
# ════════════════════════════════════════════════════════════


def _rec(text, *, role=ROLE_USER, kind=KIND_MESSAGE, ts="2026-07-01T10:00:00+08:00",
         source="claude", session="s1", project="p1"):
    return MemoryRecord(source=source, session_id=session, ts=ts, role=role,
                        text=text, kind=kind, project=project)


def test_denoise_drops_template_confirmation_and_blob():
    template = _rec("<system-reminder>系统注入</system-reminder>")
    confirm = _rec("好的")
    blob = _rec("Traceback (most recent call last)\n" + "  at x (y.js:1)\n" * 150)
    keep = _rec("帮我把部署脚本改成幂等的，失败可重跑")
    kept, dropped, reasons = pipeline.denoise([template, confirm, blob, keep])
    assert [r.text for r in kept] == [keep.text]
    assert reasons == {"template": 1, "short_confirmation": 1, "tool_result_blob": 1}
    assert {d.reason for d in dropped} == set(reasons)


def test_dedupe_keeps_first_and_counts_groups():
    a = _rec("同一句话  重复", session="s1")
    b = _rec("同一句话 重复", session="s2")
    c = _rec("另一句话")
    kept, dropped, stats = pipeline.dedupe([a, b, c])
    assert [r.record_id for r in kept] == [a.record_id, c.record_id]
    assert dropped[0].duplicate_of == a.record_id
    assert stats == {"groups": 1, "extra": 1}


def test_batches_split_by_carrier_and_period():
    records = [
        _rec("## 语言偏好\n一律中文", role="note", kind=KIND_PREFERENCE, project="user"),
        _rec("第一问", kind=KIND_PROMPT, ts="2026-05-01T10:00:00+08:00", source="trae"),
        _rec("第二问", kind=KIND_PROMPT, ts="2026-05-20T10:00:00+08:00", source="trae"),
        _rec("六月问", kind=KIND_PROMPT, ts="2026-06-02T10:00:00+08:00", source="trae"),
    ]
    batches = pipeline.build_batches(records)
    ids = {(b.carrier, b.date) for b in batches}
    assert (CARRIER_CARD, "static") in ids
    assert (CARRIER_MEMORY, "2026-05") in ids
    assert (CARRIER_MEMORY, "2026-06") in ids
    card = next(b for b in batches if b.carrier == CARRIER_CARD)
    assert "一律中文" in card.digest


def test_redact_records_blocks_unsafe_and_masks_safe(monkeypatch):
    monkeypatch.setattr(redact, "gate_check", lambda text: ([], True))
    safe = _rec("key=sk-" + "b" * 20)
    kept, info = pipeline.redact_records([safe])
    assert info["masked_records"] == 1
    assert "sk-bbb" not in kept[0].text

    monkeypatch.setattr(redact, "gate_check",
                        lambda text: ([redact.GATE_UNAVAILABLE], False))
    blocked, info2 = pipeline.redact_records([_rec("随便一句话")])
    assert blocked == [] and info2["blocked"] == 1


# ════════════════════════════════════════════════════════════
#  读取器
# ════════════════════════════════════════════════════════════


def test_trae_reader_reads_prompts_and_reports(tmp_path):
    _make_trae(tmp_path, {
        "chat.ChatSessionStore.index": '{"version":1,"entries":{}}',
        "ai-chat.chatQueryCompletion.v1.aaaa": _query_value([
            {"id": "20260717131215ABC-1", "text": "帮我修 sensor 的 to_dict", "references": []},
            {"id": "20260717131215ABC-2", "text": "再跑一遍测试", "references": []},
        ]),
        "ai-chat.chatQueryCompletion.v1.bbbb": "",
    })
    result = trae_reader.read_trae(appdata=tmp_path)
    report = result.report
    assert report["query_keys"] == 2
    assert report["empty_values"] == 1
    assert report["session_index_entries"] == 0
    assert len(result.records) == 2
    first = result.records[0]
    assert first.source == "trae"
    assert first.ts == "2026-07-17T13:12:15"
    assert first.session_id == "trae-prompts-2026-07-17"
    assert first.kind == KIND_PROMPT


def test_claude_reader_sections_history_and_text_blocks(tmp_path):
    root = tmp_path / ".claude"
    root.mkdir()
    (root / "CLAUDE.md").write_text("# 标题\n开场白\n\n## 语言偏好\n一律中文\n\n## 开发规范\n先写测试\n",
                                    encoding="utf-8")
    (root / "history.jsonl").write_text(
        json.dumps({"display": "你好", "timestamp": 1776949713643,
                    "project": "p", "sessionId": "s1"}, ensure_ascii=False) + "\n" + "not-json\n",
        encoding="utf-8")
    project = root / "projects" / "proj-a"
    project.mkdir(parents=True)
    session = project / "sess-1.jsonl"
    lines = [
        {"type": "user", "sessionId": "sess-1", "timestamp": "2026-06-23T09:02:53.453Z",
         "message": {"role": "user", "content": [
             {"type": "text", "text": "<ide_opened_file>x</ide_opened_file>"},
             {"type": "text", "text": "帮我看看这个报错"}]}},
        {"type": "assistant", "sessionId": "sess-1", "timestamp": "2026-06-23T09:03:00.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "thinking", "thinking": "先读代码"},
             {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
             {"type": "text", "text": "问题在正则的 None 分支"}]}},
        {"type": "queue-operation", "operation": "enqueue"},
    ]
    session.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
                       encoding="utf-8")

    result = claude_reader.read_claude(claude_dir=root)
    kinds = [r.kind for r in result.records]
    assert kinds.count(KIND_PREFERENCE) == 3  # 概述 + 两个二级标题
    assert "开场白" in next(r for r in result.records if r.kind == KIND_PREFERENCE).text
    assert "标题" not in result.records[0].text.splitlines()[0]
    assert kinds.count(KIND_PROMPT) == 1
    assert kinds.count(KIND_MESSAGE) == 2
    messages = [r for r in result.records if r.kind == KIND_MESSAGE]
    # 读取器不做策略：模板原样带出，由流水线的去噪阶段统一剥离
    assert "帮我看看这个报错" in messages[0].text
    assert "<ide_opened_file>" in messages[0].text
    assert pipeline.strip_templates(messages[0].text) == "帮我看看这个报错"
    assert messages[0].role == ROLE_USER
    assert messages[1].role == ROLE_ASSISTANT
    assert "思考" not in messages[1].text
    assert result.report["sessions"]["empty_msgs"] == 0
    assert result.report["history"]["bad_lines"] == 1


# ════════════════════════════════════════════════════════════
#  预览 / 提交 / 回滚
# ════════════════════════════════════════════════════════════


@pytest.fixture()
def trae_env(tmp_path):
    appdata = tmp_path / "appdata"
    _make_trae(appdata, {
        "ai-chat.chatQueryCompletion.v1.aaaa": _query_value([
            {"id": "20260717131215ABC-1", "text": "帮我把部署脚本改成幂等", "references": []},
            {"id": "20260717131215ABC-2", "text": "好的", "references": []},
        ]),
    })
    return appdata, tmp_path / "store"


def test_preview_is_read_only_and_stable(trae_env, monkeypatch):
    appdata, store = trae_env
    monkeypatch.setattr(service, "_audit", lambda *a, **k: {})
    plan = service.preview(sources=["trae"], appdata=appdata, root=store)
    assert plan["dry_run"] is True
    assert plan["requires_confirm"] is True
    assert plan["stats"]["scanned"] == 2
    assert plan["stats"]["denoised"] == 1  # "好的" 被去噪
    assert len(plan["batches"]) == 1
    assert Path(plan["plan_path"]).is_file()
    again = service.preview(sources=["trae"], appdata=appdata, root=store)
    assert again["plan_id"] == plan["plan_id"]
    # 预览不写记忆：批次里只有已脱敏 digest 与记录 id
    batch = plan["batches"][0]
    assert batch["carrier"] == CARRIER_MEMORY
    assert all(rid.startswith("mr_") for rid in batch["record_ids"])


def test_commit_requires_confirm_and_dry_run_replays(trae_env, monkeypatch):
    appdata, store = trae_env
    monkeypatch.setattr(service, "_audit", lambda *a, **k: {})
    plan = service.preview(sources=["trae"], appdata=appdata, root=store)
    dry = service.commit(plan["plan_id"], root=store)
    assert dry["dry_run"] is True and dry["applied"] is False
    assert dry["would"][0]["batch_id"] == plan["batches"][0]["batch_id"]
    with pytest.raises(ConfirmationRequired):
        service.commit(plan["plan_id"], [plan["batches"][0]["batch_id"]],
                       confirm=False, dry_run=False, root=store)


def test_commit_writes_manifest_and_audit_then_rolls_back(trae_env, monkeypatch):
    appdata, store = trae_env
    audited = []
    flushed = []
    monkeypatch.setattr(service, "_audit",
                        lambda batch, outcome, plan_id: audited.append(batch.batch_id) or {})
    # 一次性进程必须把审计队列刷进库（否则最后几条只停在 .seqjournal）
    monkeypatch.setattr(service, "_flush_audit", lambda: flushed.append(True) or True)
    plan = service.preview(sources=["trae"], appdata=appdata, root=store)
    batch_id = plan["batches"][0]["batch_id"]
    written = []

    def fake_writer(batch):
        written.append(batch.batch_id)
        return {"ok": True, "ref": "note-1", "detail": {"chars": batch.chars}}

    result = service.commit(plan["plan_id"], [batch_id], confirm=True, dry_run=False,
                            writers={CARRIER_MEMORY: fake_writer}, root=store)
    assert result["ok"] is True
    assert result["audit_flushed"] is True
    assert written == [batch_id]
    assert audited == [batch_id]
    assert flushed == [True]
    manifest = service.batch_dir(store) / f"{batch_id}.json"
    assert manifest.is_file()
    entry = json.loads(manifest.read_text(encoding="utf-8"))
    assert entry["plan_id"] == plan["plan_id"] and entry["ref"] == "note-1"
    # 长期记忆没有自动回滚入口 => 如实报 manual
    rb = service.rollback(batch_id, root=store)
    assert rb["manual"] is True and rb["ok"] is False


def test_commit_rejects_unknown_batch_and_missing_plan(trae_env, monkeypatch):
    appdata, store = trae_env
    monkeypatch.setattr(service, "_audit", lambda *a, **k: {})
    plan = service.preview(sources=["trae"], appdata=appdata, root=store)
    with pytest.raises(UnknownBatch):
        service.commit(plan["plan_id"], ["mb_nope"], confirm=True, dry_run=False, root=store)
    with pytest.raises(PlanNotFound):
        service.load_plan("mp_missing", root=store)


@pytest.mark.parametrize("bad", ["../../etc/passwd", "mp_../../x", "mp_x", "", "mp_" + "z" * 24])
def test_plan_id_must_match_format(bad, tmp_path):
    """plan_id 会拼进文件路径 ⇒ 必须先过格式闸门（CodeQL 路径注入）。"""
    with pytest.raises(PlanNotFound):
        service.load_plan(bad, root=tmp_path)


def test_rollback_rejects_bad_batch_id(tmp_path):
    with pytest.raises(UnknownBatch):
        service.rollback("../../x", root=tmp_path)


def test_memory_manual_route_targets_real_memory_manager(monkeypatch):
    """载体可用性回归：/api/memory/manual 必须调 MemoryManager 的真实方法。

    2026-10-24 实测：路由调 add_memory（MemoryManager 没有这个方法）⇒ 生产
    环境稳定 500（'MemoryManager' object has no attribute 'add_memory'），
    被 test_envelope_batch6 的同名 stub 掩盖。本用例用"只暴露真实方法"的替身
    盯住契约：再多一个 add_memory 的替身才过的写法就会被这里抓住。
    """
    calls = []

    class _RealMemory:
        def score_and_save_message(self, role, content):
            calls.append((role, content))
            return "2026-10-24T00:00:00+00:00"

    class _YunshuStub:
        _memory = _RealMemory()

    fake_app_server = types.ModuleType("app_server")
    fake_app_server._Yunshu = _YunshuStub
    fake_app_server.require_token = lambda f: f
    fake_app_server.log_request = lambda *a, **k: (lambda f: f)
    fake_app_server.logger = logging.getLogger("fake_app_server")
    monkeypatch.setitem(sys.modules, "app_server", fake_app_server)

    from flask import Flask

    import plugins.memory as memory_plugin

    app = Flask(__name__)
    app.register_blueprint(memory_plugin.bp)
    app.config["TESTING"] = True
    response = app.test_client().post("/api/memory/manual", json={"content": "摘要一条"})
    assert response.status_code == 200, response.get_data(as_text=True)[:200]
    assert calls and calls[0][0] == "user"
    assert "摘要一条" in calls[0][1]


def test_card_writer_disambiguates_slug(tmp_path):
    from agent.memory_import.targets import write_knowledge_card
    from agent.memory_import.models import Batch

    batch = Batch(batch_id="mb_x", carrier=CARRIER_CARD, title="claude 偏好与规则（外部记忆导入）",
                  source="claude", digest="# 规则\n一律中文", record_ids=["mr_1"])

    class FakeStore:
        def __init__(self):
            self.slugs = []

        def create(self, card):
            from agent.knowledge.card import CardConflictError
            if card.slug in self.slugs:
                raise CardConflictError("exists")
            self.slugs.append(card.slug)
            return card

    store = FakeStore()
    first = write_knowledge_card(batch, store=store)
    second = write_knowledge_card(batch, store=store)
    assert first["ref"] != second["ref"]
    assert second["ref"].endswith("-2")
