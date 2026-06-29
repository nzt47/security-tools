"""模型路由器——根据任务复杂度分配模型"""
import logging
import json
import uuid

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


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
            "module_name": "router",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
