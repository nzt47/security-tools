# -*- coding: utf-8 -*-
"""yunshu learn CLI 单测（方案交付物 A）。

覆盖：解析器结构 / 「yunshu learn」token 跳过 / 多帧 zstd 读取 /
probe 与 extract 端到端（用合成会话，不依赖真实会话库）。
"""
from __future__ import annotations

import json
import os

import pytest

zstd = pytest.importorskip("zstandard")

from agent.experience_cli import build_parser, main
from agent.experience_cli._common import iter_sessions


def _mk_session(dirpath: str, records) -> str:
    """把记录写成**多帧** session.jsonl.zstd —— 真实 DSH 会话正是这种结构，
    只解首帧的读取器会静默截断（实测 3,830 条 -> 1 条）。
    """
    os.makedirs(dirpath, exist_ok=True)
    p = os.path.join(dirpath, "session.jsonl.zstd")
    c = zstd.ZstdCompressor()
    with open(p, "wb") as fh:
        for r in records:
            fh.write(c.compress((json.dumps(r, ensure_ascii=False) + "\n").encode("utf-8")))
    return p


def _rec(seq, typ, data, time="2026-09-20T10:00:00", **kw):
    o = {"type": typ, "seq": seq, "time": time, "data": data}
    o.update(kw)
    return o


@pytest.fixture()
def fake_lib(tmp_path):
    root = tmp_path / "sessions" / "--ws--" / "sess-1"
    recs = [
        {"type": "session", "cwd": str(tmp_path / "proj"), "id": "s1"},
        # 真实用户消息（kind=user）—— 只有它才是任务来源
        _rec(1, "user/message", {"content": "修复 pytest 断言失败并补充回归测试",
                                 "source": {"kind": "user"}}),
        # 系统样板（kind=plugin）—— 必须被忽略，否则 task 会变成 background job 文本
        _rec(2, "user/message", {"content": "background job pwsh-1 finished",
                                 "source": {"kind": "plugin"}}),
        _rec(3, "turn/start", {"turn": 1}),
        _rec(4, "step/start", {"turn": 1, "step": 1}),
        _rec(5, "tool/call", {"turn": 1, "step": 1, "callId": "c1", "name": "edit",
                              "arguments": json.dumps({"file_path": str(tmp_path / "proj" / "a.py"),
                                                       "old_string": "x = 1", "new_string": "x = 2"})}),
        _rec(6, "tool/result", {"turn": 1, "step": 1,
                                "message": {"source": {"kind": "tool", "callId": "c1"},
                                            "content": [{"type": "text", "toolCallId": "c1",
                                                         "content": [{"type": "text", "text": "ok"}],
                                                         "isError": False}]}}),
        _rec(7, "tool/call", {"turn": 1, "step": 2, "callId": "c2", "name": "pwsh",
                              "arguments": json.dumps({"command": "pytest -q"})}),
        _rec(8, "tool/result", {"turn": 1, "step": 2,
                                "message": {"source": {"kind": "tool", "callId": "c2"},
                                            "content": [{"type": "text", "toolCallId": "c2",
                                                         "content": [{"type": "text", "text": "2 passed"}],
                                                         "isError": False}]}}),
        _rec(9, "step/end", {"turn": 1, "step": 2}),
        _rec(10, "turn/end", {"turn": 1}),
        # 噪声：思考链必须被排除
        _rec(11, "reasoning-chunks", {"chunk": "thinking..."}),
    ]
    _mk_session(str(root), recs)
    return str(tmp_path / "sessions")


# ── 解析器 ──

def test_parser_has_all_subcommands():
    p = build_parser()
    acts = [a for a in p._actions if a.dest == "command"]
    assert acts, "应有子命令"
    assert set(acts[0].choices) == {"probe", "extract", "ingest", "eval", "inspect"}


def test_main_skips_leading_learn_token(monkeypatch, fake_lib, tmp_path):
    """console_scripts 形态「yunshu learn probe …」的 argv 首位是 learn。"""
    out = tmp_path / "r.json"
    rc = main(["learn", "probe", fake_lib, "--json-out", str(out)])
    assert rc == 0
    assert out.is_file()


# ── probe ──

def test_probe_end_to_end(fake_lib, tmp_path):
    out = tmp_path / "r.md"
    js = tmp_path / "r.json"
    rc = main(["probe", fake_lib, "--out", str(out), "--json-out", str(js)])
    assert rc == 0
    rep = json.loads(js.read_text(encoding="utf-8"))
    assert rep["meta"]["files"] == 1
    # 多帧被完整读出：12 条记录 = session 头 + 11 条 _rec（只解首帧则仅 1 条）
    assert rep["corpus"]["records"] == 12, "多帧 zstd 必须被完整解出"
    assert rep["pairing"]["matched"] == 2
    assert rep["failure"]["isError"] == 0
    assert rep["event_types"]["total_distinct"] >= 5
    assert "reasoning-chunks" in dict(rep["event_types"]["counts"])
    assert "P0 Schema 报告" in out.read_text(encoding="utf-8")


def test_probe_rejects_missing_dir(tmp_path):
    assert main(["probe", str(tmp_path / "nope")]) == 2


# ── extract ──

def test_extract_end_to_end(fake_lib, tmp_path):
    out = tmp_path / "samples.ndjson"
    rej = tmp_path / "rejected.ndjson"
    rc = main(["extract", fake_lib, "--out", str(out), "--rejected", str(rej)])
    assert rc == 0
    rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 1
    s = rows[0]
    # 任务必须来自 kind=user 的消息，而不是 background job 样板
    assert "pytest" in s["task"] and "background job" not in s["task"]
    # 路径相对化（去用户名）
    assert s["diffs"][0]["path"] == "a.py"
    # 最后一条测试命令成功 ⇒ verified=pass
    assert s["verified"] == "pass"
    assert s["stack"]["lang"] == "python"
    assert s["task_type"] in ("bugfix", "test")


def test_extract_snapshot_filters_records(fake_lib, tmp_path):
    out = tmp_path / "s.ndjson"
    # 冻结到早于全部记录的时间 ⇒ 无 turn 可提取
    rc = main(["extract", fake_lib, "--out", str(out), "--snapshot-until", "2000-01-01T00:00:00"])
    assert rc == 0
    assert out.read_text(encoding="utf-8").strip() == ""


# ── 公共层 ──

def test_iter_sessions_finds_files(fake_lib):
    found = iter_sessions(fake_lib)
    assert len(found) == 1 and found[0].endswith("session.jsonl.zstd")


def test_desensitize_hard_block_and_replace():
    from agent.experience_cli._common import desensitize
    txt, blk, _ = desensitize("-----BEGIN RSA PRIVATE KEY-----")
    assert blk == "private_key_block"
    out, blk2, n = desensitize(r"看 C:\Users\somebody\agent\a.py 这个文件")
    assert blk2 is None and n >= 1 and "<PATH>" in out and "somebody" not in out
