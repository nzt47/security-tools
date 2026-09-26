# -*- coding: utf-8 -*-
"""F11-B · 中文触发词（2 字滑窗）回归用例

背景（实测见 `docs/audit_skill_governance/F11.md` §4.3 与 `F11-B.md`）：
触发词原先走**按字切分**，而 `admission.MIN_TRIGGER_CHARS = 2` 从 S10-01 起对
单字**全量过滤** ⇒ 任何**纯中文任务**的 `trigger_patterns` 恒为 `[]`
⇒ 结构性准入判 `NO_DISCRIMINATIVE_TRIGGER` ⇒ 一律草稿、**连原样复述都命中不了**。
该子系统因此产不出可被任何消费者使用的工作流。

F11-B 的修法是**改切分单位，不是改门槛**：
  - 中文触发词取自 **2 字滑窗(bigram)**，复用 `learner.signature_tokens`
    （与任务签名同一个分词器，不引入第二真相源）；
  - `MIN_TRIGGER_CHARS = 2` 继续原样生效，**单字仍然不得**成为触发词。

本文件按任务卡 §4 的六条要求组织，每条都有实测断言：
  1. 纯中文 ⇒ 触发词非空、每个 token >= 2 字符、无单字；
  2. **原样复述能命中**（F11 实测失败的那一条，现转绿）；
  3. **近似复述也命中**，并固化"判定标准"（见 TestNearParaphrase）；
  4. **不该命中的不命中**（无关中文句，防误召）；
  5. 英文输入行为不变（回归）；
  6. 无实词输入仍拒绝落库（F11 既有语义，不得回归）。

【数据隔离】全部用例只写 `tmp_path` 下的仓库文件，**不触碰**
`data/learned_workflows.json`（运行期数据，见 F11 报告 §5）。
"""

from __future__ import annotations

import pytest

from agent.workflow_learning import admission
from agent.workflow_learning.admission import (
    MIN_STEPS,
    MIN_TRIGGER_CHARS,
    effective_trigger_patterns,
    is_discriminative_trigger,
    single_char_triggers,
)
from agent.workflow_learning.exceptions import ErrorCode, WorkflowLearningError
from agent.workflow_learning.learner import (
    TRIGGER_TOKENS_MAX,
    signature_tokens,
    trigger_tokens,
)
from agent.workflow_learning.models import (
    LearningRecord,
    WorkflowStatus,
)
from agent.workflow_learning.service import WorkflowLearningService

#: F11 §4.3 的复现实测用的那句话（修复前：trigger_patterns=[] / draft / match()=[]）
ZH_TASK = "统计当前工作目录下有多少个 py 文件"
#: 近似复述（语序调换，不引入原句以外的字符）
ZH_TASK_REORDERED = "当前工作目录下有多少个 py 文件，统计"
#: 近似复述（换同义词/加虚词 —— 会引入原句没有的字符）
ZH_TASK_SYNONYM = "总计一下当前工作目录里有几个 py 文件"
#: 无关中文句（不得命中该工作流）
ZH_UNRELATED = "帮我把明天下午的会议改到周五上午十点"
#: 纯英文任务（英文口径回归）
EN_TASK = "search python files and save the report"

#: 退化输入：不含任何实词（空/停用字/单字）
DEGENERATE_INPUTS = ["", "   ", "???", "好", "你好"]


def _tool_calls(n: int):
    return [{"name": "tool_%d" % i, "params": {}, "success": True}
            for i in range(n)]


@pytest.fixture
def svc(tmp_path) -> WorkflowLearningService:
    """独立临时仓库（绝不写真实 data/learned_workflows.json）"""
    s = WorkflowLearningService(repo_path=str(tmp_path / "wf.json"))
    s.set_tool_executor(lambda tool, params: {"ok": True, "tool": tool})
    return s


def _learn(svc: WorkflowLearningService, user_input: str, *,
           n_steps: int = 2, session_id: str = "sess-f11b"):
    return svc.learn_from_interaction(LearningRecord(
        session_id=session_id, user_input=user_input,
        tool_calls=_tool_calls(n_steps), success=True))


# ═══════════════════════════════════════════════════════════════════
#  1. 纯中文 ⇒ 触发词非空、每个 token >= 2 个有效字符、无单字
# ═══════════════════════════════════════════════════════════════════

