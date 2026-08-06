"""任务0 · 知识卡片生命周期状态机回归测试

覆盖（评估标准）：16 组合迁移矩阵全分支、can_transition/validate_transition、
Archive 终态、非枚举入参防御、与 schema.VALID_STATUS 单一事实源一致性。
关键裁决（不易）：拒绝 Draft→Archive 直跳（任务文档样例表与评估标准冲突时以评估标准为准）。
本文件曾因外部回滚丢失，重建自 agent/knowledge/lifecycle.py。
"""

from __future__ import annotations

import pytest

from agent.knowledge.lifecycle import (
    TRANSITIONS,
    CardStatus,
    can_transition,
    validate_transition,
)
from agent.knowledge.schema import VALID_STATUS

# 期望迁移矩阵（16 组合穷举）：(current, target) → 是否合法
_EXPECTED_MATRIX = {
    (CardStatus.DRAFT, CardStatus.CURRENT): True,
    (CardStatus.DRAFT, CardStatus.ARCHIVE): False,   # 评估标准：拒绝直跳
    (CardStatus.DRAFT, CardStatus.UNKNOWN): True,
    (CardStatus.DRAFT, CardStatus.DRAFT): False,     # 自身迁移非法
    (CardStatus.CURRENT, CardStatus.ARCHIVE): True,
    (CardStatus.CURRENT, CardStatus.DRAFT): True,
    (CardStatus.CURRENT, CardStatus.UNKNOWN): False,
    (CardStatus.CURRENT, CardStatus.CURRENT): False,
    (CardStatus.ARCHIVE, CardStatus.DRAFT): False,
    (CardStatus.ARCHIVE, CardStatus.CURRENT): False,
    (CardStatus.ARCHIVE, CardStatus.UNKNOWN): False,
    (CardStatus.ARCHIVE, CardStatus.ARCHIVE): False,
    (CardStatus.UNKNOWN, CardStatus.DRAFT): True,
    (CardStatus.UNKNOWN, CardStatus.CURRENT): True,
    (CardStatus.UNKNOWN, CardStatus.ARCHIVE): False,
    (CardStatus.UNKNOWN, CardStatus.UNKNOWN): False,
}

_ALL_STATUSES = list(CardStatus)


def test_all_16_transition_combinations_expected():
    """迁移矩阵穷举断言（can_transition 与 TRANSITIONS 表逐项对齐）。"""
    for current in _ALL_STATUSES:
        for target in _ALL_STATUSES:
            expected = _EXPECTED_MATRIX[(current, target)]
            assert can_transition(current, target) is expected, (
                f"{current.value} → {target.value} 期望 {expected}"
            )


def test_transitions_table_matches_matrix():
    """TRANSITIONS 表本身与期望矩阵一致（防表被意外修改）。"""
    for current in _ALL_STATUSES:
        expected_set = {
            target
            for target in _ALL_STATUSES
            if _EXPECTED_MATRIX[(current, target)]
        }
        assert TRANSITIONS[current] == expected_set, f"{current}"


# ---------- 关键迁移路径（语义） ----------


def test_draft_to_current_is_main_chain():
    assert can_transition(CardStatus.DRAFT, CardStatus.CURRENT)


def test_current_to_archive_is_main_chain():
    assert can_transition(CardStatus.CURRENT, CardStatus.ARCHIVE)


def test_draft_to_archive_rejected():
    """评估标准：拒绝 Draft→Archive 直跳（须先经 Current）。"""
    assert not can_transition(CardStatus.DRAFT, CardStatus.ARCHIVE)


def test_unknown_only_enters_draft_or_current():
    """评估标准：Unknown 只能进入 Draft 或 Current。"""
    assert can_transition(CardStatus.UNKNOWN, CardStatus.DRAFT)
    assert can_transition(CardStatus.UNKNOWN, CardStatus.CURRENT)
    assert not can_transition(CardStatus.UNKNOWN, CardStatus.ARCHIVE)


def test_archive_is_terminal():
    """Archive 不可回迁（唯一例外：人工强制，非状态机语义）。"""
    for target in _ALL_STATUSES:
        assert not can_transition(CardStatus.ARCHIVE, target), f"archive → {target}"


def test_self_transitions_all_illegal():
    for status in _ALL_STATUSES:
        assert not can_transition(status, status), f"{status} → 自身"


def test_unknown_to_archive_rejected():
    assert validate_transition(CardStatus.UNKNOWN, CardStatus.ARCHIVE) is not None


# ---------- can_transition ----------


def test_can_transition_returns_bool():
    assert can_transition(CardStatus.DRAFT, CardStatus.CURRENT) is True
    assert can_transition(CardStatus.DRAFT, CardStatus.ARCHIVE) is False


def test_can_transition_unknown_current_returns_empty():
    """未注册的当前状态 → 空集（不抛异常）。"""
    assert can_transition(None, CardStatus.DRAFT) is False


# ---------- validate_transition ----------


def test_validate_transition_none_on_legal():
    assert validate_transition(CardStatus.DRAFT, CardStatus.CURRENT) is None
    assert validate_transition(CardStatus.UNKNOWN, CardStatus.CURRENT) is None


def test_validate_transition_reason_on_illegal():
    reason = validate_transition(CardStatus.DRAFT, CardStatus.ARCHIVE)
    assert reason is not None
    assert "draft" in reason and "archive" in reason


def test_validate_transition_non_enum_current():
    reason = validate_transition("draft", CardStatus.CURRENT)
    assert reason is not None
    assert "非法当前状态" in reason


def test_validate_transition_non_enum_target():
    reason = validate_transition(CardStatus.DRAFT, "current")
    assert reason is not None
    assert "非法目标状态" in reason


def test_validate_transition_never_raises():
    """所有输入组合都不抛异常。"""
    for current in _ALL_STATUSES + [None, "draft", 42]:
        for target in _ALL_STATUSES + [None, "current", object()]:
            result = validate_transition(current, target)
            assert result is None or isinstance(result, str)


# ---------- 状态枚举 ----------


def test_card_status_values_match_schema():
    """CardStatus 的值集必须与 schema.VALID_STATUS 完全一致（单一事实源）。"""
    assert {s.value for s in CardStatus} == VALID_STATUS


def test_card_status_str_enum():
    """str 枚举：frontmatter 取值用 .value（str(枚举) 返回 "CardStatus.X" repr）。"""
    assert CardStatus.DRAFT.value == "draft"
    assert CardStatus("current") is CardStatus.CURRENT
    assert CardStatus.ARCHIVE.value == "archive"
    assert str(CardStatus.DRAFT.value) == "draft"


def test_docstring_and_enum_members_complete():
    assert {s for s in CardStatus} == {
        CardStatus.DRAFT,
        CardStatus.CURRENT,
        CardStatus.ARCHIVE,
        CardStatus.UNKNOWN,
    }
