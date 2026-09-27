# -*- coding: utf-8 -*-
"""yunshu learn CLI 单测（方案交付物 A）。

覆盖：解析器结构 / 「yunshu learn」token 跳过 / 多帧 zstd 读取 /
probe 与 extract 端到端（用合成会话，不依赖真实会话库）。
"""
from __future__ import annotations

import datetime
import json
import os
from datetime import timezone

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


#: 真实 DSH 会话里 time 是 **epoch 毫秒整数**（实测 1789200668629），不是 ISO 字符串。
#: 夹具必须照抄真实形态 —— 此前夹具写成 ISO 字符串，掩盖了 ts_le 对真实会话恒真的缺陷。
_T2026_09_20 = int(datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)


def _rec(seq, typ, data, time=_T2026_09_20, **kw):
    """time=None 表示**该记录没有 time 字段** —— 真实 `turn/start` 正是如此。"""
    o = {"type": typ, "seq": seq, "data": data}
    if time is not None:
        o["time"] = time
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
        # 真实 turn/start **没有 time 字段**（实测），夹具必须照抄，否则 _first_ts 分支测不到
        _rec(3, "turn/start", {"turn": 1}, time=None),
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
        # 噪声：思考链必须被排除（且它同样没有 time —— 与真实 *-chunks 一致）
        _rec(11, "reasoning-chunks", {"chunk": "thinking..."}, time=None),
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

# ── 同任务去重（实测 505 条语料中 21 条为真重复）──

def _s(sid, task, paths, n_pitfalls=0, seq=1):
    return {"id": sid, "task": task, "source": {"seq_from": seq},
            "diffs": [{"path": p} for p in paths],
            "pitfalls": [{"symptom": "x"}] * n_pitfalls,
            "signal": {}, "verified": "pass"}


def test_task_fingerprint_erases_uuid_and_whitespace():
    from agent.experience_cli.extract import task_fingerprint
    a = task_fingerprint("Background subagent c0ab7562-04be-4aee-9af2-c16cb6f7ef27 finished\n\n  hi")
    b = task_fingerprint("Background subagent 11111111-2222-3333-4444-555555555555 finished hi")
    assert a == b, "UUID 与空白必须被抹平，否则同一句指令在不同会话里指纹不同"


def test_dedup_merges_only_same_task_and_same_files():
    from agent.experience_cli.extract import dedup_samples
    stat = {}
    rows = [
        _s("a", "重构检索层", ["x.py"]),               # 与 b 同任务同文件 ⇒ 合并
        _s("b", "重构检索层", ["x.py"], n_pitfalls=2),  # 信息量更大 ⇒ 保留
        _s("c", "重构检索层", ["y.py"]),               # 同任务**不同文件** ⇒ 必须保留
    ]
    out = dedup_samples(rows, stat)
    ids = [r["id"] for r in out]
    assert ids == ["b", "c"], "同任务不同文件是两件不同的工作，不能压掉"
    assert out[0]["signal"]["dup_samples"] == 2, "合并条数必须可观测，不静默丢数据"
    assert stat["merged_dup_samples"] == 1


def test_dedup_keeps_first_when_equally_informative():
    from agent.experience_cli.extract import dedup_samples
    stat = {}
    rows = [_s("a", "同一件事", ["x.py"], seq=1), _s("b", "同一件事", ["x.py"], seq=2)]
    out = dedup_samples(rows, stat)
    assert [r["id"] for r in out] == ["a"]
    assert out[0]["signal"]["dup_samples"] == 2

# ── 时间戳形态（真实 schema 是 epoch 毫秒整数，不是 ISO 字符串）──

def test_snapshot_filter_works_on_real_epoch_ms():
    """回归锁：DSH 记录的 time 是 int 毫秒。旧实现 isinstance(ts, str) 判假 ⇒
    --snapshot-until 恒为空操作，"冻结后结果可复现"实为不成立。
    """
    from agent.experience_cli._common import ts_le
    assert ts_le(_T2026_09_20, "2027-01-01T00:00:00") is True
    assert ts_le(_T2026_09_20, "2020-01-01T00:00:00") is False, "毫秒整数必须能被截止点挡住"
    # 秒级整数（另一种可能的形态）同样要正确
    assert ts_le(_T2026_09_20 // 1000, "2020-01-01T00:00:00") is False
    # ISO 字符串仍需兼容（旧夹具形态）
    assert ts_le("2026-09-20T10:00:00", "2020-01-01T00:00:00") is False
    # 无法解析时不筛（宁可多收，不可静默丢历史）
    assert ts_le("not-a-time", "2020-01-01T00:00:00") is True
    assert ts_le(None, "2020-01-01T00:00:00") is True


def test_ms_to_iso_roundtrip_and_failure():
    from agent.experience_cli._common import ms_to_iso
    got = ms_to_iso(_T2026_09_20)
    assert got and got.startswith("2026-09-20T"), got
    assert ms_to_iso(None) is None
    assert ms_to_iso("garbage") is None


def test_extract_created_at_is_session_time_not_extraction_time(fake_lib, tmp_path):
    """created_at 必须是「经验发生的时间」，否则来源日期列无信息量、过期判定失效。

    【必须覆盖 turn 首条记录无 time 的情形】真实 `turn/start` **没有 time 字段**，
    只取 recs[0] 会静默落到"当前时刻"兜底 —— 该缺陷在重抽 484 条后被打印
    created_at 月份分布才发现，仅凭"等于自己"的断言看不出来。
    """
    out = tmp_path / "s.ndjson"
    assert main(["extract", fake_lib, "--out", str(out)]) == 0
    s = json.loads(out.read_text(encoding="utf-8").strip().splitlines()[0])
    assert s["created_at"].startswith("2026-09-20T"), s["created_at"]
    # 抽取时间另存，不与被抽取内容混为一谈
    assert s["extracted_at"] and not s["extracted_at"].startswith("2026-09-20T")


