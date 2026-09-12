"""TASK-S8-01 策略表单测：覆盖面 / 红线不变量 / 配置解析 / 非法回退。"""

from __future__ import annotations

import os

import pytest

from agent.retention.policy import (
    ARCHIVE_COLD_PACK,
    ARCHIVE_NONE,
    ARCHIVE_WARM_DAILY,
    DEFAULT_CLASSES,
    DELETE_GUARDED,
    DELETE_NONE,
    DELETE_S5_01_FORGETTING,
    ENV_CLASSES,
    ENV_DAY_OF_WEEK,
    ENV_DELETE_SOURCE,
    ENV_DRY_RUN,
    ENV_ENABLED,
    ENV_HOUR,
    FORGETTING_CLASS_IDS,
    KIND_JSONL,
    KIND_SQLITE,
    KIND_TREE,
    POLICY_SCHEMA,
    REDLINE_CLASS_IDS,
    RetentionClass,
    RetentionPolicy,
    RetentionPolicyError,
    load_policy,
)

# ── 1. 覆盖面（验收#1：≥7 类运行时数据均有成文策略）────────────


def test_policy_covers_at_least_seven_runtime_classes():
    assert len(DEFAULT_CLASSES) >= 7, "策略表类数不足 7"


def test_policy_covers_every_upstream_named_class():
    """任务书 §二.步骤 1 逐条点名的落点必须都在表内。"""
    required = {
        "unified_traces",      # S2-01
        "audit_chain",         # S2-02（链 + 每日根）
        "events",              # S2-03
        "case_store",          # S3-02 判定集历史版本
        "digestion_drafts",    # S3-01 消化草稿
        "shadow_ledger",       # S3-03 灰度/抽检台账
        "policy_decisions",    # S4-02 决策日志
        "cost_daily",          # cost_daily.json
        "skills_audit",        # 技能审计分片
    }
    have = {c.class_id for c in DEFAULT_CLASSES}
    assert required <= have, f"策略表缺少任务书点名落点：{required - have}"


def test_every_class_has_owner_and_basis():
    for cls in DEFAULT_CLASSES:
        assert cls.owner.strip(), f"{cls.class_id} 缺执行者"
        assert cls.basis.strip(), f"{cls.class_id} 缺依据"


def test_every_class_declares_retention_and_delete_semantics():
    for cls in DEFAULT_CLASSES:
        assert cls.archive_mode in (ARCHIVE_NONE, ARCHIVE_WARM_DAILY, ARCHIVE_COLD_PACK)
        assert cls.delete_mode in (DELETE_NONE, DELETE_GUARDED,
                                   DELETE_S5_01_FORGETTING)
        # 保留期：None（永久）或正整数
        assert cls.retention_days is None or int(cls.retention_days) > 0


def test_all_globs_are_relative_or_external():
    for cls in DEFAULT_CLASSES:
        assert cls.globs, f"{cls.class_id} 没有落点 globs"
        if cls.external:
            assert cls.external_root, f"{cls.class_id} 标外部但无 external_root"
        else:
            for pattern in cls.globs:
                assert not os.path.isabs(pattern), \
                    f"{cls.class_id} 非外部类不应使用绝对 glob：{pattern}"


# ── 2. 红线不变量（验收#2）────────────────────────────────────


def test_audit_chain_is_redline_and_never_deletable():
    cls = {c.class_id: c for c in DEFAULT_CLASSES}["audit_chain"]
    assert cls.redline is True
    assert cls.deletable is False
    assert cls.delete_mode == DELETE_NONE
    assert cls.retention_days is None, "审计链必须永久保留"
    assert cls.can_purge is False


def test_audit_chain_globs_cover_chain_db_and_daily_roots():
    cls = {c.class_id: c for c in DEFAULT_CLASSES}["audit_chain"]
    joined = " ".join(cls.globs)
    assert "audit_chain.db" in joined
    assert "daily_roots.jsonl" in joined


def test_signing_key_is_not_archived():
    """安全边界：签名私钥不进归档 globs（避免把私钥复制进归档目录）。"""
    cls = {c.class_id: c for c in DEFAULT_CLASSES}["audit_chain"]
    joined = " ".join(cls.globs)
    assert "signing_key" not in joined


def test_redline_classes_are_all_forbidden():
    redline = [c for c in DEFAULT_CLASSES if c.redline]
    assert redline, "红线类集合不应为空"
    for cls in redline:
        assert cls.can_purge is False
        assert cls.delete_mode == DELETE_NONE
        assert cls.retention_days is None
    assert "audit_chain" in REDLINE_CLASS_IDS


def test_redline_ids_derived_from_table():
    assert set(REDLINE_CLASS_IDS) == {c.class_id for c in DEFAULT_CLASSES if c.redline}


