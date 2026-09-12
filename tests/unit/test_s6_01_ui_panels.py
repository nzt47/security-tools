"""TASK-S6-01 六面板数据层与 HTTP 面单测

【覆盖】
    §1 口径纪律（schema）：样本 <20 只披露、缺位记 None、不可追溯百分比自查
    §2 六面板数据层：消化流水线（真实 stage 事件）/ 能力地图（真实台账）/
       审批收件箱（真实审批流 + 缺 undo_hint 不出气泡）/ ROI / 自愈事故 / 记忆技能库
    §3 只读端点权限：**未授权访问被拒**（sub_agent 一律 ❌）
    §4 写动作不旁路：L5 整包回滚 + 组件子集拒绝 + 边界词凭据（单次 + 60s）
    §5 审计导出：含验签摘要 + CSV
    §6 性能口径：聚合 <200ms / 明细分页 <1s（真实台账量级）
    §7 安全渲染常量：U1 单一来源（未自定义五类/60s/z-index）

【隔离纪律】
    所有落盘用例显式传临时目录（`tmp_path`），不触碰仓库运行时台账；
    事件/审计/审批/事故/PR 目录一律注入。
"""

from __future__ import annotations

import json
import os
import time

import pytest
from flask import Flask

from agent.ui_panels import data as D
from agent.ui_panels import schema as S


# ════════════════════════════════════════════════════════════
#  §1 口径纪律（schema.py）
# ════════════════════════════════════════════════════════════


class TestDiscipline:
    """§0.3 三条纪律机器化"""

    def test_min_sample_source_is_downstream_module(self):
        value, source = S.min_disclosure_sample()
        assert value == 20
        # 阈值不得在本模块复制：来源必须指向既有模块
        assert "cost_brake" in source

    def test_insufficient_sample_marks_disclosure_only(self):
        row = S.sample_discipline(19)
        assert row["insufficient_sample"] is True
        assert row["disclosure_only"] is True
        assert row["min_sample"] == 20
        assert "只披露不考核" in row["note"]

    def test_sufficient_sample_is_assessable(self):
        row = S.sample_discipline(20)
        assert row["insufficient_sample"] is False
        assert row["disclosure_only"] is False
        assert row["note"] == ""

    def test_absent_records_none_not_zero(self):
        row = S.absent("事件源缺位")
        assert row["value"] is None
        assert row["available"] is False
        assert row["reason"] == "事件源缺位"

    def test_metric_carries_source_and_formula(self):
        row = S.metric(0.5, source="agent/x.py::f", formula="a/b",
                       unit="ratio", sample_size=3)
        assert row["value"] == 0.5
        assert row["source"] == "agent/x.py::f"
        assert row["formula"] == "a/b"
        assert row["traceable"] is True
        assert row["insufficient_sample"] is True     # 3 < 20

    def test_metric_none_value_is_not_traceable_but_available_false(self):
        row = S.metric(None, source="s", formula="f", unit="ms")
        assert row["value"] is None
        assert row["available"] is False

    def test_untraceable_scan_flags_bare_ratio(self):
        report = S.untraceable_scan({"pass_rate": 0.97})
        assert report["ok"] is False
        assert any("pass_rate" in v for v in report["violations"])

    def test_untraceable_scan_passes_traceable_payload(self):
        payload = {"m": S.metric(0.97, source="agent/x.py", formula="p/t",
                                 unit="ratio", sample_size=57)}
        report = S.untraceable_scan(payload)
        assert report["ok"] is True, report["violations"]

    def test_metric_without_source_is_flagged(self):
        payload = {"m": S.metric(1, source="", formula="")}
        report = S.untraceable_scan(payload)
        assert report["ok"] is False


# ════════════════════════════════════════════════════════════
#  §2 数据层
# ════════════════════════════════════════════════════════════


def _write_events(directory, rows) -> str:
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "events.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        for i, row in enumerate(rows):
            env = {"v": 1, "event_id": f"ev-{i}", "ts": row.get("ts"),
                   "correlation_id": "c1", "actor": row.get("actor", "auto"),
                   "type": row["type"], "payload": row.get("payload", {})}
            fh.write(json.dumps(env, ensure_ascii=False) + "\n")
    return path


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


