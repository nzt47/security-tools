# -*- coding: utf-8 -*-
"""F11-C · 匹配面「与语料规模无关」回归用例

背景（实测见 `docs/audit_skill_governance/F11C.md`）
----------------------------------------------------
F11-B 交卡时留下一条未解决的瓶颈：`matcher._idf` 的平滑下界 `0.001`
使**召回质量依赖「索引里有几条工作流」**。单文档时未见词 idf=0.693、
已见词 idf=0.001（相差 **693 倍**），查询里只要引入索引文本中不存在的词，
相似度就塌到 0.002；索引里多一条工作流后**同一句话**又能到 0.55。

F11-C 的两处修复（都在 `matcher.py`，都不新增第三方依赖）：
  1. `_idf` 改**加 1 平滑** `log((1+N)/(1+df)) + 1`：任何词权重恒 >= 1，
     单文档时最坏比值 **1.69 倍**（而不是 693 倍）；
  2. `_tokenize` 与 `learner.term_stream` **同源**：索引侧与查询侧
     都是「中文 2 字滑窗 + 英文整词」。改前 `_tokenize` 把中文切回**单字**，
     于是 `register` 拼进索引文本的 bigram 触发词**永远不可能被查询命中**
     （死特征），且单字索引下"任意两段中文都有字重叠"，只能靠 idf 压误召 ——
     那正是召回塌陷的来源。

本文件按**可伪证**方式组织：每条断言在 F11-C 之前的状态（HEAD+F11-B）
下都**真的会失败**（对照数据见报告 §4 差分探针）。

【数据隔离】全部用例只写 `tmp_path` 下的仓库文件，**不触碰**
`data/learned_workflows.json`（运行期数据）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.workflow_learning import matcher as M
from agent.workflow_learning.learner import term_stream
from agent.workflow_learning.matcher import TfidfIndex, _idf, _tokenize
from agent.workflow_learning.models import LearningRecord
from agent.workflow_learning.service import WorkflowLearningService

#: F11-B §5 / F11C §1 的复现句
ZH_TASK = "统计当前工作目录下有多少个 py 文件"
ZH_TASK_REORDERED = "当前工作目录下有多少个 py 文件，统计"
ZH_TASK_POLITE = "请帮我统计当前工作目录下有多少个 py 文件"
#: 换同义词 —— **会引入索引文本里没有的词**，是 F11-B 时代塌陷的那一条
ZH_TASK_SYNONYM = "总计一下当前工作目录里有几个 py 文件"
ZH_UNRELATED = "帮我把明天下午的会议改到周五上午十点"

NEAR = {
    "原样": ZH_TASK,
    "语序调换": ZH_TASK_REORDERED,
    "加虚词": ZH_TASK_POLITE,
    "换同义词": ZH_TASK_SYNONYM,
}
FAR = [
    "帮我订一张去上海的高铁票", "给我讲个笑话吧", "今天股市行情怎么样",
    "推荐几部好看的科幻电影", "我最近总是失眠该怎么办", "解释一下什么是量子纠缠",
    "帮我写一首关于春天的诗",
]
#: 与目标话题无关的填充工作流（只为把语料撑大，不参与目标话题）
PADDING = [
    "把服务器上的日志文件按日期归档备份", "读取配置文件并校验数据库连接字符串",
    "生成上个月的销售数据统计报表", "检查磁盘剩余空间并清理缓存目录",
    "把用户上传的图片统一压缩尺寸", "查询订单表里退款状态的记录",
    "把长文本摘要成三条要点", "监控接口响应时间并发送告警",
    "解析 CSV 表格并写入数据仓库", "抓取网页标题并保存为清单",
    "批量重命名下载目录下的文件", "导出售后工单的处理时长分布",
    "把音频文件转写成文字稿", "校验两份合同的条款是否一致",
    "生成季度预算执行的对比图表", "清理超过九十天的临时文件",
    "把邮件附件自动归档到网盘", "汇总本周的客服通话记录",
    "把身份证照片识别成文字", "绘制城市气温变化的折线",
]

#: 生产入口实测的四档语料规模（F11C §1 的 1/2/5/20）
CORPUS_SIZES = (1, 2, 5, 20)


def _tool_calls(n: int):
    return [{"name": "tool_%d" % i, "params": {}, "success": True}
            for i in range(n)]


def _build(tmp_path, n_docs: int):
    """用**生产入口**造 N 条工作流的索引：service + learn_from_interaction"""
    tmp_path = Path(str(tmp_path))
    tmp_path.mkdir(parents=True, exist_ok=True)
    svc = WorkflowLearningService(repo_path=str(tmp_path / ("wf_%d.json" % n_docs)))
    svc.set_tool_executor(lambda tool, params: {"ok": True, "tool": tool})
    target = svc.learn_from_interaction(LearningRecord(
        session_id="sess-target", user_input=ZH_TASK,
        tool_calls=_tool_calls(5), success=True))
    for i in range(n_docs - 1):
        svc.learn_from_interaction(LearningRecord(
            session_id="sess-pad-%d" % i, user_input=PADDING[i],
            tool_calls=_tool_calls(3), success=True))
    return svc, target


def _sim(idx: TfidfIndex, q: str) -> float:
    got = idx.query(q, top_k=5)
    return got[0][1] if got else 0.0


@pytest.fixture(params=CORPUS_SIZES)
def corpus(request, tmp_path):
    """参数化语料规模 (1/2/5/20)：同一组查询在每个规模下都必须给同一结论"""
    n = request.param
    svc, target = _build(tmp_path, n)
    assert len(svc.matcher._index._docs) == n, "索引规模必须与请求一致"
    return svc, target, n


# ═══════════════════════════════════════════════════════════════════
#  1. 核心修复：引入新词的改写不再塌陷（F11-B 遗留项）
# ═══════════════════════════════════════════════════════════════════

class TestParaphraseNoLongerCollapses:

    def test_引入索引文本以外新词的改写仍能命中(self, corpus):
        """F11-C 的核心断言

        F11-C 之前（HEAD+F11-B）：`换同义词` 在单文档索引下 raw sim **0.0020**
        ⇒ 判不命中；`加虚词` **0.0028** ⇒ 判不命中。本断言在修复前必失败。
        """
        svc, target, _ = corpus
        doc_tokens = set(svc.matcher._index._docs[target.id])

        # 伪证 A：换同义词**确实**引入了索引文本里没有的特征
        #         —— 这正是 F11-B 时代塌到 0.002 的场景
        assert [t for t in _tokenize(ZH_TASK_SYNONYM) if t not in doc_tokens], (
            "「换同义词」必须真的引入新特征，否则断言没有信息量")
        # 伪证 B：加虚词在**新口径**下不引入任何新特征（请/帮/我 是停用字，
        #         不产生 bigram）—— 它当年塌陷纯粹是 idf 悬崖造成的，
        #         修好后它与原样同分，这一条把机制钉死
        assert [t for t in _tokenize(ZH_TASK_POLITE) if t not in doc_tokens] == [], (
            "「加虚词」的虚词应被分词器直接丢弃，不引入新特征")

        for label, q in NEAR.items():
            raw = _sim(svc.matcher._index, q)
            assert raw >= svc.matcher.min_similarity, (
                "「%s」raw sim %.4f < min_similarity %.2f（F11-B 遗留的塌陷）"
                % (label, raw, svc.matcher.min_similarity))
            assert [w.id for w, _ in svc.matcher.match(q, top_k=5)] == [target.id], (
                "「%s」必须命中学到的那一条" % label)

    def test_原样与近似复述都能免LLM执行(self, tmp_path):
        """生产调用路径 = orchestrator._workflow_learning_layer_match → try_execute

        每次都用**全新仓库**测（`try_execute` 成功会 `record_execution` 抬高
        confidence 并重注册索引，复用同一个 svc 会让后面的用例拿到被改过的分数）。
        """
        for label, q in NEAR.items():
            svc, target = _build(tmp_path / ("exec_%s" % abs(hash(label))), 1)
            res = svc.try_execute(q)
            assert res.matched is True and res.success is True, (
                "「%s」应走 0-Token 短路" % label)
            assert res.workflow_id == target.id

    def test_执行器综合分门槛仍是第二道独立过滤_登记为遗留(self, tmp_path):
        """**F11-C 只修了 matcher 层**；端到端还有 executor 的综合分门槛：

            combined = sim × conf_factor × (0.5 + priority/200) >= min_score

        已执行过的条目 `conf_factor = confidence`（<= 1，且在 0.4~1.0 之间爬升），
        于是**弱改写**（sim 0.3~0.5）在执行过几次之后**又**会被这道门槛挡掉
        （冷启动 `success_count==0` 时 conf_factor=1.0，反而最容易过）。

        本用例把这个**既有**行为钉成可复算的事实：修好 matcher **不等于**
        端到端召回变好。该门槛属 executor 策略，不在 F11-C 的改动范围，
        登记为遗留（F11C.md §5）。
        """
        svc, target = _build(tmp_path / "gate", 1)
        # 模拟"已经成功执行过 3 次"（record_execution 的真实步长 0.4→0.5→0.582→0.649）
        wf = svc.repo.get(target.id)
        wf.success_count = 3
        wf.confidence = 0.649
        svc.repo.upsert(wf)
        svc.matcher.register(wf)

        hits = svc.matcher.match(ZH_TASK_SYNONYM, top_k=5)
        assert hits, "matcher 层仍应召回（这就是 F11-C 的契约）"
        sim = svc.matcher._index.query(ZH_TASK_SYNONYM, top_k=5)[0][1]
        combined = hits[0][1]
        assert combined < sim, (
            "综合分必然低于原始相似度（conf<=1 且 priority_factor<=1）")
        # 既有语义：综合分不过 executor.min_score 就不执行 —— F11-C 未改这一层
        assert combined < svc.executor.min_score, (
            "本用例的前提是综合分确实低于 executor 门槛；若此断言失败说明"
            "该门槛已被改动，请更新 F11C.md §5 的遗留登记")
        assert svc.try_execute(ZH_TASK_SYNONYM).matched is False


# ═══════════════════════════════════════════════════════════════════
#  2. 行为稳定性：召回决策与语料规模无关
# ═══════════════════════════════════════════════════════════════════

class TestDecisionIndependentOfCorpusSize:

    def test_同一查询在1_2_5_20条语料下结论一致(self, corpus):
        """这是 F11-C 认为**比召回率更值得处理**的性质

        F11-C 之前：`加虚词` 在 N=1 时 MISS(0.0028)、N=2 时 HIT(0.5481)
        —— 同一个用户查询，系统里有 1 条还是 2 条工作流，召回结果不同。
        """
        svc, target, n = corpus
        for label, q in NEAR.items():
            bits = svc.matcher.match(q, top_k=5)
            assert bits, "N=%d 时「%s」必须命中 —— 决策不得随语料规模变化" % (n, label)

    def test_相似度不随语料规模漂移超过阈值(self, corpus):
        """稳定性要有**量**的约束，不能只看 0/1"""
        svc, _, n = corpus
        for label, q in NEAR.items():
            raw = _sim(svc.matcher._index, q)
            assert raw >= svc.matcher.min_similarity * 1.3, (
                "N=%d「%s」raw=%.4f 距阈值过近，规模再变就可能翻转" % (n, label, raw))


# ═══════════════════════════════════════════════════════════════════
#  3. 防误召：与语料规模无关地不命中（且保留数量级差距）
# ═══════════════════════════════════════════════════════════════════

class TestFalseRecallStaysSuppressed:

    def test_无关句在任何语料规模下都不命中(self, corpus):
        svc, _, n = corpus
        for q in FAR:
            assert svc.matcher.match(q, top_k=5) == [], (
                "N=%d 无关句误召: %r" % (n, q))
        assert svc.try_execute(ZH_UNRELATED).matched is False

    def test_命中与不命中之间保持数量级差距(self, corpus):
        """改前该差距**随语料规模衰减**：far_max 由 0.0001(N=1) 涨到 0.1763(N=20)

        F11-C 后 far_max 在四档规模下恒为 0（bigram 口径下无关中文句几乎不共享
        特征），故这里只要求「一个数量级」，把余量留给真实语料。
        """
        svc, _, n = corpus
        near = min(_sim(svc.matcher._index, q) for q in NEAR.values())
        far = max((_sim(svc.matcher._index, q) for q in FAR), default=0.0)
        assert far < svc.matcher.min_similarity
        assert near > far * 10, (
            "N=%d near=%.4f far=%.4f —— 命中/不命中没有拉开数量级" % (n, near, far))


# ═══════════════════════════════════════════════════════════════════
#  4. 机制层：idf 下界与分词口径（可脱离语料直接复算）
# ═══════════════════════════════════════════════════════════════════

class TestSmoothingAndTokenizerMechanism:

    def test_idf不再有693倍悬崖(self):
        """单文档：未见词/已见词的权重比必须 <= 2（改前是 ~693）"""
        seen = _idf(1, 1)      # 索引里有
        unseen = _idf(1, 0)    # 索引里没有
        assert seen >= 1.0, "任何词的权重恒 >= 1 ⇒ 单文档不会退化为全零"
        assert unseen / seen <= 2.0, (
            "单文档下未见词只应比已见词贵 ~1.69 倍，实测 %.1f 倍" % (unseen / seen))

    def test_idf在多文档下仍保持区分度(self):
        """加 1 平滑不得把"到处都有"与"很少见"压成同一个权重"""
        assert _idf(20, 20) < _idf(20, 1) < _idf(20, 0)
        tree = TfidfIndex()
        tree.add("a", "搜索最新科技新闻并翻译成英文")
        tree.add("b", "查询今日天气")
        assert tree.query("搜索最新科技新闻并翻译成英文", top_k=2)[0][0] == "a"
        assert tree.query("查询今日天气", top_k=2)[0][0] == "b"

    def test_索引与查询共用learner分词口径(self):
        """matcher 不得有第二套分词（F11-B 已把口径收敛到 learner）"""
        for text in (ZH_TASK, ZH_TASK_SYNONYM, "search python files", "", "好"):
            assert _tokenize(text) == term_stream(text)

    def test_中文按2字滑窗_英文按整词(self):
        # 2 字滑窗是**连续**的（"列出当前目录" 无停用字 ⇒ 相邻字都成词）
        assert _tokenize("列出当前目录") == ["列出", "出当", "当前", "前目", "目录"]
        assert _tokenize("列出当前工作目录") == [
            "列出", "出当", "当前", "前工", "工作", "作目", "目录"]
        assert _tokenize("search python files") == ["search", "python", "files"]
        # 单字/单字母永不成为特征（与 admission.MIN_TRIGGER_CHARS 同一口径）
        assert _tokenize("好") == []
        assert _tokenize("a b c") == []

    def test_触发词不再是死特征(self, corpus):
        """F11-C 的第二个缺陷：`register` 把 bigram 触发词拼进索引文本，
        但旧 `_tokenize` 又把它切回单字 ⇒ 查询侧永远产不出该特征。

        改后：索引文本里的每个 bigram 触发词都能被分词器产出 ⇒ 可被命中。
        """
        svc, target, _ = corpus
        doc_tokens = set(svc.matcher._index._docs[target.id])
        for trigger in target.trigger_patterns:
            assert trigger in doc_tokens, "触发词必须真的进了索引特征: %r" % trigger
            assert trigger in _tokenize(target.source_user_input), (
                "bigram 触发词必须能被查询侧同一分词器产出（否则是死特征）: %r"
                % trigger)


# ═══════════════════════════════════════════════════════════════════
#  5. 回归：英文路径与 F11-B 分词口径不变
# ═══════════════════════════════════════════════════════════════════

class TestEnglishAndF11BTokenizerUnchanged:

    def test_英文仍可被原样复述命中(self, tmp_path):
        svc = WorkflowLearningService(repo_path=str(tmp_path / "en.json"))
        svc.set_tool_executor(lambda tool, params: {"ok": True, "tool": tool})
        en = "search python files and save the report"
        wf = svc.learn_from_interaction(LearningRecord(
            session_id="s-en", user_input=en, tool_calls=_tool_calls(2),
            success=True))
        assert wf.trigger_patterns == ["search", "python", "files", "save", "report"]
        assert [w.id for w, _ in svc.matcher.match(en, top_k=5)] == [wf.id]

    def test_signature_tokens与term_stream同源(self):
        """F11-C 把 `signature_tokens` 重写为 term_stream 的去重+截断 —— 行为不变"""
        from agent.workflow_learning.learner import (
            SIGNATURE_MAX_TOKENS, signature_tokens, trigger_tokens)

        for text in (ZH_TASK, ZH_TASK_SYNONYM, "search python files and save the report",
                     "alpha beta beta", "", "好", "I am a Python user"):
            stream = term_stream(text)
            dedup = list(dict.fromkeys(stream))
            assert signature_tokens(text, limit=None) == dedup
            assert signature_tokens(text) == dedup[:SIGNATURE_MAX_TOKENS]
            # F11-B 口径：触发词是签名分词的去停用词前缀，每个 >= 2 字符
            toks = trigger_tokens(text)
            assert all(len(t) >= 2 for t in toks)
            assert all(t in set(stream) for t in toks)

    def test_无实词输入仍被拒绝(self, tmp_path):
        svc = WorkflowLearningService(repo_path=str(tmp_path / "deg.json"))
        from agent.workflow_learning.exceptions import ErrorCode, WorkflowLearningError
        for bad in ("", "   ", "???", "好", "你好"):
            with pytest.raises(WorkflowLearningError) as exc:
                svc.learn_from_interaction(LearningRecord(
                    session_id="s", user_input=bad,
                    tool_calls=_tool_calls(2), success=True))
            assert exc.value.code == ErrorCode.LEARN_FAILED
        assert svc.repo.count() == 0
