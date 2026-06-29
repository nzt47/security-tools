"""PromptBuilder 系统提示构建测试"""
import pytest
from agent.orchestrator.prompt_builder import PromptBuilder


class TestPromptBuilder:
    """PromptBuilder 单元测试"""

    def test_init(self):
        builder = PromptBuilder()
        assert builder is not None
        assert builder._memory_token_limit == 8000

    def test_init_custom_limit(self):
        builder = PromptBuilder(memory_token_limit=4000)
        assert builder._memory_token_limit == 4000

    def test_build_memory_context_no_memory(self):
        builder = PromptBuilder()
        ctx = builder.build_memory_context(None)
        assert "暂无历史对话" in ctx

    def test_build_memory_context_with_summary(self):
        builder = PromptBuilder()

        class FakeMemory:
            def load_summary(self):
                return [{"summary": "这是测试摘要内容"}]

        ctx = builder.build_memory_context(FakeMemory())
        assert ctx is not None

    def test_build_memory_context_summary_exception(self):
        builder = PromptBuilder()

        class BrokenMemory:
            def load_summary(self):
                raise Exception("模拟错误")

        ctx = builder.build_memory_context(BrokenMemory())
        assert "暂无历史对话" in ctx