class TestDigestionPipeline:
    """消化流水线：泳道五列来自**真实 digest.stage 事件**（非 mock 结构）"""

    def test_lanes_five_and_from_real_events(self, tmp_path):
        events_dir = str(tmp_path / "events")
        _write_events(events_dir, [
            {"ts": f"{_today()}T08:00:00", "type": "digest.stage",
             "payload": {"capability_id": "cp.a", "scope": "digestion",
                         "to_stage": "borrowed", "applied": True}},
            {"ts": f"{_today()}T08:01:00", "type": "digest.stage",
             "payload": {"capability_id": "cp.a", "scope": "acceptance_gate",
                         "to_stage": "shadow", "passport_id": "psp-1",
                         "applied": False}},
            {"ts": f"{_today()}T08:02:00", "type": "digest.stage",
             "payload": {"capability_id": "cp.a", "scope": "shadow_gray",
                         "to_stage": "shadow", "pass_rate": 0.9}},
            {"ts": f"{_today()}T08:03:00", "type": "digest.stage",
             "payload": {"capability_id": "cp.a", "scope": "internalize",
                         "to_stage": "internalized", "rank_score": 3.1}},
            {"ts": f"{_today()}T08:04:00", "type": "tool.called",
             "payload": {"capability_id": "cp.a"}},
        ])
        view = D.pipeline_view(days=7, events_dir=events_dir,
                               shadow_dir=str(tmp_path / "shadow"),
                               promote_dir=str(tmp_path / "pr"))
        lanes = {l["lane"]: l for l in view["lanes"]}
        assert [l["lane"] for l in view["lanes"]] == [
            "trace_collect", "pattern_mining", "skill_generation",
            "acceptance", "gray"]
        # 只统计 digest.stage（tool.called 不计入）
        assert view["summary"]["digest_stage_events"]["value"] == 4
        assert lanes["trace_collect"]["event_count"]["value"] == 1
        assert lanes["acceptance"]["event_count"]["value"] == 1
        assert lanes["gray"]["event_count"]["value"] == 2

    def test_every_number_is_traceable(self, tmp_path):
        events_dir = str(tmp_path / "events")
        _write_events(events_dir, [
            {"ts": f"{_today()}T08:00:00", "type": "digest.stage",
             "payload": {"capability_id": "cp.a", "scope": "digestion",
                         "applied": True}}])
        view = D.pipeline_view(days=7, events_dir=events_dir,
                               shadow_dir=str(tmp_path / "s"),
                               promote_dir=str(tmp_path / "p"))
        report = S.untraceable_scan(view)
        assert report["ok"] is True, report["violations"]

    def test_missing_sources_yield_none_not_zero(self, tmp_path):
        view = D.pipeline_view(days=7, events_dir=str(tmp_path / "none"),
                               shadow_dir=str(tmp_path / "none2"),
                               promote_dir=str(tmp_path / "none3"))
        assert view["summary"]["digest_stage_events"]["value"] == 0
        # 内化率为比率且分母为 0 ⇒ None（不以 0 冒充"零内化"）
        assert view["summary"]["internalize_rate"]["value"] is None
        assert view["shadow"] == []
        assert view["internalize"] == []

    def test_shadow_card_p99_and_pass_rate_traceable(self, tmp_path):
        shadow_dir = tmp_path / "shadow"
        shadow_dir.mkdir(parents=True)
        rows = [
            {"kind": "shadow_run", "capability_id": "cp.a", "generated_at": 1.0,
             "allowed": True, "budget": 50, "sampled": 57, "passed": 55,
             "negative": 2, "judge_kind": "deterministic_local",
             "degradation": "stable", "p99_wall_candidate_ms": 420.0,
             "p99_wall_upstream_ms": 380.0, "shadow_version": "s3-03.1"},
        ]
        with open(shadow_dir / "shadow_ledger.jsonl", "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        view = D.pipeline_view(days=7, events_dir=str(tmp_path / "e"),
                               shadow_dir=str(shadow_dir),
                               promote_dir=str(tmp_path / "p"))
        card = view["shadow"][0]
        assert card["sampled"] == 57
        assert card["p99_wall_candidate_ms"]["value"] == 420.0
        assert card["pass_rate"]["value"] == round(55 / 57, 6)
        assert card["pass_rate"]["unit"] == "ratio"
        assert card["pass_rate"]["formula"]
        # 57 ≥ 20 ⇒ 可考核
        assert card["pass_rate"]["insufficient_sample"] is False

    def test_internalize_decision_card_from_promote_dir(self, tmp_path):
        pr_dir = tmp_path / "pr" / "cp-a" / "pr_abc"
        pr_dir.mkdir(parents=True)
        decision = {
            "capability_id": "cp.a", "verdict": "promote", "blocker": "",
            "stage": "shadow", "passport_id": "psp", "rank_score": 3.5,
            "promotable": True, "manual_required": False,
            "veto_failed": [], "rank_failed": [], "generated_at": 1.0,
            "engine_version": "s3-03.1",
            "conditions": [{"name": "roi_positive", "dimension": "rank",
                            "passed": True, "actual": 340, "threshold": 0,
                            "comparator": ">", "score": 1.0,
                            "evidence_source": "s2-03_utc"}],
            "roi_report": {"monthly_saving_cents": 340.0,
                           "one_time_investment_cents": 2200.0,
                           "amortized_monthly_cents": 183.33,
                           "net_monthly_cents": 156.67, "positive": True,
                           "monthly_samples": 57,
                           "formula": "月省 = (上游 − 自研) × 月样本数"},
        }
        (pr_dir / "decision.json").write_text(
            json.dumps(decision, ensure_ascii=False), encoding="utf-8")
        view = D.pipeline_view(days=7, events_dir=str(tmp_path / "e"),
                               shadow_dir=str(tmp_path / "s"),
                               promote_dir=str(tmp_path / "pr"))
        cards = view["internalize"]
        assert len(cards) == 1
        card = cards[0]
        assert card["verdict"] == "promote"
        assert card["conditions"][0]["name"] == "roi_positive"
        assert card["roi"]["monthly_saving_cents"]["value"] == 340.0
        assert card["roi"]["monthly_saving_cents"]["source"].endswith("ROIReport")
        assert view["summary"]["internalize_rate"]["value"] == 1.0


