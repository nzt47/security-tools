"""离线批量进化器 — P1.4

能力:
    1. 批量进化: 对多个技能进行多轮参数迭代
    2. 变异算法: 策略模式,支持参数微调/组合/突变/重置
    3. 帕累托前沿: 多目标优化(成功率 + 延迟 + 满意度)
    4. cron触发: 定时批量进化(骨架阶段提供手动接口)

核心流程:
    1. 选择候选技能 (usage_count >= 阈值 且 success_rate < 目标)
    2. 生成变异体 (按策略生成多组参数)
    3. 评估变异体 (多目标加权评分)
    4. 帕累托前沿筛选 (非支配排序)
    5. 提交最优变异体 (版本升级 + 持久化)

设计原则:
    - 安全第一: 每轮进化前快照,失败可回滚 (复用 enhancer.bump_version)
    - 边界显性化: 变异失败/评估异常 → 跳过该候选,不中断批量
    - 可观测: 结构化日志 + emit_metric 埋点
    - 与 SkillEnhancer 协作: 复用版本管理 + 参数优化能力
    - 骨架阶段: 变异用简化逻辑,帕累托用支配关系,cron用接口占位
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from .enhancer import SkillEnhancer, VersionBump
from .models import Skill, SkillMetrics
from .observability import logger, emit_metric, traced_action
from .store import SkillStore
from .exceptions import SkillNotFoundError


# ════════════════════════════════════════════════════════════
#  进化策略
# ════════════════════════════════════════════════════════════

class EvolutionStrategy(str, Enum):
    """变异策略枚举"""
    FINE_TUNE = "fine_tune"   # 参数微调: 对现有参数做小幅扰动
    COMBINE = "combine"       # 组合: 融合多个高分技能的参数
    MUTATE = "mutate"         # 突变: 随机生成新参数组合
    RESET = "reset"           # 重置: 回退到默认参数


# ════════════════════════════════════════════════════════════
#  数据结构
# ════════════════════════════════════════════════════════════

@dataclass
class Variant:
    """单个变异体 — 一组待评估的参数组合"""
    skill_id: str
    strategy: EvolutionStrategy
    params: Dict[str, Any]
    parent_version: str
    # 评估后填充
    score: Optional[float] = None          # 综合评分 (越高越好)
    objectives: Optional[Dict[str, float]] = None  # 多目标值 {success_rate, neg_latency, satisfaction}
    metrics: Optional[SkillMetrics] = None  # 采样指标 (骨架阶段用历史指标占位)


@dataclass
class EvolutionResult:
    """单轮进化结果"""
    skill_id: str
    strategy: Optional[EvolutionStrategy] = None
    old_version: str = ""
    new_version: str = ""
    improvement: float = 0.0   # 评分提升幅度 (正数=改善)
    committed: bool = False    # 是否已持久化
    error: Optional[str] = None
    skipped: bool = False      # 是否跳过 (无候选/指标不足)


@dataclass
class ParetoFront:
    """帕累托前沿筛选结果"""
    front: List[Variant]        # 非支配变异体集合
    dominated_count: int        # 被支配的变异体数量
    total_count: int            # 总变异体数量


@dataclass
class BatchEvolutionReport:
    """批量进化报告"""
    started_at: str
    finished_at: str = ""
    total_skills: int = 0
    evolved_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    results: List[EvolutionResult] = field(default_factory=list)
    avg_improvement: float = 0.0


# ════════════════════════════════════════════════════════════
#  离线进化器
# ════════════════════════════════════════════════════════════

# 评估目标维度 (用于帕累托支配判断)
_OBJECTIVE_KEYS = ("success_rate", "neg_latency", "satisfaction")

# 默认变异策略权重
_DEFAULT_STRATEGY_WEIGHTS: Dict[EvolutionStrategy, float] = {
    EvolutionStrategy.FINE_TUNE: 0.5,
    EvolutionStrategy.COMBINE: 0.2,
    EvolutionStrategy.MUTATE: 0.2,
    EvolutionStrategy.RESET: 0.1,
}


class OfflineEvolver:
    """离线批量进化器

    核心流程 (evolve_once):
        选择候选 → 生成变异体 → 评估 → 帕累托筛选 → 提交最优

    使用示例:
        evolver = OfflineEvolver(store, enhancer)
        result = evolver.evolve_once("my-skill")
        report = evolver.evolve_batch(max_rounds=3)
    """

    def __init__(self, store: SkillStore, enhancer: SkillEnhancer, *,
                 min_usage: int = 10,
                 target_success_rate: float = 0.95,
                 max_variants_per_skill: int = 5,
                 improvement_threshold: float = 0.05,
                 random_seed: Optional[int] = None):
        """
        Args:
            store: 技能存储
            enhancer: 技能增强器 (复用版本管理+参数优化)
            min_usage: 候选技能最小使用次数阈值
            target_success_rate: 目标成功率 (低于此值才纳入进化)
            max_variants_per_skill: 每个技能每轮生成的最大变异体数
            improvement_threshold: 评分提升阈值 (低于此值不提交)
            random_seed: 随机种子 (可复现)
        """
        self._store = store
        self._enhancer = enhancer
        self.min_usage = min_usage
        self.target_success_rate = target_success_rate
        self.max_variants_per_skill = max_variants_per_skill
        self.improvement_threshold = improvement_threshold
        self._rng = random.Random(random_seed)

    # ════════════════════════════════════════════════════════════
    #  公共接口
    # ════════════════════════════════════════════════════════════

    def evolve_once(self, skill_id: str, *,
                    strategies: Optional[List[EvolutionStrategy]] = None) -> EvolutionResult:
        """对单个技能执行一轮进化

        流程:
            1. 加载技能 + 校验候选资格
            2. 按策略生成变异体
            3. 评估每个变异体 (多目标)
            4. 帕累托前沿筛选
            5. 提交最优变异体 (若提升超过阈值)

        Args:
            skill_id: 技能ID
            strategies: 使用的变异策略列表 (None=按默认权重采样)

        Returns:
            EvolutionResult — 包含提升幅度、是否提交、错误信息
        """
        t_total = time.time()
        with traced_action("evolve_once", skill_id=skill_id):
            # 步骤1: 加载技能
            t0 = time.time()
            try:
                skill = self._store.get(skill_id)
            except SkillNotFoundError:
                logger.warning(json.dumps({
                    "module_name": "offline_evolver",
                    "action": "evolve_once.skill_not_found",
                    "skill_id": skill_id,
                    "duration_ms": round((time.time() - t0) * 1000, 2),
                }, ensure_ascii=False))
                return EvolutionResult(
                    skill_id=skill_id, skipped=True,
                    error=f"技能不存在: {skill_id}",
                )

            # 步骤2: 候选资格校验
            if not self._is_candidate(skill):
                logger.info(json.dumps({
                    "module_name": "offline_evolver",
                    "action": "evolve_once.not_candidate",
                    "skill_id": skill_id,
                    "usage_count": skill.metrics.usage_count,
                    "success_rate": round(skill.metrics.success_rate, 4),
                    "load_ms": round((time.time() - t0) * 1000, 2),
                }, ensure_ascii=False))
                return EvolutionResult(
                    skill_id=skill_id, skipped=True,
                    error=f"不满足候选条件 (usage={skill.metrics.usage_count}, "
                          f"success_rate={skill.metrics.success_rate:.2f})",
                )

            # 步骤3: 评估基线
            t_eval_base = time.time()
            old_score = self._evaluate_skill(skill)
            old_version = skill.version
            logger.info(json.dumps({
                "module_name": "offline_evolver",
                "action": "evolve_once.baseline",
                "skill_id": skill_id,
                "old_score": old_score,
                "old_version": old_version,
                "baseline_eval_ms": round((time.time() - t_eval_base) * 1000, 2),
            }, ensure_ascii=False))

            # 步骤4: 生成变异体
            t_mutate = time.time()
            variants = self._mutate(skill, strategies or self._sample_strategies())
            mutate_ms = (time.time() - t_mutate) * 1000
            if not variants:
                logger.warning(json.dumps({
                    "module_name": "offline_evolver",
                    "action": "evolve_once.no_variants",
                    "skill_id": skill_id,
                    "mutate_ms": round(mutate_ms, 2),
                }, ensure_ascii=False))
                return EvolutionResult(
                    skill_id=skill_id, skipped=True,
                    error="未生成任何变异体",
                )

            # 步骤5: 评估变异体
            t_eval = time.time()
            for v in variants:
                v.score = self._evaluate(v)
                v.objectives = self._compute_objectives(v)
            eval_ms = (time.time() - t_eval) * 1000

            # 步骤6: 帕累托筛选 (性能热点)
            pareto = self._pareto_filter(variants)
            best = self._pick_best(pareto.front)

            logger.info(json.dumps({
                "module_name": "offline_evolver",
                "action": "evolve_once.pareto_done",
                "skill_id": skill_id,
                "variants_count": len(variants),
                "pareto_front_size": len(pareto.front),
                "dominated_count": pareto.dominated_count,
                "mutate_ms": round(mutate_ms, 2),
                "eval_ms": round(eval_ms, 2),
            }, ensure_ascii=False))

            if best is None or best.score is None:
                return EvolutionResult(
                    skill_id=skill_id, skipped=True,
                    error="无有效变异体通过评估",
                )

            improvement = best.score - old_score
            result = EvolutionResult(
                skill_id=skill_id,
                strategy=best.strategy,
                old_version=old_version,
                improvement=round(improvement, 4),
            )

            # 步骤7: 提交判定
            t_commit = time.time()
            if improvement >= self.improvement_threshold:
                committed = self._commit(best)
                result.new_version = committed.new_version if committed else ""
                result.committed = committed is not None
            else:
                result.committed = False
                logger.info(json.dumps({
                    "module_name": "offline_evolver",
                    "action": "evolve_once.skip_commit",
                    "skill_id": skill_id,
                    "improvement": round(improvement, 4),
                    "threshold": self.improvement_threshold,
                    "best_score": best.score,
                    "old_score": old_score,
                }, ensure_ascii=False))
            commit_ms = (time.time() - t_commit) * 1000

            # 汇总日志 + 性能埋点
            total_ms = (time.time() - t_total) * 1000
            logger.info(json.dumps({
                "module_name": "offline_evolver",
                "action": "evolve_once.done",
                "skill_id": skill_id,
                "strategy": best.strategy.value,
                "improvement": round(improvement, 4),
                "committed": result.committed,
                "total_ms": round(total_ms, 2),
                "breakdown_ms": {
                    "mutate": round(mutate_ms, 2),
                    "eval": round(eval_ms, 2),
                    "commit": round(commit_ms, 2),
                },
            }, ensure_ascii=False))

            emit_metric("yunshu_skill_evolution_total",
                        value=1, kind="counter",
                        labels={"skill_id": skill_id,
                                "committed": str(result.committed).lower()})
            emit_metric("yunshu_skill_evolve_latency_ms",
                        value=total_ms, kind="histogram",
                        labels={"skill_id": skill_id})
            emit_metric("yunshu_skill_pareto_variants_count",
                        value=len(variants), kind="gauge",
                        labels={"skill_id": skill_id})
            return result

    def evolve_batch(self, skill_ids: Optional[List[str]] = None, *,
                     max_rounds: int = 1) -> BatchEvolutionReport:
        """批量进化多个技能

        Args:
            skill_ids: 待进化技能列表 (None=自动选择候选)
            max_rounds: 最大进化轮次 (每轮基于上一轮结果)

        Returns:
            BatchEvolutionReport — 批量进化报告
        """
        started_at = datetime.utcnow().isoformat()
        report = BatchEvolutionReport(started_at=started_at)

        with traced_action("evolve_batch", max_rounds=max_rounds):
            candidates = skill_ids or [s.id for s in self._select_candidates()]
            report.total_skills = len(candidates)

            for round_idx in range(max_rounds):
                logger.info(json.dumps({
                    "module_name": "offline_evolver",
                    "action": "evolve_batch.round",
                    "round": round_idx + 1,
                    "total": max_rounds,
                    "candidates": len(candidates),
                }, ensure_ascii=False))

                for skill_id in candidates:
                    result = self.evolve_once(skill_id)
                    report.results.append(result)
                    if result.skipped:
                        report.skipped_count += 1
                    elif result.error:
                        report.failed_count += 1
                    elif result.committed:
                        report.evolved_count += 1

            # 汇总
            committed_results = [r for r in report.results if r.committed]
            if committed_results:
                report.avg_improvement = round(
                    sum(r.improvement for r in committed_results) / len(committed_results), 4
                )
            report.finished_at = datetime.utcnow().isoformat()

            logger.info(json.dumps({
                "module_name": "offline_evolver",
                "action": "evolve_batch.done",
                "total_skills": report.total_skills,
                "evolved": report.evolved_count,
                "skipped": report.skipped_count,
                "failed": report.failed_count,
                "avg_improvement": report.avg_improvement,
            }, ensure_ascii=False))

        return report

    def schedule(self, cron_expr: str = "0 2 * * *",
                 *, skill_ids: Optional[List[str]] = None,
                 max_rounds: int = 1) -> Dict[str, Any]:
        """注册 cron 定时任务 (骨架占位)

        骨架阶段不接入真正的 scheduler,仅返回占位响应。
        后续可接入 APScheduler / 自定义 cron 框架。

        Args:
            cron_expr: cron 表达式 (默认每天凌晨2点)
            skill_ids: 待进化技能列表 (None=自动选择)
            max_rounds: 最大进化轮次

        Returns:
            {"status": "scheduled", "cron": ..., "next_run": ..., "note": ...}
        """
        logger.warning(json.dumps({
            "module_name": "offline_evolver",
            "action": "schedule.placeholder",
            "cron_expr": cron_expr,
            "note": "骨架阶段未接入真正的 scheduler,需手动调用 evolve_batch()",
        }, ensure_ascii=False))
        return {
            "status": "scheduled_placeholder",
            "cron": cron_expr,
            "skill_ids": skill_ids,
            "max_rounds": max_rounds,
            "next_run": "manual",
            "note": "骨架阶段: 请手动调用 evolve_batch() 触发,后续接入真正的 cron",
        }

    # ════════════════════════════════════════════════════════════
    #  内部方法
    # ════════════════════════════════════════════════════════════

    def _is_candidate(self, skill: Skill) -> bool:
        """校验技能是否满足进化候选条件"""
        m = skill.metrics
        if m.usage_count < self.min_usage:
            return False
        if m.success_rate >= self.target_success_rate:
            return False
        return True

    def _select_candidates(self) -> List[Skill]:
        """从技能库中选择需要进化的候选技能"""
        all_skills = self._store.list_all()
        candidates = [s for s in all_skills if self._is_candidate(s)]
        logger.info(json.dumps({
            "module_name": "offline_evolver",
            "action": "select_candidates",
            "total": len(all_skills),
            "candidates": len(candidates),
        }, ensure_ascii=False))
        return candidates

    def _sample_strategies(self) -> List[EvolutionStrategy]:
        """按默认权重采样变异策略"""
        strategies = list(_DEFAULT_STRATEGY_WEIGHTS.keys())
        weights = list(_DEFAULT_STRATEGY_WEIGHTS.values())
        # 采样 max_variants 个策略 (带权重)
        return self._rng.choices(strategies, weights=weights,
                                  k=self.max_variants_per_skill)

    def _mutate(self, skill: Skill,
                strategies: List[EvolutionStrategy]) -> List[Variant]:
        """根据策略列表生成变异体

        策略模式: 每个策略对应一个变异器,独立生成参数组合。
        骨架阶段: 变异逻辑简化,仅做参数扰动占位。
        """
        variants: List[Variant] = []
        base_params = dict(skill.default_params)
        strategy_counts: Dict[str, int] = {}

        for strategy in strategies:
            try:
                t_strat = time.time()
                new_params = self._apply_strategy(skill, strategy, base_params)
                strat_ms = (time.time() - t_strat) * 1000
                if new_params is None:
                    logger.debug(json.dumps({
                        "module_name": "offline_evolver",
                        "action": "mutate.no_params",
                        "skill_id": skill.id,
                        "strategy": strategy.value,
                        "strat_ms": round(strat_ms, 2),
                    }, ensure_ascii=False))
                    continue
                variants.append(Variant(
                    skill_id=skill.id,
                    strategy=strategy,
                    params=new_params,
                    parent_version=skill.version,
                    metrics=skill.metrics,  # 骨架阶段用历史指标占位
                ))
                strategy_counts[strategy.value] = strategy_counts.get(strategy.value, 0) + 1
            except Exception as e:
                logger.warning(json.dumps({
                    "module_name": "offline_evolver",
                    "action": "mutate.skip",
                    "skill_id": skill.id,
                    "strategy": strategy.value,
                    "error": str(e),
                }, ensure_ascii=False))

        logger.info(json.dumps({
            "module_name": "offline_evolver",
            "action": "mutate.done",
            "skill_id": skill.id,
            "base_params_count": len(base_params),
            "strategies_requested": len(strategies),
            "variants_generated": len(variants),
            "strategy_counts": strategy_counts,
        }, ensure_ascii=False))

        return variants

    def _apply_strategy(self, skill: Skill, strategy: EvolutionStrategy,
                        base_params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """执行单个变异策略 (骨架实现)

        骨架阶段:
            - FINE_TUNE: 对数值参数做 ±10% 扰动
            - COMBINE: 融合 avoid_params 之外的最高分参数 (骨架用 base 占位)
            - MUTATE: 随机生成新参数值 (骨架用 base ±50%)
            - RESET: 返回空参数 (使用默认)
        """
        if strategy == EvolutionStrategy.RESET:
            return {}  # 空参数,触发默认行为

        if not base_params:
            return None  # 无参数可变异

        new_params = dict(base_params)
        for key, val in new_params.items():
            if not isinstance(val, (int, float)):
                continue
            if strategy == EvolutionStrategy.FINE_TUNE:
                new_params[key] = round(val * (1 + self._rng.uniform(-0.1, 0.1)), 4)
            elif strategy == EvolutionStrategy.MUTATE:
                new_params[key] = round(val * (1 + self._rng.uniform(-0.5, 0.5)), 4)
            elif strategy == EvolutionStrategy.COMBINE:
                # 骨架: 从 param_stats 取最高成功率的参数 (若有)
                best_params = self._best_params_from_history(skill)
                if best_params and key in best_params:
                    new_params[key] = best_params[key]
        return new_params

    def _best_params_from_history(self, skill: Skill) -> Optional[Dict[str, Any]]:
        """从 param_stats 历史中取成功率最高的参数组合"""
        if not skill.metrics.param_stats:
            return None
        best_hash = None
        best_rate = -1.0
        for ph, stats in skill.metrics.param_stats.items():
            total = stats.get("success", 0) + stats.get("failure", 0)
            if total < 3:
                continue
            rate = stats.get("success", 0) / total
            if rate > best_rate:
                best_rate = rate
                best_hash = ph
        if best_hash is None:
            return None
        return skill.metrics.param_stats[best_hash].get("params")

    def _compute_objectives(self, variant: Variant) -> Dict[str, float]:
        """计算多目标值 (用于帕累托支配判断)

        三个目标 (均为越大越好):
            - success_rate: 成功率
            - neg_latency: 负延迟 (取负,使延迟越小越好)
            - satisfaction: 满意度 (骨架用 success_rate 占位)
        """
        m = variant.metrics
        if m is None:
            return {"success_rate": 0.0, "neg_latency": 0.0, "satisfaction": 0.0}
        return {
            "success_rate": m.success_rate,
            "neg_latency": -m.avg_latency_ms,
            "satisfaction": m.success_rate,  # 骨架阶段: 用成功率占位
        }

    def _evaluate(self, variant: Variant) -> float:
        """评估变异体综合评分 (多目标加权)

        骨架阶段: 简单加权求和
            score = 0.5 * success_rate + 0.3 * latency_norm + 0.2 * satisfaction
        """
        obj = self._compute_objectives(variant)
        # 延迟归一化 (假设 5000ms 为基准)
        latency_norm = max(0.0, min(1.0, 1.0 + obj["neg_latency"] / 5000.0))
        score = (
            0.5 * obj["success_rate"]
            + 0.3 * latency_norm
            + 0.2 * obj["satisfaction"]
        )
        return round(score, 4)

    def _evaluate_skill(self, skill: Skill) -> float:
        """评估原始技能的评分 (基线)"""
        v = Variant(
            skill_id=skill.id,
            strategy=EvolutionStrategy.FINE_TUNE,  # 占位
            params=dict(skill.default_params),
            parent_version=skill.version,
            metrics=skill.metrics,
        )
        return self._evaluate(v)

    def _pareto_filter(self, variants: List[Variant]) -> ParetoFront:
        """帕累托前沿筛选 (非支配排序)

        变异体 A 支配 B 当且仅当:
            A 在所有目标上 >= B,且至少一个目标 > B

        性能: O(n²) 支配判断,n=变异体数量。日志记录判断次数和耗时,
        便于定位瓶颈(当 n > 100 时应考虑改用快速非支配排序)。
        """
        n = len(variants)
        if n == 0:
            return ParetoFront(front=[], dominated_count=0, total_count=0)

        t_start = time.time()
        domination_checks = 0  # 支配判断调用次数(性能指标)
        early_exit_count = 0    # 提前break的次数

        front: List[Variant] = []
        dominated_count = 0

        for i, v_i in enumerate(variants):
            if v_i.objectives is None:
                v_i.objectives = self._compute_objectives(v_i)
            is_dominated = False
            for j, v_j in enumerate(variants):
                if i == j:
                    continue
                if v_j.objectives is None:
                    v_j.objectives = self._compute_objectives(v_j)
                domination_checks += 1
                if self._dominates(v_j.objectives, v_i.objectives):
                    is_dominated = True
                    early_exit_count += 1
                    break
            if not is_dominated:
                front.append(v_i)
            else:
                dominated_count += 1

        elapsed_ms = (time.time() - t_start) * 1000
        non_dominated_ratio = len(front) / n if n > 0 else 0.0

        logger.info(json.dumps({
            "module_name": "offline_evolver",
            "action": "pareto_filter.detail",
            "variants_count": n,
            "domination_checks": domination_checks,
            "early_exit_count": early_exit_count,
            "theoretical_max_checks": n * (n - 1),
            "front_size": len(front),
            "dominated_count": dominated_count,
            "non_dominated_ratio": round(non_dominated_ratio, 4),
            "elapsed_ms": round(elapsed_ms, 2),
            "avg_check_us": round(elapsed_ms * 1000 / domination_checks, 2) if domination_checks > 0 else 0,
        }, ensure_ascii=False))

        emit_metric("yunshu_skill_pareto_filter_latency_ms",
                    value=elapsed_ms, kind="histogram")
        emit_metric("yunshu_skill_pareto_domination_checks",
                    value=domination_checks, kind="counter")
        emit_metric("yunshu_skill_pareto_front_ratio",
                    value=non_dominated_ratio, kind="gauge")

        return ParetoFront(
            front=front,
            dominated_count=dominated_count,
            total_count=n,
        )

    @staticmethod
    def _dominates(a: Dict[str, float], b: Dict[str, float]) -> bool:
        """判断目标向量 a 是否支配 b (a >= b 所有维度 且 a > b 至少一维)"""
        at_least_one_greater = False
        for key in _OBJECTIVE_KEYS:
            if a.get(key, 0.0) < b.get(key, 0.0):
                return False
            if a.get(key, 0.0) > b.get(key, 0.0):
                at_least_one_greater = True
        return at_least_one_greater

    def _pick_best(self, front: List[Variant]) -> Optional[Variant]:
        """从帕累托前沿中挑选综合评分最高的变异体"""
        if not front:
            return None
        scored = [v for v in front if v.score is not None]
        if not scored:
            return None
        return max(scored, key=lambda v: v.score or 0.0)

    def _commit(self, variant: Variant) -> Optional[VersionBump]:
        """提交最优变异体 (版本升级 + 持久化)

        复用 SkillEnhancer.bump_version 做 patch 版本升级,
        并更新 default_params。
        """
        try:
            skill = self._store.get(variant.skill_id)
            skill.default_params = dict(variant.params)
            bump = self._enhancer.bump_version(
                variant.skill_id, "patch",
                changelog=f"离线进化: strategy={variant.strategy.value}, "
                          f"improvement={variant.score}",
            )
            logger.info(json.dumps({
                "module_name": "offline_evolver",
                "action": "commit.ok",
                "skill_id": variant.skill_id,
                "strategy": variant.strategy.value,
                "old_version": variant.parent_version,
                "new_version": bump.new_version,
                "score": variant.score,
            }, ensure_ascii=False))
            return bump
        except Exception as e:
            logger.error(json.dumps({
                "module_name": "offline_evolver",
                "action": "commit.failed",
                "skill_id": variant.skill_id,
                "error": str(e),
            }, ensure_ascii=False))
            return None
