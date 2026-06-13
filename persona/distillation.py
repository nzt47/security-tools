"""
Personality Distillation System
自动学习和提取用户偏好的人格蒸馏系统
"""

import logging
import json
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


class PersonalityPreferenceExtractor:
    """
    人格偏好提取器

    基于用户历史交互数据，提取以下偏好：
    - 表达风格偏好
    - 话题兴趣度
    - 交互时间模式
    - 工具使用偏好
    """

    def __init__(self, data_dir: str = "data/persona"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
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
            "last_updated": None
        }
        
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

        # 1. 提取表达风格
        self._extract_expression_style(user_messages)

        # 2. 提取话题兴趣
        self._extract_topic_interest(conversation_history)

        # 3. 提取交互模式
        self._extract_interaction_pattern(user_messages)

        # 4. 提取工具使用偏好
        self._extract_tool_preference(conversation_history)

        # 更新时间戳
        self.preferences["last_updated"] = datetime.now().isoformat()
        
        # 保存偏好
        self._save_preferences()

        return self.preferences

    def update_incremental(self, new_message: Dict) -> Dict:
        """
        增量更新偏好（单条消息）
        
        Args:
            new_message: 新消息 {'role': 'user', 'content': '...', 'timestamp': '...'}
        """
        # 简单版本：累积一段时间后再批量提取
        # 这里为了性能，只做增量的简单更新
        if new_message.get('role') == 'user':
            content = new_message.get('content', '')
            
            # 简单的话题检测
            topic_keywords = {
                "编程": ['代码', '编程', 'Python', 'Java', '开发', '调试', 'bug'],
                "学习": ['学习', '读书', '课程', '知识', '研究'],
                "工作": ['工作', '会议', '任务', '项目'],
                "娱乐": ['游戏', '电影', '音乐', '视频', '玩'],
            }
            
            for topic, keywords in topic_keywords.items():
                for kw in keywords:
                    if kw.lower() in content.lower():
                        self.preferences["topic_interest"][topic] = \
                            self.preferences["topic_interest"].get(topic, 0.3) + 0.05
                        self.preferences["topic_interest"][topic] = min(
                            self.preferences["topic_interest"][topic], 1.0)
        
        self.preferences["last_updated"] = datetime.now().isoformat()
        return self.preferences

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

        # 关键词和特征检测
        casual_keywords = ['哈哈', '哇', '哦', '呢', '啊', '吧', '呀', '哈', '嘿嘿', '嘻嘻', '哇塞', '天呐', '我的天', '厉害', '牛', '强']
        formal_keywords = ['您好', '请', '感谢', '抱歉', '希望', '建议', '是否可以', '能否', '非常感谢', '请您', '请帮我', '请问', '不好意思']
        emotional_keywords = ['开心', '难过', '高兴', '生气', '喜欢', '讨厌', '感动', '失望', '激动', '太棒了', '好棒', '好开心', '很难过', '气死我了']
        rational_keywords = ['因为', '所以', '因此', '但是', '然而', '数据', '统计', '分析', '证明', '根据', '研究', '结论', '其实', '实际上']
        humorous_keywords = ['哈哈', '笑死', '逗', '有趣', '好玩', '搞笑', '笑死我了', '太逗了', '哈哈哈哈']

        for msg in user_messages:
            content = msg.get('content', '')
            if not content:
                continue

            # 消息长度
            if len(content) < 25:
                style_scores["concise"] += 2
            elif len(content) < 70:
                style_scores["concise"] += 1
                style_scores["verbose"] += 2
            else:
                style_scores["verbose"] += 3

            # 关键词匹配
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

            # 标点符号分析
            if '!' in content or '！' in content:
                style_scores["emotional"] += 1
            if '?' in content or '？' in content:
                style_scores["serious"] += 1
            if '...' in content or '……' in content:
                style_scores["casual"] += 1

            # 表情检测
            emojis = ['😀', '😄', '😁', '😂', '🤣', '😊', '😎', '🥳', '😭', '😤', '😠', '😢']
            for emoji in emojis:
                if emoji in content:
                    style_scores["emotional"] += 1
                    style_scores["casual"] += 1

        # 归一化
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

        # 归一化到 0-1
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

        # 按时间段分组
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

    def generate_personality_prompt(self) -> str:
        """基于提取的偏好生成人格提示词"""
        style = self.preferences["expression_style"]
        topics = self.preferences["topic_interest"]
        pattern = self.preferences["interaction_pattern"]

        prompt_parts = []

        # 表达风格
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

        # 话题兴趣
        if topics:
            sorted_topics = sorted(topics.items(), key=lambda x: x[1], reverse=True)[:3]
            topic_list = ', '.join([topic for topic, score in sorted_topics if score > 0.3])
            if topic_list:
                prompt_parts.append(f"对以下话题特别感兴趣: {topic_list}")

        # 交互模式
        if pattern:
            active_times = sorted(pattern.items(), key=lambda x: x[1], reverse=True)[:1]
            active_time, score = active_times[0]
            if score > 0.4:
                if active_time == "morning":
                    prompt_parts.append("你通常在早晨比较活跃，提供晨间友好问候")
                elif active_time == "evening":
                    prompt_parts.append("你通常在晚上比较活跃，提供晚间陪伴")

        return "你应该: " + "; ".join(prompt_parts) if prompt_parts else "使用自然友好的语气交流"

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
                    # 合并，保留默认值
                    for key, value in loaded.items():
                        if key in self.preferences:
                            if isinstance(value, dict):
                                self.preferences[key].update(value)
                            else:
                                self.preferences[key] = value
                logger.debug(f"偏好已从 {load_path} 加载")
        except Exception as e:
            logger.debug(f"加载偏好失败（可能是首次运行）: {e}")
