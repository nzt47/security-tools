"""Workflow Engine 内置规则——8 条预置规则"""

import re
import time
import logging
from datetime import datetime
from .registry import Rule
from .matcher import keyword_match, regex_match

logger = logging.getLogger(__name__)


def _current_time_fmt() -> str:
    return datetime.now().strftime("%H:%M")


def _current_date_fmt() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _current_weekday() -> str:
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return weekdays[datetime.now().weekday()]


def _greeting_time() -> str:
    h = datetime.now().hour
    if h < 6:
        return "凌晨好"
    elif h < 12:
        return "早上好"
    elif h < 14:
        return "中午好"
    elif h < 18:
        return "下午好"
    else:
        return "晚上好"


def register_builtin_rules(registry):
    """注册 8 条内置规则"""

    # 1. 时间查询
    registry.register(Rule(
        name="check_time",
        description="现在几点/当前时间",
        match_fn=keyword_match(["现在几点", "当前时间", "几点了", "什么时间", "几点钟"]),
        execute_fn=lambda _: f"现在是 {_current_time_fmt()}",
        priority=100,
        category="query",
    ))

    # 2. 日期查询
    registry.register(Rule(
        name="check_date",
        description="今天几号/今天日期",
        match_fn=keyword_match(["今天几号", "今天日期", "今天周", "今天星期", "什么日子"]),
        execute_fn=lambda _: f"今天是 {_current_date_fmt()} {_current_weekday()}",
        priority=100,
        category="query",
    ))

    # 3. 健康检查
    registry.register(Rule(
        name="check_health",
        description="你还好吗/状态查询",
        match_fn=keyword_match(["还好吗", "状态", "在吗", "在不在", "hello", "hi", "你好"]),
        execute_fn=lambda _: f"{_greeting_time()}！我在线，一切正常 😊",
        priority=90,
        category="greeting",
    ))

    # 4. 简单计算
    registry.register(Rule(
        name="simple_calc",
        description="简单算术计算",
        match_fn=regex_match(r'^[\d\s\+\-\*\/\(\)\.]+$'),
        execute_fn=lambda text: _safe_calc(text),
        priority=90,
        category="utility",
    ))

    # 5. 问候
    registry.register(Rule(
        name="greeting",
        description="自动分时段问候",
        match_fn=keyword_match(["早上好", "下午好", "晚上好", "你好", "大家好"]),
        execute_fn=lambda _: f"{_greeting_time()}！有什么我可以帮你的吗？",
        priority=80,
        category="greeting",
    ))

    # 6. 告别
    registry.register(Rule(
        name="farewell",
        description="告别回复",
        match_fn=keyword_match(["再见", "拜拜", "bye", "goodbye", "下次见", "明天见"]),
        execute_fn=lambda _: "再见！有需要随时找我 😊",
        priority=80,
        category="farewell",
    ))

    # 7. 感谢
    registry.register(Rule(
        name="thanks",
        description="感谢回复",
        match_fn=keyword_match(["谢谢", "感谢", "多谢", "thank", "thanks", "thx"]),
        execute_fn=lambda _: "不客气！很高兴能帮到你 😊",
        priority=70,
        category="polite",
    ))

    # 8. 确认
    registry.register(Rule(
        name="confirmation",
        description="确认回复",
        match_fn=keyword_match(["好的", "可以", "明白", "懂了", "知道了", "收到"]),
        execute_fn=lambda _: "好的，明白了！",
        priority=50,
        category="polite",
    ))

    logger.info("[WorkflowEngine] 已注册 %d 条内置规则", registry.count())


def _safe_calc(expr: str) -> str:
    """安全计算表达式"""
    try:
        expr = expr.strip()
        # 只允许数字和运算符
        if not re.match(r'^[\d\s\+\-\*\/\(\)\.]+$', expr):
            return "无法计算"
        result = eval(expr, {"__builtins__": {}}, {})
        return f"{expr} = {result}"
    except Exception as e:
        return f"计算错误: {e}"
