"""离线批量进化器 — P1.4（任务 EVO-T3：进化循环强化与自动化调度）

能力:
    1. 批量进化: 对多个技能进行多轮参数迭代
    2. 变异算法: 策略模式,支持参数微调/组合/突变/重置
    3. 帕累托前沿: 多目标优化(成功率 + 延迟 + 满意度)
    4. 父代选择压力: 5 种策略（best/latest/random/score_prop/score_child_prop），
       默认 score_child_prop（sigmoid(score) × exp(-(children/N)^P)，子代惩罚）
    5. 真实评估: 默认接入任务 2 评估器（EVOLUTION_DEFAULT_EVALUATOR=real），
       无样本时启发式占位并记录 no_samples 到谱系（不伪造指标）
    6. 谱系记录: 提交/拒绝/跳过均写入 EvolutionArchive（任务 1 钩子启用）
    7. 成本控制: 每轮 token/耗时核算 + 预算熔断 EVOLUTION_MAX_TOKENS_PER_ROUND
    8. cron触发: 真实调度（复用 agent/task_scheduler.py），默认关闭
       （EVOLUTION_SCHEDULE_ENABLED=false，安全底线）

核心流程 (evolve_once):
    父代选择 → 基线评估 → 变异 → 变异体评估(预算熔断) → 帕累托 → 提交判定 + 谱系

设计原则:
    - 安全第一: 每轮进化前快照,失败可回滚 (复用 enhancer.bump_version)
    - 边界显性化: 变异失败/评估异常 → 跳过该候选,不中断批量
    - 可观测: 结构化日志 + emit_metric 埋点
    - 谱系不变量（不易）: 提交/拒绝/跳过三条路径都必须产生 EvolutionRecord，
      parent_record_id 串联父代（首代为 None）
    - 调度默认关闭: 自动化进化直接消耗 token 与磁盘，未经用户确认禁止默认开启
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .enhancer import SkillEnhancer, VersionBump
from .lineage import _BATCH_OBJECT_ID, EvolutionArchive, EvolutionRecord, get_default_archive
from .models import Skill, SkillMetrics
from .observability import logger, emit_metric, traced_action
from .parent_selection import ParentSelectionStrategy, ParentSelector
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
    eval_result: Optional[Dict[str, Any]] = None  # 真实评估结果快照（EVO-T2）


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
    # 任务 EVO-T3 扩展
    score: Optional[float] = None          # 最优变异体评分
    parent_record_id: Optional[str] = None  # 父代谱系记录 ID（首代为 None）
    cost_tokens: int = 0                   # 本轮评估累计 token
    duration_ms: float = 0.0               # 本轮总耗时
    record_id: Optional[str] = None        # 本轮谱系记录 ID（committed/rejected/skipped）
    decision: str = ""                     # committed / rejected / skipped / pending_review
    approval_record_id: Optional[str] = None  # 审批流记录 ID（pending_review 时）
    budget_breached: bool = False          # 是否触发预算熔断


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
    # 任务 EVO-T3 扩展
    trigger: str = "manual"                  # manual / scheduler / api
    cost_tokens: int = 0                     # 总 token 消耗
    total_duration_ms: float = 0.0           # 批量总耗时
    budget_usage_ratio: float = 0.0          # 预算使用率 (总 token / 单轮预算)
    budget_breached: bool = False            # 是否触发预算熔断
    score_series: List[Dict[str, Any]] = field(default_factory=list)  # 每代评分序列（供任务 6 仪表盘）


# ════════════════════════════════════════════════════════════
#  环境配置辅助（全部经 .env，带默认值）
# ════════════════════════════════════════════════════════════

def _env_max_tokens_per_round() -> int:
    """每轮进化 token 预算（EVOLUTION_MAX_TOKENS_PER_ROUND，默认 500000；0=不熔断）"""
    try:
        return max(0, int(os.getenv("EVOLUTION_MAX_TOKENS_PER_ROUND", "500000")))
    except (TypeError, ValueError):
        return 500000


def _env_dynamic_budget_enabled() -> bool:
    """动态预算总开关（EVOLUTION_DYNAMIC_BUDGET，默认关闭 — 安全底线）

    开启后 evolve_batch 按候选技能数 N 动态计算预算：
    budget = max(EVOLUTION_BUDGET_MIN, N × EVOLUTION_PER_SKILL_TOKEN_BUDGET)，
    保证技能池增长/单技能成本激增时熔断余量始终 ≥ 4 倍。
    """
    return os.getenv("EVOLUTION_DYNAMIC_BUDGET", "false").strip().lower() in (
        "1", "true", "yes", "on")


def _env_per_skill_token_budget(default: int = 2400) -> int:
    """单技能 token 预算（EVOLUTION_PER_SKILL_TOKEN_BUDGET，默认 2400）

    Why 2400: 蒙特卡洛模拟单技能/轮成本约 600 tokens（±15%），
    按 ≥4 倍熔断余量要求 → 2400 / 600 = 4.0×（50 技能 → 120,000）。
    """
    try:
        return max(0, int(os.getenv("EVOLUTION_PER_SKILL_TOKEN_BUDGET", str(default))))
    except (TypeError, ValueError):
        return default


def _env_budget_min(default: int = 50000) -> int:
    """动态预算下限（EVOLUTION_BUDGET_MIN，默认 50000）— 小技能池时防止预算过小"""
    try:
        return max(0, int(os.getenv("EVOLUTION_BUDGET_MIN", str(default))))
    except (TypeError, ValueError):
        return default


def _env_default_evaluator_mode() -> str:
    """默认真实评估开关（EVOLUTION_DEFAULT_EVALUATOR，代码默认 heuristic）

    Why 代码默认 heuristic: 单元测试须密闭（无样本/无子进程依赖），
    生产环境通过 .env 设置 real 后，未注入评估器的 evolve_once 自动走真实评估。
    显式注入的评估器不受此开关影响（始终走真实评估）。
    """
    return os.getenv("EVOLUTION_DEFAULT_EVALUATOR", "heuristic").strip().lower()


def _env_schedule_enabled() -> bool:
    """调度开关（EVOLUTION_SCHEDULE_ENABLED，默认关闭 — 安全底线）"""
    return os.getenv("EVOLUTION_SCHEDULE_ENABLED", "false").strip().lower() in (
        "1", "true", "yes", "on")


def _env_regression_gate_mode() -> str:
    """评估集回归门禁模式（EVOLUTION_REGRESSION_GATE，默认 warn_only）

    off:        完全不调用回归门禁（零开销）
    warn_only:  门禁运行但只读告警（FAIL/NO_SAMPLES 仅日志，不拦截提交）
    enforce:    门禁拦截（FAIL/NO_SAMPLES/budget_exceeded → 记录谱系 rejected 并跳过提交）

    Why 默认 warn_only: 守"上线即改行为"风险——正式拦截需显式配置开启。
    """
    return os.getenv("EVOLUTION_REGRESSION_GATE", "warn_only").strip().lower()


def _env_regression_set_version() -> str:
    """回归门禁默认样本集版本（EVAL_REGRESSION_DEFAULT_SET，默认 v1）"""
    return os.getenv("EVAL_REGRESSION_DEFAULT_SET", "v1").strip() or "v1"


def _env_cron_expr(default: str) -> str:
    return os.getenv("EVOLUTION_CRON_EXPR", default).strip()


# ════════════════════════════════════════════════════════════
#  Cron 解析与下次触发计算（纯函数，支持 mock 时间测试）
# ════════════════════════════════════════════════════════════

def _parse_cron_field(spec: str, lo: int, hi: int) -> Optional[int]:
    """解析单个 cron 字段：'*' → None；合法整数 → int；否则 ValueError

    Why 简易: 任务调度器（task_scheduler.py）只支持单值/通配，不解析
    列表/范围/步进（list/range/step），避免过度抽象。
    """
    if spec == "*":
        return None
    try:
        value = int(spec)
    except ValueError as e:
        raise ValueError(f"非法 cron 字段: {spec!r}") from e
    if value < lo or value > hi:
        raise ValueError(f"cron 字段越界: {spec} (允许 {lo}-{hi})")
    return value


def _parse_cron(expr: str) -> Optional[Dict[str, Optional[int]]]:
    """解析 5 字段 cron 表达式 → {"minute","hour","day_of_month","month","day_of_week"}

    周字段约定: cron 周日=0 → 转为 python weekday（周日=6），与 task_scheduler 对齐。
    非法表达式返回 None。
    """
    parts = [p.strip() for p in expr.split()]
    if len(parts) != 5:
        return None
    try:
        minute = _parse_cron_field(parts[0], 0, 59)
        hour = _parse_cron_field(parts[1], 0, 23)
        dom = _parse_cron_field(parts[2], 1, 31)
        month = _parse_cron_field(parts[3], 1, 12)
        dow = _parse_cron_field(parts[4], 0, 6)
    except ValueError:
        return None
    if dow is not None:
        dow = (dow + 6) % 7  # cron 周日=0 → python weekday 周日=6
    return {
        "minute": minute,
        "hour": hour,
        "day_of_month": dom,
        "month": month,
        "day_of_week": dow,
    }


def _next_cron_run(cron_expr: str,
                   now: Optional[datetime] = None) -> Optional[datetime]:
    """计算 cron 表达式的下一次触发时间（纯函数，now 可注入以便 mock 测试）

    简化语义: 分/时/周精确匹配，日/月支持 * 或单值，多条件组合为 AND
    （与 task_scheduler._should_run 的简化模型一致）。
    """
    parsed = _parse_cron(cron_expr)
    if parsed is None:
        return None
    now = now or datetime.now()
    minute = parsed["minute"] if parsed["minute"] is not None else 0
    hour = parsed["hour"] if parsed["hour"] is not None else 0
    # 最多向前搜索一年（覆盖罕见但合法的每月/每年触发）
    for offset in range(0, 367):
        day = now + timedelta(days=offset)
        if parsed["month"] is not None and day.month != parsed["month"]:
            continue
        if parsed["day_of_month"] is not None and day.day != parsed["day_of_month"]:
            continue
        if parsed["day_of_week"] is not None and day.weekday() != parsed["day_of_week"]:
            continue
        candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now:
            return candidate
    return None


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
        父代选择 → 基线评估 → 生成变异体 → 评估(预算熔断) → 帕累托 → 提交+谱系

    使用示例:
        evolver = OfflineEvolver(store, enhancer, archive=archive)
        result = evolver.evolve_once("my-skill")          # 手动单轮
        report = evolver.evolve_batch(max_rounds=3)       # 手动批量
        info = evolver.schedule()                         # 注册 cron（默认关闭）
    """

    def __init__(self, store: SkillStore, enhancer: SkillEnhancer, *,
                 min_usage: int = 10,
                 target_success_rate: float = 0.95,
                 max_variants_per_skill: int = 5,
                 improvement_threshold: float = 0.05,
                 random_seed: Optional[int] = None,
                 archive: Optional[EvolutionArchive] = None,
                 parent_selector: Optional[ParentSelector] = None,
                 evaluator_factory: Optional[Callable[[Skill], Any]] = None,
                 max_tokens_per_round: Optional[int] = None,
                 regression_gate: Optional[Any] = None):
        """
        Args:
            store: 技能存储
            enhancer: 技能增强器 (复用版本管理+参数优化)
            min_usage: 候选技能最小使用次数阈值
            target_success_rate: 目标成功率 (低于此值才纳入进化)
            max_variants_per_skill: 每个技能每轮生成的最大变异体数
            improvement_threshold: 评分提升阈值 (低于此值不提交)
            random_seed: 随机种子 (可复现)
            archive: 进化档案库（None=优先复用 enhancer 注入的档案库，否则全局单例）
            parent_selector: 父代选择器（None=默认策略，读 EVOLUTION_PARENT_STRATEGY）
            evaluator_factory: 评估器工厂 callable(skill)->evaluator；None 时按
                EVOLUTION_DEFAULT_EVALUATOR 自动构建（real=任务 2 真实评估器）
            max_tokens_per_round: 每轮 token 预算（None=读 .env）
            regression_gate: 评估集回归门禁（任务1）。None=按配置惰性构建
                （EVOLUTION_REGRESSION_GATE 控制 off/warn_only/enforce，默认 warn_only）；
                注入实例（RegressionGate/桩）时绕过配置惰性构建，直接复用。
        """
        self._store = store
        self._enhancer = enhancer
        self.min_usage = min_usage
        self.target_success_rate = target_success_rate
        self.max_variants_per_skill = max_variants_per_skill
        self.improvement_threshold = improvement_threshold
        self._rng = random.Random(random_seed)
        self._archive = self._resolve_archive(enhancer, archive)
        self._parent_selector = parent_selector or ParentSelector(
            self._archive, rng=self._rng)
        self._evaluator_factory = evaluator_factory
        self._regression_gate = regression_gate  # 任务1 回归门禁（None=按配置惰性构建）
        self._max_tokens_per_round = (
            max_tokens_per_round if max_tokens_per_round is not None
            else _env_max_tokens_per_round())
        # 显式注入标记：动态预算公式只覆盖 .env 默认路径。
        # 沙箱/单测显式传预算（构造参数）或直接赋值实例属性时不受影响（防破坏验证）。
        self._budget_injected = max_tokens_per_round is not None
        # 谱系钩子上下文：evolve_once 提交前填充，bump_version 触发钩子时读取
        self._round_ctx: Dict[str, Any] = {}
        # 调度状态
        self._scheduled_task_id: Optional[str] = None
        self._scheduled_skill_ids: Optional[List[str]] = None
        self._scheduled_max_rounds: int = 1
        # 启用任务 1 谱系钩子：每次 bump_version（提交）自动写 committed 记录
        self._enhancer.set_lineage_hook(self._lineage_hook)

    # ════════════════════════════════════════════════════════════
    #  公共接口
    # ════════════════════════════════════════════════════════════

    def evolve_once(self, skill_id: str, *,
                    strategies: Optional[List[EvolutionStrategy]] = None,
                    evaluator: Optional[Any] = None,
                    trigger: str = "manual",
                    approval_flow: Optional[Any] = None,
                    value_guard: Optional[Any] = None) -> EvolutionResult:
        """对单个技能执行一轮进化

        流程:
            1. 加载技能 + 校验候选资格（跳过 → 谱系记录 skipped）
            2. 父代选择（默认 score_child_prop，从谱系活跃 committed 记录中选）
            3. 基线评估（真实评估优先；显式注入评估器不可用 → 跳过不伪造；
               默认路径无样本 → 记录 no_samples + 启发式占位）
            4. 生成变异体（基于父代参数）
            5. 评估每个变异体（预算熔断：超限立即中止剩余评估）
            6. 帕累托前沿筛选
            7. 提交最优变异体（提升超过阈值；提交/拒绝/跳过均写谱系）

        Args:
            skill_id: 技能ID
            strategies: 使用的变异策略列表 (None=按默认权重采样)
            evaluator: 真实评估器（EVO-T2）。注入后基线/变异体均走真实评估，
                       不可用时跳过（绝不伪造指标）；不传则按配置自动构建。
            trigger: 触发来源（manual/scheduler/api），写入谱系
            approval_flow: 审批流（EVO-T6 验收 8）。注入后 L1/L2 变更转为
                           pending_review 不提交（返回 approval_record_id），
                           L0 或未注入保持直连提交（守不易：既有路径不破坏）。
            value_guard: 价值观红线（EVO-T6 验收 5）。命中红线 → rejected 不提交。

        Returns:
            EvolutionResult — 包含提升幅度、提交决策、谱系记录 ID、成本
        """
        t_total = time.time()
        with traced_action("evolve_once", skill_id=skill_id):
            # 步骤0: 入口快照（排查：本轮入参/预算/阈值/选择策略）
            logger.info(json.dumps({
                "module_name": "offline_evolver",
                "action": "evolve_once.start",
                "skill_id": skill_id,
                "trigger": trigger,
                "strategies": [s.value for s in strategies]
                if strategies else "default",
                "min_usage": self.min_usage,
                "target_success_rate": self.target_success_rate,
                "improvement_threshold": self.improvement_threshold,
                "max_tokens_per_round": self._max_tokens_per_round,
                "parent_strategy": self._parent_selector.strategy.value,
            }, ensure_ascii=False))
            # 步骤1: 加载技能
            t0 = time.time()
            try:
                skill = self._store.get(skill_id)
            except SkillNotFoundError:
                skill = None
            if skill is None:
                # Why 兼容两种缺失语义: SkillStore.get 对不存在技能返回 None，
                # 部分调用方抛 SkillNotFoundError；统一按"技能不存在"跳过并写谱系。
                logger.warning(json.dumps({
                    "module_name": "offline_evolver",
                    "action": "evolve_once.skill_not_found",
                    "skill_id": skill_id,
                    "duration_ms": round((time.time() - t0) * 1000, 2),
                }, ensure_ascii=False))
                self._record_round(
                    skill_id, decision="skipped",
                    reason=f"技能不存在: {skill_id}", trigger=trigger)
                return EvolutionResult(
                    skill_id=skill_id, skipped=True,
                    error=f"技能不存在: {skill_id}",
                    decision="skipped", duration_ms=round((time.time() - t_total) * 1000, 2),
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
                self._record_round(
                    skill_id, decision="skipped",
                    reason=f"不满足候选条件 (usage={skill.metrics.usage_count}, "
                           f"success_rate={skill.metrics.success_rate:.2f})",
                    trigger=trigger)
                return EvolutionResult(
                    skill_id=skill_id, skipped=True,
                    error=f"不满足候选条件 (usage={skill.metrics.usage_count}, "
                          f"success_rate={skill.metrics.success_rate:.2f})",
                    decision="skipped", duration_ms=round((time.time() - t_total) * 1000, 2),
                )

            # 步骤3: 父代选择（引入选择压力）
            old_version = skill.version
            parent = self._select_parent(skill_id)
            parent_record_id = parent.record_id if parent else None
            parent_version = (
                parent.new_version or parent.parent_version
                if parent else old_version)
            base_params = (
                dict(parent.params)
                if parent is not None and parent.params is not None
                else dict(skill.default_params))
            if parent is not None:
                logger.info(json.dumps({
                    "module_name": "offline_evolver",
                    "action": "evolve_once.parent_selected",
                    "skill_id": skill_id,
                    "parent_record_id": parent_record_id,
                    "parent_version": parent_version,
                    "parent_score": parent.get_score(),
                    "strategy": self._parent_selector.strategy.value,
                }, ensure_ascii=False))
            else:
                # 首代：谱系无可用父代 → 退化直接变异（base=当前默认参数）
                logger.info(json.dumps({
                    "module_name": "offline_evolver",
                    "action": "evolve_once.no_parent",
                    "skill_id": skill_id,
                    "reason": "谱系中无可用父代（首代或历史已归档）",
                    "base_params": base_params,
                    "parent_strategy": self._parent_selector.strategy.value,
                }, ensure_ascii=False))

            # 步骤4: 基线评估（真实评估器默认路径）
            t_eval_base = time.time()
            evaluator_injected = evaluator is not None
            if evaluator is None:
                evaluator = self._build_default_evaluator(skill)
            using_default_eval = evaluator is not None and not evaluator_injected
            eval_mode = "heuristic"
            old_score = 0.0
            round_tokens = 0
            if evaluator is not None:
                try:
                    base_eval = evaluator.evaluate(skill)
                except Exception as e:  # noqa: BLE001 评估器异常 → 回退
                    logger.warning(json.dumps({
                        "module_name": "offline_evolver",
                        "action": "evolve_once.baseline_real_eval.error",
                        "skill_id": skill_id,
                        "error": str(e),
                    }, ensure_ascii=False))
                    base_eval = None
                if base_eval is not None:
                    round_tokens += base_eval.cost_tokens or 0
                    if base_eval.status in ("no_samples", "budget_exceeded",
                                            "degraded"):
                        detail = "; ".join(base_eval.notes[:1])
                        if not using_default_eval:
                            # 显式注入评估器 → 严守"绝不伪造指标"：跳过
                            self._record_round(
                                skill_id, decision="skipped",
                                reason=f"真实评估不可用 ({base_eval.status})："
                                       f"{detail}（绝不伪造指标）",
                                parent_record_id=parent_record_id,
                                trigger=trigger,
                                eval_result=base_eval.to_eval_result_dict(),
                                cost={"tokens": base_eval.cost_tokens or 0,
                                      "duration_ms": round(
                                          (time.time() - t_eval_base) * 1000, 2)},
                                params=base_params, parent_version=parent_version)
                            return EvolutionResult(
                                skill_id=skill_id, skipped=True,
                                error=f"真实评估不可用 ({base_eval.status})："
                                      f"{detail}（绝不伪造指标）",
                                decision="skipped",
                                cost_tokens=base_eval.cost_tokens or 0,
                                duration_ms=round((time.time() - t_total) * 1000, 2),
                            )
                        # 默认路径 → 记录 no_samples + 启发式占位（任务要求）
                        eval_mode = "heuristic_fallback"
                        self._record_round(
                            skill_id, decision="skipped",
                            reason=f"真实评估不可用 ({base_eval.status})，"
                                   f"启发式占位并记录 no_samples：{detail}",
                            parent_record_id=parent_record_id,
                            trigger=trigger,
                            eval_result=base_eval.to_eval_result_dict(),
                            cost={"tokens": base_eval.cost_tokens or 0,
                                  "duration_ms": round(
                                      (time.time() - t_eval_base) * 1000, 2)},
                            params=base_params, parent_version=parent_version)
                        logger.warning(json.dumps({
                            "module_name": "offline_evolver",
                            "action": "evolve_once.baseline_real_eval.unavailable",
                            "skill_id": skill_id,
                            "status": base_eval.status,
                            "fallback": "heuristic",
                        }, ensure_ascii=False))
                    else:
                        old_score = base_eval.score
                        eval_mode = "real"
                        logger.info(json.dumps({
                            "module_name": "offline_evolver",
                            "action": "evolve_once.baseline_real_eval",
                            "skill_id": skill_id,
                            "eval_mode": "real",
                            "old_score": round(old_score, 4),
                            "old_version": old_version,
                            "status": base_eval.status,
                            "samples": base_eval.sample_count,
                            "used_tokens": base_eval.cost_tokens,
                            "baseline_eval_ms": round(
                                (time.time() - t_eval_base) * 1000, 2),
                        }, ensure_ascii=False))
            if eval_mode in ("heuristic", "heuristic_fallback"):
                old_score = self._evaluate_skill(skill)
                logger.info(json.dumps({
                    "module_name": "offline_evolver",
                    "action": "evolve_once.baseline",
                    "skill_id": skill_id,
                    "eval_mode": eval_mode,
                    "old_score": old_score,
                    "old_version": old_version,
                    "baseline_eval_ms": round(
                        (time.time() - t_eval_base) * 1000, 2),
                }, ensure_ascii=False))

            # 步骤5: 生成变异体（基于父代参数）
            t_mutate = time.time()
            variants = self._mutate(
                skill, strategies or self._sample_strategies(),
                base_params=base_params, parent_version=parent_version)
            mutate_ms = (time.time() - t_mutate) * 1000
            if not variants:
                logger.warning(json.dumps({
                    "module_name": "offline_evolver",
                    "action": "evolve_once.no_variants",
                    "skill_id": skill_id,
                    "mutate_ms": round(mutate_ms, 2),
                }, ensure_ascii=False))
                self._record_round(
                    skill_id, decision="skipped",
                    reason="未生成任何变异体",
                    parent_record_id=parent_record_id, trigger=trigger,
                    cost={"tokens": round_tokens,
                          "duration_ms": round(mutate_ms, 2)},
                    params=base_params, parent_version=parent_version)
                return EvolutionResult(
                    skill_id=skill_id, skipped=True,
                    error="未生成任何变异体", decision="skipped",
                    parent_record_id=parent_record_id,
                    cost_tokens=round_tokens,
                    duration_ms=round((time.time() - t_total) * 1000, 2),
                )

            # 步骤6: 评估变异体（真实评估透传变异参数；预算熔断中止剩余评估）
            t_eval = time.time()
            budget_breached = False
            if evaluator is not None and eval_mode == "real":
                for v in variants:
                    if (self._max_tokens_per_round > 0
                            and round_tokens >= self._max_tokens_per_round):
                        budget_breached = True
                        logger.warning(json.dumps({
                            "module_name": "offline_evolver",
                            "action": "evolve_once.budget_break",
                            "skill_id": skill_id,
                            "used_tokens": round_tokens,
                            "budget": self._max_tokens_per_round,
                            "remaining_variants": len(variants) - variants.index(v),
                        }, ensure_ascii=False))
                        break
                    ev = evaluator.evaluate(skill, params=v.params)
                    round_tokens += ev.cost_tokens or 0
                    if ev.status in ("no_samples", "budget_exceeded",
                                     "degraded"):
                        if using_default_eval:
                            # 默认路径 → 启发式占位（no_samples 已记录）
                            v.score = self._evaluate(v)
                            v.objectives = self._compute_objectives(v)
                            v.eval_result = None
                        else:
                            # 显式注入 → 绝不伪造分数：该变异体不参与比较
                            v.score = None
                            v.eval_result = ev.to_eval_result_dict()
                            logger.warning(json.dumps({
                                "module_name": "offline_evolver",
                                "action": "evolve_once.variant_real_eval.unavailable",
                                "skill_id": skill_id,
                                "strategy": v.strategy.value,
                                "status": ev.status,
                            }, ensure_ascii=False))
                        continue
                    v.score = ev.score
                    v.objectives = {
                        "success_rate": ev.success_rate,
                        "neg_latency": -ev.latency_ms,
                        "satisfaction": ev.satisfaction,
                    }
                    v.eval_result = ev.to_eval_result_dict()
                    logger.info(json.dumps({
                        "module_name": "offline_evolver",
                        "action": "evolve_once.variant_real_eval",
                        "skill_id": skill_id,
                        "eval_mode": "real",
                        "strategy": v.strategy.value,
                        "score": round(ev.score, 4),
                        "samples": ev.sample_count,
                        "used_tokens": ev.cost_tokens,
                    }, ensure_ascii=False))
            else:
                for v in variants:
                    v.score = self._evaluate(v)
                    v.objectives = self._compute_objectives(v)
            eval_ms = (time.time() - t_eval) * 1000
            # 成本核算：本轮累计 token / 预算使用率（熔断边界可观测）
            logger.info(json.dumps({
                "module_name": "offline_evolver",
                "action": "evolve_once.cost_summary",
                "skill_id": skill_id,
                "round_tokens": round_tokens,
                "budget": self._max_tokens_per_round,
                "budget_usage_ratio": (
                    round(round_tokens / self._max_tokens_per_round, 4)
                    if self._max_tokens_per_round > 0 else None),
                "budget_breached": budget_breached,
                "phase_ms": {
                    "pre_mutate": round((t_mutate - t_eval_base) * 1000, 2),
                    "mutate": round(mutate_ms, 2),
                    "eval": round(eval_ms, 2),
                },
            }, ensure_ascii=False))

            # 步骤7: 帕累托筛选 (性能热点)
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
                # 熔断 / 无有效变异体通过评估 → 本轮以 skipped 结束
                # （预算熔断时: budget_breached=true, round_tokens=熔断前累计）
                logger.info(json.dumps({
                    "module_name": "offline_evolver",
                    "action": "evolve_once.skipped",
                    "skill_id": skill_id,
                    "decision": "skipped",
                    "reason": "无有效变异体通过评估",
                    "variants_count": len(variants),
                    "round_tokens": round_tokens,
                    "budget": self._max_tokens_per_round,
                    "budget_breached": budget_breached,
                    "parent_record_id": parent_record_id,
                    "trigger": trigger,
                    "total_ms": round((time.time() - t_total) * 1000, 2),
                }, ensure_ascii=False))
                self._record_round(
                    skill_id, decision="skipped",
                    reason="无有效变异体通过评估",
                    parent_record_id=parent_record_id, trigger=trigger,
                    cost={"tokens": round_tokens,
                          "duration_ms": round((time.time() - t_total) * 1000, 2)},
                    params=base_params, parent_version=parent_version)
                return EvolutionResult(
                    skill_id=skill_id, skipped=True,
                    error="无有效变异体通过评估", decision="skipped",
                    parent_record_id=parent_record_id,
                    cost_tokens=round_tokens,
                    duration_ms=round((time.time() - t_total) * 1000, 2),
                    budget_breached=budget_breached,
                )

            improvement = best.score - old_score
            total_ms = round((time.time() - t_total) * 1000, 2)
            cost = {"tokens": round_tokens, "duration_ms": total_ms}
            result = EvolutionResult(
                skill_id=skill_id,
                strategy=best.strategy,
                old_version=old_version,
                improvement=round(improvement, 4),
                score=round(best.score, 4),
                parent_record_id=parent_record_id,
                cost_tokens=round_tokens,
                duration_ms=total_ms,
                budget_breached=budget_breached,
            )

            # 步骤8: 提交判定（提交/拒绝/跳过均写谱系）
            t_commit = time.time()
            if improvement >= self.improvement_threshold:
                # 提交上下文供谱系钩子使用（bump_version → _lineage_hook）
                self._round_ctx = {
                    "parent_record_id": parent_record_id,
                    "strategy": best.strategy.value,
                    "trigger": trigger,
                    "cost": cost,
                    "params": dict(best.params),
                    "change_summary": (
                        f"离线进化: strategy={best.strategy.value}, "
                        f"improvement={improvement:.4f}, "
                        f"parent={parent_record_id or '首代'}"),
                }
                # 护栏 1: 价值观红线（EVO-T6 验收 5）——命中 → rejected，不提交
                if value_guard is not None:
                    vg_content = (
                        f"离线进化: strategy={best.strategy.value}, "
                        f"improvement={improvement:.4f}, "
                        f"parent={parent_record_id or '首代'}")
                    vg_result = value_guard.check(vg_content)
                    if vg_result.blocked:
                        findings = vg_result.findings or []
                        detail = findings[0].message if findings else "命中自定义红线"
                        vg_reason = f"价值观红线拦截: {detail}"
                        rejected_id = self._record_round(
                            skill_id, decision="rejected",
                            reason=vg_reason,
                            parent_record_id=parent_record_id,
                            trigger=trigger,
                            eval_result=best.eval_result,
                            cost=cost, params=dict(best.params),
                            parent_version=parent_version)
                        result.committed = False
                        result.decision = "rejected"
                        result.record_id = rejected_id
                        result.error = vg_reason
                        self._round_ctx = {}
                        logger.warning(json.dumps({
                            "module_name": "offline_evolver",
                            "action": "evolve_once.value_guard_blocked",
                            "skill_id": skill_id,
                            "reason": vg_reason,
                        }, ensure_ascii=False))
                        return result

                # 护栏 2: 审批流（EVO-T6 验收 8）——L1/L2 → pending_review 不提交
                if approval_flow is not None:
                    level = approval_flow.route_level("skill", "params_submit")
                    if level in ("L1", "L2"):
                        change_summary = (
                            f"离线进化: strategy={best.strategy.value}, "
                            f"improvement={improvement:.4f}, "
                            f"parent={parent_record_id or '首代'}")

                        def _approval_applier() -> bool:
                            """审批 merge 时执行真实提交（bump_version 复用谱系钩子）"""
                            self._round_ctx = {
                                "parent_record_id": parent_record_id,
                                "strategy": best.strategy.value,
                                "trigger": trigger,
                                "cost": cost,
                                "params": dict(best.params),
                                "change_summary": change_summary,
                            }
                            try:
                                return self._commit(best) is not None
                            finally:
                                self._round_ctx = {}

                        rec = approval_flow.submit(
                            "skill", skill_id, action="params_submit",
                            description=change_summary,
                            payload=dict(best.params),
                            eval_result=best.eval_result,
                            trigger=trigger, applier=_approval_applier)
                        pending_id = self._record_round(
                            skill_id, decision="pending_review",
                            reason=f"待审批（level={level}）",
                            parent_record_id=parent_record_id,
                            trigger=trigger,
                            eval_result=best.eval_result,
                            cost=cost, params=dict(best.params),
                            parent_version=parent_version)
                        result.committed = False
                        result.decision = "pending_review"
                        result.record_id = pending_id
                        result.approval_record_id = rec.record_id
                        self._round_ctx = {}
                        logger.info(json.dumps({
                            "module_name": "offline_evolver",
                            "action": "evolve_once.pending_review",
                            "skill_id": skill_id,
                            "level": level,
                            "approval_record_id": rec.record_id,
                        }, ensure_ascii=False))
                        return result

                # 护栏 3: 评估集回归门禁（任务1）——默认 warn_only 只读告警，enforce 拦截
                # 【语义】进化候选需通过门禁方可提交：FAIL/NO_SAMPLES/budget_exceeded 时，
                # warn_only → 只读告警继续提交（守"上线即改行为"风险）；
                # enforce → 记录谱系 decision=rejected 并跳过提交。
                reg_result = self._check_regression_gate(
                    skill, best, evaluator, trigger)
                if reg_result is not None and reg_result.status != "PASS":
                    reg_mode = _env_regression_gate_mode()
                    reg_reason = (
                        f"评估集回归未通过: status={reg_result.status} "
                        f"score={reg_result.score:.4f} "
                        f"baseline={reg_result.baseline_score} "
                        f"delta={reg_result.delta_vs_baseline} "
                        f"set={reg_result.sampleset_version} "
                        f"mode={reg_mode}")
                    if reg_mode == "enforce":
                        rejected_id = self._record_round(
                            skill_id, decision="rejected",
                            reason=reg_reason,
                            parent_record_id=parent_record_id,
                            trigger=trigger,
                            eval_result=reg_result.eval_result or best.eval_result,
                            cost=cost, params=dict(best.params),
                            parent_version=parent_version)
                        result.committed = False
                        result.decision = "rejected"
                        result.record_id = rejected_id
                        result.error = reg_reason
                        self._round_ctx = {}
                        logger.warning(json.dumps({
                            "module_name": "offline_evolver",
                            "action": "evolve_once.regression_gate_rejected",
                            "skill_id": skill_id,
                            "reason": reg_reason,
                        }, ensure_ascii=False))
                        return result
                    # warn_only: 只读告警，继续提交（decision 仍为 committed）

                # 原直连提交路径（无守卫 / 审批 L0 自动放行）
                committed = self._commit(best)
                record_id = self._round_ctx.pop("record_id", "")
                self._round_ctx = {}
                if committed is not None:
                    result.new_version = committed.new_version
                    result.committed = True
                    result.decision = "committed"
                    result.record_id = record_id or None
                    emit_metric("yunshu_skill_evolution_committed_total",
                                value=1, kind="counter",
                                labels={"skill_id": skill_id})
                    logger.info(json.dumps({
                        "module_name": "offline_evolver",
                        "action": "evolve_once.commit",
                        "skill_id": skill_id,
                        "old_version": old_version,
                        "new_version": committed.new_version,
                        "best_score": round(best.score, 4),
                        "improvement": round(improvement, 4),
                        "parent_record_id": parent_record_id,
                        "trigger": trigger,
                        "strategy": best.strategy.value,
                    }, ensure_ascii=False))
                else:
                    # 版本升级异常 → 记录 skipped（谱系不变量：不静默丢失）
                    skipped_id = self._record_round(
                        skill_id, decision="skipped",
                        reason="提交失败（版本升级异常）",
                        parent_record_id=parent_record_id, trigger=trigger,
                        cost=cost, params=dict(best.params),
                        parent_version=parent_version)
                    result.decision = "skipped"
                    result.record_id = skipped_id
                    result.error = "提交失败（版本升级异常）"
                    logger.error(json.dumps({
                        "module_name": "offline_evolver",
                        "action": "evolve_once.commit.failed_recorded",
                        "skill_id": skill_id,
                    }, ensure_ascii=False))
            else:
                result.committed = False
                result.decision = "rejected"
                reason = (f"提升 {improvement:.4f} 低于阈值 "
                          f"{self.improvement_threshold}")
                rejected_id = self._record_round(
                    skill_id, decision="rejected",
                    reason=reason,
                    parent_record_id=parent_record_id, trigger=trigger,
                    eval_result=best.eval_result,
                    cost=cost, params=dict(best.params),
                    parent_version=parent_version)
                result.record_id = rejected_id
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
            total_ms = round((time.time() - t_total) * 1000, 2)
            logger.info(json.dumps({
                "module_name": "offline_evolver",
                "action": "evolve_once.done",
                "skill_id": skill_id,
                "strategy": best.strategy.value,
                "old_score": round(old_score, 4),
                "best_score": round(best.score, 4),
                "variants_count": len(variants),
                "improvement": round(improvement, 4),
                "committed": result.committed,
                "decision": result.decision,
                "record_id": result.record_id,
                "parent_record_id": parent_record_id,
                "cost_tokens": round_tokens,
                "budget_breached": budget_breached,
                "total_ms": total_ms,
                "breakdown_ms": {
                    "mutate": round(mutate_ms, 2),
                    "eval": round(eval_ms, 2),
                    "commit": round(commit_ms, 2),
                },
            }, ensure_ascii=False))

            emit_metric("yunshu_skill_evolution_total",
                        value=1, kind="counter",
                        labels={"skill_id": skill_id,
                                "committed": str(result.committed).lower(),
                                "decision": result.decision})
            emit_metric("yunshu_skill_evolve_latency_ms",
                        value=total_ms, kind="histogram",
                        labels={"skill_id": skill_id})
            emit_metric("yunshu_skill_pareto_variants_count",
                        value=len(variants), kind="gauge",
                        labels={"skill_id": skill_id})
            if budget_breached:
                emit_metric("yunshu_skill_evolution_budget_break_total",
                            value=1, kind="counter",
                            labels={"skill_id": skill_id})
            return result

    def evolve_batch(self, skill_ids: Optional[List[str]] = None, *,
                     max_rounds: int = 1,
                     evaluator: Optional[Any] = None,
                     trigger: str = "manual") -> BatchEvolutionReport:
        """批量进化多个技能

        Args:
            skill_ids: 待进化技能列表 (None=自动选择候选)
            max_rounds: 最大进化轮次 (每轮基于上一轮结果)
            evaluator: 真实评估器（EVO-T2），透传给每次 evolve_once
            trigger: 触发来源（manual/scheduler/api），写入谱系

        Returns:
            BatchEvolutionReport — 批量进化报告（含成本汇总与评分序列）
        """
        started_at = datetime.utcnow().isoformat()
        report = BatchEvolutionReport(started_at=started_at, trigger=trigger)
        t_start = time.time()

        with traced_action("evolve_batch", max_rounds=max_rounds, trigger=trigger):
            candidates = skill_ids or [s.id for s in self._select_candidates()]
            report.total_skills = len(candidates)
            self._apply_dynamic_budget(len(candidates))

            for round_idx in range(max_rounds):
                logger.info(json.dumps({
                    "module_name": "offline_evolver",
                    "action": "evolve_batch.round",
                    "round": round_idx + 1,
                    "total": max_rounds,
                    "candidates": len(candidates),
                    "trigger": trigger,
                }, ensure_ascii=False))

                for skill_id in candidates:
                    result = self.evolve_once(
                        skill_id, evaluator=evaluator, trigger=trigger)
                    report.results.append(result)
                    if result.skipped:
                        report.skipped_count += 1
                    elif result.error:
                        report.failed_count += 1
                    elif result.committed:
                        report.evolved_count += 1
                    if result.budget_breached:
                        report.budget_breached = True

            # 汇总
            committed_results = [r for r in report.results if r.committed]
            if committed_results:
                report.avg_improvement = round(
                    sum(r.improvement for r in committed_results)
                    / len(committed_results), 4)
            report.cost_tokens = sum(r.cost_tokens or 0 for r in report.results)
            report.total_duration_ms = round((time.time() - t_start) * 1000, 2)
            if self._max_tokens_per_round > 0:
                report.budget_usage_ratio = round(
                    report.cost_tokens / self._max_tokens_per_round, 4)
            report.score_series = self._build_score_series(report)
            report.finished_at = datetime.utcnow().isoformat()

            self._write_batch_record(report, trigger)

            logger.info(json.dumps({
                "module_name": "offline_evolver",
                "action": "evolve_batch.done",
                "trigger": trigger,
                "total_skills": report.total_skills,
                "evolved": report.evolved_count,
                "skipped": report.skipped_count,
                "failed": report.failed_count,
                "cost_tokens": report.cost_tokens,
                "total_duration_ms": report.total_duration_ms,
                "budget_usage_ratio": report.budget_usage_ratio,
                "budget_breached": report.budget_breached,
            }, ensure_ascii=False))

            emit_metric("yunshu_skill_evolution_batch_total",
                        value=1, kind="counter",
                        labels={"trigger": trigger,
                                "evolved": str(report.evolved_count),
                                "skipped": str(report.skipped_count),
                                "failed": str(report.failed_count)})
            emit_metric("yunshu_skill_evolution_batch_tokens",
                        value=report.cost_tokens, kind="counter",
                        labels={"trigger": trigger})

        return report

    def _apply_dynamic_budget(self, n_skills: int) -> None:
        """动态预算公式（EVOLUTION_DYNAMIC_BUDGET=true 时生效）

        budget = max(EVOLUTION_BUDGET_MIN, n_skills × EVOLUTION_PER_SKILL_TOKEN_BUDGET)

        Why: 蒙特卡洛模拟显示单技能/轮成本约 600 tokens（±15%）。
        静态预算（如 500000）在技能池增长到 50+ 或单技能成本激增时，
        熔断余量会跌破 4 倍（<2400/600）。按技能数动态放缩可始终维持
        budget / (N×600) ≥ 4.0× 的熔断余量（变易·防成本激增）。

        仅覆盖 .env 默认路径：构造时显式注入预算（沙箱/单测）
        不被覆盖；属性赋值（测试注入小预算）时当前预算 ≠ .env 静态值，
        也不覆盖 —— 保证熔断验证不受影响（不易·安全底线）。
        语义：动态公式只放大不缩回（同一实例技能数回落时保持较大预算，
        方向保守 —— 预算偏大不误熔断，熔断余量更大，成本安全）。
        """
        if not _env_dynamic_budget_enabled() or self._budget_injected:
            return
        if self._max_tokens_per_round != _env_max_tokens_per_round():
            # 预算已被显式修改（测试注入/运维手动调整），尊重显式值
            return
        per_skill = _env_per_skill_token_budget()
        floor = _env_budget_min()
        budget = max(floor, n_skills * per_skill)
        if budget != self._max_tokens_per_round:
            logger.info(json.dumps({
                "module_name": "offline_evolver",
                "action": "dynamic_budget.applied",
                "skills": n_skills,
                "per_skill_budget": per_skill,
                "budget_min": floor,
                "old_budget": self._max_tokens_per_round,
                "new_budget": budget,
                "margin_multiplier": round(budget / (n_skills * 600), 2),
            }, ensure_ascii=False))
            self._max_tokens_per_round = budget

    def schedule(self, cron_expr: str = "0 2 * * *",
                 *, skill_ids: Optional[List[str]] = None,
                 max_rounds: int = 1) -> Dict[str, Any]:
        """注册 cron 定时任务（真实调度，默认关闭）

        【安全底线（不易）】自动化进化直接消耗 token 与磁盘：
        EVOLUTION_SCHEDULE_ENABLED 默认 false，未显式开启时只返回 disabled 信息。

        复用 agent/task_scheduler.py 的 TaskScheduler（不启动 daemon，
        由云枢主进程统一启动），cron 表达式支持 5 字段。

        Args:
            cron_expr: cron 表达式 (默认每天凌晨2点)
            skill_ids: 待进化技能列表 (None=自动选择)
            max_rounds: 最大进化轮次

        Returns:
            {"status": "disabled"|"scheduled"|"error", "cron": ..., "next_run": ...}
        """
        expr = _env_cron_expr(cron_expr)
        parsed = _parse_cron(expr)
        if parsed is None:
            logger.error(json.dumps({
                "module_name": "offline_evolver",
                "action": "schedule.invalid_cron",
                "cron_expr": expr,
            }, ensure_ascii=False))
            return {"status": "error", "cron": expr,
                    "error": f"非法 cron 表达式: {expr}",
                    "next_run": None}

        if not _env_schedule_enabled():
            next_run = _next_cron_run(expr)
            logger.warning(json.dumps({
                "module_name": "offline_evolver",
                "action": "schedule.disabled",
                "cron_expr": expr,
                "note": "EVOLUTION_SCHEDULE_ENABLED 未开启，调度默认关闭（安全底线）",
            }, ensure_ascii=False))
            return {
                "status": "disabled",
                "cron": expr,
                "skill_ids": skill_ids,
                "max_rounds": max_rounds,
                "next_run": next_run.isoformat() if next_run else None,
                "note": "EVOLUTION_SCHEDULE_ENABLED=false，进化调度默认关闭（安全底线）。"
                        "开启: .env 设置 EVOLUTION_SCHEDULE_ENABLED=true；"
                        "手动触发: evolve_batch() / API",
            }

        # 开启调度 → 注册真实 cron 任务
        try:
            from agent.task_scheduler import get_scheduler
            sched = get_scheduler()
        except Exception as e:  # noqa: BLE001 调度器不可用
            logger.error(json.dumps({
                "module_name": "offline_evolver",
                "action": "schedule.scheduler_unavailable",
                "error": str(e),
            }, ensure_ascii=False))
            return {"status": "error", "cron": expr,
                    "error": f"调度器不可用: {e}", "next_run": None}

        self._scheduled_skill_ids = skill_ids
        self._scheduled_max_rounds = max_rounds
        sched.add_cron_task(
            "技能进化",
            func=self._scheduled_run,
            day_of_week=parsed["day_of_week"],
            hour=parsed["hour"] if parsed["hour"] is not None else 0,
            minute=parsed["minute"] if parsed["minute"] is not None else 0,
        )
        self._scheduled_task_id = (
            sched.tasks[-1]["task_id"] if sched.tasks else "skill-evolution")
        next_run = _next_cron_run(expr)

        logger.info(json.dumps({
            "module_name": "offline_evolver",
            "action": "schedule.registered",
            "cron_expr": expr,
            "task_id": self._scheduled_task_id,
            "next_run": next_run.isoformat() if next_run else None,
        }, ensure_ascii=False))
        return {
            "status": "scheduled",
            "cron": expr,
            "task_id": self._scheduled_task_id,
            "skill_ids": skill_ids,
            "max_rounds": max_rounds,
            "next_run": next_run.isoformat() if next_run else None,
            "note": "已注册真实 cron 任务（daemon 由云枢主进程启动）",
        }

    def unschedule(self) -> bool:
        """注销已注册的进化 cron 任务

        Why 按任务名定位而非实例字段: service 网关每次调用可能新建 evolver
        实例，实例字段 _scheduled_task_id 无法跨实例共享；任务名"技能进化"
        固定，可跨实例注销。
        """
        try:
            from agent.task_scheduler import get_scheduler
            sched = get_scheduler()
        except Exception as e:  # noqa: BLE001
            logger.error("进化调度注销失败: %s", e)
            return False
        for task in sched.tasks:
            if task.get("name") == "技能进化":
                removed = sched.remove_task(task["task_id"])
                self._scheduled_task_id = None
                return removed
        return False

    # ════════════════════════════════════════════════════════════
    #  内部方法 — 调度
    # ════════════════════════════════════════════════════════════

    def _scheduled_run(self) -> None:
        """调度触发入口：包裹 evolve_batch，异常不抛出（调度线程稳定性）"""
        logger.info(json.dumps({
            "module_name": "offline_evolver",
            "action": "scheduled_run.start",
        }, ensure_ascii=False))
        try:
            report = self.evolve_batch(
                self._scheduled_skill_ids,
                max_rounds=self._scheduled_max_rounds,
                trigger="scheduler")
            emit_metric("yunshu_skill_evolution_scheduled_run_total",
                        value=1, kind="counter", labels={"status": "ok"})
            logger.info(json.dumps({
                "module_name": "offline_evolver",
                "action": "scheduled_run.done",
                "evolved": report.evolved_count,
                "skipped": report.skipped_count,
                "cost_tokens": report.cost_tokens,
            }, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 调度运行异常必须吞掉（守不易）
            logger.error(json.dumps({
                "module_name": "offline_evolver",
                "action": "scheduled_run.failed",
                "error": str(e),
            }, ensure_ascii=False))
            emit_metric("yunshu_skill_evolution_scheduled_run_total",
                        value=1, kind="counter", labels={"status": "error"})

    # ════════════════════════════════════════════════════════════
    #  内部方法 — 谱系与选择
    # ════════════════════════════════════════════════════════════

    def _resolve_archive(self, enhancer: SkillEnhancer,
                         archive: Optional[EvolutionArchive]) -> EvolutionArchive:
        """解析档案库：显式传入 > enhancer 注入 > 全局单例"""
        if archive is not None:
            return archive
        getter = getattr(enhancer, "_get_lineage_archive", None)
        if callable(getter):
            try:
                linked = getter()
                if linked is not None:
                    return linked
            except Exception as e:  # noqa: BLE001
                logger.warning("[OfflineEvolver] 读取 enhancer 档案库失败: %s", e)
        return get_default_archive()

    def _build_default_evaluator(self, skill: Skill) -> Optional[Any]:
        """按配置构建默认真实评估器

        - EVOLUTION_DEFAULT_EVALUATOR=real（生产默认）→ 任务 2 分阶段评估器；
        - heuristic → None（回退启发式路径，单元测试密闭性）；
        - 构建异常 → None 并告警（不中断进化）。
        """
        mode = _env_default_evaluator_mode()
        if mode not in ("real", "true", "1"):
            return None
        if self._evaluator_factory is not None:
            try:
                return self._evaluator_factory(skill)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[OfflineEvolver] evaluator_factory 构建失败，回退启发式: %s", e)
                return None
        try:
            from .evaluator import get_default_evaluator
            evaluator = get_default_evaluator(skill)
            logger.info("[OfflineEvolver] 默认真实评估器已构建 mode=%s skill=%s",
                        mode, skill.id)
            return evaluator
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[OfflineEvolver] 默认真实评估器构建失败，回退启发式: %s", e)
            return None

    # ════════════════════════════════════════════════════════════
    #  任务1：评估集回归门禁钩子（提交判定处）
    # ════════════════════════════════════════════════════════════

    def _check_regression_gate(self, skill: Skill, variant: Variant,
                               evaluator: Optional[Any],
                               trigger: str) -> Optional[Any]:
        """提交判定处的评估集回归门禁钩子（任务1）

        模式（EVOLUTION_REGRESSION_GATE，默认 warn_only）:
            off:        不调用门禁（返回 None，零开销）
            warn_only:  门禁运行但只读告警——FAIL/NO_SAMPLES/budget_exceeded
                        仅日志告警，不拦截提交（守"上线即改行为"风险）
            enforce:    门禁拦截——非 PASS 时由调用方记录谱系 rejected 并跳过提交

        返回:
            RegressionResult（门禁可用时）或 None（off / 构建失败 / 无评估器不可判定）

        【不易】门禁异常绝不阻断提交：任何异常 → 返回 None 并告警（提交优先）。
        """
        mode = _env_regression_gate_mode()
        if mode == "off":
            return None
        # 门禁需要真实评估能力：无评估器（启发式占位路径）→ 无法判定，跳过（不伪造）
        if evaluator is None:
            logger.info(
                "[OfflineEvolver] 回归门禁跳过 skill=%s: 无真实评估器（启发式路径，不伪造指标）",
                skill.id)
            return None
        try:
            gate = self._resolve_regression_gate()
            if gate is None:
                return None
            version = _env_regression_set_version()
            # warn_only + 无基线 → 跳过评估（零行为变化：无基线无从比较，不额外消耗）
            # 基线由显式门禁 CLI 或 enforce 模式建立。
            if mode == "warn_only" and not gate.has_baseline(skill.id, version):
                logger.info(
                    "[OfflineEvolver] 回归门禁 warn_only 无基线，跳过评估 skill=%s "
                    "set=%s（基线由 CLI/enforce 建立后生效）", skill.id, version)
                return None
            # 首次（enforce）：以当前技能（params=None）建立基线，再评估变异体。
            # Why 顺序: 基线须代表"进化前标准"，不能用变异体分数当基线（防坏候选抬高基线）。
            if not gate.has_baseline(skill.id, version):
                logger.info(
                    "[OfflineEvolver] 回归门禁首次建立基线 skill=%s set=%s "
                    "（以当前技能评估为准）", skill.id, version)
                gate.evaluate(skill, params=None, sampleset_version=version,
                              evaluator=evaluator, record_baseline=True)
            result = gate.evaluate(
                skill, params=variant.params,
                sampleset_version=version,
                evaluator=evaluator)
            if result.status != "PASS":
                logger.warning(
                    "[OfflineEvolver] 回归门禁 mode=%s skill=%s status=%s "
                    "score=%.4f baseline=%s delta=%s set=%s （%s）",
                    mode, skill.id, result.status, result.score,
                    result.baseline_score, result.delta_vs_baseline,
                    result.sampleset_version,
                    "只读告警不拦截" if mode == "warn_only" else "将拦截提交")
            else:
                logger.info(
                    "[OfflineEvolver] 回归门禁 PASS skill=%s score=%.4f "
                    "baseline=%s delta=%s set=%s",
                    skill.id, result.score, result.baseline_score,
                    result.delta_vs_baseline, result.sampleset_version)
            return result
        except Exception as e:  # noqa: BLE001 门禁异常绝不阻断提交
            logger.warning(
                "[OfflineEvolver] 回归门禁调用异常（不阻断提交）skill=%s: %s",
                skill.id, e)
            return None

    def _resolve_regression_gate(self) -> Optional[Any]:
        """解析回归门禁：显式注入 > 惰性构建（异常 → None 并告警）"""
        if self._regression_gate is not None:
            return self._regression_gate
        try:
            from .eval_regression import RegressionGate
            self._regression_gate = RegressionGate()
            return self._regression_gate
        except Exception as e:  # noqa: BLE001 门禁不可用 → 跳过（不阻断进化）
            logger.warning(
                "[OfflineEvolver] 回归门禁构建失败（跳过，不阻断进化）: %s", e)
            return None

    def _select_parent(self, skill_id: str) -> Optional[EvolutionRecord]:
        """父代选择：按当前策略从谱系活跃 committed 记录中选出父代

        候选范围（不易）: 未归档 + decision=committed 的历史版本；
        首代（无候选）返回 None → base_params 回退技能默认参数。
        """
        return self._parent_selector.select(skill_id)

    def _lineage_hook(self, ctx: dict) -> None:
        """bump_version 谱系钩子（任务 1 钩子启用）：提交时写 committed 记录

        仅当 evolve_once 设置了 _round_ctx（进化提交路径）时写入；
        手动 bump_version（非进化）不写进化谱系。
        异常仅告警，绝不阻断提交（提交成功优先，谱系记录尽力而为）。
        """
        rctx = self._round_ctx
        if not rctx:
            return
        try:
            rec = EvolutionRecord.from_bump(
                ctx,
                object_type="skill",
                parent_record_id=rctx.get("parent_record_id"),
                strategy=rctx.get("strategy") or "evolution",
                trigger=rctx.get("trigger", "manual"),
                cost=rctx.get("cost"),
                decision="committed",
                change_summary=rctx.get("change_summary") or ctx.get("changelog", ""),
            )
            params = rctx.get("params")
            if params is not None:
                rec.params = params
            self._archive.append(rec)
            rctx["record_id"] = rec.record_id
            logger.info(json.dumps({
                "module_name": "offline_evolver",
                "action": "lineage.commit_recorded",
                "skill_id": ctx.get("skill_id"),
                "record_id": rec.record_id,
                "parent_record_id": rec.parent_record_id,
                "new_version": rec.new_version,
            }, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 谱系失败不阻断提交（守不易）
            logger.error(
                "[OfflineEvolver] 谱系提交记录写入失败（不阻断提交）: %s", e)

    def _record_round(self, skill_id: str, *, decision: str, reason: str = "",
                      parent_record_id: Optional[str] = None,
                      strategy: str = "", trigger: str = "manual",
                      eval_result: Optional[Dict[str, Any]] = None,
                      cost: Optional[Dict[str, Any]] = None,
                      params: Optional[Dict[str, Any]] = None,
                      parent_version: str = "",
                      new_version: str = "") -> Optional[str]:
        """直接写非提交决策记录（rejected / skipped / no_samples）到谱系

        Why 独立于 bump 钩子: 拒绝/跳过不经过版本升级，无法复用 bump_version，
        由进化器直接构造 EvolutionRecord 落库，保证"提交/拒绝/跳过均写谱系"。
        """
        try:
            rec = EvolutionRecord(
                object_type="skill",
                object_id=skill_id,
                parent_record_id=parent_record_id,
                parent_version=parent_version,
                new_version=new_version,
                strategy=strategy or "evolution",
                change_summary=reason,
                decision_reason=reason,
                decision=decision,
                trigger=trigger,
                eval_result=eval_result,
                cost=cost,
                params=params,
            )
            self._archive.append(rec)
            return rec.record_id
        except Exception as e:  # noqa: BLE001 谱系失败不阻断进化主流程
            logger.error(
                "[OfflineEvolver] 谱系 %s 记录写入失败 skill=%s: %s",
                decision, skill_id, e)
            return None

    def _write_batch_record(self, report: BatchEvolutionReport,
                            trigger: str) -> Optional[str]:
        """批量/调度运行摘要写入谱系（object_type=batch，固定 _BATCH_OBJECT_ID）"""
        try:
            rec = EvolutionRecord(
                object_type="batch",
                object_id=_BATCH_OBJECT_ID,
                strategy="evolve_batch",
                change_summary=(
                    f"批量进化: evolved={report.evolved_count} "
                    f"skipped={report.skipped_count} failed={report.failed_count} "
                    f"cost_tokens={report.cost_tokens}"),
                decision="batch_run",
                decision_reason=f"trigger={trigger}",
                trigger=trigger,
                cost={"tokens": report.cost_tokens,
                      "duration_ms": report.total_duration_ms},
            )
            self._archive.append(rec)
            return rec.record_id
        except Exception as e:  # noqa: BLE001
            logger.error("[OfflineEvolver] 批量谱系摘要写入失败: %s", e)
            return None

    def _build_score_series(self, report: BatchEvolutionReport) -> List[Dict[str, Any]]:
        """构建每代评分序列（供任务 6 审计仪表盘做进化趋势可视化）"""
        series: List[Dict[str, Any]] = []
        for idx, r in enumerate(report.results):
            if r.score is None:
                continue
            series.append({
                "skill_id": r.skill_id,
                "round": idx + 1,
                "score": r.score,
                "improvement": r.improvement,
                "decision": r.decision,
                "committed": r.committed,
            })
        return series

    # ════════════════════════════════════════════════════════════
    #  内部方法 — 候选与变异
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
                strategies: List[EvolutionStrategy],
                base_params: Optional[Dict[str, Any]] = None,
                parent_version: Optional[str] = None) -> List[Variant]:
        """根据策略列表生成变异体

        策略模式: 每个策略对应一个变异器,独立生成参数组合。
        任务 3: 变异基座从技能默认参数改为父代参数（选择压力落地）。
        """
        variants: List[Variant] = []
        base = base_params if base_params is not None else dict(skill.default_params)
        strategy_counts: Dict[str, int] = {}

        for strategy in strategies:
            try:
                t_strat = time.time()
                new_params = self._apply_strategy(skill, strategy, base)
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
                    parent_version=parent_version or skill.version,
                    metrics=self._predict_metrics(skill, new_params),
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
            "base_params_count": len(base),
            "strategies_requested": len(strategies),
            "variants_generated": len(variants),
            "strategy_counts": strategy_counts,
        }, ensure_ascii=False))

        return variants

    def _apply_strategy(self, skill: Skill, strategy: EvolutionStrategy,
                        base_params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """执行单个变异策略

        - FINE_TUNE: 对数值参数做 ±10% 扰动
        - COMBINE: 融合 avoid_params 之外的最高分参数
        - MUTATE: 随机生成新参数值（±50%）
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
                # 从 param_stats 取最高成功率的参数 (若有)
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

    # ─── 启发式路径（已废弃，任务 3 起仅作无样本占位）───

    def _predict_metrics(self, skill: Skill,
                         params: Dict[str, Any]) -> SkillMetrics:
        """预测参数组合对应的运行指标（已废弃：仅无样本时占位使用）

        优先级:
            1. 命中 param_stats → 返回历史实际指标 (最可靠)
            2. 未命中 → 启发式估算 (基于参数偏离基线的程度)
        """
        matched = self._lookup_param_stats(skill, params)
        if matched is not None:
            return matched
        return self._heuristic_predict(skill, params)

    def _lookup_param_stats(self, skill: Skill,
                            params: Dict[str, Any]) -> Optional[SkillMetrics]:
        """从 param_stats 中查找匹配的参数组合的实际指标"""
        try:
            key_str = json.dumps(params, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            return None
        key = hashlib.md5(key_str.encode("utf-8")).hexdigest()[:8]
        stat = skill.metrics.param_stats.get(key)
        if stat is None:
            return None
        total = stat.get("success", 0) + stat.get("failure", 0)
        if total == 0:
            return None
        success_rate = stat.get("success", 0) / total
        avg_latency = stat.get("total_latency_ms", 0.0) / total
        return SkillMetrics(
            usage_count=total,
            success_count=stat.get("success", 0),
            failure_count=stat.get("failure", 0),
            success_rate=success_rate,
            avg_latency_ms=avg_latency,
            p95_latency_ms=avg_latency * 1.5,
        )

    def _heuristic_predict(self, skill: Skill,
                           params: Dict[str, Any]) -> SkillMetrics:
        """启发式估算（已废弃，任务 3 起仅无样本时占位）

        简化模型:
            - 小幅偏离 (<15%): 模拟探索更优解 → 成功率小幅提升
            - 中幅偏离 (15-40%): 成功率持平,延迟略增
            - 大幅偏离 (>40%): 成功率下降,延迟上升 (风险探索)
        """
        base = skill.metrics
        base_params = skill.default_params

        deviations: List[float] = []
        for key, base_val in base_params.items():
            new_val = params.get(key, base_val)
            if isinstance(base_val, (int, float)) and isinstance(new_val, (int, float)):
                if base_val != 0:
                    deviations.append(abs(new_val - base_val) / abs(base_val))

        avg_deviation = sum(deviations) / len(deviations) if deviations else 0.0

        if avg_deviation < 0.15:
            success_delta = 0.05 * (1 - avg_deviation / 0.15)
            latency_factor = 1.0 - 0.05 * (1 - avg_deviation / 0.15)
        elif avg_deviation < 0.4:
            success_delta = 0.0
            latency_factor = 1.0 + 0.05 * avg_deviation
        else:
            success_delta = -0.1 * (avg_deviation - 0.4)
            latency_factor = 1.0 + 0.1 * avg_deviation

        new_success_rate = max(0.0, min(1.0, base.success_rate + success_delta))
        new_latency = max(100.0, base.avg_latency_ms * latency_factor)

        return SkillMetrics(
            usage_count=base.usage_count,
            success_count=int(base.usage_count * new_success_rate),
            failure_count=base.usage_count - int(base.usage_count * new_success_rate),
            success_rate=new_success_rate,
            avg_latency_ms=new_latency,
            p95_latency_ms=new_latency * 1.5,
        )

    def _compute_objectives(self, variant: Variant) -> Dict[str, float]:
        """计算多目标值 (用于帕累托支配判断)

        三个目标 (均为越大越好):
            - success_rate: 成功率
            - neg_latency: 负延迟 (取负,使延迟越小越好)
            - satisfaction: 满意度
        """
        m = variant.metrics
        if m is None:
            return {"success_rate": 0.0, "neg_latency": 0.0, "satisfaction": 0.0}
        return {
            "success_rate": m.success_rate,
            "neg_latency": -m.avg_latency_ms,
            "satisfaction": m.success_rate,
        }

    def _evaluate(self, variant: Variant) -> float:
        """评估变异体综合评分（已废弃：仅启发式路径占位用）

        简化加权求和: score = 0.5*success_rate + 0.3*latency_norm + 0.2*satisfaction
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

    # ════════════════════════════════════════════════════════════
    #  内部方法 — 帕累托与提交
    # ════════════════════════════════════════════════════════════

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

        复用 SkillEnhancer.bump_version 做 patch 版本升级，
        并更新 default_params。
        bump_version 会触发谱系钩子（_lineage_hook）写入 committed 记录。
        """
        try:
            skill = self._store.get(variant.skill_id)
            skill.default_params = dict(variant.params)
            bump = self._enhancer.bump_version(
                variant.skill_id, "patch",
                changelog=f"离线进化: strategy={variant.strategy.value}, "
                          f"improvement={variant.score}",
                eval_result=variant.eval_result,
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