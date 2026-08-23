"""元规则版本化存储与变更门控测试（任务4 Step 2/3/5）

覆盖（评估标准锚点）:
    1. schema 校验：合法值通过 / 非法值回退默认（类型/范围/枚举）
    2. bump：pending 版本零生效（未审批不影响读取值）
    3. 审批门控：批准后生效 / 驳回保持当前版本 / pending 期间拒绝并发 bump
    4. rollback：恢复上一版本快照（回滚后生效值与旧版本一致）
    5. diff：版本间差异正确
    6. 审计：G3 字段（change_id/param/old/new/approver/rollback_command）齐备
    7. migrate：dry-run 仅报告差异；apply 受 META_POLICY_MIGRATE_DRY_RUN_ONLY 门控
    8. 只读默认：未启用变更时 bump 被拒；变更失败不抛异常
    9. read_runtime_value 优先级（env > config > 默认）
    10. CLI main() 退出码
"""
import json
import os
from pathlib import Path

import pytest

from agent.learning.meta_policy import (
    BumpResult,
    MetaPolicyError,
    MetaPolicySchema,
    MetaPolicyStore,
    read_runtime_value,
    validate_value,
)
from agent.skills_mgmt.approval import ApprovalFlow

PARAM = "schedule.evolver_interval_days"
PARAM2 = "budget.mode"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """临时目录隔离的存储 + 隔离的审批记录（不触碰仓库数据）"""
    monkeypatch.setenv("APPROVAL_RECORDS_PATH", str(tmp_path / "approvals.jsonl"))
    flow = ApprovalFlow(records_path=str(tmp_path / "approvals.jsonl"))
    s = MetaPolicyStore(str(tmp_path / "store"), approval_flow=flow)
    return s


def approve(store, record_id, actor="reviewer"):
    """测试辅助：一次完成批准+生效"""
    return store.approve_and_apply(record_id, actor=actor)


# ════════════════════════════════════════════════════════════
#  1. schema 校验
# ════════════════════════════════════════════════════════════

class TestSchemaValidation:
    def test_schema_loads_52_entries(self):
        schema = MetaPolicySchema()
        assert len(schema) == 52
        assert PARAM in schema.names()
        assert PARAM2 in schema.names()

    def test_valid_values_pass(self):
        schema = MetaPolicySchema()
        report = schema.validate({PARAM: 14, PARAM2: "enforce",
                                  "eval.stage1_min_score": 0.4})
        assert report["valid"] is True
        assert report["normalized"][PARAM] == 14
        assert report["normalized"][PARAM2] == "enforce"

    def test_invalid_value_falls_back_to_default(self):
        """非法值回退默认（沿用 EVO 总览 §六.2 约定）"""
        schema = MetaPolicySchema()
        res = schema.validate({PARAM: -5})["results"][0]
        assert res["valid"] is False
        assert res["value"] == 7          # schema 默认
        assert "回退默认" in res["reason"]

    def test_invalid_type_falls_back(self):
        schema = MetaPolicySchema()
        res = schema.validate({PARAM: "abc"})["results"][0]
        assert res["valid"] is False
        assert res["value"] == 7

    def test_invalid_bool_falls_back(self):
        schema = MetaPolicySchema()
        res = schema.validate({"schedule.evolver_dry_run": "maybe"})["results"][0]
        assert res["valid"] is False
        assert res["value"] is True       # 默认 dry_run=True

    def test_invalid_enum_falls_back(self):
        schema = MetaPolicySchema()
        res = schema.validate({PARAM2: "nuclear"})["results"][0]
        assert res["valid"] is False
        assert res["value"] == "warn_only"

    def test_unknown_param_rejected(self):
        schema = MetaPolicySchema()
        with pytest.raises(MetaPolicyError):
            schema.validate({"not_a_param": 1})

    def test_validate_value_min_max(self):
        entry = MetaPolicySchema().get(PARAM)
        assert validate_value(entry, 1)["valid"] is True
        assert validate_value(entry, 0)["valid"] is False      # min=1
        entry2 = MetaPolicySchema().get("trigger.window_weeks")
        assert validate_value(entry2, 53)["valid"] is False     # max=52


# ════════════════════════════════════════════════════════════
#  2. bump / pending 零生效 / 审批门控
# ════════════════════════════════════════════════════════════

