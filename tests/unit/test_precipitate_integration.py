"""TASK-04 集成测试 · 知识→技能沉淀完整闭环

覆盖（端到端主链路，串联 Step1 连接器 + Step2 调度 + 质量门控 + 审计）:
    1. 知识卡片（approved: current+distilled）→ skill_bridge → Skill DRAFT（落盘）
    2. 记忆条目 → MemorySkillAbstractor → 草稿（质量门控）
    3. PrecipitateScheduler（env 开启）→ 调度执行 → 审计日志（event=precipitate_draft）
    4. 沉淀草稿不落盘、不注册、不进发布审核链（设计 R2）
    5. max_skills 截断日志含被跳过 cluster_id 列表（防旧版日志覆盖回归）

守【不易】: 全部使用 tmp_path 隔离（CardStore/SkillStore/审计路径）；
抽象器注入 _FakeSkillsService（list_all 恒空）避免触碰真实 data/；
任务注册后必须 unschedule 清理。
"""

from __future__ import annotations

import functools
import json

import pytest

from agent.knowledge.card import CardStore
from agent.knowledge.schema import Card, slugify
from agent.knowledge.skill_bridge import KnowledgeSkillBridge
from agent.skills_mgmt.memory_abstractor import MemoryEntry, MemorySkillAbstractor
from agent.skills_mgmt.models import SkillStatus
from agent.skills_mgmt.precipitate import PrecipitateScheduler
from agent.skills_mgmt.store import SkillStore

ENV_ENABLED = "LEARNING_PRECIPITATE_ENABLED"


class _FakeSkillsService:
    """只读假服务：无已有技能 → 去重判定恒 dup_OK，不触碰真实 data/。"""

    def list_all(self):
        return []


def _make_approved_card(title: str, *, content: str = "核心要点\n- 要点1\n- 要点2") -> Card:
    """approved 卡片（status=current + metadata.distilled=True，裁决 R1）。"""
    return Card(
        title=title,
        slug=slugify(title),
        status="current",
        type="concepts",
        source="inbox/test.md",
        date="2026-08-02",
        tags=["t1"],
        insight="一句话核心洞见",
        content=content,
        metadata={"distilled": True},
    )


def _make_entries():
    """8 条记忆 → 2 组聚类（deploy x4 / backup x4），组内 Jaccard≥0.5 合并。"""
    t1 = [
        "deploy the nginx web service to the production cluster",
        "deploy the nginx web server to production cluster with health check",
        "deploy nginx service into production cluster and verify traffic",
        "rolling deploy of nginx web service on production cluster",
    ]
    t2 = [
        "backup the postgres database to the archive bucket",
        "scheduled backup of postgres database into archive storage",
        "backup postgres database and upload to archive bucket daily",
        "nightly backup of the postgres database to archive bucket",
    ]
    return [
        MemoryEntry(
            source="workflow", source_id=f"wf-{i}", task_text=t, success=True,
            params={"target": "prod" if i < 4 else "archive"},
        )
        for i, t in enumerate(t1 + t2)
    ]


def _abstractor_with_entries():
    """抽象器：注入固定记忆 + 假服务，确定性可复现。"""
    abstractor = MemorySkillAbstractor(
        skills_service=_FakeSkillsService(), enable_signal_scoring=False)
    abstractor.abstract_new_skills = functools.partial(
        abstractor.abstract_new_skills, memory_entries=_make_entries())
    return abstractor


@pytest.fixture()
def isolated(tmp_path):
    """隔离的卡片库 + 技能库 + 审计路径。"""
    card_store = CardStore(str(tmp_path / "wiki"))
    skill_store = SkillStore(str(tmp_path / "skills.json"))
    audit = tmp_path / "precipitate_audit.jsonl"
    return card_store, skill_store, audit


@pytest.fixture(autouse=True)
def _env_cleanup(monkeypatch):
    """每个用例清掉沉淀开关 env；结束清理可能注册的任务。"""
    monkeypatch.delenv(ENV_ENABLED, raising=False)
    yield
    try:
        PrecipitateScheduler().unschedule()
    except Exception:  # noqa: BLE001 清理失败不阻断
        pass


def _read_audit(audit_path):
    return [json.loads(line) for line in
            audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_full_pipeline_knowledge_to_skill_to_audit(isolated, monkeypatch):
    """端到端闭环：知识卡片 → DRAFT → 记忆抽象草稿 → 调度审计（不落盘、不进审核链）。"""
    card_store, skill_store, audit = isolated

    # 1) 知识→技能：approved 卡片 → Skill DRAFT 落盘（status=draft）
    card = _make_approved_card("生产部署技能")
    card_store.create(card)
    bridge = KnowledgeSkillBridge(card_store=card_store, skills_store=skill_store)
    skill_id = bridge.card_to_skill_draft(card)
    assert skill_id
    assert skill_store.get(skill_id).status == SkillStatus.DRAFT

    # 2) 记忆→草稿 + 3) 调度：开启 env → 注册 → 执行
    monkeypatch.setenv(ENV_ENABLED, "true")
    sched = PrecipitateScheduler(abstractor=_abstractor_with_entries(),
                                 audit_path=str(audit))
    result = sched.schedule(interval_hours=1, days=7, max_skills=1)
    assert result["status"] == "scheduled"
    sched._scheduled_run()

    # 断言：审计 1 条（质量门控通过的草稿），registered=False（不落盘）
    recs = _read_audit(audit)
    assert len(recs) == 1
    assert recs[0]["event"] == "precipitate_draft"
    assert recs[0]["registered"] is False
    assert recs[0]["draft_skill_id"].startswith("mem-")

    # 断言：沉淀草稿未进技能库（技能库仅有知识桥那 1 个 DRAFT，无 mem-*）
    assert skill_store.count() == 1
    assert not any(s.id.startswith("mem-") for s in skill_store.list_all())


def test_max_skills_truncation_only_first_cluster_audited(isolated, monkeypatch, caplog):
    """max_skills=1：8 条记忆 2 组聚类 → 仅处理首个并审计；截断日志含被跳过 cluster_id。"""
    _, _, audit = isolated

    monkeypatch.setenv(ENV_ENABLED, "true")
    sched = PrecipitateScheduler(abstractor=_abstractor_with_entries(),
                                 audit_path=str(audit))
    assert sched.schedule(interval_hours=1, days=7, max_skills=1)["status"] == "scheduled"

    with caplog.at_level("INFO", logger="agent.skills_mgmt"):
        sched._scheduled_run()

    # 仅首个聚类被处理并审计
    assert len(_read_audit(audit)) == 1

    # 截断日志包含被跳过 cluster_id 列表（防旧版日志覆盖回归）
    assert "已达 max_skills=1 上限" in caplog.text
    assert "被跳过 cluster_id=" in caplog.text
    assert "cl-" in caplog.text
