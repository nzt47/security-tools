"""学习有效性度量体系 — 7 项学习 KPI 聚合（TASK-03 / 任务2 数据源补齐与触发监控）

KPI 口径（详见 docs/zh/智能体学习机制重构计划/变更说明/TASK-03_变更说明.md）：
1. token 复用率       = 命中 workflow/skill 节省 token / 期间总 token（节省 + 实际消耗）
2. Skill 命中率        = skill 命中次数 / 语义层查询次数
3. 工作流命中率         = workflow 命中次数 / 交互总数
4. 分类型失败率         = 按 task_type 统计失败占比（任务2：接 orchestrator/feedback 生产调用方；
                         judged_complexity 为任务7 复杂度维度预留的扩展键）
5. 反馈均分趋势         = 近 7 日滑动窗口 feedback.rating 均值（含逐日趋势）
6. 沉淀增量             = 新增 Skill/工作流/经验/知识卡片/反思 数量
7. 进化采纳率           = 采纳变异体数 / 候选变异体数（任务2：定义最小候选基数口径——
                         周候选数 < N（默认 5）时该周标记 insufficient_data，不参与"连续 4 周"统计）

任务2 新增（纯增量，不改既有聚合口径与 get_snapshot() 既有字段）：
- record_task_result 增 judged_complexity 扩展键（复杂度维度失败率，任务7 预留）；
- 日粒度事件镜像 _daily_events：周级滚动统计（get_weekly_kpis）与触发条件判定
  （evaluate_trigger_conditions，报告 TASK-08 §3.3/§5.2 五条触发条件逐条可计算）的数据源，
  与 SQLite 持久化（lm_daily_agg）同源同形；持久化默认关闭时内存镜像仍工作；
- KPI#7 最小候选基数：learning.metrics.min_candidates（默认 5），
  get_snapshot() 的 evolution_adoption_rate 增 insufficient_data / min_candidates 扩展字段。

任务7 新增（纯增量，向后兼容；复杂度判定源统一后 judged_complexity 随路由元数据进入聚合）：
- record_task_result 的 judged_complexity 扩展键升级为 task_type × complexity 双维度：
  get_snapshot() 增 failure_rate_by_task_type_complexity（嵌套 task_type → complexity →
  {total, failed, rate}），get_weekly_kpis() 周行同口径；不改变 task_type 既有聚合。
- 单维度复杂度桶（_complexity_results / complexity_failure_rate，任务2 预留）保留，
  与双维度桶并列（课程难度自适应策略读双维度口径，见 agent/learning/curriculum.py）。

任务5 新增（纯增量，双假设判别的数据源；**绝不触碰 KPI#7 既有口径**）：
- record_judge_result 记录 Judge dry-run 通道逐候选结果（rule_verdict / judge_verdict /
  disagreement / judge_status / tokens_used），独立于 KPI#7（不写 _evolution_candidates/
  _evolution_adopted，零干预采纳决策）；
- get_snapshot() 增 judge_dryrun 扩展节（judge_disagreement_rate / judge_implied_adoption_rate
  等，见任务5 判别报告口径）；get_weekly_kpis() 每行增 judge 扩展节（周级判别数据源）；
- get_judge_dryrun_stats()：判别报告专用只读聚合（样本量/分歧率/两通道采纳率/token 成本）。

【不易】埋点为纯增量可观测层：不改变任何业务行为；所有 record_* 内部 try/except，
        埋点异常绝不影响主链路（验收：埋点全部挂掉时主链路零影响）。
【变易】collector 可注入（默认系统全局 MetricsCollector），便于测试隔离与扩展；
        指标名统一 learning.* 前缀（snake_case，避免与 business_metrics 冲突）。
【简易】本地聚合（线程安全 RLock）+ 透出双通道：计数器写 MetricsCollector，
        get_snapshot() 提供只读聚合视图（/api/learning/metrics 消费）。
"""

import atexit
import logging
import os
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from agent.monitoring.metrics import get_metrics_collector

logger = logging.getLogger(__name__)

# 反馈/每日统计内存保留窗口（天）——超出窗口的旧数据被裁剪，防内存无界增长
_RETENTION_DAYS = 32

# 周级滚动统计内存保留窗口（天）——覆盖"连续 4 周"判定（28 天）+ 环比余量 + 灰度观察期
_WEEKLY_RETENTION_DAYS = 182

# ── 任务2：KPI#7 最小候选基数与触发条件判定参数（报告 §3.3/§5.2 阈值）──
# 优先级：环境变量 LEARNING_METRICS_MIN_CANDIDATES > config.yaml learning.metrics.min_candidates
#          > 此处硬编码默认值（项目既有三层优先级约定）
_DEFAULT_MIN_CANDIDATES = 5        # 周候选数低于该值时该周采纳率标记 insufficient_data
_TRIGGER_WINDOW_WEEKS = 4          # "连续 4 周"判定窗口（含当前进行周）
_TRIGGER_EVOLUTION_RATE = 0.05     # KPI#7 采纳率 <5%（引入 LLM-as-Judge / L3 前置）
_TRIGGER_WORKFLOW_RATE = 0.10      # KPI#3 工作流命中率 <10%（Solver 路径增强）
_TRIGGER_FAILURE_RATE = 0.30       # KPI#4 分类型失败率 >30%（课程难度自适应）
_TRIGGER_REPLAY_COVERAGE = 0.50    # 沙箱回放覆盖率 <50%（启用沙箱回放）
_TRIGGER_ARTIFACT_STAGNATION = 0   # KPI#6 沉淀增量停滞 = 周沉淀数 == 0

# 沉淀增量支持的产物类型（KPI schema 固定枚举，未列出的归入 other）
_ARTIFACT_TYPES = ("skill", "workflow", "experience", "knowledge_card", "reflection")

# 触发条件 ID 与能力映射（报告 §5.2 远期能力触发条件表；顺序即报告顺序）
_TRIGGER_CONDITIONS = (
    ("judge_intro", "引入 LLM-as-Judge（评估角色化）"),
    ("solver_enhancement", "Solver 路径增强"),
    ("course_adaptation", "启用课程难度自适应"),
    ("sandbox_replay", "启用沙箱回放"),
    ("l3_research", "启动元规则级递归自进化（L3 研究）"),
)

# config.yaml 惰性读取缓存（任务2：无显式 config 时按"环境变量 > config.yaml > 默认值"解析）
_CONFIG_YAML_CACHE: Optional[dict] = None

# ── SQLite 持久化（方案见 TASK-08 §8 口径披露；默认关闭，与纯内存行为一致）──
_PERSIST_CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS lm_daily_agg ("
    " day TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL DEFAULT '',"
    " val REAL NOT NULL DEFAULT 0, cnt INTEGER NOT NULL DEFAULT 0,"
    " PRIMARY KEY (day, kind, key))"
)
_PERSIST_UPSERT_SQL = (
    "INSERT INTO lm_daily_agg (day, kind, key, val, cnt) VALUES (?, ?, ?, ?, ?)"
    " ON CONFLICT(day, kind, key) DO UPDATE SET"
    " val = val + excluded.val, cnt = cnt + excluded.cnt"
)


def _day_ts_approx(day: str) -> float:
    """把 'YYYY-MM-DD' 转为当日 12:00 时间戳（持久化回填 feedback 用，仅用于窗口判定）"""
    try:
        return datetime.strptime(day, "%Y-%m-%d").replace(hour=12).timestamp()
    except Exception:
        return time.time()


def _safe_collector(fn_name: str, *args, **kwargs) -> None:
    """调用 MetricsCollector 的安全包装——吞掉异常不影响主链路"""
    try:
        collector = get_metrics_collector()
        fn = getattr(collector, fn_name, None)
        if fn is not None:
            fn(*args, **kwargs)
    except Exception:
        pass


def _day_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _iso_week_key(day: str) -> Optional[Tuple[str, str, str]]:
    """'YYYY-MM-DD' → ('YYYY-Www', 周一起始日, 周日结束日)；非法日期返回 None"""
    try:
        d = datetime.strptime(day, "%Y-%m-%d").date()
        iso = d.isocalendar()
        wk = "%04d-W%02d" % (iso[0], iso[1])
        start = date.fromisocalendar(iso[0], iso[1], 1)
        end = start + timedelta(days=6)
        return wk, start.isoformat(), end.isoformat()
    except Exception:
        return None


def _load_config_yaml_once() -> Optional[dict]:
    """读取仓库根 config.yaml（带缓存；任何失败返回 None，零影响）

    任务2 目的：LearningMetrics 单例经 get_singleton 创建时无调用方显式传 config，
    为让 config.yaml 的 learning.metrics.* 配置段生效，工厂在此按
    "环境变量 > config.yaml > 硬编码默认值" 解析（与 orchestrator._get_learning_saved_estimate
    同款"延迟读文件 + 缓存"模式；文件读取失败不影响主链路）。
    """
    global _CONFIG_YAML_CACHE
    if _CONFIG_YAML_CACHE is not None:
        return _CONFIG_YAML_CACHE or None
    try:
        from pathlib import Path
        import yaml as _yaml
        _path = Path(__file__).resolve().parent.parent / "config.yaml"
        if _path.exists():
            _cfg = _yaml.safe_load(_path.read_text(encoding="utf-8")) or {}
            _CONFIG_YAML_CACHE = _cfg
            return _cfg
    except Exception:
        pass
    _CONFIG_YAML_CACHE = {}
    return None


