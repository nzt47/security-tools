"""Log System 格式化/轮转配置测试"""
from agent.log_system.formatter import LogRotationConfig


class TestLogRotationConfig:
    """日志轮转配置测试"""

    def test_default_values(self):
        cfg = LogRotationConfig()
        assert cfg.max_bytes == 50 * 1024 * 1024
        assert cfg.backup_count == 5
        assert cfg.encoding == "utf-8"
        assert cfg.when == "midnight"
        assert cfg.interval == 1
        assert cfg.utc is False
        assert cfg.use_timed_rotation is False

    def test_custom_values(self):
        cfg = LogRotationConfig(
            max_bytes=1024,
            backup_count=3,
            encoding="utf-16",
            when="daily",
            interval=2,
            utc=True,
            use_timed_rotation=True,
        )
        assert cfg.max_bytes == 1024
        assert cfg.backup_count == 3
        assert cfg.encoding == "utf-16"
        assert cfg.use_timed_rotation is True

    def test_to_dict(self):
        cfg = LogRotationConfig(max_bytes=2048, backup_count=7)
        d = cfg.to_dict()
        assert d["max_bytes"] == 2048
        assert d["backup_count"] == 7
        assert d["encoding"] == "utf-8"
        assert d["use_timed_rotation"] is False