class TestChineseTriggersAreBigrams:

    def test_纯中文任务的触发词非空且无单字(self, svc):
        wf = _learn(svc, ZH_TASK, n_steps=5)
        assert wf.trigger_patterns, (
            "纯中文任务必须拿到触发词 —— 这是 F11-B 的核心修复"
            "（修复前该断言必失败：trigger_patterns == []）")
        for t in wf.trigger_patterns:
            assert len(t) >= MIN_TRIGGER_CHARS, "触发词至少 2 个字符: %r" % t
            assert is_discriminative_trigger(t), "无区分度触发词不得入库: %r" % t
            assert not t.isascii(), "中文任务不该产出 ASCII 触发词: %r" % t
        assert single_char_triggers(wf.trigger_patterns) == [], "不得混入单字"
        assert effective_trigger_patterns(wf.trigger_patterns) == \
            wf.trigger_patterns, "列表必须是纯净的（S11-02 门槛对新路径是 no-op）"

    def test_触发词是2字滑窗且与签名同源(self):
        """复用 signature_tokens：**不存在第二套分词**（同一输入同一个切分器）"""
        toks = trigger_tokens(ZH_TASK)
        assert toks == ["统计", "计当", "当前", "前工", "工作"], (
            "2 字滑窗 + 首现序 + 上限 5（noise bigram 属已知局限，见报告 §6）")
        assert all(len(t) == 2 and not t.isascii() for t in toks)
        all_sig_tokens = signature_tokens(ZH_TASK, limit=None)
        for t in toks:
            assert t in all_sig_tokens, (
                "触发词必须来自签名分词器输出的 token 集合: %r" % t)

    def test_触发词数量不超过旧上限5(self):
        long_zh = "统计当前工作目录下所有 python 文件的行数并按扩展名分组保存成报告文件"
        assert len(trigger_tokens(long_zh)) == TRIGGER_TOKENS_MAX == 5, (
            "上限保持旧口径的 5，未引入新阈值")

    def test_政策未被放宽_单字仍不得成为触发词(self):
        """F11-B 改的是**切分单位**，不是门槛：MIN_TRIGGER_CHARS 仍为 2"""
        assert MIN_TRIGGER_CHARS == 2
        assert admission.CODE_NO_DISCRIMINATIVE_TRIGGER == \
            "NO_DISCRIMINATIVE_TRIGGER"
        assert is_discriminative_trigger("统") is False
        assert single_char_triggers(["统", "计"]) == ["统", "计"]
        # 单字/无实词输入根本不产出候选（不是"先产出再过滤"）
        assert trigger_tokens("好") == []
        assert trigger_tokens("???") == []
        # 全单字触发词列表仍被判为"无有区分度触发词"
        assert admission.check_structure(
            steps=_tool_calls(MIN_STEPS),
            trigger_patterns=["统", "计"]).codes == (
                admission.CODE_NO_DISCRIMINATIVE_TRIGGER,)


# ═══════════════════════════════════════════════════════════════════
#  2. 中文任务现在可被消费：原样复述命中 + 状态不再是草稿
# ═══════════════════════════════════════════════════════════════════