class LearningMetrics:
    """学习 KPI 聚合单例

    用法（生产，经 SingletonManager 获取）:
        from agent.learning_metrics import get_learning_metrics
        get_learning_metrics().record_workflow_match(hit=True, saved_tokens=1200)

    用法（测试，直接构造实例，避免单例状态跨测试污染）:
        lm = LearningMetrics(collector=DummyCollector())
    """

    def __init__(self, collector: Any = None, enabled: bool = True,
                 persistence: Optional[dict] = None,
                 min_candidates: Optional[int] = None,
                 trigger_window_weeks: Optional[int] = None,
                 replay_coverage_threshold: Optional[float] = None):
        self.enabled = bool(enabled)
        self._collector = collector  # 注入用；None 时经 _safe_collector 走全局
        # KPI#7 最小候选基数（周候选数 < 该值时该周采纳率标记 insufficient_data）
        self._min_candidates = max(1, int(min_candidates if min_candidates is not None
                                          else _DEFAULT_MIN_CANDIDATES))
        # 触发条件判定默认参数（config.yaml learning.metrics.trigger_monitoring）
        self._trigger_window_weeks = max(
            1, int(trigger_window_weeks if trigger_window_weeks is not None
                   else _TRIGGER_WINDOW_WEEKS))
        self._replay_coverage_threshold = float(
            replay_coverage_threshold if replay_coverage_threshold is not None
            else _TRIGGER_REPLAY_COVERAGE)
        self._lock = threading.RLock()

        # ── SQLite 持久化（可选，默认关闭；I/O 全在锁外，失败自动降级）──
        # 启用块须在内存字段初始化之后执行（_load_from_db 回填依赖这些字段）
        self._persistence: Optional[dict] = None
        self._pending: Dict[Tuple[str, str, str], List[float]] = {}  # (day,kind,key)->[val,cnt]
        self._db_lock = threading.Lock()

        # ── token 复用率 ──
        self._tokens_saved = 0
        self._tokens_total = 0
        # ── Skill / 工作流命中率 ──
        self._semantic_queries = 0
        self._skill_hits = 0
        self._total_interactions = 0
        self._workflow_queries = 0
        self._workflow_hits = 0
        # ── 分类型失败率 ──
        self._task_results: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"total": 0, "failed": 0})
        # ── 复杂度维度失败率（任务2 扩展，KPI#4 复杂度维度预留；不影响 task_type 口径）──
        self._complexity_results: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"total": 0, "failed": 0})
        # ── task_type × complexity 双维度失败率（任务7 扩展，KPI#4 双维度口径；
        #    键 (task_type, complexity)；课程难度自适应策略消费，见 curriculum.py）──
        self._task_cx_results: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(
            lambda: {"total": 0, "failed": 0})
        # ── 日粒度事件镜像（任务2：周级滚动统计/触发判定的数据源）──
        # (day, kind, key) -> [val, cnt]；与 SQLite lm_daily_agg 同形同源；
        # 持久化开启时二者同步写，默认关闭时内存镜像独立工作（周级统计不依赖持久化）
        self._daily_events: Dict[Tuple[str, str, str], List[float]] = {}
        # ── 反馈均分趋势（含逐日桶，供 7 日趋势）──
        self._feedback: List[Tuple[float, int]] = []          # [(ts, rating)]
        self._daily_stats: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"interactions": 0, "feedback_ratings": []})
        # ── 沉淀增量 ──
        self._artifacts: Dict[str, int] = defaultdict(int)
        # ── 进化采纳率 ──
        self._evolution_candidates = 0
        self._evolution_adopted = 0
        # ── 任务5：Judge dry-run 双通道判别（独立于 KPI#7，零干预采纳决策）──
        # 与日粒度事件镜像 _daily_events 同源同形；周级/快照聚合据此计算
        self._judge_candidates = 0          # 进入 dry-run 通道的候选数
        self._judge_judged = 0              # Judge 通道产出有效判定的候选数
        self._judge_rule_adopted = 0        # 其中规则通道判 accept 数
        self._judge_implied_adopted = 0     # 其中 Judge 判 accept 数（若按 Judge 判定会采纳数）
        self._judge_disagreements = 0       # 两通道分歧数
        self._judge_budget_blocked = 0      # 预算熔断跳过数
        self._judge_tokens_used = 0         # 预估 token 消耗（字符/4 启发式）

        # ── 持久化启用（字段就绪后建表 + 回填；失败自动降级为内存聚合）──
        if persistence and persistence.get("enabled"):
            try:
                self._db_path = str(persistence.get("path") or "data/learning_metrics.db")
                self._flush_batch = max(1, int(persistence.get("flush_batch_size", 200)))
                self._retention_days = max(1, int(persistence.get("retention_days", 90)))
                self._init_db()  # 建表 + 回填；失败抛给下方 except 降级
                self._persistence = {"path": self._db_path}
                atexit.register(self.flush)
            except Exception as e:
                logger.warning("[学习度量] 持久化初始化失败，降级为内存聚合: %s", e)
                self._persistence = None

    # ════════════════════════════════════════════════════════════════
    #  埋点入口（全部内部兜底，异常不影响主链路）
    # ════════════════════════════════════════════════════════════════

    def record_interaction(self, ts: Optional[float] = None) -> None:
        """一次用户交互（工作流命中率分母；ts 供测试注入时间）"""
        if not self.enabled:
            return
        try:
            ts = ts if ts is not None else time.time()
            with self._lock:
                self._total_interactions += 1
                self._daily_stats[_day_iso(ts)]["interactions"] += 1
                self._prune_daily_stats()
                self._queue_event(_day_iso(ts), "interaction", "", 1, 1)
            self._collect("increment_counter", "learning.interactions.total")
            self._maybe_flush()
        except Exception as e:  # 埋点失败隔离
            logger.debug("[学习度量] record_interaction 失败: %s", e)

    def record_workflow_match(self, hit: bool,
                              saved_tokens: int = 0,
                              ts: Optional[float] = None) -> None:
        """工作流拦截层匹配结果（命中时 saved_tokens > 0 计入 token 复用）"""
        if not self.enabled:
            return
        try:
            ts = ts if ts is not None else time.time()
            with self._lock:
                self._workflow_queries += 1
                if hit:
                    self._workflow_hits += 1
                day = _day_iso(ts)
                self._queue_event(day, "workflow_query", "", 1, 1)
                if hit:
                    self._queue_event(day, "workflow_hit", "", 1, 1)
            self._collect("increment_counter", "learning.workflow.queries")
            if hit:
                self._collect("increment_counter", "learning.workflow.hits")
                self.record_token_reuse(saved_tokens, ts=ts)
            self._maybe_flush()
        except Exception as e:
            logger.debug("[学习度量] record_workflow_match 失败: %s", e)

    def record_semantic_query(self, hit: bool,
                              saved_tokens: int = 0,
                              ts: Optional[float] = None) -> None:
        """语义层查询结果（skill 命中率；命中时计入 token 复用）"""
        if not self.enabled:
            return
        try:
            ts = ts if ts is not None else time.time()
            with self._lock:
                self._semantic_queries += 1
                if hit:
                    self._skill_hits += 1
                day = _day_iso(ts)
                self._queue_event(day, "semantic_query", "", 1, 1)
                if hit:
                    self._queue_event(day, "semantic_hit", "", 1, 1)
            self._collect("increment_counter", "learning.semantic.queries")
            if hit:
                self._collect("increment_counter", "learning.semantic.hits")
                self.record_token_reuse(saved_tokens, ts=ts)
            self._maybe_flush()
        except Exception as e:
            logger.debug("[学习度量] record_semantic_query 失败: %s", e)

    def record_llm_tokens(self, tokens: int, ts: Optional[float] = None) -> None:
        """LLM 调用实际消耗 token（token 复用率分母）"""
        if not self.enabled or tokens is None:
            return
        try:
            tokens = max(0, int(tokens))
            ts = ts if ts is not None else time.time()
            with self._lock:
                self._tokens_total += tokens
                self._queue_event(_day_iso(ts), "token_total", "", float(tokens), 1)
            self._collect("increment_counter", "learning.token.total", value=tokens)
            self._collect("record_latency", "learning.token.per_call", float(tokens))
            self._maybe_flush()
        except Exception as e:
            logger.debug("[学习度量] record_llm_tokens 失败: %s", e)

    def record_token_reuse(self, saved_tokens: int,
                           ts: Optional[float] = None) -> None:
        """workflow/skill 命中短路节省的 token（复用率分子）

        Args:
            saved_tokens: 节省 token 数
            ts: 时间戳（任务2 起支持注入：record_workflow_match/record_semantic_query
                透传调用方 ts，保证日/周粒度归因正确；生产调用仍为当前时间）
        """
        if not self.enabled or saved_tokens is None:
            return
        try:
            saved_tokens = max(0, int(saved_tokens))
            ts = ts if ts is not None else time.time()
            with self._lock:
                self._tokens_saved += saved_tokens
                self._tokens_total += saved_tokens  # 计入期间总 token（口径：节省+消耗）
                self._queue_event(_day_iso(ts), "token_saved", "", float(saved_tokens), 1)
            self._collect("increment_counter", "learning.token.saved", value=saved_tokens)
            self._maybe_flush()
        except Exception as e:
            logger.debug("[学习度量] record_token_reuse 失败: %s", e)

    def record_task_result(self, task_type: str, success: bool,
                           judged_complexity: Optional[str] = None,
                           ts: Optional[float] = None) -> None:
        """一次任务执行结果（按 task_type 分类型失败率；任务2 接 orchestrator/feedback）

        Args:
            task_type: 任务类型（取自路由/任务元数据；None/空归 "unknown"）
            success: 是否成功
            judged_complexity: 复杂度扩展键（TRIVIAL/SIMPLE/NORMAL/COMPLEX，任务7 复杂度
                维度预留；为 None 时零影响，不改变 task_type 既有聚合口径）
            ts: 时间戳（测试注入用）
        """
        if not self.enabled:
            return
        try:
            key = task_type or "unknown"
            ts = ts if ts is not None else time.time()
            day = _day_iso(ts)
            cx_key = str(judged_complexity).strip().upper() if judged_complexity else ""
            # 任务7：MODERATE 为 enhanced_planner 兼容别名，统一归一到 canonical NORMAL
            # （与 complexity_classifier.normalize_level 口径一致）
            if cx_key == "MODERATE":
                cx_key = "NORMAL"
            with self._lock:
                bucket = self._task_results[key]
                bucket["total"] += 1
                if not success:
                    bucket["failed"] += 1
                # 任务2 扩展：复杂度维度（独立桶，不并入 task_type 聚合）
                if cx_key:
                    cx = self._complexity_results[cx_key]
                    cx["total"] += 1
                    if not success:
                        cx["failed"] += 1
                # 任务7 扩展：task_type × complexity 双维度桶（KPI#4 双维度口径；
                # 键序 (task_type, complexity)；事件 key 用 '::' 拼接，周级聚合拆回）
                if cx_key:
                    cxk = (key, cx_key)
                    cx_bucket = self._task_cx_results[cxk]
                    cx_bucket["total"] += 1
                    if not success:
                        cx_bucket["failed"] += 1
                    self._queue_event(day, "task_cx_total", f"{key}::{cx_key}", 1, 1)
                    if not success:
                        self._queue_event(day, "task_cx_failed", f"{key}::{cx_key}", 1, 1)
                self._queue_event(day, "task_total", key, 1, 1)
                if not success:
                    self._queue_event(day, "task_failed", key, 1, 1)
                if cx_key:
                    self._queue_event(day, "task_complexity_total", cx_key, 1, 1)
                    if not success:
                        self._queue_event(day, "task_complexity_failed", cx_key, 1, 1)
            self._collect("increment_counter", f"learning.task.{key}.total")
            if not success:
                self._collect("increment_counter", f"learning.task.{key}.failed")
            self._maybe_flush()
        except Exception as e:
            logger.debug("[学习度量] record_task_result 失败: %s", e)

    def record_feedback(self, rating: int, ts: Optional[float] = None) -> None:
        """用户反馈评分（反馈均分趋势数据源；rating ∈ [0,5]）"""
        if not self.enabled:
            return
        try:
            rating = max(0, min(5, int(rating)))
            ts = ts if ts is not None else time.time()
            with self._lock:
                self._feedback.append((ts, rating))
                self._daily_stats[_day_iso(ts)]["feedback_ratings"].append(rating)
                self._prune_feedback()
                self._prune_daily_stats()
                self._queue_event(_day_iso(ts), "feedback", "", float(rating), 1)
            self._collect("increment_counter", "learning.feedback.total")
            self._collect("record_latency", "learning.feedback.rating", float(rating))
            self._maybe_flush()
        except Exception as e:
            logger.debug("[学习度量] record_feedback 失败: %s", e)

    def record_artifact(self, artifact_type: str,
                        ts: Optional[float] = None) -> None:
        """沉淀增量：新增 Skill/工作流/经验/知识卡片/反思 产物"""
        if not self.enabled:
            return
        try:
            key = artifact_type if artifact_type in _ARTIFACT_TYPES else "other"
            ts = ts if ts is not None else time.time()
            with self._lock:
                self._artifacts[key] += 1
                self._queue_event(_day_iso(ts), "artifact", key, 1, 1)
            self._collect("increment_counter", f"learning.artifacts.{key}")
            self._maybe_flush()
        except Exception as e:
            logger.debug("[学习度量] record_artifact 失败: %s", e)

    def record_evolution_candidate(self, adopted: bool,
                                   ts: Optional[float] = None) -> None:
        """进化采纳率：一次变异体候选决策（TASK-05 offline_evolver 接入后产生）"""
        if not self.enabled:
            return
        try:
            ts = ts if ts is not None else time.time()
            with self._lock:
                self._evolution_candidates += 1
                if adopted:
                    self._evolution_adopted += 1
                day = _day_iso(ts)
                self._queue_event(day, "evolution_candidate", "", 1, 1)
                if adopted:
                    self._queue_event(day, "evolution_adopted", "", 1, 1)
            self._collect("increment_counter", "learning.evolution.candidates")
            if adopted:
                self._collect("increment_counter", "learning.evolution.adopted")
            self._maybe_flush()
        except Exception as e:
            logger.debug("[学习度量] record_evolution_candidate 失败: %s", e)

    def record_judge_result(self,
                            rule_verdict: Optional[str] = None,
                            judge_verdict: Optional[str] = None,
                            disagreement: bool = False,
                            judge_status: str = "judged",
                            tokens_used: int = 0,
                            ts: Optional[float] = None) -> None:
        """任务5：Judge dry-run 通道逐候选记录（双假设判别数据源，零干预）

        语义（与 KPI#7 严格分离）:
            - 本方法**不写** _evolution_candidates/_evolution_adopted，不触发任何
              record_evolution_candidate 等价调用 → Judge 通道判定不影响采纳决策；
            - 只有 judge_status="judged"（Judge 通道产出有效判定）的候选计入
              分歧率/采纳率分子分母；skipped/budget_blocked/no_llm_client 等
              只计 candidates 与对应状态计数，绝不伪造判定；
            - tokens_used 为预估消耗（字符/4 启发式，与学习预算记账同口径），
              仅 >0 时计入 judge_tokens（token 成本核算数据源）。

        Args:
            rule_verdict: 规则通道判定（"accept"/"reject"，候选无记录时可为 None）
            judge_verdict: Judge 通道判定（"accept"/"reject"；未判定时为 None）
            disagreement: 两通道是否分歧（仅 judged 候选参与）
            judge_status: judged / skipped / budget_blocked / budget_not_enforce /
                          no_llm_client
            tokens_used: 本次预估 token 消耗
            ts: 时间戳（测试注入用）
        """
        if not self.enabled:
            return
        try:
            ts = ts if ts is not None else time.time()
            day = _day_iso(ts)
            judged = judge_status == "judged"
            rule_acc = bool(judged and rule_verdict == "accept")
            judge_acc = bool(judged and judge_verdict == "accept")
            with self._lock:
                self._judge_candidates += 1
                self._queue_event(day, "judge_candidate", "", 1, 1)
                if judged:
                    self._judge_judged += 1
                    self._queue_event(day, "judge_judged", "", 1, 1)
                    if rule_acc:
                        self._judge_rule_adopted += 1
                        self._queue_event(day, "judge_rule_adopt", "", 1, 1)
                    if judge_acc:
                        self._judge_implied_adopted += 1
                        self._queue_event(day, "judge_implied_adopt", "", 1, 1)
                    if disagreement:
                        self._judge_disagreements += 1
                        self._queue_event(day, "judge_disagreement", "", 1, 1)
                elif judge_status == "budget_blocked":
                    self._judge_budget_blocked += 1
                    self._queue_event(day, "judge_budget_blocked", "", 1, 1)
                tokens = max(0, int(tokens_used or 0))
                if tokens > 0:
                    self._judge_tokens_used += tokens
                    self._queue_event(day, "judge_tokens", "", float(tokens), 1)
            self._collect("increment_counter", "learning.judge.candidates")
            if judged:
                self._collect("increment_counter", "learning.judge.judged")
            if disagreement:
                self._collect("increment_counter", "learning.judge.disagreements")
            if judge_acc:
                self._collect("increment_counter", "learning.judge.implied_adopted")
            self._maybe_flush()
        except Exception as e:
            logger.debug("[学习度量] record_judge_result 失败: %s", e)

    # ════════════════════════════════════════════════════════════════
    #  只读聚合视图
    # ════════════════════════════════════════════════════════════════

    def get_snapshot(self, days: int = 7) -> Dict[str, Any]:
        """7 项 KPI 只读聚合快照（/api/learning/metrics 消费）

        含近 N 日趋势（逐日交互数与反馈均分）与 TASK-02 learning.eval.* 聚合
        （分类型失败率中的评估维度数据源）。纯只读：不触发任何写操作。
        """
        now = time.time()
        with self._lock:
            total_interactions = self._total_interactions
            workflow_queries = self._workflow_queries
            workflow_hits = self._workflow_hits
            semantic_queries = self._semantic_queries
            skill_hits = self._skill_hits
            tokens_saved = self._tokens_saved
            tokens_total = self._tokens_total
            task_results = {k: dict(v) for k, v in self._task_results.items()}
            # 任务7：task_type × complexity 双维度快照（键 (task_type, complexity) → 桶）
            task_cx_results = {
                (k[0], k[1]): dict(v) for k, v in self._task_cx_results.items()
            }
            feedback = list(self._feedback)
            artifacts = dict(self._artifacts)
            evolution_candidates = self._evolution_candidates
            evolution_adopted = self._evolution_adopted
            daily_stats = {k: {
                "interactions": v["interactions"],
                "feedback_ratings": list(v["feedback_ratings"]),
            } for k, v in self._daily_stats.items()}

        cutoff = now - days * 86400
        window_fb = [(ts, r) for ts, r in feedback if ts >= cutoff]
        prev_fb = [(ts, r) for ts, r in feedback if cutoff - days * 86400 <= ts < cutoff]

        def _avg(vals: List[int]) -> float:
            return round(sum(vals) / len(vals), 2) if vals else 0.0

        # 近 N 日趋势（按日期升序；无数据日期不输出）
        trend = []
        for d in sorted(daily_stats):
            if d < _day_iso(cutoff):
                continue
            ratings = daily_stats[d]["feedback_ratings"]
            trend.append({
                "date": d,
                "interactions": daily_stats[d]["interactions"],
                "feedback_count": len(ratings),
                "feedback_avg": _avg(ratings) if ratings else None,
            })

        # 任务7：task_type × complexity 双维度（task_type → complexity → 桶）
        _cx_by_task: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for (t, c), b in task_cx_results.items():
            _cx_by_task.setdefault(t, {})[c] = b

        return {
            "generated_at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
            "days": days,
            "kpis": {
                "token_reuse_rate": {
                    "saved_tokens": tokens_saved,
                    "total_tokens": tokens_total,
                    "rate": round(tokens_saved / tokens_total, 4) if tokens_total else 0.0,
                },
                "skill_hit_rate": {
                    "queries": semantic_queries,
                    "hits": skill_hits,
                    "rate": round(skill_hits / semantic_queries, 4) if semantic_queries else 0.0,
                },
                "workflow_hit_rate": {
                    "interactions": total_interactions,
                    "hits": workflow_hits,
                    "rate": round(workflow_hits / total_interactions, 4) if total_interactions else 0.0,
                },
                "failure_rate_by_task_type": {
                    t: {
                        "total": b["total"],
                        "failed": b["failed"],
                        "rate": round(b["failed"] / b["total"], 4) if b["total"] else 0.0,
                    }
                    for t, b in sorted(task_results.items())
                },
                # 任务7：KPI#4 task_type × complexity 双维度（课程难度自适应数据源；
                # 向后兼容：不改变 failure_rate_by_task_type 既有结构）
                "failure_rate_by_task_type_complexity": {
                    t: {
                        c: {
                            "total": b["total"],
                            "failed": b["failed"],
                            "rate": round(b["failed"] / b["total"], 4)
                            if b["total"] else 0.0,
                        }
                        for c, b in sorted(_cx_by_task.get(t, {}).items())
                    }
                    for t in sorted(_cx_by_task)
                },
                "feedback_rating_trend": {
                    "count": len(window_fb),
                    "window_days": days,
                    "current_avg": _avg([r for _, r in window_fb]),
                    "previous_avg": _avg([r for _, r in prev_fb]),
                    "by_day": trend,
                },
                "artifact_delta": {
                    t: artifacts.get(t, 0) for t in _ARTIFACT_TYPES
                } | {"other": artifacts.get("other", 0)},
                "evolution_adoption_rate": {
                    "candidates": evolution_candidates,
                    "adopted": evolution_adopted,
                    "rate": round(evolution_adopted / evolution_candidates, 4)
                    if evolution_candidates else 0.0,
                    # 任务2 扩展字段（向后兼容；周级 insufficient_data 语义见
                    # get_weekly_kpis / evaluate_trigger_conditions）
                    "insufficient_data": evolution_candidates < self._min_candidates,
                    "min_candidates": self._min_candidates,
                },
            },
            "evaluation": self._get_eval_stats(),
            "judge_dryrun": self._get_judge_dryrun_snapshot(),
            "trend_7d": trend,
        }

    def _get_judge_dryrun_snapshot(self) -> Dict[str, Any]:
        """任务5：Judge dry-run 双通道判别只读聚合（快照扩展节 + 判别报告数据源）

        口径（任务5 判别规则预设，写入判别报告 §3）:
            - 有效判定数（judged）= 两通道均产出判定的候选（Judge 通道
              judged 状态）；skipped/budget_blocked 只计 candidates 与状态计数；
            - judge_disagreement_rate = 分歧数 / judged；
            - rule_adoption_rate = 规则通道判 accept / judged；
            - judge_implied_adoption_rate = Judge 判 accept / judged
              （若按 Judge 判定会采纳的比例）；
            - adoption_rate_delta_pp = (implied - rule) × 100；
            - insufficient_data = judged < min_candidates（判别结论不成立）。
        """
        with self._lock:
            cands = self._judge_candidates
            judged = self._judge_judged
            rule_adopted = self._judge_rule_adopted
            implied = self._judge_implied_adopted
            disagreements = self._judge_disagreements
            blocked = self._judge_budget_blocked
            tokens = self._judge_tokens_used

        def _rate(n: int, d: int) -> Optional[float]:
            return round(n / d, 4) if d else None

        rule_rate = _rate(rule_adopted, judged)
        implied_rate = _rate(implied, judged)
        delta_pp = None
        if judged and rule_rate is not None and implied_rate is not None:
            delta_pp = round((implied_rate - rule_rate) * 100.0, 2)
        return {
            "candidates": cands,
            "judged": judged,
            "rule_adopted": rule_adopted,
            "implied_adopted": implied,
            "disagreements": disagreements,
            "budget_blocked": blocked,
            "tokens_used": tokens,
            "judge_disagreement_rate": _rate(disagreements, judged),
            "rule_adoption_rate": rule_rate,
            "judge_implied_adoption_rate": implied_rate,
            "adoption_rate_delta_pp": delta_pp,
            "insufficient_data": judged < self._min_candidates,
            "min_candidates": self._min_candidates,
        }

    def get_judge_dryrun_stats(self) -> Dict[str, Any]:
        """任务5：判别报告专用只读聚合（别名，含生成时间；/api/learning/metrics 消费）"""
        return self._get_judge_dryrun_snapshot()

    def to_dict(self) -> Dict[str, Any]:
        """别名：get_snapshot()"""
        return self.get_snapshot()

    # ════════════════════════════════════════════════════════════════
    #  任务2：周级滚动统计 + 触发条件判定（报告 §3.3/§5.2 五条触发条件）
    # ════════════════════════════════════════════════════════════════

    def get_weekly_kpis(self, weeks: int = 8,
                        min_candidates: Optional[int] = None) -> List[Dict[str, Any]]:
        """周级滚动 KPI（触发监控查询层数据源）

        - 以 ISO 周（周一起始）为桶，从日粒度事件镜像 _daily_events 聚合；
        - 覆盖 7 项 KPI 的周值 + KPI#7 候选基数标记（insufficient_data）；
        - 口径与 get_snapshot() 一致：token 复用率 total = 消耗 + 节省；
          workflow 命中率分母 = 交互总数；周内无数据周不输出（返回行 < weeks 时
          表明系统运行时长不足，触发判定按 available 周处理）；
        - 纯只读（仅裁剪过期的内存镜像，无任何写操作）。

        Args:
            weeks: 返回最近 weeks 个有数据周（升序，最旧在前）
            min_candidates: 覆盖 KPI#7 最小候选基数（默认实例配置/5）
        """
        weeks = max(1, int(weeks))
        min_candidates = max(1, int(min_candidates if min_candidates is not None
                                    else self._min_candidates))
        with self._lock:
            events = {k: list(v) for k, v in self._daily_events.items()}
            self._prune_daily_events()

        # ── 按 ISO 周分桶 ──
        buckets: Dict[str, Dict[str, Any]] = {}
        for (day, kind, key), (val, cnt) in events.items():
            wk_info = _iso_week_key(day)
            if wk_info is None:
                continue
            wk, _start, _end = wk_info
            b = buckets.setdefault(wk, {
                "interactions": 0,
                "workflow_queries": 0, "workflow_hits": 0,
                "semantic_queries": 0, "semantic_hits": 0,
                "tokens_total": 0.0, "tokens_saved": 0.0,
                "task_total": defaultdict(int), "task_failed": defaultdict(int),
                "cx_total": defaultdict(int), "cx_failed": defaultdict(int),
                # 任务7：task_type × complexity 双维度（事件 key 'task_type::complexity'）
                "cx_task_total": defaultdict(int), "cx_task_failed": defaultdict(int),
                "feedback_count": 0, "feedback_sum": 0.0,
                "artifact_count": 0,
                "evolution_candidates": 0, "evolution_adopted": 0,
                # 任务5：Judge dry-run（周级判别数据源；独立于 evolution 口径）
                "judge_candidates": 0, "judge_judged": 0,
                "judge_rule_adopt": 0, "judge_implied_adopt": 0,
                "judge_disagreements": 0, "judge_budget_blocked": 0,
                "judge_tokens": 0.0,
            })
            if kind == "interaction":
                b["interactions"] += cnt
            elif kind == "workflow_query":
                b["workflow_queries"] += cnt
            elif kind == "workflow_hit":
                b["workflow_hits"] += cnt
            elif kind == "semantic_query":
                b["semantic_queries"] += cnt
            elif kind == "semantic_hit":
                b["semantic_hits"] += cnt
            elif kind == "token_total":
                b["tokens_total"] += float(val)
            elif kind == "token_saved":
                b["tokens_saved"] += float(val)
            elif kind == "task_total":
                b["task_total"][key] += cnt
            elif kind == "task_failed":
                b["task_failed"][key] += cnt
            elif kind == "task_complexity_total":
                b["cx_total"][key] += cnt
            elif kind == "task_complexity_failed":
                b["cx_failed"][key] += cnt
            elif kind == "task_cx_total":
                b["cx_task_total"][key] += cnt
            elif kind == "task_cx_failed":
                b["cx_task_failed"][key] += cnt
            elif kind == "feedback":
                b["feedback_count"] += cnt
                b["feedback_sum"] += float(val)
            elif kind == "artifact":
                b["artifact_count"] += cnt
            elif kind == "evolution_candidate":
                b["evolution_candidates"] += cnt
            elif kind == "evolution_adopted":
                b["evolution_adopted"] += cnt
            elif kind == "judge_candidate":
                b["judge_candidates"] += cnt
            elif kind == "judge_judged":
                b["judge_judged"] += cnt
            elif kind == "judge_rule_adopt":
                b["judge_rule_adopt"] += cnt
            elif kind == "judge_implied_adopt":
                b["judge_implied_adopt"] += cnt
            elif kind == "judge_disagreement":
                b["judge_disagreements"] += cnt
            elif kind == "judge_budget_blocked":
                b["judge_budget_blocked"] += cnt
            elif kind == "judge_tokens":
                b["judge_tokens"] += float(val)

        def _rate(num: int, den: int) -> float:
            return round(num / den, 4) if den else 0.0

        rows: List[Dict[str, Any]] = []
        for wk in sorted(buckets.keys())[-weeks:]:
            b = buckets[wk]
            # ISO 周标签 'YYYY-Www' → 周一起始/周日结束（_iso_week_key 只接受日期串）
            try:
                _start_d = date.fromisocalendar(int(wk[:4]), int(wk[6:8]), 1)
                start_iso = _start_d.isoformat()
                end_iso = (_start_d + timedelta(days=6)).isoformat()
            except Exception:
                start_iso = end_iso = ""
            t_total = int(b["tokens_total"]) + int(b["tokens_saved"])  # 口径与快照一致
            task_rows = {
                t: {"total": b["task_total"][t], "failed": b["task_failed"][t],
                    "rate": _rate(b["task_failed"][t], b["task_total"][t])}
                for t in sorted(set(b["task_total"]) | set(b["task_failed"]))
            }
            cx_rows = {
                c: {"total": b["cx_total"][c], "failed": b["cx_failed"][c],
                    "rate": _rate(b["cx_failed"][c], b["cx_total"][c])}
                for c in sorted(set(b["cx_total"]) | set(b["cx_failed"]))
            }
            # 任务7：task_type × complexity 双维度（周级口径与快照一致；
            # 事件 key 'task_type::complexity' 拆回嵌套结构）
            cx_task_rows: Dict[str, Dict[str, Any]] = {}
            for _k in sorted(set(b["cx_task_total"]) | set(b["cx_task_failed"])):
                _t, _sep, _c = _k.partition("::")
                if not _sep or not _c:
                    continue
                cx_task_rows.setdefault(_t, {})[_c] = {
                    "total": b["cx_task_total"][_k],
                    "failed": b["cx_task_failed"][_k],
                    "rate": _rate(b["cx_task_failed"][_k], b["cx_task_total"][_k]),
                }
            cands = b["evolution_candidates"]
            adopted = b["evolution_adopted"]
            # 任务5：Judge dry-run 周级判别节（口径与快照 _get_judge_dryrun_snapshot 一致）
            j_judged = b["judge_judged"]
            j_rule = b["judge_rule_adopt"]
            j_implied = b["judge_implied_adopt"]
            j_dis = b["judge_disagreements"]
            j_rate = _rate(j_dis, j_judged)
            j_rule_rate = _rate(j_rule, j_judged)
            j_implied_rate = _rate(j_implied, j_judged)
            j_delta = None
            if j_judged and j_rule_rate is not None and j_implied_rate is not None:
                j_delta = round((j_implied_rate - j_rule_rate) * 100.0, 2)
            rows.append({
                "week": wk,
                "start": start_iso,
                "end": end_iso,
                "interactions": b["interactions"],
                "token_reuse_rate": {
                    "saved": int(b["tokens_saved"]),
                    "total": t_total,
                    "rate": _rate(int(b["tokens_saved"]), t_total),
                },
                "skill_hit_rate": {
                    "queries": b["semantic_queries"],
                    "hits": b["semantic_hits"],
                    "rate": _rate(b["semantic_hits"], b["semantic_queries"]),
                },
                "workflow_hit_rate": {
                    "interactions": b["interactions"],
                    "hits": b["workflow_hits"],
                    "rate": _rate(b["workflow_hits"], b["interactions"]),
                },
                "failure_rate_by_task_type": task_rows,
                "complexity_failure_rate": cx_rows,
                "failure_rate_by_task_type_complexity": cx_task_rows,
                "feedback": {
                    "count": b["feedback_count"],
                    "avg": round(b["feedback_sum"] / b["feedback_count"], 2)
                    if b["feedback_count"] else None,
                },
                "artifact_delta": {"count": b["artifact_count"]},
                "evolution": {
                    "candidates": cands,
                    "adopted": adopted,
                    "rate": _rate(adopted, cands),
                    "insufficient_data": cands < min_candidates,
                },
                "judge": {
                    "candidates": b["judge_candidates"],
                    "judged": j_judged,
                    "rule_adopted": j_rule,
                    "implied_adopted": j_implied,
                    "disagreements": j_dis,
                    "budget_blocked": b["judge_budget_blocked"],
                    "tokens_used": int(b["judge_tokens"]),
                    "judge_disagreement_rate": j_rate,
                    "rule_adoption_rate": j_rule_rate,
                    "judge_implied_adoption_rate": j_implied_rate,
                    "adoption_rate_delta_pp": j_delta,
                    "insufficient_data": j_judged < min_candidates,
                },
            })
        return rows

    def evaluate_trigger_conditions(
        self,
        weeks: Optional[int] = None,
        min_candidates: Optional[int] = None,
        replay_coverage: Optional[float] = None,
        audit_ok: Optional[bool] = None,
        g1_g5_ready: Optional[bool] = None,
        decision_approval: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """报告 §3.3/§5.2 五条触发条件逐条计算（任务2 触发监控查询层）

        判定窗口 = 最近 N 个有数据 ISO 周（含当前进行周；N 默认 4）。
        "连续 N 周" = 窗口内每一周均满足条件且数据可度量；任一周数据缺失
        （无交互/无任务/无候选等）→ 该条件标记 insufficient_data（不进入
        "连续 4 周"统计，也绝不判命中）。环比（KPI#1/KPI#5）取最新周 vs 前一周，
        前一周缺失 → 无法计算 → insufficient_data。

        五条触发条件（口径详见《任务2_触发条件计算说明.md》）：
        TC-1 judge_intro         KPI#7 连续 4 周 <5% 且 KPI#1 环比无提升
        TC-2 solver_enhancement  KPI#3 工作流命中率连续 4 周 <10%
        TC-3 course_adaptation   KPI#4（task_type）任一分类型失败率连续 4 周 >30%
                                 且 KPI#5 均分环比下降
        TC-4 sandbox_replay      沙箱回放覆盖率 <50% 且 KPI#6 沉淀增量连续 4 周停滞
        TC-5 l3_research         TC-1/TC-3/TC-4 全触发 + 审计 3 个月 100% + 零事故
                                 + G1-G5 就绪 + 决策层批准（后三项为外部输入，
                                 未提供 → unknown）

        Args:
            weeks: 判定窗口周数（默认 4）
            min_candidates: KPI#7 候选基数门槛（默认实例配置/5）
            replay_coverage: 沙箱回放覆盖率 [0,1]（回放统计未接入时为 None → TC-4 unknown）
            audit_ok: 审计通过率连续 3 个月 100%（外部输入，None → unknown）
            g1_g5_ready: G1-G5 全部实现（外部输入，None → unknown）
            decision_approval: 决策层书面批准（外部输入，None → unknown）

        Returns:
            {"generated_at", "window_weeks", "min_candidates", "weekly",
             "conditions": {id: {...}}}
        """
        weeks = max(1, int(weeks if weeks is not None else self._trigger_window_weeks))
        replay_threshold = self._replay_coverage_threshold
        min_candidates = max(1, int(min_candidates if min_candidates is not None
                                    else self._min_candidates))
        # 多取一周供环比（KPI#1/KPI#5 需最新周 vs 前一周）
        weekly = self.get_weekly_kpis(weeks=weeks + 1, min_candidates=min_candidates)
        latest = weekly[-1] if weekly else None
        prev = weekly[-2] if len(weekly) >= 2 else None

        def _wk_evol(wk: Dict[str, Any]) -> Tuple[int, int, float]:
            """周行 → (candidates, adopted, raw_rate)；无数据周返回 (0, 0, 0.0)"""
            ev = wk["evolution"]
            cands, adopted = ev["candidates"], ev["adopted"]
            return cands, adopted, (adopted / cands if cands else 0.0)

        def _wk_workflow(wk: Dict[str, Any]) -> Tuple[int, int, float]:
            w = wk["workflow_hit_rate"]
            return w["interactions"], w["hits"], (
                w["hits"] / w["interactions"] if w["interactions"] else 0.0)

        def _wk_task_over30(wk: Dict[str, Any]) -> Tuple[bool, dict]:
            """任一分类型失败率 >30%？返回 (命中, 明细)；无任务数据返回 (False, {})。
            阈值比较用原始 failed/total（周行 rate 为四舍五入展示值，不参与判定）。"""
            rates = wk["failure_rate_by_task_type"]
            detail = {}
            hit = False
            for t, d in rates.items():
                if d["total"] > 0:
                    raw_rate = d["failed"] / d["total"]
                    detail[t] = {"total": d["total"], "failed": d["failed"],
                                 "rate": d["rate"]}
                    if raw_rate > _TRIGGER_FAILURE_RATE:
                        hit = True
            return hit, detail

        def _streak(pred) -> Tuple[Optional[bool], str]:
            """窗口内每周 pred(week) 判定；返回 (是否满足, 状态)。
            数据缺失周 → pred 抛 KeyError 或显式返回 None → 视为不可度量。"""
            if latest is None or len(weekly) < weeks:
                return None, "insufficient_data"  # 系统运行不足一个窗口
            ok = True
            for wk in weekly[-weeks:]:
                try:
                    r = pred(wk)
                except Exception:
                    r = None
                if r is None:
                    return None, "insufficient_data"
                if not r:
                    ok = False
            return ok, ("hit" if ok else "not_hit")

        conditions: Dict[str, Dict[str, Any]] = {}

        # ── TC-1 引入 LLM-as-Judge：KPI#7 连续 4 周 <5% 且 KPI#1 环比无提升 ──
        def _judge_evol_ok(wk: Dict[str, Any]) -> Optional[bool]:
            cands, adopted, rate = _wk_evol(wk)
            if cands < min_candidates:
                return None  # 候选基数不足 → 该周不可度量
            return rate < _TRIGGER_EVOLUTION_RATE

        def _judge_kpi1(wk: Dict[str, Any]) -> Optional[bool]:
            """KPI#1 环比无提升：最新周复用率 <= 前一周复用率（双方均需有 token 数据）"""
            if latest is None or prev is None:
                return None
            cur = latest["token_reuse_rate"]
            p = prev["token_reuse_rate"]
            if cur["total"] <= 0 or p["total"] <= 0:
                return None  # 任一周无 token 消耗 → 环比不可度量
            return cur["rate"] <= p["rate"]

        ev_streak, ev_status = _streak(_judge_evol_ok)
        kpi1 = _judge_kpi1(latest) if latest else None
        hit_tc1 = bool(ev_streak and kpi1)
        if ev_status == "insufficient_data" or kpi1 is None:
            status_tc1 = "insufficient_data"
        elif hit_tc1:
            status_tc1 = "hit"
        else:
            status_tc1 = "not_hit"
        conditions["judge_intro"] = {
            "id": "judge_intro",
            "capability": "引入 LLM-as-Judge（评估角色化）",
            "source": "报告 §3.3/§5.2",
            "hit": hit_tc1,
            "status": status_tc1,
            "detail": {
                "evolution_rate_threshold": _TRIGGER_EVOLUTION_RATE,
                "min_candidates": min_candidates,
                "window_weeks": weeks,
                "latest_evolution": (dict(latest["evolution"]) if latest else None),
                "kpi1_wow_no_improvement": kpi1,
                "token_reuse_latest": (latest["token_reuse_rate"]["rate"] if latest else None),
                "token_reuse_prev": (prev["token_reuse_rate"]["rate"] if prev else None),
            },
        }

        # ── TC-2 Solver 路径增强：KPI#3 工作流命中率连续 4 周 <10% ──
        def _solver_ok(wk: Dict[str, Any]) -> Optional[bool]:
            interactions, hits, rate = _wk_workflow(wk)
            if interactions <= 0:
                return None  # 无交互周无法度量命中率
            return rate < _TRIGGER_WORKFLOW_RATE

        solver_streak, solver_status = _streak(_solver_ok)
        conditions["solver_enhancement"] = {
            "id": "solver_enhancement",
            "capability": "Solver 路径增强",
            "source": "报告 §3.3",
            "hit": bool(solver_streak),
            "status": solver_status,
            "detail": {
                "workflow_rate_threshold": _TRIGGER_WORKFLOW_RATE,
                "window_weeks": weeks,
                "latest_workflow_hit_rate": (
                    dict(latest["workflow_hit_rate"]) if latest else None),
            },
        }

        # ── TC-3 课程难度自适应：KPI#4 任一分类型失败率连续 4 周 >30%
        #       且 KPI#5 均分环比下降 ──
        def _course_fail_ok(wk: Dict[str, Any]) -> Optional[bool]:
            hit, _detail = _wk_task_over30(wk)
            if not wk["failure_rate_by_task_type"]:
                return None  # 无任务数据周无法度量失败率
            return hit

        course_streak, course_status = _streak(_course_fail_ok)
        fb_drop = None
        if latest is not None and prev is not None:
            cur_avg = latest["feedback"]["avg"]
            p_avg = prev["feedback"]["avg"]
            if cur_avg is not None and p_avg is not None:
                fb_drop = cur_avg < p_avg
        hit_tc3 = bool(course_streak and fb_drop)
        if course_status == "insufficient_data" or fb_drop is None:
            status_tc3 = "insufficient_data"
        elif hit_tc3:
            status_tc3 = "hit"
        else:
            status_tc3 = "not_hit"
        conditions["course_adaptation"] = {
            "id": "course_adaptation",
            "capability": "启用课程难度自适应",
            "source": "报告 §3.3/§5.2",
            "hit": hit_tc3,
            "status": status_tc3,
            "detail": {
                "failure_rate_threshold": _TRIGGER_FAILURE_RATE,
                "window_weeks": weeks,
                "latest_task_types": (dict(latest["failure_rate_by_task_type"])
                                      if latest else {}),
                "feedback_avg_declined": fb_drop,
                "feedback_latest_avg": (latest["feedback"]["avg"] if latest else None),
                "feedback_prev_avg": (prev["feedback"]["avg"] if prev else None),
            },
        }

        # ── TC-4 沙箱回放：覆盖率 <50% 且 KPI#6 沉淀增量连续 4 周停滞 ──
        def _artifact_ok(wk: Dict[str, Any]) -> Optional[bool]:
            return wk["artifact_delta"]["count"] <= _TRIGGER_ARTIFACT_STAGNATION

        art_streak, art_status = _streak(_artifact_ok)
        if replay_coverage is None:
            tc4_status = "unknown"
            tc4_hit = False
        elif art_status == "insufficient_data":
            tc4_status = "insufficient_data"
            tc4_hit = False
        else:
            tc4_hit = bool(art_streak and replay_coverage < replay_threshold)
            tc4_status = "hit" if tc4_hit else "not_hit"
        conditions["sandbox_replay"] = {
            "id": "sandbox_replay",
            "capability": "启用沙箱回放",
            "source": "报告 §5.2",
            "hit": tc4_hit,
            "status": tc4_status,
            "detail": {
                "replay_coverage": replay_coverage,
                "replay_coverage_threshold": replay_threshold,
                "artifact_stagnation_threshold": _TRIGGER_ARTIFACT_STAGNATION,
                "window_weeks": weeks,
                "latest_artifact_count": (latest["artifact_delta"]["count"]
                                          if latest else None),
            },
        }

        # ── TC-5 L3 研究：TC-1/TC-3/TC-4 全触发 + 外部前置 ──
        core_hit = (conditions["judge_intro"]["hit"]
                    and conditions["course_adaptation"]["hit"]
                    and conditions["sandbox_replay"]["hit"])
        core_statuses = [conditions["judge_intro"]["status"],
                         conditions["course_adaptation"]["status"],
                         conditions["sandbox_replay"]["status"]]
        externals = {"audit_ok": audit_ok, "g1_g5_ready": g1_g5_ready,
                     "decision_approval": decision_approval}
        unknown_ext = any(v is None for v in externals.values())
        if not core_hit:
            tc5_status = "not_hit" if "unknown" not in core_statuses else "unknown"
        elif unknown_ext:
            tc5_status = "unknown"
        elif all(externals.values()):
            tc5_status = "hit"
        else:
            tc5_status = "not_hit"
        conditions["l3_research"] = {
            "id": "l3_research",
            "capability": "启动元规则级递归自进化（L3 研究）",
            "source": "报告 §5.2",
            "hit": tc5_status == "hit",
            "status": tc5_status,
            "detail": {
                "core_conditions": {cid: conditions[cid]["status"]
                                    for cid in ("judge_intro", "course_adaptation",
                                                "sandbox_replay")},
                "externals": {k: (v if v is not None else "unknown")
                              for k, v in externals.items()},
            },
        }

        result = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "window_weeks": weeks,
            "min_candidates": min_candidates,
            "weekly": weekly,
            "conditions": conditions,
        }
        # 查询层 → Prometheus gauge（告警引用其结果）；失败静默（零影响）
        self._sync_trigger_gauges(result)
        return result

    def _sync_trigger_gauges(self, result: Dict[str, Any]) -> None:
        """把触发条件结果同步到 Prometheus gauge（异常静默，绝不影响查询层）"""
        try:
            from agent.monitoring.learning_trigger_metrics import sync_trigger_gauges
            sync_trigger_gauges(result)
        except Exception:
            logger.debug("[学习度量] 触发条件 gauge 同步失败（静默）")

    def _get_eval_stats(self) -> Dict[str, int]:
        """聚合 TASK-02 已埋的 learning.eval.* 计数器（评估失败率数据源）"""
        try:
            collector = get_metrics_collector()
            counters = getattr(collector, "_counters", {}) or {}
            total = counters.get("learning.eval.total", 0)
            failed = counters.get("learning.eval.failed", 0)
            passed = counters.get("learning.eval.passed", 0)
            return {
                "total": int(total),
                "passed": int(passed),
                "failed": int(failed),
                "failure_rate": round(failed / total, 4) if total else 0.0,
            }
        except Exception:
            return {"total": 0, "passed": 0, "failed": 0, "failure_rate": 0.0}

    def reset(self) -> None:
        """重置本地聚合状态（测试 / 单例清理用）"""
        with self._lock:
            self._tokens_saved = 0
            self._tokens_total = 0
            self._semantic_queries = 0
            self._skill_hits = 0
            self._total_interactions = 0
            self._workflow_queries = 0
            self._workflow_hits = 0
            self._task_results.clear()
            self._complexity_results.clear()
            self._task_cx_results.clear()
            self._daily_events.clear()
            self._feedback.clear()
            self._daily_stats.clear()
            self._artifacts.clear()
            self._evolution_candidates = 0
            self._evolution_adopted = 0
            self._judge_candidates = 0
            self._judge_judged = 0
            self._judge_rule_adopted = 0
            self._judge_implied_adopted = 0
            self._judge_disagreements = 0
            self._judge_budget_blocked = 0
            self._judge_tokens_used = 0
            self._pending.clear()

    # ════════════════════════════════════════════════════════════════
    #  SQLite 持久化（可选，默认关闭；I/O 全在锁外）
    # ════════════════════════════════════════════════════════════════

    def _queue_event(self, day: str, kind: str, key: str,
                     val: float, cnt: int) -> None:
        """累积一条事件（须在持锁上下文调用；RLock 可重入）

        任务2：恒写入日粒度内存镜像 _daily_events（周级滚动统计/触发判定的数据源，
        与持久化开关无关）；持久化开启时同步进 _pending（落库侧通道）。
        """
        try:
            k = (day, kind, key)
            e = self._daily_events.get(k)
            if e is None:
                self._daily_events[k] = [float(val), int(cnt)]
            else:
                e[0] += float(val)
                e[1] += int(cnt)
        except Exception as e:
            logger.debug("[学习度量] 日粒度事件累积失败: %s", e)
        if self._persistence is None:
            return
        try:
            k = (day, kind, key)
            e = self._pending.get(k)
            if e is None:
                self._pending[k] = [float(val), int(cnt)]
            else:
                e[0] += float(val)
                e[1] += int(cnt)
        except Exception as e:
            logger.debug("[学习度量] 持久化队列累积失败: %s", e)

    def _maybe_flush(self) -> None:
        """达批量阈值时触发一次落库（锁外调用；I/O 不进锁）"""
        if self._persistence is None:
            return
        try:
            with self._lock:
                size = len(self._pending)
            if size >= self._flush_batch:
                self.flush()
        except Exception:
            pass

    def flush(self) -> None:
        """把 pending 批量写入 SQLite（单事务）；失败自动降级为内存聚合

        【不易】_db_lock 与 self._lock 分离，I/O 全在锁外；异常不抛给调用方
        （埋点零影响）；降级后后续 flush 为 no-op，内存聚合不受影响。
        """
        if self._persistence is None:
            return
        with self._db_lock:
            with self._lock:
                if not self._pending:
                    return
                batch = self._pending
                self._pending = {}
            try:
                conn = sqlite3.connect(self._db_path, timeout=5)
                try:
                    conn.execute("PRAGMA busy_timeout=5000")
                    conn.execute(_PERSIST_CREATE_SQL)
                    conn.execute("BEGIN")
                    for (day, kind, key), (val, cnt) in batch.items():
                        conn.execute(_PERSIST_UPSERT_SQL, (day, kind, key, val, cnt))
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
            except Exception as e:
                logger.warning("[学习度量] 持久化落库失败，降级为内存聚合: %s", e)
                self._persistence = None

    def _init_db(self) -> None:
        """建表并回填近 retention_days 数据（构造时调用；失败由调用方降级）"""
        parent = os.path.dirname(self._db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self._db_path, timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(_PERSIST_CREATE_SQL)
            conn.commit()
        finally:
            conn.close()
        self._load_from_db()

    def _load_from_db(self) -> None:
        """从每日聚合表回填内存状态（重启恢复；按 kind 还原，与 record_* 口径一致）"""
        cutoff_day = _day_iso(time.time() - self._retention_days * 86400)
        try:
            conn = sqlite3.connect(self._db_path, timeout=5)
            try:
                conn.execute("PRAGMA busy_timeout=5000")
                rows = conn.execute(
                    "SELECT day, kind, key, val, cnt FROM lm_daily_agg WHERE day >= ?",
                    (cutoff_day,)).fetchall()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("[学习度量] 持久化回填失败，以内存聚合为准: %s", e)
            return
        with self._lock:
            for day, kind, key, val, cnt in rows:
                try:
                    if kind == "interaction":
                        self._total_interactions += cnt
                        self._daily_stats[day]["interactions"] += cnt
                    elif kind == "workflow_query":
                        self._workflow_queries += cnt
                    elif kind == "workflow_hit":
                        self._workflow_hits += cnt
                    elif kind == "semantic_query":
                        self._semantic_queries += cnt
                    elif kind == "semantic_hit":
                        self._skill_hits += cnt
                    elif kind == "token_total":
                        self._tokens_total += int(val)
                    elif kind == "token_saved":
                        self._tokens_saved += int(val)
                        self._tokens_total += int(val)  # 与 record_token_reuse 口径一致
                    elif kind == "task_total":
                        self._task_results[key]["total"] += cnt
                    elif kind == "task_failed":
                        self._task_results[key]["failed"] += cnt
                    elif kind == "task_complexity_total":
                        self._complexity_results[key]["total"] += cnt
                    elif kind == "task_complexity_failed":
                        self._complexity_results[key]["failed"] += cnt
                    elif kind == "task_cx_total":
                        _t, _sep, _c = key.partition("::")
                        if _sep and _c:
                            self._task_cx_results[(_t, _c)]["total"] += cnt
                    elif kind == "task_cx_failed":
                        _t, _sep, _c = key.partition("::")
                        if _sep and _c:
                            self._task_cx_results[(_t, _c)]["failed"] += cnt
                    elif kind == "feedback":
                        avg = val / cnt if cnt else 0.0
                        day_ts = _day_ts_approx(day)
                        self._feedback.extend([(day_ts, avg)] * cnt)
                        self._daily_stats[day]["feedback_ratings"].extend([avg] * cnt)
                    elif kind == "artifact":
                        self._artifacts[key] += cnt
                    elif kind == "evolution_candidate":
                        self._evolution_candidates += cnt
                    elif kind == "evolution_adopted":
                        self._evolution_adopted += cnt
                except Exception:
                    continue
            # 任务2：回填日粒度事件镜像（与持久化同源同形；周级滚动统计跨重启恢复）
            for day, kind, key, val, cnt in rows:
                try:
                    k = (day, kind, key)
                    e = self._daily_events.get(k)
                    if e is None:
                        self._daily_events[k] = [float(val), int(cnt)]
                    else:
                        e[0] += float(val)
                        e[1] += int(cnt)
                except Exception:
                    continue
            self._prune_daily_events()

    # ════════════════════════════════════════════════════════════════
    #  内部工具
    # ════════════════════════════════════════════════════════════════

    def _collect(self, fn_name: str, *args, **kwargs) -> None:
        """写透出通道：注入 collector 优先，否则走全局安全包装"""
        if self._collector is not None:
            try:
                fn = getattr(self._collector, fn_name, None)
                if fn is not None:
                    fn(*args, **kwargs)
            except Exception:
                pass  # 注入 collector 异常同样吞掉
        else:
            _safe_collector(fn_name, *args, **kwargs)

    def _prune_daily_events(self) -> None:
        """裁剪超出周级统计保留窗口的日粒度事件（必须持有锁）"""
        cutoff = _day_iso(time.time() - _WEEKLY_RETENTION_DAYS * 86400)
        self._daily_events = {
            k: v for k, v in self._daily_events.items() if k[0] >= cutoff
        }

    def _prune_feedback(self) -> None:
        """裁剪超出保留窗口的反馈记录（必须持有锁）"""
        cutoff = time.time() - _RETENTION_DAYS * 86400
        self._feedback = [(ts, r) for ts, r in self._feedback if ts >= cutoff]
        # 硬上限兜底（防单日海量反馈撑爆内存）
        if len(self._feedback) > 100000:
            self._feedback = self._feedback[-100000:]

    def _prune_daily_stats(self) -> None:
        """裁剪超出保留窗口的每日统计桶（必须持有锁）

        【任务2 修复】原实现对裁剪结果直接赋值普通 dict，破坏 __init__ 建立的
        defaultdict 自动补键契约——第二个新日期起 record_interaction/record_feedback
        访问缺失键抛 KeyError（被外层 except 静默吞掉），日粒度数据静默丢失。
        现重建为 defaultdict（同工厂），自动补键契约保持，既有 KPI 聚合口径不变。
        """
        cutoff = _day_iso(time.time() - _RETENTION_DAYS * 86400)
        self._daily_stats = defaultdict(
            lambda: {"interactions": 0, "feedback_ratings": []},
            {k: v for k, v in self._daily_stats.items() if k >= cutoff},
        )


# ════════════════════════════════════════════════════════════════
#  全局单例（SingletonManager 规范）
# ════════════════════════════════════════════════════════════════

_global_learning_metrics: Optional[LearningMetrics] = None  # fallback

try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton, reset_singleton,
    )
    _SINGLETON_AVAILABLE = True
