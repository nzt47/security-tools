"""SubagentManager 补充测试"""
import pytest


class TestSubagentManagerSimple:
    """SubagentManager 导入与基本测试"""

    def test_import_subagent_manager(self):
        from agent.orchestrator.subagent_manager import SubagentManager
        assert SubagentManager is not None

    def test_create_no_orchestrator(self):
        from agent.orchestrator.subagent_manager import SubagentManager
        with pytest.raises(AttributeError):
            mgr = SubagentManager(None)
            mgr.create({"name": "test", "model_id": "gpt-4"})
