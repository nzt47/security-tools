"""MemoryReviewer 集成测试

验证 TLM 记忆审查器的核心功能：
- review() 全面审查（空库 + 有数据）
- review_quick() 快速审查
- get_last_review() 历史结果
- 陈旧记忆识别
- 重复记忆识别
- 建议生成

设计原则：
- 每个测试用 tmp_path 隔离 SQLite 数据库
- async 方法通过 asyncio.run() 调用
- 陈旧记忆通过直接修改 last_accessed 时间戳模拟
"""
import asyncio
import time
import pytest

pytestmark = pytest.mark.integration


def _run(coro):
    """同步运行 async 协程"""
    return asyncio.run(coro)


@pytest.fixture
def ltm_instance(tmp_path):
    """每个测试独立的 LongTermMemory 实例"""
    from agent.memory.long_term_memory import LongTermMemory
    db_path = str(tmp_path / "ltm_test.db")
    ltm = LongTermMemory(db_path=db_path)
    yield ltm


@pytest.fixture
def reviewer(ltm_instance):
    """每个测试独立的 MemoryReviewer 实例"""
    from agent.memory.reviewer import MemoryReviewer
    return MemoryReviewer(
        long_term_memory=ltm_instance,
        stale_threshold_days=30,
        similarity_threshold=0.85,
    )


class TestMemoryReviewerBasic:
    """基本审查功能"""

    def test_review_empty_db(self, reviewer):
        """空库审查应返回空结果，不报错"""
        result = _run(reviewer.review())
        assert result.total_entries == 0
        assert result.healthy_entries == 0
        assert result.stale_entries == 0
        assert result.duplicate_entries == 0
        assert len(result.suggestions) > 0
        assert any("为空" in s for s in result.suggestions)

    def test_review_with_entries(self, reviewer, ltm_instance):
        """有数据的审查应返回正确统计"""
        for i in range(5):
            _run(ltm_instance.save(
                key=f"k{i}",
                content=f"content_{i}",
                importance=3,
            ))
        result = _run(reviewer.review())
        assert result.total_entries == 5
        assert result.healthy_entries >= 0

    def test_review_quick(self, reviewer):
        """快速审查返回 dict，含 quick=True"""
        result = _run(reviewer.review_quick())
        assert isinstance(result, dict)
        assert result["quick"] is True
        assert "total_entries" in result
        assert "suggestions" in result

    def test_review_quick_with_data(self, reviewer, ltm_instance):
        """有数据时 quick 审查返回正确统计"""
        _run(ltm_instance.save("k1", "content", importance=4))
        result = _run(reviewer.review_quick())
        assert result["total_entries"] == 1
        assert result["high_importance_entries"] >= 1


class TestMemoryReviewerHistory:
    """历史结果测试"""

    def test_get_last_review_initial(self, reviewer):
        """初始状态返回 None"""
        assert reviewer.get_last_review() is None

    def test_get_last_review_after_review(self, reviewer):
        """review() 后可获取上次结果"""
        _run(reviewer.review())
        last = reviewer.get_last_review()
        assert last is not None
        assert hasattr(last, "reviewed_at")
        assert hasattr(last, "total_entries")


class TestMemoryReviewerDetection:
    """陈旧与重复检测"""

    def test_stale_detection(self, reviewer, ltm_instance):
        """陈旧记忆（last_accessed 过早 + importance<4）应被识别"""
        _run(ltm_instance.save("stale_low", "old data", importance=2))
        # 直接修改 last_accessed 为 60 天前
        import sqlite3
        conn = sqlite3.connect(ltm_instance.db_path)
        old_time = time.time() - 60 * 86400
        conn.execute(
            f"UPDATE {ltm_instance._TABLE_NAME} SET last_accessed = ? WHERE key = ?",
            (old_time, "stale_low"),
        )
        conn.commit()
        conn.close()

        result = _run(reviewer.review())
        assert result.stale_entries >= 1

    def test_stale_high_importance_not_flagged(self, reviewer, ltm_instance):
        """高重要性记忆（importance>=4）即使陈旧也不标记"""
        _run(ltm_instance.save("stale_high", "old important", importance=5))
        import sqlite3
        conn = sqlite3.connect(ltm_instance.db_path)
        old_time = time.time() - 60 * 86400
        conn.execute(
            f"UPDATE {ltm_instance._TABLE_NAME} SET last_accessed = ? WHERE key = ?",
            (old_time, "stale_high"),
        )
        conn.commit()
        conn.close()

        result = _run(reviewer.review())
        assert result.stale_entries == 0

    def test_duplicate_detection(self, reviewer, ltm_instance):
        """相同内容的记忆应被标记为重复"""
        _run(ltm_instance.save("k1", "duplicate content", importance=3))
        _run(ltm_instance.save("k2", "duplicate content", importance=3))
        result = _run(reviewer.review())
        assert result.duplicate_entries >= 1


class TestMemoryReviewerSuggestions:
    """建议生成测试"""

    def test_suggestions_for_empty_db(self, reviewer):
        """空库应建议'无需清理'或'记忆库为空'"""
        result = _run(reviewer.review())
        assert len(result.suggestions) > 0

    def test_suggestions_for_stale(self, reviewer, ltm_instance):
        """有陈旧记忆时应建议清理"""
        _run(ltm_instance.save("stale", "old", importance=1))
        import sqlite3
        conn = sqlite3.connect(ltm_instance.db_path)
        old_time = time.time() - 60 * 86400
        conn.execute(
            f"UPDATE {ltm_instance._TABLE_NAME} SET last_accessed = ? WHERE key = ?",
            (old_time, "stale"),
        )
        conn.commit()
        conn.close()

        result = _run(reviewer.review())
        stale_suggestions = [s for s in result.suggestions if "陈旧" in s]
        assert len(stale_suggestions) > 0

    def test_health_score_range(self, reviewer, ltm_instance):
        """健康评分应在 0-100 范围"""
        _run(ltm_instance.save("k1", "content", importance=3))
        result = _run(reviewer.review())
        score = result.report.get("health_score", 100)
        assert 0.0 <= score <= 100.0
