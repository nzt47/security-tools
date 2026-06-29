"""HITL + Ethics + ToolRouter 集成测试

验证 Tool 调用路径上的安全防线串联：
  - 危险命令 → Ethics 违规 + HITL CRITICAL
  - 安全命令 → 两道防线均放行
"""
import pytest
from agent.human_in_the_loop.hitl import HITLManager, RiskLevel
from agent.human_in_the_loop.ethics import EthicsEngine


class TestHITLTools:
    def test_dangerous_command_double_blocked(self):
        """危险命令（rm -rf /）应被 Ethics + HITL 双重拦截"""
        ethics = EthicsEngine()
        hitl = HITLManager()

        violations = ethics.check("execute_shell", {"command": "rm -rf /"})
        risk = hitl.assess("execute_shell", {"command": "rm -rf /"})

        assert len(violations) >= 1
        assert risk == RiskLevel.CRITICAL

    def test_safe_command_passes_both(self):
        """安全命令（read_file）应通过两道检查"""
        ethics = EthicsEngine()
        hitl = HITLManager()

        violations = ethics.check("read_file", {"path": "README.md"})
        risk = hitl.assess("read_file", {"path": "README.md"})

        assert len(violations) == 0
        assert risk == RiskLevel.LOW

    def test_dangerous_write_high_risk(self):
        """危险写文件操作应被评为 HIGH 风险"""
        hitl = HITLManager()
        risk = hitl.assess("write_file", {"path": "/etc/passwd"})
        assert risk == RiskLevel.HIGH
