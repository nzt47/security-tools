# -*- coding: utf-8 -*-
"""F11-C-2 · 工作流自动执行「证据 / 偏好」两分（门槛看证据、排序看偏好）

背景（F11-C-2 §1 实测，见 docs/audit_skill_governance/F11C2.md）：
改前**同一个** combined = sim × confidence × (0.5 + priority/200) 既当排序键、
又当 executor 的门槛判据 ⇒ priority（默认 50）在乘性门槛里把候选分数整体压低
25%~50%，而 confidence 只能靠"执行成功"增长 ⇒ 「低信心 ⇒ 需要更相似 ⇒ 执行不了
⇒ 信心不涨」的**闭环**（实测：prio=0/conf=0.5 的条目**连逐字重放**都过不了门槛）。

本卡裁定（③+④）：
    · 门槛看**证据**：evidence = sim × confidence，与 min_score 比较；
    · 排序看**偏好**：combined = sim × confidence × (0.5 + priority/200)（顺序不变）；
    · 语义（④）：自动执行只在「高相似 + 有一定信心的重复场景」成立，
      改写场景**默认交给 LLM**；
    · 逃生开关 WORKFLOW_LEARNING_GATE_ON_EVIDENCE=0 ⇒ 回到旧乘性门槛。

【数据隔离】全部用例只写 tmp_path 下的仓库文件，**不触碰** data/learned_workflows.json。
"""

from __future__ import annotations

import json
import logging

import pytest

from agent.workflow_learning import admission
from agent.workflow_learning.learner import canonical_signature, trigger_tokens
from agent.workflow_learning.matcher import (
    MatchScore,
    WorkflowMatcher,
    gate_on_evidence,
    score_candidate,
)
from agent.workflow_learning.models import (
    LearnedWorkflow,
    WorkflowStatus,
    WorkflowStep,
)
from agent.workflow_learning.repository import WorkflowRepository
from agent.workflow_learning.service import WorkflowLearningService

_ENV = "WORKFLOW_LEARNING_GATE_ON_EVIDENCE"

#: 任务句（与 F11-C-2 实测探针同一批语料口径）
TASK_ARCHIVE = "把仓库里的日志文件按天归档压缩"


# ═══════════════════════════════════════════════════════════════════
#  夹具
# ═══════════════════════════════════════════════════════════════════

def _entry(wf_id: str, src: str, *, conf: float, prio: int, sc: int,
           tools=("list_directory", "compress"), name: str = "") -> LearnedWorkflow:
    return LearnedWorkflow(
        id=wf_id,
        name=name or ("测试: " + src[:8]),
        description="F11-C-2 测试条目",
        task_signature=canonical_signature(src),
        trigger_patterns=admission.effective_trigger_patterns(trigger_tokens(src)),
        steps=[WorkflowStep(step_id="step_%d" % (i + 1), tool_name=t)
               for i, t in enumerate(tools)],
        source_session_id="sess-f11c2",
        source_user_input=src,
        success_count=sc, failure_count=0, confidence=conf, priority=prio,
        status=WorkflowStatus.ACTIVE, enabled=True,
    )


def _svc(tmp_path, entries, *, tool: bool = True) -> WorkflowLearningService:
    path = str(tmp_path / "wf.json")
    repo = WorkflowRepository(path=path)
    for wf in entries:
        repo.upsert(wf)
    return WorkflowLearningService(
        repo_path=path,
        tool_executor=(lambda name, params: {"ok": True, "tool": name}) if tool else None,
    )


def _sim(svc: WorkflowLearningService, text: str, wf_id: str) -> float:
    """索引层相似度（召回读数；与 matcher.match 的召回口径同源）"""
    return float(dict(svc.matcher._index.query(text, top_k=5)).get(wf_id, 0.0))


