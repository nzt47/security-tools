"""F10 单测：`undo_merge` 的「保留方 dst 也被删除」重建分支必须真的可达。

依据：`docs/audit_skill_governance/F9.md` §6「顺带发现」+
`docs/audit_skill_governance/FINDINGS_DURING_IMPL.md` §F10。

缺陷（先用探针复现，再用用例锁住）：

    `merge_with_backup` 只写 `src_snapshot`（= `src.model_dump()`，其 `id` 恒为
    `src_id`）与 `dst_before`（保留方的 8 个字段，**不含 id**）；而 `undo_merge`
    在「保留方后来也被删除」时的重建判据却是 `src_snapshot["id"] == dst_id`。
    因为 `merge_duplicate_skills` 明确禁止 `src_id == dst_id`，该条件**恒为假**
    ⇒ 重建分支不可达 ⇒ 撤销合并**静默少恢复一个技能**且不报错。

修法：`merge_with_backup` 同批多写一份 `dst_snapshot`（保留方**完整实体**）；
`undo_merge` 改读它并以 `dst_snapshot["id"] == dst_id` 为判据，读不到时**原样
退回本卡之前的行为**且不抛异常（向前兼容，见 `TestScenarioCLegacySidecar`）。

与 F9 的语义关系（本文件显式锁定）：

    `_UNDO_MERGE_KEEP_FIELDS`（F9）的注释写明其生效范围**仅限「dst 仍存在」的
    分支** ——「技能已被删除、要靠快照重建时没有『现有治理状态』可保护」。
    故新增的 dst 重建分支**属于后者**：`enabled` 应**来自 `dst_snapshot`**，
    不得被情形一的保留口径误伤。`TestRebuildRestoresEnabledFromSnapshot`
    用一个「快照停用、删除前已启用」的技能把这条语义钉死（模型默认值也是 True，
    所以断言 False 只可能来自快照）。

隔离（本文件**不写生产 data/**，并自带自证用例 `test_isolation_self_proof`）：

    - 技能主轨 / 文件轨 / 分类注册表：tmp_path（`svc` 夹具）；
    - 合并 sidecar `data/skill_merge_backups.jsonl`：`merge_with_backup` 与
      `undo_merge` 都由 `service.py` 的 `__file__` 反推仓库根 ⇒ `isolate_paths`
      夹具把该模块的 `__file__` 指向 tmp_path；
    - 评估事件文件 `data/skills_assessment_events.jsonl`：
      `log_archiver.repo_data_dir()` 同样由 `__file__` 反推 ⇒ 一并重定向到 tmp
      （F9 的单测文件没做这一步，本文件不复制那个副作用）；
    - 审计链 / 审批库：`tests/conftest.py::_isolate_approval_stores`（会话级 autouse）；
    - `rebind_feedback=False`：不拉 `agent.feedback` 的默认实例。
"""

import json
import os
from pathlib import Path

import pytest

from agent.skills_mgmt import SkillsMgmtService
from agent.skills_mgmt.exceptions import SkillNotFoundError

#: 本卡之前 `merge_with_backup` 写出的 sidecar 记录**恰好**这几个键（探针实测）
LEGACY_RECORD_KEYS = {"merge_id", "ts", "src_id", "dst_id",
                      "src_snapshot", "dst_before"}

#: `dst_before` 的字段清单（与 `merge_with_backup` 逐字同源）
DST_BEFORE_FIELDS = {"name", "description", "content", "content_type",
                     "enabled", "default_params", "config_schema", "tags"}

CONTENT_DST = "# 保留方正文（合并前）\n提取正文与元数据，完整实现略"
CONTENT_SRC = "# 被合并方正文（合并前）\n提取正文与元数据，完整实现略（另一份）"
DESC_DST = "保留方（合并前说明）"

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def isolate_paths(tmp_path, monkeypatch):
    """把 sidecar 与评估事件文件都重定向到 tmp_path（两条路径同源：`__file__`）"""
    import agent.skills_mgmt.log_archiver as log_archiver_module
    import agent.skills_mgmt.service as service_module

    fake = tmp_path / "agent" / "skills_mgmt"
    fake.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(service_module, "__file__", str(fake / "service.py"))
    monkeypatch.setattr(log_archiver_module, "__file__",
                        str(fake / "log_archiver.py"))
    return {"sidecar": tmp_path / "data" / "skill_merge_backups.jsonl",
            "events": tmp_path / "data" / "skills_assessment_events.jsonl"}


