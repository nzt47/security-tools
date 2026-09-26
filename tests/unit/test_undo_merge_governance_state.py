"""F9 单测：`undo_merge` 是内容回滚，不得静默改回治理状态。

依据：`docs/audit_skill_governance/FINDINGS_DURING_IMPL.md` §F9（中危）——
`undo_merge` 把 `dst_before` 快照**逐字段**套回现值，而该快照含
`enabled` ⇒ 「A 与 B 合并 → 操作员事后停用 B → 撤销该次合并」会把 B
**静默重新启用**，且链上只有 `skill.assess.merge-undo`、没有
`skill.registry.set_enabled` 启停痕。

本文件断言四件事（落库值与审计链**都是真读**，无 mock）：

    1. 情形一（dst 仍存在）：撤销后 dst **仍为停用**，且**内容已回滚**；
       并用「快照里的 enabled 真的是 True」做反向对照 —— 证明该断言不是恒真
       （旧实现会把它写回，见 `TestInverseControl`）。
    2. 情形二（技能已不存在，用快照重建）：`enabled` **照常**从快照恢复
       —— 那时没有「现有治理状态」可保护，不得被一刀切禁掉。
    3. 回归：不涉及治理字段时，内容恢复与改动前**逐字段一致**
       （用旧实现的规则在测试内重算期望值，与新实现做差分比对）。
    4. 副作用面：治理状态未被改回 ⇒ 撤销过程**不产生**
       `skill.registry.set_enabled` 记录；签名与返回结构不变。

隔离（本文件**不写生产 data/**）：

    - 技能主轨 / 文件轨 / 分类注册表：tmp_path（见 `svc` 夹具）；
    - 合并 sidecar `data/skill_merge_backups.jsonl`：`undo_merge` 与
      `merge_with_backup` 都用 `service.py` 的 `__file__` 反推仓库根，
      故 `sidecar` 夹具把该模块的 `__file__` 指向 tmp_path（新写/读都落在
      tmp）；`test_sidecar_is_tmp_isolated` 真读生产 sidecar 做反向核验；
    - 审计链 / 评估事件流：`tests/conftest.py::_isolate_approval_stores`
      （会话级 autouse，改绑 AUDIT_DB_PATH 与 CP_EVENTS_DIR）；
    - `rebind_feedback=False`：不拉 `agent.feedback` 的默认实例（避免碰它的落库）。
"""

import json
import os
from pathlib import Path

import pytest

from agent.skills_mgmt import SkillsMgmtService
from agent.skills_mgmt.exceptions import SkillNotFoundError
from agent.skills_mgmt.models import Skill

#: 启停动作族（与 `agent/skills_mgmt/enhancer.py::AUDIT_ACTION_ENABLED_SET` 同字面）
ACTION_SET_ENABLED = "skill.registry.set_enabled"

#: 比对时剔除的时间戳类字段（撤销本身会 touch 一次）
_VOLATILE = {"created_at", "updated_at", "installed_at"}

CONTENT_DST = "# 保留方正文（合并前）\n提取正文与元数据，完整实现略"
CONTENT_SRC = "# 被合并方正文（合并前）\n提取正文与元数据，完整实现略（另一份）"
DESC_DST = "保留方（合并前说明）"

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    """把合并 sidecar 重定向到 tmp_path，返回它的路径（tmp 内）。

    `service.py` 的 sidecar 路径由 `__file__` 反推三层父目录得到
    （`merge_with_backup` / `list_merge_backups` / `undo_merge` 三处同源），
    所以把该模块的 `__file__` 指到 tmp 下的同名层级，读写就都落在 tmp。
    """
    import agent.skills_mgmt.service as service_module

    fake_service_py = tmp_path / "agent" / "skills_mgmt" / "service.py"
    fake_service_py.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(service_module, "__file__", str(fake_service_py))
    return tmp_path / "data" / "skill_merge_backups.jsonl"


@pytest.fixture
def svc(tmp_path, sidecar):
    """隔离的技能服务（主轨 JSON + 文件轨 repo + 分类注册表都在 tmp 下）"""
    return SkillsMgmtService(
        store_path=str(tmp_path / "skills_mgmt.json"),
        repo_path=str(tmp_path / "skills_repo"),
    )


# ─── 审计链读取（真链，不 mock）───

def _chain():
    from agent.audit import audit as facade

    chain = facade.chain
    assert chain is not None, "审计链不可用：门面被关闭或未初始化"
    return chain


def _set_enabled_records(skill_id):
    subject = f"skill:{skill_id}"
    return [e for e in _chain().entries(action=ACTION_SET_ENABLED)
            if e.subject == subject]


