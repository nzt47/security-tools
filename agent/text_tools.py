"""中文文本优化工具 — 检测并去除 AI 写作痕迹

基于 humanizer-zh 技能的 24 种 AI 写作模式检测规则，
对中文文本进行 AI 痕迹扫描和优化建议生成。
"""

import re
import logging
import json
import uuid
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# ════════════════════════════════════════════════════════════════════════════════
#  模式 1: 过度强调意义、遗产和更广泛的趋势
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_1_KEYWORDS = [
    "作为", "充当", "标志着", "见证了", "是.*的体现", "是.*的证明", "是.*的提醒",
    "极其重要的", "至关重要的", "核心的", "关键性的作用", "关键性的时刻",
    "凸显了", "强调了", "彰显了", "反映了更广泛的", "象征着",
    "为……做出贡献", "为……奠定基础", "标志着", "塑造着",
    "代表", "标志着一个转变", "关键转折点", "不断演变的格局",
    "焦点", "不可磨灭的印记", "深深植根于",
]

PATTERN_1_RE = re.compile(
    "(?:"
    + "|".join(
        kw.replace("……", ".+?") if "……" in kw else kw
        for kw in PATTERN_1_KEYWORDS
    )
    + ")"
)


# ════════════════════════════════════════════════════════════════════════════════
#  模式 2: 过度强调知名度和媒体报道
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_2_KEYWORDS = [
    "独立报道", "地方媒体", "区域媒体", "国家媒体",
    "由知名专家撰写", "活跃的社交媒体账号",
]

PATTERN_2_RE = re.compile("|".join(PATTERN_2_KEYWORDS))


# ════════════════════════════════════════════════════════════════════════════════
#  模式 3: 以 -ing 结尾的肤浅分析（中文中等效的句末分析短语）
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_3_SUFFIXES = [
    "突出", "强调", "彰显", "确保", "反映", "象征",
    "做出贡献", "培养", "促进", "涵盖", "展示",
]

PATTERN_3_RE = re.compile(
    r"，(?:" + "|".join(PATTERN_3_SUFFIXES) + r")[^。，]{2,30}$",
    re.MULTILINE,
)


# ════════════════════════════════════════════════════════════════════════════════
#  模式 4: 宣传和广告式语言
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_4_WORDS = [
    "拥有", "充满活力的", "丰富的", "深刻的",
    "增强其", "展示", "体现", "致力于",
    "自然之美", "坐落于", "位于.*的中心",
    "开创性的", "著名的", "令人叹为观止的",
    "必游之地", "迷人的",
]

PATTERN_4_RE = re.compile("|".join(PATTERN_4_WORDS))


# ════════════════════════════════════════════════════════════════════════════════
#  模式 5: 模糊归因和含糊措辞
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_5_PHRASES = [
    "行业报告显示", "观察者指出", "专家认为",
    "一些批评者认为", "多个来源", "多个出版物",
    "业内人士表示", "有分析指出",
]

PATTERN_5_RE = re.compile("|".join(PATTERN_5_PHRASES))


# ════════════════════════════════════════════════════════════════════════════════
#  模式 6: 提纲式的"挑战与未来展望"部分
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_6_PHRASES = [
    "面临若干挑战", "尽管存在这些挑战",
    "挑战与未来展望", "挑战与遗产",
    "尽管.*面临.*挑战",
]

PATTERN_6_RE = re.compile("|".join(PATTERN_6_PHRASES))


# ════════════════════════════════════════════════════════════════════════════════
#  模式 7: 过度使用的"AI 词汇"
# ════════════════════════════════════════════════════════════════════════════════

AI_VOCABULARY = [
    "此外", "与……保持一致", "至关重要", "深入探讨", "强调",
    "持久的", "增强", "培养", "获得", "突出",
    "相互作用", "复杂", "复杂性", "关键",
    "格局", "关键性的", "展示", "织锦",
    "证明", "宝贵的", "充满活力的",
    "弥足珍贵", "不可或缺",
]

