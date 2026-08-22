"""课程难度自适应策略（任务7 Step 4）— 默认关闭（观察模式）

背景（Why）:
    TASK-08 报告 F4 降级方案：复用 enhanced_planner 复杂度分级（TRIVIAL→SIMPLE→
    NORMAL→COMPLEX）作课程阶梯，"零新增基建，仅调度与路由调整"。任务7 前置
    （复杂度判定源统一 + KPI#4 复杂度维度，见 complexity_classifier.py 与
    learning_metrics.py failure_rate_by_task_type_complexity）就绪后，本模块读
    KPI#4（task_type × complexity 双维度）→ 计算各复杂度档成功率基线 → 输出
    路由概率调整建议（read-only observe）或按配置生效（enabled=true 时）。

    **默认关闭（LEARNING_CURRICULUM_ENABLED=false，观察模式）**：不读取任何 KPI、
    不产生任何路由调整、不写审计。启用决策需基于任务7 复杂度判定源对比报告与
    任务2 KPI 监控数据，属后续人工决策（不进入本任务生产启用范围）。

策略规则（推断级，方案先落地）:
    - 低复杂度档（TRIVIAL/SIMPLE）失败率 > 低档失败门槛（默认 0.30）→ 封锁高复杂度
      档路由概率提升（低档不稳 → 先积累成功样本与反思经验，不拔高难度）；
    - 高复杂度档（NORMAL/COMPLEX）成功率基线达标（≥ 0.70，默认）且样本量
      ≥ min_samples（默认 5）→ 允许提升该档路由概率（建议步长 ≤ max_step，默认 0.1）；
    - 样本不足（total < min_samples）→ 该档标记 insufficient_data，不输出建议；
    - 所有调整走审计（observe 写 decision=preview；active 写 decision=apply）。

【不易】约束（禁止触碰）:
    - 不改变主链路执行语义：本模块只输出"路由概率调整建议"（read-only observe）或
      经调用方在路由层显式消费（enabled=true 时按建议调整）；失败自动回退原路由
      （守 C5 主链路零退化）。**本任务不接线任何路由层调整**（建议仅供人工/后续决策）。
    - 不新增模型、不新增仿真环境（守 F4 降级边界）。
    - 开关默认 false：enabled=false 时 evaluate() 返回零建议空结构（无副作用）。

开关/参数（优先级: 环境变量 > config.yaml > 硬编码默认值）:
    LEARNING_CURRICULUM_ENABLED                总开关（默认 false）
    LEARNING_CURRICULUM_MODE                   模式（observe|active，默认 observe）
    LEARNING_CURRICULUM_SUCCESS_BASELINE       高复杂度档成功率基线（默认 0.70）
    LEARNING_CURRICULUM_LOW_FAILURE_THRESHOLD  低复杂度档失败门槛（默认 0.30）
    LEARNING_CURRICULUM_MIN_SAMPLES            单档最小样本量（默认 5）
    LEARNING_CURRICULUM_MAX_STEP               单档建议步长上限（默认 0.1）
    LEARNING_CURRICULUM_WINDOW_WEEKS           统计窗口周数（默认 4）
    LEARNING_CURRICULUM_AUDIT_FILE             审计路径（默认 data/learning/curriculum_audit.jsonl）
"""

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  默认值（安全底线：默认关闭）
# ════════════════════════════════════════════════════════════

DEFAULT_ENABLED = False
DEFAULT_MODE = "observe"                 # observe=只读建议+审计；active=审计+建议生效
DEFAULT_SUCCESS_BASELINE = 0.70          # 高复杂度档成功率 ≥ 该值才允许提升
DEFAULT_LOW_FAILURE_THRESHOLD = 0.30     # 低复杂度档失败率 > 该值封锁提升
DEFAULT_MIN_SAMPLES = 5                  # 单档最小样本量（不足标记 insufficient_data）
DEFAULT_MAX_STEP = 0.1                   # 单档路由概率建议步长上限
DEFAULT_WINDOW_WEEKS = 4                 # 统计窗口（周）
DEFAULT_AUDIT_FILE = "data/learning/curriculum_audit.jsonl"

# 课程阶梯（canonical 顺序，与 complexity_classifier.CANONICAL_LEVELS 一致）
LEVELS = ("TRIVIAL", "SIMPLE", "NORMAL", "COMPLEX")
# 低复杂度档（先积累成功样本/反思经验，不稳则封锁提升）
LOW_LEVELS = ("TRIVIAL", "SIMPLE")
# 高复杂度档（成功率基线达标才允许提升路由概率）
HIGH_LEVELS = ("NORMAL", "COMPLEX")

_CONFIG_CACHE: Optional[dict] = None


