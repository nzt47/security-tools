"""AuditLogger 审计日志全面单元测试

测试目标：覆盖 agent/audit/logger.py 的所有分支
覆盖维度：
1. 正常路径：log 记录、query 查询
2. 边界条件：空输入、空输出、metadata 为 None
3. 哈希计算：_hash 方法
4. 全局单例：audit_logger
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.audit.logger import AuditLogger, audit_logger


@pytest.fixture
def audit(tmp_path):
    """独立审计日志器（使用临时目录）"""
    return AuditLogger(log_dir=str(tmp_path))


# ── 1. 初始化 ──────────────────────────────────────────


class TestInit:
    def test_creates_log_dir(self, tmp_path):
        log_dir = tmp_path / "nested" / "audit"
        al = AuditLogger(log_dir=str(log_dir))
        assert log_dir.exists()

    def test_current_file_pattern(self, audit):
        """当前文件应按日期命名"""
        assert "audit_" in audit._current_file.name
        assert audit._current_file.name.endswith(".jsonl")

    def test_default_log_dir(self):
        al = AuditLogger()
        assert "audit" in str(al._log_dir).lower() or "data" in str(al._log_dir)


# ── 2. log 记录 ──────────────────────────────────────────


class TestLog:
    def test_log_basic(self, audit):
        audit.log("test_action")
        # 文件应存在并有内容
        assert audit._current_file.exists()
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["action"] == "test_action"
        assert record["status"] == "success"

    def test_log_with_input_output(self, audit):
        audit.log("action", input_data="input", output_data="output")
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["input_hash"]  # 非空
        assert record["output_hash"]

    def test_log_empty_input_output(self, audit):
        """空输入输出应记录空字符串哈希字段"""
        audit.log("action", input_data="", output_data="")
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["input_hash"] == ""
        assert record["output_hash"] == ""

    def test_log_with_metadata(self, audit):
        audit.log("action", metadata={"key": "value"})
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["metadata"] == {"key": "value"}

    def test_log_metadata_none_defaults_to_empty(self, audit):
        audit.log("action")
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["metadata"] == {}

    def test_log_with_status(self, audit):
        audit.log("action", status="failure")
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["status"] == "failure"

    def test_log_includes_timestamp(self, audit):
        audit.log("action")
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert "timestamp" in record
        assert "T" in record["timestamp"]  # ISO 格式

    def test_log_includes_trace_id(self, audit):
        audit.log("action")
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert "trace_id" in record

    def test_log_includes_stack_depth(self, audit):
        audit.log("action")
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["stack_depth"] > 0

    def test_log_appends_multiple(self, audit):
        """多次 log 应追加到同一文件"""
        audit.log("action1")
        audit.log("action2")
        audit.log("action3")
        lines = audit._current_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3


# ── 3. _hash 哈希计算 ──────────────────────────────────


class TestHash:
    def test_hash_returns_string(self, audit):
        h = audit._hash("test")
        assert isinstance(h, str)

    def test_hash_length_16(self, audit):
        """哈希应为 16 字符（sha256 前 16 位）"""
        h = audit._hash("test")
        assert len(h) == 16

    def test_hash_deterministic(self, audit):
        """相同输入应产生相同哈希"""
        h1 = audit._hash("test")
        h2 = audit._hash("test")
        assert h1 == h2

    def test_hash_different_inputs_different(self, audit):
        h1 = audit._hash("input1")
        h2 = audit._hash("input2")
        assert h1 != h2

    def test_hash_hex_chars(self, audit):
        h = audit._hash("test")
        assert all(c in "0123456789abcdef" for c in h)


# ── 4. query 查询 ──────────────────────────────────────────


class TestQuery:
    def test_query_empty_returns_empty(self, audit):
        results = audit.query()
        assert results == []

    def test_query_returns_all(self, audit):
        audit.log("action1")
        audit.log("action2")
        results = audit.query()
        assert len(results) == 2

    def test_query_by_action(self, audit):
        audit.log("action_a")
        audit.log("action_b")
        audit.log("action_a")
        results = audit.query(action="action_a")
        assert len(results) == 2
        assert all(r["action"] == "action_a" for r in results)

    def test_query_by_trace_id(self, audit):
        with patch("agent.audit.logger.get_trace_id", return_value="trace_123"):
            audit.log("action1")
        with patch("agent.audit.logger.get_trace_id", return_value="trace_456"):
            audit.log("action2")
        results = audit.query(trace_id="trace_123")
        assert len(results) == 1
        assert results[0]["trace_id"] == "trace_123"

    def test_query_limit(self, audit):
        for i in range(10):
            audit.log(f"action_{i}")
        results = audit.query(limit=5)
        assert len(results) == 5

    def test_query_filter_combined(self, audit):
        with patch("agent.audit.logger.get_trace_id", return_value="trace_x"):
            audit.log("target_action")
            audit.log("other_action")
        results = audit.query(trace_id="trace_x", action="target_action")
        assert len(results) == 1


# ── 5. 全局单例 ──────────────────────────────────────────


class TestGlobalInstance:
    def test_audit_logger_is_instance(self):
        assert isinstance(audit_logger, AuditLogger)

    def test_audit_logger_can_log(self):
        audit_logger.log("global_test_action")
        # 不抛异常即通过


# ── 6. 集成场景 ──────────────────────────────────────────


class TestIntegration:
    def test_log_then_query_roundtrip(self, audit):
        audit.log("login", input_data="user=admin", status="success")
        audit.log("logout", input_data="user=admin", status="success")
        results = audit.query(action="login")
        assert len(results) == 1
        assert results[0]["input_hash"]

    def test_multiple_log_files(self, tmp_path):
        """跨日志文件查询"""
        al1 = AuditLogger(log_dir=str(tmp_path))
        # 模拟不同日期的文件
        al1._current_file = tmp_path / "audit_20260101.jsonl"
        al1.log("old_action")
        al2 = AuditLogger(log_dir=str(tmp_path))
        al2._current_file = tmp_path / "audit_20260102.jsonl"
        al2.log("new_action")
        # 查询应跨文件
        results = AuditLogger(log_dir=str(tmp_path)).query()
        assert len(results) >= 2