class TestCapabilityMap:
    """能力地图：来自 registry.list_with_trust（真实台账结构）"""

    class _FakeRegistry:
        def __init__(self, rows):
            self._rows = rows

        def list_with_trust(self):
            return list(self._rows)

    def _rows(self):
        return [
            {"capability_id": "cp.a", "name": "a", "description": "d",
             "source_type": "builtin", "source_id": "builtin", "provenance":
             "native", "risk_level": "low", "data_class": "public",
             "requires_approval": False, "stage": "shadow", "audit_level": "full",
             "has_undo_hint": True, "has_compensating_action": False,
             "idempotent": True, "timeout_ms": 1000, "external_endpoint": False,
             "variant_count": 0, "aliases": [], "updated_at": "t",
             "success_rate": 0.97, "sample_count": 57, "p99_latency_ms": 420.0},
            {"capability_id": "cp.b", "name": "b", "description": "d",
             "source_type": "mcp", "source_id": "mcp:x", "provenance": "borrowed",
             "risk_level": "destructive", "data_class": "secret",
             "requires_approval": True, "stage": "borrowed", "audit_level": "full",
             "has_undo_hint": False, "has_compensating_action": False,
             "idempotent": False, "timeout_ms": None, "external_endpoint": True,
             "variant_count": 1, "aliases": ["b1"], "updated_at": "t",
             "success_rate": 0.5, "sample_count": 4, "p99_latency_ms": None},
        ]

    def test_distribution_and_filters(self):
        view = D.capability_map(registry=self._FakeRegistry(self._rows()))
        assert view["total"]["value"] == 2
        assert view["distribution"]["by_stage"] == {"borrowed": 1, "shadow": 1}
        assert view["distribution"]["by_risk"]["destructive"] == 1
        only_shadow = D.capability_map(registry=self._FakeRegistry(self._rows()),
                                       stage="shadow")
        assert only_shadow["matched"]["value"] == 1

    def test_sample_discipline_per_row(self):
        view = D.capability_map(registry=self._FakeRegistry(self._rows()))
        by_id = {i["capability_id"]: i for i in view["items"]}
        assert by_id["cp.a"]["sample_discipline"]["insufficient_sample"] is False
        assert by_id["cp.b"]["sample_discipline"]["insufficient_sample"] is True
        assert by_id["cp.b"]["success_rate"]["insufficient_sample"] is True

    def test_zero_samples_is_none_not_zero_percent(self):
        """★ 纪律②：台账里 sample_count=0 的 0.0 **不是**"0% 命中率"

        真实台账（`data/descriptors.json`）里 `quality.success_rate` 缺省为 0.0
        且 `sample_count=0`；若原样上屏就是"能力命中率 0%"这种**不可追溯的百分比**，
        正是 UI 五坑⑤的反面教材。故样本为 0 时一律记 None。
        """
        rows = [dict(self._rows()[0], capability_id="cp.zero", sample_count=0,
                     success_rate=0.0, p99_latency_ms=0)]
        view = D.capability_map(registry=self._FakeRegistry(rows))
        card = view["items"][0]
        assert card["sample_count"] == 0
        assert card["success_rate"]["value"] is None       # 不是 0.0
        assert card["success_rate"]["available"] is False
        assert card["p99_latency_ms"]["value"] is None
        assert "无样本" in card["success_rate"]["note"]
        report = S.untraceable_scan(view)
        assert report["ok"] is True, report["violations"]

    def test_approval_bubble_eligibility_from_governance(self):
        """缺 undo_hint **且**无补偿动作 ⇒ 不得出现审批气泡（§7）"""
        view = D.capability_map(registry=self._FakeRegistry(self._rows()))
        by_id = {i["capability_id"]: i for i in view["items"]}
        assert by_id["cp.a"]["approval_bubble_eligible"] is True
        assert by_id["cp.b"]["approval_bubble_eligible"] is False

    def test_registry_failure_is_reported_not_faked(self):
        class _Boom:
            def list_with_trust(self):
                raise RuntimeError("boom")

        view = D.capability_map(registry=_Boom())
        assert view["items"] == []
        assert view["total"]["value"] == 0
        assert "boom" in view["load_error"]


class TestApprovalInbox:
    """审批收件箱：真实 ApprovalFlow + 缺 undo_hint 不出气泡 + 批量分组键"""

    def _flow(self, tmp_path):
        from agent.skills_mgmt.approval import ApprovalFlow
        flow = ApprovalFlow(records_path=str(tmp_path / "appr.jsonl"),
                            enabled=True)
        return flow

    def test_inbox_items_and_batch_key(self, tmp_path):
        flow = self._flow(tmp_path)
        r1 = flow.submit("stage.promote", "cp.a", action="promote",
                         description="d1", actor="system")
        r2 = flow.submit("stage.promote", "cp.b", action="promote",
                         description="d2", actor="system")
        inbox = D.approval_inbox(flow=flow)
        assert inbox["count"] == 2
        ids = {i["record_id"] for i in inbox["items"]}
        assert ids == {r1.record_id, r2.record_id}
        # 同策略同风险 ⇒ 同一 batch_key（§7 一键批）
        assert len(inbox["batch_groups"]) == 1
        assert inbox["batch_groups"][0]["count"] == 2
        assert inbox["batch_groups"][0]["batch_key"].startswith("stage.promote|")

    def test_missing_undo_hint_hides_bubble(self, tmp_path):
        """★ 验收项：缺 undo_hint 不出现审批气泡"""
        flow = self._flow(tmp_path)
        flow.submit("unknown.type.no.governance", "x-1", action="a",
                    description="d", actor="system")
        inbox = D.approval_inbox(flow=flow)
        assert inbox["count"] == 1
        item = inbox["items"][0]
        assert item["bubble"]["visible"] is False
        assert inbox["bubble_hidden"]["value"] == 1
        assert "缺 undo_hint" in item["bubble"]["rule"]

    def test_flow_unavailable_reports_error(self):
        class _Boom:
            def list(self, *a, **k):
                raise RuntimeError("no flow")

        inbox = D.approval_inbox(flow=_Boom())
        assert inbox["ok"] is False
        assert "RuntimeError" in inbox["error"]


class TestRoiPanel:
    """ROI / 成本：口径版本齐备 + U8 双口径并列 + §6.7 指标接入"""

    def test_calibration_and_two_scope_latency(self, tmp_path):
        view = D.roi_view(days=7, events_dir=str(tmp_path / "events"))
        assert (view["daily"].get("cost_schema_version")
                or view["daily"].get("available") is False)
        lat = view["policy_latency"]
        # U8：两个口径并列且显式标注不可混用
        assert lat["decision_body"]["value"] == 0.0474
        assert lat["full_instrumentation"]["hit_ms"] == 3.764
        assert lat["comparability"] == "two_scopes_not_interchangeable"
        assert "不得" in lat["decision_body"]["note"]

    def test_approval_decay_is_disclosure_only(self, tmp_path):
        view = D.roi_view(days=7, events_dir=str(tmp_path / "events"))
        decay = view["approval_decay"]
        assert decay["disclosure_only"] is True
        rate = decay["decay_rate"]
        assert rate["unit"] == "rate"
        assert rate["formula"]
        assert rate["value"] is None          # 无非空样本 ⇒ None，不填 0

    def test_slo_metrics_include_u10_delegation_recovery(self, tmp_path):
        """U10：委派回收率接线（行契约 + 计算函数已交付）"""
        view = D.roi_view(days=7, events_dir=str(tmp_path / "events"),
                          delegations=[
                              {"delegation_id": "d1", "capability_id": "c1",
                               "artifact": True, "trace": True, "reflection": True},
                              {"delegation_id": "d2", "capability_id": "c2",
                               "artifact": True, "trace": False, "reflection": True},
                          ])
        metrics = (view["slo_metrics"] or {}).get("metrics") or {}
        row = metrics.get("delegation_recovery")
        assert row is not None, "委派回收率未接线"
        assert row["value"] == 0.5
        assert row["numerator"] == 1 and row["denominator"] == 2
        assert row["contract"]["required"] == ["delegation_id", "capability_id"]
        # S5-02 口径：样本 <20 ⇒ insufficient_samples（只披露不考核）
        assert row["status"] == "insufficient_samples"
        assert row["incomplete"] == ["d2"]