class _Cap(logging.Handler):
    def __init__(self):
        logging.Handler.__init__(self)
        self.records = []

    def emit(self, record):
        try:
            self.records.append(record.getMessage())
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture
def captured():
    """捕获 workflow_learning 的结构化日志（取 ctx 原始读数）"""
    cap = _Cap()
    lg = logging.getLogger("agent.workflow_learning")
    old_level = lg.level
    lg.addHandler(cap)
    lg.setLevel(logging.INFO)
    try:
        yield cap
    finally:
        lg.removeHandler(cap)
        lg.setLevel(old_level)


def _ctx(cap: _Cap, action: str):
    out = []
    for line in cap.records:
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if rec.get("action") == action:
            out.append(rec)
    return out


# ═══════════════════════════════════════════════════════════════════
#  1. 两个分数各自显式算出（不许"除法还原"）
# ═══════════════════════════════════════════════════════════════════

class TestTwoScores:

    def test_evidence与combined按定义各自成立(self):
        wf = _entry("probe-a", TASK_ARCHIVE, conf=0.5, prio=50, sc=2)
        ms = score_candidate(0.8, wf)
        assert isinstance(ms, MatchScore)
        assert ms.confidence_factor == pytest.approx(0.5)
        assert ms.priority_factor == pytest.approx(0.75)
        # 门槛分：sim × confidence（**不含** priority）
        assert ms.evidence == pytest.approx(0.8 * 0.5)
        # 排序分：sim × confidence × priority_factor
        assert ms.combined == pytest.approx(0.8 * 0.5 * 0.75)

    def test_证据分与priority无关(self):
        """同一 similarity/confidence，priority 只改 combined —— 绝不改 evidence"""
        sim, conf = 0.62, 0.5
        ev, cb = [], []
        for prio in (0, 25, 50, 75, 100):
            wf = _entry("probe-p%d" % prio, TASK_ARCHIVE, conf=conf, prio=prio, sc=2)
            ms = score_candidate(sim, wf)
            ev.append(ms.evidence)
            cb.append(ms.combined)
        assert len(set(ev)) == 1, "证据分不得随 priority 变化: %s" % ev
        assert ev[0] == pytest.approx(sim * conf)
        assert cb == sorted(cb) and cb[0] < cb[-1], (
            "排序分必须随 priority 单调变化: %s" % cb)

    def test_冷启动口径同时作用于两个分数(self):
        """success_count == 0 ⇒ confidence_factor = 1.0（既有冷启动修复，不改）"""
        cold = _entry("probe-cold", TASK_ARCHIVE, conf=0.4, prio=50, sc=0)
        ms = score_candidate(0.5, cold)
        assert ms.cold_start is True
        assert ms.confidence_factor == pytest.approx(1.0)
        assert ms.evidence == pytest.approx(0.5)
        assert ms.combined == pytest.approx(0.5 * 0.75)
        # 执行过一次后：confidence 参与两个分数
        warm = _entry("probe-warm", TASK_ARCHIVE, conf=0.5, prio=50, sc=1)
        ms2 = score_candidate(0.5, warm)
        assert ms2.cold_start is False
        assert ms2.evidence == pytest.approx(0.25)
        assert ms2.combined == pytest.approx(0.1875)

    def test_默认形状与改前一致_可选参数才带证据分(self, tmp_path):
        svc = _svc(tmp_path, [_entry("probe-a", TASK_ARCHIVE, conf=0.5, prio=50, sc=2)])
        default = svc.matcher.match(TASK_ARCHIVE, top_k=3)
        assert default, "同句必须能召回"
        for item in default:                       # 形状 = [(wf, combined)]
            assert len(item) == 2
            assert isinstance(item[1], float)
        with_ev = svc.matcher.match(TASK_ARCHIVE, top_k=3, with_evidence=True)
        for item in with_ev:                       # 形状 = [(wf, combined, evidence)]
            assert len(item) == 3
        assert [x[1] for x in default] == [x[1] for x in with_ev]
        assert [x[0].id for x in default] == [x[0].id for x in with_ev]
        scored = svc.matcher.match_scored(TASK_ARCHIVE, top_k=3)
        assert [s.combined for _, s in scored] == [x[1] for x in default]
        assert [s.evidence for _, s in scored] == [x[2] for x in with_ev]

    def test_两个分数不是互相反推出来的(self, tmp_path):
        """同样 sim/confidence、不同 priority ⇒ evidence 必须逐位相同

        若实现用 combined / priority_factor 反推证据分，priority_factor 一旦
        与 combined 的算式脱钩就会静默错位 —— 本用例锁死"各自独立算出"。
        """
        entries = [_entry("probe-p0", TASK_ARCHIVE, conf=0.5, prio=0, sc=2),
                   _entry("probe-p100", TASK_ARCHIVE, conf=0.5, prio=100, sc=2)]
        svc = _svc(tmp_path, entries)
        scored = dict((w.id, s) for w, s in svc.matcher.match_scored(TASK_ARCHIVE, top_k=5))
        a, b = scored["probe-p0"], scored["probe-p100"]
        assert a.similarity == pytest.approx(b.similarity)
        assert a.evidence == pytest.approx(b.evidence)
        assert a.combined == pytest.approx(b.evidence * 0.5)
        assert b.combined == pytest.approx(b.evidence * 1.0)


