"""
云枢 PersonaModel - 五层人格模型（增强版）
参考 immortal-skill 的人格架构，增加更多高级功能
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
import copy

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

    def to_dict(self) -> Dict:
        """转换为字典"""
        return self.data.copy()


class PersonaSnapshot:
    """人格快照 - 保存某一时刻的人格状态"""

    def __init__(self, persona_model: 'PersonaModel', name: str = ""):
        self.name = name or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.timestamp = datetime.now().isoformat()
        self.persona_data = copy.deepcopy(persona_model.persona)
        self.expression_style = persona_model.get_expression_style()
        self.identity = persona_model.get_identity()

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "name": self.name,
            "timestamp": self.timestamp,
            "persona_data": self.persona_data,
            "expression_style": self.expression_style,
            "identity": self.identity
        }


class PersonaModel:
    """云枢人格模型 - 五层架构（增强版）

    增强功能：
    - 人格相似度计算
    - 人格冲突检测
    - 人格快照与回滚
    - 多人格切换支持
    - 人格变化趋势分析
    """

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
            "stability_score": 1.0,
        },
    }

    def __init__(self, persona_path: Optional[str] = None):
        self.persona_path = Path(persona_path) if persona_path else None
        self.persona: Dict[str, Any] = copy.deepcopy(self.DEFAULT_PERSONA)

        if self.persona_path and self.persona_path.exists():
            self._load_persona()

        self._init_layers()
        self._snapshots: List[PersonaSnapshot] = []

        logger.info("PersonaModel 初始化完成（增强版）")

    def _init_layers(self):
        """初始化层对象"""
        self.layers = {}
        for layer_name, layer_data in self.persona["layers"].items():
            self.layers[layer_name] = PersonaLayer(layer_name, layer_data)

    def _load_persona(self):
        """从文件加载人格"""
        try:
            with open(self.persona_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
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
        changes = []
        skipped = []
        clipped = []
        
        for key, value in kwargs.items():
            if key in layer2_data:
                if isinstance(layer2_data[key], (int, float)):
                    old_value = layer2_data[key]
                    
                    # 尝试转换为数值
                    try:
                        new_value = float(value)
                    except (ValueError, TypeError):
                        skipped.append(f"{key}: {repr(value)} (无法转换为数值)")
                        continue
                    
                    # 检查是否需要裁剪
                    if new_value < 0.0 or new_value > 1.0:
                        clipped_value = max(0.0, min(1.0, new_value))
                        clipped.append(f"{key}: {new_value:.4f} -> {clipped_value:.4f} (裁剪)")
                        layer2_data[key] = clipped_value
                    else:
                        layer2_data[key] = new_value
                    
                    change_amount = layer2_data[key] - old_value
                    changes.append({
                        "key": key,
                        "old": old_value,
                        "new": layer2_data[key],
                        "change": change_amount,
                        "percentage": (change_amount / (old_value if old_value != 0 else 1)) * 100
                    })
                else:
                    skipped.append(f"{key} (非数值类型)")
            else:
                skipped.append(f"{key} (不存在的参数)")
        
        # 更新层对象
        self.layers["layer2"] = PersonaLayer("layer2", layer2_data)
        
        # 记录详细日志
        if changes:
            log_lines = ["[人格更新] 表达风格参数变化:"]
            for change in changes:
                direction = "↑" if change["change"] > 0 else "↓" if change["change"] < 0 else "→"
                log_lines.append(
                    f"  {change['key']}: {change['old']:.4f} {direction} {change['new']:.4f} "
                    f"(Δ={change['change']:+.4f}, {change['percentage']:+.1f}%)"
                )
            if clipped:
                log_lines.append(f"  [裁剪警告] {', '.join(clipped)}")
            if skipped:
                log_lines.append(f"  [跳过] {', '.join(skipped)}")
            logger.info("\n".join(log_lines))
        else:
            logger.info("[人格更新] 无有效参数变化")

    def record_interaction(self):
        """记录交互次数"""
        self.persona["evolution"]["interactions"] += 1

    def take_snapshot(self, name: str = "") -> PersonaSnapshot:
        """拍摄人格快照"""
        snapshot = PersonaSnapshot(self, name)
        self._snapshots.append(snapshot)
        logger.info(f"人格快照已保存: {snapshot.name}")
        return snapshot

    def rollback_to_snapshot(self, snapshot_name: str) -> bool:
        """回滚到指定快照"""
        for snapshot in self._snapshots:
            if snapshot.name == snapshot_name:
                self.persona = copy.deepcopy(snapshot.persona_data)
                self._init_layers()
                logger.info(f"已回滚到快照: {snapshot_name}")
                return True
        logger.warning(f"未找到快照: {snapshot_name}")
        return False

    def get_snapshots(self) -> List[Dict]:
        """获取所有快照信息"""
        return [{"name": s.name, "timestamp": s.timestamp} for s in self._snapshots]

    def calculate_similarity(self, other: 'PersonaModel') -> float:
        """计算与另一个人格模型的相似度

        Args:
            other: 另一个人格模型

        Returns:
            相似度分数 (0-1)
        """
        if not isinstance(other, PersonaModel):
            return 0.0

        similarities = []

        style1 = self.get_expression_style()
        style2 = other.get_expression_style()

        for key in style1:
            if key in style2:
                diff = abs(style1[key] - style2[key])
                similarity = 1 - diff
                similarities.append(similarity)

        big_five_1 = self.persona.get("traits", {}).get("big_five", {})
        big_five_2 = other.persona.get("traits", {}).get("big_five", {})

        for key in big_five_1:
            if key in big_five_2:
                diff = abs(big_five_1[key] - big_five_2[key])
                similarity = 1 - diff
                similarities.append(similarity)

        if similarities:
            return sum(similarities) / len(similarities)
        return 0.0

    def detect_conflicts(self, other: 'PersonaModel') -> List[Dict[str, Any]]:
        """检测与另一个人格模型的冲突

        Args:
            other: 另一个人格模型

        Returns:
            冲突列表
        """
        conflicts = []

        style1 = self.get_expression_style()
        style2 = other.get_expression_style()

        for key in style1:
            if key in style2:
                diff = abs(style1[key] - style2[key])
                if diff > 0.5:
                    conflicts.append({
                        "type": "expression_style",
                        "dimension": key,
                        "value1": style1[key],
                        "value2": style2[key],
                        "severity": diff
                    })

        rules1 = set(self.get_hard_rules())
        rules2 = set(other.get_hard_rules())

        if rules1 != rules2:
            conflicts.append({
                "type": "hard_rules",
                "rules1": list(rules1 - rules2),
                "rules2": list(rules2 - rules1),
                "severity": 0.8
            })

        return conflicts

    def analyze_drift(self, baseline: Optional['PersonaModel'] = None) -> Dict[str, Any]:
        """分析人格漂移

        Args:
            baseline: 基准人格模型

        Returns:
            漂移分析报告
        """
        if baseline is None:
            baseline = self

        drift_report = {
            "timestamp": datetime.now().isoformat(),
            "interactions": self.persona["evolution"]["interactions"],
            "current_style": self.get_expression_style(),
            "baseline_style": baseline.get_expression_style(),
            "style_changes": {},
            "stability_score": self.persona["evolution"].get("stability_score", 1.0),
            "drift_history": self.persona["evolution"].get("persona_drift", [])
        }

        current_style = self.get_expression_style()
        baseline_style = baseline.get_expression_style()

        for key in current_style:
            if key in baseline_style:
                change = current_style[key] - baseline_style[key]
                if abs(change) > 0.05:
                    drift_report["style_changes"][key] = {
                        "baseline": baseline_style[key],
                        "current": current_style[key],
                        "change": change,
                        "direction": "increased" if change > 0 else "decreased"
                    }

        return drift_report

    def merge_personas(self, other: 'PersonaModel', weights: Dict[str, float] = None) -> bool:
        """合并另一个人格模型

        Args:
            other: 另一个人格模型
            weights: 合并权重 {"self": 0.5, "other": 0.5}

        Returns:
            是否成功
        """
        if weights is None:
            weights = {"self": 0.5, "other": 0.5}

        try:
            style1 = self.get_expression_style()
            style2 = other.get_expression_style()
            merged_style = {}

            for key in style1:
                if key in style2:
                    merged_style[key] = (
                        style1[key] * weights["self"] +
                        style2[key] * weights["other"]
                    )

            self.update_expression_style(**merged_style)

            big_five_1 = self.persona.get("traits", {}).get("big_five", {})
            big_five_2 = other.persona.get("traits", {}).get("big_five", {})

            for key in big_five_1:
                if key in big_five_2:
                    merged_value = (
                        big_five_1[key] * weights["self"] +
                        big_five_2[key] * weights["other"]
                    )
                    self.persona["traits"]["big_five"][key] = merged_value

            logger.info("人格模型合并完成")
            return True

        except Exception as e:
            logger.error(f"人格合并失败: {e}")
            return False

    def to_system_prompt(self) -> str:
        """生成系统提示词"""
        parts = []

        identity = self.get_identity()
        parts.append(f"# 你的身份\n{identity['identity']}\n{identity['background']}")
        parts.append(f"你的价值观: {', '.join(identity['values'])}")
        parts.append(f"你的目标: {identity['purpose']}")

        rules = self.get_hard_rules()
        parts.append("\n# 必须遵守的规则")
        for i, rule in enumerate(rules, 1):
            parts.append(f"{i}. {rule}")

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

        layer3 = self.layers.get("layer3")
        if layer3:
            parts.append("\n# 决策模式")
            parts.append(f"优先级: {', '.join(layer3.get('priorities', []))}")
            for rule in layer3.get("decision_rules", []):
                parts.append(f"- {rule}")

        layer4 = self.layers.get("layer4")
        if layer4:
            parts.append("\n# 人际行为")
            parts.append(f"社交风格: {layer4.get('social_style', 'supportive')}")
            parts.append(f"性格特点: {', '.join(layer4.get('interpersonal_traits', []))}")

        return "\n".join(parts)