def _load_config_yaml() -> Optional[dict]:
    """读取仓库根 config.yaml（失败返回 None，不抛异常；带缓存）"""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE or None
    try:
        import yaml as _yaml
        _path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
        if _path.exists():
            _cfg = _yaml.safe_load(_path.read_text(encoding="utf-8")) or {}
            _CONFIG_CACHE = _cfg
            return _cfg
    except Exception:
        pass
    _CONFIG_CACHE = {}
    return None


def _parse_bool(value: Any, default: bool) -> bool:
    """开关安全解析：布尔原样返回；字符串按 true/1/yes 判 True（其余 False）"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("true", "1", "yes")


def _parse_float(value: Any, default: float) -> float:
    try:
        f = float(value)
        return f if f >= 0 else default
    except (TypeError, ValueError):
        return default


def _parse_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def resolve_curriculum_config() -> Dict[str, Any]:
    """解析课程自适应配置：环境变量 > config.yaml learning.curriculum.* > 硬编码默认值"""
    config = _load_config_yaml() or {}

    def _cfg(keys: tuple, default: Any = None) -> Any:
        node = config
        for key in keys:
            if not isinstance(node, dict):
                return default
            node = node.get(key, {})
        return node if node != {} else default

    enabled = _parse_bool(
        os.environ.get("LEARNING_CURRICULUM_ENABLED",
                       _cfg(("learning", "curriculum", "enabled"), DEFAULT_ENABLED)),
        DEFAULT_ENABLED)
    mode = str(os.environ.get("LEARNING_CURRICULUM_MODE",
                              _cfg(("learning", "curriculum", "mode"), DEFAULT_MODE))).strip().lower()
    if mode not in ("observe", "active"):
        mode = DEFAULT_MODE
    return {
        "enabled": enabled,
        "mode": mode,
        "success_baseline_threshold": _parse_float(
            os.environ.get("LEARNING_CURRICULUM_SUCCESS_BASELINE",
                           _cfg(("learning", "curriculum", "success_baseline_threshold"),
                                DEFAULT_SUCCESS_BASELINE)),
            DEFAULT_SUCCESS_BASELINE),
        "low_complexity_failure_threshold": _parse_float(
            os.environ.get("LEARNING_CURRICULUM_LOW_FAILURE_THRESHOLD",
                           _cfg(("learning", "curriculum", "low_complexity_failure_threshold"),
                                DEFAULT_LOW_FAILURE_THRESHOLD)),
            DEFAULT_LOW_FAILURE_THRESHOLD),
        "min_samples": _parse_int(
            os.environ.get("LEARNING_CURRICULUM_MIN_SAMPLES",
                           _cfg(("learning", "curriculum", "min_samples"), DEFAULT_MIN_SAMPLES)),
            DEFAULT_MIN_SAMPLES),
        "max_step": _parse_float(
            os.environ.get("LEARNING_CURRICULUM_MAX_STEP",
                           _cfg(("learning", "curriculum", "max_step"), DEFAULT_MAX_STEP)),
            DEFAULT_MAX_STEP),
        "window_weeks": _parse_int(
            os.environ.get("LEARNING_CURRICULUM_WINDOW_WEEKS",
                           _cfg(("learning", "curriculum", "window_weeks"), DEFAULT_WINDOW_WEEKS)),
            DEFAULT_WINDOW_WEEKS),
        "audit_file": str(os.environ.get(
            "LEARNING_CURRICULUM_AUDIT_FILE",
            _cfg(("learning", "curriculum", "audit_file"), DEFAULT_AUDIT_FILE))),
    }


def _aggregate_complexity(kpi_data: Any, window_weeks: int) -> Dict[str, Dict[str, int]]:
    """把 KPI 数据聚合为 complexity → {total, failed}

    支持两种输入形态（与 learning_metrics 输出对齐）:
    - list: get_weekly_kpis() 周行（取最近 window_weeks 行；每行含
      failure_rate_by_task_type_complexity 双维度节）；
    - dict: get_snapshot()["kpis"]（含 failure_rate_by_task_type_complexity 节）。

    聚合口径：各 task_type 下同一复杂度档的 total/failed 求和（复杂度维度跨任务类型）。
    """
    agg: Dict[str, Dict[str, int]] = {lvl: {"total": 0, "failed": 0} for lvl in LEVELS}
    try:
        if isinstance(kpi_data, list):
            rows = kpi_data[-window_weeks:] if kpi_data else []
            for row in rows:
                node = (row or {}).get("failure_rate_by_task_type_complexity") or {}
                for _t, cx_map in node.items():
                    for c, stats in cx_map.items():
                        key = str(c).strip().upper()
                        if key in agg:
                            agg[key]["total"] += int(stats.get("total", 0) or 0)
                            agg[key]["failed"] += int(stats.get("failed", 0) or 0)
        elif isinstance(kpi_data, dict):
            node = (kpi_data.get("kpis", kpi_data)
                    .get("failure_rate_by_task_type_complexity") or {})
            for _t, cx_map in node.items():
                for c, stats in cx_map.items():
                    key = str(c).strip().upper()
                    if key in agg:
                        agg[key]["total"] += int(stats.get("total", 0) or 0)
                        agg[key]["failed"] += int(stats.get("failed", 0) or 0)
    except Exception as e:
        logger.debug("[Curriculum] KPI 聚合失败，返回零基线: %s", e)
    return agg


class CurriculumStrategy:
    """课程难度自适应策略（默认关闭，观察模式）

    用法（生产，统一经 get_curriculum_strategy 获取）:
        from agent.learning.curriculum import get_curriculum_strategy
        advice = get_curriculum_strategy().evaluate(weekly_rows)   # 只读建议
        adjustment = get_curriculum_strategy().get_routing_adjustment(weekly_rows)

    enabled=false（默认）时 evaluate/get_routing_adjustment 返回零建议空结构，
    不读取 KPI、不写审计（零行为变化，验收：路由行为与现状一致）。
    """

    def __init__(self, config: Optional[dict] = None):
        # 基线：环境变量 > config.yaml learning.curriculum.* > 硬编码默认值
        cfg = resolve_curriculum_config()
        # 显式覆盖（测试/调用方注入）：扁平键优先于环境变量（如 {"enabled": True}）
        if config:
            for _k, _v in config.items():
                if _k in cfg and _v is not None:
                    cfg[_k] = _v
        self.enabled: bool = bool(cfg["enabled"])
        self.mode: str = cfg["mode"]
        self.success_baseline_threshold: float = cfg["success_baseline_threshold"]
        self.low_complexity_failure_threshold: float = cfg["low_complexity_failure_threshold"]
        self.min_samples: int = cfg["min_samples"]
        self.max_step: float = cfg["max_step"]
        self.window_weeks: int = cfg["window_weeks"]
        self.audit_file: str = cfg["audit_file"]
        self._audit_lock = threading.Lock()

    # ── 策略判定 ──

    def evaluate(self, kpi_data: Any) -> Dict[str, Any]:
        """读 KPI#4（复杂度维度）→ 输出路由概率调整建议（纯只读计算）

        Args:
            kpi_data: get_weekly_kpis() 周行列表 或 get_snapshot()["kpis"] dict
                      （含 failure_rate_by_task_type_complexity 双维度节）

        Returns:
            {"generated_at", "enabled", "mode", "window_weeks", "baselines",
             "gate", "adjustments", "recommendations", "audit"}
        """
        base = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "enabled": self.enabled,
            "mode": self.mode,
            "window_weeks": self.window_weeks,
        }
        if not self.enabled:
            # 默认关：零建议空结构（不读取 KPI 细节、不写审计）
            return {**base, "baselines": {}, "gate": {"blocked": False, "reason": "disabled"},
                    "adjustments": {lvl: 0.0 for lvl in LEVELS},
                    "recommendations": ["课程难度自适应默认关闭（观察模式）"],
                    "audit": {"written": False}}

        baselines = self._compute_baselines(kpi_data)
        gate = self._evaluate_gate(baselines)
        adjustments, recommendations = self._compute_adjustments(baselines, gate)

        result = {
            **base,
            "baselines": baselines,
            "gate": gate,
            "adjustments": adjustments,
            "recommendations": recommendations,
            "audit": {"written": False},
        }
        # 所有调整走审计（observe=preview / active=apply）
        decision = "apply" if self.mode == "active" else "preview"
        try:
            self._audit(decision=decision, adjustments=adjustments,
                        gate=gate, baselines=baselines)
            result["audit"] = {"written": True, "file": self.audit_file,
                               "decision": decision}
        except Exception as e:
            logger.warning("[Curriculum] 审计写入失败（不影响建议输出）: %s", e)
        return result

    def get_routing_adjustment(self, kpi_data: Any) -> Dict[str, float]:
        """路由概率调整建议（{complexity: delta}；默认关/封锁时全 0）

        - enabled=false → 全 0（零行为变化）；
        - enabled=true + observe → 按建议输出（调用方若接线，建议仅记录不生效）；
        - enabled=true + active → 按建议输出（供调用方显式应用）。
        本任务不接线任何路由层：返回值仅供人工/后续决策消费。
        """
        if not self.enabled:
            return {lvl: 0.0 for lvl in LEVELS}
        advice = self.evaluate(kpi_data)
        return dict(advice.get("adjustments") or {lvl: 0.0 for lvl in LEVELS})

    # ── 内部：基线 / 门槛 / 建议 ──

    def _compute_baselines(self, kpi_data: Any) -> Dict[str, Dict[str, Any]]:
        """各复杂度档成功率基线（跨 task_type 求和；样本不足标记 insufficient_data）"""
        agg = _aggregate_complexity(kpi_data, self.window_weeks)
        baselines: Dict[str, Dict[str, Any]] = {}
        for lvl in LEVELS:
            total = agg[lvl]["total"]
            failed = agg[lvl]["failed"]
            success = total - failed
            baselines[lvl] = {
                "total": total,
                "failed": failed,
                "success": success,
                "success_rate": round(success / total, 4) if total else 0.0,
                "failure_rate": round(failed / total, 4) if total else 0.0,
                "insufficient_data": total < self.min_samples,
            }
        return baselines

    def _evaluate_gate(self, baselines: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """低复杂度档稳定性门槛：任一低档失败率 > 门槛 → 封锁高复杂度档提升

        【推断级规则】低档不稳（失败率高）说明课程阶梯底部还没夯实，此时拔高
        高复杂度档路由概率会让失败样本集中到高难档，先积累成功样本与反思经验。
        """
        blocked = False
        reasons = []
        for lvl in LOW_LEVELS:
            b = baselines.get(lvl, {})
            if b.get("insufficient_data"):
                continue  # 样本不足不参与封锁判定
            if b.get("failure_rate", 0.0) > self.low_complexity_failure_threshold:
                blocked = True
                reasons.append(
                    "%s 失败率 %.1f%% > 门槛 %.0f%%" % (
                        lvl, b["failure_rate"] * 100,
                        self.low_complexity_failure_threshold * 100))
        if not reasons:
            reasons.append("低复杂度档稳定（无封锁）")
        return {"blocked": blocked, "reason": "；".join(reasons)}

    def _compute_adjustments(
        self,
        baselines: Dict[str, Dict[str, Any]],
        gate: Dict[str, Any],
    ) -> tuple:
        """路由概率调整建议：高复杂度档成功率达标 → +max_step；封锁/样本不足 → 0"""
        adjustments = {lvl: 0.0 for lvl in LEVELS}
        recommendations: List[str] = []
        if gate.get("blocked"):
            recommendations.append("低复杂度档失败率超门槛，封锁高复杂度档路由概率提升")
            return adjustments, recommendations
        for lvl in HIGH_LEVELS:
            b = baselines.get(lvl, {})
            if b.get("insufficient_data"):
                recommendations.append("%s 样本不足（%d < %d），不输出建议" % (
                    lvl, b.get("total", 0), self.min_samples))
                continue
            if b.get("success_rate", 0.0) >= self.success_baseline_threshold:
                adjustments[lvl] = round(self.max_step, 4)
                recommendations.append(
                    "%s 成功率 %.1f%% ≥ 基线 %.0f%% → 建议路由概率 +%.1f%%" % (
                        lvl, b["success_rate"] * 100,
                        self.success_baseline_threshold * 100, self.max_step * 100))
            else:
                recommendations.append(
                    "%s 成功率 %.1f%% < 基线 %.0f%%，不提升" % (
                        lvl, b["success_rate"] * 100,
                        self.success_baseline_threshold * 100))
        return adjustments, recommendations

    # ── 审计 ──

    def _audit(self, decision: str, adjustments: Dict[str, float],
               gate: Dict[str, Any], baselines: Dict[str, Dict[str, Any]]) -> None:
        """调整审计（JSONL 逐条追加；线程安全；失败静默不影响主链路）"""
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": "curriculum_adjustment",
            "mode": self.mode,
            "decision": decision,
            "window_weeks": self.window_weeks,
            "adjustments": adjustments,
            "gate": gate,
            "baselines": {
                lvl: {k: v for k, v in b.items() if k != "insufficient_data"}
                for lvl, b in baselines.items()
            },
        }
        path = Path(self.audit_file)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False)
            with self._audit_lock:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception as e:
            logger.warning("[Curriculum] 审计写入异常: %s", e)
            raise


# ════════════════════════════════════════════════════════════
#  全局单例（与 learning_metrics 单例模式对齐）
# ════════════════════════════════════════════════════════════

_strategy_lock = threading.Lock()
_global_strategy: Optional[CurriculumStrategy] = None


def get_curriculum_strategy() -> CurriculumStrategy:
    """获取全局课程自适应策略单例（线程安全；配置变化经 reset 重建）"""
    global _global_strategy
    if _global_strategy is None:
        with _strategy_lock:
            if _global_strategy is None:
                _global_strategy = CurriculumStrategy()
    return _global_strategy


def reset_curriculum_strategy() -> None:
    """重置全局策略单例（仅测试使用）"""
    global _global_strategy
    with _strategy_lock:
        _global_strategy = None


__all__ = [
    "LEVELS",
    "LOW_LEVELS",
    "HIGH_LEVELS",
    "DEFAULT_ENABLED",
    "CurriculumStrategy",
    "resolve_curriculum_config",
    "get_curriculum_strategy",
    "reset_curriculum_strategy",
]