# ═══════════════════════════════════════════════════════════════════
#  2. 门槛按证据分（默认）与逃生开关
# ═══════════════════════════════════════════════════════════════════

class TestGateOnEvidence:

    @staticmethod
    def _case(tmp_path):
        """构造"旧口径卡住、新口径放行"的用例（阈值由**实测相似度**自校准）"""
        wf = _entry("probe-lock", TASK_ARCHIVE, conf=0.5, prio=0, sc=2)
        svc = _svc(tmp_path, [wf])
        sim = _sim(svc, TASK_ARCHIVE, wf.id)
        evidence = sim * 0.5           # 新口径：sim × confidence
        combined = sim * 0.5 * 0.5     # 旧口径：再乘 priority_factor(prio=0 ⇒ 0.5)
        assert sim >= svc.matcher.min_similarity, (
            "用例前提：必须被召回（实测 sim=%.4f）" % sim)
        mid = (combined + evidence) / 2.0
        assert combined < mid <= evidence, (
            "用例前提：阈值必须落在两条口径之间（sim=%.4f）" % sim)
        return svc, sim, evidence, combined, mid

    def test_默认按证据分放行_旧口径会卡住(self, tmp_path):
        svc, sim, evidence, combined, mid = self._case(tmp_path)
        r = svc.try_execute(TASK_ARCHIVE, min_score=mid)
        assert r.matched is True, (
            "证据分 evidence=%.4f ≥ %.4f 应放行（旧口径 combined=%.4f 会卡住）"
            % (evidence, mid, combined))

    def test_逃生开关置0恢复旧乘性门槛(self, tmp_path, monkeypatch):
        monkeypatch.setenv(_ENV, "0")
        assert gate_on_evidence() is False
        svc, sim, evidence, combined, mid = self._case(tmp_path)
        r = svc.try_execute(TASK_ARCHIVE, min_score=mid)
        assert r.matched is False, (
            "置 0 必须回到旧乘性门槛：combined=%.4f < %.4f" % (combined, mid))

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", "disable",
                                     "OFF", " off ", "False"])
    def test_逃生开关关闭取值(self, raw, monkeypatch):
        monkeypatch.setenv(_ENV, raw)
        assert gate_on_evidence() is False

    @pytest.mark.parametrize("raw", ["1", "true", "yes", "on", "enable", ""])
    def test_逃生开关开启取值(self, raw, monkeypatch):
        monkeypatch.setenv(_ENV, raw)
        assert gate_on_evidence() is True

    def test_未设置时默认新口径(self, monkeypatch):
        monkeypatch.delenv(_ENV, raising=False)
        assert gate_on_evidence() is True

    def test_priority0条目不再结构性锁死(self, tmp_path):
        """改前：prio=0 + conf 0.5 ⇒ 门槛需要 sim ≥ 1.0 ⇒ **连逐字重放都不执行**"""
        wf = _entry("probe-lock2", TASK_ARCHIVE, conf=0.5, prio=0, sc=2)
        svc = _svc(tmp_path, [wf])
        sim = _sim(svc, TASK_ARCHIVE, wf.id)
        need_old = 0.25 / (0.5 * 0.5)
        assert need_old >= 1.0, "旧口径所需 sim=%.4f" % need_old
        assert sim < need_old, "该用例要求逐字重放也过不了旧门槛（sim=%.4f）" % sim
        assert svc.try_execute(TASK_ARCHIVE, min_score=0.25).matched is True

    def test_改写场景仍默认交给LLM(self, tmp_path, monkeypatch):
        """④ 语义：断言门槛**不该**被放宽到"任意改写都直接跑工作流" """
        wf = _entry("probe-rewrite", TASK_ARCHIVE, conf=0.5, prio=50, sc=2)
        svc = _svc(tmp_path, [wf])
        # 与任务句关系很远的改写句：要么未被召回，要么被证据分挡在门外
        r = svc.try_execute("把数据库备份到对象存储并校验校验和", min_score=0.25)
        assert r.matched is False, "语义无关的输入不得自动执行本地工作流"


