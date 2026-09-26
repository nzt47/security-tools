# -*- coding: utf-8 -*-
"""工作流学习层准入门槛 — 单元测试（TASK-S10-01 / S11-02）

覆盖：
  1. **回归锚**：单字触发词的工作流不得进入匹配候选
     （修复前该用例失败：存量 `wf-f19dc52c` 形态的条目在只有它自己注册时，
     用它的原始用户输入提问会**命中并成为候选**，索引层相似度实测 0.875）
  2. 结构性准入判定：步骤数下限 + 触发词区分度（`admission` 单一判定源）
  3. 学习落库定档：不达标 → 草稿态（不进候选、不自动转技能、仍可按 ID 执行）
  4. 转技能准入：结构性不达标者 `force=True` **也不得**转成 Skill
  5. 自动升格门槛：单会话/单轮来源（跨会话样本数 < 2）不得自动升格
  6. 存量退役：只标记不删除 + 追加式台账 + 幂等
  7. 正向回归：达标工作流照常匹配/执行；存量里达标条目仍在候选池
  8. **【S11-02 政策变更】** 触发词列表**纯净度**：混入无区分度项即**整条**判 dirty
     （改前 S10-01 语义为"存在 1 个有效触发词即放行"——存量 `json-30a189b6`
     的 `['读','取','json','配','置']` 即靠此进入候选池；改后要求列表纯净。
     阈值依据见 `agent/workflow_learning/admission.py` 模块 docstring §4）。
     第 1 组中 `test_mixed_trigger_list_rejects_whole_entry` 与
     `test_pure_multi_trigger_list_still_admitted` 为本变更的**反向/正向**锚。
"""
import json

import pytest

from agent.workflow_learning import WorkflowLearningService
from agent.workflow_learning import admission
from agent.workflow_learning.admission import (
    MIN_CROSS_SESSION_SUPPORT,
    MIN_STEPS,
    MIN_TRIGGER_CHARS,
    check_match_eligibility,
    check_structure,
    effective_trigger_patterns,
    is_discriminative_trigger,
    single_char_triggers,
)
from agent.workflow_learning.matcher import WorkflowMatcher
from agent.workflow_learning.models import (
    LearnedWorkflow, LearningRecord, WorkflowStatus, WorkflowStep,
)
from agent.workflow_learning.repository import WorkflowRepository
from agent.workflow_learning.retirement import (
    EVENT_RETIRED, retire_dirty_workflows,
)
from agent.workflow_learning.skill_converter import (
    WorkflowConvertError, WorkflowToSkillConverter,
)


# ═══════════════════════════════════════════════════════════════════
#  工具：构造存量脏条目（形态照抄 data/learned_workflows.json 的 wf-f19dc52c）
# ═══════════════════════════════════════════════════════════════════

DIRTY_INPUT = "帮我列出当前工作目录下的文件"
DIRTY_TRIGGERS = ["列", "出", "当", "前", "工"]


def _dirty_wf(wf_id: str = "wf-f19dc52c") -> LearnedWorkflow:
    """单轮来源 + 单字触发词 + 单步（= 存量脏工作流的完整形态）"""
    return LearnedWorkflow(
        id=wf_id,
        name="自动学习: 列-出-当",
        description=f"从会话 sess_x 中学习得到。原始任务: {DIRTY_INPUT}",
        task_signature="件|作|出|列|前|工|当|录|文|目",
        trigger_patterns=list(DIRTY_TRIGGERS),
        steps=[WorkflowStep(step_id="step_1", tool_name="list_directory",
                            params_template={"path": "."},
                            output_key="step_1_output")],
        source_session_id="sess_20260907_220445_39c3ebf7",
        source_user_input=DIRTY_INPUT,
        success_count=1, failure_count=0, confidence=0.5, priority=80,
        tags=["learned", "列", "出", "当"],
        converted_to_skill_id="",
    )