class TestIncidentsPanel:
    """自愈事故：事故卡六要素 + MTTD/MTTR 缺位记 None + 有事故自动展开"""

    def test_open_incident_triggers_auto_expand(self, tmp_path):
        from agent.self_healing.levels import HealLevel, IncidentCard, save_incident
        inc_dir = str(tmp_path / "inc")
        card = IncidentCard(severity=HealLevel.L4, root_cause="rc",
                            fatal_change="fc", evasion_rule="er",
                            in_strategy_memory={"yes": True, "id": "m1"},
                            regression_case_added={"yes": True, "case_id": "c1"},
                            trace_ids=["t1"], mttd_ms=120.0, mttr_ms=300.0)
        save_incident(card, directory=inc_dir)
        view = D.incidents_view(incidents_dir=inc_dir,
                                events_dir=str(tmp_path / "e"))
        assert view["auto_expand"] is True
        assert view["open_count"]["value"] == 1
        item = view["incidents"][0]
        assert item["severity"] == "L4"
        assert item["missing_elements"] == []      # 六要素齐备
        assert item["mttd_ms"] == 120.0

    def test_no_incident_no_auto_expand_and_none_latency(self, tmp_path):
        view = D.incidents_view(incidents_dir=str(tmp_path / "none"),
                                events_dir=str(tmp_path / "e"))
        assert view["auto_expand"] is False
        assert view["open_count"]["value"] == 0
        assert view["mttd_ms"]["mttd_ms"]["value"] is None      # 不填 0
        assert view["mttd_ms"]["mttr_ms"]["value"] is None

    def test_mttd_median_only_counts_present_fields(self, tmp_path):
        events_dir = str(tmp_path / "events")
        _write_events(events_dir, [
            {"ts": f"{_today()}T09:00:00", "type": "healing.triggered",
             "payload": {"level": "L2", "mttd_ms": 100.0, "mttr_ms": 200.0}},
            {"ts": f"{_today()}T09:01:00", "type": "healing.triggered",
             "payload": {"level": "L3", "mttd_ms": 300.0}},   # 无 mttr_ms
        ])
        view = D.incidents_view(incidents_dir=str(tmp_path / "none"),
                                events_dir=events_dir)
        mtt = view["mttd_ms"]
        assert mtt["mttd_samples"] == 2
        assert mtt["mttr_samples"] == 1          # 缺字段不计入（不补 0）
        assert mtt["mttd_ms"]["value"] == 200.0
        assert mtt["mttr_ms"]["value"] == 200.0

    def test_incident_card_six_elements_rule(self):
        """六要素不齐 ⇒ 不得 resolved（S4-03 语义在面板侧如实透出）"""
        from agent.self_healing.levels import HealLevel, IncidentCard, LevelError
        card = IncidentCard(severity=HealLevel.L3, root_cause="只有根因")
        assert card.missing_elements()
        with pytest.raises(LevelError):
            card.resolve()


class TestMemorySkillsPanel:
    """记忆 / 技能库（P2）：U5 召回优先级接线 + 缺位记 None"""

    def test_recall_priority_contract(self):
        view = D.memory_skills_view(limit=3)
        rp = view["recall_priority"]
        assert rp["order"] == ["strategy", "fact", "preference", "working"]
        assert "recall_priority_key" in rp["source"]
        assert rp["callable"] is True

    def test_store_failure_is_absent_not_faked(self):
        class _Boom:
            async def recall(self, *a, **k):
                raise RuntimeError("no store")

        view = D.memory_skills_view(store=_Boom())
        assert view["layers"]["available"] is False
        assert view["layers"]["value"] is None


class TestSecurityAndAuthz:
    """U1 常量单一来源 + U3 越权告警聚合"""

    def test_render_state_exposes_u1_constants(self):
        state = D.security_render_state()
        bw = state["boundary_words"]
        sr = state["safe_render"]
        assert bw["max_ttl_seconds"] == 60.0
        assert bw["accepts_text_approval"] is False
        assert bw["single_action_bound"] is True
        assert len(bw["never_automated"]) == 5
        assert sr["taint_badge"]["class"] == "cp-taint-badge"
        assert sr["taint_badge"]["base_class"] == "cp-taint-badge--has-bg"
        assert sr["approval_zone"]["style"]["z-index"] == "2147483000"
        assert "z-index" in state["frontend_contract"]["must_not_customize"][-1]

    def test_authz_alerts_has_both_scopes(self, tmp_path):
        view = D.authz_alerts(limit=10, events_dir=str(tmp_path / "e"))
        # 实时口径标注 volatile（进程内计数，重启归零）
        assert view["realtime"]["volatile"] is True
        # 耐久口径来自 policy.denied 事件
        assert view["durable"]["total"]["value"] == 0
        assert "actor_ip_hash" in view["durable"]["pii_note"]

    def test_authz_alerts_aggregates_real_events(self, tmp_path):
        events_dir = str(tmp_path / "events")
        _write_events(events_dir, [
            {"ts": f"{_today()}T10:00:00", "type": "policy.denied",
             "payload": {"operation": "governance.force_stage",
                         "actor_ip_hash": "h1", "denied_by_matrix": True,
                         "reason": "§7.0 矩阵拒绝"}},
            {"ts": f"{_today()}T10:01:00", "type": "policy.denied",
             "payload": {"operation": "approval.approve", "actor_ip_hash": "h1",
                         "denied_by_matrix": True}},
            {"ts": f"{_today()}T10:02:00", "type": "policy.denied",
             "payload": {"operation": "approval.approve", "actor_ip_hash": "h2"}},
        ])
        view = D.authz_alerts(limit=10, events_dir=events_dir)
        assert view["durable"]["total"]["value"] == 3
        assert view["durable"]["by_source_key"] == {"h1": 2, "h2": 1}
        assert view["durable"]["by_operation"]["approval.approve"] == 2


