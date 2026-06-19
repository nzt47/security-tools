#!/usr/bin/env python3
"""
为集成代码添加详细日志
"""

def add_detailed_logs():
    """添加详细日志到digital_life.py"""
    
    file_path = "agent/digital_life.py"
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    print("📝 开始添加详细日志...")
    
    # 1. 修复重复的代码块
    print("\n1. 修复重复代码...")
    duplicate_block = '''        # ── 9. 向量记忆系统（新增） ──
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
            self._knowledge_base = None
        self._safety_monitor: AgentSafetyMonitor = get_safety_monitor()
        logger.info("[ok] 安全监控器已激活")'''
    
    fixed_block = '''        # ── 9. 向量记忆系统（新增） ──
        if _MEMORY_AVAILABLE:
            try:
                memory_config = config.get("vector_memory", {})
                self._vector_memory = VectorStore(
                    collection_name=memory_config.get("collection_name", "agent_memory"),
                    persist_dir=memory_config.get("persist_dir", "./data/memory")
                )
                self._knowledge_base = KnowledgeBase(self._vector_memory)
                logger.info("[ok] 向量记忆系统已激活")
                logger.info(f"   ├─ 集合名称: {memory_config.get('collection_name', 'agent_memory')}")
                logger.info(f"   ├─ 持久化目录: {memory_config.get('persist_dir', './data/memory')}")
                logger.info(f"   └─ 当前记忆数: {len(self._vector_memory.items)}")
            except Exception as e:
                logger.error(f"初始化向量记忆系统失败: {e}")
                import traceback
                logger.error(f"堆栈: {traceback.format_exc()}")
                self._vector_memory = None
                self._knowledge_base = None
        else:
            logger.warning("向量记忆模块未安装，功能受限")
            self._vector_memory = None
            self._knowledge_base = None'''
    
    content = content.replace(duplicate_block, fixed_block)
    print("   ✅ 修复完成")
    
    # 2. 修改_call_llm中的向量记忆检索
    print("\n2. 添加记忆检索详细日志...")
    old_memory_search = '''                # 优先使用向量记忆检索
                if self._vector_memory:
                    related_memories = self._vector_memory.search(user_input, top_k=3)
                    if related_memories:
                        memory_context = "\\n【相关历史对话】\\n"
                        for mem in related_memories:
                            memory_context += f"- {mem.content}\\n"'''
    
    new_memory_search = '''                # 优先使用向量记忆检索
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
                        logger.error(f"堆栈: {traceback.format_exc()}")'''
    
    if old_memory_search in content:
        content = content.replace(old_memory_search, new_memory_search)
        print("   ✅ 记忆检索日志添加成功")
    else:
        print("   ⚠️ 记忆检索代码已添加过日志，跳过")
    
    # 3. 修改对话保存逻辑
    print("\n3. 添加对话保存详细日志...")
    old_save = '''        # ── 第七步：向量记忆保存（新增） ──
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
                logger.warning(f"保存向量记忆失败: {e}")'''
    
    new_save = '''        # ── 第七步：向量记忆保存（新增） ──
        if self._vector_memory:
            try:
                memory_content = f"用户: {user_input}\\n云枢: {response}"
                logger.info("💾 保存对话到向量记忆...")
                logger.info(f"   ├─ 对话编号: {self._interaction_count}")
                logger.info(f"   ├─ 用户输入: {user_input[:50]}...")
                logger.info(f"   └─ 云枢回复: {response[:50]}...")
                
                item_id = self._vector_memory.add(
                    content=memory_content,
                    metadata={
                        "type": "conversation",
                        "interaction": self._interaction_count
                    }
                )
                logger.info(f"   ✅ 保存成功，记忆ID: {item_id}")
                logger.info(f"   └─ 当前总记忆数: {len(self._vector_memory.items)}")
            except Exception as e:
                logger.error(f"❌ 保存向量记忆失败: {e}")
                logger.error(f"   └─ 错误详情: {str(e)}")
                import traceback
                logger.error(f"堆栈: {traceback.format_exc()}")'''
    
    if old_save in content:
        content = content.replace(old_save, new_save)
        print("   ✅ 对话保存日志添加成功")
    else:
        print("   ⚠️ 对话保存代码已添加过日志，跳过")
    
    # 4. 修复_call_llm中的缩进问题
    print("\n4. 修复原有记忆获取逻辑...")
    old_context = '''                # 降级为原有记忆
                if not memory_context:
                    context_messages = self._memory.get_context(token_limit=2048)
                summary = self._memory.load_summary()'''
    
    new_context = '''                # 降级为原有记忆
                if not memory_context:
                    try:
                        context_messages = self._memory.get_context(token_limit=2048)
                        summary = self._memory.load_summary()'''
    
    if old_context in content:
        content = content.replace(old_context, new_context)
        print("   ✅ 缩进修复成功")
    else:
        print("   ⚠️ 代码已是最新版本")
    
    # 修复后续的缩进
    old_indent = '''                if summary:
                    memory_context = f"我有过去的记忆：\\n{summary[0][:500]}"
                elif context_messages:
                    recent = context_messages[-3:]
                    memory_context = "最近对话：\\n" + "\\n".join(
                        f"{m['role']}: {m['content'][:200]}"
                        for m in recent if m.get('content')
                    )
            except Exception as e:
                logger.warning(f"获取记忆上下文失败: {e}")
                memory_context = ""'''
    
    new_indent = '''                    summary = self._memory.load_summary()
                    if summary:
                        memory_context = f"我有过去的记忆：\\n{summary[0][:500]}"
                    elif context_messages:
                        recent = context_messages[-3:]
                        memory_context = "最近对话：\\n" + "\\n".join(
                            f"{m['role']}: {m['content'][:200]}"
                            for m in recent if m.get('content')
                        )
                    logger.info("   └─ 原有记忆获取完成")
                except Exception as e:
                    logger.warning(f"获取原有记忆上下文失败: {e}")
                    memory_context = ""'''
    
    if old_indent in content:
        content = content.replace(old_indent, new_indent)
        print("   ✅ 后续缩进修复成功")
    else:
        print("   ⚠️ 后续代码已是最新版本")
    
    # 5. 添加记忆管理方法的详细日志
    print("\n5. 添加记忆管理方法日志...")
    
    old_stats = '''    def get_memory_stats(self) -> dict:
        """获取向量记忆统计"""
        if not self._vector_memory:
            return {"available": False}
        
        return {
            "available": True,
            "total_memories": len(self._vector_memory.items),
            "collection_name": self._vector_memory.collection_name,
            "persist_dir": self._vector_memory.persist_dir,
        }'''
    
    new_stats = '''    def get_memory_stats(self) -> dict:
        """获取向量记忆统计"""
        logger.info("📊 [get_memory_stats] 获取记忆统计...")
        if not self._vector_memory:
            logger.info("   └─ 向量记忆不可用")
            return {"available": False}
        
        stats = {
            "available": True,
            "total_memories": len(self._vector_memory.items),
            "collection_name": self._vector_memory.collection_name,
            "persist_dir": self._vector_memory.persist_dir,
        }
        logger.info(f"   ├─ 可用状态: True")
        logger.info(f"   ├─ 总记忆数: {stats['total_memories']}")
        logger.info(f"   ├─ 集合名称: {stats['collection_name']}")
        logger.info(f"   └─ 持久化目录: {stats['persist_dir']}")
        return stats'''
    
    if old_stats in content:
        content = content.replace(old_stats, new_stats)
        print("   ✅ 记忆统计日志添加成功")
    else:
        print("   ⚠️ 记忆统计代码已添加过日志")
    
    old_search = '''    def search_memory(self, query: str, top_k: int = 5) -> list:
        """搜索向量记忆"""
        if not self._vector_memory:
            return []
        return self._vector_memory.search(query, top_k)'''
    
    new_search = '''    def search_memory(self, query: str, top_k: int = 5) -> list:
        """搜索向量记忆"""
        logger.info(f"🔍 [search_memory] 搜索记忆: '{query[:50]}...', top_k={top_k}")
        if not self._vector_memory:
            logger.info("   └─ 向量记忆不可用，返回空列表")
            return []
        
        try:
            results = self._vector_memory.search(query, top_k)
            logger.info(f"   ├─ 检索到 {len(results)} 条相关记忆")
            for i, mem in enumerate(results, 1):
                logger.info(f"   │  {i}. {mem.content[:60]}...")
            logger.info(f"   └─ 搜索完成")
            return results
        except Exception as e:
            logger.error(f"❌ 搜索记忆失败: {e}")
            import traceback
            logger.error(f"堆栈: {traceback.format_exc()}")
            return []'''
    
    if old_search in content:
        content = content.replace(old_search, new_search)
        print("   ✅ 记忆搜索日志添加成功")
    else:
        print("   ⚠️ 记忆搜索代码已添加过日志")
    
    old_clear = '''    def clear_memory(self):
        """清空向量记忆"""
        if self._vector_memory:
            self._vector_memory.clear()
            logger.info("向量记忆已清空")'''
    
    new_clear = '''    def clear_memory(self):
        """清空向量记忆"""
        logger.info("🧹 [clear_memory] 清空向量记忆...")
        if self._vector_memory:
            try:
                before_count = len(self._vector_memory.items)
                self._vector_memory.clear()
                logger.info(f"   ├─ 清空前记忆数: {before_count}")
                logger.info(f"   └─ ✅ 清空成功，当前记忆数: 0")
            except Exception as e:
                logger.error(f"❌ 清空记忆失败: {e}")
                import traceback
                logger.error(f"堆栈: {traceback.format_exc()}")
        else:
            logger.warning("   ⚠️ 向量记忆不可用，无需清空")'''
    
    if old_clear in content:
        content = content.replace(old_clear, new_clear)
        print("   ✅ 记忆清空日志添加成功")
    else:
        print("   ⚠️ 记忆清空代码已添加过日志")
    
    # 保存文件
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("\n✅ digital_life.py 日志增强完成！")

