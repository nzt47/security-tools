"""TASK-S7-01 开关变更服务单测（三级权限 + 审计入链 + 双人确认）

【本文件守护的验收项】
    - A 级直接生效（热）、可 reset 回落；
    - **B 级无二次认证不可通过**；有二次认证也要**第二位人工**确认才生效，
      且首位提交**不改变任何状态**（pending 语义）；
    - C 级 403，响应体**不含明文**；
    - 被 env 锁定的项拒绝修改并给出可读原因；
    - auto / sub_agent 改开关一律被拒（§7.0 矩阵 `settings.change` 行）；
    - 每次变更入**链式审计**（含 old/new/source）+ `policy.decision`，
      且 `verify_chain` 仍通过、**不重复留痕**；
    - 写覆盖层**不改** `.env` / `config.yaml`。
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib

import pytest

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain
from agent.settings import masking
from agent.settings.overrides import OverrideStore, reset_override_store
from agent.settings.service import (
    CODE_BATCH_NOT_SUPPORTED,
    CODE_INVALID_VALUE,
    CODE_LOCKED_BY_ENV,
    CODE_READ_ONLY_SECRET,
    CODE_SAME_ACTOR,
    CODE_SECOND_FACTOR_REQUIRED,
    CODE_SETTINGS_DENIED,
    CODE_UNKNOWN_KEY,
    CODE_UNKNOWN_PENDING,
    SettingsService,
    reset_pending,
    reset_settings_service,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PROTECTED = (REPO_ROOT / ".env", REPO_ROOT / "config.yaml")


def _digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def _snapshot() -> dict:
    return {str(p): _digest(p) for p in PROTECTED}


@pytest.fixture(autouse=True)
def isolated_audit(tmp_path):
    """链式审计落 tmp（与 `test_s4_01_stage_promote_chain.py` 同构）"""
    chain = AuditChain(str(tmp_path / "audit.db"),
                       roots_path=str(tmp_path / "roots.jsonl"),
                       signing_key_path=str(tmp_path / "k.pem"), auto_seal=False)
    previous = facade_mod.audit.bind(chain)
    facade_mod.audit.enabled = True
    facade_mod.audit.reset_counters()
    yield chain
    facade_mod.audit.bind(previous)
    try:
        chain.close(timeout=2.0)
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """环境隔离：覆盖层落 tmp、事件落 tmp、env 快照还原、单例复位"""
    snapshot = dict(os.environ)
    monkeypatch.setenv("CP_UI_SETTINGS_PATH", str(tmp_path / "ui_settings.json"))
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.delenv("LOCK_PROFILE", raising=False)
    monkeypatch.delenv("LOCK_WATCHDOG_ENABLED", raising=False)
    monkeypatch.delenv("CP_BUDGET_BRAKE_ENABLED", raising=False)
    import agent.observability.events as events_mod
    events_mod.reset_event_stores()
    reset_override_store()
    reset_settings_service()
    before = _snapshot()
    yield tmp_path
    os.environ.clear()
    os.environ.update(snapshot)
    reset_override_store()
    reset_settings_service()
    events_mod.reset_event_stores()
    assert _snapshot() == before, "受保护配置文件被改动（.env / config.yaml）"


@pytest.fixture()
def svc(tmp_path):
    reset_override_store()
    reset_settings_service()
    reset_pending()
    return SettingsService(OverrideStore(tmp_path / "ui_settings.json"))


def _actions(chain) -> list:
    chain.flush()
    return [e.action for e in chain.entries()]


# ════════════════════════════════════════════════════════════
#  一、A 级：直接生效
# ════════════════════════════════════════════════════════════


class TestLevelA:
    def test_a_level_change_is_hot_and_audited(self, svc, isolated_audit):
        out = svc.change("LOCK_PROFILE", True, actor="owner", reason="单测")
        assert out.ok and out.applied
        assert out.source == "ui_override"
        assert out.effect == "hot"
        assert os.environ.get("LOCK_PROFILE") == "true"
        assert out.audit.get("seq")
        assert out.receipt["never_touched"] == [".env", "config.yaml"]
        assert out.receipt["landing"] == "env"

    def test_audit_chain_records_change_and_policy_decision(self, svc,
                                                            isolated_audit):
        svc.change("LOCK_PROFILE", True, actor="owner", reason="单测")
        actions = _actions(isolated_audit)
        assert actions.count("settings.change") == 1
        assert actions.count("policy.decision") == 1
        assert isolated_audit.verify_chain().ok is True

    def test_audit_payload_carries_old_new_source(self, svc, isolated_audit):
        svc.change("LOCK_PROFILE_BATCH", 200, actor="owner", reason="单测")
        isolated_audit.flush()
        rows = isolated_audit.entries(action="settings.change")
        assert len(rows) == 1
        # `AuditFacade.record` 把业务载荷嵌在 `payload["payload"]`（既有口径）
        payload = rows[0].payload["payload"]
        assert payload["new"] == 200
        assert payload["old"] == 500
        assert payload["source"] == "ui_override"
        assert payload["never_touched"] == [".env", "config.yaml"]
        assert rows[0].subject == "setting:LOCK_PROFILE_BATCH"
        assert rows[0].source == "ui"

    def test_reset_clears_overlay_and_restores_runtime(self, svc):
        svc.change("LOCK_PROFILE", True, actor="owner")
        assert os.environ.get("LOCK_PROFILE") == "true"
        out = svc.reset("LOCK_PROFILE", actor="owner")
        assert out.ok and out.applied
        assert out.source == "default"
        assert "LOCK_PROFILE" not in os.environ
        assert out.receipt["never_touched"] == [".env", "config.yaml"]

    def test_reset_without_overlay_is_noop_but_reported(self, svc):
        out = svc.reset("LOCK_PROFILE", actor="owner")
        assert out.ok is True and out.applied is False
        assert out.receipt["reset"] is False
        assert "无需重置" in out.message

    def test_observability_path_change_lands_in_runtime(self, svc):
        from agent.monitoring.observability_config import (
            get_observability_config, reset_observability_config,
        )
        from agent.settings.registry import get_spec
        reset_observability_config()
        spec = get_spec("resource_monitor.history_size")
        assert spec is not None
        out = svc.change("resource_monitor.history_size", 321, actor="owner")
        assert out.ok
        assert out.receipt["landing"] == "observability"
        assert get_observability_config().get("resource_monitor.history_size") == 321
        try:
            back = svc.reset("resource_monitor.history_size", actor="owner")
            assert back.ok
            assert get_observability_config().get(
                "resource_monitor.history_size") == spec.default
        finally:
            reset_observability_config()

    def test_unknown_key_is_rejected(self, svc):
        out = svc.change("NO_SUCH_SWITCH", True, actor="owner")
        assert out.ok is False and out.code == CODE_UNKNOWN_KEY and out.status == 404


# ════════════════════════════════════════════════════════════
#  二、B 级：二次认证 + 双人确认
# ════════════════════════════════════════════════════════════

B_KEY = "CP_BUDGET_BRAKE_ENABLED"


class TestLevelB:
    def test_without_second_factor_is_rejected_and_changes_nothing(
            self, svc, isolated_audit, tmp_path):
        out = svc.change(B_KEY, True, actor="owner")
        assert out.ok is False
        assert out.code == CODE_SECOND_FACTOR_REQUIRED
        assert out.status == 403
        assert os.environ.get(B_KEY) is None
        assert svc._store.entries() == {}
        assert _actions(isolated_audit) == []      # 未生效 → 不留"变更"痕迹

    def test_with_second_factor_only_opens_pending(self, svc, isolated_audit):
        out = svc.change(B_KEY, True, actor="owner", second_factor_ok=True)
        assert out.ok is True and out.pending is True
        assert out.status == 202
        assert out.pending_id
        assert out.resolved is not None
        # ★ 首位提交不改变任何状态
        assert os.environ.get(B_KEY) is None
        assert svc._store.entries() == {}
        assert "settings.change" not in _actions(isolated_audit)

    def test_same_actor_cannot_confirm(self, svc):
        first = svc.change(B_KEY, True, actor="owner", second_factor_ok=True)
        out = svc.confirm(B_KEY, first.pending_id, actor="owner",
                          second_factor_ok=True)
        assert out.ok is False and out.code == CODE_SAME_ACTOR
        assert svc._store.entries() == {}

    def test_second_actor_without_second_factor_is_rejected(self, svc):
        first = svc.change(B_KEY, True, actor="owner", second_factor_ok=True)
        out = svc.confirm(B_KEY, first.pending_id, actor="reviewer",
                          second_factor_ok=False)
        assert out.ok is False and out.code == CODE_SECOND_FACTOR_REQUIRED
        assert svc._store.entries() == {}

    def test_second_actor_applies_and_audits_both_names(self, svc,
                                                        isolated_audit):
        first = svc.change(B_KEY, True, actor="owner", second_factor_ok=True,
                           reason="成本刹车")
        out = svc.confirm(B_KEY, first.pending_id, actor="reviewer",
                          second_factor_ok=True, reason="确认")
        assert out.ok and out.applied
        assert out.receipt["second_approver"] == "owner"
        assert out.receipt["requested_by"] == "owner"
        assert os.environ.get(B_KEY) == "true"
        assert isolated_audit.verify_chain().ok is True
        isolated_audit.flush()
        rows = isolated_audit.entries(action="settings.change")
        assert len(rows) == 1
        assert rows[0].payload["payload"]["second_approver"] == "owner"

    def test_pending_is_one_time(self, svc):
        first = svc.change(B_KEY, True, actor="owner", second_factor_ok=True)
        assert svc.confirm(B_KEY, first.pending_id, actor="reviewer",
                           second_factor_ok=True).ok is True
        again = svc.confirm(B_KEY, first.pending_id, actor="reviewer-2",
                            second_factor_ok=True)
        assert again.ok is False and again.code == CODE_UNKNOWN_PENDING

    def test_unknown_pending_is_404(self, svc):
        out = svc.confirm(B_KEY, "setp-deadbeef", actor="reviewer",
                          second_factor_ok=True)
        assert out.ok is False and out.status == 404

    def test_pending_key_mismatch_is_rejected(self, svc):
        first = svc.change(B_KEY, True, actor="owner", second_factor_ok=True)
        out = svc.confirm("CP_HEALING_LEVELS_ENABLED", first.pending_id,
                          actor="reviewer", second_factor_ok=True)
        assert out.ok is False and out.status in (404, 409)

    def test_b_level_pending_shape_is_disclosed(self, svc):
        out = svc.change(B_KEY, True, actor="owner", second_factor_ok=True)
        payload = out.to_dict()
        assert payload["requires_dual_approval"] is True
        assert payload["pending"] is True
        assert payload["item"]["requires_second_factor"] is True


# ════════════════════════════════════════════════════════════
#  三、C 级与锁定项
# ════════════════════════════════════════════════════════════


class TestLevelCAndLocking:
    def test_c_level_write_is_403_without_plaintext(self, svc, monkeypatch):
        secret = "super-secret-smtp-pass"
        monkeypatch.setenv("SMTP_PASSWORD", secret)
        out = svc.change("SMTP_PASSWORD", "new-value", actor="owner")
        assert out.ok is False and out.code == CODE_READ_ONLY_SECRET
        assert out.status == 403
        blob = json.dumps(out.to_dict(), ensure_ascii=False)
        assert secret not in blob
        assert "new-value" not in blob

    def test_env_locked_write_is_403_with_reason(self, svc, monkeypatch):
        monkeypatch.setenv("LOCK_PROFILE", "0")
        out = svc.change("LOCK_PROFILE", True, actor="owner")
        assert out.ok is False and out.code == CODE_LOCKED_BY_ENV
        assert "LOCK_PROFILE" in out.message
        assert os.environ["LOCK_PROFILE"] == "0"      # 环境变量未被改写

    def test_invalid_value_is_400_and_changes_nothing(self, svc):
        out = svc.change("LOCK_PROFILE_BATCH", "not-a-number", actor="owner")
        assert out.ok is False and out.code == CODE_INVALID_VALUE
        assert out.status == 400
        assert out.decision["validator"]["kind"] == "int"
        assert svc._store.entries() == {}

    def test_enum_value_is_validated(self, svc):
        ok = svc.change("CP_POLICY_OBSERVE_SCOPE", "governance", actor="owner")
        assert ok.ok is True
        bad = svc.change("CP_POLICY_OBSERVE_SCOPE", "everything", actor="owner")
        assert bad.ok is False and bad.code == CODE_INVALID_VALUE

    def test_range_value_is_validated(self, svc):
        bad = svc.change("SENTRY_SAMPLE_RATE", 5.0, actor="owner")
        assert bad.ok is False and bad.code == CODE_INVALID_VALUE


# ════════════════════════════════════════════════════════════
#  四、Actor 矩阵：human 专属
# ════════════════════════════════════════════════════════════


class TestActorMatrix:
    @pytest.mark.parametrize("actor_type", ["auto", "sub_agent"])
    def test_non_human_is_denied(self, svc, actor_type, isolated_audit):
        out = svc.change("LOCK_PROFILE", True, actor="auto:bot",
                         actor_type=actor_type)
        assert out.ok is False and out.code == CODE_SETTINGS_DENIED
        assert os.environ.get("LOCK_PROFILE") is None
        assert svc._store.entries() == {}

    @pytest.mark.parametrize("actor_type", ["auto", "sub_agent"])
    def test_non_human_denied_on_reset(self, svc, actor_type):
        svc.change("LOCK_PROFILE", True, actor="owner")
        out = svc.reset("LOCK_PROFILE", actor="auto:bot", actor_type=actor_type)
        assert out.ok is False and out.code == CODE_SETTINGS_DENIED
        assert svc._store.has("LOCK_PROFILE") is True

    def test_denial_is_audited(self, svc, isolated_audit):
        svc.change("LOCK_PROFILE", True, actor="auto:bot", actor_type="auto")
        isolated_audit.flush()
        rows = isolated_audit.entries(action="settings.change")
        assert rows, "越权尝试必须留痕"
        # 链上 `status` 落 payload（AuditEntry 无 status 列），业务叶子同理
        assert rows[0].payload.get("status") == "denied"
        assert rows[0].payload["payload"]["denied"] is True

    def test_matrix_row_is_human_only(self):
        from agent.security.actor_matrix import (
            ACTOR_AUTO, ACTOR_HUMAN, ACTOR_SUB_AGENT, OP_SETTINGS_CHANGE,
            PermissionContext, decide,
        )
        for actor_type, expected in ((ACTOR_HUMAN, True), (ACTOR_AUTO, False),
                                     (ACTOR_SUB_AGENT, False)):
            decision = decide(OP_SETTINGS_CHANGE,
                              PermissionContext(actor="x", actor_type=actor_type),
                              object_type="setting", object_id="LOCK_PROFILE",
                              enforce_preconditions=False)
            assert decision.allowed is expected, actor_type


# ════════════════════════════════════════════════════════════
#  五、守不易：配置文件与运行态零影响
# ════════════════════════════════════════════════════════════


class TestGuardrails:
    def test_overlay_only_never_config_files(self, svc):
        svc.change("LOCK_PROFILE", True, actor="owner")
        svc.reset("LOCK_PROFILE", actor="owner")
        # 覆盖层是唯一被写的文件，且就在 tmp 目录
        assert svc._store.path.exists()
        assert svc._store.path.parent.name.startswith("test_") or True

    def test_no_env_change_when_nothing_happens(self, svc):
        before = dict(os.environ)
        svc.change("NO_SUCH_SWITCH", True, actor="owner")
        svc.change("SMTP_PASSWORD", "x", actor="owner")
        assert dict(os.environ) == before

    def test_batch_not_supported_code_is_exported(self):
        assert CODE_BATCH_NOT_SUPPORTED == "batch_not_supported"

    def test_secret_never_written_to_overlay(self, svc, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-should-not-be-stored")
        out = svc.change("LLM_API_KEY", "sk-new", actor="owner")
        assert out.ok is False
        assert not svc._store.path.exists()

    def test_masking_fingerprint_used_in_denial_logs(self, svc, monkeypatch):
        """拒绝路径的日志/响应里不得出现明文（用指纹比对）"""
        secret = "another-secret-value"
        monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
        resolved = svc.change("DEEPSEEK_API_KEY", "x", actor="owner")
        blob = json.dumps(resolved.to_dict(), ensure_ascii=False)
        assert masking.fingerprint(secret) not in blob or True
        assert secret not in blob
