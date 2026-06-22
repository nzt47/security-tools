"""模型路由器——根据任务复杂度分配模型"""
import logging

logger = logging.getLogger(__name__)

ROUTING_TABLE = [
    ("simple", "gpt-3.5-turbo", [
        "hello", "hi", "你好", "现在几点", "今天几号",
        "谢谢", "再见", "ok", "好吧", "yes", "no",
    ]),
    ("normal", "gpt-4o-mini", [
        "翻译", "解释", "总结", "缩写", "格式化",
        "写邮件", "写标题", "写摘要",
    ]),
]

COMPLEX_KEYWORDS = ["分析", "设计", "架构", "规划", "重构", "调试", "优化"]

class ModelRouter:
    def route(self, task_type: str, input_text: str, history_len: int = 0) -> str:
        text = input_text.lower()
        if any(kw in text for kw in COMPLEX_KEYWORDS):
            return "gpt-4"
        for level, model, keywords in ROUTING_TABLE:
            if any(kw in text for kw in keywords):
                return model
        if len(input_text) < 30:
            return "gpt-3.5-turbo"
        elif len(input_text) < 200:
            return "gpt-4o-mini"
        return "gpt-4"
