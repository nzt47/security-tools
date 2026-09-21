# -*- coding: utf-8 -*-
"""W5/L28 真实索引回归 —— 「解析pdf」必须命中 read_pdf，不得再被单字碎片劫持

事实（TASK-10 实测、本任务复核）
-------------------------------
在**真实 90 工具索引**上，查询 "解析pdf" 的 top1 曾是 `run_lint`（代码静态检查器，
BM25 = 72.47），而真正能解析 PDF 的 `read_pdf` 仅第 4（24.34）。
该用例在既有单测里却是**过**的 —— 因为那些单测用 5~10 个工具的**合成索引**，
即仓库纪律 **D12「夹具形状不得代替生产形状」**的实例。

根因（本文件把两个抓手都钉成断言）
--------------------------------
1. 中文按**单字**切分：`解析` 被拆成 解 / 析；真实索引里 `解` 只出现在
   run_lint 一条描述中（df=1，见 test_bit_of_rarity_is_not_dominant 的构造）。
2. idf **未取对数**：df=1 时旧式 `(N-df+0.5)/(df+0.5)` = 59.67，把单字碎片
   放大成支配量级，压过整条真实证据链（"pdf" 的 idf 仅 13.0）。

修法：CJK 相邻二元组切分（与 `agent/skills_mgmt/loader.py` 同尺度，零新依赖）
     + idf 取对数（标准 Robertson-Sparck-Jones 形式）。

本文件**不使用**合成小索引：工具集直接来自 `data/tool_definitions/*.yaml`
（91 个 YAML 中 llm_callable 且未弃用的 90 个，与 `data/tool_index.json` 工具集逐一相同）。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

YAML_DIR = ROOT / "data" / "tool_definitions"
PROD_INDEX = ROOT / "data" / "tool_index.json"
BASELINE_CASES = ROOT / "eval" / "routing_baseline" / "cases.json"


# ════════════════════════════════════════════════════════════
#  真实工具集（来自生产 YAML，不是夹具）
# ════════════════════════════════════════════════════════════

def _load_real_tools() -> list[dict]:
    """读生产 YAML 构造工具集（字段口径与 data/tool_index.json 一致）"""
    import yaml

    tools: list[dict] = []
    for f in sorted(YAML_DIR.glob("*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if not d.get("llm_callable") or d.get("deprecated"):
            continue
        props = ((d.get("schema") or {}).get("properties") or {})
        tools.append({
            "name": d["name"],
            "category": d.get("category"),
            "description": d.get("description", ""),
            "parameter_names": list(props.keys()),
        })
    return tools


@pytest.fixture(autouse=True)
def _disable_embedding_probe(monkeypatch):
    monkeypatch.setenv("AGENT_HYBRID_EMBEDDING", "0")


@pytest.fixture(autouse=True)
def _reset_hybrid_singleton():
    from agent.tool_router_hybrid import reset_hybrid_retriever
    import agent.tool_router_hybrid as mod

    reset_hybrid_retriever()
    mod._PROBE_RESULT = None
    yield
    reset_hybrid_retriever()
    mod._PROBE_RESULT = None


@pytest.fixture(scope="module")
def real_tools() -> list[dict]:
    tools = _load_real_tools()
    assert tools, f"未从 {YAML_DIR} 读到任何工具定义"
    return tools


@pytest.fixture
def real_retriever(tmp_path, real_tools):
    """索引由**生产 YAML** 构造后走生产加载路径（HybridRetriever(index_path=...)）"""
    from agent.tool_router_hybrid import HybridRetriever

    p = tmp_path / "tool_index.json"
    p.write_text(json.dumps({"tools": real_tools}, ensure_ascii=False), encoding="utf-8")
    r = HybridRetriever(alpha=0.5, index_path=str(p))
    assert r.available, "真实索引应可用"
    assert r.degraded is True, "本测试固定在 BM25-only 降级路（Embedding 被禁用）"
    return r


# ════════════════════════════════════════════════════════════
#  ① 夹具形状 == 生产形状（先防漂移，再谈结论）
# ════════════════════════════════════════════════════════════

class TestFixtureShapeMatchesProduction:
    def test_yaml_toolset_equals_production_index_toolset(self, real_tools):
        prod = json.loads(PROD_INDEX.read_text(encoding="utf-8"))["tools"]
        assert {t["name"] for t in real_tools} == {t["name"] for t in prod}, (
            "YAML 派生的工具集与 data/tool_index.json 不一致 ⇒ 本回归的「真实形状」前提失效"
        )
        assert len(real_tools) == 90, f"真实工具数应为 90，实际 {len(real_tools)}"

    def test_index_is_not_a_toy_fixture(self, real_retriever, real_tools):
        assert real_retriever._bm25.size == len(real_tools) == 90


# ════════════════════════════════════════════════════════════
#  ② 根因抓手：分词粒度 + idf 量纲
# ════════════════════════════════════════════════════════════

class TestRootCauseIsPinned:
    def test_tokenizer_is_cjk_bigram(self):
        """中文按相邻二元组切分（与 skills_mgmt 同尺度）；纯 ASCII 仍按整词"""
        from agent.tool_router_hybrid import _tokenize

        assert _tokenize("解析PDF文件") == ["解析", "pdf", "文件"]
        assert _tokenize("abc def") == ["abc", "def"]
        assert _tokenize("一二三四") == ["一二", "二三", "三四"]
        assert _tokenize("") == []

    def test_single_char_fragment_is_a_token_only_when_isolated(self):
        """孤立单字仍是 token（短查询不被切成空），但成串中文不再退化成单字"""
        from agent.tool_router_hybrid import _tokenize

        assert _tokenize("删") == ["删"]
        assert "解" not in _tokenize("解析pdf")
        assert "析" not in _tokenize("解析pdf")

    def test_idf_is_logarithmic_not_raw_ratio(self):
        """idf 必须是对数形式：同一输入下必须远小于未取对数的旧式（df=1 时的 59.67）

        判据用**同一实现里的同输入对拍**，不依赖绝对常数：旧式 idf 与对数式 idf
        在 df=1、N=90 时相差 14 倍以上（59.67 vs 4.09），断言比值即可钉住形式。
        """
        from agent.tool_router_hybrid import BM25Index

        idx = BM25Index()
        idx.add_document("rare", "独一无二的词")          # 唯一含 "独一" 的文档
        for i in range(89):
            idx.add_document(f"d{i}", "通用描述复数词条")
        term = "独一"
        df = len(idx._index[term])
        assert df == 1 and idx._total_docs == 90
        dl = idx._doc_lengths["rare"]
        got = idx._compute_bm25(term, 1, dl)

        # 旧式（未取对数）在同样输入下的分数：仅 idf 不同
        legacy_idf = (idx._total_docs - df + 0.5) / (df + 0.5)
        legacy = legacy_idf * (1 * (idx._k1 + 1)) / (
            1 + idx._k1 * (1 - idx._b + idx._b * dl / idx._avg_doc_length))
        assert got * 5 < legacy, (
            f"idf 似乎未取对数：新={got:.4f} 旧={legacy:.4f}（比值 {legacy / got:.2f} < 5）"
        )
        # 对数上界：idf 形式为 log(1 + (N-df+0.5)/(df+0.5))，分数必不超过"该 idf × (k1+1)"
        assert got <= math.log(1.0 + legacy_idf) * (idx._k1 + 1) + 1e-9


# ════════════════════════════════════════════════════════════
#  ③ L28 本体：真实索引上「解析pdf」→ read_pdf
# ════════════════════════════════════════════════════════════

class TestRealIndexPdfQuery:
    def test_jiexi_pdf_top1_is_read_pdf(self, real_retriever):
        """核心回归：真实 90 工具索引上，「解析pdf」的 top1 必须是 read_pdf

        改前实测 top1=run_lint（72.47），read_pdf 第 4（24.34）。
        """
        res = real_retriever.query("解析pdf", top_k=10)
        assert res, "应有检索结果"
        assert res[0][0] == "read_pdf", (
            f"「解析pdf」top1={res[0][0]}（期望 read_pdf）；"
            f"完整排序={[d for d, _ in res]}"
        )

    def test_linter_does_not_hijack_pdf_query(self, real_retriever):
        """代码静态检查器不得因「解/析」单字碎片排到 PDF 工具**之前**

        改前：run_lint 是 top1；修后：它落到 read_pdf 之后且不在 top-5。
        判据用「相对位置」而不是「完全缺席」—— 90 个工具的候选池里，
        它仍可能因 "解析" 一词出现在别的语境而留在长尾，这不是本次要解决的问题。
        """
        res = real_retriever.query("解析pdf", top_k=10)
        names = [d for d, _ in res]
        assert "read_pdf" in names[:2], f"read_pdf 应在前 2：{names}"
        if "run_lint" in names:
            assert names.index("run_lint") > names.index("read_pdf"), (
                f"run_lint 仍排在 read_pdf 之前：{names}")
            assert "run_lint" not in names[:5], f"run_lint 进入了 top-5：{names}"

    def test_score_gap_is_not_dominated_by_a_single_fragment(self, real_retriever):
        """改前 run_lint 的分是 read_pdf 的 2.98 倍；修后该比值必须 < 1.5"""
        raw = dict(real_retriever._bm25.search("解析pdf", top_k=10))
        assert "read_pdf" in raw, "read_pdf 必须在 BM25 候选中"
        lint = raw.get("run_lint", 0.0)
        assert lint < 1.5 * raw["read_pdf"], (
            f"单字碎片仍具支配量级：run_lint={lint:.3f} vs read_pdf={raw['read_pdf']:.3f}"
        )

    def test_chinese_sibling_query_still_hits_read_pdf(self, real_retriever):
        """同族查询（读取pdf）不得因本次改动而漂移"""
        res = real_retriever.query("读取pdf", top_k=10)
        assert res and res[0][0] == "read_pdf"


# ════════════════════════════════════════════════════════════
#  ③b A8：对数 idf 的显式互补——"最低 idf 证据"护栏
#     取对数压缩了稀有词的权重优势（修 L28 所需），副作用是只命中一个常见词的
#     文档变得有竞争力（实测负样本 G9 q22）。故显式补 _MIN_IDF_COVERAGE。
# ════════════════════════════════════════════════════════════

class TestIdfEvidenceFloor:
    # 查询 = 1 个常见词(检索) + 6 个各只出现一次的稀有词 ⇒ idf 质量共 7 份。
    # 只命中"检索"的文档覆盖 1/7 ≈ 0.143 < 0.2 ⇒ 应被滤除；
    # 命中全部 6 个稀有词的文档覆盖 6/7 ≈ 0.857 ⇒ 应保留。
    QUERY = "检索 甲 乙 丙 丁 戊 己"

    @staticmethod
    def _idx():
        from agent.tool_router_hybrid import BM25Index

        idx = BM25Index()
        idx.add_document("common_doc", "通用 检索")
        idx.add_document("gold_doc", "通用 甲 乙 丙 丁 戊 己")
        idx.add_document("noise1", "通用")
        idx.add_document("noise2", "通用")
        return idx

    def test_low_coverage_doc_is_not_a_candidate(self):
        """只命中查询里一个常见词的文档不得进入候选（机制级断言）"""
        from agent.tool_router_hybrid import _MIN_IDF_COVERAGE

        got = dict(self._idx().search(self.QUERY, top_k=10))
        assert "common_doc" not in got, (
            f"低覆盖候选未被滤除（阈值 {_MIN_IDF_COVERAGE}）：{got}")
        assert "gold_doc" in got, "高覆盖候选必须保留"

    def test_floor_is_query_relative_not_an_absolute_score(self):
        """护栏看的是"覆盖了查询多少 idf 质量"，与分数量纲无关：查询词重复不影响结论"""
        idx = self._idx()
        a = {d for d, _ in idx.search(self.QUERY, top_k=10)}
        b = {d for d, _ in idx.search(self.QUERY + " 甲 乙", top_k=10)}
        assert "common_doc" not in a and "common_doc" not in b
        assert "gold_doc" in a and "gold_doc" in b

    def test_real_negative_case_q22(self, real_retriever):
        """真实负样本 G9 q22：「检索 lifetrace 中的历史对话」

        期望 search_lifetrace 命中；search_memory / web_search 不得进 top-5。
        改前（仅对数 idf，无护栏）实测：search_memory 列第 4 ⇒ 该用例由 PASS 变 FAIL。
        """
        lib = json.loads((ROOT / "data" / "tool_negative_samples.json").read_text(encoding="utf-8"))
        case = None
        for g in lib["groups"]:
            for q in g["queries"]:
                if q["query"] == "检索 lifetrace 中的历史对话":
                    case = q
        assert case is not None, "负样本库中找不到 G9 q22（语料已漂移）"
        res = real_retriever.query(case["query"], top_k=5) or []
        names = [d for d, _ in res]
        for neg in case["negative"]:
            assert neg not in names, f"负样本 {neg} 泄漏进 top-5：{names}"
        for pos in case["expected_positive"]:
            assert pos in names, f"期望工具 {pos} 不在 top-5：{names}"


# ════════════════════════════════════════════════════════════
#  ③c A8-G2：护栏不得吃掉正样本召回（gold 从不被下限滤除）
# ════════════════════════════════════════════════════════════

class TestMinIdfCoverageDoesNotEatGold:
    """护栏为修负样本而悄悄牺牲正样本召回，是这类改动最典型的失手方式

    判据直接钉住**召回**而不是集合级准确率：对 hybrid_tool 基线 10 条 + 扩展 18 条，
    逐条比对"护栏开 / 护栏关"的候选集合，断言 gold **从不**出现在被滤除的那部分。
    """

    EXT = [
        ("extract pdf 里的文本", "read_pdf"), ("把多个 pdf merge 成一个文件", "merge_pdf"),
        ("split 这个 pdf 成多页", "split_pdf"), ("查 pdf 的 metadata 信息", "get_pdf_info"),
        ("在 web 上 search 信息", "web_search"), ("fetch 这个 url", "web_get"),
        ("跑个 shell command", "shell_execute"), ("读取本地 file 内容", "read_file"),
        ("按 pattern 找文件", "search_files"), ("get 北京的 weather", "get_weather"),
        ("读取pdf文件的内容", "read_pdf"), ("合并多个pdf", "merge_pdf"),
        ("把pdf拆分成多页", "split_pdf"), ("查询北京天气", "get_weather"),
        ("搜索互联网信息", "web_search"), ("执行shell命令", "shell_execute"),
        ("读取本地文件内容", "read_file"), ("列出目录下的文件", "list_directory"),
    ]

    def _cases(self):
        doc = json.loads(BASELINE_CASES.read_text(encoding="utf-8"))
        hyb = [(c["text"], c["gold"]) for c in doc["cases"]
               if c.get("layer") == "hybrid_tool" and c.get("gold")]
        return hyb + self.EXT

    def test_gold_is_never_filtered_by_the_floor(self, real_retriever, monkeypatch):
        import agent.tool_router_hybrid as mod

        cases = self._cases()
        with_floor = {}
        for q, _g in cases:
            with_floor[q] = {d for d, _ in (real_retriever._bm25.search(q, top_k=500) or [])}

        # 【C3】_MIN_IDF_COVERAGE = 0.0 即等价于不启用护栏（单点关闭）
        monkeypatch.setattr(mod, "_MIN_IDF_COVERAGE", 0.0)
        without = {}
        for q, _g in cases:
            without[q] = {d for d, _ in (real_retriever._bm25.search(q, top_k=500) or [])}

        eaten = []
        for q, gold in cases:
            lost = without[q] - with_floor[q]
            if gold in lost:
                eaten.append((q, gold))
        assert not eaten, (
            "护栏吃掉了正样本召回（A8-G2）：%s" % (eaten,))

    def test_filter_count_matches_reality(self, real_retriever, monkeypatch):
        """读数必须**等于真实被滤掉的候选数**（不是把一个常数抄进事件）

        注：本组用例的滤除数**并不恒为 0**（实测 "extract text from pdf" 会滤掉 7 个
        **非 gold** 候选），因此 G2 的判据取"gold 从不被滤除"（见上一条）+ "读数与真实
        滤除数逐位一致"，而不是"滤除数 == 0"—— 后者会让判据与事实脱节。
        """
        import agent.tool_router_hybrid as mod

        observed = []
        for q, gold in self._cases():
            # 走生产入口（_last_query_stats 由 _query_locked 写入；直接调 _bm25.search 不刷新它）
            res = {d for d, _ in (real_retriever.query(q, top_k=40) or [])}
            stats = real_retriever._last_query_stats
            reported = stats.get("bm25_filtered_by_min_coverage")
            considered = stats.get("bm25_considered")
            assert isinstance(reported, int), "召回过滤读数缺失（A8-G1）"
            assert isinstance(considered, int) and considered >= 0

            with monkeypatch.context() as m:
                m.setattr(mod, "_MIN_IDF_COVERAGE", 0.0)   # 单点关闭护栏（C3）
                without = {d for d, _ in (real_retriever._bm25.search(q, top_k=500) or [])}
            with_floor = {d for d, _ in (real_retriever._bm25.search(q, top_k=500) or [])}
            real_filtered = len(without - with_floor)
            assert reported == real_filtered, (
                "查询 %r 的滤除读数 %s 与真实值 %d 不符（读数被抄成常数？）"
                % (q, reported, real_filtered))
            assert gold in with_floor, "gold 被护栏滤除：%r" % (q,)
            assert gold in res, "gold 未进入生产返回结果：%r" % (q,)
            observed.append((q, reported))
        # 至少有一条用例真的发生了过滤，否则本判据是空转（无法变红）
        assert any(n > 0 for _q, n in observed), (
            "全部 28 条都没有发生任何过滤 ⇒ 该判据无法变红（护栏可能已失效）：%s" % (observed,))

# ════════════════════════════════════════════════════════════
#  ④ 基线用例**逐例**守护（读 TASK-10 用例集，只读）
#     —— 聚合命中率不是判据：基线 9/10 与改后 9/10 数值相同，
#        但正确集从 {…, TOOL-010} 换成 {…, TOOL-006}（横向置换，净收益为零）。
# ════════════════════════════════════════════════════════════

class TestBaselineCasesDoNotRegress:
    """**逐例**钉住 hybrid_tool 层的语义（复核 A7-2：聚合阈值不是判据）

    基线事实（已提交 `eval/routing_baseline/report.json`）：
        `metrics.per_layer.hybrid_tool` = {n:10, accuracy:0.9, n_correct:9,
        claimed_wrong_ids:["TOOL-006"]} ⇒ **改前唯一错例是 TOOL-006**，
        TOOL-010（搜索网页）原本是**命中**的。
    本次工作区状态：TOOL-006 由错转对、TOOL-010 由对转错 ⇒ 该层是
        **9/10 持平（横向置换）**，**不是改进**。

    ⚠️ 因此「top1 命中率 ≥ 0.9」在改动前后**都成立**，检测不到这次置换
    （与"空转补丁 ⇒ 假覆盖"同型）。本类改为**逐例**断言：两条都必须成立。
    """

    @staticmethod
    def _case(case_id):
        cases = json.loads(BASELINE_CASES.read_text(encoding="utf-8"))["cases"]
        hit = [c for c in cases if c["id"] == case_id]
        assert hit, f"基线用例 {case_id} 缺失（用例集可能已漂移）"
        return hit[0]

    def _predict(self, retriever, case_id):
        c = self._case(case_id)
        res = retriever.query(c["text"], top_k=10) or []
        return c, (res[0][0] if res else None), [d for d, _ in res]

    def test_tool_006_is_fixed(self, real_retriever):
        """L28 本体：「解析pdf」(TOOL-006) 必须由错转对 —— 基线 claimed_wrong_ids 就是它"""
        c, pred, names = self._predict(real_retriever, "TOOL-006")
        assert pred == c["gold"], (
            f"{c['id']}（{c['text']}）pred={pred}，应为 {c['gold']}；完整顺序={names}"
        )

    def test_tool_010_gold_stays_in_returned_set(self, real_retriever):
        """TOOL-010（搜索网页）：经**裁定**的度量置换 —— 钉住裁定后的预期，而不是放宽判据

        裁定依据（主会话 D-20260921-16 补充裁定，规则 2）：
          - R4（uni+bi 双重索引）实测**红**：TOOL-006 的 gold 在 BM25 top-40 内消失、
            该配置 hybrid top1 命中率 0.90→0.80；
          - 但 TOOL-006 / TOOL-010 / G9_q22 三条 gold **全部仍在路由器实际返回集合内**
            （hybrid_select_tools(max_tools=25)）⇒ 属度量差异，不是能力损失。
        故本守护按裁定钉住：gold 必须在返回集合内，且名次硬编码（漂移即红）。
        注意：基线层 9/10 是**横向置换、净收益为零**，**不是命中率提升**。
        """
        from unittest.mock import patch

        from agent.tool_router_hybrid import hybrid_select_tools

        c = self._case("TOOL-010")
        raw = [d for d, _ in (real_retriever._bm25.search(c["text"], top_k=40) or [])]
        with patch("agent.tool_router_hybrid.get_hybrid_retriever", return_value=real_retriever):
            returned = hybrid_select_tools(c["text"], max_tools=25) or []

        assert c["gold"] in returned, (
            "裁定后的底线被突破：gold %s 掉出返回集合（%d 个）" % (c["gold"], len(returned)))
        assert returned.index(c["gold"]) + 1 == 9, (
            "TOOL-010 gold 在返回集合中的名次漂移：%d（裁定基线=9）；顺序=%s"
            % (returned.index(c["gold"]) + 1, returned))
        assert c["gold"] in raw and raw.index(c["gold"]) + 1 == 5, (
            "TOOL-010 gold 的 BM25 名次漂移：%s（裁定基线=5）；top5=%s"
            % ((raw.index(c["gold"]) + 1) if c["gold"] in raw else None, raw[:8]))
