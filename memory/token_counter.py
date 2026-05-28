"""Token 计数器 — 策略模式，根据模型选择计数方式"""

import tiktoken


# cl100k_base 是 gpt-4/gpt-3.5-turbo 使用的编码
# 同时也是 Claude 系列模型近似的编码基准
_ENCODING_CACHE = {}


def _get_encoding(model: str):
    """获取或缓存编码实例"""
    if model not in _ENCODING_CACHE:
        try:
            _ENCODING_CACHE[model] = tiktoken.encoding_for_model(model)
        except KeyError:
            # 未知模型 → cl100k_base
            _ENCODING_CACHE[model] = tiktoken.get_encoding("cl100k_base")
    return _ENCODING_CACHE[model]


class TokenCounter:
    """Token 计数器

    根据模型类型自动选择计数策略：
    - gpt-4/gpt-3.5-turbo → tiktoken 精确计数
    - claude-3-* → cl100k_base 近似 × 1.1 系数
    - 其他 → cl100k_base 近似
    """

    CLAUDE_FACTOR = 1.1  # Claude 比 cl100k_base 略多的修正系数

    def count(self, text: str, model: str = "gpt-4") -> int:
        """计算文本的 Token 数"""
        if not text:
            return 0

        if model.startswith("claude"):
            # Claude 模型：cl100k_base 近似 × 系数
            encoding = _get_encoding("cl100k_base")
            return int(len(encoding.encode(text)) * self.CLAUDE_FACTOR)
        else:
            encoding = _get_encoding(model)
            return len(encoding.encode(text))

    def count_messages(self, messages: list[dict], model: str = "gpt-4") -> int:
        """计算消息列表的总 Token 数

        每条消息格式：{"role": str, "content": str}
        额外计入每条消息的格式开销（约 4 token）
        """
        total = 0
        for msg in messages:
            total += self.count(msg.get("content", ""), model)
            total += 4  # 消息格式开销
        total += 2  # 对话整体格式开销
        return total
