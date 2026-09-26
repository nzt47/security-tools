# -*- coding: utf-8 -*-
"""RET-1R 护栏 · R-2：负样本启发式的**作用域 = 它声称的后端**

背景（RET-1R 实测，见 docs/audit_skill_governance/RET1.md §1）:
    agent/skills_mgmt/vector_adapter.py 的
        _NEGATIVE_PATTERNS[1] = ^[a-zA-Z_][a-zA-Z0-9_ ]*$
    改前在 search() 里对**所有后端无条件生效** ⇒ 任何"纯 ASCII 字母/数字/空格"的
    query 一律被当负样本过滤掉 ⇒ 8 条英文 query 里 7 条返回 []，
    向量腿英文召回 **1/8**（绕开该启发式直接比余弦是 **8/8**）。
    而 _is_negative_query 自己的 docstring 写着"只在 BM25 fallback 模式下生效"。

本文件钉死四件事:
    1. 作用域：`_negative_filter_applies()` 为真 ⇔ 活动后端是字面匹配兜底
       （`_st_backend`/`_native_chroma` 都缺，= loader 说的 "BM25 fallback is not
       real vector search"）；真向量后端下**不**做静态过滤。
    2. 行为：同一个英文 query，在真向量后端上必须**真的检索到**期望技能
       （断言真实检索结果，不是断言配置）。
    3. 不空转：垃圾 query（纯数字/符号/空/单字符）在**任何**后端下仍被过滤
       （档 1 与后端无关，避免"修了假阴、放出假阳"）。
    4. 结构性防回归：`search()` 里对 `_is_negative_query(...)` 的调用**只允许**
       出现在 `self._negative_filter_applies()` 的守卫之下（源码级断言）。
"""
from __future__ import annotations

import inspect

import numpy as np
import pytest
import yaml

from agent.skills_mgmt.file_store import SkillFileStore
from agent.skills_mgmt.loader import SkillLoader
from agent.skills_mgmt.vector_adapter import (
    SkillVectorAdapter,
    _ASCII_IDENTIFIER_RE,
    _SYMBOL_ONLY_RE,
    _is_empty_or_symbol_only,
)


# ═══════════════════════════════════════════════════════════════════
#  假语义模型 / 假 VectorStore —— 只用于区分"走到哪条后端"
# ═══════════════════════════════════════════════════════════════════

class _FakeSemanticModel:
    """关键词袋向量模型：含相同关键词的文本相似度高（模拟语义后端）

    计数 `encode_calls` 用于证明"档 1 早退没有触发编码"。
    """

    KEYWORDS = ["testing", "anti", "patterns", "mock", "logs", "health", "界面", "反思"]

    def __init__(self):
        self.encode_calls = 0

    def get_sentence_embedding_dimension(self) -> int:
        return len(self.KEYWORDS)

    def encode(self, texts, normalize_embeddings: bool = True,
               show_progress_bar: bool = False):
        self.encode_calls += 1
        vectors = []
        for text in texts:
            vec = np.zeros(len(self.KEYWORDS), dtype=float)
            low = (text or "").lower()
            for i, kw in enumerate(self.KEYWORDS):
                if kw.lower() in low:
                    vec[i] = 1.0
            if np.linalg.norm(vec) == 0:
                vec[0] = 0.1
            if normalize_embeddings:
                vec = vec / np.linalg.norm(vec)
            vectors.append(vec)
        return np.array(vectors)


class _RecordingStore:
    """字面匹配兜底后端替身：记录有没有被查询过"""

    def __init__(self, items=None):
        self.queries = []
        self._items = items or []

    def search(self, query: str, top_k: int = 10):
        self.queries.append(query)
        return list(self._items)


# ═══════════════════════════════════════════════════════════════════
#  Fixture：3 条英文关键词可命中的技能
# ═══════════════════════════════════════════════════════════════════

_SKILLS = [
    {"id": "testing-anti-patterns", "name": "testing anti patterns",
     "description": "what anti patterns to avoid when writing tests and mocks",
     "category": "testing", "tags": ["testing", "mock"], "version": "1.0.0",
     "enabled": True},
    {"id": "code-observability", "name": "code observability",
     "description": "structured logs and health check for backend api",
     "category": "observability", "tags": ["logs", "health"], "version": "1.0.0",
     "enabled": True},
    {"id": "self-explanatory-ui", "name": "self explanatory ui",
     "description": "自解释界面，不用查文档就能看懂",
     "category": "ui", "tags": ["界面"], "version": "1.0.0", "enabled": True},
]

#: 改前被判为负样本的**合法英文 query**（RET-1R 复现用的 8 条中的代表）
EN_QUERY = "pitfalls of adding mocks in tests"
EN_EXPECTED = "testing-anti-patterns"


