# -*- coding: utf-8 -*-
"""TASK-S10-03 现象B 回归锚：检索层质量门只认「有界相似度」

真机现象（`docs/zh/CloudPivot_v7.2重构计划/TASK-S9-01_验收报告.md` §3.2，勿臆造）::

    query="2 加 3 等于多少？只回答数字"
      method=rrf
      self_reflection   score=0.9954(rrf_normalized)
                        tfidf_score=0.1  vector_score=None  bm25_score=3.5184

根因（代码行级，修复前）:
    `agent/skills_mgmt/loader.py:1516-1522` 把各路 ``*_score`` 一股脑塞进
    ``max_raw_score``，其中：
      · ``tfidf_score`` / ``vector_score`` 是**有界到 [0,1]** 的余弦相似度；
      · ``bm25_score`` 是 **BM25Okapi 无界原始分**（同一批数据里 1.2 ~ 21.0）。
    再把 ``max_raw_score`` 与有界阈值 ``_RRF_QUALITY_MIN = 0.3``（`loader.py:820`）
    比大小 ⇒ 量纲混用：无界分恒赢，噪声级候选（tfidf=0.1）过闸。

口径（与本仓已落地的编排层门控一致，`orchestrator.py:72-75` / `:3057-3108`）:
    **阈值只与有界相似度比较**；BM25 无界原始分与 ``rrf_normalized``（RRF 按 rank-1
    归一化，top1 恒 ≈1.0）都不参与阈值比较。

本文件是**回归锚**：修复前 1/2/5 应失败，修复后全通过。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.skills_mgmt.file_store import SkillFileStore  # noqa: E402
from agent.skills_mgmt.loader import SkillLoader, SkillMatch  # noqa: E402


#: 真机实测形态①（min_score=0.01 探针）：tfidf 有值但噪声级，BM25 无界分撑起 max()
LOW_QUALITY_MIXED = {
    "tfidf_rank": 2, "vector_rank": None, "bm25_rank": 1,
    "tfidf_score": 0.1, "vector_score": None, "bm25_score": 3.5184,
    "rrf_score": 0.016318, "rrf_normalized": 0.9954,
}

#: 真机实测形态②（min_score=0.3 编排器配置）：只有 BM25 命中，有界路全空
LOW_QUALITY_BM25_ONLY = {
    "tfidf_rank": None, "vector_rank": None, "bm25_rank": 1,
    "tfidf_score": None, "vector_score": None, "bm25_score": 3.5184,
    "rrf_score": 0.032, "rrf_normalized": 1.0,
}

#: 真机实测形态③（真实命中）：tfidf 有界相似度 0.4444，BM25 同样高
GENUINE_HIT = {
    "tfidf_rank": 1, "vector_rank": None, "bm25_rank": 1,
    "tfidf_score": 0.4444, "vector_score": None, "bm25_score": 15.1856,
    "rrf_score": 0.032, "rrf_normalized": 1.0,
}

#: 有界相似度存在但低于阈值（真机形态①的同族：0.1 的噪声级）
LOW_BOUNDED_ONLY = {
    "tfidf_rank": 1, "vector_rank": None, "bm25_rank": None,
    "tfidf_score": 0.1, "vector_score": None, "bm25_score": None,
    "rrf_score": 0.016, "rrf_normalized": 1.0,
}


def _mk_match(skill_id: str, score: float, breakdown: dict | None = None) -> SkillMatch:
    return SkillMatch(
        skill_id=skill_id,
        name=skill_id,
        description="",
        score=score,
        estimated_tokens=10,
        category="test",
        tags=[],
        version="1.0.0",
        enabled=True,
        score_breakdown=dict(breakdown) if breakdown else None,
    )


def _fake_store(tmp_path: Path) -> SkillFileStore:
    """最小技能库（只为让 loader 能构造；检索路被 monkeypatch 接管）"""
    for sid in ("self_reflection", "memory_summary"):
        d = tmp_path / "skills_repo" / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "skill.md").write_text(
            "---\nid: %s\nname: %s\ndescription: 测试技能\n"
            "category: meta\ntags: [测试]\nversion: 1.0.0\nenabled: true\n---\n\n正文\n"
            % (sid, sid), encoding="utf-8")
    return SkillFileStore(repo_path=str(tmp_path / "skills_repo"))


def _loader_with_paths(tmp_path: Path, *, tfidf_matches, bm25_matches):
    """构造 loader 并把两条检索路替换为固定输入（确定性，不依赖真 BM25 语料）"""
    loader = SkillLoader(file_store=_fake_store(tmp_path))
    loader._get_vector_adapter = lambda: None          # 向量路不可用（离线真机形态）
    loader._tfidf_scan = lambda **kw: list(tfidf_matches)
    loader._try_bm25_match = lambda **kw: list(bm25_matches)
    return loader


def _match(loader, query, *, min_score=0.01):
    return loader.match(query, top_k=5, enabled_only=True, min_score=min_score,
                        use_vector=True, use_bm25=True, fusion_mode="rrf")


# ═══════════════════════════════════════════════════════════════
#  意图：质量门只认有界相似度 ⇒ 低质候选被挡住
# ═══════════════════════════════════════════════════════════════

class Test质量门只认有界相似度:

    def test_真机形态_1_无界BM25撑起max_raw_必须被挡(self, tmp_path):
        """★ 真机锚：tfidf=0.1（噪声级）+ bm25=3.5184（无界）⇒ 不得过闸"""
        loader = _loader_with_paths(
            tmp_path,
            tfidf_matches=[_mk_match("self_reflection", 0.1)],
            bm25_matches=[_mk_match("self_reflection", 3.5184)],
        )
        result = _match(loader, "2 加 3 等于多少？只回答数字")

        assert result.matches == [], (
            "噪声级候选（有界相似度 0.1 < 0.3）不得靠无界 BM25 原始分过闸；"
            "实际=%r" % ([(m.skill_id, m.score, m.score_breakdown) for m in result.matches],))
        assert result.retrieval_method == "rrf"
        assert result.fallback_used is False, (
            "质量门拒绝后不得改走 TF-IDF fallback（会引入新的误召回路径）")

    def test_真机形态_2_仅BM25命中_有界路全空_必须被挡(self, tmp_path):
        """★ 真机锚：min_score=0.3 时 tfidf 全被过滤 ⇒ 只剩无界 BM25 分，不得过闸"""
        loader = _loader_with_paths(
            tmp_path,
            tfidf_matches=[],
            bm25_matches=[_mk_match("self_reflection", 3.5184)],
        )
        result = _match(loader, "2 加 3 等于多少？只回答数字", min_score=0.3)

        assert result.matches == [], (
            "有界相似度路存在但全部未命中时不可信（与编排层 _bounded_relevance 同口径）；"
            "实际=%r" % ([(m.skill_id, m.score) for m in result.matches],))

    def test_有界相似度低_即使BM25很高_仍被挡(self, tmp_path):
        """量纲纪律：0.1 的有界相似度不因 BM25 原始分很高而放行"""
        loader = _loader_with_paths(
            tmp_path,
            tfidf_matches=[_mk_match("self_reflection", 0.1)],
            bm25_matches=[_mk_match("self_reflection", 42.0)],
        )
        assert _match(loader, "任意噪声查询").matches == []

    def test_真机正样本_有界相似度达标_契约不变(self, tmp_path):
        """正向断言（防「一刀切拒全部」假绿）：真命中必须仍然通过"""
        loader = _loader_with_paths(
            tmp_path,
            tfidf_matches=[_mk_match("self_reflection", 0.4444)],
            bm25_matches=[_mk_match("self_reflection", 15.1856)],
        )
        result = _match(loader, "自我反思一下你的回答")

        assert [m.skill_id for m in result.matches] == ["self_reflection"], (
            "有界相似度 0.4444 ≥ 0.3 的真命中不得被质量门误伤；"
            "实际=%r" % ([(m.skill_id, m.score) for m in result.matches],))
        assert result.matches[0].score_breakdown["tfidf_score"] == 0.4444

    def test_无有界路声明_保持既有行为_向后兼容(self, tmp_path):
        """未声明任何有界相似度路的自定义 breakdown ⇒ 沿用旧口径（不新增拒召回）"""
        loader = _loader_with_paths(
            tmp_path,
            tfidf_matches=[_mk_match("custom_skill", 0.9,
                                     {"custom_score": 0.9, "rrf_normalized": 1.0})],
            bm25_matches=[],
        )
        result = _match(loader, "自定义调用方")
        assert [m.skill_id for m in result.matches] == ["custom_skill"], (
            "无有界路声明的旧形态必须保持既有行为（向后兼容）")


class Test有界相似度提取口径:

    def test_提取只取有界键_排除BM25与排名归一化分(self):
        score = SkillLoader._bounded_quality_score(LOW_QUALITY_MIXED)
        assert score == pytest.approx(0.1), (
            "有界相似度必须只取 tfidf/vector/rerank，BM25 无界分与 rrf 归一化分不参与；"
            "实际=%r" % (score,))

    def test_仅BM25命中_有界提取返回None(self):
        assert SkillLoader._bounded_quality_score(LOW_QUALITY_BM25_ONLY) is None

    def test_真命中提取到0_4444(self):
        assert SkillLoader._bounded_quality_score(GENUINE_HIT) == pytest.approx(0.4444)

    def test_无有界键声明_返回None(self):
        assert SkillLoader._bounded_quality_score({"rrf_score": 0.01,
                                                   "rrf_normalized": 1.0}) is None

    def test_非字典输入_返回None(self):
        assert SkillLoader._bounded_quality_score(None) is None
        assert SkillLoader._bounded_quality_score("not-a-dict") is None

    def test_bool不被当作数值(self):
        """bool 是 int 子类，必须排除（否则 True 会被当成 1.0 放行一切）"""
        assert SkillLoader._bounded_quality_score({"tfidf_score": True}) is None


# ═══════════════════════════════════════════════════════════════
#  真库锚：与真机同一批 query（真 BM25 + 真技能库），逐条对照
# ═══════════════════════════════════════════════════════════════

_REAL_REPO = Path(project_root) / "data" / "skills_repo"


@pytest.mark.skipif(not _REAL_REPO.is_dir(),
                    reason="真技能库 data/skills_repo 缺失")
class Test真库同输入对照:
    """用真技能库 + 真 BM25 复现真机形态（探针 scripts/dev/s1003_retrieval_gate_probe.py）

    这些断言是「同输入前后对比」的可执行版本：修复后噪声查询必须空，真命中必须保留。
    """

    @pytest.fixture()
    def loader(self):
        pytest.importorskip("rank_bm25", reason="BM25 路不可用时本对照无意义")
        return SkillLoader(file_store=SkillFileStore(repo_path=str(_REAL_REPO)))

    def test_噪声查询_不得有候选(self, loader):
        result = loader.match("2 加 3 等于多少？只回答数字", top_k=5,
                              enabled_only=True, min_score=0.3,
                              use_vector=True, use_bm25=True, fusion_mode="rrf")
        assert result.matches == [], (
            "真机噪声查询必须零候选（修复前 top1=self_reflection，tfidf_score=0.1）；"
            "实际=%r" % ([(m.skill_id, m.score_breakdown) for m in result.matches],))

    def test_真命中_仍保留(self, loader):
        result = loader.match("自我反思一下你的回答", top_k=5,
                              enabled_only=True, min_score=0.3,
                              use_vector=True, use_bm25=True, fusion_mode="rrf")
        ids = [m.skill_id for m in result.matches]
        assert "self_reflection" in ids, (
            "真命中（tfidf_score=0.4444）不得被误伤；实际=%r" % (ids,))