# ═══════════════════════════════════════════════════════════════════
#  3. 排序不变（priority 仍在排序里起作用）
# ═══════════════════════════════════════════════════════════════════

class TestOrderingUnchanged:

    _IDS = ["probe-ord-p100", "probe-ord-p50", "probe-ord-p0"]

    def _entries(self):
        # 索引文本**逐字相同** ⇒ similarity 相同；只有 priority 不同
        return [_entry("probe-ord-p100", TASK_ARCHIVE, conf=0.5, prio=100, sc=2, name="排序 p100"),
                _entry("probe-ord-p50", TASK_ARCHIVE, conf=0.5, prio=50, sc=2, name="排序 p50"),
                _entry("probe-ord-p0", TASK_ARCHIVE, conf=0.5, prio=0, sc=2, name="排序 p0")]

    def test_sim相同priority不同_顺序仍由priority决定(self, tmp_path):
        svc = _svc(tmp_path, self._entries())
        scored = svc.matcher.match_scored(TASK_ARCHIVE, top_k=3)
        assert [w.id for w, _ in scored] == self._IDS, (
            "priority 必须仍在排序里起作用（高者靠前）")
        sims = [s.similarity for _, s in scored]
        assert len(set(round(x, 9) for x in sims)) == 1, "构造前提：三者 sim 相同"
        cbs = [s.combined for _, s in scored]
        assert cbs == sorted(cbs, reverse=True) and cbs[0] > cbs[-1]

    def test_两种口径下候选顺序逐个相同(self, tmp_path, monkeypatch):
        entries = self._entries()
        svc_new = _svc(tmp_path / "new", entries)
        order_new = [w.id for w, _ in svc_new.matcher.match(TASK_ARCHIVE, top_k=3)]
        combined_new = [c for _, c in svc_new.matcher.match(TASK_ARCHIVE, top_k=3)]

        monkeypatch.setenv(_ENV, "0")
        svc_old = _svc(tmp_path / "old", entries)
        order_old = [w.id for w, _ in svc_old.matcher.match(TASK_ARCHIVE, top_k=3)]
        combined_old = [c for _, c in svc_old.matcher.match(TASK_ARCHIVE, top_k=3)]

        assert order_new == order_old == self._IDS
        assert combined_new == combined_old, (
            "排序键必须逐位不变: %s vs %s" % (combined_new, combined_old))

    def test_召回集合不变(self, tmp_path, monkeypatch):
        entries = self._entries() + [
            _entry("probe-other", "把数据库备份到对象存储并校验校验和",
                   conf=0.6, prio=50, sc=3, tools=("shell_execute", "shell_execute"))]
        ids_new = [w.id for w, _ in _svc(tmp_path / "n", entries).matcher.match(
            TASK_ARCHIVE, top_k=5)]
        monkeypatch.setenv(_ENV, "0")
        ids_old = [w.id for w, _ in _svc(tmp_path / "o", entries).matcher.match(
            TASK_ARCHIVE, top_k=5)]
        assert ids_new == ids_old


