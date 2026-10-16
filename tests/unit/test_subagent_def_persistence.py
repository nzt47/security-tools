# -*- coding: utf-8 -*-
"""分身定义持久化 + 按需恢复（2026-10-17，P3）。

钉住：显式创建的永久分身（ttl=0）落盘后重启可恢复；destroy 后不再复活；
非法/多余字段被安全处理；已存在的同名容器不重复创建。
"""
from __future__ import annotations

import pytest

from agent.subagent import persistence as pd
from agent.subagent.lifecycle import SubagentLifecycleManager


@pytest.fixture(autouse=True)
def _tmp_defs(monkeypatch, tmp_path):
    monkeypatch.setattr(pd, "DEFS_DIR", tmp_path / "subagents")
    yield


class TestRoundTrip:
    def test_persist_load_remove(self):
        cfg = {"name": "engineering", "model_id": "", "memory_provider": "holographic"}
        assert pd.persist_definition(cfg) is True
        assert pd.load_definition("engineering") == cfg
        assert pd.load_definitions() == [cfg]
        assert pd.remove_definition("engineering") is True
        assert pd.load_definition("engineering") is None
        assert pd.load_definitions() == []

    def test_missing_name_rejected(self):
        assert pd.persist_definition({"model_id": "x"}) is False
        assert pd.load_definitions() == []

    def test_broken_file_skipped(self):
        pd.DEFS_DIR.mkdir(parents=True, exist_ok=True)
        (pd.DEFS_DIR / "broken.json").write_text("{not json", encoding="utf-8")
        assert pd.load_definitions() == []

    def test_no_tmp_left_behind(self):
        pd.persist_definition({"name": "a", "model_id": ""})
        assert list(pd.DEFS_DIR.glob("*.tmp")) == []


class TestRestore:
    def test_restore_all_and_skip_existing(self):
        pd.persist_definition({"name": "a", "model_id": "", "memory_provider": "holographic"})
        pd.persist_definition({"name": "b", "model_id": "", "memory_provider": "holographic"})
        mgr = SubagentLifecycleManager()
        restored = pd.restore_into(mgr)
        assert sorted(c.config.name for c in restored) == ["a", "b"]
        assert mgr.get("a") is not None
        assert pd.restore_into(mgr) == []

    def test_restore_one_by_name(self):
        pd.persist_definition({"name": "x", "model_id": "", "memory_provider": "holographic"})
        pd.persist_definition({"name": "y", "model_id": "", "memory_provider": "holographic"})
        mgr = SubagentLifecycleManager()
        assert [c.config.name for c in pd.restore_into(mgr, "x")] == ["x"]
        assert mgr.get("y") is None

    def test_unknown_fields_are_dropped(self):
        pd.persist_definition({"name": "ok", "model_id": "", "memory_provider": "holographic",
                               "bogus_field": 1})
        mgr = SubagentLifecycleManager()
        assert [c.config.name for c in pd.restore_into(mgr)] == ["ok"]

    def test_missing_required_field_is_skipped_not_raised(self):
        pd.persist_definition({"name": "bad"})
        mgr = SubagentLifecycleManager()
        assert pd.restore_into(mgr) == []
        assert mgr.get("bad") is None


class _FakeOrch:
    """只带 SubagentManager 需要的两个属性（裸对象，不带 Flask/编排器）。"""

    def __init__(self, mgr, persist):
        self._subagent_mgr = mgr
        self._config = {"subagent": {"persist_definitions": persist}}


class TestManagerWiring:
    """真正的生产路径（orchestrator.subagent_manager）启用了定义持久化。"""

    def _mgr(self, persist):
        from agent.orchestrator.subagent_manager import SubagentManager
        return SubagentManager(_FakeOrch(SubagentLifecycleManager(), persist))

    def test_create_persists_permanent_definition(self):
        self._mgr(True).create({"name": "keepme", "model_id": "",
                                "memory_provider": "holographic", "ttl_seconds": 0})
        assert (pd.load_definition("keepme") or {}).get("name") == "keepme"

    def test_create_ttl_positive_is_not_persisted(self):
        self._mgr(True).create({"name": "temp", "model_id": "",
                                "memory_provider": "holographic", "ttl_seconds": 60})
        assert pd.load_definition("temp") is None

    def test_create_disabled_does_not_persist(self):
        self._mgr(False).create({"name": "nokeep", "model_id": "",
                                 "memory_provider": "holographic"})
        assert pd.load_definition("nokeep") is None

    def test_destroy_removes_definition(self):
        sm = self._mgr(True)
        sm.create({"name": "gone", "model_id": "", "memory_provider": "holographic"})
        assert pd.load_definition("gone") is not None
        sm.destroy("gone")
        assert pd.load_definition("gone") is None


class TestConfigOverrides:
    """config.yaml → Config(overrides=) 的接线（P3 真机 bug 的回归守卫）。"""

    def test_reads_subagent_block(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text("subagent:\n  persist_definitions: true\n  max_subagents: 7\n", encoding="utf-8")
        assert pd.load_subagent_config_overrides(p) == {
            "subagent": {"persist_definitions": True, "max_subagents": 7}
        }

    def test_missing_or_non_dict_returns_empty(self, tmp_path):
        assert pd.load_subagent_config_overrides(tmp_path / "nope.yaml") == {}
        bad = tmp_path / "bad.yaml"
        bad.write_text("subagent: [1, 2]\n", encoding="utf-8")
        assert pd.load_subagent_config_overrides(bad) == {}

    def test_real_repo_config_enables_persistence(self):
        # 仓库自带的 config.yaml 必须开着 persist_definitions（否则真机又静默失效）
        ov = pd.load_subagent_config_overrides()
        assert ov.get("subagent", {}).get("persist_definitions") is True

    def test_config_retains_subagent_through_validation(self):
        # Config() 不解析 YAML，但 overrides 会被 _merge 保留（验证一下，防止再退回静默失效）
        from config import Config
        merged = Config(overrides=pd.load_subagent_config_overrides()).merged
        assert merged["subagent"]["persist_definitions"] is True

