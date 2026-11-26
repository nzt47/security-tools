"""E2-2 Team 任务板：认领 / 释放 / 交接（乐观锁 + append-only）

锁死：
  1. claim 写一行 update（复制上一态 + owner/租约/version+1），**不改 status**；
  2. 他人持未过期租约 ⇒ BoardConflict（409 的来源）；租约过期 ⇒ 可接手；
  3. release 清空 owner；handoff 改 owner 且校验持有者；未知任务 ⇒ BoardNotFound；
  4. board() 折叠后按状态分组计数；
  5. 文件仍是 append-only：动作只增行，不改旧行。
"""
from __future__ import annotations

import json

import pytest

from agent.subagent.task_board import (
    BoardConflict,
    BoardError,
    BoardNotFound,
    TaskBoard,
)


@pytest.fixture()
def board(tmp_path):
    b = TaskBoard(path=str(tmp_path / "board.jsonl"))
    b.record({"task_id": "t-1", "status": "pending", "goal": "把设计稿拆成步骤",
              "subagent": "alpha", "source": "tool"})
    b.record({"task_id": "t-2", "status": "done", "goal": "另一件事",
              "subagent": "beta", "source": "fan_out"})
    return b


def _lines(board: TaskBoard) -> list[dict]:
    rows = [ln for ln in open(board.path, encoding="utf-8").read().splitlines() if ln.strip()]
    return [json.loads(x) for x in rows]


def test_claim_sets_owner_and_bumps_version(board):
    before = _lines(board)
    rec = board.claim("t-1", "alice", note="我来")
    assert rec["owner"] == "alice" and rec["version"] == 1
    assert rec["board_action"] == "claim" and rec["note"] == "我来"
    assert rec["status"] == "pending", "认领不得改委派状态"
    assert rec["goal"] == "把设计稿拆成步骤", "复制上一态字段，折叠读才不丢"
    after = _lines(board)
    assert len(after) == len(before) + 1
    assert after[:len(before)] == before, "旧行不允许被改写（append-only）"
    assert board.latest("t-1")["owner"] == "alice"


def test_second_claim_by_other_conflicts(board):
    board.claim("t-1", "alice")
    with pytest.raises(BoardConflict) as ei:
        board.claim("t-1", "bob")
    assert "alice" in str(ei.value) and ei.value.code == "task_claimed"
    # 同一个人重复认领 = 刷新租约，不冲突
    rec = board.claim("t-1", "alice")
    assert rec["owner"] == "alice" and rec["version"] == 2


def test_expired_lease_can_be_taken_over(board):
    board.record({"task_id": "t-1", "status": "pending", "owner": "alice",
                  "lease_until": "2000-01-01T00:00:00+00:00", "version": 1})
    rec = board.claim("t-1", "bob")
    assert rec["owner"] == "bob"


def test_release_clears_owner_and_checks_holder(board):
    board.claim("t-1", "alice")
    with pytest.raises(BoardConflict):
        board.release("t-1", owner="bob")
    rec = board.release("t-1", owner="alice")
    assert rec["owner"] == "" and rec["lease_until"] == ""
    # 已释放 ⇒ 任何人都能重新认领
    assert board.claim("t-1", "bob")["owner"] == "bob"


def test_handoff_transfers_ownership(board):
    board.claim("t-1", "alice")
    rec = board.handoff("t-1", "bob", owner="alice", note="换人")
    assert rec["owner"] == "bob" and rec["board_action"] == "handoff"
    assert rec["note"] == "换人"
    with pytest.raises(BoardError) as ei:
        board.handoff("t-1", "  ", owner="bob")
    assert ei.value.code == "invalid_handoff"


def test_unknown_task_and_blank_ids(board):
    with pytest.raises(BoardNotFound):
        board.claim("t-nope", "alice")
    with pytest.raises(BoardError) as ei:
        board.claim("  ", "alice")
    assert ei.value.code == "invalid_task_id"
    with pytest.raises(BoardError) as ei2:
        board.claim("t-1", " ")
    assert ei2.value.code == "invalid_owner"


def test_force_steals_with_audit_trail(board):
    board.claim("t-1", "alice")
    rec = board.claim("t-1", "ops", force=True, note="运维接管")
    assert rec["owner"] == "ops"
    rows = _lines(board)
    assert any(r.get("board_action") == "claim" and r.get("owner") == "alice" for r in rows), \
        "抢占前那一行必须留在文件里（谁先认领的可审计）"


def test_board_groups_and_filters(board):
    board.claim("t-1", "alice")
    data = board.board()
    assert data["count"] == 2
    assert data["counts"]["pending"] == 1 and data["counts"]["done"] == 1
    assert data["records"][0]["task_id"] == "t-1", "最近更新的任务在前"
    only_done = board.board(status="done")
    assert [r["task_id"] for r in only_done["records"]] == ["t-2"]
    assert only_done["counts"]["done"] == 1 and only_done["counts"]["pending"] == 0


def test_latest_on_missing_file(tmp_path):
    b = TaskBoard(path=str(tmp_path / "empty.jsonl"))
    assert b.latest("t-1") is None
    with pytest.raises(BoardNotFound):
        b.release("t-1")