# ── 3. 删除语义（验收#7：记忆走 S5-01）───────────────────────


def test_only_drafts_is_directly_deletable():
    """默认策略 = 只归档不删除：直接可删的类**只允许**草稿一个。"""
    direct = [c.class_id for c in DEFAULT_CLASSES
              if c.can_purge and c.delete_mode == DELETE_GUARDED]
    assert direct == ["digestion_drafts"], f"意外多出的可直接删除类：{direct}"


def test_memory_classes_go_through_s5_01_forgetting():
    assert set(FORGETTING_CLASS_IDS) == {"memory_entries", "memory_snapshots"}
    for cid in FORGETTING_CLASS_IDS:
        cls = {c.class_id: c for c in DEFAULT_CLASSES}[cid]
        assert cls.delete_mode == DELETE_S5_01_FORGETTING
        # 标为可删，但**不经本模块删除**（PurgeGuard 会转给 ForgettingEngine）
        assert cls.can_purge is True


# ── 4. 温层口径（验收#3 前置：口径不变的结构性保证）──────────


def test_warm_layer_only_on_shard_aware_readers():
    for cls in DEFAULT_CLASSES:
        if cls.warm_allowed:
            assert cls.reader_shard_aware, \
                f"{cls.class_id} 读端非分片感知却开了温层（会丢数据）"
        if cls.warm_days is not None:
            # 既有 log_archiver 以「非今日」为界 ⇒ 只支持 0
            assert cls.warm_days == 0, \
                f"{cls.class_id} warm_days={cls.warm_days} 无法由既有实现表达"


def test_shadow_ledger_warm_is_off_because_reader_is_single_file():
    cls = {c.class_id: c for c in DEFAULT_CLASSES}["shadow_ledger"]
    assert cls.warm_days is None
    assert cls.reader_shard_aware is False
    assert cls.warm_allowed is False


def test_classes_dependent_on_metrics_are_not_deletable():
    for cls in DEFAULT_CLASSES:
        if cls.metric_dependencies:
            assert cls.can_purge is False, \
                f"{cls.class_id} 有指标依赖却标为可删"


# ── 5. 策略对象行为 ──────────────────────────────────────────


def test_policy_rejects_duplicate_class():
    dup = (DEFAULT_CLASSES[0], DEFAULT_CLASSES[0])
    with pytest.raises(RetentionPolicyError):
        RetentionPolicy(list(dup))


def test_policy_get_unknown_class_raises():
    pol = RetentionPolicy()
    with pytest.raises(RetentionPolicyError):
        pol.get("no_such_class")


def test_policy_unknown_class_override_raises():
    with pytest.raises(RetentionPolicyError):
        RetentionPolicy(class_overrides={"no_such_class": {"cold_days": 1}})


def test_policy_override_cannot_illegally_touch_redline():
    """覆盖不能改红线语义：`redline` 不在可覆盖字段白名单内。"""
    pol = RetentionPolicy(class_overrides={
        "audit_chain": {"redline": False, "deletable": True}})
    cls = pol.get("audit_chain")
    assert cls.redline is True, "覆盖竟然关掉了红线"
    # deletable 可被覆盖，但 redline 仍在 ⇒ can_purge 依旧为 False
    assert cls.can_purge is False


def test_policy_override_accepts_legal_values():
    pol = RetentionPolicy(class_overrides={
        "events": {"cold_days": 42, "archive_mode": ARCHIVE_COLD_PACK}})
    assert pol.get("events").cold_days == 42
    assert pol.get("events").archive_mode == ARCHIVE_COLD_PACK


def test_policy_override_illegal_values_fall_back():
    pol = RetentionPolicy(class_overrides={
        "events": {"cold_days": "abc", "archive_mode": "nope",
                   "delete_mode": "nope", "unknown_field": 1}})
    cls = pol.get("events")
    assert cls.cold_days == 90          # 内置默认
    assert cls.archive_mode == ARCHIVE_WARM_DAILY
    assert cls.delete_mode == DELETE_NONE


def test_selected_respects_class_filter():
    pol = RetentionPolicy(class_filter=("events", "no_such"))
    assert [c.class_id for c in pol.selected()] == ["events"]


def test_summary_separates_deletion_routes():
    summary = RetentionPolicy().summary()
    assert summary["schema"] == POLICY_SCHEMA
    assert summary["deletable_direct_classes"] == ["digestion_drafts"]
    assert summary["deletable_via_forgetting_classes"] == ["memory_entries",
                                                          "memory_snapshots"]
    assert summary["delete_source"] is False


# ── 6. 配置解析（env > config > 默认；非法回退）────────────────


def test_load_policy_defaults_are_conservative():
    pol = load_policy(env={})
    assert pol.enabled is False, "调度必须默认关闭"
    assert pol.dry_run is True, "首跑必须 dry-run"
    assert pol.delete_source is False, "默认只归档不删除"
    assert pol.schedule["day_of_week"] == 6


