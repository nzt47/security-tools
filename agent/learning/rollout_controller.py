"""L2 自进化闭环受控放行框架（任务3）

背景（Why）:
    TASK-08 报告的"中期"结论：开启策略工件级自进化（L2）——feedback 执行体 +
    evolver 调度 + lifecycle，全部 dry-run → 观察 → 人工确认。审计确认大部分
    构件已存在（feedback_agent / evolution_scheduler / lifecycle 均
    enabled=true, dry_run=true；EVO-T1~T6 谱系/审批/回滚齐备），缺的是**受控
    放行机制**：当前只有"dry-run（零副作用）"与"关闭"两态。

    本模块实现统一四态放行状态机：
        dry_run  零副作用（默认，安全底线）
        observe  真实评估 + 候选生成，零提交（结果写谱系 decision=preview + 审计）
        confirm  产物进审批队列（复用 approval.py），人工批准后提交（先回归门禁）
        rollout  按 rollout_ratio 比例命中新版本；KPI 连续恶化自动回退 + 告警
    把三类进化动作（feedback 晋升/淘汰、evolver 提交、lifecycle 迁移）纳入统一
    管控，接任务1 回归门禁（G5 第一层）与任务2 KPI 监控（G5 第二层）。

【不易】约束（禁止触碰）:
    - 不修改 offline_evolver.py 进化算法与 BatchEvolutionReport 结构；
      不修改 feedback_agent.py / lifecycle.py 建议生成逻辑——本框架只在
      **调度出口**（learning_scheduler 注册的任务 func）叠加模式控制。
    - 提交门槛保持既有逻辑（improvement>=0.05、usage>=min_usage 等）；
      回归门禁（任务1）为可开关的附加门槛（enforce/warn_only/off）。
    - 任何放行动作默认 dry_run；rollout 必须满足"回归门禁 PASS + 审批通过"。
    - 越级拦截：未经审批的提交、脱离框架的直接写操作一律禁止
      （can_commit() 统一判定，调度出口全部经 run_scheduled 包装）。
    - 所有动作可回滚（rollback_version + 参数快照），回滚后行为与旧版本一致。

开关/参数（优先级: 环境变量 > config.yaml > 硬编码默认值）:
    LEARNING_ROLLOUT_MASTER_ENABLED                 总开关（默认 false）
    LEARNING_ROLLOUT_AUDIT_FILE                    审计路径（默认 data/learning/rollout_audit.jsonl）
    LEARNING_ROLLOUT_REGRESSION_GATE               回归门禁模式（enforce/warn_only/off，默认 enforce）
    <action> ∈ {feedback, evolution, lifecycle}:
    LEARNING_ROLLOUT_<ACTION>_MODE                  模式（默认 dry_run）
    LEARNING_ROLLOUT_<ACTION>_ROLLOUT_RATIO         命中比例 0-1（默认 0.1）
    LEARNING_ROLLOUT_<ACTION>_KPI_ROLLBACK_WINDOW_WEEKS  连续恶化周数（默认 2）

审计字段统一（data/learning/rollout_audit.jsonl 逐条 JSONL）:
    action / mode / candidate_id / object_id / parent_record_id /
    approval_record_id / decision(preview|approved|rejected|rolled_back) /
    before_version / after_version / regression_result / kpi_snapshot /
    rollback_command / detail

CLI:
    python -m agent.learning.rollout_controller --status
    python -m agent.learning.rollout_controller --action feedback --run-scheduled
    python -m agent.learning.rollout_controller --action evolution --preview
    python -m agent.learning.rollout_controller --action feedback --approve <approval_record_id>
    python -m agent.learning.rollout_controller --action feedback --reject <approval_record_id> --reason "..."
    python -m agent.learning.rollout_controller --action evolution --rollback --candidate <candidate_id>
    python -m agent.learning.rollout_controller --preview-stats [--action feedback]
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  四态 / 动作 / 决策枚举
# ════════════════════════════════════════════════════════════

MODE_DRY_RUN = "dry_run"
MODE_OBSERVE = "observe"
MODE_CONFIRM = "confirm"
MODE_ROLLOUT = "rollout"
MODES = (MODE_DRY_RUN, MODE_OBSERVE, MODE_CONFIRM, MODE_ROLLOUT)

ACTIONS = ("feedback", "evolution", "lifecycle")
_ACTION_ENV = {a: a.upper() for a in ACTIONS}

# 审计 decision（任务 Step5 统一字段；preview 与谱系 DECISIONS 对齐）
DECISION_PREVIEW = "preview"
DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"
DECISION_ROLLED_BACK = "rolled_back"
DECISIONS = (DECISION_PREVIEW, DECISION_APPROVED, DECISION_REJECTED,
             DECISION_ROLLED_BACK)

# 默认值（安全底线）
DEFAULT_AUDIT_FILE = "data/learning/rollout_audit.jsonl"
DEFAULT_MASTER_ENABLED = False
DEFAULT_MODE = MODE_DRY_RUN
DEFAULT_ROLLOUT_RATIO = 0.1
DEFAULT_KPI_WINDOW_WEEKS = 2
DEFAULT_REGRESSION_GATE = "enforce"
_ENV_PREFIX = "LEARNING_ROLLOUT"


class RolloutError(Exception):
    """放行控制异常基类"""


class RolloutGateError(RolloutError):
    """回归门禁 / 审批未通过导致的拦截"""


# ════════════════════════════════════════════════════════════
#  配置读取（环境变量 > config.yaml > 硬编码默认值）
# ════════════════════════════════════════════════════════════


def _config_yaml() -> Optional[Dict[str, Any]]:
    """读取仓库根 config.yaml（失败返回 None，不抛异常）。"""
    cfg_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
    if not cfg_path.exists():
        return None
    try:
        import yaml as _yaml  # 延迟导入，避免硬依赖
        with open(cfg_path, "r", encoding="utf-8") as f:
            return _yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001 配置解析失败回退默认
        logger.debug("[Rollout] config.yaml 读取失败: %s", e)
        return None


def _rollout_cfg() -> Dict[str, Any]:
    cfg = _config_yaml()
    if cfg is None:
        return {}
    node = ((cfg.get("learning", {}) or {}).get("rollout", {}) or {})
    return node if isinstance(node, dict) else {}


def _action_node(action: str) -> Dict[str, Any]:
    node = _rollout_cfg().get(action, {}) or {}
    return node if isinstance(node, dict) else {}


def _env_bool(key: str, default: bool) -> bool:
    env = os.environ.get(key)
    if env is not None and env.strip():
        return env.strip().lower() in ("true", "1", "yes")
    return default


def master_enabled() -> bool:
    """一键总开关（默认 false）：false 时所有动作强制 dry_run。"""
    env = os.environ.get(f"{_ENV_PREFIX}_MASTER_ENABLED")
    if env is not None and env.strip():
        return env.strip().lower() in ("true", "1", "yes")
    try:
        val = _rollout_cfg().get("master_enabled")
        if val is not None:
            return str(val).strip().lower() in ("true", "1", "yes")
    except Exception as e:  # noqa: BLE001
        logger.debug("[Rollout] master_enabled 解析失败: %s", e)
    return DEFAULT_MASTER_ENABLED


def audit_file() -> str:
    """审计日志路径（默认 data/learning/rollout_audit.jsonl）。"""
    env = os.environ.get(f"{_ENV_PREFIX}_AUDIT_FILE")
    if env is not None and env.strip():
        return env.strip()
    try:
        val = _rollout_cfg().get("audit_file")
        if val:
            return str(val)
    except Exception as e:  # noqa: BLE001
        logger.debug("[Rollout] audit_file 解析失败: %s", e)
    return DEFAULT_AUDIT_FILE


def regression_gate_mode() -> str:
    """回归门禁模式（enforce=FAIL/NO_SAMPLES 拦截 / warn_only=只读告警 / off=零调用）。"""
    env = os.environ.get(f"{_ENV_PREFIX}_REGRESSION_GATE")
    if env is not None and env.strip():
        m = env.strip().lower()
        if m in ("enforce", "warn_only", "off"):
            return m
    try:
        val = _rollout_cfg().get("regression_gate")
        if val is not None:
            m = str(val).strip().lower()
            if m in ("enforce", "warn_only", "off"):
                return m
    except Exception as e:  # noqa: BLE001
        logger.debug("[Rollout] regression_gate 解析失败: %s", e)
    return DEFAULT_REGRESSION_GATE


def normalize_action(action: str) -> str:
    if action not in ACTIONS:
        raise RolloutError(f"非法进化动作: {action}（允许: {ACTIONS}）")
    return action


def mode_for(action: str) -> str:
    """判定某动作当前有效模式：总开关关闭 → 强制 dry_run（安全底线）。"""
    action = normalize_action(action)
    if not master_enabled():
        return MODE_DRY_RUN
    env = os.environ.get(f"{_ENV_PREFIX}_{_ACTION_ENV[action]}_MODE")
    if env is not None and env.strip():
        m = env.strip().lower()
        if m in MODES:
            return m
        logger.warning("[Rollout] 非法模式 %r（action=%s），回退 dry_run（安全底线）",
                       env, action)
    try:
        val = _action_node(action).get("mode")
        if val is not None:
            m = str(val).strip().lower()
            if m in MODES:
                return m
            logger.warning("[Rollout] 非法模式 %r（action=%s），回退 dry_run（安全底线）",
                           val, action)
    except Exception as e:  # noqa: BLE001
        logger.debug("[Rollout] mode 解析失败: %s", e)
    return DEFAULT_MODE


def rollout_ratio(action: str) -> float:
    """rollout 命中比例（0-1，默认 0.1）；非法值回退默认。"""
    action = normalize_action(action)
    env = os.environ.get(f"{_ENV_PREFIX}_{_ACTION_ENV[action]}_ROLLOUT_RATIO")
    if env is not None and env.strip():
        try:
            r = float(env.strip())
            if 0.0 <= r <= 1.0:
                return r
        except ValueError:
            pass
    try:
        val = _action_node(action).get("rollout_ratio")
        if val is not None:
            r = float(val)
            if 0.0 <= r <= 1.0:
                return r
    except (TypeError, ValueError) as e:  # noqa: BLE001
        logger.debug("[Rollout] rollout_ratio 解析失败: %s", e)
    return DEFAULT_ROLLOUT_RATIO


def kpi_rollback_window_weeks(action: str) -> int:
    """KPI 连续恶化回退窗口（周，默认 2）。"""
    action = normalize_action(action)
    env = os.environ.get(
        f"{_ENV_PREFIX}_{_ACTION_ENV[action]}_KPI_ROLLBACK_WINDOW_WEEKS")
    if env is not None and env.strip():
        try:
            return max(1, int(env.strip()))
        except ValueError:
            pass
    try:
        val = _action_node(action).get("kpi_rollback_window_weeks")
        if val is not None:
            return max(1, int(val))
    except (TypeError, ValueError) as e:  # noqa: BLE001
        logger.debug("[Rollout] kpi_rollback_window_weeks 解析失败: %s", e)
    return DEFAULT_KPI_WINDOW_WEEKS


# ════════════════════════════════════════════════════════════
#  候选解析（调度出口统一提取）
# ════════════════════════════════════════════════════════════


def report_candidates(action: str, report: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从三类执行体报告提取候选条目（仅取报告已判定的候选，零额外副作用）。

    - feedback:  report["planned"] = [{skill_id, action, reason, ...}]
    - evolution: report["planned_candidates"] = [{skill_id, usage_count, success_rate}]
    - lifecycle: report["deprecated"] / report["archived"] = [{skill_id, from_status,
                  to_status, idle_days, threshold}]
    """
    action = normalize_action(action)
    report = report or {}
    if action == "feedback":
        return [
            {"skill_id": p.get("skill_id"), "action": f"feedback:{p.get('action', '')}",
             "reason": p.get("reason", "")}
            for p in report.get("planned", []) if p.get("skill_id")]
    if action == "evolution":
        return [
            {"skill_id": c.get("skill_id"), "action": "evolution",
             "reason": "周期进化候选"}
            for c in report.get("planned_candidates", []) if c.get("skill_id")]
    if action == "lifecycle":
        out: List[Dict[str, Any]] = []
        for d in report.get("deprecated", []):
            out.append({"skill_id": d.get("skill_id"),
                        "action": "lifecycle:deprecate",
                        "reason": f"闲置 {d.get('idle_days')} 天超阈值 {d.get('threshold')}"})
        for a in report.get("archived", []):
            out.append({"skill_id": a.get("skill_id"),
                        "action": "lifecycle:archive",
                        "reason": f"闲置 {a.get('idle_days')} 天超阈值 {a.get('threshold')}"})
        return out
    return []