def _healthy_wf(wf_id: str = "wf-healthy",
                session_id: str = "sess-healthy") -> LearnedWorkflow:
    """达标条目：2 步 + 有区分度触发词（ASCII 词）"""
    return LearnedWorkflow(
        id=wf_id,
        name="自动学习: 统计-python",
        description="从会话 x 中学习得到。原始任务: 统计 Python 文件行数并保存",
        task_signature="python|件|保|存|报|告",
        trigger_patterns=["python", "统计"],
        steps=[
            WorkflowStep(step_id="step_1", tool_name="search_files",
                         params_template={"query": "*.py"},
                         output_key="step_1_output"),
            WorkflowStep(step_id="step_2", tool_name="write_file",
                         params_template={"path": "report.txt"},
                         output_key="step_2_output"),
        ],
        source_session_id=session_id,
        source_user_input="统计 Python 文件行数并保存报告",
        success_count=5, failure_count=0, confidence=0.75, priority=80,
        tags=["learned", "python"],
    )


@pytest.fixture
def repo(tmp_path):
    return WorkflowRepository(path=str(tmp_path / "workflows.json"))


# ═══════════════════════════════════════════════════════════════════
#  1. 回归锚：单字触发词的工作流不得进入匹配候选
# ═══════════════════════════════════════════════════════════════════

class TestSingleCharTriggerNeverCandidate:
    """TASK-S10-01 回归锚 —— 修复前本组用例失败"""

    def test_dirty_workflow_not_in_match_candidates(self, repo):
        """存量形态条目（单字触发词 + 单步）注册后**不得**成为匹配候选

        修复前：`matcher.register(wf)` 无条件入索引，用它的原始用户输入提问
        即命中（实测索引层相似度 0.875 > min_similarity 0.3）。
        """
        m = WorkflowMatcher()
        admitted = m.register(_dirty_wf())

        assert admitted is False, "单字触发词/单步条目不得进候选池"
        assert m.match(DIRTY_INPUT, top_k=5) == []
        assert "wf-f19dc52c" not in m._workflows
        assert "wf-f19dc52c" not in m._index._docs

    def test_dirty_workflow_not_in_service_search(self, repo):
        """经 service 走的同一条路径也不得返回候选"""
        repo.upsert(_dirty_wf())
        svc = WorkflowLearningService(repo_path=str(repo._path))
        assert svc.search(DIRTY_INPUT, top_k=5) == []
        res = svc.try_execute(DIRTY_INPUT)
        assert res.matched is False

    def test_mixed_trigger_list_rejects_whole_entry(self):
        """【政策变更 S11-02】混入单字触发词 → **整条**判 dirty

        改前（S10-01）语义：只要还有 1 个有效触发词即放行，单字仅被丢弃
        —— 存量 `json-30a189b6` 的 `['读','取','json','配','置']` 正是靠这条
        以"存在 1 个 json"进入候选池。
        改后（S11-02）语义：声明列表必须**纯净**，混入即整条否决。
        阈值依据（为什么不新增数字、为什么整条否决）见 admission 模块 docstring §4。
        """
        wf = _healthy_wf()
        wf.trigger_patterns = ["列", "出", "python"]  # 混入单字
        d = check_structure(steps=wf.steps, trigger_patterns=wf.trigger_patterns)
        assert d.admitted is False
        assert d.codes == (admission.CODE_IMPURE_TRIGGER_LIST,), (
            "混入单字只报 IMPURE_TRIGGER_LIST —— 与 NO_DISCRIMINATIVE_TRIGGER "
            "互斥，避免同一条目因'全坏'与'混坏'重复计数")
        assert "不纯净" in d.reason_text

        m = WorkflowMatcher()
        assert m.register(wf) is False, "不纯净条目不得进候选池"
        assert "wf-healthy" not in m._workflows
        assert "wf-healthy" not in m._index._docs, "不得留在索引里"

        # 「丢弃单字」的**特征层**过滤语义仍保留（本任务只收紧准入，不改特征构造；
        # 该过滤是 matcher.py:188 的既有行为，不改动）
        assert effective_trigger_patterns(wf.trigger_patterns) == ["python"]

        # 全是单字 → 走 NO_DISCRIMINATIVE_TRIGGER（而非 IMPURE），两种语义不混
        assert check_structure(
            steps=wf.steps,
            trigger_patterns=["列", "出"]).codes == (
                admission.CODE_NO_DISCRIMINATIVE_TRIGGER,)

    def test_pure_multi_trigger_list_still_admitted(self):
        """正向收紧（防误伤）：**纯净**的多触发词列表不得被新门槛否决

        新门槛只否决"混入无区分度项"，绝不能退化成"触发词多就否决"：
        `generator._compute_priority` 的 `>=3 → +10` 加成面向的正是这种纯净多项列表。
        """
        wf = _healthy_wf()
        wf.trigger_patterns = ["python", "统计", "报告", "行数"]
        d = check_structure(steps=wf.steps, trigger_patterns=wf.trigger_patterns)
        assert d.admitted is True
        assert d.codes == () and d.reasons == ()
        assert WorkflowMatcher().register(wf) is True
        # 混合 ASCII/中文、恰好 2 字符的边界项应算"有区分度"
        assert check_structure(
            steps=wf.steps,
            trigger_patterns=["ai", "读取"]).admitted is True

    def test_rejected_workflow_removed_from_index_when_degraded(self, repo):
        """先达标入池，后被改坏（触发词变单字）→ 同步剔除，不留残留候选"""
        m = WorkflowMatcher()
        wf = _healthy_wf()
        assert m.register(wf) is True
        wf.trigger_patterns = list(DIRTY_TRIGGERS)
        assert m.register(wf) is False
        assert wf.id not in m._workflows


