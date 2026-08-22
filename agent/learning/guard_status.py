"""护栏状态服务（任务4 Step 4）— 聚合 G1-G5 可查询状态

【背景（Why）】
    G2-G5 构件（value_guard.py / EvolutionArchive / approval.py / rollback.py /
    StagedEvaluator / eval_regression.py）均已存在但分散，决策层无法随时回答
    "当前护栏是否全就绪、最近一次元规则变更是什么、如何回滚"。本模块把 G1-G5
    收口为统一只读状态服务：

        GET /api/learning/guards  →  get_guard_status()

    G1 元规则版本化:  MetaPolicyStore（版本/最近变更/回滚命令/待审批）
    G2 价值观红线:    ValueGuard（启用/LLM 辅助/红线规则数）
    G3 谱系完整性:     EvolutionArchive（记录数/决策分布/父代链接/最近记录）
    G4 回滚与预算:     AutoRollback 阈值 + LearningBudget 模式/熔断
    G5 回归与观察期:   评估集回归门禁配置/基线数/评估集规模 + 触发监控观察窗口

【不易边界】
    - 纯只读聚合：不触发任何写操作、不改变任何既有行为；异常 → 对应护栏
      status=unknown + error 字段，绝不抛给调用方（只读接口契约）；
    - 数据来源全部是既有模块的只读查询（不访问私有写路径）；
    - 每项护栏输出 enabled/status/最近变更/回滚命令（任务4 评估标准）。

【配置】
    GUARD_STATUS_ENABLED        状态服务总开关，默认 true
    config.yaml learning.guard_status.enabled 同义
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ════════════════════════════════════════════════════════════
#  配置（环境变量 > config.yaml learning.guard_status > 默认值）
# ════════════════════════════════════════════════════════════

_ENV_ENABLED = "GUARD_STATUS_ENABLED"

_CONFIG_YAML_CACHE: Optional[Dict[str, Any]] = None
_CONFIG_YAML_LOCK = threading.Lock()


def _config_yaml() -> Optional[Dict[str, Any]]:
    global _CONFIG_YAML_CACHE
    if _CONFIG_YAML_CACHE is not None:
        return _CONFIG_YAML_CACHE or None
    with _CONFIG_YAML_LOCK:
        if _CONFIG_YAML_CACHE is not None:
            return _CONFIG_YAML_CACHE or None
        try:
            path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
            if path.exists():
                import yaml as _yaml
                with open(path, "r", encoding="utf-8") as f:
                    _CONFIG_YAML_CACHE = _yaml.safe_load(f) or {}
                    return _CONFIG_YAML_CACHE
        except Exception:  # noqa: BLE001 配置解析失败零影响
            pass
        _CONFIG_YAML_CACHE = {}
        return None


def _enabled() -> bool:
    v = os.getenv(_ENV_ENABLED)
    if v is not None and str(v).strip():
        return str(v).strip().lower() in ("1", "true", "yes", "on")
    cfg = _config_yaml()
    if cfg is not None:
        section = ((cfg.get("learning", {}) or {}).get("guard_status", {}) or {})
        val = section.get("enabled")
        if val is not None:
            return str(val).strip().lower() in ("1", "true", "yes", "on")
    return True


def _cfg_get(*keys: str, default: Any = None) -> Any:
    """按路径读 config.yaml（含 learning. 前缀写法）"""
    cfg = _config_yaml()
    if cfg is None:
        return default
    node: Any = cfg
    for key in keys:
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return default
    return node if node is not None else default


# ════════════════════════════════════════════════════════════
#  内部：单项护栏数据采集（全部 try/except 降级，绝不抛异常）
# ════════════════════════════════════════════════════════════

def _safe(fn, default: Any = None):
    """只读采集安全包装：异常 → 默认值（不抛给调用方）"""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)} if default is None else default


def _g1_meta_policy() -> Dict[str, Any]:
    """G1 元规则版本化状态（数据源: MetaPolicyStore.status()）"""
    def _collect() -> Dict[str, Any]:
        from agent.learning.meta_policy import get_meta_policy_store
        st = get_meta_policy_store().status()
        pending = st.get("pending")
        return {
            "enabled": bool(st.get("enabled", True)),
            "status": "ready",
            "detail": (
                f"元规则登记 {st.get('schema_entries')} 项，当前版本 "
                f"{st.get('current_version')}（生效 {st.get('effective_at') or '-'}）"
                + (f"，待审批变更 {pending.get('version')}"
                   if pending and pending.get("status") == "pending" else "")),
            "latest_change": {
                "change_id": st.get("last_change_id"),
                "description": st.get("last_change_description"),
                "effective_at": st.get("effective_at"),
            },
            "rollback_command": st.get("rollback_command"),
            "current_version": st.get("current_version"),
            "schema_entries": st.get("schema_entries"),
            "schema_path": st.get("schema_path"),
            "versions_count": st.get("versions_count"),
            "pending": pending,
            "approval_level": st.get("approval_level"),
            "store_dir": st.get("store_dir"),
        }
    data = _safe(_collect)
    if isinstance(data, dict) and "error" in data:
        data = {"enabled": True, "status": "unknown", "detail": data["error"],
                "latest_change": None, "rollback_command": None}
    data.setdefault("id", "G1")
    data.setdefault("name", "元规则版本化（meta-policy）")
    return data


def _g2_value_guard() -> Dict[str, Any]:
    """G2 价值观红线状态（数据源: ValueGuard）"""
    def _collect() -> Dict[str, Any]:
        from agent.skills_mgmt.value_guard import ValueGuard, _env_enabled, _env_llm_enabled
        guard = ValueGuard()
        rules = getattr(guard, "_rules", []) or []
        critical = [r for r in rules if r.get("severity") == "critical"]
        return {
            "enabled": bool(getattr(guard, "_enabled", _env_enabled())),
            "llm_enabled": bool(getattr(guard, "_use_llm", False))
                           or bool(_env_llm_enabled()),
            "rules_count": len(rules),
            "critical_rules_count": len(critical),
            "categories": sorted({r.get("category") for r in rules if r.get("category")}),
            "status": "ready" if rules else "degraded",
            "detail": (f"红线规则 {len(rules)} 条（critical {len(critical)} 条，"
                       f"类别 {len({r.get('category') for r in rules})} 类）"),
            "latest_change": None,
            "rollback_command": "恢复默认红线：清空 VALUE_GUARD_RULES_PATH",
        }
    data = _safe(_collect)
    if isinstance(data, dict) and "error" in data:
        data = {"enabled": True, "status": "unknown", "detail": data["error"],
                "latest_change": None, "rollback_command": None}
    data.setdefault("id", "G2")
    data.setdefault("name", "价值观红线（value_guard）")
    return data


def _g3_lineage() -> Dict[str, Any]:
    """G3 谱系完整性状态（数据源: EvolutionArchive + approval 审计）"""
    def _collect() -> Dict[str, Any]:
        from agent.skills_mgmt.lineage import get_default_archive
        archive = get_default_archive()
        total = _safe(lambda: archive.count(), 0)
        by_decision: Dict[str, int] = {}
        linked = 0
        latest: Optional[Dict[str, Any]] = None
        try:
            for rec in _safe(lambda: archive.query(), []):
                by_decision[rec.decision] = by_decision.get(rec.decision, 0) + 1
                if rec.parent_record_id:
                    linked += 1
                if latest is None or rec.created_at > latest["created_at"]:
                    latest = {
                        "record_id": rec.record_id,
                        "object_type": rec.object_type,
                        "object_id": rec.object_id,
                        "decision": rec.decision,
                        "created_at": rec.created_at,
                    }
        except Exception:  # noqa: BLE001 查询失败按空统计
            pass
        return {
            "enabled": True,
            "status": "ready" if total else "empty",
            "detail": (f"谱系记录 {total} 条（含父代链接 {linked} 条），"
                       f"决策分布 {json.dumps(by_decision, ensure_ascii=False)}"),
            "latest_change": latest,
            "rollback_command": "回滚进化产物：复用 agent/skills_mgmt/rollback.py",
            "records_count": total,
            "linked_records": linked,
            "by_decision": by_decision,
        }
    data = _safe(_collect)
    if isinstance(data, dict) and "error" in data:
        data = {"enabled": True, "status": "unknown", "detail": data["error"],
                "latest_change": None, "rollback_command": None}
    data.setdefault("id", "G3")
    data.setdefault("name", "谱系与档案（EvolutionArchive）")
    return data


def _g4_rollback_budget() -> Dict[str, Any]:
    """G4 回滚能力与预算模式（数据源: AutoRollback 配置 + LearningBudget）"""
    def _collect() -> Dict[str, Any]:
        from agent.learning_budget import get_learning_budget
        from agent.skills_mgmt.rollback import (
            _env_error_rise_pct, _env_latency_rise_pct, _env_max_daily,
            _env_success_drop_pct, _env_window_min,
        )
        budget = _safe(lambda: get_learning_budget().get_status(), {})
        budget_mode = str((budget or {}).get("mode") or _cfg_get(
            "learning", "budget", "mode", default="warn_only"))
        return {
            "enabled": True,
            "status": "ready",
            "detail": (
                f"自动回滚阈值: 成功率降>{_env_success_drop_pct()}% / "
                f"P95 升>{_env_latency_rise_pct()}% / 异常率升>{_env_error_rise_pct()}% "
                f"（窗口 {_env_window_min()} 分钟，日上限 {_env_max_daily()} 次）；"
                f"预算模式 {budget_mode}"),
            "latest_change": None,
            "rollback_command": "回滚上一进化版本：复用 AutoRollback / rollback.py",
            "budget_mode": budget_mode,
            "budget_tripped": bool((budget or {}).get("tripped", False)),
            "max_daily": _env_max_daily(),
            "success_drop_pct": _env_success_drop_pct(),
            "latency_rise_pct": _env_latency_rise_pct(),
            "error_rise_pct": _env_error_rise_pct(),
            "window_min": _env_window_min(),
            "max_daily_tokens": (budget or {}).get("max_daily_tokens"),
        }
    data = _safe(_collect)
    if isinstance(data, dict) and "error" in data:
        data = {"enabled": True, "status": "unknown", "detail": data["error"],
                "latest_change": None, "rollback_command": None}
    data.setdefault("id", "G4")
    data.setdefault("name", "回滚能力与预算（rollback + budget）")
    return data


def _g5_regression_observe() -> Dict[str, Any]:
    """G5 回归门禁与观察期（数据源: eval_regression 配置 + 触发监控配置）"""
    def _collect() -> Dict[str, Any]:
        from agent.skills_mgmt.eval_regression import (
            BaselineStore, SamplesetRegistry,
            _degrade_threshold, _default_budget, _default_set,
        )
        from agent.skills_mgmt.offline_evolver import _env_regression_gate_mode
        gate_mode = str(_env_regression_gate_mode())
        status = {"off": "disabled", "warn_only": "watching",
                  "enforce": "ready"}.get(gate_mode, "unknown")
        # 评估集规模（manifest 样本 id 总数）
        registry = SamplesetRegistry()
        manifest = _safe(lambda: _read_manifest(registry), {})
        sample_count = int((manifest or {}).get("sample_count", 0))
        # 回归基线数（已建基线的技能 × 版本）
        baseline_count = 0
        try:
            bl = BaselineStore()
            data = _safe(lambda: _read_baselines(bl), {})
            baseline_count = sum(len(v or {}) for v in (data or {}).values())
        except Exception:  # noqa: BLE001
            pass
        window_weeks = int(_cfg_get("learning", "metrics", "trigger_monitoring",
                                    "window_weeks", default=4) or 4)
        replay_threshold = float(_cfg_get(
            "learning", "metrics", "trigger_monitoring",
            "replay_coverage_threshold", default=0.5) or 0.5)
        return {
            "enabled": True,
            "status": status,
            "detail": (
                f"回归门禁模式 {gate_mode}（退化阈值 {_degrade_threshold()}，"
                f"样本集 {_default_set()}，预算 {_default_budget()} token）；"
                f"评估集样本 {sample_count} 条，已建基线 {baseline_count} 项；"
                f"观察期窗口 {window_weeks} 周（回放覆盖率阈值 {replay_threshold}）"),
            "latest_change": None,
            "rollback_command": ("python -m agent.skills_mgmt.eval_regression "
                                 "--skill <id> --set v1  # 复查回归状态"),
            "gate_mode": gate_mode,
            "degrade_threshold": _degrade_threshold(),
            "sampleset_version": _default_set(),
            "budget_tokens": _default_budget(),
            "eval_sample_count": sample_count,
            "baseline_count": baseline_count,
            "observation_window_weeks": window_weeks,
            "replay_coverage_threshold": replay_threshold,
        }
    data = _safe(_collect)
    if isinstance(data, dict) and "error" in data:
        data = {"enabled": True, "status": "unknown", "detail": data["error"],
                "latest_change": None, "rollback_command": None}
    data.setdefault("id", "G5")
    data.setdefault("name", "回归门禁与观察期（eval_regression + 触发监控）")
    return data


def _read_manifest(registry: Any) -> Dict[str, Any]:
    """读取样本集 manifest 并统计样本 id 总数（只读）"""
    import json as _json
    path = registry.path
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = _json.load(f) or {}
    ids = set()
    for ver, spec in (data.get("versions") or {}).items():
        for cat, lst in (spec.get("categories") or {}).items():
            if isinstance(lst, list):
                ids.update(str(i) for i in lst)
    return {"sample_count": len(ids)}


def _read_baselines(store: Any) -> Dict[str, Any]:
    """读取回归基线（只读；不触发任何写）"""
    data = getattr(store, "_load", lambda: {})() if hasattr(store, "_load") else {}
    return (data or {}).get("baselines") or {}


def _rollout_modes() -> Dict[str, Any]:
    """任务3 放行模式（如已交付）— 未配置时降级为 not_configured，不影响护栏判定"""
    cfg = _config_yaml()
    if cfg is None:
        return {"configured": False, "detail": "任务3 未交付（learning.rollout 未配置）"}
    rollout = ((cfg.get("learning", {}) or {}).get("rollout", {}) or {})
    if not rollout:
        return {"configured": False, "detail": "任务3 未交付（learning.rollout 未配置）"}
    modes = {k: (v or {}).get("mode") for k, v in rollout.items()
             if isinstance(v, dict)}
    return {"configured": True, "modes": modes,
            "detail": f"放行模式: {json.dumps(modes, ensure_ascii=False)}"}


# ════════════════════════════════════════════════════════════
#  聚合入口
# ════════════════════════════════════════════════════════════

_GUARD_BUILDERS = (
    ("G1", _g1_meta_policy),
    ("G2", _g2_value_guard),
    ("G3", _g3_lineage),
    ("G4", _g4_rollback_budget),
    ("G5", _g5_regression_observe),
)


def get_guard_status() -> Dict[str, Any]:
    """G1-G5 护栏状态聚合（只读；异常零影响主链路）

    Returns:
        {
          generated_at, enabled, summary: {total_guards, ready, watching,
          degraded, disabled, unknown, all_ready},
          guards: {G1..G5: {...}},
          rollout: 任务3 放行模式（未配置则 not_configured）
        }
    """
    if not _enabled():
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "enabled": False,
            "summary": {"note": "GUARD_STATUS_ENABLED=false，状态服务关闭"},
            "guards": {},
        }
    guards: Dict[str, Dict[str, Any]] = {}
    counter = {"ready": 0, "watching": 0, "degraded": 0,
               "disabled": 0, "unknown": 0, "empty": 0}
    for gid, builder in _GUARD_BUILDERS:
        item = builder()
        if not isinstance(item, dict) or "status" not in item:
            # 防御：builder 异常/畸形输出 → 统一降级为 unknown（绝不抛给调用方）
            err = (item or {}).get("error", "builder 输出异常") \
                if isinstance(item, dict) else "builder 输出异常"
            item = {"id": gid, "enabled": True, "status": "unknown",
                    "detail": str(err), "latest_change": None,
                    "rollback_command": None, "error": str(err)}
        item.setdefault("id", gid)
        status = str(item.get("status") or "unknown")
        counter[status] = counter.get(status, 0) + 1
        guards[gid] = item
    summary = {
        "total_guards": len(guards),
        "ready": counter["ready"],
        "watching": counter["watching"],
        "degraded": counter["degraded"],
        "disabled": counter["disabled"],
        "unknown": counter["unknown"],
        "empty": counter["empty"],
        # G1 版本化就绪 + G2-G5 无 unknown/disabled → 全就绪（L3 里程碑门 G1 全就绪判定）
        "all_ready": (
            guards["G1"].get("status") == "ready"
            and all(guards[g].get("status") not in ("unknown", "disabled", "degraded")
                    for g in ("G2", "G3", "G4", "G5"))
        ),
    }
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "enabled": True,
        "summary": summary,
        "guards": guards,
        "rollout": _rollout_modes(),
    }


__all__ = [
    "get_guard_status",
    "_g1_meta_policy", "_g2_value_guard", "_g3_lineage",
    "_g4_rollback_budget", "_g5_regression_observe",
]
