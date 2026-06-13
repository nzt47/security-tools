#!/usr/bin/env python3
"""
修复digital_life.py的完整语法错误
"""

def fix_complete():
    """完整修复语法错误"""
    
    file_path = "agent/digital_life.py"
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    print("FIXING: Complete syntax error fix...")
    
    # 替换整个有问题的代码段
    old_block = '''        # 获取记忆上下文（新增向量记忆）
        memory_context = ""
        if self._llm:
            try:
                # 优先使用向量记忆检索
                if self._vector_memory:
                    try:
                        logger.info("🔍 开始向量记忆检索...")
                        related_memories = self._vector_memory.search(user_input, top_k=3)
                        logger.info(f"   ├─ 检索关键词: {user_input[:50]}...")
                        logger.info(f"   ├─ 检索结果数: {len(related_memories)}")
                        if related_memories:
                            memory_context = "\\n【相关历史对话】\\n"
                            for i, mem in enumerate(related_memories, 1):
                                logger.info(f"   │  结果{i}: {mem.content[:60]}...")
                                memory_context += f"- {mem.content}\\n"
                            logger.info(f"   └─ 上下文构建完成")
                        else:
                            logger.info("   └─ 未找到相关记忆，使用原有记忆")
                    except Exception as e:
                        logger.error(f"向量记忆检索失败: {e}")
                        logger.error(f"   └─ 降级为原有记忆")
                        import traceback
                        logger.error(f"堆栈: {traceback.format_exc()}")
                
                # 降级为原有记忆
                elif not memory_context:
                    try:
                        logger.info("📚 使用原有记忆管理器...")
                        context_messages = self._memory.get_context(token_limit=2048)
                        summary = self._memory.load_summary()
                        if summary:
                            memory_context = f"我有过去的记忆：\\n{summary[0][:500]}"
                            logger.info(f"   ├─ 摘要长度: {len(summary[0])}")
                        elif context_messages:
                            recent = context_messages[-3:]
                            memory_context = "最近对话：\\n" + "\\n".join(
                                f"{m['role']}: {m['content'][:200]}"
                                for m in recent if m.get('content')
                            )
                            logger.info(f"   ├─ 最近对话数: {len(recent)}")
                        logger.info("   └─ 原有记忆获取完成")
                    except Exception as e:
                        logger.warning(f"获取原有记忆上下文失败: {e}")
                        memory_context = ""'''
    
    new_block = '''        # 获取记忆上下文（新增向量记忆）
        memory_context = ""
        if self._llm:
            try:
                # 优先使用向量记忆检索
                if self._vector_memory:
                    try:
                        logger.info("Start vector memory search...")
                        related_memories = self._vector_memory.search(user_input, top_k=3)
                        logger.info(f"Search keyword: {user_input[:50]}...")
                        logger.info(f"Search results: {len(related_memories)}")
                        if related_memories:
                            memory_context = "\\n[Related History]\\n"
                            for i, mem in enumerate(related_memories, 1):
                                logger.info(f"Result {i}: {mem.content[:60]}...")
                                memory_context += f"- {mem.content}\\n"
                            logger.info("Context built successfully")
                        else:
                            logger.info("No related memories found, using fallback")
                    except Exception as e:
                        logger.error(f"Vector memory search failed: {e}")
                        logger.error("Falling back to original memory")
                        import traceback
                        logger.error(f"Stack: {traceback.format_exc()}")
                # 降级为原有记忆
                elif not memory_context:
                    try:
                        logger.info("Using original memory manager...")
                        context_messages = self._memory.get_context(token_limit=2048)
                        summary = self._memory.load_summary()
                        if summary:
                            memory_context = f"Memory: {summary[0][:500]}"
                            logger.info(f"Summary length: {len(summary[0])}")
                        elif context_messages:
                            recent = context_messages[-3:]
                            memory_context = "Recent: " + "\\n".join(
                                f"{m['role']}: {m['content'][:200]}"
                                for m in recent if m.get('content')
                            )
                            logger.info(f"Recent count: {len(recent)}")
                        logger.info("Original memory retrieval complete")
                    except Exception as e:
                        logger.warning(f"Failed to get memory context: {e}")
                        memory_context = ""
            except Exception as e:
                logger.warning(f"Memory context retrieval failed: {e}")
                memory_context = ""'''
    
    if old_block in content:
        content = content.replace(old_block, new_block)
        print("   Block replaced successfully")
    else:
        print("   Block not found, trying alternative...")
        # 尝试更简单的替换
        try:
            # 找到if self._llm:块并替换
            idx = content.find("if self._llm:")
            if idx > 0:
                # 找到下一个方法定义
                end_idx = content.find("\n    def ", idx + 20)
                if end_idx > 0:
                    # 替换这段代码
                    new_code = '''        # 获取记忆上下文
        memory_context = ""
        if self._llm:
            try:
                if self._vector_memory:
                    try:
                        logger.info("Searching vector memory...")
                        related_memories = self._vector_memory.search(user_input, top_k=3)
                        if related_memories:
                            memory_context = "\\n[History]\\n"
                            for mem in related_memories:
                                memory_context += f"- {mem.content}\\n"
                    except Exception as e:
                        logger.warning(f"Vector search failed: {e}")
                
                if not memory_context:
                    context_messages = self._memory.get_context(token_limit=2048)
                    summary = self._memory.load_summary()
                    if summary:
                        memory_context = f"Memory: {summary[0][:500]}"
            except Exception as e:
                logger.warning(f"Memory context failed: {e}")
'''
                    content = content[:idx] + new_code + content[end_idx:]
                    print("   Alternative replacement successful")
        except Exception as e:
            print(f"   Alternative failed: {e}")
            return False
    
    # 保存文件
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("\nFile saved successfully")
    return True

if __name__ == "__main__":
    if fix_complete():
        print("\nSUCCESS: File fixed")
    else:
        print("\nFAILED: Fix did not work")