except ImportError:
    logger.warning(
        "[学习度量] singleton_manager 不可用，降级为模块级全局单例 "
        "（不影响 KPI 聚合，进程内共享）")
    _SINGLETON_AVAILABLE = False
    register_singleton = get_singleton = reset_singleton = None


def _resolve_persistence_config(config: Optional[dict] = None) -> Optional[dict]:
    """持久化配置解析：环境变量 > config dict > config.yaml > 默认关闭

    【不易】默认关闭——未显式开启时行为与纯内存完全一致（TASK-03 不变式）。
    任务2：config 为 None 时回退读取仓库根 config.yaml 的 learning.metrics.persistence
    （工厂经 get_singleton 创建时无调用方显式传 config，需在此落配置段）。
    """
    if config is None:
        config = _load_config_yaml_once() or {}

    def _env(k: str, default: Any = None) -> Any:
        return os.environ.get(k, default)

    def _cfg(keys: Tuple[str, ...], default: Any = None) -> Any:
        node = config or {}
        for key in keys:
            if not isinstance(node, dict):
                return default
            node = node.get(key, {})
        return node if node != {} else default

    enabled = _env("LEARNING_METRICS_PERSIST_ENABLED", None)
    if enabled is None:
        enabled = _cfg(("learning", "metrics", "persistence", "enabled"), False)
    if not enabled or str(enabled).strip().lower() in ("0", "false", "no", "off"):
        return None
    path = _env("LEARNING_METRICS_PERSIST_PATH", None) or _cfg(
        ("learning", "metrics", "persistence", "path"), "data/learning_metrics.db")
    batch = _env("LEARNING_METRICS_PERSIST_FLUSH_BATCH", None) or _cfg(
        ("learning", "metrics", "persistence", "flush_batch_size"), 200)
    retention = _env("LEARNING_METRICS_PERSIST_RETENTION_DAYS", None) or _cfg(
        ("learning", "metrics", "persistence", "retention_days"), 90)
    return {
        "enabled": True,
        "path": str(path),
        "flush_batch_size": int(batch),
        "retention_days": int(retention),
    }


