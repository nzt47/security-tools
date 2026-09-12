#!/usr/bin/env python3
"""自愈语义层 L1-L5 单元测试（TASK-S4-03 步骤 1 / v7.2 §4.4）

覆盖验收项：
    1. 「无同名不同义残留」——健康探针五层（l1_process…）与自愈升级五级（L1…L5）
       语义正交，`assert_no_level_confusion()` 是机器可读证据；
    2. 升级链判定（level_of / spec_for / next_level / escalate / resolve_level）；
    3. 事故卡六要素齐备才可 resolved（缺要素抛 `LevelError`，绝不静默降级）；
    4. 事故卡归档的单写者纪律 + **显式路径**；
    5. 映射表导出（5 行 + 缺口非空）；
    6. 发射 best-effort（事件层不可用/级别非法 → 返回 None，绝不抛）。

【路径纪律】一切落盘一律显式指向 `tmp_path`；本文件绝不写仓库 `data/`。
【隔离纪律】模块级 writer 登记逐用例复位；事件目录与审计链逐用例改道 tmp。
"""
import importlib
import json
import logging
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent.self_healing import levels as levels_mod
from agent.self_healing.levels import (
    DEFAULT_INCIDENTS_DIR,
    ENV_ENABLED,
    ENV_INCIDENTS_DIR,
    HEALTH_PROBE_LAYERS,
    INCIDENT_ELEMENTS,
    LEVEL_ORDER,
    LEVEL_SPECS,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    SCOPE_BUNDLE,
    SCOPE_COMPONENT,
    SCOPE_HOST,
    SCOPE_PROCESS,
    SCOPE_TENANT,
    STATUS_OPEN,
    STATUS_RESOLVED,
    TRIGGER_SIGNALS,
    HealLevel,
    IncidentCard,
    LevelError,
    SagaRequiredError,
    SingleWriterViolationError,
    active_incident_writers,
    assert_no_level_confusion,
    emit_healing_triggered,
    escalate,
    incident_path,
    incidents_dir,
    level_of,
    list_incidents,
    load_incident,
    mapping_table,
    next_level,
    register_incident_writer,
    release_incident_writer,
    render_mapping_markdown,
    raise_incident,
    record_healing_audit,
    reset_incident_writers,
    resolve_level,
    save_incident,
    spec_for,
)

#: 触发信号 → 期望级别（§4.4 判定表；顺序敏感：越严重的信号优先命中）
EXPECTED_SIGNAL_LEVELS = {
    "tenant_scope_breach": HealLevel.L5,
    "snapshot_restore_failed": HealLevel.L5,
    "partial_rollback": HealLevel.L4,
    "compensation_failed": HealLevel.L4,
    "saga_compensated": HealLevel.L4,
    "verified_failure": HealLevel.L3,
    "restart_required": HealLevel.L3,
    "sustained_degradation": HealLevel.L2,
    "quality_regression": HealLevel.L2,
    "transient_failure": HealLevel.L1,
}

#: v7.2 §4.4 逐字语义（改一个字符即视为语义漂移）
VERBATIM_SEMANTICS = {
    HealLevel.L1: "L1 重试≤2→降级→人工",
    HealLevel.L2: "L2 劣化自动 downgrade",
    HealLevel.L3: "L3 kill→git revert→重启→负面样本",
    HealLevel.L4: "L4 journal 补偿→快照",
    HealLevel.L5: "L5 租户级回滚+最高告警",
}


@pytest.fixture(autouse=True)
def _isolate_levels_state(monkeypatch, tmp_path):
    """逐用例隔离：writer 登记复位 + 事件目录改道 tmp + 审计链停写

    Why: 本层发射与事故卡归档都走进程级单例（事件 store / 审计门面 /
    writer 登记表）。不复位会让一个用例的登记泄漏到后续用例，或让归档
    落进仓库 `data/` —— 与 S3-02/S3-03「落盘污染」同构。
    """
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    events_mod = None
    try:
        import agent.observability.events as events_mod  # noqa: F401
        events_mod.reset_event_stores()
    except Exception:  # noqa: BLE001 事件层不可用时测试照跑
        events_mod = None
    try:
        from agent.audit import facade as audit_facade
        monkeypatch.setattr(audit_facade.audit, "_enabled", False, raising=False)
    except Exception:  # noqa: BLE001
        pass
    reset_incident_writers()
    yield
    reset_incident_writers()
    if events_mod is not None:
        events_mod.reset_event_stores()


