
"""
索引管理器
提供多维度索引以加速查询
"""

import logging
from typing import List, Dict, Any, Optional, Set
from collections import defaultdict
from datetime import datetime
import re

logger = logging.getLogger(__name__)
logger.info("[IndexManager] 加载索引管理器")


class IndexManager:
    """
    多维度索引管理器
    
    支持索引类型:
    - 全文索引 (FTS)
    - 时间索引
    - 分类索引
    - 关键词索引
    """
    
    def __init__(self):
        """初始化索引管理器"""
        logger.info("[IndexManager] 初始化")
        
        # 关键词索引: word -> set of item_ids
        self.keyword_index: Dict[str, Set[str]] = defaultdict(set)
        
        # 时间索引: date -> list of item_ids
        self.time_index: Dict[str, Set[str]] = defaultdict(set)
        
        # 分类索引: category -> set of item_ids
        self.category_index: Dict[str, Set[str]] = defaultdict(set)
        
        # ID 到 item 的映射
        self.id_to_item: Dict[str, Dict[str, Any]] = {}
        
        # 倒排索引: item_id -> set of keywords
        self.item_keywords: Dict[str, Set[str]] = defaultdict(set)
        
        logger.info("[IndexManager] 初始化完成")
    
    def _tokenize(self, text: str) -> Set[str]:
        """
        分词 (简单实现，支持中英文)
        
        Args:
            text: 输入文本
            
        Returns:
            关键词集合
        """
        # 转小写
        text = text.lower()
        
        # 提取中文字符
        chinese_chars = re.findall(r'[\u4e00-\u9fff]+', text)
        
        # 提取英文单词
        english_words = re.findall(r'[a-z]+', text)
        
        # 合并结果
        tokens = set()
        
        # 添加英文单词
        for word in english_words:
            if len(word) >= 2:  # 忽略单字符
                tokens.add(word)
        
        # 添加中文字符（作为bigram）
        for chinese in chinese_chars:
            if len(chinese) >= 2:
                # 添加完整的中文词
                tokens.add(chinese)
                # 添加 2-gram
                for i in range(len(chinese) - 1):
                    tokens.add(chinese[i:i+2])
        
        return tokens
    
    def index_item(self, item_id: str, content: str, metadata: Dict[str, Any], 
                   timestamp: str) -> None:
        """
        为记忆项建立索引
        
        Args:
            item_id: 记忆ID
            content: 记忆内容
            metadata: 元数据
            timestamp: 时间戳
        """
        logger.debug(f"[IndexManager] 索引项: {item_id}")
        
        # 存储 item
        self.id_to_item[item_id] = {
            "id": item_id,
            "content": content,
            "metadata": metadata,
            "timestamp": timestamp
        }
        
        # 分词并建立关键词索引
        tokens = self._tokenize(content)
        for token in tokens:
            self.keyword_index[token].add(item_id)
        self.item_keywords[item_id] = tokens
        
        # 建立时间索引
        date_key = timestamp[:10]  # YYYY-MM-DD
        self.time_index[date_key].add(item_id)
        
        # 建立分类索引
        if "category" in metadata:
            category = metadata["category"]
            self.category_index[category].add(item_id)
        
        if "type" in metadata:
            item_type = metadata["type"]
            self.category_index[item_type].add(item_id)
        
        logger.debug(f"[IndexManager] 索引完成: {item_id}, 关键词数: {len(tokens)}")
    
    def remove_item(self, item_id: str) -> None:
        """
        从索引中移除项
        
        Args:
            item_id: 记忆ID
        """
        if item_id not in self.id_to_item:
            return
        
        # 移除关键词索引
        if item_id in self.item_keywords:
            for keyword in self.item_keywords[item_id]:
                self.keyword_index[keyword].discard(item_id)
            del self.item_keywords[item_id]
        
        # 移除时间索引
        item = self.id_to_item[item_id]
        date_key = item["timestamp"][:10]
        self.time_index[date_key].discard(item_id)
        
        # 移除分类索引
        metadata = item["metadata"]
        if "category" in metadata:
            self.category_index[metadata["category"]].discard(item_id)
        if "type" in metadata:
            self.category_index[metadata["type"]].discard(item_id)
        
        # 移除 item
        del self.id_to_item[item_id]
    
    def search_by_keywords(self, query: str, limit: int = 100) -> List[str]:
        """
        基于关键词搜索
        
        Args:
            query: 查询文本
            limit: 返回数量
            
        Returns:
            匹配的 item_ids
        """
        logger.debug(f"[IndexManager] 关键词搜索: {query}")
        
        tokens = self._tokenize(query)
        
        if not tokens:
            return []
        
        # 收集匹配的文档
        matched_docs: Dict[str, int] = defaultdict(int)
        
        for token in tokens:
            if token in self.keyword_index:
                for doc_id in self.keyword_index[token]:
                    matched_docs[doc_id] += 1
        
        # 按匹配次数排序
        sorted_docs = sorted(matched_docs.items(), key=lambda x: x[1], reverse=True)
        
        return [doc_id for doc_id, _ in sorted_docs[:limit]]
    
    def search_by_time_range(self, start_date: str, end_date: str) -> List[str]:
        """
        基于时间范围搜索
        
        Args:
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            
        Returns:
            匹配的 item_ids
        """
        logger.debug(f"[IndexManager] 时间搜索: {start_date} ~ {end_date}")
        
        matched = set()
        current = start_date
        
        while current <= end_date:
            if current in self.time_index:
                matched.update(self.time_index[current])
            # 简单日期递增
            try:
                from datetime import timedelta
                dt = datetime.strptime(current, "%Y-%m-%d") + timedelta(days=1)
                current = dt.strftime("%Y-%m-%d")
            except:
                break
        
        return list(matched)
    
    def search_by_category(self, category: str) -> List[str]:
        """
        基于分类搜索
        
        Args:
            category: 分类名称
            
        Returns:
            匹配的 item_ids
        """
        logger.debug(f"[IndexManager] 分类搜索: {category}")
        
        return list(self.category_index.get(category, set()))
    
    def get_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        """获取记忆项"""
        return self.id_to_item.get(item_id)
    
    def clear(self) -> None:
        """清空所有索引"""
        logger.info("[IndexManager] 清空索引")
        self.keyword_index.clear()
        self.time_index.clear()
        self.category_index.clear()
        self.id_to_item.clear()
        self.item_keywords.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取索引统计"""
        return {
            "total_items": len(self.id_to_item),
            "keyword_count": len(self.keyword_index),
            "date_count": len(self.time_index),
            "category_count": len(self.category_index)
        }


# 全局索引实例
_global_index: Optional[IndexManager] = None


def get_global_index() -> IndexManager:
    """获取全局索引实例"""
    global _global_index
    if _global_index is None:
        _global_index = IndexManager()
    return _global_index
