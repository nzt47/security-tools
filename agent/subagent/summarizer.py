"""子代理摘要压缩器 — SubagentSummarizer

核心功能：
1. 将子代理执行结果压缩为摘要结论
2. 强制主代理只接收摘要，不传递原始上下文
3. 支持多种摘要策略（关键点、决策、动作项等）

设计文档：P2 云枢架构升级 — Subagent Isolation (4.1)
"""

import json
import uuid
import time
import logging
import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



class SummaryStrategy(Enum):
    """摘要策略"""
    KEY_POINTS = "key_points"        # 关键点摘要
    DECISIONS = "decisions"          # 决策摘要
    ACTION_ITEMS = "action_items"    # 动作项摘要
    FULL = "full"                    # 完整摘要（包含结论）
    MINIMAL = "minimal"              # 最小摘要（仅结论）


@dataclass
class SubagentSummary:
    """子代理摘要结果

    Attributes:
        subagent_id: 子代理 ID
        subagent_name: 子代理名称
        original_output: 原始输出（已脱敏）
        summary_text: 摘要文本
        key_findings: 关键发现列表
        decisions: 决策列表
        action_items: 动作项列表
        confidence: 摘要置信度 (0-1)
        tokens_used: 估算的 token 数量
        created_at: 创建时间戳
        trace_id: 关联的追踪 ID
        content_hash: 原始内容的哈希（用于去重）
    """
    subagent_id: str
    subagent_name: str
    original_output: str = ""
    summary_text: str = ""
    key_findings: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    confidence: float = 0.0
    tokens_used: int = 0
    created_at: float = field(default_factory=time.time)
    trace_id: str = ""
    content_hash: str = ""

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "subagent_id": self.subagent_id,
            "subagent_name": self.subagent_name,
            "summary_text": self.summary_text,
            "key_findings": self.key_findings,
            "decisions": self.decisions,
            "action_items": self.action_items,
            "confidence": self.confidence,
            "tokens_used": self.tokens_used,
            "created_at": self.created_at,
            "trace_id": self.trace_id,
        }

    def get_brief_conclusion(self) -> str:
        """获取简短结论（用于主代理消费）"""
        if self.summary_text:
            return self.summary_text[:200] + "..." if len(self.summary_text) > 200 else self.summary_text
        
        parts = []
        if self.key_findings:
            parts.append(f"关键发现: {'; '.join(self.key_findings[:2])}")
        if self.decisions:
            parts.append(f"决策: {'; '.join(self.decisions[:2])}")
        if self.action_items:
            parts.append(f"待办: {'; '.join(self.action_items[:2])}")
        
        return " | ".join(parts) if parts else "[无明确结论]"


