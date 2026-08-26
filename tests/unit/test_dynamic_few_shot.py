"""Dynamic Few-shot 采样与脱敏单元测试

覆盖范围:
- record 仅记录成功调用(ok=True)、失败不记录
- 脱敏(password→********)、结构保留
- get_recent 窗口/limit、sample_for_tools 分组
- 截断后合法 JSON、禁用开关、单例、空表
- 数据库中无明文
"""
import json
import time
from unittest.mock import patch
import pytest

from agent.tool_fewshot_store import (
    ToolFewshotStore,
    FEWSHOT_PER_TOOL,
    FEWSHOT_WINDOW_DAYS,
)


@pytest.fixture(autouse=True)
def _reset_store():
    ToolFewshotStore.reset()
    yield
    ToolFewshotStore.reset()


@pytest.fixture
def store(tmp_path):
    return ToolFewshotStore(db_path=str(tmp_path / "test_fewshot.db"))


class TestRecord:
    """record 成功/失败判定"""

    def test_record_success(self, store):
        ok = store.record("web_search", {"query": "test"}, {"ok": True, "result": "data"})
        assert ok is True
        samples = store.get_recent("web_search")
        assert len(samples) == 1

    def test_record_failure_not_stored(self, store):
        ok = store.record("web_search", {"query": "test"}, {"ok": False, "error": "fail"})
        assert ok is False
        assert store.get_recent("web_search") == []

    def test_record_ok_string_true_not_stored(self, store):
        """ok 必须是布尔 True 才算成功(防御字符串 'true')"""
        store.record("web_search", {}, {"ok": "true"})
        assert store.get_recent("web_search") == []

    def test_record_disabled(self, store):
        with patch("agent.tool_fewshot_store.FEWSHOT_ENABLED", False):
            ok = store.record("web_search", {}, {"ok": True})
        assert ok is False
        assert store.get_recent("web_search") == []


class TestMasking:
    """脱敏"""

    def test_password_masked(self, store):
        store.record("tool_a", {"password": "supersecret"}, {"ok": True})
        samples = store.get_recent("tool_a")
        assert samples[0]["input"]["password"] != "supersecret"

    def test_phone_masked(self, store):
        store.record("tool_a", {"phone": "13800138000"}, {"ok": True})
        samples = store.get_recent("tool_a")
        assert samples[0]["input"]["phone"] != "13800138000"

    def test_structure_preserved(self, store):
        """脱敏不破坏结构:字段键保留,仅值被替换"""
        store.record("tool_a", {"query": "hello", "password": "secret"}, {"ok": True})
        samples = store.get_recent("tool_a")
        assert set(samples[0]["input"].keys()) == {"query", "password"}

    def test_no_plaintext_in_db(self, store, tmp_path):
        store.record("tool_a", {"password": "secret123"}, {"ok": True})
        raw = (tmp_path / "test_fewshot.db").read_text(encoding="utf-8", errors="ignore")
        assert "secret123" not in raw


class TestTruncation:
    """截断后仍为合法 JSON"""

    def test_large_output_truncated_valid_json(self, store):
        big = {"data": "x" * 100000}
        store.record("tool_a", {"q": "1"}, {"ok": True, "data": big})
        samples = store.get_recent("tool_a")
        assert len(samples) == 1
        out = samples[0]["output"]
        assert isinstance(out, dict)
        assert out.get("_truncated") is True or "ok" in out

    def test_normal_size_not_truncated(self, store):
        store.record("tool_a", {"q": "1"}, {"ok": True, "data": "small"})
        samples = store.get_recent("tool_a")
        assert samples[0]["output"]["data"] == "small"


class TestQuery:
    """窗口与 limit"""

    def test_window_days_filter(self, store):
        old_ts = time.time() - (FEWSHOT_WINDOW_DAYS + 1) * 86400
        with patch("agent.tool_fewshot_store.time.time", return_value=old_ts):
            store.record("tool_a", {"q": "old"}, {"ok": True})
        store.record("tool_a", {"q": "new"}, {"ok": True})
        samples = store.get_recent("tool_a")
        assert len(samples) == 1
        assert samples[0]["input"]["q"] == "new"

    def test_limit_respected(self, store):
        for i in range(5):
            store.record("tool_a", {"q": "q%d" % i}, {"ok": True})
        samples = store.get_recent("tool_a", limit=2)
        assert len(samples) == 2
        # 倒序:最新在前
        assert samples[0]["input"]["q"] == "q4"

    def test_get_recent_returns_empty_for_unknown(self, store):
        assert store.get_recent("unknown_tool") == []


class TestSampleForTools:
    """批量采样"""

    def test_sample_groups_by_tool(self, store):
        store.record("tool_a", {"q": "1"}, {"ok": True})
        store.record("tool_b", {"q": "2"}, {"ok": True})
        result = store.sample_for_tools(["tool_a", "tool_b", "tool_c"])
        assert "tool_a" in result and len(result["tool_a"]) == 1
        assert "tool_b" in result and len(result["tool_b"]) == 1
        assert "tool_c" not in result

    def test_sample_empty_whitelist(self, store):
        assert store.sample_for_tools([]) == {}

    def test_sample_per_tool_limit(self, store):
        for i in range(5):
            store.record("tool_a", {"q": "q%d" % i}, {"ok": True})
        result = store.sample_for_tools(["tool_a"])
        assert len(result["tool_a"]) == FEWSHOT_PER_TOOL


class TestSingletonAndMaintenance:
    """单例与维护"""

    def test_singleton(self):
        s1 = ToolFewshotStore.instance()
        s2 = ToolFewshotStore.instance()
        assert s1 is s2

    def test_clear(self, store):
        store.record("tool_a", {"q": "1"}, {"ok": True})
        store.clear()
        assert store.get_recent("tool_a") == []

    def test_cleanup_expired(self, store):
        old_ts = time.time() - 30 * 86400  # 30 天前
        with patch("agent.tool_fewshot_store.time.time", return_value=old_ts):
            store.record("tool_a", {"q": "old"}, {"ok": True})
        store.record("tool_a", {"q": "new"}, {"ok": True})
        store.cleanup_expired(window_days=7)
        samples = store.get_recent("tool_a")
        assert len(samples) == 1
        assert samples[0]["input"]["q"] == "new"

    def test_record_exception_degraded(self, store):
        with patch.object(store, "_get_conn", side_effect=RuntimeError("db broken")):
            ok = store.record("tool_a", {"q": "1"}, {"ok": True})
        assert ok is False
