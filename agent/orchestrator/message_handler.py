"""MessageHandler — 消息解析、意图识别、输入预处理

职责：从用户输入中提取意图、检测不满、追问场景判断等。
"""

import re
import logging
import json
import uuid
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# ── 不满／负面情绪检测 ─────────────────────────────────────────────
DISSATISFACTION_PATTERNS = [
    re.compile(r"(?i)(你(是不是)?(不|没|无法|不能)|怎么(还)?(不|没)|废物|垃圾|差劲)"),
    re.compile(r"(?i)(我(已经|跟|对)你说了|你(到|有)(底|没有)(在|听)?)"),
    re.compile(r"(?i)((回答|回复|答案)(错误|不对|错的|不准确))"),
    re.compile(r"(?i)(无语|算了|懒得|不想|说了你也不懂)"),
    re.compile(r"(?i)(重新|再(次)?).{0,4}(回答|说|解释|讲)"),
]

# ── 追问检测 ────────────────────────────────────────────────────────
FOLLOW_UP_PATTERNS = [
    re.compile(r"(?i)^(那|然后|所以|接着|还有|另外|不过|但是|可是|然而)"),
    re.compile(r"(?i)^(为什么|怎么|如何|什么|哪里|谁|什么时候|哪个)"),
    re.compile(r"(?i)(具[体]?[一]?点|详细|解释|说说|继续|接着说)"),
]

# ── 简单查询检测 ────────────────────────────────────────────────────
SIMPLE_QUERY_PATTERNS = [
    re.compile(r"(?i)^(你好|hi|hello|hey|在吗|在不在|早上好|下午好|晚上好)"),
    re.compile(r"(?i)^(好的|好[的嘛]|ok|嗯|行|可以|谢谢|感谢)"),
]


class MessageHandler:
    """消息解析与意图识别"""

    @staticmethod
    def parse(text: str) -> Dict:
        """结构化解析输入"""
        return {
            "raw": text,
            "cleaned": text.strip(),
            "length": len(text),
            "is_empty": not text or not text.strip(),
        }

    @staticmethod
    def is_simple_query(text: str) -> bool:
        """判断是否为简单问候/礼貌用语"""
        text = text.strip()
        return any(p.match(text) for p in SIMPLE_QUERY_PATTERNS)

    @staticmethod
    def detect_dissatisfaction(text: str) -> bool:
        """检测用户不满/负面情绪"""
        return any(p.search(text) for p in DISSATISFACTION_PATTERNS)

    @staticmethod
    def is_follow_up(context: Dict) -> bool:
        """判断是否追问场景（委托 DST 做省略句检测 + 正则兜底）

        修复说明（【不易】接口契约对齐）:
            原 is_follow_up 读 context['text']/['history_count']，但调用方
            （orchestrator.py）传的是 {'last_was_template','confidence'}，
            导致永远取不到 text → 恒返回 False，追问降级 LLM 逻辑失效。
            现对齐调用方实际传入的键，并委托 DST.is_ellipsis_query 做指代/省略检测。

        支持的 context 键（向后兼容，缺省退化为纯正则）:
            - text: str              当前用户输入（必需）
            - last_was_template: bool 上一轮是否模板回复
            - confidence: Confidence  本轮意图置信度（保留字段，暂未用作判据）
            - session_id: str         会话 ID；提供则委托 DST 检测省略句
            - history_count: int      兼容旧调用方（保留）
        """
        text = (context.get("text") or "").strip()
        last_was_template = bool(context.get("last_was_template", False))
        history_count = int(context.get("history_count", 0))

        if not text:
            return False

        # 1. 【变易】委托 DST 做省略句/指代句检测（"那个呢"/"然后呢"等）
        session_id = context.get("session_id")
        if session_id:
            try:
                from agent.orchestrator.dialog_state import get_dialog_state
                dst = get_dialog_state(session_id)
                if dst.is_ellipsis_query(text):
                    return True
            except Exception as e:  # noqa: BLE001
                logger.debug(json.dumps({
                    "trace_id": _trace_id(),
                    "module_name": "message_handler",
                    "action": "is_follow_up.dst.error",
                    "error": f"{type(e).__name__}: {e}",
                }, ensure_ascii=False))

        # 2. 正则追问模式兜底（"为什么"/"详细"/"继续"等）
        if any(p.match(text) for p in FOLLOW_UP_PATTERNS):
            return True

        # 3. 模板后短句追问（兼容旧逻辑：history_count>2 或 last_was_template）
        if (history_count > 2 or last_was_template) and len(text) < 20:
            return True

        return False

    @staticmethod
    def extract_keywords(text: str) -> List[str]:
        """关键词提取（简单分词）"""
        # 去除标点，按空格/逗号分割
        cleaned = re.sub(r'[^\w\s]', ' ', text)
        words = cleaned.split()
        # 过滤短词和停用词
        stop_words = {"的", "了", "是", "在", "我", "有", "和", "就", "不", "人",
                      "都", "一", "一个", "上", "也", "很", "到", "说", "要",
                      "去", "你", "会", "着", "没有", "看", "好", "自己"}
        keywords = [w for w in words if len(w) >= 2 and w not in stop_words]
        return keywords


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
            "module_name": "message_handler",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