def _body(entry):
    """审计条目的业务载荷（门面会把业务载荷嵌一层 payload）"""
    inner = entry.payload.get("payload")
    return inner if isinstance(inner, dict) else entry.payload


# ─── sidecar 读写（tmp 内）───

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


def _append_record(path, rec):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _stable(dump):
    return {k: v for k, v in dump.items() if k not in _VOLATILE}


# ─── 场景构造 ───

def _create(iso_svc, skill_id, content, *, description=None, enabled=True,
            **extra):
    payload = {
        "id": skill_id, "name": skill_id,
        "description": description or f"{skill_id} 的说明（用于测试治理状态回滚）",
        "content": content, "content_type": "markdown",
        "category": "custom", "tags": ["f9"], "author": "tester",
        "enabled": enabled,
    }
    payload.update(extra)
    return iso_svc.create_manual(payload)


def _merge(iso_svc, src_id, dst_id):
    """真实「安全合并」：先写快照再合并（src 被删）。

    strategy="keep_dst" 固定主从方向（auto 会按 status/metrics 调换主从）；
    rebind_feedback=False 不触达 `agent.feedback` 的默认落库。
    """
    return iso_svc.merge_with_backup(src_id, dst_id, strategy="keep_dst",
                                     rebind_feedback=False)


def _snapshot_dict(skill_id, content, *, enabled=True):
    """构造一份合法的 Skill 存储字典（用于手工伪造 sidecar 记录）"""
    return Skill.from_storage_dict({
        "id": skill_id, "name": skill_id,
        "description": f"{skill_id} 的快照说明", "content": content,
        "content_type": "markdown", "category": "custom",
        "tags": ["f9"], "author": "tester", "enabled": enabled,
    }).model_dump()


# ═══════════════════════════════════════════════════════════════
#  情形一：dst 仍存在 ⇒ 只回滚内容，治理状态保持现值
# ═══════════════════════════════════════════════════════════════

class TestCase1ExistingDstKeepsGovernance:
    def test_disabled_dst_stays_disabled_while_content_rolls_back(self, svc,
                                                                  sidecar):
        """F9 主场景：合并 → 停用 dst → 撤销 ⇒ dst 仍停用，内容已回滚"""
        _create(svc, "f9-c1-dst", CONTENT_DST, description=DESC_DST)
        _create(svc, "f9-c1-src", CONTENT_SRC)
        merge_id = _merge(svc, "f9-c1-src", "f9-c1-dst")["merge_id"]

        # 前提（反向对照）：快照里的 enabled 真的是 True —— 旧实现会写回它，
        # 所以下面的 "is False" 断言在改动前必然失败，不是恒真。
        assert _record_of(sidecar, merge_id)["dst_before"]["enabled"] is True

        # 合并之后：dst 内容被继续编辑，操作员随后手动停用 dst
        svc.update("f9-c1-dst", {"content": "# 合并之后又被改写的正文"})
        svc.set_enabled("f9-c1-dst", False)
        assert svc.get("f9-c1-dst").enabled is False

        undo = svc.undo_merge(merge_id)

        dst = svc.get("f9-c1-dst")
        assert dst.enabled is False, \
            "撤销合并把治理状态 enabled 静默改回了合并前快照"
        assert dst.content == CONTENT_DST, "内容没有被回滚到合并前"
        assert dst.description == DESC_DST, "描述没有被回滚到合并前"
        assert svc.get("f9-c1-src").content == CONTENT_SRC
        assert undo["restored"] == ["f9-c1-src", "f9-c1-dst"]

    def test_undo_produces_no_enable_audit_record(self, svc):
        """治理状态没变 ⇒ 撤销过程不得产生启停记录（链上真读）"""
        _create(svc, "f9-c1b-dst", CONTENT_DST)
        _create(svc, "f9-c1b-src", CONTENT_SRC)
        merge_id = _merge(svc, "f9-c1b-src", "f9-c1b-dst")["merge_id"]
        svc.set_enabled("f9-c1b-dst", False)

        before = _set_enabled_records("f9-c1b-dst")
        assert len(before) == 1, f"操作员停用应恰好 1 条启停记录，实得 {len(before)}"
        assert _body(before[0])["enabled"] is False
        assert _body(before[0])["previous_enabled"] is True

        svc.undo_merge(merge_id)

        after = _set_enabled_records("f9-c1b-dst")
        assert len(after) == 1, \
            "撤销合并产生了额外启停记录：治理状态本不该被改动"
        assert svc.get("f9-c1b-dst").enabled is False

    def test_case1_never_restores_other_governance_fields(self, svc, sidecar):
        """前瞻性防护：快照若带 is_sensitive / isolation_strategy，情形一同样不恢复

        今天 `merge_with_backup` 的 `dst_before` 不含这两个字段，故本用例
        手工构造一条「快照已扩到隔离字段」的记录，把保留口径固化下来。
        """
        _create(svc, "f9-c3-dst", "# 现值正文")
        svc.set_enabled("f9-c3-dst", False)
        cur = svc.get("f9-c3-dst")
        assert cur.is_sensitive is False
        assert cur.isolation_strategy == "separate_turn"

        _append_record(sidecar, {
            "merge_id": "f9-c3-crafted", "ts": "2026-01-01T00:00:00",
            "src_id": "", "dst_id": "f9-c3-dst", "src_snapshot": {},
            "dst_before": {"content": "# 快照正文", "enabled": True,
                           "is_sensitive": True,
                           "isolation_strategy": "separate_session"},
        })
        svc.undo_merge("f9-c3-crafted")

        after = svc.get("f9-c3-dst")
        assert after.content == "# 快照正文", "内容字段应当照常回滚"
        assert after.enabled is False
        assert after.is_sensitive is False, "is_sensitive 被快照静默改回"
        assert after.isolation_strategy == "separate_turn", \
            "isolation_strategy 被快照静默改回"

    def test_sidecar_is_tmp_isolated(self, svc, sidecar, tmp_path):
        """隔离自证：本次合并的备份只落在 tmp，生产 sidecar 里没有这个 merge_id"""
        _create(svc, "f9-iso-dst", CONTENT_DST)
        _create(svc, "f9-iso-src", CONTENT_SRC)
        merge_id = _merge(svc, "f9-iso-src", "f9-iso-dst")["merge_id"]

        assert sidecar == tmp_path / "data" / "skill_merge_backups.jsonl"
        assert _record_of(sidecar, merge_id)["dst_id"] == "f9-iso-dst"

        prod = _REPO_ROOT / "data" / "skill_merge_backups.jsonl"
        if prod.exists():
            text = prod.read_text(encoding="utf-8", errors="ignore")
            assert merge_id not in text, "本次用例写进了生产 sidecar"


