#!/usr/bin/env python3
"""Saga 补偿事务单元测试（TASK-S4-03 步骤 3 / v7.2 §4.6）

覆盖验收项：
    1. **三态齐**：prepare → execute → confirm 各写一条 journal，字段严格为 §4.6 七元；
    2. **高风险强制**：risk ≥ high 无 Saga 即拒（`SagaRequiredError`）；
       `undo_hint` 必须指向真实可执行动作（占位符/空 → `UndoHintError`）；
    3. **补偿幂等可重放**：已成功补偿的步骤不重复执行；
    4. **补偿失败必升级 L4**：补偿抛错 → 记失败条目 → 事故卡 + `escalate` 条目，绝不静默。

【路径纪律】journal 与事故卡目录一律显式指向 `tmp_path`，绝不写仓库 `data/`。
"""
import dataclasses
import enum
import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent.self_healing import saga as saga_mod
from agent.self_healing.levels import HealLevel, list_incidents, load_incident
from agent.self_healing.saga import (
    DEFAULT_JOURNAL_DIR,
    ENV_JOURNAL_DIR,
    HASH_ALGO,
    JOURNAL_FIELDS,
    JOURNAL_FILENAME,
    REQUIRED_STEPS,
    SAGA_REQUIRED_RISK,
    STEP_ABORT,
    STEP_COMPENSATE,
    STEP_CONFIRM,
    STEP_ESCALATE,
    STEP_EXECUTE,
    STEP_PREPARE,
    CompensationResult,
    JournalEntry,
    JournalWriteError,
    Saga,
    SagaJournal,
    SagaRequiredError,
    SagaState,
    SagaStateError,
    SagaStep,
    SingleWriterViolationError,
    UndoHintError,
    assert_entry_shape,
    check_undo_hint,
    extract_governance,
    extract_risk,
    hash_intent,
    hash_state,
    is_saga_required,
    journal_dir,
    make_saga_for,
    register_journal_writer,
    release_journal_writer,
    require_saga,
    reset_saga_state,
    risk_rank,
    step_kind,
)

#: S1-02 回填口径的**真实** undo_hint 样例（引用真实机制：停用技能 + 版本回退）
REAL_UNDO_HINT = (
    "撤销指引：xxx 为代码型技能，如判定为致命变更，先停用技能"
    "（SkillRegistry.set_enabled=false，见 SkillRegistry 接口），"
    "技能版本可经 rollback_version 回退。"
)

#: 占位符样例（一律不得通过）
PLACEHOLDER_HINTS = ["N/A", "n/a", "-", "—", "待补", "待定", "无", "暂无", "TODO", "未实现"]


@pytest.fixture(autouse=True)
def _isolate_saga_state(monkeypatch, tmp_path):
    """逐用例隔离：journal writer 登记复位 + 事件目录改道 tmp + 审计链停写"""
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    events_mod = None
    try:
        import agent.observability.events as events_mod  # noqa: F401
        events_mod.reset_event_stores()
    except Exception:  # noqa: BLE001
        events_mod = None
    try:
        from agent.audit import facade as audit_facade
        monkeypatch.setattr(audit_facade.audit, "_enabled", False, raising=False)
    except Exception:  # noqa: BLE001
        pass
    reset_saga_state()
    yield
    reset_saga_state()
    if events_mod is not None:
        events_mod.reset_event_stores()


def _journal(tmp_path, name: str = "journal.log", **kwargs) -> SagaJournal:
    """显式路径 journal（绝不落到仓库 data/saga）"""
    return SagaJournal(path=str(tmp_path / name), **kwargs)


def _saga(tmp_path, steps=None, **kwargs) -> Saga:
    """显式 journal + 显式事故卡目录的 Saga"""
    kwargs.setdefault("journal", _journal(tmp_path))
    kwargs.setdefault("incidents_dir", str(tmp_path / "incidents"))
    kwargs.setdefault("trace_id", "trace-saga-1")
    return Saga(steps=list(steps or []), **kwargs)


def _prepare_execute_confirm(saga: Saga, result=None):
    """跑完三态（默认 execute 返回确定性结果）"""
    saga.prepare({"op": "backfill"}, snapshot={"cfg": "before"})
    returned = saga.execute(lambda: (result if result is not None else {"cfg": "after"}))
    saga.confirm()
    return returned


class _RiskEnum(str, enum.Enum):
    """枚举形态的 risk（模拟 `agent.descriptors.models` 的 RiskLevel）"""

    LOW = "low"
    HIGH = "high"
    DESTRUCTIVE = "destructive"


class _Governance:
    """对象形态的 governance"""

    def __init__(self, undo_hint="", compensating_action="", policy_ref=""):
        self.undo_hint = undo_hint
        self.compensating_action = compensating_action
        self.policy_ref = policy_ref


class _Trust:
    """对象形态的 trust 字段组"""

    def __init__(self, risk_level=None):
        self.risk_level = risk_level


class _Descriptor:
    """对象形态的 ToolDescriptor（只带本模块用到的字段）"""

    def __init__(self, capability_id="skill.backfill", risk_level=None,
                 undo_hint="", compensating_action="", policy_ref=""):
        self.capability_id = capability_id
        self.trust = _Trust(risk_level)
        self.governance = _Governance(undo_hint, compensating_action, policy_ref)


# ════════════════════════════════════════════════════════════
#  1. journal 七元 schema（§4.6 逐字）
# ════════════════════════════════════════════════════════════


