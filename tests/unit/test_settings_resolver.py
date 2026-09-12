"""TASK-S7-01 生效来源解析与覆盖层单测（**不谎报**）

【守护的四件事】
    1. 四层优先级 `env > ui_override > config > default` 逐层可证；
    2. 被 env 锁定的项**必须**置灰并给出可读原因（"UI 改了但没生效"是头号禁忌）；
    3. C 级（密钥/端点/路径）**响应体里不含明文**（只出指纹与是否已配置）；
    4. 覆盖层写入**绝不触碰** `.env` / `config.yaml`（用例断言文件未被改动），
       且"未发生变更时对运行态零影响"。
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from agent.settings import masking
from agent.settings import registry as R
from agent.settings import resolver as RS
from agent.settings.bootstrap import apply_overrides, reset_bootstrap_state
from agent.settings.overrides import OverrideStore, reset_override_store

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: 受保护配置文件（**必须逐字节不变**）
PROTECTED = (REPO_ROOT / ".env", REPO_ROOT / "config.yaml")


def _digest(path: pathlib.Path) -> str:
    if not path.exists():                                # pragma: no cover
        return "<absent>"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_snapshot():
    return {str(p): _digest(p) for p in PROTECTED}


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """覆盖层落 tmp + 环境变量快照还原 + 单例复位（防跨用例污染）"""
    import os
    snapshot = dict(os.environ)
    monkeypatch.setenv("CP_UI_SETTINGS_PATH", str(tmp_path / "ui_settings.json"))
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    reset_override_store()
    reset_bootstrap_state()
    before = _protected_snapshot()
    yield tmp_path
    os.environ.clear()
    os.environ.update(snapshot)
    reset_override_store()
    reset_bootstrap_state()
    after = _protected_snapshot()
    assert after == before, "受保护配置文件被改动（.env / config.yaml）"


@pytest.fixture()
def store(tmp_path):
    reset_override_store()
    return OverrideStore(tmp_path / "ui_settings.json")


# ════════════════════════════════════════════════════════════
#  一、四层优先级
# ════════════════════════════════════════════════════════════


class TestSourcePriority:
    def test_default_source_when_nothing_configured(self, store, monkeypatch):
        monkeypatch.delenv("LOCK_PROFILE", raising=False)
        resolved = RS.resolve("LOCK_PROFILE", store=store)
        assert resolved is not None
        assert resolved.source == RS.SOURCE_DEFAULT
        assert resolved.value is False          # 声明默认值
        assert resolved.override_present is False

    def test_ui_override_beats_default(self, store, monkeypatch):
        monkeypatch.delenv("LOCK_PROFILE", raising=False)
        store.set("LOCK_PROFILE", True, actor="owner", risk="A")
        resolved = RS.resolve("LOCK_PROFILE", store=store)
        assert resolved.source == RS.SOURCE_OVERRIDE
        assert resolved.value is True
        assert resolved.override_present is True

    def test_env_beats_ui_override_and_reports_shadow(self, store, monkeypatch):
        """★ 被 env 锁定：source=env，覆盖层被**如实**标注为被遮蔽"""
        monkeypatch.setenv("LOCK_PROFILE", "0")
        store.set("LOCK_PROFILE", True, actor="owner", risk="A")
        resolved = RS.resolve("LOCK_PROFILE", store=store)
        assert resolved.source == RS.SOURCE_ENV
        assert resolved.value is False                     # env 胜
        assert RS.SOURCE_OVERRIDE in resolved.shadowed_by
        assert resolved.env_locked is True
        assert resolved.editable is False
        assert "LOCK_PROFILE" in resolved.locked_reason
        assert "不会生效" in resolved.locked_reason

    def test_config_runtime_beats_default(self, store, monkeypatch):
        """ObservabilityConfig 运行态改过值 → source=config（热生效）"""
        from agent.monitoring.observability_config import (
            get_observability_config, reset_observability_config,
        )
        reset_observability_config()
        cfg = get_observability_config()
        path = "resource_monitor.sample_interval_sec"
        spec = R.spec_for_config(path)
        assert spec is not None
        original = cfg.get(path)
        try:
            cfg.set(path, int(original) + 7 if isinstance(original, int) else 7)
            resolved = RS.resolve(spec.key, store=store)
            assert resolved.source == RS.SOURCE_CONFIG
            assert resolved.value != spec.default
        finally:
            cfg.set(path, original)
            reset_observability_config()

    def test_override_beats_config_and_shadows_it(self, store, monkeypatch):
        from agent.monitoring.observability_config import (
            get_observability_config, reset_observability_config,
        )
        reset_observability_config()
        cfg = get_observability_config()
        path = "resource_monitor.history_size"
        spec = R.spec_for_config(path)
        assert spec is not None
        original = cfg.get(path)
        try:
            cfg.set(path, 111)
            store.set(spec.key, 222, actor="owner", risk="A")
            resolved = RS.resolve(spec.key, store=store)
            assert resolved.source == RS.SOURCE_OVERRIDE
            assert resolved.value == 222
            assert RS.SOURCE_CONFIG in resolved.shadowed_by
        finally:
            cfg.set(path, original)
            reset_observability_config()

    def test_env_wins_over_config_for_same_key(self, store, monkeypatch):
        monkeypatch.setenv("TRACING_SAMPLER_RATIO", "0.9")
        resolved = RS.resolve("TRACING_SAMPLER_RATIO", store=store)
        assert resolved.source == RS.SOURCE_ENV
        assert resolved.value == 0.9
        assert resolved.editable is False

    def test_priority_declaration_is_stable(self):
        assert RS.SOURCE_PRIORITY == ("env", "ui_override", "config", "default")
        assert set(RS.SOURCE_LABELS) == set(RS.SOURCE_PRIORITY)


# ════════════════════════════════════════════════════════════
#  二、可改性与置灰说明
# ════════════════════════════════════════════════════════════


class TestEditabilityAndLocking:
    def test_env_locked_item_is_greyed_with_reason(self, store, monkeypatch):
        monkeypatch.setenv("SKILL_RERANKER_ENABLED", "1")
        resolved = RS.resolve("SKILL_RERANKER_ENABLED", store=store)
        pub = resolved.to_public_dict()
        assert pub["editable"] is False
        assert pub["locked"] is True
        assert pub["env_locked"] is True
        assert pub["locked_reason"]

    def test_c_level_is_read_only(self, store):
        resolved = RS.resolve("REDIS_URL", store=store)
        assert resolved.editable is False
        assert resolved.locked_reason

    def test_dynamic_family_is_read_only(self, store):
        spec = next(s for s in R.all_specs() if s.dynamic_prefix)
        resolved = RS.resolve(spec.key, store=store)
        assert resolved is not None
        assert resolved.editable is False
        assert "动态" in resolved.locked_reason

    def test_config_only_item_is_locked_with_config_yaml_reason(
            self, store, monkeypatch):
        """仅存在于 config.yaml 的项：**守不易不该改**，必须置灰并说明"""
        synthetic = R.SettingSpec(
            key="PLANNING_MAX_DEPTH", category=R.CAT_ORCHESTRATION, type="int",
            default=5, description="规划最大深度（仅 config.yaml）",
            env_name="", config_path="planning.max_depth",
            owner_module="agent/orchestrator/orchestrator.py")
        monkeypatch.setattr(RS, "get_spec", lambda key: synthetic)
        resolved = RS.resolve("PLANNING_MAX_DEPTH", store=store)
        assert resolved is not None
        assert resolved.editable is False
        assert "config.yaml" in resolved.locked_reason
        assert "不修改配置文件" in resolved.locked_reason

    def test_a_level_env_item_is_editable(self, store, monkeypatch):
        monkeypatch.delenv("LOCK_PROFILE_BATCH", raising=False)
        resolved = RS.resolve("LOCK_PROFILE_BATCH", store=store)
        assert resolved.editable is True
        assert resolved.locked_reason == ""

    def test_env_only_flag_surfaces_on_public_dict(self, store):
        pub = RS.resolve("LOCK_PROFILE", store=store).to_public_dict()
        assert pub["env_only"] is True
        assert pub["config_path"] == ""


# ════════════════════════════════════════════════════════════
#  三、C 级脱敏（永不返回明文）
# ════════════════════════════════════════════════════════════


class TestSecretMasking:
    def test_secret_value_never_in_public_dict(self, store, monkeypatch):
        secret = "sk-live-9f8e7d6c5b4a3210"
        monkeypatch.setenv("LLM_API_KEY", secret)
        resolved = RS.resolve("LLM_API_KEY", store=store)
        pub = resolved.to_public_dict()
        assert pub["value"] is None
        assert pub["masked"] is True
        assert pub["configured"] is True
        assert secret not in json.dumps(pub, ensure_ascii=False)
        assert pub["fingerprint"] == masking.fingerprint(secret)

    def test_url_and_path_are_masked_too(self, store, monkeypatch):
        monkeypatch.setenv("LOKI_URL", "http://internal-loki.corp:3100")
        pub = RS.resolve("LOKI_URL", store=store).to_public_dict()
        assert "internal-loki" not in json.dumps(pub, ensure_ascii=False)
        assert pub["configured"] is True

    def test_unconfigured_secret_reports_not_configured(self, store, monkeypatch):
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        pub = RS.resolve("SENTRY_DSN", store=store).to_public_dict()
        assert pub["configured"] is False
        assert pub["display_value"] == "未配置"
        assert pub["fingerprint"] == ""

    def test_audit_leaves_carry_fingerprint_only(self, store, monkeypatch):
        secret = "top-secret-token-value"
        monkeypatch.setenv("SMTP_PASSWORD", secret)
        resolved = RS.resolve("SMTP_PASSWORD", store=store)
        leaves = resolved.to_audit_leaves()
        blob = json.dumps(leaves, ensure_ascii=False)
        assert secret not in blob
        assert leaves["old_fingerprint"] == masking.fingerprint(secret)
        assert leaves["configured"] is True

    def test_assert_no_plaintext_guard_raises(self):
        with pytest.raises(AssertionError):
            masking.assert_no_plaintext({"a": "leaked-secret"}, "leaked-secret")
        # 守卫自身**不得**把明文写进异常信息
        try:
            masking.assert_no_plaintext({"a": "leaked-secret"}, "leaked-secret")
        except AssertionError as e:
            assert "leaked-secret" not in str(e)

    def test_short_values_do_not_trip_guard(self):
        masking.assert_no_plaintext({"a": "0"}, "0")     # 过短不参与匹配


# ════════════════════════════════════════════════════════════
#  四、覆盖层：守不易 + 原子写 + 零影响
# ════════════════════════════════════════════════════════════


class TestOverrideStore:
    def test_write_then_read_back(self, store):
        store.set("LOCK_PROFILE", True, actor="owner", risk="A", reason="test")
        again = OverrideStore(store.path)
        rec = again.get("LOCK_PROFILE")
        assert rec is not None and rec.value is True
        assert rec.actor == "owner"
        assert rec.updated_at

    def test_clear_removes_record(self, store):
        store.set("LOCK_PROFILE", True, actor="owner")
        assert store.clear("LOCK_PROFILE") is not None
        assert store.has("LOCK_PROFILE") is False
        assert store.clear("LOCK_PROFILE") is None

    def test_file_is_json_with_schema_version(self, store):
        store.set("LOCK_PROFILE", True, actor="owner")
        payload = json.loads(store.path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
        assert "LOCK_PROFILE" in payload["overrides"]
        assert "config.yaml" in payload["note"]

    def test_atomic_write_leaves_no_tmp_file(self, store):
        store.set("LOCK_PROFILE", True, actor="owner")
        leftovers = list(store.path.parent.glob("*.tmp"))
        assert leftovers == []

    def test_refuses_to_write_protected_files(self, tmp_path):
        for name in (".env", "config.yaml"):
            with pytest.raises(ValueError):
                OverrideStore(tmp_path / name)
            with pytest.raises(ValueError):
                OverrideStore(REPO_ROOT / name)

    def test_never_touches_env_and_config_yaml(self, store):
        before = _protected_snapshot()
        store.set("LOCK_PROFILE", True, actor="owner")
        store.clear("LOCK_PROFILE")
        assert _protected_snapshot() == before

    def test_corrupt_overlay_degrades_to_empty(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        store = OverrideStore(path)
        assert store.entries() == {}


class TestBootstrapZeroImpact:
    def test_noop_without_overlay(self, monkeypatch):
        """★ 没有覆盖层时**对运行态零影响**（未变更即零影响）"""
        import os
        before = dict(os.environ)
        result = apply_overrides()
        assert result["existed"] is False
        assert result["applied"] == []
        assert dict(os.environ) == before

    def test_applies_overlay_to_process_env(self, store, monkeypatch):
        import os
        monkeypatch.delenv("LOCK_PROFILE", raising=False)
        store.set("LOCK_PROFILE", True, actor="owner", risk="A")
        reset_bootstrap_state()
        result = apply_overrides(force=True)
        assert any(entry["key"] == "LOCK_PROFILE" for entry in result["applied"])
        assert os.environ.get("LOCK_PROFILE") == "true"
        # 幂等：再调一次不重复应用
        reset_bootstrap_state()
        again = apply_overrides()
        assert again["applied"] or again.get("note")

    def test_env_locked_override_is_skipped_at_bootstrap(self, store, monkeypatch):
        monkeypatch.setenv("LOCK_PROFILE", "0")
        store.set("LOCK_PROFILE", True, actor="owner", risk="A")
        reset_bootstrap_state()
        result = apply_overrides(force=True)
        assert any("LOCK_PROFILE" == item.get("key") for item in result["skipped"])
        assert all(entry.get("key") != "LOCK_PROFILE" for entry in result["applied"])

    def test_overlay_exists_reflects_file_presence(self, store):
        from agent.settings.bootstrap import overlay_exists
        assert overlay_exists() is False
        store.set("LOCK_PROFILE", True, actor="owner")
        assert overlay_exists() is True

    def test_apply_is_idempotent_in_one_process(self, store, monkeypatch):
        """同一进程重复调用只应用一次（幂等；避免反复写 env）"""
        import os
        monkeypatch.delenv("LOCK_PROFILE", raising=False)
        store.set("LOCK_PROFILE", True, actor="owner", risk="A")
        reset_bootstrap_state()
        first = apply_overrides()
        assert first["applied"]
        os.environ["LOCK_PROFILE"] = "false"          # 人为篡改，验证第二次不再覆盖
        second = apply_overrides()
        assert second["applied"] == []
        assert "已应用过" in second.get("note", "")
        assert os.environ["LOCK_PROFILE"] == "false"

    def test_unknown_key_in_overlay_is_skipped_with_reason(self, tmp_path, monkeypatch):
        """覆盖层里出现未登记键 → fail-closed 跳过，并如实给出原因"""
        path = tmp_path / "ui_settings.json"
        path.write_text(json.dumps({"schema_version": 1, "overrides": {
            "NOT_A_REGISTERED_SWITCH": {"value": True, "actor": "x"}}}),
            encoding="utf-8")
        reset_override_store()
        reset_bootstrap_state()
        result = apply_overrides(force=True)
        assert result["applied"] == []
        assert result["skipped"] and "未登记" in result["skipped"][0]["reason"]

    def test_overlay_env_summary_lists_applied_envs(self, monkeypatch):
        """`overlay_env_summary()` 走**单例** store（故本用例也走单例）"""
        from agent.settings.bootstrap import overlay_env_summary
        from agent.settings.overrides import get_override_store
        import os
        monkeypatch.delenv("LOCK_PROFILE", raising=False)
        singleton = get_override_store()
        singleton.set("LOCK_PROFILE", True, actor="owner", risk="A")
        resolved = RS.resolve("LOCK_PROFILE", store=singleton)
        RS.apply_override_to_runtime(resolved, store=singleton)
        summary = overlay_env_summary()
        names = {row["env_name"] for row in summary}
        assert "LOCK_PROFILE" in names                  # 只出 env 名，不出值
        assert all("value" not in row for row in summary)
        assert os.environ["LOCK_PROFILE"] == "true"


class TestRuntimeLanding:
    def test_apply_sets_env_and_restore_removes_it(self, store, monkeypatch):
        import os
        monkeypatch.delenv("LOCK_PROFILE", raising=False)
        store.set("LOCK_PROFILE", True, actor="owner", risk="A")
        resolved = RS.resolve("LOCK_PROFILE", store=store)
        landing = RS.apply_override_to_runtime(resolved, store=store)
        assert landing["applied"] is True
        assert os.environ["LOCK_PROFILE"] == "true"
        restore = RS.restore_runtime("LOCK_PROFILE", store=store)
        assert restore["applied"] is True
        assert "LOCK_PROFILE" not in os.environ

    def test_bool_env_text_uses_true_false(self, store, monkeypatch):
        monkeypatch.delenv("LOCK_WATCHDOG_ENABLED", raising=False)
        store.set("LOCK_WATCHDOG_ENABLED", False, actor="owner", risk="A")
        resolved = RS.resolve("LOCK_WATCHDOG_ENABLED", store=store)
        RS.apply_override_to_runtime(resolved, store=store)
        import os
        assert os.environ["LOCK_WATCHDOG_ENABLED"] == "false"

    def test_needs_restart_item_is_not_hot_applied(self, store, monkeypatch):
        monkeypatch.delenv("PLANNING_WIRE_ENABLED", raising=False)
        store.set("PLANNING_WIRE_ENABLED", True, actor="owner", risk="A")
        resolved = RS.resolve("PLANNING_WIRE_ENABLED", store=store)
        landing = RS.apply_override_to_runtime(resolved, store=store)
        assert landing["applied"] is False
        assert "needs_restart" in landing["detail"]

    def test_resolve_all_covers_registry(self, store):
        resolved = RS.resolve_all(store=store)
        assert len(resolved) == len(R.all_specs())
        assert RS.resolve("NO_SUCH_KEY", store=store) is None


class TestOverrideCountConsistency:
    """`counts.overridden` 与前端"只看已覆盖"筛选**必须同义**

    Why：前端芯片数字取自后端 `counts.overridden`（= `override_present` 计数），
    而行筛选用的是 `source == 'ui_override' || 'ui_override' in shadowed_by`。
    两者若不同义，就会出现"芯片说 3 项、筛出来 2 行"的面板说谎——前端实现者把这条
    列为最需要复核的一处。本用例把等价关系**逐场景钉死**，而不是靠推理。
    """

    @staticmethod
    def _frontend_predicate(resolved) -> bool:
        pub = resolved.to_public_dict()
        return (pub["source"] == "ui_override"
                or "ui_override" in pub["shadowed_by"])

    def test_equivalence_across_all_scenarios(self, store, monkeypatch):
        # 场景 1：无覆盖、无 env
        monkeypatch.delenv("LOCK_PROFILE", raising=False)
        r1 = RS.resolve("LOCK_PROFILE", store=store)
        assert r1.override_present is False
        assert self._frontend_predicate(r1) is False

        # 场景 2：仅覆盖（覆盖生效）
        store.set("LOCK_PROFILE", True, actor="owner", risk="A")
        r2 = RS.resolve("LOCK_PROFILE", store=store)
        assert r2.override_present is True and r2.source == RS.SOURCE_OVERRIDE
        assert self._frontend_predicate(r2) is True

        # 场景 3：覆盖存在但被 env 遮蔽（前端必须仍算作"已覆盖"）
        monkeypatch.setenv("LOCK_PROFILE", "0")
        r3 = RS.resolve("LOCK_PROFILE", store=store)
        assert r3.override_present is True and r3.source == RS.SOURCE_ENV
        assert "ui_override" in r3.shadowed_by
        assert self._frontend_predicate(r3) is True

    def test_counts_overridden_equals_frontend_filter_size(self, store, monkeypatch):
        """整表口径：`override_present` 计数 == 前端谓词命中数（含被 env 遮蔽的一例）"""
        monkeypatch.delenv("LOCK_PROFILE", raising=False)
        monkeypatch.delenv("LOCK_PROFILE_BATCH", raising=False)
        store.set("LOCK_PROFILE", True, actor="owner", risk="A")
        monkeypatch.setenv("LOCK_PROFILE_BATCH", "1")     # 第二个覆盖被 env 遮蔽
        store.set("LOCK_PROFILE_BATCH", 9, actor="owner", risk="A")
        resolved = RS.resolve_all(store=store)
        backend_count = sum(1 for r in resolved if r.override_present)
        front_count = sum(1 for r in resolved if self._frontend_predicate(r))
        assert backend_count == front_count == 2
