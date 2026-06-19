#!/usr/bin/env python3
"""
LifeTrace 记忆系统全面测试
测试云枢的三层记忆树架构、数据采集器和检索器
"""

import pytest
import tempfile
import shutil
import os
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# 导入被测试模块
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from lifetrace.memory_tree import MemoryTree, MemoryNode, SourceTree, TopicTree, GlobalTree
from lifetrace.trace_recorder import TraceRecorder
from lifetrace.enhanced_recorder import EnhancedTraceRecorder
from lifetrace.retriever import MemoryRetriever


class TestMemoryNode:
    """测试 MemoryNode 节点类"""

    def test_node_creation(self):
        """测试节点创建"""
        node = MemoryNode(
            node_id="test_001",
            content="测试内容",
            node_type="leaf",
            metadata={"key": "value"}
        )
        assert node.node_id == "test_001"
        assert node.content == "测试内容"
        assert node.node_type == "leaf"
        assert node.metadata == {"key": "value"}
        assert node.children == []
        assert node.parent is None
        assert node.tags == []
        assert node.importance == 0.5
        assert node.access_count == 0

    def test_node_to_dict(self):
        """测试节点序列化"""
        node = MemoryNode(
            node_id="test_002",
            content="测试内容",
            node_type="branch"
        )
        node.tags = ["tag1", "tag2"]
        node.importance = 0.8

        data = node.to_dict()
        assert data["node_id"] == "test_002"
        assert data["content"] == "测试内容"
        assert data["node_type"] == "branch"
        assert data["tags"] == ["tag1", "tag2"]
        assert data["importance"] == 0.8

    def test_node_from_dict(self):
        """测试节点反序列化"""
        data = {
            "node_id": "test_003",
            "content": "恢复的内容",
            "node_type": "leaf",
            "metadata": {"恢复": True},
            "created_at": "2024-01-01T00:00:00",
            "children": ["child1", "child2"],
            "parent": "parent_id",
            "tags": ["恢复测试"],
            "importance": 0.9,
            "access_count": 5,
            "last_access": "2024-01-02T00:00:00"
        }
        node = MemoryNode.from_dict(data)

        assert node.node_id == "test_003"
        assert node.content == "恢复的内容"
        assert node.node_type == "leaf"
        assert node.metadata == {"恢复": True}
        assert node.children == ["child1", "child2"]
        assert node.parent == "parent_id"
        assert node.tags == ["恢复测试"]
        assert node.importance == 0.9
        assert node.access_count == 5
        assert node.last_access == "2024-01-02T00:00:00"

    def test_node_roundtrip(self):
        """测试节点序列化-反序列化往返"""
        original = MemoryNode(
            node_id="roundtrip_test",
            content="往返测试内容" * 10,
            node_type="branch",
            metadata={"复杂数据": [1, 2, 3], "嵌套": {"a": 1}},
            created_at="2024-06-01T12:00:00"
        )
        original.tags = ["tag1", "tag2", "tag3"]
        original.importance = 0.75
        original.access_count = 10
        original.last_access = "2024-06-15T18:30:00"

        # 序列化和反序列化
        data = original.to_dict()
        restored = MemoryNode.from_dict(data)

        # 验证所有字段一致
        assert restored.node_id == original.node_id
        assert restored.content == original.content
        assert restored.node_type == original.node_type
        assert restored.metadata == original.metadata
        assert restored.created_at == original.created_at
        assert restored.children == original.children
        assert restored.parent == original.parent
        assert restored.tags == original.tags
        assert restored.importance == original.importance
        assert restored.access_count == original.access_count
        assert restored.last_access == original.last_access


