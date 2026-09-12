"""TASK-S8-01 删除闸门单测（验收#2：审计链/每日根禁止删除 + 误传审计类即拒）。"""

from __future__ import annotations

import os

import pytest

from agent.retention.guard import (
    CODE_FORGETTING_PATH,
    CODE_METRIC_DEPENDENCY,
    CODE_NOT_DELETABLE,
    CODE_NO_PATHS,
    CODE_NOT_FROZEN,
    CODE_OK,
    CODE_OUT_OF_SCOPE,
    CODE_REDLINE,
    CODE_UNKNOWN_CLASS,
    FORGETTING_ENTRY,
    PurgeGuard,
    assert_no_redline_deletion,
)
from agent.retention.policy import (
    ARCHIVE_COLD_PACK,
    DEFAULT_CLASSES,
    DELETE_GUARDED,
    DELETE_NONE,
    DELETE_S5_01_FORGETTING,
    KIND_TREE,
    RetentionClass,
    RetentionPolicy,
    REDLINE_CLASS_IDS,
)

from retention_testkit import (       # noqa: E402
    drafts_class,
    events_class,
    make_policy,
    make_root,
    retention_class,
)


def _draft(root: str) -> str:
    path = os.path.normpath(os.path.join(root, "data", "digestion", "drafts",
                                         "dig-abc", "SKILL.md"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# draft\n")
    return path


def _builtin_policy() -> RetentionPolicy:
    """内置策略表（红线 / 记忆 / 可删标记全部按生产口径）。"""
    return RetentionPolicy()


# ── 1. 红线：审计链与每日根禁止删除 ──────────────────────────


@pytest.mark.parametrize("class_id", list(REDLINE_CLASS_IDS))
def test_redline_classes_are_refused(class_id):
    guard = PurgeGuard(_builtin_policy())
    decision = guard.check(class_id)
    assert decision.allowed is False
    assert decision.code == CODE_REDLINE
    assert "禁止删除" in "；".join(decision.reasons)


def test_mis_passed_audit_class_is_refused(tmp_path):
    """**验收硬要求**：把审计类当普通类传进来（带具体路径）也必须被拒。"""
    root = make_root(tmp_path)
    guard = PurgeGuard(_builtin_policy(), root=root)
    decision = guard.check("audit_chain",
                           [os.path.join(root, "data", "audit", "audit_chain.db")])
    assert decision.allowed is False
    assert decision.code == CODE_REDLINE
    assert decision.redirect == ""


def test_redline_scan_refuses_every_redline_class():
    guard = PurgeGuard(_builtin_policy())
    rows = {r["class_id"]: r for r in guard.redline_scan()}
    for cid in REDLINE_CLASS_IDS:
        assert rows[cid]["allowed"] is False
        assert rows[cid]["code"] == CODE_REDLINE


def test_assert_no_redline_deletion_raises():
    with pytest.raises(PermissionError):
        assert_no_redline_deletion(_builtin_policy(), ["events", "audit_chain"])
    assert_no_redline_deletion(_builtin_policy(), ["events"])   # 非红线不抛


def test_assert_deletable_raises_permission_error():
    guard = PurgeGuard(_builtin_policy())
    with pytest.raises(PermissionError):
        guard.assert_deletable("audit_chain")


def test_guard_describe_lists_codes_and_classes():
    described = PurgeGuard(_builtin_policy()).describe()
    assert CODE_REDLINE in described["reject_codes"]
    assert set(described["redline_classes"]) == set(REDLINE_CLASS_IDS)
    assert described["forgetting_entry"] == FORGETTING_ENTRY


# ── 2. 记忆类：必须走 S5-01「删记忆不删证据」──────────────────


@pytest.mark.parametrize("class_id", ["memory_entries", "memory_snapshots"])
def test_memory_classes_are_redirected_to_forgetting(class_id):
    decision = PurgeGuard(_builtin_policy()).check(class_id)
    assert decision.allowed is False
    assert decision.code == CODE_FORGETTING_PATH
    assert decision.redirect == FORGETTING_ENTRY


def test_memory_class_never_guarded_even_if_misconfigured():
    """即使有人把记忆类改成 `guarded`，护栏仍按"记忆"处置？——否。

    记忆的判定依据是 `delete_mode`；若被改成 `guarded`，护栏只能按可删处理。
    故这里断言**策略表本身**不允许这种写法：内置表里记忆类恒为
    `s5_01_forgetting`（配置覆盖同样受限，见 policy 单测）。
    """
    for cls in DEFAULT_CLASSES:
        if cls.class_id.startswith("memory_"):
            assert cls.delete_mode == DELETE_S5_01_FORGETTING


# ── 3. 未标可删 / 指标依赖 / 越界 / 未归档 ────────────────────


def test_class_without_deletable_flag_is_refused(tmp_path):
    root = make_root(tmp_path)
    cls = retention_class("x", globs=("data/x/*.jsonl",), deletable=False,
                          delete_mode=DELETE_NONE)
    guard = PurgeGuard(make_policy(root, [cls]), root=root)
    decision = guard.check("x")
    assert decision.allowed is False
    assert decision.code == CODE_NOT_DELETABLE


def test_class_with_metric_dependency_is_refused(tmp_path):
    root = make_root(tmp_path)
    cls = retention_class("x", globs=("data/x/*.jsonl",),
                          metric_dependencies=("utc.weekly",))
    guard = PurgeGuard(make_policy(root, [cls]), root=root)
    decision = guard.check("x")
    assert decision.allowed is False
    assert decision.code == CODE_METRIC_DEPENDENCY
    assert "utc.weekly" in "；".join(decision.reasons)


def test_out_of_scope_path_is_refused(tmp_path):
    root = make_root(tmp_path)
    inside = os.path.join(root, "data", "x", "a.jsonl")
    os.makedirs(os.path.dirname(inside), exist_ok=True)
    with open(inside, "w", encoding="utf-8") as fh:
        fh.write("{}\n")
    outside = os.path.join(root, "data", "audit", "audit_chain.db")
    os.makedirs(os.path.dirname(outside), exist_ok=True)
    with open(outside, "w", encoding="utf-8") as fh:
        fh.write("x")

    cls = retention_class("x", globs=("data/x/*.jsonl",))
    guard = PurgeGuard(make_policy(root, [cls]), root=root)
    decision = guard.check("x", [outside])
    assert decision.allowed is False
    assert decision.code == CODE_OUT_OF_SCOPE


def test_empty_path_list_is_refused(tmp_path):
    root = make_root(tmp_path)
    cls = retention_class("x", globs=("data/x/*.jsonl",))
    guard = PurgeGuard(make_policy(root, [cls]), root=root)
    assert guard.check("x", []).code == CODE_NO_PATHS


def test_unknown_class_is_refused():
    decision = PurgeGuard(_builtin_policy()).check("no_such_class")
    assert decision.allowed is False
    assert decision.code == CODE_UNKNOWN_CLASS


def test_not_frozen_is_refused_when_archived_set_provided(tmp_path):
    """**先归档、验签、再删除**：没有归档件时不许删。"""
    root = make_root(tmp_path)
    draft = _draft(root)
    cls = drafts_class()
    guard = PurgeGuard(make_policy(root, [cls]), root=root, archived_paths=[])
    decision = guard.check("drafts" if False else "digestion_drafts",
                           [draft], require_archived=True)
    assert decision.allowed is False
    assert decision.code == CODE_NOT_FROZEN


def test_allowed_when_deletable_in_scope_and_archived(tmp_path):
    root = make_root(tmp_path)
    draft = _draft(root)
    guard = PurgeGuard(make_policy(root, [drafts_class()]), root=root,
                       archived_paths=[draft])
    decision = guard.check("digestion_drafts", [draft])
    assert decision.allowed is True
    assert decision.code == CODE_OK
    assert decision.checked_paths == 1
    assert decision.total_bytes == os.path.getsize(draft) > 0


def test_decision_is_serializable():
    decision = PurgeGuard(_builtin_policy()).check("audit_chain")
    payload = decision.to_dict()
    assert payload["allowed"] is False and payload["code"] == CODE_REDLINE
    assert "audit_chain" in decision.summary()


# ── 4. 生产策略表整体自证：非可删类一个都过不去 ────────────────


def test_no_non_deletable_class_can_pass_the_guard():
    policy = _builtin_policy()
    guard = PurgeGuard(policy)
    for cls in policy.classes:
        decision = guard.check(cls.class_id)
        if cls.class_id == "digestion_drafts":
            # 唯一可删类：无文件时因"空清单"被拒（保守），也是拒绝
            assert decision.allowed is False
            assert decision.code == CODE_NO_PATHS
            continue
        assert decision.allowed is False, \
            f"{cls.class_id} 竟然通过了护栏：{decision.summary()}"


def test_forgetting_classes_report_redirect_not_ok():
    guard = PurgeGuard(_builtin_policy())
    for cid in ("memory_entries", "memory_snapshots"):
        decision = guard.check(cid)
        assert decision.redirect and not decision.allowed
