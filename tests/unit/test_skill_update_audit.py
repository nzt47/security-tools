"""D4 单测：PATCH /api/skills-mgmt/<id>（SkillsMgmtService.update）字段变更入审计链。

覆盖（断言全部**真读审计链**，不对 audit.record 做任何 mock）：
    1. 改**白名单字段**（本文件用 `tags`）→ 链上出现 `skill.update`，payload.changed 含该字段名
    2. **值不外泄**：传入的原文不出现在该技能的任何一条记录里（本卡核心隐私断言）
    2b. **【G1-B/M0 新契约】`description` 已被移出白名单 ⇒ 经 update 改它既不改值也不留痕**
        （见 `test_description_is_frozen_by_M0`；原用例拿 description 当见证字段，已按新契约换字段）
    3. 改 enabled → 链上出现 `skill.registry.set_enabled`（origin=skills_mgmt.update、
       track=main、previous_enabled/enabled），即复用启停动作族，不另开动作名
    4. 同一次 patch 同时改白名单字段 + enabled → 两个动作族各 1 条，字段变更族的 changed 不含 enabled
    5. 同值 patch / 非白名单键 / 空 patch → 0 条新记录
    6. 返回语义不变（返回 Skill 实例、与落库值一致；审计开/关两次调用返回值逐字段相等）
    7. 审计写入失败不影响 update 本身，且必须留 WARNING 日志（不完全静默）
    8. 反向对照：审计关闭时读回 0 条（证明上面的断言真的在读链，而不是恒真）

【为什么见证字段从 description 换成 tags（2026-09-26，主审计裁定）】
    D4 卡写作时 `description` 仍在 `SkillsMgmtService.update` 的白名单里。G1-B 的 M0
    （「先冻写路径再删数据」）按裁定把 `description` 移出白名单，技能的**唯一事实源**收敛为
    `data/skills_repo/<id>/skill.md` 的 front matter ⇒ 原来的 6 条用例拿一个**已冻结的字段**
    当见证，必然读到 0 条记录。
    **修法是把见证字段换成仍然可写的 `tags`，而不是放宽断言**；同时**新增**
    `test_description_is_frozen_by_M0` 把「冻结」这一新契约**钉住**（原用例的意图一条未减，
    覆盖面反而增加）。

审计隔离：tests/conftest.py::_isolate_approval_stores（会话级 autouse）把
AUDIT_DB_PATH 与门面单例指向会话临时目录；技能 store/repo 用 tmp_path 的 iso_svc 夹具
⇒ 本文件不写生产 data/。

载荷结构：AuditFacade.record 会把业务载荷嵌在 entry.payload["payload"] 下（同层还有
status / actor_source / schema，实测自 facade.py:309-322），故断言走 _body() 取业务字段。
"""

import json
import logging

import pytest

from agent.skills_mgmt import SkillsMgmtService
from agent.skills_mgmt.models import Skill

ACTION_UPDATE = "skill.update"
ACTION_SET_ENABLED = "skill.registry.set_enabled"
ORIGIN = "skills_mgmt.update"
#: 隐私断言用的原文标记串（绝不能出现在审计载荷里）
SECRET = "D4-SECRET-DESCRIPTION-7a1c"


@pytest.fixture
def iso_svc(tmp_path):
    """隔离的技能服务（主轨 JSON + 文件轨 repo 都在 tmp 下）"""
    return SkillsMgmtService(
        store_path=str(tmp_path / "skills_mgmt.json"),
        repo_path=str(tmp_path / "skills_repo"),
    )


def _chain():
    """真实审计链实例（门面已由 conftest 隔离到会话临时目录）"""
    from agent.audit import audit as facade
    chain = facade.chain
    assert chain is not None, "审计链不可用：门面被关闭或未初始化"
    return chain


def _records(skill_id: str, action: str):
    """从审计链读回指定技能 + 指定动作的真实记录（seq 升序）"""
    subject = f"skill:{skill_id}"
    return [e for e in _chain().entries(action=action) if e.subject == subject]


def _all_records(skill_id: str):
    """该技能在链上的全部记录（跨动作；entries() 无 subject 过滤，故 Python 侧过滤）"""
    subject = f"skill:{skill_id}"
    return [e for e in _chain().entries() if e.subject == subject]