class TestJournalSchema:
    """JOURNAL_FIELDS / JournalEntry / assert_entry_shape"""

    def test_journal_fields_is_exact_seven_tuple(self):
        """七元字段名与顺序逐字等于 §4.6"""
        assert JOURNAL_FIELDS == (
            "saga_id", "step", "intent_hash", "before_hash", "after_hash", "ts", "trace_id")
        assert len(JOURNAL_FIELDS) == 7
        assert REQUIRED_STEPS == (STEP_PREPARE, STEP_EXECUTE, STEP_CONFIRM)
        assert SAGA_REQUIRED_RISK == "high"

    def test_entry_to_dict_keys_equal_journal_fields(self):
        """to_dict 键序列 == JOURNAL_FIELDS（无多余键、无缺键）"""
        entry = JournalEntry(saga_id="s-1", step=STEP_PREPARE, intent_hash=f"{HASH_ALGO}:i")
        payload = entry.to_dict()
        assert tuple(payload.keys()) == JOURNAL_FIELDS
        assert json.loads(entry.to_json()) == payload
        assert entry.ts  # 默认 now_ts 非空

    def test_assert_entry_shape_positive_path(self):
        """合法条目通过形状断言（返回 None，不抛）"""
        assert assert_entry_shape(
            JournalEntry(saga_id="s-1", step=STEP_EXECUTE, ts="2026-01-01T00:00:00")) is None

    def test_assert_entry_shape_rejects_extra_key(self):
        """多一个键即非法（防七元 schema 被悄悄扩字段）"""
        class _ExtraKeyEntry(JournalEntry):
            def to_dict(self):
                payload = super().to_dict()
                payload["extra_field"] = "x"
                return payload

        with pytest.raises(SagaStateError) as exc:
            assert_entry_shape(_ExtraKeyEntry(saga_id="s-1", step=STEP_PREPARE))
        assert "journal 条目字段非法" in str(exc.value)

    def test_assert_entry_shape_rejects_missing_key(self):
        """少一个键即非法"""
        class _MissingKeyEntry(JournalEntry):
            def to_dict(self):
                payload = super().to_dict()
                payload.pop("trace_id")
                return payload

        with pytest.raises(SagaStateError):
            assert_entry_shape(_MissingKeyEntry(saga_id="s-1", step=STEP_PREPARE))

    @pytest.mark.parametrize("blank_field", ["saga_id", "step", "ts"])
    def test_assert_entry_shape_rejects_blank_required_values(self, blank_field):
        """saga_id / step / ts 为空即非法（用 dataclasses.replace 构造）"""
        good = JournalEntry(saga_id="s-1", step=STEP_PREPARE)
        bad = dataclasses.replace(good, **{blank_field: ""})
        with pytest.raises(SagaStateError) as exc:
            assert_entry_shape(bad)
        assert blank_field in str(exc.value)

    def test_entry_from_dict_fills_missing_and_drops_extra(self):
        """from_dict 容错：缺字段补空串、多余键丢弃（仍恒为七元）"""
        entry = JournalEntry.from_dict({"saga_id": "s-2", "step": STEP_CONFIRM, "junk": 1})
        assert tuple(entry.to_dict().keys()) == JOURNAL_FIELDS
        assert entry.intent_hash == "" and entry.before_hash == "" and entry.after_hash == ""
        assert step_kind(f"{STEP_COMPENSATE}:write_cfg") == STEP_COMPENSATE
        assert step_kind(STEP_PREPARE) == STEP_PREPARE
        assert step_kind("") == ""


# ════════════════════════════════════════════════════════════
#  2. 状态哈希与风险序
# ════════════════════════════════════════════════════════════


class TestHashAndRiskRank:
    """hash_state / hash_intent / risk_rank / is_saga_required"""

    def test_hash_state_none_and_determinism(self):
        """None → 空串；同输入同哈希；键序无关；不同输入不同哈希"""
        assert hash_state(None) == ""
        assert hash_intent(None) == ""
        assert hash_state({"a": 1}) == hash_state({"a": 1})
        assert hash_state({"a": 1, "b": 2}) == hash_state({"b": 2, "a": 1})
        assert hash_state({"a": 1}) != hash_state({"a": 2})
        assert hash_state("x") == hash_intent("x")
        assert hash_state({"a": 1}).startswith(f"{HASH_ALGO}:")

    def test_hash_state_handles_unserializable_values(self):
        """不可序列化值走兜底（不抛），且仍确定"""
        first = hash_state({"s": {1, 2, 3}})
        assert first.startswith(f"{HASH_ALGO}:")
        assert first == hash_state({"s": {1, 2, 3}})
        assert first != hash_state(None)

    def test_risk_rank_ordering_and_unknown(self):
        """四级序 0..3；未知/None/空 → -1；枚举形态按其 value 解析"""
        assert risk_rank("low") == 0
        assert risk_rank("medium") == 1
        assert risk_rank("high") == 2
        assert risk_rank("destructive") == 3
        assert risk_rank(" HIGH ") == 2
        assert risk_rank(_RiskEnum.HIGH) == 2
        assert risk_rank(_RiskEnum.DESTRUCTIVE) == 3
        assert risk_rank(None) is -1
        assert risk_rank("") is -1
        assert risk_rank("catastrophic") is -1
        assert risk_rank(7) is -1

    def test_is_saga_required_matrix(self):
        """§4.6：risk ≥ high 才强制；未知一律不强制（不越权拦低风险）"""
        assert is_saga_required("low") is False
        assert is_saga_required("medium") is False
        assert is_saga_required("high") is True
        assert is_saga_required("destructive") is True
        assert is_saga_required(_RiskEnum.HIGH) is True
        assert is_saga_required(None) is False
        assert is_saga_required("garbage") is False


# ════════════════════════════════════════════════════════════
#  3. SagaJournal（显式路径 + 单写者）
# ════════════════════════════════════════════════════════════


