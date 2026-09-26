"""RUNBOOK-1 护栏：S1-02 重建 → S3-01 入轨 → 再重建 = 台账**不动点**

【这条护栏防的是什么（LEDGER-1 §5 复发风险「中」）】
    LEDGER-1 判定 (b)：M8 重建台账时 `run_backfill` 新 wire 了一条存量资产
    `cp.skill.skill`（stage=None 是 S1-02 的设计 —— stage 归 S3-01 管），
    但**没有人随后跑 S3-01 首次入轨收口**，于是
    `test_s3_01_handover.py::TestL4StageIngestion::test_real_ledger_has_no_unstaged_asset`
    变红。LEDGER-1 把这条红在数据层修好了，却明确登记「**收口动作不在代码里**」：
    任何一次「主轨新增技能 + 重跑 S1-02」都会**重新制造同一条红**。

【本用例怎么把它挡在代码层】
    在 **tmp 台账**上把整条流水线原样跑一遍（重建 → 入轨 → 再重建），断言：
      1) 每一步之后台账里**没有任何资产 stage=None**（= 那条红守的不变量）；
      2) 「重建 → 入轨 → 再重建」之后台账**逐字节不变**（sha256 相同）；
      3) 关掉自动收口（`ingest_stages=False`）时，新 wire 的资产**必须**被
         `s3_01_followup` 如实报出来（含待收口清单与处置命令）——
         即「再制造同一条红」在**不改代码**的前提下也已经是**可观测**的。

【非空转自证】
    本文件的 3 条用例全部依赖 RUNBOOK-1 新增的 `newly_wired` / `s3_01_followup`
    字段与 `ingest_stages=` 形参；把改动去掉后它们会以 KeyError/TypeError 变红
    （见 docs/audit_skill_governance/RUNBOOK1.md ④）。

【为什么不碰生产台账】
    全程只用 tmp_path 下的迷你主轨 + 台账，断言只读 tmp 台账；
    `data/descriptors.json` **一次都没有被读或写**（探针与回归前后 sha256 已对照）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent.descriptors.backfill import plan_backfill, run_backfill
from agent.descriptors.registry import DescriptorRegistry

INGEST_CMD = "python scripts/run_s3_01_ingest.py --execute"


# ════════════════════════════════════════════════════════════
#  fixtures：把运行期落点全部关进 tmp_path
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _isolated_runtime_sinks(tmp_path, monkeypatch):
    """事件流 + 审计链隔离（审计链多绑一道，不依赖会话级守卫是否在位）"""
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    import agent.observability.events as events_mod
    events_mod.reset_event_stores()

    from agent.audit import facade as facade_mod
    from agent.audit.chain import AuditChain
    chain = AuditChain(str(tmp_path / "audit_chain.db"),
                       roots_path=str(tmp_path / "roots.jsonl"),
                       signing_key_path=str(tmp_path / "k.pem"), auto_seal=False)
    previous = facade_mod.audit.bind(chain)
    old_enabled = facade_mod.audit.enabled
    facade_mod.audit.enabled = True
    try:
        yield chain
    finally:
        facade_mod.audit.enabled = old_enabled
        facade_mod.audit.bind(previous)
        try:
            chain.close(timeout=2.0)
        finally:
            events_mod.reset_event_stores()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stage_empty(ledger: Path):
    reg = DescriptorRegistry(path=ledger)
    reg.load()
    return sorted(d.capability_id for d in reg.list() if not d.evolution.stage)


def _make_inputs(tmp_path: Path):
    """迷你主轨（含 LEDGER-1 那条 `skill` 的等价物）+ 空文件轨"""
    main = tmp_path / "skills_mgmt.json"
    repo = tmp_path / "skills_repo"
    repo.mkdir(exist_ok=True)
    main.write_text(json.dumps({
        "skill": {"id": "skill", "name": "易之三义", "category": "custom",
                  "source": "manual", "status": "approved", "author": "workbench",
                  "description": "三义", "content": "指令型内容",
                  "config_schema": {"type": "object", "properties": {}},
                  "output_schema": {}, "tags": [], "is_sensitive": False},
        "alpha-local": {"id": "alpha-local", "name": "alpha-local",
                        "category": "custom", "source": "manual",
                        "status": "published", "author": "workbench",
                        "description": "本地技能", "content": "指令型内容",
                        "config_schema": {"type": "object", "properties": {}},
                        "output_schema": {}, "tags": [], "is_sensitive": False},
    }, ensure_ascii=False), encoding="utf-8")
    return main, repo


def _rebuild(main: Path, repo: Path, ledger: Path, **kw):
    """一次「重建」= plan_backfill + run_backfill（M8 重跑 S1-02 的同一条路径）"""
    return run_backfill(plan_backfill(main_path=main, repo_path=repo),
                        registry_path=ledger, batch_size=1, **kw)


# ════════════════════════════════════════════════════════════
#  ① 不动点：重建 → 入轨 → 再重建，台账逐字节不变
# ════════════════════════════════════════════════════════════


class TestPipelineFixpoint:
    def test_rebuild_ingest_rebuild_is_bytewise_fixpoint(self, tmp_path):
        main, repo = _make_inputs(tmp_path)
        ledger = tmp_path / "descriptors.json"

        # ── 第 1 步：重建（S1-02）—— 新 wire 出 stage=None 的条目 ──
        r1 = _rebuild(main, repo, ledger, ingest_stages=True)
        wired = sorted(w["capability_id"] for w in r1["newly_wired"])
        assert wired == ["cp.skill.alpha-local", "cp.skill.skill"], (
            f"本次重建应新 wire 2 条资产，实际 {wired}")
        assert all(w["register_action"] == "created" for w in r1["newly_wired"])

        # ── 第 2 步：收口（S3-01）—— 同一调用内自动完成 ──
        fw = r1["s3_01_followup"]
        assert fw["auto_executed"] is True, "ingest_stages=True 却未自动收口"
        assert fw["required"] is False, f"收口后仍有待办: {fw}"
        assert sorted(i["capability_id"] for i in fw["ingested"]) == wired
        assert _stage_empty(ledger) == [], "收口后台账仍有无 stage 资产"
        fixed = _sha256(ledger)

        # ── 第 3 步：再重建（同数据、同台账）—— 必须逐字节不变 ──
        r2 = _rebuild(main, repo, ledger, ingest_stages=True)
        assert r2["newly_wired"] == [], "第二次重建不应再 wire 任何资产"
        assert r2["applied"]["no_op"] == 2
        assert r2["s3_01_followup"]["ingested"] == []
        assert r2["s3_01_followup"]["required"] is False
        assert _sha256(ledger) == fixed, (
            "「重建 → 入轨 → 再重建」不是不动点：台账字节被改写\n"
            f"  收口后 sha={fixed}\n  再重建后 sha={_sha256(ledger)}")
        assert _stage_empty(ledger) == []

        # ── 连跑第 3 次（对应 LEDGER-1「入轨连跑 3 次 sha 恒定」）──
        r3 = _rebuild(main, repo, ledger, ingest_stages=True)
        assert r3["applied"]["no_op"] == 2
        assert _sha256(ledger) == fixed
        assert _stage_empty(ledger) == []

    def test_default_call_does_not_ingest_but_reports(self, tmp_path):
        """缺省（ingest_stages=False）⇒ 零行为变化，但待收口清单必须被报出来"""
        main, repo = _make_inputs(tmp_path)
        ledger = tmp_path / "descriptors.json"

        r = _rebuild(main, repo, ledger)          # 不带 ingest_stages
        fw = r["s3_01_followup"]
        assert fw["auto_executed"] is False
        assert fw["required"] is True
        assert fw["stage_empty"] == ["cp.skill.alpha-local", "cp.skill.skill"]
        assert set(fw["newly_wired"]) == set(fw["stage_empty"])
        assert fw["command"] == INGEST_CMD
        assert INGEST_CMD in fw["reason"]
        assert "test_real_ledger_has_no_unstaged_asset" in fw["reason"]

        # 这条红在 tmp 台账上的等价复现（= 生产那条断言的同一条不变量）
        assert _stage_empty(ledger) == ["cp.skill.alpha-local", "cp.skill.skill"]

        # 按提示处置后清零（且再跑一次仍是不动点）
        _rebuild(main, repo, ledger, ingest_stages=True)
        assert _stage_empty(ledger) == []
        fixed = _sha256(ledger)
        _rebuild(main, repo, ledger, ingest_stages=True)
        assert _sha256(ledger) == fixed

    def test_dry_run_reports_but_never_writes(self, tmp_path):
        main, repo = _make_inputs(tmp_path)
        ledger = tmp_path / "descriptors.json"

        d1 = _rebuild(main, repo, ledger, dry_run=True, ingest_stages=True)
        d2 = _rebuild(main, repo, ledger, dry_run=True, ingest_stages=True)
        assert not ledger.exists(), "干跑不得落库（台账文件被创建）"
        fw = d1["s3_01_followup"]
        assert fw["auto_executed"] is False, "干跑绝不自动收口"
        assert fw["required"] is True
        assert fw["stage_empty"] == ["cp.skill.alpha-local", "cp.skill.skill"]
        # 干跑两次结果一致（既有确定性契约不被新增字段破坏）
        assert json.dumps({k: v for k, v in d1.items() if k != "run_id"},
                          ensure_ascii=False, sort_keys=True, default=str) == \
            json.dumps({k: v for k, v in d2.items() if k != "run_id"},
                       ensure_ascii=False, sort_keys=True, default=str)

    def test_guard_has_teeth_when_pipeline_stops_closing_the_loop(
            self, tmp_path, monkeypatch):
        """变异自证：把收口打成空转（= 「收口又被摘出流水线」）⇒ 护栏核心断言必红

        Why 单列一条：另外两条红了只说明「新 API 不在」；本用例证明**不变量断言
        本身有牙** —— 只要流水线不再真正收口，`_stage_empty(ledger) == []` 就会
        失败，而它正是 LEDGER-1 那条红在 tmp 台账上的等价形态。
        """
        import agent.digestion.stage as stage_mod

        def _never_closes(reg, **kw):
            return {"ingested": [], "failed": [],
                    "residual": [d.capability_id for d in reg.list()
                                 if not d.evolution.stage],
                    "policies": {}}

        monkeypatch.setattr(stage_mod, "backfill_stages", _never_closes)
        main, repo = _make_inputs(tmp_path)
        ledger = tmp_path / "descriptors.json"

        r = _rebuild(main, repo, ledger, ingest_stages=True)
        assert r["s3_01_followup"]["auto_executed"] is True
        assert r["s3_01_followup"]["required"] is True
        assert r["s3_01_followup"]["stage_empty"] == [
            "cp.skill.alpha-local", "cp.skill.skill"]
        # 流水线不真收口 ⇒ 台账残留 stage=None ⇒ 第 1 条用例的核心断言必然失败
        with pytest.raises(AssertionError):
            assert _stage_empty(ledger) == [], "收口后台账仍有无 stage 资产"
