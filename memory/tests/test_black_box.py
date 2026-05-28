"""黑匣子日志单元测试"""
import json
import tempfile
from pathlib import Path
import pytest
from memory.black_box import BlackBox


@pytest.fixture
def bb(tmp_path):
    return BlackBox(log_dir=str(tmp_path), max_size_bytes=500, max_files=3)


def test_log_and_query(bb):
    """记录事件后应能查询到"""
    event_id = bb.log("test_event", {"key": "value"})
    assert event_id is not None
    results = bb.query()
    assert len(results) == 1
    assert results[0]["event_type"] == "test_event"
    assert results[0]["data"]["key"] == "value"


def test_query_by_event_type(bb):
    """应按事件类型过滤"""
    bb.log("type_a", {})
    bb.log("type_b", {})
    bb.log("type_a", {})
    results = bb.query(event_type="type_a")
    assert len(results) == 2
    for r in results:
        assert r["event_type"] == "type_a"


def test_query_by_time_range(bb):
    """应按时间范围过滤"""
    bb.log("event1", {})
    bb.log("event2", {})
    results = bb.query(start="2099-01-01")
    assert len(results) == 0


def test_query_with_search(bb):
    """应按关键字搜索 data 字段"""
    bb.log("test", {"message": "hello world"})
    bb.log("test", {"message": "goodbye world"})
    results = bb.query(search="hello")
    assert len(results) == 1
    assert results[0]["data"]["message"] == "hello world"


def test_query_limit(bb):
    """应支持 limit 限制返回条数"""
    for i in range(5):
        bb.log("test", {"i": i})
    results = bb.query(limit=3)
    assert len(results) == 3


def test_analyze_distribution(bb):
    """analyze 应返回事件类型分布"""
    bb.log("a", {})
    bb.log("a", {})
    bb.log("b", {})
    dist = bb.analyze()
    assert dist["a"] == 2
    assert dist["b"] == 1


def test_file_rotation(bb):
    """超过 max_size 应创建新文件"""
    for i in range(20):
        bb.log("test", {"data": "x" * 50})
    log_dir = Path(bb.log_dir)
    files = sorted(log_dir.glob("blackbox_*.jsonl"))
    assert len(files) >= 2


def test_max_files_limit(bb):
    """超过 max_files 应删除最旧文件"""
    for i in range(50):
        bb.log("test", {"data": "x" * 100})
    log_dir = Path(bb.log_dir)
    files = sorted(log_dir.glob("blackbox_*.jsonl"))
    assert len(files) <= 3