@pytest.fixture
def sidecar(isolate_paths):
    return isolate_paths["sidecar"]


@pytest.fixture
def svc(tmp_path, isolate_paths):
    """隔离的技能服务（主轨 JSON + 文件轨 repo + 分类注册表都在 tmp 下）"""
    return SkillsMgmtService(
        store_path=str(tmp_path / "skills_mgmt.json"),
        repo_path=str(tmp_path / "skills_repo"),
    )


# ─── sidecar / 事件文件读写（都在 tmp 内）───

def _records(path):
    out = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    return out


def _record_of(path, merge_id):
    for rec in _records(path):
        if rec.get("merge_id") == merge_id:
            return rec
    raise AssertionError(f"sidecar 中找不到 merge_id={merge_id}")


def _drop_dst_snapshot(path, merge_id):
    """把一条新记录降级成**本卡之前的旧格式**（去掉 dst_snapshot），返回旧记录

    这样构造出来的记录与本卡之前的 `merge_with_backup` 写出的形状一致
    （键集合逐字比对见 `test_legacy_sidecar_is_key_for_key_the_pre_f10_shape`）。
    """
    rows = _records(path)
    legacy = None
    for rec in rows:
        if rec.get("merge_id") == merge_id:
            rec.pop("dst_snapshot", None)
            legacy = rec
    with open(path, "w", encoding="utf-8") as f:
        for rec in rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    assert legacy is not None, f"sidecar 中找不到 merge_id={merge_id}"
    return legacy


def _undo_events(events_path, merge_id):
    return [e for e in _records(events_path)
            if e.get("kind") == "merge-undo"
            and merge_id in str(e.get("summary", ""))]


# ─── 场景构造 ───

def _create(iso_svc, skill_id, content, *, description=None, enabled=True):
    return iso_svc.create_manual({
        "id": skill_id, "name": skill_id,
        "description": description or f"{skill_id} 的说明（F10 快照重建）",
        "content": content, "content_type": "markdown",
        "category": "custom", "tags": ["f10"], "author": "tester",
        "enabled": enabled,
    })


def _merge(iso_svc, src_id, dst_id):
    """真实「安全合并」：`keep_dst` 固定主从方向；不触达 `agent.feedback`"""
    return iso_svc.merge_with_backup(src_id, dst_id, strategy="keep_dst",
                                     rebind_feedback=False)


def _deleted(iso_svc, skill_id):
    with pytest.raises(SkillNotFoundError):
        iso_svc.get(skill_id)



# ═══════════════════════════════════════════════════════════════
#  0) 记录形状：dst_snapshot 存在；旧判据在真实记录下不可能成立
# ═══════════════════════════════════════════════════════════════

class TestRecordShape:
    def test_merge_writes_full_dst_snapshot_and_keeps_dst_before(self, svc,
                                                                sidecar):
        """新记录同时含 dst_snapshot（完整实体）与 dst_before（8 字段）"""
        _create(svc, "f10-s1-dst", CONTENT_DST, description=DESC_DST)
        _create(svc, "f10-s1-src", CONTENT_SRC)
        merge_id = _merge(svc, "f10-s1-src", "f10-s1-dst")["merge_id"]

        rec = _record_of(sidecar, merge_id)
        assert "dst_snapshot" in rec, "merge_with_backup 没有写 dst_snapshot"
        assert set(rec["dst_before"]) == DST_BEFORE_FIELDS, \
            "dst_before 的字段清单被改动（F9 的情形一依赖它逐字不变）"

        snap = rec["dst_snapshot"]
        assert snap["id"] == "f10-s1-dst"
        for key in ("id", "status", "category", "version", "created_at",
                    "content", "enabled"):
            assert key in snap, f"dst_snapshot 缺少 {key}（不是完整实体）"
        assert snap["content"] == CONTENT_DST

    def test_old_criterion_can_never_hold_for_real_records(self, svc, sidecar):
        """反向对照：旧判据 src_snapshot.id == dst_id 在真实记录下恒为假

        这正是 F10 的缺陷本体 —— 若这条断言失败，说明本文件里「dst 被重建」的
        用例可能是靠旧判据通过的，而不是靠新判据。
        """
        _create(svc, "f10-s2-dst", CONTENT_DST)
        _create(svc, "f10-s2-src", CONTENT_SRC)
        merge_id = _merge(svc, "f10-s2-src", "f10-s2-dst")["merge_id"]

        rec = _record_of(sidecar, merge_id)
        assert rec["src_snapshot"]["id"] == "f10-s2-src"
        assert rec["src_id"] != rec["dst_id"]
        assert rec["src_snapshot"]["id"] != rec["dst_id"], \
            "旧判据竟然成立：本用例的前提（F10）已不成立"


