"""KnowledgePrecipitator flush_pending 单元测试 [TLM-AUDIT-003]

验证修复后：
- precipitate 高置信度时调度可追踪的持久化任务（非 fire-and-forget）
- flush_pending 等待所有未完成任务完成
- 任务完成后自动从 _pending_persist_tasks 移除
- 无事件循环时降级跳过（不崩溃）
- 超时返回 False
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from agent.cognitive.knowledge import KnowledgePrecipitator, KnowledgeRecord


# ════════════════════════════════════════════════════════════
#  公共 fixture
# ════════════════════════════════════════════════════════════

@pytest.fixture
def mock_memory_router():
    """mock MemoryRouter，save 方法为 AsyncMock"""
    router = MagicMock()
    router.save = AsyncMock(return_value=True)
    return router


@pytest.fixture
def precipitator(mock_memory_router):
    """创建带 mock router 的 KnowledgePrecipitator"""
    return KnowledgePrecipitator(memory_router=mock_memory_router)


def _make_high_confidence_input():
    """构造高置信度输入（含 SIGNAL_WORDS + 数值事实）"""
    return "chat", "请帮我把超时设置为30秒", "已将超时配置修改为30秒"


def _make_low_confidence_input():
    """构造低置信度输入（无信号词、无数值）"""
    return "chat", "你好", "你好，有什么可以帮你？"


# ════════════════════════════════════════════════════════════
#  TestSchedulePersist — 任务调度与追踪
# ════════════════════════════════════════════════════════════

class TestSchedulePersist:
    """_schedule_persist 行为验证"""

    @pytest.mark.asyncio
    async def test_precipitate_schedules_persist_task(self, precipitator, mock_memory_router):
        """高置信度 precipitate 调度持久化任务，Task 引用被保存"""
        task_type, input_text, output = _make_high_confidence_input()
        precipitator.precipitate(task_type, input_text, output, trace_id="test_001")

        # Task 应被加入 _pending_persist_tasks
        assert len(precipitator._pending_persist_tasks) == 1, "应有 1 个待处理任务"

        # 等待任务完成
        await precipitator.flush_pending(timeout=2.0)

        # memory_router.save 应被调用
        assert mock_memory_router.save.called, "save 应被调用"
        call_args = mock_memory_router.save.call_args
        assert call_args is not None
        # 验证 key 格式
        key = call_args.args[0] if call_args.args else call_args.kwargs.get("key")
        assert key and key.startswith("knowledge_"), f"key 格式错误: {key}"

    @pytest.mark.asyncio
    async def test_low_confidence_no_persist(self, precipitator, mock_memory_router):
        """低置信度 precipitate 不触发持久化"""
        task_type, input_text, output = _make_low_confidence_input()
        # "你好" 在 SKIP_PATTERNS 中，直接返回 None
        result = precipitator.precipitate(task_type, input_text, output)
        assert result is None, "低价值交互应返回 None"
        assert len(precipitator._pending_persist_tasks) == 0, "不应调度持久化任务"
        assert not mock_memory_router.save.called, "save 不应被调用"

    @pytest.mark.asyncio
    async def test_task_auto_cleanup_after_done(self, precipitator, mock_memory_router):
        """任务完成后自动从 _pending_persist_tasks 移除"""
        task_type, input_text, output = _make_high_confidence_input()
        precipitator.precipitate(task_type, input_text, output, trace_id="test_002")

        assert len(precipitator._pending_persist_tasks) == 1
        await precipitator.flush_pending(timeout=2.0)
        # add_done_callback 应已自动清理
        assert len(precipitator._pending_persist_tasks) == 0, "任务完成后应自动移除"

    @pytest.mark.asyncio
    async def test_multiple_tasks_tracked(self, precipitator, mock_memory_router):
        """多次 precipitate 调度多个任务，全部被追踪"""
        for i in range(5):
            precipitator.precipitate(
                "chat",
                f"请把参数{i}设置为{i * 10}秒",
                f"已将参数{i}修改为{i * 10}秒",
                trace_id=f"test_multi_{i}",
            )
        assert len(precipitator._pending_persist_tasks) == 5, "应有 5 个待处理任务"
        await precipitator.flush_pending(timeout=5.0)
        assert len(precipitator._pending_persist_tasks) == 0
        assert mock_memory_router.save.call_count == 5


# ════════════════════════════════════════════════════════════
#  TestFlushPending — 关闭前等待行为
# ════════════════════════════════════════════════════════════

class TestFlushPending:
    """flush_pending 关闭行为验证"""

    @pytest.mark.asyncio
    async def test_flush_pending_returns_true_when_empty(self, precipitator):
        """空集合时立即返回 True"""
        result = await precipitator.flush_pending(timeout=1.0)
        assert result is True, "空集合应返回 True"

    @pytest.mark.asyncio
    async def test_flush_pending_waits_for_tasks(self, precipitator, mock_memory_router):
        """flush_pending 等待所有任务完成"""
        task_type, input_text, output = _make_high_confidence_input()
        precipitator.precipitate(task_type, input_text, output, trace_id="test_003")
        precipitator.precipitate(task_type, input_text, output, trace_id="test_004")

        assert len(precipitator._pending_persist_tasks) == 2
        result = await precipitator.flush_pending(timeout=5.0)
        assert result is True, "应在超时前完成"
        assert len(precipitator._pending_persist_tasks) == 0

    @pytest.mark.asyncio
    async def test_flush_pending_timeout(self, precipitator):
        """任务未完成时 flush_pending 超时返回 False"""
        # 用永不完成的 Future 模拟卡住的任务
        async def hang_forever():
            await asyncio.Future()  # 永不完成

        # 手动添加卡住的任务到 _pending_persist_tasks
        task = asyncio.create_task(hang_forever())
        precipitator._pending_persist_tasks.add(task)

        result = await precipitator.flush_pending(timeout=0.5)
        assert result is False, "超时应返回 False"

        # 清理：取消卡住的任务
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_flush_pending_handles_task_exception(self, precipitator):
        """单个任务异常不阻塞 flush_pending（return_exceptions=True）"""
        async def failing_task():
            raise ValueError("模拟持久化失败")

        task = asyncio.create_task(failing_task())
        precipitator._pending_persist_tasks.add(task)

        # 不应抛异常
        result = await precipitator.flush_pending(timeout=2.0)
        assert result is True, "异常任务应被视为完成（return_exceptions=True）"

    @pytest.mark.asyncio
    async def test_flush_pending_idempotent(self, precipitator, mock_memory_router):
        """二次调用 flush_pending 不报错（幂等性）"""
        task_type, input_text, output = _make_high_confidence_input()
        precipitator.precipitate(task_type, input_text, output, trace_id="test_005")

        first = await precipitator.flush_pending(timeout=2.0)
        assert first is True

        # 二次调用应直接返回 True（集合已空）
        second = await precipitator.flush_pending(timeout=2.0)
        assert second is True


# ════════════════════════════════════════════════════════════
#  TestSchedulePersistDegradation — 降级处理
# ════════════════════════════════════════════════════════════

class TestSchedulePersistDegradation:
    """_schedule_persist 降级场景"""

    def test_no_event_loop_degrades_gracefully(self, precipitator):
        """无事件循环时 _schedule_persist 降级跳过，不崩溃"""
        # 在同步上下文调用（无运行中的事件循环）
        record = KnowledgeRecord(
            task_type="chat",
            summary="测试摘要",
            key_facts=["参数设置为30秒"],
            entities=[],
            confidence=0.8,
        )
        # 不应抛 RuntimeError
        precipitator._schedule_persist(record, "test_no_loop")
        # 任务不应被加入集合
        assert len(precipitator._pending_persist_tasks) == 0, "无事件循环时不应创建任务"

    @pytest.mark.asyncio
    async def test_precipitate_in_async_context_works(self, precipitator, mock_memory_router):
        """异步上下文中 precipitate 正常调度任务"""
        task_type, input_text, output = _make_high_confidence_input()
        precipitator.precipitate(task_type, input_text, output, trace_id="test_async")
        assert len(precipitator._pending_persist_tasks) == 1
        await precipitator.flush_pending(timeout=2.0)
        assert mock_memory_router.save.called


# ════════════════════════════════════════════════════════════
#  TestPersistCorrectness — 持久化数据正确性
# ════════════════════════════════════════════════════════════

class TestPersistCorrectness:
    """_persist 数据正确性验证"""

    @pytest.mark.asyncio
    async def test_persist_passes_correct_data(self, precipitator, mock_memory_router):
        """_persist 传递正确的数据给 memory_router.save"""
        task_type, input_text, output = _make_high_confidence_input()
        precipitator.precipitate(task_type, input_text, output, trace_id="test_data_001")
        await precipitator.flush_pending(timeout=2.0)

        assert mock_memory_router.save.called
        call_args = mock_memory_router.save.call_args
        # save(key, data, task_type=...) 调用模式
        assert call_args is not None
        # 验证 data 包含必要字段
        # call_args.args[0] = key, call_args.args[1] = data dict
        data = call_args.args[1] if len(call_args.args) > 1 else None
        if data is None:
            # 也可能是 kwargs
            data = call_args.kwargs.get("data")
        assert data is not None, "save 应接收 data 参数"
        assert data["type"] == "cognitive_knowledge"
        assert data["task_type"] == task_type
        assert data["trace_id"] == "test_data_001"
        assert "summary" in data
        assert "key_facts" in data
        assert "confidence" in data

    @pytest.mark.asyncio
    async def test_persist_failure_does_not_block_flush(self, precipitator):
        """_persist 抛异常时 flush_pending 仍能完成（不阻塞其他任务）"""
        # mock router 抛异常
        failing_router = MagicMock()
        failing_router.save = AsyncMock(side_effect=Exception("存储不可用"))
        precipitator._memory_router = failing_router

        precipitator.precipitate(
            "chat", "设置超时为30秒", "已设置超时为30秒", trace_id="test_fail"
        )
        # flush 不应抛异常
        result = await precipitator.flush_pending(timeout=2.0)
        assert result is True, "异常任务应被视为完成"
