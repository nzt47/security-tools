# 🚀 向量记忆模块集成指南

## 📝 需要修改的文件

### 1. 修改 `agent/digital_life.py`

#### 第一步：添加导入（第33-43行后添加）

```python
# ── 新增：向量记忆模块导入 ──
try:
    from agent.memory import VectorStore, KnowledgeBase
    _MEMORY_AVAILABLE = True
    logger.info("[ok] 向量记忆模块已加载")
except ImportError as e:
    logger.warning(f"向量记忆模块导入失败: {e}")
    _MEMORY_AVAILABLE = False
```

#### 第二步：在 `__init__` 方法中添加记忆初始化（第169行后添加）

```python
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
    self._knowledge_base = None
```

#### 第三步：修改 `_call_llm` 方法，添加记忆检索（第542-558行替换）

```python
# 获取记忆上下文（新增向量记忆）
memory_context = ""
if self._llm:
    try:
        # 优先使用向量记忆检索
        if self._vector_memory:
            related_memories = self._vector_memory.search(user_input, top_k=3)
            if related_memories:
                memory_context = "\n【相关历史对话】\n"
                for mem in related_memories:
                    memory_context += f"- {mem.content}\n"
        
        # 降级为原有记忆
        if not memory_context:
            context_messages = self._memory.get_context(token_limit=2048)
            summary = self._memory.load_summary()
            if summary:
                memory_context = f"我有过去的记忆：\n{summary[0][:500]}"
            elif context_messages:
                recent = context_messages[-3:]
                memory_context = "最近对话：\n" + "\n".join(
                    f"{m['role']}: {m['content'][:200]}"
                    for m in recent if m.get('content')
                )
    except Exception as e:
        logger.warning(f"获取记忆上下文失败: {e}")
        memory_context = ""
```

#### 第四步：在 `_process_user_input` 方法中添加记忆保存（第506-508行后添加）

```python
# ── 第七步：向量记忆保存（新增） ──
if self._vector_memory:
    try:
        self._vector_memory.add(
            content=f"用户: {user_input}\n云枢: {response}",
            metadata={
                "type": "conversation",
                "interaction": self._interaction_count
            }
        )
        logger.debug("对话已保存到向量记忆")
    except Exception as e:
        logger.warning(f"保存向量记忆失败: {e}")
```

#### 第五步：添加记忆管理方法（在 `get_status` 方法后添加）

```python
def get_memory_stats(self) -> dict:
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
```

---

### 2. 修改 `agent/__init__.py`

在文件末尾添加向量记忆导出：

```python
# 向量记忆模块
from agent.memory import VectorStore, MemoryItem, KnowledgeBase
```

---

## ✅ 快速验证脚本

创建 `test_integration.py`：

```python
#!/usr/bin/env python3
"""测试向量记忆集成"""

from agent.digital_life import DigitalLife

def test_integration():
    print("=" * 60)
    print("测试向量记忆集成")
    print("=" * 60)
    
    # 创建实例
    agent = DigitalLife()
    agent.start()
    
    # 检查记忆系统
    print("\n📊 记忆系统状态:")
    print(f"  向量记忆可用: {agent._vector_memory is not None}")
    print(f"  知识库可用: {agent._knowledge_base is not None}")
    
    if agent._vector_memory:
        print(f"  当前记忆数: {len(agent._vector_memory.items)}")
    
    # 测试记忆统计
    print("\n📈 记忆统计:")
    stats = agent.get_memory_stats()
    for k, v in stats.items():
        print(f"  {k}: {v}")
    
    # 测试对话并保存记忆
    print("\n💬 测试对话:")
    response = agent.chat("你好，我是张三，我喜欢编程")
    print(f"  云枢回复: {response[:100]}...")
    
    # 检查记忆保存
    if agent._vector_memory:
        print(f"\n  记忆数量: {len(agent._vector_memory.items)}")
        print("\n🔍 搜索'张三':")
        results = agent.search_memory("张三")
        for i, mem in enumerate(results, 1):
            print(f"  {i}. {mem.content[:50]}...")
    
    agent.stop()
    print("\n✅ 集成测试完成！")

if __name__ == "__main__":
    test_integration()
```

---

## 🎯 使用建议

### 1. 立即可用的功能
- ✅ 对话自动保存到向量记忆
- ✅ 基于语义的记忆检索
- ✅ 知识库管理

### 2. 可选的增强功能
- 🔧 多用户记忆隔离
- 🔧 记忆过期机制
- 🔧 记忆重要性评分
- 🔧 自动总结长对话

---

## 📊 集成后的架构

```
DigitalLife
├── 向量记忆系统 (VectorStore)
│   ├── 对话历史持久化
│   ├── 语义检索
│   └── 多用户隔离（待实现）
├── 知识库 (KnowledgeBase)
│   ├── 文档存储
│   └── 上下文检索
└── 原有MemoryManager
    └── token级别的记忆管理
```

---

## ⚠️ 注意事项

1. **性能考虑**：向量记忆会在每次对话时保存，建议定期清理
2. **存储空间**：长期运行后数据量会增长，可配置自动过期
3. **兼容性**：与原有的MemoryManager并存，互为补充

完成集成后，运行 `python test_integration.py` 验证！