# ═══════════════════════════════════════════════════════════════
#  场景 A：合并 -> 删 dst -> undo ⇒ dst 被重建
# ═══════════════════════════════════════════════════════════════

class TestScenarioADstRebuilt:
    def test_deleting_dst_then_undo_rebuilds_it(self, svc, sidecar):
        """F10 主场景：保留方被删后，撤销合并必须把它整条重建回来"""
        _create(svc, "f10-a-dst", CONTENT_DST, description=DESC_DST)
        _create(svc, "f10-a-src", CONTENT_SRC)
        merge_id = _merge(svc, "f10-a-src", "f10-a-dst")["merge_id"]
        snapshot = _record_of(sidecar, merge_id)["dst_snapshot"]

        svc.delete("f10-a-dst")
        _deleted(svc, "f10-a-dst")

        undo = svc.undo_merge(merge_id)

        rebuilt = svc.get("f10-a-dst")
        assert rebuilt.content == CONTENT_DST, "重建内容与合并前不一致"
        assert rebuilt.description == DESC_DST
        assert rebuilt.name == "f10-a-dst"
        assert rebuilt.model_dump() == snapshot, \
            "重建结果与 dst_snapshot 不一致（未按完整实体重建）"
        assert undo["restored"] == ["f10-a-src", "f10-a-dst"], \
            "保留方没有被重建（F10 的缺陷正是这里少一项）"

    def test_dst_rebuild_does_not_duplicate_or_disturb_src(self, svc, sidecar):
        """重建只补回 dst，不改变 src 的恢复结果与顺序"""
        _create(svc, "f10-a2-dst", CONTENT_DST)
        _create(svc, "f10-a2-src", CONTENT_SRC)
        merge_id = _merge(svc, "f10-a2-src", "f10-a2-dst")["merge_id"]
        svc.delete("f10-a2-dst")

        undo = svc.undo_merge(merge_id)

        assert undo["restored"] == ["f10-a2-src", "f10-a2-dst"]
        assert svc.get("f10-a2-src").content == CONTENT_SRC
        assert len([s for s in svc.list_all()
                    if s.id in ("f10-a2-src", "f10-a2-dst")]) == 2



# ═══════════════════════════════════════════════════════════════
#  语义锁：重建 ⇒ enabled **来自 dst_snapshot**（与 F9 情形一相反）
# ═══════════════════════════════════════════════════════════════