class TestSagaJournal:
    """log / append / entries / steps_of / has_step / last / sagas / reset"""

    def test_explicit_path_write_and_read(self, tmp_path):
        """显式路径写入后可读；过滤、steps_of、has_step、last 全部正确"""
        journal = _journal(tmp_path)
        assert journal.path == tmp_path / JOURNAL_FILENAME
        journal.log("s-1", STEP_PREPARE, intent_hash="i1")
        journal.log("s-1", STEP_EXECUTE, before_hash="b1", after_hash="a1")
        journal.log("s-2", STEP_PREPARE, intent_hash="i2")
        journal.append(JournalEntry(saga_id="s-2", step=STEP_CONFIRM,
                                    trace_id="t-2"))   # append 正路径（log 的底层入口）
        assert journal.path.exists()

        assert journal.steps_of("s-1") == [STEP_PREPARE, STEP_EXECUTE]
        assert journal.steps_of("s-2") == [STEP_PREPARE, STEP_CONFIRM]
        assert [e.saga_id for e in journal.entries()] == ["s-1", "s-1", "s-2", "s-2"]
        assert len(journal.entries("s-1")) == 2
        assert journal.has_step("s-1", STEP_EXECUTE) is True
        assert journal.has_step("s-1", STEP_CONFIRM) is False
        assert journal.last("s-1").step == STEP_EXECUTE
        assert journal.last("s-2").trace_id == "t-2"
        assert journal.last("s-1", STEP_PREPARE).intent_hash == "i1"
        assert journal.last("s-9") is None
        assert journal.sagas() == ["s-1", "s-2"]

    def test_append_write_failure_raises_journal_write_error(self, tmp_path):
        """写失败必须抛（账写不下去不能继续做破坏性动作）：路径不可用 → JournalWriteError"""
        journal = SagaJournal(path=str(tmp_path))     # 目录不可当文件写
        with pytest.raises(JournalWriteError):
            journal.log("s-1", STEP_PREPARE)
        assert journal.entries() == []

    def test_append_rejects_malformed_entry(self, tmp_path):
        """append 直接受形状断言保护（缺 saga_id 写不进去）"""
        journal = _journal(tmp_path)
        with pytest.raises(SagaStateError):
            journal.append(JournalEntry(saga_id="", step=STEP_PREPARE))
        assert journal.entries() == []

    def test_three_phase_and_incomplete_sagas(self, tmp_path):
        """三态齐备判定与未完成事务清单（§4.6「再处理未完成事务」）"""
        journal = _journal(tmp_path)
        for step in REQUIRED_STEPS:
            journal.log("s-done", step)
        journal.log("s-pending", STEP_PREPARE)
        journal.log("s-compensated", STEP_PREPARE)
        journal.log("s-compensated", f"{STEP_COMPENSATE}:step_a", after_hash="h")
        journal.log("s-escalated", STEP_PREPARE)
        journal.log("s-escalated", STEP_ESCALATE, after_hash="h")

        assert journal.is_three_phase_complete("s-done") is True
        assert journal.is_three_phase_complete("s-pending") is False
        assert journal.is_three_phase_complete("s-unknown") is False
        assert journal.incomplete_sagas() == ["s-pending"]

    def test_tolerates_corrupt_line(self, tmp_path):
        """单行损坏只跳过该行（历史可重读是补偿幂等的前提）"""
        journal = _journal(tmp_path)
        good_line = json.dumps({"saga_id": "s-1", "step": STEP_PREPARE,
                                "ts": "2026-01-01T00:00:00.000+08:00"})
        journal.path.write_text(
            good_line + "\n{不是 JSON\n\n" + good_line.replace("s-1", "s-2") + "\n",
            encoding="utf-8")
        rows = journal.entries()
        assert [e.saga_id for e in rows] == ["s-1", "s-2"]
        assert journal.steps_of("s-1") == [STEP_PREPARE]

    def test_missing_file_and_reset(self, tmp_path):
        """文件不存在 → 空列表；reset() 清空文件（仅用例隔离用）"""
        journal = _journal(tmp_path)
        assert journal.entries() == []
        assert journal.sagas() == []
        journal.log("s-1", STEP_PREPARE)
        assert journal.path.exists()
        journal.reset()
        assert not journal.path.exists()
        assert journal.entries() == []

    def test_single_writer_violation_and_release(self, tmp_path):
        """同一 journal 路径第二个 owner 被拒；错 owner 释放是 no-op"""
        path = tmp_path / JOURNAL_FILENAME
        first = SagaJournal(path=str(path), writer="owner-a")
        first.log("s-1", STEP_PREPARE)
        second = SagaJournal(path=str(path), writer="owner-b")
        with pytest.raises(SingleWriterViolationError):
            second.log("s-1", STEP_EXECUTE)
        release_journal_writer(first.path, "owner-b")   # 错 owner：不得误释放
        third = SagaJournal(path=str(path), writer="owner-c")
        with pytest.raises(SingleWriterViolationError):
            third.log("s-1", STEP_CONFIRM)
        assert len(first.entries("s-1")) == 1

    def test_register_writer_same_owner_idempotent(self, tmp_path):
        """同 owner 重复登记幂等（不抛）"""
        path = tmp_path / JOURNAL_FILENAME
        register_journal_writer(path, "owner-a")
        register_journal_writer(path, "owner-a")
        with pytest.raises(SingleWriterViolationError):
            register_journal_writer(path, "owner-b")
        release_journal_writer(path, "owner-a")
        register_journal_writer(path, "owner-b")  # 释放后可换 owner

    def test_journal_dir_precedence(self, tmp_path, monkeypatch):
        """目录优先级：显式 > 环境变量 > 默认（默认即 data/saga）"""
        monkeypatch.setenv(ENV_JOURNAL_DIR, str(tmp_path / "env-journal"))
        assert journal_dir() == tmp_path / "env-journal"
        assert journal_dir(str(tmp_path / "explicit")) == tmp_path / "explicit"
        assert SagaJournal(directory=str(tmp_path / "d")).path == \
            tmp_path / "d" / JOURNAL_FILENAME
        assert SagaJournal().path == tmp_path / "env-journal" / JOURNAL_FILENAME
        monkeypatch.delenv(ENV_JOURNAL_DIR, raising=False)
        assert journal_dir() == type(tmp_path)(DEFAULT_JOURNAL_DIR)
        assert DEFAULT_JOURNAL_DIR.replace("\\", "/") == "data/saga"


# ════════════════════════════════════════════════════════════
#  4. 高风险强制闸门（§4.6：无强制即拒绝执行）
# ════════════════════════════════════════════════════════════