@pytest.fixture
def file_store(tmp_path) -> SkillFileStore:
    repo = tmp_path / "skills_repo"
    repo.mkdir()
    for skill in _SKILLS:
        d = repo / skill["id"]
        d.mkdir(parents=True, exist_ok=True)
        block = yaml.safe_dump(skill, allow_unicode=True, sort_keys=False).strip()
        (d / "skill.md").write_text(
            f"---\n{block}\n---\n\n# {skill['name']}\n\n{skill['description']}\n",
            encoding="utf-8",
        )
    return SkillFileStore(repo_path=str(repo))


def _semantic_adapter(file_store: SkillFileStore) -> SkillVectorAdapter:
    """真向量后端语义（_st_backend 就位）—— 生产默认走的就是这条"""
    adapter = SkillVectorAdapter(
        file_store=file_store, use_sentence_transformers=False,
        use_native_chroma=False,
    )
    model = _FakeSemanticModel()
    adapter._st_backend = (model, [], [], [])
    adapter._vector_store = adapter._st_backend
    adapter.ensure_indexed()
    return adapter


def _literal_adapter(file_store: SkillFileStore, store=None) -> SkillVectorAdapter:
    """字面匹配兜底后端（_st_backend/_native_chroma 都缺）—— 启发式声称的作用域"""
    adapter = SkillVectorAdapter(
        file_store=file_store, use_sentence_transformers=False,
        use_native_chroma=False,
    )
    adapter._vector_store = store if store is not None else _RecordingStore()
    return adapter


# ═══════════════════════════════════════════════════════════════════
#  1. 作用域判据（卡面护栏：断言「该负样本正则的作用域 = 它声称的后端」）
# ═══════════════════════════════════════════════════════════════════

class TestNegativeFilterScope:

    def test_semantic_backend_out_of_scope(self, file_store):
        """真向量后端（BGE-m3 / chromadb embedding）⇒ 启发式**不**适用"""
        adapter = _semantic_adapter(file_store)
        assert adapter._negative_filter_applies() is False, (
            "真向量后端下负样本启发式必须不适用（作用域 = BM25/字面匹配兜底后端）"
        )

    def test_literal_fallback_backend_in_scope(self, file_store):
        """字面匹配兜底后端 ⇒ 启发式适用"""
        adapter = _literal_adapter(file_store)
        assert adapter._negative_filter_applies() is True, (
            "字面匹配兜底后端（VectorStore 倒排 BM25 / 字符匹配）必须启用负样本启发式"
        )

    def test_native_chroma_backend_out_of_scope(self, file_store):
        """chromadb onnx embedding 后端（_native_chroma 就位）同样不是字面匹配"""
        adapter = SkillVectorAdapter(
            file_store=file_store, use_sentence_transformers=False,
            use_native_chroma=False,
        )
        adapter._native_chroma = (object(), object())
        adapter._vector_store = adapter._native_chroma
        assert adapter._negative_filter_applies() is False

    def test_scope_predicate_equals_loader_predicate(self, file_store):
        """作用域判据必须与 loader 对同一后端的判据**同口径**（同一表达式）

        loader._try_vector_match / _try_rrf_match 用
            _st_backend is None and _native_chroma is None
        判定 "BM25 fallback is not real vector search"；两处口径一旦分叉，
        就会出现"loader 认为不是真向量、adapter 却按真向量放行"。
        """
        loader_src = inspect.getsource(SkillLoader._try_vector_match)
        assert "getattr(adapter, '_st_backend', None) is None" in loader_src
        assert "getattr(adapter, '_native_chroma', None) is None" in loader_src
        for adapter in (_semantic_adapter(file_store), _literal_adapter(file_store)):
            expected = (
                getattr(adapter, "_st_backend", None) is None
                and getattr(adapter, "_native_chroma", None) is None
            )
            assert adapter._negative_filter_applies() is expected

    def test_literal_pattern_is_not_in_backend_agnostic_tier(self):
        """两档正则必须**不同**：纯 ASCII 单词/词组那条不得留在与后端无关档"""
        assert _ASCII_IDENTIFIER_RE in SkillVectorAdapter._LITERAL_BACKEND_PATTERNS
        assert _ASCII_IDENTIFIER_RE not in SkillVectorAdapter._BACKEND_AGNOSTIC_PATTERNS
        assert _SYMBOL_ONLY_RE in SkillVectorAdapter._BACKEND_AGNOSTIC_PATTERNS


# ═══════════════════════════════════════════════════════════════════
#  2. 行为：真实检索结果（不是断言配置）
# ═══════════════════════════════════════════════════════════════════