def _complete_card(**overrides) -> IncidentCard:
    """构造一张六要素齐备的事故卡（默认 L4）"""
    payload = dict(
        severity=HealLevel.L4,
        root_cause="权重基线漂移导致检索质量下降",
        fatal_change="abc1234",
        evasion_rule="policy_id:p-42",
        in_strategy_memory={"yes": True, "id": "mem-1"},
        regression_case_added={"yes": True, "case_id": "case-7"},
        trace_ids=["trace-1", "trace-2"],
    )
    payload.update(overrides)
    return IncidentCard(**payload)


# ════════════════════════════════════════════════════════════
#  1. 两套「五层」不得互相冒充
# ════════════════════════════════════════════════════════════


class TestLevelChainDiscipline:
    """自愈五级 ↔ 健康探针五层：编号重合但语义正交"""

    def test_assert_no_level_confusion_passes(self):
        """纪律断言本身可执行且通过（无返回值，不抛即合格）"""
        assert assert_no_level_confusion() is None

    def test_two_five_layer_sets_do_not_collide(self):
        """探针层名与自愈级名零交集，且形态可区分（小写带后缀 vs 纯 L<n>）"""
        level_names = {lv.value for lv in LEVEL_ORDER}
        probe_names = set(HEALTH_PROBE_LAYERS)
        assert level_names.isdisjoint(probe_names)
        assert len(probe_names) == 5 and len(level_names) == 5
        for probe in HEALTH_PROBE_LAYERS:
            assert probe.islower(), probe
            assert "l1_process" != HealLevel.L1.value
        for level in LEVEL_ORDER:
            assert level.value.startswith("L") and level.value[1:].isdigit(), level

    def test_level_order_and_specs_are_consistent(self):
        """LEVEL_ORDER 每一项都有 LEVEL_SPECS 条目，且 spec.level 与键一致"""
        assert len(LEVEL_ORDER) == 5
        for level in LEVEL_ORDER:
            assert level in LEVEL_SPECS, level
            assert LEVEL_SPECS[level].level is level

    def test_verbatim_semantics_present_for_all_five(self):
        """v7.2 §4.4 五级语义逐字在位，且 to_dict 原样透出"""
        for level, text in VERBATIM_SEMANTICS.items():
            assert LEVEL_SPECS[level].semantic == text
            assert LEVEL_SPECS[level].to_dict()["semantic"] == text

    def test_level_spec_escalation_attributes(self):
        """副作用强度随级别递增：半径/告警/审批/自动化/负面样本"""
        expected_scope = [SCOPE_PROCESS, SCOPE_COMPONENT, SCOPE_HOST, SCOPE_BUNDLE, SCOPE_TENANT]
        expected_severity = [SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_HIGH,
                             SEVERITY_HIGH, SEVERITY_CRITICAL]
        expected_negative = [False, False, True, True, True]
        for level, scope, severity, negative in zip(
                LEVEL_ORDER, expected_scope, expected_severity, expected_negative):
            spec = LEVEL_SPECS[level]
            assert spec.scope == scope, level
            assert spec.alert_severity == severity, level
            assert spec.negative_sample is negative, level
            assert spec.actions, level
            assert spec.legacy_facilities, level
        # 唯一不可自动化的级别是 L5（须人工审批）
        assert LEVEL_SPECS[HealLevel.L5].automated is False
        assert LEVEL_SPECS[HealLevel.L5].requires_approval is True
        assert [lv for lv in LEVEL_ORDER if not LEVEL_SPECS[lv].automated] == [HealLevel.L5]


# ════════════════════════════════════════════════════════════
#  2. 级别解析与升级链
# ════════════════════════════════════════════════════════════


