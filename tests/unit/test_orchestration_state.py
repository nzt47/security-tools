"""E4-2③：跨轮次**显式**状态（state.json）单元测试

判据（docs/plan/2026-11-27-p4-编排安全设计.md §5 / §12.6）：
  - 只有显式 state.get/set/clear 才会读写；不自动注入（脚本上下文里没有"上次状态"）；
  - 按会话隔离（文件名是 sha256 摘要，绝不出现原始 session_id 路径）；
  - 没有会话绑定 ⇒ state_unavailable，不落共享默认键；
  - TTL 24h：过期读当不存在；大小上限 64KB：超限不写；
  - 失败都回稳定 code，不抛异常。
"""
from __future__ import annotations

import json
import os
import time

import pytest

from agent.orchestration import state


@pytest.fixture
def root(tmp_path):
    return tmp_path / "orchestration"


class TestRoundTrip:
    def test_写了能读_值原样(self, root):
        assert state.write("sess-a", {"step": 2, "notes": ["x"]}, root)["ok"] is True
        got = state.read("sess-a", root)
        assert got["ok"] is True
        assert got["value"] == {"step": 2, "notes": ["x"]}

    def test_没写过读到_none(self, root):
        assert state.read("sess-a", root) == {"ok": True, "value": None}

    def test_显式清空(self, root):
        state.write("sess-a", 1, root)
        assert state.clear("sess-a", root) == {"ok": True, "cleared": True}
        assert state.read("sess-a", root)["value"] is None
        assert state.clear("sess-a", root) == {"ok": True, "cleared": False}

    def test_覆盖写(self, root):
        state.write("sess-a", {"n": 1}, root)
        state.write("sess-a", {"n": 2}, root)
        assert state.read("sess-a", root)["value"] == {"n": 2}

    def test_文件形状带版本与时间戳(self, root):
        state.write("sess-a", {"n": 1}, root)
        path = state.state_path("sess-a", root)
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["v"] == state.STATE_VERSION
        assert raw["ts"] and raw["value"] == {"n": 1}


class TestIsolation:
    def test_会话之间互不可见(self, root):
        state.write("sess-a", "A", root)
        state.write("sess-b", "B", root)
        assert state.read("sess-a", root)["value"] == "A"
        assert state.read("sess-b", root)["value"] == "B"

    def test_文件名是摘要_不含原始会话(self, root):
        state.write("tenant/../../evil", {"x": 1}, root)
        files = list((root / state.STATE_DIR_NAME).glob("*.json"))
        assert len(files) == 1
        assert "evil" not in files[0].name and "tenant" not in files[0].name
        assert len(files[0].stem) == 16

    def test_没有会话回unavailable_不落盘(self, root):
        for sid in ("", "   "):
            assert state.write(sid, 1, root)["code"] == "state_unavailable"
            assert state.read(sid, root)["code"] == "state_unavailable"
            assert state.clear(sid, root)["code"] == "state_unavailable"
        assert not root.exists() or not list(root.rglob("*.json"))


class TestLimits:
    def test_超过_64kb_不写(self, root):
        result = state.write("sess-a", "x" * (state.MAX_STATE_BYTES + 1), root)
        assert result["ok"] is False and result["code"] == "state_too_large"
        assert result["limit"] == state.MAX_STATE_BYTES
        assert not state.state_path("sess-a", root).exists()

    def test_刚好在上限内可写(self, root):
        # JSON 序列化后 = 2 字节引号 + 内容；留出余量
        payload = "x" * (state.MAX_STATE_BYTES - 10)
        assert state.write("sess-a", payload, root)["ok"] is True

    def test_ttl_过期读当不存在并删除(self, root):
        state.write("sess-a", {"n": 1}, root)
        path = state.state_path("sess-a", root)
        old = time.time() - state.STATE_TTL_SEC - 60
        os.utime(path, (old, old))
        assert state.read("sess-a", root)["value"] is None
        assert not path.exists()

    def test_ttl_未过期照常读(self, root):
        state.write("sess-a", {"n": 1}, root)
        path = state.state_path("sess-a", root)
        recent = time.time() - state.STATE_TTL_SEC + 60
        os.utime(path, (recent, recent))
        assert state.read("sess-a", root)["value"] == {"n": 1}


class TestProtocol:
    def test_apply_三种操作(self, root):
        assert state.apply("set", {"n": 1}, session_id="s", root=root)["ok"] is True
        assert state.apply("get", None, session_id="s", root=root)["value"] == {"n": 1}
        assert state.apply("clear", None, session_id="s", root=root)["cleared"] is True

    def test_未知操作回_bad_request(self, root):
        assert state.apply("drop", None, session_id="s", root=root)["code"] == "bad_request"

    def test_坏文件当不存在(self, root):
        path = state.state_path("s", root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json}", encoding="utf-8")
        got = state.read("s", root)
        assert got["ok"] is True and got["value"] is None