PATTERN_7_RE = re.compile(
    "(?:"
    + "|".join(
        w.replace("……", ".+?") if "……" in w else w
        for w in AI_VOCABULARY
    )
    + ")"
)


# ════════════════════════════════════════════════════════════════════════════════
#  模式 8: 避免使用"是"（系动词回避）
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_8_REPLACEMENTS = {
    "作为": "是",
    "代表": "是",
    "标志着": "是",
    "充当": "是",
    "拥有": "有",
    "设有": "有",
    "提供": "有",
}

PATTERN_8_RE = re.compile(
    r"(?<![。，、；：])"  # 不在句首标点后
    r"(作为|代表|标志着|充当|拥有|设有|提供)"
    r"(?![^。，]{0,5}(?:是|有))"  # 附近没有系动词
)


# ════════════════════════════════════════════════════════════════════════════════
#  模式 9: 否定式排比
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_9_RE = re.compile(
    r"(?:不仅……而且……|不仅(?:是|在于).*?而且(?:是|在于)|不仅仅(?:是|在于).*?而(?:是|在于)|"
    r"不仅仅是关于.*?而是|这不仅仅是|这不单单是)"
)


# ════════════════════════════════════════════════════════════════════════════════
#  模式 10: 三段式法则过度使用
# ════════════════════════════════════════════════════════════════════════════════

# 检测"X、Y 和 Z"模式的顿号列举
PATTERN_10_RE = re.compile(
    r"[^。，；]{3,30}、(?:[^。，；]{1,20}、){1,}[^。，；]{1,20}"
    r"(?:和|与|以及|或)[^。，；]{1,20}"
)


# ════════════════════════════════════════════════════════════════════════════════
#  模式 11: 刻意换词（同义词循环）
# ════════════════════════════════════════════════════════════════════════════════

# 检测同一句话中用近义词重复描述同一事物
# 捕获组首字符必须为非空白,避免匹配缩进空格产生误报
PATTERN_11_RE = re.compile(
    r"([^\s。，；][^。，；]{1,9})(?:，|\s)[^。，；]{0,20}"
    r"(?:和|与|以及)?\1"
)


# ════════════════════════════════════════════════════════════════════════════════
#  模式 12: 虚假范围（"从 X 到 Y" 结构）
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_12_RE = re.compile(
    r"从[^。，]{2,15}到[^。，]{2,15}"
)


# ════════════════════════════════════════════════════════════════════════════════
#  模式 13: 破折号过度使用
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_13_RE = re.compile(r"—")


# ════════════════════════════════════════════════════════════════════════════════
#  模式 14: 粗体过度使用
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_14_RE = re.compile(r"\*\*[^*]+\*\*")


# ════════════════════════════════════════════════════════════════════════════════
#  模式 15: 内联标题垂直列表
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_15_RE = re.compile(
    r"[-*]\s*\*\*[^*]+：\*\*",
    re.MULTILINE,
)


# ════════════════════════════════════════════════════════════════════════════════
#  模式 16: 标题中的标题大写（中文不适用，保留占位）
# ════════════════════════════════════════════════════════════════════════════════


# ════════════════════════════════════════════════════════════════════════════════
#  模式 17: 表情符号
# ════════════════════════════════════════════════════════════════════════════════

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # Emoticons
    "\U0001F300-\U0001F5FF"  # Misc symbols and pictographs
    "\U0001F680-\U0001F6FF"  # Transport symbols
    "\U0001F1E0-\U0001F1FF"  # Flags (regional indicators)
    "\U00002702-\U000027B0"  # Dingbats
    "\U000024C2-\U000024C2"  # Enclosed M
    "\U0001F000-\U0001F02F"  # Mahjong (corrected: was overly broad range)
    "\U0001F900-\U0001F9FF"  # Supplemental symbols
    "\U0001FA00-\U0001FA6F"  # Chess symbols
    "\U0001FA70-\U0001FAFF"  # Symbols extended-A
    "\U00002600-\U000026FF"  # Misc symbols
    "\U0000FE00-\U0000FE0F"  # Variation selectors
    "\U0000200D"              # Zero-width joiner
    "\U0001F0CF"              # Playing card black joker
    "]+"
)