class TestLevelResolution:
    """level_of / spec_for / next_level / escalate"""

    def test_level_of_accepts_loose_forms(self):
        """宽松解析：枚举 / 大写 / 小写 / 带空白 → 同一级别"""
        assert level_of(HealLevel.L3) is HealLevel.L3
        assert level_of("L3") is HealLevel.L3
        assert level_of("l3") is HealLevel.L3
        assert level_of("  l3  ") is HealLevel.L3

    @pytest.mark.parametrize("bad", [None, "", "   ", "L6", "L0", "five", "process", 5, object()])
    def test_level_of_unknown_returns_none(self, bad):
        """未知输入一律 None（不抛、不猜）"""
        assert level_of(bad) is None

    def test_spec_for_known_and_unknown(self):
        """spec_for：已知级别给规格；未知返回 None（不抛）"""
        assert spec_for("l1").code == "retry_degrade_human"
        assert spec_for(HealLevel.L5).code == "tenant_rollback_max_alert"
        assert spec_for("L9") is None
        assert spec_for(None) is None

    def test_next_level_walks_chain(self):
        """升级链逐级前进，未知返回 None"""
        assert next_level(HealLevel.L1) is HealLevel.L2
        assert next_level("l2") is HealLevel.L3
        assert next_level("L3") is HealLevel.L4
        assert next_level("L4") is HealLevel.L5
        assert next_level("L5") is None
        assert next_level("bogus") is None

    def test_escalate_chain_tail_does_not_overflow(self):
        """链尾 L5 保持 L5（只进不退，无越级）"""
        assert escalate(HealLevel.L1) is HealLevel.L2
        assert escalate("l3") is HealLevel.L4
        assert escalate("L4") is HealLevel.L5
        assert escalate(HealLevel.L5) is HealLevel.L5
        assert escalate("L5") is HealLevel.L5

    def test_escalate_unknown_falls_back_to_lightest(self, caplog):
        """【实现期修正】未知/None 输入告警后回退**最轻一级 L1**，绝不越级到重动作

        第一版对未知输入返回 L5（租户级回滚 + 最高告警 + 人工审批）——那与
        `resolve_level()`「宁可少做也不越级执行重动作」在同一模块里是相反口径；
        现统一为 `default=L1`：解析失败不得成为升级到最重动作的理由。
        """
        with caplog.at_level(logging.WARNING, logger="agent.self_healing.levels"):
            assert escalate(None) is HealLevel.L1
            assert escalate("no_such_level") is HealLevel.L1
            assert escalate("") is HealLevel.L1
            assert escalate("L9") is HealLevel.L1
        assert any("未知级别" in rec.getMessage() for rec in caplog.records)

    def test_escalate_unknown_respects_explicit_default(self):
        """`default=` 可显式抬高兜底；已知级别不受 default 影响（仍逐级上升）"""
        assert escalate(None, default=HealLevel.L3) is HealLevel.L3
        assert escalate("bogus", default=HealLevel.L5) is HealLevel.L5
        assert escalate("bogus", default=HealLevel.L4) is HealLevel.L4
        assert escalate("L2", default=HealLevel.L5) is HealLevel.L3
        assert escalate("L5", default=HealLevel.L1) is HealLevel.L5


# ════════════════════════════════════════════════════════════
#  3. 触发信号判定
# ════════════════════════════════════════════════════════════


class TestResolveLevel:
    """resolve_level：信号 → 级别（未知只告警不抛）"""

    def test_trigger_signals_cover_expected_table(self):
        """TRIGGER_SIGNALS 与 §4.4 判定表一致（顺序即优先级）"""
        assert list(TRIGGER_SIGNALS) == list(EXPECTED_SIGNAL_LEVELS)

    @pytest.mark.parametrize("signal", list(EXPECTED_SIGNAL_LEVELS))
    def test_resolve_level_for_every_trigger_signal(self, signal):
        """每一个已登记信号都解析到期望级别（含大小写与空白容错）"""
        assert resolve_level(signal) is EXPECTED_SIGNAL_LEVELS[signal]
        assert resolve_level(signal.upper()) is EXPECTED_SIGNAL_LEVELS[signal]
        assert resolve_level(f"  {signal}  ") is EXPECTED_SIGNAL_LEVELS[signal]

    def test_resolve_level_unknown_signal_warns_and_defaults_l1(self, caplog):
        """未知信号：兜底 L1（从最轻一级开始）并告警，**不抛**"""
        with caplog.at_level(logging.WARNING, logger="agent.self_healing.levels"):
            assert resolve_level("no_such_signal") is HealLevel.L1
        assert any("未知自愈触发信号" in rec.getMessage() for rec in caplog.records)

    def test_resolve_level_custom_default(self):
        """显式 default 覆盖兜底级别；空信号静默走兜底（不告警、不抛）"""
        assert resolve_level("nope", default=HealLevel.L3) is HealLevel.L3
        assert resolve_level(None) is HealLevel.L1
        assert resolve_level("") is HealLevel.L1
        assert resolve_level(None, default=HealLevel.L4) is HealLevel.L4