class TestBumpAndApproval:
    def test_bootstrap_creates_v1_from_defaults(self, store):
        assert store.current_version() == "v1"
        assert store.get_effective_value(PARAM) == 7

    def test_bump_pending_zero_effect(self, store):
        """未审批变更零生效：pending 版本不影响读取值"""
        before = store.effective_values()
        res = store.bump({PARAM: 14}, description="测试变更")
        assert res.ok is True
        assert res.status == "pending"
        assert res.effective is False
        assert store.current_version() == "v1"          # 版本指针未变
        assert store.get_effective_value(PARAM) == 7    # 读取值未变
        assert store.effective_values() == before
        pending = store.pending()
        assert pending is not None
        assert pending["version"] == "v2"
        assert pending["status"] == "pending"

    def test_second_bump_rejected_while_pending(self, store):
        store.bump({PARAM: 14}, description="a")
        res2 = store.bump({PARAM2: "enforce"}, description="b")
        assert res2.ok is False
        assert "待审批" in res2.message

    def test_approve_makes_effective(self, store):
        res = store.bump({PARAM: 14}, description="a")
        out = approve(store, res.approval_record_id)
        assert out.effective is True
        assert store.current_version() == "v2"
        assert store.get_effective_value(PARAM) == 14
        assert store.pending()["status"] == "applied"

    def test_reject_keeps_current(self, store):
        res = store.bump({PARAM: 14}, description="a")
        out = store.reject_change(res.approval_record_id, reason="测试驳回")
        assert out.status == "rejected"
        assert store.current_version() == "v1"
        assert store.get_effective_value(PARAM) == 7
        # 驳回后可提出新变更
        res2 = store.bump({PARAM: 21}, description="b")
        assert res2.ok is True

    def test_bump_unknown_param_rejected_without_effect(self, store):
        res = store.bump({"nope.param": 1}, description="x")
        assert res.ok is False
        assert "未登记参数" in res.message
        assert store.current_version() == "v1"

    def test_disabled_store_rejects_changes(self, tmp_path):
        store = MetaPolicyStore(str(tmp_path / "off"), enabled=False)
        res = store.bump({PARAM: 14}, description="x")
        assert res.ok is False
        assert "ENABLED=false" in res.message
        # 只读查询仍可用
        assert store.current_version() == "v1"

    def test_bump_empty_changes(self, store):
        res = store.bump({}, description="x")
        assert res.ok is False

    def test_bump_never_raises_on_failure(self, store, monkeypatch):
        """变更失败绝不阻断主流程"""
        import agent.learning.meta_policy as mp
        monkeypatch.setattr(mp, "_atomic_write_json",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        res = store.bump({PARAM: 14}, description="x")
        assert res.ok is False
        assert res.status == "error"


# ════════════════════════════════════════════════════════════
#  3. rollback：快照恢复（回滚后生效值与旧版本一致）
# ════════════════════════════════════════════════════════════

class TestRollback:
    def test_rollback_restores_previous_snapshot(self, store):
        v1_values = store.effective_values()
        store.bump({PARAM: 14}, description="a")
        approve(store, store.pending()["approval_record_id"])
        assert store.get_effective_value(PARAM) == 14

        rb = store.rollback(description="回滚测试")
        assert rb.ok is True
        assert rb.status == "pending"
        approve(store, rb.approval_record_id)
        # 回滚后生效值与旧版本（v1）一致
        assert store.current_version() == "v3"
        assert store.get_effective_value(PARAM) == 7
        assert store.effective_values() == v1_values

    def test_rollback_v1_noop(self, store):
        rb = store.rollback()
        assert rb.ok is False
        assert "无可回滚" in rb.message

    def test_rollback_target_version(self, store):
        store.bump({PARAM: 14}, description="a")
        approve(store, store.pending()["approval_record_id"])
        store.bump({PARAM: 21}, description="b")
        approve(store, store.pending()["approval_record_id"])
        assert store.get_effective_value(PARAM) == 21
        rb = store.rollback(target_version="v2", description="回滚到 v2")
        approve(store, rb.approval_record_id)
        assert store.get_effective_value(PARAM) == 14

    def test_version_chain_append_only(self, store):
        store.bump({PARAM: 14}, description="a")
        approve(store, store.pending()["approval_record_id"])
        versions = store.list_versions()
        assert [v["version"] for v in versions] == ["v1", "v2"]
        assert versions[-1]["status"] == "effective"


# ════════════════════════════════════════════════════════════
#  4. diff
# ════════════════════════════════════════════════════════════

class TestDiff:
    def test_diff_current_vs_previous(self, store):
        store.bump({PARAM: 14}, description="a")
        approve(store, store.pending()["approval_record_id"])
        d = store.diff()
        assert d["from"] == "v1"
        assert d["to"] == "v2"
        assert d["changed"][PARAM] == {"from": 7, "to": 14}

    def test_diff_specific_versions(self, store):
        store.bump({PARAM: 14}, description="a")
        approve(store, store.pending()["approval_record_id"])
        d = store.diff("v1", "v2")
        assert d["changed"][PARAM]["to"] == 14

    def test_diff_no_earlier_version(self, store):
        with pytest.raises(MetaPolicyError):
            store.diff()


# ════════════════════════════════════════════════════════════
#  5. 审计（G3 字段）
# ════════════════════════════════════════════════════════════

class TestAudit:
    def test_audit_records_g3_fields(self, store):
        store.bump({PARAM: 14}, description="审计测试")
        rows = store.list_audit(100)
        bump_rows = [r for r in rows if r["event"] == "bump"]
        assert bump_rows
        row = bump_rows[0]
        assert row["param"] == PARAM
        assert row["old"] == 7
        assert row["new"] == 14
        assert row["change_id"]
        assert row["rollback_command"].startswith("python -m agent.learning.meta_policy rollback")
        assert "approval_record_id" in row

    def test_audit_isolated_in_store_dir(self, store):
        """隔离 store_dir 的审计落在实例目录内，绝不泄漏到默认仓库路径"""
        store.bump({PARAM: 14}, description="隔离测试")
        assert store.audit_path == store.store_dir / "audit.jsonl"
        assert store.audit_path.exists()
        default_audit = Path("data/learning/meta_policy/audit.jsonl")
        if default_audit.exists():
            # 默认路径（若有历史审计）不得包含本次变更
            content = default_audit.read_text(encoding="utf-8")
            assert "隔离测试" not in content

    def test_audit_approve_records_approver(self, store):
        res = store.bump({PARAM: 14}, description="x")
        approve(store, res.approval_record_id, actor="alice")
        rows = store.list_audit(100)
        apply_rows = [r for r in rows if r["event"] == "apply"]
        assert apply_rows
        assert all(r["approver"] == "approval-flow" for r in apply_rows)


# ════════════════════════════════════════════════════════════
#  6. migrate（灰度可选；默认 dry-run）
# ════════════════════════════════════════════════════════════

class TestMigrate:
    def test_migrate_dry_run_reports_drift(self, store):
        out = store.migrate()
        assert out["dry_run"] is True
        assert "drifted" in out and "aligned" in out
        assert out["applied_change_id"] is None

    def test_migrate_apply_blocked_by_default(self, store):
        out = store.migrate(apply=True)
        assert out["dry_run"] is True
        assert "DRY_RUN_ONLY" in out["error"]

    def test_migrate_apply_allowed_when_switch_off(self, store, monkeypatch):
        monkeypatch.setenv("META_POLICY_MIGRATE_DRY_RUN_ONLY", "false")
        out = store.migrate(apply=True)
        assert out["dry_run"] is False
        assert out["applied_change_id"] is not None


# ════════════════════════════════════════════════════════════
#  7. 运行时值读取（登记用途；优先级 env > config > 默认）
# ════════════════════════════════════════════════════════════

class TestRuntimeValue:
    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("LEARNING_EVOLVER_INTERVAL_DAYS", "30")
        assert read_runtime_value(PARAM) == 30
        monkeypatch.delenv("LEARNING_EVOLVER_INTERVAL_DAYS")

    def test_config_yaml_fallback(self, monkeypatch):
        monkeypatch.delenv("LEARNING_EVOLVER_INTERVAL_DAYS", raising=False)
        # config.yaml learning.evolver.interval_days=7
        assert read_runtime_value(PARAM) == 7

    def test_code_default(self, monkeypatch):
        monkeypatch.delenv("EVOLUTION_CHILD_PENALTY_N", raising=False)
        assert read_runtime_value("evolver.child_penalty_n") == 8

    def test_unknown_name(self):
        assert read_runtime_value("no.such.param") is None


# ════════════════════════════════════════════════════════════
#  8. CLI
# ════════════════════════════════════════════════════════════

class TestCLI:
    def _run(self, tmp_path, monkeypatch, argv):
        monkeypatch.setenv("META_POLICY_STORE_DIR", str(tmp_path / "cli_store"))
        monkeypatch.setenv("APPROVAL_RECORDS_PATH", str(tmp_path / "approvals.jsonl"))
        from agent.learning.meta_policy import main
        return main(argv)

    def test_cli_validate(self, tmp_path, monkeypatch, capsys):
        rc = self._run(tmp_path, monkeypatch,
                       ["validate", "--param", f"{PARAM}=14"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["valid"] is True

    def test_cli_bump_and_approve_flow(self, tmp_path, monkeypatch, capsys):
        rc = self._run(tmp_path, monkeypatch,
                       ["bump", "--param", f"{PARAM}=14", "--description", "cli测试"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "pending"
        record = out["approval_record_id"]
        rc = self._run(tmp_path, monkeypatch, ["approve", "--record", record])
        assert rc == 0
        out2 = json.loads(capsys.readouterr().out)
        assert out2["effective"] is True

    def test_cli_unknown_param_exit_2(self, tmp_path, monkeypatch, capsys):
        rc = self._run(tmp_path, monkeypatch, ["bump", "--param", "nope=1"])
        assert rc == 2

    def test_cli_status(self, tmp_path, monkeypatch, capsys):
        rc = self._run(tmp_path, monkeypatch, ["status"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["current_version"] == "v1"
        assert out["schema_entries"] == 52