class SubagentSummarizer:
    """子代理摘要压缩器

    功能：
    - 将冗长的执行结果压缩为结构化摘要
    - 提取关键发现、决策和动作项
    - 生成简短结论供主代理消费
    - 内容哈希用于去重和缓存

    用法:
        summarizer = SubagentSummarizer()
        summary = await summarizer.summarize(
            output="详细的执行输出...",
            subagent_id="sa-xxx",
            strategy=SummaryStrategy.KEY_POINTS
        )
        # 主代理只接收 summary.get_brief_conclusion()
    """

    # 摘要长度限制
    MAX_SUMMARY_LENGTH = 500
    MAX_KEY_POINTS = 5
    MAX_DECISIONS = 3
    MAX_ACTION_ITEMS = 5

    # Token 估算（简单按字符数 / 4）
    TOKENS_PER_CHAR = 0.25

    def __init__(
        self,
        max_summary_length: int = 500,
        enable_compression: bool = True,
    ):
        """
        Args:
            max_summary_length: 摘要最大长度（字符数）
            enable_compression: 是否启用内容压缩
        """
        self._max_summary_length = max_summary_length
        self._enable_compression = enable_compression

        logger.info("[SubagentSummarizer] 初始化完成: max_length=%d, compression=%s",
                   max_summary_length, enable_compression)

    async def summarize(
        self,
        output: str,
        subagent_id: str,
        subagent_name: str = "",
        strategy: SummaryStrategy = SummaryStrategy.KEY_POINTS,
        trace_id: str = "",
    ) -> SubagentSummary:
        """生成执行结果的摘要

        Args:
            output: 原始执行输出
            subagent_id: 子代理 ID
            subagent_name: 子代理名称
            strategy: 摘要策略
            trace_id: 追踪 ID

        Returns:
            SubagentSummary 摘要结果
        """
        start_time = time.time()

        # 计算内容哈希
        content_hash = self._compute_hash(output)

        # 根据策略生成摘要
        if strategy == SummaryStrategy.MINIMAL:
            summary_text, key_findings, decisions, action_items = self._minimal_summary(output)
        elif strategy == SummaryStrategy.DECISIONS:
            summary_text, key_findings, decisions, action_items = self._decisions_summary(output)
        elif strategy == SummaryStrategy.ACTION_ITEMS:
            summary_text, key_findings, decisions, action_items = self._action_items_summary(output)
        elif strategy == SummaryStrategy.FULL:
            summary_text, key_findings, decisions, action_items = self._full_summary(output)
        else:
            summary_text, key_findings, decisions, action_items = self._key_points_summary(output)

        # 限制各字段长度
        summary_text = summary_text[:self._max_summary_length]
        key_findings = key_findings[:self.MAX_KEY_POINTS]
        decisions = decisions[:self.MAX_DECISIONS]
        action_items = action_items[:self.MAX_ACTION_ITEMS]

        # 估算 token 使用量
        tokens_used = int(len(output) * self.TOKENS_PER_CHAR)

        duration_ms = (time.time() - start_time) * 1000

        logger.info("[SubagentSummarizer] 摘要生成完成: subagent=%s, strategy=%s, "
                   "original_len=%d, summary_len=%d, duration_ms=%.2f",
                   subagent_id, strategy.value, len(output), len(summary_text), duration_ms)

        return SubagentSummary(
            subagent_id=subagent_id,
            subagent_name=subagent_name,
            original_output=self._truncate_output(output),  # 保留截断版本供审计
            summary_text=summary_text,
            key_findings=key_findings,
            decisions=decisions,
            action_items=action_items,
            confidence=self._estimate_confidence(output, summary_text),
            tokens_used=tokens_used,
            trace_id=trace_id,
            content_hash=content_hash,
        )

    def _compute_hash(self, content: str) -> str:
        """计算内容哈希"""
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _truncate_output(self, output: str, max_len: int = 1000) -> str:
        """截断输出（用于日志保留）"""
        if len(output) <= max_len:
            return output
        return output[:max_len] + f"\n... [截断 {len(output) - max_len} 字符]"

    def _estimate_confidence(self, original: str, summary: str) -> float:
        """估算摘要置信度"""
        if not original or not summary:
            return 0.0
        
        # 基于摘要占比估算
        ratio = len(summary) / len(original)
        
        # 比例越低，说明压缩越多，可能丢失信息
        if ratio > 0.5:
            return 0.95  # 高置信度，保留大部分内容
        elif ratio > 0.2:
            return 0.85  # 中等置信度
        elif ratio > 0.05:
            return 0.75  # 较低置信度，大量压缩
        else:
            return 0.65  # 低置信度，极端压缩

    # ── 摘要策略实现 ──

    def _extract_structured_items(self, output: str) -> tuple[list[str], list[str], list[str]]:
        """提取结构化项目（关键发现、决策、动作项）"""
        key_findings = []
        decisions = []
        action_items = []

        lines = output.split("\n")
        
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 关键发现标记
            if any(marker in line.lower() for marker in ["发现:", "found:", "关键:", "key:"]):
                key_findings.append(line.split(":", 1)[-1].strip())

            # 决策标记
            elif any(marker in line.lower() for marker in ["决定:", "decision:", "决策:"]):
                decisions.append(line.split(":", 1)[-1].strip())

            # 动作项标记
            elif any(marker in line.lower() for marker in ["todo:", "action:", "任务:", "下一步:"]):
                action_items.append(line.split(":", 1)[-1].strip())

        return key_findings, decisions, action_items

    def _minimal_summary(self, output: str) -> tuple[str, list[str], list[str], list[str]]:
        """最小摘要策略：仅保留结论"""
        # 提取最后一段作为结论
        paragraphs = output.split("\n\n")
        conclusion = paragraphs[-1].strip() if paragraphs else output

        return conclusion[:200], [], [], []

    def _key_points_summary(self, output: str) -> tuple[str, list[str], list[str], list[str]]:
        """关键点摘要策略"""
        key_findings, decisions, action_items = self._extract_structured_items(output)

        # 如果没有找到结构化内容，从首尾提取
        if not key_findings:
            lines = output.split("\n")
            key_findings = [line.strip() for line in lines[:3] if line.strip()]

        summary = "; ".join(key_findings[:3]) if key_findings else output[:200]

        return summary, key_findings, decisions, action_items

    def _decisions_summary(self, output: str) -> tuple[str, list[str], list[str], list[str]]:
        """决策摘要策略"""
        key_findings, decisions, action_items = self._extract_structured_items(output)

        # 强调决策
        summary = " | ".join(decisions[:2]) if decisions else output[:200]

        return summary, key_findings, decisions, action_items

    def _action_items_summary(self, output: str) -> tuple[str, list[str], list[str], list[str]]:
        """动作项摘要策略"""
        key_findings, decisions, action_items = self._extract_structured_items(output)

        # 强调待办
        summary = " | ".join(action_items[:3]) if action_items else output[:200]

        return summary, key_findings, decisions, action_items

    def _full_summary(self, output: str) -> tuple[str, list[str], list[str], list[str]]:
        """完整摘要策略：包含所有维度"""
        key_findings, decisions, action_items = self._extract_structured_items(output)

        # 如果没有结构化内容，提取首段和末段
        if not key_findings and not decisions and not action_items:
            paragraphs = output.split("\n\n")
            if len(paragraphs) >= 2:
                key_findings = [paragraphs[0].strip()[:200]]
                action_items = [paragraphs[-1].strip()[:200]]

        # 构建完整摘要
        parts = []
        if key_findings:
            parts.append(f"关键: {'; '.join(key_findings[:2])}")
        if decisions:
            parts.append(f"决策: {'; '.join(decisions[:2])}")
        if action_items:
            parts.append(f"待办: {'; '.join(action_items[:2])}")

        summary = " | ".join(parts) if parts else output[:300]

        return summary, key_findings, decisions, action_items

    # ── 便捷方法 ──

    async def summarize_to_conclusion(
        self,
        output: str,
        subagent_id: str,
        **kwargs
    ) -> str:
        """直接生成简短结论（供主代理使用）

        这是主代理获取子代理结果的唯一接口。
        """
        _reserved = {"output", "subagent_id", "strategy"}
        safe_kwargs = {k: v for k, v in kwargs.items() if k not in _reserved}
        summary = await self.summarize(
            output=output,
            subagent_id=subagent_id,
            strategy=SummaryStrategy.KEY_POINTS,
            **safe_kwargs
        )
        return summary.get_brief_conclusion()

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "max_summary_length": self._max_summary_length,
            "enable_compression": self._enable_compression,
            "tokens_per_char": self.TOKENS_PER_CHAR,
        }


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "summarizer",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