class TestChineseTaskIsNowConsumable:

    def test_原样复述能命中学到的工作流(self, svc):
        """F11 §4.3 的实测失败项：修复前"原样复述都命中不了"，现必须转绿"""
        wf = _learn(svc, ZH_TASK, n_steps=5)
        assert wf.status == WorkflowStatus.ACTIVE.value
        hits = svc.matcher.match(ZH_TASK, top_k=5)
        assert [w.id for w, _ in hits] == [wf.id], (
            "同一句话必须命中刚学到的条目（修复前返回 []）")
        assert hits[0][1] >= svc.matcher.min_similarity
        assert any(h["workflow_id"] == wf.id
                   for h in svc.search(ZH_TASK, top_k=5))

    def test_原样复述可免LLM执行(self, svc):
        wf = _learn(svc, ZH_TASK, n_steps=5)
        res = svc.try_execute(ZH_TASK)
        assert res.matched is True and res.success is True
        assert res.workflow_id == wf.id

    def test_中文2步以上不再落草稿_1步仍是草稿(self, svc):
        """**预期行为变更**（任务卡 §3 要求写明）

        改前：中文 ⇒ 触发词恒空 ⇒ 2 步交互也判 `NO_DISCRIMINATIVE_TRIGGER` ⇒ draft。
        改后：2 步以上 ⇒ 结构性准入通过 ⇒ `active`（可进候选池、可被匹配）；
        步骤门槛（`MIN_STEPS=2`）**未动** ⇒ 1 步交互仍只是草稿（仍可按 ID 人工执行）。
        """
        two = _learn(svc, ZH_TASK, n_steps=2, session_id="sess-2")
        assert two.status == WorkflowStatus.ACTIVE.value
        assert admission.check_structure(
            steps=two.steps, trigger_patterns=two.trigger_patterns).admitted

        one = _learn(svc, "把日志文件里的错误行数统计出来", n_steps=1,
                     session_id="sess-1")
        assert one.status == WorkflowStatus.DRAFT.value, "步骤门槛未动"
        assert admission.CODE_STEPS_TOO_FEW in one.description
        assert admission.CODE_NO_DISCRIMINATIVE_TRIGGER not in one.description, (
            "单步中文条目现在只因步骤数被拒（拒绝原因不得再含无触发词）")
        assert one.trigger_patterns, "单步条目同样有 bigram 触发词"


# ═══════════════════════════════════════════════════════════════════
#  3. 近似复述也命中（判定标准已固化）
# ═══════════════════════════════════════════════════════════════════

class TestNearParaphrase:

    def test_语序调换的近似复述仍命中(self, svc):
        wf = _learn(svc, ZH_TASK, n_steps=5)
        hits = svc.matcher.match(ZH_TASK_REORDERED, top_k=5)
        assert [w.id for w, _ in hits] == [wf.id], (
            "同字符集的语序调换应命中（实测 raw sim≈0.85）")

    def test_近似复述的判定标准_不再依赖字符覆盖(self, svc):
        """【判定标准 · 实测固化】详见 `F11-B.md` §5 与 `F11C.md` §2.1

        F11-B 时代的判定标准是**查询里是否引入了索引文本中没有的字符**：
          - 语序调换、不引入新字  → raw sim 0.854 ⇒ 命中；
          - 引入新字（"总计…里有几个…"）→ raw sim 0.002 ⇒ 不命中。

        后半段不是"语序不敏感"的证明，而是 `matcher._idf` 在下界 0.001 下的
        **缺陷**：未见词 idf=0.693、已见词 idf=0.001，相差 693 倍，把 sim 压到
        min_similarity 之下；索引里**多一条工作流**后同一句话又能到 0.55
        （F11-B.md §6 登记的遗留）。

        **F11-C 已修**（`_idf` 改加 1 平滑 + 索引/查询共用 learner 分词口径）：
        引入新词的改写**同样过线**，判定标准回到"语义/词重叠"而不是"字符覆盖"。
        本用例按该契约更新（F11-B 在本方法 docstring 里已授权："若将来被修好
        导致本断言失败，请更新那条遗留，不要在这里放宽断言"）——
        下面保留**可伪证**的两条对照断言，避免本用例退化恒真。
        """
        from agent.workflow_learning.matcher import _tokenize

        wf = _learn(svc, ZH_TASK, n_steps=5)
        doc_tokens = set(svc.matcher._index._docs[wf.id])

        def _raw_sim(q: str) -> float:
            got = svc.matcher._index.query(q, top_k=5)
            return got[0][1] if got else 0.0

        # 对照 1：语序调换**不**引入索引文本以外的词（否则"命中"没有信息量）
        assert [t for t in _tokenize(ZH_TASK_REORDERED) if t not in doc_tokens] == []
        # 对照 2：换同义词**确实**引入了索引文本以外的词 —— 这正是 F11-C 修的场景，
        #         不引入的话本用例就是恒真的（保留 F11-B 原有的伪证意图）
        assert [t for t in _tokenize(ZH_TASK_SYNONYM) if t not in doc_tokens] != []

        assert _raw_sim(ZH_TASK_REORDERED) >= svc.matcher.min_similarity
        assert _raw_sim(ZH_TASK_SYNONYM) >= svc.matcher.min_similarity, (
            "F11-C 修复项：引入新词的改写不得再塌到 0.002")
        assert [w.id for w, _ in svc.matcher.match(ZH_TASK_SYNONYM, top_k=5)] == \
            [wf.id]


