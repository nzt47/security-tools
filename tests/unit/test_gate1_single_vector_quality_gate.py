# -*- coding: utf-8 -*-
"""GATE-1 护栏 · A：**单向量路的质量闸**（口径 = 既有融合闸的单路兜底阈值）

背景（GATE-1 复现，见 docs/audit_skill_governance/GATE1.md §A）:
    RET-1R 把负样本启发式的**作用域**收敛到"字面匹配兜底后端"之后，
    垃圾 query 第一次真正走进语义腿，而 fusion_mode="none" + use_vector=True
    这条**单向量路根本没有质量闸** ⇒ 真库实测负样本非空 19/23 → **22/23**。
    根因不是 RET-1R 引入，而是它**放大**了一个既有缺陷。

本文件钉死三件事（全部断言**真实检索结果**或**唯一真相源的常量**）:
    1. 行为：向量腿 top1 低于「单路兜底阈值」时，单向量路必须**不**把这条腿的
       结果当成答案交出去（返回 None ⇒ 外层 fallback_used=True，降级 TF-IDF）；
       达到阈值时**必须**照常走向量路（不得一刀切拒全部 —— 防"假绿"）。
    2. 口径一致：阈值只有**一个**真相源（SkillLoader._SINGLE_PATH_MIN_TOP1），
       融合路（_try_rrf_match）与单向量路（_try_vector_match）**共用它**，
       且数值仍是既有的 0.45（本卡没有另发明一套阈值）。
    3. 结构性防回归（源码级）：单向量路的比较必须引用那个类常量；
       融合路里不得再出现就地定义的 SINGLE_PATH_MIN_TOP1 局部变量。

【为什么用假语义模型而不是真 BGE-m3】真模型要 450MB / 60s+，CI 不可接受；
本文件的假模型用**关键词袋**造出可控的余弦值（余弦 = 共享关键词数 / 归一化），
因此"低于阈值 / 达到阈值"两侧都能被精确构造。真库上的四格数字见 GATE1.md §A.4。
"""
from __future__ import annotations

import inspect

import numpy as np
import pytest
import yaml

from agent.skills_mgmt.file_store import SkillFileStore
from agent.skills_mgmt.loader import SkillLoader
from agent.skills_mgmt.vector_adapter import SkillVectorAdapter


# ═══════════════════════════════════════════════════════════════════
#  假语义模型：余弦值可控（关键词袋）
# ═══════════════════════════════════════════════════════════════════

class _BagModel:
    """关键词袋向量模型：向量第 i 维 = 第 i 个关键词是否出现，再归一化。

    ⇒ 查询与文档的余弦 = (共享关键词数) / sqrt(|query 关键词| * |doc 关键词|)。
    例：doc 含 8 个关键词、query 含 1 个（命中）⇒ 1/sqrt(8) = 0.3536 < 0.45；
        doc 含 2 个、query 含 2 个且共享 1 个 ⇒ 1/sqrt(4) = 0.5  ≥ 0.45。
    """

    KEYWORDS = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]

    def __init__(self):
        self.encode_calls = 0

    def get_sentence_embedding_dimension(self) -> int:
        return len(self.KEYWORDS)

    def encode(self, texts, normalize_embeddings: bool = True,
               show_progress_bar: bool = False):
        self.encode_calls += 1
        out = []
        for text in texts:
            vec = np.zeros(len(self.KEYWORDS), dtype=float)
            low = (text or "").lower()
            for i, kw in enumerate(self.KEYWORDS):
                if kw in low:
                    vec[i] = 1.0
            if np.linalg.norm(vec) == 0:
                vec[0] = 0.1          # 与生产 adapter 的全零兜底同形
            if normalize_embeddings:
                vec = vec / np.linalg.norm(vec)
            out.append(vec)
        return np.array(out)


#: 既有的「单路兜底阈值」（本文件用它写**前置条件**，故意**不**引用类常量：
#: 这样在"把修复摘掉"的反向补丁下，行为断言仍然会被执行到并按**错误的原因**变红，
#: 而不是红在 AttributeError 这种 API 面上）。
_PREEXISTING_THRESHOLD = 0.45

