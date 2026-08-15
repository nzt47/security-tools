"""父代选择策略（Parent Selection）— 任务 EVO-T3

【任务定位】
    落地设计文档"种群进化 + 选择压力"主张：进化循环不再固定以"最新版本"
    为父代变异，而是按策略从历史版本谱系中选择父代，引入选择压力；
    并对"子代过多"的父代施加惩罚，避免谱系坍缩到单一分支。

【策略枚举】
    best               贪心：选择评分最高的历史版本
    latest             最新：选择最新提交的版本（贴近原"当前版本"语义）
    random             随机：均匀采样
    score_prop         评分概率：w = sigmoid(score)（sigmoid 归一化分数）
    score_child_prop   评分 × 子代惩罚：w = sigmoid(score) × exp(-(children/N)^P)
                       （借鉴 HyperAgents 权重公式，子代越多权重越低，默认策略）

【配置（.env，全部带默认值，风险段要求参数不写死）】
    EVOLUTION_PARENT_STRATEGY        父代策略名，默认 score_child_prop
    EVOLUTION_CHILD_PENALTY_N        子代惩罚基数 N，默认 8
    EVOLUTION_CHILD_PENALTY_POWER    子代惩罚指数 P，默认 3

【候选范围（不易）】
    技能谱系中 active（未归档）且 decision="committed" 的历史版本；
    归档摘要（超过 active_generations 的旧代）参数已压缩丢弃，不作父代候选。

【线程安全】
    EvolutionArchive 自身持锁，select 只读调用；本类无共享可变状态。
"""

from __future__ import annotations

import math
import os
import random
from enum import Enum
from typing import List, Optional

from .lineage import EvolutionArchive, get_default_archive
from .observability import logger


# ════════════════════════════════════════════════════════════
#  策略枚举与默认配置
# ════════════════════════════════════════════════════════════

class ParentSelectionStrategy(str, Enum):
    """父代选择策略枚举"""
    BEST = "best"                  # 贪心：最高评分
    LATEST = "latest"              # 最新提交版本
    RANDOM = "random"              # 均匀随机
    SCORE_PROP = "score_prop"      # sigmoid(score) 概率采样
    SCORE_CHILD_PROP = "score_child_prop"  # sigmoid(score) × 子代惩罚


_DEFAULT_STRATEGY = ParentSelectionStrategy.SCORE_CHILD_PROP
_DEFAULT_CHILD_PENALTY_N = 8
_DEFAULT_CHILD_PENALTY_POWER = 3


def _env_strategy() -> ParentSelectionStrategy:
    raw = os.getenv("EVOLUTION_PARENT_STRATEGY", _DEFAULT_STRATEGY.value).strip().lower()
    try:
        return ParentSelectionStrategy(raw)
    except ValueError:
        logger.warning(
            "[ParentSelection] 非法策略 %r，回退默认 %s",
            raw, _DEFAULT_STRATEGY.value,
        )
        return _DEFAULT_STRATEGY


def _env_int(key: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(key, str(default))))
    except (TypeError, ValueError):
        return default


def _env_child_penalty_n() -> int:
    return _env_int("EVOLUTION_CHILD_PENALTY_N", _DEFAULT_CHILD_PENALTY_N)


def _env_child_penalty_power() -> int:
    return _env_int("EVOLUTION_CHILD_PENALTY_POWER", _DEFAULT_CHILD_PENALTY_POWER)


# ════════════════════════════════════════════════════════════
#  权重公式（纯函数，供测试直接断言衰减行为）
# ════════════════════════════════════════════════════════════

def sigmoid(x: float) -> float:
    """sigmoid 归一化：score ∈ [0,1] 映射到 (0.5, 0.73]，单调递增"""
    try:
        return 1.0 / (1.0 + math.exp(-float(x)))
    except OverflowError:  # 极端大值 → 趋近 1
        return 1.0


def child_penalty(children: int, n: int = _DEFAULT_CHILD_PENALTY_N,
                  power: int = _DEFAULT_CHILD_PENALTY_POWER) -> float:
    """子代惩罚：exp(-(children/N)^P)，子代越多 → 惩罚越重（权重越低）

    子代数量 0 时惩罚为 1（无衰减）；children == N 时约为 0.37；
    children 显著大于 N 时趋近 0（该父代基本不再被选为父代）。
    """
    if children <= 0:
        return 1.0
    ratio = children / max(1, n)
    return math.exp(-(ratio ** power))


def score_weight(score: Optional[float]) -> float:
    """评分概率权重：sigmoid(score)；无评分按 0 处理（0.5 权重，保留探索机会）"""
    return sigmoid(float(score) if score is not None else 0.0)


def score_child_prop_weight(score: Optional[float], children: int, *,
                            n: int = _DEFAULT_CHILD_PENALTY_N,
                            power: int = _DEFAULT_CHILD_PENALTY_POWER) -> float:
    """评分 × 子代惩罚 联合权重：sigmoid(score) × exp(-(children/N)^P)"""
    return score_weight(score) * child_penalty(children, n, power)


# ════════════════════════════════════════════════════════════
#  父代选择器
# ════════════════════════════════════════════════════════════