# ════════════════════════════════════════════════════════════
#  4. 事故卡六要素（§3）
# ════════════════════════════════════════════════════════════


class TestIncidentCardElements:
    """六要素齐备才可 resolved"""

    def test_empty_card_lists_all_six_missing(self):
        """空卡缺全部六要素，顺序与 INCIDENT_ELEMENTS 一致"""
        card = IncidentCard(severity=HealLevel.L1)
        assert card.missing_elements() == list(INCIDENT_ELEMENTS)
        assert len(INCIDENT_ELEMENTS) == 6

    def test_is_resolvable_requires_all_six(self):
        """逐项填齐的过程中始终不可 resolved，填满最后一项才可"""
        card = IncidentCard(severity=HealLevel.L4, root_cause="根因")
        assert card.is_resolvable() is False
        card.fatal_change = "deadbeef"
        card.evasion_rule = "policy_id:p-1"
        card.in_strategy_memory = {"yes": True, "id": "mem-1"}
        assert card.is_resolvable() is False
        card.regression_case_added = {"yes": True, "case_id": "case-1"}
        assert card.is_resolvable() is False
        card.trace_ids = ["t-1"]
        assert card.missing_elements() == []
        assert card.is_resolvable() is True

    def test_blank_strings_and_false_flags_do_not_count(self):
        """空白串/假标志/空 trace 不算要素（防「填了空字符串就算齐」）"""
        card = IncidentCard(
            severity=HealLevel.L4, root_cause="   ", fatal_change="\t",
            evasion_rule="", in_strategy_memory={"yes": False, "id": ""},
            regression_case_added={"yes": False, "case_id": ""}, trace_ids=["", "  "])
        assert set(card.missing_elements()) == set(INCIDENT_ELEMENTS)

    def test_resolve_raises_when_elements_missing(self):
        """缺要素 resolve → LevelError（绝不静默转 resolved）"""
        card = IncidentCard(severity=HealLevel.L4, root_cause="只有根因")
        with pytest.raises(LevelError) as exc:
            card.resolve()
        assert "不得 resolved" in str(exc.value)
        assert card.status == STATUS_OPEN
        assert card.resolved_at == ""

    def test_resolve_marks_resolved_when_complete(self):
        """六要素齐备 → status=resolved 且 resolved_at 落值（链式返回自身）"""
        card = _complete_card()
        assert card.resolve() is card
        assert card.status == STATUS_RESOLVED
        assert card.resolved_at
        assert card.is_resolvable() is True

    def test_to_dict_from_dict_round_trip(self):
        """to_dict / from_dict 往返：六要素、骨架字段、detail 全保留"""
        card = _complete_card(mttd_ms=120.0, mttr_ms=880.0, tenant_id="tenant-a",
                              detail={"signal": "compensation_failed"})
        card.resolve()
        payload = card.to_dict()
        assert payload["severity"] == "L4"
        assert payload["missing_elements"] == []
        restored = IncidentCard.from_dict(payload)
        assert restored.incident_id == card.incident_id
        assert restored.severity is HealLevel.L4
        assert restored.status == STATUS_RESOLVED
        assert restored.resolved_at == card.resolved_at
        assert restored.tenant_id == "tenant-a"
        assert restored.trace_ids == ["trace-1", "trace-2"]
        assert restored.detail == {"signal": "compensation_failed"}
        assert restored.to_dict() == payload

    def test_from_dict_tolerates_garbage_severity(self):
        """severity 非法/缺失 → 回退 L1（不抛）"""
        for bad in ("L9", "critical", "", None, 123, {"x": 1}):
            card = IncidentCard.from_dict({"severity": bad, "root_cause": "r"})
            assert card.severity is HealLevel.L1
        assert IncidentCard.from_dict({}).severity is HealLevel.L1