# ═══════════════════════════════════════════════════════════════════
#  2. 结构性准入判定（admission 单一判定源）
# ═══════════════════════════════════════════════════════════════════

class TestStructuralAdmission:
    def test_thresholds_are_the_documented_ones(self):
        """阈值即契约：改动必须同步本用例与模块 docstring 的依据说明"""
        assert MIN_STEPS == 2
        assert MIN_TRIGGER_CHARS == 2
        assert MIN_CROSS_SESSION_SUPPORT == 2

    @pytest.mark.parametrize("pattern,expected", [
        ("列", False),          # 单字 → 无区分度
        ("", False),
        ("   ", False),
        ("*", False),           # 仅正则元字符 → 有效字符 0
        ("a*", False),          # 有效字符 1 → 取更严
        ("json", True),
        ("列出", True),
        ("搜索*", True),        # 元字符不计入，有效字符 2
        ("trigger-wf-demo", True),
    ])
    def test_trigger_discrimination(self, pattern, expected):
        assert is_discriminative_trigger(pattern) is expected

    def test_single_step_rejected(self):
        wf = _healthy_wf()
        wf.steps = wf.steps[:1]
        d = check_structure(steps=wf.steps,
                            trigger_patterns=wf.trigger_patterns)
        assert d.admitted is False
        assert admission.CODE_STEPS_TOO_FEW in d.codes
        assert "步骤数 1" in d.reason_text

    def test_all_single_char_triggers_rejected(self):
        d = check_structure(steps=_healthy_wf().steps,
                            trigger_patterns=list(DIRTY_TRIGGERS))
        assert d.admitted is False
        assert admission.CODE_NO_DISCRIMINATIVE_TRIGGER in d.codes
        assert single_char_triggers(DIRTY_TRIGGERS) == DIRTY_TRIGGERS

    def test_healthy_workflow_admitted(self):
        d = check_structure(steps=_healthy_wf().steps,
                            trigger_patterns=_healthy_wf().trigger_patterns)
        assert d.admitted is True
        assert d.to_dict() == {"admitted": True, "codes": [], "reasons": []}

    def test_match_eligibility_requires_active_and_enabled(self):
        wf = _healthy_wf()
        assert check_match_eligibility(wf).admitted is True
        wf.status = WorkflowStatus.DRAFT
        assert admission.CODE_NOT_ACTIVE in check_match_eligibility(wf).codes
        wf.status = WorkflowStatus.ACTIVE
        wf.enabled = False
        assert admission.CODE_DISABLED in check_match_eligibility(wf).codes


# ═══════════════════════════════════════════════════════════════════
#  3. 学习落库定档（不达标 → 草稿态，但别丢能力）
# ═══════════════════════════════════════════════════════════════════