# ════════════════════════════════════════════════════════════════════════════════
#  模式 18: 弯引号
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_18_RE = re.compile(r"[“”‘’]")


# ════════════════════════════════════════════════════════════════════════════════
#  模式 19: 协作交流痕迹
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_19_PHRASES = [
    "希望这对您有帮助", "希望这对你有帮助",
    "当然！", "一定！",
    "您说得完全正确！", "你说得完全正确！",
    "您想要", "你想要",
    "请告诉我", "如果您想让我",
    "这是一个",
]

PATTERN_19_RE = re.compile("|".join(PATTERN_19_PHRASES))


# ════════════════════════════════════════════════════════════════════════════════
#  模式 20: 知识截止日期免责声明
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_20_PHRASES = [
    r"截至\s*\d{4}年",
    r"根据我最后(?:的)?训练更新",
    r"虽然具体细节有限",
    r"虽然.*信息.*(?:有限|稀缺)",
    r"基于可用信息",
    r"据我所知.*不.*(?:完整|全面|充分)",
    r"在现成资料中.*(?:不|没有).*(?:广泛|详细)",
]

PATTERN_20_RE = re.compile("|".join(PATTERN_20_PHRASES))


# ════════════════════════════════════════════════════════════════════════════════
#  模式 21: 谄媚/卑躬屈膝的语气
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_21_PHRASES = [
    "好问题！", "好问题！",
    "您说得完全正确", "你说得完全正确",
    "这是一个很好的问题",
    "非常好的观点",
    "非常棒的问题",
]

PATTERN_21_RE = re.compile("|".join(PATTERN_21_PHRASES))


# ════════════════════════════════════════════════════════════════════════════════
#  模式 22: 填充短语
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_22_REPLACEMENTS = {
    "为了实现这一目标": "为了实现这一点",
    "由于下雨的事实": "因为下雨",
    "在这个时间点": "现在",
    "在您需要帮助的情况下": "如果您需要帮助",
    "系统具有处理的能力": "系统可以处理",
    "值得注意的是数据显示": "数据显示",
}

PATTERN_22_RE = re.compile(
    r"(值得注意的是|"
    r"需要指出的是|"
    r"不可否认的是|"
    r"众所周知|"
    r"不言而喻|"
    r"从某种意义上说|"
    r"在某种程度上|"
    r"在一定意义上|"
    r"换句话说|"
    r"也就是说|"
    r"具体来说|"
    r"总的来说|"
    r"一般而言|"
    r"总体而言|"
    r"从某个角度来看)"
)


# ════════════════════════════════════════════════════════════════════════════════
#  模式 23: 过度限定
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_23_RE = re.compile(
    r"(?:可以|可能|或许|大概|也许|似乎|好像|"
    r"潜在|有可能|一定程度上|相对而言|"
    r"相当|比较|有些|略微|稍显"
    r"){2,}"
)


# ════════════════════════════════════════════════════════════════════════════════
#  模式 24: 通用积极结论
# ════════════════════════════════════════════════════════════════════════════════

PATTERN_24_PHRASES = [
    r"未来(?:看起来|将).*(?:光明|美好|可期|充满希望)",
    r"激动人心.*(?:时代|时刻).*(?:到来|即将来临)",
    r"继续.*追求.*(?:卓越|完美|更好的)",
    r"向正确方向迈出了.*一步",
    r"前景.*(?:广阔|光明|充满希望)",
]

PATTERN_24_RE = re.compile("|".join(PATTERN_24_PHRASES))


# ════════════════════════════════════════════════════════════════════════════════
#  计分规则
# ════════════════════════════════════════════════════════════════════════════════

