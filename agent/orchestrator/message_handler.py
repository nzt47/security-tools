"""MessageHandler — 消息解析、意图识别、输入预处理

职责：从用户输入中提取意图、检测不满、追问场景判断等。
"""

import re
import logging
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

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
        """判断是否追问场景"""
        # 检查是否有当前消息和历史记录
        text = context.get("text", "")
        history = context.get("history_count", 0)
        if history > 0 and any(p.match(text) for p in FOLLOW_UP_PATTERNS):
            return True
        if history > 2 and len(text) < 20:
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