# ═══════════════════════════════════════════════════════════════
#  情形二：技能已不存在 ⇒ 用快照重建（enabled 照常恢复，不得一刀切禁掉）
# ═══════════════════════════════════════════════════════════════

class TestCase2RebuildRestoresEnabled:
    def test_rebuilt_skill_keeps_snapshot_enabled(self, svc, sidecar):
        """合并 → 删除 dst → 撤销：技能由快照重建，enabled 来自快照"""
        _create(svc, "f9-c2-dst", CONTENT_DST)
        _create(svc, "f9-c2-src", CONTENT_SRC, enabled=False)
        merge_id = _merge(svc, "f9-c2-src", "f9-c2-dst")["merge_id"]

        rec = _record_of(sidecar, merge_id)
        assert rec["src_snapshot"]["id"] == "f9-c2-src"
        assert rec["src_snapshot"]["enabled"] is False, \
            "前置条件：快照里就是停用态（否则「来自快照」不可证）"

        svc.delete("f9-c2-dst")           # 保留方随后也被删除
        with pytest.raises(SkillNotFoundError):
            svc.get("f9-c2-dst")

        undo = svc.undo_merge(merge_id)

        rebuilt = svc.get("f9-c2-src")
        assert rebuilt.content == CONTENT_SRC
        assert rebuilt.enabled is False, \
            "技能已被删除、无「现有治理状态」可保护 ⇒ 快照的 enabled 应照常恢复"
        # [F10 更新 2026-09-25] 本断言原为 `== ["f9-c2-src"]`，并在注释里把
        # 「dst 重建分支不可达」记为**现状**（判据 snap["id"] == dst_id 而
        # src_snapshot["id"] 恒等于 src_id ⇒ 真实记录走不到）。
        # F10 已修：`merge_with_backup` 现在**额外写 `dst_snapshot`**，
        # `undo_merge` 改以 `dst_snapshot["id"] == dst_id` 为判据 ⇒ 该分支**现在可达**。
        # ⇒ 正确结果是 src 与 dst **都被重建**。这不是放宽断言，是**契约变更**：
        #   原先「静默少恢复一个技能且不报错」本身就是被修掉的缺陷。
        assert undo["restored"] == ["f9-c2-src", "f9-c2-dst"]
        rebuilt_dst = svc.get("f9-c2-dst")
        assert rebuilt_dst.content == CONTENT_DST, \
            "dst 应由 dst_snapshot 重建，正文回到合并前"
        assert rebuilt_dst.enabled is True, \
            "重建路径无「现有治理状态」可保护 ⇒ enabled 来自快照（本用例 dst 建时默认 True）"

    def test_dst_rebuild_branch_also_restores_snapshot_enabled(self, svc,
                                                               sidecar):
        """`except SkillNotFoundError` 的「用快照重建 dst」分支同样恢复 enabled

        该分支在真实合并记录下不可达（上面已实测），故手工构造一条形状一致的
        记录来固化语义：重建时没有「现有治理状态」可保护 ⇒ 快照里的
        enabled（False）照常恢复，不被情形一的保留口径误伤。
        """
        _append_record(sidecar, {
            "merge_id": "f9-c2c-crafted", "ts": "2026-01-01T00:00:00",
            "src_id": "", "dst_id": "f9-c2c-dst",
            "src_snapshot": _snapshot_dict("f9-c2c-dst", CONTENT_SRC,
                                           enabled=False),
            "dst_before": {"content": "# 无关快照"},
        })

        undo = svc.undo_merge("f9-c2c-crafted")

        rebuilt = svc.get("f9-c2c-dst")
        assert rebuilt.content == CONTENT_SRC
        assert rebuilt.enabled is False, "重建路径的 enabled 应来自快照"
        assert undo["restored"] == ["f9-c2c-dst"]