class TestRequireSaga:
    """require_saga / check_undo_hint"""

    def test_high_risk_without_saga_is_refused(self):
        """【headline】risk=high 且未提供 Saga → SagaRequiredError（拒绝执行）"""
        with pytest.raises(SagaRequiredError) as exc:
            require_saga({"capability_id": "skill.backfill", "risk_level": "high"})
        assert "拒绝执行" in str(exc.value)
        assert "skill.backfill" in str(exc.value)

    @pytest.mark.parametrize("risk", ["high", "destructive", _RiskEnum.HIGH])
    def test_destructive_and_enum_risk_without_saga_are_refused(self, risk):
        """destructive / 枚举形态同样强制（未提供 Saga 一律拒绝）"""
        with pytest.raises(SagaRequiredError):
            require_saga({"risk_level": risk})

    def test_low_risk_without_saga_returns_none(self):
        """risk=low/medium 不强制：无 Saga 时返回 None（不抛）"""
        assert require_saga({"risk_level": "low"}) is None
        assert require_saga({"risk_level": "medium"}) is None
        assert require_saga({"capability_id": "x"}) is None
        assert require_saga({}) is None

    def test_low_risk_returns_passed_saga(self, tmp_path):
        """低风险时原样返回传入的 saga（便于链式调用）"""
        saga = _saga(tmp_path)
        assert require_saga({"risk_level": "low"}, saga=saga, operation="noop") is saga

    def test_high_risk_with_valid_saga_and_real_hint_passes(self, tmp_path):
        """risk=high + 已 prepare 的 Saga + 真实 undo_hint → 通过"""
        saga = _saga(tmp_path)
        descriptor = {"capability_id": "skill.backfill", "risk_level": "high",
                      "governance": {"undo_hint": REAL_UNDO_HINT,
                                     "compensating_action": "rollback_version"}}
        assert require_saga(descriptor, saga=saga) is saga

    def test_high_risk_with_empty_undo_hint_is_refused(self, tmp_path):
        """空 undo_hint → UndoHintError（不执行）"""
        saga = _saga(tmp_path)
        with pytest.raises(UndoHintError) as exc:
            require_saga({"risk_level": "high", "governance": {"undo_hint": ""}}, saga=saga)
        assert "undo_hint 为空" in str(exc.value)
        with pytest.raises(UndoHintError):
            require_saga({"risk_level": "high"}, saga=saga)  # 整个 governance 缺失

    @pytest.mark.parametrize("hint", PLACEHOLDER_HINTS)
    def test_high_risk_with_placeholder_undo_hint_is_refused(self, hint, tmp_path):
        """占位符 undo_hint（N/A / 待补 / - …）→ UndoHintError"""
        saga = _saga(tmp_path)
        with pytest.raises(UndoHintError) as exc:
            require_saga({"risk_level": "high", "governance": {"undo_hint": hint}}, saga=saga)
        assert "拒绝执行" in str(exc.value)

    def test_check_hint_can_be_disabled_explicitly(self, tmp_path):
        """check_hint=False 时跳过 hint 校验（调用方自担风险，显式开关）"""
        saga = _saga(tmp_path)
        assert require_saga({"risk_level": "high", "governance": {"undo_hint": "待补"}},
                            saga=saga, check_hint=False) is saga


class TestCheckUndoHint:
    """undo_hint 必须指向真实可执行动作"""

    def test_real_s1_02_backfill_wording_passes(self):
        """【headline】S1-02 真实回填文案通过校验，且锚点可枚举"""
        verdict = check_undo_hint({"governance": {
            "undo_hint": REAL_UNDO_HINT, "compensating_action": "rollback_version"}})
        assert verdict["ok"] is True
        assert verdict["reason"] == ""
        assert verdict["anchors"]
        assert any("SkillRegistry" in a or "set_enabled" in a or "rollback_version" in a
                   for a in verdict["anchors"])
        assert verdict["compensating_action"] == "rollback_version"

    @pytest.mark.parametrize("hint", [
        "git revert HEAD",
        "回退：执行 snapshot 恢复上一版本",
        "由运维人工恢复配置",
        "调用 rollback_version 回退技能版本",
    ])
    def test_command_verbs_and_manual_handling_pass(self, hint):
        """命令动词 / 机制标识符 / 显式人工处置 → 通过"""
        assert check_undo_hint({"governance": {"undo_hint": hint}})["ok"] is True

    @pytest.mark.parametrize("hint", ["", "   "] + PLACEHOLDER_HINTS)
    def test_empty_and_placeholder_fail(self, hint):
        """空与占位符一律不通过（且给出可读原因）"""
        verdict = check_undo_hint({"governance": {"undo_hint": hint}})
        assert verdict["ok"] is False
        assert verdict["anchors"] == []
        assert verdict["reason"]

    def test_hint_without_executable_anchor_fails(self):
        """有文字但无可执行锚点 → 不通过（防"写了话但做不到"）"""
        verdict = check_undo_hint({"governance": {"undo_hint": "请谨慎处理，注意风险"}})
        assert verdict["ok"] is False
        assert "锚点" in verdict["reason"]

    def test_object_descriptor_form(self):
        """对象形态 descriptor（governance 为对象）同样可用"""
        good = _Descriptor(risk_level="high", undo_hint=REAL_UNDO_HINT)
        assert check_undo_hint(good)["ok"] is True
        bad = _Descriptor(risk_level="high", undo_hint="待补")
        assert check_undo_hint(bad)["ok"] is False
        assert check_undo_hint(None)["ok"] is False

    def test_extract_risk_from_dict_and_object(self):
        """extract_risk：dict 的 trust/顶层、对象形态、裸值、None 全覆盖"""
        assert extract_risk({"trust": {"risk_level": "high"}}) == "high"
        assert extract_risk({"risk_level": "destructive"}) == "destructive"
        assert extract_risk({"trust": {"risk_level": ""}, "risk_level": "low"}) == "low"
        assert extract_risk(_Descriptor(risk_level="high")) == "high"
        assert extract_risk(None) is None

    def test_extract_governance_from_dict_and_object(self):
        """extract_governance：dict / 对象 / None 全覆盖（对象形态转三字段 dict）"""
        assert extract_governance({"governance": {"undo_hint": "h"}}) == {"undo_hint": "h"}
        assert extract_governance({"governance": "not-a-mapping"}) == {}
        assert extract_governance({}) == {}
        assert extract_governance(None) == {}
        gov = extract_governance(_Descriptor(undo_hint="h", compensating_action="c",
                                             policy_ref="p"))
        assert gov == {"undo_hint": "h", "compensating_action": "c", "policy_ref": "p"}