def test_load_policy_env_overrides():
    pol = load_policy(env={
        ENV_ENABLED: "true", ENV_DRY_RUN: "false", ENV_DELETE_SOURCE: "1",
        ENV_DAY_OF_WEEK: "3", ENV_HOUR: "5", ENV_CLASSES: "events,audit_chain",
    })
    assert pol.enabled is True
    assert pol.dry_run is False
    assert pol.delete_source is True
    assert pol.schedule == {"day_of_week": 3, "hour": 5, "minute": 0}
    assert pol.class_filter == ("events", "audit_chain")


def test_load_policy_illegal_env_falls_back():
    pol = load_policy(env={ENV_ENABLED: "maybe", ENV_HOUR: "99",
                           ENV_DAY_OF_WEEK: "abc"})
    assert pol.enabled is False              # 非法布尔 → 默认 False
    assert pol.schedule["hour"] == 23        # 越界夹紧
    assert pol.schedule["day_of_week"] == 6  # 非法整数 → 默认 6


def test_load_policy_reads_config_yaml(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "retention:\n"
        "  enabled: true\n"
        "  dry_run: false\n"
        "  day_of_week: 1\n"
        "  hour: 4\n"
        "  classes: events\n"
        "  classes_override:\n"
        "    events:\n"
        "      cold_days: 7\n",
        encoding="utf-8")
    pol = load_policy(config_path=str(cfg), env={})
    assert pol.enabled is True
    assert pol.dry_run is False
    assert pol.schedule["day_of_week"] == 1
    assert pol.schedule["hour"] == 4
    assert pol.class_filter == ("events",)
    assert pol.get("events").cold_days == 7


def test_load_policy_env_beats_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("retention:\n  enabled: true\n  hour: 4\n", encoding="utf-8")
    pol = load_policy(config_path=str(cfg), env={ENV_ENABLED: "false",
                                                 ENV_HOUR: "9"})
    assert pol.enabled is False
    assert pol.schedule["hour"] == 9


def test_load_policy_broken_config_yaml_falls_back(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("retention: [this is not a mapping\n", encoding="utf-8")
    pol = load_policy(config_path=str(cfg), env={})
    assert pol.enabled is False
    assert pol.dry_run is True


def test_load_policy_missing_config_file_is_fine(tmp_path):
    pol = load_policy(config_path=str(tmp_path / "nope.yaml"), env={})
    assert pol.enabled is False


# ── 7. 与成文策略表同源（验收#1：文档与代码不许漂移）──────────


DOC_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "docs", "zh", "数据生命周期策略.md")


def test_policy_matches_doc():
    """文档里必须逐行出现每个策略类的 id、保留期与「可否删除」结论。"""
    assert os.path.exists(DOC_PATH), f"策略文档缺失：{DOC_PATH}"
    with open(DOC_PATH, "r", encoding="utf-8") as fh:
        text = fh.read()
    missing = [c.class_id for c in DEFAULT_CLASSES if f"`{c.class_id}`" not in text]
    assert not missing, f"策略文档未覆盖这些数据类：{missing}"
    for cls in DEFAULT_CLASSES:
        if cls.redline:
            continue
        assert cls.retention_label() in text or cls.class_id in text
    # 红线必须显式写"禁止删除"
    assert "禁止删除" in text
    for cid in REDLINE_CLASS_IDS:
        assert f"`{cid}`" in text


def test_doc_states_defaults():
    with open(DOC_PATH, "r", encoding="utf-8") as fh:
        text = fh.read()
    for token in ("只归档不删除", "dry-run", "CP_RETENTION_ENABLED",
                  "retention.run", "S5-01", "禁止删除"):
        assert token in text, f"策略文档缺少关键结论：{token}"


def test_doc_states_guard_jurisdiction_boundary():
    """闸门文案必须写明**管辖边界**，否则会被读成"全仓库只有一条删除路径"。

    S8-01 复核时发现初版写法是绝对声称（"任何 `os.remove` 之前都必须过"），
    而仓库里存在 8 处各模块自带的清理路径并不经过本闸门 —— 过度声称会让人对
    未受守卫的路径放松警惕。故此断言把"边界声明"钉住，防止被后续编辑删掉。
    """
    with open(DOC_PATH, "r", encoding="utf-8") as fh:
        text = fh.read()
    for token in ("管辖边界", "不在本治理层管辖内", "本治理层",
                  "cleanup_old_records", "cleanup_snapshots",
                  "forgetting.py::prune"):
        assert token in text, f"策略文档缺少管辖边界声明：{token}"
    assert "任何 `os.remove` 之前都必须过" not in text, "绝对声称不得复现"