class TestLearnerAdmission:
    def _svc(self, tmp_path):
        svc = WorkflowLearningService(repo_path=str(tmp_path / "wf.json"))
        svc.set_tool_executor(lambda t, p: {"ok": True, "tool": t})
        return svc

    def _record(self, tool_calls, user_input, session_id="s"):
        return LearningRecord(session_id=session_id, user_input=user_input,
                              tool_calls=tool_calls, success=True)

    def test_one_step_interaction_becomes_draft(self, tmp_path):
        """单轮单工具调用 → 草稿态（这是"单轮交互被学成可匹配工作流"的入口）"""
        svc = self._svc(tmp_path)
        wf = svc.learn_from_interaction(self._record(
            [{"name": "list_directory", "params": {"path": "."},
              "success": True}], DIRTY_INPUT))
        assert len(wf.steps) == 1
        assert wf.status == WorkflowStatus.DRAFT.value
        assert "准入未通过" in wf.description
        assert admission.CODE_STEPS_TOO_FEW in wf.description
        # 【F11-B 口径变更】中文触发词改取 2 字滑窗后，本条**只因步骤数**被拒；
        # 改前它还同时命中 NO_DISCRIMINATIVE_TRIGGER（中文按字切分 → 全单字 →
        # 被过滤 → 触发词为空）。"单步必须落草稿"的意图由上面的 status 断言继续
        # 保证，故此处改为断言"拒绝原因里不再含无触发词"。
        # 该行不在任务卡点名的 :286/:289 之内，属**同一口径变更导致的第 3 处过时
        # 断言**（实测证据与判定见 docs/audit_skill_governance/F11-B.md §3）。
        assert admission.CODE_NO_DISCRIMINATIVE_TRIGGER not in wf.description
        assert wf.trigger_patterns, "中文现在应拿到 2 字滑窗触发词"

    def test_two_step_chinese_interaction_drops_single_char_triggers(
            self, tmp_path):
        """单字触发词不得入库为 trigger_patterns（政策未变，切分口径已变）

        【F11-B 口径变更】中文的**切分单位**从"字"改为"2 字滑窗"：
        改前按字切分 ⇒ 全是单字 ⇒ 被 `MIN_TRIGGER_CHARS = 2` 全量过滤 ⇒
        `trigger_patterns == []` ⇒ 恒判 `NO_DISCRIMINATIVE_TRIGGER`、恒落草稿
        （该子系统因此产不出可被消费的工作流）。改后每个 token 都是 bigram。
        **政策本身未被放宽**：单字仍然不得成为触发词（下面的断言即此）。
        """
        svc = self._svc(tmp_path)
        wf = svc.learn_from_interaction(self._record(
            [{"name": "read_file", "params": {}, "success": True},
             {"name": "write_file", "params": {}, "success": True}],
            "统计文件行数并保存"))
        # 政策守住了：中文任务拿到的触发词**一个单字都没有**
        assert wf.trigger_patterns == ["统计", "计文", "文件", "件行", "行数"], (
            "中文触发词 = 2 字滑窗（复用 learner.signature_tokens）")
        assert single_char_triggers(wf.trigger_patterns) == []
        assert all(len(t) >= MIN_TRIGGER_CHARS for t in wf.trigger_patterns)
        assert single_char_triggers(["统", "计", "文", "件", "行"]) == \
            ["统", "计", "文", "件", "行"], "单字列表本身仍被判无区分度"
        # **预期行为变更**（任务卡 §3 要求写明，不是"为了让测试变绿"）：
        # 中文 2 步交互不再"无有区分度触发词" ⇒ 结构性准入通过 ⇒ 由 draft 变为
        # active（可进匹配候选池）。这是 F11-B 的目标本身：让中文任务也能产出
        # 可被消费的工作流。步骤门槛（MIN_STEPS=2）与单字政策都未动。
        assert wf.status == WorkflowStatus.ACTIVE.value

    def test_draft_workflow_still_executable_by_id(self, tmp_path):
        """草稿态 ≠ 失效：人工按 ID 触发仍可执行（退役/隔离不删能力）"""
        svc = self._svc(tmp_path)
        wf = svc.learn_from_interaction(self._record(
            [{"name": "list_directory", "params": {"path": "."},
              "success": True}], DIRTY_INPUT))
        res = svc.execute_by_id(wf.id, DIRTY_INPUT)
        assert res.matched is True and res.success is True

    def test_admitted_interaction_stays_active_and_matchable(self, tmp_path):
        """正向回归：2 步 + 有区分度触发词 → active 且原样复述即可命中"""
        svc = self._svc(tmp_path)
        task = "统计 Python 文件行数并保存报告"
        wf = svc.learn_from_interaction(self._record(
            [{"name": "search_files", "params": {"query": "*.py"},
              "success": True},
             {"name": "write_file", "params": {"path": "r.txt"},
              "success": True}], task))
        assert wf.status == WorkflowStatus.ACTIVE.value
        assert effective_trigger_patterns(wf.trigger_patterns) == \
            wf.trigger_patterns != []
        assert any(h["workflow_id"] == wf.id for h in svc.search(task, top_k=3))
        res = svc.try_execute(task)
        assert res.matched is True and res.success is True


