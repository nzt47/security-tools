# 🛡️ P1阶段：高级记忆功能规划

基于向量记忆模块，现在可以扩展以下高级功能。

---

## 🎯 1. 多用户记忆隔离

### 问题背景
当前系统所有用户共享同一个记忆库，需要支持：
- 不同用户的对话历史隔离
- 用户间的记忆可以共享或私有
- 管理员可以跨用户查询

### 技术方案

```python
# agent/memory/multi_user_store.py

class MultiUserVectorStore:
    """多用户向量存储"""
    
    def __init__(self, base_dir: str = "./data/memory"):
        self.base_dir = base_dir
        self.user_stores: Dict[str, VectorStore] = {}
        self._shared_store = VectorStore(
            collection_name="shared_memory",
            persist_dir=base_dir
        )
    
    def get_user_store(self, user_id: str) -> VectorStore:
        """获取用户专属的记忆存储"""
        if user_id not in self.user_stores:
            self.user_stores[user_id] = VectorStore(
                collection_name=f"user_{user_id}",
                persist_dir=f"{self.base_dir}/users/{user_id}"
            )
        return self.user_stores[user_id]
    
    def add(self, user_id: str, content: str, 
            metadata: Dict = None, shared: bool = False):
        """添加记忆，可选择是否共享"""
        metadata = metadata or {}
        metadata["user_id"] = user_id
        
        if shared:
            self._shared_store.add(content, metadata)
        else:
            self.get_user_store(user_id).add(content, metadata)
    
    def search(self, user_id: str, query: str, 
               include_shared: bool = True, top_k: int = 5):
        """搜索用户记忆，包含共享记忆"""
        results = []
        
        # 用户私有记忆
        user_results = self.get_user_store(user_id).search(query, top_k)
        results.extend(user_results)
        
        # 共享记忆（标记来源）
        if include_shared:
            shared_results = self._shared_store.search(query, top_k)
            results.extend(shared_results)
        
        return sorted(results, key=lambda x: x.timestamp, reverse=True)[:top_k]
```

### 使用示例

```python
# 不同用户添加记忆
memory_store.add("user_001", "张三喜欢Python编程")
memory_store.add("user_002", "李四是前端开发者")

# 用户查询自己的记忆 + 共享记忆
results = memory_store.search("user_001", "编程")
# 包含张三的记忆 + 共享记忆
```

---

## ⏰ 2. 记忆过期机制

### 问题背景
记忆无限增长会导致：
- 存储空间浪费
- 检索性能下降
- 语义相关性降低

### 技术方案

```python
# agent/memory/expirable_store.py

from datetime import datetime, timedelta

class ExpirableVectorStore(VectorStore):
    """带过期机制的向量存储"""
    
    def __init__(self, *args, 
                 default_ttl_days: int = 30,
                 auto_cleanup: bool = True,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.default_ttl_days = default_ttl_days
        self.auto_cleanup = auto_cleanup
    
    def add(self, content: str, metadata: Dict = None,
            ttl_days: int = None) -> str:
        """添加记忆，指定过期时间"""
        metadata = metadata or {}
        ttl_days = ttl_days or self.default_ttl_days
        
        metadata["expires_at"] = (
            datetime.now() + timedelta(days=ttl_days)
        ).isoformat()
        
        return super().add(content, metadata)
    
    def search(self, query: str, top_k: int = 5,
               include_expired: bool = False) -> List[MemoryItem]:
        """搜索，排除过期记忆"""
        all_results = super().search(query, top_k * 2)  # 获取更多
        
        if not include_expired:
            now = datetime.now()
            all_results = [
                item for item in all_results
                if not self._is_expired(item.metadata)
            ]
        
        return all_results[:top_k]
    
    def _is_expired(self, metadata: Dict) -> bool:
        """检查是否过期"""
        expires_at = metadata.get("expires_at")
        if not expires_at:
            return False
        return datetime.fromisoformat(expires_at) < datetime.now()
    
    def cleanup_expired(self) -> int:
        """清理过期记忆"""
        if self.auto_cleanup:
            before = len(self.items)
            self.items = [
                item for item in self.items
                if not self._is_expired(item.metadata)
            ]
            self._save()
            removed = before - len(self.items)
            if removed > 0:
                logger.info(f"清理了 {removed} 条过期记忆")
            return removed
        return 0
```

### 过期策略建议

| 记忆类型 | 建议过期时间 | 说明 |
|---------|-------------|------|
| 临时会话 | 1-7天 | 一次性对话 |
| 用户偏好 | 30-90天 | 习惯和偏好可能变化 |
| 知识文档 | 永久/定期更新 | 知识库文档 |
| 系统日志 | 7-30天 | 操作记录 |

---

## 📊 3. 记忆重要性评分

### 问题背景
并非所有对话都值得长期记忆：
- 需要自动评估记忆重要性
- 高重要性记忆长期保留
- 低重要性记忆快速过期

### 技术方案

