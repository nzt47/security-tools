"""技能清理调度器单元测试。

覆盖：
    - _cfg 默认值（关闭 + dry-run 保守）
    - register/unregister（默认 disabled 不注册）
    - run_cleanup_once 返回结构（注入隔离服务）
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from agent.skills_mgmt import cleanup_scheduler as mod


class TestCleanupSchedulerConfig:
    def test_default_cfg_conservative(self):
        cfg = mod._cfg()
        # 默认关闭 + dry-run 保守（不自动删任何东西）
        assert cfg["enabled"] is False
        assert cfg["orphans_dry_run"] is True
        assert cfg["unused_dry_run"] is True

    def test_register_disabled_returns_not_registered(self):
        r = mod.register_cleanup_schedulers()
        assert r.get("registered") is False
        assert r.get("reason") == "disabled"

    def test_unregister_noop_when_none(self):
        r = mod.unregister_cleanup_schedulers()
        assert r.get("ok") is True

    def test_run_cleanup_once_shape(self, monkeypatch):
        """run_cleanup_once 返回含 orphans/unused 结构的报告。"""
        from agent.skills_mgmt import SkillsMgmtService

        class _FakeSvc:
            def __init__(self):
                self._inner = SkillsMgmtService(
                    store_path=str(pathlib.Path(__file__).parent /
                                   "nonexistent_tmp_skills.json"))

            def cleanup_orphans(self, *, dry_run=True):
                return {"dry_run": dry_run, "found": 0, "orphans": [],
                        "cleaned": []}

            def cleanup_unused(self, *, dry_run=True, **kw):
                return {"dry_run": dry_run, "found": 0, "candidates": [],
                        "removed": []}

        import agent.skills_mgmt.cleanup_scheduler as cs

        def _fake_get():
            return _FakeSvc()

        monkeypatch.setattr(cs, "_cfg", lambda: {
            "enabled": False, "interval_hours": 24,
            "orphans_dry_run": True, "unused_dry_run": True,
            "unused_days": 90, "archived_days": 180})
        monkeypatch.setattr(
            "agent.state_manager.get_skills_mgmt_service", _fake_get)
        r = cs.run_cleanup_once()
        assert r["ok"] is True
        assert "orphans" in r and "unused" in r
        assert r["orphans"]["found"] == 0
