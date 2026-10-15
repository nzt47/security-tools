"""会话级智能体字段的存储层测试（语义 A 的持久化部分）。

覆盖：
- create_session(agent=) → 索引（list_sessions）与 meta（get_session_metadata）都能看到；
- set_session_agent：设置 / 清除（空串与 None 等价）/ 首尾空白裁剪；
- 不存在的会话返回 False。

【范围】本文件只测"绑定字段"这一层；"整段会话由该分身回答"的运行时行为在
_workbench_real_stream 里（依赖 app_server 与真实 LLM，属集成面）。
"""
import pytest

from agent.session_manager import SessionManager


@pytest.fixture
def manager(tmp_path):
    return SessionManager(sessions_dir=str(tmp_path / "sessions"))


class TestSessionAgentBinding:
    def test_create_with_agent(self, manager):
        info = manager.create_session(title="A", agent="demo-agent")
        assert info["agent"] == "demo-agent"
        listed = {s["id"]: s for s in manager.list_sessions()}[info["id"]]
        assert listed["agent"] == "demo-agent"
        assert manager.get_session_metadata(info["id"])["agent"] == "demo-agent"

    def test_create_without_agent(self, manager):
        info = manager.create_session(title="A")
        assert info["agent"] is None
        assert manager.get_session_metadata(info["id"])["agent"] is None

    def test_set_and_clear_agent(self, manager):
        info = manager.create_session()
        assert manager.set_session_agent(info["id"], "agent-x") is True
        assert manager.get_session(info["id"])["agent"] == "agent-x"
        assert manager.get_session_metadata(info["id"])["agent"] == "agent-x"

        assert manager.set_session_agent(info["id"], None) is True
        assert manager.get_session(info["id"])["agent"] is None

        # 空串与纯空白等价于"清除"
        assert manager.set_session_agent(info["id"], "agent-y") is True
        assert manager.set_session_agent(info["id"], "   ") is True
        assert manager.get_session(info["id"])["agent"] is None

    def test_set_agent_trims(self, manager):
        info = manager.create_session()
        manager.set_session_agent(info["id"], "  spaced  ")
        assert manager.get_session(info["id"])["agent"] == "spaced"

    def test_set_agent_unknown_session(self, manager):
        assert manager.set_session_agent("sess_nope", "agent-x") is False
