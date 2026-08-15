"""策略注入与统计 — 任务6 步骤3/4（injector）

职责:
  - 失败案例入库 + 跑 selector 生成/筛选策略（只追加不删除）
  - get_strategies(scope_key): 按范围查询可注入策略（注入时记录 strategy_id 日志，可追溯）
  - record_strategy_result(strategy_id, success): 使用计数/成功率统计
  - get_strategy_stats(): auto_tuner 联动信号源
  - generate_weekly_report(): 周报（失败案例数/命中数/成功率/deprecated 数）

存储后端（backend）:
  - "json":   默认，JSON 文件（strategies.json / failure_cases.json，兼容旧数据）
  - "sqlite": 单文件 evolution.db（strategies / failure_cases 两表，同一 .db），
              模拟真实运行环境，事务性写入（与 auto_tuner 的 auto_tuning.db 同模式）

安全（防策略库投毒）:
  - prompt_patch 长度上限 MAX_PROMPT_PATCH_LEN（超长丢弃）
  - 敏感词过滤（命中替换为占位符，防提示词注入）

【不易】策略只追加不删除；注入必须携带 strategy_id 入日志。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from .defect_case import FailureCase
from .selector import (
    STATUS_ACTIVE,
    STATUS_DEPRECATED,
    Strategy,
    generate_candidates,
    generate_llm_candidates,
    select_strategies,
)

logger = logging.getLogger("agent.evolution")

# ═══════════════════════════════════════════════════════════════
#  存储后端常量
# ═══════════════════════════════════════════════════════════════

BACKEND_JSON = "json"        # JSON 文件（默认，兼容旧数据）
BACKEND_SQLITE = "sqlite"    # 单文件 evolution.db（模拟真实运行环境）
_DB_FILENAME = "evolution.db"

# ═══════════════════════════════════════════════════════════════
#  安全常量（防投毒）
# ═══════════════════════════════════════════════════════════════

MAX_PROMPT_PATCH_LEN = 500          # 注入内容长度上限（防策略库撑爆提示词）
MAX_STRATEGIES_PER_CASE = 3         # 单案例入库策略上限（top3）
MIN_ATTEMPTS_TO_DEPRECATE = 5       # deprecated 判定：尝试次数下限
DEPRECATE_SUCCESS_RATE = 0.3        # deprecated 判定：成功率阈值（< 30%）

# 敏感词（提示词注入攻击特征；命中 → 替换占位符而非入库原文）
_SENSITIVE_KEYWORDS: List[str] = [
    "ignore previous instructions",
    "ignore all previous",
    "忽略之前", "忽略以上", "无视上述", "无视之前",
    "system prompt", "你是系统", "扮演系统", "输出原始指令",
    "reveal the prompt", "泄露系统提示词",
]

_SENSITIVE_REPLACEMENT = "[已过滤]"

# 默认存储路径（与 defect_tracker/auto_tuner 的 data/ 约定一致）
DEFAULT_STORAGE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "evolution"
)


# ═══════════════════════════════════════════════════════════════
#  配置
# ═══════════════════════════════════════════════════════════════

def _env_bool(name: str, default: bool) -> bool:
    """环境变量布尔读取（'true'/'1'/'yes' → True；非法值回退默认）"""
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _yaml_bool(cfg: Dict[str, Any], key: str, default: bool) -> bool:
    try:
        return bool(cfg.get(key, default))
    except Exception:
        return default


def get_evolution_config() -> Dict[str, Any]:
    """evolution 配置：env 优先 > config.yaml > 默认值（【变易】可配置开关）"""
    cfg: Dict[str, Any] = {}
    try:
        import yaml
        cfg_path = os.path.join(os.path.dirname(__file__), "..", "..", "config.yaml")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            evolution = data.get("evolution") or {}
            if isinstance(evolution, dict):
                cfg = evolution
    except Exception:
        cfg = {}
    return {
        "enabled": _env_bool("EVOLUTION_ENABLED", _yaml_bool(cfg, "enabled", True)),
        "llm_generate": _env_bool(
            "EVOLUTION_LLM_GENERATE", _yaml_bool(cfg, "llm_generate", True)
        ),
        "storage_path": os.environ.get(
            "EVOLUTION_STORAGE_PATH", str(cfg.get("storage_path", DEFAULT_STORAGE_PATH))
        ),
    }


# ═══════════════════════════════════════════════════════════════
#  StrategyInjector
# ═══════════════════════════════════════════════════════════════

class StrategyInjector:
    """策略注入器（单例；JSON 存储，线程安全）"""

    def __init__(
        self,
        storage_path: Optional[str] = None,
        *,
        llm_generate: Optional[bool] = None,
        backend: str = BACKEND_JSON,
    ):
        config = get_evolution_config()
        self.storage_path = os.path.abspath(storage_path or config["storage_path"])
        self.llm_generate = llm_generate if llm_generate is not None else config["llm_generate"]
        self._enabled = config["enabled"]
        self.backend = backend if backend in (BACKEND_JSON, BACKEND_SQLITE) else BACKEND_JSON
        self._strategies_path = os.path.join(self.storage_path, "strategies.json")
        self._cases_path = os.path.join(self.storage_path, "failure_cases.json")
        self._db_path = os.path.join(self.storage_path, _DB_FILENAME)
        self._lock = threading.RLock()
        self._strategies: List[Strategy] = []
        self._cases: List[FailureCase] = []
        self._load()

    # ── 存储 ────────────────────────────────────────────────────

    def _load(self) -> None:
        """加载策略与案例（损坏/缺失 → 空列表，不抛异常）"""
        try:
            os.makedirs(self.storage_path, exist_ok=True)
        except OSError:
            pass
        if self.backend == BACKEND_SQLITE:
            self._load_sqlite()
        else:
            self._strategies = self._load_json_list(self._strategies_path, Strategy.from_dict)
            self._cases = self._load_json_list(self._cases_path, FailureCase.from_dict)

    def _load_json_list(self, path: str, ctor) -> list:
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [ctor(d) for d in data if isinstance(d, dict)]
        except (json.JSONDecodeError, IOError, KeyError, TypeError):
            return []

    def _save(self) -> None:
        with self._lock:
            if self.backend == BACKEND_SQLITE:
                self._save_sqlite()
                return
            os.makedirs(self.storage_path, exist_ok=True)
            with open(self._strategies_path, "w", encoding="utf-8") as f:
                json.dump([s.to_dict() for s in self._strategies], f,
                          ensure_ascii=False, indent=2)
            with open(self._cases_path, "w", encoding="utf-8") as f:
                json.dump([c.to_dict() for c in self._cases], f,
                          ensure_ascii=False, indent=2)

    # ── SQLite 后端（单文件 evolution.db，与 auto_tuner 同模式）────

    def _get_db_conn(self) -> sqlite3.Connection:
        os.makedirs(self.storage_path, exist_ok=True)
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT NOT NULL UNIQUE,
                data TEXT NOT NULL,
                created_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS failure_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL UNIQUE,
                data TEXT NOT NULL,
                created_at REAL
            )
        """)

    def _load_sqlite(self) -> None:
        """从 SQLite 加载策略与案例（表缺失/损坏 → 空列表）"""
        if not os.path.exists(self._db_path):
            return
        try:
            conn = self._get_db_conn()
            try:
                self._init_db_schema(conn)
                rows = conn.execute("SELECT data FROM strategies ORDER BY id").fetchall()
                self._strategies = [
                    Strategy.from_dict(json.loads(r["data"]))
                    for r in rows if isinstance(json.loads(r["data"]), dict)
                ]
                rows = conn.execute("SELECT data FROM failure_cases ORDER BY id").fetchall()
                self._cases = [
                    FailureCase.from_dict(json.loads(r["data"]))
                    for r in rows if isinstance(json.loads(r["data"]), dict)
                ]
            finally:
                conn.close()
        except Exception as e:
            logger.warning("[evolution] SQLite 加载失败，回退空列表: %s", e)
            self._strategies = []
            self._cases = []

    def _save_sqlite(self) -> None:
        """事务性写入 SQLite（strategies 与 failure_cases 在同一 .db）"""
        conn = self._get_db_conn()
        try:
            self._init_db_schema(conn)
            with conn:
                conn.execute("DELETE FROM strategies")
                conn.execute("DELETE FROM failure_cases")
                for s in self._strategies:
                    conn.execute(
                        "INSERT INTO strategies (strategy_id, data, created_at) VALUES (?, ?, ?)",
                        (s.strategy_id, json.dumps(s.to_dict(), ensure_ascii=False), s.created_at),
                    )
                for c in self._cases:
                    conn.execute(
                        "INSERT INTO failure_cases (case_id, data, created_at) VALUES (?, ?, ?)",
                        (c.case_id, json.dumps(c.to_dict(), ensure_ascii=False), c.created_at),
                    )
        finally:
            conn.close()

    # ── 安全（防投毒）────────────────────────────────────────────

    @staticmethod
    def _sanitize_patch(text: str) -> str:
        """长度上限 + 敏感词过滤（命中敏感词 → 替换占位符）"""
        if text is None:
            return ""
        lowered = str(text).lower()
        for kw in _SENSITIVE_KEYWORDS:
            if kw.lower() in lowered:
                logger.warning("[evolution] 策略补丁含敏感词，已过滤: %s", kw)
                lowered = lowered.replace(kw.lower(), _SENSITIVE_REPLACEMENT)
        return lowered[:MAX_PROMPT_PATCH_LEN].strip()

    # ── 采集（步骤1/2 出口）──────────────────────────────────────

    def record_failure_case(
        self,
        case: FailureCase,
        repair_hints: Optional[List[str]] = None,
        similar_strategies: Optional[List[Strategy]] = None,
        tool_name: Optional[str] = None,
    ) -> List[Strategy]:
        """失败案例入库 + 生成/筛选策略入库（只追加不删除）。

        返回本案例新入库的策略列表。LLM 候选（evolution_llm_generate=true）
        由调用方在 async 上下文用 generate_llm_candidates 生成后并入
        similar_strategies 或直接入库（本方法保持同步、可测）。
        """
        with self._lock:
            # 去重：同 trace_id + failure_type 不重复入库
            for c in self._cases:
                if c.trace_id == case.trace_id and c.failure_type == case.failure_type:
                    logger.info("[evolution] 案例重复跳过: trace=%s type=%s",
                                case.trace_id, case.failure_type)
                    return []
            self._cases.append(case)

            candidates = generate_candidates(
                case, repair_hints=repair_hints,
                similar_strategies=similar_strategies, tool_name=tool_name,
            )
            selected = select_strategies(candidates, top_n=MAX_STRATEGIES_PER_CASE)

            saved: List[Strategy] = []
            for s in selected:
                patch = self._sanitize_patch(s.prompt_patch)
                if not patch:
                    continue
                if any(ex.strategy_id == s.strategy_id or ex.prompt_patch == patch
                       for ex in self._strategies):
                    continue  # 库内已存在同内容策略（不重复入库）
                s.prompt_patch = patch
                s.scores = {}  # 评分仅供筛选用，不入库
                self._strategies.append(s)
                saved.append(s)
                logger.info("[evolution] 策略入库: %s scope=%s source=%s",
                            s.strategy_id, s.scope, s.source)

            case.candidate_strategies = [c.to_dict() for c in candidates]
            case.selected_strategies = [s.strategy_id for s in saved]
            self._save()
            return saved

    # ── 注入（步骤3）────────────────────────────────────────────

    def get_strategies(self, scope_key: str, *, trace_id: str = "") -> List[Dict[str, Any]]:
        """按范围查询可注入策略（scope 匹配 + active 过滤）。

        scope 匹配规则: 策略 scope == "global" 或 scope == scope_key。
        scope_key 形如 "tool:<工具名>" / "task_type:<类型>" / "critic"。
        命中即写 strategy_id 日志（【不易】注入可追溯）；
        未命中也写原因日志（disabled/空库/无匹配/非 active），方便排查命中逻辑。
        trace_id 为可选链路追踪号（keyword-only，向后兼容），随日志打印，
        便于跨 react/critic/tool_router 定位策略生效链路。
        """
        if not self._enabled:
            logger.info("[进化][命中排查] trace_id=%s scope_key=%s 未命中: 注入器未启用(disabled)",
                        trace_id, scope_key)
            return []
        if not self._strategies:
            logger.info("[进化][命中排查] trace_id=%s scope_key=%s 未命中: 策略库为空",
                        trace_id, scope_key)
            return []
        hits: List[Dict[str, Any]] = []
        miss_reasons = {"inactive": 0, "scope_mismatch": 0}
        for s in self._strategies:
            if s.status != STATUS_ACTIVE:
                miss_reasons["inactive"] += 1
                logger.info(
                    "[进化][命中排查] trace_id=%s %s 未命中: 原因=非active(%s), "
                    "策略scope=%s, 命中目标=%s",
                    trace_id, s.strategy_id, s.status, s.scope, scope_key,
                )
                continue
            if s.scope == "global" or s.scope == scope_key:
                hits.append({
                    "strategy_id": s.strategy_id,
                    "prompt_patch": s.prompt_patch,
                    "param_patch": dict(s.param_patch),
                    "scope": s.scope,
                    "source": s.source,
                })
            else:
                miss_reasons["scope_mismatch"] += 1
                logger.info(
                    "[进化][命中排查] trace_id=%s %s 未命中: 原因=scope不匹配, "
                    "策略scope=%s, 命中目标=%s",
                    trace_id, s.strategy_id, s.scope, scope_key,
                )
        if hits:
            logger.info(
                "[进化][命中排查] trace_id=%s scope_key=%s 命中 %d 条: %s",
                trace_id, scope_key, len(hits),
                [{"id": h["strategy_id"], "scope": h["scope"], "source": h["source"]}
                 for h in hits],
            )
        else:
            logger.info(
                "[进化][命中排查] trace_id=%s scope_key=%s 未命中: 库内策略=%d, "
                "scope不匹配=%d, 非active=%d",
                trace_id, scope_key, len(self._strategies),
                miss_reasons["scope_mismatch"], miss_reasons["inactive"],
            )
        for h in hits:
            logger.info("[evolution] 策略命中注入: trace_id=%s %s scope_key=%s",
                        trace_id, h["strategy_id"], scope_key)
        return hits

    # ── 统计（步骤4）────────────────────────────────────────────

    def record_strategy_result(self, strategy_id: str, success: bool) -> bool:
        """记录策略使用结果（attempt+1；success → success+1）"""
        with self._lock:
            for s in self._strategies:
                if s.strategy_id == strategy_id:
                    s.attempt_count += 1
                    if success:
                        s.success_count += 1
                    self._maybe_deprecate(s)
                    self._save()
                    logger.info("[evolution] 策略结果记录: %s success=%s "
                                "(attempt=%d success=%d)",
                                strategy_id, success, s.attempt_count, s.success_count)
                    return True
            logger.warning("[evolution] 策略不存在: %s", strategy_id)
            return False

    def _maybe_deprecate(self, s: Strategy) -> None:
        """deprecated 判定：尝试 ≥5 次且成功率 <30% → 标记 deprecated（不删除）"""
        if s.attempt_count >= MIN_ATTEMPTS_TO_DEPRECATE:
            rate = s.success_count / s.attempt_count
            if rate < DEPRECATE_SUCCESS_RATE:
                s.status = STATUS_DEPRECATED
                logger.warning(
                    "[evolution] 策略标记 deprecated: %s (成功率 %.2f < %.2f)",
                    s.strategy_id, rate, DEPRECATE_SUCCESS_RATE,
                )

    def update_statuses(self) -> None:
        """全量刷新策略状态（每日调度调用）"""
        with self._lock:
            for s in self._strategies:
                if s.status == STATUS_ACTIVE:
                    self._maybe_deprecate(s)
            self._save()

    def get_strategy_stats(self) -> Dict[str, Any]:
        """策略统计（auto_tuner 联动信号源）。

        输出含 by_tool: 按工具聚类的失败率（高失败率工具 → 参数建议输入）。
        """
        total = len(self._strategies)
        active = sum(1 for s in self._strategies if s.status == STATUS_ACTIVE)
        deprecated = sum(1 for s in self._strategies if s.status == STATUS_DEPRECATED)
        total_attempts = sum(s.attempt_count for s in self._strategies)
        total_success = sum(s.success_count for s in self._strategies)

        by_tool: Dict[str, Dict[str, float]] = {}
        for s in self._strategies:
            if s.scope.startswith("tool:") and s.attempt_count > 0:
                tool = s.scope.split(":", 1)[1]
                entry = by_tool.setdefault(tool, {"attempt": 0, "success": 0})
                entry["attempt"] += s.attempt_count
                entry["success"] += s.success_count
        for tool, e in by_tool.items():
            e["rate"] = round(e["success"] / e["attempt"], 4)

        return {
            "total": total,
            "active": active,
            "deprecated": deprecated,
            "total_attempts": total_attempts,
            "success_rate": round(total_success / total_attempts, 4) if total_attempts else 0.0,
            "by_tool": by_tool,
        }

    def generate_weekly_report(self) -> Dict[str, Any]:
        """周报：本周失败案例数、策略命中数、成功率、deprecated 数"""
        week_start = time.time() - 7 * 86400
        week_cases = [c for c in self._cases if c.created_at >= week_start]
        week_hits = sum(s.attempt_count for s in self._strategies
                        if s.created_at >= week_start)
        stats = self.get_strategy_stats()
        return {
            "report_type": "evolution_weekly",
            "generated_at": time.time(),
            "week_failure_cases": len(week_cases),
            "week_strategy_hits": week_hits,
            "strategy_success_rate": stats["success_rate"],
            "deprecated_count": stats["deprecated"],
            "active_count": stats["active"],
        }

    # ── 只读辅助（测试/排障）────────────────────────────────────

    def list_strategies(self) -> List[Strategy]:
        return list(self._strategies)

    def get_strategy(self, strategy_id: str) -> Optional[Strategy]:
        return next((s for s in self._strategies if s.strategy_id == strategy_id), None)

    def list_cases(self) -> List[FailureCase]:
        return list(self._cases)