```python
# agent/memory/importance_scorer.py

class ImportanceScorer:
    """记忆重要性评分器"""
    
    def __init__(self, llm_service=None):
        self.llm = llm_service
    
    def score(self, content: str) -> float:
        """评估内容重要性 (0-1)"""
        
        # 规则1：关键词触发
        important_keywords = [
            "重要", "必须", "关键", "记住",
            "不要忘记", "务必", "约定", "承诺"
        ]
        if any(kw in content for kw in important_keywords):
            return 0.8
        
        # 规则2：长度适中（太短可能是客套话）
        word_count = len(content)
        if 10 < word_count < 500:
            return 0.6
        
        # 规则3：包含数字或日期
        if any(char.isdigit() for char in content):
            return 0.7
        
        # 规则4：使用LLM深度评估
        if self.llm and len(content) > 50:
            try:
                return self._llm_score(content)
            except:
                pass
        
        return 0.5  # 默认中等重要性
    
    def _llm_score(self, content: str) -> float:
        """使用LLM评估重要性"""
        response = self.llm.chat([
            {"role": "user", "content": f"""
评估以下内容的记忆重要性（0-1分数）：

{content}

只返回一个数字，不要其他内容。
- 1.0: 必须记住的重要信息（密码、约定、重要决定）
- 0.5: 普通的对话内容
- 0.0: 完全不值得记忆（客套话、问候）
"""}
        ])
        
        try:
            return float(response.strip())
        except:
            return 0.5
```

### 智能过期策略

```python
def calculate_ttl(importance_score: float, base_ttl: int = 30) -> int:
    """根据重要性计算过期时间"""
    # 重要性高 → 保留时间长
    # 重要性低 → 保留时间短
    ttl_multiplier = importance_score * 2  # 0-2倍基础时间
    return int(base_ttl * ttl_multiplier)
```

---

## 🔧 4. 记忆自动摘要

### 问题背景
长对话保存会占用大量空间，需要：
- 自动总结长对话
- 提取关键信息
- 保留对话精华

### 技术方案

```python
# agent/memory/summarizer.py

class MemorySummarizer:
    """记忆自动摘要"""
    
    def __init__(self, llm_service):
        self.llm = llm_service
    
    def should_summarize(self, conversation: List[dict]) -> bool:
        """判断是否需要摘要"""
        total_length = sum(
            len(msg.get("content", ""))
            for msg in conversation
        )
        return total_length > 2000  # 超过2000字符
    
    def summarize(self, conversation: List[dict]) -> str:
        """生成摘要"""
        conversation_text = "\n".join([
            f"{msg['role']}: {msg['content']}"
            for msg in conversation
        ])
        
        prompt = f"""请总结以下对话的要点：

{conversation_text}

请用简洁的语言总结（200字以内）：
1. 主要讨论的主题
2. 达成的共识或结论
3. 需要记住的重要信息
"""
        
        summary = self.llm.chat([{"role": "user", "content": prompt}])
        return summary
    
    def merge_memories(self, memories: List[MemoryItem]) -> str:
        """合并多条记忆"""
        content_text = "\n".join([
            f"- {mem.content}"
            for mem in memories
        ])
        
        prompt = f"""将以下相关记忆合并为一个简洁的摘要：

{content_text}

保留所有重要信息，去除重复。
"""
        
        return self.llm.chat([{"role": "user", "content": prompt}])
```

---

## 🎯 5. 完整集成方案

### 多层记忆架构

```
DigitalLife
│
├── L1: 工作记忆 (Working Memory)
│   └── 当前对话上下文 (自动管理)
│
├── L2: 情景记忆 (Episodic Memory)
│   ├── 用户对话历史
│   ├── 短期事件记录
│   └── 重要性筛选 + 自动过期
│
├── L3: 语义记忆 (Semantic Memory)
│   ├── 知识库文档
│   ├── 用户偏好
│   └── 长期稳定信息
│
└── L4: 共享记忆 (Shared Memory)
    └── 跨用户共享信息
```

### 统一API

```python
class UnifiedMemorySystem:
    """统一记忆系统"""
    
    def __init__(self):
        self.working = WorkingMemory()
        self.episodic = ExpirableVectorStore(collection_name="episodic")
        self.semantic = KnowledgeBase()
        self.shared = VectorStore(collection_name="shared")
    
    def remember(self, user_id: str, content: str,
                 memory_type: str = "episodic", **kwargs):
        """统一存储接口"""
        if memory_type == "episodic":
            scorer = ImportanceScorer()
            score = scorer.score(content)
            ttl = calculate_ttl(score)
            self.episodic.add(content, {
                "user_id": user_id,
                "importance": score,
                **kwargs
            }, ttl_days=ttl)
        
        elif memory_type == "semantic":
            self.semantic.add_document(content, **kwargs)
        
        elif memory_type == "shared":
            self.shared.add(content, {
                "user_id": user_id,
                "shared": True,
                **kwargs
            })
    
    def recall(self, user_id: str, query: str) -> str:
        """统一检索接口"""
        # 搜索所有层级
        results = []
        
        # 情景记忆
        episodic = self.episodic.search(query, top_k=3)
        results.extend(episodic)
        
        # 语义记忆
        semantic = self.semantic.query(query)
        results.append(semantic)
        
        # 共享记忆
        shared = self.shared.search(query, top_k=2)
        results.extend(shared)
        
        return self._format_results(results)
```

---

## 📋 实施优先级

| 功能 | 优先级 | 工作量 | 价值 | 建议 |
|------|--------|--------|------|------|
| 多用户隔离 | ⭐⭐⭐⭐⭐ | 中 | 高 | 立即实现 |
| 记忆过期 | ⭐⭐⭐⭐ | 低 | 高 | 立即实现 |
| 重要性评分 | ⭐⭐⭐ | 中 | 中 | 第二阶段 |
| 自动摘要 | ⭐⭐⭐ | 高 | 中 | 第三阶段 |

---

## 🚀 下一步建议

1. **立即实现**：多用户隔离 + 记忆过期（代码量小，效果明显）
2. **快速验证**：使用现有 `agent/memory/` 模块进行测试
3. **逐步扩展**：根据实际使用情况添加其他功能

您希望我先帮您实现哪个功能？
