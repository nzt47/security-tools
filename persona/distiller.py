"""
Persona Distiller - 人格蒸馏器
增强版人格提取和融合系统
"""

import logging
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import copy

logger = logging.getLogger(__name__)


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
    """人格蒸馏器"""

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
        """应用话题兴趣更新（扩展到其他人格特征）"""
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
        # 基于历史蒸馏次数和成功率
        if self.evaluation_metrics["total_distillations"] == 0:
            return 0.5
        
        success_rate = self.evaluation_metrics["success_count"] / self.evaluation_metrics["total_distillations"]
        return success_rate * 0.7 + 0.3

    def _check_consistency(self) -> float:
        """检查人格一致性"""
        style = self.persona_model.get_expression_style()
        
        # 检查表达风格是否合理
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
        # 确保历史数据已加载
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
        # 确保历史数据已加载
        self._ensure_history_loaded()
        
        # 根据反馈调整学习率
        if feedback < 0.3:
            # 降低学习率
            self.config.learning_rate = max(0.01, self.config.learning_rate * 0.7)
            logger.info(f"降低学习率至: {self.config.learning_rate}")
        elif feedback > 0.7:
            # 提升学习率
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
        # 确保历史数据已加载
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
