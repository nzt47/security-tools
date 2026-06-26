"""记忆库审查接口 — MemoryReviewer

核心功能：
1. 定期审查长期记忆库的健康度
2. 识别过时、重复、低价值的记忆
3. 建议清理或合并记忆
4. 生成记忆库报告

设计文档：P2 云枢架构升级 — Memory Abstraction Layer (3.3)
"""

import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any

from agent.memory.long_term_memory import LongTermMemory

logger = logging.getLogger(__name__)


@dataclass
class ReviewResult:
    """审查结果

    Attributes:
        reviewed_at: 审查时间
        total_entries: 总条目数
        healthy_entries: 健康条目数
        stale_entries: 陈旧条目数（久未访问）
        duplicate_entries: 重复条目数
        sensitive_unverified: 敏感未审查条目数
        suggestions: 建议操作列表
        report: 详细报告
    """
    reviewed_at: float = field(default_factory=time.time)
    total_entries: int = 0
    healthy_entries: int = 0
    stale_entries: int = 0
    duplicate_entries: int = 0
    sensitive_unverified: int = 0
    suggestions: list[str] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)


class MemoryReviewer:
    """记忆库审查器

    定期执行记忆库健康检查：
    - 识别陈旧记忆（久未访问）
    - 检测重复或高度相似的记忆
    - 标记敏感未审查记忆
    - 生成清理建议

    用法:
        reviewer = MemoryReviewer(long_term_memory)
        result = await reviewer.review()
        for suggestion in result.suggestions:
            print(suggestion)
    """

    def __init__(
        self,
        long_term_memory: LongTermMemory,
        stale_threshold_days: int = 30,
        similarity_threshold: float = 0.85,
    ) -> None:
        """
        Args:
            long_term_memory: LongTermMemory 实例
            stale_threshold_days: 陈旧阈值（天）
            similarity_threshold: 相似度阈值（用于去重）
        """
        self._ltm = long_term_memory
        self._stale_threshold = stale_threshold_days * 86400  # 转换为秒
        self._similarity_threshold = similarity_threshold
        self._last_review: Optional[ReviewResult] = None

        logger.info("[MemoryReviewer] 初始化完成: stale_threshold=%d days", stale_threshold_days)

    async def review(self) -> ReviewResult:
        """执行全面审查

        Returns:
            ReviewResult 审查结果
        """
        start_time = time.time()
        trace_id = f"review_{int(start_time)}"

        logger.info("[%s] [MemoryReviewer] 开始审查记忆库: trace_id=%s", trace_id, trace_id)

        result = ReviewResult()

        try:
            # 1. 获取统计信息
            stats = self._ltm.get_stats()
            result.total_entries = stats.get("total_entries", 0)
            result.sensitive_unverified = stats.get("sensitive_entries", 0) - stats.get("verified_entries", 0)

            # 2. 识别陈旧记忆
            stale_keys = await self._find_stale_entries(trace_id)
            result.stale_entries = len(stale_keys)

            # 3. 检测重复记忆
            duplicate_keys = await self._find_duplicate_entries(trace_id)
            result.duplicate_entries = len(duplicate_keys)

            # 4. 计算健康条目
            result.healthy_entries = result.total_entries - result.stale_entries - result.duplicate_entries

            # 5. 生成建议
            result.suggestions = self._generate_suggestions(result, stale_keys, duplicate_keys)

            # 6. 生成报告
            result.report = self._generate_report(result, stale_keys, duplicate_keys)

            duration_ms = (time.time() - start_time) * 1000
            logger.info("[%s] [MemoryReviewer] 审查完成: total=%d, healthy=%d, stale=%d, duplicates=%d, duration_ms=%.2f",
                       trace_id, result.total_entries, result.healthy_entries, result.stale_entries,
                       result.duplicate_entries, duration_ms)

            self._last_review = result
            return result

        except Exception as e:
            logger.error("[MemoryReviewer] 审查失败: error=%s", e)
            result.suggestions.append(f"审查过程发生错误: {e}")
            return result

    async def review_quick(self) -> dict[str, Any]:
        """快速审查（仅统计，不深度分析）

        Returns:
            快速审查结果
        """
        start_time = time.time()
        trace_id = f"quick_{int(start_time)}"

        stats = self._ltm.get_stats()
        
        # 检查未审查的高重要性条目
        unverified = self._ltm.list_unverified(limit=10)

        suggestions = []
        if stats.get("sensitive_entries", 0) > 0:
            suggestions.append(f"存在 {stats['sensitive_entries']} 条敏感记忆，建议审查")
        if stats.get("high_importance_entries", 0) > 0 and stats.get("verified_entries", 0) == 0:
            suggestions.append("存在高重要性记忆但未经过审查，建议处理")
        if len(unverified) > 0:
            suggestions.append(f"存在 {len(unverified)} 条未审查的重要记忆")

        duration_ms = (time.time() - start_time) * 1000

        logger.info("[%s] [MemoryReviewer] 快速审查完成: duration_ms=%.2f", trace_id, duration_ms)

        return {
            "reviewed_at": time.time(),
            "quick": True,
            "total_entries": stats.get("total_entries", 0),
            "sensitive_entries": stats.get("sensitive_entries", 0),
            "high_importance_entries": stats.get("high_importance_entries", 0),
            "verified_entries": stats.get("verified_entries", 0),
            "unverified_entries": len(unverified),
            "suggestions": suggestions,
        }

    async def _find_stale_entries(self, trace_id: str) -> list[str]:
        """查找陈旧记忆"""
        stale_keys = []

        try:
            # 读取所有条目（分批处理以避免内存问题）
            threshold = time.time() - self._stale_threshold

            import sqlite3
            conn = sqlite3.connect(self._ltm.db_path)
            conn.row_factory = sqlite3.Row

            rows = conn.execute(
                f"SELECT key FROM {self._ltm._TABLE_NAME} "
                f"WHERE last_accessed < ? AND importance < 4",
                (threshold,)
            ).fetchall()

            stale_keys = [row["key"] for row in rows]
            conn.close()

            if stale_keys:
                logger.debug("[%s] [MemoryReviewer] 发现 %d 条陈旧记忆", trace_id, len(stale_keys))

        except Exception as e:
            logger.error("[MemoryReviewer] 查找陈旧记忆失败: error=%s", e)

        return stale_keys

    async def _find_duplicate_entries(self, trace_id: str) -> list[str]:
        """查找重复记忆（简化版：基于内容哈希）"""
        duplicate_keys = []
        seen_hashes: dict[bytes, list[str]] = {}

        try:
            import sqlite3
            import hashlib

            conn = sqlite3.connect(self._ltm.db_path)
            conn.row_factory = sqlite3.Row

            rows = conn.execute(
                f"SELECT key, content FROM {self._ltm._TABLE_NAME}"
            ).fetchall()

            for row in rows:
                content_hash = hashlib.md5(str(row["content"]).encode()).digest()

                if content_hash in seen_hashes:
                    # 保留第一条，其他标记为重复
                    duplicate_keys.append(row["key"])
                    logger.debug("[%s] [MemoryReviewer] 重复: %s <-> %s",
                               trace_id, seen_hashes[content_hash][0], row["key"])
                else:
                    seen_hashes[content_hash] = [row["key"]]

            conn.close()

            if duplicate_keys:
                logger.debug("[%s] [MemoryReviewer] 发现 %d 条重复记忆", trace_id, len(duplicate_keys))

        except Exception as e:
            logger.error("[MemoryReviewer] 查找重复记忆失败: error=%s", e)

        return duplicate_keys

    def _generate_suggestions(
        self,
        result: ReviewResult,
        stale_keys: list[str],
        duplicate_keys: list[str],
    ) -> list[str]:
        """生成清理建议"""
        suggestions = []

        if result.stale_entries > 0:
            suggestions.append(
                f"建议清理 {result.stale_entries} 条陈旧记忆（超过 {self._stale_threshold // 86400} 天未访问）"
            )

        if result.duplicate_entries > 0:
            suggestions.append(
                f"建议合并或删除 {result.duplicate_entries} 条重复记忆"
            )

        if result.sensitive_unverified > 0:
            suggestions.append(
                f"建议审查 {result.sensitive_unverified} 条敏感记忆"
            )

        if result.total_entries == 0:
            suggestions.append("记忆库为空，无需清理")

        if not suggestions:
            suggestions.append("记忆库状态良好，无需特殊处理")

        return suggestions

    def _generate_report(
        self,
        result: ReviewResult,
        stale_keys: list[str],
        duplicate_keys: list[str],
    ) -> dict[str, Any]:
        """生成详细报告"""
        return {
            "reviewer_version": "1.0.0",
            "reviewed_at": datetime.fromtimestamp(result.reviewed_at, tz=timezone.utc).isoformat(),
            "total_entries": result.total_entries,
            "healthy_entries": result.healthy_entries,
            "stale_entries": result.stale_entries,
            "duplicate_entries": result.duplicate_entries,
            "sensitive_unverified": result.sensitive_unverified,
            "health_score": self._calculate_health_score(result),
            "stale_threshold_days": self._stale_threshold // 86400,
            "stale_keys_sample": stale_keys[:10],  # 最多显示 10 条
            "duplicate_keys_sample": duplicate_keys[:10],
        }

    def _calculate_health_score(self, result: ReviewResult) -> float:
        """计算健康评分 (0-100)"""
        if result.total_entries == 0:
            return 100.0

        # 基础分 100，每有问题扣分
        score = 100.0

        # 陈旧记忆扣分（每条扣 2 分）
        score -= min(result.stale_entries * 2, 30)

        # 重复记忆扣分（每条扣 3 分）
        score -= min(result.duplicate_entries * 3, 30)

        # 敏感未审查扣分（每条扣 5 分）
        score -= min(result.sensitive_unverified * 5, 40)

        return max(0.0, round(score, 1))

    def get_last_review(self) -> Optional[ReviewResult]:
        """获取上次审查结果"""
        return self._last_review