# 每个模式的建议（中文）
SUGGESTIONS = {
    1: '删除过度强调意义的词汇（"标志着""见证了""关键性的"等），改用中性、事实性的描述',
    2: '避免空泛的知名度声明，直接引用具体来源和上下文',
    3: '删除句末的肤浅分析短语（"突出""彰显""反映"等），改为直接陈述事实',
    4: '去除宣传性语言（"令人叹为观止""必游之地"等），使用客观描述',
    5: '将模糊归因替换为具体来源引用',
    6: '避免公式化的"挑战与未来展望"段落结构',
    7: '替换高频 AI 词汇（"此外""至关重要""格局"等）为更自然的表达',
    8: '优先使用简单的系动词"是"替代"作为""代表""标志着"等复杂结构',
    9: '避免"不仅……而且……"等否定式排比结构',
    10: '打破三段式列举，改为两项或四项，或分散叙述',
    11: '合并重复的近义词描述，避免刻意换词',
    12: '避免"从 X 到 Y"的虚假范围结构',
    13: '减少破折号使用（超过 2 个即过度），改用逗号或分号',
    14: '删除不必要的粗体强调',
    15: '将内联标题列表改写为连贯的段落',
    16: '（中文标题无大小写问题，无需处理）',
    17: '删除表情符号，使用文字表达',
    18: '将弯引号替换为直引号',
    19: '删除协作交流痕迹（"希望这对您有帮助""当然！"等），直接呈现内容',
    20: '删除知识截止日期相关的免责声明',
    21: '删除谄媚/恭维语气，保持平实客观',
    22: '删除填充短语（"值得注意的是""总的来说"等），直接陈述',
    23: '减少过度限定词（"可能""或许""大概"等）的堆叠使用',
    24: '将通用积极结论替换为具体的未来计划或事实',
}


def _count_matches(pattern: re.Pattern, text: str) -> int:
    """统计正则匹配次数"""
    return len(pattern.findall(text))