class TestObservabilityStream:
    """S2-03 #12：ACR/UTC/降级拓扑/逃逸清单四类面板非 mock"""

    def test_four_aggregates_present(self, tmp_path):
        view = D.observability_stream(days=7, events_dir=str(tmp_path / "e"))
        assert "acr" in view and "utc" in view
        assert "edges" in view["model_degrade"] or "available" in view["model_degrade"]
        assert "ledger" in view["escape"] or "available" in view["escape"]

    def test_degrade_topology_from_events(self, tmp_path):
        events_dir = str(tmp_path / "events")
        _write_events(events_dir, [
            {"ts": f"{_today()}T11:00:00", "type": "model.degraded",
             "payload": {"from": "gpt-4o-mini", "to": "gpt-3.5-turbo",
                         "reason": "E_MODEL_DEGRADED:timeout",
                         "fallback_attempted": True}},
        ])
        view = D.observability_stream(days=7, events_dir=events_dir)
        assert view["model_degrade"]["total"]["value"] == 1
        assert "gpt-4o-mini -> gpt-3.5-turbo" in view["model_degrade"]["edges"]

    def test_escape_summary_from_events(self, tmp_path):
        events_dir = str(tmp_path / "events")
        _write_events(events_dir, [
            {"ts": f"{_today()}T11:10:00", "type": "escape",
             "payload": {"path": "data/scheduled_tasks.json",
                         "reason": "untracked_content_change",
                         "capability_id": "cp.governed.scheduled_task.write"}},
        ])
        view = D.observability_stream(days=7, events_dir=events_dir)
        assert view["escape"]["total"]["value"] == 1
        assert view["escape"]["by_path"]["data/scheduled_tasks.json"] == 1


class TestAuditExport:
    """审计导出：含验签摘要（S2-02）"""

    def _chain(self, tmp_path):
        from agent.audit.chain import AuditChain
        return AuditChain(str(tmp_path / "chain.db"))

    def test_export_contains_verification(self, tmp_path):
        chain = self._chain(tmp_path)
        for i in range(5):
            chain.append(action=f"act{i}", actor="tester", subject=f"s{i}",
                         payload={"i": i}, source="agent")
        payload = D.audit_export(limit=10, chain=chain)
        assert payload["count"] == 5
        assert payload["verify"]["ok"] is True
        assert payload["verify"]["checked"] == 5
        assert payload["verify_scope"] == "exported"
        assert payload["chain_head"]["last_seq"] == 5
        assert payload["elapsed_ms"]["value"] is not None
        chain.close()

    def test_export_detects_tampering(self, tmp_path):
        chain = self._chain(tmp_path)
        for i in range(5):
            chain.append(action=f"act{i}", actor="t", subject=f"s{i}",
                         payload={"i": i}, source="agent")
        # 链式写入是**批量异步**的：篡改前必须落库，否则改的是空表
        # （而读取会回落到进程内待写缓冲 ⇒ 验签反而"通过"，这正是要避免的假阴性）
        self._flush(chain)
        import sqlite3
        con = sqlite3.connect(chain._db_path)
        changed = con.execute(
            "UPDATE audit_chain SET actor='attacker' WHERE seq=3").rowcount
        con.commit()
        con.close()
        assert changed == 1, "篡改未命中任何记录"
        payload = D.audit_export(limit=10, verify_scope="full", chain=chain)
        assert payload["verify"]["ok"] is False
        assert payload["verify"]["first_bad_seq"] == 3
        chain.close()

    @staticmethod
    def _flush(chain) -> None:
        """等待批量写入落库（best-effort；无 flush API 时按写入节奏让出）"""
        flush = getattr(chain, "flush", None)
        if callable(flush):
            try:
                flush()
                return
            except Exception:  # noqa: BLE001 落入下面的等待路径
                pass
        for _ in range(50):
            if chain.count() >= 5:
                return
            time.sleep(0.02)

    def test_csv_has_verify_digest_header(self, tmp_path):
        chain = self._chain(tmp_path)
        chain.append(action="a", actor="t", subject="s", payload={}, source="agent")
        csv_text = D.audit_export_csv(D.audit_export(limit=10, chain=chain))
        assert csv_text.splitlines()[0].startswith("# verify_ok=")
        assert "seq,ts,actor,action" in csv_text
        chain.close()


# ════════════════════════════════════════════════════════════
#  §3–§4 HTTP 面（权限、不旁路、批量裁决）
# ════════════════════════════════════════════════════════════


@pytest.fixture()
def app_client(monkeypatch):
    """Flask 测试客户端（token 未配置 ⇒ 走既有降级身份；矩阵仍生效）"""
    from agent.server_routes import routes_ui_panels as R
    from agent.server_routes import routes_approval as AR
    app = Flask(__name__)
    R.register_routes(app, None)
    R.reset_batches()
    app.config["TESTING"] = True
    return app.test_client(), R, AR


def _force_actor(monkeypatch, module, *, actor: str, actor_type: str,
                 scope: str = "") -> None:
    """把路由侧身份固定为指定执行体（`ActorContext` 是 frozen dataclass）"""
    from agent.security import approval_guard as guard

    def _ctx():
        return guard.ActorContext(actor=actor, actor_type=actor_type,
                                  identity_source="test", scope=scope,
                                  actor_ip="127.0.0.1")

    monkeypatch.setattr(module, "_actor_ctx", _ctx)


class TestReadEndpointsAuthorization:
    """**验收项：未授权访问被拒**"""

    def test_sub_agent_is_denied_on_every_read_panel(self, app_client, monkeypatch):
        client, R, _AR = app_client
        _force_actor(monkeypatch, R, actor="sub-1", actor_type="sub_agent")
        for path in ("/api/cp/panels", "/api/cp/digestion/pipeline",
                     "/api/cp/descriptors/map", "/api/cp/approvals/inbox",
                     "/api/cp/roi", "/api/cp/healing/incidents",
                     "/api/cp/memory/skills", "/api/cp/security/render-state",
                     "/api/cp/audit/export", "/api/cp/security/authz-alerts"):
            resp = client.get(path)
            assert resp.status_code == 403, f"{path} 未被拒绝"
            body = resp.get_json()
            assert body["ok"] is False
            assert body["decision"]["denied_by_matrix"] is True

    def test_auto_without_scope_is_denied(self, app_client, monkeypatch):
        """auto 仅自身 scope：未声明 scope 时目标无法匹配 ⇒ 拒绝"""
        client, R, _AR = app_client
        _force_actor(monkeypatch, R, actor="skill-x", actor_type="auto",
                     scope="scope-a")
        resp = client.get("/api/cp/descriptors/map")
        assert resp.status_code == 403
        assert resp.get_json()["decision"]["matrix_hit"] is True

    def test_human_is_allowed(self, app_client):
        client, _R, _AR = app_client
        resp = client.get("/api/cp/panels")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert len(body["panels"]) == 6
        # 六面板优先级：P0 展开 / P1·P2 折叠
        prio = {p["panel"]: p["priority"] for p in body["panels"]}
        assert prio["digestion_pipeline"] == "P0"
        assert prio["approval_inbox"] == "P0"
        assert prio["capability_map"] == "P1"
        assert prio["memory_skills"] == "P2"


