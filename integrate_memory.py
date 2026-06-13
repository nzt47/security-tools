#!/usr/bin/env python3
"""
自动集成向量记忆模块到DigitalLife
"""

import re

def integrate_vector_memory():
    """执行集成修改"""
    
    file_path = "agent/digital_life.py"
    
    # 读取原文件
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    print("📝 开始集成向量记忆模块...")
    
    # 第一步：添加导入
    print("\n1. 添加向量记忆模块导入...")
    if "from agent.memory import VectorStore, KnowledgeBase" not in content:
        old_import = "_PLANNING_AVAILABLE = False\n\n# ── 默认系统提示词模板 ──"
        new_import = """_PLANNING_AVAILABLE = False

# ── 新增：向量记忆模块导入 ──
try:
    from agent.memory import VectorStore, KnowledgeBase
    _MEMORY_AVAILABLE = True
    logger.info("[ok] 向量记忆模块已加载")
except ImportError as e:
    logger.warning(f"向量记忆模块导入失败: {e}")
    _MEMORY_AVAILABLE = False

# ── 默认系统提示词模板 ──"""
        content = content.replace(old_import, new_import)
        print("   ✅ 导入添加成功")
    else:
        print("   ⚠️ 导入已存在，跳过")
    
    # 第二步：添加初始化
    print("\n2. 添加向量记忆系统初始化...")
    init_marker = "# ── 8. 安全监控器（新增） ──"
    init_code = '''# ── 8. 安全监控器（新增） ──
        self._safety_monitor: AgentSafetyMonitor = get_safety_monitor()
        logger.info("[ok] 安全监控器已激活")

        # ── 9. 向量记忆系统（新增） ──
        if _MEMORY_AVAILABLE:
            memory_config = config.get("vector_memory", {})
            self._vector_memory = VectorStore(
                collection_name=memory_config.get("collection_name", "agent_memory"),
                persist_dir=memory_config.get("persist_dir", "./data/memory")
            )
            self._knowledge_base = KnowledgeBase(self._vector_memory)
            logger.info("[ok] 向量记忆系统已激活")
        else:
            self._vector_memory = None
            self._knowledge_base = None'''
    
    if "self._vector_memory" not in content:
        content = content.replace(init_marker, init_code)
        print("   ✅ 初始化代码添加成功")
    else:
        print("   ⚠️ 初始化代码已存在，跳过")
    
    # 第三步：修改_call_llm方法，添加记忆检索
    print("\n3. 修改_call_llm方法，添加向量记忆检索...")
    
    old_memory_context = '''# 获取记忆上下文
        memory_context = ""
        if self._llm:
            try:
                context_messages = self._memory.get_context(token_limit=2048)'''
    
    new_memory_context = '''# 获取记忆上下文（新增向量记忆）
        memory_context = ""
        if self._llm:
            try:
                # 优先使用向量记忆检索
                if self._vector_memory:
                    related_memories = self._vector_memory.search(user_input, top_k=3)
                    if related_memories:
                        memory_context = "\\n【相关历史对话】\\n"
                        for mem in related_memories:
                            memory_context += f"- {mem.content}\\n"
                
                # 降级为原有记忆
                if not memory_context:
                    context_messages = self._memory.get_context(token_limit=2048)'''
    
    if "related_memories = self._vector_memory.search(user_input, top_k=3)" not in content:
        content = content.replace(old_memory_context, new_memory_context)
        print("   ✅ 记忆检索代码添加成功")
    else:
        print("   ⚠️ 记忆检索代码已存在，跳过")
    
    # 第四步：在_process_user_input中添加记忆保存
    print("\n4. 添加对话记忆保存...")
    
    old_save = '''# ── 第六步：记忆 ──
        # 我要记住这次交互…
        self._memory.add_message("user", user_input)
        self._memory.add_message("assistant", response)

        return response'''
    
    new_save = '''# ── 第六步：记忆 ──
        # 我要记住这次交互…
        self._memory.add_message("user", user_input)
        self._memory.add_message("assistant", response)

        # ── 第七步：向量记忆保存（新增） ──
        if self._vector_memory:
            try:
                self._vector_memory.add(
                    content=f"用户: {user_input}\\n云枢: {response}",
                    metadata={
                        "type": "conversation",
                        "interaction": self._interaction_count
                    }
                )
                logger.debug("对话已保存到向量记忆")
            except Exception as e:
                logger.warning(f"保存向量记忆失败: {e}")

        return response'''
    
    if "向量记忆保存" not in content:
        content = content.replace(old_save, new_save)
        print("   ✅ 记忆保存代码添加成功")
    else:
        print("   ⚠️ 记忆保存代码已存在，跳过")
    
    # 第五步：添加记忆管理方法
    print("\n5. 添加记忆管理方法...")
    
    old_get_status = '''    # ════════════════════════════════════════════════════════════
    #  清理
    # ════════════════════════════════════════════════════════════'''
    
    new_get_status = '''    def get_memory_stats(self) -> dict:
        """获取向量记忆统计"""
        if not self._vector_memory:
            return {"available": False}
        
        return {
            "available": True,
            "total_memories": len(self._vector_memory.items),
            "collection_name": self._vector_memory.collection_name,
            "persist_dir": self._vector_memory.persist_dir,
        }

    def search_memory(self, query: str, top_k: int = 5) -> list:
        """搜索向量记忆"""
        if not self._vector_memory:
            return []
        return self._vector_memory.search(query, top_k)

    def clear_memory(self):
        """清空向量记忆"""
        if self._vector_memory:
            self._vector_memory.clear()
            logger.info("向量记忆已清空")

    # ════════════════════════════════════════════════════════════
    #  清理
    # ════════════════════════════════════════════════════════════'''
    
    if "def get_memory_stats" not in content:
        content = content.replace(old_get_status, new_get_status)
        print("   ✅ 记忆管理方法添加成功")
    else:
        print("   ⚠️ 记忆管理方法已存在，跳过")
    
    # 保存文件
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("\n✅ digital_life.py 修改完成！")

def update_init():
    """更新__init__.py"""
    
    file_path = "agent/__init__.py"
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    print("\n6. 更新agent/__init__.py...")
    
    if "from agent.memory import VectorStore" not in content:
        content += "\n# 向量记忆模块\nfrom agent.memory import VectorStore, MemoryItem, KnowledgeBase\n"
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print("   ✅ __init__.py 更新成功")
    else:
        print("   ⚠️ __init__.py 已包含向量记忆导出，跳过")

def main():
    """主函数"""
    print("=" * 60)
    print("🚀 向量记忆模块集成脚本")
    print("=" * 60)
    
    try:
        integrate_vector_memory()
        update_init()
        
        print("\n" + "=" * 60)
        print("🎉 集成完成！")
        print("=" * 60)
        print("\n下一步：")
        print("1. 运行 python test_memory.py 验证基础功能")
        print("2. 创建并运行 test_integration.py 测试集成")
        
    except Exception as e:
        print(f"\n❌ 集成失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