def humanize_zh(text: str, aggressive: bool = False) -> dict:
    """检测中文文本中的 AI 写作痕迹，返回检测结果和优化建议

    Args:
        text: 待检测的中文文本
        aggressive: 是否启用严格检测模式（检测更多边缘情况）

    Returns:
        包含检测结果的字典:
        {
            "detected_patterns": [{"pattern_id": int, "name": str, "count": int, "matches": [str]}],
            "total_issues": int,
            "score": int,  # 0-100，越高越像人类书写
            "suggestions": [str],
            "text_length": int,
            "aggressive": bool,
        }
    """
    if not text or not text.strip():
        return {
            "detected_patterns": [],
            "total_issues": 0,
            "score": 100,
            "suggestions": [],
            "text_length": 0,
            "aggressive": aggressive,
        }

    detected = []
    total_issues = 0

    # ── 模式 1: 过度强调意义 ──
    m1 = PATTERN_1_RE.findall(text)
    if m1:
        matched_texts = list(set(m1))
        detected.append({
            "pattern_id": 1,
            "name": "过度强调意义、遗产和更广泛的趋势",
            "count": len(m1),
            "matches": matched_texts[:10],
        })
        total_issues += len(m1)

    # ── 模式 2: 过度强调知名度 ──
    m2 = PATTERN_2_RE.findall(text)
    if m2:
        detected.append({
            "pattern_id": 2,
            "name": "过度强调知名度和媒体报道",
            "count": len(m2),
            "matches": list(set(m2)),
        })
        total_issues += len(m2)

    # ── 模式 3: 肤浅分析 ──
    m3 = PATTERN_3_RE.findall(text)
    if m3:
        detected.append({
            "pattern_id": 3,
            "name": "以 -ing 结尾的肤浅分析（句末分析短语）",
            "count": len(m3),
            "matches": m3[:10],
        })
        total_issues += len(m3)

    # ── 模式 4: 宣传语言 ──
    m4 = PATTERN_4_RE.findall(text)
    if m4:
        detected.append({
            "pattern_id": 4,
            "name": "宣传和广告式语言",
            "count": len(m4),
            "matches": list(set(m4)),
        })
        total_issues += len(m4)

    # ── 模式 5: 模糊归因 ──
    m5 = PATTERN_5_RE.findall(text)
    if m5:
        detected.append({
            "pattern_id": 5,
            "name": "模糊归因和含糊措辞",
            "count": len(m5),
            "matches": list(set(m5)),
        })
        total_issues += len(m5)

    # ── 模式 6: 挑战与未来展望 ──
    m6 = PATTERN_6_RE.findall(text)
    if m6:
        detected.append({
            "pattern_id": 6,
            "name": "提纲式的「挑战与未来展望」部分",
            "count": len(m6),
            "matches": list(set(m6)),
        })
        total_issues += len(m6)

    # ── 模式 7: AI 词汇 ──
    m7 = PATTERN_7_RE.findall(text)
    if m7:
        matched_texts = list(set(m7))
        detected.append({
            "pattern_id": 7,
            "name": "过度使用的 AI 高频词汇",
            "count": len(m7),
            "matches": matched_texts[:10],
        })
        total_issues += len(m7)

    # ── 模式 8: 系动词回避 ──
    m8 = PATTERN_8_RE.findall(text)
    if m8:
        detected.append({
            "pattern_id": 8,
            "name": "避免使用「是」（系动词回避）",
            "count": len(m8),
            "matches": list(set(m8)),
        })
        total_issues += len(m8)

    # ── 模式 9: 否定式排比 ──
    m9 = PATTERN_9_RE.findall(text)
    if m9:
        detected.append({
            "pattern_id": 9,
            "name": "否定式排比（「不仅……而且……」等）",
            "count": len(m9),
            "matches": m9[:10],
        })
        total_issues += len(m9)

    # ── 模式 10: 三段式法则 ──
    m10 = PATTERN_10_RE.findall(text)
    if m10 or (aggressive and len(PATTERN_10_RE.findall(text)) > 0):
        if m10:
            detected.append({
                "pattern_id": 10,
                "name": "三段式法则过度使用（顿号列举三项）",
                "count": len(m10),
                "matches": m10[:10],
            })
            total_issues += len(m10)

    # ── 模式 11: 刻意换词（同义词循环）──
    m11 = PATTERN_11_RE.findall(text)
    if m11:
        detected.append({
            "pattern_id": 11,
            "name": "刻意换词（同义词循环）",
            "count": len(m11),
            "matches": m11[:10],
        })
        total_issues += len(m11)

    # ── 模式 12: 虚假范围 ──
    m12 = PATTERN_12_RE.findall(text)
    if m12:
        detected.append({
            "pattern_id": 12,
            "name": "虚假范围（「从 X 到 Y」结构）",
            "count": len(m12),
            "matches": m12[:10],
        })
        total_issues += len(m12)

    # ── 模式 13: 破折号过度使用 ──
    dash_count = _count_matches(PATTERN_13_RE, text)
    threshold = 1 if aggressive else 2
    if dash_count > threshold:
        detected.append({
            "pattern_id": 13,
            "name": "破折号过度使用",
            "count": dash_count,
            "matches": [f"使用了 {dash_count} 个破折号"],
        })
        total_issues += dash_count - threshold

    # ── 模式 14: 粗体过度使用 ──
    m14 = PATTERN_14_RE.findall(text)
    bold_count = len(m14)
    if bold_count > (1 if aggressive else 2):
        detected.append({
            "pattern_id": 14,
            "name": "粗体过度使用",
            "count": bold_count,
            "matches": m14[:10],
        })
        total_issues += bold_count

    # ── 模式 15: 内联标题垂直列表 ──
    m15 = PATTERN_15_RE.findall(text)
    if m15:
        detected.append({
            "pattern_id": 15,
            "name": "内联标题垂直列表",
            "count": len(m15),
            "matches": m15[:10],
        })
        total_issues += len(m15)

    # ── 模式 16: 标题大写（中文不适用）──

    # ── 模式 17: 表情符号 ──
    emoji_matches = EMOJI_PATTERN.findall(text)
    if emoji_matches:
        detected.append({
            "pattern_id": 17,
            "name": "表情符号使用",
            "count": len(emoji_matches),
            "matches": emoji_matches[:10],
        })
        total_issues += len(emoji_matches)

    # ── 模式 18: 弯引号 ──
    m18 = PATTERN_18_RE.findall(text)
    if m18:
        detected.append({
            "pattern_id": 18,
            "name": "弯引号（AI 常见格式）",
            "count": len(m18),
            "matches": m18[:10],
        })
        total_issues += len(m18)

    # ── 模式 19: 协作交流痕迹 ──
    m19 = PATTERN_19_RE.findall(text)
    if m19:
        detected.append({
            "pattern_id": 19,
            "name": "协作交流痕迹",
            "count": len(m19),
            "matches": list(set(m19)),
        })
        total_issues += len(m19)

    # ── 模式 20: 知识截止日期免责声明 ──
    m20 = PATTERN_20_RE.findall(text)
    if m20:
        detected.append({
            "pattern_id": 20,
            "name": "知识截止日期免责声明",
            "count": len(m20),
            "matches": list(set(m20)),
        })
        total_issues += len(m20)

    # ── 模式 21: 谄媚语气 ──
    m21 = PATTERN_21_RE.findall(text)
    if m21:
        detected.append({
            "pattern_id": 21,
            "name": "谄媚/卑躬屈膝的语气",
            "count": len(m21),
            "matches": list(set(m21)),
        })
        total_issues += len(m21)

    # ── 模式 22: 填充短语 ──
    m22 = PATTERN_22_RE.findall(text)
    if m22:
        detected.append({
            "pattern_id": 22,
            "name": "填充短语（「值得注意的是」「总的来说」等）",
            "count": len(m22),
            "matches": list(set(m22)),
        })
        total_issues += len(m22)

    # ── 模式 23: 过度限定 ──
    m23 = PATTERN_23_RE.findall(text)
    if m23:
        detected.append({
            "pattern_id": 23,
            "name": "过度限定（堆叠模糊词）",
            "count": len(m23),
            "matches": m23[:10],
        })
        total_issues += len(m23)

    # ── 模式 24: 通用积极结论 ──
    m24 = PATTERN_24_RE.findall(text)
    if m24:
        detected.append({
            "pattern_id": 24,
            "name": "通用积极结论",
            "count": len(m24),
            "matches": list(set(m24)),
        })
        total_issues += len(m24)

    # ── 额外：三连句检测（同长度句子连续出现）──
    if aggressive:
        sentences = re.split(r"[。！？\n]+", text)
        sentences = [s.strip() for s in sentences if len(s.strip()) >= 8]
        same_length_count = 0
        for i in range(len(sentences) - 2):
            l1, l2, l3 = len(sentences[i]), len(sentences[i + 1]), len(sentences[i + 2])
            if abs(l1 - l2) < 5 and abs(l2 - l3) < 5:
                same_length_count += 1
        if same_length_count > 0:
            detected.append({
                "pattern_id": 99,
                "name": "连续三句长度相似（机械节奏）",
                "count": same_length_count,
                "matches": [f"检测到 {same_length_count} 处连续三句长度相近"],
            })
            total_issues += same_length_count

    # ── 计算得分 ──
    text_len = len(text)
    # 基础分 100，每个问题扣分（根据文本长度调整）
    if text_len > 0:
        raw_score = max(0, 100 - (total_issues * 100 / max(text_len / 20, 1)))
        score = min(100, int(raw_score))
    else:
        score = 100

    # ── 生成建议列表 ──
    suggestion_ids = set(d["pattern_id"] for d in detected)
    suggestions = [SUGGESTIONS[pid] for pid in sorted(suggestion_ids) if pid in SUGGESTIONS]

    return {
        "detected_patterns": detected,
        "total_issues": total_issues,
        "score": score,
        "suggestions": suggestions,
        "text_length": text_len,
        "aggressive": aggressive,
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
            "module_name": "text_tools",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