def _resolve_metrics_config(config: Optional[dict] = None) -> Dict[str, Any]:
    """任务2 触发监控参数解析：环境变量 > config（learning.metrics.*）> 默认值"""
    if config is None:
        config = _load_config_yaml_once() or {}

    def _env(k: str, default: Any = None) -> Any:
        return os.environ.get(k, default)

    def _cfg(keys: Tuple[str, ...], default: Any = None) -> Any:
        node = config or {}
        for key in keys:
            if not isinstance(node, dict):
                return default
            node = node.get(key, {})
        return node if node != {} else default

    min_candidates = _env("LEARNING_METRICS_MIN_CANDIDATES", None)
    if min_candidates is None:
        min_candidates = _cfg(
            ("learning", "metrics", "min_candidates"), _DEFAULT_MIN_CANDIDATES)
    try:
        min_candidates = max(1, int(min_candidates))
    except (TypeError, ValueError):
        min_candidates = _DEFAULT_MIN_CANDIDATES

    window_weeks = _env("LEARNING_METRICS_TRIGGER_WINDOW_WEEKS", None)
    if window_weeks is None:
        window_weeks = _cfg(
            ("learning", "metrics", "trigger_monitoring", "window_weeks"),
            _TRIGGER_WINDOW_WEEKS)
    try:
        window_weeks = max(1, int(window_weeks))
    except (TypeError, ValueError):
        window_weeks = _TRIGGER_WINDOW_WEEKS

    replay_threshold = _env("LEARNING_METRICS_REPLAY_COVERAGE_THRESHOLD", None)
    if replay_threshold is None:
        replay_threshold = _cfg(
            ("learning", "metrics", "trigger_monitoring",
             "replay_coverage_threshold"), _TRIGGER_REPLAY_COVERAGE)
    try:
        replay_threshold = float(replay_threshold)
    except (TypeError, ValueError):
        replay_threshold = _TRIGGER_REPLAY_COVERAGE

    return {
        "min_candidates": min_candidates,
        "trigger_window_weeks": window_weeks,
        "replay_coverage_threshold": replay_threshold,
    }


