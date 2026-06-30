"""AuditLogger 结构化审计日志测试

测试 AuditLogger 的日志记录、查询、追加写入特性。
"""
import json
import os
import tempfile

import pytest

from agent.audit.logger import AuditLogger


# ═══════════════════════════════════════════════════════════════════
#  基础日志记录测试
# ═══════════════════════════════════════════════════════════════════

class TestAuditLog:
    """AuditLogger 基础日志功能"""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.logger = AuditLogger(log_dir=self.tmpdir)

    def test_log_creates_file(self):
        """记录日志后应生成对应的日志文件"""
        self.logger.log("test_action", "输入", "输出")
        files = list(self.logger._log_dir.glob("audit_*.jsonl"))
        assert len(files) == 1
        assert files[0].exists()

    def test_log_writes_valid_json(self):
        """写入的每行应是合法 JSON"""
        self.logger.log("action1", "data", "result", status="success")
        with open(self.logger._current_file, "r", encoding="utf-8") as f:
            line = f.readline().strip()
        record = json.loads(line)
        assert record["action"] == "action1"
        assert record["status"] == "success"

    def test_log_has_all_fields(self):
        """审计记录应包含全部必填字段"""
        self.logger.log("test", "input", "output", status="success", metadata={"key": "val"})
        with open(self.logger._current_file, "r", encoding="utf-8") as f:
            record = json.loads(f.readline().strip())
        assert "timestamp" in record
        assert "trace_id" in record
        assert "action" in record
        assert "input_hash" in record
        assert "output_hash" in record
        assert "stack_depth" in record
        assert "status" in record
        assert "metadata" in record
        assert record["metadata"] == {"key": "val"}

    def test_log_multiple_entries(self):
        """连续记录多条应全部写入"""
        for i in range(5):
            self.logger.log(f"action_{i}", f"in_{i}", f"out_{i}")
        with open(self.logger._current_file, "r", encoding="utf-8") as f:
            lines = [line for line in f if line.strip()]
        assert len(lines) == 5

    def test_input_hash(self):
        """输入数据应被正确哈希"""
        self.logger.log("test", "hello world", "")
        with open(self.logger._current_file, "r", encoding="utf-8") as f:
            record = json.loads(f.readline().strip())
        expected_hash = self.logger._hash("hello world")
        assert record["input_hash"] == expected_hash

    def test_empty_input_hash(self):
        """空输入不应产生哈希"""
        self.logger.log("test", "", "output")
        with open(self.logger._current_file, "r", encoding="utf-8") as f:
            record = json.loads(f.readline().strip())
        assert record["input_hash"] == ""

    def test_output_hash(self):
        """输出数据应被正确哈希"""
        self.logger.log("test", "", "result data")
        with open(self.logger._current_file, "r", encoding="utf-8") as f:
            record = json.loads(f.readline().strip())
        expected_hash = self.logger._hash("result data")
        assert record["output_hash"] == expected_hash

    def test_log_dir_creation(self):
        """日志目录不存在时应自动创建"""
        new_dir = os.path.join(self.tmpdir, "nested", "audit")
        assert not os.path.exists(new_dir)
        l = AuditLogger(log_dir=new_dir)
        assert os.path.exists(new_dir)
        l.log("test", "", "")
        assert l._current_file.exists()


# ═══════════════════════════════════════════════════════════════════
#  查询功能测试
# ═══════════════════════════════════════════════════════════════════