def _report_summary(action: str, report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """报告摘要（审计 detail 用，避免整表落盘）。"""
    report = report or {}
    if action == "feedback":
        return {"total_skills": report.get("total_skills", 0),
                "processed": report.get("processed", 0),
                "planned": len(report.get("planned", [])),
                "executed": len(report.get("executed", [])),
                "rejected": len(report.get("rejected", []))}
    if action == "evolution":
        return {"total_skills": report.get("total_skills", 0),
                "evolved_count": report.get("evolved_count", 0),
                "skipped_count": report.get("skipped_count", 0),
                "failed_count": report.get("failed_count", 0)}
    return {"total_skills": report.get("total_skills", 0),
            "deprecated": len(report.get("deprecated", [])),
            "archived": len(report.get("archived", [])),
            "suggestions": len(report.get("suggestions", []))}


def _new_candidate_id() -> str:
    """生成候选 ID：cand-<timestamp>-<hash>（与谱系 record_id 风格一致）。"""
    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
    return f"cand-{ts}-{uuid.uuid4().hex[:8]}"


# ════════════════════════════════════════════════════════════
#  G5 第二层：KPI 连续恶化判据（纯函数，可独立单测）
# ════════════════════════════════════════════════════════════


def judge_kpi_degradation(weekly_rows: List[Dict[str, Any]],
                          window_weeks: int = DEFAULT_KPI_WINDOW_WEEKS) -> Dict[str, Any]:
    """任一 KPI 连续 N 个评估周期恶化 → 触发自动回退（G5 第二层判据）。

    每周"恶化"定义（与 learning_metrics.get_weekly_kpis 行结构对齐）:
        - token_reuse_rate.rate 环比下降
        - skill_hit_rate.rate 环比下降
        - workflow_hit_rate.rate 环比下降
        - failure_rate_by_task_type 任一分类型原始失败率环比上升
        - feedback.avg 环比下降
        - evolution.rate 环比下降（两侧候选基数足够）
        - artifact_delta.count 环比下降
    数据缺失/无对比周不判恶化（保守，防误回滚）。

    Args:
        weekly_rows: get_weekly_kpis 输出形状的周级行（按周升序）
        window_weeks: 连续恶化周数阈值（默认 2）

    Returns:
        {"triggered": bool, "reason": str, "detail": {week: {degraded_kpi: ...}}}
    """
    rows = list(weekly_rows or [])
    window = max(1, int(window_weeks))
    if len(rows) < window + 1:
        return {
            "triggered": False,
            "reason": (f"数据不足（{len(rows)} 周，判定连续 {window} 周恶化至少需 "
                       f"{window + 1} 周）"),
            "detail": {},
        }

    def _degraded(cur: Dict[str, Any], prev: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}

        def _rate_down(cur_d: Optional[Dict[str, Any]], prev_d: Optional[Dict[str, Any]],
                       cur_key: str, prev_key: str) -> bool:
            if not cur_d or not prev_d:
                return False
            if (prev_d.get(prev_key) or 0) <= 0 or (cur_d.get(cur_key) or 0) <= 0:
                return False
            return float(cur_d.get("rate", 0.0)) < float(prev_d.get("rate", 0.0))

        c, p = cur.get("token_reuse_rate"), prev.get("token_reuse_rate")
        if _rate_down(c, p, "total", "total"):
            out["token_reuse_rate"] = {"from": p["rate"], "to": c["rate"]}
        c, p = cur.get("skill_hit_rate"), prev.get("skill_hit_rate")
        if _rate_down(c, p, "queries", "queries"):
            out["skill_hit_rate"] = {"from": p["rate"], "to": c["rate"]}
        c, p = cur.get("workflow_hit_rate"), prev.get("workflow_hit_rate")
        if _rate_down(c, p, "interactions", "interactions"):
            out["workflow_hit_rate"] = {"from": p["rate"], "to": c["rate"]}

        cf, pf = cur.get("failure_rate_by_task_type") or {}, \
            prev.get("failure_rate_by_task_type") or {}
        for t in set(cf) | set(pf):
            cd, pd = cf.get(t), pf.get(t)
            if not cd or not pd:
                continue
            if (pd.get("total") or 0) <= 0 or (cd.get("total") or 0) <= 0:
                continue
            cr, pr = cd["failed"] / cd["total"], pd["failed"] / pd["total"]
            if cr > pr:
                out.setdefault("failure_rate_by_task_type", {})[t] = {
                    "from": round(pr, 4), "to": round(cr, 4)}

        cf, pf = cur.get("feedback"), prev.get("feedback")
        if cf and pf and cf.get("avg") is not None and pf.get("avg") is not None \
                and cf["avg"] < pf["avg"]:
            out["feedback_avg"] = {"from": pf["avg"], "to": cf["avg"]}

        ce, pe = cur.get("evolution"), prev.get("evolution")
        if ce and pe and not ce.get("insufficient_data") \
                and not pe.get("insufficient_data") \
                and (ce.get("candidates") or 0) > 0 and (pe.get("candidates") or 0) > 0 \
                and ce["rate"] < pe["rate"]:
            out["evolution_rate"] = {"from": pe["rate"], "to": ce["rate"]}

        ca, pa = cur.get("artifact_delta"), prev.get("artifact_delta")
        if ca and pa and (ca.get("count") or 0) < (pa.get("count") or 0):
            out["artifact_delta"] = {"from": pa.get("count"), "to": ca.get("count")}
        return out

    detail: Dict[str, Any] = {}
    for i in range(len(rows) - window, len(rows)):
        wk = rows[i]
        deg = _degraded(rows[i], rows[i - 1])
        if deg:
            detail[wk.get("week") or str(i)] = deg

    last_weeks = [rows[i].get("week") or str(i)
                  for i in range(len(rows) - window, len(rows))]
    if all(w in detail for w in last_weeks):
        return {
            "triggered": True,
            "reason": f"任一 KPI 连续 {window} 个评估周期恶化（G5 第二层判据命中）",
            "detail": detail,
        }
    return {"triggered": False, "reason": "KPI 无连续恶化（观察期安全）",
            "detail": detail}


# ════════════════════════════════════════════════════════════
#  执行体 runner 构建（learning_scheduler 接线与 CLI 共用）
# ════════════════════════════════════════════════════════════


def _executor_runners(action: str) -> Dict[str, Callable[[], Any]]:
    """构建该动作的 dry_runner / run_real（与调度接线同一约定）。

    dry_runner: 显式 dry_run=True —— observe/confirm/rollout 预检与预演用
                （保证零提交，不受模块自身 dry_run 配置影响）；
    run_real:   显式 dry_run=False —— 审批合并 / rollout 命中后的真实提交。
    """
    action = normalize_action(action)
    if action == "feedback":
        from agent.skills_mgmt.feedback_agent import FeedbackAgent
        agent = FeedbackAgent()
        return {
            "dry_runner": lambda: agent.execute_recommendations(dry_run=True),
            "run_real": lambda: agent.execute_recommendations(dry_run=False),
        }
    if action == "evolution":
        from agent.skills_mgmt.evolution_scheduler import EvolutionScheduler
        sched = EvolutionScheduler()
        return {
            "dry_runner": lambda: sched.run(dry_run=True, trigger="scheduler"),
            "run_real": lambda: sched.run(dry_run=False, trigger="scheduler"),
        }
    from agent.skills_mgmt.lifecycle import LifecycleManager
    mgr = LifecycleManager()
    return {
        "dry_runner": lambda: mgr.run_lifecycle_check(dry_run=True),
        "run_real": lambda: mgr.run_lifecycle_check(dry_run=False),
    }


# ════════════════════════════════════════════════════════════
#  放行控制器
# ════════════════════════════════════════════════════════════


class RolloutController:
    """四态放行控制器 — 统一读配置、判定模式、预览/审批/回滚/审计

    用法（调度出口接线，见 learning_scheduler.register_learning_schedulers）:
        ctrl = RolloutController()
        ctrl.run_scheduled("feedback", dry_runner=..., run_real=...)

    操作员 API（CLI / 人工确认流程）:
        ctrl.status()
        ctrl.approve(action, approval_record_id, actor=..., note=...)
        ctrl.reject(action, approval_record_id, actor=..., reason=...)
        ctrl.rollback(action, candidate_id, reason=...)
        ctrl.preview_stats(action, days=...)
    """

    def __init__(self, *, audit_path: Optional[str] = None,
                 archive: Optional[Any] = None,
                 approval_flow: Optional[Any] = None,
                 service: Optional[Any] = None,
                 regression_checker: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
                 kpi_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
                 rng: Optional[random.Random] = None):
        """Args:
            audit_path: 放行审计 JSONL（None=按配置/默认）
            archive: EvolutionArchive 实例（None=全局默认；测试请注入隔离实例）
            approval_flow: ApprovalFlow 实例（None=默认；测试请注入隔离实例）
            service: SkillsMgmtService（None=懒加载；测试请注入隔离实例）
            regression_checker: callable(skill_id) -> dict|None（任务1 门禁；
                注入桩供单测；None=默认构建 RegressionGate）
            kpi_provider: callable() -> 周级 KPI 行列表（任务2 监控；None=LearningMetrics）
            rng: 随机源（比例命中；None=进程随机）
        """
        self._audit_path = Path(audit_path) if audit_path else Path(audit_file())
        self._archive = archive
        self._approval_flow = approval_flow
        self._service = service
        self._regression_checker = regression_checker
        self._kpi_provider = kpi_provider
        self._rng = rng or random.Random()
        self._lock = threading.RLock()

    # ─── 模式判定 ───

    def mode_for(self, action: str) -> str:
        return mode_for(action)

    def can_commit(self, action: str) -> bool:
        """是否允许提交（越级拦截统一判定）：仅 confirm/rollout 且总开关开启。"""
        return self.mode_for(action) in (MODE_CONFIRM, MODE_ROLLOUT)

    def rollout_ratio(self, action: str) -> float:
        return rollout_ratio(action)

    def kpi_window(self, action: str) -> int:
        return kpi_rollback_window_weeks(action)

    def status(self) -> Dict[str, Any]:
        """放行状态总览（运维/操作手册用）。"""
        return {
            "master_enabled": master_enabled(),
            "regression_gate": regression_gate_mode(),
            "audit_file": str(self._audit_path),
            "actions": {
                a: {
                    "mode": self.mode_for(a),
                    "can_commit": self.can_commit(a),
                    "rollout_ratio": self.rollout_ratio(a),
                    "kpi_rollback_window_weeks": self.kpi_window(a),
                } for a in ACTIONS
            },
        }

    # ─── 调度出口统一入口 ───

    def run_scheduled(self, action: str, *, dry_runner: Callable[[], Any],
                      run_real: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
        """调度触发的统一放行入口（三类进化动作的调度出口接线处调用）。

        Args:
            dry_runner: 显式 dry_run=True 的执行体调用（候选生成/预演，零提交）
            run_real:   显式 dry_run=False 的执行体调用（审批合并 / rollout 命中提交）

        Returns:
            本轮报告 dict：{action, mode, status, ...}（按模式各异）
        """
        action = normalize_action(action)
        if not callable(dry_runner):
            raise RolloutError("dry_runner 必须可调用")
        mode = self.mode_for(action)
        logger.info("[Rollout] run_scheduled action=%s mode=%s", action, mode)
        if mode == MODE_DRY_RUN:
            report = dry_runner()
            return {"action": action, "mode": mode, "status": "dry_run",
                    "report": report}
        if mode == MODE_OBSERVE:
            report = dry_runner()
            previews = self.record_preview(action, report)
            return {"action": action, "mode": mode, "status": "observed",
                    "previews": len(previews), "report": report}
        if mode == MODE_CONFIRM:
            report = dry_runner()
            return self.enqueue_confirm(action, report, run_real)
        return self.run_rollout(action, dry_runner, run_real)

    # ─── observe 态：真实评估 + 零提交（写谱系 preview + 审计） ───

    def record_preview(self, action: str,
                       report: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """observe 态：报告中的候选逐条写谱系（decision=preview）+ 放行审计。

        零提交 / 零 KPI / 零版本变更（本方法只写谱系 preview 与审计 JSONL）。
        """
        action = normalize_action(action)
        candidates = report_candidates(action, report)
        records: List[Dict[str, Any]] = []
        for cand in candidates:
            skill_id = cand.get("skill_id")
            before = self._skill_version(skill_id)
            rec_id = self._write_archive_preview(action, skill_id, cand)
            audit = self._audit(
                action=action, mode=MODE_OBSERVE,
                candidate_id=_new_candidate_id(),
                object_id=skill_id,
                parent_record_id=rec_id,
                decision=DECISION_PREVIEW,
                before_version=before, after_version=None,
                detail={"reason": cand.get("reason", ""), "candidate": cand})
            records.append(audit)
        logger.info("[Rollout] observe 预演 action=%s candidates=%d（零提交）",
                    action, len(records))
        return records

    def preview_stats(self, action: Optional[str] = None,
                      days: int = 30) -> Dict[str, Any]:
        """预演采纳率数据源（喂任务2 监控；与真实采纳率 evolution_adoption_rate 区分）。

        统计 rollout_audit.jsonl 中 decision=preview 且带 candidate_id 的记录
        （按动作/日聚合；批级摘要记录不计入候选）。
        """
        cutoff = datetime.now() - timedelta(days=max(1, int(days)))
        by_action: Dict[str, int] = {}
        by_day: Dict[str, int] = {}
        total = 0
        for rec in self._audit_records():
            if rec.get("decision") != DECISION_PREVIEW:
                continue
            if rec.get("candidate_id") is None:
                continue
            try:
                ts = datetime.fromisoformat(rec.get("ts", ""))
            except (TypeError, ValueError):
                continue
            if ts < cutoff:
                continue
            act = rec.get("action", "unknown")
            day = ts.date().isoformat()
            by_action[act] = by_action.get(act, 0) + 1
            by_day[day] = by_day.get(day, 0) + 1
            total += 1
        if action is not None:
            action = normalize_action(action)
            by_action = {action: by_action.get(action, 0)}
        return {"days": int(days), "total": total, "by_action": by_action,
                "by_day": by_day}

    # ─── confirm 态：审批队列（复用 approval.py） ───

    def enqueue_confirm(self, action: str, report: Optional[Dict[str, Any]],
                        run_real: Optional[Callable[[], Any]]) -> Dict[str, Any]:
        """候选进入审批队列（L1 pending_review）；人工批准后提交。

        入队前记录 before 版本快照与回归门禁预检结果（提交时强制复核）。
        """
        action = normalize_action(action)
        candidates = report_candidates(action, report)
        if not candidates:
            return {"action": action, "mode": MODE_CONFIRM,
                    "status": "no_candidates", "candidates": []}
        snapshots: Dict[str, Dict[str, Any]] = {}
        for cand in candidates:
            skill_id = cand.get("skill_id")
            skill = self._get_skill(skill_id)
            snapshots[skill_id] = {
                "before_version": getattr(skill, "version", None) if skill else None,
                "params_snapshot": (dict(skill.default_params or {})
                                    if skill is not None else None),
            }
        regs: Dict[str, Optional[Dict[str, Any]]] = {}
        for cand in candidates:
            regs[cand.get("skill_id")] = self._check_regression(cand.get("skill_id"))
        payload = {
            "candidates": [dict(c) for c in candidates],
            "snapshots": snapshots,
            "regression": regs,
        }
        applier = self._build_confirm_applier(action, candidates, regs, run_real)
        try:
            flow = self._get_approval_flow()
            rec = flow.submit(
                object_type=f"rollout:{action}",
                object_id=f"batch-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                action=f"{action}_rollout_confirm",
                description=f"[rollout-confirm] {action} 批量 {len(candidates)} 个候选待审批",
                payload=payload,
                actor="rollout_controller",
                trigger="scheduler",
                applier=applier,
                level="L1",
            )
        except Exception as e:  # noqa: BLE001 入队失败不中断调度线程
            logger.error("[Rollout] confirm 入队失败 action=%s: %s", action, e)
            return {"action": action, "mode": MODE_CONFIRM,
                    "status": "enqueue_error", "error": str(e)}
        audit_records: List[Dict[str, Any]] = []
        for cand in candidates:
            skill_id = cand.get("skill_id")
            audit_records.append(self._audit(
                action=action, mode=MODE_CONFIRM,
                candidate_id=_new_candidate_id(),
                object_id=skill_id,
                approval_record_id=rec.record_id,
                decision=DECISION_PREVIEW,
                before_version=snapshots[skill_id]["before_version"],
                after_version=None,
                regression_result=regs.get(skill_id),
                detail={"reason": cand.get("reason", ""), "candidate": cand,
                        "status": "submitted_for_approval"}))
        logger.info("[Rollout] confirm 入队 action=%s candidates=%d approval=%s "
                    "（未审批零提交）", action, len(candidates), rec.record_id)
        return {"action": action, "mode": MODE_CONFIRM,
                "status": "pending_approval",
                "approval_record_id": rec.record_id,
                "candidates": len(candidates),
                "audit_records": len(audit_records)}

    def _build_confirm_applier(self, action: str, candidates: List[Dict[str, Any]],
                               regs: Dict[str, Optional[Dict[str, Any]]],
                               run_real: Optional[Callable[[], Any]]) -> Callable[[], Any]:
        """审批 merge 时执行的真实提交闭包：提交前强制回归门禁（G5 第一层）。"""

        def applier() -> Any:
            failed = [c.get("skill_id") for c in candidates
                      if (regs.get(c.get("skill_id")) or {}).get("status") == "FAIL"]
            mode = regression_gate_mode()
            if failed and mode == "enforce":
                raise RolloutGateError(
                    f"[rollout-confirm] 回归门禁拦截退化候选 {failed}（mode={mode}）")
            if run_real is None:
                raise RolloutGateError("[rollout-confirm] 未配置真实执行器 run_real")
            return run_real()

        return applier

    def approve(self, action: str, approval_record_id: str, *,
                actor: str = "reviewer", note: str = "") -> Dict[str, Any]:
        """人工批准（confirm 态）：审批通过 → merge（执行 applier）→ 审计 approved。

        越级拦截：非 confirm/rollout 模式（或总开关关闭）时拒绝。
        """
        action = normalize_action(action)
        if not self.can_commit(action):
            raise RolloutGateError(
                f"action={action} 当前模式 {self.mode_for(action)} 不允许提交"
                "（需 confirm/rollout 且总开关开启）")
        flow = self._get_approval_flow()
        rec = flow.get(approval_record_id)
        payload = (rec.payload or {}) if rec else {}
        flow.approve(approval_record_id, actor=actor, note=note)
        try:
            flow.merge(approval_record_id, actor=actor)
        except Exception as e:  # noqa: BLE001 合并被拦截（如回归门禁）
            logger.warning("[Rollout] merge 被拦截 approval=%s: %s",
                           approval_record_id, e)
            try:
                # 审批记录 approved → archived（标记未提交原因），避免悬挂在 approved
                flow.mark_manual_executed(
                    approval_record_id, actor=actor,
                    note=f"merge 被拦截（未提交）: {e}")
            except Exception:  # noqa: BLE001 归档失败不掩盖原始错误
                pass
            regs = payload.get("regression") or {}
            for cand in payload.get("candidates", []):
                skill_id = cand.get("skill_id")
                self._audit(action=action, mode=MODE_CONFIRM,
                            candidate_id=_new_candidate_id(),
                            object_id=skill_id,
                            approval_record_id=approval_record_id,
                            decision=DECISION_REJECTED,
                            regression_result=regs.get(skill_id),
                            detail={"candidate": cand, "error": str(e)})
            raise RolloutGateError(f"审批合并被拦截: {e}") from e
        audit_records: List[Dict[str, Any]] = []
        for cand in payload.get("candidates", []):
            skill_id = cand.get("skill_id")
            snap = (payload.get("snapshots") or {}).get(skill_id, {})
            audit_records.append(self._audit(
                action=action, mode=MODE_CONFIRM,
                candidate_id=_new_candidate_id(),
                object_id=skill_id,
                approval_record_id=approval_record_id,
                decision=DECISION_APPROVED,
                before_version=snap.get("before_version"),
                after_version=self._skill_version(skill_id),
                regression_result=(payload.get("regression") or {}).get(skill_id),
                detail={"candidate": cand,
                        "params_snapshot": snap.get("params_snapshot")}))
        logger.info("[Rollout] approve 生效 action=%s approval=%s 审计 %d 条",
                    action, approval_record_id, len(audit_records))
        return {"action": action, "mode": MODE_CONFIRM, "status": "approved",
                "approval_record_id": approval_record_id,
                "audit_records": len(audit_records)}

    def reject(self, action: str, approval_record_id: str, *,
               actor: str = "reviewer", reason: str = "") -> Dict[str, Any]:
        """人工驳回（confirm 态）：审批记录 → rejected + 审计。"""
        action = normalize_action(action)
        if not reason.strip():
            raise RolloutError("reject 必须提供 reason（审计要求）")
        flow = self._get_approval_flow()
        flow.reject(approval_record_id, actor=actor, reason=reason)
        self._audit(action=action, mode=MODE_CONFIRM,
                    approval_record_id=approval_record_id,
                    decision=DECISION_REJECTED,
                    detail={"reason": reason})
        return {"action": action, "mode": MODE_CONFIRM, "status": "rejected",
                "approval_record_id": approval_record_id}

    # ─── rollout 态：比例命中 + KPI 自动回退 ───

    def run_rollout(self, action: str, dry_runner: Callable[[], Any],
                    run_real: Optional[Callable[[], Any]]) -> Dict[str, Any]:
        """rollout 态调度：KPI 观察期判据 → 比例命中 → 真实提交（回归门禁在前）。

        流程:
            1. G5 第二层：KPI 连续恶化 → 自动回退上一版本 + 告警（本轮不新提交）；
            2. 比例命中（rollout_ratio）：未命中 → 预演零提交（审计 skipped_by_ratio）；
            3. 命中 → 候选预检（任务1 回归门禁）→ 真实提交 → 审计 approved。
        """
        action = normalize_action(action)
        window = self.kpi_window(action)
        weekly = self._weekly_kpis()
        kpi = judge_kpi_degradation(weekly, window)
        if kpi.get("triggered"):
            rolled = self.auto_rollback(action, kpi)
            return {"action": action, "mode": MODE_ROLLOUT,
                    "status": "kpi_degraded_rolled_back",
                    "kpi": kpi, "rolled_back": rolled}
        ratio = self.rollout_ratio(action)
        if self._rng.random() >= ratio:
            report = dry_runner()
            self._audit(action=action, mode=MODE_ROLLOUT,
                        decision=DECISION_PREVIEW,
                        detail={"status": "skipped_by_ratio", "ratio": ratio,
                                "report_summary": _report_summary(action, report)})
            logger.info("[Rollout] rollout 未命中 action=%s ratio=%s（预演零提交）",
                        action, ratio)
            return {"action": action, "mode": MODE_ROLLOUT,
                    "status": "skipped_by_ratio", "ratio": ratio,
                    "report": report}
        # 命中 → 真实提交（先快照 before 版本/参数，供回滚与审计）
        report = dry_runner()
        candidates = report_candidates(action, report)
        snapshots: Dict[str, Dict[str, Any]] = {}
        for cand in candidates:
            skill_id = cand.get("skill_id")
            skill = self._get_skill(skill_id)
            snapshots[skill_id] = {
                "before_version": getattr(skill, "version", None) if skill else None,
                "params_snapshot": (dict(skill.default_params or {})
                                    if skill is not None else None),
            }
        regs: Dict[str, Optional[Dict[str, Any]]] = {}
        for cand in candidates:
            regs[cand.get("skill_id")] = self._check_regression(cand.get("skill_id"))
        failed = [c.get("skill_id") for c in candidates
                  if (regs.get(c.get("skill_id")) or {}).get("status") == "FAIL"]
        if failed and regression_gate_mode() == "enforce":
            for cand in candidates:
                skill_id = cand.get("skill_id")
                if skill_id in failed:
                    self._audit(action=action, mode=MODE_ROLLOUT,
                                candidate_id=_new_candidate_id(),
                                object_id=skill_id,
                                decision=DECISION_REJECTED,
                                before_version=snapshots[skill_id]["before_version"],
                                regression_result=regs.get(skill_id),
                                detail={"candidate": cand,
                                        "status": "regression_gate_blocked"})
            logger.warning("[Rollout] rollout 回归门禁拦截 action=%s failed=%s",
                           action, failed)
            return {"action": action, "mode": MODE_ROLLOUT,
                    "status": "regression_blocked", "failed": failed}
        real = run_real() if run_real is not None else report
        committed = self._audit_rollout_commit(action, candidates, snapshots, kpi)
        logger.info("[Rollout] rollout 命中提交 action=%s committed=%d",
                    action, len(committed))
        return {"action": action, "mode": MODE_ROLLOUT, "status": "committed",
                "committed": len(committed), "report": real}

    def _audit_rollout_commit(self, action: str, candidates: List[Dict[str, Any]],
                              snapshots: Dict[str, Dict[str, Any]],
                              kpi: Dict[str, Any]) -> List[Dict[str, Any]]:
        """rollout 命中提交后的逐候选审计（decision=approved，含版本与参数快照）。"""
        records: List[Dict[str, Any]] = []
        kpi_latest = _kpi_latest_week(kpi)
        for cand in candidates:
            skill_id = cand.get("skill_id")
            snap = snapshots.get(skill_id, {})
            records.append(self._audit(
                action=action, mode=MODE_ROLLOUT,
                candidate_id=_new_candidate_id(),
                object_id=skill_id,
                decision=DECISION_APPROVED,
                before_version=snap.get("before_version"),
                after_version=self._skill_version(skill_id),
                kpi_snapshot=kpi_latest,
                detail={"candidate": cand, "committed": True,
                        "params_snapshot": snap.get("params_snapshot")}))
        return records

    def auto_rollback(self, action: str, kpi: Dict[str, Any]) -> List[Dict[str, Any]]:
        """G5 第二层：KPI 连续恶化 → 对最近 approved 候选自动回退上一版本并告警。"""
        action = normalize_action(action)
        committed = self._recent_approved_candidates(action)
        results: List[Dict[str, Any]] = []
        for item in committed:
            try:
                results.append(self.rollback(
                    action, item["candidate_id"],
                    reason=f"KPI 连续恶化自动回退: {kpi.get('reason', '')}",
                    kpi_snapshot=kpi))
            except Exception as e:  # noqa: BLE001 单个回退失败不阻断批量
                logger.error("[Rollout] 自动回退失败 candidate=%s: %s",
                             item.get("candidate_id"), e)
        logger.warning("[Rollout] KPI 恶化自动回退 action=%s count=%d kpi=%s",
                       action, len(results), kpi.get("detail", {}))
        return results

    def _recent_approved_candidates(self, action: str) -> List[Dict[str, Any]]:
        """审计日志中该动作最近 approved 的已提交候选（含回滚所需快照）。"""
        out: List[Dict[str, Any]] = []
        for rec in reversed(self._audit_records()):
            if rec.get("action") != action:
                continue
            if rec.get("decision") != DECISION_APPROVED:
                continue
            cand_id = rec.get("candidate_id")
            if not cand_id:
                continue
            detail = rec.get("detail") or {}
            skill_id = rec.get("object_id") or (
                (detail.get("candidate") or {}).get("skill_id"))
            if not skill_id:
                continue
            out.append({
                "candidate_id": cand_id,
                "skill_id": skill_id,
                "before_version": rec.get("before_version"),
                "params_snapshot": detail.get("params_snapshot"),
            })
            if len(out) >= 20:
                break
        return out

    # ─── 回滚 ───

    def rollback(self, action: str, candidate_id: str, *,
                 reason: str = "",
                 kpi_snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """一键回滚：内容回滚（rollback_version）+ 参数快照恢复（快照比对一致）。

        回滚命令写审计（rollback_command），供人工复核与复现。
        """
        action = normalize_action(action)
        rec = self._audit_get(candidate_id)
        if rec is None:
            raise RolloutError(f"审计记录不存在: candidate_id={candidate_id}")
        detail = rec.get("detail") or {}
        cand = detail.get("candidate") or {}
        skill_id = (rec.get("object_id") or cand.get("skill_id"))
        if not skill_id:
            raise RolloutError(f"审计记录缺少 skill_id: candidate_id={candidate_id}")
        before_version = rec.get("before_version")
        params = detail.get("params_snapshot")
        restored = False
        svc = self._get_service()
        if before_version:
            try:
                svc.rollback_version(skill_id, target_version=before_version)
                restored = True
            except Exception as e:  # noqa: BLE001 回滚失败记录不致命
                logger.error("[Rollout] rollback_version 失败 %s→%s: %s",
                             skill_id, before_version, e)
        skill = self._get_skill(skill_id)
        if params is not None and skill is not None:
            skill.default_params = dict(params)
            svc.store.upsert(skill)
            restored = True
        cmd = (f"python -m agent.learning.rollout_controller "
               f"--action {action} --rollback --candidate {candidate_id}")
        self._audit(action=action, mode=self.mode_for(action) or MODE_ROLLOUT,
                    candidate_id=candidate_id, object_id=skill_id,
                    decision=DECISION_ROLLED_BACK,
                    before_version=before_version,
                    after_version=self._skill_version(skill_id),
                    kpi_snapshot=kpi_snapshot,
                    rollback_command=cmd,
                    detail={"reason": reason, "restored": restored})
        logger.warning("[Rollout] 回滚完成 candidate=%s skill=%s →v%s restored=%s",
                       candidate_id, skill_id, before_version, restored)
        return {"candidate_id": candidate_id, "skill_id": skill_id,
                "before_version": before_version, "restored": restored,
                "rollback_command": cmd}

    # ─── 内部：依赖与门禁 ───

    def _get_archive(self) -> Any:
        if self._archive is None:
            from agent.skills_mgmt.lineage import get_default_archive
            self._archive = get_default_archive()
        return self._archive

    def _get_approval_flow(self) -> Any:
        if self._approval_flow is None:
            from agent.skills_mgmt.approval import ApprovalFlow
            self._approval_flow = ApprovalFlow()
        return self._approval_flow

    def _get_service(self) -> Any:
        if self._service is None:
            from agent.skills_mgmt.service import SkillsMgmtService
            self._service = SkillsMgmtService()
        return self._service

    def _get_skill(self, skill_id: str) -> Optional[Any]:
        try:
            return self._get_service().get(skill_id)
        except Exception as e:  # noqa: BLE001 技能读取失败不致命
            logger.debug("[Rollout] 技能读取失败 %s: %s", skill_id, e)
            return None

    def _skill_version(self, skill_id: str) -> Optional[str]:
        skill = self._get_skill(skill_id)
        return getattr(skill, "version", None) if skill else None

    def _check_regression(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """任务1 回归门禁（G5 第一层）：PASS/FAIL/NO_SAMPLES/budget_exceeded/error。

        注入 regression_checker 时直接复用（单测注入桩）；
        默认构建 RegressionGate 真实评估（mode=off 时零调用）。
        """
        if self._regression_checker is not None:
            try:
                return self._regression_checker(skill_id)
            except Exception as e:  # noqa: BLE001
                return {"status": "error", "error": str(e)}
        if regression_gate_mode() == "off":
            return None
        try:
            from agent.skills_mgmt.eval_regression import RegressionGate
            skill = self._get_service().store.get(skill_id)
            if skill is None:
                return {"status": "NO_SAMPLES", "reason": "技能不存在"}
            res = RegressionGate().evaluate(skill)
            return res.to_dict()
        except Exception as e:  # noqa: BLE001 门禁异常 → error 记录，绝不伪造
            logger.warning("[Rollout] 回归门禁调用失败 %s: %s", skill_id, e)
            return {"status": "error", "error": str(e)}

    def _weekly_kpis(self) -> List[Dict[str, Any]]:
        """周级 KPI 行（任务2 监控数据源）；不可用时返回空（判据保守不触发）。"""
        if self._kpi_provider is not None:
            try:
                rows = self._kpi_provider()
                return list(rows) if rows else []
            except Exception as e:  # noqa: BLE001
                logger.debug("[Rollout] kpi_provider 失败: %s", e)
                return []
        try:
            from agent.learning_metrics import get_learning_metrics
            metrics = get_learning_metrics()
            window = max([self.kpi_window(a) for a in ACTIONS]) + 1
            return metrics.get_weekly_kpis(weeks=window)
        except Exception as e:  # noqa: BLE001
            logger.debug("[Rollout] 周级 KPI 不可用（判据保守不触发）: %s", e)
            return []

    # ─── 审计 ───

    def _audit(self, action: str, mode: str, *, candidate_id: Optional[str] = None,
               object_id: Optional[str] = None,
               parent_record_id: Optional[str] = None,
               approval_record_id: Optional[str] = None,
               decision: str = DECISION_PREVIEW,
               before_version: Optional[str] = None,
               after_version: Optional[str] = None,
               regression_result: Optional[Dict[str, Any]] = None,
               kpi_snapshot: Optional[Dict[str, Any]] = None,
               rollback_command: Optional[str] = None,
               detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """统一审计字段写入（data/learning/rollout_audit.jsonl 追加 JSONL）。"""
        detail = dict(detail or {})
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": "rollout_action",
            "action": action,
            "mode": mode,
            "candidate_id": candidate_id,
            "object_id": object_id or (detail.get("candidate") or {}).get("skill_id"),
            "parent_record_id": parent_record_id,
            "approval_record_id": approval_record_id,
            "decision": decision,
            "before_version": before_version,
            "after_version": after_version,
            "regression_result": regression_result,
            "kpi_snapshot": kpi_snapshot,
            "rollback_command": rollback_command,
            "detail": detail,
        }
        try:
            path = self._audit_path
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("[Rollout] 审计日志写入失败: %s", e)
        return rec

    def _audit_records(self) -> List[Dict[str, Any]]:
        """读取全部放行审计记录（损坏行跳过）。"""
        if not self._audit_path.exists():
            return []
        records: List[Dict[str, Any]] = []
        try:
            with open(self._audit_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        continue
        except OSError as e:
            logger.warning("[Rollout] 审计日志读取失败: %s", e)
        return records

    def _audit_get(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        for rec in reversed(self._audit_records()):
            if rec.get("candidate_id") == candidate_id:
                return rec
        return None

    def _write_archive_preview(self, action: str, skill_id: str,
                               cand: Dict[str, Any]) -> Optional[str]:
        """谱系 preview 记录（EvolutionArchive，decision=preview）。"""
        try:
            from agent.skills_mgmt.lineage import EvolutionRecord
            rec = EvolutionRecord(
                object_type="skill",
                object_id=skill_id,
                parent_record_id=None,
                strategy=f"rollout:{action}",
                change_summary=(f"[rollout-observe] {action} 预演（零提交）: "
                                f"{cand.get('reason', '')}"),
                decision=DECISION_PREVIEW,
                decision_reason="observe 预演，未放行",
                trigger="scheduler",
                actor="system",
            )
            return self._get_archive().append(rec)
        except Exception as e:  # noqa: BLE001 谱系写入失败不阻断预演
            logger.warning("[Rollout] 谱系 preview 写入失败 %s/%s: %s",
                           action, skill_id, e)
            return None


def _kpi_latest_week(kpi: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从判据结果提取最新周 KPI 快照（审计 kpi_snapshot 用）。"""
    if not kpi:
        return None
    detail = kpi.get("detail") or {}
    if not detail:
        return {"triggered": kpi.get("triggered", False)}
    last_week = sorted(detail.keys())[-1]
    return {"triggered": kpi.get("triggered", False), "week": last_week,
            "degraded": detail[last_week]}


# ════════════════════════════════════════════════════════════
#  CLI（人工确认流程入口）
# ════════════════════════════════════════════════════════════


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="L2 自进化闭环受控放行（任务3）— 放行状态机 / 人工确认 / 回滚")
    parser.add_argument("--action", choices=list(ACTIONS), default=None,
                        help="进化动作（feedback/evolution/lifecycle）")
    parser.add_argument("--status", action="store_true",
                        help="查看各动作放行状态（模式/总开关/比例/窗口）")
    parser.add_argument("--run-scheduled", action="store_true",
                        help="按当前模式执行一轮调度（人工触发，等价调度出口）")
    parser.add_argument("--preview", action="store_true",
                        help="observe 预演：dry_run 报告写谱系 preview + 审计")
    parser.add_argument("--preview-stats", action="store_true",
                        help="预演采纳率统计（observe 数据源，喂任务2 监控）")
    parser.add_argument("--approve", metavar="APPROVAL_RECORD_ID",
                        help="人工批准 confirm 审批记录（需 --action）")
    parser.add_argument("--reject", metavar="APPROVAL_RECORD_ID",
                        help="人工驳回 confirm 审批记录（需 --action 与 --reason）")
    parser.add_argument("--rollback", action="store_true",
                        help="一键回滚候选（需 --action 与 --candidate）")
    parser.add_argument("--candidate", metavar="CANDIDATE_ID",
                        help="回滚目标候选 ID（审计 candidate_id）")
    parser.add_argument("--reason", default="",
                        help="驳回原因 / 回滚原因（审计要求）")
    parser.add_argument("--actor", default="reviewer", help="操作者标识")
    parser.add_argument("--audit-file", default=None,
                        help="放行审计路径覆盖（默认读配置）")
    parser.add_argument("--approval-records", default=None,
                        help="审批记录路径覆盖（默认读 APPROVAL_RECORDS_PATH）")
    args = parser.parse_args(argv)

    kwargs = {}
    if args.audit_file:
        kwargs["audit_path"] = args.audit_file
    if args.approval_records:
        from agent.skills_mgmt.approval import ApprovalFlow
        kwargs["approval_flow"] = ApprovalFlow(
            records_path=args.approval_records)
    ctrl = RolloutController(**kwargs)

    try:
        if args.status:
            print(json.dumps(ctrl.status(), ensure_ascii=False, indent=2))
            return 0
        if args.preview_stats:
            print(json.dumps(ctrl.preview_stats(args.action), ensure_ascii=False,
                             indent=2))
            return 0
        if args.action is None:
            parser.error("--action 必填（除 --status / --preview-stats 外）")
        if args.approve:
            out = ctrl.approve(args.action, args.approve, actor=args.actor,
                               note=args.reason)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        if args.reject:
            out = ctrl.reject(args.action, args.reject, actor=args.actor,
                              reason=args.reason)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        if args.rollback:
            if not args.candidate:
                parser.error("--rollback 需 --candidate")
            out = ctrl.rollback(args.action, args.candidate, reason=args.reason)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        if args.preview:
            runners = _executor_runners(args.action)
            report = runners["dry_runner"]()
            records = ctrl.record_preview(args.action, report)
            print(json.dumps({"action": args.action, "mode": MODE_OBSERVE,
                              "previews": len(records)},
                             ensure_ascii=False, indent=2))
            return 0
        if args.run_scheduled:
            runners = _executor_runners(args.action)
            out = ctrl.run_scheduled(args.action, dry_runner=runners["dry_runner"],
                                     run_real=runners["run_real"])
            print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
            return 0
    except RolloutError as e:
        print(f"错误: {e}", file=__import__("sys").stderr)
        return 2
    parser.error("请指定操作（--status/--run-scheduled/--preview/--approve/"
                 "--reject/--rollback/--preview-stats）")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__: List[str] = [
    "RolloutController", "RolloutError", "RolloutGateError",
    "MODE_DRY_RUN", "MODE_OBSERVE", "MODE_CONFIRM", "MODE_ROLLOUT",
    "MODES", "ACTIONS", "DECISION_PREVIEW", "DECISION_APPROVED",
    "DECISION_REJECTED", "DECISION_ROLLED_BACK", "DECISIONS",
    "DEFAULT_AUDIT_FILE", "DEFAULT_MASTER_ENABLED", "DEFAULT_MODE",
    "DEFAULT_ROLLOUT_RATIO", "DEFAULT_KPI_WINDOW_WEEKS",
    "DEFAULT_REGRESSION_GATE",
    "master_enabled", "mode_for", "rollout_ratio",
    "kpi_rollback_window_weeks", "regression_gate_mode", "audit_file",
    "normalize_action", "report_candidates", "judge_kpi_degradation",
    "_executor_runners", "main",
]
