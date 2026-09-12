"""TASK-S8-05 步骤 1（D1）队列完整性校验单测

验收对应（任务书 §四）：

- 【D1】入队时 case 不存在 → **拒绝并记事件**；已入队但 case 消失 → 标 ``stale``
  且**不删除记录**；
- 【D1】判定集**重生成**后，受影响队列项自动转 ``stale``（含事件/审计）；
- 失效项不阻塞队列闭合（它没有被裁的证据），但**必须可见**（复核表/摘要都有它）。

隔离纪律（S3-02/S3-03 两次污染的教训）：判定集与队列**一律显式传 tmp 路径**，
并使用**注入时钟**，避免跨日与运行时区污染。
"""

from __future__ import annotations

import json

import pytest

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain
from agent.digestion import cases as C
from agent.digestion import shadow as SH

CAP = "cp.builtin.read_file"
#: 注入时钟（固定值 ⇒ 台账时间戳确定、可复算）
CLOCK = 1_800_000_000.0


# ════════════════════════════════════════════════════════════
#  隔离 fixtures
# ════════════════════════════════════════════════════════════


@pytest.fixture
def chain(tmp_path):
    c = AuditChain(str(tmp_path / "audit.db"),
                   roots_path=str(tmp_path / "roots.jsonl"),
                   signing_key_path=str(tmp_path / "k.pem"), auto_seal=False)
    yield c
    c.close(timeout=2.0)


@pytest.fixture(autouse=True)
def isolated_audit(chain):
    previous = facade_mod.audit.bind(chain)
    facade_mod.audit.enabled = True
    facade_mod.audit.reset_counters()
    yield facade_mod.audit
    facade_mod.audit.bind(previous)


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """默认落点一律隔离（**不显式传路径的代码也不得写到运行时区**）"""
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setenv(C.CASE_ROOT_ENV, str(tmp_path / "cases"))
    monkeypatch.setenv(SH.SHADOW_DIR_ENV, str(tmp_path / "shadow"))
    import agent.observability.events as events_mod
    events_mod.reset_event_stores()
    yield tmp_path
    events_mod.reset_event_stores()


def _case(index: int, *, capability_id: str = CAP) -> C.EquivalenceCase:
    """单能力用例（形状与被评能力匹配 ⇒ 可入队）"""
    return C.EquivalenceCase(
        case_id=f"case-{index:03d}", capability_id=capability_id,
        upstream=[C.ProgramStep(label="read_file", capability_id=capability_id,
                                params={"path": f"C:/sandbox/{index}.txt"})],
        expected_output_schema={"text": "str"}, expected_status="success")


def _chain_case(index: int) -> C.EquivalenceCase:
    """多能力链用例（形状不匹配 ⇒ 应被形状闸挡下）"""
    return C.EquivalenceCase(
        case_id=f"chain-{index:03d}", capability_id=CAP,
        upstream=[
            C.ProgramStep(label="read_file", capability_id=CAP),
            C.ProgramStep(label="shell_execute",
                          capability_id="cp.builtin.shell_execute"),
            C.ProgramStep(label="write_file",
                          capability_id="cp.builtin.write_file"),
        ], expected_status="success")


@pytest.fixture
def store(tmp_path):
    """判定集存储（**显式路径**；tmp 隔离）"""
    return C.open_case_store(str(tmp_path / "cases"))


@pytest.fixture
def queue(tmp_path, store):
    return SH.ManualReviewQueue(str(tmp_path / "shadow" / "reviews.jsonl"),
                                case_store=store, clock=lambda: CLOCK)


@pytest.fixture
def v1(store):
    """判定集 v1：2 条单能力 + 1 条多能力链（落盘）"""
    case_set = C.build_case_set(CAP, [_case(0), _case(1), _chain_case(0)],
                                version=1)
    store.save(case_set, record_cost=False)
    return case_set


# ════════════════════════════════════════════════════════════
#  1. 入队存活校验（拒绝 + 留痕）
# ════════════════════════════════════════════════════════════