class TestAuditQuery:
    """AuditLogger 查询功能"""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.logger = AuditLogger(log_dir=self.tmpdir)

    def test_query_by_trace_id(self):
        """应按 trace_id 过滤查询结果（trace_id 为空时全部返回）"""
        self.logger.log("action1", "in", "out")
        self.logger.log("action2", "in", "out")

        # trace_id 为空时应返回全部
        results = self.logger.query(trace_id="")
        assert len(results) == 2

        # 不存在的 trace_id 应返回空
        results = self.logger.query(trace_id="nonexistent_trace_id_xyz")
        assert results == []

    def test_query_by_action(self):
        """应按 action 精确匹配过滤"""
        self.logger.log("create", "data1", "ok")
        self.logger.log("delete", "data2", "ok")
        self.logger.log("create", "data3", "ok")

        results = self.logger.query(action="create")
        assert len(results) == 2
        assert all(r["action"] == "create" for r in results)

    def test_query_limit(self):
        """查询应受 limit 参数限制"""
        for i in range(50):
            self.logger.log(f"action_{i}", f"in_{i}", f"out_{i}")

        results = self.logger.query(limit=10)
        assert len(results) == 10

    def test_query_no_params_returns_all(self):
        """无过滤参数应返回全部（受 limit 限制）"""
        for i in range(5):
            self.logger.log(f"action_{i}", "", "")

        results = self.logger.query(limit=100)
        assert len(results) == 5

    def test_query_empty_returns_empty(self):
        """查询到空结果应返回空列表"""
        results = self.logger.query(trace_id="nonexistent_trace_xyz")
        assert results == []

    def test_query_empty_log_dir(self):
        """空日志目录的查询应返回空"""
        empty_dir = tempfile.mkdtemp()
        l = AuditLogger(log_dir=empty_dir)
        results = l.query(action="anything")
        assert results == []

    def test_query_combined_filters(self):
        """多个过滤条件应同时生效"""
        self.logger.log("read", "in1", "out1")  # 会被查到
        self.logger.log("write", "in2", "out2")

        # 获取 read 操作的 trace_id
        with open(self.logger._current_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        read_trace = [l["trace_id"] for l in lines if l["action"] == "read"][0]

        results = self.logger.query(trace_id=read_trace, action="read")
        assert len(results) == 1
        assert results[0]["action"] == "read"


# ═══════════════════════════════════════════════════════════════════
#  追加写入特性测试
# ═══════════════════════════════════════════════════════════════════

class TestAuditAppendOnly:
    """AuditLogger Append-only 特性"""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.logger = AuditLogger(log_dir=self.tmpdir)

    def test_log_is_appended_not_overwritten(self):
        """多次日志应追加写入而非覆盖"""
        self.logger.log("first", "in1", "out1")
        content_before = self._read_all_lines()
        assert len(content_before) == 1

        self.logger.log("second", "in2", "out2")
        content_after = self._read_all_lines()
        assert len(content_after) == 2
        assert content_after[0] == content_before[0]  # 第一条不变
        assert "first" in content_after[0]
        assert "second" in content_after[1]

    def test_log_sequential_integrity(self):
        """追加写入应保证数据完整性"""
        entries = [f"entry_{i}" for i in range(100)]
        for e in entries:
            self.logger.log(e, "", "")

        lines = self._read_all_lines()
        assert len(lines) == 100
        for i, line in enumerate(lines):
            record = json.loads(line)
            assert record["action"] == f"entry_{i}"

    def test_log_file_by_date(self):
        """不同日期的日志应写入不同文件"""
        # 正常情况下所有日志写入当天文件
        self.logger.log("today", "", "")
        files_today = list(self.logger._log_dir.glob("audit_*.jsonl"))
        assert len(files_today) == 1

    def test_hash_consistency(self):
        """相同输入的哈希值应一致"""
        h1 = self.logger._hash("same input")
        h2 = self.logger._hash("same input")
        assert h1 == h2

    def test_hash_different_inputs(self):
        """不同输入的哈希值应不同"""
        h1 = self.logger._hash("input A")
        h2 = self.logger._hash("input B")
        assert h1 != h2

    def test_hash_length(self):
        """SHA256 截取应返回 16 位十六进制"""
        h = self.logger._hash("test data")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_stack_depth_is_positive(self):
        """调用栈深度应为正整数"""
        self.logger.log("test", "", "")
        with open(self.logger._current_file, "r", encoding="utf-8") as f:
            record = json.loads(f.readline().strip())
        assert isinstance(record["stack_depth"], int)
        assert record["stack_depth"] > 0

    def _read_all_lines(self):
        """读取所有日志行"""
        with open(self.logger._current_file, "r", encoding="utf-8") as f:
            return [line for line in f if line.strip()]