# ════════════════════════════════════════════════════════════
#  5. 三态状态机
# ════════════════════════════════════════════════════════════


class TestSagaThreePhase:
    """prepare → execute → confirm"""

    def test_three_phase_writes_journal_with_correct_hashes(self, tmp_path):
        """三态各一条；prepare 填 intent+before，execute 填 before+after，confirm 填 after"""
        saga = _saga(tmp_path)
        result = _prepare_execute_confirm(saga)
        entries = saga.journal.entries(saga.saga_id)
        assert [e.step for e in entries] == [STEP_PREPARE, STEP_EXECUTE, STEP_CONFIRM]
        prepare, execute, confirm = entries
        assert prepare.intent_hash == hash_intent({"op": "backfill"})
        assert prepare.before_hash == hash_state({"cfg": "before"})
        assert execute.before_hash == prepare.before_hash
        assert execute.after_hash == hash_state(result)
        assert confirm.after_hash == execute.after_hash
        assert confirm.before_hash == ""
        assert saga.state is SagaState.CONFIRMED
        assert saga.state_from_journal() is SagaState.CONFIRMED
        assert saga.journal.is_three_phase_complete(saga.saga_id) is True

    def test_every_entry_carries_all_seven_fields(self, tmp_path):
        """三态条目逐个通过七元形状断言（验收项「journal 字段齐」）"""
        saga = _saga(tmp_path)
        _prepare_execute_confirm(saga)
        for entry in saga.journal.entries(saga.saga_id):
            assert tuple(entry.to_dict().keys()) == JOURNAL_FIELDS
            assert assert_entry_shape(entry) is None

    def test_trace_id_and_saga_id_propagate(self, tmp_path):
        """trace_id 随每条 journal 落账（可追溯）"""
        saga = _saga(tmp_path, trace_id="trace-abc")
        _prepare_execute_confirm(saga)
        assert {e.trace_id for e in saga.journal.entries(saga.saga_id)} == {"trace-abc"}
        assert saga.saga_id.startswith("saga-")

    def test_two_sagas_do_not_mix_in_one_journal(self, tmp_path):
        """同一 journal 文件可承载多个 saga，互不串账"""
        journal = _journal(tmp_path)
        first = Saga(journal=journal, trace_id="t1", incidents_dir=str(tmp_path / "inc"))
        second = Saga(journal=journal, trace_id="t2", incidents_dir=str(tmp_path / "inc"))
        _prepare_execute_confirm(first)
        second.prepare({"op": "other"})
        assert first.journal.steps_of(second.saga_id) == [STEP_PREPARE]
        assert len(first.journal.steps_of(first.saga_id)) == 3
        assert first.journal.sagas() == [first.saga_id, second.saga_id]

    def test_confirm_before_execute_raises(self, tmp_path):
        """未 execute 就 confirm → SagaStateError（状态机不静默）"""
        saga = _saga(tmp_path)
        saga.prepare({"op": "x"})
        with pytest.raises(SagaStateError) as exc:
            saga.confirm()
        assert "非法状态转移" in str(exc.value)
        assert saga.state is SagaState.PREPARED

    def test_confirm_and_execute_before_prepare_raise(self, tmp_path):
        """未 prepare 就 execute / confirm → SagaStateError"""
        saga = _saga(tmp_path)
        with pytest.raises(SagaStateError):
            saga.confirm()
        with pytest.raises(SagaStateError):
            saga.execute(lambda: None)
        assert saga.state is SagaState.INIT
        assert saga.state_from_journal() is SagaState.INIT
        assert saga.journal.entries() == []

    def test_prepare_is_repeatable_but_confirm_needs_execute(self, tmp_path):
        """prepare 幂等可重入；confirm 在 EXECUTED 之后合法"""
        saga = _saga(tmp_path)
        saga.prepare({"op": "x"})
        saga.prepare({"op": "x"})       # 允许（INIT → PREPARED → PREPARED）
        saga.execute(lambda: {"ok": 1})
        saga.confirm()
        assert saga.state is SagaState.CONFIRMED

    def test_execute_failure_writes_entry_and_reraises(self, tmp_path):
        """execute 回调抛错：记一条 after_hash 为空的失败条目后原样上抛"""
        saga = _saga(tmp_path)
        saga.prepare({"op": "boom"}, snapshot={"cfg": "before"})

        def _boom():
            raise RuntimeError("动作失败")

        with pytest.raises(RuntimeError) as exc:
            saga.execute(_boom)
        assert "动作失败" in str(exc.value)
        entry = saga.journal.last(saga.saga_id, STEP_EXECUTE)
        assert entry is not None
        assert entry.after_hash == ""
        assert entry.before_hash == hash_state({"cfg": "before"})
        assert saga.state is SagaState.EXECUTED


# ════════════════════════════════════════════════════════════
#  6. 补偿：逆序 / 幂等 / 失败升级 L4
# ════════════════════════════════════════════════════════════