# ═══════════════════════════════════════════════════════════════════
#  4. 转技能准入（force 不得越过结构性门槛）
# ═══════════════════════════════════════════════════════════════════

class TestSkillPromotionAdmission:
    def test_force_cannot_convert_single_step_workflow(self, repo):
        """`force=True` 也不得把单步/单字触发词条目转成 Skill

        历史事故复现路径：`wf-f19dc52c` → `wf-f19dc52c-skill`。
        （结构性门槛在 skills_service 之前触发，故此处可传 None）
        """
        wf = _dirty_wf()
        wf.success_count = 5
        wf.confidence = 0.75          # 统计门控已被"重复执行"刷过
        repo.upsert(wf)
        conv = WorkflowToSkillConverter(None, repo)
        with pytest.raises(WorkflowConvertError) as exc:
            conv.convert_workflow_to_skill(wf.id, force=True)
        assert exc.value.code == "QUALITY_GATE_FAILED"
        assert admission.CODE_STEPS_TOO_FEW in exc.value.codes
        assert "结构性准入" in str(exc.value)

    def test_force_still_allowed_for_structurally_sound_workflow(self, repo):
        """结构性达标但统计未达标 → force 仍可转（人工判断权保留）"""
        wf = _healthy_wf()
        wf.success_count, wf.confidence, wf.priority = 0, 0.1, 10
        repo.upsert(wf)
        conv = WorkflowToSkillConverter(_FakeSkills(), repo)
        res = conv.convert_workflow_to_skill(wf.id, force=True)
        assert res["action"] == "created"

    def test_auto_promotion_excludes_dirty_even_with_good_stats(self, repo):
        """自动升格路径：统计刷满也不列出脏条目"""
        wf = _dirty_wf()
        wf.success_count, wf.confidence, wf.priority = 9, 0.9, 100
        repo.upsert(wf)
        svc = WorkflowLearningService(repo_path=str(repo._path))
        assert svc.list_convertible_workflows() == []
        report = svc.admission_report()
        blocked = {b["workflow_id"]: b for b in report["disabled_or_blocked"]}
        assert admission.CODE_STEPS_TOO_FEW in blocked[wf.id]["codes"]

    def test_auto_promotion_requires_cross_session_support(self, repo):
        """最少样本数：单会话来源不得自动升格；跨会话复现后才可"""
        wf = _healthy_wf(session_id="sess-A")
        repo.upsert(wf)
        svc = WorkflowLearningService(repo_path=str(repo._path))
        assert repo.count_distinct_sessions(wf.task_signature) == 1
        assert svc.list_convertible_workflows() == [], (
            "单会话（单轮来源）不该自动升格为 Skill")

        # 同一 task_signature 在第二个会话复现 → 支持数 2
        repo.upsert(_healthy_wf(wf_id="wf-healthy-b", session_id="sess-B"))
        svc2 = WorkflowLearningService(repo_path=str(repo._path))
        assert repo.count_distinct_sessions(wf.task_signature) == 2
        listed = {c["workflow_id"] for c in svc2.list_convertible_workflows()}
        assert wf.id in listed
        assert all(c["support_sessions"] >= MIN_CROSS_SESSION_SUPPORT
                   for c in svc2.list_convertible_workflows())


