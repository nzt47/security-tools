"""任务3 · L2 自进化闭环受控放行框架测试

覆盖（验收 §6）:
    1. 四态行为：observe 零提交零 KPI / confirm 未审批零提交 / rollout 比例命中
       + KPI 恶化自动回退 / 总开关关闭强制 dry_run
    2. 越级拦截：非 confirm/rollout 模式 approve 被拒；can_commit 统一判定
    3. confirm 提交前回归门禁生效（退化候选被拒）
    4. 审计字段完备：action/mode/candidate_id/decision/版本号/回归结果/KPI 快照/回滚命令
    5. 回滚可用：放行后提交可一键回滚且行为与旧版本一致（快照比对）
    6. 调度出口接线：register_learning_schedulers 包装三类任务 func

守【不易】: 不触碰 offline_evolver/feedback_agent/lifecycle 内部逻辑；
runner 全部注入桩；store/archive/approval/audit 全部 tmp_path 隔离。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from agent.learning.rollout_controller import (
    DECISION_APPROVED,
    DECISION_PREVIEW,
    DECISION_REJECTED,
    DECISION_ROLLED_BACK,
    MODE_CONFIRM,
    MODE_DRY_RUN,
    MODE_OBSERVE,
    MODE_ROLLOUT,
    RolloutController,
    RolloutError,
    RolloutGateError,
    judge_kpi_degradation,
    mode_for,
    normalize_action,
    report_candidates,
)
from agent.learning_metrics import get_learning_metrics, reset_learning_metrics
from agent.skills_mgmt.approval import ApprovalFlow
from agent.skills_mgmt.learning_scheduler import (
    _make_rollout_wrapper,
    _ROLLOUT_TASKS,
    register_learning_schedulers,
    unregister_learning_schedulers,
)
from agent.skills_mgmt.lineage import DECISIONS, EvolutionArchive
from agent.skills_mgmt.models import SkillStatus
from agent.skills_mgmt.service import SkillsMgmtService

ENV_PREFIX = "LEARNING_ROLLOUT"
_ENV_KEYS = (
    "MASTER_ENABLED", "AUDIT_FILE", "REGRESSION_GATE",
    "FEEDBACK_MODE", "FEEDBACK_ROLLOUT_RATIO",
    "FEEDBACK_KPI_ROLLBACK_WINDOW_WEEKS",
    "EVOLUTION_MODE", "EVOLUTION_ROLLOUT_RATIO",
    "EVOLUTION_KPI_ROLLBACK_WINDOW_WEEKS",
    "LIFECYCLE_MODE", "LIFECYCLE_ROLLOUT_RATIO",
    "LIFECYCLE_KPI_ROLLBACK_WINDOW_WEEKS",
)


@pytest.fixture()
def svc(tmp_path):
    return SkillsMgmtService(store_path=str(tmp_path / "skills.json"))


@pytest.fixture()
def audit_path(tmp_path):
    return tmp_path / "rollout_audit.jsonl"


@pytest.fixture()
def archive(tmp_path):
    return EvolutionArchive(
        active_path=str(tmp_path / "archive_active.jsonl"),
        archive_path=str(tmp_path / "archive_old.jsonl"),
        active_generations=50,
    )


@pytest.fixture()
def approval_flow(tmp_path):
    return ApprovalFlow(records_path=str(tmp_path / "approval_records.jsonl"))


@pytest.fixture(autouse=True)
def _env_cleanup(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(f"{ENV_PREFIX}_{key}", raising=False)
    reset_learning_metrics()
    yield
    reset_learning_metrics()


def _add_skill(svc, skill_id: str, *, params=None, usage: int = 10,
               status=SkillStatus.PUBLISHED, content: str = "body x") -> None:
    # content 在创建时传入：让初始版本快照携带正确内容（回滚快照比对可验证）
    skill = svc.create_manual({"id": skill_id, "name": skill_id,
                               "content": content})
    if params is not None:
        skill.default_params = dict(params)
    skill.metrics.usage_count = usage
    skill.status = status
    svc.store.upsert(skill)


def _audit_lines(path: Path) -> list:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return [json.loads(x) for x in text.splitlines() if x.strip()]


def _controller(tmp_path, svc, audit_path, archive, approval_flow, **kw) -> RolloutController:
    kw.setdefault("service", svc)
    kw.setdefault("audit_path", str(audit_path))
    kw.setdefault("archive", archive)
    kw.setdefault("approval_flow", approval_flow)
    return RolloutController(**kw)


def _feedback_report(*skill_ids) -> dict:
    return {
        "total_skills": len(skill_ids), "processed": len(skill_ids),
        "planned": [{"skill_id": s, "action": "promote_to_published",
                     "reason": "满意率 95% 达 90% 且反馈数 8>=5"} for s in skill_ids],
        "executed": [], "rejected": [], "errors": [],
    }


def _evolution_report(*skill_ids) -> dict:
    return {
        "total_skills": len(skill_ids), "dry_run": True,
        "planned_candidates": [{"skill_id": s, "usage_count": 10,
                                "success_rate": 0.7} for s in skill_ids],
    }


def _week_row(week: str, *, reuse: float = 0.5, skill_hit: float = 0.6,
              workflow: float = 0.7, feedback_avg: float = 4.0,
              artifacts: int = 5, ev_candidates: int = 0,
              ev_rate: float = 0.0, task_total: int = 0,
              task_failed: int = 0) -> dict:
    return {
        "week": week, "start": week, "end": week,
        "interactions": 100,
        "token_reuse_rate": {"saved": int(reuse * 100), "total": 100,
                             "rate": reuse},
        "skill_hit_rate": {"queries": 100, "hits": int(skill_hit * 100),
                           "rate": skill_hit},
        "workflow_hit_rate": {"interactions": 100, "hits": int(workflow * 100),
                              "rate": workflow},
        "failure_rate_by_task_type": (
            {"code": {"total": task_total, "failed": task_failed,
                      "rate": (task_failed / task_total if task_total else 0.0)}}
            if task_total else {}),
        "complexity_failure_rate": {},
        "feedback": {"count": 10, "avg": feedback_avg},
        "artifact_delta": {"count": artifacts},
        "evolution": {"candidates": ev_candidates, "adopted": 0,
                      "rate": ev_rate,
                      "insufficient_data": ev_candidates < 5},
    }


def _healthy_weekly(n: int = 4) -> list:
    return [_week_row(f"W{i}", reuse=0.3 + i * 0.1) for i in range(n)]


def _degraded_weekly(n: int = 4) -> list:
    """最近 2 周 token 复用率连续下滑 → 恶化。"""
    rows = [_week_row(f"W{i}", reuse=0.5 - i * 0.1) for i in range(n)]
    return rows


# ─── 四态：总开关与模式判定 ───


def test_master_off_forces_dry_run(monkeypatch, tmp_path):
    """总开关默认关闭：全部动作强制 dry_run，can_commit=False。"""
    monkeypatch.setenv(f"{ENV_PREFIX}_FEEDBACK_MODE", MODE_ROLLOUT)
    monkeypatch.setenv(f"{ENV_PREFIX}_EVOLUTION_MODE", MODE_CONFIRM)
    # 总开关未开（默认 false）
    for action in ("feedback", "evolution", "lifecycle"):
        assert mode_for(action) == MODE_DRY_RUN
    ctrl = RolloutController(audit_path=str(tmp_path / "a.jsonl"))
    status = ctrl.status()
    assert status["master_enabled"] is False
    assert all(a["mode"] == MODE_DRY_RUN for a in status["actions"].values())
    assert all(a["can_commit"] is False for a in status["actions"].values())


def test_master_on_mode_resolution(monkeypatch, tmp_path):
    """总开关开启 + 模式配置：confirm/rollout 可提交，dry_run/observe 不可。"""
    monkeypatch.setenv(f"{ENV_PREFIX}_MASTER_ENABLED", "true")
    monkeypatch.setenv(f"{ENV_PREFIX}_FEEDBACK_MODE", MODE_CONFIRM)
    monkeypatch.setenv(f"{ENV_PREFIX}_EVOLUTION_MODE", MODE_ROLLOUT)
    ctrl = RolloutController(audit_path=str(tmp_path / "a.jsonl"))
    assert ctrl.mode_for("feedback") == MODE_CONFIRM
    assert ctrl.mode_for("evolution") == MODE_ROLLOUT
    assert ctrl.mode_for("lifecycle") == MODE_DRY_RUN
    assert ctrl.can_commit("feedback") is True
    assert ctrl.can_commit("evolution") is True
    assert ctrl.can_commit("lifecycle") is False


def test_invalid_action_rejected():
    with pytest.raises(RolloutError):
        normalize_action("bogus")


# ─── observe 态：零提交零 KPI ───


def test_observe_zero_commit_zero_kpi(svc, tmp_path, audit_path, archive,
                                      approval_flow, monkeypatch):
    """observe：报告候选写谱系 preview + 审计；零提交/零KPI/零版本变更。"""
    monkeypatch.setenv(f"{ENV_PREFIX}_MASTER_ENABLED", "true")
    monkeypatch.setenv(f"{ENV_PREFIX}_FEEDBACK_MODE", MODE_OBSERVE)
    _add_skill(svc, "s1", params={"k": 1})
    _add_skill(svc, "s2", params={"k": 2})
    before = {s: svc.get(s).version for s in ("s1", "s2")}

    calls = {"dry": 0, "real": 0}

    def dry_runner():
        calls["dry"] += 1
        return _feedback_report("s1", "s2")

    def run_real():
        calls["real"] += 1
        return {"executed": []}

    ctrl = _controller(tmp_path, svc, audit_path, archive, approval_flow)
    out = ctrl.run_scheduled("feedback", dry_runner=dry_runner, run_real=run_real)

    assert out["mode"] == MODE_OBSERVE
    assert out["status"] == "observed"
    assert out["previews"] == 2
    # 零提交 / 零版本变更
    assert calls["real"] == 0
    assert {s: svc.get(s).version for s in ("s1", "s2")} == before
    # 零审批
    assert approval_flow.count_by_state() == 0
    # 零 KPI（进化采纳率无候选）
    snap = get_learning_metrics().get_snapshot()
    assert snap["kpis"]["evolution_adoption_rate"]["candidates"] == 0
    # 谱系 preview（decision=preview）
    assert DECISION_PREVIEW in DECISIONS  # 最小扩展守约
    archive_records = archive.query({"decision": DECISION_PREVIEW})
    assert len(archive_records) == 2
    assert {r.object_id for r in archive_records} == {"s1", "s2"}
    # 审计 preview 记录
    lines = _audit_lines(audit_path)
    assert len(lines) == 2
    for rec in lines:
        assert rec["decision"] == DECISION_PREVIEW
        assert rec["mode"] == MODE_OBSERVE
        assert rec["action"] == "feedback"
        assert rec["candidate_id"].startswith("cand-")
        assert rec["parent_record_id"]  # 谱系记录 ID 可回溯
        assert rec["before_version"] is not None
        assert rec["after_version"] is None


def test_preview_stats_counts_observe(tmp_path, svc, archive, approval_flow):
    """预演采纳率数据源：只统计带 candidate_id 的 preview 记录（批级摘要不计）。"""
    audit_path = tmp_path / "audit.jsonl"
    ctrl = _controller(tmp_path, svc, audit_path, archive, approval_flow)
    ctrl.record_preview("feedback", _feedback_report("s1", "s2"))
    # 批级摘要记录（candidate_id=None）不应计入
    ctrl._audit(action="feedback", mode=MODE_ROLLOUT, decision=DECISION_PREVIEW,
                detail={"status": "skipped_by_ratio"})
    stats = ctrl.preview_stats("feedback", days=30)
    assert stats["total"] == 2
    assert stats["by_action"] == {"feedback": 2}


# ─── confirm 态：审批队列 ───


def test_confirm_no_approval_zero_commit(svc, tmp_path, audit_path, archive,
                                         approval_flow, monkeypatch):
    """confirm：产物入审批队列；未审批 → 零提交。"""
    monkeypatch.setenv(f"{ENV_PREFIX}_MASTER_ENABLED", "true")
    monkeypatch.setenv(f"{ENV_PREFIX}_FEEDBACK_MODE", MODE_CONFIRM)
    _add_skill(svc, "s1")
    before = svc.get("s1").version
    calls = {"real": 0}

    def dry_runner():
        return _feedback_report("s1")

    def run_real():
        calls["real"] += 1
        return {"executed": [{"skill_id": "s1", "result": "published"}]}

    ctrl = _controller(tmp_path, svc, audit_path, archive, approval_flow,
                       regression_checker=lambda sid: {"status": "PASS"})
    out = ctrl.run_scheduled("feedback", dry_runner=dry_runner, run_real=run_real)

    assert out["mode"] == MODE_CONFIRM
    assert out["status"] == "pending_approval"
    assert out["approval_record_id"]
    # 未审批 → run_real 零调用、零版本变更
    assert calls["real"] == 0
    assert svc.get("s1").version == before
    # 审批记录存在且 pending_review
    rec = approval_flow.get(out["approval_record_id"])
    assert rec is not None and rec.state == "pending_review"
    assert rec.level == "L1"
    # 审计 preview 挂 approval_record_id
    lines = _audit_lines(audit_path)
    assert any(r["approval_record_id"] == out["approval_record_id"]
               and r["decision"] == DECISION_PREVIEW for r in lines)


def test_confirm_regression_fail_rejected(svc, tmp_path, audit_path, archive,
                                          approval_flow, monkeypatch):
    """confirm：提交前回归门禁生效——退化候选（FAIL）被拒，零提交。"""
    monkeypatch.setenv(f"{ENV_PREFIX}_MASTER_ENABLED", "true")
    monkeypatch.setenv(f"{ENV_PREFIX}_FEEDBACK_MODE", MODE_CONFIRM)
    monkeypatch.setenv(f"{ENV_PREFIX}_REGRESSION_GATE", "enforce")
    _add_skill(svc, "s1")
    before = svc.get("s1").version
    calls = {"real": 0}

    def dry_runner():
        return _feedback_report("s1")

    def run_real():
        calls["real"] += 1
        return {"executed": []}

    ctrl = _controller(
        tmp_path, svc, audit_path, archive, approval_flow,
        regression_checker=lambda sid: {
            "status": "FAIL", "score": 0.2, "baseline_score": 0.9,
            "delta_vs_baseline": -0.7, "sampleset_version": "v1",
            "used_tokens": 10, "sample_count": 5})
    out = ctrl.run_scheduled("feedback", dry_runner=dry_runner, run_real=run_real)

    with pytest.raises(RolloutGateError):
        ctrl.approve("feedback", out["approval_record_id"], actor="reviewer",
                     note="同意")

    # 退化候选被拒 → 零提交、零版本变更
    assert calls["real"] == 0
    assert svc.get("s1").version == before
    # 审计 rejected 且带回归结果
    lines = _audit_lines(audit_path)
    rejected = [r for r in lines if r["decision"] == DECISION_REJECTED]
    assert rejected
    assert rejected[-1]["regression_result"]["status"] == "FAIL"
    # 审批记录已归档（不悬挂在 approved）
    rec = approval_flow.get(out["approval_record_id"])
    assert rec.state == "archived"


def test_confirm_regression_pass_commits(svc, tmp_path, audit_path, archive,
                                         approval_flow, monkeypatch):
    """confirm：回归 PASS + 人工批准 → 提交生效，审计含版本号。"""
    monkeypatch.setenv(f"{ENV_PREFIX}_MASTER_ENABLED", "true")
    monkeypatch.setenv(f"{ENV_PREFIX}_FEEDBACK_MODE", MODE_CONFIRM)
    monkeypatch.setenv(f"{ENV_PREFIX}_REGRESSION_GATE", "enforce")
    _add_skill(svc, "s1", params={"k": 1})
    before = svc.get("s1").version
    calls = {"real": 0}

    def dry_runner():
        return _feedback_report("s1")

    def run_real():
        calls["real"] += 1
        svc.bump_version("s1", "minor", changelog="[test] confirm commit")
        return {"executed": [{"skill_id": "s1", "result": "published"}]}

    ctrl = _controller(tmp_path, svc, audit_path, archive, approval_flow,
                       regression_checker=lambda sid: {"status": "PASS"})
    out = ctrl.run_scheduled("feedback", dry_runner=dry_runner, run_real=run_real)

    result = ctrl.approve("feedback", out["approval_record_id"], actor="reviewer")

    assert result["status"] == "approved"
    assert calls["real"] == 1
    assert svc.get("s1").version != before  # 已提交（版本变更）
    # 审计 approved：含 before/after 版本号
    lines = _audit_lines(audit_path)
    approved = [r for r in lines if r["decision"] == DECISION_APPROVED]
    assert approved
    assert approved[-1]["before_version"] == before
    assert approved[-1]["after_version"] == svc.get("s1").version
    assert approved[-1]["regression_result"]["status"] == "PASS"
    # 审批记录 merged
    rec = approval_flow.get(out["approval_record_id"])
    assert rec.state == "merged"


def test_approve_blocked_outside_confirm(svc, tmp_path, audit_path, archive,
                                         approval_flow, monkeypatch):
    """越级拦截：非 confirm/rollout 模式（observe）下 approve 被拒。"""
    monkeypatch.setenv(f"{ENV_PREFIX}_MASTER_ENABLED", "true")
    monkeypatch.setenv(f"{ENV_PREFIX}_FEEDBACK_MODE", MODE_OBSERVE)
    ctrl = _controller(tmp_path, svc, audit_path, archive, approval_flow)
    assert ctrl.mode_for("feedback") == MODE_OBSERVE
    assert ctrl.can_commit("feedback") is False
    with pytest.raises(RolloutGateError):
        ctrl.approve("feedback", "appr-whatever", actor="reviewer")


def test_approve_blocked_master_off(svc, tmp_path, audit_path, archive,
                                    approval_flow):
    """越级拦截：总开关关闭（默认）→ approve 一律被拒。"""
    ctrl = _controller(tmp_path, svc, audit_path, archive, approval_flow)
    with pytest.raises(RolloutGateError):
        ctrl.approve("feedback", "appr-whatever", actor="reviewer")


# ─── rollout 态：比例命中 + KPI 自动回退 ───


def test_rollout_ratio_zero_skips(svc, tmp_path, audit_path, archive,
                                  approval_flow, monkeypatch):
    """rollout：ratio=0 → 全部未命中 → 预演零提交。"""
    monkeypatch.setenv(f"{ENV_PREFIX}_MASTER_ENABLED", "true")
    monkeypatch.setenv(f"{ENV_PREFIX}_EVOLUTION_MODE", MODE_ROLLOUT)
    monkeypatch.setenv(f"{ENV_PREFIX}_EVOLUTION_ROLLOUT_RATIO", "0.0")
    _add_skill(svc, "s1")
    before = svc.get("s1").version
    calls = {"dry": 0, "real": 0}

    def dry_runner():
        calls["dry"] += 1
        return _evolution_report("s1")

    def run_real():
        calls["real"] += 1
        return {}

    ctrl = _controller(tmp_path, svc, audit_path, archive, approval_flow,
                       kpi_provider=lambda: _healthy_weekly())
    out = ctrl.run_scheduled("evolution", dry_runner=dry_runner, run_real=run_real)

    assert out["status"] == "skipped_by_ratio"
    assert calls["real"] == 0
    assert svc.get("s1").version == before
    lines = _audit_lines(audit_path)
    assert any(r["decision"] == DECISION_PREVIEW
               and r["detail"].get("status") == "skipped_by_ratio"
               and r["candidate_id"] is None for r in lines)


def test_rollout_ratio_one_commits(svc, tmp_path, audit_path, archive,
                                   approval_flow, monkeypatch):
    """rollout：ratio=1 → 命中 → 真实提交 + 审计 approved（含版本号）。"""
    monkeypatch.setenv(f"{ENV_PREFIX}_MASTER_ENABLED", "true")
    monkeypatch.setenv(f"{ENV_PREFIX}_EVOLUTION_MODE", MODE_ROLLOUT)
    monkeypatch.setenv(f"{ENV_PREFIX}_EVOLUTION_ROLLOUT_RATIO", "1.0")
    _add_skill(svc, "s1", params={"k": 1})
    before = svc.get("s1").version
    calls = {"real": 0}

    def dry_runner():
        return _evolution_report("s1")

    def run_real():
        calls["real"] += 1
        svc.bump_version("s1", "patch", changelog="[test] rollout commit")
        skill = svc.get("s1")
        skill.default_params = {"k": 99}
        svc.store.upsert(skill)
        return {"evolved_count": 1}

    ctrl = _controller(tmp_path, svc, audit_path, archive, approval_flow,
                       regression_checker=lambda sid: {"status": "PASS"},
                       kpi_provider=lambda: _healthy_weekly(),
                       rng=random.Random(0))
    out = ctrl.run_scheduled("evolution", dry_runner=dry_runner, run_real=run_real)

    assert out["status"] == "committed"
    assert calls["real"] == 1
    assert svc.get("s1").version != before
    lines = _audit_lines(audit_path)
    approved = [r for r in lines if r["decision"] == DECISION_APPROVED]
    assert approved
    assert approved[-1]["before_version"] == before
    assert approved[-1]["after_version"] == svc.get("s1").version
    assert approved[-1]["kpi_snapshot"] is not None


def test_rollout_regression_gate_blocks(svc, tmp_path, audit_path, archive,
                                        approval_flow, monkeypatch):
    """rollout：回归门禁 FAIL → 拦截提交，审计 rejected。"""
    monkeypatch.setenv(f"{ENV_PREFIX}_MASTER_ENABLED", "true")
    monkeypatch.setenv(f"{ENV_PREFIX}_EVOLUTION_MODE", MODE_ROLLOUT)
    monkeypatch.setenv(f"{ENV_PREFIX}_EVOLUTION_ROLLOUT_RATIO", "1.0")
    monkeypatch.setenv(f"{ENV_PREFIX}_REGRESSION_GATE", "enforce")
    _add_skill(svc, "s1")
    before = svc.get("s1").version
    calls = {"real": 0}

    def dry_runner():
        return _evolution_report("s1")

    def run_real():
        calls["real"] += 1
        return {}

    ctrl = _controller(tmp_path, svc, audit_path, archive, approval_flow,
                       regression_checker=lambda sid: {"status": "FAIL"},
                       kpi_provider=lambda: _healthy_weekly(),
                       rng=random.Random(0))
    out = ctrl.run_scheduled("evolution", dry_runner=dry_runner, run_real=run_real)

    assert out["status"] == "regression_blocked"
    assert calls["real"] == 0
    assert svc.get("s1").version == before
    lines = _audit_lines(audit_path)
    assert any(r["decision"] == DECISION_REJECTED
               and r["detail"].get("status") == "regression_gate_blocked"
               for r in lines)


def test_rollout_kpi_degraded_auto_rollback(svc, tmp_path, audit_path, archive,
                                            approval_flow, monkeypatch):
    """rollout：KPI 连续恶化（G5 第二层）→ 自动回退上一版本 + 告警审计。"""
    monkeypatch.setenv(f"{ENV_PREFIX}_MASTER_ENABLED", "true")
    monkeypatch.setenv(f"{ENV_PREFIX}_EVOLUTION_MODE", MODE_ROLLOUT)
    monkeypatch.setenv(f"{ENV_PREFIX}_EVOLUTION_ROLLOUT_RATIO", "1.0")
    _add_skill(svc, "s1", params={"k": 1}, content="body x")
    before_version = svc.get("s1").version
    before_params = dict(svc.get("s1").default_params)

    def dry_runner():
        return _evolution_report("s1")

    def run_real():
        svc.bump_version("s1", "patch", changelog="[test] rollout commit")
        skill = svc.get("s1")
        skill.default_params = {"k": 99}
        svc.store.upsert(skill)
        return {"evolved_count": 1}

    ctrl = _controller(tmp_path, svc, audit_path, archive, approval_flow,
                       regression_checker=lambda sid: {"status": "PASS"},
                       kpi_provider=lambda: _healthy_weekly(),
                       rng=random.Random(0))
    # 第一轮：KPI 健康 → 命中提交
    out1 = ctrl.run_scheduled("evolution", dry_runner=dry_runner, run_real=run_real)
    assert out1["status"] == "committed"
    assert svc.get("s1").version != before_version
    assert svc.get("s1").default_params == {"k": 99}

    # 第二轮：KPI 连续 2 周恶化 → 自动回退
    degraded = _degraded_weekly()
    ctrl2 = _controller(tmp_path, svc, audit_path, archive, approval_flow,
                        regression_checker=lambda sid: {"status": "PASS"},
                        kpi_provider=lambda: degraded,
                        rng=random.Random(0))
    out2 = ctrl2.run_scheduled("evolution", dry_runner=dry_runner, run_real=run_real)

    assert out2["status"] == "kpi_degraded_rolled_back"
    assert out2["kpi"]["triggered"] is True
    assert len(out2["rolled_back"]) == 1
    # 回滚后行为与旧版本一致（快照比对）
    skill = svc.get("s1")
    assert skill.version == before_version
    assert dict(skill.default_params) == before_params
    assert skill.content == "body x"
    # 审计 rolled_back 含回滚命令
    lines = _audit_lines(audit_path)
    rolled = [r for r in lines if r["decision"] == DECISION_ROLLED_BACK]
    assert rolled
    assert "python -m agent.learning.rollout_controller" in rolled[-1]["rollback_command"]
    assert rolled[-1]["before_version"] == before_version


# ─── 回滚：快照比对 ───


def test_rollback_restores_snapshot(svc, tmp_path, audit_path, archive,
                                    approval_flow):
    """任意放行后的提交可一键回滚，且行为与旧版本一致（版本/参数/内容比对）。"""
    _add_skill(svc, "s1", params={"k": 1}, content="original content")
    before_version = svc.get("s1").version
    before_params = dict(svc.get("s1").default_params)

    ctrl = _controller(tmp_path, svc, audit_path, archive, approval_flow)
    # 模拟一次放行提交（bump + 参数变更）+ 审计 approved（含快照）
    svc.bump_version("s1", "minor", changelog="[test] released")
    skill = svc.get("s1")
    skill.default_params = {"k": 999}
    svc.store.upsert(skill)
    cand_id = ctrl._audit(
        action="evolution", mode=MODE_ROLLOUT,
        candidate_id="cand-test-00000000",
        object_id="s1",
        decision=DECISION_APPROVED,
        before_version=before_version,
        after_version=svc.get("s1").version,
        detail={"candidate": {"skill_id": "s1", "action": "evolution"},
                "params_snapshot": before_params})["candidate_id"]

    result = ctrl.rollback("evolution", cand_id, reason="人工回滚")

    assert result["restored"] is True
    assert result["before_version"] == before_version
    skill = svc.get("s1")
    assert skill.version == before_version
    assert dict(skill.default_params) == before_params
    assert skill.content == "original content"
    # 审计 rolled_back + rollback_command
    lines = _audit_lines(audit_path)
    rolled = [r for r in lines if r["decision"] == DECISION_ROLLED_BACK]
    assert rolled and rolled[-1]["rollback_command"]


def test_rollback_unknown_candidate(tmp_path, svc, archive, approval_flow):
    """回滚未知候选 → RolloutError（审计可回溯前提）。"""
    ctrl = _controller(tmp_path, svc, tmp_path / "a.jsonl", archive, approval_flow)
    with pytest.raises(RolloutError):
        ctrl.rollback("evolution", "cand-does-not-exist")


# ─── G5 第二层判据（纯函数） ───


def test_kpi_judge_insufficient_data():
    rows = _healthy_weekly(n=2)  # 2 周 < 3 周（window=2 需 ≥3）
    verdict = judge_kpi_degradation(rows, window_weeks=2)
    assert verdict["triggered"] is False
    assert "数据不足" in verdict["reason"]


def test_kpi_judge_healthy_not_triggered():
    verdict = judge_kpi_degradation(_healthy_weekly(n=4), window_weeks=2)
    assert verdict["triggered"] is False


def test_kpi_judge_consecutive_degradation_triggered():
    verdict = judge_kpi_degradation(_degraded_weekly(n=4), window_weeks=2)
    assert verdict["triggered"] is True
    assert len(verdict["detail"]) == 2


def test_kpi_judge_single_degraded_week_not_triggered():
    """仅 1 周恶化（非连续）→ 不触发。"""
    rows = [_week_row("W0", reuse=0.5), _week_row("W1", reuse=0.5),
            _week_row("W2", reuse=0.4), _week_row("W3", reuse=0.4)]
    verdict = judge_kpi_degradation(rows, window_weeks=2)
    assert verdict["triggered"] is False


def test_kpi_judge_failure_rate_rise_triggered():
    """分类型失败率上升亦触发（任一 KPI 恶化即算）。"""
    rows = [_week_row("W0", task_total=100, task_failed=10),
            _week_row("W1", task_total=100, task_failed=10),
            _week_row("W2", task_total=100, task_failed=30),
            _week_row("W3", task_total=100, task_failed=40)]
    verdict = judge_kpi_degradation(rows, window_weeks=2)
    assert verdict["triggered"] is True


# ─── 候选解析 ───


def test_report_candidates_extraction():
    assert [c["skill_id"] for c in
            report_candidates("feedback", _feedback_report("s1", "s2"))] == ["s1", "s2"]
    assert [c["skill_id"] for c in
            report_candidates("evolution", _evolution_report("s1"))] == ["s1"]
    lifecycle = {"deprecated": [{"skill_id": "s1", "from_status": "published",
                                 "to_status": "deprecated", "idle_days": 100,
                                 "threshold": 90}],
                 "archived": [{"skill_id": "s2", "from_status": "deprecated",
                               "to_status": "archived", "idle_days": 200,
                               "threshold": 180}]}
    cands = report_candidates("lifecycle", lifecycle)
    assert [c["action"] for c in cands] == ["lifecycle:deprecate", "lifecycle:archive"]


# ─── 审计字段完备 ───


def test_audit_fields_complete(svc, tmp_path, audit_path, archive, approval_flow):
    """统一审计字段：action/mode/candidate_id/decision/版本号/回归结果/KPI 快照/回滚命令。"""
    ctrl = _controller(tmp_path, svc, audit_path, archive, approval_flow)
    rec = ctrl._audit(
        action="evolution", mode=MODE_ROLLOUT,
        candidate_id="cand-x", object_id="s1",
        parent_record_id="evt-parent", approval_record_id=None,
        decision=DECISION_APPROVED,
        before_version="0.1.0", after_version="0.1.1",
        regression_result={"status": "PASS"},
        kpi_snapshot={"triggered": False},
        rollback_command="python -m agent.learning.rollout_controller --rollback",
        detail={"candidate": {"skill_id": "s1"}})
    for field in ("action", "mode", "candidate_id", "object_id",
                  "parent_record_id", "approval_record_id", "decision",
                  "before_version", "after_version", "regression_result",
                  "kpi_snapshot", "rollback_command", "detail", "ts", "event"):
        assert field in rec, f"审计字段缺失: {field}"


# ─── 调度出口接线 ───


def test_scheduler_wiring_wraps_tasks(monkeypatch):
    """register_learning_schedulers：三类进化动作任务 func 被放行包装器替换。"""
    for mod, attr in (("agent.skills_mgmt.feedback_agent", "_enabled"),
                      ("agent.skills_mgmt.evolution_scheduler", "_enabled"),
                      ("agent.skills_mgmt.lifecycle", "_enabled"),
                      ("agent.learning.behavior_drift",
                       "_sensor_learning_enabled")):
        monkeypatch.setattr(f"{mod}.{attr}", lambda: True)
    try:
        register_learning_schedulers()
        from agent.task_scheduler import get_scheduler
        by_name = {t["name"]: t for t in get_scheduler().list_tasks()}
        assert _ROLLOUT_TASKS.keys() <= by_name.keys()
        sched = get_scheduler()
        for task in sched.tasks:
            if task.get("name") in _ROLLOUT_TASKS:
                assert task["func"].__name__ == "wrapper"
    finally:
        unregister_learning_schedulers()


def test_make_rollout_wrapper_dispatches(monkeypatch, tmp_path, svc, archive,
                                         approval_flow):
    """包装器按模式分派：observe → dry_runner 执行 + 写 preview，run_real 零调用。"""
    monkeypatch.setenv(f"{ENV_PREFIX}_MASTER_ENABLED", "true")
    monkeypatch.setenv(f"{ENV_PREFIX}_FEEDBACK_MODE", MODE_OBSERVE)
    audit_path = tmp_path / "wrapped_audit.jsonl"
    ctrl = RolloutController(audit_path=str(audit_path), archive=archive,
                             service=svc, approval_flow=approval_flow)
    calls = {"dry": 0, "real": 0}

    def dry_runner():
        calls["dry"] += 1
        return _feedback_report("s1")

    def run_real():
        calls["real"] += 1
        return {}

    wrapper = _make_rollout_wrapper(ctrl, "feedback", dry_runner, run_real)
    wrapper()

    assert calls["dry"] == 1
    assert calls["real"] == 0
    lines = _audit_lines(audit_path)
    assert lines and lines[0]["decision"] == DECISION_PREVIEW
    assert lines[0]["mode"] == MODE_OBSERVE
