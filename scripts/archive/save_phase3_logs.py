#!/usr/bin/env python3
"""
运行 VectorStore 测试并保存日志到文件
"""

import sys
import os
import logging
from pathlib import Path

# 设置路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 日志文件路径
log_file = Path("phase3_step1_debug.log")

# 配置日志同时输出到文件和控制台
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 清除旧的处理器
for handler in logger.handlers[:]:
    logger.removeHandler(handler)

# 文件处理器
file_handler = logging.FileHandler(log_file, encoding="utf-8")
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# 控制台处理器
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

print("=" * 80)
print(f"  日志将保存到: {log_file}")
print("=" * 80)
print()

# 导入并运行测试
from memory.vector_store import VectorStore, KnowledgeBase
import tempfile


def test_vector_store_basic():
    logger.info("=" * 80)
    logger.info("  VectorStore 测试开始")
    logger.info("=" * 80)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        logger.info(f"使用临时目录: {temp_dir}")
        
        # 初始化
        logger.info("-" * 60)
        logger.info("步骤 1: 初始化 VectorStore")
        logger.info("-" * 60)
        store = VectorStore(collection_name="test_collection", persist_dir=temp_dir)
        logger.info("✅ VectorStore 初始化成功")
        
        # 添加记忆
        logger.info("")
        logger.info("-" * 60)
        logger.info("步骤 2: 添加测试记忆")
        logger.info("-" * 60)
        id1 = store.add("这是第一条测试记忆，关于Python编程", metadata={"category": "programming", "lang": "python"})
        id2 = store.add("这是第二条测试记忆，关于人工智能和机器学习", metadata={"category": "ai", "topic": "machine_learning"})
        id3 = store.add("第三条测试记忆，关于向量存储和数据库", metadata={"category": "database", "topic": "vector_db"})
        logger.info(f"✅ 已添加 3 条记忆")
        
        # 搜索
        logger.info("")
        logger.info("-" * 60)
        logger.info("步骤 3: 测试搜索")
        logger.info("-" * 60)
        results = store.search("人工智能", top_k=2)
        logger.info(f"✅ 搜索返回 {len(results)} 条结果")
        
        # 清空
        logger.info("")
        logger.info("-" * 60)
        logger.info("步骤 4: 清空记忆")
        logger.info("-" * 60)
        store.clear()
        logger.info("✅ 清空成功")
    
    logger.info("")
    logger.info("=" * 80)
    logger.info("  VectorStore 测试完成")
    logger.info("=" * 80)
    return True


def test_knowledge_base():
    logger.info("")
    logger.info("=" * 80)
    logger.info("  KnowledgeBase 测试开始")
    logger.info("=" * 80)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        logger.info(f"使用临时目录: {temp_dir}")
        
        store = VectorStore(collection_name="test_kb", persist_dir=temp_dir)
        kb = KnowledgeBase(store=store)
        
        logger.info("-" * 60)
        logger.info("步骤 1: 添加文档")
        logger.info("-" * 60)
        kb.add_document(
            content="Python是一种高级编程语言，简洁优雅",
            source="test_source_1",
            tags=["python", "programming"]
        )
        kb.add_document(
            content="向量数据库用于存储和检索向量嵌入",
            source="test_source_2",
            tags=["vector_db", "ai"]
        )
        logger.info("✅ 文档添加成功")
        
        logger.info("")
        logger.info("-" * 60)
        logger.info("步骤 2: 查询知识库")
        logger.info("-" * 60)
        result = kb.query("向量数据库是什么", top_k=2)
        logger.info(f"✅ 查询结果: {result}")
    
    logger.info("")
    logger.info("=" * 80)
    logger.info("  KnowledgeBase 测试完成")
    logger.info("=" * 80)
    return True


if __name__ == "__main__":
    try:
        success1 = test_vector_store_basic()
        success2 = test_knowledge_base()
        
        all_success = success1 and success2
        
        logger.info("")
        logger.info("=" * 80)
        if all_success:
            logger.info("  🎉 所有测试通过！")
        else:
            logger.info("  ⚠️ 部分测试失败")
        logger.info(f"  详细日志已保存到: {log_file}")
        logger.info("=" * 80)
        
        print(f"\n✅ 详细日志已保存到: {log_file}")
        
        sys.exit(0 if all_success else 1)
        
    except Exception as e:
        logger.error(f"❌ 测试异常: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)