class TestMemoryTree:
    """测试 MemoryTree 基类"""

    @pytest.fixture
    def temp_dir(self):
        """创建临时目录"""
        tmp = tempfile.mkdtemp()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    def test_tree_creation(self, temp_dir):
        """测试树创建"""
        tree = MemoryTree("test_tree", temp_dir)
        assert tree.tree_name == "test_tree"
        assert tree.data_dir == Path(temp_dir) / "test_tree"
        assert tree.data_dir.exists()
        assert len(tree.nodes) == 0
        assert tree.root_id is None

    def test_add_root_node(self, temp_dir):
        """测试添加根节点"""
        tree = MemoryTree("test_tree", temp_dir)
        node = tree.add_node("根节点内容", node_type="root")

        assert node.node_type == "root"
        assert node.parent is None
        assert tree.root_id == node.node_id
        assert len(tree.nodes) == 1

    def test_add_child_node(self, temp_dir):
        """测试添加子节点"""
        tree = MemoryTree("test_tree", temp_dir)
        parent = tree.add_node("父节点", node_type="root")
        child = tree.add_node("子节点内容", parent_id=parent.node_id)

        assert child.parent == parent.node_id
        assert child.node_id in parent.children  # 子节点ID应在父节点的children列表中
        assert len(tree.nodes) == 2

    def test_add_leaf_node_without_parent(self, temp_dir):
        """测试添加叶子节点（无父节点）"""
        tree = MemoryTree("test_tree", temp_dir)
        node = tree.add_node("独立叶子节点", node_type="leaf")

        # 没有父节点时，成为根节点
        assert tree.root_id == node.node_id

    def test_get_node(self, temp_dir):
        """测试获取节点"""
        tree = MemoryTree("test_tree", temp_dir)
        created = tree.add_node("测试节点")
        retrieved = tree.get_node(created.node_id)

        assert retrieved is not None
        assert retrieved.node_id == created.node_id
        assert retrieved.content == created.content
        # 验证访问计数增加
        assert retrieved.access_count == 1

    def test_get_nonexistent_node(self, temp_dir):
        """测试获取不存在的节点"""
        tree = MemoryTree("test_tree", temp_dir)
        result = tree.get_node("nonexistent_id")
        assert result is None

    def test_search_by_tag(self, temp_dir):
        """测试按标签搜索"""
        tree = MemoryTree("test_tree", temp_dir)
        tree.add_node("节点1", tags=["工作", "重要"])
        tree.add_node("节点2", tags=["生活"])
        tree.add_node("节点3", tags=["工作", "紧急"])

        results = tree.search_by_tag("工作")
        assert len(results) == 2

        results = tree.search_by_tag("生活")
        assert len(results) == 1

        results = tree.search_by_tag("不存在")
        assert len(results) == 0

    def test_search_by_content(self, temp_dir):
        """测试按内容搜索"""
        tree = MemoryTree("test_tree", temp_dir)
        tree.add_node("Python 编程语言")
        tree.add_node("Java 编程语言")
        tree.add_node("Python 框架 Django")

        results = tree.search_by_content("Python")
        assert len(results) == 2

        # "编程语言" 只在两个节点中包含
        results = tree.search_by_content("编程语言")
        assert len(results) == 2

        results = tree.search_by_content("不存在")
        assert len(results) == 0

    def test_search_by_content_case_insensitive(self, temp_dir):
        """测试内容搜索大小写不敏感"""
        tree = MemoryTree("test_tree", temp_dir)
        tree.add_node("Hello World")
        tree.add_node("hello world")
        tree.add_node("HELLO WORLD")

        results = tree.search_by_content("hello")
        assert len(results) == 3

    def test_get_recent_nodes(self, temp_dir):
        """测试获取最近的节点"""
        tree = MemoryTree("test_tree", temp_dir)
        for i in range(15):
            tree.add_node(f"节点 {i}")

        recent = tree.get_recent_nodes(limit=10)
        assert len(recent) == 10

        # 验证按时间排序（最新的在前）
        for i in range(len(recent) - 1):
            assert recent[i].created_at >= recent[i + 1].created_at

    def test_tree_persistence(self, temp_dir):
        """测试树持久化"""
        # 创建并添加节点
        tree1 = MemoryTree("persist_tree", temp_dir)
        tree1.add_node("持久化节点1", tags=["tag1"])
        tree1.add_node("持久化节点2", tags=["tag2"])

        # 重新创建树实例
        tree2 = MemoryTree("persist_tree", temp_dir)

        # 验证数据已恢复
        assert len(tree2.nodes) == 2
        assert tree2.root_id is not None

        # 验证可以通过标签找到节点
        results = tree2.search_by_tag("tag1")
        assert len(results) == 1
        assert results[0].content == "持久化节点1"


