"""E1-4b 会话消息索引（FTS5 trigram）：写入/检索/短查询回落/重建/开关。

全部用临时库（`CP_SESSION_INDEX_PATH` 指到 tmp），不碰真实 `data/session_index.db`。
"""
from __future__ import annotations

import json

import pytest

from agent.context import session_index as S


@pytest.fixture(autouse=True)
def _db(monkeypatch, tmp_path):
    monkeypatch.delenv(S.ENABLED_ENV, raising=False)
    monkeypatch.setenv("CP_SESSION_INDEX_PATH", str(tmp_path / "idx.db"))
    yield


# ── 纯函数 ──────────────────────────────────────────────────


def test_normalize_query_压平并去语法字符():
    assert S.normalize_query('  检查点  "变更"*  ') == "检查点 变更"


def test_normalize_query_截断():
    assert len(S.normalize_query("长" * 500)) == S.MAX_QUERY_CHARS


def test_enabled_开关(monkeypatch):
    monkeypatch.setenv(S.ENABLED_ENV, "0")
    assert S.enabled() is False


# ── 写入 + 检索 ─────────────────────────────────────────────


def test_写入并检索中文子串():
    assert S.index_message("s1", "user", "帮我看看检查点与变更文件卡") is True
    out = S.search("检查点")
    assert out["ok"] is True and out["mode"] == "match"
    assert len(out["hits"]) == 1
    hit = out["hits"][0]
    assert hit["session_id"] == "s1" and hit["role"] == "user"
    assert "检查点" in hit["snippet"]


def test_同分时新的在前():
    """两个会话内容完全一样 ⇒ rank 相同；必须让**后写入**的排在前面。

    真机踩过（2026-11-26 E1-4d 探针）：同一关键词重复跑过多轮后，旧的同分命中占满
    limit=5，刚写的消息搜不到 —— 回忆场景语义上应该"新的优先"。
    """
    S.index_message("sess_old", "user", "同一句话：检查点口径")
    S.index_message("sess_new", "user", "同一句话：检查点口径")
    hits = S.search("检查点口径", limit=2)["hits"]
    assert [h["session_id"] for h in hits][:2] == ["sess_new", "sess_old"]


def test_检索未命中返回空():
    S.index_message("s1", "user", "完全无关的内容")
    out = S.search("检查点")
    assert out["ok"] is True and out["hits"] == []


def test_短查询回落_LIKE():
    S.index_message("s1", "user", "关于知识库的回答")
    out = S.search("知识")
    assert out["mode"] == "like"
    assert out["hits"] and "知识" in out["hits"][0]["snippet"]


def test_空查询不报错也不返回():
    assert S.search("   ")["hits"] == []


def test_seq_自增且同_seq_覆盖():
    S.index_message("s1", "user", "第一条")
    S.index_message("s1", "assistant", "第二条")
    S.index_message("s1", "assistant", "第二条改", seq=1)
    out = S.search("第二条")
    # snippet 会在命中词两侧插高亮标记 ⇒ 断言"同一行 + 覆盖后的尾字在里面"
    assert len(out["hits"]) == 1
    plain = out["hits"][0]["snippet"].replace("[", "").replace("]", "")
    assert plain == "第二条改"


def test_limit_上限被钳制():
    for i in range(60):
        S.index_message("s-big", "user", "关键内容 %d" % i)
    out = S.search("关键内容", limit=999)
    assert len(out["hits"]) == S.MAX_LIMIT


def test_关闭后不写不搜(monkeypatch):
    monkeypatch.setenv(S.ENABLED_ENV, "0")
    assert S.index_message("s1", "user", "检查点") is False
    assert S.search("检查点")["ok"] is False


def test_空内容不写():
    assert S.index_message("s1", "user", "   ") is False


# ── 清理 / 重建 / 读数 ─────────────────────────────────────


def test_clear_按会话():
    S.index_message("s1", "user", "甲")
    S.index_message("s2", "user", "乙")
    assert S.clear("s1") is True
    assert S.search("甲")["hits"] == [] and len(S.search("乙")["hits"]) == 1


def test_rebuild_从_messages_jsonl(tmp_path):
    base = tmp_path / "sessions"
    (base / "sess_a").mkdir(parents=True)
    rows = [{"role": "user", "content": "重建索引的第一条", "timestamp": "t1"},
            {"role": "assistant", "content": "重建索引的第二条", "timestamp": "t2"}]
    (base / "sess_a" / "messages.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    out = S.rebuild(base)
    assert out["ok"] is True and out["sessions"] == 1 and out["messages"] == 2
    assert S.search("重建索引")["hits"]


def test_rebuild_目录不存在返回错误(tmp_path):
    out = S.rebuild(tmp_path / "nope")
    assert out["ok"] is False and "不存在" in out["error"]


def test_stats_读数():
    S.index_message("s1", "user", "检查点")
    info = S.stats()
    assert info["messages"] == 1 and info["sessions"] == 1
    assert info["size_bytes"] > 0 and info["enabled"] is True