# ═══════════════════════════════════════════════════════════════════
#  4. 不该命中的不命中（防误召）
# ═══════════════════════════════════════════════════════════════════

class TestUnrelatedChineseDoesNotMatch:

    def test_无关中文句不命中该工作流(self, svc):
        wf = _learn(svc, ZH_TASK, n_steps=5)
        hits = svc.matcher.match(ZH_UNRELATED, top_k=5)
        assert hits == [], "无关句不得召回: %r" % [w.id for w, _ in hits]
        assert svc.search(ZH_UNRELATED, top_k=5) == []
        assert svc.try_execute(ZH_UNRELATED).matched is False

    def test_无关句不因触发词入库而变近(self, svc):
        """触发词进索引后仍要与无关句保持**数量级**差距（防"bigram 稀释"）"""
        wf = _learn(svc, ZH_TASK, n_steps=5)
        near = svc.matcher._index.query(ZH_TASK_REORDERED, top_k=5)[0][1]
        far = svc.matcher._index.query(ZH_UNRELATED, top_k=5)
        far_sim = far[0][1] if far else 0.0
        assert far_sim < svc.matcher.min_similarity
        assert near > far_sim * 10, "命中与不命中之间应有数量级差距"


# ═══════════════════════════════════════════════════════════════════
#  5. 英文行为不变（回归）
# ═══════════════════════════════════════════════════════════════════

class TestEnglishBehaviourUnchanged:

    def test_英文任务仍取整词_小写_去停用词(self, svc):
        wf = _learn(svc, EN_TASK, n_steps=2)
        assert wf.trigger_patterns == \
            ["search", "python", "files", "save", "report"]
        assert wf.status == WorkflowStatus.ACTIVE.value

    def test_单字母与停用词永不成为触发词(self):
        from agent.workflow_learning.learner import _extract_keywords

        assert trigger_tokens("a an the and to of it is") == []
        assert trigger_tokens("the python and the files") == ["python", "files"]
        # 边界：`am` 不在 `_STOP_WORDS` 里，旧口径同样保留它 —— 新口径与旧口径
        # 对同一输入给出**完全相同**的结果（这里顺带把"行为不变"钉成对照）
        for text in ("I am a Python user", "the python and the files"):
            legacy = effective_trigger_patterns(_extract_keywords(text, top_k=5))
            assert trigger_tokens(text) == legacy, (
                "英文触发词与旧口径必须一致: %r" % text)

    def test_英文顺序口径的差异只在于排序_集合不变(self):
        """旧: 词频降序（`_extract_keywords`）→ 新: 首现序

        实测差别只在**重复词的排序**上，集合完全相同（索引是词袋，顺序无影响）：
          "alpha beta beta" → 旧 [beta, alpha] / 新 [alpha, beta]
        """
        from agent.workflow_learning.learner import _extract_keywords

        text = "alpha beta beta"
        legacy = effective_trigger_patterns(_extract_keywords(text, top_k=5))
        now = trigger_tokens(text)
        assert legacy == ["beta", "alpha"], "旧口径（词频降序）"
        assert now == ["alpha", "beta"], "新口径（首现序）"
        assert sorted(legacy) == sorted(now), "两套口径的集合必须相同"

    def test_英文仍可被原样复述命中(self, svc):
        wf = _learn(svc, EN_TASK, n_steps=2)
        assert [w.id for w, _ in svc.matcher.match(EN_TASK, top_k=5)] == [wf.id]


# ═══════════════════════════════════════════════════════════════════
#  6. 无实词输入仍拒绝落库（F11 既有语义，不得回归）
# ═══════════════════════════════════════════════════════════════════

class TestNoRealWordInputStillRefused:

    @pytest.mark.parametrize("bad", DEGENERATE_INPUTS)
    def test_无实词输入仍拒绝落库(self, svc, bad):
        with pytest.raises(WorkflowLearningError) as exc:
            _learn(svc, bad)
        assert exc.value.code == ErrorCode.LEARN_FAILED
        assert svc.repo.count() == 0, "拒绝必须**零写入**"

    def test_触发词口径变更没有给退化输入开后门(self):
        """退化输入在触发词层同样拿不到候选（与签名层同源）"""
        for bad in DEGENERATE_INPUTS:
            assert trigger_tokens(bad) == []