def _body(entry) -> dict:
    """审计条目的业务载荷（facade 嵌一层 payload）"""
    inner = entry.payload.get("payload")
    return inner if isinstance(inner, dict) else entry.payload


#: 与本次实验无关的易变键（时间戳类）：逐字段比对前递归剔除
_VOLATILE_KEYS = {"created_at", "updated_at", "reviewed_at", "ts", "timestamp"}


def _strip_volatile(obj):
    """递归剔除时间戳类键，便于比对"语义"是否一致"""
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items()
                if k not in _VOLATILE_KEYS}
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def _make_main(iso_svc, skill_id: str, **extra):
    payload = {"id": skill_id, "name": skill_id, "content": "# x",
               "content_type": "markdown"}
    payload.update(extra)
    return iso_svc.create_manual(payload)


def _iso_at(tmp_path, name: str) -> SkillsMgmtService:
    """同一测试内起两个互不相干的隔离服务（避免与自身比对时触发重复检测）"""
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    return SkillsMgmtService(store_path=str(d / "skills_mgmt.json"),
                             repo_path=str(d / "skills_repo"))


class TestFieldChangeAudit:
    def test_whitelisted_field_update_lands_in_chain(self, iso_svc):
        """要求 1：改**白名单字段**落 skill.update，changed 含字段名

        【G1-B/M0 后改用 tags 作见证】原用例见文件头「为什么见证字段从 description 换成 tags」。
        """
        _make_main(iso_svc, "d4-desc-1", tags=["old description"])
        assert _records("d4-desc-1", ACTION_UPDATE) == []
        iso_svc.update("d4-desc-1", {"tags": ["new description"]})
        recs = _records("d4-desc-1", ACTION_UPDATE)
        assert len(recs) == 1, f"期望恰好 1 条 {ACTION_UPDATE}，实得 {len(recs)}"
        e = recs[0]
        assert e.action == ACTION_UPDATE
        assert e.subject == "skill:d4-desc-1"
        assert e.source == "agent"
        assert e.actor, "actor 不应为空（门面解析 UI/Agent 上下文或回落 system）"
        assert e.ts
        assert e.payload["status"] == "ok"
        body = _body(e)
        assert body["skill_id"] == "d4-desc-1"
        assert body["changed"] == ["tags"]
        assert body["origin"] == ORIGIN
        assert set(body) == {"skill_id", "changed", "origin"}, \
            "payload 只允许字段名与来源，不得夹带字段值"
        assert iso_svc.get("d4-desc-1").tags == ["new description"]

    def test_description_is_frozen_by_M0(self, iso_svc):
        """【G1-B/M0 新契约】`description` 已移出 update 白名单 ⇒ 改它**既不改值也不留痕**

        Why（G1-B0 M0 + 决策 4「先冻写路径再删数据」）：技能 description 的唯一事实源是
        `data/skills_repo/<id>/skill.md`。若仍允许经 update 改主轨 description，
        UI 编辑会重新制造出与 skill.md 冲突的第二份文案（G1-A C2 的 15/15 冲突就是这么来的）。
        本用例把「冻结」钉住：它是**故意**的静默忽略（与其它白名单外键同语义），
        不是「忘了接线」——若将来有人把 description 加回白名单，本用例会红。
        """
        _make_main(iso_svc, "d4-frozen-1", description="old description")
        before = len(_all_records("d4-frozen-1"))
        iso_svc.update("d4-frozen-1", {"description": "new description"})
        assert iso_svc.get("d4-frozen-1").description == "old description", \
            "description 已被冻结：经 update 不得改写主轨值"
        assert len(_all_records("d4-frozen-1")) == before, \
            "冻结字段不产生变更 ⇒ 不得留痕（与其它白名单外键同语义）"

    def test_secret_values_never_enter_chain(self, iso_svc):
        """要求 4（本卡核心隐私断言）：传入的原文绝不出现在审计记录中

        【G1-B/M0 后加强】同一 patch 里**故意**再塞一个已冻结的 `description=SECRET`
        —— 被冻结字段的值**同样不得泄漏**，且必须确认它也没落库（否则"没泄漏"是假阴性）。
        """
        _make_main(iso_svc, "d4-secret-1", description="old description",
                   content="# old", tags=["old"])
        iso_svc.update("d4-secret-1",
                       {"tags": [SECRET], "content": SECRET, "description": SECRET})
        recs = _records("d4-secret-1", ACTION_UPDATE)
        assert len(recs) == 1
        assert _body(recs[0])["changed"] == ["content", "tags"]
        # 跨动作扫该技能的全部记录（含 _advisory_assess 旁路写的 skill.assess.*）
        dumped = json.dumps([e.payload for e in _all_records("d4-secret-1")],
                            ensure_ascii=False, sort_keys=True)
        assert SECRET not in dumped, "描述/正文原文泄漏进审计链"
        assert "new description" not in dumped
        # 反向对照：白名单字段的原文确实落到了技能本体（证明上面不是"没写进去"造成的假阴性）
        assert iso_svc.get("d4-secret-1").content == SECRET
        assert iso_svc.get("d4-secret-1").tags == [SECRET]
        # 被冻结的 description 既不落库（见下）也不留痕 ⇒ 它的值同样没进链
        assert iso_svc.get("d4-secret-1").description == "old description"

    def test_enabled_update_reuses_enable_action_family(self, iso_svc):
        """要求 1：改 enabled 复用 skill.registry.set_enabled 动作族"""
        _make_main(iso_svc, "d4-enabled-1")
        iso_svc.update("d4-enabled-1", {"enabled": False})
        recs = _records("d4-enabled-1", ACTION_SET_ENABLED)
        assert len(recs) == 1, f"期望恰好 1 条 {ACTION_SET_ENABLED}，实得 {len(recs)}"
        e = recs[0]
        assert e.subject == "skill:d4-enabled-1"
        assert e.payload["status"] == "ok"
        body = _body(e)
        assert body["skill_id"] == "d4-enabled-1"
        assert body["origin"] == ORIGIN
        assert body["track"] == "main"
        assert body["previous_enabled"] is True
        assert body["enabled"] is False
        assert set(body) == {"skill_id", "previous_enabled", "enabled", "track",
                             "origin"}, "启停族 payload 字段应与 enhancer 同口径"
        # 纯 enabled 变更不另写 skill.update（动作族不分裂）
        assert _records("d4-enabled-1", ACTION_UPDATE) == []
        assert iso_svc.get("d4-enabled-1").enabled is False

    def test_mixed_patch_records_both_families(self, iso_svc):
        """同时改 description + enabled：字段变更族 + 启停动作族各 1 条"""
        _make_main(iso_svc, "d4-mixed-1", tags=["old"])
        iso_svc.update("d4-mixed-1", {"tags": ["new"], "enabled": False})
        ups = _records("d4-mixed-1", ACTION_UPDATE)
        sets = _records("d4-mixed-1", ACTION_SET_ENABLED)
        assert len(ups) == 1 and len(sets) == 1
        assert _body(ups[0])["changed"] == ["tags"]  # enabled 归启停族
        assert _body(sets[0])["origin"] == ORIGIN
        assert _body(sets[0])["enabled"] is False

    def test_same_value_patch_writes_nothing(self, iso_svc):
        """同值 patch 不算变更 → 不写记录（避免审计噪音）"""
        _make_main(iso_svc, "d4-same-1", tags=["same"])
        before = len(_all_records("d4-same-1"))
        iso_svc.update("d4-same-1", {"tags": ["same"], "enabled": True})
        assert len(_all_records("d4-same-1")) == before
        assert _records("d4-same-1", ACTION_UPDATE) == []
        assert _records("d4-same-1", ACTION_SET_ENABLED) == []

    def test_non_whitelisted_and_empty_patch_write_nothing(self, iso_svc):
        """非白名单键被忽略（不落库）→ 也不得留痕；空 patch 同理"""
        _make_main(iso_svc, "d4-nonwl-1")
        before = len(_all_records("d4-nonwl-1"))
        iso_svc.update("d4-nonwl-1", {"status": "archived", "quality_score": 99})
        iso_svc.update("d4-nonwl-1", {})
        assert len(_all_records("d4-nonwl-1")) == before
        assert iso_svc.get("d4-nonwl-1").status != "archived"