# ════════════════════════════════════════════════════════════
#  5. 事故卡归档（显式路径 + 单写者）
# ════════════════════════════════════════════════════════════


class TestIncidentArchive:
    """save / load / list：一律显式目录"""

    def test_save_and_load_round_trip(self, tmp_path):
        """落盘到显式目录后可原样读回，且不留 .tmp 残留"""
        card = _complete_card()
        path = save_incident(card, directory=str(tmp_path))
        assert path == incident_path(card.incident_id, directory=str(tmp_path))
        assert path.exists()
        assert not list(tmp_path.glob("*.tmp"))
        loaded = load_incident(card.incident_id, directory=str(tmp_path))
        assert loaded is not None
        assert loaded.incident_id == card.incident_id
        assert loaded.severity is HealLevel.L4
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["id"] == card.incident_id

    def test_load_missing_and_corrupt_returns_none(self, tmp_path):
        """不存在/损坏的事故卡读取返回 None（不抛）"""
        assert load_incident("inc-not-exist", directory=str(tmp_path)) is None
        bad = tmp_path / "inc-broken.json"
        bad.write_text("{不是 JSON", encoding="utf-8")
        assert load_incident("inc-broken", directory=str(tmp_path)) is None

    def test_list_incidents_filters_by_severity_and_status(self, tmp_path):
        """list 支持 severity / status 过滤，损坏文件跳过"""
        l4_open = _complete_card()
        l5_open = _complete_card(severity=HealLevel.L5)
        l4_resolved = _complete_card()
        l4_resolved.resolve()
        for card in (l4_open, l5_open, l4_resolved):
            save_incident(card, directory=str(tmp_path))
        (tmp_path / "inc-junk.json").write_text("nonsense", encoding="utf-8")

        assert len(list_incidents(directory=str(tmp_path))) == 3
        assert len(list_incidents(directory=str(tmp_path), severity="L4")) == 2
        assert len(list_incidents(directory=str(tmp_path), severity=HealLevel.L5)) == 1
        assert len(list_incidents(directory=str(tmp_path), status=STATUS_RESOLVED)) == 1
        assert len(list_incidents(directory=str(tmp_path), severity="L4",
                                  status=STATUS_OPEN)) == 1
        assert list_incidents(directory=str(tmp_path), severity="L2") == []

    def test_list_incidents_empty_directory(self, tmp_path):
        """目录不存在 → 空列表（不创建目录、不抛）"""
        missing = tmp_path / "nope"
        assert list_incidents(directory=str(missing)) == []
        assert not missing.exists()

    def test_incidents_dir_precedence(self, tmp_path, monkeypatch):
        """目录优先级：显式 > 环境变量 > 默认（默认值即 data/...，不得被本用例写）"""
        monkeypatch.setenv(ENV_INCIDENTS_DIR, str(tmp_path / "env-dir"))
        assert incidents_dir(str(tmp_path / "explicit")) == tmp_path / "explicit"
        assert incidents_dir() == tmp_path / "env-dir"
        monkeypatch.delenv(ENV_INCIDENTS_DIR, raising=False)
        assert incidents_dir() == type(tmp_path)(DEFAULT_INCIDENTS_DIR)
        assert DEFAULT_INCIDENTS_DIR.startswith("data")

    def test_single_writer_violation_and_release_semantics(self, tmp_path):
        """单写者纪律：同路径第二个 owner 被拒；同 owner 幂等；错 owner 释放是 no-op"""
        path = incident_path("inc-writer", directory=str(tmp_path))
        register_incident_writer(path, "owner-a")
        register_incident_writer(path, "owner-a")  # 同 owner 幂等
        assert active_incident_writers()[str(path.resolve())] == "owner-a"
        with pytest.raises(SingleWriterViolationError):
            register_incident_writer(path, "owner-b")
        release_incident_writer(path, "owner-b")  # 错 owner 不得误释放
        assert str(path.resolve()) in active_incident_writers()
        release_incident_writer(path, "owner-a")
        assert active_incident_writers() == {}

    def test_save_incident_second_writer_raises(self, tmp_path):
        """登记了他人 owner 后，save_incident 不得悄悄改写同一路径"""
        card = _complete_card()
        path = incident_path(card.incident_id, directory=str(tmp_path))
        register_incident_writer(path, "other-owner")
        with pytest.raises(SingleWriterViolationError):
            save_incident(card, directory=str(tmp_path), writer="self_healing.levels")