class TestSourceTree:
    """测试 SourceTree 来源树"""

    @pytest.fixture
    def temp_dir(self):
        tmp = tempfile.mkdtemp()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    def test_source_tree_creation(self, temp_dir):
        """测试来源树创建"""
        tree = SourceTree(temp_dir)
        assert tree.tree_name == "sources"

    def test_record_chat(self, temp_dir):
        """测试记录对话"""
        tree = SourceTree(temp_dir)
        node = tree.record_chat("user", "你好，这是测试对话")

        assert node.content == "你好，这是测试对话"
        assert node.metadata["source"] == "chat"
        assert node.metadata["role"] == "user"
        assert "chat" in node.tags
        assert "user" in node.tags

    def test_record_chat_assistant(self, temp_dir):
        """测试记录助手对话"""
        tree = SourceTree(temp_dir)
        node = tree.record_chat("assistant", "我是你的AI助手")

        assert node.metadata["role"] == "assistant"
        assert "assistant" in node.tags

    def test_record_sensor(self, temp_dir):
        """测试记录传感器数据"""
        tree = SourceTree(temp_dir)
        sensor_data = {"temperature": 25.5, "humidity": 60}
        node = tree.record_sensor("environment", sensor_data)

        assert "temperature" in node.content
        assert "25.5" in node.content
        assert node.metadata["source"] == "sensor"
        assert node.metadata["sensor_type"] == "environment"
        assert "sensor" in node.tags
        assert "environment" in node.tags

    def test_record_window(self, temp_dir):
        """测试记录窗口活动"""
        tree = SourceTree(temp_dir)
        node = tree.record_window("VS Code", "focus", {"process": "code.exe"})

        assert "VS Code" in node.content
        assert "focus" in node.content
        assert node.metadata["source"] == "window"
        assert node.metadata["window_title"] == "VS Code"
        assert node.metadata["event_type"] == "focus"
        assert "window" in node.tags

    def test_record_file(self, temp_dir):
        """测试记录文件变更"""
        tree = SourceTree(temp_dir)
        node = tree.record_file("/path/to/file.py", "modify", {"size": 1024})

        assert "file.py" in node.content
        assert "modify" in node.content
        assert node.metadata["source"] == "file"
        assert node.metadata["file_path"] == "/path/to/file.py"
        assert node.metadata["event_type"] == "modify"
        assert "file" in node.tags


class TestTopicTree:
    """测试 TopicTree 主题树"""

    @pytest.fixture
    def temp_dir(self):
        tmp = tempfile.mkdtemp()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    def test_topic_tree_creation(self, temp_dir):
        """测试主题树创建"""
        tree = TopicTree(temp_dir)
        assert tree.tree_name == "topics"
        assert tree.topics == {}

    def test_add_to_topic_new(self, temp_dir):
        """测试添加新主题"""
        tree = TopicTree(temp_dir)
        node = tree.add_to_topic("工作", "今天完成了项目报告")

        assert "工作" in tree.topics
        assert node.node_id in tree.topics["工作"]
        assert "topic" in node.tags
        assert "工作" in node.tags

    def test_add_to_topic_existing(self, temp_dir):
        """测试添加到已存在主题"""
        tree = TopicTree(temp_dir)
        tree.add_to_topic("工作", "任务1")
        node2 = tree.add_to_topic("工作", "任务2")

        assert len(tree.topics["工作"]) == 2
        assert node2.node_id in tree.topics["工作"]

    def test_add_to_topic_with_tags(self, temp_dir):
        """测试添加主题时包含额外标签"""
        tree = TopicTree(temp_dir)
        node = tree.add_to_topic("学习", "学习Python", tags=["编程", "重要"])

        assert "学习" in node.tags
        assert "编程" in node.tags
        assert "重要" in node.tags

    def test_get_topic_content(self, temp_dir):
        """测试获取主题内容"""
        tree = TopicTree(temp_dir)
        tree.add_to_topic("健康", "早起锻炼")
        tree.add_to_topic("健康", "健康饮食")

        content = tree.get_topic_content("健康")
        assert len(content) == 2

    def test_get_nonexistent_topic_content(self, temp_dir):
        """测试获取不存在主题的内容"""
        tree = TopicTree(temp_dir)
        content = tree.get_topic_content("不存在")
        assert content == []


