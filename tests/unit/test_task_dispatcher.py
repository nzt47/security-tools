"""TaskDispatcher 任务调度器补充测试"""
import pytest
from agent.orchestrator.task_dispatcher import TaskDispatcher


class TestTaskDispatcher:
    """TaskDispatcher 基本功能测试"""

    def setup_method(self):
        self.dispatcher = TaskDispatcher()

    def test_create_dispatcher(self):
        assert self.dispatcher is not None

    def test_dispatch_unknown_task(self):
        with pytest.raises(Exception):
            self.dispatcher.dispatch("unknown_task", {})
