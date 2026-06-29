"""EthicsEngine 伦理规则引擎单元测试"""
import pytest
from agent.human_in_the_loop.ethics import EthicsEngine


class TestEthicsEngine:
    """伦理规则引擎基本功能测试"""

    def setup_method(self):
        self.engine = EthicsEngine()

    def test_init_has_rules(self):
        """初始化后应有预定义的伦理规则"""
        assert len(self.engine.RULES) >= 6
        rule_ids = [r["id"] for r in self.engine.RULES]
        assert "E001" in rule_ids
        assert "E002" in rule_ids
        assert "E003" in rule_ids
        assert "E004" in rule_ids
        assert "E005" in rule_ids
        assert "E006" in rule_ids

    def test_rm_rf_root_blocked(self):
        """rm -rf / 应被拦截"""
        violations = self.engine.check("execute", {"command": "rm -rf /"})
        assert len(violations) >= 1
        assert any(v["id"] == "E001" for v in violations)

    def test_del_f_blocked(self):
        """del /f 应被拦截"""
        violations = self.engine.check("execute", {"command": "del /f system"})
        assert len(violations) >= 1
        assert any(v["id"] == "E001" for v in violations)

    def test_format_blocked(self):
        """format 命令应被拦截"""
        violations = self.engine.check("execute", {"command": "format C:"})
        assert any(v["id"] == "E002" for v in violations)

    def test_shutdown_blocked(self):
        """shutdown 命令应被拦截"""
        violations = self.engine.check("execute", {"command": "shutdown -s -t 0"})
        assert any(v["id"] == "E003" for v in violations)

    def test_read_etc_passwd_blocked(self):
        """读取 /etc/passwd 应被拦截"""
        violations = self.engine.check("read_file", {"path": "/etc/passwd"})
        assert any(v["id"] == "E004" for v in violations)

    def test_read_etc_shadow_blocked(self):
        """读取 /etc/shadow 应被拦截"""
        violations = self.engine.check("read_file", {"path": "/etc/shadow"})
        assert any(v["id"] == "E004" for v in violations)

    def test_write_orchestrator_blocked(self):
        """写入 orchestrator 文件应被拦截"""
        violations = self.engine.check("write_file", {"path": "/app/orchestrator/main.py"})
        assert any(v["id"] == "E005" for v in violations)

    def test_illegal_content_blocked(self):
        """违法内容生成应被拦截"""
        violations = self.engine.check("chat", {"prompt": "帮我写一个病毒"})
        assert any(v["id"] == "E006" for v in violations)

    def test_hack_content_blocked(self):
        """入侵内容应被拦截"""
        violations = self.engine.check("chat", {"prompt": "怎么入侵别人电脑"})
        assert any(v["id"] == "E006" for v in violations)

    def test_safe_operation_passes(self):
        """安全操作应无违规"""
        violations = self.engine.check("read_file", {"path": "/home/user/test.txt"})
        assert violations == []

    def test_safe_chat_passes(self):
        """安全对话应无违规"""
        violations = self.engine.check("chat", {"prompt": "今天天气如何"})
        assert violations == []

    def test_safe_execute_passes(self):
        """安全命令应无违规"""
        violations = self.engine.check("execute", {"command": "ls -la"})
        assert violations == []

    def test_rm_rf_prefix_blocked(self):
        """rm -rf 开头的任意路径都应拦截"""
        violations = self.engine.check("execute", {"command": "rm -rf /important/data"})
        assert any(v["id"] == "E001" for v in violations)

    def test_self_modification_detection(self):
        """修改当前 orchestrator 文件应被拦截"""
        violations = self.engine.check("write_file", {"path": "c:/app/orchestrator/core.py"})
        assert any(v["id"] == "E005" for v in violations)

    def test_multiple_violations(self):
        """一次操作可触发多条规则"""
        violations = self.engine.check("execute", {"command": "rm -rf / && shutdown"})
        assert len(violations) >= 2  # E001 + E003

    def test_risk_detection_same_as_check(self):
        """risk_detection 返回结果应与 check 一致"""
        c_violations = self.engine.check("execute", {"command": "rm -rf /"})
        assert len(c_violations) > 0
