# -*- coding: utf-8 -*-
"""G1C-U1 验收：生产 Layer-1 的**索引文本**必须并入中文 description_zh。

## 这张卡在补什么

G1-C 把 5 条主轨独有技能迁移成 data/skills_repo/<id>/skill.md 后，**英文** query
5/5 可召回；但它自己登记的 U1 是：生产 Layer-1 的 loader._meta_to_meta_text()
只拼英文 description ⇒ **中文 query 实测 4/5 返回 []**。
真实用户输入绝大多数是中文 ⇒ 不修就等于这次迁移对中文场景没生效。

## 本文件守的七件事（全部走**生产入口**，不接受"我改了字段所以它应该能中"）

1. **字段选择**：_meta_to_meta_text() 拼出的文本里含 description_zh（20 条双轨技能全中）；
2. **候选池**：倒排索引里真的能查到该技能中文文案切出的 bigram；
3. **分词口径**：索引侧 / 查询侧 / 打分侧是**同一个** _tokenize（守 G1-A/F11-C 的坑：
   两侧口径不一致 = 死特征）。本卡修的是**字段**，不是分词；
4. **中文召回**：8 条贴近真实用途的中文 query 在生产链路上 8/8 命中这 5 条；
5. **英文不退化**：同语义 8 条英文 query 仍 8/8；且**逐条技能的分数只增不减**
   （数学上必然：命中集合只增、分母 len(query_tokens) 不变）；
6. **逃生开关**：CP_SKILL_META_INCLUDE_ZH=0/false/no/off ⇒ **逐字**回到旧行为
   （含倒排索引缓存键必须跟着开关走，否则"看起来生效、实际命中旧倒排"）；
7. **误召边界（如实锁定，不粉饰）**：中文召回上升的**代价**是无关中文也开始命中。
   本文件把实测值**钉死**（远邻 1 次 / 近邻 6 次），任何人把这个改动放宽都会变红。

## 数字口径

· "误召次数"用 **min_score=0.0 全量扫描后 score>0 的条数**，不用 top_k 截断后的条数 ——
  0.0769 这种同分并列的先后由 set 迭代顺序决定（Python 字符串哈希随机化），
  top_k 口径下**跨进程不可复现**，不能当断言。
· 黄金集指标只锁 Precision@3 / Recall@3（4 次实测稳定为 0.4444 / 1.0）；
  **MRR 不锁** —— 它被同分并列的先后影响，实测同一配置不同进程落在 0.9519~0.9778。
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / "data" / "skills_repo"

#: G1-C 迁移的 5 条（本卡要证明"它们对中文 query 也可召回"）
TARGET5 = (
    "code-observability",
    "engineering-test-delivery",
    "frontend-state-sync",
    "self-explanatory-ui",
    "testing-anti-patterns",
)

ENV = "CP_SKILL_META_INCLUDE_ZH"

#: (中文 query, 期望技能, 同语义的英文 query)
PAIRS = (
    ("写测试时要避免哪些反模式", "testing-anti-patterns",
     "what anti-patterns to avoid when writing tests"),
    ("给测试加 Mock 有什么坑", "testing-anti-patterns",
     "pitfalls of adding mocks in tests"),
    ("生成后端接口时怎么加结构化日志和健康检查", "code-observability",
     "structured logs and health check for backend api"),
    ("前后端状态不同步、有竞态该怎么防", "frontend-state-sync",
     "frontend backend state out of sync race condition"),
    ("乐观更新回滚和请求取消怎么写", "frontend-state-sync",
     "optimistic update rollback and request cancellation"),
    ("做一个不用查文档就能看懂的自解释界面", "self-explanatory-ui",
     "self explanatory interface without documentation"),
    ("界面设计时怎么把帮助信息集成进去", "self-explanatory-ui",
     "integrate help information into interface design"),
    ("代码交付前怎么做自测和审计报告", "engineering-test-delivery",
     "self testing and audit report before code delivery"),
)

#: 负样本（远邻）：与该 5 条技能**毫无关系**的中文诉求
NEG_FAR = (
    "统计当前工作目录下有多少个文件",
    "把这段话翻译成日语",
    "今天北京的天气怎么样",
    "帮我订一张明天去上海的高铁票",
    "解释一下傅里叶变换的物理意义",
    "给我讲个睡前故事",
    "计算 1234 乘以 5678 等于多少",
    "推荐几部适合周末看的电影",
)

#: 负样本（近邻）：同属"写代码"大主题、词汇有重叠，但**诉求不同**
NEG_NEAR = (
    "帮我重构这段代码，把函数拆小一点",
    "解释一下什么是闭包",
    "帮我优化一下这条 SQL 查询的性能",
    "git 怎么撤销上一次提交",
    "帮我把这个网页的字体调大一点",
    "这个接口为什么返回 500，帮我看看",
    "帮我把变量名改成驼峰命名",
    "写一个正则匹配邮箱地址",
)

#: 实测（G1C-U1，min_score=0.0 全量扫描、score>0 计入）：
#:   远邻：改前 0 → 改后 1（唯一一条：'解释一下傅里叶变换的物理意义' 命中 self-explanatory-ui）
#:   近邻：改前 1 → 改后 6
#: 这两条是**代价**，不是要炫耀的指标；钉死它们是为了让"再放宽"必须显式改数字。
MEASURED_FAR_FP = 1
MEASURED_NEAR_FP = 6

#: 改前（只拼英文 description）黄金集口径：4 次实测稳定
PRE_CHANGE_PRECISION_AT_3 = 0.4444
PRE_CHANGE_RECALL_AT_3 = 1.0


# ════════════════════════════════════════════════════════════
#  夹具：全部走生产入口
# ════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def file_store():
    from agent.skills_mgmt.file_store import SkillFileStore
    return SkillFileStore()


@pytest.fixture(scope="module")
def meta_index(file_store):
    return file_store.load_metadata_index(refresh=True) or {}


@pytest.fixture()
def loader(file_store):
    from agent.skills_mgmt.loader import SkillLoader
    return SkillLoader(file_store=file_store)


def _hit_ids(ld, query, meta_index, *, top_k=5):
    """在**全量技能**上取分（top_k 直接给足，避免截断把结论搅浑）"""
    res = ld.match(query, top_k=max(top_k, len(meta_index)), min_score=0.0)
    return {m.skill_id: m.score for m in res.matches}


def _fp_count(ld, query, meta_index):
    """误召计数：目标 5 条里 score>0 的条数（与 set 迭代顺序无关，可复现）"""
    scores = _hit_ids(ld, query, meta_index)
    return sum(1 for sid in TARGET5 if scores.get(sid, 0.0) > 0.0)


# ════════════════════════════════════════════════════════════
#  1. 字段选择 + 候选池 + 分词口径
# ════════════════════════════════════════════════════════════

class TestIndexSideFieldSelection:

    def test_meta_text_includes_description_zh_for_all_dual_track(self, meta_index):
        """所有带 description_zh 的技能（实测 20 条）的中文都必须进索引文本"""
        from agent.skills_mgmt.loader import _meta_to_meta_text
        dual = [sid for sid, m in meta_index.items()
                if str(m.get("description_zh") or "").strip()]
        assert len(dual) >= 20, "前置不成立：带 description_zh 的技能少于 20 条"
        bad = [sid for sid in dual
               if str(meta_index[sid]["description_zh"]).strip()
               not in _meta_to_meta_text(meta_index[sid])]
        assert bad == [], "description_zh 没有进索引文本（中文 query 无从命中）: %r" % bad

    def test_the_five_targets_have_chinese_in_their_index_text(self, meta_index):
        """起点证据：改前这 5 条的索引文本里 CJK 字符数 = 0（code/frontend/self 三条）"""
        from agent.skills_mgmt.loader import _meta_to_meta_text
        cjk = lambda s: sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
        got = {sid: cjk(_meta_to_meta_text(meta_index[sid])) for sid in TARGET5}
        bad = [sid for sid, n in got.items() if n < 10]
        assert bad == [], "索引文本里几乎没有中文（实测 CJK 计数 %r）" % got

    def test_inverted_index_contains_chinese_bigrams_of_targets(self, loader, meta_index):
        """倒排索引（Layer-1 候选池）里必须有中文文案切出的 bigram"""
        inv = loader._get_inverted_index(dict(meta_index))
        want = {
            "testing-anti-patterns": "测试",       # '编写或修改测试'
            "code-observability": "日志",          # '结构化日志'
            "frontend-state-sync": "竞态",         # '竞态防御'
            "self-explanatory-ui": "界面",         # '自解释用户界面'
            "engineering-test-delivery": "交付",   # '交付生产级高质量代码'
        }
        bad = {sid: tok for sid, tok in want.items() if sid not in inv.get(tok, set())}
        assert bad == {}, "倒排索引里没有这些中文 token（候选池进不去）: %r" % bad

    def test_index_and_query_share_the_same_tokenizer(self, loader, meta_index):
        """分词口径对拍：倒排索引存的 token 集合 == 对同一 meta_text 重新分词的结果。

        这条守 G1-A/F11-C 的坑（索引侧与查询侧口径不一致 = 死特征）。
        本卡修的是**被索引的字段**，分词函数两侧一直是同一个 _tokenize。
        """
        from agent.skills_mgmt.loader import _meta_to_meta_text, _tokenize
        inv = loader._get_inverted_index(dict(meta_index))
        bad = {}
        for sid in TARGET5:
            stored = {t for t, bucket in inv.items() if sid in bucket}
            recomputed = set(_tokenize(_meta_to_meta_text(meta_index[sid])))
            if stored != recomputed:
                bad[sid] = (len(stored), len(recomputed),
                            sorted(stored ^ recomputed)[:6])
        assert bad == {}, "索引侧与查询侧分词口径不一致（死特征）: %r" % bad

    def test_query_side_tokens_are_produced_by_the_same_function(self):
        from agent.skills_mgmt import loader as L
        assert L._tokenize("写测试时要避免哪些反模式") == [
            "写测", "测试", "试时", "时要", "要避", "避免", "免哪", "哪些", "些反", "反模", "模式"]


# ════════════════════════════════════════════════════════════
#  2. 生产链路的中文召回 + 英文回归
# ════════════════════════════════════════════════════════════

class TestProductionRecall:

    @pytest.mark.parametrize("query,expected,en", PAIRS,
                             ids=["zh%02d" % i for i in range(1, len(PAIRS) + 1)])
    def test_chinese_query_recalls_expected_skill(self, loader, meta_index, query, expected, en):
        got = _hit_ids(loader, query, meta_index)
        assert expected in got, (
            "中文 query %r 召回不到 %s；实际 score>0 的有 %r"
            % (query, expected, sorted(s for s, v in got.items() if v > 0)))

    @pytest.mark.parametrize("query,expected,en", PAIRS,
                             ids=["en%02d" % i for i in range(1, len(PAIRS) + 1)])
    def test_english_counterpart_still_recalls_expected_skill(
            self, loader, meta_index, query, expected, en):
        got = _hit_ids(loader, en, meta_index)
        assert expected in got, "英文 query %r 召回不到 %s" % (en, expected)

    def test_zh_and_en_hit_counts(self, loader, meta_index):
        zh = sum(1 for q, exp, _ in PAIRS if exp in _hit_ids(loader, q, meta_index))
        en = sum(1 for _, exp, q in PAIRS if exp in _hit_ids(loader, q, meta_index))
        assert (zh, en) == (len(PAIRS), len(PAIRS)), (
            "中文命中 %d/%d、英文命中 %d/%d" % (zh, len(PAIRS), en, len(PAIRS)))

    def test_english_scores_never_drop(self, file_store, meta_index):
        """数学不变量的实测：并入中文只**增** token ⇒ 任何 query 的任何技能分数只增不减。

        这条同时是"英文没有被改坏"的最强形态：不是"top-k 条数没变"，
        而是**逐条技能的分数一个都没降**。
        """
        from agent.skills_mgmt.loader import SkillLoader
        queries = [en for _, _, en in PAIRS] + [q for q, _, _ in PAIRS]
        with_zh = SkillLoader(file_store=file_store)
        on = {q: _hit_ids(with_zh, q, meta_index) for q in queries}
        os.environ[ENV] = "0"
        try:
            off = {q: _hit_ids(SkillLoader(file_store=file_store), q, meta_index) for q in queries}
        finally:
            os.environ.pop(ENV, None)
        drops = []
        for q in queries:
            for sid, s_new in on[q].items():
                s_old = off[q].get(sid, 0.0)
                if s_new < s_old - 1e-12:
                    drops.append((q, sid, s_old, s_new))
        assert drops == [], "出现分数下降（英文被改坏）: %r" % drops[:5]


# ════════════════════════════════════════════════════════════
#  3. 逃生开关
# ════════════════════════════════════════════════════════════

class TestEscapeHatch:

    @pytest.mark.parametrize("value,expected", [
        (None, True), ("", True), ("1", True), ("true", True), ("yes", True), ("on", True),
        ("0", False), ("false", False), ("no", False), ("off", False),
        (" OFF ", False), ("False", False), ("0 ", False),
    ])
    def test_flag_values(self, monkeypatch, value, expected):
        from agent.skills_mgmt.loader import _include_description_zh
        if value is None:
            monkeypatch.delenv(ENV, raising=False)
        else:
            monkeypatch.setenv(ENV, value)
        assert _include_description_zh() is expected

    def test_flag_zero_restores_pre_change_meta_text(self, monkeypatch, meta_index):
        """置 0 ⇒ 逐字回到旧的四字段拼接（name/description/tags/category）"""
        from agent.skills_mgmt.loader import _meta_to_meta_text
        monkeypatch.setenv(ENV, "0")
        bad = {}
        for sid, m in meta_index.items():
            parts = [
                m.get("name", ""),
                m.get("description", ""),
                " ".join(m.get("tags", []) or []),
                m.get("category", ""),
            ]
            want = " ".join(p for p in parts if p)
            if _meta_to_meta_text(m) != want:
                bad[sid] = _meta_to_meta_text(m)[:80]
        assert bad == {}, "置 0 后仍在拼别的字段（逃生开关没到位）: %r" % bad

    def test_flag_zero_makes_chinese_queries_that_newly_hit_miss_again(
            self, monkeypatch, loader, meta_index):
        """红/绿分界：改后 8/8 命中的中文 query，置 0 后必须回落到改前的 2/8。

        这条是"改动真的由本开关承载"的判据 —— 也是删掉改动后本文件变红的同一处。
        """
        def hits():
            return sum(1 for q, exp, _ in PAIRS if exp in _hit_ids(loader, q, meta_index))
        assert hits() == len(PAIRS)
        monkeypatch.setenv(ENV, "0")
        got = hits()
        assert got == 2, (
            "置 0 后中文命中 = %d/8，期望 2/8（改前实测：只有 'Mock' 与 tags 里的中文能中）" % got)

    def test_flag_flip_rebuilds_inverted_index(self, monkeypatch, loader, meta_index):
        """倒排索引缓存键必须跟着开关走：否则"翻转后仍命中旧倒排" = 逃生开关假生效"""
        from agent.skills_mgmt import loader as L
        inv_on = loader._get_inverted_index(dict(meta_index))
        # '日志' 实测：旧索引里没有（old=False），只可能由 code-observability 的
        # description_zh（'强制输出结构化日志'）带进来 ⇒ 是这次改动的干净指纹。
        assert "日志" in inv_on, "前置不成立：新行为下倒排索引里应有中文 bigram"
        monkeypatch.setenv(ENV, "0")
        inv_off = loader._get_inverted_index(dict(meta_index))
        assert inv_off is not inv_on, "开关翻转后仍返回旧倒排索引（缓存键缺开关）"
        assert "日志" not in inv_off, "旧行为的倒排索引里不应有该中文 bigram"
        monkeypatch.delenv(ENV, raising=False)
        inv_back = loader._get_inverted_index(dict(meta_index))
        assert inv_back is not inv_off and "日志" in inv_back

    def test_flag_zero_reproduces_pre_change_golden_metrics(self, monkeypatch):
        """置 0 ⇒ scripts/eval_skill_retrieval.py 的黄金集指标回到改前值。

        只锁 Precision@3 / Recall@3 —— MRR 受同分并列的 set 迭代顺序影响，
        跨进程不可复现（实测 0.9519~0.9778），不能当断言。
        """
        monkeypatch.setenv(ENV, "0")
        spec = importlib.util.spec_from_file_location(
            "g1cu1_eval_skill_retrieval",
            str(ROOT / "scripts" / "eval_skill_retrieval.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rep = mod.evaluate()
        assert rep["overall"]["precision"] == pytest.approx(PRE_CHANGE_PRECISION_AT_3, abs=1e-4)
        assert rep["overall"]["recall"] == pytest.approx(PRE_CHANGE_RECALL_AT_3, abs=1e-4)


# ════════════════════════════════════════════════════════════
#  4. 误召边界（**代价**，如实锁定）
# ════════════════════════════════════════════════════════════

class TestFalsePositiveBoundary:

    def test_clearly_unrelated_queries_still_miss_all_five(self, loader, meta_index):
        """7 条与这 5 条技能毫无关系的中文诉求：一条都不许命中"""
        clean = [q for q in NEG_FAR if q != "解释一下傅里叶变换的物理意义"]
        bad = {q: _fp_count(loader, q, meta_index) for q in clean}
        bad = {q: n for q, n in bad.items() if n}
        assert bad == {}, "无关中文诉求开始命中这 5 条技能: %r" % bad

    def test_far_neighbour_false_positive_count_is_locked(self, loader, meta_index):
        total = sum(_fp_count(loader, q, meta_index) for q in NEG_FAR)
        assert total == MEASURED_FAR_FP, (
            "远邻负样本误召 = %d，钉死值 = %d（'解释一下傅里叶变换的物理意义' 命中 "
            "self-explanatory-ui 是本改动**已知的代价**；若变大必须显式改数字并说明）"
            % (total, MEASURED_FAR_FP))

    def test_near_neighbour_false_positive_count_is_locked(self, loader, meta_index):
        total = sum(_fp_count(loader, q, meta_index) for q in NEG_NEAR)
        assert total == MEASURED_NEAR_FP, (
            "近邻负样本误召 = %d，钉死值 = %d" % (total, MEASURED_NEAR_FP))

    def test_escape_hatch_zero_also_removes_those_false_positives(self, monkeypatch, loader, meta_index):
        """代价随开关一起回滚：置 0 后远邻 0 次、近邻 1 次（= 改前实测值）"""
        monkeypatch.setenv(ENV, "0")
        far = sum(_fp_count(loader, q, meta_index) for q in NEG_FAR)
        near = sum(_fp_count(loader, q, meta_index) for q in NEG_NEAR)
        assert (far, near) == (0, 1), "置 0 后误召 = (远邻 %d, 近邻 %d)，期望 (0, 1)" % (far, near)


# ════════════════════════════════════════════════════════════
#  5. 开关已登记（本轮已因"加 env 不登记"让零缺口护栏红过一次）
# ════════════════════════════════════════════════════════════

class TestSwitchIsRegistered:

    def test_registered_as_level_a_in_skills_category(self):
        from agent.settings import registry as R
        spec = R.get_spec(ENV)
        assert spec is not None, "新 env %s 未登记进 agent/settings/registry.py" % ENV
        assert spec.category == R.CAT_SKILLS, "类别应为 %s，实得 %s" % (R.CAT_SKILLS, spec.category)
        assert spec.risk == R.RISK_A, "置 0 = 恢复旧行为（非拆防护）⇒ 应为 A 级，实得 %s" % spec.risk
        assert spec.default is True, "默认必须是新行为 True，实得 %r" % (spec.default,)

    def test_read_site_is_the_loader(self):
        """登记里的 owner 必须是真正的读取点，避免"UI 显示了一个没人读的开关" """
        import inspect
        from agent.skills_mgmt import loader as L
        src = inspect.getsource(L)
        assert ENV in src, "loader.py 源码里没有该 env 名"
        assert "os.environ.get(_ENV_META_INCLUDE_ZH)" in src, (
            "loader.py 没有真的读这个 env")
