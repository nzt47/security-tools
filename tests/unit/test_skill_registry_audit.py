"""D2 单测：技能注册表启停入审计链（真实读写链路，不 mock 审计）。

覆盖：
    1. `SkillRegistry.set_enabled` 主轨分支 → 审计链出现 `skill.registry.set_enabled`
    2. `SkillRegistry.set_enabled` 文件轨分支 → 同上，payload.track = "file_track"
    3. `SkillRegistry.toggle`（主轨 / 文件轨）→ `skill.registry.toggle`
    4. 绕过 SkillRegistry 直调 service（`/api/skills-mgmt/<id>/toggle` 的路径）也留痕
    5. 审计可用/不可用时启停返回值完全一致（不改变既有返回结构）
    6. 审计写入失败不影响启停本身，但必须留 WARNING 日志（不静默）
    7. 未知技能不产生审计记录；payload 不含技能描述等用户原文

审计隔离：`tests/conftest.py::_isolate_approval_stores`（会话级 autouse）已把
`AUDIT_DB_PATH` / 门面单例指向会话级临时目录，故本文件读回的是临时链，
不触碰生产 `data/audit/audit_chain.db`。

断言读的是审计链里真实存在的记录（`chain.entries(action=...)`），
不对 `audit.record` 做任何 mock —— 否则无法发现"best-effort 静默吞掉"。

载荷结构：`AuditFacade.record` 会把业务载荷嵌在 `entry.payload["payload"]` 下
（同层还有 status / actor_source / schema，实测自 `facade.py:309-322`），
故断言走 `_body()` 取业务字段。
"""

import json
import logging

import pytest

from agent.skills_mgmt import SkillsMgmtService
from agent.skills_mgmt.registry import SkillRegistry

ACTION_SET = "skill.registry.set_enabled"
ACTION_TOGGLE = "skill.registry.toggle"


@pytest.fixture
def iso_svc(tmp_path):
    """隔离的技能服务（主轨 JSON + 文件轨 repo 都在 tmp 下）"""
    return SkillsMgmtService(
        store_path=str(tmp_path / "skills_mgmt.json"),
        repo_path=str(tmp_path / "skills_repo"),
    )


@pytest.fixture
def reg(iso_svc):
    return SkillRegistry(service=iso_svc)


def _chain():
    """真实审计链实例（门面已由 conftest 隔离到临时目录）"""
    from agent.audit import audit as facade
    chain = facade.chain
    assert chain is not None, "审计链不可用：门面被关闭或未初始化"
    return chain


def _records(skill_id: str, action: str):
    """从审计链读回指定技能的真实记录（seq 升序）"""
    subject = f"skill:{skill_id}"
    return [e for e in _chain().entries(action=action) if e.subject == subject]


def _body(entry) -> dict:
    """审计条目的业务载荷（facade 嵌一层 `payload`）"""
    inner = entry.payload.get("payload")
    return inner if isinstance(inner, dict) else entry.payload


def _make_main(iso_svc, skill_id: str, **extra):
    payload = {"id": skill_id, "name": skill_id, "content": "# x",
               "content_type": "markdown"}
    payload.update(extra)
    return iso_svc.create_manual(payload)


def _make_file(iso_svc, skill_id: str, *, enabled=True, **extra):
    meta = {"id": skill_id, "enabled": enabled, "status": "approved"}
    meta.update(extra)
    return iso_svc.file_store.create(skill_id, meta=meta, instruction="# x")


class TestMainTrackAudit:
    def test_set_enabled_main_track_lands_in_chain(self, reg, iso_svc):
        _make_main(iso_svc, "d2-main-1")
        r = reg.set_enabled("d2-main-1", False)
        assert r == {"ok": True, "id": "d2-main-1", "enabled": False,
                     "track": "main"}
        recs = _records("d2-main-1", ACTION_SET)
        assert len(recs) == 1, f"期望恰好 1 条审计记录，实得 {len(recs)}"
        e = recs[0]
        assert e.action == ACTION_SET
        assert e.subject == "skill:d2-main-1"
        assert e.source == "agent"
        assert e.actor, "actor 不应为空（门面会解析 UI/Agent 上下文或回落到 system）"
        assert e.ts
        assert e.payload["status"] == "ok"
        body = _body(e)
        assert body["skill_id"] == "d2-main-1"
        assert body["previous_enabled"] is True
        assert body["enabled"] is False
        assert body["track"] == "main"
        assert body["origin"] == "skill_registry.set_enabled"

    def test_toggle_main_track_records_toggle_action(self, reg, iso_svc):
        _make_main(iso_svc, "d2-toggle-main-1")
        r = reg.toggle("d2-toggle-main-1")
        assert r == {"ok": True, "id": "d2-toggle-main-1", "enabled": False,
                     "track": "main"}
        recs = _records("d2-toggle-main-1", ACTION_TOGGLE)
        assert len(recs) == 1
        body = _body(recs[0])
        assert body["track"] == "main"
        assert body["origin"] == "skill_registry.toggle"
        assert body["previous_enabled"] is True
        assert body["enabled"] is False
        # 同一次 toggle 只产生一条记录（主轨由 SkillEnhancer 落库点写出）
        assert _records("d2-toggle-main-1", ACTION_SET) == []

    def test_direct_service_call_is_audited(self, iso_svc):
        """绕过 SkillRegistry 的调用方（/api/skills-mgmt/<id>/toggle）同样留痕"""
        _make_main(iso_svc, "d2-direct-1")
        iso_svc.set_enabled("d2-direct-1", False)
        recs = _records("d2-direct-1", ACTION_SET)
        assert len(recs) == 1
        body = _body(recs[0])
        assert body["track"] == "main"
        assert body["origin"] == "skills_mgmt.enhancer"
        assert body["previous_enabled"] is True
        assert body["enabled"] is False