class _FakeSkills:
    """最小 skills 服务替身（只覆盖转换落库所需接口）"""

    def __init__(self):
        self._items = {}

    def get(self, skill_id):
        if skill_id not in self._items:
            # 对齐真实语义：不存在 → 抛异常（_skill_exists 据此判定冲突）
            raise KeyError(skill_id)
        return self._items[skill_id]

    def create_manual(self, data):
        class _S:
            pass
        s = _S()
        s.id = data["id"]
        s.name = data["name"]
        s.version = "0.1.0"
        self._items[s.id] = s
        return s

    def list_all(self):
        return list(self._items.values())


# ═══════════════════════════════════════════════════════════════════
#  5. 存量退役（只标记不删除 + 追加台账 + 幂等）
# ═══════════════════════════════════════════════════════════════════

class TestStockRetirement:
    def test_retire_marks_archived_without_deleting(self, repo, tmp_path):
        repo.upsert(_dirty_wf())
        repo.upsert(_healthy_wf())
        ledger = tmp_path / "retired.jsonl"

        dry = retire_dirty_workflows(repo, apply=False, ledger_path=ledger)
        assert dry["applied"] is False
        assert dry["dirty_before"] == 1 and dry["dirty_after"] == 1
        assert not ledger.exists(), "试运行不得写台账"
        assert repo.get("wf-f19dc52c").status == WorkflowStatus.ACTIVE.value

        out = retire_dirty_workflows(repo, apply=True, ledger_path=ledger)
        assert out["dirty_before"] == 1 and out["dirty_after"] == 0
        # 只标记不删除：条目仍在仓库里，内容字段原样保留
        wf = repo.get("wf-f19dc52c")
        assert wf is not None
        assert wf.status == WorkflowStatus.ARCHIVED.value
        assert wf.trigger_patterns == DIRTY_TRIGGERS
        assert wf.converted_to_skill_id == ""      # 不改写历史
        assert repo.get("wf-healthy").status == WorkflowStatus.ACTIVE.value

        lines = [json.loads(l) for l in
                 ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 1
        rec = lines[0]
        assert rec["event"] == EVENT_RETIRED
        assert rec["workflow_id"] == "wf-f19dc52c"
        assert rec["from_status"] == "active"
        assert rec["to_status"] == "archived"
        assert rec["deleted"] is False
        assert admission.CODE_STEPS_TOO_FEW in rec["codes"]
        assert rec["evidence"]["trigger_patterns"] == DIRTY_TRIGGERS
        assert rec["evidence"]["step_count"] == 1
        assert rec["policy"]["MIN_STEPS"] == MIN_STEPS

    def test_retire_is_idempotent(self, repo, tmp_path):
        repo.upsert(_dirty_wf())
        ledger = tmp_path / "retired.jsonl"
        first = retire_dirty_workflows(repo, apply=True, ledger_path=ledger)
        second = retire_dirty_workflows(repo, apply=True, ledger_path=ledger)
        assert first["ledger_records"] == 1
        assert second["retired"] == []
        assert second["already_archived"] == ["wf-f19dc52c"]
        assert second["ledger_records"] == 0
        assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1

    def test_service_construction_does_not_touch_repo(self, repo, tmp_path):
        """反回归（对齐 P0「测试不再覆盖真实运行期数据」）：**构造服务不写盘**

        存量隔离只走显式入口（`--apply` / `svc.retire_dirty_workflows`）；
        构造期写盘会在任何"初始化全局状态"的测试/进程里改真实
        `data/learned_workflows.json`——该风险在本次开发中实测发生过。
        """
        repo.upsert(_dirty_wf())
        before = repo._path.read_text(encoding="utf-8")
        svc = WorkflowLearningService(repo_path=str(repo._path))
        assert repo._path.read_text(encoding="utf-8") == before
        ledger = tmp_path / "learned_workflows_retired.jsonl"
        assert not ledger.exists(), "构造服务不得追加台账"
        # 但准入隔离已生效（与 status 无关，纯读路径）
        assert svc.search(DIRTY_INPUT, top_k=5) == []
        assert svc.get("wf-f19dc52c").status == WorkflowStatus.ACTIVE.value

    def test_service_explicit_retirement(self, repo, tmp_path):
        """显式退役入口：标记归档 + 追加台账 + 台账落在仓库同目录"""
        repo.upsert(_dirty_wf())
        svc = WorkflowLearningService(repo_path=str(repo._path))
        out = svc.retire_dirty_workflows(apply=True)
        assert out["dirty_before"] == 1 and out["dirty_after"] == 0
        wf = svc.get("wf-f19dc52c")
        assert wf.status == WorkflowStatus.ARCHIVED.value
        ledger = tmp_path / "learned_workflows_retired.jsonl"
        assert ledger.exists()
        rec = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
        assert rec["to_status"] == "archived" and rec["deleted"] is False
        # 归档条目未被删除：仓库里仍在，且健康检查可见
        assert svc.health()["stats"]["total"] == 1
        assert svc.health()["admission"]["archived"] == 1
        assert svc.search(DIRTY_INPUT, top_k=5) == []

    def test_committed_stock_repo_never_yields_dirty_candidates(self):
        """真实存量仓库（data/learned_workflows.json）的**永不进候选**不变量

        不依赖"文件当前是否已被归档"（运行期进程可能改写统计/状态），
        只断言代码层保证：脏条目注册后不进候选池，且达标条目仍在池中
        （防止断言因"对象从报告里消失"而假绿）。

        【S11-02 口径变更】存量 6 条在新增的**纯净度**门槛下**全部**不达标
        （含原被判 clean 的 `json-30a189b6`），故"达标条目仍在池中"改用
        **合成达标条目**作正向对照，并把逐条判定与"按该条自身数据独立复算"
        的结果对齐 —— 断言仍有牙齿，且不会因"全被拒"而失去意义。
        """
        from agent.workflow_learning.retirement import WORKFLOW_REPO_PATH
        if not WORKFLOW_REPO_PATH.exists():
            pytest.skip("存量仓库不存在（非仓库运行环境）")
        data = json.loads(WORKFLOW_REPO_PATH.read_text(encoding="utf-8"))
        assert data, "存量仓库不应为空"
        m = WorkflowMatcher()

        # 正向对照（先做，避免下面"被拒"断言在空池上失义）
        assert m.register(_healthy_wf()) is True
        assert "wf-healthy" in m._workflows

        admitted_ids, rejected_ids = [], []
        for wf_id, raw in data.items():
            wf = LearnedWorkflow(**raw)
            (admitted_ids if m.register(wf) else rejected_ids).append(wf_id)

        # 脏条件命中者一律不在候选池
        for wf_id in rejected_ids:
            assert wf_id not in m._workflows

        # 逐条独立复算（不依赖被测判定函数，避免"用被测代码预测被测代码"）
        def _should_reject(wf: LearnedWorkflow) -> bool:
            status = str(getattr(wf.status, "value", wf.status))
            return (len(wf.steps or []) < MIN_STEPS
                    or not effective_trigger_patterns(wf.trigger_patterns)
                    or bool(single_char_triggers(wf.trigger_patterns))
                    or not wf.enabled or status != "active")

        for wf_id, raw in data.items():
            wf = LearnedWorkflow(**raw)
            assert (wf_id in rejected_ids) is _should_reject(wf), (
                f"{wf_id} 的准入判定与该条自身数据的独立复算不一致")

        # 具名证据：json-30a189b6（4 个单字 + 'json'）必须被纯净度门槛否决
        # （改前它因"存在 'json'"被判 clean 且 active —— 本次审核指出的漏洞）
        if "json-30a189b6" in data:
            assert "json-30a189b6" in rejected_ids
            raw_json = data["json-30a189b6"]
            codes = check_structure(
                steps=LearnedWorkflow(**raw_json).steps,
                trigger_patterns=raw_json["trigger_patterns"]).codes
            assert admission.CODE_IMPURE_TRIGGER_LIST in codes