class ParentSelector:
    """父代选择器 — 从技能谱系中按策略选出一个父代版本

    Args:
        archive: 进化档案库（None=默认全局单例）
        strategy: 选择策略（None=读 .env EVOLUTION_PARENT_STRATEGY）
        child_penalty_n: 子代惩罚基数（None=读 .env）
        child_penalty_power: 子代惩罚指数（None=读 .env）
        rng: 随机源（测试注入固定种子可复现）
    """

    def __init__(self, archive: Optional[EvolutionArchive] = None, *,
                 strategy: Optional[ParentSelectionStrategy] = None,
                 child_penalty_n: Optional[int] = None,
                 child_penalty_power: Optional[int] = None,
                 rng: Optional[random.Random] = None):
        self._archive = archive or get_default_archive()
        self.strategy = strategy if strategy is not None else _env_strategy()
        self.child_penalty_n = (
            child_penalty_n if child_penalty_n is not None else _env_child_penalty_n())
        self.child_penalty_power = (
            child_penalty_power if child_penalty_power is not None
            else _env_child_penalty_power())
        self._rng = rng if rng is not None else random.Random()

    @property
    def archive(self) -> EvolutionArchive:
        return self._archive

    # ─── 候选与权重 ───

    def candidate_records(self, skill_id: str) -> List["EvolutionRecord"]:
        """父代候选：active（未归档）且 committed 的历史版本（按 created_at 升序）

        不变量（不易）: 归档摘要不含参数快照，无法作为变异基座 → 排除；
        非 committed 记录（rejected/skipped）不构成"可遗传的父代" → 排除。
        """
        recs = [r for r in self._archive.list_by_object(skill_id)
                if not r.archived and r.decision == "committed"]
        recs.sort(key=lambda r: r.created_at)
        return recs

    def children_count(self, skill_id: str, record_id: str) -> int:
        """统计某父代记录的子代数：parent_record_id 指向它的记录数

        含已归档子代（子代可能已被分层压缩），保证衰减估计不因归档失真。
        """
        return sum(
            1 for r in self._archive.list_by_object(skill_id)
            if r.parent_record_id == record_id
        )

    def weights(self, skill_id: str,
                candidates: Optional[List["EvolutionRecord"]] = None) -> List[float]:
        """按策略计算每个候选的采样权重（顺序与 candidates 一致）

        - best / latest 为确定性策略，权重仅供 select 内部分支使用；
        - score_prop / score_child_prop 返回概率权重；
        - random 返回全 1（均匀）。
        所有权重为 0 时由 select 兜底为均匀采样（防坍缩）。
        """
        cands = candidates if candidates is not None else self.candidate_records(skill_id)
        if self.strategy == ParentSelectionStrategy.RANDOM:
            return [1.0] * len(cands)
        if self.strategy in (ParentSelectionStrategy.BEST,
                             ParentSelectionStrategy.LATEST):
            # 确定性策略不走概率采样，返回全 1 仅占位
            return [1.0] * len(cands)
        if self.strategy == ParentSelectionStrategy.SCORE_PROP:
            return [score_weight(r.get_score()) for r in cands]
        # score_child_prop
        return [
            score_child_prop_weight(
                r.get_score(),
                self.children_count(skill_id, r.record_id),
                n=self.child_penalty_n, power=self.child_penalty_power,
            )
            for r in cands
        ]

    def select(self, skill_id: str) -> Optional["EvolutionRecord"]:
        """按策略选出一个父代记录；无候选返回 None（首代，无父代）"""
        cands = self.candidate_records(skill_id)
        if not cands:
            logger.info(
                "[ParentSelection] skill=%s 无父代候选（首代），退化直接变异",
                skill_id,
            )
            return None

        if self.strategy == ParentSelectionStrategy.LATEST:
            chosen = cands[-1]
        elif self.strategy == ParentSelectionStrategy.BEST:
            chosen = max(cands, key=lambda r: r.get_score() if r.get_score() is not None else -1.0)
        else:
            w = self.weights(skill_id, cands)
            if sum(w) <= 0:
                w = [1.0] * len(cands)  # 全零权重兜底均匀（防坍缩）
            # 权重明细日志（仅 score_child_prop 策略）：逐候选输出
            # sigmoid(score) 与 child_penalty(children) 的乘积分解，
            # 便于确认"子代惩罚是否生效"（子代越多 → penalty 越小 → 权重越低）。
            if self.strategy == ParentSelectionStrategy.SCORE_CHILD_PROP:
                detail = []
                for r, wi in zip(cands, w):
                    children = self.children_count(skill_id, r.record_id)
                    sig = score_weight(r.get_score())
                    penalty = child_penalty(
                        children, n=self.child_penalty_n,
                        power=self.child_penalty_power)
                    detail.append({
                        "record_id": r.record_id,
                        "version": r.new_version or r.parent_version,
                        "score": r.get_score(),
                        "sigmoid": round(sig, 4),
                        "children": children,
                        "child_penalty": round(penalty, 4),
                        "weight": round(sig * penalty, 6),
                    })
                logger.info(
                    "[ParentSelection] skill=%s strategy=%s N=%d P=%d "
                    "权重明细(weight=sigmoid(score)*child_penalty(children)): %s",
                    skill_id, self.strategy.value,
                    self.child_penalty_n, self.child_penalty_power, detail,
                )
            chosen = self._rng.choices(cands, weights=w, k=1)[0]

        logger.info(
            "[ParentSelection] skill=%s strategy=%s 选中父代 %s "
            "score=%s children=%d (v%s)",
            skill_id, self.strategy.value, chosen.record_id,
            chosen.get_score(),
            self.children_count(skill_id, chosen.record_id),
            chosen.new_version or chosen.parent_version,
        )
        return chosen