#: 索引文本含**全部 8 个**关键词 ⇒ 只含 1 个关键词的 query 与它余弦 = 1/sqrt(8) = 0.354
_ALL_KEYWORDS_SKILL = {
    "id": "wide-vocabulary-skill",
    "name": "wide vocabulary skill",
    "description": "alpha beta gamma delta epsilon zeta eta theta 全部关键词",
    "category": "general",
    "tags": ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"],
    "version": "1.0.0",
    "enabled": True,
}

#: 只含 2 个关键词 ⇒ 含 1 个共享关键词的 query 与它余弦 = 1/sqrt(2) = 0.707 ≥ 0.45
_NARROW_SKILL = {
    "id": "narrow-skill",
    "name": "narrow skill",
    "description": "gamma delta 两个关键词",
    "category": "general",
    "tags": ["gamma", "delta"],
    "version": "1.0.0",
    "enabled": True,
}


@pytest.fixture
def file_store(tmp_path) -> SkillFileStore:
    repo = tmp_path / "skills_repo"
    repo.mkdir()
    for skill in (_ALL_KEYWORDS_SKILL, _NARROW_SKILL):
        d = repo / skill["id"]
        d.mkdir(parents=True, exist_ok=True)
        block = yaml.safe_dump(skill, allow_unicode=True, sort_keys=False).strip()
        (d / "skill.md").write_text(
            f"---\n{block}\n---\n\n# {skill['name']}\n\n{skill['description']}\n",
            encoding="utf-8",
        )
    return SkillFileStore(repo_path=str(repo))


def _semantic_adapter(file_store: SkillFileStore) -> SkillVectorAdapter:
    """真向量后端语义（_st_backend 就位）—— 与 RET-1R 护栏同一造法"""
    adapter = SkillVectorAdapter(
        file_store=file_store, use_sentence_transformers=False, use_native_chroma=False,
    )
    adapter._st_backend = (_BagModel(), [], [], [])
    adapter._vector_store = adapter._st_backend
    adapter.ensure_indexed()
    return adapter


def _vector_top1(file_store, adapter, query: str) -> float:
    """这条腿自己的 top1 余弦（闸的输入量）"""
    results = adapter.search(query, top_k=5, enabled_only=True, min_score=0.01)
    assert results, "前置：假模型下该 query 必须有候选（否则测的不是闸）"
    return float(results[0]["score"])


# ═══════════════════════════════════════════════════════════════════
#  1. 行为：低于阈值 ⇒ 不把这条腿的结果当答案；达到阈值 ⇒ 照常走
# ═══════════════════════════════════════════════════════════════════

