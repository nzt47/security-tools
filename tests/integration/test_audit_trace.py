"""审计日志 + Trace 集成测试

验证 AuditLogger 能感知当前 Trace_ID：
  - 设置 trace_id → 审计记录携带相同 trace_id
  - query 能按 trace_id 过滤
"""
import os
import json
import shutil
from agent.audit.logger import AuditLogger
from agent.observability.tracer import generate_trace_id, get_trace_id, set_trace_id


class TestAuditTrace:
    def setup_method(self):
        self._log_dir = "./test_audit_integration"
        os.makedirs(self._log_dir, exist_ok=True)

    def test_audit_carries_trace_id(self):
        """审计记录应携带当前设置的 Trace_ID"""
        trace_id = generate_trace_id()
        set_trace_id(trace_id)

        logger = AuditLogger(log_dir=self._log_dir)
        logger.log("test_action", input_data="input")

        records = logger.query(trace_id=trace_id)
        assert len(records) >= 1
        assert records[0]["trace_id"] == trace_id

    def test_different_trace_ids_isolated(self):
        """不同 trace_id 的审计记录应互不干扰"""
        trace_a = generate_trace_id()
        set_trace_id(trace_a)
        logger = AuditLogger(log_dir=self._log_dir)
        logger.log("action_a", input_data="aaa")

        trace_b = generate_trace_id()
        set_trace_id(trace_b)
        logger.log("action_b", input_data="bbb")

        records_a = logger.query(trace_id=trace_a)
        records_b = logger.query(trace_id=trace_b)

        assert len(records_a) == 1
        assert len(records_b) == 1
        assert records_a[0]["input_hash"] == logger._hash("aaa")
        assert records_b[0]["input_hash"] == logger._hash("bbb")

    def teardown_method(self):
        if os.path.exists(self._log_dir):
            shutil.rmtree(self._log_dir)
