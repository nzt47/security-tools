"""
Memory 模块单元测试

测试 VectorStore 和 KnowledgeBase 的完整功能
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
import shutil
from agent.memory import VectorStore, MemoryItem, KnowledgeBase


def test_memory_item():
    """测试 MemoryItem 数据类"""
    print("\n" + "=" * 70)
    print("[ITEM] 测试 MemoryItem 数据类")
    print("=" * 70)
    
    item = MemoryItem(
        id="test_001",
        content="这是一条测试记忆",
        metadata={"type": "test", "tags": ["unit-test"]},
        timestamp="2026-05-31T12:00:00"
    )
    
    print(f"[OK] 创建 MemoryItem: {item.id}")
    print(f"   Content: {item.content}")
    print(f"   Metadata: {item.metadata}")
    print(f"   Timestamp: {item.timestamp}")
    
    item_dict = item.to_dict()
    print(f"[OK] to_dict(): {type(item_dict)} - {len(item_dict)} fields")
    
    item2 = MemoryItem.from_dict(item_dict)
    print(f"[OK] from_dict(): {item2.id} == {item.id}? {item2.id == item.id}")
    print(f"[OK] Content consistency: {item2.content == item.content}")
    
    return item2.id == item.id and item2.content == item.content


def test_vector_store_basic():
    """测试 VectorStore 基本操作"""
    print("\n" + "=" * 70)
    print("[BASIC] 测试 VectorStore 基本操作")
    print("=" * 70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        vs = VectorStore(
            collection_name="test_basic",
            persist_dir=tmpdir
        )
        
        print(f"[OK] 初始化 VectorStore")
        print(f"   Storage type: {vs.get_stats()['type']}")
        print(f"   Initial count: {vs.count}")
        
        item_id1 = vs.add("学习 Python 编程", {"subject": "编程", "level": "初级"})
        print(f"[OK] 添加记忆 #1: {item_id1}")
        
        item_id2 = vs.add("掌握 Flask Web 开发框架", {"subject": "Web", "level": "中级"})
        print(f"[OK] 添加记忆 #2: {item_id2}")
        
        item_id3 = vs.add("深入理解 Django REST Framework", {"subject": "API", "level": "高级"})
        print(f"[OK] 添加记忆 #3: {item_id3}")
        
        print(f"\n[STATS] 当前记忆数量: {vs.count}")
        
        return vs.count == 3


def test_vector_store_search():
    """测试 VectorStore 搜索功能"""
    print("\n" + "=" * 70)
    print("[SEARCH] 测试 VectorStore 搜索功能")
    print("=" * 70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        vs = VectorStore(
            collection_name="test_search",
            persist_dir=tmpdir
        )
        
        vs.add("Python 基础语法和数据类型", {"type": "note"})
        vs.add("Flask 快速入门指南", {"type": "tutorial"})
        vs.add("Django REST Framework 教程", {"type": "tutorial"})
        vs.add("Python 面向对象编程", {"type": "note"})
        vs.add("数据库设计和 SQL 基础", {"type": "note"})
        
        print(f"[OK] 添加了 5 条测试记忆")
        
        results1 = vs.search("Python", top_k=3)
        print(f"\n[QUERY] 搜索 'Python':")
        print(f"   找到 {len(results1)} 条结果")
        for i, item in enumerate(results1, 1):
            print(f"   {i}. {item.content[:40]}...")
        
        results2 = vs.search("Django REST", top_k=2)
        print(f"\n[QUERY] 搜索 'Django REST':")
        print(f"   找到 {len(results2)} 条结果")
        for i, item in enumerate(results2, 1):
            print(f"   {i}. {item.content[:40]}...")
        
        results3 = vs.search("不存在的内容", top_k=5)
        print(f"\n[QUERY] 搜索 '不存在的内容':")
        print(f"   找到 {len(results3)} 条结果 (应为 0)")
        
        return len(results1) >= 2 and len(results2) >= 1 and len(results3) == 0


def test_vector_store_recent():
    """测试获取最近记忆"""
    print("\n" + "=" * 70)
    print("[RECENT] 测试获取最近记忆")
    print("=" * 70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        vs = VectorStore(
            collection_name="test_recent",
            persist_dir=tmpdir
        )
        
        for i in range(10):
            vs.add(f"记忆条目 #{i+1}", {"index": i})
        
        print(f"[OK] 添加了 10 条记忆")
        
        recent5 = vs.get_recent(limit=5)
        print(f"\n[RECENT] 获取最近 5 条记忆:")
        for i, item in enumerate(recent5, 1):
            print(f"   {i}. {item.content}")
        
        recent3 = vs.get_recent(limit=3)
        print(f"\n[RECENT] 获取最近 3 条记忆:")
        for i, item in enumerate(recent3, 1):
            print(f"   {i}. {item.content}")
        
        return len(recent5) == 5 and len(recent3) == 3


def test_vector_store_persistence():
    """测试 VectorStore 持久化功能"""
    print("\n" + "=" * 70)
    print("[PERSIST] 测试 VectorStore 持久化功能")
    print("=" * 70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        vs1 = VectorStore(
            collection_name="test_persist",
            persist_dir=tmpdir
        )
        
        vs1.add("持久化测试记忆 #1", {"test": "persist"})
        vs1.add("持久化测试记忆 #2", {"test": "persist"})
        vs1.add("持久化测试记忆 #3", {"test": "persist"})
        
        print(f"[OK] 写入实例: {vs1.count} 条记忆")
        
        vs2 = VectorStore(
            collection_name="test_persist",
            persist_dir=tmpdir
        )
        
        print(f"[OK] 读取实例: {vs2.count} 条记忆")
        
        results = vs2.search("持久化测试记忆")
        print(f"\n[QUERY] 搜索 '持久化测试记忆':")
        print(f"   找到 {len(results)} 条结果")
        
        return vs2.count == 3 and len(results) == 3


def test_vector_store_clear():
    """测试清空记忆"""
    print("\n" + "=" * 70)
    print("[CLEAR] 测试清空记忆")
    print("=" * 70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        vs = VectorStore(
            collection_name="test_clear",
            persist_dir=tmpdir
        )
        
        vs.add("记忆 #1", {"test": "clear"})
        vs.add("记忆 #2", {"test": "clear"})
        vs.add("记忆 #3", {"test": "clear"})
        
        print(f"[OK] 添加后: {vs.count} 条记忆")
        
        vs.clear()
        print(f"[OK] 清空后: {vs.count} 条记忆")
        
        vs.add("新记忆", {"test": "new"})
        print(f"[OK] 添加新记忆后: {vs.count} 条记忆")
        
        return vs.count == 1


def test_vector_store_stats():
    """测试统计信息"""
    print("\n" + "=" * 70)
    print("[STATS] 测试统计信息")
    print("=" * 70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        vs = VectorStore(
            collection_name="test_stats",
            persist_dir=tmpdir
        )
        
        vs.add("统计测试 #1", {"test": "stats"})
        vs.add("统计测试 #2", {"test": "stats"})
        
        stats = vs.get_stats()
        print(f"[OK] 获取统计信息:")
        print(f"   Storage type: {stats['type']}")
        print(f"   Memory count: {stats['count']}")
        print(f"   Persist dir: {stats['persist_dir']}")
        print(f"   Collection name: {stats['collection_name']}")
        
        return stats['count'] == 2


def test_knowledge_base():
    """测试 KnowledgeBase"""
    print("\n" + "=" * 70)
    print("[KB] 测试 KnowledgeBase")
    print("=" * 70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        kb_store = VectorStore(
            collection_name="test_knowledge_base",
            persist_dir=tmpdir
        )
        kb = KnowledgeBase(store=kb_store)
        
        kb.add_document(
            "Python 是一种高级编程语言",
            "Python官网",
            ["编程语言", "Python"]
        )
        print("[OK] 添加文档 #1: Python官网")
        
        kb.add_document(
            "Flask 是一个轻量级的 Web 框架",
            "Flask文档",
            ["Web开发", "Flask"]
        )
        print("[OK] 添加文档 #2: Flask文档")
        
        kb.add_document(
            "Django 是一个高级 Python Web 框架",
            "Django文档",
            ["Web开发", "Django"]
        )
        print("[OK] 添加文档 #3: Django文档")
        
        print(f"\n[STATS] 知识库状态: {kb.store.count} 条文档")
        
        result1 = kb.query("Python")
        print(f"\n[QUERY] 查询 'Python':")
        print(f"   {result1[:100]}...")
        
        result2 = kb.query("Web框架")
        print(f"\n[QUERY] 查询 'Web框架':")
        print(f"   {result2[:100]}...")
        
        result3 = kb.query("机器学习")
        print(f"\n[QUERY] 查询 '机器学习':")
        print(f"   {result3}")
        
        return kb.store.count == 3


def test_empty_handling():
    """测试空输入处理"""
    print("\n" + "=" * 70)
    print("[EMPTY] 测试空输入处理")
    print("=" * 70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        vs = VectorStore(
            collection_name="test_empty",
            persist_dir=tmpdir
        )
        
        item_id = vs.add("")
        print(f"[OK] 添加空内容: ID = {item_id}")
        print(f"   Current total: {vs.count}")
        
        results = vs.search("")
        print(f"   Search empty string: {len(results)} results")
        
        return vs.count == 1


def run_all_tests():
    """运行所有测试"""
    print("\n")
    print("=" * 70)
    print(">>> Memory 模块完整测试套件 <<<")
    print("=" * 70)
    
    results = {
        "MemoryItem": test_memory_item(),
        "VectorStore基本操作": test_vector_store_basic(),
        "VectorStore搜索": test_vector_store_search(),
        "VectorStore最近记忆": test_vector_store_recent(),
        "VectorStore持久化": test_vector_store_persistence(),
        "VectorStore清空": test_vector_store_clear(),
        "VectorStore统计": test_vector_store_stats(),
        "KnowledgeBase": test_knowledge_base(),
        "空输入处理": test_empty_handling(),
    }
    
    print("\n" + "=" * 70)
    print("[SUMMARY] 测试结果汇总")
    print("=" * 70)
    
    for test_name, passed in results.items():
        status = "[PASS]" if passed else "[FAIL]"
        print(f"   {test_name:20s}: {status}")
    
    all_passed = all(results.values())
    print("\n" + "=" * 70)
    if all_passed:
        print("[SUCCESS] All tests passed! Memory module is working!")
    else:
        failed = [name for name, passed in results.items() if not passed]
        print(f"[ERROR] {len(failed)} tests failed: {', '.join(failed)}")
    print("=" * 70)
    
    return all_passed


if __name__ == "__main__":
    run_all_tests()