class TestContractAndBestEffort:
    def test_return_semantics_unchanged_with_and_without_audit(self, tmp_path):
        """要求 2：update 的返回语义不因审计而变（逐字段比对开/关两条路径）"""
        from agent.audit import audit as facade

        # 两个**互不相干**的隔离服务（同一服务里两个同内容技能会互相触发重复检测，
        # 与审计无关，故分开建库），输入完全相同，唯一差异是审计是否可用。
        svc_on = _iso_at(tmp_path, "on")
        svc_off = _iso_at(tmp_path, "off")
        _make_main(svc_on, "d4-contract-a", name="contract probe",
                   tags=["old"])
        _make_main(svc_off, "d4-contract-b", name="contract probe",
                   tags=["old"])
        r_on = svc_on.update("d4-contract-a",
                             {"tags": ["new"], "enabled": False})
        prev = facade.enabled
        facade.enabled = False
        try:
            r_off = svc_off.update("d4-contract-b",
                                   {"tags": ["new"], "enabled": False})
        finally:
            facade.enabled = prev

        assert isinstance(r_on, Skill) and isinstance(r_off, Skill)
        assert r_on.tags == r_off.tags == ["new"]
        assert r_on.enabled is False and r_off.enabled is False
        # 返回值与落库值一致（返回的就是被持久化的那一份）
        assert r_on.model_dump() == svc_on.get("d4-contract-a").model_dump()
        assert r_off.model_dump() == svc_off.get("d4-contract-b").model_dump()
        # 除 id / 时间戳外逐字段相等（审计记账不碰返回值与落库内容）
        d_on = _strip_volatile(r_on.model_dump())
        d_off = _strip_volatile(r_off.model_dump())
        d_on.pop("id"), d_off.pop("id")
        assert d_on == d_off
        # 审计关闭时 skill.update 确实没写（反向对照，证明断言读的是真实链）
        assert len(_records("d4-contract-a", ACTION_UPDATE)) == 1
        assert _records("d4-contract-b", ACTION_UPDATE) == []

    def test_audit_failure_does_not_break_update(self, iso_svc, monkeypatch,
                                                 caplog):
        """要求 3：审计抛异常不得让 update 失败，但必须留 WARNING"""
        from agent.audit import audit as facade

        _make_main(iso_svc, "d4-boom-1", tags=["old"])

        def _boom(*a, **kw):
            raise RuntimeError("audit boom")

        monkeypatch.setattr(facade, "record", _boom)
        with caplog.at_level(logging.WARNING, logger="agent.skills_mgmt"):
            r = iso_svc.update("d4-boom-1", {"tags": ["new"]})
        assert r.tags == ["new"]
        assert iso_svc.get("d4-boom-1").tags == ["new"]  # 更新真的生效了
        assert any("技能字段变更审计留痕失败" in rec.getMessage()
                   for rec in caplog.records), \
            "审计失败必须留 WARNING 日志，不能完全静默"

        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="agent.skills_mgmt"):
            r2 = iso_svc.update("d4-boom-1", {"enabled": False})
        assert r2.enabled is False
        assert iso_svc.get("d4-boom-1").enabled is False
        assert any("技能启停审计留痕失败" in rec.getMessage()
                   for rec in caplog.records), \
            "启停审计失败同样必须留 WARNING（不静默）"

    def test_audit_disabled_writes_nothing_but_logs(self, iso_svc, caplog):
        """门面关闭（record 返回 None）→ 不落链但留 WARNING，update 照常"""
        from agent.audit import audit as facade

        _make_main(iso_svc, "d4-off-1", tags=["old"])
        assert _records("d4-off-1", ACTION_UPDATE) == []
        prev = facade.enabled
        facade.enabled = False
        try:
            with caplog.at_level(logging.WARNING, logger="agent.skills_mgmt"):
                r = iso_svc.update("d4-off-1", {"tags": ["new"]})
        finally:
            facade.enabled = prev
        assert r.tags == ["new"]
        assert _records("d4-off-1", ACTION_UPDATE) == []
        assert any("技能字段变更审计未落链" in rec.getMessage()
                   for rec in caplog.records), \
            "门面返回 None（审计关闭/静默失败）必须留 WARNING"