class TestEnglishRecallOnSemanticBackend:

    def test_english_query_is_not_treated_as_negative(self, file_store):
        """改前红：合法英文 query 被 ^[a-zA-Z_][a-zA-Z0-9_ ]*$ 判成负样本"""
        adapter = _semantic_adapter(file_store)
        # 该正则本身仍然匹配这条 query（启发式规则没变，变的是**作用域**）
        assert _ASCII_IDENTIFIER_RE.match(EN_QUERY), "前置：该 query 确实命中那条正则"
        results = adapter.search(EN_QUERY, top_k=3, enabled_only=True, min_score=0.01)
        ids = [r["skill_id"] for r in results]
        assert ids, (
            "真向量后端下合法英文 query 不得被负样本启发式过滤（改前返回 [] ⇒ 英文 1/8）"
        )
        assert ids[0] == EN_EXPECTED, f"top1 应为 {EN_EXPECTED}，实际 {ids}"

    def test_literal_backend_still_filters_the_same_query(self, file_store):
        """同一个 query 在它声称的后端上仍被过滤（作用域的另一半）"""
        store = _RecordingStore()
        adapter = _literal_adapter(file_store, store)
        assert adapter.search(EN_QUERY, top_k=3) == []
        assert store.queries == [], "字面匹配后端不该被查询（过滤要发生在后端调用之前）"

    def test_loader_sees_the_semantic_leg(self, file_store):
        """与 loader 的口径联动：真向量后端 ⇒ loader 不走 BM25 fallback 判定"""
        adapter = _semantic_adapter(file_store)
        loader = SkillLoader(file_store=file_store, vector_adapter=adapter)
        result = loader.match("pitfalls of adding mocks in tests", top_k=3,
                              use_vector=True)
        assert result.retrieval_method == "vector"
        assert [m.skill_id for m in result.matches][:1] == [EN_EXPECTED]


# ═══════════════════════════════════════════════════════════════════
#  3. 不空转：垃圾 query 的过滤**没有**被一起放宽（假阳代价的守卫）
# ═══════════════════════════════════════════════════════════════════

class TestBackendAgnosticTierStillFiltersGarbage:

    @pytest.mark.parametrize("query", ["12345", "1 2 3", "!!!", "", "a", "  "])
    def test_garbage_filtered_on_semantic_backend(self, file_store, query):
        adapter = _semantic_adapter(file_store)
        model = adapter._st_backend[0]
        before = model.encode_calls
        assert adapter.search(query, top_k=3) == [], (
            f"垃圾 query {query!r} 在任何后端下都必须被过滤（档 1 与后端无关）"
        )
        assert model.encode_calls == before, (
            "档 1 必须在懒构建/编码之前早退（垃圾 query 不得拉起语义后端）"
        )

    @pytest.mark.parametrize("query", ["12345", "1 2 3", "!!!", "", "a", "  "])
    def test_garbage_filtered_on_literal_backend(self, file_store, query):
        store = _RecordingStore()
        adapter = _literal_adapter(file_store, store)
        assert adapter.search(query, top_k=3) == []
        assert store.queries == []

    def test_helper_semantics(self):
        """档 1 helper 的语义（含中文豁免，守原行为）"""
        assert _is_empty_or_symbol_only("") is True
        assert _is_empty_or_symbol_only("   ") is True
        assert _is_empty_or_symbol_only("a") is True
        assert _is_empty_or_symbol_only("12345") is True
        assert _is_empty_or_symbol_only("!!!") is True
        assert _is_empty_or_symbol_only("123 安全") is False, "含中文的仍是合法查询"
        assert _is_empty_or_symbol_only(EN_QUERY) is False
        assert _is_empty_or_symbol_only("帮我反思一下") is False


# ═══════════════════════════════════════════════════════════════════
#  4. 结构性防回归：调用点必须被作用域守卫包住
# ═══════════════════════════════════════════════════════════════════

class TestCallSiteIsGuarded:

    def test_is_negative_query_is_only_called_under_the_scope_guard(self):
        """源码级：search() 里 _is_negative_query( 只允许出现在守卫之下

        【为什么需要它】改前的缺陷形态是"实现与 docstring 不符"：规则本身写着
        "只在 BM25 fallback 模式下生效"，调用点却无条件执行。行为测试可能因为
        mock 后端恰好是兜底档而漏过，所以这里再钉一道结构断言。
        """
        src = inspect.getsource(SkillVectorAdapter.search)
        call_lines = [
            ln.strip() for ln in src.splitlines()
            if "_is_negative_query(" in ln and not ln.strip().startswith("#")
        ]
        assert call_lines, "前置：search() 应当调用 _is_negative_query"
        for line in call_lines:
            assert "_negative_filter_applies()" in line, (
                "负样本启发式的调用必须被作用域守卫包住（作用域 = 它声称的后端）："
                f"{line}"
            )