class TestCompensation:
    """compensate / abort / recover"""

    @staticmethod
    def _two_step_saga(tmp_path):
        """两个可自动补偿的步骤（a 先撤 b 后撤 → 逆序应为 b、a）"""
        calls = []
        steps = [
            SagaStep(name="step_a", action="write_a",
                     compensator=lambda: calls.append("a")),
            SagaStep(name="step_b", action="write_b",
                     compensator=lambda: calls.append("b") or {"ok": True}),
        ]
        return _saga(tmp_path, steps=steps), calls

    def test_compensate_runs_in_reverse_step_order(self, tmp_path):
        """逆序补偿（后做的先撤）——Saga 语义的标准顺序"""
        saga, calls = self._two_step_saga(tmp_path)
        saga.prepare({"op": "x"})
        saga.execute(lambda: {"cfg": "after"})
        saga.abort("执行后失败")
        assert calls == ["b", "a"]
        assert saga.journal.has_step(saga.saga_id, STEP_ABORT) is True
        assert saga.state is SagaState.COMPENSATED
        assert saga.state_from_journal() is SagaState.COMPENSATED

    def test_compensate_is_idempotent_on_replay(self, tmp_path):
        """重复 compensate：已成功步骤不重复执行，第二次结果全进 skipped"""
        saga, calls = self._two_step_saga(tmp_path)
        saga.prepare({"op": "x"})
        saga.execute(lambda: {"cfg": "after"})
        first = saga.compensate()
        assert first.compensated == ["step_b", "step_a"]
        assert first.failed == [] and first.skipped == []
        assert first.ok is True
        assert first.state == SagaState.COMPENSATED.value
        calls_snapshot = list(calls)

        second = saga.compensate()
        assert calls == calls_snapshot                     # 无新补偿动作
        assert second.compensated == []
        assert sorted(second.skipped) == ["step_a", "step_b"]
        assert second.failed == [] and second.escalated is False
        assert saga.state_from_journal() is SagaState.COMPENSATED
        # 每个步骤至多一条**成功**补偿条目（after_hash 非空）
        success = [e for e in saga.journal.entries(saga.saga_id)
                   if step_kind(e.step) == STEP_COMPENSATE and e.after_hash]
        assert len(success) == 2

    def test_abort_marks_journal_then_compensates(self, tmp_path):
        """abort(reason)：先写 abort 条目，再补偿"""
        saga, calls = self._two_step_saga(tmp_path)
        saga.prepare({"op": "x"})
        result = saga.abort("外部依赖失败")
        steps = saga.journal.steps_of(saga.saga_id)
        assert steps[0] == STEP_PREPARE
        assert STEP_ABORT in steps
        assert steps.index(STEP_ABORT) < steps.index(f"{STEP_COMPENSATE}:step_b")
        assert saga.journal.last(saga.saga_id, STEP_ABORT).after_hash != ""
        assert result.compensated == ["step_b", "step_a"]
        assert calls == ["b", "a"]
        assert saga.state is SagaState.COMPENSATED

    def test_step_without_compensator_is_not_treated_as_success(self, tmp_path):
        """compensator=None 的步骤进 failed（不得静默当成功）→ 依默认口径升级 L4"""
        saga = _saga(tmp_path, steps=[SagaStep(name="manual_step", action="manual")])
        saga.prepare({"op": "x"})
        result = saga.compensate()
        assert result.failed == ["manual_step"]
        assert result.compensated == [] and result.skipped == []
        assert result.ok is False
        entry = saga.journal.last(saga.saga_id, f"{STEP_COMPENSATE}:manual_step")
        assert entry is not None and entry.after_hash == ""
        # 失败即升级（默认 escalate_on_failure=True）：内存态与 journal 推导态一致
        assert result.escalated is True
        assert saga.state is SagaState.ESCALATED
        assert saga.state_from_journal() is SagaState.ESCALATED

    def test_compensate_failure_escalates_to_l4(self, tmp_path):
        """【headline】补偿抛错 → L4 事故卡 + escalate 条目 + 失败 compensate 条目"""
        def _boom():
            raise RuntimeError("补偿本身失败")

        incidents = tmp_path / "incidents"
        saga = _saga(tmp_path, steps=[SagaStep(name="write_cfg", action="write_config",
                                               compensating_action="删除配置",
                                               compensator=_boom)])
        saga.prepare({"op": "write_cfg"})
        saga.execute(lambda: {"cfg": "after"})
        result = saga.compensate(reason="执行失败回滚")

        assert result.failed == ["write_cfg"]
        assert result.escalated is True
        assert result.incident_id
        assert result.state == SagaState.ESCALATED.value
        assert result.ok is False
        assert saga.state is SagaState.ESCALATED
        assert saga.state_from_journal() is SagaState.ESCALATED

        card = load_incident(result.incident_id, directory=str(incidents))
        assert card is not None
        assert card.severity is HealLevel.L4
        assert card.trace_ids == ["trace-saga-1"]
        assert card.detail["failed_steps"] == ["write_cfg"]
        assert len(list_incidents(directory=str(incidents), severity="L4")) == 1

        assert saga.journal.has_step(saga.saga_id, STEP_ESCALATE) is True
        failed_entry = saga.journal.last(saga.saga_id, f"{STEP_COMPENSATE}:write_cfg")
        assert failed_entry is not None and failed_entry.after_hash == ""
        # escalate 条目自带事故卡 id（可追溯）
        assert saga.journal.last(saga.saga_id, STEP_ESCALATE).after_hash != ""

    def test_compensation_failure_without_escalation_flag(self, tmp_path):
        """escalate_on_failure=False：状态为 COMPENSATION_FAILED，**不**冒充 escalated

        【实现期修正】第一版把内存态置为 `escalated`，而 journal 里没有 escalate 条目、
        也没有事故卡——三处互相矛盾。现用独立状态 `compensation_failed` 如实表达
        「补偿失败但调用方显式关闭了升级」，且内存态与 journal 推导态一致。
        """
        def _boom():
            raise RuntimeError("补偿失败")

        incidents = tmp_path / "incidents"
        saga = _saga(tmp_path, steps=[SagaStep(name="s1", compensator=_boom)])
        saga.prepare({"op": "x"})
        result = saga.compensate(escalate_on_failure=False)

        assert SagaState.COMPENSATION_FAILED.value == "compensation_failed"
        assert result.failed == ["s1"]
        assert result.state == SagaState.COMPENSATION_FAILED.value
        assert result.state != SagaState.ESCALATED.value
        assert result.escalated is False
        assert result.incident_id == ""
        assert result.ok is False
        # 未升级 ⇒ 没有事故卡、journal 里也没有 escalate 条目
        assert list_incidents(directory=str(incidents)) == []
        assert saga.journal.has_step(saga.saga_id, STEP_ESCALATE) is False
        # 内存态与 journal 推导态一致（这正是修正的核心）
        assert saga.state is SagaState.COMPENSATION_FAILED
        assert saga.state_from_journal() is SagaState.COMPENSATION_FAILED

    def test_compensate_mixed_success_and_failure(self, tmp_path):
        """部分补偿成功 + 部分失败：成功条目照写，失败触发升级，两态一致"""
        def _boom():
            raise RuntimeError("补偿失败")

        saga = _saga(tmp_path, steps=[
            SagaStep(name="ok_step", compensator=lambda: "done"),
            SagaStep(name="bad_step", compensator=_boom),
        ])
        saga.prepare({"op": "x"})
        result = saga.compensate()
        assert result.compensated == ["ok_step"]
        assert result.failed == ["bad_step"]
        assert result.escalated is True
        assert saga.journal.has_step(saga.saga_id, f"{STEP_COMPENSATE}:ok_step") is True
        # 升级条目最后写入 ⇒ 逆序扫描取到 escalate（最新一步说了算）
        assert saga.journal.last(saga.saga_id).step == STEP_ESCALATE
        assert saga.state is SagaState.ESCALATED
        assert saga.state_from_journal() is SagaState.ESCALATED

    def test_confirmed_then_compensated_state_from_journal(self, tmp_path):
        """【实现期修正】先 confirm 后补偿 → journal 重建态为 COMPENSATED

        修正前用固定优先级（escalate > confirm > compensate > …）反推，会把
        "confirm 之后又被补偿"的 saga 报成 `confirmed`，与内存态、与补偿幂等键
        （`_compensated_steps`）都不一致。现在改为**逆序扫描、最新一步说了算**：
        后发生的事覆盖先发生的，内存态与磁盘态在任意时点可对齐。
        """
        saga = _saga(tmp_path, steps=[SagaStep(name="s1", compensator=lambda: "ok")])
        _prepare_execute_confirm(saga)
        # 补偿之前：confirm 是最后一步 ⇒ confirmed（中间态断言保留）
        assert saga.state is SagaState.CONFIRMED
        assert saga.state_from_journal() is SagaState.CONFIRMED

        result = saga.compensate()
        assert result.compensated == ["s1"]
        assert saga.journal.has_step(saga.saga_id, f"{STEP_COMPENSATE}:s1") is True
        # 补偿之后：compensate 条目是最后一步 ⇒ compensated（覆盖 confirm）
        assert saga.journal.last(saga.saga_id).step == f"{STEP_COMPENSATE}:s1"
        assert saga.state is SagaState.COMPENSATED
        assert saga.state_from_journal() is SagaState.COMPENSATED

    def test_state_from_journal_latest_step_wins(self, tmp_path):
        """逆序扫描口径：abort 之后的成功补偿 ⇒ compensated；失败补偿 ⇒ compensation_failed"""
        saga = _saga(tmp_path, steps=[SagaStep(name="s1", compensator=lambda: "ok")])
        saga.prepare({"op": "x"})
        saga.execute(lambda: {"cfg": "after"})
        assert saga.state_from_journal() is SagaState.EXECUTED
        saga.abort("失败")
        assert saga.state_from_journal() is SagaState.COMPENSATED

        failed = _saga(tmp_path, steps=[SagaStep(name="s1", compensator=None)],
                       journal=_journal(tmp_path, "journal2.log"))
        failed.prepare({"op": "x"})
        failed.compensate(escalate_on_failure=False)
        assert failed.journal.last(failed.saga_id).step == f"{STEP_COMPENSATE}:s1"
        assert failed.state_from_journal() is SagaState.COMPENSATION_FAILED

    def test_recover_reports_documented_structure(self, tmp_path):
        """recover() 返回 {saga_id,state,compensation,skipped_compensation,incomplete,consistency,ok}"""
        saga = _saga(tmp_path, steps=[SagaStep(name="s1", compensator=lambda: "ok")])
        _prepare_execute_confirm(saga)
        report = saga.recover()
        assert set(report) == {"saga_id", "state", "compensation", "skipped_compensation",
                               "incomplete", "consistency", "ok"}
        assert report["saga_id"] == saga.saga_id
        assert report["compensation"] is None          # 已 confirm ⇒ 拒绝补偿
        assert isinstance(report["skipped_compensation"], str)
        assert report["skipped_compensation"]           # 非空 = 给出跳过理由
        assert report["incomplete"] == []
        assert report["consistency"]["three_phase_complete"] is True
        assert report["consistency"]["state"] == SagaState.CONFIRMED.value
        assert report["consistency"]["memory_state"] == SagaState.CONFIRMED.value
        assert report["consistency"]["memory_matches_journal"] is True
        assert report["ok"] is True

    def test_recover_does_not_undo_committed_transaction(self, tmp_path):
        """已 confirm 的事务是终态：recover 不得重放补偿（否则把已成功的操作撤掉）"""
        calls = []
        saga = _saga(tmp_path, steps=[SagaStep(name="s1",
                                               compensator=lambda: calls.append("s1"))])
        _prepare_execute_confirm(saga)
        report = saga.recover()
        assert report["compensation"] is None            # 一次补偿都没跑
        assert calls == []                               # 补偿回调零调用
        assert report["skipped_compensation"] != ""
        assert "confirmed" in report["skipped_compensation"]
        assert saga.state_from_journal() is SagaState.CONFIRMED
        assert saga.state is SagaState.CONFIRMED
        assert report["consistency"]["memory_matches_journal"] is True
        assert report["ok"] is True

    def test_recover_compensates_unconfirmed_transaction(self, tmp_path):
        """未 confirm 的事务（prepared+executed）：recover 真的执行补偿，两态一致"""
        calls = []
        saga = _saga(tmp_path, steps=[SagaStep(name="s1",
                                               compensator=lambda: calls.append("s1"))])
        saga.prepare({"op": "x"})
        saga.execute(lambda: {"cfg": "after"})           # 故意不 confirm
        assert saga.state_from_journal() is SagaState.EXECUTED

        report = saga.recover()
        assert calls == ["s1"]                           # 补偿确实跑了
        assert report["compensation"] is not None
        assert report["compensation"]["compensated"] == ["s1"]
        assert report["compensation"]["failed"] == []
        assert report["skipped_compensation"] == ""      # 未跳过 ⇒ 空串
        assert saga.state_from_journal() is SagaState.COMPENSATED
        assert saga.state is SagaState.COMPENSATED
        assert report["consistency"]["state"] == SagaState.COMPENSATED.value
        assert report["consistency"]["memory_state"] == SagaState.COMPENSATED.value
        assert report["consistency"]["memory_matches_journal"] is True
        assert report["consistency"]["three_phase_complete"] is False
        assert report["ok"] is True

    def test_recover_skips_escalated_and_compensation_failed_sagas(self, tmp_path):
        """已升级 / 补偿失败（未升级）也是终态：recover 不重复补偿、不重复开事故卡"""
        def _boom():
            raise RuntimeError("补偿失败")

        incidents = tmp_path / "incidents"
        escalated = _saga(tmp_path, steps=[SagaStep(name="s1", compensator=_boom)])
        escalated.prepare({"op": "x"})
        first = escalated.compensate()
        assert first.escalated is True
        cards_before = len(list_incidents(directory=str(incidents)))
        report = escalated.recover()
        assert report["compensation"] is None
        assert report["skipped_compensation"] != ""
        assert escalated.state_from_journal() is SagaState.ESCALATED
        assert len(list_incidents(directory=str(incidents))) == cards_before  # 不重复开卡

        failed = _saga(tmp_path, steps=[SagaStep(name="s1", compensator=_boom)],
                       journal=_journal(tmp_path, "journal2.log"))
        failed.prepare({"op": "x"})
        failed.compensate(escalate_on_failure=False)
        report2 = failed.recover()
        assert report2["compensation"] is None
        assert failed.state_from_journal() is SagaState.COMPENSATION_FAILED
        assert report2["consistency"]["memory_matches_journal"] is True

    def test_recover_with_no_steps_reports_none_compensation(self, tmp_path):
        """无步骤的 Saga：recover 不做补偿（compensation=None），一致性如实反映 journal"""
        saga = _saga(tmp_path, steps=[])
        saga.prepare({"op": "x"})
        report = saga.recover()
        assert report["compensation"] is None
        assert report["skipped_compensation"] == ""    # 非终态 ⇒ 不写跳过理由
        assert report["consistency"]["three_phase_complete"] is False
        assert report["consistency"]["state"] == SagaState.PREPARED.value
        assert report["consistency"]["memory_state"] == SagaState.PREPARED.value
        assert report["consistency"]["memory_matches_journal"] is True
        assert report["ok"] is False                   # 未到终态
        assert report["incomplete"] == [saga.saga_id]   # 未完成事务如实列出

    def test_compensation_result_to_dict(self, tmp_path):
        """CompensationResult.to_dict 含 ok 派生字段（供审计/面板直接消费）"""
        result = CompensationResult(saga_id="s-1", compensated=["a"], skipped=["b"])
        payload = result.to_dict()
        assert payload["ok"] is True
        assert payload["saga_id"] == "s-1"
        assert payload["compensated"] == ["a"] and payload["skipped"] == ["b"]
        assert CompensationResult(saga_id="s-1", failed=["x"]).ok is False
        assert CompensationResult(saga_id="s-1", escalated=True).ok is False


