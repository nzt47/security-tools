"""handoff_generator 单元测试

测试覆盖:
1. 正常流程: mock LLM 返回固定摘要，验证 Markdown 渲染含 4 章节
2. 脱敏: 含 Bearer/api_key 的消息，验证输出含 [REDACTED]
3. 降级链: LLM.chat 和 summarize 都抛异常，验证 fallback_used="manual"
4. 空会话: 无消息时抛 ValueError
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.handoff.handoff_generator import generate_handoff


def _make_state(messages, llm_chat_return="## 目标\n测试目标\n## 已完成\n无\n## 待办\n无\n## 阻塞\n无",
                llm_chat_exc=None, llm_summarize_exc=None):
    """构造 mock state 对象"""
    state = MagicMock()
    state.session_mgr.get_messages.return_value = messages
    state.session_mgr.get_session.return_value = {
        "id": "sess_test",
        "title": "测试会话",
        "message_count": len(messages),
    }
    state.session_mgr.get_current_id.return_value = "sess_test"

    llm = MagicMock()
    if llm_chat_exc:
        llm.chat.side_effect = llm_chat_exc
    else:
        llm.chat.return_value = llm_chat_return
    if llm_summarize_exc:
        llm.summarize.side_effect = llm_summarize_exc
    else:
        llm.summarize.return_value = "降级摘要"
    state.Yunshu._llm = llm
    return state


@pytest.fixture
def isolated_tmp(tmp_path):
    """让 _write_temp_file 写到测试的 tmp_path 而非真实 OS 临时目录"""
    with patch("agent.handoff.handoff_generator.tempfile.gettempdir", return_value=str(tmp_path)):
        yield tmp_path


@pytest.fixture
def mock_skills_service():
    """mock get_skills_mgmt_service 避免 skill 推荐真实调用"""
    with patch("agent.state_manager.get_skills_mgmt_service") as m:
        svc = MagicMock()
        match_result = MagicMock()
        match_result.matches = []
        svc.match_skills.return_value = match_result
        m.return_value = svc
        yield m


# ── 1. 正常流程 ──────────────────────────────────────────────


class TestNormalFlow:
    def test_renders_four_sections(self, isolated_tmp, mock_skills_service):
        messages = [
            {"role": "user", "content": "帮我修复 bug"},
            {"role": "assistant", "content": "好的，我看一下 agent/session_manager.py"},
            {"role": "user", "content": "commit abc12345 已提交"},
        ]
        state = _make_state(messages)

        result = generate_handoff(state)

        assert result["session_id"] == "sess_test"
        assert result["message_count"] == 3
        assert result["fallback_used"] is None
        assert result["skills_count"] == 0

        md = Path(result["file_path"]).read_text(encoding="utf-8")
        assert "## 目标" in md
        assert "## 已完成" in md
        assert "## 待办" in md
        assert "## 阻塞" in md
        assert "## Suggested Skills" in md
        assert "## 脱敏声明" in md


# ── 2. 脱敏 ──────────────────────────────────────────────────


class TestRedaction:
    def test_bearer_and_api_key_redacted(self, isolated_tmp, mock_skills_service):
        messages = [
            {"role": "user", "content": "用 Bearer sk-12345abcdef 调用 API"},
            {"role": "assistant", "content": "已用 api_key=secret789 配置完成"},
        ]
        # mock LLM 复述敏感信息（模拟泄露场景），验证输出后脱敏
        state = _make_state(
            messages,
            llm_chat_return="摘要: Bearer sk-12345abcdef, api_key=secret789",
        )

        result = generate_handoff(state)
        md = Path(result["file_path"]).read_text(encoding="utf-8")

        assert "sk-12345abcdef" not in md
        assert "secret789" not in md
        assert "[REDACTED]" in md


# ── 3. 降级链 ────────────────────────────────────────────────


class TestFallback:
    def test_manual_fallback_when_llm_fails(self, isolated_tmp, mock_skills_service):
        messages = [
            {"role": "user", "content": "第一条用户消息"},
            {"role": "assistant", "content": "助手回复"},
            {"role": "user", "content": "第二条用户消息"},
            {"role": "assistant", "content": "最终回复"},
        ]
        state = _make_state(
            messages,
            llm_chat_exc=RuntimeError("LLM 不可用"),
            llm_summarize_exc=RuntimeError("summarize 也不可用"),
        )

        result = generate_handoff(state)

        assert result["fallback_used"] == "manual"
        md = Path(result["file_path"]).read_text(encoding="utf-8")
        assert "## 会话摘录" in md
        assert "第一条用户消息" in md

    def test_summarize_fallback(self, isolated_tmp, mock_skills_service):
        messages = [{"role": "user", "content": "测试消息"}]
        state = _make_state(
            messages,
            llm_chat_exc=RuntimeError("chat 失败"),
        )

        result = generate_handoff(state)

        assert result["fallback_used"] == "summarize"


# ── 4. 空会话 ────────────────────────────────────────────────


class TestErrors:
    def test_empty_messages_raises_valueerror(self, isolated_tmp, mock_skills_service):
        state = _make_state([])
        state.session_mgr.get_messages.return_value = []

        with pytest.raises(ValueError, match="无消息"):
            generate_handoff(state)

    def test_no_current_session_raises_valueerror(self, isolated_tmp, mock_skills_service):
        state = _make_state([])
        state.session_mgr.get_current_id.return_value = None
        state.session_mgr.get_messages.return_value = []

        with pytest.raises(ValueError, match="无当前会话"):
            generate_handoff(state)