class TestEnqueueLiveness:
    def test_existing_cases_are_enqueued(self, queue, v1):
        expect = {}
        items = queue.enqueue(CAP, ["case-000", "case-001"], case_set=v1,
                              cases=v1.active_cases(), now=CLOCK, expect=expect)
        assert [i.case_id for i in items] == ["case-000", "case-001"]
        assert expect["rejected"] == []
        # 入队时记录判定集版本 ⇒ 后来版本变了即可机器判定"这是旧引用"
        assert all(i.case_version_at_enqueue == 1 for i in items)

    def test_missing_case_is_rejected_not_silently_enqueued(self, queue, v1):
        expect = {}
        items = queue.enqueue(CAP, ["case_ghost"], case_set=v1, now=CLOCK,
                              expect=expect)
        assert items == []
        assert [r["reason"] for r in expect["rejected"]] == \
            [SH.REJECT_CASE_MISSING]
        # 拒绝必须**留痕**：队列台账里不得出现该 case
        assert queue.items(CAP) == []

    def test_rejection_writes_audit_and_does_not_write_queue(self, queue, v1,
                                                             tmp_path):
        queue.enqueue(CAP, ["case_ghost"], case_set=v1, now=CLOCK)
        actions = [e.action for e in facade_mod.audit.recent(limit=20)]
        assert SH.AUDIT_ACTION_ENQUEUE_REJECTED in actions
        path = tmp_path / "shadow" / "reviews.jsonl"
        assert not path.exists(), "被拒绝的入队不得落队列台账"

    def test_missing_case_set_rejects_everything(self, queue):
        """判定集整个不存在 ⇒ 拒绝（无判定依据，不得入队）"""
        expect = {}
        queue.enqueue(CAP, ["case-000"], now=CLOCK, expect=expect)
        assert [r["reason"] for r in expect["rejected"]] == \
            [SH.REJECT_CASE_MISSING]

    def test_deactivated_case_counts_as_missing(self, queue, store, v1):
        """`invalidate`（漂移失效）后该用例不再可复核 ⇒ 视同不存在"""
        case_set = store.load(CAP)
        case_set.invalidate("上游契约漂移", now=CLOCK)
        store.save(case_set, record_cost=False)
        expect = {}
        queue.enqueue(CAP, ["case-000"], case_set=store.load(CAP), now=CLOCK,
                      expect=expect)
        assert [r["reason"] for r in expect["rejected"]] == \
            [SH.REJECT_CASE_MISSING]

    def test_shape_mismatch_is_rejected(self, queue, v1):
        """D2 的入队闸：多能力链不得进入单能力抽检（"别问错误的问题"）"""
        expect = {}
        queue.enqueue(CAP, ["chain-000"], case_set=v1,
                      cases=v1.active_cases(), now=CLOCK, expect=expect)
        assert [r["reason"] for r in expect["rejected"]] == \
            [SH.REJECT_SHAPE_MISMATCH]

    def test_idempotent_for_pending(self, queue, v1):
        queue.enqueue(CAP, ["case-000"], case_set=v1, now=CLOCK)
        queue.enqueue(CAP, ["case-000"], case_set=v1, now=CLOCK)
        assert len(queue.items(CAP)) == 1


# ════════════════════════════════════════════════════════════
#  2. stale 标注（**保留不删**）
# ════════════════════════════════════════════════════════════


class TestStaleMarking:
    def _enqueued_then_regenerated(self, queue, store, v1):
        queue.enqueue(CAP, ["case-000", "case-001"], case_set=v1, now=CLOCK)
        # 重生成 v2：**不含**旧用例（模拟 S7-05 用真实轨迹重建判定集）
        v2 = C.build_case_set(CAP, [_case(100), _case(101)], version=2)
        store.save(v2, record_cost=False)
        return v2

    def test_regeneration_marks_stale_and_keeps_records(self, queue, store, v1):
        v2 = self._enqueued_then_regenerated(queue, store, v1)
        before = SH.queue_liveness_report(queue, CAP, case_set=v2)
        assert before["stale"] == 2

        result = queue.mark_stale(CAP, case_set=v2, now=CLOCK + 60)
        assert result["marked"] == 2
        after = SH.queue_liveness_report(queue, CAP, case_set=v2)
        assert after["stale"] == 2
        # **记录保留**（留痕优先）：样本条数不变
        assert after["sampled"] == 2
        assert after["live"] == 0

    def test_stale_reason_names_regeneration(self, queue, store, v1):
        v2 = self._enqueued_then_regenerated(queue, store, v1)
        queue.mark_stale(CAP, case_set=v2, now=CLOCK + 60)
        reasons = {i.case_id: i.stale_reason for i in queue.stale_items(CAP)}
        assert reasons == {
            "case-000": SH.STALE_CASE_REMOVED_AFTER_REGENERATION,
            "case-001": SH.STALE_CASE_REMOVED_AFTER_REGENERATION}

    def test_stale_items_do_not_block_closure(self, queue, store, v1):
        v2 = self._enqueued_then_regenerated(queue, store, v1)
        queue.mark_stale(CAP, case_set=v2, now=CLOCK + 60)
        summary = queue.summary(CAP)
        assert summary["pending"] == 0
        assert summary["closed"] is True
        # 但**必须可见**（不许静默）
        assert summary["stale"] == 2
        assert summary["by_stale_reason"] == \
            {SH.STALE_CASE_REMOVED_AFTER_REGENERATION: 2}
        assert "失效" in summary["stale_note"]

    def test_mark_stale_is_idempotent(self, queue, store, v1):
        v2 = self._enqueued_then_regenerated(queue, store, v1)
        first = queue.mark_stale(CAP, case_set=v2, now=CLOCK + 60)
        second = queue.mark_stale(CAP, case_set=v2, now=CLOCK + 120)
        assert first["marked"] == 2 and second["marked"] == 0
        assert second["already"] == 2
        assert len(queue.rows()) == 2 + 2          # 入队 2 + 失效 2（未重复追加）

    def test_mark_stale_writes_audit(self, queue, store, v1):
        v2 = self._enqueued_then_regenerated(queue, store, v1)
        queue.mark_stale(CAP, case_set=v2, now=CLOCK + 60)
        actions = [e.action for e in facade_mod.audit.recent(limit=30)]
        assert SH.AUDIT_ACTION_STALE in actions

    def test_scan_after_regeneration_alias(self, queue, store, v1):
        """对外入口（薄封装）与 `mark_stale` 同语义"""
        v2 = self._enqueued_then_regenerated(queue, store, v1)
        result = SH.mark_stale_after_regeneration(queue, CAP, case_set=v2,
                                                  now=CLOCK + 60)
        assert result["marked"] == 2

    def test_healthy_queue_has_no_stale(self, queue, v1):
        queue.enqueue(CAP, ["case-000"], case_set=v1, now=CLOCK)
        assert queue.stale_reasons(CAP, case_set=v1) == {}
        assert queue.mark_stale(CAP, case_set=v1, now=CLOCK)["marked"] == 0

    def test_missing_case_set_marks_all_stale_with_plain_reason(self, queue,
                                                                store, v1):
        """判定集**整个被删除**（非重生成）⇒ 原因如实记为 `case_missing`"""
        queue.enqueue(CAP, ["case-000"], case_set=v1, now=CLOCK)
        assert store.delete(CAP) is True
        reasons = queue.stale_reasons(CAP)
        assert reasons == {"case-000": SH.STALE_CASE_MISSING}
        assert queue.mark_stale(CAP, now=CLOCK + 60)["marked"] == 1


