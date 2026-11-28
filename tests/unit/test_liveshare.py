"""E4-5 第六批：LiveShare 核心（令牌只存 sha256 / TTL / 撤销 / 只读投影）。"""
from __future__ import annotations

import json

import pytest

from agent import liveshare


@pytest.fixture(autouse=True)
def _tmp_root(monkeypatch, tmp_path):
    monkeypatch.setattr(liveshare, "SHARES_DIR", tmp_path / "liveshare")
    return tmp_path / "liveshare"


class TestCreate:
    def test_明文只回一次_盘上只有摘要(self, _tmp_root):
        created = liveshare.create_share("sess_a", ttl_seconds=600, now=1_000_000)
        assert len(created["token"]) >= 32
        raw = (_tmp_root / liveshare.SHARES_FILE).read_text(encoding="utf-8")
        assert created["token"] not in raw, "盘上绝不能出现明文令牌"
        assert liveshare.token_digest(created["token"]) in raw
        assert created["ttl_seconds"] == 600
        assert created["expires_at"] == "1970-01-12T13:56:40Z"

    def test_每个令牌唯一(self):
        a = liveshare.create_share("sess_a")
        b = liveshare.create_share("sess_a")
        assert a["token"] != b["token"]

    def test_非法会话_id_拒绝(self):
        with pytest.raises(ValueError):
            liveshare.create_share("../etc/passwd")


class TestTtl:
    @pytest.mark.parametrize("value,expected", [
        (None, liveshare.DEFAULT_TTL_SECONDS),
        ("bogus", liveshare.DEFAULT_TTL_SECONDS),
        (1, liveshare.MIN_TTL_SECONDS),
        (10 ** 9, liveshare.MAX_TTL_SECONDS),
        (600, 600),
    ])
    def test_夹取(self, value, expected):
        assert liveshare.clamp_ttl(value) == expected


class TestResolve:
    def test_有效令牌(self):
        token = liveshare.create_share("sess_a", ttl_seconds=600, now=1_000_000)["token"]
        record = liveshare.resolve(token, now=1_000_001)
        assert record and record["session_id"] == "sess_a"

    @pytest.mark.parametrize("token", ["", "short", "x" * 20])
    def test_无效令牌(self, token):
        assert liveshare.resolve(token) is None

    def test_过期即失效(self):
        token = liveshare.create_share("sess_a", ttl_seconds=600, now=1_000_000)["token"]
        assert liveshare.resolve(token, now=1_000_599) is not None
        assert liveshare.resolve(token, now=1_000_601) is None


class TestRevokeAndList:
    def test_撤销后失效(self):
        token = liveshare.create_share("sess_a", now=1_000_000)["token"]
        assert liveshare.revoke_session("sess_a", now=1_000_100) == 1
        assert liveshare.resolve(token, now=1_000_200) is None
        assert liveshare.revoke_session("sess_a", now=1_000_300) == 0, "重复撤销不重复计数"

    def test_状态不含令牌与摘要(self):
        liveshare.create_share("sess_a", ttl_seconds=600, now=1_000_000)
        liveshare.create_share("sess_b", ttl_seconds=600, now=1_000_000)
        rows = liveshare.list_shares("sess_a", now=1_000_100)
        assert len(rows) == 1
        keys = set(rows[0])
        assert keys == {"created_at", "expires_at", "revoked", "active"}
        assert rows[0]["active"] is True
        expired = liveshare.list_shares("sess_a", now=1_000_999)
        assert expired[0]["active"] is False

    def test_清理超保留期(self):
        liveshare.create_share("sess_a", ttl_seconds=300, now=1_000_000)
        # 过期超过保留期（7 天）后才清
        assert liveshare.prune(now=1_000_000 + 300 + 10) == 0
        assert liveshare.prune(now=1_000_000 + 300 + liveshare.RETENTION_SECONDS + 10) == 1


class TestProject:
    MESSAGES = [
        {"role": "user", "content": "第一问", "timestamp": "t1", "tool_calls": ["x"]},
        {"role": "assistant", "content": "第一答", "timestamp": "t2", "steps": [{"a": 1}]},
        {"role": "user", "content": "x" * 50, "timestamp": "t3"},
    ]

    def test_字段白名单与序号(self):
        body = liveshare.project(self.MESSAGES, after=0, limit=2)
        assert [row["seq"] for row in body["messages"]] == [1, 2]
        assert set(body["messages"][0]) == {"seq", "role", "content", "chars",
                                            "truncated", "timestamp"}
        assert "tool_calls" not in json.dumps(body) and "steps" not in json.dumps(body)
        assert body["total"] == 3 and body["next_after"] == 2 and body["truncated"] is True

    def test_游标增量(self):
        body = liveshare.project(self.MESSAGES, after=2, limit=10)
        assert [row["seq"] for row in body["messages"]] == [3]
        assert body["next_after"] == 3 and body["truncated"] is False

    def test_正文截断(self, monkeypatch):
        monkeypatch.setattr(liveshare, "MAX_CONTENT_CHARS", 5)
        body = liveshare.project(self.MESSAGES, after=2)
        assert body["messages"][0]["content"] == "xxxxx"
        assert body["messages"][0]["chars"] == 50 and body["messages"][0]["truncated"] is True

    def test_limit_夹取(self):
        assert liveshare.project(self.MESSAGES, limit=0)["limit"] == 1
        assert liveshare.project(self.MESSAGES, limit=999)["limit"] == liveshare.MAX_PAGE
        assert liveshare.project(self.MESSAGES, after=99)["messages"] == []