class TestFileTrackAudit:
    def test_set_enabled_file_track_lands_in_chain(self, reg, iso_svc):
        _make_file(iso_svc, "d2-file-1")
        r = reg.set_enabled("d2-file-1", False)
        assert r == {"ok": True, "id": "d2-file-1", "enabled": False,
                     "track": "file_track"}
        recs = _records("d2-file-1", ACTION_SET)
        assert len(recs) == 1
        body = _body(recs[0])
        assert body["skill_id"] == "d2-file-1"
        assert body["previous_enabled"] is True
        assert body["enabled"] is False
        assert body["track"] == "file_track"
        assert body["origin"] == "skill_registry.set_enabled"
        # 确实落到了 front matter
        assert iso_svc.file_store.get_metadata("d2-file-1")["enabled"] is False

    def test_toggle_file_track_records_toggle_action(self, reg, iso_svc):
        _make_file(iso_svc, "d2-toggle-file-1")
        r = reg.toggle("d2-toggle-file-1")
        assert r == {"ok": True, "id": "d2-toggle-file-1", "enabled": False,
                     "track": "file_track"}
        recs = _records("d2-toggle-file-1", ACTION_TOGGLE)
        assert len(recs) == 1
        body = _body(recs[0])
        assert body["track"] == "file_track"
        assert body["origin"] == "skill_registry.toggle"


class TestContractAndBestEffort:
    def test_return_contract_identical_with_and_without_audit(self, reg, iso_svc):
        from agent.audit import audit as facade

        _make_main(iso_svc, "d2-contract-on")
        _make_main(iso_svc, "d2-contract-off")
        r_on = reg.set_enabled("d2-contract-on", False)

        prev_enabled = facade.enabled
        facade.enabled = False
        try:
            r_off = reg.set_enabled("d2-contract-off", False)
        finally:
            facade.enabled = prev_enabled

        assert r_on == {"ok": True, "id": "d2-contract-on", "enabled": False,
                        "track": "main"}
        assert r_off == {"ok": True, "id": "d2-contract-off", "enabled": False,
                         "track": "main"}
        assert set(r_on) == set(r_off) == {"ok", "id", "enabled", "track"}
        # 审计关闭时确实没有记录（证明上面的断言针对的是真实写入）
        assert _records("d2-contract-off", ACTION_SET) == []
        assert iso_svc.get("d2-contract-off").enabled is False

    def test_unknown_skill_writes_no_record(self, reg):
        r = reg.set_enabled("d2-unknown-1", False)
        assert r == {"ok": False, "id": "d2-unknown-1",
                     "error": "未知技能: d2-unknown-1"}
        assert _records("d2-unknown-1", ACTION_SET) == []
        assert _records("d2-unknown-1", ACTION_TOGGLE) == []

    def test_audit_failure_does_not_break_set_enabled(
            self, reg, iso_svc, monkeypatch, caplog):
        from agent.audit import audit as facade

        _make_main(iso_svc, "d2-boom-1")

        def _boom(*a, **kw):
            raise RuntimeError("audit boom")

        monkeypatch.setattr(facade, "record", _boom)
        with caplog.at_level(logging.WARNING, logger="agent.skills_mgmt"):
            r = reg.set_enabled("d2-boom-1", False)

        assert r == {"ok": True, "id": "d2-boom-1", "enabled": False,
                     "track": "main"}
        assert reg.is_enabled("d2-boom-1") is False  # 启停真的生效了
        assert any("技能启停审计留痕失败" in rec.getMessage()
                   for rec in caplog.records), \
            "审计失败必须留 WARNING 日志，不能完全静默"

    def test_payload_contains_no_skill_text(self, reg, iso_svc):
        marker = "D2-SECRET-DESCRIPTION-9f3c"
        _make_main(iso_svc, "d2-secret-1", description=marker,
                   content=f"# {marker}")
        reg.set_enabled("d2-secret-1", False)
        recs = _records("d2-secret-1", ACTION_SET)
        assert len(recs) == 1
        dumped = json.dumps(recs[0].payload, ensure_ascii=False, sort_keys=True)
        assert marker not in dumped
        assert set(_body(recs[0])) == {"skill_id", "previous_enabled", "enabled",
                                       "track", "origin"}