# ═══════════════════════════════════════════════════════════════
#  回归：内容恢复行为与改动前逐字段一致
# ═══════════════════════════════════════════════════════════════

class TestContentRollbackRegression:
    def test_rollback_matches_pre_fix_rule_field_by_field(self, svc, sidecar):
        """不涉及治理字段时，落库结果与改动前「逐字段套回快照」完全一致

        判据：用**旧实现的规则**在测试内重算期望值（`expected_old`），
        再断言它与新实现的期望（`expected_new`）相等，并断言真实的落库结果
        等于该期望 —— 即本卡只收窄了治理字段，没有动内容恢复行为。
        """
        _create(svc, "f9-r1-dst", CONTENT_DST, description=DESC_DST)
        _create(svc, "f9-r1-src", CONTENT_SRC)
        merge_id = _merge(svc, "f9-r1-src", "f9-r1-dst")["merge_id"]
        before = _record_of(sidecar, merge_id)["dst_before"]

        # 合并后只改内容/描述，**不碰** enabled ⇒ 快照与现值在治理字段上同值
        svc.update("f9-r1-dst", {"content": "# 合并后改写",
                                 "description": "合并后说明"})
        cur_dump = svc.get("f9-r1-dst").model_dump()
        assert cur_dump["enabled"] is True
        assert before["enabled"] is True, "本用例要求快照与现值同值"

        expected_old = {**cur_dump, **before}                    # 改动前的规则
        expected_new = {
            **cur_dump,
            **{k: v for k, v in before.items()
               if k not in SkillsMgmtService._UNDO_MERGE_KEEP_FIELDS},
        }
        assert expected_old == expected_new, \
            "同值情形下新旧规则本应逐字段一致（否则本卡的收窄超出了治理字段）"

        svc.undo_merge(merge_id)
        stored = svc.get("f9-r1-dst").model_dump()

        # 1) 整条记录等于改动前的期望值（时间戳除外）——逐字段一致
        assert _stable(stored) == _stable(expected_old)
        # 2) 快照里的字段确实逐个套回（防止上面因"两边都没写"而假绿）
        for k, v in before.items():
            assert stored[k] == v, f"快照字段 {k} 未被恢复"
        # 3) 快照之外的字段保持现值（本卡没有扩大影响面）
        for k, v in cur_dump.items():
            if k in before or k in _VOLATILE:
                continue
            assert stored[k] == v, f"非快照字段 {k} 被意外改动"

    def test_return_contract_unchanged(self, svc):
        """签名与返回结构不变：{ok, merge_id, restored, note}"""
        _create(svc, "f9-r2-dst", CONTENT_DST)
        _create(svc, "f9-r2-src", CONTENT_SRC)
        merge_id = _merge(svc, "f9-r2-src", "f9-r2-dst")["merge_id"]

        undo = svc.undo_merge(merge_id)

        assert set(undo) == {"ok", "merge_id", "restored", "note"}
        assert undo["ok"] is True
        assert undo["merge_id"] == merge_id
        assert undo["restored"] == ["f9-r2-src", "f9-r2-dst"]
        assert isinstance(undo["note"], str) and undo["note"]
        assert "治理状态" in undo["note"], "note 应说明治理状态不随内容回滚"
