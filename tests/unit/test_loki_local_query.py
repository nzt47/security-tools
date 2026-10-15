# -*- coding: utf-8 -*-
"""本地日志回退查询口径（agent/monitoring/loki.py::_query_local_logs）。

锁死 2026-10-15 修复的三处实测缺陷：
  1. 无 query 时路由传 ".+"，旧实现按字面子串匹配 ⇒ 永远 0 条；
  2. level=INFO / service=xxx 被当整串子串 ⇒ 永远不中；
  3. 默认窗口只扫最近 1 小时，而日志文件最新常是两天前 ⇒ 永远 0 条。

用 tmp_path 造 jsonl，直接调 _query_local_logs（不经过网络 / Loki）。
"""
import json
import time
from datetime import datetime

from agent.monitoring import loki


def _client(tmp_path):
    """只造出带 _local_log_dir 的实例，避开构造函数里的网络/配置读取。"""
    client = loki.LokiClient.__new__(loki.LokiClient)
    client._local_log_dir = str(tmp_path)
    return client


def _write(tmp_path, date_str, entries):
    path = tmp_path / (date_str + ".jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path


def _entry(ts, level=None, message="m", app="yunshu-route"):
    labels = {"app": app}
    if level:
        labels["level"] = level
    return {"timestamp": ts, "labels": labels, "message": message}


def _today():
    return datetime.now().strftime("%Y-%m-%d")


class TestParseLocalQuery:
    def test_empty_and_wildcard_are_match_all(self):
        for q in (None, "", "   ", ".+", ".*"):
            assert loki._parse_local_query(q) == ({}, [])

    def test_level_field_filter(self):
        fields, terms = loki._parse_local_query("level=INFO")
        assert fields == {"level": "INFO"}
        assert terms == []

    def test_query_plus_level(self):
        fields, terms = loki._parse_local_query("error level=ERROR")
        assert fields == {"level": "ERROR"}
        assert terms == ["error"]

    def test_service_field_filter(self):
        fields, _ = loki._parse_local_query("service=agent-config")
        assert fields == {"service": "agent-config"}

    def test_pipe_filter_is_text_term(self):
        fields, terms = loki._parse_local_query("boom |~ 'ERROR'")
        assert fields == {}
        assert set(terms) == {"boom", "ERROR"}


class TestQueryLocalLogs:
    def test_dot_plus_matches_all(self, tmp_path):
        """修复 ①：无 query 时的 ".+" 必须全匹配，而不是字面子串。"""
        now = time.time()
        _write(tmp_path, _today(), [
            _entry(now - 60, "INFO", "hello"),
            _entry(now - 120, "ERROR", "boom"),
        ])
        out = _client(tmp_path)._query_local_logs(".+")
        assert len(out) == 2
        assert all(e["source"] == "local" for e in out)

    def test_empty_query_matches_all(self, tmp_path):
        now = time.time()
        _write(tmp_path, _today(), [_entry(now - 60, "INFO", "hello")])
        assert len(_client(tmp_path)._query_local_logs("")) == 1

    def test_level_filter_hits_labels_not_literal_string(self, tmp_path):
        """修复 ②：level=INFO 是 labels.level 字段过滤。"""
        now = time.time()
        _write(tmp_path, _today(), [
            _entry(now - 60, "INFO", "ok"),
            _entry(now - 120, "ERROR", "boom"),
        ])
        assert [e["message"] for e in _client(tmp_path)._query_local_logs("level=INFO")] == ["ok"]
        assert [e["message"] for e in _client(tmp_path)._query_local_logs("level=ERROR")] == ["boom"]

    def test_keyword_query_still_substring(self, tmp_path):
        now = time.time()
        _write(tmp_path, _today(), [
            _entry(now - 60, "INFO", "semantic layer hit"),
            _entry(now - 120, "INFO", "unrelated"),
        ])
        out = _client(tmp_path)._query_local_logs("semantic")
        assert [e["message"] for e in out] == ["semantic layer hit"]

    def test_pipe_filter_matches_labels(self, tmp_path):
        """路由把 level 拼成 |~ 'ERROR'：对 message+labels 一起匹配才命得中。"""
        now = time.time()
        _write(tmp_path, _today(), [
            _entry(now - 60, "INFO", "ok"),
            _entry(now - 120, "ERROR", "boom"),
        ])
        out = _client(tmp_path)._query_local_logs("boom |~ 'ERROR'")
        assert [e["message"] for e in out] == ["boom"]

    def test_default_window_is_not_one_hour(self, tmp_path):
        """修复 ③：两天前的日志在默认窗口内；40 天前的不在。"""
        two_days = time.time() - 2 * 86400
        old = time.time() - 40 * 86400
        _write(tmp_path, datetime.fromtimestamp(two_days).strftime("%Y-%m-%d"),
               [_entry(two_days, "INFO", "two-days-ago")])
        _write(tmp_path, datetime.fromtimestamp(old).strftime("%Y-%m-%d"),
               [_entry(old, "INFO", "forty-days-ago")])
        out = _client(tmp_path)._query_local_logs("")
        assert [e["message"] for e in out] == ["two-days-ago"]

    def test_explicit_window_still_honored(self, tmp_path):
        """显式 start_time/end_time 仍按调用方给的窗口过滤（不被默认放宽覆盖）。"""
        now = time.time()
        _write(tmp_path, _today(), [_entry(now - 60, "INFO", "recent")])
        out = _client(tmp_path)._query_local_logs("", start_time=now - 10, end_time=now)
        assert out == []

    def test_meta_reports_scanned_window(self, tmp_path):
        now = time.time()
        _write(tmp_path, _today(), [_entry(now - 30, "INFO", "hi")])
        meta = {}
        _client(tmp_path)._query_local_logs(".+", meta=meta)
        assert meta["source"] == "local"
        assert meta["window_days"] == loki.DEFAULT_LOCAL_WINDOW_DAYS
        assert meta["matched"] == 1
        assert meta["files_scanned"] == [_today() + ".jsonl"]

    def test_non_date_jsonl_ignored(self, tmp_path):
        now = time.time()
        _write(tmp_path, "observability_access", [_entry(now - 30, "INFO", "not-a-day-file")])
        _write(tmp_path, _today(), [_entry(now - 30, "INFO", "real")])
        out = _client(tmp_path)._query_local_logs(".+")
        assert [e["message"] for e in out] == ["real"]

    def test_results_sorted_desc_and_limited(self, tmp_path):
        now = time.time()
        _write(tmp_path, _today(), [
            _entry(now - 10, "INFO", "a"),
            _entry(now - 20, "INFO", "b"),
            _entry(now - 30, "INFO", "c"),
        ])
        out = _client(tmp_path)._query_local_logs(".+", limit=2)
        assert [e["message"] for e in out] == ["a", "b"]