def _create_learning_metrics(config: Optional[dict] = None) -> LearningMetrics:
    """LearningMetrics 工厂（供 SingletonManager 使用）"""
    mcfg = _resolve_metrics_config(config)
    return LearningMetrics(
        persistence=_resolve_persistence_config(config),
        min_candidates=mcfg.get("min_candidates", _DEFAULT_MIN_CANDIDATES),
        trigger_window_weeks=mcfg.get(
            "trigger_window_weeks", _TRIGGER_WINDOW_WEEKS),
        replay_coverage_threshold=mcfg.get(
            "replay_coverage_threshold", _TRIGGER_REPLAY_COVERAGE),
    )


def get_learning_metrics() -> LearningMetrics:
    """获取全局学习度量单例"""
    if _SINGLETON_AVAILABLE:
        return get_singleton("learning_metrics")
    global _global_learning_metrics
    if _global_learning_metrics is None:
        _global_learning_metrics = _create_learning_metrics()
    return _global_learning_metrics


def reset_learning_metrics() -> None:
    """重置全局学习度量单例（仅测试使用）"""
    global _global_learning_metrics
    if _SINGLETON_AVAILABLE:
        reset_singleton("learning_metrics")
    _global_learning_metrics = None


if _SINGLETON_AVAILABLE:
    register_singleton("learning_metrics", _create_learning_metrics)


__all__ = [
    "LearningMetrics",
    "get_learning_metrics",
    "reset_learning_metrics",
]