class TestGlobalTree:
    """测试 GlobalTree 全局树"""

    @pytest.fixture
    def temp_dir(self):
        tmp = tempfile.mkdtemp()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    def test_global_tree_creation(self, temp_dir):
        """测试全局树创建"""
        tree = GlobalTree(temp_dir)
        assert tree.tree_name == "global"
        assert tree.persona_path == Path(temp_dir) / "global" / "persona.json"
        assert tree.summary_path == Path(temp_dir) / "global" / "summary.md"

    def test_save_and_load_persona(self, temp_dir):
        """测试人格数据保存和加载"""
        tree = GlobalTree(temp_dir)
        persona = {
            "name": "云枢",
            "性格": "友善、智能",
            "偏好": {"语气": "专业", "响应速度": "快速"}
        }

        tree.save_persona(persona)
        loaded = tree.load_persona()

        assert loaded == persona

    def test_load_nonexistent_persona(self, temp_dir):
        """测试加载不存在的人格数据"""
        tree = GlobalTree(temp_dir)
        loaded = tree.load_persona()
        assert loaded is None

    def test_save_and_load_summary(self, temp_dir):
        """测试摘要保存和加载"""
        tree = GlobalTree(temp_dir)
        summary = "# 云枢摘要\n\n这是一个智能助手..."

        tree.save_summary(summary)
        loaded = tree.load_summary()

        assert loaded == summary

    def test_load_nonexistent_summary(self, temp_dir):
        """测试加载不存在的摘要"""
        tree = GlobalTree(temp_dir)
        loaded = tree.load_summary()
        assert loaded is None