class TestRebuildRestoresEnabledFromSnapshot:
    def test_rebuilt_dst_takes_enabled_from_snapshot_not_from_default(
            self, svc, sidecar):
        """情形二恢复 enabled；且该值只能来自快照（快照 False、模型默认 True）

        `_UNDO_MERGE_KEEP_FIELDS`（F9）注释原文：

            「生效范围仅限『dst 仍存在』的分支：技能已被删除、要靠快照重建时
              没有『现有治理状态』可保护，快照里的 `enabled` 等照常恢复」

        本用例把「没有现有治理状态」推到极致：删除**之前**它是启用态（True，
        且 `Skill.enabled` 的模型默认值也是 True），删除**之后**重建出来必须是
        快照里的 False —— 否则要么是保留口径误伤了情形二，要么是重建根本没走
        快照。
        """
        assert "enabled" in SkillsMgmtService._UNDO_MERGE_KEEP_FIELDS

        _create(svc, "f10-a3-dst", CONTENT_DST, enabled=False)
        _create(svc, "f10-a3-src", CONTENT_SRC)
        merge_id = _merge(svc, "f10-a3-src", "f10-a3-dst")["merge_id"]

        rec = _record_of(sidecar, merge_id)
        assert rec["dst_snapshot"]["enabled"] is False, "前置：快照里是停用态"

        svc.set_enabled("f10-a3-dst", True)
        assert svc.get("f10-a3-dst").enabled is True

        svc.delete("f10-a3-dst")
        undo = svc.undo_merge(merge_id)

        assert undo["restored"] == ["f10-a3-src", "f10-a3-dst"]
        assert svc.get("f10-a3-dst").enabled is False, \
            "重建没有取 dst_snapshot 里的 enabled（False 只可能来自快照）"

    def test_case_one_still_keeps_governance_state(self, svc, sidecar):
        """对照：dst **仍存在**时（情形一）enabled 保持现值，绝不回滚（F9 不回归）"""
        _create(svc, "f10-a4-dst", CONTENT_DST, enabled=True)
        _create(svc, "f10-a4-src", CONTENT_SRC)
        merge_id = _merge(svc, "f10-a4-src", "f10-a4-dst")["merge_id"]

        rec = _record_of(sidecar, merge_id)
        assert rec["dst_snapshot"]["enabled"] is True, \
            "反向对照：快照 enabled 为 True ⇒ 若被写回就测不出保留口径"

        svc.set_enabled("f10-a4-dst", False)
        svc.undo_merge(merge_id)

        assert svc.get("f10-a4-dst").enabled is False
        assert svc.get("f10-a4-dst").content == CONTENT_DST


# ═══════════════════════════════════════════════════════════════
#  场景 B：合并 -> 不删 dst -> undo ⇒ 与 F9 之后完全一致
# ═══════════════════════════════════════════════════════════════

class TestScenarioBExistingDstUnchanged:
    def test_governance_fields_not_restored_when_dst_still_exists(
            self, svc, sidecar):
        """F9 行为逐条复验：内容回滚、治理字段保持现值、顺序不变"""
        _create(svc, "f10-b-dst", CONTENT_DST, description=DESC_DST)
        _create(svc, "f10-b-src", CONTENT_SRC)
        merge_id = _merge(svc, "f10-b-src", "f10-b-dst")["merge_id"]

        snap = _record_of(sidecar, merge_id)["dst_snapshot"]
        assert snap["enabled"] is True and snap["is_sensitive"] is False

        svc.update("f10-b-dst", {"content": "# 合并之后又被改写的正文"})
        svc.set_enabled("f10-b-dst", False)
        svc.update("f10-b-dst", {"is_sensitive": True,
                                 "isolation_strategy": "separate_session"})
        before_undo = svc.get("f10-b-dst")

        undo = svc.undo_merge(merge_id)

        after = svc.get("f10-b-dst")
        assert after.content == CONTENT_DST, "内容没有被回滚"
        assert after.description == DESC_DST
        assert after.enabled is False, "enabled 被静默改回（F9 回归）"
        assert after.is_sensitive is True, "is_sensitive 被静默改回（F9 回归）"
        assert after.isolation_strategy == "separate_session"
        assert after.status == before_undo.status
        assert after.version == before_undo.version
        assert undo["restored"] == ["f10-b-src", "f10-b-dst"]

    def test_case_one_ignores_dst_snapshot(self, svc, sidecar):
        """证据：情形一读的是 dst_before，删掉整个 dst_snapshot 也不影响结果"""
        _create(svc, "f10-b2-dst", CONTENT_DST, description=DESC_DST)
        _create(svc, "f10-b2-src", CONTENT_SRC)
        merge_id = _merge(svc, "f10-b2-src", "f10-b2-dst")["merge_id"]

        rows = _records(sidecar)
        for rec in rows:
            if rec.get("merge_id") == merge_id:
                rec.pop("dst_snapshot")          # 情形一不该需要它
        with open(sidecar, "w", encoding="utf-8") as f:
            for rec in rows:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        svc.set_enabled("f10-b2-dst", False)
        undo = svc.undo_merge(merge_id)

        assert svc.get("f10-b2-dst").content == CONTENT_DST
        assert svc.get("f10-b2-dst").enabled is False
        assert undo["restored"] == ["f10-b2-src", "f10-b2-dst"]