# ════════════════════════════════════════════════════════════
#  6. 映射表导出
# ════════════════════════════════════════════════════════════


class TestMappingTable:
    """验收交付物「自愈语义映射表」"""

    def test_mapping_table_five_rows_with_gap(self):
        """5 行；每行级别正确、缺口非空、语义与规格一致"""
        rows = mapping_table()
        assert len(rows) == 5
        assert [r["level"] for r in rows] == ["L1", "L2", "L3", "L4", "L5"]
        for row in rows:
            assert row["gap"].strip(), row["level"]
            assert row["v7.2_semantic"] == VERBATIM_SEMANTICS[HealLevel(row["level"])]
            assert row["legacy_facilities"], row["level"]

    def test_mapping_table_marks_l4_l5_as_newly_filled_gap(self):
        """L4/L5 的缺口文案必须写明「此前完全缺失」（否则会把新增当既有）"""
        gaps = {row["level"]: row["gap"] for row in mapping_table()}
        assert "完全缺失" in gaps["L4"] and "完全缺失" in gaps["L5"]
        assert "saga" in gaps["L4"]

    def test_render_mapping_markdown_has_five_data_rows(self):
        """Markdown 表：表头 + 分隔行 + 5 个数据行"""
        text = render_mapping_markdown()
        lines = text.splitlines()
        assert lines[0].startswith("| v7.2 级别 |")
        assert set(lines[1].replace("|", "").strip()) == {"-"}
        data_rows = [ln for ln in lines if ln.startswith("| **L")]
        assert len(data_rows) == 5
        for row in data_rows:
            assert len([cell for cell in row.split("|") if cell.strip()]) == 10
        assert "**否**" in text  # L5 自动化列显式否


# ════════════════════════════════════════════════════════════
#  7. 发射 best-effort（绝不成为新故障源）
# ════════════════════════════════════════════════════════════


class TestEmitBestEffort:
    """事件/审计发射：失败一律吞掉，不抛"""

    @pytest.mark.parametrize("bad", [None, "", "L9", "critical", 42, object()])
    def test_emit_invalid_level_returns_none_without_raising(self, bad):
        """级别非法 → 返回 None，绝不抛"""
        assert emit_healing_triggered(bad) is None

    def test_emit_returns_none_when_disabled_by_env(self, monkeypatch):
        """总开关关闭 → 直接返回 None（连级别解析都不做）"""
        monkeypatch.setenv(ENV_ENABLED, "0")
        assert emit_healing_triggered(HealLevel.L4, signal="partial_rollback") is None
        assert record_healing_audit(HealLevel.L4) is None

    def test_emit_swallowed_when_event_layer_unavailable(self, monkeypatch):
        """事件层不可导入（ImportError）→ 吞掉并返回 None，主路径不受影响"""
        monkeypatch.setitem(sys.modules, "agent.observability.events", None)
        assert emit_healing_triggered(HealLevel.L5, signal="tenant_scope_breach") is None

    def test_emit_payload_leaf_fields_when_available(self, monkeypatch, tmp_path):
        """事件层可用时：载荷只放叶子字段，且带 MTTD/MTTR 契约字段

        显式开启事件层并指向 tmp（不留 skip 分支：本用例要么真断言，要么真失败）。
        """
        monkeypatch.setenv("CP_EVENTS_ENABLED", "1")
        monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
        events_mod = importlib.import_module("agent.observability.events")
        events_mod.reset_event_stores()
        envelope = emit_healing_triggered(
            HealLevel.L4, signal="compensation_failed", mttd_ms=1200.0, mttr_ms=9000.0,
            tenant_id="tenant-a", incident_id="inc-x", extra={"saga_id": "saga-1"})
        assert envelope is not None
        assert envelope.type == "healing.triggered"
        payload = envelope.payload
        assert payload["level"] == "L4"
        assert payload["level_code"] == "journal_compensate_snapshot"
        assert payload["signal"] == "compensation_failed"
        assert payload["mttd_ms"] == 1200.0 and payload["mttr_ms"] == 9000.0
        assert payload["tenant_id"] == "tenant-a"
        assert payload["incident_id"] == "inc-x"
        assert payload["saga_id"] == "saga-1"
        assert isinstance(payload["scope"], str) and isinstance(payload["severity"], str)
        events_mod.reset_event_stores()

    def test_record_healing_audit_never_raises(self, monkeypatch):
        """审计写入失败（门面不可用）也不得上抛"""
        monkeypatch.setitem(sys.modules, "agent.audit.facade", None)
        assert record_healing_audit(HealLevel.L3, subject="heal:L3") is None
        assert record_healing_audit("not-a-level") is None


