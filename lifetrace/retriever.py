
"""
云枢 MemoryRetriever - 记忆检索器
从三层记忆树中智能检索相关内容
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from .memory_tree import MemoryNode

logger = logging.getLogger(__name__)


class MemoryRetriever:
    """记忆检索器 - 智能从记忆树中检索相关内容"""

    def __init__(self, source_tree, topic_tree, global_tree):
        self.source_tree = source_tree
        self.topic_tree = topic_tree
        self.global_tree = global_tree

    def retrieve(
        self,
        query: str,
        limit: int = 10,
        include_sources: Optional[List[str]] = None,
        time_range_hours: Optional[int] = None,
    ) -> List[MemoryNode]:
        """综合检索 - 从所有记忆树中检索"""
        results = []

        # 从来源树检索
        source_results = self._search_source_tree(
            query, include_sources, time_range_hours
        )
        results.extend(source_results)

        # 从主题树检索
        topic_results = self._search_topic_tree(query)
        results.extend(topic_results)

        # 排序和去重
        results = self._rank_and_deduplicate(results, limit)

        return results

    def _search_source_tree(
        self,
        query: str,
        include_sources: Optional[List[str]] = None,
        time_range_hours: Optional[int] = None,
    ) -> List[MemoryNode]:
        """搜索来源树"""
        # 内容搜索
        content_results = self.source_tree.search_by_content(query)

        # 标签搜索
        tag_results = []
        words = query.split()
        for word in words:
            tag_results.extend(self.source_tree.search_by_tag(word.lower()))

        # 合并结果
        all_results = list({n.node_id: n for n in content_results + tag_results}.values())

        # 来源过滤
        if include_sources:
            all_results = [
                n
                for n in all_results
                if n.metadata.get("source") in include_sources
            ]

        # 时间过滤
        if time_range_hours:
            cutoff = datetime.now() - timedelta(hours=time_range_hours)
            all_results = [
                n
                for n in all_results
                if datetime.fromisoformat(n.created_at) > cutoff
            ]

        return all_results

    def _search_topic_tree(self, query: str) -> List[MemoryNode]:
        """搜索主题树"""
        # 简单的主题匹配
        topic_keywords = {
            "工作": ["工作", "任务", "项目", "会议"],
            "学习": ["学习", "教程", "文档", "代码"],
            "生活": ["生活", "休息", "娱乐", "游戏"],
            "健康": ["健康", "运动", "饮食"],
        }

        matched_topics = []
        for topic, keywords in topic_keywords.items():
            if any(kw in query for kw in keywords):
                matched_topics.append(topic)

        results = []
        for topic in matched_topics:
            results.extend(self.topic_tree.get_topic_content(topic))

        # 直接内容搜索作为补充
        results.extend(self.topic_tree.search_by_content(query))

        return results

    def _rank_and_deduplicate(
        self,
        nodes: List[MemoryNode],
        limit: int,
    ) -> List[MemoryNode]:
        """排序和去重"""
        # 去重
        seen = set()
        unique_nodes = []
        for node in nodes:
            if node.node_id not in seen:
                seen.add(node.node_id)
                unique_nodes.append(node)

        # 排序：重要性 > 访问次数 > 时间
        def rank_key(node):
            return (
                -node.importance,
                -node.access_count,
                node.created_at,
            )

        unique_nodes.sort(key=rank_key)

        return unique_nodes[:limit]

    def get_recent_context(self, hours: int = 24) -> List[MemoryNode]:
        """获取近期上下文"""
        return self._search_source_tree(
            "", time_range_hours=hours
        )

    def get_summary_context(self) -> Optional[str]:
        """获取摘要上下文"""
        return self.global_tree.load_summary()

    def get_persona_context(self) -> Optional[Dict]:
        """获取人格上下文"""
        return self.global_tree.load_persona()