# ════════════════════════════════════════════════════════════
#  7. make_saga_for 与模块导出
# ════════════════════════════════════════════════════════════


class TestMakeSagaFor:
    """按 descriptor 建 Saga（带风险闸门）"""

    def test_high_risk_without_hint_raises(self, tmp_path):
        """高风险 descriptor 缺 undo_hint → UndoHintError（不产出 Saga）"""
        with pytest.raises(UndoHintError):
            make_saga_for({"capability_id": "skill.backfill", "risk_level": "high"},
                          journal=_journal(tmp_path))
        with pytest.raises(UndoHintError):
            make_saga_for(_Descriptor(capability_id="skill.backfill", risk_level="high"),
                          journal=_journal(tmp_path))

    def test_high_risk_with_good_hint_returns_saga(self, tmp_path):
        """真实 hint → 返回 Saga，且步骤自带 compensating_action 描述"""
        saga = make_saga_for(
            {"capability_id": "skill.backfill", "risk_level": "high",
             "governance": {"undo_hint": REAL_UNDO_HINT,
                            "compensating_action": "rollback_version"}},
            journal=_journal(tmp_path), trace_id="t-1",
            incidents_dir=str(tmp_path / "incidents"))
        assert isinstance(saga, Saga)
        assert saga.state is SagaState.INIT
        assert len(saga.steps) == 1
        step = saga.steps[0]
        assert step.name == "skill.backfill"
        assert step.compensating_action == "rollback_version"
        assert step.compensator is None          # 无可执行补偿 → 补偿时进 failed
        assert saga.journal.path == tmp_path / "journal.log"
        assert saga.state_from_journal() is SagaState.INIT

    def test_low_risk_descriptor_without_hint_is_allowed(self, tmp_path):
        """低风险不强制 Saga：无 hint 也返回 Saga（闸门不越权）"""
        saga = make_saga_for({"capability_id": "noop.op", "risk_level": "low"},
                             journal=_journal(tmp_path))
        assert isinstance(saga, Saga)
        assert saga.steps[0].name == "noop.op"

    def test_object_descriptor_path(self, tmp_path):
        """对象形态 descriptor 走通同一闸门"""
        saga = make_saga_for(
            _Descriptor(capability_id="skill.disable", risk_level="destructive",
                        undo_hint="技能版本可经 rollback_version 回退",
                        compensating_action="rollback_version"),
            journal=_journal(tmp_path))
        assert saga.steps[0].compensating_action == "rollback_version"

    def test_module_exports_are_complete(self):
        """`__all__` 与模块属性一致"""
        for name in saga_mod.__all__:
            assert hasattr(saga_mod, name), name
        assert saga_mod.SagaRequiredError is SagaRequiredError