def add_logs_to_vector_store():
    """为vector_store.py添加详细日志"""
    
    file_path = "agent/memory/vector_store.py"
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    print("\n6. 为vector_store.py添加详细日志...")
    
    # 1. __init__ 日志
    old_init = '''        os.makedirs(persist_dir, exist_ok=True)
        self._load()
        
        logger.info(f"向量存储初始化完成: {collection_name}")'''
    
    new_init = '''        os.makedirs(persist_dir, exist_ok=True)
        self._load()
        
        logger.info(f"向量存储初始化完成: {collection_name}")
        logger.info(f"   ├─ 集合名称: {collection_name}")
        logger.info(f"   ├─ 持久化目录: {persist_dir}")
        logger.info(f"   ├─ 文件路径: {self.file_path}")
        logger.info(f"   └─ 当前记忆数: {len(self.items)}")'''
    
    if old_init in content:
        content = content.replace(old_init, new_init)
        print("   ✅ 初始化日志添加成功")
    else:
        print("   ⚠️ 初始化日志已存在")
    
    # 2. _load 日志
    old_load = '''    def _load(self):
        """从文件加载"""
        self.items: List[MemoryItem] = []
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.items = [MemoryItem(**item) for item in data]
                logger.debug(f"加载了 {len(self.items)} 条记忆")
            except Exception as e:
                logger.warning(f"加载记忆失败: {e}")'''
    
    new_load = '''    def _load(self):
        """从文件加载"""
        logger.info(f"📂 加载记忆文件: {self.file_path}")
        self.items: List[MemoryItem] = []
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.items = [MemoryItem(**item) for item in data]
                logger.info(f"   ├─ 文件存在: True")
                logger.info(f"   ├─ 加载记忆数: {len(self.items)}")
                logger.info(f"   └─ ✅ 加载成功")
            except FileNotFoundError:
                logger.info(f"   ├─ 文件存在: False")
                logger.info(f"   └─ ✅ 新建空记忆库")
            except Exception as e:
                logger.warning(f"加载记忆失败: {e}")
                import traceback
                logger.warning(f"堆栈: {traceback.format_exc()}")'''
    
    if old_load in content:
        content = content.replace(old_load, new_load)
        print("   ✅ 文件加载日志添加成功")
    else:
        print("   ⚠️ 文件加载日志已存在")
    
    # 3. _save 日志
    old_save = '''    def _save(self):
        """保存到文件"""
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump([item.to_dict() for item in self.items], f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存记忆失败: {e}")'''
    
    new_save = '''    def _save(self):
        """保存到文件"""
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump([item.to_dict() for item in self.items], f, ensure_ascii=False, indent=2)
            logger.debug(f"💾 保存成功: {len(self.items)} 条记忆")
        except Exception as e:
            logger.error(f"❌ 保存记忆失败: {e}")
            import traceback
            logger.error(f"堆栈: {traceback.format_exc()}")'''
    
    if old_save in content:
        content = content.replace(old_save, new_save)
        print("   ✅ 文件保存日志添加成功")
    else:
        print("   ⚠️ 文件保存日志已存在")
    
    # 4. add 日志
    old_add = '''        self.items.append(item)
        self._save()
        
        logger.debug(f"添加记忆: {item_id}")'''
    
    new_add = '''        self.items.append(item)
        self._save()
        
        logger.info(f"✅ 添加记忆: {item_id}")
        logger.info(f"   ├─ 内容: {content[:60]}...")
        logger.info(f"   ├─ 元数据: {metadata}")
        logger.info(f"   └─ 当前总数: {len(self.items)}")'''
    
    if old_add in content:
        content = content.replace(old_add, new_add)
        print("   ✅ 添加记忆日志添加成功")
    else:
        print("   ⚠️ 添加记忆日志已存在")
    
    # 5. search 日志
    old_search = '''    def search(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """关键词搜索（简化版语义检索）
        
        Args:
            query: 查询文本
            top_k: 返回数量
            
        Returns:
            匹配的记忆项列表
        """
        # 改进的匹配算法：支持中文和模糊匹配
        results = []
        query_lower = query.lower()'''
    
    new_search = '''    def search(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """关键词搜索（简化版语义检索）
        
        Args:
            query: 查询文本
            top_k: 返回数量
            
        Returns:
            匹配的记忆项列表
        """
        logger.info(f"🔍 搜索记忆: query='{query[:50]}...', top_k={top_k}")
        logger.info(f"   └─ 当前记忆总数: {len(self.items)}")
        
        # 改进的匹配算法：支持中文和模糊匹配
        results = []
        query_lower = query.lower()'''
    
    if old_search in content:
        content = content.replace(old_search, new_search)
        print("   ✅ 搜索日志添加成功")
    else:
        print("   ⚠️ 搜索日志已存在")
    
    # 6. search结果日志
    old_search_result = '''        # 按分数排序
        results.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in results[:top_k]]
    
    def get_recent'''
    
    new_search_result = '''        # 按分数排序
        results.sort(key=lambda x: x[0], reverse=True)
        final_results = [item for _, item in results[:top_k]]
        
        logger.info(f"   ├─ 匹配结果数: {len(final_results)}")
        for i, item in enumerate(final_results, 1):
            logger.info(f"   │  {i}. [分数: {item.metadata.get('created_at', 'N/A')}] {item.content[:50]}...")
        logger.info(f"   └─ 返回: {len(final_results)} 条")
        
        return final_results
    
    def get_recent'''
    
    if old_search_result in content:
        content = content.replace(old_search_result, new_search_result)
        print("   ✅ 搜索结果日志添加成功")
    else:
        print("   ⚠️ 搜索结果日志已存在")
    
    # 7. clear 日志
    old_clear = '''    def clear(self):
        """清空记忆"""
        self.items = []
        self._save()
        logger.info("记忆已清空")'''
    
    new_clear = '''    def clear(self):
        """清空记忆"""
        before_count = len(self.items)
        self.items = []
        self._save()
        logger.info(f"🗑️ 记忆已清空")
        logger.info(f"   ├─ 清空前: {before_count} 条")
        logger.info(f"   └─ 清空后: 0 条")'''
    
    if old_clear in content:
        content = content.replace(old_clear, new_clear)
        print("   ✅ 清空日志添加成功")
    else:
        print("   ⚠️ 清空日志已存在")
    
    # 保存文件
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("   ✅ vector_store.py 日志增强完成")

def main():
    """主函数"""
    print("=" * 70)
    print("🚀 添加详细日志")
    print("=" * 70)
    
    try:
        add_detailed_logs()
        add_logs_to_vector_store()
        
        print("\n" + "=" * 70)
        print("🎉 所有日志添加完成！")
        print("=" * 70)
        print("\n下一步：")
        print("1. 运行 python test_integration.py 查看详细日志输出")
        print("2. 查看日志格式：INFO级别，包含关键操作和堆栈信息")
        
    except Exception as e:
        print(f"\n❌ 操作失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