class TestSingleVectorQualityGate:

    def test_low_top1_is_not_returned_as_the_answer(self, file_store):
        """改前红：单向量路没有任何质量闸，0.354 的 top1 照样被当成答案返回"""
        adapter = _semantic_adapter(file_store)
        query = "alpha"                     # 只含 1 个关键词
        top1 = _vector_top1(file_store, adapter, query)
        assert top1 < _PREEXISTING_THRESHOLD, (
            "前置：本用例要构造的是**低于**单路兜底阈值（%.2f）的候选，实际 top1=%.4f"
            % (_PREEXISTING_THRESHOLD, top1)
        )

        loader = SkillLoader(file_store=file_store, vector_adapter=adapter)
        result = loader.match(query, top_k=3, use_vector=True)

        assert result.retrieval_method != "vector", (
            "向量腿 top1（%.4f）低于单路兜底阈值（%.2f）时，单向量路不得把这条腿的"
            "结果当成答案交出去（RET-1R 残留 R2-1：这条路径上原本没有任何质量闸）；"
            "实际 retrieval_method=%s，matches=%r"
            % (top1, _PREEXISTING_THRESHOLD, result.retrieval_method,
               [m.skill_id for m in result.matches])
        )
        assert result.fallback_used is True, (
            "低于阈值时必须与融合路的「单路兜底阈值检查」同一处置：返回 None ⇒ "
            "调用方降级 TF-IDF（fallback_used=True）"
        )

    def test_top1_at_or_above_threshold_still_goes_vector(self, file_store):
        """防「一刀切拒全部」的假绿：达到阈值的候选必须仍然走向量路"""
        adapter = _semantic_adapter(file_store)
        query = "gamma delta"               # 与 narrow-skill 余弦 = 1.0
        top1 = _vector_top1(file_store, adapter, query)
        assert top1 >= _PREEXISTING_THRESHOLD, "前置：本用例要构造**达标**的候选"

        loader = SkillLoader(file_store=file_store, vector_adapter=adapter)
        result = loader.match(query, top_k=3, use_vector=True)

        assert result.retrieval_method == "vector", (
            "向量腿 top1（%.4f）达到单路兜底阈值（%.2f）时不得被质量闸误伤；"
            "实际 retrieval_method=%s" % (top1, _PREEXISTING_THRESHOLD,
                                          result.retrieval_method)
        )
        assert result.fallback_used is False
        assert [m.skill_id for m in result.matches][:1] == ["narrow-skill"], (
            "达标候选必须被真的返回（断言真实检索结果，不是断言配置）"
        )

    def test_gate_uses_bounded_cosine_not_raw_score(self, file_store):
        """闸比的是**这条腿的 top1 余弦本身**（有界 [0,1]），不是名次分/无界分"""
        adapter = _semantic_adapter(file_store)
        top1 = _vector_top1(file_store, adapter, "alpha")
        assert 0.0 <= top1 <= 1.0, "向量腿 top1 必须是有界相似度（余弦）"
        assert abs(top1 - 1.0 / np.sqrt(8)) < 1e-6, (
            "前置：假模型下该候选的余弦应为 1/sqrt(8)=0.3536，实际 %.6f" % top1
        )


# ═══════════════════════════════════════════════════════════════════
#  2. 口径一致：只有一个真相源，且数值仍是既有的 0.45
# ═══════════════════════════════════════════════════════════════════

class TestThresholdCaliberIsShared:

    def test_threshold_value_is_the_pre_existing_one(self):
        """本卡**没有**调阈值：仍是既有单路兜底阈值的 0.45"""
        assert SkillLoader._SINGLE_PATH_MIN_TOP1 == 0.45, (
            "单路兜底阈值必须是既有的 0.45；本卡只把它从局部变量上提为类常量，"
            "数值一字未改（改数值 = 另发明一套阈值，需要重新标定）"
        )

    def test_both_call_sites_reference_the_same_constant(self):
        """融合路与单向量路必须引用**同一个**类常量（源码级）"""
        rrf_src = inspect.getsource(SkillLoader._try_rrf_match)
        vec_src = inspect.getsource(SkillLoader._try_vector_match)
        assert "self._SINGLE_PATH_MIN_TOP1" in rrf_src, (
            "融合路的单路兜底阈值检查必须引用类常量（口径唯一真相源）"
        )
        assert "self._SINGLE_PATH_MIN_TOP1" in vec_src, (
            "单向量路的质量闸必须引用**同一个**类常量 —— 不许另发明一套阈值"
        )
        assert "SINGLE_PATH_MIN_TOP1 = 0.45" not in rrf_src, (
            "融合路里不得再保留就地定义的 SINGLE_PATH_MIN_TOP1 局部变量"
            "（两处各写一份就是口径分叉的入口）"
        )

    def test_gate_only_guards_the_single_vector_path(self):
        """结构：质量闸在 _try_vector_match 内、且以 return None 交回降级语义"""
        src = inspect.getsource(SkillLoader._try_vector_match)
        gate_at = src.find("self._SINGLE_PATH_MIN_TOP1")
        assert gate_at != -1
        tail = src[gate_at:gate_at + 600]
        assert "return None" in tail, (
            "低于阈值必须 return None（与融合路同一处置：交回调用方降级 TF-IDF）"
        )