# ════════════════════════════════════════════════════════════
#  3. 复核表与读取路径的可见性
# ════════════════════════════════════════════════════════════


class TestReviewSheetVisibility:
    def test_sheet_discloses_stale_with_reason(self, queue, store, v1):
        queue.enqueue(CAP, ["case-000"], case_set=v1, now=CLOCK)
        v2 = C.build_case_set(CAP, [_case(100)], version=2)
        store.save(v2, record_cost=False)
        queue.mark_stale(CAP, case_set=v2, now=CLOCK + 60)
        sheet = queue.review_sheet(CAP)
        assert "已失效" in sheet
        assert SH.STALE_CASE_REMOVED_AFTER_REGENERATION in sheet
        assert "保留留痕" in sheet

    def test_sheet_marks_pending_rows(self, queue, v1):
        queue.enqueue(CAP, ["case-000"], case_set=v1, now=CLOCK)
        sheet = queue.review_sheet(CAP)
        assert "case-000" in sheet and "待裁定" in sheet

    def test_legacy_rows_without_stale_fields_still_load(self, tmp_path):
        """向后兼容：S8-05 之前写入的行（无 stale 字段）必须仍可读"""
        path = tmp_path / "legacy.jsonl"
        path.write_text(json.dumps({
            "kind": "queued", "case_id": "case-000",
            "capability_id": CAP, "queued_at": CLOCK}) + "\n", encoding="utf-8")
        queue = SH.ManualReviewQueue(str(path))
        items = queue.items(CAP)
        assert len(items) == 1
        assert items[0].stale is False and items[0].stale_reason == ""
        assert items[0].case_version_at_enqueue == 0

    def test_clock_is_injected(self, tmp_path, store):
        queue = SH.ManualReviewQueue(str(tmp_path / "r.jsonl"),
                                     case_store=store, clock=lambda: CLOCK)
        case_set = C.build_case_set(CAP, [_case(0)], version=1)
        store.save(case_set, record_cost=False)
        item = queue.enqueue(CAP, ["case-000"], case_set=case_set)[0]
        assert item.queued_at == CLOCK


# ════════════════════════════════════════════════════════════
#  4. 与 D4 的衔接（已裁定项不重复入队）
# ════════════════════════════════════════════════════════════


class TestEnqueueSkipsResolved:
    def test_resolved_case_is_not_requeued(self, queue, v1, tmp_path):
        from agent.digestion import resolutions as R
        store = R.ResolutionStore(path=str(tmp_path / "res.jsonl"), audit=False)
        store.record(R.ResolutionRecord(
            scope=R.SCOPE_CASE, rule="M5", case_id="case-000",
            reason="Owner 已裁定该用例行为等价", decided_by="Owner"))
        expect = {}
        items = queue.enqueue(CAP, ["case-000", "case-001"], case_set=v1,
                              resolutions=store, now=CLOCK, expect=expect)
        assert [i.case_id for i in items] == ["case-001"]
        assert [r["reason"] for r in expect["rejected"]] == \
            [SH.REJECT_ALREADY_RESOLVED]

    def test_unresolved_case_is_still_enqueued(self, queue, v1, tmp_path):
        from agent.digestion import resolutions as R
        store = R.ResolutionStore(path=str(tmp_path / "res.jsonl"), audit=False)
        store.record(R.ResolutionRecord(
            scope=R.SCOPE_CASE, rule="M5", case_id="case-000",
            reason="其他用例的裁定", decided_by="Owner"))
        items = queue.enqueue(CAP, ["case-001"], case_set=v1,
                              resolutions=store, now=CLOCK)
        assert [i.case_id for i in items] == ["case-001"]
