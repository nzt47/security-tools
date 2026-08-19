"""学习有效性度量体系 — 7 项学习 KPI 聚合（TASK-03）

KPI 口径（详见 docs/zh/智能体学习机制重构计划/变更说明/TASK-03_变更说明.md）：
1. token 复用率       = 命中 workflow/skill 节省 token / 期间总 token（节省 + 实际消耗）
2. Skill 命中率        = skill 命中次数 / 语义层查询次数
3. 工作流命中率         = workflow 命中次数 / 交互总数
4. 分类型失败率         = 按 task_type 统计失败占比
5. 反馈均分趋势         = 近 7 日滑动窗口 feedback.rating 均值（含逐日趋势）
6. 沉淀增量             = 新增 Skill/工作流/经验/知识卡片/反思 数量
7. 进化采纳率           = 采纳变异体数 / 候选变异体数（TASK-05 接入后产生）

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
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from agent.monitoring.metrics import get_metrics_collector

logger = logging.getLogger(__name__)

# 反馈/每日统计内存保留窗口（天）——超出窗口的旧数据被裁剪，防内存无界增长
_RETENTION_DAYS = 32

# 沉淀增量支持的产物类型（KPI schema 固定枚举，未列出的归入 other）
_ARTIFACT_TYPES = ("skill", "workflow", "experience", "knowledge_card", "reflection")

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


class LearningMetrics:
    """学习 KPI 聚合单例

    用法（生产，经 SingletonManager 获取）:
        from agent.learning_metrics import get_learning_metrics
        get_learning_metrics().record_workflow_match(hit=True, saved_tokens=1200)

    用法（测试，直接构造实例，避免单例状态跨测试污染）:
        lm = LearningMetrics(collector=DummyCollector())
    """

    def __init__(self, collector: Any = None, enabled: bool = True,
                 persistence: Optional[dict] = None):
        self.enabled = bool(enabled)
        self._collector = collector  # 注入用；None 时经 _safe_collector 走全局
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
        # ── 反馈均分趋势（含逐日桶，供 7 日趋势）──
        self._feedback: List[Tuple[float, int]] = []          # [(ts, rating)]
        self._daily_stats: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"interactions": 0, "feedback_ratings": []})
        # ── 沉淀增量 ──
        self._artifacts: Dict[str, int] = defaultdict(int)
        # ── 进化采纳率 ──
        self._evolution_candidates = 0
        self._evolution_adopted = 0

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
                self.record_token_reuse(saved_tokens)
            self._maybe_flush()
        except Exception as e:
            logger.debug("[学习度量] record_workflow_match 失败: %s", e)

    def record_semantic_query(self, hit: bool,
                              saved_tokens: int = 0) -> None:
        """语义层查询结果（skill 命中率；命中时计入 token 复用）"""
        if not self.enabled:
            return
        try:
            with self._lock:
                self._semantic_queries += 1
                if hit:
                    self._skill_hits += 1
                day = _day_iso(time.time())
                self._queue_event(day, "semantic_query", "", 1, 1)
                if hit:
                    self._queue_event(day, "semantic_hit", "", 1, 1)
            self._collect("increment_counter", "learning.semantic.queries")
            if hit:
                self._collect("increment_counter", "learning.semantic.hits")
                self.record_token_reuse(saved_tokens)
            self._maybe_flush()
        except Exception as e:
            logger.debug("[学习度量] record_semantic_query 失败: %s", e)

    def record_llm_tokens(self, tokens: int) -> None:
        """LLM 调用实际消耗 token（token 复用率分母）"""
        if not self.enabled or tokens is None:
            return
        try:
            tokens = max(0, int(tokens))
            with self._lock:
                self._tokens_total += tokens
                self._queue_event(_day_iso(time.time()), "token_total", "", float(tokens), 1)
            self._collect("increment_counter", "learning.token.total", value=tokens)
            self._collect("record_latency", "learning.token.per_call", float(tokens))
            self._maybe_flush()
        except Exception as e:
            logger.debug("[学习度量] record_llm_tokens 失败: %s", e)

    def record_token_reuse(self, saved_tokens: int) -> None:
        """workflow/skill 命中短路节省的 token（复用率分子）"""
        if not self.enabled or saved_tokens is None:
            return
        try:
            saved_tokens = max(0, int(saved_tokens))
            with self._lock:
                self._tokens_saved += saved_tokens
                self._tokens_total += saved_tokens  # 计入期间总 token（口径：节省+消耗）
                self._queue_event(_day_iso(time.time()), "token_saved", "", float(saved_tokens), 1)
            self._collect("increment_counter", "learning.token.saved", value=saved_tokens)
            self._maybe_flush()
        except Exception as e:
            logger.debug("[学习度量] record_token_reuse 失败: %s", e)

    def record_task_result(self, task_type: str, success: bool) -> None:
        """一次任务执行结果（按 task_type 分类型失败率）"""
        if not self.enabled:
            return
        try:
            key = task_type or "unknown"
            with self._lock:
                bucket = self._task_results[key]
                bucket["total"] += 1
                if not success:
                    bucket["failed"] += 1
                day = _day_iso(time.time())
                self._queue_event(day, "task_total", key, 1, 1)
                if not success:
                    self._queue_event(day, "task_failed", key, 1, 1)
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

    def record_artifact(self, artifact_type: str) -> None:
        """沉淀增量：新增 Skill/工作流/经验/知识卡片/反思 产物"""
        if not self.enabled:
            return
        try:
            key = artifact_type if artifact_type in _ARTIFACT_TYPES else "other"
            with self._lock:
                self._artifacts[key] += 1
                self._queue_event(_day_iso(time.time()), "artifact", key, 1, 1)
            self._collect("increment_counter", f"learning.artifacts.{key}")
            self._maybe_flush()
        except Exception as e:
            logger.debug("[学习度量] record_artifact 失败: %s", e)

    def record_evolution_candidate(self, adopted: bool) -> None:
        """进化采纳率：一次变异体候选决策（TASK-05 offline_evolver 接入后产生）"""
        if not self.enabled:
            return
        try:
            with self._lock:
                self._evolution_candidates += 1
                if adopted:
                    self._evolution_adopted += 1
                day = _day_iso(time.time())
                self._queue_event(day, "evolution_candidate", "", 1, 1)
                if adopted:
                    self._queue_event(day, "evolution_adopted", "", 1, 1)
            self._collect("increment_counter", "learning.evolution.candidates")
            if adopted:
                self._collect("increment_counter", "learning.evolution.adopted")
            self._maybe_flush()
        except Exception as e:
            logger.debug("[学习度量] record_evolution_candidate 失败: %s", e)

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
                },
            },
            "evaluation": self._get_eval_stats(),
            "trend_7d": trend,
        }

    def to_dict(self) -> Dict[str, Any]:
        """别名：get_snapshot()"""
        return self.get_snapshot()

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
            self._feedback.clear()
            self._daily_stats.clear()
            self._artifacts.clear()
            self._evolution_candidates = 0
            self._evolution_adopted = 0
            self._pending.clear()

    # ════════════════════════════════════════════════════════════════
    #  SQLite 持久化（可选，默认关闭；I/O 全在锁外）
    # ════════════════════════════════════════════════════════════════

    def _queue_event(self, day: str, kind: str, key: str,
                     val: float, cnt: int) -> None:
        """累积一条事件进 pending（须在持锁上下文调用；RLock 可重入）"""
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

    def _prune_feedback(self) -> None:
        """裁剪超出保留窗口的反馈记录（必须持有锁）"""
        cutoff = time.time() - _RETENTION_DAYS * 86400
        self._feedback = [(ts, r) for ts, r in self._feedback if ts >= cutoff]
        # 硬上限兜底（防单日海量反馈撑爆内存）
        if len(self._feedback) > 100000:
            self._feedback = self._feedback[-100000:]

    def _prune_daily_stats(self) -> None:
        """裁剪超出保留窗口的每日统计桶（必须持有锁）"""
        cutoff = _day_iso(time.time() - _RETENTION_DAYS * 86400)
        self._daily_stats = {
            k: v for k, v in self._daily_stats.items() if k >= cutoff
        }


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
    """持久化配置解析：环境变量 > config dict（learning.metrics.persistence）> 默认关闭

    【不易】默认关闭——未显式开启时行为与纯内存完全一致（TASK-03 不变式）。
    """
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


def _create_learning_metrics(config: Optional[dict] = None) -> LearningMetrics:
    """LearningMetrics 工厂（供 SingletonManager 使用）"""
    return LearningMetrics(persistence=_resolve_persistence_config(config))


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