# ════════════════════════════════════════════════════════════
#  8. raise_incident（含 L5 最高告警路径）
# ════════════════════════════════════════════════════════════


class TestRaiseIncident:
    """开卡三件套：落盘 + 审计 + 事件"""

    def test_raise_incident_writes_card_file(self, tmp_path):
        """落盘到显式目录，返回卡片且信息正确"""
        card = raise_incident(
            HealLevel.L4, signal="compensation_failed", root_cause="补偿失败",
            fatal_change="deadbeef", trace_ids=["t-1"], tenant_id="tenant-b",
            directory=str(tmp_path), detail={"saga_id": "saga-9"}, mttd_ms=50.0)
        assert isinstance(card, IncidentCard)
        assert card.severity is HealLevel.L4
        assert card.tenant_id == "tenant-b"
        assert card.mttd_ms == 50.0
        path = incident_path(card.incident_id, directory=str(tmp_path))
        assert path.exists()
        assert load_incident(card.incident_id, directory=str(tmp_path)).detail == \
            {"saga_id": "saga-9"}

    def test_raise_incident_unknown_level_falls_back_to_l1(self, tmp_path):
        """级别非法 → 兜底 L1（不是抛异常，也不是最高级）"""
        card = raise_incident("L9", directory=str(tmp_path))
        assert card.severity is HealLevel.L1

    def test_raise_incident_l5_returns_card_even_without_alert_subsystem(
            self, tmp_path, monkeypatch):
        """L5 走最高告警路径：告警子系统缺席也必须返回卡片（best-effort）"""
        monkeypatch.setitem(sys.modules, "agent.monitoring.alert_manager", None)
        monkeypatch.setitem(sys.modules, "agent.monitoring.alert_evaluator", None)
        card = raise_incident(
            HealLevel.L5, signal="tenant_scope_breach", root_cause="跨租户影响",
            directory=str(tmp_path))
        assert isinstance(card, IncidentCard)
        assert card.severity is HealLevel.L5
        assert incident_path(card.incident_id, directory=str(tmp_path)).exists()

    def test_raise_incident_computes_mttr_from_started_at(self, tmp_path):
        """给了 started_at → MTTR 非负毫秒；不给 → None"""
        import time as _time
        started = _time.perf_counter() - 0.05
        card = raise_incident(HealLevel.L3, directory=str(tmp_path), started_at=started)
        assert card.mttr_ms is not None and card.mttr_ms >= 45.0
        plain = raise_incident(HealLevel.L3, directory=str(tmp_path))
        assert plain.mttr_ms is None

    def test_levels_module_exports_are_complete(self):
        """`__all__` 与模块属性一致（防导出名与实际不符）"""
        for name in levels_mod.__all__:
            assert hasattr(levels_mod, name), name
        assert issubclass(SagaRequiredError, LevelError)
