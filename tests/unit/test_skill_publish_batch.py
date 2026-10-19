"""待放行队列 · 批量放行（publish_batch）单元测试。

锁三条安全语义（错哪条都比"没接线"更贵）：

  1. **没有显式 ids 就没有批量** —— 服务端不提供"全部"语义：队列里有评审阻断项，
     "一键全放"等于把强制审核链一起拆掉；
  2. **force 必须带可审计原因** —— 不允许空原因静默回落到 "explicit_waiver"，
     否则审计分不清"人忘了填"和"人明确豁免"；
  3. **部分失败不中断** —— 成功项照常发布，失败项带业务原因回传，已发布的不回滚。

隔离：SkillStore 与审计文件都落在 tmp_path；不经 create_manual 造数（避免
advisory 评估/自动分类在 CI 高负载下拖慢，见 test_skills_mgmt._seed_skill 的注释）。
"""
from __future__ import annotations

import json

import pytest

from agent.skills_mgmt.exceptions import SkillValidationError
from agent.skills_mgmt.models import Skill, SkillStatus
from agent.skills_mgmt.service import WAIVER_MIN_REASON_LEN, SkillsMgmtService

AUDIT_ENV = "SKILLS_REVIEW_AUDIT_FILE"


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv(AUDIT_ENV, str(tmp_path / "audit.jsonl"))
    return SkillsMgmtService(store_path=str(tmp_path / "skills.json"))


def _seed(svc, skill_id: str) -> Skill:
    """直接落库造数（外来源草稿：source 是 url: 前缀，status=draft）。"""
    skill = Skill.from_storage_dict({
        "id": skill_id,
        "name": skill_id,
        "description": "外来草稿",
        "content": "# draft\n",
        "content_type": "text",
        "category": "custom",
        "tags": [],
        "author": "tester",
        "source": "url:https://example.com/skill.zip",
    })
    svc.store.upsert(skill)
    return skill


def _audit(tmp_path):
    path = tmp_path / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ─── 请求校验：不接受"全部"语义 ───


def test_rejects_empty_ids(svc):
    with pytest.raises(SkillValidationError, match="ids 不能为空"):
        svc.publish_batch([])


def test_rejects_string_instead_of_list(svc):
    """传 "all" / "ext-a" 这类裸字符串必须拒绝：它就是"一键全放"的入口。"""
    with pytest.raises(SkillValidationError, match="字符串数组"):
        svc.publish_batch("all")


def test_rejects_non_string_members(svc):
    with pytest.raises(SkillValidationError, match="字符串数组"):
        svc.publish_batch(["ext-a", 42])


def test_rejects_over_limit(svc):
    from agent.skills_mgmt.service import PUBLISH_BATCH_MAX

    with pytest.raises(SkillValidationError, match="最多放行"):
        svc.publish_batch(["s%d" % i for i in range(PUBLISH_BATCH_MAX + 1)])


# ─── force 必须带原因 ───


def test_force_without_reason_rejected(svc):
    _seed(svc, "ext-a")

    with pytest.raises(SkillValidationError, match="force 豁免必须填写原因"):
        svc.publish_batch(["ext-a"], force=True, reason="")
    with pytest.raises(SkillValidationError, match="force 豁免必须填写原因"):
        svc.publish_batch(["ext-a"], force=True,
                          reason="x" * (WAIVER_MIN_REASON_LEN - 1))

    # 校验失败不得留下任何副作用（状态还是草稿、没写审计）
    assert svc.get("ext-a").status == SkillStatus.DRAFT


# ─── 正常路径 ───


def test_force_publishes_each_item_and_writes_audit(svc, tmp_path):
    _seed(svc, "ext-a")
    _seed(svc, "ext-b")

    result = svc.publish_batch(["ext-a", "ext-b", "ext-a"], force=True,
                               reason="人工复核通过")

    assert result["ok"] is True
    assert result["force"] is True
    assert result["total"] == 2, "重复勾选要去重，而不是放行两次"
    assert result["published"] == ["ext-a", "ext-b"]
    assert result["failed"] == []
    assert svc.get("ext-a").status == SkillStatus.PUBLISHED
    assert svc.get("ext-b").status == SkillStatus.PUBLISHED

    recs = _audit(tmp_path)
    assert [r["skill_id"] for r in recs] == ["ext-a", "ext-b"]
    assert all(r["event"] == "review_waiver_publish" for r in recs)
    assert all(r["reason"] == "人工复核通过" for r in recs)


def test_without_force_the_gate_still_blocks(svc):
    _seed(svc, "ext-a")

    result = svc.publish_batch(["ext-a"])

    assert result["ok"] is False
    assert result["published"] == []
    assert result["failed"][0]["id"] == "ext-a"
    assert result["failed"][0]["code"] == "SKILL_REVIEW_REJECTED"
    assert svc.get("ext-a").status == SkillStatus.DRAFT


def test_partial_failure_does_not_block_the_rest(svc):
    _seed(svc, "ext-ok")

    result = svc.publish_batch(["ext-ok", "missing"], force=True,
                               reason="人工复核通过")

    assert result["ok"] is False
    assert result["published"] == ["ext-ok"]
    assert [f["id"] for f in result["failed"]] == ["missing"]
    assert result["failed"][0]["code"] == "SKILL_NOT_FOUND"
    assert svc.get("ext-ok").status == SkillStatus.PUBLISHED
