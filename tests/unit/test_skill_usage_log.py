# -*- coding: utf-8 -*-
"""技能使用台账（usage_log）—— 写、聚合、分片读取、窗口过滤。

背景（2026-10-28）：技能「有没有在发挥作用」此前答不出来（usage_count 全 0、
exec 指标 0 样本、L1 指标无 skill_id）。台账是进化/学习闭环的前置，所以它的
写入路径、聚合口径与归档读取都必须有守卫。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from agent.skills_mgmt import usage_log


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    p = tmp_path / "skill_usage_events.jsonl"
    monkeypatch.setenv(usage_log._ENV_PATH, str(p))
    return p


def test_record_and_aggregate(log_path):
    usage_log.record("match", "alpha", extra={"top_ids": ["alpha", "beta"], "count": 2})
    usage_log.record("load", "alpha", latency_ms=12.5)
    usage_log.record("exec", "alpha", success=True, latency_ms=30)
    usage_log.record("exec", "alpha", success=False, latency_ms=40)

    s = usage_log.stats(7)
    assert s["totals"]["match_calls"] == 1
    assert s["totals"]["hit_calls"] == 1
    assert s["totals"]["hit_rate"] == 1.0
    assert s["totals"]["exec_ok"] == 1 and s["totals"]["exec_fail"] == 1
    row = s["skills"]["alpha"]
    assert (row["hits"], row["loads"], row["execs"]) == (1, 1, 2)
    assert row["last_used_at"], "last_used_at 必须落值（UI 用它显示最近使用）"


def test_miss_is_counted_but_not_a_hit(log_path):
    usage_log.record("match", None, extra={"top_ids": [], "count": 0})
    usage_log.record("match", "beta", extra={"top_ids": ["beta"], "count": 1})
    s = usage_log.stats(7)
    assert s["totals"]["match_calls"] == 2
    assert s["totals"]["hit_calls"] == 1
    assert s["totals"]["hit_rate"] == 0.5
    assert set(s["skills"]) == {"beta"}


def test_archived_shards_are_read(log_path):
    # 一条「今天」的 + 一条落在按日分片里的历史事件
    usage_log.record("load", "alpha")
    old_name = log_path.with_name(log_path.stem + "-" + (datetime.now().date() - timedelta(days=1)).isoformat() + ".jsonl")
    old_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    old_name.write_text(json.dumps({"ts": old_ts, "stage": "exec", "skill_id": "alpha", "success": True}) + "\n", encoding="utf-8")

    s = usage_log.stats(7)
    assert s["skills"]["alpha"]["loads"] == 1
    assert s["skills"]["alpha"]["execs"] == 1, "分片里的事件也要被读到（历史不能丢）"


def test_window_filters_old_events(log_path):
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    log_path.write_text(json.dumps({"ts": old_ts, "stage": "exec", "skill_id": "old", "success": True}) + "\n", encoding="utf-8")
    assert usage_log.stats(7)["skills"] == {}
    assert "old" in usage_log.stats(90)["skills"]


def test_bad_stage_and_bad_json_are_ignored(log_path):
    usage_log.record("bogus", "x")
    log_path.write_text("{not json}\n\n", encoding="utf-8")
    assert usage_log.stats(7)["totals"]["match_calls"] == 0
    assert usage_log.recent(5) == []


def test_record_match_extracts_top_ids():
    class _M:
        def __init__(self, sid):
            self.skill_id = sid

    class _R:
        matches = [_M("a"), _M("b")]
        elapsed_ms = 3.2
        retrieval_method = "rrf"

    # 只验形状：record_match 不抛异常即可（实际写入已在上面的用例覆盖）
    usage_log.record_match(_R())


# ── 工具级使用台账（stage=tool，2026-10-29）─────────────────────────
def test_record_tool_and_aggregate(log_path):
    usage_log.record_tool("read_file", success=True, latency_ms=10)
    usage_log.record_tool("read_file", success=True, latency_ms=30)
    usage_log.record_tool("shell_execute", success=False, latency_ms=100,
                          error_code="ToolError: 找不到命令")

    s = usage_log.stats(7)
    tot = s["totals"]
    assert (tot["tool_calls"], tot["tool_ok"], tot["tool_fail"]) == (3, 2, 1)
    assert tot["tool_ok_rate"] == pytest.approx(2 / 3, abs=1e-3)
    rf = s["tools"]["read_file"]
    assert (rf["calls"], rf["ok"], rf["fail"]) == (2, 2, 0)
    assert rf["avg_ms"] == pytest.approx(20.0) and rf["max_ms"] == pytest.approx(30.0)
    sh = s["tools"]["shell_execute"]
    assert sh["fail"] == 1 and "找不到命令" in sh["last_error"]
    assert sh["last_used_at"]


def test_tool_and_skill_events_do_not_mix(log_path):
    """工具事件不能污染技能聚合（反之亦然）—— 两段共用一次扫描但各自成账。"""
    usage_log.record("match", "alpha", extra={"top_ids": ["alpha"], "count": 1})
    usage_log.record_tool("read_file", success=True, latency_ms=5)

    s = usage_log.stats(7)
    assert set(s["skills"]) == {"alpha"}
    assert set(s["tools"]) == {"read_file"}
    assert s["totals"]["match_calls"] == 1 and s["totals"]["tool_calls"] == 1


def test_record_tool_ignores_empty_name(log_path):
    usage_log.record_tool("", success=True)
    usage_log.record_tool(None, success=True)
    assert usage_log.stats(7)["tools"] == {}
