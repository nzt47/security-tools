"""HITL + Ethics 测试"""
from agent.human_in_the_loop.hitl import HITLManager, RiskLevel
from agent.human_in_the_loop.ethics import EthicsEngine

class TestHITL:
    def setup_method(self):
        self.hitl = HITLManager()

    def test_low_risk_auto_approve(self):
        r = self.hitl.request_approval("read_file", {"path": "test.txt"})
        assert r.approved

    def test_high_risk(self):
        r = self.hitl.assess("delete_file", {"path": "test.txt"})
        assert r == RiskLevel.HIGH

    def test_critical_risk(self):
        r = self.hitl.assess("execute_shell", {"command": "rm -rf /"})
        assert r == RiskLevel.CRITICAL

class TestEthics:
    def setup_method(self):
        self.ethics = EthicsEngine()

    def test_block_dangerous(self):
        v = self.ethics.check("execute_shell", {"command": "rm -rf /"})
        assert len(v) > 0

    def test_allow_safe(self):
        v = self.ethics.check("read_file", {"path": "readme.txt"})
        assert len(v) == 0
