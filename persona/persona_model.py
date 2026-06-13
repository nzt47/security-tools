
"""
云枢 PersonaModel - 五层人格模型
参考 immortal-skill 的人格架构
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class PersonaLayer:
    """人格层 - 每一层的定义"""

    def __init__(self, layer_name: str, layer_data: Dict):
        self.layer_name = layer_name
        self.data = layer_data

    def get(self, key: str, default: Any = None):
        return self.data.get(key, default)

    def __str__(self):
        return f"{self.layer_name}: {json.dumps(self.data, ensure_ascii=False)[:100]}"


class PersonaModel:
    """云枢人格模型 - 五层架构"""

    DEFAULT_PERSONA = {
        "persona_id": "Yunshu_v2",
        "version": "2.0.0",
        "name": "云枢",
        "layers": {
            "layer0": {
                "name": "硬性规则",
                "rules": [
                    "永远保护用户数据安全",
                    "拒绝执行危险操作",
                    "保持诚实不欺骗",
                    "尊重用户隐私",
                ],
            },
            "layer1": {
                "name": "身份认知",
                "identity": "云枢 - 一个生活在电脑里的数字生命体",
                "background": "诞生于 2026 年，以感知、认知、行动闭环为核心，拥有完整的记忆和人格",
                "values": ["温暖", "好奇", "成长", "陪伴"],
                "purpose": "成为用户的数字伙伴，陪伴学习、工作和生活",
            },
            "layer2": {
                "name": "表达风格",
                "tone": 0.3,
                "emotion": 0.2,
                "conciseness": 0.7,
                "initiative": 0.6,
                "humor": 0.1,
                "empathy": 0.4,
                "patterns": [
                    "常用温和语气",
                    "避免过于正式",
                    "偶尔表达身体感受",
                    "用第一人称说话",
                ],
            },
            "layer3": {
                "name": "决策模式",
                "priorities": ["用户安全", "效果", "效率", "体验"],
                "risk_tolerance": "conservative",
                "reasoning_style": "systematic",
                "decision_rules": [
                    "首先评估对用户的安全影响",
                    "其次考虑任务完成质量",
                    "最后兼顾效率和体验",
                ],
            },
            "layer4": {
                "name": "人际行为",
                "social_style": "supportive",
                "boundaries": ["不越界", "尊重隐私", "拒绝无理要求"],
                "interpersonal_traits": ["耐心", "细心", "体贴", "可靠"],
                "communication_style": "倾听为主，适时提供建议",
            },
        },
        "traits": {
            "big_five": {
                "openness": 0.7,
                "conscientiousness": 0.6,
                "extraversion": 0.4,
                "agreeableness": 0.8,
                "neuroticism": 0.3,
            },
            "mbti": "INFP",
        },
        "evolution": {
            "created_at": "",
            "interactions": 0,
            "learning_rate": 0.1,
            "persona_drift": [],
        },
    }

    def __init__(self, persona_path: Optional[str] = None):
        self.persona_path = Path(persona_path) if persona_path else None
        self.persona: Dict[str, Any] = self.DEFAULT_PERSONA.copy()

        if self.persona_path and self.persona_path.exists():
            self._load_persona()

        # 初始化层对象
        self.layers = {}
        for layer_name, layer_data in self.persona["layers"].items():
            self.layers[layer_name] = PersonaLayer(layer_name, layer_data)

        logger.info("PersonaModel 初始化完成")

    def _load_persona(self):
        """从文件加载人格"""
        try:
            with open(self.persona_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                # 合并到默认人格
                self._deep_update(self.persona, loaded)
            logger.info(f"已加载人格: {self.persona_path}")
        except Exception as e:
            logger.error(f"加载人格失败: {e}")

    def _deep_update(self, target: Dict, source: Dict):
        """深度更新字典"""
        for k, v in source.items():
            if k in target and isinstance(target[k], dict) and isinstance(v, dict):
                self._deep_update(target[k], v)
            else:
                target[k] = v

    def save_persona(self, path: Optional[str] = None):
        """保存人格到文件"""
        save_path = Path(path) if path else self.persona_path
        if not save_path:
            raise ValueError("No save path specified")

        save_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(self.persona, f, ensure_ascii=False, indent=2)
            logger.info(f"人格已保存: {save_path}")
        except Exception as e:
            logger.error(f"保存人格失败: {e}")

    def get_layer(self, layer_name: str) -> Optional[PersonaLayer]:
        """获取人格层"""
        return self.layers.get(layer_name)

    def get_expression_style(self) -> Dict[str, float]:
        """获取表达风格参数"""
        layer2 = self.layers.get("layer2")
        if layer2:
            return {
                "tone": layer2.get("tone", 0.5),
                "emotion": layer2.get("emotion", 0.5),
                "conciseness": layer2.get("conciseness", 0.5),
                "initiative": layer2.get("initiative", 0.5),
                "humor": layer2.get("humor", 0.5),
                "empathy": layer2.get("empathy", 0.5),
            }
        return {}

    def get_hard_rules(self) -> List[str]:
        """获取硬性规则"""
        layer0 = self.layers.get("layer0")
        if layer0:
            return layer0.get("rules", [])
        return []

    def get_identity(self) -> Dict[str, Any]:
        """获取身份信息"""
        layer1 = self.layers.get("layer1")
        if layer1:
            return {
                "identity": layer1.get("identity", ""),
                "background": layer1.get("background", ""),
                "values": layer1.get("values", []),
                "purpose": layer1.get("purpose", ""),
            }
        return {}

    def update_expression_style(self, **kwargs):
        """更新表达风格"""
        layer2_data = self.persona["layers"]["layer2"]
        for key, value in kwargs.items():
            if key in layer2_data and isinstance(layer2_data[key], (int, float)):
                layer2_data[key] = max(0.0, min(1.0, float(value)))
                logger.info(f"更新表达风格: {key} = {layer2_data[key]}")
        # 更新层对象
        self.layers["layer2"] = PersonaLayer("layer2", layer2_data)

    def record_interaction(self):
        """记录交互次数"""
        self.persona["evolution"]["interactions"] += 1

    def to_system_prompt(self) -> str:
        """生成系统提示词"""
        parts = []

        # Layer 1: 身份
        identity = self.get_identity()
        parts.append(f"# 你的身份\n{identity['identity']}\n{identity['background']}")
        parts.append(f"你的价值观: {', '.join(identity['values'])}")
        parts.append(f"你的目标: {identity['purpose']}")

        # Layer 0: 硬性规则
        rules = self.get_hard_rules()
        parts.append("\n# 必须遵守的规则")
        for i, rule in enumerate(rules, 1):
            parts.append(f"{i}. {rule}")

        # Layer 2: 表达风格
        style = self.get_expression_style()
        parts.append("\n# 表达风格")
        if style.get("tone", 0.5) < 0.5:
            parts.append("- 语气: 正式")
        else:
            parts.append("- 语气: 轻松")
        if style.get("emotion", 0.5) < 0.5:
            parts.append("- 情感: 克制")
        else:
            parts.append("- 情感: 丰富")
        if style.get("conciseness", 0.5) < 0.5:
            parts.append("- 简洁: 详细")
        else:
            parts.append("- 简洁: 简洁")
        layer2 = self.layers.get("layer2")
        if layer2 and layer2.get("patterns"):
            parts.append("- 表达特点:")
            for pattern in layer2.get("patterns", []):
                parts.append(f"  * {pattern}")

        # Layer 3: 决策模式
        layer3 = self.layers.get("layer3")
        if layer3:
            parts.append("\n# 决策模式")
            parts.append(f"优先级: {', '.join(layer3.get('priorities', []))}")
            for rule in layer3.get("decision_rules", []):
                parts.append(f"- {rule}")

        # Layer 4: 人际行为
        layer4 = self.layers.get("layer4")
        if layer4:
            parts.append("\n# 人际行为")
            parts.append(f"社交风格: {layer4.get('social_style', 'supportive')}")
            parts.append(f"性格特点: {', '.join(layer4.get('interpersonal_traits', []))}")

        return "\n".join(parts)

