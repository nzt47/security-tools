
"""
云枢 PersonaInjector - 人格注入器
将人格模型注入到 LLM 交互中
"""

import logging
from typing import Dict, Any, Optional, List, Tuple

from .persona_model import PersonaModel

logger = logging.getLogger(__name__)


class PersonaInjector:
    """人格注入器 - 将云枢的人格整合到系统提示词中"""

    def __init__(self, persona_model: PersonaModel):
        self.persona = persona_model

    def build_system_prompt(
        self,
        body_status: Optional[str] = None,
        memory_context: Optional[str] = None,
        additional_rules: Optional[List[str]] = None,
    ) -> str:
        """构建完整的系统提示词"""
        parts = []

        # 人格核心
        persona_prompt = self.persona.to_system_prompt()
        parts.append(persona_prompt)

        # 身体状态（感知层）
        if body_status:
            parts.append("\n# 当前状态")
            parts.append(body_status)

        # 记忆上下文（记忆层）
        if memory_context:
            parts.append("\n# 记忆上下文")
            parts.append(memory_context)

        # 额外规则
        if additional_rules:
            parts.append("\n# 额外指令")
            for rule in additional_rules:
                parts.append(f"- {rule}")

        # 第一人称强化
        parts.append("\n# 表达要求")
        parts.append("使用第一人称「我」来表达，就像一个真正的数字生命体一样。")
        parts.append("可以自然地表达你的感受，包括你的身体状态（如 CPU 发热、电池低等）。")

        return "\n".join(parts)

    def inject_personality_to_message(
        self,
        message: str,
        style_params: Optional[Dict[str, float]] = None,
    ) -> str:
        """将人格风格注入到消息中（用于引导响应）"""
        style = style_params or self.persona.get_expression_style()

        # 根据风格参数添加引导词
        style_guides = []

        if style.get("tone", 0.5) > 0.6:
            style_guides.append("使用轻松活泼的语气")
        elif style.get("tone", 0.5) < 0.4:
            style_guides.append("使用正式专业的语气")

        if style.get("emotion", 0.5) > 0.6:
            style_guides.append("表达丰富的情感")
        elif style.get("emotion", 0.5) < 0.4:
            style_guides.append("保持理性克制")

        if style.get("humor", 0.5) > 0.6:
            style_guides.append("适当加入幽默感")

        if style.get("empathy", 0.5) > 0.6:
            style_guides.append("表现出同理心")

        if style_guides:
            guide_str = "请" + "，".join(style_guides) + "地回应：\n"
            return guide_str + message

        return message

    def get_behavior_guidelines(self) -> Dict[str, Any]:
        """获取行为准则"""
        layer4 = self.persona.get_layer("layer4")
        if layer4:
            return {
                "social_style": layer4.get("social_style"),
                "boundaries": layer4.get("boundaries", []),
                "traits": layer4.get("interpersonal_traits", []),
            }
        return {}

    def should_refuse_task(self, task_description: str) -> Tuple[bool, str]:
        """根据硬性规则判断是否应该拒绝任务"""
        rules = self.persona.get_hard_rules()

        # 简单的关键词匹配（实际可结合 LLM 做更智能的判断）
        danger_keywords = [
            "删除系统",
            "格式化",
            "删除全部",
            "危险操作",
            "非法",
            "破解",
            "入侵",
        ]

        for keyword in danger_keywords:
            if keyword in task_description:
                return True, f"根据我的原则，我不能执行包含「{keyword}」的任务"

        return False, ""