# ═══════════════════════════════════════════════════════════════
#  场景 C：旧格式 sidecar（无 dst_snapshot）⇒ 不抛异常 + 行为可预期
# ═══════════════════════════════════════════════════════════════

class TestScenarioCLegacySidecar:
    def test_legacy_sidecar_is_key_for_key_the_pre_f10_shape(self, svc,
                                                              sidecar):
        """先证明「降级构造」出的记录就是本卡之前的形状（键集合逐字相等）"""
        _create(svc, "f10-c0-dst", CONTENT_DST)
        _create(svc, "f10-c0-src", CONTENT_SRC)
        merge_id = _merge(svc, "f10-c0-src", "f10-c0-dst")["merge_id"]
        assert set(_record_of(sidecar, merge_id)) == LEGACY_RECORD_KEYS | {
            "dst_snapshot"}

        legacy = _drop_dst_snapshot(sidecar, merge_id)
        assert set(legacy) == LEGACY_RECORD_KEYS

    def test_legacy_sidecar_does_not_raise_and_does_not_rebuild(
            self, svc, sidecar, isolate_paths, caplog):
        """旧记录 + 保留方已删除 ⇒ **不抛异常**；行为 = 不重建 + 明确留痕

        降级口径（本卡实测行为，非推测）：

            1. `undo_merge` 正常返回（ok=True），restored == [src]；
            2. dst 不被重建，仍为不存在（与改动前逐字一致：旧判据恒为假）；
            3. 不留静默：logger 出 warning，评估事件 summary 追加「未能自动重建」。

        为什么选这个口径而不是「拿 dst_before 的 8 个残字段硬拼一个技能」：
        旧记录的 dst_before 不含 id/status/category/version/时间戳，只能靠默认值
        猜补 ⇒ 会凭空造出一个**看起来真实**的技能（status=draft、version=0.1.0、
        category=custom）。撤销合并宁可少恢复一个，也不造假技能。
        """
        _create(svc, "f10-c-dst", CONTENT_DST, description=DESC_DST)
        _create(svc, "f10-c-src", CONTENT_SRC)
        merge_id = _merge(svc, "f10-c-src", "f10-c-dst")["merge_id"]
        legacy = _drop_dst_snapshot(sidecar, merge_id)
        assert "dst_snapshot" not in legacy

        svc.delete("f10-c-dst")

        with caplog.at_level("WARNING"):
            undo = svc.undo_merge(merge_id)          # 不得抛异常

        assert undo["ok"] is True
        assert undo["restored"] == ["f10-c-src"], \
            "旧记录应当是「不重建」而不是「用残字段造一个技能」"
        _deleted(svc, "f10-c-dst")
        assert svc.get("f10-c-src").content == CONTENT_SRC

        warnings = [r.getMessage() for r in caplog.records
                    if r.levelname == "WARNING"]
        assert any("dst_snapshot" in w for w in warnings), \
            f"没有 warning 说明无法重建：{warnings}"
        events = _undo_events(isolate_paths["events"], merge_id)
        assert events, "撤销合并没有写评估事件"
        assert "未能自动重建" in events[-1]["summary"], \
            "事件里没有说明未能重建：" + events[-1]["summary"]

    def test_legacy_sidecar_still_restores_src(self, svc, sidecar):
        """旧记录里 src 的恢复能力不受影响（既有能力不得回归）"""
        _create(svc, "f10-c2-dst", CONTENT_DST)
        _create(svc, "f10-c2-src", CONTENT_SRC, enabled=False)
        merge_id = _merge(svc, "f10-c2-src", "f10-c2-dst")["merge_id"]
        _drop_dst_snapshot(sidecar, merge_id)

        # 合并本身已把 src 删除；这里再删掉保留方，制造「两侧都不存在」的极端
        svc.delete("f10-c2-dst")
        _deleted(svc, "f10-c2-src")

        undo = svc.undo_merge(merge_id)

        assert undo["restored"] == ["f10-c2-src"]
        assert svc.get("f10-c2-src").content == CONTENT_SRC
        assert svc.get("f10-c2-src").enabled is False
        _deleted(svc, "f10-c2-dst")