# ═══════════════════════════════════════════════════════════════
#  单例（与 auto_tuner/feedback_manager 一致的 SingletonManager 模式）
# ═══════════════════════════════════════════════════════════════

_global_injector: Optional[StrategyInjector] = None

try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton,
    )
    _SINGLETON_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SINGLETON_AVAILABLE = False
    register_singleton = None
    get_singleton = None


def _create_injector(config: Optional[Dict[str, Any]] = None) -> StrategyInjector:
    storage = None
    backend = BACKEND_JSON
    if isinstance(config, dict):
        storage = config.get("storage_path")
        backend = config.get("backend", BACKEND_JSON)
    return StrategyInjector(storage_path=storage, backend=backend)


def get_injector(required: bool = False) -> Optional[StrategyInjector]:
    """获取全局注入器（未注册时返回 None，供接线点安全降级）"""
    global _global_injector
    if _SINGLETON_AVAILABLE:
        try:
            return get_singleton("evolution_injector", required=required)
        except Exception:
            return None
    if _global_injector is None:
        _global_injector = _create_injector()
    return _global_injector


if _SINGLETON_AVAILABLE:
    register_singleton("evolution_injector", _create_injector)


__all__ = [
    "StrategyInjector",
    "get_injector",
    "get_evolution_config",
    "BACKEND_JSON",
    "BACKEND_SQLITE",
    "MAX_PROMPT_PATCH_LEN",
]