class TestSevenActions:
    """七动作：写动作走既有审批，不得旁路（U4）"""

    #: 五类的可判定文本（与 `boundary_words.BOUNDARY_PATTERNS` 的正则匹配；
    #: 前端**不得自定义**这些类别——常量来自 `/api/cp/security/render-state`）
    NEVER_TEXT = {
        "force-push": "git push --force origin master",
        "drop-database": "drop database prod",
        "permission-change": "chmod 777 /etc/shadow",
        "transfer": "transfer funds 1000 usd to account",
        "publish": "deploy to production",
    }

    def test_unknown_action_is_fail_closed(self, app_client):
        client, _R, _AR = app_client
        resp = client.post("/api/cp/actions/not-a-real-action", json={})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "unknown_action"

    def test_rollback_requires_bundle_hash(self, app_client):
        client, _R, _AR = app_client
        # §7.0：governance 类操作 reason 必填 ⇒ 缺 reason 由矩阵拒绝（403）
        # 故先验证「矩阵要求 reason」这一前置条件本身
        resp = client.post("/api/cp/actions/rollback", json={"target": ""})
        assert resp.status_code == 403
        assert resp.get_json()["decision"]["requires_reason"] is True

        resp = client.post("/api/cp/actions/rollback",
                           json={"target": "", "reason": "演练整包回滚"})
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["code"] == "missing_bundle_hash"
        assert body["requires_approval"] is True     # L5 恒定 True
        assert body["level"] == "L5"

    def test_rollback_rejects_component_subset(self, app_client):
        """★ U4：组件子集一律拒绝（不得旁路原子性闸门）"""
        client, _R, _AR = app_client
        resp = client.post("/api/cp/actions/rollback",
                           json={"target": "h" * 8,
                                 "reason": "只回滚代码组件（应被拒）",
                                 "components": ["code"]})
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["code"] == "partial_rollback_rejected"
        assert body["requires_approval"] is True

    def test_rollback_unknown_bundle_is_rejected_not_faked(self, app_client):
        client, _R, _AR = app_client
        resp = client.post("/api/cp/actions/rollback",
                           json={"target": "0" * 32, "reason": "演练"})
        # 台账里没有这个整包 ⇒ 拒绝（BundleNotFoundError 或完整性失败）
        assert resp.status_code == 409
        assert resp.get_json()["ok"] is False

    def test_l5_spec_requires_approval_is_fact(self):
        from agent.self_healing.levels import HealLevel, spec_for
        spec = spec_for(HealLevel.L5)
        assert spec.requires_approval is True
        assert spec.automated is False

    def test_never_automated_needs_confirmation_before_execution(self, app_client):
        """★ 验收项：永不自动化五类必须先拿 UI 单次确认"""
        client, _R, _AR = app_client
        for action, text in self.NEVER_TEXT.items():
            resp = client.post(f"/api/cp/actions/{action}",
                               json={"target": "t", "action_text": text})
            assert resp.status_code == 428, f"{action}: {resp.get_json()}"
            body = resp.get_json()
            assert body["code"] == "boundary_confirmation_required"
            assert body["boundary"]["allowed"] is False
            assert body["boundary"]["hits"], f"{action} 未命中边界词"

    def test_all_five_categories_are_covered(self):
        """五类齐全（不多不少），且与 boundary_words 的类别键同词（连字符化）"""
        from agent.guardrails.boundary_words import BOUNDARY_LABELS
        from agent.server_routes import routes_ui_panels as R
        assert len(R.NEVER_AUTOMATED_ACTIONS) == 5
        assert set(R.NEVER_AUTOMATED_ACTIONS) == {
            k.replace("_", "-") for k in BOUNDARY_LABELS}
        # 五类一律只走审批提案（本端点无执行分支）
        assert R.NEVER_AUTOMATED_OPERATION == "approval.submit"

    def test_confirmation_issue_and_consume_is_single_action_bound(self, app_client):
        """★ 单次 action + 60s：凭据只能核销一次，且不可自定义 TTL"""
        client, _R, _AR = app_client
        text = self.NEVER_TEXT["force-push"]
        issued = client.post("/api/cp/confirmations/force-push",
                             json={"target": "origin master",
                                   "action_text": text})
        assert issued.status_code == 200, issued.get_json()
        body = issued.get_json()
        assert body["max_ttl_seconds"] == 60.0
        assert body["single_action_bound"] is True
        assert body["accepts_text_approval"] is False
        assert body["single_use"] is True
        token = body["confirmation"]["token"]

        first = client.post("/api/cp/actions/force-push",
                            json={"target": "origin master", "action_text": text,
                                  "confirmation_token": token})
        assert first.status_code == 200, first.get_json()
        # 五类拿到确认也**不执行**：只转审批提案（§7）
        assert first.get_json()["executed"] is False
        assert first.get_json()["submitted_for_approval"] is True
        second = client.post("/api/cp/actions/force-push",
                             json={"target": "origin master", "action_text": text,
                                   "confirmation_token": token})
        assert second.status_code == 428      # 单次性：已核销 ⇒ 必须重新确认

    def test_confirmation_binds_action_not_text(self, app_client):
        """凭据绑定 action 摘要：换目标即失效（"文本批准"不成立）"""
        client, _R, _AR = app_client
        text = self.NEVER_TEXT["force-push"]
        issued = client.post("/api/cp/confirmations/force-push",
                             json={"target": "origin master",
                                   "action_text": text}).get_json()
        token = issued["confirmation"]["token"]
        resp = client.post("/api/cp/actions/force-push",
                           json={"target": "other-branch", "action_text": text,
                                 "confirmation_token": token})
        assert resp.status_code == 428
        assert resp.get_json()["boundary"]["verdict"] == "need_confirmation"

    def test_confirmation_ttl_cannot_be_overridden_from_body(self, app_client):
        """★ U1：TTL 不由前端自定义（60s 硬上限）——请求体里的 ttl 一律忽略"""
        client, _R, _AR = app_client
        text = self.NEVER_TEXT["drop-database"]
        issued = client.post("/api/cp/confirmations/drop-database",
                             json={"target": "prod", "action_text": text,
                                   "ttl_seconds": 86400}).get_json()
        assert issued["confirmation"]["ttl_seconds"] == 60.0
        assert issued["max_ttl_seconds"] == 60.0

    def test_trace_diff_is_read_only(self, app_client):
        client, _R, _AR = app_client
        resp = client.post("/api/cp/actions/trace_diff",
                           json={"target": "cp.builtin.read_file"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["read_only"] is True
        assert body["capability_id"] == "cp.builtin.read_file"

    def test_trace_diff_requires_target(self, app_client):
        client, _R, _AR = app_client
        resp = client.post("/api/cp/actions/trace_diff", json={})
        assert resp.status_code == 400

    def test_approve_action_redirects_to_approval_endpoint(self, app_client):
        client, _R, _AR = app_client
        resp = client.post("/api/cp/actions/approve", json={"target": "r1"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "use_approval_endpoint"

    def test_sub_agent_cannot_run_governance_action(self, app_client, monkeypatch):
        client, R, _AR = app_client
        _force_actor(monkeypatch, R, actor="sub-1", actor_type="sub_agent")
        resp = client.post("/api/cp/actions/switch_forge", json={"target": "x"})
        assert resp.status_code == 403
        assert resp.get_json()["decision"]["denied_by_matrix"] is True

    def test_governance_action_records_intent_and_names_executor(self, app_client):
        """熔断/降级等动作：鉴权 + 意图落账 + 返回既有执行入口（不新建执行器）"""
        client, _R, _AR = app_client
        resp = client.post("/api/cp/actions/degrade",
                           json={"target": "gpt-4o-mini",
                                 "reason": "主力模型超时"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["authorized"] is True
        assert "model_degrade" in body["executor"]
        assert body["intent"]["target"] == "gpt-4o-mini"


class TestApprovalBatch:
    """审批收件箱批量裁决：同策略同风险一键批 + 逐条复用同一安全链"""

    def _setup_flow(self, tmp_path, monkeypatch):
        from agent.security import approval_session as session_mod
        from agent.skills_mgmt.approval import ApprovalFlow
        from agent.server_routes import routes_approval as AR

        flow = ApprovalFlow(records_path=str(tmp_path / "appr.jsonl"), enabled=True)
        monkeypatch.setattr(AR, "_flow", flow)
        monkeypatch.setattr(AR, "_handlers_installed", True)
        store = session_mod.get_session_store()
        store.reset() if hasattr(store, "reset") else None
        return flow, store, session_mod

    def test_batch_link_then_batch_approve(self, tmp_path, monkeypatch):
        client, R, AR = app_client_factory()
        flow, store, sm = self._setup_flow(tmp_path, monkeypatch)
        # 同策略同风险：两条都是 stage.promote（不同 object_id 属同一策略/等级/风险）
        r1 = flow.submit("stage.promote", "cp.a", action="promote", actor="system")
        r2 = flow.submit("stage.promote", "cp.b", action="promote", actor="system")
        sid = store.open_session(actor="owner", actor_type="human")
        headers = {sm.CSRF_HEADER_NAME: sid.csrf_token}
        client.set_cookie(sm.SESSION_COOKIE_NAME, sid.session_id)

        link = client.post("/api/cp/approvals/batch/link",
                           json={"record_ids": [r1.record_id, r2.record_id]},
                           headers=headers)
        assert link.status_code == 200, link.get_json()
        batch_id = link.get_json()["batch_id"]

        out = client.post("/api/cp/approvals/batch",
                          json={"batch_id": batch_id, "decision": "approve",
                                "reason": "一键批（同策略同风险）"},
                          headers=headers)
        assert out.status_code == 200, out.get_json()
        body = out.get_json()
        assert body["requested"] == 2
        assert body["succeeded"] == 2
        assert flow.get(r1.record_id).state == "approved"
        assert flow.get(r2.record_id).state == "approved"

    def test_batch_rejects_mixed_policy_and_risk(self, tmp_path, monkeypatch):
        client, R, AR = app_client_factory()
        flow, store, sm = self._setup_flow(tmp_path, monkeypatch)
        r1 = flow.submit("stage.promote", "cp.a", action="promote", actor="system")
        r2 = flow.submit("skill", "s-1", action="params_submit", actor="system")

        sid = store.open_session(actor="owner", actor_type="human")
        client.set_cookie(sm.SESSION_COOKIE_NAME, sid.session_id)
        resp = client.post("/api/cp/approvals/batch/link",
                           json={"record_ids": [r1.record_id, r2.record_id]},
                           headers={sm.CSRF_HEADER_NAME: sid.csrf_token})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "mixed_batch"

    def test_batch_reject_requires_reason(self, tmp_path, monkeypatch):
        client, R, AR = app_client_factory()
        flow, store, sm = self._setup_flow(tmp_path, monkeypatch)
        r1 = flow.submit("stage.promote", "cp.a", action="promote", actor="system")
        sid = store.open_session(actor="owner", actor_type="human")
        client.set_cookie(sm.SESSION_COOKIE_NAME, sid.session_id)
        link = client.post("/api/cp/approvals/batch/link",
                           json={"record_ids": [r1.record_id]},
                           headers={sm.CSRF_HEADER_NAME: sid.csrf_token})
        assert link.status_code == 200, link.get_json()
        batch_id = link.get_json()["batch_id"]
        resp = client.post("/api/cp/approvals/batch",
                           json={"batch_id": batch_id, "decision": "reject"},
                           headers={sm.CSRF_HEADER_NAME: sid.csrf_token})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "reason_required"
        assert flow.get(r1.record_id).state == "pending_review"   # 未被动过

    def test_batch_unknown_batch_id_is_rejected(self, tmp_path, monkeypatch):
        client, R, AR = app_client_factory()
        self._setup_flow(tmp_path, monkeypatch)
        resp = client.post("/api/cp/approvals/batch",
                           json={"batch_id": "batch-none", "decision": "approve"})
        assert resp.status_code == 401
        assert resp.get_json()["code"] == "unknown_batch"

    def test_batch_link_requires_session(self, tmp_path, monkeypatch):
        client, R, AR = app_client_factory()
        flow, _store, _sm = self._setup_flow(tmp_path, monkeypatch)
        r1 = flow.submit("stage.promote", "cp.a", action="promote", actor="system")
        resp = client.post("/api/cp/approvals/batch/link",
                           json={"record_ids": [r1.record_id]})
        assert resp.status_code == 401


def app_client_factory():
    """批量裁决用例的小工具：独立 Flask app + 干净批量集合"""
    from agent.server_routes import routes_ui_panels as R
    from agent.server_routes import routes_approval as AR
    app = Flask(__name__)
    R.register_routes(app, None)
    R.reset_batches()
    app.config["TESTING"] = True
    return app.test_client(), R, AR


# ════════════════════════════════════════════════════════════
#  §6 性能口径（真实台账量级）
# ════════════════════════════════════════════════════════════


class TestPerformanceBudget:
    """§2.6 P7.2-24：聚合 <200ms / 明细分页 <1s（实测，非声明）

    口径（**必须标注**，否则等于谎报）：
        - 计时用 ``time.perf_counter``（单调墙钟）；
        - 台账一次性加载（descriptor 台账 / 首读文件页缓存）**不计入**聚合预算，
          与生产一致：进程内经其他路由预热后再测量；
        - 取 3 次的中位数（单次含 GC 抖动），即"稳态聚合耗时"。
    """

    @staticmethod
    def _median_ms(fn, rounds: int = 3) -> float:
        fn()                                     # 预热（台账加载 / 首次读页）
        samples = []
        for _ in range(rounds):
            t0 = time.perf_counter()
            fn()
            samples.append((time.perf_counter() - t0) * 1000)
        samples.sort()
        return samples[len(samples) // 2]

    def test_pipeline_aggregate_under_200ms(self, tmp_path):
        events_dir = tmp_path / "events"
        events_dir.mkdir(parents=True)
        # 造 2000 条真实结构事件（模拟真实台账量级）
        with open(events_dir / "events.jsonl", "w", encoding="utf-8") as fh:
            for i in range(2000):
                fh.write(json.dumps({
                    "v": 1, "event_id": f"e{i}", "ts": f"{_today()}T08:00:0{i % 10}",
                    "correlation_id": "c", "actor": "auto", "type": "digest.stage",
                    "payload": {"capability_id": f"cp.c{i % 50}",
                                "scope": "digestion", "applied": i % 3 == 0,
                                "to_stage": "borrowed"}}, ensure_ascii=False) + "\n")
        from agent.descriptors.registry import DescriptorRegistry
        registry = DescriptorRegistry(autosave=False)
        registry.list_with_trust()               # 台账预热（一次性）

        def _run():
            return D.pipeline_view(days=7, events_dir=str(events_dir),
                                   shadow_dir=str(tmp_path / "s"),
                                   promote_dir=str(tmp_path / "p"),
                                   registry=registry)

        view = _run()
        assert view["summary"]["digest_stage_events"]["value"] == 2000
        elapsed_ms = self._median_ms(_run)
        assert elapsed_ms < 200, f"稳态聚合中位耗时 {elapsed_ms:.1f}ms 超出 200ms 预算"

    def test_detail_pagination_under_1s(self, tmp_path):
        events_dir = tmp_path / "events"
        events_dir.mkdir(parents=True)
        with open(events_dir / "events.jsonl", "w", encoding="utf-8") as fh:
            for i in range(3000):
                fh.write(json.dumps({
                    "v": 1, "event_id": f"e{i}", "ts": f"{_today()}T08:00:0{i % 10}",
                    "correlation_id": "c", "actor": "auto", "type": "digest.stage",
                    "payload": {"capability_id": f"cp.c{i % 50}",
                                "scope": "digestion"}}, ensure_ascii=False) + "\n")
        from agent.descriptors.registry import DescriptorRegistry
        registry = DescriptorRegistry(autosave=False)
        registry.list_with_trust()

        def _run():
            return D.pipeline_view(days=7, limit=500, events_dir=str(events_dir),
                                   shadow_dir=str(tmp_path / "s"),
                                   promote_dir=str(tmp_path / "p"),
                                   registry=registry)

        view = _run()
        # 明细分页：每列上限 500（虚拟滚动阈值同源）
        for lane in view["lanes"]:
            assert len(lane["items"]) <= 500
        elapsed_ms = self._median_ms(_run)
        assert elapsed_ms < 1000, f"明细分页中位耗时 {elapsed_ms:.1f}ms 超出 1s 预算"

    def test_limit_is_clamped_to_virtual_scroll_threshold(self, tmp_path):
        view = D.pipeline_view(days=7, limit=99999,
                               events_dir=str(tmp_path / "e"),
                               shadow_dir=str(tmp_path / "s"),
                               promote_dir=str(tmp_path / "p"))
        for lane in view["lanes"]:
            assert len(lane["items"]) <= 500


class TestNoUntraceablePercentages:
    """五坑⑤红线自查：面板响应里不存在不可追溯的百分比"""

    def test_all_panels_pass_scan(self, tmp_path):
        from agent.skills_mgmt.approval import ApprovalFlow
        flow = ApprovalFlow(records_path=str(tmp_path / "a.jsonl"), enabled=True)
        flow.submit("stage.promote", "cp.a", action="promote", actor="system")
        payloads = {
            "pipeline": D.pipeline_view(days=7, events_dir=str(tmp_path / "e"),
                                        shadow_dir=str(tmp_path / "s"),
                                        promote_dir=str(tmp_path / "p")),
            "inbox": D.approval_inbox(flow=flow),
            "roi": D.roi_view(days=7, events_dir=str(tmp_path / "e")),
            "incidents": D.incidents_view(incidents_dir=str(tmp_path / "i"),
                                          events_dir=str(tmp_path / "e")),
            "memory": D.memory_skills_view(limit=2),
            "render": D.security_render_state(),
        }
        for name, payload in payloads.items():
            report = S.untraceable_scan(payload)
            assert report["ok"] is True, f"{name}: {report['violations'][:5]}"