# ═══════════════════════════════════════════════════════════════
#  场景 D：删 src 不删 dst ⇒ src 重建（既有能力，不得回归）
# ═══════════════════════════════════════════════════════════════

class TestScenarioDSrcRebuildRegression:
    def test_src_rebuilt_while_dst_rolls_back(self, svc, sidecar):
        """src 被删、dst 仍在 ⇒ src 由 src_snapshot 重建，dst 只回滚内容"""
        _create(svc, "f10-d-dst", CONTENT_DST, description=DESC_DST)
        _create(svc, "f10-d-src", CONTENT_SRC, enabled=False)
        merge_id = _merge(svc, "f10-d-src", "f10-d-dst")["merge_id"]

        rec = _record_of(sidecar, merge_id)
        assert rec["src_snapshot"]["enabled"] is False

        svc.update("f10-d-dst", {"content": "# 合并后改写"})
        svc.set_enabled("f10-d-dst", False)
        _deleted(svc, "f10-d-src")

        undo = svc.undo_merge(merge_id)

        src = svc.get("f10-d-src")
        assert src.content == CONTENT_SRC
        assert src.enabled is False, "src 重建应恢复快照里的启用态"
        dst = svc.get("f10-d-dst")
        assert dst.content == CONTENT_DST
        assert dst.enabled is False, "情形一：dst 的治理状态保持现值"
        assert undo["restored"] == ["f10-d-src", "f10-d-dst"]


# ═══════════════════════════════════════════════════════════════
#  契约与隔离
# ═══════════════════════════════════════════════════════════════

class TestContractAndIsolation:
    def test_return_contract_unchanged(self, svc):
        """签名与返回结构不变：ok/merge_id/restored/note；restored 顺序 src→dst"""
        _create(svc, "f10-e-dst", CONTENT_DST)
        _create(svc, "f10-e-src", CONTENT_SRC)
        merge_id = _merge(svc, "f10-e-src", "f10-e-dst")["merge_id"]

        undo = svc.undo_merge(merge_id)

        assert set(undo) == {"ok", "merge_id", "restored", "note"}
        assert undo["ok"] is True and undo["merge_id"] == merge_id
        assert undo["restored"] == ["f10-e-src", "f10-e-dst"]
        assert "治理状态" in undo["note"], "F9 的 note 语义被改动"

    def test_isolation_self_proof(self, svc, isolate_paths, tmp_path):
        """自证：sidecar 与评估事件都落在 tmp，生产 data/ 里没有本次 merge_id"""
        _create(svc, "f10-iso-dst", CONTENT_DST)
        _create(svc, "f10-iso-src", CONTENT_SRC)
        merge_id = _merge(svc, "f10-iso-src", "f10-iso-dst")["merge_id"]
        svc.delete("f10-iso-dst")
        svc.undo_merge(merge_id)

        assert isolate_paths["sidecar"] == tmp_path / "data" / \
            "skill_merge_backups.jsonl"
        assert _record_of(isolate_paths["sidecar"], merge_id)

        events_text = isolate_paths["events"].read_text(encoding="utf-8",
                                                        errors="ignore")
        assert merge_id in events_text, \
            "评估事件没有落在 tmp（log_archiver.__file__ 重定向失效）"

        prod_sidecar = _REPO_ROOT / "data" / "skill_merge_backups.jsonl"
        if prod_sidecar.exists():
            text = prod_sidecar.read_text(encoding="utf-8", errors="ignore")
            assert merge_id not in text, "本次用例写进了生产 sidecar"

        prod_events = _REPO_ROOT / "data" / "skills_assessment_events.jsonl"
        if prod_events.exists():
            text = prod_events.read_text(encoding="utf-8", errors="ignore")
            assert merge_id not in text, "本次用例写进了生产评估事件文件"

