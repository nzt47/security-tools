"""摘要生成与压缩 — 判断压缩时机、执行压缩、管理摘要链"""


class Summarizer:
    """对话摘要器

    职责：
    1. 判断是否达到压缩阈值
    2. 调用 LLM 压缩对话为摘要
    3. 管理摘要链的合并
    """

    STRATEGIES = {
        "default": "将以下对话总结为核心要点，保留关键决策、问题和结论。要求简洁准确。",
        "brief": "用一句话概括以下对话的核心内容。",
        "detail": "详细总结以下对话，保留技术细节、代码片段和上下文信息。",
    }

    def __init__(self, llm_service):
        self._llm = llm_service

    def should_compress(self, total_tokens: int, token_limit: int,
                        threshold: float = 0.8) -> bool:
        """判断是否达到压缩阈值

        Args:
            total_tokens: 当前总 Token 数
            token_limit: 上下文窗口上限
            threshold: 触发比例，默认 80%

        Returns:
            True 表示需要压缩
        """
        return total_tokens >= int(token_limit * threshold)

    def compress(self, messages: list[dict], strategy: str = "default") -> str:
        """压缩对话为摘要

        Args:
            messages: 待压缩的消息列表
            strategy: 摘要策略（default/brief/detail）

        Returns:
            摘要文本
        """
        if not messages:
            return ""
        return self._llm.summarize(messages, max_tokens=500)

    def merge_summaries(self, old_summary: str, new_summary: str) -> str:
        """合并新旧摘要

        Args:
            old_summary: 已有的旧摘要（可能为空）
            new_summary: 新生成的摘要

        Returns:
            合并后的摘要
        """
        if not old_summary:
            return new_summary
        if not new_summary:
            return old_summary

        merge_messages = [
            {"role": "user", "content": f"已有的摘要：\n{old_summary}\n\n新的信息摘要：\n{new_summary}\n\n请将两者合并为一份连贯的完整摘要，保留所有重要信息。"}
        ]
        return self._llm.summarize(merge_messages, max_tokens=600)