# ═══════════════════════════════════════════════════════════════════
#  4. 门槛决策可审计（ctx 里能复盘"为什么过/没过"）
# ═══════════════════════════════════════════════════════════════════

class TestAuditContext:

    _KEYS = ("similarity", "confidence", "confidence_factor", "priority",
             "priority_factor", "evidence", "combined", "gate_field",
             "gate_value", "min_score", "cold_start")

    def test_通过时的审计字段(self, tmp_path, captured):
        svc = _svc(tmp_path, [_entry("probe-a", TASK_ARCHIVE, conf=0.5, prio=50, sc=2)])
        captured.records.clear()
        r = svc.try_execute(TASK_ARCHIVE, min_score=0.25)
        assert r.matched is True
        recs = _ctx(captured, "wf_try_execute.end")
        assert recs, "必须有 wf_try_execute 的追踪记录"
        rec = recs[-1]
        for k in self._KEYS:
            assert k in rec, "审计字段缺失: %s（实际键: %s）" % (k, sorted(rec))
        assert rec["gate_field"] == "evidence"
        assert rec["min_score"] == pytest.approx(0.25)
        assert rec["evidence"] == pytest.approx(rec["similarity"] * rec["confidence_factor"])
        assert rec["combined"] == pytest.approx(
            rec["evidence"] * rec["priority_factor"])
        assert rec["gate_value"] == pytest.approx(rec["evidence"])

    def test_被卡住时的审计字段与reason(self, tmp_path, captured, monkeypatch):
        monkeypatch.setenv(_ENV, "0")      # 旧口径更容易被卡住，故用它造"卡住"
        wf = _entry("probe-block", TASK_ARCHIVE, conf=0.5, prio=0, sc=2)
        svc = _svc(tmp_path, [wf])
        sim = _sim(svc, TASK_ARCHIVE, wf.id)
        threshold = (sim * 0.5 * 0.5 + sim * 0.5) / 2.0    # 旧卡住/新放行之间
        captured.records.clear()
        r = svc.try_execute(TASK_ARCHIVE, min_score=threshold)
        assert r.matched is False
        rec = _ctx(captured, "wf_try_execute.end")[-1]
        assert rec["matched"] is False
        assert rec["gate_field"] == "combined"
        assert "score " in rec["reason"], "旧口径 reason 文案逐字保留: %s" % rec["reason"]
        assert rec["gate_value"] < rec["min_score"]
        # 同一用例在新口径下 reason 改口为 evidence，且数字对得上
        monkeypatch.delenv(_ENV, raising=False)
        captured.records.clear()
        r2 = svc.try_execute(TASK_ARCHIVE, min_score=threshold)
        rec2 = _ctx(captured, "wf_try_execute.end")[-1]
        if r2.matched is False:
            assert "evidence " in rec2["reason"]
        assert rec2["gate_field"] == "evidence"
        assert rec2["gate_value"] == pytest.approx(rec2["evidence"])

    def test_审计字段不含用户原文(self, tmp_path, captured):
        """ctx 只放数值与 id —— 除既有的 task_text 截断外不得新塞原文"""
        secret = "把仓库里的日志文件按天归档压缩"
        svc = _svc(tmp_path, [_entry("probe-a", secret, conf=0.5, prio=50, sc=2)])
        captured.records.clear()
        svc.try_execute(secret, min_score=0.25)
        rec = _ctx(captured, "wf_try_execute.end")[-1]
        numeric = ("similarity", "confidence", "confidence_factor", "priority",
                   "priority_factor", "evidence", "combined", "gate_value",
                   "min_score", "score")
        for k in numeric:
            if k in rec:
                assert isinstance(rec[k], (int, float)) and not isinstance(rec[k], bool)
        # 新增审计键里不得出现用户原文
        for k in ("gate_field", "gate_on_evidence", "workflow_id", "mode"):
            if k in rec:
                assert secret not in str(rec[k])
