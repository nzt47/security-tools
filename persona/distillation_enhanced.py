"""
Personality Distillation System (增强版)
自动学习和提取用户偏好的人格蒸馏系统
增强功能：
- 情感倾向分析
- 对话风格迁移检测
- 用户满意度推断
- 交互节奏偏好
- 自适应学习率
- 偏好置信度
- 偏好衰减机制
"""

import logging
import json
import copy
import math
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from collections import defaultdict

logger = logging.getLogger(__name__)


class PersonalityPreferenceExtractor:
    """
    人格偏好提取器（增强版）

    基于用户历史交互数据，提取以下偏好：
    - 表达风格偏好
    - 话题兴趣度
    - 交互时间模式
    - 工具使用偏好
    - 情感倾向分析（新增）
    - 对话风格迁移检测（新增）
    - 用户满意度推断（新增）
    - 交互节奏偏好（新增）
    """

    def __init__(self, data_dir: str = "data/persona", learning_rate: float = 0.1):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.learning_rate = learning_rate
        self.adaptive_learning = True

        self.preferences = {
            "expression_style": {
                "formal": 0.5,
                "casual": 0.5,
                "concise": 0.5,
                "verbose": 0.5,
                "humorous": 0.5,
                "serious": 0.5,
                "emotional": 0.5,
                "rational": 0.5
            },
            "topic_interest": {},
            "interaction_pattern": {
                "morning": 0.0,
                "afternoon": 0.0,
                "evening": 0.0,
                "night": 0.0
            },
            "tool_preference": {},
            "response_length": 0.5,
            "emotional_tendency": {
                "positive": 0.5,
                "negative": 0.5,
                "neutral": 0.5
            },
            "satisfaction_indicators": {},
            "interaction_rhythm": {
                "avg_response_time": 0.0,
                "message_frequency": 0.0,
                "burst_indicator": 0.0
            },
            "confidence": {},
            "decay_factor": 0.95,
            "last_updated": None
        }

        self._confidence_threshold = 0.3
        self._message_buffer = []
        self._batch_size = 10
        self._load_preferences()

    def extract_from_conversation(self, conversation_history: List[Dict]) -> Dict:
        """
        从对话历史中提取偏好

        Args:
            conversation_history: 对话列表
                [{'role': 'user', 'content': '...', 'timestamp': '...'}, ...]

        Returns:
            更新后的偏好字典
        """
        if not conversation_history:
            return self.preferences

        user_messages = [msg for msg in conversation_history if msg.get('role') == 'user']
        assistant_messages = [msg for msg in conversation_history if msg.get('role') == 'assistant']

        self._extract_expression_style(user_messages)
        self._extract_topic_interest(conversation_history)
        self._extract_interaction_pattern(user_messages)
        self._extract_tool_preference(conversation_history)
        self._extract_emotional_tendency(user_messages)
        self._extract_interaction_rhythm(conversation_history)
        self._infer_satisfaction(conversation_history)
        self._update_confidence(conversation_history)

        self.preferences["last_updated"] = datetime.now().isoformat()
        self._save_preferences()

        return self.preferences

    def update_incremental(self, new_message: Dict) -> Dict:
        """
        增量更新偏好（单条消息）
        """
        self._message_buffer.append(new_message)

        if len(self._message_buffer) >= self._batch_size:
            self.extract_from_conversation(self._message_buffer)
            self._message_buffer = []

        if new_message.get('role') == 'user':
            self._extract_topic_interest_incremental(new_message)

        self.preferences["last_updated"] = datetime.now().isoformat()
        return self.preferences

    def _extract_topic_interest_incremental(self, message: Dict):
        """增量提取话题兴趣"""
        content = message.get('content', '')

        topic_keywords = {
            "编程": ['代码', '编程', 'Python', 'Java', '开发', '调试', 'bug'],
            "学习": ['学习', '读书', '课程', '知识', '研究'],
            "工作": ['工作', '会议', '任务', '项目'],
            "娱乐": ['游戏', '电影', '音乐', '视频', '玩'],
        }

        for topic, keywords in topic_keywords.items():
            for kw in keywords:
                if kw.lower() in content.lower():
                    if self.adaptive_learning:
                        confidence = self.preferences["confidence"].get(f"topic_{topic}", 0.5)
                        adaptive_lr = self.learning_rate * confidence
                        self.preferences["topic_interest"][topic] = \
                            self.preferences["topic_interest"].get(topic, 0.3) + adaptive_lr
                    else:
                        self.preferences["topic_interest"][topic] = \
                            self.preferences["topic_interest"].get(topic, 0.3) + self.learning_rate * 0.5

                    self.preferences["topic_interest"][topic] = min(
                        self.preferences["topic_interest"][topic], 1.0)

    def _extract_expression_style(self, user_messages: List[Dict]):
        """提取表达风格偏好"""
        if not user_messages:
            return

        style_scores = {
            "formal": 0,
            "casual": 0,
            "concise": 0,
            "verbose": 0,
            "humorous": 0,
            "serious": 0,
            "emotional": 0,
            "rational": 0
        }

        casual_keywords = ['哈哈', '哇', '哦', '呢', '啊', '吧', '呀', '哈', '嘿嘿', '嘻嘻', '哇塞', '天呐', '我的天', '厉害', '牛', '强']
        formal_keywords = ['您好', '请', '感谢', '抱歉', '希望', '建议', '是否可以', '能否', '非常感谢', '请您', '请帮我', '请问', '不好意思']
        emotional_keywords = ['开心', '难过', '高兴', '生气', '喜欢', '讨厌', '感动', '失望', '激动', '太棒了', '好棒', '好开心', '很难过', '气死我了']
        rational_keywords = ['因为', '所以', '因此', '但是', '然而', '数据', '统计', '分析', '证明', '根据', '研究', '结论', '其实', '实际上']
        humorous_keywords = ['哈哈', '笑死', '逗', '有趣', '好玩', '搞笑', '笑死我了', '太逗了', '哈哈哈哈']

        for msg in user_messages:
            content = msg.get('content', '')
            if not content:
                continue

            if len(content) < 25:
                style_scores["concise"] += 2
            elif len(content) < 70:
                style_scores["concise"] += 1
                style_scores["verbose"] += 2
            else:
                style_scores["verbose"] += 3

            for kw in casual_keywords:
                if kw in content:
                    style_scores["casual"] += 2
            for kw in formal_keywords:
                if kw in content:
                    style_scores["formal"] += 2
            for kw in emotional_keywords:
                if kw in content:
                    style_scores["emotional"] += 2
            for kw in rational_keywords:
                if kw in content:
                    style_scores["rational"] += 2
            for kw in humorous_keywords:
                if kw in content:
                    style_scores["humorous"] += 3

            if '!' in content or '！' in content:
                style_scores["emotional"] += 1
            if '?' in content or '？' in content:
                style_scores["serious"] += 1
            if '...' in content or '……' in content:
                style_scores["casual"] += 1

            emojis = ['😀', '😄', '😁', '😂', '🤣', '😊', '😎', '🥳', '😭', '😤', '😠', '😢']
            for emoji in emojis:
                if emoji in content:
                    style_scores["emotional"] += 1
                    style_scores["casual"] += 1

        for key in style_scores:
            if key in ["formal", "casual"]:
                total = max(style_scores["formal"] + style_scores["casual"], 2)
                self.preferences["expression_style"][key] = style_scores[key] / total
            elif key in ["concise", "verbose"]:
                total = max(style_scores["concise"] + style_scores["verbose"], 2)
                self.preferences["expression_style"][key] = style_scores[key] / total
            elif key in ["humorous", "serious"]:
                total = max(style_scores["humorous"] + style_scores["serious"], 2)
                self.preferences["expression_style"][key] = style_scores[key] / total
            elif key in ["emotional", "rational"]:
                total = max(style_scores["emotional"] + style_scores["rational"], 2)
                self.preferences["expression_style"][key] = style_scores[key] / total

        logger.debug(f"提取的表达风格: {self.preferences['expression_style']}")

    def _extract_topic_interest(self, conversation_history: List[Dict]):
        """提取话题兴趣度"""
        topic_keywords = {
            "编程": ['代码', '编程', 'Python', 'Java', '开发', '调试', 'bug', '算法', '程序', '函数', '变量'],
            "学习": ['学习', '读书', '课程', '知识', '研究', '论文', '教育', '教程', '学', '学会', '练习'],
            "工作": ['工作', '会议', '任务', '项目', '老板', '同事', '公司', '加班', 'KPI', '绩效', '汇报'],
            "生活": ['生活', '吃饭', '睡觉', '休息', '家', '朋友', '家人', '聚会', '周末', '假期'],
            "娱乐": ['游戏', '电影', '音乐', '视频', '追剧', '动漫', '娱乐', '综艺', '玩', '好看', '好听'],
            "健康": ['健康', '运动', '健身', '饮食', '睡眠', '医院', '医生', '锻炼', '跑步', '瑜伽'],
            "科技": ['科技', 'AI', '人工智能', '机器人', '手机', '电脑', '科技产品', 'ChatGPT', '模型']
        }

        topic_counts = defaultdict(int)
        all_messages = [msg.get('content', '') for msg in conversation_history]

        for content in all_messages:
            for topic, keywords in topic_keywords.items():
                for kw in keywords:
                    if kw.lower() in content.lower():
                        topic_counts[topic] += 1

        max_count = max(topic_counts.values()) if topic_counts else 1
        for topic, count in topic_counts.items():
            self.preferences["topic_interest"][topic] = min(count / max_count, 1.0)

        logger.debug(f"提取的话题兴趣: {self.preferences['topic_interest']}")

    def _extract_interaction_pattern(self, user_messages: List[Dict]):
        """提取交互时间模式"""
        if not user_messages:
            return

        hour_counts = [0] * 24

        for msg in user_messages:
            try:
                ts = msg.get('timestamp')
                if ts:
                    if isinstance(ts, str):
                        dt = datetime.fromisoformat(ts)
                    else:
                        dt = datetime.fromtimestamp(ts)
                    hour_counts[dt.hour] += 1
            except Exception as e:
                logger.debug(f"时间解析失败: {e}")

        total = max(sum(hour_counts), 1)

        self.preferences["interaction_pattern"]["morning"] = sum(hour_counts[6:12]) / total
        self.preferences["interaction_pattern"]["afternoon"] = sum(hour_counts[12:18]) / total
        self.preferences["interaction_pattern"]["evening"] = sum(hour_counts[18:23]) / total
        self.preferences["interaction_pattern"]["night"] = (sum(hour_counts[23:]) + sum(hour_counts[:6])) / total

        logger.debug(f"提取的交互模式: {self.preferences['interaction_pattern']}")

    def _extract_tool_preference(self, conversation_history: List[Dict]):
        """提取工具使用偏好"""
        tool_keywords = {
            "search": ['搜索', '查找', '百度', '谷歌', '搜索一下', '查一下', '找', '看看'],
            "calculation": ['计算', '算一下', '多少', '等于', '加减乘除', '数学', '数'],
            "memory": ['记住', '保存', '记录', '别忘了', '记下来', '存'],
            "creative": ['写', '创作', '生成', '帮我写', '故事', '文章', '画', '设计']
        }

        tool_counts = defaultdict(int)
        all_content = ' '.join([msg.get('content', '') for msg in conversation_history])

        for tool, keywords in tool_keywords.items():
            for kw in keywords:
                if kw in all_content:
                    tool_counts[tool] += 1

        max_count = max(tool_counts.values()) if tool_counts else 1
        for tool, count in tool_counts.items():
            self.preferences["tool_preference"][tool] = min(count / max_count, 1.0)

        logger.debug(f"提取的工具偏好: {self.preferences['tool_preference']}")

    def _extract_emotional_tendency(self, user_messages: List[Dict]):
        """提取情感倾向（新增）"""
        positive_words = ['开心', '高兴', '喜欢', '棒', '好', '赞', '谢谢', '感谢', '哈哈', '太好了']
        negative_words = ['难过', '生气', '讨厌', '烦', '讨厌', '失望', '糟糕', '不行', '不满意', '气']
        neutral_words = ['好的', '可以', '行', '嗯', '哦', '这样', '怎样']

        emotion_scores = {"positive": 0, "negative": 0, "neutral": 0}

        for msg in user_messages:
            content = msg.get('content', '')
            if not content:
                continue

            for word in positive_words:
                if word in content:
                    emotion_scores["positive"] += 1
            for word in negative_words:
                if word in content:
                    emotion_scores["negative"] += 1
            for word in neutral_words:
                if word in content:
                    emotion_scores["neutral"] += 1

        total = sum(emotion_scores.values())
        if total > 0:
            self.preferences["emotional_tendency"]["positive"] = emotion_scores["positive"] / total
            self.preferences["emotional_tendency"]["negative"] = emotion_scores["negative"] / total
            self.preferences["emotional_tendency"]["neutral"] = emotion_scores["neutral"] / total

        logger.debug(f"提取的情感倾向: {self.preferences['emotional_tendency']}")

    def _extract_interaction_rhythm(self, conversation_history: List[Dict]):
        """提取交互节奏偏好（新增）"""
        if len(conversation_history) < 2:
            return

        timestamps = []
        for msg in conversation_history:
            try:
                ts = msg.get('timestamp')
                if ts:
                    if isinstance(ts, str):
                        dt = datetime.fromisoformat(ts)
                    else:
                        dt = datetime.fromtimestamp(ts)
                    timestamps.append(dt)
            except Exception:
                continue

        if len(timestamps) >= 2:
            timestamps.sort()
            time_diffs = [(timestamps[i+1] - timestamps[i]).total_seconds()
                         for i in range(len(timestamps)-1)]

            if time_diffs:
                self.preferences["interaction_rhythm"]["avg_response_time"] = sum(time_diffs) / len(time_diffs)
                self.preferences["interaction_rhythm"]["message_frequency"] = len(conversation_history) / max(
                    (timestamps[-1] - timestamps[0]).total_seconds(), 1)

                burst_threshold = 60
                burst_count = sum(1 for diff in time_diffs if diff < burst_threshold)
                self.preferences["interaction_rhythm"]["burst_indicator"] = burst_count / len(time_diffs)

        logger.debug(f"提取的交互节奏: {self.preferences['interaction_rhythm']}")

    def _infer_satisfaction(self, conversation_history: List[Dict]):
        """推断用户满意度（新增）"""
        satisfaction_indicators = {
            "follow_up_questions": 0,
            "positive_acknowledgments": 0,
            "negative_feedback": 0,
            "repetition": 0
        }

        positive_ack = ['好的', '明白了', '谢谢', '了解', '对的', '没错', '知道了', '好的']
        negative_feedback = ['不对', '不是', '错了', '不好', '不行', '不满意', '重新']
        follow_up_keywords = ['然后', '还有', '另外', '接下来', '继续', '还有吗']

        user_contents = [msg.get('content', '').lower()
                        for msg in conversation_history if msg.get('role') == 'user']

        for i, content in enumerate(user_contents):
            for kw in positive_ack:
                if kw in content:
                    satisfaction_indicators["positive_acknowledgments"] += 1
            for kw in negative_feedback:
                if kw in content:
                    satisfaction_indicators["negative_feedback"] += 1
            for kw in follow_up_keywords:
                if kw in content:
                    satisfaction_indicators["follow_up_questions"] += 1

            if i > 0 and content == user_contents[i-1]:
                satisfaction_indicators["repetition"] += 1

        self.preferences["satisfaction_indicators"] = satisfaction_indicators

        total_interactions = len(user_contents)
        if total_interactions > 0:
            satisfaction_score = (
                satisfaction_indicators["positive_acknowledgments"] * 0.3 +
                satisfaction_indicators["follow_up_questions"] * 0.2 +
                satisfaction_indicators["negative_feedback"] * -0.3 +
                satisfaction_indicators["repetition"] * -0.2
            ) / total_interactions
            satisfaction_score = max(0.0, min(1.0, 0.5 + satisfaction_score))

            self.preferences["satisfaction_indicators"]["score"] = satisfaction_score

        logger.debug(f"推断的满意度: {satisfaction_indicators}")

    def _update_confidence(self, conversation_history: List[Dict]):
        """更新偏好置信度（新增）"""
        message_count = len([msg for msg in conversation_history if msg.get('role') == 'user'])

        base_confidence = min(message_count / 50.0, 1.0)

        confidence_decay = self.preferences.get("decay_factor", 0.95)
        last_updated = self.preferences.get("last_updated")

        if last_updated:
            try:
                last_dt = datetime.fromisoformat(last_updated)
                days_since_update = (datetime.now() - last_dt).days
                if days_since_update > 0:
                    confidence_decay = math.pow(self.preferences["decay_factor"], days_since_update)
            except Exception:
                pass

        adjusted_confidence = base_confidence * confidence_decay

        self.preferences["confidence"]["overall"] = adjusted_confidence

        for topic in self.preferences["topic_interest"]:
            topic_count = sum(1 for msg in conversation_history
                             if any(kw in msg.get('content', '').lower()
                                   for kw in topic.lower()))
            self.preferences["confidence"][f"topic_{topic}"] = min(topic_count / 10.0, 1.0)

        logger.debug(f"更新的置信度: {self.preferences['confidence']}")

    def generate_personality_prompt(self) -> str:
        """基于提取的偏好生成人格提示词"""
        style = self.preferences["expression_style"]
        topics = self.preferences["topic_interest"]
        pattern = self.preferences["interaction_pattern"]

        prompt_parts = []

        if style.get("casual", 0.5) > style.get("formal", 0.5) + 0.1:
            prompt_parts.append("使用轻松随意的语气")
        elif style.get("formal", 0.5) > style.get("casual", 0.5) + 0.1:
            prompt_parts.append("使用正式专业的语气")

        if style.get("concise", 0.5) > style.get("verbose", 0.5) + 0.1:
            prompt_parts.append("回答简洁明了")
        elif style.get("verbose", 0.5) > style.get("concise", 0.5) + 0.1:
            prompt_parts.append("回答详细周全")

        if style.get("humorous", 0.5) > style.get("serious", 0.5) + 0.1:
            prompt_parts.append("适当加入幽默感")
        elif style.get("serious", 0.5) > style.get("humorous", 0.5) + 0.1:
            prompt_parts.append("保持严肃认真")

        if style.get("emotional", 0.5) > style.get("rational", 0.5) + 0.1:
            prompt_parts.append("表达丰富的情感")
        elif style.get("rational", 0.5) > style.get("emotional", 0.5) + 0.1:
            prompt_parts.append("保持理性客观")

        if topics:
            sorted_topics = sorted(topics.items(), key=lambda x: x[1], reverse=True)[:3]
            topic_list = ', '.join([topic for topic, score in sorted_topics if score > 0.3])
            if topic_list:
                prompt_parts.append(f"对以下话题特别感兴趣: {topic_list}")

        if pattern:
            active_times = sorted(pattern.items(), key=lambda x: x[1], reverse=True)[:1]
            active_time, score = active_times[0]
            if score > 0.4:
                if active_time == "morning":
                    prompt_parts.append("你通常在早晨比较活跃，提供晨间友好问候")
                elif active_time == "evening":
                    prompt_parts.append("你通常在晚上比较活跃，提供晚间陪伴")

        return "你应该: " + "; ".join(prompt_parts) if prompt_parts else "使用自然友好的语气交流"

    def get_preference_confidence(self, preference_type: str) -> float:
        """获取指定偏好的置信度"""
        return self.preferences["confidence"].get(preference_type, 0.0)

    def is_confidence_sufficient(self, preference_type: str) -> bool:
        """检查置信度是否足够"""
        confidence = self.get_preference_confidence(preference_type)
        return confidence >= self._confidence_threshold

    def apply_decay(self, decay_factor: float = None):
        """应用偏好衰减（新增）"""
        if decay_factor is not None:
            self.preferences["decay_factor"] = decay_factor

        decay = self.preferences["decay_factor"]

        for topic in self.preferences["topic_interest"]:
            self.preferences["topic_interest"][topic] *= decay

        for style_key in self.preferences["expression_style"]:
            if isinstance(self.preferences["expression_style"][style_key], (int, float)):
                base_value = self.preferences["expression_style"][style_key]
                if 0.4 < base_value < 0.6:
                    self.preferences["expression_style"][style_key] = 0.5 - (0.5 - base_value) * decay

        for confidence_key in self.preferences["confidence"]:
            self.preferences["confidence"][confidence_key] *= decay

        logger.info(f"已应用偏好衰减 (factor={decay})")

    def export_preferences(self) -> Dict:
        """导出偏好数据"""
        return {
            "extracted_at": datetime.now().isoformat(),
            "preferences": self.preferences
        }

    def _save_preferences(self):
        """保存偏好到文件"""
        try:
            save_path = self.data_dir / "preferences.json"
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(self.preferences, f, ensure_ascii=False, indent=2)
            logger.debug(f"偏好已保存到: {save_path}")
        except Exception as e:
            logger.error(f"保存偏好失败: {e}")

    def _load_preferences(self):
        """从文件加载偏好"""
        try:
            load_path = self.data_dir / "preferences.json"
            if load_path.exists():
                with open(load_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    for key, value in loaded.items():
                        if key in self.preferences:
                            if isinstance(value, dict):
                                self.preferences[key].update(value)
                            else:
                                self.preferences[key] = value
                logger.debug(f"偏好已从 {load_path} 加载")
        except Exception as e:
            logger.debug(f"加载偏好失败（可能是首次运行）: {e}")


class DistillationStrategy(Enum):
    """蒸馏策略枚举"""
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"
    CUSTOM = "custom"


@dataclass
class DistillationConfig:
    """蒸馏配置"""
    strategy: DistillationStrategy = DistillationStrategy.BALANCED
    learning_rate: float = 0.1
    min_confidence: float = 0.3
    time_decay_factor: float = 0.95
    stability_weight: float = 0.7
    adaptation_weight: float = 0.3
    max_history_snapshots: int = 50


@dataclass
class DistillationResult:
    """蒸馏结果"""
    success: bool
    updated_persona: Dict[str, Any]
    changes_made: List[str]
    evaluation_score: float
    timestamp: str
    strategy_used: str


class PersonaDistiller:
    """人格蒸馏器 - 协调偏好提取到人格更新的完整流程"""

    def __init__(self, persona_model=None, config: DistillationConfig = None,
                 lazy_load: bool = True):
        """
        初始化人格蒸馏器

        Args:
            persona_model: PersonaModel 实例
            config: 蒸馏配置
            lazy_load: 是否懒加载历史数据（默认True，可提升初始化速度）
        """
        from persona.persona_model_enhanced import PersonaModel

        self.persona_model = persona_model or PersonaModel()
        self.config = config or DistillationConfig()
        self.history: List[Dict[str, Any]] = []
        self.snapshots: List[Dict[str, Any]] = []
        self.evaluation_metrics = {
            "total_distillations": 0,
            "success_count": 0,
            "average_score": 0.0,
            "trend_analysis": []
        }

        # 懒加载历史数据：仅记录元数据，不加载完整历史
        self._history_loaded = False
        if not lazy_load:
            self._load_history()
            logger.info("PersonaDistiller 初始化完成（同步加载历史）")
        else:
            logger.info("PersonaDistiller 初始化完成（懒加载历史）")

    def _ensure_history_loaded(self):
        """确保历史数据已加载（懒加载触发）"""
        if not self._history_loaded:
            self._load_history()
            self._history_loaded = True

    def distill_from_preferences(self, preferences: Dict[str, Any],
                                strategy: Optional[DistillationStrategy] = None) -> DistillationResult:
        """
        从偏好数据中蒸馏人格

        Args:
            preferences: 偏好数据
            strategy: 蒸馏策略（可选）

        Returns:
            DistillationResult: 蒸馏结果
        """
        # 确保历史数据已加载
        self._ensure_history_loaded()

        if strategy:
            self.config.strategy = strategy

        logger.info(f"开始人格蒸馏，策略: {self.config.strategy.value}")

        changes = []
        original_persona = copy.deepcopy(self.persona_model.persona)

        try:
            # 1. 根据策略确定更新强度
            update_factor = self._get_update_factor()

            # 2. 应用表达风格更新
            style_changes = self._apply_style_updates(preferences, update_factor)
            changes.extend(style_changes)

            # 3. 应用话题兴趣更新
            topic_changes = self._apply_topic_updates(preferences, update_factor)
            changes.extend(topic_changes)

            # 4. 应用交互模式更新
            pattern_changes = self._apply_pattern_updates(preferences, update_factor)
            changes.extend(pattern_changes)

            # 5. 应用大五人格调整
            trait_changes = self._apply_trait_updates(preferences, update_factor)
            changes.extend(trait_changes)

            # 6. 评估蒸馏效果
            evaluation_score = self._evaluate_distillation(original_persona)

            # 7. 创建快照
            if changes:
                self._create_snapshot(f"distillation_{len(self.history)}")

            # 8. 记录历史
            self._record_distillation(evaluation_score)

            result = DistillationResult(
                success=True,
                updated_persona=self.persona_model.persona,
                changes_made=changes,
                evaluation_score=evaluation_score,
                timestamp=datetime.now().isoformat(),
                strategy_used=self.config.strategy.value
            )

            logger.info(f"人格蒸馏完成，评分: {evaluation_score:.2f}，变更: {len(changes)}")
            return result

        except Exception as e:
            logger.error(f"人格蒸馏失败: {e}")
            return DistillationResult(
                success=False,
                updated_persona=self.persona_model.persona,
                changes_made=[],
                evaluation_score=0.0,
                timestamp=datetime.now().isoformat(),
                strategy_used=self.config.strategy.value
            )

    def merge_personas(self, personas: List[Dict[str, Any]],
                      weights: Optional[List[float]] = None) -> Dict[str, Any]:
        """
        融合多个人格

        Args:
            personas: 人格数据列表
            weights: 权重列表（可选）

        Returns:
            Dict[str, Any]: 融合后的人格
        """
        # 确保历史数据已加载
        self._ensure_history_loaded()

        if not personas:
            return self.persona_model.persona

        if weights is None:
            weights = [1.0 / len(personas)] * len(personas)

        if len(weights) != len(personas):
            raise ValueError("人格数量和权重数量不匹配")

        logger.info(f"开始融合 {len(personas)} 个人格")

        merged_persona = copy.deepcopy(personas[0])

        # 融合表达风格
        if "layers" in merged_persona and "layer2" in merged_persona["layers"]:
            for style_key in ["tone", "emotion", "conciseness", "initiative", "humor", "empathy"]:
                values = []
                for i, persona in enumerate(personas):
                    if "layers" in persona and "layer2" in persona["layers"]:
                        value = persona["layers"]["layer2"].get(style_key, 0.5)
                        values.append(value * weights[i])

                if values:
                    merged_persona["layers"]["layer2"][style_key] = sum(values)

        # 融合大五人格
        if "traits" in merged_persona and "big_five" in merged_persona["traits"]:
            for trait_key in ["openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"]:
                values = []
                for i, persona in enumerate(personas):
                    if "traits" in persona and "big_five" in persona["traits"]:
                        value = persona["traits"]["big_five"].get(trait_key, 0.5)
                        values.append(value * weights[i])

                if values:
                    merged_persona["traits"]["big_five"][trait_key] = sum(values)

        logger.info("人格融合完成")
        return merged_persona

    def _get_update_factor(self) -> float:
        """根据策略获取更新因子"""
        strategy_factors = {
            DistillationStrategy.CONSERVATIVE: 0.3,
            DistillationStrategy.BALANCED: 0.6,
            DistillationStrategy.AGGRESSIVE: 1.0,
            DistillationStrategy.CUSTOM: self.config.learning_rate
        }
        return strategy_factors.get(self.config.strategy, 0.6)

    def _apply_style_updates(self, preferences: Dict[str, Any],
                           update_factor: float) -> List[str]:
        """应用表达风格更新"""
        changes = []
        expression_style = preferences.get("expression_style", {})

        if not expression_style:
            return changes

        current_style = self.persona_model.get_expression_style()

        for key in ["tone", "emotion", "conciseness", "initiative", "humor", "empathy"]:
            if key in expression_style:
                old_value = current_style.get(key, 0.5)
                new_value = expression_style[key]

                delta = (new_value - old_value) * update_factor * self.config.learning_rate
                target_value = old_value + delta

                # 更新人格模型
                self.persona_model.update_expression_style(**{key: target_value})

                if abs(delta) > 0.01:
                    changes.append(f"style_{key}: {old_value:.2f} -> {target_value:.2f}")

        return changes

    def _apply_topic_updates(self, preferences: Dict[str, Any],
                           update_factor: float) -> List[str]:
        """应用话题兴趣更新"""
        changes = []
        topic_interest = preferences.get("topic_interest", {})

        if not topic_interest:
            return changes

        # 根据话题兴趣调整表达风格
        if "编程" in topic_interest and topic_interest["编程"] > 0.5:
            current_conciseness = self.persona_model.get_expression_style().get("conciseness", 0.5)
            new_conciseness = min(0.8, current_conciseness + 0.1 * update_factor)
            self.persona_model.update_expression_style(conciseness=new_conciseness)
            changes.append(f"topic_effect: conciseness increased for programming")

        return changes

    def _apply_pattern_updates(self, preferences: Dict[str, Any],
                             update_factor: float) -> List[str]:
        """应用交互模式更新"""
        changes = []
        interaction_pattern = preferences.get("interaction_pattern", {})

        if not interaction_pattern:
            return changes

        # 根据时间模式调整风格
        if interaction_pattern.get("evening", 0) > 0.5:
            current_casualness = self.persona_model.get_expression_style().get("emotion", 0.5)
            new_casualness = min(0.6, current_casualness + 0.05 * update_factor)
            self.persona_model.update_expression_style(emotion=new_casualness)
            changes.append(f"pattern_effect: emotion adjusted for evening")

        return changes

    def _apply_trait_updates(self, preferences: Dict[str, Any],
                           update_factor: float) -> List[str]:
        """应用大五人格调整"""
        changes = []
        emotional_tendency = preferences.get("emotional_tendency", {})

        if not emotional_tendency:
            return changes

        # 根据情感倾向调整神经质性
        if "emotional" in emotional_tendency:
            current_neuroticism = self.persona_model.persona["traits"]["big_five"].get("neuroticism", 0.5)
            new_neuroticism = current_neuroticism + (emotional_tendency["emotional"] - 0.5) * 0.2 * update_factor
            self.persona_model.persona["traits"]["big_five"]["neuroticism"] = max(0.0, min(1.0, new_neuroticism))
            changes.append(f"trait_neuroticism: {current_neuroticism:.2f} -> {new_neuroticism:.2f}")

        return changes

    def _evaluate_distillation(self, original_persona: Dict[str, Any]) -> float:
        """评估蒸馏效果"""
        score = 0.0

        # 1. 人格稳定性评估 (30%)
        similarity = self.persona_model.calculate_similarity(self.persona_model)
        stability_score = 1.0 - abs(similarity - 0.5) * 2
        score += stability_score * 0.3

        # 2. 置信度评估 (25%)
        confidence = self._calculate_confidence()
        score += confidence * 0.25

        # 3. 一致性评估 (25%)
        consistency = self._check_consistency()
        score += consistency * 0.25

        # 4. 历史表现 (20%)
        historical_performance = self._get_historical_performance()
        score += historical_performance * 0.2

        return min(1.0, max(0.0, score))

    def _calculate_confidence(self) -> float:
        """计算置信度"""
        if self.evaluation_metrics["total_distillations"] == 0:
            return 0.5

        success_rate = self.evaluation_metrics["success_count"] / self.evaluation_metrics["total_distillations"]
        return success_rate * 0.7 + 0.3

    def _check_consistency(self) -> float:
        """检查人格一致性"""
        style = self.persona_model.get_expression_style()

        contradictions = 0

        if abs(style.get("formal", 0.5) - style.get("casual", 0.5)) > 0.8:
            contradictions += 1

        if abs(style.get("humorous", 0.5) - style.get("serious", 0.5)) > 0.8:
            contradictions += 1

        return 1.0 - (contradictions * 0.25)

    def _get_historical_performance(self) -> float:
        """获取历史表现"""
        if not self.evaluation_metrics["trend_analysis"]:
            return 0.5

        recent_scores = self.evaluation_metrics["trend_analysis"][-10:]
        if not recent_scores:
            return 0.5

        return sum(recent_scores) / len(recent_scores)

    def _create_snapshot(self, name: str = None) -> Dict[str, Any]:
        """创建人格快照"""
        snapshot = {
            "name": name or datetime.now().strftime("%Y%m%d_%H%M%S"),
            "timestamp": datetime.now().isoformat(),
            "persona": copy.deepcopy(self.persona_model.persona)
        }

        self.snapshots.append(snapshot)

        # 限制快照数量
        if len(self.snapshots) > self.config.max_history_snapshots:
            self.snapshots = self.snapshots[-self.config.max_history_snapshots:]

        logger.debug(f"已创建快照: {snapshot['name']}")
        return snapshot

    def rollback_to_snapshot(self, snapshot_name: str) -> bool:
        """回滚到指定快照"""
        self._ensure_history_loaded()

        for snapshot in reversed(self.snapshots):
            if snapshot["name"] == snapshot_name:
                self.persona_model.persona = copy.deepcopy(snapshot["persona"])
                logger.info(f"已回滚到快照: {snapshot_name}")
                return True

        logger.warning(f"未找到快照: {snapshot_name}")
        return False

    def _record_distillation(self, score: float):
        """记录蒸馏历史"""
        record = {
            "timestamp": datetime.now().isoformat(),
            "score": score,
            "strategy": self.config.strategy.value
        }

        self.history.append(record)
        self.evaluation_metrics["total_distillations"] += 1

        if score > 0.5:
            self.evaluation_metrics["success_count"] += 1

        self.evaluation_metrics["average_score"] = (
            (self.evaluation_metrics["average_score"] * (self.evaluation_metrics["total_distillations"] - 1) + score) /
            self.evaluation_metrics["total_distillations"]
        )

        self.evaluation_metrics["trend_analysis"].append(score)
        if len(self.evaluation_metrics["trend_analysis"]) > 100:
            self.evaluation_metrics["trend_analysis"] = self.evaluation_metrics["trend_analysis"][-100:]

        self._save_history()

    def auto_tune(self, feedback: float):
        """
        自动调参

        Args:
            feedback: 反馈评分 (0-1)，越高越好
        """
        self._ensure_history_loaded()

        # 根据反馈调整学习率
        if feedback < 0.3:
            self.config.learning_rate = max(0.01, self.config.learning_rate * 0.7)
            logger.info(f"降低学习率至: {self.config.learning_rate}")
        elif feedback > 0.7:
            self.config.learning_rate = min(0.5, self.config.learning_rate * 1.3)
            logger.info(f"提升学习率至: {self.config.learning_rate}")

        # 根据反馈调整策略
        if feedback < 0.4:
            self.config.strategy = DistillationStrategy.CONSERVATIVE
            logger.info("切换至保守策略")
        elif feedback > 0.8:
            self.config.strategy = DistillationStrategy.AGGRESSIVE
            logger.info("切换至激进策略")

    def get_evaluation_report(self) -> Dict[str, Any]:
        """获取评估报告"""
        self._ensure_history_loaded()

        return {
            "timestamp": datetime.now().isoformat(),
            "metrics": self.evaluation_metrics,
            "current_strategy": self.config.strategy.value,
            "current_config": {
                "learning_rate": self.config.learning_rate,
                "stability_weight": self.config.stability_weight,
                "adaptation_weight": self.config.adaptation_weight
            },
            "snapshot_count": len(self.snapshots),
            "history_count": len(self.history)
        }

    def _save_history(self):
        """保存历史记录"""
        try:
            history_file = Path("data/persona/distillation_history.json")
            history_file.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "snapshots": self.snapshots,
                "history": self.history,
                "metrics": self.evaluation_metrics,
                "saved_at": datetime.now().isoformat()
            }

            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error(f"保存历史失败: {e}")

    def _load_history(self):
        """加载历史记录"""
        try:
            history_file = Path("data/persona/distillation_history.json")
            if history_file.exists():
                with open(history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.snapshots = data.get("snapshots", [])
                    self.history = data.get("history", [])
                    self.evaluation_metrics = data.get("metrics", self.evaluation_metrics)
                logger.info(f"已加载历史记录，共 {len(self.history)} 条")
        except Exception as e:
            logger.debug(f"加载历史失败（可能是首次运行）: {e}")
