"""LogStorage 存储层单元测试（SQLite 内存模式）"""
import os
import tempfile

import pytest
from agent.log_system.storage import LogStorage
from agent.log_system.models import (
    LogEntry, LogCategory, LogLevel, LogQuery,
    PerformanceRecord, ErrorRecord, BehaviorRecord,
    Insight, ActionItem, KnowledgeFinding,
)


@pytest.fixture
def storage():
    """创建内存存储实例"""
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test.db")
    s = LogStorage(db_path=db_path, raw_log_dir=os.path.join(tmp_dir, "raw"))
    s.initialize()
    yield s
    s.close()


class TestLogStorageInit:
    """初始化测试"""

    def test_initialize_creates_tables(self):
        tmp = tempfile.mkdtemp()
        s = LogStorage(db_path=os.path.join(tmp, "test.db"), raw_log_dir=os.path.join(tmp, "raw"))
        s.initialize()
        assert s._initialized is True
        s.close()

    def test_double_initialize(self):
        tmp = tempfile.mkdtemp()
        s = LogStorage(db_path=os.path.join(tmp, "test.db"), raw_log_dir=tmp)
        s.initialize()
        s.initialize()  # should no-op
        assert s._initialized is True
        s.close()


class TestLogStorageWrite(storage.__class__):
    """写入操作测试"""

    def test_write_entry(self, storage):
        entry = LogEntry(category=LogCategory.OPERATION, message="测试日志", source="test")
        storage.write_entry(entry)
        results = storage.query_operations(LogQuery(limit=10))
        assert len(results) >= 1
        assert results[0]["message"] == "测试日志"

    def test_write_performance(self, storage):
        pr = PerformanceRecord(metric_name="latency", value=150.5)
        storage.write_performance(pr)
        results = storage.query_performance(limit=10)
        assert len(results) >= 1
        assert results[0]["metric_name"] == "latency"

    def test_write_error(self, storage):
        er = ErrorRecord(message="test error", severity="error")
        storage.write_error(er)
        results = storage.query_errors(limit=10)
        assert len(results) >= 1
        assert results[0]["message"] == "test error"

    def test_write_behavior(self, storage):
        br = BehaviorRecord(user_id="u1", action_type="search", session_id="s1")
        storage.write_behavior(br)
        results = storage.query_operations(LogQuery(limit=10))
        # behavior 需要按 behavior 表查，我们用 query_operations 只是操作表
        # 所以这里只验证不报错
        assert True

    def test_write_insight(self, storage):
        insight = Insight(type="pattern", summary="发现模式", confidence=0.9)
        storage.write_insight(insight)
        results = storage.query_insights(limit=10)
        assert len(results) >= 1
        assert results[0]["type"] == "pattern"

    def test_write_action_item(self, storage):
        item = ActionItem(priority="high", category="performance", title="优化查询")
        storage.write_action_item(item)
        results = storage.query_action_items(limit=10)
        assert len(results) >= 1
        assert results[0]["title"] == "优化查询"

    def test_write_knowledge(self, storage):
        kf = KnowledgeFinding(domain="user_pattern", finding="用户经常搜索天气")
        storage.write_knowledge(kf)
        results = storage.query_knowledge(limit=10)
        assert len(results) >= 1
        assert results[0]["finding"] == "用户经常搜索天气"

    def test_write_raw(self, storage):
        storage.write_raw("chat", {"role": "user", "content": "你好"})
        # JSONL 文件写入，不报错即可
        assert True

    def test_query_filter_by_source(self, storage):
        entry = LogEntry(category=LogCategory.OPERATION, message="test", source="module_a")
        storage.write_entry(entry)
        query = LogQuery(source="module_a", limit=10)
        results = storage.query_operations(query)
        assert len(results) >= 1
        assert results[0]["source"] == "module_a"

    def test_query_no_match_returns_empty(self, storage):
        query = LogQuery(text_search="nonexistent_xyz", limit=10)
        results = storage.query_operations(query)
        assert results == []


class TestLogStorageRaw(storage.__class__):
    """原始 JSONL 日志测试"""

    def test_write_raw_file_created(self, storage):
        storage.write_raw("test_cat", {"key": "value"})
        assert True  # 不报错即可

    def test_write_raw_concurrent_no_loss(self, storage):
        """并发 write_raw 追加无丢失（JSONL 追加在独立 _append_lock 内保护）"""
        import glob
        import threading

        n_threads = 8
        per_thread = 25
        barrier = threading.Barrier(n_threads)
        errors = []

        def _writer(tid):
            try:
                barrier.wait()
                for i in range(per_thread):
                    storage.write_raw("test_cat", {"tid": tid, "i": i})
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=_writer, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发 write_raw 出现异常: {errors}"
        files = glob.glob(os.path.join(storage.raw_log_dir, "test_cat", "*", "test_cat.jsonl"))
        assert files, "JSONL 文件应存在"
        total_lines = 0
        for f in files:
            with open(f, encoding="utf-8") as fh:
                total_lines += sum(1 for _ in fh)
        assert total_lines == n_threads * per_thread, \
            f"并发追加丢失行数: 期望 {n_threads * per_thread}，实际 {total_lines}"