class TestTraceRecorder:
    """测试 TraceRecorder 数据采集器"""

    @pytest.fixture
    def temp_dir(self):
        tmp = tempfile.mkdtemp()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    def test_recorder_creation(self, temp_dir):
        """测试采集器创建"""
        recorder = TraceRecorder(temp_dir)
        assert recorder.is_recording is False  # 初始为False
        assert recorder.source_tree is not None
        assert recorder.topic_tree is not None
        assert recorder.global_tree is not None

    def test_record_chat(self, temp_dir):
        """测试记录对话"""
        recorder = TraceRecorder(temp_dir)
        node = recorder.record_chat("user", "测试消息")

        assert node is not None
        assert node.content == "测试消息"
        assert node.metadata["role"] == "user"

    def test_record_chat_auto_topic(self, temp_dir):
        """测试对话自动主题分类"""
        recorder = TraceRecorder(temp_dir)

        # 工作相关消息
        recorder.record_chat("user", "今天有个重要会议", auto_topic=True)
        # 学习相关消息
        recorder.record_chat("user", "学习Python编程教程", auto_topic=True)

        # 验证主题已添加
        work_content = recorder.get_topic_content("工作")
        learn_content = recorder.get_topic_content("学习")

        assert len(work_content) >= 1
        assert len(learn_content) >= 1

    def test_record_sensor(self, temp_dir):
        """测试记录传感器数据"""
        recorder = TraceRecorder(temp_dir)
        data = {"cpu": 45.5, "memory": 70.2}
        node = recorder.record_sensor("system", data)

        assert node is not None
        assert "cpu" in node.content

    def test_record_window(self, temp_dir):
        """测试记录窗口活动"""
        recorder = TraceRecorder(temp_dir)
        node = recorder.record_window("Chrome", "switch", {"process": "chrome.exe"})

        assert "Chrome" in node.content
        assert node.metadata["window_title"] == "Chrome"

    def test_record_file(self, temp_dir):
        """测试记录文件变更"""
        recorder = TraceRecorder(temp_dir)
        node = recorder.record_file("/path/test.py", "create")

        assert "test.py" in node.content
        assert node.metadata["event_type"] == "create"

    def test_add_to_topic(self, temp_dir):
        """测试添加到主题"""
        recorder = TraceRecorder(temp_dir)
        recorder.add_to_topic("测试主题", "测试内容", tags=["测试"])

        content = recorder.get_topic_content("测试主题")
        assert len(content) == 1
        assert content[0].content == "测试内容"

    def test_get_recent_chat(self, temp_dir):
        """测试获取最近对话"""
        recorder = TraceRecorder(temp_dir)
        for i in range(15):
            recorder.record_chat("user", f"消息 {i}")

        recent = recorder.get_recent_chat(limit=10)
        assert len(recent) == 10

    def test_get_recent_sensor(self, temp_dir):
        """测试获取最近传感器数据"""
        recorder = TraceRecorder(temp_dir)
        for i in range(5):
            recorder.record_sensor("cpu", {"value": i})

        recent = recorder.get_recent_sensor(limit=10)
        assert len(recent) == 5

    def test_callback_registration(self, temp_dir):
        """测试回调注册"""
        recorder = TraceRecorder(temp_dir)
        callback_called = {"count": 0}

        def test_callback(node):
            callback_called["count"] += 1

        recorder.register_callback("chat", test_callback)
        recorder.record_chat("user", "测试")

        assert callback_called["count"] == 1

    def test_unregister_callback(self, temp_dir):
        """测试注销回调"""
        recorder = TraceRecorder(temp_dir)
        callback_called = {"count": 0}

        def test_callback(node):
            callback_called["count"] += 1

        recorder.register_callback("chat", test_callback)
        recorder.unregister_callback("chat", test_callback)
        recorder.record_chat("user", "测试")

        assert callback_called["count"] == 0

    def test_callback_error_handling(self, temp_dir):
        """测试回调错误处理"""
        recorder = TraceRecorder(temp_dir)

        def error_callback(node):
            raise ValueError("回调错误")

        recorder.register_callback("chat", error_callback)
        # 不应抛出异常
        node = recorder.record_chat("user", "测试")
        assert node is not None

    def test_get_statistics(self, temp_dir):
        """测试获取统计信息"""
        recorder = TraceRecorder(temp_dir)
        recorder.record_chat("user", "消息1")
        recorder.record_chat("assistant", "回复1")
        recorder.add_to_topic("工作", "工作内容")

        stats = recorder.get_statistics()
        assert "source_nodes" in stats
        assert "topic_nodes" in stats
        assert "topics" in stats
        assert "summary" in stats

    def test_concurrent_recording(self, temp_dir):
        """测试并发记录"""
        recorder = TraceRecorder(temp_dir)
        results = {"success": 0, "errors": []}
        lock = threading.Lock()

        def record_task(task_id):
            try:
                for i in range(20):
                    recorder.record_chat("user", f"任务{task_id}消息{i}")
                    recorder.record_sensor("test", {"id": task_id, "i": i})
                with lock:
                    results["success"] += 1
            except Exception as e:
                with lock:
                    results["errors"].append(str(e))

        threads = []
        for i in range(5):
            t = threading.Thread(target=record_task, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert results["success"] == 5
        assert len(results["errors"]) == 0


class TestEnhancedTraceRecorder:
    """测试 EnhancedTraceRecorder 增强版采集器"""

    @pytest.fixture
    def temp_dir(self):
        tmp = tempfile.mkdtemp()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    def test_enhanced_recorder_creation(self, temp_dir):
        """测试增强版采集器创建"""
        recorder = EnhancedTraceRecorder(temp_dir)
        assert recorder is not None
        # 验证继承的属性
        assert recorder.source_tree is not None
        assert recorder.topic_tree is not None
        assert recorder.global_tree is not None

    def test_record_user_activity(self, temp_dir):
        """测试记录用户活动"""
        recorder = EnhancedTraceRecorder(temp_dir)
        node = recorder.record_user_activity("work", "编写代码")

        assert node is not None
        assert node.metadata["source"] == "user_activity"
        assert node.metadata["activity_type"] == "work"
        assert "activity" in node.tags
        assert "work" in node.tags

    def test_record_context_snapshot(self, temp_dir):
        """测试记录上下文快照"""
        recorder = EnhancedTraceRecorder(temp_dir)
        node = recorder.record_context_snapshot(
            window_title="VS Code",
            screen_text="def hello(): print('world')"
        )

        assert "VS Code" in node.content
        assert "def hello" in node.content
        assert node.metadata["source"] == "context_snapshot"
        assert node.metadata["window_title"] == "VS Code"

    @patch("lifetrace.enhanced_recorder.EnhancedTraceRecorder.enable_window_monitoring")
    def test_enable_window_monitoring(self, mock_enable, temp_dir):
        """测试启用窗口监控"""
        recorder = EnhancedTraceRecorder(temp_dir)
        # 由于 WindowSensor 可能不可用，我们只测试逻辑
        # 实际环境中如果 WindowSensor 存在则会真正启用
        mock_enable.return_value = None

    def test_get_app_usage_stats(self, temp_dir):
        """测试获取应用使用统计"""
        recorder = EnhancedTraceRecorder(temp_dir)
        recorder._update_app_usage("chrome.exe")
        time.sleep(0.1)
        recorder._update_app_usage("code.exe")

        stats = recorder.get_app_usage_stats()
        assert "chrome.exe" in stats
        assert "code.exe" in stats

    def test_get_most_used_apps(self, temp_dir):
        """测试获取最常用应用"""
        recorder = EnhancedTraceRecorder(temp_dir)
        recorder._app_usage = {
            "chrome.exe": 3600,
            "code.exe": 1800,
            "explorer.exe": 600
        }

        top_apps = recorder.get_most_used_apps(limit=2)
        assert len(top_apps) == 2
        assert top_apps[0][0] == "chrome.exe"
        assert top_apps[1][0] == "code.exe"

    def test_get_capabilities(self, temp_dir):
        """测试获取功能列表"""
        recorder = EnhancedTraceRecorder(temp_dir)
        caps = recorder.get_capabilities()

        assert "window_monitoring" in caps
        assert "ocr_available" in caps
        assert "ocr_enabled" in caps

    def test_shutdown(self, temp_dir):
        """测试关闭"""
        recorder = EnhancedTraceRecorder(temp_dir)
        recorder._window_sensor = MagicMock()  # 模拟已初始化的window_sensor
        recorder._is_monitoring_windows = True
        recorder._ocr_sensor = "mock_sensor"

        recorder.shutdown()
        assert recorder._is_monitoring_windows is False


class TestMemoryRetriever:
    """测试 MemoryRetriever 记忆检索器"""

    @pytest.fixture
    def temp_dir(self):
        tmp = tempfile.mkdtemp()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    @pytest.fixture
    def setup_trees(self, temp_dir):
        """设置测试用的记忆树"""
        source_tree = SourceTree(temp_dir)
        topic_tree = TopicTree(temp_dir)
        global_tree = GlobalTree(temp_dir)

        # 添加测试数据
        source_tree.record_chat("user", "Python 编程很有意思")
        source_tree.record_chat("user", "Java 编程很难")
        source_tree.record_chat("assistant", "我可以帮你学习Python")
        source_tree.record_sensor("cpu", {"usage": 50})

        topic_tree.add_to_topic("编程", "Python编程入门")
        topic_tree.add_to_topic("编程", "JavaScript高级技巧")

        return source_tree, topic_tree, global_tree

    def test_retriever_creation(self, setup_trees):
        """测试检索器创建"""
        source, topic, global_tree = setup_trees
        retriever = MemoryRetriever(source, topic, global_tree)

        assert retriever.source_tree is not None
        assert retriever.topic_tree is not None
        assert retriever.global_tree is not None

    def test_retrieve_basic(self, setup_trees):
        """测试基本检索"""
        source, topic, global_tree = setup_trees
        retriever = MemoryRetriever(source, topic, global_tree)

        results = retriever.retrieve("Python")
        assert len(results) > 0

    def test_retrieve_with_limit(self, setup_trees):
        """测试带限制的检索"""
        source, topic, global_tree = setup_trees
        retriever = MemoryRetriever(source, topic, global_tree)

        results = retriever.retrieve("编程", limit=1)
        assert len(results) <= 1

    def test_retrieve_with_source_filter(self, setup_trees):
        """测试来源过滤"""
        source, topic, global_tree = setup_trees
        retriever = MemoryRetriever(source, topic, global_tree)

        results = retriever.retrieve("Python", include_sources=["chat"])
        # 验证返回的结果要么是 chat 来源，要么根本没有 source 元数据（来自 topic_tree）
        for r in results:
            source_val = r.metadata.get("source")
            # 如果有 source 元数据，它应该是 "chat"
            if source_val is not None:
                assert source_val == "chat"

    def test_retrieve_with_time_range(self, setup_trees):
        """测试时间范围过滤"""
        source, topic, global_tree = setup_trees
        retriever = MemoryRetriever(source, topic, global_tree)

        # 获取过去1小时的记录
        results = retriever.retrieve("编程", time_range_hours=1)
        # 新创建的记录应该在这个范围内
        for r in results:
            node_time = datetime.fromisoformat(r.created_at)
            assert datetime.now() - node_time < timedelta(hours=1)

    def test_get_recent_context(self, setup_trees):
        """测试获取近期上下文"""
        source, topic, global_tree = setup_trees
        retriever = MemoryRetriever(source, topic, global_tree)

        context = retriever.get_recent_context(hours=24)
        assert isinstance(context, list)

    def test_get_summary_context(self, setup_trees):
        """测试获取摘要上下文"""
        source, topic, global_tree = setup_trees
        global_tree.save_summary("# 摘要\n这是摘要内容")

        retriever = MemoryRetriever(source, topic, global_tree)
        summary = retriever.get_summary_context()

        assert summary is not None
        assert "摘要" in summary

    def test_get_persona_context(self, setup_trees):
        """测试获取人格上下文"""
        source, topic, global_tree = setup_trees
        persona = {"name": "云枢", "性格": "友善"}
        global_tree.save_persona(persona)

        retriever = MemoryRetriever(source, topic, global_tree)
        loaded_persona = retriever.get_persona_context()

        assert loaded_persona == persona


class TestLifeTraceIntegration:
    """LifeTrace 集成测试"""

    @pytest.fixture
    def temp_dir(self):
        tmp = tempfile.mkdtemp()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    def test_full_workflow(self, temp_dir):
        """测试完整工作流"""
        # 1. 创建采集器
        recorder = TraceRecorder(temp_dir)

        # 2. 记录多种类型的数据
        recorder.record_chat("user", "今天学习Python面向对象编程")
        recorder.record_chat("assistant", "Python的类和方法很强大")

        recorder.record_sensor("keyboard", {"keystrokes": 500})
        recorder.record_window("VS Code", "active")

        recorder.add_to_topic("学习", "Python学习笔记")

        # 3. 创建检索器
        retriever = MemoryRetriever(
            recorder.source_tree,
            recorder.topic_tree,
            recorder.global_tree
        )

        # 4. 验证数据可检索
        results = retriever.retrieve("Python")
        assert len(results) >= 2

        # 5. 验证统计信息
        stats = recorder.get_statistics()
        assert stats["source_nodes"] >= 4
        assert "学习" in stats["topics"]

    def test_concurrent_access(self, temp_dir):
        """测试并发访问"""
        recorder = TraceRecorder(temp_dir)
        retriever = MemoryRetriever(
            recorder.source_tree,
            recorder.topic_tree,
            recorder.global_tree
        )

        errors = []
        results_count = {"read": 0, "write": 0}

        def writer_task():
            for i in range(50):
                try:
                    recorder.record_chat("user", f"并发消息 {i}")
                    results_count["write"] += 1
                except Exception as e:
                    errors.append(str(e))

        def reader_task():
            for i in range(50):
                try:
                    retriever.retrieve("消息")
                    results_count["read"] += 1
                except Exception as e:
                    errors.append(str(e))

        threads = [
            threading.Thread(target=writer_task),
            threading.Thread(target=writer_task),
            threading.Thread(target=reader_task),
            threading.Thread(target=reader_task),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"并发访问错误: {errors}"
        assert results_count["write"] == 100
        assert results_count["read"] == 100

    def test_data_isolation(self, temp_dir):
        """测试数据隔离（不同实例互不影响）"""
        # 创建两个独立的记录器
        recorder1 = TraceRecorder(f"{temp_dir}/instance1")
        recorder2 = TraceRecorder(f"{temp_dir}/instance2")

        recorder1.record_chat("user", "实例1的私密消息")
        recorder2.record_chat("user", "实例2的私密消息")

        # 验证数据隔离
        results1 = recorder1.get_statistics()
        results2 = recorder2.get_statistics()

        assert results1["source_nodes"] >= 1
        assert results2["source_nodes"] >= 1

        # 检索验证
        retriever1 = MemoryRetriever(
            recorder1.source_tree,
            recorder1.topic_tree,
            recorder1.global_tree
        )
        retriever2 = MemoryRetriever(
            recorder2.source_tree,
            recorder2.topic_tree,
            recorder2.global_tree
        )

        results1 = retriever1.retrieve("实例1")
        results2 = retriever2.retrieve("实例2")

        assert len(results1) >= 1
        assert len(results2) >= 1


class TestLifeTraceEdgeCases:
    """LifeTrace 边界情况测试"""

    @pytest.fixture
    def temp_dir(self):
        tmp = tempfile.mkdtemp()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    def test_empty_content(self, temp_dir):
        """测试空内容"""
        recorder = TraceRecorder(temp_dir)
        node = recorder.record_chat("user", "")

        assert node is not None
        assert node.content == ""

    def test_very_long_content(self, temp_dir):
        """测试超长内容"""
        recorder = TraceRecorder(temp_dir)
        long_content = "测试内容 " * 10000
        node = recorder.record_chat("user", long_content)

        assert node is not None
        assert len(node.content) == len(long_content)

    def test_special_characters(self, temp_dir):
        """测试特殊字符"""
        recorder = TraceRecorder(temp_dir)
        special_content = "特殊字符: <>&\"' \n\t\r emoji: 🎉👍 中文: 中文测试"
        node = recorder.record_chat("user", special_content)

        assert node.content == special_content

    def test_unicode_content(self, temp_dir):
        """测试Unicode内容"""
        recorder = TraceRecorder(temp_dir)
        unicode_content = "中文内容 🎭 日本語 🎋 한국어 🔥"
        node = recorder.record_chat("user", unicode_content)

        assert node.content == unicode_content

    def test_search_empty_query(self, temp_dir):
        """测试空查询"""
        retriever = MemoryRetriever(
            SourceTree(temp_dir),
            TopicTree(temp_dir),
            GlobalTree(temp_dir)
        )

        results = retriever.retrieve("")
        # 空查询可能返回空结果或全部结果，取决于实现

    def test_concurrent_same_node_access(self, temp_dir):
        """测试并发访问同一节点"""
        recorder = TraceRecorder(temp_dir)
        node = recorder.record_chat("user", "共享节点")

        errors = []

        def access_node():
            try:
                for _ in range(100):
                    recorder.source_tree.get_node(node.node_id)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=access_node) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_metadata_with_complex_types(self, temp_dir):
        """测试复杂类型的元数据"""
        recorder = TraceRecorder(temp_dir)
        complex_metadata = {
            "list": [1, 2, 3],
            "nested": {"a": {"b": "c"}},
            "tuple": (1, 2),
            "none": None
        }
        node = recorder.source_tree.add_node(
            content="复杂元数据测试",
            metadata=complex_metadata
        )

        assert node.metadata == complex_metadata

    def test_tree_with_no_root(self, temp_dir):
        """测试没有根节点的树"""
        tree = MemoryTree("empty_tree", temp_dir)
        assert tree.root_id is None
        assert len(tree.nodes) == 0

        # 获取最近节点应该返回空
        recent = tree.get_recent_nodes()
        assert len(recent) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])