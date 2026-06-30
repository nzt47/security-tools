"""Log System 安全日志 — SensitiveDataFilter / AgentSafetyMonitor 测试"""
import time
import logging

import pytest
from agent.log_system.safe_logger import (
    SensitiveDataFilter, AgentSafetyMonitor, AgentTimeoutException,
)


class TestSensitiveDataFilter:
    """敏感信息脱敏过滤器测试"""

    def setup_method(self):
        self.filter = SensitiveDataFilter()

    def test_mask_api_key(self):
        cleaned = self.filter._sanitize("API key: sk-abcdefghijklmnopqrst")
        assert "***" in cleaned
        assert "sk-abcdefghij" not in cleaned

    def test_mask_jwt_token(self):
        cleaned = self.filter._sanitize("token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNqP")
        # JWT 被 base64 字符串模式匹配
        assert "***" in cleaned or "eyJhbGci" not in cleaned

    def test_mask_phone(self):
        cleaned = self.filter._sanitize("手机号 13812345678")
        assert "****" in cleaned  # 1[3-9]\d 被替换为 ****
        assert "13812345678" not in cleaned

    def test_mask_id_card(self):
        cleaned = self.filter._sanitize("身份证 110101199001011234")
        assert "********" in cleaned
        assert "19900101" not in cleaned

    def test_mask_email(self):
        cleaned = self.filter._sanitize("联系邮箱 test@example.com")
        assert "***@***.com" in cleaned

    def test_mask_password_field(self):
        cleaned = self.filter._sanitize('password=mysecret123')
        assert "***" in cleaned or "mysecret123" not in cleaned

    def test_clean_text_unchanged(self):
        cleaned = self.filter._sanitize("你好，今天天气不错")
        assert cleaned == "你好，今天天气不错"

    def test_sanitize_dict(self):
        data = {"user": "admin", "password": "secret123", "detail": {"token": "abc"}}
        result = self.filter._sanitize_dict(data)
        assert result["password"] == "***"
        assert result["user"] == "admin"
        assert result["detail"]["token"] == "***"

    def test_sanitize_dict_list_values(self):
        data = {"names": ["alice", "bob"], "phone": "13812345678"}
        result = self.filter._sanitize_dict(data)
        assert "****" in result["phone"]

    def test_filter_record(self):
        record = logging.LogRecord("test", logging.INFO, "", 0, "user password=secret123", None, None)
        result = self.filter.filter(record)
        assert result is True
        assert "secret123" not in record.msg


class TestAgentSafetyMonitor:
    """Agent 安全监控器测试"""

    def setup_method(self):
        self.monitor = AgentSafetyMonitor(max_iterations_per_minute=10, state_stuck_threshold_seconds=1)

    def test_record_first_iteration(self):
        assert self.monitor.record_iteration("task1") is True

    def test_record_exceeds_limit(self):
        monitor = AgentSafetyMonitor(max_iterations_per_minute=5, state_stuck_threshold_seconds=60)
        for i in range(6):
            monitor.record_iteration("fast_task")
        # 窗口计数会持续增加（同一分钟内超过阈值）
        assert monitor.record_iteration("fast_task") is False

    def test_check_state_initial(self):
        assert self.monitor.check_state("t1", "running") is True

    def test_check_state_change(self):
        self.monitor.check_state("t1", "running")
        assert self.monitor.check_state("t1", "done") is True

    def test_check_state_stuck(self):
        self.monitor.check_state("stuck_task", "loading")
        time.sleep(1.1)
        assert self.monitor.check_state("stuck_task", "loading") is False

    def test_reset_single(self):
        self.monitor.record_iteration("to_reset")
        self.monitor.reset("to_reset")
        assert self.monitor.record_iteration("to_reset") is True

    def test_reset_all(self):
        self.monitor.record_iteration("a")
        self.monitor.record_iteration("b")
        self.monitor.reset()
        stats = self.monitor.get_stats()
        assert stats["tracked_identifiers"] == 0

    def test_get_stats(self):
        self.monitor.record_iteration("stat_task")
        stats = self.monitor.get_stats()
        assert stats["tracked_identifiers"] == 1
        assert stats["max_iterations_per_minute"] == 10
        assert stats["state_stuck_threshold"] == 1
